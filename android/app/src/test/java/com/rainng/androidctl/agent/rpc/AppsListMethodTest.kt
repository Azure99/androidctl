package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.device.AppEntryResponse
import com.rainng.androidctl.agent.device.AppsListResponse
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AppsListMethodTest {
    @Test
    fun policyUsesExpectedDefaults() {
        val method = AppsListMethod(appsListProvider = { fakeResponse() })

        assertFalse(method.policy.requiresReadyRuntime)
        assertEquals("INTERNAL_ERROR", method.policy.timeoutError.name)
        assertEquals("apps.list timed out", method.policy.timeoutMessage)
    }

    @Test
    fun prepareDefersProviderUntilExecuteAndEncodesResponse() {
        var providerCalls = 0
        val method =
            AppsListMethod(
                appsListProvider = {
                    providerCalls += 1
                    fakeResponse()
                },
            )
        val prepared = method.prepare(request())

        assertEquals(0, providerCalls)
        val payload = prepared.executeEncoded()
        val apps = payload.getJSONArray("apps")

        assertEquals(1, providerCalls)
        assertEquals(5000L, prepared.timeoutMs)
        assertEquals(2, apps.length())
        assertEquals("com.android.settings", apps.getJSONObject(0).getString("packageName"))
        assertEquals("Settings", apps.getJSONObject(0).getString("appLabel"))
        assertEquals(true, apps.getJSONObject(0).getBoolean("launchable"))
        assertEquals("com.android.chrome", apps.getJSONObject(1).getString("packageName"))
        assertEquals("Chrome", apps.getJSONObject(1).getString("appLabel"))
        assertEquals(true, apps.getJSONObject(1).getBoolean("launchable"))
        assertTrue((0 until apps.length()).all { apps.getJSONObject(it).getBoolean("launchable") })
    }

    @Test
    fun prepareEncodesEmptyAppsResponse() {
        var providerCalls = 0
        val method =
            AppsListMethod(
                appsListProvider = {
                    providerCalls += 1
                    AppsListResponse(apps = emptyList())
                },
            )

        val payload = method.prepare(request()).executeEncoded()

        assertEquals(1, providerCalls)
        assertEquals(0, payload.getJSONArray("apps").length())
    }

    private fun request(): RpcRequestEnvelope = RpcRequestEnvelope(id = "req-apps", method = "apps.list", params = JSONObject())

    private fun fakeResponse(): AppsListResponse =
        AppsListResponse(
            apps =
                listOf(
                    AppEntryResponse(packageName = "com.android.settings", appLabel = "Settings", launchable = true),
                    AppEntryResponse(packageName = "com.android.chrome", appLabel = "Chrome", launchable = true),
                ),
        )
}
