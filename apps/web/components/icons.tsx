type IconProps = { size?: number; className?: string };

export function HelixMark({ size = 34, className }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      height={size}
      viewBox="0 0 36 36"
      width={size}
    >
      <path d="M9 4c0 9 18 19 18 28" fill="none" stroke="currentColor" strokeWidth="2.4" />
      <path d="M27 4C27 13 9 23 9 32" fill="none" stroke="currentColor" strokeWidth="2.4" />
      <path d="M11 9h14M9 18h18M11 27h14" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

export function PlusIcon({ size = 18 }: IconProps) {
  return (
    <svg aria-hidden="true" height={size} viewBox="0 0 20 20" width={size}>
      <path d="M10 3v14M3 10h14" fill="none" stroke="currentColor" strokeWidth="1.8" />
    </svg>
  );
}

export function BranchIcon({ size = 18 }: IconProps) {
  return (
    <svg aria-hidden="true" height={size} viewBox="0 0 20 20" width={size}>
      <circle cx="5" cy="4" fill="none" r="2" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="15" cy="6" fill="none" r="2" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="5" cy="16" fill="none" r="2" stroke="currentColor" strokeWidth="1.5" />
      <path d="M5 6v8M7 11h2a6 6 0 0 0 6-3" fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

