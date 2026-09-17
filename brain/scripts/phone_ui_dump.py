"""Print the bridge app's status / log text from the phone via uiautomator (debug helper).

    python scripts/phone_ui_dump.py
"""

from __future__ import annotations

import html
import re
import subprocess

PKG = "com.jonathanandrei.phantombridge"


def main() -> None:
    subprocess.run(["adb", "shell", "uiautomator", "dump", "/sdcard/ui.xml"], check=True, capture_output=True)
    x = subprocess.run(["adb", "shell", "cat", "/sdcard/ui.xml"], check=True, capture_output=True).stdout.decode(
        "utf-8", errors="ignore")
    subprocess.run(["adb", "shell", "rm", "/sdcard/ui.xml"], capture_output=True)
    for node in re.findall(r"<node [^>]*/?>", x):
        m_id = re.search(r'resource-id="' + re.escape(PKG) + r':id/(\w+)"', node)
        m_txt = re.search(r'text="([^"]*)"', node)
        if m_id and m_txt and m_id.group(1) in ("statusText", "logText", "overlayText"):
            print(f"== {m_id.group(1)}")
            print(html.unescape(m_txt.group(1)))


if __name__ == "__main__":
    main()
