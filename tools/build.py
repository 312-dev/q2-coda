#!/usr/bin/env python3
"""Reproducibly patch only the audited Q2 V1.36 ZIP. Requires LLVM, squashfs-tools and ImageMagick.

--logo swaps the boot splash JPEG (320x375); it defaults to assets/boot-logo.jpg.
"""
import argparse, dataclasses, hashlib, io, json, pathlib, re, shlex, struct, subprocess, tarfile, zipfile
from typing import TypeVar
ROOT = pathlib.Path(__file__).resolve().parents[1]
ZIP_SHA = '4e298cab6c12d86cfc55565645f7e3ae11d3fb44677a93ca13991e7184ecec4e'
DEMO_SHA = 'e65d638d6e70332b4c3193af8ac2044941ec0157d20e41e40233aac5b0c2c59f'
VERSION = '1.8.2'  # major.minor.patch, single digits, shown as is (About and release tag)
BETA = 0  # a pre-release's beta number (1-9), 0 for the release itself
REPO = '312-dev/q2-coda'  # whose releases Software Update fetches (--update-repo) and release.py publishes
# The updater's identity (firmware_v20.info and demo's version literal) must be 5 characters and
# differ from the installed one, so it packs the digits and the beta number: 1.8.0 beta 2 is V1802,
# 1.8.0 itself V1800, so each installs over the betas before it. Releases up to 1.8.0 BETA ended in I
# (1.0.1 was V101I). About shows the stock firmware version and a PRODUCT_NAME row with VERSION and
# the edition instead (ringnav_about).
TAG = 'V' + VERSION.replace('.', '') + str(BETA)

def version_ok(version, beta):
    """VERSION is three single digits and BETA one, as TAG packs them; 17.6 or 1.76 would give a 5-character tag too."""
    return bool(re.fullmatch(r'[0-9]\.[0-9]\.[0-9]', version)) and beta in range(10)

def dev_tag(number):
    """--dev build number's tag: V, three base-36 digits and i (V00Ai), so no two test builds share a
    tag, and none equals a release's, and the updater takes each."""
    digits, n = '', (number - 1) % (36**3 - 1) + 1  # 1..ZZZ, then round again
    for _ in range(3): n, d = divmod(n, 36); digits = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'[d] + digits
    return 'V' + digits + 'i'
BASE = 0xb00000
SCRATCH = 0xc80000  # 1.5 MB of payload text below it; demo's heap starts after the scratch state
RING_STEP = 48
HOOKS = {
    # USB charging is inhibited before insertion and guarded through enumeration/disconnect.
    'switch_charge_enable': (0x4fe42c, 'ringnav_usb_charge', None),
    'config_init': (0x503d0c, 'ringnav_usb_config', 'usb_config'),
    'usb_detect': (0x4ff220, 'ringnav_usb_detect', 'usb_detect'),
    'check_usb_status': (0x4f3744, 'ringnav_usb_status', 'usb_status'),
    'createPlayListItem': (0x517fdc, 'ringnav_playlist_create', None),
    'playlist_delete_click': (0x4b62c8, 'ringnav_playlist_delete', None),
    'localmusic_playlistsmore_page_init': (0x4b6614, 'ringnav_playlistsmore', 'playlistsmore'),
    'on_wm_keydown_before_fun': (0x4f24bc, 'ringnav_keydown', 'keydown'),
    'on_wm_keyup_before_fun': (0x4f2660, 'ringnav', 'keyup'),
    'on_wm_tsdown_before_fun': (0x4f29dc, 'ringnav_touch', 'touch'),
    'widget_on_paint_border': (0x66cc10, 'ringnav_paint', 'paint'),
    'widget_dispatch': (0x67165c, 'ringnav_dispatch', 'dispatch'),
    'on_wm_keylong_fun': (0x4f27e4, 'ringnav_keylong', 'keylong'),
    # The volume dialog's wheel ticks step at the wheel's speed, then stock's own step (volume.c vol_keyup)
    'volume_dialog_on_keyup': (0x4a3688, 'vol_keyup', 'vol_keyup'),
    # The key-up no page took, whose wheel tick opens the volume dialog: playback screens' alone (volume.c vol_wm_keyup)
    'on_wm_keyup_fun': (0x4f30c0, 'vol_wm_keyup', 'wm_keyup'),
    'playset_equalizer_page_init': (0x4b9c94, 'peq_page_init', None),
    'set_equalizer_value': (0x5039c0, 'peq_stock_eq', 'eq'),
    'select_timezone': (0x503b40, 'ringnav_timezone', 'timezone'),
    'timezone_row': (0x4c1aac, 'ringnav_timezone_row', 'timezone_row'),  # autotime_page's row i (0: the switch)
    'home_page_init': (0x534b6c, 'coverflow_home', 'home'),
    # Wi-Fi transfer's PIN (wifi_transfer.c): the server's switch, and the page that shows the PIN
    'switch_wifitransport': (0x519b30, 'ringnav_wifi_transfer', 'wifi_transfer'),
    'systemset_wifitransport_page_init': (0x4d797c, 'ringnav_wifi_transfer_page', 'wifi_transfer_page'),
    'localmusic_page_init': (0x535334, 'ringnav_localmusic', 'localmusic'),
    # Library lists: an Unknown row with no songs is dropped
    'load_localclass_list': (0x51357c, 'ringnav_localclass', 'localclass'),
    'toolsQueryDbTableAndSort': (0x5daf44, 'ringnav_sorted_query', 'sorted_query'),
    'toolsQueryDbTable': (0x5dad14, 'ringnav_library_query', 'dbquery'),
    # An artist's albums tab (getAlbumByAlbumArtist): the same album-artist rewrite
    'toolsQueryContentDbTable': (0x5db30c, 'ringnav_content_query', 'content_query'),
    'keylock_rows': (0x4c9dd8, 'ringnav_keylock_rows', None),
    'quick_lock': (0x4a1720, 'ringnav_quick_lock', None),
    'dialog_statusbar_dialog_init': (0x4a2338, 'ringnav_quick_settings', 'quicksettings'),
    # An Albums row: the album it opens keeps only that album's songs
    'load_album_detaillist': (0x513bac, 'ringnav_album_detail', 'detail'),
    # An artist's tabs: its albums' art keys (navigation.c ringnav_artist_list)
    'load_localartist_list': (0x51405c, 'ringnav_artist_list', 'artist'),
    # The three songtable writers; Coverflow keeps its album list until one runs.
    'scanAllMusicFile': (0x5072f8, 'coverflow_scan_all', 'scan_all'),
    'scanSpecFolder': (0x5074d4, 'coverflow_scan_folder', 'scan_folder'),
    'deleteMusicFromMusicDb': (0x50b794, 'coverflow_delete_song', 'delete_song'),
    # A song's added date: the card's creation stamp, not the access time (coverflow.c)
    'toolsGetFileCreateTime': (0x5d0954, 'ringnav_file_created', 'file_created'),
    'player_play_pause': (0x520410, 'ringnav_play_pause', 'pause'),
    # A user's seek stamps a press for hciplayer's wait for the DAC (docs/internals.md#wired-start)
    'player_seek_time': (0x51f234, 'ringnav_seek_time', 'seek_time'),
    'player_seek_start': (0x51f090, 'ringnav_seek_start', 'seek_start'),
    'mclSetBtVol': (0x5bf3dc, 'ringnav_btvol', 'btvol'),
    'mclSetOutputWay': (0x5bd544, 'ringnav_output', 'output'),
    'write_int_config': (0x4fe67c, 'ringnav_write_config', 'write_config'),
    # Every config.ini write: a section run into the line before it is split first (config_ini.c)
    'toolsWriteConfig': (0x5d0284, 'ringnav_ini_write', 'ini_write'),
    'main_loop_sleep_default': (0x65c470, 'ringnav_sleep', 'sleep'),
    'systemset_about_page_init': (0x4c1390, 'ringnav_about', 'about'),
    # stock's System Update page (patch/netupdate.c): its second row opens Software Update (sw_update.c)
    'systemset_fwupdate_page_init': (0x4c88d0, 'ringnav_fwupdate', 'fwupdate'),
    # Podcasts and Audiobooks: folder_page opened at their folder, and Back there leaving it
    'folder_page_init': (0x533ed0, 'ringnav_folder', 'folder'),
    'folder_back': (0x512778, 'ringnav_folder_back', 'folder_back'),
    # Folder's roots: OTG only with a drive plugged in (g_otg_status)
    'folder_load_top': (0x511ff8, 'ringnav_folder_top', 'folder_top'),
    # Videos from Folder view: a video row plays in q2video, not as a song (navigation.c)
    'folder_click': (0x532414, 'ringnav_folder_click', 'folder_click'),
    # Videos: no input reaches the UI while q2video plays
    'window_manager_dispatch_input_event': (0x680a4c, 'ringnav_input', 'input'),
    # SyncLink's (Eddict Player's) play/pause, previous and next pass the Buttons lock (#113)
    'main_loop_post_key_event': (0x69ebac, 'ringnav_post_key', 'post_key'),
    # Key Tone: no click while music plays, and none on the buzzer while headphones or Bluetooth listen
    'buzzeer_switch': (0x4fe3f8, 'ringnav_buzzer', 'buzzer'),
    # Artists (PLAYSET ARTISTTYPE) without a saved choice is Album Artist
    'read_artistype_value': (0x4fee80, 'ringnav_read_artistype', None),
    # Tag reads: a FLAC's "ALBUM ARTIST" when stock's libtag finds no ALBUMARTIST
    'toolsGetMusicInfo': (0x5d8b98, 'ringnav_music_info', 'music_info'),
    # Embedded art: none from stock's TagLib for an .ogg whose comment header is over OGG_TAGS_MAX (coverflow.c)
    'toolsGetAlbumCover': (0x5d709c, 'ringnav_album_cover', 'album_cover'),
    # Power gains Charge limit; Playback gains Artists (Album Artist)
    'systemset_sysset_page_init': (0x4d3ba8, 'ringnav_systemset', 'systemset'),
    'systemset_display_page_init': (0x4c7168, 'ringnav_display', 'display'),
    'save_memoryplay_info': (0x521058, 'ringnav_savequeue', 'savequeue'),
    # A reboot saves the queue first, as power-off does (playback.c ringnav_reboot): Restart and
    # the firmware update's dialog, which stops the player before it flashes and reboots
    'device_reboot': (0x502004, 'ringnav_reboot', 'reboot'),
    'dialog_checkfw_dialog_init': (0x49982c, 'ringnav_checkfw', 'checkfw'),
    # Software Update's card half (patch/sw_update.c): the card's update.tar checked, then stock's checkfw_dialog flashes it
    'fwupdate_tf': (0x4c7eb4, 'ringnav_fwupdate_tf', None),
    'mclLoadPlayList': (0x5bcfc8, 'ringnav_load', 'load'),
    'mclNextSong': (0x5c063c, 'ringnav_next', 'next'),
    # Lyrics: a local track without a .lrc takes its LYRICS tag (lyrics.c ringnav_lyric)
    'mclLoadExLyric': (0x5bd384, 'ringnav_lyric', 'lyric'),
    # its .lrc's UTF-8 test, which read 6 bytes and no 4-byte character (lyrics.c ringnav_lrc_utf8)
    'mclIsUtf8WithoutBOM': (0x5bbc4c, 'ringnav_lrc_utf8', None),
    # Now Playing's lyrics match (patch/onlinematch.c): LRCLIB alone, in place of stock's Kugou search
    'kugou_obtainlyric': (0x51b3f4, 'ringnav_lyric_search', None),
    # its ⋯ page: Get Album Lyrics under stock's Download Cover & Lyrics (onlinematch.c)
    'playermore_page_init': (0x539f60, 'ringnav_playermore', 'playermore'),
    # The sleep timer's switch as a row, under a set that hides its toolbar (navigation.c)
    'systemset_sleepshutdown_page_init': (0x4d1224, 'ringnav_sleeptimer', 'sleeptimer'),
    # any Tidal track asked for, from a queue or Now Playing, wakes its stream thread (tidal.c)
    'get_tidal_trackurl': (0x483f28, 'ringnav_tidal_track', 'tidaltrack'),
    # its cover match: MusicBrainz/Cover Art Archive and iTunes candidates in place of stock's Kugou search,
    # and none from NetEase's or Baidu's, online_coverthd's other two sources (onlinematch.c)
    'kugou_obtaincover': (0x51bf74, 'ringnav_cover_search', None),
    'com163_obtaincover': (0x51c46c, 'ringnav_cover_none', None),
    'baidu_obtaincover': (0x51bbdc, 'ringnav_cover_none', None),
    'mclPrevSong': (0x5c0518, 'ringnav_prev', 'prev'),
    'mclSetPlayMode': (0x5bd94c, 'ringnav_mode', 'mode'),
    'mclSetPlaySpeed': (0x5bd9dc, 'ringnav_speed', 'speed'),
    'mcl_open_preload': (0x5baff8, 'ringnav_open_preload', 'open_preload'),
    # Manual Next/Prev: at the queue ends stock wraps or loads the next folder without mclNextSong
    'player_change_music': (0x51fd24, 'ringnav_change', 'change'),
    'mcl_preload': (0x5bb2cc, 'ringnav_preload', 'preload'),
    'memeory_startplayer': (0x5211f4, 'ringnav_memory', 'memory'),
    'systemset_powermanager_page_init': (0x4cded4, 'ringnav_powermanager', 'power'),
    # Standby limit: the suspend to RAM between the RTC wake alarm's set and clear
    'toolsIntoStandby': (0x5d1110, 'ringnav_standby', 'standby'),
    # Standby's card driver reload: no removed and inserted toasts on the wake
    'check_cardmount_status': (0x4f39a4, 'ringnav_cardmount', 'cardmount'),
    'playset_playset_page_init': (0x4bd4d0, 'ringnav_audioset', 'audioset'),
    # Bluetooth's list, rebuilt on every search and link change, gains Resume on connect
    'btmenu_rows': (0x4c3ef8, 'ringnav_btrows', 'btrows'),
    # Spotify (patch/spotify.c): Streaming's row, and local music letting librespot go first
    'stream_page_init': (0x5410cc, 'ringnav_stream', 'stream'),
    'mclStartPlayer': (0x5c0118, 'ringnav_start_player', 'start_player'),
    # View Queue's row and Up Next's (navigation.c): a picked queue index, kept in advanced play's order
    'player_start_queue': (0x520df0, 'ringnav_start_queue', 'start_queue'),
    # A list pick of the playing file only asks (playback.c)
    'player_start': (0x520b4c, 'ringnav_player_start', 'player_start'),
    # Card apps (patch/addon.c): USB mode's page, before it unmounts the card, ends them first
    'usbmode_page_init': (0x542e68, 'ringnav_usbmode', 'usbmode'),
    # Tidal cache (patch/tidal.c): the stream URL thread's player start, with its song id in $s2
    'mcl_tidalStartPlayer': (0x5bf948, 'tidal_start', 'tidal'),
    # Tidal's playlist, album and favourite-tracks threads: every page, not stock's first 50 (tidal.c)
    'tidalPlaylistDetailThd': (0x48c178, 'ringnav_tidal_playlist', 'tidalplaylist'),
    'tidalAlbumDetailThd': (0x48c33c, 'ringnav_tidal_album', 'tidalalbum'),
    'tidalColTracksThd': (0x489074, 'ringnav_tidal_favs', 'tidalfavs'),
    # Ratings (ratings.c rate_bind): Now Playing's heart, held, opens the picker
    'playing_page_init': (0x53e334, 'ringnav_playing', 'playing'),
    # The Wi-Fi signal (system.c ringnav_wifi_signal): the driver's rx_signal instead of an ioctl
    # that takes the radio out of power save; the status bar and the network monitor (a bal) poll it
    'networkGetWifiSignal': (0x5c25d8, 'ringnav_wifi_signal', None),
    'playlist_rebuild': (0x4b5444, 'ringnav_playlist', 'playlist'),
    'widget_on_paint_background': (0x66fcec, 'ringnav_paint_bg', 'paint_bg'),
    'bigcover_page_init': (0x52db48, 'ringnav_bigcover', 'bigcover'),
    # Every window's asset (window_open's): the geometry set's own copy (navigation.c ringnav_ui_load)
    'ui_loader_load_widget_with_parent': (0x667304, 'ringnav_ui_load', 'ui_load'),
    # Status bar's 1 s tick: the payload's play state, before any paint (navigation.c bar_sync)
    'systembar_showface': (0x5417d0, 'ringnav_showface', 'showface'),
    'style_get_color': (0x65d4dc, 'ringnav_style_color', 'color'),
    'image_manager_add': (0x657b44, 'ringnav_image_add', 'image'),
    # Song rows: no format badge (navigation.c ringnav_badge)
    'list_get_img_pic': (0x50c5fc, 'ringnav_badge', None),
    # Every scrolling label's start: its delay and pace (navigation.c ringnav_hscroll_start)
    'hscroll_label_start': (0x5fb4ac, 'ringnav_hscroll_start', 'hscroll_start'),
    # Smart Wi-Fi (smart_wifi.c smart_poll): the Wi-Fi page wakes the radio before it reads g_wififlag
    'systemset_wifi_page_init': (0x4d67ac, 'ringnav_wifi_page', 'wifi_page'),
    # Smart Wi-Fi (smart_wifi.c smart_hold): stock actions that check the link once, held while the
    # radio comes back. Streaming's Tidal row (stream_page_init's row click), Network service's rows
    # (Wi-Fi transfer only) and Now Playing More's Download Cover & Lyrics (its row's case); none has
    # a symbol.
    'stream_row': (0x540f7c, 'ringnav_stream_row', 'stream_row'),
    'netservice_row': (0x4cce64, 'ringnav_netservice_row', 'netservice_row'),
    'playermore_online': (0x539614, 'ringnav_online_match', 'online_match'),
    # Scan Folders' row click: a tap on a folder with no folders inside ticks it (scan_folders.c)
    'specfolder_click': (0x53f9b4, 'ringnav_specfolder_click', 'specfolder_click'),
    # The boot animation plays to its end: demo's screen shows nothing until it has (splash_hold.c)
    'lcd_linux_fb_create': (0x63a57c, 'splash_lcd', 'lcd_create'),
    # A phone's A2DP stream events (stock's receiver page): dropped while Guest Play is on (guest.c guest_a2dp)
    'btrecvA2dpFunc': (0x4fc72c, 'guest_a2dp', 'btrecv'),
    # Guest Play's receivers (guest.c, docs/internals.md#guest-play-receivers): AirPlay's take-over and the DLNA
    # renderer's actions wait for the owner's answer while Guest Play is on
    'airplay_mainscheduling': (0x519c28, 'guest_airplay', 'airplay_sched'),
    'dmrActionSetPlayURI': (0x51005c, 'guest_dlna_uri', 'dlna_uri'),
    'dmrActionPlay': (0x50fd50, 'guest_dlna_play', 'dlna_play'),
    'dmrActionStop': (0x50fe4c, 'guest_dlna_stop', 'dlna_stop'),
}
# The payload's stock_<name>_trampoline, the hooks' third element, resumes each hook past its 3-word PIC
# prologue.
# Every audited stock PIC prologue resolves this GOT base.
GP = 0xa3aae0
# style_get_gradient has no PIC prologue. It is a leaf that null-checks the style and its
# vtable, then tail-calls get_gradient (+0x18); its first two words (beqz a0; nop) become the jump
# and the payload does the whole of it. The third word is pinned too, so the layout is the audited one.
GRADIENT_LEAF = ('style_get_gradient', 0x65d4ac, 'ringnav_style_gradient', (0x10800009, 0, 0x8c820000))
# Videos: window_manager_paint is a leaf too (null-checks the manager and its vtable at +0x94, then
# tail-calls paint, +0xc); the payload does the whole of it and skips it while q2video plays.
WM_PAINT_LEAF = ('window_manager_paint', 0x680a1c, 'ringnav_wm_paint', (0x10800009, 0, 0x8c820094))
# The UI loop's input queue has one way in, main_loop_queue_event, a leaf too (null-checks the loop, its
# queue_event slot at +0x18 and the request, then tail-calls the slot); the payload does the whole of it
# and keeps the touch thread's empty events out of the queue (playback.c ringnav_queue_event).
QUEUE_LEAF = ('main_loop_queue_event', 0x65c418, 'ringnav_queue_event', (0x10800008, 0, 0x8c990018))
# Videos' player (patch/video.c): a separate executable against the rootfs's own libraries,
# with display_logo's inode metadata.
HELPER, HELPER_LIKE = 'usr/bin/q2video', 'usr/bin/display_logo'
HELPER_LIBS = ['lib/libc-2.28.so', 'lib/libpthread-2.28.so', 'usr/lib/libasound.so.2.0.0']
# Rockbox dual boot (docs/boot.md#rockbox): S90play starts Rockbox whenever the card has it, as an
# iPod with Rockbox does. Play/Pause held at power-on (patch/boot.c reads the key) or Rockbox's
# exit 0x51 (Boot stock OS) starts Q2 Muse for that session only. boot-target selects the next
# power-on default (missing or invalid retains Rockbox). ponytail: the card's mount is awaited (up to 3 s without a card)
# unless Q2 Muse was asked for, as the card probe can't tell "no card" from "not yet". Rockbox runs
# with the launcher contract in its tools/shanlingq2/README; demo starts when it exits, or when the
# card has none. demo keeps its argv[0], which checkappprocess.sh pgreps for. iPod's Home Rockbox row
# (navigation.c rockbox_open) leaves ROCKBOX_FLAG and kills demo: Rockbox runs again, its
# watchdog stopped in case demo's kill was not reached, then demo once more. Any other end of demo
# ends the loop, so the watchdog reboots as stock's would. Wi-Fi is off while Rockbox runs (WIFI_OFF).
BOOT = 'usr/bin/q2boot'
# The boot animation's player (docs/boot.md#boot-animation, patch/splash.c): S11logo_display_shell runs it.
SPLASH = 'usr/bin/q2splash'
REMOTE = 'usr/bin/q2remote'
# --dev only (docs/adb.md#q2eq, patch/q2eq.c): the EQ editor from a computer, through demo's q2eq_poll
Q2EQ = 'usr/bin/q2eq'
# The car remote daemon (docs/remote-protocol.md, docs/internals.md#car-remote): demo's payload starts it
# while Settings > Car Remote is on (patch/coda_rc_shim.c). SQLite reads the library, libdbus talks to BlueZ.
CODA_RC = 'usr/bin/coda-rc'
CODA_RC_SOURCES = ['coda_rc', 'coda_rc_core', 'coda_rc_lib', 'coda_rc_ble', 'monocypher/monocypher',
                   'monocypher/monocypher-ed25519']
CODA_RC_LIBS = ['lib/libc-2.28.so', 'usr/lib/libsqlite3.so.0.8.6', 'usr/lib/libdbus-1.so.3.19.15']
# Guest Play's daemon (docs/internals.md#guest-play): demo starts it (patch/guest.c), libdbus talks to BlueZ,
# bluealsa and bluetoothd's shanling.bluez; and the D-Bus policy its sink daemon needs.
GUEST = 'usr/bin/q2-guest'
GUEST_LIBS = ['lib/libc-2.28.so', 'usr/lib/libdbus-1.so.3.19.15']
GUEST_DBUS = 'etc/dbus-1/system.d/bluealsa-sink.conf'
GUEST_HOTSPOT = 'usr/bin/q2-hotspot'  # its hotspot (docs/internals.md#guest-play-hotspot), a shell script q2-guest runs
PLAYER_STOCK_DIR = b'usr/lib/hciplayer-stock'  # --new-player: hciplayer.xz's place, player/main.c's STOCK_XZ
# Card apps the firmware carries (docs/internals.md#bundled-apps): id -> (version, release zip, its size, its
# SHA-256), each the zip its release ships for the App Store, read from --apps (APPS_DIR). Its .<id>/ files go
# to BUNDLED_DIR/<id> (patch/addon.c BUNDLED) with a version file and bundled_launcher's run in place of the
# app's, so the app keeps its data in the card's .<id>. A new pin is a new zip: the source hash covers the pin.
BUNDLED_DIR = 'usr/lib/q2apps'
BUNDLED_APPS = {
    'subsonic': ('q2-v1.0', 'q2-subsonic-v1.0-6bdfe61.zip', 2859355,
                 '03eeebeef4a06fe665cb2b5e50ec09ae4094027ed35989b79e84fe81e5434a10'),
}
APPS_DIR = pathlib.Path.home()/'.cache/q2-apps'
S90PLAY = 'etc/init.d/S90play'
ROCKBOX_FLAG = '/tmp/q2pod-rockbox'
# Rockbox can't use Wi-Fi and the radio costs battery, so rockbox() below stops it. Nothing is saved or restored
# here: demo, which follows every Rockbox exit, restarts Wi-Fi from WIFI in config.ini, which wifi_switch writes on
# every toggle. That covers a forced reset during Rockbox too, and writing the config here would lose the user's choice.
# No rmmod 8733bs: that module also holds Bluetooth's power line, and Bluetooth must survive the hand-over.
# The rest ends as stock's networkStop does. wifi_connect.sh because demo may have just started it. No ifconfig wlan0
# 0.0.0.0: BusyBox brings a down interface up to set an address, which would wake a cold boot's radio; demo clears it.
# Home's hand-over pauses first: a shell demo forked for one of these just before it died isn't named yet, and would
# start Wi-Fi under Rockbox. demo is gone, so nothing new can start and a short wait is enough. Not a wait for Wi-Fi to
# finish starting: wifi_connect.sh exits before its /tmp/wpasupplicant_initsuccess without a saved network, and demo
# starts udhcpc later too. A second pass 2 s in, called off if Rockbox has already left and demo is starting Wi-Fi
# again, catches a shell that took longer. ponytail: 300 ms and 2 s are guesses; a shell slower than 2 s still escapes.
WIFI_OFF = 'killall -9 wifi_connect.sh udhcpc wpa_supplicant 2>/dev/null; ifconfig wlan0 down 2>/dev/null'
DEFAULT_CONFIG = 'release/assets/default/raw/config.ini'  # stock's reset copies it (DEFAULT_CONFIG_OFF below)
DEFAULTS_MARK = '/mnt/data/q2-defaults'  # written once Q2 Muse's defaults replaced the config before it (BOOT_HOOK)
BOOT_HOOK = (b'    /release/bin/demo &\n', f'''    (
        # Q2 Muse's defaults over every config.ini from before the q2-defaults mark, once, before demo reads it: a stock
        # Shanling config, or an earlier build's, holds stock's values (Enlarged Album Art on); later updates keep
        # the user's settings. Saved Wi-Fi, Bluetooth pairings and the library are other files (docs/internals.md#first-boot)
        d={DEFAULTS_MARK}
        [ -f $d ] || {{ cp /{DEFAULT_CONFIG} /mnt/data/config.ini.new && sync &&
            mv -f /mnt/data/config.ini.new /mnt/data/config.ini && echo 1 > $d && sync; }}
        c=/mnt/data/q2-crash.log
        m=/mnt/data/q2-state
        [ -f $m ] && {{
            [ -f $c ] || {{
                echo "== date"; date
                echo "== demo did not end: a forced restart, or the battery ran out"
            }} > $c
            {{ echo "== last state"; ls -l $m; cat $m; }} >> $c
            # Coverflow's open never finished (coverflow.c cf_trace): the phase it reached
            [ -f /mnt/data/q2-cf-trace ] && {{ echo "== cover flow"; cat /mnt/data/q2-cf-trace; }} >> $c
            # The UI loop stalled (system.c stall_write): what its thread was doing
            [ -f /mnt/data/q2-stall ] && {{ echo "== stall"; cat /mnt/data/q2-stall; }} >> $c
            rm -f $m
        }}
        rm -f /mnt/data/q2-cf-trace /mnt/data/q2-stall
        # Baidu Netdisk's sign-in, which stock sends over plain HTTP: Q2 Muse has no Baidu row to use it from
        rm -f /mnt/data/baidu_token.conf
        [ -f $c ] && (
            i=0
            while [ $i -lt 60 ] && ! grep -q ' /mnt/mmc ' /proc/mounts; do usleep 500000; i=$((i+1)); done
            grep -q ' /mnt/mmc ' /proc/mounts || exit
            # the card keeps the two before it: q2-crash.log.1, then .2; they move only once the copy is
            # whole (a full card or a power cut leaves them, and the log stays on /mnt/data)
            l=/mnt/mmc/q2-crash.log
            cp $c $l.new && sync || exit
            [ -f $l.1 ] && mv -f $l.1 $l.2
            [ -f $l ] && mv -f $l $l.1
            mv -f $l.new $l && sync && rm -f $c
        ) &
        rb=/mnt/mmc/.rockbox
        rockbox() {{
            [ -f $rb/rockbox ] || return
            killall q2splash 2>/dev/null  # the boot animation would draw over Rockbox, which never pans (patch/splash.c)
            /usr/bin/cmd_gpio set_func PE22 output0  # Rockbox never drives the charge pin, so the S12 inhibit would stay on
            # Rockbox only starts here, so power-on and Home's hand-over both lose Wi-Fi (build.py WIFI_OFF)
            {WIFI_OFF}
            (cd $rb && exec ./rockbox) > $rb/rockbox.log 2>&1
            echo "exit $?" >> $rb/rockbox.log
        }}
        if [ "$(cat /mnt/data/boot-target 2>/dev/null)" != q2pod ] && ! /usr/bin/q2boot; then
            for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
                [ -e /tmp/mmc_add ] && break
                usleep 200000
            done
            rockbox
        fi
        # demo's stderr (docs/boot.md#rockbox-from-home): stock starts its Bluetooth and Wi-Fi helpers from demo,
        # so they inherit it and their reasons for failing land here, in RAM, instead of on the serial console;
        # emptied once a minute past 512 KB (O_APPEND keeps demo writing at its end), its tail goes in the crash
        # log. stdout stays on the console as stock's; --dev takes both (LOG_HOOK)
        (while sleep 60; do n=$(wc -c 2>/dev/null </tmp/q2-demo.err); [ "${{n:-0}}" -gt 524288 ] && : >/tmp/q2-demo.err; done) &
        while :; do
            /release/bin/demo 2>>/tmp/q2-demo.err
            s=$?
            [ -e {ROCKBOX_FLAG} ] || break
            rm -f {ROCKBOX_FLAG}
            killall checkappprocess.sh 2>/dev/null
            # demo is gone: what it just launched gets this long to reach a name WIFI_OFF kills (build.py)
            usleep 300000
            (usleep 2000000; {WIFI_OFF}) &
            rockbox
            kill $! 2>/dev/null
        done
        # 0 and 143 are a power-off's, but a clean end (ringnav_clean_end) removes $m first: still there, it wasn't one
        case $s in 0|143) [ -f $m ] || s= ;; esac
        if [ "$s" ]; then
            {{
                echo "== date"; date
                echo "== demo exit $s"
                # the OOM killer's own lines from all of dmesg: its task dump can push them out of the tail
                echo "== oom"; dmesg | grep -E 'invoked oom-killer|oom-kill:|Killed process|oom_reaper'
                echo "== dmesg"; dmesg | tail -n 200
                echo "== meminfo"; cat /proc/meminfo
                echo "== ps"; ps
                [ -f /tmp/q2-scan-file ] && {{ echo "== last scanned"; cat /tmp/q2-scan-file; }}
                [ -f /tmp/q2-scan.log ] && {{ echo "== scan"; tail -n 60 /tmp/q2-scan.log; }}
                [ -f /tmp/q2-art-file ] && {{ echo "== last art"; cat /tmp/q2-art-file; }}
                [ -f /tmp/q2-art.log ] && {{ echo "== art"; tail -n 60 /tmp/q2-art.log; }}
                [ -f /tmp/q2-demo.err ] && {{ echo "== stderr"; tail -n 60 /tmp/q2-demo.err; }}
                [ -f /tmp/q2-demo.log ] && {{ echo "== q2-demo.log"; tail -n 200 /tmp/q2-demo.log; }}
            }} > $c.tmp 2>&1; mv $c.tmp $c
            sync
        fi
    ) &
'''.encode())
# User boot logo (docs/boot.md#custom-boot-logo): demo copies the card's to /mnt/data, which
# S10mount_ubifs mounts before the splash; without it, the built-in one, in Light Mode too (the boot is
# dark in either mode: docs/boot.md#light-mode).
# Boot animation (docs/boot.md#boot-animation): where the built-in dark splash would show and the build
# ships LOGO_ANIM (the default logo's builds), display_logo draws LOGO_BLACK, which turns the screen on as
# for any splash, and q2splash plays the animation over it; if q2splash can't start, the still logo shows.
S11LOGO = 'etc/init.d/S11logo_display_shell'
LOGO = 'release/assets/default/raw/images/xx/logo.jpg'
LOGO_ANIM, LOGO_BLACK = 'release/assets/default/raw/images/xx/logo.q2a', 'release/assets/default/raw/images/xx/logo-black.jpg'
REMOTE_HOOK = (b'/usr/bin/dbus-daemon --config-file=/etc/dbus-1/system.conf\n',
               b'/usr/bin/dbus-daemon --config-file=/etc/dbus-1/system.conf\n/usr/bin/q2remote &\n')
