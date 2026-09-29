"use client";

import type { AnswerLanguage } from "@code-genome/contracts";

export function Empty({
  title,
  children,
  action,
  icon,
  centered = false,
}: {
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
  icon?: React.ReactNode;
  centered?: boolean;
}) {
  return (
    <div className={`empty ${centered ? "centered" : ""}`}>
      {icon && <span className="empty-mark">{icon}</span>}
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action && <div style={{ marginTop: 8 }}>{action}</div>}
    </div>
  );
}

export function Notice({ tone = "info", title, children }: { tone?: "info" | "error" | "warn"; title?: string; children: React.ReactNode }) {
  return (
    <div className={`notice ${tone === "info" ? "" : `notice-${tone}`}`} role={tone === "error" ? "alert" : undefined}>
      <div>{title && <strong>{title}</strong>}{children}</div>
    </div>
  );
}

export function Panel({
  title,
  description,
  actions,
  children,
  flush = false,
  id,
}: {
  title?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  flush?: boolean;
  id?: string;
}) {
  return (
    <section className="panel" id={id}>
      {(title || actions) && (
        <div className="panel-head">
          <div style={{ minWidth: 0 }}>
            {title && <h2>{title}</h2>}
            {description && <p>{description}</p>}
          </div>
          {actions}
        </div>
      )}
      <div className={flush ? "panel-flush" : "panel-body"}>{children}</div>
    </section>
  );
}

export function Loading({ rows = 3, height = 40 }: { rows?: number; height?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading" style={{ display: "grid", gap: 8, padding: 16 }}>
      {Array.from({ length: rows }, (_, index) => <div className="skeleton" key={index} style={{ height }} />)}
    </div>
  );
}

export function Meter({ value, tone = "hema" }: { value: number; tone?: "hema" | "eosin" }) {
  return (
    <div aria-hidden="true" className={`meter ${tone === "eosin" ? "eosin" : ""}`} style={{ width: 80, flex: "none" }}>
      <i style={{ width: `${Math.max(3, Math.min(1, value) * 100)}%` }} />
    </div>
  );
}

export function SearchField({ value, onChange, placeholder, label }: { value: string; onChange: (value: string) => void; placeholder: string; label: string }) {
  return (
    <label className="search-input" style={{ display: "block" }}>
      <span className="sr-only">{label}</span>
      <svg aria-hidden="true" fill="none" height="16" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24" width="16"><circle cx="11" cy="11" r="6.5" /><path d="m20 20-4.2-4.2" /></svg>
      <input className="input" onChange={(event) => onChange(event.target.value)} placeholder={placeholder} type="search" value={value} />
    </label>
  );
}

export const languages: Array<{ value: AnswerLanguage; label: string; hint: string }> = [
  { value: "auto", label: "Auto", hint: "Reply in the language you use" },
  { value: "en", label: "English", hint: "Always reply in English" },
  { value: "hi", label: "हिन्दी", hint: "Always reply in Hindi" },
  { value: "hinglish", label: "Hinglish", hint: "Hindi and English mixed, in Roman script" },
];

export function LanguagePicker({ value, onChange, disabled = false }: { value: AnswerLanguage; onChange: (value: AnswerLanguage) => void; disabled?: boolean }) {
  return (
    <div aria-label="Answer language" className="segmented" role="group">
      {languages.map((language) => (
        <button
          aria-pressed={value === language.value}
          disabled={disabled}
          key={language.value}
          lang={language.value === "hi" ? "hi" : undefined}
          onClick={() => onChange(language.value)}
          title={language.hint}
          type="button"
        >
          {language.label}
        </button>
      ))}
    </div>
  );
}

const languageKey = "cg-language";
export function readLanguage(): AnswerLanguage {
  try {
    const stored = localStorage.getItem(languageKey);
    if (stored === "auto" || stored === "en" || stored === "hi" || stored === "hinglish") return stored;
  } catch {
    // Storage unavailable.
  }
  return "auto";
}
export function saveLanguage(language: AnswerLanguage) {
  try {
    localStorage.setItem(languageKey, language);
  } catch {
    // Storage unavailable.
  }
}
