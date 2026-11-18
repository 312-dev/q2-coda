#!/bin/sh
# S12: the GPIO driver has loaded, but the player has not read its saved DAC charging choice.
# PD18, the USB ID pin, has no pull of its own: with nothing attached it drifted low after an unplug and
# demo powered the bus into the next cable (docs/internals.md#otg-host-power). Its pull-up holds it high;
# an OTG adapter grounds it and still reads low.
case "$1" in
    start)
        /usr/bin/cmd_gpio set_func PE22 output1
        /usr/bin/cmd_gpio set_func PD18 input pull_up
        ;;
esac
