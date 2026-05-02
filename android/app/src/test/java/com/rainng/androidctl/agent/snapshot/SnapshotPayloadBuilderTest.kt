package com.rainng.androidctl.agent.snapshot

import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.rpc.codec.JsonWriter
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SnapshotPayloadBuilderTest {
    @Test
    fun buildsSchemaWithFlatNodes() {
        val payload = encode(samplePayload())

        assertEquals(42L, payload.getLong("snapshotId"))
        assertEquals("com.android.settings", payload.getString("packageName"))
        assertTrue(payload.getJSONObject("display").has("widthPx"))
        assertEquals(1, payload.getJSONArray("windows").length())
        assertEquals(2, payload.getJSONArray("nodes").length())
    }

    @Test
    fun preservesParentAndChildRelationshipsInFlatGraph() {
        val payload = encode(samplePayload())
        val nodes = payload.getJSONArray("nodes")
        val parent = nodes.getJSONObject(0)
        val child = nodes.getJSONObject(1)

        assertEquals("w1:0", parent.getString("rid"))
        assertEquals("w1:0.0", child.getString("rid"))
        assertEquals("w1:0", child.getString("parentRid"))
        assertEquals("w1:0.0", parent.getJSONArray("childRids").getString(0))
    }

    @Test
    fun preservesNullableForegroundImeAndPackageNameFieldsAsExplicitJsonNull() {
        val samplePayload = samplePayload()
        val payload =
            encode(
                samplePayload.copy(
                    packageName = null,
                    activityName = null,
                    windows = listOf(samplePayload.windows.single().copy(packageName = null)),
                    nodes =
                        listOf(
                            samplePayload.nodes.first(),
                            samplePayload.nodes[1].copy(
                                packageName = null,
                            ),
                        ),
                ),
            )

        assertTrue(payload.has("packageName"))
        assertTrue(payload.isNull("packageName"))
        assertTrue(payload.has("activityName"))
        assertTrue(payload.isNull("activityName"))

        val ime = payload.getJSONObject("ime")
        assertTrue(ime.has("windowId"))
        assertTrue(ime.isNull("windowId"))

        val window = payload.getJSONArray("windows").getJSONObject(0)
        assertTrue(window.has("packageName"))
        assertTrue(window.isNull("packageName"))

        val root = payload.getJSONArray("nodes").getJSONObject(0)
        assertTrue(root.has("parentRid"))
        assertTrue(root.isNull("parentRid"))
        val child = payload.getJSONArray("nodes").getJSONObject(1)
        assertTrue(child.has("packageName"))
        assertTrue(child.isNull("packageName"))
    }

    @Test
    fun preservesNullableNodeContentFieldsAndKeepsClassNameString() {
        val samplePayload = samplePayload()
        val payload =
            encode(
                samplePayload.copy(
                    nodes =
                        listOf(
                            samplePayload.nodes.first(),
                            samplePayload.nodes[1].copy(
                                resourceId = null,
                                text = null,
                                contentDesc = null,
                                hintText = null,
                                stateDescription = null,
                                paneTitle = null,
                            ),
                        ),
                ),
            )

        val child = payload.getJSONArray("nodes").getJSONObject(1)
        assertEquals("android.widget.Switch", child.getString("className"))
        listOf(
            "resourceId",
            "text",
            "contentDesc",
            "hintText",
            "stateDescription",
            "paneTitle",
        ).forEach { field ->
            assertTrue(child.has(field))
            assertTrue(child.isNull(field))
        }
    }

    @Test
    fun publicationNormalizesBlankClassNameAtPublicationBoundary() {
        val samplePayload = samplePayload()
        val publication =
            publication(
                samplePayload.copy(
                    nodes =
                        listOf(
                            samplePayload.nodes.first().copy(className = "   "),
                            samplePayload.nodes[1],
                        ),
                ),
            )

        assertEquals(
            "android.view.View",
            publication.response.nodes
                .first()
                .className,
        )
        assertEquals(
            "android.view.View",
            publication.registryRecord.ridToHandle
                .getValue("w1:0")
                .fingerprint.className,
        )
    }

    @Test
    fun throwsNoActiveWindowWhenPayloadHasNoWindows() {
        try {
            publication(
                samplePayload().copy(
                    windows = emptyList(),
                    nodes = emptyList(),
                ),
            )
        } catch (error: SnapshotException) {
            assertEquals(RpcErrorCode.NO_ACTIVE_WINDOW, error.code)
            assertTrue(error.retryable)
            return
        }

        throw AssertionError("expected SnapshotException")
    }

    @Test
    fun throwsSnapshotUnavailableWhenPayloadHasNoNodes() {
        try {
            publication(
                samplePayload().copy(
                    nodes = emptyList(),
                ),
            )
        } catch (error: SnapshotException) {
            assertEquals(RpcErrorCode.SNAPSHOT_UNAVAILABLE, error.code)
            assertEquals("snapshot capture produced no nodes", error.message)
            assertTrue(error.retryable)
            return
        }

        throw AssertionError("expected SnapshotException")
    }

    @Test
    fun fingerprintIgnoresVolatileUiState() {
        val original = samplePayload().nodes[1]
        val changed =
            original.copy(
                text = "Bluetooth",
                stateDescription = "Off",
                bounds = listOf(80, 300, 960, 420),
                focused = true,
                checked = false,
                selected = true,
            )

        assertEquals(
            NodeFingerprint.fromSnapshotNode(original),
            NodeFingerprint.fromSnapshotNode(changed),
        )
    }

    @Test
    fun fingerprintChangesWhenResourceIdIsMissingAndClassNameDrifts() {
        val original = samplePayload().nodes[1].copy(resourceId = null)
        val changed =
            original.copy(
                className = "android.widget.TextView",
                text = "Wi-Fi",
                stateDescription = "Off",
            )

        assertNotEquals(
            NodeFingerprint.fromSnapshotNode(original),
            NodeFingerprint.fromSnapshotNode(changed),
        )
    }

    private fun samplePayload(): SnapshotPayload =
        SnapshotPayload(
            snapshotId = 42L,
            capturedAt = "2026-03-15T00:00:00Z",
            packageName = "com.android.settings",
            activityName = "SettingsActivity",
            display =
                SnapshotDisplay(
                    widthPx = 1080,
                    heightPx = 2400,
                    densityDpi = 420,
                    rotation = 0,
                ),
            ime =
                SnapshotIme(
                    visible = false,
                    windowId = null,
                ),
            windows =
                listOf(
                    SnapshotWindow(
                        windowId = "w1",
                        type = "application",
                        layer = 0,
                        packageName = "com.android.settings",
                        bounds = listOf(0, 0, 1080, 2400),
                        rootRid = "w1:0",
                    ),
                ),
            nodes =
                listOf(
                    SnapshotNode(
                        rid = "w1:0",
                        windowId = "w1",
                        parentRid = null,
                        childRids = listOf("w1:0.0"),
                        className = "android.widget.FrameLayout",
                        resourceId = null,
                        text = null,
                        contentDesc = null,
                        hintText = null,
                        stateDescription = null,
                        paneTitle = null,
                        packageName = "com.android.settings",
                        bounds = listOf(0, 0, 1080, 2400),
                        visibleToUser = true,
                        importantForAccessibility = true,
                        clickable = false,
                        enabled = true,
                        editable = false,
                        focusable = false,
                        focused = false,
                        checkable = false,
                        checked = false,
                        selected = false,
                        scrollable = false,
                        password = false,
                        actions = emptyList(),
                    ),
                    SnapshotNode(
                        rid = "w1:0.0",
                        windowId = "w1",
                        parentRid = "w1:0",
                        childRids = emptyList(),
                        className = "android.widget.Switch",
                        resourceId = "android:id/switch_widget",
                        text = "Wi-Fi",
                        contentDesc = null,
                        hintText = null,
                        stateDescription = "On",
                        paneTitle = null,
                        packageName = "com.android.settings",
                        bounds = listOf(100, 320, 980, 410),
                        visibleToUser = true,
                        importantForAccessibility = true,
                        clickable = true,
                        enabled = true,
                        editable = false,
                        focusable = true,
                        focused = false,
                        checkable = true,
                        checked = true,
                        selected = false,
                        scrollable = false,
                        password = false,
                        actions = listOf("click"),
                    ),
                ),
        )

    private fun publication(payload: SnapshotPayload): SnapshotPublication =
        SnapshotPublication.create(
            response = payload,
            registryRecord =
                SnapshotRecord(
                    snapshotId = payload.snapshotId,
                    ridToHandle = buildHandles(payload),
                ),
            generation = 0L,
        )

    private fun encode(payload: SnapshotPayload): org.json.JSONObject {
        val writer = JsonWriter.objectWriter()
        SnapshotResponseCodec.write(writer, payload)
        return writer.toJsonObject()
    }

    private fun buildHandles(payload: SnapshotPayload): Map<String, SnapshotNodeHandle> {
        val root = payload.nodes.firstOrNull()
        val child = payload.nodes.getOrNull(1)
        return buildMap {
            if (root != null) {
                put(
                    "w1:0",
                    SnapshotNodeHandle(
                        path = NodePath("w1", emptyList()),
                        fingerprint = NodeFingerprint.fromSnapshotNode(root),
                    ),
                )
            }
            if (child != null) {
                put(
                    "w1:0.0",
                    SnapshotNodeHandle(
                        path = NodePath("w1", listOf(0)),
                        fingerprint = NodeFingerprint.fromSnapshotNode(child),
                    ),
                )
            }
        }
    }
}
