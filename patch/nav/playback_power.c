/* Replaces mclNextSong's shuffle pick call: stock picks and bookkeeps, then a pending Play next
 * becomes the next track if it is still at its index. */
int shuffle_forced(void) {
    void *queue = P(mcl_pdeqplaylist, 0);
    unsigned at = st.qm_forced - 1;
    return queue && at < deque_size(queue) && rec_hash(deque_at(queue, at)) == st.qm_forced_hash
               ? (int)at
               : -1;
}
int ringnav_shuffle(int forward) {
    unsigned was = (unsigned)MCL(MCL_POS);
    int back = playback_back(), result = mcl_shuffle_pick(forward), forced = shuffle_forced(),
        ahead = playback_peek(was); /* after the pick: Folder Skip's jump changes the queue */
    st.qm_forced = 0;
    if (forced >= 0)
        MCL(MCL_POS) = forced;
    else if (back >= 0)
        MCL(MCL_POS) = back; /* after Previous, Next returns to the track it left */
    else if (ahead >= 0)
        MCL(MCL_POS) = ahead;
    return result;
}

/* Replaces the name comparators' toolsTrimLeft calls (SORT_TRIMS): each trims its own copy of a
 * name, which then sorts without its article. */
void ringnav_sort_key(char *s) {
    toolsTrimLeft(s);
    unsigned n = article(s);
    if (n)
        for (char *d = s; (*d = d[n]); ++d) {}
}

/* Moves key's place to the front of the ring at sec, or forgets it near either end. */
static void spot_keep(unsigned key, int sec, int total) {
    int i = 0, keep = sec >= RESUME_EDGE_S && sec < total - RESUME_EDGE_S;
    while (i < RESUME_SLOTS - 1 && st.spots[i].key != key) i++;
    /* nothing to do: already first at sec, or nothing kept to forget */
    if (keep ? !i && st.spots[0].key == key && st.spots[0].sec == sec : st.spots[i].key != key)
        return;
    if (keep) {
        for (; i > 0; i--) st.spots[i] = st.spots[i - 1]; /* the oldest falls off the end */
        st.spots[0].key = key;
        st.spots[0].sec = sec;
    } else {
        for (; i < RESUME_SLOTS - 1; i++) st.spots[i] = st.spots[i + 1];
        st.spots[i].key = 0;
    }
    BLOB_IO(RESUME_FILE, st.spots, 1);
}

/* Counts key's play and moves it first, so among equal counts the least recently played is the
 * one replaced when no slot is free. */
static void play_count(void *r, unsigned key) {
    key = catalogue_song_key(r, key); /* a server song's offline copy counts as its row */
    plays_load();
    int i = 0;
    for (int j = 0; j < PLAYS_SLOTS && st.plays[i].key != key; j++)
        if (st.plays[j].key == key || st.plays[j].n <= st.plays[i].n) i = j;
    unsigned evicted = st.plays[i].key;
    mp_counted(r, key, evicted != key ? evicted : 0);
    unsigned n = st.plays[i].key == key ? st.plays[i].n + 1 : 1;
    for (; i > 0; i--) st.plays[i] = st.plays[i - 1];
    st.plays[0].key = key;
    st.plays[0].n = n;
    BLOB_IO(PLAYS_FILE, st.plays, 1);
}

/* Scrobbling (docs/internals.md#scrobbling): a Rockbox-style AudioScrobbler 1.1 log at the card's
 * root, for any .scrobbler.log uploader or Upload Scrobbles (scrobble.c). Artist, album and title
 * are the player's parsed tags when it has parsed this track (now_tag), or with .scrobble.ini's
 * ALBUM_ARTIST the song's album artist when it has one (parsed too: folder queues' records have
 * none); untagged (artist-less) tracks are skipped, as scrobblers reject them. */
char *scrobble_tag(char *o, char *end, const char *s) {
    for (; s && *s && o < end - 1; s++) *o++ = *s == '\t' || *s == '\n' || *s == '\r' ? ' ' : *s;
    *o++ = '\t';
    return o;
}

