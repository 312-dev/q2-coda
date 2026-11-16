"""patch/feedback.c confirm(): the confirmation card over the dimmed page (docs/oled-design.md#confirmation-card), its
layout, default row, Return, answers, switch row and timeout."""
import ctypes as C

import pytest

from conftest import O
from test_nav_hold import Ui, ui  # noqa: F401 (fixtures)
from test_nav_painters import Canvas, scratch, st, theme  # noqa: F401 (fixtures)

KEY_RETURN, KEY_PLAY, EVT_KEY_UP = O['KEY_RETURN'], O['KEY_PLAY'], O['EVT_KEY_UP']
NEUTRAL, DESTRUCTIVE, CONSEQUENTIAL, INFO = range(4)
ANSWER = C.CFUNCTYPE(None, C.c_void_p, C.c_int, C.c_int)


class Confirm(C.Structure):
    _fields_ = [('title', C.c_char_p), ('name', C.c_char_p), ('body', C.c_char_p), ('code', C.c_char_p),
                ('rows', C.POINTER(C.c_char_p)), ('n_rows', C.c_int), ('kind', C.c_int), ('back', C.c_int),
                ('toggle', C.c_char_p), ('toggle_on', C.c_int), ('timeout_ms', C.c_int), ('timeout_row', C.c_int),
                ('answer', ANSWER), ('ctx', C.c_void_p)]


class Card:
    """confirm() over a page, stock's sortselect_dialog opened for it (its navbar, list and scroll view)."""
    def __init__(self, fw, ui, now=None):
        self.fw, self.ui, self.answers = fw, ui, []
        self.now = now if now is not None else [1000]
        fw.mock('time_now_ms', lambda *_: self.now[0])
        ui.window('settings_page')
        ui.routes['dialog/sortselect_dialog'] = lambda ctx: self.open()
        self.answer = ANSWER(lambda ctx, row, on: self.answers.append((row, on)))

    def open(self):
        ui = self.ui
        self.dialog = d = ui.window('sortselect_dialog', 'dialog')
        nav = ui.child(d, 'view', 0, 0, 375, 44)
        ui.nodes[nav].name = 'view_navbar'
        ui.nodes[ui.child(nav, 'image', 0, 0, 44, 44)].name = 'img_return'
        self.list = ui.child(d, 'list_view', 0, 44, 375, 210)
        ui.nodes[self.list].name = 'list_view'
        self.view = ui.child(self.list, 'scroll_view', 0, 0, 375, 210)
        ui.nodes[self.view].name = 'scroll_view'

    def ask(self, title, rows, kind=DESTRUCTIVE, name=None, body=None, code=None, back=-1, toggle=None, timeout=0,
            timeout_row=0):
        self.rows = (C.c_char_p * len(rows))(*(r.encode() for r in rows))
        enc = lambda s: s.encode() if s is not None else None  # noqa: E731
        self.c = Confirm(enc(title), enc(name), enc(body), enc(code), self.rows, len(rows), kind, back, enc(toggle), 0,
                         timeout, timeout_row, self.answer, None)
        return self.fw.fn('confirm', C.c_int, C.c_void_p)(C.addressof(self.c))

    @property
    def selected(self):
        return self.ui.nodes[self.view].props.get('_ringnav_index')

    def items(self):
        return [k for k in self.ui.kids(self.view) if self.ui.nodes[k].kind == 'list_item']

    def paint(self):
        canvas = Canvas(self.fw, char_w=5)  # the body fits its 251 px
        self.fw.fn('confirm_paint', None, C.c_void_p, C.c_void_p)(self.dialog, canvas.addr)
        return canvas


def rect(ui, w):
    return tuple(ui.nodes[w].rect())


