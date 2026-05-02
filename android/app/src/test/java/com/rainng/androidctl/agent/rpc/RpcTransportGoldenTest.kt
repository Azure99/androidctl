package com.rainng.androidctl.agent.rpc

import android.accessibilityservice.AccessibilityService
import com.rainng.androidctl.agent.actions.ActionResult
import com.rainng.androidctl.agent.actions.ActionResultStatus
import com.rainng.androidctl.agent.actions.ActionTarget
import com.rainng.androidctl.agent.device.AppEntryResponse
import com.rainng.androidctl.agent.device.AppsListResponse
import com.rainng.androidctl.agent.events.DeviceEvent
import com.rainng.androidctl.agent.events.EventPollRequest
import com.rainng.androidctl.agent.events.EventPollResult
import com.rainng.androidctl.agent.events.ImeChangedPayload
import com.rainng.androidctl.agent.events.SnapshotInvalidatedPayload
import com.rainng.androidctl.agent.runtime.ObservedWindowState
import com.rainng.androidctl.agent.runtime.RuntimeAccess
import com.rainng.androidctl.agent.runtime.RuntimeReadiness
import com.rainng.androidctl.agent.screenshot.ScreenshotResponse
import com.rainng.androidctl.agent.snapshot.SnapshotDisplay
import com.rainng.androidctl.agent.snapshot.SnapshotIme
import com.rainng.androidctl.agent.snapshot.SnapshotNode
import com.rainng.androidctl.agent.snapshot.SnapshotPayload
import com.rainng.androidctl.agent.snapshot.SnapshotPublication
import com.rainng.androidctl.agent.snapshot.SnapshotRegistry
import com.rainng.androidctl.agent.snapshot.SnapshotWindow
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock

class RpcTransportGoldenTest {
    @Before
    fun setUp() {
        SnapshotRegistry.resetForTest()
    }

    @After
    fun tearDown() {
        SnapshotRegistry.resetForTest()
    }

    @Test
    fun metaGet_transportShapeMatchesGolden() {
        val actual =
            dispatch(
                method = "meta.get",
                params = JSONObject(),
            )

        assertJsonEquals(
            """
            {
              "id":"req-transport",
              "ok":true,
              "result":{
                "service":"androidctl-device-agent",
                "version":"1.0.0",
                "capabilities":{
                  "supportsEventsPoll":true,
                  "supportsScreenshot":true,
                  "actionKinds":["tap","longTap","type","node","scroll","global","gesture","launchApp","openUrl"]
                }
              }
            }
            """.trimIndent(),
            actual,
        )
    }

    @Test
    fun appsList_transportShapeMatchesGolden() {
        val actual =
            dispatch(
                method = "apps.list",
                params = JSONObject(),
            )

        assertJsonEquals(
            """
            {
              "id":"req-transport",
              "ok":true,
              "result":{
                "apps":[
                  {
                    "packageName":"com.android.settings",
                    "appLabel":"Settings",
                    "launchable":true
                  }
                ]
              }
            }
            """.trimIndent(),
            actual,
        )
    }

    @Test
    fun snapshotGet_transportShapeMatchesGolden() {
        val actual =
            dispatch(
                method = "snapshot.get",
                params = JSONObject("""{"includeInvisible":true,"includeSystemWindows":true}"""),
            )

        assertJsonEquals(
            """
            {
              "id":"req-transport",
              "ok":true,
              "result":{
                "snapshotId":42,
                "capturedAt":"2026-03-26T00:00:00Z",
                "packageName":"com.android.settings",
                "activityName":"SettingsActivity",
                "display":{
                  "widthPx":1080,
                  "heightPx":2400,
                  "densityDpi":420,
                  "rotation":0
                },
                "ime":{
                  "visible":false,
                  "windowId":null
                },
                "windows":[
                  {
                    "windowId":"w1",
                    "type":"application",
                    "layer":0,
                    "packageName":"com.android.settings",
                    "bounds":[0,0,1080,2400],
                    "rootRid":"w1:0"
                  }
                ],
                "nodes":[
                  {
                    "rid":"w1:0",
                    "windowId":"w1",
                    "parentRid":null,
                    "childRids":[],
                    "className":"android.widget.FrameLayout",
                    "resourceId":"android:id/content",
                    "text":null,
                    "contentDesc":null,
                    "hintText":null,
                    "stateDescription":null,
                    "paneTitle":null,
                    "packageName":"com.android.settings",
                    "bounds":[0,0,1080,2400],
                    "visibleToUser":true,
                    "importantForAccessibility":true,
                    "clickable":false,
                    "enabled":true,
                    "editable":false,
                    "focusable":false,
                    "focused":false,
                    "checkable":false,
                    "checked":false,
                    "selected":false,
                    "scrollable":false,
                    "password":false,
                    "actions":[]
                  }
                ]
              }
            }
            """.trimIndent(),
            actual,
        )
    }

    @Test
    fun actionPerform_transportShapeMatchesGolden() {
        val params =
            JSONObject(
                """
                {
                  "kind":"tap",
                  "target":{
                    "kind":"handle",
                    "handle":{"snapshotId":42,"rid":"w1:0.1"}
                  },
                  "options":{"timeoutMs":1200}
                }
                """.trimIndent(),
            )

        val actual = dispatch(method = "action.perform", params = params)

        assertJsonEquals(
            """
            {
              "id":"req-transport",
              "ok":true,
              "result":{
                "actionId":"act-00042",
                "status":"done",
                "durationMs":17,
                "resolvedTarget":{
                  "kind":"handle",
                  "handle":{
                    "snapshotId":42,
                    "rid":"w1:0.1"
                  }
                },
                "observed":{
                  "packageName":"com.android.settings",
                  "activityName":null
                }
              }
            }
            """.trimIndent(),
            actual,
        )
    }

