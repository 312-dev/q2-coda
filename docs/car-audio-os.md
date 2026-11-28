# Car Audio on Vamp OS

Car Audio ([iphone-mode](iphone-mode.md#car-audio-in-the-image)) is kept in Vamp OS's scope. Vamp OS carries today's pieces unchanged and puts q2sysd where demo was:

| Piece | Today's Vamp | Vamp OS |
| --- | --- | --- |
| The iPhone a car sees, iAP2, the car's buttons, Now Playing, the cover | `q2-iap` (`patch/q2_iap.c`, `iap2_*.c`, `q2_iap_lib.c`) | the same sources, built by Buildroot (`os/package/q2-iap`), `/usr/bin/q2-iap` |
| The gadget and the module | `patch/q2car.sh` as `/usr/bin/q2car`, `/module_driver/usb_f_uac1.ko` | `patch/q2car.sh` as `/usr/libexec/vamp/q2car`, run by `/usr/bin/q2car` with Vamp OS's paths (`Q2CAR_MODULE`, `Q2CAR_PLAYER`, `Q2CAR_IAP_ARGS`); the module tools/uac1.py pins, from the third-party cache (`pins.py uac1`), at `/usr/lib/vamp/usb_f_uac1.ko` |
| Starting it, following the verdict | demo (`patch/usb_ui.c`, `patch/usb_car.c`) at a cable in on Automatic | q2sysd at a cable in on Automatic ([q2sysd: The cable](sysd.md#the-cable)), or its `usb car` ([Q2SYS/1](platform/system.md)): the song paused, `q2car start`; `/tmp/q2car.state` read twice a second: `verdict=car` is a `car` event and the mode stays; any other verdict, once q2car writes `down=1`, is adb again (`usb adb`), except a computer at Automatic's cable, which gets the card (`usb storage`). `usb adb` while shown, or the cable out, runs `q2car stop` |
| The audio | `q2player`'s way 3 while `/tmp/q2-iap.audio` is there | the same: the image's `q2player` is built with `Q2_CAR_USB` |
| The car's buttons and Now Playing | the shim's `/tmp/coda-rc.cmdq` and `/tmp/coda-rc.now`, answered in `/tmp/coda-rc.ack` | `q2playd` reads the same FIFO, answers each command in the same ack file and writes the same now file ([q2playd](playd.md)) |
| The library the car browses | `database.db`, `.q2pod/catalogue.db` | `--lib-db /mnt/data/vamp/library.db --lib-cat /mnt/mmc/.vamp/catalogue.db` |

`q2-iap` parks and unparks adb's gadget itself, as in a `--dev` build.

## What is missing

- **The car's cover**: `q2-iap` looks in Cover Flow's cache (`/mnt/mmc/.coverflow`) and the server covers; q2libd's covers (`/mnt/mmc/.vamp/covers/`, [libd: Covers](libd.md#covers)) are keyed otherwise, so a card song's cover reaches the car only through q2-iap's own ffmpeg path, which needs an `ffmpeg` the image does not have.
- **Shuffle and repeat from the car**: `q2-iap` reads them from the now file and sends the car no way to set them; `q2playd` takes `mode SHUFFLE REPEAT` on the command queue ([playd: The play mode](playd.md#the-play-mode)) should it send one.

## Tests

`test/sysd.py`: `usb car` pauses the song and runs `q2car start`, a `car` verdict is told once and the mode stays, `usb adb` then runs `q2car stop`; a `computer` verdict stays until `down=1` and then is adb with no stop sent. Its cable tests ([q2sysd: The cable](sysd.md#the-cable)) start it from a stand-in `ac/online` on Automatic: a car keeps it until the cable is out, a computer gets the card, a charger only charges. A copy that leaves at any verdict without `down=1` fails it (`Q2SYSD_SOURCE`). `test/os_image.py` checks the image carries `q2-iap`, `q2car` and the module with its pinned SHA-256. q2-iap's own tests (`test/iap_probe.py`) and the player's car tests (`test/player/run.py -k car`) are today's.

## Needs a Q2

All of Car Audio's car and desk results ([iphone-mode](iphone-mode.md)) are from today's Vamp; on Vamp OS the module loading under this init, the iPhone gadget on the controller beside q2adb's park, and a drive.
