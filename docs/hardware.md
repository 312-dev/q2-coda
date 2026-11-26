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
| Touchscreen       | Hynitron CST8xx-family controller (IC type `0xb6`, firmware project 38 v2, checksum `0xb925`; `hyn_touch` V2.9, input `hyn_ts`)     | I²C 3 @ 0x5a                   |
| Touch wheel       | A second Hynitron CST8xx-family controller (the driver's bundled image is IC type `0xb6`; `hyn_slider` V2.25, input `hyn_sliderbar`) | I²C 2 @ 0x15                   |
| Screen            | `lcd_h0209s` panel, 320 × 375, 32-bit framebuffer `jzfb` double-buffered (320 × 750 virtual), 9.3 MHz pixel clock                   |                                |
| Buttons           | `md-gpio-keys` (`keyboard_gpio_add`), plus the AXP2101's power key                                                                  | GPIO                           |
| Wi-Fi + Bluetooth | Realtek RTL8733BS (SDIO `0x024c:0xb733`, `8733bs`), two antennas; Bluetooth over UART H5 (`hci_uart_h5`) on `ttyS0`               | MSC 3 (SDIO), UART 0           |
| microSD           | X2000 SDHCI (ADMA), `mmcblk4`; cards run at SD High Speed (50 MHz bus), not UHS-I; exFAT or FAT32                                   | MSC 4                          |
| Speaker           | Buzzer on PWM channel 1 (pin `PC01`), clicked by the X2000's built-in MCU (`soc_mcu`, `cmd_mcu write_str buzzer`); Linux plays tones on it through `/dev/jz_pwm` (Find My Vamp) |                                |
| USB               | Synopsys DesignWare OTG (`dwc2`, 9 endpoints), high speed (USB 2.0); host drivers for storage, HID core, audio and UVC are built in |                                |
| Clock             | The X2000's RTC (`rtc-ingenic`), with a wake alarm                                                                                  |                                |
| Other             | Watchdog (`soc_watchdog`), SAR ADC (`soc_adc`, four aux channels), PWM, rotator (`soc_rotator`)                                     |                                |

- **Serial console:** `ttyS3`, 115200 8N1, booted `quiet`. Stock runs a login there; Vamp removes it and locks root ([building](building.md#what-the-rootfs-drops)), so the port shows boot messages only.
- **Boot:** the kernel reaches `/linuxrc` 0.57 s in, the UI drivers (screen, touch, DAC, radio) are up by 2.2 s, and the DAC's first stream opens at 44.1 kHz by 5.6 s. The RTC keeps the time across a reboot. UARTs `ttyS0`-`ttyS4` and `ttyS9` exist.
- **Wheel:** stock reads the slider with tslib, position 1-200 a turn plus pressure; Vamp makes a tick every 28 units by default ([internals.md](internals.md)).
- **Jacks:** headphone detect on GPIO `PA07` (3.5 mm) and `PA08` (4.4 mm). The buzzer stays quiet while either is in ([Key Tone](internals.md#key-tone)). Powering the DAC up reads `PA07` unplugged for about 120 ms with a cable in, which the firmware ignores ([Jack detect](internals.md#jack-detect)).
- **DAC channels:** the four CS43131s are two per channel. Stock's `codec_cs43131.ko` writes one register table to the chips at I²C 0x30 and 0x31 and another to 0x32 and 0x33; they differ in PCM Path Signal Control 2 (`0x090004`), whose low two bits are the PCM channel select (Linux's `cs43130.c` `pcm_ch_text`: Left-Right, Left-Left, Right-Left, Right-Right): 0x05, Left-Left, for 0x30 and 0x31, and 0x07, Right-Right, for 0x32 and 0x33. Both tables set the serial port as Linux's I²S defaults do (Clock Config `0x040018` 0x0c, no LRCK inversion; Frame Config `0x040019` 0x0a; channel locations 0), and the machine driver sets the X2000's I²S to `SND_SOC_DAIFMT_I2S | CBS_CFS` (0x4001). So the first sample of each frame, the left one, goes to the 0x30/0x31 pair. Which pair drives which contact of the 3.5 mm and 4.4 mm jacks is the board's wiring, which no file shows.
- **DAC mute:** the mute line lets go 90-100 ms after an unmute, and the volume comes back on the DAC's soft ramp ([CS43131 product page](https://www.cirrus.com/products/cs43131/)).
- **One radio chip, two antennas:** the RTL8733BS also holds Bluetooth's power line (`gpio_bt_dis_n` in `module_driver/rtl8733bs.sh`, `rtl_bt_en` on `PD20` in the kernel's GPIO list). If it fails, Wi-Fi and Bluetooth both go: stock shows **Bluetooth abnormal** and reboots at every start. It brings up two interfaces, `wlan0` and `wlan1`. It is one 2.4 GHz radio, so Wi-Fi and Bluetooth take turns on that band, but each has its own antenna ([Radio and antennas](#radio-and-antennas)).
- **Touch controllers:** both I²C clients are named `hyn,66xx`, which is the name Hynitron's drivers register under, not the part: both drivers also carry CST66xx images (IC types `0xcaca220e` and `0xcaca220c`) that are not for these chips, and the chips report IC type `0xb6`, a CST816-class part (inferred from Hynitron's IC type numbering). Their firmware and how the drivers update it are in [firmware](firmware.md#touch-controllers).
- **Bluetooth transmit power:** every sample of Read Transmit Power Level (current) on the Q2 reads 4 dBm ([link monitor](internals.md#bluetooth-link-monitor), `tpl`), and that is about all the chip has: `rtl8733bs_config` holds a UART entry (offset 0x000c) and two LMP feature bytes (0x008d, 0x008f) and no power entry, so the level is the chip's calibrated default; an RTL8733BS module's FCC filing (FN-Link 6233A-SRB, FCC ID 2AATL-6233A-SRB, RF exposure exhibit of 2024-09) measures BR+EDR at 5.72 dBm peak conducted with a tune-up of 6 ±1 dBm, and LB-Link's BL-M8733BS2 specification gives BR and EDR 2 / 5 / 8 dBm (min / typ / max). The module's print in the [teardown photo](teardown.jpg) reads as FN-Link's 6233A-SRB, but the photo is not sharp enough to be sure, and the module's maximum (Read Transmit Power Level type 1) has not been read from a Q2.
- **Bluetooth version and features** (read from a Q2 on 2026-11-19 with `hciconfig hci0 version`, `hciconfig hci0 features` and `hcitool cmd`): HCI and LMP 5.2, firmware revision 0xd5c8, subversion 0x1143. Classic: EDR 2 Mbps ACL with 3- and 5-slot packets, no 3 Mbps (cleared by `rtl8733bs_config`'s feature bytes, as above), so the largest ACL packet is 2-DH5. LE (Read Local Supported Features, `BD 5F 66 3C 00 00 00 00`): 2M and Coded PHY, extended advertising, data length extension, and Connected Isochronous Stream as central and peripheral (bits 28 and 29), with no isochronous broadcast or sync receiver (bits 30 and 31); LE Read Buffer Size v2 answers 251-byte ACL x 7 and 251-byte ISO x 12. The radio could carry LE Audio unicast (LC3 over CIS), but not Auracast. The stock kernel, 5.10, has no isochronous channel support (Linux added its ISO socket in a later release), and Vamp never replaces the kernel, so LE Audio is out of reach; classic A2DP is the only Bluetooth audio path.
- **Battery reporting:** two gauges. The CellWise CW2015 (`cw2015_driver.ko`, `/sys/devices/i2c-0/0-0062/cw2015_capacity` and `cw2015_voltage`) is the one stock and the firmware use: stock's `toolsGetBatterycapacity` (`0x5cf49c`) reads it into `g_power_capacity`, which the status row, Battery History, Charge Limit and the low-battery warning and power-off (`check_lowbattery_state`, `0x4f3b38`) all read. The AXP2101 PMIC's `battery` supply (`/sys/class/power_supply/battery`) has a gauge of its own that nothing reads and that is misconfigured: its `energy_full_design` reads 4000 against the 1850 mAh cell, and on 2026-11-16 it said 12% while the CW2015 said 61% at 3.88 V.
- **USB:** the card is exported through the kernel's `f_mass_storage` in a configfs gadget. The stock kernel has no `hid-generic` (nor `hidraw`), so no USB HID device gets an input node and USB keyboards are not seen; a USB-C headset's buttons are read from its HID interface through usbfs instead ([internals: USB headset buttons](internals.md#usb-headset-buttons)).
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

## Buses and pins

Read on a Q2 running Vamp on 2026-11-25, every read non-destructive: `/sys/bus/i2c/devices`, an `i2cdetect -y -r` of every bus, `/sys/kernel/debug/gpio` and `pinctrl/*/pinmux-pins`, `devmem` of the GPIO and CPM registers, `/proc/interrupts`. The bus parameters are from stock's `module_driver/soc_i2c.sh` and the device tree in the kernel image. The Q2 has `i2cdetect`, `i2cget`, `i2cset`, `i2cdump`, `i2ctransfer`, `devmem`, `lsusb`, `cmd_gpio` and `cmd_i2c` in stock's rootfs, so nothing needs shipping to repeat any of it.

### I²C

| Bus | Pins | Clients | Scan, 2026-11-25 |
| --- | ---- | ------- | ---------------- |
| 0 | `PC13` SCL, `PC14` SDA, 200 kHz | 0x62 CW2015 (`cw2015`) | 0x62 only |
| 1 | `PD11`, `PD12` | none: disabled (`i2c1_is_enable=0`) | no adapter |
| 2 | `PB22`, `PB23` | 0x15 touch wheel (`hyn,66xx`, `hyn_sliderbar`) | 0x15 only |
| 3 | `PA16`, `PA17` | 0x5a touchscreen (`hyn,66xx`, `hyn_ts`) | 0x15 busy, nothing else answers (below) |
| 4 | `PD00`, `PD01` | none: disabled; `PD01` is the Wi-Fi enable | no adapter |
| 5 | `PD04`, `PD05`, 400 kHz | 0x31 `4xcs43131`, which also writes 0x30, 0x32 and 0x33 | 0x31 only, with the DACs unpowered |
| 6 | `i2c-gpio` (device tree `i2c@0`): `PC24` SDA, `PC23` SCL, 2 µs delay | 0x34 AXP2101 | 0x34 only |

- **No other chip is on any bus**, so the Type-C controller has no I²C ([USB port](#usb-port)). A scan sees only powered chips: bus 5's other three CS43131s did not answer because `dac_en` (`PB01`) was low. A Type-C controller is powered whenever the port can be used, so that does not hide one. The scans cover 0x03 to 0x77, every address the candidate Type-C parts can be strapped to.
- **Bus 3's scan marks 0x15 busy and gets no answer at 0x5a** (the kernel logs `i2c3 TXABRT ... ABRT_7B_ADDR_NOACK` for each address probed), though the touchscreen's client is `3-005a`. The likeliest reading, not checked, is that `hyn_touch` moves its client to 0x15, where CST8xx parts answer in normal operation, so the busy mark follows it. `i2cget -f -y 3 0x15 0xa7` (a read) would settle it.
- **Bus 6 is shared**: the PMIC's driver, `q2guard` (`patch/q2guard_pmic.c`, `I2C_SLAVE_FORCE`) and the [USB tracer](internals.md#usb-tracer) read it. One SMBus byte read is about 0.3 ms of busy-waiting at its 100 to 125 kHz (an estimate from the 2 µs delay). Reads only from anything of ours: a write races the kernel's driver.

### GPIO

`gpiochipN` is port A to E for N 0 to 4, 32 lines each, so `gpio-114` is `PD18`. The registers are at `0x10010000` plus `0x100` a port: `PIN` 0x00, `INT` 0x10, `MSK` 0x20, `PAT1` 0x30, `PAT0` 0x40, `FLG` 0x50, `EDG` 0x70, `PU` 0x80, `PD` 0x90 ([X2000 programming manual](https://whycan.com/files/members/3946/X2000_PM_20200515.pdf), chapter 27). A pin's mode is its `INT`, `MSK`, `PAT1` and `PAT0` bits: `INT` 1 is an interrupt; `INT` 0 and `MSK` 1 a GPIO, an input with `PAT1` 1, else an output at `PAT0`'s level; `INT` 0 and `MSK` 0 a device function, numbered by `PAT1:PAT0`. That reading agrees with every pin of known use read on 2026-11-25: I²C 0's `PC13` and `PC14` a device function, `PC20` an output at 0, `PC23` and `PC24` outputs at 1, `PD17` an interrupt.

The lines the kernel lists as claimed (`/sys/kernel/debug/gpio`):

| Pin | Name | Pin | Name |
| --- | ---- | --- | ---- |
| `PA12` | `msc2_pwr` (output) | `PC00` | `pwm0` |
| `PA13` | `gpio_lcd_rst` (output) | `PC05`-`PC08` | `i2s1-tx-bclk`, `-lrck`, `-data`, `-mclk` |
| `PA14`, `PA15` | touchscreen reset, interrupt | `PC21` | `axp_irq` ([below](#the-pmics-interrupt-line)) |
| `PB01` | `dac_en` | `PD01` | `rtl_wifi_en` |
| `PB02` | `vcp_en` | `PD08`-`PD13` | `msc1_clk`, `_cmd`, `_d0`-`_d3` |
| `PB09` | `dac_mode` | `PD17` | `q2-usb-cable` (`patch/usb_event.c`) |
| `PB10` | `po_mute` | `PD20` | `rtl_bt_en` |
| `PB14` | `dac_rst` | `PE00`-`PE05` | `msc2_clk`, `_cmd`, `_d0`-`_d3` |
| `PB16`, `PB17`, `PB18` | Previous, Down, Up keys | `PE23` | `clk32k_out_o` |
| `PB20`, `PB21` | wheel reset, interrupt | `PE26`, `PE31` | Next key, power key |

Pins stock reads or drives through `soc_gpio.ko`'s ioctl (`/dev/gpio`, `toolsGpioGetValue` and `toolsGpioSetValue`, which write the registers without gpiolib) are not in that list: the headphone sense pins `PA07` and `PA08`, `PC20`, `PE22` and `PD18` among them. demo names only `PD17`, `PD18`, `PD20` and `PD22` to `PD26` in port D.

### USB port

The port's state is spread over a Type-C controller's ID output, a VBUS sense, the PMIC and the SoC's OTG block; [usb.md](usb.md) is how the firmware reads them and what goes wrong. The signals:

| Signal | Pin or register | What it carries | Known from |
| ------ | --------------- | --------------- | ---------- |
| VBUS present | `PD17`, input, no pull | low while VBUS is above a low threshold; low too while the Q2 powers the bus and for about 1.6 s after it stops | measured |
| ID | `PD18`, input, pull-up (`S12usbcharge`) | the Type-C controller's ID output, on one net with the SoC's OTG ID pad (the dwc2's `id_status` follows it); low while the controller has the Q2 attached as source, that is a sink is on the port | measured |
| `PD19` | input, pull-up on | read 1 on every record so far; the controller's audio-accessory output if it has one and it is wired | inferred; a passive USB-C to 3.5 mm adapter settles it |
| `PD16` | device function 0, no pull, unclaimed | read 0 with a source attached; either the controller's "attached as sink" output or a floating pin | inferred; one read with the cable out settles it |
| `PD14`, `PD15` | device function 0, unclaimed | read 0 | not known |
| Bus power | `PC20`, output | turns on 5 V onto VBUS from a supply apart from the PMIC, which has no boost; only demo drives it (`check_otg_status`, `0x4f3408`), gated by [OTG host power](internals.md#otg-host-power) | code; the supply inferred |
| Charging off | `PE22`, output | set with `PC20`, so the Q2 does not charge from its own 5 V | code |
| `DRV_VBUS` | `PC17` | the X2000's OTG VBUS drive pin; unclaimed, unused | the manual |
| VBUS good | AXP2101 `00h` bit 5 (`power_supply/usb/online`) | VBUS above the PMIC's threshold: 1 for every real source, 0 for the Q2's own dying 5 V and for back-fed residue | measured |
| PMIC interrupt | `PC21` (`axp_irq`) | nothing ([below](#the-pmics-interrupt-line)) | measured |

#### The Type-C controller

What it is, from behaviour: a dual-role (DRP) controller with its own debounce. A laptop once resolved the Q2 as the source, which fixed Rd resistors cannot do, and USB-C headphones bring `PD18` low though a Type-C plug has no ID contact. It is pin-strapped, with no digital interface the SoC can reach:

- no chip answers on any I²C bus (above);
- no Type-C driver, part name or address is in stock's rootfs (every binary, library and the 24 vendor modules), the kernel's strings or the device tree; the only matches for `typec`, `tcpc`, `fusb30`, `tusb32`, `husb3`, `wusb38`, `rt1711`, `sgm7220` and the like are the vendor PHY's unused `id_detect` and `vbus_detect` names;
- the kernel has no `typec` class (`/sys/class/typec` is absent), and `/sys/class/extcon` and `/sys/class/usb_role` are empty.

The part is not identified: a 12-pin package beside the port, which the teardown photo does not resolve. Every candidate pin-strapped DRP part works the same way in its GPIO mode, where the I²C pins become outputs and the part has no input a host can talk to; I²C is strapped on or off, and on it would have answered the scan:

| Part | GPIO-mode outputs (open drain unless noted) |
| ---- | ------------------------------------------- |
| TI TUSB320, TUSB320LAI; SG Micro SGM7220 | ID (attached as source), OUT1/OUT2 (attached as sink and the source's current), OUT3 (audio accessory, low) |
| TI TUSB321 | ID, OUT1/OUT2, DIR (orientation), VCONN_FAULT; no accessory output, no I²C |
| WillSemi WUSB3801 | ID, INOUT1/INOUT2 (`01` nothing, `11` default, `10` 1.5 A, `00` 3 A), OUT3 (audio accessory) |
| Hynetek HUSB320 | ID, OUT1/OUT2, OUT3, ORIENT and DEBUG_N (both push-pull) |

So, whichever it is: `PD18` is ID (confirmed); orientation, the source's current, an audio accessory and a debug accessory are readable only if one of those outputs reaches a SoC pin, and `PD16` and `PD19` are where to look; and the Q2 cannot ask to be the sink (Try.SNK), so a dual-role host can still resolve it as source. The settling measurement is a sharp photo of the part's marking and a meter from its pins to `PD16`, `PD19`, VBUS and the test pads; unclaimed pins that read idle (`PD14`, `PD15`, `PD21`, `PD27` to `PD29`) are where a bodge wire would go, after a meter shows them free.

#### The AXP2101

Read with `i2cget -f -y 6 0x34 REG` on 2026-11-25, cable in, charging:

| Register | Value | Meaning (AXP2101 datasheet) |
| -------- | ----- | --------------------------- |
| `00h` | `0x38` | bit 5 VBUS good; battery present |
| `01h` | `0x33` | bits 6:5 charging, bits 2:0 constant voltage |
| `03h` | `0x4a` | chip ID |
| `16h` | `0x05` | input current limit, bits 2:0 (100, 500, 900, 1000, 1500, 2000 mA): 2 A, from the device tree's `pmu_usbpc_cur = 2000`, for every source |
| `40h`-`42h` | `0x00 0x03 0x00` | IRQ enables: only the power key's two edges (`41h` bits 1, 0) |
| `48h`-`4Ah` | `0x00 0x8b 0x08` | IRQ status: `49h` bit 7 VBUS insert, bits 3, 1, 0 the power key; `4Ah` bit 3 charge start |

- A status bit is cleared by writing 1 to it. The kernel's IRQ number for a bit is `8 × (REG − 0x40) + bit`: VBUS insert is 15, remove 14, battery insert and remove 13 and 12, the power key's falling and rising edges 9 and 8 (`axp20x-pek-dbf`, `-dbr`), charge done 20, charge start 19.
- **`49h` bits 7 and 6 latch the last VBUS transition with the interrupt off.** Insert sets when VBUS rises past 3.75 V (VVBUS_UVLOZ) and is cleared by a remove, and the other way round. Read at a `PD17` edge, they tell a real source from residue that never reaches 3.75 V, beside `00h` bit 5.
- No BC1.2 detection (none in the datasheet; the AXP2202, which the vendor driver family also covers, has it), and no boost converter.

#### The PMIC's interrupt line

No PMIC interrupt reaches the kernel, measured on 2026-11-25. The vendor's `axp20x_i2c_probe` requests `PC21` as `axp_irq` from a constant compiled into it (the device tree's `pmu@34` has no `interrupts`) and builds `axp2101_irq_chip` on it (IRQ 72, with the power key's 81 and 82). Through four presses of the power key all three counted 0, while `41h` enabled the power key's bits and `49h` held them pending, which on the AXP2101 pulls its open-drain IRQ pin low; `PC21` read high, and its registers read `INT` 0, `MSK` 0, `PAT1` 1, `PAT0` 0 (a device function, not an interrupt) with `FLG` bit 21 clear. The power key works anyway, through `PE31` (`KEY_POWER`). Whether the PMIC's IRQ pin is wired to `PC21` at all is not known; the pad reading high with bits pending suggests not. A test that settles it, with its risk, is W1 in [usb.md](usb.md#on-the-device). The charger driver's VBUS and charge handlers exist and are never requested ([inefficiencies](inefficiencies.md#event-sources-on-this-hardware)).

#### The OTG block

The X2000's OTG controller is a Synopsys dwc2 (`ingenic,x2000-dwc2-hsotg`, `dr_mode = "otg"`) with Ingenic's PHY (`ingenic,usbphy-x2000`). The device tree gives it no `usb-role-switch`, no extcon and none of the vendor PHY driver's GPIO properties (`ingenic,id-dete`, `ingenic,vbus-dete`, `ingenic,drvvbus`, `ingenic,usb-switch`), so the kernel knows no ID pin, no VBUS pin and no VBUS regulator (`supply vusb_a not found, using dummy regulator` at boot). It switches role by itself on the ID pad (`dwc2_conn_id_status_change`, which sends no uevent). Read on 2026-11-25 with a source attached and the gadget configured:

| Register | Value | Bits that matter |
| -------- | ----- | ---------------- |
| `USBPCR` `0x1000003c` | `0xE09919B8` | 31 OTG mode; **30 `AVLD_REG` 1**, A-session forced valid; 29:28 `10`, the ID pull-up always on (`otg_hold` sets bit 29) |
| `USBRDT` `0x10000040` | `0x03800096` | **24 `IDDIG_EN` 1 and 23 `IDDIG_REG` 1: the role forced to device by software**, the write `otg_hold` makes (`OTG_HOLD` in `patch/usb_otg.c`); clearing both gives the role back to the pad; 25 `VBFIL_LD_EN` |
| `USBVBFIL` `0x10000044` | not read | 31:16 the ID filter, 15:0 the VBUS filter |
| `USBPCR1` `0x10000048` | `0xFDC23360` | **31 `BVLD_REG` 1**, B-session forced valid |
| dwc2 `GOTGCTL` `0x13500000` | `0x000d00fc` | 19 `BSesVld`, 18 `ASesVld`, 16 `ConIDSts` all 1; **7:2 all set: the A-valid, B-valid and VBUS-valid overrides enabled, each valued 1** |
| `GUSBCFG`, `GINTSTS`, `DSTS`, `HPRT0` | `0x0000140f`, `0x04008028`, `0x0024f900`, `0` | device mode, configured |

- **The controller never sees VBUS leave.** Session valid is forced in both the CPM (`AVLD_REG`, `BVLD_REG`) and `GOTGCTL`'s overrides, so after an unplug the UDC reads `suspended`, not disconnected. `echo disconnect > /sys/class/udc/13500000.otg/soft_connect` gives the gadget's functions a real disconnect from userspace: in 5.10.21 it calls the gadget driver's `disconnect()`. The X2000 has no `USBPCR_VBUSVLDEXT`; that bit is the JZ4770's and JZ4780's.
- **Shared writers.** The vendor PHY driver rewrites `USBPCR` at init and resume, and the dwc2 driver writes `GOTGCTL`; a `devmem` read-modify-write of either races them. `soft_connect` and `USBRDT` are the safe handles.
- The dwc2's debugfs (`mount -t debugfs none /sys/kernel/debug`) has `regdump`, `params` (read-only in 5.10: `phy_type` 1, `external_id_pin_ctl` 0, HNP and SRP 1, host DMA), `hw_params`, `dr_mode` and `state`; its sysfs `id_status`, `dwc2_mode` and `connected` are read-only. The UDC's `state` sends `sysfs_notify`, so `poll(POLLPRI)` on it works.
- What the PHY's own VBUS input does is not known: the manual documents a VBUS pin ("leave floating if not used"); W3 in [usb.md](usb.md#on-the-device) settles it.

### Radio and antennas

- **Two antennas.** The board has two U.FL sockets silk-screened `WIFI` and `BT`, each with a coax to its own flex antenna on the plastic back cover: Bluetooth's (`YJ-BT-Q2-..`) at the top, stuck to the face of the battery; Wi-Fi's (`YJ-Q2-WIFI-..02`) at the bottom ([teardown photo](teardown.jpg)). The Wi-Fi driver's efuse reading agrees: `Ant PG Num/ Mech/ Pos/ RFE/ Dist = 2/ Non-Shared/ Main / 5/ 10` (`/proc/net/rtl8733bs/wlan0/btcoex`, 2026-11-25).
- **What that means for range.** A hand over the back, or the back against the body, sits on the Bluetooth antenna, and the battery's foil under it likely detunes and shields it (inferred). Nothing in software recovers that.
- **Coexistence.** It is still one 2.4 GHz radio. Realtek's coex in the Wi-Fi driver shares the band by time division (TDMA) and by an AFH map that keeps Bluetooth off the Wi-Fi channel. On 2026-11-25, with Wi-Fi joined on 2.4 GHz channel 1 and idle and Bluetooth connected but idle, it ran TDMA case 3 with Bluetooth slots of 44 to 45 ms and told the controller to avoid channel 1; the Q2 was the link's master. On 5 GHz Realtek's coex turns TDMA off. The coex's versions, how to read it and what it means for dropouts are in [bluetooth.md](bluetooth.md#coexistence).

### Wi-Fi faults

Two failures seen on the owner's Q2 on 2026-11-25, recorded here as observations; the [Wi-Fi fault recorder](internals.md#wi-fi-fault-recorder) (`q2-wifi` log) is there to catch the next of each with the state the logs then lacked.

- **The driver never came up** (09:26 to 14:20 local, five boots in a row, among them a reinstall of an older build and a manual `rmmod` and reload of the driver). About 10 s after `rtk_wlan_power_on`, `insmod` of `8733bs` (`/module_driver/rtl8733bs.sh`, run by `driver_default_init_script.sh` at boot) failed with `insmod: vmalloc: allocation failure: 0 bytes, mode:0xcc0(GFP_KERNEL)` in `_rtw_zvmalloc`, called from `_rtw_init_mlme_priv` < `rtw_init_drv_sw` < `rtw_sdio_primary_adapter_init` < `rtw_drv_init`; no `wlan0`, so the unified log held only `Failed to connect to wpa_supplicant` and `Could not read interface wlan0 flags: No such device`. A reboot did not clear it; a full power-off did. In Linux 5.10's `__vmalloc_node_range` (mainline's source; Shanling's kernel source is not public) that message prints the size asked for, and a request of 0 bytes fails there before any memory is looked for, so the driver most likely asked for nothing, a size it computed as 0, rather than for memory the kernel lacked (inferred). The chip's power is not cut by a reboot, only by a power-off, which fits a state held in the RTL8733BS that the driver reads at load (inferred, not shown).
- **The radio went deaf** (boot 20:31 UTC, `wlan0` up). `wpa_supplicant` saw 12 to 16 networks for about 3 minutes and stayed `DISCONNECTED` without joining the saved one; then every scan for 40 minutes returned none (the join watch: `wifi not joined N s after wpa_supplicant started: wpa_state=DISCONNECTED, 0 networks in its last scan, rescans every 5 s (not held), switch 1 scan 0 connect 0`); then at 21:14:49 it associated on its own, on 5 GHz (5240 MHz), and got a lease. No Bluetooth link was up. Nothing else in the logs explains it.

## Firmware

The version of every component's firmware and software on the Q2, against the newest public, and what can be updated: [firmware.md](firmware.md).

## Build tolerances and warranty

From Shanling, in reply to owners' reports (Vamp Discord, 2026-11-13):

- **A Center button that wiggles or rattles is normal.** The wheel and the Center button come from two different suppliers, and the tolerance lets the button move slightly. Shaken near the ear, nearly every unit rattles a little or more. Shanling does not treat it as a warranty fault.
- **A sticking wheel is a fault.** Amazon has seen a few cases, caused by the mounting tape under the button not being aligned. It is covered under warranty.

## Opening it

- [Replacing the battery](battery.md), with Shanling's tips and a step-by-step PDF.
- [Teardown photo](teardown.jpg).
- If a Q2 won't start, use Shanling's recovery package ([Restoring stock firmware](../README.md#restoring-stock-firmware)).
