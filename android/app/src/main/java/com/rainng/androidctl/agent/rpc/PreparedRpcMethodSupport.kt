package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.rpc.codec.JsonDecoder
import com.rainng.androidctl.agent.rpc.codec.JsonEncoder
import com.rainng.androidctl.agent.rpc.codec.JsonReader
import com.rainng.androidctl.agent.rpc.codec.JsonWriter
import org.json.JSONObject

internal object PreparedRpcMethodSupport {
    fun <RequestT> decodeRequest(
        request: RpcRequestEnvelope,
        decoder: JsonDecoder<RequestT>,
    ): RequestT = decoder.read(JsonReader.fromObject(request.params))

    fun <ResponseT> prepareUnit(
        timeoutMs: Long,
        execute: () -> ResponseT,
        encoder: JsonEncoder<ResponseT>,
    ): PreparedRpcCall =
        PreparedRpcCall.typed(
            timeoutMs = timeoutMs,
            execute = execute,
            encode = { response -> encodeResponse(response, encoder) },
        )

    fun <RequestT, ResponseT> prepareDecoded(
        request: RpcRequestEnvelope,
        decoder: JsonDecoder<RequestT>,
        timeoutMs: (RequestT) -> Long,
        execute: (RequestT) -> ResponseT,
        encoder: JsonEncoder<ResponseT>,
    ): PreparedRpcCall {
        val decoded = decodeRequest(request, decoder)
        return PreparedRpcCall.typed(
            timeoutMs = timeoutMs(decoded),
            execute = { execute(decoded) },
            encode = { response -> encodeResponse(response, encoder) },
        )
    }

    private fun <ResponseT> encodeResponse(
        response: ResponseT,
        encoder: JsonEncoder<ResponseT>,
    ): JSONObject {
        val writer = JsonWriter.objectWriter()
        encoder.write(writer, response)
        return writer.toJsonObject()
    }
}
