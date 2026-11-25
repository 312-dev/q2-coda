"""Caller Ten (patch/game_callerten.c) on the engine: the songs the worker picks from a real SQLite library, the
rounds and their clock, the clips it asks of the player, the music it gives back, its pictures and the library's
text on the pad, saves, and the glass's corners; through Games' page."""
import ctypes as C
import ctypes.util
import math
import pathlib
import re
import sqlite3
import struct

import pytest

from conftest import O, Files, block, stock_start_with, threads_now
from test_engine import CENTER, E_CENTRE, E_PLAY, E_TOUCH, ENGINE, NEXT, PLAY, RETURN, Eng, GameDef, Input, api, screen
from test_games import PAGE, SHIPPED, Canvas, Page, fnv1a

ROOT = pathlib.Path(__file__).resolve().parents[2]
ART = (ROOT/'assets/games/callerten.bin').read_bytes()
ART_PATH = '/usr/share/q2games/callerten.bin'
SAVE = '/mnt/data/game-callerten'
PLAYS = '/mnt/mmc/.q2pod/ringnav-plays'  # card_store.c's play counts
MAGIC = 0x314e5443
CALLER = ENGINE + 3  # Games' screen: eng_games[] lists it after Pop Stall, Cabin Solitaire and the Evening Puzzle
BROKEN, TUNE, STANDBY, NOSIGNAL, ROUND, END = range(6)
CUE, AIR, CALL, REVEAL = range(4)
TITLES, ARTISTS, DEEP = range(3)
TAP, TICK, ALERT = 2, 1, 3  # clicker_kind as eng_sfx sets it
SONGTABLE = ('create table songtable(id INTEGER PRIMARY KEY AUTOINCREMENT, filename, fileurl, album, songer, '
             'style, name, composer, albumsonger, year INTEGER, track INTEGER, cd_num INTEGER, time INTEGER, '
             'time_end INTEGER, time_create INTEGER, Initial INTEGER, bitdepth INTEGER, samprate INTEGER)')
PICS = re.findall(r'A_(\w+),', (ROOT/'patch/game_callerten_art.h').read_text())
A = {n: k for k, n in enumerate(PICS)}


class Keep(C.Structure):
    _fields_ = [('art', C.c_void_p), ('pk', C.c_void_p), ('owned', C.c_int), ('was_playing', C.c_int), ('watch', C.c_uint)]


class Ct(C.Structure):
    _fields_ = [('k', C.POINTER(Keep)),
                *((n, C.c_int) for n in 'h screen phase mode sel t at reduced round rounds wrongs rights streak score shown '
                                        'pts best_new slot stream_dead'.split()),
                ('row', C.c_int * 4), *((n, C.c_int) for n in 'right pick song'.split()), ('missed', C.c_int * 10),
                *((n, C.c_int) for n in 'nmissed top clip_sec clip_on clip_held heard ask replay needle4 needle_from '
                                        'needle_t turn_t lift lift_dir ring ring_from lamps dim dim_t cut end_t swell'.split()),
                *((n, C.c_int * 4) for n in 'fit_px fit_n full_w maxw scroll'.split()), ('scroll_t', C.c_int),
                ('sfx', C.c_ubyte * 8), ('sfx_n', C.c_int), ('sfx_wait', C.c_int), ('marks', C.c_uint),
                ('magic', C.c_uint), ('best', C.c_uint * 3), ('played', C.c_uint), ('sum', C.c_uint)]


class Song(C.Structure):
    _fields_ = [('id', C.c_uint), ('key', C.c_uint), ('style', C.c_uint), ('sec', C.c_int), ('year', C.c_int),
                ('title', C.c_char * 128), ('artist', C.c_char * 80)]


class Pick(C.Structure):
    _fields_ = [*((n, C.c_int) for n in 'refs done mode why n answers played_n'.split()), ('played', C.c_uint * 512),
                ('seed', C.c_uint), ('song', Song * 80), ('path', (C.c_char * 384) * 13), ('there', C.c_int * 13),
                ('app', C.c_char * 16), *((n, C.c_int) for n in 'stream seq quit'.split()),
                *((n, C.c_int * 13) for n in 'kind from_ms want got off'.split()), ('asked', C.c_uint * 13),
                ('sid', (C.c_char * 40) * 13), ('clip', (C.c_char * 128) * 13), ('sent', C.c_char * 2048)]


def key(path):
    """listen_key's: FNV-1a of the path and its NUL (fnv), never 0."""
    return fnv1a(path.encode() + b'\0') or 1


def music(n, start=0, artists=None, length=200):
    """n songs of distinct titles, each a file on the card: (path, title, artist, style, year, seconds)."""
    return [(f'/mnt/mmc/Music/A{i % (artists or n)}/song{i}.mp3', f'Song {i}', f'Artist {i % (artists or n)}',
             ('Rock', 'Jazz', 'Folk')[i % 3], 1960 + 7 * i, length) for i in range(start, start + n)]


