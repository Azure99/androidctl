package com.rainng.androidctl.agent.snapshot

import com.rainng.androidctl.agent.rpc.codec.JsonDecoder
import com.rainng.androidctl.agent.rpc.codec.JsonReader

internal object SnapshotGetRequestCodec : JsonDecoder<SnapshotGetRequest> {
    override fun read(reader: JsonReader): SnapshotGetRequest {
        val includeInvisible =
            reader.requiredBoolean(
                key = "includeInvisible",
                missingMessage = "snapshot.get requires includeInvisible",
                invalidMessage = "snapshot.get includeInvisible must be a boolean",
            )
        val includeSystemWindows =
            reader.requiredBoolean(
                key = "includeSystemWindows",
                missingMessage = "snapshot.get requires includeSystemWindows",
                invalidMessage = "snapshot.get includeSystemWindows must be a boolean",
            )

        return SnapshotGetRequest(
            includeInvisible = includeInvisible,
            includeSystemWindows = includeSystemWindows,
        )
    }
}
