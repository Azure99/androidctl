package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.rpc.codec.JsonDecoder
import com.rainng.androidctl.agent.rpc.codec.JsonEncoder
import com.rainng.androidctl.agent.rpc.codec.JsonReader
import com.rainng.androidctl.agent.rpc.codec.JsonWriter
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PreparedRpcMethodSupportTest {
    @Test
    fun prepareUnitDefersExecutionAndEncodesResponse() {
        var executeCalls = 0
        val prepared =
            PreparedRpcMethodSupport.prepareUnit(
                timeoutMs = 1234L,
                execute = {
                    executeCalls += 1
                    UnitResponse(value = "ok")
                },
                encoder = UnitResponseCodec,
            )

        assertEquals(0, executeCalls)
        assertEquals(1234L, prepared.timeoutMs)
        val encoded = prepared.executeEncoded()
        assertEquals(1, executeCalls)
        assertEquals("ok", encoded.getString("value"))
    }

    @Test
    fun prepareDecodedDecodesOnceForTimeoutAndExecution() {
        var decodeCalls = 0
        var executedRequest: DecodedRequest? = null
        val request =
            RpcRequestEnvelope(
                id = "req-prepared-helper",
                method = "test.prepareDecoded",
                params = JSONObject("""{"waitMs":250,"label":"event"}"""),
            )
        val prepared =
            PreparedRpcMethodSupport.prepareDecoded(
                request = request,
                decoder =
                    object : JsonDecoder<DecodedRequest> {
                        override fun read(reader: JsonReader): DecodedRequest {
                            decodeCalls += 1
                            return DecodedRequestCodec.read(reader)
                        }
                    },
                timeoutMs = { decoded -> decoded.waitMs + 5L },
                execute = { decoded ->
                    executedRequest = decoded
                    DecodedResponse(label = decoded.label.uppercase())
                },
                encoder = DecodedResponseCodec,
            )

        assertEquals(1, decodeCalls)
        assertNull(executedRequest)
        assertEquals(255L, prepared.timeoutMs)
        val encoded = prepared.executeEncoded()
        assertEquals(1, decodeCalls)
        assertEquals(DecodedRequest(waitMs = 250L, label = "event"), executedRequest)
        assertEquals("EVENT", encoded.getString("label"))
    }

    private data class UnitResponse(
        val value: String,
    )

    private object UnitResponseCodec : JsonEncoder<UnitResponse> {
        override fun write(
            writer: JsonWriter,
            value: UnitResponse,
        ) {
            writer.requiredString("value", value.value)
        }
    }

    private data class DecodedRequest(
        val waitMs: Long,
        val label: String,
    )

    private object DecodedRequestCodec : JsonDecoder<DecodedRequest> {
        override fun read(reader: JsonReader): DecodedRequest =
            DecodedRequest(
                waitMs =
                    reader.requiredLong(
                        key = "waitMs",
                        missingMessage = "missing waitMs",
                        invalidMessage = "invalid waitMs",
                    ),
                label =
                    reader.requiredString(
                        key = "label",
                        missingMessage = "missing label",
                        invalidMessage = "invalid label",
                    ),
            )
    }

    private data class DecodedResponse(
        val label: String,
    )

    private object DecodedResponseCodec : JsonEncoder<DecodedResponse> {
        override fun write(
            writer: JsonWriter,
            value: DecodedResponse,
        ) {
            writer.requiredString("label", value.label)
        }
    }
}
