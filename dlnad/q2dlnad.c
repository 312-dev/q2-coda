/* q2dlnad: VampOS's DLNA renderer (docs/dlnad.md, docs/vamp-os-services.md#dlnaupnp-renderer). A UPnP AV
 * MediaRenderer:1 that a phone's or a computer's control point (BubbleUPnP, a media server's "play to") finds by SSDP
 * and drives over SOAP: AVTransport:1, RenderingControl:1 and ConnectionManager:1, with GENA events. What it plays it
 * hands to q2playd as a Q2PLAY/1 source (docs/platform/playback.md#audio-focus): the URL with its DIDL-Lite title,
 * artist and album; the transport's state, position and volume come back on the watch.
 *
 * Off unless [SYSSET] DLNA=1 in /mnt/data/config.ini, stock's switch, read every 5 s: while off it binds nothing. While
 * it holds the player and plays, it holds the radio up through Q2NET/1 (hold, every 60 s). One thread, one poll loop.
 * --root prefixes every file and socket path, --port and --ssdp-port move the HTTP and SSDP ports, for test/dlnad.py. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define CONNS 8           /* HTTP requests at once */
#define REQ_MAX 16384     /* a request, headers and body */
#define SUBS 8            /* GENA subscriptions */
#define MAX_AGE 1800      /* SSDP's CACHE-CONTROL and a subscription's longest timeout, seconds */
#define ANNOUNCE_S 600    /* ssdp:alive again this often */
#define SETTING_S 5       /* config.ini read this often */
#define HOLD_S 60         /* Q2NET/1's hold sent this often while playing, */
#define HOLD_FOR 120      /* for this many seconds */
#define GROUP "239.255.255.250"

static char root[256], uuid[64], name[64] = "Vamp Q2";
static int http_port = 49494, ssdp_port = 1900;
static volatile sig_atomic_t stopping;

static char *path(const char *rel) {
    static char b[2][700];
    static int i;
    i ^= 1;
    snprintf(b[i], sizeof b[i], "%s%s", root, rel);
    return b[i];
}

static long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static void logf_(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "q2dlnad: ");
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}

static void on_term(int s) { (void)s, stopping = 1; }

/* ---- text ---- */

struct buf { char *p; size_t n, cap; };

static void put(struct buf *b, const char *fmt, ...) {
    va_list ap;
    for (;;) {
        size_t room = b->cap - b->n;
        va_start(ap, fmt);
        int n = b->p ? vsnprintf(b->p + b->n, room, fmt, ap) : -1;
        va_end(ap);
        if (n >= 0 && (size_t)n < room) { b->n += (size_t)n; return; }
        size_t cap = b->cap ? b->cap * 2 : 4096;
        while (n >= 0 && cap - b->n <= (size_t)n) cap *= 2;
        char *g = realloc(b->p, cap);
        if (!g) return;
        b->p = g, b->cap = cap;
    }
}

/* XML's five escapes, for a value in an element or an attribute */
static void put_xml(struct buf *b, const char *s) {
    for (; *s; ++s) {
        if (*s == '&') put(b, "&amp;");
        else if (*s == '<') put(b, "&lt;");
        else if (*s == '>') put(b, "&gt;");
        else if (*s == '"') put(b, "&quot;");
        else if (*s == '\'') put(b, "&apos;");
        else put(b, "%c", *s);
    }
}

/* an element's text back to its bytes: the five named escapes and &#N; / &#xN; (as UTF-8) */
static void unxml(char *s) {
    char *o = s;
    for (char *p = s; *p;) {
        static const struct { const char *e; char c; } E[] = {{"&amp;", '&'}, {"&lt;", '<'}, {"&gt;", '>'}, {"&quot;", '"'}, {"&apos;", '\''}};
        int done = 0;
        for (size_t i = 0; i < sizeof E / sizeof *E && !done; ++i)
            if (!strncmp(p, E[i].e, strlen(E[i].e))) *o++ = E[i].c, p += strlen(E[i].e), done = 1;
        if (!done && p[0] == '&' && p[1] == '#') {
            char *end;
            unsigned long v = p[2] == 'x' || p[2] == 'X' ? strtoul(p + 3, &end, 16) : strtoul(p + 2, &end, 10);
            if (*end == ';' && v && v < 0x110000) {
                if (v < 0x80) *o++ = (char)v;
                else if (v < 0x800) *o++ = (char)(0xc0 | v >> 6), *o++ = (char)(0x80 | (v & 63));
                else if (v < 0x10000) *o++ = (char)(0xe0 | v >> 12), *o++ = (char)(0x80 | (v >> 6 & 63)), *o++ = (char)(0x80 | (v & 63));
                else *o++ = (char)(0xf0 | v >> 18), *o++ = (char)(0x80 | (v >> 12 & 63)), *o++ = (char)(0x80 | (v >> 6 & 63)), *o++ = (char)(0x80 | (v & 63));
                p = end + 1, done = 1;
            }
        }
        if (!done) *o++ = *p++;
    }
    *o = 0;
}

/* the text of the first element named tag (any namespace prefix, e.g. "title" finds <dc:title>), unescaped; 0 none */
static int element(const char *xml, const char *tag, char *out, size_t room) {
    size_t k = strlen(tag);
    for (const char *p = xml; (p = strchr(p, '<'));) {
        const char *n = ++p, *colon;
        size_t len = strcspn(n, " \t\r\n/>");
        if ((colon = memchr(n, ':', len))) len -= (size_t)(colon + 1 - n), n = colon + 1;
        if (len != k || strncmp(n, tag, k)) continue;
        const char *gt = strchr(n, '>');
        if (!gt) return 0;
        if (gt[-1] == '/') { out[0] = 0; return 1; } /* <Tag/> */
        const char *end = strstr(gt + 1, "</");
        while (end) { /* the matching close: </prefix:tag> or </tag> */
            const char *c = end + 2, *cc;
            size_t cl = strcspn(c, ">");
            if ((cc = memchr(c, ':', cl))) cl -= (size_t)(cc + 1 - c), c = cc + 1;
            if (cl == k && !strncmp(c, tag, k)) break;
            end = strstr(end + 2, "</");
        }
        if (!end) return 0;
        size_t n2 = (size_t)(end - gt - 1);
        if (n2 >= room) n2 = room - 1;
        memcpy(out, gt + 1, n2), out[n2] = 0;
        unxml(out);
        return 1;
    }
    return 0;
}

/* Q2PLAY/1's free-text encoding (docs/platform/README.md#the-common-shape) */
static void put_field(struct buf *b, const char *s) {
    for (const unsigned char *p = (const unsigned char *)s; *p; ++p)
        if (*p < 0x21 || *p == '%' || *p == '"' || *p == '=' || *p == 0x7f) put(b, "%%%02X", *p);
        else put(b, "%c", *p);
}

static int hexv(int c) { return isdigit(c) ? c - '0' : isxdigit(c) ? tolower(c) - 'a' + 10 : -1; }

/* a now line's number field */
static int num(const char *line, const char *key) {
    char k[32];
    snprintf(k, sizeof k, " %s=", key);
    const char *p = strstr(line, k);
    return p ? atoi(p + strlen(k)) : 0;
}

