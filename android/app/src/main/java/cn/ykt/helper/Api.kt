package cn.ykt.helper

import org.json.JSONArray
import org.json.JSONObject

/** 雨课堂接口封装，路径与字段见 docs/PROTOCOL.md。 */
class YktApi(private val http: YktHttp) {

    private fun unwrap(data: JSONObject, path: String): JSONObject {
        val code = data.optInt("code", 0)
        if (code != 0) {
            val msg = data.optString("msg").ifBlank { "code=$code" }
            throw ApiException("$msg（$path）")
        }
        return data.optJSONObject("data") ?: JSONObject()
    }

    fun preInfo(): JSONObject = unwrap(http.request("GET", "/api/v3/user/login/pre-info"), "pre-info")

    fun loginByQr(token: String): JSONObject {
        val resp = http.request("POST", "/api/v3/user/login", JSONObject().put("token", token))
        if (resp.optInt("code", -1) != 0) {
            throw ApiException(resp.optString("msg").ifBlank { "扫码未完成" })
        }
        return resp.optJSONObject("data") ?: JSONObject()
    }

    fun userInfo(): JSONObject {
        val resp = http.request("GET", "/v/course_meta/user_info")
        return resp.optJSONObject("data")?.optJSONObject("user_profile")
            ?: throw ApiException("取不到用户资料，Cookie 可能已失效")
    }

    fun courses(): List<JSONObject> {
        val arr = http.request("GET", "/v/course_meta/learning_list/").optJSONArray("data") ?: JSONArray()
        return (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }
    }

    fun onLesson(): List<JSONObject> {
        val resp = http.request("GET", "/api/v3/classroom/on-lesson")
        if (resp.optInt("code", 0) != 0) return emptyList()
        val arr = resp.optJSONObject("data")?.optJSONArray("onLessonClassrooms") ?: JSONArray()
        return (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }
    }

    /** 进入课堂，成功后 http.bearer / http.lessonToken 就绪。 */
    fun checkin(lessonId: String): JSONObject {
        val body = JSONObject()
            .put("source", 21)
            .put("lessonId", lessonId)
            .put("joinIfNotIn", true)
        val (resp, headers) = http.send("POST", "/api/v3/lesson/checkin", body)
        val code = resp.optInt("code", -1)
        if (code != 0) throw ApiException(resp.optString("msg").ifBlank { "进课堂失败 code=$code" })
        headers["set-auth"]?.takeIf { it.isNotBlank() }?.let { http.bearer = it }
        http.lessonToken = resp.optJSONObject("data")?.optString("lessonToken")
        return resp
    }

    fun locationEligibility(): JSONObject =
        http.request("GET", "/api/v3/lesson/location-checkin/eligibility", auth = true)

    fun locationSubmit(lessonId: String, lat: Double, lng: Double, address: String): JSONObject {
        val body = JSONObject()
            .put("lat", lat)
            .put("lng", lng)
            .put("lessonId", lessonId)
            .put("source", 54)
            .put("address", address)
        return http.request("POST", "/api/v3/lesson/location-checkin/submit", body, auth = true)
    }

    fun presentation(presentationId: String): JSONObject =
        http.request("GET", "/api/v3/lesson/presentation/fetch?presentation_id=$presentationId", auth = true)
            .optJSONObject("data") ?: JSONObject()

    fun answer(problemId: String, problemType: Int, result: Any, dt: Long): JSONObject {
        val body = JSONObject()
            .put("problemId", problemId)
            .put("dt", dt)
            .put("problemType", problemType)
            .put("result", result)
        return http.request("POST", "/api/v3/lesson/problem/answer", body, auth = true)
    }
}
