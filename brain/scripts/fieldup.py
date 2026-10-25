"""One command that takes you from "drone is on" to "cleared to fly".

    python scripts/fieldup.py
    python scripts/fieldup.py --skip-install       # phone already has this build
    python scripts/fieldup.py --no-preflight       # just the link

It does, in order and without anyone touching the phone:

    1. finds the phone over adb
    2. joins the aircraft's Wi-Fi (the phone drifts back to home Wi-Fi indoors)
    3. installs the APK if the one on the phone is older than the sources
    4. launches the app with autostart, so it registers, connects and starts
       the bridge by itself (no `input tap` at fixed coordinates: those break
       on any layout change and silently tap the wrong thing)
    5. forwards port 8765 over USB
    6. waits for telemetry AND for the camera component, which are separate
       events on the P3S and routinely arrive minutes apart
    7. runs the full preflight check and prints GO or NO-GO

Nothing here arms the aircraft or moves anything except the gimbal, inside
preflight_check.

If step 6 reports the camera never attached, the fix is a Disconnect and
Connect cycle in the app; the script says so rather than leaving you guessing.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.client import BridgeClient, BridgeError  # noqa: E402

PKG = "com.jonathanandrei.phantombridge"
APK = Path(__file__).resolve().parents[2] / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
SRC = Path(__file__).resolve().parents[2] / "android" / "app" / "src"
# The RC's Wi-Fi. The password is printed on the remote; keep it out of the repo:
# PHANTOM_SSID / PHANTOM_PSK in the environment, or brain/configs/wifi.local.json
# ({"ssid": ..., "psk": ...}, git-ignored).
import json as _json
import os as _os
_wifi_file = Path(__file__).resolve().parents[1] / "configs" / "wifi.local.json"
_wifi = _json.loads(_wifi_file.read_text()) if _wifi_file.exists() else {}
SSID = _os.environ.get("PHANTOM_SSID", _wifi.get("ssid", "PHANTOM3_xxxxxx"))
PSK = _os.environ.get("PHANTOM_PSK", _wifi.get("psk", ""))


def adb(*args: str, timeout: int = 60) -> tuple[int, str]:
    p = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


def step(n: int, text: str) -> None:
    print(f"\n[{n}] {text}", flush=True)


def wifi_state() -> tuple[str | None, str | None]:
    """The SSID the phone is actually ASSOCIATED with, and its IP.

    `dumpsys wifi` mentions an SSID in scan results and saved networks too, so
    a substring search finds the drone's network while the phone sits on
    nothing at all. Only the `mWifiInfo` line describes the live connection.
    """
    rc, out = adb("shell", "dumpsys", "wifi", timeout=90)
    for line in out.splitlines():
        if line.strip().startswith("mWifiInfo"):
            ssid = re.search(r'SSID:\s*"([^"]+)"', line)
            ip = re.search(r"IP:\s*/?([\d.]+)", line)
            return (ssid.group(1) if ssid else None,
                    ip.group(1) if ip and ip.group(1) != "0.0.0.0" else None)
    return None, None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--ssid", default=SSID)
    ap.add_argument("--psk", default=PSK)
    ap.add_argument("--skip-install", action="store_true")
    ap.add_argument("--skip-wifi", action="store_true")
    ap.add_argument("--no-preflight", action="store_true")
    ap.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for the link")
    args = ap.parse_args()

    step(1, "looking for the phone")
    rc, out = adb("devices")
    devices = [ln.split("\t")[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]
    if not devices:
        print("  no phone over adb. Plug in the USB cable and unlock the screen.")
        return 1
    print(f"  {devices[0]}")

    if not args.skip_wifi:
        step(2, f"joining {args.ssid}")
        state = wifi_state()
        if state[0] == args.ssid and state[1]:
            print(f"  already on it, {state[1]}")
        else:
            if state[0]:
                print(f"  currently on {state[0]!r}, switching")
            adb("shell", "cmd", "wifi", "connect-network", args.ssid, "wpa2", args.psk)
            for _ in range(24):
                time.sleep(1.5)
                ssid, ip = wifi_state()
                # An SSID with no IP means DHCP has not finished, and the DJI SDK
                # cannot reach 192.168.1.1 until it has. Waiting only for the
                # SSID is what made this step lie the first time it ran.
                if ssid == args.ssid and ip:
                    print(f"  joined, {ip}")
                    break
            else:
                ssid, ip = wifi_state()
                print(f"  could not join {args.ssid} (now on {ssid!r}, ip {ip!r}).")
                print("  Is the remote on, linked to the aircraft, and is the aircraft powered?")
                return 1

    if not args.skip_install:
        step(3, "checking the app build")
        if not APK.exists():
            print(f"  no APK at {APK}. Build it first:\n"
                  f"    gradlew.bat -p android assembleDebug")
            return 1
        newest_src = max((p.stat().st_mtime for p in SRC.rglob("*")
                          if p.suffix in (".kt", ".xml")), default=0)
        if APK.stat().st_mtime < newest_src:
            print("  APK is older than the sources; build it before flying")
            return 1
        rc, out = adb("install", "-r", str(APK), timeout=300)
        print("  installed" if "Success" in out else f"  install said: {out[-160:]}")

    step(4, "starting the app (it brings the link up by itself)")
    # The phone's GNSS is the grader for every distance claimed, so the app must
    # be allowed to read it. Granting over adb avoids a dialog on a phone that is
    # clipped to the RC.
    adb("shell", "pm", "grant", PKG, "android.permission.ACCESS_FINE_LOCATION")
    adb("shell", "am", "force-stop", PKG)
    time.sleep(1)
    adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity", "--ez", "autostart", "true")

    step(5, "forwarding port 8765 over USB")
    adb("forward", "tcp:8765", "tcp:8765")

    step(6, "waiting for telemetry and the camera")
    deadline = time.monotonic() + args.timeout
    telemetry = camera = False
    last_note = ""
    while time.monotonic() < deadline:
        try:
            async with BridgeClient(args.url) as bc:
                try:
                    t = await bc.wait_for_telemetry(8)
                except asyncio.TimeoutError:
                    if last_note != "link":
                        print("  bridge is up, aircraft has not reported yet")
                        last_note = "link"
                    await asyncio.sleep(2)
                    continue
                if not telemetry:
                    print(f"  telemetry: {bc.status.get('product') or 'aircraft'}, "
                          f"battery {t.battery}%, {t.gps_sats} satellites")
                    telemetry = True
                try:
                    await bc.command("await_camera", seconds=10)
                    camera = True
                    print("  camera component attached")
                    break
                except BridgeError:
                    if last_note != "cam":
                        print("  camera has not registered yet, retrying")
                        last_note = "cam"
                    await asyncio.sleep(2)
        except Exception:  # noqa: BLE001
            # adb's forwarded port accepts the TCP connection before the app is
            # listening behind it, so a too-early attempt fails inside the
            # websocket handshake rather than as a refused connection.
            if last_note != "boot":
                print("  waiting for the bridge to open the port")
                last_note = "boot"
            await asyncio.sleep(2)

    if not telemetry:
        print("\nNO-GO: no telemetry. Check the remote is on, linked to the aircraft,")
        print("and that the app log says 'bound to wlan0' rather than 'swlan0'.")
        return 1
    if not camera:
        print("\nWARNING: telemetry is fine but the camera component never attached.")
        print("  Tap Disconnect then Connect in the app; that clears it in about 10 s.")
        print("  Video and recording will not work until it does.")

    if args.no_preflight:
        print("\nlink is up")
        return 0

    step(7, "preflight")
    rc = subprocess.run([sys.executable,
                         str(Path(__file__).resolve().parent / "preflight_check.py"),
                         "--url", args.url]).returncode
    print("\n" + ("=" * 62))
    if rc == 0 and camera:
        print("  READY. S1 to the BOTTOM (F) when you want the code to fly it.")
    elif rc == 0:
        print("  Link is up but the camera is missing; fix that before perception work.")
    else:
        print("  NOT READY. See the NO-GO reasons above.")
    print("=" * 62)
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