# --dev only (docs/adb.md): adbd over USB. Its gadget is made and FunctionFS mounted before adbd starts, and before
# demo, whose first USB check may already want the controller for stock's gadget; adbd is started again if it
# ends, and its gadget bound once it is up (unless stock's gadget has the controller). After REMOTE_HOOK's line.
ADBD, ADB_GADGET = 'usr/bin/adbd', 'usr/bin/q2adb-gadget'
ADB_HOOK = (b'/usr/bin/q2remote &\n',
            b'/usr/bin/q2remote &\n/usr/bin/q2adb-gadget setup && (while :; do /usr/bin/adbd </dev/null >/dev/null 2>&1 &'
            b' /usr/bin/q2adb-gadget bind; wait $!; sleep 2; done) &\n')
# Stock's USB scripts, which demo runs to start and stop USB storage and the USB DAC: in a --dev build each is
# wrapped (adb_wrapper) and stock's kept beside it as <name>_stock.sh, so the ADB gadget gives way to stock's.
USB_SCRIPTS = ('storage_config.sh', 'uac_device_config.sh')


def adb_wrapper(name):
    """usr/bin/name in a --dev build: stock's script with the ADB gadget off the USB controller while it runs
    (docs/adb.md#the-usb-gadget). Stock's exit status is kept."""
    stock = name.replace('.sh', '_stock.sh')
    return (f'#!/bin/sh\n# q2-coda --dev: stock\'s {name} is {stock}; the ADB gadget gives the USB controller to its\n'
            f'# gadget from start to stop (docs/adb.md#the-usb-gadget).\n'
            f'[ "$1" = start ] && /usr/bin/q2adb-gadget park\n/usr/bin/{stock} "$@"\nr=$?\n'
            f'[ "$1" = stop ] && /usr/bin/q2adb-gadget unpark\nexit $r\n').encode()
# --dev only (docs/remote.md#log): demo's console output, and hciplayer's, which inherits it, in RAM, so
# emptied once a minute past 4 MB (O_APPEND keeps demo writing at its end), in place of the release's stderr
# file and its cap. In BOOT_HOOK's start path.
LOG_HOOK = (b'        (while sleep 60; do n=$(wc -c 2>/dev/null </tmp/q2-demo.err); [ "${n:-0}" -gt 524288 ] && : >/tmp/q2-demo.err; done) &\n'
            b'        while :; do\n            /release/bin/demo 2>>/tmp/q2-demo.err\n',
            b'        (while sleep 60; do n=$(wc -c 2>/dev/null </tmp/q2-demo.log); [ "${n:-0}" -gt 4000000 ] && : >/tmp/q2-demo.log; done) &\n'
            b'        while :; do\n            /release/bin/demo >>/tmp/q2-demo.log 2>&1\n')
# --dev only: the wheel's frame log (playback.c wl_flush) and stale-press notes (navigation.c stale_note) go to the
# card's q2-wheel.log, appended, when the card is up, up to 256 KB. In BOOT_HOOK's start path.
WHEEL_LOG_HOOK = (b'        rb=/mnt/mmc/.rockbox\n',
                  b'        w=/mnt/data/q2-wheel.log\n'
                  b'        [ -f $w ] && (\n'
                  b'            i=0\n'
                  b"            while [ $i -lt 60 ] && ! grep -q ' /mnt/mmc ' /proc/mounts; do usleep 500000; i=$((i+1)); done\n"
                  b"            grep -q ' /mnt/mmc ' /proc/mounts || exit\n"
                  b'            c=/mnt/mmc/q2-wheel.log\n'
                  b'            n=$(wc -c 2>/dev/null <$c)\n'
                  b'            [ "${n:-0}" -lt 262144 ] || { rm -f $w; exit; }\n'
                  # renamed first: a flush after this makes a new file for the next boot
                  b'            mv -f $w $w.m && cat $w.m >> $c && sync && rm -f $w.m\n'
                  b'        ) &\n'
                  b'        rb=/mnt/mmc/.rockbox\n')
# The byte in bluealsa's AAC capability holding the 44.1 kHz bit; see docs/internals.md.
BLUEALSA = 'usr/bin/bluealsa'
# Stock's bluetoothd (BlueZ 5.66 with Shanling's changes, docs/bluetoothd.md) answers the control channel's
# GetElementAttributes from a registered BlueZ player it never has, so headsets and cars got empty song
# information: tools/bt_meta.c, mapped at BT_META, answers it from libbtctl's g_track_meta instead
# (docs/internals.md#track-metadata). BT_META_SLOT is GetElementAttributes's entry in its control handler
# table (PDU 0x20, STATUS, then the handler), carrying an R_MIPS_REL32 the loader adds the load address to,
# as bluetoothd is position independent; BT_META_GOT the GOT slots ($gp offsets) bt_meta.c reads.
BLUETOOTHD = 'usr/bin/bluetoothd'
BLUETOOTHD_SHA = '6e6d45d66f958a028a523786ae661110b1853bf49b8a50946dd70a0518b4f357'
BT_META = 0x150000
BT_META_SLOT = (0x12f698, 0x00000120, 0x00039454)  # (address, stock PDU and type word, stock handler)
BT_META_GOT = {'TRACK_META': ('g_track_meta', -28700), 'NOTIFY_MUTEX': ('g_notify_mutex', -28244),
               'LOCK': ('pthread_mutex_lock', -28204), 'UNLOCK': ('pthread_mutex_unlock', -27872)}
BT_META_ROOM = 502  # an AVRCP PDU's parameters in one AVC frame (BlueZ's AVRCP_PDU_MTU)
# Its launcher: keep the A2DP transport open 5 s after hciplayer closes the PCM (a stop, a skip, a cold start), so
# a track change doesn't restart the stream (true-wireless buds started one ear first).
BLUEALSA2 = 'usr/bin/bluealsa2'
BT_KEEPALIVE = (b'bluealsa -c LDAC', b'bluealsa --keep-alive=5 -c LDAC')
# ... and while Guest Play is on, only the source role, so a device that is both (an Echo) connects as a sink and a
# guest's phone reaches q2-guest's own sink daemon (docs/internals.md#the-relay). Applied after BT_KEEPALIVE.
BT_GUEST_SOURCE = (b'bluealsa --keep-alive=5', b'bluealsa $(grep -qx enabled=1 /mnt/data/q2-guest/config 2>/dev/null '
                   b'&& echo -p a2dp-source) --keep-alive=5')
SCROBBLE_CA = 'etc/scrobble-ca.pem'
# BlueZ's settings. demo sets Discoverable each time it starts Bluetooth (openBluetooth), and stock's 0 kept the Q2
# discoverable for as long as Bluetooth stayed on; 180 s gives a phone three minutes from then to find it.
BT_MAIN = 'etc/bluetooth/main.conf'
BT_DISCOVERABLE = (b'\nDiscoverableTimeout = 0\n', b'\nDiscoverableTimeout = 180\n')
# The name a phone lists the Q2 as when the user chose none: BlueZ's own Name, which bt_name.c's Alias of "" falls
# back to (docs/internals.md#bluetooth-name); a chosen one is [Q2POD] BTNAME in config.ini.
BT_NAME = (b'\nName = Shanling Q2\n', b'\nName = Q2 Muse\n')
# BlueZ 5.66's experimental D-Bus interfaces, off in stock's file: BatteryProviderManager1 among them, through which
# bluealsa hands BlueZ the level a headset reports over HFP, so it shows as the device's Battery1 (patch/bt_battery.c,
# docs/internals.md#headset-battery).
BT_EXPERIMENTAL = (b'\n#Experimental = false\n', b'\nExperimental = true\n')
# Wi-Fi transfer's server (docs/internals.md#wi-fi-transfer-root). thttpd drops to www-data once it has bound :8888, so its
# own request parsing, which runs before the PIN check, is not root's; stock's conf kept it root. Only root may write
# the card, so each of the six CGIs is patch/cgi_root.c, set-uid root (CGI_MODE), which runs Q2CGI as root.
THTTPD_CONF = 'etc/thttpd.conf'
THTTPD_USER = (b'\nuser=root\n', b'\nuser=www-data\n')
WWW_DATA = b'\nwww-data:x:33:33:'  # stock's /etc/passwd line: cgi_root.c WWW_UID
Q2CGI = 'usr/bin/q2cgi'
CGIS = ('create', 'delete', 'list', 'download', 'upload', 'auth')  # cgi-bin/<name>.cgi; auth is Q2 Muse's own
CGI_MODE = (b'775', b'4755')  # stock's mode for a CGI, then set-uid root and no group write
# Every V1.36 unit has the same root password hash, and init runs a login on the board's serial pad (ttyS3).
# Nothing on the Q2 logs in (q2remote, --dev only, runs /bin/sh itself), so root is locked and the getty goes;
# the kernel's and init's messages still reach the console.
SHADOW = 'etc/shadow'
INITTAB = 'etc/inittab'
SERIAL_GETTY = (b'# Put a getty on the serial port\nconsole::respawn:/sbin/getty -L ttyS3 115200 vt100 # GENERIC_SERIAL\n\n', b'')
def mozilla_ca():
    """assets/mozilla-ca.pem, refused unless it is the pinned curl release (STOCK_CA_SHA)."""
    data = (ROOT/'assets/mozilla-ca.pem').read_bytes()
    check(sha(data) == STOCK_CA_SHA, 'Unexpected assets/mozilla-ca.pem')
    return data
def lock_root(shadow):
    """shadow with root's password field '*', which no password matches."""
    lines = shadow.split(b'\n')
    rows = [i for i, l in enumerate(lines) if l.startswith(b'root:')]
    check(len(rows) == 1, 'Unexpected /etc/shadow')
    fields = lines[rows[0]].split(b':')
    fields[1] = b'*'
    lines[rows[0]] = b':'.join(fields)
    return b'\n'.join(lines)
def password_logins(shadow):
    """The accounts in shadow a password logs in to: an empty field or a hash, not one starting * or !."""
    return [l.split(b':')[0].decode() for l in shadow.split(b'\n')
            if l.strip() and not l.split(b':')[1].startswith((b'*', b'!'))]
AAC_44K1 = 0x317b8
# DROP is acknowledged before the encoder handles its signal. Flush in the control
# thread instead, so a delayed signal cannot discard the next track's first PCM.
BT_DROP = 0x40cb50
BT_DROP_HANDLER = 0x4147ac
BT_DROP_PAYLOAD = 0x450000
# Return address -> PCM ffb_t offset from the caller's io state. The last caller
# is Q2's LDAC loop, which accumulates 256 samples in saved s6/s7 instead of ffb_t.
BT_PCM_CALLERS = ((0x40649c, 0x74), (0x4150dc, 0x34), (0x41746c, 0x98),
                  (0x418114, 0x34), (0x418c84, 0x60), (0x41a6ec, None))
# A skip left the encoder without PCM for ~400 ms and stock sent no packets, so the headset's buffer
# ran dry: tools/bt_silence.S, linked at BT_SILENCE, returns silence from Drain's timeout instead,
# for up to BT_SILENCE_SAMPLES (interleaved: 2 s at 48 kHz stereo, 1 s at 96 kHz) after real PCM.
BT_SILENCE, BT_SILENCE_SAMPLES = 0x450400, 192000
# (address, stock word, patched with: j or jal to a bt_silence.S symbol, or a word):
# poll's timeout, poll's timeout path (its delay slot takes the moved move a1,zero), the PCM read,
# and the signal jump table's PAUSE entry: demo pauses hciplayer before some skips, so PAUSE takes
# Drain's case (a 100 ms poll, then the fill) instead of polling again unbounded.
BT_SILENCE_SITES = ((0x41465c, 0x8e460030, 'j', 'timeout'), (0x414684, 0x00002825, 'j', 'fill'),
                    (0x414688, 0x0c10c2b0, None, 0x00002825), (0x414754, 0x0c105053, 'jal', 'read'),
                    (0x42d09c, 0x0041463c, None, 0x004147a0))
# platform_init's crash watchdog forks pgrep every 2 s; 10 s is still quick to reboot a dead UI.
WATCHDOG = 'usr/bin/checkappprocess.sh'
WATCHDOG_SLEEP = (b'\tsleep 2\n', b'\tsleep 10\n')
# The config a reset copies to /mnt/data: Enlarged Album Art (10 s wait) and Standby start off. So does stock's Wheel Function: Q2 Muse runs stock in Volume Mode whatever the key holds (WHEEL_MODE), and a Q2 reset here and returned to stock gets Menu Mode, the default stock's changelog names and a missing key gives (the shipped file says 1).
# It also names the language, English (2): stock's config_init (0x5047f8) sets g_guideflag when SYSSET LANGUAGE is
# missing, and the first boot and every reset opened stock's language guide, where Q2 Muse's wheel and Centre are off.
# Q2 Muse is English only (Settings has no Language row), so the guide is never shown (docs/internals.md#first-boot).
# Smart Awake (IPOD KEEPLIT) starts on, as a missing key reads (theme_engine.c settings_read); stock's file has no [IPOD],
# and ends with no newline (docs/internals.md#config-ini), so the section starts on a line of its own.
DEFAULT_CONFIG_OFF = (b'COVERMODE', b'STANDBYMODE', b'WHEELMODE')
DEFAULT_LANGUAGE = b'LANGUAGE=2\n'
DEFAULT_IPOD = b'[IPOD]\nKEEPLIT=1\n'
def default_config(config):
    for key in DEFAULT_CONFIG_OFF:
        check(config.count(b'\n%s=1\n' % key) == 1, 'Unexpected default config.ini')
        config = config.replace(b'\n%s=1\n' % key, b'\n%s=0\n' % key)
    check(config.count(b'[SYSSET]\n') == 1 and b'\nLANGUAGE=' not in config and b'[IPOD]' not in config,
          'Unexpected default config.ini')
    config = config.replace(b'[SYSSET]\n', b'[SYSSET]\n' + DEFAULT_LANGUAGE)
    return config + (b'' if config.endswith(b'\n') else b'\n') + DEFAULT_IPOD
# toolsSetRtcTime's command, with its padding; -u keeps the RTC in UTC. See docs/internals.md#clock.
RTC_WRITE = (b'hwclock -w\0\0', b'hwclock -wu\0')
# networktime_adjust_thd's time servers (0x7836bc, after time.apple.com, before time.windows.com): stock's
# China pool becomes the worldwide one, NUL-padded to its length. See docs/internals.md#clock.
NTP_POOL = (b'\0cn.pool.ntp.org\0time.', b'\x000.pool.ntp.org\0\0time.')
# network_checkstatus's command once Wi-Fi connects (its digit at file offset 0x3852bd): stock turns
# the radio's power save (LPS) off for the whole connection; 2 restores the driver's own default,
# PS_MODE_MAX. See docs/internals.md#battery.
WIFI_LPS = (b'rtwpriv wlan0 pm_set lps=0\0', b'rtwpriv wlan0 pm_set lps=2\0')
# Now Playing's info page: its one fill (0x53bc78), a bal, becomes jal ringnav_np_info, which tidies the lines.
NP_INFO_CALL = 0x53d308
# Now Playing's A-B markers (0x53baa4): its three calls of their x (0x53b9d8), each a bal, become jal ringnav_ab_x,
# which keeps them on the slider wherever the page has moved it from the build's place.
AB_X_CALLS = (0x53bb78, 0x53bbf4, 0x53bc04)
# Songs' row bind (0x4ab208): a record with no artist gets stock's "Unknown Artist" through this getFormatString jalr,
# which becomes jal ringnav_song_unknown.
SONG_UNKNOWN_CALL = 0x4ab424
# table_client's load loop: its on_load_data jalr becomes jal ringnav_table_load, which calls it and
# then re-picks the row's faces for the text it bound (the load is lw t9, TABLE_LOAD_DATA(s4) at -0x14).
TABLE_LOAD_CALL = 0x5dcdac
# A virtual table's drag (its pointer move, at 0x5ddf04 in what tools/stock_sites.tsv names table_view_on_event) scrolls
# by bal table_client_set_yoffset(client, yoffset_save - dy) with no bound, and its release springs back from past an
# end: jal ringnav_table_drag, which holds the offset between the list's ends.
TABLE_DRAG_CALL = (0x5ddf04, 0x0411fbf6)
# Folder row names are text, not printf formats ("99% Invisible" crashes stock's formatter).
# tk_strncpy writes its terminator at n, so reserve the last byte of the 512-byte buffer.
FOLDER_NAME = ((0x532cb0, 0x24050200, 0x240601ff),  # a2 = 511
               (0x532cbc, 0x8f99a7c4, 0x8f99b034),  # tk_snprintf -> tk_strncpy
               (0x532cc4, 0x8e260008, 0x8e250008))  # a1 = record->name
# mclNextSong's shuffle pick; the payload calls the stock pick, then applies a pending Play next.
SHUFFLE_CALL = (0x5c0870, 0x0411e7d7)  # bal mcl_shuffle_pick; its delay slot (a0=1) stays
# Hold Return: only on_wm_keylong_fun's final Home call (bal) changes; all stock gates and its release
# guard precede it. It becomes jal compact_now_playing (docs/ipod.md#hold-return).
HOLD_RETURN = (0x4f29cc, 0x0411117c)
# Hold Play/Pause and Return: input_device_status' long press is 1000 ms for every key but 232 (unused on the
# Q2). The key-down's lw $t9 timer_add becomes move $a3, $s3 (the key) and its jalr jal ringnav_hold_timer; the
# timer's bal input_device_status_find_key_long_press_info becomes jal ringnav_hold_info. Both give KEY_HOLD_MS
# to those two keys (patch/navigation.c, docs/internals.md#queue-menu).
HOLD_KEYS = ((0x6b3658, 0x8f99b228, 0x02603825, None), (0x6b3668, 0x0320f809, None, 'ringnav_hold_timer'),
             (0x6b39b8, 0x0411ffa4, None, 'ringnav_hold_info'))
# V1.36's input_device_status takes a key-down for a press it has ended (should_abort: the window in front changed
# under it) as a new press: it clears the key's record and times it again, where V1.32's kept the record and dropped the
# key until its release. The Q2's buttons repeat their down while held, so a hold that opened a window came back to life
# in it: its release was delivered there (Play/Pause played or paused under its own menu, #303) and the hold fired again
# once the new time had run. The branch past the clearing is taken always, as V1.32's code
# (docs/internals.md#queue-menu).
KEY_ABORTED = (0x6b35fc, 0x10400011, 0x10000011)
# device_standby's bal awake_screen after the resume from Standby becomes li $v0, 1: the screen stays dark,
# so the resuming key acts as it does dark (Play plays, centre wakes).
STANDBY_WAKE = (0x5024b4, 0x0411ff26, 0x24020001)
# config_init reads SYSSET NAVHIDE and WHEELMODE, V1.36's Show Navigation Bar and Wheel Function, into g_navhide_flag and
# g_wheelmode. Q2 Muse's themes own the bar and every list's box, and its wheel is built on stock's Volume Mode (1): NAVHIDE's
# sb stores $zero (the bar shown) and WHEELMODE's movz $v0, $zero, $v1 becomes li $v0, 1, whatever the keys hold. Neither
# key is written, and stock's two rows are not offered (navigation.c ringnav_display, ringnav_systemset).
NAV_SHOWN = (0x504b5c, 0xa0620000, 0xa0600000)
WHEEL_MODE = (0x504ba4, 0x0003100a, 0x24020001)
# V1.36's home_page_init binds a key-up of its own on Home (0x534a74: Return opens Now Playing and stops there; its wheel and
# Centre act only outside Volume Mode). Home's keys are ringnav's and on_wm_keyup_fun's, as they were: the widget_on call goes.
HOME_KEYUP = (0x534c28, 0x0320f809, 0)
# V1.36's Now Playing key-up handler takes Centre: the screen goes off 350 ms on, or at once on a second release
# (power_keyup_detect), and the release ends there. ringnav takes Centre on that page first, and what it passes on is for
# on_wm_keyup_fun: the screen-off of a single press, and stock's power-key rules (a boot key's or a held key's release,
# the guard after Standby), whose flags only that function clears. So the handler's test for Centre never matches (its
# bne is a b), and Centre is one more key it knows nothing of, as until V1.34.
NP_CENTRE = (0x53dbac, 0x1443000d, 0x1000000d)
# Previous restarts the track from 3 s in, as an iPod's does (docs/ipod.md#previous-and-next), and goes back a track
# before that: player_seek_start's test of player_playtime against 11 (slti $2, $2, 11) is against 3.
# patch/playback.c ringnav_seek_start asks the same question for the restart guard.
PREV_RESTART = (0x51f0ec, 0x2842000b, 0x28420003)
# V1.36's on_wm_keyup_before_fun applies Buttons lock on the lit lock window too (beqz g_lockscreen_pageflag past the lock).
# Buttons lock stays the dark screen's (navigation.c lock_names), as V1.32's: the branch is taken always.
KEYLOCK_LIT = (0x4f2728, 0x10400010, 0x10000010)
# config_init stores SYSSET SCREENSAVER's 0 or 1 in g_screensaver_mode (sb $2); it stores 0 (sb $zero) whatever
# the file says, so stock's wake never opens its lock window (docs/internals.md#screen-off-and-wake).
SAVER_OFF = (0x504c78, 0xa0620000, 0xa0600000)
# V1.36's volume_dialog_on_keyup leaves at once with the screen off (beqz g_backlight_status), so a dark wheel tick over the
# volume dialog falls through to on_wm_keyup_fun, which opens "dialog/volume_dialog" again: navigator_to looks an open window
# up by that path, never its name "volume_dialog", so every tick stacked one more dialog and none changed the volume. Each
# closes on its own 3 s only once on top, so the lit screen sat under them (#303). The dialog takes its keys dark as V1.32's
# (no branch); its hook (volume.c vol_keyup) still sends a dark wheel tick on, to on_wm_keyup_fun's, which drops it.
VOL_DARK = (0x4a36b4, 0x10400045, 0)
# Now Playing More's Artist info (0x5398fc) asks navigator_window_is_exist for "artistinfo_page" and "albuminfo_page", then
# closes "localmusic/artistinfo_page" and "localmusic/albuminfo_page", the names it opens them by: navigator_close finds a
# window by its own name (widget_child, a strcmp), which has no folder, so neither ever closed and a second artist page
# opened over the first. The artist page's record is one global its close frees (0x4aec54), so the older page's close freed
# the newer's strings, and the newer's freed them again (#303). Each close takes the name its check asked for, the addiu's
# immediate (docs/internals.md#queue-menu). ponytail: only this handler opens an artist page over another (qm_goto's rows are
# left out while one is open); a new opener must close the old page first, or the page's init wants the guard.
# The names' addresses ("artistinfo_page", "albuminfo_page") give the words their low halves, so a rebase carries them.
MORE_ARTIST_CLOSE = ((0x5399d4, 0x2624ccf8, 0x26240000 | 0x786994 & 0xffff), (0x539a00, 0x2484ccdc, 0x24840000 | 0x779d3c & 0xffff))
# on_wm_idle_status, stock's idle callback (every UI pass), calls player_mainscheduling (jalr $t9), whose
# mclAutoChange asks hciplayer for its status and waits for the answer: 40 to 125 round trips a second.
# The call goes through ringnav_mainscheduling (navigation.c), which paces it (docs/internals.md#battery).
MAINSCHEDULING_CALL = (0x4f42a4, 0x0320f809)
# mclStartPlayer's wired mute and sleep from the idle player (crossfade.c start_mute, docs/internals.md#wired-start).
START_MUTE_CALL = (0x5bf8c4, 0x0411fefa)  # bal mclSetMute; its delay slot (a0=1) stays
START_SLEEP_CALL = (0x5bf8dc, 0x0320f809)  # jalr usleep, a0=100000
# checkBluetoothStatus' jalr player_save_start once a headset's A2DP comes up (a0 0: the queue restarts
# unpaused, whatever it was doing). It becomes jal ringnav_bt_connect_start (docs/internals.md#resume-on-connect).
BT_CONNECT_START = (0x4fdcbc, 0x0320f809)
# platform_init's control thread start: install callbacks before it discovers boot connections.
BT_INIT = (0x51d248, 0x0320f809)
BT_PARAMS = (0x4fe1e4, 0x0411f5ef)  # config_bt_init's reset moves before the control thread too
# openBluetooth's jalr btctl_device_status_callback(0x4fc180) registers headset_bt_status instead, and
# autoLinkBluetooth's bal checkPairDev also skips while the link list holds a connected link.
BT_STATUS = (0x4fa700, 0x0320f809)
BT_AUTOLINK = (0x4fbf2c, 0x0411fd04)
# on_wm_idle_status's two "Bluetooth abnormal" toasts, each before its device_reboot (bal navigator_to_with_context
# at 0x4f4278 and 0x4f448c, the name loaded in the delay slot), become jal bt_stopped_toast: Bluetooth Stopped's
# page (patch/bt_stopped.c) says what happened instead, and Not Now keeps the loop's later passes quiet.
BT_ABNORMAL_TOASTS = ((0x4f4278, 0x04110afe), (0x4f448c, 0x04110a79))
# player_parsecover_thd's jalr toolsGetExternCover (lw $t9 at 0x51dcbc; Now Playing's folder art, after its
# embedded art failed) becomes jal coverflow_player_cover, which gives ffmpeg what stock's decoder refuses.
PLAYER_COVER = (0x51dccc, 0x0320f809)
# Stock's shared database connection is opened lazily by 0x5c8d30, and again after any exec error closes it: its
# jalr sqlite3_open (lw $t9 = GOT[-0x544] at 0x5c8d60; a1 = the handle in the jalr's delay slot) becomes jal
# stock_db_open, which sets a busy timeout on it, so stock's writers wait out playlist art's reader. Stock's own
# PRAGMA auto_vacuum on the new connection (since V1.36) runs after it as before.
STOCK_DB_OPEN = (0x5c8d64, 0x0320f809)
# player_get_id3info's jalr strcpy of the parsed path (lw $t9 at 0x51f5c8), which every fresh parse reaches
# before Now Playing's lines, becomes jal ringnav_id3_path: a CUE track without sheet tags takes its file's.
ID3_PATH = (0x51f5d0, 0x0320f809)
# The Now Playing timer's jalr scroll_view_scroll_to(scroll_lrc, 0, y, 10) (lw $t9 at 0x53d6d4), its lyric
# follow, becomes jal ringnav_lyric_follow, which pads the content so the view's end is a row top.
LYRIC_FOLLOW = (0x53d6e4, 0x0320f809)
# hscroll_label's timer, at a looping label's end of pass, adds its lull with jalr widget_add_timer (lw $t9 at
# 0x5fb954): jal ringnav_hscroll_lull, which holds the end MARQUEE_HOLD_MS and snaps the label back to its
# start when the lull ends, so the start's delay (ringnav_hscroll_start) holds there instead of at the end.
HSCROLL_LULL = (0x5fb958, 0x0320f809)
# A pass of a label that doesn't loop ends at once, its timer removed (bnez loop at 0x5fb870 falls through), so
# stock paints its start with the ellipsis the moment it reaches its end. The branch goes to the lull for
# every label (b 0x5fb930): ringnav_hscroll_lull holds the end, and its end stops a single pass there.
HSCROLL_ONCE = (0x5fb870, 0x1440002f, 0x1000002f)
# A stopped label with ellipses set paints through widget_draw_text_in_rect(..., ellipses 1) (jalr at 0x5fc074,
# lw $t9 at 0x5fc060, the 1st of hscroll_label_on_paint_self's 2 calls; the 2nd draws without): jal
# ringnav_ellipsis_text, which narrows the box so the cut before stock's "..." never ends on a space.
HSCROLL_ELLIPSIS = (0x5fc074, 0x0320f809)
# Since V1.36 set_hscroll_label_attribute gives a label a loop_interval_distance of its own width (widget_set_prop_int,
# its jalr): the text runs off the label and comes back in after that gap, without an end to hold. Q2 Muse's labels hold
# at the start and the end and snap back (ringnav_hscroll_start, ringnav_hscroll_lull), so the call goes and the
# distance stays at its default, -1, with which hscroll_label's range, timer and paint are V1.34's.
HSCROLL_GAP = (0x4f1f4c, 0x0320f809, 0)
# The same function sets only_parent_focus in Menu Mode alone, and V1.36's Albums rows call it where V1.34's set a
# focus flag outright: the list row's artist line (only_focus) and each grid cell's title (only_parent_focus). With
# neither, those four labels would scroll by themselves as the page paints. Their calls (jalr, the lw $t9 before each)
# become jal ringnav_album_label, which sets the flag after stock's call (navigation.c).
ALBUM_LABELS = (0x4a6cb8, 0x4a6f9c, 0x4a71e4, 0x4a742c)
# V1.36 puts the focus back on the row that had it once the album page or Playlists has loaded its rows again, for
# Menu Mode: widget_set_focused, which scrolls the list to that row, on a row it judges in view by stock's 78 px
# pitch. Q2 Muse's selection is ringnav's and its rows are a theme's pitch, so neither page finds a focused row: the
# album page's beqz past the look is taken always, and Playlists' bnez to it never.
FOCUS_RESTORE = ((0x4a8ee4, 0x1040000c, 0x1000000c), (0x4b5948, 0x14400061, 0))
# The page refresh's bal to the lyric row builder (0x53b67c, which ends at offset 0) becomes jal
# ringnav_lyric_rows, which keeps the offset: Play/Pause no longer flicks the lyrics to the top.
LYRIC_ROWS = (0x53d10c, 0x0411f95b)
# Stock's .lrc loader (0x5bbdc8) opens the file it parses with jalr fopen (lw $t9 at 0x5bc304): jal
# ringnav_lrc_open, which keeps a copy that ringnav_lyric reads again, without its blank lines and, for iPod's
# lyrics page, with its milliseconds and word stamps.
LYRIC_OPEN = (0x5bc30c, 0x0320f809)
# parse_albumcovertask_thd's jalr toolsGetAlbumCover (Albums grids; Folder view's folder and track rows),
# each with its lw $t9, become jal coverflow_grid_cover, which gives ffmpeg the Ogg pictures stock skips;
# the Albums grid's (first, before stock's toolsGetExternCover) jal coverflow_album_cover, and the folder
# row's jal coverflow_folder_cover, which tries the folder's first song by name first.
GRID_COVER = ((0x4f863c, 0x4f8638, 'coverflow_album_cover'), (0x4f9738, 0x4f9728, 'coverflow_folder_cover'),
              (0x4f97f4, 0x4f97f0, 'coverflow_grid_cover'))
