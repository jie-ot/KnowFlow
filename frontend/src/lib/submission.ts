/** Stable submission intents; transport and storage are injected for failure tests. */
export type Grade = "forgot" | "hard" | "good";
export type ReviewCard = { id: string; review_revision: number };
export type ReviewRequest = { grade: Grade; operation_id: string; expected_revision: number };
type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;
type JournalStorage = StorageLike & Pick<Storage, "length" | "key">;

export const newOperationId = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
const reviewKey = (card: ReviewCard) => `kf-review-operation:${encodeURIComponent(card.id)}:${card.review_revision}`;

export class ReviewSubmission {
  busy = false;
  private completed = new Set<string>();
  private pending = new Map<string, ReviewRequest>();
  private getStorage: () => StorageLike | null;

  constructor(getStorage: () => StorageLike | null) { this.getStorage = getStorage; }

  pendingFor(card: ReviewCard): ReviewRequest | null {
    const key = reviewKey(card);
    if (this.pending.has(key)) return this.pending.get(key)!;
    try {
      const saved = JSON.parse(this.getStorage()?.getItem(key) || "null") as ReviewRequest | null;
      if (saved && typeof saved.operation_id === "string" && saved.operation_id.length <= 120 &&
        saved.expected_revision === card.review_revision && ["forgot", "hard", "good"].includes(saved.grade)) {
        this.pending.set(key, saved);
        return saved;
      }
    } catch { /* A damaged browser record must not prevent a fresh review. */ }
    return null;
  }

  clear(card: ReviewCard) {
    const key = reviewKey(card);
    const previous = this.pending.get(key);
    this.pending.delete(key);
    try {
      const saved = JSON.parse(this.getStorage()?.getItem(key) || "null");
      if (saved?.operation_id === previous?.operation_id) this.getStorage()?.removeItem(key);
    } catch {}
  }

  async submit(card: ReviewCard, grade: Grade, send: (request: ReviewRequest) => Promise<unknown>): Promise<boolean> {
    const key = reviewKey(card);
    // This lock is synchronous: React's next render is too late for double clicks.
    if (this.busy || this.completed.has(key)) return false;
    this.busy = true;
    try {
      const request = this.pendingFor(card) ?? { grade, operation_id: newOperationId(), expected_revision: card.review_revision };
      this.pending.set(key, request);
      try { this.getStorage()?.setItem(key, JSON.stringify(request)); } catch {}
      await send(request);
      this.completed.add(key);
      this.clear(card);
      return true; // Exactly one caller may advance this card in the local queue.
    } finally { this.busy = false; }
  }
}

export type FocusRequest = { session_id: string; minutes: number; started_at: string; done: boolean };
export type FocusSession = {
  id: string; taskId: string; planId: string; title: string; startedAt: string;
  durationMin: number; runMs: number; resumedAt: number | null; submission?: FocusRequest;
};
export const MAX_FOCUS_MINUTES = 44_640; // 31 days; overnight sessions remain exact.
export const FOCUS_KEY = "kf-focus";
const journalPrefix = "kf-focus-pending-v1:";

function journals(storage: JournalStorage): { key: string; session: FocusSession }[] {
  const result: { key: string; session: FocusSession }[] = [];
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i);
    if (!key?.startsWith(journalPrefix)) continue;
    const session = restoreFocusSession(storage.getItem(key));
    if (session?.submission) result.push({ key, session });
  }
  return result.sort((a, b) => a.key.localeCompare(b.key));
}

/** Immutable per-window journals survive stale windows editing the running key.
 * localStorage has no cross-window CAS; we deliberately do not describe a
 * read/check/write as atomic. Competing freezes retain separate journals.
 */
export function recoverFocus(storage: JournalStorage): FocusSession | null {
  const active = restoreFocusSession(storage.getItem(FOCUS_KEY));
  const pending = journals(storage);
  return pending.find((item) => item.session.id === active?.id)?.session ?? pending[0]?.session ?? active;
}