static void scrobble(void *r, int total, int heard) {
    const char *artist = now_tag(r, REC_ARTIST), *album_artist = now_tag(r, REC_ALBUM_ARTIST);
    if (!album_artist || !*album_artist)
        album_artist = P(r, REC_ALBUM_ARTIST); /* CUE: none parsed */
    if (album_artist && *album_artist && scrobble_flag("ALBUM_ARTIST")) artist = album_artist;
    if (!artist || !*artist) return;
    long long now = wall_time();
    if (now < CLOCK_SET) return; /* clock never set: Last.fm would reject the time */
    if (!addon_scrobbles(P(r, REC_PATH))) return; /* its card app's q2app says scrobble=0 */
    char line[800], name[512], *end = line + sizeof line - 48, *o = line; /* 48: the numbers */
    const char *title = now_tag(r, REC_TITLE);
    if (!title || !*title || !strcmp(title, "(null)")) title = track_name(name, sizeof name, r);
    o = scrobble_tag(o, end, artist);
    o = scrobble_tag(o, end, now_tag(r, REC_ALBUM));
    o = scrobble_tag(o, end, title);
    if (I(r, REC_TRACK) > 0) o += tk_snprintf(o, 12, "%d", I(r, REC_TRACK));
    o += tk_snprintf(o, (unsigned)(line + sizeof line - o), "\t%d\tL\t%u\t\n", total,
                     (unsigned)(now - heard));
    if (scrobble_append(line, (unsigned)(o - line))) scrobble_listened();
}

/* The polled track's place at sec, its file's seconds. A CUE track becomes the book's place only
 * past its own first RESUME_EDGE_S; only the last one (no CUE end) knows the file's end, where the
 * book is finished and forgotten. */
static void spot_save(int sec) {
    if (!(st.rs_cue || st.rs_end) || sec >= RESUME_EDGE_S)
        spot_keep(st.rs_file, st.rs_cue + sec, st.rs_end ? 0x7fffffff : st.rs_cue + st.rs_total);
}

/* Would starting the polled track again lose a place worth keeping? spot_keep keeps none near
 * either end, and spot_save leaves a CUE book's place alone at a chapter's start. */
int resume_held(void) {
    int sec, total;
    if (!st.rs_long || st.rs_cue || st.rs_end) return 0;
    if (player_playtime_and_length(&sec, &total) < 0) sec = st.rs_sec; /* stopped: where it was */
    return sec >= RESUME_EDGE_S && sec < st.rs_total - RESUME_EDGE_S;
}

/* A load of the file playing, from another list: back to this second once it starts over. */
void resume_return(void) {
    int sec, total;
    st.rs_go = player_playtime_and_length(&sec, &total) < 0 ? st.rs_sec : sec;
    st.rs_go_key = st.rs_key;
    st.rs_go_at = (unsigned)time_now_ms();
}

/* A confirmed restart: a stop before it doesn't send it back to the place. */
void resume_restart(void) { st.rs_stopped = 0; }

/* Once a second from the UI loop: a long track saves its place and, when it starts playing near
 * its beginning, jumps to the saved one. A spoken CUE track saves its file's place but jumps only
 * when a Resume row (book_place) started it. A track change waits a poll, so the previous track's
 * time never counts for the new one. */
