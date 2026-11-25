/* iAP2's types (docs/iphone-mode.md#iap2): what the Q2 is playing, its library as the car is sent it, and the host
 * q2_iap.c gives the iAP2 side, every file and the USB behind it.
 *
 * The iAP2 side (the iap2_*.c files) is a port of PodBox's firmware/usbstack/usb_iap2.c (the transport and link
 * layer) and the M1 part of usb_iap2_control.c (the control session up to identification, and the answers an iPhone
 * gives right after), Copyright the PodBox and Rockbox authors, under the GNU General Public License version 2 or (at
 * your option) any later version, here under version 3 as the rest of Vamp is. PodBox takes its message IDs and
 * packet layout from lvalen91/carplayd (public domain) and its message order and timing from iPhones in real cars;
 * the traps it found are kept, each with its note. Apple's MFi specification was never consulted, here or there. */
#ifndef IAP2_H
#define IAP2_H

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

/* What the Q2 is playing, for NowPlayingUpdate (the shim's /tmp/coda-rc.now, read by the caller). */
struct iap2_np {
    int state; /* 0 stopped, 1 playing, 2 paused */
    int live;  /* a radio's stream (the shim's live=1): no duration or position, an empty cover */
    unsigned pos_ms, len_ms, qpos, qlen;
    int shuffle, repeat; /* repeat 0 off, 1 one, 2 all */
    unsigned id, qgen; /* the song's library id (songtable's rid, 0 for none) and the queue's generation */
    char title[160], artist[160], album[160], path[400];
};

/* The Q2's library as the car is sent it (q2_iap_lib.c fills it): its songs in rid order, their strings as offsets
 * into pool, each one's hash of what the car is sent of it, the revision, and what the car was last sent in full. */
struct iap2_track {
    unsigned rid, hash, album_id, artist_id, aartist_id, genre_id, composer_id, ms; /* ms: its length, 0 unknown */
    unsigned title, album, artist, aartist, genre, composer; /* offsets into pool */
    unsigned short track, disc;
};
struct iap2_sent {
    unsigned rid, hash;
};
/* A list the car is sent as a playlist: stock's playlists (their ids, 0 to 100), Favorites and Recently Played
 * (LIST_FAVORITES, LIST_RECENT); its name an offset into pool, its songs' rids lrids[at] to lrids[at + n - 1]. */
struct iap2_list {
    unsigned id, name, at, n;
};
#define LIST_FAVORITES 1001
#define LIST_RECENT 1002
struct iap2_library {
    struct iap2_track *tracks;
    unsigned n;
    char *pool, rev[40], sent_rev[40];
    struct iap2_sent *sent;
    unsigned sent_n;
    struct iap2_list *lists; /* in the order their songs go first: Favorites, Recently Played, then stock's */
    unsigned nlists, *lrids;
};

struct iap2_host {
    void (*send)(const u8 *report, unsigned len); /* an input report: [id][link control][data, padded] */
    long (*now)(void);                             /* milliseconds */
    void (*log)(const char *msg);
    int (*now_playing)(struct iap2_np *np);       /* 1 and np filled, or 0: nothing known */
    const char *name, *uuid, *library_id;
    void (*audio)(int on);                         /* the car wants USB audio (1) or no longer (0) */
    void (*command)(const char *verb);             /* a button or a seek: the shim's verbs (coda_rc_shim.c cr_command) */
    unsigned (*artwork)(const struct iap2_np *np, u8 *out, unsigned cap); /* the song's cover, a JPEG, or 0 */
    int (*art_pending)(const struct iap2_np *np); /* 1 while its cover is still looked for, quickly (q2_iap.c) */
    struct iap2_library *lib;   /* the library, filled once lib_load's read is over */
    int (*lib_load)(void);      /* its read begun, off the loop: 0, or -1 with none to send */
    int (*lib_ready)(void);     /* 1 lib filled, 2 the server's catalogue unreadable, -1 none to send, 0 reading */
    void (*lib_saved)(void);    /* the car holds lib's revision whole */
    void (*play)(const unsigned *rids, unsigned n, unsigned at, int shuffle);
    int (*queue)(unsigned *rids, unsigned cap); /* the playing queue's rids, or -1 */
    int (*backlog)(void);     /* input reports handed over and not yet taken by the car */
    void (*link_lost)(void);  /* the link given up on */
    unsigned (*rate)(void);   /* the player's rate for the song about to play, read now */
};

/* The link's packets: FF 5A, a 9-byte header, a checksummed payload */
#define HEADER_LEN 9
#define MAX_PACKET 1024 /* the longest the Q2 sends: the car's certificate, about 950 bytes, fits one */

/* The wire's pace (docs/iphone-mode.md#the-link-s-pace): the BMW takes input reports from an interrupt endpoint, 64
 * bytes every 8 ms, so a 1,024-byte packet is about 170 ms on the wire and its window of 5 most of a second, longer
 * than its 200 ms retransmission timer and its 10 tries. So a packet goes to the wire only while no more than
 * BACKLOG_MAX reports wait for it (a bare acknowledgement always goes: one report), a packet is sent again only after
 * the wire has been idle for the retransmission time, and the bulk (library updates, covers, the queue list) goes in
 * packets of at most BULK_MAX bytes, so an acknowledgement waits behind no more than about 130 ms of it, well inside
 * the car's 200 ms. */
#define BACKLOG_MAX 4
#define BULK_MAX 768

extern const struct iap2_host *H; /* the host of this connection (iap2_connect) */

#endif
