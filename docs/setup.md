# Q2 setup guide

For a brand new Q2. You need the Q2, its SD card, a USB-C cable and a computer with internet. The [user guide](https://github.com/DiamondBond/q2-pod/releases/latest/download/Q2-Pod-User-Guide.pdf) has the full walkthrough, with what you should see at each step.

## 1. Install Vamp

1. Download the latest `update.tar` here: https://github.com/DiamondBond/q2-pod/releases/latest.
2. Copy `update.tar` onto the card (the main folder, not inside a folder). Eject the card and unplug the Q2.
3. Put the card in the Q2 and turn it on.
4. On the Q2: **Settings → General → Software Update**, then **Install**. Leave the card in until the Q2 restarts. Do not unplug it while it updates.

After the restart the Q2 opens on Vamp's Home. Then follow **Start here: play your first song** in the [user guide](https://github.com/DiamondBond/q2-pod/releases/latest/download/Q2-Pod-User-Guide.pdf): plug in headphones, copy your music, then choose **Library → Update Local Music**.

## 2. Optional: Spotify

Spotify needs Spotify Premium and a separate add-on, not included in Vamp.

1. Download `q2-librespot-*.zip` here: https://github.com/DiamondBond/q2-librespot/releases/latest
2. Unzip it onto the card's main folder. It adds a `.spotify` folder.
3. On the Q2, open **Streaming → Spotify**, then pick **Q2** in the Spotify app on a phone on the same Wi-Fi.

Or, with the Q2 on Wi-Fi, skip steps 1 and 2: **System settings → App Store → Spotify** downloads and installs it.

## 3. Optional: Rockbox

Rockbox is a separate player and is not needed for Vamp. Skip this step unless you want it.

1. Download `rockbox.zip` here: https://github.com/DiamondBond/q2-rockbox/releases
2. Unzip it onto the card (the main folder). It adds a `.rockbox` folder.
3. Turn the Q2 on. Rockbox starts.
4. Optional themes: download the zip here: https://github.com/DiamondBond/q2-rockbox-themes/releases, unzip it onto the card (it fills in the `.rockbox` folder), then on the Q2: **Settings → Theme Settings → Browse Theme Files**. Pick one and load it.

Or, with Vamp on Wi-Fi: **System settings → App Store → Rockbox** installs it (press twice: it becomes what the Q2 starts at power-on), then **Rockbox Themes** installs the themes. The same page updates and removes them.

## How to use it

- **Want Vamp instead of Rockbox?** Hold Play/Pause while turning the Q2 on. It only lasts that time; the next start is Rockbox. To always start Vamp, set **System settings → Power → Boot to: Vamp**.
- **To copy music over USB:** turn on with Play/Pause held, plug in, drag your files over.
- **No card in it?** It starts Vamp.
- **Bluetooth headphones with Rockbox:** shut Rockbox down (it goes to Vamp), turn on Bluetooth and connect your headphones, and pick **Rockbox** on the Home menu (above Streaming). Rockbox starts and plays through the headphones. When it shuts down, you are back in Vamp.

## If something goes wrong

- **Rockbox is stuck:** hold Play/Pause while turning the Q2 on. It goes to Vamp.
- **Rockbox does not start:** the card must be exFAT or FAT32, and the `.rockbox` folder must be in the card's main folder.
- **Want the normal Shanling player back:** flash Shanling's stock V1.36 file the same way as step 1.
- **Anything else:** see **Troubleshooting by symptom** and the **bug-report checklist** in Chapter 10 of the [user guide](https://github.com/DiamondBond/q2-pod/releases/latest/download/Q2-Pod-User-Guide.pdf).
