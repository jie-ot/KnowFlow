import type { Metadata } from "next";
import { Shell } from "@/components/Shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "KnowFlow",
  description: "多学科笔记、资料整理与智能复习",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        {/* 在页面绘制前应用主题，避免闪一下浅色 */}
        <script dangerouslySetInnerHTML={{ __html: `try{var t=localStorage.getItem("kf-theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}` }} />
      </head>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
