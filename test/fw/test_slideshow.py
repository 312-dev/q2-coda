"""slideshow.c: Split's slideshow picks, a random library song's album art off the UI thread (docs/ipod.md#home)."""
import ctypes as C

from conftest import FNV_PRIME, FNV_SEED, O, Files, fnv1a, stock_start_with

SLIDE = '/tmp/q2-slide.jpg'
ROW = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_void_p)
IDLE, RUNNING, ANSWERED, READY = range(4)


class Pick(C.Structure):
    _fields_ = [('thread', C.c_ulong), ('state', C.c_int), ('kind', C.c_int), ('key', C.c_uint),
                ('recent', C.c_uint * O['SLIDE_RECENT']), ('next', C.c_uint), ('failed', C.c_uint)]


class Library:
    """era_sql's connection: each song query answers the next of songs, (path, artist, album) or None for no row; the
    count query the card's and the server's highest rowid (server None: no catalogue attached, so a query naming cat.
    fails); the cover readers write a picture for the paths in embedded or folder; the files on the card and in /tmp as
    a dict."""
    def __init__(self, fw, songs, embedded=(), folder=(), card=100, server=None, seed=12345):
        self.fw, self.songs, self.sql, self.counts = fw, list(songs), [], []
        self.rows, self.seed = (card, server), seed
        self.card = Files(fw)
        fw.mock('fw_access', lambda path, *_: 0 if C.string_at(path).decode() in self.card.files else -1)
        self.now = [5000]
        fw.mock('time_now_ms', lambda *_: self.now[0])
        fw.mock('sqlite3_open', lambda path, db, *_: setattr(C.c_void_p.from_address(db), 'value', 0x5d) or 0)
        fw.mock('sqlite3_exec', self.exec)
        fw.mock('toolsGetAlbumCover', lambda track, dst, w, h, *_: self.cover(track, dst, (w, h), embedded))
        fw.mock('toolsGetExternCover', lambda track, dst, w, h, *_: self.cover(track, dst, (w, h), folder))
        self.sizes = []

    def exec(self, db, sql, cb, ctx, *_):
        q = C.string_at(sql).decode()
        if not q.startswith('select'):
            return 0  # era_sql's PRAGMAs
        if 'cat.' in q and self.rows[1] is None:
            return 1  # no such database
        if not q.startswith('select fileurl'):
            self.counts.append(q)
            v = [b'%d' % self.rows[0], b'%d' % self.seed] + ([b'%d' % self.rows[1]] if 'cat.' in q else [])
            row = (C.c_char_p * len(v))(*v)  # held until the callback returns
            ROW(cb)(ctx, len(v), C.addressof(row), 0)
            return 0
        self.sql.append(q)
        song = self.songs.pop(0) if self.songs else None
        if song and cb:
            v = (C.c_char_p * 3)(*[None if x is None else x.encode() for x in song])
            ROW(cb)(ctx, 3, C.addressof(v), 0)
        return 0

    def cover(self, track, dst, size, have):
        self.sizes.append(size)
        if C.string_at(track).decode() not in have:
            return 0
        self.card.files[C.string_at(dst).decode()] = b'jpeg'
        return 1

    def pick(self):
        """The worker run here, start to answer."""
        self.fw.fn('slideshow__pick_worker', C.c_void_p, C.c_void_p)(None)
        return self.state()

    def state(self):
        return self.fw.var('slideshow__sp', Pick)

    def take(self, take=1):
        url = C.create_string_buffer(600)
        got = self.fw.fn('slide_pick', C.c_int, C.c_void_p, C.c_int)(url, take)
        return got, url.value.decode()


def fnv(text, h=FNV_SEED):
    """coverflow.c fnv: FNV-1a and a closing multiply, a separator between fields."""
    return fnv1a(text.encode(), h) * FNV_PRIME & 0xffffffff


def art_key(artist, album):
    return fnv(album, fnv(artist))


