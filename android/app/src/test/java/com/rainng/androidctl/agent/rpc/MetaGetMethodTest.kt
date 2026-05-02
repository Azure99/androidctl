package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.BuildConfig
import com.rainng.androidctl.agent.actions.ActionKind
import com.rainng.androidctl.agent.rpc.codec.JsonWriter
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class MetaGetMethodTest {
    @Test
    fun policyUsesExpectedDefaults() {
        val method = MetaGetMethod(versionProvider = { "1.0.0" })

        assertFalse(method.policy.requiresReadyRuntime)
        assertEquals("INTERNAL_ERROR", method.policy.timeoutError.name)
        assertEquals("meta.get timed out", method.policy.timeoutMessage)
    }

    @Test
    fun prepareBuildsMetaPayloadWithoutInvokingProviderUntilExecute() {
        var versionCalls = 0
        val method =
            MetaGetMethod(
                versionProvider = {
                    versionCalls += 1
                    "1.2.3"
                },
            )
        val prepared = method.prepare(request())

        assertEquals(0, versionCalls)
        assertEquals(1000L, prepared.timeoutMs)
        val payload = prepared.executeEncoded()

        assertEquals("androidctl-device-agent", payload.getString("service"))
        assertEquals("1.2.3", payload.getString("version"))
        assertEquals(true, payload.getJSONObject("capabilities").getBoolean("supportsEventsPoll"))
        assertEquals(1, versionCalls)
        assertEquals(
            ActionKind.capabilityWireNames(),
            jsonArrayStrings(payload.getJSONObject("capabilities").getJSONArray("actionKinds")),
        )
    }

    @Test
    fun prepareWithDefaultEnvironmentVersionProviderReturnsBuildConfigVersionName() {
        val payload =
            MetaGetMethod(versionProvider = RpcEnvironment().versionProvider)
                .prepare(request())
                .executeEncoded()

        assertEquals(BuildConfig.VERSION_NAME, payload.getString("version"))
    }

    @Test
    fun codecWritesMetaResponseShape() {
        val writer = JsonWriter.objectWriter()
        val response =
            MetaResponse(
                service = "androidctl-device-agent",
                version = "1.2.3",
                capabilities =
                    MetaCapabilities(
                        supportsEventsPoll = true,
                        supportsScreenshot = true,
                        actionKinds = ActionKind.capabilityWireNames(),
                    ),
            )

        MetaResponseCodec.write(writer, response)

        val payload = writer.toJsonObject()
        assertEquals("androidctl-device-agent", payload.getString("service"))
        assertEquals("1.2.3", payload.getString("version"))
        assertEquals(true, payload.getJSONObject("capabilities").getBoolean("supportsEventsPoll"))
        assertEquals(true, payload.getJSONObject("capabilities").getBoolean("supportsScreenshot"))
        assertEquals(
            ActionKind.capabilityWireNames(),
            jsonArrayStrings(payload.getJSONObject("capabilities").getJSONArray("actionKinds")),
        )
    }

    private fun request(): RpcRequestEnvelope = RpcRequestEnvelope(id = "req-meta", method = "meta.get", params = JSONObject())

    private fun jsonArrayStrings(array: JSONArray): List<String> = List(array.length(), array::getString)
}
