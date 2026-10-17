"""时间工具。“一天”以本地凌晨 4 点为分界（D5）：凌晨 1:30 复习算前一天。"""

from __future__ import annotations

from datetime import date, datetime, timedelta


def now_local() -> datetime:
    return datetime.now().astimezone()


def logical_day(moment: datetime | None = None, day_start_hour: int = 4) -> date:
    moment = moment or now_local()
    return (moment - timedelta(hours=day_start_hour)).date()


def today_str(day_start_hour: int = 4) -> str:
    return logical_day(None, day_start_hour).isoformat()


def add_days(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
