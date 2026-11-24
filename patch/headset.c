/* Headset I/O stays on one bounded worker; only the UI loop controls players. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

extern int stock_pause_trampoline(void);
extern int ringnav_bt_resume(void);
/* Stock drops an unlisted device's Connected (3) while a link thread waits; adopt it when the wait
 * is for BTLINKMAC (0xa4c0d8), its Connect running (5); checkBluetoothStatus then judges it last.
 * ponytail: the target's Connect still runs; powered on within its page timeout, both may link. */
static void headset_bt_status(int event, char *address, char *name, void *data) {
    char target[32] = { 0 };
    int status = 0;
    if (event == 3 && *(const volatile int *)bt_connectstatus == 1 && !getBtLinkStatus(address) &&
        checkPairDev(address)) {
        getBtLinkItem(getBtLinkSize() - 1, &status, target);
        if (status == 5 && !strcmp(target, (const char *)0xa4c0d8)) {
            addBtLinkItem(address, 3);
            updateBtLinkStatus(4, target);
            return;
        }
    }
    ((void (*)(int, char *, char *, void *))0x4fc180)(event, address, name, data);
}
/* Bluetooth's start (boot, the switch, a relaunch) and a connect headset_bt_usb is starting */
#define BT_USB_QUIET_MS 20000 /* the stack-up auto-link and its 15 s wait, with room */
static struct {
    unsigned on_at;
    int usb;
} hl __attribute__((section(".scratch")));
void bt_name_apply(void); /* bt_name.c */
/* openBluetooth's registration (BT_STATUS), at each Bluetooth on: also the Q2's name to BlueZ. */
void headset_status_register(void) {
    hl.on_at = time_now_ms() | 1;
    btctl_device_status_callback(headset_bt_status, 0);
    bt_name_apply();
    guest_bt_on();
}
/* The connected (3) link, its MAC in mac[32]. */
static int bt_connected(char *mac) {
    int status = 0;
    for (int i = getBtLinkSize(); status != 3 && i-- > 0;) getBtLinkItem(i, &status, mac);
    return status == 3;
}
void bt_trace_why(const char *why, const char *mac); /* bt_trace.c */
void bt_trace_autolink(const char *why, const char *target, const char *connected, int go);
const char *usb_car_name(void);                           /* usb_car.c */
int bt_dev_find(const char *mac);                         /* bt_ui.c */
const char *bt_dev_name(int i);
/* Auto-link would append its target after a connected incoming link; its failure clears both. Each
 * decision is logged with its target and why (bt_trace_autolink), and a connect it goes ahead with
 * as auto, or usb from headset_bt_usb (docs/internals.md#bluetooth-connection-trace). */
int headset_bt_autolink(char *address) {
    char item[32];
    int linked = bt_connected(item);
    const char *why = hl.usb ? "usb" : "auto", *car = usb_car_name(), *name = bt_dev_name(bt_dev_find(address));
    if (!linked && car && name && !tk_strcmp(car, name)) { /* the car on the cable plays the Q2 over USB already */
        bt_trace_autolink(why, address, 0, -1);
        return 0;
    }
    int go = !linked && checkPairDev(address);
    bt_trace_autolink(why, address, linked ? item : 0, go);
    if (go) bt_trace_why(why, address);
    return go;
}
/* A cable in (usb_ui.c usb_ui_status): stock's auto-link once more, as at Bluetooth on, since a car
 * that ended the link at its switch-off is paged by nothing else
 * (docs/internals.md#bluetooth-on-usb-power). Not while Bluetooth is off, its stack not up or
 * within BT_USB_QUIET_MS of its start (where the stack-up pass auto-links), with a link thread
 * running or no saved device; autoLinkBluetooth checks these again, and headset_bt_autolink skips
 * while a listed device is connected. */
