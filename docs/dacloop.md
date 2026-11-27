# q2dacloop: USB DAC mode

USB DAC mode makes the Q2 a USB Audio 2.0 DAC for a computer: the computer plays, the Q2's DAC and amplifier are its sound card. It is kept in Vamp OS's scope ([roadmap: Scope](vamp-os.md#scope)); stock's `uac2_loopback` and `uac_device_config.sh` do it today, and in Vamp OS q2sysd's `usb dac` and `q2dacloop` do.

| What | Where |
| --- | --- |
| Source | `dacloop/q2dacloop.c`, `dacloop/Makefile`; the gadget in `sysd/q2sysd.c` (`dac_gadget`) |
| In the image | `/usr/bin/q2dacloop` (`os/package/q2dacloop`), the `q2dacloop` service, down until `usb dac` (`/etc/sv/q2dacloop/down`) |
| Tests | `test/dacloop.py` (this machine), `test/sysd.py` (the mode), `test/os_image.py`'s boot (down at boot) |

## The mode

[Q2SYS/1](platform/system.md)'s `usb dac`, from any mode (through adb's: storage gives the card back first): the player stopped over Q2PLAY/1 (the DAC is the loop's while the mode lasts), adb's gadget parked, the UAC2 gadget bound, and `sv up /etc/sv/q2dacloop`. `usb adb` (or `usb storage`) takes it down in the other order: the loop's service down first, so its PCM on the gadget is closed before the gadget goes (a gadget's sound card is freed only at its last close), then the gadget removed and adb's unparked.

The gadget has stock's descriptors, learned from its `uac_device_config.sh`, so a computer that knew the Q2 as a DAC knows it again: `0x20B1:0x301F`, "Shanling", "Q2", one configuration, one UAC2 function with capture alone (`c_chmask` 3, two channels; `p_chmask` 0), 32-bit samples (`c_ssize` 4), the rates 32 to 384 kHz stock offers, asynchronous, no mute or volume control: the Q2's own volume stays the DAC's ([q2playd: The volume](playd.md#the-volume)).

## The loop

`q2dacloop` reads the gadget's capture PCM (`hw:CARD=UAC2Gadget,0`) and writes the Q2's DAC (`plughw:0,0`), 32-bit stereo, as it is, at the computer's rate:

- **The rate** is the gadget card's `Capture Rate` control, looked at every 200 ms and whenever 200 ms pass with no packets. 0 is a computer not streaming: nothing is open. A change closes both PCMs and opens them at the new rate.
- **A prebuffer** at each start: 100 ms of the computer's audio up to 96 kHz, 200 ms to 192 kHz, 300 ms above (stock's sizes), gathered before the first write so the DAC does not run dry while the computer's packets settle.
- **Recovery**: a capture overrun or a playback underrun is recovered (`snd_pcm_recover`) and counted in the session's closing line; a PCM that cannot be opened (the player still holding the DAC, the gadget not bound yet) is tried again each second.
- `--rate-file FILE` reads the rate from a file instead and `--rate N` fixes it: for a kernel without the control, and for the test. SIGTERM ends it with both PCMs closed.

## Tests

`python3 test/dacloop.py` (pytest) builds `q2dacloop` for this machine against its libasound and runs it between two of alsa-lib's file PCMs: the capture reads frames, each sample its own number, from a FIFO the test feeds in whole reads (a file PCM's short read leaves stale frames, which would look like a fault of the loop's), and the playback writes a file. What the computer sent arrives bit for bit after the 100 ms prebuffer at 96 kHz; a rate change from 44.1 to 192 kHz closes and opens again with a 200 ms prebuffer; a computer not streaming opens nothing; SIGTERM ends it. Copies without the prebuffer's write, without the rate's look or with one prebuffer size each fail it (`Q2DACLOOP_SOURCE`). `test/sysd.py`: `usb dac` stops the player, parks adb, binds the gadget with stock's attributes and starts the loop, and `usb storage` from there takes the loop down before the gadget and comes through adb's. `test/os_image.py`'s boot: the loop's service stays down at boot (`--perturb dac-up`, its `down` file gone, fails it).

## What is missing

- **Holding the fill level**: stock tunes the gadget's `Capture Pitch 1000000` control once a second to keep its ring near the prebuffer's target, so the computer's clock and the DAC's never drift apart far enough to overrun or underrun. Here a drift ends in an xrun, recovered.
- **DSD**: stock reads the gadget's `Capture Bits` (1 is DSD) and tells the DAC its mode through `/dev/shanling_dac`. Here every stream is PCM.
- **Leaving the mode at an unplug**: q2sysd changes modes on Q2SYS/1's word alone; the cable's events ([usb](usb.md)) are not followed yet.

## Needs a Q2

Whether the Q2's kernel has `usb_f_uac2` with Shanling's multi-rate `c_srate` and the `Capture Rate` control (stock's script and loop say so); the DAC opening as `plughw:0,0` with no `demo` having set it up ([q2playd: What is missing](playd.md#what-is-missing)); whether `q2player` lets the DAC go when stopped; the sound at each rate, and a computer's view of the device.
