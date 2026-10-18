package com.jonathanandrei.phantombridge.dji

import android.os.SystemClock
import android.util.Log
import com.jonathanandrei.phantombridge.bridge.Protocol
import dji.common.error.DJIError
import dji.common.flightcontroller.FlightControllerState
import dji.common.flightcontroller.virtualstick.FlightControlData
import dji.common.flightcontroller.virtualstick.FlightCoordinateSystem
import dji.common.flightcontroller.virtualstick.RollPitchControlMode
import dji.common.flightcontroller.virtualstick.VerticalControlMode
import dji.common.flightcontroller.virtualstick.YawControlMode
import dji.common.gimbal.Rotation
import dji.common.gimbal.RotationMode
import dji.common.camera.SettingsDefinitions
import dji.common.remotecontroller.HardwareState.FlightModeSwitch
import dji.common.util.CommonCallbacks
import dji.sdk.products.Aircraft
import dji.sdk.sdkmanager.DJISDKManager
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import kotlin.math.abs

/**
 * Wraps the DJI FlightController: telemetry, takeoff/land/RTH, limits, gimbal,
 * and the virtual-stick velocity loop.
 *
 * Virtual stick runs at 20 Hz on its own thread. The brain streams "vel"
 * messages at 10 Hz; if none arrives for WATCHDOG_MS the loop sends zeros
 * (hover). Flipping the RC mode switch out of F mode also kicks the SDK out
 * of virtual stick, which is the pilot's hardware override.
 *
 * AXIS MAPPING (verify on the first flight with scripts/keyboard_fly.py):
 *   coordinate system GROUND, roll/pitch mode VELOCITY:
 *     roll  = velocity along X = NORTH  (m/s)
 *     pitch = velocity along Y = EAST   (m/s)
 *   yaw mode ANGULAR_VELOCITY: deg/s, clockwise positive
 *   vertical mode VELOCITY: m/s, up positive
 * If W (north) moves the drone east instead, tick "Swap roll/pitch axes".
 */
class DroneController(private val log: (String) -> Unit) {

    @Volatile var aircraft: Aircraft? = null
        set(value) {
            field = value
            if (value != null) attach(value) else detach()
        }

    /**
     * The camera, found by whichever route currently works.
     *
     * `aircraft.camera` comes back null on the P3S for a while after connect,
     * and sometimes stays null even once video is flowing, because the video
     * feed arrives through VideoFeeder and does not need the camera component.
     * Every camera command used to fail with "no camera" in that window, which
     * looked like a broken camera and was a component that had not registered.
     */
    private val camera: dji.sdk.camera.Camera?
        get() {
            aircraft?.camera?.let { return it }
            aircraft?.cameras?.firstOrNull { it != null }?.let { return it }
            val product = DJISDKManager.getInstance().product as? Aircraft
            product?.camera?.let { return it }
            return product?.cameras?.firstOrNull { it != null }
        }

    private val gimbalOrNull: dji.sdk.gimbal.Gimbal?
        get() = aircraft?.gimbal
            ?: aircraft?.gimbals?.firstOrNull { it != null }
            ?: (DJISDKManager.getInstance().product as? Aircraft)?.gimbal

    /** Arm the motors without an auto-takeoff. Fails with a REASON when takeoff will not. */
    fun startMotors(): Pair<Boolean, String?> {
        val fc = aircraft?.flightController ?: return Pair(false, "no flight controller")
        val r = await { cb -> fc.turnOnMotors(cb) }
        log(if (r.first) "motors started" else "motor start refused: ${r.second}")
        return r
    }

