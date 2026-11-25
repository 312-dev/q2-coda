"""patch/netupdate.c: GitHub's redirects and release JSON, sources.list, unzip's listing, and installing,
removing and checking apps, with the card and the network stubbed; then System Update's online row and the App
Store's jobs end to end, a press to the toast, on a card whose busybox runs on its files."""
import ctypes as C
import fnmatch
import hashlib
import io
import pathlib
import random
import re
import shlex
import subprocess
import zipfile

import pytest

import host
from conftest import O, block

# stock.h's Q2POD_TAG: tools/build.py's VERSION (host.py builds as a release, not --dev)
TAG = host.tools_build.VERSION.encode()
# the next single-digit tag (1.6.9 -> 1.7.0), as netupdate's strcmp orders them
_n = int(TAG.replace(b'.', b'')) + 1
NEWER = b'%d.%d.%d' % (_n // 100, _n // 10 % 10, _n % 10)
OLDER = b'0.9.9'

# The App Store's window and another over it, each a zeroed widget (no parent, no name): OLED's title and toast
# checks read the window on top
_WINDOWS = C.create_string_buffer(0x400), C.create_string_buffer(0x400)
WIN, OTHER = (C.addressof(w) for w in _WINDOWS)


def rows(fw):
    """list_item_create's rows: a zeroed widget each, alive for the test."""
    made: list = []
    fw.mock('list_item_create', lambda *_: made.append(C.create_string_buffer(0x400)) or C.addressof(made[-1]))
    return made

SH_MAX = 16
SH_CAP = 1 << 30
SH_OK, SH_NET, SH_SPACE, SH_CARD, SH_MISSING, SH_BAD, SH_CANCELLED, SH_BUSY, SH_UNSIGNED, SH_FORGED = range(10)
NU_IDLE, NU_RUNNING, NU_DONE, NU_LATEST, NU_NET, NU_MISSING, NU_CARD, NU_CANCELLED, NU_UNSIGNED, NU_FORGED = range(10)
NET_NEWER, NET_LATEST, NET_FAILED = 1, 2, 3
JOB_CHECK, JOB_INSTALL, JOB_REMOVE, JOB_ERASE = 1, 2, 3, 4
ST_INSTALL, ST_UPDATE, ST_INSTALLED = range(3)
ROW_CHECK = SH_MAX
STAGING = '/mnt/mmc/.q2/staging'
JSON_PATH = b'/tmp/q2-shelf.json'
LIST = pathlib.Path(__file__).resolve().parents[2]/'sources.list'
PIN_SHA = b'ab' * 32


class App(C.Structure):
    _fields_ = [('name', C.c_char * 32), ('id', C.c_char * 16), ('repo', C.c_char * 80), ('dir', C.c_char * 17),
                ('bin', C.c_char * 40), ('latest', C.c_char * 32), ('dropped', C.c_char), ('keep', C.c_char_p),
                ('asset', C.c_char * 64), ('sha256', C.c_ubyte * 32), ('size', C.c_uint)]


def pinned(text, tag=b'1.0', asset=b'app.zip', size=5, sha=PIN_SHA):
    """Each app's line of text with a pin after its repo, as tools/shelf.py writes them; comments, blank lines and
    serial lines as they are."""
    out = []
    for line in text.split(b'\n'):
        m = re.match(rb'(\s*-?[^\s#]\S*)(.*)', line, re.S)
        if not m or m[1].lstrip().startswith((b'serial', b'signature')):
            out.append(line)
        else:
            out.append(m[1] + b' %s %s %d %s' % (tag, asset, size, sha) + m[2])
    return b'\n'.join(out)


class Sh(C.Structure):
    _fields_ = [('thread', C.c_ulong), *((n, C.c_int) for n in ('running', 'cancel', 'percent', 'stage', 'result', 'job', 'app', 'armed', 'rows', 'n')),
                ('map', C.c_int * (SH_MAX + 1)), ('manual', C.c_int), ('need', C.c_uint), ('checked', C.c_ulonglong), ('next', C.POINTER(App)),
                ('page', C.c_void_p), ('row', C.c_void_p * (SH_MAX + 1))]


class Nu(C.Structure):
    _fields_ = [('thread', C.c_ulong), ('state', C.c_int), ('cancel', C.c_int), ('percent', C.c_int),
                ('verifying', C.c_int)]


def hostile(seed, samples, count=2000, alphabet=b'"\\:/ \n{}[],\x00.'):
    """Random bytes, truncations of the samples, and mutations of them."""
    r = random.Random(seed)
    for _ in range(count):
        yield r.randbytes(r.randrange(200))
    for s in samples:
        for k in sorted(r.sample(range(len(s)), min(len(s), 400))):
            yield s[:k]
    for _ in range(count):
        b = bytearray(r.choice(samples))
        for _ in range(r.randrange(1, 6)):
            b[r.randrange(len(b))] = r.choice(alphabet)
        yield bytes(b)


@pytest.mark.parametrize('location,tag', [
    (b'https://github.com/o/r/releases/tag/1.3.4', b'1.3.4'),
    (b'/releases/tag/x', b'x'),
    (b'https://github.com/o/r/releases/tag/v1/rc', b'rc'),      # what follows the last '/'
    (b'https://github.com/o/r/releases/tag/', None),
    (b'https://github.com/o/r/releases', None), (b'', None), (b'tag', None), (b'/', None),
    (b'https://github.com/o/r/releases/latest', None),
])
def test_the_tag_in_a_redirect(fw, location, tag):
    assert fw.fn('netupdate_tag', C.c_char_p, C.c_char_p)(location) == tag


def test_no_redirect_no_tag(fw):
    assert fw.fn('netupdate_tag', C.c_char_p, C.c_char_p)(None) is None


@pytest.mark.parametrize('name,yes', [(b'rockbox', 1), (b'themes', 1), (b'spotify', 1), (b'q2-rockbox', 0), (b'rock', 0), (b'', 0), (b'deezer', 0)])
def test_q2_pods_own_apps(fw, name, yes):
    assert fw.fn('netupdate__special_id', C.c_int, C.c_char_p)(name) == yes


def test_the_bytes_each_kind_allows(fw):
    only = fw.fn('netupdate__only', C.c_int, C.c_char_p, C.c_uint, C.c_int)
    id_ = set(b'abcdefghijklmnopqrstuvwxyz0123456789-')
    owner = id_ | set(b'ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    repo = owner | set(b'._')
    for c in range(1, 256):
        assert only(bytes([c]), 1, 0) == (c in id_), c
        assert only(bytes([c]), 1, 1) == (c in owner), c
        assert only(bytes([c]), 1, 2) == (c in repo), c


def test_only_looks_at_n_bytes_and_none_is_not_enough(fw):
    only = fw.fn('netupdate__only', C.c_int, C.c_char_p, C.c_uint, C.c_int)
    assert only(b'abc!', 3, 0) == 1 and only(b'abc!', 4, 0) == 0 and only(b'abc', 0, 0) == 0


def sources(fw, text, count=SH_MAX, pin=True, raw=False):
    """shelf_sources over an exact table of count apps, each app's line pinned first unless pin is off; raw: the Apps."""
    out = block(fw, size=count * C.sizeof(App))
    n = fw.fn('shelf_sources', C.c_int, C.c_void_p, C.c_void_p, C.c_int)(block(fw, pinned(text) if pin else text), out, count)
    table = C.cast(out, C.POINTER(App))
    return table[:n] if raw else [(a.repo, a.name, a.id, a.dir, a.bin, a.keep) for a in table[:n]]


def test_a_card_app_from_its_line(fw):
    assert sources(fw, b'me/q2-foo Foo Bar\n') == [(b'me/q2-foo', b'Foo Bar', b'foo', b'.foo', b'.foo/q2-foo', b'')]


def test_the_id_is_the_repo_without_its_q2(fw):
    assert sources(fw, b'me/plain\n') == [(b'me/plain', b'plain', b'plain', b'.plain', b'.plain/q2-plain', b'')]


def test_q2_pods_own_repos_by_any_owner(fw):
    table = sources(fw, b'fork/q2-rockbox\nfork/q2-rockbox-themes T\nfork/q2-librespot\n')
    assert [(a[2], a[3], a[4]) for a in table] == [(b'rockbox', b'.rockbox', b'.rockbox/rockbox'), (b'themes', b'.rockbox', b''),
                                                   (b'spotify', b'.spotify', b'.spotify/librespot')]
    assert [a[5] for a in table] == [b'.rockbox/config.cfg', b'.rockbox/config.cfg', b"'.spotify/cache/*' .spotify/.cache-size"]


@pytest.mark.parametrize('line', [
    b'', b'   ', b'# comment', b'  # comment', b'#me/q2-foo',
    b'me', b'/q2-x', b'me/', b'/', b'me/q2-x/y', b'm e/q2-x', b'a.b/q2-x', b'me!/q2-x',
    b'me/q2-Foo', b'me/q2-a;b', b"me/q2-a'b", b'me/q2-a$b', b'me/..', b'me/q2-..', b'me/.', b'me/q2-a b\x01c',
    b'me/q2-x bad\x7fname',
    b'a' * 40 + b'/q2-x',                    # GitHub's longest owner is 39
    b'me/q2-' + b'a' * 16,                   # an id is 15 at most
    b'me/q2-x ' + b'n' * 32,                 # a name, 31
    b'me/rockbox', b'me/q2-spotify', b'me/themes',          # another repo's "spotify" isn't Vamp's
    b'me/coverflow', b'me/q2-downpic', b'me/q2', b'me/sldp', b'me/q2-tidal',
])
def test_a_bad_line_is_passed_over_and_the_next_read(fw, line):
    assert [a[2] for a in sources(fw, line + b'\nme/good\n')] == [b'good']


def test_the_longest_allowed_of_each(fw):
    owner, id_, name = b'a' * 39, b'b' * 15, b'n' * 31
    (repo, shown, got, folder, binary, _), = sources(fw, owner + b'/q2-' + id_ + b' ' + name)
    assert repo == owner + b'/q2-' + id_ and shown == name and got == id_
    assert folder == b'.' + id_ and binary == b'.' + id_ + b'/q2-' + id_


def test_lines_end_and_space_as_they_may(fw):
    table = sources(fw, b'\r\n  \t me/a \t  Name Two \r\nme/b\r\nme/c')
    assert [(a[0], a[1]) for a in table] == [(b'me/a', b'Name Two'), (b'me/b', b'b'), (b'me/c', b'c')]


def test_a_utf8_name_is_kept(fw):
    assert sources(fw, 'me/x Café\n'.encode())[0][1] == 'Café'.encode()


def test_a_repeated_id_keeps_the_first(fw):
    assert [a[0] for a in sources(fw, b'a/q2-x\nb/x\nc/y\n')] == [b'a/q2-x', b'c/y']


def test_two_q2_pod_repos_of_one_id_keep_the_first(fw):
    assert [a[0] for a in sources(fw, b'a/q2-librespot\nb/q2-librespot\n')] == [b'a/q2-librespot']


def test_the_table_is_never_overrun(fw):
    text = b''.join(b'me/app%d\n' % i for i in range(40))
    assert len(sources(fw, text)) == SH_MAX and len(sources(fw, text, 3)) == 3


def test_no_list_is_no_apps(fw):
    assert fw.fn('shelf_sources', C.c_int, C.c_void_p, C.c_void_p, C.c_int)(None, None, SH_MAX) == 0
    assert sources(fw, b'') == []


def test_the_repo_ships_a_list_that_parses_pins_and_all(fw):
    lines = [x for x in LIST.read_bytes().splitlines() if x.strip() and not x.startswith((b'#', b'serial ', b'signature '))]
    table = sources(fw, LIST.read_bytes(), pin=False, raw=True)
    assert len(table) == len(lines) and all(a.latest and a.asset.endswith(b'.zip') and a.size and any(a.sha256) for a in table)
    assert b'subsonic' not in [a.id for a in table]  # the firmware's own (tools/build.py BUNDLED_APPS, builtin)


def test_hostile_sources(fw):
    samples = [LIST.read_bytes(), pinned(b'me/q2-foo Foo Bar\n# c\nx/q2-librespot\n\n-me/old\n')]
    for text in hostile(31, samples, alphabet=b'/ \t\n\r#.-\x01Q0a'):
        for a in sources(fw, text.split(b'\0')[0], pin=False):
            assert 1 <= len(a[2]) <= 15 and set(a[2]) <= set(b'abcdefghijklmnopqrstuvwxyz0123456789-')
            assert a[3][:1] == b'.' and set(a[0]) <= set(b'abcdefghijklmnopqrstuvwxyz0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ._/')


def test_a_pin_and_its_fields(fw):
    sha = bytes(range(32))
    a, = sources(fw, b'me/q2-foo v1.2_rc-3 foo-1.2.zip 1234567 ' + sha.hex().encode() + b' Foo Bar\n', pin=False, raw=True)
    assert (a.latest, a.asset, a.size, bytes(a.sha256), a.name, a.dropped) == (b'v1.2_rc-3', b'foo-1.2.zip', 1234567, sha, b'Foo Bar', b'\0')


def test_a_pin_without_a_name_names_the_app_by_its_repo(fw):
    a, = sources(fw, b'me/q2-foo 1.0 f.zip 5 ' + PIN_SHA, pin=False, raw=True)
    assert a.name == b'q2-foo' and a.latest == b'1.0'


@pytest.mark.parametrize('line', [
    b'me/q2-foo Foo',                                     # no pin: nothing a Q2 could check
    b'me/q2-foo 1.0 f.zip 5 Foo',
    b'me/q2-foo 1.0 f.zip 5 ' + PIN_SHA[:-1],             # a digit short
    b'me/q2-foo 1.0 f.zip 5 ' + PIN_SHA + b'a',           # a digit over
    b'me/q2-foo 1.0 f.zip 5 ' + PIN_SHA.upper(),
    b'me/q2-foo 1.0 f.zip 5 ' + b'g' * 64,
    b'me/q2-foo 1.0 f.tar 5 ' + PIN_SHA, b'me/q2-foo 1.0 .zip 5 ' + PIN_SHA, b'me/q2-foo 1.0 f.ZIP 5 ' + PIN_SHA,
    b'me/q2-foo 1.0 f/x.zip 5 ' + PIN_SHA, b"me/q2-foo 1.0 f'.zip 5 " + PIN_SHA, b'me/q2-foo 1.0 ' + b'f' * 60 + b'.zip 5 ' + PIN_SHA,
    b'me/q2-foo ' + b't' * 32 + b' f.zip 5 ' + PIN_SHA, b'me/q2-foo 1/0 f.zip 5 ' + PIN_SHA, b'me/q2-foo 1?0 f.zip 5 ' + PIN_SHA,
    b'me/q2-foo 1.0 f.zip 0 ' + PIN_SHA, b'me/q2-foo 1.0 f.zip -5 ' + PIN_SHA, b'me/q2-foo 1.0 f.zip 5x ' + PIN_SHA,
    b'me/q2-foo 1.0 f.zip 1073741825 ' + PIN_SHA, b'me/q2-foo 1.0 f.zip 99999999999 ' + PIN_SHA,
    b'serial 3', b'signature ' + b'00' * 64,
])
def test_a_line_without_a_pin_a_q2_can_check_is_passed_over(fw, line):
    assert [a[2] for a in sources(fw, line + b'\nme/good 1.0 g.zip 5 ' + PIN_SHA + b'\n', pin=False)] == [b'good']


def test_the_longest_pin(fw):
    a, = sources(fw, b'me/q2-foo ' + b't' * 31 + b' ' + b'f' * 59 + b'.zip 1073741824 ' + PIN_SHA, pin=False, raw=True)
    assert len(a.latest) == 31 and len(a.asset) == 63 and a.size == 1 << 30


def test_a_dropped_app_needs_no_pin_and_keeps_no_tag(fw):
    table = sources(fw, b'-me/q2-old Old\n-me/q2-was 1.0 w.zip 5 ' + PIN_SHA + b' Was\n', pin=False, raw=True)
    assert [(a.id, a.name, a.dropped, a.latest) for a in table] == [(b'old', b'Old', b'\x01', b''), (b'was', b'Was', b'\x01', b'')]


def serial(fw, text):
    return fw.fn('shelf_serial', C.c_uint, C.c_char_p)(text)


@pytest.mark.parametrize('text,n', [
    (b'serial 7\n', 7), (b'# x\nserial 12\r\nme/a\n', 12), (b'serial 3', 3), (b'serial 123456789\n', 123456789),
    (b'', 0), (b'serial\n', 0), (b'serial x\n', 0), (b'serial 7 8\n', 0), (b' serial 7\n', 0), (b'serial 1234567890\n', 0),
    (b'me/a\nserial 4\nserial 5\n', 4),
])
def test_the_lists_serial(fw, text, n):
    assert serial(fw, text) == n


def test_no_list_has_no_serial(fw):
    assert serial(fw, None) == 0


def row(size, name):
    return f'{size:9}  10-06-2026 13:18   {name}\n'.encode()


HEAD = b'  Length      Date    Time    Name\n---------  ---------- -----   ----\n'
FOOT = b'---------                     -------\n 19786468                     3 files\n'


def unpacked(fw, listing, cap=SH_CAP):
    total = C.c_uint(77)
    n = fw.fn('shelf_unpacked', C.c_int, C.c_void_p, C.c_uint, C.POINTER(C.c_uint))(block(fw, listing), cap, C.byref(total))
    return (n, total.value) if n >= 0 else None


def test_entries_and_their_bytes(fw):
    assert unpacked(fw, HEAD + row(100, '.a/b') + row(20, '.a/c d') + row(0, '.a/') + FOOT) == (3, 120)


def test_nothing_listed(fw):
    assert unpacked(fw, b'') == (0, 0) and unpacked(fw, HEAD + FOOT) == (0, 0)


def test_the_last_line_needs_no_newline(fw):
    assert unpacked(fw, row(5, '.a/b').rstrip(b'\n')) == (1, 5)


def test_the_cap_is_a_most_not_a_less_than(fw):
    assert unpacked(fw, row(10, 'a') + row(5, 'b'), 15) == (2, 15)
    assert unpacked(fw, row(10, 'a') + row(6, 'b'), 15) is None


def test_a_huge_entry_is_past_the_cap(fw):
    assert unpacked(fw, row(4000000000, 'a')) is None


@pytest.mark.parametrize('name', [
    '/etc/passwd', '/', '..', '../x', 'a/..', 'a/../b', 'a/b/../../c', '.spotify/..', '.a/./../b',
])
def test_a_name_leaving_the_card_refuses_the_whole_zip(fw, name):
    assert unpacked(fw, HEAD + row(1, '.a/ok') + row(1, name) + FOOT) is None


@pytest.mark.parametrize('name', ['..a', 'a..', '...', 'a/...', 'a/..b/c', 'a/b..', '.', './x', '. .', '.. ', ' ..', 'a b/c d'])
def test_names_that_only_look_it(fw, name):
    assert unpacked(fw, row(1, name)) == (1, 1)


@pytest.mark.parametrize('line', [
    b'garbage\n', b'123\n', b'123  \n', b'123  x 2026\n', b'123   10-06-2026 13:18   a\n',
    b'   100  10-06-2026 13:18   \n',     # no name
])
def test_lines_that_are_no_entry(fw, line):
    assert unpacked(fw, line) == (0, 0)


def test_hostile_listings(fw):
    samples = [HEAD + row(19783496, '.spotify/librespot') + row(2149, '.spotify/../run') + FOOT, row(1, 'a'), row(10, '/x') + row(9, '..')]
    for listing in hostile(33, samples, alphabet=b' 0123./\n\r'):
        got = unpacked(fw, listing.split(b'\0')[0])
        assert got is None or 0 <= got[1] <= SH_CAP


def listed(fw, lines, path, n=None):
    return fw.fn('shelf_listed', C.c_int, C.c_void_p, C.c_uint, C.c_char_p)(block(fw, lines), len(lines) if n is None else n, path)


def test_a_path_in_a_list_of_lines(fw):
    lines = b'.a/b\0.a/c d\0.e\0'
    assert listed(fw, lines, b'.a/b') == 1 and listed(fw, lines, b'.a/c d') == 1 and listed(fw, lines, b'.e') == 1
    assert listed(fw, lines, b'.a') == 0 and listed(fw, lines, b'.a/c') == 0 and listed(fw, lines, b'') == 0


def test_a_last_line_without_its_nul_is_read_to_the_end(fw):
    assert listed(fw, b'.a\0.b', b'.b') == 1  # read_all's NUL ends it


def test_a_blank_line_matches_an_empty_path_only(fw):
    assert listed(fw, b'\0.a\0', b'') == 1 and listed(fw, b'\0.a\0', b'.a') == 1


def test_lines_past_n_are_not_looked_at(fw):
    assert listed(fw, b'.a\0.b\0', b'.b', 3) == 0
    assert listed(fw, b'', b'.a') == 0


def test_no_list_is_no_match(fw):
    assert fw.fn('shelf_listed', C.c_int, C.c_void_p, C.c_uint, C.c_char_p)(None, 0, b'.a') == 0


def apps(fw, *lines):
    """The Apps page's list, as shelf_sources filled it from lines pinned to tag 1.0."""
    table = (App * SH_MAX).in_dll(fw.lib, 'shelf_apps')
    n = fw.fn('shelf_sources', C.c_int, C.c_char_p, C.c_void_p, C.c_int)(pinned(b'\n'.join(lines)), table, SH_MAX)
    fw.var('shelf_n', C.c_int).value = n
    return table


def test_a_staged_path_and_an_app_file(fw):
    apps(fw, b'me/foo')
    out = block(fw, size=96)
    fw.fn('netupdate__staged', None, C.c_void_p, C.c_uint, C.c_int, C.c_char_p)(out, 96, 0, b'.part')
    assert C.string_at(out).decode() == STAGING + '/foo.part'
    fw.fn('netupdate__app_file', None, C.c_void_p, C.c_uint, C.c_int, C.c_char_p)(out, 96, 0, b'.tag')
    assert C.string_at(out) == b'/mnt/mmc/.q2/apps/foo.tag'


def test_a_staged_path_is_cut_to_its_buffer(fw):
    apps(fw, b'me/foo')
    out = block(fw, size=8)
    fw.fn('netupdate__staged', None, C.c_void_p, C.c_uint, C.c_int, C.c_char_p)(out, 8, 0, b'')
    assert C.string_at(out) == b'/mnt/mm'


def test_every_card_path_fits_the_longest_id(fw):
    apps(fw, b'me/q2-' + b'a' * 15)
    out = block(fw, size=96)
    fw.fn('netupdate__staged', None, C.c_void_p, C.c_uint, C.c_int, C.c_char_p)(out, 96, 0, b'.files')
    assert C.string_at(out).decode().endswith('a' * 15 + '.files')


def test_progress_is_percent_of_kilobytes(fw):
    progress = fw.fn('netupdate__progress', C.c_int, C.c_void_p, C.c_longlong, C.c_longlong, C.c_longlong, C.c_longlong)
    x = (C.c_int * 2)(0, -1)
    assert progress(x, 1023, 500, 0, 0) == 0 and x[1] == -1      # no length yet
    assert progress(x, 1024, 512, 0, 0) == 0 and x[1] == 0
    assert progress(x, 200 << 20, 50 << 20, 0, 0) == 0 and x[1] == 25
    assert progress(x, 200 << 20, 200 << 20, 0, 0) == 0 and x[1] == 100
    x[0] = 1
    assert progress(x, 0, 0, 0, 0) == 1


def test_an_app_by_id(fw):
    apps(fw, b'me/foo', b'me/bar')
    is_ = fw.fn('netupdate__is', C.c_int, C.c_int, C.c_char_p)
    find = fw.fn('netupdate__sh_find', C.c_int, C.c_char_p)
    assert is_(1, b'bar') == 1 and is_(0, b'bar') == 0 and is_(-1, b'foo') == 0 and is_(2, b'') == 0 and is_(SH_MAX, b'') == 0
    assert find(b'bar') == 1 and find(b'foo') == 0 and find(b'baz') == -1 and find(b'') == -1


def test_the_folder_a_job_is_replacing(fw):
    apps(fw, b'me/foo', b'me/bar')
    sh = fw.var('netupdate__sh', Sh)
    busy = fw.fn('shelf_busy', C.c_char_p)
    assert busy() is None
    sh.running, sh.job, sh.app = 1, JOB_INSTALL, 1
    assert busy() == b'.bar'
    sh.job = JOB_REMOVE
    assert busy() == b'.bar'
    for job in (JOB_CHECK, JOB_ERASE):
        sh.job = job
        assert busy() is None
    sh.job, sh.running = JOB_INSTALL, 0
    assert busy() is None
    moving = fw.fn('shelf_moving', C.c_char_p)  # once its files move: a remove's whole run, an install's stage 3
    for job, stage, folder in ((JOB_INSTALL, 1, None), (JOB_INSTALL, 2, None), (JOB_INSTALL, 3, b'.bar'),
                               (JOB_REMOVE, 4, b'.bar'), (JOB_CHECK, 4, None), (JOB_ERASE, 4, None)):
        sh.running, sh.job, sh.stage = 1, job, stage
        assert moving() == folder
    sh.running = 0
    assert moving() is None


def test_closing_the_page_forgets_its_rows_only(fw):
    sh = fw.var('netupdate__sh', Sh)
    sh.page, sh.rows, sh.armed, sh.job = 9, 3, 2, JOB_INSTALL
    assert fw.fn('netupdate__sh_closed', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0
    assert not sh.page and sh.rows == 0 and sh.armed == 0 and sh.job == JOB_INSTALL


class Card:
    """fopen, fread, fseek, ftell, fclose, __xstat, mkdir, rename, unlink, rmdir and system over a dict of files."""
    def __init__(self, fw, files=None, dirs=()):
        self.files, self.dirs, self.log, self.at, self.names = dict(files or {}), set(dirs), [], {}, {}
        self.written, self.system = {}, lambda cmd: 0
        def fopen(path, mode, *_):
            name, how = C.string_at(path), C.string_at(mode)
            if b'r' in how and name not in self.files:
                return 0
            f = len(self.names) + 1
            self.names[f], self.at[f] = name, 0
            if b'w' in how:
                self.written[name] = b''
            return f
        def fread(buf, size, n, f, *_):
            piece = self.files[self.names[f]][self.at[f]:self.at[f] + size * n]
            C.memmove(buf, piece, len(piece))
            self.at[f] += len(piece)
            return len(piece) // size
        def fseek(f, at, whence, *_):
            self.at[f] = len(self.files[self.names[f]]) if whence == 2 else at
        def fwrite(buf, size, n, f, *_):
            self.written[self.names[f]] += C.string_at(buf, size * n)
            return n
        def rename(a, b, *_):
            a, b = C.string_at(a), C.string_at(b)
            self.log.append(('rename', a, b))
            if a in self.written and a not in self.files:
                self.files[a] = self.written[a]
            self.files[b] = self.files.pop(a, b'')
        def unlink(p, *_):
            self.log.append(('unlink', C.string_at(p)))
            self.files.pop(C.string_at(p), None)
        def mkdir(p, mode, *_):
            self.log.append(('mkdir', C.string_at(p)))
        def rmdir(p, *_):
            name = C.string_at(p)
            self.log.append(('rmdir', name))
            if any(f.startswith(name + b'/') for f in self.files):
                return -1
            self.dirs.discard(name)
        def system(cmd, *_):
            self.log.append(('system', C.string_at(cmd)))
            return self.system(C.string_at(cmd))
        def stat(version, path, info, *_):
            name = C.string_at(path)
            if name not in self.files:
                return -1
            C.cast(info, C.POINTER(C.c_uint))[5] = 0o100644
        for stub, fn in (('fw_fopen', fopen), ('fw_fread', fread), ('fw_fseek', fseek), ('fw_fwrite', fwrite), ('fw_rename', rename),
                         ('fw_unlink', unlink), ('fw_mkdir', mkdir), ('fw_rmdir', rmdir), ('fw_system', system), ('fw___xstat', stat)):
            fw.mock(stub, fn)
        fw.mock('fw_ftell', lambda f, *_: self.at[f])
        fw.mock('fw_access', lambda p, *_: 0 if C.string_at(p) in self.files else -1)

    def of(self, kind):
        return [e[1:] for e in self.log if e[0] == kind]


def test_a_file_of_lines_is_nul_ended_in_place(fw):
    Card(fw, {b'/l': b'a\nb c\n\nd'})
    n = C.c_uint(0)
    p = fw.fn('netupdate__lines', C.c_void_p, C.c_char_p, C.POINTER(C.c_uint))(b'/l', C.byref(n))
    assert n.value == 8 and C.string_at(p, 8) == b'a\0b c\0\0d'


def test_a_missing_file_of_lines_is_none(fw):
    Card(fw)
    n = C.c_uint(9)
    assert fw.fn('netupdate__lines', C.c_void_p, C.c_char_p, C.POINTER(C.c_uint))(b'/l', C.byref(n)) is None and n.value == 0


def test_a_file_of_lines_over_4mb_is_refused(fw):
    Card(fw, {b'/l': b'a' * ((4 << 20) + 1)})
    n = C.c_uint(9)
    assert fw.fn('netupdate__lines', C.c_void_p, C.c_char_p, C.POINTER(C.c_uint))(b'/l', C.byref(n)) is None


def move(fw, rel):
    return fw.fn('netupdate__move', C.c_int, C.c_int, C.c_char_p)(0, rel)


def test_a_file_moves_with_its_folders_made(fw):
    apps(fw, b'me/foo')
    card = Card(fw, {f'{STAGING}/foo/.foo/a/b.bin'.encode(): b'x'})
    assert move(fw, b'.foo/a/b.bin') == 0
    assert card.log == [('mkdir', b'/mnt/mmc/.foo'), ('mkdir', b'/mnt/mmc/.foo/a'),
                        ('rename', f'{STAGING}/foo/.foo/a/b.bin'.encode(), b'/mnt/mmc/.foo/a/b.bin')]


def test_a_path_too_long_moves_nothing(fw):
    apps(fw, b'me/foo')
    card = Card(fw)
    assert move(fw, b'a' * 600) == 1 and card.log == []


def test_the_longest_path_fits_exactly(fw):
    apps(fw, b'me/foo')
    Card(fw)
    room = 511 - len(f'{STAGING}/foo/')
    assert move(fw, b'a' * room) == 0 and move(fw, b'a' * (room + 1)) == 1


def place(fw):
    return fw.fn('shelf_place', C.c_int, C.c_int)(0)


def staged_list(*names):
    return ''.join(name + '\n' for name in names).encode()


def test_files_are_placed_with_the_binary_last_then_listed(fw):
    apps(fw, b'me/foo')
    card = Card(fw, {f'{STAGING}/foo.files'.encode(): staged_list('.foo/q2-foo', '.foo/a', '.foo/b')})
    assert place(fw) == SH_OK
    moved = [e[0].rsplit(b'/')[-1] for e in card.of('rename')]
    assert moved == [b'a', b'b', b'q2-foo', b'foo.files']
    assert card.of('rename')[-1] == (f'{STAGING}/foo.files'.encode(), b'/mnt/mmc/.q2/apps/foo.files')
    assert card.of('system')[-1] == (b'sync',)


def test_a_failed_move_keeps_the_old_binary(fw):
    apps(fw, b'me/foo')
    card = Card(fw, {f'{STAGING}/foo.files'.encode(): staged_list('.foo/q2-foo', '.foo/a')})
    tried = []
    def rename(a, b, *_):
        tried.append(C.string_at(a))
        return -1 if tried[-1].endswith(b'/.foo/a') else 0
    fw.mock('fw_rename', rename)
    assert place(fw) == SH_CARD
    assert not [p for p in tried if p.endswith(b'q2-foo')]


def test_nothing_to_place_is_a_failure(fw):
    apps(fw, b'me/foo')
    card = Card(fw, {f'{STAGING}/foo.files'.encode(): b''})
    assert place(fw) == SH_CARD


def test_a_failed_find_places_nothing(fw):
    apps(fw, b'me/foo')
    card = Card(fw, {f'{STAGING}/foo.files'.encode(): staged_list('.foo/a')})
    card.system = lambda cmd: 1 if b'find' in cmd else 0
    assert place(fw) == SH_CARD and not card.of('rename')


def remove(fw, i):
    fw.fn('shelf_remove', None, C.c_int)(i)


def test_a_card_app_goes_whole_with_its_list_and_tag(fw):
    apps(fw, b'me/foo')
    files = {b'/mnt/mmc/.q2/apps/foo.files': b'.foo/q2-foo\n.foo/a\n', b'/mnt/mmc/.foo/q2-foo': b'', b'/mnt/mmc/.foo/a': b'',
             b'/mnt/mmc/.q2/apps/foo.tag': b'1.0'}
    card = Card(fw, files)
    remove(fw, 0)
    assert (b"rm -rf '/mnt/mmc/.foo'",) in card.of('system')
    assert card.of('unlink')[0] == (b'/mnt/mmc/.foo/q2-foo',)  # first: a part remove still boots Vamp
    assert (b'/mnt/mmc/.q2/apps/foo.files',) in card.of('unlink') and (b'/mnt/mmc/.q2/apps/foo.tag',) in card.of('unlink')


def test_a_file_two_apps_list_stays_and_q2_pods_own_folder_is_never_deleted_whole(fw):
    apps(fw, b'x/q2-rockbox', b'x/q2-rockbox-themes')
    files = {b'/mnt/mmc/.q2/apps/rockbox.files': b'.rockbox/rockbox\n.rockbox/fonts/a.fnt\n',
             b'/mnt/mmc/.q2/apps/themes.files': b'.rockbox/fonts/a.fnt\n.rockbox/wps/b\n',
             b'/mnt/mmc/.rockbox/fonts/a.fnt': b'', b'/mnt/mmc/.rockbox/wps/b': b'', b'/mnt/mmc/.rockbox/rockbox': b''}
    card = Card(fw, files)
    remove(fw, 1)
    assert (b'/mnt/mmc/.rockbox/wps/b',) in card.of('unlink') and (b'/mnt/mmc/.rockbox/fonts/a.fnt',) not in card.of('unlink')
    assert not [c for c in card.of('system') if c[0].startswith(b'rm')]


def test_empty_folders_go_up_to_the_cards_root_and_no_further(fw):
    apps(fw, b'x/q2-rockbox')
    card = Card(fw, {b'/mnt/mmc/.q2/apps/rockbox.files': b'.rockbox/a/b/c\n'})
    remove(fw, 0)
    assert card.of('rmdir') == [(b'/mnt/mmc/.rockbox/a/b',), (b'/mnt/mmc/.rockbox/a',), (b'/mnt/mmc/.rockbox',)]


def test_a_folder_left_with_files_stops_the_climb(fw):
    apps(fw, b'x/q2-rockbox')
    card = Card(fw, {b'/mnt/mmc/.q2/apps/rockbox.files': b'.rockbox/a/b/c\n', b'/mnt/mmc/.rockbox/a/keep': b''})
    remove(fw, 0)
    assert card.of('rmdir') == [(b'/mnt/mmc/.rockbox/a/b',), (b'/mnt/mmc/.rockbox/a',)]


def test_a_remove_with_no_list_still_goes(fw):
    apps(fw, b'me/foo')
    card = Card(fw)
    remove(fw, 0)
    assert (b"rm -rf '/mnt/mmc/.foo'",) in card.of('system')


def test_unzip_is_listed_then_checked(fw):
    apps(fw, b'me/foo')
    listing = f'{STAGING}/foo.list'.encode()
    card = Card(fw, {listing: row(5, '.foo/a') + row(6, '.foo/b')})
    total = C.c_uint(0)
    assert fw.fn('shelf_list', C.c_int, C.c_int, C.POINTER(C.c_uint))(0, C.byref(total)) == SH_OK and total.value == 11
    assert card.of('system') == [(f"busybox unzip -l -qq {STAGING}/foo.part '.foo/*' -x '*/._*'  > {STAGING}/foo.list".encode(),)]
    assert card.of('unlink') == [(listing,)]


@pytest.mark.parametrize('listing', [b'', row(1, '../x'), HEAD + FOOT])
def test_a_zip_with_nothing_safe_is_bad(fw, listing):
    apps(fw, b'me/foo')
    Card(fw, {f'{STAGING}/foo.list'.encode(): listing})
    total = C.c_uint(0)
    assert fw.fn('shelf_list', C.c_int, C.c_int, C.POINTER(C.c_uint))(0, C.byref(total)) == SH_BAD


def test_a_failed_listing_is_bad(fw):
    apps(fw, b'me/foo')
    card = Card(fw)
    card.system = lambda cmd: 1
    total = C.c_uint(0)
    assert fw.fn('shelf_list', C.c_int, C.c_int, C.POINTER(C.c_uint))(0, C.byref(total)) == SH_BAD


def test_unpacking_keeps_a_special_apps_kept_files(fw):
    apps(fw, b'x/q2-librespot')
    card = Card(fw)
    assert fw.fn('shelf_unpack', C.c_int, C.c_int)(0) == SH_OK
    cmd = card.of('system')[0][0].decode()
    assert cmd == f"busybox unzip -o -qq -d {STAGING}/spotify {STAGING}/spotify.part '.spotify/*' -x '*/._*' '.spotify/cache/*' .spotify/.cache-size"


def test_a_failed_unpack_is_a_card_error(fw):
    apps(fw, b'me/foo')
    Card(fw).system = lambda cmd: 1
    assert fw.fn('shelf_unpack', C.c_int, C.c_int)(0) == SH_CARD


class Net:
    """libcurl: each transfer answered by the first of its URL's replies: (result, code, location[, body]). A body goes
    through the transfer's own write callback in CHUNKS pieces, its progress callback told after each and during(k)
    run then, as a UI timer might: a short write ends it as curl's CURLE_WRITE_ERROR (23), a progress callback's
    non-zero as CURLE_ABORTED_BY_CALLBACK (42). during(k) runs in update.tar.sig's transfer too only with sig_too."""
    def __init__(self, fw, replies, during=None, sig_too=False, length=-1.0):
        self.replies, self.urls, self.opts, self.during, self.sig_too = replies, [], {}, during, sig_too
        self.card_list = b''  # store_net's: the list a check leaves on the card
        self.location = None
        def setopt(c, opt, value, *_):
            self.opts[opt] = value
        def perform(c, *_):
            url = C.string_at(self.opts[10002])
            self.urls.append(url)
            self.result, self.code, location, *body = next(v for k, v in self.replies.items() if url.startswith(k))
            self.location = C.create_string_buffer(location) if location else None
            return self.transfer(body[0], self.sig_too or not url.endswith(b'.sig')) if body and 20011 in self.opts else self.result
        def info(c, what, out, *_):
            if what == 0x200002:
                C.cast(out, C.POINTER(C.c_long))[0] = self.code
            elif what == 0x10001f:
                C.cast(out, C.POINTER(C.c_void_p))[0] = C.addressof(self.location) if self.location else 0
            elif what == 0x30000f:  # CONTENT_LENGTH_DOWNLOAD
                C.cast(out, C.POINTER(C.c_double))[0] = length
        fw.mock('fw_curl_easy_init', lambda *_: self.opts.clear() or 1)
        fw.mock('fw_curl_easy_setopt', setopt)
        fw.mock('fw_curl_easy_perform', perform)
        fw.mock('fw_curl_easy_getinfo', info)

    def transfer(self, body, watched=True):
        write = C.CFUNCTYPE(C.c_uint, C.c_void_p, C.c_uint, C.c_uint, C.c_void_p)(self.opts[20011])  # WRITEFUNCTION
        progress = C.CFUNCTYPE(C.c_int, C.c_void_p, *[C.c_longlong] * 4)(self.opts[20219]) if 20219 in self.opts else None
        step = max(1, -(-len(body) // CHUNKS))
        for k, at in enumerate(range(0, len(body), step)):
            piece = body[at:at + step]
            if write(C.create_string_buffer(piece, len(piece)), 1, len(piece), self.opts[10001]) != len(piece):
                return 23
            if progress and progress(self.opts[10057], len(body), at + len(piece), 0, 0):
                return 42
            if self.during and watched:
                self.during(k)
        return self.result


CHUNKS = 4


RELEASES = b'https://github.com/owner/repo/releases'
LATEST = RELEASES + b'/latest'
ASSET = RELEASES + b'/download/' + NEWER + b'/update.tar'
SIG = ASSET + b'.sig'  # before ASSET in a Net's replies, as ASSET is its prefix
PART = b'/mnt/mmc/update.tar.part'
PEM = None


@pytest.fixture(autouse=True)
def release_key(signing_pem):
    """The private half of the key this library checks releases with (host.signing_key), for signed()."""
    global PEM
    PEM = signing_pem


def signed(body, tag=NEWER):
    """update.tar.sig for body as the release tagged tag: tools/signing.py's message (test_release_sig.py checks
    that the firmware's is the same)."""
    return host.tools_signing.sign(b'q2-coda-update-v1 %s %s\n' % (tag, hashlib.sha512(body).hexdigest().encode()), PEM)


def rel(asset=(0, 200, None, b'tar'), latest=(0, 302, RELEASES + b'/tag/' + NEWER), sig=None):
    """A release's replies: the tag's redirect, update.tar.sig (signed for asset's body unless sig is given) and
    update.tar."""
    body = asset[3] if len(asset) > 3 else b''
    return {SIG: sig if sig is not None else (0, 200, None, signed(body)), ASSET: asset, LATEST: latest}


def update(fw, code=302, location=RELEASES + b'/tag/' + NEWER, asset=(0, 200, None, b'tar'), card=None, sig=None):
    net = Net(fw, rel(asset, (0, code, location), sig))
    card = card or Disk(fw)
    nu = fw.var('netupdate__nu', Nu)
    fw.fn('netupdate__worker', C.c_void_p, C.c_void_p)(None)
    return nu.state, net, card


def test_a_newer_tag_downloads_and_renames_once_whole(fw):
    state, net, card = update(fw)
    assert state == NU_DONE and net.urls == [LATEST, SIG, ASSET]
    assert card.of('rename') == [(PART, b'/mnt/mmc/update.tar')] and card.files[b'/mnt/mmc/update.tar'] == b'tar'
    assert b'/tmp/q2-update.sig' not in card.files


def test_a_release_without_a_signature_downloads_nothing(fw):
    """q2-pod's upstream releases have no update.tar.sig: nothing is fetched past the signature's 404."""
    state, net, card = update(fw, sig=(0, 404, None, b'Not Found'))
    assert state == NU_UNSIGNED and net.urls == [LATEST, SIG] and PART not in card.written


@pytest.mark.parametrize('sig', [b'', b'x' * 63, b'x' * 65, b'x' * 128])
def test_a_signature_that_is_no_signature_downloads_nothing(fw, sig):
    state, net, _ = update(fw, sig=(0, 200, None, sig))
    assert state == NU_UNSIGNED and net.urls == [LATEST, SIG]


def test_a_signature_that_cant_be_fetched_is_no_network(fw):
    assert update(fw, sig=(7, 0, None))[0] == NU_NET
    assert update(fw, sig=(0, 500, None, b'oops'))[0] == NU_NET


def test_a_forged_update_never_replaces_the_tar(fw):
    """The tar's bytes changed after signing, a signature of another tag's, of another key's, or flipped: the part is
    deleted and the old update.tar stays."""
    for sig in (signed(b'tar!'), signed(b'tar', TAG), bytes(64), bytes([signed(b'tar')[0] ^ 1]) + signed(b'tar')[1:]):
        card = Disk(fw, {TAR: b'old'})
        state, net, card = update(fw, sig=(0, 200, None, sig), card=card)
        assert state == NU_FORGED and net.urls == [LATEST, SIG, ASSET]
        assert card.files[TAR] == b'old' and PART not in card.files and not card.of('rename')


def test_another_keys_signature_is_forged(fw, tmp_path):
    other = tmp_path/'other.pem'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'ed25519', '-out', str(other)], check=True, capture_output=True)
    m = b'q2-coda-update-v1 %s %s\n' % (NEWER, hashlib.sha512(b'tar').hexdigest().encode())
    assert update(fw, sig=(0, 200, None, host.tools_signing.sign(m, other.read_text())))[0] == NU_FORGED


def test_the_check_reads_the_part_back_from_the_card(fw):
    """What the card holds is what is checked, not what curl handed over: a byte the card got wrong fails."""
    card = Disk(fw, {TAR: b'old'})
    real = card.fclose

    def fclose(f, *_):
        r = real(f)
        if card.names[f] == PART:
            card.files[PART] = b'taR'
        return r
    fw.mock('fw_fclose', fclose)
    assert update(fw, card=card)[0] == NU_FORGED and card.files[TAR] == b'old'


@pytest.mark.parametrize('tag', [b'1.0 x', b'1.0?x', b'1.0%2F', b'9' * 32])
def test_a_tag_that_cant_be_signed_or_fetched_is_no_tag(fw, tag):
    state, net, _ = update(fw, location=RELEASES + b'/tag/' + tag)
    assert state == NU_NET and net.urls == [LATEST]


@pytest.mark.parametrize('tag', [TAG, OLDER])
def test_nothing_older_or_equal_is_ever_flashed(fw, tag):
    state, net, _ = update(fw, location=RELEASES + b'/tag/' + tag)
    assert state == NU_LATEST and net.urls == [LATEST]


@pytest.mark.parametrize('code,location', [(200, None), (302, b'https://github.com/owner/repo/releases'), (302, b''), (404, None)])
def test_no_tag_is_no_network(fw, code, location):
    assert update(fw, code, location)[0] == NU_NET


def test_a_long_redirect_is_no_tag(fw):
    assert update(fw, location=b'https://x/releases/tag/9' + b'9' * 200)[0] == NU_NET


def test_a_missing_asset_leaves_no_part(fw):
    state, _, card = update(fw, asset=(0, 404, None))
    assert state == NU_MISSING and card.of('unlink') == [(b'/tmp/q2-update.sig',), (PART,)] and not card.of('rename')


def test_a_failed_transfer_says_network_a_write_error_card(fw):
    assert update(fw, asset=(28, 0, None))[0] == NU_NET
    fw.mock('fw_ferror', lambda *_: 1)
    assert update(fw, asset=(23, 0, None))[0] == NU_CARD


def test_a_card_that_cant_open_the_part(fw):
    card = Disk(fw)
    real = card.fopen
    fw.mock('fw_fopen', lambda path, mode, *_: 0 if C.string_at(path) == PART else real(path, mode))
    assert update(fw, card=card)[0] == NU_CARD


def test_no_room_for_the_signature(fw):
    card = Disk(fw)
    fw.mock('fw_fopen', lambda *_: 0)
    state, net, _ = update(fw, card=card)
    assert state == NU_CARD and net.urls == [LATEST]


def test_a_cancel_during_the_check_downloads_nothing(fw):
    fw.var('netupdate__nu', Nu).cancel = 1
    state, net, _ = update(fw)
    assert state == NU_CANCELLED and net.urls == [LATEST]


def test_a_cancel_during_the_signatures_fetch_downloads_nothing(fw):
    nu = fw.var('netupdate__nu', Nu)
    Disk(fw)
    net = Net(fw, rel(), during=lambda k: setattr(nu, 'cancel', 1), sig_too=True)
    fw.fn('netupdate__worker', C.c_void_p, C.c_void_p)(None)
    assert nu.state == NU_CANCELLED and net.urls == [LATEST, SIG]


def test_a_cancel_while_the_signature_is_checked_is_a_cancel(fw):
    card = Disk(fw, {TAR: b'old'})
    nu = fw.var('netupdate__nu', Nu)
    real = card.fopen

    def fopen(path, mode, *_):  # the check's read of the part
        if C.string_at(path) == PART and b'r' in C.string_at(mode):
            nu.cancel = 1
        return real(path, mode)
    fw.mock('fw_fopen', fopen)
    state, _, card = update(fw, card=card)
    assert state == NU_CANCELLED and nu.verifying == 1 and card.files[TAR] == b'old' and PART not in card.files


def test_a_cancel_while_the_card_takes_the_last_bytes_is_a_cancel(fw):
    """The press lands during the fsync of the whole part, seconds on a slow card with the label at 100%: the part
    goes and the old tar stays, as for any cancel, and the word is Download canceled."""
    card = Disk(fw, {TAR: b'old'})
    nu = fw.var('netupdate__nu', Nu)
    fw.mock('fw_fsync', lambda fd, *_: setattr(nu, 'cancel', 1) or 0)
    state, _, card = update(fw, card=card)
    assert state == NU_CANCELLED and card.files[TAR] == b'old' and PART not in card.files and not card.of('rename')


def head(fw, url=b'https://x', tag_size=64, size=False, secs=5):
    """netupdate.c's head: (its error, the tag, the code, the size in KB)."""
    tag, code, kb = block(fw, size=tag_size), C.c_long(0), C.c_uint(77)
    fn = fw.fn('netupdate__head', C.c_int, C.c_char_p, C.c_long, C.c_void_p, C.c_uint, C.c_void_p, C.POINTER(C.c_long))
    err = fn(url, secs, None if size else tag, tag_size, C.addressof(kb) if size else None, C.byref(code))
    return err, C.string_at(tag), code.value, kb.value


def test_a_header_fetch_sends_no_body_and_keeps_the_tag(fw):
    net = Net(fw, {b'https': (0, 302, b'https://g/releases/tag/1.2.3')})
    assert head(fw)[:3] == (0, b'1.2.3', 302)
    assert net.opts[44] == 1 and 52 not in net.opts and net.opts[13] == 5 and net.opts[78] == 5  # NOBODY, no
    # FOLLOWLOCATION, the whole request and its connect in secs


def test_a_size_follows_the_redirects_and_reads_the_length(fw):
    net = Net(fw, {b'https': (0, 200, None)}, length=56_000_000.0)
    assert head(fw, size=True) == (0, b'', 200, 56_000_000 // 1024) and net.opts[52] == 1


def test_a_download_follows_redirects_to_its_file_and_verifies(fw):
    net = Net(fw, {b'https': (0, 200, None)})
    code, xfer = C.c_long(0), (C.c_int * 2)()
    fn = fw.fn('netupdate__fetch', C.c_int, C.c_char_p, C.c_void_p, C.POINTER(C.c_long), C.c_void_p)
    assert fn(b'https://x', 7, C.byref(code), xfer) == 0
    assert net.opts[52] == 1 and net.opts[10001] == 7 and net.opts[64] == 1 and net.opts[81] == 2


def test_no_curl_handle_is_an_error(fw):
    assert head(fw)[0] == 1


def test_a_redirect_with_no_location_clears_the_tag(fw):
    Net(fw, {b'https': (0, 302, None)})
    assert head(fw)[:2] == (0, b'')


def online(fw, replies):
    """netupdate_check over replies: its answer, the tag and the size in KB."""
    Net(fw, replies, length=56_000_000.0)
    tag, kb = C.create_string_buffer(32), C.c_uint(9)
    r = fw.fn('netupdate_check', C.c_int, C.c_void_p, C.c_uint, C.POINTER(C.c_uint))(tag, 32, C.byref(kb))
    return r, tag.value, kb.value


def test_the_check_reads_a_newer_releases_tag_and_size(fw):
    assert online(fw, rel()) == (NET_NEWER, NEWER, 56_000_000 // 1024)


@pytest.mark.parametrize('replies', [
    lambda: {LATEST: (6, 0, None)},  # no DNS
    lambda: {LATEST: (28, 0, None)},  # timed out
    lambda: {LATEST: (0, 200, None)},  # no redirect
    lambda: rel((0, 404, None)),  # the release has no update.tar
    lambda: rel((28, 0, None)),
])
def test_a_check_that_cant_answer_failed(fw, replies):
    assert online(fw, replies())[0] == NET_FAILED


def test_this_builds_own_release_or_an_older_is_the_latest(fw):
    assert online(fw, {LATEST: (0, 302, TAG_URL + TAG)})[0] == NET_LATEST


def sign_list(body):
    """body with its signature line, the release key's (this library's) signature of it."""
    return body + b'signature ' + host.tools_signing.sign(host.tools_signing.list_message(body), PEM).hex().encode() + b'\n'


def listing(text, serial=1, **pin):
    """A list as q2-coda publishes it: numbered, each app pinned (pinned's pin), signed."""
    return sign_list(b'serial %d\n' % serial + pinned(text, **pin))


def fresh(fw, card, body, code=200, result=0):
    """netupdate.c's fresh_list over a fetch answered with body: the list's text, or None and why."""
    Net(fw, {b'https://raw': (result, code, None)})
    card.files[JSON_PATH] = body
    why = C.c_int(-1)
    got = fw.fn('netupdate__fresh_list', C.c_void_p, C.POINTER(C.c_int))(C.byref(why))
    return (C.string_at(got) if got else None), why.value


def test_a_signed_list_is_taken_without_its_signature_line(fw):
    card = Card(fw)
    text = listing(b'me/foo Foo\n')
    assert fresh(fw, card, text)[0] == text[:text.index(b'signature ')] and (JSON_PATH,) in card.of('unlink')


@pytest.mark.parametrize('mangle', [
    lambda t: t[:t.index(b'signature ')],                    # unsigned: q2-pod's own list, or any edit not signed
    lambda t: t.replace(b'Foo', b'Fop'),                     # changed after signing
    lambda t: t.replace(PIN_SHA, b'cd' * 32, 1),            # another zip pinned
    lambda t: t[:-3] + b'%02x\n' % (int(t[-3:-1], 16) ^ 0xff),  # the signature changed: its last byte flipped, never itself
])
def test_a_list_the_release_key_didnt_sign_is_refused(fw, mangle):
    assert fresh(fw, Card(fw), mangle(listing(b'me/foo Foo\n'))) == (None, SH_UNSIGNED)


def test_another_keys_list_is_refused(fw, tmp_path):
    other = tmp_path/'other.pem'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'ed25519', '-out', str(other)], check=True, capture_output=True)
    body = b'serial 1\n' + pinned(b'me/foo Foo\n')
    text = body + b'signature ' + host.tools_signing.sign(host.tools_signing.list_message(body), other.read_text()).hex().encode() + b'\n'
    assert fresh(fw, Card(fw), text) == (None, SH_UNSIGNED)


def test_an_older_signed_list_is_refused(fw):
    """A list signed before the card's was written, served again: its serial is below the card's list's."""
    card = Card(fw, {SOURCES: b'serial 5\n' + pinned(b'me/foo Foo\n')})
    assert fresh(fw, card, listing(b'me/foo Foo\n', 4)) == (None, SH_UNSIGNED)
    assert fresh(fw, card, listing(b'me/foo Foo\n', 5))[0].startswith(b'serial 5\n')
    assert fresh(fw, card, listing(b'me/foo Foo\n', 6))[0].startswith(b'serial 6\n')


def test_a_card_list_with_no_serial_takes_any_signed_list(fw):
    assert fresh(fw, Card(fw, {SOURCES: pinned(b'me/foo Foo\n')}), listing(b'me/foo Foo\n', 1))[0]


@pytest.mark.parametrize('code,result,why', [(404, 0, SH_NET), (500, 0, SH_NET), (403, 0, SH_BUSY), (429, 0, SH_BUSY), (0, 7, SH_NET)])
def test_a_list_that_cant_be_fetched_says_why(fw, code, result, why):
    assert fresh(fw, Card(fw), listing(b'me/foo\n'), code, result) == (None, why)


def test_a_list_over_a_megabyte_is_refused(fw):
    assert fresh(fw, Card(fw), b' ' * ((1 << 20) + 1))[0] is None


def room(fw, bytes_, blocks, size=4096, ok=0):
    def statfs(path, buf, *_):
        words = C.cast(buf, C.POINTER(C.c_uint))
        words[1], words[7] = size, blocks
        return ok
    fw.mock('fw_statfs', statfs)
    return fw.fn('netupdate__room', C.c_int, C.c_uint)(bytes_), fw.var('netupdate__sh', Sh).need


RESERVE = 64 << 20


def test_room_for_the_bytes_and_the_reserve(fw):
    assert room(fw, 4096, (RESERVE + 4096) // 4096)[0] == SH_OK


def test_one_block_short_says_how_many_mb(fw):
    assert room(fw, 8192, (RESERVE + 4096) // 4096) == (SH_SPACE, 1)
    assert room(fw, 100 << 20, 0) == (SH_SPACE, 164)


def test_a_huge_card_doesnt_overflow(fw):
    assert room(fw, 1 << 30, 1 << 31, 32768)[0] == SH_OK


def test_no_statfs_is_a_card_error(fw):
    assert room(fw, 1, 1 << 20, ok=-1)[0] == SH_CARD


ZIP_URL = b'https://github.com/me/foo/releases/download/1.0/app.zip'


def install(fw, card, net=None, size=5, blocks=1 << 24, body=b'', sha=None, readable=True):
    """netupdate.c's install of me/foo from its pin (tag 1.0, app.zip, size bytes, body's SHA-256 unless sha), the zip
    answered with body; the part readable once written, as the card's is."""
    net = net or Net(fw, {b'https://github.com/': (0, 200, None, body)})
    table = apps(fw, b'me/foo')
    pin = App.from_buffer_copy(table[0])
    pin.size = size
    pin.sha256[:] = sha or hashlib.sha256(body).digest()
    def statfs(path, buf, *_):
        words = C.cast(buf, C.POINTER(C.c_uint))
        words[1], words[7] = 4096, blocks
    fw.mock('fw_statfs', statfs)
    def fclose(f, *_):
        if readable and card.names[f] in card.written:
            card.files[card.names[f]] = card.written[card.names[f]]
        return 0
    fw.mock('fw_fclose', fclose)
    return fw.fn('netupdate__install', C.c_int, C.c_int, C.c_void_p)(0, C.byref(pin)), fw.var('netupdate__sh', Sh), table


def installing(fw):
    card = Card(fw, {f'{STAGING}/foo.list'.encode(): row(5, '.foo/q2-foo'), f'{STAGING}/foo.files'.encode(): b'.foo/q2-foo\n'})
    return card


def test_a_whole_install(fw):
    card = installing(fw)
    r, sh, table = install(fw, card, body=b'the zip')
    assert r == SH_OK and sh.stage == 3 and table[0].latest == b'1.0'
    assert b'/mnt/mmc/.foo/q2-foo' in card.files and b'/mnt/mmc/.q2/apps/foo.files' in card.files
    assert card.of('rename')[-1] == (b'/mnt/mmc/.q2/apps/foo.tag.tmp', b'/mnt/mmc/.q2/apps/foo.tag')
    assert card.written[b'/mnt/mmc/.q2/apps/foo.tag.tmp'] == b'1.0'
    assert card.of('system')[-1] == (b'rm -rf ' + STAGING.encode(),)


def test_the_pins_zip_is_what_is_fetched(fw):
    card = installing(fw)
    net = Net(fw, {b'https://github.com/': (0, 200, None, b'z')})
    assert install(fw, card, net, body=b'z')[0] == SH_OK and net.urls == [ZIP_URL]


@pytest.mark.parametrize('body', [b'the zip!', b'the zi', b'', b'the zip' * 2])
def test_a_zip_that_isnt_the_pins_changes_nothing(fw, body):
    """Another zip at the pinned tag (a release replaced since it was pinned): never listed, unpacked or moved."""
    card = installing(fw)
    r, sh, _ = install(fw, card, Net(fw, {b'https://github.com/': (0, 200, None, body)}), sha=hashlib.sha256(b'the zip').digest())
    assert r == SH_FORGED and sh.stage == 1 and b'/mnt/mmc/.foo/q2-foo' not in card.files and not card.of('rename')
    assert not [c for c in card.of('system') if b'unzip' in c[0]] and card.of('system')[-1] == (b'rm -rf ' + STAGING.encode(),)


def test_a_part_that_cant_be_read_back_is_a_card_error(fw):
    card = installing(fw)
    r, _, _ = install(fw, card, body=b'z', readable=False)
    assert r == SH_CARD and b'/mnt/mmc/.foo/q2-foo' not in card.files


def test_a_cancel_before_moving_leaves_the_app_alone(fw):
    card = installing(fw)
    fw.var('netupdate__sh', Sh).cancel = 1
    assert install(fw, card)[0] == SH_CANCELLED
    assert b'/mnt/mmc/.foo/q2-foo' not in card.files and not card.of('rename')


def test_no_zip_at_the_pinned_tag_stops_it(fw):
    card = installing(fw)
    net = Net(fw, {b'https://github.com/': (0, 404, None)})
    assert install(fw, card, net)[0] == SH_MISSING and b'/mnt/mmc/.foo/q2-foo' not in card.files


def test_a_bad_zip_changes_nothing(fw):
    card = Card(fw, {f'{STAGING}/foo.list'.encode(): row(5, '../x')})
    assert install(fw, card)[0] == SH_BAD
    assert b'/mnt/mmc/.foo/q2-foo' not in card.files


def test_no_room_for_the_zip(fw):
    card = installing(fw)
    r, sh, _ = install(fw, card, size=900 << 20, blocks=1)
    assert r == SH_SPACE and sh.need == 964 and not [e for e in card.log if e[0] == 'rename']


def test_a_failed_download_says_why(fw):
    card = installing(fw)
    net = Net(fw, {b'https://github.com/': (7, 0, None)})
    assert install(fw, card, net)[0] == SH_NET
    net.replies[b'https://github.com/'] = (0, 404, None)
    assert install(fw, card, net)[0] == SH_MISSING


def state(fw, card, latest=b'', i=0):
    (App * SH_MAX).in_dll(fw.lib, 'shelf_apps')[i].latest = latest
    return fw.fn('netupdate__sh_state', C.c_int, C.c_int)(i)


def test_installed_with_a_tag(fw):
    apps(fw, b'me/foo')
    card = Card(fw, {b'/mnt/mmc/.q2/apps/foo.tag': b'2.0'})
    assert state(fw, card) == ST_INSTALLED and state(fw, card, b'2.0') == ST_INSTALLED
    assert state(fw, card, b'2.1') == ST_UPDATE


def test_a_binary_without_a_tag_is_a_manual_install(fw):
    apps(fw, b'me/foo')
    assert state(fw, Card(fw, {b'/mnt/mmc/.foo/q2-foo': b''})) == ST_UPDATE


def test_a_folder_where_the_binary_should_be_is_not_installed(fw):
    apps(fw, b'me/foo')
    card = Card(fw, {b'/mnt/mmc/.foo/q2-foo': b''})
    fw.mock('fw___xstat', lambda v, p, info, *_: C.cast(info, C.POINTER(C.c_uint)).__setitem__(5, 0o040755))
    assert state(fw, card) == ST_INSTALL


def test_nothing_on_the_card(fw):
    apps(fw, b'me/foo', b'x/q2-rockbox-themes')
    card = Card(fw)
    assert state(fw, card) == ST_INSTALL and state(fw, card, i=1) == ST_INSTALL  # the themes have no binary


def test_a_tag_file_too_long_is_held_to_its_buffer(fw):
    apps(fw, b'me/foo')
    assert state(fw, Card(fw, {b'/mnt/mmc/.q2/apps/foo.tag': b'x' * 500}), b'x' * 31) == ST_UPDATE


def test_file_mode_is_its_type_bits(fw):
    mode = fw.fn('netupdate__file_mode', C.c_uint, C.c_char_p)
    Card(fw, {b'/f': b''})
    assert mode(b'/f') == 0o100000 and mode(b'/missing') == 0


class Page:
    """The Apps page: its rows' text and toasts, with no thread run. Row 0 is Check for Updates."""
    def __init__(self, fw, *lines):
        self.fw, self.text, self.toasts = fw, {}, []
        self.table = apps(fw, *lines)
        self.card = Card(fw)
        self.sh = fw.var('netupdate__sh', Sh)
        self.wifi, self.top, self.now = 1, WIN, 3600
        fw.mock('get_wifisignal', lambda *_: self.wifi)
        fw.mock('window_create', lambda *_: WIN)
        fw.mock('window_manager_get_top_window', lambda *_: self.top)
        fw.mock('fw_clock_gettime', lambda which, ts, *_: C.cast(ts, C.POINTER(C.c_long)).__setitem__(0, self.now))
        fw.mock('widget_get_child', lambda row, i, *_: row)
        self.rows = rows(fw)
        self.said: list[str] = []  # every text written, in order
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.said.append(C.string_at(s).decode()) or
                self.text.__setitem__(w, self.said[-1]))
        fw.mock('navigator_to_with_context', lambda path, info, *_: self.toasts.append(C.string_at(info + 8).decode()))
        self.sh.page = WIN
        fw.fn('netupdate__sh_rows', None)()

    def label(self, r):
        return self.text[self.sh.row[r]]

    def click(self, r=1):
        self.fw.fn('netupdate__sh_click', C.c_int, C.c_void_p, C.c_void_p)(r, None)

    def checked(self, *latest, manual=0, result=SH_OK):
        """A check that found these latest tags, one an app, ended: sh_wait's toasts."""
        self.sh.next = C.cast(self.table, C.POINTER(App))
        for i, tag in enumerate(latest):
            self.sh.next[i].latest = tag
        self.sh.n, self.sh.job, self.sh.manual, self.sh.result = self.fw.var('shelf_n', C.c_int).value, JOB_CHECK, manual, result
        self.fw.fn('netupdate__sh_wait', C.c_int, C.c_void_p)(None)
        return self.toasts


def test_rows_say_install_under_check_for_updates(fw):
    p = Page(fw, b'me/foo', b'me/bar')
    assert p.sh.rows == 3 and p.sh.map[0] == ROW_CHECK and p.label(0) == ''
    assert not [a for _, a in fw.calls('widget_resize') if a[0] == p.sh.row[0]]  # over apps, the two-line row
    assert p.label(1) == 'Install' and p.label(2) == 'Install'


def test_a_press_installs_at_once(fw):
    p = Page(fw, b'me/foo')
    p.click()
    assert p.sh.job == JOB_INSTALL and p.sh.app == 0 and p.sh.running == 1 and p.label(1) == 'Checking…'


def test_a_press_without_wifi_says_so(fw):
    p = Page(fw, b'me/foo')
    p.wifi = 0
    p.click()
    assert p.sh.job == 0 and p.toasts == ['Connect to Wi-Fi first']


def test_rockbox_asks_twice_as_it_becomes_the_default(fw):
    p = Page(fw, b'x/q2-rockbox')
    p.click()
    assert p.sh.job == 0 and p.label(1).endswith('Press again to install')
    p.click()
    assert p.sh.job == JOB_INSTALL


def test_an_installed_app_is_removed_on_a_second_press(fw):
    p = Page(fw, b'me/foo')
    p.card.files[b'/mnt/mmc/.q2/apps/foo.tag'] = b'1.0'
    p.click()
    assert p.sh.armed == 2 and p.label(1) == 'Press again to remove'
    p.click()
    assert p.sh.job == JOB_REMOVE and p.sh.stage == 4


def test_a_press_on_the_running_install_cancels_it_until_it_moves(fw):
    p = Page(fw, b'me/foo', b'me/bar')
    p.click(1)
    p.click(2)
    assert p.toasts == ['Wait for the one in progress'] and p.sh.cancel == 0
    p.click(1)
    assert p.sh.cancel == 1 and p.label(1) == 'Canceling…'
    p.sh.cancel, p.sh.stage = 0, 3
    p.click(1)
    assert p.sh.cancel == 0


def test_download_progress_on_the_row(fw):
    p = Page(fw, b'me/foo')
    p.click()
    p.sh.stage, p.sh.percent = 1, 42
    fw.fn('netupdate__sh_refresh', None)()
    assert p.label(1) == 'Downloading 42%'


def test_with_no_list_check_for_updates_loads_it(fw):
    p = Page(fw)
    # the row's detail blank: OLED's note under it says why (feedback_empty)
    assert p.sh.rows == 1 and p.sh.map[0] == ROW_CHECK and p.label(0) == ''
    assert {'No apps listed', 'Connect to Wi-Fi, then Check for Updates'} <= set(p.said)
    # alone, the row is a plain one, the list's pitch, not a two-line row (audit2 J3)
    pitch = fw.fn('page_h', C.c_int, C.c_int)(48)
    assert [a[2] for _, a in fw.calls('widget_resize') if a[0] == p.sh.row[0]][-1:] == [pitch]
    p.click(0)
    assert p.sh.job == JOB_CHECK and p.sh.app == 0 and p.sh.manual == 1 and p.label(0) == 'Checking…'


def test_check_for_updates_checks_inside_the_ten_minutes(fw):
    p = Page(fw, b'me/foo')
    p.sh.checked = (p.now - 120) * 1000
    p.click(0)
    assert p.sh.job == JOB_CHECK and p.sh.manual == 1 and not p.toasts


def test_check_for_updates_within_a_minute_says_the_last_count_again(fw):
    p = Page(fw, b'me/foo')
    p.card.files[b'/mnt/mmc/.q2/apps/foo.tag'] = b'1.0'
    p.table[0].latest = b'2.0'
    p.sh.checked = (p.now - 30) * 1000
    p.click(0)
    assert p.sh.job == 0 and p.toasts == ['1 app can be updated']
    p.table[0].latest = b'1.0'
    p.click(0)
    assert p.sh.job == 0 and p.toasts[-1] == 'All apps are up to date'


def test_check_for_updates_while_checking_or_busy(fw):
    p = Page(fw, b'me/foo')
    p.sh.job = JOB_CHECK
    p.click(0)
    assert p.sh.cancel == 0 and p.sh.manual == 1 and not p.toasts  # checking already: its end answers
    p.sh.job = JOB_INSTALL
    p.click(0)
    assert p.toasts == ['Wait for the one in progress']
    p.sh.job, p.wifi = 0, 0
    p.click(0)
    assert p.sh.job == 0 and p.toasts[-1] == 'Connect to Wi-Fi first'


def test_a_check_says_how_many_apps_have_a_newer_release(fw):
    p = Page(fw, b'me/a', b'me/b', b'me/c', b'me/d')
    for app in b'abc':
        p.card.files[b'/mnt/mmc/.q2/apps/%c.tag' % app] = b'1.0'
    p.card.files[b'/mnt/mmc/.d/q2-d'] = b''  # a manual install: Update, not counted
    assert p.checked(b'2.0', b'2.0', b'2.0') == ['3 apps can be updated']
    assert p.label(4) == 'Update'
    p.toasts.clear()
    assert p.checked(b'2.0', b'1.0', b'1.0') == ['1 app can be updated']


def test_none_to_update_is_said_only_after_check_for_updates(fw):
    p = Page(fw, b'me/foo')
    assert p.checked() == []
    assert p.checked(manual=1) == ['All apps are up to date']


def test_a_failed_check_is_said_only_after_check_for_updates(fw):
    p = Page(fw, b'me/foo')
    assert p.checked(result=SH_BUSY) == []
    assert p.checked(result=SH_BUSY, manual=1) == ['GitHub is busy, try later']
    p.toasts.clear()
    assert p.checked(result=SH_NET, manual=1) == ["Can't reach GitHub"]
    p.card.files[b'/mnt/mmc/.q2/apps/foo.tag'] = b'1.0'
    p.toasts.clear()
    assert p.checked(b'2.0', result=SH_BUSY, manual=1) == ['1 app can be updated']  # what it found first


def test_no_toast_when_the_page_is_left_covered_or_the_check_stopped(fw):
    p = Page(fw, b'me/foo')
    p.top = OTHER
    assert p.checked(manual=1) == []
    p.top, p.sh.cancel = WIN, 1
    assert p.checked(manual=1) == []
    p.sh.cancel = 0
    p.fw.fn('netupdate__sh_closed', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert p.checked(manual=1) == []


def test_the_next_check_isnt_check_for_updates_unless_pressed_again(fw):
    p = Page(fw, b'me/foo')
    p.click(0)
    p.sh.running = 0
    assert p.checked(manual=p.sh.manual) == ['All apps are up to date']
    p.fw.fn('netupdate__sh_start', None, C.c_int, C.c_int)(JOB_CHECK, 0)  # the opening check
    assert p.sh.manual == 0


def test_rockbox_themes_show_only_with_rockbox(fw):
    p = Page(fw, b'x/q2-rockbox', b'x/q2-rockbox-themes')
    assert p.sh.rows == 2
    p.card.files[b'/mnt/mmc/.rockbox/rockbox'] = b''
    fw.fn('netupdate__sh_rows', None)()
    assert p.sh.rows == 3


def test_a_press_while_checking_stops_the_check(fw):
    p = Page(fw, b'me/foo')
    p.sh.job = JOB_CHECK
    p.click(1)
    assert p.sh.cancel == 1 and p.toasts == ['Stopped checking for updates']


def test_erase_runs_unless_a_job_does(fw):
    p = Page(fw, b'me/foo')
    erase = fw.fn('shelf_erase', C.c_int, C.c_int)
    assert erase(0) == 0
    assert erase(1) == 1 and p.sh.job == JOB_ERASE
    p.sh.job = JOB_INSTALL
    assert erase(1) == 0 and p.toasts == ['Wait for the one in progress']


def check(fw):
    return fw.fn('netupdate__check', C.c_int)()


def checking(fw, card, text):
    """A check whose list fetch answers text; the card's list it writes."""
    Net(fw, {b'https://raw': (0, 200, None)})
    card.files[JSON_PATH] = text
    return check(fw)


def test_the_check_takes_a_fresh_list_alone(fw):
    """One request: each app's latest is its pin's tag, no release is asked."""
    card = Card(fw)
    net = Net(fw, {b'https://raw': (0, 200, None)})
    card.files[JSON_PATH] = listing(b'me/foo Foo\nme/bar\n', 3, tag=b'2.0')
    sh = fw.var('netupdate__sh', Sh)
    assert check(fw) == 1 and net.urls == [b'https://raw.githubusercontent.com/owner/repo/main/sources.list']
    assert sh.n == 2 and sh.next[0].id == b'foo' and sh.next[0].latest == b'2.0' and bytes(sh.next[1].sha256).hex().encode() == PIN_SHA
    assert b'/mnt/mmc/.q2/sources.list' in [c[1] for c in card.of('rename')]
    assert card.written[b'/mnt/mmc/.q2/sources.list.tmp'] == b'serial 3\n' + pinned(b'me/foo Foo\nme/bar bar\n', tag=b'2.0')


def test_the_cards_list_reads_back_as_the_check_left_it(fw):
    card = Card(fw)
    checking(fw, card, listing(b'me/foo Foo\n-me/old Old\n', 2))
    sh = fw.var('netupdate__sh', Sh)
    again = sources(fw, card.written[b'/mnt/mmc/.q2/sources.list.tmp'], pin=False, raw=True)
    assert [(a.repo, a.latest, a.asset, a.size, bytes(a.sha256), a.dropped) for a in again] == \
        [(x.repo, x.latest, x.asset, x.size, bytes(x.sha256), x.dropped) for x in sh.next[:sh.n]]


@pytest.mark.parametrize('result,code,why', [(7, 0, SH_NET), (0, 403, SH_BUSY), (0, 429, SH_BUSY), (0, 404, SH_NET)])
def test_a_list_that_cant_be_fetched_keeps_the_page_s_and_says_why(fw, result, code, why):
    Card(fw)
    apps(fw, b'me/foo', b'me/bar')
    sh = fw.var('netupdate__sh', Sh)
    Net(fw, {b'https://raw': (result, code, None)})
    assert check(fw) == 0 and sh.result == why and [sh.next[i].id for i in range(sh.n)] == [b'foo', b'bar']


def test_an_unsigned_list_is_never_taken(fw):
    """q2-pod's own list, or a list a taken-over repository rewrote: the page keeps the list it had."""
    card = Card(fw)
    apps(fw, b'me/kept')
    sh = fw.var('netupdate__sh', Sh)
    text = listing(b'me/evil Evil\n')
    assert checking(fw, card, text[:text.index(b'signature ')]) == 0 and sh.result == SH_UNSIGNED
    assert sh.n == 1 and sh.next[0].id == b'kept' and b'/mnt/mmc/.q2/sources.list.tmp' not in card.written


def test_an_older_signed_list_is_never_taken(fw):
    card = Card(fw, {SOURCES: b'serial 9\n' + pinned(b'me/kept\n')})
    apps(fw, b'me/kept')
    sh = fw.var('netupdate__sh', Sh)
    assert checking(fw, card, listing(b'me/old\n', 8)) == 0 and sh.result == SH_UNSIGNED and sh.next[0].id == b'kept'


def test_no_list_anywhere_is_a_failed_check(fw):
    Card(fw)
    fw.var('shelf_n', C.c_int).value = 0
    sh = fw.var('netupdate__sh', Sh)
    Net(fw, {b'https://raw': (7, 0, None)})
    assert check(fw) == 0 and sh.result == SH_NET and sh.n == 0


def test_a_bad_list_never_replaces_the_one_there(fw):
    card = Card(fw)
    apps(fw, b'me/kept')
    sh = fw.var('netupdate__sh', Sh)
    checking(fw, card, sign_list(b'serial 1\n# nothing\nme/unpinned Foo\n'))
    assert sh.n == 1 and sh.next[0].id == b'kept' and b'/mnt/mmc/.q2/sources.list.tmp' not in card.written


def test_an_app_dropped_from_the_list_but_installed_stays_removable(fw):
    card = Card(fw, {b'/mnt/mmc/.q2/apps/old.tag': b'1'})
    apps(fw, b'me/old', b'me/gone')
    checking(fw, card, listing(b'me/new\n'))
    sh = fw.var('netupdate__sh', Sh)
    assert [sh.next[i].id for i in range(sh.n)] == [b'new', b'old']


def test_a_dropped_app_keeps_no_pin_and_stays_dropped_on_the_card(fw):
    """Dropping an app from the list is what stops a taken-over repo's releases (GHSA-8wpr-pmqx-538x): its pin and
    an earlier check's tag are let go, and the card's list marks it "-" so a later offline page knows."""
    card = Card(fw, {b'/mnt/mmc/.q2/apps/old.tag': b'1'})
    table = apps(fw, b'me/old')
    table[0].latest = b'2.0'
    checking(fw, card, listing(b'me/new\n'))
    sh = fw.var('netupdate__sh', Sh)
    assert sh.next[1].dropped == b'\x01' and sh.next[1].latest == b''
    assert card.written[b'/mnt/mmc/.q2/sources.list.tmp'] == b'serial 1\n' + pinned(b'me/new new\n') + b'-me/old old\n'
    reloaded = sources(fw, card.written[b'/mnt/mmc/.q2/sources.list.tmp'], pin=False, raw=True)
    assert [(a.id, a.dropped) for a in reloaded[:2]] == [(b'new', b'\x00'), (b'old', b'\x01')]


def test_a_dropped_app_never_installs_and_shows_only_while_installed(fw):
    card = installing(fw)
    net = Net(fw, {b'https://github.com/': (0, 200, None)})
    apps(fw, b'-me/foo')
    pin = App()
    assert fw.fn('netupdate__install', C.c_int, C.c_int, C.c_void_p)(0, C.byref(pin)) == SH_MISSING and not net.urls
    p = Page(fw, b'me/bar', b'-me/foo')
    assert p.sh.rows == 2  # Check for Updates and bar: foo isn't installed
    p.card.files[b'/mnt/mmc/.q2/apps/foo.tag'] = b'1.0'
    fw.fn('netupdate__sh_rows', None)()
    assert p.sh.rows == 3 and p.label(2) == 'Installed'


def test_a_full_list_keeps_the_cards_list_inside_its_buffer(fw):
    card = Card(fw)
    lines = b''.join(b'%s/%s%02d %s\n' % (b'o' * 39, b'a' * 13, i, b'n' * 31) for i in range(SH_MAX))
    pins = dict(tag=b't' * 31, asset=b'f' * 59 + b'.zip', size=1 << 30)
    checking(fw, card, listing(lines, 123456789, **pins))
    assert card.written[b'/mnt/mmc/.q2/sources.list.tmp'] == b'serial 123456789\n' + pinned(lines, **pins)


def test_the_page_opens_on_the_cards_last_list(fw):
    card = Card(fw, {b'/mnt/mmc/.q2/sources.list': b'serial 2\n' + pinned(b'me/foo Foo\n', tag=b'3.0')})
    fw.mock('window_create', lambda *_: WIN)
    fw.mock('get_wifisignal', lambda *_: 0)
    fw.fn('shelf_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    table = (App * SH_MAX).in_dll(fw.lib, 'shelf_apps')
    assert fw.var('shelf_n', C.c_int).value == 1 and table[0].id == b'foo' and table[0].latest == b'3.0'


def test_the_page_opens_with_wifi_and_checks(fw):
    Card(fw)
    fw.mock('window_create', lambda *_: WIN)
    fw.mock('get_wifisignal', lambda *_: 1)
    fw.fn('shelf_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert fw.var('netupdate__sh', Sh).job == JOB_CHECK


def test_a_hostile_list_on_the_card_opens_the_page_safely(fw):
    for raw in hostile(34, [pinned(b'me/foo Foo\nme/q2-bar\n')], 300, b'/ \n\xff0a'):
        fw.var('shelf_n', C.c_int).value = 0
        fw.var('netupdate__sh', Sh).page = None
        Card(fw, {b'/mnt/mmc/.q2/sources.list': raw})
        fw.mock('window_create', lambda *_: WIN)
        fw.mock('get_wifisignal', lambda *_: 0)
        fw.fn('shelf_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)


# End to end: a press to the toast, the card's files after.

EVT_CLICK, EVT_DESTROY = O['EVT_CLICK'], O['EVT_DESTROY']
RET_REPEAT = 8
TAR = b'/mnt/mmc/update.tar'
TAG_URL = b'https://github.com/owner/repo/releases/tag/'
BODY = bytes(range(256)) * 1024  # 256 KB: CHUNKS pieces of 64 KB, KB-exact so progress reads 25/50/75/100
SOURCES = b'/mnt/mmc/.q2/sources.list'
MOUNTS = b'/proc/mounts'


class Disk(Card):
    """Card, with what a whole job needs: a file written whole at its fclose, folders that open (the card's root, for
    its fsync) and list (addon.c's scan), each fsync logged by its file's name, and busybox's commands run on the
    files: unzip's listing and unpacking of the downloaded zip (fnmatch without FNM_PATHNAME, as busybox's), find
    and rm -rf. The rest (kill, sync) is only logged."""
    def __init__(self, fw, files=None):
        super().__init__(fw, files)
        self.writing, self.listing, self.unpack_fails = set(), {}, 0
        self.system = self.shell
        fw.mock('fw_fopen', self.fopen)
        fw.mock('fw_fclose', self.fclose)
        fw.mock('fw_fileno', lambda f, *_: f + 100)
        fw.mock('fw_fsync', lambda fd, *_: self.log.append(('fsync', self.names[fd - 100])))
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)

    def folder(self, name):
        return any(f.startswith(name + b'/') for f in self.files)

    def fopen(self, path, mode, *_):
        name, how = C.string_at(path), C.string_at(mode)
        if b'r' in how and name not in self.files and not self.folder(name):
            return 0
        f = len(self.names) + 1
        self.names[f], self.at[f] = name, 0
        if b'w' in how:
            self.written[name] = b''
            self.writing.add(f)
        return f

    def fclose(self, f, *_):
        if f in self.writing:
            self.writing.discard(f)
            self.files[self.names[f]] = self.written[self.names[f]]
        return 0

    def opendir(self, path, *_):
        base = C.string_at(path) + b'/'
        names = sorted({f[len(base):].split(b'/')[0] for f in self.files if f.startswith(base)})
        self.listing[len(self.listing) + 0x3000] = iter([(n, 4 if self.folder(base + n) else 8) for n in names])
        return len(self.listing) + 0x2fff

    def readdir(self, d, *_):
        class Dirent(C.Structure):  # host.py's struct dirent
            _fields_ = [('ino', C.c_uint), ('off', C.c_uint), ('reclen', C.c_ushort), ('d_type', C.c_ubyte), ('d_name', C.c_char * 256)]
        for name, kind in self.listing[d]:
            self.entry = Dirent(0, 0, 0, kind, name)
            return C.addressof(self.entry)
        return 0

    def shell(self, cmd):
        line = cmd.decode()
        if line.startswith('busybox unzip '):
            return self.unzip(shlex.split(line[len('busybox unzip '):]))
        found = re.fullmatch(r'cd (\S+) && find (\S+) -type f > (\S+)', line)
        if found:
            top, folder, out = (g.encode() for g in found.groups())
            names = sorted(f[len(top) + 1:] for f in self.files if f.startswith(top + b'/' + folder + b'/'))
            self.files[out] = b''.join(n + b'\n' for n in names)
        elif line.startswith('rm -rf '):
            for path in shlex.split(line[len('rm -rf '):]):
                for f in [f for f in self.files if f == path.encode() or f.startswith(path.encode() + b'/')]:
                    del self.files[f]
        return 0

    def unzip(self, args):
        out = args[args.index('>') + 1].encode() if '>' in args else None
        args = args[:args.index('>')] if out else args
        part = next(a for a in args if a.endswith('.part'))
        patterns = args[args.index(part) + 1:]
        want, skip = patterns[:patterns.index('-x')], patterns[patterns.index('-x') + 1:]
        with zipfile.ZipFile(io.BytesIO(self.files[part.encode()])) as z:
            entries = [e for e in z.infolist() if any(fnmatch.fnmatchcase(e.filename, p) for p in want)
                       and not any(fnmatch.fnmatchcase(e.filename, p) for p in skip)]
            if '-l' in args:
                self.files[out] = b''.join(row(e.file_size, e.filename) for e in entries)
                return 0
            folder = args[args.index('-d') + 1]
            for e in entries:
                self.files[f'{folder}/{e.filename}'.encode()] = z.read(e)
        return self.unpack_fails

    def moves(self, into):
        return [e[2] for e in self.log if e[0] == 'rename' and e[2].startswith(into)]

    def commands(self):
        return [e[1] for e in self.log if e[0] == 'system']


class Download:
    """Software Update's download (sw_update.c drives it) through netupdate_start, netupdate_poll and
    netupdate_cancel: the worker run by hand, its endings in netupdate_said's words."""
    def __init__(self, fw, thread=0):
        self.fw, self.workers = fw, []
        fw.mock('fw_pthread_create', lambda t, a, fn, arg, *_: thread or self.workers.append(fn) or 0)
        self.nu = fw.var('netupdate__nu', Nu)

    def start(self):
        return self.fw.fn('netupdate_start', C.c_int)()

    def poll(self):
        percent, verifying = C.c_int(-1), C.c_int(-1)
        state = self.fw.fn('netupdate_poll', C.c_int, C.c_void_p, C.c_void_p)(C.byref(percent), C.byref(verifying))
        return state, percent.value, verifying.value

    def cancel(self):
        self.fw.fn('netupdate_cancel', None)()

    def run(self):
        assert C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.workers.pop())(None) is None

    def said(self, state):
        return C.string_at(self.fw.fn('netupdate_said', C.c_void_p, C.c_int)(state)).decode()


def test_a_start_downloads_a_newer_release_onto_the_card(fw):
    card = Disk(fw, {TAR: b'old'})
    dl = Download(fw)
    assert dl.start() == 1 and dl.poll() == (NU_RUNNING, 0, 0)
    seen = []
    Net(fw, rel((0, 200, None, BODY)), during=lambda k: seen.append(dl.poll()))
    dl.run()
    assert seen == [(NU_RUNNING, p, 0) for p in (25, 50, 75, 100)]
    assert {args[:3] for _, args in fw.calls('reset_poweroptions_timer')} == {(1, 1, 0)}  # standby held, not the screen
    assert card.files[TAR] == BODY and PART not in card.files
    # the part on the card before it replaces the old tar, then the rename's own entry before stock flashes it
    assert [e for e in card.log if e[0] in ('rename', 'fsync')] == [('fsync', PART), ('rename', PART, TAR), ('fsync', b'/mnt/mmc')]
    assert dl.poll()[0] == NU_DONE and len(fw.calls('fw_pthread_join')) == 1 and dl.poll()[0] == NU_IDLE


@pytest.mark.parametrize('at', [1, CHUNKS - 1])  # mid-download, and after the last byte, with no progress call left
def test_a_cancel_keeps_the_old_tar(fw, at):
    card = Disk(fw, {TAR: b'old'})
    dl = Download(fw)
    dl.start()
    Net(fw, rel((0, 200, None, BODY)), during=lambda k: k == at and dl.cancel())
    dl.run()
    assert dl.poll()[0] == NU_CANCELLED and dl.said(NU_CANCELLED) == 'Download canceled'
    assert card.files[TAR] == b'old' and PART not in card.files and not card.of('rename')


def test_a_part_the_card_can_t_sync_never_replaces_the_tar(fw):
    card = Disk(fw, {TAR: b'old'})
    fw.mock('fw_fsync', lambda fd, *_: -1 if card.names[fd - 100] == PART else 0)
    dl = Download(fw)
    dl.start()
    Net(fw, rel((0, 200, None, BODY)))
    dl.run()
    assert dl.poll()[0] == NU_CARD and dl.said(NU_CARD) == "Can't write to the card"
    assert card.files[TAR] == b'old' and PART not in card.files and not card.of('rename')


@pytest.mark.parametrize('replies, said', [
    (lambda: {LATEST: (0, 302, TAG_URL + TAG)}, 'Latest firmware installed'),
    (lambda: {LATEST: (6, 0, None)}, "Can't reach GitHub"),  # CURLE_COULDNT_RESOLVE_HOST
    (lambda: {LATEST: (0, 200, None)}, "Can't reach GitHub"),  # no redirect
    (lambda: rel((0, 404, None, b'Not Found')), 'No update for this build'),
    (lambda: rel((28, 200, None, BODY)), "Can't reach GitHub"),  # timed out
    (lambda: rel((0, 200, None, BODY), sig=(0, 404, None, b'Not Found')), 'Release not signed: not installed'),
    (lambda: rel((0, 200, None, BODY), sig=(0, 200, None, signed(BODY[:-1]))), 'Signature check failed: not installed'),
])
def test_each_ending_says_why_and_keeps_the_old_tar(fw, replies, said):
    card = Disk(fw, {TAR: b'old'})
    dl = Download(fw)
    dl.start()
    Net(fw, replies())
    dl.run()
    assert dl.said(dl.poll()[0]) == said
    assert card.files[TAR] == b'old' and PART not in card.files


def test_a_full_card_fails_the_write_and_says_so(fw):
    card = Disk(fw, {TAR: b'old'})

    def fwrite(buf, size, n, f, *_):  # the card full; the signature in /tmp, RAM, still written
        if card.names[f] == PART:
            return 0
        card.written[card.names[f]] += C.string_at(buf, size * n)
        return n
    fw.mock('fw_fwrite', fwrite)
    fw.mock('fw_ferror', lambda f, *_: card.names[f] == PART)
    dl = Download(fw)
    dl.start()
    net = Net(fw, rel((0, 200, None, BODY)))
    dl.run()
    assert net.urls == [LATEST, SIG, ASSET] and dl.poll()[0] == NU_CARD
    assert card.files[TAR] == b'old' and PART not in card.files


def test_one_download_at_a_time_and_none_without_a_thread(fw):
    dl = Download(fw)
    assert dl.start() == 1 and dl.start() == 0 and len(dl.workers) == 1
    dl = Download(fw, thread=11)
    dl.nu.state = NU_IDLE
    assert dl.start() == 0 and dl.nu.state == NU_IDLE


def test_a_new_start_forgets_the_last_download(fw):
    dl = Download(fw)
    dl.nu.verifying, dl.nu.percent, dl.nu.cancel = 1, 80, 1
    dl.start()
    assert (dl.nu.verifying, dl.nu.percent, dl.nu.cancel) == (0, 0, 0)


def test_the_poll_says_verifying_while_the_signature_is_checked(fw):
    dl = Download(fw)
    dl.nu.state, dl.nu.percent, dl.nu.verifying = NU_RUNNING, 100, 1
    assert dl.poll() == (NU_RUNNING, 100, 1) and not fw.calls('fw_pthread_join')


def stock_page(fw, children=2, win=0x7000):
    """Stock's System Update page as stock lays it out (stand-in handles), hooked by ringnav_fwupdate: the names and
    click handlers it gave."""
    VIEW, ROW, BUTTON = 0x7100, 0x7200, 0x7300
    names, handlers = {}, []
    tree = {(VIEW, 1): ROW, (ROW, 0): BUTTON}
    fw.mock('stock_fwupdate_trampoline', lambda *_: 7)
    fw.mock('widget_lookup', lambda w, name, *_: VIEW if C.string_at(name) == b'scroll_view_fwupdate' else 0)
    fw.mock('widget_count_children', lambda w, *_: children if w == VIEW else 0)
    fw.mock('widget_get_child', lambda w, i, *_: tree.get((w, i), 0))
    fw.mock('widget_set_name', lambda w, s, *_: names.__setitem__(w, C.string_at(s)))
    fw.mock('widget_on', lambda w, evt, fn, ctx, *_: handlers.append((w, evt, fn)))
    result = fw.fn('ringnav_fwupdate', C.c_int, C.c_void_p, C.c_void_p)(win, 0x7001)
    return result, names, handlers, BUTTON


def test_stock_s_second_row_opens_software_update(fw):
    result, names, handlers, button = stock_page(fw)
    opener = C.cast(fw.lib.sw_update_open, C.c_void_p).value
    assert result == 7 and names == {button: b'2'} and handlers == [(button, EVT_CLICK, opener)]


@pytest.mark.parametrize('children, win', [(1, 0x7000), (3, 0x7000), (2, 0)])
def test_a_page_laid_out_otherwise_stays_stock_s(fw, children, win):
    result, names, handlers, _ = stock_page(fw, children, win)
    assert result == 7 and names == {} and handlers == []


# The App Store.

def app_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


FOO = {'.foo/q2-foo': b'\x7fELF foo', '.foo/q2app': b'name=Foo\n', '.foo/lib/x.so': b'lib', '.foo/._q2app': b'macOS',
       'INSTALL.txt': b'read me', '__MACOSX/.foo/._q2-foo': b'macOS'}


def store_net(fw, files=FOO, listed=b'me/q2-foo Foo\n', size=None, **replies):
    """The signed list, each app pinned to tag 2.0's app.zip, the zip of files (size its pinned bytes, the zip's own
    unless given), and the zip; list= or zip= replace those replies. net.card_list is the list a check leaves on the
    card."""
    data = app_zip(files)
    pin = dict(tag=b'2.0', asset=b'app.zip', size=size or len(data), sha=hashlib.sha256(data).hexdigest().encode())
    net = Net(fw, {b'https://raw': replies.get('list', (0, 200, None, listing(listed, **pin))),
                   b'https://github.com/': replies.get('zip', (0, 200, None, data))})
    net.card_list = b'serial 1\n' + pinned(listed, **pin)
    return net


def on_card(text):
    """The card's list as an earlier check left it: each app pinned to tag 2.0."""
    return b'serial 1\n' + pinned(text, tag=b'2.0')


class Store:
    """The App Store opened for real on a Disk, on top while open: each row's detail, the toasts, the wait timer, the
    worker, the standby holds, the clock (now_ms), the card's free bytes (statfs) and the card mounted (/proc/mounts,
    card_mounted). Row 0 is Check for Updates, the list's apps under it."""
    def __init__(self, fw, card, wifi=1, thread=0, free=1 << 30):
        self.fw, self.card, self.wifi, self.free, self.seconds = fw, card, wifi, free, 5000
        self.text, self.toasts, self.timers, self.workers, self.held = {}, [], [], [], []
        fw.mock('get_wifisignal', lambda *_: self.wifi)
        fw.mock('window_create', lambda *_: WIN)
        fw.mock('window_manager_get_top_window', lambda *_: WIN)
        fw.mock('widget_get_child', lambda row, i, *_: row)
        self.rows = rows(fw)
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.text.__setitem__(w, C.string_at(s).decode()))
        fw.mock('navigator_to_with_context', lambda path, info, *_: self.toasts.append(C.string_at(info + 8).decode()))
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append(fn) or len(self.timers))
        fw.mock('fw_pthread_create', lambda t, a, fn, arg, *_: thread or self.workers.append(fn) or 0)
        fw.mock('reset_poweroptions_timer', lambda *args: self.held.append(args[:3]))
        fw.mock('fw_clock_gettime', self.clock)
        fw.mock('fw_statfs', self.statfs)
        card.files[MOUNTS] = b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n'
        self.sh = fw.var('netupdate__sh', Sh)
        self.open()

    def clock(self, kind, ts, *_):
        C.c_long.from_address(ts).value, C.c_long.from_address(ts + 8).value = self.seconds, 0

    def statfs(self, path, buf, *_):
        words = C.cast(buf, C.POINTER(C.c_uint))
        words[1], words[7] = 4096, self.free // 4096

    def open(self):
        self.fw.fn('shelf_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)

    def close(self):
        self.fw.fn('netupdate__sh_closed', C.c_int, C.c_void_p, C.c_void_p)(None, None)

    def wait(self):
        """sh_wait, as its timer runs it."""
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(self.timers[-1])(None)

    def run(self):
        """The job's worker to its end, then the wait that joins it."""
        assert C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.workers.pop())(None) is None
        return self.wait()

    def click(self, r=1):
        self.fw.fn('netupdate__sh_click', C.c_int, C.c_void_p, C.c_void_p)(r, None)

    def check_label(self):
        return self.text.get(self.sh.row[0])

    def labels(self):
        """The apps' rows, under Check for Updates."""
        assert self.sh.map[0] == ROW_CHECK
        return [self.text.get(self.sh.row[r]) for r in range(1, self.sh.rows)]


def test_the_store_loads_its_list_then_not_again_for_ten_minutes(fw):
    card = Disk(fw)
    net = store_net(fw)
    s = Store(fw, card)
    assert s.sh.job == JOB_CHECK and s.labels() == [] and s.check_label() == 'Checking…'
    s.click(0)  # Check for Updates while the list loads: no second check, the one running answers it
    assert s.toasts == [] and s.sh.cancel == 0 and s.sh.manual == 1 and len(s.workers) == 1
    assert s.wait() == RET_REPEAT and s.held == []  # a check holds no standby
    assert s.run() == 0 and s.sh.job == 0 and s.toasts == ['All apps are up to date']  # none installed has a newer one
    assert s.labels() == ['Install'] and card.files[SOURCES] == net.card_list and s.sh.next[0].latest == b'2.0'
    assert s.check_label().strip() == ''  # its detail blank again, the list loaded
    assert net.urls == [LIST_URL]  # one request: the pins are the releases
    s.close()
    s.seconds += 600
    s.open()
    assert not s.workers and s.sh.job == 0  # checked ten minutes ago: not yet
    s.close()
    s.seconds += 1
    s.open()
    assert s.sh.job == JOB_CHECK and len(s.workers) == 1


def test_a_check_stopped_part_way_is_done_again_at_the_next_open(fw):
    card = Disk(fw, {SOURCES: on_card(b'me/q2-foo Foo\n')})
    net = store_net(fw)
    s = Store(fw, card)
    s.click()  # an app's row
    assert s.sh.cancel == 1 and s.toasts == ['Stopped checking for updates']
    assert s.run() == 0 and net.urls[-1].startswith(b'https://raw')  # the list, then no release
    assert s.toasts == ['Stopped checking for updates']  # and nothing said at its end
    s.close()
    s.seconds += 1
    s.open()
    assert s.sh.job == JOB_CHECK


# An install goes by the list as it is now, fetched as the install starts. The page's rows may be the card's
# list of days ago, and a repo dropped since then must not install (GHSA-8wpr-pmqx-538x): on that list its
# release was fetched and unpacked onto the card in each of the three ways below.
LIST_URL = b'https://raw.githubusercontent.com/owner/repo/main/sources.list'  # host.py's --update-repo
FOO_APP = b'/mnt/mmc/.foo/q2-foo'


def test_an_app_dropped_since_the_cards_list_is_not_installed_with_no_check_run(fw):
    """Opened with no Wi-Fi: no check runs and the rows are the card's list. The link comes up and the row is pressed."""
    card = Disk(fw, {SOURCES: on_card(b'me/q2-foo Foo\n')})
    net = store_net(fw, listed=b'me/q2-bar Bar\n')
    s = Store(fw, card, wifi=0)
    assert s.sh.job == 0 and s.labels() == ['Install']
    s.wifi = 1
    s.click()
    assert s.sh.job == JOB_INSTALL and s.check_label().strip() == '' and s.labels() == ['Checking…']
    assert s.run() == 0 and s.toasts == ['No update for this build']
    assert net.urls == [LIST_URL] and FOO_APP not in card.files and not card.of('mkdir')


def test_an_app_dropped_since_the_cards_list_is_not_installed_after_the_check_is_stopped(fw):
    """A row pressed while the opening check loads the list stops the check; the next press acts on the card's list."""
    card = Disk(fw, {SOURCES: on_card(b'me/q2-foo Foo\n')})
    net = store_net(fw, listed=b'me/q2-bar Bar\n')
    s = Store(fw, card)
    s.click()
    assert s.run() == 0 and s.labels() == ['Install']
    s.click()
    assert s.sh.job == JOB_INSTALL and s.run() == 0 and s.toasts[-1] == 'No update for this build'
    assert not [u for u in net.urls if not u.startswith(b'https://raw')] and FOO_APP not in card.files


def test_no_app_installs_while_the_list_cant_be_fetched(fw):
    """The list's host out of reach: the check keeps the card's list, which no install takes its pin from."""
    card = Disk(fw, {SOURCES: on_card(b'me/q2-foo Foo\n')})
    net = store_net(fw)
    net.replies[b'https://raw'] = (7, 0, None)
    s = Store(fw, card)
    assert s.run() == 0 and s.labels() == ['Install'] and s.sh.next[0].latest == b'2.0'
    s.click()
    assert s.run() == 0 and s.toasts[-1] == "Can't reach GitHub"
    assert not [u for u in net.urls if u.startswith(b'https://github.com/')] and FOO_APP not in card.files


@pytest.mark.parametrize('listed', [b'other/q2-foo Foo\n', b'-me/q2-foo Foo\n', b'# me/q2-foo Foo\nme/q2-bar Bar\n'])
def test_an_app_installs_only_from_the_repo_the_list_names_now(fw, listed):
    """Another owner's repo under the same name, the app marked dropped, and its line commented out."""
    card = Disk(fw, {SOURCES: on_card(b'me/q2-foo Foo\n')})
    net = store_net(fw, listed=listed)
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    s.click()
    assert s.run() == 0 and s.toasts == ['No update for this build']
    assert net.urls == [LIST_URL] and FOO_APP not in card.files


def test_an_install_asks_for_the_list_then_its_pinned_zip(fw):
    card = Disk(fw, {SOURCES: on_card(b'me/q2-foo Foo\n')})
    net = store_net(fw, listed=b'# apps\nme/q2-bar Bar\nme/q2-foo Foo renamed\n')
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    s.click()
    assert s.run() == 0 and s.toasts == ['Foo is ready in Streaming'] and card.files[FOO_APP] == FOO['.foo/q2-foo']
    assert net.urls == [LIST_URL, b'https://github.com/me/q2-foo/releases/download/2.0/app.zip']
    assert card.files[SOURCES] == on_card(b'me/q2-foo Foo\n') and s.labels() == ['Installed']  # the page's list is the check's to change


def test_an_install_cancelled_while_it_asks_for_the_list_installs_nothing(fw):
    card = Disk(fw, {SOURCES: on_card(b'me/q2-foo Foo\n')})
    net = store_net(fw)
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    s.click()
    s.click()  # the same row again, before the list is in
    assert s.sh.cancel == 1 and s.run() == 0 and s.toasts == ['Download canceled']
    assert net.urls == [LIST_URL] and FOO_APP not in card.files


@pytest.mark.parametrize('q2app, where', [(b'name=Foo\n', 'Streaming'), (b'name=Foo\ngame=1\n', "Library's Games")])
def test_an_app_installs_into_place_and_removes_without_a_trace(fw, q2app, where):
    card = Disk(fw)
    store_net(fw, {**FOO, '.foo/q2app': q2app})
    s = Store(fw, card)
    s.run()

    def on_card():  # addon.c's scan writes its /tmp caps file: only the card counts
        return {k: v for k, v in card.files.items() if k.startswith(b'/mnt/mmc/')}
    before = on_card()
    s.click()
    assert s.sh.job == JOB_INSTALL and s.labels() == ['Checking…']
    assert s.wait() == RET_REPEAT and s.held == [(1, 1, 0)]  # standby and power-off held through a job
    assert s.run() == 0 and s.toasts == [f'Foo is ready in {where}'] and s.labels() == ['Installed']
    placed = {k: v for k, v in card.files.items() if k.startswith(b'/mnt/mmc/.foo/')}
    assert placed == {b'/mnt/mmc/.foo/q2-foo': b'\x7fELF foo', b'/mnt/mmc/.foo/q2app': q2app, b'/mnt/mmc/.foo/lib/x.so': b'lib'}
    assert card.files[b'/mnt/mmc/.q2/apps/foo.files'] == b''.join(sorted(k[len(b'/mnt/mmc/'):] + b'\n' for k in placed))
    assert card.files[b'/mnt/mmc/.q2/apps/foo.tag'] == b'2.0'
    assert card.moves(b'/mnt/mmc/.foo/')[-1] == b'/mnt/mmc/.foo/q2-foo'  # the binary last
    assert not [k for k in card.files if b'staging' in k or b'INSTALL' in k or b'MACOSX' in k]
    s.click()
    assert s.labels() == ['Press again to remove'] and not s.workers
    s.click()
    assert s.labels() == ['Removing…']
    assert s.run() == 0 and s.toasts[-1] == 'Foo removed' and s.labels() == ['Install']
    assert on_card() == before


# A remove leaves no login behind, even from an app that saves it on its TERM, after 3 s too.
@pytest.mark.parametrize('save_ms', [1000, 3500])
def test_a_remove_leaves_nothing_an_app_saves_on_its_way_out(fw, save_ms):
    card = Disk(fw)
    store_net(fw)
    s = Store(fw, card)
    s.run()
    s.click()
    assert s.run() == 0 and s.toasts == ['Foo is ready in Streaming']
    card.files[b'/tmp/q2-foo'] = b'4321'  # its loop's pid: running
    now, term = [s.seconds * 1000], []

    def app_saves():  # its TERM handler: the login written back, then the app and its loop gone
        if term and now[0] - term[0] >= save_ms and b'/tmp/q2-foo' in card.files:
            card.files[b'/mnt/mmc/.foo/login'] = b'token'
            del card.files[b'/tmp/q2-foo']

    def clock(kind, ts, *_):
        C.c_long.from_address(ts).value, C.c_long.from_address(ts + 8).value = now[0] // 1000, now[0] % 1000 * 1000000

    def usleep(us, *_):
        now[0] += us // 1000
        app_saves()

    def system(cmd):
        if cmd.startswith(b'kill $(cat /tmp/q2-foo)'):
            term.append(now[0])
        return card.shell(cmd)
    card.system = system
    fw.mock('fw_clock_gettime', clock)
    fw.mock('fw_usleep', usleep)
    s.click()
    s.click()
    assert s.labels() == ['Removing…']
    assert s.run() == 0 and s.toasts[-1] == 'Foo removed' and term
    now[0] = term[0] + 60000  # the app's own time runs on after the remove
    app_saves()
    assert not [k for k in card.files if k.startswith(b'/mnt/mmc/.foo')]


# USB storage started during a remove's wait for the app: the card is gone, so nothing is removed and it says so, not
# "Foo removed" with the app still there to start again once the card is back.
def test_a_remove_cut_off_by_usb_storage_says_the_card_was_not_written(fw):
    card = Disk(fw)
    store_net(fw)
    s = Store(fw, card)
    s.run()
    s.click()
    assert s.run() == 0 and s.toasts == ['Foo is ready in Streaming']
    card.files[b'/tmp/q2-foo'] = b'4321'  # its loop's pid: running
    installed = {k for k in card.files if k.startswith(b'/mnt/mmc/')}
    card.log.clear()

    def usleep(*_):  # during the wait: USB storage takes the card, and the app ends
        card.files[MOUNTS] = b'/dev/root / squashfs ro 0 0\n'
        card.files.pop(b'/tmp/q2-foo', None)
    fw.mock('fw_usleep', usleep)
    s.click()
    s.click()
    assert s.run() == 0 and s.toasts[-1] == "Can't write to the card" and s.labels() == ['Installed']
    assert installed <= set(card.files) and not card.of('unlink') and not card.of('rmdir')


# The same for Rockbox: its themes go with it only once the card is known to be there.
def test_a_rockbox_remove_cut_off_by_usb_storage_keeps_the_themes(fw):
    card = Disk(fw, {SOURCES: on_card(b'x/q2-rockbox Rockbox\nx/q2-rockbox-themes Rockbox Themes\n'),
                     b'/mnt/mmc/.rockbox/rockbox': b'bin', b'/mnt/mmc/.rockbox/wps/a.wps': b'wps',
                     b'/mnt/mmc/.q2/apps/rockbox.files': b'.rockbox/rockbox\n', b'/mnt/mmc/.q2/apps/rockbox.tag': b'2.0',
                     b'/mnt/mmc/.q2/apps/themes.files': b'.rockbox/wps/a.wps\n', b'/mnt/mmc/.q2/apps/themes.tag': b'2.0'})
    store_net(fw, {'.rockbox/wps/a.wps': b'wps'})
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    card.files[b'/tmp/q2-rockbox'] = b'4321'  # running
    card.log.clear()

    def usleep(*_):  # during the wait: USB storage takes the card, and the app ends
        card.files[MOUNTS] = b'/dev/root / squashfs ro 0 0\n'
        card.files.pop(b'/tmp/q2-rockbox', None)
    fw.mock('fw_usleep', usleep)
    s.click()
    s.click()
    assert s.run() == 0 and s.toasts[-1] == "Can't write to the card"
    assert not card.of('unlink') and b'/mnt/mmc/.rockbox/wps/a.wps' in card.files


def test_a_cancel_during_the_lists_fetch_downloads_nothing(fw):
    card = Disk(fw, {SOURCES: on_card(b'me/q2-foo Foo\n')})
    net = store_net(fw)
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    net.during = lambda k: net.urls[-1].startswith(b'https://raw') and k == CHUNKS - 1 and s.click()  # as it ends
    s.click()
    assert s.run() == 0 and s.toasts == ['Download canceled'] and s.labels() == ['Install']
    assert not [u for u in net.urls if u.startswith(b'https://github.com/')] and sorted(card.files) == [SOURCES, MOUNTS]


def test_a_cancel_while_unpacking_installs_nothing(fw):
    card = Disk(fw, {SOURCES: on_card(b'me/q2-foo Foo\n')})
    store_net(fw)
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    shell, seen = card.shell, []

    def system(cmd):
        if cmd.startswith(b'busybox unzip -l'):  # the download checked: pressed as the row says so
            s.wait()
            seen.append(s.labels())
            s.click()
            seen.append(s.labels())
        return shell(cmd)
    card.system = system
    s.click()
    assert s.run() == 0 and seen == [['Unpacking…'], ['Canceling…']]
    assert s.toasts == ['Download canceled'] and s.labels() == ['Install']
    assert not card.moves(b'/mnt/mmc/.foo/') and sorted(card.files) == [SOURCES, MOUNTS]  # nothing placed, no staging left


ROOM = 64 << 20  # left free beyond what a download or an unpack needs


@pytest.mark.parametrize('case, said', [
    (dict(list=(0, 404, None, b'Not Found')), "Can't reach GitHub"),
    (dict(list=(0, 403, None, b'{}')), 'GitHub is busy, try later'),
    (dict(list=(0, 429, None, b'{}')), 'GitHub is busy, try later'),
    (dict(list=(7, 0, None)), "Can't reach GitHub"),
    (dict(list=(0, 200, None, b'serial 2\n' + pinned(b'me/q2-foo Foo\n'))), 'App list not signed: not used'),
    (dict(zip=(0, 404, None, b'Not Found')), 'No update for this build'),
    (dict(zip=(0, 200, None, app_zip({**FOO, '.foo/q2-foo': b'\x7fELF evil'}))), "Download doesn't match the list"),
    (dict(zip=(28, 200, None, b'PK partial')), "Can't reach GitHub"),
    (dict(files={'INSTALL.txt': b'x', 'other/q2-foo': b'y'}), 'The download is not a usable app'),
    (dict(files={**FOO, '.foo/../x': b'up'}), 'The download is not a usable app'),
    (dict(unpack=1), "Can't write to the card"),
    (dict(free=ROOM, size=1), 'Needs 1 MB more space'),  # no room for the zip
    (dict(free=ROOM + (1 << 20), files={'.foo/q2-foo': bytes(8 << 20)}), 'Needs 7 MB more space'),  # nor for what it unpacks to
])
def test_an_install_that_fails_says_why_and_leaves_the_card_alone(fw, case, said):
    card = Disk(fw, {SOURCES: on_card(b'me/q2-foo Foo\n')})
    card.unpack_fails = case.get('unpack', 0)
    store_net(fw, case.get('files', FOO), **{k: v for k, v in case.items() if k in ('list', 'zip', 'size')})
    s = Store(fw, card, wifi=0, free=case.get('free', 1 << 30))
    s.wifi = 1
    s.click()
    assert s.run() == 0 and s.toasts == [said] and s.labels() == ['Install']
    assert sorted(card.files) == [SOURCES, MOUNTS]


def test_rockbox_installs_on_a_second_press_keeping_its_config(fw):
    card = Disk(fw, {SOURCES: on_card(b'x/q2-rockbox Rockbox\n'), b'/mnt/mmc/.rockbox/config.cfg': b'mine'})
    store_net(fw, {'.rockbox/rockbox': b'bin', '.rockbox/config.cfg': b'defaults', '.rockbox/fonts/a.fnt': b'font'},
              listed=b'x/q2-rockbox Rockbox\n')
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    s.click()
    label, = s.labels()
    assert label.endswith('Press again to install') and not s.workers
    s.click()
    assert s.run() == 0 and s.toasts == ['Rockbox is ready. Find it on Home'] and s.labels() == ['Installed']
    assert card.files[b'/mnt/mmc/.rockbox/config.cfg'] == b'mine' and card.files[b'/mnt/mmc/.rockbox/rockbox'] == b'bin'
    assert card.moves(b'/mnt/mmc/.rockbox/') == [b'/mnt/mmc/.rockbox/fonts/a.fnt', b'/mnt/mmc/.rockbox/rockbox']


def test_the_themes_come_with_rockbox_and_go_before_it(fw):
    card = Disk(fw, {SOURCES: on_card(b'x/q2-rockbox Rockbox\nx/q2-rockbox-themes Rockbox Themes\n'),
                     b'/mnt/mmc/.rockbox/rockbox': b'bin', b'/mnt/mmc/.rockbox/fonts/a.fnt': b'font',
                     b'/mnt/mmc/.q2/apps/rockbox.files': b'.rockbox/rockbox\n.rockbox/fonts/a.fnt\n',
                     b'/mnt/mmc/.q2/apps/rockbox.tag': b'2.0'})
    store_net(fw, {'.rockbox/wps/a.wps': b'wps', '.rockbox/fonts/a.fnt': b'font'}, listed=b'x/q2-rockbox Rockbox\nx/q2-rockbox-themes Rockbox Themes\n')
    s = Store(fw, card, wifi=0)
    assert s.labels() == ['Installed', 'Install']
    s.wifi = 1
    s.click(2)
    assert s.run() == 0 and s.toasts == ["Rockbox Themes are ready in Rockbox's Theme Settings"]
    assert s.labels() == ['Installed', 'Installed'] and card.files[b'/mnt/mmc/.rockbox/wps/a.wps'] == b'wps'
    s.click(1)
    s.click(1)
    assert s.run() == 0 and s.toasts[-1] == 'Rockbox removed'
    gone = [e[1] for e in card.log if e[0] == 'unlink']
    assert gone.index(b'/mnt/mmc/.rockbox/wps/a.wps') < gone.index(b'/mnt/mmc/.rockbox/rockbox')  # the themes first
    # both apps' files, lists and tags gone: the card holds its list alone
    assert sorted(k for k in card.files if k.startswith(b'/mnt/mmc/')) == [SOURCES]
    assert s.labels() == ['Install']  # and the themes' row with Rockbox


def test_another_app_s_remove_leaves_rockbox_and_its_themes(fw):
    rockbox = {b'/mnt/mmc/.rockbox/rockbox': b'bin', b'/mnt/mmc/.rockbox/wps/a.wps': b'wps',
               b'/mnt/mmc/.q2/apps/rockbox.files': b'.rockbox/rockbox\n', b'/mnt/mmc/.q2/apps/rockbox.tag': b'2.0',
               b'/mnt/mmc/.q2/apps/themes.files': b'.rockbox/wps/a.wps\n', b'/mnt/mmc/.q2/apps/themes.tag': b'2.0'}
    card = Disk(fw, {SOURCES: on_card(b'x/q2-rockbox Rockbox\nx/q2-rockbox-themes Rockbox Themes\nme/q2-foo Foo\n'), **rockbox,
                     FOO_APP: b'bin', b'/mnt/mmc/.q2/apps/foo.files': b'.foo/q2-foo\n', b'/mnt/mmc/.q2/apps/foo.tag': b'2.0'})
    s = Store(fw, card, wifi=0)
    assert s.labels() == ['Installed', 'Installed', 'Installed']
    s.click(3)
    s.click(3)
    assert s.run() == 0 and s.toasts[-1] == 'Foo removed' and FOO_APP not in card.files
    assert {path: card.files.get(path) for path in rockbox} == rockbox  # the themes go with Rockbox alone
    assert s.labels() == ['Installed', 'Installed', 'Install']


def test_the_themes_pressed_once_rockbox_left_the_card_ask_for_it(fw):
    card = Disk(fw, {SOURCES: on_card(b'x/q2-rockbox Rockbox\nx/q2-rockbox-themes Rockbox Themes\n'), b'/mnt/mmc/.rockbox/rockbox': b'bin'})
    s = Store(fw, card, wifi=0)
    assert len(s.labels()) == 2  # Rockbox and its themes
    del card.files[b'/mnt/mmc/.rockbox/rockbox']
    s.wifi = 1
    s.click(2)
    assert s.toasts == ['Install Rockbox first'] and s.sh.job == 0


def test_spotify_installs_with_librespot_stopped_and_its_cache_kept(fw):
    kill = b'kill -9 $(cat /tmp/q2-librespot)'
    card = Disk(fw, {SOURCES: on_card(b'x/q2-librespot Spotify\n'), b'/mnt/mmc/.spotify/cache/x': b'mine', b'/mnt/mmc/.spotify/.cache-size': b'2G'})
    store_net(fw, {'.spotify/librespot': b'bin', '.spotify/run': b'sh', '.spotify/cache/x': b'theirs', '.spotify/.cache-size': b'0'},
              listed=b'x/q2-librespot Spotify\n')
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    s.click()
    assert s.run() == 0 and s.toasts == ['Spotify is ready in Streaming']
    assert card.files[b'/mnt/mmc/.spotify/cache/x'] == b'mine' and card.files[b'/mnt/mmc/.spotify/.cache-size'] == b'2G'
    assert card.files[b'/mnt/mmc/.spotify/librespot'] == b'bin'
    steps = []
    for e in card.log:
        if e[0] == 'system' and e[1].startswith(kill):
            steps.append('stop')
        elif e[0] == 'system' and e[1].startswith(b'busybox unzip -o'):
            steps.append('unpack')
        elif e[0] == 'rename' and e[2].startswith(b'/mnt/mmc/.spotify/') and 'move' not in steps:
            steps.append('move')
    # stopped on the UI thread as the job starts, again before .spotify is touched (it may have started since), and
    # as the job ends, so spotify.c forgets the old one
    assert steps == ['stop', 'unpack', 'stop', 'move', 'stop']


def test_erasing_spotify_s_data_runs_as_a_job(fw):
    card = Disk(fw, {SOURCES: on_card(b'x/q2-librespot Spotify\n'), b'/mnt/mmc/.spotify/librespot': b'bin',
                     b'/mnt/mmc/.spotify/cache/x': b'mine', b'/mnt/mmc/.spotify/.cache-size': b'2G'})
    s = Store(fw, card, wifi=0)
    erase = fw.fn('shelf_erase', C.c_int, C.c_int)
    assert erase(1) == 1 and erase(0) == 1
    assert s.run() == 0 and s.toasts == ["Spotify's login and cache erased"] and erase(0) == 0
    assert [k for k in card.files if b'.spotify' in k] == [b'/mnt/mmc/.spotify/librespot']
    assert ('rmdir', b'/mnt/mmc/.spotify') in card.log  # tried: still holds librespot


SPOT_LOGS = [b'/mnt/mmc/.spotify/spot.log', b'/mnt/mmc/.spotify/aplay.log', b'/mnt/mmc/.spotify/load.log']


def test_erasing_spotify_s_data_takes_the_add_on_s_logs_and_leaves_its_settings(fw):
    """q2-librespot's launcher appends librespot's messages to spot.log, which so names the account at each login and
    every track played; aplay.log is its sink's and load.log its debug file's. .quality and debug are settings the
    user put there, and a spot.log elsewhere on the card is not Spotify's."""
    kept = [b'/mnt/mmc/.spotify/.quality', b'/mnt/mmc/.spotify/debug', b'/mnt/mmc/.spotify/librespot',
            b'/mnt/mmc/Music/spot.log']
    card = Disk(fw, {SOURCES: on_card(b'x/q2-librespot Spotify\n'), b'/mnt/mmc/.spotify/cache/credentials.json': b'{}',
                     **dict.fromkeys(kept + SPOT_LOGS, b"Authenticated as 'me' !\n")})
    s = Store(fw, card, wifi=0)
    assert fw.fn('shelf_erase', C.c_int, C.c_int)(1) == 1 and s.run() == 0
    assert sorted(k for k in card.files if b'/.spotify/' in k or k.endswith(b'spot.log')) == sorted(kept)


def test_erasing_spotify_s_data_after_removing_spotify_leaves_no_folder(fw):
    """Remove takes only the files the add-on's zip listed, so the logs, cache/ and .cache-size stay after it;
    the erase must take every one of them for the folder to go."""
    card = Disk(fw, {SOURCES: on_card(b'x/q2-librespot Spotify\n'), b'/mnt/mmc/.spotify/cache/credentials.json': b'{}',
                     **dict.fromkeys(SPOT_LOGS, b'log')})
    card.dirs.add(b'/mnt/mmc/.spotify')
    s = Store(fw, card, wifi=0)
    assert fw.fn('shelf_erase', C.c_int, C.c_int)(1) == 1 and s.run() == 0
    assert not [k for k in card.files if b'.spotify' in k] and b'/mnt/mmc/.spotify' not in card.dirs


def test_erase_while_checking_stops_the_check(fw):
    card = Disk(fw)
    store_net(fw)
    s = Store(fw, card)
    assert fw.fn('shelf_erase', C.c_int, C.c_int)(1) == 0
    assert s.sh.cancel == 1 and s.toasts == ['Stopped checking for updates'] and s.sh.job == JOB_CHECK


def test_a_job_that_can_t_start_says_so_and_the_row_stays(fw):
    card = Disk(fw, {SOURCES: on_card(b'me/q2-foo Foo\n')})
    s = Store(fw, card, wifi=0, thread=11)
    s.wifi = 1
    s.click()
    assert s.toasts == ["Can't start the download"] and s.sh.job == 0 and not s.sh.running and s.labels() == ['Install']


# ---- pacing while A2DP streams on 2.4 GHz (docs/internals.md#bluetooth-coexistence-pacing) ----

PACE = b'/tmp/q2-bt-coex'


@pytest.mark.parametrize('text, rate', [(b'paced=1 rate=204800 freq=2412\n', 204800), (b'paced=0 rate=0 freq=5180\n', 0),
                                        (b'paced=1 rate=0 freq=2412\n', 0), (b'', 0), (b'paced=1\n', 0),
                                        (b'paced=1 rate=99999999999 freq=2412\n', 0)])
def test_the_pace_file_s_rate(fw, text, rate):
    assert fw.fn('netupdate_pace_rate', C.c_int, C.c_char_p)(text) == rate


class Clock:
    """time_now_ms, moved on by each usleep, whose sleeps are kept"""
    def __init__(self, fw):
        self.ms, self.slept = 5_000_000, []
        fw.mock('time_now_ms', lambda *_: self.ms)
        def usleep(us, *_):
            self.slept.append(us)
            self.ms += us // 1000
            return 0
        fw.mock('fw_usleep', usleep)


@pytest.mark.parametrize('paced', [True, False])
def test_a_download_is_held_to_200_kb_a_second_while_paced(fw, paced):
    """q2guard says a stream runs on 2.4 GHz: Network Update's 1 MB comes in about 5 s, not at once, held by its
    write callback, which TCP passes back to the server. Unpaced (5 GHz, or no stream) it is not held at all."""
    clock = Clock(fw)
    body = bytes(range(256)) * 4096  # 1 MB
    text = b'paced=1 rate=204800 freq=2412\n' if paced else b'paced=0 rate=0 freq=5180\n'
    state, net, card = update(fw, asset=(0, 200, None, body), card=Disk(fw, {PACE: text}))
    assert state == NU_DONE
    held = sum(clock.slept) / 1e6
    if paced:
        assert 3.7 <= held <= 5.2, held  # 1 MB at 200 KB/s, its first second's worth free
    else:
        assert held == 0
