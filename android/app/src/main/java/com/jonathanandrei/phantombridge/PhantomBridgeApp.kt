package com.jonathanandrei.phantombridge

import android.app.Application
import android.content.Context
import androidx.multidex.MultiDex
import com.cySdkyc.clx.Helper

/**
 * DJI MSDK v4 requires its native loader to run before any SDK class is touched,
 * so it lives in attachBaseContext, exactly as in the DJI sample app.
 * (The loader class was com.secneo.sdk.Helper before 4.16; it is
 * com.cySdkyc.clx.Helper in 4.16+.)
 */
class PhantomBridgeApp : Application() {
    override fun attachBaseContext(base: Context) {
        super.attachBaseContext(base)
        MultiDex.install(this)
        Helper.install(this)
    }
}
