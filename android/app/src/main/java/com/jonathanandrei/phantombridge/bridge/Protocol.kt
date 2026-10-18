package com.jonathanandrei.phantombridge.bridge

import org.json.JSONObject
import java.nio.ByteBuffer

/**
 * Wire protocol shared with brain/phantom_brain/protocol.py. Keep the two in sync.
 *
 * Text (JSON):  status | telemetry | ack | event   (bridge -> brain)
 *               cmd | vel                          (brain -> bridge)
 * Binary:       4-byte big-endian header length, JSON header, JPEG bytes.
 */
object Protocol {
    const val PORT = 8765
    const val WATCHDOG_MS = 500L
    const val VERSION = 1

    data class Telemetry(
        val t: Double,
        val lat: Double,
        val lon: Double,
        val alt: Double,
        val heading: Double,
        val vn: Double,
        val ve: Double,
        val vu: Double,
        val battery: Int,
        val gpsSats: Int,
        val isFlying: Boolean,
        val motorsOn: Boolean,
        val virtualStick: Boolean,
        val flightMode: String,
        val rcModeSwitch: String,
        val homeLat: Double?,
        val homeLon: Double?,
        val homeSet: Boolean,
        val gimbalPitch: Double,
        val roll: Double = 0.0,
        val pitch: Double = 0.0,
        // The phone's own GNSS fix: where the person holding the RC is.
        val phoneLat: Double? = null,
        val phoneLon: Double? = null,
        val phoneAccuracyM: Double? = null,
        val phoneFixAgeS: Double? = null,
    ) {
        /** DJI reports NaN for position before a GPS fix; JSON cannot carry NaN. */
        val gpsFix: Boolean get() = lat.isFinite() && lon.isFinite() && (lat != 0.0 || lon != 0.0)

        private fun num(v: Double): Double = if (v.isFinite()) v else 0.0

        fun toJson(): String = JSONObject().apply {
            put("type", "telemetry")
            put("t", num(t)); put("lat", num(lat)); put("lon", num(lon)); put("alt", num(alt))
            put("heading", num(heading))
            put("vn", num(vn)); put("ve", num(ve)); put("vu", num(vu))
            put("battery", battery); put("gps_sats", gpsSats); put("gps_fix", gpsFix)
            put("is_flying", isFlying); put("motors_on", motorsOn); put("virtual_stick", virtualStick)
            put("flight_mode", flightMode); put("rc_mode_switch", rcModeSwitch)
            put("home_lat", homeLat?.takeIf { it.isFinite() } ?: JSONObject.NULL)
            put("home_lon", homeLon?.takeIf { it.isFinite() } ?: JSONObject.NULL)
            put("home_set", homeSet); put("gimbal_pitch", num(gimbalPitch))
            put("roll", num(roll)); put("pitch", num(pitch))
            put("phone_lat", phoneLat?.takeIf { it.isFinite() } ?: JSONObject.NULL)
            put("phone_lon", phoneLon?.takeIf { it.isFinite() } ?: JSONObject.NULL)
            put("phone_accuracy_m", phoneAccuracyM?.takeIf { it.isFinite() } ?: JSONObject.NULL)
            put("phone_fix_age_s", phoneFixAgeS?.takeIf { it.isFinite() } ?: JSONObject.NULL)
        }.toString()
    }

    data class Velocity(val vn: Double, val ve: Double, val vu: Double, val yawRate: Double) {
        companion object {
            val ZERO = Velocity(0.0, 0.0, 0.0, 0.0)
        }
    }

    fun status(sdkRegistered: Boolean, productConnected: Boolean, product: String, virtualStick: Boolean): String =
        JSONObject().apply {
            put("type", "status")
            put("sdk_registered", sdkRegistered)
            put("product_connected", productConnected)
            put("product", product)
            put("virtual_stick", virtualStick)
            put("bridge_version", "android-$VERSION")
        }.toString()

    /**
     * `detail` carries a value back from a command that answers a question
     * rather than performing an action, so a reader like the SD card state does
     * not have to smuggle its answer through the error field.
     */
    fun ack(id: String?, ok: Boolean, error: String?, detail: String? = null): String =
        JSONObject().apply {
            put("type", "ack"); put("id", id ?: ""); put("ok", ok)
            put("error", if (ok) JSONObject.NULL else (error ?: JSONObject.NULL))
            put("detail", if (ok) (detail ?: error ?: JSONObject.NULL) else JSONObject.NULL)
        }.toString()

    fun event(name: String, detail: String): String = JSONObject().apply {
        put("type", "event"); put("name", name); put("detail", detail)
    }.toString()

    fun frame(t: Double, seq: Long, w: Int, h: Int, gimbalPitch: Double, jpeg: ByteArray): ByteBuffer {
        val header = JSONObject().apply {
            put("type", "frame"); put("t", t); put("seq", seq); put("w", w); put("h", h)
            put("gimbal_pitch", gimbalPitch)
        }.toString().toByteArray(Charsets.UTF_8)
        val buf = ByteBuffer.allocate(4 + header.size + jpeg.size)
        buf.putInt(header.size)
        buf.put(header)
        buf.put(jpeg)
        buf.flip()
        return buf
    }
}
