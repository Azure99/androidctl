package com.rainng.androidctl.agent.device

import com.rainng.androidctl.agent.rpc.codec.JsonWriter
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AppsListResponseCodecTest {
    @Test
    fun writeEncodesV1SchemaOnly() {
        val label = "Settings \"Main\"\nTools & More"
        val payload =
            encode(
                AppsListResponse(
                    apps =
                        listOf(
                            AppEntryResponse(
                                packageName = "com.android.settings",
                                appLabel = label,
                                launchable = true,
                            ),
                        ),
                ),
            )

        assertEquals(setOf("apps"), payload.keySetFromIterator())
        val apps = payload.getJSONArray("apps")
        assertEquals(1, apps.length())

        val app = apps.getJSONObject(0)
        assertEquals(setOf("packageName", "appLabel", "launchable"), app.keySetFromIterator())
        assertEquals("com.android.settings", app.getString("packageName"))
        assertEquals(label, app.getString("appLabel"))
        assertTrue(app.getBoolean("launchable"))
        assertFalse(app.has("activityName"))
        assertFalse(app.has("icon"))
        assertFalse(app.has("activities"))
        assertFalse(app.has("installed"))
        assertFalse(app.has("packageLabel"))
    }

    @Test
    fun writeEncodesEmptyAppsArray() {
        val payload = encode(AppsListResponse(apps = emptyList()))

        assertEquals(setOf("apps"), payload.keySetFromIterator())
        assertEquals(0, payload.getJSONArray("apps").length())
    }

    private fun encode(response: AppsListResponse): JSONObject {
        val writer = JsonWriter.objectWriter()
        AppsListResponseCodec.write(writer, response)
        return JSONObject(writer.toJsonObject().toString())
    }

    private fun JSONObject.keySetFromIterator(): Set<String> = keys().asSequence().toSet()
}
