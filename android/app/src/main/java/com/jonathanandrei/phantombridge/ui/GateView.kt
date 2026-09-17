package com.jonathanandrei.phantombridge.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.util.AttributeSet
import android.view.View
import android.widget.FrameLayout
import androidx.core.content.res.ResourcesCompat
import com.jonathanandrei.phantombridge.R

/**
 * A 16:9 window ("the gate") for the picture: sizes its width from the
 * available height so the video is never stretched, and draws the gate marks
 * (corner brackets) and a NO PICTURE notice over an empty window.
 */
class GateView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null,
) : FrameLayout(context, attrs) {

    var hasPicture = false
        set(value) {
            if (field != value) {
                field = value
                marks.invalidate()
            }
        }

    private val marks = GateMarks(context)

    init {
        setWillNotDraw(false)
        addView(marks, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val h = MeasureSpec.getSize(heightMeasureSpec)
        val maxW = MeasureSpec.getSize(widthMeasureSpec)
        var w = h * 16 / 9
        var hh = h
        if (w > maxW) {
            w = maxW
            hh = maxW * 9 / 16
        }
        super.onMeasure(MeasureSpec.makeMeasureSpec(w, MeasureSpec.EXACTLY),
                        MeasureSpec.makeMeasureSpec(hh, MeasureSpec.EXACTLY))
    }

    override fun addView(child: View?, index: Int, params: android.view.ViewGroup.LayoutParams?) {
        super.addView(child, index, params)
        // keep the marks on top of whatever is added later (the TextureView)
        if (child !== marks && indexOfChild(marks) >= 0) marks.bringToFront()
    }

    private inner class GateMarks(context: Context) : View(context) {
        private val dp = resources.displayMetrics.density
        private val sp = resources.displayMetrics.scaledDensity
        private val ink = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = context.getColor(R.color.ink_on_bars)
            style = Paint.Style.STROKE
            strokeWidth = 2f * dp
            alpha = 120
        }
        private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = context.getColor(R.color.ink_muted_on_bars)
            typeface = ResourcesCompat.getFont(context, R.font.anybody)
            textSize = 13f * sp
            letterSpacing = 0.14f
            fontVariationSettings = "'wdth' 100, 'wght' 600"
            textAlign = Paint.Align.CENTER
        }

        override fun onDraw(canvas: Canvas) {
            val w = width.toFloat()
            val h = height.toFloat()
            val m = 10f * dp
            val l = 22f * dp
            // corner brackets
            for ((sx, sy) in listOf(1 to 1, -1 to 1, 1 to -1, -1 to -1)) {
                val x = if (sx > 0) m else w - m
                val y = if (sy > 0) m else h - m
                canvas.drawLine(x, y, x + sx * l, y, ink)
                canvas.drawLine(x, y, x, y + sy * l, ink)
            }
            if (!hasPicture) {
                canvas.drawText("NO PICTURE", w / 2f, h / 2f + text.textSize * 0.36f, text)
            }
        }
    }
}
