"use client";

import {
  BookOpen, ChevronDown, Download, FileDown, FileUp, History, Layers, Link2, MoreHorizontal, Network, Pencil, Plus,
  Search, Sparkles, Trash2, X,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { api, Course, download, fetcher, Note, NoteLinks, NoteSummary, Plan, Revision } from "@/lib/api";
import { relTime, REVISION_REASON } from "@/lib/format";
import { useAiTask } from "@/components/ai";
import { CompareSheet, ExtractSheet } from "@/components/AiSheets";
import { OutlineSheet } from "@/components/OutlineSheet";
import { Markdown } from "@/components/Markdown";
import { EditorHandle, MarkdownEditor, TitleOption } from "@/components/MarkdownEditor";
import { CourseDialog } from "@/components/Shell";
import { Dot, Empty, Modal, useConfirm, useToast } from "@/components/ui";
import { isNoteDraftKey, NoteDraft, NoteFields, NoteSaveCoordinator, SaveSnapshot } from "@/lib/note-save";
import { isNoteListCacheKey } from "@/lib/note-list-cache";

// Survives editor unmounts: switching away and back cannot start another save for the same note.
const noteSavers = new Map<string, NoteSaveCoordinator<Note>>();
const wikiCreates = new Map<string, Promise<Note>>();
let draftOwner = "";

export default function NotesPage() {
  return (
    <Suspense>
      <Notes />
    </Suspense>
  );
}

function Notes() {
  const params = useSearchParams();
  const router = useRouter();
  const courseId = params.get("course");
  const noteId = params.get("id");
  const tag = params.get("tag");
  const wiki = params.get("wiki");
  const wikiSource = params.get("from");
  const [wikiCandidates, setWikiCandidates] = useState<{ id: string; title: string; course_id: string }[]>([]);
  const [wikiError, setWikiError] = useState("");
  const [resolvingWiki, setResolvingWiki] = useState(false);
  const [filter, setFilter] = useState("");
  const [debouncedFilter, setDebouncedFilter] = useState("");
  const [paging, setPaging] = useState({ scope: "", offset: 0 });
  const [fresh, setFresh] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [outlining, setOutlining] = useState(false);
  const [editCourse, setEditCourse] = useState(false);
  const toast = useToast();
  const confirm = useConfirm();
  const importRef = useRef<HTMLInputElement>(null);

  const { data: courses, mutate: mutateCourses } = useSWR<Course[]>("/api/courses", fetcher);
  useEffect(() => { const timer = setTimeout(() => setDebouncedFilter(filter.trim()), 200); return () => clearTimeout(timer); }, [filter]);
  const scope = JSON.stringify([courseId, tag, debouncedFilter]);
  const offset = paging.scope === scope ? paging.offset : 0;
  const listParams = new URLSearchParams({ limit: "40", offset: String(offset) });
  if (courseId) listParams.set("course_id", courseId);
  if (tag) listParams.set("tag", tag);
  if (debouncedFilter) listParams.set("q", debouncedFilter);
  const listKey = `/api/notes/page?${listParams.toString()}`;
  const { data: notePage, error: listError, mutate: mutateList } = useSWR<{ items: NoteSummary[]; total: number; offset: number; limit: number; has_more: boolean }>(listKey, fetcher);
  useEffect(() => {
    if (notePage && offset > 0 && notePage.items.length === 0) setPaging({ scope, offset: Math.max(0, Math.floor((notePage.total - 1) / 40) * 40) });
  }, [notePage, offset, scope]);
  const notes = notePage?.items;
  const { data: allNotes, mutate: mutateAll } = useSWR<Pick<NoteSummary, "id" | "title" | "course_id">[]>("/api/notes/index", fetcher);
  const { data: tagStats } = useSWR<{ tag: string; count: number }[]>(`/api/tags${courseId ? `?course_id=${courseId}` : ""}`, fetcher);
  const exportKey = `/api/notes${courseId ? `?course_id=${courseId}` : ""}`;
  const { data: exportNotes } = useSWR<NoteSummary[]>(exporting ? exportKey : null, fetcher);
  const known = useMemo(() => new Set((allNotes || []).map((n) => n.title.trim().toLowerCase())), [allNotes]);
  const titles = useMemo<TitleOption[]>(() => (allNotes || []).map((n) => ({ id: n.id, title: n.title })), [allNotes]);
  const lastNote = useRef<string | null>(null);
  if (noteId) lastNote.current = noteId;
  const courseMap = useMemo(() => Object.fromEntries((courses || []).map((c) => [c.id, c])), [courses]);
  const course = courseId ? courseMap[courseId] : null;

  const shown = notes || [];
  const tags = (tagStats || []).slice(0, 12);

  const setParam = (key: string, value: string | null) => {
    const p = new URLSearchParams(params.toString());
    p.delete("new");
    p.delete("wiki");
    p.delete("from");
    p.delete("edit");
    if (value) p.set(key, value);
    else p.delete(key);
    router.replace(`/notes?${p.toString()}`);
  };

  const create = async () => {
    const target = courseId || courses?.[0]?.id;
    if (!target) {
      toast("先在左侧新建一个学科", "bad");
      return;
    }
    const n = await api<Note>("/api/notes", { method: "POST", json: { course_id: target, title: "未命名笔记" } });
    setPaging({ scope, offset: 0 });
    await Promise.all([mutateList(), mutateAll()]);
    globalMutate("/api/courses");
    setFresh(n.id);
    setParam("id", n.id);
  };

  // ⌘K 里的“新建笔记”
  const newBusy = useRef(false);
  useEffect(() => {
    if (params.get("new") !== "1" || !courses || newBusy.current) return;
    newBusy.current = true;
    create().finally(() => { newBusy.current = false; });
  }, [params, courses]); // eslint-disable-line react-hooks/exhaustive-deps

  // Legacy title links require a unique target or an explicit user choice.
  useEffect(() => {
    if (!wiki || !allNotes || !courses) return;
    let cancelled = false;
    setWikiCandidates([]); setWikiError("");
    const go = (n: { id: string; course_id: string }) =>
      !cancelled && router.replace(`/notes?id=${n.id}${courseId ? `&course=${n.course_id}` : ""}`);
    void (async () => {
      if (wikiSource) {
        const queue = noteSavers.get(wikiSource);
        if (queue && !(await queue.flush())) {
          if (!cancelled) setWikiError("来源笔记尚未同步，请先返回来源笔记重试或解决冲突。");
          return;
        }
        const sourceLinks = await api<NoteLinks>(`/api/notes/${wikiSource}/links`);
        const bound = sourceLinks.outgoing.find((link) => link.target.toLowerCase() === wiki.trim().toLowerCase());
        if (bound?.note_id) return go({ id: bound.note_id, course_id: bound.course_id || "" });
        if (bound?.bound_id) {
          if (!cancelled) setWikiError("这条链接指向的原笔记已删除。可在回收站恢复；它不会改指同名笔记。");
          return;
        }
        if (bound?.status === "ambiguous") {
          if (!cancelled) { setWikiCandidates(bound.candidates); if (!bound.candidates.length) setWikiError("原候选笔记已删除，请返回来源笔记编辑这条链接。"); }
          return;
        }
      }
      const hits = allNotes.filter((n) => n.title.trim().toLowerCase() === wiki.trim().toLowerCase());
      if (hits.length === 1) return go(hits[0]);
      if (hits.length > 1) { if (!cancelled) setWikiCandidates(hits); return; }
      const target = allNotes.find((n) => n.id === lastNote.current)?.course_id || courseId || courses[0]?.id;
      if (!target) return;
      const creationKey = `${target}:${wiki.trim().toLowerCase()}`;
      let creation = wikiCreates.get(creationKey);
      if (!creation) {
        creation = api<Note>("/api/notes", { method: "POST", json: { course_id: target, title: wiki.trim().slice(0, 100) } });
        wikiCreates.set(creationKey, creation);
        void creation.finally(() => wikiCreates.delete(creationKey)).catch(() => {});
      }
      const n = await creation;
        await Promise.all([mutateList(), mutateAll()]);
        globalMutate("/api/courses");
        toast(`已新建笔记「${n.title}」`);
        go(n);
    })().catch((e) => { if (!cancelled) setWikiError((e as Error).message); });
    return () => { cancelled = true; };
  }, [wiki, wikiSource, !!allNotes, !!courses]); // eslint-disable-line react-hooks/exhaustive-deps

  const chooseWiki = async (candidate: { id: string; title: string; course_id: string }) => {
    if (resolvingWiki) return;
    setResolvingWiki(true);
    try {
      if (wikiSource) {
        const source = await api<Note>(`/api/notes/${wikiSource}`);
        await api(`/api/notes/${wikiSource}/links/resolve`, { method: "POST", json: { target: wiki, target_id: candidate.id, base_version: source.version } });
        globalMutate(`/api/notes/${wikiSource}`); globalMutate(`/api/notes/${wikiSource}/links`); mutateAll(); mutateList();
      }
      router.replace(`/notes?id=${candidate.id}`);
    } catch (error) { toast((error as Error).message, "bad"); }
    finally { setResolvingWiki(false); }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "n") { e.preventDefault(); create(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const importFiles = async (files: FileList | null) => {
    const target = courseId || courses?.[0]?.id;
    if (!files?.length || !target) return;
    const form = new FormData();
    form.append("course_id", target);
    Array.from(files).forEach((f) => form.append("files", f));
    try {
      const r = await api<{ created: unknown[]; failed: { name: string; reason: string }[] }>("/api/notes/import", { method: "POST", body: form });
      toast(`导入了 ${r.created.length} 篇${r.failed.length ? `，${r.failed.map((f) => `${f.name}：${f.reason}`).join("；")}` : ""}`, r.failed.length ? "bad" : "ok");
      globalMutate(isNoteListCacheKey);
      globalMutate("/api/courses");
    } catch (e) {
      toast((e as Error).message, "bad");
    }
    if (importRef.current) importRef.current.value = "";
  };

  const removeCourse = async () => {
    if (!course) return;
    const ok = await confirm({
      title: `删除「${course.name}」？`,
      body: `将永久删除这个学科下的 ${course.note_count} 篇笔记、${course.material_count} 份资料、${course.card_count} 张卡片和 ${course.plan_count} 个计划，无法恢复。`,
      confirm: "永久删除", danger: true,
    });
    if (!ok) return;
    await api(`/api/courses/${course.id}`, { method: "DELETE" });
    mutateCourses();
    globalMutate(isNoteListCacheKey);
    router.replace("/notes");
    toast("已删除学科");
  };

  const syncOutlineSources = async (ids: string[]): Promise<boolean> => {
    const queues = ids.map((id) => noteSavers.get(id)).filter((queue) => !!queue);
    if (queues.some((queue) => queue.getSnapshot().recovered || queue.getSnapshot().status === "conflict")) {
      toast("所选笔记还有待确认草稿或冲突，请先在笔记中解决后生成提纲", "bad");
      return false;
    }
    if ((await Promise.all(queues.map((queue) => queue.flush()))).some((saved) => !saved)) {
      toast("所选笔记尚未同步成功，本地编辑已保留，请重试保存后生成", "bad");
      return false;
    }
    return true;
  };

  return (
    <div className="flex h-full">
      <section className="w-[300px] shrink-0 border-r border-rule flex flex-col">
        <div className="px-4 pt-5 pb-3">
          <div className="flex items-center gap-2 mb-3">
            {course && <Dot color={course.color} size={10} />}
            <h1 className="flex-1 text-[17px] font-semibold truncate">{course ? course.name : "全部笔记"}</h1>
            {course && (
              <Menu items={[
                { label: "编辑学科", icon: Pencil, onClick: () => setEditCourse(true) },
                { label: "删除学科", icon: Trash2, onClick: removeCourse, danger: true },
              ]} />
            )}
            <Link href={courseId ? `/graph?course=${courseId}` : "/graph"} className="btn btn-icon text-muted" aria-label="知识图谱" title="知识图谱"><Network size={16} /></Link>
            <button className="btn btn-icon" onClick={create} aria-label="新建笔记" title="新建笔记（⌘⇧N）"><Plus size={17} /></button>
          </div>
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" />
            <input className="input h-8 pl-8 text-[13px]" placeholder="搜索标题、正文和标签" maxLength={100} value={filter} onChange={(e) => setFilter(e.target.value)} />
          </div>
          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-3">
              {tags.map(({ tag: t, count: n }) => (
                <button key={t} onClick={() => setParam("tag", tag === t ? null : t)}
                  className="chip hover:text-ink" style={tag === t ? { background: "var(--accent-soft)", color: "var(--accent)" } : undefined}>
                  #{t}<span className="text-faint">{n}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-3">
          {!notePage && !listError && <p className="p-3 text-[13px] text-muted">正在载入笔记…</p>}
          {listError && <p role="alert" className="p-3 text-[13px] text-warn">{(listError as Error).message} <button className="underline" onClick={() => mutateList()}>重试</button></p>}
          {shown.map((n) => (
            <button key={n.id} onClick={() => setParam("id", n.id)}
              className={`w-full text-left px-3 py-2.5 rounded-xl mb-0.5 transition-colors ${noteId === n.id ? "bg-hover" : "row-hover"}`}>
              <span className="flex items-center gap-2">
                {!courseId && <Dot color={courseMap[n.course_id]?.color} size={7} />}
                <span className="text-[14px] font-medium truncate flex-1">{n.title}</span>
              </span>
              <span className="block text-[12.5px] text-muted line-clamp-2 mt-1 leading-relaxed">{n.excerpt || "空白笔记"}</span>
              <span className="flex items-center gap-2 mt-1.5 text-[11.5px] text-faint">
                {relTime(n.updated_at)}
                {n.card_count > 0 && <span className="flex items-center gap-0.5"><Layers size={11} />{n.card_count}</span>}
                {n.source_count > 0 && <span>{n.source_count} 处出处</span>}
              </span>
            </button>
          ))}
          {notes && shown.length === 0 && (
            <Empty title={filter || tag ? "没有符合条件的笔记" : "这里还没有笔记"}>
              {!filter && !tag && <button className="btn btn-primary mt-3" onClick={create}><Plus size={14} />新建笔记</button>}
            </Empty>
          )}
        </div>
        {notePage && <div className="px-3 py-2 border-t border-rule flex items-center gap-2 text-[11.5px] text-muted">
          <span className="flex-1">{notePage.total ? `${offset + 1}–${offset + notes!.length} / ${notePage.total}` : "0 篇笔记"}</span>
          <button className="btn h-7 text-[12px]" disabled={offset === 0} onClick={() => setPaging({ scope, offset: Math.max(0, offset - 40) })}>上一页</button>
          <button className="btn h-7 text-[12px]" disabled={!notePage.has_more} onClick={() => setPaging({ scope, offset: offset + 40 })}>下一页</button>
        </div>}
        <div className="px-3 py-2 border-t border-rule">
          <button className="btn text-accent w-full justify-center" onClick={() => setOutlining(true)}><Sparkles size={14} />多篇笔记生成复习提纲</button>
        </div>
        <div className="flex items-center gap-1 px-3 h-12 border-t border-rule">
          <input ref={importRef} type="file" accept=".md,.markdown,.txt" multiple hidden onChange={(e) => importFiles(e.target.files)} />
          <button className="btn text-muted" onClick={() => importRef.current?.click()}><FileUp size={14} />导入 .md</button>
          <button className="btn text-muted" onClick={() => setExporting(true)}><FileDown size={14} />导出 HTML</button>
        </div>
      </section>

      <section className="flex-1 min-w-0">
        {noteId ? (
          <Editor key={noteId} id={noteId} courses={courses || []} known={known} titles={titles} fresh={fresh === noteId}
            startEdit={params.get("edit") === "1"}
            onChanged={() => { mutateList(); mutateAll(); globalMutate("/api/courses"); }}
            onDeleted={() => { setParam("id", null); mutateList(); mutateAll(); globalMutate("/api/courses"); }} />
        ) : (
          <div className="h-full grid place-items-center">
            {wiki ? <div className="max-w-lg p-6">
              {wikiCandidates.length > 0 ? <>
                <p className="text-[15px] font-medium">「{wiki}」有多个可能目标，请明确选择</p>
                <p className="text-[13px] text-muted mt-2">{wikiSource ? "选择后会把来源笔记中的这条旧链接转换为稳定 ID，之后重命名或删除不会改指其他笔记。" : "选择要打开的笔记。"}</p>
                <ul className="space-y-2 mt-4">{wikiCandidates.map((candidate) => <li key={candidate.id}><button disabled={resolvingWiki} onClick={() => chooseWiki(candidate)} className="w-full text-left rounded-xl border border-rule p-3 row-hover">
                  <span className="block text-[14px] font-medium">{candidate.title}</span>
                  <span className="block text-[12px] text-muted mt-1">{courseMap[candidate.course_id]?.name || candidate.course_id} · {candidate.id.slice(0, 8)}</span>
                </button></li>)}</ul>
              </> : <p className="text-[14px] text-muted">{wikiError || `正在打开「${wiki}」…`}</p>}
              {wikiSource && <Link href={`/notes?id=${wikiSource}`} className="btn mt-4">返回来源笔记</Link>}
            </div> : (
              <Empty icon={<BookOpen size={28} strokeWidth={1.5} />} title="选择一篇笔记，或新建一篇">
                笔记支持 Markdown、LaTeX 公式（$…$）和代码块；输入 [[ 可以链接到另一篇笔记。按 ⌘K 可以全局搜索。
              </Empty>
            )}
          </div>
        )}
      </section>

      <ExportDialog open={exporting} onClose={() => setExporting(false)} notes={exportNotes || []} courseMap={courseMap} loading={exporting && !exportNotes} />
      <OutlineSheet open={outlining} courses={courses || []} defaultCourseId={courseId || undefined}
        beforeGenerate={syncOutlineSources} onClose={() => setOutlining(false)} onAccepted={(note) => {
          setOutlining(false); globalMutate(isNoteListCacheKey); globalMutate("/api/courses");
          router.replace(`/notes?id=${note.id}`);
        }} />
      {course && <CourseDialog open={editCourse} onClose={() => setEditCourse(false)} course={course} onSaved={() => mutateCourses()} />}
    </div>
  );
}

type MenuItem = { label: string; icon: typeof Pencil; onClick: () => void; danger?: boolean };

function Menu({ items, label = "更多" }: { items: MenuItem[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  return (
    <div className="relative" ref={ref}>
      <button className="btn btn-icon" onClick={() => setOpen((v) => !v)} aria-label={label}><MoreHorizontal size={17} /></button>
      {open && (
        <div className="anim-sheet absolute right-0 top-9 z-30 w-44 p-1 rounded-xl bg-paper border border-rule shadow-xl">
          {items.map(({ label: l, icon: Icon, onClick, danger }) => (
            <button key={l} onClick={() => { setOpen(false); onClick(); }}
              className={`w-full flex items-center gap-2 h-8 px-2.5 rounded-lg text-[13px] row-hover ${danger ? "text-bad" : ""}`}>
              <Icon size={14} />{l}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

type Mode = "read" | "split" | "edit";

function Editor({ id, courses, known, titles, fresh, startEdit, onChanged, onDeleted }: {
  id: string; courses: Course[]; known: Set<string>; titles: TitleOption[]; fresh: boolean; startEdit?: boolean;
  onChanged: () => void; onDeleted: () => void;
}) {
  const { data: note, error: noteError, mutate } = useSWR<Note>(`/api/notes/${id}`, fetcher);
  const { data: links } = useSWR<NoteLinks>(`/api/notes/${id}/links`, fetcher);
  const editorRef = useRef<EditorHandle>(null);
  const saver = useRef<NoteSaveCoordinator<Note> | null>(null);
  const [saveView, setSaveView] = useState<SaveSnapshot<Note> | null>(null);
  const [showConflict, setShowConflict] = useState(false);
  const [showDrafts, setShowDrafts] = useState(false);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [mode, setMode] = useState<Mode>("read");
  const [history, setHistory] = useState(false);
  const [sheet, setSheet] = useState<null | "organize" | "summarize" | "extract">(null);
  const [lastRun, setLastRun] = useState<Record<string, unknown>>({});
  const loaded = useRef(false);
  const ai = useAiTask();
  const toast = useToast();
  const confirm = useConfirm();
  const router = useRouter();

  useEffect(() => {
    if (!note || loaded.current) return;
    let storage: Storage | null = null;
    try { storage = window.localStorage; } catch {}
    if (!draftOwner) draftOwner = crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    let queue = noteSavers.get(id);
    if (!queue) {
      queue = new NoteSaveCoordinator(note, {
        noteId: id, owner: draftOwner, storage,
        save: (fields, baseVersion) => api<Note>(`/api/notes/${id}`, { method: "PATCH", json: { ...fields, base_version: baseVersion } }),
        onSaved: (n) => {
          globalMutate(`/api/notes/${id}`, n, { revalidate: false });
          globalMutate(isNoteListCacheKey);
          globalMutate(`/api/notes/${id}/links`);
          globalMutate("/api/courses");
        },
      });
      noteSavers.set(id, queue);
    }
    saver.current = queue;
    queue.receiveRemote(note);
    loaded.current = true;
    let savedMode: Mode | null = null;
    try { savedMode = localStorage.getItem("kf-mode") as Mode | null; } catch {}
    setMode(startEdit ? (savedMode === "split" ? "split" : "edit") : note.content.trim() ? savedMode || "read" : "edit");
    if (startEdit) setTimeout(() => editorRef.current?.focus(), 80);
    const unsubscribe = queue.subscribe((view) => {
      setSaveView(view);
      setTitle(view.fields.title);
      setContent(view.fields.content);
      setTags(view.fields.tags);
    });
    return () => { unsubscribe(); if (!queue.getSnapshot().recovered) void queue.flush(); loaded.current = false; };
  }, [id, !!note]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (note) saver.current?.receiveRemote(note); }, [note]);
  useEffect(() => {
    const refresh = () => { saver.current?.refreshDrafts(); void mutate(); };
    const storage = (event: StorageEvent) => { if (isNoteDraftKey(event.key)) refresh(); };
    const retry = () => { if (!saver.current?.getSnapshot().recovered) void saver.current?.flush(); };
    const beforeUnload = (event: BeforeUnloadEvent) => {
      const view = saver.current?.getSnapshot();
      if (view && !view.localSaved && saver.current?.hasChanges()) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("storage", storage);
    window.addEventListener("online", retry);
    window.addEventListener("focus", refresh);
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      window.removeEventListener("storage", storage); window.removeEventListener("online", retry);
      window.removeEventListener("focus", refresh); window.removeEventListener("beforeunload", beforeUnload);
    };
  }, [mutate]);

  const save = useCallback(async (fields: Partial<NoteFields> = {}) => {
    const queue = saver.current;
    if (!queue) return false;
    queue.edit(fields, false);
    return queue.flush();
  }, []);
  const editTitle = (value: string) => saver.current?.edit({ title: value });
  const editContent = (value: string) => saver.current?.edit({ content: value });

  const lastEdit = useRef<Mode>("split");
  const changeMode = (m: Mode) => {
    setMode(m);
    if (m !== "read") lastEdit.current = m;
    try { localStorage.setItem("kf-mode", m); } catch {}
  };

  // ⌘E：阅读 ⇄ 编辑
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === "e") {
        e.preventDefault();
        const next = mode === "read" ? lastEdit.current : "read";
        changeMode(next);
        if (next !== "read") setTimeout(() => editorRef.current?.focus(), 30);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const commitTags = (next: string[]) => {
    save({ tags: next });
  };

  const runTask = async (kind: "organize" | "summarize" | "extract", extra: Record<string, unknown> = {}) => {
    if (!(await save())) { toast("请先同步笔记或解决版本冲突，再使用 AI", "bad"); return; }
    const body = { kind, note_id: id, ...extra };
    setLastRun(body);
    setSheet(kind);
    const ok = await ai.run(body);
    if (!ok) setSheet(null);
  };

  const remove = async () => {
    const preview = await api<{ card_count: number }>(`/api/notes/${id}/delete-preview`);
    const ok = await confirm({
      title: "把这篇笔记移到回收站？",
      body: `${preview.card_count ? `由它生成的 ${preview.card_count} 张卡片会一起移走。` : ""}30 天内可以在设置 › 回收站里恢复。`,
      confirm: "移到回收站", danger: true,
    });
    if (!ok) return;
    if (!(await save())) { toast("请先同步笔记或解决版本冲突，再移到回收站", "bad"); return; }
    await api(`/api/notes/${id}`, { method: "DELETE" });
    noteSavers.delete(id);
    toast("已移到回收站");
    onDeleted();
  };

  if (!note) return <div className="p-10 text-muted text-[14px]">{noteError ? <><p>{(noteError as Error).message}。链接仍保留原笔记身份。</p><Link href="/settings" className="btn mt-4">前往设置查看回收站</Link><button className="btn mt-4" onClick={() => mutate()}>重新读取</button></> : "正在打开…"}</div>;
  const chars = content.replace(/\s/g, "").length;
  const busy = ai.state.running;
  const colors = Object.fromEntries(courses.map((c) => [c.id, c.color]));
  const linksPanel = <LinksPanel noteId={id} links={links} title={title} colors={colors} />;

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-2 h-14 px-6 border-b border-rule shrink-0">
        <select className="h-8 rounded-lg bg-transparent text-[13px] text-muted pr-1 outline-none hover:bg-hover"
          value={saveView?.fields.course_id || note.course_id} aria-label="所属学科"
          onChange={async (e) => { const course = e.target.value; if (await save({ course_id: course })) router.replace(`/notes?id=${id}&course=${course}`); }}>
          {courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <span className="flex-1" />
        <div className="seg">
          <button data-on={mode === "read"} onClick={() => changeMode("read")} title="⌘E 切换阅读 / 编辑">阅读</button>
          <button data-on={mode === "split"} onClick={() => changeMode("split")}>分栏</button>
          <button data-on={mode === "edit"} onClick={() => changeMode("edit")}>编辑</button>
        </div>
        <div className="w-px h-5 bg-rule mx-1" />
        <button className="btn" disabled={busy} onClick={() => runTask("organize")}><Sparkles size={14} className="text-accent" />整理</button>
        <Dropdown label="总结" disabled={busy} options={[
          { label: "约 100 字", onClick: () => runTask("summarize", { length: 100 }) },
          { label: "约 300 字", onClick: () => runTask("summarize", { length: 300 }) },
        ]} />
        <Dropdown label="提炼知识点" disabled={busy} options={[5, 10, 15, 20].map((n) => ({
          label: `${n} 个`, onClick: () => runTask("extract", { count: n }),
        }))} />
        <Menu items={[
          { label: "修订历史", icon: History, onClick: () => setHistory(true) },
          { label: "导出 .md", icon: Download, onClick: () => download(`/api/notes/${id}/export.md`, {}, `${title}.md`) },
          { label: "移到回收站", icon: Trash2, onClick: remove, danger: true },
        ]} />
      </div>

      {saveView && (saveView.status === "conflict" || saveView.recovered || !saveView.localSaved || saveView.drafts.length > 0) && (
        <div className="px-6 py-2 border-b border-rule text-[12.5px] flex gap-3 items-center flex-wrap" role="status">
          <span className="flex-1">{!saveView.localSaved ? `本地草稿写入失败：${saveView.localError}。请保持页面打开并重试同步。` : saveView.status === "conflict" ? "版本冲突：本地草稿已保留，编辑内容不会被后端覆盖。" : saveView.recovered ? "已恢复本地草稿，确认后可同步到后端。" : "发现其他窗口或上次会话保留的草稿。"}</span>
          {saveView.status === "conflict" && <button className="btn" onClick={() => setShowConflict(true)}>查看差异 / 解决冲突</button>}
          {saveView.recovered && saveView.status !== "conflict" && <button className="btn" onClick={() => save()}>同步恢复的草稿</button>}
          {saveView.drafts.length > 0 && <button className="btn" onClick={() => { saver.current?.refreshDrafts(); setShowDrafts(true); }}>查看草稿（{saveView.drafts.length}）</button>}
        </div>
      )}

      <div className="flex-1 min-h-0 flex">
        {mode !== "read" && (
          <div className={`${mode === "split" ? "w-1/2 border-r border-rule" : "w-full"} overflow-y-auto`}>
            <div className="max-w-[760px] mx-auto px-10 py-8">
              <TitleAndTags title={title} setTitle={editTitle} tags={tags} tagInput={tagInput}
                setTagInput={setTagInput} commitTags={commitTags} fresh={fresh} onEnter={() => editorRef.current?.focus()} />
              <MarkdownEditor ref={editorRef} value={content} onChange={editContent} readOnly={busy} titles={titles.filter((t) => t.id !== id)}
                onSave={() => save()}
                placeholder={"用 Markdown 写笔记。\n\n## 小标题\n- 列表（回车自动续写，Tab 缩进）\n公式：$E = mc^2$\n输入 [[ 链接到另一篇笔记\n\n```c\nint main() {}\n```"} />
              {mode === "edit" && linksPanel}
            </div>
          </div>
        )}
        {mode !== "edit" && (
          <div className={`${mode === "split" ? "w-1/2" : "w-full"} overflow-y-auto`}>
            <article className="max-w-[760px] mx-auto px-10 py-8">
              {mode === "read" ? (
                <TitleAndTags title={title} setTitle={editTitle} tags={tags} tagInput={tagInput}
                  setTagInput={setTagInput} commitTags={commitTags} />
              ) : (
                <h1 className="font-serif text-[28px] leading-tight mb-6">{title}</h1>
              )}
              {content.trim() ? (
                <div onDoubleClick={() => mode === "read" && changeMode("edit")}><Markdown text={content} known={known} noteId={id} links={links?.outgoing} /></div>
              ) : (
                <button className="text-[14px] text-faint" onClick={() => changeMode("edit")}>空白笔记，点这里开始写</button>
              )}
              {note.sources.some((s) => !s.available) && (
                <p className="mt-6 text-[12.5px] text-warn">有 {note.sources.filter((s) => !s.available).length} 处出处的原件已被删除，链接已失效。</p>
              )}
              {note.card_count > 0 && (
                <Link href={`/review?tab=cards&note=${id}`} className="inline-flex items-center gap-1.5 mt-8 text-[13px] text-muted hover:text-accent">
                  <Layers size={14} />由这篇笔记生成的 {note.card_count} 张卡片
                </Link>
              )}
              {linksPanel}
            </article>
          </div>
        )}
      </div>

      <div className="h-8 px-6 flex items-center gap-4 border-t border-rule text-[11.5px] text-faint shrink-0">
        <span role="status">{saveView ? ({ saved: "已同步到后端", saving: `${saveView.localSaved ? "已存本地，" : ""}正在同步…`, dirty: saveView.localSaved ? "已存本地，尚未同步" : "尚未同步，本地草稿不可用", error: saveView.localSaved ? "已存本地，同步失败" : "同步失败，本地草稿不可用", conflict: saveView.localSaved ? "已存本地，版本冲突" : "版本冲突，本地草稿不可用" })[saveView.status] : "正在载入…"}</span>
        {saveView?.status === "error" && <button className="text-accent" title={saveView.error || undefined} onClick={() => save()}>重试同步</button>}
        <span className="tabular-nums">{chars} 字</span>
        {links && links.backlinks.length > 0 && <span className="flex items-center gap-1"><Link2 size={11} />{links.backlinks.length} 篇笔记链接到这里</span>}
        <span className="flex-1" />
        <span className="hidden lg:inline">⌘E 阅读/编辑 · ⌘B 加粗 · [[ 链接笔记</span>
        {busy && <span className="text-accent">AI 生成期间编辑区只读</span>}
      </div>

      {(sheet === "organize" || sheet === "summarize") && (
        <CompareSheet open kind={sheet} original={content} state={ai.state}
          onClose={() => { ai.reset(); setSheet(null); }}
          onRetry={() => ai.run(lastRun)}
          onAccepted={async () => { ai.reset(); setSheet(null); const n = await mutate(); if (n) saver.current?.receiveRemote(n); onChanged(); }} />
      )}
      {sheet === "extract" && (
        <ExtractSheet open state={ai.state}
          onClose={() => { ai.reset(); setSheet(null); }}
          onRetry={() => ai.run(lastRun)}
          onAccepted={(n) => { ai.reset(); setSheet(null); mutate(); onChanged(); globalMutate("/api/stats?days=365"); toast(`已加入 ${n} 张卡片，今天就可以复习`); }} />
      )}
      <HistoryDrawer open={history} noteId={id} onClose={() => setHistory(false)}
        beforeRestore={() => save()}
        onRestored={async () => { const n = await mutate(); if (n) saver.current?.receiveRemote(n); onChanged(); }} />
      {saver.current && <NoteConflictDialog open={showConflict} id={id} queue={saver.current} onClose={() => setShowConflict(false)} />}
      <Modal open={showDrafts} onClose={() => setShowDrafts(false)} title="本地草稿" width={720}>
        <div className="p-5 space-y-4">
          <p className="text-[13px] text-muted">不同窗口的草稿单独保留。载入后可查看差异；当前编辑也会保留为本地草稿。</p>
          {saveView?.drafts.map((draft) => <DraftPreview key={draft.owner} draft={draft} onRecover={() => {
            try {
              saver.current?.recoverDraft(draft); setShowDrafts(false);
              if (saver.current?.getSnapshot().status === "conflict") setShowConflict(true);
            } catch (error) { toast((error as Error).message, "bad"); }
          }} />)}
        </div>
      </Modal>
    </div>
  );
}

function TitleAndTags({ title, setTitle, tags, tagInput, setTagInput, commitTags, fresh, onEnter }: {
  title: string; setTitle: (v: string) => void; tags: string[]; tagInput: string;
  setTagInput: (v: string) => void; commitTags: (t: string[]) => void; fresh?: boolean; onEnter?: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (fresh) { ref.current?.focus(); ref.current?.select(); }
  }, [fresh]);
  return (
    <div className="mb-7">
      <input ref={ref} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={100} aria-label="标题"
        onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing && onEnter) { e.preventDefault(); onEnter(); } }}
        className="w-full bg-transparent outline-none font-serif text-[30px] leading-tight placeholder:text-faint" placeholder="标题" />
      <div className="flex flex-wrap items-center gap-1.5 mt-3">
        {tags.map((t) => (
          <span key={t} className="chip group">
            #{t}
            <button aria-label={`删除标签 ${t}`} className="opacity-0 group-hover:opacity-100" onClick={() => commitTags(tags.filter((x) => x !== t))}>
              <X size={11} />
            </button>
          </span>
        ))}
        <input value={tagInput} onChange={(e) => setTagInput(e.target.value)} placeholder="+ 标签" aria-label="添加标签"
          className="h-6 w-20 bg-transparent outline-none text-[12px] text-muted placeholder:text-faint"
          onKeyDown={(e) => {
            if (e.key === "Enter" && tagInput.trim()) {
              commitTags([...tags, tagInput.trim().replace(/^#/, "")]);
              setTagInput("");
            }
          }} />
      </div>
    </div>
  );
}

function Dropdown({ label, options, disabled }: { label: string; options: { label: string; onClick: () => void }[]; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  return (
    <div className="relative" ref={ref}>
      <button className="btn" disabled={disabled} onClick={() => setOpen((v) => !v)}>{label}<ChevronDown size={13} className="text-muted" /></button>
      {open && (
        <div className="anim-sheet absolute right-0 top-9 z-30 w-32 p-1 rounded-xl bg-paper border border-rule shadow-xl">
          {options.map((o) => (
            <button key={o.label} onClick={() => { setOpen(false); o.onClick(); }} className="w-full text-left h-8 px-2.5 rounded-lg text-[13px] row-hover">{o.label}</button>
          ))}
        </div>
      )}
    </div>
  );
}

function DraftPreview({ draft, onRecover }: { draft: NoteDraft; onRecover: () => void }) {
  return <div className="border border-rule rounded-xl p-4">
    <p className="font-medium text-[14px]">{draft.fields.title || "未命名笔记"}</p>
    <p className="text-[12px] text-muted mt-1">{new Date(draft.updatedAt).toLocaleString()} · 基于版本 {draft.baseVersion} · {draft.fields.content.length} 字符</p>
    <pre className="text-[12px] whitespace-pre-wrap max-h-36 overflow-auto mt-3">{draft.fields.content}</pre>
    <button className="btn mt-3" onClick={onRecover}>载入此草稿</button>
  </div>;
}

function NoteConflictDialog({ open, id, queue, onClose }: {
  open: boolean; id: string; queue: NoteSaveCoordinator<Note>; onClose: () => void;
}) {
  const { data: remote, error, isValidating, mutate } = useSWR<Note>(open ? `/api/notes/${id}` : null, fetcher);
  const [merged, setMerged] = useState<NoteFields>(queue.getSnapshot().fields);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState("");
  const [view, setView] = useState(queue.getSnapshot());
  useEffect(() => queue.subscribe(setView), [queue]);
  useEffect(() => {
    if (open) { setMerged(queue.getSnapshot().fields); setFailure(""); }
  }, [open, queue]);
  const resolve = async (fields: NoteFields) => {
    if (!remote || isValidating || submitting) return;
    setSubmitting(true);
    if (!queue.resolve(remote, fields)) {
      setFailure("仍有同步请求在完成，请稍后重试"); setSubmitting(false); return;
    }
    const ok = await queue.flush();
    setSubmitting(false);
    if (ok) onClose();
    else { setFailure(queue.getSnapshot().error || "同步失败，本地草稿仍然保留"); void mutate(); }
  };
  return <Modal open={open} onClose={onClose} title="查看差异与解决冲突" width={1100}
    footer={<>
      <span className="mr-auto text-[12px] text-muted">取消会继续保留本地草稿</span>
      <button className="btn" disabled={!remote || isValidating || submitting} onClick={() => remote && resolve(remote)}>采用后端版本</button>
      <button className="btn" disabled={!remote || isValidating || submitting} onClick={() => resolve(queue.getSnapshot().fields)}>以本地草稿为准</button>
      <button className="btn btn-primary" disabled={!remote || isValidating || submitting} onClick={() => resolve(merged)}>{submitting ? "同步中…" : "保存合并结果"}</button>
    </>}>
    <div className="p-5 space-y-4 max-h-[72vh] overflow-y-auto">
      <p className="text-[13px] text-muted">对照本地草稿与后端版本，或编辑合并结果。高亮行表示与编辑开始时的相同行号不同；保存仍会检查最新版本。</p>
      {(error || failure) && <p role="alert" className="text-[13px] text-warn">{failure || (error as Error).message} <button className="underline" onClick={() => mutate()}>重新读取后端</button></p>}
      {!remote ? <p className="text-[13px] text-muted">正在读取后端版本…</p> : <>
        <div className="grid grid-cols-2 gap-4">
          <NoteDiff label="本地草稿" fields={view.fields} base={view.base} />
          <NoteDiff label={`后端版本 ${remote.version}`} fields={remote} base={view.base} />
        </div>
        <details className="text-[12px] text-muted"><summary className="cursor-pointer">查看编辑开始时的内容</summary><pre className="whitespace-pre-wrap p-3 border border-rule rounded-lg mt-2">{view.base.title}{"\n\n"}{view.base.content}</pre></details>
        <div className="space-y-2 border-t border-rule pt-4">
          <label className="label" htmlFor="conflict-title">合并后的标题</label>
          <input id="conflict-title" className="input" maxLength={100} value={merged.title} onChange={(e) => setMerged({ ...merged, title: e.target.value })} />
          <label className="label" htmlFor="conflict-content">合并后的正文</label>
          <textarea id="conflict-content" className="input font-mono text-[12px] min-h-44 resize-y" value={merged.content} onChange={(e) => setMerged({ ...merged, content: e.target.value })} />
          <p className="text-[12px] text-muted">合并结果沿用本地标签及所属学科；“采用后端版本”会采用后端的全部字段。</p>
        </div>
      </>}
    </div>
  </Modal>;
}

function NoteDiff({ label, fields, base }: { label: string; fields: NoteFields; base: NoteFields }) {
  const lines = base.content.split("\n");
  return <section className="min-w-0 border border-rule rounded-xl p-3">
    <h3 className="text-[13px] font-semibold mb-2">{label}</h3>
    <p className={`text-[13px] font-medium mb-2 ${fields.title !== base.title ? "bg-accent-soft" : ""}`}>{fields.title}</p>
    <p className="text-[11px] text-muted mb-3">标签：{fields.tags.join("、") || "无"} · 学科 ID：{fields.course_id}</p>
    <pre className="text-[12px] whitespace-pre-wrap break-words max-h-72 overflow-y-auto">{fields.content.split("\n").map((line, index) => <span key={index} className={`block min-h-[1.5em] ${line !== lines[index] ? "bg-accent-soft" : ""}`}>{line || " "}</span>)}</pre>
  </section>;
}

function HistoryDrawer({ open, noteId, onClose, onRestored, beforeRestore }: { open: boolean; noteId: string; onClose: () => void; onRestored: () => void; beforeRestore: () => Promise<boolean> }) {
  const { data, mutate } = useSWR<Revision[]>(open ? `/api/notes/${noteId}/revisions` : null, fetcher);
  const [preview, setPreview] = useState<{ id: string; content: string } | null>(null);
  const toast = useToast();
  const restore = async (rid: string) => {
    if (!(await beforeRestore())) { toast("请先同步笔记或解决版本冲突，再恢复历史版本", "bad"); return; }
    await api(`/api/notes/${noteId}/revisions/${rid}/restore`, { method: "POST" });
    toast("已恢复，恢复前的内容也存进了历史");
    mutate();
    setPreview(null);
    onRestored();
  };
  return (
    <Modal open={open} onClose={() => { setPreview(null); onClose(); }} title="修订历史" width={preview ? 900 : 480}>
      <div className="flex min-h-[40vh]">
        <ul className="w-[300px] shrink-0 p-2">
          {data?.length === 0 && <p className="p-4 text-[13px] text-muted">还没有修订记录。采纳 AI 整理或摘要前，系统会自动保存一份旧版本。</p>}
          {data?.map((r) => (
            <li key={r.id}>
              <button onClick={async () => setPreview({ id: r.id, content: (await api<{ content: string }>(`/api/notes/${noteId}/revisions/${r.id}`)).content })}
                className={`w-full text-left px-3 py-2.5 rounded-xl ${preview?.id === r.id ? "bg-hover" : "row-hover"}`}>
                <span className="block text-[13.5px] font-medium">{REVISION_REASON[r.reason] || "旧版本"}</span>
                <span className="block text-[12px] text-muted mt-0.5">{r.created_at.replace("T", " ").slice(0, 16)}，{r.length} 字符</span>
              </button>
            </li>
          ))}
        </ul>
        {preview && (
          <div className="flex-1 border-l border-rule flex flex-col min-w-0">
            <div className="flex-1 overflow-y-auto px-6 py-5 max-h-[60vh]"><Markdown text={preview.content} className="text-[14px]" /></div>
            <div className="h-14 px-5 flex items-center justify-end border-t border-rule">
              <button className="btn btn-primary" onClick={() => restore(preview.id)}>恢复到这个版本</button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

function ExportDialog({ open, onClose, notes, courseMap, loading = false }: {
  open: boolean; onClose: () => void; notes: NoteSummary[]; courseMap: Record<string, Course>; loading?: boolean;
}) {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [planId, setPlanId] = useState("");
  const [title, setTitle] = useState("复习资料");
  const { data: plans } = useSWR<Plan[]>(open ? "/api/plans" : null, fetcher);
  const toast = useToast();
  useEffect(() => { if (open) setPicked(new Set(notes.map((n) => n.id))); }, [open, notes]);
  const go = async () => {
    try {
      await download("/api/export/html", { method: "POST", json: { note_ids: notes.filter((n) => picked.has(n.id)).map((n) => n.id), plan_id: planId || null, title } }, `${title}.html`);
      toast("已导出，可以离线打开或打印");
      onClose();
    } catch (e) {
      toast((e as Error).message, "bad");
    }
  };
  return (
    <Modal open={open} onClose={onClose} title="导出离线 HTML" width={560}
      footer={<>
        <span className="mr-auto text-[12.5px] text-muted">单个文件，断网也能看，可直接打印</span>
        <button className="btn btn-outline" onClick={onClose}>取消</button>
        <button className="btn btn-primary" disabled={loading || !picked.size} onClick={go}>{loading ? "正在载入笔记…" : `导出 ${picked.size} 篇`}</button>
      </>}>
      <div className="p-5 space-y-4">
        <div>
          <label className="label" htmlFor="export-title">文件标题</label>
          <input id="export-title" className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <span className="label">笔记</span>
          <div className="max-h-[36vh] overflow-y-auto border border-rule rounded-xl divide-y divide-rule">
            {notes.map((n) => (
              <label key={n.id} className="flex items-center gap-3 px-3 py-2.5 text-[13.5px] cursor-pointer row-hover">
                <input type="checkbox" checked={picked.has(n.id)} className="accent-[var(--accent)]"
                  onChange={(e) => setPicked((s) => { const x = new Set(s); if (e.target.checked) x.add(n.id); else x.delete(n.id); return x; })} />
                <Dot color={courseMap[n.course_id]?.color} size={7} />
                <span className="flex-1 truncate">{n.title}</span>
              </label>
            ))}
          </div>
        </div>
        <div>
          <label className="label" htmlFor="export-plan">附带学习计划</label>
          <select id="export-plan" className="input" value={planId} onChange={(e) => setPlanId(e.target.value)}>
            <option value="">不附带</option>
            {plans?.map((p) => <option key={p.id} value={p.id}>{p.goal}</option>)}
          </select>
        </div>
      </div>
    </Modal>
  );
}


function LinksPanel({ noteId, links, title, colors }: { noteId: string; links?: NoteLinks; title: string; colors: Record<string, string> }) {
  if (!links) return null;
  const missing = links.outgoing.filter((o) => o.status === "missing" && !o.bound_id);
  const unavailable = links.outgoing.filter((o) => o.status === "missing" && !!o.bound_id);
  const ambiguous = links.outgoing.filter((o) => o.status === "ambiguous");
  const clean = (t: string) => t.replace(/\[\[([^\]|]+)(?:\|([^\]]*))?\]\]/g, (_, target: string, label?: string) => label || target);
  return (
    <section className="mt-12 pt-5 border-t border-rule">
      <h3 className="section-title flex items-center gap-1.5"><Link2 size={13} />反向链接 {links.backlinks.length > 0 && <span className="text-faint font-normal">{links.backlinks.length}</span>}</h3>
      {links.backlinks.length === 0 ? (
        <p className="text-[13px] text-faint">还没有笔记链接到这里。在别的笔记里输入 [[{title}]] 就能连过来。</p>
      ) : (
        <ul className="grid gap-2">
          {links.backlinks.map((b) => (
            <li key={b.id}>
              <Link href={`/notes?id=${b.id}`} className="block rounded-xl border border-rule px-3.5 py-2.5 row-hover">
                <span className="flex items-center gap-2 text-[13.5px] font-medium"><Dot color={colors[b.course_id]} size={7} />{b.title}</span>
                {b.snippet && <span className="block text-[12.5px] text-muted mt-1 leading-relaxed line-clamp-2">{clean(b.snippet)}</span>}
              </Link>
            </li>
          ))}
        </ul>
      )}
      {missing.length > 0 && (
        <p className="mt-4 text-[12.5px] text-muted leading-relaxed">
          还没建的笔记：
          {missing.map((o, i) => (
            <span key={o.title}>{i > 0 && "、"}
              <Link href={`/notes?wiki=${encodeURIComponent(o.target)}&from=${noteId}`} className="border-b border-dashed border-faint hover:text-accent">{o.label}</Link>
            </span>
          ))}
          <span className="text-faint">（点击即新建）</span>
        </p>
      )}
      {ambiguous.length > 0 && <div className="mt-4 text-[12.5px] text-warn space-y-2">
        <p>以下旧链接存在同名目标，尚未连接到任何一篇笔记：</p>
        {ambiguous.map((link) => <p key={link.target}><Link className="underline" href={`/notes?wiki=${encodeURIComponent(link.target)}&from=${noteId}`}>{link.label}：选择目标并转换为稳定链接</Link></p>)}
      </div>}
      {unavailable.length > 0 && <p className="mt-4 text-[12.5px] text-muted">原目标已删除：{unavailable.map((link) => link.label).join("、")}。恢复原笔记后链接会重新可用。</p>}
    </section>
  );
}
