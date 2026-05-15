#!/usr/bin/env python3
"""
Time Tracker - Background productivity logger with floating overlay
Reads keyboard events directly from /dev/input/, bypassing Wayland/GNOME.

Requires the user to be in the 'input' group:
    sudo usermod -a -G input $USER   (then log out and back in)

Hotkeys (each key TOGGLES start -> end):
  Ctrl+Shift+F1 = Phone Call
  Ctrl+Shift+F2 = Distraction
  Ctrl+Shift+F3 = Email
  Ctrl+Shift+F4 = Walk-in

Three log files written daily to ~/time_tracker_logs/:
  CK_YYYY-MM-DD.txt   human-readable log
  CK_YYYY-MM-DD.csv   spreadsheet-ready
  CK_YYYY-MM-DD.json  structured data

Run with --debug to print every raw key event.
Run with --no-gui to run headless (no overlay window).
"""

import csv
import json
import os
import selectors
import signal
import sys
import threading
import time
from datetime import datetime

try:
    import evdev
    from evdev import InputDevice, ecodes
except ImportError:
    print("ERROR: evdev not installed.  Run:  pip3 install evdev")
    sys.exit(1)

NO_GUI = "--no-gui" in sys.argv
DEBUG  = "--debug"  in sys.argv

if not NO_GUI:
    import tkinter as tk

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

LABELS = list(TOGGLE_MAP.values())   # ordered list for the overlay

DEDUP_WINDOW = 0.15   # seconds — suppresses duplicate fires from multi-interface keyboards

# ─── Colours ──────────────────────────────────────────────────────────────────

BG          = "#1a1a2e"   # deep navy
ROW_ODD     = "#16213e"
ROW_EVEN    = "#0f3460"
TEXT_ACTIVE = "#e2e8f0"
TEXT_IDLE   = "#64748b"
ACTIVE_DOT  = "#22c55e"   # green
IDLE_DOT    = "#334155"   # dark slate
PULSE_1     = "#4ade80"   # lighter green for pulse animation
TITLE_COL   = "#38bdf8"   # sky blue
BORDER_COL  = "#0f3460"

# ─── File paths ───────────────────────────────────────────────────────────────

def base_path() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(LOG_DIR, f"CK_{today}")

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

def fmt_elapsed(start_dt: datetime) -> str:
    return fmt_duration(int((datetime.now() - start_dt).total_seconds()))

def append_txt(text: str) -> None:
    with open(txt_path(), "a") as fh:
        fh.write(text + "\n")
    print(text, flush=True)

# ─── Shared state (guarded by state_lock) ─────────────────────────────────────

state_lock        = threading.Lock()
active_sessions:  dict[str, datetime] = {}
completed_events: list[dict]          = []
last_fired:       dict[str, float]    = {}

# ─── Session logic ────────────────────────────────────────────────────────────

def load_existing_events() -> None:
    path = json_path()
    if os.path.exists(path):
        try:
            with open(path) as fh:
                for entry in json.load(fh):
                    if entry.get("status") == "completed":
                        completed_events.append(entry)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

def init_output_files() -> None:
    if not os.path.exists(json_path()):
        write_json()
    if not os.path.exists(csv_path()):
        with open(csv_path(), "w", newline="") as fh:
            csv.DictWriter(fh, fieldnames=CSV_FIELDS).writeheader()

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

def toggle_event(label: str) -> None:
    now_mono = time.monotonic()
    with state_lock:
        if now_mono - last_fired.get(label, 0) < DEDUP_WINDOW:
            if DEBUG:
                print(f"  [dedup] {label} ignored", flush=True)
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

# ─── Overlay GUI ──────────────────────────────────────────────────────────────

