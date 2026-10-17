"use client";

import { Check, Pencil, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { api, ApiError, Card, Course, fetcher } from "@/lib/api";
import { colorOf, intervalLabel } from "@/lib/format";
import { Markdown } from "@/components/Markdown";
import { Dot, Empty, Modal, PageHeader, useConfirm, useToast } from "@/components/ui";

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
  const tab = params.get("tab") === "cards" ? "cards" : "review";
  const setTab = (t: string) => {
    const p = new URLSearchParams(params.toString());
    p.set("tab", t);
    router.replace(`/review?${p.toString()}`);
  };
  return (
    <div className="max-w-[880px] mx-auto px-10 py-12">
      <PageHeader title={tab === "review" ? "复习" : "卡片库"}
        sub={tab === "review" ? "按 SM-2 间隔安排：记得越牢，下次出现得越晚。" : "所有知识点卡片，包括还没到期的。"}
        actions={<div className="seg">
          <button data-on={tab === "review"} onClick={() => setTab("review")}>今日复习</button>
          <button data-on={tab === "cards"} onClick={() => setTab("cards")}>卡片库</button>
        </div>} />
      {tab === "review" ? <Session courseId={params.get("course")} /> : <Library noteId={params.get("note")} />}
    </div>
  );
}

type Queue = { today: string; items: Card[]; reviewed_today: number; next_due: string | null };

function Session({ courseId }: { courseId: string | null }) {
  const key = `/api/review/queue${courseId ? `?course_id=${courseId}` : ""}`;
  const { data, mutate } = useSWR<Queue>(key, fetcher, { revalidateOnFocus: false });
  const [queue, setQueue] = useState<Card[] | null>(null);
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [done, setDone] = useState(0);
  const [editing, setEditing] = useState<Card | null>(null);
  const toast = useToast();

  useEffect(() => {
    if (data && queue === null) setQueue(data.items);
  }, [data, queue]);

  const card = queue?.[index];
  const grade = useCallback(async (g: "forgot" | "hard" | "good") => {
    if (!card || !flipped) return;
    try {
      await api(`/api/review/${card.id}`, { method: "POST", json: { grade: g } });
      setDone((d) => d + 1);
      setFlipped(false);
      setIndex((i) => i + 1);
      globalMutate("/api/courses");
      globalMutate("/api/stats?days=30");
    } catch (e) {
      toast((e as ApiError).message, "bad");
    }
  }, [card, flipped, toast]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (editing || (e.target as HTMLElement).tagName.match(/INPUT|TEXTAREA|SELECT/)) return;
      if (e.key === " ") { e.preventDefault(); setFlipped((f) => !f); }
      if (flipped && e.key === "1") grade("forgot");
      if (flipped && e.key === "2") grade("hard");
      if (flipped && e.key === "3") grade("good");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [flipped, grade, editing]);

  if (!data || queue === null) return null;
  const total = queue.length;

  if (!card) {
    return (
      <div className="flex flex-col items-center py-12">
        <div className="card-stack w-full max-w-[460px]">
          <div className="index-card px-8 pt-12 pb-9 text-center" style={{ "--course": "var(--accent)" } as React.CSSProperties}>
            <div className="w-11 h-11 mx-auto rounded-full grid place-items-center bg-accent-soft text-accent mb-4"><Check size={22} /></div>
            <p className="font-serif text-[24px]">{done ? `这一轮复习了 ${done} 张` : "今天没有要复习的卡片"}</p>
            <p className="text-[14px] text-muted mt-2">
              {data.next_due ? `下一批卡片在 ${data.next_due} 到期。` : "从笔记里提炼知识点，就会有新卡片。"}
            </p>
            <div className="flex justify-center gap-2 mt-6">
              <Link href="/" className="btn btn-outline">回到今天</Link>
              {done > 0 && <button className="btn" onClick={() => { setQueue(null); setIndex(0); setDone(0); mutate(); }}>再看一遍还有没有</button>}
            </div>
          </div>
        </div>
      </div>
    );
  }

  const color = colorOf(card.course_color);
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
          <button onClick={() => setFlipped(true)} aria-label="翻面"
            className="flip-face index-card [grid-area:1/1] min-h-[320px] px-10 pt-14 pb-10 flex flex-col items-center justify-center text-center"
            style={{ "--course": color } as React.CSSProperties}>
            <div className="font-serif text-[24px] leading-snug"><Markdown text={card.front} className="!text-[24px] !leading-snug" /></div>
            <span className="mt-8 text-[12.5px] text-faint">点击卡片或按空格翻面</span>
          </button>
          <div className="flip-face flip-back index-card [grid-area:1/1] min-h-[320px] px-10 pt-12 pb-8 flex flex-col"
            style={{ "--course": color } as React.CSSProperties}>
            <p className="font-serif text-[16px] text-muted mb-4 text-center">{card.front.replace(/\$+/g, "")}</p>
            <div className="flex-1 overflow-y-auto"><Markdown text={card.back} /></div>
            <div className="flex items-center justify-between mt-6 text-[12px] text-faint">
              {card.source_note_id ? <Link href={`/notes?id=${card.source_note_id}`} className="hover:text-accent">来自《{card.note_title}》</Link> : <span>手动添加</span>}
              <button className="flex items-center gap-1 hover:text-ink" onClick={() => setEditing(card)}><Pencil size={12} />改错字</button>
            </div>
          </div>
        </div>
      </div>

      <div className={`grid grid-cols-3 gap-3 w-full max-w-[560px] mt-8 transition-opacity ${flipped ? "" : "opacity-30 pointer-events-none"}`}>
        {([["forgot", "忘记", "1", "var(--bad)"], ["hard", "模糊", "2", "var(--warn)"], ["good", "记得", "3", "var(--accent)"]] as const).map(([g, label, k, c]) => (
          <button key={g} onClick={() => grade(g)} className="h-16 rounded-xl border border-rule hover:border-current transition-colors flex flex-col items-center justify-center"
            style={{ color: c }}>
            <span className="text-[15px] font-semibold">{label}</span>
            <span className="text-[11.5px] text-muted mt-0.5">{card.preview && intervalLabel(card.preview[g])}后再见 <kbd className="ml-1 px-1 rounded border border-rule text-[10px] text-faint font-sans">{k}</kbd></span>
          </button>
        ))}
      </div>
      <CardEditor card={editing} onClose={() => setEditing(null)} onSaved={(c) => setQueue((q) => q!.map((x) => (x.id === c.id ? { ...x, ...c } : x)))} />
    </div>
  );
}

