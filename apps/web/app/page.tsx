"use client";

import Link from "next/link";

import { AuditIcon, ChatIcon, GraphIcon, HelixMark, MicIcon, PlusIcon, PulseIcon } from "../components/icons";
import { TopBar } from "../components/shell";
import { Empty, Notice } from "../components/ui";
import { useWorkspace } from "../components/workspace";
import { relativeTime, repoName } from "../lib/format";

const capabilities = [
  { icon: GraphIcon, title: "See the structure", text: "Files, symbols, and imports extracted with Tree-sitter, pinned to one commit." },
  { icon: PulseIcon, title: "Know what is risky", text: "Hotspots and co-change show where edits tend to break things, and what moves together." },
  { icon: ChatIcon, title: "Ask in plain words", text: "Answers cite the modules and commits they came from. English, Hindi, and Hinglish." },
  { icon: MicIcon, title: "Talk it through", text: "A voice agent on Gemini 3.8 Live that looks up evidence before it answers." },
  { icon: AuditIcon, title: "Check delivery claims", text: "Paste a status update and see which claims the repository supports." },
];

const stateLabel: Record<string, string> = {
  SUCCEEDED: "Analyzed",
  RUNNING: "Analyzing",
  QUEUED: "Queued",
  FAILED: "Last run failed",
};

export default function Home() {
  const { repositories, states, loading, error, setDialog, reload } = useWorkspace();

  return (
    <>
      <TopBar
        title="Repositories"
        actions={<button className="button button-primary" onClick={() => setDialog("add-repository")} type="button"><PlusIcon size={16} />Add repository</button>}
      />
      <div className="content">
        <section className="home-hero">
          <h1>Read a codebase from its evidence</h1>
          <p>Every answer here links back to a file, a commit, or a pinned snapshot. When the repository cannot support a claim, Code Genome says so.</p>
        </section>

        {error && (
          <Notice tone="error" title="Repositories could not be loaded">
            {error} <button className="button button-small button-secondary" onClick={reload} style={{ marginLeft: 8 }} type="button">Try again</button>
          </Notice>
        )}

        {loading ? (
          <div className="repo-cards">{[0, 1, 2].map((item) => <div className="skeleton" key={item} style={{ height: 150, borderRadius: 16 }} />)}</div>
        ) : repositories.length === 0 && !error ? (
          <div className="panel">
            <Empty
              centered
              icon={<HelixMark size={40} />}
              title="Add your first repository"
              action={<button className="button button-primary" onClick={() => setDialog("add-repository")} type="button"><PlusIcon size={16} />Add repository</button>}
            >
              Paste a GitHub URL. Code Genome mirrors it read-only, analyzes the branch you choose, and keeps every result tied to that commit.
            </Empty>
          </div>
        ) : (
          <div className="repo-cards">
            {repositories.map((repository) => {
              const { owner, name } = repoName(repository.external_id);
              const state = states[repository.id];
              return (
                <Link className="panel repo-card" href={`/r/${repository.id}`} key={repository.id}>
                  <div className="repo-card-top">
                    <div style={{ minWidth: 0 }}>
                      <h3 className="truncate">{name}</h3>
                      <span className="muted small">{owner}</span>
                    </div>
                    <span className={`badge ${state === "SUCCEEDED" ? "badge-ok" : state === "FAILED" ? "badge-bad" : state ? "badge-warn" : ""}`}>
                      {state ? stateLabel[state] : "Not analyzed"}
                    </span>
                  </div>
                  <div className="muted small">Branch <code>{repository.default_branch}</code> · added {relativeTime(repository.created_at)}</div>
                </Link>
              );
            })}
          </div>
        )}

        <section aria-label="What Code Genome does" className="capabilities">
          {capabilities.map(({ icon: Icon, title, text }) => (
            <div className="capability" key={title}>
              <Icon size={20} />
              <h3>{title}</h3>
              <p>{text}</p>
            </div>
          ))}
        </section>
      </div>
    </>
  );
}
