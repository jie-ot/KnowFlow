"use client";

import { ArrowLeft, ExternalLink, Pencil, RotateCcw, Sparkles, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { API, api, ApiError, Course, fetcher, Material, Note, Unit } from "@/lib/api";
import { sizeLabel } from "@/lib/format";
import { useAiTask } from "@/components/ai";
import { AiProgress, AiStatusLine, useStopGuard, Warnings } from "@/components/AiSheets";
import { StatusPill } from "@/components/MaterialBits";
import { Markdown } from "@/components/Markdown";
import { Modal, useConfirm, useToast } from "@/components/ui";

export default function MaterialPage() {
  return (
    <Suspense>
      <MaterialDetail />
    </Suspense>
  );
}

function MaterialDetail() {
  const params = useSearchParams();
  const id = params.get("id") || "";
  const focus = params.get("unit");
  const { data: m, mutate } = useSWR<Material>(`/api/materials/${id}`, (p: string) => api<Material>(p), {
    refreshInterval: (d) => (d && (d.status === "extracting" || d.status === "uploaded") ? 1000 : 0),
  });
  const { data: ai } = useSWR<{ max_input_chars: number }>("/api/ai/status", fetcher);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [lastClicked, setLastClicked] = useState<number | null>(null);
  const [composing, setComposing] = useState(false);
  const [editingUnit, setEditingUnit] = useState<Unit | null>(null);
  const toast = useToast();
  const confirm = useConfirm();
  const router = useRouter();
  const scrolled = useRef(false);

  useEffect(() => {
    if (focus && m?.units && !scrolled.current) {
      scrolled.current = true;
      setTimeout(() => document.getElementById(`u-${focus}`)?.scrollIntoView({ block: "center" }), 60);
    }
  }, [focus, m]);

  if (!m) return <div className="p-10 text-muted text-[14px]">正在打开…</div>;
  const units = m.units || [];
  const chars = units.filter((u) => picked.has(u.id)).reduce((s, u) => s + u.char_count, 0);
  const limit = (ai?.max_input_chars || 16000) * 4; // 资料整理会分批进行，可以一次选得比单次上限多

  const toggle = (u: Unit, i: number, shift: boolean) => {
    setPicked((s) => {
      const x = new Set(s);
      if (shift && lastClicked !== null) {
        const [a, b] = [Math.min(lastClicked, i), Math.max(lastClicked, i)];
        units.slice(a, b + 1).forEach((v) => v.text.trim() && x.add(v.id));
      } else if (x.has(u.id)) x.delete(u.id);
      else x.add(u.id);
      return x;
    });
    setLastClicked(i);
  };

  const remove = async () => {
    const ok = await confirm({
      title: "删除这份资料？",
      body: `原件和解析出的片段都会删除。${m.used_by?.length ? `引用它的 ${m.used_by.length} 篇笔记会保留，但出处会显示为“原件已删除”。` : ""}`,
      confirm: "删除", danger: true,
    });
    if (!ok) return;
    await api(`/api/materials/${id}`, { method: "DELETE" });
    globalMutate("/api/courses");
    router.push("/materials");
  };

  return (
    <div className="max-w-[920px] mx-auto px-10 py-10 pb-32">
      <Link href="/materials" className="inline-flex items-center gap-1 text-[13px] text-muted hover:text-ink mb-5"><ArrowLeft size={14} />资料</Link>
      <div className="flex items-start gap-4 mb-2">
        <h1 className="flex-1 text-[24px] font-semibold tracking-tight">{m.display_title}</h1>
        <a className="btn btn-outline" href={`${API}/api/materials/${id}/file`} target="_blank" rel="noreferrer"><ExternalLink size={14} />打开原件</a>
        {m.status === "failed" && <button className="btn btn-outline" onClick={async () => { await api(`/api/materials/${id}/retry`, { method: "POST" }); mutate(); }}><RotateCcw size={14} />重新解析</button>}
        <button className="btn btn-icon text-muted" onClick={remove} aria-label="删除资料"><Trash2 size={16} /></button>
      </div>
      <div className="flex items-center gap-3 text-[13px] text-muted mb-2">
        <StatusPill status={m.status} />
        <span>{m.original_name}，{sizeLabel(m.size_bytes)}，{m.unit_count} 个片段</span>
      </div>
      {m.error_message && <p className="text-[13px] text-warn mb-2">{m.error_message}</p>}
      {m.used_by && m.used_by.length > 0 && (
        <p className="text-[13px] text-muted">
          被这些笔记引用：{m.used_by.map((n, i) => <span key={n.id}>{i > 0 && "、"}<Link className="text-accent" href={`/notes?id=${n.id}`}>{n.title}</Link></span>)}
        </p>
      )}

      <div className="flex items-center gap-3 mt-8 mb-3">
        <p className="section-title mb-0 flex-1">内容片段（勾选后让 AI 整理，按住 Shift 可连选）</p>
        <button className="btn text-muted" onClick={() => setPicked(new Set(units.filter((u) => u.text.trim()).map((u) => u.id)))}>全选</button>
        {picked.size > 0 && <button className="btn text-muted" onClick={() => setPicked(new Set())}>清除</button>}
      </div>

      <div className="space-y-2">
        {units.map((u, i) => {
          const on = picked.has(u.id);
          return (
            <div key={u.id} id={`u-${u.id}`}
              className="group flex gap-4 rounded-xl px-4 py-3 border transition-colors"
              style={{
                borderColor: on ? "var(--accent)" : focus === u.id ? "var(--warn)" : "var(--rule)",
                background: on ? "var(--accent-soft)" : focus === u.id ? "var(--warn-soft)" : "transparent",
              }}>
              <input type="checkbox" className="mt-1 accent-[var(--accent)]" checked={on} disabled={!u.text.trim()}
                aria-label={`选择${u.label}`} onChange={() => {}} onClick={(e) => toggle(u, i, e.shiftKey)} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[12px] font-medium text-muted">{u.label}</span>
                  <span className="text-[11px] text-faint">{u.char_count} 字</span>
                  <span className="flex-1" />
                  <button className="opacity-0 group-hover:opacity-100 text-[12px] text-muted hover:text-ink flex items-center gap-1" onClick={() => setEditingUnit(u)}>
                    <Pencil size={11} />{u.text.trim() ? "校对" : "补充文字"}
                  </button>
                </div>
                {u.text.trim() ? (
                  <p className={`text-[13.5px] leading-relaxed whitespace-pre-wrap ${m.format === "code" ? "font-mono text-[12.5px]" : ""} line-clamp-[12]`}>{u.text}</p>
                ) : (
                  <p className="text-[13px] text-faint">{u.warning_text.join("；") || "没有文字"}</p>
                )}
                {u.text.trim() && u.warning_text.length > 0 && <p className="text-[12px] text-warn mt-1">{u.warning_text.join("；")}</p>}
              </div>
            </div>
          );
        })}
      </div>

      {picked.size > 0 && (
        <div className="anim-sheet fixed bottom-6 left-[calc(50%+116px)] -translate-x-1/2 z-20 flex items-center gap-4 pl-5 pr-2 h-14 rounded-2xl bg-paper border border-rule shadow-2xl">
          <span className="text-[13.5px]">已选 {picked.size} 段，共 <b className={chars > limit ? "text-bad" : ""}>{chars.toLocaleString()}</b> 字</span>
          {chars > limit && <span className="text-[12px] text-bad">超过 {limit} 字上限，请少选一些</span>}
          <button className="btn btn-primary h-10 px-4" disabled={chars > limit} onClick={() => setComposing(true)}><Sparkles size={14} />整理成笔记</button>
        </div>
      )}

      <ComposeSheet open={composing} unitIds={[...picked]} defaultCourse={m?.course_id} onClose={() => setComposing(false)}
        onSaved={(note) => { setComposing(false); toast("已保存为笔记"); globalMutate("/api/courses"); router.push(`/notes?id=${note.id}`); }} />
      <UnitEditor unit={editingUnit} materialId={id} onClose={() => setEditingUnit(null)} onSaved={() => mutate()} />
    </div>
  );
}

function ComposeSheet({ open, unitIds, defaultCourse, onClose, onSaved }: {
  open: boolean; unitIds: string[]; defaultCourse?: string; onClose: () => void; onSaved: (n: Note) => void;
}) {
  const ai = useAiTask();
  const { data: courses } = useSWR<Course[]>(open ? "/api/courses" : null, fetcher);
  const [courseId, setCourseId] = useState("");
  useEffect(() => { if (open) setCourseId(defaultCourse || ""); }, [open, defaultCourse]);
  const [instruction, setInstruction] = useState("");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  const result = ai.state.draft?.result as { title: string; content: string } | undefined;

  useEffect(() => {
    if (!open) { ai.reset(); setContent(null); setTitle(""); }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (result) setTitle(result.title); }, [result]);

  const start = () => ai.run({ kind: "compose", unit_ids: unitIds, instruction });
  const save = async () => {
    if (!ai.state.draft) return;
    setSaving(true);
    try {
      const r = await api<{ note: Note }>(`/api/ai/drafts/${ai.state.draft.id}/accept`, {
        method: "POST", json: { title, content: content ?? result?.content, course_id: courseId || null },
      });
      onSaved(r.note);
    } catch (e) {
      toast((e as ApiError).message, "bad");
    } finally {
      setSaving(false);
    }
  };
  const guard = useStopGuard();
  const discard = () => guard(ai.state, async () => {
    ai.cancel();
    if (ai.state.draft) await api(`/api/ai/drafts/${ai.state.draft.id}/discard`, { method: "POST" }).catch(() => {});
    onClose();
  });
  const begun = ai.state.running || ai.state.draft || ai.state.error;

  return (
    <Modal open={open} onClose={discard} width={820}
      title={<span className="flex items-center gap-2"><Sparkles size={15} className="text-accent" />把资料整理成笔记</span>}
      footer={begun ? <>
        <span className="mr-auto"><AiStatusLine state={ai.state} /></span>
        <button className="btn btn-outline" onClick={discard}>{ai.state.running ? "停止整理" : "放弃"}</button>
        {ai.state.error && <button className="btn btn-outline" onClick={start}>重试</button>}
        <button className="btn btn-primary" disabled={!ai.state.draft || saving || !title.trim()} onClick={save}>保存为笔记</button>
      </> : <>
        <button className="btn btn-outline" onClick={onClose}>取消</button>
        <button className="btn btn-primary" onClick={start}>开始整理</button>
      </>}>
      {!begun ? (
        <div className="p-5 space-y-3">
          <p className="text-[14px] text-muted">将发送选中的 {unitIds.length} 个片段。每一节都会标注出处，点出处可以回到原文对应的页、段或行。</p>
          <label className="label" htmlFor="compose-instruction">整理要求（可不填）</label>
          <textarea id="compose-instruction" className="input" rows={3} maxLength={500} value={instruction}
            placeholder="例如：按考点整理，重点写清定义和成立条件" onChange={(e) => setInstruction(e.target.value)} />
        </div>
      ) : (
        <div className="p-5 space-y-4 min-h-[50vh]">
          {ai.state.running && (
            <AiProgress state={ai.state} title="正在阅读资料并整理成笔记"
              hint={`共 ${unitIds.length} 个片段。资料多时会分成几部分依次整理，每部分都标好出处，最后合成一篇笔记。整理只在这个窗口开着时进行：关闭窗口或点“停止整理”都会停止，不会在后台继续。`} />
          )}
          {ai.state.error && (
            <div className="rounded-xl px-4 py-3 bg-bad-soft text-[13px]">
              <p className="font-medium text-bad">{ai.state.error.message}</p>
              {ai.state.error.detail && <p className="text-muted mt-1 line-clamp-3 break-all">{ai.state.error.detail}</p>}
            </div>
          )}
          {result && (
            <>
              <Warnings items={ai.state.draft!.warnings} />
              <input className="w-full bg-transparent outline-none font-serif text-[26px]" value={title} onChange={(e) => setTitle(e.target.value)} aria-label="笔记标题" />
              <label className="flex items-center gap-2 text-[13px] text-muted">
                保存到学科
                <select className="input h-8 w-auto min-w-[180px] text-[13px]" value={courseId} onChange={(e) => setCourseId(e.target.value)}>
                  {courses?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </label>
              <div className="flex justify-end">
                <div className="seg">
                  <button data-on={content === null} onClick={() => setContent(null)}>预览</button>
                  <button data-on={content !== null} onClick={() => setContent(content ?? result.content)}>编辑</button>
                </div>
              </div>
              {content !== null ? (
                <textarea className="input font-mono text-[13px] min-h-[46vh]" value={content} onChange={(e) => setContent(e.target.value)} />
              ) : (
                <Markdown text={result.content} className="text-[14px]" />
              )}
            </>
          )}
        </div>
      )}
    </Modal>
  );
}

function UnitEditor({ unit, materialId, onClose, onSaved }: { unit: Unit | null; materialId: string; onClose: () => void; onSaved: () => void }) {
  const [text, setText] = useState("");
  useEffect(() => { if (unit) setText(unit.text); }, [unit]);
  const save = async () => {
    await api(`/api/materials/${materialId}/units/${unit!.id}`, { method: "PATCH", json: { text } });
    onSaved();
    onClose();
  };
  return (
    <Modal open={!!unit} onClose={onClose} title={unit ? `${unit.text.trim() ? "校对" : "补充"}：${unit.label}` : ""} width={640}
      footer={<><button className="btn btn-outline" onClick={onClose}>取消</button><button className="btn btn-primary" onClick={save}>保存</button></>}>
      <div className="p-5">
        <p className="text-[13px] text-muted mb-3">保存后这段会标记为“你手动补充或修改”，AI 整理时按你写的内容处理。</p>
        <textarea className="input text-[13.5px]" rows={14} value={text} onChange={(e) => setText(e.target.value)} />
      </div>
    </Modal>
  );
}
