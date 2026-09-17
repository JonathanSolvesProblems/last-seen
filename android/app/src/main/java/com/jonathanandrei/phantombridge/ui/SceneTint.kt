package com.jonathanandrei.phantombridge.ui

import android.animation.ArgbEvaluator
import android.animation.ValueAnimator
import android.graphics.ColorMatrix
import android.graphics.ColorMatrixColorFilter
import android.graphics.Paint
import android.view.View
import android.view.animation.DecelerateInterpolator
import androidx.core.graphics.ColorUtils

/**
 * The frame is lit by the picture.
 *
 * Every second the video streamer reports the average colour of the live
 * frame. The frame margins take that HUE at a fixed lightness and a low,
 * fixed saturation, so the chrome reads as "in the scene" (green-grey over
 * grass, blue-grey under sky) without ever costing legibility.
 *
 * STOP is a cut: saturation drops to zero at 0 ms on the frame and on the
 * picture itself (a hardware-layer colour matrix on the TextureView). Colour
 * returns over 900 ms only when the brain regains control.
 */
class SceneTint(
    private val frameViews: List<View>,
    private val pictureView: View,
    private val baseFrame: Int,       // resting colour when there is no picture
    private val dark: Boolean,
) {
    private val sat = if (dark) 0.16f else 0.12f
    private val light = if (dark) 0.31f else 0.82f
    private var hue: Float? = null
    private var current = baseFrame
    private var animator: ValueAnimator? = null
    var cut = false
        private set

    fun onSceneColor(rgb: Int) {
        val hsl = FloatArray(3)
        ColorUtils.colorToHSL(rgb, hsl)
        // a grey or near-black scene carries no hue worth taking
        hue = if (hsl[1] < 0.10f || hsl[2] < 0.06f) null else hsl[0]
        animateTo(target(), 600)
    }

    fun onNoPicture() {
        hue = null
        animateTo(target(), 600)
    }

    fun setCut(on: Boolean) {
        if (cut == on) return
        cut = on
        val m = ColorMatrix().apply { setSaturation(if (on) 0f else 1f) }
        pictureView.setLayerType(View.LAYER_TYPE_HARDWARE, Paint().apply { colorFilter = ColorMatrixColorFilter(m) })
        if (on) {
            animator?.cancel()
            apply(target())            // 0 ms: a cut
        } else {
            animateTo(target(), 900)
        }
    }

    private fun target(): Int {
        val h = hue
        if (h == null || cut) {
            // unexposed frame: base colour, fully desaturated when cut
            if (!cut) return baseFrame
            val hsl = FloatArray(3)
            ColorUtils.colorToHSL(baseFrame, hsl)
            return ColorUtils.HSLToColor(floatArrayOf(hsl[0], 0f, hsl[2]))
        }
        return ColorUtils.HSLToColor(floatArrayOf(h, sat, light))
    }

    private fun animateTo(color: Int, durationMs: Long) {
        animator?.cancel()
        animator = ValueAnimator.ofObject(ArgbEvaluator(), current, color).apply {
            duration = durationMs
            interpolator = DecelerateInterpolator()
            addUpdateListener { apply(it.animatedValue as Int) }
            start()
        }
    }

    private fun apply(color: Int) {
        current = color
        for (v in frameViews) v.setBackgroundColor(color)
    }
}
