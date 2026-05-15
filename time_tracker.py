#!/usr/bin/env python3
"""
Time Tracker - Background productivity logger (evdev edition)
Reads keyboard events directly from /dev/input/, bypassing Wayland/GNOME.

Requires the user to be in the 'input' group:
    sudo usermod -a -G input $USER   (then log out and back in)

Three log files are written on every event, all prefixed with CK_<date>:
  CK_YYYY-MM-DD.txt   human-readable log
  CK_YYYY-MM-DD.csv   spreadsheet-ready
  CK_YYYY-MM-DD.json  structured data

Run with --debug to print every raw key event.
"""

import csv
import json
import os
import selectors
import signal
import sys
import time
from datetime import datetime

try:
    import evdev
    from evdev import InputDevice, ecodes
except ImportError:
    print("ERROR: evdev not installed.  Run:  pip3 install evdev")
    sys.exit(1)

# ─── Configuration ────────────────────────────────────────────────────────────

LOG_DIR = os.path.expanduser("~/time_tracker_logs")
DEBUG   = "--debug" in sys.argv
os.makedirs(LOG_DIR, exist_ok=True)

CTRL_KEYS  = {ecodes.KEY_LEFTCTRL,  ecodes.KEY_RIGHTCTRL}
SHIFT_KEYS = {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT}

TOGGLE_MAP = {
    ecodes.KEY_F1: "Phone Call",
    ecodes.KEY_F2: "Email",
    ecodes.KEY_F3: "Distraction",
    ecodes.KEY_F4: "Walk-in",
    ecodes.KEY_F5: "Meeting",
    ecodes.KEY_F6: "Projects",
}

# Ignore duplicate fires from multiple device interfaces within this window
DEDUP_WINDOW = 0.15  # seconds

# ─── File paths ───────────────────────────────────────────────────────────────

def base_path() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(LOG_DIR, f"Timetracker_{today}")

def txt_path()  -> str: return base_path() + ".txt"
def csv_path()  -> str: return base_path() + ".csv"
def json_path() -> str: return base_path() + ".json"

# ─── Helpers ──────────────────────────────────────────────────────────────────

