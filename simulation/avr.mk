# simulation/avr.mk - build and simulation rules for an ATmega328P (Arduino Nano) assembly project.
#
# The project's Makefile only needs:
#     NAME := myproject
#     include simulation/avr.mk
#
#   make          assemble every .s in the project folder into NAME.hex
#   make run      browser page: avr-gdb + simulator (tmux via ttyd) beside the circuit viewer
#   make run UI=terminal
#                 avr-gdb + simulator (tmux) in this terminal, circuit viewer in the browser
#   make sim      simulator + avr-gdb (tmux) in this terminal, no viewer
#   make view     simulator running freely + circuit viewer, no gdb
#   make clean    delete build output

# Folder this file lives in (simulation/), without a trailing slash
SIM_DIR   := $(patsubst %/,%,$(dir $(lastword $(MAKEFILE_LIST))))
NAME      ?= $(notdir $(CURDIR))

MCU       := atmega328p
F_CPU     := 16000000
# Linker emulation for the ATmega328P family
ARCH      := avr5
GDB_PORT  := 1234
VIEW_PORT := 8000
# ttyd (the terminal shown in the browser by make run)
TERM_PORT := 7681

# Where make run shows the gdb session: browser (beside the circuit) or terminal (this terminal).
# Override per run (make run UI=terminal) or set it in the project Makefile.
UI        ?= browser

BUILD     := $(SIM_DIR)/build
SRCS      := $(wildcard *.s)
OBJS      := $(SRCS:%.s=$(BUILD)/%.o)
ELF       := $(BUILD)/$(NAME).elf
HEX       := $(NAME).hex
LAUNCHER  := $(BUILD)/launcher

AS      := avr-as
LD      := avr-ld
OBJCOPY := avr-objcopy
SIZE    := avr-size
CC      ?= cc
# avr-gdb: Homebrew osx-cross/avr/avr-gdb on macOS, the gdb-avr package on Ubuntu
# (Ubuntu's gdb-multiarch is built without AVR support)
GDB     := $(shell command -v avr-gdb 2>/dev/null)

ASFLAGS := -g -mmcu=$(MCU)
LDFLAGS := -m $(ARCH)

LAUNCHER_CFLAGS := -O2 -Wall -Wextra $(shell pkg-config --cflags simavr 2>/dev/null)
LAUNCHER_LIBS   := $(shell pkg-config --libs simavr libelf 2>/dev/null || pkg-config --libs simavr 2>/dev/null || echo -lsimavr -lelf)

.PHONY: all run sim view clean distclean launcher
all: $(HEX)

$(BUILD):
	@mkdir -p $@

$(BUILD)/%.o: %.s | $(BUILD)
	$(AS) $(ASFLAGS) -o $@ $<

$(ELF): $(OBJS)
	$(LD) $(LDFLAGS) -o $@ $^

$(HEX): $(ELF)
	$(OBJCOPY) -O ihex -R .eeprom $< $@
	@$(SIZE) $<

# The launcher is compiled per machine, so a project copied between Mac and Linux rebuilds it.
launcher: $(LAUNCHER)
$(LAUNCHER): $(SIM_DIR)/launcher/launcher.c | $(BUILD)
	$(CC) $(LAUNCHER_CFLAGS) $< $(LAUNCHER_LIBS) -o $@

# Everything run.sh needs, passed as environment variables
RUN_ENV := SIM_DIR="$(SIM_DIR)" NAME="$(NAME)" ELF="$(ELF)" LAUNCHER="$(LAUNCHER)" \
           GDB="$(GDB)" MCU="$(MCU)" F_CPU="$(F_CPU)" GDB_PORT="$(GDB_PORT)" VIEW_PORT="$(VIEW_PORT)" TERM_PORT="$(TERM_PORT)" UI="$(UI)"

run sim view: $(HEX) $(LAUNCHER)
	@$(RUN_ENV) "$(SIM_DIR)/run.sh" $@

clean:
	rm -f $(BUILD)/*.o $(BUILD)/*.elf $(HEX)

# Also removes the compiled launcher
distclean: clean
	rm -f $(LAUNCHER)

# Keep the .elf: it holds the debug info avr-gdb uses
.SECONDARY:
