package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.rpc.codec.JsonWriter
import com.rainng.androidctl.agent.snapshot.SnapshotException
import com.rainng.androidctl.agent.snapshot.SnapshotGetRequest
import com.rainng.androidctl.agent.snapshot.SnapshotGetRequestCodec
import com.rainng.androidctl.agent.snapshot.SnapshotPayload
import com.rainng.androidctl.agent.snapshot.SnapshotPublication
import com.rainng.androidctl.agent.snapshot.SnapshotPublicationGuard
import com.rainng.androidctl.agent.snapshot.SnapshotRecord
import com.rainng.androidctl.agent.snapshot.SnapshotRegistry
import com.rainng.androidctl.agent.snapshot.SnapshotResponseCodec
import org.json.JSONObject

internal class SnapshotGetMethod(
    private val snapshotExecutionFactory: (SnapshotGetRequest) -> () -> SnapshotPublication,
    private val snapshotPublisher: (Long, SnapshotRecord) -> SnapshotPublicationGuard? = SnapshotRegistry::beginPublicationIfCurrent,
    private val responseEncoder: (SnapshotPayload) -> JSONObject = ::encodeSnapshotResponse,
) : DeviceRpcMethod {
    override val name: String = "snapshot.get"
    override val policy: RpcMethodPolicy =
        RpcMethodPolicy(
            requiresReadyRuntime = true,
            requiresAccessibilityHandle = true,
            timeoutError = RpcErrorCode.SNAPSHOT_UNAVAILABLE,
            timeoutMessage = "snapshot.get timed out",
        )

    override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall {
        val decoded = decodeRequest(request)
        val execute = snapshotExecutionFactory(decoded)
        return PreparedRpcCall.typed(
            timeoutMs = RequestBudgets.SNAPSHOT_GET_METHOD_TIMEOUT_MS,
            execute = { collectPublication(execute) },
            encode = ::encodePublishedResponse,
        )
    }

    private fun decodeRequest(request: RpcRequestEnvelope): SnapshotGetRequest =
        PreparedRpcMethodSupport.decodeRequest(request, SnapshotGetRequestCodec)

    private fun collectPublication(snapshotExecution: () -> SnapshotPublication): PublishedSnapshot {
        val publication = snapshotExecution()
        val publicationGuard = snapshotPublisher(publication.generation, publication.registryRecord)
        if (publicationGuard == null) {
            throw SnapshotException(
                code = RpcErrorCode.SNAPSHOT_UNAVAILABLE,
                message = "snapshot publication raced with session reset",
                retryable = true,
            )
        }
        return PublishedSnapshot(
            payload = publication.response,
            guard = publicationGuard,
        )
    }

    private fun encodePublishedResponse(publication: PublishedSnapshot): JSONObject =
        try {
            responseEncoder(publication.payload)
        } finally {
            publication.guard.release()
        }

    private data class PublishedSnapshot(
        val payload: SnapshotPayload,
        val guard: SnapshotPublicationGuard,
    )
}

private fun encodeSnapshotResponse(payload: SnapshotPayload): JSONObject {
    val writer = JsonWriter.objectWriter()
    SnapshotResponseCodec.write(writer, payload)
    return writer.toJsonObject()
}
