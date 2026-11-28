#!/bin/sh
# uac_tone.sh [S [NAME]]: the desk test of USB audio (docs/usb-audio.md#the-desk-test), run on the Q2 from /tmp with the
# q2adb gadget's audio on. It waits up to S seconds (60) for the computer to start recording "Q2 Output", takes the
# rate the computer chose from the kernel's "srate=" line (stock's rootfs has no amixer to read the card's Playback
# Rate control), and plays /tmp/NAME-RATE.wav (NAME tone; pushed beside it, S32 stereo at that rate) into the gadget's card
# twice, 2 s apart, so a recording shows whether what reaches the computer depends on the play's open. While each
# plays, the PCM's status (state, hw_ptr, appl_ptr, avail) is sampled every 100 ms: whether the card takes the
# frames at the computer's pace. The kernel's lines since it started close the run. Every line also goes to
# /mnt/data/vamp-usb-audio.log.
LOG=/mnt/data/vamp-usb-audio.log
say() { echo "$*"; echo "$(cut -d' ' -f1 /proc/uptime) [tone] $*" >> "$LOG"; sync; }
C=$(sed -n 's/^ *\([0-9]*\) \[UAC2Gadget *\].*/\1/p' /proc/asound/cards)
[ -n "$C" ] || { say "no UAC2Gadget card: is the gadget's audio on?"; cat /proc/asound/cards; exit 1; }
S=/proc/asound/card$C/pcm0p/sub0
seen=$(dmesg | wc -l)
since() { dmesg | tail -n +$((seen + 1)); }
rate() { since | sed -n 's/.*srate=\([0-9]*\).*/\1/p' | tail -1; }
say "card $C; waiting for the computer to record"
i=0
while [ -z "$(rate)" ] && [ $i -lt "${1:-60}" ]; do sleep 1; i=$((i+1)); done
r=$(rate)
[ -n "$r" ] || { say "the computer did not start recording within ${1:-60} s"; since | tail -20; exit 1; }
say "the computer records at $r Hz"
f=/tmp/${2:-tone}-$r.wav
[ -f "$f" ] || { say "no $f: push a tone at $r Hz (have: $(ls /tmp/*-[0-9]*.wav 2>/dev/null | tr '\n' ' '))"; exit 1; }
sample() {
    while [ -f /tmp/uac_tone.playing ]; do
        say "status $(cut -d' ' -f1 /proc/uptime): $(grep -E 'state|hw_ptr|appl_ptr|avail' $S/status 2>/dev/null | tr -s ' \n' ' ')"
        usleep 100000
    done
}
for n in 1 2; do
    : > /tmp/uac_tone.playing
    sample &
    say "play $n at $(cut -d' ' -f1 /proc/uptime)"
    (sleep 1; say "hw_params: $(tr '\n' ' ' < $S/hw_params)") &
    aplay -D "hw:$C,0" "$f"
    say "play $n: aplay rc $? at $(cut -d' ' -f1 /proc/uptime)"
    rm -f /tmp/uac_tone.playing
    wait
    sleep 2
done
since | while read -r l; do say "dmesg $l"; done