    /** Everything the flight controller knows about why it might not fly. */
    fun flightState(): Pair<Boolean, String?> {
        val fc = aircraft?.flightController ?: return Pair(false, "no flight controller")
        val s = fc.state ?: return Pair(false, "no flight controller state")
        val text = buildString {
            append("mode=${runCatching { s.flightModeString }.getOrNull()} ")
            append("flying=${runCatching { s.isFlying }.getOrNull()} ")
            append("motorsOn=${runCatching { s.areMotorsOn() }.getOrNull()} ")
            append("imuPreheating=${runCatching { s.isIMUPreheating }.getOrNull()} ")
            append("ultrasonicWorking=${runCatching { s.isUltrasonicBeingUsed }.getOrNull()} ")
            append("homeSet=${runCatching { s.isHomeLocationSet }.getOrNull()} ")
            append("sats=${runCatching { s.satelliteCount }.getOrNull()} ")
            append("gpsLevel=${runCatching { s.gpsSignalLevel }.getOrNull()} ")
            append("failsafe=${runCatching { s.isFailsafeEnabled }.getOrNull()} ")
            append("lowBatteryRTH=${runCatching { s.isLowerThanBatteryWarningThreshold }.getOrNull()} ")
            append("seriousLowBattery=${runCatching { s.isLowerThanSeriousBatteryWarningThreshold }.getOrNull()} ")
            append("flightCount=${runCatching { s.flightCount }.getOrNull()} ")
            // The compass is the usual reason an aircraft silently refuses to
            // arm, and the field is under high-voltage transmission lines.
            val c = runCatching { fc.compass }.getOrNull()
            append("compass=${if (c == null) "unavailable" else
                "error=${runCatching { c.hasError() }.getOrNull()} " +
                "calibrating=${runCatching { c.calibrationState }.getOrNull()}"}")
        }
        log("flight state: $text")
        return Pair(true, text)
    }

    /** What the SDK currently thinks is attached. For diagnosing "no camera". */
    fun components(): Pair<Boolean, String?> {
        val a = aircraft ?: return Pair(false, "no aircraft")
        val text = "model=${a.model?.displayName} connected=${a.isConnected} " +
                   "flightController=${a.flightController != null} " +
                   "camera=${a.camera != null} cameras=${a.cameras?.size ?: 0} " +
                   "gimbal=${a.gimbal != null} gimbals=${a.gimbals?.size ?: 0} " +
                   "battery=${a.battery != null} remoteController=${a.remoteController != null} " +
                   "resolvedCamera=${camera != null}"
        log(text)
        return Pair(true, text)
    }

    /** Wait for the camera component to register, up to `seconds`. */
    fun awaitCamera(seconds: Int = 20): Pair<Boolean, String?> {
        val end = System.currentTimeMillis() + seconds * 1000L
        while (System.currentTimeMillis() < end) {
            if (camera != null) return Pair(true, "camera attached")
            Thread.sleep(500)
        }
        return Pair(false, "camera did not attach within ${seconds}s")
    }

    @Volatile var swapAxes = false
    @Volatile var virtualStickEnabled = false
        private set

    // latest telemetry pieces
    @Volatile private var state: FlightControllerState? = null
    @Volatile private var battery: Int = -1
    @Volatile private var rcSwitch: String = ""
    @Volatile private var lastRawSwitch: Int = -1
    @Volatile var gimbalPitch: Double = 0.0
        private set

    // velocity command + watchdog
    @Volatile private var cmd = Protocol.Velocity.ZERO
    @Volatile private var cmdAtMs: Long = 0
    @Volatile private var watchdogTripped = false
    private var exec: ScheduledExecutorService? = null

