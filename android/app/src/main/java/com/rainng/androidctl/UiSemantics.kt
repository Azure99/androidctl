package com.rainng.androidctl

import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

internal enum class Role(
    val wire: String,
) {
    TEXTBOX("textbox"),
    CHECKBOX("checkbox"),
    RADIO("radio"),
    SWITCH("switch"),
    BUTTON("button"),
    LIST("list"),
    ITEM("item"),
    SCROLL("scroll"),
    IMAGE("image"),
    TEXT("text"),
}

internal data class SelectionProjection(
    val view: JSONObject,
    val nameSource: String?,
)

internal object UiSemantics {
    private const val MAX_CONTEXT_NODES = 64
    private const val MAX_CONTEXT_DEPTH = 8
    private val actionNames =
        UiAction.entries.associate { it.platformId to it.platformName } +
            mapOf(
                AccessibilityNodeInfo.ACTION_CLEAR_FOCUS to "clear-focus",
                AccessibilityNodeInfo.ACTION_SELECT to "select",
                AccessibilityNodeInfo.ACTION_CLEAR_SELECTION to "clear-selection",
                AccessibilityNodeInfo.ACTION_COPY to "copy",
                AccessibilityNodeInfo.ACTION_CUT to "cut",
                AccessibilityNodeInfo.ACTION_PASTE to "paste",
                AccessibilityNodeInfo.ACTION_SET_SELECTION to "set-selection",
                android.R.id.accessibilityActionPageUp to "page-up",
                android.R.id.accessibilityActionPageDown to "page-down",
                android.R.id.accessibilityActionPageLeft to "page-left",
                android.R.id.accessibilityActionPageRight to "page-right",
            )

    fun actionName(id: Int): String = actionNames[id] ?: "action-${id.toString(HEX)}"

    fun roleComplete(node: JSONObject): Boolean = !node.unavailable("className")

    fun independent(node: JSONObject): Boolean =
        listOf("editable", "checkable", "clickable", "longClickable", "scrollable").any { node.optBoolean(it) }

    fun role(node: JSONObject): Role =
        when {
            node.optBoolean("editable") -> Role.TEXTBOX
            node.optBoolean("checkable") -> checkRole(node.text("className"))
            node.text("className").endsWith("Button") -> Role.BUTTON
            !node.isNull("collectionInfo") -> Role.LIST
            !node.isNull("collectionItemInfo") -> Role.ITEM
            node.optBoolean("scrollable") -> Role.SCROLL
            node.text("className").endsWith("ImageView") -> Role.IMAGE
            node.optBoolean("clickable") || node.text("className") == "android.webkit.WebView" -> Role.ITEM
            else -> Role.TEXT
        }

    private fun checkRole(type: String): Role =
        when {
            type.endsWith("Switch") -> Role.SWITCH
            type.endsWith("RadioButton") -> Role.RADIO
            else -> Role.CHECKBOX
        }

    fun selection(
        root: JSONObject,
        children: (JSONObject) -> Sequence<JSONObject>,
        consumed: MutableSet<String> = mutableSetOf(),
        nameOnly: Boolean = false,
    ): SelectionProjection {
        val context = Selection(root, children, consumed, nameOnly)
        val role = role(root)
        if (role == Role.TEXTBOX) context.own(root, listOf("description", "hint")) else context.own(root, listOf("text", "description"))
        if (context.values.isEmpty() && independent(root) && role !in listOf(Role.TEXTBOX, Role.LIST, Role.SCROLL)) context.visit(root, 0)
        return SelectionProjection(
            view =
                json(
                    "role" to role.wire,
                    "name" to context.values.firstOrNull().orEmpty(),
                    "detail" to context.values.drop(1).joinToString(" | "),
                    "nameComplete" to context.nameComplete,
                    "detailComplete" to context.detailComplete,
                ),
            nameSource = context.nameSource,
        )
    }

