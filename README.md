# AudioDuck 🦆

**Automatically lowers your music when something needs your ears. Raises it back when it's done.**

No clicks. No hotkeys. Just works.

<img width="800" height="450" alt="Image" src="https://github.com/user-attachments/assets/81676417-8a5c-44b8-8fd6-4c25982f0a35" />

---

## Requirements

- **Python 3.8+** — used for running the daemon itself
- **`pactl`** — used to read and control audio streams

```bash
# Arch / EndeavourOS
sudo pacman -S libpulse

# Debian / Ubuntu
sudo apt install pulseaudio-utils
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
journalctl --user -u audio-duck -f     # confirm it's working
```

Or run it directly without the service:

```bash
./audio-duck.py
```

---

## Config

AudioDuck uses two app lists in `audio-duck.py` — `BACKGROUND_APPS` (gets ducked) and `FOREGROUND_APPS` (triggers ducking). Apps not in either list are ignored.

### Change duck volume or polling rate

- Edit `~/.config/systemd/user/audio-duck.service` and uncomment:

```ini
# ExecStart=%h/.local/bin/audio-duck.py --duck-level 0.333 --poll 0.5
```

- Then apply: `systemctl --user daemon-reload && systemctl --user restart audio-duck`

---

## Known Limitations

- **Multi-tab browser audio** — two tabs in the same browser share one audio stream. Tab-level detection requires a companion browser extension.
- **Multiple foreground sources** — when two foreground apps are active at once, both trigger ducking simultaneously. Priority rules between conflicting sources are planned for future version.

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
