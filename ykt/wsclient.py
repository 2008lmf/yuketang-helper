"""课堂 WebSocket：握手、事件分发、断线重连。

握手帧：

    {"op":"hello","userid":"<uid>","role":"student","auth":"<lessonToken>","lessonid":"<lessonId>"}

`lessonToken` 来自 `POST /api/v3/lesson/checkin` 的响应体，`set-auth` 响应头则是同一课堂的 Bearer。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

import websocket

watch_op = Callable[[str, dict[str, Any]], None]


def ws_url(base_url: str) -> str:
    host = base_url.split("://", 1)[-1].rstrip("/")
    return f"wss://{host}/wsapp/"


class LessonSocket:
    def __init__(
        self,
        base_url: str,
        uid: str,
        lesson_id: str,
        lesson_token: str,
        on_event: watch_op,
        *,
        logger: logging.Logger | None = None,
        max_retries: int = 5,
    ) -> None:
        self.url = ws_url(base_url)
        self.uid = str(uid)
        self.lesson_id = str(lesson_id)
        self.lesson_token = lesson_token
        self.on_event = on_event
        self.log = logger or logging.getLogger("ykt.ws")
        self.max_retries = max_retries
        self._stopped = threading.Event()
        self._app: websocket.WebSocketApp | None = None

    def stop(self) -> None:
        self._stopped.set()
        if self._app is not None:
            try:
                self._app.close()
            except Exception:  # 关闭时的异常无需上报
                pass

    # ---------- 回调 ----------

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        hello = {
            "op": "hello",
            "userid": self.uid,
            "role": "student",
            "auth": self.lesson_token,
            "lessonid": self.lesson_id,
        }
        ws.send(json.dumps(hello, ensure_ascii=False))
        self.log.info("WS 已连接 %s，已发送 hello（lesson=%s）", self.url, self.lesson_id)

    def _on_message(self, _ws: websocket.WebSocketApp, message: str) -> None:
        try:
            payload = json.loads(message)
        except ValueError:
            self.log.debug("非 JSON 帧：%s", message)
            return
        op = str(payload.get("op") or "")
        if op == "hello" and payload.get("message") == "lesson finished":
            self.log.info("课堂已结束")
            self._stopped.set()
            self.on_event("lessonfinished", {"event": {"code": "LESSON_FINISH"}})
            return
        self.log.debug("RECV %s", payload)
        try:
            self.on_event(op, payload)
        except Exception as exc:  # 单个事件处理失败不能拖垮连接
            self.log.exception("处理 %s 事件失败：%s", op, exc)

    def _on_error(self, _ws: websocket.WebSocketApp, error: Exception) -> None:
        self.log.warning("WS 错误：%s", error)

    # ---------- 主循环 ----------

    def run_forever(self) -> None:
        retries = 0
        while not self._stopped.is_set():
            self._app = websocket.WebSocketApp(
                self.url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
            )
            try:
                self._app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                self.log.warning("WS 连接异常：%s", exc)
            if self._stopped.is_set():
                break
            retries += 1
            if retries > self.max_retries:
                self.log.error("WS 连续失败 %s 次，放弃本堂课", retries - 1)
                break
            backoff = min(2 ** retries, 30)
            self.log.info("%s 秒后重连 WS（第 %s 次）", backoff, retries)
            time.sleep(backoff)

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.run_forever, name=f"ykt-ws-{self.lesson_id}", daemon=True)
        thread.start()
        return thread
