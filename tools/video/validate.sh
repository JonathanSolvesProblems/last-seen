#!/bin/sh
# Checks on the rendered file, not the plan. Usage: sh validate.sh demo.mp4
f="$1"
echo "== streams"; ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate,sample_rate,channels -of compact "$f"
echo "== duration"; ffprobe -v error -show_entries format=duration -of csv=p=0 "$f"
echo "== loudness (want about -16 LUFS)"; ffmpeg -hide_banner -i "$f" -af ebur128 -f null - 2>&1 | grep -E "I:|LRA:|Peak:" | tail -3
echo "== black frames (want none)"; ffmpeg -hide_banner -i "$f" -vf "blackdetect=d=0.3:pix_th=0.06" -f null - 2>&1 | grep black_start || echo "  none"
echo "== head: exact-silence 10 ms windows in the first 6 s (want 0)"
ffmpeg -hide_banner -i "$f" -t 6 -af "astats=metadata=1:reset=480,ametadata=print:key=lavfi.astats.Overall.Peak_level:file=-" -f null - 2>/dev/null | grep -c "Peak_level=-inf"
echo "== audio packet durations (want essentially all 1024)"
ffprobe -v error -select_streams a -show_entries packet=duration -of csv=p=0 "$f" | sort | uniq -c | sort -rn | head -4
echo "== start times (want both 0)"
ffprobe -v error -show_entries stream=codec_type,start_time -of compact "$f"
echo "== scene changes per 10 s (a beat with 0 is a still)"
ffmpeg -hide_banner -i "$f" -vf "select='gt(scene,0.0005)',metadata=print:file=-" -f null - 2>/dev/null | grep -o "pts_time:[0-9.]*" | cut -d: -f2 | awk '{b=int($1/10); c[b]++} END {for (i=0;i<=b;i++) printf "%3ds:%4d  ", i*10, c[i]+0; print ""}'