void headset_bt_usb(void) {
    if (!g_bluetoothflag || I(bt_initstatus, 0) || I(bt_connectstatus, 0) || !hl.on_at ||
        time_now_ms() - hl.on_at < BT_USB_QUIET_MS || !*(const char *)0xa4c0d8)
        return;
    hl.usb = 1;
    autoLinkBluetooth();
    hl.usb = 0;
}
void bt_pair_register(void *stock, void *unused); /* bt_pair.c */
/* The control thread can discover a boot reconnect before openBluetooth powers the radio. */
int headset_bt_init(void *thread) {
    initBluetoothParam(); /* Lists must exist before callbacks; the later reset would erase links. */
    bt_pair_register((void *)0x4f9d20, 0); /* bt_pair.c's callback in place of stock's */
    headset_status_register();
    btctl_set_a2dp_callback((void *)btrecvA2dpFunc, (void *)0x01020304);
    return btctl_create_daemon_thread(thread);
}
static struct {
    volatile unsigned generation, running, head, tail, report;
    volatile int keys[16];
    unsigned worker_generation, checked, epoch, paused_epoch, paused_at, recent_at,
        recent_owner, recent_session;
    unsigned last_at, session, linked_at, eq_checked;
    int linked, known, ears, want, paused, owner, automatic, last_key;
    unsigned char address[14];
    int airpods;
} hs __attribute__((section(".scratch")));

void headset_manual(void) {
    if (!hs.automatic) {
        ++hs.epoch;
        hs.recent_owner = 0;
    }
}
/* player_play_pause. Headphones required with none to play to: nothing starts, as stock's own
 * Play key (playpause_quick_click, 0x4f2fc8), which toasts so, and a Play while USB audio connects
 * is kept for it (usb_otg.c usb_otg_forbid_resume); a pause always goes through.
 * ponytail: a card app's own player and a card app's song through player_start aren't asked
 * (docs/internals.md#no-playback-without-an-output); gate addon.c's starts if that matters. */
int ringnav_play_pause(void) {
    headset_manual();
    int status = mclGetPlayStatus();
    if (status != 2 && usb_otg_forbid_resume()) return 0;
    if (status == 1) press_stamp(); /* Play from a stop starts a track */
    return stock_pause_trampoline();
}
/* Who has the playback keys: 5 Radio (radio_tuner.c, its page open or shut), 4 a video, 3 Internet
 * Radio, 2 Spotify, 1 the Library. */
int media_owner(void) { return tuner_state() ? 5 : video_status() ? 4 : radio_status() ? 3 : spot_status() ? 2 : 1; }
/* What owns playback says, as mclGetPlayStatus does: 2 playing, 3 paused, else stopped or none. */
int media_status(void) {
    int t = tuner_state(); /* Radio's: 1 playing, 2 paused */
    if (t) return t + 1;
    return video_status() ?: radio_status() ?: spot_status() ?: mclGetPlayStatus();
}
int media_playing(void) { return media_status() == 2; }
static unsigned media_session(void) {
    int owner = media_owner();
    if (owner == 5) return 0x7ad10; /* Radio: one session for as long as it plays */
    if (owner == 4) return video_session();
    if (owner == 3) return radio_session();
    if (owner == 2) return spot_session();
    /* Both local and stock Tidal queues: changing the queue or its position ends this session. */
    return *(volatile unsigned *)MCL_POS ^ *(volatile unsigned *)MCL_TYPE ^
           *(volatile unsigned *)mcl_pdeqplaylist;
}
void media_set_playing(int playing) {
    if (playing && addon_game_audio()) return; /* a card game with the sound: nothing of stock's
                                                  starts over its PCM; media_key too */
    hs.automatic = 1;
    int owner = media_owner();
    if (owner == 5)
        tuner_set_playing(playing);
    else if (owner == 4)
        video_set_playing(playing);
    else if (owner == 3)
        radio_set_playing(playing);
    else if (owner == 2)
        spot_set_playing(playing);
    else if (media_playing() != playing)
        player_play_pause();
    hs.automatic = 0;
}
/* The streaming page on top, which owns the playback keys: 1 Spotify, 2 radio, 3 Tidal; 0 none. */
static int focus(void) {
    void *top = window_manager_get_top_window(window_manager());
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    return !tk_strcmp(name, "spotify_page") || !tk_strcmp(name, "spotbrowse_page") ? 1
           : !tk_strcmp(name, "radio_page") || !tk_strcmp(name, "radionp_page") ? 2
           : tk_str_start_with(name, "tidal_")                                  ? 3
                                                                                : 0;
}
int media_focused(void) { return focus() != 0; }
void media_key(unsigned key) {
    headset_manual();
    if (addon_game_audio()) { /* a card game with the sound: stock's player must not retake it;
                                 Play/Pause still pauses what plays (a Spotify a phone resumed) */
        if (key == KEY_PLAY && media_playing()) media_set_playing(0);
        return;
    }
    int f = focus(); /* a focused Spotify or radio that isn't running leaves them to the owner */
    if ((f == 1 && spot_media(key)) || (f == 2 && radio_media(key)))
        return;
    if (f != 3 && tuner_state())
        tuner_media(key); /* Radio, its page shut or not, until the Library is chosen again */
    else if (f != 3 && video_status())
        video_key(key);
    else if (f != 3 && radio_status())
        radio_media(key);
    else if (f != 3 && spot_status())
        spot_media(key);
    else if (key == KEY_PLAY)
        player_play_pause();
    else if (check_forbid_play_no_headset(0)) /* Headphones required: as stock's skip keys */
        ;
    else if (key == KEY_FWD_BTN)
        player_next_music();
    else if (key == KEY_BACK_BTN)
        player_prev_music();
}

