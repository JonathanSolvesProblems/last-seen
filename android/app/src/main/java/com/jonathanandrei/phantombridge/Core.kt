package com.jonathanandrei.phantombridge

import android.content.Context
import com.jonathanandrei.phantombridge.bridge.BridgeServer
import com.jonathanandrei.phantombridge.dji.DroneController
import com.jonathanandrei.phantombridge.dji.PhoneLocation
import com.jonathanandrei.phantombridge.dji.SdkManager

/**
 * Everything that must survive the activity: the SDK session, the flight
 * controller wrapper and the WebSocket server. The activity is recreated on
 * a theme switch (SUN / NIGHT), and a pilot flips that mid-flight, so none
 * of this may live in the activity.
 */
object Core {
    lateinit var sdk: SdkManager
    val drone = DroneController { log(it) }
    var server: BridgeServer? = null
    var bindWifi = true
    @Volatile var stopEngaged = false
    val logLines = ArrayDeque<String>()

    /** The current activity's log sink; swapped on recreate. */
    @Volatile var logSink: ((String) -> Unit)? = null

    /** The phone's own GNSS: the subject's position during a follow test. */
    val phoneGps = PhoneLocation { log(it) }

    fun init(context: Context, listener: SdkManager.Listener) {
        if (!::sdk.isInitialized) {
            sdk = SdkManager(context.applicationContext, listener)
        } else {
            sdk.listener = listener
        }
        phoneGps.start(context.applicationContext)
    }

    fun log(msg: String) {
        android.util.Log.i("PhantomBridge", msg)
        logSink?.invoke(msg)
    }
}
