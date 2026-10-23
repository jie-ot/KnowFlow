/** Durable, per-note save queue. Transport and storage are injected so tests never touch real data. */
export type NoteFields = { title: string; content: string; tags: string[]; course_id: string };
export type SavedNote = NoteFields & { version: number };
export type DraftStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
export type NoteDraft = {
  schema: 1; noteId: string; owner: string; updatedAt: number;
  baseVersion: number; base: NoteFields; fields: NoteFields;
};
export type SaveStatus = "saved" | "dirty" | "saving" | "error" | "conflict";
export type SaveSnapshot<N extends SavedNote> = {
  fields: NoteFields; base: NoteFields; saved: N; status: SaveStatus; localSaved: boolean;
  localError: string | null; error: string | null; drafts: NoteDraft[]; recovered: boolean;
};

const prefix = "kf-note-draft-v1:";
const fieldsOf = (note: NoteFields): NoteFields => ({ title: note.title, content: note.content, tags: [...note.tags], course_id: note.course_id });
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const validFields = (value: unknown): value is NoteFields => {
  const item = value as NoteFields | null;
  return !!item && typeof item.title === "string" && typeof item.content === "string" &&
    typeof item.course_id === "string" && Array.isArray(item.tags) && item.tags.every((tag) => typeof tag === "string");
};

export function readNoteDrafts(storage: DraftStorage | null, noteId: string): NoteDraft[] {
  if (!storage) return [];
  const result: NoteDraft[] = [];
  try {
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i);
      if (!key?.startsWith(`${prefix}${encodeURIComponent(noteId)}:`)) continue;
      try {
        const item = JSON.parse(storage.getItem(key) || "null") as NoteDraft;
        if (item?.schema === 1 && item.noteId === noteId && typeof item.owner === "string" &&
          Number.isFinite(item.updatedAt) && Number.isInteger(item.baseVersion) && validFields(item.base) && validFields(item.fields)) result.push(item);
      } catch { /* One damaged draft must not hide the other drafts. */ }
    }
  } catch { /* Storage can be disabled by the host WebView or browser. */ }
  return result.sort((a, b) => b.updatedAt - a.updatedAt);
}

export function isNoteDraftKey(key: string | null): boolean { return key === null || key.startsWith(prefix); }

export class NoteSaveCoordinator<N extends SavedNote> {
  private saved: N;
  private fields: NoteFields;
  private running: Promise<boolean> | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private listeners = new Set<(snapshot: SaveSnapshot<N>) => void>();
  private error: string | null = null;
  private conflict = false;
  private localSaved = true;
  private localError: string | null = null;
  private recovered = false;
  private adoptedDrafts: NoteDraft[] = [];
  private recoveryBase: { fields: NoteFields; version: number } | null = null;
  private drafts: NoteDraft[] = [];
  private readonly key: string;
  private readonly options: {
    noteId: string; owner: string; storage: DraftStorage | null;
    save: (fields: NoteFields, baseVersion: number) => Promise<N>;
    onSaved?: (note: N) => void; delay?: number;
  };

  constructor(note: N, options: NoteSaveCoordinator<N>["options"]) {
    this.saved = note;
    this.fields = fieldsOf(note);
    this.options = options;
    this.key = `${prefix}${encodeURIComponent(options.noteId)}:${options.owner}`;
    this.refreshDrafts();
    // A lone recoverable draft is shown immediately, but waits for explicit synchronization.
    // Multiple windows' drafts are offered individually, never chosen by last-write-wins.
    if (this.drafts.length === 1) this.recoverDraft(this.drafts[0]);
  }

  getSnapshot(): SaveSnapshot<N> {
    return { fields: fieldsOf(this.fields), base: fieldsOf(this.recoveryBase?.fields || this.saved), saved: this.saved,
      status: this.conflict ? "conflict" : this.running ? "saving" : this.error ? "error" : this.hasChanges() ? "dirty" : "saved",
      localSaved: this.localSaved, localError: this.localError, error: this.error, drafts: [...this.drafts], recovered: this.recovered };
  }

  subscribe(listener: (snapshot: SaveSnapshot<N>) => void): () => void {
    this.listeners.add(listener);
    listener(this.getSnapshot());
    return () => { this.listeners.delete(listener); };
  }

  private emit() { const value = this.getSnapshot(); this.listeners.forEach((listener) => listener(value)); }
  hasChanges() { return !same(this.fields, fieldsOf(this.saved)); }

  private persist() {
    try {
      if (!this.options.storage) throw new Error("浏览器禁止了本地存储");
      if (this.hasChanges() || this.running) {
        const draft: NoteDraft = { schema: 1, noteId: this.options.noteId, owner: this.options.owner,
          updatedAt: Date.now(), baseVersion: this.recoveryBase?.version ?? this.saved.version,
          base: fieldsOf(this.recoveryBase?.fields || this.saved), fields: fieldsOf(this.fields) };
        this.options.storage.setItem(this.key, JSON.stringify(draft));
      } else this.options.storage.removeItem(this.key);
      this.localSaved = true;
      this.localError = null;
    } catch (error) {
      this.localSaved = false;
      this.localError = error instanceof Error ? error.message : "无法写入本地草稿";
    }
  }

