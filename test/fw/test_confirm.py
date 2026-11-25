"""patch/feedback.c confirm(): the confirmation card over the dimmed page (docs/oled-design.md#confirmation-card), its
layout, default row, Return, answers, switch row and timeout."""
import ctypes as C
import struct

import pytest

from conftest import O
from test_nav_hold import Ui, ui  # noqa: F401 (fixtures)
from test_nav_painters import GEOMETRY, Canvas, scratch, st, theme  # noqa: F401 (fixtures)

KEY_RETURN, KEY_PLAY, EVT_KEY_UP = O['KEY_RETURN'], O['KEY_PLAY'], O['EVT_KEY_UP']
NEUTRAL, DESTRUCTIVE, CONSEQUENTIAL, INFO = range(4)
ANSWER = C.CFUNCTYPE(None, C.c_void_p, C.c_int, C.c_int)


class Confirm(C.Structure):
    _fields_ = [('title', C.c_char_p), ('name', C.c_char_p), ('body', C.c_char_p), ('code', C.c_char_p),
                ('rows', C.POINTER(C.c_char_p)), ('n_rows', C.c_int), ('kind', C.c_int), ('back', C.c_int),
                ('toggle', C.c_char_p), ('toggle_on', C.c_int), ('timeout_ms', C.c_int), ('timeout_row', C.c_int),
                ('answer', ANSWER), ('ctx', C.c_void_p), ('glyph', C.c_char_p), ('note', C.c_char_p),
                ('across', C.c_int)]


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


def repeat(ui, name):
    """One pass of name's timer, kept as stock keeps it while it answers RET_REPEAT (8); its answers."""
    kept = {t: v for t, v in ui.timers.items() if v[0] == ui.fn(name)}
    answers = ui.due(name)
    for (t, v), answer in zip(kept.items(), answers):
        if answer == 8:
            ui.timers[t] = v
    return answers


def rect(ui, w):
    return tuple(ui.nodes[w].rect())


def test_a_destructive_card_opens_on_cancel_its_verb_red_and_spaces_its_answers_evenly(fw, ui, st):
    """docs/oled-design.md#confirmation-card: the answers stand CARD_ROW apart whatever the kind, as Shut Down's do (the
    user's, 2026-11-19); only the ink and the default row say destructive."""
    theme(fw)
    card = Card(fw, ui)
    assert card.ask('Forget %s?', ['Cancel', 'Forget Device'], name='WH-1000XM5',
                    body="You'll need to pair it again to use it.")
    assert card.selected == 0  # Cancel
    items = card.items()
    assert [rect(ui, i)[1] for i in items] == [0, 32]  # no gap before the destructive row
    canvas = card.paint()
    card_box = [b for b in canvas.boxes if b[0][0] == 42 and b[0][2] == 291]
    # 18 + 24 title + 4 + 19 body + 14, two rows, 8: centred on y 168
    h = 18 + 24 + 4 + 19 + 14 + 2 * 32 + 8
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
    now[0] += 1000
    assert repeat(ui, 'feedback__card_tick') == [8]  # RET_REPEAT: AWTK drops a timer that answers anything else
    now[0] += 29000
    assert repeat(ui, 'feedback__card_tick') == [0]
    ui.due()
    assert card.answers == [(2, 0)]


def test_only_one_card_at_a_time(fw, ui, st):
    theme(fw)
    card = Card(fw, ui)
    assert card.ask('Restart?', ['Cancel', 'Restart'], kind=CONSEQUENTIAL)
    assert not card.ask('Shut Down?', ['Cancel', 'Shut Down'], kind=CONSEQUENTIAL)
    fw.fn('confirm_close', None)()
    assert card.dialog in ui.closed and not fw.fn('confirm_up', C.c_int)() and not card.answers


# pair_prompt.c: one pairing presentation over the card, Car Remote's and Guest Play's (docs/oled-design.md#pairing).
def pair(fw, card, phone, code=b'242610', body=b'Enter this code in the Vamp app', timeout=120000):
    rows = (C.c_char_p * 1)(b'Cancel')
    card.rows = rows
    return fw.fn('pair_prompt', C.c_void_p, C.c_char_p, C.c_char_p, C.c_char_p, C.c_void_p, C.c_int, C.c_int,
                 C.c_int, C.c_int, ANSWER, C.c_void_p)(phone, code, body, rows, 1, 0, timeout, 0, card.answer, None)


