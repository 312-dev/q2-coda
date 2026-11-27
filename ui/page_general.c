/* Settings > General, q2ui's own (docs/ui-rewrite.md, step 4): About first, q2ui's page, then the
 * rest of demo's General page through the gate (Software Update, Storage, USB, Date & Time, App
 * Store, Network, Restart, Shut Down, Reset).
 *
 * About lists what demo's lists (patch/nav/chapters_streams_folders.c ringnav_about): the product
 * and this build's version and the stock firmware's, as demo says them (product=, version=,
 * firmware= in its status file, patch/q2ui_gate.c tell: they are demo's build's constants), and what
 * q2ui reads itself: the library's songs, the card's capacity and room left (statvfs of --card, in
 * GB of 10^9 bytes, two decimals) and Wi-Fi's IPv4 address, "Not connected" without one. Read as the
 * page opens; its rows choose nothing. */
#include <arpa/inet.h>
#include <ifaddrs.h>
#include <netinet/in.h>
#include <stdio.h>
#include <string.h>
#include <sys/statvfs.h>

#include "q2ui.h"

const char *general_card = "/mnt/mmc";

enum { A_PRODUCT, A_FIRMWARE, A_SONGS, A_CAPACITY, A_AVAILABLE, A_WIFI, A_ROWS };
static struct {
    char product[32], value[A_ROWS][64];
} about;

static void gb(char *out, size_t room, unsigned long long bytes) {
    snprintf(out, room, "%llu.%02llu GB", bytes / 1000000000ull, bytes % 1000000000ull / 10000000ull);
}
static void wifi_address(char *out, size_t room) {
    struct ifaddrs *all = 0;
    snprintf(out, room, "Not connected");
    if (getifaddrs(&all)) return;
    for (struct ifaddrs *a = all; a; a = a->ifa_next)
        if (a->ifa_addr && a->ifa_addr->sa_family == AF_INET && !strcmp(a->ifa_name, "wlan0")) {
            inet_ntop(AF_INET, &((struct sockaddr_in *)(void *)a->ifa_addr)->sin_addr, out, (socklen_t)room);
            break;
        }
    freeifaddrs(all);
}
static void about_read(void) {
    struct statvfs fs;
    memset(&about, 0, sizeof about);
    if (!status_told_value("product", about.product, sizeof about.product) || !*about.product)
        snprintf(about.product, sizeof about.product, "Vamp");
    status_told_value("version", about.value[A_PRODUCT], sizeof about.value[A_PRODUCT]);
    status_told_value("firmware", about.value[A_FIRMWARE], sizeof about.value[A_FIRMWARE]);
    snprintf(about.value[A_SONGS], sizeof about.value[A_SONGS], "%d", lib_songs());
    if (!statvfs(general_card, &fs)) {
        gb(about.value[A_CAPACITY], sizeof about.value[A_CAPACITY], (unsigned long long)fs.f_blocks * fs.f_frsize);
        gb(about.value[A_AVAILABLE], sizeof about.value[A_AVAILABLE], (unsigned long long)fs.f_bavail * fs.f_frsize);
    }
    wifi_address(about.value[A_WIFI], sizeof about.value[A_WIFI]);
}
static int about_count(void *ctx) {
    (void)ctx;
    return A_ROWS;
}
static void about_row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    static const char *const titles[] = { 0, "Firmware", "Songs", "Capacity", "Available", "Wi-Fi Address" };
    out->title = i == A_PRODUCT ? about.product : titles[i];
    out->value = about.value[i];
}
static void about_chosen(void *ctx, int i) { (void)ctx, (void)i; }
static void about_push(void) {
    about_read();
    ui_source src = { .count = about_count, .row = about_row, .chosen = about_chosen, .name = "about" };
    ui_push(&list_page, &src);
}

static int count(void *ctx) {
    (void)ctx;
    return 2;
}
static void row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    out->title = i ? "More General Settings" : "About", out->flags = ROW_CHEVRON;
}
static void chosen(void *ctx, int i) {
    (void)ctx;
    if (i)
        ui_demo_page("systemset/sysset_page");
    else
        about_push();
}
void general_push(void) {
    ui_source src = { .count = count, .row = row, .chosen = chosen, .name = "general" };
    ui_push(&list_page, &src);
}