def test_a_pick_is_a_random_songs_embedded_art_at_the_slides_size(fw):
    lib = Library(fw, [('/mnt/mmc/A/1.flac', 'Ann', 'Alpha')], embedded={'/mnt/mmc/A/1.flac'})
    sp = lib.pick()
    assert (sp.state, sp.kind) == (ANSWERED, 1) and SLIDE in lib.card.files and SLIDE + '.tmp' not in lib.card.files
    assert lib.sizes == [(O['SLIDE_ART_PX'], O['SLIDE_ART_PX'])]
    sql = lib.sql[0]
    assert 'main.songtable' in sql and 'random()' in sql and "ifnull(album,'')<>''" in sql and 'limit 1' in sql
    assert 'audiobooks' in sql  # MUSIC_WHERE: the library's music alone
    assert list(sp.recent)[0] == fnv('Alpha')


def test_no_embedded_art_takes_the_folder_image_then_coverflows_thumbnail(fw):
    lib = Library(fw, [('/m/B/1.mp3', 'Bo', 'Beta')], folder={'/m/B/1.mp3'})
    assert lib.pick().kind == 1 and lib.sizes == [(O['SLIDE_ART_PX'],) * 2] * 2  # embedded asked first
    lib = Library(fw, [('/m/C/1.mp3', 'Cy', 'Gamma')])
    thumb = '/mnt/mmc/.coverflow/%08x.jpg' % art_key('Cy', 'Gamma')
    lib.card.files[thumb] = b'jpeg'
    sp = lib.pick()
    assert (sp.kind, sp.key) == (2, art_key('Cy', 'Gamma')) and SLIDE not in lib.card.files


def test_songs_with_no_art_and_empty_rows_are_passed_over_up_to_slide_tries(fw):
    tries = O['SLIDE_TRIES']
    lib = Library(fw, [None, ('/m/D/1.mp3', 'D', 'Delta')] + [None] * tries)
    thumb = '/mnt/mmc/.coverflow/%08x.jpg' % art_key('D', 'Delta')
    lib.card.files[thumb] = b''  # Coverflow found no art: an empty marker
    sp = lib.pick()
    assert (sp.state, sp.kind) == (ANSWERED, 0) and len(lib.sql) == tries
    assert not any(sp.recent)


def test_albums_shown_lately_are_passed_over_but_for_the_last_tries(fw):
    tries = O['SLIDE_TRIES']
    a, b = ('/m/A/1.flac', 'A', 'Alpha'), ('/m/B/1.flac', 'B', 'Beta')
    lib = Library(fw, [a, a, b], embedded={a[0], b[0]})
    lib.state().recent[0] = fnv('Alpha')
    lib.state().next = 1
    sp = lib.pick()
    assert sp.kind == 1 and len(lib.sql) == 3 and list(sp.recent)[:2] == [fnv('Alpha'), fnv('Beta')]
    lib = Library(fw, [a] * tries, embedded={a[0]})  # a library of one album still turns
    sp = lib.pick()
    assert sp.kind == 1 and len(lib.sql) == tries - 1


def test_slide_pick_starts_a_worker_and_hands_its_answer_over_once(fw):
    lib = Library(fw, [])
    started = []
    fw.mock('fw_pthread_create', lambda t, a, fn, arg, *_: started.append(fn) or 0)
    fw.mock('fw_pthread_join', lambda *_: 0)
    assert lib.take(0) == (0, '') and len(started) == 1 and lib.state().state == RUNNING
    assert lib.take() == (0, '') and len(started) == 1  # one at a time
    sp = lib.state()
    sp.kind, sp.state = 1, ANSWERED
    assert lib.take(0) == (0, '') and sp.state == READY and len(fw.calls('fw_pthread_join')) == 1  # fetched ahead, kept
    assert lib.take() == (1, 'file://' + SLIDE) and sp.state == IDLE
    assert lib.take(0)[0] == 0 and len(started) == 2  # the next one
    sp.kind, sp.key, sp.state = 2, 0x1234abcd, ANSWERED
    assert lib.take() == (1, 'file:///mnt/mmc/.coverflow/1234abcd.jpg')


def test_a_pick_that_found_nothing_waits_before_the_next(fw):
    lib = Library(fw, [])
    started = []
    fw.mock('fw_pthread_create', lambda *_: started.append(1) or 0)
    sp = lib.state()
    sp.kind, sp.state = 0, ANSWERED
    assert lib.take() == (0, '') and sp.state == IDLE and not started
    lib.now[0] += O['SLIDE_RETRY_MS']  # failed holds the time | 1, a ms on
    assert lib.take() == (0, '') and not started
    lib.now[0] += 1
    lib.take()
    assert started == [1]