    private fun attach(ac: Aircraft) {
        val fc = ac.flightController
        if (fc == null) {
            log("no flight controller on product")
            return
        }
        fc.setStateCallback { s -> state = s }
        ac.battery?.setStateCallback { b -> battery = b.chargeRemainingInPercent }
        ac.gimbal?.setStateCallback { g -> gimbalPitch = g.attitudeInDegrees.pitch.toDouble() }
        ac.remoteController?.setHardwareStateCallback { hw ->
            // Logged only: on the Phantom 3 Standard this fires once and then stays
            // silent, so the displayed letter comes from the flight-mode string.
            val pos = when (hw.flightModeSwitch) {
                FlightModeSwitch.POSITION_ONE -> 1
                FlightModeSwitch.POSITION_TWO -> 2
                FlightModeSwitch.POSITION_THREE -> 3
                else -> 0
            }
            if (pos != lastRawSwitch) { lastRawSwitch = pos; log("rc switch hardware position: $pos") }
        }
        fc.setVirtualStickAdvancedModeEnabled(true)
        fc.rollPitchControlMode = RollPitchControlMode.VELOCITY
        fc.yawControlMode = YawControlMode.ANGULAR_VELOCITY
        fc.verticalControlMode = VerticalControlMode.VELOCITY
        fc.rollPitchCoordinateSystem = FlightCoordinateSystem.GROUND
        startLoop()
        log("flight controller attached")
    }

    private fun detach() {
        stopLoop()
        state = null
        virtualStickEnabled = false
    }

    // ---- telemetry -------------------------------------------------------------------

    /**
     * The S1 letter, derived from the aircraft's own flight-mode string. The RC
     * hardware-state callback on the Phantom 3 Standard reports once and then
     * goes quiet, so it cannot be trusted; the mode string always reflects the
     * switch ("P-GPS"/"P-Atti" = P, "Atti" = A, "F-..." / Joystick / Waypoint = F).
     */
    private fun switchLetter(mode: String): String {
        val m = mode.trim()
        return when {
            m.startsWith("P", ignoreCase = true) -> "P"
            m.equals("Atti", ignoreCase = true) || m.startsWith("A-", ignoreCase = true) -> "A"
            m.startsWith("F", ignoreCase = true) -> "F"
            m.startsWith("Joystick", true) || m.startsWith("Waypoint", true) ||
                m.startsWith("HotPoint", true) || m.startsWith("FollowMe", true) -> "F"
            else -> rcSwitch   // TakeOff / Landing / GoHome: keep the last known letter
        }.also { rcSwitch = it }
    }

    fun telemetry(): Protocol.Telemetry? {
        val s = state ?: return null
        val loc = s.aircraftLocation
        val yaw = s.attitude.yaw
        val heading = if (yaw < 0) yaw + 360.0 else yaw
        val home = s.homeLocation
        val modeString = s.flightModeString ?: ""
        return Protocol.Telemetry(
            t = SystemClock.elapsedRealtime() / 1000.0,
            lat = loc?.latitude ?: 0.0,
            lon = loc?.longitude ?: 0.0,
            alt = (loc?.altitude ?: 0f).toDouble(),
            heading = heading,
            // DJI velocities are N-E-D (Z positive down); protocol is up positive
            vn = s.velocityX.toDouble(),
            ve = s.velocityY.toDouble(),
            vu = -s.velocityZ.toDouble(),
            battery = battery,
            gpsSats = s.satelliteCount,
            isFlying = s.isFlying,
            motorsOn = s.areMotorsOn(),
            virtualStick = virtualStickEnabled,
            flightMode = modeString,
            rcModeSwitch = switchLetter(modeString),
            homeLat = home?.latitude,
            homeLon = home?.longitude,
            homeSet = s.isHomeLocationSet,
            gimbalPitch = gimbalPitch,
            roll = s.attitude.roll,
            pitch = s.attitude.pitch,
            phoneLat = com.jonathanandrei.phantombridge.Core.phoneGps.last?.latitude,
            phoneLon = com.jonathanandrei.phantombridge.Core.phoneGps.last?.longitude,
            phoneAccuracyM = com.jonathanandrei.phantombridge.Core.phoneGps.last?.accuracy?.toDouble(),
            phoneFixAgeS = com.jonathanandrei.phantombridge.Core.phoneGps.ageSeconds(),
        )
    }

    // ---- virtual stick loop ------------------------------------------------------------

