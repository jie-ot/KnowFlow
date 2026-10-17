"use client";

import { AlertTriangle, RotateCcw, Sparkles, Trash2 } from "lucide-react";
import { ReactNode, useEffect, useState } from "react";
import { api, ApiError, Draft } from "@/lib/api";
import { AiState } from "./ai";
import { Markdown } from "./Markdown";
import { Modal, Spinner, useToast } from "./ui";

function Elapsed({ since }: { since: number | null }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, []);
  if (!since) return null;
  return <span className="tabular-nums">{((now - since) / 1000).toFixed(0)} 秒</span>;
}

export function AiStatusLine({ state }: { state: AiState }) {
  if (state.running)
    return (
      <span className="flex items-center gap-2 text-[13px] text-muted">
        <Spinner size={12} /> 生成中，已用 <Elapsed since={state.startedAt} />
        {state.model && <span className="text-faint">（{state.model}）</span>}
      </span>
    );
  if (state.error) return <span className="text-[13px] text-bad">{state.error.message}</span>;
  if (state.draft) return <span className="text-[13px] text-muted">已生成，确认后才会写入</span>;
  return null;
}

export function Warnings({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="rounded-xl px-4 py-3 bg-warn-soft text-[13px] leading-relaxed space-y-1">
      <p className="flex items-center gap-1.5 font-medium text-warn"><AlertTriangle size={14} /> 请核对</p>
      {items.map((w, i) => <p key={i}>{w}</p>)}
    </div>
  );
}

function ErrorBox({ state, onRetry }: { state: AiState; onRetry: () => void }) {
  if (!state.error) return null;
  return (
    <div className="rounded-xl px-4 py-3 bg-bad-soft text-[13px] leading-relaxed">
      <p className="font-medium text-bad">{state.error.message}</p>
      {state.error.detail && <p className="mt-1 text-muted break-all line-clamp-3">服务商返回：{state.error.detail}</p>}
      <p className="mt-1 text-muted">原笔记没有被改动。{state.error.retryable ? "" : "修改配置后再试。"}</p>
      <button className="btn btn-outline mt-2" onClick={onRetry}><RotateCcw size={13} /> 重试</button>
    </div>
  );
}

/** AI 整理 / 总结的对比视图（我的 UI-04）：原文 | AI 结果（可编辑）| 风险提示。 */
export function CompareSheet({
  open, kind, original, state, onClose, onRetry, onAccepted,
}: {
  open: boolean; kind: "organize" | "summarize"; original: string; state: AiState;
  onClose: () => void; onRetry: () => void; onAccepted: () => void;
}) {
  const [edit, setEdit] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  useEffect(() => { if (open) setEdit(null); }, [open, state.draft?.id]);

  const result = edit ?? (state.draft?.output || state.text);
  const accept = async () => {
    if (!state.draft) return;
    setSaving(true);
    try {
      await api(`/api/ai/drafts/${state.draft.id}/accept`, { method: "POST", json: { content: result } });
      toast(kind === "organize" ? "已用整理结果替换笔记，可在历史里撤销" : "摘要已加到笔记开头");
      onAccepted();
    } catch (e) {
      toast((e as ApiError).message, "bad");
    } finally {
      setSaving(false);
    }
  };
  const discard = async () => {
    if (state.draft) await api(`/api/ai/drafts/${state.draft.id}/discard`, { method: "POST" }).catch(() => {});
    onClose();
  };

  return (
    <Modal open={open} onClose={discard} wide
      title={<span className="flex items-center gap-2"><Sparkles size={15} className="text-accent" />{kind === "organize" ? "AI 整理" : "AI 摘要"}</span>}
      footer={<>
        <span className="mr-auto"><AiStatusLine state={state} /></span>
        <button className="btn btn-outline" onClick={discard}>放弃</button>
        <button className="btn btn-primary" disabled={!state.draft || saving} onClick={accept}>
          {edit !== null && edit !== state.draft?.output ? "修改后采纳" : "采纳"}
        </button>
      </>}>
      <div className="grid grid-cols-2 gap-0 h-[66vh] max-[900px]:grid-cols-1">
        <div className="overflow-y-auto px-6 py-5 border-r border-rule bg-canvas/50">
          <p className="text-[12px] font-medium text-muted mb-3">原文</p>
          <Markdown text={original} className="text-[14px]" />
        </div>
        <div className="flex flex-col min-h-0">
          <div className="flex items-center justify-between px-6 pt-5 pb-3">
            <p className="text-[12px] font-medium text-muted">{kind === "organize" ? "整理结果" : "摘要（会以引用块插在笔记开头）"}</p>
            {state.draft && (
              <div className="seg">
                <button data-on={edit === null} onClick={() => setEdit(null)}>预览</button>
                <button data-on={edit !== null} onClick={() => setEdit(result)}>编辑</button>
              </div>
            )}
          </div>
          <div className="flex-1 overflow-y-auto px-6 pb-5 space-y-4">
            <ErrorBox state={state} onRetry={onRetry} />
            {edit !== null ? (
              <textarea className="input font-mono text-[13px] h-full min-h-[50vh]" value={edit} onChange={(e) => setEdit(e.target.value)} />
            ) : result ? (
              <div className={state.running ? "typing" : ""}>
                <Markdown text={result} className="text-[14px]" />
              </div>
            ) : (
              state.running && <p className="text-[14px] text-muted">正在等待模型返回第一个字…</p>
            )}
            {state.draft && <Warnings items={state.draft.warnings} />}
          </div>
        </div>
      </div>
    </Modal>
  );
}

