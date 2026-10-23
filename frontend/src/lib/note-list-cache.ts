/** Saving a title must invalidate every filtered list and the global completion identity index. */
export function isNoteListCacheKey(key: unknown): boolean {
  return typeof key === "string" && (key === "/api/notes" || key === "/api/notes/index" ||
    key.startsWith("/api/notes?") || key.startsWith("/api/notes/page?") || key.startsWith("/api/tags"));
}
