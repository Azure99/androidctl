package com.rainng.androidctl.agent.events

import com.rainng.androidctl.agent.rpc.codec.ForegroundContextFragmentCodec
import com.rainng.androidctl.agent.rpc.codec.ImeFragmentCodec
import com.rainng.androidctl.agent.rpc.codec.JsonEncoder
import com.rainng.androidctl.agent.rpc.codec.JsonWriter

internal object DeviceEventCodec : JsonEncoder<DeviceEvent> {
    override fun write(
        writer: JsonWriter,
        value: DeviceEvent,
    ) {
        writer.requiredLong("seq", value.seq)
        writer.requiredString("type", value.data.wireType)
        writer.requiredString("timestamp", value.timestamp)
        writer.objectField("data") { dataWriter ->
            writePayload(writer = dataWriter, payload = value.data)
        }
    }

    private fun writePayload(
        writer: JsonWriter,
        payload: DeviceEventPayload,
    ) {
        when (payload) {
            is RuntimeStatusPayload -> {
                writer.requiredBoolean("serverRunning", payload.serverRunning)
                writer.requiredBoolean("accessibilityEnabled", payload.accessibilityEnabled)
                writer.requiredBoolean("accessibilityConnected", payload.accessibilityConnected)
                writer.requiredBoolean("runtimeReady", payload.runtimeReady)
            }

            is PackageChangedPayload -> {
                ForegroundContextFragmentCodec.writeRequiredPackageAndNullableActivity(
                    writer = writer,
                    packageName = payload.packageName,
                    activityName = payload.activityName,
                )
            }

            is WindowChangedPayload -> {
                ForegroundContextFragmentCodec.writeFields(
                    writer = writer,
                    packageName = payload.packageName,
                    activityName = payload.activityName,
                )
                writer.requiredString("reason", payload.reason)
            }

            is FocusChangedPayload -> {
                ForegroundContextFragmentCodec.writeFields(
                    writer = writer,
                    packageName = payload.packageName,
                    activityName = payload.activityName,
                )
                writer.requiredString("reason", payload.reason)
            }

            is ImeChangedPayload -> ImeFragmentCodec.write(writer, payload)

            is SnapshotInvalidatedPayload -> {
                writer.nullableString("packageName", payload.packageName)
                writer.requiredString("reason", payload.reason)
            }
        }
    }
}
