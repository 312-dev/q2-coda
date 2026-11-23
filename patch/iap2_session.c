/* iAP2's control session (docs/iphone-mode.md#iap2): the conversation with the car, as an iPhone holds it. The Q2 asks
 * for the car's certificate, challenges it, accepts whatever answers (the Q2 verifies nothing and holds no key), asks
 * it to identify, accepts that, then sends what an iPhone sends next; then power, CarPlay's availability, the car's
 * USB audio and its rate, and each message the car sends handed to what answers it (iap2_control_receive). Part of
 * the PodBox port (iap2.h). */
#include "q2_iap_sys.h"
#include "iap2.h"
#include "iap2_names.h"
#include "iap2_hid.h"
#include "iap2_msg.h"
#include "iap2_xfer.h"
#include "iap2_library.h"
#include "iap2_nowplaying.h"
#include "iap2_session.h"

static u16 car_receives[96];
static unsigned car_receives_n;
int identified;
static int bt_component = -1;
int now_playing;
int ever_identified; /* this cable: identified kept after the link is lost, for the summary */
long auth_due, ident_due, availability_due; /* 0: not due */
long np_at;
static unsigned car_current;
static u32 rnd;
u32 media_mask, playback_mask;
int np_sent;
int audio_on;
char car_name[64]; /* the car's identification name, for the status file (q2_iap.c state_write) */

static void set_audio(int on) {
    if (on == audio_on) return;
    audio_on = on;
    H->log(on ? "audio: on (the player plays into the car)" : "audio: off");
    if (H->audio) H->audio(on);
}

static long due_in(long ms) {
    long d = H->now() + ms;
    return d ? d : 1;
}

void iap2_control_reset(void) {
    if (H) set_audio(0);
    art_going = 0, art_id = 0, n_controls = 0, held = 0, scanning = 0;
    lib_reset();
    identified = now_playing = 0;
    auth_due = ident_due = availability_due = 0;
    car_receives_n = 0, car_current = 0, bt_component = -1;
    np_sent = 0;
}

static int car_takes(u16 id) {
    for (unsigned i = 0; i < car_receives_n; ++i)
        if (car_receives[i] == id) return 1;
    return 0;
}

void iap2_control_link_up(void) { send_empty(0xAA00); }

/* A 20-byte challenge to an MFi 2.0 certificate (about 900 bytes), 32 to a 3.0 one (about 600), as PodBox sends.
 * Nothing checks the answer. */
static void send_challenge(unsigned cert_len) {
    unsigned n = cert_len > 800 ? 20 : 32;
    u8 c[32];
    char t[96];
    if (!rnd) rnd = (u32)H->now() | 1;
    for (unsigned i = 0; i < n; ++i) rnd ^= rnd << 13, rnd ^= rnd >> 17, rnd ^= rnd << 5, c[i] = rnd;
    snprintf(t, sizeof t, "auth: the car's certificate is %u bytes; a %u-byte challenge", cert_len, n);
    H->log(t);
    msg_start(0xAA02);
    param(0, c, n);
    msg_send();
}

/* The car's identification: its names, and the message lists (6 it sends, 7 it takes) logged by name. */
static void take_identification(const u8 *p, unsigned len) {
    static const char *const fields[] = { "name", "model", "manufacturer", "serial", "firmware", "hardware" };
    char t[1800];
    unsigned n, o;
    for (unsigned f = 0; f < 6; ++f) {
        const u8 *v = find(p, len, f, &n);
        if (v && n && !v[n - 1]) {
            snprintf(t, sizeof t, "ident: %s \"%s\"", fields[f], (const char *)v);
            H->log(t);
            if (f == 0) snprintf(car_name, sizeof car_name, "%s", (const char *)v);
        }
    }
    for (unsigned which = 6; which <= 7; ++which) {
        const u8 *list = find(p, len, which, &n);
        o = (unsigned)snprintf(t, sizeof t, "ident: the car %s:", which == 6 ? "sends" : "takes");
        for (unsigned i = 0; list && i + 1 < n && o + 48 < sizeof t; i += 2) {
            unsigned id = list[i] << 8 | list[i + 1];
            o += (unsigned)snprintf(t + o, sizeof t - o, " %04x %s", id, iap2_name(id));
            if (which == 7 && car_receives_n < sizeof car_receives / sizeof *car_receives)
                car_receives[car_receives_n++] = id;
        }
        H->log(t);
    }
    const u8 *bt = find(p, len, 17, &n);
    bt_component = bt && (bt = find(bt, n, 0, &n)) && n == 2 ? (int)get_be(bt, 2) : -1;
    /* An iPhone accepts an identification 24 to 81 ms after it (PodBox, from iPhones' logs). */
    ident_due = due_in(30);
}

