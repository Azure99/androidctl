package com.rainng.androidctl

import org.json.JSONObject
import java.lang.reflect.InvocationTargetException
import java.security.MessageDigest
import java.util.Base64
import java.util.UUID

internal object Limits {
    const val MESSAGE_BYTES = 256 * 1024
    const val UPGRADE_BYTES = 16 * 1024
    const val JSON_UTF8_BYTES = 96 * 1024
    const val CHUNK_BYTES = 32 * 1024
    const val CONNECTIONS = 16
    const val QUEUE = 8
    const val NETWORK_HANDSHAKE_MS = 5000L
    const val IDLE_MS = 60000L
    const val TREE_BYTES = 4 * 1024 * 1024
    const val IMAGE_BYTES = 8 * 1024 * 1024
    const val TEXT_BYTES = 65536
    const val OUTPUT_BYTES = 128 * 1024
    const val OUTPUTS = 8
    const val OUTPUT_MS = 120000L
    const val MAX_RUN_MS = 600000L
    const val NODES = 1024
    const val WINDOWS = 20
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

internal fun encodeChunk(
    bytes: ByteArray,
    offset: Int,
): String {
    if (offset !in 0..bytes.size) throw Rejected("offset", "Offset outside retained bytes")
    return Base64.getEncoder().encodeToString(bytes.copyOfRange(offset, minOf(bytes.size, offset + Limits.CHUNK_BYTES)))
}

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
