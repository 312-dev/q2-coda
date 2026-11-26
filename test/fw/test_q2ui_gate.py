"""q2ui_gate.c: the screen handed to q2ui and taken back (docs/ui-rewrite.md#cutover). demo starts q2ui as its child
while /mnt/data/ui-target says q2ui and the image has /usr/bin/q2ui, paints nothing and leaves the wheel, Centre,
Return and touches to it; it takes the screen back for a window of its own and gives it again on Home. With no
target or no q2ui in the image, which is every default build, the gate never closes."""
import ctypes as C

import pytest

from conftest import O

TARGET, BINARY = b'/mnt/data/ui-target', b'/usr/bin/q2ui'
CHILD = 4242


class Gate:
    """demo's world round the gate: the target file, the image's q2ui, the child, and the window on top."""

    def __init__(self, fw, target=b'q2ui\n', binary=True):
        self.fw, self.target, self.binary = fw, target, binary
        self.top, self.ended, self.forks, self.shell, self.repaints, self.lit = b'home_page', 0, 0, [], 0, 0
        self.name = C.create_string_buffer(64)
        fw.mock('fw_open', lambda path, *_: 9 if target is not None and C.string_at(path) == TARGET else -1)
        fw.mock('fw_read', self.read)
        fw.mock('fw_close', lambda *_: 0)
        fw.mock('fw_access', lambda path, *_: 0 if binary and C.string_at(path) == BINARY else -1)
        fw.mock('fw_fork', self.fork)
        fw.mock('fw_waitpid', lambda pid, *_: pid if self.ended and pid == CHILD else 0)
        fw.mock('fw_system', lambda cmd, *_: self.shell.append(C.string_at(cmd).decode()))
        fw.mock('window_manager', lambda *_: 0x1000)
        fw.mock('window_manager_get_top_window', lambda *_: 0x2000 if self.top is not None else 0)
        fw.mock('widget_get_prop_str', self.prop)
        fw.mock('widget_invalidate_force', self.repaint)
        fw.mock('reset_poweroptions_timer', self.light)
        self.on = fw.fn('q2ui_on', C.c_int)
        self.poll = fw.fn('q2ui_poll', None)
        self.input = fw.fn('q2ui_input', C.c_int, C.c_uint, C.c_uint)

    def read(self, fd, out, room, *_):
        data = (self.target or b'')[:room]
        C.memmove(out, data, len(data))
        return len(data)

    def fork(self, *_):
        self.forks += 1
        return CHILD

    def prop(self, w, key, default, *_):
        self.name.value = self.top
        return C.addressof(self.name)

    def repaint(self, *_):
        self.repaints += 1

    def light(self, *_):
        self.lit += 1


KEYS = {k: O[k] for k in ('KEY_PREV', 'KEY_NEXT', 'KEY_CENTER', 'KEY_RETURN', 'KEY_PLAY')}
DOWN, UP, TOUCH = O['EVT_KEY_DOWN'], O['EVT_KEY_UP'], O['EVT_POINTER_FIRST']


@pytest.mark.parametrize('target, binary', [(None, True), (b'demo\n', True), (b'q2u', True), (b'q2ui\n', False)])
def test_without_the_target_or_the_program_the_gate_never_closes(fw, target, binary):
    g = Gate(fw, target, binary)
    assert g.on() == 0
    for _ in range(3):
        g.poll()
    assert g.forks == 0 and g.shell == [] and g.repaints == 0
    assert [g.input(UP, k) for k in KEYS.values()] == [0] * 5 and g.input(TOUCH, 0) == 0 and g.lit == 0


def test_the_target_closes_the_gate_before_the_first_paint_and_the_first_pass_starts_q2ui(fw):
    g = Gate(fw)
    assert g.on() == -1 and g.forks == 0  # the screen's holder already: no frame of demo's shows first
    g.poll()
    assert g.forks == 1 and g.on() == CHILD
    assert g.shell == ['killall q2splash 2>/dev/null']  # the boot animation would draw over it
    g.poll()
    assert g.forks == 1 and g.repaints == 0  # running: nothing more


def test_the_wheel_centre_return_and_touches_are_q2uis_and_the_other_keys_stay_demos(fw):
    g = Gate(fw)
    g.poll()
    for key in ('KEY_PREV', 'KEY_NEXT', 'KEY_CENTER', 'KEY_RETURN'):
        assert g.input(DOWN, KEYS[key]) == 1 and g.input(UP, KEYS[key]) == 1, key
    assert g.input(TOUCH, 0) == 1 and g.input(O['EVT_POINTER_ABORT'], 0) == 1
    assert g.lit == 10  # each one keeps the screen lit, as a press demo acted on would
    assert g.input(UP, KEYS['KEY_PLAY']) == 0 and g.input(DOWN, KEYS['KEY_PLAY']) == 0 and g.lit == 10


def test_q2ui_ending_gives_demo_the_screen_whole_and_it_is_not_started_again(fw):
    g = Gate(fw)
    g.poll()
    g.ended = 1  # Return on its first page, or a crash
    g.poll()
    assert g.on() == 0 and g.repaints == 1
    for _ in range(3):
        g.poll()
    assert g.forks == 1 and g.input(UP, KEYS['KEY_RETURN']) == 0


def test_a_window_of_demos_ends_q2ui_and_home_starts_it_again(fw):
    g = Gate(fw)
    g.poll()
    g.top = b'usbmode_page'  # it opened by itself: the user must see it
    g.poll()
    assert g.shell[-1] == f'kill {CHILD}' and g.on() == CHILD
    g.poll()
    assert g.shell.count(f'kill {CHILD}') == 1  # asked once
    g.ended = 1
    g.poll()
    assert g.on() == 0 and g.repaints == 1 and g.forks == 1
    g.poll()
    assert g.forks == 1  # not while that window is up
    g.top, g.ended = b'home_page', 0
    g.poll()  # Home is back on top: started on that pass
    assert g.forks == 2 and g.on() == CHILD and g.shell.count('killall q2splash 2>/dev/null') == 1


def test_with_no_window_yet_demo_is_still_starting_and_q2ui_is_left_alone(fw):
    g = Gate(fw)
    g.top = None
    g.poll()
    g.poll()
    assert g.forks == 1 and not any(c.startswith('kill ') for c in g.shell)
