package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.events.EventPollRequest
import com.rainng.androidctl.agent.events.EventPollRequestCodec
import com.rainng.androidctl.agent.events.EventPollResult
import com.rainng.androidctl.agent.events.EventPollResultCodec

internal class EventsPollMethod(
    private val eventsPollProvider: (EventPollRequest) -> EventPollResult,
) : DeviceRpcMethod {
    override val name: String = "events.poll"
    override val policy: RpcMethodPolicy =
        RpcMethodPolicy(
            timeoutError = RpcErrorCode.INTERNAL_ERROR,
            timeoutMessage = "events.poll timed out",
        )

    override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall =
        PreparedRpcMethodSupport.prepareDecoded(
            request = request,
            decoder = EventPollRequestCodec,
            timeoutMs = { decoded -> decoded.waitMs + RequestBudgets.EVENTS_POLL_GRACE_MS },
            execute = eventsPollProvider,
            encoder = EventPollResultCodec,
        )
}