class Rig:
    """Games' page with Caller Ten open over a real SQLite library in tmp_path, the card's files, the player's socket
    (what was sent; the replies it will give), its Library song's state and the timers with their contexts."""
    def __init__(self, fw, tmp_path, songs=(), status=1, files=None, art=ART, open_game=True, server=None):
        self.fw, self.e, self.tmp = fw, api(fw), tmp_path
        self.page = Page(fw)
        self.timers, self.removed = {}, []
        fw.mock('timer_add', self.timer_add)
        fw.mock('timer_remove', lambda t, *_: self.removed.append(t) or self.timers.pop(t, None) and 0)
        self.files = Files(fw, {**({ART_PATH: art} if art is not None else {}), **(files or {})}, card=True)
        fw.mock('fw_fflush', lambda *_: 0), fw.mock('fw_fsync', lambda *_: 0)
        self.sent, self.replies, self.sounds = [], [], []
        fw.mock('fw_send', lambda fd, data, n, *_: self.sent.append(C.string_at(data, n).decode()) or n)
        C.c_int.from_address(O['MCL_FD']).value = 9
        fw.mock('mcl_reply_take_by_key', self.take)
        fw.mock('tk_str_start_with', stock_start_with)  # catalogue_path's test for a server's row
        fw.mock('buzzeer_switch', lambda *_: self.sounds.append(fw.var('clicker_kind', C.c_int).value))
        # the library: stock's database.db as a real file
        self.lib = C.CDLL(ctypes.util.find_library('sqlite3'))
        self.lib.sqlite3_open.argtypes = [C.c_char_p, C.c_void_p]
        self.lib.sqlite3_exec.argtypes = [C.c_void_p] * 5
        self.lib.sqlite3_close.argtypes = [C.c_void_p]
        db = sqlite3.connect(tmp_path/'database.db')
        db.execute(SONGTABLE)
        self.sqls, self.present, self.lengths = [], set(), {}
        for path, title, artist, style, year, sec in songs:
            db.execute('insert into songtable(fileurl,name,songer,style,year,time,time_end) values(?,?,?,?,?,0,0)',
                       (path, title, artist, style, year))
            self.present.add(path)
            self.lengths[path] = sec
        db.commit()
        db.close()
        if server is not None:  # a server's catalogue (catalogue.c's file), attached as cat as catalogue_attach_db does
            cat = sqlite3.connect(tmp_path/'catalogue.db')
            cat.execute(SONGTABLE.replace('songtable', 'servertable'))
            cat.execute('create table servermeta(id integer primary key,fileurl,app,sid,local,seconds integer,size integer,'
                        'mbid,suffix,dup,ntitle integer,nartist integer,naa integer,nalbum integer,track integer)')
            for k, (sid, title, artist, style, year, sec, local) in enumerate(server):
                url = f'/mnt/mmc/.subsonic/cat/{sid}.mp3'
                cat.execute('insert into servertable(id,fileurl,name,songer,style,year,time,time_end) values(?,?,?,?,?,?,0,0)',
                            (0x40000000 + k, url, title, artist, style, year))
                cat.execute('insert into servermeta(id,fileurl,app,sid,local,seconds) values(?,?,?,?,?,?)',
                            (0x40000000 + k, url, 'subsonic', sid, local, sec))
                if local:
                    self.present.add(local)
                    self.lengths[local] = sec
            cat.commit()
            cat.close()
        def open_db(name, out, *_):
            r = self.lib.sqlite3_open(str(tmp_path/'database.db').encode(), out)
            if server is not None:
                self.lib.sqlite3_exec(C.c_void_p.from_address(out).value,
                                      f"attach '{tmp_path/'catalogue.db'}' as cat".encode(), None, None, None)
            return r
        fw.mock('sqlite3_open', open_db)
        fw.mock('sqlite3_exec', self.exec)
        fw.mock('sqlite3_close', lambda d, *_: self.lib.sqlite3_close(d))
        self.threads = threads_now(fw)
        # the clips' thread runs here by its passes (clip_pass), not as a thread: the others run at once
        worker = C.cast(fw.lib.game_callerten__clip_worker, C.c_void_p).value
        self.clip_threads = []
        def threads(t, attr, fn, arg, *_):
            C.c_ulong.from_address(t).value = 1
            if fn == worker:
                self.clip_threads.append(arg)
                return 0
            self.threads.append((fn, arg))
            C.CFUNCTYPE(C.c_void_p, C.c_void_p)(fn)(arg)
            return 0
        fw.mock('fw_pthread_create', threads)
        fw.mock('fw_pthread_detach', lambda *_: 0)
        fw.mock('fw_access', lambda p, m, *_: 0 if C.string_at(p).decode() in self.present | set(self.files.files) else -1)
        self.opened, self.keep = {}, []
        self.length = C.CFUNCTYPE(C.c_int, C.c_void_p)(lambda p: self.lengths[self.opened[p]])
        fw.mock('taglib_file_new', self.taglib)
        fw.mock('taglib_file_audioproperties', lambda f, *_: f)
        # the Library's player: its state (1 stopped, 2 playing, 3 paused), its place, its saves and starts
        self.status, self.place, self.saves, self.starts, self.stops = status, (95, 240), 0, [], 0
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('player_playtime_and_length', self.playtime)
        fw.mock('player_save_stop', self.save_stop)
        fw.mock('player_save_start', self.save_start)
        fw.mock('player_stop', self.player_stop)
        if open_game:
            self.open()

    def timer_add(self, fn, ctx, ms, *_):
        t = len(self.timers) + len(self.removed) + 100
        self.timers[t] = (fn, ctx, ms)
        return t

    def watches(self):
        watch = C.cast(self.fw.lib.game_callerten__ct_watch, C.c_void_p).value
        return {t: v for t, v in self.timers.items() if v[0] == watch}

    def run_timers(self):
        """One pass of the game's watch (ct_watch): removed when it says so, as AWTK removes a timer."""
        for t, (fn, ctx, every) in self.watches().items():
            assert every == 100
            info = C.create_string_buffer(0x40)  # AWTK calls a timer with its timer_info_t, the ctx inside
            C.c_void_p.from_buffer(info, O['TIMER_CTX']).value = ctx
            if C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(C.addressof(info)) != 8:
                self.timers.pop(t, None)

    def take(self, name, *_):
        name = C.string_at(name).decode()
        for k, (n, text) in enumerate(self.replies):
            if n == name:
                del self.replies[k]
                return block(self.fw, text.encode())
        return 0

    def exec(self, db, sql, cb, ctx, err, *_):
        self.sqls.append(C.string_at(sql).decode())
        return self.lib.sqlite3_exec(db, sql, cb, ctx, err)

    def taglib(self, path, kind, *_):
        path = C.string_at(path).decode()
        if path not in self.lengths:
            return 0
        vtable = (C.c_void_p * 4)(0, 0, 0, C.cast(self.length, C.c_void_p).value)
        props = C.c_void_p(C.addressof(vtable))
        self.keep += [vtable, props]
        self.opened[C.addressof(props)] = path
        return C.addressof(props)

    def playtime(self, sec, total, *_):
        C.c_int.from_address(sec).value, C.c_int.from_address(total).value = self.place if self.status != 1 else (-1, 0)
        return 0

    def save_stop(self, *_):
        self.saves += 1
        self.saved = (self.status, self.place)
        self.status = 1
        return 0

    def save_start(self, pause, *_):
        self.starts.append(pause)
        self.status = 3 if pause else 2
        return 0

    def player_stop(self, *_):
        self.stops += 1
        return 0

    # the game
    def open(self):
        self.page.go(CALLER)
        assert self.page.s.screen == CALLER
        en = self.fw.var('engine__en', C.c_void_p).value
        self.g = Ct.from_address(en + C.sizeof(Eng) + (-C.sizeof(Eng)) % 8)
        self.stepper = self.fw.fn('game_callerten__step', C.c_int, C.c_void_p, C.POINTER(Input))
        return self

    def step(self, ticks=0, pressed=0, tx=0, ty=0, n=1):
        r = None
        for _ in range(n):
            r = self.stepper(C.addressof(self.g), C.byref(Input(0, ticks, ticks, 0, pressed, 0, tx, ty)))
            ticks = pressed = 0
        return r

    @property
    def pk(self):
        return Pick.from_address(self.g.k.contents.pk or 0)

    def clips(self):
        """The clips asked of the player: (path, second)."""
        return [(m[1], int(m[2])) for s in self.sent for m in re.finditer(r'\{set-songurl\\(.*?)\|ss=(\d+)\|', s)]

    def start(self, mode=TITLES):
        """Tune mode in and call: the worker's pick (run at once), the first round's clip asked for."""
        self.g.sel = mode
        self.step(pressed=E_CENTRE)
        assert self.g.screen == STANDBY and self.pk.done
        self.step()

    def hear(self):
        """The player answers that the clip is heard, at its next ask."""
        self.replies.append(('player-status', 'playing'))
        for _ in range(7):
            self.step()
            if self.g.phase == AIR:
                return
        raise AssertionError('not heard')

    def call(self, row, after=0):
        """Row dialed after `after` steps of the clock, the call placed, the reveal run to the next round's start."""
        self.hear()
        self.step(n=after)
        self.step(ticks=row + 1 - self.g.sel)
        self.step(pressed=E_CENTRE)
        assert self.g.phase == CALL and self.g.pick == row
        self.step(n=20)
        assert self.g.phase == REVEAL
        self.step(n=60)

    def right_row(self):
        return self.g.right

    def wrong_row(self):
        return (self.g.right + 1) % 4

    def show(self, text):
        """The song reading text put on a decoy's row of this round, as deal could have dealt it: its row."""
        song = next(k for k in range(self.pk.n) if self.visible(k) == text)
        if song not in self.g.row:
            self.g.row[(self.g.right + 1) % 4] = song
            self.g.fit_px[:] = [0] * 4
        return list(self.g.row).index(song)

    def visible(self, i):
        s = self.pk.song[i]
        return (s.artist if self.pk.mode == ARTISTS else s.title).decode()

    def quit(self):
        """Games' menu, Quit: the page shows its chooser on its next pass, the game closed."""
        self.page.key(RETURN, NEXT)
        self.page.key(CENTER)
        self.page.g.show(None)
        assert not self.fw.var('engine__en', C.c_void_p).value

    def paint(self, canvas, clip=(0, 0, 375, 320)):
        buf = screen(canvas, clip=clip, origin=(0, 0))
        canvas.ops.clear()
        self.e['eng_paint'](canvas.p)
        return buf


