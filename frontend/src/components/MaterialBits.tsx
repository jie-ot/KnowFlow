"use client";

import { FileCode2, FileImage, FileText, Presentation } from "lucide-react";
import { MATERIAL_STATUS } from "@/lib/format";
import { Spinner } from "./ui";

export const FORMAT_ICON: Record<string, typeof FileText> = {
  pdf: FileText, pptx: Presentation, docx: FileText, text: FileText, md: FileText, code: FileCode2, image: FileImage,
};

export function StatusPill({ status }: { status: string }) {
  const s = MATERIAL_STATUS[status] || { label: status, tone: "muted" };
  const style = {
    ok: { background: "var(--accent-soft)", color: "var(--accent)" },
    warn: { background: "var(--warn-soft)", color: "var(--warn)" },
    bad: { background: "var(--bad-soft)", color: "var(--bad)" },
    muted: { background: "var(--hover)", color: "var(--muted)" },
  }[s.tone];
  return (
    <span className="inline-flex items-center gap-1.5 h-6 px-2 rounded-md text-[12px] font-medium" style={style}>
      {(status === "extracting" || status === "uploaded") && <Spinner size={10} />}{s.label}
    </span>
  );
}

