package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.rpc.codec.JsonCodec
import com.rainng.androidctl.agent.rpc.codec.JsonReader
import com.rainng.androidctl.agent.rpc.codec.JsonWriter

internal object ActionTargetCodec : JsonCodec<ActionTarget> {
    override fun read(reader: JsonReader): ActionTarget {
        val rawKind = reader.optionalNullableString("kind", "target kind must be a string")
        val kind =
            when (rawKind) {
                TargetKind.Handle.wireName -> TargetKind.Handle
                TargetKind.Coordinates.wireName -> TargetKind.Coordinates
                TargetKind.None.wireName -> TargetKind.None
                else -> throw RequestValidationException("unsupported target kind")
            }
        return when (kind) {
            TargetKind.Handle -> readHandleTarget(reader)
            TargetKind.Coordinates -> readCoordinatesTarget(reader)
            TargetKind.None -> {
                reader.requireOnlyKeys(setOf("kind"), "target")
                ActionTarget.None
            }
        }
    }

    override fun write(
        writer: JsonWriter,
        value: ActionTarget,
    ) {
        writer.requiredString("kind", value.kind.wireName)
        when (value) {
            is ActionTarget.Handle ->
                writer.objectField("handle") {
                    it.requiredLong("snapshotId", value.snapshotId)
                    it.requiredString("rid", value.rid)
                }

            is ActionTarget.Coordinates -> {
                writer.requiredDouble("x", value.x.toDouble())
                writer.requiredDouble("y", value.y.toDouble())
            }

            ActionTarget.None -> Unit
        }
    }

    private fun readHandleTarget(reader: JsonReader): ActionTarget.Handle {
        reader.requireOnlyKeys(setOf("kind", "handle"), "target")
        val handle =
            reader.requiredObject(
                key = "handle",
                missingMessage = "handle target requires handle payload",
                invalidMessage = "handle target requires handle payload",
            )
        handle.requireOnlyKeys(setOf("snapshotId", "rid"), "target.handle")
        val rid =
            handle.requiredString(
                key = "rid",
                missingMessage = "handle target requires rid",
                invalidMessage = "handle target requires rid",
            )
        if (rid.isBlank()) {
            throw RequestValidationException("handle target requires rid")
        }
        return ActionTarget.Handle(
            snapshotId =
                handle.requiredLong(
                    key = "snapshotId",
                    missingMessage = "handle target requires snapshotId",
                    invalidMessage = "handle target requires snapshotId",
                ),
            rid = rid,
        )
    }

    private fun readCoordinatesTarget(reader: JsonReader): ActionTarget.Coordinates {
        reader.requireOnlyKeys(setOf("kind", "x", "y"), "target")
        return ActionTarget.Coordinates(
            x =
                reader
                    .requiredDouble(
                        key = "x",
                        missingMessage = "coordinates target requires x",
                        invalidMessage = "coordinates target requires x",
                    ).toFloat(),
            y =
                reader
                    .requiredDouble(
                        key = "y",
                        missingMessage = "coordinates target requires y",
                        invalidMessage = "coordinates target requires y",
                    ).toFloat(),
        )
    }
}

internal fun JsonReader.requireOnlyKeys(
    allowedKeys: Set<String>,
    objectName: String,
) {
    val iterator = toJsonObject().keys()
    while (iterator.hasNext()) {
        val key = iterator.next()
        if (!allowedKeys.contains(key)) {
            throw RequestValidationException("$objectName contains unknown field '$key'")
        }
    }
}
