/* Videos (docs/internals.md#videos), demo's side: a video file in Folder view (the card's Videos
 * folder, browsed from the Library's Videos row) starts q2video (patch/video.c), which draws on
 * /dev/fb0 and plays the sound while demo runs on without painting (ringnav_wm_paint) or input
 * (ringnav_input); a key's release reaches it as a datagram on its socket. Each video's place, the
 * second it stopped at, is kept in VIDEO_MARKS, the most recently played first. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

/* ponytail: the file's name is the one the place list has always had on the data partition, so the
 * places saved stay; a rename would need a move at boot. */
#define VIDEO_MARKS "/mnt/data/ringnav-books"
#define MARKS_N 64 /* videos whose place is kept, the most recently played first */

static struct {
    unsigned key, pos;
} marks[MARKS_N] __attribute__((section(".scratch")));

/* Folder view's listing (playback.c ringnav_audio_extension) and its click (video_click): 1 when
 * dot, a name's extension from its dot, is a video's. */
int video_ext(const char *dot) {
    if (!dot) return 0;
    for (const char *v = "mp4\0m4v\0mkv\0avi\0mov\0mpg\0"; *v; v += 4)
        if (!strcasecmp(dot + 1, v)) return 1;
    return 0;
}

/* key's place first in VIDEO_MARKS, the list read again first, as it is written whole. */
static void mark(unsigned key, unsigned pos) {
    int k = 0;
    BLOB_IO(VIDEO_MARKS, marks, 0);
    while (k < MARKS_N - 1 && marks[k].key != key) ++k;
    for (; k; --k) marks[k] = marks[k - 1];
    marks[0].key = key, marks[0].pos = pos;
    BLOB_IO(VIDEO_MARKS, marks, 1);
}

#define VIDEO_BIN "/usr/bin/q2video"
#define VIDEO_SOCK "/tmp/q2video.sock" /* video.c Q2VIDEO_SOCK */
/* video.c Q2VIDEO_PLACE: the second to start at, then left */
#define VIDEO_PLACE "/tmp/q2video.place"
#define VIDEO_PAUSE_QUIT_MS 30000 /* paused this long, no key: quit as Return, so it sleeps */
/* seek: the wheel seeks, since seek_at (time_now_ms); key: the video's in VIDEO_MARKS, at its
 * second there; paused_at: time_now_ms when the pause began, 0 playing */
static struct {
    int paused, volume, gain, pid, sock, seek;
    unsigned polled, control_at, seek_at, key, at;
    unsigned paused_at;
} vid __attribute__((section(".scratch")));

int video_on(void) { return vid.pid; }

/* A USB DAC's volume is in software, hciplayer's as Bluetooth's, only when the USB volume is
 * variable (g_usbvol_mode) and the DAC has no volume control of its own: mclUsbAudioSetVol then
 * finds none (USB_MIXER -2) and tells hciplayer {mcl-softvolflag\1}. Otherwise the DAC applies
 * it. */
static int usb_soft(void) { return g_usbvol_mode && I(USB_MIXER, 0) == -2; }

/* The output in use for q2video (here, radio.c) and librespot's sink (spotify.c): 0 for the
 * headphone DAC, which keeps the volume set. Bluetooth's is hciplayer's soft volume, so the helper
 * gets g_volume in vol (5 bytes) to apply it the same way, on hciplayer's own plug:bluealsa (the
 * device demo writes to /mnt/data/asound.conf). A USB DAC is "usb", which the helper finds in
 * /proc/asound/cards (plughw, for its rate): with g_volume when its volume is soft, else "h" and
 * the volume, which the helper only shows. */
#define CAR_FILE "/tmp/q2-iap.audio" /* q2_iap.c's car_file: the car takes the player's audio over USB */
int usb_car_on(void);                /* usb_car.c */
const char *output_device(char *vol) {
    /* the car: ALSA's q2car (assets/alsa/30-q2car.conf), which the player plays on to the car at unity, the car's knob
     * its volume (docs/iphone-mode.md#other-sources-over-usb) */
    if (usb_car_on() && !access(CAR_FILE, 0)) {
        memcpy(vol, "h100", 5); /* "h": q2video only shows it */
        return "q2car";
    }
    int way = mclGetOutputWay();
    tk_snprintf(vol, 5, way == 2 && !usb_soft() ? "h%u" : "%u",
                way == 1 ? headset_gain() : g_volume);
    return way == 1 ? "plug:bluealsa" : way == 2 ? "usb" : 0;
}

