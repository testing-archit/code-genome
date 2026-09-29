type IconProps = { size?: number; className?: string };

function Icon({ size = 18, className, children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="none"
      height={size}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.7"
      viewBox="0 0 24 24"
      width={size}
    >
      {children}
    </svg>
  );
}

export function HelixMark({ size = 20, className }: IconProps) {
  return (
    <svg aria-hidden="true" className={className} height={size} viewBox="0 0 36 36" width={size}>
      <path d="M9 4c0 9 18 19 18 28" fill="none" stroke="currentColor" strokeWidth="2.6" />
      <path d="M27 4C27 13 9 23 9 32" fill="none" stroke="currentColor" strokeWidth="2.6" />
      <path d="M11 9h14M9 18h18M11 27h14" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

export const PlusIcon = (p: IconProps) => <Icon {...p}><path d="M12 5v14M5 12h14" /></Icon>;
export const SearchIcon = (p: IconProps) => <Icon {...p}><circle cx="11" cy="11" r="6.5" /><path d="m20 20-4.2-4.2" /></Icon>;
export const OverviewIcon = (p: IconProps) => <Icon {...p}><path d="M4 18V9M9 18V5M14 18v-6M19 18V8" /></Icon>;
export const ChatIcon = (p: IconProps) => <Icon {...p}><path d="M5 18.5V6.5A1.5 1.5 0 0 1 6.5 5h11A1.5 1.5 0 0 1 19 6.5v8a1.5 1.5 0 0 1-1.5 1.5H9z" /><path d="M9 10h6M9 13h4" /></Icon>;
export const MicIcon = (p: IconProps) => <Icon {...p}><rect height="11" rx="3" width="6" x="9" y="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" /></Icon>;
export const MicOffIcon = (p: IconProps) => <Icon {...p}><path d="M15 9.4V6a3 3 0 0 0-5.7-1.3M9 9v2a3 3 0 0 0 4.6 2.5M5.5 11a6.5 6.5 0 0 0 10.4 5.2M18.5 11c0 .9-.2 1.8-.5 2.6M12 17.5V21M4 4l16 16" /></Icon>;
export const GraphIcon = (p: IconProps) => <Icon {...p}><circle cx="6" cy="7" r="2.2" /><circle cx="18" cy="6" r="2.2" /><circle cx="12" cy="17" r="2.2" /><path d="M8 8l2.8 7M16.6 7.8 13 15.2M8.2 6.8l7.6-.6" /></Icon>;
export const FilesIcon = (p: IconProps) => <Icon {...p}><path d="M4 7.5A1.5 1.5 0 0 1 5.5 6H10l2 2h6.5A1.5 1.5 0 0 1 20 9.5v8a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z" /></Icon>;
export const FileIcon = (p: IconProps) => <Icon {...p}><path d="M7 3.5h7l4 4v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1z" /><path d="M14 3.5V8h4" /></Icon>;
export const HistoryIcon = (p: IconProps) => <Icon {...p}><circle cx="12" cy="6" r="2" /><circle cx="12" cy="18" r="2" /><path d="M12 8v8M17 12h-5" /><circle cx="19" cy="12" r="2" /></Icon>;
export const LayersIcon = (p: IconProps) => <Icon {...p}><path d="m12 4 8 4-8 4-8-4z" /><path d="m4 12 8 4 8-4M4 16l8 4 8-4" /></Icon>;
export const PulseIcon = (p: IconProps) => <Icon {...p}><path d="M3 12h4l2-5 4 10 2-5h6" /></Icon>;
export const AuditIcon = (p: IconProps) => <Icon {...p}><path d="M8 4h8M9 4v2h6V4M6 6h12v14H6z" /><path d="m9 13 2 2 4-4" /></Icon>;
export const SettingsIcon = (p: IconProps) => <Icon {...p}><circle cx="12" cy="12" r="3" /><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8" /></Icon>;
export const ActivityIcon = (p: IconProps) => <Icon {...p}><path d="M4 6h16M4 12h10M4 18h13" /></Icon>;
export const BranchIcon = (p: IconProps) => <Icon {...p}><circle cx="7" cy="5.5" r="2" /><circle cx="7" cy="18.5" r="2" /><circle cx="17" cy="8" r="2" /><path d="M7 7.5v9M17 10c0 4-10 2-10 6.5" /></Icon>;
export const CloseIcon = (p: IconProps) => <Icon {...p}><path d="m6 6 12 12M18 6 6 18" /></Icon>;
export const MenuIcon = (p: IconProps) => <Icon {...p}><path d="M4 7h16M4 12h16M4 17h16" /></Icon>;
export const SunIcon = (p: IconProps) => <Icon {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" /></Icon>;
export const MoonIcon = (p: IconProps) => <Icon {...p}><path d="M19 14.5A7.5 7.5 0 0 1 9.5 5a7.5 7.5 0 1 0 9.5 9.5z" /></Icon>;
export const SendIcon = (p: IconProps) => <Icon {...p}><path d="M5 12h13M13 6l6 6-6 6" /></Icon>;
export const TrashIcon = (p: IconProps) => <Icon {...p}><path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" /></Icon>;
export const CopyIcon = (p: IconProps) => <Icon {...p}><rect height="11" rx="1.5" width="11" x="8" y="8" /><path d="M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8" /></Icon>;
export const PhoneOffIcon = (p: IconProps) => <Icon {...p}><path d="M4.5 13.5c4.2-4 10.8-4 15 0l-2 2.5-3-1.2v-2.3a8 8 0 0 0-5 0v2.3l-3 1.2z" /></Icon>;
export const DownloadIcon = (p: IconProps) => <Icon {...p}><path d="M12 4v11M7 10.5l5 5 5-5M5 20h14" /></Icon>;
export const LockIcon = (p: IconProps) => <Icon {...p}><rect height="9" rx="1.5" width="14" x="5" y="10.5" /><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" /></Icon>;
export const PlayIcon = (p: IconProps) => <Icon {...p}><path d="M8 5.5v13l10.5-6.5z" /></Icon>;
export const ModelsIcon = (p: IconProps) => <Icon {...p}><circle cx="5" cy="6" r="1.8" /><circle cx="5" cy="18" r="1.8" /><circle cx="12" cy="12" r="1.8" /><circle cx="19" cy="7" r="1.8" /><circle cx="19" cy="17" r="1.8" /><path d="M6.6 6.9 10.4 11M6.6 17.1l3.8-4.1M13.7 11.2l3.6-3.2M13.7 12.8l3.6 3.2" /></Icon>;
