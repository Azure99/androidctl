package com.rainng.androidctl.agent.device

import com.rainng.androidctl.agent.rpc.codec.JsonEncoder
import com.rainng.androidctl.agent.rpc.codec.JsonWriter

internal object AppsListResponseCodec : JsonEncoder<AppsListResponse> {
    override fun write(
        writer: JsonWriter,
        value: AppsListResponse,
    ) {
        writer.array("apps") { apps ->
            value.apps.forEach { app ->
                apps.objectElement { entry ->
                    entry.requiredString("packageName", app.packageName)
                    entry.requiredString("appLabel", app.appLabel)
                    entry.requiredBoolean("launchable", app.launchable)
                }
            }
        }
    }
}