def test_a_pairing_names_the_phone_over_its_code_with_the_phone_glyph(fw, ui, st):
    """The card's title names the phone, a 28 px phone glyph over it (6 px gap), the six digits in two groups, the
    body saying where to enter them, Cancel lit, and the pairing's time as the foot's bar."""
    theme(fw)
    card = Card(fw, ui)
    assert pair(fw, card, b'Pixel 10 Pro') == card.dialog
    assert card.selected == 0
    canvas = card.paint()
    assert [t[0] for t in canvas.texts] == ['Pair with Pixel 10 Pro?', 'Enter this code in the Vamp app', '242 610']
    h = 18 + 28 + 6 + 24 + 4 + 19 + 6 + 40 + 14 + 32 + 8
    assert [b[0] for b in canvas.boxes if b[0][0] == 42 and b[0][2] == 291] == [(42, 168 - h // 2, 291, h)]
    assert any(b[0][3] == 2 for b in canvas.boxes)  # the timeout's track along the foot


def test_a_long_phone_name_scrolls_in_the_title_never_cut(fw, ui, st):
    """A title wider than the 251 px column is drawn twice, the copy 40 px behind, clipped to the column, and a
    frame timer moves it; a short one is drawn once, centred."""
    theme(fw)
    card = Card(fw, ui)
    pair(fw, card, b'Pixel 10 Pro XL of the Whole Family')  # 46 characters: 460 px at 10 px each
    canvas = Canvas(fw)
    fw.fn('confirm_paint', None, C.c_void_p, C.c_void_p)(card.dialog, canvas.addr)
    titles = [t for t in canvas.texts if t[0].startswith('Pair with')]
    assert len(titles) == 2 and ui.fn('feedback__card_marq') in ui.pending()


def test_a_guests_pairing_drops_the_glyph_rather_than_run_past_the_screen(fw, ui, st):
    """A guest's pairing (two body lines, the code, three answers) is 282 px with the glyph, past the 258 px between
    the status row and the glass's foot (y 44 to 302): the glyph goes, so the card (248 px) shows whole."""
    theme(fw)
    card = Card(fw, ui)
    rows = (C.c_char_p * 3)(b'Allow Once', b'Always Allow', b'Decline')
    card.rows = rows
    fw.fn('pair_prompt', C.c_void_p, C.c_char_p, C.c_char_p, C.c_char_p, C.c_void_p, C.c_int, C.c_int, C.c_int,
          C.c_int, ANSWER, C.c_void_p)(b"Alex's Pixel", b'482913', b"Wants to play music.\nCheck it's on Alex's phone.",
                                       rows, 3, 2, 30000, 2, card.answer, None)
    canvas = card.paint()
    h = 18 + 24 + 4 + 2 * 19 + 6 + 40 + 14 + 3 * 32 + 8
    (box,) = [b[0] for b in canvas.boxes if b[0][0] == 42 and b[0][2] == 291]
    assert box[3] == h and 44 <= box[1] and box[1] + box[3] <= 302


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


def mo_frame(ui):
    """One tick of ui_kit.c's shared frame timer, kept while it answers RET_REPEAT as stock keeps it."""
    for t, (fn, ctx, ms) in list(ui.timers.items()):
        if fn == ui.fn('ui_kit__mo_frame'):
            if ui.due('ui_kit__mo_frame') == [8]:
                ui.timers[t] = (fn, ctx, ms)
            return


def opacity_of(fw, w):
    """w's opacity as last set; stock's 255 until then."""
    mine = [a[1] for _, a in fw.calls('widget_set_opacity') if a[0] == w]
    return mine[-1] if mine else 255


@pytest.mark.parametrize('reduce', [0, 1])
def test_the_card_shows_though_stock_never_paints_a_dialog_under_opacity_9(fw, ui, st, reduce):
    """Stock's widget_paint (demo 0x672848) returns before any paint hook while a widget's opacity is under 9. The
    card's dialog starts at 0, so what fades it in runs from the shared frame timer (ui_kit.c ovm_bind), never from the
    dialog's own paint; under Reduce Motion it is at 255 at once."""
    theme(fw, **({'REDUCEMOTION': b'1'} if reduce else {}))
    now = [1000]
    card = Card(fw, ui, now)
    assert card.ask('Replace Manual 01?', ['Cancel', 'Replace'])
    assert opacity_of(fw, card.dialog) == (255 if reduce else 0)
    painted = 0
    for _ in range(20):  # 320 ms of frames
        now[0] += 16
        mo_frame(ui)
        if opacity_of(fw, card.dialog) >= 9:  # as stock's widget_paint: below 9, no paint at all
            card.paint()
            painted += 1
    assert opacity_of(fw, card.dialog) == 255 and painted >= 10


@pytest.mark.parametrize('reduce', [0, 1])
def test_the_card_fades_in_over_200_ms_and_out_over_120_before_its_answer(fw, ui, st, reduce):
    """docs/oled-design.md#overlay-motion: the dialog (scrim and card together) at opacity 0 when asked, up cubic out
    over 200 ms on the frame timer (at once under Reduce Motion), the status row's dim with it; an answer fades it
    out, linear, over 120 ms, and runs once that is done."""
    theme(fw, **({'REDUCEMOTION': b'1'} if reduce else {}))
    now = [1000]
    card = Card(fw, ui, now)
    assert card.ask('Scan Music?', ['Not Now', 'Scan'], kind=NEUTRAL)
    opacity = lambda: opacity_of(fw, card.dialog)  # noqa: E731
    assert opacity() == (255 if reduce else 0)
    now[0] += 100  # half way: 7/8 in
    mo_frame(ui)
    assert opacity() == (255 if reduce else 223)
    now[0] += 200
    mo_frame(ui)
    assert opacity() == 255
    ui.key(card.dialog, KEY_RETURN)
    assert [ms for fn, _, ms in ui.timers.values() if fn == ui.fn('feedback__card_answer')] == [120]
    now[0] += 60
    mo_frame(ui)
    assert opacity() == 127 and not card.answers
    ui.due('feedback__card_answer')
    assert card.answers == [(0, 0)]


def wheel(fw, card, code):
    """One wheel detent through the firmware's key filter (ringnav), well after the last, on a lit Q2."""
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    card.now[0] += 1000
    event = C.create_string_buffer(0x40)
    C.c_int.from_address(C.addressof(event) + O['EVENT_KEY']).value = code
    return fw.fn('ringnav', C.c_int, C.c_void_p, C.c_void_p)(None, event)


def test_the_wheel_moves_between_the_answers_and_the_list_never_scrolls(fw, ui, st):
    """Software Update's Install card: the answers' list sits across the set's list end (y 224 on the OLED set), and a
    list cut there scrolled Cancel under the bar as the second answer took it, so the wheel seemed stuck on Cancel
    (p31, 2026-11-16). The answers stand whole: the wheel lights each in turn and the list stays put."""
    theme(fw)
    card = Card(fw, ui)
    assert card.ask('Install %s?', ['Cancel', 'Install and Restart'], kind=CONSEQUENTIAL, name='Vamp Dev · V0RHi',
                    body='58.5 MB, over V0RHi. Takes about 2 min;\nyour music and settings stay.')
    rows = rect(ui, card.list)
    view = ui.nodes[card.view]
    struct.pack_into('<2i', view.block, O['W_W'], rows[2], rows[3])  # AWTK lays the view out over the list
    C.c_ubyte.from_address(card.view + O['VIEW_VERTICAL']).value = 1
    C.c_int.from_address(card.view + O['VIEW_CONTENT_H']).value = rows[3]
    assert rows[1] + rows[3] > GEOMETRY.list_end and card.selected == 0
    wheel(fw, card, O['KEY_NEXT'])
    assert card.selected == 1 and not [a for _, a in fw.calls('scroll_view_set_offset') if a[2]]
    wheel(fw, card, O['KEY_NEXT'])
    assert card.selected == 1
    wheel(fw, card, O['KEY_PREV'])
    assert card.selected == 0


def test_a_long_body_wraps_at_words_and_the_card_grows(fw, ui, st):
    """A body line wider than the 251 px column wraps at its words onto as many 19 px lines as it needs, never cut;
    the card grows by each line."""
    theme(fw)
    canvas = Canvas(fw, char_w=8)  # 31 characters to the column
    fw.mock('widget_get_canvas', lambda *_: canvas.addr)
    card = Card(fw, ui)
    assert card.ask('Install Vamp?', ['Cancel', 'Install and Restart'], kind=CONSEQUENTIAL,
                    body='58.5 MB, over V0RHi. Takes about 2 min;\nyour music and settings stay.')
    fw.fn('confirm_paint', None, C.c_void_p, C.c_void_p)(card.dialog, canvas.addr)
    body = [t[0] for t in canvas.texts if t[0] != 'Install Vamp?']
    assert body == ['58.5 MB, over V0RHi. Takes', 'about 2 min;', 'your music and settings stay.']
    h = 18 + 24 + 4 + 3 * 19 + 14 + 2 * 32 + 8
    assert [b[0] for b in canvas.boxes if b[0][0] == 42 and b[0][2] == 291] == [(42, 168 - h // 2, 291, h)]


def test_the_install_cards_long_title_scrolls_never_cut(fw, ui, st):
    """Software Update's 'Install Vamp Dev · V0RHi?' is wider than the column: it scrolls, drawn twice, never cut
    with an ellipsis."""
    theme(fw)
    card = Card(fw, ui)
    card.ask('Install %s?', ['Cancel', 'Install and Restart'], kind=CONSEQUENTIAL, name='Vamp Dev · V0RHi')
    canvas = Canvas(fw)
    fw.fn('confirm_paint', None, C.c_void_p, C.c_void_p)(card.dialog, canvas.addr)
    titles = [t[0] for t in canvas.texts if t[0].startswith('Install')]
    assert titles == ['Install Vamp Dev · V0RHi?'] * 2 and ui.fn('feedback__card_marq') in ui.pending()
    for _ in range(3):  # every frame repaints and asks for the next
        card.now[0] += 33
        assert repeat(ui, 'feedback__card_marq') == [8]
