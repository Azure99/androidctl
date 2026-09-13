package com.rainng.androidctl

import android.os.SystemClock
import org.java_websocket.WrappedByteChannel
import org.java_websocket.interfaces.ISSLChannel
import java.nio.ByteBuffer
import java.nio.channels.ByteChannel
import java.nio.channels.SelectionKey
import java.nio.channels.SocketChannel
import java.util.concurrent.atomic.AtomicInteger
import javax.net.ssl.SSLEngine
import javax.net.ssl.SSLEngineResult

internal class TrackedChannel(
    private val raw: SocketChannel,
    channel: ByteChannel,
    private val key: SelectionKey,
    private val ended: (TrackedChannel) -> Unit,
) : WrappedByteChannel,
    ISSLChannel {
    private val delegate = channel as WrappedByteChannel
    private val ssl = channel as ISSLChannel
    private val inFlight = AtomicInteger()
    private val created = SystemClock.elapsedRealtime()

    @Volatile private var tlsReady = 0L

    @Volatile var websocketReady = false
        private set

    @Volatile private var closed = false

    private var upgradeBytes = 0
    val idle: Boolean get() = inFlight.get() == 0 && !delegate.isNeedWrite

    @Volatile var lastIo = created
        private set

    fun expired(now: Long): Boolean {
        if (tlsReady == 0L && sslEngine.handshakeStatus == SSLEngineResult.HandshakeStatus.NOT_HANDSHAKING) tlsReady = now
        return when {
            tlsReady == 0L -> now - created > Limits.NETWORK_HANDSHAKE_MS
            !websocketReady -> now - tlsReady > Limits.NETWORK_HANDSHAKE_MS
            else -> false
        }
    }

    @Synchronized fun websocketOpened(): Boolean {
        if (closed) return false
        websocketReady = true
        return true
    }

    @Synchronized fun closeIfExpired(now: Long) {
        if (expired(now)) close()
    }

    private inline fun <T> io(action: () -> T): T {
        inFlight.incrementAndGet()
        return try {
            action()
        } finally {
            if (inFlight.decrementAndGet() == 0 && closed) ended(this)
        }
    }

    private fun checkedRead(count: Int): Int {
        if (!websocketReady && count > 0) {
            upgradeBytes += count
            if (upgradeBytes > Limits.UPGRADE_BYTES) {
                close()
                throw java.io.IOException("Upgrade input exceeds the byte budget")
            }
        }
        return count
    }

    private fun recordIo(count: Int): Int {
        if (count > 0) lastIo = SystemClock.elapsedRealtime()
        return count
    }

    override fun read(dst: ByteBuffer): Int = io { recordIo(checkedRead(delegate.read(dst))) }

    override fun write(src: ByteBuffer): Int = io { recordIo(delegate.write(src)) }

    override fun readMore(dst: ByteBuffer): Int = io { recordIo(checkedRead(delegate.readMore(dst))) }

    override fun writeMore() =
        io {
            delegate.writeMore()
            lastIo = SystemClock.elapsedRealtime()
        }

    override fun isNeedRead(): Boolean = delegate.isNeedRead

    override fun isNeedWrite(): Boolean = delegate.isNeedWrite

    override fun isBlocking(): Boolean = delegate.isBlocking

    override fun isOpen(): Boolean = delegate.isOpen

    override fun getSSLEngine(): SSLEngine = ssl.sslEngine

    fun abortRaw() {
        try {
            raw.close()
        } finally {
            // Registered socket destruction waits for selector deregistration.
            key.selector().wakeup()
        }
    }

    @Synchronized override fun close() {
        if (closed) return
        try {
            abortRaw()
            delegate.close()
        } finally {
            closed = true
            if (inFlight.get() == 0) ended(this)
        }
    }
}
