package com.rainng.androidctl.agent.actions

import android.accessibilityservice.AccessibilityService

internal data class ActionTextInput(
    val text: String,
    val replace: Boolean,
    val submit: Boolean,
    val ensureFocused: Boolean,
)

internal interface ActionBackend {
    fun tapHandle(
        snapshotId: Long,
        rid: String,
        longPress: Boolean,
    ): ActionResultStatus

    fun tapCoordinates(
        x: Float,
        y: Float,
        longPress: Boolean,
        timeoutMs: Long,
    ): ActionResultStatus

    fun type(
        snapshotId: Long,
        rid: String,
        input: ActionTextInput,
        timeoutMs: Long,
    ): ActionResultStatus

    fun global(action: GlobalAction): ActionResultStatus

    fun launchApp(packageName: String): ActionResultStatus

    fun openUrl(url: String): ActionResultStatus

    fun nodeAction(
        snapshotId: Long,
        rid: String,
        action: NodeAction,
    ): ActionResultStatus

    fun scroll(
        snapshotId: Long,
        rid: String,
        direction: ScrollDirection,
    ): ActionResultStatus

    fun gesture(
        direction: GestureDirection,
        timeoutMs: Long,
    ): ActionResultStatus
}

internal class AccessibilityActionBackend(
    service: AccessibilityService,
    private val dependencies: AccessibilityActionBackendDependencies = AccessibilityActionBackendDependencies.create(service),
) : ActionBackend {
    override fun tapHandle(
        snapshotId: Long,
        rid: String,
        longPress: Boolean,
    ): ActionResultStatus =
        dependencies.targetResolver.withResolvedNode(snapshotId, rid) { node ->
            dependencies.nodeActionExecutor.tap(node, longPress)
        }

    override fun tapCoordinates(
        x: Float,
        y: Float,
        longPress: Boolean,
        timeoutMs: Long,
    ): ActionResultStatus = dependencies.gestureDispatcher.tapCoordinates(x, y, longPress, timeoutMs)

    override fun type(
        snapshotId: Long,
        rid: String,
        input: ActionTextInput,
        timeoutMs: Long,
    ): ActionResultStatus =
        dependencies.targetResolver.withResolvedNode(snapshotId, rid) { node ->
            dependencies.nodeActionExecutor.type(
                node = node,
                text = input.text,
                replace = input.replace,
                submit = input.submit,
                ensureFocused = input.ensureFocused,
                timeoutMs = timeoutMs,
            )
        }

    override fun global(action: GlobalAction): ActionResultStatus {
        val globalAction =
            when (action) {
                GlobalAction.Back -> AccessibilityService.GLOBAL_ACTION_BACK
                GlobalAction.Home -> AccessibilityService.GLOBAL_ACTION_HOME
                GlobalAction.Recents -> AccessibilityService.GLOBAL_ACTION_RECENTS
                GlobalAction.Notifications -> AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS
            }
        return dependencies.globalActionPerformer(globalAction, action.wireName)
    }

    override fun launchApp(packageName: String): ActionResultStatus = dependencies.intentLauncher.launchApp(packageName)

    override fun openUrl(url: String): ActionResultStatus = dependencies.intentLauncher.openUrl(url)

    override fun nodeAction(
        snapshotId: Long,
        rid: String,
        action: NodeAction,
    ): ActionResultStatus =
        dependencies.targetResolver.withResolvedNode(snapshotId, rid) { node ->
            dependencies.nodeActionExecutor.nodeAction(node, action)
        }

    override fun scroll(
        snapshotId: Long,
        rid: String,
        direction: ScrollDirection,
    ): ActionResultStatus =
        dependencies.targetResolver.withResolvedNode(snapshotId, rid) { node ->
            dependencies.nodeActionExecutor.scroll(node, direction)
        }

    override fun gesture(
        direction: GestureDirection,
        timeoutMs: Long,
    ): ActionResultStatus = dependencies.gestureDispatcher.gesture(direction, timeoutMs)
}

internal data class AccessibilityActionBackendDependencies(
    val targetResolver: ActionTargetResolver,
    val nodeActionExecutor: NodeActionExecutor,
    val gestureDispatcher: GestureDispatcher,
    val intentLauncher: IntentLauncher,
    val globalActionPerformer: (actionId: Int, actionName: String) -> ActionResultStatus,
) {
    companion object {
        fun create(service: AccessibilityService): AccessibilityActionBackendDependencies =
            AccessibilityActionBackendDependencies(
                targetResolver = AccessibilityActionTargetResolver(service),
                nodeActionExecutor = AccessibilityNodeActionExecutor(),
                gestureDispatcher = AccessibilityGestureDispatcher(service),
                intentLauncher = AccessibilityIntentLauncher(service),
                globalActionPerformer = { actionId, actionName ->
                    if (!service.performGlobalAction(actionId)) {
                        throw ActionException(
                            code = com.rainng.androidctl.agent.errors.RpcErrorCode.ACTION_FAILED,
                            message = "failed to perform global action '$actionName'",
                            retryable = true,
                        )
                    }
                    ActionResultStatus.Done
                },
            )
    }
}
