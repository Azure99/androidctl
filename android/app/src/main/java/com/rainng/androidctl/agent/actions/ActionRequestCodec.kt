package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.rpc.codec.JsonDecoder
import com.rainng.androidctl.agent.rpc.codec.JsonReader

internal object ActionRequestCodec : JsonDecoder<ActionRequest> {
    override fun read(reader: JsonReader): ActionRequest {
        val kind = readActionKind(reader)
        val target =
            ActionTargetCodec.read(
                reader.requiredObject(
                    key = "target",
                    missingMessage = "action.perform requires target",
                    invalidMessage = "action.perform requires target",
                ),
            )
        val timeoutMs = readTimeoutMs(reader)
        return when (kind) {
            ActionKind.Tap ->
                TapActionRequest(
                    target = requireTapTarget(target, "tap requires handle or coordinates target"),
                    timeoutMs = timeoutMs,
                )

            ActionKind.LongTap ->
                LongTapActionRequest(
                    target = requireTapTarget(target, "tap requires handle or coordinates target"),
                    timeoutMs = timeoutMs,
                )

            ActionKind.Type ->
                TypeActionRequest(
                    target = requireHandleTarget(target, "type requires handle target"),
                    timeoutMs = timeoutMs,
                    input = readTypeInput(reader),
                )

            ActionKind.Node ->
                NodeActionRequest(
                    target = requireHandleTarget(target, "node action requires handle target"),
                    timeoutMs = timeoutMs,
                    action = readNodeAction(reader),
                )

            ActionKind.Scroll ->
                ScrollActionRequest(
                    target = requireHandleTarget(target, "scroll requires handle target"),
                    timeoutMs = timeoutMs,
                    direction = readScrollDirection(reader),
                )

            ActionKind.Global ->
                GlobalActionRequest(
                    timeoutMs = timeoutMs,
                    action = readGlobalAction(reader),
                    target = requireNoneTarget(target, "global action requires none target"),
                )

            ActionKind.Gesture -> readGestureRequest(reader, timeoutMs, target)
            ActionKind.LaunchApp -> readLaunchAppRequest(reader, timeoutMs, target)
            ActionKind.OpenUrl -> readOpenUrlRequest(reader, timeoutMs, target)
        }
    }

    fun readTimeoutMs(reader: JsonReader): Long =
        readTimeoutMsFromOptions(
            reader.requiredObject(
                key = "options",
                missingMessage = "action.perform requires options",
                invalidMessage = "options must be a JSON object",
            ),
        )

    fun readTimeoutMsFromOptions(optionsReader: JsonReader): Long {
        val timeoutMs =
            optionsReader.let { options ->
                options.requireOnlyKeys(setOf("timeoutMs"), "options")
                options.requiredLong(
                    key = "timeoutMs",
                    missingMessage = "action.perform requires options.timeoutMs",
                    invalidMessage = "options.timeoutMs must be an integer",
                )
            }

        if (timeoutMs <= 0L) {
            throw RequestValidationException("options.timeoutMs must be greater than 0")
        }
        if (timeoutMs > RequestBudgets.MAX_ACTION_TIMEOUT_MS) {
            throw RequestValidationException("options.timeoutMs must be <= ${RequestBudgets.MAX_ACTION_TIMEOUT_MS}")
        }
        return timeoutMs
    }

    private fun readActionKind(reader: JsonReader): ActionKind {
        val rawKind =
            reader.requiredString(
                key = "kind",
                missingMessage = "action.perform requires kind",
                invalidMessage = "action.perform requires kind",
            )
        if (rawKind.isBlank()) {
            throw RequestValidationException("action.perform requires kind")
        }
        return ActionKind.decode(rawKind)
    }

