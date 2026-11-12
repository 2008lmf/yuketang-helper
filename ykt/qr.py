"""二维码签到：从链接 / 剪贴板 / 图片里拿到扫码内容。

雨课堂的动态二维码签到本质是把老师屏幕上的链接交给 `/api/v3/app/scan`，链接形如：

    https://www.yuketang.cn/api/v3/lesson/check-in/dynamic-qr-code?lessonId=…&enc=…

所以只要能拿到这条链接（手动粘贴、剪贴板、截图识别），就能完成签到。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_URL_RE = re.compile(r"https?://[^\s\"'<>）)】]+", re.IGNORECASE)
_QR_HINTS = ("yuketang.cn", "check-in", "checkin", "enc=")


def extract_url(text: str) -> str | None:
    """从任意文本里挑出第一个像雨课堂签到链接的 URL。"""
    for match in _URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;")
        if any(hint in url.lower() for hint in _QR_HINTS):
            return url
    return None


def read_clipboard() -> str:
    """读系统剪贴板，不引入第三方依赖。"""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            return out.stdout or ""
        if sys.platform == "darwin":
            return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5, check=False).stdout or ""
        for cmd in (["wl-paste", "-n"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "-b"]):
            if shutil.which(cmd[0]):
                return subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""
    return ""


def decode_image(path: str) -> str | None:
    """从图片里识别二维码（需要可选的 opencv-python）。"""
    try:
        import cv2  # type: ignore
    except ImportError:
        raise RuntimeError("识别图片需要先安装依赖：pip install \"yuketang-helper[qr]\"") from None
    image = cv2.imread(str(path))
    if image is None:
        raise RuntimeError(f"读不到图片：{path}")
    detector = cv2.QRCodeDetector()
    data, _points, _ = detector.detectAndDecode(image)
    return data or None


def resolve_source(source: str) -> str:
    """把 --qr 的取值解析成最终要提交给 scan 的字符串。

    支持：完整链接 / 含链接的文本 / 图片路径 / 关键字 clipboard、paste。
    """
    value = (source or "").strip()
    if value in ("", "clipboard", "paste", "cb"):
        text = read_clipboard()
        url = extract_url(text)
        if not url:
            raise RuntimeError("剪贴板里没有雨课堂签到链接")
        return url
    path = Path(value)
    if path.exists() and path.is_file():
        decoded = decode_image(str(path))
        url = extract_url(decoded or "") or (decoded or "")
        if url:
            return url
        raise RuntimeError(f"图片里没有识别到二维码内容：{path}")
    url = extract_url(value)
    if url:
        return url
    if value.lower().startswith("http"):
        return value  # 用户直接给了链接，原样提交
    raise RuntimeError("没看出这是雨课堂签到链接，请直接粘贴扫码得到的那条 URL")
