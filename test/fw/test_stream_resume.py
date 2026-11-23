"""A streamed queue picked up after a restart (docs/internals.md#memory-playback-and-a-streamed-queue): the
user's, 2026-11-18, Memory Playback brought back a queue of server songs with Up Next holding only the one or two
songs whose downloads were on the card, and nothing fetched the rest. playback.c keeps the session's songs not yet
queued beside the snapshot; ringnav_memory, restoring, asks the songs' app (q2app resume=1) to resume the stream,
paused (addon.c addon_resume_ask): every saved song in order, the current one's place and its seconds. Up Next lists
the songs not on the card meanwhile; the app's answer is a queue file whose first song is the one restored, kept as a
radio's seed is (no load, still paused), and an app that isn't there or declines leaves the songs on the card, as
before, with a line in the log. The rig is test_addon's Stream: /tmp and the card as a dict, folders listed as
given."""
import ctypes as C
import re

import pytest

import host
from conftest import O, claim, mcl  # noqa: F401
from test_addon import LIBRARY, REC_PATH, S_DONE, Sm, Stream, ap, record_field, songs  # noqa: F401

MOUNTS = b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n'
STREAM = b'/mnt/mmc/.subsonic/stream'
CACHE = STREAM + b'/acct/original/a'
SONGS = [CACHE + b'/song %d [s%d].flac' % (i, i) for i in range(10)]
QUEUE_FILE = b'/mnt/mmc/.q2pod/ringnav-queue'
TAIL_FILE = QUEUE_FILE + b'-stream'
REQUEST = b'/tmp/q2-subsonic.resume'
STATE = b'/tmp/q2-subsonic.state'
SOCK = b'/tmp/q2-subsonic.browse.sock'
PAUSED, PLAYING = 3, 2  # mclGetPlayStatus


@pytest.fixture
def free_rec():
    """toolsFreeStSongInfo at its stock address (addon.c's define), which addon_pending_free calls: a ret there."""
    at = int(re.findall(r'toolsFreeStSongInfo \(\(void \(\*\)\(void \*\)\)(0x[0-9a-f]+)u\)',
                        (host.ROOT/'patch/addon.c').read_text())[0], 16)
    claim(at)
    saved = C.string_at(at, 1)
    C.memmove(at, b'\xc3', 1)
    yield
    C.memmove(at, saved, 1)


def pending(fw):
    """Up Next's pending rows (addon_pending): each record's path."""
    n = C.c_uint()
    rows = fw.fn('addon_pending', C.c_void_p, C.c_uint, C.POINTER(C.c_uint))(50, C.byref(n))
    out = [record_field(C.c_void_p.from_address(rows + 8 * i).value, REC_PATH) for i in range(n.value)]
    fw.fn('addon_pending_free', None, C.c_void_p, C.c_uint)(rows, n.value)
    return out