static void hms(char *out, size_t room, int s) {
    if (s < 0) s = 0;
    snprintf(out, room, "%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60);
}

/* H:MM:SS, H:MM:SS.F or H:MM:SS.F0/F1 to whole seconds; -1 not a time */
static int seconds(const char *t) {
    int h, m, s;
    if (sscanf(t, "%d:%d:%d", &h, &m, &s) != 3 || h < 0 || m < 0 || m > 59 || s < 0 || s > 59) return -1;
    return h * 3600 + m * 60 + s;
}

/* ---- the renderer's state ---- */

enum { NO_MEDIA, STOPPED, PLAYING, PAUSED, TRANSITIONING };
static const char *const STATE[] = {"NO_MEDIA_PRESENT", "STOPPED", "PLAYING", "PAUSED_PLAYBACK", "TRANSITIONING"};

static struct {
    char uri[1024], meta[4096], title[256], artist[256], album[256];
    int state, held;         /* held: Q2PLAY/1's source is ours */
    int pos, len, volume, muted, unmuted;
    unsigned changed;        /* moves at each change an event carries */
} r = {.state = NO_MEDIA, .volume = -1};

/* ---- Q2PLAY/1 (docs/platform/playback.md) ---- */

static int play_fd = -1;
static char pin[8192];
static size_t pin_n;
static char pend[32]; /* what each answer still awaited is for: h hello, s a source, o anything else */
static unsigned pend_h, pend_t;
static long long play_retry_at;

static void play_close(void) {
    if (play_fd >= 0) close(play_fd), logf_("Q2PLAY/1 went away");
    play_fd = -1, pin_n = 0, pend_h = pend_t = 0;
    if (r.held) r.held = 0, r.state = r.uri[0] ? STOPPED : NO_MEDIA, ++r.changed;
}

static int play_send(char tag, const char *line, size_t n) {
    if (play_fd < 0 || pend_t - pend_h >= sizeof pend) return -1;
    if (send(play_fd, line, n, MSG_NOSIGNAL) != (ssize_t)n) { play_close(); return -1; }
    pend[pend_t++ % sizeof pend] = tag;
    return 0;
}

static int play_cmd(const char *fmt, ...) {
    char line[600];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line, sizeof line - 1, fmt, ap);
    va_end(ap);
    if (n < 0 || n >= (int)sizeof line - 1) return -1;
    line[n++] = '\n';
    return play_send('o', line, (size_t)n);
}

static void play_open(void) {
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    snprintf(a.sun_path, sizeof a.sun_path, "%s", path("/run/vamp/play.sock"));
    play_fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (play_fd < 0 || connect(play_fd, (struct sockaddr *)&a, sizeof a)) {
        if (play_fd >= 0) close(play_fd);
        play_fd = -1, play_retry_at = now_ms() + 2000;
        return;
    }
    play_send('h', "Q2PLAY/1\n", 9);
    play_cmd("watch");
    logf_("Q2PLAY/1 connected");
}

/* the URL set, to Q2PLAY/1 as a source with its DIDL-Lite fields; at most a line (512 bytes) */
static int play_source(void) {
    struct buf b = {0};
    put(&b, "source "), put_field(&b, r.uri);
    if (*r.title) put(&b, " title="), put_field(&b, r.title);
    if (*r.artist) put(&b, " artist="), put_field(&b, r.artist);
    if (*r.album) put(&b, " album="), put_field(&b, r.album);
    put(&b, "\n");
    int ok = b.p && b.n <= 512 && !play_send('s', b.p, b.n);
    if (!ok && b.p && b.n > 512) logf_("a URL too long for a Q2PLAY/1 line (%zu bytes)", b.n);
    free(b.p);
    if (ok) r.state = TRANSITIONING, ++r.changed;
    return ok ? 0 : -1;
}

static void play_line(char *line) {
    if (!strncmp(line, "now ", 4)) {
        int v = num(line, "volume");
        if (v != r.volume) r.volume = v, ++r.changed;
        if (r.held) {
            int st = num(line, "state"), s = st == 1 ? PLAYING : st == 2 ? PAUSED : r.state;
            int pos = num(line, "pos"), len = num(line, "len");
            if (s != r.state) r.state = s, ++r.changed;
            r.pos = pos, r.len = len;
        }
        return;
    }
    if (!strncmp(line, "focus lost", 10)) { /* the source ended, or was taken: a stopped transport */
        logf_("Q2PLAY/1: %s", line);
        r.held = 0, r.state = STOPPED, r.pos = 0, ++r.changed;
        return;
    }
    char tag = pend_h != pend_t ? pend[pend_h++ % sizeof pend] : 0;
    if (tag == 'h' && strcmp(line, "Q2PLAY/1 ok")) logf_("Q2PLAY/1 refused the hello: %s", line), play_close();
    else if (tag == 's') {
        if (!strcmp(line, "ok")) r.held = 1, r.state = PLAYING, r.pos = 0, r.len = 0;
        else logf_("Q2PLAY/1 refused the source: %s", line), r.state = STOPPED;
        ++r.changed;
    }
}

static void play_read(void) {
    ssize_t got = recv(play_fd, pin + pin_n, sizeof pin - 1 - pin_n, 0);
    if (got <= 0) { play_close(), play_retry_at = now_ms() + 2000; return; }
    pin_n += (size_t)got;
    char *p = pin, *nl;
    while (play_fd >= 0 && (nl = memchr(p, '\n', pin_n - (size_t)(p - pin)))) {
        *nl = 0;
        play_line(p);
        p = nl + 1;
    }
    if (play_fd < 0) return;
    pin_n -= (size_t)(p - pin);
    memmove(pin, p, pin_n);
    if (pin_n >= sizeof pin - 1) pin_n = 0;
}

/* ---- Q2NET/1's hold (docs/platform/network.md): one short connection a hold ---- */

static long long held_at;

static void net_hold(void) {
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    snprintf(a.sun_path, sizeof a.sun_path, "%s", path("/run/vamp/net.sock"));
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return;
    char line[64];
    int n = snprintf(line, sizeof line, "Q2NET/1\nhold %d\n", HOLD_FOR);
    if (connect(fd, (struct sockaddr *)&a, sizeof a) || send(fd, line, (size_t)n, MSG_NOSIGNAL) != n) logf_("no Q2NET/1 to hold the radio");
    close(fd);
}

/* ---- the device and its services (UPnP Device Architecture 1.0, UPnP AV 1) ---- */

static const char *const SERVICES[] = {"AVTransport", "RenderingControl", "ConnectionManager"};
#define NSERV 3
#define DEVICE_TYPE "urn:schemas-upnp-org:device:MediaRenderer:1"

/* what the Q2's player plays (docs/player.md#formats) as UPnP's protocolInfo */
static const char SINK[] =
    "http-get:*:audio/mpeg:*,http-get:*:audio/mp3:*,http-get:*:audio/flac:*,http-get:*:audio/x-flac:*,"
    "http-get:*:audio/wav:*,http-get:*:audio/x-wav:*,http-get:*:audio/L16:*,http-get:*:audio/mp4:*,"
    "http-get:*:audio/x-m4a:*,http-get:*:audio/aac:*,http-get:*:audio/ogg:*,http-get:*:audio/x-ape:*,"
    "http-get:*:audio/x-aiff:*,http-get:*:audio/aiff:*,http-get:*:audio/dsf:*,http-get:*:audio/x-dsf:*,"
    "http-get:*:audio/x-dff:*,http-get:*:audio/opus:*";

