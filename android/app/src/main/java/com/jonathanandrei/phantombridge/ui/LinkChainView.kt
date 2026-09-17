package com.jonathanandrei.phantombridge.ui

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.util.AttributeSet
import android.view.View
import android.view.animation.DecelerateInterpolator
import androidx.core.content.res.ResourcesCompat
import com.jonathanandrei.phantombridge.R

/**
 * The signal path, drawn from real state, in the top letterbox bar:
 *
 *     BRAIN ----- PHONE ----- CONTROLLER ----- AIRCRAFT
 *
 * A hop is solid when that link is up and dotted when it is not. Each node
 * "exposes" (fades in) over 400 ms when its link comes up, and nothing on it
 * ever moves at rest. This is the one diagram in the app and it is also the
 * architecture slide for the video: laptop, phone, controller, drone.
 */
class LinkChainView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null,
) : View(context, attrs) {

    data class State(
        val brainClients: Int = 0,      // brain <-> phone: websocket clients
        val sdkRegistered: Boolean = false,
        val productConnected: Boolean = false,   // phone <-> RC (wifi link)
        val telemetryLive: Boolean = false,      // RC <-> aircraft (state callback flowing)
        val productName: String = "",
        val port: Int = 8765,
        val virtualStick: Boolean = false,
    )

    private var state = State()
    private val exposure = FloatArray(4) { 0f }   // per node, 0..1
    private var animators = arrayOfNulls<ValueAnimator>(4)

    private val dp = resources.displayMetrics.density
    private val sp = resources.displayMetrics.scaledDensity
    private val ink = context.getColor(R.color.ink_on_bars)
    private val inkMuted = context.getColor(R.color.ink_muted_on_bars)
    private val typeface = ResourcesCompat.getFont(context, R.font.anybody)

    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        strokeWidth = 2f * dp
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.BUTT
    }
    private val dotPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        strokeWidth = 1f * dp
        style = Paint.Style.STROKE
        pathEffect = DashPathEffect(floatArrayOf(2f * dp, 5f * dp), 0f)
        color = inkMuted
    }
    private val nodePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = this@LinkChainView.typeface
        textSize = 12f * sp
        letterSpacing = 0.10f
        fontVariationSettings = "'wdth' 100, 'wght' 700"
        textAlign = Paint.Align.CENTER
    }
    private val subPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = this@LinkChainView.typeface
        textSize = 11f * sp
        letterSpacing = 0.04f
        fontVariationSettings = "'wdth' 100, 'wght' 500"
        textAlign = Paint.Align.CENTER
        fontFeatureSettings = "tnum"
    }

    fun update(s: State) {
        val previous = state
        state = s
        val lit = booleanArrayOf(s.brainClients > 0, true, s.productConnected, s.telemetryLive)
        val wasLit = booleanArrayOf(previous.brainClients > 0, true, previous.productConnected, previous.telemetryLive)
        for (i in 0 until 4) {
            val target = if (lit[i]) 1f else 0.35f
            if (lit[i] != wasLit[i] || (exposure[i] == 0f)) animateNode(i, target)
        }
        invalidate()
    }

    private fun animateNode(i: Int, target: Float) {
        animators[i]?.cancel()
        animators[i] = ValueAnimator.ofFloat(exposure[i], target).apply {
            duration = 400
            interpolator = DecelerateInterpolator()
            addUpdateListener { exposure[i] = it.animatedValue as Float; invalidate() }
            start()
        }
    }

    override fun onDraw(canvas: Canvas) {
        val s = state
        val w = width.toFloat()
        val h = height.toFloat()
        val padX = 72f * dp
        val n = 4
        val xs = FloatArray(n) { i -> padX + (w - 2 * padX) * i / (n - 1) }
        val lineY = h * 0.58f
        val labels = arrayOf("BRAIN", "PHONE", "CONTROLLER", "AIRCRAFT")
        val subs = arrayOf(
            if (s.brainClients > 0) "${s.brainClients} CLIENT${if (s.brainClients > 1) "S" else ""} :${s.port}" else "NO CLIENT :${s.port}",
            if (s.sdkRegistered) "SDK REGISTERED" else "SDK NOT REGISTERED",
            if (s.productConnected) (s.productName.ifBlank { "WI-FI LINK" }).uppercase() else "NO LINK",
            if (s.telemetryLive) (if (s.virtualStick) "VIRTUAL STICK" else "TELEMETRY") else "NO TELEMETRY",
        )
        val hops = booleanArrayOf(s.brainClients > 0, s.productConnected, s.telemetryLive)

        // hops
        for (i in 0 until n - 1) {
            val r = 5f * dp
            if (hops[i]) {
                linePaint.color = ink
                canvas.drawLine(xs[i] + r, lineY, xs[i + 1] - r, lineY, linePaint)
            } else {
                canvas.drawLine(xs[i] + r, lineY, xs[i + 1] - r, lineY, dotPaint)
            }
        }
        // nodes: a square gate mark, exposure sets alpha
        for (i in 0 until n) {
            val a = (exposure[i].coerceIn(0.35f, 1f) * 255).toInt()
            nodePaint.color = ink
            nodePaint.alpha = a
            val r = 5f * dp
            canvas.drawRect(xs[i] - r, lineY - r, xs[i] + r, lineY + r, nodePaint)
            labelPaint.color = ink
            labelPaint.alpha = a
            canvas.drawText(labels[i], xs[i], lineY - 11f * dp, labelPaint)
            subPaint.color = if (exposure[i] > 0.6f) inkMuted else inkMuted
            subPaint.alpha = (a * 0.85f).toInt()
            canvas.drawText(subs[i], xs[i], lineY + 16f * dp, subPaint)
        }
    }
}
