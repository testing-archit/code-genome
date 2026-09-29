"use client";

import type { AnswerLanguage, GroundedAnswer, VoiceName, VoiceSession } from "@code-genome/contracts";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { MicIcon, MicOffIcon, PhoneOffIcon, SendIcon } from "../../../../components/icons";
import { EvidenceChips, RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { LanguagePicker, Notice, Panel, readLanguage, saveLanguage } from "../../../../components/ui";
import { LiveVoiceAgent, VoiceEvent, VoiceStatus } from "../../../../lib/live-voice";
import { moduleColor, shortSha } from "../../../../lib/format";

const voices: Array<{ value: VoiceName; label: string }> = [
  { value: "Kore", label: "Kore (firm)" },
  { value: "Puck", label: "Puck (upbeat)" },
  { value: "Charon", label: "Charon (informative)" },
  { value: "Aoede", label: "Aoede (breezy)" },
  { value: "Leda", label: "Leda (youthful)" },
  { value: "Orus", label: "Orus (steady)" },
  { value: "Fenrir", label: "Fenrir (excitable)" },
  { value: "Zephyr", label: "Zephyr (bright)" },
];

const statusCopy: Record<VoiceStatus, { title: string; detail: string }> = {
  idle: { title: "Ready when you are", detail: "Start a call and ask out loud. Speak English, Hindi, or Hinglish; you can interrupt at any time." },
  connecting: { title: "Connecting", detail: "Opening a private session with Gemini 3.8 Live." },
  listening: { title: "Listening", detail: "Ask about modules, recent changes, or risky files." },
  thinking: { title: "Checking evidence", detail: "Searching the published snapshot before answering." },
  speaking: { title: "Speaking", detail: "Start talking to interrupt." },
  ended: { title: "Call ended", detail: "The transcript and the evidence it used stay on this page until you leave." },
  error: { title: "The call could not continue", detail: "" },
};

type Line =
  | { id: number; kind: "user" | "assistant"; text: string; open: boolean }
  | { id: number; kind: "tool"; question: string; answer: GroundedAnswer | null; error?: string };

const BAR_COUNT = 40;

export default function VoicePage() {
  return (
    <RequiresSnapshot what="The voice agent">
      <VoiceAgent />
    </RequiresSnapshot>
  );
}

function VoiceAgent() {
  const { repository, published, architecture } = useRepo();
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [detail, setDetail] = useState<string | null>(null);
  const [voice, setVoice] = useState<VoiceName>("Kore");
  const [language, setLanguage] = useState<AnswerLanguage>("auto");
  const [muted, setMuted] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [session, setSession] = useState<VoiceSession | null>(null);
  const [typed, setTyped] = useState("");
  const agent = useRef<LiveVoiceAgent | null>(null);
  const bars = useRef<Array<HTMLElement | null>>([]);
  const lineId = useRef(0);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const live = status === "connecting" || status === "listening" || status === "thinking" || status === "speaking";

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLanguage(readLanguage());
  }, []);

  const onEvent = useCallback((event: VoiceEvent) => {
    if (event.type === "status") {
      setStatus(event.status);
      setDetail(event.detail ?? null);
    } else if (event.type === "session") {
      setSession(event.session);
    } else if (event.type === "tool") {
      setLines((current) => [...current, { id: ++lineId.current, kind: "tool", question: event.question, answer: event.answer, error: event.error }]);
    } else if (event.type === "transcript") {
      setLines((current) => {
        const last = [...current].reverse().find((line) => line.kind === event.role);
        const lastIndex = last ? current.lastIndexOf(last) : -1;
        // Append streaming fragments to the open line for this speaker when it is the latest line.
        if (last && last.kind !== "tool" && last.open && lastIndex === current.length - 1) {
          const next = [...current];
          next[lastIndex] = { ...last, text: last.text + event.text, open: !event.final };
          return next;
        }
        if (!event.text.trim()) {
          return current.map((line) => (line.kind === event.role && line.open ? { ...line, open: false } : line));
        }
        const closed = current.map((line) => (line.kind !== "tool" && line.open ? { ...line, open: false } : line));
        return [...closed, { id: ++lineId.current, kind: event.role, text: event.text, open: !event.final }];
      });
    }
  }, []);

  useEffect(() => {
    transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight, behavior: "smooth" });
  }, [lines]);

  // Drive the band visualizer from whichever side is producing sound.
  useEffect(() => {
    if (!live) {
      bars.current.forEach((bar) => bar && (bar.style.height = "8%"));
      return;
    }
    let frame = 0;
    const data = new Uint8Array(128);
    const draw = () => {
      const current = agent.current;
      const analyser = status === "speaking" ? current?.outputAnalyser.node : current?.inputAnalyser.node;
      if (analyser) {
        analyser.getByteFrequencyData(data);
        bars.current.forEach((bar, index) => {
          if (!bar) return;
          const mirrored = index < BAR_COUNT / 2 ? BAR_COUNT / 2 - index : index - BAR_COUNT / 2 + 1;
          const value = data[Math.min(data.length - 1, mirrored * 2)] / 255;
          bar.style.height = `${Math.max(6, value * 100)}%`;
        });
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [live, status]);

  useEffect(() => () => agent.current?.stop(), []);

  async function start() {
    setLines([]);
    setSession(null);
    setMuted(false);
    const instance = new LiveVoiceAgent(repository.id, voice, language, onEvent);
    agent.current = instance;
    await instance.start();
  }

  function stop() {
    agent.current?.stop();
    agent.current = null;
  }

  function toggleMute() {
    const next = !muted;
    setMuted(next);
    agent.current?.setMuted(next);
  }

  function sendTyped(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!typed.trim()) return;
    agent.current?.sendText(typed.trim());
    setTyped("");
  }

  function changeLanguage(value: AnswerLanguage) {
    setLanguage(value);
    saveLanguage(value);
  }

  const copy = statusCopy[status];
  const modules = architecture.data?.modules.length ?? 0;

  return (
    <div className="voice">
      <section className="panel">
        <div className="voice-stage" data-status={status}>
          <div aria-hidden="true" className="voice-bands">
            {Array.from({ length: BAR_COUNT }, (_, index) => (
              <i
                key={index}
                ref={(element) => {
                  bars.current[index] = element;
                }}
                style={{ "--band-color": moduleColor(modules ? index % Math.min(modules, 10) : 0) } as React.CSSProperties}
              />
            ))}
          </div>
          <div aria-live="polite">
            <div className="voice-status">{copy.title}</div>
            <p className="voice-status-detail">{detail ?? copy.detail}</p>
          </div>

          {!live && (
            <div className="voice-settings">
              <div className="field" style={{ minWidth: 0 }}>
                Language
                <LanguagePicker onChange={changeLanguage} value={language} />
              </div>
              <label className="field">
                Voice
                <select className="select" onChange={(event) => setVoice(event.target.value as VoiceName)} value={voice}>
                  {voices.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </label>
            </div>
          )}

          <div className="voice-controls">
            {live ? (
              <>
                <button aria-pressed={muted} className="button button-secondary voice-call" onClick={toggleMute} type="button">
                  {muted ? <MicOffIcon /> : <MicIcon />}{muted ? "Unmute" : "Mute"}
                </button>
                <button className="button button-primary voice-call live" onClick={stop} type="button"><PhoneOffIcon />End call</button>
              </>
            ) : (
              <button className="button button-primary voice-call" onClick={() => void start()} type="button">
                <MicIcon />{status === "ended" || status === "error" ? "Start a new call" : "Start voice call"}
              </button>
            )}
          </div>

          {status === "listening" && (
            <form onSubmit={sendTyped} style={{ width: "min(100%, 460px)" }}>
              <div className="composer-box">
                <label className="sr-only" htmlFor="voice-typed">Type instead of speaking</label>
                <textarea id="voice-typed" onChange={(event) => setTyped(event.target.value)} placeholder="Or type a question into the call" rows={1} value={typed} />
                <button aria-label="Send to call" className="button button-secondary" type="submit"><SendIcon size={16} /></button>
              </div>
            </form>
          )}

          <p className="muted small" style={{ maxWidth: "60ch" }}>
            Uses {session?.model ?? "gemini-3.8-live"} over a single-use session token; your API key never reaches the browser.
            The agent looks up snapshot <code>{shortSha(published?.snapshot_sha, 8)}</code> before it answers repository questions.
          </p>
        </div>
      </section>

      <Panel title="Transcript" description="Speech is transcribed live. Evidence lookups show what each spoken answer relied on." flush>
        <div aria-live="polite" className="transcript" ref={transcriptRef}>
          {lines.length === 0 && <p className="muted small">Nothing yet. Start a call and say something like “What changed this week?” or “Billing module kahan hai?”</p>}
          {lines.map((line) =>
            line.kind === "tool" ? (
              <div className="transcript-tool" key={line.id}>
                <strong>Looked up: {line.question}</strong>
                {line.error ? <span className="muted">{line.error}</span> : line.answer && (
                  <>
                    {line.answer.evidence_ids.length === 0 ? <span className="muted">No supporting evidence was identified in the selected scope.</span> : <EvidenceChips ids={line.answer.evidence_ids} limit={5} />}
                  </>
                )}
              </div>
            ) : (
              <div className={`transcript-line ${line.kind}`} key={line.id}>
                <span>{line.kind === "user" ? "You" : "Agent"}</span>
                <p lang={/[ऀ-ॿ]/.test(line.text) ? "hi" : undefined}>{line.text}</p>
              </div>
            ),
          )}
        </div>
        {session && (
          <div className="panel-body" style={{ borderTop: "1px solid var(--rule)" }}>
            <Notice>{session.limitations.join(" ")}</Notice>
          </div>
        )}
      </Panel>
    </div>
  );
}
