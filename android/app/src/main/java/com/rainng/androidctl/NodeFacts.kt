package com.rainng.androidctl

import android.graphics.Rect
import android.os.Build
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONArray
import org.json.JSONObject
import java.nio.ByteBuffer
import java.nio.CharBuffer
import java.nio.charset.CodingErrorAction

internal object NodeFacts {
    val observationFields =
        listOf(
            "text",
            "description",
            "showingHintText",
            "focused",
            "selected",
            "checked",
            "enabled",
            "visible",
            "bounds",
            "actions",
            "stateDescription",
            "error",
            "contentInvalid",
            "rangeInfo",
            "collectionInfo",
            "collectionItemInfo",
        )

    fun bounds(rect: Rect): JSONArray = JSONArray(listOf(rect.left, rect.top, rect.right, rect.bottom))

    fun defaultWindows(windows: List<AccessibilityWindowInfo>): List<AccessibilityWindowInfo> {
        val visible = windows.filter { it.displayId == 0 }
        if (visible.size > Limits.WINDOWS) throw Rejected("scopeUnknown", "Window inventory exceeds its limit")
        return visible
    }

    fun window(window: AccessibilityWindowInfo): JSONObject {
        val rect = Rect().also(window::getBoundsInScreen)
        val title = window.title?.let(::limitedText)
        return json(
            "id" to window.id,
            "type" to
                when (window.type) {
                    AccessibilityWindowInfo.TYPE_APPLICATION -> "application"
                    AccessibilityWindowInfo.TYPE_INPUT_METHOD -> "inputMethod"
                    AccessibilityWindowInfo.TYPE_SYSTEM -> "system"
                    AccessibilityWindowInfo.TYPE_ACCESSIBILITY_OVERLAY -> "accessibilityOverlay"
                    AccessibilityWindowInfo.TYPE_SPLIT_SCREEN_DIVIDER -> "splitScreenDivider"
                    AccessibilityWindowInfo.TYPE_MAGNIFICATION_OVERLAY -> "magnificationOverlay"
                    else -> window.type.toString()
                },
            "layer" to window.layer,
            "active" to window.isActive,
            "focused" to window.isFocused,
            "bounds" to bounds(rect),
            "title" to title?.first,
            "titleTruncated" to (title?.second == true),
        )
    }

    @Suppress("LongMethod", "CyclomaticComplexMethod") // Keep node facts in one flat, auditable projection.
    fun read(
        node: AccessibilityNodeInfo,
        ref: String,
        parent: String?,
    ): JSONObject {
        val out = json("ref" to ref, "parent" to parent)
        val unavailable = JSONArray()

        // Text conversion and source-ID reflection can fail independently of local node facts.
        fun field(
            name: String,
            read: () -> Any?,
        ) {
            try {
                out.put(name, read() ?: JSONObject.NULL)
            } catch (error: Exception) {
                out.put(name, JSONObject.NULL)
                unavailable.put(json("field" to name, "reason" to error.javaClass.simpleName))
            }
        }

        fun text(
            name: String,
            value: () -> CharSequence?,
        ) = field(name) {
            val original = value()
            val limited = original?.let(::limitedText)
            if (limited?.second == true) {
                unavailable.put(json("field" to name, "reason" to "truncated", "length" to original.length))
            }
            limited?.first
        }
        out.put("windowId", node.windowId)
        text("packageName") { node.packageName }
        text("className") { node.className }
        text("resourceId") { node.viewIdResourceName }
        text("text") { node.text }
        text("description") { node.contentDescription }
        text("hint") { node.hintText }
        text("error") { node.error }
        out.put("contentInvalid", node.isContentInvalid)
        out.put("showingHintText", node.isShowingHintText)
        out.put("enabled", node.isEnabled)
        out.put("editable", node.isEditable)
        out.put("isMultiLine", node.isMultiLine)
        out.put("clickable", node.isClickable)
        out.put("longClickable", node.isLongClickable)
        out.put("scrollable", node.isScrollable)
        out.put("visible", node.isVisibleToUser)
        out.put("focused", node.isFocused)
        out.put("password", node.isPassword)
        out.put("checkable", node.isCheckable)
        @Suppress("DEPRECATION")
        val checked =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.BAKLAVA) {
                node.checked
            } else if (node.isChecked) {
                1
            } else {
                0
            }
        out.put(
            "checked",
            when (checked) {
                0 -> "false"
                1 -> "true"
                2 -> "mixed"
                else -> checked.toString()
            },
        )
        out.put(
            "checkedModel",
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.BAKLAVA) "triState" else "binary",
        )
        out.put("selected", node.isSelected)
        text("stateDescription") { node.stateDescription }
        out.put("bounds", bounds(Rect().also(node::getBoundsInScreen)))
        out.put("actions", JSONArray(node.actionList.map { UiSemantics.actionName(it.id) }))
        out.put("drawingOrder", node.drawingOrder)
        out.put(
            "collectionInfo",
            node.collectionInfo?.let {
                json(
                    "rows" to it.rowCount,
                    "columns" to it.columnCount,
                    "hierarchical" to it.isHierarchical,
                    "selectionMode" to it.selectionMode,
                )
            } ?: JSONObject.NULL,
        )
        out.put(
            "collectionItemInfo",
            node.collectionItemInfo?.let {
                @Suppress("DEPRECATION")
                json(
                    "rowIndex" to it.rowIndex,
                    "rowSpan" to it.rowSpan,
                    "columnIndex" to it.columnIndex,
                    "columnSpan" to it.columnSpan,
                    "heading" to it.isHeading,
                    "selected" to it.isSelected,
                )
            } ?: JSONObject.NULL,
        )
        out.put(
            "rangeInfo",
            node.rangeInfo?.let { json("type" to it.type, "min" to it.min, "max" to it.max, "current" to it.current) }
                ?: JSONObject.NULL,
        )
        field("sourceId") {
            HiddenApi.sourceNodeId
                .invoke(node)
                ?.toString()
        }
        out.put("unavailable", unavailable)
        return out
    }

    private fun limitedText(
        value: CharSequence,
        limit: Int = Limits.TEXT_BYTES,
    ): Pair<String, Boolean> {
        val encoder =
            Charsets.UTF_8
                .newEncoder()
                .onMalformedInput(CodingErrorAction.REPLACE)
                .onUnmappableCharacter(CodingErrorAction.REPLACE)
        val chars = CharBuffer.wrap(value)
        val bytes =
            ByteBuffer.allocate(
                minOf(
                    limit,
                    value.length
                        .toLong()
                        .times(MAX_UTF8_BYTES_PER_CHAR)
                        .coerceAtMost(Int.MAX_VALUE.toLong())
                        .toInt(),
                ),
            )
        encoder.encode(chars, bytes, true)
        bytes.flip()
        return Charsets.UTF_8.decode(bytes).toString() to chars.hasRemaining()
    }

    private const val MAX_UTF8_BYTES_PER_CHAR = 3
}
