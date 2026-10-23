"use client";

import { BookOpen, Layers } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { mutate } from "swr";
import { api, Card, Course } from "@/lib/api";
import { CourseDialog } from "./Shell";
import { Modal, Spinner, useToast } from "./ui";

export type CardSource = { id: string; title: string; course_id: string; selection: string };

export function CardEditor({ card, courses, source, onClose, onSaved }: {
  card: Partial<Card> | null; courses?: Course[]; source?: CardSource;
  onClose: () => void; onSaved: (card: Card) => void;
}) {
  const [front, setFront] = useState("");
  const [back, setBack] = useState("");
  const [course, setCourse] = useState("");
  const [saving, setSaving] = useState(false);
  const [addingCourse, setAddingCourse] = useState(false);
  const savingRef = useRef(false);
  const toast = useToast();
  const open = card !== null;
  const isNew = open && !card.id;

  useEffect(() => {
    if (!open) return;
    setFront(card?.front || source?.title || "");
    setBack(card?.back || source?.selection || "");
    setCourse(source?.course_id || card?.course_id || courses?.[0]?.id || "");
  }, [open, card?.id, source?.id]); // 表单打开时初始化，刷新学科不会清空正在填写的内容。

  useEffect(() => { if (open && !course && courses?.length) setCourse(courses[0].id); }, [open, course, courses]);
  const save = async () => {
    if (!front.trim() || !back.trim() || savingRef.current || (isNew && !course)) return;
    savingRef.current = true; setSaving(true);
    try {
      const result = isNew
        ? await api<Card>("/api/cards", { method: "POST", json: { course_id: course, front: front.trim(), back: back.trim(), source_note_id: source?.id || card?.source_note_id || null } })
        : await api<Card>(`/api/cards/${card!.id}`, { method: "PATCH", json: { front: front.trim(), back: back.trim() } });
      mutate("/api/courses"); mutate("/api/stats?days=365");
      onSaved(result); onClose();
      toast(isNew ? "卡片已保存，今天可复习" : "卡片已更新");
    } catch (e) { toast((e as Error).message, "bad"); }
    finally { savingRef.current = false; setSaving(false); }
  };

  return <>
    <Modal open={open} onClose={() => { if (!saving) onClose(); }} title={isNew ? "制作复习卡" : "编辑卡片"} width={560}
      footer={<><button className="btn btn-outline" disabled={saving} onClick={onClose}>取消</button><button className="btn btn-primary" disabled={saving || !front.trim() || !back.trim() || (isNew && !course)} onClick={save}>{saving ? <Spinner /> : <Layers size={14} />}保存卡片</button></>}>
      <div className="p-6 space-y-5">
        {source ? <p className="flex items-center gap-2 text-[12px] text-muted"><BookOpen size={14} /><span className="truncate">来自《{source.title}》</span></p> : isNew && <div>
          <label className="label" htmlFor="card-course">学科</label>
          <div className="flex gap-2"><select id="card-course" className="input" value={course} onChange={(e) => setCourse(e.target.value)}>
            {!courses?.length && <option value="">请选择学科</option>}
            {courses?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>{!courses?.length && <button className="btn btn-outline h-10" onClick={() => setAddingCourse(true)}>新建学科</button>}</div>
        </div>}
        <div><label className="label" htmlFor="card-front">正面 · 问题</label><input id="card-front" className="input" maxLength={200} value={front} onChange={(e) => setFront(e.target.value)} placeholder="你希望自己能回答什么？" /></div>
        <div><label className="label" htmlFor="card-back">背面 · 答案</label><textarea id="card-back" className="input !leading-7" rows={6} maxLength={2000} value={back} onChange={(e) => setBack(e.target.value)} placeholder="写下关键要点" /><p className="mt-1.5 text-right text-[11px] text-muted">{back.length} / 2000</p></div>
      </div>
    </Modal>
    <CourseDialog open={addingCourse} onClose={() => setAddingCourse(false)} onSaved={(c) => { setCourse(c.id); mutate("/api/courses"); }} />
  </>;
}
