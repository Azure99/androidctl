package com.rainng.androidctl

import org.json.JSONObject

internal data class RowSelection(
    val row: JSONObject,
    val verificationControl: JSONObject,
)

internal object UiAdvice {
    fun suggestedRow(
        tree: JSONObject,
        target: JSONObject,
        action: UiAction,
    ): RowSelection? = rowSuggestion(tree, target, action)?.takeIf { it.verificationControl.optBoolean("refAvailable") }

    private fun rowSuggestion(
        tree: JSONObject,
        target: JSONObject,
        action: UiAction,
    ): RowSelection? {
        if (!action.changesChecked || action.ready(target)) return null
        val nodes = tree.getJSONArray("nodes").objects()
        val row = collectionRow(target, nodes.associateBy { it.getString("ref") }) ?: return null
        val check = rowCheckable(row, nodes) ?: return null
        return if (row.optBoolean("refAvailable") && UiAction.CLICK.ready(row)) {
            RowSelection(row, check)
        } else {
            null
        }
    }

    fun requireUncheckedRadio(node: JSONObject) {
        val state =
            when (node.text("checked")) {
                "false" -> return
                "true" -> "selected"
                "mixed" -> "mixed"
                else -> "unknown"
            }
        throw Rejected("radioUncheck", "Radio state is $state; it cannot be unchecked directly; explicitly choose another option")
    }

    private fun rowCheckable(
        row: JSONObject,
        nodes: List<JSONObject>,
    ): JSONObject? {
        val members = rowMembers(row, nodes) ?: return null
        return members.filter { it.optBoolean("checkable") }.singleOrNull()?.takeIf(::rowControlReady)
    }

    fun rowControlReady(check: JSONObject): Boolean = rowControlNotReadyReason(check) == null

    fun rowControlNotReadyReason(check: JSONObject): String? {
        val actions = check.getJSONArray("actions")
        return when {
            !check.getBoolean("checkable") -> "control is not checkable"
            !check.getBoolean("visible") -> "control is not visible"
            !check.getBoolean("enabled") -> "control is disabled"
            check.text("checked") !in listOf("false", "true", "mixed") -> "checked state is not false, true, or mixed"
            (0 until actions.length()).any { actions.getString(it) == "click" } -> "control now exposes the click action"
            else -> null
        }
    }

    private fun rowMembers(
        row: JSONObject,
        nodes: List<JSONObject>,
    ): List<JSONObject>? {
        val children = nodes.groupBy { it.text("parent") }
        val members = mutableListOf<JSONObject>()
        val pending = java.util.ArrayDeque(listOf(row))
        while (pending.isNotEmpty()) {
            val member = pending.removeFirst()
            val nested = !member.isNull("collectionItemInfo") || !member.isNull("collectionInfo")
            if (member !== row && nested) continue
            if (member.optBoolean("childrenIncomplete")) return null
            if (member.getBoolean("visible")) members.add(member)
            pending.addAll(children[member.getString("ref")].orEmpty())
        }
        return members
    }

    private fun collectionRow(
        node: JSONObject,
        byRef: Map<String, JSONObject>,
    ): JSONObject? {
        var current: JSONObject? = node
        while (current != null) {
            val parent = byRef[current.text("parent")]
            val item = !current.isNull("collectionItemInfo")
            if (item || (parent != null && !parent.isNull("collectionInfo"))) return current
            current = parent
        }
        return null
    }
}
