"""雨课堂接口封装。路径与字段说明见 docs/PROTOCOL.md。"""

from __future__ import annotations

import time
from typing import Any

from .http import RCHttp, YktError


def _unwrap(data: dict[str, Any], path: str) -> Any:
    """统一处理 {code, data, msg} 形态。"""
    code = data.get("code", 0)
    if code in (0, None):
        return data.get("data")
    msg = data.get("msg") or data.get("message") or f"code={code}"
    raise YktError(f"{msg}（{path}）", code=code, path=path)


class YuketangAPI:
    def __init__(self, http: RCHttp, device_id: str = "") -> None:
        self.http = http
        self.device_id = device_id

    # ---------------- 登录 ----------------

    def pre_info(self) -> dict[str, Any]:
        """取扫码登录票据（qrContent / qrImage / token）。"""
        return _unwrap(self.http.json("GET", "/api/v3/user/login/pre-info"), "pre-info") or {}

    def login_by_qr(self, token: str, wait: float = 35.0) -> dict[str, Any]:
        """轮询扫码结果。服务端会挂起最长约 30s。"""
        data = self.http.json("POST", "/api/v3/user/login", json_body={"token": token}, timeout=wait)
        code = data.get("code")
        if code == 0:
            self.http.save_cookies()
            return data.get("data") or {}
        if code == 50001:
            raise YktError("二维码登录超时（未扫码或未确认）", code=code, path="login")
        raise YktError(str(data.get("msg") or "扫码登录失败"), code=code, path="login")

    def send_code(self, phone: str) -> dict[str, Any]:
        body = {"phoneNumber": phone, "email": "", "ticket": "", "rand": ""}
        return _unwrap(self.http.json("POST", "/api/v3/user/code/send", json_body=body), "code/send") or {}

    def verify_code(self, phone: str, code: str) -> dict[str, Any]:
        body = {"phoneNumber": phone, "email": "", "code": code}
        return _unwrap(self.http.json("POST", "/api/v3/user/code/verify", json_body=body), "code/verify") or {}

    def _login_app(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload.setdefault("pushDeviceId", self.device_id)
        data = self.http.json("POST", "/api/v3/user/login/app", json_body=payload)
        code = data.get("code")
        if code != 0:
            raise YktError(str(data.get("msg") or "登录失败"), code=code, path="login/app")
        self.http.save_cookies()
        return data.get("data") or {}

    def login_with_code(self, phone: str, code: str) -> dict[str, Any]:
        return self._login_app(
            {
                "type": 3,
                "phoneNumber": phone,
                "password": "",
                "email": "",
                "code": code,
                "ticket": "",
                "rand": "",
            }
        )

    def login_with_password(self, account: str, password: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": 2,
            "phoneNumber": "",
            "password": password,
            "email": "",
            "code": "",
            "ticket": "",
            "rand": "",
        }
        if "@" in account:
            payload["email"] = account
        else:
            payload["type"] = 1
            payload["phoneNumber"] = account
        return self._login_app(payload)

    def user_info(self) -> dict[str, Any]:
        data = self.http.json("GET", "/v/course_meta/user_info")
        profile = ((data.get("data") or {}).get("user_profile")) or {}
        if not profile:
            raise YktError("取不到用户资料，Cookie 可能已失效", path="user_info")
        return {
            "uid": str(profile.get("user_id") or ""),
            "name": profile.get("name") or "",
            "school": profile.get("school") or "",
            "phone": profile.get("phone_number") or "",
        }

    # ---------------- 课程与课堂 ----------------

    def courses(self) -> list[dict[str, Any]]:
        data = self.http.json("GET", "/v/course_meta/learning_list/")
        rows = data.get("data") or []
        out = []
        for row in rows:
            teacher = row.get("teacher") or {}
            out.append(
                {
                    "course_id": str(row.get("course_id") or ""),
                    "classroom_id": str(row.get("classroom_id") or ""),
                    "course_name": row.get("course_name") or "未命名课程",
                    "classroom_name": row.get("classroom_name") or "",
                    "teacher": teacher.get("name") or "",
                }
            )
        return out

    def on_lesson(self) -> list[dict[str, Any]]:
        data = self.http.json("GET", "/api/v3/classroom/on-lesson")
        if data.get("code") not in (0, None):
            return []
        payload = data.get("data") or {}
        rows = payload.get("onLessonClassrooms") or []
        return [
            {
                "course_id": str(r.get("courseId") or ""),
                "lesson_id": str(r.get("lessonId") or ""),
                "raw": r,
            }
            for r in rows
        ]

    def checkin(self, lesson_id: str) -> dict[str, Any]:
        """进入课堂。成功后 http.bearer / http.lesson_token 被填充。"""
        resp = self.http.request(
            "POST",
            "/api/v3/lesson/checkin",
            json_body={"source": 21, "lessonId": str(lesson_id), "joinIfNotIn": True},
        )
        try:
            data = resp.json()
        except ValueError:
            raise YktError("进课堂响应异常", path="checkin") from None
        code = data.get("code")
        if code != 0:
            return {"ok": False, "code": code, "msg": data.get("msg") or "", "lesson_id": str(lesson_id)}
        bearer = resp.headers.get("set-auth")
        if bearer:
            self.http.bearer = bearer
        self.http.lesson_token = (data.get("data") or {}).get("lessonToken")
        return {
            "ok": True,
            "code": 0,
            "lesson_id": str(lesson_id),
            "lesson_token": self.http.lesson_token,
            "bearer": bool(bearer),
        }

    def scan(self, qr_url_or_content: str) -> dict[str, Any]:
        """扫动态二维码：返回 lessonId 并顺手进课堂。"""
        data = _unwrap(self.http.json("POST", "/api/v3/app/scan", json_body={"url": qr_url_or_content}), "scan")
        lesson_id = str((data or {}).get("value") or "")
        if not lesson_id:
            raise YktError("扫码结果里没有 lessonId", path="scan")
        result = self.checkin(lesson_id)
        return {"lesson_id": lesson_id, "checkin": result}

    # ---------------- 位置签到 ----------------

    def location_eligibility(self) -> dict[str, Any]:
        return self.http.json("GET", "/api/v3/lesson/location-checkin/eligibility", headers=self.http.bearer_headers())

    def location_submit(self, lesson_id: str, lat: float, lng: float, address: str) -> dict[str, Any]:
        body = {
            "lat": lat,
            "lng": lng,
            "lessonId": str(lesson_id),
            "source": 54,
            "address": address or "",
        }
        return self.http.json(
            "POST",
            "/api/v3/lesson/location-checkin/submit",
            json_body=body,
            headers=self.http.bearer_headers(),
        )

    # ---------------- 课件与答题 ----------------

    def presentation(self, presentation_id: str) -> dict[str, Any]:
        data = self.http.json(
            "GET",
            "/api/v3/lesson/presentation/fetch",
            params={"presentation_id": presentation_id},
            headers=self.http.bearer_headers(),
        )
        return (data.get("data") or {}) if isinstance(data, dict) else {}

    def answer(
        self,
        problem_id: str,
        problem_type: int,
        result: Any,
        *,
        dt: int | None = None,
        retry: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "problemId": problem_id,
            "dt": int(dt if dt is not None else time.time() * 1000),
            "problemType": int(problem_type),
            "result": result,
        }
        path = "/api/v3/lesson/problem/retry" if retry else "/api/v3/lesson/problem/answer"
        if retry:
            body = {"problems": [{**body, "retry_times": None}]}
        data = self.http.json("POST", path, json_body=body, headers=self.http.bearer_headers())
        return data if isinstance(data, dict) else {"code": 0}