static void description(struct buf *b) {
    put(b, "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<root xmlns=\"urn:schemas-upnp-org:device-1-0\">"
           "<specVersion><major>1</major><minor>0</minor></specVersion><device><deviceType>" DEVICE_TYPE "</deviceType>"
           "<friendlyName>");
    put_xml(b, name);
    put(b, "</friendlyName><manufacturer>Vamp</manufacturer><modelName>VampOS</modelName><modelNumber>1</modelNumber>"
           "<UDN>%s</UDN><serviceList>", uuid);
    for (int i = 0; i < NSERV; ++i)
        put(b, "<service><serviceType>urn:schemas-upnp-org:service:%s:1</serviceType><serviceId>urn:upnp-org:serviceId:%s"
               "</serviceId><SCPDURL>/%s.xml</SCPDURL><controlURL>/ctl/%s</controlURL><eventSubURL>/evt/%s</eventSubURL>"
               "</service>", SERVICES[i], SERVICES[i], SERVICES[i], SERVICES[i], SERVICES[i]);
    put(b, "</serviceList></device></root>\n");
}

/* Each service's SCPD from a table: an action is "Name in:Arg:Var ... out:Arg:Var ..."; a state variable is
 * "name type evented [allowed,values]" (the first letter of evented y or n). */
static const char *const AVT_ACTIONS[] = {
    "SetAVTransportURI in:InstanceID:A_ARG_TYPE_InstanceID in:CurrentURI:AVTransportURI in:CurrentURIMetaData:AVTransportURIMetaData",
    "GetMediaInfo in:InstanceID:A_ARG_TYPE_InstanceID out:NrTracks:NumberOfTracks out:MediaDuration:CurrentMediaDuration "
    "out:CurrentURI:AVTransportURI out:CurrentURIMetaData:AVTransportURIMetaData out:NextURI:NextAVTransportURI "
    "out:NextURIMetaData:NextAVTransportURIMetaData out:PlayMedium:PlaybackStorageMedium out:RecordMedium:RecordStorageMedium "
    "out:WriteStatus:RecordMediumWriteStatus",
    "GetTransportInfo in:InstanceID:A_ARG_TYPE_InstanceID out:CurrentTransportState:TransportState "
    "out:CurrentTransportStatus:TransportStatus out:CurrentSpeed:TransportPlaySpeed",
    "GetPositionInfo in:InstanceID:A_ARG_TYPE_InstanceID out:Track:CurrentTrack out:TrackDuration:CurrentTrackDuration "
    "out:TrackMetaData:CurrentTrackMetaData out:TrackURI:CurrentTrackURI out:RelTime:RelativeTimePosition "
    "out:AbsTime:AbsoluteTimePosition out:RelCount:RelativeCounterPosition out:AbsCount:AbsoluteCounterPosition",
    "GetDeviceCapabilities in:InstanceID:A_ARG_TYPE_InstanceID out:PlayMedia:PossiblePlaybackStorageMedia "
    "out:RecMedia:PossibleRecordStorageMedia out:RecQualityModes:PossibleRecordQualityModes",
    "GetTransportSettings in:InstanceID:A_ARG_TYPE_InstanceID out:PlayMode:CurrentPlayMode out:RecQualityMode:CurrentRecordQualityMode",
    "GetCurrentTransportActions in:InstanceID:A_ARG_TYPE_InstanceID out:Actions:CurrentTransportActions",
    "Stop in:InstanceID:A_ARG_TYPE_InstanceID",
    "Play in:InstanceID:A_ARG_TYPE_InstanceID in:Speed:TransportPlaySpeed",
    "Pause in:InstanceID:A_ARG_TYPE_InstanceID",
    "Seek in:InstanceID:A_ARG_TYPE_InstanceID in:Unit:A_ARG_TYPE_SeekMode in:Target:A_ARG_TYPE_SeekTarget",
    "Next in:InstanceID:A_ARG_TYPE_InstanceID",
    "Previous in:InstanceID:A_ARG_TYPE_InstanceID",
    0};
static const char *const AVT_VARS[] = {
    "TransportState string n STOPPED,PLAYING,PAUSED_PLAYBACK,TRANSITIONING,NO_MEDIA_PRESENT",
    "TransportStatus string n OK,ERROR_OCCURRED", "PlaybackStorageMedium string n NETWORK,NONE",
    "RecordStorageMedium string n NOT_IMPLEMENTED", "PossiblePlaybackStorageMedia string n", "PossibleRecordStorageMedia string n",
    "CurrentPlayMode string n NORMAL", "TransportPlaySpeed string n 1", "RecordMediumWriteStatus string n NOT_IMPLEMENTED",
    "CurrentRecordQualityMode string n NOT_IMPLEMENTED", "PossibleRecordQualityModes string n", "NumberOfTracks ui4 n",
    "CurrentTrack ui4 n", "CurrentTrackDuration string n", "CurrentMediaDuration string n", "CurrentTrackMetaData string n",
    "CurrentTrackURI string n", "AVTransportURI string n", "AVTransportURIMetaData string n", "NextAVTransportURI string n",
    "NextAVTransportURIMetaData string n", "RelativeTimePosition string n", "AbsoluteTimePosition string n",
    "RelativeCounterPosition i4 n", "AbsoluteCounterPosition i4 n", "CurrentTransportActions string n", "LastChange string y",
    "A_ARG_TYPE_SeekMode string n REL_TIME,ABS_TIME", "A_ARG_TYPE_SeekTarget string n", "A_ARG_TYPE_InstanceID ui4 n", 0};
static const char *const RC_ACTIONS[] = {
    "ListPresets in:InstanceID:A_ARG_TYPE_InstanceID out:CurrentPresetNameList:PresetNameList",
    "SelectPreset in:InstanceID:A_ARG_TYPE_InstanceID in:PresetName:A_ARG_TYPE_PresetName",
    "GetMute in:InstanceID:A_ARG_TYPE_InstanceID in:Channel:A_ARG_TYPE_Channel out:CurrentMute:Mute",
    "SetMute in:InstanceID:A_ARG_TYPE_InstanceID in:Channel:A_ARG_TYPE_Channel in:DesiredMute:Mute",
    "GetVolume in:InstanceID:A_ARG_TYPE_InstanceID in:Channel:A_ARG_TYPE_Channel out:CurrentVolume:Volume",
    "SetVolume in:InstanceID:A_ARG_TYPE_InstanceID in:Channel:A_ARG_TYPE_Channel in:DesiredVolume:Volume", 0};
static const char *const RC_VARS[] = {"PresetNameList string n", "LastChange string y", "Mute boolean n",
                                      "Volume ui2 n 0..100", "A_ARG_TYPE_Channel string n Master",
                                      "A_ARG_TYPE_InstanceID ui4 n", "A_ARG_TYPE_PresetName string n FactoryDefaults", 0};
static const char *const CM_ACTIONS[] = {
    "GetProtocolInfo out:Source:SourceProtocolInfo out:Sink:SinkProtocolInfo",
    "GetCurrentConnectionIDs out:ConnectionIDs:CurrentConnectionIDs",
    "GetCurrentConnectionInfo in:ConnectionID:A_ARG_TYPE_ConnectionID out:RcsID:A_ARG_TYPE_RcsID "
    "out:AVTransportID:A_ARG_TYPE_AVTransportID out:ProtocolInfo:A_ARG_TYPE_ProtocolInfo "
    "out:PeerConnectionManager:A_ARG_TYPE_ConnectionManager out:PeerConnectionID:A_ARG_TYPE_ConnectionID "
    "out:Direction:A_ARG_TYPE_Direction out:Status:A_ARG_TYPE_ConnectionStatus", 0};
