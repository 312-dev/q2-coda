/* q2radiofetch, the local radio fetcher (docs/internals.md#local-radio-fetch): the stations that
 * reach a place, from the bundled FCC table, and their streams from the web. radiofetch_core.c
 * holds what needs no network (the tables, the JSON and page readers, matching, the list's text)
 * and is tested on this machine by test/unit/test_radiofetch.py; radiofetch.c is the program, its
 * transfers on one libcurl multi handle. */
#ifndef RADIOFETCH_H
#define RADIOFETCH_H

#ifdef RF_HOST
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#else
/* No MIPS sysroot (as q2guard.h): glibc 2.28's declarations, with MIPS o32 open flags. */
typedef unsigned size_t;
typedef int ssize_t;
#define NULL ((void *)0)
int open(const char *, int, ...), close(int), fsync(int), rename(const char *, const char *);
int unlink(const char *), mkdir(const char *, unsigned), flock(int, int), dup2(int, int);
int snprintf(char *, size_t, const char *, ...);
ssize_t read(int, void *, size_t), write(int, const void *, size_t);
long lseek(int, long, int);
void *malloc(size_t), *calloc(size_t, size_t), *realloc(void *, size_t);
void free(void *), exit(int);
void *memcpy(void *, const void *, size_t), *memmove(void *, const void *, size_t);
void *memset(void *, int, size_t);
int memcmp(const void *, const void *, size_t), strcmp(const char *, const char *);
int strncmp(const char *, const char *, size_t);
size_t strlen(const char *);
char *strchr(const char *, int), *strstr(const char *, const char *), *getenv(const char *);
double strtod(const char *, char **);
long strtol(const char *, char **, int);
long time(long *);
struct timespec {
    long tv_sec, tv_nsec;
};
int clock_gettime(int, struct timespec *);
double sin(double), cos(double), asin(double), atan2(double, double), sqrt(double);
#define O_RDONLY 0
#define O_WRONLY 1
#define O_CREAT 0x100
#define O_TRUNC 0x200
#define O_CLOEXEC 0x80000
#define SEEK_SET 0
#define LOCK_EX 2
#define LOCK_NB 4
#define CLOCK_MONOTONIC 1
#endif

/* stations.bin (tools/radiodata.py stations_table): a 16-byte header, then RF_RECORD-byte stations
 * sorted by latitude, then the cities. A station: call (8 bytes, NUL-padded), frequency (u16: FM in
 * 100 kHz, AM in kHz), flags (u8, RF_AM...), 0, latitude and longitude (i32, 1e-5 degrees), its
 * city's offset in the file's pool (u32), RF_RADII reach radii in km (u8) from true north
 * clockwise. */
#define RF_MAGIC "Q2RS"
#define RF_RECORD 40
#define RF_RADII 16
#define RF_AM 1        /* an AM station; else FM */
#define RF_LPFM 2      /* low-power FM */
#define RF_AUDACY 4    /* licensed to Audacy, whose streams are on Amperwave under the call sign */
#define RF_ESTIMATED 8 /* the reach is radiodata.py's AM rule, not an FCC contour */
/* zcta.bin (radiodata.py zcta_table): a bitmap of the ZIPs present, then each one's point as two
 * i16s, latitude and longitude in hundredths of a degree, in ZIP order. */
#define RF_ZIPS 100000

#define RF_STATIONS                                                                                \
    96 /* the most a list holds: the nearest, when more reach a place (an AM-heavy city) */
#define RF_STREAMS 4 /* streams kept per station, the first source's first */
#define RF_URL 300
#define RF_NAME 48
#define RF_CITY 40

typedef struct {
    char src[16], url[RF_URL];
} rf_stream;

typedef struct {
    char call[8], base[6], name[RF_NAME], city[RF_CITY], tunein[16];
    unsigned short freq; /* FM in 100 kHz, AM in kHz */
    unsigned char flags;
    float km; /* from the transmitter */
    int n;    /* streams */
    rf_stream s[RF_STREAMS];
} rf_station;

typedef struct {
    double lat, lon;
    int n;
    rf_station st[RF_STATIONS];
} rf_list;

/* radiofetch_core.c */
double rf_km(double lat1, double lon1, double lat2, double lon2, double *bearing);
int rf_near(const char *table, double lat, double lon, rf_list *out);
int rf_zip(const char *table, const char *zip, double *lat, double *lon);
int rf_geo(const char *body, double *lat, double *lon);

/* A JSON value as rf_json walks to it: ev is one of these; key is the member's name in an object
 * ("" in an array), val the scalar's text, unescaped and cut to its buffer (strings) or as written
 * (numbers, true...). */
enum { RF_J_OBJECT, RF_J_END_OBJECT, RF_J_ARRAY, RF_J_END_ARRAY, RF_J_VALUE };
typedef void (*rf_json_cb)(void *ctx, int ev, int depth, const char *key, const char *val);
int rf_json(const char *s, unsigned n, rf_json_cb cb, void *ctx);

rf_station *rf_find(rf_list *l, const char *base, int am);
int rf_add(rf_station *st, const char *src, const char *url);
int rf_iheart(rf_list *l, const char *body, unsigned n);
int rf_radiobrowser(rf_list *l, const char *body, unsigned n);
int rf_tunein_local(rf_list *l, const char *body, unsigned n);
int rf_tunein_tune(rf_station *st, const char *body, unsigned n);
int rf_triton(rf_station *st, const char *mount, const char *body);
int rf_soundstack(rf_station *st, const char *body);
void rf_mount(const rf_station *st, char *out, int aac);
const char *rf_state_name(const char *code);
int rf_states(const rf_list *l, const char **out, int max);
int rf_text(const rf_list *l, char *out, unsigned cap, long fetched, int complete,
            const char *label);
int rf_header(const char *text, long *fetched, int *complete);
void rf_key(char *out, unsigned cap, const char *zip, double lat, double lon);

#endif
