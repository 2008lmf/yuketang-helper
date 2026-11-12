package cn.ykt.helper

import android.content.Context

/** 极简持久化：Cookie、uid、站点、开关。 */
class Store(context: Context) {
    private val prefs = context.getSharedPreferences("ykt", Context.MODE_PRIVATE)

    var cookies: String
        get() = prefs.getString("cookies", "") ?: ""
        set(value) = prefs.edit().putString("cookies", value).apply()

    var uid: String
        get() = prefs.getString("uid", "") ?: ""
        set(value) = prefs.edit().putString("uid", value).apply()

    var userName: String
        get() = prefs.getString("userName", "") ?: ""
        set(value) = prefs.edit().putString("userName", value).apply()

    var server: String
        get() = prefs.getString("server", "yuketang") ?: "yuketang"
        set(value) = prefs.edit().putString("server", value).apply()

    var autoAnswer: Boolean
        get() = prefs.getBoolean("autoAnswer", true)
        set(value) = prefs.edit().putBoolean("autoAnswer", value).apply()

    var autoLocation: Boolean
        get() = prefs.getBoolean("autoLocation", true)
        set(value) = prefs.edit().putBoolean("autoLocation", value).apply()

    var fallbackSingle: String
        get() = prefs.getString("fallbackSingle", "A") ?: "A"
        set(value) = prefs.edit().putString("fallbackSingle", value).apply()

    var delayRatio: Float
        get() = prefs.getFloat("delayRatio", 0.6f)
        set(value) = prefs.edit().putFloat("delayRatio", value).apply()

    var pollInterval: Int
        get() = prefs.getInt("pollInterval", 30)
        set(value) = prefs.edit().putInt("pollInterval", value).apply()

    fun clearLogin() {
        cookies = ""
        uid = ""
        userName = ""
    }
}