/* The headphone output readied as stock's player_start readies it before each track (headset mode,
 * DAC powered, PCM mode, unmuted, the volume); Bluetooth's way left alone. Shared with spotify.c.
 */
void dac_take(void) {
    if (g_headset_output < 2)
        config_outputchannel(g_headset_output, 2);
    else if (I(g_dacoff_time, 0) < 0)
        mclSetDacPwr(1);
    mclSetPcmMode();
    mclSetMute(0);
    device_set_volume(g_volume, 1);
}

/* q2video on output_device, else the headphone DAC's plughw:0,0 (the only card), readied first, for
 * file or, with radio 1, Internet Radio's url (radio.c), or with radio 2 Radio's receiver
 * (radio_tuner.c), which takes no file: its pid, 0 when it did not start. */
int video_start(const char *file, int radio) {
    char vol[5];
    const char *out = output_device(vol);
    player_stop();        /* hciplayer holds the PCM even paused */
    if (!out) dac_take(); /* without it the PCM takes the sound and the jack stays silent */
    int pid = fork();
    if (!pid) {
        child_oom_first();
        const char *dev = out ? out : "plughw:0,0", *v = out ? vol : (char *)0;
        if (radio == 2) /* Radio's receiver (radio_tuner.c): no file */
            execl(VIDEO_BIN, VIDEO_BIN, "-t", dev, v, (char *)0);
        else if (radio)
            execl(VIDEO_BIN, VIDEO_BIN, "-r", dev, file, v, (char *)0);
        else
            execl(VIDEO_BIN, VIDEO_BIN, dev, file, v, (char *)0);
        child_exit(127);
    }
    return pid > 0 ? pid : 0;
}

/* path played from its saved place (video_click). */
static void video_play(const char *path) {
    if (vid.pid) return;
    if (check_forbid_play_no_headset(0)) return; /* nothing to play to (output_rule.c) */
    headset_manual();
    spot_yield(); /* librespot and radio hold the PCM too, as for local music */
    radio_stop();
    BLOB_IO(VIDEO_MARKS, marks, 0);
    vid.key = fnv(FNV_SEED, (const unsigned char *)path);
    vid.key |= !vid.key;
    vid.at = 0;
    for (int k = 0; k < MARKS_N; ++k)
        if (marks[k].key == vid.key) vid.at = marks[k].pos;
    BLOB_IO(VIDEO_PLACE, vid.at, 1);
    now_hold(); /* the Library paused where it was, not stopped and forgotten (now_source.c) */
    vid.pid = video_start(path, 0);
    vid.seek = vid.paused = 0;
    vid.paused_at = 0;
    vid.volume = g_volume; /* the helper starts at output_device's level: nothing to send, no bar */
    vid.gain = headset_gain();
    unlink("/tmp/q2video.state");
    if (vid.pid)
        vid.sock = socket(1, 1, 0); /* AF_UNIX, SOCK_DGRAM (MIPS numbering) */
    else
        now_release(); /* no video: the Library's song back at once */
}
/* Folder view's file (navigation.c ringnav_folder_click): a video, not hidden, plays; 0 for
 * anything else, left to stock. */
int video_click(const char *path) {
    const char *name = path ? strrchr(path, '/') : 0;
    if (!name || name[1] == '.' || !video_ext(strrchr(name, '.'))) return 0;
    video_play(path);
    return 1;
}

/* the helper's soft volume and gain (its bar), and what it was last sent */
static void video_sync(void) {
    char v[4] = { 'v', (char)g_volume, 'g', (char)headset_gain() };
    dgram_send(&vid.sock, VIDEO_SOCK, v, 2);
    dgram_send(&vid.sock, VIDEO_SOCK, v + 2, 2);
    vid.volume = g_volume;
    vid.gain = headset_gain();
}

/* ringnav_sleep, every UI loop pass: the helper's end, and meanwhile no screen, standby or DAC
 * power-off timeout (on_wm_idle_status, check_dacoff_state). A pause of VIDEO_PAUSE_QUIT_MS quits
 * as Return does (the place kept), so a pause from a dropped headset can't hold them forever.
 * ponytail: input goes to the helper, so the pause can't just let the screen time out under it;
 * a hold that lets a paused video go dark needs the wake path to reach stock while q2video runs. */