    private class Selection(
        val root: JSONObject,
        val children: (JSONObject) -> Sequence<JSONObject>,
        val consumed: MutableSet<String>,
        val nameOnly: Boolean,
    ) {
        val values = mutableListOf<String>()
        var nameComplete = true
        var detailComplete = true
        var nameSource: String? = null
        private var visited = 1

        fun missing() {
            detailComplete = false
            if (values.isEmpty()) nameComplete = false
        }

        fun own(
            node: JSONObject,
            fields: List<String>,
            consume: Boolean = true,
        ) {
            for (field in fields) {
                if (nameOnly && values.isNotEmpty()) return
                if (node.unavailable(field)) {
                    missing()
                }
                val value = node.text(field)
                if (value.isNotBlank() && value !in values) {
                    if (values.isEmpty()) nameSource = node.getString("ref")
                    values.add(value)
                    if (consume && node !== root && root.optBoolean("visible")) consumed.add(node.getString("ref"))
                }
            }
        }

        fun visit(
            node: JSONObject,
            depth: Int,
            consume: Boolean = true,
        ) {
            if (nameOnly && values.isNotEmpty()) return
            if (depth > MAX_CONTEXT_DEPTH) {
                missing()
                return
            }
            for (child in children(node)) {
                if (gapBefore(node, child)) missing()
                if (!visitChild(child, depth, consume)) break
            }
            if (node.optBoolean("childrenIncomplete")) missing()
        }

        private fun visitChild(
            child: JSONObject,
            depth: Int,
            consume: Boolean,
        ): Boolean {
            val hidden = !child.getBoolean("visible")
            if (hidden || independent(child) || namedAction(child)) return true
            if (child.opt("windowId") == root.opt("windowId")) {
                if (++visited > MAX_CONTEXT_NODES) {
                    missing()
                    return false
                }
                val withinItem = consume && child.isNull("collectionItemInfo")
                own(child, listOf("text", "description"), withinItem)
                visit(child, depth + 1, withinItem)
            }
            return !nameOnly || values.isEmpty()
        }

        private fun namedAction(node: JSONObject): Boolean {
            val actions = node.getJSONArray("actions")
            return (0 until actions.length()).any { actions.getString(it) in listOf("expand", "collapse", "dismiss") }
        }
    }

    @Suppress("CyclomaticComplexMethod") // Every collection ancestor and the path to the root are identity evidence.
    fun row(
        root: JSONObject,
        parent: (JSONObject) -> JSONObject?,
        children: (JSONObject) -> Sequence<JSONObject>,
    ): JSONObject {
        var current: JSONObject? = root
        val chain = JSONArray()
        var nearest: JSONObject? = null
        var label: String? = null
        val seen = mutableSetOf<String>()
        while (current != null) {
            val node = current
            val item = node.optJSONObject("collectionItemInfo")
            if (!seen.add(node.text("sourceId").ifEmpty { node.getString("ref") })) {
                return json("complete" to false)
            }
            if (item != null) {
                val indices = json("row" to item.getInt("rowIndex"), "column" to item.getInt("columnIndex"))
                chain.put(indices)
            }
            if (item != null && nearest == null) {
                nearest = chain.getJSONObject(chain.length() - 1)
                val result = nearestRowLabel(node, children)
                if (!result.getBoolean("complete")) return json("complete" to false)
                label = result.text("label").takeIf { it.isNotBlank() }
            }
            current = parent(node)
            if (current == null && !node.isNull("parent")) return json("complete" to false)
        }
        return json("complete" to true, "nearest" to nearest, "label" to label, "chain" to chain)
    }

    private fun nearestRowLabel(
        node: JSONObject,
        children: (JSONObject) -> Sequence<JSONObject>,
    ): JSONObject {
        val label = RowLabel(node, children)
        label.visit(node)
        if (!label.complete || label.value != null) return json("complete" to label.complete, "label" to label.value)
        val own = selection(node, children, nameOnly = true).view
        if (!own.getBoolean("nameComplete") || own.text("name").isNotBlank()) {
            return json("complete" to own.getBoolean("nameComplete"), "label" to own.text("name"))
        }
        val description = RowDescription(node, children)
        description.visit(node, 0)
        return json("complete" to description.complete, "label" to description.value)
    }

    private class RowLabel(
        val root: JSONObject,
        val children: (JSONObject) -> Sequence<JSONObject>,
    ) {
        var value: String? = null
        var complete = true
        private var visited = 0

        @Suppress("CyclomaticComplexMethod") // Stop at the first mechanically attributable label, never guess past a gap.
        fun visit(node: JSONObject) {
            if (++visited > MAX_CONTEXT_NODES) {
                complete = false
                return
            }
            if (excluded(node)) return
            if (node.unavailable("className") ||
                (node.optBoolean("visible") && node.unavailable("text"))
            ) {
                complete = false
            }
            if (node.optBoolean("visible") && node.text("text").isNotBlank()) {
                value = node.text("text")
                return
            }
            if (!complete || nestedItem(node)) return
            for (child in children(node)) {
                if (gapBefore(node, child)) {
                    complete = false
                } else {
                    visit(child)
                }
                if (value != null || !complete) break
            }
            if (value == null && node.optBoolean("childrenIncomplete")) complete = false
        }

        private fun excluded(node: JSONObject): Boolean =
            node.optBoolean("editable") ||
                node.optBoolean("checkable") ||
                node.optBoolean("password") ||
                (!node.unavailable("className") && node.text("className").endsWith("Button"))

        private fun nestedItem(node: JSONObject): Boolean = node !== root && !node.isNull("collectionItemInfo")
    }

