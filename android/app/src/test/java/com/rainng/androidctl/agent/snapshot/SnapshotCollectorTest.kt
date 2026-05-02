package com.rainng.androidctl.agent.snapshot

import android.accessibilityservice.AccessibilityService
import android.content.res.Resources
import android.graphics.Rect
import android.util.DisplayMetrics
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.rpc.codec.JsonWriter
import com.rainng.androidctl.agent.runtime.AccessibilityForegroundObservationProvider
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge
import com.rainng.androidctl.agent.runtime.ForegroundObservation
import com.rainng.androidctl.agent.runtime.ForegroundObservationProvider
import com.rainng.androidctl.agent.runtime.ObservedWindowState
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.doAnswer
import org.mockito.Mockito.mock
import org.mockito.Mockito.never
import org.mockito.Mockito.verify
import org.mockito.Mockito.`when`

class SnapshotCollectorTest {
    @Before
    fun setUp() {
        AgentRuntimeBridge.resetForTest()
        SnapshotRegistry.resetForTest()
    }

    @After
    fun tearDown() {
        AgentRuntimeBridge.resetForTest()
        SnapshotRegistry.resetForTest()
    }

    @Test
    fun collectFiltersSystemWindowsSkipsNullRootsAndBuildsDeterministicHandles() {
        val root =
            node(
                packageName = "com.example.app",
                className = "android.widget.FrameLayout",
                resourceId = "root",
                children =
                    listOf(
                        node(
                            packageName = "com.example.app",
                            className = "android.widget.TextView",
                            resourceId = "title",
                            text = "Hello",
                        ),
                    ),
            )
        val collector =
            newCollector(
                windows =
                    listOf(
                        window(
                            id = 10,
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            layer = 2,
                            root = root,
                            bounds = Rect(0, 0, 1080, 2400),
                            displayId = 7,
                        ),
                        window(
                            id = 11,
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            layer = 1,
                            root = null,
                            bounds = Rect(0, 0, 1080, 2400),
                        ),
                        window(
                            id = 12,
                            type = AccessibilityWindowInfo.TYPE_SYSTEM,
                            layer = 3,
                            root = node(packageName = "android"),
                            bounds = Rect(0, 0, 1080, 120),
                        ),
                    ),
                widthPx = 1080,
                heightPx = 2400,
                densityDpi = 420,
                displayRotations =
                    mapOf(
                        7 to 3,
                        Display.DEFAULT_DISPLAY to 1,
                    ),
                actionIdsForNode = { node ->
                    if (node === root) listOf(AccessibilityNodeInfo.ACTION_CLICK, 999) else emptyList()
                },
            )

        val publication =
            collector.collect(
                includeInvisible = false,
                includeSystemWindows = false,
            )
        val payload = encode(publication.response)

        assertEquals("com.example.app", payload.getString("packageName"))
        assertTrue(payload.has("activityName"))
        assertTrue(payload.isNull("activityName"))
        assertEquals(1080, payload.getJSONObject("display").getInt("widthPx"))
        assertEquals(2400, payload.getJSONObject("display").getInt("heightPx"))
        assertEquals(420, payload.getJSONObject("display").getInt("densityDpi"))
        assertEquals(3, payload.getJSONObject("display").getInt("rotation"))

        val windows = payload.getJSONArray("windows")
        assertEquals(1, windows.length())
        assertEquals("w10", windows.getJSONObject(0).getString("windowId"))
        assertEquals("application", windows.getJSONObject(0).getString("type"))
        assertEquals("com.example.app", windows.getJSONObject(0).getString("packageName"))
        assertEquals("w10:0", windows.getJSONObject(0).getString("rootRid"))

        val nodesByRid = payloadNodesByRid(payload)
        assertNotNull(nodesByRid["w10:0"])
        assertNotNull(nodesByRid["w10:0.0"])
        assertEquals("w10:0", nodesByRid.getValue("w10:0.0").getString("parentRid"))
        assertEquals(listOf("w10:0.0"), jsonArrayStrings(nodesByRid.getValue("w10:0").getJSONArray("childRids")))
        assertEquals(
            listOf("click", "action_999"),
            jsonArrayStrings(nodesByRid.getValue("w10:0").getJSONArray("actions")),
        )

        assertEquals(SnapshotRegistry.currentGeneration(), publication.generation)
        assertNull(SnapshotRegistry.find(publication.response.snapshotId))
        val rootHandle = publication.registryRecord.ridToHandle["w10:0"]
        val childHandle = publication.registryRecord.ridToHandle["w10:0.0"]
        assertEquals(NodePath("w10", emptyList()), rootHandle?.path)
        assertEquals(NodePath("w10", listOf(0)), childHandle?.path)
        assertEquals("com.example.app", childHandle?.fingerprint?.packageName)
        assertEquals("android.widget.TextView", childHandle?.fingerprint?.className)
        assertEquals("title", childHandle?.fingerprint?.resourceId)
    }

