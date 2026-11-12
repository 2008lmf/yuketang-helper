"""桌面通知：Windows Toast / macOS 通知 / Linux notify-send，失败则退回控制台。

Windows 走 PowerShell 调 WinRT 的 ToastNotification，不需要装任何第三方包。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request

_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null
$title = $env:YKT_TOAST_TITLE
$body  = $env:YKT_TOAST_BODY
$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)
$nodes = $xml.GetElementsByTagName('text')
$nodes.Item(0).AppendChild($xml.CreateTextNode($title)) | Out-Null
$nodes.Item(1).AppendChild($xml.CreateTextNode($body)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('雨课堂助手').Show($toast)
"""


def _console(title: str, body: str) -> None:
    print(f"[提醒] {title} — {body}", flush=True)
    try:
        if os.name == "nt":
            import winsound

            winsound.MessageBeep()
    except Exception:  # 通知失败不影响主流程
        pass


def _toast(title: str, body: str) -> bool:
    try:
        env = {**os.environ, "YKT_TOAST_TITLE": title, "YKT_TOAST_BODY": body}
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _webhook(url: str, title: str, body: str) -> bool:
    if not url:
        return False
    payload = json.dumps({"text": f"{title} — {body}", "title": title, "body": body}).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def notify(title: str, body: str, webhook: str = "") -> None:
    """发一条提醒。桌面通知失败也一定会打印到控制台。"""
    delivered = _webhook(webhook, title, body)

    if os.name == "nt":
        delivered = _toast(title, body) or delivered
    elif sys.platform == "darwin":
        try:
            script = f'display notification {json.dumps(body)} with title {json.dumps(title)}'
            delivered = subprocess.run(["osascript", "-e", script], timeout=8, check=False).returncode == 0 or delivered
        except (OSError, subprocess.SubprocessError):
            pass
    elif shutil.which("notify-send"):
        delivered = subprocess.run(["notify-send", title, body], timeout=8, check=False).returncode == 0 or delivered

    if not delivered:
        _console(title, body)
