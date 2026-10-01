package com.rainng.androidctl

import android.os.Build
import android.os.SystemClock
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONArray
import org.json.JSONObject
import java.io.Closeable
import java.util.ArrayDeque
import java.util.PriorityQueue

@Suppress("DEPRECATION")
internal class TreeCollector(
    private val sampled: List<AccessibilityWindowInfo>,
    private val scope: JSONObject,
    private val budget: UiBudget,
    deadline: Long = budget.deadline,
) : Closeable {
    val tree = json("startedMs" to SystemClock.elapsedRealtime())
    val targets = LinkedHashMap<String, AccessibilityNodeInfo>()
    private val nodes = mutableListOf<CollectedNode>()
    private val missing = JSONArray()
    private val expanded = mutableSetOf<Pair<Int, String>>()

    private data class Pending(
        val node: AccessibilityNodeInfo,
        val parentIndex: Int?,
        val childIndex: Int,
        val depth: Int,
        val hidden: Boolean,
        val order: Int,
    )

    private data class CollectedNode(
        val facts: JSONObject,
        val parentIndex: Int?,
    )

    private val queue = PriorityQueue(compareBy<Pending> { it.hidden }.thenBy { it.depth }.thenBy { it.order })
    private var order = 0
    private var bytes = 0
    private var discovered = 0
    private var closed = false
    private val until = minOf(deadline, budget.deadline, SystemClock.elapsedRealtime() + COLLECT_MS)

    fun collect(): JSONObject {
        budget.check()
        val visible = NodeFacts.defaultWindows(sampled)
        val windows = visible.map(NodeFacts::window)
        val selected = select(visible, windows)
        val facts = windows.single { it.getInt("id") == selected.first.id }
        val root = selected.second
        facts.put("rootAvailable", root != null).put("packageName", root?.packageName?.toString() ?: JSONObject.NULL)
        if (root == null) missing.put(json("reason" to "rootUnavailable")) else enqueue(root, null, 0, 0)
        discovered = queue.size
        while (queue.isNotEmpty() && nodes.size < Limits.NODES && budget.remaining() > 0) {
            if (!addNode()) break
        }
        if (queue.isNotEmpty()) {
            missing.put(json("reason" to "collectionLimit"))
            queue.forEach { markMissing(it.parentIndex, it.childIndex) }
        }
        return tree
            .put("selectionScope", copy(scope))
            .put("scope", json("window" to selected.first.id, "package" to facts.opt("packageName")))
            .put("windows", JSONArray(windows))
            .put("keyboard", if (visible.any { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD }) "shown" else "hidden")
            .put("nodes", structureOrder())
            .put("missing", missing)
            .put("partial", missing.length() > 0)
            .put("finishedMs", SystemClock.elapsedRealtime())
    }

    private fun enqueue(
        node: AccessibilityNodeInfo,
        parentIndex: Int?,
        index: Int,
        depth: Int,
    ) {
        val hidden = !node.isVisibleToUser
        queue.add(Pending(node, parentIndex, index, depth, hidden, order++))
    }

    private fun markMissing(
        parentIndex: Int?,
        index: Int,
    ) {
        if (parentIndex == null) return
        val facts = nodes[parentIndex].facts
        facts.put("childrenIncomplete", true)
        facts.put("missingChildIndex", minOf(index, facts.optInt("missingChildIndex", Int.MAX_VALUE)))
    }

    private fun structureOrder(): JSONArray {
        val children = nodes.withIndex().groupBy { it.value.parentIndex }
        val pending = ArrayDeque(children[null].orEmpty())
        val result = JSONArray()
        while (pending.isNotEmpty()) {
            val node = pending.removeFirst()
            result.put(node.value.facts)
            children[node.index]
                .orEmpty()
                .sortedBy { it.value.facts.getInt("childIndex") }
                .asReversed()
                .forEach(pending::addFirst)
        }
        return result
    }

    private fun select(
        windows: List<AccessibilityWindowInfo>,
        facts: List<JSONObject>,
    ): Pair<AccessibilityWindowInfo, AccessibilityNodeInfo?> {
        val roots = mutableMapOf<Int, AccessibilityNodeInfo?>()
        var retained: Int? = null
        try {
            val matches =
                windows.filter { window ->
                    when {
                        scope.has("window") -> window.id == scope.getInt("window")
                        scope.has("package") -> {
                            checkTime()
                            val root = fresh(window.root)
                            roots[window.id] = root
                            facts
                                .single { it.getInt("id") == window.id }
                                .put("rootAvailable", root != null)
                                .put("packageName", root?.packageName?.toString() ?: JSONObject.NULL)
                            root?.packageName?.toString() == scope.getString("package")
                        }
                        else -> window.isActive && window.type != AccessibilityWindowInfo.TYPE_INPUT_METHOD
                    }
                }
            if (matches.size != 1 || (scope.has("package") && facts.any { it.isNull("packageName") })) {
                val choices = facts.joinToString("; ") { "snapshot --window ${it.getInt("id")}" }
                throw Rejected(
                    "scopeUnknown",
                    "One complete task window is required; " +
                        if (choices.isEmpty()) {
                            "no windows enumerated; run snapshot when the screen is available"
                        } else {
                            "inspect a listed window: $choices"
                        },
                )
            }
            val selected = matches.single()
            checkTime()
            val root = if (roots.containsKey(selected.id)) roots[selected.id] else fresh(selected.root).also { roots[selected.id] = it }
            retained = selected.id
            return selected to root
        } finally {
            roots.filterKeys { it != retained }.values.forEach { it?.recycle() }
        }
    }

    private fun addNode(): Boolean {
        val pending = queue.remove()
        val node = pending.node
        val parentIndex = pending.parentIndex
        val childIndex = pending.childIndex
        val index = nodes.size
        val ref = "n$index"
        var retained = false
        try {
            val facts = NodeFacts.read(node, ref, parentIndex?.let { "n$it" }).put("childIndex", childIndex)
            bytes += facts.toString().toByteArray().size
            if (bytes > Limits.TREE_BYTES - TREE_OVERHEAD) {
                missing.put(json("reason" to "treeByteLimit"))
                markMissing(parentIndex, childIndex)
                return false
            }
            nodes.add(CollectedNode(facts, parentIndex))
            targets[ref] = node
            retained = true
            addChildren(node, facts, index, pending.depth)
            return true
        } finally {
            if (!retained) node.recycle()
        }
    }

    private fun addChildren(
        node: AccessibilityNodeInfo,
        facts: JSONObject,
        parentIndex: Int,
        depth: Int,
    ) {
        val count = node.childCount
        val addressKnown = !facts.isNull("sourceId")
        if (
            count > 0 &&
            addressKnown &&
            !expanded.add(facts.getInt("windowId") to facts.getString("sourceId"))
        ) {
            facts.put("childrenIncomplete", true).put("missingChildIndex", 0)
            missing.put(json("ref" to facts.getString("ref"), "reason" to "repeatedSource", "unreadChildren" to count))
            return
        }
        var attempted = 0
        var unavailable = 0
        while (attempted < count && discovered < Limits.NODES && withinTime()) {
            discovered++
            val index = attempted++
            val child = NodeAccess.child(node, index)
            if (child == null) {
                unavailable++
                markMissing(parentIndex, index)
            } else {
                enqueue(child, parentIndex, index, depth + 1)
            }
        }
        val incomplete = unavailable > 0 || attempted < count
        facts.put("childrenIncomplete", incomplete)
        if (attempted < count) markMissing(parentIndex, attempted)
        if (incomplete) {
            missing.put(
                json(
                    "ref" to facts.getString("ref"),
                    "unavailableChildren" to unavailable,
                    "unreadChildren" to count - attempted,
                ),
            )
        }
    }

    private fun withinTime() = SystemClock.elapsedRealtime() < until

    private fun checkTime() {
        budget.check()
        if (!withinTime()) throw Rejected("snapshotBudget", "Snapshot collection budget exhausted")
    }

    private fun fresh(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
        if (node != null && Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            var retained = false
            try {
                retained = node.refresh()
                if (!retained) return null
            } finally {
                if (!retained) node.recycle()
            }
        }
        return node
    }

    override fun close() {
        if (closed) return
        closed = true
        queue.forEach { it.node.recycle() }
        queue.clear()
        sampled.forEach { it.recycle() }
        targets.values.forEach { it.recycle() }
        targets.clear()
    }

    private companion object {
        const val COLLECT_MS = 1500L
        const val TREE_OVERHEAD = 512 * 1024
    }
}