static void resume_poll(void) {
    unsigned now = (unsigned)time_now_ms(), at, n;
    if (now - st.rs_at < 1000) return;
    st.rs_at = now;
    int sec = 0, total = 0;
    void *r = queue_now(&at, &n);
    if (!r || player_playtime_and_length(&sec, &total) < 0 || total <= 0) {
        if (st.rs_long && mclGetPlayStatus() == 1)
            st.rs_stopped = 1; /* not a load's or seek's -1 */
        return;
    }
    unsigned key = listen_key(r);                   /* a CUE image's tracks share one path */
    if (now - st.rs_go_at > 8000) st.rs_go_key = 0; /* a Resume row's chapter never came */
    if (key != st.rs_key) {
        if (st.rs_long && !st.rs_pending) spot_save(st.rs_sec);
        st.rs_key = key;
        st.rs_long = st.rs_stopped = 0;
        st.rs_settle = 1;
        return;
    }
    if (st.rs_settle) {
        st.rs_settle = 0;
        st.rs_spoken = spoken(P(r, REC_PATH));
        st.rs_file = fnv(FNV_SEED, P(r, REC_PATH));
        st.rs_cue = I(r, REC_CUE_START);
        st.rs_end = I(r, REC_CUE_END);
        int book = st.rs_cue || st.rs_end; /* music CUE images are left alone */
        int min = resume_min();            /* Q2POD RESUMEMIN, 0 for Off */
        st.rs_long = book ? st.rs_spoken : (min && total >= min * 60) || st.rs_spoken;
        int saved = st.rs_long ? spot_at(st.rs_file) : 0; /* reads the ring spot_keep writes */
        st.rs_pending = book ? 0 : saved;
        if (playback_resumed(r)) st.rs_pending = 0;
        st.rs_sec = st.rs_saved = sec;
        st.rs_still = 0;
    }
    /* Started again after a stop, as a list pick or Play does: back to the place it stopped at. */
    if (st.rs_stopped) {
        st.rs_stopped = 0;
        if (st.rs_long && !st.rs_cue && !st.rs_end && sec < RESUME_START_S &&
            st.rs_sec >= RESUME_EDGE_S && st.rs_sec < st.rs_total - RESUME_EDGE_S)
            st.rs_pending = st.rs_sec;
    }
    if (st.ch_key == key)
        st.rs_pending = 0;
    else
        st.ch_key = 0;
    /* A listen: half the track or LISTEN_MAX_S heard, seeks and pauses aside; a repeat starts over.
     * It scrobbles then too, or with [SCROBBLE] PERCENT once that share of it is heard (of all but
     * its last 2 s, which a poll a second apart may not see). */
    if (!st.rs_pending && !st.rs_spoken) {
        unsigned lk = listen_key(r);
        if (lk != st.ls_key || (sec < 2 && st.ls_sec > LISTEN_MIN_S)) {
            st.ls_key = lk;
            st.ls_sec = sec;
            st.ls_heard = st.ls_done = st.ls_sent = 0;
            int pct = scrobble_value("PERCENT");
            st.ls_pct = pct >= 1 && pct <= 100 ? pct : 0;
        }
        int d = sec - st.ls_sec;
        if (d > 0 && d <= 2) st.ls_heard += d;
        st.ls_sec = sec;
        int listen = total / 2 < LISTEN_MAX_S ? total / 2 : LISTEN_MAX_S;
        if (!st.ls_done && total > LISTEN_MIN_S && st.ls_heard >= listen) {
            st.ls_done = 1;
            play_count(r, lk);
        }
        if (!st.ls_sent && total > LISTEN_MIN_S &&
            st.ls_heard >= (st.ls_pct ? (total - 2) * st.ls_pct / 100 : listen)) {
            st.ls_sent = 1;
            scrobble(r, total, st.ls_heard);
        }
    }
    if (!st.rs_long) return;
    st.rs_total = total;
    if (key == st.rs_go_key) { /* a Resume row's chapter, the playing one too, once it starts */
        if (sec < 1 || sec >= RESUME_START_S) return; /* the old position until it does */
        st.rs_go_key = 0;
        st.rs_pending = st.rs_go;
    }
    if (st.rs_pending) {
        if (sec < 1) return; /* not playing yet: a paused boot resume waits for Play */
        if (sec < RESUME_START_S) {
            /* blocks the UI up to 2 s, as Scrub's commit; past the hook: nobody pressed, no wait */
            stock_seek_time_trampoline(st.rs_pending);
            sec = st.rs_pending;
        }
        st.rs_pending = 0;
        st.rs_sec = st.rs_saved = sec;
        return;
    }
    st.rs_still = sec == st.rs_sec ? st.rs_still + 1 : 0;
    st.rs_sec = sec;
    int moved = sec - st.rs_saved;
    if (moved && (st.rs_still >= 2 || moved >= RESUME_SAVE_S || moved <= -RESUME_SAVE_S)) {
        st.rs_saved = sec;
        spot_save(sec);
    }
}

