"""守护逻辑：发现上课 → 自动进课堂 → 处理位置签到 / 课堂答题 / 点名。"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any

from .answer import QuestionBank, build_result, find_problem, problems_from_presentation, submit_delay, type_name
from .api import YuketangAPI
from .http import YktError
from .notify import notify
from .wsclient import LessonSocket


class LessonWatcher:
    def __init__(
        self,
        api: YuketangAPI,
        cfg: dict[str, Any],
        bank: QuestionBank | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.api = api
        self.cfg = cfg
        self.bank = bank or QuestionBank()
        self.log = logger or logging.getLogger("ykt.watch")
        self.active: dict[str, LessonSocket] = {}
        self._problems: dict[str, dict[str, Any]] = {}
        self._presentation_id: str = ""
        self._submitted: set[str] = set()

    # ---------------- 进课堂 ----------------

    def enter(self, lesson_id: str) -> bool:
        result = self.api.checkin(lesson_id)
        if result.get("ok"):
            self.log.info("已进入课堂 lesson=%s（Bearer %s）", lesson_id, "就绪" if result.get("bearer") else "缺失")
            return True
        self.log.warning("进课堂失败 lesson=%s code=%s %s", lesson_id, result.get("code"), result.get("msg") or "")
        return False

    # ---------------- 位置签到 ----------------

    def auto_location_checkin(self, lesson_id: str) -> dict[str, Any]:
        cfg_loc = self.cfg.get("location") or {}
        try:
            elig = self.api.location_eligibility()
        except YktError as exc:
            self.log.warning("查询位置签到状态失败：%s", exc)
            return {"ok": False, "reason": str(exc)}

        if elig.get("signed") is True:
            self.log.info("位置签到：此前已签，无需操作")
            return {"ok": True, "skipped": True, "reason": "already-signed"}
        if elig.get("needLocationSignIn") is False:
            self.log.info("位置签到：本堂课不要求位置签到")
            return {"ok": True, "skipped": True, "reason": "not-required"}

        session = elig.get("session") or {}
        fallback = cfg_loc.get("fallback") or {}
        lat = session.get("centerLat")
        lng = session.get("centerLng")
        if (lat is None or lng is None) and cfg_loc.get("use_server_center", True):
            lat, lng = fallback.get("lat") or 0.0, fallback.get("lng") or 0.0
        if lat in (None, 0) and lng in (None, 0):
            self.log.warning("位置签到：拿不到有效坐标（eligibility 无 center，且未配置 fallback）")
            return {"ok": False, "reason": "no-coordinate"}

        address = elig.get("address") or fallback.get("address") or ""
        try:
            resp = self.api.location_submit(lesson_id, float(lat), float(lng), str(address))
        except (YktError, TypeError, ValueError) as exc:
            self.log.warning("提交位置签到失败：%s", exc)
            return {"ok": False, "reason": str(exc)}

        code = resp.get("code")
        if code == 0:
            self.log.info("位置签到成功（%.6f, %.6f）", float(lat), float(lng))
            notify("雨课堂 · 位置签到", f"已签到（{address or '课堂中心点'}）", (self.cfg.get("remind") or {}).get("webhook", ""))
            return {"ok": True, "lat": lat, "lng": lng, "address": address}
        self.log.warning("位置签到返回 code=%s %s", code, resp.get("msg") or "")
        return {"ok": False, "code": code, "reason": resp.get("msg") or ""}

    # ---------------- 课件与答题 ----------------

    def _load_problems(self, presentation_id: str) -> None:
        if not presentation_id:
            return
        try:
            pres = self.api.presentation(presentation_id)
        except YktError as exc:
            self.log.warning("拉取课件失败：%s", exc)
            return
        problems = problems_from_presentation(pres)
        for problem in problems:
            self._problems[str(problem.get("problemId"))] = problem
        self._presentation_id = presentation_id
        self.log.debug("课件 %s 内共 %s 道题（累计 %s）", presentation_id, len(problems), len(self._problems))

    def _schedule_answer(self, info: dict[str, Any]) -> None:
        if not (self.cfg.get("answer") or {}).get("enabled", True):
            return
        problem_id = str(info.get("prob") or info.get("problemId") or "")
        if not problem_id or problem_id in self._submitted:
            return
        limit = info.get("limit")
        dt = info.get("dt")
        delay = submit_delay(limit, self.cfg.get("answer") or {})
        self.log.info("新题 %s（限时 %s 秒），%.1f 秒后提交", problem_id, limit or "无", delay)
        timer = threading.Timer(delay, self._submit_answer, args=(problem_id, dt))
        timer.daemon = True
        timer.start()

    def _submit_answer(self, problem_id: str, dt: int | None) -> None:
        problem = self._problems.get(str(problem_id))
        if problem is None:
            # 发题瞬间课件可能还没落到本地缓存，补一次
            if self._presentation_id:
                self._load_problems(self._presentation_id)
            problem = self._problems.get(str(problem_id))
        if problem is None:
            self.log.warning("题目 %s 不在课件缓存里，跳过自动作答", problem_id)
            return

        ptype = int(problem.get("problemType") or 0)
        result, source = build_result(problem, self.cfg.get("answer") or {}, self.bank)
        try:
            resp = self.api.answer(str(problem_id), ptype, result, dt=dt)
        except YktError as exc:
            self.log.warning("提交答案失败（%s）：%s", type_name(ptype), exc)
            return
        code = resp.get("code")
        if code == 0:
            self._submitted.add(str(problem_id))
            self.log.info("已作答 %s [%s · %s] -> %s", problem_id, type_name(ptype), source, result)
        else:
            self.log.warning("作答被拒 %s：code=%s %s", problem_id, code, resp.get("msg") or "")

    # ---------------- WS 事件 ----------------

    def handle_event(self, op: str, data: dict[str, Any], lesson_id: str = "") -> None:
        lesson_id = lesson_id or self._lesson_id
        if op == "hello":
            presentation = data.get("presentation")
            for event in reversed(data.get("timeline") or []):
                if event.get("type") == "slide" and event.get("pres"):
                    presentation = event.get("pres")
                    break
            if presentation:
                self._load_problems(str(presentation))
            self.log.info("课堂握手完成，当前课件 %s", presentation or "未下发")
        elif op == "showpresentation":
            if data.get("presentation"):
                self._load_problems(str(data["presentation"]))
        elif op == "unlockproblem":
            info = data.get("problem") or {}
            self._schedule_answer(info)
        elif op == "extendtime":
            info = data.get("problem") or {}
            self.log.info("限时延长 %s 秒", info.get("extend"))
        elif op == "callpaused":
            event = data.get("event") or {}
            if event.get("code") == "RANDOM_PICK":
                who = data.get("name") or ""
                self.log.warning("随机点名：%s", who or "有人被点")
                notify("雨课堂 · 点名", f"课堂随机点名：{who}", (self.cfg.get("remind") or {}).get("webhook", ""))
        elif op == "locationcheckinext":
            self.log.info("收到位置签到开始事件")
            threading.Thread(target=self.auto_location_checkin, args=(lesson_id,), daemon=True).start()
        elif op in ("lessonfinished", "showfinished"):
            event = data.get("event") or {}
            if event.get("code") == "LESSON_FINISH":
                self.log.info("课堂结束，停止监听")
                socket = self.active.pop(lesson_id, None)
                if socket:
                    socket.stop()

    # ---------------- 单堂课 ----------------

    _lesson_id: str = ""

    def watch_lesson(self, lesson: dict[str, Any]) -> None:
        lesson_id = str(lesson.get("lesson_id") or "")
        if not lesson_id or lesson_id in self.active:
            return
        self._lesson_id = lesson_id
        course = lesson.get("course_id") or ""
        self.log.info("发现正在上课：lesson=%s course=%s", lesson_id, course)

        if (self.cfg.get("lesson") or {}).get("auto_join", True) and not self.enter(lesson_id):
            return
        if not self.api.http.lesson_token:
            self.log.warning("没有 lessonToken，无法建立课堂连接")
            return

        socket = LessonSocket(
            self.api.http.base_url,
            self.api.http.uid,
            lesson_id,
            str(self.api.http.lesson_token),
            lambda op, data: self.handle_event(op, data, lesson_id),
            logger=self.log,
        )
        self.active[lesson_id] = socket
        socket.run_forever()  # 阻塞到下课或放弃重连

    # ---------------- 轮询 ----------------

    def poll_once(self) -> list[dict[str, Any]]:
        try:
            lessons = self.api.on_lesson()
        except YktError as exc:
            self.log.warning("查询正在上课失败：%s", exc)
            return []
        watch = set(str(c) for c in ((self.cfg.get("lesson") or {}).get("watch_courses") or []))
        return [l for l in lessons if not watch or l.get("course_id") in watch]

    def loop(self, interval: float | None = None) -> None:
        gap = float(interval or ((self.cfg.get("lesson") or {}).get("poll_interval") or 30))
        self.log.info("开始监听课堂（每 %s 秒轮询一次，Ctrl+C 结束）", gap)
        while True:
            for lesson in self.poll_once():
                lesson_id = str(lesson.get("lesson_id") or "")
                if lesson_id in self.active:
                    continue
                thread = threading.Thread(
                    target=self.watch_lesson, args=(lesson,), name=f"ykt-lesson-{lesson_id}", daemon=True
                )
                thread.start()
            time.sleep(gap)


def now_text() -> str:
    return datetime.now().strftime("%H:%M:%S")
