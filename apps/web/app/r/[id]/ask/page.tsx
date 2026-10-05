"use client";

import type { AnswerLanguage, ConversationMessage, ConversationSummary } from "@code-genome/contracts";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, KeyboardEvent, Suspense, useCallback, useEffect, useRef, useState } from "react";

import { CopyIcon, HelixMark, MenuIcon, MicIcon, PlayIcon, PlusIcon, SendIcon, TrashIcon } from "../../../../components/icons";
import { EvidenceChips, RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { LanguagePicker, Notice, readLanguage, saveLanguage } from "../../../../components/ui";
import { useWorkspace } from "../../../../components/workspace";
import { api, errorMessage } from "../../../../lib/api";
import { isRefusal, relativeTime, shortSha } from "../../../../lib/format";

const suggestions: Record<AnswerLanguage, string[]> = {
  auto: ["What changed recently?", "Which files are the riskiest to edit?", "Billing ka code kahan hai?", "हाल में क्या बदला है?"],
  en: ["What changed recently?", "Which files are the riskiest to edit?", "Where does authentication live?"],
  hi: ["हाल में क्या बदला है?", "सबसे ज़्यादा बदलने वाली फ़ाइलें कौन सी हैं?", "बिलिंग मॉड्यूल कहाँ है?"],
  hinglish: ["Recently kya change hua hai?", "Sabse risky files kaunsi hain?", "Billing module kahan hai?"],
};

type PendingTurn = { question: string; draft: string; model: string | null; replaced: boolean } | null;

/** Hide `[1]`-style citation markers (including a half-received one) in streamed drafts. */
function cleanDraft(text: string): string {
  return text
    .replace(/\s*\[\d{1,2}(?:\s*,\s*\d{1,2})*\]/g, "")
    .replace(/\s*\[[\d,\s]*$/, "")
    .replace(/[ \t]+([.,;:!?।])/g, "$1");
}

export default function AskPage() {
  return (
    <RequiresSnapshot what="Answers">
      <Suspense fallback={null}>
        <Chat />
      </Suspense>
    </RequiresSnapshot>
  );
}

function Chat() {
  const { repository, published } = useRepo();
  const { toast } = useWorkspace();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [threads, setThreads] = useState<ConversationSummary[] | null>(null);
  const [activeId, setActiveIdState] = useState<string | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [pending, setPending] = useState<PendingTurn>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [language, setLanguage] = useState<AnswerLanguage>("auto");
  const [threadsOpen, setThreadsOpen] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  // Conversations created by sending a message already hold their messages locally;
  // fetching them again could overwrite the first answer with an older copy.
  const createdLocally = useRef<string | null>(null);
  const setActiveId = useCallback((id: string | null) => {
    if (id === null) setMessages([]);
    setActiveIdState(id);
  }, []);
  const initialQuestion = useRef(searchParams.get("q"));

  useEffect(() => {
    // Language preference lives in localStorage, which is only readable after mount.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLanguage(readLanguage());
  }, []);

  useEffect(() => {
    let active = true;
    api
      .listConversations(repository.id)
      .then((items) => {
        if (!active) return;
        setThreads(items);
        if (!initialQuestion.current && items[0]) setActiveIdState(items[0].id);
      })
      .catch((caught: unknown) => {
        if (active) {
          setThreads([]);
          setError(errorMessage(caught, "Conversations could not be loaded."));
        }
      });
    return () => {
      active = false;
    };
  }, [repository.id]);

  useEffect(() => {
    if (!activeId || createdLocally.current === activeId) return;
    let active = true;
    api
      .getConversation(activeId)
      .then((conversation) => {
        if (active) setMessages(conversation.messages);
      })
      .catch((caught: unknown) => {
        if (active) setError(errorMessage(caught, "The conversation could not be opened."));
      });
    return () => {
      active = false;
    };
  }, [activeId]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, pending]);

  const send = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (question.length < 2 || pending) return;
      setError(null);
      setPending({ question, draft: "", model: null, replaced: false });
      setDraft("");
      try {
        let conversationId = activeId;
        if (!conversationId) {
          const created = await api.createConversation(repository.id);
          conversationId = created.id;
          createdLocally.current = created.id;
          setMessages([]);
          setThreads((current) => [created, ...(current ?? [])]);
          setActiveIdState(created.id);
        }
        const turn = await api.streamMessage(conversationId, question, language, {
          onStatus: (model) => setPending((current) => (current ? { ...current, model } : current)),
          onDelta: (text) => setPending((current) => (current ? { ...current, draft: current.draft + text } : current)),
          onFallback: () => setPending((current) => (current ? { ...current, draft: "", replaced: true } : current)),
        });
        setMessages((current) => [...current, turn.user_message, turn.assistant_message]);
        setThreads((current) => [turn.conversation, ...(current ?? []).filter((item) => item.id !== turn.conversation.id)]);
      } catch (caught) {
        setError(errorMessage(caught, "The question could not be answered."));
        setDraft(question);
      } finally {
        setPending(null);
      }
    },
    [activeId, language, pending, repository.id],
  );

  useEffect(() => {
    const question = initialQuestion.current;
    if (!question || threads === null) return;
    initialQuestion.current = null;
    router.replace(`/r/${repository.id}/ask`);
    void send(question);
  }, [repository.id, router, send, threads]);

  async function remove(id: string) {
    try {
      await api.deleteConversation(id);
      setThreads((current) => (current ?? []).filter((item) => item.id !== id));
      if (activeId === id) setActiveId(null);
      toast("Conversation deleted. Its cited answers remain in the audit record.");
    } catch (caught) {
      toast(errorMessage(caught, "The conversation could not be deleted."));
    }
  }

  function changeLanguage(value: AnswerLanguage) {
    setLanguage(value);
    saveLanguage(value);
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send(draft);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send(draft);
    }
  }

  return (
    <div className="chat" style={{ position: "relative" }}>
      <aside className="chat-threads" data-open={threadsOpen} aria-label="Conversations">
        <div className="chat-threads-head">
          <button className="button button-secondary" onClick={() => { setActiveId(null); setThreadsOpen(false); }} style={{ width: "100%" }} type="button">
            <PlusIcon size={16} />New conversation
          </button>
        </div>
        <div className="chat-thread-list">
          {threads === null && <div className="skeleton" style={{ height: 40 }} />}
          {threads?.length === 0 && <p className="muted small" style={{ padding: 10 }}>Your conversations with this repository appear here. Only you can see them.</p>}
          {threads?.map((thread) => (
            <div aria-current={thread.id === activeId} className="thread" key={thread.id}>
              <button onClick={() => { setActiveId(thread.id); setThreadsOpen(false); }} type="button">
                <span className="truncate" style={{ display: "block" }}>{thread.title}</span>
                <small>{relativeTime(thread.updated_at)} · {Math.floor(thread.message_count / 2)} questions</small>
              </button>
              <button aria-label={`Delete ${thread.title}`} className="thread-delete" onClick={() => void remove(thread.id)} type="button"><TrashIcon size={15} /></button>
            </div>
          ))}
        </div>
      </aside>

      <div className="chat-main">
        <div className="chat-toolbar">
          <div style={{ display: "flex", gap: 8, alignItems: "center", minWidth: 0 }}>
            <button aria-label="Show conversations" className="icon-button menu-button" onClick={() => setThreadsOpen((open) => !open)} type="button"><MenuIcon /></button>
            <span className="muted small truncate">Grounded in snapshot <code>{shortSha(published?.snapshot_sha, 8)}</code></span>
          </div>
          <LanguagePicker onChange={changeLanguage} value={language} />
        </div>

        <div aria-live="polite" className="chat-log" ref={logRef}>
          {messages.length === 0 && !pending && (
            <div className="empty centered" style={{ alignSelf: "center" }}>
              <span className="empty-mark"><HelixMark size={34} /></span>
              <h3>Ask what the repository can show</h3>
              <p>Answers come only from indexed modules, hotspots, and commit messages, and cite each one. Ask in English, Hindi, or Hinglish.</p>
              <div className="suggestions" style={{ justifyContent: "center", marginTop: 8 }}>
                {suggestions[language].map((item) => (
                  <button className="suggestion" key={item} onClick={() => void send(item)} type="button">{item}</button>
                ))}
              </div>
            </div>
          )}
          {messages.map((message) => <Message key={message.id} message={message} />)}
          {pending && (
            <>
              <div className="msg msg-user"><div className="msg-body">{pending.question}</div></div>
              <div className="msg msg-assistant" aria-busy="true">
                <div className="msg-who">
                  <i><HelixMark size={12} /></i>
                  {pending.replaced
                    ? "Draft failed citation checks, showing the extractive answer"
                    : pending.model
                      ? `${pending.model} is writing · checking citations when done`
                      : "Searching repository evidence"}
                </div>
                {pending.draft ? (
                  <div className="msg-body" aria-live="polite">{cleanDraft(pending.draft)}</div>
                ) : (
                  <span className="typing" aria-label="Answer in progress"><i /><i /><i /></span>
                )}
              </div>
            </>
          )}
        </div>

        <form className="composer" onSubmit={onSubmit}>
          {error && <Notice tone="error">{error}</Notice>}
          <div className="composer-box">
            <label className="sr-only" htmlFor="chat-input">Your question</label>
            <textarea
              id="chat-input"
              maxLength={2000}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
              placeholder={language === "hi" ? "अपना सवाल लिखें…" : language === "hinglish" ? "Apna sawaal likho…" : "Ask about modules, recent changes, or risky files…"}
              rows={1}
              value={draft}
            />
            <Dictation language={language} onText={(text) => setDraft((current) => (current ? `${current} ${text}` : text))} />
            <button aria-label="Send question" className="button button-primary" disabled={Boolean(pending) || draft.trim().length < 2} type="submit"><SendIcon size={16} /></button>
          </div>
          <div className="composer-hint">
            <span>Enter to send, Shift+Enter for a new line</span>
            <span>Answers are limited to the latest published snapshot</span>
          </div>
        </form>
      </div>
    </div>
  );
}

