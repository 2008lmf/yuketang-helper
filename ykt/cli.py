"""命令行入口：ykt login / watch / signin / remind / answer …"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .answer import QuestionBank, build_result, type_name
from .api import YuketangAPI
from .config import (
    SERVERS,
    bank_path,
    config_path,
    load_config,
    resolve_server,
    save_config,
    state_dir,
)
from .http import RCHttp, YktError
from .notify import notify
from .qr import resolve_source
from .remind import describe, next_occurrence, parse_hhmm, remind_at, upcoming
from .watch import LessonWatcher


# ---------------------------------------------------------------- 基础


def setup_logging(verbose: bool) -> logging.Logger:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger("ykt")


def build_api(cfg: dict[str, Any], server: str | None = None) -> YuketangAPI:
    base_url = resolve_server(server or cfg.get("server") or "yuketang")
    key = next((k for k, v in SERVERS.items() if v == base_url), base_url)
    if not cfg.get("device_id"):
        cfg["device_id"] = uuid.uuid4().hex
        save_config(cfg)
    http = RCHttp(base_url, server=str(key))
    http.uid = str(cfg.get("uid") or "")
    return YuketangAPI(http, device_id=str(cfg["device_id"]))


def require_login(api: YuketangAPI) -> None:
    if not api.http.has_session():
        raise YktError("还没登录，先执行：ykt login --qr")


def finish_login(api: YuketangAPI, cfg: dict[str, Any]) -> None:
    info = api.user_info()
    cfg["uid"] = info["uid"]
    cfg["user"] = {"name": info["name"], "school": info["school"]}
    api.http.uid = info["uid"]
    path = save_config(cfg)
    print(f"登录成功：{info['name'] or '（未命名）'}  {info['school']}")
    print(f"uid={info['uid']}  cookie 已存到 {state_dir()}")
    print(f"配置：{path}")


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- login


def cmd_login(args: argparse.Namespace, cfg: dict[str, Any], log: logging.Logger) -> int:
    api = build_api(cfg, args.server)

    if args.phone:
        phone = args.phone
        if args.code:
            api.login_with_code(phone, args.code)
        else:
            api.send_code(phone)
            print(f"验证码已发送到 {phone}")
            code = input("输入收到的验证码：").strip()
            api.login_with_code(phone, code)
        finish_login(api, cfg)
        return 0

    if args.account:
        if not args.password:
            raise YktError("密码登录需要 --password（注意雨课堂可能要求图形验证码，失败就改用 --phone）")
        api.login_with_password(args.account, args.password)
        finish_login(api, cfg)
        return 0

    # 默认：扫码
    info = api.pre_info()
    token = str(info.get("token") or "")
    if not token:
        raise YktError("pre-info 没返回 token")
    print("用雨课堂 App（或微信）扫下面的二维码登录：")
    if info.get("qrImage"):
        print(f"  二维码图片：{info['qrImage']}")
    if info.get("qrContent"):
        print(f"  二维码内容：{info['qrContent']}")
    _try_render_ascii(str(info.get("qrContent") or ""))

    deadline = time.time() + float(args.timeout)
    while time.time() < deadline:
        try:
            api.login_by_qr(token)
            finish_login(api, cfg)
            return 0
        except YktError as exc:
            if exc.code == 50001:  # 本轮 30 秒没扫到，继续轮询
                log.debug("等待扫码…")
                continue
            raise YktError(f"扫码登录失败：{exc}") from exc
    raise YktError("扫码登录超时，重新执行 `ykt login --qr` 换个二维码")


def _try_render_ascii(content: str) -> None:
    try:
        import qrcode  # type: ignore
    except ImportError:
        return
    try:
        qr = qrcode.QRCode(border=1)
        qr.add_data(content)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:  # 渲染失败不影响主流程
        pass


# ---------------------------------------------------------------- 查询


def cmd_whoami(args: argparse.Namespace, cfg: dict[str, Any], log: logging.Logger) -> int:
    api = build_api(cfg, args.server)
    require_login(api)
    info = api.user_info()
    cfg["uid"] = info["uid"]
    cfg["user"] = {"name": info["name"], "school": info["school"]}
    save_config(cfg)
    print(f"{info['name']}  uid={info['uid']}  {info['school']}")
    print(f"站点：{api.http.base_url}   config：{config_path()}")
    return 0


def cmd_courses(args: argparse.Namespace, cfg: dict[str, Any], log: logging.Logger) -> int:
    api = build_api(cfg, args.server)
    require_login(api)
    rows = api.courses()
    if not rows:
        print("没有课程（或登录已失效）")
        return 0
    print(f"{'课程ID':<12}{'课程':<24}{'班级':<18}教师")
    for row in rows:
        print(f"{row['course_id']:<12}{row['course_name'][:22]:<24}{row['classroom_name'][:16]:<18}{row['teacher']}")
    return 0


def cmd_lessons(args: argparse.Namespace, cfg: dict[str, Any], log: logging.Logger) -> int:
    api = build_api(cfg, args.server)
    require_login(api)
    rows = api.on_lesson()
    if not rows:
        print("当前没有正在上的课")
        return 0
    for row in rows:
        print(f"lesson={row['lesson_id']}  course={row['course_id']}")
    return 0


# ---------------------------------------------------------------- 签到


def cmd_checkin(args: argparse.Namespace, cfg: dict[str, Any], log: logging.Logger) -> int:
    api = build_api(cfg, args.server)
    require_login(api)
    lesson_id = args.lesson
    if not lesson_id:
        rows = api.on_lesson()
        if not rows:
            raise YktError("没有正在上的课；请用 --lesson 指定，或先 `ykt lessons` 看一眼")
        lesson_id = rows[0]["lesson_id"]
        print(f"自动选择课堂：{lesson_id}")
    result = api.checkin(lesson_id)
    if not result.get("ok"):
        print(f"进入课堂失败：code={result.get('code')} {result.get('msg') or ''}")
        return 1
    print(f"已进入课堂 {lesson_id}（Bearer {'就绪' if result.get('bearer') else '缺失'}）")
    if args.location:
        watcher = LessonWatcher(api, cfg, QuestionBank.load(bank_path()), log)
        print(json.dumps(watcher.auto_location_checkin(lesson_id), ensure_ascii=False))
    return 0


def cmd_signin(args: argparse.Namespace, cfg: dict[str, Any], log: logging.Logger) -> int:
    api = build_api(cfg, args.server)
    require_login(api)
    url = resolve_source(args.qr)
    print(f"扫码内容：{url[:120]}{'…' if len(url) > 120 else ''}")
    result = api.scan(url)
    lesson_id = result["lesson_id"]
    print(f"lessonId={lesson_id}  进课堂：{result['checkin'].get('ok')}")
    if args.location:
        watcher = LessonWatcher(api, cfg, QuestionBank.load(bank_path()), log)
        print(json.dumps(watcher.auto_location_checkin(lesson_id), ensure_ascii=False))
    return 0 if result["checkin"].get("ok") else 1


def cmd_watch(args: argparse.Namespace, cfg: dict[str, Any], log: logging.Logger) -> int:
    api = build_api(cfg, args.server)
    require_login(api)
    watcher = LessonWatcher(api, cfg, QuestionBank.load(bank_path()), log)
    if args.lesson:
        watcher.watch_lesson({"lesson_id": args.lesson})
        return 0
    watcher.loop(args.interval)
    return 0


def cmd_answer(args: argparse.Namespace, cfg: dict[str, Any], log: logging.Logger) -> int:
    api = build_api(cfg, args.server)
    require_login(api)
    result: Any
    if args.result:
        result = json.loads(args.result)
    else:
        result = [x.strip() for x in args.options.split(",") if x.strip()]
    resp = api.answer(args.problem_id, args.type, result)
    print(f"{type_name(args.type)} {args.problem_id} -> {result}")
    _print_json(resp)
    return 0 if resp.get("code") == 0 else 1


# ---------------------------------------------------------------- 题库


def cmd_bank(args: argparse.Namespace, cfg: dict[str, Any], log: logging.Logger) -> int:
    path = bank_path()
    bank = QuestionBank.load(path)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    if args.add:
        question, _, answer = args.add.partition("=")
        if not answer:
            raise YktError("格式：ykt bank --add \"题干=答案\"")
        data[question.strip()] = [a.strip() for a in answer.split("|") if a.strip()]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入题库（共 {len(data)} 条）：{path}")
        return 0

    if args.import_file:
        raw = json.loads(Path(args.import_file).read_text(encoding="utf-8"))
        data.update(raw)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已导入，共 {len(data)} 条")
        return 0

    print(f"题库：{path}（{len(bank)} 条可用）")
    for question, answer in list(data.items())[:30]:
        print(f"  {question} -> {'|'.join(answer) if isinstance(answer, list) else answer}")
    return 0


# ---------------------------------------------------------------- 提醒


def cmd_remind(args: argparse.Namespace, cfg: dict[str, Any], log: logging.Logger) -> int:
    table: list[dict[str, Any]] = cfg.setdefault("remind", {}).setdefault("timetable", [])

    if args.action == "list":
        if not table:
            print("课表是空的，用 `ykt remind add --name 高数 --weekday 1 --time 08:00` 添加")
            return 0
        now = datetime.now()
        for when, entry in upcoming(table, now):
            print(f"  下次：{when:%Y-%m-%d %H:%M}  {describe(entry)}")
        return 0

    if args.action == "add":
        if not args.name or not args.time or not args.weekday:
            raise YktError("需要 --name、--weekday(1-7)、--time HH:MM")
        parse_hhmm(args.time)
        entry = {
            "name": args.name,
            "weekday": int(args.weekday),
            "time": args.time,
            "room": args.room or "",
            "before_minutes": args.before,
        }
        table.append(entry)
        save_config(cfg)
        print(f"已添加：{describe(entry)}")
        return 0

    if args.action == "remove":
        if not args.name:
            raise YktError("用 --name 指定要删除的课程名")
        before = len(table)
        cfg["remind"]["timetable"] = [e for e in table if e.get("name") != args.name]
        save_config(cfg)
        print(f"已删除 {before - len(cfg['remind']['timetable'])} 条")
        return 0

    if args.action == "run":
        interval = float(args.interval)
        print(f"提醒已启动（每 {interval:.0f} 秒检查一次，Ctrl+C 结束）")
        fired: set[str] = set()
        while True:
            now = datetime.now()
            for entry in table:
                key = f"{entry.get('name')}|{now:%Y-%m-%d}"
                due = remind_at(entry, now, int((cfg.get("remind") or {}).get("before_minutes") or 10))
                if now >= due and key not in fired and now < next_occurrence(entry, now):
                    fired.add(key)
                    title = "雨课堂 · 上课提醒"
                    body = f"{describe(entry)} 即将开始"
                    notify(title, body, str((cfg.get("remind") or {}).get("webhook") or ""))
                    log.info("%s | %s", title, body)
            time.sleep(interval)
    return 0


# ---------------------------------------------------------------- 配置


def cmd_config(args: argparse.Namespace, cfg: dict[str, Any], log: logging.Logger) -> int:
    if args.set:
        key, _, value = args.set.partition("=")
        if not value:
            raise YktError("格式：ykt config --set key=value（嵌套用 a.b.c=1）")
        node = cfg
        parts = key.strip().split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        try:
            parsed: Any = json.loads(value)
        except ValueError:
            parsed = value
        node[parts[-1]] = parsed
        save_config(cfg)
        print(f"{key} = {parsed!r}")
        return 0
    _print_json(cfg)
    print(f"\n配置文件：{config_path()}")
    return 0


# ---------------------------------------------------------------- 参数


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ykt", description="雨课堂助手：签到 / 答题 / 上课提醒")
    parser.add_argument("--version", action="version", version=f"yuketang-helper {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    parser.add_argument("--server", default=None, help="站点：" + " / ".join(SERVERS))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="登录")
    p.add_argument("--qr", action="store_true", help="扫码登录（默认）")
    p.add_argument("--phone", help="手机号：短信验证码登录")
    p.add_argument("--code", help="配合 --phone：直接给出验证码，不交互")
    p.add_argument("--account", help="账号：密码登录（雨课堂可能要求图形验证码）")
    p.add_argument("--password", help="配合 --account")
    p.add_argument("--timeout", type=float, default=180.0, help="扫码等待总时长（秒）")
    p.set_defaults(func=cmd_login)

    sub.add_parser("whoami", help="查看当前登录用户").set_defaults(func=cmd_whoami)
    sub.add_parser("courses", help="课程列表").set_defaults(func=cmd_courses)
    sub.add_parser("lessons", help="正在上课的课堂").set_defaults(func=cmd_lessons)

    p = sub.add_parser("checkin", help="进入课堂（= 签到），可选顺带位置签到")
    p.add_argument("--lesson", help="lessonId，省略则自动取第一个正在上课的")
    p.add_argument("--location", action="store_true", help="顺便提交位置签到")
    p.set_defaults(func=cmd_checkin)

    p = sub.add_parser("signin", help="动态二维码签到")
    p.add_argument("--qr", default="clipboard", help="链接 / 含链接的文本 / 图片路径 / clipboard（默认）")
    p.add_argument("--location", action="store_true", help="顺便提交位置签到")
    p.set_defaults(func=cmd_signin)

    p = sub.add_parser("watch", help="守护：自动进课堂、位置签到、课堂答题、点名提醒")
    p.add_argument("--lesson", help="只盯这一堂课")
    p.add_argument("--interval", type=float, help="轮询 on-lesson 的间隔（秒）")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("answer", help="手动提交一道题的答案")
    p.add_argument("--problem-id", required=True)
    p.add_argument("--type", type=int, required=True, help="1单选 2多选 3投票 4填空 5主观 6判断")
    p.add_argument("--options", default="", help="选项的逗号分隔列表，如 A,C")
    p.add_argument("--result", help="直接给 result 的 JSON（主观题用）")
    p.set_defaults(func=cmd_answer)

    p = sub.add_parser("bank", help="题库")
    p.add_argument("--add", help="\"题干=答案\"，多个答案用 | 分隔")
    p.add_argument("--import-file", help="导入 JSON 题库")
    p.set_defaults(func=cmd_bank)

    p = sub.add_parser("remind", help="上课提醒")
    p.add_argument("action", choices=["add", "list", "remove", "run"])
    p.add_argument("--name", help="课程名")
    p.add_argument("--weekday", type=int, help="1=周一 … 7=周日")
    p.add_argument("--time", help="上课时间 HH:MM")
    p.add_argument("--room", help="教室")
    p.add_argument("--before", type=int, help="提前多少分钟提醒")
    p.add_argument("--interval", type=float, default=20.0, help="run 时检查间隔（秒）")
    p.set_defaults(func=cmd_remind)

    p = sub.add_parser("config", help="查看 / 修改配置")
    p.add_argument("--set", help="key=value，嵌套用 a.b=1")
    p.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    log = setup_logging(args.verbose)
    cfg = load_config()
    try:
        return int(args.func(args, cfg, log) or 0)
    except YktError as exc:
        print(f"出错了：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已停止")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
