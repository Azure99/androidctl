package com.rainng.androidctl

import android.os.SystemClock.elapsedRealtime
import org.json.JSONObject
import java.util.Base64
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

internal class FileJob(
    private val resource: Resource,
    private val sessions: Sessions,
    private val executor: ExecutorService,
    private val file: TransferHandle,
    private val deadline: Long,
    private val completed: () -> Unit,
) : Job {
    override val id = "f" + token()
    private val direction: String get() = if (file.push) "push" else "pull"
    override val owner: SessionOwner = resource.lease.owner
    private var fact: JSONObject? = null
    private var ready = false
    private var working = false
    private var lastStep: JSONObject? = null
    private var begun = false
    private var lastActivity = elapsedRealtime()

    @Volatile private var cancelled = false

    @Volatile override var ended = false
        private set

    fun start(size: Long) {
        submit {
            synchronized(this) {
                sessions.withCurrentLease(resource.lease) {
                    checkActive()
                    begun = true
                }
            }
            file.open(size)
            synchronized(this) {
                ready = true
            }
            view()
        }
    }

    @Synchronized fun view(observe: Boolean = false): JSONObject {
        if (observe) lastActivity = elapsedRealtime()
        val value =
            fact?.let(::copy) ?: outcome(
                direction,
                Outcome.RUNNING,
                data =
                    json(
                        "job" to id,
                        "ready" to (ready && !working),
                        "size" to file.size,
                        "step" to if (working) null else lastStep?.let(::copy),
                    ),
            )
        value.getJSONObject("data").put("resourcesActive", !ended)
        return value
    }

    override fun cancel() {
        synchronized(this) {
            if (ended) return
            cancelled = true
        }
        submit(cancelling = true) {
            checkActive()
            view()
        }
    }

    fun expire() {
        val expired =
            synchronized(this) {
                if (ended) return@synchronized false
                val deadlineReached = elapsedRealtime() >= deadline
                val idle = !deadlineReached && !working && elapsedRealtime() - lastActivity >= TRANSFER_IDLE_MS
                val expired = deadlineReached || idle
                if (expired) cancelled = true
                expired
            }
        if (expired) cancel()
    }

    fun finish(args: JSONObject): JSONObject =
        step {
            synchronized(this) {
                ready = false
            }
            val details =
                file.finish(args.getString("sha256")) {
                    synchronized(this) {
                        checkActive()
                    }
                }
            complete(outcome(direction, data = details.put("job", id)))
            view()
        }

    fun chunk(args: JSONObject): JSONObject =
        step {
            val offset = args.getLong("offset")
            if (offset != file.offset) throw Rejected("offset", "Unexpected file offset; a chunk is never repeated automatically")
            val data =
                if (file.push) {
                    val bytes = decodeFileChunk(args.getString("bytes"))
                    if (bytes.isEmpty() || bytes.size > Limits.CHUNK_BYTES) throw Rejected("chunk", "Invalid chunk size")
                    file.write(bytes)
                    json("offset" to file.offset)
                } else {
                    json("offset" to offset, "bytes" to Base64.getEncoder().encodeToString(file.read()))
                }
            val value = outcome("file chunk", data = data.put("job", id))
            synchronized(this) {
                lastStep = json("offset" to offset, "result" to value)
            }
            value
        }

    private fun step(action: () -> JSONObject): JSONObject {
        val future =
            submit(requireReady = true) {
                checkActive()
                action()
            }
        return try {
            future.get(minOf(STEP_WAIT_MS, maxOf(1, deadline - elapsedRealtime())), TimeUnit.MILLISECONDS)
        } catch (_: TimeoutException) {
            view()
        }
    }

    // Platform/file errors still require the same resource owner to close.
    private fun submit(
        cancelling: Boolean = false,
        requireReady: Boolean = false,
        action: () -> JSONObject,
    ): CompletableFuture<JSONObject> {
        val answer = CompletableFuture<JSONObject>()
        synchronized(this) {
            if (cancelling && (working || ended)) return CompletableFuture.completedFuture(view())
            if (ended || (requireReady && !ready)) throw Rejected("transferState", "Transfer is not ready for another step")
            if (working) throw Rejected("busy", "The previous file step is still running; query its ID")
            working = true
            lastActivity = elapsedRealtime()
            lastStep = null
        }
        executor.execute {
            execute(action, answer)
        }
        return answer
    }

    // The I/O owner also completes failed platform steps.
    private fun execute(
        action: () -> JSONObject,
        answer: CompletableFuture<JSONObject>,
    ) {
        var value: JSONObject
        try {
            value = action()
        } catch (error: Exception) {
            fail(error)
            value = view()
        }
        val stop =
            synchronized(this) {
                lastActivity = elapsedRealtime()
                val stop = !ended && cancelled
                if (!stop) working = false
                stop
            }
        if (stop) fail(Rejected("cancelled", "Transfer cancelled"))
        answer.complete(if (ended) view() else value)
    }

    private fun checkActive() {
        if (cancelled || elapsedRealtime() >= deadline) throw Rejected("cancelled", "Transfer cancelled or deadline reached")
        sessions.requireAuthorized(owner)
    }

    private fun fail(error: Exception) {
        complete(
            fileFailure(
                command = direction,
                id = id,
                begun = begun,
                opened = file.opened,
                publicationAttempted = file.publicationAttempted,
                error = error,
            ),
        )
    }

    private fun complete(value: JSONObject) {
        synchronized(this) {
            ready = false
            fact = value
        }
        val cleanup = file.close()
        sessions.finished(resource)
        synchronized(this) {
            cleanup?.let {
                value.getJSONObject("data").put("cleanupError", it)
                if (value.getString("result") == Outcome.NOT_SENT) value.put("result", Outcome.FAILED)
            }
            working = false
            ended = true
        }
        completed()
    }

    companion object {
        private const val STEP_WAIT_MS = 5000L
        const val TRANSFER_IDLE_MS = 120000L
    }
}

internal fun decodeFileChunk(encoded: String): ByteArray =
    try {
        Base64.getDecoder().decode(encoded)
    } catch (_: IllegalArgumentException) {
        throw Rejected("chunk", "Invalid base64 chunk")
    }

@Suppress("LongParameterList") // These are the existing dispatch and file publication facts.
internal fun fileFailure(
    command: String,
    id: String,
    begun: Boolean,
    opened: Boolean,
    publicationAttempted: Boolean,
    error: Exception,
): JSONObject {
    val result =
        when {
            publicationAttempted && uncertain(error) -> Outcome.UNKNOWN
            !opened -> Outcome.NOT_SENT
            else -> Outcome.FAILED
        }
    val reason =
        if (!begun && error is Rejected && error.code == "sessionChanged") {
            "Session released before transfer began; file open was not attempted"
        } else {
            message(error)
        }
    return outcome(command, result, reason, json("job" to id)).put("error", problem(error))
}
