import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Scrapper — Storyboard",
  description: "Paste links, scrape videos, compile into one download.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
