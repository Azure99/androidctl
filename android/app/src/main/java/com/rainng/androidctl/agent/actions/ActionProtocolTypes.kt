package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.errors.RequestValidationException

private interface WireToken {
    val wireName: String
}

private inline fun <reified T> decodeWireToken(
    value: String,
    invalidMessage: (String) -> String,
): T where T : Enum<T>, T : WireToken =
    enumValues<T>().firstOrNull { it.wireName == value }
        ?: throw RequestValidationException(invalidMessage(value))

private inline fun <reified T> canonicalWireNames(): List<String> where T : Enum<T>, T : WireToken =
    enumValues<T>().map(WireToken::wireName)

internal enum class ActionKind(
    override val wireName: String,
) : WireToken {
    Tap("tap"),
    LongTap("longTap"),
    Type("type"),
    Node("node"),
    Scroll("scroll"),
    Global("global"),
    Gesture("gesture"),
    LaunchApp("launchApp"),
    OpenUrl("openUrl"),
    ;

    companion object {
        fun decode(value: String): ActionKind = decodeWireToken(value) { "unsupported action kind '$it'" }

        fun capabilityWireNames(): List<String> = canonicalWireNames<ActionKind>()
    }
}

internal enum class TargetKind(
    override val wireName: String,
) : WireToken {
    Handle("handle"),
    Coordinates("coordinates"),
    None("none"),
}

internal enum class NodeAction(
    override val wireName: String,
) : WireToken {
    Focus("focus"),
    Submit("submit"),
    Dismiss("dismiss"),
    ShowOnScreen("showOnScreen"),
    ;

    companion object {
        fun decode(value: String): NodeAction = decodeWireToken(value) { "unsupported node action '$it'" }
    }
}

internal enum class GlobalAction(
    override val wireName: String,
) : WireToken {
    Back("back"),
    Home("home"),
    Recents("recents"),
    Notifications("notifications"),
    ;

    companion object {
        fun decode(value: String): GlobalAction = decodeWireToken(value) { "unsupported global action '$it'" }
    }
}

internal enum class ScrollDirection(
    override val wireName: String,
) : WireToken {
    Backward("backward"),
    Down("down"),
    Up("up"),
    Left("left"),
    Right("right"),
    ;

    companion object {
        fun decode(value: String): ScrollDirection = decodeWireToken(value) { "unsupported scroll direction '$it'" }
    }
}

internal enum class GestureDirection(
    override val wireName: String,
) : WireToken {
    Down("down"),
    Up("up"),
    Left("left"),
    Right("right"),
    ;

    companion object {
        fun decode(value: String): GestureDirection = decodeWireToken(value) { "unsupported swipe direction '$it'" }
    }
}