# The bal toolsTrimLeft on each name copy in the two library name comparators (0x5cc870, 0x5cd188, the
# Chinese and other-language sorts); they become jal ringnav_sort_key, which also drops a leading article.
SCAN_REFRESH = (0x5dc4d8, 0x0411fd8d)  # toolsLoadAllFile's existing-song check, folder scans only
AUDIO_EXTENSION_CALLS = (0x5d4cac, 0x5d69fc)  # toolsLoadDirectory and recursive scan
M3U_DIR_CALL = 0x518768  # importPlayList's toolsGetDirectoryM3u8 listing (card apps' own imports)
SORT_TRIMS = (0x5cc968, 0x5cc9d4, 0x5cd280, 0x5cd2ec)
# The comparators toolsQueryDbTable (name sort) and toolsQueryDbTableAndSort (modes 1 and 2, else the name
# sort) hand algo_sort_if, by the flag at 0xa5011d (set: the first of a pair), as stock.h defines for
# name_order.c, which sorts with the same one: each the lw $v0, GOT / addiu $v0 pair that loads it there.
NAME_COMPARATORS = {'NAME_CMP': (0x5dae48, 0x5cd188), 'NAME_CMP_PINYIN': (0x5daee8, 0x5cc870),
                    'FILE_NAME_CMP': (0x5db080, 0x5ca690), 'FILE_NAME_CMP_PINYIN': (0x5db11c, 0x5c7c04),
                    'PATH_CMP': (0x5db1a8, 0x5cda0c)}
# Tag text as tagged (docs/internals.md#tag-text): toolsFixId3ErrorCode (0x5d8950), which toolsGetMusicInfo
# runs on each of its six fields, took any field whose every character TagLib decoded below U+0100 ("Lo que
# la música me enseñó") for GBK bytes when they went to UTF-8 and back unchanged, and showed it in Chinese
# ("m" + 鷖...). That round trip's strcmp (jalr, with the round trip's copy in its delay slot) becomes jal
# ringnav_gbk_tag, which also asks that the bytes read as GBK; otherwise TagLib's UTF-8 copy is kept.
TAG_TEXT = (0x5d8ad4, 0x0320f809)
# toolsGetMusicInfo's genre strcpy (dst info+0x200 at 0x5d8f28, the 0x80-byte style field before
# title at +0x280) becomes jal ringnav_genre_copy. Its delay slot still loads the joined genre into
# a1; bounding this one copy covers both the library scan and the player's tag reads.
GENRE_COPY = (0x5d8f34, 0x0320f809)
# The CUE sheet parser (the unnamed function after toolsFreeStSongInfo) ran each field through the same
# toolsIconvConvert(field, "utf-8", "gbk"); its seven bal become jal ringnav_cue_text.
CUE_TEXT = (0x5d3328, 0x5d33ec, 0x5d34a4, 0x5d3560, 0x5d3820, 0x5d3bdc, 0x5d3f64)
# dialog_modal (0x65271c) ends by queueing its dialog's close (0x6521dc) with idle_add(close, dialog): a
# bare pointer, so a dialog freed before that idle ran was read from freed memory. Its bal (the close in
# its delay slot) becomes jal ringnav_dialog_close (system.c), which closes only a dialog still open.
DIALOG_CLOSE = 0x652874
# The library scan's folder walk (the unnamed recursive function toolsLoadAllFile runs, 0x5d67bc) skips
# ".", "..", .LOST.DIR, lost+found and the like by name; its first strcmp (jalr, with "." in its delay
# slot) becomes jal ringnav_scan_dir, which skips every hidden folder, as Folder view's list does: a card
# app's own (q2-deezer's stream cache) never joins the Library.
SCAN_DIRS = (0x5d6864, 0x0320f809)
# Library Albums' cover cache names (navigation.c art_key): the row renderer's snprintf of the name,
# with a2 the row (addiu a2,"%s" becomes move a2,s3), the Albums page's albumcoverinfo_init calls
# and the album page's big cover snprintf become jal to the payload; so do an artist page's albums
# renderer (its row in s2) and albumcoverinfo_init calls on tab entry, return and init.
ALBUM_ART = ((0x4a52bc, 'ringnav_art_name'), (0x4a5c00, 'ringnav_albumcovers'), (0x4a5c68, 'ringnav_albumcovers'),
             (0x4a757c, 'ringnav_albumcovers'), (0x4a910c, 'ringnav_art_header'),
             (0x4adb4c, 'ringnav_artist_art_name'), (0x4aeb70, 'ringnav_artist_covers'),
             (0x4b07f0, 'ringnav_artist_covers'), (0x4b12ac, 'ringnav_artist_covers'))
ALBUM_ART_ROW = ((0x4a52c0, 0x24c6ae54, 0x02603025), (0x4adb50, 0x24c6ae54, 0x02403025))
# albumcoverinfo_init's and foldercoverinfo_init's jalr _deque_push_back of each row's cover task becomes jal
# ringnav_cover_push, which frees the task's name and path once the list has copied them; see docs/internals.md.
COVER_PUSH = ((0x4f79c4, 0x4f79b8), (0x4f7b6c, 0x4f7b64))  # the jalr, the lw t9 that loads it
# Library Artists rows: the row bind's bal to class_icon becomes jal ringnav_class_icon, which hides the head.
CLASS_ICON = 0x4b37e4
# Album grids' and Songs' scroll start no longer pauses the cover worker (sw $3,0($2) -> nop); see docs/internals.md.
COVER_PAUSE = ((0x4ace50, 0xac430000, 0), (0x4a3930, 0xac430000, 0), (0x4a9c20, 0xac430000, 0))
# The playlist, album and favourite-tracks threads' done store (sb $3,0($2) -> nop): their hooks
# (tidal.c tidal_pages) run them once per page and set the page's flag after the last.
TIDAL_PAGE_DONE = ((0x48c31c, 0xa0430000, 0), (0x48c4ac, 0xa0430000, 0), (0x4891d4, 0xa0430000, 0))
# An artist's albums scroll end divides yoffset by its 160 px rows, not 256; see docs/internals.md.
ARTIST_COVER_ROW = ((0x4b0adc, 0x244400ff, 0x240400a0),   # addiu $4,$2,0xff -> li $4,160
                    (0x4b0ae4, 0x28430000, 0x0044001a),   # slti $3,$2,0 -> div $zero,$2,$4
                    (0x4b0ae8, 0x0043200a, 0),            # movz $4,$2,$3 -> nop
                    (0x4b0af0, 0x00042203, 0x00002012))   # sra $4,$4,8 -> mflo $4
# switchBtPriority sends LDAC quality 0x16 (SQ, 660 kbps) for both HQ (row 0) and Standard (row 1).
# The dispatch keeps 0x15 + row in its free 0x14($sp) slot, so HQ sends 0x15 (990 kbps); see docs/internals.md.
LDAC_TABLE = 0x24422e30  # stock's addiu $2,$2,<its jump table's low half>, which the lw that replaces it keeps
LDAC_HQ = ((0x4faa5c, 0x00042080, 0x00041880),   # sll $3,$4,2
           (0x4faa60, LDAC_TABLE, 0x00431021),   # addu $2,$2,$3
           (0x4faa64, 0x00442021, 0x8c420000 | LDAC_TABLE & 0xffff),   # lw $2,<the table's low half>($2)
           (0x4faa68, 0x8c820000, 0x24840015),   # addiu $4,$4,0x15
           (0x4faa74, 0x00000000, 0xafa40014),   # sw $4,0x14($sp), jr's delay slot
           (0x4faa80, 0x24040016, 0x8fa40014))   # lw $4,0x14($sp), was li $4,0x16
# check_mem_thd (platform_init's detached thread) returns NULL on entry: it never opens drop_caches,
# writes 3 to it or wakes each second. lui/addiu $gp -> jr $ra; move $v0,$zero.
DROP_CACHES = ((0x51cd00, 0x3c1c0052, 0x03e00008), (0x51cd04, 0x279cdde0, 0x00001025))
# Stock V1.36's main loop starts a thread of its own on /dev/input/event4, BlueZ's AVRCP keys, and posts Play and Pause
# alike as the Play button, Next and Previous as the skip buttons. patch/headset.c's worker reads that device already,
# and tells Play from Pause, holds a connecting headset's Play for Resume on Connect and sends the keys to the player
# that has the sound, so each press would act twice. btinput_thread_run returns NULL on entry, as DROP_CACHES' thread
# does, which its caller (0x6357b0) takes as no such device: the thread never starts.
BT_INPUT = ((0x639320, 0x3c1c0040, 0x03e00008), (0x639324, 0x279c17c0, 0x00001025))
# Two threads started at boot polled a start flag with usleep(10000) for the life of the player: Baidu
# Pan's (baidu_pthread_playurl) and Tidal's URL worker (after tidalGetFavoritesIdsThd). Each poll call
# (jalr $25) becomes jal to its payload function (tidal.c), which parks the thread: Tidal's wakes once
# per track URL asked for, Baidu's never.
STREAM_POLL_CALLS = ((0x4805b8, 'ringnav_baidu_poll'), (0x4871a4, 'ringnav_tidal_poll'))
# The Library's song rows and Play All ask access(path, R_OK) (lw $25,-0x4b0($gp), the word before each jalr)
# before they play, and say File Not Found (msg_notfile) when it fails: a server row (patch/catalogue.c) has no
# file. Each jalr becomes jal catalogue_access, which passes a server row on to ringnav_load's own check.
# Songs, Songs' Play All, an album's song and Play All, an artist's song, Library's class rows, Search's track
# result and Now Playing's jump list.
CATALOGUE_ACCESS = (0x4aad8c, 0x4abfe0, 0x4a7d34, 0x4a9798, 0x4af6a0, 0x4b2130, 0x49d17c, 0x537e50)
ACCESS_GOT_LOAD = 0x8f99fb50  # lw $25, -0x4b0($gp): access
# get_onlineid3info's player_set_coverinfo(REC_COVER, REC_NAME, type) for a 0xf1 record: its jalr, the
# REC_COVER load in its delay slot, and the stub that adds the record ($s1) for tidal.c app_coverinfo
COVERINFO_CALL = (0x493708, 0x8e240028, 'app_coverinfo_call')
# Stock routines tidal.c's URL queues call at their addresses (not FUNCTIONS, whose names test/patch.py
# stands in for everywhere: these run as stock's in its scenarios). Checked against the ELF's symbols.
APP_URL_CALLS = {'mclSetPause': 0x5bd7c4, 'tidal_playurl': 0x5bc914, 'curl_stop_httpget': 0x506168,
                 'toolsFreeStSongInfo': 0x5d2e10, 'player_set_coverinfo': 0x51d7fc}
# The jalr usleep (lw $t9,-0x4e4($gp)) of mclSyncPlayerStatus' 200 ms loop becomes jal ringnav_dark_usleep
# (its argument set in the delay slot): at least DARK_POLL_US while ringnav_sleep finds the Q2 dark, quiet and
# the DAC off, else stock's. parse_albumcovertask_thd's 50 ms and player_parsecover_thd's 100 ms waits become
# jal ringnav_dark_cover_usleep: at least DARK_POLL_US whenever the screen is off, playing or not.
DARK_POLLS = ((0x5bbc3c, 'ringnav_dark_usleep'), (0x4f8300, 'ringnav_dark_cover_usleep'),
              (0x51dc5c, 'ringnav_dark_cover_usleep'))
# Download Cover & Lyrics' accept (0x535f24) snprintfs cp -rf /tmp/oslyric_N "/mnt/mmc/.downpic/<name>" for
# system(); that jalr snprintf becomes jal ringnav_lyric_save (onlinematch.c), which writes the lyrics beside
# the song instead. The word before it, addiu $a2,$a2,0x67f0 (the format at 0x7867f0), is pinned.
LYRIC_SAVE_CALL, LYRIC_SAVE_FMT = 0x536488, (0x536484, 0x24c667f0)
# Stock's HTTPS helpers turn certificate checks off (curl_easy_setopt(c, SSL_VERIFYPEER or SSL_VERIFYHOST, 0)) and set
# no CA file, so Tidal's sign-in and API, covers by URL and Baidu's calls could be intercepted. Each of those calls
# (a jalr through curl_easy_setopt's GOT slot, $5 0x40 or 0x51 just before) becomes jal ringnav_stock_tls
# (patch/stock_tls.c), which verifies against STOCK_CA. See docs/internals.md#stock-https.
STOCK_TLS_CALLS = (
    (0x481ad8, 0x40), (0x481af4, 0x51),  # baidu_curl_download_file
    (0x4845a8, 0x40), (0x4845c0, 0x51),  # 0x484514: Tidal's favourites added and removed
    (0x48485c, 0x40), (0x484874, 0x51),  # 0x4847c8: a Tidal playlist's tracks added and removed
    (0x484d00, 0x40), (0x484d18, 0x51),  # httpRequest: Tidal's sign-in, browsing and favourites
    (0x506254, 0x40), (0x50626c, 0x51))  # curl_http_request: covers by URL, Baidu, the update check
SETOPT_GOT = 0x8f99f620  # lw $25, -0x9e0($gp): curl_easy_setopt
def stock_tls_site(data, address, option):
    """1 if the word at address is a jalr $25 to curl_easy_setopt with $5 = option, set in the four words before."""
    off = fileoff(data, address)
    before = struct.unpack_from('<4I', data, off - 16)
    return struct.unpack_from('<I', data, off)[0] == 0x0320f809 and SETOPT_GOT in before and 0x24050000 | option in before
# Three of stock's Tidal API URLs are plain HTTP, though they carry the session's access token: the subscription check,
# a track's stream URL (playbackinfopostpaywall) and an album's tracks. Each becomes https://, taking one of the NULs
# that pad it to its word; only its start is referenced (docs/internals.md#stock-https).
TIDAL_HTTP = (b'/v1/users/%u/subscription',
              b'/v1/tracks/%lld/playbackinfopostpaywall?playbackmode=STREAM&assetpresentation=FULL&audioquality=%s',
              b'/v1/albums/%u/tracks?offset=%d&limit=%d&countryCode=%s')
def tidal_https(data):
    """data with each TIDAL_HTTP URL made https://, in place; each must be whole and once, with a spare NUL after it."""
    for path in TIDAL_HTTP:
        old, new = b'\0http://api.tidal.com' + path + b'\0\0', b'\0https://api.tidal.com' + path + b'\0'
        check(data.count(old) == 1, f'Unexpected Tidal URL {path.decode()}')
        data = data.replace(old, new)
    return data
# Mozilla's CA bundle as curl publishes it, whole, for stock's HTTPS (above) and the player's (tools/peq.py TLS_OPTIONS),
# whose hosts no list of ours could cover; /etc/scrobble-ca.pem stays the few roots Q2 Muse's own code needs.
STOCK_CA = 'etc/ssl/certs/ca-certificates.crt'
STOCK_CA_SHA = '796f1389bfea13c0ed78f663b765baaff54a508e5483786bd6ece73c5e3e3cbe'  # https://curl.se/ca/cacert-2026-10-20.pem
# The same function's other command, no lyrics chosen: rm -rf "/mnt/mmc/.downpic/<name>" for the same system().
# Its jalr snprintf becomes jal ringnav_lyric_drop (onlinematch.c), which unlinks the copy itself. The call's delay
# slot, addiu $a2,$a2,0x6814 (the format at 0x786814), is pinned.
LYRIC_DROP_CALL, LYRIC_DROP_FMT = 0x53650c, (0x536510, 0x24c66814)
# player_parsecover_thd snprintfs cp -f "/mnt/mmc/.downpic/<file name>.jpg" '/tmp/externpic.jpg' for system() when
# that pick exists. Its jalr snprintf becomes jal ringnav_cover_copy (onlinematch.c), which copies the file itself.
# The delay slot, addiu $a2,$a2,0x2974 (the format at 0x782974), is pinned.
COVER_PICK_CALL, COVER_PICK_FMT = 0x51db14, (0x51db18, 0x24c62974)
# remove_file snprintfs rm -rf "<folder>" for system(). Its jalr snprintf becomes jal ringnav_folder_rm
# (folder_delete.c), which single-quotes the path. The delay slot, addiu $a2,$a2,0x2ff8 (the format at 0x782ff8), is pinned.
FOLDER_RM_CALL, FOLDER_RM_FMT = 0x5000f0, (0x5000f4, 0x24c62ff8)
# The encoder thread's ts_read call (jalr $t9) becomes jal ringnav_ts_read, which makes the wheel's
# ticks from the samples (playback.c wheel_sample).
WHEEL_TS_READ = (0x638a4c, 0x0320f809)
# Smart Wi-Fi (smart_wifi.c smart_ready): networkStart and networkStop act only when initNetworkModule's
# type, the unnamed word NET_TYPE (patch/offsets.inc), is 2: lw $v1,-0x53c($v0) off GOT page 0xa50000;
# li $v0,2.
NET_TYPE_READS = ((0x5c1d6c, 0x8c4300d4), (0x5c1d70, 0x24020002), (0x5c2078, 0x8c4300d4), (0x5c207c, 0x24020002))
# English labels renamed, as an iPod words them: Now Playing More's queue, which stock calls "Playlists"
# (player_playlist, used nowhere else); the Library's plural categories; settings rows in title case
# and plain words; the Lock screen help; Baidu without its (BETA); My Fav as Favorites, US spelling, the name of
# the row that opens it; and toasts in sentence case.
LABELS = tuple((k.encode(), o.encode(), n.encode()) for k, o, n in (
    ('msg_playlistfull', 'Playlist Is Full', 'Could not create playlist'),
    ('player_playlist', 'Playlists', 'Queue'), ('system_powermanager', 'Idle', 'Battery'),
    ('powerm_deep_off', 'standby/shutdown', 'Standby'),
    ('local_songer', 'Artist', 'Artists'), ('local_album', 'Album', 'Albums'),
    ('local_allsongs', 'All Songs', 'Songs'), ('local_playlist', 'Playlist', 'Playlists'),
    ('playset_bootvol', 'Default Vol', 'Default Volume'),
    ('playset_covermode', 'Enlarged album art(10s wait)', 'Enlarged Album Art'),
    ('playset_filter', 'Digital filters', 'Digital Filters'),
    ('playset_memplay', 'Memory playback', 'Memory Playback'),
    ('playset_noplay', 'Headphones required for playback', 'Headphones Required'),
    ('replaygain_track', 'Audio', 'Song'),
    ('small_playset', 'Playback Setting', 'Playback'), ('small_sysset', 'System Setting', 'General'),
    ('system_wifiset', 'Wireless Setting', 'Wireless'), ('system_display', 'Display setting', 'Display'),
    ('system_netservice', 'Network Service', 'Network'), ('system_time', 'Date and time', 'Date & Time'),
    ('netservice_wifitrans', 'Wi-Fi file transfer', 'Wi-Fi File Transfer'),
    ('system_keylock', 'Buttons lock', 'Buttons Lock'), ('system_keytone', 'Key tone', 'Key Tone'),
    ('system_carmode', 'In-Vehicle mode', 'In-Vehicle Mode'), ('system_reset', 'Reset settings', 'Reset Settings'),
    ('reset_initset', 'Reset only settings', 'Reset Settings Only'),
    ('reset_factory', 'Full factory reset', 'Full Factory Reset'),
    ('display_lighttime', 'Screen off time', 'Screen Off Time'),
    ('about_sn', 'Serial number', 'Serial Number'), ('about_version', 'FW. Version', 'Firmware'),
    # About's rows as approved (audit-2 #31, 2026-11-16): sizes with their unit, the MAC by what it is
    ('about_cardcap', 'Card Capacity', 'Capacity'), ('about_cardremain', 'Remaining', 'Available'),
    ('about_wifimac', 'Wi-Fi MAC', 'Wi-Fi Address'),
    ('list_baidu', 'Baidu Netdisk(BETA)', 'Baidu Netdisk'), ('msg_poweroff', 'Confirm Shutdown?', 'Shut Down?'),
    ('system_prove', '技术证明', 'Certification'),  # About's regulatory row, Chinese in stock's English
    ('genre', 'Genres', 'Genre'), ('player_era', 'Era', 'Year'), ('batch_addplaylist', 'Add to playlist', 'Add to Playlist'),
    ('msg_importplaylist', 'Import Playlist?', 'Import Playlists?'),  # it imports the whole folder
    ('fav', 'My Fav', 'Favorites'), ('local_fav', 'My Fav', 'Favorites'),  # the Favorites page's title, as Home's row names it
    ('scan_startscan', 'Scanning', 'Scan'),  # Scan specified folders' button; wifi_scan keeps its "Scanning"
    ('msg_addplaylistsuccess', 'Added To Playlist', 'Added to Playlist'),
    ('msg_addfavsuccess', 'Added To Fav', 'Added to Favorites'), ('msg_favsuccess', 'Added To Fav', 'Added to Favorites'),
    ('msg_namenull', "The name can't be empty", "Name can't be empty"),
    ('msg_lowarn', 'Line Out sets max volume,continue?', 'Line Out sets max volume, continue?'),
    ('scan_now', 'Scan music now', 'Scan Music Now'), ('scan_spec', 'Scan specified folders', 'Scan Specified Folders'),
    # Settings > Bluetooth (bt_ui.c, docs/oled-design.md#bluetooth): the device lists' headers, a paired device's
    # state, the Audio group's rows and the Quality picker's choices in the design's words; Unpair is Forget
    ('bt_paireddev', 'Paired Device', 'My Devices'), ('bt_compatiabledev', 'Compatible Device', 'Other Devices'),
    ('msg_paired', 'Paired', 'Not Connected'), ('msg_connecting', 'Connecting', 'Connecting…'),
    ('bt_adjvol', 'Bluetooth Headset Volume Control', 'Headset Volume'),
    ('btcode_ldachq', 'LDAC HQ', 'LDAC Best'), ('btcode_ldacauto', 'LDAC Connection (auto)', 'LDAC Auto'),
    ('msg_unpairing', 'unpair?', 'Forget This Device?'), ('msg_unpaired', 'Unpaired', 'Device Forgotten'),
    # the card is an SD card in every label (docs/oled-design.md Copy): stock says TF
    ('update_tf', 'TF card update', 'SD Card Update'), ('msg_nottf', 'No TF Card Found', 'No SD Card Found'),
    ('msg_tfeject', 'TF Card Removed', 'SD Card Removed'), ('msg_tfinsert', 'TF Card Inserted', 'SD Card Inserted'),
    # Screen Off Time's and Standby's choices spelled out, as iOS's Auto-Lock words them (the menu review's,
    # 2026-11-15): "30 seconds", "1 minute"
    *((f'lighttime_{n}s', f'{n}s', f'{n} seconds') for n in (15, 30)),
    *((f'{page}_{n}min', f'{n}min', f'{n} minute' + 's' * (n > 1)) for page, choices in (
        ('lighttime', (1, 2, 5, 30)), ('standbyoff', (1, 3, 5, 10))) for n in choices)))

# Wi-Fi transfer's web page (stock thttpd on :8888): in English, an Upload Folder button, and a folder,
# picked or dropped, keeps its tree: each file's folders are made with stock's create (a plain mkdir) before
# it goes, as stock's upload only moves a file into an existing folder. The formats Q2 Muse's pages read are allowed too.
MKDIR_JS = r'''// Each folder of a folder upload is made once per batch, parents first; one that exists is no
// error, and a folder that really couldn't be made shows as its files' upload errors.
var _made = {};
function _mkdir(path, base) {
  if (path == base)
    return $.Deferred().resolve();
  if (!_made[path]) {
    var made = $.Deferred();
    _mkdir(path.replace(/[^\/]*\/$/, ""), base).always(function() {
      $.ajax({url: 'create', type: 'POST', data: {path: path.slice(0, -1)}, dataType: 'json'})
        .always(function() { made.resolve(); });
    });
    _made[path] = made;
  }
  return _made[path];
}

function _showError('''
# The breadcrumb is built as HTML: _esc puts each folder's name into it as text, as a folder named like
# markup would otherwise run as script in the page (docs/internals.md#wi-fi-transfer).
ESC_JS = r'''// A file or folder name as HTML text, quotes included, for markup built from strings.
function _esc(s) {
  return $("<div>").text(s).html().replace(/"/g, "&quot;");
}

function _reload(path) {'''
# Wi-Fi transfer's PIN (docs/internals.md#wi-fi-transfer-pin): every CGI answers 401 until the page has
# sent the PIN the Q2 shows to cgi-bin/auth.cgi, which sets the session cookie; the browser then sends it
# with each request. Any 401 opens the PIN box, so a page left open across a restart of transfer asks for
# the new PIN.
PIN_JS = r'''// The PIN the Q2 shows on its Wi-Fi File Transfer screen, asked for once; the server answers it
// with a session cookie the browser sends with every request after.
var _pinFor = null;
function _askPin(path) {
  _pinFor = path;
  if ($("#pin-modal").hasClass("in"))
    return;
  $("#pin-error").text("");
  $("#pin-input").val("");
  $("#pin-modal").modal("show");
}
function _sendPin() {
  var pin = $("#pin-input").val().replace(/\s/g, "");
  if (!/^[0-9]{6}$/.test(pin)) {
    $("#pin-error").text("The PIN is six digits.");
    return false;
  }
  $.ajax({url: 'cgi-bin/auth.cgi', type: 'POST', data: {pin: pin}, dataType: 'json'})
    .done(function() {
      $("#pin-modal").modal("hide");
      _reload(_pinFor || _home);
    })
    .fail(function(jqXHR) {
      var r = {};
      try { r = $.parseJSON(jqXHR.responseText) || {}; } catch (e) {}
      $("#pin-error").text(r.error == "locked" ? "Too many wrong PINs. On the Q2, go back, then open Wi-Fi File Transfer again for a new PIN."
        : r.error == "off" ? "Wi-Fi transfer is off on the Q2."
        : "Wrong PIN" + (r.left != null ? " (" + r.left + (r.left == 1 ? " try" : " tries") + " left)." : "."));
      $("#pin-input").val("").focus();
    });
  return false;
}

// A file or folder name as HTML text, quotes included, for markup built from strings.'''
PIN_HTML = '''<div class="modal fade" id="pin-modal" tabindex="-1" data-backdrop="static" data-keyboard="false">
      <div class="modal-dialog">
        <div class="modal-content">
          <div class="modal-header">
            <h4 class="modal-title">Enter the PIN</h4>
          </div>
          <div class="modal-body">
            <p>The Q2 shows a six-digit PIN on its Wi-Fi File Transfer screen, above the address.</p>
            <form id="pin-form">
              <input type="text" class="form-control" id="pin-input" inputmode="numeric" autocomplete="one-time-code" maxlength="7">
            </form>
            <p class="text-danger" id="pin-error"></p>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-primary" id="pin-ok">OK</button>
          </div>
        </div>
      </div>
    </div>

    <div class="modal fade" id="create-modal" tabindex="-1">'''
