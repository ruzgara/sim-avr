# sim-avr

Simulate and debug ATmega328P (Arduino Nano) assembly projects with one command:
simavr-based simulator + avr-gdb (with an I/O register pane) + a Wokwi-style circuit viewer in
your browser, all driven by the same simulation.

Works on macOS and Ubuntu (Windows: use WSL).

## New project

```sh
mkdir myproject && cd myproject
cp -R ~/Developer/sim-avr/simulation .
./simulation/setup          # checks/installs dependencies, creates Makefile + myproject.s
make run
```

`setup` never overwrites existing files, so it can also be run in a folder that already has a `.s`.

## Commands

| command     | what it does                                                         |
|-------------|----------------------------------------------------------------------|
| `make`      | assemble all `*.s` into `NAME.hex` (`.o`/`.elf` go to `simulation/build/`) |
| `make run`  | tmux: avr-gdb (`layout avr`) + simulator, circuit viewer opens in the browser |
| `make sim`  | tmux: avr-gdb + simulator, no viewer                                 |
| `make view` | simulator running freely + circuit viewer, no gdb (Ctrl-C to stop)   |
| `make clean`| delete build output                                                   |

Quitting gdb ends the whole session. Ports: gdb 1234, viewer http://127.0.0.1:8000/.

## The circuit

`simulation/diagram.json` is a Wokwi diagram. Design the circuit on wokwi.com (or edit the file by
hand) and paste the diagram in. The viewer currently understands `wokwi-arduino-nano`,
`wokwi-led` and `wokwi-resistor`. `simulation/wokwi.toml` lets the official Wokwi VS Code extension
run the same project if you want it.

## How it works

See [DESIGN.md](DESIGN.md). In short: `simulation/build/launcher` is a small C program built on
libsimavr that runs the firmware at real-time speed, serves gdb on :1234 and prints port changes;
`viewer/server.py` relays those to the browser, where `viewer.js` draws the circuit with Wokwi's
open-source part artwork (`@wokwi/elements`, MIT) and lights the LEDs.

`example/` is a test project for developing the kit (its `simulation` is a symlink).
