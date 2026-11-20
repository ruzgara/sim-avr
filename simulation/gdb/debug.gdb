# gdb startup commands, run by simulation/run.sh after avr_io.py has been loaded.
target remote :1234
layout avr
break *0