@pytest.fixture
def rig(fw, tmp_path):
    return lambda **kw: Rig(fw, tmp_path, **kw)


def test_caller_ten_is_games_fourth_engine_row_full_screen_still_and_its_state_fits(fw, rig):
    r = rig(songs=music(12))
    game = fw.var('eng_callerten', GameDef)
    assert game.name == b'Caller Ten' and game.flags == 2 | 4 and game.size == C.sizeof(Ct)  # ENG_STILL | ENG_FULL
    assert r.e['eng_count']() == r.page.s.engines == len(SHIPPED) and SHIPPED[3] == 'Caller Ten'
    assert r.g.h == 320 and r.g.screen == TUNE and r.g.k.contents.art and not r.g.k.contents.owned
    assert C.sizeof(Ct) < 1024 and C.sizeof(Pick) < 32 * 1024  # with the art's 192 KB and the worker's 24, under 256 and len(ART) < 200 * 1024  # heap: the art, the rows, the state
    assert r.status == 1 and not r.sent and not r.saves  # tuning takes nothing: the music plays on


@pytest.mark.parametrize('art', [None, ART[:-1] + bytes([ART[-1] ^ 1]), ART[:1000]])
def test_missing_or_damaged_art_leaves_with_a_word_not_a_crash(fw, rig, art):
    r = rig(songs=music(12), art=art)
    assert r.g.screen == BROKEN and not r.g.k.contents.art
    canvas = Canvas(fw)
    buf = r.paint(canvas)
    assert set(buf) == {0xff000000}
    r.step()
    assert Eng.from_address(fw.var('engine__en', C.c_void_p).value).quit == 1  # back to Games' list


@pytest.mark.parametrize('n, why', [(0, 1), (11, 1), (12, 0)])
def test_a_library_too_small_for_a_contest_says_so_honestly(fw, rig, n, why):
    r = rig(songs=music(n))
    r.g.sel = TITLES
    r.step(pressed=E_CENTRE)
    r.step()
    if why:
        assert r.g.screen == NOSIGNAL and r.pk.why == why and not r.clips() and not r.saves
        canvas = Canvas(fw)
        r.paint(canvas)
        words = [o.text for o in canvas.ops if o.kind == 'text']
        assert words == ['Add more music', 'to play', 'A contest needs 12 songs', 'Center: quit']
        r.step(pressed=E_CENTRE)
        assert Eng.from_address(fw.var('engine__en', C.c_void_p).value).quit == 1
    else:
        assert r.g.screen == ROUND and r.g.rounds == 10 and len(r.clips()) == 1


@pytest.mark.parametrize('local, short, server, screen, callers', [
    (12, 0, 0, ROUND, 10), (13, 0, 0, ROUND, 10), (40, 0, 0, ROUND, 10), (9000, 0, 0, ROUND, 10),
    (13, 12, 0, ROUND, 1),        # the pool card: twelve 25 s tones and one song a clip can come from
    (40, 30, 0, ROUND, 10),       # ten long enough among forty, wherever the random order puts them
    (12, 0, 9000, ROUND, 10),     # a server's library, a dozen songs on the card
    (11, 0, 9000, NOSIGNAL, 0),   # one fewer on the card: no contest
])
def test_the_callers_are_the_card_songs_a_clip_can_come_from_up_to_ten(fw, tmp_path, local, short, server, screen,
                                                                         callers):
    songs = [(*s[:5], 25 if i < short else 200) for i, s in enumerate(music(local))]
    songs += [(f'/mnt/mmc/.subsonic/cat/{i}.mp3', f'Server {i}', f'Cloud {i % 50}', 'Rock', 2001, 300) for i in range(server)]
    r = Rig(fw, tmp_path, songs=songs)
    r.start()
    assert r.g.screen == screen and (r.g.rounds if screen == ROUND else 0) == callers


