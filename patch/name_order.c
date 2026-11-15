/* A sorted library query's order kept across loads (docs/internals.md#name-order).
 * toolsQueryDbTable (with its sort argument) and toolsQueryDbTableAndSort (in every mode) sort a
 * query's records in C, algo_sort_if with one of stock's name comparators, and on a large library
 * that sort is most of a list's open: in the emulator, 2.4-2.7 s of a 3.2-3.6 s Songs open at
 * 20,103 songs, every open. The records and the comparator stay the same until the library changes,
 * so the order does too.
 *
 * After a sorted load this keeps each record's id in the order stock's sort left them, and the
 * records' multiset hash, under the query's text and sort (library_query's order:
 * toolsQueryDbTable's argument, or 0x100 and toolsQueryDbTableAndSort's mode). The next load of the
 * same query while library_gen has not moved asks toolsQueryDbTable for it unsorted (the same exec
 * and row callback) and moves the records into the kept order (rec_permute). The hash is the proof:
 * the same records come in the same order (the query's rows), so stock's sort would make the same
 * comparisons and the same order, equal names included. Another record count or hash, an id not
 * kept or a repeated one, and the load runs again sorted, as every load did before. */
#include "navigation.h"

#define NO_SLOTS 4
#define NO_MIN 512 /* below this many records the sort is a few ms: nothing kept */

typedef struct {
    unsigned key, gen, n, used; /* gen: library_gen + 1 when kept, 0 an empty slot */
    unsigned long long sum;
    int *ids; /* REC_ID in the sorted order */
} no_slot;
static struct {
    no_slot s[NO_SLOTS];
    unsigned tick;
} no __attribute__((section(".scratch")));

static unsigned long long fnv64(unsigned long long h, const char *s) {
    if (s)
        while (*s) h = (h ^ (unsigned char)*s++) * 0x100000001b3ull;
    return (h ^ 0xff) * 0x100000001b3ull; /* a field's end: "ab","c" is not "a","bc" */
}
/* What the comparators read (the title, the file name, the type) and the record's id. */
static unsigned long long rec_hash(void *r) {
    char id[24];
    tk_snprintf(id, sizeof id, "%d/%d", I(r, REC_ID), I(r, REC_TYPE));
    return fnv64(fnv64(fnv64(0xcbf29ce484222325ull, id), P(r, REC_TITLE)), P(r, REC_NAME));
}
static unsigned long long list_hash(void *list) {
    unsigned long long sum = 0;
    for (unsigned i = 0, n = deque_size(list); i < n; ++i) sum += rec_hash(deque_at(list, i));
    return sum;
}
/* The query's text, its sort argument and which name comparator stock picks (NAME_SORT_FLAG). */
static unsigned query_key(const char *sql, int sort) {
    unsigned h = 0x811c9dc5u ^ (unsigned)sort ^ (unsigned)B(NAME_SORT_FLAG, 0) << 16;
    while (*sql) h = (h ^ (unsigned char)*sql++) * 0x01000193u;
    return h;
}

static no_slot *slot_of(unsigned key) {
    for (int i = 0; i < NO_SLOTS; ++i)
        if (no.s[i].gen == library_gen + 1 && no.s[i].key == key) return &no.s[i];
    return 0;
}

/* The kept order for this query when the library has not changed since, else 0. */
void *name_order_find(const char *sql, int sort) {
    if (!sql || !sort || pthread_self() != ui_thread) return 0;
    return slot_of(query_key(sql, sort));
}

/* list (the unsorted load) moved into s's order; 0 when it is not s's records. */
int name_order_apply(void *s, void *list) {
    no_slot *k = s;
    unsigned n = deque_size(list), m = 2;
    if (n != k->n || list_hash(list) != k->sum) return 0;
    while (m < 2 * n) m *= 2;
    unsigned *slot = calloc(m + n, sizeof *slot), *at = slot + m;
    if (!slot) return 0;
    /* the kept ids' places, by id: slot holds place + 1 */
    for (unsigned i = 0; i < n; ++i) {
        unsigned h = (unsigned)k->ids[i] * 0x9e3779b1u & (m - 1);
        while (slot[h] && k->ids[slot[h] - 1] != k->ids[i]) h = (h + 1) & (m - 1);
        if (slot[h]) return free(slot), 0; /* a repeated id: no one place for it */
        slot[h] = i + 1;
    }
    for (unsigned i = 0; i < n; ++i) at[i] = n;
    for (unsigned i = 0; i < n; ++i) {
        int id = I(deque_at(list, i), REC_ID);
        unsigned h = (unsigned)id * 0x9e3779b1u & (m - 1);
        while (slot[h] && k->ids[slot[h] - 1] != id) h = (h + 1) & (m - 1);
        if (!slot[h] || at[slot[h] - 1] != n) return free(slot), 0;
        at[slot[h] - 1] = i; /* that place takes record i */
    }
    rec_permute(list, at);
    free(slot);
    k->used = ++no.tick;
    return 1;
}

/* After a sorted load of sql: its order kept, in the least recently used slot. */
void name_order_keep(const char *sql, int sort, void *list) {
    unsigned n = list ? deque_size(list) : 0;
    if (!sql || !sort || n < NO_MIN || pthread_self() != ui_thread) return;
    unsigned key = query_key(sql, sort);
    no_slot *k = slot_of(key);
    if (!k)
        for (int i = 0; i < NO_SLOTS; ++i)
            if (!k || no.s[i].used < k->used) k = &no.s[i];
    for (int i = 0; i < NO_SLOTS; ++i) /* an older library's order of the same query goes */
        if (no.s[i].key == key && &no.s[i] != k) no.s[i].gen = 0;
    int *ids = k->n == n && k->ids ? k->ids : realloc(k->ids, n * sizeof *ids);
    if (!ids) {
        free(k->ids), k->ids = 0, k->gen = k->n = 0;
        return;
    }
    for (unsigned i = 0; i < n; ++i) ids[i] = I(deque_at(list, i), REC_ID);
    k->ids = ids, k->n = n, k->key = key, k->sum = list_hash(list);
    k->gen = library_gen + 1, k->used = ++no.tick;
}
