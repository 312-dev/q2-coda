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
    card_blob_write(RESUME_STORE, st.spots, sizeof st.spots);
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
    card_blob_write(PLAYS_STORE, st.plays, sizeof st.plays);
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
    if (!now_scrobbles()) return; /* the Library's songs alone (now_source.c) */
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

/* A confirmed restart: a stop before it doesn't send it back to the place. */
void resume_restart(void) { st.rs_stopped = 0; }

/* Once a second from the UI loop: a long track saves its place and, when it starts playing near
 * its beginning, jumps to the saved one. A spoken CUE track saves its file's place but jumps only
 * when a Resume row (book_place) started it. A track change waits a poll, so the previous track's
 * time never counts for the new one. Places and counts kept in RAM while the card was away go to
 * it here once it is back. */
static void resume_poll(void) {
    unsigned now = (unsigned)time_now_ms(), at, n;
    if (now - st.rs_at < 1000) return;
    card_blob_flush(RESUME_STORE);
    card_blob_flush(PLAYS_STORE);
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
        /* up to 3 s on counts as heard: dark, the cached second is up to DARK_POLL_US old, so two
         * reads a second apart can be 3 s apart; a longer step is a seek */
        int d = sec - st.ls_sec;
        if (d > 0 && d <= 3) st.ls_heard += d;
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

/* The headset's absolute volume (AVRCP, 0 to 127) for the Q2's volume v (0 to 100): the same share
 * of the headset's range, 127 v / 100 rounded, as a phone sends its own volume, so a headset that
 * shapes the value into loudness itself (Pixel Buds do) is as even on the Q2 as on a phone. At
 * least 1 above 0, so only 0 mutes. docs/internals.md#headset-controls-and-volume. */
static unsigned bt_vol_avrcp(int v) {
    return v <= 0 ? 0 : v >= 100 ? 127 : v == 1 ? 1 : (127u * (unsigned)v + 50) / 100;
}
/* The Q2 volume a headset's level reads as: the one whose level is nearest, the lower of two as
 * near, so a level the Q2 wrote reads back as the volume that wrote it. */
static int bt_vol_q2(unsigned level) {
    int best = 0;
    unsigned off = 255;
    for (int v = 0; v <= 100; ++v) {
        unsigned l = bt_vol_avrcp(v), d = l > level ? l - level : level - l;
        if (d < off) best = v, off = d;
    }
    return best;
}

/* Home's Rockbox row (docs/boot.md#rockbox-from-home), bound by coverflow_home: a click leaves
 * Q2 Muse for Rockbox. demo cannot start it itself: Rockbox needs the screen, the keys and ALSA. So
 * demo leaves S90play's flag and ends; S90play sees it, runs Rockbox and starts demo again when
 * Rockbox exits. Bluetooth's daemons are not demo's children and keep the headphones connected, so
 * Rockbox finds them and plays there (q2-rockbox's pcm-alsa). The queue is saved as power-off
 * saves it, charging is handed back as Rockbox expects it at boot, hciplayer (which holds the
 * DAC's PCM) is stopped, and demo is killed outright, so no exit handler can hang it. */
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
        btctl_transport_set_volume(bt_vol_avrcp(g_maxvolume));
    ringnav_clean_end(); /* the watchdog would outlive demo's kill; nothing to report */
    system("killall -9 hciplayer; " SPOT_KILL "; sync; kill -9 $PPID");
    return 0;
}

/* Now Playing on top. */
static int now_playing(void) {
    void *top = window_manager_get_top_window(window_manager());
    return top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "playing_page");
}

/* Now Playing's lyrics gliding: the glide's steps would run late on an idle pass. Its progress
 * and the lyrics' follow tick every 250 ms and keep up. A game's 33 ms frame timer too, and the
 * engine's, due every pass (engine.c): Vortex's raw wheel posts no input to stamp, nor does an
 * engine game's or a card game's, whose travel goes once a pass. */
static int np_animating(void) {
    return games_live() || addon_game_on() || (now_playing() && st.lyric_glide);
}

/* Keep stock VOLUME for wired/USB and boot; Bluetooth saves its own level. Smart Wi-Fi's own
 * toggles of the Wi-Fi switch save nothing (smart_config). A key a wheel steps is held and written
 * once it settles (config_hold, config_ini.c), under the output's key as it was at the step. */
