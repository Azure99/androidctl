package com.rainng.androidctl

import android.annotation.SuppressLint
import android.os.SystemClock
import android.util.Log
import org.java_websocket.WebSocket
import org.java_websocket.drafts.Draft
import org.java_websocket.drafts.Draft_6455
import org.java_websocket.exceptions.InvalidDataException
import org.java_websocket.framing.CloseFrame
import org.java_websocket.handshake.ClientHandshake
import org.java_websocket.handshake.ServerHandshakeBuilder
import org.java_websocket.protocols.Protocol
import org.java_websocket.server.DefaultSSLWebSocketServerFactory
import org.java_websocket.server.WebSocketServer
import org.json.JSONObject
import java.net.InetSocketAddress
import java.nio.channels.ByteChannel
import java.nio.channels.SelectionKey
import java.nio.channels.SocketChannel
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ScheduledThreadPoolExecutor
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit

internal class NetworkGateway(
    identity: DeviceIdentity,
    private val pairings: Pairings,
    private val worker: WorkerConnection,
    private val changed: () -> Unit,
) {
    private val tlsTasks = pool("tls", 2)
    private val calls = pool("calls", 2)
    private val queries = pool("queries", 1)
    private val deadlines = ScheduledThreadPoolExecutor(1) { Thread(it, "androidctl-deadlines") }
    private val channels = ConcurrentHashMap.newKeySet<TrackedChannel>()
    private val clients = ConcurrentHashMap.newKeySet<GatewayClient>()
    private val channelGate = Any()

    @Volatile private var stopping = false
    private var resourceCloser: Thread? = null

    @Volatile var state = GatewayState.STARTING
        private set

    @Volatile var failure: String? = null
        private set
    private var closingFailure: String? = null
    private val server =
        object : WebSocketServer(
            InetSocketAddress("0.0.0.0", Limits.PORT),
            2,
            listOf(Draft_6455(emptyList(), listOf(Protocol("")), Limits.MESSAGE_BYTES)),
        ) {
            override fun onWebsocketHandshakeReceivedAsServer(
                socket: WebSocket,
                draft: Draft,
                request: ClientHandshake,
            ): ServerHandshakeBuilder {
                if (stopping || request.resourceDescriptor != "/androidctl") {
                    throw InvalidDataException(CloseFrame.POLICY_VALIDATION, "protocolMismatch")
                }
                val authorization = request.getFieldValue("Authorization")
                val pairing = pairings.authenticate(authorization)
                val compatible = request.getFieldValue("X-AndroidCtl-Version") == BuildConfig.VERSION_NAME
                val window =
                    if (compatible && authorization.isEmpty()) pairings.acceptCode(request.getFieldValue("X-AndroidCtl-Pairing")) else null
                val rejection =
                    when {
                        !compatible -> "protocolMismatch"
                        pairing == null && window == null -> "unauthorized"
                        else -> null
                    }
                socket.setAttachment(GatewayClient(socket, pairing, window, rejection))
                return super.onWebsocketHandshakeReceivedAsServer(socket, draft, request)
            }

            override fun onOpen(
                socket: WebSocket,
                handshake: ClientHandshake,
            ) {
                val client = socket.getAttachment<GatewayClient>()
                val accepted =
                    synchronized(channelGate) {
                        if (stopping || !client.channel.websocketOpened()) false else clients.add(client)
                    }
                if (!accepted) {
                    client.abort("stopping")
                    return
                }
                if (!client.reserve()) {
                    clients.remove(client)
                    return
                }
                if (client.rejection != null) {
                    client.send(json("event" to "rejected", "code" to client.rejection))
                    return
                }
                client.send(
                    json(
                        "event" to "connected",
                        "version" to BuildConfig.VERSION_NAME,
                        "deviceId" to identity.id,
                        "name" to identity.name,
                        "pairingId" to client.pairing,
                    ),
                )
            }

            @SuppressLint("SyntheticAccessor") // Keep dispatch private; the compiler bridge is intentional.
            // Malformed messages close only their own connection.
            override fun onMessage(
                socket: WebSocket,
                message: String,
            ) {
                val received = SystemClock.elapsedRealtime()
                val client = socket.getAttachment<GatewayClient>()
                if (client.rejection != null) {
                    client.abort("rejected")
                    return
                }
                try {
                    val request =
                        try {
                            JSONObject(message)
                        } catch (_: StackOverflowError) {
                            client.abort("invalidMessage")
                            return
                        }
                    if (request.has("ack")) {
                        if (request.length() != 1 || !client.acknowledge(request.getString("ack"))) client.abort("invalidAck")
                    } else {
                        handle(client, request, received)
                    }
                } catch (_: Exception) {
                    client.abort("invalidMessage")
                }
            }

            override fun onClose(
                socket: WebSocket,
                code: Int,
                reason: String,
                remote: Boolean,
            ) {
                val client = socket.getAttachment<GatewayClient?>()
                if (client != null) {
                    client.abort("closed")
                    clients.remove(client)
                }
            }

            override fun onError(
                socket: WebSocket?,
                error: Exception,
            ) {
                Log.e("AndroidCtlGateway", "network: ${error.javaClass.simpleName}")
                if (socket == null) {
                    synchronized(channelGate) {
                        if (stopping) return
                        failure = message(error)
                        state = GatewayState.FAILED
                    }
                    changed()
                }
            }

            @SuppressLint("LogConditional") // One release log marks actual listening for connection diagnostics.
            override fun onStart() {
                synchronized(channelGate) {
                    if (stopping) return
                    state = GatewayState.LISTENING
                }
                Log.i("AndroidCtlGateway", "listening ${SystemClock.elapsedRealtime()} port=${Limits.PORT}")
                changed()
            }

            fun decoderThreads(): List<Thread> = decoders.toList()
        }
    private val selectorThread = Thread(server, "androidctl-network")

    init {
        server.isReuseAddr = true
        server.isTcpNoDelay = true
        server.connectionLostTimeout = 0
        server.setWebSocketFactory(
            object : DefaultSSLWebSocketServerFactory(identity.tls, tlsTasks) {
                // Any failed wrapper construction must close its raw socket.
                override fun wrapChannel(
                    raw: SocketChannel,
                    key: SelectionKey,
                ): ByteChannel =
                    synchronized(channelGate) {
                        if (stopping || channels.size >= Limits.CONNECTIONS) {
                            raw.close()
                            throw java.io.IOException("connectionLimit")
                        }
                        try {
                            TrackedChannel(raw, super.wrapChannel(raw, key), key) { channels.remove(it) }.also { channels.add(it) }
                        } catch (error: Exception) {
                            raw.close()
                            throw error
                        }
                    }
            },
        )
        deadlines.scheduleWithFixedDelay({ expire() }, TICK_MS, TICK_MS, TimeUnit.MILLISECONDS)
    }

    fun start() {
        synchronized(channelGate) {
            if (!stopping) selectorThread.start()
        }
    }

    fun snapshot(): GatewayStatus =
        GatewayStatus(
            state,
            failure,
            GatewayTraffic(
                connections = channels.size,
                clients = clients.size,
                activeCalls = calls.activeCount,
                activeQueries = queries.activeCount,
                queuedCalls = calls.queue.size,
                queuedQueries = queries.queue.size,
                tlsActive = tlsTasks.activeCount,
                tlsQueued = tlsTasks.queue.size,
            ),
        )

    fun disconnectPairing(id: String) {
        clients.filter { it.pairing == id }.forEach { it.abort("revoked") }
    }

    // RPC boundary preserves unknown errors after possible dispatch.
    private fun handle(
        client: GatewayClient,
        request: JSONObject,
        received: Long,
    ) {
        if (!client.reserve()) {
            client.abort("outstandingRequest")
            return
        }
        val id = request.getString("id")
        if (!id.matches(REQUEST_ID)) {
            client.abort("requestId")
            return
        }
        val method = request.getString("method")
        val executor = if (method in RpcMethods.QUERY_METHODS) queries else calls
        try {
            executor.execute {
                try {
                    val args = request.getJSONObject("args")
                    val deadline = requestDeadline(method, args, received)
                    // Only the gateway supplies the worker deadline.
                    request.put("deadlineMs", deadline)
                    synchronized(channelGate) {
                        if (stopping) throw Rejected("gatewayStopping", "Gateway stopped before dispatch")
                    }
                    val value = dispatch(client, request, method, args)
                    client.send(json("id" to id, "result" to value))
                } catch (error: Exception) {
                    runCatching {
                        client.send(json("id" to id, "error" to problem(error), "uncertain" to uncertain(error)))
                    }.onFailure { client.abort("sendFailure") }
                }
            }
        } catch (_: java.util.concurrent.RejectedExecutionException) {
            runCatching {
                client.send(
                    json(
                        "id" to id,
                        "error" to problem(Rejected("queueFull", "Gateway queue is full; request was not dispatched")),
                        "uncertain" to false,
                    ),
                )
            }.onFailure { client.abort("sendFailure") }
        }
    }

    private fun requestDeadline(
        method: String,
        args: JSONObject,
        received: Long,
    ): Long {
        val milliseconds = args.getLong("timeoutMs")
        val maximum =
            minOf(
                if (method in RpcMethods.UI_COMMANDS) UiBudget.MAX_MS else Long.MAX_VALUE,
                Long.MAX_VALUE - received,
            )
        if (milliseconds !in 1..maximum) {
            throw Rejected("timeout", "Timeout must be 1 to $maximum ms")
        }
        val deadline = received + milliseconds
        if (deadline <= SystemClock.elapsedRealtime()) {
            throw Rejected("timeout", "Request budget exhausted in the gateway queue")
        }
        return deadline
    }

    private fun dispatch(
        client: GatewayClient,
        request: JSONObject,
        method: String,
        args: JSONObject,
    ): JSONObject {
        if (client.pairing == null) {
            if (method != "pair.register") throw Rejected("pairingOnly", "This connection can only register a credential")
            val id = pairings.register(requireNotNull(client.pairingWindow), args.getString("credential"), args.getString("name"))
            return json("pairingId" to id)
        }
        val verified = request.put("pairingId", client.pairing).put("pairingName", pairings.name(client.pairing))
        return worker.call(verified).apply {
            if (method == "status") {
                getJSONObject("data").put("gateway", snapshot().toJson())
            }
        }
    }

    @Suppress("TooGenericExceptionCaught") // Report each expiry failure and continue closing independent connections.
    private fun expire() {
        val now = SystemClock.elapsedRealtime()
        for (client in clients) {
            try {
                if (!client.channel.isOpen || (client.pairing != null && !pairings.authorized(client.pairing))) {
                    client.abort("closedOrRevoked")
                } else {
                    client.abortIfExpired(now)
                }
            } catch (failure: Throwable) {
                Log.e("AndroidCtlGateway", "Failed to close an expired or revoked client", failure)
            }
        }
        for (channel in channels) {
            try {
                channel.closeIfExpired(now)
            } catch (failure: Throwable) {
                Log.e("AndroidCtlGateway", "Failed to close an expired channel", failure)
            }
        }
    }

    fun openPairing() {
        synchronized(channelGate) {
            if (stopping || state != GatewayState.LISTENING) throw Rejected("gatewayNotReady", "Gateway is not listening")
            pairings.open()
        }
    }

    fun beginStop() {
        synchronized(channelGate) {
            if (stopping) return
            stopping = true
            pairings.closeWindow()
            state = GatewayState.STOPPING
            failure = null
            calls.queue.clear()
            queries.queue.clear()
            calls.shutdown()
            queries.shutdown()
            tlsTasks.shutdown()
            deadlines.shutdown()
            resourceCloser =
                Thread({
                    channels.forEach { closing { it.abortRaw() } }
                    clients.forEach { closing { it.abort("serviceStop") } }
                    channels.forEach { closing { it.close() } }
                    closing { server.stop(1) }
                }, "androidctl-network-stop").also { it.start() }
        }
        changed()
    }

    // Continue closing independent resources, retaining every error.
    private fun closing(action: () -> Unit) {
        try {
            action()
        } catch (error: Exception) {
            synchronized(channelGate) {
                closingFailure = listOfNotNull(closingFailure, error.toString()).joinToString("; ")
            }
        }
    }

    fun stop(deadline: Long = SystemClock.elapsedRealtime() + STOP_MS) {
        beginStop()
        val threads = listOfNotNull(resourceCloser, selectorThread) + server.decoderThreads()
        val pools = listOf("calls" to calls, "queries" to queries, "tls" to tlsTasks, "deadlines" to deadlines)
        for (thread in threads) {
            val remaining = deadline - SystemClock.elapsedRealtime()
            if (thread.isAlive && remaining > 0) thread.join(remaining)
        }
        for ((_, pool) in pools) {
            val remaining = deadline - SystemClock.elapsedRealtime()
            if (!pool.isTerminated && remaining > 0) pool.awaitTermination(remaining, TimeUnit.MILLISECONDS)
        }
        val pending = mutableListOf<String>()
        pending.addAll(threads.filter { it.isAlive }.map { it.name })
        pending.addAll(pools.filter { !it.second.isTerminated }.map { it.first })
        if (channels.isNotEmpty()) pending.add("raw channels=${channels.size}")
        if (clients.isNotEmpty() || server.connections.isNotEmpty()) pending.add("websocket clients")
        val stopError =
            synchronized(channelGate) {
                val errors = mutableListOf<String>()
                val closeError = closingFailure
                if (closeError != null) errors.add(closeError)
                if (pending.isNotEmpty()) errors.add("Stop incomplete: ${pending.joinToString()}")
                // Later explicit checks consider new close errors and remaining resources.
                closingFailure = null
                if (errors.isEmpty()) {
                    failure = null
                    state = GatewayState.STOPPED
                } else {
                    failure = "${errors.joinToString("; ")}; use Refresh status after work ends"
                    state = GatewayState.FAILED
                }
                failure
            }
        changed()
        if (stopError != null) throw Rejected("gatewayStopping", stopError)
    }

    companion object {
        const val STOP_MS = 5000L
        private const val TICK_MS = 250L

        fun pool(
            name: String,
            threads: Int,
        ): ThreadPoolExecutor =
            ThreadPoolExecutor(
                threads,
                threads,
                0,
                TimeUnit.SECONDS,
                ArrayBlockingQueue(Limits.QUEUE),
                { Thread(it, "androidctl-$name") },
            )
    }
}

private val REQUEST_ID = Regex("[a-zA-Z0-9_-]{1,100}")
