package com.rainng.androidctl

internal object RpcMethods {
    // Short-latency scheduling includes cancellation; this is not read-only authorization.
    val QUERY_METHODS =
        setOf(
            "status",
            "job.get",
            "job.cancel",
            "file.get",
            "file.cancel",
            "output.read",
            "ui.read",
            "pair.register",
        )
    val ALLOWED_WHILE_STOPPING = setOf("job.get", "job.cancel", "output.read", "file.get", "file.cancel", "file.chunk", "file.finish")

    val UI_READ_ONLY = setOf("snapshot", "screenshot", "apps")
    val SYSTEM_ACTIONS = setOf("wake", "open", "back", "home", "recents", "notifications", "key")
    val UI_COMMANDS =
        UI_READ_ONLY + SYSTEM_ACTIONS +
            setOf(
                "click",
                "fill",
                "check",
                "uncheck",
                "focus",
                "scroll",
                "tap",
                "swipe",
                "long-click",
                "expand",
                "collapse",
                "dismiss",
                "ime",
            )
}
