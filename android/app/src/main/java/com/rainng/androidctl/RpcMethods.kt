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
            "pair.register",
        )
    val ALLOWED_WHILE_STOPPING = setOf("job.get", "job.cancel", "output.read", "file.get", "file.cancel", "file.chunk", "file.finish")
}