    fun setVelocity(v: Protocol.Velocity) {
        cmd = v
        cmdAtMs = SystemClock.elapsedRealtime()
    }

    private fun startLoop() {
        stopLoop()
        exec = Executors.newSingleThreadScheduledExecutor().also {
            it.scheduleAtFixedRate({ tick() }, 0, 50, TimeUnit.MILLISECONDS)
        }
    }

    private fun stopLoop() {
        exec?.shutdownNow()
        exec = null
    }

    private fun tick() {
        val fc = aircraft?.flightController ?: return
        if (!virtualStickEnabled) return
        // The pilot flipping S1 out of F (or the FC dropping the mode) ends virtual
        // stick without telling us. Notice it, so telemetry reports the truth and the
        // brain sees the override.
        if (!fc.isVirtualStickControlModeAvailable) {
            virtualStickEnabled = false
            cmd = Protocol.Velocity.ZERO
            log("virtual stick ended by the aircraft (mode switch?)")
            return
        }
        val stale = SystemClock.elapsedRealtime() - cmdAtMs > Protocol.WATCHDOG_MS
        val v = if (stale) Protocol.Velocity.ZERO else cmd
        if (stale && !watchdogTripped && cmdAtMs > 0) {
            watchdogTripped = true
            log("watchdog: no velocity for ${Protocol.WATCHDOG_MS} ms, hovering")
        } else if (!stale) {
            watchdogTripped = false
        }
        val north = v.vn.toFloat()
        val east = v.ve.toFloat()
        // Setters, not the positional constructor, so the mapping is explicit:
        // GROUND + VELOCITY mode: roll = X axis (north), pitch = Y axis (east).
        val data = FlightControlData(0f, 0f, 0f, 0f).apply {
            roll = if (swapAxes) east else north
            pitch = if (swapAxes) north else east
            yaw = v.yawRate.toFloat()
            verticalThrottle = v.vu.toFloat()
        }
        try {
            fc.sendVirtualStickFlightControlData(data) { err ->
                if (err != null) Log.v(TAG, "vs send: ${err.description}")
            }
        } catch (e: Exception) {
            Log.w(TAG, "vs send threw", e)
        }
    }

    // ---- commands (blocking, called from the bridge's command thread) ---------------------

    fun enableVirtualStick(on: Boolean): Pair<Boolean, String?> {
        val fc = aircraft?.flightController ?: return Pair(false, "no flight controller")
        if (!on) {
            cmd = Protocol.Velocity.ZERO
        }
        val r = await { cb -> fc.setVirtualStickModeEnabled(on, cb) }
        if (r.first) {
            virtualStickEnabled = on
            if (on) {
                cmd = Protocol.Velocity.ZERO
                cmdAtMs = SystemClock.elapsedRealtime()
            }
            log("virtual stick ${if (on) "ON" else "OFF"}")
        }
        return r
    }

    fun takeoff(): Pair<Boolean, String?> {
        val fc = aircraft?.flightController ?: return Pair(false, "no flight controller")
        return await { cb -> fc.startTakeoff(cb) }
    }

    fun land(): Pair<Boolean, String?> {
        val fc = aircraft?.flightController ?: return Pair(false, "no flight controller")
        if (virtualStickEnabled) enableVirtualStick(false)
        return await { cb -> fc.startLanding(cb) }
    }

    fun confirmLanding(): Pair<Boolean, String?> {
        val fc = aircraft?.flightController ?: return Pair(false, "no flight controller")
        return await { cb -> fc.confirmLanding(cb) }
    }

    fun goHome(): Pair<Boolean, String?> {
        val fc = aircraft?.flightController ?: return Pair(false, "no flight controller")
        // RTH is a flight-controller mission; virtual stick must be off or it fights it.
        if (virtualStickEnabled) enableVirtualStick(false)
        return await { cb -> fc.startGoHome(cb) }
    }

