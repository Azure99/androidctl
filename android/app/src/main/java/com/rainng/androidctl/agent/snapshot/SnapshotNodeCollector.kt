package com.rainng.androidctl.agent.snapshot

import android.os.Build
import android.view.accessibility.AccessibilityNodeInfo
import androidx.annotation.RequiresApi

private const val ACCESSIBILITY_CHECKED_STATE_API_LEVEL = 36

@Suppress("DEPRECATION")
internal class SnapshotNodeCollector(
    private val actionIdProvider: (AccessibilityNodeInfo) -> List<Int>,
) {
    private data class NodeCollectionContext(
        val windowKey: String,
        val includeInvisible: Boolean,
        val nodesPayload: MutableList<SnapshotNode>,
        val ridToHandle: MutableMap<String, SnapshotNodeHandle>,
    )

    private data class NodeRecord(
        val context: NodeCollectionContext,
        val rid: String,
        val parentRid: String?,
        val childPath: List<Int>,
        val childRids: List<String>,
    )

    fun appendWindowRoot(
        node: AccessibilityNodeInfo,
        windowKey: String,
        includeInvisible: Boolean,
        state: SnapshotCollectionState,
    ): String? {
        val context =
            NodeCollectionContext(
                windowKey = windowKey,
                includeInvisible = includeInvisible,
                nodesPayload = state.nodesPayload,
                ridToHandle = state.ridToHandle,
            )
        return appendNode(
            node = node,
            context = context,
            childPath = mutableListOf(),
            parentRid = null,
        )
    }

    private fun appendNode(
        node: AccessibilityNodeInfo,
        context: NodeCollectionContext,
        childPath: MutableList<Int>,
        parentRid: String?,
    ): String? {
        val visible = node.isVisibleToUser
        if (!context.includeInvisible && !visible) {
            node.recycle()
            return null
        }

        val rid = buildRid(context.windowKey, childPath)
        val childRids = mutableListOf<String>()
        for (index in 0 until node.childCount) {
            val child = node.getChild(index) ?: continue
            childPath += index
            val childRid =
                appendNode(
                    node = child,
                    context = context,
                    childPath = childPath,
                    parentRid = rid,
                )
            childPath.removeAt(childPath.lastIndex)
            if (childRid != null) {
                childRids += childRid
            }
        }

        recordSnapshotNode(
            node = node,
            record =
                NodeRecord(
                    context = context,
                    rid = rid,
                    parentRid = parentRid,
                    childPath = childPath.toList(),
                    childRids = childRids,
                ),
        )

        node.recycle()
        return rid
    }

    private fun recordSnapshotNode(
        node: AccessibilityNodeInfo,
        record: NodeRecord,
    ) {
        val snapshotNode =
            SnapshotNode(
                rid = record.rid,
                windowId = record.context.windowKey,
                parentRid = record.parentRid,
                childRids = record.childRids,
                className = normalizeSnapshotClassName(node.className?.toString()),
                resourceId = node.viewIdResourceName,
                text = node.text?.toString(),
                contentDesc = node.contentDescription?.toString(),
                hintText = node.hintText?.toString(),
                stateDescription = node.stateDescription?.toString(),
                paneTitle = node.paneTitle?.toString(),
                packageName = node.packageName?.toString(),
                bounds = rectToList(node.boundsInScreenRect()),
                visibleToUser = node.isVisibleToUser,
                importantForAccessibility = node.isImportantForAccessibility,
                clickable = node.isClickable,
                enabled = node.isEnabled,
                editable = node.isEditable,
                focusable = node.isFocusable,
                focused = node.isFocused,
                checkable = node.isCheckable,
                checked = checkedCompat(node),
                selected = node.isSelected,
                scrollable = node.isScrollable,
                password = node.isPassword,
                actions = actionIdProvider(node).map(SnapshotProtocolTypes::actionType),
            )
        record.context.nodesPayload += snapshotNode
        record.context.ridToHandle[record.rid] =
            SnapshotNodeHandle(
                path = NodePath(windowId = record.context.windowKey, childIndices = record.childPath),
                fingerprint = NodeFingerprint.fromSnapshotNode(snapshotNode),
            )
    }

    private fun buildRid(
        windowKey: String,
        childIndices: List<Int>,
    ): String {
        val suffix =
            if (childIndices.isEmpty()) {
                "0"
            } else {
                "0." + childIndices.joinToString(".")
            }
        return "$windowKey:$suffix"
    }

    private fun checkedCompat(node: AccessibilityNodeInfo): Boolean =
        if (Build.VERSION.SDK_INT >= ACCESSIBILITY_CHECKED_STATE_API_LEVEL) {
            checkedFromApi36(node)
        } else {
            node.isChecked
        }

    @RequiresApi(ACCESSIBILITY_CHECKED_STATE_API_LEVEL)
    private fun checkedFromApi36(node: AccessibilityNodeInfo): Boolean = node.getChecked() == AccessibilityNodeInfo.CHECKED_STATE_TRUE
}
