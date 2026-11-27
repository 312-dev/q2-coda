# Turning on Vamp's own Bluetooth on a Q2

The steps for the owner's Q2, at a desk, to turn on Vamp's own `bluetoothd` and `bluealsa` with the
[launcher](bluez.md#the-launcher) and decide whether each can become the default. Nothing here has been run:
every row is a **needs a Q2**. It gathers what [bluez: Needs a device](bluez.md#needs-a-device),
[bluez: On a Q2](bluez.md#on-a-q2) and [bluealsa: On a Q2](bluealsa.md#on-a-q2) ask, in the order that keeps one
suspect at a time, and adds the launcher's own rows. Results go back into those pages, each as measured.

## What is needed

- A `--dev` build with both daemons: `B=$(tools/mac/q2env python3 tools/ci/rebuild.py --dev -- --own-bluez --own-bluealsa | tail -1)`,
  installed the usual way ([building](building.md)); `adb` ([adb](adb.md)) for a shell.
- The owner's headsets: at least one that offers AAC and one that offers LDAC (any of aptX and aptX HD too), a
  car or a headset that shows the track, the phone with the Car Remote app, and a phone that can play to the Q2
  (the receiver page, Guest Play). Note each device by a letter in the results, never by its name or address.
- The unified log ([internals](internals.md#unified-log)): `adb pull /mnt/mmc/.q2muse/logs/q2-log.0` after each
  step, and the `q2-bt` log beside it. The launcher's lines are source `bt`, words `bluez:` or `bluealsa:`.

## Before anything

| # | Step | Note |
| --- | --- | --- |
| 0.1 | `cp -a /mnt/data/bluetooth /mnt/data/bluetooth.before-own`, and `cp /mnt/data/config.ini /mnt/data/config.ini.before-own` | the copies the last step compares with |
| 0.2 | On stock's daemons (no key set): headset A playing AAC for 10 minutes. `grep VmRSS /proc/$(pidof bluetoothd)/status /proc/$(pidof bluealsa)/status`; both daemons' CPU ticks (`/proc/PID/stat` fields 14 and 15) at the start and the end | the baseline for step 4.6 |
| 0.3 | `ls -l /usr/bin/bluetoothd /usr/lib/bluez-stock /usr/lib/vamp-bluez /usr/lib/vamp-bt`; `ps -o pid,ppid,comm \| grep -E 'blue\|bt-watch'` | the launcher is in place; with no key, stock's daemons run with no `bt-watch` |

To turn a daemon on: `adb shell`, then add the key under the file's `[Q2POD]` header (a key above any header,
or in another section, is not read), and turn Bluetooth off and on from Settings:

```sh
grep -n '^\[Q2POD\]' /mnt/data/config.ini      # the section's line
sed -i '/^\[Q2POD\]$/a BLUEZ=1' /mnt/data/config.ini && sync
```

To turn it off: remove the line (`sed -i '/^BLUEZ=1$/d'`), sync, Bluetooth off and on. A reboot always
starts again from the key, whatever fell back.

## 1. bluetoothd alone (`BLUEZ=1`, stock's bluealsa)

| # | Step | Pass |
| --- | --- | --- |
| 1.1 | Set `BLUEZ=1`, Bluetooth off and on | the log has `bluez: Vamp's /usr/lib/vamp-bluez/bluetoothd, pid N` and `bluez: org.bluez up after Ns` (note N s); `ps` shows `bluetoothd` the child of `bt-watch`; `pidof bluetoothd` prints one pid |
| 1.2 | **Discoverable** (the failure of 2026-11-26, [bluez: Not discoverable](bluez.md#not-discoverable)): before step 1.1's Bluetooth on, start `hcidump -X -t > /tmp/hci.txt 2>&1 &` (stock's; the own build has no `btmon`), then Bluetooth on, then open Settings > Bluetooth so demo sets Discoverable. Stop it after 30 s and pull `/tmp/hci.txt`. Then `hciconfig hci0` | `UP RUNNING PSCAN ISCAN`, or, if `ISCAN` is missing: the command whose Command Complete carries a non-zero status just before the `Set Discoverable` failure, written into bluez.md with its status. Run it twice: Bluetooth from the page, and with Bluetooth already on when the key is set |
| 1.3 | `hciconfig hci0 class` after Bluetooth on; then `btmgmt info` is not in the image, so `dbus-send --system --print-reply --dest=org.bluez /org/bluez/hci0 org.freedesktop.DBus.Properties.Get string:org.bluez.Adapter1 string:Class` | the class stock gives (`0x20041c`), so a phone lists the Q2 as audio |
| 1.4 | **Pairing kept**: headset A (paired under stock) switched on | it connects with no pairing prompt; the Bluetooth page lists every paired device as before |
| 1.5 | **A2DP per codec** with stock's bluealsa: AAC, then SBC, then LDAC and aptX HD where the headset has them, each chosen on the Quality page, a song a minute each | audio each time; the codec the Bluetooth page names is the one chosen (Shanling's codec priority is ported, [bluez: Shanling's changes](bluez.md#shanlings-changes)); `q2-bt` log `CODEC` lines |
| 1.6 | **AVRCP**: play, pause, next and previous from headset A; the track's title and play state on a car or a headset that shows them | each acts; title and state shown, and they change with the track (GetElementAttributes, the CHANGED notifications) |
| 1.7 | **Absolute volume**: the headset's volume keys, then the Q2's wheel on Now Playing | the Q2's volume follows the headset's and the headset's the Q2's; `MediaTransport1.Volume` moves (`dbus-send ... Get string:org.bluez.MediaTransport1 string:Volume` on the transport's path) |
| 1.8 | **Reconnect**: headset off and on, three times; out of range and back; the Q2 rebooted with the headset on (the key stays set) | it comes back each time; note how long from on to audio, against the same steps on stock |
| 1.9 | **A new pairing** under ours from the Bluetooth page (headset B, after Forget on it) | the pairing card or none, as on stock; audio plays |
| 1.10 | **The phone app's BLE link** (`[GATT] Server = false`, [bluez: The LE attribute channel](bluez.md#the-le-attribute-channel)): Car Remote on, the phone app connects over BLE | the app finds the Q2's service and controls playback; `/tmp/coda-rc.log` shows the connection; no pairing card |
| 1.11 | **The BLE pairing prompt** ([bluez: The BLE pairing prompt](bluez.md#the-ble-pairing-prompt)), if it appears in 1.10 or on stock: `hcidump -X` running while the app connects | the first SMP packet and who sent it, written into bluez.md |
| 1.12 | The receiver page: a phone plays to the Q2 | the receiver page opens with the phone's title |
| 1.13 | **Memory and CPU**: as step 0.2, under ours | within a few hundred kB of stock's and no more CPU (the first run measured 5,120 kB against 7,136 kB) |
| 1.14 | **Fallback by crashes**: while playing, `kill -SEGV $(pidof bluetoothd)` three times, each once the log says `up after` again | two `W bt bluez: Vamp's daemon ended (status 139), started again` lines, then `E bt bluez: Vamp's daemon ended 3 times in 120s ...; stock's for the rest of this boot`; `/tmp/q2-bt/bluez.fallback` exists; stock's runs (`ls -l /proc/$(pidof bluetoothd)/exe` is `/usr/lib/bluez-stock/bluetoothd`); headset A reconnects |
| 1.15 | **Fallback by not coming up**: Bluetooth off from Settings; then by hand, as demo starts it but detached from the adb shell (which would end it) and with demo's stderr: `setsid sh -c 'bluetoothd & sleep 0.3; kill -STOP $(pidof bluetoothd)' < /dev/null >/dev/null 2>>/tmp/q2-demo.log` | 20 s later `E bt bluez: org.bluez not taken 20s after its start; ...`; stock's owns `org.bluez`; the stopped daemon is gone |
| 1.16 | After a fallback, Bluetooth off and on; then reboot | off and on: stock's directly, no `bt-watch`, no new `Vamp's` line. After the reboot: ours again (step 1.1's lines) |
| 1.17 | **Bluetooth off** from Settings while ours runs | no `bluetoothd` and no `bt-watch` left (`ps`); no `ended` line in the log |

## 2. bluealsa alone (`BLUEALSA=1`, stock's bluetoothd)

Remove `BLUEZ=1` first. Each step names its pair in bluealsa.md's [On a Q2](bluealsa.md#on-a-q2), whose hand
starts the launcher now makes.

| # | Step | Pass |
| --- | --- | --- |
| 2.1 | Set `BLUEALSA=1`, Bluetooth off and on | `bluealsa: Vamp's /usr/lib/vamp-bluealsa/bluealsa, pid N`, `bluealsa: org.bluealsa up after Ns`; `cat /proc/$(pidof bluealsa)/cmdline \| tr '\0' ' '` holds `--shanling`, `-p a2dp-source`, `-p a2dp-sink`, `-p hfp-ag`; `readlink /tmp/q2-bt/libasound_module_pcm_bluealsa.so` is `/usr/lib/vamp-bluealsa/...` |
| 2.2 | demo with the compatibility service: connect headset A | the Bluetooth page names the codec (StatusChange 257); `/tmp/bt_startup` exists |
| 2.3 | **A2DP per codec** as step 1.5, now through ours: q2player plays through our plugin | audio each time; the codec chosen against the Quality page's (codec priority with `RegisterApplication` under stock's bluetoothd, [bluealsa: Risks](bluealsa.md#risks)); LDAC at each Quality setting |
| 2.4 | The Quality page's SBC and LDAC rows on a live link | the next stream's rate changes (`q2-bt` log) |
| 2.5 | **Absolute volume** as step 1.7 | the level moves both ways; bluealsa writes nothing back; no double attenuation (a full-scale song, by ear against stock) |
| 2.6 | An AirPods-like headset that opens the stream itself | the rate it chose, and 44.1 and 48 kHz songs clean ([internals](internals.md#bluetooth-aac)) |
| 2.7 | A headset that reports its battery over HFP | the status bar shows it (`/tmp/q2bt.batt`) |
| 2.8 | Pause 30 s and play; skip quickly through five songs | one AVDTP start for the pause (keep-alive), no gap at a skip worse than stock's |
| 2.9 | `q2guard`'s link monitor on a weak link (walk away with SBC, then AAC) | the `q2-bt` log's STEP lines: stock's step-down by bytes does not reach ours ([bluealsa: Cutover](bluealsa.md#cutover-and-fallback), step 3): expect `STEP` decisions with no effect and no `STEP failed` loop; note what it logs |
| 2.10 | Guest Play: a phone streams to the Q2 and on to headset A | the guest's sink daemon is ours too (`-B sink` under a `bt-watch`), `bluealsa-aplay` is ours (`/tmp/q2-bt/bluealsa.which` is `own`), audio reaches the headset |
| 2.11 | Rockbox from Home with headset A connected | Rockbox plays through `bluealsa` (our plugin) |
| 2.12 | **Fallback**: `kill -SEGV $(pidof bluealsa)` three times as step 1.14 | the same three lines with `bluealsa:`; stock's daemon runs, the links point at `/usr/lib/alsa-lib`, `.which` is `stock`; `killall -9 hciplayer` (note whether demo starts it again at the next play) and audio plays: a client that loaded ours before the fallback keeps it until it starts again ([bluealsa: Cutover](bluealsa.md#cutover-and-fallback), step 2) |
| 2.13 | Memory and CPU as step 0.2 | `VmRSS` and the encoder thread's CPU (`top -H`) against stock's |

## 3. Both (`BLUEZ=1`, `BLUEALSA=1`)

Steps 1.4 to 1.8, 1.10, 1.17 and 2.2 to 2.5 again, then a week of the owner's ordinary use with both keys set.
Each boot's log is read for `E bt` lines; none is the bar for making ours the default
([bluez: Cutover](bluez.md#cutover-and-fallback), step 3).

## 4. Back to stock

| # | Step | Pass |
| --- | --- | --- |
| 4.1 | Remove both keys, Bluetooth off and on | stock's daemons, no `bt-watch`, nothing logged by the launcher |
| 4.2 | Headset A and the one paired in step 1.9 | both connect with no new pairing (the store ours wrote, read by stock's) |
| 4.3 | `diff -r /mnt/data/bluetooth.before-own /mnt/data/bluetooth` | only the records of devices paired or forgotten meanwhile differ, and 5.88's added keys (`LastUsedBearer` and the like, [bluez: The pairing store](bluez.md#the-pairing-store)) |
