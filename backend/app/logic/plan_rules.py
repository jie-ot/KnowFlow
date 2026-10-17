"""学习计划的确定性校验（同学文档 BR-05、Q-P）。

- 任务日期不得早于今天（按凌晨 4 点分界的“今天”），不得晚于计划截止日；
- 设了每日可用时间时，同一天任务的预计分钟合计不得超过它；
  有日期的任务必须填写预计分钟；
- 违反任意一条都不能采纳，必须先调整；AI 不能只加一句提示就突破约束。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .timeutil import parse_day

TASK_KINDS = {"learn": "学习", "review": "复习", "practice": "练习", "read": "阅读"}
TASK_STATUSES = {"todo": "待办", "in_progress": "进行中", "done": "已完成"}


def validate_tasks(
    tasks: list[dict[str, Any]],
    *,
    today: str,
    deadline: str | None,
    daily_minutes: int | None,
    existing: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """返回违规列表，每项为 {index, field, message}；index 为 None 表示整体问题。"""
    problems: list[dict[str, Any]] = []
    per_day: dict[str, int] = defaultdict(int)
    for task in existing or []:
        if task.get("status") != "done" and task.get("due_date") and task.get("estimated_minutes"):
            per_day[task["due_date"]] += int(task["estimated_minutes"])

    for index, task in enumerate(tasks):
        title = (task.get("title") or "").strip()
        if not (1 <= len(title) <= 200):
            problems.append({"index": index, "field": "title", "message": "任务名称需要 1–200 个字"})
        if task.get("kind") not in TASK_KINDS:
            problems.append({"index": index, "field": "kind", "message": "任务类型只能是学习、复习、练习或阅读"})
        minutes = task.get("estimated_minutes")
        if minutes is not None and (not isinstance(minutes, int) or not (1 <= minutes <= 1440)):
            problems.append({"index": index, "field": "estimated_minutes", "message": "预计时长需要是 1–1440 分钟的整数"})
            minutes = None
        due = task.get("due_date")
        if due:
            day = parse_day(due)
            if day is None:
                problems.append({"index": index, "field": "due_date", "message": "日期格式应为 YYYY-MM-DD"})
                continue
            if due < today:
                problems.append({"index": index, "field": "due_date", "message": f"日期 {due} 早于今天"})
            if deadline and due > deadline:
                problems.append({"index": index, "field": "due_date", "message": f"日期 {due} 晚于截止日 {deadline}"})
            if daily_minutes:
                if minutes is None:
                    problems.append({"index": index, "field": "estimated_minutes", "message": "有日期的任务需要填写预计时长"})
                else:
                    per_day[due] += minutes

    if daily_minutes:
        for day, total in sorted(per_day.items()):
            if total > daily_minutes:
                problems.append({
                    "index": None, "field": "due_date",
                    "message": f"{day} 的任务合计 {total} 分钟，超过每天 {daily_minutes} 分钟的预算",
                    "day": day,
                })
    return problems
