package com.rainng.androidctl.agent.rpc.codec

import com.rainng.androidctl.agent.events.ImeChangedPayload
import com.rainng.androidctl.agent.runtime.ObservedWindowState
import com.rainng.androidctl.agent.snapshot.SnapshotDisplay
import com.rainng.androidctl.agent.snapshot.SnapshotIme

internal object DisplayFragmentCodec : JsonEncoder<SnapshotDisplay> {
    override fun write(
        writer: JsonWriter,
        value: SnapshotDisplay,
    ) = writeFields(writer, value.widthPx, value.heightPx, value.densityDpi, value.rotation)

    private fun writeFields(
        writer: JsonWriter,
        widthPx: Int,
        heightPx: Int,
        densityDpi: Int,
        rotation: Int,
    ) {
        writer.requiredInt("widthPx", widthPx)
        writer.requiredInt("heightPx", heightPx)
        writer.requiredInt("densityDpi", densityDpi)
        writer.requiredInt("rotation", rotation)
    }
}

internal object ImeFragmentCodec : JsonEncoder<SnapshotIme> {
    override fun write(
        writer: JsonWriter,
        value: SnapshotIme,
    ) = writeFields(writer, value.visible, value.windowId)

    fun write(
        writer: JsonWriter,
        value: ImeChangedPayload,
    ) = writeFields(writer, value.visible, value.windowId)

    private fun writeFields(
        writer: JsonWriter,
        visible: Boolean,
        windowId: String?,
    ) {
        writer.requiredBoolean("visible", visible)
        writer.nullableString("windowId", windowId)
    }
}

internal object ForegroundContextFragmentCodec : JsonEncoder<ObservedWindowState> {
    override fun write(
        writer: JsonWriter,
        value: ObservedWindowState,
    ) = writeFields(writer, value.packageName, value.activityName)

    fun writeFields(
        writer: JsonWriter,
        packageName: String?,
        activityName: String?,
    ) {
        writer.nullableString("packageName", packageName)
        writer.nullableString("activityName", activityName)
    }

    fun writeRequiredPackageAndNullableActivity(
        writer: JsonWriter,
        packageName: String,
        activityName: String?,
    ) {
        writer.requiredString("packageName", packageName)
        writer.nullableString("activityName", activityName)
    }
}
