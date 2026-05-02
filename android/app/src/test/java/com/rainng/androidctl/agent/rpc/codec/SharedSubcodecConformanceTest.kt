package com.rainng.androidctl.agent.rpc.codec

import com.rainng.androidctl.agent.actions.ActionRequestCodec
import com.rainng.androidctl.agent.actions.ActionResult
import com.rainng.androidctl.agent.actions.ActionResultCodec
import com.rainng.androidctl.agent.actions.ActionResultStatus
import com.rainng.androidctl.agent.actions.ActionTargetCodec
import com.rainng.androidctl.agent.events.DeviceEvent
import com.rainng.androidctl.agent.events.DeviceEventCodec
import com.rainng.androidctl.agent.events.FocusChangedPayload
import com.rainng.androidctl.agent.events.ImeChangedPayload
import com.rainng.androidctl.agent.events.PackageChangedPayload
import com.rainng.androidctl.agent.events.WindowChangedPayload
import com.rainng.androidctl.agent.runtime.ObservedWindowState
import com.rainng.androidctl.agent.snapshot.SnapshotDisplay
import com.rainng.androidctl.agent.snapshot.SnapshotIme
import com.rainng.androidctl.agent.snapshot.SnapshotPayload
import com.rainng.androidctl.agent.snapshot.SnapshotResponseCodec
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class SharedSubcodecConformanceTest {
    @Test
    fun actionTargetFragmentDecodesAndEncodesConsistentlyAcrossRequestAndResult() {
        val requestJson =
            JSONObject(
                """
                {
                  "kind":"tap",
                  "target":{"kind":"handle","handle":{"snapshotId":42,"rid":"w1:0.1"}},
                  "options":{"timeoutMs":5000}
                }
                """.trimIndent(),
            )

        val decodedFromRequest = ActionRequestCodec.read(JsonReader.fromObject(requestJson)).target
        val decodedDirectly = ActionTargetCodec.read(JsonReader.fromObject(requestJson.getJSONObject("target")))

        assertEquals(decodedDirectly, decodedFromRequest)

        val resultPayload =
            ActionResult(
                actionId = "act-1",
                status = ActionResultStatus.Done,
                durationMs = 1L,
                resolvedTarget = decodedFromRequest,
                observed = ObservedWindowState(packageName = "com.android.settings", activityName = null),
            )
        val resultJson = writeObject(resultPayload, ActionResultCodec)
        val directTargetJson = writeObject(decodedFromRequest, ActionTargetCodec)

        assertJsonEquals(directTargetJson, resultJson.getJSONObject("resolvedTarget"))
    }

    @Test
    fun displayFragmentEncodingMatchesSnapshotEncoding() {
        val expectedJson = JSONObject("""{"widthPx":1080,"heightPx":2400,"densityDpi":420,"rotation":0}""")
        val snapshotJson =
            writeObject(
                SnapshotPayload(
                    snapshotId = 42L,
                    capturedAt = "2026-03-26T00:00:00Z",
                    packageName = "com.android.settings",
                    activityName = "SettingsActivity",
                    display = SnapshotDisplay(widthPx = 1080, heightPx = 2400, densityDpi = 420, rotation = 0),
                    ime = SnapshotIme(visible = false, windowId = null),
                    windows = emptyList(),
                    nodes = emptyList(),
                ),
                SnapshotResponseCodec,
            ).getJSONObject("display")

        assertJsonEquals(expectedJson, snapshotJson)
    }

    @Test
    fun imeFragmentEncodingMatchesSnapshotAndImeChangedEventEncodings() {
        val expectedJson = JSONObject("""{"visible":false,"windowId":null}""")
        val snapshotJson =
            writeObject(
                SnapshotPayload(
                    snapshotId = 42L,
                    capturedAt = "2026-03-26T00:00:00Z",
                    packageName = "com.android.settings",
                    activityName = "SettingsActivity",
                    display = SnapshotDisplay(widthPx = 1080, heightPx = 2400, densityDpi = 420, rotation = 0),
                    ime = SnapshotIme(visible = false, windowId = null),
                    windows = emptyList(),
                    nodes = emptyList(),
                ),
                SnapshotResponseCodec,
            ).getJSONObject("ime")
        val eventJson =
            writeObject(
                DeviceEvent(
                    seq = 1L,
                    timestamp = "2026-03-27T00:00:00Z",
                    data = ImeChangedPayload(visible = false, windowId = null),
                ),
                DeviceEventCodec,
            ).getJSONObject("data")

        assertJsonEquals(expectedJson, snapshotJson)
        assertJsonEquals(expectedJson, eventJson)
    }

    @Test
    fun foregroundContextFragmentEncodingMatchesActionAndEventPayloads() {
        val expectedJson = JSONObject("""{"packageName":"com.android.settings","activityName":null}""")
        val actionObservedJson =
            writeObject(
                ActionResult(
                    actionId = "act-1",
                    status = ActionResultStatus.Done,
                    durationMs = 1L,
                    resolvedTarget = com.rainng.androidctl.agent.actions.ActionTarget.None,
                    observed = ObservedWindowState(packageName = "com.android.settings", activityName = null),
                ),
                ActionResultCodec,
            ).getJSONObject("observed")
        val packageChangedJson =
            writeObject(
                DeviceEvent(
                    seq = 1L,
                    timestamp = "2026-03-27T00:00:00Z",
                    data = PackageChangedPayload(packageName = "com.android.settings", activityName = null),
                ),
                DeviceEventCodec,
            ).getJSONObject("data")
        val windowChangedJson =
            writeObject(
                DeviceEvent(
                    seq = 2L,
                    timestamp = "2026-03-27T00:00:01Z",
                    data = WindowChangedPayload(packageName = "com.android.settings", activityName = null, reason = "windowStateChanged"),
                ),
                DeviceEventCodec,
            ).getJSONObject("data")
        val focusChangedJson =
            writeObject(
                DeviceEvent(
                    seq = 3L,
                    timestamp = "2026-03-27T00:00:02Z",
                    data = FocusChangedPayload(packageName = "com.android.settings", activityName = null, reason = "focusEntered"),
                ),
                DeviceEventCodec,
            ).getJSONObject("data")

        assertJsonEquals(expectedJson, actionObservedJson)
        assertJsonEquals(expectedJson, packageChangedJson)
        assertJsonEquals(expectedJson, subset(windowChangedJson, "packageName", "activityName"))
        assertEquals("windowStateChanged", windowChangedJson.getString("reason"))
        assertJsonEquals(expectedJson, subset(focusChangedJson, "packageName", "activityName"))
        assertEquals("focusEntered", focusChangedJson.getString("reason"))
    }

    private fun <T> writeObject(
        value: T,
        codec: JsonEncoder<T>,
    ): JSONObject {
        val writer = JsonWriter.objectWriter()
        codec.write(writer, value)
        return writer.toJsonObject()
    }

    private fun subset(
        source: JSONObject,
        vararg keys: String,
    ): JSONObject =
        JSONObject().also { subset ->
            keys.forEach { key ->
                subset.put(key, source.opt(key))
            }
        }

    private fun assertJsonEquals(
        expected: JSONObject,
        actual: JSONObject,
    ) {
        assertEquals(normalize(expected), normalize(actual))
    }

    private fun normalize(value: Any?): Any? =
        when (value) {
            null,
            JSONObject.NULL,
            is String,
            is Boolean,
            is Int,
            is Long,
            is Double,
            is Float,
            -> value

            is JSONObject -> {
                val keys = mutableListOf<String>()
                val iterator = value.keys()
                while (iterator.hasNext()) {
                    keys.add(iterator.next())
                }
                keys.sorted().associateWith { key -> normalize(value.get(key)) }
            }

            is JSONArray -> List(value.length()) { index -> normalize(value.get(index)) }
            else -> error("unsupported JSON value type: ${value::class.java.name}")
        }
}
