"use client";

import { ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { createContext, ReactNode, useCallback, useContext, useRef, useState } from "react";
import { api, ApiError, Draft, runAi } from "@/lib/api";
import { Modal, useToast } from "./ui";

export type AiState = {
  running: boolean;
  text: string;
  draft: Draft | null;
  error: { kind: string; message: string; retryable: boolean; detail?: string } | null;
  startedAt: number | null;
  inputChars: number;
  model: string;
};

const initial: AiState = { running: false, text: "", draft: null, error: null, startedAt: null, inputChars: 0, model: "" };

type Consent = { config_id: string; provider_host: string; model: string; purpose: string; resolve: (ok: boolean) => void };

const PURPOSE: Record<string, string> = {
  organize: "整理这篇笔记的全文",
  summarize: "总结这篇笔记的全文",
  extract: "从这篇笔记中提炼知识点",
  compose: "整理你选中的资料片段",
  plan: "根据计划目标、进度和所选笔记的标题生成任务",
};

const ConsentCtx = createContext<(info: Omit<Consent, "resolve">) => Promise<boolean>>(async () => false);

/** 首次调用 AI 前的发送确认（我的 NFR-08 / 同学 NFR-09）。 */
export function AiProvider({ children }: { children: ReactNode }) {
  const [consent, setConsent] = useState<Consent | null>(null);
  const ask = useCallback(
    (info: Omit<Consent, "resolve">) => new Promise<boolean>((resolve) => setConsent({ ...info, resolve })),
    [],
  );
  const close = async (ok: boolean) => {
    if (ok && consent) await api(`/api/ai/configs/${consent.config_id}/consent`, { method: "POST" });
    consent?.resolve(ok);
    setConsent(null);
  };
  return (
    <ConsentCtx.Provider value={ask}>
      {children}
      <Modal open={!!consent} onClose={() => close(false)} width={460}>
        {consent && (
          <div className="p-6">
            <div className="w-10 h-10 rounded-xl grid place-items-center mb-4 bg-accent-soft text-accent">
              <ShieldCheck size={20} />
            </div>
            <h2 className="text-[16px] font-semibold mb-2">内容将发送给 {consent.provider_host}</h2>
            <div className="text-[14px] text-muted leading-relaxed space-y-2">
              <p>这次操作会{consent.purpose}，发送给模型 {consent.model} 处理。只发送这一次需要的内容，不会附带其他笔记、资料或你的 API Key 之外的配置。</p>
              <p>服务商如何留存这些内容，以它的隐私政策为准。同意后，同一服务商不再询问；更换 Base URL 时会重新确认。</p>
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <button className="btn btn-outline" onClick={() => close(false)}>不发送</button>
              <button className="btn btn-primary" onClick={() => close(true)}>同意并继续</button>
            </div>
          </div>
        )}
      </Modal>
    </ConsentCtx.Provider>
  );
}

/** 运行一个 AI 任务：处理未配置、需要同意、流式输出、取消。 */
export function useAiTask() {
  const askConsent = useContext(ConsentCtx);
  const toast = useToast();
  const router = useRouter();
  const [state, setState] = useState<AiState>(initial);
  const ctrl = useRef<AbortController | null>(null);

  const run = useCallback(
    async (body: Record<string, unknown>): Promise<boolean> => {
      ctrl.current?.abort();
      const c = new AbortController();
      ctrl.current = c;
      setState({ ...initial, running: true, startedAt: Date.now() });
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          await runAi(
            body,
            {
              onStart: (d) => setState((s) => ({ ...s, inputChars: d.input_chars, model: d.model })),
              onDelta: (t) => setState((s) => ({ ...s, text: s.text + t })),
              onDone: (draft) => setState((s) => ({ ...s, running: false, draft })),
              onError: (error, partial) => setState((s) => ({ ...s, running: false, error, text: partial || s.text })),
            },
            c.signal,
          );
          setState((s) => (s.running ? { ...s, running: false } : s));
          return true;
        } catch (e) {
          const err = e as ApiError;
          if (err.code === "CONSENT_REQUIRED" && attempt === 0) {
            const ok = await askConsent({
              config_id: String(err.data.config_id),
              provider_host: String(err.data.provider_host),
              model: String(err.data.model),
              purpose: PURPOSE[String(body.kind)] || "处理选中的内容",
            });
            if (ok) continue;
            setState(initial);
            return false;
          }
          setState(initial);
          if (err.code === "NO_CONFIG") {
            toast("还没有连接大模型，先在设置里填写", "bad");
            router.push("/settings");
          } else toast(err.message, "bad");
          return false;
        }
      }
      return false;
    },
    [askConsent, toast, router],
  );

  const cancel = useCallback(() => {
    ctrl.current?.abort();
    setState((s) => ({ ...s, running: false }));
  }, []);

  const reset = useCallback(() => {
    ctrl.current?.abort();
    setState(initial);
  }, []);

  return { state, run, cancel, reset };
}
