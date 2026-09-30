import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "GoBeyond — trail intelligence from verified map data",
    template: "%s · GoBeyond",
  },
  description:
    "Search a mountain, trail or region and get the trails that can actually be drawn: verified OpenStreetMap geometry, terrain, current conditions, a learned difficulty estimate, what to bring, and an assistant that answers only from the trail you selected.",
  applicationName: "GoBeyond",
  keywords: [
    "hiking trails",
    "trekking routes",
    "trail difficulty",
    "OpenStreetMap",
    "route planning",
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      // Browser extensions add attributes to <html> before React hydrates,
      // which would otherwise raise a hydration warning on every load.
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
