package com.rainng.androidctl

import android.os.Build
import android.os.IBinder
import android.view.accessibility.AccessibilityNodeInfo

internal object NodeAccess {
    fun child(
        node: AccessibilityNodeInfo,
        index: Int,
    ): AccessibilityNodeInfo? {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) return node.getChild(index)
        val id = HiddenApi.childId.invoke(node, index) as Long
        val leash = HiddenApi.leashedChild.get(node)
        return if (leash != null && id == HiddenApi.leashedNodeId.getLong(null)) {
            query(node, leash, HiddenApi.rootNodeId.getLong(null))
        } else {
            query(node, node.windowId, id)
        }
    }

    fun parent(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) return node.parent
        val leash = HiddenApi.leashedParent.get(node)
        val leashedId = HiddenApi.leashedParentNodeId.getLong(node)
        return if (leash != null && leashedId != HiddenApi.undefinedNodeId.getLong(null)) {
            query(node, leash, leashedId)
        } else {
            query(node, node.windowId, HiddenApi.parentId.invoke(node) as Long)
        }
    }

    private fun query(
        node: AccessibilityNodeInfo,
        window: Any,
        id: Long,
    ): AccessibilityNodeInfo? {
        val view = HiddenApi.accessibilityViewId.invoke(null, id)
        if (view == HiddenApi.undefinedItemId.getInt(null)) return null
        // Old getChild/getParent prefetch can overflow Binder before a later refresh runs.
        return (if (window is IBinder) HiddenApi.nodeByLeash else HiddenApi.nodeByWindow)
            .invoke(
                HiddenApi.clientInstance.invoke(null),
                HiddenApi.nodeConnectionId.invoke(node),
                window,
                id,
                true,
                0,
                null,
            ) as AccessibilityNodeInfo?
    }
}
