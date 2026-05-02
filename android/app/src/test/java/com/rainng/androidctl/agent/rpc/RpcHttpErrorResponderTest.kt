package com.rainng.androidctl.agent.rpc

import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class RpcHttpErrorResponderTest {
    @Test
    fun unexpectedFailureMapsToInternalErrorEnvelope() {
        val response =
            RpcHttpErrorResponder().unexpected(
                error = IllegalStateException("boom"),
                onError = {},
                logError = { _, _ -> },
            )

        assertEquals(NanoHTTPD.Response.Status.OK, response.status)
        val payload = JSONObject(response.data.bufferedReader().readText())
        assertEquals("INTERNAL_ERROR", payload.getJSONObject("error").getString("code"))
        assertEquals("boom", payload.getJSONObject("error").getString("message"))
    }
}
