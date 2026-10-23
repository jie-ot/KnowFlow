"use client";

import { ArrowLeft, CalendarDays, Check, Flag, List, Plus, Sparkles, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { api, ApiError, Course, fetcher, NoteSummary, Plan, Problem, Task } from "@/lib/api";
import { dayLabel, TASK_KINDS } from "@/lib/format";
import { useAiTask } from "@/components/ai";
import { AiProgress, AiStatusLine, useStopGuard, Warnings } from "@/components/AiSheets";
import { PlanCalendar } from "@/components/PlanCalendar";
import { FocusButton } from "@/components/Focus";
import { Dot, Empty, Modal, PageHeader, useConfirm, useToast } from "@/components/ui";

export default function PlansPage() {
  return (
    <Suspense>
      <Plans />
    </Suspense>
  );
}

function Plans() {
  const id = useSearchParams().get("id");
  return id ? <PlanDetail id={id} /> : <PlanList />;
}

function PlanList() {
  const { data: plans, mutate } = useSWR<Plan[]>("/api/plans", fetcher);
  const params = useSearchParams();
  const [creating, setCreating] = useState(params.get("new") === "1");
  const router = useRouter();
  const [view, setView] = useState<"list" | "calendar">(() => {
    if (params.get("view") === "calendar") return "calendar";
    try { return localStorage.getItem("kf-plan-view") === "calendar" ? "calendar" : "list"; } catch { return "list"; }
  });
  const changeView = (v: "list" | "calendar") => {
    setView(v);
    try { localStorage.setItem("kf-plan-view", v); } catch {}
    router.replace(v === "calendar" ? "/plans?view=calendar" : "/plans");
  };
  return (
    <div className={`desktop-page ${view === "calendar" ? "!max-w-[1240px]" : ""}`}>
      <PageHeader title="计划"
        actions={<>
          <div className="seg">
            <button data-on={view === "list"} onClick={() => changeView("list")} className="flex items-center gap-1"><List size={13} />列表</button>
            <button data-on={view === "calendar"} onClick={() => changeView("calendar")} className="flex items-center gap-1"><CalendarDays size={13} />月历</button>
          </div>
          <button className="btn btn-primary" onClick={() => setCreating(true)}><Plus size={14} />新建计划</button>
        </>} />
      {view === "calendar" ? <PlanCalendar /> : <>
      <div className="border-y border-rule divide-y divide-rule">
        {plans?.map((p) => {
          const pct = p.task_total ? Math.round((p.task_done / p.task_total) * 100) : 0;
          return (
            <Link key={p.id} href={`/plans?id=${p.id}`} className="flex items-center gap-5 py-4 px-1 row-hover rounded-lg">
              <div className="flex-1 min-w-0">
                <p className={`text-[15px] font-medium truncate ${p.archived ? "text-faint" : ""}`}>{p.goal}</p>
                <p className="text-[12.5px] text-muted mt-1 flex items-center gap-2">
                  {p.course_name && <><Dot color={p.course_color} size={6} />{p.course_name}</>}
                  {p.deadline && <span>截止 {p.deadline}</span>}
                  {p.daily_minutes && <span>每天 {p.daily_minutes} 分钟</span>}
                  {p.task_overdue > 0 && <span className="text-bad">{p.task_overdue} 项逾期</span>}
                </p>
              </div>
              <div className="w-40">
                <div className="flex justify-between text-[12px] text-muted mb-1.5"><span>{p.task_done}/{p.task_total} 项</span><span>{pct}%</span></div>
                <div className="h-1.5 rounded-full bg-rule overflow-hidden"><div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} /></div>
              </div>
            </Link>
          );
        })}
      </div>
      {plans?.length === 0 && <Empty title="还没有计划">比如“两周后期中考试，每天 90 分钟”，建一个试试。</Empty>}
      </>}
      <PlanDialog open={creating} onClose={() => setCreating(false)} onSaved={(p) => { mutate(); router.push(`/plans?id=${p.id}`); }} />
    </div>
  );
}

