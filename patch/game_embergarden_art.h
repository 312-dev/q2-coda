/* Ember Garden's art (the design's design/games/sky-defense/src/export_retune.py writes this and
 * assets/games/embergarden.bin; do not edit): the
 * file's size and parts, and the scene's places. */
#define EG_ART_BYTES 234036u
#define EG_ART_MAGIC 0x31414745u
#define EG_BAND 32    /* rows a palette */
#define EG_COLOURS 256 /* colours a palette */
#define EG_CH_Y 231   /* the charred boxes: their first row, size and each one's first column */
#define EG_CH_W 34
#define EG_CH_H 41
#define EG_CH_X { 27, 62, 97, 132, 209, 244, 279, 314 }
#define EG_PLUME_X 176 /* a pixel of the ash column's light side */
#define EG_PLUME_Y 65

enum {
    EG_SCENE, /* 375 x 320 */
    EG_PALETTE, /* 256 x 10 */
    EG_CHARRED, /* 34 x 328 */
    EG_FLAME0_0, /* 13 x 22 */
    EG_FLAME0_1, /* 15 x 26 */
    EG_FLAME0_2, /* 15 x 20 */
    EG_FLAME0_3, /* 15 x 23 */
    EG_FLAME1_0, /* 17 x 28 */
    EG_FLAME1_1, /* 19 x 32 */
    EG_FLAME1_2, /* 17 x 25 */
    EG_FLAME1_3, /* 19 x 30 */
    EG_FLAME2_0, /* 21 x 37 */
    EG_FLAME2_1, /* 23 x 42 */
    EG_FLAME2_2, /* 23 x 34 */
    EG_FLAME2_3, /* 23 x 40 */
    EG_FIREGLOW, /* 45 x 27 */
    EG_FIRE0, /* 22 x 18 */
    EG_FIRE1, /* 22 x 16 */
    EG_FIRE2, /* 22 x 20 */
    EG_PUFF1, /* 27 x 27 */
    EG_PUFF2, /* 29 x 29 */
    EG_PUFF3, /* 30 x 31 */
    EG_PUFF4, /* 31 x 32 */
    EG_CLOUD0, /* 73 x 10 */
    EG_CLOUD1, /* 53 x 10 */
    EG_CLOUD2, /* 81 x 10 */
    EG_CLOUD3, /* 45 x 10 */
    EG_BILLOW, /* 24 x 18 */
    EG_VGLOW, /* 61 x 45 */
    EG_LAVA0, /* 26 x 59 */
    EG_LAVA1, /* 26 x 65 */
    EG_LAVA2, /* 26 x 61 */
    EG_SIGN_0, /* 8 x 9 */
    EG_SIGN_1, /* 4 x 9 */
    EG_SIGN_2, /* 7 x 9 */
    EG_SIGN_3, /* 7 x 9 */
    EG_SIGN_4, /* 8 x 9 */
    EG_SIGN_5, /* 7 x 9 */
    EG_SIGN_6, /* 7 x 9 */
    EG_SIGN_7, /* 6 x 9 */
    EG_SIGN_8, /* 7 x 9 */
    EG_SIGN_9, /* 7 x 9 */
    EG_SIGN_COMMA, /* 4 x 5 */
    EG_CARD_0, /* 10 x 12 */
    EG_CARD_1, /* 6 x 12 */
    EG_CARD_2, /* 8 x 12 */
    EG_CARD_3, /* 9 x 12 */
    EG_CARD_4, /* 10 x 12 */
    EG_CARD_5, /* 9 x 12 */
    EG_CARD_6, /* 9 x 12 */
    EG_CARD_7, /* 8 x 12 */
    EG_CARD_8, /* 10 x 12 */
    EG_CARD_9, /* 9 x 12 */
    EG_CARD_COMMA, /* 5 x 6 */
    EG_SMALL_0, /* 8 x 10 */
    EG_SMALL_1, /* 5 x 9 */
    EG_SMALL_2, /* 7 x 9 */
    EG_SMALL_3, /* 7 x 10 */
    EG_SMALL_4, /* 8 x 9 */
    EG_SMALL_5, /* 8 x 10 */
    EG_SMALL_6, /* 8 x 10 */
    EG_SMALL_7, /* 7 x 9 */
    EG_SMALL_8, /* 8 x 10 */
    EG_SMALL_9, /* 8 x 10 */
    EG_SMALL_COMMA, /* 4 x 5 */
    EG_BIG_0, /* 17 x 20 */
    EG_BIG_0_LINE, /* 21 x 25 */
    EG_BIG_1, /* 10 x 20 */
    EG_BIG_1_LINE, /* 15 x 25 */
    EG_BIG_2, /* 14 x 20 */
    EG_BIG_2_LINE, /* 19 x 24 */
    EG_BIG_3, /* 14 x 20 */
    EG_BIG_3_LINE, /* 20 x 25 */
    EG_BIG_4, /* 17 x 20 */
    EG_BIG_4_LINE, /* 22 x 25 */
    EG_BIG_5, /* 15 x 19 */
    EG_BIG_5_LINE, /* 19 x 25 */
    EG_BIG_6, /* 16 x 20 */
    EG_BIG_6_LINE, /* 20 x 25 */
    EG_BIG_7, /* 14 x 19 */
    EG_BIG_7_LINE, /* 19 x 25 */
    EG_BIG_8, /* 16 x 20 */
    EG_BIG_8_LINE, /* 21 x 25 */
    EG_BIG_9, /* 15 x 20 */
    EG_BIG_9_LINE, /* 20 x 25 */
    EG_POP_0, /* 7 x 8 */
    EG_POP_0_LINE, /* 9 x 10 */
    EG_POP_1, /* 4 x 8 */
    EG_POP_1_LINE, /* 6 x 10 */
    EG_POP_2, /* 6 x 8 */
    EG_POP_2_LINE, /* 8 x 10 */
    EG_POP_3, /* 6 x 8 */
    EG_POP_3_LINE, /* 8 x 10 */
    EG_POP_4, /* 7 x 8 */
    EG_POP_4_LINE, /* 9 x 10 */
    EG_POP_5, /* 6 x 8 */
    EG_POP_5_LINE, /* 8 x 10 */
    EG_POP_6, /* 6 x 8 */
    EG_POP_6_LINE, /* 9 x 10 */
    EG_POP_7, /* 6 x 8 */
    EG_POP_7_LINE, /* 8 x 10 */
    EG_POP_8, /* 7 x 8 */
    EG_POP_8_LINE, /* 9 x 10 */
    EG_POP_9, /* 6 x 8 */
    EG_POP_9_LINE, /* 9 x 10 */
    EG_POP_PLUS, /* 6 x 7 */
    EG_POP_PLUS_LINE, /* 9 x 9 */
    EG_T_WAVE_BIG, /* 76 x 19 */
    EG_T_WAVE_BIG_LINE, /* 82 x 25 */
    EG_T_DAWN, /* 98 x 13 */
    EG_T_DAWN_LINE, /* 101 x 15 */
    EG_T_DAY, /* 73 x 13 */
    EG_T_DAY_LINE, /* 76 x 15 */
    EG_T_DUSK, /* 80 x 13 */
    EG_T_DUSK_LINE, /* 84 x 15 */
    EG_T_NIGHT, /* 72 x 13 */
    EG_T_NIGHT_LINE, /* 76 x 15 */
    EG_T_LULL, /* 46 x 9 */
    EG_T_LULL_LINE, /* 49 x 12 */
    EG_T_LAST, /* 38 x 7 */
    EG_T_LAST_LINE, /* 41 x 9 */
    EG_T_NEWBEST, /* 41 x 7 */
    EG_T_TITLE, /* 132 x 13 */
    EG_T_LOST, /* 118 x 13 */
    EG_T_KEEP, /* 133 x 9 */
    EG_T_BEST, /* 25 x 8 */
    EG_T_BESTWAVE, /* 55 x 8 */
    EG_T_SCORE, /* 32 x 8 */
    EG_T_WAVE, /* 28 x 8 */
    EG_T_START, /* 71 x 8 */
    EG_T_REPLANT, /* 84 x 8 */
    EG_PARTS
};
