"""HTTP 层：雨课堂 App 请求头、Cookie 持久化、Bearer 令牌。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

from .config import cookie_path, read_json, write_json

# 与移动端保持一致的请求头。部分接口（如 lesson/checkin）会校验 xtbz / x-client / x-csrftoken。
APP_HEADERS: dict[str, str] = {
    "user-agent": "Android",
    "brand": "google Pixel 9 Pro",
    "uuid": "",
    "buildnumber": "1610",
    "xtua": "client=app&tag=1.3.3&platform=Android",
    "systemversion": "16",
    "incremental": "14624737",
    "accept": "application/json",
    "isphysicaldevice": "true",
    "xtbz": "ykt",
    "x-client": "app",
}

DEFAULT_UA_FALLBACK = "Mozilla/5.0 (Linux; Android 16; Pixel 9 Pro)"


class YktError(RuntimeError):
    """雨课堂接口返回了非预期结果。"""

    def __init__(self, message: str, *, code: Any = None, path: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.path = path


class RCHttp:
    """一个站点 + 一份 Cookie 的会话。"""

    def __init__(self, base_url: str, server: str = "yuketang", timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.server = server
        self.timeout = timeout
        self.bearer: str | None = None       # checkin 下发的 set-auth
        self.lesson_token: str | None = None # WS 握手用的 lessonToken
        self._uid: str = ""
        self.session = requests.Session()
        self.session.headers.update(APP_HEADERS)
        self._load_cookies()

    # ---------- Cookie 持久化 ----------

    @property
    def _cookie_file(self) -> Path:
        return cookie_path(self.server)

    def _load_cookies(self) -> None:
        saved = read_json(self._cookie_file, {})
        if isinstance(saved, dict):
            for name, value in saved.items():
                if isinstance(name, str) and isinstance(value, str):
                    self.session.cookies.set(name, value)

    def save_cookies(self) -> Path:
        data = {c.name: c.value for c in self.session.cookies}
        path = self._cookie_file
        write_json(path, data)
        return path

    def clear_cookies(self) -> None:
        self.session.cookies.clear()
        write_json(self._cookie_file, {})

    # ---------- 身份 ----------

    @property
    def csrf_token(self) -> str:
        return self.session.cookies.get("csrftoken") or ""

    @property
    def uid(self) -> str:
        """当前登录用户 id 不一定在 Cookie 里，由登录流程写入配置后回填。"""
        return self._uid

    @uid.setter
    def uid(self, value: str) -> None:
        self._uid = str(value or "")

    def has_session(self) -> bool:
        return bool(self.session.cookies.get("sessionid"))

    def auth_headers(self) -> dict[str, str]:
        headers = {}
        csrf = self.csrf_token
        if csrf:
            headers["x-csrftoken"] = csrf
            headers["sessionid"] = self.session.cookies.get("sessionid") or ""
        if self._uid:
            headers["x-uid"] = self._uid
        return headers

    def bearer_headers(self) -> dict[str, str]:
        if not self.bearer:
            raise YktError("缺少 Bearer 令牌，请先在课堂内执行 `ykt checkin --lesson <id>`")
        return {"authorization": f"Bearer {self.bearer}"}

    # ---------- 请求 ----------

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        retries: int = 2,
    ) -> requests.Response:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        merged = {**self.auth_headers(), **(headers or {})}
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return self.session.request(
                    method.upper(),
                    url,
                    json=json_body,
                    params=params,
                    headers=merged,
                    timeout=timeout or self.timeout,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:  # 网络抖动重试一次
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(1.0 + attempt)
        raise YktError(f"请求失败：{url}（{last_error}）", path=path)

    def json(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        retries: int = 2,
    ) -> dict[str, Any]:
        resp = self.request(
            method,
            path,
            json_body=json_body,
            params=params,
            headers=headers,
            timeout=timeout,
            retries=retries,
        )
        if resp.status_code == 401:
            raise YktError("未登录或登录已过期，请重新执行 `ykt login`", path=path)
        if resp.status_code >= 400:
            raise YktError(f"HTTP {resp.status_code}：{path}", code=resp.status_code, path=path)
        try:
            data = resp.json()
        except ValueError:
            raise YktError(f"响应不是 JSON：{path}", path=path) from None
        if isinstance(data, dict) and data.get("success") is False:
            raise YktError(str(data.get("msg") or data.get("message") or "接口返回失败"), path=path)
        return data