function PlanDialog({ open, onClose, onSaved, plan }: { open: boolean; onClose: () => void; onSaved: (p: Plan) => void; plan?: Plan }) {
  const { data: courses } = useSWR<Course[]>("/api/courses", fetcher);
  const [f, setF] = useState({ goal: "", course_id: "", deadline: "", daily_minutes: "", progress_text: "" });
  const toast = useToast();
  useEffect(() => {
    if (open) setF({
      goal: plan?.goal || "", course_id: plan?.course_id || "", deadline: plan?.deadline || "",
      daily_minutes: plan?.daily_minutes ? String(plan.daily_minutes) : "", progress_text: plan?.progress_text || "",
    });
  }, [open, plan]);
  const save = async () => {
    const json = {
      goal: f.goal, course_id: f.course_id || null, deadline: f.deadline || null,
      daily_minutes: f.daily_minutes ? Number(f.daily_minutes) : null, progress_text: f.progress_text,
    };
    try {
      const p = plan
        ? await api<Plan>(`/api/plans/${plan.id}`, { method: "PATCH", json })
        : await api<Plan>("/api/plans", { method: "POST", json });
      onSaved(p);
      onClose();
    } catch (e) {
      toast((e as ApiError).message, "bad");
    }
  };
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));
  return (
    <Modal open={open} onClose={onClose} title={plan ? "编辑计划" : "新建计划"} width={520}
      footer={<><button className="btn btn-outline" onClick={onClose}>取消</button><button className="btn btn-primary" disabled={!f.goal.trim()} onClick={save}>{plan ? "保存" : "创建"}</button></>}>
      <div className="p-5 space-y-4">
        <div>
          <label className="label" htmlFor="p-goal">目标</label>
          <input id="p-goal" className="input" maxLength={200} placeholder="例如：操作系统期中考试复习" value={f.goal} onChange={set("goal")} />
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="label" htmlFor="p-course">学科</label>
            <select id="p-course" className="input" value={f.course_id} onChange={set("course_id")}>
              <option value="">不限</option>
              {courses?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="p-deadline">截止日（可不填）</label>
            <input id="p-deadline" type="date" className="input" value={f.deadline} onChange={set("deadline")} />
          </div>
          <div>
            <label className="label" htmlFor="p-min">每天可用分钟</label>
            <input id="p-min" type="number" min={1} max={1440} className="input" placeholder="不限" value={f.daily_minutes} onChange={set("daily_minutes")} />
          </div>
        </div>
        <div>
          <label className="label" htmlFor="p-progress">现在的进度</label>
          <textarea id="p-progress" className="input" rows={3} maxLength={2000} placeholder="例如：前两章看完了，第三章还没开始" value={f.progress_text} onChange={set("progress_text")} />
        </div>
      </div>
    </Modal>
  );
}

