"use client";

import Link from "next/link";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { NoteLinks } from "@/lib/api";

/** 旧格式 /materials/<id>?unit=… 转成 /materials/view?id=…&unit=… */
const appHref = (href: string) => {
  const m = href.match(/^\/materials\/([0-9a-f-]{36})(?:\?unit=([0-9a-f-]+))?/);
  return m ? `/materials/view?id=${m[1]}${m[2] ? `&unit=${m[2]}` : ""}` : href;
};

const CODE = /```[\s\S]*?```|`[^`\n]*`/g;
const WIKI = /\[\[([^[\]\n|]{1,100})(?:\|([^[\]\n]{0,100}))?\]\]/g;
const WIKI_PREFIX = "/notes/?wiki=";
const STABLE = /^id:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;

/** 把 [[标题]] / [[标题|文字]] 转成普通链接；代码里的不动 */
function wikify(text: string) {
  const conv = (s: string) =>
    s.replace(WIKI, (_, title: string, label?: string) =>
      `[${(label || title).trim()}](${WIKI_PREFIX}${encodeURIComponent(title.trim())})`);
  let out = "";
  let last = 0;
  for (const m of text.matchAll(CODE)) {
    out += conv(text.slice(last, m.index)) + m[0];
    last = (m.index ?? 0) + m[0].length;
  }
  return out + conv(text.slice(last));
}

/**
 * 渲染 Markdown：GFM 表格 / 脚注、LaTeX 公式、代码高亮、[[双链]]。原始 HTML 不渲染。
 * known：已存在笔记的小写标题，用来区分“已有笔记”和“还没建的笔记”。
 */
export function Markdown({ text, className = "", known, noCreate, noteId, links }: {
  text: string; className?: string; known?: Set<string>;
  noteId?: string; links?: NoteLinks["outgoing"];
  /** 为 true 时，指向不存在笔记的 [[链接]] 只显示文字，点击不会新建（用于 AI 助手的回答） */
  noCreate?: boolean;
}) {
  return (
    <div className={`prose-kf ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }], [rehypeHighlight, { detect: false }]]}
        components={{
          a: ({ href = "", children, ...rest }) => {
            if (href.startsWith(WIKI_PREFIX)) {
              const title = decodeURIComponent(href.slice(WIKI_PREFIX.length));
              const stable = title.match(STABLE);
              const resolved = links?.find((link) => link.target.toLowerCase() === title.toLowerCase());
              const targetId = stable?.[1] || resolved?.note_id || resolved?.bound_id;
              if (targetId) return <Link href={`/notes?id=${encodeURIComponent(targetId)}`} className="wikilink"
                title={resolved?.status === "missing" ? "原笔记已删除，链接仍保留原身份" : `打开「${resolved?.title || children}」`}>{children}</Link>;
              const missing = known ? !known.has(title.toLowerCase()) : false;
              if (missing && noCreate) return <span>{children}</span>;
              return (
                <Link href={`/notes?wiki=${encodeURIComponent(title)}${noteId ? `&from=${encodeURIComponent(noteId)}` : ""}`} className={missing ? "wikilink wikilink-missing" : "wikilink"}
                  title={resolved?.status === "ambiguous" ? "存在同名笔记，点击明确选择目标" : missing ? `还没有「${title}」这篇笔记，点击新建` : `打开「${title}」`}>
                  {children}
                </Link>
              );
            }
            if (href.startsWith("/")) return <Link href={appHref(href)}>{children}</Link>;
            if (href.startsWith("#")) return <a href={href} {...rest}>{children}</a>;
            return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
          },
        }}
      >
        {text.includes("[[") ? wikify(text) : text}
      </ReactMarkdown>
    </div>
  );
}
