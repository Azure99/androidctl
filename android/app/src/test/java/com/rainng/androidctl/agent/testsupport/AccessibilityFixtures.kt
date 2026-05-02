package com.rainng.androidctl.agent.testsupport

import android.accessibilityservice.AccessibilityService
import android.content.res.Resources
import android.os.Bundle
import android.util.DisplayMetrics
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import com.rainng.androidctl.agent.actions.ActionException
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.snapshot.NodeFingerprint
import com.rainng.androidctl.agent.snapshot.NodePath
import com.rainng.androidctl.agent.snapshot.SnapshotNodeHandle
import com.rainng.androidctl.agent.snapshot.SnapshotRecord
import org.junit.Assert.assertEquals
import org.mockito.ArgumentMatchers.any
import org.mockito.ArgumentMatchers.anyInt
import org.mockito.Mockito.doAnswer
import org.mockito.Mockito.doReturn
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

internal fun mockService(): AccessibilityService {
    val service = mock(AccessibilityService::class.java)
    val resources = mock(Resources::class.java)
    val metrics =
        DisplayMetrics().apply {
            widthPixels = 1080
            heightPixels = 2400
        }
    `when`(resources.displayMetrics).thenReturn(metrics)
    `when`(service.resources).thenReturn(resources)
    return service
}

internal fun mockWindow(
    id: Int,
    root: AccessibilityNodeInfo?,
): AccessibilityWindowInfo {
    val window = mock(AccessibilityWindowInfo::class.java)
    doReturn(id).`when`(window).id
    doReturn(root).`when`(window).root
    return window
}

internal fun mockNode(
    packageName: String = "com.android.settings",
    className: String = "android.widget.Button",
    resourceId: String? = "android:id/button1",
    editable: Boolean = false,
    focused: Boolean = false,
    text: String? = "",
    textProvider: (() -> CharSequence?)? = null,
    children: List<AccessibilityNodeInfo> = emptyList(),
    actionResults: Map<Int, Boolean> = emptyMap(),
    actionHandler: ((Int, Bundle?) -> Boolean)? = null,
    refreshHandler: (() -> Boolean)? = null,
): AccessibilityNodeInfo {
    val node = mock(AccessibilityNodeInfo::class.java)
    `when`(node.packageName).thenReturn(packageName)
    `when`(node.className).thenReturn(className)
    `when`(node.viewIdResourceName).thenReturn(resourceId)
    `when`(node.isEditable).thenReturn(editable)
    `when`(node.isFocused).thenReturn(focused)
    doAnswer {
        textProvider?.invoke() ?: text
    }.`when`(node).text
    `when`(node.childCount).thenReturn(children.size)
    children.forEachIndexed { index, child ->
        `when`(node.getChild(index)).thenReturn(child)
    }
    val handler = actionHandler ?: { actionId: Int, _: Bundle? -> actionResults[actionId] ?: false }
    doAnswer { invocation ->
        handler(invocation.getArgument(0), null)
    }.`when`(node).performAction(anyInt())
    doAnswer { invocation ->
        handler(invocation.getArgument(0), invocation.getArgument(1))
    }.`when`(node).performAction(anyInt(), any(Bundle::class.java))
    doAnswer {
        refreshHandler?.invoke() ?: true
    }.`when`(node).refresh()
    return node
}

internal fun snapshotRecord(
    snapshotId: Long,
    rid: String? = null,
    path: NodePath = NodePath(windowId = "w1", childIndices = emptyList()),
    fingerprint: NodeFingerprint = nodeFingerprint(),
): SnapshotRecord =
    SnapshotRecord(
        snapshotId = snapshotId,
        ridToHandle =
            if (rid == null) {
                emptyMap()
            } else {
                mapOf(rid to SnapshotNodeHandle(path = path, fingerprint = fingerprint))
            },
    )

internal fun nodeFingerprint(windowId: String = "w1"): NodeFingerprint =
    NodeFingerprint(
        windowId = windowId,
        packageName = "com.android.settings",
        className = "android.widget.Button",
        resourceId = "android:id/button1",
    )

internal fun assertActionException(
    expectedCode: RpcErrorCode,
    expectedMessage: String,
    expectedRetryable: Boolean,
    block: () -> Unit,
) {
    try {
        block()
    } catch (error: ActionException) {
        assertEquals(expectedCode, error.code)
        assertEquals(expectedMessage, error.message)
        assertEquals(expectedRetryable, error.retryable)
        return
    }

    throw AssertionError("expected ActionException")
}