/* Home's Rockbox row (docs/boot.md#rockbox-from-home), bound by coverflow_home: a click leaves
 * Q2 Muse for Rockbox. demo cannot start it itself: Rockbox needs the screen, the keys and ALSA. So
 * demo leaves S90play's flag and ends; S90play sees it, runs Rockbox and starts demo again when
 * Rockbox exits. Bluetooth's daemons are not demo's children and keep the headphones connected, so
 * Rockbox finds them and plays there (q2-rockbox's pcm-alsa). The queue is saved as power-off
 * saves it, charging is handed back as Rockbox expects it at boot, the
 * watchdog (checkappprocess.sh, which reboots once demo is gone) and hciplayer (which holds the
 * DAC's PCM) are stopped, and demo is killed outright, so no exit handler can hang it. */
int rockbox_open(void *ctx, void *event) {
    void *flag = !rockbox_available() ? (void *)0 : fopen(ROCKBOX_FLAG, "w");
    if (!flag) {
        toast("Rockbox is not on the card");
        return 0;
    }
    fclose(flag);
    playback_save();
    save_memoryplay_info();
    player_stop();
    radio_stop();
    addon_stop(); /* the card apps' loops run on past demo: each asked to end */
    if (st.charge_held) switch_charge_enable(1);
    /* Rockbox's volume is pcm-alsa's software mixer alone, so the headset's absolute volume, left
     * at the Q2's, would cap it: open it to the maximum volume limit. Demo syncs it back on return.
     */
    if (bt_link() && g_bluetoothflag && bt_linkstatus &&
        (unsigned)btctl_transport_get_volume() <= 127)
        btctl_transport_set_volume(((g_maxvolume < 100 ? g_maxvolume : 100) * 127 + 50) / 100);
    ringnav_clean_end(); /* the watchdog would outlive demo's kill; nothing to report */
    system("killall checkappprocess.sh; killall -9 hciplayer; " SPOT_KILL "; sync; kill -9 $PPID");
    return 0;
}

/* Now Playing on top. */
static int now_playing(void) {
    void *top = window_manager_get_top_window(window_manager());
    return top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "playing_page");
}

/* Now Playing's visualizer repainting or its lyrics gliding: its 40 ms timer and the glide's steps
 * would run late on an idle pass. Its progress and the lyrics' follow tick every 250 ms and keep
 * up. A game's 33 ms frame timer too, and the engine's, due every pass (engine.c): Vortex's raw
 * wheel posts no input to stamp, nor does an engine game's or a card game's, whose travel goes
 * once a pass. */
static int np_animating(void) {
    return games_live() || addon_game_on() ||
           (now_playing() && ((NP_VISUALIZER && visualizer_live()) || st.lyric_glide));
}

/* Keep stock VOLUME for wired/USB and boot; Bluetooth saves its own level. Smart Wi-Fi's own
 * toggles of the Wi-Fi switch save nothing (smart_config). */
int ringnav_write_config(int value, const char *section, const char *key) {
    if (smart_config(section, key)) return 0;
    if (!tk_strcmp(section, "PLAYSET") && !tk_strcmp(key, "VOLUME") && airplay_on())
        section = "Q2POD", key = "AIRVOL"; /* an AirPlay speaker's, while it plays (airplay.c) */
    else if (!tk_strcmp(section, "PLAYSET") && !tk_strcmp(key, "VOLUME") && mclGetOutputWay() == 1)
        key = "BT_VOLUME";
    return stock_write_config_trampoline(value, section, key);
}