function Message({ message }: { message: ConversationMessage }) {
  const { toast } = useWorkspace();
  const [rating, setRating] = useState<-1 | 1 | null>(null);
  const [showLimits, setShowLimits] = useState(false);

  if (message.role === "user") {
    return <div className="msg msg-user"><div className="msg-body">{message.content}</div></div>;
  }
  const answer = message.answer;
  const refusal = answer ? isRefusal(answer.answer, answer.evidence_ids) : false;
  const hindi = /[ऀ-ॿ]/.test(message.content);

  async function rate(value: -1 | 1) {
    if (!answer) return;
    try {
      await api.rateAnswer(answer.id, value);
      setRating(value);
    } catch (caught) {
      toast(errorMessage(caught, "Feedback could not be saved."));
    }
  }

  return (
    <article className={`msg msg-assistant ${refusal ? "msg-refusal" : ""}`}>
      <div className="msg-who">
        <i><HelixMark size={12} /></i>
        {answer?.retrieval_version.includes("gemini") ? "Gemini, from cited evidence" : "Extracted from evidence"}
        {message.channel === "voice" && <span className="badge">voice</span>}
      </div>
      <div className="msg-body" lang={hindi ? "hi" : undefined}>{message.content}</div>
      {answer && <EvidenceChips ids={answer.evidence_ids} />}
      {answer && showLimits && (
        <div className="limitations">
          {answer.limitations.map((item) => <span key={item}>{item}</span>)}
          <span>Snapshot {shortSha(answer.scope.snapshot_sha, 12)} · {answer.retrieval_version}</span>
        </div>
      )}
      <div className="msg-foot">
        {answer && <button aria-pressed={rating === 1} onClick={() => void rate(1)} type="button">Useful</button>}
        {answer && <button aria-pressed={rating === -1} onClick={() => void rate(-1)} type="button">Not useful</button>}
        <button onClick={() => { void navigator.clipboard.writeText(message.content); toast("Answer copied"); }} type="button"><CopyIcon size={13} /> Copy</button>
        <ReadAloud text={message.content} />
        {answer && <button aria-expanded={showLimits} onClick={() => setShowLimits((open) => !open)} type="button">{showLimits ? "Hide limits" : "Limits and scope"}</button>}
      </div>
    </article>
  );
}

