# sim-avr

Simulate and debug ATmega328P (Arduino Nano) assembly projects with one command:
simavr-based simulator + avr-gdb (with an I/O register pane) + a Wokwi-style circuit viewer,
side by side in one browser page, all driven by the same simulation.

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
| `make run`  | browser page: tmux session (avr-gdb `layout avr` + simulator) beside the circuit viewer |
| `make run UI=terminal` | tmux session in this terminal, circuit viewer alone in the browser |
| `make sim`  | tmux in this terminal: avr-gdb + simulator, no viewer                |
| `make view` | simulator running freely + circuit viewer, no gdb (Ctrl-C to stop)   |
| `make clean`| delete build output                                                   |

To make the terminal version the default for a project, add `UI := terminal` to its Makefile.

Quitting gdb (or Ctrl-C in the terminal that ran `make run`) ends the whole session.
Ports: gdb 1234, viewer http://127.0.0.1:8000/, terminal (ttyd) 7681.

In the `make run` page:
- the terminal is the real tmux session, served by [ttyd](https://github.com/tsl0922/ttyd);
  `tmux attach -t avr-NAME` attaches to it from a normal terminal as well
- drag the bar between the halves to resize
- closing the tab asks first; the session keeps running and reopening the page reconnects
- **Fullscreen** (bottom right): in Chrome/Edge/Brave/Arc it also sends browser shortcuts such as
  Ctrl-W / Ctrl-T to the terminal instead of the browser (hold Esc to leave) - useful on Linux,
  where Ctrl-W would otherwise close the tab

## The circuit

`simulation/diagram.json` is a Wokwi diagram. Design the circuit on wokwi.com (or edit the file by
hand) and paste the diagram in. The viewer currently understands `wokwi-arduino-nano`,
`wokwi-led` and `wokwi-resistor`. `simulation/wokwi.toml` lets the official Wokwi VS Code extension
run the same project if you want it.

## How it works

`simulation/build/launcher` is a small C program built on
libsimavr that runs the firmware at real-time speed, serves gdb on :1234 and prints port changes;
`viewer/server.py` relays those to the browser, where `viewer.js` draws the circuit with Wokwi's
open-source part artwork (`@wokwi/elements`, MIT) and lights the LEDs.

`example/` is a test project for developing the kit (its `simulation` is a symlink).
