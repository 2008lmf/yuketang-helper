package cn.ykt.helper

import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject

/**
 * 课堂 WebSocket：wss://<host>/wsapp/，首帧 hello。
 * 事件通过 onEvent(op, payload) 抛给上层。
 */
class LessonSocket(
    private val http: YktHttp,
    private val lessonId: String,
    private val uid: String,
    private val lessonToken: String,
    private val onEvent: (String, JSONObject) -> Unit,
) {
    @Volatile
    private var socket: WebSocket? = null

    @Volatile
    var stopped = false
        private set

    private val wsUrl: String
        get() = http.baseUrl.replaceFirst("https://", "wss://").trimEnd('/') + "/wsapp/"

    fun connect() {
        val request = Request.Builder().url(wsUrl).build()
        socket = http.client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                val hello = JSONObject()
                    .put("op", "hello")
                    .put("userid", uid)
                    .put("role", "student")
                    .put("auth", lessonToken)
                    .put("lessonid", lessonId)
                webSocket.send(hello.toString())
                LogBus.add("WS 已连接，已发送 hello（lesson=$lessonId）")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val payload = runCatching { JSONObject(text) }.getOrNull() ?: return
                val op = payload.optString("op")
                if (op == "hello" && payload.optString("message") == "lesson finished") {
                    onEvent("lessonfinished", payload)
                    return
                }
                runCatching { onEvent(op, payload) }
                    .onFailure { LogBus.add("处理 $op 事件失败：${it.message}") }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (!stopped) onEvent("wsfail", JSONObject().put("error", t.message ?: "连接中断"))
            }
        })
    }

    fun close() {
        stopped = true
        runCatching { socket?.close(1000, null) }
    }
}
