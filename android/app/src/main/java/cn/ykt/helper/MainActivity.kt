package cn.ykt.helper

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.ImageView
import android.widget.Switch
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.qrcode.QRCodeWriter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

class MainActivity : AppCompatActivity() {

    private lateinit var store: Store
    private lateinit var http: YktHttp
    private lateinit var api: YktApi

    private lateinit var tvStatus: TextView
    private lateinit var tvLog: TextView
    private lateinit var ivQr: ImageView
    private lateinit var btnLogin: Button
    private lateinit var btnLogout: Button
    private lateinit var btnCourses: Button
    private lateinit var btnStart: Button
    private lateinit var btnStop: Button
    private lateinit var swAnswer: Switch
    private lateinit var swLocation: Switch

    private val logListener: (String) -> Unit = { line ->
        runOnUiThread {
            tvLog.append(line + "\n")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        store = Store(this)
        http = YktHttp(store)
        api = YktApi(http)

        tvStatus = findViewById(R.id.tvStatus)
        tvLog = findViewById(R.id.tvLog)
        ivQr = findViewById(R.id.ivQr)
        btnLogin = findViewById(R.id.btnLogin)
        btnLogout = findViewById(R.id.btnLogout)
        btnCourses = findViewById(R.id.btnCourses)
        btnStart = findViewById(R.id.btnStart)
        btnStop = findViewById(R.id.btnStop)
        swAnswer = findViewById(R.id.swAnswer)
        swLocation = findViewById(R.id.swLocation)

        swAnswer.isChecked = store.autoAnswer
        swLocation.isChecked = store.autoLocation

        swAnswer.setOnCheckedChangeListener { _, checked -> store.autoAnswer = checked }
        swLocation.setOnCheckedChangeListener { _, checked -> store.autoLocation = checked }

        btnLogin.setOnClickListener { startLogin() }
        btnLogout.setOnClickListener {
            store.clearLogin()
            LogBus.add("已清除本地登录信息")
            updateStatus()
        }
        btnCourses.setOnClickListener { loadCourses() }
        btnStart.setOnClickListener {
            ensureNotificationPermission()
            WatchService.start(this)
            LogBus.add("已请求启动守护服务")
        }
        btnStop.setOnClickListener {
            WatchService.stop(this)
        }

        LogBus.register(logListener)
        LogBus.snapshot().forEach { tvLog.append(it + "\n") }

        ensureNotificationPermission()
        updateStatus()
    }

    override fun onDestroy() {
        LogBus.unregister(logListener)
        super.onDestroy()
    }

    private fun updateStatus() {
        val name = store.userName.ifBlank { "未登录" }
        val uid = store.uid.ifBlank { "-" }
        tvStatus.text = "账号：$name（uid=$uid）\n站点：${SERVERS[store.server] ?: "-"}"
    }

    private fun ensureNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED
            if (!granted) {
                ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1001)
            }
        }
    }

    // ---------------- 登录 ----------------

    private fun startLogin() {
        btnLogin.isEnabled = false
        ivQr.visibility = View.VISIBLE
        ivQr.setImageResource(R.drawable.ic_stat)
        LogBus.add("正在获取登录二维码…")
        lifecycleScope.launch {
            try {
                val info = withContext(Dispatchers.IO) { api.preInfo() }
                val token = info.optString("token")
                val content = info.optString("qrContent").ifBlank { info.optString("qrImage") }
                if (token.isBlank() || content.isBlank()) throw ApiException("pre-info 没返回 token/qrContent")
                val bitmap = withContext(Dispatchers.Default) { renderQr(content) }
                ivQr.setImageBitmap(bitmap)
                LogBus.add("请用微信扫码登录")

                var ok = false
                while (isActive) {
                    val done = withContext(Dispatchers.IO) {
                        runCatching { api.loginByQr(token) }
                    }
                    if (done.isSuccess) {
                        ok = true
                        break
                    }
                    val message = done.exceptionOrNull()?.message ?: ""
                    if (message.contains("超时") || message.contains("timeout")) {
                        delay(1500)
                        continue
                    }
                    if (message.isNotBlank() && !message.contains("扫码未完成") && !message.contains("SCAN")) {
                        LogBus.add("登录失败：$message")
                        break
                    }
                    delay(1500)
                }

                if (ok) {
                    withContext(Dispatchers.IO) {
                        val profile = api.userInfo()
                        store.uid = profile.optString("user_id")
                        store.userName = profile.optString("name")
                    }
                    LogBus.add("登录成功：${store.userName}")
                    ivQr.visibility = View.GONE
                    updateStatus()
                }
            } catch (e: Exception) {
                LogBus.add("登录异常：${e.message}")
            } finally {
                btnLogin.isEnabled = true
            }
        }
    }

    private fun renderQr(content: String, size: Int = 640): Bitmap {
        val hints = mapOf<EncodeHintType, Any>(EncodeHintType.MARGIN to 1, EncodeHintType.CHARACTER_SET to "UTF-8")
        val matrix = QRCodeWriter().encode(content, BarcodeFormat.QR_CODE, size, size, hints)
        val bitmap = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        for (x in 0 until size) {
            for (y in 0 until size) {
                bitmap.setPixel(x, y, if (matrix[x, y]) Color.BLACK else Color.WHITE)
            }
        }
        return bitmap
    }

    // ---------------- 课程 ----------------

    private fun loadCourses() {
        LogBus.add("拉取课程列表…")
        lifecycleScope.launch {
            try {
                val rows = withContext(Dispatchers.IO) { api.courses() }
                if (rows.isEmpty()) {
                    LogBus.add("没有课程（或登录已失效）")
                    return@launch
                }
                rows.forEach { row ->
                    val teacher = row.optJSONObject("teacher")?.optString("name") ?: ""
                    LogBus.add("· ${row.optString("course_name")}（${row.optString("classroom_name")}）$teacher  id=${row.optString("course_id")}")
                }
                val live = withContext(Dispatchers.IO) { api.onLesson() }
                if (live.isEmpty()) {
                    LogBus.add("当前没有正在上的课")
                } else {
                    live.forEach { LogBus.add("正在上课：lesson=${it.optString("lessonId")}") }
                }
            } catch (e: Exception) {
                LogBus.add("拉取失败：${e.message}")
            }
        }
    }
}