static const char *const CM_VARS[] = {
    "SourceProtocolInfo string y", "SinkProtocolInfo string y", "CurrentConnectionIDs string y",
    "A_ARG_TYPE_ConnectionStatus string n OK,ContentFormatMismatch,InsufficientBandwidth,UnreliableChannel,Unknown",
    "A_ARG_TYPE_ConnectionManager string n", "A_ARG_TYPE_Direction string n Input,Output", "A_ARG_TYPE_ProtocolInfo string n",
    "A_ARG_TYPE_ConnectionID i4 n", "A_ARG_TYPE_AVTransportID i4 n", "A_ARG_TYPE_RcsID i4 n", 0};
static const char *const *const ACTIONS[] = {AVT_ACTIONS, RC_ACTIONS, CM_ACTIONS}, *const *const VARS[] = {AVT_VARS, RC_VARS, CM_VARS};

static void scpd(struct buf *b, int s) {
    put(b, "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<scpd xmlns=\"urn:schemas-upnp-org:service-1-0\">"
           "<specVersion><major>1</major><minor>0</minor></specVersion><actionList>");
    for (const char *const *a = ACTIONS[s]; *a; ++a) {
        const char *p = *a, *sp = strchr(p, ' ');
        put(b, "<action><name>%.*s</name><argumentList>", (int)(sp ? sp - p : (long)strlen(p)), p);
        for (p = sp; p && *p;) {
            const char *w = p + 1, *e = strchr(w, ' '), *c1 = strchr(w, ':'), *c2 = c1 ? strchr(c1 + 1, ':') : 0;
            size_t n = e ? (size_t)(e - w) : strlen(w);
            if (c2 && c2 < w + n)
                put(b, "<argument><name>%.*s</name><direction>%.*s</direction><relatedStateVariable>%.*s</relatedStateVariable></argument>",
                    (int)(c2 - c1 - 1), c1 + 1, (int)(c1 - w), w, (int)(w + n - c2 - 1), c2 + 1);
            p = e;
        }
        put(b, "</argumentList></action>");
    }
    put(b, "</actionList><serviceStateTable>");
    for (const char *const *v = VARS[s]; *v; ++v) {
        char nm[64], type[16], ev[4], allowed[200] = "";
        if (sscanf(*v, "%63s %15s %3s %199s", nm, type, ev, allowed) < 3) continue;
        put(b, "<stateVariable sendEvents=\"%s\"><name>%s</name><dataType>%s</dataType>", *ev == 'y' ? "yes" : "no", nm, type);
        char *dots = strstr(allowed, "..");
        if (dots) *dots = 0, put(b, "<allowedValueRange><minimum>%s</minimum><maximum>%s</maximum><step>1</step></allowedValueRange>", allowed, dots + 2);
        else if (*allowed) {
            put(b, "<allowedValueList>");
            for (char *t = strtok(allowed, ","); t; t = strtok(0, ",")) put(b, "<allowedValue>%s</allowedValue>", t);
            put(b, "</allowedValueList>");
        }
        put(b, "</stateVariable>");
    }
    put(b, "</serviceStateTable></scpd>\n");
}

/* ---- events (GENA): LastChange for AVTransport and RenderingControl, the three variables for ConnectionManager ---- */

static void actions_now(char *out, size_t room) {
    snprintf(out, room, "%s", !r.uri[0] ? "" : r.state == PLAYING ? "Pause,Stop,Seek" : r.state == PAUSED ? "Play,Stop,Seek" : "Play");
}

static void last_change(struct buf *b, int s) {
    struct buf e = {0};
    if (s == 0) {
        char d[16], acts[32];
        hms(d, sizeof d, r.len), actions_now(acts, sizeof acts);
        put(&e, "<Event xmlns=\"urn:schemas-upnp-org:metadata-1-0/AVT/\"><InstanceID val=\"0\"><TransportState val=\"%s\"/>"
                "<TransportStatus val=\"OK\"/><CurrentTrackURI val=\"", STATE[r.state]);
        put_xml(&e, r.uri);
        put(&e, "\"/><AVTransportURI val=\"");
        put_xml(&e, r.uri);
        put(&e, "\"/><AVTransportURIMetaData val=\"");
        put_xml(&e, r.meta);
        put(&e, "\"/><CurrentTrackDuration val=\"%s\"/><CurrentTransportActions val=\"%s\"/></InstanceID></Event>", d, acts);
    } else {
        put(&e, "<Event xmlns=\"urn:schemas-upnp-org:metadata-1-0/RCS/\"><InstanceID val=\"0\"><Volume channel=\"Master\" val=\"%d\"/>"
                "<Mute channel=\"Master\" val=\"%d\"/></InstanceID></Event>", r.muted ? r.unmuted : r.volume < 0 ? 0 : r.volume, r.muted);
    }
    put(b, "<LastChange>");
    if (e.p) put_xml(b, e.p);
    put(b, "</LastChange>");
    free(e.p);
}

static void propertyset(struct buf *b, int s) {
    put(b, "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<e:propertyset xmlns:e=\"urn:schemas-upnp-org:event-1-0\">");
    if (s == 2) put(b, "<e:property><SourceProtocolInfo></SourceProtocolInfo></e:property><e:property><SinkProtocolInfo>%s"
                       "</SinkProtocolInfo></e:property><e:property><CurrentConnectionIDs>0</CurrentConnectionIDs></e:property>", SINK);
    else put(b, "<e:property>"), last_change(b, s), put(b, "</e:property>");
    put(b, "</e:propertyset>\n");
}

static struct sub { int on, service; unsigned seq; long long until; char sid[80], host[64], path[256]; int port; } subs[SUBS];
static unsigned sid_n;

/* one NOTIFY to a subscriber's callback, waited for at most a second in all */
static void notify(struct sub *u) {
    struct buf body = {0}, req = {0};
    propertyset(&body, u->service);
    put(&req, "NOTIFY %s HTTP/1.1\r\nHOST: %s:%d\r\nCONTENT-TYPE: text/xml; charset=\"utf-8\"\r\nNT: upnp:event\r\n"
              "NTS: upnp:propchange\r\nSID: %s\r\nSEQ: %u\r\nCONTENT-LENGTH: %zu\r\nCONNECTION: close\r\n\r\n%s",
        u->path, u->host, u->port, u->sid, u->seq, body.n, body.p ? body.p : "");
    u->seq = u->seq == 0xffffffffu ? 1 : u->seq + 1;
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons((unsigned short)u->port)};
    int fd = inet_pton(AF_INET, u->host, &a.sin_addr) == 1 ? socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0) : -1;
    if (fd >= 0 && (!connect(fd, (struct sockaddr *)&a, sizeof a) || errno == EINPROGRESS)) {
        struct pollfd p = {.fd = fd, .events = POLLOUT};
        int err = 0;
        socklen_t el = sizeof err;
        if (poll(&p, 1, 500) == 1 && !getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &el) && !err) {
            size_t off = 0;
            long long end = now_ms() + 500;
            while (off < req.n && now_ms() < end) {
                ssize_t w = send(fd, req.p + off, req.n - off, MSG_NOSIGNAL);
                if (w > 0) off += (size_t)w;
                else if (errno == EAGAIN) poll(&p, 1, 50);
                else break;
            }
            p.events = POLLIN;
            char ans[256];
            if (off == req.n && poll(&p, 1, 300) == 1) (void)!recv(fd, ans, sizeof ans, 0);
        }
    }
    if (fd >= 0) close(fd);
    free(body.p), free(req.p);
}

