#ifndef Q2_PEQ_TYPES_H
#define Q2_PEQ_TYPES_H
/* The EQ's presets and state as stored and passed around: peq.h's types that need nothing of the
 * platform, so the q2eq helper (q2eq.c, docs/adb.md#q2eq) shares them with demo. */

#define PEQ_BANDS 30 /* 10 per side plus 10 shared, as L/R APO files use */
/* A band's type (docs/internals.md#parametric-eq): the RBJ Audio EQ Cookbook's biquads, in the
 * order peq_compile designs them and peq_export names them. Only the first three use a gain. */
enum { PEQ_PK, PEQ_LS, PEQ_HS, PEQ_LP, PEQ_HP, PEQ_BP, PEQ_NO, PEQ_AP, PEQ_TYPES };
#define PEQ_GAINED(type) ((type) <= PEQ_HS)
#define PEQ_Q_MAX(type) ((type) == PEQ_NO ? 30 : 10) /* APO's notch without a Q is Q 30 */

/* Versioned disk representation: fixed-width fields, no pointers or implicit padding.
 * enabled: 0 off, 1 both channels, 2 left only, 3 right only (v1 files hold 0 or 1).
 * type: PEQ_* (firmware before the pass, notch, band-pass and all-pass types reads only 0 to 2).
 * balance: dB, above 0 turns the left channel down, below 0 the right (v2 only).
 * automatic: the preamp is 1 Auto (preamp holds the loudest cut over the standard PCM rates, kept
 * so by demo and Vamp OS's q2playd: peq_level.c peq_headroom), 2 picked (an import's own preamp
 * too), 0 never picked (saved before 1.3.6: PEQ_PRESET_V2 bytes, or imported before 1.4.4), which
 * the UI takes as Auto. Every preamp plays as saved. */
typedef struct {
    int enabled, type;
    double frequency, gain, q;
} peq_band;
typedef struct {
    int count, bypass;
    double preamp;
    peq_band bands[PEQ_BANDS];
    double balance;
    int automatic, spare;
} peq_preset;
#define PEQ_PRESET_V2 __builtin_offsetof(peq_preset, automatic)
/* The EQ state (docs/internals.md#3eq), PEQ_ACTIVE's Q2PEQ03 record, replaced whole: which EQ plays
 * (mode, EQ_*), the last applied PEQ and the 3EQ, its three fixed bands (eq3_bands) and their
 * headroom preamp. hold: Off, but the 3EQ's preamp kept while its page compares against bypass. */
enum { EQ_OFF, EQ_3, EQ_PEQ };
typedef struct {
    int mode, hold;
    peq_preset peq, eq3;
} eq_state;

#endif
