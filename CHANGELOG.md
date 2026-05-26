# Changelog

All notable changes to AudioDuck will be documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.1.0] — 2026-05-25

### Added
- PipeWire-native backend via `pw-dump` — sees all audio nodes including apps
  that bypass the PulseAudio compatibility layer (e.g. Spotify via spotify-launcher)
- Client → Node join logic to resolve `application.name` from PipeWire graph
- Proportional ducking: background volume reduced to `pre_duck_volume × DUCK_RATIO`
  rather than an absolute target level
- Pre-duck volume snapshot — restores to exactly what the user had, not a hardcoded value
- Smooth fade transitions (configurable steps and delay)
- Explicit allow-list classification: unknown apps are ignored entirely,
  preventing spurious ducking from background system processes
- `--dry-run` flag for safe testing without touching volumes
- `--duck-ratio` and `--poll` CLI flags
- systemd user service (`audio-duck.service`)
- Fish shell helper function (`audioduck start/stop/restart/status/log`)

### Fixed
- Volume drift bug: restore fade now starts from the exact ducked-to value
  stored in state, never from `si.volume` which may be caught mid-fade by the poller
- Snapshot corruption bug: pre-duck snapshot is only updated when current volume
  is meaningfully above the duck target, preventing compounding volume decay
  across rapid duck/unduck cycles
- `value_percent` parsing: handles PipeWire's `"40 %"` format with space before `%`
