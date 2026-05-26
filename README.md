# AudioDuck 🦆

**Automatically lowers your music when something needs your ears. Raises it back when it's done.**

No clicks. No hotkeys. Just works.

<img width="800" height="450" alt="Image" src="https://github.com/user-attachments/assets/81676417-8a5c-44b8-8fd6-4c25982f0a35" />

---

## Requirements

- **Python 3.8+**
- **`wpctl`** — part of WirePlumber, used to control audio streams

```bash
# Arch / EndeavourOS
sudo pacman -S pipewire wireplumber

# Debian / Ubuntu
sudo apt install pipewire wireplumber
```

---

## Installation

Clone the repo and make the script executable:

```bash
git clone https://github.com/naimneman99/audio-duck.git ~/.local/share/audio-duck
cd ~/.local/share/audio-duck
chmod +x audio-duck.py
```

That's enough to run it.

To have it start automatically with your session, install as a systemd user service:

```bash
ln -s ~/.local/share/audio-duck/audio-duck.py ~/.local/bin/audio-duck.py \
  && cp ~/.local/share/audio-duck/audio-duck.service ~/.config/systemd/user/ \
  && systemctl --user daemon-reload \
  && systemctl --user enable --now audio-duck
```

---

## Usage

```bash
systemctl --user start audio-duck      # start
systemctl --user stop audio-duck       # stop
journalctl --user -u audio-duck -f     # live logs
```

Or run it directly without the service:

```bash
./audio-duck.py --dry-run   # test without touching volumes
./audio-duck.py             # run in foreground
```

---

## Config

AudioDuck uses two app lists in `audio-duck.py` — `BACKGROUND_APPS` (gets ducked) and `FOREGROUND_APPS` (triggers ducking). Apps not in either list are ignored.

### To find the exact name an app registers under

```bash
pw-dump | python3 -c "
import json, sys
for obj in json.load(sys.stdin):
    p = obj.get('info', {}).get('props', {})
    name = p.get('application.name') or p.get('node.name', '')
    if name: print(obj.get('id'), name)
"
```

### To change duck volume or polling rate

- Edit `~/.config/systemd/user/audio-duck.service` and uncomment:

```ini
# ExecStart=%h/.local/bin/audio-duck.py --duck-ratio 0.333 --poll 0.5
```

> `--duck-ratio` is proportional — `0.333` means duck to 1/3 of whatever volume the user had set, not an absolute level. Then apply:

- Then apply: `systemctl --user daemon-reload && systemctl --user restart audio-duck`

---

## Known Limitations

- **Multi-tab browser audio** — tabs in the same browser are separate PipeWire nodes and distinguishable by title. Per-tab ducking is planned for v0.2.
- **Multiple foreground sources** — when two foreground apps are active simultaneously, both trigger ducking. Priority rules are planned for a future version.
- **Gapless volume display** — Gapless reflects PipeWire node volume in its internal slider, so the slider appears lower than expected while ducked. Audio behavior is correct.

---

## Uninstall

```bash
systemctl --user disable --now audio-duck
rm ~/.config/systemd/user/audio-duck.service ~/.local/bin/audio-duck.py
systemctl --user daemon-reload
```

---

## License

[GPL v3](LICENSE) — free and open source.