/* Only complete, valid ear packets are published, in one word. No battery state. */
int ringnav_aap_parse(const unsigned char *p, int n) {
    if (!p || n != 8 || p[0] != 4 || p[1] || p[2] != 4 || p[3] || p[4] != 6 || p[5] || p[6] > 2 ||
        p[7] > 2)
        return 0;
    hs.report = 4u | (p[6] == 0) | ((p[7] == 0) << 1);
    return 2;
}
static int wait_fd(int fd, int writing) {
    if (fd < 0 || fd >= 1024) return -1;
    unsigned set[32] = { 0 };
    long tv[2] = { 0, 200000 };
    set[fd / 32] = 1u << (fd % 32);
    return select(fd + 1, writing ? 0 : set, writing ? set : 0, 0, tv);
}
/* Up to 1 s for either fd (-1 for none) to read: bit 1 a, bit 2 b, -1 an error. */
static int wait_either(int a, int b) {
    if (a >= 1024 || b >= 1024) return -1;
    unsigned set[32] = { 0 };
    long tv[2] = { 1, 0 };
    if (a >= 0) set[a / 32] |= 1u << (a % 32);
    if (b >= 0) set[b / 32] |= 1u << (b % 32);
    int n = select((a > b ? a : b) + 1, set, 0, 0, tv);
    if (n <= 0) return n;
    return (a >= 0 && set[a / 32] >> (a % 32) & 1) | (b >= 0 && set[b / 32] >> (b % 32) & 1) << 1;
}
static int avrcp_open(void) {
    char path[32], name[80]; /* uinput's longest name, its NUL included (UINPUT_MAX_NAME_SIZE) */
    for (int i = 0; i < 32; ++i) {
        snprintf(path, sizeof path, "/dev/input/event%d", i);
        int fd = open(path, 0x80080); /* O_RDONLY | O_NONBLOCK | O_CLOEXEC, MIPS */
        if (fd < 0) continue;
        memset(name, 0, sizeof name);
        /* EVIOCGNAME(sizeof name): BlueZ keeps " (AVRCP)" at the end of a long name, which a
         * 64-byte read cut off, so a headset named past 55 characters never had its keys read */
        int got = ioctl(fd, 0x40504506u, name);
        name[sizeof name - 1] = 0;
        if (got > 0 && strstr(name, "(AVRCP)")) return fd;
        close(fd);
    }
    return -1;
}
static const unsigned char hello[16] = { 0, 0, 4, 0, 1, 0, 2, 0 };
static const unsigned char flags[14] = { 4, 0, 4, 0, 0x4d, 0, 0xd7 };
static const unsigned char notify[10] = { 4, 0, 4, 0, 0xf, 0, 255, 255, 255, 255 };
static int aap_open(const unsigned char *address, unsigned generation) {
    int aap = -1;
    if (hs.airpods && generation == hs.generation) {
        aap = socket(31, 5, 0);
        if (aap >= 0) {
            if (fcntl(aap, 4, 0x80) < 0) {
                close(aap);
                return -1;
            } /* F_SETFL, O_NONBLOCK: connect and send never block */
            int ready = connect(aap, address, 14) == 0;
            for (int i = 0; !ready && i < 5 && generation == hs.generation; ++i)
                if (wait_fd(aap, 1) > 0) {
                    int error = -1;
                    unsigned len = sizeof error;
                    ready = !getsockopt(aap, 0xffff, 0x1007, &error, &len) &&
                            !error; /* SOL_SOCKET, SO_ERROR */
                    break;
                }
            if (!ready || send(aap, hello, sizeof hello, 0x40) != sizeof hello ||
                send(aap, flags, sizeof flags, 0x40) != sizeof flags ||
                send(aap, notify, sizeof notify, 0x40) != sizeof notify)
                close(aap), aap = -1;
        }
    }
    return aap;
}
static void *headset_worker(void *arg) {
    unsigned generation = (unsigned)(long)arg;
    int aap = -1, input = -1, tries = 0;
    unsigned char address[14];
    memcpy(address, hs.address, sizeof address);
    while (generation == hs.generation) {
        /* A pass is up to 1 s. Retry throughout the connection, including late-created and
         * replaced uinput devices: each second for the first 10, then every 2 s; AACP every 5. */
        if (aap < 0 && !(tries % 5)) aap = aap_open(address, generation);
        if (input < 0 && (tries < 10 || !(tries % 2))) input = avrcp_open();
        if (aap < 0 && input < 0) {
            usleep(1000000); /* a stale worker exits at the loop head; headset_poll ignores it */
            ++tries;
            continue;
        }
        int ready = wait_either(aap, input);
        if (ready < 0) { /* which one: a closed fd shows as readable, so take each alone */
            if (aap >= 0 && wait_fd(aap, 0) < 0) close(aap), aap = -1;
            if (input >= 0 && wait_fd(input, 0) < 0) close(input), input = -1;
            ++tries;
            continue;
        }
        if (ready & 1) {
            unsigned char packet[256];
            int n = recv(aap, packet, sizeof packet, 0x40);
            if (n <= 0) close(aap), aap = -1;
            /* Drain the queue: AirPods stream other reports too, and one packet a pass fell
             * behind, delaying ear reports. */
            for (int i = 0; n > 0 && i < 64 && generation == hs.generation; ++i) {
                ringnav_aap_parse(packet, n);
                n = recv(aap, packet, sizeof packet, 0x40);
            }
        }
        if (ready & 2) {
            struct {
                unsigned sec, usec;
                unsigned short type, code;
                int value;
            } ev[8];
            int n = read(input, ev, sizeof ev);
            if (n <= 0)
                close(input), input = -1;
            else
                for (int i = 0; i < n / (int)sizeof ev[0] && generation == hs.generation; ++i)
                    if (ev[i].type == 1 && ev[i].value == 1 && hs.head - hs.tail < 16) {
                        hs.keys[hs.head % 16] = ev[i].code;
                        __sync_synchronize();
                        ++hs.head;
                        loop_wake(); /* headset_poll takes it on the pass this starts */
                    }
        }
        ++tries;
    }
    if (input >= 0) close(input);
    if (aap >= 0) close(aap);
    __sync_synchronize();
    hs.running = 0;
    return 0;
}
static void headset_start(void) {
    char mac[64] = { 0 };
    int n = getBtLinkSize(), status = 0;
    if (n > 0) getBtLinkItem(n - 1, &status, mac);
    hs.airpods = 0;
    void *pairs = *(void *volatile *)0xa4c080; /* stock pdeq_btpairlist */
    for (unsigned i = 0; pairs && i < deque_size(pairs) && i < 64; ++i) {
        const char *p = deque_at(pairs, i);
        if (p && !strcmp(p + 4, mac) && strstr(p + 0x24, "AirPods")) hs.airpods = 1;
    }
    memset(hs.address, 0, sizeof hs.address);
    hs.address[0] = 31;
    hs.address[2] = 1;
    hs.address[3] = 16;
    if (strlen(mac) != 17)
        hs.airpods = 0;
    else
        for (int i = 0; i < 6; ++i) {
            int hi = hex(mac[3 * i]), lo = hex(mac[3 * i + 1]);
            if (hi < 0 || lo < 0 || (i < 5 && mac[3 * i + 2] != ':')) hs.airpods = 0;
            hs.address[9 - i] = (unsigned char)(hi * 16 + lo);
        }
    hs.report = hs.head = hs.tail = 0;
    hs.known = hs.paused = 0;
    unsigned long tid;
    hs.worker_generation = hs.generation;
    hs.running = 1;
    if (pthread_create(&tid, 0, headset_worker, (void *)(long)hs.generation))
        hs.running = 0;
    else
        pthread_detach(tid);
}

