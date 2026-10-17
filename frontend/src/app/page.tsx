"use client";

import { ArrowUpRight, BookOpenCheck, Check, FilePlus2, FileUp, Pencil, Plus, Sparkles, Target, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { api, ApiError, Course, fetcher, Note, NoteSummary, Plan, Task } from "@/lib/api";
import { colorOf, greeting, relTime, shiftDay, TASK_KINDS, todayLabel } from "@/lib/format";
import { Heatmap } from "@/components/Heatmap";
import { FocusButton } from "@/components/Focus";
import { QuickReview, WeeklySummary } from "@/components/HomeWidgets";
import { Dot, useToast } from "@/components/ui";

type Stats = {
  today: string; streak: number; accuracy_7d: number | null; reviewed_today: number; due_today: number;
  total_cards: number; series: { day: string; total: number; good: number }[];
  due_by_course: { id: string; name: string; color: string; due: number }[];
};

export default function Today() {
  const { data: stats } = useSWR<Stats>("/api/stats?days=365", fetcher);
  const { data: tasks, mutate: mutateTasks } = useSWR<{ today: string; items: Task[] }>("/api/tasks/today", fetcher);
  const { data: notes } = useSWR<NoteSummary[]>("/api/notes", fetcher);
  const { data: courses } = useSWR<Course[]>("/api/courses", fetcher);
  const { data: ai } = useSWR<{ configured: boolean }>("/api/ai/status", fetcher);
  const courseMap = Object.fromEntries((courses || []).map((c) => [c.id, c]));

  const [adding, setAdding] = useState(false);
  const [quick, setQuick] = useState(false);
  const toast = useToast();
  const toggle = async (t: Task) => {
    await api(`/api/tasks/${t.id}`, { method: "PATCH", json: { status: t.status === "done" ? "todo" : "done" } });
    mutateTasks();
  };
  const postpone = async (t: Task) => {
    await api(`/api/tasks/${t.id}`, { method: "PATCH", json: { due_date: shiftDay(tasks!.today, 1) } });
    toast(`「${t.title}」改到明天了`);
    mutateTasks();
  };

  const due = stats?.due_today ?? 0;
  const dueCourses = (stats?.due_by_course || []).filter((c) => c.due > 0);
  const openTasks = tasks?.items.filter((t) => t.status !== "done") || [];
  const minutes = openTasks.reduce((s, t) => s + (t.estimated_minutes || 0), 0);

  return (
    <div className="max-w-[1040px] mx-auto px-10 py-12">
      <header className="mb-8 flex items-end justify-between gap-4 flex-wrap">
        <div>
          <p className="text-[14px] text-muted">{todayLabel()}</p>
          <h1 className="text-[30px] font-semibold tracking-tight mt-1">{greeting()}</h1>
        </div>
        <div className="flex items-center gap-1.5 flex-wrap">
          <Link href="/notes?new=1" className="btn btn-outline"><FilePlus2 size={14} />新建笔记</Link>
          <Link href="/materials" className="btn btn-outline"><FileUp size={14} />上传资料</Link>
          <Link href="/plans?new=1" className="btn btn-outline"><Target size={14} />新建计划</Link>
          <button className="btn btn-outline" onClick={() => window.dispatchEvent(new Event("kf-assistant"))}>
            <Sparkles size={14} className="text-accent" />问 AI
          </button>
        </div>
      </header>

      <GuideBanner />
      <QuickCapture courses={courses || []} />

      {ai && !ai.configured && (
        <Link href="/settings"
          className="flex items-center gap-3 mb-8 px-4 py-3 rounded-xl border border-rule hover:bg-hover transition-colors">
          <Sparkles size={16} className="text-accent" />
          <span className="text-[14px] flex-1">连接大模型后，就能让 AI 整理笔记、写摘要、提炼知识点和排计划。</span>
          <span className="text-[13px] text-accent font-medium">去设置</span>
        </Link>
      )}

      <div className="grid grid-cols-[minmax(0,420px)_1fr] gap-14 max-[900px]:grid-cols-1">
        {/* 今日复习：整页唯一的“实物” */}
        <section>
          <h2 className="section-title">今日复习</h2>
          {quick ? <QuickReview onExit={() => setQuick(false)} /> : (
          <div className="card-stack" style={{ "--course": colorOf(dueCourses[0]?.color) } as React.CSSProperties}>
            <div className="index-card px-7 pt-10 pb-7" style={{ "--course": due ? colorOf(dueCourses[0]?.color) : "var(--rule)" } as React.CSSProperties}>
              {due > 0 ? (
                <>
                  <p className="font-serif text-[52px] leading-none tabular-nums">{due}</p>
                  <p className="text-[15px] mt-2">张卡片等你回忆</p>
                  <div className="mt-6 space-y-2">
                    {dueCourses.map((c) => (
                      <Link key={c.id} href={`/review?course=${c.id}`}
                        className="flex items-center gap-2.5 text-[13.5px] hover:text-accent">
                        <Dot color={c.color} />
                        <span className="flex-1 truncate">{c.name}</span>
                        <span className="tabular-nums text-muted">{c.due}</span>
                      </Link>
                    ))}
                  </div>
                  <div className="flex gap-2 mt-7">
                    <button className="btn btn-primary h-10 px-5 text-[14px] flex-1" onClick={() => setQuick(true)}>在这里翻卡</button>
                    <Link href="/review" className="btn btn-outline h-10 px-4 text-[14px]">复习页</Link>
                  </div>
                </>
              ) : (
                <>
                  <p className="font-serif text-[22px] leading-snug">今天的卡片都复习完了</p>
                  <p className="text-[13.5px] text-muted mt-2">
                    {stats?.reviewed_today ? `今天回忆了 ${stats.reviewed_today} 张。` : ""}
                    {stats?.total_cards ? "新卡片会在到期那天出现在这里。" : "从笔记里提炼知识点，就会生成复习卡片。"}
                  </p>
                  <Link href={stats?.total_cards ? "/review?tab=cards" : "/notes"} className="btn btn-outline mt-6">
                    {stats?.total_cards ? "查看全部卡片" : "去写笔记"}
                  </Link>
                </>
              )}
            </div>
          </div>
          )}
          {stats && (
            <div className="mt-12">
              <Heatmap series={stats.series} today={stats.today} streak={stats.streak} accuracy={stats.accuracy_7d} />
            </div>
          )}
        </section>

        <div className="space-y-12 min-w-0">
          <section>
            <div className="flex items-baseline justify-between">
              <h2 className="section-title">今日计划</h2>
              <span className="flex items-center gap-3">
                {minutes > 0 && <span className="text-[12px] text-muted">还剩约 {minutes} 分钟</span>}
                <button className="text-[12px] text-muted hover:text-accent flex items-center gap-0.5" onClick={() => setAdding((v) => !v)}>
                  <Plus size={13} />添加
                </button>
              </span>
            </div>
            {adding && tasks && <AddTodayTask today={tasks.today} onDone={() => { setAdding(false); mutateTasks(); }} />}
            {tasks && tasks.items.length === 0 && (
              !adding && (
                <p className="text-[14px] text-muted py-3">
                  今天没有安排任务。<button className="text-accent" onClick={() => setAdding(true)}>加一个</button>，或<Link href="/plans?view=calendar" className="text-accent">打开月历</Link>
                </p>
              )
            )}
            <ul className="divide-y divide-rule border-y border-rule">
              {tasks?.items.map((t) => (
                <li key={t.id} className="group flex items-center gap-3 py-3">
                  <button onClick={() => toggle(t)} aria-label={t.status === "done" ? "标为未完成" : "标为完成"}
                    className={`w-[18px] h-[18px] rounded-full border grid place-items-center shrink-0 transition-colors ${t.status === "done" ? "bg-accent border-accent text-[var(--accent-ink)]" : "border-faint hover:border-accent"}`}>
                    {t.status === "done" && <Check size={12} strokeWidth={3} />}
                  </button>
                  <Link href={`/plans?id=${t.plan_id}`} className="flex-1 min-w-0">
                    <span className={`block text-[14px] truncate ${t.status === "done" ? "line-through text-faint" : ""}`}>{t.title}</span>
                    <span className="block text-[12px] text-muted truncate mt-0.5">
                      {TASK_KINDS[t.kind]}
                      {t.overdue && <span className="text-bad">，已逾期（原定 {t.due_date}）</span>}
                      {t.course_name && `，${t.course_name}`}
                    </span>
                  </Link>
                  {t.status !== "done" && (
                    <button className="btn h-7 text-[12px] text-muted opacity-0 group-hover:opacity-100 focus:opacity-100" onClick={() => postpone(t)}
                      title="改到明天">推到明天</button>
                  )}
                  <FocusButton task={t} className="opacity-0 group-hover:opacity-100 focus:opacity-100" />
                  <span className="text-[12px] text-muted tabular-nums text-right">
                    {t.estimated_minutes ? `${t.estimated_minutes} 分钟` : ""}
                    {t.actual_minutes ? <span className="block text-[11px] text-accent">实际 {t.actual_minutes}</span> : null}
                  </span>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <div className="flex items-baseline justify-between">
              <h2 className="section-title">最近的笔记</h2>
              <span className="flex items-center gap-3">
                <Link href="/notes?new=1" className="text-[12px] text-muted hover:text-accent flex items-center gap-0.5"><Plus size={13} />新建</Link>
                <Link href="/notes" className="text-[12px] text-muted hover:text-ink">全部笔记</Link>
              </span>
            </div>
            <ul className="divide-y divide-rule border-y border-rule">
              {notes?.slice(0, 5).map((n) => (
                <li key={n.id} className="group relative">
                  <Link href={`/notes?id=${n.id}&edit=1`} title="直接编辑"
                    className="absolute right-6 top-1/2 -translate-y-1/2 btn btn-icon h-7 w-7 bg-paper text-muted opacity-0 group-hover:opacity-100 focus:opacity-100 z-10">
                    <Pencil size={13} />
                  </Link>
                  <Link href={`/notes?id=${n.id}`} className="flex items-center gap-3 py-3">
                    <Dot color={courseMap[n.course_id]?.color} />
                    <span className="flex-1 min-w-0">
                      <span className="block text-[14px] font-medium truncate">{n.title}</span>
                      <span className="block text-[12.5px] text-muted truncate mt-0.5">{n.excerpt || "空白笔记"}</span>
                    </span>
                    <span className="text-[12px] text-faint shrink-0">{relTime(n.updated_at)}</span>
                    <ArrowUpRight size={14} className="text-faint opacity-0 group-hover:opacity-100 transition-opacity" />
                  </Link>
                </li>
              ))}
            </ul>
            {notes?.length === 0 && <p className="text-[14px] text-muted py-3">还没有笔记。</p>}
          </section>

          <WeeklySummary />
        </div>
      </div>

    </div>
  );
}

/** 快速记一笔：第一行作标题，其余作正文，直接存成笔记 */
function QuickCapture({ courses }: { courses: Course[] }) {
  const [text, setText] = useState("");
  const [courseId, setCourseId] = useState("");
  const [saved, setSaved] = useState<Note | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const target = courseId || courses[0]?.id || "";
  const save = async () => {
    const body = text.trim();
    if (!body || !target || busy) return;
    const [first, ...rest] = body.split("\n");
    const title = first.replace(/^#+\s*/, "").trim().slice(0, 40) || "随手记";
    setBusy(true);
    try {
      const n = await api<Note>("/api/notes", { method: "POST", json: { course_id: target, title, content: rest.join("\n").trim() || (first.length > 40 ? body : "") } });
      setSaved(n);
      setText("");
      globalMutate("/api/notes");
      globalMutate("/api/courses");
    } catch (e) {
      toast((e as ApiError).message, "bad");
    } finally {
      setBusy(false);
    }
  };
  if (!courses.length) return null;
  return (
    <div className="mb-10 rounded-2xl border border-rule bg-paper px-4 pt-3 pb-2.5 focus-within:border-accent focus-within:shadow-[0_0_0_3px_var(--accent-soft)] transition-shadow">
      <textarea value={text} onChange={(e) => { setText(e.target.value); setSaved(null); }} rows={1} maxLength={20000}
        placeholder="快速记一笔：想到什么先记下来。第一行当标题，回车保存，Shift+回车换行"
        className="w-full resize-none bg-transparent outline-none text-[14.5px] leading-7 placeholder:text-faint max-h-48"
        style={{ fieldSizing: "content" } as React.CSSProperties}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); save(); } }} />
      <div className="flex items-center gap-2 mt-1 text-[12px] text-muted">
        <span>存到</span>
        <select className="h-7 rounded-md bg-transparent outline-none hover:bg-hover px-1" value={target} onChange={(e) => setCourseId(e.target.value)} aria-label="存到哪个学科">
          {courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <span className="flex-1" />
        {saved && (
          <Link href={`/notes?id=${saved.id}&edit=1`} className="text-accent anim-fade">已存为《{saved.title}》，继续编辑 →</Link>
        )}
        <button className="btn btn-primary h-7 text-[12px]" disabled={!text.trim() || busy} onClick={save}>保存</button>
      </div>
    </div>
  );
}

/** 今日计划里直接加一个今天的任务 */
function AddTodayTask({ today, onDone }: { today: string; onDone: () => void }) {
  const { data: plans } = useSWR<Plan[]>("/api/plans", fetcher);
  const active = (plans || []).filter((p) => !p.archived);
  const [title, setTitle] = useState("");
  const [minutes, setMinutes] = useState("30");
  const [planId, setPlanId] = useState("");
  const toast = useToast();
  const plan = planId || active[0]?.id || "";
  if (plans && !active.length) {
    return <p className="text-[13px] text-muted py-3">还没有计划，<Link href="/plans?new=1" className="text-accent">先建一个</Link>，任务要放在计划里。</p>;
  }
  const add = async () => {
    if (!title.trim() || !plan) return;
    try {
      await api(`/api/plans/${plan}/tasks`, { method: "POST", json: { title: title.trim(), kind: "learn", due_date: today, estimated_minutes: minutes ? Number(minutes) : null } });
      onDone();
    } catch (e) {
      toast((e as ApiError).message, "bad");
    }
  };
  return (
    <div className="flex items-center gap-2 mb-2 p-1.5 rounded-xl border border-accent shadow-[0_0_0_3px_var(--accent-soft)] anim-fade">
      <input autoFocus className="flex-1 min-w-0 h-8 px-2 bg-transparent outline-none text-[14px] placeholder:text-faint" placeholder="今天要做什么？回车添加"
        value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) add(); if (e.key === "Escape") onDone(); }} />
      <input type="number" min={1} max={1440} value={minutes} onChange={(e) => setMinutes(e.target.value)} aria-label="预计分钟"
        className="w-12 h-8 rounded-lg bg-hover text-right px-1.5 text-[12.5px] outline-none" />
      <span className="text-[12px] text-muted">分钟</span>
      {active.length > 1 && (
        <select className="h-8 max-w-[120px] rounded-lg bg-hover px-1 text-[12px] text-muted outline-none" value={plan} onChange={(e) => setPlanId(e.target.value)} aria-label="加到哪个计划">
          {active.map((p) => <option key={p.id} value={p.id}>{p.goal}</option>)}
        </select>
      )}
      <button className="btn btn-primary h-8" disabled={!title.trim()} onClick={add}>添加</button>
    </div>
  );
}

/** 第一次打开时提示看使用说明，关掉后不再出现 */
function GuideBanner() {
  const [show, setShow] = useState(false);
  useEffect(() => {
    try { setShow(!localStorage.getItem("kf-guide-seen")); } catch {}
  }, []);
  if (!show) return null;
  const hide = () => { setShow(false); try { localStorage.setItem("kf-guide-seen", "1"); } catch {} };
  return (
    <div className="flex items-center gap-3 mb-6 px-4 py-3 rounded-xl bg-accent-soft anim-fade">
      <BookOpenCheck size={17} className="text-accent shrink-0" />
      <p className="flex-1 text-[14px]">第一次用 KnowFlow？花两分钟看看<Link href="/guide" onClick={hide} className="text-accent font-medium mx-1">使用说明</Link>，或者按 Ctrl+J 直接问 AI。</p>
      <button className="btn btn-icon h-7 w-7 text-muted" onClick={hide} aria-label="不再提示"><X size={14} /></button>
    </div>
  );
}
