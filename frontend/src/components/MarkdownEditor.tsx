"use client";

import { FilePlus2, Link2 } from "lucide-react";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";

export type TitleOption = { id: string; title: string; color?: string };

type Props = {
  value: string;
  onChange: (v: string) => void;
  titles: TitleOption[];
  readOnly?: boolean;
  onSave?: () => void;
  placeholder?: string;
};

export type EditorHandle = { focus: () => void };

const LIST_RE = /^(\s*)([-*+] \[[ x]\] |[-*+] |(\d+)\. |> )(.*)$/;

/** 用 execCommand 插入文字：浏览器会把它记进撤销栈，⌘Z 可以撤回 */
function replaceRange(el: HTMLTextAreaElement, start: number, end: number, text: string) {
  el.focus();
  el.setSelectionRange(start, end);
  const ok = text ? document.execCommand("insertText", false, text) : start === end || document.execCommand("delete");
  if (!ok) {
    el.setRangeText(text, start, end, "end");
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
  const pos = start + text.length;
  el.setSelectionRange(pos, pos);
}

/** 计算光标在页面上的位置（镜像 div 法），用来摆放 [[ 补全菜单 */
function caretPoint(el: HTMLTextAreaElement, pos: number) {
  const div = document.createElement("div");
  const style = getComputedStyle(el);
  for (const prop of ["fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing", "paddingTop",
    "paddingLeft", "paddingRight", "borderLeftWidth", "boxSizing", "whiteSpace", "wordBreak", "overflowWrap"] as const) {
    div.style[prop] = style[prop];
  }
  div.style.position = "absolute";
  div.style.visibility = "hidden";
  div.style.whiteSpace = "pre-wrap";
  div.style.width = `${el.clientWidth}px`;
  div.textContent = el.value.slice(0, pos);
  const mark = document.createElement("span");
  mark.textContent = "​";
  div.appendChild(mark);
  document.body.appendChild(div);
  const rect = el.getBoundingClientRect();
  const lh = parseFloat(style.lineHeight) || 26;
  const point = { x: rect.left + mark.offsetLeft, y: rect.top + mark.offsetTop - el.scrollTop + lh, lh };
  document.body.removeChild(div);
  return point;
}

export const MarkdownEditor = forwardRef<EditorHandle, Props>(function MarkdownEditor(
  { value, onChange, titles, readOnly, onSave, placeholder }, ref,
) {
  const el = useRef<HTMLTextAreaElement>(null);
  const [ac, setAc] = useState<{ start: number; query: string; x: number; y: number; lh: number } | null>(null);
  const [index, setIndex] = useState(0);
  useImperativeHandle(ref, () => ({ focus: () => el.current?.focus() }), []);

  const matches = useMemo(() => {
    if (!ac) return [];
    const q = ac.query.trim().toLowerCase();
    return titles.filter((t) => !q || t.title.toLowerCase().includes(q)).slice(0, 8);
  }, [ac, titles]);

  const checkAutocomplete = useCallback(() => {
    const t = el.current;
    if (!t || t.selectionStart !== t.selectionEnd) return setAc(null);
    const before = t.value.slice(0, t.selectionStart);
    const m = before.match(/\[\[([^[\]\n|]{0,50})$/);
    if (!m) return setAc(null);
    const start = t.selectionStart - m[1].length;
    const p = caretPoint(t, start);
    setAc({ start, query: m[1], ...p });
    setIndex(0);
  }, []);

  const pick = (title: string) => {
    const t = el.current;
    if (!t || !ac) return;
    const after = t.value.slice(t.selectionStart);
    const close = after.startsWith("]]") ? "" : "]]";
    replaceRange(t, ac.start, t.selectionStart, title + close);
    if (!close) t.setSelectionRange(t.selectionStart + 2, t.selectionStart + 2);
    setAc(null);
  };

  const wrap = (mark: string) => {
    const t = el.current!;
    const { selectionStart: s, selectionEnd: e } = t;
    const selected = t.value.slice(s, e);
    replaceRange(t, s, e, mark + selected + mark);
    if (!selected) t.setSelectionRange(s + mark.length, s + mark.length);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const t = e.currentTarget;
    const mod = e.metaKey || e.ctrlKey;

    if (ac && (matches.length > 0 || ac.query.trim())) {
      const total = matches.length + (ac.query.trim() && !matches.some((m) => m.title.toLowerCase() === ac.query.trim().toLowerCase()) ? 1 : 0);
      if (e.key === "ArrowDown") { e.preventDefault(); setIndex((i) => (i + 1) % Math.max(total, 1)); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setIndex((i) => (i - 1 + total) % Math.max(total, 1)); return; }
      if (e.key === "Escape") { e.preventDefault(); setAc(null); return; }
      if ((e.key === "Enter" || e.key === "Tab") && total > 0) {
        e.preventDefault();
        pick(index < matches.length ? matches[index].title : ac.query.trim());
        return;
      }
    }

    if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); onSave?.(); return; }
    if (mod && e.key.toLowerCase() === "b") { e.preventDefault(); wrap("**"); return; }
    if (mod && e.key.toLowerCase() === "i") { e.preventDefault(); wrap("*"); return; }

    const { selectionStart: s, selectionEnd: end, value: v } = t;
    const lineStart = v.lastIndexOf("\n", s - 1) + 1;

    if (e.key === "Tab") {
      e.preventDefault();
      const blockEnd = v.indexOf("\n", end) === -1 ? v.length : v.indexOf("\n", end);
      const original = v.slice(lineStart, blockEnd);
      const lines = original.split("\n");
      const multi = lines.length > 1;
      if (!multi && !e.shiftKey && !LIST_RE.test(lines[0])) {
        replaceRange(t, s, end, "  "); // 普通文字：插入两个空格
        return;
      }
      const next = lines.map((l) => (e.shiftKey ? l.replace(/^( {1,2}|\t)/, "") : "  " + l)).join("\n");
      if (next === original) return;
      replaceRange(t, lineStart, blockEnd, next);
      if (multi) t.setSelectionRange(lineStart, lineStart + next.length);
      else {
        const caret = Math.max(lineStart, s + next.length - original.length);
        t.setSelectionRange(caret, caret);
      }
      return;
    }

    if (e.key === "Enter" && !e.shiftKey && !mod && s === end && !e.nativeEvent.isComposing) {
      const line = v.slice(lineStart, s);
      const m = line.match(LIST_RE);
      if (m) {
        e.preventDefault();
        const [, indent, marker, num, rest] = m;
        if (!rest.trim()) {
          replaceRange(t, lineStart, s, ""); // 空的列表项：结束列表
          return;
        }
        const nextMarker = num ? `${Number(num) + 1}. ` : marker.replace("[x]", "[ ]");
        replaceRange(t, s, s, "\n" + indent + nextMarker);
      }
    }
  };

  useEffect(() => {
    const close = () => setAc(null);
    window.addEventListener("scroll", close, true);
    return () => window.removeEventListener("scroll", close, true);
  }, []);

  const showCreate = ac && ac.query.trim() && !matches.some((m) => m.title.toLowerCase() === ac.query.trim().toLowerCase());
  // 菜单默认在光标下一行；下方放不下就放到上方
  const menuH = 34 + (matches.length + (showCreate ? 1 : 0)) * 32;
  const menuTop = ac ? (ac.y + 4 + menuH > window.innerHeight - 8 ? ac.y - ac.lh - menuH - 4 : ac.y + 4) : 0;

  return (
    <>
      <textarea
        ref={el}
        value={value}
        readOnly={readOnly}
        spellCheck={false}
        placeholder={placeholder}
        onChange={(e) => { onChange(e.target.value); requestAnimationFrame(checkAutocomplete); }}
        onKeyDown={onKeyDown}
        onClick={checkAutocomplete}
        onBlur={() => setTimeout(() => setAc(null), 150)}
        className="w-full min-h-[60vh] resize-none bg-transparent outline-none text-[15px] leading-[1.85] placeholder:text-faint"
        style={{ fieldSizing: "content" } as React.CSSProperties}
      />
      {ac && (matches.length > 0 || showCreate) && (
        <div className="anim-fade fixed z-40 w-72 p-1 rounded-xl bg-paper border border-rule shadow-xl"
          style={{ left: Math.min(ac.x, window.innerWidth - 300), top: Math.max(8, menuTop) }}>
          <p className="px-2.5 pt-1.5 pb-1 text-[11px] text-faint">链接到笔记</p>
          {matches.map((m, i) => (
            <button key={m.id} onMouseDown={(e) => { e.preventDefault(); pick(m.title); }} onMouseEnter={() => setIndex(i)}
              className={`w-full flex items-center gap-2 h-8 px-2.5 rounded-lg text-[13px] text-left ${i === index ? "bg-hover" : ""}`}>
              <Link2 size={13} className="text-faint shrink-0" />
              <span className="truncate">{m.title}</span>
            </button>
          ))}
          {showCreate && (
            <button onMouseDown={(e) => { e.preventDefault(); pick(ac.query.trim()); }} onMouseEnter={() => setIndex(matches.length)}
              className={`w-full flex items-center gap-2 h-8 px-2.5 rounded-lg text-[13px] text-left ${index === matches.length ? "bg-hover" : ""}`}>
              <FilePlus2 size={13} className="text-accent shrink-0" />
              <span className="truncate">链接到新笔记「{ac.query.trim()}」</span>
            </button>
          )}
        </div>
      )}
    </>
  );
});