class Resume(Stream):
    """A Subsonic stream of SONGS, the first two on the card, saved by Memory Playback (Location) and restored after
    a restart. manifest: the app's q2app; paths the session's songs, the first landed of them on the card."""
    def __init__(self, fw, mcl, songs, manifest=b'name=Subsonic\nresume=1\n', paths=SONGS, landed=2):
        super().__init__(fw, mcl, songs, subsonic=manifest)
        self.world.files[b'/proc/mounts'] = MOUNTS
        self.status, self.logged = PAUSED, []
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('mclGetPlayTime', lambda sec, total, *_: C.c_int.from_address(sec).__setattr__('value', 42))
        fw.mock('fw_deque_pop_back', lambda q, *_: self.deques[q].pop() and 0)
        fw.mock('fw_fclose', lambda *_: 0)  # the snapshot's reader checks it
        fw.mock('fw_write', lambda fd, data, n, *_: self.logged.append(C.string_at(data, n).decode()) or n)
        fw.var('g_memory_play', C.c_ubyte).value = 2  # Location: the place in the song too
        m3u = STREAM + b'/queue-1.m3u'
        self.world.files[m3u] = b''.join(p + b'\n' for p in paths)
        self.land(*paths[:landed])
        self.play(b'1 ' + m3u + b'\n', app=b'subsonic')
        self.step()
        self.step()
        mcl.type = LIBRARY
        assert self.queued() == paths[:landed] and pending(fw) == paths[landed:]
        fw.fn('playback_save', None)()

    def restart(self):
        """The Q2 restarted: /tmp empty, nothing running, no queue; then Home's Memory Playback (ringnav_memory)."""
        for k in [k for k in self.world.files if k.startswith(b'/tmp/')]:
            del self.world.files[k]
        for i in range(ap(self.fw).n):
            ap(self.fw).a[i].launched = 0
        self.fw.var('addon__sm', Sm).state = S_DONE
        card = dict(self.world.files)
        self.fw.fn('playback_clear', None)()  # which drops the snapshot: the card's files as they were before it
        self.world.files.clear()
        self.world.files.update(card)
        self.deques[self.queue][:] = []
        self.mcl.pos = 0
        self.loads.clear()  # Now Playing's opens before the restart
        directory = self.fw.var('fw_ctx_tools_pdeq_directory', C.c_uint)
        self.fw.mock('getAllMusic', lambda *_: self.deques[directory.value].clear() or 0)  # no song in the Library
        self.fw.mock('stock_memory_trampoline', lambda *_: -7)
        self.fw.mock('mclLoadPlayList', self.stock_load)
        return self.fw.fn('ringnav_memory', C.c_int, C.c_void_p)(self.deques.new())

    def stock_load(self, q, at, kind, *_):
        self.deques[self.queue][:] = self.deques[q]
        self.mcl.pos, self.mcl.type = at, kind

    def asked(self):
        """The request, once the app, started for it (which removes a socket a killed run left), listens: its first
        line's words, and its paths."""
        self.step()
        self.world.files[SOCK] = b''
        self.step()
        lines = self.world.files[REQUEST].split(b'\n')
        assert lines[-1] == b''
        return lines[0].split(b' '), lines[1:-1]


def test_the_session_s_songs_not_yet_queued_are_saved_beside_the_snapshot(fw, mcl, songs, free_rec):
    s = Resume(fw, mcl, songs)
    tail = s.world.files[TAIL_FILE]
    assert all(p in tail for p in SONGS[2:]) and SONGS[0] not in tail and SONGS[1] not in tail


def test_a_restore_of_ten_server_songs_with_two_on_the_card_keeps_ten_rows(fw, mcl, songs, free_rec):
    s = Resume(fw, mcl, songs)
    assert s.restart() == 0
    assert s.queued() == SONGS[:2]  # the two on the card, the first current
    assert pending(fw) == SONGS[2:]  # and the eight to come, in Up Next's pending rows: ten in all
    assert fw.calls('mclSetStartSeekTime')[-1][1][0] == 42


def test_a_restore_of_stream_cache_songs_lists_each_folder_once_and_not_the_library(fw, mcl, songs, free_rec):
    """A streamed queue's downloads sit in the stream cache's hex buckets (stream_path.h), in no Library table: the
    whole library is not loaded to look for them, and each bucket is listed once however the queue interleaves
    them, the queue's order kept."""
    paths = [STREAM + b'/acct/original/%x/song %d [s%d].flac' % (i % 4, i, i) for i in range(12)]
    s = Resume(fw, mcl, songs, paths=paths, landed=12)
    listed = len(fw.calls('toolsLoadDirectory'))
    assert s.restart() == 0
    assert s.queued() == paths
    assert len(fw.calls('getAllMusic')) == 0
    assert len(fw.calls('toolsLoadDirectory')) - listed == 4


