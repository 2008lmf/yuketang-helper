package cn.ykt.helper

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 进程内日志总线：服务写，界面读。 */
object LogBus {
    private const val MAX = 300
    private val lines = ArrayDeque<String>()
    private val listeners = mutableListOf<(String) -> Unit>()
    private val fmt = SimpleDateFormat("HH:mm:ss", Locale.getDefault())

    fun add(message: String) {
        val line = "${fmt.format(Date())} $message"
        val snapshot: List<(String) -> Unit>
        synchronized(this) {
            lines.addLast(line)
            while (lines.size > MAX) lines.removeFirst()
            snapshot = listeners.toList()
        }
        snapshot.forEach { runCatching { it(line) } }
    }

    fun snapshot(): List<String> = synchronized(this) { lines.toList() }

    fun register(listener: (String) -> Unit) {
        synchronized(this) { listeners.add(listener) }
    }

    fun unregister(listener: (String) -> Unit) {
        synchronized(this) { listeners.remove(listener) }
    }
}
