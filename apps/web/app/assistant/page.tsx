"use client";

import type { AnswerLanguage, AssistantAnswer, VoiceName } from "@code-genome/contracts";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { MicIcon, MicOffIcon, PhoneOffIcon, SendIcon } from "../../components/icons";
import { Markdown } from "../../components/markdown";
import { TopBar } from "../../components/shell";
import { LanguagePicker, Notice, Panel, readLanguage, saveLanguage } from "../../components/ui";
import { useWorkspace } from "../../components/workspace";
import { api, errorMessage } from "../../lib/api";
import { resolveRepository } from "../../lib/assistant";
import { LiveVoiceAgent, MAX_TEXT_LENGTH, VoiceEvent, VoiceStatus } from "../../lib/live-voice";

const VOICES: VoiceName[] = ["Kore", "Puck", "Charon", "Aoede", "Leda", "Orus", "Fenrir", "Zephyr"];
const REPOSITORY_PAGES: Record<string, string> = {
  overview: "", genome: "genome", explorer: "explorer", docs: "docs", architecture: "architecture",
  graph: "graph", files: "files", history: "history", bugs: "bugs", change: "change", risk: "risk",
  compare: "compare", models: "models", ask: "ask", voice: "voice", search: "search", audit: "audit",
  settings: "settings",
};  // prettier-ignore
const WORKSPACE_PAGES: Record<string, string> = { home: "/", activity: "/activity", assistant: "/assistant" };
const STATUS_COPY: Record<VoiceStatus, string> = {
  idle: "Ask about Code Genome itself or any repository here, out loud or by typing.",
  connecting: "Opening a private session with Gemini Live.",
  listening: "Listening. Try “What is a bus factor?” or “ky mein risky files kaun si hain?”",
  thinking: "Looking it up.",
  speaking: "Speaking. Start talking to interrupt.",
  ended: "Call ended. The transcript stays until you leave this page.",
  error: "The call could not continue.",
};

type Line =
  | { id: number; kind: "user" | "assistant"; text: string; open: boolean }
  | { id: number; kind: "tool"; label: string; question: string; text: string; sources: string[]; error?: string };