    @Test
    fun collectNormalizesNullClassNameIntoPublishedPayloadAndRetainedFingerprint() {
        val collector =
            newCollector(
                windows =
                    listOf(
                        window(
                            id = 50,
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            root =
                                node(
                                    packageName = "com.example.app",
                                    className = "android.widget.FrameLayout",
                                    children =
                                        listOf(
                                            node(
                                                packageName = "com.example.app",
                                                className = null,
                                                resourceId = "missing_class",
                                            ),
                                        ),
                                ),
                        ),
                    ),
            )

        val publication =
            collector.collect(
                includeInvisible = false,
                includeSystemWindows = false,
            )
        val payload = encode(publication.response)
        val childNode = payloadNodesByRid(payload).getValue("w50:0.0")

        assertEquals("android.view.View", childNode.getString("className"))
        assertEquals(
            "android.view.View",
            publication
                .registryRecord
                .ridToHandle
                .getValue("w50:0.0")
                .fingerprint
                .className,
        )
    }

    @Test
    @Suppress("DEPRECATION")
    fun collectExcludesInvisibleNodesWhenRequested() {
        val invisibleChild =
            node(
                packageName = "com.example.app",
                className = "android.widget.ImageView",
                resourceId = "hidden",
                visibleToUser = false,
            )
        val collector =
            newCollector(
                windows =
                    listOf(
                        window(
                            id = 20,
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            root =
                                node(
                                    packageName = "com.example.app",
                                    children = listOf(invisibleChild),
                                ),
                        ),
                    ),
            )

        val payload =
            encode(
                collector
                    .collect(
                        includeInvisible = false,
                        includeSystemWindows = false,
                    ).response,
            )

        assertFalse(payloadNodesByRid(payload).containsKey("w20:0.0"))
        verify(invisibleChild).recycle()
    }

    @Test
    @Suppress("DEPRECATION")
    fun collectOmitsWindowWhenRootIsFilteredOut() {
        val invisibleRoot =
            node(
                packageName = "com.example.app",
                className = "android.widget.FrameLayout",
                resourceId = "root",
                visibleToUser = false,
            )
        val collector =
            newCollector(
                windows =
                    listOf(
                        window(
                            id = 22,
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            root = invisibleRoot,
                        ),
                    ),
                foregroundObservationProvider =
                    object : ForegroundObservationProvider {
                        override fun observe(): ForegroundObservation =
                            ForegroundObservation(
                                state = ObservedWindowState(packageName = "com.example.app"),
                                interactive = true,
                            )
                    },
            )

        try {
            collector.collect(
                includeInvisible = false,
                includeSystemWindows = false,
            )
            fail("expected SnapshotException")
        } catch (error: SnapshotException) {
            assertEquals(RpcErrorCode.NO_ACTIVE_WINDOW, error.code)
            assertEquals("no active accessibility window is available", error.message)
            assertTrue(error.retryable)
            verify(invisibleRoot).recycle()
        }
    }

