#!/usr/bin/env bash
# simulation/run.sh - start a simulation session. Called by avr.mk; not meant to be run by hand.
#
#   run.sh run    tmux: avr-gdb (top) + viewer server and launcher (bottom), browser opens the viewer
#   run.sh sim    tmux: avr-gdb (top) + launcher (bottom), no viewer
#   run.sh view   viewer server + launcher running freely in this terminal, browser opens the viewer
#
# Settings come from avr.mk as environment variables.
set -euo pipefail

mode="${1:?usage: run.sh run|sim|view}"
: "${SIM_DIR:?}" "${NAME:?}" "${ELF:?}" "${LAUNCHER:?}" "${MCU:?}" "${F_CPU:?}" "${GDB_PORT:?}" "${VIEW_PORT:?}"
GDB="${GDB:-}"

SESSION="avr-$NAME"
VIEW_URL="http://127.0.0.1:$VIEW_PORT/"

# Quote an array into one string that a shell (inside tmux) will split back the same way
quote() { printf '%q ' "$@"; }

port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

check_port() {
    if port_in_use "$1"; then
        echo "error: port $1 is already in use - is another simulation still running?" >&2
        echo "       (tmux kill-session -t $SESSION   or close the other one)" >&2
        exit 1
    fi
}

# Open the viewer once the server answers (in the background so we don't block)
open_browser() {
    [[ -n "${NO_BROWSER:-}" ]] && return   # used by tests
    (
        for _ in $(seq 50); do port_in_use "$VIEW_PORT" && break; sleep 0.1; done
        if [[ "$(uname)" == Darwin ]]; then
            open "$VIEW_URL"
        elif command -v wslview >/dev/null; then
            wslview "$VIEW_URL"                       # WSL
        elif command -v xdg-open >/dev/null; then
            xdg-open "$VIEW_URL" >/dev/null 2>&1
        else
            echo "Open $VIEW_URL in your browser" >&2
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
        echo "error: no avr-gdb or gdb-multiarch found - run ./simulation/setup" >&2
        exit 1
    fi
    local args=("$GDB" -q)
    # gdb-multiarch needs to be told the target architecture before connecting
    [[ "$(basename "$GDB")" == gdb-multiarch ]] && args+=(-ex "set architecture avr")
    args+=(-ex "source $SIM_DIR/gdb/avr_io.py" -x "$SIM_DIR/gdb/debug.gdb" "$ELF")
    quote "${args[@]}"
}

# Two panes: gdb on top (most of the space), simulator output below.
# Quitting gdb kills the whole session, which also stops the simulator.
start_tmux() {   # start_tmux "<bottom pane command>"
    local bottom="$1"
    if ! command -v tmux >/dev/null; then
        echo "error: tmux not found - run ./simulation/setup" >&2
        exit 1
    fi
    tmux kill-session -t "$SESSION" 2>/dev/null || true
    tmux new-session -d -s "$SESSION" -x "$(tput cols)" -y "$(tput lines)" "$bottom; read -r -p 'simulator exited - press enter'"
    # Give the gdb server a moment to start listening before gdb connects
    tmux split-window -t "$SESSION" -v -b -l 80% \
        "sleep 0.5; $(gdb_cmd); tmux kill-session -t $(quote "$SESSION")"
    if [[ -n "${TMUX:-}" ]]; then
        tmux switch-client -t "$SESSION"
    else
        tmux attach-session -t "$SESSION"
    fi
}

case "$mode" in
    run)
        check_port "$GDB_PORT"; check_port "$VIEW_PORT"
        open_browser
        start_tmux "$(server_cmd --wait-gdb)"
        ;;
    sim)
        check_port "$GDB_PORT"
        start_tmux "$(launcher_cmd --wait-gdb)"
        ;;
    view)
        check_port "$VIEW_PORT"
        open_browser
        echo "Ctrl-C to stop"
        eval "exec $(server_cmd --no-gdb)"
        ;;
    *)
        echo "usage: run.sh run|sim|view" >&2
        exit 1
        ;;
esac
