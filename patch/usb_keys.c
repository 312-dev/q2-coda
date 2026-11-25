/* A USB-C headset's own buttons (docs/internals.md#usb-headset-buttons). A USB-C headset, or a
 * USB-C to 3.5 mm adapter with its own DAC, sends its remote's presses on a USB HID interface
 * beside its audio interfaces, as consumer-page usages. The stock kernel binds usbhid to that
 * interface but has no hid-generic, so no HID driver takes the device and no input node appears
 * (docs/hardware.md#inside-as-the-device-reports-it). So a worker reads the interface itself
 * through usbfs: it takes that interface alone from usbhid (USBDEVFS_DISCONNECT_CLAIM, only when
 * usbhid or nothing holds it), reads its report descriptor for where each key is, and waits on its
 * interrupt endpoint with one URB in flight. The audio interfaces are never claimed, and nothing
 * runs without a USB DAC on the port (g_otg_status 2). The UI loop then acts as for the Q2's own
 * keys: a volume key steps the volume one, as a slow wheel tick, repeated while held; Play/Pause,
 * Next and Previous as a Bluetooth headset's (media_key); and Play/Pause pressed twice is Next,
 * three times Previous. Built with peq.h alone, as headset.c is: navigation.h takes libc's select
 * away. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

void vol_show(int from);  /* volume.c */
void usb_keys_poll(void); /* from usb_ui_poll */

#define UK_DEVICES "/sys/bus/usb/devices"
#define UK_CARD "/sys/class/sound/card1/device" /* the USB DAC's interface (stock's hw:1) */
/* A held volume key steps at once, then after UK_REPEAT_FIRST_MS every UK_REPEAT_MS: the Q2's own
 * keys' repeat (Center's measured at about 258 ms, then 50 ms: docs/internals.md#wake). */
#define UK_REPEAT_FIRST_MS 250
#define UK_REPEAT_MS 50
/* Play/Pause presses are one gesture while each starts within this of the last one's release */
#define UK_MULTI_MS 400
#define UK_CHECKS 24
/* MIPS o32 ioctl numbers (direction bits 31..29: 2 read, 4 write, 6 both) */
#define UK_CONTROL 0xc0105500u          /* _IOWR('U', 0, struct usbdevfs_ctrltransfer), 16 bytes */
#define UK_SUBMITURB 0x402c550au        /* _IOR('U', 10, struct usbdevfs_urb), 44 bytes */
#define UK_REAPURBNDELAY 0x8004550du    /* _IOW('U', 13, void *) */
#define UK_DISCONNECT_CLAIM 0x4108551bu /* _IOR('U', 27, struct usbdevfs_disconnect_claim), 264 */
/* What the worker posts: AVRCP's codes for the media keys (as headset.c and guest.c take them),
 * the input layer's for volume, and UK_SAVE once a volume key is let go */
#define UK_NEXT 163
#define UK_PLAYPAUSE 164
#define UK_PREV 165
#define UK_PLAY 200
#define UK_PAUSE 201
#define UK_VOLUP 115
#define UK_VOLDOWN 114
#define UK_SAVE 1

/* The keys, by bit: consumer-page usages (HID Usage Tables 15) and what each posts. Volume first.
 */
static const unsigned short uk_usage[7] = { 0xe9, 0xea, 0xcd, 0xb0, 0xb1, 0xb5, 0xb6 };
static const unsigned char uk_code[7] = { UK_VOLUP, UK_VOLDOWN, UK_PLAYPAUSE, UK_PLAY,
                                          UK_PAUSE, UK_NEXT,    UK_PREV };

/* Where a key is in an input report: `bits` wide at bit `off` after the report ID; a variable
 * field (count 0) holds it while not 0, an array of `count` such fields while one of them is
 * `value`. */
typedef struct {
    unsigned short off, value;
    unsigned char rid, bits, count, key;
} uk_check_t;

static struct {
    unsigned generation, head, tail;
    int ring[16], tried;
} uk __attribute__((section(".scratch")));

/* A report descriptor's input fields that carry the keys, into c; *ids whether reports start with
 * a report ID. Their count. Long items and Push/Pop are passed over (no headset seen uses them). */
