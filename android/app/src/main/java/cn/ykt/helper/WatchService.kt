package cn.ykt.helper

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

/**
 * 前台服务：轮询正在上课 → 进课堂 → 连课堂 WS → 位置签到 / 自动答题 / 点名提醒。
 */
class WatchService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var store: Store
    private lateinit var http: YktHttp
    private lateinit var api: YktApi
    private var pollJob: Job? = null

    private val sockets = ConcurrentHashMap<String, LessonSocket>()
    private val problems = ConcurrentHashMap<String, JSONObject>()
    private val answered = ConcurrentHashMap.newKeySet<String>()

    override fun onCreate() {
        super.onCreate()
        store = Store(this)
        http = YktHttp(store)
        api = YktApi(http)
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIFY_ID, buildNotification("正在监听课堂…"))
        if (pollJob == null) {
            LogBus.add("守护已启动（每 ${store.pollInterval} 秒查一次正在上课）")
            pollJob = scope.launch { pollLoop() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        LogBus.add("守护已停止")
        sockets.values.forEach { it.close() }
        sockets.clear()
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ---------------- 轮询 ----------------

    private suspend fun pollLoop() {
        while (scope.isActive) {
            try {
                val lessons = api.onLesson()
                for (lesson in lessons) {
                    val lessonId = lesson.optString("lessonId")
                    if (lessonId.isBlank() || sockets.containsKey(lessonId)) continue
                    joinLesson(lessonId)
                }
            } catch (e: Exception) {
                LogBus.add("轮询失败：${e.message}")
            }
            delay(store.pollInterval * 1000L)
        }
    }

    private fun joinLesson(lessonId: String) {
        try {
            api.checkin(lessonId)
            LogBus.add("已进入课堂 lesson=$lessonId")
            notify("已进入课堂", "lesson=$lessonId", 2)
        } catch (e: Exception) {
            LogBus.add("进课堂失败：${e.message}")
            return
        }
        val token = http.lessonToken ?: run {
            LogBus.add("没有 lessonToken，无法连接课堂")
            return
        }
        val socket = LessonSocket(http, lessonId, store.uid, token) { op, payload ->
            onEvent(lessonId, op, payload)
        }
        sockets[lessonId] = socket
        socket.connect()
    }

    // ---------------- WS 事件 ----------------

    private fun onEvent(lessonId: String, op: String, payload: JSONObject) {
        when (op) {
            "hello" -> {
                var presentation = payload.optString("presentation")
                val timeline = payload.optJSONArray("timeline")
                if (timeline != null) {
                    for (i in timeline.length() - 1 downTo 0) {
                        val item = timeline.optJSONObject(i) ?: continue
                        if (item.optString("type") == "slide" && item.optString("pres").isNotBlank()) {
                            presentation = item.optString("pres")
                            break
                        }
                    }
                }
                if (presentation.isNotBlank()) loadPresentation(presentation)
            }

            "showpresentation" -> {
                val presentation = payload.optString("presentation")
                if (presentation.isNotBlank()) loadPresentation(presentation)
            }

            "unlockproblem" -> {
                val problem = payload.optJSONObject("problem") ?: return
                val problemId = problem.optString("prob")
                if (problemId.isBlank()) return
                val limit = problem.optInt("limit", 0)
                scheduleAnswer(problemId, limit)
            }

            "extendtime" -> LogBus.add("限时延长 ${payload.optJSONObject("problem")?.optInt("extend") ?: 0} 秒")

            "callpaused" -> {
                val event = payload.optJSONObject("event")
                if (event?.optString("code") == "RANDOM_PICK") {
                    val who = payload.optString("name")
                    LogBus.add("随机点名：$who")
                    notify("随机点名", "课堂点名：$who", 3)
                }
            }

            "locationcheckinext" -> {
                LogBus.add("收到位置签到开始事件")
                scope.launch { autoLocationCheckin(lessonId) }
            }

            "lessonfinished" -> {
                LogBus.add("课堂结束，停止监听 lesson=$lessonId")
                sockets.remove(lessonId)?.close()
            }

            "wsfail" -> LogBus.add("WS 断开：${payload.optString("error")}")
        }
    }

    // ---------------- 课件与答题 ----------------

    private fun loadPresentation(presentationId: String) {
        try {
            val data = api.presentation(presentationId)
            val slides = data.optJSONArray("slides") ?: return
            var count = 0
            for (i in 0 until slides.length()) {
                val problem = slides.optJSONObject(i)?.optJSONObject("problem") ?: continue
                val id = problem.optString("problemId")
                if (id.isNotBlank()) {
                    problems[id] = problem
                    count++
                }
            }
            LogBus.add("课件 $presentationId 载入，题目 $count 道（累计 ${problems.size}）")
        } catch (e: Exception) {
            LogBus.add("拉取课件失败：${e.message}")
        }
    }

    private fun scheduleAnswer(problemId: String, limitSeconds: Int) {
        if (!store.autoAnswer || answered.contains(problemId)) return
        val wait = AnswerEngine.submitDelayMillis(limitSeconds, store.delayRatio)
        LogBus.add("新题 $problemId（限时 ${if (limitSeconds > 0) "${limitSeconds}s" else "无"}），${wait / 1000} 秒后提交")
        scope.launch {
            delay(wait)
            submitAnswer(problemId)
        }
    }

    private fun submitAnswer(problemId: String) {
        val problem = problems[problemId] ?: run {
            LogBus.add("题目 $problemId 不在缓存里，跳过")
            return
        }
        val type = problem.optInt("problemType", 0)
        val (result, source) = AnswerEngine.build(problem, store.fallbackSingle)
        try {
            val resp = api.answer(problemId, type, result, System.currentTimeMillis())
            if (resp.optInt("code", -1) == 0) {
                answered.add(problemId)
                LogBus.add("已作答 ${AnswerEngine.typeName(type)} · $source → $result")
            } else {
                LogBus.add("作答被拒：${resp.optString("msg")}")
            }
        } catch (e: Exception) {
            LogBus.add("提交答案失败：${e.message}")
        }
    }

    // ---------------- 位置签到 ----------------

    private fun autoLocationCheckin(lessonId: String) {
        if (!store.autoLocation) {
            LogBus.add("位置签到开关关闭，跳过")
            return
        }
        try {
            val eligibility = api.locationEligibility()
            if (eligibility.optBoolean("signed", false)) {
                LogBus.add("位置签到：此前已签")
                return
            }
            if (!eligibility.optBoolean("needLocationSignIn", true)) {
                LogBus.add("位置签到：本堂课不要求")
                return
            }
            val session = eligibility.optJSONObject("session")
            val lat = session?.optDouble("centerLat", Double.NaN) ?: Double.NaN
            val lng = session?.optDouble("centerLng", Double.NaN) ?: Double.NaN
            if (lat.isNaN() || lng.isNaN()) {
                LogBus.add("位置签到：拿不到课堂中心坐标")
                return
            }
            val address = eligibility.optString("address")
            val resp = api.locationSubmit(lessonId, lat, lng, address)
            if (resp.optInt("code", -1) == 0) {
                LogBus.add("位置签到成功（%.6f, %.6f）".format(lat, lng))
                notify("签到成功", "位置签到已完成", 4)
            } else {
                LogBus.add("位置签到被拒：${resp.optString("msg")}")
            }
        } catch (e: Exception) {
            LogBus.add("位置签到失败：${e.message}")
        }
    }

    // ---------------- 通知 ----------------

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(NotificationManager::class.java)
            val channel = NotificationChannel(CHANNEL_ID, "雨课堂助手", NotificationManager.IMPORTANCE_LOW)
            manager.createNotificationChannel(channel)
            val alert = NotificationChannel(CHANNEL_ALERT, "雨课堂提醒", NotificationManager.IMPORTANCE_HIGH)
            manager.createNotificationChannel(alert)
        }
    }

    private fun buildNotification(text: String): Notification {
        val intent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("雨课堂助手")
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentIntent(intent)
            .setOngoing(true)
            .build()
    }

    private fun notify(title: String, body: String, id: Int) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val manager = getSystemService(NotificationManager::class.java)
            if (manager.areNotificationsEnabled()) post(title, body, id)
        } else {
            post(title, body, id)
        }
    }

    private fun post(title: String, body: String, id: Int) {
        val notification = NotificationCompat.Builder(this, CHANNEL_ALERT)
            .setContentTitle(title)
            .setContentText(body)
            .setSmallIcon(R.drawable.ic_stat)
            .setAutoCancel(true)
            .build()
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(id, notification)
    }

    companion object {
        private const val CHANNEL_ID = "ykt_service"
        private const val CHANNEL_ALERT = "ykt_alert"
        private const val NOTIFY_ID = 1001

        fun start(context: Context) {
            val intent = Intent(context, WatchService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, WatchService::class.java))
        }
    }
}
