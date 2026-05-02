package com.rainng.androidctl.agent.snapshot

import android.view.accessibility.AccessibilityNodeInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.verify
import org.mockito.Mockito.`when`

@Suppress("DEPRECATION")
class SnapshotNodeCollectorTest {
    @Test
    fun appendWindowRootBuildsDeterministicHandles() {
        val child =
            mockNode(
                packageName = "com.example.app",
                className = "android.widget.TextView",
                resourceId = "title",
                childCount = 0,
            )
        val root =
            mockNode(
                packageName = "com.example.app",
                className = "android.widget.FrameLayout",
                resourceId = "root",
                childCount = 1,
                children = mapOf(0 to child),
            )
        val collector = SnapshotNodeCollector(actionIdProvider = { emptyList() })
        val state = SnapshotCollectionState(snapshotId = 42L)

        val rootRid =
            collector.appendWindowRoot(
                node = root,
                windowKey = "w10",
                includeInvisible = false,
                state = state,
            )

        assertEquals("w10:0", rootRid)
        assertEquals(listOf("w10:0.0", "w10:0"), state.nodesPayload.map { it.rid })
        assertNotNull(state.ridToHandle["w10:0"])
        assertEquals(NodePath(windowId = "w10", childIndices = listOf(0)), state.ridToHandle["w10:0.0"]?.path)
        verify(root).recycle()
        verify(child).recycle()
    }

    @Test
    fun appendWindowRootReturnsNullWhenRootIsInvisibleAndInvisibleNodesAreExcluded() {
        val root =
            mockNode(
                packageName = "com.example.app",
                className = "android.widget.FrameLayout",
                childCount = 0,
                visibleToUser = false,
            )
        val collector = SnapshotNodeCollector(actionIdProvider = { emptyList() })
        val state = SnapshotCollectionState(snapshotId = 7L)

        val rootRid =
            collector.appendWindowRoot(
                node = root,
                windowKey = "w22",
                includeInvisible = false,
                state = state,
            )

        assertNull(rootRid)
        assertEquals(emptyList<SnapshotNode>(), state.nodesPayload)
        verify(root).recycle()
    }

    private fun mockNode(
        packageName: String?,
        className: String?,
        resourceId: String? = null,
        childCount: Int,
        visibleToUser: Boolean = true,
        children: Map<Int, AccessibilityNodeInfo> = emptyMap(),
    ): AccessibilityNodeInfo {
        val node = mock(AccessibilityNodeInfo::class.java)
        `when`(node.packageName).thenReturn(packageName)
        `when`(node.className).thenReturn(className)
        `when`(node.viewIdResourceName).thenReturn(resourceId)
        `when`(node.childCount).thenReturn(childCount)
        `when`(node.isVisibleToUser).thenReturn(visibleToUser)
        `when`(node.actionList).thenReturn(emptyList())
        `when`(node.isImportantForAccessibility).thenReturn(true)
        `when`(node.isClickable).thenReturn(false)
        `when`(node.isEnabled).thenReturn(true)
        `when`(node.isEditable).thenReturn(false)
        `when`(node.isFocusable).thenReturn(false)
        `when`(node.isFocused).thenReturn(false)
        `when`(node.isCheckable).thenReturn(false)
        `when`(node.isChecked).thenReturn(false)
        `when`(node.isSelected).thenReturn(false)
        `when`(node.isScrollable).thenReturn(false)
        `when`(node.isPassword).thenReturn(false)
        children.forEach { (index, child) ->
            `when`(node.getChild(index)).thenReturn(child)
        }
        return node
    }
}
