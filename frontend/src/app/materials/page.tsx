"use client";

import { FileText, Plus, Search, UploadCloud, X } from "lucide-react";
import { FORMAT_ICON, StatusPill } from "@/components/MaterialBits";
import Link from "next/link";
import { useRef, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { api, Course, fetcher, Material } from "@/lib/api";
import { relTime, sizeLabel } from "@/lib/format";
import { Dot, Empty, Modal, PageHeader, Spinner, useToast } from "@/components/ui";
import { CourseDialog } from "@/components/Shell";

export default function MaterialsPage() {
  const [course, setCourse] = useState("");
  const [drag, setDrag] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [filter, setFilter] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [destination, setDestination] = useState("");
  const [addingCourse, setAddingCourse] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const { data: courses } = useSWR<Course[]>("/api/courses", fetcher);
  const key = `/api/materials${course ? `?course_id=${course}` : ""}`;
  const { data: items, error, mutate } = useSWR<Material[]>(key, fetcher, {
    refreshInterval: (d) => (d?.some((m) => m.status === "extracting" || m.status === "uploaded") ? 1000 : 0),
  });
  const courseMap = Object.fromEntries((courses || []).map((c) => [c.id, c]));
  const toast = useToast();
  const shown = items?.filter((m) => `${m.display_title} ${m.original_name}`.toLowerCase().includes(filter.trim().toLowerCase()));

  const chooseFiles = (selection: FileList | File[] | null) => {
    if (!selection?.length || uploading) return;
    const chosen = Array.from(selection);
    if (chosen.length > 5) { toast("一次最多上传 5 个文件", "bad"); return; }
    const oversized = chosen.find((f) => f.size > 20_000_000);
    if (oversized) { toast(`「${oversized.name}」超过 20 MB`, "bad"); return; }
    setDestination(course || (courses?.length === 1 ? courses[0].id : ""));
    setFiles(chosen);
  };

  const upload = async () => {
    if (!files.length || !destination || uploading) return;
    const form = new FormData();
    form.append("course_id", destination);
    Array.from(files).forEach((f) => form.append("files", f));
    setUploading(true);
    try {
      const r = await api<{ results: { name: string; ok: boolean; message?: string }[] }>("/api/materials", { method: "POST", body: form });
      const bad = r.results.filter((x) => !x.ok);
      if (bad.length) toast(bad.map((b) => `${b.name}：${b.message}`).join("；"), "bad");
      else toast(`已上传 ${r.results.length} 个文件，正在解析`);
      setFiles((selected) => selected.filter((f) => bad.some((b) => b.name === f.name)));
      setCourse(destination);
      mutate();
      globalMutate("/api/courses");
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="desktop-page">
      <PageHeader title="资料" actions={<button className="btn btn-primary" onClick={() => input.current?.click()}><UploadCloud size={15} />上传资料</button>} />
      <input ref={input} type="file" multiple hidden accept=".pdf,.pptx,.docx,.txt,.md,.markdown,.cpp,.cc,.c,.h,.hpp,.py,.java,.png,.jpg,.jpeg"
        onChange={(e) => { chooseFiles(e.target.files); e.target.value = ""; }} />
      <div className="flex items-center gap-3 mb-5 flex-wrap">
          <div className="relative flex-1 min-w-48">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input className="input pl-9" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="搜索资料名称" aria-label="搜索资料名称" />
          </div>
          <select className="input w-auto text-[13px] max-w-52" value={course} onChange={(e) => setCourse(e.target.value)} aria-label="筛选学科">
            <option value="">全部学科</option>
            {courses?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); chooseFiles(e.dataTransfer.files); }}
        className="flex items-center justify-center gap-3 py-6 px-4 rounded-xl border border-dashed transition-colors mb-7"
        style={{ borderColor: drag ? "var(--accent)" : "var(--faint)", background: drag ? "var(--accent-soft)" : "var(--canvas)" }}>
        {uploading ? <Spinner size={18} /> : <UploadCloud size={22} className="text-muted" strokeWidth={1.6} />}
        <div><p className="text-[14px]">拖入文件，或<button className="text-accent font-medium ml-1" onClick={() => input.current?.click()}>选择文件</button></p>
          <p className="text-[12px] text-muted mt-1">PDF、课件、文档、代码、图片 · 每次 5 个以内，单个不超过 20 MB</p>
        </div>
      </div>

      {error ? <Empty title="资料加载失败"><button className="btn btn-outline mt-3" onClick={() => mutate()}>重新加载</button></Empty> : !items ? <div role="status" className="text-muted py-8"><Spinner /> 正在加载…</div> : <>
      <div className="flex justify-between text-[12px] text-muted pb-3 border-b border-rule"><span>{shown?.length} 份资料</span><span>最近上传</span></div>
      <div className="divide-y divide-rule">
        {shown?.map((m) => {
          const Icon = FORMAT_ICON[m.format] || FileText;
          return (
            <Link key={m.id} href={`/materials/view?id=${m.id}`} className="flex items-center gap-4 py-3.5 px-1 row-hover rounded-lg">
              <Icon size={20} className="text-muted shrink-0" strokeWidth={1.6} />
              <div className="flex-1 min-w-0">
                <p className="text-[14px] font-medium truncate">{m.display_title}</p>
                <p className="text-[12.5px] text-muted mt-1 flex items-center gap-2 min-w-0">
                  <Dot color={courseMap[m.course_id]?.color} size={6} />
                  <span className="truncate">{courseMap[m.course_id]?.name}</span><span className="shrink-0">· {m.ext.replace(/^\./, "").toUpperCase()} · {sizeLabel(m.size_bytes)}</span>
                </p>
                {m.error_message && <p className="text-[12px] text-warn mt-0.5">{m.error_message}</p>}
              </div>
              <span className="text-[12px] text-faint">{relTime(m.uploaded_at)}</span>
              <StatusPill status={m.status} />
            </Link>
          );
        })}
      </div>
      {shown?.length === 0 && <Empty icon={<FileText size={28} strokeWidth={1.5} />} title={filter ? "没有找到匹配的资料" : "还没有资料"}>{filter ? <button className="btn btn-outline mt-3" onClick={() => setFilter("")}>清除搜索</button> : "上传后可查看原文，并整理成笔记。"}</Empty>}
      </>}
      <Modal open={files.length > 0} onClose={() => { if (!uploading) setFiles([]); }} title="上传资料" footer={<>
        <button className="btn btn-outline" disabled={uploading} onClick={() => setFiles([])}>取消</button>
        <button className="btn btn-primary" disabled={!destination || uploading} onClick={upload}>{uploading ? <><Spinner />上传中…</> : `上传 ${files.length} 个文件`}</button>
      </>}>
        <div className="p-5 space-y-5">
          <div>
            <label className="label" htmlFor="upload-course">保存到学科</label>
            <div className="flex gap-2">
              <select id="upload-course" className="input" value={destination} onChange={(e) => setDestination(e.target.value)} disabled={uploading}>
                <option value="">请选择学科</option>
                {courses?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
              <button className="btn btn-outline h-9" disabled={uploading} onClick={() => setAddingCourse(true)}><Plus size={14} />新建</button>
            </div>
          </div>
          <ul className="divide-y divide-rule">{files.map((f, i) => <li key={`${f.name}-${i}`} className="flex items-center gap-3 py-3 text-[13px]">
            <FileText size={16} className="text-muted shrink-0" /><span className="flex-1 truncate" title={f.name}>{f.name}</span><span className="text-muted">{sizeLabel(f.size)}</span>
            <button className="btn btn-icon" disabled={uploading} aria-label={`移除 ${f.name}`} onClick={() => setFiles((v) => v.filter((_, j) => j !== i))}><X size={14} /></button>
          </li>)}</ul>
        </div>
      </Modal>
      <CourseDialog open={addingCourse} onClose={() => setAddingCourse(false)} onSaved={(c) => { setDestination(c.id); globalMutate("/api/courses"); }} />
    </div>
  );
}