static int uk_parse(const unsigned char *d, int n, uk_check_t *c, int *ids) {
    unsigned short offs[256]; /* the next input bit of each report ID */
    unsigned page = 0, size = 0, count = 0, rid = 0, used[16], nu = 0, umin = 0, umax = 0,
             range = 0;
    int lmin = 0, found = 0;
    memset(offs, 0, sizeof offs);
    *ids = 0;
    for (int i = 0; i < n;) {
        unsigned b = d[i], sz = b & 3, v = 0;
        if (b == 0xfe) { /* a long item: its data size, then its tag */
            i += 3 + (i + 1 < n ? d[i + 1] : 0);
            continue;
        }
        if (sz == 3) sz = 4;
        if (i + 1 + (int)sz > n) break;
        for (unsigned k = 0; k < sz; ++k) v |= (unsigned)d[i + 1 + k] << 8 * k;
        i += 1 + sz;
        unsigned full =
            sz == 4 ? v : page << 16 | v; /* a local usage takes the page unless it has one */
        switch (b & 0xfc) {
        case 0x04:
            page = v;
            break;
        case 0x14:
            lmin = sz == 1 ? (signed char)v : sz == 2 ? (short)v : (int)v;
            break;
        case 0x74:
            size = v;
            break;
        case 0x84:
            rid = v & 0xff, *ids = 1;
            break;
        case 0x94:
            count = v;
            break;
        case 0x08:
            if (nu < 16) used[nu++] = full;
            break;
        case 0x18:
            umin = full, range = 1;
            break;
        case 0x28:
            umax = full, range = 1;
            break;
        case 0x80: { /* Input: data, not constant. Each slot's usage: a variable field's (the last
                        repeated), or the one an array field's index j holds */
            unsigned slots = v & 2 ? count : range ? umax - umin + 1 : nu;
            for (unsigned j = 0;
                 !(v & 1) && size && size <= 16 && count && (range || nu) && j < slots && j < 1024;
                 ++j) {
                unsigned u =
                    range ? (umin + j <= umax ? umin + j : umax) : used[j < nu ? j : nu - 1];
                for (int k = 0; k < 7 && found < UK_CHECKS; ++k)
                    if (u == (0xc0000u | uk_usage[k]))
                        c[found++] =
                            v & 2 ? (uk_check_t){ offs[rid] + j * size, 0, rid, size, 0, k }
                                  : (uk_check_t){
                                        offs[rid], lmin + j, rid, size, count > 255 ? 255 : count, k
                                    };
            }
            offs[rid] += size * count;
            break;
        }
        }
        if (!(b & 0x0c)) nu = range = umin = umax = 0; /* a main item ends the local ones */
    }
    return found;
}

static unsigned uk_field(const unsigned char *r, int n, unsigned off, unsigned bits) {
    unsigned v = 0;
    for (unsigned b = 0; b < bits; ++b, ++off)
        if ((int)(off >> 3) < n && r[off >> 3] >> (off & 7) & 1) v |= 1u << b;
    return v;
}

/* The keys report r (n bytes) holds down, and in *cover the keys its report ID carries. */
static unsigned uk_keys(const unsigned char *r, int n, const uk_check_t *c, int nc, int ids,
                        unsigned *cover) {
    unsigned rid = 0, held = 0;
    *cover = 0;
    if (ids) {
        if (n < 1) return 0;
        rid = *r++, --n;
    }
    for (int i = 0; i < nc; ++i)
        if (c[i].rid == rid) {
            *cover |= 1u << c[i].key;
            for (unsigned j = 0; j < (c[i].count ? c[i].count : 1u); ++j) {
                unsigned v = uk_field(r, n, c[i].off + j * c[i].bits, c[i].bits);
                if (c[i].count ? v == c[i].value : v != 0) held |= 1u << c[i].key;
            }
        }
    return held;
}

/* A sysfs attribute of UK_DEVICES/dev as a decimal number, -1 if none. A class reads so too: "03".
 */
static int uk_attr(const char *dev, const char *name) {
    char path[96], buf[16];
    snprintf(path, sizeof path, UK_DEVICES "/%s/%s", dev, name);
    int fd = open(path, 0x80000), n = fd < 0 ? -1 : read(fd, buf, sizeof buf - 1); /* O_CLOEXEC */
    if (fd >= 0) close(fd);
    if (n <= 0) return -1;
    buf[n] = 0;
    return atoi(buf);
}

/* The USB DAC's device, as stock's hw:1 (card1) names its interface ("../../../1-1.2:1.0"): its
 * usbfs node into path and the number of its first HID interface (class 3) of interfaces 0 to 7,
 * or -1. */
