package com.rainng.androidctl.agent.auth

import java.util.Base64

internal sealed interface HostTokenProvisioningResult {
    data class Valid(
        val token: String,
    ) : HostTokenProvisioningResult

    data class Invalid(
        val message: String,
    ) : HostTokenProvisioningResult
}

internal object HostTokenProvisioning {
    const val EXTRA_DEVICE_TOKEN = "androidctl.setup.deviceToken"
    const val TOKEN_BYTE_LENGTH = 32
    const val TOKEN_ENCODED_LENGTH = 43

    private val tokenPattern = Regex("^[A-Za-z0-9_-]{$TOKEN_ENCODED_LENGTH}$")
    private val urlDecoder: Base64.Decoder = Base64.getUrlDecoder()
    private val urlEncoder: Base64.Encoder = Base64.getUrlEncoder().withoutPadding()

    fun validate(token: String?): HostTokenProvisioningResult =
        when {
            token.isNullOrEmpty() ->
                invalid("setup device token is required")

            !tokenPattern.matches(token) ->
                invalid("setup device token must be canonical base64url without padding")

            else ->
                validateDecodedToken(token)
        }

    private fun validateDecodedToken(token: String): HostTokenProvisioningResult =
        when (val decoded = decodeToken(token)) {
            null ->
                invalid("setup device token must be valid base64url")

            else ->
                validateCanonicalToken(token = token, decoded = decoded)
        }

    private fun validateCanonicalToken(
        token: String,
        decoded: ByteArray,
    ): HostTokenProvisioningResult =
        when {
            decoded.size != TOKEN_BYTE_LENGTH ->
                invalid("setup device token must decode to 32 bytes")

            urlEncoder.encodeToString(decoded) != token ->
                invalid("setup device token must be canonical base64url without padding")

            else ->
                HostTokenProvisioningResult.Valid(token)
        }

    private fun decodeToken(token: String): ByteArray? =
        try {
            urlDecoder.decode(token)
        } catch (_: IllegalArgumentException) {
            null
        }

    private fun invalid(message: String): HostTokenProvisioningResult.Invalid = HostTokenProvisioningResult.Invalid(message)
}
