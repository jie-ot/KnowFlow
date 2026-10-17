"""文本小工具：字数统计、token 估算、摘要片段。"""

from __future__ import annotations

import re

_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿　-〿＀-￯]")
_MD_NOISE = re.compile(r"(```[\s\S]*?```)|(\$\$[\s\S]*?\$\$)|[#>*_`~\[\]()|-]")


def char_count(text: str) -> int:
    """字数：去掉空白后的字符数。"""
    return len(re.sub(r"\s", "", text or ""))


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文约 0.6 token/字，其他字符约 0.3 token/字符。

    依据 DeepSeek 文档给出的近似值，只用于提示，不用于计费。
    """
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return int(cjk * 0.6 + other * 0.3) + 1


def plain_excerpt(markdown: str, limit: int = 120) -> str:
    """把 Markdown 粗略转为纯文本，用于列表摘要。"""
    text = re.sub(r"```[\s\S]*?```", " ", markdown or "")
    text = re.sub(r"\$\$[\s\S]*?\$\$", " 公式 ", text)
    text = re.sub(r"^\s*\|?\s*:?-{3,}.*$", " ", text, flags=re.M)
    text = re.sub(r"\$([^$\n]+)\$", r"\1", text)
    text = re.sub(r"\[\^\d+\]:.*", " ", text)
    text = re.sub(r"\[\^\d+\]", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[\[([^\[\]\n|]+)(?:\|([^\[\]\n]*))?\]\]", lambda m: (m.group(2) or m.group(1)).strip(), text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s*", "", text, flags=re.M)
    text = text.replace("摘要：", "", 1) if text.startswith("> 摘要：") or text.lstrip("> ").startswith("摘要：") else text
    text = re.sub(r"[*_`~|]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def normalize_extracted(text: str) -> str:
    """清理从文件中提取的文字：统一换行、去掉行尾空白、压缩多余空行。"""
    text = (text or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