WEB = {
  'usr/share/web/index.html': [
    ('山灵音乐', 'Shanling Q2'),
    ('支持音频格式:ISO,DFF,DSF,APE,FLAC,AIF,AIFF,WAV,M4A,AAC,MP2,AC3,DTS,MP3,OGG,WMA,CUE,M3U,M3U8<br/>图片格式:PNG,JPG,JPEG<br/>歌词格式:LRC<br/>固件升级包:BIN',
     'Audio: ISO, DFF, DSF, APE, FLAC, AIF, AIFF, WAV, M4A, M4B, AAC, MP2, AC3, DTS, MP3, OGG, WMA, CUE, M3U, M3U8<br/>'
     'Pictures: PNG, JPG, JPEG<br/>Lyrics: LRC<br/>Videos: MP4, M4V, MKV, AVI, MOV, MPG<br/>EQ presets: TXT<br/>Firmware: BIN'),
    ('''上传文件
          <input id="fileupload" type="file" name="files[]" multiple>
        </button>''', '''Upload Files
          <input id="fileupload" type="file" name="files[]" multiple>
        </button>
        <button type="button" class="btn btn-primary fileinput-button" id="upload-folder">
          <span class="glyphicon glyphicon-folder-open"></span> Upload Folder
          <input id="folderupload" type="file" name="files[]" multiple webkitdirectory>
        </button>'''),
    ('<div class="modal fade" id="create-modal" tabindex="-1">', PIN_HTML),
    ('创建文件夹', 'New Folder'), ('返回上一级', 'Up'), ('正在进行文件上传', 'Uploading'), ('新建文件夹', 'New Folder'),
    ('请输入要创建的文件夹的名称:', 'Folder name:'), ('取消', 'Cancel'), ('移动项目', 'Move'), ('请输入此项的新位置:', 'New location:')],
  'usr/share/web/js/index.js': [
    ('"LRC","BIN"];', '"LRC","BIN","M4B","MP4","M4V","MKV","AVI","MOV","MPG","TXT"];'),
    ('function _showError(', MKDIR_JS),
    ('function _reload(path) {', ESC_JS),
    ('// A file or folder name as HTML text, quotes included, for markup built from strings.', PIN_JS),
    ('''    url: 'list',
    type: 'GET',
    data: {path: path},
    dataType: 'json'
  }).fail(function(jqXHR, textStatus, errorThrown) {
''', '''    url: 'list',
    type: 'GET',
    data: {path: path},
    dataType: 'json'
  }).fail(function(jqXHR, textStatus, errorThrown) {
    if (jqXHR.status == 401)
      return;  // the PIN box (_askPin) takes it
'''),
    ('''          $("#path").append('<li data-path="' + subpath + '"><a style="cursor:pointer">' + components[i] + '</a></li>');''',
     '''          $("#path").append('<li data-path="' + _esc(subpath) + '"><a style="cursor:pointer">' + _esc(components[i]) + '</a></li>');'''),
    ('''        $("#path").append('<li class="active">' + components[components.length - 1] + '</li>');''',
     '''        $("#path").append('<li class="active">' + _esc(components[components.length - 1]) + '</li>');'''),
    ('''
  _reload(_home);

});''', '''
  $("#pin-ok").click(_sendPin);
  $("#pin-form").submit(_sendPin);
  $("#pin-modal").on("shown.bs.modal", function() {
    $("#pin-input").focus();
  });
  $(document).ajaxError(function(event, jqXHR) {
    if (jqXHR.status == 401)
      _askPin(_path || _home);
  });

  _reload(_home);

});'''),
    ('''  $("#upload-file").click(function(event) {
    $("#fileupload").click();
  });
''', '''  $("#upload-file").click(function(event) {
    $("#fileupload").click();
  });
  $("#upload-folder").click(function(event) {
    $("#folderupload").click();
  });
'''),
    ('$("#fileupload").click(function(event) {', '$("#fileupload, #folderupload").click(function(event) {'),
    ('    dropZone: $(document),', '    fileInput: $("#fileupload, #folderupload"),\n    dropZone: $(document),'),
    ('''      $(".uploading").hide();''', '''      if (!$("#uploads").children().length) {  // rows still waiting on their folders keep it up
        $(".uploading").hide();
        _made = {};
      }'''),
    ('''      var file = data.files[0];
      if(_isValidFileType(file.name)) {
        data.formData = {
          path: _path
        };
        data.context = $(tmpl("template-uploads", {
          path: _path + file.name
        })).appendTo("#uploads");
        var jqXHR = data.submit();
        data.context.find("button").click(function(event) {
          jqXHR.abort();
        });''', '''      var file = data.files[0];
      // a dropped folder's files carry relativePath ("Album/CD1/"), a picked one's webkitRelativePath ("Album/CD1/01.flac")
      var dir = _path + (file.relativePath || (file.webkitRelativePath || "").replace(/[^\\/]*$/, ""));
      if (dir != _path && file.name.charAt(0) == ".")
        return;  // .DS_Store, macOS's ._ copies
      if(_isValidFileType(file.name)) {
        data.formData = {
          path: dir
        };
        data.context = $(tmpl("template-uploads", {
          path: dir + file.name
        })).appendTo("#uploads");
        $(".uploading").show();  // before its upload starts: it may wait on its folders
        var jqXHR = null;
        _mkdir(dir, _path).always(function() {
          if (data.context.parent().length)  // not cancelled while its folder was made
            jqXHR = data.submit();
        });
        data.context.find("button").click(function(event) {
          if (jqXHR) jqXHR.abort(); else data.context.remove();
        });'''),
    ('"无法获取列表内容 - \\""', '"Can\'t list \\""'),
    ('"无法创建\\"" + name + "\\"到\\""', '"Can\'t create \\"" + name + "\\" in \\""'), ('"无法移动\\""', '"Can\'t move \\""'), ('"\\"到\\""', '"\\" to \\""'),
    ('"无效的命名 - \'\'"', '"Invalid name - \'\'"'), ('"你确定要删除吗?"', '"Delete this item?"'),
    ('"没有权限删除\\""', '"Not allowed to delete \\""'), ('"无法删除\\""', '"Can\'t delete \\""'),
    ('"没有权限上传内容到\\""', '"Not allowed to upload to \\""'), ('"不支持上传此类型文件 - \\""', '"Unsupported file type - \\""'),
    ('"上传文件失败 \\"" + file.name + "\\" to \\"" + _path', '"Can\'t upload \\"" + file.name + "\\" to \\"" + data.formData.path'),
    ('"没有权限添加内容到\\""', '"Not allowed to add to \\""'), ('"新建文件夹"', '"New Folder"'),
    ('"没有权限移动内容到此目录 - \\""', '"Not allowed to move to \\""'), ('"当前已是顶级目录"', '"Already at the top folder"'),
    ('//去掉后面的/', '// drop the trailing /'), ('//得到上一级目录，/结束', '// the parent folder, ending in /')],
  # A dropped folder's entries: Chromium's readEntries gives at most 100 a call, so read until it gives none.
  'usr/share/web/js/jquery.fileupload.js': [
    ('''                dirReader.readEntries(function (entries) {
                    that._handleFileTreeEntries(
                        entries,
                        path + entry.name + '/'
                    ).done(function (files) {
                        dfd.resolve(files);
                    }).fail(errorHandler);
                }, errorHandler);''', '''                var all = [], readEntries = function () {
                    dirReader.readEntries(function (entries) {
                        if (entries.length) {
                            all = all.concat(Array.prototype.slice.call(entries));
                            return readEntries();
                        }
                        that._handleFileTreeEntries(
                            all,
                            path + entry.name + '/'
                        ).done(function (files) {
                            dfd.resolve(files);
                        }).fail(errorHandler);
                    }, errorHandler);
                };
                readEntries();''')]}

def patch_web(path, data):
    text = data.decode()
    for old, new in WEB[path]:
        check(old in text, f'Unexpected {path}: {old[:40]!r}')
        text = text.replace(old, new)
    check(not re.search('[　-鿿＀-￯]', text), f'Chinese left in {path}')
    return text.encode()

# Stock's UI assets carry Chinese text (docs/internals.md#english), most of it designers' placeholders that
# stock replaces, but some shows through in every language (Search's tabs, Now Playing's info labels, Folder's
# title, Quick Settings' row names, the test-mode pages). Every Chinese text in them is given in English
# (english_ui); a per-asset entry wins over the shared one. The T9 keyboard's EN key opened its Chinese
# pinyin page: it toggles case instead (ABC on the lowercase page, abc on the capitals), so that page is
# never reached, and its 《》 key types «».
CJK = re.compile('[\u3000-\u9fff\uff00-\uffef]')
ENGLISH = {
    '文件夹': 'Folder', '正在播放': 'Now Playing', '本地音乐': 'Library', '更多': 'More', 'AB复读': 'A-B Repeat',
    '倍速': 'Speed', '*仅对含48KHz以下有效': '*Only for 48 kHz and below', '无歌词': 'No lyrics', '专辑': 'Album',
    '风格': 'Genre', '大小': 'Size', '年代': 'Year', '编码': 'Format', '位深': 'Bit Depth', '比特率': 'Bit Rate',
    '采样率': 'Sample Rate', '位置': 'Location', '流媒体': 'Streaming', '自动扫描音乐': 'Auto Scan',
    '立即更新曲库': 'Update Library Now', '指定文件夹扫描': 'Scan Folders', '确认': 'OK', '确定': 'OK',
    '取消': 'Cancel', '单曲': 'Songs', '歌手': 'Artists', '蓝牙': 'Bluetooth', '按键锁': 'Key Lock',
    '低增益': 'Low Gain', 'USB存储': 'USB Storage', '播放设置': 'Playback', '设置': 'Settings',
    '自定义': 'Custom', '重置': 'Reset', '在浏览器输入以下网址:': 'Open this address in a browser:',
    '下载中': 'Downloading', '已下载': 'Downloaded', '百度网盘': 'Baidu Netdisk', '退出登录': 'Sign Out',
    '登录': 'Sign In', '请使用百度网盘或百度App扫码登录': 'Scan with the Baidu app to sign in',
    '百度加密技术保护您的隐私安全': 'Baidu encryption protects your privacy',
    '蓝牙定频测试,请操作电脑连接': 'Bluetooth RF test: connect from a computer', '按下一曲退出': 'Press Next to exit',
    '亮度测试': 'Brightness Test', '下一步': 'Next', '蓝牙测试': 'Bluetooth Test', '右': 'Right', '左': 'Left',
    '下': 'Down', '上': 'Up', '左滑': 'Swipe Left', '右滑': 'Swipe Right', '按键测试': 'Key Test',
    '耳机输出测试': 'Headphone Test', '请插单端耳机': 'Plug in single-ended headphones',
    '播放(男左女右)': 'Play (left male, right female)', '蓝牙接收测试': 'Bluetooth Receiver Test', '完成': 'Done',
    '电池检测': 'Battery Test', '当前电量：': 'Battery:', '充电状态：': 'Charging:', 'TF测试': 'SD Card Test',
    '触摸测试': 'Touch Test', 'wifi测试': 'Wi-Fi Test', '太阳之子': 'Album', '周杰伦': 'Artist',
    '播放全部': 'Play All', '导入歌单': 'Import Playlists', '导出歌单': 'Export Playlists', '测试歌单': 'Playlist',
    '共0首': '0 songs',
}
ENGLISH_IN = {'testmode/test_key.bin': {'中': 'Center'}, 'kb_default_t9.bin': {'中': 'abc', '《》': '«»'},
              'dialog/search_dialog.bin': {'专辑': 'Albums', '风格': 'Genres'}}

def english_ui(rel, data):
    """rel's widgets' Chinese text in English, and the T9 keyboard's Chinese page out of reach."""
    from ipod import decode, encode, walk
    root = decode(data)
    check(encode(root) == data, f'{rel}: UI round trip differs')
    upper = {id(n) for page in walk(root) if page[2].get('name') == 'upper' for n in walk(page)}
    for n in walk(root):
        props = n[2]
        if rel == 'kb_default_t9.bin' and props.get('name') == 'page:chinese':  # EN: the case toggle
            props['name'], props['text'] = ('page:lower', 'abc') if id(n) in upper else ('page:upper', 'ABC')
        if rel == 'kb_default_t9.bin' and props.get('name') == 'opt:《》':
            props['name'] = 'opt:«»'
        text = props.get('text', '')
        if not CJK.search(text):
            continue
        props['text'] = ENGLISH_IN.get(rel, {}).get(text) or ENGLISH[text]
    check(not any(CJK.search(n[2].get('text', '')) for n in walk(root)), f'Chinese left in {rel}')
    return encode(root)

# The stock font (Arial merged with a CJK font) gives curly quotes the CJK em advance, a wide gap
# inside words wherever a tag has one; its cmaps map them to the straight glyphs instead. Its hearts
# mix too: the ratings' ♡ (CJK: smaller, higher, the em advance) is fitted to Arial's ♥ box, both a wider advance.
FONT = 'release/assets/default/raw/fonts/default.ttf'
QUOTES = {0x2018: 0x27, 0x2019: 0x27, 0x201c: 0x22, 0x201d: 0x22}

def memo(fn):  # pragma: no mutate block
    """fn, its results kept on disk in $Q2_BUILD_CACHE, keyed by tools/build.py's text, fontTools' version
    and the arguments (a path by its file's bytes): a rebuild with the same fonts skips fontTools' 15 s or so.
    Off unless Q2_BUILD_CACHE names a directory (tools/ci/rebuild.py sets it), so a release's two
    reproducibility builds still compute everything."""
    import functools, os, pickle
    @functools.wraps(fn)
    def cached(*args, **kwargs):
        where = os.environ.get('Q2_BUILD_CACHE')
        if not where: return fn(*args, **kwargs)
        from fontTools import version as fonttools
        h = hashlib.sha256(pathlib.Path(__file__).read_bytes() + fn.__name__.encode() + fonttools.encode())
        for a in [*args, *sorted(kwargs.items())]: h.update(a.read_bytes() if isinstance(a, pathlib.Path) else repr(a).encode())
        path = pathlib.Path(where)/f'{fn.__name__}-{h.hexdigest()}.pickle'
        try: return pickle.loads(path.read_bytes())
        except (OSError, pickle.UnpicklingError, EOFError): pass
        result = fn(*args, **kwargs)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f'.{os.getpid()}'); tmp.write_bytes(pickle.dumps(result)); tmp.replace(path)
        return result
    return cached

@memo
def font_quotes(data):
    """Only cmap is decompiled; fontTools copies every other table's bytes as they are, and head keeps
    its stock timestamp so the build is reproducible."""
    from fontTools.ttLib import TTFont
    font = TTFont(io.BytesIO(data), lazy=True, recalcTimestamp=False)
    for table in font['cmap'].tables:
        if table.isUnicode():
            check(all(c in table.cmap for c in QUOTES.values()), 'Font without straight quotes')
            table.cmap.update({c: table.cmap[q] for c, q in QUOTES.items()})
    glyf, hmtx, cmap = font['glyf'], font['hmtx'], font.getBestCmap()
    full, empty = glyf[cmap[0x2665]], glyf[cmap[0x2661]]
    check(not empty.isComposite() and empty.xMin == 2 and empty.yMax == 159, 'Unexpected ♡ glyph')
    sx, sy = (full.xMax - full.xMin) / (empty.xMax - empty.xMin), (full.yMax - full.yMin) / (empty.yMax - empty.yMin)
    empty.coordinates.transform(((sx, 0), (0, sy)))
    empty.coordinates.translate((full.xMin - round(empty.xMin * sx), full.yMin - round(empty.yMin * sy)))
    empty.coordinates.toInt()
    empty.recalcBounds(glyf)
    hmtx[cmap[0x2661]] = hmtx[cmap[0x2665]] = (hmtx[cmap[0x2665]][0] + 32, full.xMin)  # a gap between hearts
    out = io.BytesIO()
    font.save(out, reorderTables=False)
    return out.getvalue()

PLAY_MARK = 0x25b6

