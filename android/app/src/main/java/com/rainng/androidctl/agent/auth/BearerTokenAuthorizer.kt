package com.rainng.androidctl.agent.auth

object BearerTokenAuthorizer {
    fun extractBearerToken(headers: Map<String, String>): String? {
        val authorizationHeader =
            headers.entries.firstOrNull { it.key.equals("authorization", ignoreCase = true) }
                ?: return null
        val authorization = authorizationHeader.value.trim()

        val prefix = "Bearer "
        if (!authorization.startsWith(prefix, ignoreCase = true)) {
            return null
        }

        val token = authorization.substring(prefix.length).trim()
        return token.takeIf { it.isNotEmpty() }
    }
}