def fmt_time(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def fmt_duration(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs   = divmod(remainder, 60)
    parts = []
    if hours:            parts.append(f"{hours}h")
    if minutes or hours: parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)

def append_txt(text: str) -> None:
    with open(txt_path(), "a") as fh:
        fh.write(text + "\n")
    print(text, flush=True)

# ─── Session state ────────────────────────────────────────────────────────────

active_sessions:  dict[str, datetime] = {}
completed_events: list[dict]          = []
last_fired:       dict[str, float]    = {}   # label -> monotonic time of last toggle


def load_existing_events() -> None:
    """On startup, reload today's completed events so CSV/JSON stay consistent."""
    path = json_path()
    if os.path.exists(path):
        try:
            with open(path) as fh:
                for entry in json.load(fh):
                    if entry.get("status") == "completed":
                        completed_events.append(entry)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass


def toggle_event(label: str) -> None:
    """
    Start the session if none is open; end it if one is running.
    A short dedup window prevents the same physical keypress from firing
    twice when the keyboard exposes multiple /dev/input interfaces.
    """
    now_mono = time.monotonic()
    if now_mono - last_fired.get(label, 0) < DEDUP_WINDOW:
        if DEBUG:
            print(f"  [dedup] {label} ignored (duplicate within {DEDUP_WINDOW}s)", flush=True)
        return
    last_fired[label] = now_mono

    now = datetime.now()

    if label not in active_sessions:
        active_sessions[label] = now
        append_txt(f"[{fmt_time(now)}] {label} START")
    else:
        start_dt     = active_sessions.pop(label)
        elapsed      = int((now - start_dt).total_seconds())
        duration_str = fmt_duration(elapsed)

        append_txt(f"[{fmt_time(now)}] {label} END")
        append_txt(f"  └─ Duration: {duration_str}")
        append_txt("")

        event = {
            "label":            label,
            "status":           "completed",
            "start":            fmt_time(start_dt),
            "end":              fmt_time(now),
            "duration_seconds": elapsed,
            "duration":         duration_str,
        }
        completed_events.append(event)
        write_csv(event)
        write_json()

# ─── Output writers ───────────────────────────────────────────────────────────

CSV_FIELDS = ["label", "start", "end", "duration_seconds", "duration"]

def write_csv(event: dict) -> None:
    path         = csv_path()
    write_header = not os.path.exists(path)
    with open(path, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(event)

def write_json() -> None:
    with open(json_path(), "w") as fh:
        json.dump(completed_events, fh, indent=2)

# ─── Device discovery ─────────────────────────────────────────────────────────

def find_keyboards() -> list[InputDevice]:
    """
    Return ALL event interfaces that look like full keyboards.
    We deliberately keep every interface (even duplicates from the same
    physical keyboard) and rely on DEDUP_WINDOW in toggle_event to ignore
    the second fire that arrives within 150 ms from a second interface.
    """
    keyboards = []
    for path in evdev.list_devices():
        try:
            dev  = InputDevice(path)
            caps = dev.capabilities()
            keys = caps.get(ecodes.EV_KEY, [])
            has_fkey = any(k in keys for k in (ecodes.KEY_F1, ecodes.KEY_F2))
            has_mod  = any(k in keys for k in
                           (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL))
            if has_fkey and has_mod:
                keyboards.append(dev)
        except (PermissionError, OSError):
            pass
    return keyboards

# ─── Main event loop ──────────────────────────────────────────────────────────

def event_loop(keyboards: list[InputDevice]) -> None:
    """
    Use selectors.DefaultSelector (epoll on Linux) to wait on all
    keyboard devices simultaneously in a single thread.
    """
    ctrl_held  = False
    shift_held = False

    sel = selectors.DefaultSelector()
    for dev in keyboards:
        sel.register(dev, selectors.EVENT_READ)

    while True:
        try:
            ready = sel.select(timeout=2.0)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            print(f"selector error: {e}", flush=True)
            break

        for key, _ in ready:
            device: InputDevice = key.fileobj  # type: ignore[assignment]
            try:
                for event in device.read():
                    if event.type != ecodes.EV_KEY:
                        continue

                    code  = event.code
                    value = event.value  # 1=down, 0=up, 2=repeat

                    if DEBUG:
                        name  = ecodes.KEY.get(code, f"code={code}")
                        state = {0: "up", 1: "down", 2: "repeat"}.get(value, value)
                        print(f"  [{device.name}] {name} {state}  "
                              f"ctrl={ctrl_held} shift={shift_held}", flush=True)

                    if code in CTRL_KEYS:
                        ctrl_held = (value != 0)
                        continue
                    if code in SHIFT_KEYS:
                        shift_held = (value != 0)
                        continue

                    if value == 1 and ctrl_held and shift_held:
                        if code in TOGGLE_MAP:
                            toggle_event(TOGGLE_MAP[code])

            except (OSError, IOError) as e:
                print(f"Read error on {device.path}: {e} — removing.", flush=True)
                sel.unregister(device)

        if not sel.get_map():
            print("All keyboard devices lost. Exiting.", flush=True)
            sys.exit(1)

# ─── Startup / shutdown ───────────────────────────────────────────────────────

def write_banner(event: str) -> None:
    sep = "=" * 52
    if event == "start":
        lines = [
            "",
            sep,
            f"Time Tracker started : {fmt_time(datetime.now())}",
            f"Log directory        : {LOG_DIR}",
            sep,
            "Hotkeys  (Ctrl + Shift + function key — toggles start/end)",
            "  Ctrl+Shift+F1  ->  Phone Call",
            "  Ctrl+Shift+F2  ->  Email",
            "  Ctrl+Shift+F3  ->  Distraction",
            "  Ctrl+Shift+F4  ->  Walk-in",
            "  Ctrl+Shift+F5  ->  Meeting",
            "  Ctrl+Shift+F6  ->  Projects",
            sep,
        ]
    else:
        lines = [
            "",
            f"Time Tracker stopped : {fmt_time(datetime.now())}",
            sep,
            "",
        ]
    append_txt("\n".join(lines))


def shutdown(signum, frame) -> None:
    for label, start_dt in active_sessions.items():
        append_txt(f"  WARNING: '{label}' still open at shutdown "
                   f"(started {fmt_time(start_dt)})")
    write_banner("stop")
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT,  shutdown)

    load_existing_events()
    write_banner("start")

    keyboards = find_keyboards()
    if not keyboards:
        print()
        print("ERROR: No keyboard devices found or permission denied.")
        print("Fix:   sudo usermod -a -G input $USER   then log out and back in.")
        sys.exit(1)

    print(f"Monitoring {len(keyboards)} device(s):", flush=True)
    for kb in keyboards:
        print(f"  {kb.path}  [{kb.name}]", flush=True)

    if DEBUG:
        print("\n-- DEBUG MODE: printing all key events --\n", flush=True)

    event_loop(keyboards)


if __name__ == "__main__":
    main()
