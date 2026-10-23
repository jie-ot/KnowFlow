import assert from "node:assert/strict";
import test from "node:test";
import { isNoteListCacheKey } from "../src/lib/note-list-cache.ts";
import { NoteSaveCoordinator } from "../src/lib/note-save.ts";

test("a completed rename invalidates the global completion index and every filtered page", async () => {
  const note = { title: "Before", content: "body", tags: [], course_id: "course", version: 1 };
  const cache = new Map([
    ["/api/notes/index", [{ id: "note", title: "Before", course_id: "course" }]],
    ["/api/notes/page?offset=0&limit=40", { items: [{ title: "Before" }] }],
    ["/api/notes/page?offset=40&limit=40&course_id=course", { items: [{ title: "Before" }] }],
    ["/api/notes?course_id=course", [{ title: "Before" }]],
    ["/api/materials", [{ title: "Material stays" }]],
  ]);
  const queue = new NoteSaveCoordinator(note, { noteId: "note", owner: "test", storage: null,
    save: async (fields, version) => ({ ...fields, version: version + 1 }),
    onSaved: (saved) => {
      for (const key of cache.keys()) if (isNoteListCacheKey(key)) cache.set(key, saved.title);
    },
  });
  queue.edit({ title: "After" }, false);
  assert.equal(await queue.flush(), true);
  assert.equal(cache.get("/api/notes/index"), "After");
  assert.equal(cache.get("/api/notes/page?offset=40&limit=40&course_id=course"), "After");
  assert.deepEqual(cache.get("/api/materials"), [{ title: "Material stays" }]);
  assert.equal(isNoteListCacheKey("/api/notes/note/links"), false);
});

test("changing sidebar pages cannot replace a pending editor or create a second save request", async () => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  let requests = 0;
  const note = { title: "Note", content: "original", tags: [], course_id: "course", version: 1 };
  const queue = new NoteSaveCoordinator(note, { noteId: "note", owner: "test", storage: null,
    save: async (fields, version) => { requests++; if (requests === 1) await pending; return { ...fields, version: version + 1 }; },
  });
  let editor = queue.getSnapshot().fields;
  const unsubscribe = queue.subscribe((view) => { editor = view.fields; });
  queue.edit({ content: "pending edit" }, false);
  const saving = queue.flush();
  let sidebarPage = [{ id: "different-note", title: "Different page" }];
  assert.equal(editor.content, "pending edit");
  queue.edit({ content: "typed while paging" }, false);
  sidebarPage = [];
  assert.equal(editor.content, "typed while paging");
  assert.equal(requests, 1);
  release();
  assert.equal(await saving, true);
  assert.equal(queue.getSnapshot().saved.content, "typed while paging");
  assert.equal(requests, 2);
  assert.deepEqual(sidebarPage, []);
  unsubscribe();
});
