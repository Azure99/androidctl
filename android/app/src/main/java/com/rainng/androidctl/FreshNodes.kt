package com.rainng.androidctl

import android.os.Build
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONObject
import java.io.Closeable

@Suppress("DEPRECATION")
internal class FreshNodes(
    private val budget: UiBudget,
) : Closeable {
    private val owned = mutableListOf<AccessibilityNodeInfo>()
    private val handles = mutableMapOf<String, AccessibilityNodeInfo>()
    private var sequence = 0

    fun read(
        target: AccessibilityNodeInfo,
        window: AccessibilityWindowInfo,
    ): JSONObject {
        val root = window.root ?: throw Rejected("identityUnknown", "Current window root is unavailable")
        try {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU && !root.refresh()) {
                throw Rejected("identityUnknown", "Current window root is unavailable")
            }
            val rootFacts = NodeFacts.read(root, "root", null)
            if (rootFacts.isNull("sourceId") || rootFacts.opt("windowId") != window.id) {
                throw Rejected("identityUnknown", "Current window root has an unavailable address or a different window")
            }
            return readTarget(target, rootFacts)
        } finally {
            root.recycle()
        }
    }

    private fun readTarget(
        target: AccessibilityNodeInfo,
        currentRoot: JSONObject,
    ): JSONObject {
        budget.check()
        if (!target.refresh()) throw Rejected("staleRef", "Original object is no longer available")
        val facts = NodeFacts.read(target, "target", null)
        handles["target"] = target
        facts.put("view", UiSemantics.selection(facts, ::children, nameOnly = true).view)
        facts.put(
            "row",
            UiSemantics.row(
                facts,
                { context ->
                    when {
                        context.isNull("sourceId") || context.opt("windowId") != currentRoot.opt("windowId") ->
                            throw Rejected("identityUnknown", "Selection parent chain has an unavailable address or a different window")
                        context.text("sourceId") == currentRoot.text("sourceId") -> null
                        else ->
                            NodeAccess.parent(handles.getValue(context.getString("ref")))?.let(::retain)
                                ?: throw Rejected("identityUnknown", "Selection parent chain does not reach the current window root")
                    }
                },
                ::children,
            ),
        )
        return facts
    }

    private fun retain(node: AccessibilityNodeInfo): JSONObject {
        owned.add(node)
        budget.check()
        // Newly obtained relatives may reflect a row rebind after the target's first refresh.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && !node.refresh()) {
            throw Rejected("identityUnknown", "Selection context is unavailable")
        }
        val ref = "f${++sequence}"
        handles[ref] = node
        return NodeFacts.read(node, ref, null)
    }

    private fun children(facts: JSONObject): Sequence<JSONObject> =
        sequence {
            val handle = handles.getValue(facts.getString("ref"))
            val count = handle.childCount
            for (index in 0 until count) {
                budget.check()
                yield(
                    retain(
                        NodeAccess.child(handle, index) ?: throw Rejected("identityUnknown", "Selection child is unavailable"),
                    ).put("childIndex", index),
                )
            }
        }

    override fun close() {
        owned.forEach { it.recycle() }
    }
}
