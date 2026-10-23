/** Immutable operation journals survive a lost response and browser refresh. */
type Store = Pick<Storage, "getItem" | "setItem" | "removeItem" | "key" | "length">;
export type StudyRequest = Record<string, unknown> & { operation_id: string };
export class StudyPendingChanged extends Error {
  pending: StudyRequest;
  constructor(pending: StudyRequest) {
    super("发现另一份待确认操作，请先查看原内容，再重试确认；当前草稿已保留");
    this.pending = pending;
  }
}
export type StudyDraft = { text: string; baseVersion: number | null; needsRefresh: boolean;
  plan: string; goal: string; title: string; due: string; minutes: string; taskEdited: boolean };
export const emptyStudyDraft = (): StudyDraft => ({ text: "", baseVersion: null, needsRefresh: false,
  plan: "new", goal: "薄弱知识点补强", title: "", due: "", minutes: "25", taskEdited: false });
export type StudyDraftRecord = { key: string; value: StudyDraft; updatedAt: number };
const draftPrefix = (cardId: string) => `kf-weak-study-draft-v2:${encodeURIComponent(cardId)}:`;
export function storeStudyDraft(storage: Store, cardId: string, owner: string, value: StudyDraft): void {
  const key = draftPrefix(cardId) + owner;
  if (!value.text.trim() && !value.taskEdited) { storage.removeItem(key); return; }
  storage.setItem(key, JSON.stringify({ value, updatedAt: Date.now() }));
}
export function studyDrafts(storage: Store, cardId: string, owner: string): StudyDraftRecord[] {
  const records: StudyDraftRecord[] = [];
  const legacyKey = `kf-weak-study-draft-v1:${encodeURIComponent(cardId)}`;
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i);
    if (!key || (key !== legacyKey && !key.startsWith(draftPrefix(cardId))) || key === draftPrefix(cardId) + owner) continue;
    try {
      const saved = JSON.parse(storage.getItem(key) || "null");
      const value = key === legacyKey ? saved : saved?.value;
      if (!value || typeof value.text !== "string") continue;
      const recovered: StudyDraft = { ...emptyStudyDraft(), ...value };
      if (!Number.isInteger(recovered.baseVersion) || (recovered.baseVersion ?? 0) < 1) recovered.baseVersion = null;
      if (!recovered.text.trim() && !recovered.taskEdited && !(key === legacyKey && recovered.title)) continue;
      records.push({ key, value: recovered, updatedAt: saved.updatedAt || 0 });
    } catch { /* Keep damaged data untouched; never send it as an operation. */ }
  }
  return records.sort((a, b) => b.updatedAt - a.updatedAt);
}
export function forgetStudyDraft(storage: Store, record: StudyDraftRecord): void {
  const raw = storage.getItem(record.key);
  if (!raw) return;
  const saved = JSON.parse(raw);
  const value = saved.value ?? saved;
  // Do not erase a draft that its owner has edited since it was displayed.
  if (JSON.stringify({ ...emptyStudyDraft(), ...value }) === JSON.stringify(record.value)) storage.removeItem(record.key);
}
export function settledUnderstanding(draft: StudyDraft, request: StudyRequest, version: number): StudyDraft {
  if (draft.text === request.text && draft.baseVersion === request.base_version)
    return { ...draft, text: "", baseVersion: version, needsRefresh: false };
  if (!draft.text.trim()) return { ...draft, baseVersion: version, needsRefresh: false };
  // Confirming another window's operation must not clear or silently rebase ours.
  return { ...draft, needsRefresh: !!draft.text.trim() || draft.needsRefresh };
}
export function settledLearningTask(draft: StudyDraft, request: StudyRequest): StudyDraft {
  const matches = draft.title === request.title &&
    (draft.plan === "new" ? !request.plan_id && draft.goal === request.plan_goal : draft.plan === request.plan_id) &&
    (draft.due || null) === request.due_date && (draft.minutes ? Number(draft.minutes) : null) === request.estimated_minutes;
  return matches ? { ...draft, title: "", taskEdited: false } : draft;
}
export type PendingRecoveryView = { kind: "understanding" | "task"; operationId: string; text: string; title: string };
export function pendingRecoveryView(understanding: StudyRequest | null, task: StudyRequest | null): PendingRecoveryView[] {
  const out: PendingRecoveryView[] = [];
  if (understanding) out.push({ kind: "understanding", operationId: understanding.operation_id,
    text: String(understanding.text || ""), title: "补充理解" });
  if (task) out.push({ kind: "task", operationId: task.operation_id,
    text: studyDraftText({ ...emptyStudyDraft(), text: "", title: String(task.title || ""),
      plan: task.plan_id ? String(task.plan_id) : "new", goal: String(task.plan_goal || ""),
      due: String(task.due_date || ""), minutes: task.estimated_minutes ? String(task.estimated_minutes) : "", taskEdited: true }),
    title: String(task.title || "关联学习任务") });
  return out;
}
export function studyDraftText(draft: StudyDraft): string {
  const parts: string[] = [];
  if (draft.text) parts.push(`补充理解\n\n${draft.text}`);
  if (draft.title || draft.taskEdited) parts.push(`学习任务：${draft.title}\n计划：${draft.plan === "new" ? draft.goal : draft.plan}\n日期：${draft.due || "未安排"}\n预计分钟：${draft.minutes || "未填写"}`);
  return parts.join("\n\n");
}
export class StudySubmission {
  busy = false;
  private prefix: string;
  private storage: () => Store;
  private current: StudyRequest | null = null;
  constructor(cardId: string, kind: string, storage: () => Store) {
    this.prefix = `kf-study-operation-v1:${encodeURIComponent(cardId)}:${kind}:`;
    this.storage = storage;
  }
  pending(): StudyRequest | null {
    if (this.current) return this.current;
    const storage = this.storage();
    const keys: string[] = [];
    for (let index = 0; index < storage.length; index++) {
      const key = storage.key(index);
      if (key?.startsWith(this.prefix)) keys.push(key);
    }
    for (const key of keys.sort()) {
      try {
        const saved = JSON.parse(storage.getItem(key) || "null");
        if (saved && typeof saved.operation_id === "string" && saved.operation_id.length >= 8 &&
          key === this.prefix + saved.operation_id) return this.current = saved;
      } catch { /* Do not submit a damaged journal as a new operation. */ }
    }
    return null;
  }
  private clear() {
    if (this.current) this.storage().removeItem(this.prefix + this.current.operation_id);
    this.current = null;
  }
  /** Only a definite rejection can unlock editing; unknown results must retry. */
  async submit<T>(payload: Record<string, unknown>, send: (request: StudyRequest) => Promise<T>,
                  visibleOperationId: string | null = null,
                  beforeRejectedClear?: (request: StudyRequest, error: unknown) => void): Promise<T | undefined> {
    if (this.busy) return undefined;
    this.busy = true;
    try {
      const pending = this.pending();
      if (pending && pending.operation_id !== visibleOperationId) throw new StudyPendingChanged(pending);
      const request = pending ?? { ...payload, operation_id: globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}` };
      this.current = request;
      // If durable storage fails, never create a result that cannot be retried.
      this.storage().setItem(this.prefix + request.operation_id, JSON.stringify(request));
      try {
        const result = await send(request);
        this.clear();
        return result;
      } catch (error) {
        const status = (error as { status?: number }).status;
        const code = (error as { code?: string }).code;
        if (status && status >= 400 && status < 500 && !["IDEMPOTENCY_CONFLICT", "DATASET_CHANGED"].includes(code || "")) {
          beforeRejectedClear?.(request, error); // If preservation fails, keep the original immutable journal.
          this.clear();
        }
        throw error;
      }
    } finally { this.busy = false; }
  }
}
