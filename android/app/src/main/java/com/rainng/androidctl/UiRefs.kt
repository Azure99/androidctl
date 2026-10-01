package com.rainng.androidctl

import org.json.JSONObject

internal class UiRefs {
    private var next = 1L

    @Suppress("CyclomaticComplexMethod") // Ref retention requires complete identities and unique sources without clipped expansion.
    fun replace(
        raw: JSONObject,
        previous: Map<String, JSONObject>,
    ): Map<String, JSONObject> {
        val nodes = raw.getJSONArray("nodes").objects()
        val old = previous.entries.groupBy { UiSemantics.identity(it.value) }
        val clipped = raw.getJSONArray("missing").objects().any { it.text("reason") == "repeatedSource" }
        val addresses = nodes.groupingBy { it.opt("windowId") to it.opt("sourceId") }.eachCount()

        fun unique(node: JSONObject) = !clipped && addresses[node.opt("windowId") to node.opt("sourceId")] == 1
        val aliases =
            nodes.associate { node ->
                val key = UiSemantics.identity(node)
                val retained = if (key != null && unique(node)) old[key]?.singleOrNull()?.key else null
                node.getString("ref") to (retained ?: "e${next++}")
            }
        val usable = linkedMapOf<String, JSONObject>()
        for (node in nodes) {
            val original = node.getString("ref")
            val key = UiSemantics.identity(node)
            node.put("ref", aliases.getValue(original))
            if (!node.isNull("parent")) node.put("parent", aliases[node.getString("parent")])
            val view = node.getJSONObject("view")
            if (!view.isNull("selectionTarget")) view.put("selectionTarget", aliases[view.getString("selectionTarget")])
            val available = key != null && unique(node)
            node.put("refAvailable", available)
            node.put(
                "refReason",
                when {
                    available -> JSONObject.NULL
                    clipped -> "Repeated source expansion was stopped; uniqueness is unknown"
                    key == null -> "Selection identity is incomplete"
                    else -> "Selection identity is not unique"
                },
            )
            if (available) usable[original] = node
        }
        raw
            .getJSONArray("missing")
            .objects()
            .filter { it.has("ref") }
            .forEach { it.put("ref", aliases[it.getString("ref")]) }
        return usable
    }
}
