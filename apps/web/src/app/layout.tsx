import type { Metadata } from "next";
import { Newsreader, Onest } from "next/font/google";

import { TooltipProvider } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import "./globals.css";

// next/font downloads these at build time and serves them locally:
// the running app makes no requests to Google.
const onest = Onest({ subsets: ["latin"], variable: "--font-sans" });
const newsreader = Newsreader({
  subsets: ["latin"],
  variable: "--font-serif",
  axes: ["opsz"],
  style: ["normal", "italic"],
});

export const metadata: Metadata = {
  title: "Cortex",
  description: "Find your photos by describing them. Everything stays on this computer.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={cn("dark h-full antialiased", onest.variable, newsreader.variable)}>
      <body className="flex min-h-full flex-col">
        <TooltipProvider delayDuration={400}>{children}</TooltipProvider>
      </body>
    </html>
  );
}
