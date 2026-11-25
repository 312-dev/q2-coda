# Q2 hardware

What's known about the Shanling Q2's hardware: Shanling's published specs, what the stock firmware shows of the parts inside, and what Shanling has said about build tolerances and warranty.

## Specifications

Shanling's figures, as retailers and the launch press list them ([Audiophonics](https://www.audiophonics.fr/en/dap-digital-audio-player-hifi/shanling-q2-black-p-22390.html), [Phile-web](https://www.phileweb.com/sp/news/d-av/202609/18/65987.html)).

|              |                                                                          |
| ------------ | ------------------------------------------------------------------------ |
| SoC          | Ingenic X2000                                                            |
| RAM          | 128 MB, no swap                                                          |
| DAC          | 4 × Cirrus Logic CS43131                                                 |
| Decoding     | PCM to 768 kHz / 32-bit, DSD512                                          |
| Screen       | 2.09 in touchscreen, 320 × 375                                           |
| Controls     | Touch wheel, Center button, four side buttons                            |
| Storage      | microSD, up to 2 TB; no user storage inside                              |
| Battery      | 1850 mAh: 13.5 h on 3.5 mm, 12.5 h on 4.4 mm, 16.5 h on Bluetooth (LDAC) |
| Bluetooth    | 5.2, send and receive; LDAC, aptX HD, aptX, AAC, SBC                     |
| Wi-Fi        | Yes (AirPlay, DLNA in stock firmware)                                    |
| USB          | USB-C: charging, USB DAC to 384 kHz / 32-bit, digital out                |
| Size, weight | 80 × 50 × 13.5 mm, 83 g; aluminium and glass                             |
| Colors      | Smoke gray, silver, orange                                               |

| Output                        | 3.5 mm               | 4.4 mm balanced      |
| ----------------------------- | -------------------- | -------------------- |
| Power @ 32 Ω, low / high gain | 30 / 120 mW          | 120 / 425 mW         |
| Line out                      | 1.9 Vrms             | 3.8 Vrms             |
| SNR, dynamic range @ 32 Ω     | 125 dB               | 130 dB               |
| THD+N @ 32 Ω                  | 0.0005%              | 0.0006%              |
| Crosstalk @ 32 Ω              | 65 dB                | 110 dB               |
| Output impedance              | 1.4 Ω                | 1.8 Ω                |
| Frequency response            | 20 Hz–40 kHz (±1 dB) | 20 Hz–40 kHz (±1 dB) |

## Inside, as the device reports it

Read off a Q2 running stock V1.36's kernel (`/proc`, `/sys`, the device tree) and from the stock rootfs; [internals.md](internals.md) has the detail behind the firmware lines.

