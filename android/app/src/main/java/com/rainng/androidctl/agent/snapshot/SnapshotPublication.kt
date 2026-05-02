package com.rainng.androidctl.agent.snapshot

import com.rainng.androidctl.agent.errors.RpcErrorCode

internal data class SnapshotPublication(
    val response: SnapshotPayload,
    val registryRecord: SnapshotRecord,
    val generation: Long,
) {
    companion object {
        fun create(
            response: SnapshotPayload,
            registryRecord: SnapshotRecord,
            generation: Long,
        ): SnapshotPublication {
            val normalizedResponse = normalizeSnapshotPayload(response)
            val normalizedRegistryRecord = normalizeRegistryRecord(normalizedResponse, registryRecord)
            validate(normalizedResponse)
            require(normalizedRegistryRecord.snapshotId == normalizedResponse.snapshotId) {
                "snapshot publication metadata must match response snapshotId"
            }
            return SnapshotPublication(
                response = normalizedResponse,
                registryRecord = normalizedRegistryRecord,
                generation = generation,
            )
        }

        private fun validate(response: SnapshotPayload) {
            if (response.windows.isEmpty()) {
                throw SnapshotException(
                    code = RpcErrorCode.NO_ACTIVE_WINDOW,
                    message = "no active accessibility window is available",
                    retryable = true,
                )
            }
            if (response.nodes.isEmpty()) {
                throw SnapshotException(
                    code = RpcErrorCode.SNAPSHOT_UNAVAILABLE,
                    message = "snapshot capture produced no nodes",
                    retryable = true,
                )
            }
            require(response.nodes.all { node -> node.className.isNotBlank() }) {
                "snapshot nodes must have non-blank className"
            }
        }
    }
}

private fun normalizeSnapshotPayload(payload: SnapshotPayload): SnapshotPayload =
    payload.copy(nodes = payload.nodes.map(::normalizeSnapshotNode))

private fun normalizeSnapshotNode(node: SnapshotNode): SnapshotNode = node.copy(className = normalizeSnapshotClassName(node.className))

private fun normalizeRegistryRecord(
    payload: SnapshotPayload,
    registryRecord: SnapshotRecord,
): SnapshotRecord {
    val normalizedClassNames = payload.nodes.associate { node -> node.rid to node.className }
    return registryRecord.copy(
        ridToHandle =
            registryRecord.ridToHandle.mapValues { (rid, handle) ->
                val normalizedClassName = normalizedClassNames[rid]
                if (normalizedClassName == null) {
                    handle
                } else {
                    handle.copy(
                        fingerprint =
                            handle.fingerprint.copy(
                                className = normalizedClassName,
                            ),
                    )
                }
            },
    )
}

internal data class SnapshotPayload(
    val snapshotId: Long,
    val capturedAt: String,
    val packageName: String?,
    val activityName: String?,
    val display: SnapshotDisplay,
    val ime: SnapshotIme,
    val windows: List<SnapshotWindow>,
    val nodes: List<SnapshotNode>,
)

internal data class SnapshotDisplay(
    val widthPx: Int,
    val heightPx: Int,
    val densityDpi: Int,
    val rotation: Int,
)

internal data class SnapshotIme(
    val visible: Boolean,
    val windowId: String?,
)

internal data class SnapshotWindow(
    val windowId: String,
    val type: String,
    val layer: Int,
    val packageName: String?,
    val bounds: List<Int>,
    val rootRid: String,
)

internal data class SnapshotNode(
    val rid: String,
    val windowId: String,
    val parentRid: String?,
    val childRids: List<String>,
    val className: String,
    val resourceId: String?,
    val text: String?,
    val contentDesc: String?,
    val hintText: String?,
    val stateDescription: String?,
    val paneTitle: String?,
    val packageName: String?,
    val bounds: List<Int>,
    val visibleToUser: Boolean,
    val importantForAccessibility: Boolean,
    val clickable: Boolean,
    val enabled: Boolean,
    val editable: Boolean,
    val focusable: Boolean,
    val focused: Boolean,
    val checkable: Boolean,
    val checked: Boolean,
    val selected: Boolean,
    val scrollable: Boolean,
    val password: Boolean,
    val actions: List<String>,
)
