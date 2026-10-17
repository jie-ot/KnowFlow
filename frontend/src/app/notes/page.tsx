"use client";

import {
  BookOpen, ChevronDown, Download, FileDown, FileUp, History, Layers, Link2, MoreHorizontal, Network, Pencil, Plus,
  Search, Sparkles, Trash2, X,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { api, ApiError, Course, download, fetcher, Note, NoteLinks, NoteSummary, Plan, Revision } from "@/lib/api";
import { relTime, REVISION_REASON } from "@/lib/format";
import { useAiTask } from "@/components/ai";
import { CompareSheet, ExtractSheet } from "@/components/AiSheets";
import { Markdown } from "@/components/Markdown";
import { EditorHandle, MarkdownEditor, TitleOption } from "@/components/MarkdownEditor";
import { CourseDialog } from "@/components/Shell";
import { Dot, Empty, Modal, useConfirm, useToast } from "@/components/ui";

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
  const [filter, setFilter] = useState("");
  const [fresh, setFresh] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [editCourse, setEditCourse] = useState(false);
  const toast = useToast();
  const confirm = useConfirm();
  const importRef = useRef<HTMLInputElement>(null);

  const { data: courses, mutate: mutateCourses } = useSWR<Course[]>("/api/courses", fetcher);
  const listKey = `/api/notes${courseId ? `?course_id=${courseId}` : ""}`;
  const { data: notes, mutate: mutateList } = useSWR<NoteSummary[]>(listKey, fetcher);
  const { data: allNotes, mutate: mutateAll } = useSWR<NoteSummary[]>("/api/notes", fetcher);
  const known = useMemo(() => new Set((allNotes || []).map((n) => n.title.trim().toLowerCase())), [allNotes]);
  const titles = useMemo<TitleOption[]>(() => (allNotes || []).map((n) => ({ id: n.id, title: n.title })), [allNotes]);
  const lastNote = useRef<string | null>(null);
  if (noteId) lastNote.current = noteId;
  const courseMap = useMemo(() => Object.fromEntries((courses || []).map((c) => [c.id, c])), [courses]);
  const course = courseId ? courseMap[courseId] : null;

  const shown = (notes || []).filter(
    (n) => (!tag || n.tags.includes(tag)) &&
      (!filter || (n.title + n.excerpt + n.tags.join(" ")).toLowerCase().includes(filter.toLowerCase())),
  );
  const tags = useMemo(() => {
    const m = new Map<string, number>();
    (notes || []).forEach((n) => n.tags.forEach((t) => m.set(t, (m.get(t) || 0) + 1)));
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12);
  }, [notes]);

  const setParam = (key: string, value: string | null) => {
    const p = new URLSearchParams(params.toString());
    p.delete("new");
    p.delete("wiki");
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

  // 点击 [[双链]]：打开同名笔记；没有就在当前学科里新建一篇
  const wikiBusy = useRef(false);
  useEffect(() => {
    if (!wiki || !allNotes || !courses || wikiBusy.current) return;
    const go = (n: { id: string; course_id: string }) =>
      router.replace(`/notes?id=${n.id}${courseId ? `&course=${n.course_id}` : ""}`);
    const hit = allNotes.find((n) => n.title.trim().toLowerCase() === wiki.trim().toLowerCase());
    if (hit) return go(hit);
    const target = allNotes.find((n) => n.id === lastNote.current)?.course_id || courseId || courses[0]?.id;
    if (!target) return;
    wikiBusy.current = true;
    api<Note>("/api/notes", { method: "POST", json: { course_id: target, title: wiki.trim().slice(0, 100) } })
      .then(async (n) => {
        await Promise.all([mutateList(), mutateAll()]);
        globalMutate("/api/courses");
        toast(`已新建笔记「${n.title}」`);
        go(n);
      })
      .catch((e) => toast((e as Error).message, "bad"))
      .finally(() => { wikiBusy.current = false; });
  }, [wiki, allNotes, courses]); // eslint-disable-line react-hooks/exhaustive-deps

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
      mutateList();
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
    router.replace("/notes");
    toast("已删除学科");
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
            <input className="input h-8 pl-8 text-[13px]" placeholder="筛选标题和内容" value={filter} onChange={(e) => setFilter(e.target.value)} />
          </div>
          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-3">
              {tags.map(([t, n]) => (
                <button key={t} onClick={() => setParam("tag", tag === t ? null : t)}
                  className="chip hover:text-ink" style={tag === t ? { background: "var(--accent-soft)", color: "var(--accent)" } : undefined}>
                  #{t}<span className="text-faint">{n}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-3">
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
            {wiki ? <p className="text-[14px] text-muted">正在打开「{wiki}」…</p> : (
              <Empty icon={<BookOpen size={28} strokeWidth={1.5} />} title="选择一篇笔记，或新建一篇">
                笔记支持 Markdown、LaTeX 公式（$…$）和代码块；输入 [[ 可以链接到另一篇笔记。按 ⌘K 可以全局搜索。
              </Empty>
            )}
          </div>
        )}
      </section>

      <ExportDialog open={exporting} onClose={() => setExporting(false)} notes={notes || []} courseMap={courseMap} />
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
  const { data: note, mutate } = useSWR<Note>(`/api/notes/${id}`, fetcher, { revalidateOnFocus: false });
  const { data: links, mutate: mutateLinks } = useSWR<NoteLinks>(`/api/notes/${id}/links`, fetcher);
  const editorRef = useRef<EditorHandle>(null);
  const pending = useRef<Record<string, unknown> | null>(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [mode, setMode] = useState<Mode>("read");
  const [saveState, setSaveState] = useState<"saved" | "saving" | "dirty" | "error">("saved");
  const [history, setHistory] = useState(false);
  const [sheet, setSheet] = useState<null | "organize" | "summarize" | "extract">(null);
  const [lastRun, setLastRun] = useState<Record<string, unknown>>({});
  const version = useRef(0);
  const loaded = useRef(false);
  // 编辑框里真的已经填进了这篇笔记的内容。只看 loaded 不够：它在填内容的同一轮就变成 true，
  // 那一刻标题和正文还是空的，会被误判为“改动”（曾导致切换笔记时把空内容存了回去）
  const [hydrated, setHydrated] = useState(false);
  const ai = useAiTask();
  const toast = useToast();
  const confirm = useConfirm();
  const router = useRouter();

  useEffect(() => {
    if (note && (!loaded.current || note.version !== version.current)) {
      setTitle(note.title);
      setContent(note.content);
      setTags(note.tags);
      version.current = note.version;
      if (!loaded.current) {
        let saved: Mode | null = null;
        try { saved = localStorage.getItem("kf-mode") as Mode | null; } catch {}
        setMode(startEdit ? (saved === "split" ? "split" : "edit") : note.content.trim() ? saved || "read" : "edit");
        if (startEdit) setTimeout(() => editorRef.current?.focus(), 80);
      }
      loaded.current = true;
      setHydrated(true);
    }
  }, [note]);

  const save = useCallback(async (fields: Record<string, unknown>) => {
    pending.current = null;
    setSaveState("saving");
    try {
      const n = await api<Note & { renamed_links?: number }>(`/api/notes/${id}`, { method: "PATCH", json: { ...fields, base_version: version.current } });
      version.current = n.version;
      mutate(n, { revalidate: false });
      setSaveState("saved");
      onChanged();
      mutateLinks();
      if (n.renamed_links) toast(`已同步改好 ${n.renamed_links} 处指向这篇笔记的链接`);
    } catch (e) {
      setSaveState("error");
      toast((e as ApiError).message, "bad");
    }
  }, [id, mutate, onChanged, toast]);

  // 自动保存：停止输入 800 ms 后保存
  useEffect(() => {
    if (!note || !hydrated) return;
    if (title === note.title && content === note.content) {
      pending.current = null;
      return;
    }
    setSaveState("dirty");
    pending.current = { title, content };
    const t = setTimeout(() => save({ title, content }), 800);
    return () => clearTimeout(t);
  }, [title, content, hydrated]); // eslint-disable-line react-hooks/exhaustive-deps

  // 切到别的笔记（或点了双链）时，把还没来得及自动保存的改动存掉
  const noteRef = useRef(note);
  noteRef.current = note;
  useEffect(() => () => {
    const p = pending.current;
    const n = noteRef.current;
    // 保险：标题和正文都是空的、而原笔记有内容时，绝不保存
    if (p && n && !String(p.title || "").trim() && !String(p.content || "").trim() && (n.content.trim() || n.title.trim())) return;
    if (p) {
      api(`/api/notes/${id}`, { method: "PATCH", json: { ...pending.current, base_version: version.current } })
        .then(onChanged).catch(() => {});
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
    setTags(next);
    save({ tags: next });
  };

  const runTask = async (kind: "organize" | "summarize" | "extract", extra: Record<string, unknown> = {}) => {
    if (saveState === "dirty" || saveState === "saving") await save({ title, content });
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
    await api(`/api/notes/${id}`, { method: "DELETE" });
    toast("已移到回收站");
    onDeleted();
  };

  if (!note) return <div className="p-10 text-muted text-[14px]">正在打开…</div>;
  const chars = content.replace(/\s/g, "").length;
  const busy = ai.state.running;
  const colors = Object.fromEntries(courses.map((c) => [c.id, c.color]));
  const linksPanel = <LinksPanel links={links} title={title} colors={colors} />;

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-2 h-14 px-6 border-b border-rule shrink-0">
        <select className="h-8 rounded-lg bg-transparent text-[13px] text-muted pr-1 outline-none hover:bg-hover"
          value={note.course_id} aria-label="所属学科"
          onChange={async (e) => { await save({ course_id: e.target.value }); router.replace(`/notes?id=${id}&course=${e.target.value}`); }}>
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

      <div className="flex-1 min-h-0 flex">
        {mode !== "read" && (
          <div className={`${mode === "split" ? "w-1/2 border-r border-rule" : "w-full"} overflow-y-auto`}>
            <div className="max-w-[760px] mx-auto px-10 py-8">
              <TitleAndTags title={title} setTitle={setTitle} tags={tags} tagInput={tagInput}
                setTagInput={setTagInput} commitTags={commitTags} fresh={fresh} onEnter={() => editorRef.current?.focus()} />
              <MarkdownEditor ref={editorRef} value={content} onChange={setContent} readOnly={busy} titles={titles.filter((t) => t.id !== id)}
                onSave={() => save({ title, content })}
                placeholder={"用 Markdown 写笔记。\n\n## 小标题\n- 列表（回车自动续写，Tab 缩进）\n公式：$E = mc^2$\n输入 [[ 链接到另一篇笔记\n\n```c\nint main() {}\n```"} />
              {mode === "edit" && linksPanel}
            </div>
          </div>
        )}
        {mode !== "edit" && (
          <div className={`${mode === "split" ? "w-1/2" : "w-full"} overflow-y-auto`}>
            <article className="max-w-[760px] mx-auto px-10 py-8">
              {mode === "read" ? (
                <TitleAndTags title={title} setTitle={setTitle} tags={tags} tagInput={tagInput}
                  setTagInput={setTagInput} commitTags={commitTags} />
              ) : (
                <h1 className="font-serif text-[28px] leading-tight mb-6">{title}</h1>
              )}
              {content.trim() ? (
                <div onDoubleClick={() => mode === "read" && changeMode("edit")}><Markdown text={content} known={known} /></div>
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
        <span>{{ saved: "已保存", saving: "保存中…", dirty: "有改动", error: "保存失败，内容仍在编辑区" }[saveState]}</span>
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
          onAccepted={async () => { ai.reset(); setSheet(null); loaded.current = true; const n = await mutate(); if (n) { setContent(n.content); version.current = n.version; } onChanged(); }} />
      )}
      {sheet === "extract" && (
        <ExtractSheet open state={ai.state}
          onClose={() => { ai.reset(); setSheet(null); }}
          onRetry={() => ai.run(lastRun)}
          onAccepted={(n) => { ai.reset(); setSheet(null); mutate(); onChanged(); globalMutate("/api/stats?days=365"); toast(`已加入 ${n} 张卡片，今天就可以复习`); }} />
      )}
      <HistoryDrawer open={history} noteId={id} onClose={() => setHistory(false)}
        onRestored={async () => { const n = await mutate(); if (n) { setContent(n.content); setTitle(n.title); version.current = n.version; } onChanged(); }} />
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

function HistoryDrawer({ open, noteId, onClose, onRestored }: { open: boolean; noteId: string; onClose: () => void; onRestored: () => void }) {
  const { data, mutate } = useSWR<Revision[]>(open ? `/api/notes/${noteId}/revisions` : null, fetcher);
  const [preview, setPreview] = useState<{ id: string; content: string } | null>(null);
  const toast = useToast();
  const restore = async (rid: string) => {
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

function ExportDialog({ open, onClose, notes, courseMap }: {
  open: boolean; onClose: () => void; notes: NoteSummary[]; courseMap: Record<string, Course>;
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
        <button className="btn btn-primary" disabled={!picked.size} onClick={go}>导出 {picked.size} 篇</button>
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


function LinksPanel({ links, title, colors }: { links?: NoteLinks; title: string; colors: Record<string, string> }) {
  if (!links) return null;
  const missing = links.outgoing.filter((o) => !o.note_id);
  const clean = (t: string) => t.replace(/\[\[([^\]|]+)(\|[^\]]*)?\]\]/g, "$1");
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
              <Link href={`/notes?wiki=${encodeURIComponent(o.title)}`} className="border-b border-dashed border-faint hover:text-accent">{o.title}</Link>
            </span>
          ))}
          <span className="text-faint">（点击即新建）</span>
        </p>
      )}
    </section>
  );
}