def test_a_refused_thread_waits_too(fw):
    lib = Library(fw, [])
    fw.mock('fw_pthread_create', lambda *_: 11)
    lib.take()
    lib.take()
    assert len(fw.calls('fw_pthread_create')) == 1 and lib.state().failed & 1


def test_a_server_only_library_shows_its_apps_album_covers(fw):
    """The owner's library is all a server's (servertable), the card's songtable empty: the picks are the server's
    rows, each shown with the cover its app keeps for the album (catalogue_cover), made the slide's size."""
    fw.mock('tk_str_start_with', stock_start_with)
    row = '/mnt/mmc/.subsonic/cat/al1/s1.flac'
    lib = Library(fw, [(row, 'Ann', 'Alpha')], card=0, server=9199)
    lib.card.files['/mnt/mmc/.subsonic/covers/al1.jpg'] = b'jpeg'
    made = []
    fw.mock('toolsThumbSpecCover', lambda src, dst, w, h, *_: made.append((C.string_at(src).decode(), (w, h)))
            or lib.card.files.__setitem__(C.string_at(dst).decode(), b'jpeg') or 1)
    sp = lib.pick()
    assert (sp.state, sp.kind) == (ANSWERED, 1) and SLIDE in lib.card.files
    assert made == [('/mnt/mmc/.subsonic/covers/al1.jpg', (O['SLIDE_ART_PX'],) * 2)]
    assert len(lib.sql) == 1 and 'cat.servertable' in lib.sql[0] and 'main.songtable' not in lib.sql[0]
    assert lib.sizes == []  # no file to read embedded art from


import pytest


@pytest.mark.parametrize('large', [True, False])
def test_a_server_album_s_slide_is_made_from_its_512_px_cover_when_the_card_has_it(fw, large):
    """The owner's report (2026-11-23, "why are slideshow library album covers so low res"): the slide was made from
    the app's list-row cover, 160 px, scaled up to SLIDE_ART_PX (352) and blocky. The app keeps a larger one beside
    it, covers/512/<album>.jpg (500 px on the owner's card, 2,048 of 2,048 albums): the slide is made from that,
    and from the small one only when it is missing."""
    fw.mock('tk_str_start_with', stock_start_with)
    row = '/mnt/mmc/.subsonic/cat/al1/s1.flac'
    lib = Library(fw, [(row, 'Ann', 'Alpha')], card=0, server=9199)
    lib.card.files['/mnt/mmc/.subsonic/covers/al1.jpg'] = b'jpeg'
    if large:
        lib.card.files['/mnt/mmc/.subsonic/covers/512/al1.jpg'] = b'jpeg'
    made = []
    fw.mock('toolsThumbSpecCover', lambda src, dst, w, h, *_: made.append(C.string_at(src).decode())
            or lib.card.files.__setitem__(C.string_at(dst).decode(), b'jpeg') or 1)
    assert lib.pick().kind == 1
    assert made == ['/mnt/mmc/.subsonic/covers/512/al1.jpg' if large else '/mnt/mmc/.subsonic/covers/al1.jpg']


def test_a_library_on_both_draws_from_each_by_its_size(fw):
    """Songs on the card and on a server: each try takes the card's table or the server's, in proportion to their
    rows, so neither hides the other."""
    picks = {'main': 0, 'cat': 0}
    for seed in range(40):
        lib = Library(fw, [None] * O['SLIDE_TRIES'], card=100, server=300, seed=seed * 7919)
        lib.pick()
        for q in lib.sql:
            picks['cat' if 'cat.servertable' in q else 'main'] += 1
    total = picks['main'] + picks['cat']
    assert 0.6 < picks['cat'] / total < 0.9, picks


def test_without_a_catalogue_the_card_alone(fw):
    lib = Library(fw, [('/mnt/mmc/A/1.flac', 'Ann', 'Alpha')], embedded={'/mnt/mmc/A/1.flac'})
    assert lib.pick().kind == 1 and 'main.songtable' in lib.sql[0] and len(lib.counts) == 1  # cat's refused: the card's
