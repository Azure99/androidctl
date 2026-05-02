package com.rainng.androidctl.agent.actions

import android.view.accessibility.AccessibilityNodeInfo
import com.rainng.androidctl.agent.WindowIds
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.snapshot.NodeFingerprint
import com.rainng.androidctl.agent.snapshot.NodePath
import com.rainng.androidctl.agent.snapshot.SnapshotRegistry
import com.rainng.androidctl.agent.testsupport.assertActionException
import com.rainng.androidctl.agent.testsupport.mockNode
import com.rainng.androidctl.agent.testsupport.mockService
import com.rainng.androidctl.agent.testsupport.mockWindow
import com.rainng.androidctl.agent.testsupport.nodeFingerprint
import com.rainng.androidctl.agent.testsupport.snapshotRecord
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Test
import org.mockito.Mockito.doReturn

class AccessibilityActionTargetResolverTest {
    @After
    fun tearDown() {
        SnapshotRegistry.resetForTest()
    }

    @Test
    fun withResolvedNodeMatchesPlatformWindowUsingSharedFormatter() {
        val root = mockNode()
        publishCurrent(
            snapshotRecord(
                snapshotId = 1L,
                rid = "w12:0",
                path = NodePath(windowId = WindowIds.fromPlatformWindowId(12), childIndices = emptyList()),
                fingerprint = nodeFingerprint(windowId = WindowIds.fromPlatformWindowId(12)),
            ),
        )
        val service = mockService()
        doReturn(listOf(mockWindow(12, root))).`when`(service).windows
        val resolver = AccessibilityActionTargetResolver(service)

        val status =
            resolver.withResolvedNode(1L, "w12:0") { node ->
                assertEquals(root, node)
                "done"
            }

        assertEquals("done", status)
    }

    @Test
    fun withResolvedNodeFailsWhenNoSnapshotExists() {
        val resolver = AccessibilityActionTargetResolver(mockService())

        assertActionException(
            expectedCode = RpcErrorCode.STALE_TARGET,
            expectedMessage = "snapshot handle is stale",
            expectedRetryable = true,
        ) {
            resolver.withResolvedNode(1L, "w1:0") { "done" }
        }
    }

    @Test
    fun withResolvedNodeFailsWhenSnapshotIdIsStale() {
        publishCurrent(snapshotRecord(snapshotId = 1L))
        val resolver = AccessibilityActionTargetResolver(mockService())

        assertActionException(
            expectedCode = RpcErrorCode.STALE_TARGET,
            expectedMessage = "snapshot handle is stale",
            expectedRetryable = true,
        ) {
            resolver.withResolvedNode(2L, "w1:0") { "done" }
        }
    }

    @Test
    fun withResolvedNodeAllowsNonLatestSnapshotWhenStillRetainedAndValid() {
        val child = mockNode(actionResults = mapOf(AccessibilityNodeInfo.ACTION_CLICK to true))
        val root = mockNode(children = listOf(child))
        publishCurrent(
            snapshotRecord(
                snapshotId = 1L,
                rid = "w1:0",
                path = NodePath(windowId = "w1", childIndices = listOf(0)),
            ),
        )
        publishCurrent(snapshotRecord(snapshotId = 2L, rid = "w2:0"))
        val service = mockService()
        doReturn(listOf(mockWindow(1, root))).`when`(service).windows
        val resolver = AccessibilityActionTargetResolver(service)

        val status =
            resolver.withResolvedNode(1L, "w1:0") { node ->
                assertEquals(child, node)
                "done"
            }

        assertEquals("done", status)
    }

    @Test
    fun withResolvedNodeFailsWhenSnapshotHasBeenEvicted() {
        repeat(9) { index ->
            publishCurrent(snapshotRecord(snapshotId = (index + 1).toLong(), rid = "w${index + 1}:0"))
        }
        val resolver = AccessibilityActionTargetResolver(mockService())

        assertActionException(
            expectedCode = RpcErrorCode.STALE_TARGET,
            expectedMessage = "snapshot handle is stale",
            expectedRetryable = true,
        ) {
            resolver.withResolvedNode(1L, "w1:0") { "done" }
        }
    }

    @Test
    fun withResolvedNodeFailsWhenRidIsMissing() {
        publishCurrent(snapshotRecord(snapshotId = 1L))
        val resolver = AccessibilityActionTargetResolver(mockService())

        assertActionException(
            expectedCode = RpcErrorCode.STALE_TARGET,
            expectedMessage = "target handle no longer exists on the current snapshot",
            expectedRetryable = true,
        ) {
            resolver.withResolvedNode(1L, "w1:0") { "done" }
        }
    }