class TrackerOverlay:
    POLL_MS     = 500    # how often the GUI polls for state changes
    PULSE_MS    = 600    # dot pulse speed when active
    DOT_SIZE    = 12     # diameter of status dot
    ROW_H       = 38
    PAD_X       = 14
    PAD_Y       = 8
    HEADER_H    = 32

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._drag_x = 0
        self._drag_y = 0
        self._pulse_state: dict[str, bool] = {lbl: False for lbl in LABELS}

        self._build_window()
        self._build_ui()
        self._poll()
        self._pulse()

    # ── Window setup ──────────────────────────────────────────────────────────

    def _build_window(self) -> None:
        root = self.root
        root.title("Time Tracker")
        root.overrideredirect(True)          # no title bar
        root.attributes("-topmost", True)    # always on top
        root.attributes("-alpha", 0.92)      # slight transparency
        root.configure(bg=BG)
        root.resizable(False, False)

        # Position top-right corner with some margin
        root.update_idletasks()
        sw = root.winfo_screenwidth()
        root.geometry(f"+{sw - 240}+40")

        # Drag support
        root.bind("<ButtonPress-1>",   self._drag_start)
        root.bind("<B1-Motion>",       self._drag_move)

    def _drag_start(self, e: tk.Event) -> None:
        self._drag_x = e.x_root - self.root.winfo_x()
        self._drag_y = e.y_root - self.root.winfo_y()

    def _drag_move(self, e: tk.Event) -> None:
        self.root.geometry(f"+{e.x_root - self._drag_x}+{e.y_root - self._drag_y}")

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        W = 220

        # ── Header bar ──
        header = tk.Frame(self.root, bg=ROW_EVEN, height=self.HEADER_H)
        header.pack(fill="x")
        header.bind("<ButtonPress-1>", self._drag_start)
        header.bind("<B1-Motion>",     self._drag_move)

        tk.Label(
            header, text="◈  TIME TRACKER", bg=ROW_EVEN, fg=TITLE_COL,
            font=("Courier", 9, "bold"), pady=7,
        ).pack(side="left", padx=self.PAD_X)

        close_btn = tk.Label(
            header, text="✕", bg=ROW_EVEN, fg=TEXT_IDLE,
            font=("Courier", 10), cursor="hand2",
        )
        close_btn.pack(side="right", padx=8)
        close_btn.bind("<Button-1>", lambda _: self.root.destroy())
        close_btn.bind("<Enter>",    lambda _: close_btn.config(fg="#ef4444"))
        close_btn.bind("<Leave>",    lambda _: close_btn.config(fg=TEXT_IDLE))

        # Thin accent line under header
        tk.Frame(self.root, bg=TITLE_COL, height=1).pack(fill="x")

        # ── Event rows ──
        self._dot_canvas: dict[str, tk.Canvas]  = {}
        self._label_var:  dict[str, tk.StringVar] = {}
        self._time_var:   dict[str, tk.StringVar] = {}
        self._rows:       dict[str, tk.Frame]    = {}

        for i, label in enumerate(LABELS):
            bg = ROW_ODD if i % 2 == 0 else BG

            row = tk.Frame(self.root, bg=bg, height=self.ROW_H)
            row.pack(fill="x")
            row.pack_propagate(False)
            row.bind("<ButtonPress-1>", self._drag_start)
            row.bind("<B1-Motion>",     self._drag_move)
            self._rows[label] = row

            # Status dot (canvas so we can redraw colour)
            canvas = tk.Canvas(
                row, width=self.DOT_SIZE, height=self.DOT_SIZE,
                bg=bg, highlightthickness=0,
            )
            canvas.pack(side="left", padx=(self.PAD_X, 6), pady=0, anchor="center")
            canvas.create_oval(
                0, 0, self.DOT_SIZE, self.DOT_SIZE,
                fill=IDLE_DOT, outline="", tags="dot",
            )
            self._dot_canvas[label] = canvas

            # Activity name
            lv = tk.StringVar(value=label)
            self._label_var[label] = lv
            tk.Label(
                row, textvariable=lv, bg=bg, fg=TEXT_IDLE,
                font=("Courier", 9), anchor="w",
            ).pack(side="left", fill="x", expand=True)

            # Elapsed timer (right-aligned)
            tv = tk.StringVar(value="")
            self._time_var[label] = tv
            tk.Label(
                row, textvariable=tv, bg=bg, fg=ACTIVE_DOT,
                font=("Courier", 8), width=7, anchor="e",
            ).pack(side="right", padx=self.PAD_X)

        # ── Footer ──
        tk.Frame(self.root, bg=TITLE_COL, height=1).pack(fill="x")
        footer = tk.Frame(self.root, bg=ROW_EVEN)
        footer.pack(fill="x")
        footer.bind("<ButtonPress-1>", self._drag_start)
        footer.bind("<B1-Motion>",     self._drag_move)
        tk.Label(
            footer, text="Ctrl+Shift+F1–F4", bg=ROW_EVEN, fg=TEXT_IDLE,
            font=("Courier", 7), pady=5,
        ).pack()

    # ── Polling & animation ───────────────────────────────────────────────────

    def _poll(self) -> None:
        """Refresh row states from shared session data every POLL_MS ms."""
        with state_lock:
            snapshot = dict(active_sessions)   # label -> start datetime

        for label in LABELS:
            active = label in snapshot
            bg     = ROW_ODD if LABELS.index(label) % 2 == 0 else BG
            lv     = self._label_var[label]
            tv     = self._time_var[label]
            row    = self._rows[label]

            lv.set(label)

            # Update elapsed timer
            if active:
                tv.set(fmt_elapsed(snapshot[label]))
            else:
                tv.set("")

            # Update text colour
            for widget in row.winfo_children():
                try:
                    widget.config(fg=TEXT_ACTIVE if active else TEXT_IDLE)
                except tk.TclError:
                    pass

        self.root.after(self.POLL_MS, self._poll)

    def _pulse(self) -> None:
        """Animate the status dots — pulse green when active, dim when idle."""
        with state_lock:
            snapshot = set(active_sessions.keys())

        for label in LABELS:
            canvas = self._dot_canvas[label]
            active = label in snapshot

            if active:
                # Alternate between two greens for a breathing effect
                self._pulse_state[label] = not self._pulse_state[label]
                colour = PULSE_1 if self._pulse_state[label] else ACTIVE_DOT
            else:
                self._pulse_state[label] = False
                colour = IDLE_DOT

            canvas.itemconfig("dot", fill=colour)

        self.root.after(self.PULSE_MS, self._pulse)


