"use client";

import {
  BookOpen, BookOpenCheck, CalendarCheck, CalendarDays, CornerDownLeft, FilePlus2, FileStack, FileUp, Layers, Monitor, Moon, Network,
  Plus, Search, Settings, Sparkles, Sun, Target,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ReactNode, Suspense, useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { api, Course, fetcher, SearchItem } from "@/lib/api";
import { colorOf, COURSE_COLORS } from "@/lib/format";
import { AiProvider } from "./ai";
import { AssistantPanel } from "./Assistant";
import { FocusProvider } from "./Focus";
import { ConfirmProvider, Dot, Highlight, Modal, ToastProvider, useToast } from "./ui";

const NAV = [
  { href: "/", label: "今天", icon: Sun },
  { href: "/notes", label: "笔记", icon: BookOpen },
  { href: "/materials", label: "资料", icon: FileStack },
  { href: "/review", label: "复习", icon: Layers },
  { href: "/plans", label: "计划", icon: CalendarCheck },
  { href: "/graph", label: "图谱", icon: Network },
];

/** ⌘K 里可以直接执行的操作 */
const ACTIONS = [
  { id: "assistant", label: "问 AI 助手", keys: "ai zhushou wenti help bangzhu assistant", icon: Sparkles, href: "#assistant" },
  { id: "new-note", label: "新建笔记", keys: "xinjian biji note new", icon: FilePlus2, href: "/notes?new=1" },
  { id: "review", label: "开始复习", keys: "fuxi review kapian", icon: Layers, href: "/review" },
  { id: "upload", label: "上传资料", keys: "shangchuan ziliao upload material", icon: FileUp, href: "/materials" },
  { id: "new-plan", label: "新建计划", keys: "xinjian jihua plan new", icon: Target, href: "/plans?new=1" },
  { id: "calendar", label: "打开计划月历", keys: "yueli rili calendar jihua", icon: CalendarDays, href: "/plans?view=calendar" },
  { id: "graph", label: "打开知识图谱", keys: "tupu guanxi graph link", icon: Network, href: "/graph" },
  { id: "guide", label: "使用说明", keys: "shiyong shuoming bangzhu guide help jiaocheng", icon: BookOpenCheck, href: "/guide" },
  { id: "backup", label: "备份与恢复数据", keys: "beifen huifu daochu backup restore", icon: Settings, href: "/settings#backup" },
  { id: "settings", label: "设置", keys: "shezhi settings model moxing", icon: Settings, href: "/settings" },
];
type Action = (typeof ACTIONS)[number];

export function Shell({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      <ConfirmProvider>
        <AiProvider>
          <FocusProvider>
            <Suspense>
              <Frame>{children}</Frame>
            </Suspense>
          </FocusProvider>
        </AiProvider>
      </ConfirmProvider>
    </ToastProvider>
  );
}

function Frame({ children }: { children: ReactNode }) {
  const [palette, setPalette] = useState(false);
  const [assistant, setAssistant] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((v) => !v);
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setAssistant((v) => !v);
      }
    };
    const onOpen = () => setAssistant(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("kf-assistant", onOpen);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("kf-assistant", onOpen); };
  }, []);
  return (
    <div className="flex h-dvh overflow-hidden">
      <Sidebar onSearch={() => setPalette(true)} onAssistant={() => setAssistant((v) => !v)} assistantOpen={assistant} />
      <main className="flex-1 min-w-0 overflow-y-auto bg-paper">{children}</main>
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
      <AssistantPanel open={assistant} onClose={() => setAssistant(false)} />
    </div>
  );
}

