package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.errors.RpcErrorCode
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RpcEnvelopeTest {
    @Test
    fun successEnvelope_containsIdAndResult() {
        val result = JSONObject().put("service", "androidctl-device-agent")
        val payload = JSONObject(RpcEnvelope.success("req-1", result))

        assertEquals("req-1", payload.getString("id"))
        assertTrue(payload.getBoolean("ok"))
        assertEquals("androidctl-device-agent", payload.getJSONObject("result").getString("service"))
    }

    @Test
    fun errorEnvelope_containsStableFields() {
        val payload =
            JSONObject(
                RpcEnvelope.error(
                    id = "req-2",
                    code = RpcErrorCode.RUNTIME_NOT_READY,
                    message = "not ready",
                    retryable = true,
                ),
            )

        assertEquals("req-2", payload.getString("id"))
        assertFalse(payload.getBoolean("ok"))
        assertEquals("RUNTIME_NOT_READY", payload.getJSONObject("error").getString("code"))
        assertEquals("not ready", payload.getJSONObject("error").getString("message"))
        assertTrue(payload.getJSONObject("error").getBoolean("retryable"))
    }
}
