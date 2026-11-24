"""Sync Offline (patch/offline.c, catalogue.c catalogue_offline_key, addon.c addon_offline_ask; docs/internals.md#sync-offline):
a Library item's server identity, its state in the app's offline.txt, the hold menu's and ⋯'s row, and the request
and its toast."""
import ctypes as C

from conftest import O
from test_catalogue import APP, Rig, song_row, url
from test_status_row import Later

from test_nav_painters import low, scratch, st  # noqa: F401
from test_nav_hold import recs, ui  # noqa: F401  the hold menu's fixtures

import pytest

OFF_SONG, OFF_ALBUM, OFF_ARTIST, OFF_PLAYLIST = 1, 2, 3, 4
LIST = f'/mnt/mmc/.{APP}/offline.txt'
REQ = f'/tmp/q2-{APP}.offline'


@pytest.fixture
def rig(fw, tmp_path):
    r = Rig(fw, tmp_path)
    r.files.files[f'/mnt/mmc/.{APP}/q2app'] += b'offline=1\n'
    r.files.files[f'/mnt/mmc/.{APP}/q2-{APP}'] = b''  # its binary: an app on the card
    r.write(1, [song_row('s1', 'Wild Horses', 'The Band', 'Sticky', 1, 180, 5000, album_id='al7'),
                song_row('s2', 'Brown Sugar', 'The Band', 'Sticky', 2, 200, 5000, album_id='al7')])
    r.poll()
    return r


def key(fw, kind, r, at=-1):
    app, k = C.create_string_buffer(16), C.create_string_buffer(256)
    ok = fw.fn('catalogue_offline_key', C.c_int, C.c_int, C.c_void_p, C.c_int, C.c_char_p, C.c_char_p, C.c_uint)(
        kind, r, at, app, k, 256)
    return (app.value.decode(), k.value.decode()) if ok else None


def test_a_library_items_server_identity(rig, fw, recs):
    """A server song by its sid; an album by the album id in its rows' paths; an artist by name; a card song
    nothing."""
    assert key(fw, OFF_SONG, rig.record(url('s1', 'al7'))) == (APP, 'song s1')
    assert key(fw, OFF_ALBUM, recs(kind=1, album='sticky', artist='The Band')) == (APP, 'album al7')  # any case, as the Library groups
    assert key(fw, OFF_ARTIST, recs(kind=1, artist='The Band')) == (APP, 'artistname The Band')
    assert key(fw, OFF_SONG, rig.record('/mnt/mmc/Music/a.flac')) is None
    assert key(fw, OFF_ALBUM, recs(kind=1, album='Nothing Here')) is None
    rig.sign_out()
    assert key(fw, OFF_SONG, rig.record(url('s1', 'al7'))) is None  # signed out: not the server's any more


@pytest.mark.parametrize('lines, k, state', [
    (b'album\tal7\tSticky\n', 'album al7', 1), (b'album\tal70\tOther\n', 'album al7', 0),
    (b'song\ts1\tWild Horses\r\n', 'song s1', 1), (b'artist\tar1\tTHE BAND\n', 'artistname The Band', 1),
    (b'artist\tar1\tThe Band\n', 'song ar1', 0), (b'library\tall\tEntire Library\n', 'song s1', 2), (b'', 'song s1', 0),
    (b'broken line\n', 'song s1', 0)])
def test_the_apps_list_says_the_items_state(rig, fw, lines, k, state):
    rig.files.files[LIST] = lines
    assert fw.fn('offline_state', C.c_int, C.c_char_p, C.c_char_p)(APP.encode(), k.encode()) == state


def row(fw, kind, r, at=-1):
    s = fw.fn('offline_row', C.c_char_p, C.c_int, C.c_void_p, C.c_int)(kind, r, at)
    return s.decode() if s else None


def test_the_row_names_what_a_press_does(rig, fw):
    """Sync Offline for an item not chosen, Stop Syncing Offline for one that is, none under Entire Library or for an
    app without offline=1 (an older Subsonic)."""
    r = rig.record(url('s1', 'al7'))
    assert row(fw, OFF_SONG, r) == 'Sync Offline'
    assert C.string_at(fw.fn('offline_icon', C.c_char_p)()) == b'cloud-arrow-down'
    rig.files.files[LIST] = b'song\ts1\tWild Horses\n'
    assert row(fw, OFF_SONG, r) == 'Stop Syncing Offline'
    assert C.string_at(fw.fn('offline_icon', C.c_char_p)()) == b'cloud-slash'
    rig.files.files[LIST] = b'library\tall\tEntire Library\n'
    assert row(fw, OFF_SONG, r) is None
    assert row(fw, OFF_SONG, rig.record('/mnt/mmc/Music/a.flac')) is None


