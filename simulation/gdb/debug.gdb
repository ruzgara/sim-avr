# gdb startup commands, run by simulation/run.sh after avr_io.py is loaded and gdb is
# connected to the simulator (run.sh does `target remote :<GDB_PORT>`, so don't add it here).
layout avr
break *0
