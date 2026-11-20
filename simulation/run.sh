#!/usr/bin/env bash
# simulation/run.sh - start a simulation session. Called by avr.mk; not meant to be run by hand.
#
#   run.sh run    browser page: tmux session (avr-gdb + simulator, via ttyd) beside the circuit viewer
#                 (UI=terminal: tmux in this terminal instead, circuit viewer alone in the browser)
#   run.sh sim    tmux in this terminal: avr-gdb (top) + simulator (bottom), no viewer
#   run.sh view   simulator running freely + circuit viewer in the browser, no gdb
#
# Settings come from avr.mk as environment variables.
set -euo pipefail

mode="${1:?usage: run.sh run|sim|view}"
: "${SIM_DIR:?}" "${NAME:?}" "${ELF:?}" "${LAUNCHER:?}" "${MCU:?}" "${F_CPU:?}" \
  "${GDB_PORT:?}" "${VIEW_PORT:?}" "${TERM_PORT:?}"
GDB="${GDB:-}"

SESSION="avr-$NAME"
VIEW_URL="http://127.0.0.1:$VIEW_PORT/"

# Quote an array into one string that a shell (inside tmux) will split back the same way
quote() { printf '%q ' "$@"; }

require() {
    if ! command -v "$1" >/dev/null; then
        echo "error: $1 not found - run ./simulation/setup" >&2
        exit 1
    fi
}

port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

check_port() {
    if port_in_use "$1"; then
        echo "error: port $1 is already in use - is another simulation still running?" >&2
        echo "       (tmux kill-session -t $SESSION   or close the other one)" >&2
        exit 1
    fi
}

# Open a URL once the viewer server answers (in the background so we don't block)
open_browser() {   # open_browser URL
    local url="$1"
    [[ -n "${NO_BROWSER:-}" ]] && return   # used by tests
    (
        for _ in $(seq 50); do port_in_use "$VIEW_PORT" && break; sleep 0.1; done
        if [[ "$(uname)" == Darwin ]]; then
            open "$url"
        elif command -v wslview >/dev/null; then
            wslview "$url"                       # WSL
        elif command -v xdg-open >/dev/null; then
            xdg-open "$url" >/dev/null 2>&1
        else
            echo "Open $url in your browser" >&2
        fi
    ) &
}

launcher_cmd() {   # launcher_cmd [extra launcher options...]
    quote "$LAUNCHER" --mcu "$MCU" --freq "$F_CPU" --gdb-port "$GDB_PORT" "$@" "$ELF"
}

server_cmd() {     # server_cmd [extra launcher options...]
    printf '%s' "$(quote python3 "$SIM_DIR/viewer/server.py" --port "$VIEW_PORT" \
        --diagram "$SIM_DIR/diagram.json" --)"
    launcher_cmd "$@"
}

gdb_cmd() {
    if [[ -z "$GDB" ]]; then
        echo "error: avr-gdb not found - run ./simulation/setup" >&2
        exit 1
    fi
    local args=("$GDB" -q -ex "source $SIM_DIR/gdb/avr_io.py" -x "$SIM_DIR/gdb/debug.gdb" "$ELF")
    quote "${args[@]}"
}

# Create the tmux session (detached): gdb on top with most of the space, simulator output below.
# Quitting gdb kills the whole session, which also stops the simulator.
new_session() {   # new_session "<bottom pane command>"
    local bottom="$1"
    require tmux
    tmux kill-session -t "$SESSION" 2>/dev/null || true
    tmux new-session -d -s "$SESSION" -x "$(tput cols)" -y "$(tput lines)" \
        "$bottom; read -r -p 'simulator exited - press enter'"
    # No status bar: it only shows the window name, host name and clock
    tmux set-option -t "$SESSION" status off
    # Give the gdb server a moment to start listening before gdb connects
    tmux split-window -t "$SESSION" -v -b -l 80% \
        "sleep 0.5; $(gdb_cmd); tmux kill-session -t $(quote "$SESSION")"
}

attach_here() {
    if [[ -n "${TMUX:-}" ]]; then
        tmux switch-client -t "$SESSION"
    else
        tmux attach-session -t "$SESSION"
    fi
}

# Serve the tmux session to the browser with ttyd and wait until it ends
attach_browser() {
    require ttyd
    # Only listen on this machine (loopback), and with -O refuse websocket connections that
    # come from another website's page: a terminal running gdb can run shell commands.
    local lo=lo
    [[ "$(uname)" == Darwin ]] && lo=lo0
    ttyd -W -O -i "$lo" -p "$TERM_PORT" \
        -t fontSize=14 -t disableResizeOverlay=true \
        -t 'theme={"background":"#1e1e1e"}' \
        tmux attach-session -t "$SESSION" >/dev/null 2>&1 &
    TTYD_PID=$!   # global: the exit trap runs after this function has returned
    # On Ctrl-C or exit: end the tmux session (stops gdb + simulator) and ttyd
    trap 'tmux kill-session -t "$SESSION" 2>/dev/null; kill "$TTYD_PID" 2>/dev/null' EXIT
    trap 'exit 0' INT TERM

    local url="${VIEW_URL}session.html?term=$TERM_PORT"
    open_browser "$url"
    echo "Session: $url"
    echo "To use it from this terminal too:  tmux attach -t $SESSION"
    echo "Quit gdb or press Ctrl-C here to stop."
    while tmux has-session -t "$SESSION" 2>/dev/null; do sleep 0.5; done
}

case "$mode" in
    run)
        case "${UI:-browser}" in
            browser)
                check_port "$GDB_PORT"; check_port "$VIEW_PORT"; check_port "$TERM_PORT"
                new_session "$(server_cmd --wait-gdb)"
                attach_browser
                ;;
            terminal)
                check_port "$GDB_PORT"; check_port "$VIEW_PORT"
                new_session "$(server_cmd --wait-gdb)"
                open_browser "$VIEW_URL"
                attach_here
                ;;
            *)
                echo "error: UI must be 'browser' or 'terminal' (got '$UI')" >&2
                exit 1
                ;;
        esac
        ;;
    sim)
        check_port "$GDB_PORT"
        new_session "$(launcher_cmd --wait-gdb)"
        attach_here
        ;;
    view)
        check_port "$VIEW_PORT"
        open_browser "$VIEW_URL"
        echo "Ctrl-C to stop"
        eval "exec $(server_cmd --no-gdb)"
        ;;
    *)
        echo "usage: run.sh run|sim|view" >&2
        exit 1
        ;;
esac
