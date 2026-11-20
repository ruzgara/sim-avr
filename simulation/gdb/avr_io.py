# avr_io.py - a gdb TUI pane that always shows the ATmega328P I/O registers.
#
# Load it in avr-gdb with:   source avr_io.py
# then switch layout with:   layout avr
#
# The pane refreshes every time the program stops (break, stepi, nexti...).
# Edit REGISTERS below to add or remove rows.

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

    lines = [f"{BOLD}{'reg':<7}{'hex':>5}  {'76543210'}{RESET}"]
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
        self.render()

    def close(self):
        gdb.events.stop.disconnect(self._on_event)
        gdb.events.memory_changed.disconnect(self._on_event)

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