function ReadAloud({ text }: { text: string }) {
  const [speaking, setSpeaking] = useState(false);
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return null;
  function toggle() {
    if (speaking) {
      window.speechSynthesis.cancel();
      setSpeaking(false);
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text);
    const hindi = /[ऀ-ॿ]/.test(text);
    utterance.lang = hindi ? "hi-IN" : "en-IN";
    const voice = window.speechSynthesis.getVoices().find((item) => item.lang.startsWith(hindi ? "hi" : "en-IN"));
    if (voice) utterance.voice = voice;
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
    setSpeaking(true);
  }
  return <button aria-pressed={speaking} onClick={toggle} type="button"><PlayIcon size={13} /> {speaking ? "Stop" : "Read aloud"}</button>;
}

type Recognition = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
};

/** Browser speech-to-text for the text box. Uses hi-IN for Hindi and Hinglish, en-IN otherwise. */
function Dictation({ language, onText }: { language: AnswerLanguage; onText: (text: string) => void }) {
  const [listening, setListening] = useState(false);
  const recognition = useRef<Recognition | null>(null);
  const [supported, setSupported] = useState(false);

  useEffect(() => {
    const win = window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown };
    // Feature detection has to wait for the browser.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSupported(Boolean(win.SpeechRecognition ?? win.webkitSpeechRecognition));
    return () => recognition.current?.stop();
  }, []);

  if (!supported) return null;

  function toggle() {
    if (listening) {
      recognition.current?.stop();
      return;
    }
    const win = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };
    const Constructor = win.SpeechRecognition ?? win.webkitSpeechRecognition;
    if (!Constructor) return;
    const instance = new Constructor();
    instance.lang = language === "hi" || language === "hinglish" ? "hi-IN" : "en-IN";
    instance.interimResults = false;
    instance.continuous = false;
    instance.onresult = (event) => {
      const text = Array.from(event.results).map((result) => result[0]?.transcript ?? "").join(" ").trim();
      if (text) onText(text);
    };
    instance.onend = () => setListening(false);
    instance.onerror = () => setListening(false);
    recognition.current = instance;
    instance.start();
    setListening(true);
  }

  return (
    <button
      aria-label={listening ? "Stop dictation" : "Dictate question"}
      aria-pressed={listening}
      className="icon-button"
      onClick={toggle}
      style={listening ? { color: "var(--eosin)", borderColor: "var(--eosin)" } : { border: 0 }}
      title="Dictate"
      type="button"
    >
      <MicIcon size={17} />
    </button>
  );
}
