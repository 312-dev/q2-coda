"""patch/playlist_songs.c and the hold menu's Sort (hold_menu.c): a playlist's songs in the order chosen for that
playlist, Recently Added (the songs added last first), Oldest First (stock's Time added, the stored order) or Title,
kept as [Q2POD] PLAYLISTSONGS, one digit a header id (docs/internals.md#playlist-song-order). getMusicByPlayList's
hook (ringnav_playlist_songs) makes the choice, so the playlist's page shows and plays the songs in it; a playlist
with no choice keeps stock's Sort (SYSSET PLAYLISTSORT, g_playlistsort_flag), and Edit Playlist keeps the stored
order."""
import ctypes as C

import pytest

from conftest import O
from test_nav_hold import TITLES, Hold, Ple, field, recs, scratch, st, ui, use_set  # noqa: F401  (fixtures)
from test_playlist_pages import OTG, open_page, road_trip, pages  # noqa: F401  (pages: the fixture)

REC_TITLE, REC_DISC = O['REC_TITLE'], O['REC_DISC']
NEWEST, OLDEST, TITLE = 1, 2, 3  # PSO_*
QM_PLAYLIST = 8


class Config:
    """config.ini's [Q2POD] as toolsReadConfig and toolsWriteConfig see it, and each write."""
    def __init__(self, fw, **values):
        self.values = {k: v.encode() for k, v in values.items()}
        self.writes: list = []
        fw.mock('toolsReadConfig', self.read)
        fw.mock('toolsWriteConfig', self.write)

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key).decode(), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)
        return 0

    def write(self, path, section, key, value, *_):
        k, v = C.string_at(key).decode(), C.string_at(value)
        self.values[k] = v
        self.writes.append((C.string_at(section).decode(), k, v.decode()))
        return 0


def hooked(fw):
    """getMusicByPlayList as the build has it: its prologue jumps to the hook (tools/build.py HOOKS)."""
    songs = fw.fn('ringnav_playlist_songs', C.c_int, C.c_int)
    fw.mock('getMusicByPlayList', lambda cd, *_: songs(C.c_int(cd).value))


def by_name(pages):
    """sort_playlist_first_alphabet: the playlist's songs A to Z by title, as stock's name sort gives them."""
    def alphabet(cd, *_):
        table = pages.table
        cd = C.c_int(cd).value
        rows = [table.recs(path=p, title=table.titles[p], id_=i) for i, (c, p) in sorted(table.rows.items()) if c == cd]
        return table.fill(sorted(rows, key=lambda r: field(r, REC_TITLE).lower()))
    pages.fw.mock('sort_playlist_first_alphabet', alphabet)


def flag(fw, value):
    fw.var('fw_ctx_g_playlistsort_flag', C.c_int).value = value


def shown(ui, pv):
    return ui.rows(pv.view)[2:]  # under Shuffle and Add Songs


@pytest.fixture
def songs(fw, ui, recs, pages):  # noqa: F811
    """Road Trip (3: One, Two, Three, added in that order) under the hook, stock's Sort at Time added."""
    hooked(fw)
    by_name(pages)
    flag(fw, 1)
    return pages


@pytest.mark.parametrize('stored, order', [('0001', ['Three', 'Two', 'One']), ('0002', ['One', 'Two', 'Three']),
                                           ('0003', ['One', 'Three', 'Two'])])
def test_a_playlist_s_page_shows_its_songs_in_its_own_order(fw, ui, songs, stored, order):
    Config(fw, PLAYLISTSONGS=stored)
    pv = open_page(fw, 3, b'Road Trip')
    assert shown(ui, pv) == order


@pytest.mark.parametrize('sort, order', [(1, ['One', 'Two', 'Three']), (0, ['One', 'Three', 'Two'])])
def test_with_no_choice_a_playlist_keeps_stock_s_sort(fw, ui, songs, sort, order):
    Config(fw, PLAYLISTSONGS='0100')  # another playlist's (1) choice only
    flag(fw, sort)
    assert shown(ui, open_page(fw, 3, b'Road Trip')) == order


def test_on_the_go_keeps_its_added_order(fw, ui, songs):
    songs.table.put(OTG, '/mnt/mmc/a.flac')  # Five, then One
    Config(fw, PLAYLISTSONGS='1' * 100)
    assert shown(ui, open_page(fw, OTG, b'On-The-Go'))[2:] == ['Five', 'One']  # under Save as Playlist and Clear


def test_a_song_plays_the_list_as_shown_newest_first(fw, ui, songs):
    Config(fw, PLAYLISTSONGS='0001')
    played = []
    ui.routes['playing_page'] = lambda ctx: played.append(tuple((C.c_int * 2).from_address(ctx + C.sizeof(C.c_void_p))))
    pv = open_page(fw, 3, b'Road Trip')
    ui.click(ui.find(pv.view, 'Two'))
    ui.due('pv_run')
    queue = [field(r, REC_TITLE) for r in songs.deques[pv.songs]]
    assert queue == ['Three', 'Two', 'One'] and played == [(1, 0xff13)]  # from Two, the list it shows


def test_the_hook_reverses_only_the_rows_it_staged(fw, recs, songs):
    """Recently Added is the stored order backwards: rows staged before the call stay first, as they were."""
    Config(fw, PLAYLISTSONGS='0001')
    staging = songs.deques.new([recs(title='Earlier')])
    fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value = staging
    fw.mock('sort_playlist_addtime', lambda cd, *_: songs.deques[staging].extend(  # appends, as stock's row callback
        [recs(path=p, title=TITLES[p]) for i, (c, p) in sorted(songs.table.rows.items()) if c == C.c_int(cd).value]) or 3)
    assert fw.fn('ringnav_playlist_songs', C.c_int, C.c_int)(3) == 3
    assert [field(r, REC_TITLE) for r in songs.deques[staging]] == ['Earlier', 'Three', 'Two', 'One']