| Part              | Chip, driver                                                                                                                        | Bus                            |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| SoC               | Ingenic X2000: two XBurst II cores at 1.2 GHz (APLL), DDR clock 750 MHz; Linux 5.10.21                                              |                                |
| RAM               | 128 MB, no swap                                                                                                                     |
| Flash             | 128 MB SPI NAND, ID `e5 71` (Dosilicon's ID, as on the DS35Q1GA), on the X2000's SFC: 2 KB pages, 64 B OOB, 128 KB blocks           | SFC                            |
| DAC               | 4 × CS43131, one I²C device `4xcs43131` (`codec_cs43131.ko`, `/dev/shanling_dac`); ALSA card `ecodec-sound-card`, one playback PCM  | I²C 5 @ 0x31, I²S 1            |
| Power             | X-Powers AXP2101 PMIC (chip ID `0x4a`), charger and power key (`axp2101-pek`)                                                       | I²C 6 (GPIO bit-banged) @ 0x34 |
| Fuel gauge        | CellWise CW2015 (`cw2015_driver`)                                                                                                   | I²C 0 @ 0x62                   |
| Touchscreen       | Hynitron CST8xx-family controller (IC type `0xb6`, firmware project 38 v2; `hyn,66xx`, `hyn_touch` V2.9, input `hyn_ts`)            | I²C 3 @ 0x5a                   |
| Touch wheel       | A second Hynitron CST8xx-family controller (driver firmware IC type `0xb6`; `hyn,66xx`, `hyn_slider` V2.25, input `hyn_sliderbar`)  | I²C 2 @ 0x15                   |
| Screen            | `lcd_h0209s` panel, 320 × 375, 32-bit framebuffer `jzfb` double-buffered (320 × 750 virtual), 9.3 MHz pixel clock                   |                                |
| Buttons           | `md-gpio-keys` (`keyboard_gpio_add`), plus the AXP2101's power key                                                                  | GPIO                           |
| Wi-Fi + Bluetooth | Realtek RTL8733BS (SDIO `0x024c:0xb733`, `8733bs`); Bluetooth over UART H5 (`hci_uart_h5`)                                          | MSC 3 (SDIO), UART             |
| microSD           | X2000 SDHCI (ADMA), `mmcblk4`; cards run at SD High Speed (50 MHz bus), not UHS-I; exFAT or FAT32                                   | MSC 4                          |
| Speaker           | Buzzer on the X2000's built-in MCU (`soc_mcu`, `cmd_mcu write_str buzzer`)                                                          |                                |
| USB               | Synopsys DesignWare OTG (`dwc2`, 9 endpoints), high speed (USB 2.0); host drivers for storage, HID core, audio and UVC are built in |                                |
| Clock             | The X2000's RTC (`rtc-ingenic`), with a wake alarm                                                                                  |                                |
| Other             | Watchdog (`soc_watchdog`), SAR ADC (`soc_adc`, four aux channels), PWM, rotator (`soc_rotator`)                                     |                                |

- **Serial console:** `ttyS3`, 115200 8N1, booted `quiet`. Stock runs a login there; Q2 Muse removes it and locks root ([building](building.md#what-the-rootfs-drops)), so the port shows boot messages only.
- **Boot:** the kernel reaches `/linuxrc` 0.57 s in, the UI drivers (screen, touch, DAC, radio) are up by 2.2 s, and the DAC's first stream opens at 44.1 kHz by 5.6 s. The RTC keeps the time across a reboot. UARTs `ttyS0`-`ttyS4` and `ttyS9` exist.
- **Wheel:** stock reads the slider with tslib, position 1-200 a turn plus pressure; Q2 Muse makes a tick every 28 units by default ([internals.md](internals.md)).
- **Jacks:** headphone detect on GPIO `PA07` (3.5 mm) and `PA08` (4.4 mm). The buzzer stays quiet while either is in ([Key Tone](internals.md#key-tone)).
- **DAC channels:** the four CS43131s are two per channel. Stock's `codec_cs43131.ko` writes one register table to the chips at I²C 0x30 and 0x31 and another to 0x32 and 0x33; they differ in PCM Path Signal Control 2 (`0x090004`), whose low two bits are the PCM channel select (Linux's `cs43130.c` `pcm_ch_text`: Left-Right, Left-Left, Right-Left, Right-Right): 0x05, Left-Left, for 0x30 and 0x31, and 0x07, Right-Right, for 0x32 and 0x33. Both tables set the serial port as Linux's I²S defaults do (Clock Config `0x040018` 0x0c, no LRCK inversion; Frame Config `0x040019` 0x0a; channel locations 0), and the machine driver sets the X2000's I²S to `SND_SOC_DAIFMT_I2S | CBS_CFS` (0x4001). So the first sample of each frame, the left one, goes to the 0x30/0x31 pair. Which pair drives which contact of the 3.5 mm and 4.4 mm jacks is the board's wiring, which no file shows.
- **DAC mute:** the mute line lets go 90-100 ms after an unmute, and the volume comes back on the DAC's soft ramp ([CS43131 product page](https://www.cirrus.com/products/cs43131/)).
- **One radio module:** the RTL8733BS also holds Bluetooth's power line (`gpio_bt_dis_n`, `module_driver/rtl8733bs.sh`). If it fails, Wi-Fi and Bluetooth both go: stock shows **Bluetooth abnormal** and reboots at every start. It brings up two interfaces, `wlan0` and `wlan1`.
- **Bluetooth transmit power:** every sample of Read Transmit Power Level (current) on the Q2 reads 4 dBm ([link monitor](internals.md#bluetooth-link-monitor), `tpl`), and that is about all the chip has: `rtl8733bs_config` holds a UART entry (offset 0x000c) and two LMP feature bytes (0x008d, 0x008f) and no power entry, so the level is the chip's calibrated default; an RTL8733BS module's FCC filing (FN-Link 6233A-SRB, FCC ID 2AATL-6233A-SRB, RF exposure exhibit of 2024-09) measures BR+EDR at 5.72 dBm peak conducted with a tune-up of 6 ±1 dBm, and LB-Link's BL-M8733BS2 specification gives BR and EDR 2 / 5 / 8 dBm (min / typ / max). Which module the Q2 carries is not known, and its maximum (Read Transmit Power Level type 1) has not been read from a Q2.
- **Bluetooth version and features** (read from a Q2 on 2026-11-19 with `hciconfig hci0 version`, `hciconfig hci0 features` and `hcitool cmd`): HCI and LMP 5.2, firmware revision 0xd5c8, subversion 0x1143. Classic: EDR 2 Mbps ACL with 3- and 5-slot packets, no 3 Mbps (cleared by `rtl8733bs_config`'s feature bytes, as above), so the largest ACL packet is 2-DH5. LE (Read Local Supported Features, `BD 5F 66 3C 00 00 00 00`): 2M and Coded PHY, extended advertising, data length extension, and Connected Isochronous Stream as central and peripheral (bits 28 and 29), with no isochronous broadcast or sync receiver (bits 30 and 31); LE Read Buffer Size v2 answers 251-byte ACL x 7 and 251-byte ISO x 12. The radio could carry LE Audio unicast (LC3 over CIS), but not Auracast. The stock kernel, 5.10, has no isochronous channel support (Linux added its ISO socket in a later release), and Q2 Muse never replaces the kernel, so LE Audio is out of reach; classic A2DP is the only Bluetooth audio path.
- **Battery reporting:** two gauges. The CellWise CW2015 (`cw2015_driver.ko`, `/sys/devices/i2c-0/0-0062/cw2015_capacity` and `cw2015_voltage`) is the one stock and the firmware use: stock's `toolsGetBatterycapacity` (`0x5cf49c`) reads it into `g_power_capacity`, which the status row, Battery History, Charge Limit and the low-battery warning and power-off (`check_lowbattery_state`, `0x4f3b38`) all read. The AXP2101 PMIC's `battery` supply (`/sys/class/power_supply/battery`) has a gauge of its own that nothing reads and that is misconfigured: its `energy_full_design` reads 4000 against the 1850 mAh cell, and on 2026-11-16 it said 12% while the CW2015 said 61% at 3.88 V.
- **USB:** the card is exported through the kernel's `f_mass_storage` in a configfs gadget. The stock kernel has no `hid-generic`, so USB keyboards and remotes are not seen.
- **Manuals:** the [X2000 programming manual](https://whycan.com/files/members/3946/X2000_PM_20200515.pdf) covers the SoC's registers ([mcu-sleep.md](mcu-sleep.md#hardware-basis), [clock-gating.md](clock-gating.md)).

### Flash layout

As `/proc/mtd` lists it: a second kernel and a smaller second rootfs sit beside the main pair.

| mtd | Name       | Size     | Use                                                         |
| --- | ---------- | -------- | ----------------------------------------------------------- |
| 0   | `uboot`    | 1 MB     | Bootloader                                                  |
| 1   | `kernel`   | 6 MB     | Kernel                                                      |
| 2   | `rootfs`   | 75 MB    | Root filesystem, squashfs, read-only (46.4 MB used); the recovery erases it whole and `nandwrite`s the image with no size check, so builds stop at 72 MiB (`ROOTFS_ROOM`), 24 of its 600 blocks spare for bad ones |
| 3   | `kernel2`  | 6 MB     | Second kernel                                               |
| 4   | `rootfs2`  | 12 MB    | Second rootfs                                               |
| 5   | `userid`   | 128 KB   |                                                             |
| 6   | `ota`      | 128 KB   | Update state                                                |
| 7   | `userdata` | 24.75 MB | UBIFS `user_data`, mounted at `/mnt/data` (17.7 MB), `sync` |

## Build tolerances and warranty

From Shanling, in reply to owners' reports (Q2 Muse Discord, 2026-11-13):

- **A Center button that wiggles or rattles is normal.** The wheel and the Center button come from two different suppliers, and the tolerance lets the button move slightly. Shaken near the ear, nearly every unit rattles a little or more. Shanling does not treat it as a warranty fault.
- **A sticking wheel is a fault.** Amazon has seen a few cases, caused by the mounting tape under the button not being aligned. It is covered under warranty.

## Opening it

- [Replacing the battery](battery.md), with Shanling's tips and a step-by-step PDF.
- [Teardown photo](teardown.jpg).
- If a Q2 won't start, use Shanling's recovery package ([Restoring stock firmware](../README.md#restoring-stock-firmware)).
