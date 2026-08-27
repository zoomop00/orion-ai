import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "ORION",
  description: "ORION - autonomous market intelligence. Mission 10 Phase 1 visual prototype.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // The interface paints edge to edge, so it has to opt into the notch area and
  // then respect it with env(safe-area-inset-*) in the layout stylesheet.
  viewportFit: "cover",
  themeColor: "#04060b",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
