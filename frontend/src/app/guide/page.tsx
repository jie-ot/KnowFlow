"use client";

import { BookOpenCheck, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { Markdown } from "@/components/Markdown";

/** 使用说明：内容和 AI 助手用的是同一份手册，改一处两边都更新 */
export default function GuidePage() {
  const { data } = useSWR<{ markdown: string }>("/api/manual", fetcher);
  const [active, setActive] = useState("");
  const { intro, sections } = useMemo(() => {
    const md = (data?.markdown || "").replace(/^# .*\n/, "");
    const parts = md.split(/\n(?=## )/);
    const intro = parts[0].startsWith("## ") ? "" : parts.shift() || "";
    return {
      intro: intro.replace(/^链接写法.*$/m, "").trim(),
      sections: parts.map((p, i) => {
        const [head, ...rest] = p.split("\n");
        return { id: `s${i}`, title: head.replace(/^## /, "").trim(), body: rest.join("\n").trim() };
      }),
    };
  }, [data]);

  useEffect(() => {
    if (!sections.length) return;
    const io = new IntersectionObserver((entries) => {
      const hit = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
      if (hit) setActive(hit.target.id);
    }, { rootMargin: "0px 0px -70% 0px" });
    sections.forEach((s) => { const el = document.getElementById(s.id); if (el) io.observe(el); });
    return () => io.disconnect();
  }, [sections]);

  return (
    <div className="max-w-[1080px] mx-auto px-10 py-12 flex gap-12">
      <nav className="w-48 shrink-0 sticky top-12 self-start max-[900px]:hidden">
        <p className="text-[12px] font-medium text-muted mb-2 px-2">目录</p>
        <ul className="space-y-0.5">
          {sections.map((s) => (
            <li key={s.id}>
              <a href={`#${s.id}`} className={`block px-2 py-1.5 rounded-lg text-[13px] ${active === s.id ? "bg-hover text-ink font-medium" : "text-muted hover:text-ink"}`}>{s.title}</a>
            </li>
          ))}
        </ul>
      </nav>
      <article className="flex-1 min-w-0">
        <div className="flex items-center gap-3 mb-2">
          <span className="w-9 h-9 rounded-xl grid place-items-center bg-accent-soft text-accent"><BookOpenCheck size={18} /></span>
          <h1 className="text-[26px] font-semibold tracking-tight">使用说明</h1>
        </div>
        {intro && <Markdown text={intro.replaceAll("用户", "你")} className="text-muted" />}
        <button onClick={() => window.dispatchEvent(new Event("kf-assistant"))}
          className="mt-4 mb-10 flex items-center gap-2 px-4 py-2.5 rounded-xl border border-rule text-[13.5px] hover:border-accent hover:text-accent transition-colors">
          <Sparkles size={15} className="text-accent" />找不到答案？按 Ctrl+J 直接问 AI 助手，它读过这份说明，也能查你的数据
        </button>
        {sections.map((s) => (
          <section key={s.id} id={s.id} className="scroll-mt-8 mb-10">
            <h2 className="text-[22px] font-semibold mb-3 pb-2 border-b border-rule">{s.title}</h2>
            <Markdown text={s.body.replaceAll("用户", "你")} className="text-[14.5px]" />
          </section>
        ))}
      </article>
    </div>
  );
}
