#!/usr/bin/env bash
# simulation/upload.sh - upload the .hex to a real Arduino Nano with avrdude. Called by `make upload`.
#
# Settings come from avr.mk as environment variables:
#   HEX    the file to upload
#   MCU    avrdude part name (atmega328p)
#   PORT   serial port; found automatically when empty
#   BAUD   bootloader speed: 57600 for the old Nano bootloader, 115200 for the new one
set -euo pipefail

: "${HEX:?}" "${MCU:?}" "${BAUD:?}"
PORT="${PORT:-}"

if ! command -v avrdude >/dev/null; then
    echo "error: avrdude not found - run ./simulation/setup (or install it: brew/apt install avrdude)" >&2
    exit 1
fi

# Find the board's USB serial port if none was given
if [[ -z "$PORT" ]]; then
    shopt -s nullglob
    if [[ "$(uname)" == Darwin ]]; then
        # FTDI / CH340 (most Nano clones) / boards with native USB
        candidates=(/dev/cu.usbserial* /dev/cu.wchusbserial* /dev/cu.usbmodem*)
    else
        candidates=(/dev/ttyUSB* /dev/ttyACM*)
    fi
    shopt -u nullglob

    if ((${#candidates[@]} == 0)); then
        echo "error: no Arduino found on a USB serial port." >&2
        echo "       Plug the Nano in, or give the port: make upload PORT=/dev/..." >&2
        exit 1
    fi
    if ((${#candidates[@]} > 1)); then
        echo "error: more than one possible port, pick one with PORT=:" >&2
        printf '         make upload PORT=%s\n' "${candidates[@]}" >&2
        exit 1
    fi
    PORT="${candidates[0]}"
fi

if [[ ! -e "$PORT" ]]; then
    echo "error: $PORT does not exist - is the Nano plugged in?" >&2
    exit 1
fi

echo "Uploading $HEX to $PORT at $BAUD baud"
cmd=(avrdude -p "$MCU" -c arduino -P "$PORT" -b "$BAUD" -D -U "flash:w:$HEX:i")
echo "${cmd[*]}"

if ! "${cmd[@]}"; then
    echo >&2
    if [[ ! -r "$PORT" || ! -w "$PORT" ]]; then
        echo "hint: no permission to use $PORT. On Linux add yourself to the dialout group:" >&2
        echo "      sudo usermod -aG dialout \$USER   (then log out and back in)" >&2
    elif [[ "$BAUD" == 57600 ]]; then
        echo "hint: if avrdude could not sync with the board, it may have the new bootloader:" >&2
        echo "      make upload BOOTLOADER=new" >&2
    else
        echo "hint: if avrdude could not sync with the board, it may have the old bootloader:" >&2
        echo "      make upload BOOTLOADER=old" >&2
    fi
    exit 1
fi