/* Counts, not a bud mask: the report's bytes are the primary and secondary bud, which swap when
 * the primary leaves an ear, so a mask can miss the returning bud (one worn AirPod) or see a swap
 * as a removal. A worn bud coming out pauses, as on iOS; as many buds back as were worn resume. */
void headset_ears(unsigned report) {
    unsigned now = time_now_ms();
    int owner = media_owner();
    unsigned session = media_session();
    if (hs.paused && (hs.epoch != hs.paused_epoch || owner != hs.owner || session != hs.session ||
                      (media_playing() && now - hs.paused_at > 1500)))
        hs.paused = 0;
    if (report & 4) {
        int ears = report & 3, in = (ears & 1) + (ears >> 1), was = (hs.ears & 1) + (hs.ears >> 1);
        if (hs.known && in < was &&
            (media_playing() || (hs.recent_owner == (unsigned)owner &&
                                 hs.recent_session == session && now - hs.recent_at < 1500))) {
            if (!hs.paused) hs.want = was;
            hs.owner = owner;
            hs.session = session;
            hs.paused = 1;
            hs.paused_epoch = hs.epoch;
            hs.paused_at = now;
            if (media_playing()) media_set_playing(0);
        } else if (hs.known && hs.paused && in >= hs.want) {
            media_set_playing(1);
            hs.paused = 0;
        }
        hs.known = 1;
        hs.ears = ears;
    }
    if (hs.known && !hs.ears && media_playing()) {
        media_set_playing(0);
        if (!hs.paused) hs.want = 0; /* first report never grants auto-resume */
    }
}
/* Resume on connect off (navigation.c, the default): AirPods send AVRCP PLAY as their link comes
 * up. The worker finds the AVRCP input within about a second of the link (retried every fifth
 * 200 ms pass), and the headset's PLAY follows A2DP setup by a second or two, so a start within
 * CONNECT_HOLD_MS of the link is dropped; a deliberate press after that plays. */