void video_poll(void) {
    int status;
    if (!vid.pid) return;
    if (!waitpid(vid.pid, &status, 1)) { /* WNOHANG: still playing */
        int paused;
        if (time_now_ms() - vid.polled >= 400) {
            vid.polled = time_now_ms();
            if (time_now_ms() - vid.control_at >= 1000 &&
                BLOB_IO("/tmp/q2video.state", paused, 0) && (paused == 0 || paused == 1))
                vid.paused = paused;
            if (vid.volume != g_volume || vid.gain != headset_gain()) video_sync();
            if (!vid.paused)
                vid.paused_at = 0;
            else if (!vid.paused_at)
                vid.paused_at = vid.polled ?: 1;
            else if (vid.polled - vid.paused_at >= VIDEO_PAUSE_QUIT_MS)
                dgram_send(&vid.sock, VIDEO_SOCK, "q", 1); /* again each poll until it ends */
        }
        reset_poweroptions_timer(1, 1, 1);
        if (I(g_dacoff_time, 0) > 0) I(g_dacoff_time, 0) = 0;
        if (vid.seek && time_now_ms() - vid.seek_at >= SCRUB_MS) vid.seek = 0; /* back on volume */
        return;
    }
    close(vid.sock);
    vid.pid = 0;
    unsigned at;
    /* where it stopped; a video never resumed takes no slot from the others */
    if (BLOB_IO(VIDEO_PLACE, at, 0) && (at || vid.at)) mark(vid.key, at);
    now_release(); /* the Library's song back, paused where the video found it */
    widget_invalidate_force(window_manager(), 0);
}

/* Return quits, Play/Pause pauses, the side buttons seek. The wheel is the volume, as on Now
 * Playing; Centre toggles it to seeking, which ends SCRUB_MS after the last tick (video_poll). The
 * volume steps as the volume dialog's ticks (volume.c vol_next): 1 a tick turned slowly, more at
 * speed, up to vol_cap (100, Max Volume and a Bluetooth device's Volume Limit), through
 * device_set_volume (the DAC's, or hciplayer's for Bluetooth and a soft USB volume) and saved; the
 * helper gets it for that soft gain and its bar. */
void video_key(unsigned key) {
    vid.paused_at = 0; /* a key restarts a pause's VIDEO_PAUSE_QUIT_MS */
    if (key == KEY_PLAY || key == KEY_FWD_BTN || key == KEY_BACK_BTN || key == KEY_RETURN)
        headset_manual();
    if (key == KEY_PLAY && vid.paused && check_forbid_play_no_headset(0)) return; /* output_rule.c */
    int wheel = key == KEY_NEXT || key == KEY_PREV, v;
    if (key == KEY_CENTER) vid.seek = !vid.seek;
    if (key == KEY_CENTER || (wheel && vid.seek)) vid.seek_at = time_now_ms();
    if (wheel && !vid.seek && (v = vol_next(key)) != g_volume) {
        g_volume = (unsigned char)v;
        device_set_volume(v, 1);
        write_int_config(v, "PLAYSET", "VOLUME");
    }
    char c = key == KEY_RETURN                                      ? 'q'
             : key == KEY_PLAY                                      ? 'p'
             : key == KEY_FWD_BTN || (key == KEY_NEXT && vid.seek)  ? 'f'
             : key == KEY_BACK_BTN || (key == KEY_PREV && vid.seek) ? 'b'
             : key == KEY_CENTER && vid.seek                        ? 's'
             : wheel || key == KEY_CENTER                           ? 'v'
                                                                    : 0;
    if (c == 'v')
        video_sync();
    else if (c)
        dgram_send(&vid.sock, VIDEO_SOCK, &c, 1);
    if (c == 'p') {
        vid.paused = !vid.paused;
        vid.control_at = time_now_ms();
    }
}

int video_status(void) { return vid.pid ? (vid.paused ? 3 : 2) : 0; }
unsigned video_session(void) { return (unsigned)vid.pid; }
void video_set_playing(int playing) {
    if (playing && vid.paused && check_forbid_play_no_headset(0)) return; /* output_rule.c */
    if (vid.pid) {
        dgram_send(&vid.sock, VIDEO_SOCK, playing ? "r" : "a", 1);
        vid.paused = !playing;
        vid.control_at = time_now_ms();
    }
}
