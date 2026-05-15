#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
#  start_tracker.sh  –  Launch Time Tracker in the background
# ─────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRACKER="$SCRIPT_DIR/time_tracker.py"
PIDFILE="$HOME/.time_tracker.pid"
LOGDIR="$HOME/time_tracker_logs"

# ── Sanity checks ─────────────────────────────────────────
if [ ! -f "$TRACKER" ]; then
    echo "ERROR: time_tracker.py not found at $TRACKER"
    exit 1
fi

if ! python3 -c "import pynput" 2>/dev/null; then
    echo "ERROR: pynput is not installed."
    echo "Install it with:  pip3 install pynput"
    exit 1
fi

# ── Already running? ──────────────────────────────────────
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Time Tracker is already running (PID $OLD_PID)."
        echo "Use stop_tracker.sh to stop it first."
        exit 1
    else
        echo "Stale PID file found — cleaning up."
        rm -f "$PIDFILE"
    fi
fi

# ── Launch ────────────────────────────────────────────────
mkdir -p "$LOGDIR"

# Redirect stdout/stderr to a runtime log so nothing is lost
RUNTIME_LOG="$LOGDIR/tracker_runtime.log"

nohup python3 -u "$TRACKER" >> "$RUNTIME_LOG" 2>&1 &
TRACKER_PID=$!

echo "$TRACKER_PID" > "$PIDFILE"
echo "Time Tracker started (PID $TRACKER_PID)."
echo "Logs → $LOGDIR"
echo "       Runtime log → $RUNTIME_LOG"
echo "Stop with:  ./stop_tracker.sh"
