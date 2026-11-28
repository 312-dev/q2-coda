/* PEQ's Auto preamp as the EQ state saves it: the editor's (peq_ui.c) and VampOS's q2playd's one
 * definition, apart from the page so a service without the payload's UI links it with peq.c and
 * hash.c (playd/Makefile). */
#include "offsets.inc" /* FNV_SEED */
#include "peq.h"

/* Auto, PEQ's and 3EQ's: one saved cut that covers every standard PCM rate's peak, so tracks at
 * different rates play at one level and the player works nothing out at a track's start. Kept until
 * a band changes, as preamp, balance and the switch move none: the bands are known again by two
 * FNV-1a hashes of them from two seeds, 8 bytes in place of a copy of the preset. ponytail: two
 * band sets with both hashes equal would share a cut (odds about 1 in 2^64); no bound for
 * nonstandard rates (37.8 kHz and the like), whose peak can sit between two of these. */
#define HEADROOM_SEED2 0x9e3779b9u
double peq_headroom(const peq_preset *p) {
    static const int rates[] = { 8000,  11025, 12000, 16000, 22050,  24000,  32000,  44100,
                                 48000, 64000, 88200, 96000, 176400, 192000, 352800, 384000 };
    static unsigned cached[2];
    static int have_cache;
    static double gain;
    peq_preset key = *p;
    key.preamp = key.balance = 0, key.bypass = key.automatic = key.spare = 0;
    if (!peq_valid(&key)) return p->preamp;
    unsigned h[2] = { hash_bytes(FNV_SEED, (const unsigned char *)&key, sizeof key),
                      hash_bytes(HEADROOM_SEED2, (const unsigned char *)&key, sizeof key) };
    if (have_cache && h[0] == cached[0] && h[1] == cached[1]) return gain;
    gain = 0;
    for (unsigned i = 0; i < sizeof(rates) / sizeof(*rates); ++i) {
        double g = peq_auto(&key, rates[i]);
        if (g < gain) gain = g;
    }
    cached[0] = h[0], cached[1] = h[1];
    have_cache = 1;
    return gain;
}

/* PEQ's Auto (or never picked) preamp as the cut for every rate, which the player plays as saved
 * (docs/internals.md#parametric-eq). 1 if that changed it. */
int eq_auto_level(eq_state *s) {
    if (s->peq.automatic == 2) return 0;
    double cut = peq_headroom(&s->peq);
    int changed = s->peq.automatic != 1 || s->peq.preamp != cut;
    s->peq.automatic = 1, s->peq.preamp = cut;
    return changed;
}
