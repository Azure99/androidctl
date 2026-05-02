package com.rainng.androidctl.agent.runtime

import android.content.Context
import com.rainng.androidctl.agent.auth.DeviceTokenLoadResult
import com.rainng.androidctl.agent.auth.DeviceTokenStore

internal interface DeviceTokenStoreAccess {
    fun initialize(context: Context)

    fun loadCurrentToken(): DeviceTokenLoadResult

    fun regenerateToken(): String

    fun replaceToken(token: String): String = throw UnsupportedOperationException("device token replacement is not configured")
}

internal fun defaultDeviceTokenStoreAccess(): DeviceTokenStoreAccess =
    object : DeviceTokenStoreAccess {
        override fun initialize(context: Context) {
            DeviceTokenStore.initialize(context)
        }

        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenStore.loadCurrentToken()

        override fun regenerateToken(): String = DeviceTokenStore.regenerateToken()

        override fun replaceToken(token: String): String = DeviceTokenStore.replaceToken(token)
    }

internal class DeviceTokenCoordinator(
    var deviceTokenStoreAccess: DeviceTokenStoreAccess = defaultDeviceTokenStoreAccess(),
) {
    fun initialize(context: Context) {
        deviceTokenStoreAccess.initialize(context)
    }

    fun loadCurrentToken(): DeviceTokenLoadResult = deviceTokenStoreAccess.loadCurrentToken()

    fun regenerateToken(): String = deviceTokenStoreAccess.regenerateToken()

    fun replaceToken(token: String): String = deviceTokenStoreAccess.replaceToken(token)
}
