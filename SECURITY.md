# Security

Report security problems privately with **Report a vulnerability** on this repository's Security tab, not in a public issue or on Discord.

Only the latest release gets fixes.

## Card apps are trusted code

A card app (any `.<id>/` folder on the card, from the App Store or copied by hand) runs as root, as the firmware does, and nothing isolates it from the firmware or from other apps: it can read and change anything on the card, including other apps' logins and your Last.fm, ListenBrainz and Spotify logins. Install only apps you trust.

## SyncLink is off unless you turn it on

SyncLink, Shanling's remote for its Eddict Player phone app (**System settings → Wireless → SyncLink**), asks a phone for no password. Over Wi-Fi anything on the same network can find the Q2 and connect to it on port 1751; over Bluetooth any phone that can connect can. Once connected it can play and browse your music, change settings, read and change the saved Wi-Fi network's name and password, and start a firmware update. q2-coda keeps it off: a Q2 updated from firmware that had it on has it turned off once, and it stays off until you choose **Bluetooth** or **Wi-Fi** on its page. Turn it on only on a network you trust, and off again when you are done ([internals](docs/internals.md#synclink)).
