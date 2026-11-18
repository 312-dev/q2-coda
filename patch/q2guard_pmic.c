/* q2guard's temperature on the Q2: the AXP2101 PMIC's die temperature ADC, read over the PMIC's I2C
 * bus (docs/internals.md#overheat-guardian, "The die temperature"). No reads of the device here:
 * q2guard.c gives the bus (i2c-dev on the Q2, a recorder in test/unit/test_q2guard.py).
 *
 * The stock kernel leaves the ADC in a debug mode (0x34 bits 7:6 = 01, "VBAT uses all channels",
 * written by its power-key probe), where the die temperature channel never gets a conversion. So
 * each sample is a short window: the mode cleared to 00 (the chip's power-on default, every channel
 * in turn), a wait for a conversion, the result read high, low, high, and the mode put back. The
 * restore is written whatever the read did. Nothing is written unless every register checked reads
 * as the stock kernel leaves it; anything else turns this off for good, and q2guard watches the CPU. */
#include "q2guard.h"

static int bad_config(struct pmic *p, const char *what, int reg, int v) {
    p->state = PMIC_OFF;
    snprintf(p->note, sizeof p->note, "SENSOR pmic off: 0x%02x is 0x%02x, %s", reg, v, what);
    return PMIC_REFUSED;
}

/* An I2C error: PMIC_ERRORS of them in a row (over samples) turn the reader off. */
static int bus_error(struct pmic *p, const char *op, int reg) {
    if (++p->errors >= PMIC_ERRORS) {
        p->state = PMIC_OFF;
        snprintf(p->note, sizeof p->note, "SENSOR pmic off: %d i2c errors in a row (last: %s 0x%02x)",
                 p->errors, op, reg);
    }
    return PMIC_IO;
}

/* The registers the stock kernel leaves, read only: 0x30 the ADC channels (VBAT and TS, die
 * temperature maybe already on from an earlier start), 0x34 bits 7:6 the debug mode's low bits
 * (01 as the kernel sets it, 00 the chip's default), 0x36 bit 6 its high bit (0), 0x50 the TS pin
 * detached from the charger (0x10). PMIC_OK with *v30 and *v34 as read. */
static int check(const struct pmic_bus *b, struct pmic *p, int *v30, int *v34) {
    static const unsigned char regs[] = { 0x30, 0x34, 0x36, 0x50 };
    int v[4];
    for (int i = 0; i < 4; ++i)
        if ((v[i] = b->rd(b->ctx, regs[i])) < 0) return bus_error(p, "read", regs[i]);
    if (v[0] != PMIC_ADC_STOCK && v[0] != PMIC_ADC_DIE)
        return bad_config(p, "not 0x03 or 0x13 (ADC channels)", 0x30, v[0]);
    if ((v[1] & 0xc0) != 0x40 && (v[1] & 0xc0) != 0)
        return bad_config(p, "debug mode not 01 or 00", 0x34, v[1]);
    if (v[2] & 0x40) return bad_config(p, "debug mode high bit set", 0x36, v[2]);
    if (v[3] != 0x10) return bad_config(p, "not 0x10 (TS pin detached from the charger)", 0x50, v[3]);
    *v30 = v[0], *v34 = v[1];
    return PMIC_OK;
}

/* Turn the die temperature channel on: 0x30 bit 4, read, modify, write, read back. */
static int enable(const struct pmic_bus *b, struct pmic *p, int v30) {
    if (v30 == PMIC_ADC_DIE) return PMIC_OK;
    if (b->wr(b->ctx, 0x30, v30 | 0x10) < 0) return bus_error(p, "write", 0x30);
    int now = b->rd(b->ctx, 0x30);
    if (now < 0) return bus_error(p, "read", 0x30);
    if (now != PMIC_ADC_DIE) return bad_config(p, "did not take bit 4", 0x30, now);
    snprintf(p->note, sizeof p->note, "SENSOR pmic die temperature channel on (0x30 0x%02x to 0x%02x)", v30,
             now);
    return PMIC_OK;
}

/* The window: clear 0x34's mode bits (bits 5:0 are read-only data, so v34 & 0x3f leaves them), wait,
 * read 0x3C (bits 5:0, the high six), 0x3D, 0x3C again, restore v34. The restore is tried up to
 * three times and is written even when a read failed. *raw the 14-bit count, or -1. */
static int window(const struct pmic_bus *b, struct pmic *p, int v34, int *raw) {
    int rc = PMIC_OK, h = -1, l = -1, h2 = -1;
    *raw = -1;
    if (b->wr(b->ctx, 0x34, v34 & 0x3f) < 0) {
        rc = bus_error(p, "write", 0x34);
    } else {
        if (!p->measured) { /* the first window: how soon a conversion lands, for the log */
            int was_h = b->rd(b->ctx, 0x3c), was_l = b->rd(b->ctx, 0x3d), ms = 0;
            p->first_ms = -1;
            while (ms < PMIC_WINDOW_MS) {
                b->sleep_ms(b->ctx, PMIC_POLL_MS), ms += PMIC_POLL_MS;
                if (p->first_ms >= 0) continue;
                int now_h = b->rd(b->ctx, 0x3c), now_l = b->rd(b->ctx, 0x3d);
                if (now_h >= 0 && now_l >= 0 && (now_h != was_h || now_l != was_l)) p->first_ms = ms;
            }
            p->measured = 1; /* q2guard.c logs first_ms and makes it 2 */
        } else {
            b->sleep_ms(b->ctx, PMIC_WINDOW_MS);
        }
        if ((h = b->rd(b->ctx, 0x3c)) < 0)
            rc = bus_error(p, "read", 0x3c);
        else if ((l = b->rd(b->ctx, 0x3d)) < 0)
            rc = bus_error(p, "read", 0x3d);
        else if ((h2 = b->rd(b->ctx, 0x3c)) < 0)
            rc = bus_error(p, "read", 0x3c);
    }
    int restored = 0;
    for (int i = 0; i < 3 && !restored; ++i) restored = b->wr(b->ctx, 0x34, v34) == 0;
    if (!restored) {
        p->state = PMIC_OFF;
        snprintf(p->note, sizeof p->note, "SENSOR pmic off: 0x34 not restored to 0x%02x (debug mode left 00)", v34);
        return PMIC_IO;
    }
    if (rc != PMIC_OK) return rc;
    p->errors = 0;
    if (h != h2) return PMIC_TORN;
    *raw = (h & 0x3f) << 8 | l;
    return *raw ? PMIC_OK : PMIC_TORN; /* 0: no conversion yet */
}

int pmic_deci(int raw) { return 220 + (7274 - raw) / 2; } /* 22 + (7274 - raw) / 20 C, in tenths */

int pmic_sample(const struct pmic_bus *b, struct pmic *p, int *raw) {
    int v30, v34, rc;
    *raw = -1;
    if (p->state == PMIC_OFF) return GUARD_NONE;
    if ((rc = check(b, p, &v30, &v34)) != PMIC_OK) return GUARD_NONE;
    if (p->state != PMIC_ON || v30 != PMIC_ADC_DIE) { /* the start, or the channel found off again */
        if ((rc = enable(b, p, v30)) != PMIC_OK) return GUARD_NONE;
        p->state = PMIC_ON;
    }
    if (window(b, p, v34, raw) != PMIC_OK) return GUARD_NONE;
    return pmic_deci(*raw);
}
