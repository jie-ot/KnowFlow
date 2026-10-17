"use client";

import { FileText, UploadCloud } from "lucide-react";
import { FORMAT_ICON, StatusPill } from "@/components/MaterialBits";
import Link from "next/link";
import { useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { api, Course, fetcher, Material } from "@/lib/api";
import { relTime, sizeLabel } from "@/lib/format";
import { Dot, Empty, PageHeader, Spinner, useToast } from "@/components/ui";

export default function MaterialsPage() {
  const [course, setCourse] = useState("");
  const [drag, setDrag] = useState(false);
  const [uploading, setUploading] = useState(false);
  const { data: courses } = useSWR<Course[]>("/api/courses", fetcher);
  const target = course || courses?.[0]?.id || "";
  const key = `/api/materials${course ? `?course_id=${course}` : ""}`;
  const { data: items, mutate } = useSWR<Material[]>(key, fetcher, {
    refreshInterval: (d) => (d?.some((m) => m.status === "extracting" || m.status === "uploaded") ? 1000 : 0),
  });
  const courseMap = Object.fromEntries((courses || []).map((c) => [c.id, c]));
  const toast = useToast();

  const upload = async (files: FileList | File[] | null) => {
    if (!files || !files.length || !target) return;
    const form = new FormData();
    form.append("course_id", target);
    Array.from(files).forEach((f) => form.append("files", f));
    setUploading(true);
    try {
      const r = await api<{ results: { name: string; ok: boolean; message?: string }[] }>("/api/materials", { method: "POST", body: form });
      const bad = r.results.filter((x) => !x.ok);
      if (bad.length) toast(bad.map((b) => `${b.name}：${b.message}`).join("；"), "bad");
      else toast(`已上传 ${r.results.length} 个文件，正在解析`);
      mutate();
      globalMutate("/api/courses");
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="max-w-[920px] mx-auto px-10 py-12">
      <PageHeader title="资料" sub="上传课件、讲义、作业和代码，选中其中的片段让 AI 整理成带出处的笔记。"
        actions={
          <select className="input h-8 w-auto text-[13px]" value={course} onChange={(e) => setCourse(e.target.value)} aria-label="学科">
            <option value="">全部学科</option>
            {courses?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        } />

      <label
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files); }}
        className="flex flex-col items-center justify-center gap-2 h-36 rounded-2xl border border-dashed cursor-pointer transition-colors mb-10"
        style={{ borderColor: drag ? "var(--accent)" : "var(--faint)", background: drag ? "var(--accent-soft)" : "var(--canvas)" }}>
        <input type="file" multiple hidden accept=".pdf,.pptx,.docx,.txt,.md,.markdown,.cpp,.cc,.c,.h,.hpp,.py,.java,.png,.jpg,.jpeg"
          onChange={(e) => { upload(e.target.files); e.target.value = ""; }} />
        {uploading ? <Spinner size={18} /> : <UploadCloud size={22} className="text-muted" strokeWidth={1.6} />}
        <span className="text-[14px]">拖到这里，或<span className="text-accent font-medium">选择文件</span></span>
        <span className="text-[12px] text-muted">
          上传到「{courseMap[target]?.name || "—"}」。PDF、PPTX、DOCX、TXT、MD、代码、图片；一次最多 5 个，每个不超过 20 MB
        </span>
      </label>

      <div className="border-y border-rule divide-y divide-rule">
        {items?.map((m) => {
          const Icon = FORMAT_ICON[m.format] || FileText;
          return (
            <Link key={m.id} href={`/materials/${m.id}`} className="flex items-center gap-4 py-3.5 px-1 row-hover rounded-lg">
              <Icon size={20} className="text-muted shrink-0" strokeWidth={1.6} />
              <div className="flex-1 min-w-0">
                <p className="text-[14px] font-medium truncate">{m.display_title}</p>
                <p className="text-[12.5px] text-muted mt-0.5 flex items-center gap-2">
                  <Dot color={courseMap[m.course_id]?.color} size={6} />
                  {m.original_name}，{sizeLabel(m.size_bytes)}
                  {m.unit_count > 0 && `，${m.unit_count} 个片段，${m.char_count.toLocaleString()} 字`}
                </p>
                {m.error_message && <p className="text-[12px] text-warn mt-0.5">{m.error_message}</p>}
              </div>
              <span className="text-[12px] text-faint">{relTime(m.uploaded_at)}</span>
              <StatusPill status={m.status} />
            </Link>
          );
        })}
      </div>
      {items?.length === 0 && <Empty title="还没有资料">把这门课的 PPT 或讲义拖进来试试。</Empty>}
    </div>
  );
}