def test_spoken_audio_and_a_servers_rows_never_count_nor_play(fw, rig):
    spoken = [(f'/mnt/mmc/{d}/x{i}.mp3', f'Talk {d}{i}', 'Host', 'Speech', 2000, 900)
              for d in ('Audiobooks', 'audiobooks', 'Podcasts') for i in range(6)]
    spoken += [(f'/mnt/mmc/Music/book{i}.m4b', f'Book {i}', 'Reader', 'Speech', 2000, 900) for i in range(6)]
    server = [(f'/mnt/mmc/.subsonic/cat/{i}.mp3', f'Server {i}', 'Cloud', 'Rock', 2001, 300) for i in range(20)]
    r = rig(songs=music(11) + spoken + server)
    r.start()
    assert r.g.screen == NOSIGNAL and r.pk.n == 11  # eleven of music: no contest, whatever else the card holds


def test_a_contest_plays_only_the_cards_music(fw, tmp_path):
    spoken = [(f'/mnt/mmc/Podcasts/x{i}.mp3', f'Talk {i}', 'Host', 'Speech', 2000, 900) for i in range(10)]
    spoken += [(f'/mnt/mmc/Music/book{i}.M4B', f'Book {i}', 'Reader', 'Speech', 2000, 900) for i in range(10)]
    server = [(f'/mnt/mmc/.subsonic/cat/{i}.mp3', f'Server {i}', 'Cloud', 'Rock', 2001, 300) for i in range(20)]
    r = Rig(fw, tmp_path, songs=music(14) + spoken + server)
    r.start()
    assert r.g.screen == ROUND
    sql = [s for s in r.sqls if 'random()' in s][0]
    assert 'main.songtable' in sql and 'servertable' not in sql and "fileurl not like '%.m4b'" in sql
    titles = {r.pk.song[i].title.decode() for i in range(r.pk.n)}
    assert r.pk.n == 14 and titles == {f'Song {i}' for i in range(14)}
    for k in range(10):
        r.call(r.right_row())
    assert r.g.screen == END and all(p.startswith('/mnt/mmc/Music/') for p, _ in r.clips())


def test_deep_cuts_leave_out_songs_played_twice_or_more(fw, tmp_path):
    songs = music(30)
    counts = [(songs[i][0], 2 + i % 3) for i in range(14)] + [(songs[14][0], 1)]
    data = b''.join(struct.pack('<2I', key(p), n) for p, n in counts)
    r = Rig(fw, tmp_path, songs=songs, files={PLAYS: data + b'\0' * (512 * 8 - len(data))})
    r.start(DEEP)
    assert r.g.screen == ROUND and r.pk.played_n == 14
    kept = {r.pk.song[i].title.decode() for i in range(r.pk.n)}
    assert kept == {f'Song {i}' for i in range(14, 30)}  # once played is a deep cut still
    assert all(r.pk.song[i].key == key(songs[int(r.pk.song[i].title[5:])][0]) for i in range(r.pk.n))


def test_the_worker_keeps_answers_that_are_there_and_long_enough_first(fw, tmp_path):
    songs = music(20, length=200)
    songs = [s if i % 4 else (*s[:5], 30) for i, s in enumerate(songs)]  # every fourth too short for a clip
    r = Rig(fw, tmp_path, songs=songs)
    r.present.discard(songs[1][0])  # gone from the card since the last scan
    r.start()
    assert r.pk.answers == 13
    paths = [r.pk.path[i].value.decode() for i in range(13)]
    for i in range(13):
        s = r.pk.song[i]
        assert s.sec == 200 and paths[i] == songs[int(s.title[5:])][0]
    assert songs[1][0] not in paths and not any(songs[k][0] in paths for k in range(0, 20, 4))


def test_a_round_deals_four_rows_that_read_apart_the_answer_once_genre_first(fw, tmp_path):
    songs = music(16, artists=5)
    songs[3] = (*songs[3][:1], 'SONG 0', *songs[3][2:])  # reads as song 0's title, case aside
    r = Rig(fw, tmp_path, songs=songs)
    r.start()
    seen = 0
    for k in range(10):
        rows = [r.visible(i) for i in r.g.row]
        assert len({t.lower() for t in rows}) == 4 and r.g.row[r.g.right] == r.g.song
        assert sum(i == r.g.song for i in r.g.row) == 1
        style = r.pk.song[r.g.song].style
        seen += sum(r.pk.song[i].style == style for i in r.g.row if i != r.g.song)
        r.call(r.right_row())
    assert seen >= 20  # of 30 decoys, most share the answer's genre when there are enough


def test_by_artist_asks_who_is_playing_and_its_rows_are_artists(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(16, artists=6))
    r.start(ARTISTS)
    assert r.g.screen == ROUND and r.pk.mode == ARTISTS
    rows = [r.pk.song[i].artist.decode() for i in r.g.row]
    assert len(set(rows)) == 4 and rows[r.g.right] == r.pk.song[r.g.song].artist.decode()
    canvas = Canvas(fw)
    r.paint(canvas)
    assert [o.text for o in canvas.ops if o.kind == 'text'] == rows


def test_by_artist_with_three_artists_is_no_contest(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(20, artists=3))
    r.start(ARTISTS)
    assert r.g.screen == NOSIGNAL and r.pk.why == 2  # WHY_ARTISTS


