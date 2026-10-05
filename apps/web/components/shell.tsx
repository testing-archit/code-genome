"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { errorMessage, workspaceId } from "../lib/api";
import { repoName } from "../lib/format";
import {
  BugIcon,
  GenomeIcon,
  ActivityIcon,
  AuditIcon,
  ChatIcon,
  CloseIcon,
  CompareIcon,
  DiffIcon,
  DocIcon,
  FilesIcon,
  GraphIcon,
  HelixMark,
  HistoryIcon,
  HotspotIcon,
  LayersIcon,
  MenuIcon,
  MicIcon,
  ModelsIcon,
  MoonIcon,
  OverviewIcon,
  PlusIcon,
  PulseIcon,
  SearchIcon,
  SettingsIcon,
  SunIcon,
} from "./icons";
import { useWorkspace, WorkspaceProvider } from "./workspace";

export type SectionGroup = "Understand" | "Predict" | "Explain" | "Repository";

/* Grouped by the three jobs the product does: understand the system, predict what a change
   will do, and explain it in plain language. Every view is always visible in the rail. */
export const repoSections = [
  { slug: "", label: "Overview", group: null, icon: OverviewIcon, keywords: "summary genome status health score" },
  { slug: "genome", label: "Genome graph", group: "Understand", icon: GenomeIcon, keywords: "software genome graph nodes relationships calls developers commits data stores apis" },
  { slug: "explorer", label: "Architecture explorer", group: "Understand", icon: LayersIcon, keywords: "components map modules architecture dependencies" },
  { slug: "docs", label: "Generated docs", group: "Understand", icon: DocIcon, keywords: "documentation architecture modules data flow dependencies business logic risk report markdown" },
  { slug: "architecture", label: "Modules and hotspots", group: "Understand", icon: HotspotIcon, keywords: "modules co-change hotspots" },
  { slug: "graph", label: "Dependency graph", group: "Understand", icon: GraphIcon, keywords: "imports nodes edges files" },
  { slug: "files", label: "Files", group: "Understand", icon: FilesIcon, keywords: "tree explorer manifest" },
  { slug: "history", label: "Evolution timeline", group: "Understand", icon: HistoryIcon, keywords: "commits timeline authors history" },
  { slug: "bugs", label: "Bug history", group: "Understand", icon: BugIcon, keywords: "bugs szz fix commits introduced regression origin blame" },
  { slug: "change", label: "Change impact", group: "Predict", icon: DiffIcon, keywords: "what will break impact diff pull request pr patch change simulator" },
  { slug: "risk", label: "Risk", group: "Predict", icon: PulseIcon, keywords: "risk defect blast radius" },
  { slug: "compare", label: "Compare snapshots", group: "Predict", icon: CompareIcon, keywords: "compare snapshots branches drift" },
  { slug: "models", label: "Models", group: "Predict", icon: ModelsIcon, keywords: "machine learning ml models training evaluation defect prediction" },
  { slug: "ask", label: "Ask", group: "Explain", icon: ChatIcon, keywords: "chat chatbot question conversation archaeologist" },
  { slug: "voice", label: "Voice agent", group: "Explain", icon: MicIcon, keywords: "voice talk speak live hindi hinglish" },
  { slug: "search", label: "Search", group: "Explain", icon: SearchIcon, keywords: "semantic search bm25 find code" },
  { slug: "audit", label: "Delivery audit", group: "Explain", icon: AuditIcon, keywords: "claims report verify" },
  { slug: "settings", label: "Settings", group: "Repository", icon: SettingsIcon, keywords: "private access token connection automation webhook" },
] as const;

export const sectionGroups: SectionGroup[] = ["Understand", "Predict", "Explain", "Repository"];

export function sectionFor(pathname: string, repositoryId: string) {
  const base = `/r/${repositoryId}`;
  return (
    repoSections.find((section) => section.slug && pathname.startsWith(`${base}/${section.slug}`)) ??
    repoSections[0]
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <WorkspaceProvider>
      <ShellFrame>{children}</ShellFrame>
    </WorkspaceProvider>
  );
}

