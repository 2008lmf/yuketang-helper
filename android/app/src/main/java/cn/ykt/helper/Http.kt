package cn.ykt.helper

import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** 雨课堂四个站点。 */
val SERVERS = mapOf(
    "yuketang" to "https://www.yuketang.cn",
    "pro" to "https://pro.yuketang.cn",
    "changjiang" to "https://changjiang.yuketang.cn",
    "huanghe" to "https://huanghe.yuketang.cn",
)

val JSON_TYPE = "application/json; charset=utf-8".toMediaType()

class ApiException(message: String) : Exception(message)

/** Cookie 存成 {"name":"value"} 的 JSON，落在 SharedPreferences 里。 */
class PrefsCookieJar(private val store: Store) : CookieJar {
    private fun load(): MutableMap<String, String> {
        val map = mutableMapOf<String, String>()
        val raw = store.cookies
        if (raw.isNotBlank()) {
            runCatching {
                val obj = JSONObject(raw)
                obj.keys().forEach { key -> map[key] = obj.optString(key) }
            }
        }
        return map
    }

    private fun dump(map: Map<String, String>) {
        val obj = JSONObject()
        map.forEach { (k, v) -> obj.put(k, v) }
        store.cookies = obj.toString()
    }

    override fun loadForRequest(url: HttpUrl): List<Cookie> =
        load().map { (name, value) ->
            Cookie.Builder().name(name).value(value).domain(url.host).path("/").build()
        }

    override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
        val map = load()
        cookies.forEach { map[it.name] = it.value }
        dump(map)
    }
}

/** 与移动端一致的请求头。 */
class YktHttp(private val store: Store) {
    val client: OkHttpClient = OkHttpClient.Builder()
        .cookieJar(PrefsCookieJar(store))
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(40, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .build()

    val baseUrl: String get() = SERVERS[store.server] ?: SERVERS.getValue("yuketang")

    var bearer: String? = null
    var lessonToken: String? = null

    private fun cookie(name: String): String {
        val raw = store.cookies
        if (raw.isBlank()) return ""
        return runCatching { JSONObject(raw).optString(name) }.getOrDefault("")
    }

    fun headers(): Map<String, String> = buildMap {
        put("user-agent", "Android")
        put("brand", "google Pixel 9 Pro")
        put("buildnumber", "1610")
        put("xtua", "client=app&tag=1.3.3&platform=Android")
        put("systemversion", "16")
        put("incremental", "14624737")
        put("accept", "application/json")
        put("isphysicaldevice", "true")
        put("xtbz", "ykt")
        put("x-client", "app")
        cookie("csrftoken").takeIf { it.isNotBlank() }?.let { put("x-csrftoken", it) }
        cookie("sessionid").takeIf { it.isNotBlank() }?.let { put("sessionid", it) }
        store.uid.takeIf { it.isNotBlank() }?.let { put("x-uid", it) }
    }

    fun request(method: String, path: String, body: JSONObject? = null, auth: Boolean = false): JSONObject =
        send(method, path, body, auth).first

    /** 与 request 相同，但把响应头也返回（进课堂需要 set-auth）。 */
    fun send(method: String, path: String, body: JSONObject? = null, auth: Boolean = false): Pair<JSONObject, Map<String, String>> {
        val url = if (path.startsWith("http")) path else baseUrl + path
        val builder = Request.Builder().url(url)
        headers().forEach { (k, v) -> builder.header(k, v) }
        if (auth) {
            val token = bearer ?: throw ApiException("缺少 Bearer 令牌，请先进入课堂")
            builder.header("authorization", "Bearer $token")
        }
        val verb = method.uppercase()
        val request = if (verb == "GET") {
            builder.get().build()
        } else {
            builder.method(verb, (body ?: JSONObject()).toString().toRequestBody(JSON_TYPE)).build()
        }
        client.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (response.code == 401) throw ApiException("未登录或登录已过期")
            if (response.code >= 400) throw ApiException("HTTP ${response.code}")
            val out = if (text.isBlank()) JSONObject() else runCatching { JSONObject(text) }.getOrElse { throw ApiException("响应不是 JSON") }
            val hs = mutableMapOf<String, String>()
            response.headers.names().forEach { name -> hs[name.lowercase()] = response.header(name).orEmpty() }
            return out to hs
        }
    }
}