static int uk_find(char *path, unsigned size) {
    char link[128], name[48];
    int n = readlink(UK_CARD, link, sizeof link - 1);
    if (n <= 0) return -1;
    link[n] = 0;
    char *base = strrchr(link, '/'), *colon = strstr(link, ":"), *dot = strrchr(link, '.');
    if (!base++ || colon < base || dot < colon) return -1;
    for (int i = 0; i < 8; ++i) {
        snprintf(name, sizeof name, "%.*s.%d", (int)(dot - base), base, i); /* "1-1.2:1.3" */
        if (uk_attr(name, "bInterfaceClass") == 3) {
            *colon = 0; /* the device, "1-1.2" */
            snprintf(path, size, "/dev/bus/usb/%03d/%03d", uk_attr(base, "busnum"),
                     uk_attr(base, "devnum"));
            return i;
        }
    }
    return -1;
}

/* In the descriptors usbfs reads (the device's 18 bytes, then the configurations'), interface
 * iface's report descriptor length and interrupt IN endpoint. */
static void uk_interface(const unsigned char *d, int n, int iface, unsigned *len, unsigned *ep,
                         unsigned *maxp) {
    int in = 0;
    for (int i = 18; i + 2 <= n && d[i] >= 2 && i + d[i] <= n; i += d[i]) {
        const unsigned char *p = d + i;
        if (p[1] == 2 || p[1] == 4) { /* a configuration or an interface: ours ends there */
            if (in) return;
            in = p[1] == 4 && p[0] >= 9 && p[2] == iface && !p[3];
        } else if (in && p[1] == 0x21 && p[0] >= 9 &&
                   p[6] == 0x22)     /* HID: the report descriptor, */
            *len = p[7] | p[8] << 8; /* its first class descriptor (HID 1.11, 6.2.1) */
        else if (in && p[1] == 5 && p[0] >= 7 && p[2] & 0x80 && (p[3] & 3) == 3 && !*ep)
            *ep = p[2], *maxp = p[4] | (p[5] & 7) << 8;
    }
}

static void uk_post(unsigned gen, int code) {
    if (gen != uk.generation || uk.head - uk.tail >= 16) return;
    uk.ring[uk.head % 16] = code;
    __sync_synchronize();
    ++uk.head;
    loop_wake(); /* usb_keys_poll takes it on the pass this starts, lit or dark */
}

