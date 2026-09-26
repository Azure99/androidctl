package com.rainng.androidctl

internal interface Job {
    val id: String
    val owner: SessionOwner
    val ended: Boolean

    fun cancel()
}

internal class JobTable<T : Job>(
    private val label: String,
    private val busyReason: String,
) {
    private val items = linkedMapOf<String, T>()

    @Synchronized fun start(
        job: T,
        action: () -> Unit,
    ) {
        action()
        items[job.id] = job
        trim()
    }

    @Synchronized fun find(
        owner: SessionOwner,
        id: String,
    ): T {
        val job: T = items[id] ?: throw Rejected("jobUnavailable", "$label is not retained in this worker")
        if (!job.owner.sameSession(owner)) throw Rejected("jobOwner", "$label belongs to another pairing/session")
        return job
    }

    @Synchronized fun trim() {
        while (items.count { it.value.ended } > RECENT_JOBS) items.remove(items.entries.first { it.value.ended }.key)
    }

    @Synchronized fun snapshot(): List<T> = items.values.toList()

    fun revoke(pairing: String) {
        snapshot().filter { it.owner.pairing == pairing }.forEach { it.cancel() }
    }

    @Synchronized fun prepareStop(action: () -> Unit) {
        if (items.values.any { !it.ended }) throw Rejected("busy", busyReason)
        action()
    }
}

private const val RECENT_JOBS = 64

internal fun checkRunMs(runMs: Long) {
    if (runMs !in 1..Limits.MAX_RUN_MS) throw Rejected("runMs", "runMs must be 1 to ${Limits.MAX_RUN_MS}")
}
