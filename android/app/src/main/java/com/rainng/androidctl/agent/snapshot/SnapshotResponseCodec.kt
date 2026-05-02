package com.rainng.androidctl.agent.snapshot

import com.rainng.androidctl.agent.rpc.codec.DisplayFragmentCodec
import com.rainng.androidctl.agent.rpc.codec.ForegroundContextFragmentCodec
import com.rainng.androidctl.agent.rpc.codec.ImeFragmentCodec
import com.rainng.androidctl.agent.rpc.codec.JsonEncoder
import com.rainng.androidctl.agent.rpc.codec.JsonWriter

internal object SnapshotResponseCodec : JsonEncoder<SnapshotPayload> {
    override fun write(
        writer: JsonWriter,
        value: SnapshotPayload,
    ) {
        writer.requiredLong("snapshotId", value.snapshotId)
        writer.requiredString("capturedAt", value.capturedAt)
        ForegroundContextFragmentCodec.writeFields(writer, value.packageName, value.activityName)
        writer.objectField("display") { display ->
            DisplayFragmentCodec.write(display, value.display)
        }
        writer.objectField("ime") { ime ->
            ImeFragmentCodec.write(ime, value.ime)
        }
        writer.array("windows") { windows ->
            value.windows.forEach { window ->
                windows.objectElement { windowWriter -> writeWindow(windowWriter, window) }
            }
        }
        writer.array("nodes") { nodes ->
            value.nodes.forEach { node ->
                nodes.objectElement { nodeWriter -> writeNode(nodeWriter, node) }
            }
        }
    }

    private fun writeWindow(
        writer: JsonWriter,
        window: SnapshotWindow,
    ) {
        writer.requiredString("windowId", window.windowId)
        writer.requiredString("type", window.type)
        writer.requiredInt("layer", window.layer)
        writer.nullableString("packageName", window.packageName)
        writer.writeIntArray("bounds", window.bounds)
        writer.requiredString("rootRid", window.rootRid)
    }

    private fun writeNode(
        writer: JsonWriter,
        node: SnapshotNode,
    ) {
        writer.requiredString("rid", node.rid)
        writer.requiredString("windowId", node.windowId)
        writer.nullableString("parentRid", node.parentRid)
        writer.writeStringArray("childRids", node.childRids)
        writer.requiredString("className", node.className)
        writer.nullableString("resourceId", node.resourceId)
        writer.nullableString("text", node.text)
        writer.nullableString("contentDesc", node.contentDesc)
        writer.nullableString("hintText", node.hintText)
        writer.nullableString("stateDescription", node.stateDescription)
        writer.nullableString("paneTitle", node.paneTitle)
        writer.nullableString("packageName", node.packageName)
        writer.writeIntArray("bounds", node.bounds)
        writer.requiredBoolean("visibleToUser", node.visibleToUser)
        writer.requiredBoolean("importantForAccessibility", node.importantForAccessibility)
        writer.requiredBoolean("clickable", node.clickable)
        writer.requiredBoolean("enabled", node.enabled)
        writer.requiredBoolean("editable", node.editable)
        writer.requiredBoolean("focusable", node.focusable)
        writer.requiredBoolean("focused", node.focused)
        writer.requiredBoolean("checkable", node.checkable)
        writer.requiredBoolean("checked", node.checked)
        writer.requiredBoolean("selected", node.selected)
        writer.requiredBoolean("scrollable", node.scrollable)
        writer.requiredBoolean("password", node.password)
        writer.writeStringArray("actions", node.actions)
    }

    private fun JsonWriter.writeIntArray(
        fieldName: String,
        values: List<Int>,
    ) {
        array(fieldName) { writer ->
            values.forEach(writer::requiredIntValue)
        }
    }

    private fun JsonWriter.writeStringArray(
        fieldName: String,
        values: List<String>,
    ) {
        array(fieldName) { writer ->
            values.forEach(writer::requiredStringValue)
        }
    }
}