int ringnav_output(int way) {
    int old = mclGetOutputWay();
    int changed = (old == 1) != (way == 1);
    if (changed &&
        !airplay_on()) { /* a speaker's level stays its own; airplay.c swaps back at its end */
        write_int_config(g_volume, "PLAYSET", "VOLUME");
        char s[256] = "";
        toolsReadConfig("/mnt/data/config.ini", "PLAYSET", way == 1 ? "BT_VOLUME" : "VOLUME", s,
                        "");
        int v = 0;
        unsigned i;
        for (i = 0; i < 3 && s[i] >= '0' && s[i] <= '9'; ++i) v = v * 10 + s[i] - '0';
        if (!i || s[i] || v > 100) v = g_volume;
        if (v > g_maxvolume) v = g_maxvolume;
        g_volume = (unsigned char)v;
        headset_volume_reset();
    }
    int result = stock_output_trampoline(way);
    if (changed) device_set_volume(g_volume, 1);
    return result;
}

/* AVRCP absolute volume, as in PR #3. libbtctl returns 0 on a successful write.
 * Keep stock software gain unless the headset accepted the requested level. */
static int bt_audio_ready(void) {
    return bt_link() && g_bluetoothflag && bt_linkstatus &&
           *(const volatile int *)bt_showcoding > 0 && !bt__recv_pageflag && mclGetOutputWay() == 1;
}

static struct {
    unsigned checked;
    int ready, last, failed; /* failed: the headset refused the last absolute volume */
} btvol __attribute__((section(".scratch")));

void headset_volume_reset(void) { btvol.ready = 0; }

static int bt_sync_volume(int left, int right) {
    unsigned cur = (unsigned)btctl_transport_get_volume();
    if (cur > 127) return btvol.failed = 1, 0; /* no absolute volume (yet): the slow pace */
    int v = left > right ? left : right;
    v = v < 0 ? 0 : v > 100 ? 100 : v;
    /* 128 headset steps onto 101: one that already reads as v stays, so the headset's own steps
     * hold */
    if ((int)(cur * 100 + 63) / 127 != v) {
        unsigned want = (v * 127 + 50) / 100;
        if (btctl_transport_set_volume(want)) return btvol.failed = 1, 0;
        cur = want;
    }
    btvol.last = cur;
    btvol.failed = 0;
    return 1;
}

int headset_gain(void) { return bt_audio_ready() && btvol.ready && g_volume ? 100 : g_volume; }

/* Absolute zero still needs software mute. Unsupported/failed writes retain stock gain. */
int ringnav_btvol(int left, int right) {
    if (bt_audio_ready()) {
        btvol.ready = bt_sync_volume(left, right);
        if (btvol.ready && (left > 0 || right > 0)) {
            int volume = left > right ? left : right;
            return stock_btvol_trampoline(left <= 0 ? 0 : left * 100 / volume,
                                          right <= 0 ? 0 : right * 100 / volume);
        }
    }
    return stock_btvol_trampoline(left, right);
}

/* Every 400 ms lit; dark, every BT_VOLUME_LIMIT_MS while a volume limit has to be held on the
 * headset's own keys, else BT_VOLUME_SLOW_MS, so a long dark session doesn't wake libbtctl 2.5
 * times a second for a volume nobody sees. A headset that refuses or can't report absolute volume
 * gets the slow pace lit too. ponytail: dark, the headset's keys can pass the limit for up to
 * BT_VOLUME_LIMIT_MS before it is pulled back. */
