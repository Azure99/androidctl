package com.rainng.androidctl

import org.java_websocket.SocketChannelIOHelper
import org.java_websocket.WebSocketImpl
import org.java_websocket.WebSocketListener
import org.java_websocket.WrappedByteChannel
import org.java_websocket.drafts.Draft_6455
import org.java_websocket.framing.CloseFrame
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.lang.reflect.Proxy
import java.nio.ByteBuffer
import org.java_websocket.enums.Role as WebSocketRole

class WebSocketBatchTest {
    @Test fun pendingWrapperOutputMustDrainBeforeFlushAndCloseCompletes() {
        val listener =
            Proxy.newProxyInstance(javaClass.classLoader, arrayOf(WebSocketListener::class.java)) { _, _, _ -> null } as WebSocketListener
        val socket = WebSocketImpl(listener, Draft_6455())
        socket.draft.setParseMode(WebSocketRole.SERVER)
        val channel = PendingChannel()
        socket.channel = channel
        socket.outQueue.add(ByteBuffer.wrap(byteArrayOf(1)))
        socket.flushAndClose(CloseFrame.NORMAL, "Finished", false)

        assertFalse(SocketChannelIOHelper.batch(socket, channel))
        assertTrue(socket.outQueue.isEmpty())
        assertTrue(channel.isNeedWrite)
        assertTrue(channel.isOpen)

        assertTrue(SocketChannelIOHelper.batch(socket, channel))
        assertFalse(channel.isNeedWrite)
        assertFalse(channel.isOpen)
    }

    private class PendingChannel : WrappedByteChannel {
        private var pending = false
        private var open = true

        override fun write(source: ByteBuffer): Int {
            val size = source.remaining()
            source.position(source.limit())
            pending = true
            return size
        }

        override fun isNeedWrite(): Boolean = pending

        override fun writeMore() {
            pending = false
        }

        override fun isNeedRead(): Boolean = false

        override fun readMore(target: ByteBuffer): Int = 0

        override fun isBlocking(): Boolean = false

        override fun read(target: ByteBuffer): Int = 0

        override fun isOpen(): Boolean = open

        override fun close() {
            open = false
        }
    }
}
