# 雨课堂助手 · yuketang-helper

命令行版的雨课堂（Rain Classroom）助手：**自动签到、课堂自动答题、上课提醒**。只做雨课堂，不掺学习通。

```
$ ykt login --qr          # 微信扫码登录，Cookie 存本地
$ ykt watch               # 挂着这一个命令就够了
08:02 发现正在上课：lesson=… course=…
08:02 已进入课堂 lesson=…（Bearer 就绪）
08:02 WS 已连接 wss://www.yuketang.cn/wsapp/，已发送 hello
08:03 收到位置签到开始事件
08:03 位置签到成功（30.274084, 120.155070）
08:06 新题 3rK9（限时 90 秒），54.0 秒后提交
08:06 已作答 3rK9 [单选题 · 题库/参考答案] -> ['B']
```

## 三个功能

**1. 签到（雨课堂只有三种，都支持）**

| 签到方式 | 触发 | 命令 |
|---|---|---|
| 进入课堂签到 | 老师开课即算签到 | `ykt watch` 或 `ykt checkin` |
| 位置签到 | 课堂 WS 推 `locationcheckinext` 事件 | `ykt watch` 自动完成 |
| 动态二维码签到 | 老师屏幕上的二维码链接 | `ykt signin --qr clipboard` |

**2. 课堂答题**

WS 推 `unlockproblem` 时自动作答案，依次尝试：课件里下发的参考答案 → 本地题库（`ykt bank`）
→ 兜底策略。按限时比例延时提交（默认在限时的 60% 处交，最多等 60 秒），避免 0.1 秒交卷。
题型覆盖单选 / 多选 / 投票 / 填空 / 主观 / 判断。

**3. 上课提醒**

本地课表 + 桌面通知（Windows Toast / macOS / Linux），可配 webhook 推到手机。
`ykt watch` 也会在开课时立刻通知，等于双保险。

## 安装

需要 Python 3.9+。

```powershell
git clone https://github.com/2008lmf/yuketang-helper.git
cd yuketang-helper
python -m pip install -r requirements.txt
python -m pip install -e .        # 之后可以直接用 ykt 命令
```

可选：`pip install ".[qr]"` 支持从二维码图片识别，`pip install ".[ascii]"` 支持终端里直接显示二维码。

## 快速开始

```powershell
ykt login --qr        # 用微信扫终端里打印的二维码
ykt whoami            # 确认登录成功
ykt courses           # 看课程列表
ykt watch             # 挂着：自动进课堂 + 位置签到 + 课堂答题 + 点名提醒
```

上课提醒另开一个窗口：

```powershell
ykt remind add --name "高等数学" --weekday 3 --time 08:00 --room "教三-201" --before 10
ykt remind list
ykt remind run
```

## 命令一览

| 命令 | 作用 |
|---|---|
| `ykt login --qr` | 扫码登录（默认） |
| `ykt login --phone 138xxxx` | 短信验证码登录 |
| `ykt login --account x@y.com --password ***` | 密码登录（可能被图形验证码拦住） |
| `ykt whoami` / `ykt courses` / `ykt lessons` | 看身份 / 课程 / 正在上的课 |
| `ykt checkin [--lesson ID] [--location]` | 进课堂，顺带位置签到 |
| `ykt signin [--qr clipboard\|URL\|图片路径]` | 动态二维码签到 |
| `ykt watch [--lesson ID] [--interval 30]` | 守护进程，全自动 |
| `ykt answer --problem-id ID --type 1 --options A` | 手动交一道题 |
| `ykt bank --add "题干=答案"` / `--import-file x.json` | 维护题库 |
| `ykt remind add\|list\|remove\|run` | 课表与提醒 |
| `ykt config [--set answer.delay_ratio=0.8]` | 查看 / 修改配置 |

`--server` 可切换站点：`yuketang`（默认）、`pro`（荷塘）、`changjiang`（长江）、`huanghe`（黄河）。

## 数据放在哪

- Windows：`%APPDATA%\yuketang-helper\`
- macOS / Linux：`~/.config/yuketang-helper/`
- 可用环境变量 `YKT_HOME` 覆盖

里面是 `config.json`（配置）、`cookies.<站点>.json`（登录态）、`bank.json`（题库）。
`.gitignore` 已经把这几类文件排除，不会被提交。

关键配置项：

```json
{
  "lesson":  { "auto_join": true, "watch_courses": [], "poll_interval": 30 },
  "location": { "use_server_center": true, "fallback": { "lat": 0.0, "lng": 0.0, "address": "" } },
  "answer":  { "enabled": true, "delay_ratio": 0.6, "delay_max": 60, "fallback_single": "A" },
  "remind":  { "before_minutes": 10, "webhook": "" }
}
```

`location.use_server_center = true` 表示位置签到直接用课堂 `eligibility` 接口返回的中心点
（`session.centerLat/centerLng`），这是最容易通过半径校验的做法；也可以在 `fallback` 里写死一个坐标。

## 实现依据（已实测）

协议细节整理在 [docs/PROTOCOL.md](docs/PROTOCOL.md)。发布前用本机实测过两个端点：

```
GET https://www.yuketang.cn/api/v3/user/login/pre-info
→ 200  {"code":0,"data":{"token":"eyJ0eXAiOiJKV1Qi…","qrContent":"http://weixin.qq.com/q/02hHNKF…","qrImage":"https://mp.weixin.qq.com/cgi-bin/showqrcode?…"}}

GET https://www.yuketang.cn/api/v3/classroom/on-lesson
→ 200  {"code":50000,"msg":"UNAUTHENTICATED","data":""}
```

也就是说：扫码票据、微信二维码、未登录拦截都符合预期。**尚未实测的是需要真实课堂的部分**
——位置签到提交、`unlockproblem` 时序、答题受理，这些代码按协议实现，但没有真实开课环境能跑通，
第一次实战请开着 `-v` 看日志。

## 项目结构

```
ykt/
├── api.py        雨课堂接口（登录 / 课程 / 进出课堂 / 位置签到 / 课件 / 答题）
├── http.py       请求头、Cookie 持久化、Bearer
├── wsclient.py   课堂 WebSocket（hello / 事件 / 断线重连）
├── watch.py      守护逻辑：发现开课 → 进课堂 → 处理签到与答题
├── answer.py     答题引擎（参考答案 → 题库 → 兜底 + 延时提交）
├── qr.py         二维码链接解析（文本 / 剪贴板 / 图片）
├── remind.py     课表与下一次上课时间
├── notify.py     Windows Toast / macOS / Linux / webhook
└── cli.py        命令行
```

## 测试

```powershell
python -m unittest discover -s tests -t . -v
```

17 个离线用例，覆盖二维码链接解析、六种题型的作答构造、延时计算、课表时间轮转、站点解析，不需要联网。

## 声明

仅供个人学习与技术研究使用。签到与答题自动化会绕过教师设计的课堂互动环节，
是否使用、用在哪里，由使用者自己判断并承担后果；作者不对成绩、考勤或校规后果负责。

## 致谢

协议事实参考了 [course_helper](https://github.com/AneryCoft/course_helper)（Dart/Flutter，GPLv3）的公开研究，
本项目是独立的 Python 实现，未复制其源码。

## License

[MIT](LICENSE)
