"use client";

import { X } from "lucide-react";
import { createContext, ReactNode, useCallback, useContext, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { colorOf } from "@/lib/format";

/* ---------- 弹层 ---------- */

const modalStack: number[] = [];
let modalSeq = 0;

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  width = 520,
  wide = false,
  label,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
  wide?: boolean;
  label?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  // onClose 往往是每次渲染新建的函数；放进 ref，避免输入时弹窗反复“重新打开”把焦点抢到关闭按钮上
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const id = ++modalSeq;
    modalStack.push(id);
    // 弹窗叠在一起时（比如确认框盖在 AI 弹窗上），Esc 只关最上面那个
    const onKey = (e: KeyboardEvent) => {
      if (modalStack[modalStack.length - 1] !== id) return;
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeRef.current(); }
      if (e.key !== "Tab") return;
      const nodes = Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled):not([type=hidden]), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]') || []).filter((n) => n.getClientRects().length > 0);
      const first = nodes[0], last = nodes.at(-1);
      if (!first) { e.preventDefault(); ref.current?.focus(); return; }
      if (e.shiftKey && (document.activeElement === first || !ref.current?.contains(document.activeElement))) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && (document.activeElement === last || !ref.current?.contains(document.activeElement))) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", onKey);
    const t = setTimeout(() => {
      const box = ref.current;
      if (!box || box.contains(document.activeElement)) return;
      (box.querySelector<HTMLElement>("input:not([type=checkbox]):not([type=file]), textarea, select")
        || box.querySelector<HTMLElement>("button:not([aria-label='关闭'])"))?.focus();
    }, 30);
    return () => {
      clearTimeout(t);
      modalStack.splice(modalStack.indexOf(id), 1);
      window.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, [open]);
  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div className="modal-backdrop fixed inset-0 z-50 flex items-center justify-center p-4 anim-fade" style={{ background: "var(--overlay)" }}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} role="dialog" aria-modal="true" tabIndex={-1} aria-labelledby={title ? titleId : undefined} aria-label={!title ? label || "确认" : undefined}
        className="modal-surface anim-sheet flex flex-col max-h-[90vh] w-full overflow-hidden"
        style={{ maxWidth: wide ? 1180 : width }}>
        {title && (
          <div className="flex items-center justify-between px-5 h-14 shrink-0 border-b border-rule">
            <h2 id={titleId} className="text-[16px] font-semibold">{title}</h2>
            <button className="btn btn-icon" onClick={onClose} aria-label="关闭">
              <X size={16} />
            </button>
          </div>
        )}
        <div className="flex-1 overflow-y-auto">{children}</div>
        {footer && <div className="flex items-center justify-end gap-2 px-5 h-14 shrink-0 border-t border-rule">{footer}</div>}
      </div>
    </div>, document.body,
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
    setItems((x) => [...x.slice(-1), { id, text, tone }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), tone === "bad" ? 5000 : 2600);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed top-6 right-7 z-[60] flex flex-col items-end gap-2 pointer-events-none max-w-[calc(100vw-48px)]" aria-live="polite">
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

type ConfirmOpts = { title: string; body?: ReactNode; confirm?: string; cancel?: string; danger?: boolean };
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
              <button className="btn btn-outline" onClick={() => close(false)} autoFocus={state.danger}>{state.cancel || "取消"}</button>
              <button className={`btn ${state.danger ? "" : "btn-primary"}`} autoFocus={!state.danger}
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
      {icon && <div className="empty-symbol">{icon}</div>}
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
    <div className="page-header flex items-end justify-between gap-4 mb-8">
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
