package com.rainng.androidctl.agent.snapshot

import android.view.accessibility.AccessibilityNodeInfo

internal object SnapshotRegistry {
    private val stateLock = Any()
    private val generationGate = SnapshotGenerationGate()
    private val publicationTracker = SnapshotPublicationTracker()
    private val retainedSnapshotStore = RetainedSnapshotStore(limit = RETAINED_SNAPSHOT_LIMIT)

    fun nextSnapshotId(): Long = generationGate.nextSnapshotId()

    fun currentGeneration(): Long =
        synchronized(stateLock) {
            generationGate.currentGenerationLocked()
        }

    fun beginPublicationIfCurrent(
        generation: Long,
        snapshot: SnapshotRecord,
    ): SnapshotPublicationGuard? =
        synchronized(stateLock) {
            if (!generationGate.canPublishLocked(generation)) {
                null
            } else {
                retainedSnapshotStore.putLocked(
                    snapshot = snapshot,
                    inFlightSnapshotIds = publicationTracker.inFlightSnapshotIdsLocked(),
                )
                publicationTracker.beginPublicationLocked(snapshot.snapshotId)
                SnapshotPublicationGuard { completePublication(snapshot.snapshotId) }
            }
        }

    fun find(snapshotId: Long): SnapshotRecord? =
        synchronized(stateLock) {
            if (generationGate.resetInProgressLocked() && !publicationTracker.isPublicationInFlightLocked(snapshotId)) {
                return@synchronized null
            }
            retainedSnapshotStore.findLocked(snapshotId)
        }

    fun resetSessionState(timeoutMs: Long = DEFAULT_RESET_TIMEOUT_MS): SnapshotResetResult {
        require(timeoutMs >= 0L) { "timeoutMs must be non-negative" }
        return synchronized(stateLock) {
            generationGate.beginResetLocked()
            val waitResult = publicationTracker.waitForActivePublicationsLocked(stateLock, timeoutMs)
            if (!waitResult.completed) {
                publicationTracker.abandonActivePublicationsLocked(stateLock)
            }
            retainedSnapshotStore.clearLocked()
            generationGate.finishResetLocked()
            SnapshotResetResult(
                completed = waitResult.completed,
                timedOut = !waitResult.completed,
                activePublicationCount = waitResult.activePublicationCount,
                timeoutMs = timeoutMs,
            )
        }
    }

    internal fun resetForTest() {
        synchronized(stateLock) {
            generationGate.resetForTestLocked()
            publicationTracker.resetForTestLocked()
            retainedSnapshotStore.clearLocked()
        }
    }

    internal fun resetInProgressForTest(): Boolean =
        synchronized(stateLock) {
            generationGate.resetInProgressLocked()
        }

    private fun completePublication(snapshotId: Long) {
        synchronized(stateLock) {
            publicationTracker.completePublicationLocked(snapshotId, stateLock)
        }
    }

    private const val RETAINED_SNAPSHOT_LIMIT = 8
    private const val DEFAULT_RESET_TIMEOUT_MS = 1_000L
}

internal data class SnapshotResetResult(
    val completed: Boolean,
    val timedOut: Boolean,
    val activePublicationCount: Int,
    val timeoutMs: Long,
)

internal class SnapshotPublicationGuard(
    private val releaseAction: () -> Unit,
) {
    private var released = false

    fun release() {
        if (released) {
            return
        }
        released = true
        releaseAction()
    }
}

internal data class SnapshotRecord(
    val snapshotId: Long,
    val ridToHandle: Map<String, SnapshotNodeHandle>,
)

internal data class SnapshotNodeHandle(
    val path: NodePath,
    val fingerprint: NodeFingerprint,
)

internal data class NodePath(
    val windowId: String,
    val childIndices: List<Int>,
)

internal data class NodeFingerprint(
    val windowId: String,
    val packageName: String?,
    val className: String,
    val resourceId: String?,
) {
    companion object {
        internal fun fromSnapshotNode(node: SnapshotNode): NodeFingerprint =
            NodeFingerprint(
                windowId = node.windowId,
                packageName = node.packageName,
                className = normalizeSnapshotClassName(node.className),
                resourceId = node.resourceId,
            )

        @Suppress("DEPRECATION")
        internal fun fromAccessibilityNode(
            windowId: String,
            node: AccessibilityNodeInfo,
        ): NodeFingerprint =
            NodeFingerprint(
                windowId = windowId,
                packageName = node.packageName?.toString(),
                className = normalizeSnapshotClassName(node.className?.toString()),
                resourceId = node.viewIdResourceName,
            )
    }
}