def test_a_choice_is_saved_and_read_after_a_restart(fw, ui, songs, scratch):
    config = Config(fw)
    fw.fn('playlist_songs_set', None, C.c_int, C.c_int)(3, NEWEST)
    assert config.writes == [('Q2POD', 'PLAYLISTSONGS', '0001')]
    fw.fn('playlist_songs_set', None, C.c_int, C.c_int)(3, NEWEST)  # the same again: nothing written
    assert len(config.writes) == 1
    C.c_ubyte.from_address(C.addressof(fw.var('playlist_songs__ps', C.c_ubyte))).value = 0  # a restart: read again
    config.values['PLAYLISTSONGS'] = b'0001'
    assert shown(ui, open_page(fw, 3, b'Road Trip')) == ['Three', 'Two', 'One']
    fw.fn('playlist_songs_set', None, C.c_int, C.c_int)(3, 0)  # forgotten: the string ends after its last choice
    assert config.writes[-1] == ('Q2POD', 'PLAYLISTSONGS', '')


def test_deleting_a_playlist_forgets_its_choice_and_a_new_one_starts_without(fw, songs):
    config = Config(fw, PLAYLISTSONGS='1003')
    assert fw.fn('playlist_delete', C.c_int, C.c_int)(3) == 1
    assert config.writes[-1] == ('Q2POD', 'PLAYLISTSONGS', '1')
    config.values['PLAYLISTSONGS'] = b'0001'
    C.c_ubyte.from_address(C.addressof(fw.var('playlist_songs__ps', C.c_ubyte))).value = 0
    assert fw.fn('ringnav_playlist_create', C.c_int, C.c_void_p, C.c_char_p)(1, b'Fresh') == 0
    assert fw.fn('playlist_songs_chosen', C.c_int, C.c_int)(0) == 0  # id 0 had none: nothing to forget
    songs.table.headers.pop(1)  # Gym gone too: Fresh takes id 1
    config.values['PLAYLISTSONGS'] = b'03'
    C.c_ubyte.from_address(C.addressof(fw.var('playlist_songs__ps', C.c_ubyte))).value = 0
    assert fw.fn('ringnav_playlist_create', C.c_int, C.c_void_p, C.c_char_p)(1, b'Fresher') == 1
    assert fw.fn('playlist_songs_chosen', C.c_int, C.c_int)(1) == 0 and config.writes[-1][2] == ''


def test_edit_playlist_keeps_the_stored_order(fw, ui, songs):
    """Its moves swap neighbours' ids, so it lists the stored order (ple_query), whatever the page shows."""
    Config(fw, PLAYLISTSONGS='0001')
    ple = fw.var('playlists__ple', Ple)
    ple.dest = 3
    fw.fn('playlists__ple_reload', None)()
    assert [field(r, REC_TITLE) for r in songs.deques[ple.songs]] == ['One', 'Two', 'Three']


# The hold menu on a playlist's row in Library > Playlists: Sort, its value the playlist's order, its choices the
# three, the current one ticked; a pick saves it for that playlist.
@pytest.fixture
def menu(fw, ui, st, recs, mcl):  # noqa: F811
    use_set(st)
    hold = Hold(fw, ui, st, recs, mcl)
    road = recs(name='Road Trip', title='Road Trip', disc=3, id_=3, kind=4)
    fw.var('fw_ctx_p_deque_playlist', C.c_uint).value = hold.deques.new([road])
    flag(fw, 1)

    def open_():
        st('qm_kind').value, st('qm_idx', C.c_uint).value, st('qm_rows', C.c_uint).value = QM_PLAYLIST, 0, 1
        st('qm_hash', C.c_uint).value = fw.fn('navigation__rec_hash', C.c_uint, C.c_void_p)(road)
        fw.fn('navigation__qm_open', C.c_int, C.c_void_p)(None)
        return ui.rows(hold.view())
    hold.open = open_
    return hold


def test_a_playlist_s_hold_menu_offers_sort_with_its_order(fw, ui, menu):
    Config(fw)
    assert menu.open() == ['Play', 'Shuffle', 'Sort', 'Rename', None, 'Delete']  # Delete after its gap
    row = ui.find(menu.view(), 'Sort')
    assert ui.nodes[row].props['_mm_value'] == 'Oldest First'  # none chosen: stock's Time added


@pytest.mark.parametrize('pick, stored', [('Recently Added', '0001'), ('Title', '0003')])
def test_sort_s_choices_tick_the_current_one_and_a_pick_saves_it(fw, ui, menu, pick, stored):
    config = Config(fw)
    menu.open()
    menu.pick('Sort')
    assert menu.title() == 'Sort' and ui.rows(menu.view()) == ['Recently Added', 'Oldest First', 'Title']
    ticks = {c: ui.nodes[ui.find(menu.view(), c)].props.get('_mm_icon') for c in ('Recently Added', 'Oldest First', 'Title')}
    assert ticks == {'Recently Added': '', 'Oldest First': 'check', 'Title': ''}  # none chosen: stock's Time added
    menu.pick(pick)
    assert config.writes == [('Q2POD', 'PLAYLISTSONGS', stored)]
    menu.open()  # held again: its value the pick
    assert ui.nodes[ui.find(menu.view(), 'Sort')].props['_mm_value'] == pick
