# sim-avr

Simulate and debug ATmega328P (Arduino Nano) assembly projects with one command:
simavr-based simulator + avr-gdb (with an I/O register pane) + a Wokwi-style circuit viewer,
side by side in one browser page, all driven by the same simulation.

Works on macOS and Ubuntu (Windows: use WSL).

![make run: avr-gdb with the I/O register pane on the left, the circuit viewer on the right](docs/screenshot.png)

## Install: global

Clone the kit once, anywhere:

```sh
git clone https://github.com/ruzgara/sim-avr ~/sim-avr
```

## Install: per project

Without cloning, download the latest `simulation/` folder straight into the project:

```sh
curl -L https://github.com/ruzgara/sim-avr/archive/master.tar.gz \
  | tar -xz --strip-components=1 sim-avr-master/simulation
```

## New project

Each project gets its own copy of the `simulation/` folder:

```sh
mkdir myproject && cd myproject
cp -R ~/sim-avr/simulation .  # or the curl command above
./simulation/setup          # checks/installs dependencies, creates Makefile + myproject.s
make run
```

`setup` never overwrites existing files, so it can also be run in a folder that already has a `.s`.
To update a project's kit later, replace its `simulation/` folder with a newer copy (keep your
`simulation/diagram.json`).

## Usage

By default this tool opens a browser window with gdb debugging the program and the Wokwi-like viewer displaying it. You will need to know the basics of gdb.

[GDB Intro here](https://www.geeksforgeeks.org/c/gdb-step-by-step-introduction/)

> **Note:** the intro starts programs with `run` / `start`. Don't use those here: the program is
> already loaded in the simulator and paused at its first instruction, so just step or continue.

For those familiar with other debuggers, the basics are:

- `si [n]` or `stepi [n]` -> step into (n instructions)
- `ni [n]` or `nexti [n]` -> step over (n instructions)
- `c [n]` -> continue (if stopped at a breakpoint, stop at its nth hit, skipping the ones before)
- `fin` or `finish` -> step out (only works inside a routine entered with `rcall`)
- `b <label>` or `break <label>` -> set a breakpoint, e.g. `b delay`
- `p $r16` or `info registers r16` -> show a register
- `Ctrl-C` -> pause the program while it is running
- `q` or `quit` -> quit gdb (answer `y`), which ends the whole session
- press Enter to repeat the last command

An example debugging session could go like:

```
si
<enter-key>
<enter-key>
si 5
ni
si
<enter-key>
fin
c
c 2
<ctrl-c>
q
y
```

## Commands

| command     | what it does                                                         |
|-------------|----------------------------------------------------------------------|
| `make`      | assemble all `*.s` into `NAME.hex` (`.o`/`.elf` go to `simulation/build/`) |
| `make run`  | browser page: tmux session (avr-gdb `layout avr` + simulator) beside the circuit viewer |
| `make run UI=terminal` | tmux session in this terminal, circuit viewer alone in the browser |
| `make sim`  | tmux in this terminal: avr-gdb + simulator, no viewer                |
| `make view` | simulator running freely + circuit viewer, no gdb (Ctrl-C to stop)   |
| `make upload` | upload `NAME.hex` to a real Nano with avrdude (port found automatically) |
| `make clean`| delete build output                                                   |

To make the terminal version the default for a project, add `UI := terminal` to its Makefile.

Quitting gdb (or Ctrl-C in the terminal that ran `make run`) ends the whole session.
Ports: gdb 1234, viewer <http://127.0.0.1:8000/>, terminal (ttyd) 7681.

In the `make run` page:

- the terminal is the real tmux session, served by [ttyd](https://github.com/tsl0922/ttyd);
  `tmux attach -t avr-NAME` attaches to it from a normal terminal as well
- drag the bar between the halves to resize
- closing the tab asks first; the session keeps running and reopening the page reconnects
- **Fullscreen** (bottom right): in Chrome/Edge/Brave/Arc it also sends browser shortcuts such as
  Ctrl-W / Ctrl-T to the terminal instead of the browser (hold Esc to leave) - useful on Linux,
  where Ctrl-W would otherwise close the tab

## Uploading to a real Nano

`make upload` flashes exactly the `.hex` you simulated, using avrdude (`setup` offers to install it).

- The board's USB serial port is found automatically; if there are none or several, give it:
  `make upload PORT=/dev/cu.usbserial-1410` (macOS) or `PORT=/dev/ttyUSB0` (Linux).
- The default is the **old Nano bootloader** (57600 baud), which most clones have. For the new
  bootloader (115200 baud) use `make upload BOOTLOADER=new`, or put `BOOTLOADER := new` in the
  project Makefile. If avrdude can't sync with the board, it suggests the other one.
- Linux: if the port isn't accessible, add yourself to the `dialout` group.

## Existing projects

`setup` leaves an existing `Makefile` alone. If it doesn't include `simulation/avr.mk`, either rename
it (`mv Makefile Makefile.old`) and run `setup` again, or add `NAME := myproject` and
`include simulation/avr.mk` to it. Also note: every `.s` in the folder is linked into one program
(one program per folder), and sources are assembled with plain `avr-as` (use `.equ`, no `#include`).

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