export function storeFocus(storage: JournalStorage, next: FocusSession | null, owner: string,
                           previous: FocusSession | null): FocusSession | null {
  const pending = journals(storage);
  const stored = restoreFocusSession(storage.getItem(FOCUS_KEY));
  const protectedSession = pending.find((item) => item.session.id === (next?.id ?? previous?.id))?.session;
  if (next?.submission) {
    // Every competing window retains its own original snapshot. A conflict is
    // shown for review; no window can erase another window's unconfirmed work.
    const key = `${journalPrefix}${encodeURIComponent(next.id)}:${encodeURIComponent(owner)}`;
    const existing = restoreFocusSession(storage.getItem(key));
    const frozen = existing?.submission ? existing : next;
    storage.setItem(key, JSON.stringify(frozen));
    storage.setItem(FOCUS_KEY, JSON.stringify(frozen));
    return frozen;
  }
  if (protectedSession) return protectedSession;
  if (next) {
    if (stored?.submission && stored.id === next.id) return stored;
    storage.setItem(FOCUS_KEY, JSON.stringify(next));
    return next;
  }
  if (!stored || stored.id === previous?.id) storage.removeItem(FOCUS_KEY);
  return recoverFocus(storage);
}

/** Call only after backend confirmation, or an explicit user's reviewed discard. */
export function clearFocusJournal(storage: JournalStorage, session: FocusSession, allSnapshots = false): FocusSession | null {
  for (const entry of journals(storage)) {
    if (entry.session.id === session.id && (allSnapshots || JSON.stringify(entry.session.submission) === JSON.stringify(session.submission)))
      storage.removeItem(entry.key);
  }
  const stored = restoreFocusSession(storage.getItem(FOCUS_KEY));
  if (stored?.id === session.id && (allSnapshots || JSON.stringify(stored.submission) === JSON.stringify(session.submission)))
    storage.removeItem(FOCUS_KEY);
  return recoverFocus(storage);
}

export async function refreshReviewQueue<T>(load: () => Promise<T | undefined>, apply: (queue: T) => void): Promise<void> {
  const fresh = await load();
  if (fresh === undefined) throw new Error("复习队列更新失败，请重试");
  apply(fresh); // The completed queue stays intact while loading or on failure.
}

export const focusElapsed = (session: FocusSession, now = Date.now()) =>
  session.runMs + (session.resumedAt === null ? 0 : Math.max(0, now - session.resumedAt));

export function restoreFocusSession(raw: string | null): FocusSession | null {
  if (!raw) return null;
  try {
    const saved = JSON.parse(raw) as FocusSession;
    if (!saved || ![saved.taskId, saved.planId, saved.title, saved.startedAt].every((v) => typeof v === "string") ||
      !Number.isFinite(saved.durationMin) || saved.durationMin <= 0 || !Number.isFinite(saved.runMs) || saved.runMs < 0 ||
      !(saved.resumedAt === null || Number.isFinite(saved.resumedAt))) return null;
    // Upgrade the existing local timer without resetting elapsed time. This ID
    // is deterministic so two windows loading the same legacy timer agree.
    const id = typeof saved.id === "string" && saved.id ? saved.id : `legacy-${saved.taskId}:${saved.startedAt}`;
    if (id.length > 120) return null;
    const session = { ...saved, id };
    if (session.submission) {
      const request = session.submission;
      if (request.session_id !== id || request.started_at !== session.startedAt ||
        !Number.isInteger(request.minutes) || request.minutes < 1 || typeof request.done !== "boolean") return null;
      session.resumedAt = null;
    }
    return session;
  } catch { return null; }
}

export function freezeFocus(session: FocusSession, done: boolean, now = Date.now()): FocusSession {
  if (session.submission) return session;
  const runMs = focusElapsed(session, now);
  if (Math.max(1, Math.round(runMs / 60000)) > MAX_FOCUS_MINUTES)
    throw new Error("单次计时超过 31 天，结果已保留；请先暂停并核对开始时间，再导出计时记录。不会截短时长或冻结无效提交。");
  return { ...session, runMs, resumedAt: null,
    submission: { session_id: session.id, minutes: Math.max(1, Math.round(runMs / 60000)), started_at: session.startedAt, done } };
}
