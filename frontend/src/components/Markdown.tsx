"use client";

import Link from "next/link";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

/** 渲染 Markdown：GFM 表格 / 脚注、LaTeX 公式、代码高亮。原始 HTML 不渲染。 */
export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div className={`prose-kf ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }], [rehypeHighlight, { detect: false }]]}
        components={{
          a: ({ href = "", children, ...rest }) =>
            href.startsWith("/") ? (
              <Link href={href}>{children}</Link>
            ) : href.startsWith("#") ? (
              <a href={href} {...rest}>
                {children}
              </a>
            ) : (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
