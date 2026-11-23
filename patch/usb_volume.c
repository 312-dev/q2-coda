/* A USB DAC's own volume control in decibels (docs/player.md#usb-volume). Stock's mclUsbAudioSetVol
 * (0x5bec64) writes the Q2's volume v (0 to 100) to each "Playback Volume" control its finder
 * recorded (USB_MIXER and the numids before it) as trunc(range * (0.4 + 0.6 * v / 100)) for a
 * range (max - min) of 255 or more, else trunc(range * (0.2 + 0.8 * v / 100)), clamped to the
 * control's min and max, and the min for v 0. On a control of 0..11520 in 1/256 dB (-45 to 0 dB,
 * the AB13X earbuds, 3302:13d1) volume 1 is -26.7 dB, loud in an ear.
 *
 * ringnav_usb_setvol takes it: when the first control reports a dB range (its TLV, read with
 * SNDRV_CTL_IOCTL_TLV_READ on /dev/snd/controlC1, as demo imports no TLV call), v from 1 to 100 is
 * spread linearly in dB from max(the control's minimum, -60 dB) up to its maximum, and stock's own
 * pass is given the volume whose stock value is that control value, so stock still finds and
 * writes every control and channel. v 0 and a control with no dB range or a range under 255 keep
 * stock's law exactly. ponytail: a DAC with more than one volume control gets the first one's
 * mapping on all of them; read each control's TLV if one is seen. */
#include "navigation.h"

#define UV_CONTROL "/dev/snd/controlC1" /* the DAC, stock's "hw:1" */
#define UV_NUMIDS 0xa50030              /* the finder's numids, USB_MIXER of them */
#define UV_FLOOR_CDB (-6000)            /* volume 1 at -60 dB at most, in 0.01 dB */
/* MIPS ioctl numbers (direction bits 31..29: 6 for read and write): _IOWR('U', 0x11,
 * struct snd_ctl_elem_info) and _IOWR('U', 0x1a, struct snd_ctl_tlv) */
#define UV_ELEM_INFO 0xc1105511u
#define UV_TLV_READ 0xc008551au
#define UV_TLV_CONTAINER 0
#define UV_TLV_DB_SCALE 1
#define UV_TLV_DB_MINMAX 4
#define UV_TLV_DB_MINMAX_MUTE 5

/* struct snd_ctl_elem_info as the MIPS o32 kernel has it: the id (64 bytes), type, access, count,
 * owner, then the value union at 80 (integer: long min, max, step), 128 bytes, and 64 reserved. */
struct uv_info {
    unsigned numid;
    int iface;
    unsigned device, subdevice;
    char name[44];
    unsigned index;
    int type;
    unsigned access, count;
    int owner;
    int min, max, step;
    char rest[180];
};

struct uv_tlv {
    unsigned numid, length; /* length: the bytes of tlv the kernel may fill */
    int tlv[32];
};

/* The dB range in 0.01 dB of a TLV, searching a container; 0 when it has none. */
static int uv_db(const int *t, int words, int range, int *lo, int *hi) {
    while (words >= 2) {
        int type = t[0], len = t[1] / 4;
        if (len < 0 || len + 2 > words) return 0;
        if (type == UV_TLV_CONTAINER && uv_db(t + 2, len, range, lo, hi)) return 1;
        if ((type == UV_TLV_DB_MINMAX || type == UV_TLV_DB_MINMAX_MUTE) && len >= 2) {
            *lo = t[2], *hi = t[3];
            return 1;
        }
        if (type == UV_TLV_DB_SCALE && len >= 2) {
            *lo = t[2], *hi = t[2] + (t[3] & 0xffff) * range;
            return 1;
        }
        t += len + 2, words -= len + 2;
    }
    return 0;
}

/* The first control's min, max and dB range; 0 when any of it can't be read. */
static int uv_control(int numid, int *min, int *max, int *lo, int *hi) {
    int fd = open(UV_CONTROL, 2 /* O_RDWR */);
    if (fd < 0) return 0;
    struct uv_info info;
    memset(&info, 0, sizeof info);
    info.numid = (unsigned)numid;
    struct uv_tlv t;
    memset(&t, 0, sizeof t);
    t.numid = (unsigned)numid, t.length = sizeof t.tlv;
    int ok = !ioctl(fd, UV_ELEM_INFO, &info) && info.type == 2 /* INTEGER */ &&
             info.max > info.min && !ioctl(fd, UV_TLV_READ, &t) &&
             uv_db(t.tlv, (int)(t.length / 4), info.max - info.min, lo, hi) && *hi > *lo;
    close(fd);
    *min = info.min, *max = info.max;
    return ok;
}

/* Stock's value for volume s on a control of range 255 or more, as mclUsbAudioSetVol computes it
 * (single-precision s / 100, the rest in double), clamped as it is. */
static int uv_stock(int s, int min, int max) {
    float range = (float)(max - min);
    int x =
        s ? (int)((double)range * 0.4 + (double)((float)s / 100.0f) * ((double)range * 0.6)) : 0;
    return x > max ? max : x < min ? min : x;
}

int ringnav_usb_setvol(int v, int right, const char *card) {
    if (*(int *)USB_MIXER == -1) { /* stock's first call runs its finder, at a DAC's attach */
        int result = stock_usb_setvol_trampoline(v, right, card);
        if (*(int *)USB_MIXER <= 0) return result;
    }
    int min, max, lo, hi;
    if (v <= 0 || *(int *)USB_MIXER <= 0 || !uv_control(*(int *)UV_NUMIDS, &min, &max, &lo, &hi) ||
        max - min < 255)
        return stock_usb_setvol_trampoline(v, right, card);
    int floor = lo > UV_FLOOR_CDB ? lo : UV_FLOOR_CDB;
    if (v > 100) v = 100;
    double db = hi - (double)(100 - v) / 99.0 * (hi - floor);
    double want = min + (db - lo) * (max - min) / (hi - lo);
    /* the volume whose stock value is nearest the control value wanted (0 is stock's minimum, so
     * not 0) */
    int s0 = (int)((want / (max - min) - 0.4) / 0.006), best = s0 ? s0 : 1;
    for (int s = s0 - 2; s <= s0 + 3; ++s) {
        double d = uv_stock(s, min, max) - want, e = uv_stock(best, min, max) - want;
        if (s && (d < 0 ? -d : d) < (e < 0 ? -e : e)) best = s;
    }
    return stock_usb_setvol_trampoline(best, best, card);
}
