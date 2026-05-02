package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.rpc.codec.JsonReader
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class ActionRequestCodecTest {
    @Test
    fun readDecodesEverySupportedActionBranch() {
        val requests =
            listOf(
                ActionRequestCodec.read(
                    JsonReader.fromObject(
                        JSONObject(
                            """
                            {
                              "kind":"tap",
                              "target":{"kind":"handle","handle":{"snapshotId":42,"rid":"w1:0.1"}},
                              "options":{"timeoutMs":5000}
                            }
                            """.trimIndent(),
                        ),
                    ),
                ),
                ActionRequestCodec.read(
                    JsonReader.fromObject(
                        JSONObject(
                            """
                            {
                              "kind":"longTap",
                              "target":{"kind":"coordinates","x":540,"y":1200},
                              "options":{"timeoutMs":5000}
                            }
                            """.trimIndent(),
                        ),
                    ),
                ),
                ActionRequestCodec.read(
                    JsonReader.fromObject(
                        JSONObject(
                            """
                            {
                              "kind":"type",
                              "target":{"kind":"handle","handle":{"snapshotId":7,"rid":"w1:0.5"}},
                              "input":{"text":"wifi","replace":false,"submit":true,"ensureFocused":false},
                              "options":{"timeoutMs":5000}
                            }
                            """.trimIndent(),
                        ),
                    ),
                ),
                ActionRequestCodec.read(
                    JsonReader.fromObject(
                        JSONObject(
                            """
                            {
                              "kind":"node",
                              "target":{"kind":"handle","handle":{"snapshotId":11,"rid":"w1:0.9"}},
                              "node":{"action":"showOnScreen"},
                              "options":{"timeoutMs":5000}
                            }
                            """.trimIndent(),
                        ),
                    ),
                ),
                ActionRequestCodec.read(
                    JsonReader.fromObject(
                        JSONObject(
                            """
                            {
                              "kind":"scroll",
                              "target":{"kind":"handle","handle":{"snapshotId":11,"rid":"w1:0.9"}},
                              "scroll":{"direction":"backward"},
                              "options":{"timeoutMs":5000}
                            }
                            """.trimIndent(),
                        ),
                    ),
                ),
                ActionRequestCodec.read(
                    JsonReader.fromObject(
                        JSONObject(
                            """
                            {
                              "kind":"global",
                              "target":{"kind":"none"},
                              "global":{"action":"notifications"},
                              "options":{"timeoutMs":5000}
                            }
                            """.trimIndent(),
                        ),
                    ),
                ),
                ActionRequestCodec.read(
                    JsonReader.fromObject(
                        JSONObject(
                            """
                            {
                              "kind":"gesture",
                              "target":{"kind":"none"},
                              "gesture":{"direction":"left"},
                              "options":{"timeoutMs":5000}
                            }
                            """.trimIndent(),
                        ),
                    ),
                ),
                ActionRequestCodec.read(
                    JsonReader.fromObject(
                        JSONObject(
                            """
                            {
                              "kind":"launchApp",
                              "target":{"kind":"none"},
                              "intent":{"packageName":"com.android.settings"},
                              "options":{"timeoutMs":5000}
                            }
                            """.trimIndent(),
                        ),
                    ),
                ),
                ActionRequestCodec.read(
                    JsonReader.fromObject(
                        JSONObject(
                            """
                            {
                              "kind":"openUrl",
                              "target":{"kind":"none"},
                              "intent":{"url":"https://example.com"},
                              "options":{"timeoutMs":5000}
                            }
                            """.trimIndent(),
                        ),
                    ),
                ),
            )

        assertEquals(
            listOf(
                ActionKind.Tap,
                ActionKind.LongTap,
                ActionKind.Type,
                ActionKind.Node,
                ActionKind.Scroll,
                ActionKind.Global,
                ActionKind.Gesture,
                ActionKind.LaunchApp,
                ActionKind.OpenUrl,
            ),
            requests.map(ActionRequest::kind),
        )
        assertEquals(NodeAction.ShowOnScreen, (requests[3] as NodeActionRequest).action)
        assertEquals(ScrollDirection.Backward, (requests[4] as ScrollActionRequest).direction)
        assertEquals(GlobalAction.Notifications, (requests[5] as GlobalActionRequest).action)
        assertEquals(GestureDirection.Left, (requests[6] as GestureActionRequest).direction)
    }

    @Test
    fun readAllowsUnknownTopLevelFieldsOutsideClosedBranches() {
        val request =
            ActionRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject(
                        """
                        {
                          "kind":"tap",
                          "target":{"kind":"coordinates","x":100,"y":200},
                          "options":{"timeoutMs":5000},
                          "ignoredTopLevelField":true
                        }
                        """.trimIndent(),
                    ),
                ),
            )

        assertTrue(request is TapActionRequest)
    }

    @Test
    fun readRejectsUnknownFieldInsideInputPayload() {
        assertValidationError("input contains unknown field 'foo'") {
            ActionRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject(
                        """
                        {
                          "kind":"type",
                          "target":{"kind":"handle","handle":{"snapshotId":7,"rid":"w1:0.5"}},
                          "options":{"timeoutMs":5000},
                          "input":{"text":"wifi","foo":true}
                        }
                        """.trimIndent(),
                    ),
                ),
            )
        }
    }

    @Test
    fun readRejectsUnknownFieldInsideHandlePayload() {
        assertValidationError("target.handle contains unknown field 'extra'") {
            ActionRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject(
                        """
                        {
                          "kind":"tap",
                          "target":{"kind":"handle","handle":{"snapshotId":42,"rid":"w1:0.1","extra":1}},
                          "options":{"timeoutMs":5000}
                        }
                        """.trimIndent(),
                    ),
                ),
            )
        }
    }

    @Test
    fun readRejectsUnknownFieldInsideOptionsPayload() {
        assertValidationError("options contains unknown field 'waitForIdle'") {
            ActionRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject(
                        """
                        {
                          "kind":"tap",
                          "target":{"kind":"coordinates","x":100,"y":200},
                          "options":{"timeoutMs":1500,"waitForIdle":true}
                        }
                        """.trimIndent(),
                    ),
                ),
            )
        }
    }

    @Test
    fun readPreservesNonWebOpenUrlTarget() {
        val request =
            readRequest(
                JSONObject(
                    """
                    {
                      "kind":"openUrl",
                      "target":{"kind":"none"},
                      "intent":{"url":"smsto:10086?body=phase-d"}
                    }
                    """.trimIndent(),
                ),
            ) as OpenUrlActionRequest

        assertEquals("smsto:10086?body=phase-d", request.url)
    }

    @Test
    fun readAllowsEmptyTextOnlyWhenReplaceIsTrue() {
        val request =
            ActionRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject(
                        """
                        {
                          "kind":"type",
                          "target":{"kind":"handle","handle":{"snapshotId":7,"rid":"w1:0.5"}},
                          "input":{"text":"","replace":true,"submit":false,"ensureFocused":true},
                          "options":{"timeoutMs":5000}
                        }
                        """.trimIndent(),
                    ),
                ),
            ) as TypeActionRequest

        assertEquals("", request.input.text)

        assertValidationError("type requires non-empty text unless replace=true") {
            ActionRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject(
                        """
                        {
                          "kind":"type",
                          "target":{"kind":"handle","handle":{"snapshotId":7,"rid":"w1:0.5"}},
                          "input":{"text":"","replace":false,"submit":false,"ensureFocused":true},
                          "options":{"timeoutMs":5000}
                        }
                        """.trimIndent(),
                    ),
                ),
            )
        }
    }

    @Test
    fun readPreservesWhitespaceOnlyText() {
        val request =
            readRequest(
                JSONObject(
                    """
                    {
                      "kind":"type",
                      "target":{"kind":"handle","handle":{"snapshotId":7,"rid":"w1:0.5"}},
                      "input":{"text":"   ","replace":false,"submit":false,"ensureFocused":true}
                    }
                    """.trimIndent(),
                ),
            ) as TypeActionRequest

        assertEquals("   ", request.input.text)
    }

    @Test
    fun readRejectsMissingKind() {
        assertValidationError("action.perform requires kind") {
            readRequest(
                JSONObject().put("target", JSONObject().put("kind", "none")),
            )
        }
    }

    @Test
    fun readRejectsNullKind() {
        assertValidationError("action.perform requires kind") {
            readRequest(
                JSONObject()
                    .put("kind", JSONObject.NULL)
                    .put("target", JSONObject().put("kind", "none")),
            )
        }
    }

    @Test
    fun readRejectsMissingTarget() {
        assertValidationError("action.perform requires target") {
            readRequest(JSONObject().put("kind", "tap"))
        }
    }

    @Test
    fun readRejectsNonObjectTarget() {
        assertValidationError("action.perform requires target") {
            readRequest(
                JSONObject()
                    .put("kind", "tap")
                    .put("target", "none"),
            )
        }
    }

    @Test
    fun readRejectsUnsupportedTargetKind() {
        assertValidationError("unsupported target kind") {
            readRequest(
                JSONObject()
                    .put("kind", "tap")
                    .put("target", JSONObject().put("kind", "widget")),
            )
        }
    }

    @Test
    fun readRejectsNullTargetKindAsUnsupportedTargetKind() {
        assertValidationError("unsupported target kind") {
            readRequest(
                JSONObject()
                    .put("kind", "tap")
                    .put("target", JSONObject().put("kind", JSONObject.NULL)),
            )
        }
    }

    @Test
    fun readRejectsMissingOptionsAndTimeout() {
        assertValidationError("action.perform requires options") {
            readRequest(
                JSONObject()
                    .put("kind", "global")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("global", JSONObject().put("action", "home")),
                includeDefaultOptions = false,
            )
        }
        assertValidationError("action.perform requires options.timeoutMs") {
            readRequest(
                JSONObject()
                    .put("kind", "global")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("global", JSONObject().put("action", "home"))
                    .put("options", JSONObject()),
                includeDefaultOptions = false,
            )
        }
    }

    @Test
    fun readRejectsHandleTargetWithoutPayload() {
        assertValidationError("handle target requires handle payload") {
            readRequest(
                JSONObject()
                    .put("kind", "tap")
                    .put("target", JSONObject().put("kind", "handle")),
            )
        }
    }

    @Test
    fun readRejectsHandleTargetWithoutSnapshotId() {
        assertValidationError("handle target requires snapshotId") {
            readRequest(
                JSONObject()
                    .put("kind", "tap")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("rid", "w1:0.1")),
                    ),
            )
        }
    }

    @Test
    fun readRejectsHandleTargetWithStringSnapshotId() {
        assertValidationError("handle target requires snapshotId") {
            readRequest(
                JSONObject()
                    .put("kind", "tap")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put(
                                "handle",
                                JSONObject()
                                    .put("snapshotId", "42")
                                    .put("rid", "w1:0.1"),
                            ),
                    ),
            )
        }
    }

    @Test
    fun readRejectsHandleTargetWithoutRid() {
        assertValidationError("handle target requires rid") {
            readRequest(
                JSONObject()
                    .put("kind", "tap")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 42)),
                    ),
            )
        }
    }

    @Test
    fun readRejectsCoordinatesTargetWithoutX() {
        assertValidationError("coordinates target requires x") {
            readRequest(
                JSONObject()
                    .put("kind", "tap")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "coordinates")
                            .put("y", 1200),
                    ),
            )
        }
    }

    @Test
    fun readRejectsCoordinatesTargetWithStringX() {
        assertValidationError("coordinates target requires x") {
            readRequest(
                JSONObject()
                    .put("kind", "tap")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "coordinates")
                            .put("x", "540")
                            .put("y", 1200),
                    ),
            )
        }
    }

    @Test
    fun readRejectsTypeWithoutInputPayload() {
        assertValidationError("type requires input payload") {
            readRequest(
                JSONObject()
                    .put("kind", "type")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.5")),
                    ),
            )
        }
    }

    @Test
    fun readRejectsTypeInputWithNullText() {
        assertValidationError("type requires text string") {
            readRequest(
                JSONObject()
                    .put("kind", "type")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.5")),
                    ).put(
                        "input",
                        JSONObject()
                            .put("text", JSONObject.NULL)
                            .put("replace", true)
                            .put("submit", false)
                            .put("ensureFocused", true),
                    ),
            )
        }
    }

    @Test
    fun readRejectsInvalidTargetsForTapTypeAndGlobalActions() {
        assertActionError("tap requires handle or coordinates target") {
            readRequest(
                JSONObject()
                    .put("kind", "tap")
                    .put("target", JSONObject().put("kind", "none")),
            )
        }
        assertActionError("type requires handle target") {
            readRequest(
                JSONObject()
                    .put("kind", "type")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("input", JSONObject().put("text", "wifi")),
            )
        }
        assertActionError("global action requires none target") {
            readRequest(
                JSONObject()
                    .put("kind", "global")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.1")),
                    ).put("global", JSONObject().put("action", "home")),
            )
        }
    }

    @Test
    fun readRejectsMissingOrNullGlobalActionName() {
        assertValidationError("global action requires action name") {
            readRequest(
                JSONObject()
                    .put("kind", "global")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("global", JSONObject()),
            )
        }
        assertValidationError("global action requires action name") {
            readRequest(
                JSONObject()
                    .put("kind", "global")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("global", JSONObject().put("action", JSONObject.NULL)),
            )
        }
    }

    @Test
    fun readRejectsInvalidLaunchAppAndOpenUrlRequestShapes() {
        assertActionError("launchApp requires none target") {
            readRequest(
                JSONObject()
                    .put("kind", "launchApp")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.1")),
                    ).put("intent", JSONObject().put("packageName", "com.android.settings")),
            )
        }
        assertValidationError("launchApp requires intent payload") {
            readRequest(
                JSONObject()
                    .put("kind", "launchApp")
                    .put("target", JSONObject().put("kind", "none")),
            )
        }
        assertValidationError("launchApp requires packageName") {
            readRequest(
                JSONObject()
                    .put("kind", "launchApp")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("intent", JSONObject()),
            )
        }
        assertValidationError("launchApp requires packageName") {
            readRequest(
                JSONObject()
                    .put("kind", "launchApp")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("intent", JSONObject().put("packageName", JSONObject.NULL)),
            )
        }
        assertActionError("openUrl requires none target") {
            readRequest(
                JSONObject()
                    .put("kind", "openUrl")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.1")),
                    ).put("intent", JSONObject().put("url", "https://example.com")),
            )
        }
        assertValidationError("openUrl requires intent payload") {
            readRequest(
                JSONObject()
                    .put("kind", "openUrl")
                    .put("target", JSONObject().put("kind", "none")),
            )
        }
        assertValidationError("openUrl requires url") {
            readRequest(
                JSONObject()
                    .put("kind", "openUrl")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("intent", JSONObject()),
            )
        }
        assertValidationError("openUrl requires url") {
            readRequest(
                JSONObject()
                    .put("kind", "openUrl")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("intent", JSONObject().put("url", JSONObject.NULL)),
            )
        }
    }

    @Test
    fun readRejectsInvalidNodeScrollAndGestureRequestShapes() {
        assertActionError("node action requires handle target") {
            readRequest(
                JSONObject()
                    .put("kind", "node")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("node", JSONObject().put("action", "focus")),
            )
        }
        assertValidationError("node action requires action name") {
            readRequest(
                JSONObject()
                    .put("kind", "node")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.9")),
                    ).put("node", JSONObject()),
            )
        }
        assertValidationError("node action requires action name") {
            readRequest(
                JSONObject()
                    .put("kind", "node")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.9")),
                    ).put("node", JSONObject().put("action", JSONObject.NULL)),
            )
        }
        assertActionError("scroll requires handle target") {
            readRequest(
                JSONObject()
                    .put("kind", "scroll")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("scroll", JSONObject().put("direction", "down")),
            )
        }
        assertValidationError("scroll requires direction") {
            readRequest(
                JSONObject()
                    .put("kind", "scroll")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.4")),
                    ).put("scroll", JSONObject()),
            )
        }
        assertValidationError("scroll requires direction") {
            readRequest(
                JSONObject()
                    .put("kind", "scroll")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.4")),
                    ).put("scroll", JSONObject().put("direction", JSONObject.NULL)),
            )
        }
        assertActionError("gesture requires none target") {
            readRequest(
                JSONObject()
                    .put("kind", "gesture")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.1")),
                    ).put("gesture", JSONObject().put("direction", "down")),
            )
        }
        assertValidationError("gesture requires gesture payload") {
            readRequest(
                JSONObject()
                    .put("kind", "gesture")
                    .put("target", JSONObject().put("kind", "none")),
            )
        }
        assertValidationError("gesture requires direction") {
            readRequest(
                JSONObject()
                    .put("kind", "gesture")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("gesture", JSONObject()),
            )
        }
        assertValidationError("gesture requires direction") {
            readRequest(
                JSONObject()
                    .put("kind", "gesture")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("gesture", JSONObject().put("direction", JSONObject.NULL)),
            )
        }
    }

    @Test
    fun readRejectsBlankSingleStringPayloadValues() {
        fun handleTarget(): JSONObject =
            JSONObject()
                .put("kind", "handle")
                .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.9"))

        fun noneTarget(): JSONObject = JSONObject().put("kind", "none")

        assertValidationError("node action requires action name") {
            readRequest(
                JSONObject()
                    .put("kind", "node")
                    .put("target", handleTarget())
                    .put("node", JSONObject().put("action", "   ")),
            )
        }
        assertValidationError("scroll requires direction") {
            readRequest(
                JSONObject()
                    .put("kind", "scroll")
                    .put("target", handleTarget())
                    .put("scroll", JSONObject().put("direction", "   ")),
            )
        }
        assertValidationError("global action requires action name") {
            readRequest(
                JSONObject()
                    .put("kind", "global")
                    .put("target", noneTarget())
                    .put("global", JSONObject().put("action", "   ")),
            )
        }
        assertValidationError("gesture requires direction") {
            readRequest(
                JSONObject()
                    .put("kind", "gesture")
                    .put("target", noneTarget())
                    .put("gesture", JSONObject().put("direction", "   ")),
            )
        }
        assertValidationError("launchApp requires packageName") {
            readRequest(
                JSONObject()
                    .put("kind", "launchApp")
                    .put("target", noneTarget())
                    .put("intent", JSONObject().put("packageName", "   ")),
            )
        }
        assertValidationError("openUrl requires url") {
            readRequest(
                JSONObject()
                    .put("kind", "openUrl")
                    .put("target", noneTarget())
                    .put("intent", JSONObject().put("url", "   ")),
            )
        }
    }

    @Test
    fun readRejectsTypeInputWithMissingFlags() {
        assertValidationError("type requires input.replace") {
            readRequest(
                JSONObject()
                    .put("kind", "type")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.5")),
                    ).put(
                        "input",
                        JSONObject()
                            .put("text", "wifi")
                            .put("submit", false)
                            .put("ensureFocused", true),
                    ),
            )
        }
        assertValidationError("type requires input.submit") {
            readRequest(
                JSONObject()
                    .put("kind", "type")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.5")),
                    ).put(
                        "input",
                        JSONObject()
                            .put("text", "wifi")
                            .put("replace", true)
                            .put("ensureFocused", true),
                    ),
            )
        }
        assertValidationError("type requires input.ensureFocused") {
            readRequest(
                JSONObject()
                    .put("kind", "type")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.5")),
                    ).put(
                        "input",
                        JSONObject()
                            .put("text", "wifi")
                            .put("replace", true)
                            .put("submit", false),
                    ),
            )
        }
    }

    @Test
    fun readRejectsTypeInputWithNonBooleanFlags() {
        assertValidationError("type input.replace must be a boolean") {
            readRequest(
                JSONObject()
                    .put("kind", "type")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.5")),
                    ).put(
                        "input",
                        JSONObject()
                            .put("text", "wifi")
                            .put("replace", "true")
                            .put("submit", false)
                            .put("ensureFocused", true),
                    ),
            )
        }
        assertValidationError("type input.submit must be a boolean") {
            readRequest(
                JSONObject()
                    .put("kind", "type")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.5")),
                    ).put(
                        "input",
                        JSONObject()
                            .put("text", "wifi")
                            .put("replace", true)
                            .put("submit", "false")
                            .put("ensureFocused", true),
                    ),
            )
        }
        assertValidationError("type input.ensureFocused must be a boolean") {
            readRequest(
                JSONObject()
                    .put("kind", "type")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 1).put("rid", "w1:0.5")),
                    ).put(
                        "input",
                        JSONObject()
                            .put("text", "wifi")
                            .put("replace", true)
                            .put("submit", false)
                            .put("ensureFocused", "true"),
                    ),
            )
        }
    }

    @Test
    fun readRejectsInvalidNodeGestureAndIntentFieldTypes() {
        assertValidationError("node action requires action name") {
            readRequest(
                JSONObject()
                    .put("kind", "node")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 11).put("rid", "w1:0.9")),
                    ).put("node", JSONObject().put("action", true)),
            )
        }
        assertValidationError("gesture contains unknown field 'kind'") {
            readRequest(
                JSONObject()
                    .put("kind", "gesture")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("gesture", JSONObject().put("kind", true).put("direction", "down")),
            )
        }
        assertValidationError("intent contains unknown field 'activityName'") {
            readRequest(
                JSONObject()
                    .put("kind", "launchApp")
                    .put("target", JSONObject().put("kind", "none"))
                    .put(
                        "intent",
                        JSONObject()
                            .put("packageName", "com.android.settings")
                            .put("activityName", true),
                    ),
            )
        }
        assertValidationError("intent contains unknown field 'packageName'") {
            readRequest(
                JSONObject()
                    .put("kind", "openUrl")
                    .put("target", JSONObject().put("kind", "none"))
                    .put(
                        "intent",
                        JSONObject()
                            .put("url", "https://example.com")
                            .put("packageName", true),
                    ),
            )
        }
    }

    @Test
    fun readRejectsUnsupportedWireTokensForActionSubtypes() {
        assertValidationError("unsupported global action 'power'") {
            readRequest(
                JSONObject()
                    .put("kind", "global")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("global", JSONObject().put("action", "power")),
            )
        }
        assertValidationError("unsupported node action 'expand'") {
            readRequest(
                JSONObject()
                    .put("kind", "node")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 11).put("rid", "w1:0.9")),
                    ).put("node", JSONObject().put("action", "expand")),
            )
        }
        assertValidationError("unsupported swipe direction 'forward'") {
            readRequest(
                JSONObject()
                    .put("kind", "gesture")
                    .put("target", JSONObject().put("kind", "none"))
                    .put("gesture", JSONObject().put("direction", "forward")),
            )
        }
    }

    @Test
    fun readRejectsInvalidOptionsAndTimeoutTypes() {
        assertValidationError("options must be a JSON object") {
            readRequest(
                JSONObject()
                    .put("kind", "tap")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 42).put("rid", "w1:0.1")),
                    ).put("options", "slow"),
            )
        }
        assertValidationError("options.timeoutMs must be greater than 0") {
            readRequest(
                JSONObject(
                    """
                    {
                      "kind":"tap",
                      "target":{
                        "kind":"handle",
                        "handle":{"snapshotId":42,"rid":"w1:0.1"}
                      },
                      "options":{"timeoutMs":0}
                    }
                    """.trimIndent(),
                ),
            )
        }
        assertValidationError("options.timeoutMs must be <= ${RequestBudgets.MAX_ACTION_TIMEOUT_MS}") {
            readRequest(
                JSONObject(
                    """
                    {
                      "kind":"tap",
                      "target":{
                        "kind":"handle",
                        "handle":{"snapshotId":42,"rid":"w1:0.1"}
                      },
                      "options":{"timeoutMs":20000}
                    }
                    """.trimIndent(),
                ),
            )
        }
        assertValidationError("options.timeoutMs must be an integer") {
            readRequest(
                JSONObject(
                    """
                    {
                      "kind":"tap",
                      "target":{
                        "kind":"handle",
                        "handle":{"snapshotId":42,"rid":"w1:0.1"}
                      },
                      "options":{"timeoutMs":"5000"}
                    }
                    """.trimIndent(),
                ),
            )
        }
        assertValidationError("options.timeoutMs must be an integer") {
            readRequest(
                JSONObject()
                    .put("kind", "tap")
                    .put(
                        "target",
                        JSONObject()
                            .put("kind", "handle")
                            .put("handle", JSONObject().put("snapshotId", 42).put("rid", "w1:0.1")),
                    ).put("options", JSONObject().put("timeoutMs", JSONObject.NULL)),
            )
        }
        assertValidationError("options.timeoutMs must be an integer") {
            readRequest(
                JSONObject(
                    """
                    {
                      "kind":"tap",
                      "target":{
                        "kind":"handle",
                        "handle":{"snapshotId":42,"rid":"w1:0.1"}
                      },
                      "options":{"timeoutMs":12.5}
                    }
                    """.trimIndent(),
                ),
            )
        }
    }

    private fun readRequest(
        params: JSONObject,
        includeDefaultOptions: Boolean = true,
    ): ActionRequest {
        if (includeDefaultOptions && !params.has("options")) {
            params.put("options", JSONObject().put("timeoutMs", 5000L))
        }
        return ActionRequestCodec.read(JsonReader.fromObject(params))
    }

    private fun assertValidationError(
        expectedMessage: String,
        block: () -> Unit,
    ) {
        try {
            block()
            fail("expected RequestValidationException")
        } catch (error: RequestValidationException) {
            assertEquals(expectedMessage, error.message)
        }
    }

    private fun assertActionError(
        expectedMessage: String,
        block: () -> Unit,
    ) {
        try {
            block()
            fail("expected ActionException")
        } catch (error: ActionException) {
            assertEquals(RpcErrorCode.INVALID_REQUEST, error.code)
            assertEquals(expectedMessage, error.message)
        }
    }
}
