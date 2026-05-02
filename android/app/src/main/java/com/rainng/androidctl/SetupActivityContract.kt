package com.rainng.androidctl

import android.os.BadParcelableException
import com.rainng.androidctl.agent.auth.HostTokenProvisioning
import com.rainng.androidctl.agent.auth.HostTokenProvisioningResult

internal data class SetupIntentValidation(
    val accepted: Boolean,
    val autoStartServer: Boolean,
    val reason: String? = null,
)

internal data class SetupIntentPayload(
    val extraKeys: Set<String> = emptySet(),
    val deviceToken: String? = null,
)

internal object SetupActivityContract {
    const val ACTION_SETUP = "com.rainng.androidctl.action.SETUP"
    const val COMPONENT_CLASS_NAME = "com.rainng.androidctl.SetupActivity"

    private val supportedPayloadKeys = setOf(HostTokenProvisioning.EXTRA_DEVICE_TOKEN)

    fun validate(
        action: String?,
        extraKeys: Set<String> = emptySet(),
    ): SetupIntentValidation =
        when {
            action != ACTION_SETUP ->
                SetupIntentValidation(
                    accepted = false,
                    autoStartServer = false,
                    reason = "setup action is required",
                )

            (extraKeys - supportedPayloadKeys).isNotEmpty() ->
                SetupIntentValidation(
                    accepted = false,
                    autoStartServer = false,
                    reason = "setup payload is not supported yet",
                )

            HostTokenProvisioning.EXTRA_DEVICE_TOKEN !in extraKeys ->
                SetupIntentValidation(
                    accepted = false,
                    autoStartServer = false,
                    reason = "setup device token is required",
                )

            else ->
                SetupIntentValidation(
                    accepted = true,
                    autoStartServer = true,
                )
        }
}

internal object SetupIntentHandler {
    private const val BAD_PAYLOAD_REASON = "setup payload could not be read"

    fun handle(
        action: String?,
        payloadReader: () -> SetupIntentPayload,
        startServer: () -> Unit,
        provisionDeviceToken: (String) -> Unit,
    ): SetupIntentValidation {
        val validation =
            if (action != SetupActivityContract.ACTION_SETUP) {
                SetupActivityContract.validate(
                    action = action,
                    extraKeys = emptySet(),
                )
            } else {
                handleSetupPayload(
                    action = action,
                    payloadReader = payloadReader,
                    provisionDeviceToken = provisionDeviceToken,
                )
            }
        if (validation.autoStartServer) {
            startServer()
        }
        return validation
    }

    private fun handleSetupPayload(
        action: String?,
        payloadReader: () -> SetupIntentPayload,
        provisionDeviceToken: (String) -> Unit,
    ): SetupIntentValidation =
        try {
            val payload = payloadReader()
            val validation =
                SetupActivityContract.validate(
                    action = action,
                    extraKeys = payload.extraKeys,
                )
            provisionTokenIfNeeded(
                validation = validation,
                payload = payload,
                provisionDeviceToken = provisionDeviceToken,
            )
        } catch (_: BadParcelableException) {
            SetupIntentValidation(
                accepted = false,
                autoStartServer = false,
                reason = BAD_PAYLOAD_REASON,
            )
        }

    private fun provisionTokenIfNeeded(
        validation: SetupIntentValidation,
        payload: SetupIntentPayload,
        provisionDeviceToken: (String) -> Unit,
    ): SetupIntentValidation =
        when {
            !validation.accepted ->
                validation

            else ->
                provisionToken(
                    token = payload.deviceToken,
                    fallbackValidation = validation,
                    provisionDeviceToken = provisionDeviceToken,
                )
        }

    private fun provisionToken(
        token: String?,
        fallbackValidation: SetupIntentValidation,
        provisionDeviceToken: (String) -> Unit,
    ): SetupIntentValidation =
        when (val result = HostTokenProvisioning.validate(token)) {
            is HostTokenProvisioningResult.Valid -> {
                provisionDeviceToken(result.token)
                fallbackValidation
            }

            is HostTokenProvisioningResult.Invalid ->
                SetupIntentValidation(
                    accepted = false,
                    autoStartServer = false,
                    reason = result.message,
                )
        }
}
