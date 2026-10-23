"use client";

import {
  forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY,
  Simulation, SimulationLinkDatum, SimulationNodeDatum,
} from "d3-force";
import { ArrowUpRight, Check, Layers, Link2, Maximize2, Minus, MousePointer2, Network, Plus, Search, Sparkles, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { api, ApiError, Course, fetcher, GraphData, GraphNode, RelationKind } from "@/lib/api";
import { colorOf } from "@/lib/format";
import { useAiConsent } from "@/components/ai";
import { Dot, Spinner, useToast } from "@/components/ui";
import { connectSuggestions } from "@/lib/graph-links";
import { isNoteListCacheKey } from "@/lib/note-list-cache";
import { directedLine, relationDescriptions, relationLabels } from "@/lib/graph-relations";

type Suggestion = { source: string; target: string; source_title: string; target_title: string; reason: string };

type SimNode = GraphNode & SimulationNodeDatum & { r: number };
type SimLink = SimulationLinkDatum<SimNode> & { kind: RelationKind };
type View = { x: number; y: number; k: number };

const radius = (n: GraphNode) => 5 + Math.sqrt(n.degree) * 3.2 + (n.card_count > 0 ? 1.5 : 0);
const idOf = (end: string | number | SimNode) => (typeof end === "object" ? end.id : String(end));

export default function GraphPage() {
  return (
    <Suspense>
      <Graph />
    </Suspense>
  );
}

function Graph() {
  const params = useSearchParams();
  const router = useRouter();
  const course = params.get("course");
  const requestedCenter = params.get("center");
  const requestedDepth = params.get("depth") === "2" ? 2 : 1;
  const graphParams = new URLSearchParams();
  if (course) graphParams.set("course_id", course);
  if (requestedCenter) { graphParams.set("center_id", requestedCenter); graphParams.set("depth", String(requestedDepth)); }
  else if (params.get("scope") !== "all") graphParams.set("auto_local", "true");
  const { data, error: graphError, isLoading: graphLoading, mutate } = useSWR<GraphData>(`/api/graph?${graphParams.toString()}`, fetcher);
  const { data: noteIndex } = useSWR<{ id: string; title: string; course_id: string }[]>("/api/notes/index", fetcher);
  const [centerQuery, setCenterQuery] = useState("");
  const setRange = (centerId: string | null, depth = requestedDepth) => {
    const next = new URLSearchParams(params.toString());
    if (centerId) { next.set("center", centerId); next.set("depth", String(depth)); next.delete("scope"); }
    else { next.delete("center"); next.delete("depth"); next.set("scope", "all"); }
    router.replace(`/graph?${next.toString()}`);
  };
  const toast = useToast();
  const askConsent = useAiConsent();
  const [linking, setLinking] = useState(false);
  const [newKind, setNewKind] = useState<RelationKind>("related");
  const [typeSaving, setTypeSaving] = useState(false);
  const [linkDrag, setLinkDrag] = useState<{ from: string; x: number; y: number } | null>(null);
  const [edge, setEdge] = useState<{ source: string; target: string } | null>(null);
  const [suggest, setSuggest] = useState<Suggestion[] | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const pendingLinks = useRef(new Set<string>());
  const [linkErrors, setLinkErrors] = useState<Record<string, string>>({});
  const [pick, setPick] = useState("");
  const titleOf = (id: string) => data?.nodes.find((n) => n.id === id)?.title || "";

  const refreshAfterLink = (ids: string[]) => {
    mutate();
    globalMutate(isNoteListCacheKey);
    ids.forEach((id) => { globalMutate(`/api/notes/${id}`); globalMutate(`/api/notes/${id}/links`); });
  };
  const addLink = async (from: string, to: string, quiet = false, kind?: RelationKind) => {
    const key = `${from}:${to}`;
    if (pendingLinks.current.has(key)) return false;
    pendingLinks.current.add(key);
    try {
      const r = await api<{ changed: boolean }>(`/api/notes/${from}/links`, { method: "POST", json: { target_id: to, ...(kind ? { kind } : {}) } });
      if (!quiet) toast(r.changed ? `已连接：${titleOf(from)} → ${titleOf(to)}` : "这两篇已经连着了");
      refreshAfterLink([from, to]);
      setLinkErrors((errors) => { const next = { ...errors }; delete next[key]; return next; });
      return true;
    } catch (e) {
      setLinkErrors((errors) => ({ ...errors, [key]: (e as Error).message }));
      if (!quiet) toast((e as Error).message, "bad");
      return false;
    } finally { pendingLinks.current.delete(key); }
  };
  const changeType = async (from: string, to: string, kind: RelationKind) => {
    if (typeSaving) return;
    setTypeSaving(true);
    try {
      await api(`/api/notes/${from}/links/${to}`, { method: "PATCH", json: { kind } });
      refreshAfterLink([from, to]);
      toast("已更新关系类型");
    } catch (error) { toast((error as Error).message, "bad"); }
    finally { setTypeSaving(false); }
  };
  const removeLink = async (from: string, to: string) => {
    // 连线可能写在任意一边，先删 from → to，没有就删反方向
    const forward = data?.edges.some((x) => x.source === from && x.target === to);
    const [a, b] = forward ? [from, to] : [to, from];
    try {
      await api(`/api/notes/${a}/links/${b}`, { method: "DELETE" });
      toast(`已删除连线：${titleOf(a)} → ${titleOf(b)}`);
      setEdge(null);
      refreshAfterLink([a, b]);
    } catch (e) {
      toast((e as Error).message, "bad");
    }
  };
  const runSuggest = async () => {
    setSuggesting(true);
    setSelected(null);
    setEdge(null);
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const r = await api<{ links: Suggestion[]; message?: string }>("/api/graph/suggest", { method: "POST", json: { course_id: course } });
        setSuggest(r.links);
        if (!r.links.length) toast(r.message || "AI 没有找到需要补的连线");
        break;
      } catch (e) {
        const err = e as ApiError;
        if (err.code === "CONSENT_REQUIRED" && attempt === 0) {
          const ok = await askConsent({
            config_id: String(err.data.config_id), provider_host: String(err.data.provider_host), model: String(err.data.model),
            purpose: "推荐笔记之间的连线：会发送笔记标题、开头约 120 字和已有链接",
          });
          if (ok) continue;
        } else toast(err.code === "NO_CONFIG" ? "还没有连接大模型，先到设置里填写" : err.message, "bad");
        break;
      }
    }
    setSuggesting(false);
  };
  const acceptSuggestion = async (sg: Suggestion) => {
    if (connecting) return;
    setConnecting(true);
    const ok = await addLink(sg.source, sg.target, true);
    if (ok) setSuggest((xs) => (xs ? xs.filter((x) => x !== sg) : xs));
    setConnecting(false);
  };
  const { data: courses } = useSWR<Course[]>("/api/courses", fetcher);
  const courseMap = useMemo(() => Object.fromEntries((courses || []).map((c) => [c.id, c])), [courses]);

  const [hideIsolated, setHideIsolated] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [, setFrame] = useState(0);

  const svgRef = useRef<SVGSVGElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const sim = useRef<Simulation<SimNode, SimLink> | null>(null);
  const nodes = useRef<SimNode[]>([]);
  const links = useRef<SimLink[]>([]);
  const viewRef = useRef(view);
  viewRef.current = view;

  // 画布大小跟随窗口
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const fit = useCallback(() => {
    const ns = nodes.current;
    if (!ns.length || !boxRef.current) return;
    const { width: w, height: h } = boxRef.current.getBoundingClientRect();
    const xs = ns.map((n) => n.x ?? 0);
    const ys = ns.map((n) => n.y ?? 0);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const k = Math.max(0.35, Math.min(1.6, Math.min((w - 160) / (x1 - x0 || 1), (h - 160) / (y1 - y0 || 1))));
    setView({ k, x: -((x0 + x1) / 2) * k, y: -((y0 + y1) / 2) * k });
  }, []);

  // 建立力导向模拟；数据变化时保留已有节点的位置
  const hasEdges = (data?.edges.length || 0) > 0;
  useEffect(() => {
    if (!data) return;
    const prev = new Map(nodes.current.map((n) => [n.id, n]));
    const visible = hideIsolated && hasEdges ? data.nodes.filter((n) => n.degree > 0) : data.nodes;
    const ns: SimNode[] = visible.map((n) => ({ ...n, r: radius(n), x: prev.get(n.id)?.x, y: prev.get(n.id)?.y }));
    const ids = new Set(ns.map((n) => n.id));
    const ls: SimLink[] = data.edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ ...e }));
    // 同一学科的笔记聚在一起：每个学科一个“锚点”，排成一圈
    const groups = [...new Set(ns.map((n) => n.course_id))];
    const ring = groups.length > 1 ? 70 + groups.length * 40 : 0;
    const anchor = new Map(groups.map((c, i) => {
      const a = (i / groups.length) * Math.PI * 2 + Math.PI; // 从左边开始排，适合宽屏
      return [c, { x: Math.cos(a) * ring * 1.4, y: Math.sin(a) * ring * 0.8 }];
    }));
    let raf = 0;
    let fitted = prev.size > 0;
    const s = forceSimulation<SimNode>(ns)
      .force("link", forceLink<SimNode, SimLink>(ls).id((d) => d.id).distance(70).strength(0.5))
      .force("charge", forceManyBody<SimNode>().strength(-200).distanceMax(420))
      .force("collide", forceCollide<SimNode>().radius((d) => d.r + 16))
      .force("x", forceX<SimNode>((d) => anchor.get(d.course_id)!.x).strength(0.07))
      .force("y", forceY<SimNode>((d) => anchor.get(d.course_id)!.y).strength(0.07))
      .alphaDecay(0.035)
      .on("tick", () => {
        if (!raf) raf = requestAnimationFrame(() => { raf = 0; setFrame((f) => f + 1); });
        if (!fitted && s.alpha() < 0.12) { fitted = true; fit(); }
      });
    nodes.current = ns;
    links.current = ls;
    sim.current = s;
    return () => { s.stop(); cancelAnimationFrame(raf); };
  }, [data, hideIsolated, hasEdges, fit]);

  // 邻接表：悬停 / 选中时高亮相邻笔记
  const neighbors = useMemo(() => {
    const m = new Map<string, Set<string>>();
    data?.edges.forEach(({ source, target }) => {
      if (!m.has(source)) m.set(source, new Set());
      if (!m.has(target)) m.set(target, new Set());
      m.get(source)!.add(target);
      m.get(target)!.add(source);
    });
    return m;
  }, [data]);

  const focus = hovered || selected;
  const q = query.trim().toLowerCase();
  const matches = useMemo(() => new Set(q ? (data?.nodes || []).filter((n) => n.title.toLowerCase().includes(q)).map((n) => n.id) : []), [q, data]);
  const lit = (id: string) => {
    if (q) return matches.has(id);
    if (!focus) return true;
    return id === focus || !!neighbors.get(focus)?.has(id);
  };

  const center = (id: string) => {
    const n = nodes.current.find((x) => x.id === id);
    if (n) setView((v) => ({ ...v, x: -(n.x ?? 0) * v.k, y: -(n.y ?? 0) * v.k }));
  };

  // 滚轮缩放（以鼠标位置为中心）
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const px = e.clientX - rect.left - rect.width / 2;
      const py = e.clientY - rect.top - rect.height / 2;
      const v = viewRef.current;
      const k = Math.max(0.25, Math.min(3, v.k * Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015))));
      const wx = (px - v.x) / v.k;
      const wy = (py - v.y) / v.k;
      setView({ k, x: px - wx * k, y: py - wy * k });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setLinking(false); setLinkDrag(null); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const zoom = (f: number) => setView((v) => ({ ...v, k: Math.max(0.25, Math.min(3, v.k * f)), x: v.x * f, y: v.y * f }));

  // 拖动：空白处拖动平移画布，拖节点则移动节点
  const drag = useRef<{ kind: "pan" | "node" | "link"; id?: string; sx: number; sy: number; vx: number; vy: number; moved: boolean } | null>(null);
  const toWorld = (clientX: number, clientY: number) => {
    const rect = svgRef.current!.getBoundingClientRect();
    const v = viewRef.current;
    return { x: (clientX - rect.left - rect.width / 2 - v.x) / v.k, y: (clientY - rect.top - rect.height / 2 - v.y) / v.k };
  };
  const onPointerDown = (e: React.PointerEvent, id?: string) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    drag.current = { kind: id ? (linking ? "link" : "node") : "pan", id, sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y, moved: false };
  };
  const nodeAt = (clientX: number, clientY: number) => {
    const p = toWorld(clientX, clientY);
    return nodes.current.find((n) => Math.hypot((n.x ?? 0) - p.x, (n.y ?? 0) - p.y) <= n.r + 8 / viewRef.current.k);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.sx;
    const dy = e.clientY - d.sy;
    if (!d.moved && Math.hypot(dx, dy) < 4) return;
    d.moved = true;
    if (d.kind === "link") {
      const p = toWorld(e.clientX, e.clientY);
      setLinkDrag({ from: d.id!, x: p.x, y: p.y });
      const over = nodeAt(e.clientX, e.clientY);
      setHovered(over && over.id !== d.id ? over.id : null);
    } else if (d.kind === "pan") {
      setView((v) => ({ ...v, x: d.vx + dx, y: d.vy + dy }));
    } else {
      const n = nodes.current.find((x) => x.id === d.id);
      if (!n) return;
      const p = toWorld(e.clientX, e.clientY);
      n.fx = p.x;
      n.fy = p.y;
      sim.current?.alphaTarget(0.25).restart();
    }
  };
  const onPointerUp = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (d.kind === "link") {
      setLinkDrag(null);
      const target = d.moved ? nodeAt(e.clientX, e.clientY) : undefined;
      if (target && target.id !== d.id) addLink(d.id!, target.id, false, newKind);
      else if (!d.moved) { setSelected(d.id!); setEdge(null); }
      return;
    }
    if (d.kind === "node") {
      const n = nodes.current.find((x) => x.id === d.id);
      if (n) { n.fx = null; n.fy = null; }
      sim.current?.alphaTarget(0);
      if (!d.moved && d.id) { setSelected((s) => (s === d.id ? null : d.id!)); setEdge(null); setPick(""); }
    } else if (!d.moved) {
      setSelected(null);
      setEdge(null);
    }
  };

  const ns = nodes.current;
  const ls = links.current;
  const many = ns.length > 60;
  const sel = selected ? (data?.nodes.find((n) => n.id === selected) ?? null) : null;
  const selNeighbors = sel ? [...(neighbors.get(sel.id) || [])].map((id) => data!.nodes.find((n) => n.id === id)).filter(Boolean) as GraphNode[] : [];

  const setCourse = (c: string | null) => {
    setSelected(null);
    nodes.current = [];
    router.replace(c ? `/graph?course=${c}` : "/graph");
  };

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-3 h-14 px-6 border-b border-rule shrink-0">
        <Network size={17} className="text-accent" />
        <h1 className="text-[16px] font-semibold">知识图谱{data?.center_id ? " · 局部" : " · 全图"}</h1>
        {data && (
          <span className="text-[12.5px] text-muted">
            {data.nodes.length} 篇笔记 · {data.edges.length} 条链接
            {data.unresolved > 0 && <span className="text-faint"> · {data.unresolved} 个链接尚未解析{data.ambiguous > 0 ? `（${data.ambiguous} 个同名目标需到笔记中选择）` : ""}</span>}
          </span>
        )}
        <span className="flex-1" />
        <label className="flex items-center gap-2 text-[12px] text-muted" title={relationDescriptions[newKind]}>新连线类型
          <select aria-label="新连线关系类型" className="input h-7 w-28 text-[12px]" value={newKind} onChange={(event) => setNewKind(event.target.value as RelationKind)}>
            {Object.entries(relationLabels).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}
          </select>
        </label>
        <div className="relative w-52">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" />
          <input className="input h-8 pl-7 text-[13px]" placeholder="找一篇笔记" value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                const first = [...matches][0];
                if (first) { setSelected(first); center(first); }
              }
              if (e.key === "Escape") setQuery("");
            }} />
        </div>
        <button className={`btn ${linking ? "btn-primary" : "btn-outline"}`} onClick={() => { setLinking((v) => !v); setSuggest(null); }}
          title="打开后，从一篇笔记拖到另一篇就能连线">
          {linking ? <MousePointer2 size={14} /> : <Link2 size={14} />}{linking ? "退出连线" : "手动连线"}
        </button>
        <button className="btn btn-outline" onClick={runSuggest} disabled={suggesting || (data?.nodes.length || 0) < 2}>
          {suggesting ? <Spinner /> : <Sparkles size={14} className="text-accent" />}AI 推荐连线
        </button>
        <label className="flex items-center gap-1.5 text-[12.5px] text-muted cursor-pointer select-none">
          <input type="checkbox" className="accent-[var(--accent)]" checked={hideIsolated} disabled={!hasEdges}
            onChange={(e) => setHideIsolated(e.target.checked)} />
          隐藏孤立笔记
        </label>
      </div>

      <div className="flex items-center gap-3 px-6 py-2 border-b border-rule text-[12.5px] flex-wrap">
        {data?.center_id && <span className="text-muted">{`以「${noteIndex?.find((note) => note.id === data.center_id)?.title || "所选笔记"}」为中心 ${data.depth} 层，显示 ${data.nodes.length} / ${data.total_nodes} 篇；连线保留原方向。`}</span>}
        {data?.center_id && <><button className="btn h-7" disabled={data.depth === 1} onClick={() => setRange(data.center_id!, 1)}>一层</button><button className="btn h-7" disabled={data.depth === 2} onClick={() => setRange(data.center_id!, 2)}>两层</button><button className="btn h-7" onClick={() => setRange(null)}>查看全图</button></>}
        <span className="flex-1" />
        <label className="flex items-center gap-2">局部中心
          <input className="input h-7 w-40 text-[12px]" placeholder="筛选全部笔记" value={centerQuery} onChange={(event) => setCenterQuery(event.target.value)} />
          <select className="input h-7 max-w-52 text-[12px]" aria-label="选择局部图中心" value={data?.center_id || ""} onChange={(event) => { if (event.target.value) setRange(event.target.value); }}>
            <option value="">选择笔记…</option>
            {(noteIndex || []).filter((note) => (!course || note.course_id === course) && (!centerQuery || note.title.toLowerCase().includes(centerQuery.toLowerCase()))).map((note) => <option key={note.id} value={note.id}>{note.title} · {note.id.slice(0, 8)}</option>)}
          </select>
        </label>
        {graphLoading && <span className="text-muted">正在载入图谱…</span>}
        {graphError && <span role="alert" className="text-warn">{(graphError as Error).message} <button className="underline" onClick={() => setRange(null)}>返回全图</button></span>}
      </div>

      <div className="flex items-center gap-1.5 px-6 py-2 border-b border-rule shrink-0 overflow-x-auto">
        <button onClick={() => setCourse(null)} className="chip hover:text-ink shrink-0"
          style={!course ? { background: "var(--accent-soft)", color: "var(--accent)" } : undefined}>全部学科</button>
        {courses?.filter((c) => c.note_count > 0).map((c) => (
          <button key={c.id} onClick={() => setCourse(c.id)} className="chip hover:text-ink shrink-0"
            style={course === c.id ? { background: "var(--accent-soft)", color: "var(--accent)" } : undefined}>
            <Dot color={c.color} size={7} />{c.name}
          </button>
        ))}
      </div>

      <div className="flex-1 min-h-0 flex">
        <div ref={boxRef} className="relative flex-1 min-w-0 overflow-hidden bg-canvas"
          style={{ backgroundImage: "radial-gradient(var(--rule) 1px, transparent 1px)", backgroundSize: `${22 * view.k}px ${22 * view.k}px`, backgroundPosition: `${size.w / 2 + view.x}px ${size.h / 2 + view.y}px` }}>
          <svg ref={svgRef} width={size.w} height={size.h} className="block touch-none select-none"
            style={{ cursor: linking ? "crosshair" : drag.current?.kind === "pan" ? "grabbing" : "grab" }}
            onPointerDown={(e) => onPointerDown(e)} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
            <defs>{[["relation-arrow", "var(--muted)"], ["relation-arrow-accent", "var(--accent)"], ["relation-arrow-selected", "var(--bad)"]].map(([id, color]) => (
              <marker key={id} id={id} viewBox="0 0 10 10" refX="5" refY="5" markerWidth={9 / Math.max(view.k, 0.6)} markerHeight={9 / Math.max(view.k, 0.6)} orient="auto" markerUnits="userSpaceOnUse">
                <path d="M 2 1 L 8 5 L 2 9" fill="none" stroke={color} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" strokeDasharray="none" />
              </marker>
            ))}</defs>
            <g transform={`translate(${size.w / 2 + view.x},${size.h / 2 + view.y}) scale(${view.k})`}>
              {ls.map((l) => {
                const s = l.source as SimNode;
                const t = l.target as SimNode;
                if (s.x === undefined || s.y === undefined || t.x === undefined || t.y === undefined) return null;
                const line = directedLine({ x: s.x, y: s.y, r: s.r }, { x: t.x, y: t.y, r: t.r });
                if (!line) return null;
                const on = (focus || q) ? lit(idOf(s)) && lit(idOf(t)) && (q ? true : idOf(s) === focus || idOf(t) === focus) : true;
                const picked = edge && edge.source === idOf(s) && edge.target === idOf(t);
                const highlighted = on && !!(focus || q);
                return (
                  <g key={`${idOf(s)}-${idOf(t)}`}>
                    <path d={line.d} fill="none" markerMid={`url(#${picked ? "relation-arrow-selected" : highlighted ? "relation-arrow-accent" : "relation-arrow"})`}
                      stroke={picked ? "var(--bad)" : highlighted ? "var(--accent)" : "var(--faint)"}
                      opacity={picked ? 1 : on ? (focus ? 0.8 : 0.45) : 0.08}
                      strokeWidth={(picked ? 2.4 : on && focus ? 1.6 : 1) / Math.max(view.k, 0.6)} />
                    <title>{s.title} → {t.title} · {relationLabels[l.kind]}（{relationDescriptions[l.kind]}）</title>
                    <path d={line.d} fill="none" stroke="transparent" strokeWidth={12 / view.k} pointerEvents="stroke" className="cursor-pointer"
                      onPointerDown={(e) => { e.stopPropagation(); setEdge({ source: idOf(s), target: idOf(t) }); setSelected(null); setSuggest(null); }} />
                  </g>
                );
              })}
              {suggest?.map((sg) => {
                const a = ns.find((n) => n.id === sg.source);
                const b = ns.find((n) => n.id === sg.target);
                if (!a || !b || a.x === undefined || a.y === undefined || b.x === undefined || b.y === undefined) return null;
                const line = directedLine({ x: a.x, y: a.y, r: a.r }, { x: b.x, y: b.y, r: b.r });
                if (!line) return null;
                return <path key={`sg-${sg.source}-${sg.target}`} d={line.d} fill="none" stroke="var(--accent)" markerMid="url(#relation-arrow-accent)"
                  strokeWidth={1.6 / Math.max(view.k, 0.6)} strokeDasharray={`${5 / view.k} ${4 / view.k}`} />;
              })}
              {linkDrag && (() => {
                const a = ns.find((n) => n.id === linkDrag.from);
                if (!a || a.x === undefined || a.y === undefined) return null;
                const line = directedLine({ x: a.x, y: a.y, r: a.r }, { x: linkDrag.x, y: linkDrag.y, r: 0 });
                return line ? <path d={line.d} fill="none" stroke="var(--accent)" markerMid="url(#relation-arrow-accent)" strokeWidth={2 / view.k}
                  strokeDasharray={`${6 / view.k} ${4 / view.k}`} /> : null;
              })()}
              {ns.map((n) => {
                if (n.x === undefined || n.y === undefined) return null;
                const on = lit(n.id);
                const isSel = n.id === selected;
                const showLabel = on && (!many || view.k >= 1.1 || n.degree >= 3 || n.id === focus || (!!focus && on) || (!!q && on));
                return (
                  <g key={n.id} transform={`translate(${n.x},${n.y})`} className={linking ? "cursor-crosshair" : "cursor-pointer"}
                    style={{ opacity: on ? 1 : 0.18, transition: "opacity .15s" }}
                    onPointerDown={(e) => onPointerDown(e, n.id)}
                    onPointerEnter={() => !drag.current && setHovered(n.id)}
                    onPointerLeave={() => setHovered((h) => (h === n.id ? null : h))}
                    onDoubleClick={() => router.push(`/notes?id=${n.id}`)}>
                    {isSel && <circle r={n.r + 5} fill="none" stroke="var(--accent)" strokeWidth={2} />}
                    <circle r={n.r} fill={colorOf(courseMap[n.course_id]?.color)} stroke="var(--paper)" strokeWidth={1.8} />
                    {showLabel && (
                      <text y={n.r + 13} textAnchor="middle" fontSize={11.5 / Math.max(view.k, 0.75)}
                        fill={n.id === focus || isSel ? "var(--ink)" : "var(--muted)"} fontWeight={n.id === focus || isSel ? 600 : 400}
                        stroke="var(--canvas)" strokeWidth={3 / Math.max(view.k, 0.75)} paintOrder="stroke" strokeLinejoin="round">
                        {n.title.length > 18 ? n.title.slice(0, 17) + "…" : n.title}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          </svg>

          <div className="absolute left-4 bottom-4 flex flex-col rounded-xl border border-rule bg-paper shadow-sm overflow-hidden">
            <button className="btn btn-icon rounded-none" onClick={() => zoom(1.25)} aria-label="放大"><Plus size={15} /></button>
            <button className="btn btn-icon rounded-none border-y border-rule" onClick={() => zoom(0.8)} aria-label="缩小"><Minus size={15} /></button>
            <button className="btn btn-icon rounded-none" onClick={fit} aria-label="适应窗口" title="适应窗口"><Maximize2 size={14} /></button>
          </div>

          {data && data.nodes.length === 0 && (
            <div className="absolute inset-0 grid place-items-center text-center px-6">
              <div>
                <p className="text-[15px] font-medium">这里还没有笔记</p>
                <p className="text-[13px] text-muted mt-1">写几篇笔记，用 [[ ]] 把它们连起来，就会长成一张网。</p>
                <Link href="/notes" className="btn btn-primary mt-4">去写笔记</Link>
              </div>
            </div>
          )}
          {data && data.nodes.length > 0 && !hasEdges && (
            <div className="absolute left-1/2 -translate-x-1/2 bottom-5 max-w-[460px] px-4 py-3 rounded-xl border border-rule bg-paper shadow-sm text-[13px] text-muted">
              笔记之间还没有链接。点右上角“手动连线”，把一篇笔记拖到另一篇上就能连上；也可以让“AI 推荐连线”帮你找。
            </div>
          )}
          {linking && (
            <div className="absolute left-1/2 -translate-x-1/2 top-4 px-4 py-2 rounded-xl bg-ink text-paper text-[13px] shadow-lg anim-fade">
              连线模式：从一篇笔记<b className="mx-1">按住拖到</b>另一篇松开即可连上；点一条线可以删除。按 Esc 退出
            </div>
          )}
          {data && hasEdges && !sel && !linking && (
            <p className="absolute right-4 bottom-4 text-[11.5px] text-faint">滚轮缩放 · 拖动空白处平移 · 单击查看 · 双击打开</p>
          )}
        </div>

        {suggest && suggest.length > 0 && (
          <aside className="w-[320px] shrink-0 border-l border-rule bg-paper overflow-y-auto anim-fade">
            <div className="p-5">
              <div className="flex items-center gap-2">
                <Sparkles size={15} className="text-accent" />
                <h2 className="flex-1 text-[15px] font-semibold">AI 推荐的连线</h2>
                <button className="btn btn-icon -mr-2 text-muted" onClick={() => setSuggest(null)} aria-label="关闭"><X size={15} /></button>
              </div>
              <p className="text-[12.5px] text-muted mt-1.5 leading-relaxed">图上虚线就是推荐的连线。连上后会在前一篇笔记末尾的“相关笔记：”里加上链接。</p>
              <ul className="mt-4 space-y-2.5">
                {suggest.map((sg) => (
                  <li key={sg.source + sg.target} className="rounded-xl border border-rule p-3"
                    onMouseEnter={() => setHovered(sg.source)} onMouseLeave={() => setHovered(null)}>
                    <p className="text-[13.5px] font-medium leading-snug">{sg.source_title} <span className="text-faint">→</span> {sg.target_title}</p>
                    {sg.reason && <p className="text-[12.5px] text-muted mt-1 leading-relaxed">{sg.reason}</p>}
                    {linkErrors[`${sg.source}:${sg.target}`] && <p role="alert" className="text-[12px] text-warn mt-2">连接失败：{linkErrors[`${sg.source}:${sg.target}`]}，可重试。</p>}
                    <div className="flex gap-1.5 mt-2.5">
                      <button className="btn btn-primary h-7 text-[12px]" disabled={connecting} onClick={() => acceptSuggestion(sg)}><Check size={13} />{linkErrors[`${sg.source}:${sg.target}`] ? "重试" : "连上"}</button>
                      <button className="btn h-7 text-[12px] text-muted" onClick={() => setSuggest((xs) => xs && xs.filter((x) => x !== sg))}>忽略</button>
                    </div>
                  </li>
                ))}
              </ul>
              <button className="btn btn-outline w-full mt-4" disabled={connecting} onClick={async () => {
                if (connecting) return;
                setConnecting(true);
                const result = await connectSuggestions(suggest, (sg) => addLink(sg.source, sg.target, true));
                const succeeded = new Set(result.successes);
                setSuggest((items) => items?.filter((item) => !succeeded.has(item)) || null);
                toast(`成功 ${result.successes.length} 条，失败 ${result.failures.length} 条${result.failures.length ? "，失败项已保留，可重试" : ""}`, result.failures.length ? "bad" : "ok");
                setConnecting(false);
              }}>{connecting ? "正在连接…" : "全部连上"}</button>
            </div>
          </aside>
        )}
        {edge && !suggest?.length && (
          <aside className="w-[300px] shrink-0 border-l border-rule bg-paper anim-fade">
            <div className="p-5">
              <div className="flex items-start gap-2">
                <h2 className="flex-1 text-[15px] font-semibold">这条连线</h2>
                <button className="btn btn-icon -mr-2 -mt-1 text-muted" onClick={() => setEdge(null)} aria-label="关闭"><X size={15} /></button>
              </div>
              <p className="mt-3 text-[14px] leading-relaxed">
                <Link href={`/notes?id=${edge.source}`} className="font-medium hover:text-accent">{titleOf(edge.source)}</Link>
                <span className="text-faint"> → </span>
                <Link href={`/notes?id=${edge.target}`} className="font-medium hover:text-accent">{titleOf(edge.target)}</Link>
              </p>
              <label className="block text-[12px] text-muted mt-4">关系类型
                <select aria-label="修改关系类型" className="input mt-2" disabled={typeSaving} value={data?.edges.find((item) => item.source === edge.source && item.target === edge.target)?.kind || "related"}
                  onChange={(event) => changeType(edge.source, edge.target, event.target.value as RelationKind)}>
                  {Object.entries(relationLabels).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}
                </select>
              </label>
              <p className="text-[12px] text-muted mt-2">{relationDescriptions[data?.edges.find((item) => item.source === edge.source && item.target === edge.target)?.kind || "related"]}。A 为箭头起点，B 为箭头终点。</p>
              <button className="btn btn-outline w-full mt-5 text-bad" onClick={() => removeLink(edge.source, edge.target)}>
                <Trash2 size={14} />删除这条连线
              </button>
              <p className="text-[12px] text-faint mt-2 leading-relaxed">“相关笔记：”里的链接会直接删掉；写在正文中间的会改回普通文字，正文内容不丢。</p>
            </div>
          </aside>
        )}
        {sel && !edge && !suggest?.length && (
          <aside className="w-[300px] shrink-0 border-l border-rule bg-paper overflow-y-auto anim-fade">
            <div className="p-5">
              <div className="flex items-start gap-2">
                <h2 className="flex-1 font-serif text-[21px] leading-snug">{sel.title}</h2>
                <button className="btn btn-icon -mr-2 -mt-1 text-muted" onClick={() => setSelected(null)} aria-label="关闭"><X size={15} /></button>
              </div>
              <p className="flex items-center gap-3 mt-2 text-[12.5px] text-muted">
                <span className="flex items-center gap-1.5"><Dot color={courseMap[sel.course_id]?.color} size={7} />{courseMap[sel.course_id]?.name}</span>
                <span className="flex items-center gap-1"><Link2 size={12} />{sel.degree}</span>
                {sel.card_count > 0 && <span className="flex items-center gap-1"><Layers size={12} />{sel.card_count} 张卡片</span>}
              </p>
              {sel.excerpt && <p className="mt-4 text-[13.5px] leading-relaxed text-muted">{sel.excerpt}</p>}
              <Link href={`/notes?id=${sel.id}`} className="btn btn-primary w-full mt-5"><ArrowUpRight size={14} />打开笔记</Link>
              <button className="btn btn-outline w-full mt-2" onClick={() => setRange(sel.id)}>以此笔记查看局部图</button>
              <h3 className="section-title mt-7 mb-2">连到另一篇笔记</h3>
              <input className="input h-8 text-[13px]" placeholder="输入标题筛选" value={pick} onChange={(e) => setPick(e.target.value)} />
              {pick.trim() && (
                <ul className="mt-1.5 space-y-0.5">
                  {(data?.nodes || []).filter((n) => n.id !== sel.id && !neighbors.get(sel.id)?.has(n.id)
                    && n.title.toLowerCase().includes(pick.trim().toLowerCase())).slice(0, 6).map((n) => (
                    <li key={n.id}>
                      <button className="w-full flex items-center gap-2 h-8 px-2 -mx-2 rounded-lg text-[13px] text-left row-hover"
                        onClick={async () => { if (await addLink(sel.id, n.id, false, newKind)) setPick(""); }}>
                        <Plus size={13} className="text-accent" /><span className="flex-1 truncate">{n.title}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <h3 className="section-title mt-7 mb-2">相连的笔记</h3>
              {selNeighbors.length === 0 && <p className="text-[13px] text-faint">还没有和其他笔记相连。</p>}
              <ul className="space-y-0.5">
                {selNeighbors.map((n) => (
                  <li key={n.id}>
                    <div className="group flex items-center -mx-2">
                      <button className="flex-1 min-w-0 flex items-center gap-2 h-9 px-2 rounded-lg text-[13.5px] text-left row-hover"
                        onClick={() => { setSelected(n.id); center(n.id); }}
                        onMouseEnter={() => setHovered(n.id)} onMouseLeave={() => setHovered(null)}>
                        <Dot color={courseMap[n.course_id]?.color} size={7} />
                        <span className="flex-1 truncate">{n.title}</span>
                      </button>
                      <button className="btn btn-icon h-7 w-7 text-muted opacity-0 group-hover:opacity-100" title="删除连线"
                        onClick={() => removeLink(sel.id, n.id)} aria-label="删除连线"><X size={13} /></button>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
