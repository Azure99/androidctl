package com.rainng.androidctl

import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import java.io.Closeable

internal data class UiTarget(
    val handle: AccessibilityNodeInfo,
    val facts: JSONObject,
)

internal data class SnapshotAssembly(
    val tree: JSONObject,
    val targets: Map<String, UiTarget>,
    val collector: TreeCollector,
    val changed: Boolean,
)

@Suppress("DEPRECATION")
internal class UiSnapshot : Closeable {
    private val refs = UiRefs()
    private var sequence = 0L
    private var first = true
    private var collector: TreeCollector? = null
    private var prepared: UiDelivery.PreparedTree? = null
    private var targets = emptyMap<String, UiTarget>()
    var tree: JSONObject? = null
        private set

    fun assemble(collected: TreeCollector): SnapshotAssembly {
        val assembled = collected.tree
        val nodes = assembled.getJSONArray("nodes").objects()
        UiSemantics.project(nodes)
        val previous = tree
        val changed = previous != null && signature(previous) != signature(assembled)
        val replacement =
            refs.replace(assembled, targets.mapValues { it.value.facts }).entries.associate { (original, node) ->
                node.getString("ref") to UiTarget(collected.targets.getValue(original), node)
            }
        assembled.put("sequence", sequence + 1)
        coverage(nodes)
        return SnapshotAssembly(assembled, replacement, collected, changed)
    }

    fun install(assembled: SnapshotAssembly) {
        close()
        targets = assembled.targets
        collector = assembled.collector
        tree = assembled.tree
        sequence = assembled.tree.getLong("sequence")
    }

    fun deliveryTree(): UiDelivery.PreparedTree? =
        tree?.let { current ->
            prepared ?: UiDelivery.prepareTree(current).also { prepared = it }
        }

    fun notice(): String? {
        if (!first) return null
        first = false
        return "New worker: previous refs are invalid"
    }

    fun targetOrNull(ref: String): UiTarget? = targets[ref]

    fun atSource(original: JSONObject): UiTarget? =
        targets.values.firstOrNull {
            it.facts.opt("sourceId") == original.opt("sourceId") && it.facts.opt("windowId") == original.opt("windowId")
        }

    fun target(ref: String): UiTarget {
        targetOrNull(ref)?.let { return it }
        val node = tree?.getJSONArray("nodes")?.objects()?.firstOrNull { it.getString("ref") == ref }
        val reason =
            node?.let { "Ref was not usable in this observation: ${it.text("refReason")}" }
                ?: "Ref is absent from the current observation; use the attached observation if available, otherwise run snapshot"
        throw Rejected("staleRef", reason)
    }

    fun cached(): JSONObject = tree ?: throw Rejected("noSnapshot", "No current snapshot; run snapshot")

    override fun close() {
        targets = emptyMap()
        tree = null
        prepared = null
        collector?.close()
        collector = null
    }

    companion object {
        private val signatureFields = NodeFacts.observationFields + listOf("drawingOrder", "unavailable", "childrenIncomplete")

        // Settlement, image invalidation and endpoint change reporting share this structure-and-facts signature.
        fun signature(tree: JSONObject): String {
            val nodes = tree.getJSONArray("nodes").objects()
            val identities = nodes.associate { it.getString("ref") to it.opt("sourceId") }
            val values =
                nodes.map { node ->
                    JSONArray(
                        listOf(
                            node.opt("sourceId"),
                            identities[node.text("parent")],
                            node.getJSONObject("view").opt("role"),
                            node.getJSONObject("view").opt("name"),
                            node.getJSONObject("view").opt("detail"),
                            node.opt("row"),
                        ) +
                            signatureFields.map(node::opt),
                    )
                }
            val windows =
                tree.getJSONArray("windows").objects().map { window ->
                    if (window.getInt("id") == tree.getJSONObject("scope").getInt("window")) {
                        window
                    } else {
                        // Only the selected root is read under every selection strategy.
                        JSONObject().apply {
                            for (field in window.keys()) {
                                if (field != "rootAvailable" && field != "packageName") put(field, window.get(field))
                            }
                        }
                    }
                }
            return sha256(
                json(
                    "windows" to JSONArray(windows),
                    "partial" to tree.getBoolean("partial"),
                    "nodes" to JSONArray(values),
                ).toString().toByteArray(),
            )
        }

        fun coverage(nodes: List<JSONObject>) {
            val byRef = nodes.associateBy { it.getString("ref") }
            val paths = nodes.associate { node -> node.getString("ref") to path(node, byRef) }
            for (target in nodes.filter(UiSemantics::independent)) {
                val bounds = target.getJSONArray("bounds")
                val x = (bounds.getDouble(0) + bounds.getDouble(2)) / 2
                val y = (bounds.getDouble(1) + bounds.getDouble(BOTTOM)) / 2
                val own = paths.getValue(target.getString("ref"))
                val covering =
                    nodes.firstOrNull { candidate ->
                        val box = candidate.getJSONArray("bounds")
                        candidate.optBoolean("visible") &&
                            candidate.optBoolean("clickable") &&
                            candidate.opt("windowId") == target.opt("windowId") &&
                            x >= box.getDouble(0) &&
                            x < box.getDouble(2) &&
                            y >= box.getDouble(1) &&
                            y < box.getDouble(BOTTOM) &&
                            above(own, paths.getValue(candidate.getString("ref")))
                    }
                if (covering != null) target.put("covered", covering.getString("ref"))
            }
        }

        private fun path(
            node: JSONObject,
            byRef: Map<String, JSONObject>,
        ): List<JSONObject> {
            val result = mutableListOf<JSONObject>()
            var current: JSONObject? = node
            while (current != null && result.size < MAX_PATH) {
                result.add(current)
                current = byRef[current.text("parent")]
            }
            return result.reversed()
        }

        private fun above(
            own: List<JSONObject>,
            other: List<JSONObject>,
        ): Boolean {
            var index = 0
            while (index < minOf(own.size, other.size) && own[index] === other[index]) index++
            if (index == 0 || index == own.size || index == other.size) return false
            return other[index].getInt("drawingOrder") > own[index].getInt("drawingOrder")
        }

        private const val MAX_PATH = 64
        private const val BOTTOM = 3
    }
}