    private class RowDescription(
        val root: JSONObject,
        val children: (JSONObject) -> Sequence<JSONObject>,
    ) {
        var value: String? = null
        var complete = true
        private var visited = 0

        @Suppress("CyclomaticComplexMethod") // Ordered evidence stops at the first value or unreadable boundary.
        fun visit(
            node: JSONObject,
            depth: Int,
        ) {
            if (value != null || !complete) return
            if (++visited > MAX_CONTEXT_NODES || depth > MAX_CONTEXT_DEPTH) {
                complete = false
                return
            }
            if (outside(node)) return
            if (node.optBoolean("visible") && node.unavailable("description")) {
                complete = false
                return
            }
            if (node.optBoolean("visible") && node.text("description").isNotBlank()) {
                value = node.text("description")
                return
            }
            for (child in children(node)) {
                if (gapBefore(node, child)) {
                    complete = false
                } else {
                    visit(child, depth + 1)
                }
                if (value != null || !complete) break
            }
            if (value == null && node.optBoolean("childrenIncomplete")) complete = false
        }

        private fun outside(node: JSONObject): Boolean =
            node !== root && (!node.isNull("collectionItemInfo") || node.opt("windowId") != root.opt("windowId"))
    }

    fun gapBefore(
        parent: JSONObject,
        child: JSONObject,
    ): Boolean = parent.optBoolean("childrenIncomplete") && child.optInt("childIndex", 0) >= parent.optInt("missingChildIndex", 0)

    fun identity(node: JSONObject): SelectionIdentity? {
        val view = node.getJSONObject("view")
        val row = node.getJSONObject("row")
        if (node.isNull("sourceId")) return null
        if (!roleComplete(node) || !view.getBoolean("nameComplete") || !row.getBoolean("complete")) return null
        val positions = row.getJSONArray("chain").objects()
        return SelectionIdentity(
            node.getString("sourceId"),
            node.getInt("windowId"),
            view.getString("role"),
            view.getString("name"),
            if (row.isNull("label")) null else row.getString("label"),
            positions.map { RowPosition(it.getInt("row"), it.getInt("column")) },
        )
    }

    fun project(nodes: List<JSONObject>) {
        val byRef = nodes.associateBy { it.getString("ref") }
        val children = nodes.groupBy { it.text("parent") }
        val consumed = mutableSetOf<String>()
        val nameSources = mutableMapOf<String, String?>()
        for (node in nodes) {
            val projection = selection(node, { children[it.getString("ref")].orEmpty().asSequence() }, consumed)
            node.put("view", projection.view)
            nameSources[node.getString("ref")] = projection.nameSource
            node.put("row", row(node, { byRef[it.text("parent")] }, { children[it.getString("ref")].orEmpty().asSequence() }))
        }
        for (node in nodes) {
            node.getJSONObject("view").put("nameConsumed", node.getString("ref") in consumed)
            node.getJSONObject("view").put("selectionTarget", JSONObject.NULL)
            if (node.text("className") == "android.webkit.WebView") webContent(node, children)
        }
        for (parent in nodes) {
            val view = parent.getJSONObject("view")
            nameSources[parent.getString("ref")]?.let { byRef[it] }?.let { source ->
                if (view.getBoolean("nameComplete") && source !== parent && absorbs(parent, source, byRef)) {
                    source.getJSONObject("view").put("selectionTarget", parent.getString("ref"))
                }
            }
        }
    }

    private fun absorbs(
        parent: JSONObject,
        source: JSONObject,
        byRef: Map<String, JSONObject>,
    ): Boolean {
        var current: JSONObject? = source
        while (current != null && current !== parent) {
            if (independent(current) || !current.isNull("collectionItemInfo")) {
                return false
            }
            if (current.opt("windowId") != parent.opt("windowId")) return false
            current = byRef[current.text("parent")]
        }
        return current === parent
    }

    private fun webContent(
        root: JSONObject,
        children: Map<String, List<JSONObject>>,
    ) {
        var complete = true
        var readable = false
        val pending = java.util.ArrayDeque(children[root.getString("ref")].orEmpty())
        while (pending.isNotEmpty()) {
            val node = pending.removeFirst()
            if (node.optBoolean("childrenIncomplete") || node.unavailable("text", "description")) complete = false
            if (node.text("text").isNotBlank() || node.text("description").isNotBlank()) readable = true
            pending.addAll(children[node.getString("ref")].orEmpty())
        }
        root.getJSONObject("view").put("webEmpty", complete && !root.optBoolean("childrenIncomplete") && !readable)
    }

    fun loading(nodes: List<JSONObject>): Boolean =
        nodes.any {
            it.optBoolean("visible") &&
                it.text("className") == "android.widget.ProgressBar" &&
                (it.isNull("rangeInfo") || it.getJSONObject("rangeInfo").optInt("type") == INDETERMINATE)
        }

    private const val INDETERMINATE = 3
    private const val HEX = 16
}
