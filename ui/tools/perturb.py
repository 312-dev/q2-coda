#!/usr/bin/env python3
"""Prove test/ui.py's checks can fail (docs/ui-rewrite.md#requirements): one source change at a time, each taking
out the behaviour a check holds, the suite's part run and that check expected red, the file put back from a copy.

    python3 ui/tools/perturb.py [NAME ...]    # all of them without names; about a minute each

A case is (file, the text to change, what to change it to, the part of test/ui.py to run, words of the check that
must fail). "SOURCE NOT FOUND" means the code moved on and the case needs its text again. The payload's gate
(patch/q2ui_gate.c) is checked the same way by hand against test/fw/test_q2ui_gate.py."""
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
KEEP = pathlib.Path(tempfile.mkdtemp(prefix='q2ui-perturb-'))
CASES = {
 'snap': ('ui/list.c', '    kick(l);\n}\n\nvoid list_select', '    spring_set(&l->scroll, l->scroll.to);\n    kick(l);\n}\n\nvoid list_select', 'scripts', 'the list glides a row'),
 'retarget-jump': ('ui/list.c', '    l->scroll.to = (float)((l->top - p) * l->row_h);', '    l->scroll.x = l->scroll.to;\n    l->scroll.to = (float)((l->top - p) * l->row_h);', 'scripts', 'never back and never by a jump'),
 'two-rows': ('ui/list.c', '        l->sel = sel;\n        /* a tick throws', '        l->sel = sel + (sel > 6 && sel < l->n - 1);\n        /* a tick throws', 'scripts', 'moves the selection one row'),
 'focus-clipped': ('ui/list.c', '    rect_t on;\n    clip_set(r);\n', '    rect_t on;\n    clip_set(rows);\n', 'scripts', 'drawn over the pinned row'),
 'focus-faded': ('ui/list.c', '        row_paint(l, i, y, 1, l->lean.x * over);\n    }\n    clip_set(r);', '        row_paint(l, i, y, 1, l->lean.x * over);\n    }\n    clip_set(r);\n    shade_rows(l->box.y + l->box.h, SCREEN_H, FOOT_KEEP, 0);', 'scripts', 'over the fade, as bright as ever'),
 'content-flat': ('ui/list.c', '#define CONTENT_DEPTH 1.4f', '#define CONTENT_DEPTH 1.0f', 'scripts', 'further than it does'),
 'never-rests': ('ui/list.c', '    traced = l;\n', '    traced = l;\n    ui_mark(sb_rect());\n', 'scripts', 'draws no frame for three seconds'),
 'no-dither': ('ui/px.h', 'unsigned k = 65536 - a, n = noise << 8;', 'unsigned k = 65536 - a, n = 128 << 8; (void)noise;', 'engine', 'most of its rows mix two levels'),
 'no-narrow-dither': ('ui/fb.c', 'uint32_t add1 = n >> fb_bits,', 'uint32_t add1 = (n >> fb_bits) * 0,', 'engine', '0x53 dithered to 6 bits'),
 'no-guard': ('ui/ui.c', '    if (ui_painting) {\n        fprintf', '    if (0 && ui_painting) {\n        fprintf', 'engine', 'a draw that marks is aborted'),
 'hard-edge': ('ui/draw.c', '            unsigned a = (unsigned)(cov * (float)whole + 0.5f);\n            *p = a >= 256 ? 0xff000000 | ink', '            unsigned a = cov > 0.5f ? whole : 0;\n            *p = a >= 256 ? 0xff000000 | ink', 'engine', 'a box edge half over a pixel'),
 'whole-pixel-glyph': ('ui/draw.c', '    if (sx == 16) sx = 0, ++x;', '    sx = sy = 0;\n    if (sx == 16) sx = 0, ++x;', 'engine', 'half a pixel right'),
 'no-reduce': ('ui/list.c', '        if (!list_motion || ui_reduce_motion) return;\n        float a', '        if (!list_motion) return;\n        float a', 'scripts', 'Reduce Motion leans nothing'),
 'flat-card': ('ui/page_gallery.c', '{ s->badge, 0, 7, 0, 52 }', '{ s->badge, 0, 0, 0, 52 }', 'scripts', 'the layer in front of it moves further'),
 'grey-selectable': ('ui/list.c', 'return !(r.flags & (ROW_GREY | ROW_HEADER));', 'return !(r.flags & ROW_HEADER);', 'scripts', 'passes over a greyed row'),
 'knob-jumps': ('ui/list.c', '    if (!list_motion) spring_set(&l->knob, l->knob.to);\n    else kick(l);', '    spring_set(&l->knob, l->knob.to);', 'scripts', 'its knob slides there'),
 'play-wrong-song': ('ui/page_songs.c', 'player_play(lib_ids(), lib_songs(), i ? i - 1 : 0, !i);', 'player_play(lib_ids(), lib_songs(), i, !i);', 'live', 'sends one play, from that song'),
 'play-no-list': ('ui/player.c', '    if (!ok || rename(tmp, player_list)) return -1;', '    if (!ok) return -1;', 'live', "every song's id"),
 'no-restore': ('ui/main.c', '    first_page();\n    state_restore();', '    first_page();', 'live', 'opens on the row it was ended on'),
 'never-dark': ('ui/ui.c', '        if (got && dark()) quiet_until = mono_us() + 400000;', '        if (0 && got && dark()) quiet_until = mono_us() + 400000;', 'live', 'with the backlight off'),
 'spin-skips-top': ('ui/list.c', '    if (l->top < p || l->sel < p) l->top = p;', '    if (l->top < p) l->top = p;', 'scripts', 'every one leaves the songs at their top'),
 'no-playing-row': ('ui/page_home.c', 'static int state_now(void) { return now_get()->playing ? 1 : now_get()->paused ? 2 : 0; }', 'static int state_now(void) { return 0; }', 'pages', 'Home has one row more'),
 'bar-empty': ('ui/page_now.c', '    float share = n->len_s ? clampf((float)pos / ((float)n->len_s * 1000), 0, 1) : 0;', '    float share = 0 * pos;', 'pages', "bar is filled to 14 s"),
 'centre-not-toggle': ('ui/page_now.c', '        player_send("toggle");', '        player_send("pause");', 'pages', 'Centre is play and pause'),
 'group-plays-library': ('ui/page_library.c', '    player_play(s->ids, s->n, i, 0);', '    player_play(lib_ids(), lib_songs(), i, 0);', 'pages', "plays that artist's songs"),
 'album-by-title': ('ui/lib.c', '    if (group_kind == LIB_ALBUM && lib.track[x] != lib.track[y]) return lib.track[x] - lib.track[y];', '', 'pages', "its tracks in order"),
 'old-menus-stays': ('ui/page_home.c', '        ui_quit(0); /* demo', '        (void)0; /* demo', 'pages', 'Centre on Old Menus ends q2ui'),
 'route-forgotten': ('ui/main.c', '        else if (!library_route(seg))\n            break;', '        else\n            break;', 'live', 'on the page and the row it was ended on'),
 'no-volume-bar': ('ui/page_now.c', '        s->volume = to, s->volume_at = ui_time();', '        s->volume = to;', 'pages', 'shows the volume in the bar'),
 'volume-bar-stays': ('ui/page_now.c', '    if (s->volume_at && ui_time() - s->volume_at >= VOLUME_SHOW_US) s->volume_at = 0,', '    if (0 && s->volume_at) s->volume_at = 0,', 'pages', 'shows the volume in the bar'),
 'dithers-share-noise': ('ui/fb.c', 'narrow(src[i], noise[((r.x + i + 17) & 63) * 64])', 'narrow(src[i], dither64[(y & 63) * 64 + ((r.x + i) & 63)])', 'engine', 'keeps its mean at 6 bits'),
 'panel-any-link': ('ui/panel.c', "    if (!dsi || dsi[DPI_COLOR_CODING / 4] != PANEL_CODING || dsi[VID_PKT_SIZE / 4] != PANEL_PKT) return 0;", "    if (!dsi) return 0;", 'panel', "a link that is not the Q2's"),
 'panel-no-wait': ('ui/panel.c', "        if (waited == 20) return 0; /* 20 ms and still full: left for the next time */", "        if (waited == 20) break;", 'panel', 'FIFOs that stay full'),
 'panel-keep-ignored': ('ui/panel.c', "    if (panel_keep) return 0;", "", 'panel', '--panel-mode keep'),
 'panel-wrong-word': ('ui/panel.c', "    dsi[GEN_PLD_DATA / 4] = COLMOD | COLMOD_24 << 8;", "    dsi[GEN_PLD_DATA / 4] = COLMOD | 0x55 << 8;", 'panel', 'sent COLMOD 0x77'),
 'hup-ends': ('ui/ui.c', "            if (ui_restart) ui_restart(); /* does not come back when it works */", "", 'live', 'SIGHUP starts q2ui again in place'),
 'battery-always-full': ('ui/status.c', "        float full = (body.w - 4) * clampf((float)S.level / 100, 0.08f, 1);", "        float full = (body.w - 4);", 'status', "half a charge fills the battery's left half"),
 'battery-never-red': ('ui/status.c', "S.cable ? tok(TOK_POSITIVE) : S.level < LOW ? tok(TOK_NEGATIVE) : ink;", "S.cable ? tok(TOK_POSITIVE) : ink;", 'status', 'under a fifth it is red'),
 'no-play-glyph': ('ui/status.c', "    if (play != S.play) S.play = play, ui_mark(glyphs_rect());", "    (void)play;", 'status', 'a song playing puts three bars'),
 'fan-always-full': ('ui/status.c', "        wifi = quality >= 45 ? 3 : quality >= 25 ? 2 : 1;", "        wifi = 3;", 'status', "Wi-Fi's fan is lit by its signal"),
 'no-volume-in-row': ('ui/status.c', "        S.volume_at = ui_time();\n        ui_mark(volume_rect());", "        ui_mark(volume_rect());", 'status', 'a volume change shows its level'),
 'title-never-slides': ('ui/page_now.c', "            x = (float)(t - MQ_REST_US) / 1e6f * MQ_SPEED, ui_again();", "            ui_again();", 'pages', 'a long title is still for its first two seconds'),
 'title-no-fade': ('ui/page_now.c', "        fade_set(TEXT_X, TEXT_X + room, s->mq_x > 0.5f ? MQ_FADE : 0,", "        fade_set(TEXT_X, TEXT_X + room, 0,", 'pages', "at its end it fades out at the column"),
 'hup-runs-old-file': ('ui/main.c', "    if (strchr(started_with[0], '/')) execv(started_with[0], started_with);", "", 'live', 'the program now at the path runs'),
}
names = sys.argv[1:] or list(CASES)
bad = 0
for name in names:
    path, old, new, part, want = CASES[name]
    f = ROOT/path
    text = f.read_text()
    if text.count(old) != 1:
        print(f'{name}: SOURCE NOT FOUND ({text.count(old)} matches)'); bad += 1; continue
    KEEP.mkdir(exist_ok=True)
    shutil.copy2(f, KEEP/f.name)
    try:
        f.write_text(text.replace(old, new))
        out = subprocess.run(['bash', '-c', f'ulimit -c 0; timeout 300 python3 test/ui.py {part}'], cwd=ROOT, capture_output=True, text=True)
        fails = [l for l in out.stdout.splitlines() if l.startswith('FAIL')]
        red = any(want in l for l in fails)
        print(f"{name}: {'RED as expected' if red else 'NOT CAUGHT'} ({len(fails)} failed: {'; '.join(l[5:60] for l in fails)}) {out.stderr[-200:] if not fails else ''}")
        bad += not red
    finally:
        shutil.copy2(KEEP/f.name, f)
        f.touch()  # newer than the object built from the changed text, so make builds it again
        assert f.read_text() == text
print('perturb:', 'every check went red' if not bad else f'{bad} NOT CAUGHT')
