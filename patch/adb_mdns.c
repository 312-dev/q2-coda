/* q2adb-mdns: the Wi-Fi adbd's mDNS advertisement (docs/adb.md#adb-over-wi-fi), so `vamp find` and
 * `vamp connect NAME` reach the Q2 by name on any network with no address typed:
 *
 *   _adb._tcp.local                 PTR  <name>._adb._tcp.local      <name>: the Bluetooth name
 *   <name>._adb._tcp.local          SRV  0 0 5555 q2muse-<6 hex>.local
 *   <name>._adb._tcp.local          TXT  id=<device id, 16 hex> v=1
 *   q2muse-<6 hex>.local            A    wlan0's address
 *
 * It is coda-rc's responder (coda_rc_mdns.c) with the service type set to _adb, the type modern adb's
 * own discovery lists (`adb mdns services`): this adbd is 4.2.2's, which has no TLS and so no
 * _adb-tls-connect._tcp. The device id and the host name are Car Remote's (/mnt/data/coda-rc/id, made
 * here as coda-rc makes it when neither has run yet), so both responders give the Q2 one A record.
 * q2adb-wifi (patch/adb_wifi.sh) runs it beside adbd --tcp and stops it with that adbd, so the Q2 is
 * advertised only while the port is open; SIGTERM sends the goodbye.
 *
 *   q2adb-mdns [--port 5555] [--if wlan0] [--root DIR]
 *
 * --root puts every path under DIR, for test/unit/test_adb_mdns.py. ponytail: the address is looked at
 * every 5 s (coda_rc_mdns.c MDNS_CHECK_IDLE_MS), one ioctl a wake-up; a netlink socket would wake it
 * only on a change. */
#include "coda_rc_sys.h"
#include "coda_rc.h"

#define ID_FILE "/mnt/data/coda-rc/id"

static char root[256];
static int port = 5555;
uint8_t crc_device_id[8];
static volatile int stopping;

const char *crc_path(char *buf, size_t cap, const char *path) {
    snprintf(buf, cap, "%s%s", root, path);
    return buf;
}

/* q2adb-wifi sends adbd's and this program's output to /dev/null: a line here is for a test's stderr */
void crc_log(const char *fmt, ...) {
    char b[256];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b - 1, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof b - 2) n = sizeof b - 2;
    b[n++] = '\n';
    (void)!write(2, b, n);
}

uint32_t crc_now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint32_t)(t.tv_sec * 1000u + t.tv_nsec / 1000000);
}

int crc_tcp_port(void) { return port; }

static int read_bytes(const char *path, void *buf, size_t n) {
    char p[320];
    int fd = open(crc_path(p, sizeof p, path), O_RDONLY | O_CLOEXEC), got = 0;
    if (fd < 0) return -1;
    while ((size_t)got < n) {
        int r = (int)read(fd, (char *)buf + got, n - got);
        if (r <= 0) break;
        got += r;
    }
    close(fd);
    return got;
}

/* coda-rc's id (coda_rc.c device_id), or a new one written the way it writes it */
static int device_id(void) {
    char p[320], t[330];
    if (read_bytes(ID_FILE, crc_device_id, 8) == 8) return 1;
    if (read_bytes("/dev/urandom", crc_device_id, 8) != 8) return 0;
    mkdir(crc_path(p, sizeof p, "/mnt/data/coda-rc"), 0700);
    crc_path(p, sizeof p, ID_FILE);
    snprintf(t, sizeof t, "%s.new", p);
    int fd = open(t, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600), ok = fd >= 0;
    if (ok) {
        ok = write(fd, crc_device_id, 8) == 8;
        ok &= fsync(fd) == 0;
        ok &= close(fd) == 0;
    }
    if (!ok || rename(t, p)) unlink(t);
    return 1; /* an id that could not be saved still names the Q2 until it restarts */
}

static void on_term(int sig) {
    (void)sig;
    stopping = 1;
}

int main(int argc, char **argv) {
    const char *ifname = "wlan0";
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--port") && i + 1 < argc) port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--if") && i + 1 < argc) ifname = argv[++i];
        else if (!strcmp(argv[i], "--root") && i + 1 < argc) snprintf(root, sizeof root, "%s", argv[++i]);
        else {
            crc_log("usage: q2adb-mdns [--port N] [--if IFNAME] [--root DIR]");
            return 2;
        }
    }
    if (port < 1 || port > 65535) {
        crc_log("q2adb-mdns: --port wants 1 to 65535");
        return 2;
    }
    if (!device_id()) {
        crc_log("q2adb-mdns: no /dev/urandom");
        return 2;
    }
    signal(SIGTERM, on_term);
    signal(SIGHUP, on_term);
    mdns_service("_adb");
    mdns_start(ifname);
    mdns_pace(1); /* no client is ever "connected" here: the idle pace */
    while (!stopping) {
        mdns_poll(crc_now_ms());
        uint32_t due = mdns_due(crc_now_ms());
        struct pollfd f = {mdns_fd(), POLLIN, 0};
        int r = poll(&f, f.fd >= 0 ? 1 : 0, due > 60000 ? 60000 : (int)due);
        if (r > 0 && f.revents & POLLIN) mdns_handle();
    }
    mdns_stop();
    return 0;
}
