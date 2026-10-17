// 打包后前端由后端同源提供，留空即可；开发时 .env.development 指向 8000 端口
export const API = process.env.NEXT_PUBLIC_API_BASE || "";

export class ApiError extends Error {
  code: string;
  status: number;
  data: Record<string, unknown>;
  constructor(status: number, data: Record<string, unknown>) {
    super(String(data.message || "请求失败"));
    this.status = status;
    this.code = String(data.code || "ERROR");
    this.data = data;
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (res.ok) return res.json() as Promise<T>;
  let body: { error?: Record<string, unknown> } = {};
  try {
    body = await res.json();
  } catch {}
  throw new ApiError(res.status, body.error || { message: `请求失败（${res.status}）` });
}

export async function api<T = unknown>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init || {};
  let res: Response;
  try {
    res = await fetch(API + path, {
      ...rest,
      headers: json !== undefined ? { "Content-Type": "application/json", ...(rest.headers || {}) } : rest.headers,
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    });
  } catch {
    throw new ApiError(0, { code: "OFFLINE", message: "连不上本地后端，请确认 KnowFlow 正在运行" });
  }
  return handle<T>(res);
}

export const fetcher = <T,>(path: string) => api<T>(path);

export async function download(path: string, init: RequestInit & { json?: unknown }, fallbackName: string) {
  const { json, ...rest } = init;
  const res = await fetch(API + path, {
    ...rest,
    headers: json !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!res.ok) await handle(res);
  const disposition = res.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename\*=UTF-8''([^;]+)/);
  const name = match ? decodeURIComponent(match[1]) : fallbackName;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/* ---------- 类型 ---------- */

export type Course = {
  id: string; name: string; color: string; description: string | null;
  note_count: number; material_count: number; card_count: number; due_count: number; plan_count: number;
};

export type NoteSummary = {
  id: string; course_id: string; title: string; tags: string[]; excerpt: string; char_count: number;
  card_count: number; source_count: number; created_at: string; updated_at: string; version: number;
  days_left?: number;
};

export type Source = {
  ord: number; material_id: string; unit_id: string | null; material_name: string; locator: string;
  label: string; precision: string; available: boolean;
};

export type Note = NoteSummary & { content: string; revision_count: number; sources: Source[] };

export type NoteLinks = {
  outgoing: { title: string; note_id: string | null; course_id: string | null }[];
  backlinks: { id: string; title: string; course_id: string; snippet: string }[];
};

export type GraphNode = { id: string; title: string; course_id: string; degree: number; card_count: number; excerpt: string };
export type GraphData = { nodes: GraphNode[]; edges: { source: string; target: string }[]; unresolved: number };

export type CalendarData = {
  today: string; tasks: Task[]; unscheduled: Task[]; reviews: Record<string, number>;
  plans: { id: string; goal: string; deadline: string | null; course_id: string | null }[];
};

export type Revision = { id: string; title: string; reason: string; created_at: string; length: number };

export type Card = {
  id: string; course_id: string; source_note_id: string | null; front: string; back: string;
  n: number; interval: number; ef: number; due: string; forgot_count: number;
  course_name?: string; course_color?: string; note_title?: string | null;
  preview?: { forgot: number; hard: number; good: number };
};

export type Unit = {
  id: string; kind: string; ordinal: number; locator: string; label: string; text: string;
  warnings: string[]; warning_text: string[]; char_count: number;
};

export type Material = {
  id: string; course_id: string; original_name: string; display_title: string; format: string; ext: string;
  size_bytes: number; status: string; error_code: string | null; error_message: string | null;
  unit_count: number; char_count: number; uploaded_at: string;
  units?: Unit[]; used_by?: { id: string; title: string }[];
};

export type Task = {
  id: string; plan_id: string; title: string; kind: string; status: "todo" | "in_progress" | "done";
  needs_review: boolean; due_date: string | null; estimated_minutes: number | null; note_ids: string[];
  overdue: boolean; unscheduled: boolean; plan_goal?: string; course_id?: string | null; course_name?: string | null;
  actual_minutes?: number;
  course_color?: string | null;
};

export type Plan = {
  id: string; course_id: string | null; goal: string; progress_text: string; deadline: string | null;
  daily_minutes: number | null; archived: boolean; task_total: number; task_done: number; task_overdue: number;
  minutes_total: number; minutes_done: number; course_name?: string | null; course_color?: string | null;
  tasks?: Task[]; budget_warnings?: Problem[]; updated_at: string;
};

export type Problem = { index: number | null; field: string; message: string; day?: string };

export type ModelConfig = {
  id: string; name: string; base_url: string; model: string; json_mode: boolean; max_output_tokens: number;
  is_default: boolean; api_key_masked: string; has_key: boolean; consented: boolean;
};

export type Draft = {
  id: string; kind: string; status: string; note_id: string | null; plan_id: string | null; course_id: string | null;
  output: string | null; result: Record<string, any> | null; warnings: string[]; error_kind: string | null;
  error_message: string | null; input_chars: number; model: string | null;
};

export type SearchItem = {
  type: "note" | "material" | "card"; id: string; title: string; course_id: string;
  snippet: { text: string; highlights: number[][] }; unit_id?: string; unit_label?: string; source_note_id?: string;
};

/* ---------- AI 流式任务 ---------- */

/** 分批任务（资料整理）的进度：第 step / total 部分，本部分已生成 chars 字，预计约 expected 字 */
export type AiProgressInfo = { step: number; total: number; done: number; label: string; chars: number; expected: number };

export type RunHandlers = {
  onStart?: (d: { draft_id: string; input_chars: number; model: string }) => void;
  onDelta?: (text: string) => void;
  onProgress?: (p: AiProgressInfo) => void;
  onDone?: (draft: Draft) => void;
  onError?: (err: { kind: string; message: string; retryable: boolean; detail?: string }, partial?: string) => void;
};

export async function runAi(body: Record<string, unknown>, h: RunHandlers, signal?: AbortSignal) {
  let res: Response;
  try {
    res = await fetch(API + "/api/ai/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    throw new ApiError(0, { code: "OFFLINE", message: "连不上本地后端，请确认 KnowFlow 正在运行" });
  }
  if (!res.ok || !res.body) await handle(res);
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const ev = block.match(/^event: (.*)$/m)?.[1];
        const data = block.match(/^data: (.*)$/m)?.[1];
        if (!ev || !data) continue;
        const payload = JSON.parse(data);
        if (ev === "start") h.onStart?.(payload);
        else if (ev === "delta") h.onDelta?.(payload.text);
        else if (ev === "progress") h.onProgress?.(payload);
        else if (ev === "done") h.onDone?.(payload.draft);
        else if (ev === "error") h.onError?.(payload.error, payload.partial);
      }
    }
  } catch (e) {
    if ((e as Error).name !== "AbortError") throw e;
  }
}

/** 通用 SSE 请求：POST JSON，逐个事件回调。请求本身失败（如 409）时抛出 ApiError。 */
export async function postSse(path: string, body: unknown, onEvent: (event: string, data: Record<string, unknown>) => void,
  signal?: AbortSignal) {
  let res: Response;
  try {
    res = await fetch(API + path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    throw new ApiError(0, { code: "OFFLINE", message: "连不上本地后端，请确认 KnowFlow 正在运行" });
  }
  if (!res.ok || !res.body) await handle(res);
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const ev = block.match(/^event: (.*)$/m)?.[1];
        const data = block.match(/^data: (.*)$/m)?.[1];
        if (ev && data) onEvent(ev, JSON.parse(data));
      }
    }
  } catch (e) {
    if ((e as Error).name !== "AbortError") throw e;
  }
}