static void bt_volume_poll(void) {
    unsigned now = time_now_ms();
    unsigned gap = btvol.failed                       ? BT_VOLUME_SLOW_MS
                   : g_backlight_status               ? 400u
                   : btvol.ready && g_maxvolume < 100 ? BT_VOLUME_LIMIT_MS
                                                      : BT_VOLUME_SLOW_MS;
    if (now - btvol.checked < gap) return;
    btvol.checked = now;
    if (!bt_audio_ready()) {
        btvol.ready = btvol.failed = 0;
        return;
    }
    if (!btvol.ready) {
        device_set_volume(g_volume, 0); /* Reuse stock's Balance calculation on reconnect. */
        return;                         /* Initial connection preserves Q2 volume. */
    }
    int cur = btctl_transport_get_volume();
    if (cur < 0 || cur > 127) {
        btvol.ready = 0;
        device_set_volume(g_volume, 0);
        return;
    }
    if (cur == btvol.last) return;
    int v = (cur * 100 + 63) / 127;
    int limit = g_maxvolume < 100 ? g_maxvolume : 100;
    if (v > limit) v = limit;
    btvol.last = cur;
    g_volume = (unsigned char)v;
    *(volatile signed char *)0xa4cbb5u = (signed char)v; /* stock responseSetVolume's level */
    device_set_volume(v, 1);
    write_int_config(v, "PLAYSET", "VOLUME");
    void *top = vol_top();
    if (top) {
        void *slider = widget_lookup(top, "slider_vol", 1);
        if (slider) widget_set_prop_int(slider, "value", v);
    }
}

/* main_loop_sleep_default paces the UI loop at 8 ms (125 Hz), screen on or off. With the backlight
 * off it first idles SCREEN_OFF_SLEEP_MS (DARK_IDLE_SLEEP_MS once nothing plays and the DAC is
 * off, unless Wake's second press may be coming); stock then finds its 8 ms gone, sleeps 0 and
 * keeps its own bookkeeping. A dark wheel turn keeps stock's pace, DARK_TURN_MS on from each tick
 * (tick_gate): an idling pass dispatched every tick it had slept through at once, so a dark turn
 * clicked in bursts and stepped the volume in jumps (#303). ponytail: a turn's first ticks still
 * wait out one idle and arrive together, to one click (tone_dark). Ending the idle on a key
 * instead is select() on a datagram socket that ringnav_queue_event sends to, a send on every
 * input thread; build it if the first tick's wait ever shows. The first pass with it back on
 * repaints every window once, so nothing drawn while dark, or only partly, stays on screen until
 * the next input; after a centre press woke it, it also restarts Wake's window for a dropped press
 * (woke_at). With the screen on, a pass idles IDLE_SLEEP_MS once input and window animations have
 * been still for IDLE_MS, except while Now Playing animates or the volume row's push runs (it
 * leaves 5.3 s after the last key). */
static volatile int dark_quiet; /* read by ringnav_dark_usleep on stock's threads */
static int tone_dark;           /* this pass clicked with the screen off (ringnav_buzzer) */
int ringnav_dark_usleep(unsigned us) {
    return usleep(dark_quiet && us < DARK_POLL_US ? DARK_POLL_US : us);
}
/* The cover pollers feed only what is painted, so they slow down whenever the screen is off,
 * playing or not. ponytail: art parsed while dark can show up to DARK_POLL_US late after wake. */
int ringnav_dark_cover_usleep(unsigned us) {
    return usleep(!g_backlight_status && us < DARK_POLL_US ? DARK_POLL_US : us);
}
/* on_wm_idle_status ran player_mainscheduling every UI pass, and its mclAutoChange asks hciplayer
 * for its status and waits for the answer on the UI thread: 40 to 125 round trips a second, each
 * waking both players. It runs every MAINSCHEDULING_MS instead, DARK_POLL_US while dark and quiet:
 * a lyric line and the play-state notifications follow within that, as stock's 200 ms status sync
 * does. From a second before the playing track's end (player_playtime_and_length's cache, a CUE
 * track's own end) until MAINSCHEDULING_END_MS after, it runs every pass, so a change without
 * Gapless starts as soon as stock's did; paused, it stays paced. ponytail: a length that overstates
 * the audio (a VBR MP3 without a Xing header) ends outside the window, up to 100 ms late; follow
 * mclAutoChange's own end flag if that shows. */
