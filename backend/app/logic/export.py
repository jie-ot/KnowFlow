"""导出：单篇 .md（我的 FR-04）与多篇离线 HTML（同学 FR-07 / DC-05）。

离线 HTML 的要求：单个文件、样式内嵌、不依赖 CDN 和脚本；
公式转为 MathML（浏览器原生渲染），代码用 Pygments 生成带样式的高亮；
用户 Markdown 中的原始 HTML 一律转义，不执行。
"""

from __future__ import annotations

import html
import re
from typing import Any

from latex2mathml.converter import convert as latex_to_mathml
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.footnote import footnote_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

from .links import map_links

_FORMATTER = HtmlFormatter(cssclass="hl", style="friendly")


def _math(content: str, options: dict[str, Any]) -> str:
    display = "block" if options.get("display_mode") else "inline"
    try:
        return latex_to_mathml(content, display=display)
    except Exception:
        return f'<code class="tex">{html.escape(content)}</code>'


def _fence(self, tokens, idx, options, env) -> str:  # type: ignore[no-untyped-def]
    token = tokens[idx]
    lang = (token.info or "").strip().split(" ")[0]
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    label = f'<span class="lang">{html.escape(lang)}</span>' if lang else ""
    return f'<figure class="code">{label}{highlight(token.content, lexer, _FORMATTER)}</figure>\n'


def _heading_open(prefix: str):  # type: ignore[no-untyped-def]
    counter = {"n": 0}

    def render(self, tokens, idx, options, env) -> str:  # type: ignore[no-untyped-def]
        counter["n"] += 1
        tag = tokens[idx].tag
        # 笔记内的一级标题在导出中降为二级，避免与笔记标题同级
        level = min(int(tag[1]) + 1, 6)
        return f'<h{level} id="{prefix}-h{counter["n"]}">'

    return render


def _heading_close(self, tokens, idx, options, env) -> str:  # type: ignore[no-untyped-def]
    level = min(int(tokens[idx].tag[1]) + 1, 6)
    return f"</h{level}>\n"


_APP_LINK = re.compile(r"\[([^\]]+)\]\((/[^)]*)\)")


def render_markdown(text: str, prefix: str, anchors: dict[str, str] | None = None) -> str:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    md.enable("table").enable("strikethrough")
    md.use(dollarmath_plugin, allow_space=True, double_inline=True, renderer=_math)
    md.use(footnote_plugin)
    md.add_render_rule("fence", _fence)
    md.add_render_rule("heading_open", _heading_open(prefix))
    md.add_render_rule("heading_close", _heading_close)
    # 应用内链接（如资料定位）在离线文件里无法打开，只保留文字
    cleaned = _APP_LINK.sub(r"\1", text or "")
    # [[双链]]：导出的文件里有这篇笔记就跳到它，没有就只保留文字
    anchors = anchors or {}
    cleaned = map_links(cleaned, lambda title, label: f"[{label}](#{anchors[title.lower()]})"
                        if title.lower() in anchors else label)
    return md.render(cleaned, {"docId": prefix})


def note_to_markdown(note: dict[str, Any], course_name: str) -> str:
    def yaml_str(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    tags = ", ".join(yaml_str(t) for t in note.get("tags") or [])
    front = [
        "---",
        f"title: {yaml_str(note['title'])}",
        f"subject: {yaml_str(course_name)}",
        f"tags: [{tags}]",
        f"created: {note['created_at']}",
        f"updated: {note['updated_at']}",
        "---",
        "",
    ]
    return "\n".join(front) + (note.get("content") or "").rstrip() + "\n"


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """解析导入 .md 的 YAML 头（只认 title / subject / tags 三个字段）。"""
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"^---\s*\n([\s\S]*?)\n---\s*\n?", text)
    if not match:
        return {}, text
    meta: dict[str, Any] = {}
    for line in match.group(1).split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "tags":
            inner = value.strip("[]")
            meta["tags"] = [t.strip().strip('"\'') for t in inner.split(",") if t.strip().strip('"\'')]
        elif key in ("title", "subject"):
            meta[key] = value.strip().strip('"\'')
    return meta, text[match.end():]


TASK_KIND_LABEL = {"learn": "学习", "review": "复习", "practice": "练习", "read": "阅读"}
TASK_STATUS_LABEL = {"todo": "待办", "in_progress": "进行中", "done": "已完成"}


