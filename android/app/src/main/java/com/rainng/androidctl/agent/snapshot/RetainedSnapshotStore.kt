package com.rainng.androidctl.agent.snapshot

internal class RetainedSnapshotStore(
    private val limit: Int,
) {
    private val retainedSnapshots = LinkedHashMap<Long, SnapshotRecord>()

    fun putLocked(
        snapshot: SnapshotRecord,
        inFlightSnapshotIds: Set<Long>,
    ) {
        retainedSnapshots[snapshot.snapshotId] = snapshot
        trim(inFlightSnapshotIds + snapshot.snapshotId)
    }

    fun findLocked(snapshotId: Long): SnapshotRecord? = retainedSnapshots[snapshotId]

    fun clearLocked() {
        retainedSnapshots.clear()
    }

    private fun trim(inFlightSnapshotIds: Set<Long>) {
        while (retainedSnapshots.size > limit) {
            val oldestEvictableSnapshotId =
                retainedSnapshots.keys.firstOrNull { it !in inFlightSnapshotIds }
            if (oldestEvictableSnapshotId == null) {
                break
            }
            retainedSnapshots.remove(oldestEvictableSnapshotId)
        }
    }
}
