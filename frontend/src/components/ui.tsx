"use client";

import { X } from "lucide-react";
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { colorOf } from "@/lib/format";

/* ---------- 弹层 ---------- */

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  width = 520,
  wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    setTimeout(() => ref.current?.querySelector<HTMLElement>("input, textarea, button")?.focus(), 30);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 anim-fade" style={{ background: "var(--overlay)" }}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} role="dialog" aria-modal="true"
        className="anim-sheet flex flex-col max-h-[92vh] w-full bg-paper rounded-2xl border border-rule shadow-2xl"
        style={{ maxWidth: wide ? 1180 : width }}>
        {title && (
          <div className="flex items-center justify-between px-5 h-14 shrink-0 border-b border-rule">
            <h2 className="text-[15px] font-semibold">{title}</h2>
            <button className="btn btn-icon" onClick={onClose} aria-label="关闭">
              <X size={16} />
            </button>
          </div>
        )}
        <div className="flex-1 overflow-y-auto">{children}</div>
        {footer && <div className="flex items-center justify-end gap-2 px-5 h-14 shrink-0 border-t border-rule">{footer}</div>}
      </div>
    </div>
  );
}

/* ---------- 提示条 ---------- */

type Toast = { id: number; text: string; tone: "ok" | "bad" };
const ToastCtx = createContext<(text: string, tone?: "ok" | "bad") => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: "ok" | "bad" = "ok") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, text, tone }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), tone === "bad" ? 5000 : 2600);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[60] flex flex-col items-center gap-2 pointer-events-none" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className="anim-sheet px-4 py-2 rounded-full text-[13px] shadow-lg"
            style={{ background: t.tone === "bad" ? "var(--bad)" : "var(--ink)", color: "var(--paper)" }}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ---------- 确认框 ---------- */

type ConfirmOpts = { title: string; body?: ReactNode; confirm?: string; danger?: boolean };
const ConfirmCtx = createContext<(o: ConfirmOpts) => Promise<boolean>>(async () => false);
export const useConfirm = () => useContext(ConfirmCtx);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<(ConfirmOpts & { resolve: (v: boolean) => void }) | null>(null);
  const ask = useCallback((o: ConfirmOpts) => new Promise<boolean>((resolve) => setState({ ...o, resolve })), []);
  const close = (v: boolean) => {
    state?.resolve(v);
    setState(null);
  };
  return (
    <ConfirmCtx.Provider value={ask}>
      {children}
      <Modal open={!!state} onClose={() => close(false)} width={420}>
        {state && (
          <div className="p-6">
            <h2 className="text-[16px] font-semibold mb-2">{state.title}</h2>
            {state.body && <div className="text-[14px] text-muted leading-relaxed">{state.body}</div>}
            <div className="flex justify-end gap-2 mt-6">
              <button className="btn btn-outline" onClick={() => close(false)}>取消</button>
              <button className={`btn ${state.danger ? "" : "btn-primary"}`} autoFocus
                style={state.danger ? { background: "var(--bad)", color: "#fff" } : undefined}
                onClick={() => close(true)}>
                {state.confirm || "确定"}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </ConfirmCtx.Provider>
  );
}

/* ---------- 小部件 ---------- */

export const Dot = ({ color, size = 8 }: { color?: string | null; size?: number }) => (
  <span className="inline-block rounded-full shrink-0" style={{ width: size, height: size, background: colorOf(color) }} />
);

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-16 px-6">
      {icon && <div className="mb-3 text-faint">{icon}</div>}
      <p className="text-[15px] font-medium">{title}</p>
      {children && <div className="mt-1.5 text-[13px] text-muted max-w-sm leading-relaxed">{children}</div>}
    </div>
  );
}

export function Highlight({ text, ranges }: { text: string; ranges: number[][] }) {
  if (!ranges.length) return <>{text}</>;
  const out: ReactNode[] = [];
  let pos = 0;
  ranges.forEach(([s, e], i) => {
    if (s < pos) return;
    out.push(text.slice(pos, s));
    out.push(<mark key={i} className="rounded px-px" style={{ background: "var(--mark)", color: "inherit" }}>{text.slice(s, e)}</mark>);
    pos = e;
  });
  out.push(text.slice(pos));
  return <>{out}</>;
}

export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex items-end justify-between gap-4 mb-8">
      <div>
        <h1 className="text-[26px] font-semibold tracking-tight">{title}</h1>
        {sub && <p className="text-[14px] text-muted mt-1">{sub}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Spinner({ size = 14 }: { size?: number }) {
  return (
    <span className="inline-block rounded-full border-2 border-current border-r-transparent animate-spin opacity-60"
      style={{ width: size, height: size }} />
  );
}