# ─── evdev keyboard thread ────────────────────────────────────────────────────

def find_keyboards() -> list[InputDevice]:
    keyboards = []
    for path in evdev.list_devices():
        try:
            dev  = InputDevice(path)
            caps = dev.capabilities()
            keys = caps.get(ecodes.EV_KEY, [])
            has_fkey = any(k in keys for k in (ecodes.KEY_F1, ecodes.KEY_F2))
            has_mod  = any(k in keys for k in (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL))
            if has_fkey and has_mod:
                keyboards.append(dev)
        except (PermissionError, OSError):
            pass
    return keyboards

def evdev_loop(keyboards: list[InputDevice]) -> None:
    ctrl_held  = False
    shift_held = False
    sel = selectors.DefaultSelector()
    for dev in keyboards:
        sel.register(dev, selectors.EVENT_READ)

    while True:
        try:
            ready = sel.select(timeout=2.0)
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
                    value = event.value

                    if DEBUG:
                        name  = ecodes.KEY.get(code, f"code={code}")
                        state = {0:"up", 1:"down", 2:"repeat"}.get(value, value)
                        print(f"  [{device.name}] {name} {state}  "
                              f"ctrl={ctrl_held} shift={shift_held}", flush=True)

                    if code in CTRL_KEYS:
                        ctrl_held = (value != 0); continue
                    if code in SHIFT_KEYS:
                        shift_held = (value != 0); continue

                    if value == 1 and ctrl_held and shift_held:
                        if code in TOGGLE_MAP:
                            toggle_event(TOGGLE_MAP[code])

            except (OSError, IOError) as e:
                print(f"Read error on {device.path}: {e}", flush=True)
                sel.unregister(device)

        if not sel.get_map():
            print("All keyboard devices lost.", flush=True)
            break

# ─── Startup / shutdown ───────────────────────────────────────────────────────

def write_banner(event: str) -> None:
    sep = "=" * 52
    if event == "start":
        lines = [
            "", sep,
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
        lines = ["", f"Time Tracker stopped : {fmt_time(datetime.now())}", sep, ""]
    append_txt("\n".join(lines))

def shutdown(signum, frame) -> None:
    with state_lock:
        for label, start_dt in active_sessions.items():
            append_txt(f"  WARNING: '{label}' still open at shutdown "
                       f"(started {fmt_time(start_dt)})")
    write_banner("stop")
    sys.exit(0)

def main() -> None:
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT,  shutdown)

    load_existing_events()
    init_output_files()
    write_banner("start")

    keyboards = find_keyboards()
    if not keyboards:
        print("\nERROR: No keyboard devices found or permission denied.")
        print("Fix:   sudo usermod -a -G input $USER   then log out and back in.")
        sys.exit(1)

    print(f"Monitoring {len(keyboards)} device(s):", flush=True)
    for kb in keyboards:
        print(f"  {kb.path}  [{kb.name}]", flush=True)

    # Start evdev loop in a daemon thread so it dies when the GUI closes
    t = threading.Thread(target=evdev_loop, args=(keyboards,), daemon=True)
    t.start()

    if NO_GUI:
        print("Running headless (--no-gui). Press Ctrl+C to stop.", flush=True)
        t.join()
    else:
        root = tk.Tk()
        TrackerOverlay(root)
        root.mainloop()
        # GUI closed — flush any open sessions and exit cleanly
        shutdown(None, None)

if __name__ == "__main__":
    main()