def play_mark(font):
    """U+25B6 drawn into font, a face without one: the mark before View Queue's playing title
    (navigation.c stock_row_style), which a row could otherwise only show in stock's face. A triangle on
    the middle of the capitals, 78% of their height, its left edge a pixel in."""
    cap = font['OS/2'].sCapHeight
    x0, x1, y0, y1 = round(cap * .073), round(cap * .773), round(cap * .11), round(cap * .89)
    name, points = 'uni25B6', [(x0, y0), (x0, y1), (x1, (y0 + y1) // 2)]  # clockwise, as glyf's contours are
    def drawn(pen, points):
        pen.moveTo(points[0])
        for point in points[1:]: pen.lineTo(point)
        pen.closePath()
        return pen
    from fontTools.pens.ttGlyphPen import TTGlyphPen  # every face cut is TrueType (glyf)
    font['glyf'][name] = drawn(TTGlyphPen(None), points).glyph()
    font['hmtx'][name] = (x0 + x1, x0)
    for table in font['cmap'].tables:
        if table.isUnicode(): table.cmap[PLAY_MARK] = name

TABULAR = '0123456789:'

@memo
def subset_font(src, family, ranges_macro, tabular=''):
    """src given a play mark (play_mark), then cut to its Latin, Greek, Cyrillic, punctuation and that
    mark, renamed family (its own name is the font's reserved name under OFL and GUST alike): the font's
    bytes and ranges_macro listing exactly what it keeps. tabular, if any, is the suffix of the font's tabular figures' glyph names (Inter's .tf):
    TABULAR's characters map to those glyphs, as font_quotes remaps stock's quotes, so a clock or a
    song's time keeps its width as it counts."""
    from fontTools import subset
    from fontTools.ttLib import TTFont
    font = TTFont(src, recalcTimestamp=False)
    # the family: a weight's own file names it in ID 16 (Inter Medium's ID 1 is "Inter Medium")
    old = font['name'].getDebugName(16) or font['name'].getDebugName(1)
    label = ranges_macro.split('_')[0].title()
    cmap = font.getBestCmap()
    have = sorted(u for u in cmap if 0x20 <= u < 0x530 or 0x2000 <= u < 0x2200)
    if tabular:
        glyphs = set(font.getGlyphOrder())
        figures = {ord(c): cmap[ord(c)] + tabular for c in TABULAR}
        for name in figures.values():
            check(name in glyphs, f'{label} font without {name}')
        for table in font['cmap'].tables:
            if table.isUnicode(): table.cmap.update(figures)
    play_mark(font)
    have.append(PLAY_MARK)
    sub = subset.Subsetter(subset.Options())
    sub.populate(unicodes=have)
    sub.subset(font)
    check(sorted(font.getBestCmap()) == have, f'{label} font subset lost characters')
    # A subset is a Modified Version: it may not carry the Reserved Font Name (its names; the notices
    # are the licence files FONT_NOTICES ships beside it)
    for rec in font['name'].names:
        if rec.nameID in (1, 3, 4, 6, 16, 17, 18, 21, 22):
            if rec.nameID == 6:  # the PostScript name has no spaces
                rec.string = str(rec).replace(old.replace(' ', ''), family.replace(' ', ''))
            else:
                rec.string = str(rec).replace(old, family).replace(old.replace(' ', ''), family.replace(' ', ''))
            check(old.split()[0] not in str(rec), f'{label} font name {rec.nameID} keeps {old.split()[0]}')
    out = io.BytesIO()
    font.save(out, reorderTables=False)
    runs = []
    for u in have:
        if runs and runs[-1][1] + 1 == u: runs[-1][1] = u
        else: runs.append([u, u])
    return out.getvalue(), f'#define {ranges_macro} {{' + ','.join(f'0x{u:x}' for r in runs for u in r) + '}'

FONT_DIR = 'release/assets/default/raw/fonts/'

@dataclasses.dataclass(frozen=True)
class Face:
    """A face a theme names for a font role (theme.xml's font values: FACES' keys). Its C names come
    from name: FONT_<NAME> its index (stock.h, in FACES' order), <NAME>_RANGES what it keeps."""
    name: str  # AWTK's font_name, and its file: FONT_DIR + name + '.ttf'
    source: str = ''  # its original in assets/fonts, cut by subset_font; none: stock's own default.ttf, uncut
    family: str = ''  # the subset's name
    notices: tuple = ()  # assets/fonts' licence files, shipped beside it (FONT_NOTICES)
    fallback: str = ''  # the face (FACES key) a text takes when this one lacks one of its characters
    # the fallback's px, in % of this face's: the two faces' cap heights' ratio (stb sizes a font by its
    # ascent - descent; stock's default.ttf has a cap height of 183 of 299), so a stand-in's capitals
    # match (test_ipod test_fallback_scale_matches_the_faces)
    fallback_pct: int = 100
    tabular: str = ''  # its tabular figures' glyph name suffix, if 0-9 and the colon take them (subset_font)

    @property
    def c(self): return 'FONT_' + self.name.upper().replace('-', '_')

    @property
    def ranges_macro(self): return self.name.upper().replace('-', '_') + '_RANGES'

    @property
    def path(self): return FONT_DIR + self.name + '.ttf'

# Each face but stock's is cut by subset_font to its Latin, Greek, Cyrillic and punctuation, with a play
# mark (play_mark); its <NAME>_RANGES, from the same cmap, lists exactly what it keeps, so a label
# holding anything else (CJK, say) takes the fallback (navigation.c theme_font), down to stock's, which
# has CJK.
FACES = {
    'regular': Face('default'),
    # Inter 4.1 (OFL), Regular and Medium, with tabular figures: OLED's
    'inter': Face('inter', 'Inter-Regular.ttf', 'Q2 Muse OLED', ('Inter-OFL.txt',), fallback='regular', fallback_pct=98, tabular='.tf'),
    'inter-medium': Face('inter-medium', 'Inter-Medium.ttf', 'Q2 Muse OLED', ('Inter-OFL.txt',), fallback='regular', fallback_pct=98,
                         tabular='.tf'),
}
# OFL 2 asks each copy to hold its licence, and the subsets' name tables don't: these ship beside the fonts
FONT_NOTICES = tuple(dict.fromkeys(n for f in FACES.values() for n in f.notices))

def face_font(face):
    """The face's file and its ranges macro (subset_font)."""
    return subset_font(ROOT/'assets/fonts'/face.source, face.family, face.ranges_macro, face.tabular)

def font_indices():
    """FONT_<NAME>, each face's index in FACES' order, and FONT_N: theme_t.font[]'s values."""
    return [*(f'#define {f.c} {i}' for i, f in enumerate(FACES.values())), f'#define FONT_N {len(FACES)}']

def faces_header(ranges):
    """stock.h's faces, ranges the subset faces' range macros in FACES' order: font_indices, then
    FONT_NAMES, FONT_FALLBACK and FONT_FALLBACK_PCT by index, FONT_RANGES every subset face's runs one
    after another, a face's from FONT_RANGE_AT[i] to [i + 1] (none: stock's, every character)."""
    faces = list(FACES.values())
    cut = [f for f in faces if f.source]
    check(len(ranges) == len(cut), 'One ranges macro per subset face')
    runs = dict(zip((f.name for f in cut), (r.split(' ', 2)[2][1:-1].split(',') for r in ranges)))
    at = [0]
    for f in faces: at.append(at[-1] + len(runs.get(f.name, ())))
    return [*font_indices(), *ranges,
            '#define FONT_NAMES {' + ','.join(json.dumps(f.name) for f in faces) + '}',
            '#define FONT_FALLBACK {' + ','.join(FACES[f.fallback].c if f.fallback else '0' for f in faces) + '}',
            '#define FONT_FALLBACK_PCT {' + ','.join(str(f.fallback_pct) for f in faces) + '}',
            '#define FONT_RANGES {' + ','.join(v for f in cut for v in runs[f.name]) + '}',
            '#define FONT_RANGE_AT {' + ','.join(map(str, at)) + '}']

def relabel(data):
    """A strings/*.bin value replaced: after a u16 0 and a u16 pair count, a u32 offset to each key and
    value, in file order; the offsets past each change move with it. A key stock lists more than once
    (scan_now, scan_spec: each pair twice) has every pair replaced, each holding the old value."""
    for key, old, new in LABELS:
        pair = b'\0' + key + b'\0' + old + b'\0'  # back to back, two such pairs share a NUL
        pairs = sum(data.startswith(pair, i) for i in range(len(data)) if data[i] == 0)
        check(pairs > 0 and data.count(b'\0' + key + b'\0') == pairs, f'Unexpected {key.decode()} label')
        for _ in range(pairs):
            at = data.find(b'\0' + key + b'\0' + old + b'\0') + len(key) + 2
            n = 2 * struct.unpack_from('<H', data, 2)[0]
            offsets = [o + len(new) - len(old) if o > at else o for o in struct.unpack_from(f'<{n}I', data, 4)]
            data = bytearray(data[:at] + new + data[at + len(old):])
            struct.pack_into(f'<{n}I', data, 4, *offsets)
    return bytes(data)

# demo's .pdr section (offset, size): MIPS procedure descriptors past every LOAD segment, which nothing
# reads at run time. Zeroed, they pack to almost nothing, which keeps the rootfs within stock's size.
PDR = (0x62a830, 0x6b180)

def run(*args):
    return subprocess.check_output([str(a) for a in args], text=True)
def sha(b): return hashlib.sha256(b).hexdigest()

def source_sha256():
    """Hash every build input, so a test run cannot silently use a stale output directory."""
    h = hashlib.sha256()
    manual = [ROOT/'docs/manual'/f for f in ('build.py', 'guide.html')]
    for path in sorted([*(p for p in ROOT.glob('assets/**/*') if p.is_file()), *(p for p in ROOT.glob('patch/**/*') if p.is_file()),
                        *ROOT.glob('tools/*.py'), *ROOT.glob('tools/*.c'),
                        *ROOT.glob('tools/*.h'), *ROOT.glob('tools/*.S'), *ROOT.glob('player/*.[ch]'),
                        *ROOT.glob('player/Makefile'), *manual]):
        h.update(str(path.relative_to(ROOT)).encode() + b'\0')
        h.update(path.read_bytes())
    return h.hexdigest()

def dev_build():
    """The next --dev build number, past the untracked .build-number's and the one in the git directory
    every worktree of this checkout shares, so a second worktree's first build is not the first's V001i.
    Each file is replaced whole; an empty or unreadable one (a power loss mid-write) counts as 0."""
    counters = [ROOT/'.build-number']
    if (ROOT/'.git').exists():  # not an enclosing repo's, for a source archive unpacked inside one
      try: counters.append(ROOT/run('git', '-C', ROOT, 'rev-parse', '--git-common-dir').strip()/'build-number')
      except (OSError, subprocess.CalledProcessError): pass
    def read(counter):
        try: return int(counter.read_text())
        except (OSError, ValueError): return 0
    import os
    if os.environ.get('Q2_DEV_NUMBER'):  # pragma: no mutate block
        # tools/ci/q2live.py: one number for every rebuild of a live session, so stock.h, which holds it,
        # stays the same and ccache recompiles only the files that changed; the counters are left alone
        return int(os.environ['Q2_DEV_NUMBER'])
    number = max(map(read, counters)) + 1
    for counter in counters:
        temp = counter.with_name(counter.name + '.tmp'); temp.write_text(f'{number}\n'); temp.replace(counter)
    return number

def j(addr): return 0x08000000 | addr >> 2  # MIPS j
def jal(addr): return 0x0c000000 | addr >> 2  # MIPS jal
def check(condition, message):
    if not condition: raise ValueError(message)
_T = TypeVar('_T')
def need(value: _T | None, message: str) -> _T:
    """value, or check's ValueError(message) where a search or lookup came back None."""
    if value is None: raise ValueError(message)
    return value
def jpeg_size(b):
    """Validate the splash's supported JPEG frame header and return its dimensions."""
    check(b[:2] == b'\xff\xd8', 'Logo must be a JPEG')
    i = 2
    while i < len(b):
        check(b[i] == 0xFF, 'Malformed JPEG')
        while i < len(b) and b[i] == 0xFF: i += 1
        check(i < len(b), 'Truncated JPEG marker')
        marker = b[i]
        i += 1
        if marker in (0xD9, 0xDA): break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7: continue
        check(marker not in (0, 0xD8), 'Malformed JPEG marker')
        check(i + 2 <= len(b), 'Truncated JPEG segment')
        size = struct.unpack_from('>H', b, i)[0]
        check(size >= 2 and i + size <= len(b), 'Invalid JPEG segment length')
        if marker in (0xC0,0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB,0xCD,0xCE,0xCF):
            check(size >= 8 and size == 8 + 3 * b[i+7], 'Invalid JPEG frame length')
            # Stock display_logo indexes decoded pixels as RGB triples (0x400ec0 onward),
            # but leaves libjpeg's output color space unchanged. Grayscale/CMYK are unsafe.
            check(marker == 0xC0 and b[i+2] == 8 and b[i+7] == 3,
                  'Logo must be an 8-bit, three-component baseline JPEG (not grayscale/CMYK)')
            h, w = struct.unpack_from('>HH', b, i+3)
            check(w > 0 and h > 0, 'Invalid JPEG dimensions')
            return w, h
        i += size
    raise ValueError('No JPEG size marker')
def symbols(p, table=None):
    out = {}
    for line in (table or run('readelf', '-Ws', p)).splitlines():
        s = line.split()
        if len(s) >= 8 and s[0].endswith(':'):
            try: out[s[7]] = int(s[1], 16)
            except ValueError: pass
    return out

# The recovery (mtd4 demo) erases mtd2 and nandwrites the file with no size check; mtd2 is 600 blocks of 128 KB,
# keep 24 for bad ones (docs/hardware.md#flash-layout). Stock V1.36's own image is 50,688,000 bytes.
ROOTFS_ROOM = 72 << 20  # 75,497,472 bytes


# The lines of an app's launcher (its card copy's run) that bundled_launcher changes, each found once: the
# folder it keeps its data in, the card's .<id> made if it is missing, and the binary linked into /tmp from
# the read-only image, not copied there (7.9 MB of RAM for q2-subsonic).
LAUNCHER_DIR = 'dir=$(cd "$(dirname "$0")" && pwd)'
LAUNCHER_COPY = 'cp -f "$dir/q2-{id}" /tmp/q2-{id}.bin && chmod 755 /tmp/q2-{id}.bin || exit 1'


def bundled_launcher(app_id, text):
    """An app's own launcher, made to run the firmware's copy of it with its data in the card's .<id>."""
    copy = LAUNCHER_COPY.format(id=app_id)
    check(text.count(LAUNCHER_DIR) == 1 and text.count(copy) == 1, f'Unexpected launcher in the {app_id} app')
    return (text.replace(LAUNCHER_DIR, f'dir=/mnt/mmc/.{app_id}; mkdir -p "$dir" || exit 1')
                .replace(copy, f'ln -sf /{BUNDLED_DIR}/{app_id}/q2-{app_id} /tmp/q2-{app_id}.bin || exit 1'))


def bundled_app(app_id, apps_dir):
    """BUNDLED_APPS[app_id]'s files, name -> (bytes, executable), from its pinned zip in apps_dir."""
    version, name, size, digest = BUNDLED_APPS[app_id]
    path = pathlib.Path(apps_dir)/name
    check(path.is_file(), f'Missing {path}: build {app_id}\'s {version} (docs/building.md#bundled-apps)')
    data = path.read_bytes()
    check(len(data) == size and sha(data) == digest, f'{path} is not the pinned {app_id} {version}')
    prefix = f'.{app_id}/'
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        files = {n[len(prefix):]: z.read(n) for n in z.namelist() if n.startswith(prefix) and not n.endswith('/')}
    need_files = {f'q2-{app_id}', 'run', 'q2app'}
    check(need_files <= set(files) and all('/' not in n for n in files), f'Unexpected files in {name}')
    files['run'] = bundled_launcher(app_id, files['run'].decode()).encode()
    files['version'] = version.encode() + b'\n'
    return {n: (b, n in (f'q2-{app_id}', 'run')) for n, b in sorted(files.items())}


def rootfs_fits(size, new_player=False):
    """Refuse a repacked rootfs of size bytes that leaves mtd2 less than its bad-block spare."""
    check(size <= ROOTFS_ROOM, f'Repacked rootfs is {size:,} bytes, {size - ROOTFS_ROOM:,} over the {ROOTFS_ROOM:,} mtd2 holds'
          + (' (--new-player adds q2player beside hciplayer: make room or build without it)' if new_player else ''))


def segments(b):
    phoff = struct.unpack_from('<I', b, 28)[0]
    size, num = struct.unpack_from('<HH', b, 42)
    check(size == 32, 'Unexpected ELF program header size')
    return [(phoff+i*size, struct.unpack_from('<8I', b, phoff+i*size)) for i in range(num)]
def load_elf(u, b, load=0):
    """Map an ELF's LOAD segments into a Unicorn instance, shifted by load."""
    for _, (t, o, v, _, f, m, _, _) in segments(b):
        if t != 1: continue
        v += load
        start = v & ~4095
        u.mem_map(start, ((v+m+4095) & ~4095) - start)
        u.mem_write(v, b[o:o+f])
def machine(raw, scratch, load=0):
    """A little-endian MIPS Unicorn with raw's LOAD segments (at +load) and 64 KB of scratch at scratch."""
    from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    load_elf(u, raw, load)
    u.mem_map(scratch, 0x10000)
    return u
def word(u, address, value): u.mem_write(address, struct.pack('<I', value & 0xffffffff))
def read(u, address): return struct.unpack('<I', u.mem_read(address, 4))[0]
def fileoff(b, a):
    for _, (t, o, v, _, f, _, _, _) in segments(b):
        if t == 1 and v <= a < v+f: return o+a-v
    raise ValueError(f'Unmapped address {a:x}')

# Every saved g_time_zone index, in stock's order (0-26) then the additions: its label and TZ. Labels
# are literal, short enough for the 246 px row at 24 px; the offset is the standard one. The
# rows show the one in effect now instead, daylight saving included (ringnav_timezone_row). POSIX rules stand in where the rootfs tzdata is out of date: Mexico City
# and Tehran dropped DST in 2022, Fiji suspended it. Stock's GMT-12 row was Kwajalein, which is +12;
# its +6 Almaty is +5 since 2024, so that row is Dhaka.
TIMEZONES = (
    ('GMT-12 Baker Island', '<-12>12'), ('GMT-11 Midway', 'Pacific/Midway'),
    ('GMT-10 Honolulu', 'Pacific/Honolulu'), ('GMT-9 Anchorage', 'America/Anchorage'),
    ('GMT-8 Los Angeles', 'America/Los_Angeles'), ('GMT-7 Denver', 'America/Denver'),
    ('GMT-6 Mexico City', 'CST6'), ('GMT-5 New York', 'America/New_York'),
    ('GMT-4 Halifax', 'America/Halifax'), ("GMT-3:30 St. John's", 'America/St_Johns'),
    ('GMT-3 Buenos Aires', 'America/Argentina/Buenos_Aires'), ('GMT-1 Azores', 'Atlantic/Azores'),
    ('GMT+0 London', 'Europe/London'), ('GMT+1 Amsterdam', 'Europe/Amsterdam'),
    ('GMT+2 Bucharest', 'Europe/Bucharest'), ('GMT+3 Kuwait', 'Asia/Kuwait'),
    ('GMT+3:30 Tehran', '<+0330>-3:30'), ('GMT+4 Muscat', 'Asia/Muscat'),
    ('GMT+4:30 Kabul', 'Asia/Kabul'), ('GMT+5 Karachi', 'Asia/Karachi'),
    ('GMT+6 Dhaka', 'Asia/Dhaka'), ('GMT+7 Bangkok', 'Asia/Bangkok'),
    ('GMT+8 Shanghai', 'Asia/Shanghai'), ('GMT+9 Tokyo', 'Asia/Tokyo'),
    ('GMT+9:30 Adelaide', 'Australia/Adelaide'), ('GMT+10 Sydney', 'Australia/Sydney'),
    ('GMT+12 Fiji', '<+12>-12'),
    ('GMT-9:30 Marquesas', 'Pacific/Marquesas'), ('GMT-2 South Georgia', 'Atlantic/South_Georgia'),
    ('GMT+5:30 Colombo', 'Asia/Colombo'), ('GMT+5:45 Kathmandu', 'Asia/Kathmandu'),
    ('GMT+6:30 Yangon', 'Asia/Yangon'), ('GMT+8:45 Eucla', 'Australia/Eucla'),
    ('GMT+10:30 L. Howe', 'Australia/Lord_Howe'), ('GMT+11 Noumea', 'Pacific/Noumea'),
    ('GMT+12:45 Chatham', 'Pacific/Chatham'), ('GMT+13 Tongatapu', 'Pacific/Tongatapu'),
    ('GMT+14 Kiritimati', 'Pacific/Kiritimati'),
    ('GMT-6 Chicago', 'America/Chicago'), ('GMT-7 Phoenix', 'America/Phoenix'),
    ('GMT+5:30 Kolkata', 'Asia/Kolkata'), ('GMT+10 Brisbane', 'Australia/Brisbane'),
    ('GMT+12 Auckland', 'Pacific/Auckland'),
)
LOGO_HOOK = (b'   \tdisplay_logo /release/assets/default/raw/images/xx/logo.jpg\n',
             b'   \tl=/mnt/data/boot-logo.jpg; [ -f $l ] || { l=/%s; [ -f /%s ] && display_logo /%s && /usr/bin/q2splash /%s && l=; }; [ -z "$l" ] || display_logo $l\n'
             % (LOGO.encode(), LOGO_ANIM.encode(), LOGO_BLACK.encode(), LOGO_ANIM.encode()))
def timezone_offset(label):
    sign, h, m = need(re.match(r'GMT([+-])(\d+)(?::(\d+))?', label), f'Unexpected time zone {label}').groups()
    return (1 if sign == '+' else -1) * (int(h) * 60 + int(m or 0))
# The rows, west to east: the saved index each row shows.
TIMEZONE_ORDER = sorted(range(len(TIMEZONES)), key=lambda i: (timezone_offset(TIMEZONES[i][0]), TIMEZONES[i][0]))

def patch_timezones(demo):
    # Keep the stock indices so saved zones survive; fit the extra rows in the existing table.
    start = fileoff(demo, 0x77efb0)
    original = demo[start:start+28*64]
    labels = [original[i:i+64].split(b'\0')[0] for i in range(0, len(original), 64)]
    check(labels[0] == b'timedate_auto' and labels[-1] == b'timezone_gmt+12', 'Unexpected timezone labels')
    labels = labels[:1] + [label.encode() for label, _ in TIMEZONES]
    check(len(labels)*32 <= len(original) and all(len(label) < 32 for label in labels), 'Timezone labels exceed stock table')
    demo[start:start+len(original)] = b''.join(label.ljust(32, b'\0') for label in labels).ljust(len(original), b'\0')
    for address, old, new in (
        (0x4c1bd8, 0x00102980, 0x00102940),  # label stride: 64 -> 32
        (0x4c1ed8, 0x2402001c, 0x24020000 | len(labels)),
        (0x4c23f4, 0x2402001c, 0x24020000 | (len(labels)-1)),
        (0x504eec, 0x2c43001b, 0x2c430000 | (len(labels)-1)),  # boot accepts saved additions
    ):
        off = fileoff(demo, address)
        check(struct.unpack_from('<I', demo, off)[0] == old, f'Unexpected timezone code at {address:x}')
        struct.pack_into('<I', demo, off, new)

FUNCTIONS = {
 '_create_vector': ('void *', 'const char *'),
 'vector_init': ('void', 'void *'),
 '_vector_push_back': ('void', 'void *, ...'),
 'sqlite3_open': ('int', 'const char *, void **'),
 'sqlite3_close': ('int', 'void *'),
 'sqlite3_exec': ('int', 'void *, const char *, int (*)(void *, int, char **, char **), void *, char **'),
 'getMusicByPlayList': ('int', 'int'),
 'sort_playlist_addtime': ('int', 'int'),
 'getAllPlayList': ('int', 'void'),  # every playlist's header, into tools_pdeq_directory (coverflow.c's playlists)
 'checkPlayListExist': ('int', 'void *, int'),
 'toolsSetTimeZone': ('int', 'const char *'),
 'widget_on': ('unsigned', 'void *, unsigned, int (*)(void *, void *), void *'),
 'widget_set_visible': ('int', 'void *, int, int'),
 'widget_set_opacity': ('int', 'void *, unsigned'),
 'widget_set_enable': ('int', 'void *, int'),
 # Crossfade's page (crossfade.c): stock's Boot volume slider, its value a double
 'slider_set_min': ('int', 'void *, double'), 'slider_set_max': ('int', 'void *, double'),
 'slider_set_step': ('int', 'void *, double'), 'slider_set_value': ('int', 'void *, double'),
 'widget_set_text_utf8': ('int', 'void *, const char *'),
 'text_selector_count_options': ('unsigned', 'void *'),
 'text_selector_set_selected_index': ('int', 'void *, unsigned'),
 'widget_use_style': ('int', 'void *, const char *'),
 'widget_set_name': ('int', 'void *, const char *'),
 'widget_set_sensitive': ('int', 'void *, int'),
 'widget_to_local': ('int', 'void *, void *'),
 'widget_dispatch_event_to_target_recursive': ('int', 'void *, void *'),
 'label_create': ('void *', 'void *, int, int, int, int'),
 'stock_search': ('int', 'void *, void *'),
 'widget_set_children_layout': ('int', 'void *, const char *'),
 'widget_get_canvas': ('void *', 'void *'),  # the lyrics page measures its lines (lyrics.c)
 'widget_resize': ('int', 'void *, int, int'),
 'widget_layout_children': ('int', 'void *'),
 'widget_lookup': ('void *', 'void *, const char *, int'),
 'scroll_bar_scroll_to': ('int', 'void *, int, unsigned'),
 'navigator_back_to_home': ('int', 'void'),
 'navigator_switch_to_with_context': ('int', 'const char *, const void *, int'),
 'window_manager': ('void *', 'void'),
 'window_manager_get_top_window': ('void *', 'void *'),
 'window_manager_is_animating': ('int', 'void *'),
 'window_manager_get_pointer_pressed': ('int', 'void *'),
 'main_loop': ('void *', 'void'),  # +LOOP_PRESSED: the touch thread's own down flag (navigation.c stale_note)
 'widget_get_visible': ('int', 'void *'),
 'widget_get_prop_bool': ('int', 'void *, const char *, int'),
 'widget_get_prop_int': ('int', 'void *, const char *, int'),
 'style_get_int': ('int', 'void *, const char *, int'),
 'widget_get_prop_str': ('const char *', 'void *, const char *, const char *'),
 'widget_get_text': ('const unsigned *', 'void *'),
 'widget_get_text_utf8': ('int', 'void *, char *, unsigned'),
 'widget_get_type': ('const char *', 'void *'),
 'widget_count_children': ('unsigned', 'void *'),
 'widget_get_child': ('void *', 'void *, unsigned'),
 'widget_set_prop_int': ('int', 'void *, const char *, int'),
 'widget_set_prop_str': ('int', 'void *, const char *, const char *'),
 'pages_set_active_by_name': ('int', 'void *, const char *'),
 'edit_set_select': ('int', 'void *, unsigned, unsigned'),
 'widget_invalidate_force': ('int', 'void *, void *'),
 'widget_animator_start': ('int', 'void *'),
 'widget_animator_prop_create': ('void *', 'void *, unsigned, unsigned, int, const char *'),  # Home's Settings slide
 'widget_animator_prop_set_params': ('int', 'void *, double, double'),
 'widget_animator_scroll_set_params': ('int', 'void *, int, int, int, int'),
 'slide_menu_set_value': ('int', 'void *, int'),
 'slide_menu_item_width': ('int', 'void *'),
 'slide_menu_on_scroll_done': ('int', 'void *, void *'),
 'widget_animator_scroll_create': ('void *', 'void *, unsigned, unsigned, int'),
 'widget_animator_on': ('unsigned', 'void *, unsigned, int (*)(void *, void *), void *'),
 'widget_set_focused': ('int', 'void *, int'),
 'input_method': ('void *', 'void'),
 'input_method_request': ('int', 'void *, void *'),
 'input_method_dispatch_action': ('int', 'void *'),  # a text box's OK, as its keyboard's Enter (search_strip.c)
 'widget_animator_pause': ('int', 'void *'),
 'widget_animator_destroy': ('int', 'void *'),
 'easing_get': ('void *', 'int'),  # Coverflow's glides set a reused animator's easing
 'canvas_get_clip_rect': ('int', 'void *, void *'),
 'canvas_set_clip_rect': ('int', 'void *, const void *'),
 'canvas_set_fill_color': ('int', 'void *, unsigned'),
 'canvas_set_stroke_color': ('int', 'void *, unsigned'),
 'canvas_stroke_rect': ('int', 'void *, int, int, int, int'),
 'canvas_fill_rect': ('int', 'void *, int, int, int, int'),
 'canvas_draw_icon': ('int', 'void *, void *, int, int'),
 'canvas_set_font': ('int', 'void *, const char *, unsigned'),
 'canvas_set_text_color': ('int', 'void *, unsigned'),
 'canvas_draw_text_in_rect': ('int', 'void *, const unsigned *, unsigned, const void *'),
 'widget_draw_text_in_rect': ('int', 'void *, void *, const unsigned *, unsigned, const void *, int'),  # ringnav_ellipsis_text
 'canvas_draw_text': ('int', 'void *, const unsigned *, unsigned, int, int'),  # one line: lyrics.c's words, the status row's percentage
 'canvas_measure_text': ('float', 'void *, const unsigned *, unsigned'),
 'canvas_fill_rounded_rect': ('int', 'void *, const void *, const void *, const void *, unsigned'),
 'canvas_stroke_rounded_rect': ('int', 'void *, const void *, const void *, const void *, unsigned, unsigned'),
 # The PEQ curve (peq_ui.c) among others: AWTK's software nanovg, float_t float, color_t by value
 'canvas_get_vgcanvas': ('void *', 'void *'),
 'vgcanvas_save': ('int', 'void *'), 'vgcanvas_restore': ('int', 'void *'),
 'vgcanvas_translate': ('int', 'void *, float, float'),
 'vgcanvas_begin_path': ('int', 'void *'), 'vgcanvas_close_path': ('int', 'void *'),
 'vgcanvas_move_to': ('int', 'void *, float, float'), 'vgcanvas_line_to': ('int', 'void *, float, float'),
 'vgcanvas_arc': ('int', 'void *, float, float, float, float, float, int'),
 'vgcanvas_fill': ('int', 'void *'), 'vgcanvas_stroke': ('int', 'void *'),
 'vgcanvas_set_fill_color': ('int', 'void *, unsigned'), 'vgcanvas_set_stroke_color': ('int', 'void *, unsigned'),
 'vgcanvas_set_fill_linear_gradient': ('int', 'void *, float, float, float, float, unsigned, unsigned'),
 'vgcanvas_set_line_width': ('int', 'void *, float'), 'vgcanvas_set_line_cap': ('int', 'void *, const char *'),
 'pointer_event_init': ('void *', 'void *, int, void *, int, int'),
 'time_now_ms': ('unsigned', 'void'),
 'sleep_ms': ('int', 'unsigned'),
 'timer_add': ('unsigned', 'int (*)(const void *), void *, unsigned'),
 'idle_add': ('unsigned', 'int (*)(const void *), void *'),
 'timer_remove': ('int', 'unsigned'),
 'tk_strcmp': ('int', 'const char *, const char *'),
 'slide_menu_scroll_to_next': ('int', 'void *'),
 'slide_menu_scroll_to_prev': ('int', 'void *'),
 'table_client_stop_animator_scroll': ('int', 'void *'),
 'table_client_set_yoffset': ('int', 'void *, int'),
 'table_row_of': ('void *', 'void *'),  # the table row holding a widget, or 0
 'widget_dispatch_simple_event': ('int', 'void *, unsigned'),
 'scroll_view_set_offset': ('int', 'void *, int, int'),
 'scroll_view_scroll_to': ('int', 'void *, int, int, int'),
 'table_client_scroll_to': ('int', 'void *, int'),
 'table_client_get_virtual_h': ('int', 'void *'),
 'scroll_view_scroll_delta_to': ('int', 'void *, int, int, int'),
 'widget_destroy_children': ('int', 'void *'),
 'widget_destroy': ('int', 'void *'),  # Streaming's Baidu Netdisk row (home.c ringnav_stream)
 'list_item_create': ('void *', 'void *, int, int, int, int'),
 'hscroll_label_create': ('void *', 'void *, int, int, int, int'),
 'set_hscroll_label_attribute': ('void', 'void *'),
 'hscroll_label_set_only_focus': ('int', 'void *, int'),
 'hscroll_label_set_only_parent_focus': ('int', 'void *, int'),
 'hscroll_label_set_ellipses': ('int', 'void *, int'),
 'hscroll_label_set_xoffset': ('int', 'void *, int'),
 'hscroll_label_set_loop_interval_distance': ('int', 'void *, int'),  # Now Playing's lines' copy (np_marquee.c)
 'hscroll_label_start': ('int', 'void *'),
 'hscroll_label_stop': ('int', 'void *'),
 'widget_add_timer': ('unsigned', 'void *, int (*)(const void *), unsigned'),
 'widget_set_text': ('int', 'void *, const unsigned *'),
 'widget_off_by_func': ('int', 'void *, unsigned, void *, void *'),
 'widget_off_by_ctx': ('int', 'void *, void *'),  # Home's Library list (home.c library_rows)
 'on_home_music_onclick': ('int', 'void *, void *'),  # stock's Library card, which Home's Full keeps (coverflow.c home_library)
 'on_home_stream_onclick': ('int', 'void *, void *'),  # stock's Streaming card, the same (coverflow.c home_streaming)
 'window_close': ('int', 'void *'),
 'dialog_quit': ('int', 'void *, unsigned'),  # quick settings' close, as its swipe up ends (0x4a1fd8)
 'window_manager_close_window_force': ('int', 'void *, void *'),
 'navigator_to': ('int', 'const char *'),
 'navigator_to_with_context': ('int', 'const char *, const void *'),
 'window_manager_get_input_device_status': ('char *', 'void *'),
 'airplayGetFlag': ('int', 'void'),
 'tk_snprintf': ('int', 'char *, unsigned, const char *, ...'),
 'toolsTimeItoa': ('int', 'char *, int'),
 'getMusicByAlbum': ('int', 'const char *'),
 'getMusicByAlbumAndSonger': ('int', 'const char *, const char *, int'),
 'getMusicByAlbumAndAlbumSonger': ('int', 'const char *, const char *, int'),
 'toolsLoadDirectory': ('int', 'const char *'),
 'toolsCheckTypeDeque': ('int', 'void *, int'),
 'get_specfolderurl': ('void', 'int, char *, int'),
 # Queue menu rows (navigation.c): artist/composer/genre queries, My Fav, the batch selection record
 'getMusicBySonger': ('int', 'const char *'),
 'getMusicByAlbumArtist': ('int', 'const char *'),
 'getMusicByComposer': ('int', 'const char *'),
 'getMusicByGenre': ('int', 'const char *'),
 'getMusicByAlbumAndComposer': ('int', 'const char *, const char *, int'),
 'getMusicByAlbumAndGenre': ('int', 'const char *, const char *, int'),
 'checkFavExist': ('int', 'void *'),
 'getAllFav': ('int', 'void'),
 # Long Songs (library_lists.c): a file's length, probed once, as stock's scanner reads tags
 'taglib_file_new': ('void *', 'const char *, int'),
 'taglib_file_tag': ('void *', 'void *'),
 'toolsCheckItemExist': ('int', 'void *, const char *, int'),
 'toolsGetMusicInfo': ('int', 'int, char *, const char *'),
 'toolsFixId3ErrorCode': ('void', 'int, const char *, char *, char *, int'),  # tags.c ogg_music_info
 'toolsUpdateDbTable': ('int', 'const char *, const char *'),
 'toolsTrimRight': ('void', 'char *'),
 'toolsHanziToPinyin': ('int', 'const char *, char *'),
 'toolsGetUserStop': ('int', 'void'),  # toolsStopUpdateMusic's flag: the scan dialog's Back
 'taglib_file_audioproperties': ('void *', 'void *'),
 'taglib_file_free': ('void', 'void *'),
 'deleteMusicFromFav': ('int', 'void *'),
 'batch_init_selectrecord': ('int', 'int'),
 'batch_set_selectitem': ('int', 'int'),
 'batch_get_selectitem': ('int', 'int'),
 'batch_add_file': ('int', 'int, int, void *, void *, int'),
 'navigator_window_is_exist': ('int', 'const char *'),
 'mclStartPlayer': ('int', 'void'),
 'player_start_queue': ('int', 'int'),
 'mclStop': ('int', 'void'),
 'mclGetPlayTime': ('int', 'int *, int *'),
 'mclSetStartSeekTime': ('int', 'int'),
 'mcl_open_preload': ('int', 'void'),
 'mclLoadPlayList': ('int', 'void *, int, int'),
 'mcl_shuffle_pick': ('int', 'int'),
 'getAllAlbum': ('int', 'void'),
 # Shuffle Songs (library_lists.c): every song, shuffle saved as the play-mode setting does, a random start
 'getAllMusic': ('int', 'int'),
 'music_nagvation_title': ('int', 'int'),  # Library -> Songs' load (navigation.c songs_load)
 'mclGetPlayM3uFlag': ('int', 'void'),  # stock ignores Folder Skip on an M3U (playback.c change_restarts)
 'config_playmode': ('int', 'int, int'),
 'toolsRandnum': ('int', 'int'),
 'widget_restack': ('int', 'void *, unsigned'),
 'widget_index_of': ('int', 'void *'),  # Now Playing's lyrics column back in its place (navigation.c np_full)
 # ⋯'s About moves Now Playing's details into its page and back (np_about.c)
 'widget_remove_child': ('int', 'void *, void *'),
 'widget_add_child': ('int', 'void *, void *'),
 'toolsThumbSpecCover': ('int', 'const char *, const char *, int, int'),
 'toolsGetAlbumCover': ('int', 'const char *, const char *, int, int'),
 'toolsGetExternCover': ('int', 'const char *, const char *, int, int'),
 'window_create': ('void *', 'void *, int, int, int, int'),
 'widget_factory': ('void *', 'void'),
 'widget_factory_create_widget': ('void *', 'void *, const char *, void *, int, int, int, int'),
 'image_create': ('void *', 'void *, int, int, int, int'),
 'image_set_draw_type': ('int', 'void *, int'),
 'image_base_set_image': ('int', 'void *, const char *'),
 'widget_load_image': ('int', 'void *, const char *, void *'),
 'widget_unload_image': ('int', 'void *, void *'),
 'list_view_create': ('void *', 'void *, int, int, int, int'),
 'scroll_view_create': ('void *', 'void *, int, int, int, int'),
 'navigator_back': ('int', 'void'),
 'write_int_config': ('int', 'int, const char *, const char *'),
 'toolsWriteConfig': ('int', 'const char *, const char *, const char *, const char *'),
 'toolsReadConfig': ('int', 'const char *, const char *, const char *, char *, const char *'),
 'button_create': ('void *', 'void *, int, int, int, int'),
 'widget_move_resize': ('int', 'void *, int, int, int, int'),
 # Swipe back (swipe_back.c): a page or Home's list moved with the finger, its children's layout kept
 'widget_move': ('int', 'void *, int, int'),
 # The artist page's toolbar (navigation.c artist_bar): its tabs move down under it
 'widget_set_self_layout_params': ('int', 'void *, const char *, const char *, const char *, const char *'),
 'widget_layout': ('int', 'void *'),
 'tk_str_end_with': ('int', 'const char *, const char *'),
 'tk_str_start_with': ('int', 'const char *, const char *'),
 'image_manager': ('void *', 'void'),
 'image_manager_unload_all': ('int', 'void *'),
 'image_manager_unload_bitmap_by_name': ('int', 'void *, const char *'),
 'bitmap_get_line_length': ('unsigned', 'void *'),
 'bitmap_lock_buffer_for_write': ('unsigned char *', 'void *'),
 'bitmap_unlock_buffer': ('int', 'void *'),
 # Coverflow depth (coverflow.c): its frame bitmap, reading decoded covers, and the slide_menu stride
 'bitmap_create_ex': ('void *', 'unsigned, unsigned, unsigned, unsigned'),
 'bitmap_destroy': ('int', 'void *'),
 'bitmap_lock_buffer_for_read': ('const unsigned char *', 'void *'),
 'canvas_draw_image': ('int', 'void *, void *, const void *, const void *'),
 'canvas_set_global_alpha': ('int', 'void *, unsigned'),
 'htranslate_update': ('int', 'void *'), 'htranslate_draw_prev': ('int', 'void *'),
 'widget_paint': ('int', 'void *, void *'),  # a widget painted into another's canvas (coverflow.c coverflow_home_strip)
 # Cover Flow's flip paints the tracks panel to a bitmap as stock's window snapshots do (coverflow.c flip_capture)
 'canvas_offline_create': ('void *', 'unsigned, unsigned, unsigned'),
 'canvas_offline_begin_draw': ('int', 'void *'),
 'canvas_offline_end_draw': ('int', 'void *'),
 'canvas_offline_destroy': ('int', 'void *'),
 'canvas_translate': ('int', 'void *, int, int'),
 'widget_vtable_on_paint_self': ('int', 'void *, void *'),  # native foreground for fallback weight
 'slide_menu_set_spacer': ('int', 'void *, int'),
 'playing_timer_start': ('int', 'void *'),
 'playing_timer_clear': ('int', 'void *'),
 'player_seek_time': ('int', 'int'),
 'player_playtime': ('int', 'void'),
 'prev_quick_click': ('int', 'void'), 'next_quick_click': ('int', 'void'),
 'player_mainscheduling': ('int', 'void'),
 'player_playtime_and_length': ('int', 'int *, int *'),  # elapsed, total: track seconds; -1 none
 'player_start': ('int', 'void *, int, int, int'),
 'buzzeer_switch': ('int', 'int'),  # the stock key click; it reads g_keytone_flag
 # Headphones required (g_block_withoutphone_flag) with no output: toasts (0) and returns 1, as before each stock start
 'check_forbid_play_no_headset': ('int', 'int'),
 'on_wm_keyup_fun': ('int', 'void *, void *'),  # stock key-up: np_single replays a centre release
 'get_wifisignal': ('int', 'void'),  # the status bar's Wi-Fi bars, 1-4; -1 when not connected
 'fwupdate_tf': ('int', 'void'),  # the card's update alone (sw_update.c ringnav_fwupdate_tf)
 # its stock checks: -15 battery under 16% with no cable, -2 no card, -11 no file, -12 under 0.1 GB free, else 1
 'check_updatefw_exist': ('int', 'const char *, const char *'),
 'widget_set_tr_text': ('int', 'void *, const char *'),
 # Podcasts and Audiobooks (navigation.c): folder_page's reload of g_folder_path, title and list rebuild
 'folder_reload_data': ('int', 'void'),
 'folder_reset_variable': ('int', 'void'),  # a fresh visit: layer 0, no path, enter history or CUE
 'folder_reinit_navbarname': ('int', 'void'),
 'folder_refresh': ('int', 'void *'),
 # Videos (videos.c): stop the music, the DAC's power, and the screen and standby timeouts held off
 'mclGetOutputWay': ('int', 'void'),
 'mclGetLyricSize': ('int', 'void'),  # the playing track's lyric lines, 0 without
 'player_play_pause': ('int', 'void'),
 'player_next_music': ('int', 'void'), 'player_prev_music': ('int', 'void'),
 'getBtLinkSize': ('int', 'void'), 'getBtLinkItem': ('int', 'int, int *, char *'),
 'getBtLinkStatus': ('int', 'char *'), 'addBtLinkItem': ('void', 'char *, int'),
 'updateBtLinkStatus': ('int', 'int, char *'), 'checkPairDev': ('int', 'char *'),
 'btStartSearch': ('int', 'void'),  # Settings > Bluetooth's scan (bt_ui.c), as stock's Scan now row started it
 'btmenu_item_on_click': ('int', 'void *, void *'),  # a device row's click: connect, disconnect or Unpair? (bt_ui.c)
 # a device's page's Forget (bt_detail.c), as stock's row click does it after its Unpair? dialog's OK: the pairing
 # removed (1, or 2 when it was the output), the output back to a jack, the list read again
 # Guest Play's join QR codes (guest_ui.c): libqrencode, which demo imports; a QRcode is its version, width and
 # width x width modules, bit 0 dark
 'QRcode_encodeString': ('void *', 'const char *, int, int, int, int'), 'QRcode_free': ('void', 'void *'),
 'handleBluetoothPair': ('int', 'int'), 'check_outputdevice': ('int', 'int, int'), 'getSearchResult': ('int', 'void'),
 'btctl_create_daemon_thread': ('int', 'void *'),
 'initBluetoothParam': ('void', 'void'),
 'btctl_set_prompt_callback': ('void', 'void *, void *'),
 'btctl_device_status_callback': ('void', 'void *, void *'),
 'btctl_set_a2dp_callback': ('void', 'void *, void *'),
 'btrecvA2dpFunc': ('int', 'int, void *, void *'),
 'sendBtHeadsetPlayInfo': ('int', 'void'),  # the playing song to bluetoothd's AVRCP record (docs/internals.md#track-metadata)
 'sendBtHeadsetPlayStatus': ('int', 'void'),  # its play status, likewise
 'player_save_stop': ('int', 'void'),
 'airplaySendStop': ('int', 'void'),  # asks shairport, through its shared memory, to end the phone's session  # stops the local or Tidal queue, its position kept for player_save_start
 'mclGetCueFlag': ('int', 'void'),  # the playing record has a CUE start or end
 'mclGetPlayStatus': ('int', 'void'),  # 1 stopped, 2 playing, 3 paused (mclStop, mclSetResume, mclSetPause)
 'player_stop': ('int', 'void'),
 'player_save_start': ('int', 'int'),  # pause: mclStartPlayer at the saved seek, then paused if set
 'mclGetSavePlayStatus': ('int', 'void'),  # player_save_stop's mclGetPlayStatus, unless that was 1
 'save_memoryplay_info': ('int', 'void'),  # Memory playback's queue and position, as into_poweroff saves them
 'mclSetDacPwr': ('int', 'int'),
 'reset_poweroptions_timer': ('int', 'int, int, int'),
 'awake_screen': ('int', 'void'),  # check_usb_status's own at a cable in: the screen lit (usb_ui.c's pages)
 'enable_fb': ('int', 'void'),  # FBIOBLANK unblank: the panel lights (keylock.c's card, as awake_screen)
 'screen_action': ('int', 'int'),  # 1: the backlight and stock's power timers; 0: the panel blanks (disable_fb)
 'btctl_transport_get_volume': ('int', 'void'),
 'btctl_transport_set_volume': ('int', 'int'),
 'device_set_volume': ('int', 'int, int'),
 # Settings' inline sliders (inline_slider.c): stock's setters, each as its page sets a step
 'config_balance': ('int', 'int'),  # -10 (left) to 10: the DAC's ioctl and PLAYSET BALANCE; the caller clamps
 'config_lightness': ('int', 'int'),  # 0 to 100: g_lightness, the backlight and SYSSET LIGHTNESS
 'mclGetBalance': ('int', 'void'),  # volume, notify: the DAC's or hciplayer's, as the volume dialog
 # Spotify (spotify.c): the headphone output as a headset insert sets it, then PCM mode and unmuted, as AirPlay's page
 'config_outputchannel': ('int', 'int, int'),  # g_headset_output, 2
 'mclSetPcmMode': ('int', 'void'),
 'mclSetMute': ('int', 'int'),
 'toolsTrimLeft': ('void', 'char *'),
 'switch_charge_enable': ('int', 'int'),  # 1 charges: the active-low charge enable on GPIO PE22 (usbmode_page_init)
 'toolsGpioSetValue': ('int', 'const char *, int'),
 'toolsGpioGetValue': ('int', 'const char *'),  # USB charge inhibit readback, not a current measurement
 # Coverflow's Sort: another ORDER BY over getAllAlbum's grouping, filled by its own row callback
 'toolsQueryDbTable': ('int', 'const char *, const char *, void *, int'),  # db, sql, row, name sort
 'album_row': ('int', 'void *, int, char **, char **'),
 'albumcoverinfo_init': ('int', 'void *'),  # a list's cover tasks, each a copy of its row's name and path
 'class_icon': ('int', 'void *, int'),  # Artists, Composers and Genres rows' img_icon (CLASS_ICON)
 'np_info': ('int', 'void *'),  # Now Playing's info page labels (ringnav_np_info)
 'ab_x': ('int', 'int'),  # an A-B marker's x for a point (ringnav_ab_x)
 'getFormatString': ('int', 'const char *, char *, int'),  # a string's label in the language: key, out, size
 # Lyrics and cover search (onlinematch.c): demo's cJSON, a node's valuestring at +16
 'cJSON_Parse': ('void *', 'const char *'), 'cJSON_Delete': ('void', 'void *'),
 'cJSON_GetArraySize': ('int', 'void *'), 'cJSON_GetArrayItem': ('void *', 'void *, int'),
 'cJSON_GetObjectItem': ('void *', 'void *, const char *'),
 'online_getkeyword_info': ('void', 'char *'),  # stock's lyric keywords (0x600 bytes), copied under its lock
 # the cover match's candidate URLs (onlinematching_page shows each), under the same lock
 'online_addcoverlist': ('void', 'const char *'), 'online_getsize_coverlist': ('int', 'void'),
 # Tidal's list replies (tidal.c tidal_pages): /tmp/tidal_<thread> parsed into stTidalTracks
 'parseTidalPlaylistDetailJson': ('int', 'const char *'), 'parseTidalAlbumDetailJson': ('int', 'const char *'),
 'parseTidalColTracksJson': ('int', 'const char *'),
 # A card app's library update (addon_library.c): stock's Import Playlists, and its folder listing
 'importPlayList': ('int', 'void'), 'toolsGetDirectoryM3u8': ('int', 'const char *, void *'),
 # Settings' Export Playlists (playlists.c playlists_transfer): every header of the deque (all 1), else
 # the batch selection's; -2 no card, -6 none, else done
 'exportPlayList': ('int', 'int, void *, void *'),
 # Add Songs' lists (add_songs.c), into tools_pdeq_directory as the Library's: Recent, Recently Added, Artists
 # (by artist or album artist, as Playback's Artists says)
 'getAllRecentRecord': ('int', 'void'), 'getAllRecentAddRecord': ('int', 'void'), 'getAllArtist': ('int', 'void'),
 'GetAllAlbumArtist': ('int', 'void'),
 'mclSetSeek': ('int', 'int'),  # the player's seek in file seconds; it plays on from there, or on Play once paused
 # Smart Wi-Fi (smart_wifi.c smart_poll): stock's Wi-Fi switch, a toggle of g_wififlag (its callers pass 1,
 # the background connect), the link and the queue's type
 'wifi_switch': ('int', 'int'),
 'networkGetConnectStatus': ('int', 'void'),  # 0 no link (networkStop), above 0 connected
 'mclGetPlayType': ('int', 'void'),  # the queue's type: 2, 3, 0xf1 and 0xf2 stream (on_wm_timer_networkstatus)
}
# Local stock routines in the SHA-256-pinned V1.36 executable.
PRIVATE_FUNCTIONS = {
    # stock's htranslate window animator's own (its vtable, HTRANSLATE_VT): page_parallax.c runs each after its change
    'htranslate_update': 0x696bd0, 'htranslate_draw_prev': 0x696d90,
    "playlist_delete_click": 0x4b62c8,
    'volume_dialog_on_keyup': HOOKS['volume_dialog_on_keyup'][0],
    'keylock_rows': HOOKS['keylock_rows'][0],
    'timezone_row': HOOKS['timezone_row'][0],
    'quick_lock': HOOKS['quick_lock'][0],
    "playlist_rebuild": HOOKS['playlist_rebuild'][0],  # picker init and re-entry rebuild all rows
    "stock_search": 0x5350d4,
    "slide_menu_item_width": 0x605ed0,
    "slide_menu_on_scroll_done": 0x6064e4,
    "mcl_preload": HOOKS['mcl_preload'][0],
    "mcl_open_preload": HOOKS['mcl_open_preload'][0],
    "btmenu_rows": HOOKS['btmenu_rows'][0],  # systemset_bluetooth_page's list: rows, paired, found
    "btmenu_item_on_click": 0x4c43a4,  # a device row's click, by its button's name (bt_ui.c bt_stock_click)
    "mcl_shuffle_pick": 0x5ba7d0,
    "album_row": 0x506e94,  # getAllAlbum's sqlite3_exec callback: id, album, songer, fileurl to a record
    "folder_refresh": 0x531efc,  # folder_page's navbar and table from p_deque_showlist (its init, back)
    "class_icon": 0x4b2268,  # a category row's picture: the head for Artists, a genre icon, else .downpic
    "folder_click": HOOKS['folder_click'][0],  # folder_page's row click (folder_enter, then a file's queue)
    "np_info": 0x53bc78,  # Now Playing's info page labels, from the track (NP_INFO_CALL)
    "ab_x": 0x53b9d8,  # an A-B marker's x, on the boot set's np_bar_x and np_bar_w (AB_X_CALLS)
    "fwupdate_tf": HOOKS['fwupdate_tf'][0],  # systemset_fwupdate_page's row 0 click (0x4c84cc): sw_update.c's card update
    # Smart Wi-Fi's held stock actions (smart_wifi.c smart_hold)
    "stream_row": HOOKS['stream_row'][0],  # Streaming's Tidal row: atoi of the button's name
    "netservice_row": HOOKS['netservice_row'][0],  # Network service's DLNA, AirPlay and Wi-Fi transfer
    "playermore_online": HOOKS['playermore_online'][0],  # More's Download Cover & Lyrics: (window)
    "specfolder_click": HOOKS['specfolder_click'][0],  # Scan Folders' row click
}
GLOBALS = ['tidal_albuminfo_thread_run', 'tidal_collection_thread_run',  # 2: a Tidal list loaded
           'savestartbt', 'transposition', 'tidalfurlstart',  # a stream to start paused; 1 arms Now Playing's online tick (tidal.c app_start)
           'isFav_playing',  # Now Playing's heart on a 0xf1 track: 1 a favourite (tidal.c app_fav)
           'g_time_zone','g_playspeed_mode', 'g_time24h_flag', 'g_backlight_status', 'g_lockscreen_pageflag', 'g_testmode_flag',
           'g_keylock_flag', 'g_keylock_mode',
           'g_sort_changeflag',  # Songs reloads in its order on its return (song_sort.c, as stock's Sort picker)
           'g_bluetoothflag', 'bt_linkstatus', 'g_guideflag', 'g_poweroff_state', 'g_usblink_status', 'bt__recv_pageflag',
           'g_power_longkey', 'g_ingore_bootkey_flag', 'g_equalizer_flag', 'g_navbar_status', 'g_playcover_type',
           'g_keytone_flag', 'g_folder_layer', 'g_otg_status', 'g_delete_flag', 'g_volume', 'g_maxvolume',
           'g_lightness', 'g_bootvolume', 'g_bootvol_flag',  # Brightness, Default Volume's level and switch (inline_slider.c)
           'g_po_status', 'g_bal_status',  # 3.5 mm and 4.4 mm jacks: 1 plugged (check_headset_status)
           'g_usbvol_mode',  # USB DAC volume: 0 fixed, else the volume (config_usbvolmode, device_set_volume)
           'g_usbdac_chargeflag',  # USB mode's charge choice, which switch_charge_enable gets there
           'g_memory_play', 'g_carmode', 'g_carmode_state',  # In-Vehicle Mode (pod_click)
           'g_bigcovermode', 'g_foldercover_flag',  # Enlarged Album Art (1 on) and Folder Album Cover (pod_click)
           'g_headset_output',  # the output config_outputchannel sets: 0, 1 the jacks, 2 Bluetooth
           'g_usbmode',  # USB mode: 1 Audio and DAC, else USB Storage (usbmode_page_init)
           'g_usbdet_value',  # 1 while a USB cable is in (USB mode's scan prompt outlives it)
           # Smart Wi-Fi (smart_wifi.c smart_poll): the Wi-Fi switch's state and stock's own gates on
           # toggling it (wifi_initstatus 0, wifi_connectstatus not 1, connecting), Network service's
           # AirPlay and DLNA, SyncLink (2: over Wi-Fi) and its session, Wi-Fi transfer and a pending time sync
           'g_wififlag', 'wifi_initstatus', 'wifi_connectstatus', 'g_airplayflag', 'g_dlnaflag',
           'g_synclinkflag', 'g_syncLink_status', 'g_wifitransport_flag', 'g_syncnettime_flag',
           'g_forcerefresh_flag',  # Now Playing's page update writes the title and artist again (catalogue.c np_done)
           'wifi_searchtime',
           # Settings > Bluetooth (bt_ui.c): its scan's countdown (not 0 while it runs, 1 as it ends), a flag that
           # has the page's timer rebuild its list, and Quality's choice (the btcode_* row, 0 LDAC Best to 7 SBC)
           'bt_searchtime', 'g_btsetting_flag', 'g_btcoding']  # the Wi-Fi page's scan running: not 0 (wifimenu_rows_build; wifi_rows.c)
# Audited stock browsing state, deque pointers, art locks, the status bar widget
# (system_bar_init stores it), the playing cover's track path and the playing track's tags as
# player_get_id3info parsed them; sizes are checked against the ELF.
CONTEXT_DATA = {'bt_showcoding': 4, 'g_memory_info': 3476, 'g_folder_path': 1024, 'g_class_type': 4,
                'g_local_classinfo_save': 912, 'g_artist_type': 4, 'album_modetype': 4, 'g_sort_flag': 4,
                'g_playlistsort_flag': 4,  # a playlist's Sort (PLAYLISTSORT): 0 by name, else Time added
                'p_deque_playlist': 4, 'p_deque_showlist': 4, 'tools_pdeq_directory': 4, 'mcl_pdeqplaylist': 4,
                'p_deque_searchshowlist': 4,  # Search's results (search_track_row_on_click reads its row's)
                'parse_cover_mutex': 24, 'g_playcover_mutex': 24, 'system_bar': 4, 'g_lastcover_url': 1024,
                'g_dacoff_time': 4, 'p_vector_select_record': 4,
                'g_play_id3_info': 2716,
                # Artists' source (PLAYSET ARTISTTYPE, the artist page's switch: 1 album artist);
                # the battery level (0-100) and the charger's state (1, 2 charging), get_battery_capacity's
                'artist_type': 4, 'g_power_capacity': 4, 'g_power_chargestate': 4,
                'pdeq_albumcoverlist': 4, 'aclist_mutex': 24,
                'tidalStreamingLevelNow': 4,  # the quality the Tidal URL thread asked for
                'stTidalTracks': 16,  # a Tidal track list: total, limit, offset, its deque
                'g_pressdown_y': 4,  # the touch's start, the window manager's pointer-down writes it
                'g_dropdown_menu_state': 4,  # Quick Settings: 1, 2 while it slides or follows a swipe, 3 open
                'bt_connectstatus': 4,  # a link thread runs (1), ended (2), checked (3)
                'bt_initstatus': 4,  # Bluetooth's start-up status: -102 is stock's "Bluetooth abnormal" (system.c reboot_record)
                'wifi_showlist': 4,  # the Wi-Fi page's scan records: name @0, flags @4 (wifi_rows.c)
                'pdeq_btshowlist': 4}  # Bluetooth's paired then found devices: MAC @4, name @0x24 (bt_ui.c)
# Windows the payload creates at runtime (window_create), so no rootfs asset names them.
PAYLOAD_WINDOWS = {'searchstrip_page', 'playlistedit_page', 'playlistsongs_page', 'addsongs_page', 'chapters_page', 'coverflow_page', 'games_page', 'mostplayed_page', 'shuffle_page', 'upnext_page', 'ratings_page', 'era_page', 'era_albums_page', 'rated_page', 'longsongs_page', 'spotify_page',
                   'spotbrowse_page', 'radio_page', 'radionp_page', 'streamconf_page', 'streamsvc_page', 'tidal_downloads', 'apps_page',
                   'addon_page', 'app_songs', 'qrcode_page', 'battery_page', 'carremote_page', 'carphone_page', 'carhowto_page', 'artistrows_page',
                   'storage_page', 'downloads_page', 'usbchoose_page', 'usbdebug_page',
                   'swupdate_page', 'empty_page', 'btdevice_page', 'btstopped_page',
                   'guestnp_page', 'guestset_page', 'guesttrust_page', 'hotspot_page', 'guestconnect_page', 'guestfail_page'}
ICONS = [f'menu_{c}{s}.png' for c in ('coverflow', 'rockbox') for s in ('', 'down')]
# The stock EQ preset page and the images only it and the stock equalizer page show: the PEQ
# editor clears that page's widgets on init and never binds the preset button, so none can load.
STOCK_EQ = ['release/assets/default/raw/ui/playset/preseteq_page.bin'] + [
    f'release/assets/default/raw/images/xx/{n}.png' for n in
    ['eq_bg', 'eq_off', 'eq_sidebg', 'eqbox'] + [f'eq_{p}{s}' for p in
    ('blues', 'classical', 'custon', 'dance', 'jazz', 'metal', 'pop', 'rock', 'scene') for s in ('', '_select')]]
# Stock V1.36's Album Wall, spectrum and VU pages, and the images only they and the buttons that open them show.
# Q2 Muse offers none: tools/ipod.py takes their Home card and their two Now Playing buttons out of the assets, and
# only those widgets' click handlers name the pages (on_home_coverflow_onclick, and Now Playing's two in
# tools/stock_map.tsv, playing_page_vu_on_click and playing_page_spectrum_on_click), so nothing can load them.
STOCK_PAGES = [f'release/assets/default/raw/ui/{n}_page.bin' for n in ('coverflow', 'spectrum', 'vu')] + [
    f'release/assets/default/raw/images/xx/{n}.png' for n in
    ['default_album_coverflow', 'play_spectrum', 'play_spectrumdown', 'play_vu', 'play_vudown', 'spectrum_back', 'spectrum_bar',
     *(f'spectrum_point{i}' for i in range(12)), 'vu_back', 'vu_dot', 'vu_left', 'vu_right']] + [
    # Stock's lock screen too (docs/internals.md#screen-off-and-wake): the lock window, its settings page and that
    # page's About, and the images only they and Display's Lock Screen row show. Only on_wm_keyup_fun and
    # device_standby open the lock window, and only with g_screensaver_mode set, which SAVER_OFF keeps clear; only
    # Display's row opens the settings page, and ringnav_display destroys that row.
    *(f'release/assets/default/raw/ui/{n}.bin' for n in
      ('saverscreen_page', 'systemset/screensaver_page', 'systemset/screensaver_about')),
    *(f'release/assets/default/raw/images/xx/{n}.png' for n in
      ('display_screenlock', 'screensaver_default', 'scrsaver_battery', 'scrsaver_charing', 'scrsaver_defaulticon',
       'scrsaver_lowbattery', 'scrsaver_playcover', 'scrsaver_slide'))]
# Superseded library versions (the unversioned and soname links already resolve to the newer ones)
# and on-device debugging and network tools; no script, program or library in the rootfs, and nothing
# in patch/, names them as a command (the rootfs's libcurl speaks only http, https and file). The
# links are busybox applets: busybox keeps the code, the command goes.
DEBUG_TOOLS = [*(f'usr/bin/{n}' for n in ('strace', 'strace-log-merge', 'sqlite3', 'hcidump', 'memtester')),
               'usr/sbin/nandtest']
DEBUG_LINKS = [*(f'usr/bin/{n}' for n in ('telnet', 'tftp')),
               *(f'usr/sbin/{n}' for n in ('inetd', 'dnsd', 'dhcprelay'))]
OBSOLETE = ['usr/lib/libdbus-1.so.3.19.8', *(f'usr/lib/lib{n}-2.0.so.0.5400.3' for n in
            ('gio', 'glib', 'gmodule', 'gobject', 'gthread')), *DEBUG_TOOLS, *DEBUG_LINKS]

FLAGS = ['--target=mipsel-linux-gnu','-march=mips32r2','-mabi=32','-mfp64',
         '-mno-abicalls','-fno-pic','-G0','-ffreestanding','-fno-builtin',
         '-fno-stack-protector','-fno-unwind-tables','-fno-asynchronous-unwind-tables',
         '-Oz','-Wall','-Wextra','-Werror','-Wno-unused-parameter']
# demo's payload only: LLVM's delay-slot filler also takes an instruction from the branch's likelier
# successor block, or from after the branch, which it does not by default. It moves only one that is
# safe on every path (a register live into the other successor is not written; a load only from the
# stack or a constant), so the code does the same with fewer nops. Measured on the --dev --new-player build of
# d3dd2e59: 20,704 bytes of .text and 6,799 nops fewer, 9,440 left in patch.elf.
FILL = ['-mllvm', '-disable-mips-df-succbb-search=false', '-mllvm', '-disable-mips-df-forward-search=false']
# navigation.c and the files split out of it (patch/navigation.h), each its own object, linked in this order.
NAVIGATION = ['navigation.c', 'theme_engine.c', 'playlists.c', 'scan_page.c', 'home.c', 'np_bar.c', 'volume.c', 'smart_wifi.c', 'system.c', 'usb_charge.c', 'usb_ui.c', 'tags.c', 'scrobble_ui.c', 'library_lists.c', 'durations.c', 'coverflow_position.c', 'ratings.c', 'scan_folders.c', 'folder_delete.c', 'battery.c', 'wifi_transfer.c', 'list_marks.c', 'song_rows.c', 'catalogue.c', 'clicker.c', 'seek.c', 'slider_page.c', 'inline_slider.c', 'wifi_rows.c', 'feedback.c', 'search_strip.c', 'keyboard.c', 'settings_pages.c', 'np_marquee.c', 'menu_modal.c', 'song_sort.c', 'name_order.c', 'power_rows.c', 'np_taps.c', 'coda_rc_shim.c', 'carremote_page.c', 'synclink.c', 'artist_page.c', 'np_about.c', 'az_index.c', 'library_artist.c', 'config_ini.c', 'library_album.c', 'keylock.c', 'playlist_pages.c', 'add_songs.c', 'storage.c', 'library_edit.c', 'engine.c', 'game_bricks.c', 'engine_demo.c', 'tilt.c', 'control_center.c', 'levels.c', 'upnext.c', 'charging.c', 'sw_update.c', 'library_settings.c', 'cf_empty.c', 'swipe_back.c', 'diagnostics.c', 'bt_name.c', 'bt_recv.c', 'bt_battery.c', 'pair_prompt.c', 'bt_ui.c', 'bt_detail.c', 'bt_stopped.c', 'guest_ui.c', 'home_np.c', 'status_row.c', 'ui_kit.c', 'np_settle.c', 'np_skip.c', 'song_radio.c', 'page_parallax.c', 'stream_config.c', 'stream_state.c', 'np_stream.c']

# Payload functions nothing in the payload calls, which build.py and ipod.py patch stock to call (jal and
# pointer sites by symbol): rooted, so --gc-sections keeps them. A name missing here fails the build with its
# KeyError at the lookup.
PAYLOAD_ROOTS = ['compact_set_row_layout', 'ipod_list_layout', 'parallax_prev', 'parallax_update', 'ringnav_ab_x', 'ringnav_album_label',
                 'ringnav_albumcovers', 'ringnav_art_header', 'ringnav_art_name', 'ringnav_artist_art_name',
                 'ringnav_artist_covers', 'ringnav_boot', 'ringnav_bt_connect_start', 'ringnav_class_icon',
                 'ringnav_cover_done', 'ringnav_cover_push', 'ringnav_cue_text', 'ringnav_dark_cover_usleep',
                 'ringnav_dark_usleep', 'ringnav_dialog_close', 'ringnav_ellipsis_text', 'ringnav_gbk_tag',
                 'ringnav_hold_info', 'ringnav_hold_timer', 'ringnav_hscroll_lull', 'ringnav_id3_path',
                 'ringnav_lyric_follow', 'ringnav_lyric_rows', 'ringnav_mainscheduling', 'ringnav_scan_dir',
                 'ringnav_scan_refresh', 'ringnav_shuffle', 'ringnav_song_unknown', 'ringnav_table_drag',
                 'ringnav_table_load', 'stock_row_style', 'stock_row_text', 'stock_set_row_layout']


def compile_payload(out):  # pragma: no mutate block
    """Compile and link the payload."""
    from peq import compile_common
    extra = compile_common(out, out/'stock-demo')  # also writes the libc/libcstl imports navigation.c uses
    nav = [out/f'{name[:-2]}.o' for name in NAVIGATION]
    # A section per function and per object, as UI_SOURCES get (peq.py compile_common), so --gc-sections drops
    # whatever nothing reaches; what only build.py or ipod.py patch in is kept by its --undefined below.
    for name, o in zip(NAVIGATION, nav):
        run('clang',*FLAGS,*FILL,'-ffunction-sections','-fdata-sections','-I',out,'-c',ROOT/'patch'/name,'-o',o)
    asm = ['.set noreorder', '.text']
    resume = {x: address for address, _, x in HOOKS.values() if x}
    for x, address in resume.items():
        asm += [f'.globl stock_{x}_trampoline', f'stock_{x}_trampoline:', f'lui $gp, {GP >> 16}',
                f'ori $gp, $gp, {GP & 65535}', f'j 0x{address + 12:x}', 'nop']
    (out/'trampoline.S').write_text('\n'.join(asm)+'\n')
    run('clang',*FLAGS,'-c',out/'trampoline.S','-o',out/'trampoline.o')
    # -O2: lld merges a string constant that ends another into its tail, which -O1 does not (3,328 bytes)
    run('ld.lld','-m','elf32ltsmip','-O2','--gc-sections','-T',ROOT/'patch/link.ld','-e','ringnav',
        *[f'--undefined={name}' for _, name, _ in HOOKS.values()], f'--undefined={GRADIENT_LEAF[2]}', '--undefined=ringnav_lrc_open',
        f'--undefined={WM_PAINT_LEAF[2]}', f'--undefined={QUEUE_LEAF[2]}', '--undefined=ringnav_audio_extension', '--undefined=start_mute', '--undefined=start_sleep', '--undefined=ringnav_genre_copy', '--undefined=ringnav_lyric_save', '--undefined=ringnav_lyric_drop', '--undefined=ringnav_cover_copy','--undefined=ringnav_folder_rm','--undefined=ringnav_bootmode', '--undefined=ringnav_ts_read',
        '--undefined=ringnav_baidu_poll', '--undefined=ringnav_stock_tls', '--undefined=ringnav_tidal_poll', '--undefined=catalogue_access', '--undefined=addon_m3u_dir', f'--undefined={COVERINFO_CALL[2]}',
        '--undefined=coverflow_player_cover', '--undefined=coverflow_grid_cover', '--undefined=coverflow_album_cover', '--undefined=coverflow_folder_cover', '--undefined=headset_bt_init',
        '--undefined=headset_status_register', '--undefined=headset_bt_autolink', '--undefined=bt_stopped_toast',
        *[f'--undefined={name}' for name in PAYLOAD_ROOTS],
        '--undefined=scrobble_auto_ms',  # inlined where the payload calls it; test/patch.py calls its own copy
        *nav,out/'trampoline.o',*extra,'-o',out/'patch.elf')
    # link.ld has no .data: an initialised static would land in the payload's read-only text
    check(not re.search(r'\] \.s?data ', run('readelf', '-SW', out/'patch.elf')), 'Payload has .data: writable state goes in .scratch')
    run('llvm-objcopy','-O','binary',out/'patch.elf',out/'patch.bin')
    return symbols(out/'patch.elf')

def check_layout(payload, ps):
    """The payload at BASE ends by SCRATCH, and its scratch state starts there and fills at most 128 KB."""
    check(len(payload) <= SCRATCH-BASE, 'Payload overlaps its scratch page')
    check(ps['__scratch_start'] == SCRATCH, 'Scratch state moved')
    check(ps['__scratch_end'] <= SCRATCH + 0x20000, 'Scratch state exceeds its 128 KB')

def compile_helper(out, cat, src, name, rels):
    """Link patch/{src}.c (or each of a list of sources under patch/) and start.c as name against the stock
    rootfs's libraries rels."""
    libs = []
    for rel in rels:
        libs.append(out/rel.rsplit('/', 1)[-1])
        libs[-1].write_bytes(cat(rel))
    srcs = [*([src] if isinstance(src, str) else src), 'start']
    objs = [out/f"{s.replace('/', '_')}.o" for s in srcs]
    for s, o in zip(srcs, objs):
        run('clang', *[f for f in FLAGS if f not in ('-mno-abicalls', '-G0')], '-mnan=2008', '-mabs=2008',
            '-mabicalls', *([] if isinstance(src, str) else ['-ffunction-sections', '-fdata-sections']), '-c',
            ROOT/'patch'/f'{s}.c', '-o', o)
    run('ld.lld', '-m', 'elf32ltsmip', '-e', '__start', '--dynamic-linker', '/lib/ld-linux-mipsn8.so.1',
        '--image-base=0x400000', '-z', 'noexecstack', '--gc-sections', '-s', '--hash-style=sysv', '--build-id=none',
        *objs, f'-L{out}', *(f'-l:{lib.name}' for lib in libs), '-o', out/name)  # by name: a rebuilt library may have no SONAME
    return (out/name).read_bytes()

def adbd_binary(out):
    """--dev builds' adbd (docs/adb.md), built by tools/thirdparty.py, stripped into out."""
    import thirdparty
    return thirdparty.stripped(thirdparty.build('adbd')/ADBD, out/'adbd').read_bytes()


def adb_files(out, cat):
    """A --dev build's ADB files ({rootfs path: (path whose inode metadata it takes, bytes)}): adbd, its gadget
    script, and stock's USB scripts wrapped (adb_wrapper), each stock copy before its wrapper, which takes the
    inode the copy's metadata comes from."""
    return {ADBD: (HELPER_LIKE, adbd_binary(out)),
            ADB_GADGET: (HELPER_LIKE, (ROOT/'patch/adb_gadget.sh').read_bytes()),
            **{f'usr/bin/{n.replace(".sh", "_stock.sh")}': (f'usr/bin/{n}', cat(f'usr/bin/{n}')) for n in USB_SCRIPTS},
            **{f'usr/bin/{n}': (f'usr/bin/{n}', adb_wrapper(n)) for n in USB_SCRIPTS}}


def adbd_record(out):
    """The manifest's adbd: its version, SHA-256 and size."""
    import thirdparty
    data = (out/'adbd').read_bytes()
    return dict(version=thirdparty.SOURCES['adbd'][0], sha256=sha(data), bytes=len(data))


def append_payload(image, payload, base, memsz, label):
    """Map payload at base through the image's final PT_NULL header, R/W/X: payloads keep static state."""
    segs = segments(image)
    nulls = [(o,p) for o,p in segs if p[0] == 0]
    check(len(nulls) == 1 and nulls[0][0] == segs[-1][0], f'{label}: no final PT_NULL slot')
    check(all(p[2]+p[5] < base for _,p in segs if p[0] == 1), f'{label}: payload mapping overlaps')
    off = (len(image)+65535)&~65535
    image.extend(bytes(off-len(image)))
    image.extend(payload)
    struct.pack_into('<8I',image,nulls[0][0],1,off,base,base,len(payload),memsz,7,65536)

TAG_C = 'usr/lib/libtag_c.so.2.0.1'
TAG_C_SHA = 'b00f06c20b33b1238d095fcbcd9e9e04594e715b44e8a9e1a0aa9e3962e8b2b1'

def patch_m4b_cover(raw):
    """Extend the pinned TagLib cover dispatcher with its existing MP4 extractor."""
    from ipod import branch
    check(sha(raw) == TAG_C_SHA, 'Unexpected TagLib cover library')
    image = bytearray(raw)
    base, site, mp4, next_format, gp = 0x30000, 0x125ac, 0x126d8, 0x125b4, 0x327e0
    # Keep M4A's successful comparison, then compare M4B before the original MP4 check.
    # GP-relative data and PC-relative branches work at any shared-library load address.
    words = [branch(0x10400000, base, mp4), 0,
             0x8f998170, 0x02002025, 0x27850000 | ((base + 44 - gp) & 0xffff),
             0x0320f809, 0,
             branch(0x10400000, base + 28, mp4), 0x8fbc0010,
             branch(0x10000000, base + 36, next_format), 0]
    payload = struct.pack('<11I', *words) + b'm4b\0'
    append_payload(image, payload, base, len(payload), 'TagLib cover')
    struct.pack_into('<I', image, segments(image)[-1][0] + 24, 5)  # immutable code/data
    check(struct.unpack_from('<I', raw, fileoff(raw, site))[0] == 0x1040004a,
          'Unexpected TagLib M4A cover branch')
    struct.pack_into('<I', image, fileoff(raw, site), branch(0x10000000, site, base))
    return bytes(image)

def bt_silence():
    """tools/bt_silence.S linked at BT_SILENCE: its bytes and symbols."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        run('clang', '--target=mipsel-linux-gnu', '-march=mips32r2', '-mno-abicalls', '-fno-pic',
            f'-DBUDGET={BT_SILENCE_SAMPLES}', '-c', ROOT/'tools/bt_silence.S', '-o', d/'s.o')
        run('ld.lld', '-m', 'elf32ltsmip', f'-Ttext={BT_SILENCE:#x}', '-e', 'timeout', d/'s.o', '-o', d/'s.elf')
        run('llvm-objcopy', '-O', 'binary', '-j', '.text', d/'s.elf', d/'s.bin')
        return (d/'s.bin').read_bytes(), symbols(d/'s.elf')

def bt_meta():
    """tools/bt_meta.c linked at BT_META: its bytes. Position independent by construction (bt_meta.c), so
    the object may carry no relocation but its branches' own."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        run('clang', '--target=mipsel-linux-gnu', '-march=mips32r2', '-mabicalls', '-fpic', '-Os', '-ffreestanding',
            '-fno-builtin', '-fno-jump-tables', '-Wall', '-Wextra', '-Werror', f'-DBT_META_ROOM={BT_META_ROOM}',
            *(f'-DBT_META_GOT_{k}={o}' for k, (_, o) in BT_META_GOT.items()), '-c', ROOT/'tools/bt_meta.c', '-o', d/'m.o')
        relocs = run('readelf', '-rW', d/'m.o')
        check(not re.search(r'R_MIPS_(?!PC16)\w+', relocs.split('.rel.pdr')[0]), 'bt_meta.c needs a relocation')
        check(not re.search(r'\.(rodata|data|bss)\S*\s+PROGBITS\s+\S+\s+\S+\s+0*[1-9a-f]', run('readelf', '-SW', d/'m.o')),
              'bt_meta.c has data')
        run('ld.lld', '-m', 'elf32ltsmip', f'-Ttext={BT_META:#x}', '-e', 'bt_meta_get_element_attributes', d/'m.o', '-o', d/'m.elf')
        run('llvm-objcopy', '-O', 'binary', '-j', '.text', d/'m.elf', d/'m.bin')
        return (d/'m.bin').read_bytes(), symbols(d/'m.elf')

def patch_bluetoothd(raw):  # pragma: no mutate block
    """Point stock bluetoothd's control-channel GetElementAttributes at tools/bt_meta.c."""
    check(sha(raw) == BLUETOOTHD_SHA, 'Unexpected bluetoothd')
    image = bytearray(raw)
    code, sym = bt_meta()
    append_payload(image, code, BT_META, len(code), 'bluetoothd')
    struct.pack_into('<I', image, segments(image)[-1][0] + 24, 5)  # code: read and execute only
    site, pdu, handler = BT_META_SLOT
    check(struct.unpack_from('<2I', raw, fileoff(raw, site)) == (pdu, handler), 'Unexpected bluetoothd handler table')
    struct.pack_into('<I', image, fileoff(raw, site + 4), sym['bt_meta_get_element_attributes'])
    return bytes(image)

def patch_bluealsa(raw):
    """Offer AAC at 48 kHz only. A headset that opens the stream itself (AirPods out of the case)
    picks 44.1 kHz, and bluealsa, still fed 48 kHz, drops about 8% of the AAC frames."""
    image = bytearray(raw)
    image[AAC_44K1] = 0
    # o32: preserve PCM and ra; hold its fd mutex while flushing the nonblocking
    # FIFO in 32 KiB batches, then send DROP. No encoder restart or codec change.
    words = [0x27bdffe0, 0xafbf001c, 0xafb00018, 0x00808025,
             0x0c10c1a4, 0x2604000c,
             0x0c10502e, 0x02002025, 0x1c40fffd, 0,
             0x0c10c0e8, 0x2604000c,
             0x8e040000, 0x24050006, 0x0c102dce, 0x24840050,
             0x8fb00018, 0x8fbf001c, 0x00001025, 0x03e00008, 0x27bd0020]
    reset = BT_DROP_PAYLOAD + len(words) * 4
    # The shared read helper receives a raw tail pointer, not the ffb_t. Rewind
    # each audited caller's partial PCM and update the helper's pointer/capacity
    # before it repolls; otherwise old and new tracks share one encoder frame.
    words += [0x8fa80054]  # saved return address
    for caller, offset in BT_PCM_CALLERS:
        check(struct.unpack_from('<I', raw, fileoff(raw, caller-8))[0] == 0x0c105174,
              'Bluetooth PCM caller mismatch')
        body = ([0x264b0000 | offset, 0x8d760000, 0xad760004, 0x8d750008]
                if offset is not None else
                [0x8fab004c, 0x8fac0040, 0x02cbb023, 0x02ccb021,
                 0xafac004c, 0x24150100, 0xafb50048])
        body += [0x081051e4, 0]  # existing OPEN/RESUME pacing reset and repoll
        words += [0x3c0a0000 | caller >> 16, 0x354a0000 | caller & 0xffff,
                  0x150a0000 | len(body)+1, 0, *body]
    words += [0x081051e4, 0]  # unknown caller: reset pacing without touching its buffer
    check(len(words)*4 <= BT_SILENCE - BT_DROP_PAYLOAD, 'Bluetooth DROP payload too long')
    words += [0] * ((BT_SILENCE - BT_DROP_PAYLOAD) // 4 - len(words))
    code, sym = bt_silence()
    payload = struct.pack(f'<{len(words)}I', *words) + code
    append_payload(image, payload, BT_DROP_PAYLOAD, len(payload), 'bluealsa')
    struct.pack_into('<2I', image, fileoff(raw, BT_DROP),
                     j(BT_DROP_PAYLOAD), 0)
    # Never flush the new track's FIFO from this asynchronously handled signal.
    struct.pack_into('<2I', image, fileoff(raw, BT_DROP_HANDLER), j(reset), 0)
    for address, old, op, target in BT_SILENCE_SITES:
        check(struct.unpack_from('<I', raw, fileoff(raw, address))[0] == old, 'Bluetooth silence site mismatch')
        new = {'j': j, 'jal': jal}[op](sym[target]) if op else target
        struct.pack_into('<I', image, fileoff(raw, address), new)
    return bytes(image)

# Stock's htranslate window animator's vtable (window_animator_htranslate_create's, .data.rel.ro): update_percent
# and draw_prev_window at +0x14 and +0x18, each pointed at page_parallax.c's, which runs stock's with its change (docs/internals.md#page-parallax).
HTRANSLATE_VT = 0x93b234
HTRANSLATE_SLOTS = ((0x696bd0, 'parallax_update'), (0x696d90, 'parallax_prev'))

PRODUCT_NAME = 'Q2 Muse'  # the product's name on screen (About); one place to rename it
STOCK_VERSION = 'V1.36'

def stock_header(context, ranges, themes, dev=0, repo=REPO, key=None):
    """stock.h, which the payload's C includes: context maps each CONTEXT_DATA name to the C address of
    its data (demo's symbol on the Q2; test/fw/host.py's own array), ranges are the subset faces' range macros
    (subset_font, in FACES' order: faces_header), themes theme.builtins()'s and key the release key's 32 public
    bytes (signing_key's; the tracked key's when None)."""
    from ipod import SETTINGS_ICONS
    from theme import theme_keys
    header = [f'#define RING_STEP {RING_STEP}', *faces_header(ranges)]
    header.append('#define TIMEZONE_NAMES {' + ','.join(json.dumps(zone) for _, zone in TIMEZONES) + '}')
    header.append('#define TIMEZONE_ORDER {' + ','.join(map(str, TIMEZONE_ORDER)) + '}')
    header.append('#define TIMEZONE_CITIES {' + ','.join(json.dumps(label.split(' ', 1)[1]) for label, _ in TIMEZONES) + '}')
    header += [f'#define {name} ((const unsigned char *){context[name]})' for name in CONTEXT_DATA]
    # iPod's image hook leaves the settings icons' category colours alone (navigation.c settings_icon).
    names = ''.join(n.removesuffix('.png') + '\\0' for n in SETTINGS_ICONS)
    header.append(f'#define SETTINGS_ICON_NAMES "{names}"')
    header.append(f'#define ROCKBOX_FLAG "{ROCKBOX_FLAG}"')  # Home's Rockbox row, for S90play
    header += [f'#define {name} 0x{address:x}u' for name, (_, address) in NAME_COMPARATORS.items()]  # name_order.c
    # About: the stock firmware's version on its own row, and this build's on a row named for the
    # product (PRODUCT_NAME, the one place the name the screens show is spelled).
    header += [f'#define PRODUCT_NAME "{PRODUCT_NAME}"', f'#define STOCK_VERSION "{STOCK_VERSION}"', f'#define Q2POD_VERSION "{VERSION} Muse{f" dev {dev}" if dev else f" beta {BETA}" if BETA else ""}"',
               f'#define Q2POD_TAG "{"" if dev else VERSION}"',  # netupdate.c: the release tag, none when --dev
               f'#define Q2POD_DEV {1 if dev else 0}',  # navigation.c stale_note
               # sw_update.c: the updater's identity (firmware_v20.info's version), which an equal update.tar's repeats
               f'#define Q2POD_FW_TAG "{dev_tag(dev) if dev else TAG}"',
               f'#define Q2POD_REPO "{repo}"',  # netupdate.c: the repo whose latest release it fetches
               '#define Q2POD_SIGN_KEY {' + ','.join(f'0x{b:02x}' for b in key or signing_key(None, False)) + '}',  # release_sig.c
               f'#define Q2POD_UA "Q2Muse/{VERSION} (https://github.com/{REPO})"']  # onlinematch.c, as MusicBrainz asks: the project's contact, not --update-repo's
    check(len(themes) == 1, f'{len(themes)} built-in themes: the payload has one (theme_engine.c theme())')
    header.append(f'#define THEME_N {len(themes)}')  # theme_engine.c theme()
    header.append(f'#define THEME_KEYS {len(theme_keys(themes))}')  # navigation.c st.own[], themes.inc theme_keys[]
    from art import corner_table
    header.append(f'#define ROUND_COVER {corner_table()}')  # navigation.c paint_round_rows
    return '\n'.join(header)+'\n'

def signing_key(path, dev):
    """The release key's 32 public bytes that Software Update and the App Store check signatures with
    (docs/internals.md#release-signatures): assets/signing/q2-coda-release.pub, which a release build must have;
    a --dev build may take another (path), such as a test key whose private half is at hand."""
    from signing import PUBLIC_KEY, public_key
    check(path is None or dev, '--signing-key requires --dev: a release is checked against assets/signing/q2-coda-release.pub')
    path = path or PUBLIC_KEY
    check(path.is_file(), f'{path}: no release public key (docs/releasing.md#signing-a-release)')
    return public_key(path.read_bytes())

def build(zip_path, out, logo, dev=False, repo=REPO, aic_idle_wait=False, rotator_lazy_clock=False, pwm_idle_clock=False, mcu_idle_sleep=False, flac_experiments='sync,lpc,output', signing_pub=None, new_player=False, apps_dir=APPS_DIR):  # pragma: no mutate block
    from flac import selection
    flac_experiments = selection(flac_experiments)
    # Kernel-module experiments: no measured saving (docs/clock-gating.md, pwm-clock-gating.md,
    # audio-notifier.md), none handles suspend/resume, and stock Standby suspends by default; dev only.
    check(not aic_idle_wait or dev, '--aic-idle-wait requires --dev')
    check(not rotator_lazy_clock or dev, '--rotator-lazy-clock requires --dev')
    check(not pwm_idle_clock or dev, '--pwm-idle-clock requires --dev')
    check(not mcu_idle_sleep or (dev and pwm_idle_clock), '--mcu-idle-sleep requires --dev --pwm-idle-clock')
    # The new player (docs/player-plan.md): in a --dev build only until it replaces hciplayer
    check(not new_player or dev, '--new-player requires --dev')
    check(dev or version_ok(VERSION, BETA), f'VERSION must be three single digits (x.y.z) and BETA 0-9: {VERSION}, {BETA}')
    version = TAG
    check(re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9._-]+', repo), f'{repo}: not a GitHub owner/name')  # a C string
    key = signing_key(signing_pub, dev)
    out.mkdir(parents=True, exist_ok=True)
    check(not (out/'update.tar').exists(), 'Output already exists; use a fresh --out directory')
    if dev:
        dev = dev_build()
        version = dev_tag(dev)
    source = source_sha256()
    raw = zip_path.read_bytes()
    check(sha(raw) == ZIP_SHA, 'Unsupported ZIP: SHA-256 differs from audited original')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        tarbytes = z.read('Q2 Firmware V1.36/update.tar')
    with tarfile.open(fileobj=io.BytesIO(tarbytes)) as t:
        meta = t.getmembers()
        check([m.name for m in meta] == ['firmware_v20.info','recovery-update',
              'recovery-update/xImage','recovery-update/rootfs.squashfs'], 'Unexpected package members')
        blobs = {m.name:need(t.extractfile(m), f'Unreadable {m.name}').read() for m in meta if m.isfile()}
    info = blobs['firmware_v20.info'].decode().splitlines()
    check(info[:2] == ['Shanling Q2', STOCK_VERSION], 'Wrong model/version')
    sq = out/'stock.squashfs'; sq.write_bytes(blobs['recovery-update/rootfs.squashfs'])
    def cat(path): return subprocess.check_output(['unsquashfs', '-cat', str(sq), path])
    raw_demo = cat('release/bin/demo')
    check(sha(raw_demo) == DEMO_SHA, 'Unsupported demo: SHA-256 differs from the audited original')
    (out/'libc-2.28.so').write_bytes(cat('lib/libc-2.28.so'))
    # Every allowlisted context must be a window name. The runtime name is the root "name"
    # property of the UI asset, not the asset path, so check the stock rootfs assets directly:
    # a prefix-trimmed typo cannot silently disable a screen this way.
    from ipod import (AUDIT, ARTIST_ALBUMS, SETTINGS_ICONS, inc, UI_ASSETS, patch_asset,
                      QUIET_ICONS, quiet_icon, imagemagick, patch_code, CLOSE_LOAD, DUMMY_LOAD, patch_style, patch_word, branch, settings_icon,
                      STATUS_ICONS, status_icon, row_icon, ART_PLACEHOLDERS, sized_art,
                      set_assets)
    contexts = re.findall(r'"([^"]+)"', (ROOT/'patch/contexts.inc').read_text())
    check(contexts, 'No navigation contexts audited')
    windows, chinese = set(), set()  # chinese: the UI assets english_ui rewrites
    for line in run('unsquashfs', '-l', sq).splitlines():
        found = re.search(r'/raw/ui/(.+)\.bin$', line)
        if not found: continue
        rel = found.group(1)
        data = cat('release/assets/default/raw/ui/'+rel+'.bin')
        if CJK.search(data.decode('utf-8', 'replace')) and 'release/assets/default/raw/ui/'+rel+'.bin' not in STOCK_EQ + STOCK_PAGES:
            chinese.add(rel + '.bin')  # the stock EQ pages go, and the pages not offered
        i = data.find(b'name\x00')
        name = data[i+5:data.find(b'\x00', i+5)].decode('utf-8', 'replace') if i >= 0 else ''
        windows.add(name or rel.split('/')[-1])
        # iPod pre-sizes the settings icons, so no UI asset may name one (only native settings code does).
        for icon in SETTINGS_ICONS:
            check(icon.removesuffix('.png').encode() + b'\0' not in data, f'{rel}: names settings icon {icon}')
    check(windows, 'No UI assets in the stock rootfs')
    for name in contexts:
        check(name in windows | PAYLOAD_WINDOWS, f'Context {name} is not a window name in the stock rootfs')
    demo = out/'stock-demo'; demo.write_bytes(raw_demo)
    symbol_table = run('readelf', '-Ws', demo)
    syms = symbols(demo, symbol_table)
    for name, address in APP_URL_CALLS.items():
        check(syms.get(name) == address, f'{name}: not at {address:#x} in the stock demo')
    syms.update(PRIVATE_FUNCTIONS)
    fonts = {f.path: face_font(f) for f in FACES.values() if f.source}
    for name in GLOBALS:
        check(re.search(rf'\b1\s+OBJECT\s+GLOBAL\s+DEFAULT\s+\d+\s+{name}$',
                        symbol_table, re.M), f'{name}: byte global size mismatch')
    for name, size in CONTEXT_DATA.items():
        check(re.search(rf'\b{size}\s+OBJECT\s+GLOBAL\s+DEFAULT\s+\d+\s+{name}$',
                        symbol_table, re.M), f'{name}: context data size mismatch')
    from theme import builtins, emit, geometry_sets
    themes = builtins()
    (out/'stock.h').write_text(stock_header({name: f'0x{syms[name]:x}u' for name in CONTEXT_DATA},
                                            [r for _, r in fonts.values()], themes, dev, repo, key))
    set_pages, redirects = {}, {}
    ui = 'release/assets/default/raw/ui/'
    for i, g in enumerate(geometry_sets(themes)[1:], 1):
        for path, data in set_assets(cat, g).items():
            folder, _, base = path.rpartition('/')
            like = f'{folder}/{base.removeprefix(g.name + "_")}'
            rel = like.removeprefix('release/assets/default/raw/ui/')
            set_pages[path] = (like, english_ui(rel, data) if rel in chinese else data)
            redirects[rel.removesuffix('.bin')] = redirects.get(rel.removesuffix('.bin'), 0) | 1 << i
    emit(themes, out, redirects)
    ps = compile_payload(out)
    payload = (out/'patch.bin').read_bytes()
    check_layout(payload, ps)
    patched = bytearray(raw_demo)
    patch_timezones(patched)
    def jump(off, name): patched[off:off+8] = struct.pack('<II', j(ps[name]), 0)
    for name, (address, replacement, _) in HOOKS.items():
        check(syms[name] == address, f'{name}: callback address mismatch')
        off = fileoff(patched, address)
        prolog = struct.unpack_from('<III', patched, off)
        check(prolog[0] >> 16 == 0x3c1c and prolog[1] >> 16 == 0x279c and
              prolog[2] == 0x0399e021, f'{name}: unexpected PIC prologue')
        low = prolog[1] & 65535
        gp = ((prolog[0] & 65535) << 16) + (low if low < 32768 else low - 65536) + address
        check(gp == GP, f'{name}: unexpected GOT base')
        jump(off, replacement)
    for name, address, replacement, words in (WM_PAINT_LEAF, GRADIENT_LEAF, QUEUE_LEAF):
        off = fileoff(patched, address)
        check(syms[name] == address and struct.unpack_from('<III', patched, off) == words, f'{name}: unexpected code')
        jump(off, replacement)
    # style_get_color's own bal style_get_gradient, returning to STYLE_COLOR_GRADIENT_RET, stays unmapped.
    ret = inc('STYLE_COLOR_GRADIENT_RET')
    check(struct.unpack_from('<I', patched, fileoff(patched, ret - 8))[0] == branch(0x04110000, ret - 8, GRADIENT_LEAF[1]),
          'style_get_color: unexpected gradient call')
    # tidal_start reads the song id from $s2: set to the thread's own copy at 0x48721c, freed after
    # its only mcl_tidalStartPlayer call (lw t9; jalr; move a0, s7).
    check(struct.unpack_from('<I', patched, fileoff(patched, 0x48721c))[0] == 0x00409025 and
          struct.unpack_from('<III', patched, fileoff(patched, 0x487b28)) == (0x8f99ab18, 0x0320f809, 0x02e02025),
          'Tidal URL thread: unexpected song id register')
    # ringnav_mode leaves advanced play on for player_initconfig's call, which returns to INITCONFIG_MODE_RET.
    ret = inc('INITCONFIG_MODE_RET')
    check(struct.unpack_from('<II', patched, fileoff(patched, ret - 12)) == (0x8f99b80c, 0x0320f809),  # 0x51e0c4: lw $t9, mclSetPlayMode's slot
          'player_initconfig: unexpected mclSetPlayMode call')
    patch_word(patched, 0x504188, 0x0411e963, jal(ps['ringnav_bootmode']),
               'config_init preserves the saved queue when applying the boot play mode')
    from peq import patch_player
    raw_player = cat('usr/bin/hciplayer')
    audio = patch_player(raw_player, out/'peq', flac_experiments)
    bluealsa = patch_bluealsa(cat(BLUEALSA))
    (out/'bluealsa').write_bytes(bluealsa)
    bluetoothd = patch_bluetoothd(cat(BLUETOOTHD))
    (out/'bluetoothd').write_bytes(bluetoothd)
    (out/'watchdog').write_bytes(cat(WATCHDOG).replace(*WATCHDOG_SLEEP))
    shadow = lock_root(cat(SHADOW))
    check(not password_logins(shadow), 'A password still logs in')
    (out/'shadow').write_bytes(shadow)
    inittab = cat(INITTAB)
    inittab, gettys = inittab.replace(*SERIAL_GETTY), inittab.count(SERIAL_GETTY[0])
    check(gettys == 1 and b'getty' not in inittab, 'Unexpected inittab')
    (out/'inittab').write_bytes(inittab)
    bt_main = cat(BT_MAIN)
    check(all(bt_main.count(old) == 1 for old, _ in (BT_DISCOVERABLE, BT_NAME, BT_EXPERIMENTAL)), 'Unexpected Bluetooth main.conf')
    (out/'main.conf').write_bytes(bt_main.replace(*BT_DISCOVERABLE).replace(*BT_NAME).replace(*BT_EXPERIMENTAL))
    thttpd_conf = cat(THTTPD_CONF)
    check(thttpd_conf.count(THTTPD_USER[0]) == 1 and b'user=' not in thttpd_conf.replace(THTTPD_USER[0], b''),
          'Unexpected thttpd.conf')
    check(WWW_DATA in b'\n' + cat('etc/passwd'), 'No www-data:33 in /etc/passwd')
    (out/'thttpd.conf').write_bytes(thttpd_conf.replace(*THTTPD_USER))
    for address, old, new in ARTIST_ALBUMS:
        patch_word(patched, address, old, new, 'artist detail opens on Albums')
    patch_word(patched, *SHUFFLE_CALL, jal(ps['ringnav_shuffle']),
               'shuffle honours Play next')
    patch_word(patched, *HOLD_RETURN, jal(ps['compact_now_playing']),
               'long Return destination after stock input gates')
    patch_word(patched, *KEY_ABORTED, 'A press the window changed under stays ended until its release')
    patch_word(patched, *STANDBY_WAKE, 'Standby resume stays dark')
    patch_word(patched, *NAV_SHOWN, "stock's navigation bar stays shown")
    patch_word(patched, *WHEEL_MODE, "stock's wheel stays in Volume Mode")
    patch_word(patched, *HOME_KEYUP, 'Home binds no key-up of its own')
    patch_word(patched, *NP_CENTRE, "Now Playing leaves Centre to stock's power-key handling")
    patch_word(patched, *KEYLOCK_LIT, 'Buttons lock acts only with the screen off')
    patch_word(patched, *SAVER_OFF, "stock's lock screen stays off")
    patch_word(patched, *PREV_RESTART, 'Previous restarts the track from 3 s in')
    patch_word(patched, *VOL_DARK, 'The volume dialog steps with the screen off')
    for site in MORE_ARTIST_CLOSE:
        patch_word(patched, *site, "Artist info closes the artist and album pages it found")
    patch_word(patched, *MAINSCHEDULING_CALL, jal(ps['ringnav_mainscheduling']), 'player scheduling paced')
    patch_word(patched, *START_MUTE_CALL, jal(ps['start_mute']), 'natural start: no mute before the end')
    patch_word(patched, *START_SLEEP_CALL, jal(ps['start_sleep']), 'natural start: no sleep before the end')
    for address, old, new, fn in HOLD_KEYS:
        patch_word(patched, address, old, new or jal(ps[fn]), 'Play/Pause and Return hold time')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, 0x51dcbc))[0] == 0x8f99b770,
          'player cover: expected toolsGetExternCover import')
    patch_word(patched, *PLAYER_COVER, jal(ps['coverflow_player_cover']), 'Now Playing art through ffmpeg')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, 0x5c8d60))[0] == 0x8f99fabc,
          "stock's database open: unexpected GOT load")
    patch_word(patched, *STOCK_DB_OPEN, jal(ps['stock_db_open']), "stock's database waits out a reader")
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, 0x51f5c8))[0] == 0x8f99f680,
          'id3 path: expected strcpy import')
    patch_word(patched, *ID3_PATH, jal(ps['ringnav_id3_path']), "a CUE track's file tags")
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, 0x53d6d4))[0] == 0x8f99c14c,
          'lyric follow: expected scroll_view_scroll_to import')
    patch_word(patched, *LYRIC_FOLLOW, jal(ps['ringnav_lyric_follow']), 'lyric rows whole at the top')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, 0x5fb954))[0] == 0x8f99a858,
          'hscroll lull: expected widget_add_timer import')
    patch_word(patched, *HSCROLL_LULL, jal(ps['ringnav_hscroll_lull']), 'a scrolling label holds at its start')
    patch_word(patched, *HSCROLL_ONCE, 'a single pass holds its end')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, 0x5fc060))[0] == 0x8f99ccd0,
          'hscroll ellipsis: expected widget_draw_text_in_rect import')
    patch_word(patched, *HSCROLL_ELLIPSIS, jal(ps['ringnav_ellipsis_text']), 'an ellipsis never follows a space')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, HSCROLL_GAP[0] - 4))[0] == 0x8f99aebc,
          'hscroll gap: expected widget_set_prop_int')
    patch_word(patched, *HSCROLL_GAP, 'a scrolling label ends its pass at its end')
    for address in ALBUM_LABELS:
        check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, address - 4))[0] == 0x8f99ac00,
              'album labels: expected set_hscroll_label_attribute')
        patch_word(patched, address, 0x0320f809, jal(ps['ringnav_album_label']), "Albums' artist line and grid titles stay still")
    for word in FOCUS_RESTORE:
        patch_word(patched, *word, "stock's focus stays off a reloaded page's rows")
    patch_word(patched, *LYRIC_ROWS, jal(ps['ringnav_lyric_rows']), 'lyrics keep their place on a refresh')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, 0x5bc304))[0] == 0x8f99f5ac,
          'lyric file: expected fopen import')
    patch_word(patched, *LYRIC_OPEN, jal(ps['ringnav_lrc_open']), 'lyric files read as tags are')
    for call, load, fn in GRID_COVER:
        check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, load))[0] == 0x8f99b76c,
              'grid cover: expected toolsGetAlbumCover import')
        patch_word(patched, call, 0x0320f809, jal(ps[fn]), 'Ogg art in Albums and Folder view')
    patch_word(patched, *BT_CONNECT_START, jal(ps['ringnav_bt_connect_start']),
               'Bluetooth: Resume on connect')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, BT_INIT[0] - 4))[0] == 0x8f99f61c,
          'Bluetooth init: expected btctl_create_daemon_thread import')
    patch_word(patched, *BT_INIT, jal(ps['headset_bt_init']), 'Bluetooth callbacks before control thread')
    patch_word(patched, *BT_PARAMS, 0, 'Bluetooth state initialized before control thread')
    check(struct.unpack_from('<III', raw_demo, fileoff(raw_demo, BT_STATUS[0] - 4)) ==
          (0x8f99fc1c, 0x0320f809, 0x2484c180), 'Bluetooth status: expected btctl_device_status_callback(0x4fc180)')
    patch_word(patched, *BT_STATUS, jal(ps['headset_status_register']), 'Bluetooth: incoming link during a connect')
    patch_word(patched, *BT_AUTOLINK, jal(ps['headset_bt_autolink']), 'Bluetooth: auto-link keeps an incoming link')
    for site, stock in BT_ABNORMAL_TOASTS:
        patch_word(patched, site, stock, jal(ps['bt_stopped_toast']), 'Bluetooth Stopped: its page, not the toast')
    patch_word(patched, *SCAN_REFRESH, jal(ps['ringnav_scan_refresh']), 'folder scan refreshes existing tags')
    patch_word(patched, *TAG_TEXT, jal(ps['ringnav_gbk_tag']), 'tag text GBK only when it reads as GBK')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, GENRE_COPY[0] - 4))[0] == 0x8f99f680,
          'genre copy: expected strcpy import')
    patch_word(patched, *GENRE_COPY, jal(ps['ringnav_genre_copy']),
               'long genre tags stay inside the style field')
    for address in CUE_TEXT:
        patch_word(patched, address, branch(0x04110000, address, syms['toolsIconvConvert']),
                   jal(ps['ringnav_cue_text']), 'CUE text read as UTF-8, GBK or Windows-1252')
    for slot, (old, name) in enumerate(HTRANSLATE_SLOTS):  # page push and pop with parallax (page_parallax.c)
        patch_word(patched, HTRANSLATE_VT + 0x14 + 4 * slot, old, ps[name], 'htranslate: the page under drifts and dims')
    patch_word(patched, DIALOG_CLOSE, branch(0x04110000, DIALOG_CLOSE, syms['idle_add']),
               jal(ps['ringnav_dialog_close']), "a modal dialog's close skips one already gone")
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, SCAN_DIRS[0] + 4))[0] == 0x24a59870,
          'library scan: expected the "." exclusion')
    patch_word(patched, *SCAN_DIRS, jal(ps['ringnav_scan_dir']), 'the library scan skips hidden folders')
    for address, old, new in DROP_CACHES:
        patch_word(patched, address, old, new, 'keep the page cache')
    for address, old, new in BT_INPUT:
        patch_word(patched, address, old, new, "a headset's keys are read once, by the headset worker")
    for address, name in STREAM_POLL_CALLS:
        patch_word(patched, address, 0x0320f809, jal(ps[name]), 'stream threads park while idle')
    for address in CATALOGUE_ACCESS:
        check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, address - 4))[0] == ACCESS_GOT_LOAD,
              f'{address:#x}: expected the access import before the call')
        patch_word(patched, address, 0x0320f809, jal(ps['catalogue_access']), "a server row's play check")
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, COVERINFO_CALL[0] + 4))[0] == COVERINFO_CALL[1],
          "online id3: expected the REC_COVER load in the cover call's delay slot")
    patch_word(patched, COVERINFO_CALL[0], 0x0320f809, jal(ps[COVERINFO_CALL[2]]), "a card app's cover by URL")
    for address, name in DARK_POLLS:
        patch_word(patched, address, 0x0320f809, jal(ps[name]), 'stock polls idle while dark')
    check(struct.unpack_from('<I', patched, fileoff(patched, LYRIC_SAVE_FMT[0]))[0] == LYRIC_SAVE_FMT[1],
          'Unexpected Download Cover & Lyrics save')
    patch_word(patched, LYRIC_SAVE_CALL, 0x0320f809, jal(ps['ringnav_lyric_save']), 'lyrics saved beside the song')
    for address, option in STOCK_TLS_CALLS:
        check(stock_tls_site(patched, address, option), f'{address:#x}: not a TLS check setopt')
        patch_word(patched, address, 0x0320f809, jal(ps['ringnav_stock_tls']), 'stock HTTPS verifies its peer')
    check(struct.unpack_from('<I', patched, fileoff(patched, LYRIC_DROP_FMT[0]))[0] == LYRIC_DROP_FMT[1],
          'Unexpected Download Cover & Lyrics removal')
    patch_word(patched, LYRIC_DROP_CALL, 0x0320f809, jal(ps['ringnav_lyric_drop']), "no lyrics removes .downpic's copy")
    check(struct.unpack_from('<I', patched, fileoff(patched, COVER_PICK_FMT[0]))[0] == COVER_PICK_FMT[1],
          "Unexpected Now Playing's picked cover copy")
    patch_word(patched, COVER_PICK_CALL, 0x0320f809, jal(ps['ringnav_cover_copy']), "Now Playing copies a picked cover")
    check(struct.unpack_from('<I', patched, fileoff(patched, FOLDER_RM_FMT[0]))[0] == FOLDER_RM_FMT[1],
          'Unexpected folder delete')
    patch_word(patched, FOLDER_RM_CALL, 0x0320f809, jal(ps['ringnav_folder_rm']), "a folder's delete quotes its path")
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, SONG_UNKNOWN_CALL-8))[0] == 0x8f99a924,
          "song bind: expected getFormatString import")  # lw t9, -0x56f8(gp)
    patch_word(patched, SONG_UNKNOWN_CALL, 0x0320f809, jal(ps['ringnav_song_unknown']), "a song row's empty artist")
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, TABLE_LOAD_CALL-0x14))[0] == 0x8e9900e4,
          "table load: expected on_load_data's callback")  # lw t9, 0xe4(s4)
    patch_word(patched, TABLE_LOAD_CALL, 0x0320f809, jal(ps['ringnav_table_load']), "a bound table row's faces")
    patch_word(patched, *TABLE_DRAG_CALL, jal(ps['ringnav_table_drag']), 'a drag stops at a table\'s ends')
    for word in FOLDER_NAME:
        patch_word(patched, *word, 'folder names are literal text')
    # The GOT loads Q2 Muse writes (FOLDER_NAME's, and tools/ipod.py's two): no stock word beside one names its slot, and
    # V1.36 moved two of the three to other functions' slots. Each slot holds its function, or the build stops.
    for load, name in ((FOLDER_NAME[1][2], 'tk_strncpy'), (CLOSE_LOAD, 'navigator_close'), (DUMMY_LOAD, 'dummy_destroy')):
        check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, GP + ((load & 0xffff) ^ 0x8000) - 0x8000))[0] == syms[name],
              f"{name}: the GOT load written for it names another slot")
    patch_word(patched, NP_INFO_CALL, branch(0x04110000, NP_INFO_CALL, PRIVATE_FUNCTIONS['np_info']),
               jal(ps['ringnav_np_info']), "Now Playing's info lines")
    for site in AB_X_CALLS:
        patch_word(patched, site, branch(0x04110000, site, PRIVATE_FUNCTIONS['ab_x']),
                   jal(ps['ringnav_ab_x']), "A-B markers on the slider")
    for address, old, new in LDAC_HQ:
        patch_word(patched, address, old, new, 'LDAC HQ sends 990 kbps')
    patch_word(patched, WHEEL_TS_READ[0], WHEEL_TS_READ[1], jal(ps['ringnav_ts_read']), 'wheel ticks')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, GP - 0x7f44))[0] == 0xa50000 and
          all(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, a))[0] == w for a, w in NET_TYPE_READS),
          'networkStart/networkStop: unexpected network type gate')
    for address in AUDIO_EXTENSION_CALLS:
        check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, address-8))[0] == 0x8f99fa14,
              'audio extension: expected strstr import')
        patch_word(patched, address, 0x0320f809, jal(ps['ringnav_audio_extension']),
                   'recognize M4B alongside existing audio extensions')
    check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, M3U_DIR_CALL-4))[0] == 0x8f99bb64,
          'playlist import: expected toolsGetDirectoryM3u8 call')  # lw t9, -0x452c(gp)
    patch_word(patched, M3U_DIR_CALL, 0x0320f809, jal(ps['addon_m3u_dir']), "card apps import only their own playlists")
    for at, comparator in NAME_COMPARATORS.values():
        lw, addiu = struct.unpack_from('<2I', raw_demo, fileoff(raw_demo, at))
        page = struct.unpack_from('<I', raw_demo, fileoff(raw_demo, GP + (lw & 0xffff ^ 0x8000) - 0x8000))[0]
        check(lw >> 16 == 0x8f82 and addiu >> 16 == 0x2442 and page + (addiu & 0xffff ^ 0x8000) - 0x8000 == comparator,
              f'name sort: expected comparator 0x{comparator:x} at 0x{at:x}')
    for address in SORT_TRIMS:
        patch_word(patched, address, branch(0x04110000, address, syms['toolsTrimLeft']),
                   jal(ps['ringnav_sort_key']), 'sort without a leading article')
    for address, name in ALBUM_ART:
        patch_word(patched, address, 0x0320f809, jal(ps[name]), 'same-named albums apart in art')
    for row in ALBUM_ART_ROW:
        patch_word(patched, *row, 'same-named albums apart in art')
    for address, load in COVER_PUSH:
        check(struct.unpack_from('<I', raw_demo, fileoff(raw_demo, load))[0] == 0x8f99f7f8,
              'cover push: expected _deque_push_back import')  # lw t9, -0x8cc(gp)
        patch_word(patched, address, 0x0320f809, jal(ps['ringnav_cover_push']), "a cover task's name and path freed once copied")
    patch_word(patched, CLASS_ICON, branch(0x04110000, CLASS_ICON, syms['class_icon']),
               jal(ps['ringnav_class_icon']), 'Artists rows without the head picture')
    for word in COVER_PAUSE:
        patch_word(patched, *word, 'album grids never pause the cover worker')
    for word in TIDAL_PAGE_DONE:
        patch_word(patched, *word, 'Tidal lists load every page')
    for word in ARTIST_COVER_ROW:
        patch_word(patched, *word, "an artist's albums request the covers in view")
    # Local art completion: pass the worker's copied key, then validate the row under aclist_mutex.
    patch_word(patched, 0x4f84bc, 0x8f99b760, 0x27a61688, 'cover completion key')
    patch_word(patched, 0x4f84c0, 0x0411ff44, jal(ps['ringnav_cover_done']),
               'discard stale cover completion')
    # The folder-cover path branched past the key (b 0x4f84c0); it enters at the key as the others do.
    patch_word(patched, 0x4f94fc, 0x1000fbf0, 0x1000fbef, 'folder cover completion key')
    # Pin added private entry points as well as every replaced instruction, and the stock bitmap,
    # canvas and slide_menu entries Coverflow's depth renderer calls (docs/internals.md#coverflow-depth).
    for name, original in AUDIT['private_prologues'].items():
        off = fileoff(raw_demo, syms[name])
        check(raw_demo[off:off+12].hex() == original, f'{name}: unexpected stock entry')
    for address, original in AUDIT['event_abi_words'].items():
        off = fileoff(raw_demo, int(address, 16))
        check(raw_demo[off:off+4].hex() == original, f'{address}: unexpected event ABI instruction')
    code_changes = patch_code(patched, ps)
    # Single shared version literal: the updater refuses an equal firmware_v20.info version (update_firmware
    # 0x502bd0, check_otginfo 0x5030f8), so it carries the build tag and stock V1.36 can be flashed back.
    # About's FW. Version row reads it too (0x4c10b0); ringnav_about shows STOCK_VERSION there instead.
    check(patched.count(b'V1.36\0') == 1, 'Version literal is not unique')
    patched = patched.replace(b'V1.36\0', version.encode()+b'\0')
    patched = patched.replace(*RTC_WRITE)
    patched = tidal_https(patched)
    check(patched.count(NTP_POOL[0]) == 1, 'Unexpected NTP servers')
    patched = patched.replace(*NTP_POOL)
    check(patched.count(WIFI_LPS[0]) == 1, 'Unexpected Wi-Fi power save command')
    patched = patched.replace(*WIFI_LPS)
    check(re.search(r'\.pdr +PROGBITS +0+ +0*%x +0*%x ' % PDR, run('readelf', '-SW', demo)) and
          all(p[1]+p[4] <= PDR[0] for _,p in segments(patched) if p[0] == 1), '.pdr: not the audited section')
    patched[PDR[0]:PDR[0]+PDR[1]] = bytes(PDR[1])
    append_payload(patched, payload, BASE, ps['__scratch_end']-BASE, 'demo')
    (out/'demo').write_bytes(patched)
    import os
    if os.environ.get('Q2_DEMO_ONLY'):  # pragma: no mutate block
        # tools/ci/q2live.py: a payload change needs only demo (and the player, already in out/peq); the
        # rootfs, update.tar and manifest are not made
        return
    # Pseudo-file round trip preserves every original inode's metadata and hardlinks.
    pseudo = out/'root.pseudo'
    run('unsquashfs','-pf',pseudo,sq)
    p = pseudo.read_bytes()
    # Two parts: p, the inode definitions (about 270 KB) every search and edit below works on, and
    # pseudo_data, from the data marker on (about 108 MB of embedded file contents), written back unchanged.
    at = p.index(b'# START OF DATA')
    p, pseudo_data = p[:at], p[at:]
    # mksquashfs takes "/" from the source dir, not the pseudo file; carry stock values over.
    root = need(re.search(rb'^/ D (\d+) (\d+) (\d+) (\d+)$',p,re.M), 'Missing root pseudo inode')
    t,mode,uid,gid = (x.decode() for x in root.groups())
    rootargs = ['-root-time',t,'-root-mode',mode,'-root-uid',uid,'-root-gid',gid]
    # Paths are passed through a shell by mksquashfs F entries; quote them explicitly.
    def inode(p, path): return re.search(rb'^'+re.escape(path)+rb' R (\d+) (\d+) (\d+) (\d+) .+$',p,re.M)
    def swap_inode(p, path, src):
        line = need(inode(p, path), f'Missing {path.decode()} pseudo inode')
        return p[:line.start()]+path+b' F '+b' '.join(line.groups())+b' cat '+shlex.quote(str(src)).encode()+p[line.end():]
    s90play = cat(S90PLAY)
    check(s90play.count(BOOT_HOOK[0]) == 1 and s90play.count(REMOTE_HOOK[0]) == 1, 'Unexpected S90play')
    s90play = s90play.replace(*BOOT_HOOK)
    check(s90play.count(LOG_HOOK[0]) == 1, 'Unexpected demo loop')
    check(s90play.count(WHEEL_LOG_HOOK[0]) == 1, 'Unexpected card path')
    (out/'S90play').write_bytes(s90play.replace(*REMOTE_HOOK).replace(*ADB_HOOK).replace(*LOG_HOOK).replace(*WHEEL_LOG_HOOK) if dev else s90play)
    (out/'config.ini').write_bytes(default_config(cat(DEFAULT_CONFIG)))
    s11logo = cat(S11LOGO)
    check(s11logo.count(LOGO_HOOK[0]) == 1, 'Unexpected S11logo_display_shell')
    (out/'S11logo').write_bytes(s11logo.replace(*LOGO_HOOK))
    bluealsa2 = cat(BLUEALSA2)
    check(bluealsa2.count(BT_KEEPALIVE[0]) == 1, 'Unexpected bluealsa2')
    bluealsa2 = bluealsa2.replace(*BT_KEEPALIVE)
    check(bluealsa2.count(BT_GUEST_SOURCE[0]) == 1, 'Unexpected bluealsa2')
    (out/'bluealsa2').write_bytes(bluealsa2.replace(*BT_GUEST_SOURCE))
    logo_data = logo.read_bytes()
    check(jpeg_size(logo_data) == (320, 375), 'Logo must be 320x375 like the stock splash')
    # Package exactly the validated bytes, even if the input is edited during compression.
    logo = out/'logo.jpg'
    logo.write_bytes(logo_data)
    default_logo = logo_data == (ROOT/'assets/boot-logo.jpg').read_bytes()
    # The boot animation is the default logo's too: a custom --logo shows as a still, as it always has.
    anim = default_logo and {LOGO_ANIM: (ROOT/'assets/boot-anim.q2a').read_bytes(), LOGO_BLACK: (ROOT/'assets/boot-black.jpg').read_bytes()}
    check(not anim or (anim[LOGO_ANIM][:4] == b'Q2AN' and struct.unpack_from('<2H', anim[LOGO_ANIM], 8) == (320, 375)
                       and jpeg_size(anim[LOGO_BLACK]) == (320, 375)), 'Boot animation must be 320x375 like the stock splash')
    q2player, player_added = None, []
    if new_player:
        # q2player at /usr/bin/hciplayer, and the patched hciplayer, which it runs while /mnt/data/q2player is
        # absent, xz-compressed at STOCK_XZ (player/main.c), which unpacks it to /tmp/hciplayer-stock/hciplayer: a
        # directory of its own, so its file and process name stay "hciplayer" for demo's killall. Compressed, it takes
        # 1.27 MB less of the root filesystem, the room q2player and its TLS need (docs/player-plan.md#risks); the
        # check is CRC32, which every xz decoder takes. q2player is linked against this image's own libasound.
        import lzma
        from player import build as build_player
        stock_xz = out/'peq/hciplayer.xz'
        stock_xz.write_bytes(lzma.compress((out/'peq/hciplayer').read_bytes(), format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32,
                                           preset=9 | lzma.PRESET_EXTREME))
        alsa = out/'player-alsa/libasound.so.2.0.0'
        alsa.parent.mkdir(exist_ok=True)
        alsa.write_bytes(cat('usr/lib/libasound.so.2.0.0'))
        q2player = build_player('mips', alsa=alsa)
        stock_dir = need(re.search(rb'^usr/lib D (\d+) (\d+) (\d+) (\d+)$', p, re.M), 'Missing usr/lib pseudo inode')
        hci = need(inode(p, b'usr/bin/hciplayer'), 'Missing usr/bin/hciplayer pseudo inode')
        # p is the definitions alone (pseudo_data, after them, goes back on unchanged): these two follow them
        p = (p + PLAYER_STOCK_DIR + b' D ' + b' '.join(stock_dir.groups()) + b'\n' + PLAYER_STOCK_DIR + b'/hciplayer.xz F ' +
             b' '.join(hci.groups()) + b' cat ' + shlex.quote(str(stock_xz)).encode() + b'\n')
        player_added = [[PLAYER_STOCK_DIR, b'D', *stock_dir.groups()], [PLAYER_STOCK_DIR + b'/hciplayer.xz', b'R', *hci.groups()]]
    # The card apps the firmware carries (BUNDLED_APPS): folders as usr/lib is, the app and its launcher as a
    # stock program is, the rest as a stock data file is
    lib_dir = need(re.search(rb'^usr/lib D (\d+) (\d+) (\d+) (\d+)$', p, re.M), 'Missing usr/lib pseudo inode')
    program, data_file = need(inode(p, HELPER_LIKE.encode()), 'Missing program inode'), need(inode(p, b'etc/hosts'), 'Missing etc/hosts inode')
    bundled = {}
    for d in [BUNDLED_DIR, *(f'{BUNDLED_DIR}/{i}' for i in BUNDLED_APPS)]:
        p += d.encode() + b' D ' + b' '.join(lib_dir.groups()) + b'\n'
        player_added.append([d.encode(), b'D', *lib_dir.groups()])
    for app_id in BUNDLED_APPS:
        files = bundled_app(app_id, apps_dir)
        (out/'q2apps'/app_id).mkdir(parents=True, exist_ok=True)
        for name, (data, executable) in files.items():
            src, path, like = out/'q2apps'/app_id/name, f'{BUNDLED_DIR}/{app_id}/{name}'.encode(), program if executable else data_file
            src.write_bytes(data)
            p += path + b' F ' + b' '.join(like.groups()) + b' cat ' + shlex.quote(str(src)).encode() + b'\n'
            player_added.append([path, b'R', *like.groups()])
        app_version, zip_name, _, digest = BUNDLED_APPS[app_id]
        bundled[app_id] = dict(version=app_version, zip=zip_name, sha256=digest,
                               bytes=sum(len(d) for d, _ in files.values()))
    for path, src in ((b'release/bin/demo', out/'demo'), (b'usr/bin/hciplayer', q2player or out/'peq/hciplayer'),
                      (BLUEALSA.encode(), out/'bluealsa'), (BLUETOOTHD.encode(), out/'bluetoothd'), (WATCHDOG.encode(), out/'watchdog'),
                      (S90PLAY.encode(), out/'S90play'), (S11LOGO.encode(), out/'S11logo'),
                      (BLUEALSA2.encode(), out/'bluealsa2'), (DEFAULT_CONFIG.encode(), out/'config.ini'),
                      (LOGO.encode(), logo)):
        p = swap_inode(p, path, src)
    p = swap_inode(p, SHADOW.encode(), out/'shadow')
    p = swap_inode(p, INITTAB.encode(), out/'inittab')
    p = swap_inode(p, BT_MAIN.encode(), out/'main.conf')
    p = swap_inode(p, THTTPD_CONF.encode(), out/'thttpd.conf')
    aic_notifier = None
    if aic_idle_wait:
        from aic import MODULE_PATH, PATCH_POLICY, STOCK_SHA256, patch_module
        module = patch_module(cat(MODULE_PATH))
        (out/'soc_aic.ko').write_bytes(module)
        p = swap_inode(p, MODULE_PATH.encode(), out/'soc_aic.ko')
        aic_notifier = dict(policy=PATCH_POLICY, path=MODULE_PATH,
                            original_sha256=STOCK_SHA256, sha256=sha(module))
    rotator = None
    if rotator_lazy_clock:
        from rotator import MODULE_PATH, STOCK_SHA256, patch_module
        module = patch_module(cat(MODULE_PATH))
        (out/'soc_rotator.ko').write_bytes(module)
        p = swap_inode(p, MODULE_PATH.encode(), out/'soc_rotator.ko')
        rotator = dict(policy='lazy-first-use-v1', path=MODULE_PATH,
                       original_sha256=STOCK_SHA256, sha256=sha(module))
    pwm = None
    if pwm_idle_clock:
        from pwm import patch_bundle
        files, pwm = patch_bundle(cat, sleep=mcu_idle_sleep)
        for path, data in files.items():
            src = out/pathlib.Path(path).name
            src.write_bytes(data)
            p = swap_inode(p, path.encode(), src)
    # New inodes, each with its stock image's metadata: Home's Coverflow and Shortcut card icons (menu_music's,
    # in their own colours like stock's cards), the Local Music and Spotify rows' icons, drawn in assets/icons/ (stock's
    # 52px style) or else a copy of the stock image named (not the copies iPod pre-sizes for Settings), and q2video.
    xx = 'release/assets/default/raw/images/xx/'
    icons = {n: ('menu_musicdown.png' if n.endswith('down.png') else 'menu_music.png',
                 (ROOT/'assets/icons'/n).read_bytes()) for n in ICONS}
    drawn = ('podcasts', 'audiobooks', 'videos')
    icons.update({f'local_{n}.png': (like, (ROOT/'assets/icons'/f'local_{n}.png').read_bytes() if n in drawn
                                     else cat(xx+like)) for n, like in dict(
        shuffle='playset_playmode.png', scrobble='wifiset_wifi.png', podcasts='netservice_dlna.png',
        audiobooks='playset_foldercover.png', videos='system_display.png', longsongs='system_time.png',
        era='local_genre.png').items()})
    icons['local_games.png'] = ('system_language.png', (ROOT/'assets/icons/local_games.png').read_bytes())
    for n in ('spotify', 'radio', 'app', 'config'):  # Streaming's Spotify, Internet Radio, card app and Configuration rows
        icons[f'stream_{n}.png'] = ('list_tidal.png', (ROOT/'assets/icons'/f'stream_{n}.png').read_bytes())
    icons['stream_airplay.png'] = ('list_tidal.png', cat(xx+'netservice_airplay.png'))  # iPod resizes stock's
    art = (ROOT/'assets/icons/default_album_home.png').read_bytes()
    # Streaming's rows draw them at SET_ICON; Home's placeholder at the art panel's 290px
    from theme import builtins as builtin_themes
    for n in ('spotify', 'radio', 'app', 'airplay', 'config'):
        like, data = icons[f'stream_{n}.png']
        icons[f'stream_{n}.png'] = (like, row_icon(data, sharp=True))
    icons['default_album_home.png'] = ('default_album_big.png', art)
    icons['default_album_flow.png'] = ('default_album_big.png', sized_art(art, 160))
    cgi_root = compile_helper(out, cat, 'cgi_root', 'q2cgi-root', HELPER_LIBS[:1])
    added = list(player_added)
    removed = []
    changed_assets = {}
    from art import theme_images
    drawn = {xx+n+'.png': (xx+'list_tidal.png', d) for n, d in theme_images(builtin_themes()).items()}
    for path, (like, data) in {**{xx+n: (xx+l, d) for n, (l, d) in icons.items()}, **set_pages, **drawn,
                               'etc/init.d/S12usbcharge': ('etc/init.d/S20urandom', (ROOT/'patch/usb_charge.sh').read_bytes()),
                               HELPER: (HELPER_LIKE, compile_helper(out, cat, 'video', 'q2video', HELPER_LIBS)),
                               SCROBBLE_CA: ('etc/hosts', (ROOT/'assets/scrobble-ca.pem').read_bytes()),
                               STOCK_CA: ('etc/hosts', mozilla_ca()),
                               # Wi-Fi transfer's PIN check (q2cgi auth), a CGI stock has no inode for; CGI_MODE below
                               'usr/share/web/cgi-bin/auth.cgi': ('usr/share/web/cgi-bin/list.cgi', cgi_root),
                               Q2CGI: (HELPER_LIKE, (ROOT/'assets/web/q2cgi').read_bytes()),
                               BOOT: (HELPER_LIKE, compile_helper(out, cat, 'boot', 'q2boot', HELPER_LIBS[:1])),
                               SPLASH: (HELPER_LIKE, compile_helper(out, cat, 'splash', 'q2splash', HELPER_LIBS[:1])),
                               **({path: (xx+'list_tidal.png', data) for path, data in anim.items()} if anim else {}),
                               CODA_RC: (HELPER_LIKE, compile_helper(out, cat, CODA_RC_SOURCES, 'coda-rc', CODA_RC_LIBS)),
                               GUEST: (HELPER_LIKE, compile_helper(out, cat, 'q2_guest', 'q2-guest', GUEST_LIBS)),
                               GUEST_DBUS: ('etc/dbus-1/system.d/bluealsa.conf', (ROOT/'assets/dbus/bluealsa-sink.conf').read_bytes()),
                               GUEST_HOTSPOT: (HELPER_LIKE, (ROOT/'patch/q2_hotspot.sh').read_bytes()),
                               **{path: (FONT, data) for path, (data, _) in fonts.items()},
                               **{FONT_DIR + n: ('etc/hosts', (ROOT/'assets/fonts'/n).read_bytes()) for n in FONT_NOTICES},
                               **(adb_files(out, cat) if dev else {}),
                               **({REMOTE: (HELPER_LIKE, compile_helper(out, cat, 'remote', 'q2remote', HELPER_LIBS[:1])),
                                   Q2EQ: (HELPER_LIKE, compile_helper(out, cat, 'q2eq', 'q2eq', HELPER_LIBS[:1]))} if dev else {})}.items():
        stock = need(inode(p, like.encode()), f'Missing stock inode for {path}')
        name = path.rsplit('/', 1)[-1]
        if path not in (HELPER, BOOT, SPLASH, REMOTE, Q2EQ, CODA_RC, GUEST): (out/name).write_bytes(data)  # package the hashed bytes, as the logo
        if inode(p, path.encode()):  # stock has a file of that name (V1.36's own menu_coverflow*.png): ours takes its inode
            changed_assets[path] = dict(original_sha256=sha(cat(path)), sha256=sha(data))
            p = swap_inode(p, path.encode(), out/name)
            continue
        path = path.encode()
        entry = path+b' F '+b' '.join(stock.groups())+b' cat '+shlex.quote(str(out/name)).encode()+b'\n'
        p += entry  # the definitions' end, before the embedded data
        added.append([path, b'R', *stock.groups()])
    for path in STOCK_EQ + STOCK_PAGES + OBSOLETE:
        line = need(re.search(rb'^'+re.escape(path.encode())+rb' [RS] .+\n', p, re.M), f'Missing stock inode {path}')
        removed.append(line.group().split()[:6])
        p = p[:line.start()]+p[line.end():]
    for path, fix, name in ((TAG_C, patch_m4b_cover, 'libtag_c.so.2.0.1'), (FONT, font_quotes, 'default.ttf'),
                            *((p, lambda d, p=p: patch_web(p, d), p.rsplit('/', 1)[-1]) for p in WEB)):
        original = cat(path)
        data = fix(original)
        (out/name).write_bytes(data)
        p = swap_inode(p, path.encode(), out/name)
        changed_assets[path] = dict(original_sha256=sha(original), sha256=sha(data))
    # Wi-Fi transfer file manager (docs/internals.md#wi-fi-transfer): the stock CGIs are CGIC programs
    # that build shell commands from the request (mkdir "%s", rm -rf "%s", mv "%s" "%s") and pass the
    # path/filename straight to system(), so a name with $(...) or a backtick runs as root, and they
    # read paths outside the card. q2cgi (Q2CGI, added above) replaces all five, doing each operation
    # with argv (never a shell command built from input) and validating every path stays under
    # /mnt/mmc. Every cgi-bin/*.cgi inode is cgi_root, which runs it as root for thttpd's www-data, so
    # both /name and /cgi-bin/name.cgi reach it with no bypass; so is cgi-bin/auth.cgi (added above),
    # the PIN check every other route requires.
    for n in CGIS[:-1]:
        path = 'usr/share/web/cgi-bin/%s.cgi' % n
        changed_assets[path] = dict(original_sha256=sha(cat(path)), sha256=sha(cgi_root))
        p = swap_inode(p, path.encode(), out/'q2cgi-root')
    for n in CGIS:
        path = b'usr/share/web/cgi-bin/%s.cgi' % n.encode()
        line = need(re.search(rb'^' + re.escape(path) + rb' F (\d+) ' + CGI_MODE[0] + rb' 0 0 ', p, re.M), f'Unexpected {path.decode()} mode')
        p = p[:line.start()] + path + b' F ' + line[1] + b' ' + CGI_MODE[1] + b' 0 0 ' + p[line.end():]
        stock = [path, b'R', line[1], CGI_MODE[0], b'0', b'0']  # auth.cgi's is in added, the others in stock's image
        if stock in added: added.remove(stock)
        else: removed.append(stock)
        added.append([path, b'R', line[1], CGI_MODE[1], b'0', b'0'])
    patched_ui = list(UI_ASSETS)
    check(chinese, 'No Chinese UI text found')
    assets = ['ui/'+rel for rel in patched_ui + sorted(chinese - set(patched_ui))] + ['strings/en_US.bin']
    assets += ['styles/'+rel for rel in AUDIT['styles']]
    # images changed in place, so each keeps its inode metadata: settings icons pre-sized to the rows'
    # SET_ICON and the art placeholders
    assets += ['images/'+name for name in SETTINGS_ICONS | QUIET_ICONS | STATUS_ICONS | ART_PLACEHOLDERS]
    for rel in assets:
        kind, name = rel.split('/', 1)
        path = 'release/assets/default/raw/' + ('images/xx/'+name if kind == 'images' else rel)
        original = cat(path)
        data = (relabel(original) if kind == 'strings' else
                patch_style(original, AUDIT['styles'][name]) if kind == 'styles' else
                (sized_art(art, ART_PLACEHOLDERS[name]) if name in ART_PLACEHOLDERS else
                 status_icon(name, original) if name in STATUS_ICONS else quiet_icon(name, original) if name in QUIET_ICONS else settings_icon(name, original)) if kind == 'images' else
                patch_asset(name, original) if name in patched_ui else original)
        if kind == 'ui' and name in chinese:
            data = english_ui(name, data)
        target = out/rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        p = swap_inode(p, path.encode(), target)
        changed_assets[path] = dict(original_sha256=sha(original), sha256=sha(data))
    # Shanling's outdated OpenSSL, curl, zlib and the rest, rebuilt from upstream (docs/third-party.md)
    from thirdparty import install as install_thirdparty
    p, thirdparty = install_thirdparty(p, out, cat, swap_inode, sq)
    pseudo.write_bytes(p + pseudo_data)
    (out/'empty').mkdir(exist_ok=True)
    newsq = out/'rootfs.squashfs'
    epoch = struct.unpack_from('<I',sq.read_bytes(),8)[0]
    # On every core: squashfs-tools 4.6.1 wrote the same image with 1, 2, 3, 5, 8 and 16 threads.
    run('mksquashfs',out/'empty',newsq,'-pf',pseudo,'-noappend','-comp','lzo',
        '-b','131072','-Xcompression-level','9','-tailends','-mkfs-time',epoch,*rootargs,'-no-progress')
    # All inodes, including demo and the logo, keep name/type/mtime/mode/uid/gid (sizes/offsets shift).
    def inodes(image):
        text = subprocess.check_output(['unsquashfs','-pf','-',str(image)]).split(b'\n# START OF DATA')[0]
        return sorted(l.split()[:6] for l in text.splitlines() if l and not l.startswith(b'#'))
    check(inodes(newsq) == sorted([i for i in inodes(sq) if i not in removed]+added),
          'Repacked rootfs metadata differs from stock')
    blobs['recovery-update/rootfs.squashfs'] = newsq.read_bytes()
    rootfs_fits(len(blobs['recovery-update/rootfs.squashfs']), new_player)
    blobs['firmware_v20.info'] = (f'Shanling Q2\n{version}\n'+''.join(
        hashlib.md5(blobs[n]).hexdigest()+'  '+n+'\n' for n in [
            'recovery-update/xImage','recovery-update/rootfs.squashfs'])).encode()
    with tarfile.open(out/'update.tar','w',format=tarfile.GNU_FORMAT) as t:
        for m in meta:
            data = blobs.get(m.name)
            if data is not None: m.size=len(data)
            t.addfile(m,io.BytesIO(data) if data is not None else None)
    manifest = dict(input_zip_sha256=ZIP_SHA, stock_demo_sha256=DEMO_SHA, source_sha256=source,
        demo_sha256=sha(patched), patch_sha256=sha(payload), update_sha256=sha((out/'update.tar').read_bytes()),
        rootfs_sha256=sha(newsq.read_bytes()), kernel_sha256=sha(blobs['recovery-update/xImage']),
        **({'aic_notifier': aic_notifier} if aic_notifier else {}),
        patch_bytes=len(payload), ring_step_pixels=RING_STEP,
        audio_extension_calls=[dict(address=hex(a), original='0x0320f809', replacement=hex(jal(ps['ringnav_audio_extension']))) for a in AUDIO_EXTENSION_CALLS],
        version=version, dev=dev, update_repo=repo, signing_key=key.hex(), peq=audio, bluealsa_sha256=sha(bluealsa), bluetoothd_sha256=sha(bluetoothd), q2video_sha256=sha((out/'q2video').read_bytes()), q2boot_sha256=sha((out/'q2boot').read_bytes()), q2splash_sha256=sha((out/'q2splash').read_bytes()), coda_rc_sha256=sha((out/'coda-rc').read_bytes()), coda_rc_bytes=len((out/'coda-rc').read_bytes()), q2_guest_sha256=sha((out/'q2-guest').read_bytes()), **({'q2remote_sha256': sha((out/'q2remote').read_bytes()), 'q2eq_sha256': sha((out/'q2eq').read_bytes())} if dev else {}), compact_code=code_changes, changed_assets=changed_assets, thirdparty=thirdparty, logo_sha256=sha(logo_data),
        **({'rotator_clock': rotator} if rotator else {}),
        **({'adbd': adbd_record(out)} if dev else {}),
        **({'q2player_sha256': sha(q2player.read_bytes()), 'rootfs_bytes': size} if q2player else {}),
        bundled_apps=bundled, rootfs_squashfs_bytes=len(blobs['recovery-update/rootfs.squashfs']),
        **({'pwm_clock': pwm} if pwm else {}),
        patch_symbols={n:hex(v) for n,v in ps.items() if n.startswith('stock_')},
        tools={t:run(t,'--version').splitlines()[0] for t in ['clang','ld.lld','llvm-objcopy']} |
              {'imagemagick': imagemagick('-version', data=b'').decode().splitlines()[0]})
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({k:manifest[k] for k in ['update_sha256','patch_bytes','version']},indent=2))