def test_the_app_is_asked_to_resume_paused_once_its_socket_is_there(fw, mcl, songs, free_rec):
    s = Resume(fw, mcl, songs)
    s.restart()
    s.step()
    assert REQUEST not in s.world.files  # no socket yet: an app takes a request there at its start as answered
    assert any(b'/mnt/mmc/.subsonic/run' in c[0] for c in s.world.of('system'))  # started for it
    head, paths = s.asked()
    assert head[1:] == [b'0', b'42', b'paused'] and paths == SONGS  # every saved song, the current at 0, 42 s in
    assert not fw.calls('player_play_pause') and not fw.calls('player_start_queue')


def test_the_app_s_queue_is_kept_paused_with_every_song_in_up_next(fw, mcl, songs, free_rec):
    s = Resume(fw, mcl, songs)
    s.restart()
    head, _ = s.asked()
    seq = head[0]
    s.world.files[STATE] = b'version=1\nresumeack=' + seq + b' 0\n'
    m3u = STREAM + b'/queue-2.m3u'
    s.world.files[m3u] = b''.join(p + b'\n' for p in SONGS)  # from the current song on
    s.land(SONGS[2])
    s.play(seq + b' ' + m3u + b'\n', app=b'subsonic')
    for _ in range(3):
        s.step()
    assert s.loads == [] and mcl.pos == 0  # nothing loaded: the restored song stays, paused, at its place
    assert s.queued() == SONGS[:3] and pending(fw) == SONGS[3:]
    assert not fw.calls('player_start_queue') and not fw.calls('player_play_pause')
    assert s.playing(b'subsonic') is None  # not told it plays while it is paused
    s.status = PLAYING  # the user's Play
    s.step()
    assert s.playing(b'subsonic') == SONGS[0] + b'\n'
    s.land(SONGS[3])
    s.step()
    assert s.queued() == SONGS[:4] and s.loads == []


@pytest.mark.parametrize('answer', [b'1', b'2'])
def test_an_app_that_declines_leaves_the_songs_on_the_card(fw, mcl, songs, free_rec, answer):
    s = Resume(fw, mcl, songs)
    s.restart()
    head, _ = s.asked()
    s.world.files[STATE] = b'version=1\nresumeack=' + head[0] + b' ' + answer + b'\n'
    s.step()
    assert s.queued() == SONGS[:2] and pending(fw) == []  # today's restore: the songs on the card
    assert any(' W resume ' in line for line in s.logged)


def test_an_app_that_never_answers_leaves_the_songs_on_the_card(fw, mcl, songs, free_rec):
    s = Resume(fw, mcl, songs)
    s.restart()
    s.step()  # started, never listening
    s.world.ms += 60000
    s.step()
    assert REQUEST not in s.world.files and pending(fw) == [] and s.queued() == SONGS[:2]
    assert any(' W resume ' in line for line in s.logged)


def test_an_app_without_resume_is_not_asked(fw, mcl, songs, free_rec):
    s = Resume(fw, mcl, songs, manifest=b'name=Subsonic\n')
    s.restart()
    s.world.files[SOCK] = b''
    s.step()
    assert REQUEST not in s.world.files and pending(fw) == [] and s.queued() == SONGS[:2]


def test_a_late_answer_after_another_queue_plays_nothing(fw, mcl, songs, free_rec):
    """The user played something else before the app answered: its queue file, named by the request's seq, is not
    played over it."""
    s = Resume(fw, mcl, songs)
    s.restart()
    head, _ = s.asked()
    other = b'/mnt/mmc/Music/x.flac'
    s.deques[s.queue][:] = [songs(other)]
    s.loaded.value += 1
    s.step()
    m3u = STREAM + b'/queue-2.m3u'
    s.world.files[m3u] = b''.join(p + b'\n' for p in SONGS)
    s.play(head[0] + b' ' + m3u + b'\n', app=b'subsonic')
    for _ in range(3):
        s.step()
    assert s.queued() == [other] and s.loads == []
