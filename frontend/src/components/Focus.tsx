"use client";

import { Check, Download, Pause, Play, Square, Timer, X } from "lucide-react";
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { mutate as globalMutate } from "swr";
import { api, ApiError, Task } from "@/lib/api";
import { FocusSession as Session, FOCUS_KEY, MAX_FOCUS_MINUTES, clearFocusJournal, focusElapsed as elapsedMs,
  freezeFocus, newOperationId, recoverFocus, storeFocus } from "@/lib/submission";
import { useConfirm, useToast } from "./ui";

/** 番茄钟：同一时间只计一个任务；状态存在本机，刷新或切页面都不会丢。 */
const PRESETS = [25, 45, 15];

type Ctx = { session: Session | null; start: (t: Pick<Task, "id" | "plan_id" | "title">, minutes?: number) => void };
const FocusCtx = createContext<Ctx>({ session: null, start: () => {} });
export const useFocus = () => useContext(FocusCtx);

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
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [localSaved, setLocalSaved] = useState(true);
  const toast = useToast();
  const confirm = useConfirm();
  const loaded = useRef(false);
  const sessionRef = useRef<Session | null>(null);
  const busy = useRef(false);
  const owner = useRef(newOperationId());

  const updateSession = useCallback((next: Session | null) => {
    const previous = sessionRef.current;
    try {
      next = storeFocus(localStorage, next, owner.current, previous);
      setLocalSaved(true);
    } catch { setLocalSaved(false); toast("本地计时保存失败，关闭页面前请重试", "bad"); }
    sessionRef.current = next;
    setSession(next);
  }, [toast]);

  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    try {
      const restored = recoverFocus(localStorage);
      if (restored) updateSession(restored);
    } catch {}
  }, [updateSession]);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if ((event.key !== FOCUS_KEY && !event.key?.startsWith("kf-focus-pending-v1:")) || busy.current) return;
      let restored: Session | null;
      try { restored = recoverFocus(localStorage); } catch { return; }
      sessionRef.current = restored;
      setSession(restored);
      setRinging(false);
      setSubmitError(null);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

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
    const current = sessionRef.current;
    if (current) {
      toast(`先结束或同步「${current.title}」的计时`, "bad");
      return;
    }
    setRinging(false);
    setSubmitError(null);
    updateSession({ id: newOperationId(), taskId: t.id, planId: t.plan_id, title: t.title, startedAt: new Date().toISOString(),
      durationMin: minutes, runMs: 0, resumedAt: Date.now() });
  }, [toast, updateSession]);

  const finish = async (done: boolean) => {
    if (busy.current || !sessionRef.current) return;
    let frozen: Session;
    try {
      const latest = recoverFocus(localStorage);
      if (latest?.id === sessionRef.current.id) sessionRef.current = latest;
    } catch {}
    try { frozen = freezeFocus(sessionRef.current, done); }
    catch (error) {
      updateSession({ ...sessionRef.current, runMs: elapsedMs(sessionRef.current), resumedAt: null });
      setRinging(false);
      setSubmitError((error as Error).message);
      toast((error as Error).message, "bad");
      return;
    }
    busy.current = true;
    const request = frozen.submission!;
    // Persist an immutable request before contacting the backend. A failed or
    // lost response can then be retried after refresh with the exact same ID.
    updateSession(frozen);
    setRinging(false);
    setSubmitting(true);
    setSubmitError(null);
    try {
      await api(`/api/tasks/${frozen.taskId}/focus`, { method: "POST", json: request });
      toast(request.done ? `「${frozen.title}」完成了，这次专注 ${request.minutes} 分钟` : `已记录专注 ${request.minutes} 分钟`);
      globalMutate("/api/tasks/today");
      globalMutate(`/api/plans/${frozen.planId}`);
      globalMutate((k) => typeof k === "string" && (k.startsWith("/api/calendar") || k === "/api/focus/today"));
      if (sessionRef.current?.id === frozen.id) {
        try {
          const next = clearFocusJournal(localStorage, frozen);
          sessionRef.current = next;
          setSession(next);
        } catch { setLocalSaved(false); toast("记录已同步，本地副本清理失败；再次同步不会重复累计", "bad"); }
      }
      document.title = "KnowFlow";
    } catch (e) {
      const error = e as ApiError;
      const message = error.code === "IDEMPOTENCY_CONFLICT" || error.code === "FOCUS_SESSION_EXISTS"
        ? "另一窗口已经记录此会话；请核对任务累计时长后清理本地副本，不会重复累计"
        : error.message;
      setSubmitError(message);
      toast(`${message}；计时结果已保留，可重试`, "bad");
    } finally {
      busy.current = false;
      setSubmitting(false);
    }
  };

  const clearReviewed = async () => {
    const current = sessionRef.current;
    if (!current?.submission || busy.current) return;
    if (!(await confirm({ title: "核对后清除本地待同步记录？", body: "请先在任务中核对实际累计时长。这里只清除本机同一会话的待同步副本，不会删除后端已记录的专注。", confirm: "已核对，清除本地副本" }))) return;
    try {
      const next = clearFocusJournal(localStorage, current, true);
      sessionRef.current = next;
      setSession(next);
      setSubmitError(null);
    } catch { toast("清理本地副本失败，请重试", "bad"); }
  };

  const exportSession = () => {
    const current = sessionRef.current;
    if (!current) return;
    const data = JSON.stringify({ ...current, elapsed_ms: elapsedMs(current), exported_at: new Date().toISOString() }, null, 2);
    const url = URL.createObjectURL(new Blob([data], { type: "application/json" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `KnowFlow-focus-${current.id}.json`; anchor.click();
    URL.revokeObjectURL(url);
  };

  const pause = () => {
    const current = sessionRef.current;
    if (current && !current.submission && !busy.current) updateSession({ ...current, runMs: elapsedMs(current), resumedAt: null });
  };
  const resume = () => {
    const current = sessionRef.current;
    if (current && !current.submission && !busy.current) updateSession({ ...current, resumedAt: Date.now() });
  };
  const extend = (min: number) => {
    const current = sessionRef.current;
    if (!current || current.submission || busy.current) return;
    updateSession({ ...current, durationMin: Math.ceil(elapsedMs(current) / 60000) + min, resumedAt: current.resumedAt ?? Date.now() });
    setRinging(false);
    document.title = "KnowFlow";
  };

  return (
    <FocusCtx.Provider value={{ session, start }}>
      {children}
      {session && (
        <FocusPill session={session} ringing={ringing} submitting={submitting} submitError={submitError} localSaved={localSaved} onPause={pause} onResume={resume} onExtend={extend}
          onClearReviewed={clearReviewed} onExport={exportSession}
          onFinish={finish} onDiscard={() => { if (!busy.current && !sessionRef.current?.submission) { updateSession(null); setRinging(false); document.title = "KnowFlow"; } }}
          onPreset={(m) => { const current = sessionRef.current; if (current && !current.submission && !busy.current) updateSession({ ...current, durationMin: m }); }} />
      )}
    </FocusCtx.Provider>
  );
}

function FocusPill({ session, ringing, submitting, submitError, localSaved, onPause, onResume, onExtend, onFinish, onDiscard, onPreset, onClearReviewed, onExport }: {
  session: Session; ringing: boolean; submitting: boolean; submitError: string | null; localSaved: boolean; onPause: () => void; onResume: () => void; onExtend: (m: number) => void;
  onFinish: (done: boolean) => void; onDiscard: () => void; onPreset: (m: number) => void;
  onClearReviewed: () => void; onExport: () => void;
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
  const overLimit = Math.max(1, Math.round(ms / 60000)) > MAX_FOCUS_MINUTES;
  return (
    <div className={`glass-surface fixed left-[248px] bottom-4 z-30 flex items-center gap-3 pl-2 pr-2 py-2 rounded-2xl anim-sheet
      ${ringing ? "border-accent shadow-[0_0_0_4px_var(--accent-soft)]" : "border-rule"}`} role="timer" aria-label="番茄钟">
      <svg width="38" height="38" viewBox="0 0 38 38" className="shrink-0 -rotate-90">
        <circle cx="19" cy="19" r={r} fill="none" stroke="var(--rule)" strokeWidth="3" />
        <circle cx="19" cy="19" r={r} fill="none" stroke="var(--accent)" strokeWidth="3" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - pct)} style={{ transition: "stroke-dashoffset 1s linear" }} />
      </svg>
      <div className="min-w-0 max-w-[220px]">
        <p className="text-[11.5px] text-muted flex items-center gap-1"><Timer size={11} />{session.submission ? submitting ? "正在同步记录…" : "记录待同步" : ringing ? "时间到！" : running ? "专注中" : "已暂停"}</p>
        <p className="text-[13px] font-medium truncate">{session.title}</p>
      </div>
      <span className="font-mono text-[22px] tabular-nums w-[68px] text-center">{ringing ? "00:00" : `${mm}:${ss}`}</span>
      {ms < 60000 && !ringing && !session.submission && (
        <span className="flex gap-0.5">
          {PRESETS.map((m) => (
            <button key={m} onClick={() => onPreset(m)} className={`h-6 px-1.5 rounded-md text-[11px] ${session.durationMin === m ? "bg-accent-soft text-accent" : "text-muted hover:bg-hover"}`}>{m}</button>
          ))}
        </span>
      )}
      {session.submission ? (
        <div className="flex items-center gap-2" role="status">
          <span className="text-[12px] text-muted max-w-[220px]" title={submitError || undefined}>{session.submission.minutes} 分钟{localSaved ? "已保留在本机" : "暂存于当前窗口，本地保存失败"}{submitError ? "，同步失败" : ""}</span>
          <button className="btn btn-primary h-8" disabled={submitting} onClick={() => onFinish(session.submission!.done)}>{submitting ? "提交中…" : "重试同步"}</button>
          {submitError && <><a className="btn h-8 text-muted" href={`/plans?id=${session.planId}`}>核对任务</a>
            <button className="btn h-8 text-muted" disabled={submitting} onClick={onClearReviewed}>核对后清理</button></>}
        </div>
      ) : ringing ? (
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
          <button className="btn btn-icon h-8 w-8 text-accent" onClick={() => onFinish(true)} title={overLimit ? "超过单次 31 天上限，请先导出核对" : "完成任务并记录用时"}><Check size={16} /></button>
          <button className="btn btn-icon h-8 w-8 text-muted" onClick={() => onFinish(false)} title="结束并记录用时（任务不打勾）"><Square size={13} /></button>
          {overLimit && <button className="btn h-8" onClick={onExport}><Download size={13} />超出 31 天，导出计时</button>}
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
