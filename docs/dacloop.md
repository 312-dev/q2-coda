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

## Holding the fill level

The computer's clock and the DAC's are two crystals, and the gadget is asynchronous: left alone, a computer 100 ppm fast at 96 kHz puts about 10 more frames a second into the loop than the DAC takes, so the 100 ms prebuffer grows until the capture overruns, or, slow, drains until the DAC underruns (in under 20 minutes at that error). Stock holds it with the gadget's `Capture Pitch 1000000` control, once a second; so does `q2dacloop`, with a controller of its own (`dacloop/pitch.h`):

- **The control** scales the rate the Q2's feedback endpoint asks the computer for: 1000000 is nominal, 1000100 asks for 100 ppm more (the kernel's `u_audio` meaning). Its range is read from the control itself, and never more than 2000 ppm either way is asked (`PITCH_SPAN_PPM`, beyond any real crystal's error). Nominal at each session's start and again at its end, so the next session, or stock, starts clean.
- **The fill** is the frames between the computer and the DAC: the capture's waiting frames plus the playback's delay, taken after each write and averaged over the second. Its error is that less the prebuffer's size.
- **The controller** is proportional and integral, critically damped with a 60 s time constant (`PITCH_TAU_S`): the proportional part pulls the fill back, the integral learns the computer's steady error, so the fill settles on the prebuffer and not beside it. Slow on purpose: a second's measurement is a few packets' worth of noise and the drift is tiny, and a quick loop would only move the computer's rate about. Held at a limit, the integral moves only back towards the range, so a jump in the fill (a burst, an xrun's recovery) with a narrow control does not swing past the target on the way back.
- A kernel without the control is left as it is, with one line in the log per session; `--pitch-file FILE` writes each value to a file instead, for the test.

Simulated (`test/dacloop_pitch.c`, half an hour, a measurement with up to half a 1024-frame read of noise each second): a computer off by 500 ppm, USB's limit, at 44.1, 96 and 384 kHz keeps the fill within about 12 ms of the prebuffer at its worst, and over the last ten minutes on it within the noise; held at nominal, 100 ppm at 44.1 kHz is 180 ms out by then.

## Tests

`python3 test/dacloop.py` (pytest) builds `q2dacloop` for this machine against its libasound and runs it between two of alsa-lib's file PCMs: the capture reads frames, each sample its own number, from a FIFO the test feeds in whole reads (a file PCM's short read leaves stale frames, which would look like a fault of the loop's), and the playback writes a file. What the computer sent arrives bit for bit after the 100 ms prebuffer at 96 kHz; a rate change from 44.1 to 192 kHz closes and opens again with a 200 ms prebuffer; a computer not streaming opens nothing; SIGTERM ends it. Copies without the prebuffer's write, without the rate's look or with one prebuffer size each fail it (`Q2DACLOOP_SOURCE`). `test/sysd.py`: `usb dac` stops the player, parks adb, binds the gadget with stock's attributes and starts the loop, and `usb storage` from there takes the loop down before the gadget and comes through adb's; a cable in with USB Audio remembered (`USBPICK=3`) starts the mode and the cable out ends it ([q2sysd: The cable](sysd.md#the-cable)). `test/os_image.py`'s boot: the loop's service stays down at boot (`--perturb dac-up`, its `down` file gone, fails it).

The fill level: `test/dacloop.py` builds `test/dacloop_pitch.c` with `dacloop/pitch.h` and runs it for 15 computers (off by -500, -100, 0, 100 and 500 ppm at three rates): the fill within 20 ms of the target throughout, its last ten minutes' mean within 0.5 ms, the control's mean on the computer's error within 10 ppm; a slow computer with a control that can only speed up; a 50 ms jump with a control of 100 ppm either way coming back without going 5 ms past. The loop itself, with `--pitch-file`: nominal at the start, a step a second within the span while it plays, nominal at the end; and without the control, it plays as before and says so. Copies of `pitch.h` held at nominal, without the integral, with the sign turned, and with an integral that winds up at a limit each fail it (`Q2DACLOOP_PITCH`); `q2dacloop.c` without the pitch fails the loop's (`Q2DACLOOP_SOURCE`).

## What is missing

- **DSD**: stock reads the gadget's `Capture Bits` (1 is DSD) and tells the DAC its mode through `/dev/shanling_dac`. Here every stream is PCM.

## Needs a Q2

Whether the Q2's kernel has `usb_f_uac2` with Shanling's multi-rate `c_srate` and the `Capture Rate` control (stock's script and loop say so), and `Capture Pitch 1000000` with upstream's meaning and its range; whether the fill measured from the gadget's capture and the DAC's delay is as steady as the simulation's half-read noise, and the drift a real computer shows; the DAC opening as `plughw:0,0` with no `demo` having set it up ([q2playd: What is missing](playd.md#what-is-missing)); whether `q2player` lets the DAC go when stopped; the sound at each rate, and a computer's view of the device.
