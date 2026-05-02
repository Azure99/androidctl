package com.rainng.androidctl.agent.actions

import android.view.accessibility.AccessibilityNodeInfo
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.testsupport.assertActionException
import com.rainng.androidctl.agent.testsupport.mockNode
import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.ArrayDeque

class AccessibilityNodeActionExecutorTest {
    @Test
    fun tapUsesLongClickActionWhenRequested() {
        val attemptedActions = mutableListOf<Int>()
        val node =
            mockNode(
                actionHandler = { actionId, _ ->
                    attemptedActions += actionId
                    true
                },
            )
        val executor = AccessibilityNodeActionExecutor()

        val status = executor.tap(node, longPress = true)

        assertEquals(ActionResultStatus.Done, status)
        assertEquals(listOf(AccessibilityNodeInfo.ACTION_LONG_CLICK), attemptedActions)
    }

    @Test
    fun typeRejectsNonEditableNodes() {
        val node = mockNode(editable = false)
        val executor = AccessibilityNodeActionExecutor()

        assertActionException(
            expectedCode = RpcErrorCode.TARGET_NOT_ACTIONABLE,
            expectedMessage = "target is not editable",
            expectedRetryable = false,
        ) {
            executor.type(node, "wifi", replace = true, submit = false, ensureFocused = true, timeoutMs = 8000L)
        }
    }

    @Test
    fun typeRequestsFocusAndUsesCombinedTextWhenReplaceIsFalse() {
        val performedActions = mutableListOf<Int>()
        val submittedTexts = mutableListOf<String>()
        val typedValues = ArrayDeque(listOf("wifi", "wifi!"))
        val node =
            mockNode(
                editable = true,
                focused = false,
                text = "wifi",
                textProvider = {
                    if (typedValues.isEmpty()) {
                        "wifi!"
                    } else {
                        typedValues.removeFirst()
                    }
                },
                actionHandler = { actionId, _ ->
                    performedActions += actionId
                    true
                },
            )
        val executor =
            AccessibilityNodeActionExecutor(
                setTextActionExecutor = { actionNode, finalText ->
                    submittedTexts += finalText
                    actionNode.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT)
                },
            )

        val status = executor.type(node, "!", replace = false, submit = false, ensureFocused = true, timeoutMs = 8000L)

