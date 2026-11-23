/* q2-iap's library (q2_iap_lib.c, docs/iphone-mode.md#the-library-on-idrive): what q2_iap.c takes of it, the tables it
 * hands the iAP2 side as its host's, the files and options main sets and the children's pipes its loop polls. */
#ifndef Q2_IAP_LIB_H
#define Q2_IAP_LIB_H

extern const char *lib_db, *lib_cat, *lib_sent_file, *list_file, *queue_file;
extern const char *card_root;
extern long card_wait_ms;
extern struct iap2_library lib_t;

struct lib_sid;
/* the server songs' stream-path index (lib_stream_rids) and its child */
struct lib_sids {
    struct lib_sid *v; /* the index, sorted; n of them */
    void *block;       /* the allocation v lies in, when it is the index's own (sid_install) */
    unsigned n, cap;
    int fd;               /* the child's pipe while it reads the catalogue, else -1 */
    int asked;            /* a child was started this run: not again */
    unsigned char *in;    /* its bytes so far: a count, then that many rows */
    unsigned in_n, want;  /* want: the bytes it sends, once its count is in, else 0 */
};
extern struct lib_sids sids;
/* the library's read in a child (lib_load) */
struct lib_lload {
    int fd, pid;          /* the child's pipe and pid while it reads, else -1 and 0 */
    int ready;            /* 1 the tables installed, 2 the catalogue unreadable, -1 none read; 0 not yet */
    long at;              /* when it began */
    unsigned char *in;    /* its bytes so far */
    unsigned in_n, in_cap;
};
extern struct lib_lload lload;

int lib_load(void);
int lib_ready(void);
void lib_saved(void);
void lib_play(const unsigned *rids, unsigned n, unsigned at, int shuffle);
int lib_queue(unsigned *out, unsigned cap);
void lib_load_poll(void);
void sid_index_start(void);
void sid_index_poll(void);
unsigned lib_stream_rids(const char *const *paths, unsigned *rids, unsigned n);
int lib_app_album(unsigned rid, char *app, unsigned appcap, char *album, unsigned albumcap);
int lib_write_all(int fd, const void *p, unsigned n);

#endif