function Library({ noteId }: { noteId: string | null }) {
  const [course, setCourse] = useState("");
  const [weak, setWeak] = useState(false);
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
      <div className="flex items-center gap-2 mb-4">
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
            </div>
            <div className="text-right text-[12px] text-muted shrink-0 tabular-nums">
              <p>{c.due}</p>
              <p className="text-faint">间隔 {c.interval} 天，EF {c.ef.toFixed(2)}{c.forgot_count ? `，忘记 ${c.forgot_count} 次` : ""}</p>
            </div>
            <div className="flex opacity-0 group-hover:opacity-100 transition-opacity">
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
    </div>
  );
}

function CardEditor({ card, courses, onClose, onSaved }: {
  card: Card | null; courses?: Course[]; onClose: () => void; onSaved: (c: Card) => void;
}) {
  const [front, setFront] = useState("");
  const [back, setBack] = useState("");
  const [course, setCourse] = useState("");
  const toast = useToast();
  const isNew = card !== null && !card.id;
  useEffect(() => {
    if (card) {
      setFront(card.front || "");
      setBack(card.back || "");
      setCourse(card.course_id || courses?.[0]?.id || "");
    }
  }, [card, courses]);
  const save = async () => {
    try {
      const c = isNew
        ? await api<Card>("/api/cards", { method: "POST", json: { course_id: course, front, back } })
        : await api<Card>(`/api/cards/${card!.id}`, { method: "PATCH", json: { front, back } });
      onSaved(c);
      onClose();
    } catch (e) {
      toast((e as ApiError).message, "bad");
    }
  };
  return (
    <Modal open={!!card} onClose={onClose} title={isNew ? "手动建卡" : "编辑卡片"} width={520}
      footer={<><button className="btn btn-outline" onClick={onClose}>取消</button>
        <button className="btn btn-primary" disabled={!front.trim() || !back.trim() || (isNew && !course)} onClick={save}>保存</button></>}>
      <div className="p-5 space-y-4">
        {isNew && (
          <div>
            <label className="label" htmlFor="card-course">学科</label>
            <select id="card-course" className="input" value={course} onChange={(e) => setCourse(e.target.value)}>
              {courses?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
        )}
        <div>
          <label className="label" htmlFor="card-front">正面：问题或关键词</label>
          <input id="card-front" className="input font-serif" maxLength={200} value={front} onChange={(e) => setFront(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="card-back">背面：要点（支持 Markdown 和公式）</label>
          <textarea id="card-back" className="input" rows={5} maxLength={2000} value={back} onChange={(e) => setBack(e.target.value)} />
        </div>
      </div>
    </Modal>
  );
}