function Sidebar({ onSearch, onAssistant, assistantOpen }: { onSearch: () => void; onAssistant: () => void; assistantOpen: boolean }) {
  const path = usePathname();
  const params = useSearchParams();
  const { data: courses, mutate } = useSWR<Course[]>("/api/courses", fetcher);
  const [adding, setAdding] = useState(false);
  const due = courses?.reduce((s, c) => s + c.due_count, 0) ?? 0;
  const activeCourse = params.get("course");

  return (
    <aside className="w-[232px] shrink-0 bg-canvas border-r border-rule flex flex-col">
      <div className="h-14 px-5 flex items-center gap-2">
        <Logo />
        <span className="text-[15px] font-semibold tracking-tight">KnowFlow</span>
      </div>
      <div className="px-3">
        <button onClick={onSearch}
          className="w-full h-8 px-2.5 mb-3 rounded-lg flex items-center gap-2 text-[13px] text-muted bg-paper border border-rule hover:border-faint transition-colors">
          <Search size={14} />
          <span className="flex-1 text-left">搜索</span>
          <kbd className="text-[11px] text-faint font-sans">⌘K</kbd>
        </button>
        <button onClick={onAssistant} aria-pressed={assistantOpen}
          className={`w-full h-9 px-2.5 mb-3 rounded-lg flex items-center gap-2 text-[13.5px] font-medium transition-colors
            ${assistantOpen ? "bg-accent text-[var(--accent-ink)]" : "bg-accent-soft text-accent hover:brightness-95"}`}>
          <Sparkles size={15} />
          <span className="flex-1 text-left">问 AI</span>
          <kbd className={`text-[11px] font-sans ${assistantOpen ? "opacity-80" : "opacity-60"}`}>⌘J</kbd>
        </button>
        <nav className="space-y-0.5">
          {NAV.map(({ href, label, icon: Icon }) => {
            const on = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link key={href} href={href}
                className={`flex items-center gap-2.5 h-8 px-2.5 rounded-lg text-[14px] transition-colors ${on ? "bg-paper font-medium shadow-[0_1px_2px_rgba(0,0,0,0.06)]" : "row-hover"}`}>
                <Icon size={16} className={on ? "text-accent" : "text-muted"} strokeWidth={1.8} />
                <span className="flex-1">{label}</span>
                {href === "/review" && due > 0 && (
                  <span className="text-[11px] font-semibold tabular-nums px-1.5 rounded-full bg-accent text-[var(--accent-ink)]">{due}</span>
                )}
              </Link>
            );
          })}
        </nav>
      </div>

      <div className="mt-6 px-3 flex-1 overflow-y-auto">
        <div className="flex items-center justify-between px-2.5 mb-1">
          <span className="text-[12px] font-medium text-muted">学科</span>
          <button className="btn btn-icon h-6 w-6 text-muted" onClick={() => setAdding(true)} aria-label="新建学科">
            <Plus size={14} />
          </button>
        </div>
        <div className="space-y-0.5">
          {courses?.map((c) => {
            const on = path.startsWith("/notes") && activeCourse === c.id;
            return (
              <Link key={c.id} href={`/notes?course=${c.id}`}
                className={`flex items-center gap-2.5 h-8 px-2.5 rounded-lg text-[13.5px] ${on ? "bg-paper font-medium" : "row-hover"}`}>
                <Dot color={c.color} />
                <span className="flex-1 truncate">{c.name}</span>
                <span className="text-[11px] text-faint tabular-nums">{c.note_count}</span>
              </Link>
            );
          })}
          {courses?.length === 0 && <p className="px-2.5 text-[12px] text-faint">还没有学科</p>}
        </div>
      </div>

      <div className="p-3 border-t border-rule flex items-center gap-1">
        <Link href="/guide" title="使用说明" aria-label="使用说明"
          className={`btn btn-icon h-8 w-8 ${path.startsWith("/guide") ? "bg-paper text-accent" : "text-muted"}`}>
          <BookOpenCheck size={16} strokeWidth={1.8} />
        </Link>
        <Link href="/settings"
          className={`flex-1 flex items-center gap-2.5 h-8 px-2.5 rounded-lg text-[14px] ${path.startsWith("/settings") ? "bg-paper font-medium" : "row-hover"}`}>
          <Settings size={16} className="text-muted" strokeWidth={1.8} />
          设置
        </Link>
        <ThemeSwitch />
      </div>
      <CourseDialog open={adding} onClose={() => setAdding(false)} onSaved={() => mutate()} />
    </aside>
  );
}

type Theme = "light" | "dark" | "system";

