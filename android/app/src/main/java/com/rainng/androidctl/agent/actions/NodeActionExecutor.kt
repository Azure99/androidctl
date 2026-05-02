package com.rainng.androidctl.agent.actions

import android.os.Bundle
import android.view.accessibility.AccessibilityNodeInfo
import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.errors.RpcErrorCode
import java.util.concurrent.TimeUnit

internal interface NodeActionExecutor {
    fun tap(
        node: AccessibilityNodeInfo,
        longPress: Boolean,
    ): ActionResultStatus

    @Suppress("LongParameterList")
    fun type(
        node: AccessibilityNodeInfo,
        text: String,
        replace: Boolean,
        submit: Boolean,
        ensureFocused: Boolean,
        timeoutMs: Long,
    ): ActionResultStatus

    fun nodeAction(
        node: AccessibilityNodeInfo,
        action: NodeAction,
    ): ActionResultStatus

    fun scroll(
        node: AccessibilityNodeInfo,
        direction: ScrollDirection,
    ): ActionResultStatus
}

internal class AccessibilityNodeActionIds {
    fun imeEnter(): Int =
        runCatching { AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id }
            .getOrDefault(android.R.id.accessibilityActionImeEnter)

    fun showOnScreen(): Int =
        AccessibilityNodeInfo.AccessibilityAction.ACTION_SHOW_ON_SCREEN?.id ?: android.R.id.accessibilityActionShowOnScreen

    fun scrollUp(): Int = AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_UP?.id ?: android.R.id.accessibilityActionScrollUp

    fun scrollDown(): Int = AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_DOWN?.id ?: android.R.id.accessibilityActionScrollDown

    fun scrollLeft(): Int = AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_LEFT?.id ?: android.R.id.accessibilityActionScrollLeft

    fun scrollRight(): Int =
        AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_RIGHT?.id ?: android.R.id.accessibilityActionScrollRight
}