static unsigned told;

static void notify_changes(void) {
    if (told == r.changed) return;
    told = r.changed;
    long long t = now_ms();
    for (int i = 0; i < SUBS; ++i)
        if (subs[i].on && subs[i].until < t) subs[i].on = 0;
        else if (subs[i].on && subs[i].service < 2) notify(&subs[i]);
}

/* ---- SOAP ---- */

struct reply { int code; struct buf body; const char *ctype; char extra[300]; };

static void soap_ok(struct reply *o, int s, const char *action, const char *fmt, ...) {
    put(&o->body, "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\" "
                  "s:encodingStyle=\"http://schemas.xmlsoap.org/soap/encoding/\"><s:Body><u:%sResponse "
                  "xmlns:u=\"urn:schemas-upnp-org:service:%s:1\">", action, SERVICES[s]);
    if (fmt) {
        char *args = 0;
        va_list ap;
        va_start(ap, fmt);
        if (vasprintf(&args, fmt, ap) < 0) args = 0;
        va_end(ap);
        if (args) put(&o->body, "%s", args), free(args);
    }
    put(&o->body, "</u:%sResponse></s:Body></s:Envelope>\n", action);
    o->code = 200, o->ctype = "text/xml; charset=\"utf-8\"";
}

static void soap_fault(struct reply *o, int code, const char *what) {
    put(&o->body, "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\" "
                  "s:encodingStyle=\"http://schemas.xmlsoap.org/soap/encoding/\"><s:Body><s:Fault><faultcode>s:Client</faultcode>"
                  "<faultstring>UPnPError</faultstring><detail><UPnPError xmlns=\"urn:schemas-upnp-org:control-1-0\">"
                  "<errorCode>%d</errorCode><errorDescription>%s</errorDescription></UPnPError></detail></s:Fault></s:Body>"
                  "</s:Envelope>\n", code, what);
    o->code = 500, o->ctype = "text/xml; charset=\"utf-8\"";
}

/* an argument's value, escaped again for a response */
static char *esc(const char *s) {
    struct buf b = {0};
    put(&b, "%s", "");
    put_xml(&b, s);
    return b.p;
}

static void avtransport(struct reply *o, const char *act, const char *body) {
    char v[4096], d[16], t[16];
    if (!strcmp(act, "SetAVTransportURI")) {
        if (!element(body, "CurrentURI", r.uri, sizeof r.uri)) return soap_fault(o, 402, "Invalid Args");
        if (!element(body, "CurrentURIMetaData", r.meta, sizeof r.meta)) r.meta[0] = 0;
        r.title[0] = r.artist[0] = r.album[0] = 0;
        if (*r.meta) {
            element(r.meta, "title", r.title, sizeof r.title);
            if (!element(r.meta, "artist", r.artist, sizeof r.artist)) element(r.meta, "creator", r.artist, sizeof r.artist);
            element(r.meta, "album", r.album, sizeof r.album);
        }
        logf_("URI set: %s", r.uri);
        int was = r.state;
        r.pos = r.len = 0;
        if (!*r.uri) r.state = NO_MEDIA, ++r.changed;
        else if ((was == PLAYING || was == TRANSITIONING) && play_source()) return soap_fault(o, 716, "Resource not found");
        else if (was != PLAYING && was != TRANSITIONING) {
            if (r.held) play_cmd("focus release"), r.held = 0;
            r.state = STOPPED, ++r.changed;
        }
        return soap_ok(o, 0, act, 0);
    }
    if (!strcmp(act, "Play")) {
        if (!*r.uri) return soap_fault(o, 701, "Transition not available");
        if (r.held && r.state == PAUSED) play_cmd("resume");
        else if (!r.held && r.state != TRANSITIONING && play_source()) return soap_fault(o, 716, "Resource not found");
        return soap_ok(o, 0, act, 0);
    }
    if (!strcmp(act, "Pause")) {
        if (!r.held || r.state != PLAYING) return soap_fault(o, 701, "Transition not available");
        play_cmd("pause");
        return soap_ok(o, 0, act, 0);
    }
    if (!strcmp(act, "Stop")) {
        if (r.held) play_cmd("focus release"), r.held = 0;
        if (*r.uri) r.state = STOPPED, r.pos = 0, ++r.changed;
        return soap_ok(o, 0, act, 0);
    }
    if (!strcmp(act, "Seek")) {
        char unit[32] = "";
        element(body, "Unit", unit, sizeof unit);
        if (strcmp(unit, "REL_TIME") && strcmp(unit, "ABS_TIME")) return soap_fault(o, 710, "Seek mode not supported");
        int s = element(body, "Target", v, sizeof v) ? seconds(v) : -1;
        if (s < 0) return soap_fault(o, 711, "Illegal seek target");
        if (!r.held) return soap_fault(o, 701, "Transition not available");
        play_cmd("seek %d", s);
        r.pos = s;
        return soap_ok(o, 0, act, 0);
    }
    if (!strcmp(act, "Next") || !strcmp(act, "Previous")) return soap_fault(o, 701, "Transition not available");
    if (!strcmp(act, "GetTransportInfo"))
        return soap_ok(o, 0, act, "<CurrentTransportState>%s</CurrentTransportState><CurrentTransportStatus>OK"
                                  "</CurrentTransportStatus><CurrentSpeed>1</CurrentSpeed>", STATE[r.state]);
    if (!strcmp(act, "GetPositionInfo")) {
        char *u = esc(r.uri), *m = esc(r.meta);
        hms(d, sizeof d, r.len), hms(t, sizeof t, r.pos);
        soap_ok(o, 0, act, "<Track>%d</Track><TrackDuration>%s</TrackDuration><TrackMetaData>%s</TrackMetaData><TrackURI>%s"
                           "</TrackURI><RelTime>%s</RelTime><AbsTime>%s</AbsTime><RelCount>2147483647</RelCount>"
                           "<AbsCount>2147483647</AbsCount>", *r.uri ? 1 : 0, d, m ? m : "", u ? u : "", t, t);
        free(u), free(m);
        return;
    }
    if (!strcmp(act, "GetMediaInfo")) {
        char *u = esc(r.uri), *m = esc(r.meta);
        hms(d, sizeof d, r.len);
        soap_ok(o, 0, act, "<NrTracks>%d</NrTracks><MediaDuration>%s</MediaDuration><CurrentURI>%s</CurrentURI>"
                           "<CurrentURIMetaData>%s</CurrentURIMetaData><NextURI></NextURI><NextURIMetaData></NextURIMetaData>"
                           "<PlayMedium>%s</PlayMedium><RecordMedium>NOT_IMPLEMENTED</RecordMedium><WriteStatus>NOT_IMPLEMENTED"
                           "</WriteStatus>", *r.uri ? 1 : 0, d, u ? u : "", m ? m : "", *r.uri ? "NETWORK" : "NONE");
        free(u), free(m);
        return;
    }
    if (!strcmp(act, "GetDeviceCapabilities"))
        return soap_ok(o, 0, act, "<PlayMedia>NETWORK</PlayMedia><RecMedia>NOT_IMPLEMENTED</RecMedia><RecQualityModes>"
                                  "NOT_IMPLEMENTED</RecQualityModes>");
    if (!strcmp(act, "GetTransportSettings"))
        return soap_ok(o, 0, act, "<PlayMode>NORMAL</PlayMode><RecQualityMode>NOT_IMPLEMENTED</RecQualityMode>");
    if (!strcmp(act, "GetCurrentTransportActions")) {
        actions_now(v, sizeof v);
        return soap_ok(o, 0, act, "<Actions>%s</Actions>", v);
    }
    soap_fault(o, 401, "Invalid Action");
}