int ringnav_write_config(int value, const char *section, const char *key) {
    if (smart_config(section, key)) return 0;
    if (!tk_strcmp(section, "PLAYSET") && !tk_strcmp(key, "VOLUME") && airplay_on())
        section = "Q2POD", key = "AIRVOL"; /* an AirPlay speaker's, while it plays (airplay.c) */
    else if (!tk_strcmp(section, "PLAYSET") && !tk_strcmp(key, "VOLUME") && mclGetOutputWay() == 1)
        key = "BT_VOLUME";
    if (!tk_strcmp(section, "PLAYSET") && (!tk_strcmp(key, "VOLUME") || !tk_strcmp(key, "BT_VOLUME")))
        btlim_saved(); /* the user's level now, not one a device's limit pulled down (bt_vol_limit.c) */
    if (config_hold(value, section, key)) return 0;
    return stock_write_config_trampoline(value, section, key);
}

int ringnav_output(int way) {
    int old = mclGetOutputWay();
    int changed = (old == 1) != (way == 1);
    if (changed &&
        !airplay_on()) { /* a speaker's level stays its own; airplay.c swaps back at its end */
        /* Bluetooth's level as it was before a device's limit pulled it down (bt_vol_limit.c) */
        write_int_config(old == 1 ? btlim_departing(g_volume) : g_volume, "PLAYSET", "VOLUME");
        config_settle(1); /* the old output's level on the flash before the new one's is read */
        char s[256] = "";
        toolsReadConfig("/mnt/data/config.ini", "PLAYSET", way == 1 ? "BT_VOLUME" : "VOLUME", s,
                        "");
        int v = 0;
        unsigned i;
        for (i = 0; i < 3 && s[i] >= '0' && s[i] <= '9'; ++i) v = v * 10 + s[i] - '0';
        if (!i || s[i] || v > 100) v = g_volume;
        if (v > g_maxvolume) v = g_maxvolume;
        if (way == 1) v = btlim_entering(v); /* and the device's Volume Limit (bt_vol_limit.c) */
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
    int pending;             /* a volume signal not yet read (bt_vol_event.c) */
} btvol __attribute__((section(".scratch")));

void headset_volume_reset(void) { btvol.ready = 0; }

static int bt_sync_volume(int left, int right) {
    unsigned cur = (unsigned)btctl_transport_get_volume();
    if (cur > 127) return btvol.failed = 1, 0; /* no absolute volume (yet): the slow pace */
    int v = left > right ? left : right;
    v = v < 0 ? 0 : v > 100 ? 100 : v;
    /* v's level on the curve, unless the headset is there or at a level of its own that reads as
     * v, so the headset's own steps hold */
    unsigned want = bt_vol_avrcp(v);
    if (cur != want && bt_vol_q2(cur) != v) {
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

/* With BlueZ's volume signal subscribed (bt_vol_event_live), a signal reads the level on the next
 * pass, unless one was read within BT_VOLUME_EVENT_GAP_MS (then that long after it), and without
 * one every BT_VOLUME_FALLBACK_MS, lit or dark, so a lit headset change shows within a pass.
 * Without the subscription: every 400 ms lit; dark, every BT_VOLUME_LIMIT_MS while a volume limit
 * has to be held on the headset's own keys, else BT_VOLUME_SLOW_MS, so a long dark session doesn't
 * wake libbtctl 2.5 times a second for a volume nobody sees. A headset that refuses or can't report
 * absolute volume gets the slow pace lit too, signal or not. ponytail: without the signal, dark,
 * the headset's keys can pass the limit for up to BT_VOLUME_LIMIT_MS before it is pulled back. */
static void bt_volume_poll(void) {
    btlim_poll(); /* the connected device's Volume Limit, Link Volume on or off (bt_vol_limit.c) */
    unsigned now = time_now_ms();
    if (bt_vol_event_take()) btvol.pending = 1;
    int live = bt_vol_event_live();
    unsigned gap = btvol.failed                       ? BT_VOLUME_SLOW_MS
                   : live                             ? BT_VOLUME_FALLBACK_MS
                   : g_backlight_status               ? 400u
                   : btvol.ready && vol_cap() < 100   ? BT_VOLUME_LIMIT_MS
                                                      : BT_VOLUME_SLOW_MS;
    if (btvol.pending && !btvol.failed && gap > BT_VOLUME_EVENT_GAP_MS)
        gap = BT_VOLUME_EVENT_GAP_MS;
    if (now - btvol.checked < gap) return;
    btvol.checked = now;
    btvol.pending = 0;
    if (!bt_audio_ready()) {
        btvol.ready = btvol.failed = 0;
        return;
    }
    bt_vol_event_start(); /* once */
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
    int v = bt_vol_q2((unsigned)cur), from = g_volume;
    int limit = vol_cap(); /* Max Volume and the device's Volume Limit */
    if (v > limit) v = limit;
    btvol.last = cur;
    g_volume = (unsigned char)v;
    *(volatile signed char *)0xa4cbb5u = (signed char)v; /* stock responseSetVolume's level */
    device_set_volume(v, 1);
    write_int_config(v, "PLAYSET", "VOLUME");
    vol_show(from); /* on screen, as a wheel's change: volume.c */
}

/* main_loop_sleep_default paces the UI loop at 8 ms (125 Hz), screen on or off. With the backlight
 * off it first idles SCREEN_OFF_SLEEP_MS (DARK_IDLE_SLEEP_MS once nothing plays and the DAC is
 * off, unless Wake's second press may be coming); stock then finds its 8 ms gone, sleeps 0 and
 * keeps its own bookkeeping. A dark wheel turn keeps stock's pace, DARK_TURN_MS on from each tick
 * (tick_gate): an idling pass dispatched every tick it had slept through at once, so a dark turn
 * clicked in bursts and stepped the volume in jumps (#303). Every idle is loop_wait's poll(), which
 * any queued event ends at once, so a turn's first tick no longer waits one out; and it may run
 * past these lengths while nothing needs the loop sooner (loop_span, loop_wake.c; dark, only while
 * the player polls are slow, no stream's network timer runs and input has been still IDLE_MS).
 * The first pass with it back on
 * repaints every window once, so nothing drawn while dark, or only partly, stays on screen until
 * the next input; after a centre press woke it, it also restarts Wake's window for a dropped press
 * (woke_at). With the screen on, a pass idles IDLE_SLEEP_MS once input and window animations have
 * been still for IDLE_MS, except while Now Playing animates or the volume row's push runs (it
 * leaves 5.3 s after the last key). */
static int dark_quiet;         /* dark, nothing playing, the DAC off: the long idle */
static volatile int dark_slow; /* the player polls' slow pace; read on stock's status thread */
static int player_quick;       /* ringnav_mainscheduling: the track's end is near or just passed */
static volatile int push_pace; /* the player polls wait on q2player's push (player_push.c) */
static int tone_dark;          /* this pass clicked with the screen off (ringnav_buzzer) */
/* mclSyncPlayerStatus' 200 ms wait (DARK_POLLS): DARK_POLL_US while dark_slow, else until
 * q2player's push while push_pace, else stock's. */
int ringnav_dark_usleep(unsigned us) {
    if (us >= DARK_POLL_US) return usleep(us);
    if (dark_slow) return usleep(DARK_POLL_US);
    return push_pace ? player_push_wait(us) : usleep(us);
}
/* nothing_follows: nothing but the screen reads the local player's position closely (below), so
 * lit the polls may wait on q2player's push; Car Remote is no exception there, as each second is
 * pushed. dark_slow_ok: dark, may stock's status sync and player scheduling slow to DARK_POLL_US,
 * blind? Always when quiet.
 * While the local player plays or is paused, only the screen reads its position closely, so they
 * slow down unless something else follows it: the track's last DARK_END_LEAD_S and the moments
 * after its end (player_quick: the Gapless preload, a change without Gapless, the sleep timer's
 * end, Download Ahead's next song), A-B Repeat's loop (check_abrepeatmode reads the cached
 * position), a phone on Car Remote or Car Audio while dark (the phone or the car shows it, and the dark second is
 * blind: with Car Remote on but no phone connected nobody reads it, coda_rc_online; and
 * the car's commands, a held button's scan among them, are read a pass apart: usb_car.c), or another
 * player (AirPlay, Bluetooth receive, a video), which keeps stock's pace. Play and pause need
 * neither: mclSetPause and mclSetResume set the status word themselves. ponytail: dark, the cached
 * second can be up to DARK_POLL_US old: Resume's place, a card app's .now and the listen count
 * (playback_power.c resume_poll) see the position that much later; and lit again, the thread
 * finishes its long sleep first, so Now Playing's time and a lyric line can lag up to a second at
 * wake. */
static int nothing_follows(void) {
    return !player_quick && media_owner() == 1 && !video_on() && airplayGetFlag() != 2 &&
           (signed char)bt__recv_pageflag != 2 &&
           !(*(volatile unsigned char *)REPEAT_INFO && I(REPEAT_INFO, REPEAT_AB_STATE) == 2);
}
static int dark_slow_ok(int quiet) { return quiet || (nothing_follows() && !coda_rc_online() && !usb_car_on()); }
/* on_wm_idle_status ran player_mainscheduling every UI pass, and its mclAutoChange asks hciplayer
 * for its status and waits for the answer on the UI thread: 40 to 125 round trips a second, each
 * waking both players. It runs every MAINSCHEDULING_MS instead, DARK_POLL_US while dark_slow:
 * a lyric line and the play-state notifications follow within that, as stock's 200 ms status sync
 * does. While push_pace (q2player's push is bound and nothing_follows), it runs on the pass after
 * the status thread took a push, at most every MAINSCHEDULING_MS, else DARK_POLL_US after its last
 * run (docs/internals.md#player-status-push). From a second before the playing track's end
 * (player_playtime_and_length's cache, a CUE track's own end) until MAINSCHEDULING_END_MS after, it
 * runs every pass, so a change without Gapless starts as soon as stock's did; paused, it stays
 * paced. Playing, from DARK_END_LEAD_S before the end, or with no length or position to go by,
 * player_quick keeps dark_slow and push_pace off. ponytail: a length that overstates the audio (a
 * VBR MP3 without a Xing header) ends outside the window, up to 100 ms late (DARK_POLL_US dark);
 * follow mclAutoChange's own end flag if that shows. */
#define MAINSCHEDULING_MS 100
#define MAINSCHEDULING_END_MS 2000
int ringnav_mainscheduling(void) {
    static unsigned last, end, seq;
    int sec = -1, total = 0, playing = mclGetPlayStatus() == 2;
    if (playing) player_playtime_and_length(&sec, &total);
    unsigned now = (unsigned)time_now_ms();
    if (total > 0 && total - sec <= 1) end = now | !now;
    int ending = end && now - end < MAINSCHEDULING_END_MS;
    player_quick = ending || (playing && (total <= 0 || sec < 0 || total - sec <= DARK_END_LEAD_S));
    int pushed = push_pace = player_push_live() && nothing_follows();
    unsigned gap = dark_slow || pushed ? DARK_POLL_US / 1000 : MAINSCHEDULING_MS;
    if (last && !ending && now - last < gap &&
        !(pushed && player_push_seq() != seq && now - last >= MAINSCHEDULING_MS))
        return 0;
    last = now | !now; /* never 0 */
    seq = player_push_seq();
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

/* A stock stream queue (Tidal's), whose network state stock's 500 ms timer follows
 * (on_wm_timer_networkstatus): the dark wait stays short for it. */
static int playing_stream(void) {
    unsigned type = (unsigned)mclGetPlayType();
    return type - 2 < 2 || type - 0xf1 < 2;
}

int ringnav_sleep(void *loop) {
    ui_watch_enter();  /* the UI pass watchdog (ui_watch.c): this pass's work so far */
    cover_wake_poll(); /* stock's album cover thread, woken for a request (cover_wake.c) */
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
    tuner_poll(); /* Radio's list and receiver (radio_tuner.c) */
    addon_poll();
    now_poll(); /* the car's Bluetooth told of a radio and of the position (now_source.c) */
    coda_rc_poll();
    song_radio_poll();
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
    np_settle_poll();
    if ((st.np_slide && widget_get_prop_int(st.np_slide, "value", 0)) || /* off the art page */
        np_about_page()) /* the details over it (np_about.c) */
        *(volatile signed char *)BC_IDLE = BC_IDLE_FULL;
    if (st.bc[0] && np_track() != st.bc_track) bc_show();
    ui_watch_polled(); /* the pass's work ends here: the waits below are not counted */
    if (!g_backlight_status) {
        st.dark |= 1;
        int quiet = I(g_dacoff_time, 0) < 0 && !media_playing() && !video_on() &&
                    airplayGetFlag() != 2 && (signed char)bt__recv_pageflag != 2 &&
                    time_now_ms() - st.wake_at >= WAKE_MS;
        dark_quiet = quiet;
        dark_slow = dark_slow_ok(quiet);
        unsigned now = (unsigned)time_now_ms();
        if (now - st.tick_at >= DARK_TURN_MS) {
            unsigned ms = quiet ? DARK_IDLE_SLEEP_MS : SCREEN_OFF_SLEEP_MS;
            if (dark_slow && now - st.last_input >= IDLE_MS && !playing_stream())
                ms = loop_span(ms, LOOP_DARK_MAX_MS, 0);
            loop_wait(ms);
        }
    } else if (st.dark) {
        dark_quiet = dark_slow = 0;
        if (st.dark & 2) st.woke_at = (unsigned)time_now_ms();
        st.dark = 0;
        if (st.clock_timer) clock_show(); /* a time set while dark */
        widget_invalidate_force(window_manager(), (void *)0);
    } else if (time_now_ms() - st.last_input >= IDLE_MS &&
               !window_manager_is_animating(window_manager()) && !np_animating())
        loop_wait(np_glide_wait(np_marq_wait(loop_span(IDLE_SLEEP_MS, LOOP_LIT_MAX_MS, 1))));
    tone_dark = 0; /* after the polls: a click of theirs is this pass's too */
    int r = stock_sleep_trampoline(loop);
    ui_watch_woke();
    return r;
}
