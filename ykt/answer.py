"""课堂答题：从课件题目 / 题库里算出提交内容。

雨课堂下发的 `problem` 结构（见 docs/PROTOCOL.md）：

    {
      "problemId": "…", "problemType": 1, "body": "题干", "score": 2,
      "options": [{"key": "A", "value": "…"}],
      "blanks":  [{"num": 1, "answers": ["…"]}],
      "answers": []   # 部分题型会带参考答案
    }

`problemType`: 1 单选 / 2 多选 / 3 投票 / 4 填空 / 5 主观 / 6 判断。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PROBLEM_TYPES = {1: "单选题", 2: "多选题", 3: "投票题", 4: "填空题", 5: "主观题", 6: "判断题"}

_PUNCT = re.compile(r"[\s\u3000，。、；：？！,.;:?!\"'“”‘’（）()\[\]【】<>《》]+")


def type_name(problem_type: int) -> str:
    return PROBLEM_TYPES.get(int(problem_type), f"未知({problem_type})")


def normalize(text: Any) -> str:
    """题干归一到可比较的形态：去空白与标点、统一小写。"""
    return _PUNCT.sub("", str(text or "")).lower()


class QuestionBank:
    """极简题库：{题干: [答案…]}，题干比较前会归一化。"""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._items: dict[str, list[str]] = {}
        for key, value in (data or {}).items():
            answers = [value] if isinstance(value, str) else list(value or [])
            self._items[normalize(key)] = [str(a) for a in answers]

    def __len__(self) -> int:
        return len(self._items)

    @classmethod
    def load(cls, path: Path | None) -> "QuestionBank":
        if path is None:
            return cls()
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls(raw if isinstance(raw, dict) else {})

    def lookup(self, body: str) -> list[str] | None:
        key = normalize(body)
        if not key:
            return None
        if key in self._items:
            return self._items[key]
        # 题干带前后缀（如「【第3题】…」）时做一次包含匹配
        for question, answers in self._items.items():
            if len(question) >= 6 and (question in key or key in question):
                return answers
        return None


def option_keys(problem: dict[str, Any]) -> list[str]:
    return [str((o or {}).get("key") or "").strip() for o in (problem.get("options") or []) if (o or {}).get("key")]


def _as_keys(values: list[str], problem: dict[str, Any]) -> list[str]:
    """把「A」「选项文本」「1」统一成选项 key。"""
    keys = option_keys(problem)
    upper = {k.upper(): k for k in keys}
    text_map = {normalize((o or {}).get("value")): str((o or {}).get("key")) for o in (problem.get("options") or [])}
    out: list[str] = []
    for value in values:
        raw = str(value).strip()
        if raw.upper() in upper:
            out.append(upper[raw.upper()])
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(keys):
            out.append(keys[int(raw) - 1])
            continue
        hit = text_map.get(normalize(raw))
        if hit:
            out.append(hit)
    return out


def build_result(problem: dict[str, Any], cfg: dict[str, Any], bank: QuestionBank) -> tuple[Any, str]:
    """算出该题的 `result` 提交体，并返回一句人类可读的来源说明。"""
    ptype = int(problem.get("problemType") or 0)
    body = problem.get("body") or ""
    known = [str(a) for a in (problem.get("answers") or [])]
    banked = bank.lookup(body) or []

    if ptype == 5:  # 主观题
        text = (banked[0] if banked else "") or cfg.get("short_answer_text") or ""
        return {"content": text, "pics": [], "videos": []}, "主观题·文本"

    if ptype == 4:  # 填空题：按空提交
        blanks = problem.get("blanks") or []
        results: list[str] = []
        source = "课件答案"
        for index, blank in enumerate(blanks):
            answers = [str(a) for a in ((blank or {}).get("answers") or [])]
            if answers:
                results.append(answers[0])
            elif index < len(banked):
                results.append(banked[index])
                source = "题库"
            elif known and index < len(known):
                results.append(known[index])
            else:
                results.append(str(cfg.get("fill_blank_empty") or ""))
                source = "占位"
        if not blanks:
            results = banked or known or [str(cfg.get("fill_blank_empty") or "")]
            source = "题库" if banked else source
        return results, f"填空题·{source}（{len(results)} 空）"

    keys = option_keys(problem)
    candidates = banked or known
    if candidates:
        mapped = _as_keys(candidates, problem)
        if mapped:
            return mapped, "题库/参考答案"
    # 兜底策略
    if ptype == 2:
        if str(cfg.get("fallback_multiple") or "all").lower() == "first":
            keys = keys[:1]
        return keys, "多选·兜底"
    fallback = str(cfg.get("fallback_single") or "A").strip()
    mapped = _as_keys([fallback], problem)
    if mapped:
        return mapped[:1], "单选·兜底"
    return keys[:1], "单选·兜底(取首项)"


def submit_delay(limit: int | float | None, cfg: dict[str, Any]) -> float:
    """限时题的提交延迟：按比例等一会儿，避免 0.1 秒交卷。"""
    try:
        seconds = float(limit or 0)
    except (TypeError, ValueError):
        return 0.0
    if seconds <= 0:
        return 0.0
    ratio = float(cfg.get("delay_ratio") or 0)
    cap = float(cfg.get("delay_max") or 0)
    delay = seconds * max(0.0, min(ratio, 1.0))
    if cap > 0:
        delay = min(delay, cap)
    return max(0.0, delay)


def find_problem(problems: list[dict[str, Any]], problem_id: str) -> dict[str, Any] | None:
    for problem in problems:
        if str(problem.get("problemId") or "") == str(problem_id):
            return problem
    return None


def problems_from_presentation(pres: dict[str, Any]) -> list[dict[str, Any]]:
    """把课件里所有带题目的 slide 拉平成 problem 列表。"""
    out: list[dict[str, Any]] = []
    for slide in pres.get("slides") or []:
        problem = (slide or {}).get("problem")
        if isinstance(problem, dict) and problem.get("problemId"):
            out.append(problem)
    return out
