# 雨课堂助手 · 协议层

本文件记录 `ykt` 实现所依赖的雨课堂（Rain Classroom）接口事实。所有信息来自对公开客户端行为与
[course_helper](https://github.com/AneryCoft/course_helper)（Dart/Flutter，GPLv3）公开研究的比对整理，
本项目是独立的 Python 实现，未复制其源码。

## 服务器

| 名称 | base_url |
|---|---|
| 雨课堂 | `https://www.yuketang.cn` |
| 荷塘雨课堂 | `https://pro.yuketang.cn` |
| 长江雨课堂 | `https://changjiang.yuketang.cn` |
| 黄河雨课堂 | `https://huanghe.yuketang.cn` |

WebSocket：`wss://<server>/wsapp/`（`www` 也写作 `www.yuketang.cn/wsapp/`）。

## 请求头

移动端形态，缺一不可（部分接口会校验 `x-csrftoken`）：

```
user-agent: Android
xtbz: ykt
x-client: app
xtua: client=app&tag=1.3.3&platform=Android
brand: google Pixel 9 Pro
systemversion: 16
buildnumber: 1610
incremental: 14624737
isphysicaldevice: true
accept: application/json
x-csrftoken: <cookie csrftoken>
x-uid: <当前用户 user_id>
sessionid: <cookie sessionid>
```

## 登录

| 用途 | 方法 | 路径 | 关键字段 |
|---|---|---|---|
| 取扫码登录票据 | GET | `/api/v3/user/login/pre-info` | 返回 `data.qrContent / qrImage / token` |
| 轮询扫码结果 | POST | `/api/v3/user/login` | `{token}`，服务端最长挂 30s，`code=0` 即成功并下发 Cookie；`50001` 为扫码超时 |
| 发送短信验证码 | POST | `/api/v3/user/code/send` | `{phoneNumber, email:"", ticket:"", rand:""}` |
| 校验验证码 | POST | `/api/v3/user/code/verify` | `{phoneNumber, email:"", code}` |
| 验证码 / 密码登录 | POST | `/api/v3/user/login/app` | `type: 1 手机 / 2 密码 / 3 短信码`；密码登录时手机号进 `phoneNumber`、邮箱进 `email` |
| 当前用户 | GET | `/v/course_meta/user_info` | `data.user_profile.{user_id,name,phone_number,school,avatar}` |

登录成功后必须持有 Cookie：`sessionid`、`csrftoken`、`university_id` 等。

## 课程与课堂

| 用途 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 课程列表 | GET | `/v/course_meta/learning_list/` | `data[].{course_id, classroom_id, course_name, classroom_name, teacher:{name,avatar}}` |
| 正在上课 | GET | `/api/v3/classroom/on-lesson` | `data.onLessonClassrooms[].{courseId, lessonId}` |
| 进入课堂（=签到） | POST | `/api/v3/lesson/checkin` | `{source:21, lessonId, joinIfNotIn:true}`；响应头 `set-auth` 即 `Bearer` 令牌，`data.lessonToken` 用于 WS |
| 扫动态二维码 | POST | `/api/v3/app/scan` | `{url:"<扫码得到的完整链接>"}` → `data.value` 为 lessonId，随后调用上面的 checkin |

## 位置签到

| 用途 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 查询可签状态 | GET | `/api/v3/lesson/location-checkin/eligibility` | 需 `authorization: Bearer <set-auth>`；返回 `signed`、`mode`、`address`、`session.{centerLat,centerLng}` |
| 提交位置签到 | POST | `/api/v3/lesson/location-checkin/submit` | `{lat, lng, lessonId, source:54, address}`，`code=0` 为成功 |

`session.centerLat/centerLng` 是课堂允许签到的中心点，直接用它提交即可通过半径校验。

## 课堂内容与答题

| 用途 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 取课件（含题目） | GET | `/api/v3/lesson/presentation/fetch?presentation_id=<id>` | 需 Bearer；`data.slides[].problem` 内含 `problemId, problemType, body, options, blanks, answers` |
| 提交答案 | POST | `/api/v3/lesson/problem/answer` | `{problemId, dt, problemType, result}`，需 Bearer |
| 补交 | POST | `/api/v3/lesson/problem/retry` | 同上，外层包一层 `{problems:[...]}` |

`problemType`：`1` 单选、`2` 多选、`3` 投票、`4` 填空、`5` 主观、`6` 判断。

`result` 形态：

- 选择/判断/投票：`["A"]` / `["A","C"]`（选项 key 数组）
- 填空：`["答案1","答案2"]`（按空顺序）
- 主观：`{"content": "文字", "pics": [{"pic":"", "thumb":""}], "videos": []}`

`dt` 为客户端毫秒时间戳；服务端用它与下发时间比对，用来判断"是否在限时内作答"。

## WebSocket 课堂事件

连接 `wss://<server>/wsapp/`，首帧发送：

```json
{"op":"hello","userid":"<uid>","role":"student","auth":"<lessonToken>","lessonid":"<lessonId>"}
```

服务端下行 `op` 及含义：

| op | 关键字段 | 含义 |
|---|---|---|
| `hello` | `presentation, slideindex, timeline[]` | 握手成功；`message == "lesson finished"` 表示已下课 |
| `showpresentation` | `presentation, slideindex, timeline` | 切换课件 |
| `slide` / `slidenav` | `slideindex` / `slide.si` | 翻页 |
| `unlockproblem` | `problem.{prob, limit, dt}` | 发题；`limit` 为限时秒数，`prob` 即 `problemId` |
| `extendtime` | `problem.extend` | 延长作答时间 |
| `callpaused` | `event.code == "RANDOM_PICK"` | 随机点名 |
| `showfinished` | `event.code == "SHOW_FINISH"` | 课件放映结束 |
| `lessonfinished` | `event.code == "LESSON_FINISH"` | 下课 |
| `locationcheckinext` | `startMs/serverTime` | 位置签到开始 |
| `locationcheckinend` | `endedAt/endMs` | 位置签到结束 |