function PlanDetail({ id }: { id: string }) {
  const { data: plan, mutate } = useSWR<Plan>(`/api/plans/${id}`, fetcher);
  const [editing, setEditing] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDue, setNewDue] = useState<string | null>(null);
  const [newMin, setNewMin] = useState("30");
  const confirm = useConfirm();
  const router = useRouter();
  const today = new Date(Date.now() - 4 * 3600e3).toLocaleDateString("sv-SE");

  const groups = useMemo(() => {
    const g = new Map<string, Task[]>();
    (plan?.tasks || []).forEach((t) => {
      const k = t.overdue ? "overdue" : t.due_date || "none";
      g.set(k, [...(g.get(k) || []), t]);
    });
    return [...g.entries()].sort(([a], [b]) => (a === "overdue" ? -1 : b === "overdue" ? 1 : a === "none" ? 1 : b === "none" ? -1 : a.localeCompare(b)));
  }, [plan]);

  if (!plan) return null;
  const patchTask = async (t: Task, json: Record<string, unknown>) => {
    await api(`/api/tasks/${t.id}`, { method: "PATCH", json });
    mutate();
    globalMutate("/api/tasks/today");
  };
  const addTask = async () => {
    if (!newTitle.trim()) return;
    await api(`/api/plans/${id}/tasks`, {
      method: "POST",
      json: { title: newTitle.trim(), kind: "learn", due_date: newDue ?? today, estimated_minutes: newMin ? Number(newMin) : null },
    });
    setNewTitle("");
    mutate();
    globalMutate("/api/tasks/today");
  };
  const remove = async () => {
    if (!(await confirm({ title: "删除这个计划？", body: "计划里的任务会一起删除。", confirm: "删除", danger: true }))) return;
    await api(`/api/plans/${id}`, { method: "DELETE" });
    router.push("/plans");
  };
  const budgetDays = new Map((plan.budget_warnings || []).filter((w) => w.day).map((w) => [w.day!, w.message]));

  return (
    <div className="max-w-[880px] mx-auto px-10 py-10">
      <Link href="/plans" className="inline-flex items-center gap-1 text-[13px] text-muted hover:text-ink mb-5"><ArrowLeft size={14} />计划</Link>
      <div className="flex items-start gap-3">
        <h1 className="flex-1 text-[26px] font-semibold tracking-tight">{plan.goal}</h1>
        <button className="btn btn-outline" onClick={() => setEditing(true)}>编辑</button>
        <button className="btn btn-primary" onClick={() => setAiOpen(true)}><Sparkles size={14} />AI 拆任务</button>
        <button className="btn btn-icon text-muted" onClick={remove} aria-label="删除计划"><Trash2 size={16} /></button>
      </div>
      <p className="text-[13px] text-muted mt-2 flex items-center gap-3">
        {plan.course_name && <span className="flex items-center gap-1.5"><Dot color={plan.course_color} size={7} />{plan.course_name}</span>}
        <span>{plan.deadline ? `截止 ${plan.deadline}` : "没有截止日"}</span>
        <span>{plan.daily_minutes ? `每天 ${plan.daily_minutes} 分钟` : "每天时间不限"}</span>
        <span>已完成 {plan.task_done}/{plan.task_total} 项</span>
      </p>
      {plan.progress_text && <p className="mt-4 text-[14px] px-4 py-3 rounded-xl bg-canvas">现在的进度：{plan.progress_text}</p>}

      <div className="mt-8 space-y-8">
        {groups.map(([k, tasks]) => (
          <section key={k}>
            <h2 className={`section-title flex items-center gap-2 ${k === "overdue" ? "!text-bad" : ""}`}>
              {k === "overdue" ? "已逾期" : dayLabel(k === "none" ? null : k, today)}
              {k !== "none" && k !== "overdue" && <span className="font-normal text-faint">{tasks.reduce((s, t) => s + (t.estimated_minutes || 0), 0)} 分钟</span>}
              {budgetDays.get(k) && <span className="font-normal text-warn">{budgetDays.get(k)}</span>}
            </h2>
            <ul className="border-y border-rule divide-y divide-rule">
              {tasks.map((t) => <TaskRow key={t.id} t={t} onPatch={(j) => patchTask(t, j)}
                onDelete={async () => { await api(`/api/tasks/${t.id}`, { method: "DELETE" }); mutate(); }} />)}
            </ul>
          </section>
        ))}
        {plan.tasks?.length === 0 && <Empty title="计划里还没有任务">点“AI 拆任务”，或在下面直接添加。</Empty>}
        <div className="flex gap-2 items-center rounded-xl border border-rule p-1.5 focus-within:border-accent focus-within:shadow-[0_0_0_3px_var(--accent-soft)] transition-shadow">
          <Plus size={15} className="text-faint ml-1.5 shrink-0" />
          <input className="flex-1 min-w-0 h-8 bg-transparent outline-none text-[14px] placeholder:text-faint" placeholder="添加任务，回车保存" value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)} onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && addTask()} />
          <input type="date" className="h-8 px-2 rounded-lg bg-hover text-[12.5px] text-muted outline-none" value={newDue ?? today}
            onChange={(e) => setNewDue(e.target.value || null)} aria-label="日期" />
          <span className="flex items-center h-8 rounded-lg bg-hover pr-2 text-[12.5px] text-muted">
            <input type="number" min={1} max={1440} className="w-12 h-8 bg-transparent text-right outline-none" value={newMin}
              onChange={(e) => setNewMin(e.target.value)} aria-label="预计分钟" />
            分钟
          </span>
          <button className="btn btn-primary h-8" disabled={!newTitle.trim()} onClick={addTask}>添加</button>
        </div>
      </div>
      <PlanDialog open={editing} plan={plan} onClose={() => setEditing(false)} onSaved={() => mutate()} />
      <PlanAiSheet open={aiOpen} plan={plan} onClose={() => setAiOpen(false)} onAccepted={() => { setAiOpen(false); mutate(); globalMutate("/api/tasks/today"); }} />
    </div>
  );
}