if __name__ == '__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('zip',type=pathlib.Path)
    ap.add_argument('--out',type=pathlib.Path,default=ROOT/'build')
    ap.add_argument('--logo',type=pathlib.Path,default=ROOT/'assets/boot-logo.jpg',
                    help='320x375 JPEG boot splash (default: assets/boot-logo.jpg)')
    ap.add_argument('--dev', action='store_true',
                    help='development build: tag from the next build number (V001i, V002i...); never a release input')
    ap.add_argument('--mcu-idle-sleep', action='store_true',
                    help='experimental: sleep parked MCU with explicit IRQ wake (requires --dev --pwm-idle-clock)')
    ap.add_argument('--flac-experiments', default='sync,lpc,output', metavar='sync,lpc,output',
                    help='FLAC fast paths (comma-separated subset; empty for stock FLAC)')
    ap.add_argument('--update-repo', default=REPO, metavar='OWNER/NAME',
                    help="GitHub repo whose latest release Software Update fetches (default: %(default)s), for a fork's test builds")
    ap.add_argument('--signing-key', type=pathlib.Path, metavar='PUB.PEM',
                    help='Ed25519 public key that updates and the app list must be signed with, in place of assets/signing/q2-coda-release.pub (requires --dev)')
    ap.add_argument('--aic-idle-wait', action='store_true',
                    help='experimental: sleep the stopped PCM notifier until start or teardown (requires --dev)')
    ap.add_argument('--rotator-lazy-clock', action='store_true',
                    help='experimental: defer the rotator clock until its first conversion (requires --dev)')
    ap.add_argument('--new-player', action='store_true',
                    help='install q2player as /usr/bin/hciplayer, falling back to hciplayer unless /mnt/data/q2player exists (requires --dev)')
    ap.add_argument('--apps', type=pathlib.Path, default=APPS_DIR, metavar='DIR',
                    help=f'where the pinned zips of the card apps the firmware carries are (default {APPS_DIR})')
    ap.add_argument('--pwm-idle-clock', action='store_true',
                    help='experimental: cooperative Linux/MCU PWM clock gating (requires --dev)')
    a=ap.parse_args()
    try:
        build(a.zip,a.out.resolve(),a.logo,a.dev,a.update_repo,a.aic_idle_wait,a.rotator_lazy_clock,a.pwm_idle_clock,a.mcu_idle_sleep,a.flac_experiments,a.signing_key,a.new_player,a.apps)
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        ap.error(str(exc))