function ShellFrame({ children }: { children: React.ReactNode }) {
  const { repositories, states, loading, dialog, setDialog, toasts, railOpen, setRailOpen } = useWorkspace();
  const pathname = usePathname();
  const activeRepoId = pathname.startsWith("/r/") ? pathname.split("/")[2] : null;

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setDialog(dialog === "palette" ? null : "palette");
      } else if (event.key === "Escape" && dialog) {
        setDialog(null);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dialog, setDialog]);

  useEffect(() => setRailOpen(false), [pathname, setRailOpen]);

  return (
    <div className="app">
      {railOpen && <div className="overlay" onClick={() => setRailOpen(false)} role="presentation" />}
      <aside className="rail" data-open={railOpen} aria-label="Workspace">
        <Link className="brand" href="/">
          <span className="brand-mark"><HelixMark /></span>
          <span><strong>Code Genome</strong><small>Repository evidence</small></span>
        </Link>
        <nav className={`rail-section ${activeRepoId ? "rail-repos" : "grow"}`} aria-label="Repositories">
          <div className="rail-heading">
            <span>Repositories</span>
            <button aria-label="Add repository" onClick={() => setDialog("add-repository")} title="Add repository" type="button">
              <PlusIcon size={16} />
            </button>
          </div>
          {loading && [0, 1, 2].map((item) => <div className="skeleton" key={item} style={{ height: 36, margin: "2px 8px" }} />)}
          {!loading && repositories.length === 0 && (
            <p className="muted small" style={{ padding: "4px 8px" }}>Add a GitHub repository to start.</p>
          )}
          {repositories.map((repository) => {
            const { owner, name } = repoName(repository.external_id);
            return (
              <Link
                aria-current={activeRepoId === repository.id ? "true" : undefined}
                className="rail-link"
                href={`/r/${repository.id}`}
                key={repository.id}
              >
                <span className="repo-dot" data-state={states[repository.id] ?? "NONE"} />
                <span className="truncate">
                  {name}
                  <span className="repo-owner truncate">{owner}</span>
                </span>
              </Link>
            );
          })}
        </nav>
        {activeRepoId && repositories.some((item) => item.id === activeRepoId) && (
          <RepoNav pathname={pathname} repositoryId={activeRepoId} />
        )}
        <div className="rail-foot">
          <Link aria-current={pathname === "/activity" ? "page" : undefined} className="rail-link" href="/activity">
            <ActivityIcon /> Activity log
          </Link>
          <div className="workspace-chip"><span className="repo-dot" data-state="SUCCEEDED" />Workspace <code>{workspaceId}</code></div>
        </div>
      </aside>

      <div className="main">{children}</div>

      {dialog === "palette" && <CommandPalette onClose={() => setDialog(null)} />}
      {dialog === "add-repository" && <AddRepositoryDialog onClose={() => setDialog(null)} />}
      <div aria-live="polite" className="toast-stack">
        {toasts.map((item) => <div className="toast" key={item.id}>{item.text}</div>)}
      </div>
    </div>
  );
}

function RepoNav({ repositoryId, pathname }: { repositoryId: string; pathname: string }) {
  const base = `/r/${repositoryId}`;
  const current = sectionFor(pathname, repositoryId);
  const overview = repoSections[0];
  const OverviewGlyph = overview.icon;
  return (
    <nav aria-label="Repository views" className="rail-section grow rail-views">
      <Link aria-current={current.slug === "" ? "page" : undefined} className="rail-link" href={base}>
        <OverviewGlyph size={17} />{overview.label}
      </Link>
      {sectionGroups.map((group) => (
        <div className="rail-group" key={group}>
          <div className="rail-group-label">{group}</div>
          {repoSections
            .filter((section) => section.group === group)
            .map((section) => {
              const Icon = section.icon;
              return (
                <Link
                  aria-current={current.slug === section.slug ? "page" : undefined}
                  className="rail-link"
                  href={`${base}/${section.slug}`}
                  key={section.slug}
                >
                  <Icon size={17} />{section.label}
                </Link>
              );
            })}
        </div>
      ))}
    </nav>
  );
}