void accept_identification(void) {
    send_empty(0x1D02);
    identified = ever_identified = 1;
    H->log("ident: accepted");
    /* What an iPhone sends next, in its order, a packet each; PodBox's car resets without the UUID. */
    if (car_takes(0x4E0C)) msg_start(0x4E0C), param_str(0, H->uuid), msg_send();
    if (car_takes(0x4E0A)) msg_start(0x4E0A), param_str(0, "en"), msg_send();
    if (car_takes(0x4E09)) msg_start(0x4E09), param_str(0, H->name), msg_send();
    /* The iPhone then starts pairing with the car's Bluetooth component and stops it at once, the stop carrying an
     * empty parameter 2. */
    if (bt_component >= 0 && car_takes(0x0B00) && car_takes(0x0B03)) {
        msg_start(0x0B00), param_u16(0, bt_component), msg_send();
        msg_start(0x0B03), param_u16(0, bt_component), param(2, 0, 0), msg_send();
    }
}

/* CarPlay is never available, as an iPhone with it switched off, so the car falls back to plain USB audio: groups 0
 * (wired) and 1 (wireless), each Available false in sub-parameter 0. */
void send_carplay_availability(void) {
    availability_due = 0;
    msg_start(0x4300);
    group_start(0), param_u8(0, 0), group_end();
    group_start(1), param_u8(0, 0), group_end();
    msg_send();
}

/* The attribute ids a Start...Updates group lists, as a mask. */
static u32 wanted(const u8 *g, unsigned len, u32 fallback) {
    if (!g) return fallback;
    u32 mask = 0;
    for (unsigned off = 0; off + 4 <= len;) {
        unsigned plen = g[off] << 8 | g[off + 1], id = g[off + 2] << 8 | g[off + 3];
        if (plen < 4) break;
        if (id < 32) mask |= 1u << id;
        off += plen;
    }
    return mask;
}

/* Each power attribute asked for, a message each in an iPhone's order 4, 5, 0, 1, 6 (PodBox): 4 01; 5 the charge
 * state (2 charged: the Q2 on the car's power); 0 the current it takes, never more than the car offers; 1 01; 6 the
 * battery percentage, 100 as an iPhone at full charge sends. */
static void send_power(u32 mask) {
    static const u8 order[] = { 4, 5, 0, 1, 6 };
    for (unsigned i = 0; i < sizeof order; ++i) {
        unsigned a = order[i];
        if (!(mask & 1u << a)) continue;
        msg_start(0xAE01);
        if (a == 5) param_u8(a, 2);
        else if (a == 0) param_u16(a, car_current && car_current < 1500 ? car_current : 1500);
        else if (a == 6) param_u16(a, 100);
        else param_u8(a, 1);
        msg_send();
    }
}

/* The rate as an index into an iPhone's nine (8000 to 48000); an iPhone at 44100 sends 7, then two uint32s, both 0.
 * The rate is the player's for the song about to play (iap2_audio_rate, docs/iphone-mode.md#per-song-rate), sent at
 * StartUSBDeviceModeAudio and again at each song whose rate differs: 32, 44.1 and 48 kHz (6, 7, 8), the 48 kHz family
 * above it 8, anything else 44.1 kHz's 7. */
const unsigned iap2_rates[9] = { 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000 };
unsigned audio_hz = 44100;
static int audio_sent = -1;

int iap2_rate_index(unsigned hz) {
    if (hz == 32000) return 6;
    if (hz == 48000 || (hz > 48000 && hz % 48000 == 0)) return 8;
    return 7;
}

static void send_audio_rate(void) {
    char t[64];
    audio_sent = iap2_rate_index(audio_hz);
    snprintf(t, sizeof t, "audio: %u Hz announced (DA01 index %d)", iap2_rates[audio_sent], audio_sent);
    H->log(t);
    msg_start(0xDA01);
    param_u8(0, (u8)audio_sent);
    param_u32(1, 0);
    param_u32(2, 0);
    msg_send();
}

/* The player's rate for the song about to play (q2_iap.c rate_poll): told the car while it takes USB audio. */
void iap2_audio_rate(unsigned hz) {
    audio_hz = hz;
    if (audio_on && iap2_rate_index(hz) != audio_sent) send_audio_rate();
}

