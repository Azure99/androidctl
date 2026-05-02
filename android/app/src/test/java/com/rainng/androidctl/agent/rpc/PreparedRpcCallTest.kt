package com.rainng.androidctl.agent.rpc

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class PreparedRpcCallTest {
    @Test
    fun typedDefersExecutionUntilEncodedResponseIsRequested() {
        var executeCalls = 0
        var encodeCalls = 0

        val prepared =
            PreparedRpcCall.typed(
                timeoutMs = 321L,
                execute = {
                    executeCalls += 1
                    "typed-result"
                },
                encode = { value ->
                    encodeCalls += 1
                    JSONObject().put("value", value)
                },
            )

        assertEquals(0, executeCalls)
        assertEquals(0, encodeCalls)
        assertEquals(321L, prepared.timeoutMs)

        val encoded = prepared.executeEncoded()

        assertEquals(1, executeCalls)
        assertEquals(1, encodeCalls)
        assertEquals("typed-result", encoded.getString("value"))
    }

    @Test
    fun typedRejectsPreEncodedJsonObjectResults() {
        val prepared =
            PreparedRpcCall.typed(
                timeoutMs = 654L,
                execute = { JSONObject().put("already", "encoded") },
                encode = { payload -> payload },
            )

        try {
            prepared.executeEncoded()
        } catch (error: IllegalArgumentException) {
            assertEquals(
                "PreparedRpcCall.typed requires typed results, not pre-encoded JSONObject payloads",
                error.message,
            )
            return
        }

        throw AssertionError("expected IllegalArgumentException")
    }
}
