# avr_io.py - a gdb TUI pane that always shows the ATmega328P I/O registers.
#
# Load it in avr-gdb with:   source avr_io.py
# then switch layout with:   layout avr
#
# The pane refreshes every time the program stops (break, stepi, nexti...).
# Edit REGISTERS below to add or remove rows.
#
# It also shows a stopwatch: simulated time and instruction counts, from the launcher's
# counters (file named by $AVR_STATS). Commands:
#   stopwatch          (or: sw)  print total time, the last lap and time since a reset
#   stopwatch reset    (or: sw reset)  start a new measurement from here

import os
import threading
import urllib.request

import gdb

DATA_BASE = 0x800000      # avr-gdb puts SRAM / I/O at this address offset
IO_START = 0x20           # first I/O register (data-space address)
IO_END = 0x60             # one past SREG

# (label, data-space address). Use None for a blank spacer line.
REGISTERS = [
    ("DDRB",  0x24), ("PORTB", 0x25), ("PINB",  0x23),
    None,
    ("DDRC",  0x27), ("PORTC", 0x28), ("PINC",  0x26),
    None,
    ("DDRD",  0x2A), ("PORTD", 0x2B), ("PIND",  0x29),
    None,
    ("TCCR0A", 0x44), ("TCCR0B", 0x45), ("TCNT0", 0x46),
    None,
    ("SPH",   0x5E), ("SPL",   0x5D),
]

# Port letter -> (DDR address, PORT address, Arduino Nano pin names per bit)
PORTS = {
    "B": (0x24, 0x25, ["D8", "D9", "D10", "D11", "D12", "D13", "XTAL", "XTAL"]),
    "C": (0x27, 0x28, ["A0", "A1", "A2", "A3", "A4", "A5", "RESET", "-"]),
    "D": (0x2A, 0x2B, ["D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7"]),
}

SREG_ADDR = 0x5F
SREG_FLAGS = "ITHSVNZC"   # bit 7 .. bit 0

BOLD = "\x1b[1m"
GREEN = "\x1b[32m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"


class Stopwatch:
    """Simulated time and instruction counts, read from the launcher's stats file.

    Time is cycles / clock frequency, so it is exact (62.5 ns per cycle at 16 MHz).
      total = since the program started
      lap   = between the last two times the program stopped (e.g. breakpoint to breakpoint)
      since reset = since the last `stopwatch reset`
    """

    RETRY_S = 0.02     # gdb can report a stop just before the launcher has written its totals
    RETRIES = 25

    def __init__(self):
        self.path = os.environ.get("AVR_STATS")
        # The circuit viewer's server (make run), which shows the lap in the browser too
        port = os.environ.get("AVR_VIEW_PORT")
        self.server = f"http://127.0.0.1:{port}" if port else None
        self.mark_path = os.environ.get("AVR_MARK")   # reset point, shared with the browser
        self.last_stop = None    # (cycles, instructions) at the previous stop you saw
        self.lap = None          # (cycles, instructions) between the last two stops
        self.listeners = []      # called after the counters change (the pane redraws)
        gdb.events.stop.connect(self._on_stop)

    def read(self):
        """(cycles, instructions, freq, state) or None if there is no data."""
        if not self.path:
            return None
        try:
            with open(self.path) as f:
                cycles, instr, freq, state = f.read().split()
            return int(cycles), int(instr), int(freq), state
        except (OSError, ValueError):
            return None

    def _on_stop(self, _event):
        # gdb.events.stop only fires for stops you see. gdb also stops the CPU internally
        # (e.g. to step over a breakpoint before continuing), which the launcher can't tell
        # apart, so the lap is worked out here.
        self._try_update(self.RETRIES)

    def _try_update(self, tries):
        r = self.read()
        if r is not None and r[3] != "stopped" and tries > 0:
            # Not written yet: look again shortly (on gdb's thread, via post_event)
            threading.Timer(self.RETRY_S, gdb.post_event,
                            [lambda: self._try_update(tries - 1)]).start()
            return
        if r is not None and r[3] == "stopped":
            now = (r[0], r[1])
            if self.last_stop is not None and now != self.last_stop:
                self.lap = (now[0] - self.last_stop[0], now[1] - self.last_stop[1])
                self._send_lap()
            self.last_stop = now
        self.notify()

    def _send_lap(self):
        self._post("/lap", self.lap)

    def _post(self, path, pair):
        """Tell the viewer's server (in the background; ignore failures, e.g. in make sim)."""
        if not self.server:
            return
        req = urllib.request.Request(self.server + path, data=f"{pair[0]} {pair[1]}".encode())

        def post():
            try:
                urllib.request.urlopen(req, timeout=1)
            except OSError:
                pass

        threading.Thread(target=post, daemon=True).start()

    def read_mark(self):
        """(cycles, instructions) of the last reset, from `sw reset` or the browser button."""
        if not self.mark_path:
            return None
        try:
            with open(self.mark_path) as f:
                cycles, instr = f.read().split()
            return int(cycles), int(instr)
        except (OSError, ValueError):
            return None

    def reset(self):
        r = self.read()
        if r is None:
            return
        mark = (r[0], r[1])
        if self.mark_path:
            with open(self.mark_path + ".tmp", "w") as f:
                f.write(f"{mark[0]} {mark[1]}\n")
            os.replace(self.mark_path + ".tmp", self.mark_path)
        self._post("/mark", mark)
        self.notify()

    def notify(self):
        for fn in list(self.listeners):
            fn()

    def lines(self, bold="", reset=""):
        """Text lines describing the counters."""
        r = self.read()
        if r is None:
            return [f"{'time':<6}n/a (no simulator counters)"]
        cycles, instr, freq, _ = r
        lap = self.lap

        # Two lines per measurement so it fits the narrow pane:
        #   time      246.725 ms
        #             3,947,601 cyc  2,636,872 ins
        def block(label, c, i):
            return [f"{bold}{label:<6}{reset}{c * 1000 / freq:>11.3f} ms",
                    f"{'':<6}{c:,} cyc  {i:,} ins"]

        out = block("time", cycles, instr)
        if lap is not None:
            out += block("lap", *lap)
        mark = self.read_mark()
        if mark is not None:
            out += block("reset", cycles - mark[0], instr - mark[1])
        return out