#define CONNECT_HOLD_MS 5000
static int connect_hold(unsigned now) {
    return hs.linked && now - hs.linked_at < CONNECT_HOLD_MS && !ringnav_bt_resume();
}
extern void ulog(char level, const char *src, const char *msg); /* ulog.c */
/* Each key a Bluetooth peer sends, in the unified log with its address and what played: the drive
 * of 2026-11-22 paused twice with the car on A2DP and nothing on the Q2 said who asked. */
static void avrcp_say(int code) {
    const char *what = code == 163                 ? "next"
                       : code == 165               ? "previous"
                       : code == 164               ? "play/pause"
                       : code == 200 || code == 207 ? "play"
                       : code == 119 || code == 201 ? "pause"
                       : code == 166               ? "stop"
                                                   : "key";
    const unsigned char *a = hs.address;
    char line[80];
    snprintf(line, sizeof line, "avrcp %s (%d) from %02X:%02X:%02X:%02X:%02X:%02X, the player %d", what, code,
             a[9], a[8], a[7], a[6], a[5], a[4], media_status());
    ulog('I', "bt", line);
}
/* A pause the headset sent (its key, or the pause a multipoint headset sends the Q2 as it moves to another
 * source: AirPods' automatic switching): q2player is asked to close its Bluetooth PCM at once rather than
 * BT_PAUSE_MS on, so bluealsa's keep-alive lets the A2DP stream go and the next Play starts it afresh
 * (docs/internals.md#bluetooth-pause). An ear's pause (headset_ears) keeps the PCM as any pause does. Stock's
 * player ignores the command. Never waits on the player (MSG_DONTWAIT | MSG_NOSIGNAL). */
