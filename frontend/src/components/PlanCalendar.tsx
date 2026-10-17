"use client";

import { Check, ChevronLeft, ChevronRight, Flag, Layers, Plus } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { api, ApiError, CalendarData, fetcher, Task } from "@/lib/api";
import { colorOf, parseDay, shiftDay, WEEKDAYS, weekdayIndex, ymd } from "@/lib/format";
import { useToast } from "./ui";

const MAX_CHIPS = 3;
const DRAG_TYPE = "text/x-knowflow-task";

/** 计划月历：所有未归档计划的任务按日期铺开，可拖动改期；每天显示到期的复习卡片数和计划截止日。 */
export function PlanCalendar() {
  const guess = ymd(new Date(Date.now() - 4 * 3600e3)); // 凌晨 4 点前算前一天，和后端一致
  const [first, setFirst] = useState(() => guess.slice(0, 8) + "01");
  const [dragging, setDragging] = useState<Task | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [adding, setAdding] = useState<string | null>(null);
  const toast = useToast();

  const firstDate = parseDay(first);
  const gridStart = shiftDay(first, -weekdayIndex(firstDate));
  const daysInMonth = new Date(firstDate.getFullYear(), firstDate.getMonth() + 1, 0).getDate();
  const weeks = Math.ceil((weekdayIndex(firstDate) + daysInMonth) / 7);
  const gridEnd = shiftDay(gridStart, weeks * 7 - 1);
  const key = `/api/calendar?start=${gridStart}&end=${gridEnd}`;
  const { data, mutate } = useSWR<CalendarData>(key, fetcher, { keepPreviousData: true });
  const today = data?.today || guess;
  const month = first.slice(0, 7);

  const byDay = useMemo(() => {
    const m = new Map<string, Task[]>();
    data?.tasks.forEach((t) => t.due_date && m.set(t.due_date, [...(m.get(t.due_date) || []), t]));
    return m;
  }, [data]);
  const deadlines = useMemo(() => {
    const m = new Map<string, CalendarData["plans"]>();
    data?.plans.forEach((p) => p.deadline && m.set(p.deadline, [...(m.get(p.deadline) || []), p]));
    return m;
  }, [data]);

  const refresh = (t?: Task) => {
    mutate();
    globalMutate("/api/tasks/today");
    if (t) globalMutate(`/api/plans/${t.plan_id}`);
    globalMutate("/api/plans");
  };

  const move = async (t: Task, day: string | null) => {
    if (t.due_date === day) return;
    // 先在界面上挪过去，再告诉后端
    mutate((d) => d && {
      ...d,
      tasks: day ? [...d.tasks.filter((x) => x.id !== t.id), { ...t, due_date: day, overdue: false, unscheduled: false }] : d.tasks.filter((x) => x.id !== t.id),
      unscheduled: day ? d.unscheduled.filter((x) => x.id !== t.id) : [...d.unscheduled, { ...t, due_date: null, unscheduled: true }],
    }, { revalidate: false });
    try {
      await api(`/api/tasks/${t.id}`, { method: "PATCH", json: { due_date: day } });
      toast(day ? `已改到 ${parseDay(day).getMonth() + 1} 月 ${parseDay(day).getDate()} 日` : "已移到未排期");
    } catch (e) {
      toast((e as ApiError).message, "bad");
    }
    refresh(t);
  };

  const toggle = async (t: Task) => {
    await api(`/api/tasks/${t.id}`, { method: "PATCH", json: { status: t.status === "done" ? "todo" : "done" } });
    refresh(t);
  };

  const dropProps = (day: string | null) => ({
    onDragOver: (e: React.DragEvent) => {
      if (!dragging || (day && day < today)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      if (over !== (day ?? "none")) setOver(day ?? "none");
    },
    onDragLeave: (e: React.DragEvent) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(null);
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      setOver(null);
      if (dragging) move(dragging, day);
      setDragging(null);
    },
  });

  const chip = (t: Task) => (
    <TaskChip key={t.id} t={t} onToggle={() => toggle(t)}
      onDragStart={() => setDragging(t)} onDragEnd={() => { setDragging(null); setOver(null); }} />
  );

  const shiftMonth = (n: number) => {
    const d = new Date(firstDate.getFullYear(), firstDate.getMonth() + n, 1);
    setFirst(ymd(d));
    setExpanded(null);
    setAdding(null);
  };

  return (
    <div className="flex gap-6 items-start max-[1000px]:flex-col">
      <div className="flex-1 min-w-0 w-full">
        <div className="flex items-center gap-2 mb-4">
          <h2 className="font-serif text-[24px] leading-none mr-2 tabular-nums">
            {firstDate.getFullYear()} 年 {firstDate.getMonth() + 1} 月
          </h2>
          <button className="btn btn-icon" onClick={() => shiftMonth(-1)} aria-label="上个月"><ChevronLeft size={17} /></button>
          <button className="btn btn-outline h-7 text-[12px]" onClick={() => setFirst(today.slice(0, 8) + "01")}>今天</button>
          <button className="btn btn-icon" onClick={() => shiftMonth(1)} aria-label="下个月"><ChevronRight size={17} /></button>
          <span className="flex-1" />
          <span className="flex items-center gap-3 text-[12px] text-muted">
            <span className="flex items-center gap-1"><Layers size={12} className="text-accent" />到期卡片</span>
            <span className="flex items-center gap-1"><Flag size={12} className="text-bad" />计划截止</span>
          </span>
        </div>

        <div className="grid grid-cols-7 text-[12px] text-muted mb-1.5">
          {WEEKDAYS.map((w, i) => <span key={w} className={`px-2 ${i >= 5 ? "text-faint" : ""}`}>周{w}</span>)}
        </div>
        <div className="grid grid-cols-7 border-t border-l border-rule rounded-xl overflow-hidden">
          {Array.from({ length: weeks * 7 }, (_, i) => {
            const day = shiftDay(gridStart, i);
            const date = parseDay(day);
            const inMonth = day.startsWith(month);
            const isToday = day === today;
            const past = day < today;
            const tasks = byDay.get(day) || [];
            const open = expanded === day;
            const shown = open ? tasks : tasks.slice(0, MAX_CHIPS);
            const due = data?.reviews[day] || 0;
            return (
              <div key={day} {...dropProps(day)}
                className={`group relative min-h-[118px] p-1.5 border-r border-b border-rule flex flex-col gap-1 transition-colors
                  ${inMonth ? "" : "bg-canvas/60"} ${i % 7 >= 5 && inMonth ? "bg-canvas/30" : ""}
                  ${over === day ? "!bg-accent-soft" : ""} ${dragging && past ? "cursor-not-allowed" : ""}`}>
                <div className="flex items-center gap-1 h-6">
                  <span className={`grid place-items-center min-w-6 h-6 px-1 rounded-full text-[12.5px] tabular-nums
                    ${isToday ? "bg-accent text-[var(--accent-ink)] font-semibold" : inMonth ? (past ? "text-muted" : "") : "text-faint"}`}>
                    {date.getDate() === 1 && !isToday ? `${date.getMonth() + 1}/1` : date.getDate()}
                  </span>
                  {due > 0 && (
                    <Link href={isToday ? "/review" : "/review?tab=cards"} title={`${due} 张卡片${isToday ? "今天到期（含逾期）" : "这天到期"}`}
                      className="flex items-center gap-0.5 h-5 px-1.5 rounded-md text-[11px] text-accent bg-accent-soft tabular-nums hover:brightness-95">
                      <Layers size={11} />{due}
                    </Link>
                  )}
                  <span className="flex-1" />
                  {!past && (
                    <button onClick={() => setAdding(adding === day ? null : day)} aria-label="在这天添加任务"
                      className="btn btn-icon w-6 h-6 opacity-0 group-hover:opacity-100 focus:opacity-100 text-muted">
                      <Plus size={14} />
                    </button>
                  )}
                </div>
                {deadlines.get(day)?.map((p) => (
                  <Link key={p.id} href={`/plans?id=${p.id}`} className="flex items-center gap-1 text-[11.5px] text-bad truncate hover:underline">
                    <Flag size={11} className="shrink-0" /><span className="truncate">{p.goal}</span>
                  </Link>
                ))}
                {shown.map(chip)}
                {tasks.length > MAX_CHIPS && (
                  <button className="text-left text-[11.5px] text-muted hover:text-ink px-1" onClick={() => setExpanded(open ? null : day)}>
                    {open ? "收起" : `还有 ${tasks.length - MAX_CHIPS} 项`}
                  </button>
                )}
                {adding === day && data && (
                  <QuickAdd day={day} plans={data.plans} onClose={() => setAdding(null)} onAdded={() => refresh()} />
                )}
              </div>
            );
          })}
        </div>
        <p className="mt-3 text-[12px] text-faint">拖动任务可以改期；拖到右侧“未排期”就取消日期。点日期右上角的 + 直接添加任务。</p>
      </div>

      <aside className="w-[232px] shrink-0 max-[1000px]:w-full" {...dropProps(null)}>
        <div className={`rounded-xl border border-dashed p-3 transition-colors ${over === "none" ? "border-accent bg-accent-soft" : "border-rule"}`}>
          <h3 className="section-title mb-2">未排期 <span className="font-normal text-faint">{data?.unscheduled.length ?? ""}</span></h3>
          <div className="flex flex-col gap-1">
            {data?.unscheduled.map(chip)}
            {data && data.unscheduled.length === 0 && <p className="text-[12.5px] text-faint leading-relaxed">没有未排期的任务。把任务拖到这里可以先不定日期。</p>}
          </div>
        </div>
        {data && data.plans.length === 0 && (
          <p className="mt-4 text-[13px] text-muted">还没有计划。先建一个计划，任务就会出现在月历上。</p>
        )}
      </aside>
    </div>
  );
}