static void rendering(struct reply *o, const char *act, const char *body) {
    char v[64];
    if (!strcmp(act, "ListPresets")) return soap_ok(o, 1, act, "<CurrentPresetNameList>FactoryDefaults</CurrentPresetNameList>");
    if (!strcmp(act, "SelectPreset")) return soap_ok(o, 1, act, 0);
    if (!strcmp(act, "GetVolume")) return soap_ok(o, 1, act, "<CurrentVolume>%d</CurrentVolume>", r.muted ? r.unmuted : r.volume < 0 ? 0 : r.volume);
    if (!strcmp(act, "GetMute")) return soap_ok(o, 1, act, "<CurrentMute>%d</CurrentMute>", r.muted);
    if (!strcmp(act, "SetVolume")) {
        char *end;
        long n = element(body, "DesiredVolume", v, sizeof v) ? strtol(v, &end, 10) : -1;
        if (n < 0 || n > 100 || !*v || *end) return soap_fault(o, 402, "Invalid Args");
        if (r.muted) r.unmuted = (int)n, ++r.changed; /* heard when the mute is lifted */
        else play_cmd("volume %ld", n);
        return soap_ok(o, 1, act, 0);
    }
    if (!strcmp(act, "SetMute")) { /* Q2PLAY/1 has no mute: volume 0, and the level kept to set again */
        int m = element(body, "DesiredMute", v, sizeof v) && (!strcmp(v, "1") || !strcasecmp(v, "true"));
        if (m && !r.muted) r.unmuted = r.volume < 0 ? 0 : r.volume, r.muted = 1, play_cmd("volume 0"), ++r.changed;
        else if (!m && r.muted) r.muted = 0, play_cmd("volume %d", r.unmuted), ++r.changed;
        return soap_ok(o, 1, act, 0);
    }
    soap_fault(o, 401, "Invalid Action");
}

static void connections(struct reply *o, const char *act) {
    if (!strcmp(act, "GetProtocolInfo")) return soap_ok(o, 2, act, "<Source></Source><Sink>%s</Sink>", SINK);
    if (!strcmp(act, "GetCurrentConnectionIDs")) return soap_ok(o, 2, act, "<ConnectionIDs>0</ConnectionIDs>");
    if (!strcmp(act, "GetCurrentConnectionInfo"))
        return soap_ok(o, 2, act, "<RcsID>0</RcsID><AVTransportID>0</AVTransportID><ProtocolInfo></ProtocolInfo>"
                                  "<PeerConnectionManager></PeerConnectionManager><PeerConnectionID>-1</PeerConnectionID>"
                                  "<Direction>Input</Direction><Status>OK</Status>");
    soap_fault(o, 401, "Invalid Action");
}

/* ---- HTTP ---- */

static int service_of(const char *p, const char *prefix) {
    size_t k = strlen(prefix);
    for (int i = 0; i < NSERV; ++i)
        if (!strncmp(p, prefix, k) && !strcmp(p + k, SERVICES[i])) return i;
    return -1;
}

/* a request header's value (case-insensitive name), trimmed; "" none */
static void header(const char *head, const char *nm, char *out, size_t room) {
    size_t k = strlen(nm);
    out[0] = 0;
    for (const char *l = strstr(head, "\r\n"); l && l[2]; l = strstr(l + 2, "\r\n")) {
        const char *h = l + 2;
        if (!strncasecmp(h, nm, k) && h[k] == ':') {
            h += k + 1;
            while (*h == ' ' || *h == '\t') ++h;
            size_t n = strcspn(h, "\r\n");
            while (n && (h[n - 1] == ' ' || h[n - 1] == '\t')) --n;
            snprintf(out, room, "%.*s", (int)n, h);
            return;
        }
    }
}

static void subscribe(struct reply *o, const char *method, const char *head, int s) {
    char sid[80], cb[300], nt[32], to[32];
    header(head, "SID", sid, sizeof sid), header(head, "CALLBACK", cb, sizeof cb), header(head, "NT", nt, sizeof nt);
    header(head, "TIMEOUT", to, sizeof to);
    int secs = !strncasecmp(to, "Second-", 7) && isdigit((unsigned char)to[7]) ? atoi(to + 7) : MAX_AGE;
    if (secs <= 0 || secs > MAX_AGE) secs = MAX_AGE;
    o->ctype = 0;
    if (!strcmp(method, "UNSUBSCRIBE")) {
        o->code = 412;
        for (int i = 0; i < SUBS; ++i)
            if (subs[i].on && !strcmp(subs[i].sid, sid)) subs[i].on = 0, o->code = 200;
        return;
    }
    if (*sid) { /* a renewal */
        if (*cb || *nt) { o->code = 400; return; }
        for (int i = 0; i < SUBS; ++i)
            if (subs[i].on && !strcmp(subs[i].sid, sid) && subs[i].service == s) {
                subs[i].until = now_ms() + secs * 1000LL, o->code = 200;
                snprintf(o->extra, sizeof o->extra, "SID: %s\r\nTIMEOUT: Second-%d\r\n", sid, secs);
                return;
            }
        o->code = 412;
        return;
    }
    char host[64] = "", pth[256] = "/";
    int port = 80;
    if (strcmp(nt, "upnp:event") || sscanf(cb, "<http://%63[0-9.]:%d%255[^>]>", host, &port, pth) < 2) {
        if (sscanf(cb, "<http://%63[0-9.]%255[^>]>", host, pth) < 1 || strcmp(nt, "upnp:event") || pth[0] != '/') { o->code = 412; return; }
        port = 80;
    }
    if (pth[0] != '/') snprintf(pth, sizeof pth, "/");
    long long t = now_ms();
    int i = 0;
    while (i < SUBS && subs[i].on && subs[i].until >= t) ++i;
    if (i == SUBS) { o->code = 500; return; }
    struct sub *u = &subs[i];
    *u = (struct sub){.on = 1, .service = s, .until = t + secs * 1000LL, .port = port};
    snprintf(u->sid, sizeof u->sid, "uuid:%s-%08x", uuid + 5, ++sid_n);
    snprintf(u->host, sizeof u->host, "%s", host), snprintf(u->path, sizeof u->path, "%s", pth);
    o->code = 200;
    snprintf(o->extra, sizeof o->extra, "SID: %s\r\nTIMEOUT: Second-%d\r\n", u->sid, secs);
    u->service = s | 0x100; /* the first event goes once the answer is sent (UDA 1.0: after the SUBSCRIBE's 200) */
}