function TaskRow({ t, onPatch, onDelete }: { t: Task; onPatch: (j: Record<string, unknown>) => void; onDelete: () => void }) {
  const done = t.status === "done";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(t.title);
  const commit = () => {
    setEditing(false);
    if (draft.trim() && draft.trim() !== t.title) onPatch({ title: draft.trim() });
    else setDraft(t.title);
  };
  return (
    <li className="group flex items-center gap-3 py-3">
      <button onClick={() => onPatch({ status: done ? "todo" : "done" })} aria-label={done ? "标为未完成" : "标为完成"}
        className={`w-[18px] h-[18px] rounded-full border grid place-items-center shrink-0 ${done ? "bg-accent border-accent text-[var(--accent-ink)]" : "border-faint hover:border-accent"}`}>
        {done && <Check size={12} strokeWidth={3} />}
      </button>
      <div className="flex-1 min-w-0">
        {editing ? (
          <input autoFocus className="w-full bg-transparent outline-none text-[14px] border-b border-accent" value={draft} maxLength={200}
            onChange={(e) => setDraft(e.target.value)} onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) commit();
              if (e.key === "Escape") { setDraft(t.title); setEditing(false); }
            }} />
        ) : (
          <p className={`text-[14px] cursor-text ${done ? "line-through text-faint" : ""}`} title="双击修改"
            onDoubleClick={() => { setDraft(t.title); setEditing(true); }}>{t.title}</p>
        )}
        <p className="text-[12px] text-muted mt-0.5 flex items-center gap-2">
          <span>{TASK_KINDS[t.kind]}</span>
          {t.status === "in_progress" && <span className="text-accent">进行中</span>}
          {t.needs_review && <span className="text-warn">需加强</span>}
          {!!t.actual_minutes && <span className="text-accent">实际用时 {t.actual_minutes} 分钟</span>}
        </p>
      </div>
      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
        <FocusButton task={t} />
        {!done && <button className="btn h-7 text-[12px]" onClick={() => onPatch({ status: t.status === "in_progress" ? "todo" : "in_progress" })}>{t.status === "in_progress" ? "暂停" : "开始"}</button>}
        <button className="btn btn-icon h-7" title="标记需加强" onClick={() => onPatch({ needs_review: !t.needs_review })}
          style={t.needs_review ? { color: "var(--warn)" } : undefined}><Flag size={13} /></button>
        <input type="date" className="h-7 px-1.5 rounded-md border border-rule bg-paper text-[12px] text-muted" value={t.due_date || ""}
          aria-label="改期" onChange={(e) => onPatch({ due_date: e.target.value || null })} />
        <button className="btn btn-icon h-7 text-muted" onClick={onDelete} aria-label="删除任务"><X size={14} /></button>
      </div>
      <select className="h-7 rounded-md bg-transparent text-[12px] text-muted tabular-nums text-right outline-none hover:bg-hover cursor-pointer"
        value={t.estimated_minutes ?? ""} aria-label="预计分钟"
        onChange={(e) => onPatch({ estimated_minutes: e.target.value ? Number(e.target.value) : null })}>
        <option value="">不估时</option>
        {[...new Set([10, 15, 20, 30, 45, 60, 90, 120, ...(t.estimated_minutes ? [t.estimated_minutes] : [])])].sort((a, b) => a - b)
          .map((m) => <option key={m} value={m}>{m} 分钟</option>)}
      </select>
    </li>
  );
}

type DraftTask = { title: string; kind: string; due_date: string | null; estimated_minutes: number | null; note_ids: string[] };

