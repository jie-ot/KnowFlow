"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { api, ApiError, Card, fetcher, Plan, Source, Task } from "@/lib/api";
import { emptyStudyDraft, forgetStudyDraft, settledLearningTask, settledUnderstanding, storeStudyDraft,
  studyDrafts, StudyDraft, StudyDraftRecord, StudyPendingChanged, StudyRequest, StudySubmission } from "@/lib/learning-submission";
import { pendingRecoveryView, studyDraftText } from "@/lib/learning-submission";
import { isNoteListCacheKey } from "@/lib/note-list-cache";
import { Modal, useToast } from "./ui";

type LearningDetail = {
  card: Card; source_status: "manual" | "missing" | "deleted" | "available";
  source_note: { id: string; title: string; version: number; available: boolean; sources: Source[] } | null;
  reviews: { grade: string; reviewed_at: string; logical_day: string }[];
  tasks: (Task & { plan_archived: boolean })[];
};
export function WeakStudySheet({ cardId, onClose, onSaved }: {
  cardId: string; onClose: () => void; onSaved: () => void;
}) {
  const { data, error, mutate } = useSWR<LearningDetail>(`/api/learning/cards/${cardId}`, fetcher);
  const { data: plans } = useSWR<Plan[]>("/api/plans", fetcher);
  const [draft, setDraft] = useState<StudyDraft>(emptyStudyDraft);
  const draftRef = useRef<StudyDraft>(emptyStudyDraft());
  const [recoverable, setRecoverable] = useState<StudyDraftRecord[]>([]);
  const owner = useMemo(() => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`, []);
  const [ready, setReady] = useState(false);
  const [storageError, setStorageError] = useState("");
  const [pendingUnderstanding, setPendingUnderstanding] = useState<StudyRequest | null>(null);
  const [pendingTask, setPendingTask] = useState<StudyRequest | null>(null);
  const [pendingResult, setPendingResult] = useState("");
  const [resultLink, setResultLink] = useState<{ href: string; label: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [front, setFront] = useState("");
  const [back, setBack] = useState("");
  const editInitialized = useRef(false);
  const actionLock = useRef(false);
  const toast = useToast();
  const understanding = useMemo(() => new StudySubmission(cardId, "understanding", () => localStorage), [cardId]);
  const task = useMemo(() => new StudySubmission(cardId, "task", () => localStorage), [cardId]);

  useEffect(() => {
    const read = () => {
      try { setRecoverable(studyDrafts(localStorage, cardId, owner)); }
      catch { setStorageError("无法读取本地学习草稿，请保持此窗口打开；恢复浏览器存储后再重试。"); }
    };
    read();
    try {
      setPendingUnderstanding(understanding.pending());
      setPendingTask(task.pending());
    } catch { setStorageError("无法读取本地学习草稿，请保持此窗口打开；恢复浏览器存储后再重试。"); }
    setReady(true);
    window.addEventListener("storage", read);
    return () => window.removeEventListener("storage", read);
  }, [cardId, owner, understanding, task]);

  const update = (fields: Partial<StudyDraft>) => {
    const next = { ...draftRef.current, ...fields };
    draftRef.current = next;
    try { storeStudyDraft(localStorage, cardId, owner, next); setStorageError(""); }
    catch { setStorageError("本地保存失败，请保持此窗口打开并检查浏览器存储。"); }
    setDraft(next);
  };
  const recover = (record: StudyDraftRecord) => {
    try {
      // Switching recovery copies must not overwrite the current window's work.
      if (draftRef.current.text.trim() || draftRef.current.taskEdited)
        storeStudyDraft(localStorage, cardId, `${owner}-preserved-${Date.now()}`, draftRef.current);
      update(record.value);
      setRecoverable(studyDrafts(localStorage, cardId, owner));
      toast("已恢复这份草稿，其他副本仍保留");
    } catch { setStorageError("恢复草稿失败，原副本仍保留，请检查浏览器存储。"); }
  };
  const copyText = async (text: string) => {
    try { await navigator.clipboard.writeText(text); toast("已复制本地内容"); }
    catch { toast("无法自动复制，请在展开的文本框中选择并复制，或下载文本", "bad"); }
  };
  const exportText = (text: string) => {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `KnowFlow-学习草稿-${cardId.slice(0, 8)}.txt`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  useEffect(() => {
    if (!data || !ready) return;
    if (!editInitialized.current) {
      setFront(data.card.front); setBack(data.card.back); editInitialized.current = true;
      if (!draft.title) update({ title: `补强：${data.card.front}`.slice(0, 200) });
    }
    // Never silently rebase saved understanding onto newer note content.
    if (draft.baseVersion === null && data.source_note?.available) update({ baseVersion: data.source_note.version });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, ready]);

  const refreshRelated = () => {
    mutate(); onSaved(); globalMutate("/api/plans"); globalMutate("/api/tasks/today");
    globalMutate((cacheKey) => isNoteListCacheKey(cacheKey));
    if (data?.source_note) globalMutate(`/api/notes/${data.source_note.id}`);
  };
  const submitUnderstanding = async () => {
    if (actionLock.current || (!pendingUnderstanding && (!draftRef.current.text.trim() || draftRef.current.needsRefresh || draftRef.current.baseVersion === null))) return;
    actionLock.current = true; setBusy("understanding");
    let original: StudyRequest | null = pendingUnderstanding;
    try {
      const result = await understanding.submit<{ note_id: string; version: number }>(
        { text: draftRef.current.text, base_version: draftRef.current.baseVersion }, (request) =>
          { original = request; return api(`/api/learning/cards/${cardId}/understanding`, { method: "POST", json: request }); },
        pendingUnderstanding?.operation_id ?? null, (request, failure) => {
          if (request.text === draftRef.current.text && request.base_version === draftRef.current.baseVersion) return;
          storeStudyDraft(localStorage, cardId, `rejected-${request.operation_id}`,
            { ...draftRef.current, text: String(request.text || ""), baseVersion: Number(request.base_version),
              needsRefresh: (failure as ApiError).code === "VERSION_CONFLICT" });
          setRecoverable(studyDrafts(localStorage, cardId, owner));
        });
      if (result && original) {
        update(settledUnderstanding(draftRef.current, original, result.version));
        setPendingResult("补充理解已确认。若来源已移入回收站，请在设置中恢复后查看。");
        setResultLink({ href: `/notes?id=${result.note_id}`, label: "查看来源笔记" });
        refreshRelated(); toast("已确认补充到来源笔记，可在笔记历史中撤销");
      }
    } catch (e) {
      if (!(e instanceof StudyPendingChanged) && original && (e as ApiError).status >= 400 && (e as ApiError).status < 500) {
        const conflict = (e as ApiError).code === "VERSION_CONFLICT";
        if (conflict) update({ needsRefresh: true });
      }
      toast((e as Error).message || "提交失败，本地内容已保留", "bad");
    } finally {
      try { setPendingUnderstanding(understanding.pending()); } catch {}
      actionLock.current = false; setBusy("");
    }
  };
  const refreshBase = async () => {
    if (actionLock.current) return;
    actionLock.current = true; setBusy("refresh");
    try {
      const latest = await mutate();
      if (!latest?.source_note?.available) throw new Error("来源笔记当前不可用，补充内容已保留");
      update({ baseVersion: latest.source_note.version, needsRefresh: false });
      toast("已读取来源笔记的最新版本，请核对后再次提交");
    } catch (e) { toast((e as Error).message, "bad"); }
    finally { actionLock.current = false; setBusy(""); }
  };
  const submitTask = async () => {
    if (actionLock.current || (!pendingTask && !draftRef.current.title.trim())) return;
    actionLock.current = true; setBusy("task");
    try {
      let original: StudyRequest | null = null;
      const result = await task.submit<Task>({ title: draft.title, kind: "learn",
        plan_id: draft.plan === "new" ? null : draft.plan, plan_goal: draft.plan === "new" ? draft.goal : null,
        due_date: draft.due || null, estimated_minutes: draft.minutes ? Number(draft.minutes) : null },
      (request) => { original = request; return api(`/api/learning/cards/${cardId}/tasks`, { method: "POST", json: request }); }, pendingTask?.operation_id ?? null,
      (request) => {
        storeStudyDraft(localStorage, cardId, `rejected-${request.operation_id}`, { ...draftRef.current, text: "",
          plan: request.plan_id ? String(request.plan_id) : "new", goal: String(request.plan_goal || ""),
          title: String(request.title || ""), due: String(request.due_date || ""),
          minutes: request.estimated_minutes ? String(request.estimated_minutes) : "", taskEdited: true });
        setRecoverable(studyDrafts(localStorage, cardId, owner));
      });
      if (result && original) {
        update(settledLearningTask(draftRef.current, original));
        setPendingResult("关联学习任务已确认。若卡片已移入回收站，可在设置中恢复；任务记录保留。");
        setResultLink({ href: `/plans?id=${result.plan_id}`, label: "查看学习任务" });
        refreshRelated(); toast("已创建关联学习任务");
      }
    } catch (e) { toast((e as Error).message || "提交失败，原操作已保留", "bad"); }
    finally {
      try { setPendingTask(task.pending()); } catch {}
      actionLock.current = false; setBusy("");
    }
  };
  const saveCard = async () => {
    if (actionLock.current) return;
    actionLock.current = true; setBusy("card");
    try { await api(`/api/cards/${cardId}`, { method: "PATCH", json: { front, back } }); refreshRelated(); toast("卡片已更新，复习记录保留"); }
    catch (e) { toast((e as Error).message, "bad"); }
    finally { actionLock.current = false; setBusy(""); }
  };
  const completeTask = async (item: Task) => {
    if (actionLock.current) return;
    actionLock.current = true; setBusy(item.id);
    try { await api(`/api/tasks/${item.id}`, { method: "PATCH", json: { status: item.status === "done" ? "todo" : "done" } }); refreshRelated(); }
    catch (e) { toast((e as Error).message, "bad"); }
    finally { actionLock.current = false; setBusy(""); }
  };
  const eligiblePlans = plans?.filter((p) => !p.archived && (!p.course_id || p.course_id === data?.card.course_id));
  const source = data?.source_note;
  return <Modal open onClose={onClose} title="薄弱知识点学习" width={740}>
    <div className="p-5 space-y-6">
      {error && <p className="text-bad">{(error as Error).message}<button className="btn" onClick={() => mutate()}>重试读取</button></p>}
      {!data && !error && <p className="text-muted">正在读取卡片与来源…</p>}
      {storageError && <p className="text-bad text-[13px]" role="alert">{storageError}</p>}
      {recoverable.length > 0 && <section className="rounded-xl border border-rule p-3 space-y-2">
        <h3 className="font-semibold text-[13px]">发现本机其他学习草稿</h3>
        <p className="text-[12px] text-muted">刷新前和其他窗口的内容分别保存。恢复前会另存当前草稿；不会自动替换输入。</p>
        {recoverable.map((record) => <details key={record.key} className="text-[12px] border-t border-rule pt-2">
          <summary className="cursor-pointer truncate">{record.value.text || record.value.title}</summary>
          <textarea readOnly className="input mt-2" rows={5} aria-label="本地草稿全文" value={studyDraftText(record.value)} />
          <div className="flex gap-2 mt-2">
            <button className="btn btn-outline h-7" disabled={!!busy || !!pendingUnderstanding || !!pendingTask} onClick={() => recover(record)}>恢复这份草稿</button>
            <button className="btn btn-outline h-7" onClick={() => copyText(studyDraftText(record.value))}>复制全文</button>
            <button className="btn btn-outline h-7" onClick={() => exportText(studyDraftText(record.value))}>下载文本</button>
            <button className="btn h-7 text-muted" disabled={!!busy} onClick={() => {
              try { forgetStudyDraft(localStorage, record); setRecoverable(studyDrafts(localStorage, cardId, owner)); }
              catch { toast("无法删除草稿，请检查浏览器存储", "bad"); }
            }}>删除副本</button>
          </div>
        </details>)}
      </section>}
      {!data && (draft.text || draft.taskEdited) && <section className="space-y-2">
        <h3 className="font-semibold text-[13px]">本窗口本地草稿</h3>
        <p className="text-[12px] text-muted">卡片暂不可读取，输入仍保留在本机，可复制或下载后继续。</p>
        <textarea readOnly className="input" rows={5} aria-label="本窗口草稿全文" value={studyDraftText(draft)} />
        <button className="btn btn-outline" onClick={() => copyText(studyDraftText(draft))}>复制全文</button>
        <button className="btn btn-outline ml-2" onClick={() => exportText(studyDraftText(draft))}>下载文本</button>
      </section>}
      {(pendingUnderstanding || pendingTask) && <section className="rounded-xl border border-[var(--bad)] p-3 space-y-2" role="status">
        <h3 className="font-semibold text-[13px]">待确认的学习操作</h3>
        <p className="text-[12px] text-muted">来源或卡片暂时不可读取，但后端仍会按操作编号返回原结果。请先重试确认；不会创建新的操作。</p>
        {pendingRecoveryView(pendingUnderstanding, pendingTask).map((item) => <div key={item.operationId} className="space-y-2 text-[12px]">
          <p>{item.title}</p>
          <textarea readOnly className="input" rows={3} aria-label="待确认操作原内容" value={item.text} />
          <button className="btn btn-primary h-7" disabled={!!busy} onClick={item.kind === "understanding" ? submitUnderstanding : submitTask}>{busy === item.kind ? "确认中…" : "重试确认"}</button>
          <button className="btn btn-outline h-7 ml-2" onClick={() => copyText(item.text)}>复制原内容</button>
        </div>)}
        <Link className="btn btn-outline h-7" href="/settings">前往设置查看回收站</Link>
      </section>}
      {pendingResult && <div className="rounded-lg border border-rule p-3 text-[12px] text-accent space-y-2" role="status">
        <p>{pendingResult}</p>
        {resultLink && <Link className="btn btn-outline h-7" href={resultLink.href}>{resultLink.label}</Link>}
        {!data && <Link className="btn btn-outline h-7 ml-2" href="/settings">前往设置查看回收站</Link>}
      </div>}
      {data && <>
        <div className="text-[13px] text-muted space-y-1">
          <p>累计忘记 {data.card.forgot_count} 次 · 当前连续记住 {data.card.n} 轮 · 间隔 {data.card.interval} 天 · 下次复习 {data.card.due}</p>
          <p>学习任务记录行动进度；知识掌握程度仍由之后的复习评分反映。</p>
          {data.reviews.length > 0 && <p>近期评分：{data.reviews.map((r) => `${r.logical_day} ${{ forgot: "忘记", hard: "费力", good: "记住" }[r.grade] || r.grade}`).join("；")}</p>}
        </div>
        <section className="space-y-2">
          <h3 className="font-semibold text-[14px]">回到来源</h3>
          {source?.available ? <Link className="text-accent text-[14px]" href={`/notes?id=${source.id}`}>打开笔记：{source.title}</Link>
            : <p className="text-[13px] text-muted">{data.source_status === "manual" ? "手动卡片没有来源笔记。" : data.source_status === "deleted" ? "来源笔记在回收站，可先恢复笔记。" : "来源笔记已不可用。"}仍可编辑卡片或创建学习任务。</p>}
          {source?.sources.map((s, i) => <p key={i} className="text-[13px]">{s.available
            ? <Link className="text-accent" href={`/materials/view?id=${s.material_id}${s.unit_id ? `&unit=${s.unit_id}` : ""}`}>{s.material_name} · {s.label || s.locator}</Link>
            : <span className="text-faint">{s.material_name} · 资料已不可用</span>}</p>)}
        </section>
        <section className="space-y-2">
          <h3 className="font-semibold text-[14px]">补充理解到来源笔记</h3>
          <textarea className="input" rows={4} maxLength={200000} value={pendingUnderstanding ? String(pendingUnderstanding.text || "") : draft.text}
            disabled={!!pendingUnderstanding || !!busy} placeholder="用自己的话解释，记录例子或容易混淆的地方…" onChange={(e) => update({ text: e.target.value })} />
          {pendingUnderstanding && <p className="text-[12px] text-bad">上次提交结果待确认，已保存原内容和操作编号。请重试确认，暂不修改内容。</p>}
          {draft.needsRefresh && <p className="text-[12px] text-bad">来源笔记已变化，补充内容仍保留。先打开来源核对，再读取最新版本。</p>}
          <div className="flex gap-2">
            {draft.needsRefresh && <button className="btn btn-outline" disabled={!!busy} onClick={refreshBase}>读取最新版本</button>}
          <button className="btn btn-primary" onClick={submitUnderstanding} disabled={!ready || !!busy || (!pendingUnderstanding && (!source?.available || !draft.text.trim() || draft.needsRefresh || draft.baseVersion === null))}>
              {busy === "understanding" ? "提交中…" : pendingUnderstanding ? "重试原补充" : "追加到来源笔记"}</button>
            <span className="text-[12px] text-faint self-center">输入保存在本机；追加成功后可在笔记历史撤销。</span>
          </div>
        </section>
        <section className="space-y-2">
          <h3 className="font-semibold text-[14px]">编辑卡片</h3>
          <input className="input" aria-label="卡片正面" maxLength={200} value={front} disabled={!!busy} onChange={(e) => setFront(e.target.value)} />
          <textarea className="input" aria-label="卡片背面" rows={3} maxLength={2000} value={back} disabled={!!busy} onChange={(e) => setBack(e.target.value)} />
          <button className="btn btn-outline" disabled={!!busy || !front.trim() || !back.trim()} onClick={saveCard}>{busy === "card" ? "保存中…" : "保存卡片"}</button>
        </section>
        <section className="space-y-2">
          <h3 className="font-semibold text-[14px]">创建关联学习任务</h3>
          {pendingTask && <p className="text-[12px] text-bad">原任务「{String(pendingTask.title)}」结果待确认。重试会沿用原计划、日期和操作编号。</p>}
          <fieldset disabled={!!busy || !!pendingTask} className="space-y-2">
            <select className="input" aria-label="关联计划" value={draft.plan} onChange={(e) => update({ plan: e.target.value, taskEdited: true })}>
              <option value="new">新建同学科学习计划</option>
              {eligiblePlans?.map((p) => <option key={p.id} value={p.id}>{p.goal}</option>)}
            </select>
            {draft.plan === "new" && <input className="input" aria-label="新计划名称" maxLength={200} value={draft.goal} onChange={(e) => update({ goal: e.target.value, taskEdited: true })} />}
            <input className="input" aria-label="学习任务名称" maxLength={200} value={draft.title} onChange={(e) => update({ title: e.target.value, taskEdited: true })} />
            <div className="flex gap-3">
              <label className="text-[12px] text-muted">安排日期（可留空）<input type="date" className="input" value={draft.due} onChange={(e) => update({ due: e.target.value, taskEdited: true })} /></label>
              <label className="text-[12px] text-muted">预计分钟<input type="number" className="input" min={1} max={1440} value={draft.minutes} onChange={(e) => update({ minutes: e.target.value, taskEdited: true })} /></label>
            </div>
          </fieldset>
          <button className="btn btn-outline" disabled={!ready || !!busy || (!pendingTask && (!draft.title.trim() || (draft.plan === "new" && !draft.goal.trim())))} onClick={submitTask}>
            {busy === "task" ? "提交中…" : pendingTask ? "重试原任务" : "创建学习任务"}</button>
          {data.tasks.map((item) => <div key={item.id} className="flex items-center gap-2 text-[13px] border-t border-rule pt-2">
            <button className="btn btn-outline h-7" disabled={!!busy || !!item.plan_archived} onClick={() => completeTask(item)}>{item.status === "done" ? "已完成 ✓" : "标为完成"}</button>
            <Link className="text-accent" href={`/plans?id=${item.plan_id}`}>{item.title}</Link>
            <span className="text-faint">{item.due_date || "未安排"}{item.plan_archived ? " · 计划已归档" : ""}</span>
          </div>)}
        </section>
      </>}
    </div>
  </Modal>;
}