#define MAINSCHEDULING_MS 100
#define MAINSCHEDULING_END_MS 2000
int ringnav_mainscheduling(void) {
    static unsigned last, end;
    int sec = -1, total = 0;
    if (mclGetPlayStatus() == 2) player_playtime_and_length(&sec, &total);
    unsigned now = (unsigned)time_now_ms(),
             gap = dark_quiet ? DARK_POLL_US / 1000 : MAINSCHEDULING_MS;
    if (total > 0 && total - sec <= 1) end = now | !now;
    if (last && now - last < gap && !(end && now - end < MAINSCHEDULING_END_MS)) return 0;
    last = now | !now; /* never 0 */
    return player_mainscheduling();
}

/* Cover Flow's thumb, every UI loop pass before lyric_poll, so a wheel handed between them ends up
 * the lyrics' or, rewritten next pass, the covers'. The gates ticks have, for raw travel: stock's
 * lockout after Centre or Return, as ringnav keeps it; Return's stop; no frame in WHEEL_FRESH_MS.
 * No key event comes, so a turn times the loop's idle and the screen's timeout itself. */
static void thumb_poll(void) {
    if (!coverflow_wheel_live()) return;
    unsigned now = (unsigned)time_now_ms();
    int gated = *(volatile signed char *)KEY_LOCKOUT > 8 - LOCKOUT_KEEP || wheel_mute ||
                (int)(now - wheel_frame_at) >= WHEEL_FRESH_MS;
    int r = coverflow_wheel_poll(usable(), gated);
    if (r) st.last_input = now;
    if (r & THUMB_TURNED) reset_poweroptions_timer(1, 1, 1);
    if (r & THUMB_CLICK) buzzeer_switch(1);
}

int ringnav_sleep(void *loop) {
    splash_poll();
    usb_ui_poll(); /* USB and charging toasts, adbd's questions (usb_ui.c) */
    stale_heal();
    quick_poll();
    headset_poll();
    bt_ui_poll(); /* Settings > Bluetooth's scan, the connection toasts (bt_ui.c) */
    headset_eq_poll();
    bt_volume_poll();
    video_poll();
    spot_poll();
    radio_poll();
    addon_poll();
    coda_rc_poll();
    airplay_poll();
    playback_poll();
    resume_poll();
    scrobble_auto();
    power_poll();
    thumb_poll();
    tilt_poll();
    swipe_back_poll();
    lyric_poll();
    keep_lit_poll();
    np_art_poll();
    if ((st.np_slide && widget_get_prop_int(st.np_slide, "value", 0)) || /* off the art page */
        np_about_page()) /* the details over it (np_about.c) */
        *(volatile signed char *)BC_IDLE = BC_IDLE_FULL;
    if (st.bc[0] && np_track() != st.bc_track) bc_show();
    if (!g_backlight_status) {
        st.dark |= 1;
        int quiet = I(g_dacoff_time, 0) < 0 && !media_playing() && !video_on() &&
                    airplayGetFlag() != 2 && (signed char)bt__recv_pageflag != 2 &&
                    time_now_ms() - st.wake_at >= WAKE_MS;
        dark_quiet = quiet;
        if ((unsigned)time_now_ms() - st.tick_at >= DARK_TURN_MS)
            sleep_ms(quiet ? DARK_IDLE_SLEEP_MS : SCREEN_OFF_SLEEP_MS);
    } else if (st.dark) {
        dark_quiet = 0;
        if (st.dark & 2) st.woke_at = (unsigned)time_now_ms();
        st.dark = 0;
        if (st.clock_timer) clock_show(); /* a time set while dark */
        widget_invalidate_force(window_manager(), (void *)0);
    } else if (time_now_ms() - st.last_input >= IDLE_MS &&
               !window_manager_is_animating(window_manager()) && !np_animating())
        sleep_ms(np_glide_wait(np_marq_wait(IDLE_SLEEP_MS))); /* by the next frame due */
    tone_dark = 0; /* after the polls: a click of theirs is this pass's too */
    return stock_sleep_trampoline(loop);
}

