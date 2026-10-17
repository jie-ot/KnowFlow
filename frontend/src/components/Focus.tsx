"use client";

import { Check, Pause, Play, Square, Timer, X } from "lucide-react";
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { mutate as globalMutate } from "swr";
import { api, ApiError, Task } from "@/lib/api";
import { useToast } from "./ui";

/** 番茄钟：同一时间只计一个任务；状态存在本机，刷新或切页面都不会丢。 */
type Session = {
  taskId: string; planId: string; title: string;
  startedAt: string;      // ISO，开始时间
  durationMin: number;    // 这一轮的目标分钟数
  runMs: number;          // 暂停前已累计的毫秒
  resumedAt: number | null; // 正在计时时，最近一次开始 / 继续的时间戳；暂停时为 null
};

const KEY = "kf-focus";
const PRESETS = [25, 45, 15];

type Ctx = { session: Session | null; start: (t: Pick<Task, "id" | "plan_id" | "title">, minutes?: number) => void };
const FocusCtx = createContext<Ctx>({ session: null, start: () => {} });
export const useFocus = () => useContext(FocusCtx);

const elapsedMs = (s: Session, now = Date.now()) => s.runMs + (s.resumedAt ? now - s.resumedAt : 0);

function beep() {
  try {
    const ctx = new AudioContext();
    [0, 0.25, 0.5].forEach((t) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.frequency.value = 880;
      g.gain.setValueAtTime(0.15, ctx.currentTime + t);
      g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + t + 0.2);
      o.connect(g).connect(ctx.destination);
      o.start(ctx.currentTime + t);
      o.stop(ctx.currentTime + t + 0.2);
    });
  } catch {}
}

export function FocusProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [, tick] = useState(0);
  const [ringing, setRinging] = useState(false);
  const toast = useToast();
  const loaded = useRef(false);

  useEffect(() => {
    try { const raw = localStorage.getItem(KEY); if (raw) setSession(JSON.parse(raw)); } catch {}
    loaded.current = true;
  }, []);
  useEffect(() => {
    if (!loaded.current) return;
    try { if (session) localStorage.setItem(KEY, JSON.stringify(session)); else localStorage.removeItem(KEY); } catch {}
  }, [session]);

  // 每秒刷新显示；到点响铃一次
  useEffect(() => {
    if (!session?.resumedAt) return;
    const t = setInterval(() => {
      tick((n) => n + 1);
      if (!ringing && elapsedMs(session) >= session.durationMin * 60000) {
        setRinging(true);
        beep();
        document.title = "⏰ 时间到 · KnowFlow";
      }
    }, 1000);
    return () => clearInterval(t);
  }, [session, ringing]);

  const start = useCallback((t: Pick<Task, "id" | "plan_id" | "title">, minutes = 25) => {
    if (session && session.taskId !== t.id) {
      toast(`先结束正在专注的「${session.title}」`, "bad");
      return;
    }
    setRinging(false);
    setSession({ taskId: t.id, planId: t.plan_id, title: t.title, startedAt: new Date().toISOString(),
      durationMin: minutes, runMs: 0, resumedAt: Date.now() });
  }, [session, toast]);

  const finish = async (done: boolean) => {
    if (!session) return;
    const minutes = Math.max(1, Math.round(elapsedMs(session) / 60000));
    try {
      await api(`/api/tasks/${session.taskId}/focus`, { method: "POST", json: { minutes, started_at: session.startedAt, done } });
      toast(done ? `「${session.title}」完成了，这次专注 ${minutes} 分钟` : `已记录专注 ${minutes} 分钟`);
    } catch (e) {
      toast((e as ApiError).message, "bad");
    }
    globalMutate("/api/tasks/today");
    globalMutate(`/api/plans/${session.planId}`);
    globalMutate((k) => typeof k === "string" && (k.startsWith("/api/calendar") || k === "/api/focus/today"));
    setSession(null);
    setRinging(false);
    document.title = "KnowFlow";
  };

  const pause = () => session && setSession({ ...session, runMs: elapsedMs(session), resumedAt: null });
  const resume = () => session && setSession({ ...session, resumedAt: Date.now() });
  const extend = (min: number) => {
    if (!session) return;
    setSession({ ...session, durationMin: Math.ceil(elapsedMs(session) / 60000) + min, resumedAt: session.resumedAt ?? Date.now() });
    setRinging(false);
    document.title = "KnowFlow";
  };

  return (
    <FocusCtx.Provider value={{ session, start }}>
      {children}
      {session && (
        <FocusPill session={session} ringing={ringing} onPause={pause} onResume={resume} onExtend={extend}
          onFinish={finish} onDiscard={() => { setSession(null); setRinging(false); document.title = "KnowFlow"; }}
          onPreset={(m) => setSession({ ...session, durationMin: m })} />
      )}
    </FocusCtx.Provider>
  );
}