/** 知识点审核页（我的 UI-05）：逐条编辑或删除，确认后加入卡组。 */
export function ExtractSheet({
  open, state, onClose, onRetry, onAccepted,
}: { open: boolean; state: AiState; onClose: () => void; onRetry: () => void; onAccepted: (n: number) => void }) {
  const [items, setItems] = useState<{ front: string; back: string }[]>([]);
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  useEffect(() => {
    setItems((state.draft?.result?.items as { front: string; back: string }[]) || []);
  }, [state.draft]);

  const invalid = items.some((i) => !i.front.trim() || !i.back.trim() || i.front.length > 200 || i.back.length > 2000);
  const accept = async () => {
    if (!state.draft) return;
    setSaving(true);
    try {
      const r = await api<{ created_cards: number }>(`/api/ai/drafts/${state.draft.id}/accept`, { method: "POST", json: { items } });
      onAccepted(r.created_cards);
    } catch (e) {
      toast((e as ApiError).message, "bad");
    } finally {
      setSaving(false);
    }
  };
  const discard = async () => {
    if (state.draft) await api(`/api/ai/drafts/${state.draft.id}/discard`, { method: "POST" }).catch(() => {});
    onClose();
  };

  return (
    <Modal open={open} onClose={discard} width={760}
      title={<span className="flex items-center gap-2"><Sparkles size={15} className="text-accent" />提炼知识点</span>}
      footer={<>
        <span className="mr-auto"><AiStatusLine state={state} /></span>
        <button className="btn btn-outline" onClick={discard}>放弃</button>
        <button className="btn btn-primary" disabled={!state.draft || saving || invalid || !items.length} onClick={accept}>
          加入卡组（{items.length} 张）
        </button>
      </>}>
      <div className="p-5 space-y-3 min-h-[40vh]">
        <ErrorBox state={state} onRetry={onRetry} />
        {state.running && <p className="text-[14px] text-muted py-10 text-center">正在从笔记中挑出值得记住的知识点…</p>}
        {state.draft && <Warnings items={state.draft.warnings} />}
        {items.map((item, i) => (
          <div key={i} className="index-card px-5 pt-7 pb-4 group">
            <div className="flex gap-3">
              <span className="text-[12px] text-faint tabular-nums pt-2 w-5">{i + 1}</span>
              <div className="flex-1 space-y-2">
                <input className="input font-serif text-[15px]" value={item.front} placeholder="正面：问题或关键词"
                  style={!item.front.trim() ? { borderColor: "var(--bad)" } : undefined}
                  onChange={(e) => setItems((xs) => xs.map((x, j) => (j === i ? { ...x, front: e.target.value } : x)))} />
                <textarea className="input text-[13.5px]" rows={3} value={item.back} placeholder="背面：要点内容"
                  style={!item.back.trim() ? { borderColor: "var(--bad)" } : undefined}
                  onChange={(e) => setItems((xs) => xs.map((x, j) => (j === i ? { ...x, back: e.target.value } : x)))} />
              </div>
              <button className="btn btn-icon text-muted opacity-50 group-hover:opacity-100" aria-label="删除这张"
                onClick={() => setItems((xs) => xs.filter((_, j) => j !== i))}>
                <Trash2 size={15} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}

export function SheetSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <p className="text-[12px] font-medium text-muted mb-2">{title}</p>
      {children}
    </div>
  );
}

export type { Draft };