    @Test
    fun collectRetainsInvisibleNodesWhenRequested() {
        val collector =
            newCollector(
                windows =
                    listOf(
                        window(
                            id = 21,
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            root =
                                node(
                                    packageName = "com.example.app",
                                    children =
                                        listOf(
                                            node(
                                                packageName = "com.example.app",
                                                className = "android.widget.ImageView",
                                                resourceId = "hidden",
                                                visibleToUser = false,
                                            ),
                                        ),
                                ),
                        ),
                    ),
            )

        val payload =
            encode(
                collector
                    .collect(
                        includeInvisible = true,
                        includeSystemWindows = false,
                    ).response,
            )

        val hiddenNode = payloadNodesByRid(payload).getValue("w21:0.0")
        assertFalse(hiddenNode.getBoolean("visibleToUser"))
        assertEquals("hidden", hiddenNode.getString("resourceId"))
    }

    @Test
    fun collectReturnsNoActiveWindowWhenDeviceIsNotInteractive() {
        val service = mock(AccessibilityService::class.java)
        val collector =
            SnapshotCollector(
                service = service,
                foregroundObservationProvider =
                    object : ForegroundObservationProvider {
                        override fun observe(): ForegroundObservation =
                            ForegroundObservation(
                                interactive = false,
                            )
                    },
            )

        try {
            collector.collect(
                includeInvisible = true,
                includeSystemWindows = true,
            )
            fail("expected SnapshotException")
        } catch (error: SnapshotException) {
            assertEquals(RpcErrorCode.NO_ACTIVE_WINDOW, error.code)
            assertEquals("device screen is not interactive", error.message)
        }

        verify(service, never()).windows
    }

    @Test
    fun collectPrefersResolvedForegroundPackageOverObservedSystemUiPackage() {
        AgentRuntimeBridge.recordObservedWindowState(
            eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.android.systemui",
            windowClassName = "com.android.systemui.SomeOverlay",
        )
        val collector =
            newCollector(
                windows =
                    listOf(
                        window(
                            id = 30,
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            layer = 1,
                            root = node(packageName = "com.root.package"),
                        ),
                        window(
                            id = 31,
                            type = AccessibilityWindowInfo.TYPE_SYSTEM,
                            layer = 5,
                            root = node(packageName = "com.android.systemui"),
                        ),
                    ),
            )

        val payload =
            encode(
                collector
                    .collect(
                        includeInvisible = false,
                        includeSystemWindows = false,
                    ).response,
            )

        assertEquals("com.root.package", payload.getString("packageName"))
        assertTrue(payload.has("activityName"))
        assertTrue(payload.isNull("activityName"))
    }

    @Test
    fun collectKeepsTrustedObservedActivityWhenForegroundPackageMatches() {
        AgentRuntimeBridge.recordObservedWindowState(
            eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.root.package",
            windowClassName = "com.root.package.MainActivity",
        )
        val collector =
            newCollector(
                windows =
                    listOf(
                        window(
                            id = 32,
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            root = node(packageName = "com.root.package"),
                        ),
                    ),
            )

        val payload =
            encode(
                collector
                    .collect(
                        includeInvisible = false,
                        includeSystemWindows = false,
                    ).response,
            )

        assertEquals("com.root.package", payload.getString("packageName"))
        assertEquals("com.root.package.MainActivity", payload.getString("activityName"))
    }

    @Test
    fun collectRejectsCrossPackageActivityShapedObservedHint() {
        AgentRuntimeBridge.recordObservedWindowState(
            eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.root.package",
            windowClassName = "com.fake.overlay.SomeActivity",
        )
        val collector =
            newCollector(
                windows =
                    listOf(
                        window(
                            id = 38,
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            root = node(packageName = "com.root.package"),
                        ),
                    ),
            )

        val payload =
            encode(
                collector
                    .collect(
                        includeInvisible = false,
                        includeSystemWindows = false,
                    ).response,
            )

        assertEquals("com.root.package", payload.getString("packageName"))
        assertTrue(payload.has("activityName"))
        assertTrue(payload.isNull("activityName"))
    }

    @Test
    fun collectRejectsSharedPrefixForeignPackageObservedHint() {
        AgentRuntimeBridge.recordObservedWindowState(
            eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.root.package",
            windowClassName = "com.root.packagehelper.SomeActivity",
        )
        val collector =
            newCollector(
                windows =
                    listOf(
                        window(
                            id = 39,
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            root = node(packageName = "com.root.package"),
                        ),
                    ),
            )

        val payload =
            encode(
                collector
                    .collect(
                        includeInvisible = false,
                        includeSystemWindows = false,
                    ).response,
            )

        assertEquals("com.root.package", payload.getString("packageName"))
        assertTrue(payload.has("activityName"))
        assertTrue(payload.isNull("activityName"))
    }