/* The worker of one attach (generation gen): until the device goes or a newer attach starts. */
static void *uk_worker(void *arg) {
    unsigned gen = (unsigned)(long)arg, len = 0, ep = 0, maxp = 0, held = 0, presses = 0,
             rep_at = 0, multi_at = 0;
    char path[32];
    unsigned char buf[1024], rep[64];
    uk_check_t c[UK_CHECKS];
    int iface = uk_find(path, sizeof path), fd = iface < 0 ? -1 : open(path, 0x80002), ids = 0,
        nc = 0, errors = 0;
    int n = fd < 0 ? -1 : read(fd, buf, sizeof buf); /* usbfs: the descriptors */
    if (n > 0) uk_interface(buf, n, iface, &len, &ep, &maxp);
    struct {
        unsigned interface, flags; /* USBDEVFS_DISCONNECT_CLAIM_IF_DRIVER: only from that driver */
        char driver[256];
    } dc = { (unsigned)iface, 1, "usbhid" };
    struct {
        unsigned char type, request;
        unsigned short value, index, length;
        unsigned timeout;
        void *data;
    } get = { 0x81,
              6,
              0x2200,
              (unsigned short)iface,
              (unsigned short)(len < sizeof buf ? len : sizeof buf),
              1000,
              buf };
    struct {
        unsigned char type, endpoint;
        int status;
        unsigned flags;
        void *buffer;
        int length, actual, start, packets, errors;
        unsigned signr;
        void *context;
    } urb = { .type = 1, /* interrupt */
              .endpoint = (unsigned char)ep,
              .buffer = rep,
              .length = maxp < sizeof rep ? (int)maxp : (int)sizeof rep };
    if (ep && len && ioctl(fd, UK_DISCONNECT_CLAIM, &dc) >= 0 &&
        (n = ioctl(fd, UK_CONTROL, &get)) > 0)
        nc = uk_parse(buf, n, c, &ids);
    if (nc && urb.length > 0 && ioctl(fd, UK_SUBMITURB, &urb) >= 0)
        while (gen == uk.generation) {
            unsigned now = time_now_ms();
            if (rep_at && (int)(now - rep_at) >= 0) {
                uk_post(gen, held & 1 ? UK_VOLUP : UK_VOLDOWN);
                rep_at = now + UK_REPEAT_MS;
            }
            if (multi_at && (int)(now - multi_at) >= 0) {
                uk_post(gen, presses == 1 ? UK_PLAYPAUSE : UK_NEXT);
                presses = multi_at = 0;
            }
            int ms = rep_at ? (int)(rep_at - now) : -1;
            if (multi_at && (ms < 0 || (int)(multi_at - now) < ms)) ms = (int)(multi_at - now);
            unsigned set[32] = { 0 };
            long tv[2] = { 0, ms * 1000 }; /* under UK_MULTI_MS, so under a second */
            set[fd / 32] = 1u << (fd % 32); /* writable: the URB done, or the device gone */
            int r = select(fd + 1, 0, set, 0, ms < 0 ? 0 : tv);
            void *done = 0;
            if (r < 0 && *__errno_location() == 4) continue; /* EINTR */
            if (r <= 0) {
                if (r < 0) break;
                continue;
            }
            if (ioctl(fd, UK_REAPURBNDELAY, &done) < 0) {
                if (*__errno_location() == 11) continue; /* EAGAIN */
                break;
            }
            if (urb.status) {
                if (++errors > 8) break;
            } else {
                unsigned cover, now_held = uk_keys(rep, urb.actual, c, nc, ids, &cover);
                now_held |= held & ~cover;
                unsigned down = now_held & ~held, up = held & ~now_held;
                held = now_held;
                now = time_now_ms();
                errors = 0;
                for (int k = 0; k < 7; ++k)
                    if (down >> k & 1) {
                        if (k != 2) uk_post(gen, uk_code[k]);
                        if (k < 2) rep_at = now + UK_REPEAT_FIRST_MS;
                        if (k == 2) ++presses, multi_at = 0;
                    }
                if (up & 3 && !(held & 3)) rep_at = 0, uk_post(gen, UK_SAVE);
                if (up & 4 && presses >= 3)
                    uk_post(gen, UK_PREV), presses = 0;
                else if (up & 4)
                    multi_at = now + UK_MULTI_MS;
            }
            if (ioctl(fd, UK_SUBMITURB, &urb) < 0) break;
        }
    if (held & 3) uk_post(gen, UK_SAVE); /* gone mid-press: the volume reached is kept */
    if (fd >= 0) close(fd);              /* releases the interface and its URB */
    return 0;
}

/* One posted action, on the UI loop. Volume as a headset's own change (bt_volume_poll): up to
 * vol_cap, played at once and shown lit (vol_show), saved when the key is let go; nothing lights
 * the screen. The media keys go to a guest's phone while Guest Play plays, else to what plays. */
static void uk_act(int code) {
    if (code == UK_VOLUP || code == UK_VOLDOWN) {
        int from = g_volume, top = vol_cap(), v = from + (code == UK_VOLUP ? 1 : -1);
        if (code == UK_VOLUP && v > top)
            v = from > top ? from : top; /* never pulled down to it from above */
        if (v < 0) v = 0;
        if (v == from) return;
        g_volume = (unsigned char)v;
        device_set_volume(v, 1);
        vol_show(from);
    } else if (code == UK_SAVE)
        write_int_config(g_volume, "PLAYSET", "VOLUME");
    else if (guest_key(code))
        ;
    else if (code == UK_PLAY || code == UK_PAUSE) {
        if (media_playing() != (code == UK_PLAY)) media_key(KEY_PLAY);
    } else
        media_key(code == UK_NEXT ? KEY_FWD_BTN : code == UK_PREV ? KEY_BACK_BTN : KEY_PLAY);
}

/* Every UI pass (usb_ui_poll): a worker for each USB DAC stock's host pass finds (g_otg_status 2),
 * one per attach, and what the worker posted. Without a DAC this is a byte's test. */
void usb_keys_poll(void) {
    if (g_otg_status != 2)
        uk.tried = 0;
    else if (!uk.tried) {
        unsigned long tid;
        uk.tried = 1;
        if (!pthread_create(&tid, 0, uk_worker, (void *)(long)++uk.generation)) pthread_detach(tid);
    }
    while (uk.tail != uk.head) {
        __sync_synchronize();
        uk_act(uk.ring[uk.tail % 16]);
        ++uk.tail;
    }
}