def test_the_clock_starts_when_the_player_says_the_clip_is_heard_not_when_asked(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(14))
    r.start()
    (path, sec), = r.clips()
    s = r.pk.song[r.g.song]
    assert path == r.pk.path[r.g.song].value.decode() and 60 <= sec <= 120 and sec == s.sec * (sec * 100 // s.sec) // 100
    asks = lambda: sum(m.count('{player-status\\request}') for m in r.sent)
    r.step(n=200)
    assert r.g.phase == CUE and r.g.at == 0 and asks() == 33  # asked every 6 steps, never waited on
    r.replies.append(('player-status', 'ready'))
    r.step(n=6)
    assert r.g.phase == CUE
    r.step(pressed=E_CENTRE)
    assert r.g.phase == CUE  # nothing to answer yet
    r.hear()
    assert r.g.at == 0 and r.g.heard
    r.step(n=60)
    assert r.g.at == 60 and r.g.lamps == 9


@pytest.mark.parametrize('after, points', [(0, 100), (299, 55), (599, 10), (900, 10)])
def test_scoring_falls_from_100_to_10_over_ten_seconds(fw, tmp_path, after, points):
    r = Rig(fw, tmp_path, songs=music(14))
    r.start()
    r.hear()
    r.step(n=after)
    r.step(ticks=r.right_row() + 1 - r.g.sel)
    r.step(pressed=E_CENTRE)
    assert r.g.pts == points  # at 0, 5 and 10 s of the clock; one lamp stays after
    r.step(n=20)
    assert r.g.score == points and r.g.phase == REVEAL


def test_three_wrong_end_the_contest_and_the_summary_lists_them(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(14))
    r.start()
    r.call(r.right_row())
    for k in range(3):
        assert r.g.screen == ROUND
        r.call(r.wrong_row())
    assert r.g.screen == END and r.g.wrongs == 3 and r.g.round == 4 and r.g.nmissed == 3 and r.g.rights == 1
    assert r.sent[-1].endswith('{mcl-stop\\null}')
    saved = r.files.files[SAVE]
    assert struct.unpack('<6I', saved)[:2] == (MAGIC, r.g.score) and struct.unpack('<6I', saved)[4] == 1


def test_the_reveal_pauses_the_clip_and_the_next_caller_starts_another(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(14))
    r.start()
    r.hear()
    r.step(pressed=E_CENTRE)
    r.step(n=19)
    assert r.g.phase == CALL and not r.sent[-1].startswith('{mcl-pause')
    r.step()
    assert r.g.phase == REVEAL and r.sent[-1] == '{mcl-pause\\null}'
    r.step(n=60)
    assert r.g.round == 1 and r.g.phase == CUE and len(r.clips()) == 2
    assert r.sent[-1].startswith('{mcl-stop\\null}{set-songurl\\')  # the paused clip ended before the next


def test_play_replays_the_clip_once_a_round(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(14))
    r.start()
    r.hear()
    r.step(n=100)
    r.step(pressed=E_PLAY)
    assert r.sent[-1] == '{mcl-seek\\%d}' % r.g.clip_sec and r.g.replay == 1 and r.g.at == 101
    r.step(n=3)
    assert r.g.replay == 3
    sent = len(r.sent)
    r.step(pressed=E_PLAY)
    assert len(r.sent) == sent + (r.g.at % 6 == 0)  # nothing more but the player's asks


def test_after_a_replay_the_next_callers_rings_start_from_their_first_frame(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(14))
    r.start()
    r.hear()
    r.step(n=100)
    r.step(pressed=E_PLAY)
    r.step(n=30)
    r.step(pressed=E_CENTRE)
    r.step(n=80)
    assert r.g.round == 1 and r.g.replay == 0
    r.hear()
    for k in range(40):  # the rings' frame is a picture's offset: never before the first
        assert r.g.ring == (r.g.at // 10) % 3
        r.step()


def test_calling_in_keeps_the_frame_timer_until_the_first_caller_is_on(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(14))
    r.e['eng_key'](CENTER, 1), r.e['eng_key'](CENTER, 0)
    r.page.now += 16
    r.e['frame'](None)
    assert r.g.screen == STANDBY and r.e['eng_live']()  # stand by moves on by itself, with no key
    r.page.now += 16
    r.e['frame'](None)
    assert r.g.screen == ROUND and r.e['eng_live']() and len(r.clips()) == 1


def test_a_finger_on_a_row_places_the_call(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(14))
    r.start()
    r.hear()
    r.step(pressed=E_TOUCH, tx=250, ty=161 + 30 * 2 + 5)
    assert r.g.phase == CALL and r.g.pick == 2 and r.g.sel == 3


def test_the_wheel_turns_the_dial_one_to_four_and_stops_at_each_end(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(14))
    r.start()
    seen = []
    for t in (1, 1, 1, 1, -1, -5, -1):
        r.step(ticks=t)
        seen.append(r.g.sel)
    assert seen == [2, 3, 4, 4, 3, 1, 1] and r.g.turn_t >= 0 and r.sounds.count(TICK) == 5


def test_a_song_gone_from_the_card_mid_game_is_passed_over_without_a_stall(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(20))
    r.g.sel = TITLES
    r.step(pressed=E_CENTRE)
    gone = r.pk.path[1].value.decode()
    r.present.discard(gone)  # after the pick, before its round
    r.step()
    assert r.pk.there[1] == -1  # the look one round ahead saw it
    r.call(r.right_row())
    assert r.g.round == 1 and r.g.song == 2 and r.g.phase == CUE  # the next caller at once, with the next song
    assert gone not in [p for p, _ in r.clips()]
    # one the player cannot open: passed over at its answer, the caller's number kept
    r.replies.append(('player-status', 'error'))
    r.step(n=6)
    assert r.g.round == 1 and r.g.song == 3 and r.pk.there[2] == -1 and len(r.clips()) == 3


def test_a_clip_never_heard_is_passed_over_after_five_seconds(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(20))
    r.start()
    r.step(n=300)
    assert r.g.song == 0
    r.step(n=6)
    assert r.g.song == 1 and r.g.round == 0 and len(r.clips()) == 2


def test_with_spares_spent_the_contest_ends_short_not_stuck(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(12))
    r.start()
    assert r.pk.answers == 12 and r.g.rounds == 10
    for k in range(9):
        r.replies.append(('player-status', 'error'))
        r.step(n=6)
    assert r.g.song == 9 and r.g.round == 0
    for k in range(3):
        r.replies.append(('player-status', 'error'))
        r.step(n=6)
    assert r.g.screen == END and r.g.round == 0


@pytest.mark.parametrize('status', [2, 3, 1])  # playing, paused, nothing
@pytest.mark.parametrize('leave', ['quit', 'screen off', 'pause'])
def test_what_played_before_is_given_back_as_it_was_whichever_way_the_game_is_left(fw, tmp_path, status, leave):
    r = Rig(fw, tmp_path, songs=music(14), status=status)
    r.start()
    assert r.g.k.contents.owned and r.saves == (status != 1) and r.status == 1  # held: stopped, its place kept
    now_time = fw.fn('now_time', C.c_int, C.POINTER(C.c_int), C.POINTER(C.c_int))
    sec, total = C.c_int(), C.c_int()
    assert now_time(sec, total) == (3 if status != 1 else 1)
    if status != 1:
        assert (sec.value, total.value) == r.place  # Now Playing reads it paused where it was, all game long
    assert fw.fn('addon_game_audio', C.c_int)() == 1  # stock's player starts nothing meanwhile
    r.hear()
    r.step(n=30)
    if leave == 'quit':
        r.quit()
    elif leave == 'screen off':
        fw.var('g_backlight_status', C.c_ubyte).value = 0
        r.page.now += 16
        r.e['frame'](None)
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        assert r.page.s.paused == 1 and r.sent[-1] == '{mcl-pause\\null}'
        r.run_timers()
        assert not r.starts and r.status == 1 and fw.var('engine__en', C.c_void_p).value  # held, not left
        r.page.close()  # Home: the page goes
    else:
        r.page.key(RETURN)
        assert r.page.s.paused == 2 and r.sent[-1] == '{mcl-pause\\null}'
        r.run_timers()
        assert not r.starts
        r.page.key(RETURN)  # Continue
        r.step()
        assert r.sent[-1] == '{mcl-play\\null}' and not r.watches() and not r.g.k.contents.watch
        r.step(n=10)
        r.quit()
    assert not r.starts  # nothing yet: the game's pause went first, then the close
    r.run_timers()
    assert r.sent[-1] == '{mcl-stop\\null}' and not r.watches()
    assert r.starts == ([0] if status == 2 else [1] if status == 3 else [])  # playing on, or paused, from its place
    assert r.status == status and r.stops == 0 and r.saves == (status != 1)
    assert fw.fn('addon_game_audio', C.c_int)() == 0
    assert now_time(sec, total) == status


def test_leaving_before_a_contest_takes_nothing_and_frees_everything(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(14), status=2)
    r.step(ticks=1)
    r.quit()
    r.run_timers()
    assert r.status == 2 and not r.saves and not r.starts and not r.sent and not r.watches()


def test_a_missed_song_on_the_summary_plays_in_full_and_leaves(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(14), status=3)
    r.start()
    for k in range(3):
        r.call(r.wrong_row())
    assert r.g.screen == END and r.g.nmissed == 3
    r.step(ticks=1)
    assert r.g.sel == 1
    r.step(pressed=E_CENTRE)
    assert not r.g.k.contents.owned and r.sent[-1] == '{mcl-stop\\null}'
    assert Eng.from_address(fw.var('engine__en', C.c_void_p).value).quit == 1
    r.page.g.show(None)
    r.run_timers()
    assert not r.starts  # the queue is the missed song's now: nothing held is given back over it


def test_play_on_the_summary_tunes_in_again_and_keeps_the_sound(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(14), status=2)
    r.start()
    for k in range(10):
        r.call(r.right_row())
    assert r.g.screen == END and r.g.nmissed == 0 and r.g.best_new
    r.step(pressed=E_PLAY)
    assert r.g.screen == TUNE and r.g.k.contents.owned
    r.start()
    assert r.g.screen == ROUND and r.saves == 1


def test_bests_and_contests_are_saved_at_a_contests_end_and_read_back(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(14))
    r.start()
    for k in range(10):
        r.call(r.right_row())
    assert r.g.best[TITLES] == r.g.score == 1000 and r.g.played == 1
    saved = r.files.files[SAVE]
    r.quit()
    r.run_timers()
    r.open()
    assert list(r.g.best) == [1000, 0, 0] and r.g.played == 1


def sealed(words):
    raw = b''.join(struct.pack('<I', w) for w in words)
    return raw + struct.pack('<I', fnv1a(raw))


@pytest.mark.parametrize('save, best', [
    (sealed([MAGIC, 400, 0, 90, 3]), [400, 0, 90]),
    (sealed([MAGIC, 400, 0, 90, 3])[:-1] + b'\0', [0, 0, 0]),  # torn
    (sealed([MAGIC, 400, 0, 90, 3])[:12], [0, 0, 0]),  # short
    (sealed([0x314b5242, 400, 0, 90, 3]), [0, 0, 0]),  # another game's magic
    (sealed([MAGIC, 1001, 0, 90, 3]), [0, 0, 0]),  # sealed, but no contest scores that
])
def test_a_save_loads_only_whole_and_sane(fw, tmp_path, save, best):
    r = Rig(fw, tmp_path, songs=music(14), files={SAVE: save})
    assert list(r.g.best) == best


def lit_outside_glass(buf):
    out = []
    for y in [*range(80), *range(240, 320)]:
        for x in [*range(80), *range(295, 375)]:
            if buf[y * 375 + x] & 0xffffff:
                ox, oy = (80 - x - .5 if x < 80 else x + .5 - 295), (80 - y - .5 if y < 80 else y + .5 - 240)
                if ox > 0 and oy > 0 and math.hypot(ox, oy) > 80:
                    out.append((x, y))
    return out


def texts_inside(canvas, x0, x1):
    return all(x0 <= o.rect[0] and o.rect[0] + o.rect[2] <= x1 for o in canvas.ops if o.kind == 'text')


def test_nothing_lit_falls_under_the_glass_on_any_screen_and_text_keeps_off_the_corners(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(14))
    canvas = Canvas(fw)
    inset = r.e['eng_inset']
    def check():
        buf = r.paint(canvas)
        assert lit_outside_glass(buf) == []
        for o in canvas.ops:
            if o.kind == 'text':
                x, y, w, h = o.rect
                assert inset(y) + 8 <= x and x + w <= 375 - inset(y + h) - 8, o
    check()
    r.start()
    check()
    r.hear()
    r.step(n=30)
    check()
    for k in range(3):
        r.call(r.wrong_row())
    for t in range(40):
        r.step()
        check() if t in (0, 7, 14, 39) else None


def test_the_round_paints_the_mockups_layers_and_the_rows_in_the_theme_face(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(14))
    r.start()
    r.hear()
    r.step(ticks=2)
    canvas = Canvas(fw)
    buf = r.paint(canvas)
    words = [(o.text, o.color & 0xffffff, o.rect) for o in canvas.ops if o.kind == 'text']
    assert [w for w, _, _ in words] == [r.visible(i) for i in r.g.row]
    bgr = lambda rgb: (rgb & 255) << 16 | rgb & 0xff00 | rgb >> 16  # a color_t as stock's canvas takes it
    assert [c for _, c, _ in words] == [bgr(c) for c in (0x1E1410, 0x1E1410, 0x14102E, 0x1E1410)]  # chosen: ink on gold
    assert all(rect[0] == 187 and abs(rect[1] + 12 - (161 + 30 * i)) <= 1 for i, (_, _, rect) in enumerate(words))
    gold = buf[175 * 375 + 300 + 46 * 375]  # the third row's gold, right of its title
    assert gold == 0xffffd23f or abs((gold >> 16 & 255) - 0xff) < 8
    # ON AIR lit, all ten lamps, the needle on TITLES' station
    assert buf[76 * 375 + 160] & 0xff0000 > 0xc00000
    assert buf[30 * 375 + 175] == 0xffd62c42 and buf[30 * 375 + 173] != 0xffd62c42  # 175.5, 2.4 px wide


def measure_like_inter(fw, canvas):
    """canvas_measure_text at about Inter's width: 0.55 em a character."""
    fw.mock('canvas_measure_text', lambda c, s, n, *_: 0.55 * canvas.px * n)


LONG = 'The Slow Train Home, Parts One and Two (Live at the Old Mill, 1974)'
CJK = '夜明けのスキャット'
CYRILLIC = 'Подмосковные вечера'


def test_long_titles_shrink_then_are_cut_and_a_chosen_one_scrolls_within_its_line(fw, tmp_path):
    songs = music(14)
    songs[:4] = [(*s[:1], t, *s[2:]) for s, t in zip(songs, (LONG, 'Harbor Lights at Dawn', CJK, CYRILLIC))]
    r = Rig(fw, tmp_path, songs=songs)
    r.start()
    canvas = Canvas(fw)
    measure_like_inter(fw, canvas)
    i = r.show(LONG)
    r.hear()
    r.paint(canvas)
    drawn = {o.text: o for o in canvas.ops if o.kind == 'text'}
    cut = next(t for t in drawn if t.endswith('...') and LONG.startswith(t[:-3]))
    assert drawn[cut].px == 14 and drawn[cut].rect[2] - 2 <= 152
    assert texts_inside(canvas, 187, 188 + 152 + 1)  # every title within its line on the pad
    r.step(ticks=(1 if i == 0 else 0) + 1 - r.g.sel)  # another row, then this one: its scroll starts there
    r.step(ticks=i + 1 - r.g.sel)
    assert r.g.sel == i + 1 and r.g.scroll_t == 1
    clips = []
    fw.mock('canvas_set_clip_rect', lambda c, box, *_: clips.append(tuple((C.c_int * 4).from_address(box))) or 0)
    r.step(n=39)
    assert r.g.scroll[i] == 0  # still for 40 steps, then a px every 2
    r.step(n=21)
    sc = r.g.scroll[i]
    assert sc == 10
    r.paint(canvas)
    whole = next(o for o in canvas.ops if o.kind == 'text' and o.text == LONG)
    assert whole.rect[0] == 188 - sc - 1 and (188, 161 + 30 * i - 14, 152, 29) in clips
    r.step(n=2 * 200)
    assert 0 <= r.g.scroll[i] <= r.g.full_w[i] - 152


@pytest.mark.parametrize('title', [CJK, CYRILLIC, 'Ünïcödé Çafé', 'Ελληνικά'])
def test_a_title_in_any_script_reaches_the_canvas_whole_within_the_pad(fw, tmp_path, title):
    songs = music(14)
    songs = [(*s[:1], title if i == 0 else s[1], *s[2:]) for i, s in enumerate(songs)]
    r = Rig(fw, tmp_path, songs=songs)
    r.start()
    canvas = Canvas(fw)
    measure_like_inter(fw, canvas)
    r.show(title)
    r.paint(canvas)
    texts = [o.text for o in canvas.ops if o.kind == 'text']
    assert title in texts and texts_inside(canvas, 187, 341)


def test_a_frame_of_play_marks_only_what_moved(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(14))
    rects = []
    fw.mock('widget_invalidate_force', lambda w, box, *_: rects.append(tuple((C.c_int * 4).from_address(box)) if box else None))
    r.start()
    r.hear()
    def frame():
        en = Eng.from_address(fw.var('engine__en', C.c_void_p).value)
        en.rects = 0
        r.step()
        return [tuple(en.rect[k]) for k in range(en.rects)]
    assert frame() in ([(148, 89, 320, 105)], [(148, 89, 320, 105), (55, 28, 137, 92)])  # the lamps lighting
    r.step(n=12)
    quiet = [frame() for _ in range(30)]
    assert all(len(f) <= 2 for f in quiet) and any(f == [] for f in quiet)


# Clips streamed from a music server: the bundled app's clip channel (docs/server-library.md#clips), faked here by
# its two files, and the card's songs as the fallback.
CLIP_REQ, CLIP_ANS = '/tmp/q2-subsonic.clip', '/tmp/q2-subsonic.clips'


def server_songs(n, local=0, length=240):
    """n server-only songs (sid, title, artist, style, year, seconds, offline copy), the first `local` with a copy."""
    return [(f'sid{i}', f'Server {i}', f'Cloud {i % 9}', ('Rock', 'Jazz')[i % 2], 1990 + i % 30, length,
             f'/mnt/mmc/.subsonic/stream/Cloud/Server {i} [sid{i}].mp3' if i < local else '') for i in range(n)]


class App:
    """The bundled app's side of the clip channel: it answers what the request asks, a clip a ranged fetch from its
    start point, or nothing (a stall), or fail."""
    def __init__(self, rig, up=True):
        self.r, self.answers = rig, {}
        rig.files.files[CLIP_ANS] = f'#Q2CLIPS/1\tserver={"up" if up else "down"}\n'.encode()

    def request(self):
        text = self.r.files.files.get(CLIP_REQ, b'').decode()
        lines = text.splitlines()
        seq = int(lines[0].split('seq=')[1]) if lines else 0
        return seq, [tuple(line.split('\t')) for line in lines[1:]]

    def answer(self, state='done'):
        seq, want = self.request()
        named = {slot for slot, *_ in want}
        for slot in [k for k in self.answers if k not in named]:  # the spec: a clip no longer named goes
            self.r.present.discard(self.answers.pop(slot).split('\t')[4])
        for slot, sid, start, length in want:
            if slot in self.answers and self.answers[slot].split('\t')[2] == 'done':
                continue
            path = f'/mnt/mmc/.subsonic/clips/{sid}.mp3'
            if state == 'done':
                self.r.present.add(path)
                self.r.lengths[path] = int(length) // 1000
            self.answers[slot] = (f'{seq}\t{slot}\tfail\t0\t' if state == 'fail'  # the app's fail: no path
                                  else f'{seq}\t{slot}\t{state}\t{start}\t{path}')
        head = self.r.files.files[CLIP_ANS].decode().splitlines()[0]
        self.r.files.files[CLIP_ANS] = '\n'.join([head, *self.answers.values()]).encode() + b'\n'


def clip_pass(r):
    for arg in r.clip_threads:
        r.fw.fn('game_callerten__clip_pass', None, C.c_void_p)(arg)


def kinds(r):
    return [r.pk.kind[i] for i in range(r.pk.answers)]


CARD_K, COPY_K, STREAM_K = range(3)


def test_a_library_mostly_on_the_server_gets_ten_callers_streamed(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(3), server=server_songs(300))
    app = App(r)
    r.start()
    assert r.g.screen == ROUND and r.g.rounds == 10 and kinds(r).count(STREAM_K) == 10 and len(r.clip_threads) == 1
    clip_pass(r)
    seq, want = app.request()
    assert len(want) == 2 and seq == r.pk.seq  # this caller's clip and the next's, prefetched
    first = [w for w in want if int(w[0]) == r.g.song]
    assert first and int(first[0][2]) == r.pk.from_ms[r.g.song]
    app.answer()
    clip_pass(r)
    r.step()
    path, sec = r.clips()[-1]
    assert path.startswith('/mnt/mmc/.subsonic/clips/') and sec == 0  # the clip's file starts at its start
    for k in range(10):
        if r.g.screen != ROUND:
            break
        clip_pass(r), app.answer(), clip_pass(r)
        r.step(n=2)
        r.call(r.right_row())
    assert r.g.screen == END and r.g.round == 10
    played = [p for p, _ in r.clips()]
    assert all(p.startswith(('/mnt/mmc/.subsonic/clips/', '/mnt/mmc/Music/')) for p in played)
    assert sum(p.startswith('/mnt/mmc/.subsonic/clips/') for p in played) >= 7  # three card songs at most


def test_the_next_callers_clip_is_asked_for_while_this_one_plays(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(3), server=server_songs(40))
    app = App(r)
    r.start()
    clip_pass(r), app.answer(), clip_pass(r)
    r.step()
    for k in range(4):  # the next caller's clip, when it streams, is there before its round
        nxt = r.g.slot
        clip_pass(r), app.answer(), clip_pass(r)
        assert r.pk.kind[nxt] != STREAM_K or r.pk.got[nxt] == 1
        r.call(r.right_row())
        assert r.g.song == nxt and r.g.phase == CUE  # no wait for it
        if r.pk.kind[nxt] == STREAM_K:
            assert r.clips()[-1][0].endswith(f'{r.pk.sid[nxt].value.decode()}.mp3')
            assert r.clips()[-1][0] in r.present  # still there: the request named it until it played


def test_a_stalled_stream_gives_way_to_a_card_song_within_its_bound(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(12), server=server_songs(60))
    app = App(r)  # it never answers
    r.start()
    stream = [i for i in range(r.pk.answers) if r.pk.kind[i] == STREAM_K]
    assert stream and r.g.song == stream[0] or r.pk.kind[r.g.song] != STREAM_K
    if r.pk.kind[r.g.song] == STREAM_K:
        for k in range(600):
            clip_pass(r)
            r.step()
            if r.pk.kind[r.g.song] != STREAM_K:
                break
        assert k <= 481 and r.g.round == 0 and r.pk.kind[r.g.song] != STREAM_K
    assert r.clips()[-1][0].startswith('/mnt/mmc/Music/')  # a card song's clip, the caller's number kept
    while r.g.screen == ROUND and r.g.round < 3:
        r.call(r.right_row())
    assert all(r.pk.kind[s] != STREAM_K for s in [r.g.song])  # the stream given up for the contest


@pytest.mark.parametrize('app_state', ['down', 'none'])
def test_with_the_server_away_the_card_plays_on_its_own(fw, tmp_path, app_state):
    r = Rig(fw, tmp_path, songs=music(14), server=server_songs(200, local=4))
    if app_state == 'down':
        App(r, up=False)
    r.start()
    assert r.g.screen == ROUND and not r.clip_threads
    assert set(kinds(r)) <= {CARD_K, COPY_K} and kinds(r).count(COPY_K) <= 4


def test_with_the_server_away_too_few_on_the_card_says_why(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(5), server=server_songs(200, local=3))
    r.start()
    assert r.g.screen == NOSIGNAL and r.pk.why == 5  # WHY_SERVER
    canvas = Canvas(fw)
    r.paint(canvas)
    assert [o.text for o in canvas.ops if o.kind == 'text'][:3] == ["Can't reach your", 'music server', 'A contest needs 12 songs']


def test_offline_copies_of_server_songs_play_as_card_songs(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(2), server=server_songs(30, local=30))
    r.start()
    assert r.g.screen == ROUND and r.g.rounds == 10 and set(kinds(r)) <= {CARD_K, COPY_K} and kinds(r).count(COPY_K) >= 8
    assert r.clips()[-1][0].startswith(('/mnt/mmc/.subsonic/stream/', '/mnt/mmc/Music/'))


@pytest.mark.parametrize('status', [2, 3, 1])
def test_a_streamed_contest_gives_the_music_back_and_ends_its_clips(fw, tmp_path, status):
    r = Rig(fw, tmp_path, songs=music(3), server=server_songs(60), status=status)
    app = App(r)
    r.start()
    clip_pass(r), app.answer(), clip_pass(r)
    r.step()
    r.hear()
    r.quit()
    r.run_timers()
    assert r.starts == ([0] if status == 2 else [1] if status == 3 else []) and r.status == status
    clip_pass(r)
    seq, want = app.request()
    assert want == []  # the app's clips asked for no more: it deletes them
    assert not any('{set-songurl' in m and '/stream/' in m for m in r.sent)  # never the user's stream cache


def test_an_answer_downloading_is_pending_and_a_fail_with_no_path_is_given_up(fw, tmp_path):
    r = Rig(fw, tmp_path, songs=music(12), server=server_songs(60))
    app = App(r)
    r.start()
    s = r.g.song if r.pk.kind[r.g.song] == STREAM_K else None
    clip_pass(r)
    slots = [int(w[0]) for w in app.request()[1]]
    app.answer('get')
    clip_pass(r)
    assert slots and all(r.pk.got[k] == 0 for k in slots)  # downloading: still waited for
    assert [int(w[0]) for w in app.request()[1]] == slots  # and still asked for
    app.answers.clear()
    app.answer('fail')
    clip_pass(r)
    assert all(r.pk.got[k] == -1 for k in slots)
    if s is not None:
        r.step()
        assert r.pk.kind[r.g.song] != STREAM_K or r.g.song != s  # that song passed over at once
