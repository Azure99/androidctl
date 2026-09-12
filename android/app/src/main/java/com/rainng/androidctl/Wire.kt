package com.rainng.androidctl

import org.json.JSONObject
import java.lang.reflect.InvocationTargetException
import java.security.MessageDigest
import java.util.UUID

internal object Limits {
    const val MESSAGE_BYTES = 256 * 1024
    const val UPGRADE_BYTES = 16 * 1024
    const val JSON_UTF8_BYTES = 96 * 1024
    const val CONNECTIONS = 16
    const val QUEUE = 8
    const val NETWORK_HANDSHAKE_MS = 5000L
    const val IDLE_MS = 60000L
    const val PORT = 18843
}

internal class Rejected(
    val code: String,
    override val message: String,
    val uncertain: Boolean = false,
) : RuntimeException(message)

internal object Outcome {
    const val DONE = "done"
    const val FAILED = "failed"
    const val NOT_SENT = "not sent"
    const val UNKNOWN = "UNKNOWN"
    const val RUNNING = "still running"

    fun undelivered(sent: Boolean): String = if (sent) UNKNOWN else NOT_SENT
}

internal fun json(vararg fields: Pair<String, Any?>): JSONObject =
    JSONObject().apply { fields.forEach { (key, value) -> put(key, value ?: JSONObject.NULL) } }

internal fun copy(value: JSONObject): JSONObject = JSONObject(value.toString())

internal fun token(): String = UUID.randomUUID().toString()

internal fun displayName(value: String): String =
    value
        .map {
            if (it.isISOControl() || Character.getType(it) == Character.FORMAT.toInt()) "\\u%04x".format(it.code) else it.toString()
        }.joinToString("")

internal fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

internal fun rootCause(error: Throwable): Throwable = if (error is InvocationTargetException) error.targetException else error

internal fun message(error: Throwable): String = rootCause(error).let { it.message ?: it.javaClass.simpleName }

internal fun problem(error: Throwable): JSONObject {
    val cause = rootCause(error)
    return json("code" to ((cause as? Rejected)?.code ?: "workerFailure"), "message" to message(error))
}

internal fun uncertain(error: Throwable): Boolean = (rootCause(error) as? Rejected)?.uncertain ?: true

internal fun outcome(
    command: String,
    result: String = Outcome.DONE,
    reason: String? = null,
    data: JSONObject = JSONObject(),
): JSONObject =
    json(
        "command" to command,
        "result" to result,
        "reason" to reason,
        "data" to data,
    )
