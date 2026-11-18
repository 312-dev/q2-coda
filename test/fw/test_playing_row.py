"""The playing song's row in a song list (docs/oled-design.md#song-rows, song_rows.c song_playing, playing_bars,
playing_fit): its title in the accent and four bars on the right, beside its length (B) or in the length's place
where both don't fit or the row has none (A), white on the selection. A song is the playing one by its path and CUE
start, never by its title."""

import ctypes as C
import struct

import pytest

from conftest import O, Deques, high_halves
from test_nav_painters import Page, Scheme, layout, low, mapped, rgba, scratch, st, text_ink  # noqa: F401  (fixtures)

TITLE_HIGHS = high_halves(3)  # each row's title

PLAYING = 2  # mclGetPlayStatus: 1 stopped, 2 playing, 3 paused
BARS_W, GAP, TITLE_MIN = 16, 12, 80  # song_rows.c PLAY_BARS_W, SONG_COL_GAP, PLAY_TITLE_MIN


@pytest.fixture
def titles():
    """A page for each row's title at one of this file's high halves (conftest.high_halves), its low half 0: on the
    Q2 a record's path (+0xc) and title (+0x10) are 4-byte pointers, here P() reads 8, so the path's high half is
    the title's low half, 0, and the path is a low address (low)."""
    at = [h << 32 for h in TITLE_HIGHS]
    with mapped(*((a, 0x1000) for a in at)):
        yield at