def test_an_app_without_the_word_has_no_row(fw, tmp_path):
    r = Rig(fw, tmp_path)  # its q2app has no offline=1
    r.files.files[f'/mnt/mmc/.{APP}/q2-{APP}'] = b''
    r.write(1, [song_row('s1', 'Wild Horses', 'The Band', 'Sticky', 1, 180, 5000)])
    r.poll()
    assert row(fw, OFF_SONG, r.record(url('s1'))) is None


def test_a_press_asks_the_app_once_it_listens_and_toasts_its_answer(rig, fw):
    """The request waits for the app's socket (one there at its start it takes as answered), goes once, and the
    app's offlineack= is the toast; a second request replaces the first."""
    rig.now += 11_000  # the rig's autostart (catalogue.c cat_autostart), never listening, over: the press starts it
    fw.fn('addon_poll', None)()
    started = []
    fw.mock('fw_system', lambda cmd, *_: started.append(C.string_at(cmd)) or 0)
    rig.files.files[f'/mnt/mmc/.{APP}/q2-{APP}'] = b''
    assert row(fw, OFF_SONG, rig.record(url('s1', 'al7'))) == 'Sync Offline'
    fw.fn('offline_press', None)()
    assert started and REQ not in rig.files.files  # started; nothing written before it listens
    rig.files.files[f'/tmp/q2-{APP}.browse.sock'] = b''
    poll = fw.fn('addon_poll', None)
    rig.now += 600
    poll()
    seq, rest = rig.files.files[REQ].decode().split(' ', 1)
    assert rest == 'add song s1\n'
    later = fw.var('feedback__later', Later)
    rig.files.files[f'/tmp/q2-{APP}.state'] = f'offlineack={seq} Syncing offline · 2 songs\n'.encode()
    rig.now += 600
    poll()
    assert later.text.decode() == 'Syncing offline · 2 songs'
    later.text = b''
    rig.now += 600
    poll()
    assert later.text == b''  # toasted once


def test_no_answer_says_so(rig, fw, recs):
    fw.mock('fw_system', lambda *_: 0)
    rig.files.files[f'/mnt/mmc/.{APP}/q2-{APP}'] = b''
    rig.files.files[f'/tmp/q2-{APP}.browse.sock'] = b''
    rig.files.files[f'/tmp/q2-{APP}'] = b''  # running
    row(fw, OFF_ALBUM, recs(kind=1, album='Sticky'))
    fw.fn('offline_press', None)()
    assert rig.files.files[REQ].decode().split(' ', 1)[1] == 'add album al7\n'
    later = fw.var('feedback__later', Later)
    for _ in range(50):
        rig.now += 600
        fw.fn('addon_poll', None)()
    assert later.text.decode() == "Subsonic didn't answer"


def test_the_hold_menu_offers_it_after_the_favorite_and_a_pick_asks(rig, fw, ui, st, recs, mcl):
    """A server song's hold menu (docs/oled-design.md#menus): Sync Offline after Favorite, before the Go to rows;
    picked, the app is asked."""
    from test_nav_hold import Hold, use_set
    use_set(st)
    hold = Hold(fw, ui, st, recs, mcl).songs_page(
        'allmusic_page', [recs(path=url('s1', 'al7'), title='Wild Horses', album='Sticky', artist='The Band')])
    files = hold.card.files  # the hold's own card, which the rig's files join
    files.update(rig.files.files)
    files[f'/tmp/q2-{APP}.browse.sock'] = files[f'/tmp/q2-{APP}'] = b''  # running and listening
    hold.hold()
    rows = hold.menu()
    assert rows[rows.index('Favorite') + 1:] == ['Sync Offline', 'Go to Album', 'Go to Artist'], rows
    hold.pick('Sync Offline')
    assert files[REQ].decode().split(' ', 1)[1] == 'add song s1\n'