@Suppress("DEPRECATION")
internal class AccessibilityNodeActionExecutor(
    private val setTextActionExecutor: (AccessibilityNodeInfo, String) -> Boolean = { node, finalText ->
        val arguments =
            Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, finalText)
            }
        node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
    },
    private val actionIds: AccessibilityNodeActionIds = AccessibilityNodeActionIds(),
    private val refreshExecutor: (AccessibilityNodeInfo) -> Boolean = AccessibilityNodeInfo::refresh,
    private val textReader: (AccessibilityNodeInfo) -> String? = { node -> node.text?.toString() },
    private val nanoTimeProvider: () -> Long = System::nanoTime,
    private val sleepProvider: (Long) -> Unit = { Thread.sleep(it) },
) : NodeActionExecutor {
    override fun tap(
        node: AccessibilityNodeInfo,
        longPress: Boolean,
    ): ActionResultStatus {
        val actionId = if (longPress) AccessibilityNodeInfo.ACTION_LONG_CLICK else AccessibilityNodeInfo.ACTION_CLICK
        if (!node.performAction(actionId)) {
            throw ActionException(
                code = RpcErrorCode.TARGET_NOT_ACTIONABLE,
                message = "target does not support ${if (longPress) "longTap" else "tap"}",
                retryable = false,
            )
        }
        return ActionResultStatus.Done
    }

    @Suppress("LongParameterList")
    override fun type(
        node: AccessibilityNodeInfo,
        text: String,
        replace: Boolean,
        submit: Boolean,
        ensureFocused: Boolean,
        timeoutMs: Long,
    ): ActionResultStatus {
        if (!node.isEditable) {
            throw ActionException(
                code = RpcErrorCode.TARGET_NOT_ACTIONABLE,
                message = "target is not editable",
                retryable = false,
            )
        }

        if (ensureFocused && !node.isFocused) {
            node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        }

        val finalText = if (replace) text else node.text?.toString().orEmpty() + text
        if (!setTextActionExecutor(node, finalText)) {
            throw ActionException(
                code = RpcErrorCode.ACTION_FAILED,
                message = "failed to set text on target",
                retryable = true,
            )
        }

        val status = verifyTypedText(node = node, expectedText = finalText, timeoutMs = timeoutMs)

        if (submit) {
            performRequiredNodeAction(
                node = node,
                actionId = actionIds.imeEnter(),
                actionName = "submit",
            )
        }
        return status
    }

    override fun nodeAction(
        node: AccessibilityNodeInfo,
        action: NodeAction,
    ): ActionResultStatus {
        when (action) {
            NodeAction.Focus -> performRequiredNodeAction(node, AccessibilityNodeInfo.ACTION_FOCUS, action.wireName)
            NodeAction.Submit -> performRequiredNodeAction(node, actionIds.imeEnter(), action.wireName)
            NodeAction.Dismiss -> performRequiredNodeAction(node, AccessibilityNodeInfo.ACTION_DISMISS, action.wireName)
            NodeAction.ShowOnScreen -> performRequiredNodeAction(node, actionIds.showOnScreen(), action.wireName)
        }
        return ActionResultStatus.Done
    }

    override fun scroll(
        node: AccessibilityNodeInfo,
        direction: ScrollDirection,
    ): ActionResultStatus {
        val candidates =
            when (direction) {
                ScrollDirection.Backward -> listOf(AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD)
                ScrollDirection.Down ->
                    listOf(
                        actionIds.scrollDown(),
                        AccessibilityNodeInfo.ACTION_SCROLL_FORWARD,
                    )
                ScrollDirection.Up -> listOf(actionIds.scrollUp())
                ScrollDirection.Left -> listOf(actionIds.scrollLeft())
                ScrollDirection.Right -> listOf(actionIds.scrollRight())
            }
        if (!candidates.any(node::performAction)) {
            throw ActionException(
                code = RpcErrorCode.TARGET_NOT_ACTIONABLE,
                message = "target does not support scroll '${direction.wireName}'",
                retryable = false,
            )
        }
        return ActionResultStatus.Done
    }

    private fun performRequiredNodeAction(
        node: AccessibilityNodeInfo,
        actionId: Int,
        actionName: String,
    ) {
        if (!node.performAction(actionId)) {
            throw ActionException(
                code = RpcErrorCode.TARGET_NOT_ACTIONABLE,
                message = "target does not support '$actionName'",
                retryable = false,
            )
        }
    }

    private fun verifyTypedText(
        node: AccessibilityNodeInfo,
        expectedText: String,
        timeoutMs: Long,
    ): ActionResultStatus {
        // Device-side verification is intentionally best-effort. Host-side `androidctld`
        // still owns the final public confirmation after the refreshed snapshot.
        var status = ActionResultStatus.Done
        if (!matchesExpectedText(node = node, expectedText = expectedText)) {
            status = ActionResultStatus.Partial
            val verificationTimeoutMs = typeVerificationTimeoutMs(timeoutMs)
            val deadline = nanoTimeProvider() + TimeUnit.MILLISECONDS.toNanos(verificationTimeoutMs)
            while (nanoTimeProvider() < deadline && status != ActionResultStatus.Done) {
                sleepQuietlyForVerification(durationMs = TYPE_VERIFICATION_POLL_INTERVAL_MS)
                if (!refreshNode(node)) {
                    break
                }
                if (matchesExpectedText(node = node, expectedText = expectedText)) {
                    status = ActionResultStatus.Done
                }
            }
        }
        return status
    }

    private fun refreshNode(node: AccessibilityNodeInfo): Boolean = runCatching { refreshExecutor(node) }.getOrDefault(false)

    private fun matchesExpectedText(
        node: AccessibilityNodeInfo,
        expectedText: String,
    ): Boolean {
        val actualText = textReader(node).orEmpty()
        return if (expectedText.isEmpty()) {
            actualText.isEmpty()
        } else {
            actualText == expectedText
        }
    }

    private fun sleepQuietlyForVerification(durationMs: Long) {
        if (durationMs <= 0L) {
            return
        }
        try {
            sleepProvider(durationMs)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
    }

    private fun typeVerificationTimeoutMs(timeoutMs: Long): Long =
        minOf(
            RequestBudgets.MAX_TYPE_VERIFICATION_TIMEOUT_MS,
            maxOf(
                RequestBudgets.MIN_TYPE_VERIFICATION_TIMEOUT_MS,
                timeoutMs / RequestBudgets.TYPE_VERIFICATION_TIMEOUT_DIVISOR,
            ),
        )
}

private const val TYPE_VERIFICATION_POLL_INTERVAL_MS = 50L
