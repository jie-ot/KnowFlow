import type { Metadata } from "next";
import { Shell } from "@/components/Shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "KnowFlow",
  description: "多学科笔记、资料整理与智能复习",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