    @Test
    fun withResolvedNodeFailsWhenCurrentWindowTreeCannotResolvePath() {
        publishCurrent(
            snapshotRecord(
                snapshotId = 1L,
                rid = "w9:0",
                path = NodePath(windowId = "w9", childIndices = emptyList()),
                fingerprint = nodeFingerprint(windowId = "w9"),
            ),
        )
        val service = mockService()
        doReturn(emptyList<android.view.accessibility.AccessibilityWindowInfo>()).`when`(service).windows
        val resolver = AccessibilityActionTargetResolver(service)

        assertActionException(
            expectedCode = RpcErrorCode.STALE_TARGET,
            expectedMessage = "target handle no longer resolves on the current screen",
            expectedRetryable = true,
        ) {
            resolver.withResolvedNode(1L, "w9:0") { "done" }
        }
    }

    @Test
    fun withResolvedNodeFailsWhenFingerprintDoesNotMatchResolvedNode() {
        val root =
            mockNode(
                packageName = "com.android.settings",
                className = "android.widget.Button",
                resourceId = "other:id/button",
            )
        publishCurrent(
            snapshotRecord(
                snapshotId = 1L,
                rid = "w1:0",
                path = NodePath(windowId = "w1", childIndices = emptyList()),
                fingerprint =
                    NodeFingerprint(
                        windowId = "w1",
                        packageName = "com.android.settings",
                        className = "android.widget.Button",
                        resourceId = "android:id/button1",
                    ),
            ),
        )
        val service = mockService()
        doReturn(listOf(mockWindow(1, root))).`when`(service).windows
        val resolver = AccessibilityActionTargetResolver(service)

        assertActionException(
            expectedCode = RpcErrorCode.STALE_TARGET,
            expectedMessage = "target handle no longer matches the current node",
            expectedRetryable = true,
        ) {
            resolver.withResolvedNode(1L, "w1:0") { "done" }
        }
    }

    @Test
    fun withResolvedNodeFailsWhenResourceIdIsMissingAndResolvedClassDrifts() {
        val root =
            mockNode(
                packageName = "com.android.settings",
                className = "android.widget.TextView",
                resourceId = null,
            )
        publishCurrent(
            snapshotRecord(
                snapshotId = 1L,
                rid = "w1:0",
                path = NodePath(windowId = "w1", childIndices = emptyList()),
                fingerprint =
                    NodeFingerprint(
                        windowId = "w1",
                        packageName = "com.android.settings",
                        className = "android.widget.Button",
                        resourceId = null,
                    ),
            ),
        )
        val service = mockService()
        doReturn(listOf(mockWindow(1, root))).`when`(service).windows
        val resolver = AccessibilityActionTargetResolver(service)

        assertActionException(
            expectedCode = RpcErrorCode.STALE_TARGET,
            expectedMessage = "target handle no longer matches the current node",
            expectedRetryable = true,
        ) {
            resolver.withResolvedNode(1L, "w1:0") { "done" }
        }
    }

    @Test
    fun withResolvedNodeAcceptsLiveNodeWhoseClassNameNormalizesToStoredFallback() {
        val root =
            mockNode(
                packageName = "com.android.settings",
                resourceId = null,
            )
        doReturn(null).`when`(root).className
        publishCurrent(
            snapshotRecord(
                snapshotId = 1L,
                rid = "w1:0",
                path = NodePath(windowId = "w1", childIndices = emptyList()),
                fingerprint =
                    NodeFingerprint(
                        windowId = "w1",
                        packageName = "com.android.settings",
                        className = "android.view.View",
                        resourceId = null,
                    ),
            ),
        )
        val service = mockService()
        doReturn(listOf(mockWindow(1, root))).`when`(service).windows
        val resolver = AccessibilityActionTargetResolver(service)

        val status = resolver.withResolvedNode(1L, "w1:0") { "done" }

        assertEquals("done", status)
    }

    @Test
    fun withResolvedNodeResolvesNestedChildAndInvokesBlock() {
        val child = mockNode(actionResults = mapOf(AccessibilityNodeInfo.ACTION_CLICK to true))
        val root = mockNode(children = listOf(child))
        publishCurrent(
            snapshotRecord(
                snapshotId = 1L,
                rid = "w1:0",
                path = NodePath(windowId = "w1", childIndices = listOf(0)),
            ),
        )
        val service = mockService()
        doReturn(listOf(mockWindow(1, root))).`when`(service).windows
        val resolver = AccessibilityActionTargetResolver(service)

        val status =
            resolver.withResolvedNode(1L, "w1:0") { node ->
                assertEquals(child, node)
                "done"
            }

        assertEquals("done", status)
    }

    private fun publishCurrent(snapshot: com.rainng.androidctl.agent.snapshot.SnapshotRecord): Boolean {
        val publication =
            SnapshotRegistry.beginPublicationIfCurrent(
                SnapshotRegistry.currentGeneration(),
                snapshot,
            ) ?: return false
        publication.release()
        return true
    }
}