export function TopBar({ title, detail, actions }: { title: React.ReactNode; detail?: React.ReactNode; actions?: React.ReactNode }) {
  const { setDialog, setRailOpen } = useWorkspace();
  return (
    <header className="topbar">
      <button aria-label="Open navigation" className="icon-button menu-button" onClick={() => setRailOpen(true)} type="button">
        <MenuIcon />
      </button>
      <div className="topbar-title">
        <strong className="truncate">{title}</strong>
        {detail && <span className="muted small truncate">{detail}</span>}
      </div>
      {actions}
      <button className="search-trigger" onClick={() => setDialog("palette")} type="button" aria-label="Search and jump">
        <SearchIcon size={16} /><span>Search or jump to…</span><kbd>⌘K</kbd>
      </button>
      <ThemeToggle />
    </header>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark" | null>(null);
  useEffect(() => {
    const stored = document.documentElement.dataset.theme;
    const initial = stored === "light" || stored === "dark"
      ? stored
      : window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    // Reading the DOM theme after hydration keeps server and client markup identical.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTheme(initial);
  }, []);
  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("cg-theme", next);
    } catch {
      // Storage can be unavailable in private windows.
    }
    setTheme(next);
  }
  return (
    <button aria-label={theme === "dark" ? "Use light theme" : "Use dark theme"} className="icon-button" onClick={toggle} type="button">
      {theme === "dark" ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

type PaletteItem = { id: string; group: string; label: string; hint?: string; keywords: string; run: () => void };

function CommandPalette({ onClose }: { onClose: () => void }) {
  const { repositories, setDialog } = useWorkspace();
  const router = useRouter();
  const pathname = usePathname();
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const activeRepoId = pathname.startsWith("/r/") ? pathname.split("/")[2] : null;
  const activeRepo = repositories.find((item) => item.id === activeRepoId) ?? null;

  const items = useMemo<PaletteItem[]>(() => {
    const go = (href: string) => () => {
      router.push(href);
      onClose();
    };
    const list: PaletteItem[] = [];
    const trimmed = query.trim();
    if (activeRepo && trimmed.length > 2) {
      list.push({
        id: "ask",
        group: "Ask",
        label: `Ask “${trimmed}”`,
        hint: repoName(activeRepo.external_id).name,
        keywords: trimmed.toLowerCase(),
        run: go(`/r/${activeRepo.id}/ask?q=${encodeURIComponent(trimmed)}`),
      });
    }
    if (activeRepo) {
      for (const section of repoSections) {
        list.push({
          id: `section-${section.slug}`,
          group: repoName(activeRepo.external_id).name,
          label: section.label,
          keywords: `${section.label} ${section.keywords}`.toLowerCase(),
          run: go(`/r/${activeRepo.id}${section.slug ? `/${section.slug}` : ""}`),
        });
      }
    }
    for (const repository of repositories) {
      list.push({
        id: `repo-${repository.id}`,
        group: "Repositories",
        label: repository.external_id,
        hint: repository.default_branch,
        keywords: repository.external_id.toLowerCase(),
        run: go(`/r/${repository.id}`),
      });
    }
    list.push(
      { id: "add", group: "Workspace", label: "Add repository", keywords: "add new register repository github", run: () => setDialog("add-repository") },
      { id: "home", group: "Workspace", label: "All repositories", keywords: "home repositories", run: go("/") },
      { id: "activity", group: "Workspace", label: "Activity log", keywords: "audit activity events log", run: go("/activity") },
    );
    const needle = trimmed.toLowerCase();
    return needle ? list.filter((item) => item.id === "ask" || item.keywords.includes(needle) || item.label.toLowerCase().includes(needle)) : list;
  }, [activeRepo, onClose, query, repositories, router, setDialog]);

  const selected = Math.min(cursor, Math.max(0, items.length - 1));

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setCursor((selected + 1) % Math.max(1, items.length));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setCursor((selected - 1 + items.length) % Math.max(1, items.length));
    } else if (event.key === "Enter") {
      event.preventDefault();
      items[selected]?.run();
    }
  }

  let lastGroup = "";
  return (
    <>
      <div className="overlay" onClick={onClose} role="presentation" />
      <div aria-label="Search and jump" aria-modal="true" className="dialog palette" role="dialog">
        <div className="palette-input">
          <SearchIcon />
          <input
            aria-activedescendant={items[selected] ? `palette-${items[selected].id}` : undefined}
            aria-controls="palette-list"
            autoFocus
            onChange={(event) => {
              setQuery(event.target.value);
              setCursor(0);
            }}
            onKeyDown={onKeyDown}
            placeholder={activeRepo ? "Jump to a view, or type a question to ask" : "Jump to a repository or view"}
            role="combobox"
            aria-expanded="true"
            value={query}
          />
          <kbd>Esc</kbd>
        </div>
        <div className="palette-list" id="palette-list" role="listbox">
          {items.length === 0 && <p className="muted small" style={{ padding: 12 }}>No matches. Try a repository name or a view such as “graph”.</p>}
          {items.map((item, index) => {
            const header = item.group !== lastGroup ? <div className="palette-group" key={`g-${item.group}`}>{item.group}</div> : null;
            lastGroup = item.group;
            return (
              <div key={item.id}>
                {header}
                <button
                  aria-selected={index === selected}
                  className="palette-item"
                  id={`palette-${item.id}`}
                  onClick={item.run}
                  onMouseEnter={() => setCursor(index)}
                  role="option"
                  type="button"
                >
                  <span className="truncate">{item.label}</span>
                  {item.hint && <small>{item.hint}</small>}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}

function AddRepositoryDialog({ onClose }: { onClose: () => void }) {
  const { addRepository, toast } = useWorkspace();
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const repository = await addRepository(url.trim(), branch.trim());
      toast(`Added ${repository.external_id}`);
      onClose();
      router.push(`/r/${repository.id}`);
    } catch (caught) {
      setError(errorMessage(caught, "The repository could not be added."));
      setSaving(false);
      input.current?.focus();
    }
  }

  return (
    <>
      <div className="overlay" onClick={onClose} role="presentation" />
      <div aria-labelledby="add-repo-title" aria-modal="true" className="dialog" role="dialog">
        <div className="drawer-head">
          <div>
            <h2 id="add-repo-title">Add a repository</h2>
            <p className="muted small">Public GitHub repositories work right away. Private ones need a read-only token in Settings after you add them.</p>
          </div>
          <button aria-label="Close" className="icon-button" onClick={onClose} type="button"><CloseIcon /></button>
        </div>
        <form onSubmit={submit}>
          <label className="field">
            GitHub URL
            <input autoFocus className="input" maxLength={500} onChange={(event) => setUrl(event.target.value)} placeholder="https://github.com/owner/repository" ref={input} required type="url" value={url} />
            <small>Never paste credentials into the URL.</small>
          </label>
          <label className="field">
            Branch to analyze
            <input className="input" maxLength={255} onChange={(event) => setBranch(event.target.value)} required value={branch} />
          </label>
          {error && <div className="notice notice-error" role="alert">{error}</div>}
          <div className="dialog-actions">
            <button className="button button-ghost" onClick={onClose} type="button">Cancel</button>
            <button className="button button-primary" disabled={saving} type="submit">{saving ? "Adding…" : "Add repository"}</button>
          </div>
        </form>
      </div>
    </>
  );
}
