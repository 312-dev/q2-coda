#!/bin/sh
# uac_tone.sh [S]: the desk test of USB audio (docs/usb-audio.md#the-desk-test), run on the Q2 from /tmp with the
# q2adb gadget's audio on. It waits up to S seconds (60) for the computer to start recording "Q2 Output", reads the
# rate the computer chose, and plays /tmp/tone-RATE.wav (pushed beside it, S32 stereo at that rate) into the
# gadget's card at it, so the tone arrives at its own pitch and level. The computer's rate is the card's Playback
# Rate control (0 while nobody records), or failing that the kernel's last "srate=" line since this started.
# Every line also goes to /mnt/data/vamp-usb-audio.log.
LOG=/mnt/data/vamp-usb-audio.log
say() { echo "$*"; echo "$(cut -d' ' -f1 /proc/uptime) [tone] $*" >> "$LOG"; sync; }
C=$(sed -n 's/^ *\([0-9]*\) \[UAC2Gadget *\].*/\1/p' /proc/asound/cards)
[ -n "$C" ] || { say "no UAC2Gadget card: is the gadget's audio on?"; cat /proc/asound/cards; exit 1; }
say "card $C; its controls:"
amixer -c "$C" contents 2>&1 | head -40
seen=$(dmesg | grep -c 'srate=')
# the rate and where it was read: "44100 control", "44100 dmesg", or "0 -"
rate() {
    r=$(amixer -c "$C" cget iface=PCM,name='Playback Rate' 2>/dev/null | sed -n 's/.*: values=\([0-9]*\).*/\1/p')
    [ -n "$r" ] && [ "$r" != 0 ] && { echo "$r control"; return; }
    r=$(dmesg | grep 'srate=' | tail -n +$((seen + 1)) | sed -n 's/.*srate=\([0-9]*\).*/\1/p' | tail -1)
    [ -n "$r" ] && { echo "$r dmesg"; return; }
    echo "0 -"
}
i=0
while [ "$(rate)" = "0 -" ] && [ $i -lt "${1:-60}" ]; do sleep 1; i=$((i+1)); done
set -- $(rate)
r=$1 how=$2
[ "$r" != 0 ] || { say "the computer did not start recording"; exit 1; }
say "the computer records at $r Hz (from the $how)"
f=/tmp/tone-$r.wav
[ -f "$f" ] || { say "no $f: push a tone at $r Hz (have: $(ls /tmp/tone-*.wav 2>/dev/null | tr '\n' ' '))"; exit 1; }
(sleep 1; say "hw_params: $(tr '\n' ' ' < /proc/asound/card$C/pcm0p/sub0/hw_params)") &
aplay -D "hw:$C,0" "$f"
say "aplay $f: rc $?"
wait
