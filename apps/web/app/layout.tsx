import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Noto_Sans_Devanagari, Schibsted_Grotesk } from "next/font/google";

import { AppShell } from "../components/shell";
import "./styles.css";

const sans = Schibsted_Grotesk({ subsets: ["latin"], variable: "--font-schibsted", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains", display: "swap" });
const devanagari = Noto_Sans_Devanagari({
  subsets: ["devanagari"],
  weight: ["400", "600", "700"],
  variable: "--font-devanagari",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Code Genome",
  description: "Read a codebase from its evidence: architecture, history, risk, and delivery claims, with cited answers by chat or voice.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f4f9" },
    { media: "(prefers-color-scheme: dark)", color: "#13111c" },
  ],
};

// Applies a saved theme before first paint so there is no flash of the wrong theme.
const themeScript = `try{var t=localStorage.getItem("cg-theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html className={`${sans.variable} ${mono.variable} ${devanagari.variable}`} lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body suppressHydrationWarning>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
