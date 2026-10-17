"""SM-2 复习调度（需求文档 3.5 节，设计文档 DD-07）。

- 三档自评映射为 q：忘记=1，模糊=3，记得=5。
- q < 3：已复习次数 n 归零，下次间隔 1 天，EF 不变。
- q >= 3：EF' = max(1.3, EF + 0.1 - (5-q)(0.08 + 0.02(5-q)))，n 加 1；
  I(1)=1，I(2)=6，I(n)=round(I(n-1) × EF')，四舍五入 0.5 进位。
- EF 以“百分位整数”存储和计算（2.5 存为 250），避免浮点误差：
  25 × 2.3 在浮点下是 57.4999…，按规则应得 58。
- 与原始 SM-2 的差异：不实现“当天把 q < 4 的卡片再复习一遍”。
"""

from __future__ import annotations

from dataclasses import dataclass

GRADE_Q = {"forgot": 1, "hard": 3, "good": 5}
GRADE_LABEL = {"forgot": "忘记", "hard": "模糊", "good": "记得"}
MIN_EF = 130
NEW_CARD_EF = 250


@dataclass(frozen=True)
class SM2State:
    n: int
    interval: int
    ef: int  # 百分位整数


def schedule(state: SM2State, grade: str) -> SM2State:
    """纯函数：根据当前状态和自评返回新状态。"""
    if grade not in GRADE_Q:
        raise ValueError(f"unknown grade: {grade}")
    q = GRADE_Q[grade]
    if q < 3:
        return SM2State(n=0, interval=1, ef=state.ef)
    d = 5 - q
    ef = max(MIN_EF, state.ef + 10 - d * (8 + 2 * d))
    n = state.n + 1
    if n == 1:
        interval = 1
    elif n == 2:
        interval = 6
    else:
        interval = max(1, (state.interval * ef + 50) // 100)
    return SM2State(n=n, interval=interval, ef=ef)


def preview(state: SM2State) -> dict[str, int]:
    """三个自评按钮各自对应的下次间隔（天），用于复习页按钮提示。"""
    return {grade: schedule(state, grade).interval for grade in GRADE_Q}


def ef_to_float(ef: int) -> float:
    return round(ef / 100, 2)
