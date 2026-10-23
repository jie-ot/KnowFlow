"use client";

import { BookOpen, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, Course, Draft, Note, NoteSummary } from "@/lib/api";
import { useAiTask } from "./ai";
import { AiProgress, AiStatusLine, useStopGuard, Warnings } from "./AiSheets";
import { Modal, Spinner, useConfirm, useToast } from "./ui";

type Section = { heading: string; body: string; source_note_ids: string[] };
type SourceNote = { id: string; title: string; version: number };
type Page = { items: NoteSummary[]; total: number; has_more: boolean };

// A status check identifies the saved draft, but cannot prove which window's edits won.
const alreadyAcceptedMessage = (recovered: boolean) => recovered
  ? "这份草稿已被采纳，未重复写入；由于可能来自另一窗口，请核对已保存结果，当前窗口的后续修改未写入"
  : "此草稿此前已被采纳，本窗口的后续修改没有写入";

/** Choose independently of the note sidebar's page; generation never changes source notes. */
export function OutlineSheet({ open, courses, defaultCourseId, beforeGenerate, onClose, onAccepted }: {
  open: boolean; courses: Course[]; defaultCourseId?: string | null;
  beforeGenerate: (ids: string[]) => Promise<boolean>;
  onClose: () => void; onAccepted: (note: Note) => void;
}) {
  const ai = useAiTask();
  const toast = useToast();
  const confirm = useConfirm();
  const guard = useStopGuard();
  const [selected, setSelected] = useState<Map<string, NoteSummary>>(new Map());
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0);
  const [instruction, setInstruction] = useState("");
  const [courseId, setCourseId] = useState("");
  const [title, setTitle] = useState("");
  const [sections, setSections] = useState<Section[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [preparing, setPreparing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const pendingAccept = useRef<{ title: string; course_id: string; outline_sections: { heading: string; body: string }[] } | null>(null);
  const generating = useRef(false);
  const preparingRef = useRef(false);
  const accepting = useRef(false);

  useEffect(() => {
    if (!open) return;
    ai.reset();
    setSelected(new Map()); setQuery(""); setSearch(""); setOffset(0);
    setFilter(defaultCourseId || ""); setCourseId(defaultCourseId || courses[0]?.id || "");
    setInstruction(""); setTitle(""); setSections([]); setWarnings([]);
    setUncertain(false); pendingAccept.current = null;
    // Courses can be refreshed while the sheet is open without losing selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    const timer = setTimeout(() => { setSearch(query); setOffset(0); }, 250);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!open || ai.state.draft) return;
    let active = true;
    const controller = new AbortController();
    setLoading(true); setLoadError("");
    const params = new URLSearchParams({ q: search, offset: String(offset), limit: "20" });
    if (filter) params.set("course_id", filter);
    api<Page>(`/api/notes/page?${params}`, { signal: controller.signal }).then((data) => {
      if (active) setPage(data);
    }).catch((error: ApiError) => { if (active) setLoadError(error.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [open, search, offset, filter, reload, ai.state.draft]);

  useEffect(() => {
    const draft = ai.state.draft;
    if (!draft) return;
    setTitle(draft.result?.title || "复习提纲");
    setSections(draft.result?.sections || []);
    setWarnings(draft.warnings || []);
  }, [ai.state.draft]);

  const toggle = (note: NoteSummary) => setSelected((current) => {
    const next = new Map(current);
    if (next.has(note.id)) next.delete(note.id);
    else if (next.size < 30) next.set(note.id, note);
    return next;
  });

  const generate = async () => {
    if (generating.current || accepting.current || selected.size < 2 || selected.size > 30) return;
    generating.current = true; preparingRef.current = true; setPreparing(true);
    const ids = Array.from(selected.keys());
    try {
      if (!await beforeGenerate(ids)) return;
      preparingRef.current = false; setPreparing(false);
      await ai.run({ kind: "outline", scope_note_ids: ids, instruction });
    } catch (error) {
      toast((error as Error).message || "暂时无法生成，请重试", "bad");
    } finally {
      preparingRef.current = false; generating.current = false; setPreparing(false);
    }
  };

  const close = () => {
    if (preparingRef.current || accepting.current) { toast("正在保存，请稍候"); return; }
    void guard(ai.state, async () => {
      const draft = ai.state.draft;
      const edited = draft && (title !== draft.result?.title || sections.some((section, i) =>
        section.heading !== draft.result?.sections?.[i]?.heading || section.body !== draft.result?.sections?.[i]?.body));
      if (uncertain && !await confirm({ title: "保存结果尚未确认", body: "请求可能已创建提纲笔记。建议先重试确认保存结果；现在关闭后，请在笔记列表检查。", confirm: "仍关闭", cancel: "返回重试" })) return;
      if (!uncertain && edited && !await confirm({ title: "关闭并保留原始草稿？", body: "生成的草稿会保留，可稍后处理；本窗口尚未采纳的标题和正文修改会丢失。", confirm: "关闭", cancel: "继续编辑" })) return;
      ai.cancel();
      ai.reset(); onClose();
    });
  };

  const discard = async () => {
    if (!ai.state.draft || accepting.current || uncertain) return;
    if (!await confirm({ title: "放弃这份提纲草稿？", body: "草稿及本窗口修改将放弃，来源笔记不会改变。", confirm: "放弃草稿", cancel: "继续编辑", danger: true })) return;
    try {
      await api(`/api/ai/drafts/${ai.state.draft.id}/discard`, { method: "POST" });
      ai.reset(); onClose();
    } catch (error) { toast((error as ApiError).message, "bad"); }
  };

  const accept = async () => {
    const draft = ai.state.draft;
    if (!draft || accepting.current || !courseId || !title.trim() || sections.some((section) => !section.heading.trim() || !section.body.trim())) return;
    accepting.current = true; setSaving(true);
    try {
      const latest = await api<Draft>(`/api/ai/drafts/${draft.id}`);
      setWarnings(latest.warnings || []);
      if ((latest.status === "accepted" || latest.status === "edited") && latest.note_id) {
        if (!await confirm({ title: "这份草稿已保存", body: "服务器只能确认这份草稿已被采纳，无法确认是哪个窗口的编辑生效。本窗口后续修改尚未保存，请核对已保存的提纲。", confirm: "打开已保存的提纲", cancel: "返回保留修改" })) return;
        const note = await api<Note>(`/api/notes/${latest.note_id}`);
        toast(alreadyAcceptedMessage(!!pendingAccept.current));
        pendingAccept.current = null; setUncertain(false); ai.reset(); onAccepted(note); onClose();
        return;
      }
      const addedWarnings = (latest.warnings || []).filter((warning) => !(draft.warnings || []).includes(warning));
      if (addedWarnings.length && !await confirm({ title: "来源笔记已变化", body: addedWarnings.join("\n"), confirm: "仍保存这份提纲", cancel: "返回核对" })) return;
      pendingAccept.current ||= { title: title.trim(), course_id: courseId, outline_sections: sections.map(({ heading, body }) => ({ heading, body })) };
      const result = await api<{ note?: Note; draft: Draft; already?: boolean }>(`/api/ai/drafts/${draft.id}/accept`, {
        method: "POST", json: pendingAccept.current,
      });
      // A lost response may have already created the note. Retrying reuses its ID.
      const note = result.note || await api<Note>(`/api/notes/${result.draft.note_id}`);
      toast(result.already ? alreadyAcceptedMessage(true) : "复习提纲已另存为新笔记");
      pendingAccept.current = null; setUncertain(false);
      ai.reset(); onAccepted(note); onClose();
    } catch (error) {
      const err = error as ApiError;
      if (pendingAccept.current && (!err.status || err.status >= 500)) {
        setUncertain(true);
        toast("保存结果尚未确认，编辑已冻结。请重试确认原保存结果，避免重复创建。", "bad");
      } else {
        pendingAccept.current = null; setUncertain(false); toast(err.message, "bad");
      }
    }
    finally { accepting.current = false; setSaving(false); }
  };

  const sources: SourceNote[] = ai.state.draft?.result?.source_notes || [];
  const busy = ai.state.running || preparing || saving;
  const editingDisabled = saving || uncertain;
  const courseName = (id: string) => courses.find((course) => course.id === id)?.name || "学科";
  return <Modal open={open} onClose={close} wide
    title={<span className="flex items-center gap-2"><BookOpen size={16} className="text-accent" />多篇笔记复习提纲</span>}
    footer={<>
      <span className="mr-auto"><AiStatusLine state={ai.state} /></span>
      {ai.state.draft && <button className="btn btn-outline" disabled={saving || uncertain} onClick={discard}>放弃草稿</button>}
      <button className="btn btn-outline" disabled={preparing || saving} onClick={close}>{ai.state.running ? "停止生成" : ai.state.draft ? "稍后处理" : "关闭"}</button>
      {ai.state.draft
        ? <button className="btn btn-primary" disabled={saving || !courseId || !title.trim() || sections.some((section) => !section.heading.trim() || !section.body.trim())} onClick={accept}>{saving && <Spinner size={13} />}{uncertain ? "重试确认保存结果" : "确认并另存笔记"}</button>
        : <button className="btn btn-primary" disabled={busy || selected.size < 2} onClick={generate}>{preparing ? <Spinner size={13} /> : <Sparkles size={13} />}生成提纲</button>}
    </>}>
    {ai.state.running ? <AiProgress state={ai.state} title="正在整理复习提纲" hint="依据所选笔记全文生成草稿；每节保留来源，确认后另存新笔记。" />
      : ai.state.draft ? <div className="p-6 space-y-4 max-h-[70vh] overflow-y-auto">
        <Warnings items={warnings} />
        {uncertain && <p role="alert" className="rounded-xl p-3 bg-warn-soft text-[13px]">保存结果尚未确认，当前编辑已冻结。重试会核实同一次保存，不会重复创建笔记。</p>}
        <p className="text-[13px] text-muted">可编辑标题和各节正文。每节来源会自动保留；确认后保存为一篇新笔记。</p>
        <label className="block text-[13px]">提纲标题<input className="input mt-1 w-full" value={title} maxLength={100} disabled={editingDisabled} onChange={(event) => setTitle(event.target.value)} /></label>
        <label className="block text-[13px]">保存到学科<select className="input mt-1 w-full" value={courseId} disabled={editingDisabled} onChange={(event) => setCourseId(event.target.value)}>{courses.map((course) => <option key={course.id} value={course.id}>{course.name}</option>)}</select></label>
        {sections.map((section, index) => <section key={index} className="rounded-xl border border-rule p-4 space-y-3">
          <label className="block text-[13px]">第 {index + 1} 节标题<input className="input mt-1 w-full" value={section.heading} maxLength={100} disabled={editingDisabled} onChange={(event) => setSections((current) => current.map((item, i) => i === index ? { ...item, heading: event.target.value } : item))} /></label>
          <label className="block text-[13px]">正文（Markdown）<textarea className="input mt-1 w-full min-h-40 resize-y" value={section.body} disabled={editingDisabled} onChange={(event) => setSections((current) => current.map((item, i) => i === index ? { ...item, body: event.target.value } : item))} /></label>
          <p className="text-[12px] text-muted">来源：{section.source_note_ids.map((id, i) => <span key={id}>{i > 0 && "、"}<a className="text-accent hover:underline" target="_blank" rel="noreferrer" href={`/notes?id=${encodeURIComponent(id)}`}>{sources.find((source) => source.id === id)?.title || id}</a></span>)}</p>
        </section>)}
      </div> : <div className="p-6 space-y-4">
        <p className="text-[13px] text-muted">选择 2–30 篇笔记生成草稿。正文超出模型输入上限时会明确提示缩小范围。</p>
        {ai.state.error && <div role="alert" className="rounded-xl p-4 bg-bad-soft text-[13px]"><p className="text-bad">{ai.state.error.message}（{ai.state.error.kind}）</p>{ai.state.error.detail && <p className="mt-1 break-all line-clamp-3 text-muted">{ai.state.error.detail}</p>}<p className="mt-1 text-muted">选择仍保留，可以调整后重新生成。</p></div>}
        <div className="flex gap-2"><input aria-label="搜索来源笔记" className="input flex-1 min-w-0" placeholder="搜索来源笔记" value={query} maxLength={100} disabled={busy} onChange={(event) => setQuery(event.target.value)} /><select aria-label="筛选来源学科" className="input" value={filter} disabled={busy} onChange={(event) => { setFilter(event.target.value); setOffset(0); }}><option value="">全部学科</option>{courses.map((course) => <option key={course.id} value={course.id}>{course.name}</option>)}</select></div>
        <div className="text-[12px] text-muted">已选 {selected.size} / 30 篇</div>
        {selected.size > 0 && <div className="flex flex-wrap gap-2">{Array.from(selected.values()).map((note) => <button key={note.id} className="btn btn-outline max-w-64 text-[12px]" disabled={busy} onClick={() => toggle(note)} title={`${note.title} · ${courseName(note.course_id)}`}><span className="truncate">{note.title}</span><X size={12} /></button>)}</div>}
        <div className="border border-rule rounded-xl min-h-40 max-h-72 overflow-y-auto" aria-busy={loading}>
          {loading ? <div className="p-6 flex gap-2 items-center text-muted text-[13px]"><Spinner size={14} />加载笔记</div> : loadError ? <div role="alert" className="p-4 text-[13px] text-bad">{loadError}<button className="btn btn-outline ml-3" onClick={() => setReload((value) => value + 1)}>重试</button></div> : page?.items.length ? page.items.map((note) => <label key={note.id} className="flex items-start gap-3 p-3 border-b last:border-0 border-rule cursor-pointer hover:bg-hover"><input type="checkbox" className="mt-1" checked={selected.has(note.id)} disabled={busy || (!selected.has(note.id) && selected.size >= 30)} onChange={() => toggle(note)} /><span className="min-w-0 text-[13px]"><span className="block font-medium truncate">{note.title}</span><span className="block text-[12px] text-muted">{courseName(note.course_id)} · {note.char_count} 字</span><span className="block text-[12px] text-faint line-clamp-1">{note.excerpt}</span></span></label>) : <p className="p-6 text-[13px] text-muted">没有符合条件的笔记</p>}
        </div>
        <div className="flex items-center justify-between text-[12px] text-muted"><span>共 {page?.total || 0} 篇{page && page.items.length > 0 ? ` · ${offset + 1}–${offset + page.items.length}` : ""}</span><span className="flex gap-2"><button className="btn btn-outline" disabled={busy || loading || offset === 0} onClick={() => setOffset((value) => Math.max(0, value - 20))}>上一页</button><button className="btn btn-outline" disabled={busy || loading || !page?.has_more} onClick={() => setOffset((value) => value + 20)}>下一页</button></span></div>
        <label className="block text-[13px]">复习要求（可选）<textarea className="input mt-1 w-full min-h-20" value={instruction} maxLength={500} disabled={busy} placeholder="例如：比较易混淆的概念，并列出自测问题" onChange={(event) => setInstruction(event.target.value)} /></label>
      </div>}
  </Modal>;
}
