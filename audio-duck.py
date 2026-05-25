#!/usr/bin/env python3
"""
audio-duck.py — Automatic audio ducking daemon for Linux (PipeWire / PulseAudio)

How it works:
  - Polls active sink-inputs every POLL_INTERVAL seconds via `pactl`
  - If any FOREGROUND app is playing, background apps get ducked to DUCK_LEVEL
  - When foreground goes quiet, background fades back to the pre-duck volume
  - Apps not in either list are ignored entirely (won't trigger or be ducked)

Requirements:  Python 3.8+, pulseaudio-utils (pactl) or pipewire-pulse
Install deps:  sudo pacman -S libpulse   (Arch/EndeavourOS)
               sudo apt install pulseaudio-utils  (Debian/Ubuntu)

Usage:
  chmod +x audio-duck.py
  ./audio-duck.py            # run in foreground
  ./audio-duck.py --dry-run  # print what it would do without touching volumes

Systemd user service:
  See the audio-duck.service file alongside this script.
"""

import subprocess
import time
import json
import re
import sys
import argparse
import logging
from dataclasses import dataclass, field
from typing import Optional

# ─── Configuration ────────────────────────────────────────────────────────────

# Apps whose audio is treated as background music (duck these)
BACKGROUND_APPS: set[str] = {
    "spotify",
    "gapless",
    "rhythmbox",
    "amarok",
    "clementine",
    "quodlibet",
    "cantata",
    "strawberry",
    "lollypop",
    "mpd",
    "cmus",
    "cs2",
}

# Apps that trigger ducking when they start producing audio
FOREGROUND_APPS: set[str] = {
    "firefox",
    "chromium",
    "chrome",
    "mpv",
    "vlc",
    "celluloid",
    "totem",
    "zoom",
    "teams",
    "discord",
    "slack",
    "telegram",
    "signal",
    "skype",
    "whatsapp",
}

# Volume to duck background music to (0.0 – 1.0)
DUCK_LEVEL: float = 0.333

# How many seconds between polls
POLL_INTERVAL: float = 0.5

# How many steps to take when fading (spread over ~0.4s)
FADE_STEPS: int = 8
FADE_DELAY: float = 0.025  # seconds between each fade step

# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("audio-duck")


@dataclass
class SinkInput:
    index: int
    app_name: str
    volume: float          # 0.0 – 1.0
    muted: bool
    corked: bool           # paused / not producing audio


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout


def list_sink_inputs() -> list[SinkInput]:
    """Return all active PulseAudio/PipeWire sink-inputs via pactl."""
    raw = _run(["pactl", "--format=json", "list", "sink-inputs"])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    inputs: list[SinkInput] = []
    for item in data:
        idx = item.get("index", -1)
        props = item.get("properties", {})
        app_name = (
            props.get("application.name", "")
            or props.get("application.process.binary", "")
        ).lower().strip()

        # Volume: average across channels, expressed as a fraction
        vol_map = item.get("volume", {})
        fractions = []
        for ch_data in vol_map.values():
            val = ch_data.get("value_percent", "0%").replace("%", "").strip()
            try:
                fractions.append(float(val) / 100.0)
            except ValueError:
                pass
        avg_vol = sum(fractions) / len(fractions) if fractions else 0.0

        muted = item.get("mute", False)
        corked = item.get("corked", False)

        inputs.append(SinkInput(
            index=idx,
            app_name=app_name,
            volume=avg_vol,
            muted=muted,
            corked=corked,
        ))
    return inputs


def set_volume(index: int, level: float, dry_run: bool = False) -> None:
    """Set absolute volume for a sink-input (0.0 = silent, 1.0 = 100%)."""
    pct = f"{int(round(level * 100))}%"
    cmd = ["pactl", "set-sink-input-volume", str(index), pct]
    if dry_run:
        log.info(f"[dry-run] would run: {' '.join(cmd)}")
    else:
        subprocess.run(cmd, capture_output=True)


def fade_volume(
    index: int,
    from_vol: float,
    to_vol: float,
    dry_run: bool = False,
) -> None:
    """Smoothly fade a sink-input between two volume levels."""
    if abs(from_vol - to_vol) < 0.01:
        return
    for step in range(1, FADE_STEPS + 1):
        t = step / FADE_STEPS
        vol = from_vol + (to_vol - from_vol) * t
        set_volume(index, vol, dry_run)
        time.sleep(FADE_DELAY)


def classify(app_name: str) -> str:
    """Return 'background', 'foreground', or 'unknown'."""
    name = app_name.lower()
    for bg in BACKGROUND_APPS:
        if bg in name:
            return "background"
    for fg in FOREGROUND_APPS:
        if fg in name:
            return "foreground"
    # Unknown apps are ignored — only explicitly listed apps participate
    return "ignored"


def main() -> None:
    parser = argparse.ArgumentParser(description="Automatic audio ducking daemon")
    parser.add_argument("--dry-run", action="store_true", help="Don't change any volumes")
    parser.add_argument("--duck-level", type=float, default=DUCK_LEVEL,
                        help=f"Volume for background music when ducked (default {DUCK_LEVEL})")
    parser.add_argument("--poll", type=float, default=POLL_INTERVAL,
                        help=f"Polling interval in seconds (default {POLL_INTERVAL})")
    args = parser.parse_args()

    duck_level    = args.duck_level
    poll_interval = args.poll
    dry_run       = args.dry_run

    if dry_run:
        log.info("Dry-run mode — no volumes will be changed")

    # Track state per sink-input index as (status, pre_duck_volume)
    # status: "ducked" = we set it to duck_level
    #         "restored" = volume is at whatever the user had it
    state: dict[int, tuple[str, float]] = {}

    log.info(f"Daemon started  duck={duck_level:.0%}  poll={poll_interval}s")

    while True:
        try:
            inputs = list_sink_inputs()

            # Active = not muted, not corked (paused)
            active = [i for i in inputs if not i.corked and not i.muted]

            background = [i for i in active if classify(i.app_name) == "background"]
            foreground = [i for i in active if classify(i.app_name) == "foreground"]

            foreground_active = len(foreground) > 0

            for si in background:
                current_state, _ = state.get(si.index, ("restored", si.volume))

                if foreground_active and current_state != "ducked":
                    pre_duck_volume = si.volume
                    log.info(
                        f"Ducking [{si.index}] {si.app_name!r}  "
                        f"{pre_duck_volume:.0%} → {duck_level:.0%}  "
                        f"(foreground: {[f.app_name for f in foreground]})"
                    )
                    fade_volume(si.index, pre_duck_volume, duck_level, dry_run)
                    state[si.index] = ("ducked", pre_duck_volume)

                elif not foreground_active and current_state == "ducked":
                    _, saved_volume = state[si.index]
                    log.info(
                        f"Restoring [{si.index}] {si.app_name!r}  "
                        f"{si.volume:.0%} → {saved_volume:.0%}"
                    )
                    fade_volume(si.index, si.volume, saved_volume, dry_run)
                    state[si.index] = ("restored", saved_volume)

            # Clean up state for sink-inputs that no longer exist
            active_indices = {i.index for i in inputs}
            gone = [k for k in state if k not in active_indices]
            for k in gone:
                del state[k]

        except KeyboardInterrupt:
            log.info("Interrupted — restoring all ducked inputs...")
            for idx, (st, saved_volume) in state.items():
                if st == "ducked":
                    set_volume(idx, saved_volume, dry_run)
            sys.exit(0)
        except Exception as exc:
            log.warning(f"Poll error: {exc}")

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
