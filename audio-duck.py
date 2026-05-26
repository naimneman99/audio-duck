#!/usr/bin/env python3
"""
audio-duck.py — Automatic audio ducking daemon for Linux (PipeWire)

How it works:
  - Polls active audio nodes every POLL_INTERVAL seconds via `pw-dump`
  - Joins Client objects to Node objects via client.id to resolve app names
  - Background apps are ducked to pre_duck_volume × DUCK_RATIO (proportional, not absolute)
  - When foreground goes quiet, background fades back to the pre-duck volume
  - Apps not in either list are ignored entirely (won't trigger or be ducked)

Requirements:  Python 3.8+, pipewire, wireplumber (wpctl)
Install deps:  sudo pacman -S pipewire wireplumber   (Arch/EndeavourOS)
               sudo apt install pipewire wireplumber  (Debian/Ubuntu)

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
import sys
import signal
import argparse
import logging
from dataclasses import dataclass
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

# Ratio to multiply the pre-duck volume by when ducking (0.0 – 1.0)
# e.g. 0.333 means duck to 1/3 of whatever the user had it at
DUCK_RATIO: float = 0.333

# How many seconds between polls
POLL_INTERVAL: float = 0.5

# How many steps to take when fading (spread over ~0.4s)
FADE_STEPS: int = 8
FADE_DELAY: float = 0.05  # seconds between each fade step

# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("audio-duck")


@dataclass
class SinkInput:
    index: int           # PipeWire node id
    app_name: str        # resolved from Client object via client.id
    media_name: str      # media.name — tab title for browsers, track info for players
    volume: float        # 0.0 – 1.0, average of channelVolumes
    muted: bool
    corked: bool         # True = not actively producing audio


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout


def list_sink_inputs() -> list[SinkInput]:
    """
    Return all audio output nodes from the PipeWire graph via pw-dump.

    PipeWire separates identity (Client) from streaming (Node).
    Spotify registers as a Client with application.name = 'spotify',
    then opens a Node with only client.id linking back to that Client.
    pactl cannot see nodes that bypass the PulseAudio compatibility layer;
    pw-dump sees the full graph regardless.

    Join strategy:
      1. Build a client_map: { client_id -> application.name }
         from all PipeWire:Interface:Client objects
      2. For each PipeWire:Interface:Node with media.class = Stream/Output/Audio,
         look up app_name via props.client.id → client_map
    """
    raw = _run(["pw-dump"])
    try:
        objects = json.loads(raw)
    except json.JSONDecodeError:
        return []

    # ── Step 1: build client_id → app_name lookup ──────────────────────────
    client_map: dict[int, str] = {}
    for obj in objects:
        if obj.get("type") != "PipeWire:Interface:Client":
            continue
        props = obj.get("info", {}).get("props", {})
        client_id = obj.get("id")
        app_name = (
            props.get("application.name", "")
            or props.get("application.process.binary", "")
        ).lower().strip()
        if client_id is not None and app_name:
            client_map[client_id] = app_name

    # ── Step 2: collect audio output nodes ─────────────────────────────────
    inputs: list[SinkInput] = []
    for obj in objects:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue

        info = obj.get("info", {})
        props = info.get("props", {})

        # Only care about audio output streams
        if props.get("media.class") != "Stream/Output/Audio":
            continue

        node_id = obj.get("id", -1)
        client_id = props.get("client.id")

        # Resolve app name: prefer Client lookup, fall back to node props
        if client_id is not None and client_id in client_map:
            app_name = client_map[client_id]
        else:
            app_name = (
                props.get("application.name", "")
                or props.get("node.name", "")
            ).lower().strip()

        media_name = props.get("media.name", "")

        # ── Volume ─────────────────────────────────────────────────────────
        # channelVolumes is the per-channel linear float array in Props.
        # This is what Spotify and native PipeWire apps expose.
        # Falls back to top-level volume (always 1.0 for native apps) if absent.
        channel_volumes: list[float] = []
        params_props = info.get("params", {}).get("Props", [])
        if params_props:
            cv = params_props[0].get("channelVolumes", [])
            if cv:
                channel_volumes = [float(v) for v in cv]

        if channel_volumes:
            avg_vol = sum(channel_volumes) / len(channel_volumes)
        else:
            # Fallback: top-level volume field (pulse-compatible apps)
            avg_vol = float(params_props[0].get("volume", 1.0)) if params_props else 1.0

        muted = params_props[0].get("mute", False) if params_props else False

        # Node state: "running" = actively producing audio
        # "idle", "suspended", "error" = not producing audio
        node_state = info.get("state", "")
        corked = node_state != "running"

        inputs.append(SinkInput(
            index=node_id,
            app_name=app_name,
            media_name=media_name,
            volume=avg_vol,
            muted=muted,
            corked=corked,
        ))

    return inputs


def set_volume(index: int, level: float, dry_run: bool = False) -> None:
    """
    Set absolute volume for a PipeWire node via wpctl.
    wpctl accepts a linear float directly (0.0 = silent, 1.0 = 100%).
    """
    level_str = f"{level:.6f}"
    cmd = ["wpctl", "set-volume", str(index), level_str]
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
    """Smoothly fade a node between two volume levels."""
    if abs(from_vol - to_vol) < 0.01:
        return
    for step in range(1, FADE_STEPS + 1):
        t = step / FADE_STEPS
        vol = from_vol + (to_vol - from_vol) * t
        set_volume(index, vol, dry_run)
        time.sleep(FADE_DELAY)


def classify(app_name: str) -> str:
    """Return 'background', 'foreground', or 'ignored'."""
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
    parser.add_argument("--duck-ratio", type=float, default=DUCK_RATIO,
                        help=f"Ratio to multiply pre-duck volume by (default {DUCK_RATIO})")
    parser.add_argument("--poll", type=float, default=POLL_INTERVAL,
                        help=f"Polling interval in seconds (default {POLL_INTERVAL})")
    args = parser.parse_args()

    duck_ratio    = args.duck_ratio
    poll_interval = args.poll
    dry_run       = args.dry_run

    if dry_run:
        log.info("Dry-run mode — no volumes will be changed")

    # Track state per node index as (status, pre_duck_volume, ducked_to)
    # status:        "ducked"   = we faded it down
    #                "restored" = volume is at whatever the user had it
    # pre_duck_vol:  snapshot taken just before ducking — restore target
    # ducked_to:     exact volume we set — used as from_vol for restore fade
    #                avoids reading si.volume mid-fade which causes drift
    state: dict[int, tuple[str, float, float]] = {}

    def _on_signal(signum, frame):
        log.info(f"Received signal {signum} — restoring all ducked inputs...")
        for idx, (st, saved_volume, _) in state.items():
            if st == "ducked":
                set_volume(idx, saved_volume)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGHUP, _on_signal)

    log.info(f"Daemon started  duck_ratio={duck_ratio:.0%}  poll={poll_interval}s")

    while True:
        try:
            inputs = list_sink_inputs()

            # Active = not muted, not corked (node state != running)
            active = [i for i in inputs if not i.corked and not i.muted]

            background = [i for i in active if classify(i.app_name) == "background"]
            foreground = [i for i in active if classify(i.app_name) == "foreground"]

            foreground_active = len(foreground) > 0

            for si in background:
                current_state, _, _ = state.get(si.index, ("restored", si.volume, si.volume))

                if foreground_active and current_state != "ducked":
                    # Only snapshot if the current volume looks like a user-set
                    # value — not something we put there. If it's at or near
                    # our duck target the previous restore hasn't finished or
                    # state got out of sync; keep the last known good snapshot.
                    _, last_saved, _ = state.get(si.index, ("restored", si.volume, si.volume))
                    pre_duck_volume = si.volume if si.volume > last_saved * duck_ratio * 1.5 else last_saved
                    duck_target = pre_duck_volume * duck_ratio
                    log.info(
                        f"Ducking [{si.index}] {si.app_name!r}  "
                        f"{pre_duck_volume:.2f} → {duck_target:.2f}  "
                        f"(foreground: {[f.app_name for f in foreground]})"
                    )
                    fade_volume(si.index, pre_duck_volume, duck_target, dry_run)
                    state[si.index] = ("ducked", pre_duck_volume, duck_target)

                elif not foreground_active and current_state == "ducked":
                    _, saved_volume, ducked_to = state[si.index]
                    log.info(
                        f"Restoring [{si.index}] {si.app_name!r}  "
                        f"{ducked_to:.2f} → {saved_volume:.2f}"
                    )
                    # Use ducked_to as from_vol — never si.volume which may be
                    # caught mid-fade by the poller and cause volume drift
                    fade_volume(si.index, ducked_to, saved_volume, dry_run)
                    state[si.index] = ("restored", saved_volume, ducked_to)

            # Clean up state for nodes that no longer exist
            active_indices = {i.index for i in inputs}
            gone = [k for k in state if k not in active_indices]
            for k in gone:
                del state[k]

        except KeyboardInterrupt:
            log.info("Interrupted — restoring all ducked inputs...")
            for idx, (st, saved_volume, _) in state.items():
                if st == "ducked":
                    set_volume(idx, saved_volume, dry_run)
            sys.exit(0)
        except Exception as exc:
            log.warning(f"Poll error: {exc}")

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
