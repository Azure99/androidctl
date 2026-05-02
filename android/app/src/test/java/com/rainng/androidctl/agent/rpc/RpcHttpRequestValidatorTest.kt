package com.rainng.androidctl.agent.rpc

import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RpcHttpRequestValidatorTest {
    @Test
    fun rejectsNonRpcPath() {
        val response = RpcHttpRequestValidator().validate(uri = "/bad", method = NanoHTTPD.Method.POST)

        assertTrue(response != null)
        assertEquals(NanoHTTPD.Response.Status.NOT_FOUND, response?.status)
        val payload = JSONObject(response!!.data.bufferedReader().readText())
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
    }
}
