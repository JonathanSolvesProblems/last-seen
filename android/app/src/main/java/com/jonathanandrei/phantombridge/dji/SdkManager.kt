package com.jonathanandrei.phantombridge.dji

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.util.Log
import dji.common.error.DJIError
import dji.common.error.DJISDKError
import dji.sdk.base.BaseComponent
import dji.sdk.base.BaseProduct
import dji.sdk.products.Aircraft
import dji.sdk.sdkmanager.DJISDKInitEvent
import dji.sdk.sdkmanager.DJISDKManager

/**
 * Registers the app with DJI (needs internet once, then cached) and connects
 * to the aircraft over the remote controller's Wi-Fi hotspot.
 *
 * Phantom 3 Standard specifics:
 *  - The phone joins the RC's "PHANTOM3_xxxxxx" Wi-Fi. That network has no
 *    internet, so Android may route sockets over mobile data instead. After
 *    registration we bind the process to the Wi-Fi network so the SDK's
 *    traffic reaches 192.168.x.x. DJI GO does the same thing.
 *  - No USB accessory involved; startConnectionToProduct() discovers it.
 */
class SdkManager(private val context: Context, @Volatile var listener: Listener) {

    interface Listener {
        fun onRegistered(ok: Boolean, error: String?)
        fun onProduct(product: BaseProduct?)
        fun onLog(msg: String)
    }

    @Volatile var registered = false
        private set
    @Volatile var product: BaseProduct? = null
        private set
    val aircraft: Aircraft? get() = product as? Aircraft
    val isConnected: Boolean get() = product?.isConnected == true

    private val callback = object : DJISDKManager.SDKManagerCallback {
        override fun onRegister(error: DJIError?) {
            val ok = error == DJISDKError.REGISTRATION_SUCCESS
            registered = ok
            listener.onLog(if (ok) "SDK registered (v${DJISDKManager.getInstance().sdkVersion})"
                           else "SDK registration failed: ${error?.description}")
            listener.onRegistered(ok, error?.description)
        }

        override fun onProductDisconnect() {
            listener.onLog("product disconnected")
            product = null
            listener.onProduct(null)
        }

        override fun onProductConnect(p: BaseProduct?) {
            product = p
            listener.onLog("product connected: ${p?.model?.displayName ?: "unknown"}")
            listener.onProduct(p)
        }

        override fun onProductChanged(p: BaseProduct?) {
            product = p
            listener.onLog("product changed: ${p?.model?.displayName ?: "none"}")
            listener.onProduct(p)
        }

        override fun onComponentChange(key: BaseProduct.ComponentKey?, old: BaseComponent?, new: BaseComponent?) {
            new?.setComponentListener { connected ->
                listener.onLog("component $key connected=$connected")
            }
        }

        override fun onInitProcess(event: DJISDKInitEvent?, total: Int) {
            listener.onLog("SDK init: ${event?.initializationState} ($total)")
        }

        override fun onDatabaseDownloadProgress(current: Long, total: Long) {
            // fly-safe database excluded from the build; nothing to report
        }
    }

    /** Registration must run off the main thread (DJI sample does the same). */
    fun register() {
        listener.onLog("registering with DJI...")
        Thread {
            try {
                DJISDKManager.getInstance().registerApp(context.applicationContext, callback)
            } catch (e: Throwable) {
                Log.e(TAG, "registerApp threw", e)
                listener.onLog("registerApp threw: ${e.message}")
            }
        }.start()
    }

    fun connect(bindToWifi: Boolean) {
        if (!registered) {
            listener.onLog("register first")
            return
        }
        holdWifi()
        if (bindToWifi) bindProcessToWifi()
        listener.onLog("starting connection to product...")
        DJISDKManager.getInstance().startConnectionToProduct()
    }

    /**
     * Release the aircraft cleanly. The Phantom 3 Standard's camera keeps a video
     * session open for ~3 minutes after a client vanishes, so an app killed while
     * connected gets telemetry but no picture on the next connect. Always
     * disconnect through here before closing the app.
     */
    fun disconnect() {
        listener.onLog("disconnecting from product...")
        try {
            DJISDKManager.getInstance().stopConnectionToProduct()
        } catch (e: Throwable) {
            listener.onLog("stopConnectionToProduct threw: ${e.message}")
        }
        product = null
        listener.onProduct(null)
    }