    @Test
    fun collectKeepsTrustedActivityWhenImeHintDoesNotMatchResolvedPackage() {
        AgentRuntimeBridge.recordObservedWindowState(
            eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.google.android.settings.intelligence",
            windowClassName = "com.google.android.settings.intelligence.modules.search.activity.SearchActivity",
        )
        AgentRuntimeBridge.recordObservedWindowState(
            eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.google.android.inputmethod.latin",
            windowClassName = "android.inputmethodservice.SoftInputWindow",
        )
        val collector =
            newCollector(
                windows =
                    listOf(
                        window(
                            id = 35,
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            root = node(packageName = "com.google.android.settings.intelligence"),
                        ),
                        window(
                            id = 36,
                            type = AccessibilityWindowInfo.TYPE_INPUT_METHOD,
                            root = node(packageName = "com.google.android.inputmethod.latin"),
                        ),
                    ),
            )

        val payload =
            encode(
                collector
                    .collect(
                        includeInvisible = false,
                        includeSystemWindows = true,
                    ).response,
            )

        assertEquals("com.google.android.settings.intelligence", payload.getString("packageName"))
        assertTrue(payload.has("activityName"))
        assertTrue(payload.isNull("activityName"))
    }

    @Test
    fun collectFallsBackToDefaultDisplayWhenWindowDisplayIsUnavailable() {
        val collector =
            newCollector(
                windows =
                    listOf(
                        window(
                            id = 33,
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            root = node(packageName = "com.root.package"),
                            displayId = 9,
                        ),
                    ),
                displayRotations = mapOf(Display.DEFAULT_DISPLAY to 2),
            )

        val payload =
            encode(
                collector
                    .collect(
                        includeInvisible = false,
                        includeSystemWindows = false,
                    ).response,
            )

        assertEquals(2, payload.getJSONObject("display").getInt("rotation"))
    }

    @Test
    fun collectUsesSingleDisplaySourceForAllDisplayMetadata() {
        val collector =
            newCollector(
                windows =
                    listOf(
                        window(
                            id = 37,
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            root = node(packageName = "com.root.package"),
                            displayId = 9,
                        ),
                    ),
                widthPx = 1080,
                heightPx = 2400,
                densityDpi = 420,
                displayInfos =
                    mapOf(
                        9 to SnapshotDisplay(widthPx = 1440, heightPx = 3120, densityDpi = 560, rotation = 3),
                        Display.DEFAULT_DISPLAY to
                            SnapshotDisplay(widthPx = 1080, heightPx = 2400, densityDpi = 420, rotation = 1),
                    ),
            )

        val payload =
            encode(
                collector
                    .collect(
                        includeInvisible = false,
                        includeSystemWindows = false,
                    ).response,
            )

        val display = payload.getJSONObject("display")
        assertEquals(1440, display.getInt("widthPx"))
        assertEquals(3120, display.getInt("heightPx"))
        assertEquals(560, display.getInt("densityDpi"))
        assertEquals(3, display.getInt("rotation"))
    }

    @Test
    fun collectFallsBackToZeroRotationWhenNoDisplayIsAvailable() {
        val collector =
            newCollector(
                windows =
                    listOf(
                        window(
                            id = 34,
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            root = node(packageName = "com.root.package"),
                            displayId = 9,
                        ),
                    ),
                displayRotations = emptyMap(),
            )

        val payload =
            encode(
                collector
                    .collect(
                        includeInvisible = false,
                        includeSystemWindows = false,
                    ).response,
            )

        assertEquals(0, payload.getJSONObject("display").getInt("rotation"))
    }

