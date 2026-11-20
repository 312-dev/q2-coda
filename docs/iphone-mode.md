# iPhone mode: route 1's experiments

Route 1 of the car design has the Q2 present itself to a car as an iPhone in media mode over USB: iAP2 over a HID interface, the audio as USB audio PCM. The design is in the user's notes (`car-modes-design-20261119.md`); this page covers the tooling built for its first experiments and what the desk found. Nothing here is in an update image. The user's own step-by-step runbook for the car is `car-experiments-runbook.md` beside the design.

**Apple's identity stays out of release builds.** The tools here use Apple's vendor ID and the strings "Apple Inc." and "iPhone", and run only from `/tmp` on a `--dev` build. When any of it moves into an image (the `q2-iap` daemon, milestone M1), it goes behind a build flag that `--dev` turns on and release builds leave off.

## Files

| File | What |
| --- | --- |
| `tools/iap_probe/q2-iap-probe.c` | The probe: owns the gadget's FunctionFS instances, writes each one's descriptors, logs every event and control request, answers as an iPhone in media mode (below), and echoes iAP2's detect when asked. Built for the Q2 and, with `-DPROBE_HOST`, for the host. |
| `tools/iap_probe/q2-iap-probe.sh` | Runs on the Q2 from `/tmp`: makes the `q2iap` gadget, runs plans of phases, restores the ADB gadget, and enforces the time limit. |
| `tools/iap_probe/q2iap` | The computer's side over adb: `start`, `stop`, `status`, `pull`, `push`, `build`. |
| `tools/iap_probe/build.py` | Cross-compiles the probe the way `compile_helper` builds the helpers (clang, ld.lld, the stock rootfs's glibc, `patch/start.c`) and writes the test tone. |
| `tools/iap_probe/iap2.c` | iAP2 with the Q2 as the phone ([below](#iap2)), included by the probe for `--iap2`: the link, the car's buttons, the audio handoff, the cover ([car audio](#car-audio)). |
| `tools/iap_probe/hid-echo.ps1` | The desk's HID echo test on Windows. |
| `tools/iap_probe/hid-car.ps1` | The desk's stand-in car on Windows: M1's whole conversation through Windows' HID driver. |
| `tools/uac1_module/` | `usb_f_uac1.ko` for the stock kernel ([below](#the-uac1-module)). |
| `test/iap_probe.py` | The probe on the host: its blobs parsed by `f_fs.c`'s rules, and its answers, echo and sessions against a pty standing in for ep0. |

## The gadget and the probe

`q2iap` is one configfs gadget: `05ac:12a8`, `bcdDevice` `0x1001`, class `00/00/00`, "Apple Inc.", "iPhone", a 40-hex serial made once in `/mnt/data/q2-iap.serial`, every configuration `0xC0` and 500 mA. The `full` variant has four configurations: 1 PTP, 2 "iPod USB Interface" (audio, then a HID interface), 3 PTP with Apple Mobile Device, 4 those with Apple USB Ethernet. `min` has configuration 2 alone. Configuration 2's audio is the kernel's `uac2` with the Q2 as the source (`p_chmask` 3, `c_chmask` 0, 44.1 kHz, 16-bit), or `uac1` from the module. The HID interface is a FunctionFS instance carrying the 208-byte report descriptor (vendor page `0xFF00`, input reports 1 to 12, output reports 13 to 21), one interrupt IN endpoint of 64 bytes every 8 ms. The FunctionFS flags are `0xC3`, so requests addressed to the device, Apple's vendor requests among them, reach the probe.

The probe answers `81 06 22xx` with the report descriptor, `81 06 21xx` with the HID descriptor, Apple's `C0 53` with four zero bytes (`--carplay`: `01 00 00 00`) and `GET_REPORT` with zeros. It reads and logs every OUT request's data, and stalls every other IN request. With echo on, a `SET_REPORT` whose payload after `[id][link control]` is `FF 55 02 00 EE 10` is echoed as the smallest input report that holds it (report 2: `02 00 FF 55 02 00 EE 10 00 00`) on the interrupt endpoint, written from a child so the event loop never waits on the host. The kernel answers the host's descriptor requests itself, so those never reach the log. The controller's state (`udc=default/addressed/configured/not attached`) is logged at each change instead.

<a id="sessions"></a>
### Sessions and plans

A session is a plug: it begins at the first `ENABLE` (the host chose a configuration) and ends when a `DISABLE` or `SUSPEND` is not followed by another `ENABLE` or `RESUME` within 8 s (`--gap-ms`). In the car a cable out was a `SUSPEND` (the controller left `suspended`, `usb_online` 0), never a `DISABLE`, so the first car run stayed in its first phase. A host's reset during enumeration is therefore not a new session, while a cable out and in again is. A plan is phases run in order, each a gadget made, probed and removed. `car` is `full`, `full:echo`, `min:echo`, one session each, then the restore; `m1` is `full:iap2`, `min:iap2`; `m3` is `full:iap2:uac1`, `full:iap2`. `desk` is `full:echo:tone` until the time limit. Tokens: `full` or `min`, `:echo`, `:iap2`, `:noaudio`, `:uac1`, `:linuxid` (desk only: Linux's `1d6b:0106` in place of Apple's, so Windows' own audio driver takes the audio function), `:tone` (a -20 dBFS 1 kHz tone played into the gadget's sound card).

`start` returns at once. The runner, detached with `setsid` and with hangups ignored (adb's shell ends when `start` returns, and its hangup killed a runner that had not reached its own session yet), parks `q2adb` after `--delay` (at least 2 s, so `start`'s own lines reach the computer), and runs the plan. `--for` (default 900 s, at most 3600) is the limit from start, measured on the wall clock. The probe exits at it, and a separate watchdog forces `stop` 60 s later if the runner has not finished. The restore is `q2adb-gadget unpark`. `start` refuses unless the USB picker remembers Charge Only (`USBPICK=1`) or USB Debugging (4): any other choice makes demo start stock's storage gadget at the next cable in. Arming needs adb, and picking Charge Only with the cable in parks the ADB gadget, so the way to arm is with USB Debugging, then Charge Only on the Q2 within `--delay`; the runner logs a warning if the picker is still not Charge Only when the delay ends. That park unmounts configfs, so the teardown mounts it again before it looks for the gadget. `--force` skips the check, which is right only when the cable is already in and stays in.

The log is `/mnt/mmc/.q2muse/logs/q2-iap-probe.log`, the shell's lines and the probe's together, stamped with `CLOCK_MONOTONIC` as dmesg is. `.dmesg` beside it gets the kernel log at each phase's end. The previous run is kept as `.prev.log`. **Send Logs** on the LocalSend page sends them, since it sends that whole folder.

<a id="desk-rehearsal"></a>
## Desk rehearsal (2026-11-19, the user's Q2 on V1JDi, Windows 11 with Apple's driver)

USB is visible to Windows only, and binding it to WSL with `usbipd` needs an administrator prompt, so the host side was read with PowerShell (`Get-PnpDevice`, `Get-PnpDeviceProperty`) and the probe's log.

| Run | What | Result |
| --- | --- | --- |
| 1 | `full:echo:tone`, 150 s | All four FunctionFS instances took their descriptors (80, 70, 126 and 190 bytes). The gadget bound at high speed with four configurations. Shanling's `f_uac2` took `p_chmask=3 c_chmask=0 p_srate=44100 p_ssize=2` and registered ALSA card 1, `UAC2Gadget`. Windows showed "Apple Mobile Device USB Composite Device" (`usbccgp`, bus-reported "iPhone", hardware ID `USB\VID_05AC&PID_12A8&REV_1001`), sent usbmux's `C0 45` (stalled) and chose configuration 1 (PTP, as an MTP device). That is the "computer with Apple's drivers" fingerprint the design's detection table expects. The restore brought adb back by itself. |
| 2 | `min:echo:tone`, 100 s | Windows chose configuration 2, sent `SET_IDLE`, asked for the HID report descriptor (`81 06 2200`, `wLength` 0x110) and got all 208 bytes in under 1 ms, then listed "HID-compliant vendor-defined device". Apple's driver claimed interface 0 ("restore mode"), so Windows offered no audio input to record from, and `aplay` into the gadget card got I/O errors with no host streaming. |
| 3 | `min:echo`, Windows sending the detect (`hid-echo.ps1`) | Windows' `HidD_SetOutputReport` arrived as `SETUP 21 09 020e`, data `0e 00 ff 55 02 00 ee 10 00 00`. The probe wrote `02 00 ff 55 02 00 ee 10 00 00` on the interrupt endpoint, and Windows read exactly that back. |
| 4 | `car` with "unplugs" made by `soft_connect` | Each phase ended 8 s after its unplug (probe exit 10), the next phase's gadget was made and enumerated, and after the third the runner restored adb. |

Not proved at the desk: audio streaming out of the `uac2` source, since no host opened it (a Linux host recording it needs `usbipd bind` once, as administrator), and a real cable out, since `soft_connect` stood in for it. One trap was found: writing `connect` to `soft_connect` while the gadget is already connected left the controller in `default` and Windows with "Device Descriptor Request Failed" until the cable was replugged.

## In the car: experiment 1 (2026-11-19, one plug, phase `full`)

The BMW (iDrive 7, armrest USB-C) treated the Q2 as an iPhone. 2 s after the bind it sent Apple's `C0 53` twice before choosing a configuration (it reached configuration 1's instance, the one that takes requests before a configuration, `CONFIG0_SETUP`; answered `00 00 00 00`), chose configuration 2, read the HID descriptor and the 208-byte report descriptor, and sent iAP2's detect (`SET_REPORT` report 14: `0e 00 ff 55 02 00 ee 10 00 00`) once a second for 8 s. Unanswered (echo was off in that phase), it reset the device (`DISABLE`, `ENABLE` 160 ms later) and waited, configured and silent, until the cable came out 2 minutes later. It never sent `C0 45` or chose configurations 1, 3 or 4.

<a id="iap2"></a>
## iAP2: milestone M1

`--iap2` (the `:iap2` token) puts `iap2.c` on the HID instance: a port of PodBox's link layer and the M1 part of its control session (GPL version 2 or later, under version 3 here; the file's head says what came from where). In order:

- **Transport:** the car's output reports (`SET_REPORT` data, `[id][link control][data]`) reassembled into packets; the Q2's packets cut into the smallest input report that holds what is left, up to report 7 (95 bytes), as PodBox sends at high speed.
- **Link:** the detect echoed; the car's SYN logged whole and answered SYN+ACK with its own parameters, the packet length capped at 8,192; the link up at the car's ACK of it. PodBox's traps are kept: a packet is never sent again to a car whose SYN gives no retries, and a bare ACK goes only to one whose SYN gives a cumulative acknowledgement timeout. One message to a packet.
- **Authentication, the Q2 as verifier:** `AA00`; to the certificate, a 20-byte challenge (32 to one under 800 bytes); 20 ms after the answer, `AA05` and `1D00`. Nothing is checked: the Q2 holds no key and needs none.
- **Identification:** the car's names and its two message lists logged by name; accepted 30 ms later (`1D02`), then the UUID (the serial's first 32 hex digits), the language `en` and the name "Q2 Muse", each only if the car takes it, and a Bluetooth pairing start and stop if it names a component.
- **What comes next, answered as an iPhone answers:** power (`AE01`, in an iPhone's order), the Bluetooth connection (`4E04`, nothing connected), CarPlay unavailable (`4300`, timed after the car's `PowerSourceUpdate` as PodBox found it must be), the library by name with no items (`4C01`, `4C04`: the car's browser starts empty, by the user's decision), USB audio at 44.1 kHz (`DA01` index 7; M3 plays into it), and Now Playing (`5001`) from the shim's `/tmp/coda-rc.now`, which it writes while Car Remote is on (title, artist, album, length, state, the queue's place, the position every half second while playing), else the title "Q2 Muse". The car's button reports (`6802`) are logged, not acted on (M2).

The car's reports go out through a child process that writes them to the HID instance's `ep1` one at a time, fed by a non-blocking pipe, so a host that stops collecting the interrupt endpoint costs reports, never the loop that answers the car's control requests.

**Tested:** `test/iap_probe.py` drives the probe with a synthetic car through a pty standing in for `ep0` and a FIFO for `ep1`: the whole conversation to identification, power, now playing and audio, every packet's checksums and acknowledgements, and the no-retries trap (11 tests; each of three changes to `iap2.c`, a message dropped, the SYN+ACK's window and a checksum, fails them). At the desk (2026-11-19), `hid-car.ps1` played the car through Windows' HID driver against the Q2 in a `min:iap2` phase: every step answered, the 900-byte certificate crossing several output reports, the Q2 sending Now Playing for the song the shim had ("1901", 3:13). **Not tested:** the BMW itself, its real SYN and identification, and whether iDrive lists the Q2 as a source.