static void handle(const char *req, size_t head_n, const char *body, struct reply *o) {
    char method[16] = "", target[300] = "";
    sscanf(req, "%15s %299s", method, target);
    char *head = strndup(req, head_n);
    int s;
    if (!head) { o->code = 500; return; }
    if (!strcmp(method, "GET") || !strcmp(method, "HEAD")) {
        o->ctype = "text/xml; charset=\"utf-8\"";
        if (!strcmp(target, "/description.xml")) description(&o->body), o->code = 200;
        else {
            char svc[64];
            o->code = 404;
            for (int i = 0; i < NSERV; ++i)
                if (snprintf(svc, sizeof svc, "/%s.xml", SERVICES[i]), !strcmp(target, svc)) scpd(&o->body, i), o->code = 200;
        }
    } else if (!strcmp(method, "POST") && (s = service_of(target, "/ctl/")) >= 0) {
        char sa[200], act[64] = "";
        header(head, "SOAPACTION", sa, sizeof sa);
        const char *hash = strchr(sa, '#');
        if (hash) snprintf(act, sizeof act, "%.*s", (int)strcspn(hash + 1, "\""), hash + 1);
        if (!*act) soap_fault(o, 401, "Invalid Action");
        else if (s == 0) avtransport(o, act, body);
        else if (s == 1) rendering(o, act, body);
        else connections(o, act);
    } else if ((!strcmp(method, "SUBSCRIBE") || !strcmp(method, "UNSUBSCRIBE")) && (s = service_of(target, "/evt/")) >= 0) {
        subscribe(o, method, head, s);
    } else o->code = !strcmp(method, "POST") || !strcmp(method, "SUBSCRIBE") ? 404 : 405;
    if (!strcmp(method, "HEAD")) o->body.n = 0;
    free(head);
}

static struct conn { int fd; char *in; size_t n; long long until; } conns[CONNS];
static int http_fd = -1, ssdp_fd = -1;

static void conn_close(struct conn *c) {
    close(c->fd), c->fd = -1;
    free(c->in), c->in = 0, c->n = 0;
}

static void reply_send(struct conn *c, struct reply *o) {
    static const struct { int code; const char *what; } W[] = {{200, "OK"}, {400, "Bad Request"}, {404, "Not Found"},
                                                               {405, "Method Not Allowed"}, {412, "Precondition Failed"},
                                                               {413, "Payload Too Large"}, {500, "Internal Server Error"}};
    const char *what = "Error";
    for (size_t i = 0; i < sizeof W / sizeof *W; ++i)
        if (W[i].code == o->code) what = W[i].what;
    struct buf h = {0};
    put(&h, "HTTP/1.1 %d %s\r\nSERVER: Linux/3.10 UPnP/1.0 q2dlnad/1\r\nCONTENT-LENGTH: %zu\r\nCONNECTION: close\r\n%s%s%s%s\r\n",
        o->code, what, o->body.n, o->ctype ? "CONTENT-TYPE: " : "", o->ctype ? o->ctype : "", o->ctype ? "\r\n" : "", o->extra);
    int fl = fcntl(c->fd, F_GETFL);
    fcntl(c->fd, F_SETFL, fl & ~O_NONBLOCK);
    struct timeval tv = {.tv_sec = 2};
    setsockopt(c->fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    if (h.p) (void)!send(c->fd, h.p, h.n, MSG_NOSIGNAL);
    if (o->body.n) (void)!send(c->fd, o->body.p, o->body.n, MSG_NOSIGNAL);
    free(h.p);
}

static void conn_read(struct conn *c) {
    if (!c->in && !(c->in = malloc(REQ_MAX + 1))) { conn_close(c); return; }
    ssize_t got = recv(c->fd, c->in + c->n, REQ_MAX - c->n, 0);
    if (got <= 0) { conn_close(c); return; }
    c->n += (size_t)got;
    c->in[c->n] = 0;
    char *end = strstr(c->in, "\r\n\r\n");
    struct reply o = {0};
    if (!end) {
        if (c->n < REQ_MAX) return;
        o.code = 413;
    } else {
        char cl[16];
        size_t head_n = (size_t)(end - c->in) + 2;
        char *hd = strndup(c->in, head_n);
        header(hd ? hd : "", "CONTENT-LENGTH", cl, sizeof cl);
        free(hd);
        size_t want = (size_t)strtoul(cl, 0, 10), have = c->n - (size_t)(end + 4 - c->in);
        if (want > REQ_MAX - (size_t)(end + 4 - c->in)) o.code = 413;
        else if (have < want) return;
        else end[4 + want] = 0, handle(c->in, head_n, end + 4, &o);
    }
    reply_send(c, &o);
    free(o.body.p);
    conn_close(c);
    for (int i = 0; i < SUBS; ++i) /* a new subscription's first event, now that its 200 is out */
        if (subs[i].on && subs[i].service & 0x100) subs[i].service &= 0xff, notify(&subs[i]);
}

/* ---- SSDP (UDA 1.0 section 1) ---- */

/* this side's address toward `to`: what a route to it would leave from */
static void local_addr(const struct sockaddr_in *to, char *out, size_t room) {
    struct sockaddr_in me;
    socklen_t ml = sizeof me;
    int fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    snprintf(out, room, "127.0.0.1");
    if (fd >= 0 && !connect(fd, (const struct sockaddr *)to, sizeof *to) && !getsockname(fd, (struct sockaddr *)&me, &ml))
        inet_ntop(AF_INET, &me.sin_addr, out, (socklen_t)room);
    if (fd >= 0) close(fd);
}

/* the five (NT, USN) pairs a root device with three services answers for */
static int target(int i, char *nt, size_t ntn, char *usn, size_t usnn) {
    if (i == 0) snprintf(nt, ntn, "upnp:rootdevice"), snprintf(usn, usnn, "%s::upnp:rootdevice", uuid);
    else if (i == 1) snprintf(nt, ntn, "%s", uuid), snprintf(usn, usnn, "%s", uuid);
    else if (i == 2) snprintf(nt, ntn, DEVICE_TYPE), snprintf(usn, usnn, "%s::" DEVICE_TYPE, uuid);
    else if (i < 3 + NSERV)
        snprintf(nt, ntn, "urn:schemas-upnp-org:service:%s:1", SERVICES[i - 3]), snprintf(usn, usnn, "%s::%s", uuid, nt);
    else return 0;
    return 1;
}

static void ssdp_notify(const char *nts) {
    struct sockaddr_in g = {.sin_family = AF_INET, .sin_port = htons(1900)};
    inet_pton(AF_INET, GROUP, &g.sin_addr);
    char me[64], nt[128], usn[200], m[700];
    local_addr(&g, me, sizeof me);
    for (int i = 0; target(i, nt, sizeof nt, usn, sizeof usn); ++i) {
        int n = !strcmp(nts, "ssdp:alive")
                    ? snprintf(m, sizeof m, "NOTIFY * HTTP/1.1\r\nHOST: " GROUP ":1900\r\nCACHE-CONTROL: max-age=%d\r\n"
                                            "LOCATION: http://%s:%d/description.xml\r\nNT: %s\r\nNTS: ssdp:alive\r\n"
                                            "SERVER: Linux/3.10 UPnP/1.0 q2dlnad/1\r\nUSN: %s\r\n\r\n", MAX_AGE, me, http_port, nt, usn)
                    : snprintf(m, sizeof m, "NOTIFY * HTTP/1.1\r\nHOST: " GROUP ":1900\r\nNT: %s\r\nNTS: ssdp:byebye\r\nUSN: %s\r\n\r\n", nt, usn);
        sendto(ssdp_fd, m, (size_t)n, 0, (struct sockaddr *)&g, sizeof g);
    }
}

static void ssdp_read(void) {
    char m[1500], st[200], mx[16], man[64], nt[128], usn[200], me[64], a[800];
    struct sockaddr_in from;
    socklen_t fl = sizeof from;
    ssize_t n = recvfrom(ssdp_fd, m, sizeof m - 1, 0, (struct sockaddr *)&from, &fl);
    if (n <= 0) return;
    m[n] = 0;
    if (strncmp(m, "M-SEARCH * HTTP/1.1\r\n", 21)) return;
    header(m, "ST", st, sizeof st), header(m, "MAN", man, sizeof man), header(m, "MX", mx, sizeof mx);
    if (strcmp(man, "\"ssdp:discover\"")) return;
    local_addr(&from, me, sizeof me);
    for (int i = 0; target(i, nt, sizeof nt, usn, sizeof usn); ++i) {
        if (strcmp(st, "ssdp:all") && strcmp(st, nt)) continue;
        int k = snprintf(a, sizeof a, "HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=%d\r\nEXT:\r\nLOCATION: http://%s:%d/description.xml\r\n"
                                      "SERVER: Linux/3.10 UPnP/1.0 q2dlnad/1\r\nST: %s\r\nUSN: %s\r\n\r\n", MAX_AGE, me, http_port, nt, usn);
        sendto(ssdp_fd, a, (size_t)k, 0, (struct sockaddr *)&from, fl);
    }
}

/* ---- on and off: [SYSSET] DLNA in stock's config.ini ---- */

static int setting_on(void) {
    FILE *f = fopen(path("/mnt/data/config.ini"), "r");
    char l[256];
    int in = 0, on = 0;
    while (f && fgets(l, sizeof l, f)) {
        if (l[0] == '[') in = !strncmp(l, "[SYSSET]", 8);
        else if (in && !strncmp(l, "DLNA=", 5)) on = atoi(l + 5) == 1;
    }
    if (f) fclose(f);
    return on;
}

static int start(void) {
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons((unsigned short)http_port), .sin_addr.s_addr = htonl(INADDR_ANY)};
    int one = 1;
    http_fd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (http_fd < 0 || setsockopt(http_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one) || bind(http_fd, (struct sockaddr *)&a, sizeof a) ||
        listen(http_fd, CONNS)) {
        logf_("HTTP port %d: %s", http_port, strerror(errno));
        if (http_fd >= 0) close(http_fd);
        http_fd = -1;
        return -1;
    }
    a.sin_port = htons((unsigned short)ssdp_port);
    ssdp_fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    struct ip_mreq mr = {.imr_interface.s_addr = htonl(INADDR_ANY)};
    inet_pton(AF_INET, GROUP, &mr.imr_multiaddr);
    if (ssdp_fd < 0 || setsockopt(ssdp_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one) || bind(ssdp_fd, (struct sockaddr *)&a, sizeof a)) {
        logf_("SSDP port %d: %s", ssdp_port, strerror(errno));
        if (ssdp_fd >= 0) close(ssdp_fd);
        close(http_fd), http_fd = ssdp_fd = -1;
        return -1;
    }
    if (setsockopt(ssdp_fd, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mr, sizeof mr)) logf_("no multicast yet (%s): searches sent to it go unheard", strerror(errno));
    unsigned char ttl = 4;
    setsockopt(ssdp_fd, IPPROTO_IP, IP_MULTICAST_TTL, &ttl, sizeof ttl);
    ssdp_notify("ssdp:alive");
    logf_("on: %s at port %d", uuid, http_port);
    return 0;
}

