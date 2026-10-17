"use client";

import { ArrowUpRight, Check, Sparkles } from "lucide-react";
import Link from "next/link";
import useSWR from "swr";
import { api, Course, fetcher, NoteSummary, Task } from "@/lib/api";
import { colorOf, greeting, relTime, TASK_KINDS, todayLabel } from "@/lib/format";
import { Dot } from "@/components/ui";

type Stats = {
  today: string; streak: number; accuracy_7d: number | null; reviewed_today: number; due_today: number;
  total_cards: number; series: { day: string; total: number; good: number }[];
  due_by_course: { id: string; name: string; color: string; due: number }[];
};

export default function Today() {
  const { data: stats } = useSWR<Stats>("/api/stats?days=30", fetcher);
  const { data: tasks, mutate: mutateTasks } = useSWR<{ today: string; items: Task[] }>("/api/tasks/today", fetcher);
  const { data: notes } = useSWR<NoteSummary[]>("/api/notes", fetcher);
  const { data: courses } = useSWR<Course[]>("/api/courses", fetcher);
  const { data: ai } = useSWR<{ configured: boolean }>("/api/ai/status", fetcher);
  const courseMap = Object.fromEntries((courses || []).map((c) => [c.id, c]));

  const toggle = async (t: Task) => {
    await api(`/api/tasks/${t.id}`, { method: "PATCH", json: { status: t.status === "done" ? "todo" : "done" } });
    mutateTasks();
  };

  const due = stats?.due_today ?? 0;
  const dueCourses = (stats?.due_by_course || []).filter((c) => c.due > 0);
  const openTasks = tasks?.items.filter((t) => t.status !== "done") || [];
  const minutes = openTasks.reduce((s, t) => s + (t.estimated_minutes || 0), 0);

  return (
    <div className="max-w-[1040px] mx-auto px-10 py-12">
      <header className="mb-10">
        <p className="text-[14px] text-muted">{todayLabel()}</p>
        <h1 className="text-[30px] font-semibold tracking-tight mt-1">{greeting()}</h1>
      </header>

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
                  <Link href="/review" className="btn btn-primary h-10 px-5 text-[14px] mt-7 w-full">开始复习</Link>
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
          {stats && <ReviewBars stats={stats} />}
        </section>

        <div className="space-y-12 min-w-0">
          <section>
            <div className="flex items-baseline justify-between">
              <h2 className="section-title">今日计划</h2>
              {minutes > 0 && <span className="text-[12px] text-muted">还剩约 {minutes} 分钟</span>}
            </div>
            {tasks && tasks.items.length === 0 && (
              <p className="text-[14px] text-muted py-3">
                今天没有安排任务。<Link href="/plans" className="text-accent">打开计划</Link>
              </p>
            )}
            <ul className="divide-y divide-rule border-y border-rule">
              {tasks?.items.map((t) => (
                <li key={t.id} className="flex items-center gap-3 py-3">
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
                  {t.estimated_minutes && <span className="text-[12px] text-muted tabular-nums">{t.estimated_minutes} 分钟</span>}
                </li>
              ))}
            </ul>
          </section>

          <section>
            <div className="flex items-baseline justify-between">
              <h2 className="section-title">最近的笔记</h2>
              <Link href="/notes" className="text-[12px] text-muted hover:text-ink">全部笔记</Link>
            </div>
            <ul className="divide-y divide-rule border-y border-rule">
              {notes?.slice(0, 5).map((n) => (
                <li key={n.id}>
                  <Link href={`/notes?id=${n.id}`} className="group flex items-center gap-3 py-3">
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
        </div>
      </div>
    </div>
  );
}

function ReviewBars({ stats }: { stats: Stats }) {
  const max = Math.max(4, ...stats.series.map((s) => s.total));
  return (
    <div className="mt-12">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="section-title mb-0">近 30 天</h2>
        <span className="text-[12px] text-muted">
          连续 {stats.streak} 天
          {stats.accuracy_7d !== null && `，近 7 天记得 ${Math.round(stats.accuracy_7d * 100)}%`}
        </span>
      </div>
      <div className="flex items-end gap-[3px] h-16" role="img" aria-label="近 30 天每日复习数">
        {stats.series.map((s) => (
          <div key={s.day} className="flex-1 h-full flex items-end" title={`${s.day}：复习 ${s.total} 张，记得 ${s.good} 张`}>
            <div className="w-full rounded-[2px]"
              style={{
                height: s.total ? `${Math.max(8, (s.total / max) * 100)}%` : "3px",
                background: s.total ? "var(--accent)" : "var(--rule)",
                opacity: s.total ? 0.35 + 0.65 * (s.good / s.total) : 1,
              }} />
          </div>
        ))}
      </div>
      <div className="flex justify-between text-[11px] text-faint mt-1.5">
        <span>30 天前</span>
        <span>今天</span>
      </div>
    </div>
  );
}