    fun cancelGoHome(): Pair<Boolean, String?> {
        val fc = aircraft?.flightController ?: return Pair(false, "no flight controller")
        return await { cb -> fc.cancelGoHome(cb) }
    }

    fun setLimits(maxAlt: Int, maxRadius: Int): Pair<Boolean, String?> {
        val fc = aircraft?.flightController ?: return Pair(false, "no flight controller")
        val a = await { cb -> fc.setMaxFlightHeight(maxAlt.coerceIn(20, 120), cb) }
        if (!a.first) return a
        val en = await { cb -> fc.setMaxFlightRadiusLimitationEnabled(true, cb) }
        if (!en.first) log("radius limitation enable: ${en.second}")
        val r = await { cb -> fc.setMaxFlightRadius(maxRadius.coerceIn(15, 8000), cb) }
        if (!r.first) log("max radius: ${r.second}")
        val gh = await { cb -> fc.setGoHomeHeightInMeters(maxAlt.coerceIn(20, 120).coerceAtMost(40), cb) }
        if (!gh.first) log("go home height: ${gh.second}")
        return Pair(true, null)
    }

    /**
     * Point the camera.
     *
     * The duration is scaled to the size of the move rather than fixed at a
     * second. Follow-me re-aims the gimbal continuously to keep the subject
     * centred, in steps of a few degrees, and a fixed one-second rotation means
     * every one of those is still executing when the next arrives: the gimbal
     * never settles and reports several degrees behind where it was sent.
     * About 30 deg/s is smooth on camera and keeps a small step inside one
     * control cycle.
     */
    fun setGimbalPitch(pitch: Double): Pair<Boolean, String?> {
        val g = gimbalOrNull ?: return Pair(false, "no gimbal (component not attached)")
        val want = pitch.coerceIn(-90.0, 0.0)
        val seconds = (Math.abs(want - gimbalPitch) / 30.0).coerceIn(0.2, 1.5)
        val rot = Rotation.Builder()
            .mode(RotationMode.ABSOLUTE_ANGLE)
            .pitch(want.toFloat())
            .time(seconds)
            .build()
        return await { cb -> g.rotate(rot, cb) }
    }

    /**
     * Cut the propellers, immediately.
     *
     * This is for one situation: the pilot has his hands on the aircraft. In
     * the air it drops, so it is never an alternative to landing. Virtual
     * stick is released first so nothing keeps commanding it.
     */
    fun stopMotors(): Pair<Boolean, String?> {
        val fc = aircraft?.flightController ?: return Pair(false, "no flight controller")
        cmd = Protocol.Velocity.ZERO
        if (virtualStickEnabled) enableVirtualStick(false)
        log("CUTTING MOTORS")
        val r = await { cb -> fc.turnOffMotors(cb) }
        log(if (r.first) "motors off" else "motor cut failed: ${r.second}")
        return r
    }

    /**
     * Let the RC's mode switch reach F.
     *
     * On the Phantom 3 the flight mode switch is inert until "Multiple Flight
     * Mode" is on, and it ships off. That setting normally lives in DJI GO,
     * which cannot run while this app holds the aircraft, so set it here and
     * save the app swap in the field. Virtual stick needs F, so nothing flies
     * without this.
     */
    fun enableMultipleFlightMode(): Pair<Boolean, String?> {
        val fc = aircraft?.flightController ?: return Pair(false, "no flight controller")
        val r = await { cb -> fc.setMultipleFlightModeEnabled(true, cb) }
        log(if (r.first) "multiple flight mode enabled (S1 can reach F)"
            else "could not enable multiple flight mode: ${r.second}")
        return r
    }

