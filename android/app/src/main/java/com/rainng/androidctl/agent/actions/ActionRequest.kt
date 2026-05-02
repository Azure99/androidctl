package com.rainng.androidctl.agent.actions

internal sealed interface ActionRequest {
    val kind: ActionKind
    val target: ActionTarget
    val timeoutMs: Long
}

internal data class TapActionRequest(
    override val target: ActionTarget,
    override val timeoutMs: Long,
) : ActionRequest {
    override val kind: ActionKind = ActionKind.Tap
}

internal data class LongTapActionRequest(
    override val target: ActionTarget,
    override val timeoutMs: Long,
) : ActionRequest {
    override val kind: ActionKind = ActionKind.LongTap
}

internal data class TypeActionRequest(
    override val target: ActionTarget.Handle,
    override val timeoutMs: Long,
    val input: ActionTextInput,
) : ActionRequest {
    override val kind: ActionKind = ActionKind.Type
}

internal data class GlobalActionRequest(
    override val timeoutMs: Long,
    val action: GlobalAction,
    override val target: ActionTarget.None = ActionTarget.None,
) : ActionRequest {
    override val kind: ActionKind = ActionKind.Global
}

internal data class LaunchAppActionRequest(
    override val timeoutMs: Long,
    val packageName: String,
    override val target: ActionTarget.None = ActionTarget.None,
) : ActionRequest {
    override val kind: ActionKind = ActionKind.LaunchApp
}

internal data class OpenUrlActionRequest(
    override val timeoutMs: Long,
    val url: String,
    override val target: ActionTarget.None = ActionTarget.None,
) : ActionRequest {
    override val kind: ActionKind = ActionKind.OpenUrl
}

internal data class NodeActionRequest(
    override val target: ActionTarget.Handle,
    override val timeoutMs: Long,
    val action: NodeAction,
) : ActionRequest {
    override val kind: ActionKind = ActionKind.Node
}

internal data class ScrollActionRequest(
    override val target: ActionTarget.Handle,
    override val timeoutMs: Long,
    val direction: ScrollDirection,
) : ActionRequest {
    override val kind: ActionKind = ActionKind.Scroll
}

internal data class GestureActionRequest(
    override val timeoutMs: Long,
    val direction: GestureDirection,
    override val target: ActionTarget.None = ActionTarget.None,
) : ActionRequest {
    override val kind: ActionKind = ActionKind.Gesture
}

internal sealed interface ActionTarget {
    val kind: TargetKind

    data class Handle(
        val snapshotId: Long,
        val rid: String,
    ) : ActionTarget {
        override val kind: TargetKind = TargetKind.Handle
    }

    data class Coordinates(
        val x: Float,
        val y: Float,
    ) : ActionTarget {
        override val kind: TargetKind = TargetKind.Coordinates
    }

    data object None : ActionTarget {
        override val kind: TargetKind = TargetKind.None
    }
}