export default function AssistantPage() {
  const { repositories } = useWorkspace();
  const router = useRouter();
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [detail, setDetail] = useState<string | null>(null);
  const [voice, setVoice] = useState<VoiceName>("Kore");
  const [language, setLanguage] = useState<AnswerLanguage>("auto");
  const [muted, setMuted] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [typed, setTyped] = useState("");
  const [asking, setAsking] = useState(false);
  const [typedAnswer, setTypedAnswer] = useState<{ question: string; answer: AssistantAnswer } | null>(null);
  const [typedError, setTypedError] = useState<string | null>(null);
  const agent = useRef<LiveVoiceAgent | null>(null);
  const lineId = useRef(0);
  // Tools run during a call; read the latest repository list, not the one from call start.
  const repositoriesRef = useRef(repositories);
  useEffect(() => {
    repositoriesRef.current = repositories;
  }, [repositories]);
  const live = status === "connecting" || status === "listening" || status === "thinking" || status === "speaking";

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLanguage(readLanguage());
  }, []);
  useEffect(() => () => agent.current?.stop(), []);

  const addTool = useCallback((line: Omit<Extract<Line, { kind: "tool" }>, "id" | "kind">) => {
    setLines((current) => [...current, { id: ++lineId.current, kind: "tool", ...line }]);
  }, []);

  const runTool = useCallback(
    async (name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> => {
      const question = typeof args.question === "string" ? args.question.slice(0, MAX_TEXT_LENGTH) : "";
      if (name === "search_code_genome_docs") {
        const answer = await api.askAssistant(question, "en");
        addTool({ label: "Code Genome docs", question, text: answer.answer, sources: answer.sources.map((item) => `${item.path} › ${item.title}`) });
        return { answer: answer.answer, evidence_ids: answer.evidence_ids, limitations: answer.limitations };
      }
      if (name === "ask_repository") {
        const spoken = typeof args.repository === "string" ? args.repository : "";
        const repository = resolveRepository(spoken, repositoriesRef.current);
        if (!repository) {
          const names = repositoriesRef.current.map((item) => item.external_id).join(", ");
          addTool({ label: "Repository", question, text: "", sources: [], error: `No single repository matches “${spoken}”.` });
          return { error: `No single repository matches "${spoken}". Repositories: ${names || "none"}.` };
        }
        const answer = await api.ask(repository.id, question, "en", "voice");
        addTool({ label: repository.external_id, question, text: answer.answer, sources: answer.evidence_ids.slice(0, 6) });
        return { repository: repository.external_id, answer: answer.answer, evidence_ids: answer.evidence_ids, limitations: answer.limitations };
      }
      if (name === "open_page") {
        const page = typeof args.page === "string" ? args.page : "";
        if (page in WORKSPACE_PAGES) {
          router.push(WORKSPACE_PAGES[page]);
          addTool({ label: "Opened", question: page, text: "", sources: [] });
          return { opened: page };
        }
        const repository = resolveRepository(typeof args.repository === "string" ? args.repository : "", repositoriesRef.current);
        if (!(page in REPOSITORY_PAGES) || !repository) {
          return { error: repository ? `Unknown page "${page}".` : "Say which repository to open." };
        }
        const slug = REPOSITORY_PAGES[page];
        window.open(`/r/${repository.id}${slug ? `/${slug}` : ""}`, "_blank", "noopener");
        addTool({ label: "Opened", question: `${page} for ${repository.external_id} (new tab)`, text: "", sources: [] });
        return { opened: page, repository: repository.external_id, note: "Opened in a new tab so this call continues." };
      }
      return { error: "Unsupported tool call." };
    },
    [addTool, router],
  );

  const onEvent = useCallback((event: VoiceEvent) => {
    if (event.type === "status") {
      setStatus(event.status);
      setDetail(event.detail ?? null);
    } else if (event.type === "transcript") {
      setLines((current) => {
        const last = current[current.length - 1];
        if (last && last.kind === event.role && last.open) {
          return [...current.slice(0, -1), { ...last, text: last.text + event.text, open: !event.final }];
        }
        if (!event.text.trim()) return current;
        return [...current, { id: ++lineId.current, kind: event.role, text: event.text, open: !event.final }];
      });
    }
  }, []);

  async function start() {
    agent.current?.stop();
    setLines([]);
    setMuted(false);
    const instance: LiveVoiceAgent = new LiveVoiceAgent(
      "",
      voice,
      language,
      (event) => {
        if (agent.current === instance) onEvent(event);
      },
      { createSession: () => api.createAssistantSession(voice, language), runTool },
    );
    agent.current = instance;
    await instance.start();
  }

  function stop() {
    agent.current?.stop();
    agent.current = null;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = typed.trim();
    if (!question) return;
    setTyped("");
    if (live && agent.current) {
      agent.current.sendText(question);
      return;
    }
    setAsking(true);
    setTypedError(null);
    try {
      setTypedAnswer({ question, answer: await api.askAssistant(question, language) });
    } catch (caught) {
      setTypedError(errorMessage(caught, "The assistant could not answer."));
    } finally {
      setAsking(false);
    }
  }

  return (
    <>
      <TopBar title="Assistant" detail="Code Genome and every repository in this workspace" />
      <div className="content">
        <div className="split split-side">
          <Panel title="Code Genome assistant" description={detail ?? STATUS_COPY[status]}>
            <div style={{ display: "grid", gap: 16 }}>
              <div className="voice-settings" style={{ justifyContent: "flex-start" }}>
                <LanguagePicker
                  disabled={live}
                  onChange={(value) => {
                    setLanguage(value);
                    saveLanguage(value);
                  }}
                  value={language}
                />
                <label className="field" style={{ minWidth: 160 }}>
                  <span className="sr-only">Voice</span>
                  <select className="select input" disabled={live} onChange={(event) => setVoice(event.target.value as VoiceName)} value={voice}>
                    {VOICES.map((name) => <option key={name} value={name}>{name}</option>)}
                  </select>
                </label>
              </div>
              <div className="voice-controls" style={{ justifyContent: "flex-start" }}>
                {live ? (
                  <>
                    <button aria-pressed={muted} className="button button-secondary voice-call" onClick={() => { setMuted(!muted); agent.current?.setMuted(!muted); }} type="button">
                      {muted ? <MicOffIcon /> : <MicIcon />}{muted ? "Unmute" : "Mute"}
                    </button>
                    <button className="button button-primary voice-call live" onClick={stop} type="button"><PhoneOffIcon />End call</button>
                  </>
                ) : (
                  <button className="button button-primary voice-call" onClick={() => void start()} type="button"><MicIcon />Start voice call</button>
                )}
              </div>
              <form className="row" onSubmit={(event) => void submit(event)} style={{ gap: 8 }}>
                <label className="sr-only" htmlFor="assistant-question">Ask the assistant</label>
                <input
                  className="input"
                  id="assistant-question"
                  maxLength={MAX_TEXT_LENGTH}
                  onChange={(event) => setTyped(event.target.value)}
                  placeholder={live ? "Type to the voice assistant" : "Ask about Code Genome, e.g. what is a bus factor?"}
                  style={{ flex: 1 }}
                  value={typed}
                />
                <button aria-label="Send" className="button button-primary" disabled={asking || !typed.trim()} type="submit"><SendIcon /></button>
              </form>
              {typedError && <Notice tone="error">{typedError}</Notice>}
              {typedAnswer && !live && (
                <div style={{ display: "grid", gap: 8 }}>
                  <strong>{typedAnswer.question}</strong>
                  <Markdown source={typedAnswer.answer.answer} />
                  {typedAnswer.answer.sources.length > 0 && (
                    <div className="chip-row">
                      {typedAnswer.answer.sources.map((source) => <span className="chip" key={source.id} title={source.excerpt}>{source.path} › {source.title}</span>)}
                    </div>
                  )}
                  <small className="muted">{typedAnswer.answer.limitations.join(" ")}</small>
                </div>
              )}
              <p className="muted small">
                Typed questions answer from Code Genome&apos;s documentation. For a repository&apos;s code, use voice or that repository&apos;s{" "}
                {repositories[0] ? <Link href={`/r/${repositories[0].id}/ask`}>Ask</Link> : "Ask"} page.
              </p>
            </div>
          </Panel>

          <Panel title="Conversation" description="What was said, what the assistant looked up, and what it opened." flush>
            {lines.length === 0 ? (
              <p className="muted small" style={{ padding: 20 }}>Nothing yet. Start a call and ask something.</p>
            ) : (
              <div className="list" style={{ maxHeight: 560, overflow: "auto" }}>
                {lines.map((line) =>
                  line.kind === "tool" ? (
                    <div className="list-row" key={line.id} style={{ display: "grid", gap: 4 }}>
                      <span className="badge badge-hema" style={{ justifySelf: "start" }}>{line.label}</span>
                      {line.question && <small>{line.question}</small>}
                      {line.error ? <small className="muted">{line.error}</small> : line.text && <small className="muted">{line.text.slice(0, 280)}{line.text.length > 280 ? "…" : ""}</small>}
                      {line.sources.length > 0 && <small className="muted truncate">Sources: {line.sources.join(" · ")}</small>}
                    </div>
                  ) : (
                    <div className="list-row" key={line.id}>
                      <strong style={{ minWidth: 64 }}>{line.kind === "user" ? "You" : "Assistant"}</strong>
                      <span className="grow">{line.text}</span>
                    </div>
                  ),
                )}
              </div>
            )}
          </Panel>
        </div>
      </div>
    </>
  );
}
