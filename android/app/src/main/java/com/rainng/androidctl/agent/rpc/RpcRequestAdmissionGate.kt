package com.rainng.androidctl.agent.rpc

import fi.iki.elonen.NanoHTTPD
import java.util.concurrent.TimeUnit
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

internal class RpcRequestAdmissionGate {
    private val stateLock = ReentrantLock()
    private val idleCondition = stateLock.newCondition()

    @Volatile
    private var quiescing = false

    private var activeRequests = 0

    fun enterOrReject(stoppingResponse: () -> NanoHTTPD.Response): NanoHTTPD.Response? =
        stateLock.withLock {
            if (quiescing) {
                return stoppingResponse()
            }
            activeRequests += 1
            null
        }

    fun leave() {
        stateLock.withLock {
            activeRequests = (activeRequests - 1).coerceAtLeast(0)
            if (activeRequests == 0) {
                idleCondition.signalAll()
            }
        }
    }

    fun beginShutdown() {
        quiescing = true
    }

    fun awaitQuiescence(timeoutMs: Long): Boolean =
        stateLock.withLock {
            var remainingNs = TimeUnit.MILLISECONDS.toNanos(timeoutMs)
            while (activeRequests > 0 && remainingNs > 0L) {
                remainingNs = idleCondition.awaitNanos(remainingNs)
            }
            activeRequests == 0
        }

    fun finishShutdown() {
        stateLock.withLock {
            activeRequests = 0
            idleCondition.signalAll()
        }
    }
}
