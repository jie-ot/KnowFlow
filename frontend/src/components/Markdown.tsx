"use client";

import Link from "next/link";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

/** 旧格式 /materials/<id>?unit=… 转成 /materials/view?id=…&unit=… */
const appHref = (href: string) => {
  const m = href.match(/^\/materials\/([0-9a-f-]{36})(?:\?unit=([0-9a-f-]+))?/);
  return m ? `/materials/view?id=${m[1]}${m[2] ? `&unit=${m[2]}` : ""}` : href;
};

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
              <Link href={appHref(href)}>{children}</Link>
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
