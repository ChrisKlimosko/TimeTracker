# TimeTracker

A lightweight background productivity logger for Linux. Press a keyboard shortcut to mark the start of an activity — press it again to end it. Every session is automatically timestamped and the duration is calculated. Three log formats are written daily so you can review your time in a text editor, a spreadsheet, or any tool that reads JSON.

Designed specifically for **Wayland / GNOME** environments where most hotkey tools fail, using direct `/dev/input/` access via `evdev` to capture keys at the hardware level, below anything the desktop environment can intercept.

---

## Features

- Runs silently in the background
- Toggle-based hotkeys — one combo starts and stops each activity
- No root access required (just the `input` group)
- Three output formats written automatically each day:
  - `.txt` — human-readable running log
  - `.csv` — spreadsheet-ready summary
  - `.json` — structured data for any other use
- New log files created automatically each calendar day
- Handles multiple keyboard devices (including gaming keyboards with duplicate interfaces)
- Graceful shutdown with warnings for any sessions left open

---

## Requirements

- Linux with a Wayland or X11 desktop (tested on Kali / GNOME with Wayland)
- Python 3.10 or later
- `evdev` Python library

---

## Installation

### 1. Clone or copy the files

Place the following files into a folder, for example `~/time_tracker/`:

```
time_tracker.py
start_tracker.sh
stop_tracker.sh
time-tracker.service    ← optional, for auto-start on login
```

### 2. Make the scripts executable

```bash
chmod +x ~/time_tracker/start_tracker.sh
chmod +x ~/time_tracker/stop_tracker.sh
```

### 3. Install the evdev library

```bash
pip3 install evdev
```

### 4. Add your user to the `input` group

This allows the script to read from `/dev/input/` without root:

```bash
sudo usermod -a -G input $USER
```

**You must log out and log back in** for the group change to take effect. Verify it worked with:

```bash
groups
# should include "input" in the list
```

---

## Usage

### Starting and stopping

```bash
# Start the tracker in the background
~/time_tracker/start_tracker.sh

# Stop the tracker gracefully
~/time_tracker/stop_tracker.sh
```

The tracker runs silently in the background and survives closing the terminal. A PID file at `~/.time_tracker.pid` is used to track the running process.

### Hotkeys

All hotkeys use **Ctrl + Shift + Function key**. Each combo is a **toggle** — press once to start the session, press again to end it.

| Hotkey | Activity |
|---|---|
| `Ctrl + Shift + F1` | Phone Call |
| `Ctrl + Shift + F2` | Email |
| `Ctrl + Shift + F3` | Distraction |
| `Ctrl + Shift + F4` | Walk-in |
| `Ctrl + Shift + F5` | Meetings |
| `Ctrl + Shift + F6` | Projects |

---

## Log Files

Logs are written to `~/time_tracker_logs/` and are named with the current date. Three files are created each day:

### `Timetracker_YYYY-MM-DD.txt` — Human-readable log

```
====================================================
Time Tracker started : 2026-05-14 09:00:00
====================================================

[2026-05-14 09:03:11] Phone Call START
[2026-05-14 09:17:44] Phone Call END
  └─ Duration: 14m 33s

[2026-05-14 10:22:05] Distraction START
[2026-05-14 10:29:18] Distraction END
  └─ Duration: 7m 13s
```

### `Timetracker_YYYY-MM-DD.csv` — Spreadsheet log

One row per completed session, suitable for importing into Excel, LibreOffice Calc, or Google Sheets.

```
label,start,end,duration_seconds,duration
Phone Call,2026-05-14 09:03:11,2026-05-14 09:17:44,873,14m 33s
Distraction,2026-05-14 10:22:05,2026-05-14 10:29:18,433,7m 13s
```

### `Timetracker_YYYY-MM-DD.json` — Structured log

Full event array, rewritten after each completed session.

```json
[
  {
    "label": "Phone Call",
    "status": "completed",
    "start": "2026-05-14 09:03:11",
    "end": "2026-05-14 09:17:44",
    "duration_seconds": 873,
    "duration": "14m 33s"
  }
]
```

The CSV and JSON files are created at startup (with an empty/header-only state) so they always exist, even on days with no completed sessions.

---

## Auto-start on Login (Optional)

To have the tracker start automatically when you log in, use the included systemd user service.

```bash
# 1. Copy the service file
mkdir -p ~/.config/systemd/user
cp ~/time_tracker/time-tracker.service ~/.config/systemd/user/

# 2. Edit the ExecStart path to match where you put time_tracker.py
nano ~/.config/systemd/user/time-tracker.service

# 3. Enable and start the service
systemctl --user enable --now time-tracker.service

# Check its status
systemctl --user status time-tracker.service
```

To disable auto-start:

```bash
systemctl --user disable --now time-tracker.service
```

---

## Customising Hotkeys and Activities

Open `time_tracker.py` and find the `TOGGLE_MAP` near the top of the file:

```python
TOGGLE_MAP = {
    ecodes.KEY_F1: "Phone Call",
    ecodes.KEY_F2: "Distraction",
    ecodes.KEY_F3: "Email",
    ecodes.KEY_F4: "Walk-in",
}
```

Add, remove, or rename entries as needed. Any F-key (`KEY_F1` through `KEY_F12`) can be used. Save the file and restart the tracker for changes to take effect.

---

## File Overview

| File | Purpose |
|---|---|
| `time_tracker.py` | Main script |
| `start_tracker.sh` | Launches the tracker in the background |
| `stop_tracker.sh` | Stops the tracker gracefully, clears orphaned processes |
| `time-tracker.service` | Systemd unit for auto-start on login |
