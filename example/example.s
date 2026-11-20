; bin_count: counts up in binary on the LEDs on D8-D11 (PB0-PB3), about 4 steps a second

.equ SREG, 0x3f
.equ DDRB, 0x04
.equ PORTB, 0x05

.org 0

main:
    ldi r16, 0
    out SREG, r16
    ldi r16, 0b00001111
    out DDRB, r16
    ldi r16, 0x00
    
mainloop:
    rcall cycle
    rcall delay
    rjmp mainloop

delay:
    ldi r20, 20 
1:  clr r19
2:  clr r18
3:  dec r18
    brne 3b
    dec r19
    brne 2b
    dec r20
    brne 1b
    ret

cycle:
    out PORTB, r16
    inc r16
    ret
