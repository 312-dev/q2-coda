/* The library on iDrive and playing from it (iap2_library.c) */
#ifndef IAP2_LIBRARY_H
#define IAP2_LIBRARY_H
#include "iap2.h"

#define LIB_KEY 0x51320001u /* a song's id's high word (its rid the low), in the library and Now Playing alike */
#define FIRST_MAX 150            /* songs in the first update: about 7 s of the BMW's wire */
#define LISTS_MAX 128
#define LIST_WAIT_MS 5000 /* a list's transfer the car has not started in this long is given up */
enum { LIB_IDLE, LIB_FIRST, LIB_LISTS, LIB_ITEMS, LIB_GONE, LIB_FINAL, LIB_READING };
/* the push (lib_pump, lists_pump) */
struct iap2_lib_push {
    int phase, after, diff, reset, held; /* after: the phase once the lists' transfers end; held: the car has rev */
    unsigned next, gone, mask, items, updates, completes;
    unsigned nf, fi, rec, xi, x_sent; /* the first set's size and cursor; the next record; the list in transfer */
    unsigned first[FIRST_MAX], sorted[FIRST_MAX]; /* the first set's song indexes, in sending order and sorted */
    u8 x_id, x_state, list_ids[LISTS_MAX];         /* x_state: 0 none, 1 set up, 2 its songs going */
    long started, complete_at, x_since;
};
extern struct iap2_lib_push ls;

void send_library_info(void);
void send_library_updates(const char *car_rev, unsigned mask);
void send_library_none(void);
void library_unreadable(void);
void library_read(void);
void lib_pump(void);
void lists_pump(long now);
int list_transfer_word(const u8 *d);
void play_items(const u8 *p, unsigned len);
void play_collection(const u8 *p, unsigned len);
void play_all(void);
void lib_reset(void);

#endif