        assertEquals(ActionResultStatus.Done, status)
        assertEquals(
            listOf(AccessibilityNodeInfo.ACTION_FOCUS, AccessibilityNodeInfo.ACTION_SET_TEXT),
            performedActions,
        )
        assertEquals(listOf("wifi!"), submittedTexts)
    }

    @Test
    fun typeFailsWhenSetTextActionFails() {
        val node =
            mockNode(
                editable = true,
                actionResults = mapOf(AccessibilityNodeInfo.ACTION_SET_TEXT to false),
            )
        val executor =
            AccessibilityNodeActionExecutor(
                setTextActionExecutor = { actionNode, _ ->
                    actionNode.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT)
                },
            )

        assertActionException(
            expectedCode = RpcErrorCode.ACTION_FAILED,
            expectedMessage = "failed to set text on target",
            expectedRetryable = true,
        ) {
            executor.type(node, "wifi", replace = true, submit = false, ensureFocused = true, timeoutMs = 8000L)
        }
    }

    @Test
    fun typeFailsWhenSubmitActionFails() {
        val typedValues = ArrayDeque(listOf("", "wifi"))
        val node =
            mockNode(
                editable = true,
                textProvider = {
                    if (typedValues.isEmpty()) {
                        "wifi"
                    } else {
                        typedValues.removeFirst()
                    }
                },
                actionResults =
                    mapOf(
                        AccessibilityNodeInfo.ACTION_SET_TEXT to true,
                        imeEnterActionId() to false,
                    ),
            )
        val executor =
            AccessibilityNodeActionExecutor(
                setTextActionExecutor = { actionNode, _ ->
                    actionNode.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT)
                },
            )

        assertActionException(
            expectedCode = RpcErrorCode.TARGET_NOT_ACTIONABLE,
            expectedMessage = "target does not support 'submit'",
            expectedRetryable = false,
        ) {
            executor.type(node, "wifi", replace = true, submit = true, ensureFocused = false, timeoutMs = 8000L)
        }
    }

    @Test
    fun typeReturnsPartialWhenTextCannotBeVerified() {
        val clock = TestClock()
        val refreshAttempts = mutableListOf<Int>()
        val node =
            mockNode(
                editable = true,
                textProvider = { "" },
                refreshHandler = {
                    refreshAttempts += 1
                    true
                },
                actionResults = mapOf(AccessibilityNodeInfo.ACTION_SET_TEXT to true),
            )
        val executor =
            AccessibilityNodeActionExecutor(
                setTextActionExecutor = { actionNode, _ ->
                    actionNode.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT)
                },
                nanoTimeProvider = clock::nanoTime,
                sleepProvider = clock::sleep,
            )

        val status = executor.type(node, "wifi", replace = true, submit = false, ensureFocused = false, timeoutMs = 800L)

        assertEquals(ActionResultStatus.Partial, status)
        assertEquals(listOf(50L, 50L, 50L, 50L), clock.sleeps)
        assertEquals(4, refreshAttempts.size)
    }

    @Test
    fun typeUsesSharedVerificationDivisorForMidrangeTimeout() {
        val clock = TestClock()
        val refreshAttempts = mutableListOf<Int>()
        val node =
            mockNode(
                editable = true,
                textProvider = { "" },
                refreshHandler = {
                    refreshAttempts += 1
                    true
                },
                actionResults = mapOf(AccessibilityNodeInfo.ACTION_SET_TEXT to true),
            )
        val executor =
            AccessibilityNodeActionExecutor(
                setTextActionExecutor = { actionNode, _ ->
                    actionNode.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT)
                },
                nanoTimeProvider = clock::nanoTime,
                sleepProvider = clock::sleep,
            )

        executor.type(node, "wifi", replace = true, submit = false, ensureFocused = false, timeoutMs = 1000L)

        assertEquals(listOf(50L, 50L, 50L, 50L, 50L), clock.sleeps)
        assertEquals(5, refreshAttempts.size)
    }

    @Test
    fun typeUsesSharedVerificationMaximumBudget() {
        val clock = TestClock()
        val refreshAttempts = mutableListOf<Int>()
        val node =
            mockNode(
                editable = true,
                textProvider = { "" },
                refreshHandler = {
                    refreshAttempts += 1
                    true
                },
                actionResults = mapOf(AccessibilityNodeInfo.ACTION_SET_TEXT to true),
            )
        val executor =
            AccessibilityNodeActionExecutor(
                setTextActionExecutor = { actionNode, _ ->
                    actionNode.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT)
                },
                nanoTimeProvider = clock::nanoTime,
                sleepProvider = clock::sleep,
            )

        executor.type(node, "wifi", replace = true, submit = false, ensureFocused = false, timeoutMs = 10_000L)

        assertEquals(List(24) { 50L }, clock.sleeps)
        assertEquals(24, refreshAttempts.size)
    }

    @Test
    fun typeTreatsNullTextAsSuccessfulClearWhenReplaceIsTrue() {
        var currentText: CharSequence? = "seed"
        val node =
            mockNode(
                editable = true,
                text = "seed",
                textProvider = { currentText },
                actionResults = mapOf(AccessibilityNodeInfo.ACTION_SET_TEXT to true),
            )
        val executor =
            AccessibilityNodeActionExecutor(
                setTextActionExecutor = { actionNode, _ ->
                    currentText = null
                    actionNode.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT)
                },
                textReader = { currentText?.toString() },
            )

        val status = executor.type(node, "", replace = true, submit = false, ensureFocused = false, timeoutMs = 8000L)

        assertEquals(ActionResultStatus.Done, status)
    }

    @Test
    fun downScrollFallsBackToGenericForwardWhenDirectionalDownFails() {
        val attemptedActions = mutableListOf<Int>()
        val actionIds = AccessibilityNodeActionIds()
        val node =
            mockNode(
                actionHandler = { actionId, _ ->
                    attemptedActions += actionId
                    actionId == AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
                },
            )
        val executor = AccessibilityNodeActionExecutor()

        val status = executor.scroll(node, ScrollDirection.Down)

        assertEquals(ActionResultStatus.Done, status)
        assertEquals(
            listOf(actionIds.scrollDown(), AccessibilityNodeInfo.ACTION_SCROLL_FORWARD),
            attemptedActions,
        )
    }

    @Test
    fun nonDownDirectionalScrollDoesNotUseGenericFallbackActions() {
        val node =
            mockNode(
                actionHandler = { actionId, _ ->
                    actionId == AccessibilityNodeInfo.ACTION_SCROLL_FORWARD ||
                        actionId == AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
                },
            )
        val executor = AccessibilityNodeActionExecutor()

        assertActionException(
            expectedCode = RpcErrorCode.TARGET_NOT_ACTIONABLE,
            expectedMessage = "target does not support scroll 'up'",
            expectedRetryable = false,
        ) {
            executor.scroll(node, ScrollDirection.Up)
        }
        assertActionException(
            expectedCode = RpcErrorCode.TARGET_NOT_ACTIONABLE,
            expectedMessage = "target does not support scroll 'left'",
            expectedRetryable = false,
        ) {
            executor.scroll(node, ScrollDirection.Left)
        }
        assertActionException(
            expectedCode = RpcErrorCode.TARGET_NOT_ACTIONABLE,
            expectedMessage = "target does not support scroll 'right'",
            expectedRetryable = false,
        ) {
            executor.scroll(node, ScrollDirection.Right)
        }
    }

    @Test
    fun downScrollFailsAfterDirectionalAndGenericCandidatesFail() {
        val attemptedActions = mutableListOf<Int>()
        val actionIds = AccessibilityNodeActionIds()
        val node =
            mockNode(
                actionHandler = { actionId, _ ->
                    attemptedActions += actionId
                    false
                },
            )
        val executor = AccessibilityNodeActionExecutor()

        assertActionException(
            expectedCode = RpcErrorCode.TARGET_NOT_ACTIONABLE,
            expectedMessage = "target does not support scroll 'down'",
            expectedRetryable = false,
        ) {
            executor.scroll(node, ScrollDirection.Down)
        }

        assertEquals(
            listOf(actionIds.scrollDown(), AccessibilityNodeInfo.ACTION_SCROLL_FORWARD),
            attemptedActions,
        )
    }

    @Test
    fun directionalScrollUsesOnlyMatchingDirectionalAction() {
        val executor = AccessibilityNodeActionExecutor()
        val cases =
            listOf(
                ScrollCase(
                    direction = ScrollDirection.Down,
                    expectedActionId = AccessibilityNodeActionIds().scrollDown(),
                ),
                ScrollCase(
                    direction = ScrollDirection.Up,
                    expectedActionId = AccessibilityNodeActionIds().scrollUp(),
                ),
                ScrollCase(
                    direction = ScrollDirection.Left,
                    expectedActionId = AccessibilityNodeActionIds().scrollLeft(),
                ),
                ScrollCase(
                    direction = ScrollDirection.Right,
                    expectedActionId = AccessibilityNodeActionIds().scrollRight(),
                ),
            )

        cases.forEach { scrollCase ->
            val attemptedActions = mutableListOf<Int>()
            val node =
                mockNode(
                    actionHandler = { actionId, _ ->
                        attemptedActions += actionId
                        actionId == scrollCase.expectedActionId
                    },
                )

            val status = executor.scroll(node, scrollCase.direction)

            assertEquals(ActionResultStatus.Done, status)
            assertEquals(listOf(scrollCase.expectedActionId), attemptedActions)
        }
    }

    @Test
    fun backwardScrollUsesGenericBackwardAction() {
        val attemptedActions = mutableListOf<Int>()
        val node =
            mockNode(
                actionHandler = { actionId, _ ->
                    attemptedActions += actionId
                    actionId == AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
                },
            )
        val executor = AccessibilityNodeActionExecutor()

        val status = executor.scroll(node, ScrollDirection.Backward)

        assertEquals(ActionResultStatus.Done, status)
        assertEquals(listOf(AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD), attemptedActions)
    }

    @Test
    fun scrollFailsWhenNoCandidateActionSucceeds() {
        val node = mockNode(actionHandler = { _, _ -> false })
        val executor = AccessibilityNodeActionExecutor()

        assertActionException(
            expectedCode = RpcErrorCode.TARGET_NOT_ACTIONABLE,
            expectedMessage = "target does not support scroll 'right'",
            expectedRetryable = false,
        ) {
            executor.scroll(node, ScrollDirection.Right)
        }
    }

    private data class ScrollCase(
        val direction: ScrollDirection,
        val expectedActionId: Int,
    )
}
