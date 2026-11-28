#!/bin/sh
# uac_tone.sh [S [NAME]]: the desk test of USB audio (docs/usb-audio.md#the-desk-test), run on the Q2 from /tmp with the
# q2adb gadget's audio on. Start it before the computer opens "Q2 Output": it waits up to S seconds (60) for the
# kernel's "playback stream START, srate=R" line (the computer selected the streaming alternate setting), takes R from
# it (stock's rootfs has no amixer to read the card's Playback Rate control), and plays /tmp/NAME-R.wav (NAME tone;
# pushed beside it, S32 stereo at that rate) into the gadget's card twice, 2 s apart, so a recording shows whether
# what reaches the computer depends on the play's open. Each play waits for the stream to be running (the last of
# the START and STOP lines a START), and a STOP while it plays is logged within 500 ms of it. While each plays, the PCM's
# status (state, hw_ptr, appl_ptr, avail) is sampled every 100 ms: whether the card takes the frames at the
# computer's pace. Kernel lines count from the script's start by their timestamp, not by position: once the kernel's
# ring buffer is full each new line pushes an old one out, and the number of lines stops growing. The kernel's
# lines since it started close the run. Every line also goes to /mnt/data/vamp-usb-audio.log.
# UAC_PROC, UAC_LOG, UAC_TMP and UAC_DMESG stand in for /proc, the log, /tmp and dmesg in tests.
PROC=${UAC_PROC:-/proc}
LOG=${UAC_LOG:-/mnt/data/vamp-usb-audio.log}
TMP=${UAC_TMP:-/tmp}
DMESG=${UAC_DMESG:-dmesg}
up() { cut -d' ' -f1 "$PROC/uptime"; }
say() { echo "$*"; echo "$(up) [tone] $*" >> "$LOG"; sync; }
C=$(sed -n 's/^ *\([0-9]*\) \[UAC2Gadget *\].*/\1/p' "$PROC/asound/cards")
[ -n "$C" ] || { say "no UAC2Gadget card: is the gadget's audio on?"; cat "$PROC/asound/cards"; exit 1; }
S=$PROC/asound/card$C/pcm0p/sub0
t0=$(up)
# the kernel's lines stamped after t0 ("[ 1579.451568] ..."); a line without a stamp is skipped
since() { $DMESG | awk -v t="$t0" '{ s = $0; if (sub(/^\[ */, "", s) && s + 0 > t) print }'; }
# "START R" or "STOP R": the last stream line since t0, empty if none
stream() {
    since | awk '/playback stream (START|STOP), srate=/ { st = $0 }
        END { if (st != "") { sub(/.*playback stream /, "", st); sub(/, srate=/, " ", st); sub(/[^0-9]*$/, "", st); print st } }'
}
running() { case "$(stream)" in START\ *) return 0 ;; esac; return 1; }
say "card $C; waiting for the computer to record (kernel lines $($DMESG | wc -l), from $t0)"
wait_running() {
    i=0
    while ! running && [ $i -lt "${1:-60}" ]; do sleep 1; i=$((i+1)); done
    running
}
wait_running "${1:-60}" || { say "the computer did not start recording within ${1:-60} s (last stream line: $(stream))"; since | tail -20; exit 1; }
r=$(stream | cut -d' ' -f2)
say "the computer records at $r Hz"
f=$TMP/${2:-tone}-$r.wav
[ -f "$f" ] || { say "no $f: push a tone at $r Hz (have: $(ls "$TMP"/*-[0-9]*.wav 2>/dev/null | tr '\n' ' '))"; exit 1; }
sample() {
    k=0
    while [ -f "$TMP/uac_tone.playing" ]; do
        # the stream's state every 500 ms: a whole dmesg each sample would load the Q2 while it plays
        [ $((k % 5)) -eq 0 ] && ! running && { say "stream stopped by $(up): $(stream)"; return; }
        k=$((k+1))
        say "status $(up): $(grep -E 'state|hw_ptr|appl_ptr|avail' "$S/status" 2>/dev/null | tr -s ' \n' ' ')"
        usleep 100000
    done
}
for n in 1 2; do
    wait_running "${1:-60}" || { say "play $n: the stream is not running (last stream line: $(stream))"; break; }
    : > "$TMP/uac_tone.playing"
    sample &
    say "play $n at $(up)"
    (sleep 1; say "hw_params: $(tr '\n' ' ' < "$S/hw_params")") &
    aplay -D "hw:$C,0" "$f"
    say "play $n: aplay rc $? at $(up)"
    rm -f "$TMP/uac_tone.playing"
    wait
    sleep 2
done
say "kernel lines $($DMESG | wc -l)"
since | while read -r l; do say "dmesg $l"; done
