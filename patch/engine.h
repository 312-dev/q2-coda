/* The game engine (docs/games-engine.md): what a game written on it sees. One game runs at a time,
 * on Games' sheet (games.c), so the calls below take no handle. */
#ifndef ENGINE_H
#define ENGINE_H

#define ENG_HZ 60     /* steps a second: the panel's refresh (docs/games-engine.md#the-panel) */
#define ENG_STEP 1000 /* a step in the frame timer's unit, 1/60000 s (a millisecond is ENG_HZ) */
#define ENG_MAX_STEPS                                                                              \
    4 /* a late frame catches up this many steps; the rest of the time is dropped */
#define ENG_TIMER_MS 4   /* the frame timer: due at every pass of the UI loop */
#define ENG_SNAP_LO 15   /* a pass this many ms after the last is whole refreshes: one step each */
#define ENG_RECTS 8      /* changed rects a frame; more join the last */
#define ENG_ACCEL_MAX 10 /* fast's most steps a tick (offsets.inc VOL_ACCEL_MAX, the volume's) */
#define ENG_W 375        /* the sheet's width; its height is eng_surface's h (the theme's page) */

/* Fixed point, 16.16. fx_div's quotient is exact to 8 fraction bits and needs |a| < 128.0. */
typedef int fx;
#define FX(n) ((fx)((n) * 65536))
#define FX_INT(a) ((a) >> 16)
static inline fx fx_mul(fx a, fx b) { return (fx)(((long long)a * b) >> 16); }
static inline fx fx_div(fx a, fx b) { return (fx)(((a << 8) / b) << 8); }
/* games.c's tables: an angle is 1024 a turn, a sine 65536 at one. */
int eng_sin(int angle), eng_cos(int angle), eng_atan2(int y, int x);
unsigned eng_sqrt(unsigned long long n);
unsigned rand_below(unsigned n); /* playback.c's shared generator: 0 to n - 1 */

/* The keys, as bits of held, pressed and released. ENG_MENU never reaches a game: it pauses. */
enum {
    ENG_CENTRE = 1,
    ENG_PLAY = 2,
    ENG_PREV = 4,
    ENG_NEXT = 8,
    ENG_TOUCH = 16 /* a finger on the sheet, at tx, ty */
};

/* One step's input. wheel, ticks, fast, pressed and released are a frame's, given to its first
 * step; held and the finger's place stay. */
struct eng_input {
    int wheel;       /* ENG_RAW: the wheel's travel, 200 a turn, clockwise positive */
    int ticks, fast; /* else its detents, clockwise positive; fast is ticks at the wheel's speed */
    unsigned held, pressed, released;
    int tx, ty; /* the finger's place on the sheet, while ENG_TOUCH is held or released */
};

/* What draw paints on: the screen's own pixels (0xffRRGGBB), the sheet's origin at ox, oy, and the
 * rows and columns being painted this pass, x0 <= x < x1 and y0 <= y < y1 on the sheet. Every
 * drawing call clips to them, so draw paints its whole scene each time and pays for the changed
 * part. */
struct eng_surface {
    unsigned *px;
    int stride, ox, oy, w, h;
    int x0, y0, x1, y1;
    void *canvas; /* stock's, for eng_text */
};

enum {
    ENG_RAW = 1,   /* the wheel as travel (wheel), no ticks */
    ENG_STILL = 2, /* turn-based: the frame timer runs only after input and while step returns 1 */
    ENG_FULL = 4   /* the whole screen, 375 x 320, over the status row: Games opens the sheet so */
};

struct eng_game {
    const char *name;
    unsigned size, flags; /* the state's bytes, zeroed at the start; ENG_* */
    void (*init)(void *state, int h);
    int (*step)(void *state, const struct eng_input *in); /* 1/ENG_HZ s; 1: something still moves */
    void (*draw)(const void *state, struct eng_surface *s);
    void (*pause)(
        void *state); /* before the menu, the screen going off or the page closing: save */
};

/* engine.c, for a game's step. */
void eng_dirty(int x, int y, int w, int h); /* this part of the sheet changed: painted next frame */
void eng_dirty_all(void);
int eng_inset(int y); /* the columns the glass's corners hide at each end of the sheet's row y */
void eng_quit(void);  /* back to Games' list, after this step */
void eng_awake(void); /* the screen's timeout starts over, as a key does it */
enum { ENG_SFX_TICK, ENG_SFX_TAP, ENG_SFX_ALERT }; /* the Clicker's wheel, button and beep */
void eng_sfx(int kind);
/* A save, /mnt/data/game-NAME: its first word the magic, its last the sum of the rest, written to
 * a temporary, synced and renamed. eng_load: 0 and buf zeroed unless a whole save was read. */
int eng_load(const char *name, void *buf, unsigned size, unsigned magic);
int eng_save(const char *name, void *buf, unsigned size, unsigned magic);

/* engine.c, for a game's draw. Colours are the screen's, 0xRRGGBB; Light Mode leaves them. */
void eng_fill(struct eng_surface *s, int x, int y, int w, int h, unsigned rgb);
/* a disc with a smoothed edge, its centre and radius in 16.16 so it moves by parts of a pixel */
void eng_disc(struct eng_surface *s, fx cx, fx cy, fx r, unsigned rgb);
void eng_round(struct eng_surface *s, int x, int y, int w, int h, int r, unsigned rgb);
/* ASCII in the theme's face at px, h rows from y: from x (0), centred on it (1) or ending at it
 * (2); returns its width */
int eng_text(struct eng_surface *s, const char *text, int x, int y, int h, unsigned px,
             unsigned rgb, int align);

/* engine.c, for games.c: the page's side. */
int eng_count(void); /* the games in eng_games[], Games' rows after its own */
const struct eng_game *eng_game(int k);
int eng_open(const struct eng_game *game, void *page, void *sheet, int h, void (*held)(void),
             void (*left)(void));
void eng_close(void);
void eng_run(void), eng_hold(void);
int eng_live(void); /* the frame timer runs: the UI loop keeps its pace (navigation.c) */
int eng_key(unsigned key, int down);
int eng_touch(void *ctx, void *event); /* the sheet's and the page's pointer events */
void eng_paint(void *canvas);
/* games.c: a save's seal and its check, and text_at for eng_text */
int games_ok(const void *b, unsigned n, unsigned magic);
void games_seal(void *b, unsigned n, unsigned magic);
int games_text(void *canvas, const char *s, int x, int y, int h, unsigned px, unsigned color,
               int align);

extern const struct eng_game eng_bricks;    /* game_bricks.c */
extern const struct eng_game eng_solitaire; /* game_solitaire.c */
extern const struct eng_game eng_embergarden; /* game_embergarden.c */
extern const struct eng_game eng_demo;      /* engine_demo.c */

#endif
