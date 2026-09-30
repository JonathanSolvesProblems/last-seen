package com.jonathanandrei.phantombridge

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.TextureView
import android.view.View
import android.view.WindowManager
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.jonathanandrei.phantombridge.bridge.BridgeServer
import com.jonathanandrei.phantombridge.bridge.Protocol
import com.jonathanandrei.phantombridge.dji.DroneController
import com.jonathanandrei.phantombridge.dji.SdkManager
import com.jonathanandrei.phantombridge.dji.VideoStreamer
import com.jonathanandrei.phantombridge.ui.EStopView
import com.jonathanandrei.phantombridge.ui.GateView
import com.jonathanandrei.phantombridge.ui.LinkChainView
import com.jonathanandrei.phantombridge.ui.SceneTint
import dji.sdk.base.BaseProduct
import dji.sdk.products.Aircraft
import org.java_websocket.WebSocket
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : AppCompatActivity(), BridgeServer.Handler, SdkManager.Listener {

    private lateinit var chain: LinkChainView
    private lateinit var gate: GateView
    private lateinit var logText: TextView
    private lateinit var logScroll: ScrollView
    private lateinit var estop: EStopView
    private lateinit var numRegister: TextView
    private lateinit var numConnect: TextView
    private lateinit var numBridge: TextView
    private lateinit var lblBridge: TextView
    private lateinit var lblConnect: TextView
    private lateinit var markBind: TextView
    private lateinit var markSwap: TextView
    private lateinit var btnTheme: TextView
    private lateinit var tint: SceneTint
    private val readouts = HashMap<Int, Pair<TextView, TextView>>()

    private val sdk: SdkManager get() = Core.sdk
    private val drone: DroneController get() = Core.drone
    private lateinit var video: VideoStreamer
    private var server: BridgeServer?
        get() = Core.server
        set(value) { Core.server = value }

    private val ui = Handler(Looper.getMainLooper())
    private val logLines get() = Core.logLines
    private val timeFmt = SimpleDateFormat("HH:mm:ss", Locale.US)
    private var bindWifi: Boolean
        get() = Core.bindWifi
        set(value) { Core.bindWifi = value }
    private var lastTelemetryMs = 0L
    private var lastFrames = 0L
    private var lastFpsMs = 0L
    private var fps = 0.0
    private var hasPicture = false
    private var lastVideoKb = -1L

    private val ticker = object : Runnable {
        override fun run() {
            val t = drone.telemetry()
            val now = SystemClock.elapsedRealtime()
            if (t != null) {
                lastTelemetryMs = now
                server?.broadcastText(t.toJson())
                showTelemetry(t)
            } else {
                showTelemetry(null)
            }
            if (now - lastFpsMs >= 1000) {
                fps = (video.framesSent - lastFrames) * 1000.0 / (now - lastFpsMs).coerceAtLeast(1)
                lastFrames = video.framesSent
                lastFpsMs = now
                setReadout(R.id.roFps, "fps", if (server != null) String.format(Locale.US, "%.1f", fps) else "--")
                // once every 5 s: how much H.264 has the aircraft actually sent us
                if (drone.aircraft != null && (now / 1000) % 5 == 0L) {
                    val kb = video.bytesReceived / 1024
                    if (kb != lastVideoKb) { log("video bytes: ${kb} KB"); lastVideoKb = kb }
                    else if (kb == 0L) log("video bytes: 0 KB (no H.264 from the aircraft yet)")
                }
            }
            updateChain()
            ui.postDelayed(this, 100)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        // Dark is the home polarity; SUN is a deliberate switch for direct sunlight.
        val prefs = getSharedPreferences("bridge", MODE_PRIVATE)
        AppCompatDelegate.setDefaultNightMode(
            if (prefs.getBoolean("sun", false)) AppCompatDelegate.MODE_NIGHT_NO else AppCompatDelegate.MODE_NIGHT_YES)
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        applyTypeVariations(findViewById(android.R.id.content))
        goImmersive()

        chain = findViewById(R.id.chain)
        gate = findViewById(R.id.gate)
        logText = findViewById(R.id.logText)
        logScroll = findViewById(R.id.logScroll)
        estop = findViewById(R.id.estop)
        numRegister = findViewById(R.id.numRegister)
        numConnect = findViewById(R.id.numConnect)
        numBridge = findViewById(R.id.numBridge)
        lblBridge = findViewById(R.id.lblBridge)
        lblConnect = findViewById(R.id.lblConnect)
        markBind = findViewById(R.id.markBind)
        markSwap = findViewById(R.id.markSwap)
        btnTheme = findViewById(R.id.btnTheme)
        val videoView: TextureView = findViewById(R.id.videoView)

        for ((id, label) in listOf(R.id.roAlt to "alt m", R.id.roHdg to "hdg", R.id.roSats to "sats",
                                   R.id.roBat to "batt", R.id.roMode to "mode", R.id.roSw to "sw", R.id.roFps to "fps")) {
            val cell: View = findViewById(id)
            val l: TextView = cell.findViewById(R.id.label)
            val v: TextView = cell.findViewById(R.id.value)
            l.text = label
            readouts[id] = Pair(l, v)
        }

        val dark = (resources.configuration.uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK) ==
            android.content.res.Configuration.UI_MODE_NIGHT_YES
        tint = SceneTint(listOf(findViewById(R.id.marginLeft), findViewById(R.id.marginRight)),
                         videoView, getColor(R.color.frame), dark)
        btnTheme.text = if (dark) "SUN" else "NIGHT"
        btnTheme.setOnClickListener {
            val sun = dark   // switching away from dark means sun on
            prefs.edit().putBoolean("sun", sun).apply()
            AppCompatDelegate.setDefaultNightMode(if (sun) AppCompatDelegate.MODE_NIGHT_NO else AppCompatDelegate.MODE_NIGHT_YES)
        }

        Core.init(this, this)
        Core.logSink = logSink
        Core.server?.handler = this
        video = VideoStreamer(this, videoView) { log(it) }
        video.gimbalPitchProvider = { drone.gimbalPitch }
        video.onFrame = { buf -> server?.broadcastBinary(buf) }
        video.onSceneColor = { rgb, picture ->
            ui.post {
                hasPicture = picture
                gate.hasPicture = picture
                if (picture) tint.onSceneColor(rgb) else tint.onNoPicture()
            }
        }

        findViewById<View>(R.id.rowRegister).setOnClickListener { sdk.register() }
        findViewById<View>(R.id.rowConnect).setOnClickListener { if (sdk.isConnected) sdk.disconnect() else sdk.connect(bindWifi) }
        findViewById<View>(R.id.rowBridge).setOnClickListener { toggleServer() }
        findViewById<View>(R.id.rowBind).setOnClickListener {
            bindWifi = !bindWifi
            setMark(markBind, bindWifi, "on", "off")
        }
        findViewById<View>(R.id.rowSwap).setOnClickListener {
            drone.swapAxes = !drone.swapAxes
            setMark(markSwap, drone.swapAxes, "on", "off")
            log("swap axes = ${drone.swapAxes}")
        }
        estop.onCutMotors = {
            Core.stopEngaged = true
            tint.setCut(true)
            log("LONG PRESS: cutting motors")
            Thread { drone.stopMotors() }.start()
            server?.broadcastText(Protocol.event("motors_cut", "pilot long-pressed STOP"))
        }
        estop.onStop = {
            drone.stop()
            Core.stopEngaged = true
            tint.setCut(true)
            log("STOP: hover, virtual stick off")
            server?.broadcastText(Protocol.event("stop_pressed", "pilot pressed STOP"))
        }

        setMark(markBind, bindWifi, "on", "off")
        setMark(markSwap, drone.swapAxes, "on", "off")
        requestPermissions()
        updateChain()
        ui.post(ticker)

        // Recreated mid-session (theme switch): rebind what lives in Core.
        if (drone.aircraft != null) { video.attach(); video.start() }
        if (server != null) {
            video.streaming = true
            lblBridge.text = "Stop bridge"
        }
        if (Core.stopEngaged) {
            estop.engaged = true
            tint.setCut(true)
        }
        logText.text = logLines.joinToString("\n")
        logScroll.post { logScroll.fullScroll(View.FOCUS_DOWN) }

        // registration is cached after the first success, so bring the link up on launch
        ui.postDelayed({ if (!sdk.registered) sdk.register() }, 1500)

        if (intent?.getBooleanExtra(EXTRA_AUTOSTART, false) == true) autoStart()
    }

    /**
     * Bring the whole chain up without anyone touching the screen.
     *
     * Started with `am start -n ... --ez autostart true`. The alternative was
     * driving the UI with `adb shell input tap` at fixed coordinates, which
     * breaks on any layout change and silently taps the wrong thing, in a park,
     * with a drone powered on. This asks the app to do it instead.
     */
    private fun autoStart() {
        log("autostart: bringing the link up")
        var tries = 0
        val step = object : Runnable {
            override fun run() {
                tries++
                when {
                    !sdk.registered -> {
                        if (tries % 10 == 0) log("autostart: waiting for SDK registration")
                        ui.postDelayed(this, 1000)
                    }
                    !sdk.isConnected -> {
                        if (tries % 8 == 1) {
                            log("autostart: connecting to the aircraft")
                            sdk.connect(bindWifi)
                        }
                        ui.postDelayed(this, 1000)
                    }
                    server == null -> {
                        log("autostart: starting the bridge")
                        toggleServer()
                        ui.postDelayed(this, 1000)
                    }
                    else -> log("autostart: chain is up")
                }
                if (tries > 90) log("autostart: gave up after 90s")
            }
        }
        ui.postDelayed(step, 2000)
    }

    companion object {
        /** `am start -n <pkg>/.MainActivity --ez autostart true` */
        const val EXTRA_AUTOSTART = "autostart"
    }

    override fun onDestroy() {
        ui.removeCallbacks(ticker)
        // The server and the SDK session live in Core and survive this activity.
        video.detach()
        if (Core.logSink === logSink) Core.logSink = null
        super.onDestroy()
    }

    // ---- permissions ---------------------------------------------------------------------------

    private fun requestPermissions() {
        val wanted = mutableListOf(
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION,
            Manifest.permission.READ_PHONE_STATE,
        )
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.S_V2) {
            wanted += Manifest.permission.WRITE_EXTERNAL_STORAGE
            wanted += Manifest.permission.READ_EXTERNAL_STORAGE
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            wanted += Manifest.permission.NEARBY_WIFI_DEVICES
            wanted += Manifest.permission.POST_NOTIFICATIONS
        }
        val missing = wanted.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) ActivityCompat.requestPermissions(this, missing.toTypedArray(), 1)
    }

    // ---- bridge server -------------------------------------------------------------------------

    private fun toggleServer() {
        val s = server
        if (s == null) {
            val srv = BridgeServer(Protocol.PORT, this)
            srv.start()
            server = srv
            video.streaming = true
            lblBridge.text = "Stop bridge"
            BridgeService.start(this)      // keeps the process alive if the app is backgrounded
        } else {
            video.streaming = false
            s.stop(1000)
            server = null
            lblBridge.text = "Start bridge"
            BridgeService.stop(this)
            log("bridge stopped")
        }
        updateChain()
    }

    /**
     * Drop the aircraft and pick it up again, from the brain.
     *
     * The P3S camera session dies fairly often: telemetry, gimbal and recording
     * all keep working while the decoder emits pure black, so it looks like a
     * broken detector rather than a dead camera. A Disconnect and Connect cycle
     * clears it in about ten seconds, and until now that meant tapping the
     * phone. In a park, holding a controller, that is the wrong thing to need.
     */
    private fun reconnectProduct(seconds: Int): Pair<Boolean, String?> {
        log("reconnect: dropping the aircraft")
        ui.post { sdk.disconnect() }
        Thread.sleep(2500)
        ui.post { sdk.connect(bindWifi) }
        val end = System.currentTimeMillis() + seconds * 1000L
        while (System.currentTimeMillis() < end) {
            if (sdk.isConnected && drone.awaitCamera(1).first) {
                log("reconnect: aircraft and camera back")
                return Pair(true, "reconnected")
            }
            Thread.sleep(500)
        }
        return Pair(false, "did not come back within ${seconds}s")
    }

    override fun onCommand(name: String, args: JSONObject): Pair<Boolean, String?> {
        log("cmd $name")
        val r = when (name) {
            "takeoff" -> drone.takeoff()
            "land" -> drone.land()
            "confirm_landing" -> drone.confirmLanding()
            "go_home" -> drone.goHome()
            "cancel_go_home" -> drone.cancelGoHome()
            "enable_vs" -> drone.enableVirtualStick(true)
            "disable_vs" -> drone.enableVirtualStick(false)
            "set_limits" -> drone.setLimits(args.optInt("max_alt", 50), args.optInt("max_radius", 150))
            "gimbal" -> drone.setGimbalPitch(args.optDouble("pitch", 0.0))
            "stop_motors" -> drone.stopMotors()
            "multi_mode" -> drone.enableMultipleFlightMode()
            "sd_state" -> drone.sdCardState()
            "format_sd" -> drone.formatSdCard()
            "components" -> drone.components()
            "flight_state" -> drone.flightState()
            "start_motors" -> drone.startMotors()
            "await_camera" -> drone.awaitCamera(args.optInt("seconds", 20))
            "reconnect" -> reconnectProduct(args.optInt("seconds", 25))
            "record_start" -> drone.startRecording()
            "record_stop" -> drone.stopRecording()
            "ping" -> Pair(true, null)
            else -> Pair(false, "unknown command $name")
        }
        if (name == "enable_vs" && r.first) {
            // the brain has control again: colour returns
            Core.stopEngaged = false
            ui.post { estop.engaged = false; tint.setCut(false) }
        }
        return r
    }

    override fun onVelocity(v: Protocol.Velocity) {
        drone.setVelocity(v)
    }

    override fun onClientConnected(conn: WebSocket) {
        conn.send(Protocol.status(sdk.registered, sdk.isConnected,
                                  sdk.product?.model?.displayName ?: "", drone.virtualStickEnabled))
        ui.post { updateChain() }
    }

    // ---- sdk listener -------------------------------------------------------------------------

    override fun onRegistered(ok: Boolean, error: String?) {
        ui.post { updateChain() }
    }

    override fun onProduct(product: BaseProduct?) {
        ui.post {
            val ac = product as? Aircraft
            drone.aircraft = ac
            if (ac != null) { video.attach(); video.start() } else video.stop()
            updateChain()
            server?.broadcastText(Protocol.status(sdk.registered, sdk.isConnected,
                                                   product?.model?.displayName ?: "", drone.virtualStickEnabled))
        }
    }

    // ---- ui ---------------------------------------------------------------------------------------

    private fun updateChain() {
        val telemetryLive = SystemClock.elapsedRealtime() - lastTelemetryMs < 2000 && lastTelemetryMs > 0
        chain.update(LinkChainView.State(
            brainClients = server?.clientCount ?: 0,
            sdkRegistered = sdk.registered,
            productConnected = sdk.isConnected,
            telemetryLive = telemetryLive,
            productName = sdk.product?.model?.displayName ?: "",
            port = Protocol.PORT,
            virtualStick = drone.virtualStickEnabled,
        ))
        setStep(numRegister, sdk.registered)
        setStep(numConnect, sdk.isConnected)
        lblConnect.text = if (sdk.isConnected) "Disconnect" else "Connect drone"
        setStep(numBridge, server != null)
    }

    /** The step's number square fills with ink when the step is done. */
    private fun setStep(v: TextView, done: Boolean) {
        v.setBackgroundResource(if (done) R.drawable.mark_on else R.drawable.mark_off)
        v.setTextColor(getColor(if (done) R.color.frame else R.color.ink))
    }

    private fun setMark(v: TextView, on: Boolean, onText: String, offText: String) {
        v.text = if (on) onText else offText
        v.setBackgroundResource(if (on) R.drawable.mark_on else R.drawable.mark_off)
        v.setTextColor(getColor(if (on) R.color.frame else R.color.ink_muted))
    }

    /**
     * Variable-font axes from XML styles are not applied reliably when a
     * fontFamily is set at the same time, so each text style carries its
     * variation string in android:tag and we apply it here after inflation.
     */
    private fun applyTypeVariations(root: View) {
        if (root is TextView) {
            val tag = root.tag
            if (tag is String && tag.contains("wght")) root.fontVariationSettings = tag
        }
        if (root is android.view.ViewGroup) {
            for (i in 0 until root.childCount) applyTypeVariations(root.getChildAt(i))
        }
    }

    /** No system bars: the frame is the whole screen (swipe from an edge to peek). */
    private fun goImmersive() {
        val c = androidx.core.view.WindowCompat.getInsetsController(window, window.decorView)
        c.systemBarsBehavior = androidx.core.view.WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        c.hide(androidx.core.view.WindowInsetsCompat.Type.systemBars())
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) goImmersive()
    }

    private fun setReadout(id: Int, label: String, value: String) {
        readouts[id]?.let { (l, v) -> l.text = label; v.text = value }
    }

    private fun showTelemetry(t: Protocol.Telemetry?) {
        if (t == null) {
            for (id in readouts.keys) if (id != R.id.roFps) readouts[id]?.second?.text = "--"
            return
        }
        setReadout(R.id.roAlt, "alt m", String.format(Locale.US, "%.1f", t.alt))
        setReadout(R.id.roHdg, "hdg", String.format(Locale.US, "%03.0f°", t.heading))
        setReadout(R.id.roSats, "sats", t.gpsSats.toString())
        setReadout(R.id.roBat, "batt", if (t.battery >= 0) "${t.battery}%" else "--")
        setReadout(R.id.roMode, if (t.virtualStick) "mode · vs" else "mode",
                   t.flightMode.ifBlank { if (t.isFlying) "FLY" else "GND" }.uppercase().take(9))
        setReadout(R.id.roSw, "sw", t.rcModeSwitch.ifBlank { "--" })
    }

    override fun onLog(msg: String) = log(msg)

    /** Everything logs through Core (logcat + whichever activity is showing). */
    private fun log(msg: String) = Core.log(msg)

    private val logSink: (String) -> Unit = { msg ->
        ui.post {
            logLines.addLast("${timeFmt.format(Date())}  $msg")
            while (logLines.size > 60) logLines.removeFirst()
            logText.text = logLines.joinToString("\n")
            logScroll.post { logScroll.fullScroll(View.FOCUS_DOWN) }
            updateChain()
        }
    }
}
