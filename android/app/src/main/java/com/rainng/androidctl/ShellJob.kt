package com.rainng.androidctl

import android.os.SystemClock
import org.json.JSONObject
import java.io.DataInputStream
import java.io.File

internal data class ShellSpec(
    val executable: String,
    val slotLock: SlotLockIdentity,
    val script: String,
    val runMs: Long,
    val background: Boolean,
)

// One owner reports and closes every helper protocol/I/O failure.
internal class ShellJob(
    private val resource: Resource,
    private val sessions: Sessions,
    outputs: OutputStore,
    private val spec: ShellSpec,
    private val checkWorker: () -> Unit,
    private val completed: () -> Unit,
) : Job {
    override val id = "j" + token()
    private val lease = resource.lease
    override val owner: SessionOwner = lease.owner
    private val protocol = ShellProtocol(id, outputs, releaseForeground = ::releaseForeground)
    private var process: Process? = null
    private var cancelled = false

    @Volatile override var ended = false
        private set

    fun start() {
        Thread({ run() }, "androidctl-shell").start()
    }

    @Synchronized override fun cancel() {
        if (cancelled || ended) return
        cancelled = true
        process?.outputStream?.let {
            try {
                it.write("CANCEL\n".toByteArray())
                it.flush()
            } catch (error: Exception) {
                protocol.cleanupFailed(error)
            }
        }
    }

    @Synchronized fun view(): JSONObject = protocol.view(ended)

    private fun run() {
        var active: Process? = null
        try {
            active =
                ProcessBuilder(
                    listOf(
                        spec.executable,
                        (SystemClock.elapsedRealtime() + spec.runMs).toString(),
                        spec.script,
                        spec.slotLock.path,
                        spec.slotLock.device.toString(),
                        spec.slotLock.inode.toString(),
                    ),
                ).redirectError(ProcessBuilder.Redirect.to(File("/dev/null")))
                    .start()
            synchronized(this) { process = active }
            readFrames(active)
        } catch (error: Exception) {
            synchronized(this) { protocol.failed(error) }
        } finally {
            // The worker owns this control pipe; a lost CLI never closes it.
            closeStreams(active)
            active?.waitFor()
            synchronized(this) {
                protocol.ended()
                ended = true
            }
            completed()
        }
    }

    private fun closeStreams(active: Process?) {
        listOfNotNull(active?.outputStream, active?.inputStream, active?.errorStream).forEach { stream ->
            try {
                stream.close()
            } catch (error: Exception) {
                synchronized(this) { protocol.cleanupFailed(error) }
            }
        }
    }

    private fun readFrames(active: Process) {
        val input = DataInputStream(active.inputStream)
        while (true) {
            val frame = readShellFrame(input) ?: break
            synchronized(this) {
                if (protocol.accept(frame)) ready(active)
            }
        }
    }

    private fun ready(active: Process) {
        if (cancelled || !sessions.isCurrent(lease)) {
            active.outputStream.write("CANCEL\n".toByteArray())
            active.outputStream.flush()
            return
        }
        checkWorker()
        sessions.withCurrentLease(lease) {
            protocol.sent = true
            active.outputStream.write("GO\n".toByteArray())
            active.outputStream.flush()
            if (spec.background) releaseForeground()
        }
    }

    private fun releaseForeground() = sessions.finished(resource)
}
