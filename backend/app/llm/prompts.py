"""各类 AI 任务的提示词。

系统提示里写明规则（例如整理时不得增删事实），依据我的需求 FR-09/10/13
和同学需求 DC-04（只能引用给定片段、不确定时提出问题）。
"""

from __future__ import annotations

ORGANIZE_SYSTEM = (
    "你是学习笔记整理助手。把用户提供的笔记整理成结构清晰的 Markdown：\n"
    "1. 用二级、三级标题划分层次，用列表和表格归纳并列内容；\n"
    "2. 原文中的所有事实、数字、公式（$...$ 与 $$...$$）和代码块必须原样保留；\n"
    "3. 不得增加原文没有的事实，也不得删除原文内容；可以修正明显的错别字和格式问题；\n"
    "4. 只输出整理后的 Markdown 正文，不要输出笔记标题、解释或前言。"
)


def organize_user(title: str, content: str) -> str:
    return f"笔记标题：{title}\n\n笔记内容：\n{content}"


def summarize_system(length: int) -> str:
    return (
        f"你是学习笔记摘要助手。用约 {length} 个汉字概括笔记的核心内容（允许上下浮动 20%）。\n"
        "要求：只依据原文，不得编造原文没有的数字或结论；"
        "输出一段连贯的中文纯文本，不要标题、列表、引号或前言。"
    )


def extract_system(count: int) -> str:
    return (
        f"你是复习卡片助手。从笔记中提炼 {count} 个最值得记住的知识点，做成复习卡片。\n"
        "每张卡片包含：\n"
        "- front：知识点名称或回忆提示，用一个简短的问题或关键词表达，不超过 60 字；\n"
        "- back：要点内容，准确、简洁，可用 Markdown 列表和 LaTeX 公式，不超过 400 字。\n"
        "只依据原文；不要出选择题或练习题。\n"
        '以 JSON 对象返回，格式为 {"items": [{"front": "...", "back": "..."}]}，不要输出其他文字。'
    )


COMPOSE_SYSTEM = (
    "你是学习资料整理助手。根据给定的资料片段，整理成一篇结构清晰的中文学习笔记。\n"
    "规则：\n"
    "1. 每个片段以 [S编号] 开头，并注明来源文件和位置；\n"
    "2. 每一节都要在 source_ids 中列出它依据的片段编号，只能使用给定的编号；\n"
    "3. 不得编造资料中没有的内容；需要补充解释时单独成节，evidence 设为 ai_supplement，source_ids 可为空；\n"
    "4. body 使用 Markdown，可包含列表、表格、LaTeX 公式和代码块，不要在 body 里重复小节标题。\n"
    '以 JSON 对象返回：{"title": "笔记标题", "sections": [{"heading": "小节标题", '
    '"body": "Markdown 正文", "evidence": "source_based 或 ai_supplement", "source_ids": ["S1"]}]}，'
    "不要输出其他文字。"
)


def compose_user(instruction: str, blocks: list[str]) -> str:
    head = f"整理要求：{instruction}\n\n" if instruction.strip() else ""
    return head + "资料片段：\n\n" + "\n\n".join(blocks)


def plan_system(today: str, deadline: str | None, daily_minutes: int | None) -> str:
    rules = [
        f"今天是 {today}。",
        "任务类型 kind 只能是 learn（学习）、review（复习）、practice（练习）、read（阅读）。",
        "每个任务要具体、可执行，标题不超过 40 字。",
    ]
    if deadline or daily_minutes:
        rules.append("due_date 使用 YYYY-MM-DD 格式，不得早于今天。")
        if deadline:
            rules.append(f"due_date 不得晚于截止日 {deadline}。")
        if daily_minutes:
            rules.append(
                f"每个有日期的任务都要给出 estimated_minutes（正整数），"
                f"同一天所有任务的 estimated_minutes 合计不得超过 {daily_minutes}。"
            )
    else:
        rules.append("用户没有给出截止日和每日时间，所有任务的 due_date 必须为 null，只按先后顺序排列。")
    rules.append("note_ids 只能使用给定的 N 编号，没有相关笔记时为空数组。")
    rules.append(
        "如果目标含糊或范围不清，不要硬排计划，返回 result_type 为 needs_clarification，"
        "并在 questions 中列出需要用户回答的问题。"
    )
    return (
        "你是学习计划助手，根据用户的目标、当前进度和时间约束生成任务草稿，由用户确认后才生效。\n"
        + "\n".join(f"- {r}" for r in rules)
        + '\n以 JSON 对象返回：{"result_type": "draft 或 needs_clarification", "questions": [], '
        '"assumptions": [], "tasks": [{"title": "...", "kind": "learn", "due_date": "YYYY-MM-DD 或 null", '
        '"estimated_minutes": 30, "note_ids": ["N1"]}]}，不要输出其他文字。'
    )


def plan_user(goal: str, progress: str, scope_lines: list[str], existing: list[str]) -> str:
    parts = [f"学习目标：{goal}", f"当前进度：{progress or '（未填写）'}"]
    parts.append("相关笔记：\n" + ("\n".join(scope_lines) if scope_lines else "（未选择）"))
    if existing:
        parts.append("计划里已有的未完成任务（不要重复）：\n" + "\n".join(existing))
    return "\n\n".join(parts)
