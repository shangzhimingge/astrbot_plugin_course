"""Strict validation for personal class-period tables."""
from __future__ import annotations

import json
import re
from datetime import time

from .course_types import MAX_PERIOD
from .importer import ImportProblem

MAX_INPUT = 50_000
_CLOCK = r"[0-9]{1,2}:[0-9]{2}"
_TEXT_ROW = re.compile(
    rf"\s*(?:第\s*)?(?P<period>[0-9]{{1,2}})(?:\s*节\s*|\s+)"
    rf"[:：]?\s*(?P<start>{_CLOCK})\s*[-–—~～至]\s*(?P<end>{_CLOCK})\s*"
)


def is_app_timetable(value: object) -> bool:
    """Return whether a decoded JSON object uses the supported timetable export envelope."""
    return isinstance(value, dict) and value.get("type") == "timetable"


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _input_rows(source: str, *, json_only: bool):
    source = source.strip().lstrip("\ufeff").strip()
    fenced = re.fullmatch(r"\x60\x60\x60(?:json)?\s*([\s\S]+?)\s*\x60\x60\x60", source, re.I)
    if fenced:
        source = fenced[1].strip()
        json_only = True
    if json_only or source.startswith(("{", "[")):
        try:
            return json.loads(source, object_pairs_hook=_object_pairs)
        except (ValueError, RecursionError) as exc:
            raise ImportProblem("作息 JSON 格式无效或包含重复键。") from exc
    rows = []
    for line in source.splitlines():
        if not line.strip():
            continue
        match = _TEXT_ROW.fullmatch(line)
        if not match:
            raise ImportProblem("作息文本格式无效，请使用「第1节 08:00-08:45」。")
        rows.append(match.groupdict())
        if len(rows) > MAX_PERIOD:
            raise ImportProblem("作息节次数量过多。")
    return rows


def _rows_from_app_timetable(value: dict) -> list[dict]:
    """Convert the timetable export's clock fields to the canonical row shape."""
    data = value.get("data")
    if not isinstance(data, dict):
        raise ImportProblem("作息 JSON 缺少有效的 data 对象。")
    rows = data.get("items")
    special = data.get("specialPeriods")
    if special is not None and special != []:
        raise ImportProblem("当前仅支持常规作息节次；请先移除特别节次后导出。")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_PERIOD:
        raise ImportProblem("作息 JSON 的 items 数量无效。")

    converted = []
    for row in rows:
        if not isinstance(row, dict):
            raise ImportProblem("作息 JSON 的 items 项格式无效。")
        values = (row.get("section"), row.get("startHour"), row.get("startMinute"),
                  row.get("endHour"), row.get("endMinute"))
        if any(type(item) is not int for item in values):
            raise ImportProblem("作息 JSON 的节次和时刻必须为整数。")
        section, start_hour, start_minute, end_hour, end_minute = values
        if not 0 <= start_hour <= 23 or not 0 <= end_hour <= 23:
            raise ImportProblem("作息 JSON 含有无效小时。")
        if not 0 <= start_minute <= 59 or not 0 <= end_minute <= 59:
            raise ImportProblem("作息 JSON 含有无效分钟。")
        converted.append({
            "period": section,
            "start": f"{start_hour:02d}:{start_minute:02d}",
            "end": f"{end_hour:02d}:{end_minute:02d}",
        })
    return converted


def parse_period_times(
    source: str | bytes | dict | list, *, json_only: bool = False,
) -> dict[int, tuple[str, str]]:
    if isinstance(source, (str, bytes)):
        if len(source) > MAX_INPUT:
            raise ImportProblem("作息内容过长。")
        if isinstance(source, bytes):
            try:
                source = source.decode("utf-8-sig")
            except UnicodeError as exc:
                raise ImportProblem("作息文件请使用 UTF-8 编码。") from exc
        value = _input_rows(source, json_only=json_only)
    else:
        value = source
    if is_app_timetable(value):
        value = _rows_from_app_timetable(value)
    if isinstance(value, dict) and set(value) == {"periods"}:
        value = value["periods"]

    if not isinstance(value, (dict, list)) or not 1 <= len(value) <= MAX_PERIOD:
        raise ImportProblem("未识别到有效作息，或节次数量过多。")

    if isinstance(value, dict):
        rows = []
        for period, pair in value.items():
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise ImportProblem("作息 JSON 需为节次数字键对应 [开始,结束]。")
            rows.append({"period": period, "start": pair[0], "end": pair[1]})
    else:
        rows = value

    result = {}
    for row in rows:
        if not isinstance(row, dict) or not re.fullmatch(r"[0-9]{1,2}", str(row.get("period", ""))):
            raise ImportProblem("作息节次格式无效。")
        period = int(row["period"])
        if not 1 <= period <= MAX_PERIOD or period in result:
            raise ImportProblem(f"节次须为 1–{MAX_PERIOD} 内的不重复整数。")
        start_text, end_text = row.get("start"), row.get("end")
        if not all(isinstance(text, str) and re.fullmatch(_CLOCK, text) for text in (start_text, end_text)):
            raise ImportProblem("作息时间需包含 HH:MM 格式的开始和结束时间。")
        try:
            start, end = (time.fromisoformat(text.zfill(5)) for text in (start_text, end_text))
            if start >= end:
                raise ValueError
        except ValueError as exc:
            raise ImportProblem("作息时间无效或结束时间不晚于开始时间。") from exc
        result[period] = (start.strftime("%H:%M"), end.strftime("%H:%M"))

    ordered = sorted(result)
    if any(result[a][1] > result[b][0] for a, b in zip(ordered, ordered[1:])):
        raise ImportProblem("相邻节次的时间不可倒序或重叠。")
    return dict(sorted(result.items()))
