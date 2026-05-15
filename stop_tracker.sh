#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
#  stop_tracker.sh  –  Gracefully stop the Time Tracker
# ─────────────────────────────────────────────────────────

PIDFILE="$HOME/.time_tracker.pid"

if [ ! -f "$PIDFILE" ]; then
    echo "Time Tracker does not appear to be running (no PID file found)."
    exit 0
fi

PID=$(cat "$PIDFILE")

if ! kill -0 "$PID" 2>/dev/null; then
    echo "Process $PID is not running. Cleaning up stale PID file."
    rm -f "$PIDFILE"
    exit 0
fi

echo "Stopping Time Tracker (PID $PID)..."
kill -TERM "$PID"

# Wait up to 5 seconds for it to exit cleanly
for i in $(seq 1 10); do
    sleep 0.5
    if ! kill -0 "$PID" 2>/dev/null; then
        rm -f "$PIDFILE"
        echo "Time Tracker stopped."
        exit 0
    fi
done

# Force-kill if still alive
echo "Process did not exit cleanly — force-killing."
kill -KILL "$PID"
rm -f "$PIDFILE"
echo "Time Tracker force-stopped."
