package com.rainng.androidctl.agent.auth

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class BearerTokenAuthorizerTest {
    @Test
    fun extractsBearerToken_caseInsensitive() {
        val token =
            BearerTokenAuthorizer.extractBearerToken(
                mapOf("Authorization" to "Bearer abc-123"),
            )

        assertEquals("abc-123", token)
    }

    @Test
    fun returnsNullWhenAuthorizationHeaderIsMissingOrMalformed() {
        assertNull(BearerTokenAuthorizer.extractBearerToken(emptyMap()))

        assertNull(
            BearerTokenAuthorizer.extractBearerToken(
                mapOf("authorization" to "Basic abc-123"),
            ),
        )
    }
}