def build_html(
    *,
    title: str,
    exported_at: str,
    courses: dict[str, dict[str, Any]],
    notes: list[dict[str, Any]],
    cards_by_note: dict[str, list[dict[str, Any]]],
    plan: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
    link_targets: dict[str, dict[str, str | None]] | None = None,
) -> str:
    esc = html.escape
    course_names = sorted({courses[n["course_id"]]["name"] for n in notes if n["course_id"] in courses})

    toc_items = []
    sections = []
    anchors = {}
    title_counts: dict[str, int] = {}
    for note in notes:
        key = note["title"].strip().lower()
        title_counts[key] = title_counts.get(key, 0) + 1
    for index, note in enumerate(notes, start=1):
        anchors[f"id:{note['id']}"] = f"n{index}"
        key = note["title"].strip().lower()
        if title_counts[key] == 1:
            anchors[key] = f"n{index}"
    for index, note in enumerate(notes, start=1):
        anchor = f"n{index}"
        course = courses.get(note["course_id"], {"name": "未分类"})
        toc_items.append(f'<li><a href="#{anchor}">{esc(note["title"])}</a><span>{esc(course["name"])}</span></li>')
        tags = "".join(f"<span class=\"tag\">{esc(t)}</span>" for t in note.get("tags") or [])
        source_anchors = dict(anchors)
        for target, target_id in (link_targets or {}).get(note["id"], {}).items():
            actual = anchors.get(f"id:{target_id}") if target_id else None
            if actual:
                source_anchors[target] = actual
            else:
                source_anchors.pop(target, None)
        body = render_markdown(note.get("content") or "", anchor, source_anchors)
        sections.append(
            f'<article class="note" id="{anchor}">'
            f'<header><p class="course">{esc(course["name"])}</p><h2>{esc(note["title"])}</h2>'
            f'<p class="meta">更新于 {esc(note["updated_at"][:10])}{tags}</p></header>'
            f'<div class="body">{body}</div></article>'
        )

    index_rows = []
    for index, note in enumerate(notes, start=1):
        for card in cards_by_note.get(note["id"], []):
            index_rows.append(
                f'<tr><td>{esc(card["front"])}</td><td><a href="#n{index}">{esc(note["title"])}</a></td></tr>'
            )
    knowledge = (
        '<table class="index"><thead><tr><th>知识点</th><th>所在笔记</th></tr></thead>'
        f"<tbody>{''.join(index_rows)}</tbody></table>"
        if index_rows else '<p class="empty">所选笔记还没有知识点卡片。</p>'
    )

    if plan:
        rows = []
        for task in tasks:
            rows.append(
                "<tr>"
                f'<td>{esc(task.get("due_date") or "未排期")}</td>'
                f'<td>{esc(task["title"])}</td>'
                f'<td>{esc(TASK_KIND_LABEL.get(task["kind"], task["kind"]))}</td>'
                f'<td>{esc(str(task["estimated_minutes"]) + " 分钟" if task.get("estimated_minutes") else "—")}</td>'
                f'<td>{esc(TASK_STATUS_LABEL.get(task["status"], task["status"]))}'
                f'{"（需加强）" if task.get("needs_review") else ""}</td>'
                "</tr>"
            )
        plan_meta = []
        if plan.get("deadline"):
            plan_meta.append(f"截止 {esc(plan['deadline'])}")
        if plan.get("daily_minutes"):
            plan_meta.append(f"每天 {plan['daily_minutes']} 分钟")
        plan_html = (
            f'<p class="goal">{esc(plan["goal"])}</p>'
            + (f'<p class="meta">{"，".join(plan_meta)}</p>' if plan_meta else "")
            + (f'<p class="progress">当前进度：{esc(plan["progress_text"])}</p>' if plan.get("progress_text") else "")
            + '<table class="tasks"><thead><tr><th>日期</th><th>任务</th><th>类型</th><th>预计</th><th>状态</th></tr></thead>'
            + f"<tbody>{''.join(rows)}</tbody></table>"
        )
    else:
        plan_html = '<p class="empty">本次未选入计划。</p>'

    css = _CSS + _FORMATTER.get_style_defs(".hl")
    scope = "、".join(course_names) or "—"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{css}</style>
