import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "CODE GENOME — Repository evidence",
  description: "Evidence-backed software intelligence and delivery auditing.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