    /**
     * Roll the onboard camera to the SD card.
     *
     * The bridge's own video feed is 720p and lossy: fine for the brain, not
     * good enough to cut a video from. The 2.7K file on the card is the real
     * footage, so every flight records whether or not anything interesting is
     * planned. The camera has to be in RECORD_VIDEO mode first; it boots in
     * SHOOT_PHOTO.
     */
    fun startRecording(): Pair<Boolean, String?> {
        val cam = camera ?: return Pair(false, "no camera (component not attached)")
        val m = await { cb -> cam.setMode(SettingsDefinitions.CameraMode.RECORD_VIDEO, cb) }
        if (!m.first) log("camera mode set failed: ${m.second}")
        val r = await { cb -> cam.startRecordVideo(cb) }
        log(if (r.first) "recording to SD card" else "record start failed: ${r.second}")
        return r
    }

    fun stopRecording(): Pair<Boolean, String?> {
        val cam = camera ?: return Pair(false, "no camera (component not attached)")
        val r = await { cb -> cam.stopRecordVideo(cb) }
        log(if (r.first) "recording stopped" else "record stop failed: ${r.second}")
        return r
    }

    /**
     * What the camera says about its own card, read from the aircraft.
     *
     * A PC format produces a clean filesystem, and DJI still warns because the
     * card was not formatted by the camera. This is the only way to see what the
     * camera actually thinks without opening DJI GO, which cannot run while the
     * bridge holds the aircraft.
     */
    fun sdCardState(): Pair<Boolean, String?> {
        val cam = camera ?: return Pair(false, "no camera (component not attached)")
        val latch = CountDownLatch(1)
        var text: String? = null
        try {
            cam.setStorageStateCallBack { s ->
                if (text == null) {
                    text = "inserted=${s.isInserted} formatted=${s.isFormatted} " +
                           "formatting=${s.isFormatting} initializing=${s.isInitializing} " +
                           "full=${s.isFull} verified=${s.isVerified} " +
                           "invalidFormat=${s.isInvalidFormat} error=${s.hasError()} " +
                           "remainingMB=${s.remainingSpaceInMB} " +
                           "videos=${s.availableRecordingTimeInSeconds}s photos=${s.availableCaptureCount}"
                    latch.countDown()
                }
            }
        } catch (e: Exception) {
            return Pair(false, e.message ?: e.toString())
        }
        val got = latch.await(8, TimeUnit.SECONDS)
        try {
            cam.setStorageStateCallBack(null)
        } catch (_: Exception) {
        }
        return if (got) Pair(true, text) else Pair(false, "camera never reported a storage state")
    }

    /**
     * Format the card in the camera, which is the only format DJI trusts.
     *
     * ERASES THE CARD. Everything on it is gone, so the caller is responsible
     * for having pulled the footage first. Formatting on a PC leaves DJI GO
     * still warning that "SD Card Formatting is Recommended", because that
     * warning is about which device laid down the filesystem.
     */
    fun formatSdCard(): Pair<Boolean, String?> {
        val cam = camera ?: return Pair(false, "no camera (component not attached)")
        log("FORMATTING THE SD CARD (everything on it will be erased)")
        val r = await { cb -> cam.formatSDCard(cb) }
        log(if (r.first) "SD card formatted by the camera" else "format failed: ${r.second}")
        return r
    }

    /** Emergency: zero the command and drop out of virtual stick. */
    fun stop() {
        cmd = Protocol.Velocity.ZERO
        Thread { enableVirtualStick(false) }.start()
    }

    private fun await(call: (CommonCallbacks.CompletionCallback<DJIError>) -> Unit): Pair<Boolean, String?> {
        val latch = CountDownLatch(1)
        var error: DJIError? = null
        try {
            call(CommonCallbacks.CompletionCallback<DJIError> { err ->
                error = err
                latch.countDown()
            })
        } catch (e: Exception) {
            return Pair(false, e.message ?: e.toString())
        }
        if (!latch.await(12, TimeUnit.SECONDS)) return Pair(false, "timeout")
        return if (error == null) Pair(true, null) else Pair(false, error?.description)
    }

    companion object {
        private const val TAG = "DroneController"
    }
}
