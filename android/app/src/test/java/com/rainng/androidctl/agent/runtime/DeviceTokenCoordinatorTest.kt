package com.rainng.androidctl.agent.runtime

import android.content.Context
import com.rainng.androidctl.agent.auth.DeviceTokenLoadResult
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Test
import org.mockito.Mockito.mock

class DeviceTokenCoordinatorTest {
    @Test
    fun delegatesInitializeAndTokenOperationsToConfiguredAccess() {
        val context = mock(Context::class.java)
        var initializedContext: Context? = null
        val coordinator =
            DeviceTokenCoordinator(
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) {
                            initializedContext = context
                        }

                        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                        override fun regenerateToken(): String = "token-2"

                        override fun replaceToken(token: String): String = "replaced:$token"
                    },
            )

        coordinator.initialize(context)

        assertSame(context, initializedContext)
        assertEquals(DeviceTokenLoadResult.Available("token-1"), coordinator.loadCurrentToken())
        assertEquals("token-2", coordinator.regenerateToken())
        assertEquals("replaced:host-token", coordinator.replaceToken("host-token"))
    }
}
