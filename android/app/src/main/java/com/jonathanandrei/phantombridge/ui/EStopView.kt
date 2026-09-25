package com.jonathanandrei.phantombridge.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.util.AttributeSet
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.View
import androidx.core.content.res.ResourcesCompat
import com.jonathanandrei.phantombridge.R
import kotlin.math.min

/**
 * The emergency stop: a red mushroom head on a yellow collar (ISO 13850).
 * The only circle and the only raised object on the screen: a hard 3 dp
 * offset base with no blur, which drops to 0 while pressed. It is also the
 * only thing that keeps its colour when the screen is cut to black and white.
 */
class EStopView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null,
) : View(context, attrs) {

    var onStop: (() -> Unit)? = null
    /** Long press (1.2 s) cuts the propellers. Long, because it is the drastic one. */
    var onCutMotors: (() -> Unit)? = null
    private var cutFired = false
    private val cutRunnable = Runnable {
        cutFired = true
        engaged = true
        performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
        onCutMotors?.invoke()
    }
    var engaged = false
        set(value) {
            field = value
            contentDescription = if (value) "Stop engaged" else "Stop"
            invalidate()
        }

    private val dp = resources.displayMetrics.density
    private val sp = resources.displayMetrics.scaledDensity
    private var pressed = false
    private val red = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = context.getColor(R.color.estop_red) }
    private val collar = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = context.getColor(R.color.estop_collar) }
    private val base = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF0B0B0A.toInt() }
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.estop_ink)
        typeface = ResourcesCompat.getFont(context, R.font.anybody)
        textSize = 18f * sp
        letterSpacing = 0.12f
        fontVariationSettings = "'wdth' 90, 'wght' 800"
        textAlign = Paint.Align.CENTER
    }

    init {
        isClickable = true
        isFocusable = true
        contentDescription = "Stop"
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                pressed = true
                cutFired = false
                postDelayed(cutRunnable, 1200)
                invalidate()
                return true
            }
            MotionEvent.ACTION_UP -> {
                pressed = false
                removeCallbacks(cutRunnable)
                if (!cutFired) {           // a tap hovers; only a long press cuts
                    engaged = true
                    performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
                    onStop?.invoke()
                }
                performClick()
                return true
            }
            MotionEvent.ACTION_CANCEL -> {
                pressed = false
                removeCallbacks(cutRunnable)
                invalidate()
                return true
            }
        }
        return super.onTouchEvent(event)
    }

    override fun performClick(): Boolean {
        super.performClick()
        return true
    }

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        val lift = if (pressed || engaged) 0f else 3f * dp
        val r = min(w, h) / 2f - 3f * dp
        val cx = w / 2f
        val cy = h / 2f - lift + 3f * dp   // head sits up by `lift` over the base
        // base (the part of the mushroom you see below the head)
        canvas.drawCircle(cx, h / 2f + 3f * dp, r, base)
        // collar
        canvas.drawCircle(cx, cy, r, collar)
        // head
        canvas.drawCircle(cx, cy, r - 7f * dp, red)
        val label = if (engaged) "STOPPED" else "STOP"
        text.textSize = (if (engaged) 14f else 18f) * sp
        canvas.drawText(label, cx, cy + text.textSize * 0.36f, text)
    }
}