function PlanAiSheet({ open, plan, onClose, onAccepted }: { open: boolean; plan: Plan; onClose: () => void; onAccepted: () => void }) {
  const ai = useAiTask();
  const { data: noteIndex } = useSWR<Pick<NoteSummary, "id" | "title" | "course_id">[]>(open ? "/api/notes/index" : null, fetcher);
  const notes = noteIndex?.filter((note) => !plan.course_id || note.course_id === plan.course_id);
  const [scope, setScope] = useState<Set<string>>(new Set());
  const [tasks, setTasks] = useState<DraftTask[]>([]);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  const result = ai.state.draft?.result as { result_type: string; questions: string[]; assumptions: string[]; tasks: DraftTask[]; problems: Problem[] } | undefined;

  useEffect(() => { if (!open) { ai.reset(); setTasks([]); setProblems([]); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (open && notes) setScope(new Set(notes.slice(0, 10).map((n) => n.id))); }, [open, notes]);
  useEffect(() => { if (result) { setTasks(result.tasks); setProblems(result.problems); } }, [result]);

  const start = () => ai.run({ kind: "plan", plan_id: plan.id, scope_note_ids: [...scope].slice(0, 30) });
  const accept = async () => {
    setSaving(true);
    try {
      await api(`/api/ai/drafts/${ai.state.draft!.id}/accept`, { method: "POST", json: { tasks } });
      toast(`已加入 ${tasks.length} 个任务`);
      onAccepted();
    } catch (e) {
      const err = e as ApiError;
      if (err.code === "PLAN_INVALID") setProblems(err.data.problems as Problem[]);
      toast(err.message, "bad");
    } finally {
      setSaving(false);
    }
  };
  const edit = (i: number, patch: Partial<DraftTask>) => {
    setTasks((xs) => xs.map((x, j) => (j === i ? { ...x, ...patch } : x)));
    setProblems([]);
  };
  const begun = ai.state.running || ai.state.draft || ai.state.error;
  const problemFor = (i: number) => problems.filter((p) => p.index === i);
  const general = problems.filter((p) => p.index === null);
  const guard = useStopGuard();
  const close = () => guard(ai.state, () => { ai.reset(); onClose(); });

  return (
    <Modal open={open} onClose={close} width={820}
      title={<span className="flex items-center gap-2"><Sparkles size={15} className="text-accent" />AI 拆任务</span>}
      footer={begun ? <>
        <span className="mr-auto"><AiStatusLine state={ai.state} /></span>
        <button className="btn btn-outline" onClick={start}>重新生成</button>
        <button className="btn btn-primary" disabled={!tasks.length || saving || result?.result_type === "needs_clarification"} onClick={accept}>加入计划（{tasks.length} 项）</button>
      </> : <>
        <button className="btn btn-outline" onClick={onClose}>取消</button>
        <button className="btn btn-primary" onClick={start}>生成草稿</button>
      </>}>
      {!begun ? (
        <div className="p-5 space-y-4">
          <p className="text-[14px] text-muted">
            AI 会参考目标「{plan.goal}」、你写的进度{plan.deadline ? `、截止日 ${plan.deadline}` : ""}{plan.daily_minutes ? `、每天 ${plan.daily_minutes} 分钟` : ""}，以及下面勾选的笔记标题。
            {!plan.deadline && !plan.daily_minutes && "因为没设时间，任务只排先后顺序，不会编日期。"}
          </p>
          <div>
            <span className="label">参考笔记（只发送标题和开头一小段）</span>
            <div className="max-h-[40vh] overflow-y-auto border border-rule rounded-xl divide-y divide-rule">
              {notes?.map((n) => (
                <label key={n.id} className="flex items-center gap-3 px-3 py-2 text-[13.5px] cursor-pointer row-hover">
                  <input type="checkbox" className="accent-[var(--accent)]" checked={scope.has(n.id)}
                    onChange={(e) => setScope((s) => { const x = new Set(s); if (e.target.checked) x.add(n.id); else x.delete(n.id); return x; })} />
                  {n.title}
                </label>
              ))}
              {notes?.length === 0 && <p className="p-3 text-[13px] text-muted">这个学科还没有笔记，也可以直接生成。</p>}
            </div>
          </div>
        </div>
      ) : (
        <div className="p-5 space-y-4 min-h-[44vh]">
          {ai.state.running && <AiProgress state={ai.state} title="正在根据你的目标和时间拆分任务" />}
          {ai.state.error && <p className="rounded-xl px-4 py-3 bg-bad-soft text-[13px] text-bad">{ai.state.error.message}</p>}
          {result?.result_type === "needs_clarification" && (
            <div className="rounded-xl px-4 py-3 bg-warn-soft text-[13.5px]">
              <p className="font-medium mb-1">AI 需要先弄清这些问题（可以把答案写进计划的“进度”后重新生成）：</p>
              <ul className="list-disc pl-5">{result.questions.map((q) => <li key={q}>{q}</li>)}</ul>
            </div>
          )}
          {ai.state.draft && <Warnings items={[...ai.state.draft.warnings, ...(result?.assumptions || []).map((a) => `AI 的假设：${a}`)]} />}
          {general.map((p) => <p key={p.message} className="text-[13px] text-bad">{p.message}</p>)}
          {tasks.map((t, i) => (
            <div key={i} className="flex items-start gap-2">
              <div className="flex-1 grid grid-cols-[1fr_88px_140px_92px] gap-2">
                <input className="input" value={t.title} onChange={(e) => edit(i, { title: e.target.value })} aria-label="任务" />
                <select className="input" value={t.kind} onChange={(e) => edit(i, { kind: e.target.value })} aria-label="类型">
                  {Object.entries(TASK_KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
                <input type="date" className="input" value={t.due_date || ""} onChange={(e) => edit(i, { due_date: e.target.value || null })} aria-label="日期" />
                <input type="number" className="input" min={1} max={1440} value={t.estimated_minutes ?? ""} placeholder="分钟"
                  onChange={(e) => edit(i, { estimated_minutes: e.target.value ? Number(e.target.value) : null })} aria-label="预计分钟" />
                {problemFor(i).map((p) => <p key={p.message} className="col-span-4 text-[12px] text-bad -mt-1">{p.message}</p>)}
              </div>
              <button className="btn btn-icon text-muted" onClick={() => { setTasks((xs) => xs.filter((_, j) => j !== i)); setProblems([]); }} aria-label="删除"><X size={14} /></button>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}

