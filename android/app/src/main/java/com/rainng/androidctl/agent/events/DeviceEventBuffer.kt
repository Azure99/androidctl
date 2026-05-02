package com.rainng.androidctl.agent.events

import java.time.Instant
import java.util.ArrayDeque
import java.util.concurrent.TimeUnit
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

class DeviceEventBuffer(
    private val capacity: Int = 256,
    private val timestampProvider: () -> String = { Instant.now().toString() },
) {
    init {
        require(capacity > 0) { "capacity must be greater than 0" }
    }

    private val lock = ReentrantLock()
    private val eventPublished = lock.newCondition()
    private val events = ArrayDeque<DeviceEvent>()
    private var latestSeq = 0L
    private var resetBoundarySeq = 0L

    fun publish(data: DeviceEventPayload): DeviceEvent {
        lock.withLock {
            return publishLocked(data)
        }
    }

    fun reset() {
        lock.withLock {
            latestSeq += 1L
            resetBoundarySeq = latestSeq
            events.clear()
            eventPublished.signalAll()
        }
    }

    fun poll(request: EventPollRequest): EventPollResult {
        lock.withLock {
            if (request.waitMs <= 0L) {
                return snapshotLocked(request, timedOut = false)
            }

            var remainingNs = TimeUnit.MILLISECONDS.toNanos(request.waitMs)
            while (true) {
                val immediate = snapshotLocked(request, timedOut = false)
                if (immediate.events.isNotEmpty() || immediate.needResync) {
                    return immediate
                }

                if (remainingNs <= 0L) {
                    return snapshotLocked(request, timedOut = true)
                }

                remainingNs = eventPublished.awaitNanos(remainingNs)
            }
        }
    }

    private fun snapshotLocked(
        request: EventPollRequest,
        timedOut: Boolean,
    ): EventPollResult {
        val oldestSeq = events.firstOrNull()?.seq
        val matchingEvents =
            events
                .asSequence()
                .filter { it.seq > request.afterSeq }
                .take(request.limit)
                .map(::copyEvent)
                .toList()

        return EventPollResult(
            events = matchingEvents,
            latestSeq = latestSeq,
            needResync =
                requiresResyncAfterReset(request.afterSeq) ||
                    (request.afterSeq > 0L && oldestSeq != null && request.afterSeq < oldestSeq - 1),
            timedOut = timedOut && matchingEvents.isEmpty(),
        )
    }

    private fun publishLocked(data: DeviceEventPayload): DeviceEvent {
        val event =
            DeviceEvent(
                seq = ++latestSeq,
                timestamp = timestampProvider(),
                data = data,
            )
        if (events.size == capacity) {
            events.removeFirst()
        }
        events.addLast(event)
        eventPublished.signalAll()
        return copyEvent(event)
    }

    private fun copyEvent(event: DeviceEvent): DeviceEvent = event.copy()

    private fun requiresResyncAfterReset(afterSeq: Long): Boolean =
        afterSeq > 0L &&
            resetBoundarySeq > 0L &&
            afterSeq < resetBoundarySeq
}
