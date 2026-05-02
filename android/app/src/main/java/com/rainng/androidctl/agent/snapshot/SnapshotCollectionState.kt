package com.rainng.androidctl.agent.snapshot

internal data class SnapshotCollectionState(
    val snapshotId: Long,
    val ridToHandle: MutableMap<String, SnapshotNodeHandle> = linkedMapOf(),
    val windowsPayload: MutableList<SnapshotWindow> = mutableListOf(),
    val nodesPayload: MutableList<SnapshotNode> = mutableListOf(),
)