    @Test
    fun collectMapsWindowTypesAndActionNames() {
        val actionRoot = node(packageName = "com.example.app")
        val windows =
            listOf(
                window(
                    id = 40,
                    type = AccessibilityWindowInfo.TYPE_APPLICATION,
                    root = actionRoot,
                ),
                window(id = 41, type = AccessibilityWindowInfo.TYPE_INPUT_METHOD, root = node(packageName = "ime.package")),
                window(id = 42, type = AccessibilityWindowInfo.TYPE_SYSTEM, root = node(packageName = "android")),
                window(
                    id = 43,
                    type = AccessibilityWindowInfo.TYPE_ACCESSIBILITY_OVERLAY,
                    root = node(packageName = "overlay.package"),
                ),
                window(
                    id = 44,
                    type = AccessibilityWindowInfo.TYPE_SPLIT_SCREEN_DIVIDER,
                    root = node(packageName = "divider.package"),
                ),
                window(
                    id = 45,
                    type = AccessibilityWindowInfo.TYPE_MAGNIFICATION_OVERLAY,
                    root = node(packageName = "magnification.package"),
                ),
                window(id = 46, type = 999, root = node(packageName = "unknown.package")),
            )
        val collector =
            newCollector(
                windows = windows,
                actionIdsForNode = { node ->
                    if (node === actionRoot) {
                        listOf(
                            AccessibilityNodeInfo.ACTION_CLICK,
                            AccessibilityNodeInfo.ACTION_LONG_CLICK,
                            AccessibilityNodeInfo.ACTION_FOCUS,
                            AccessibilityNodeInfo.ACTION_SCROLL_FORWARD,
                            AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD,
                            AccessibilityNodeInfo.ACTION_SET_TEXT,
                            AccessibilityNodeInfo.ACTION_DISMISS,
                            321,
                        )
                    } else {
                        emptyList()
                    }
                },
            )

        val payload =
            encode(
                collector
                    .collect(
                        includeInvisible = false,
                        includeSystemWindows = true,
                    ).response,
            )

        val windowsById = payloadWindowsById(payload)
        assertEquals("application", windowsById.getValue("w40").getString("type"))
        assertEquals("inputMethod", windowsById.getValue("w41").getString("type"))
        assertEquals("system", windowsById.getValue("w42").getString("type"))
        assertEquals("accessibilityOverlay", windowsById.getValue("w43").getString("type"))
        assertEquals("splitScreenDivider", windowsById.getValue("w44").getString("type"))
        assertEquals("magnificationOverlay", windowsById.getValue("w45").getString("type"))
        assertEquals("unknown", windowsById.getValue("w46").getString("type"))

        val rootNode = payloadNodesByRid(payload).getValue("w40:0")
        assertEquals(
            listOf(
                "click",
                "longClick",
                "focus",
                "scrollForward",
                "scrollBackward",
                "setText",
                "dismiss",
                "action_321",
            ),
            jsonArrayStrings(rootNode.getJSONArray("actions")),
        )
    }

    @Test
    fun collectKeepsImePayloadFromFullWindowSetWhenSystemWindowsAreExcluded() {
        val collector =
            newCollector(
                windows =
                    listOf(
                        window(
                            id = 60,
                            type = AccessibilityWindowInfo.TYPE_APPLICATION,
                            root = node(packageName = "com.example.app"),
                        ),
                        window(
                            id = 61,
                            type = AccessibilityWindowInfo.TYPE_INPUT_METHOD,
                            root = node(packageName = "ime.package"),
                        ),
                    ),
            )

        val payload =
            encode(
                collector
                    .collect(
                        includeInvisible = false,
                        includeSystemWindows = false,
                    ).response,
            )

        assertEquals(1, payload.getJSONArray("windows").length())
        assertTrue(payload.getJSONObject("ime").getBoolean("visible"))
        assertEquals("w61", payload.getJSONObject("ime").getString("windowId"))
    }

