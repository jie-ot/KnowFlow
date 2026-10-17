"""AI 产出的事实校验（设计文档 EX-10）。

- 整理（保留模式）：原文中的数字、公式、代码块必须全部出现在结果里。
- 总结（子集模式）：摘要中出现的数字必须在原文中；字数在目标的 ±20% 内。

校验只产生提示，不阻止采纳；最终由用户判断。
"""

from __future__ import annotations

import re

from .text import char_count

CODE_RE = re.compile(r"```[^\n]*\n([\s\S]*?)```")
FORMULA_RE = re.compile(r"\$\$[\s\S]+?\$\$|\$[^$\n]+?\$")
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?%?")
LIST_MARKER_RE = re.compile(r"^\s*\d+(?:[.)]\s|、)", re.M)
HEADING_NUM_RE = re.compile(r"^\s*#{1,6}\s*\d+(?:\.\d+)*", re.M)


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _strip_code_and_formulas(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = FORMULA_RE.sub(" ", text)
    text = re.sub(r"`[^`\n]+`", " ", text)
    return text


def _numbers(text: str) -> set[str]:
    body = _strip_code_and_formulas(text)
    body = LIST_MARKER_RE.sub(" ", body)
    body = HEADING_NUM_RE.sub(" ", body)
    return set(NUMBER_RE.findall(body))


def _short(items: list[str], limit: int = 5) -> str:
    shown = "、".join(items[:limit])
    return shown + (f" 等 {len(items)} 处" if len(items) > limit else "")


def check_preserve(original: str, output: str) -> list[str]:
    warnings: list[str] = []
    out_squashed = _squash(output)

    missing_code = [
        block.strip().split("\n")[0][:30]
        for block in CODE_RE.findall(original)
        if block.strip() and _squash(block) not in out_squashed
    ]
    if missing_code:
        warnings.append(f"有 {len(missing_code)} 个代码块没有原样保留：{_short(missing_code)}")

    original_no_code = re.sub(r"```[\s\S]*?```", " ", original)
    missing_formulas = [
        f for f in dict.fromkeys(FORMULA_RE.findall(original_no_code))
        if _squash(f.strip("$")) not in out_squashed
    ]
    if missing_formulas:
        warnings.append(f"可能遗漏的公式：{_short(missing_formulas)}")

    missing_numbers = sorted(_numbers(original) - set(NUMBER_RE.findall(output)),
                             key=lambda s: (len(s), s))
    if missing_numbers:
        warnings.append(f"可能遗漏的数字：{_short(missing_numbers, 8)}")
    return warnings


def check_summary(original: str, summary: str, target: int) -> list[str]:
    warnings: list[str] = []
    new_numbers = sorted(_numbers(summary) - set(NUMBER_RE.findall(original)))
    if new_numbers:
        warnings.append(f"摘要里出现了原文没有的数字：{_short(new_numbers, 8)}，请核对")
    length = char_count(summary)
    low, high = int(target * 0.8), int(target * 1.2)
    if not (low <= length <= high):
        warnings.append(f"摘要共 {length} 字，不在目标 {target} 字 ±20%（{low}–{high} 字）范围内")
    return warnings
