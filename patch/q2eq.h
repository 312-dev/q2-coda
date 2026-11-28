#ifndef Q2_Q2EQ_H
#define Q2_Q2EQ_H
/* q2eq (docs/adb.md#q2eq), --dev builds only: the EQ editor driven from a computer. The q2eq helper
 * (q2eq.c) writes one q2eq_request to Q2EQ_REQUEST, root's alone (mode 0600), through a temporary
 * name; demo's q2eq_poll (peq_ui.c), every 500 ms of headset_eq_poll, takes it, makes the change
 * through the editor's own actions, and answers with a q2eq_reply in Q2EQ_REPLY ("Q2EQR01", then
 * the struct) and, for a list or an export, text in Q2EQ_TEXT, written first. */
#include "peq_types.h"

#ifndef Q2EQ_ROOT
#define Q2EQ_ROOT "" /* host tests pass a scratch directory */
#endif
#define Q2EQ_REQUEST Q2EQ_ROOT "/tmp/q2eq.req"
#define Q2EQ_REPLY Q2EQ_ROOT "/tmp/q2eq.rep"
#define Q2EQ_TEXT Q2EQ_ROOT "/tmp/q2eq.txt"
#define Q2EQ_IMPORT Q2EQ_ROOT "/tmp/q2eq.in" /* an import's text, the helper's copy */

enum {
    Q2EQ_STATE,  /* nothing changed: the reply alone */
    Q2EQ_LIST,   /* the card's presets, a name a line, in Q2EQ_TEXT */
    Q2EQ_EXPORT, /* the draft as Equalizer APO text, in Q2EQ_TEXT */
    Q2EQ_ON,     /* Advanced's Enabled: arg 0 or 1; 0 turns 3EQ off too */
    Q2EQ_PER_OUTPUT,
    Q2EQ_APPLY,
    Q2EQ_DISCARD,     /* the draft back to what plays, as leaving the page does */
    Q2EQ_RESET,       /* Reset to defaults */
    Q2EQ_PREAMP,      /* text: auto or dB */
    Q2EQ_BALANCE,     /* text: dB, above 0 the left quieter */
    Q2EQ_BAND,        /* band: on (arg 1) or off (0) */
    Q2EQ_CHANNEL,     /* arg: 1 both, 2 left, 3 right */
    Q2EQ_TYPE,        /* arg: PEQ_* */
    Q2EQ_FREQ,        /* text: Hz */
    Q2EQ_Q,           /* text */
    Q2EQ_GAIN,        /* text: dB */
    Q2EQ_LOAD,        /* name: a preset on the card */
    Q2EQ_STOCK,       /* name: Pop to Jazz, into the draft */
    Q2EQ_SAVE,        /* arg: Manual 01 to 10 as 0 to 9 */
    Q2EQ_SAVE_AS,     /* name */
    Q2EQ_RENAME,      /* name to text */
    Q2EQ_DELETE,      /* name */
    Q2EQ_IMPORT_AS,   /* Q2EQ_IMPORT's text saved as name, then loaded */
    Q2EQ_SIMPLE,      /* 3EQ's switch: arg 0 or 1 */
    Q2EQ_SIMPLE_GAIN, /* band 0 to 2 (Low, Mid, High), text: dB */
    Q2EQ_SIMPLE_RESET,
    Q2EQ_SIMPLE_PRESET, /* name: Pop to Jazz, playing at once, as Simple's Presets */
    Q2EQ_OPS
};
#define Q2EQ_NOW 1     /* flags: apply the draft after the change */
#define Q2EQ_REPLACE 2 /* flags: a save over a preset of that name */

typedef struct {
    unsigned nonce; /* echoed in the reply */
    int op, band, arg, flags;
    char output[20]; /* the output the change is for: refused unless it is the one playing */
    char name[256], text[256];
} q2eq_request;

typedef struct {
    unsigned nonce;
    int ok, page, flag, per_output, dirty; /* flag: g_equalizer_flag, the EQ in the chain */
    char message[160];
    char owner[20];               /* the output the playing EQ belongs to, with EQ per Output on */
    char name[256], applied[256]; /* the draft's preset, and the playing one's */
    double automatic;             /* Auto's cut for the draft, dB (peq_level.c peq_headroom) */
    eq_state state;               /* what is saved to play */
    peq_preset draft;
} q2eq_reply;

#endif
