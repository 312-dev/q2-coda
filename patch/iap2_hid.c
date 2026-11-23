/* The car's buttons (docs/iphone-mode.md#iap2): its StartHID report descriptor's one-bit consumer controls (PodBox
 * parse_hid), each AccessoryHIDReport's presses as the shim's verbs, and hold to scan on Next and Previous. Part of
 * the PodBox port (iap2.h). */
#include "q2_iap_sys.h"
#include "iap2.h"
#include "iap2_hid.h"

#define MAX_CONTROLS 24
static struct {
    u8 report;  /* report id, or 0 */
    u16 bit;    /* from the report's data */
    u16 usage;
} controls[MAX_CONTROLS];
unsigned n_controls;
static unsigned report_ids;
u8 last_report[16];

void parse_hid(const u8 *d, unsigned len) {
    u32 page = 0, size = 0, count_ = 0, id = 0, usages[16], bits[8] = { 0 };
    unsigned n_usages = 0;
    n_controls = report_ids = 0;
    for (unsigned i = 0; i < len;) {
        u8 prefix = d[i];
        if (prefix == 0xFE) { /* a long item */
            i += 3 + (i + 1 < len ? d[i + 1] : 0);
            continue;
        }
        unsigned n = (prefix & 3) == 3 ? 4 : prefix & 3;
        if (i + 1 + n > len) break;
        u32 v = n == 4 ? d[i + 1] | d[i + 2] << 8 | (u32)d[i + 3] << 16 | (u32)d[i + 4] << 24
              : n == 2 ? (u32)(d[i + 1] | d[i + 2] << 8)
              : n == 1 ? d[i + 1] : 0u;
        i += 1 + n;
        switch (prefix & 0xFC) {
        case 0x04: page = v; break;
        case 0x74: size = v; break;
        case 0x84: id = v, report_ids = 1; break;
        case 0x94: count_ = v; break;
        case 0x08:
            if (n_usages < 16) usages[n_usages++] = n == 4 ? v : page << 16 | v;
            break;
        case 0x80: { /* Input: each one-bit variable on the consumer page, as its usage */
            u32 *at = &bits[id & 7];
            if (!(v & 1) && (v & 2) && page == 0x0C && size == 1)
                for (u32 k = 0; k < count_ && k < n_usages && n_controls < MAX_CONTROLS; ++k)
                    if (usages[k] >> 16 == 0x0C)
                        controls[n_controls].report = id, controls[n_controls].bit = *at + k,
                        controls[n_controls++].usage = usages[k] & 0xFFFF;
            *at += size * count_;
            n_usages = 0; /* a main item clears the local items */
            break;
        }
        case 0x90: case 0xB0: case 0xA0: case 0xC0:
            n_usages = 0;
            break;
        }
    }
}

/* A control pressed: what the Q2's own remotes send (patch/coda_rc_shim.c cr_command's verbs). */
static void press(unsigned usage) {
    char v[32], t[96];
    struct iap2_np now;
    memset(&now, 0, sizeof now);
    if (H->now_playing) H->now_playing(&now); /* shuffle and repeat as they are now, not as last sent */
    const char *verb = usage == 0xB0 ? "resume" : usage == 0xB1 ? "pause" : usage == 0xCD ? "toggle"
                     : usage == 0xB5 ? "next" : usage == 0xB6 ? "previous" : 0;
    if (!verb && usage == 0xB9) snprintf(v, sizeof v, "mode %d %d", !now.shuffle, now.repeat), verb = v;
    if (!verb && usage == 0xBC) snprintf(v, sizeof v, "mode %d %d", now.shuffle, (now.repeat + 1) % 3), verb = v;
    snprintf(t, sizeof t, "hid: usage %02x pressed: %s", usage, verb ? verb : "not mapped");
    H->log(t);
    if (verb && H->command) H->command(verb);
}

static unsigned report_bit(const u8 *r, unsigned len, unsigned bit) { return bit / 8 < len && (r[bit / 8] >> (bit % 8) & 1); }

/* Hold to scan, as an iPod's wheel buttons: the BMW's descriptor has no fast-forward or rewind usage, only Next and
 * Previous. Next or Previous is acted on at its release: up within HOLD_MS, a skip as before (the car's taps were up
 * 13 to 120 ms after the press, 2026-11-20); held past it, the Q2's own scan of a held button (seek.c seek_car, heard
 * at 2, 4 and 8 times and then in growing jumps), started with the shim's "scan 1" or "scan -1", said again every
 * SCAN_KEEP_MS while the button stays down (seek.c lets a car's scan go after 2 s without it), and "scan 0" at the
 * release; the song plays on from where the scan reached. The first drive's scan, a seek every 500 ms, came out uneven
 * ("pretty inconsistent", 2026-11-20): each seek a mute, a drop and a fresh decode, read a loop pass apart. */
unsigned held; /* Next's or Previous's usage while it is down, else 0 */
long held_at, scan_at;
int scanning;

static void scan_say(long now, const char *verb) {
    char t[96];
    scan_at = now;
    snprintf(t, sizeof t, "hid: usage %02x held %ld ms: %s", held, now - held_at, verb);
    H->log(t);
    if (H->command) H->command(verb);
}

/* From iap2_tick: a hold that passed HOLD_MS starts the scan, and every SCAN_KEEP_MS says it is still held. */
void scan_tick(long now) {
    if (!held || now - held_at < HOLD_MS) return;
    if (!scanning || now - scan_at >= SCAN_KEEP_MS) scanning = 1, scan_say(now, held == 0xB5 ? "scan 1" : "scan -1");
}

static void release(unsigned usage) {
    char t[96];
    if (usage != held) return;
    long ms = H->now() - held_at;
    if (!scanning) press(usage); /* a tap: the skip */
    else snprintf(t, sizeof t, "hid: usage %02x released after %ld ms: the scan ends", usage, ms), H->log(t),
                                                                                                  scan_say(H->now(), "scan 0");
    held = 0, scanning = 0;
}

/* Each control down now and up in the last report, and up now and down in it. */
void take_hid_report(const u8 *r, unsigned len) {
    u8 id = 0;
    if (report_ids && len) id = *r++, --len;
    if (len > sizeof last_report) len = sizeof last_report;
    for (unsigned i = 0; i < n_controls; ++i) {
        if (controls[i].report != id) continue;
        unsigned now = report_bit(r, len, controls[i].bit), was = report_bit(last_report, len, controls[i].bit),
                 usage = controls[i].usage;
        if (now && !was && (usage == 0xB5 || usage == 0xB6)) {
            char t[64];
            held = usage, held_at = H->now(), scanning = 0;
            snprintf(t, sizeof t, "hid: usage %02x down", usage);
            H->log(t);
        } else if (now && !was) {
            press(usage);
        } else if (!now && was) {
            release(usage);
        }
    }
    memset(last_report, 0, sizeof last_report);
    memcpy(last_report, r, len);
}
