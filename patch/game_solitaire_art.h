/* Cabin Solitaire's art file (assets/games/solitaire.bin, usr/share/q2games/solitaire.bin): its layout.
 * Written by the design's src/export.py with the file; do not edit by hand. */
#ifndef GAME_SOLITAIRE_ART_H
#define GAME_SOLITAIRE_ART_H

#define ART_MAGIC 0x314c4f53u
#define ART_BYTES 37180 /* the whole file, its seal the last word */
#define ART_ENTRIES 181 /* from byte 4: offset (bit 31: a palette image), w, h, dx, dy */
#define ART_TILE 22232 /* 64 x 64 fibre steps, 4 bits, 7 is none */
#define ART_SHADE 24280 /* 94 x 80 shade cells of 4 px, 128 is none */
#define ART_SHADE_W 94
#define ART_SHADE_H 80
#define ART_LUT 31800 /* a felt pixel: 15 words a shade level, from ART_LUT_LO, by fibre step */
#define ART_LUT_LO 87
#define ART_LUT_LEVELS 64
#define ART_STYLES 35640 /* per style: 96 entry numbers (255: none), 96 advances in 1/4 px, from ' ' */

enum {
    A_RANK_A = 0,
    A_RANK_2 = 1,
    A_RANK_3 = 2,
    A_RANK_4 = 3,
    A_RANK_5 = 4,
    A_RANK_6 = 5,
    A_RANK_7 = 6,
    A_RANK_8 = 7,
    A_RANK_9 = 8,
    A_RANK_10 = 9,
    A_RANK_J = 10,
    A_RANK_Q = 11,
    A_RANK_K = 12,
    A_SUIT_h = 13,
    A_SUIT_s = 14,
    A_SUIT_d = 15,
    A_SUIT_c = 16,
    A_PIP_h = 17,
    A_PIP_s = 18,
    A_PIP_d = 19,
    A_PIP_c = 20,
    A_ACE_h = 21,
    A_ACE_s = 22,
    A_ACE_d = 23,
    A_ACE_c = 24,
    A_SLOT_h = 25,
    A_SLOT_s = 26,
    A_SLOT_d = 27,
    A_SLOT_c = 28,
    A_RECYCLE = 29,
    A_SLOT_K = 30,
    A_COURT_J_RED = 31,
    A_COURT_J_BLACK = 32,
    A_COURT_Q_RED = 33,
    A_COURT_Q_BLACK = 34,
    A_COURT_K_RED = 35,
    A_COURT_K_BLACK = 36,
    A_BACK = 37,
};
enum { T_LABEL, T_SLIP, T_FIGURE, T_VALUE, T_WOOD, T_HINT, T_TITLE, T_TITLE_L, ART_STYLE_COUNT };

#endif
