package com.rainng.androidctl

import android.os.SystemClock
import org.json.JSONObject
import java.util.concurrent.Executors

internal class FileJobs(
    private val sessions: Sessions,
) {
    private val executor = Executors.newSingleThreadExecutor { Thread(it, "androidctl-file") }
    private val jobs = JobTable<FileJob>("Transfer", "File resources are still active")

    fun start(
        owner: SessionOwner,
        args: JSONObject,
    ): JSONObject {
        val duration = args.getLong("runMs")
        checkRunMs(duration)
        val direction = args.getString("direction")
        if (direction != "push") throw Rejected("direction", "Choose push")
        val file = TransferHandle(args.getString("path"), args.optBoolean("replace"))
        val size = args.getLong("size")
        if (size < 0) throw Rejected("size", "Invalid source size")
        val lease = sessions.acquire(owner)
        val resource = sessions.reserve(lease, ResourceKind.FILE, changing = true)
        val transfer = FileJob(resource, sessions, executor, file, SystemClock.elapsedRealtime() + duration, jobs::trim)
        jobs.start(transfer) { transfer.start(size) }
        return transfer.view()
    }

    fun call(
        owner: SessionOwner,
        method: String,
        args: JSONObject,
    ): JSONObject {
        val transfer = jobs.find(owner, args.getString("job"))
        return when (method) {
            "file.get" -> transfer.view(observe = true)
            "file.cancel" -> {
                transfer.cancel()
                transfer.view()
            }
            "file.finish" -> transfer.finish(args)
            else -> transfer.chunk(args)
        }
    }

    fun expire() {
        jobs.snapshot().forEach { it.expire() }
    }

    fun retainedPairings(): Set<String> = jobs.snapshot().map { it.owner.pairing }.toSet()

    fun revoke(pairing: String) = jobs.revoke(pairing)

    fun prepareStop(action: () -> Unit) = jobs.prepareStop(action)
}