    private fun newCollector(
        windows: List<AccessibilityWindowInfo>,
        widthPx: Int = 1080,
        heightPx: Int = 2400,
        densityDpi: Int = 420,
        displayRotations: Map<Int, Int> = mapOf(Display.DEFAULT_DISPLAY to 0),
        displayInfos: Map<Int, SnapshotDisplay> =
            displayRotations.mapValues { (_, rotation) ->
                SnapshotDisplay(
                    widthPx = widthPx,
                    heightPx = heightPx,
                    densityDpi = densityDpi,
                    rotation = rotation,
                )
            },
        actionIdsForNode: (AccessibilityNodeInfo) -> List<Int> = { emptyList() },
        foregroundObservationProvider: ForegroundObservationProvider? = null,
    ): SnapshotCollector {
        val service = mock(AccessibilityService::class.java)
        val resources = mock(Resources::class.java)
        val metrics =
            DisplayMetrics().apply {
                this.widthPixels = widthPx
                this.heightPixels = heightPx
                this.densityDpi = densityDpi
            }
        val displayIds =
            linkedSetOf<Int>().apply {
                addAll(displayRotations.keys)
                addAll(displayInfos.keys)
            }
        val displays = displayIds.associateWith { mock(Display::class.java) }
        displayRotations.forEach { (displayId, rotation) ->
            `when`(displays.getValue(displayId).rotation).thenReturn(rotation)
        }
        val displayInfoByDisplay: Map<Display, SnapshotDisplay?> =
            displays.entries.associate { (displayId, display) ->
                display to displayInfos[displayId]
            }

        `when`(service.windows).thenReturn(windows)
        `when`(service.resources).thenReturn(resources)
        `when`(resources.displayMetrics).thenReturn(metrics)

        return SnapshotCollector(
            service = service,
            foregroundObservationProvider =
                foregroundObservationProvider ?: AccessibilityForegroundObservationProvider(service),
            actionIdProvider = actionIdsForNode,
            displayProvider = { displayId -> displays[displayId] },
            snapshotDisplayProvider = { display -> displayInfoByDisplay[display] },
        )
    }

    @Test
    fun defaultForegroundObservationProviderFollowsLatestBridgeGraphAfterReset() {
        val root = node(packageName = "com.example.latest")
        val service = mock(AccessibilityService::class.java)
        val resources = mock(Resources::class.java)
        val latestWindow =
            window(
                id = 30,
                type = AccessibilityWindowInfo.TYPE_APPLICATION,
                root = root,
                displayId = 1,
            )
        val metrics =
            DisplayMetrics().apply {
                widthPixels = 1080
                heightPixels = 2400
                densityDpi = 420
            }
        val display = mock(Display::class.java)
        `when`(display.rotation).thenReturn(0)
        `when`(service.windows).thenReturn(listOf(latestWindow))
        `when`(service.resources).thenReturn(resources)
        `when`(resources.displayMetrics).thenReturn(metrics)
        val collector =
            SnapshotCollector(
                service = service,
                actionIdProvider = { emptyList() },
                displayProvider = { displayId -> if (displayId == 1) display else null },
                snapshotDisplayProvider = {
                    SnapshotDisplay(
                        widthPx = 1080,
                        heightPx = 2400,
                        densityDpi = 420,
                        rotation = 0,
                    )
                },
            )

        AgentRuntimeBridge.resetForTest()
        AgentRuntimeBridge.recordObservedWindowState(
            eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.example.latest",
            windowClassName = "com.example.latest.LatestActivity",
        )

        val payload =
            encode(
                collector
                    .collect(
                        includeInvisible = false,
                        includeSystemWindows = false,
                    ).response,
            )

        assertFalse(payload.isNull("activityName"))
        assertEquals("com.example.latest.LatestActivity", payload.getString("activityName"))
    }

    private fun window(
        id: Int,
        type: Int,
        layer: Int = 0,
        root: AccessibilityNodeInfo?,
        bounds: Rect = Rect(0, 0, 100, 100),
        displayId: Int = Display.DEFAULT_DISPLAY,
        active: Boolean = type == AccessibilityWindowInfo.TYPE_APPLICATION,
        focused: Boolean = type == AccessibilityWindowInfo.TYPE_APPLICATION,
    ): AccessibilityWindowInfo {
        val window = mock(AccessibilityWindowInfo::class.java)
        `when`(window.id).thenReturn(id)
        `when`(window.type).thenReturn(type)
        `when`(window.layer).thenReturn(layer)
        `when`(window.displayId).thenReturn(displayId)
        `when`(window.isActive).thenReturn(active)
        `when`(window.isFocused).thenReturn(focused)
        `when`(window.root).thenReturn(root)
        doAnswer { invocation ->
            val rect = invocation.arguments[0] as Rect
            rect.left = bounds.left
            rect.top = bounds.top
            rect.right = bounds.right
            rect.bottom = bounds.bottom
            null
        }.`when`(window).getBoundsInScreen(org.mockito.ArgumentMatchers.any(Rect::class.java))
        return window
    }