def test_a_destructive_card_opens_on_cancel_its_verb_red_after_a_gap(fw, ui, st):
    theme(fw)
    card = Card(fw, ui)
    assert card.ask('Forget %s?', ['Cancel', 'Forget Device'], name='WH-1000XM5',
                    body="You'll need to pair it again to use it.")
    assert card.selected == 0  # Cancel
    items = card.items()
    assert [rect(ui, i)[1] for i in items] == [0, 32 + 12]  # the destructive row after the 12 px gap
    canvas = card.paint()
    card_box = [b for b in canvas.boxes if b[0][0] == 42 and b[0][2] == 291]
    # 18 + 24 title + 4 + 19 body + 14, two rows and the gap, 8: centred on y 168
    h = 18 + 24 + 4 + 19 + 14 + 2 * 32 + 12 + 8
    assert card_box and card_box[0][0] == (42, 168 - h // 2, 291, h) and card_box[0][2] == 16
    assert [t[0] for t in canvas.texts] == ['Forget WH-1000XM5?', "You'll need to pair it again to use it."]
    assert rect(ui, card.list)[1] == 168 - h // 2 + 18 + 24 + 4 + 19 + 14  # the answers from there (dialog at y 0)


def test_a_neutral_card_opens_on_its_verb_and_return_answers_the_safe_row(fw, ui, st):
    theme(fw)
    card = Card(fw, ui)
    assert card.ask('Scan Music?', ['Not Now', 'Scan'], kind=NEUTRAL)
    assert card.selected == 1
    assert ui.key(card.dialog, KEY_PLAY) == 11  # the music's keys go nowhere
    ui.key(card.dialog, KEY_RETURN)
    assert not card.answers
    ui.due()
    assert card.answers == [(0, 0)] and card.dialog in ui.closed


def test_a_row_answers_once_after_the_card_closes_and_the_switch_row_flips(fw, ui, st):
    theme(fw)
    card = Card(fw, ui)
    assert card.ask('Delete %s?', ['Cancel', 'Delete Song'], name='Time', toggle='Delete the File Too')
    assert card.selected == 1  # Cancel, under the switch row
    switch, cancel, delete = card.items()
    ui.click(switch)
    ui.click(delete)
    ui.click(cancel)  # a second answer before the first ran is dropped
    ui.due()
    assert card.answers == [(1, 1)]
    assert not card.ask('Again?', ['OK'], kind=INFO) or card.answers == [(1, 1)]


def test_a_timeout_answers_its_row_by_itself_counting_down_on_it(fw, ui, st):
    theme(fw)
    now = [1000]
    card = Card(fw, ui, now)
    assert card.ask("%s", ['Allow Once', 'Always Allow', 'Decline'], kind=NEUTRAL, name="Alex's Pixel", code='482 913',
                    timeout=30000, timeout_row=2)
    canvas = Canvas(fw)
    fw.fn('confirm_rows_paint', None, C.c_void_p, C.c_void_p)(card.view, canvas.addr)
    assert '30 s' in [t[0] for t in canvas.texts]
    now[0] += 30000
    ui.due('feedback__card_tick')
    ui.due()
    assert card.answers == [(2, 0)]


def test_only_one_card_at_a_time(fw, ui, st):
    theme(fw)
    card = Card(fw, ui)
    assert card.ask('Restart?', ['Cancel', 'Restart'], kind=CONSEQUENTIAL)
    assert not card.ask('Shut Down?', ['Cancel', 'Shut Down'], kind=CONSEQUENTIAL)
    fw.fn('confirm_close', None)()
    assert card.dialog in ui.closed and not fw.fn('confirm_up', C.c_int)() and not card.answers


def test_a_two_line_body_stands_on_19_px_lines_under_the_title(fw, ui, st):
    """The title's line from 18 px down, then 4 px, then each body line's 19 px: centres 9 or 10 px into each."""
    theme(fw)
    card = Card(fw, ui)
    assert card.ask('Hide Streaming Songs?', ['Cancel', 'Turn Off'], kind=CONSEQUENTIAL,
                    body='They leave the Library.\nShow in Library pauses.')
    canvas = card.paint()
    top = [b for b in canvas.boxes if b[0][0] == 42 and b[0][2] == 291][0][0][1]
    centres = [r[1] + r[3] // 2 for _, r, _ in canvas.texts]
    assert centres == [top + 18 + 12, top + 18 + 24 + 4 + 9, top + 18 + 24 + 4 + 19 + 9]
