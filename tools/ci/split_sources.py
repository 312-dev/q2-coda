#!/usr/bin/env python3
"""Cut test/patch.py into test/patch_suite/ and patch/navigation.c into patch/nav/, by anchor lines.

    python3 tools/ci/split_sources.py REPO           # cut whichever of the two is still whole
    python3 tools/ci/split_sources.py REPO --check   # only report where the cuts fall

Both cuts are mechanical: the pieces, joined in order, are the original file byte for byte.

- test/patch.py becomes test/patch_suite/NN_area.py, one file per area, and test/patch.py the runner that
  executes them in name order in one namespace (test/suite_source.py lists them and maps a joined line
  number back to a file's).
- patch/navigation.c keeps its head (includes, state, helpers) and #includes patch/nav/*.c in order after it:
  one translation unit, as before, so the payload's bytes do not change.

A cut falls at an anchor (a class or function's first line), moved up over the comment block directly
above it. A branch made before the split runs this on its own tree first, commits, and then merges the
split branch: both sides then hold the same files, and the merge conflicts only where lines differ.
"""
import pathlib
import sys

PATCH_PARTS = [  # (file stem, the line the part starts at); the first starts at line 1
    ('00_machine', None),
    ('01_search_swipe', 'class SearchMachine(Machine):'),
    ('02_navigation_pull', 'class NavigationMachine(Machine):'),
    ('03_queue_playlists', 'class QueueMachine(Machine):'),
    ('04_bluetooth_volume', 'class BtInitMachine(Machine):'),
    ('05_library', 'class SpecFolderMachine(Machine):'),
    ('06_coverflow_playlist_edit', 'class CoverflowMachine(QueueMachine):'),
    ('07_messages_lyrics', 'class ABMachine(QueueMachine):'),
    ('08_usb_ratings_era', 'class UsbMachine(FileMocks,MsgMachine):'),
    ('09_depth_art', 'class DepthMachine(CoverflowMachine):'),
    ('10_settings_titles', 'class RockboxMachine(Machine):'),
    ('11_shuffle_rate_motion', 'class ShuffleMachine(CoverflowMachine):'),
    ('12_cards_clock', 'class CardMachine(DepthMachine):'),
    ('13_radio_spotify', 'class RadioMachine(SpotMachine):'),
    ('14_card_apps', 'class AppMachine(BrowseMachine):'),
    ('15_update_shelf_resume', 'class UpdateMachine(RadioMachine):'),
    ('16_lock_tags_mono', 'class LockMachine(Machine):'),
    ('17_power_smart_boot', 'class PowerMachine(ResumeMachine):'),
    ('18_headset_info_eq', 'class HeadsetMachine(Machine):'),
]

NAV_PIECES = [  # (file, the start of the line its first function is defined on)
    ('home_carousel.c', 'static int carousel_page(void *top) {'),
    ('lists.c', 'int usable(void) {'),
    ('pull.c', 'void pull_cancel(void) {'),
    ('row_paint.c', 'static int pill_radius('),
    ('rows_settings.c', 'static void *list_button('),
    ('lyrics_status_bar.c', 'static int lyric_centre('),
    ('now_playing.c', 'static int settle_poll('),
    ('theme_titles_paint.c', 'static int boot_differs('),
    ('settings_display.c', 'static void setting_text(int i) {'),
    ('touch_dispatch_layout.c', 'static int untouch(const void *info) {'),
    ('library_pages.c', 'int compact_now_playing(void) {'),
    ('chapters_streams_folders.c', 'static int spot_at('),
    ('hold_menu.c', 'static int qm_restore('),
    ('playback_power.c', 'int shuffle_forced(void) {'),
    ('input.c', 'static int wake_gate('),
]

RUNNER = '''#!/usr/bin/env python3
"""Execute the actual patched MIPS callback with stock filter and mocked UI services.
Requires unicorn==2.1.4. Does not emulate the entire device or flash hardware.

    python3 test/patch.py BUILD

The suite is test/patch_suite/'s files, one per area, run here in name order in this one namespace: a
later file uses the Machines, helpers and state of the earlier ones, so none runs alone
(test/suite_source.py; tools/ci/patch_shards.py runs ranges of it in parallel).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from suite_source import parts  # noqa: E402

for _part in parts():
    exec(compile(_part.read_text(), str(_part), 'exec'))
'''


def _cut(lines, anchors, exact, up):
    """The index each anchor's piece starts at, in order."""
    starts = []
    for anchor in anchors:
        hits = [i for i, l in enumerate(lines) if (l.rstrip('\n') == anchor if exact else l.startswith(anchor))]
        if len(hits) != 1:
            raise SystemExit(f'{anchor!r}: {len(hits)} matches, not 1')
        i = hits[0]
        while i > 0 and up(lines[i - 1]):
            i -= 1
        if starts and i <= starts[-1]:
            raise SystemExit(f'{anchor!r} is out of order')
        starts.append(i)
    return starts


def split_patch(repo, check):
    path, out = repo/'test/patch.py', repo/'test/patch_suite'
    if out.exists():
        print('test/patch.py: already split')
        return
    src = path.read_text()
    lines = src.splitlines(keepends=True)
    starts = [0] + _cut(lines, [a for _, a in PATCH_PARTS[1:]], True, lambda l: l.startswith('#'))
    ends = starts[1:] + [len(lines)]
    for (stem, _), a, b in zip(PATCH_PARTS, starts, ends):
        print(f'test/patch_suite/{stem}.py: lines {a + 1}-{b}')
    if check:
        return
    out.mkdir()
    pieces = [''.join(lines[a:b]) for a, b in zip(starts, ends)]
    assert ''.join(pieces) == src
    for (stem, _), text in zip(PATCH_PARTS, pieces):
        (out/f'{stem}.py').write_text(text)
    path.write_text(RUNNER)


def split_nav(repo, check):
    path, out = repo/'patch/navigation.c', repo/'patch/nav'
    if out.exists():
        print('patch/navigation.c: already split')
        return
    src = path.read_text()
    lines = src.splitlines(keepends=True)
    starts = _cut(lines, [a for _, a in NAV_PIECES], False, lambda l: bool(l.strip()))
    ends = starts[1:] + [len(lines)]
    for (name, _), a, b in zip(NAV_PIECES, starts, ends):
        print(f'patch/nav/{name}: lines {a + 1}-{b}')
    if check:
        return
    out.mkdir()
    head = ''.join(lines[:starts[0]])
    pieces = [''.join(lines[a:b]) for a, b in zip(starts, ends)]
    assert head + ''.join(pieces) == src
    for (name, _), text in zip(NAV_PIECES, pieces):
        (out/name).write_text(text)
    path.write_text(head + '/* The rest of the UI, by area (tools/ci/split_sources.py): one translation unit with the above. */\n'
                    + ''.join(f'#include "nav/{name}"\n' for name, _ in NAV_PIECES))


def main():
    repo = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else '.')
    check = '--check' in sys.argv
    split_patch(repo, check)
    split_nav(repo, check)


if __name__ == '__main__':
    main()