function TaskChip({ t, onToggle, onDragStart, onDragEnd }: {
  t: Task; onToggle: () => void; onDragStart: () => void; onDragEnd: () => void;
}) {
  const done = t.status === "done";
  return (
    <div draggable={!done}
      onDragStart={(e) => { e.dataTransfer.setData(DRAG_TYPE, t.id); e.dataTransfer.effectAllowed = "move"; onDragStart(); }}
      onDragEnd={onDragEnd}
      title={`${t.title}${t.plan_goal ? `\n计划：${t.plan_goal}` : ""}${t.estimated_minutes ? `\n约 ${t.estimated_minutes} 分钟` : ""}`}
      className={`group/chip flex items-center gap-1.5 min-h-[24px] pl-1.5 pr-1 rounded-md text-[12px] bg-paper border border-rule
        ${done ? "opacity-55" : "cursor-grab active:cursor-grabbing hover:border-faint"}`}
      style={{ borderLeft: `3px solid ${colorOf(t.course_color)}` }}>
      <button onClick={onToggle} aria-label={done ? "标为未完成" : "标为完成"}
        className={`w-3.5 h-3.5 rounded-full border grid place-items-center shrink-0 ${done ? "bg-accent border-accent text-[var(--accent-ink)]" : "border-faint hover:border-accent"}`}>
        {done && <Check size={9} strokeWidth={3.5} />}
      </button>
      <Link href={`/plans?id=${t.plan_id}`} draggable={false}
        className={`flex-1 min-w-0 truncate leading-tight ${done ? "line-through" : ""} ${t.overdue ? "text-bad" : ""}`}>
        {t.title}
      </Link>
    </div>
  );
}