void iap2_control_receive(const u8 *msg, unsigned len) {
    unsigned id = msg[4] << 8 | msg[5], plen = len - 6, n;
    const u8 *p = msg + 6, *v;
    /* whole: identification, and the car's Start*Updates, which list the attributes it wants (on 2026-11-22 the 160
     * bytes cut StartMediaLibraryUpdates' list of media item attributes, the question of a library item's cover) */
    log_msg(0, msg, len, id == 0x1D01 || id == 0x4C03 || id == 0x5000 ? 1100 : id == 0xAA01 || id == 0xAA03 ? 24 : 160);
    switch (id) {
    case 0xAA01:
        send_challenge(find(p, plen, 0, &n) ? n : 0);
        break;
    case 0xAA03:
        auth_due = due_in(20); /* an iPhone waits about 20 ms before accepting it (PodBox) */
        break;
    case 0xAA04:
        H->log("auth: the car says authentication failed");
        break;
    case 0x1D01:
        take_identification(p, plen);
        break;
    case 0x1D03:
        H->log("ident: the car rejected its own identification?");
        break;
    case 0xAE00:
        /* answered whatever the identification lists, as an iPhone does (PodBox's Lexus) */
        send_power(wanted(p, plen, 0));
        break;
    case 0xAE03:
        /* Trap (PodBox): CarPlayAvailability before this, or within a millisecond of it, and that car goes quiet
         * after StartHID; 50 ms after it, or at StartHID if that comes first, it goes on. */
        if ((v = find(p, plen, 0, &n)) && n == 2) car_current = get_be(v, 2);
        if (identified && car_takes(0x4300)) availability_due = due_in(50);
        break;
    case 0x4E03:
        /* an iPhone answers with the component and an empty parameter 1: no Bluetooth profile connected */
        if ((v = find(p, plen, 0, &n)) && n == 2) {
            msg_start(0x4E04), param_u16(0, get_be(v, 2)), param(1, 0, 0), msg_send();
        }
        break;
    case 0x4C00:
        send_library_info();
        break;
    case 0x4C03: {
        unsigned rn, wn;
        const u8 *rev = find(p, plen, 1, &rn), *w = find(p, plen, 2, &wn);
        send_library_updates(rev && rn && !rev[rn - 1] ? (const char *)rev : 0, wanted(w, wn, 0x0A0FFFFF));
        break;
    }
    case 0x4C05:
        ls.phase = LIB_IDLE, ls.x_state = 0;
        break;
    case 0x4C06:
        if (H->command) H->command("resume");
        break;
    case 0x4C07:
        play_items(p, plen);
        break;
    case 0x4C08:
        play_collection(p, plen);
        break;
    case 0x4C09:
        play_all();
        break;
    case 0xDA00:
        H->log("audio: the car asks for USB audio");
        if (H->rate) audio_hz = H->rate(); /* read only every RATE_IDLE_MS until now (q2_iap.c rate_poll) */
        send_audio_rate();
        set_audio(1);
        break;
    case 0xDA02:
        H->log("audio: the car stops USB audio");
        set_audio(0);
        break;
    case 0x5000:
        v = find(p, plen, 0, &n);
        media_mask = wanted(v, n, DEFAULT_MEDIA);
        v = find(p, plen, 1, &n);
        playback_mask = wanted(v, n, DEFAULT_PLAYBACK);
        now_playing = 1, np_sent = 0, np_at = 0;
        poll_now_playing(H->now());
        break;
    case 0x5002:
        now_playing = 0;
        break;
    case 0x5003:
        /* the car's seek: parameter 0, the position in ms */
        if ((v = find(p, plen, 0, &n)) && n == 4 && H->command) {
            char verb[32];
            snprintf(verb, sizeof verb, "seek %u", get_be(v, 4) / 1000);
            H->log(verb);
            H->command(verb);
        }
        break;
    case 0x6800: {
        char t[80];
        if ((v = find(p, plen, 4, &n))) parse_hid(v, n);
        snprintf(t, sizeof t, "hid: the car starts HID: %u buttons in its report descriptor", n_controls);
        H->log(t);
        memset(last_report, 0, sizeof last_report);
        if (availability_due) send_carplay_availability();
        break;
    }
    case 0x6802:
        if ((v = find(p, plen, 1, &n))) {
            char h[200];
            iap2_hex(h, sizeof h, v, n);
            iap2_log("hid: a button report", h);
            take_hid_report(v, n);
        }
        break;
    case 0x6803:
        n_controls = 0;
        break;
    default:
        break;
    }
}