function FocusPill({ session, ringing, onPause, onResume, onExtend, onFinish, onDiscard, onPreset }: {
  session: Session; ringing: boolean; onPause: () => void; onResume: () => void; onExtend: (m: number) => void;
  onFinish: (done: boolean) => void; onDiscard: () => void; onPreset: (m: number) => void;
}) {
  const ms = elapsedMs(session);
  const total = session.durationMin * 60000;
  const left = Math.max(0, total - ms);
  const pct = Math.min(1, ms / total);
  const mm = String(Math.floor(left / 60000)).padStart(2, "0");
  const ss = String(Math.floor((left % 60000) / 1000)).padStart(2, "0");
  const running = !!session.resumedAt;
  const r = 15;
  const c = 2 * Math.PI * r;
  return (
    <div className={`fixed left-[248px] bottom-4 z-30 flex items-center gap-3 pl-2 pr-2 py-2 rounded-2xl border bg-paper shadow-xl anim-sheet
      ${ringing ? "border-accent shadow-[0_0_0_4px_var(--accent-soft)]" : "border-rule"}`} role="timer" aria-label="番茄钟">
      <svg width="38" height="38" viewBox="0 0 38 38" className="shrink-0 -rotate-90">
        <circle cx="19" cy="19" r={r} fill="none" stroke="var(--rule)" strokeWidth="3" />
        <circle cx="19" cy="19" r={r} fill="none" stroke="var(--accent)" strokeWidth="3" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - pct)} style={{ transition: "stroke-dashoffset 1s linear" }} />
      </svg>
      <div className="min-w-0 max-w-[220px]">
        <p className="text-[11.5px] text-muted flex items-center gap-1"><Timer size={11} />{ringing ? "时间到！" : running ? "专注中" : "已暂停"}</p>
        <p className="text-[13px] font-medium truncate">{session.title}</p>
      </div>
      <span className="font-serif text-[22px] tabular-nums w-[68px] text-center">{ringing ? "00:00" : `${mm}:${ss}`}</span>
      {ms < 60000 && !ringing && (
        <span className="flex gap-0.5">
          {PRESETS.map((m) => (
            <button key={m} onClick={() => onPreset(m)} className={`h-6 px-1.5 rounded-md text-[11px] ${session.durationMin === m ? "bg-accent-soft text-accent" : "text-muted hover:bg-hover"}`}>{m}</button>
          ))}
        </span>
      )}
      {ringing ? (
        <>
          <button className="btn btn-primary h-8" onClick={() => onFinish(true)}><Check size={14} />完成任务</button>
          <button className="btn h-8" onClick={() => onExtend(5)}>再 5 分钟</button>
          <button className="btn h-8 text-muted" onClick={() => onFinish(false)}>只记录</button>
        </>
      ) : (
        <>
          {running
            ? <button className="btn btn-icon h-8 w-8" onClick={onPause} title="暂停"><Pause size={15} /></button>
            : <button className="btn btn-icon h-8 w-8" onClick={onResume} title="继续"><Play size={15} /></button>}
          <button className="btn btn-icon h-8 w-8 text-accent" onClick={() => onFinish(true)} title="完成任务并记录用时"><Check size={16} /></button>
          <button className="btn btn-icon h-8 w-8 text-muted" onClick={() => onFinish(false)} title="结束并记录用时（任务不打勾）"><Square size={13} /></button>
          <button className="btn btn-icon h-8 w-8 text-faint" onClick={onDiscard} title="放弃这次计时（不记录）"><X size={15} /></button>
        </>
      )}
    </div>
  );
}

/** 任务行里的“开始专注”按钮 */
export function FocusButton({ task, className = "" }: { task: Task; className?: string }) {
  const { session, start } = useFocus();
  if (task.status === "done") return null;
  const active = session?.taskId === task.id;
  return (
    <button className={`btn h-7 text-[12px] ${active ? "text-accent" : "text-muted"} ${className}`} disabled={active}
      onClick={() => start(task)} title="开始一个番茄钟（默认 25 分钟）">
      <Timer size={13} />{active ? "专注中" : "专注"}
    </button>
  );
}
