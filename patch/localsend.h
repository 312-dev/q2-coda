/* LocalSend v2 (docs/localsend.md): what patch/localsend_core.c, the protocol with no sockets or files, gives the
 * daemon q2send (patch/localsend.c) and test/unit/test_localsend.py. The core calls one platform function,
 * ls_random. */
#ifndef LOCALSEND_H
#define LOCALSEND_H
#include <stddef.h>
#include <stdint.h>

#define LS_PORT 53317
#define LS_GROUP "224.0.0.167"
#define LS_VERSION "2.2"
#define LS_ALIAS 64
#define LS_FP 65        /* uppercase hex SHA-256 and its NUL */
#define LS_PATH 512     /* a received file's path under the Received folder */
#define LS_ID 72        /* a sender's file id (the app's are UUIDs) */
#define LS_TOKEN 72     /* a session id or file token: ours are 32 hex digits, the app's UUIDs */
#define LS_MAX_FILES 4096
#define LS_MAX_PEERS 16
#define LS_PEER_TTL_MS 120000 /* a peer not heard from for this long leaves the list */

void ls_random(void *buf, size_t n); /* the platform's: RAND_bytes on the Q2 */

/* ---- JSON: a tokenizer (jsmn's shape) and the few reads the protocol needs */
enum { LS_J_OBJ = 1, LS_J_ARR, LS_J_STR, LS_J_PRIM };
typedef struct {
    int type, start, end, size; /* [start, end) in the text; size: an object's keys or an array's items */
} ls_tok;
int ls_json_parse(const char *js, int n, ls_tok *t, int nt); /* tokens used, or -1 */
int ls_json_skip(const ls_tok *t, int nt, int i);           /* the token after i and all it holds */
int ls_json_get(const char *js, const ls_tok *t, int nt, int obj, const char *key); /* the value's index or -1 */
int ls_json_str(const char *js, const ls_tok *t, int i, char *out, int cap); /* a string's bytes, unescaped; -1 */
int ls_json_num(const char *js, const ls_tok *t, int i, long long *out);   /* an integer; -1 */
int ls_json_bool(const char *js, const ls_tok *t, int i);                  /* 1, 0, or -1 */
int ls_json_esc(char *out, int cap, const char *s);                        /* s quoted for JSON; -1 */

/* ---- a device's info: an announcement, register's body and answer, prepare-upload's "info" */
typedef struct {
    char alias[LS_ALIAS], model[32], type[16], fp[LS_FP], version[8];
    int port, https, download, announce; /* announce: -1 when absent */
} ls_info;
int ls_info_parse(const char *js, const ls_tok *t, int nt, int obj, ls_info *out); /* 0, or -1 without an alias */
int ls_info_text(const char *js, int n, ls_info *out);                            /* the same from text */
/* me as JSON: with_port adds port and protocol, announce >= 0 adds the flag */
int ls_info_json(char *out, int cap, const ls_info *me, int with_port, int announce);

/* ---- devices found */
typedef struct {
    ls_info info;
    char ip[16];
    uint32_t seen_ms;
} ls_peer;
/* i heard or answered from ip at now: added or refreshed, the oldest replaced when full; its index, or -1 for
 * one carrying self's fingerprint */
int ls_peer_seen(ls_peer *tab, int *n, const ls_info *i, const char *ip, uint32_t now, const char *self);
int ls_peer_expire(ls_peer *tab, int *n, uint32_t now); /* the count removed */
int ls_peer_find(const ls_peer *tab, int n, const char *fp);

/* ---- HTTP: one request or response head, and a chunked body */
typedef struct {
    char method[8], path[256], query[768], ctype[64];
    int status;          /* a response's */
    long long length;    /* Content-Length, or -1 */
    int chunked;         /* Transfer-Encoding: chunked */
} ls_http;
/* The head in buf[0..n): its bytes through the blank line, 0 when more are needed, -1 when it is not HTTP or too
 * long. resp: a status line rather than a request line. */
int ls_http_head(const char *buf, int n, ls_http *h, int resp);
int ls_query(const char *q, const char *key, char *out, int cap); /* key's value, %-decoded; -1 */
typedef struct {
    int st;
    unsigned long long left;
    char line[20];
    int ln;
} ls_chunk;
/* Decodes buf[0..n) in place: the payload bytes moved to buf's start, or -1 on a malformed body; ls_chunk_done
 * once the last chunk and its trailer are in (bytes after it are not read). */
int ls_chunk_feed(ls_chunk *c, unsigned char *buf, int n);
int ls_chunk_done(const ls_chunk *c);

/* ---- a transfer's files and session */
enum { LS_F_SKIP = 0, LS_F_WANT, LS_F_BUSY, LS_F_DONE, LS_F_FAIL };
typedef struct {
    char id[LS_ID], token[LS_TOKEN], name[LS_PATH], sha[LS_FP], type[48];
    long long size, done;
    int state;
} ls_file;
typedef struct {
    char id[LS_TOKEN];
    ls_info from;
    ls_file *f;
    int n, want; /* files, of which accepted */
    long long total;
} ls_session;
/* A sent name made safe for the card: "/"-separated components, none empty, ".", ".." or starting with a dot
 * (-1: the file is skipped, as hidden), no control character (-1); FAT's \ : * ? " < > | become _, a
 * component's trailing spaces and dots go; at most 255 bytes a component and LS_PATH in all (-1). */
int ls_safe_name(const char *in, char *out, int cap);
/* prepare-upload's body into s (s->f malloc'd: ls_session_free): 0, or the HTTP status to answer (400). Each
 * file gets a token; hidden and unsafe names are skipped. */
int ls_prepare_parse(const char *js, int n, ls_session *s);
void ls_session_free(ls_session *s);
int ls_prepare_reply(const ls_session *s, char *out, int cap); /* the 200 body: the session and its tokens */
/* upload's query against s: the file's index, or the status to answer (400 a missing field, 403 a wrong
 * session, file or token, 409 a file already done) */
int ls_upload_lookup(const ls_session *s, const char *query, int *idx);
/* The sender's side: prepare-upload's body for me and files (each wanted), and its 200 answer into s: the
 * session id and each file's token, files left out set to LS_F_SKIP. 0 or -1. */
int ls_prepare_body(char *out, int cap, const ls_info *me, const ls_file *f, int n);
int ls_prepare_answer(const char *js, int n, ls_session *s);
#endif
