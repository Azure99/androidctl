package com.rainng.androidctl.agent.actions

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityNodeInfo
import com.rainng.androidctl.agent.testsupport.mockNode
import com.rainng.androidctl.agent.testsupport.mockService
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class AccessibilityActionBackendTest {
    @Test
    fun tapHandleDelegatesResolvedNodeToNodeActionExecutor() {
        val resolvedNode = mockNode()
        var observedSnapshotId: Long? = null
        var observedRid: String? = null
        var observedLongPress: Boolean? = null
        val backend =
            AccessibilityActionBackend(
                service = mockService(),
                dependencies =
                    AccessibilityActionBackendDependencies(
                        targetResolver =
                            object : ActionTargetResolver {
                                override fun <T> withResolvedNode(
                                    snapshotId: Long,
                                    rid: String,
                                    block: (AccessibilityNodeInfo) -> T,
                                ): T {
                                    observedSnapshotId = snapshotId
                                    observedRid = rid
                                    return block(resolvedNode)
                                }
                            },
                        nodeActionExecutor =
                            object : NodeActionExecutor {
                                override fun tap(
                                    node: AccessibilityNodeInfo,
                                    longPress: Boolean,
                                ): ActionResultStatus {
                                    assertSame(resolvedNode, node)
                                    observedLongPress = longPress
                                    return ActionResultStatus.Done
                                }

                                override fun type(
                                    node: AccessibilityNodeInfo,
                                    text: String,
                                    replace: Boolean,
                                    submit: Boolean,
                                    ensureFocused: Boolean,
                                    timeoutMs: Long,
                                ): ActionResultStatus = error("unexpected")

                                override fun nodeAction(
                                    node: AccessibilityNodeInfo,
                                    action: NodeAction,
                                ): ActionResultStatus = error("unexpected")

                                override fun scroll(
                                    node: AccessibilityNodeInfo,
                                    direction: ScrollDirection,
                                ): ActionResultStatus = error("unexpected")
                            },
                        gestureDispatcher = unexpectedGestureDispatcher(),
                        intentLauncher = unexpectedIntentLauncher(),
                        globalActionPerformer = { _, _ -> error("unexpected") },
                    ),
            )

        val status = backend.tapHandle(1L, "w1:0", longPress = true)

        assertEquals(ActionResultStatus.Done, status)
        assertEquals(1L, observedSnapshotId)
        assertEquals("w1:0", observedRid)
        assertTrue(observedLongPress == true)
    }

    @Test
    fun tapCoordinatesDelegatesToGestureDispatcher() {
        var observedLongPress: Boolean? = null
        var observedTimeoutMs: Long? = null
        val backend =
            AccessibilityActionBackend(
                service = mockService(),
                dependencies =
                    AccessibilityActionBackendDependencies(
                        targetResolver = unexpectedTargetResolver(),
                        nodeActionExecutor = unexpectedNodeActionExecutor(),
                        gestureDispatcher =
                            object : GestureDispatcher {
                                override fun tapCoordinates(
                                    x: Float,
                                    y: Float,
                                    longPress: Boolean,
                                    timeoutMs: Long,
                                ): ActionResultStatus {
                                    assertEquals(10f, x)
                                    assertEquals(20f, y)
                                    observedLongPress = longPress
                                    observedTimeoutMs = timeoutMs
                                    return ActionResultStatus.Done
                                }

                                override fun gesture(
                                    direction: GestureDirection,
                                    timeoutMs: Long,
                                ): ActionResultStatus = error("unexpected")
                            },
                        intentLauncher = unexpectedIntentLauncher(),
                        globalActionPerformer = { _, _ -> error("unexpected") },
                    ),
            )

        val status = backend.tapCoordinates(10f, 20f, longPress = false, timeoutMs = 30L)

        assertEquals(ActionResultStatus.Done, status)
        assertTrue(observedLongPress == false)
        assertEquals(30L, observedTimeoutMs)
    }

    @Test
    fun launchAppDelegatesToIntentLauncher() {
        val backend =
            AccessibilityActionBackend(
                service = mockService(),
                dependencies =
                    AccessibilityActionBackendDependencies(
                        targetResolver = unexpectedTargetResolver(),
                        nodeActionExecutor = unexpectedNodeActionExecutor(),
                        gestureDispatcher = unexpectedGestureDispatcher(),
                        intentLauncher =
                            object : IntentLauncher {
                                override fun launchApp(packageName: String): ActionResultStatus {
                                    assertEquals("com.android.settings", packageName)
                                    return ActionResultStatus.Done
                                }

                                override fun openUrl(url: String): ActionResultStatus = error("unexpected")
                            },
                        globalActionPerformer = { _, _ -> error("unexpected") },
                    ),
            )

        val status = backend.launchApp("com.android.settings")

        assertEquals(ActionResultStatus.Done, status)
    }

    @Test
    fun typeDelegatesTimeoutToNodeActionExecutor() {
        val resolvedNode = mockNode(editable = true)
        var observedTimeoutMs: Long? = null
        val backend =
            AccessibilityActionBackend(
                service = mockService(),
                dependencies =
                    AccessibilityActionBackendDependencies(
                        targetResolver =
                            object : ActionTargetResolver {
                                override fun <T> withResolvedNode(
                                    snapshotId: Long,
                                    rid: String,
                                    block: (AccessibilityNodeInfo) -> T,
                                ): T = block(resolvedNode)
                            },
                        nodeActionExecutor =
                            object : NodeActionExecutor {
                                override fun tap(
                                    node: AccessibilityNodeInfo,
                                    longPress: Boolean,
                                ): ActionResultStatus = error("unexpected")

                                override fun type(
                                    node: AccessibilityNodeInfo,
                                    text: String,
                                    replace: Boolean,
                                    submit: Boolean,
                                    ensureFocused: Boolean,
                                    timeoutMs: Long,
                                ): ActionResultStatus {
                                    observedTimeoutMs = timeoutMs
                                    return ActionResultStatus.Partial
                                }

                                override fun nodeAction(
                                    node: AccessibilityNodeInfo,
                                    action: NodeAction,
                                ): ActionResultStatus = error("unexpected")

                                override fun scroll(
                                    node: AccessibilityNodeInfo,
                                    direction: ScrollDirection,
                                ): ActionResultStatus = error("unexpected")
                            },
                        gestureDispatcher = unexpectedGestureDispatcher(),
                        intentLauncher = unexpectedIntentLauncher(),
                        globalActionPerformer = { _, _ -> error("unexpected") },
                    ),
            )

        val status =
            backend.type(
                snapshotId = 7L,
                rid = "w1:0.5",
                input = ActionTextInput(text = "wifi", replace = true, submit = false, ensureFocused = true),
                timeoutMs = 3210L,
            )

        assertEquals(ActionResultStatus.Partial, status)
        assertEquals(3210L, observedTimeoutMs)
    }

    @Test
    fun globalDelegatesToConfiguredGlobalActionPerformer() {
        var observedActionId: Int? = null
        var observedActionName: String? = null
        val backend =
            AccessibilityActionBackend(
                service = mockService(),
                dependencies =
                    AccessibilityActionBackendDependencies(
                        targetResolver = unexpectedTargetResolver(),
                        nodeActionExecutor = unexpectedNodeActionExecutor(),
                        gestureDispatcher = unexpectedGestureDispatcher(),
                        intentLauncher = unexpectedIntentLauncher(),
                        globalActionPerformer = { actionId, actionName ->
                            observedActionId = actionId
                            observedActionName = actionName
                            ActionResultStatus.Done
                        },
                    ),
            )

        val status = backend.global(GlobalAction.Back)

        assertEquals(ActionResultStatus.Done, status)
        assertEquals(AccessibilityService.GLOBAL_ACTION_BACK, observedActionId)
        assertEquals("back", observedActionName)
    }

    private fun unexpectedTargetResolver(): ActionTargetResolver =
        object : ActionTargetResolver {
            override fun <T> withResolvedNode(
                snapshotId: Long,
                rid: String,
                block: (AccessibilityNodeInfo) -> T,
            ): T = error("unexpected")
        }

    private fun unexpectedNodeActionExecutor(): NodeActionExecutor =
        object : NodeActionExecutor {
            override fun tap(
                node: AccessibilityNodeInfo,
                longPress: Boolean,
            ): ActionResultStatus = error("unexpected")

            override fun type(
                node: AccessibilityNodeInfo,
                text: String,
                replace: Boolean,
                submit: Boolean,
                ensureFocused: Boolean,
                timeoutMs: Long,
            ): ActionResultStatus = error("unexpected")

            override fun nodeAction(
                node: AccessibilityNodeInfo,
                action: NodeAction,
            ): ActionResultStatus = error("unexpected")

            override fun scroll(
                node: AccessibilityNodeInfo,
                direction: ScrollDirection,
            ): ActionResultStatus = error("unexpected")
        }

    private fun unexpectedGestureDispatcher(): GestureDispatcher =
        object : GestureDispatcher {
            override fun tapCoordinates(
                x: Float,
                y: Float,
                longPress: Boolean,
                timeoutMs: Long,
            ): ActionResultStatus = error("unexpected")

            override fun gesture(
                direction: GestureDirection,
                timeoutMs: Long,
            ): ActionResultStatus = error("unexpected")
        }

    private fun unexpectedIntentLauncher(): IntentLauncher =
        object : IntentLauncher {
            override fun launchApp(packageName: String): ActionResultStatus = error("unexpected")

            override fun openUrl(url: String): ActionResultStatus = error("unexpected")
        }
}