/** 浅色 / 深色 / 跟随系统，记在本机浏览器里 */
function ThemeSwitch() {
  const [theme, setTheme] = useState<Theme>("system");
  useEffect(() => {
    try {
      const t = localStorage.getItem("kf-theme");
      if (t === "light" || t === "dark") setTheme(t);
    } catch {}
  }, []);
  const choose = (t: Theme) => {
    setTheme(t);
    if (t === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = t;
    try { localStorage.setItem("kf-theme", t); } catch {}
  };
  const options = [
    { t: "light" as const, icon: Sun, label: "浅色" },
    { t: "dark" as const, icon: Moon, label: "深色" },
    { t: "system" as const, icon: Monitor, label: "跟随系统" },
  ];
  return (
    <div className="seg" role="radiogroup" aria-label="主题">
      {options.map(({ t, icon: Icon, label }) => (
        <button key={t} data-on={theme === t} onClick={() => choose(t)} title={label} aria-label={label}
          role="radio" aria-checked={theme === t} className="!px-1.5 grid place-items-center">
          <Icon size={13} />
        </button>
      ))}
    </div>
  );
}

function Logo() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden>
      <rect x="4.5" y="2.5" width="13" height="10" rx="2.2" fill="none" stroke="var(--faint)" strokeWidth="1.3" transform="rotate(6 11 7.5)" />
      <rect x="2.5" y="6" width="13" height="10" rx="2.2" fill="var(--paper)" stroke="var(--ink)" strokeWidth="1.4" />
      <line x1="5" y1="9" x2="13" y2="9" stroke="var(--accent)" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

export function CourseDialog({
  open, onClose, onSaved, course,
}: { open: boolean; onClose: () => void; onSaved: (c: Course) => void; course?: Course }) {
  const [name, setName] = useState("");
  const [color, setColor] = useState("blue");
  const toast = useToast();
  useEffect(() => {
    if (open) {
      setName(course?.name || "");
      setColor(course?.color || Object.keys(COURSE_COLORS)[Math.floor(Math.random() * 8)]);
    }
  }, [open, course]);
  const save = async () => {
    if (!name.trim()) return;
    try {
      const c = course
        ? await api<Course>(`/api/courses/${course.id}`, { method: "PATCH", json: { name, color } })
        : await api<Course>("/api/courses", { method: "POST", json: { name, color } });
      onSaved(c);
      onClose();
    } catch (e) {
      toast((e as Error).message, "bad");
    }
  };
  return (
    <Modal open={open} onClose={onClose} title={course ? "编辑学科" : "新建学科"} width={400}
      footer={<>
        <button className="btn btn-outline" onClick={onClose}>取消</button>
        <button className="btn btn-primary" onClick={save} disabled={!name.trim()}>{course ? "保存" : "创建"}</button>
      </>}>
      <div className="p-5 space-y-4">
        <div>
          <label className="label" htmlFor="course-name">名称</label>
          <input id="course-name" className="input" value={name} maxLength={50} placeholder="例如：数据结构"
            onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && save()} />
        </div>
        <div>
          <span className="label">颜色</span>
          <div className="flex gap-2">
            {Object.keys(COURSE_COLORS).map((c) => (
              <button key={c} onClick={() => setColor(c)} aria-label={c}
                className="w-7 h-7 rounded-full grid place-items-center transition-transform hover:scale-110"
                style={{ boxShadow: color === c ? `0 0 0 2px var(--paper), 0 0 0 4px ${colorOf(c)}` : undefined }}>
                <span className="w-5 h-5 rounded-full" style={{ background: colorOf(c) }} />
              </button>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}

const TYPE_LABEL = { note: "笔记", material: "资料", card: "卡片" };

function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<SearchItem[]>([]);
  const [index, setIndex] = useState(0);
  const router = useRouter();
  const { data: courses } = useSWR<Course[]>("/api/courses", fetcher);
  const courseMap = useMemo(() => Object.fromEntries((courses || []).map((c) => [c.id, c])), [courses]);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQ("");
      setIndex(0);
      setTimeout(() => input.current?.focus(), 20);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const t = setTimeout(async () => {
      try {
        const r = await api<{ items: SearchItem[] }>(`/api/search?limit=12&q=${encodeURIComponent(q)}`);
        setItems(r.items);
        setIndex(0);
      } catch {}
    }, 160);
    return () => clearTimeout(t);
  }, [q, open]);

  const needle = q.trim().toLowerCase();
  const actions = needle ? ACTIONS.filter((a) => a.label.includes(needle) || a.keys.includes(needle)) : ACTIONS.slice(0, 4);
  const total = actions.length + items.length;

  const go = (it: SearchItem) => {
    onClose();
    if (it.type === "note") router.push(`/notes?id=${it.id}`);
    else if (it.type === "material") router.push(`/materials/view?id=${it.id}${it.unit_id ? `&unit=${it.unit_id}` : ""}`);
    else router.push(it.source_note_id ? `/notes?id=${it.source_note_id}` : "/review?tab=cards");
  };
  const act = (a: Action) => {
    onClose();
    if (a.href === "#assistant") window.dispatchEvent(new Event("kf-assistant"));
    else router.push(a.href);
  };
  const choose = (i: number) => (i < actions.length ? act(actions[i]) : items[i - actions.length] && go(items[i - actions.length]));

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-center pt-[14vh] px-4 anim-fade" style={{ background: "var(--overlay)" }}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="anim-sheet w-full max-w-[600px] h-fit bg-paper rounded-2xl border border-rule shadow-2xl overflow-hidden">
        <div className="flex items-center gap-3 px-4 h-14 border-b border-rule">
          <Search size={17} className="text-muted" />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="搜索笔记、资料和卡片，或输入要做的事"
            className="flex-1 bg-transparent outline-none text-[15px] placeholder:text-faint"
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              if (e.key === "ArrowDown") { e.preventDefault(); setIndex((i) => Math.min(i + 1, total - 1)); }
              if (e.key === "ArrowUp") { e.preventDefault(); setIndex((i) => Math.max(i - 1, 0)); }
              if (e.key === "Enter" && !e.nativeEvent.isComposing && total) choose(index);
            }} />
        </div>
        <div className="max-h-[52vh] overflow-y-auto p-2">
          {actions.length > 0 && <p className="px-3 pt-1 pb-1.5 text-[12px] text-muted">操作</p>}
          {actions.map((a, i) => (
            <button key={a.id} onMouseEnter={() => setIndex(i)} onClick={() => act(a)}
              className={`w-full flex items-center gap-3 h-10 px-3 rounded-xl text-[14px] text-left ${i === index ? "bg-hover" : ""}`}>
              <a.icon size={15} className="text-muted" />
              <span className="flex-1">{a.label}</span>
              {i === index && <CornerDownLeft size={13} className="text-faint" />}
            </button>
          ))}
          {items.length > 0 && <p className="px-3 pt-3 pb-1.5 text-[12px] text-muted">{q ? "搜索结果" : "最近编辑"}</p>}
          {items.map((it, j) => { const i = j + actions.length; return (
            <button key={it.type + it.id} onMouseEnter={() => setIndex(i)} onClick={() => go(it)}
              className={`w-full text-left px-3 py-2.5 rounded-xl flex gap-3 ${i === index ? "bg-hover" : ""}`}>
              <Dot color={courseMap[it.course_id]?.color} />
              <span className="flex-1 min-w-0">
                <span className="flex items-baseline gap-2">
                  <span className="text-[14px] font-medium truncate">{it.title}</span>
                  <span className="text-[11px] text-faint shrink-0">{TYPE_LABEL[it.type]}{it.unit_label ? `，${it.unit_label}` : ""}</span>
                </span>
                <span className="block text-[12.5px] text-muted truncate mt-0.5">
                  <Highlight text={it.snippet.text} ranges={it.snippet.highlights} />
                </span>
              </span>
            </button>
          ); })}
          {q && total === 0 && <p className="px-3 py-8 text-center text-[13px] text-muted">没有找到包含「{q}」的内容</p>}
        </div>
      </div>
    </div>
  );
}
