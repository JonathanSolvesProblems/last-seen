package com.jonathanandrei.phantombridge.dji

import android.annotation.SuppressLint
import android.content.Context
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Bundle
import android.os.Looper
import android.os.SystemClock

/**
 * Where the phone is, from the phone's own GPS.
 *
 * The phone sits on the RC, and the RC is in the pilot's hands, so during a
 * follow or a search test this is where the SUBJECT is. That makes it the
 * grader for every distance the brain claims: the drone's camera says the
 * person is 9 m away, Android's GNSS says where the person actually stood.
 * Nothing in the vision pipeline touches this number, which is the point.
 *
 * GPS provider only, never the fused or network provider, so a Wi-Fi-derived
 * position a hundred metres off cannot slip in on a bad day. Accuracy and age
 * travel with every reading so the brain can refuse a stale or vague fix.
 */
class PhoneLocation(private val log: (String) -> Unit) : LocationListener {

    @Volatile var last: Location? = null
        private set
    @Volatile private var fixAtMs: Long = 0L
    private var started = false

    @SuppressLint("MissingPermission")
    fun start(context: Context) {
        if (started) return
        val lm = context.getSystemService(Context.LOCATION_SERVICE) as? LocationManager ?: return
        try {
            lm.requestLocationUpdates(LocationManager.GPS_PROVIDER, 1000L, 0f, this, Looper.getMainLooper())
            started = true
            log("phone GPS: listening")
        } catch (e: SecurityException) {
            log("phone GPS: no location permission (grant ACCESS_FINE_LOCATION)")
        } catch (e: Exception) {
            log("phone GPS unavailable: ${e.message}")
        }
    }

    /** Seconds since the last fix, or null if there has never been one. */
    fun ageSeconds(): Double? =
        if (fixAtMs == 0L) null else (SystemClock.elapsedRealtime() - fixAtMs) / 1000.0

    override fun onLocationChanged(location: Location) {
        last = location
        fixAtMs = SystemClock.elapsedRealtime()
    }

    @Deprecated("Deprecated in API")
    override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
    override fun onProviderEnabled(provider: String) {}
    override fun onProviderDisabled(provider: String) { log("phone GPS: provider disabled") }
}
