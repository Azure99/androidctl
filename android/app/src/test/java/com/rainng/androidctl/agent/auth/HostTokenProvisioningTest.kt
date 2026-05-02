package com.rainng.androidctl.agent.auth

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

class HostTokenProvisioningTest {
    @Test
    fun acceptsCanonicalBase64UrlTokenEncodingThirtyTwoBytes() {
        val token = validToken()

        val result = HostTokenProvisioning.validate(token)

        assertEquals(HostTokenProvisioningResult.Valid(token), result)
        assertEquals(HostTokenProvisioning.TOKEN_ENCODED_LENGTH, token.length)
        assertTrue('=' !in token)
    }

    @Test
    fun rejectsMissingOrMalformedTokens() {
        val invalidTokens =
            listOf(
                null,
                "",
                "short",
                validToken() + "=",
                validToken().dropLast(1) + "!",
                Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(16)),
                Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(33)),
            )

        invalidTokens.forEach { token ->
            val result = HostTokenProvisioning.validate(token)

            assertTrue("expected invalid token result for $token", result is HostTokenProvisioningResult.Invalid)
        }
    }

    private fun validToken(): String =
        Base64
            .getUrlEncoder()
            .withoutPadding()
            .encodeToString(ByteArray(HostTokenProvisioning.TOKEN_BYTE_LENGTH) { index -> index.toByte() })
}
