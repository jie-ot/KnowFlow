"use client";

import { Check, ChevronDown, ChevronUp, RotateCcw, Sparkles } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { api, ApiError, Card, fetcher, postSse } from "@/lib/api";
import { colorOf, intervalLabel } from "@/lib/format";
import { useAiConsent } from "./ai";
import { Markdown } from "./Markdown";
import { Dot, Spinner, useToast } from "./ui";

/* ---------------------------------------------------------------- 首页快速复习 */

const GRADES = [["forgot", "忘记", "1", "var(--bad)"], ["hard", "模糊", "2", "var(--warn)"], ["good", "记得", "3", "var(--accent)"]] as const;

/** 在首页卡片里直接翻卡复习，和复习页用同一套 SM-2 记录 */
export function QuickReview({ onExit }: { onExit: () => void }) {
  const { data } = useSWR<{ items: Card[] }>("/api/review/queue", fetcher, { revalidateOnFocus: false });
  const [queue, setQueue] = useState<Card[] | null>(null);
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const toast = useToast();

  useEffect(() => { if (data && queue === null) setQueue(data.items); }, [data, queue]);
  const card = queue?.[index];

  const grade = useCallback(async (g: "forgot" | "hard" | "good") => {
    if (!card || !flipped) return;
    try {
      await api(`/api/review/${card.id}`, { method: "POST", json: { grade: g } });
      setFlipped(false);
      setIndex((i) => i + 1);
      globalMutate("/api/courses");
      globalMutate("/api/stats?days=365");
    } catch (e) {
      toast((e as ApiError).message, "bad");
    }
  }, [card, flipped, toast]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName.match(/INPUT|TEXTAREA|SELECT/)) return;
      if (e.key === " ") { e.preventDefault(); setFlipped((f) => !f); }
      if (e.key === "Escape") onExit();
      if (flipped && e.key === "1") grade("forgot");
      if (flipped && e.key === "2") grade("hard");
      if (flipped && e.key === "3") grade("good");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [flipped, grade, onExit]);

  if (!queue) return <div className="index-card min-h-[300px] grid place-items-center"><Spinner /></div>;
  if (!card) {
    return (
      <div className="index-card px-7 pt-10 pb-7 text-center" style={{ "--course": "var(--accent)" } as React.CSSProperties}>
        <div className="w-10 h-10 mx-auto rounded-full grid place-items-center bg-accent-soft text-accent mb-3"><Check size={20} /></div>
        <p className="font-serif text-[22px]">{index ? `复习完了，一共 ${index} 张` : "今天没有要复习的卡片"}</p>
        <button className="btn btn-outline mt-5" onClick={onExit}>好的</button>
      </div>
    );
  }
  return (
    <div>
      <div className="flip">
        <div className="flip-inner grid" data-flipped={flipped}>
          <button onClick={() => setFlipped(true)} aria-label="翻面"
            className="flip-face index-card [grid-area:1/1] min-h-[260px] px-7 pt-10 pb-6 flex flex-col text-left"
            style={{ "--course": colorOf(card.course_color) } as React.CSSProperties}>
            <span className="flex items-center justify-between w-full text-[12px] text-muted">
              <span className="flex items-center gap-1.5"><Dot color={card.course_color} size={7} />{card.course_name}</span>
              <span className="tabular-nums">{index + 1} / {queue.length}</span>
            </span>
            <span className="flex-1 grid place-items-center w-full py-4">
              <Markdown text={card.front} className="font-serif !text-[20px] !leading-snug text-center" />
            </span>
            <span className="text-[12px] text-faint self-center">点击或按空格翻面</span>
          </button>
          <div className="flip-face flip-back index-card [grid-area:1/1] min-h-[260px] px-7 pt-10 pb-5 flex flex-col"
            style={{ "--course": colorOf(card.course_color) } as React.CSSProperties}>
            <p className="text-[12.5px] text-muted mb-2 line-clamp-1">{card.front.replace(/\$+/g, "")}</p>
            <div className="flex-1 overflow-y-auto max-h-[180px]"><Markdown text={card.back} className="text-[14px]" /></div>
          </div>
        </div>
      </div>
      <div className={`grid grid-cols-3 gap-2 mt-4 transition-opacity ${flipped ? "" : "opacity-30 pointer-events-none"}`}>
        {GRADES.map(([g, label, k, c]) => (
          <button key={g} onClick={() => grade(g)} className="h-12 rounded-xl border border-rule hover:border-current flex flex-col items-center justify-center" style={{ color: c }}>
            <span className="text-[13.5px] font-semibold">{label} <kbd className="text-[10px] text-faint font-sans">{k}</kbd></span>
            <span className="text-[11px] text-muted">{card.preview && intervalLabel(card.preview[g])}后</span>
          </button>
        ))}
      </div>
      <div className="flex justify-between mt-3 text-[12px]">
        <button className="text-muted hover:text-ink" onClick={onExit}>收起（Esc）</button>
        <Link href="/review" className="text-muted hover:text-accent">去复习页</Link>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- AI 每周小结 */

type Summary = { id: string; period_start: string; period_end: string; content: string; created_at: string };

export function WeeklySummary() {
  const { data: list, mutate } = useSWR<Summary[]>("/api/summaries", fetcher);
  const [streaming, setStreaming] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const askConsent = useAiConsent();
  const toast = useToast();
  const latest = list?.[0];

  const generate = async () => {
    setStreaming("");
    setOpen(true);
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await postSse("/api/summaries/weekly", {}, (ev, data) => {
          if (ev === "delta") setStreaming((s) => (s ?? "") + String(data.text));
          if (ev === "error") toast(String((data.error as { message: string }).message), "bad");
        });
        break;
      } catch (e) {
        const err = e as ApiError;
        if (err.code === "CONSENT_REQUIRED" && attempt === 0) {
          const ok = await askConsent({
            config_id: String(err.data.config_id), provider_host: String(err.data.provider_host), model: String(err.data.model),
            purpose: "生成学习周小结：会发送最近 7 天的复习数量、常忘卡片的正面、完成和逾期的任务标题、专注时长等统计",
          });
          if (ok) continue;
        } else toast(err.code === "NO_CONFIG" ? "还没有连接大模型，先到设置里填写" : err.message, "bad");
        break;
      }
    }
    await mutate();
    setStreaming(null);
  };

  const text = streaming ?? latest?.content;
  const fresh = latest && Date.now() - new Date(latest.created_at).getTime() < 6 * 86400e3;
  return (
    <section>
      <div className="flex items-baseline justify-between">
        <h2 className="section-title">本周小结</h2>
        <button className="text-[12px] text-muted hover:text-accent flex items-center gap-1" onClick={generate} disabled={streaming !== null}>
          {streaming !== null ? <Spinner size={11} /> : latest ? <RotateCcw size={12} /> : <Sparkles size={12} />}
          {streaming !== null ? "生成中" : latest ? "重新生成" : "让 AI 写一份"}
        </button>
      </div>
      {text ? (
        <div className="rounded-xl border border-rule px-4 py-3">
          {latest && streaming === null && (
            <p className="text-[11.5px] text-faint mb-1">{latest.period_start} 至 {latest.period_end}{!fresh && " · 已经是上周的了，可以重新生成"}</p>
          )}
          <div className={`relative ${open ? "" : "max-h-[150px] overflow-hidden"}`}>
            <Markdown text={text} className={`text-[13.5px] ${streaming !== null ? "typing" : ""}`} />
            {!open && <div className="absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-[var(--paper)] to-transparent" />}
          </div>
          <button className="mt-1 text-[12px] text-muted hover:text-ink flex items-center gap-0.5" onClick={() => setOpen((v) => !v)}>
            {open ? <><ChevronUp size={13} />收起</> : <><ChevronDown size={13} />展开全文</>}
          </button>
        </div>
      ) : (
        <p className="text-[13.5px] text-muted py-3 border-y border-rule">
          AI 会根据这 7 天的复习记录、计划完成情况和专注时长，总结做得好的、要注意的，并给下周建议。
        </p>
      )}
    </section>
  );
}
