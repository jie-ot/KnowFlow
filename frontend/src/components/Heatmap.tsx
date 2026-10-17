"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { parseDay, shiftDay, weekdayIndex } from "@/lib/format";

type Day = { day: string; total: number; good: number };

const CELL = 11;
const GAP = 3;
const LABEL_W = 14; // 左侧“一 三 五”
const LEVEL_MIX = [0, 28, 48, 72, 100]; // 学科绿在空白格上的浓度

const levelColor = (level: number) =>
  level === 0 ? "var(--hover)" : `color-mix(in oklab, var(--accent) ${LEVEL_MIX[level]}%, var(--paper))`;

/**
 * 复习热力图：每格一天，颜色越深当天复习越多。
 * 周数随容器宽度自动调整（最多 53 周），所以放在窄栏里显示近半年，宽栏里显示近一年。
 */
export function Heatmap({ series, today, streak, accuracy }: {
  series: Day[]; today: string; streak: number; accuracy: number | null;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [weeks, setWeeks] = useState(26);
  const [hover, setHover] = useState<{ d: Day; x: number; y: number } | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const n = Math.floor((e.contentRect.width - LABEL_W - 8 + GAP) / (CELL + GAP));
      setWeeks(Math.max(8, Math.min(53, n)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const { cols, months, max, sum, longest } = useMemo(() => {
    const byDay = new Map(series.map((s) => [s.day, s]));
    const start = shiftDay(today, -weekdayIndex(parseDay(today)) - (weeks - 1) * 7);
    const cols: (Day | null)[][] = [];
    const months: { col: number; label: string }[] = [];
    const visible: Day[] = [];
    for (let w = 0; w < weeks; w++) {
      const col: (Day | null)[] = [];
      for (let i = 0; i < 7; i++) {
        const day = shiftDay(start, w * 7 + i);
        if (day > today) { col.push(null); continue; }
        const d = byDay.get(day) || { day, total: 0, good: 0 };
        col.push(d);
        visible.push(d);
        const date = parseDay(day);
        if (date.getDate() === 1 && w > 0) months.push({ col: w, label: `${date.getMonth() + 1}月` });
      }
      cols.push(col);
    }
    let longest = 0;
    let run = 0;
    for (const d of visible) {
      run = d.total > 0 ? run + 1 : 0;
      longest = Math.max(longest, run);
    }
    return {
      cols, months, longest,
      max: Math.max(1, ...visible.map((d) => d.total)),
      sum: visible.reduce((n, d) => n + d.total, 0),
    };
  }, [series, today, weeks]);

  const level = (n: number) => (n === 0 ? 0 : Math.min(4, Math.max(1, Math.ceil((n / max) * 4))));
  const label = (d: Day) => {
    const date = parseDay(d.day);
    const head = `${date.getMonth() + 1}月${date.getDate()}日 周${"日一二三四五六"[date.getDay()]}`;
    return d.total ? `${head} · 复习 ${d.total} 张，记得 ${d.good} 张` : `${head} · 没有复习`;
  };
  const span = weeks >= 50 ? "近一年" : weeks >= 24 ? `近 ${Math.round(weeks / 4.35)} 个月` : `近 ${weeks} 周`;

  return (
    <div ref={box} className="relative">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="section-title mb-0">复习足迹</h2>
        <span className="text-[12px] text-muted">{span}复习 <b className="font-semibold text-ink tabular-nums">{sum}</b> 次</span>
      </div>
      <div className="flex gap-2">
        <div className="grid text-[10px] text-faint leading-none pt-4 shrink-0"
          style={{ width: LABEL_W, gridTemplateRows: `repeat(7, ${CELL}px)`, rowGap: GAP }}>
          {["一", "", "三", "", "五", "", ""].map((t, i) => <span key={i} className="flex items-center">{t}</span>)}
        </div>
        <div>
          <div className="relative h-4 text-[10.5px] text-faint">
            {months.map((m) => (
              <span key={m.col} className="absolute top-0 whitespace-nowrap" style={{ left: m.col * (CELL + GAP) }}>{m.label}</span>
            ))}
          </div>
          <div className="grid grid-flow-col" role="img" aria-label={`${span}每天的复习数量`}
            style={{ gridTemplateRows: `repeat(7, ${CELL}px)`, gridAutoColumns: `${CELL}px`, gap: GAP }}
            onMouseLeave={() => setHover(null)}>
            {cols.flatMap((col, w) => col.map((d, i) => d ? (
              <span key={d.day} className="rounded-[3px] hover:outline hover:outline-1 hover:outline-[var(--ink)]"
                style={{ background: levelColor(level(d.total)), outlineOffset: 1 }}
                onMouseEnter={(e) => {
                  const outer = box.current!.getBoundingClientRect();
                  const r = e.currentTarget.getBoundingClientRect();
                  setHover({ d, x: r.left - outer.left + CELL / 2, y: r.top - outer.top });
                }} />
            ) : <span key={`${w}-${i}`} />))}
          </div>
        </div>
      </div>
      {hover && (
        <div className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full px-2.5 py-1.5 rounded-lg text-[12px] whitespace-nowrap shadow-lg"
          style={{ left: hover.x, top: hover.y - 6, background: "var(--ink)", color: "var(--paper)" }}>
          {label(hover.d)}
        </div>
      )}
      <div className="flex items-center justify-between gap-3 mt-3 text-[12px] text-muted">
        <span className="flex flex-wrap gap-x-3">
          <span>连续 <b className="font-semibold text-ink tabular-nums">{streak}</b> 天</span>
          <span>最长 {longest} 天</span>
          {accuracy !== null && <span>近 7 天记得 {Math.round(accuracy * 100)}%</span>}
        </span>
        <span className="flex items-center gap-[3px] text-[11px] text-faint shrink-0">
          少{[0, 1, 2, 3, 4].map((l) => <span key={l} className="w-[10px] h-[10px] rounded-[2px]" style={{ background: levelColor(l) }} />)}多
        </span>
      </div>
    </div>
  );
}