  edit(patch: Partial<NoteFields>, schedule = true) {
    this.fields = { ...this.fields, ...patch };
    this.error = null;
    this.recovered = false;
    this.persist(); // Synchronous persistence also covers refresh before the debounce expires.
    if (this.timer) clearTimeout(this.timer);
    if (schedule && !this.conflict) this.timer = setTimeout(() => { this.timer = null; void this.flush(); }, this.options.delay ?? 800);
    this.emit();
  }

  receiveRemote(note: N) {
    if (note.version < this.saved.version || (note.version === this.saved.version && same(fieldsOf(note), fieldsOf(this.saved)))) return;
    if (this.hasChanges() || this.running) {
      // Keep the original base for comparison; the resolution dialog fetches the latest server copy.
      this.conflict = true;
      this.error = "这篇笔记已在其他窗口或任务中修改，本地草稿已保留";
      if (this.timer) clearTimeout(this.timer);
    } else {
      this.saved = note;
      this.fields = fieldsOf(note);
    }
    this.emit();
  }

  refreshDrafts() {
    const seen = new Set<string>();
    this.drafts = readNoteDrafts(this.options.storage, this.options.noteId)
      .filter((draft) => {
        if (draft.owner === this.options.owner || same(draft.fields, fieldsOf(this.saved))) return false;
        const signature = JSON.stringify([draft.baseVersion, draft.base, draft.fields]);
        if (seen.has(signature)) return false;
        seen.add(signature);
        return true;
      });
    this.emit();
  }

  recoverDraft(draft: NoteDraft) {
    if (this.running) return;
    // Preserve this window's edits as a separate recovery option before loading another draft.
    if (this.hasChanges() && !same(this.fields, draft.fields)) this.archiveCurrentDraft();
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    this.adoptedDrafts = readNoteDrafts(this.options.storage, this.options.noteId).filter((item) =>
      item.owner !== this.options.owner && item.baseVersion === draft.baseVersion && same(item.base, draft.base) && same(item.fields, draft.fields));
    this.recoveryBase = { fields: fieldsOf(draft.base), version: draft.baseVersion };
    this.fields = fieldsOf(draft.fields);
    this.recovered = true;
    this.conflict = draft.baseVersion !== this.saved.version || !same(draft.base, fieldsOf(this.saved));
    this.error = this.conflict ? "草稿与后端版本不同，请查看差异后处理" : null;
    this.persist();
    this.emit();
  }

  private archiveCurrentDraft() {
    if (!this.options.storage) throw new Error("无法另存当前草稿，请先同步或导出当前笔记");
    const owner = `${this.options.owner}-saved-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const archived: NoteDraft = { schema: 1, noteId: this.options.noteId, owner, updatedAt: Date.now(),
      baseVersion: this.recoveryBase?.version ?? this.saved.version, base: fieldsOf(this.recoveryBase?.fields || this.saved), fields: fieldsOf(this.fields) };
    try {
      this.options.storage.setItem(`${prefix}${encodeURIComponent(this.options.noteId)}:${owner}`, JSON.stringify(archived));
      this.drafts = [archived, ...this.drafts];
    } catch {
      // Refuse to replace an unpersisted draft when local storage is full or unavailable.
      throw new Error("无法另存当前草稿，请先同步或导出当前笔记");
    }
  }

  private clearAdoptedDraft() {
    if (!this.options.storage) return;
    for (const adopted of this.adoptedDrafts) {
      const key = `${prefix}${encodeURIComponent(this.options.noteId)}:${adopted.owner}`;
      try {
        // Another live window may have edited it since recovery. Never delete that newer draft.
        if (same(JSON.parse(this.options.storage.getItem(key) || "null"), adopted)) this.options.storage.removeItem(key);
      } catch {}
    }
    this.adoptedDrafts = [];
    this.refreshDrafts();
  }

  /** Deliberate conflict resolution; the subsequent PATCH still checks the fetched version. */
  resolve(note: N, fields: NoteFields) {
    if (this.running) return false;
    this.saved = note;
    this.fields = fieldsOf(fields);
    this.conflict = false;
    this.error = null;
    this.recovered = false;
    this.recoveryBase = null;
    this.persist();
    if (!this.hasChanges()) this.clearAdoptedDraft();
    this.emit();
    return true;
  }

  flush(): Promise<boolean> {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    if (this.running) return this.running;
    if (this.conflict) return Promise.resolve(false);
    if (!this.hasChanges()) return Promise.resolve(true);
    this.error = null;
    this.running = this.drain().finally(() => { this.running = null; this.persist(); this.emit(); });
    this.emit();
    return this.running;
  }

  private async drain(): Promise<boolean> {
    while (this.hasChanges() && !this.conflict) {
      const submitted = fieldsOf(this.fields);
      try {
        const note = await this.options.save(submitted, this.saved.version);
        // Adopt backend normalization only for fields that have not changed during the request.
        for (const key of ["title", "content", "tags", "course_id"] as const) {
          if (same(this.fields[key], submitted[key])) Object.assign(this.fields, { [key]: note[key] });
        }
        this.saved = note;
        this.recoveryBase = null;
        this.error = null;
        this.persist();
        if (!this.hasChanges()) this.clearAdoptedDraft();
        this.options.onSaved?.(note);
        this.emit();
      } catch (error) {
        const failure = error as { status?: number; code?: string; message?: string };
        this.conflict = failure.status === 409 || failure.code === "VERSION_CONFLICT";
        this.error = failure.message || "同步失败，可以重试";
        this.persist();
        this.emit();
        return false;
      }
    }
    return !this.conflict;
  }
}