class Songs:
    """An album page's (or Songs') rows bound to showlist records, and the queue playing one of them."""
    def __init__(self, fw, mcl, low, titles_at, name='albuminfo_page', titles=('Opening Track', 'The Playing One', 'Fourth'),
                 width=375, sel=None, **config):
        self.fw = fw
        self.page = Page(fw, rows=len(titles), pitch=32, sel=sel, name=name, **config)
        self.page.view.move(0, 26, width, 294)
        self.low, self.keep, self.titles_at = low, [], titles_at
        self.deques = Deques(fw)
        self.records = []
        for i, (row, title) in enumerate(zip(self.page.rows, titles)):
            row.children[0].move(10, 0, 260, 32)
            self.page.ui.set_text(row.children[0], title)
            C.memmove(titles_at[i], title.encode() + b'\0', len(title) + 1)
            self.records.append(self.record(b'/mnt/mmc/Album/%02d.flac' % (i + 1), titles_at[i], i + 1, 252))
        fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = self.deques.new(list(self.records))
        ui = self.page.ui

        def utf8(w, out, size, *_):  # a row's title, as song_record reads it to find the row's record
            words = ui.text(ui.nodes[w]).encode()[:size - 1] + b'\0'
            C.memmove(out, words, len(words))
            return 0
        fw.mock('widget_get_text_utf8', utf8)
        fw.mock('toolsTimeItoa', lambda out, sec, *_: C.memmove(out, b'%d:%02d\0' % (sec // 60, sec % 60), 6) and 0)
        fw.mock('mclGetPlayStatus', lambda *_: PLAYING)
        self.queue = fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint)
        mcl.pos, mcl.type = 0, 0xf001

    def record(self, path, title=0, track=0, seconds=0):
        r = C.create_string_buffer(O['REC_SIZE'] + 8)
        struct.pack_into('<I', r, O['REC_PATH'], self.low(path))
        struct.pack_into('<Q', r, O['REC_TITLE'], title)  # its low half 0: the path's high half
        for field, value in (('REC_TYPE', 8), ('REC_TRACK', track), ('REC_CUE_END', seconds)):
            struct.pack_into('<i', r, O[field], value)
        self.keep.append(r)
        return C.addressof(r)

    def play(self, path):
        """The queue holds one song, a record of its own (not the list's, and untitled), with this path: playing."""
        self.queue.value = self.deques.new([self.record(path, 0, 1, 252)])

    def paint(self):
        self.page.canvas.texts.clear()
        self.page.canvas.px.clear()
        self.page.paint()
        return self.page.canvas

    def column(self):
        """song_row's right column: the last px a length's ink reaches, left of the A-Z index."""
        view = self.page.view
        return view.box[2] - 1 - self.fw.fn('az_right', C.c_int, C.c_void_p)(view.addr) - 2

    def lengths(self):
        """Each row's length as painted: row -> (text, its ink's left, its ink's last px)."""
        return {box[1] // 32: (text, box[0], box[0] + box[2] - 1) for text, box, _ in self.page.canvas.texts
                if ':' in text}

    def ink(self, row, color):
        """Row's pixels in color left of the right column's end (not the A-Z index's): their xs."""
        return sorted(x for (x, y), c in self.page.canvas.px.items()
                      if y // 32 == row and c == color and x <= self.column())

    def title_end(self, row):
        r = self.page.rows[row]
        return r.box[0] + r.children[0].box[0] + r.children[0].box[2] - 2  # hscroll_label's own margin


@pytest.fixture
def accent(fw, layout):
    def get():
        return rgba(Scheme(fw, layout).tones[0])
    return get


def test_the_playing_songs_bars_stand_beside_its_length(fw, st, mcl, low, titles, accent):
    """B: an album's playing track keeps its length on the right column; the four bars stand SONG_COL_GAP before
    it and the title ends SONG_COL_GAP before them, in the accent. The other rows are as they were."""
    songs = Songs(fw, mcl, low, titles)
    songs.play(b'/mnt/mmc/Album/02.flac')
    songs.paint()
    lengths = songs.lengths()
    assert sorted(lengths) == [0, 1, 2] and {t for t, _, _ in lengths.values()} == {'4:12'}
    bars = songs.ink(1, accent())
    assert bars and max(bars) + 1 + GAP == lengths[1][1]  # the bars' right edge, then the gap, then the length
    assert max(bars) + 1 - min(bars) == BARS_W and len({x for x in bars}) == 12  # four bars 3 px wide
    assert songs.title_end(1) + GAP <= min(bars)
    assert not songs.ink(2, accent())  # (row 0 holds the selection, the accent's bar)
    assert text_ink(fw, st, songs.page.rows[1].children[0]) == accent()
    assert text_ink(fw, st, songs.page.rows[2].children[0]) == 0xffffffff


def test_the_playing_song_is_known_by_its_path_not_its_title(fw, st, mcl, low, titles, accent):
    """Another file with the same title is not the playing song; the same file, its queue record untitled, is."""
    songs = Songs(fw, mcl, low, titles, titles=('Intro', 'Intro', 'Outro'))
    songs.play(b'/mnt/mmc/Other/01.flac')
    songs.paint()
    assert not songs.ink(1, accent()) and not songs.ink(2, accent())
    assert text_ink(fw, st, songs.page.rows[1].children[0]) == 0xffffffff
    songs.play(b'/mnt/mmc/Album/02.flac')
    songs.paint()
    assert [bool(songs.ink(row, accent())) for row in (1, 2)] == [True, False]


@pytest.mark.parametrize('case', ['narrow', 'no length'])
def test_the_bars_take_the_lengths_place_where_both_dont_fit(fw, st, mcl, low, titles, accent, case):
    """A: a row too narrow for the title to keep TITLE_MIN px beside the bars and the length, or a list with no
    length (Songs' mixed rows under Song Length's Albums), has the bars on the right column in the length's place:
    the playing row shows no length, the others keep theirs."""
    narrow = case == 'narrow'
    songs = Songs(fw, mcl, low, titles, name='albuminfo_page' if narrow else 'allmusic_page', width=220 if narrow else 375)
    songs.play(b'/mnt/mmc/Album/02.flac')
    songs.paint()
    lengths = songs.lengths()
    assert 1 not in lengths and sorted(lengths) == ([0, 2] if narrow else [])
    bars = songs.ink(1, accent())
    assert bars and max(bars) == songs.column()  # where a length's ink ends
    assert all(end == songs.column() for _, _, end in lengths.values())  # the others' lengths end there too
    assert songs.title_end(1) + GAP <= min(bars)


def test_the_selected_playing_row_is_white(fw, st, mcl, low, titles, accent):
    """The wheel on the playing row (the first, where the page's selection settles): its bars white on the
    accent's bar, and its title the selection's text, not the accent."""
    songs = Songs(fw, mcl, low, titles)
    songs.play(b'/mnt/mmc/Album/01.flac')
    songs.paint()
    lengths = songs.lengths()
    white = [x for x in songs.ink(0, 0xffffffff) if lengths[0][1] - GAP - BARS_W <= x < lengths[0][1] - GAP]
    assert len(white) and max(white) + 1 + GAP == lengths[0][1] and min(white) + BARS_W + GAP == lengths[0][1]
    assert text_ink(fw, st, songs.page.rows[0].children[0]) != accent()


def test_nothing_is_marked_while_stopped(fw, st, mcl, low, titles, accent):
    songs = Songs(fw, mcl, low, titles)
    songs.play(b'/mnt/mmc/Album/02.flac')
    fw.mock('mclGetPlayStatus', lambda *_: 1)
    songs.paint()
    assert not songs.ink(1, accent()) and sorted(songs.lengths()) == [0, 1, 2]
