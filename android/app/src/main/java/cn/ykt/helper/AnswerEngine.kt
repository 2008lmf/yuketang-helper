package cn.ykt.helper

import org.json.JSONArray
import org.json.JSONObject

/** 答题构造：参考答案 → 兜底。题型 1单选 2多选 3投票 4填空 5主观 6判断。 */
object AnswerEngine {

    fun typeName(type: Int): String = when (type) {
        1 -> "单选"
        2 -> "多选"
        3 -> "投票"
        4 -> "填空"
        5 -> "主观"
        6 -> "判断"
        else -> "未知($type)"
    }

    fun build(problem: JSONObject, fallbackSingle: String): Pair<Any, String> {
        val type = problem.optInt("problemType", 0)
        val known = problem.optJSONArray("answers")

        if (type == 5) {
            val text = if (known != null && known.length() > 0) known.optString(0) else ""
            val payload = JSONObject()
                .put("content", text)
                .put("pics", JSONArray())
                .put("videos", JSONArray())
            return payload to "主观·文本"
        }

        val keys = optionKeys(problem)
        if (type == 4) {
            val blanks = problem.optJSONArray("blanks")
            val out = JSONArray()
            if (blanks != null && blanks.length() > 0) {
                for (i in 0 until blanks.length()) {
                    val answers = blanks.optJSONObject(i)?.optJSONArray("answers")
                    out.put(if (answers != null && answers.length() > 0) answers.optString(0) else "")
                }
            } else {
                out.put("")
            }
            return out to "填空·课件答案"
        }

        val mapped = mapToKeys(known, problem)
        if (mapped.isNotEmpty()) return JSONArray(mapped) to "参考答案"
        if (type == 2) return JSONArray(keys) to "多选·兜底"
        val single = fallbackSingle.ifBlank { "A" }
        return JSONArray(listOf(single)) to "单选·兜底"
    }

    private fun optionKeys(problem: JSONObject): List<String> {
        val options = problem.optJSONArray("options") ?: return emptyList()
        return (0 until options.length()).mapNotNull { options.optJSONObject(it)?.optString("key") }
    }

    private fun mapToKeys(values: JSONArray?, problem: JSONObject): List<String> {
        if (values == null || values.length() == 0) return emptyList()
        val options = problem.optJSONArray("options")
        val ordered = optionKeys(problem)
        val byText = mutableMapOf<String, String>()
        if (options != null) {
            for (i in 0 until options.length()) {
                val option = options.optJSONObject(i) ?: continue
                byText[option.optString("value").trim()] = option.optString("key")
            }
        }
        val out = mutableListOf<String>()
        for (i in 0 until values.length()) {
            val raw = values.optString(i).trim()
            if (raw.isEmpty()) continue
            val upper = raw.uppercase()
            when {
                ordered.any { it.equals(raw, ignoreCase = true) } -> out.add(upper)
                raw.toIntOrNull() != null && raw.toInt() in 1..ordered.size -> out.add(ordered[raw.toInt() - 1])
                byText.containsKey(raw) -> out.add(byText.getValue(raw))
            }
        }
        return out
    }

    /** 限时题延期比例：按 limit 的 ratio 提交，最多 60 秒。 */
    fun submitDelayMillis(limitSeconds: Int, ratio: Float): Long {
        if (limitSeconds <= 0) return 0L
        val cap = 60.0
        val seconds = (limitSeconds.toDouble() * ratio.coerceIn(0f, 1f).toDouble()).coerceAtMost(cap)
        return (seconds * 1000).toLong()
    }
}