function QuickAdd({ day, plans, onClose, onAdded }: {
  day: string; plans: CalendarData["plans"]; onClose: () => void; onAdded: () => void;
}) {
  const [title, setTitle] = useState("");
  const [planId, setPlanId] = useState(() => {
    try { const last = localStorage.getItem("kf-last-plan"); if (last && plans.some((p) => p.id === last)) return last; } catch {}
    return plans[0]?.id || "";
  });
  const toast = useToast();
  if (!plans.length) {
    return <p className="text-[11.5px] text-muted">先<Link href="/plans?new=1" className="text-accent">新建一个计划</Link></p>;
  }
  const add = async () => {
    if (!title.trim()) return onClose();
    try {
      await api(`/api/plans/${planId}/tasks`, { method: "POST", json: { title: title.trim(), kind: "learn", due_date: day, estimated_minutes: 30 } });
      try { localStorage.setItem("kf-last-plan", planId); } catch {}
      setTitle("");
      onAdded();
    } catch (e) {
      toast((e as ApiError).message, "bad");
    }
  };
  return (
    <div className="flex flex-col gap-1 anim-fade">
      <input autoFocus className="input h-7 px-2 text-[12px]" placeholder="任务名，回车添加" value={title} maxLength={200}
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.nativeEvent.isComposing) add();
          if (e.key === "Escape") onClose();
        }} />
      {plans.length > 1 && (
        <select className="h-6 rounded-md bg-transparent text-[11.5px] text-muted outline-none" value={planId}
          onChange={(e) => setPlanId(e.target.value)} aria-label="加到哪个计划">
          {plans.map((p) => <option key={p.id} value={p.id}>{p.goal}</option>)}
        </select>
      )}
    </div>
  );
}
