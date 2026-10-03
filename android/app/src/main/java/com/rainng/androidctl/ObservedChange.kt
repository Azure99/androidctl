package com.rainng.androidctl

import org.json.JSONObject

internal object ObservedChange {
    fun compare(
        before: JSONObject?,
        after: JSONObject?,
    ): JSONObject {
        fun unknown(reason: String) = json("state" to "unknown", "reason" to reason)
        if (before == null || after == null) return unknown("No before or final observation")
        if (before.isNull("connection") || before.opt("connection") != after.opt("connection")) {
            return unknown("Connection changed or unknown")
        }
        if (listOf("window", "package").any { before.getJSONObject("scope").opt(it) != after.getJSONObject("scope").opt(it) }) {
            return unknown("Observation scope changed")
        }
        if (before.getBoolean("partial") || after.getBoolean("partial")) return unknown("Observation incomplete")
        return json("state" to if (UiSnapshot.signature(before) == UiSnapshot.signature(after)) "unchanged" else "changed")
    }
}

internal fun windowAbsent(
    requested: Int?,
    before: JSONObject?,
    sent: Boolean,
    connection: Int,
    ids: List<Int>,
): Boolean {
    val wasPresent = before?.optJSONArray("windows")?.objects()?.any { it.optInt("id", -1) == requested } == true
    return sent && requested != null && wasPresent && before.optInt("connection", -1) == connection && requested !in ids
}
