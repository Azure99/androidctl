package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.runtime.RuntimeReadiness
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RpcAuthorizationGateTest {
    @Test
    fun missingBearerTokenReturnsUnauthorizedEnvelope() {
        val gate =
            RpcAuthorizationGate(
                expectedTokenProvider = { "device-token" },
                readinessProvider = { RuntimeReadiness(accessibilityEnabled = true, accessibilityConnected = true) },
            )

        val response = gate.authorize(requestId = "req-1", headers = emptyMap())

        assertTrue(response != null)
        val payload = JSONObject(response!!)
        assertEquals("UNAUTHORIZED", payload.getJSONObject("error").getString("code"))
    }

    @Test
    fun malformedBearerTokenReturnsUnauthorizedEnvelope() {
        val malformedHeaders =
            listOf(
                mapOf("authorization" to "Basic device-token"),
                mapOf("authorization" to "Bearer "),
                mapOf("authorization" to "Bearer"),
            )

        malformedHeaders.forEachIndexed { index, headers ->
            var expectedTokenLookups = 0
            val gate =
                RpcAuthorizationGate(
                    expectedTokenProvider = {
                        expectedTokenLookups += 1
                        "device-token"
                    },
                    readinessProvider = { RuntimeReadiness(accessibilityEnabled = true, accessibilityConnected = true) },
                )

            val response = gate.authorize(requestId = "req-malformed-auth-$index", headers = headers)

            assertTrue(response != null)
            val payload = JSONObject(response!!)
            assertFalse(payload.getBoolean("ok"))
            assertEquals("req-malformed-auth-$index", payload.getString("id"))
            assertEquals("UNAUTHORIZED", payload.getJSONObject("error").getString("code"))
            assertFalse(payload.getJSONObject("error").getBoolean("retryable"))
            assertEquals(0, expectedTokenLookups)
        }
    }

    @Test
    fun malformedBearerTokenReturnsUnauthorizedEvenWhenAuthBlocked() {
        val malformedHeaders =
            listOf(
                mapOf("authorization" to "Basic device-token"),
                mapOf("authorization" to "Bearer "),
                mapOf("authorization" to "Bearer"),
            )

        malformedHeaders.forEachIndexed { index, headers ->
            var expectedTokenLookups = 0
            val gate =
                RpcAuthorizationGate(
                    expectedTokenProvider = {
                        expectedTokenLookups += 1
                        "device-token"
                    },
                    readinessProvider = { authBlockedReadiness() },
                )

            val response = gate.authorize(requestId = "req-malformed-auth-blocked-$index", headers = headers)

            assertTrue(response != null)
            val payload = JSONObject(response!!)
            assertFalse(payload.getBoolean("ok"))
            assertEquals("req-malformed-auth-blocked-$index", payload.getString("id"))
            assertEquals("UNAUTHORIZED", payload.getJSONObject("error").getString("code"))
            assertFalse(payload.getJSONObject("error").getBoolean("retryable"))
            assertEquals(0, expectedTokenLookups)
        }
    }

    @Test
    fun missingBearerTokenReturnsUnauthorizedEvenWhenAuthBlocked() {
        var expectedTokenLookups = 0
        val gate =
            RpcAuthorizationGate(
                expectedTokenProvider = {
                    expectedTokenLookups += 1
                    "device-token"
                },
                readinessProvider = { authBlockedReadiness() },
            )

        val response = gate.authorize(requestId = "req-missing-auth-blocked", headers = emptyMap())

        assertTrue(response != null)
        val payload = JSONObject(response!!)
        assertFalse(payload.getBoolean("ok"))
        assertEquals("req-missing-auth-blocked", payload.getString("id"))
        assertEquals("UNAUTHORIZED", payload.getJSONObject("error").getString("code"))
        assertFalse(payload.getJSONObject("error").getBoolean("retryable"))
        assertEquals(0, expectedTokenLookups)
    }

    @Test
    fun replacedExpectedTokenImmediatelyInvalidatesOldBearer() {
        var currentToken = "old-token"
        val gate =
            RpcAuthorizationGate(
                expectedTokenProvider = { currentToken },
                readinessProvider = { RuntimeReadiness(accessibilityEnabled = true, accessibilityConnected = true) },
            )

        assertEquals(
            null,
            gate.authorize(
                requestId = "req-old-ok",
                headers = mapOf("authorization" to "Bearer old-token"),
            ),
        )

        currentToken = "new-token"

        val oldTokenResponse =
            gate.authorize(
                requestId = "req-old-invalid",
                headers = mapOf("authorization" to "Bearer old-token"),
            )

        assertTrue(oldTokenResponse != null)
        assertEquals(
            "UNAUTHORIZED",
            JSONObject(oldTokenResponse!!).getJSONObject("error").getString("code"),
        )
        assertEquals(
            null,
            gate.authorize(
                requestId = "req-new-ok",
                headers = mapOf("authorization" to "Bearer new-token"),
            ),
        )
    }

    @Test
    fun wrongBearerDuringAuthBlockedReturnsRuntimeNotReadyWithoutTokenLookup() {
        var expectedTokenLookups = 0
        val gate =
            RpcAuthorizationGate(
                expectedTokenProvider = {
                    expectedTokenLookups += 1
                    "device-token"
                },
                readinessProvider = { authBlockedReadiness() },
            )

        val response =
            gate.authorize(
                requestId = "req-wrong-auth-blocked",
                headers = mapOf("authorization" to "Bearer wrong-token"),
            )

        assertTrue(response != null)
        val payload = JSONObject(response!!)
        assertFalse(payload.getBoolean("ok"))
        assertEquals("req-wrong-auth-blocked", payload.getString("id"))
        assertEquals("RUNTIME_NOT_READY", payload.getJSONObject("error").getString("code"))
        assertEquals("stored device token could not be decrypted", payload.getJSONObject("error").getString("message"))
        assertTrue(payload.getJSONObject("error").getBoolean("retryable"))
        assertEquals(0, expectedTokenLookups)
    }

    @Test
    fun wrongBearerWithoutAuthBlockedReturnsUnauthorizedAfterTokenLookup() {
        var expectedTokenLookups = 0
        val gate =
            RpcAuthorizationGate(
                expectedTokenProvider = {
                    expectedTokenLookups += 1
                    "device-token"
                },
                readinessProvider = { RuntimeReadiness(accessibilityEnabled = true, accessibilityConnected = true) },
            )

        val response =
            gate.authorize(
                requestId = "req-wrong-auth",
                headers = mapOf("authorization" to "Bearer wrong-token"),
            )

        assertTrue(response != null)
        val payload = JSONObject(response!!)
        assertFalse(payload.getBoolean("ok"))
        assertEquals("req-wrong-auth", payload.getString("id"))
        assertEquals("UNAUTHORIZED", payload.getJSONObject("error").getString("code"))
        assertFalse(payload.getJSONObject("error").getBoolean("retryable"))
        assertEquals(1, expectedTokenLookups)
    }

    private fun authBlockedReadiness(): RuntimeReadiness =
        RuntimeReadiness(
            accessibilityEnabled = true,
            accessibilityConnected = true,
            authBlockedMessage = "stored device token could not be decrypted",
        )
}