    private fun readTypeInput(reader: JsonReader): ActionTextInput {
        val input =
            reader.requiredObject(
                key = "input",
                missingMessage = "type requires input payload",
                invalidMessage = "type requires input payload",
            )
        input.requireOnlyKeys(setOf("text", "replace", "submit", "ensureFocused"), "input")
        val replace =
            input.requiredBoolean(
                key = "replace",
                missingMessage = "type requires input.replace",
                invalidMessage = "type input.replace must be a boolean",
            )
        val text =
            input.requiredString(
                key = "text",
                missingMessage = "type requires text string",
                invalidMessage = "type requires text string",
            )
        if (text.isEmpty() && !replace) {
            throw RequestValidationException("type requires non-empty text unless replace=true")
        }
        return ActionTextInput(
            text = text,
            replace = replace,
            submit =
                input.requiredBoolean(
                    key = "submit",
                    missingMessage = "type requires input.submit",
                    invalidMessage = "type input.submit must be a boolean",
                ),
            ensureFocused =
                input.requiredBoolean(
                    key = "ensureFocused",
                    missingMessage = "type requires input.ensureFocused",
                    invalidMessage = "type input.ensureFocused must be a boolean",
                ),
        )
    }

    private fun readNodeAction(reader: JsonReader): NodeAction {
        val action =
            RequiredSingleStringPayloadSpec(
                payloadKey = "node",
                stringKey = "action",
                payloadMessage = "node action requires action name",
                stringMessage = "node action requires action name",
            ).read(reader)
        return NodeAction.decode(action)
    }

    private fun readScrollDirection(reader: JsonReader): ScrollDirection {
        val direction =
            RequiredSingleStringPayloadSpec(
                payloadKey = "scroll",
                stringKey = "direction",
                payloadMessage = "scroll requires direction",
                stringMessage = "scroll requires direction",
            ).read(reader)
        return ScrollDirection.decode(direction)
    }

    private fun readGlobalAction(reader: JsonReader): GlobalAction {
        val action =
            RequiredSingleStringPayloadSpec(
                payloadKey = "global",
                stringKey = "action",
                payloadMessage = "global action requires action name",
                stringMessage = "global action requires action name",
            ).read(reader)
        return GlobalAction.decode(action)
    }

    private fun readGestureRequest(
        reader: JsonReader,
        timeoutMs: Long,
        target: ActionTarget,
    ): GestureActionRequest {
        val rawDirection =
            RequiredSingleStringPayloadSpec(
                payloadKey = "gesture",
                stringKey = "direction",
                payloadMessage = "gesture requires gesture payload",
                stringMessage = "gesture requires direction",
            ).read(reader)
        return GestureActionRequest(
            timeoutMs = timeoutMs,
            direction = GestureDirection.decode(rawDirection),
            target = requireNoneTarget(target, "gesture requires none target"),
        )
    }

    private fun readLaunchAppRequest(
        reader: JsonReader,
        timeoutMs: Long,
        target: ActionTarget,
    ): LaunchAppActionRequest {
        val packageName =
            RequiredSingleStringPayloadSpec(
                payloadKey = "intent",
                stringKey = "packageName",
                payloadMessage = "launchApp requires intent payload",
                stringMessage = "launchApp requires packageName",
            ).read(reader)
        return LaunchAppActionRequest(
            timeoutMs = timeoutMs,
            packageName = packageName,
            target = requireNoneTarget(target, "launchApp requires none target"),
        )
    }

    private fun readOpenUrlRequest(
        reader: JsonReader,
        timeoutMs: Long,
        target: ActionTarget,
    ): OpenUrlActionRequest {
        val url =
            RequiredSingleStringPayloadSpec(
                payloadKey = "intent",
                stringKey = "url",
                payloadMessage = "openUrl requires intent payload",
                stringMessage = "openUrl requires url",
            ).read(reader)
        return OpenUrlActionRequest(
            timeoutMs = timeoutMs,
            url = url,
            target = requireNoneTarget(target, "openUrl requires none target"),
        )
    }

    private data class RequiredSingleStringPayloadSpec(
        val payloadKey: String,
        val stringKey: String,
        val payloadMessage: String,
        val stringMessage: String,
    ) {
        fun read(reader: JsonReader): String {
            val payload =
                reader.requiredObject(
                    key = payloadKey,
                    missingMessage = payloadMessage,
                    invalidMessage = payloadMessage,
                )
            payload.requireOnlyKeys(setOf(stringKey), payloadKey)
            val value =
                payload.requiredString(
                    key = stringKey,
                    missingMessage = stringMessage,
                    invalidMessage = stringMessage,
                )
            if (value.isBlank()) {
                throw RequestValidationException(stringMessage)
            }
            return value
        }
    }
}