    @Test
    fun screenshotCapture_transportShapeMatchesGolden() {
        val params = JSONObject("""{"format":"jpeg","scale":0.5}""")
        val actual = dispatch(method = "screenshot.capture", params = params)

        assertJsonEquals(
            """
            {
              "id":"req-transport",
              "ok":true,
              "result":{
                "contentType":"image/png",
                "widthPx":100,
                "heightPx":50,
                "bodyBase64":"ZmFrZQ=="
              }
            }
            """.trimIndent(),
            actual,
        )
    }

    private fun dispatch(
        method: String,
        params: JSONObject,
    ): JSONObject {
        val handler = newHandler()
        val body =
            JSONObject()
                .put("id", "req-transport")
                .put("method", method)
                .put("params", params)
                .toString()
        return try {
            JSONObject(
                handler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody = body,
                ),
            )
        } finally {
            handler.shutdown(force = true)
        }
    }

    private fun newHandler(): RpcRequestHandler {
        val methodExecutor = RpcRequestHandler.newMethodExecutor()
        val runtimeAccess =
            object : RuntimeAccess {
                override fun readiness(): RuntimeReadiness = RuntimeReadiness(true, true)

                override fun currentDeviceToken(): String = "device-token"

                override fun applicationContext() = null

                override fun currentAccessibilityService() = mock(AccessibilityService::class.java)
            }

        return RpcRequestHandler(
            authorizationGate =
                RpcAuthorizationGate(
                    expectedTokenProvider = { "device-token" },
                    readinessProvider = { RuntimeReadiness(true, true) },
                ),
            dispatcher =
                RpcDispatcher(
                    runtimeAccess = runtimeAccess,
                    methodCatalog =
                        RpcMethodCatalog(
                            listOf(
                                MetaGetMethod(versionProvider = { "1.0.0" }),
                                AppsListMethod(
                                    appsListProvider = {
                                        AppsListResponse(
                                            apps =
                                                listOf(
                                                    AppEntryResponse(
                                                        packageName = "com.android.settings",
                                                        appLabel = "Settings",
                                                        launchable = true,
                                                    ),
                                                ),
                                        )
                                    },
                                ),
                                SnapshotGetMethod(
                                    snapshotExecutionFactory = {
                                        {
                                            SnapshotPublication.create(
                                                response =
                                                    SnapshotPayload(
                                                        snapshotId = 42L,
                                                        capturedAt = "2026-03-26T00:00:00Z",
                                                        packageName = "com.android.settings",
                                                        activityName = "SettingsActivity",
                                                        display =
                                                            SnapshotDisplay(
                                                                widthPx = 1080,
                                                                heightPx = 2400,
                                                                densityDpi = 420,
                                                                rotation = 0,
                                                            ),
                                                        ime = SnapshotIme(visible = false, windowId = null),
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
                                                                    childRids = emptyList(),
                                                                    className = "android.widget.FrameLayout",
                                                                    resourceId = "android:id/content",
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
                                                            ),
                                                    ),
                                                registryRecord =
                                                    com.rainng.androidctl.agent.snapshot.SnapshotRecord(
                                                        snapshotId = 42L,
                                                        ridToHandle = emptyMap(),
                                                    ),
                                                generation = SnapshotRegistry.currentGeneration(),
                                            )
                                        }
                                    },
                                ),
                                ActionPerformMethod(
                                    actionExecutionFactory = {
                                        {
                                            ActionResult(
                                                actionId = "act-00042",
                                                status = ActionResultStatus.Done,
                                                durationMs = 17L,
                                                resolvedTarget = ActionTarget.Handle(snapshotId = 42L, rid = "w1:0.1"),
                                                observed = ObservedWindowState(packageName = "com.android.settings", activityName = null),
                                            )
                                        }
                                    },
                                ),
                                EventsPollMethod(
                                    eventsPollProvider = { _: EventPollRequest ->
                                        EventPollResult(
                                            events =
                                                listOf(
                                                    DeviceEvent(
                                                        seq = 6L,
                                                        timestamp = "2026-03-27T00:00:00Z",
                                                        data = ImeChangedPayload(visible = false, windowId = null),
                                                    ),
                                                    DeviceEvent(
                                                        seq = 7L,
                                                        timestamp = "2026-03-27T00:00:01Z",
                                                        data = SnapshotInvalidatedPayload(packageName = null, reason = "viewScrolled"),
                                                    ),
                                                ),
                                            latestSeq = 7L,
                                            needResync = true,
                                            timedOut = false,
                                        )
                                    },
                                ),
                                ScreenshotCaptureMethod(
                                    screenshotExecutionFactory = {
                                        {
                                            ScreenshotResponse(
                                                contentType = "image/png",
                                                widthPx = 100,
                                                heightPx = 50,
                                                bodyBase64 = "ZmFrZQ==",
                                            )
                                        }
                                    },
                                ),
                            ),
                        ),
                    executionRunner = RpcExecutionRunner(methodExecutor),
                ),
            methodExecutor = methodExecutor,
        )
    }

    private fun assertJsonEquals(
        expectedJson: String,
        actual: JSONObject,
    ) {
        val expected = normalize(JSONObject(expectedJson))
        val normalizedActual = normalize(actual)
        assertEquals(expected, normalizedActual)
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
