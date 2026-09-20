package com.jonathanandrei.phantombridge.dji

import android.content.Context
import android.graphics.Bitmap
import android.graphics.SurfaceTexture
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.TextureView
import dji.sdk.camera.VideoFeeder
import dji.sdk.codec.DJICodecManager
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer

/**
 * Decodes the aircraft's H.264 feed into a TextureView (so the pilot still
 * sees video on the phone) and samples it as JPEG for the brain.
 *
 * Sampling the TextureView keeps the preview alive and sidesteps the YUV
 * format zoo across devices. 8 fps at 960x540 is ~1.5 Mbit/s over USB.
 */
class VideoStreamer(
    private val context: Context,
    private val textureView: TextureView,
    private val log: (String) -> Unit,
) {
    var fps: Int = 8
    var width: Int = 960
    var height: Int = 540
    var jpegQuality: Int = 75
    var gimbalPitchProvider: () -> Double = { 0.0 }
    var onFrame: ((ByteBuffer) -> Unit)? = null
    /** Average colour of the live picture, about once a second (for the frame tint). */
    var onSceneColor: ((rgb: Int, hasPicture: Boolean) -> Unit)? = null
    private var sceneCounter = 0

    private var codec: DJICodecManager? = null
    private val main = Handler(Looper.getMainLooper())
    private var seq = 0L
    private var running = false
    private var bitmap: Bitmap? = null
    @Volatile var framesSent = 0L
        private set
    @Volatile var bytesReceived = 0L
        private set

    private val dataListener = VideoFeeder.VideoDataListener { bytes, size ->
        bytesReceived += size
        codec?.sendDataToDecoder(bytes, size)
    }

    private val sampler = object : Runnable {
        override fun run() {
            if (!running) return
            sample()
            main.postDelayed(this, (1000 / fps).toLong())
        }
    }

    private var attached = false

    fun attach() {
        if (attached) return
        attached = true
        textureView.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
            override fun onSurfaceTextureAvailable(st: SurfaceTexture, w: Int, h: Int) {
                if (codec == null) codec = DJICodecManager(context, st, w, h)
            }
            override fun onSurfaceTextureSizeChanged(st: SurfaceTexture, w: Int, h: Int) {}
            override fun onSurfaceTextureDestroyed(st: SurfaceTexture): Boolean {
                codec?.cleanSurface()
                codec?.destroyCodec()
                codec = null
                return false
            }
            override fun onSurfaceTextureUpdated(st: SurfaceTexture) {}
        }
        if (textureView.isAvailable && codec == null) {
            codec = DJICodecManager(context, textureView.surfaceTexture, textureView.width, textureView.height)
        }
        try {
            VideoFeeder.getInstance()?.primaryVideoFeed?.addVideoDataListener(dataListener)
            log("video feed listener attached")
        } catch (e: Exception) {
            log("video feed attach failed: ${e.message}")
        }
    }

    fun detach() {
        stop()
        attached = false
        try {
            VideoFeeder.getInstance()?.primaryVideoFeed?.removeVideoDataListener(dataListener)
        } catch (_: Exception) {
        }
    }

    fun start() {
        if (running) return
        running = true
        main.post(sampler)
    }

    fun stop() {
        running = false
        main.removeCallbacks(sampler)
    }

    /** True while a laptop is attached: frames get JPEG-encoded and broadcast. */
    @Volatile var streaming = false

    private fun sample() {
        if (!textureView.isAvailable) return
        try {
            val bmp = bitmap?.takeIf { it.width == width && it.height == height }
                ?: Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888).also { bitmap = it }
            textureView.getBitmap(bmp) ?: return
            sceneCounter += 1
            if (sceneCounter % fps == 0) onSceneColor?.let { cb ->
                val small = Bitmap.createScaledBitmap(bmp, 16, 9, true)
                val px = IntArray(16 * 9)
                small.getPixels(px, 0, 16, 0, 0, 16, 9)
                var r = 0L; var g = 0L; var b = 0L
                for (p in px) { r += (p shr 16) and 0xFF; g += (p shr 8) and 0xFF; b += p and 0xFF }
                val n = px.size
                val avg = (0xFF shl 24) or ((r / n).toInt() shl 16) or ((g / n).toInt() shl 8) or (b / n).toInt()
                val luma = (0.299 * r + 0.587 * g + 0.114 * b) / n
                cb(avg, luma > 8.0)
            }
            val cb = onFrame
            if (!streaming || cb == null) return
            val out = ByteArrayOutputStream(64 * 1024)
            bmp.compress(Bitmap.CompressFormat.JPEG, jpegQuality, out)
            seq += 1
            framesSent += 1
            val t = SystemClock.elapsedRealtime() / 1000.0
            val frame = com.jonathanandrei.phantombridge.bridge.Protocol.frame(
                t, seq, width, height, gimbalPitchProvider(), out.toByteArray())
            cb(frame)
        } catch (e: Exception) {
            Log.w(TAG, "frame sample failed", e)
        }
    }

    companion object {
        private const val TAG = "VideoStreamer"
    }
}