STOPWATCH = Stopwatch()


class StopwatchCommand(gdb.Command):
    """Show simulated time and instruction counts.

stopwatch          show total time, the last lap (between the last two stops)
                   and the time since the last reset
stopwatch reset    start a new measurement from here"""

    def __init__(self):
        super().__init__("stopwatch", gdb.COMMAND_STATUS)

    def invoke(self, arg, from_tty):
        if arg.strip() == "reset":
            STOPWATCH.reset()
            print("stopwatch reset")
        elif arg.strip():
            raise gdb.GdbError("usage: stopwatch [reset]")
        for line in STOPWATCH.lines():
            print(line)


StopwatchCommand()
gdb.execute("alias sw = stopwatch")


def read_io():
    """Return the I/O area (0x20..0x5F) as bytes, or None if not connected."""
    try:
        inferior = gdb.selected_inferior()
        if inferior.pid == 0:
            return None
        mem = inferior.read_memory(DATA_BASE + IO_START, IO_END - IO_START)
        return bytes(mem)
    except (gdb.error, gdb.MemoryError):
        return None


def render_text(io):
    """Build the pane's text from the raw I/O bytes."""
    if io is None:
        return f"{DIM}Not connected.\nRun: target remote :1234{RESET}"

    def reg(addr):
        return io[addr - IO_START]

    lines = STOPWATCH.lines(BOLD, RESET) + [""]
    lines.append(f"{BOLD}{'reg':<7}{'hex':>5}  {'76543210'}{RESET}")
    for entry in REGISTERS:
        if entry is None:
            lines.append("")
            continue
        name, addr = entry
        value = reg(addr)
        lines.append(f"{name:<7} 0x{value:02X}  {value:08b}")

    # SREG with flag letters: upper case = set, '.' = clear
    sreg = reg(SREG_ADDR)
    flags = "".join(
        SREG_FLAGS[i] if sreg & (0x80 >> i) else "." for i in range(8)
    )
    lines.append("")
    lines.append(f"{'SREG':<7} 0x{sreg:02X}  {flags}")

    # Pins currently driven high = output (DDR bit 1) AND high (PORT bit 1)
    high = []
    for letter, (ddr_addr, port_addr, names) in PORTS.items():
        driven = reg(ddr_addr) & reg(port_addr)
        for bit in range(8):
            if driven & (1 << bit):
                high.append(f"P{letter}{bit}({names[bit]})")
    lines.append("")
    lines.append(f"{BOLD}Pins driven HIGH:{RESET}")
    if high:
        lines.append(GREEN + " ".join(high) + RESET)
    else:
        lines.append(f"{DIM}none{RESET}")

    return "\n".join(lines)


class AvrIoWindow:
    def __init__(self, tui):
        self.tui = tui
        self.tui.title = "AVR I/O"
        gdb.events.stop.connect(self._on_event)
        gdb.events.memory_changed.connect(self._on_event)
        STOPWATCH.listeners.append(self.render)
        self.render()

    def close(self):
        gdb.events.stop.disconnect(self._on_event)
        gdb.events.memory_changed.disconnect(self._on_event)
        STOPWATCH.listeners.remove(self.render)

    def _on_event(self, _event):
        self.render()

    def render(self):
        if not self.tui.is_valid():
            return
        self.tui.erase()
        # Only write as many lines as fit: text past the bottom of the pane would otherwise
        # be squashed onto its last line
        lines = render_text(read_io()).split("\n")[: max(self.tui.height, 1)]
        self.tui.write("\n".join(lines))


gdb.register_window_type("avrio", AvrIoWindow)

# Layout: source on the left, I/O pane on the right, registers below, commands at the bottom.
gdb.execute("tui new-layout avr {-horizontal src 3 avrio 2} 3 regs 2 status 0 cmd 2")