</head>
<body>
<main>
<header class="cover">
<h1>{esc(title)}</h1>
<p class="meta">导出于 {esc(exported_at.replace("T", " ")[:16])}，包含 {len(notes)} 篇笔记，学科：{esc(scope)}</p>
</header>
<nav class="toc"><h2>目录</h2><ol>{''.join(toc_items)}</ol>
<p class="toc-extra"><a href="#knowledge">知识点索引</a><a href="#plan">学习计划</a></p></nav>
{''.join(sections)}
<section id="knowledge" class="appendix"><h2>知识点索引</h2>{knowledge}</section>
<section id="plan" class="appendix"><h2>学习计划</h2>{plan_html}</section>
<footer>由 KnowFlow 导出。这是导出时刻的快照，之后在应用里的修改不会影响本文件。</footer>
</main>
</body>
</html>
"""


_CSS = """
:root { color-scheme: light; --ink:#2b2a27; --muted:#77736b; --rule:#e8e5df; --paper:#ffffff; --tint:#f6f5f2; --accent:#2e6b5a; }
* { box-sizing: border-box; }
body { margin:0; background:var(--tint); color:var(--ink);
  font: 16px/1.75 -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Helvetica Neue", sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 56px 32px 80px; background: var(--paper); min-height: 100vh; }
h1 { font-size: 30px; line-height: 1.3; margin: 0 0 8px; letter-spacing: -0.01em; }
h2 { font-size: 22px; line-height: 1.35; margin: 0 0 8px; }
h3 { font-size: 18px; margin: 28px 0 8px; } h4, h5, h6 { font-size: 16px; margin: 22px 0 6px; }
p { margin: 0 0 12px; }
a { color: var(--accent); text-decoration: none; } a:hover { text-decoration: underline; }
.meta { color: var(--muted); font-size: 14px; }
.cover { padding-bottom: 24px; border-bottom: 1px solid var(--rule); margin-bottom: 28px; }
.toc { margin-bottom: 40px; } .toc h2 { font-size: 15px; color: var(--muted); font-weight: 600; }
.toc ol { padding-left: 1.4em; margin: 0 0 8px; } .toc li { margin: 2px 0; }
.toc li span { color: var(--muted); font-size: 13px; margin-left: 10px; }
.toc-extra a { margin-right: 16px; font-size: 14px; }
.note { padding: 32px 0; border-top: 1px solid var(--rule); }
.note header { margin-bottom: 18px; }
.course { color: var(--accent); font-size: 13px; font-weight: 600; margin: 0 0 4px; }
.tag { display:inline-block; margin-left:8px; padding:0 8px; border-radius:999px; background:var(--tint); font-size:12px; color:var(--muted); }
.body blockquote { margin: 0 0 14px; padding: 8px 14px; border-left: 3px solid var(--accent); background: var(--tint); color: #45433e; }
.body ul, .body ol { padding-left: 1.4em; }
.body table, table.index, table.tasks { border-collapse: collapse; width: 100%; margin: 8px 0 16px; font-size: 14px; }
.body th, .body td, .index th, .index td, .tasks th, .tasks td { border: 1px solid var(--rule); padding: 6px 10px; text-align: left; vertical-align: top; }
.body th, .index th, .tasks th { background: var(--tint); font-weight: 600; }
.body code, code.tex { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 0.88em; background: var(--tint); padding: 1px 5px; border-radius: 4px; }
figure.code { position: relative; margin: 0 0 16px; }
figure.code .lang { position: absolute; top: 6px; right: 10px; font-size: 11px; color: var(--muted); }
.hl { background: #f7f6f3 !important; border-radius: 8px; overflow-x: auto; }
.hl pre { margin: 0; padding: 14px 16px; font: 13px/1.6 "SF Mono", Menlo, Consolas, monospace; }
math[display="block"] { display: block; margin: 12px 0; overflow-x: auto; }
.footnotes-sep { border: none; border-top: 1px solid var(--rule); margin: 24px 0 8px; }
.footnotes { font-size: 13px; color: var(--muted); } .footnote-backref { margin-left: 4px; }
.appendix { padding-top: 32px; border-top: 1px solid var(--rule); margin-top: 8px; }
.goal { font-size: 17px; font-weight: 600; margin-bottom: 4px; }
.empty { color: var(--muted); }
footer { margin-top: 48px; color: var(--muted); font-size: 13px; }
@media print {
  body { background: #fff; } main { padding: 0; max-width: none; }
  .note { break-before: page; } a { color: inherit; }
}
"""
