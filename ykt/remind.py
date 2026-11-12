"""上课提醒：课表 + 下一节时间计算（纯函数，便于离线测试）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def parse_hhmm(value: str) -> tuple[int, int]:
    hour, _, minute = str(value).strip().partition(":")
    h, m = int(hour), int(minute or 0)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"时间格式不对：{value}（应为 HH:MM）")
    return h, m


def next_occurrence(entry: dict[str, Any], now: datetime) -> datetime:
    """这条课表在 now 之后（含今天未到点）的下一次开始时间。"""
    weekday = int(entry.get("weekday") or 1)
    if not 1 <= weekday <= 7:
        raise ValueError(f"weekday 应为 1-7，得到 {weekday}")
    hour, minute = parse_hhmm(entry.get("time") or "08:00")
    days_ahead = (weekday - now.isoweekday()) % 7
    candidate = (now + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def remind_at(entry: dict[str, Any], now: datetime, default_before: int = 10) -> datetime:
    before = entry.get("before_minutes")
    before = int(default_before if before is None else before)
    return next_occurrence(entry, now) - timedelta(minutes=before)


def upcoming(entries: Iterable[dict[str, Any]], now: datetime) -> list[tuple[datetime, dict[str, Any]]]:
    """按开始时间排序的（开始时间, 课表项）列表。"""
    rows = [(next_occurrence(e, now), e) for e in entries]
    rows.sort(key=lambda pair: pair[0])
    return rows


def describe(entry: dict[str, Any]) -> str:
    weekday = WEEKDAYS[max(0, min(6, int(entry.get("weekday") or 1) - 1))]
    name = entry.get("name") or "未命名课程"
    room = entry.get("room") or ""
    return f"{weekday} {entry.get('time') or '--:--'} {name}" + (f" @ {room}" if room else "")