    @Suppress("DEPRECATION")
    private fun node(
        packageName: String? = "com.example.app",
        className: String? = "android.view.View",
        resourceId: String? = null,
        text: String? = null,
        contentDesc: String? = null,
        hintText: String? = null,
        stateDescription: String? = null,
        paneTitle: String? = null,
        visibleToUser: Boolean = true,
        importantForAccessibility: Boolean = true,
        clickable: Boolean = false,
        enabled: Boolean = true,
        editable: Boolean = false,
        focusable: Boolean = false,
        focused: Boolean = false,
        checkable: Boolean = false,
        checked: Boolean = false,
        selected: Boolean = false,
        scrollable: Boolean = false,
        password: Boolean = false,
        bounds: Rect = Rect(0, 0, 100, 100),
        children: List<AccessibilityNodeInfo> = emptyList(),
    ): AccessibilityNodeInfo {
        val node = mock(AccessibilityNodeInfo::class.java)
        `when`(node.packageName).thenReturn(packageName)
        `when`(node.className).thenReturn(className)
        `when`(node.viewIdResourceName).thenReturn(resourceId)
        `when`(node.text).thenReturn(text)
        `when`(node.contentDescription).thenReturn(contentDesc)
        `when`(node.hintText).thenReturn(hintText)
        `when`(node.stateDescription).thenReturn(stateDescription)
        `when`(node.paneTitle).thenReturn(paneTitle)
        `when`(node.isVisibleToUser).thenReturn(visibleToUser)
        `when`(node.isImportantForAccessibility).thenReturn(importantForAccessibility)
        `when`(node.isClickable).thenReturn(clickable)
        `when`(node.isEnabled).thenReturn(enabled)
        `when`(node.isEditable).thenReturn(editable)
        `when`(node.isFocusable).thenReturn(focusable)
        `when`(node.isFocused).thenReturn(focused)
        `when`(node.isCheckable).thenReturn(checkable)
        `when`(node.isChecked).thenReturn(checked)
        `when`(node.isSelected).thenReturn(selected)
        `when`(node.isScrollable).thenReturn(scrollable)
        `when`(node.isPassword).thenReturn(password)
        `when`(node.childCount).thenReturn(children.size)
        children.forEachIndexed { index, child ->
            `when`(node.getChild(index)).thenReturn(child)
        }
        doAnswer { invocation ->
            val rect = invocation.arguments[0] as Rect
            rect.left = bounds.left
            rect.top = bounds.top
            rect.right = bounds.right
            rect.bottom = bounds.bottom
            null
        }.`when`(node).getBoundsInScreen(org.mockito.ArgumentMatchers.any(Rect::class.java))
        return node
    }

    private fun payloadNodesByRid(payload: JSONObject): Map<String, JSONObject> {
        val nodes = payload.getJSONArray("nodes")
        return buildMap {
            for (index in 0 until nodes.length()) {
                val node = nodes.getJSONObject(index)
                put(node.getString("rid"), node)
            }
        }
    }

    private fun payloadWindowsById(payload: JSONObject): Map<String, JSONObject> {
        val windows = payload.getJSONArray("windows")
        return buildMap {
            for (index in 0 until windows.length()) {
                val window = windows.getJSONObject(index)
                put(window.getString("windowId"), window)
            }
        }
    }

    private fun jsonArrayStrings(array: JSONArray): List<String> = List(array.length(), array::getString)

    private fun encode(response: SnapshotPayload): JSONObject {
        val writer = JsonWriter.objectWriter()
        SnapshotResponseCodec.write(writer, response)
        return writer.toJsonObject()
    }
}