static void finish(void) {
    if (http_fd < 0) return;
    ssdp_notify("ssdp:byebye");
    for (int i = 0; i < CONNS; ++i)
        if (conns[i].fd >= 0) conn_close(&conns[i]);
    for (int i = 0; i < SUBS; ++i) subs[i].on = 0;
    close(http_fd), close(ssdp_fd), http_fd = ssdp_fd = -1;
    if (r.held) play_cmd("focus release"), r.held = 0;
    r.state = r.uri[0] ? STOPPED : NO_MEDIA, ++r.changed;
    logf_("off");
}

/* uuid:5661-6d70 ("Vamp") and the Wi-Fi MAC: the same device every boot, with nothing stored */
static void make_uuid(void) {
    FILE *f = fopen(path("/sys/class/net/wlan0/address"), "r");
    char mac[32] = "", hex[13] = "000000000000";
    if (f && fgets(mac, sizeof mac, f)) {
        int j = 0;
        for (char *p = mac; *p && j < 12; ++p)
            if (hexv((unsigned char)*p) >= 0) hex[j++] = (char)tolower((unsigned char)*p);
    }
    if (f) fclose(f);
    snprintf(uuid, sizeof uuid, "uuid:0a2f2a3e-5661-6d70-0000-%s", hex);
}

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--root") && i + 1 < argc) snprintf(root, sizeof root, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--port") && i + 1 < argc) http_port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--ssdp-port") && i + 1 < argc) ssdp_port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--name") && i + 1 < argc) snprintf(name, sizeof name, "%s", argv[++i]);
        else return fprintf(stderr, "usage: q2dlnad [--root DIR] [--port N] [--ssdp-port N] [--name NAME]\n"), 2;
    }
    signal(SIGPIPE, SIG_IGN), signal(SIGTERM, on_term), signal(SIGINT, on_term);
    make_uuid();
    for (int i = 0; i < CONNS; ++i) conns[i].fd = -1;
    long long checked = -SETTING_S * 1000LL, announced = 0;
    while (!stopping) {
        long long t = now_ms();
        if (t - checked >= SETTING_S * 1000LL) {
            checked = t;
            int on = setting_on();
            if (on && http_fd < 0 && !start()) announced = t;
            else if (!on && http_fd >= 0) finish();
        }
        if (play_fd < 0 && t >= play_retry_at) play_open();
        if (http_fd >= 0 && t - announced >= ANNOUNCE_S * 1000LL) ssdp_notify("ssdp:alive"), announced = t;
        if (r.held && r.state == PLAYING && t - held_at >= HOLD_S * 1000LL) net_hold(), held_at = t;
        if (!r.held) held_at = 0;
        notify_changes();
        struct pollfd p[3 + CONNS];
        int n = 0;
        p[n++] = (struct pollfd){.fd = http_fd, .events = POLLIN};
        p[n++] = (struct pollfd){.fd = ssdp_fd, .events = POLLIN};
        p[n++] = (struct pollfd){.fd = play_fd, .events = POLLIN};
        for (int i = 0; i < CONNS; ++i) p[n++] = (struct pollfd){.fd = conns[i].fd, .events = POLLIN};
        if (poll(p, (nfds_t)n, 500) < 0 && errno != EINTR) break;
        if (p[0].fd >= 0 && p[0].revents & POLLIN) {
            int fd = accept4(http_fd, 0, 0, SOCK_NONBLOCK | SOCK_CLOEXEC), i = 0;
            while (fd >= 0 && i < CONNS && conns[i].fd >= 0) ++i;
            if (fd >= 0 && i == CONNS) close(fd);
            else if (fd >= 0) conns[i] = (struct conn){.fd = fd, .until = now_ms() + 5000};
        }
        if (p[1].fd >= 0 && p[1].revents & POLLIN) ssdp_read();
        if (p[2].fd >= 0 && p[2].fd == play_fd && p[2].revents) play_read();
        t = now_ms();
        for (int i = 0; i < CONNS; ++i) {
            if (conns[i].fd >= 0 && p[3 + i].fd == conns[i].fd && p[3 + i].revents) conn_read(&conns[i]);
            if (conns[i].fd >= 0 && t > conns[i].until) conn_close(&conns[i]); /* a request not finished in 5 s */
        }
    }
    finish();
    return 0;
}