## In the car: M1 (2026-11-19, plan `m1`, V1K8i)

**iDrive listed the Q2 as a source and showed its song's title and artist, with the position current; no sound, no cover.** The BMW identified as "BMW 83828", model MGU-74-P, BMW Group, firmware 23.44.2, hardware 4.4.0. Its SYN: window 5, packets up to 65,535 bytes, retransmission after 200 ms and up to 10 times, a cumulative acknowledgement within 22 ms or after 3 packets, sessions 1 (control), 2 (file transfer) and 3. Its certificate was 608 bytes (an MFi 3.0 one, so a 32-byte challenge); it took `AA05`, identified, asked for power, the library (taken empty), Now Playing (attributes 0, 1, 4, 6 to 10, 12, 16, 18, 21 to 24, 26 the artwork, 27; playback 0 to 14, 16, 17), HID and USB audio. Its buttons came as `AccessoryHIDReport`s against the report descriptor in its `StartHID`: five bits on the consumer page, Play, Pause, Next, Previous and Random (`01`, `02`, `04`, `08`, `10`), each sent pressed then released; it sent Play by itself as the source was chosen. At the cable out the controller went suspended, and the Q2 sent the link's last packet again through its ten tries.

**Why no sound** (from `dmesg` and the stock kernel's code): the car opened the UAC2 stream (`UAC2: playback stream START, srate=44100`), then sent a class request addressed to the endpoint, which Shanling's `afunc_setup` refuses (`afunc_setup:2143 Error!`: the path taken when the request's recipient is not an interface, `addiu $7, $zero, 0x85f` before `_dev_err`, 0x85f being 2143), and closed the stream 7 ms later. That is USB Audio 1.0's way of setting a sample rate (a `SET_CUR` to the isochronous endpoint), which UAC2 has no place for, and a real iPhone is UAC1. FunctionFS cannot answer it: the kernel hands an endpoint's requests to the function that owns the endpoint. 5.10's `f_uac1` answers them (`audio_set_endpoint_req` takes `SET_CUR`, `audio_get_endpoint_req` the four `GET`s), so route 1's audio is the UAC1 module ([below](#the-uac1-module)).

<a id="car-audio"></a>
## Car audio, buttons and cover (M3, M2, and the cover from M6)

- **The handoff.** At `StartUSBDeviceModeAudio` the probe writes `/tmp/q2-iap.audio`: the gadget's ALSA device (`hw:CARD=UAC1Gadget,0`, or `UAC2Gadget` in a `uac2` phase), then 44100. It removes it at `StopUSBDeviceModeAudio`, a link reset, a new plug and its own end. The player's way 3 (`player/output.c` `output_car`, `engine.c` `car_way`; [player: output](player.md#output)) takes over from the DAC's and Bluetooth's while it is there, in `--dev` builds alone (`Q2_CAR_USB`, from `tools/build.py`'s `car_usb=dev`): a song playing moves to the car where it is within 250 ms, and back when the file goes, as a seek moves; the car's rate through Bluetooth's resampler, 16 bits, unity volume (the car's knob is its volume), and the PCM kept across songs of any rate and width, so the car's stream never stops between them.
- **Never a stopped stream.** The gadget's `u_audio` requeues each packet unchanged while ALSA's stream is stopped, so a stopped PCM repeats its last millisecond, a buzz, and it cannot pause (no pause trigger). So way 3 never stops it while the car listens: a pause lets it run dry into ALSA's silence (`alsa_sw`'s silence size), a seek or a skip takes its frames back (`snd_pcm_rewind`) instead of dropping them, and a close writes two periods of silence and waits them out, bounded, first. A car that stops reading (it changed source, the cable came out) leaves no room in the PCM: a second of that ends the car's way (`OUTPUT_CAR_GONE`), the file is removed and the song goes back to demo's way.
- **Buttons (M2).** `StartHID`'s report descriptor is read for one-bit controls on the consumer page; each `AccessoryHIDReport` control that is down and was up goes to the shim's command file `/tmp/coda-rc.cmd`, as the Remote app's commands do (so Car Remote must be on): Play `resume`, Pause `pause`, Play/Pause `toggle`, Next `next`, Previous `previous`, Random `mode` with shuffle turned over, Repeat `mode` with repeat stepped. `SetNowPlayingInformation`'s position is a `seek`.
- **The cover.** When the car asks for attribute 26, Now Playing names a file transfer (ids 80 to FF), set up on the file transfer session as `[ID, 04, 8-byte size, 00 02]`; at the car's `[ID, 01]` the JPEG goes in pieces as the link allows, `[ID, 80|00|40|C0, bytes]`. The JPEG is Cover Flow's 160 px copy (`/mnt/mmc/.coverflow/<key>.jpg`, the key FNV-1a of the artist and the album as `coverflow.c` makes it), else `cover.jpg`, `folder.jpg` or `front.jpg` beside the song, a baseline JPEG up to 64 KB, sent with each quantisation and Huffman table in a segment of its own (PodBox's car needed that) and its bytes otherwise as they are; none is an empty transfer. ponytail: an iPhone sends about 300 px; Cover Flow's 160 px copy is what the Q2 has without decoding a JPEG.
- **The window.** The BMW retransmits, so the Q2 keeps no more of its own packets unacknowledged than the car's window (5): a cover's dozen packets wait in a queue (`iap2.c` `enqueue`, `pump`) and go as acknowledgements free the window.

**Tested on this machine:** `test/iap_probe.py`, 14 tests, now with the BMW's own SYN and `StartHID`: its presses `01`, `08`, `04`, `10` as `resume`, `previous`, `next`, `mode 1 2`; the audio file written and removed; a seek; a cover from a cache keyed as Cover Flow's, ffmpeg's JPEG with its tables split, never more than 5 packets out with the car holding its acknowledgements, the rest after. `test/player/run.py -k car`, 3 tests: a1 into the car's PCM bit for bit at unity with Bluetooth's volume at half, a 96 kHz song after it gapless in the same PCM; the car's audio asked for and stopped while a1 plays, three PCMs in turn (Bluetooth, the car's, Bluetooth), the car's an unbroken stretch of a1; a release build's player unmoved by the file. Each was shown to fail on a change that breaks what it checks.

<a id="the-uac1-module"></a>
## The UAC1 module

A real iPhone's configuration 2 is USB Audio Class 1, and the stock kernel has only `f_uac2`. `tools/uac1_module/build.py` builds `usb_f_uac1.ko` from Linux 5.10.21's `f_uac1.c` with a private copy of `u_audio.c`, whose six exported names `q2_rename.h` changes. A module exporting a name the kernel already has is refused, and Shanling's `u_audio` in the kernel is modified, so the module must never bind to it. The kernel tarball is fetched from kernel.org and its SHA-256 checked against kernel.org's list. The tree is unpacked without the other architectures and most sources (387 MB), configured as `malta_defconfig` plus `q2.config`, and prepared for modules. The compiler is Ubuntu's MIPS cross gcc 12, fetched with `apt-get download` and unpacked under the work directory, never installed: installing it removes `gcc-multilib`, which other test builds need.

`build.py` fails unless all of these hold:

- `q2.config` was taken whole;
- the vermagic is `5.10.21+ SMP preempt mod_unload MIPS32_R5 32BIT ` (with the stock modules' trailing space);
- `struct module` is 0x180 bytes with `init` at 0xe0 and `exit` at 0x16c, as in `soc_aic.ko`;
- each of the 46 symbols the module needs is a `__ksymtab_` name of the stock kernel;
- the module exports nothing the kernel does;
- 47 offsets and sizes in `q2_layout.ko` equal those read in the stock kernel's own code, each with the instruction it came from (`STOCK` in `build.py`), and `ep->maxpacket` is loaded as the halfword stock writes.

The stock kernel's symbols come from `vmlinux-to-elf` on `~/q2-emu/audit/kernel/vmlinux.bin`, the same #303 build the Q2 runs.

What the stock kernel was read to have, and what each changes:

| Read in the stock kernel | Option | What it moves |
| --- | --- | --- |
| `do_raw_spin_lock`, `do_raw_write_lock` | `DEBUG_SPINLOCK` | `spinlock_t` is 16 bytes, not 4 |
| `srcu_module_notify` but no `jump_label_module_notify` | no `JUMP_LABEL` | `struct module`'s `exit` 0x16c, not 0x174 |
| no `report_bug`, `_mcount` or tracepoints | no `BUG`, `FTRACE`, `TRACING` | `struct module` |
| `snd_power_wait`, standby through `/sys/power/state` | `PM`, `PM_SLEEP` (Malta needs its suspend gate relaxed in the throwaway tree) | `struct device` |
| `pinctrl_bind_pins`, `dma_alloc_from_contiguous` | `PINCTRL`, `DMA_CMA` | `struct device` 0x200 bytes, so `struct snd_pcm_str` 0x220 and `pcm->private_data` 0x51c (stock `g_audio_setup` writes it with `sw $16, 0x51c($4)`; Malta's layout put it at 0x50c, which would have handed ALSA a wrong pointer at the first stream open) |
| `snd_pcm_substream_proc_hw_params_read`; no xrun debug or OSS | `SND_VERBOSE_PROCFS` | `struct snd_pcm_str` |
| `alloc_pages_exact` shifts the size right by 12 | `PAGE_SIZE_4KB` (`malta_defconfig` has 16 kB) | `u_audio.c`'s PCM limits, multiples of the page: a buffer of at most 64 kB in periods of at most 4 kB, where 16 kB pages made them 256 kB and 16 kB |

The check was shown to fail: with `DMA_CMA` dropped, `dev->id` moved to 0x1c8 against the stock 0x1cc and the build failed (padding kept `pcm->private_data` where it was, so that row alone would not have caught it). The stock `uac_pcm_ops` table has the module's slot order (open, close, prepare, trigger, pointer at slots 0, 1, 5, 6, 8).

**At the desk (2026-11-20): the first two binds oopsed.** The module loaded and its function took its attributes, but the bind (the write of the controller's name to `UDC`) ended in an oops in `snd_card_register`: `device_add` found the card's parent no device (`kobject_get` warned, `kobject_namespace` faulted at `0x78bc`), and the writer was killed holding the gadget's lock, so every later gadget call, `q2guard`'s included, waited for ever and the Q2 needed a restart. The first try looked like a hang; `uac1-steps.sh` (each step synced to the card, the stacks of a blocked step dumped) showed the bind's exit 139 and the oops. The cause: Shanling's `struct usb_gadget` has two more 32-bit fields after `max_speed` (where upstream 5.12 put `ssp_rate` and `max_ssp_rate`), so `state` is at 0x34 and `dev` at 0x40, where 5.10.21's header puts them at 0x2c and 0x38; the module passed `snd_card_new` the gadget plus 0x38 as the card's parent. That offset had been printed by `q2_layout.ko` but was not among the stock-checked rows. `build.py` now adds the two fields in its throwaway tree's `gadget.h`, and checks 47 offsets and sizes against stock code, among them `gadget->dev`, `state`, `max_speed`, `ep0`, the gadget dev's `driver_data`, `cdev->req`, `config->cdev`, and `ep->maxpacket` from the compiled load (a bit-field); without the two fields it fails on `state` and `dev`. Every field of a kernel structure the module's two source files use is now one of the rows.

**At the desk (2026-11-20): every PCM open failed on a Q2 up 15 minutes** (60 of 60, `aplay`: "Unable to install hw params", the kernel: `page allocation failure: order:5`). The module had been compiled for `malta_defconfig`'s 16 kB pages and the Q2's are 4 kB, so `u_audio.c`'s buffer limit (16 pages) was 256 kB where Linux means 64 kB. `aplay` and `q2player` both ask for half a second, 88,200 bytes at 44.1 kHz, which the limit no longer capped; 5.10.21's `u_audio` has ALSA allocate the buffer as contiguous pages at each `hw_params` (`snd_pcm_lib_malloc_pages`, `alloc_pages_exact`), which for 88,200 bytes is a 128 kB block (order 5), and the Q2 had none free (44 of 64 kB, 35 MB free in all; the stock kernel has no memory compaction to make one). `q2.config` now sets 4 kB pages and the page size is one of the stock-checked rows; without the setting the build fails on `q2_size_page 0x4000, stock 0x1000`. Between the two builds the module's code differs only in those limits (`uac_pcm_open`, `g_audio_setup`). The buffer is now at most 64 kB: 16,384 frames, 371 ms at 44.1 kHz. A 64 kB contiguous block (order 4) at every open could still be missing on a fragmented Q2, so `build.py` also changes the one `snd_pcm_set_managed_buffer_all` call in the module's copy of `u_audio.c` from `SNDRV_DMA_TYPE_CONTINUOUS` to `SNDRV_DMA_TYPE_VMALLOC`: 16 single pages, which `u_audio` can use because it copies samples into each request with `memcpy`. The stock kernel's ALSA has the type (`snd_dma_alloc_pages` calls `__vmalloc`, `snd_dma_free_pages` `vfree`, `snd_pcm_mmap_data_fault` `vmalloc_to_page`).

**Not yet tested:** loading it. The risks left are code, not layout: Shanling may have changed composite or the dwc2 driver in ways that only running the function shows, the controller's FIFOs are sized for its own gadgets, and only the fields the kernel's code was seen to touch were checked. A crash costs a restart, nothing is flashed. At the desk, with the Q2 on USB:

```sh
python3 tools/uac1_module/build.py                       # the module, all checks ok
tools/iap_probe/q2iap start --plan min:echo:uac1 --for 120 --force   # pushes it; the phase loads it
# 2 minutes later, adb is back:
tools/iap_probe/q2iap pull desk-uac1                     # usb_f_uac1.ko loaded/would not load, dmesg, the uac1 attributes
```

## Milestones after the experiments

Unchanged from the design's staged plan, except that **the car's own library browser starts empty**: in iPhone mode the car shows now playing, progress and art and its buttons work, while music is chosen on the Q2 or in the Remote app. Pushing the library to iDrive (M7) is deferred.