static void headset_release(void) {
    int fd = *(volatile int *)MCL_FD;
    static const char m[] = "{mcl-btrelease\\null}";
    if (fd != -1) send(fd, m, sizeof m - 1, 0x4040);
}
void headset_command(int code) {
    avrcp_say(code);
    unsigned now = time_now_ms();
    if (hs.last_key == code && now - hs.last_at < 150) return;
    hs.last_key = code;
    hs.last_at = now;
    if (guest_key(code)) return; /* a guest plays: its phone's (docs/internals.md#guest-play) */
    if (code == 163 || code == 165)
        media_key(code == 163 ? KEY_FWD_BTN : KEY_BACK_BTN);
    else if (code == 164) {
        if (hs.paused && now - hs.paused_at < 1500) return;
        if (connect_hold(now) && !media_playing()) return;
        media_key(KEY_PLAY);
    } else if (code == 119 || code == 201 || code == 166) {
        if (hs.paused && now - hs.paused_at < 1500) return;
        headset_manual();
        if (media_playing()) {
            hs.recent_at = now;
            hs.recent_owner = media_owner();
            hs.recent_session = media_session();
            media_set_playing(0);
            if (hs.recent_owner == 1 && mclGetOutputWay() == 1) headset_release();
        }
    } else if (code == 200 || code == 207) {
        if (hs.paused || connect_hold(now)) return; /* insertion is governed by the ear count */
        headset_manual();
        if (!hs.known || hs.ears) media_set_playing(1);
    }
}
/* EQ per Output's name for what plays (stock's config_bal_output and config_po_output: the jack last
 * plugged in; unplugging 4.4 mm leaves output 0, so a jack must be in to count). None, nothing to
 * hear: the EQ stays with its output until another takes over. */
static const char *eq_output_key(char *mac) {
    int way = mclGetOutputWay();
    if (way == 2) return "usb";
    if (way == 1) return bt_connected(mac) && strlen(mac) == 17 ? mac : 0;
    return g_headset_output == 1 && g_bal_status  ? "4.4mm"
           : g_headset_output == 0 && g_po_status ? "3.5mm"
                                                  : 0;
}
void headset_eq_poll(void) {
    unsigned now = time_now_ms();
    if (now - hs.eq_checked < 500) return;
    hs.eq_checked = now;
    if (ringnav_eq_output()) {
        char mac[32] = { 0 };
        const char *key = eq_output_key(mac);
        if (key) eq_output(key);
    }
#if Q2POD_DEV
    q2eq_poll(); /* after the swap, so a request finds the output playing (docs/adb.md#q2eq) */
    q2update_poll(); /* an update over adb (docs/adb.md#installing-an-update-over-adb) */
#endif
}
void headset_poll(void) {
    guest_poll();
    unsigned now = time_now_ms();
    if (now - hs.checked < 100) return;
    hs.checked = now;
    int on = g_bluetoothflag && bt_linkstatus && !bt__recv_pageflag;
    if (on != hs.linked) {
        ++hs.generation;
        headset_volume_reset();
        hs.known = hs.paused = hs.recent_owner = 0;
        hs.linked = on;
        hs.linked_at = now;
        if (!on && media_playing()) media_set_playing(0);
    }
    if (!on) return;
    if (!hs.running) headset_start();
    if (hs.worker_generation != hs.generation) return;
    while (hs.tail != hs.head) {
        __sync_synchronize();
        int code = hs.keys[hs.tail % 16];
        ++hs.tail;
        headset_command(code);
    }
    headset_ears(hs.report);
}
