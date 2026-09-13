package com.rainng.androidctl

import android.os.SystemClock
import org.java_websocket.WebSocket
import org.java_websocket.WebSocketImpl
import org.json.JSONObject

internal class GatewayClient(
    val socket: WebSocket,
    val pairing: String?,
    val pairingWindow: Pairings.Window?,
    val rejection: String? = null,
) {
    val channel = (socket as WebSocketImpl).channel as TrackedChannel

    // Reservation holds this connection until the response's matching ACK arrives.
    private var reservationId: String? = null
    private var replySentAt = 0L
    private var closed = false
    private var lastRequest = SystemClock.elapsedRealtime()

    @Synchronized fun reserve(): Boolean {
        if (closed || reservationId != null) return false
        lastRequest = SystemClock.elapsedRealtime()
        reservationId = token()
        replySentAt = 0
        return true
    }

    @Synchronized fun send(body: JSONObject) {
        if (closed) return
        val bytes = body.put("blockId", requireNotNull(reservationId)).toString()
        if (bytes.toByteArray().size > Limits.MESSAGE_BYTES) {
            throw Rejected("messageLimit", "Reply exceeds network budget", uncertain = true)
        }
        replySentAt = SystemClock.elapsedRealtime()
        socket.send(bytes)
    }

    @Synchronized fun acknowledge(value: String): Boolean {
        if (reservationId != value || replySentAt == 0L || closed) return false
        reservationId = null
        replySentAt = 0L
        lastRequest = SystemClock.elapsedRealtime()
        return true
    }

    private fun expired(now: Long): Boolean =
        (replySentAt != 0L && now - replySentAt > Limits.NETWORK_HANDSHAKE_MS) ||
            (
                reservationId == null &&
                    (socket as WebSocketImpl).outQueue.isEmpty() &&
                    channel.idle &&
                    now - maxOf(lastRequest, channel.lastIo) > Limits.IDLE_MS
            )

    fun abort(reason: String) {
        synchronized(this) {
            if (closed) return
            closed = true
        }
        closeSocket(reason)
    }

    fun abortIfExpired(now: Long) {
        synchronized(this) {
            if (closed || !expired(now)) return
            closed = true
        }
        closeSocket("deadline")
    }

    private fun closeSocket(reason: String) {
        // The socket closes under its own monitor and calls onClose back into this client.
        try {
            (socket as WebSocketImpl).outQueue.clear()
            socket.closeConnection(org.java_websocket.framing.CloseFrame.ABNORMAL_CLOSE, reason)
        } finally {
            channel.close()
        }
    }
}
