"""配置与本地状态：单一目录存放 config.json / cookies / 题库。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_NAME = "yuketang-helper"

# 雨课堂各站点，--server 可直接用这些名字
SERVERS = {
    "yuketang": "https://www.yuketang.cn",
    "pro": "https://pro.yuketang.cn",
    "changjiang": "https://changjiang.yuketang.cn",
    "huanghe": "https://huanghe.yuketang.cn",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "server": "yuketang",
    "uid": "",
    "user": {"name": "", "school": ""},
    # 进课堂 / 签到
    "lesson": {
        "auto_join": True,          # 发现本班正在上课时自动进入
        "watch_courses": [],        # 只关注这些 course_id；空表示全部
        "poll_interval": 30,        # 轮询 on-lesson 的间隔（秒）
    },
    # 位置签到
    "location": {
        "use_server_center": True,  # 用 eligibility 返回的课堂中心点
        "fallback": {"lat": 0.0, "lng": 0.0, "address": ""},
    },
    # 课堂答题
    "answer": {
        "enabled": True,
        "delay_ratio": 0.6,         # 在限时的 60% 处提交，避免秒答
        "delay_max": 60,            # 最多等这么多秒
        "fallback_single": "A",     # 单选题没有答案来源时提交的选项
        "fallback_multiple": "all", # all / first
        "fill_blank_empty": "",     # 填空题拿不到答案时提交的内容
        "short_answer_text": "老师好，这道题我暂时没有思路。",
    },
    # 上课提醒
    "remind": {
        "before_minutes": 10,
        "webhook": "",              # 可选：POST {"text": "..."} 的地址
        "timetable": [],            # 见 `ykt remind add`
    },
}


def state_dir() -> Path:
    """配置目录：$YKT_HOME > %APPDATA%\\yuketang-helper > ~/.config/yuketang-helper。"""
    override = os.environ.get("YKT_HOME")
    if override:
        path = Path(override).expanduser()
    elif os.name == "nt":
        path = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
    else:
        path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def config_path() -> Path:
    return state_dir() / "config.json"


def load_config() -> dict:
    return _merge(DEFAULT_CONFIG, read_json(config_path(), {}))


def save_config(cfg: dict) -> Path:
    path = config_path()
    write_json(path, cfg)
    return path


def cookie_path(server: str) -> Path:
    return state_dir() / f"cookies.{server}.json"


def bank_path() -> Path:
    return state_dir() / "bank.json"


def resolve_server(name_or_url: str) -> str:
    """把 --server 参数归一化成 base_url。"""
    if not name_or_url:
        return SERVERS["yuketang"]
    key = name_or_url.strip().lower()
    if key in SERVERS:
        return SERVERS[key]
    if key.startswith("http://") or key.startswith("https://"):
        return key.rstrip("/")
    raise ValueError(f"未知的雨课堂站点：{name_or_url}（可选 {', '.join(SERVERS)}）")
