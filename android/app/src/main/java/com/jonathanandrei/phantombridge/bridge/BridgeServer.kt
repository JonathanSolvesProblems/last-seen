package com.jonathanandrei.phantombridge.bridge

import android.util.Log
import org.java_websocket.WebSocket
import org.java_websocket.handshake.ClientHandshake
import org.java_websocket.server.WebSocketServer
import org.json.JSONObject
import java.net.InetSocketAddress
import java.nio.ByteBuffer
import java.util.concurrent.CopyOnWriteArraySet

/**
 * WebSocket server on 0.0.0.0:8765. The laptop reaches it over USB with
 *     adb forward tcp:8765 tcp:8765
 * so the phone's Wi-Fi stays dedicated to the drone's remote controller.
 */
class BridgeServer(
    port: Int,
    @Volatile var handler: Handler,
) : WebSocketServer(InetSocketAddress("0.0.0.0", port)) {

    interface Handler {
        /** Returns (ok, error) for a command. */
        fun onCommand(name: String, args: JSONObject): Pair<Boolean, String?>
        fun onVelocity(v: Protocol.Velocity)
        fun onClientConnected(conn: WebSocket)
        fun onLog(msg: String)
    }

    private val clients = CopyOnWriteArraySet<WebSocket>()
    val clientCount: Int get() = clients.size

    init {
        isReuseAddr = true
        connectionLostTimeout = 10
    }

    override fun onOpen(conn: WebSocket, handshake: ClientHandshake) {
        clients.add(conn)
        handler.onLog("client connected ${conn.remoteSocketAddress}")
        handler.onClientConnected(conn)
    }

    override fun onClose(conn: WebSocket, code: Int, reason: String?, remote: Boolean) {
        clients.remove(conn)
        handler.onLog("client closed ($code $reason)")
        // Losing the brain mid-flight: hover. The watchdog also handles it.
        handler.onVelocity(Protocol.Velocity.ZERO)
    }

    override fun onMessage(conn: WebSocket, message: String) {
        val msg = try {
            JSONObject(message)
        } catch (e: Exception) {
            return
        }
        when (msg.optString("type")) {
            "vel" -> handler.onVelocity(
                Protocol.Velocity(
                    msg.optDouble("vn", 0.0), msg.optDouble("ve", 0.0),
                    msg.optDouble("vu", 0.0), msg.optDouble("yaw_rate", 0.0),
                )
            )
            "cmd" -> {
                val id = msg.optString("id")
                val name = msg.optString("name")
                Thread {
                    val (ok, err) = try {
                        handler.onCommand(name, msg)
                    } catch (e: Exception) {
                        Pair(false, e.message ?: e.toString())
                    }
                    try {
                        conn.send(Protocol.ack(id, ok, err))
                    } catch (e: Exception) {
                        Log.w(TAG, "ack send failed", e)
                    }
                }.start()
            }
        }
    }

    override fun onMessage(conn: WebSocket, message: ByteBuffer) {
        // brain never sends binary
    }

    override fun onError(conn: WebSocket?, ex: Exception) {
        handler.onLog("ws error: ${ex.message}")
        Log.w(TAG, "ws error", ex)
    }

    override fun onStart() {
        handler.onLog("bridge listening on :$port")
    }

    fun broadcastText(text: String) {
        for (c in clients) {
            try {
                if (c.isOpen) c.send(text)
            } catch (e: Exception) {
                Log.w(TAG, "send failed", e)
            }
        }
    }

    fun broadcastBinary(buf: ByteBuffer) {
        for (c in clients) {
            try {
                if (c.isOpen) c.send(buf.duplicate())
            } catch (e: Exception) {
                Log.w(TAG, "send failed", e)
            }
        }
    }

    companion object {
        private const val TAG = "BridgeServer"
    }
}
