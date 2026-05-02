package com.rainng.androidctl.agent.actions

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityNodeInfo
import com.rainng.androidctl.agent.WindowIds
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.snapshot.NodeFingerprint
import com.rainng.androidctl.agent.snapshot.NodePath
import com.rainng.androidctl.agent.snapshot.SnapshotNodeHandle
import com.rainng.androidctl.agent.snapshot.SnapshotRecord
import com.rainng.androidctl.agent.snapshot.SnapshotRegistry

internal interface ActionTargetResolver {
    fun <T> withResolvedNode(
        snapshotId: Long,
        rid: String,
        block: (AccessibilityNodeInfo) -> T,
    ): T
}

@Suppress("DEPRECATION")
internal class AccessibilityActionTargetResolver(
    private val service: AccessibilityService,
    private val snapshotLookup: (Long) -> SnapshotRecord? = SnapshotRegistry::find,
    private val fingerprintProvider: (SnapshotNodeHandle, AccessibilityNodeInfo) -> NodeFingerprint = { handle, node ->
        NodeFingerprint.fromAccessibilityNode(handle.path.windowId, node)
    },
) : ActionTargetResolver {
    override fun <T> withResolvedNode(
        snapshotId: Long,
        rid: String,
        block: (AccessibilityNodeInfo) -> T,
    ): T {
        val snapshotRecord =
            snapshotLookup(snapshotId) ?: throw ActionException(
                code = RpcErrorCode.STALE_TARGET,
                message = "snapshot handle is stale",
                retryable = true,
            )
        val nodeHandle =
            snapshotRecord.ridToHandle[rid] ?: throw ActionException(
                code = RpcErrorCode.STALE_TARGET,
                message = "target handle no longer exists on the current snapshot",
                retryable = true,
            )
        val node =
            resolveNode(nodeHandle.path) ?: throw ActionException(
                code = RpcErrorCode.STALE_TARGET,
                message = "target handle no longer resolves on the current screen",
                retryable = true,
            )
        return try {
            ensureFingerprintMatches(nodeHandle, node)
            block(node)
        } finally {
            node.recycle()
        }
    }

    private fun resolveNode(path: NodePath): AccessibilityNodeInfo? {
        val window = service.windows?.firstOrNull { window -> WindowIds.matchesPlatformWindow(path.windowId, window.id) }
        var current = window?.root
        if (current != null) {
            path.childIndices.forEach { index ->
                current =
                    current?.let { parent ->
                        parent.getChild(index).also {
                            parent.recycle()
                        }
                    }
            }
        }
        return current
    }

    private fun ensureFingerprintMatches(
        handle: SnapshotNodeHandle,
        node: AccessibilityNodeInfo,
    ) {
        if (fingerprintProvider(handle, node) != handle.fingerprint) {
            throw ActionException(
                code = RpcErrorCode.STALE_TARGET,
                message = "target handle no longer matches the current node",
                retryable = true,
            )
        }
    }
}