    /**
     * Route this process's sockets over the RC's Wi-Fi even though it has no internet.
     *
     * There can be more than one Wi-Fi-transport network at once. With the phone's
     * own hotspot running there are exactly two: the soft AP (swlan0, 10.x, marked
     * LOCAL_NETWORK) and the station link to the RC (wlan0, 192.168.1.20, gateway
     * 192.168.1.1). Taking the first one binds to the hotspot and every DJI UDP
     * packet is dropped, which looks identical to the aircraft being off. So score
     * the candidates and pick the one that can actually reach 192.168.1.1.
     */
    private fun bindProcessToWifi() {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        var best: Network? = null
        var bestScore = Int.MIN_VALUE
        var bestWhy = ""
        for (n in cm.allNetworks) {
            val caps = cm.getNetworkCapabilities(n) ?: continue
            if (!caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) continue
            val lp = cm.getLinkProperties(n)
            val iface = lp?.interfaceName ?: "?"
            val addrs = lp?.linkAddresses?.mapNotNull { it.address.hostAddress }.orEmpty()
            var score = 0
            // DJI hands out 192.168.1.x (P3S / Mavic) or 192.168.2.x (some Spark/Tello).
            if (addrs.any { it.startsWith("192.168.1.") || it.startsWith("192.168.2.") }) score += 100
            // The soft AP is always a separate interface; never bind to it.
            if (iface.startsWith("swlan") || iface.startsWith("ap")) score -= 200
            if (iface == "wlan0") score += 20
            // An internet-validated Wi-Fi is a home router, not the aircraft.
            if (caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)) score -= 30
            listener.onLog("wifi candidate $iface ${addrs.firstOrNull() ?: "-"} score=$score")
            if (score > bestScore) {
                bestScore = score
                best = n
                bestWhy = "$iface ${addrs.firstOrNull() ?: "-"}"
            }
        }
        if (best == null) {
            listener.onLog("no Wi-Fi network to bind to (join the PHANTOM3_ hotspot first)")
            return
        }
        if (bestScore < 0) {
            listener.onLog("no Wi-Fi looks like the aircraft; not binding")
            return
        }
        val ok = cm.bindProcessToNetwork(best)
        listener.onLog(if (ok) "bound to $bestWhy" else "bindProcessToNetwork failed ($bestWhy)")
    }

    fun unbind() {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        cm.bindProcessToNetwork(null)
    }

    /**
     * Ask Android to keep the aircraft's Wi-Fi even though it has no internet.
     *
     * The RC's network never reaches the internet, so Android treats it as a
     * bad network and drops it, especially with the phone's own hotspot running
     * and the screen off. On flight 02 the phone fell off it three times, once
     * IN THE AIR: telemetry died mid-run, the battery reading corrupted to 0%,
     * and neither land nor release-controls could be delivered.
     *
     * A NetworkRequest for a Wi-Fi transport WITHOUT NET_CAPABILITY_INTERNET
     * tells the system this process wants that network kept up regardless.
     * Held for the life of the app; releasing it is what lets Android reap the
     * connection again.
     */
    fun holdWifi() {
        if (netCallback != null) return
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val request = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
            .build()
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                listener.onLog("wifi hold: network available")
            }

            override fun onLost(network: Network) {
                listener.onLog("wifi hold: LOST the Wi-Fi network")
            }
        }
        try {
            cm.requestNetwork(request, cb)
            netCallback = cb
            listener.onLog("holding the aircraft Wi-Fi against Android's no-internet reaping")
        } catch (e: Throwable) {
            listener.onLog("could not hold Wi-Fi: ${e.message}")
        }
    }

    fun releaseWifiHold() {
        val cb = netCallback ?: return
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        try {
            cm.unregisterNetworkCallback(cb)
        } catch (_: Throwable) {
        }
        netCallback = null
    }

    private var netCallback: ConnectivityManager.NetworkCallback? = null

    companion object {
        private const val TAG = "SdkManager"
    }
}
