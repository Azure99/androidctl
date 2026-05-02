package com.rainng.androidctl.agent.actions

internal class ActionRequestDispatcher(
    private val backend: ActionBackend,
) {
    fun dispatch(request: ActionRequest): ActionResultStatus =
        when (request) {
            is TapActionRequest -> performTap(request.target, longPress = false, timeoutMs = request.timeoutMs)
            is LongTapActionRequest -> performTap(request.target, longPress = true, timeoutMs = request.timeoutMs)
            is TypeActionRequest ->
                backend.type(
                    snapshotId = request.target.snapshotId,
                    rid = request.target.rid,
                    input = request.input,
                    timeoutMs = request.timeoutMs,
                )

            is GlobalActionRequest -> backend.global(request.action)
            is LaunchAppActionRequest -> backend.launchApp(request.packageName)
            is OpenUrlActionRequest -> backend.openUrl(request.url)
            is NodeActionRequest -> backend.nodeAction(request.target.snapshotId, request.target.rid, request.action)
            is ScrollActionRequest -> backend.scroll(request.target.snapshotId, request.target.rid, request.direction)
            is GestureActionRequest -> backend.gesture(request.direction, request.timeoutMs)
        }

    private fun performTap(
        target: ActionTarget,
        longPress: Boolean,
        timeoutMs: Long,
    ): ActionResultStatus =
        when (target) {
            is ActionTarget.Handle -> backend.tapHandle(target.snapshotId, target.rid, longPress)
            is ActionTarget.Coordinates -> backend.tapCoordinates(target.x, target.y, longPress, timeoutMs)
            ActionTarget.None -> throw unsupportedTapTarget()
        }

    private fun unsupportedTapTarget(): ActionException =
        ActionException(
            code = com.rainng.androidctl.agent.errors.RpcErrorCode.INVALID_REQUEST,
            message = "tap requires handle or coordinates target",
            retryable = false,
        )
}
