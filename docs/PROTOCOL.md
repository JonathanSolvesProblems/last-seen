# Bridge protocol

One WebSocket on port 8765. The Android app (`android/.../bridge/Protocol.kt`) and the
brain (`brain/phantom_brain/protocol.py`) both implement it; the simulator
(`brain/phantom_brain/sim/server.py`) implements the bridge side too. The source
docstrings are the reference; this is the short version.

## Bridge to brain

| type | fields | rate |
|---|---|---|
| `status` | `sdk_registered`, `product_connected`, `product`, `virtual_stick`, `bridge_version` | on connect, on change |
| `telemetry` | `t` (s, phone monotonic or sim time), `lat`, `lon`, `alt` (m above takeoff), `heading` (deg, 0 = N, cw), `vn` `ve` `vu` (m/s), `battery` (%), `gps_sats`, `is_flying`, `motors_on`, `virtual_stick`, `flight_mode`, `rc_mode_switch` (`P`/`A`/`F`), `home_lat`, `home_lon`, `home_set`, `gimbal_pitch` | 10 Hz |
| `ack` | `id`, `ok`, `error` | per command |
| `event` | `name`, `detail` | as they happen |
| binary frame | `>I` header length, JSON `{"type":"frame","t","seq","w","h","gimbal_pitch"}`, JPEG bytes | 8 Hz |

The simulator adds `"truth"` to the frame header (people bboxes, drone pose). The
phone never does.

## Brain to bridge

| type | fields |
|---|---|
| `cmd` | `id`, `name` in `takeoff`, `land`, `confirm_landing`, `go_home`, `cancel_go_home`, `enable_vs`, `disable_vs`, `set_limits` (`max_alt`, `max_radius`), `gimbal` (`pitch`), `ping` |
| `vel` | `vn`, `ve`, `vu` (m/s, ground frame, up positive), `yaw_rate` (deg/s, cw positive) |

`vel` must be streamed at ~10 Hz while flying under virtual stick. The bridge
holds the last value for 500 ms, then commands a hover (watchdog). `go_home` and
`land` switch virtual stick off first; the brain re-enables it if it wants control
back.

## Transport

Laptop to phone over USB: `adb forward tcp:8765 tcp:8765`, brain connects to
`ws://127.0.0.1:8765`. The phone's Wi-Fi belongs to the drone's RC.
