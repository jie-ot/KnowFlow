"use client";

import { ArrowUp, BookOpen, Layers, Search, Sparkles, Square, SquarePen, Target, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { ApiError, fetcher, NoteSummary, postSse } from "@/lib/api";
import { useAiConsent } from "./ai";
import { Markdown } from "./Markdown";
import { Spinner } from "./ui";

type Step = { label: string; detail: string };
type Turn = { role: "user" | "assistant"; content: string; steps?: Step[]; error?: string; pending?: boolean };

const STEP_ICON: Record<string, typeof Search> = { 搜索: Search, 阅读笔记: BookOpen, 查看计划任务: Target, 查看复习卡片: Layers };

/** 每个页面给几个起手问题 */
function suggestions(path: string): string[] {
  if (path.startsWith("/notes")) return ["双链怎么用？", "帮我解释这篇笔记的重点", "怎么让 AI 帮我出复习卡片？"];
  if (path.startsWith("/materials")) return ["怎么把 PDF 整理成笔记？", "上传的图片能识别文字吗？", "出处链接怎么回到原文？"];
  if (path.startsWith("/review")) return ["今天该复习哪些？", "我最容易忘的知识点有哪些？", "1、2、3 三个按钮有什么区别？"];
  if (path.startsWith("/plans")) return ["我的计划进度怎么样？", "月历怎么改任务日期？", "AI 拆任务需要填什么？"];
  if (path.startsWith("/graph")) return ["图谱怎么看？", "为什么有些笔记没有连线？"];
  if (path.startsWith("/settings")) return ["怎么接入 DeepSeek？", "删掉配置后 API Key 还能用吗？"];
  return ["KnowFlow 能做什么？", "今天我该做什么？", "有哪些快捷键？"];
}

export function AssistantPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const path = usePathname();
  const params = useSearchParams();
  const page = `${path}${params.toString() ? `?${params}` : ""}`;
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const ctrl = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const askConsent = useAiConsent();
  const { data: status } = useSWR<{ configured: boolean; config: { model: string } | null }>(open ? "/api/ai/status" : null, fetcher);
  const { data: notes } = useSWR<NoteSummary[]>(open ? "/api/notes" : null, fetcher);
  const known = useMemo(() => new Set((notes || []).map((n) => n.title.trim().toLowerCase())), [notes]);

  useEffect(() => {
    if (open) setTimeout(() => box.current?.focus(), 60);
  }, [open]);
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [turns]);

  const patchLast = (fn: (t: Turn) => Turn) => setTurns((ts) => [...ts.slice(0, -1), fn(ts[ts.length - 1])]);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    setInput("");
    const history = [...turns.filter((t) => !t.error && t.content), { role: "user" as const, content: q }]
      .map(({ role, content }) => ({ role, content }));
    setTurns((ts) => [...ts, { role: "user", content: q }, { role: "assistant", content: "", steps: [], pending: true }]);
    setBusy(true);
    const c = new AbortController();
    ctrl.current = c;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await postSse("/api/assistant/chat", { messages: history, page }, (ev, data) => {
          if (ev === "delta") patchLast((t) => ({ ...t, content: t.content + String(data.text) }));
          else if (ev === "tool") patchLast((t) => ({ ...t, steps: [...(t.steps || []), { label: String(data.label), detail: String(data.detail || "") }] }));
          else if (ev === "error") patchLast((t) => ({ ...t, error: String((data.error as { message: string }).message) }));
        }, c.signal);
        break;
      } catch (e) {
        const err = e as ApiError;
        if (err.code === "CONSENT_REQUIRED" && attempt === 0) {
          const ok = await askConsent({
            config_id: String(err.data.config_id), provider_host: String(err.data.provider_host), model: String(err.data.model),
            purpose: "回答你的问题：会发送 KnowFlow 使用手册、你的学科和计划概况，以及助手为回答而查看的笔记、卡片内容",
          });
          if (ok) continue;
          patchLast((t) => ({ ...t, error: "没有发送。" }));
        } else {
          patchLast((t) => ({ ...t, error: err.code === "NO_CONFIG" ? "还没有连接大模型。" : err.message }));
        }
        break;
      }
    }
    patchLast((t) => ({ ...t, pending: false }));
    setBusy(false);
  };

  const stop = () => {
    ctrl.current?.abort();
    patchLast((t) => ({ ...t, pending: false }));
    setBusy(false);
  };

  if (!open) return null;
  const empty = turns.length === 0;

  return (
    <aside className="anim-sheet fixed right-0 top-0 z-40 h-dvh w-[420px] max-w-[100vw] flex flex-col bg-paper border-l border-rule shadow-2xl"
      onKeyDown={(e) => e.key === "Escape" && onClose()} aria-label="AI 助手">
      <header className="h-14 px-4 flex items-center gap-2 border-b border-rule shrink-0">
        <span className="w-7 h-7 rounded-lg grid place-items-center bg-accent-soft text-accent"><Sparkles size={15} /></span>
        <h2 className="text-[15px] font-semibold flex-1">AI 助手</h2>
        {!empty && (
          <button className="btn btn-icon text-muted" onClick={() => { stop(); setTurns([]); }} title="新对话" aria-label="新对话"><SquarePen size={16} /></button>
        )}
        <button className="btn btn-icon text-muted" onClick={onClose} title="关闭（Esc）" aria-label="关闭"><X size={17} /></button>
      </header>

      <div ref={scroller} className="flex-1 overflow-y-auto px-5 py-5">
        {status && !status.configured ? (
          <div className="rounded-xl border border-rule p-4 text-[14px]">
            <p className="font-medium">还没有连接大模型</p>
            <p className="text-muted mt-1 text-[13px] leading-relaxed">在设置里填好 DeepSeek 等服务商的 Base URL、API Key 和模型名称后，就可以在任何页面问我问题。</p>
            <Link href="/settings" onClick={onClose} className="btn btn-primary mt-3">去设置</Link>
          </div>
        ) : empty ? (
          <div className="pt-6">
            <p className="font-serif text-[22px] leading-snug">有什么想问的？</p>
            <p className="text-[13px] text-muted mt-2 leading-relaxed">
              可以问 KnowFlow 每个功能怎么用，也可以问你自己的笔记、卡片和计划，我会去查了再回答。我只看不改。
            </p>
            <div className="flex flex-col items-start gap-2 mt-6">
              {suggestions(path).map((s) => (
                <button key={s} onClick={() => send(s)}
                  className="px-3 py-1.5 rounded-xl border border-rule text-[13.5px] text-left hover:border-accent hover:text-accent transition-colors">
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-6">
            {turns.map((t, i) => t.role === "user" ? (
              <div key={i} className="flex justify-end">
                <p className="max-w-[85%] px-3.5 py-2 rounded-2xl rounded-br-md bg-hover text-[14px] leading-relaxed whitespace-pre-wrap">{t.content}</p>
              </div>
            ) : (
              <div key={i}>
                {t.steps && t.steps.length > 0 && (
                  <ul className="mb-2 space-y-1">
                    {t.steps.map((s, j) => {
                      const Icon = STEP_ICON[s.label] || Search;
                      return (
                        <li key={j} className="flex items-center gap-1.5 text-[12px] text-muted">
                          <Icon size={12} className="text-faint" />{s.label}{s.detail && <span className="text-faint truncate">· {s.detail}</span>}
                        </li>
                      );
                    })}
                  </ul>
                )}
                {t.content && <Markdown text={t.content} known={known} noCreate className={`text-[14px] ${t.pending ? "typing" : ""}`} />}
                {t.pending && !t.content && <p className="flex items-center gap-2 text-[13px] text-muted"><Spinner />{t.steps?.length ? "整理答案…" : "想一想…"}</p>}
                {t.error && (
                  <p className="mt-2 px-3 py-2 rounded-lg bg-bad-soft text-[13px] text-bad">
                    {t.error}
                    {t.error.includes("连接大模型") && <Link href="/settings" onClick={onClose} className="ml-1 underline">去设置</Link>}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="p-3 border-t border-rule shrink-0">
        <div className="flex items-end gap-2 rounded-2xl border border-rule px-3 py-2 focus-within:border-accent focus-within:shadow-[0_0_0_3px_var(--accent-soft)] transition-shadow">
          <textarea ref={box} rows={1} value={input} onChange={(e) => setInput(e.target.value)} maxLength={4000}
            placeholder="问点什么，回车发送，Shift+回车换行"
            className="flex-1 resize-none bg-transparent outline-none text-[14px] leading-6 max-h-40 placeholder:text-faint"
            style={{ fieldSizing: "content" } as React.CSSProperties}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(input); }
            }} />
          {busy ? (
            <button className="w-8 h-8 rounded-full grid place-items-center bg-ink text-paper shrink-0" onClick={stop} aria-label="停止"><Square size={11} fill="currentColor" /></button>
          ) : (
            <button className="w-8 h-8 rounded-full grid place-items-center bg-accent text-[var(--accent-ink)] shrink-0 disabled:opacity-40"
              disabled={!input.trim()} onClick={() => send(input)} aria-label="发送"><ArrowUp size={16} /></button>
          )}
        </div>
        <p className="mt-1.5 px-1 text-[11px] text-faint">
          {status?.config ? `由 ${status.config.model} 回答，` : ""}会查看相关笔记并发送给模型；Ctrl/⌘ + J 随时打开
        </p>
      </div>
    </aside>
  );
}
