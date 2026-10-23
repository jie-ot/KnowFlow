"use client";

import { Check, Pencil, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { api, ApiError, Card, Course, fetcher } from "@/lib/api";
import { colorOf, intervalLabel } from "@/lib/format";
import { refreshReviewQueue, ReviewSubmission } from "@/lib/submission";
import { Markdown } from "@/components/Markdown";
import { WeakStudySheet } from "@/components/WeakStudySheet";
import { CardEditor } from "@/components/CardEditor";
import { Dot, Empty, PageHeader, useConfirm, useToast } from "@/components/ui";

export default function ReviewPage() {
  return (
    <Suspense>
      <Review />
    </Suspense>
  );
}

function Review() {
  const params = useSearchParams();
  const router = useRouter();
  const [sessionId, setSessionId] = useState(0);
  const tab = params.get("tab") === "cards" ? "cards" : "review";
  const setTab = (t: string) => {
    if (t === "review") setSessionId((id) => id + 1);
    const p = new URLSearchParams(params.toString());
    p.set("tab", t);
    router.replace(`/review?${p.toString()}`);
  };
  return (
    <div className="desktop-page review-page !max-w-[1040px]">
      <PageHeader title={tab === "review" ? "复习" : "卡片库"}
        actions={<div className="seg">
          <button data-on={tab === "review"} onClick={() => setTab("review")}>今日复习</button>
          <button data-on={tab === "cards"} onClick={() => setTab("cards")}>卡片库</button>
        </div>} />
      {tab === "review" ? <Session key={`${params.get("course") || "all"}:${sessionId}`} courseId={params.get("course")} /> : <Library noteId={params.get("note")} weakQuery={params.get("weak") === "1"} learnId={params.get("learn")} />}
    </div>
  );
}

type Queue = { today: string; items: Card[]; reviewed_today: number; next_due: string | null };

function Session({ courseId }: { courseId: string | null }) {
  const key = `/api/review/queue${courseId ? `?course_id=${courseId}` : ""}`;
  const [queue, setQueue] = useState<Card[] | null>(null);
  const [data, setData] = useState<Queue | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [done, setDone] = useState(0);
  const [editing, setEditing] = useState<Card | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [conflict, setConflict] = useState(false);
  const submission = useRef(new ReviewSubmission(() => typeof window === "undefined" ? null : localStorage));
  const toast = useToast();

  useEffect(() => {
    let active = true;
    setQueue(null);
    setError(false);
    // 复习是一轮独立会话，进入时读取数据库中的最新队列。
    api<Queue>(key).then((fresh) => {
      if (!active) return;
      setData(fresh); setQueue(fresh.items); setIndex(0); setDone(0); setFlipped(false);
    }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, [key, attempt]);

  const card = queue?.[index];
  const grade = useCallback(async (g: "forgot" | "hard" | "good") => {
    if (!card || !flipped || submission.current.busy || conflict) return;
    setSubmitting(true);
    try {
      const advanced = await submission.current.submit(card, g, (request) =>
        api(`/api/review/${card.id}`, { method: "POST", json: request }));
      if (!advanced) return;
      setDone((d) => d + 1);
      setFlipped(false);
      setIndex((i) => i + 1);
      globalMutate("/api/courses");
      globalMutate("/api/stats?days=365");
    } catch (e) {
      if ((e as ApiError).code === "REVIEW_CONFLICT") setConflict(true);
      toast((e as ApiError).message, "bad");
    } finally { setSubmitting(false); }
  }, [card, flipped, toast, conflict]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || submitting || e.repeat || e.isComposing || editing || document.querySelector('[role="dialog"][aria-modal="true"]') || (e.target as HTMLElement).tagName.match(/INPUT|TEXTAREA|SELECT/)) return;
      if (e.key === " ") { e.preventDefault(); setFlipped((f) => !f); }
      if (flipped && e.key === "1") grade("forgot");
      if (flipped && e.key === "2") grade("hard");
      if (flipped && e.key === "3") grade("good");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [flipped, grade, editing, submitting]);

  if (error && queue === null) return <Empty title="复习卡片加载失败"><button className="btn btn-outline mt-3" onClick={() => setAttempt((n) => n + 1)}>重试</button></Empty>;
  if (!data || queue === null) return <p className="py-12 text-center text-muted text-[14px]" role="status">正在加载复习卡片…</p>;
  const total = queue.length;

  if (!card) {
    return (
      <div className="flex flex-col items-center py-12">
        <div className="card-stack w-full max-w-[460px]">
          <div className="index-card px-8 pt-12 pb-9 text-center" style={{ "--course": "var(--accent)" } as React.CSSProperties}>
            <div className="w-11 h-11 mx-auto rounded-full grid place-items-center bg-accent-soft text-accent mb-4"><Check size={22} /></div>
            <p className="text-[24px] font-semibold tracking-tight">{done ? `这一轮复习了 ${done} 张` : "今天没有要复习的卡片"}</p>
            <p className="text-[14px] text-muted mt-2">
              {data.next_due ? `下一批卡片在 ${data.next_due} 到期。` : "从笔记里提炼知识点，就会有新卡片。"}
            </p>
            <div className="flex justify-center gap-2 mt-6">
              <Link href="/" className="btn btn-outline">回到今天</Link>
              {done > 0 && <button className="btn" disabled={submitting} onClick={async () => {
                setSubmitting(true);
                try { await refreshReviewQueue(() => api<Queue>(key), (fresh) => { setData(fresh); setQueue(fresh.items); setIndex(0); setDone(0); setFlipped(false); }); }
                catch (error) { toast((error as Error).message, "bad"); }
                finally { setSubmitting(false); }
              }}>{submitting ? "更新队列中…" : "刷新待复习卡片"}</button>}
            </div>
          </div>
        </div>
      </div>
    );
  }

  const color = colorOf(card.course_color);
  const pendingGrade = submission.current.pendingFor(card)?.grade;
  return (
    <div className="flex flex-col items-center">
      <div className="w-full max-w-[560px] mb-6">
        <div className="flex justify-between text-[12.5px] text-muted mb-2">
          <span className="flex items-center gap-2"><Dot color={card.course_color} />{card.course_name}</span>
          <span className="tabular-nums">第 {index + 1} 张，共 {total} 张</span>
        </div>
        <div className="h-[3px] rounded-full bg-rule overflow-hidden">
          <div className="h-full bg-accent transition-all duration-300" style={{ width: `${(index / total) * 100}%` }} />
        </div>
      </div>

      <div className="flip w-full max-w-[560px]">
        <div className="flip-inner grid" data-flipped={flipped}>
          <button onClick={() => setFlipped(true)} aria-label="翻面" aria-hidden={flipped} tabIndex={flipped ? -1 : 0}
            className="flip-face index-card [grid-area:1/1] min-h-[320px] px-10 pt-14 pb-10 flex flex-col items-center justify-center text-center"
            style={{ "--course": color } as React.CSSProperties}>
            <div className="text-[24px] font-medium leading-snug"><Markdown text={card.front} className="!text-[24px] !leading-snug" /></div>
            <span className="mt-8 text-[12.5px] text-faint">点击卡片或按空格翻面</span>
          </button>
          <div aria-hidden={!flipped} inert={!flipped} className="flip-face flip-back index-card [grid-area:1/1] min-h-[320px] px-10 pt-12 pb-8 flex flex-col"
            style={{ "--course": color } as React.CSSProperties}>
            <p className="text-[15px] text-muted mb-4 text-center">{card.front.replace(/\$+/g, "")}</p>
            <div className="flex-1 overflow-y-auto"><Markdown text={card.back} /></div>
            <div className="flex items-center justify-between mt-6 text-[12px] text-faint">
              {card.source_note_id ? <Link href={`/notes?id=${card.source_note_id}`} className="hover:text-accent">来自《{card.note_title}》</Link> : <span>手动添加</span>}
              <button className="flex items-center gap-1 hover:text-ink" onClick={() => setEditing(card)}><Pencil size={12} />改错字</button>
            </div>
          </div>
        </div>
      </div>

      <div className="review-actions grid grid-cols-3 gap-3 w-full max-w-[560px] mt-7">
        {([["forgot", "忘记", "1", "var(--bad)"], ["hard", "模糊", "2", "var(--warn)"], ["good", "记得", "3", "var(--accent)"]] as const).map(([g, label, k, c]) => (
          <button key={g} onClick={() => grade(g)} className="h-16 rounded-xl border border-rule hover:border-current transition-colors flex flex-col items-center justify-center"
            disabled={!flipped || submitting || conflict || (!!pendingGrade && pendingGrade !== g)} style={{ color: c }}>
            <span className="text-[15px] font-semibold">{pendingGrade === g ? `重试：${label}` : label}</span>
            <span className="text-[11.5px] text-muted mt-0.5">{card.preview && intervalLabel(card.preview[g])}后再见 <kbd className="ml-1 px-1 rounded border border-rule text-[10px] text-faint font-sans">{k}</kbd></span>
          </button>
        ))}
      </div>
      <p role="status" className="mt-3 text-[12px] text-muted">
        {submitting ? "正在提交评分…" : conflict ? "这张卡片已在其他窗口评分。" : pendingGrade ? "上次评分尚未确认，重试会提交原评分。" : ""}
        {conflict && <button className="ml-2 text-accent" onClick={async () => {
          try {
            const next = await api<Queue>(key);
            submission.current.clear(card); setData(next); setQueue(next.items); setIndex(0); setFlipped(false); setConflict(false);
          } catch (error) { toast((error as Error).message, "bad"); }
        }}>更新复习队列</button>}
      </p>
      <CardEditor card={editing} onClose={() => setEditing(null)} onSaved={(c) => setQueue((q) => q!.map((x) => (x.id === c.id ? { ...x, ...c } : x)))} />
    </div>
  );
}

function Library({ noteId, weakQuery, learnId }: { noteId: string | null; weakQuery: boolean; learnId: string | null }) {
  const [course, setCourse] = useState("");
  const [weak, setWeak] = useState(weakQuery);
  const [learning, setLearning] = useState<string | null>(learnId);
  useEffect(() => setWeak(weakQuery), [weakQuery]);
  useEffect(() => setLearning(learnId), [learnId]);
  const qs = new URLSearchParams();
  if (course) qs.set("course_id", course);
  if (weak) qs.set("weak", "true");
  if (noteId) qs.set("note_id", noteId);
  const { data: cards, mutate } = useSWR<Card[]>(`/api/cards?${qs.toString()}`, fetcher);
  const { data: courses } = useSWR<Course[]>("/api/courses", fetcher);
  const courseMap = useMemo(() => Object.fromEntries((courses || []).map((c) => [c.id, c])), [courses]);
  const [editing, setEditing] = useState<Card | null>(null);
  const [creating, setCreating] = useState(false);
  const confirm = useConfirm();
  const router = useRouter();

  const remove = async (c: Card) => {
    if (!(await confirm({ title: "删除这张卡片？", body: c.front, confirm: "删除", danger: true }))) return;
    await api(`/api/cards/${c.id}`, { method: "DELETE" });
    mutate();
    globalMutate("/api/courses");
  };

  return (
    <div>
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <select className="input h-8 w-auto text-[13px]" value={course} onChange={(e) => setCourse(e.target.value)} aria-label="学科">
          <option value="">全部学科</option>
          {courses?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <button className="btn" onClick={() => setWeak((w) => !w)} style={weak ? { background: "var(--bad-soft)", color: "var(--bad)" } : undefined}>
          薄弱知识点（忘记 ≥ 3 次）
        </button>
        {noteId && <button className="chip" onClick={() => router.replace("/review?tab=cards")}>只看一篇笔记的卡片 ✕</button>}
        <span className="flex-1" />
        <button className="btn btn-primary" onClick={() => setCreating(true)}><Plus size={14} />手动建卡</button>
      </div>
      <div className="border-y border-rule divide-y divide-rule">
        {cards?.map((c) => (
          <div key={c.id} className="group flex items-start gap-4 py-3.5">
            <Dot color={courseMap[c.course_id]?.color} />
            <div className="flex-1 min-w-0">
              <p className="text-[14px] font-medium">{c.front}</p>
              <p className="text-[12.5px] text-muted line-clamp-1 mt-0.5">{c.back}</p>
              {c.source_note_id && <Link href={`/notes?id=${c.source_note_id}`} className="inline-flex mt-1.5 text-[12px] text-accent">查看来源笔记</Link>}
            </div>
            <div className="text-right text-[12px] text-muted shrink-0 tabular-nums">
              <p>{c.due}</p>
              <p className="text-faint">间隔 {c.interval} 天，EF {c.ef.toFixed(2)}{c.forgot_count ? `，忘记 ${c.forgot_count} 次` : ""}</p>
            </div>
            <div className="flex items-center">
              {c.forgot_count >= 3 && <button className="btn text-accent" onClick={() => setLearning(c.id)}>补强学习</button>}
              <button className="btn btn-icon text-muted" onClick={() => setEditing(c)} aria-label="编辑"><Pencil size={14} /></button>
              <button className="btn btn-icon text-muted" onClick={() => remove(c)} aria-label="删除"><Trash2 size={14} /></button>
            </div>
          </div>
        ))}
      </div>
      {cards?.length === 0 && <Empty title={weak ? "没有薄弱知识点" : "还没有卡片"}>在笔记里点“提炼知识点”，或手动建一张。</Empty>}
      <CardEditor card={editing} onClose={() => setEditing(null)} onSaved={() => mutate()} />
      <CardEditor card={creating ? ({} as Card) : null} courses={courses} onClose={() => setCreating(false)}
        onSaved={() => { mutate(); globalMutate("/api/courses"); }} />
      {learning && <WeakStudySheet key={learning} cardId={learning} onSaved={() => { mutate(); globalMutate("/api/courses"); }}
        onClose={() => {
          setLearning(null);
          if (learnId) {
            const query = new URLSearchParams(window.location.search);
            query.delete("learn");
            router.replace(`/review?${query.toString()}`);
          }
        }} />}
    </div>
  );
}
