/* card_store.c: records kept on the card, with a RAM copy while it is away
 * (docs/internals.md#where-vamp-writes). The structs are the caller's, zero at start (.scratch or
 * static) but for the names and caps. card_mounted() is the caller's: system.c's in demo. */
#ifndef CARD_STORE_H
#define CARD_STORE_H

#define CARD_STORE_DIR PEQ_ROOT "/mnt/mmc/.q2pod"

typedef struct {
    const char *card;  /* the file on the card */
    const char *flash; /* the flash file it replaces, read until the card has a copy; 0 none */
    unsigned cap;      /* the most the RAM copy holds */
    void *ram;         /* the newest copy while the card is away */
    unsigned len, room;
    int held;  /* ram is newer than the card's file */
    int moved; /* the card has had a copy this run: the flash file is not read again */
} card_blob;

/* b named on first use: the payload has no initialised data (.scratch is zeroed) */
static inline card_blob *card_blob_at(card_blob *b, const char *card, const char *flash, unsigned cap) {
    if (!b->card) b->card = card, b->flash = flash, b->cap = cap;
    return b;
}

/* 1 when the write is kept: on the card, or with the card away in RAM until card_blob_flush gets it
 * there; 0 when a mounted card refuses it (nothing held: the caller retries) or RAM can't hold it */
int card_blob_write(card_blob *b, const void *buf, unsigned n);
/* the newest copy into buf (cap bytes at most): its length, or -1 for none anywhere */
int card_blob_read(card_blob *b, void *buf, unsigned cap);
/* a RAM copy to the card if it is up: 1 when nothing is left held */
int card_blob_flush(card_blob *b);
/* the newest file to read in place, stdio, at its start, or 0 for none: the card's (copied from the
 * flash once), or the flash's while there is no card. A RAM copy is not in it: card_blob_flush first. */
void *card_blob_open(card_blob *b);
/* the record forgotten: the card's file left empty, which reads as none (the flash file is left) */
void card_blob_drop(card_blob *b);

typedef struct {
    const char *card; /* the file on the card, appended to */
    unsigned cap;     /* the RAM ring's bytes while the card is away */
    char *ring;
    unsigned len, dropped; /* bytes held, and whole lines dropped for room since the last flush */
} card_log;

static inline card_log *card_log_at(card_log *l, const char *card, unsigned cap) {
    if (!l->card) l->card = card, l->cap = cap;
    return l;
}

/* s (n bytes, whole lines) appended, after anything held: 1 when kept on the card or in the ring */
int card_log_append(card_log *l, const char *s, unsigned n);
/* the ring appended to the card in order if it is up: 1 when nothing is left held */
int card_log_flush(card_log *l);

#endif
