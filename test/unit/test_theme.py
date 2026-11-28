"""tools/theme.py: the Q2 theme XML reader, the schema and the generated theme table."""
import math
import dataclasses
import re
import xml.etree.ElementTree as ET

import pytest

import art
import ipod
import theme
from ipod import Geometry, O, STOCK_GRID
from theme import ThemeError



def error(f, *args):
    with pytest.raises(ThemeError) as e:
        f(*args)
    return str(e.value)


VALID = ['<!-- c -->\n<theme format="1">\n\t<view name="a&amp;&lt;&gt;&quot;"/>\r\n</theme>\n',
         '<theme format="1" id="t">\n<view name="list"><element name="row"/></view>\n</theme>']


def test_tokenize():
    assert theme.tokenize(VALID[0], 't.xml') == [('open', 'theme', {'format': '1'}, 2),
                                                 ('empty', 'view', {'name': 'a&<>"'}, 3),
                                                 ('close', 'theme', {}, 4)]
    assert theme.tokenize('<a b="&amp;lt;"/>', 't.xml') == [('empty', 'a', {'b': '&lt;'}, 1)]


@pytest.mark.parametrize('text, message', [
    ('<a/>x', 't.xml:1: text between tags'),
    ('<a/>\n\xa0', 't.xml:2: text between tags'),
    ('\nx', 't.xml:2: text between tags'),
    ('<![CDATA[x]]>', 't.xml:1: malformed tag'),
    ('<!DOCTYPE a>', 't.xml:1: malformed tag'),
    ('<?xml version="1.0"?>', 't.xml:1: malformed tag'),
    ("<a b='1'/>", 't.xml:1: malformed tag'),
    ('<a B="1"/>', 't.xml:1: malformed tag'),
    ('<a x:y="1"/>', 't.xml:1: malformed tag'),
    ('<a b="<"/>', 't.xml:1: malformed tag'),
    ('</a b="1">', 't.xml:1: malformed tag'),
    ('</a/>', 't.xml:1: malformed tag'),
    ('<a\n b="1" b="2"/>', 't.xml:1: duplicate attribute b'),
    ('<a b="&apos;"/>', 't.xml:1: unknown entity'),
    ('<a b="&"/>', 't.xml:1: unknown entity'),
    ('<a/>\n<!-- x', 't.xml:2: unclosed comment'),
    ('<a b="x\ty"/>', 't.xml:1: malformed tag'),
    ('<a b="x\ny"/>', 't.xml:1: malformed tag'),
    ('<a b="x\x01y"/>', 't.xml:1: malformed tag'),
    ('<!-- a -- b -->', 't.xml:1: unclosed comment'),
])
def test_tokenize_rejects(text, message):
    assert error(theme.tokenize, text, 't.xml') == message


def test_parse():
    root = theme.parse(VALID[1], 't.xml')
    assert (root.tag, root.attrs, root.line, root.file) == ('theme', {'format': '1', 'id': 't'}, 1, 't.xml')
    view, = root.children
    assert (view.tag, view.attrs, view.line) == ('view', {'name': 'list'}, 2)
    assert [(c.tag, c.attrs, c.children, c.line) for c in view.children] == [('element', {'name': 'row'}, [], 2)]


def test_tokenizer_matches_etree():
    """The format is an XML subset: every accepted document reads the same through xml.etree."""
    def mine(n): return (n.tag, n.attrs, [mine(c) for c in n.children])
    def etree(e): return (e.tag, e.attrib, [etree(c) for c in e])
    texts = VALID + [p.read_text() for p in sorted((theme.ROOT/'assets/themes').glob('*/theme.xml'))]
    for text in texts:
        assert mine(theme.parse(text, 't.xml')) == etree(ET.fromstring(text))


@pytest.mark.parametrize('text, message', [
    ('', 't.xml:1: root must be <theme format="1">'),
    ('<view/>', 't.xml:1: root must be <theme format="1">'),
    ('<theme format="2"/>', 't.xml:1: root must be <theme format="1">'),
    ('<theme format="1"/>\n<theme format="1"/>', 't.xml:2: more than one root element'),
    ('<theme format="1">\n</view>', 't.xml:2: mismatched </view>'),
    ('</theme>', 't.xml:1: mismatched </theme>'),
    ('<theme format="1">\n<view>', 't.xml:2: unclosed <view>'),
    ('<theme format="1">\n<view>\n<element>', 't.xml:3: unclosed <element>'),
    ('<theme format="1">x', 't.xml:1: text between tags'),
])
def test_parse_rejects(text, message):
    assert error(theme.parse, text, 't.xml') == message


def test_load(tmp_path):
    path = tmp_path/'t'/'theme.xml'
    path.parent.mkdir()
    path.write_bytes('<theme format="1" name="Ünï"/>'.encode())
    assert theme.load(path).attrs == {'format': '1', 'name': 'Ünï'}
    assert theme.load(path).file == 't/theme.xml'
    path.write_bytes(b'<theme format="1"/>' + b' ' * (32768 - 19))
    assert theme.load(path).tag == 'theme'
    path.write_bytes(b'<theme format="1"/>' + b' ' * (32769 - 19))
    assert error(theme.load, path) == 't/theme.xml:1: larger than 32 KB'
    path.write_bytes(b'<theme format="1" name="\xff"/>')
    assert error(theme.load, path) == 't/theme.xml:1: not UTF-8'


THEMES = theme.ROOT/'assets/themes'


def opt(setting, default=-1, preset=-1, keep=-1, values=0):
    return {'setting': setting, 'def': default, 'preset': preset, 'keep': keep, 'values': values}


def own(key, label, names, default=-1, preset=-1, keep=-1):  # a theme's own option, as resolve() keeps it
    return {**opt('SETTING_OWN', default, preset, keep), 'key': key, 'label': label, 'names': list(names)}


THUMB = dict(thumb=1, ticks_turn=36, ticks_album=7, fast_at=23, fast_full=90, top_speed=20, track=30, snap=25, spin=130, far_left=250,
             detent=80, hold_ms=5000, stop_ms=60, stop_left=60, rate_up=120, rate_down=50, back=50, dead=1, contact_ms=40, aim_snap=30,
             lead_from=25, lead_full=100, lead_ms=45, row_shift=20, row_from=100, row_full=300, row_shrink=8, row_flat=200, row_out=33,
             row_edge=160, row_width=40, row_key=72, jump_ms=700, jump_ticks=5, jump_at=32, jump_pace=140,
             big_jump_at=75, big_jump_ms=240, enter_albums=8, slide_ms=160, fold_near=50,
             arch_at=8, arch_full=16, arch_span=500, arch_in=100, arch_out=62, arch_tau=56)


MOTION_TODAY = {**{f'motion.{k}.{a}': 0 for k in ('page', 'playing', 'coverflow') for a in ('ms', 'easing', 'anim')},
                **{f'motion.{k}.{a}': 0 for k in ('inplace', 'swap', 'step', 'step_fast', 'coverflow_step', 'coverflow_step_fast') for a in ('ms', 'easing')},
                'motion.page.ms': 220, 'motion.page.easing': 5, 'motion.swap.ms': 220, 'motion.swap.easing': 8,
                'motion.step.ms': 200, 'motion.step.easing': 8, 'motion.step_fast.ms': 120, 'motion.step_fast.easing': 8,
                'motion.coverflow_step.ms': 200, 'motion.coverflow_step.easing': 8, 'motion.coverflow_step_fast.ms': 120, 'motion.coverflow_step_fast.easing': 8,
                'recall_ms': 300}


# Each coverflow.* key at its low end, but the eye, which must clear a side cover's near edge: a theme with no base states them all.
CF_LO = '<view name="coverflow">' + ''.join(
    f'<element name="{e}" ' + ' '.join(f'{k.path.rpartition(".")[2]}="{41 if k.path.endswith(".eye") else k.lo}"' for k in theme.SCHEMA.values()
                                       if k.path.startswith(f'coverflow.{e}.')) + '/>' for e in ('shelf', 'caption')) + '</view>'
# The engine's tests' base: a synthetic theme with no base of its own that offers every ACCENTS row, on a grid of its
# own with a right column (the chevrons and song rows stand on it) and the wheel's scroll indicator, which every
# theme draws (theme.validate).
BASE_GEOM = '<geometry right_x="28"/>'
BASE_SB = '<view name="list"><element name="scrollbar" w="4" right="5" top="134" bottom="206" min="8" thumb="#e5e5e5"/></view>'
A = '<option setting="accent" key="ACCENT"/>'
BASE_BODY = BASE_GEOM + '<option setting="light" preset="off" keep="auto"/>' + A + BASE_SB + CF_LO
BASE = f'<theme format="1" id="base" name="Base" index="1">{BASE_BODY}</theme>'


def bare(t):
    return {k: v for k, v in t.items() if k != 'line'}


def resolved(body, attrs='id="t" name="T" index="2" extends="base"'):
    by_id = {n.attrs['id']: n for n in map(theme.load, sorted(THEMES.glob('*/theme.xml')))}
    base = theme.parse(BASE, 'base.xml')
    root = theme.parse(f'<theme format="1" {attrs}>{body}</theme>', 't.xml')
    return theme.resolve(root, {**by_id, 'base': base, root.attrs.get('id', ''): root})


def oled():
    """The one built-in theme."""
    t, = theme.builtins()
    return t


def test_accent_names():
    assert theme.ACCENT_NAMES == ['graphite', 'crimson', 'tidal', 'champagne', 'cobalt', 'tangerine', 'plum', 'ivory', 'green', 'arterial']
    assert theme.ACCENT_NAMES[theme.O['CRIMSON']] == 'crimson'
    assert theme.ACCENT_NAMES[theme.O['MINIMAL_ACCENT']] == 'ivory'


def test_the_base_is_valid():
    assert theme.validate(resolved(''), 't.xml') is None


def test_extends():
    o = oled()
    assert bare(resolved('', 'id="t" name="T" index="2" extends="oled"')) == {**bare(o), 'id': 't', 'name': 'T', 'index': 2, 'art_id': 't'}
    assert resolved(A)['opts'] == [opt('SETTING_ACCENT')]
    t = resolved('<colorSchemes accents="cobalt green"/><option setting="accent" key="ACCENT" default="cobalt"/>')
    assert (t['accents'], t['schemes'][0], t['schemes'][4], t['opts']) == (0x110, theme.zero_scheme(), theme.zero_scheme(), [opt('SETTING_ACCENT', default=4, values=0x110)])
    assert resolved('<view name="home"><element name="rows" top="12"/></view>')['home_top'] == 12  # a card theme states numbers
    assert resolved('<colorScheme name="graphite" muted="#112233"/>')['schemes'][0]['muted'] == 0x112233
    assert resolved('<colorScheme name="graphite" muted="#AbCdEf"/>')['schemes'][0]['muted'] == 0xabcdef
    assert resolved('<colorSchemes/>')['accents'] == 0
    assert resolved('<view name="playing"><element name="album" opacity="255"/></view>')['np_album_op'] == 255


M = 'id="oled" name="M" index="0"'
@pytest.mark.parametrize('body, attrs, message', [
    ('<font name="x"/>', None, 'unknown element <font>'),
    ('<view name="list"><element name="selection" radius="4"><element name="x"/></element></view>', None, 'unknown element <element>'),
    ('<view name="modal"/>', None, 'unknown view modal'),
    ('<view name="list" layout="x"/>', None, 'unknown key view.layout'),
    ('<view name="list"><element name="tile"/></view>', None, 'unknown element list.tile'),
    ('<view name="list"><element name="icons" grey="1"/></view>', None, 'unknown key list.icons.grey'),
    ('<view name="playing"><element name="title" font="serif"/></view>', None, 'playing.title.font: unknown value serif'),
    ('<colorScheme name="sky-blue"/>', None, 'unknown scheme sky-blue'),
    ('<colorScheme name="ivory" sel.glow="#000000"/>', None, 'unknown key scheme.sel.glow'),
    ('<colorSchemes accents="graphite blue"/>', None, 'unknown scheme blue'),
    ('<colorSchemes accents="graphite" tint="1"/>', None, 'unknown key colorSchemes.tint'),
    ('<view name="home"><element name="chevron" visible="2"/></view>', None, 'home.chevron.visible: expected 0 or 1'),
    ('<view name="playing"><element name="album" opacity="x"/></view>', None, 'playing.album.opacity: expected an integer'),
    ('<view name="playing"><element name="album" opacity="256"/></view>', None, 'playing.album.opacity: 256 outside 0..255'),
    ('<view name="playing"><element name="album" opacity="-1"/></view>', None, 'playing.album.opacity: -1 outside 0..255'),
    ('<colorScheme name="ivory" muted="eeeeec"/>', None, 'scheme.muted: expected #rrggbb'),
    ('<colorScheme name="ivory" muted="#112233fe"/>', None, 'scheme.muted: expected #rrggbb'),
    ('<colorSchemes accents="cobalt"/><colorScheme name="green" muted="#112233"/>', None, 'scheme green is not in colorSchemes'),
    ('<option setting="volume"/>', None, 'unknown setting volume'),
    ('<option setting="light" preset="dim"/>', None, 'light: unknown value dim'),
    ('<option setting="light" bright="1"/>', None, 'unknown key option.bright'),
    ('<option setting="home" key="HOME"/>', None, 'home: unknown key HOME'),
    ('<option setting="accent" key="HOME"/>', None, "HOME: the payload's key"),
    ('', 'id="t" name="T" index="2" extends="pico"', 'extends unknown theme pico'),
    ('', 'id="T" name="T" index="2"', 'id: expected [a-z][a-z0-9_-]*'),
    ('', 'id="t" name="" index="2"', 'name: expected 1..48 bytes, no control characters'),
    ('', 'id="t" name="T" index="10"', 'index: expected a digit'),
    ('', 'id="t" name="T" index="2" colour="x"', 'unknown key theme.colour'),
    ('', 'id="t" name="T" index="2" order="1"', 'unknown key theme.order'),  # the picker's row: one theme has no picker
    ('', 'name="T" index="2"', 'id: expected [a-z][a-z0-9_-]*'),
    ('', 'id="t" name="T"', 'index: expected a digit'),
    ('<colorScheme name=""/>', None, 'unknown scheme '),
    ('<option/>', None, 'unknown setting '),
    ('<view/>', None, 'unknown view '),
    ('<view name="list"><element/></view>', None, 'unknown element list.'),
    ('<view name="home"><element name="rows" label_end_full="8"/></view>', M, 'home.rows.label_end_full comes from patch/offsets.inc'),
    ('<colorScheme name="graphite" chevron="#eeeeec"/>', M, 'scheme.chevron comes from patch/offsets.inc'),
    ('<colorScheme name="graphite" sel.text="#000000"/>', None, 'scheme.sel.text: #000000 reads as unset; use #010101'),
    ('<colorScheme name="graphite" light.sel.text="#000000"/>', None, 'scheme.sel.text: #000000 reads as unset; use #010101'),
    ('<view name="settings"><element name="values" ink="#000000"/></view>', None, 'settings.values.ink: #000000 reads as unset; use #010101'),
])
def test_resolve_rejects(body, attrs, message):
    assert error(resolved, body, *([attrs] if attrs else [])) == f't.xml:1: {message}'


def test_resolve_rejects_a_cycle():
    a = theme.parse('<theme format="1" id="a" name="A" index="2" extends="b"/>', 'a.xml')
    b = theme.parse('<theme format="1" id="b" name="B" index="3" extends="a"/>', 'b.xml')
    assert error(theme.resolve, a, {'a': a, 'b': b}) == 'a.xml:1: extends cycle at a'


def test_resolve_checks_role_macros(monkeypatch):
    monkeypatch.setitem(theme.ROLE_MACROS, 'oled', {**theme.ROLE_MACROS['oled'], 'home_label_end[1]': '400'})
    line = theme.load(THEMES/'oled/theme.xml').line
    assert error(theme.builtins) == f'oled/theme.xml:{line}: home.rows.label_end_full: 400 outside 0..375'
    monkeypatch.setitem(theme.ROLE_MACROS, 'oled', {**theme.ROLE_MACROS['oled'], 'home_label_end[1]': 'abs(4)'})
    with pytest.raises(NameError):  # offsets.inc names only, no builtins
        theme.builtins()


N = 'id="t" name="T" index="2"'
V = '<value label="Off"/><value label="On"/>'
C = '<colorSchemes accents="cobalt"/><option setting="accent" key="ACCENT" default="cobalt" preset="cobalt"/>'
@pytest.mark.parametrize('body, attrs, message', [
    ('<option setting="light"/>', None, 'no accent option'),
    ('<option setting="accent"/>', N, 'accent: no key'),
    ('<option setting="light"/><option setting="light"/><option setting="accent" key="ACCENT"/>', None, 'SETTING_LIGHT declared twice'),
    ('<colorSchemes accents="cobalt"/><option setting="accent" key="ACCENT" default="green"/>', None, 'accent default green is not in colorSchemes'),
    ('<colorSchemes accents="cobalt"/><option setting="accent" key="ACCENT" preset="green"/>', None, 'accent preset green is not in colorSchemes'),
    ('<colorSchemes accents="cobalt"/><option setting="accent" key="ACCENT" default="graphite"/>', None, 'accent default graphite is not in colorSchemes'),
    ('<colorSchemes accents=""/>', None, 'colorSchemes is empty'),
    (A + f'<option key="NPTIME" label="Art">{V}</option><option key="NPTIME" label="Art">{V}</option>', None, 'NPTIME declared twice'),
    ('<option setting="accent" key="NPTIME"/>' + f'<option key="NPTIME" label="Art">{V}</option>', None, 'NPTIME declared twice'),
    (A + '<colorScheme name="sky" base="cobalt" label="Sky"/><colorScheme name="sea" base="tidal" label="Sea"/>'
     '<colorScheme name="dew" base="green" label="Dew"/>', None, '13 accents, at most 12'),
    (A + '<option setting="battery" default="percent"/>', None,
     "SETTING_BATTERY: default has no effect without values (settings_read holds a stock setting's default)"),
    ('<colorSchemes accents="cobalt"/>' + A, None, 'accent: slot 0 is not offered, so a missing key needs a default'),
])
def test_validate_rejects(body, attrs, message):
    t = resolved(body, *([attrs] if attrs else []))
    assert error(theme.validate, t, 't.xml') == f't.xml:1: {message}'


def test_validate_accepts():
    t = resolved(C)
    assert theme.validate(t, 't.xml') is None


def test_key():
    assert theme.key('a.b.c', 'f', 'bool') == theme.Key('a.b.c', 'f', 'bool', 0, 1, 0, 'live')
    assert theme.key('a.b.c', 'f', 'color').hi == 0xffffff
    assert theme.key('scheme.mode', 'f', 'enum').hi == 2
    assert theme.key('a.b.c', 'f', 'int', 255).hi == 255


def test_builtins_rejects(tmp_path):
    def put(folder, attrs):
        (tmp_path/folder).mkdir(exist_ok=True)
        (tmp_path/folder/'theme.xml').write_text(f'<theme format="1" {attrs}>{BASE_BODY}</theme>')
    put('a', 'id="a" name="A" index="0"')
    assert [t['id'] for t in theme.builtins(tmp_path)] == ['a']
    put('b', 'id="b" name="B" index="2"')
    assert error(theme.builtins, tmp_path) == 'b/theme.xml:1: index 2, expected 1'
    put('b', 'id="c" name="B" index="1"')
    assert error(theme.builtins, tmp_path) == 'b/theme.xml:1: id c is not its folder name'
    put('b', 'id="b" name="B" index="0"')
    assert error(theme.builtins, tmp_path) == "b/theme.xml:1: index 0 is a/theme.xml's too"
    (tmp_path/'b'/'theme.xml').write_text('<theme format="1" id="b" name="B" index="1"><option setting="accent"/></theme>')
    assert error(theme.builtins, tmp_path) == 'b/theme.xml:1: accent: no key'


def test_builtins_rejects_a_duplicate_id(tmp_path):
    for folder in ('a', 'b'):
        (tmp_path/folder).mkdir()
        (tmp_path/folder/'theme.xml').write_text(f'<theme format="1" id="a" name="A" index="0">{BASE_BODY}</theme>')
    assert error(theme.builtins, tmp_path) == "b/theme.xml:1: id a is a/theme.xml's too"


def test_validate_rejects_unset_coverflow():
    """A theme with no base and no coverflow.* has 0 for keys whose range starts at 1 (reflect divides)."""
    t = resolved(BASE_GEOM + A + BASE_SB, N)
    assert error(theme.validate, t, 't.xml') == 't.xml:1: coverflow.shelf.eye: 0 outside 1..2046'
    t = resolved(BASE_GEOM + A + BASE_SB + CF_LO, N)
    assert theme.validate(t, 't.xml') is None


@pytest.mark.parametrize('name', ['A\x7fB', 'A\x85B', 'x' * 49, 'é' * 25])
def test_resolve_rejects_a_bad_name(name):
    assert error(resolved, '', f'id="t" name="{name}" index="2" extends="base"') == 't.xml:1: name: expected 1..48 bytes, no control characters'


def test_hyphen_in_an_id_is_a_c_name():
    t = resolved('', 'id="a-b" name="T" index="2" extends="base"')
    assert t['id'] == 'a-b' and theme.c_name(t) == 'a_b'


def test_home_rows_font_is_the_home_role():
    t = resolved('<view name="home"><element name="rows" font="inter" font_px="27"/></view>')
    assert (t['font[ROLE_HOME]'], t['font_px[ROLE_HOME]'], t['font[ROLE_NP]'], t['font_px[ROLE_NP]']) == (1, 27, 0, 0)


FONT_KEYS = [path for path, values in theme.CHOICES.items() if values is theme.FONTS]


@pytest.mark.parametrize('face, c', [('inter', 'FONT_INTER'), ('inter-medium', 'FONT_INTER_MEDIUM')])
@pytest.mark.parametrize('path', FONT_KEYS)
def test_a_theme_names_any_face_for_each_font_role(path, face, c):
    view, element, attr = path.split('.')
    t = resolved(f'<view name="{view}"><element name="{element}" {attr}="{face}"/></view>')
    field = theme.SCHEMA[path].field
    assert list(theme.CHOICES[path].values())[t[field]] == c
    assert all(t[k.field] == 0 for p, k in theme.SCHEMA.items() if p in FONT_KEYS and p != path)  # the others stay stock's


def test_every_font_role_takes_the_faces():
    assert len(FONT_KEYS) == 8 and theme.FONTS == {k: f.c for k, f in theme.FACES.items()}
    assert list(theme.FONTS) == ['regular', 'inter', 'inter-medium']


def test_resolve_takes_a_long_name():
    assert resolved('', f'id="t" name="{"é" * 24}" index="2"')['name'] == 'é' * 24


def test_emit(tmp_path):
    """OLED's scheme rows and its options, as emit writes them: the rows its colorSchemes leaves out are zero, its
    own Blue and Arterial on their own tones, its chevron MINIMAL_CHEVRON (ROLE_MACROS); the dark half on black."""
    theme.emit([oled()], tmp_path)
    zero = '.chevron = 0x000000, .muted = 0x000000, .sel_text = 0x000000, .mode = SCHEME_DERIVE, .paper = 0x000000, .ink = 0xffffff }'
    assert (tmp_path/'themes.inc').read_text().startswith('\n'.join([
        '/* tools/theme.py wrote this from assets/themes/<id>/theme.xml; edit those. */',
        'static const unsigned oled_tones[][5] = {',
        '    { 0x2160d8, 0x2160d8, 0x2160d8, 0x2160d8, 0x2160d8 },',
        '    { 0xb8141c, 0xb8141c, 0xb8141c, 0xb8141c, 0xb8141c },',
        '};',
        'static const scheme_t oled_schemes[] = {',
        *(f'    {{ .tones = accents[{v}], {zero},' for v in range(10)),
        '    { .tones = oled_tones[0], .chevron = 0x777777, .muted = 0x858585, .sel_text = 0xffffff, .mode = SCHEME_DERIVE, '
        '.paper = 0x000000, .ink = 0xffffff, .label = "Blue" },',
        '    { .tones = oled_tones[1], .chevron = 0x777777, .muted = 0x858585, .sel_text = 0xffffff, .mode = SCHEME_DERIVE, '
        '.paper = 0x000000, .ink = 0xffffff, .label = "Arterial" },',
        '};']))
    text = (tmp_path/'themes.inc').read_text()
    assert ('static const option_t oled_opts[] = {\n'
            '    { .setting = SETTING_LIGHT, .def = -1, .preset = 0, .keep = 2, .values = 0x0 },\n'
            '    { .setting = SETTING_ACCENT, .def = 11, .preset = 11, .keep = -1, .values = 0xc00 },\n'
            '    { .setting = SETTING_HOME, .def = -1, .preset = 0, .keep = -1, .values = 0x0 },\n'
            '    { .setting = SETTING_BATTERY, .def = 0, .preset = -1, .keep = -1, .values = 0x3 },\n') in text
    assert 'const theme_t themes[THEME_N] = {\n    {\n        .sel_radius = 8,\n        .sel_inset = 16,\n' in text


def on_oleds_set(t):
    """t on OLED's grid: the build bakes one, so themes emitted together share it."""
    return dict(t, geom=oled()['geom'])


def test_emit_both(tmp_path):
    """The engine still emits several themes: each its schemes and options, then the one table."""
    t = resolved(C, 'id="t" name="T" index="1" extends="base"')
    theme.emit([oled(), on_oleds_set(t)], tmp_path)
    text = (tmp_path/'themes.inc').read_text()
    assert [l.split(' = ')[0] for l in text.splitlines() if l.startswith('static const')] == [
        'static const unsigned oled_tones[][5]', 'static const scheme_t oled_schemes[]', 'static const scheme_t oled_light[]',
        'static const option_t oled_opts[]', 'static const scheme_t t_schemes[]', 'static const option_t t_opts[]',
        'static const volatile short geom_vals[V_N]']
    assert 'extern const theme_t themes[THEME_N];' in (tmp_path/'themes.h').read_text()
    assert ('    { .tones = accents[7], .chevron = 0x000000, .muted = 0x000000, .sel_text = 0x000000, .mode = SCHEME_DERIVE, '
            '.paper = 0xf4f4f4, .ink = 0x161616 },\n    { .tones = accents[8]') in text.split('t_schemes[] = {')[1]  # one array, both halves
    assert '    { .setting = SETTING_ACCENT, .def = 4, .preset = 4, .keep = -1, .values = 0x10 },' in text
    assert '        .accent_slot = &st.accent,\n        .accent_key = "ACCENT",\n        .schemes = t_schemes,' in text
    assert '    },\n};\nconst volatile unsigned short geom_words' in text
    assert (tmp_path/'themes.h').read_text().startswith('/* tools/theme.py wrote this from assets/themes/<id>/theme.xml; edit those. */\nenum { G_PITCH, ')


def test_view_attr():
    assert theme.view_attr('list.selection.radius') and theme.view_attr('home.rows.top')
    assert not theme.view_attr('scheme.muted')
    assert not theme.view_attr('scheme.sel.text')  # a scheme role has two dots too


# Enum fields and value counts come out as C names, so a reordered or added value is a compile error.
def test_emit_names_enums_and_counts(tmp_path):
    inc = geom_inc(tmp_path, theme.builtins())
    assert inc.count('.font[ROLE_NP] = FONT_INTER_MEDIUM,') == 1 and '.font[ROLE_NP] = 2,' not in inc
    assert '.mode = SCHEME_DERIVE' in inc and '.mode = 0' not in inc
    for name, (_, values) in theme.SETTINGS.items():
        assert f'#define SETTING_N_{name.upper()} {len(values)}\n' in inc


def with_role(monkeypatch, **cf):
    monkeypatch.setitem(theme.ROLE_MACROS, 'oled', {**theme.ROLE_MACROS['oled'], **{f'cf.{k}': str(v) for k, v in cf.items()}})


@pytest.mark.parametrize('field,value', [('eye', 2047), ('eye', 0), ('xs', 800), ('zs', 800), ('x1', 1024), ('reflect', 0),
    ('bright2', 0), ('text_y', 169), ('name_px', 33), ('artist_px', 29), ('radius', 84), ('stride', 0),
    ('tilt', 29), ('tilt', 91), ('horizon', 0), ('horizon', 100), ('third', 2), ('swing', 3), ('lift', 41), ('eye_turn', 401), ('view_h', 209), ('view_h', 295),
    ('thumb', 2), ('detent', 101), ('hold_ms', 30001), ('dead', 21), ('row_width', 101), ('art_persp', 101),
    ('jump_ms', 5001), ('jump_ticks', 201), ('jump_at', 1001), ('jump_pace', 2001), ('big_jump_at', 1001), ('big_jump_ms', 5001),
    ('enter_albums', 13), ('slide_ms', 1001), ('fold_near', 51)])
def test_coverflow_range_rejects(monkeypatch, field, value):
    if f'cf.{field}' not in theme.ROLE_MACROS['oled']:  # OLED's theme.xml states the thumb, where the covers stand and the caption
        e = 'caption' if f'coverflow.caption.{field}' in theme.SCHEMA else 'shelf'
        with pytest.raises(theme.ThemeError, match=rf'coverflow\.{e}\.{field}: {value} outside'):
            resolved(f'<view name="coverflow"><element name="{e}" {field}="{value}"/></view>')
        return
    with_role(monkeypatch, **{field: value})
    with pytest.raises(theme.ThemeError, match=rf'coverflow\.\w+\.{field}: {value} outside'):
        theme.builtins()


@pytest.mark.parametrize('field,value,message', [
    ('cf.top', 15, 'top \\+ 160 \\+ reflect = 221 exceeds 220'), ('cf.reflect', 48, 'top \\+ 160 \\+ reflect = 221 exceeds 220'),
    ('cf.view_h', 263, 'view_h 263 exceeds the client height 262'),
    ('cf.bright2', 193, 'bright2 193 exceeds bright1 192'), ('cf.text_y', 208, 'text_y \\+ 55 = 263 exceeds the client height 262'),
    ('cf.row_out', 29, 'row_out 29 puts 16 covers of the held row on the screen, past CF_WIDE 15')])
def test_coverflow_cross_rejects(field, value, message):
    with pytest.raises(theme.ThemeError, match=message):
        theme.validate({**oled(), field: value}, 't.xml')


def test_coverflow_eye_turn_stays_in_range():
    theme.validate({**oled(), 'cf.eye': 1646, 'cf.eye_turn': 400}, 't.xml')
    with pytest.raises(theme.ThemeError, match='eye \\+ eye_turn = 2047 exceeds 2046'):
        theme.validate({**oled(), 'cf.eye': 1647, 'cf.eye_turn': 400}, 't.xml')


def test_coverflow_thumb_is_oleds():  # the page's numbers, all retunable
    assert set(THUMB) == set(theme.CF_THUMB)
    o = oled()
    assert {a: o[f'cf.{a}'] for a in THUMB} == THUMB


def test_coverflow_arch_needs_its_numbers():  # th_arch's divisors, and a span of an album at least
    c = oled()
    for bad in ({'cf.arch_tau': 0}, {'cf.arch_in': 0}, {'cf.arch_out': 0}, {'cf.arch_span': 99}, {'cf.arch_full': c['cf.arch_at']}):
        with pytest.raises(theme.ThemeError, match='coverflow.shelf: arch_span'):
            theme.validate({**c, **bad}, 't.xml')
    theme.validate({**c, 'cf.arch_span': 0, 'cf.arch_tau': 0, 'cf.arch_full': 0}, 't.xml')  # no arch: the row at speed, none read


@pytest.mark.parametrize('field', ['ticks_turn', 'ticks_album', 'track', 'snap', 'spin', 'far_left', 'rate_up', 'rate_down', 'lead_ms', 'row_edge',
                                   'enter_albums', 'slide_ms'])
def test_coverflow_thumb_needs_its_divisors(field):
    with pytest.raises(theme.ThemeError, match=f'coverflow.shelf: thumb needs {field}'):
        theme.validate({**oled(), f'cf.{field}': 0}, 't.xml')


@pytest.mark.parametrize('lo,hi', [('fast_at', 'fast_full'), ('fast_at', 'big_jump_at'), ('lead_from', 'lead_full'), ('row_from', 'row_full')])
def test_coverflow_thumb_ranges_run_upward(lo, hi):
    c = oled()
    with pytest.raises(theme.ThemeError, match=f'coverflow.shelf: {hi} {c[f"cf.{lo}"]} must pass {lo} {c[f"cf.{lo}"]}'):
        theme.validate({**c, f'cf.{hi}': c[f'cf.{lo}']}, 't.xml')
    theme.validate({**c, 'cf.thumb': 0, f'cf.{hi}': 0}, 't.xml')  # off, they're not read


def test_coverflow_shelf_near_edge_clears_the_eye():
    with pytest.raises(theme.ThemeError, match="eye \\+ z1 = 69 doesn't clear 80 sin\\(tilt\\) = 69\\.3$"):
        theme.validate({**oled(), 'cf.eye': 39, 'cf.z1': 30, 'cf.tilt': 60}, 't.xml')
    theme.validate({**oled(), 'cf.eye': 40, 'cf.z1': 30, 'cf.tilt': 60, 'cf.thumb': 0}, 't.xml')  # no held row to fit


def test_coverflow_swing_near_edge_clears_the_eye_mid_step():
    tight = {**oled(), 'cf.eye': 41, 'cf.z1': 0, 'cf.tilt': 30, 'cf.thumb': 0}  # rest clears by 1, mid-step it doesn't; no held row to fit
    theme.validate(tight, 't.xml')
    with pytest.raises(theme.ThemeError, match="lift / 4 = -39\\.0$"):
        theme.validate({**tight, 'cf.swing': 1}, 't.xml')
    theme.validate({**tight, 'cf.swing': 1, 'cf.eye': 86}, 't.xml')
    with pytest.raises(theme.ThemeError, match='lift / 4 = 0\\.0$'):
        theme.validate({**tight, 'cf.swing': 1, 'cf.eye': 85, 'cf.lift': 20}, 't.xml')
    with pytest.raises(theme.ThemeError, match='rests 10368 px out, past 2048$'):
        theme.validate({**oled(), 'cf.swing': 1, 'cf.eye': 81, 'cf.z1': 0, 'cf.tilt': 90}, 't.xml')


def test_coverflow_caption_follows_the_bar():
    t = {**oled(), 'geom': Geometry(name='tall', bar=34, right_x=28)}
    with pytest.raises(theme.ThemeError, match='text_y \\+ 55 = 287 exceeds the client height 286'):
        theme.validate({**t, 'cf.text_y': 232}, 't.xml')
    theme.validate({**t, 'cf.text_y': 231}, 't.xml')


def test_coverflow_edges_pass():   # each limit itself is allowed
    for edge in ({'cf.top': 14}, {'cf.text_y': 207}, {'cf.bright2': 192}):
        theme.validate({**oled(), **edge}, 't.xml')
    theme.validate({**oled(), 'cf.eye': 2046, 'cf.x1': 1023, 'cf.xs': 0}, 't.xml')


def test_oled_shelf():  # its side covers' geometry from its keys; the frame holds the reflection and fits the page
    t = oled()
    eye, c, s = t['cf.eye'], math.cos(math.radians(t['cf.tilt'])), math.sin(math.radians(t['cf.tilt']))
    assert eye * c / s <= 3  # where a side cover's top and bottom edges meet, past the centre column
    def outer(n):
        z = eye + t['cf.z1'] + n * t['cf.zs'] - 80 * s
        return (t['cf.x1'] + n * t['cf.xs'] + 80 * c) * eye / z, 160 * eye / z
    for n, (x, h) in enumerate(((129.5, 162), (165, 162), (200.5, None))):
        assert abs(outer(n)[0] - x) <= 1 and (h is None or abs(outer(n)[1] - h) <= 1)
    assert (t['cf.horizon'], t['cf.swing'], t['cf.lift'], t['cf.eye_turn']) == (64, 2, 0, 0)  # straight out, as #329's lab
    assert t['cf.reflect_top'] == 0  # no reflection floor: the reflection's top at 0 of its cover's brightness
    assert t['cf.top'] + 160 + t['cf.reflect'] <= t['cf.view_h'] <= 320 - t['geom'].bar


def test_oled_caption_tails_end_on_the_list_end():
    """Cover Flow's artist line (coverflow.c, under the album's) in Inter Regular at artist_px: stb scales the face's
    ascent - descent to the px, the label centres that in its CF_ARTIST_H box and the baseline falls on a whole row, so a
    tail (g, the deepest of p, y, g, q, j) ends by the set's list end, as every list's last row does."""
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.ttLib import TTFont
    t = oled()
    font = TTFont(ipod.ROOT/'assets/fonts/Inter-Regular.ttf', lazy=True)
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()
    def depth(ch):
        pen = BoundsPen(glyphs)
        glyphs[cmap[ord(ch)]].draw(pen)
        return -pen.bounds[1]
    scale = t['cf.artist_px'] / (font['hhea'].ascent - font['hhea'].descent)
    top = t['cf.text_y'] + t['cf.artist_y'] + (O['CF_ARTIST_H'] - t['cf.artist_px']) // 2
    baseline = math.floor(top + font['hhea'].ascent * scale)
    tail = baseline + max(depth(ch) for ch in 'pygqj') * scale
    assert tail <= t['geom'].list_end  # window rows: the screen's y 282 under the 58 px status row


def test_emit_cf(tmp_path):
    theme.emit(theme.builtins(), tmp_path)
    text = (tmp_path/'themes.inc').read_text()
    assert text.count('.cf.eye = 256,') == 1 and text.count('.cf.text_y = 173,') == 1


# page_list's rows, detail rows and in-page title: 48/64/48 unless a theme sets them (open class).
def test_page_rows_defaults_and_oled():
    o = oled()
    assert (o['page_row'], o['page_detail'], o['page_title_h'], o['page_x']) == (32, 52, 0, 24)
    assert (o['page_cap_y'], o['page_cap_h'], o['page_det_y'], o['page_det_h']) == (4, 24, 31, 20)
    # a detail row's boxes hold its fonts (a label draws nothing in a shorter one)
    assert o['page_cap_h'] >= o['font_px[ROLE_TALL]'] and o['page_det_h'] >= o['font_px[ROLE_META]'] and o['page_row'] >= o['font_px[ROLE_TITLE]']
    d = resolved('')
    assert (d['page_row'], d['page_detail'], d['page_title_h'], d['page_x']) == (48, 64, 48, O['CF_EDGE'])
    assert (d['page_cap_y'], d['page_cap_h'], d['page_det_y'], d['page_det_h']) == (4, 32, 36, 24)
    t = resolved('<view name="page"><element name="rows" height="30" detail_height="56" detail_y="30" detail_h="24"/><element name="title" height="0"/></view>')
    theme.validate(t, 't.xml')
    assert (t['page_row'], t['page_detail'], t['page_title_h']) == (30, 56, 0)
    assert all(theme.SCHEMA[p].cls == 'open' for p in ('page.rows.height', 'page.rows.detail_height', 'page.title.height', 'page.rows.title_y'))


@pytest.mark.parametrize('body, message', [
    ('<view name="page"><element name="rows" height="19"/></view>', 't.xml:1: page.rows.height: 19 outside 20..64'),
    ('<view name="page"><element name="rows" detail_height="81"/></view>', 't.xml:1: page.rows.detail_height: 81 outside 20..80'),
    ('<view name="page"><element name="title" height="49"/></view>', 't.xml:1: page.title.height: 49 outside 0..48'),
    ('<view name="page"><element name="rows" detail_height="52" detail_y="36" detail_h="24"/></view>', 't.xml:1: page.rows: detail box ends at 60, past the 52 px row'),
    ('<view name="page"><element name="rows" detail_height="30" title_y="4" title_h="32" detail_y="16" detail_h="12"/></view>', 't.xml:1: page.rows: title box ends at 36, past the 30 px row'),
    ('<view name="page"><element name="rows" title_h="27"/></view><view name="list"><element name="row" tall_px="28"/></view>', 't.xml:1: page.rows.title_h: 27 is shorter than its 28 px font'),
    ('<view name="page"><element name="rows" detail_h="16"/></view><view name="list"><element name="row" meta_px="17"/></view>', 't.xml:1: page.rows.detail_h: 16 is shorter than its 17 px font'),
    ('<view name="page"><element name="rows" height="26"/></view><view name="list"><element name="row" title_px="27"/></view>', 't.xml:1: page.rows.height: 26 is shorter than its 27 px font'),
])
def test_page_rows_reject(body, message):
    assert error(lambda: theme.validate(resolved(body), 't.xml')) == message


def test_emit_page_rows(tmp_path):
    theme.emit(theme.builtins(), tmp_path)
    text = (tmp_path/'themes.inc').read_text()
    assert all(text.count(f'.{k} = {v},') == 1 for k, v in (('page_row', 32), ('page_detail', 52), ('page_title_h', 0), ('page_cap_y', 4)))


def oled_with(geometry):
    text = (THEMES/'oled/theme.xml').read_text()
    text = re.sub(r'(<theme\b[^>]*>)', r'\1' + geometry, text, count=1)
    root = theme.parse(text, 'oled.xml')
    t = theme.resolve(root, {'oled': root})
    theme.validate(t, 'oled.xml')
    return t


def test_oled_is_on_its_own_grid():
    o = oled()
    assert theme.geometry(o) == o['geom'] != STOCK_GRID and theme.built_geometry([o]) == theme.geometry(o)


@pytest.mark.parametrize('geometry, message', [
    ('<geometry bar="26"/>', 'one <geometry> per theme'),
    ('<geometry set="oled"/>', 'unknown key geometry.set')])
def test_geometry_rejects(geometry, message):
    with pytest.raises(theme.ThemeError, match=message):
        oled_with(geometry)


def test_built_geometry_is_the_themes_one_grid():
    o = oled()
    test = Geometry(name='test', pitch=28)
    assert theme.built_geometry([dict(o, geom=test)]) == test
    with pytest.raises(ValueError, match='2 grids: the build bakes one'):
        theme.built_geometry([o, dict(o, geom=test)])


def test_built_geometry_refuses_a_wide_value():
    with pytest.raises(ValueError, match='wide: pitch 32768 does not fit an immediate'):
        theme.built_geometry([dict(oled(), geom=Geometry(name='wide', pitch=0x8000))])


def test_a_nameless_scheme_is_every_scheme():
    t = resolved('<colorScheme sel.text="#ffffff"/>')  # no name: every scheme the theme has
    assert {s['sel_text'] for s in t['schemes']} == {0xffffff}
    assert resolved('<colorScheme name="cobalt" sel.text="#ffffff"/>')['schemes'][0]['sel_text'] == 0


def test_theme_level_halves():
    o = oled()
    assert (o['set_value_ink'], o['light']['set_value_ink']) == (0xababab, 0x5c5c5c)  # both halves stated
    assert theme.half(o, False, 0xffffff, 0)['set_value_ink'] == 0xababab and theme.half(o, True, 0xffffff, 0)['set_value_ink'] == 0x5c5c5c
    assert theme.half({**o, 'set_value_ink': 0}, False, 0xffffff, 0)['set_value_ink'] == theme.unflip(0x5c5c5c, 0xffffff, 0)  # one stated: the other derives
    assert error(resolved, '<view name="list"><element name="selection" light.radius="4"/></view>') == \
        't.xml:1: list.selection.radius: one value for both halves'


def test_scheme_mode_paper_ink():
    t = resolved('<colorScheme mode="light" light.paper="#ffffff" light.ink="#000000"/>')
    assert {(s['mode'], s['paper'], s['ink']) for s in t['schemes']} == {(1, 0xffffff, 0)}
    o = oled()
    assert {(s['mode'], s['paper'], s['ink']) for i, s in enumerate(o['schemes']) if o['accents'] >> i & 1} == {(0, 0xffffff, 0)}
    assert all(s == theme.zero_scheme() for i, s in enumerate(o['schemes']) if not o['accents'] >> i & 1)
    assert {(s['mode'], s['paper'], s['ink']) for s in resolved('')['schemes']} == {(0, 0xf4f4f4, 0x161616)}
    assert o['undeclared'] == []
    assert o['opts'][0] == opt('SETTING_LIGHT', preset=0, keep=2)
    assert not theme.shared(o) and theme.shared(resolved(''))


def test_halves():
    # a stated half is kept; the other is the flip (light) or its inverse (dark) of its neutrals by the
    # light paper and ink (here the defaults, #f4f4f4 and #161616); hues stay; unset stays unset
    s = resolved('<colorScheme name="cobalt" sel.text="#404040"/>')['schemes'][4]
    dark, light = theme.scheme_half(s, False), theme.scheme_half(s, True)
    assert (dark['paper'], dark['ink'], light['paper'], light['ink']) == (0, 0xffffff, 0xf4f4f4, 0x161616)
    assert (dark['sel_text'], light['sel_text']) == (0x404040, 0xbdbdbd)     # 0xf4 - 0x40 * (0xf4 - 0x16) // 255
    s = resolved('<colorScheme name="cobalt" light.sel.text="#c0c0c0"/>')['schemes'][4]
    assert (theme.scheme_half(s, False)['sel_text'], theme.scheme_half(s, True)['sel_text']) == (0x3b3b3b, 0xc0c0c0)  # (0xf4 - 0xc0) * 255 // (0xf4 - 0x16)
    s = resolved('<colorScheme name="cobalt" sel.text="#ffffff" light.sel.text="#ffffff"/>')['schemes'][4]
    assert (theme.scheme_half(s, False)['sel_text'], theme.scheme_half(s, True)['sel_text']) == (0xffffff, 0xffffff)  # both stated
    assert theme.scheme_half(theme.zero_scheme(), True)['sel_text'] == 0    # unset stays unset
    assert theme.flip(0x94c7de, 0xffffff, 0) == 0x94c7de
    assert theme.flip(0x636163, 0xffffff, 0) == 0x9c9e9c and theme.unflip(0x9c9e9c, 0xffffff, 0) == 0x636163


def test_a_hue_stated_in_one_half_is_the_others():
    # flip and its inverse turn neutrals only: a hue reads the same on either paper
    s = resolved('<colorScheme name="cobalt" light.sel.text="#94c7de"/>')['schemes'][4]
    assert theme.scheme_half(s, False)['sel_text'] == 0x94c7de
    s = resolved('<colorScheme name="cobalt" sel.text="#94c7de"/>')['schemes'][4]
    assert theme.scheme_half(s, True)['sel_text'] == 0x94c7de
    assert theme.unflip(0x94c7de, 0xffffff, 0) == 0x94c7de


@pytest.mark.parametrize('body, message', [
    ('<colorScheme name="cobalt" light.paper="#ff0000"/>', 'scheme.paper: expected a grey'),
    ('<colorScheme light.ink="#000001"/>', 'scheme.ink: expected a grey'),
    ('<colorScheme light.paper="#101010" light.ink="#f0f0f0"/>', 'scheme.paper: must be lighter than ink'),
    ('<colorScheme mode="light"/>', 'light: a scheme with its own mode has no Light Mode option'),
])
def test_validate_rejects_mode(body, message):
    assert error(theme.validate, resolved(body), 't.xml') == f't.xml:1: {message}'


@pytest.mark.parametrize('body, message', [
    ('<colorScheme paper="#ffffff"/>', "scheme.paper: the dark half's is #000000; write light.paper"),
    ('<colorScheme dark.ink="#ffffff"/>', "scheme.ink: the dark half's is #ffffff; write light.ink"),
    ('<colorScheme name="cobalt" light.muted="#ffffff"/>', 'scheme.muted: one value for both halves'),
    ('<colorScheme name="cobalt" dark.mode="dark"/>', 'scheme.mode: one value for both halves'),
    ('<colorScheme name="cobalt" sel.text="#111111" dark.sel.text="#222222"/>', 'scheme.sel.text: stated twice (sel.text and dark.sel.text)'),
    ('<view name="list"><element name="selection" light.inset="4"/></view>', 'list.selection.inset: one value for both halves'),
    ('<view name="settings"><element name="values" ink="#111111" dark.ink="#222222"/></view>', 'settings.values.ink: stated twice (ink and dark.ink)'),
])
def test_resolve_rejects_halves(body, message):
    assert error(resolved, body) == f't.xml:1: {message}'


def test_emit_mode_and_undeclared(tmp_path):
    inc = geom_inc(tmp_path, [oled(), on_oleds_set(resolved(C, 'id="t" name="T" index="1" extends="base"'))])
    # a theme whose halves differ only by the flip shares one array for both
    assert '.light = t_schemes,' in inc and 't_light' not in inc
    assert '.mode = SCHEME_DERIVE, .paper = 0xf4f4f4, .ink = 0x161616' in inc
    # OLED: its dark rows in oled_schemes, its light rows (white paper, black ink) in oled_light
    dark, light = (inc.split(f'static const scheme_t oled_{a}[] = {{')[1].split('};')[0] for a in ('schemes', 'light'))
    assert '.paper = 0x000000, .ink = 0xffffff' in dark and '.paper = 0xffffff, .ink = 0x000000' in light
    assert '.light = oled_light,' in inc and '_Static_assert(sizeof oled_light == sizeof oled_schemes' in inc
    assert '.undeclared = 0,' in inc
    assert '.undeclared = (1 << SETTING_LIGHT) | (1 << SETTING_HOME) | (1 << SETTING_BATTERY),' in inc


@pytest.mark.parametrize('body, message', [
    ('<colorScheme name="sky" base="teal" label="Sky"/>', 'sky: unknown base teal'),
    ('<colorScheme name="sky" base="cobalt"/>', 'sky: an own scheme needs a label'),
    ('<colorScheme name="sky" base="cobalt" label=""/>', 'sky: an own scheme needs a label'),
    ('<colorScheme name="cobalt" base="cobalt" label="C"/>', 'cobalt: an ACCENTS row takes no base'),
    ('<colorSchemes accents="graphite" derive="gloss"/>', 'unknown key colorSchemes.derive'),
    *((f'<colorScheme name="sky" base="cobalt" label="{label}"/>', 'sky: label: expected 1..48 bytes, no control characters')
      for label in ('A\x7fB', 'A\x85B', 'x' * 49, 'é' * 25)),  # as a theme's name
])
def test_own_scheme_rejects(body, message):
    assert error(resolved, body) == f't.xml:1: {message}'


def test_own_scheme_label_takes_48_bytes():
    t = resolved(f'<colorScheme name="sky" base="cobalt" label="{"é" * 24}"/>')
    assert theme.scheme_names(t)[10:] == ['sky'] and t['schemes'][10]['label'] == 'é' * 24


def test_own_scheme_restated_is_its_row():
    # a theme on OLED restating its Blue, OLED's own scheme, changes that row and adds none
    t = resolved('<colorScheme name="oled-blue" base="cobalt" label="Blue" muted="#123456"/>',
                 'id="t" name="T" index="2" extends="oled"')
    assert theme.scheme_names(t)[10:] == ['oled-blue', 'oled-arterial'] and t['schemes'][10]['muted'] == 0x123456
    assert t['accents'] == 3 << 10  # OLED's: its Blue and Arterial


def test_emit_own_scheme(tmp_path):
    inc = geom_inc(tmp_path, theme.builtins())
    assert inc.split('static const scheme_t oled_schemes[] = {')[1].split('\n')[11].startswith('    { .tones = oled_tones[0], ')
    assert inc.split('static const scheme_t oled_schemes[] = {')[1].split('\n')[12].startswith('    { .tones = oled_tones[1], ')
    assert '.label = "Blue" }' in inc and '.label = "Arterial" }' in inc and inc.count('.n_schemes = 12,') == 1
    assert inc.count('/ sizeof *oled_schemes >= ACCENT_N') == 1


def test_selection_shape_defaults_are_today_s():
    """Unstated, the bar is a pill SEL_INSET in; OLED states its own."""
    t = resolved('')
    assert (t['sel_radius'], t['sel_inset']) == (theme.O['SEL_MAX_RADIUS'], theme.O['SEL_INSET'])


def test_selection_shape_resolves():
    t = resolved('<view name="list"><element name="selection" radius="6" inset="8"/></view>')
    theme.validate(t, 't.xml')
    assert (t['sel_radius'], t['sel_inset']) == (6, 8)


@pytest.mark.parametrize('body, message', [
    ('<view name="list"><element name="selection" radius="161"/></view>', 'list.selection.radius: 161 outside 0..160'),
    ('<view name="list"><element name="selection" inset="65"/></view>', 'list.selection.inset: 65 outside 0..64'),
])
def test_selection_shape_rejects(body, message):
    assert error(resolved, body) == f't.xml:1: {message}'


def test_track_default_is_today_s():
    assert resolved('')['np_track_rgb'] == theme.O['TRACK_COLOR']
    assert oled()['np_track_rgb'] == 0x333333  # OLED's own Track grey (oled-design.md Colour)


@pytest.mark.parametrize('text, rgb', [('#262626', 0x262626), ('#000000', 0)])  # black is a colour here
def test_track_resolves(text, rgb):
    t = resolved(f'<view name="playing"><element name="scrubber" track="{text}"/></view>')
    theme.validate(t, 't.xml')
    assert t['np_track_rgb'] == rgb


def test_track_rejects():
    body = '<view name="playing"><element name="scrubber" track="grey"/></view>'
    assert error(resolved, body) == 't.xml:1: playing.scrubber.track: expected #rrggbb'


# An OLED-style theme: one own scheme, an exact blue, offered alone; a flat bar inset 8 with 6 px ends; a grey track.
OLED_BLUE = '#2f5bea #2f5bea #5a7cee #5a7cee #2f5bea'
OLED = (f'<colorScheme name="oled-blue" base="cobalt" label="Blue" tones="{OLED_BLUE}"/><colorSchemes accents="oled-blue"/>'
        '<option setting="accent" key="OLED_ACCENT" default="oled-blue"/>'
        '<view name="list"><element name="selection" radius="6" inset="8"/></view>'
        '<view name="playing"><element name="scrubber" track="#262626"/></view>')


def test_own_scheme_states_its_tones(tmp_path):
    t = resolved(OLED)
    theme.validate(t, 't.xml')
    blue = t['schemes'][10]
    assert theme.scheme_names(t)[10:] == ['oled-blue'] and t['accents'] == 1 << 10
    assert blue['tones'] == (0x2f5bea, 0x2f5bea, 0x5a7cee, 0x5a7cee, 0x2f5bea) and blue['base'] == 4
    assert not any('tones' in s for s in t['schemes'][:10])  # the ACCENTS rows keep theirs
    assert (t['sel_radius'], t['sel_inset'], t['np_track_rgb']) == (6, 8, 0x262626)
    inc = geom_inc(tmp_path, [t])
    assert 'static const unsigned t_tones[][5] = {\n    { 0x2f5bea, 0x2f5bea, 0x5a7cee, 0x5a7cee, 0x2f5bea },\n};\n' \
           'static const scheme_t t_schemes[] = {' in inc
    rows = inc.split('static const scheme_t t_schemes[] = {\n')[1].split('\n};')[0].splitlines()
    assert [r.split(',')[0] for r in rows] == [f'    {{ .tones = accents[{v}]' for v in range(10)] + ['    { .tones = t_tones[0]']


def test_own_scheme_without_tones_keeps_its_base_s():
    t = resolved('<colorScheme name="sky" base="cobalt" label="Sky"/>')
    assert 'tones' not in t['schemes'][10]


def test_builtins_state_no_tones(tmp_path):
    """Every built-in scheme's tones stay its ACCENTS row but OLED's Blue and Arterial, its own rows: only OLED writes a
    tones table."""
    inc = geom_inc(tmp_path, theme.builtins())
    assert inc.count('_tones[][5] = {') == 1 and 'static const unsigned oled_tones[][5] = {' in inc
    assert [(t['id'], s['name']) for t in theme.builtins() for s in t['schemes'] if 'tones' in s] == [('oled', 'oled-blue'), ('oled', 'oled-arterial')]


def test_arterial_is_the_default_accent_at_the_panel_s_level():
    """The owner's #B8121B as the 6-bit panel shows it: (184, 20, 28), the nearer of its two neighbours by Delta E
    2000 and the level ui/fb.c narrow rounds #B8121B to; the ACCENTS row and OLED's own scheme agree on it."""
    assert theme.panel_level(0xb8121b) == theme.DEFAULT_ACCENT == 0xb8141c
    row = theme.ACCENT_TONES[theme.ACCENT_NAMES.index('arterial')]
    assert row == [0xb8141c, 0xb8141c, 0xc03038, 0xc03038, 0xc03038] and all(theme.panel_level(c) == c for c in row)
    assert row[2] == theme.panel_level(theme.mix_rgb(row[0], 0xffffff, 0.12))  # the light tone, as every row's
    assert theme.ACCENT_NAMES[-1] == 'arterial'  # appended: a stored ACCENT keeps its meaning


@pytest.mark.parametrize('top', [0xb8141c, 0x2160d8, *(r[0] for r in theme.ACCENT_TONES)])
def test_accent_set_reads(top):
    """Each accent's companions hold their contrast: text 4.5:1 on black and on white, the caption 4.5:1 on the
    selection, else white; each at a panel level but white."""
    pressed, dark, light, caption = theme.accent_set(top)
    assert theme.contrast(dark, 0) >= 4.5 and theme.contrast(light, 0xffffff) >= 4.5
    assert theme.contrast(caption, top) >= 4.5 or caption == 0xffffff
    assert all(theme.panel_level(c) == c for c in (pressed, dark, light, caption) if c != 0xffffff)
    assert theme.luminance(pressed) >= theme.luminance(top)


def test_ui_accents_follow_the_theme():
    """tokens.h's accents for q2ui: OLED's key and default, Blue and Arterial rows, zero rows for what it does not
    offer; the tokens' accent text and caption are the default's."""
    h = theme.token_header()
    assert '#define UI_ACCENT_KEY "OLED_ACCENT"\n#define UI_ACCENT_N 12\n#define UI_ACCENT_DEFAULT 11\n' in h
    rows = h.split('#define UI_ACCENTS {')[1].split('}}')[0]
    assert rows.count('{0}') == 10 and '{0x2160d8, ' in rows and '{0xb8141c, ' in rows
    assert theme.TOKENS['accent_text'] == theme.accent_set(theme.DEFAULT_ACCENT)[1:3]
    assert theme.TOKENS['status_blue'] == (0x2160d8, 0x2160d8)


@pytest.mark.parametrize('body, message', [
    (f'<colorScheme name="cobalt" tones="{OLED_BLUE}"/>', "cobalt: tones are an own scheme's (name, base, label)"),
    (f'<colorScheme tones="{OLED_BLUE}"/>', "colorScheme: tones are an own scheme's (name, base, label)"),
    ('<colorScheme name="sky" base="cobalt" label="Sky" tones="#2f5bea #2f5bea #5a7cee #5a7cee"/>',
     'sky: tones: expected 5 of #rrggbb (top, bottom, light, red, highlight)'),
    ('<colorScheme name="sky" base="cobalt" label="Sky" tones="#2f5bea #2f5bea #5a7cee #5a7cee blue"/>',
     'sky: tones: expected 5 of #rrggbb (top, bottom, light, red, highlight)'),
])
def test_tones_reject(body, message):
    assert error(resolved, body) == f't.xml:1: {message}'


SONGARTIST = own('SONGARTIST', 'Song Artist', ('Inline', 'Selected Row', 'Right', 'Off'), default=0, preset=0)
SONGLENGTH = own('SONGLENGTH', 'Song Length', ('Albums', 'All Lists', 'Off'), default=0, preset=0)
NPTIME = own('NPTIME', 'Track Time', ('Remaining', 'Total'), default=0, preset=0)


def test_own_option():
    o = oled()
    assert [x for x in o['opts'] if x.get('key')] == [SONGARTIST, SONGLENGTH, NPTIME]
    assert theme.theme_keys([o]) == {'OLED_ACCENT': (12, 11, None), 'SONGARTIST': (4, 0, SONGARTIST), 'SONGLENGTH': (3, 0, SONGLENGTH), 'NPTIME': (2, 0, NPTIME)}
    assert {'ACCENT', 'HOME', 'BATTERY', 'THEME', 'LIGHT'} <= theme.PAYLOAD_KEYS and 'VIS' not in theme.PAYLOAD_KEYS
    assert not {'SONGARTIST', 'OLED_ACCENT'} & theme.PAYLOAD_KEYS and set(theme.ACCENT_KEYS) == {'ACCENT'}


@pytest.mark.parametrize('body, message', [
    ('<option key="NPTIME" label="Art"><value label="Off"/></option>', 'NPTIME: 2 to 10 values expected'),
    ('<option key="NPTIME" label="Art"><value label="Off"/><value label="Off"/></option>', 'NPTIME: duplicate value Off'),
    ('<option key="ART" label="Art"><value label="Off"/><value label="On"/></option>', 'ART: a plain choice the payload does not read (PLAIN_KEYS)'),
    ('<option key="NPTIME" label="Art"><value label="Off" set="ipod"/><value label="On"/></option>', 'unknown key value.set'),
    ('<option key="NPTIME" label="Art"><value label="Off" tint="1"/><value label="On"/></option>', 'unknown key value.tint'),
    ('<option key="NPTIME" label="Art"><value themes="picker"/><value label="On"/></option>', 'unknown key value.themes'),
    (f'<option key="NPTIME" label="Art" default="Dim">{V}</option>', 'NPTIME: unknown value Dim'),
    (f'<option key="art" label="Art">{V}</option>', 'option.key: expected [A-Z][A-Z0-9_]*'),
    (f'<option key="LIGHT" label="Light">{V}</option>', "LIGHT: the payload's key"),
    (f'<option key="ART" label="">{V}</option>', 'ART: label expected 1..24 bytes, no control characters'),
    ('<option key="NPTIME" label="Art"><value label=""/><value label="On"/></option>',
     'NPTIME: value label expected 1..24 bytes, no control characters'),
    ('<option setting="home"><value label="Off"/></option>', 'home: a payload setting takes no <value>'),
    ('<option setting="home" label="H"/>', 'home: a payload setting takes no label'),
    (f'<option key="NPTIME" label="Art" preset="Dim">{V}</option>', 'NPTIME: unknown value Dim'),
    (f'<option key="NPTIME" label="Art" keep="Dim">{V}</option>', 'NPTIME: unknown value Dim'),
    (f'<option key="ART" label="Art">{V}<view name="bar"/></option>', 'unknown element <view>'),
])
def test_own_option_rejects(body, message):
    assert error(resolved, body) == f't.xml:1: {message}'


def test_payload_option_values():
    # values: the bitmask of the values the row takes; default: what a stored value outside them reads as
    t = resolved('<option setting="battery" values="icon inside" default="inside" preset="icon"/><option setting="accent" key="ACCENT"/>')
    assert t['opts'][0] == opt('SETTING_BATTERY', default=3, preset=0, values=0b1001)
    assert next(o for o in oled()['opts'] if o['setting'] == 'SETTING_BATTERY') == opt('SETTING_BATTERY', default=0, values=0b11)


@pytest.mark.parametrize('body, message', [
    ('<option setting="battery" values="icon inside"/>', 'battery: values need a default, what a stored value outside them reads as'),
    ('<option setting="battery" values="icon" default="icon"/>', 'battery: 2 or more values expected'),
    ('<option setting="battery" values="icon icon" default="icon"/>', 'battery: 2 or more values expected'),
    ('<option setting="battery" values="icon dim" default="icon"/>', 'battery: unknown value dim'),
    ('<option setting="battery" values="icon inside" default="both"/>', 'battery: default both is not in values'),
    ('<option setting="battery" values="icon inside" default="icon" preset="both"/>', 'battery: preset both is not in values'),
    ('<option setting="light" values="off on" default="on" keep="auto"/>', 'light: keep auto is not in values'),
    ('<option setting="accent" key="ACCENT" values="cobalt green" default="cobalt"/>', 'accent: colorSchemes states its values'),
    (f'<option key="ART" label="Art" values="Off On">{V}</option>', 'ART: its <value>s are its values'),
])
def test_payload_option_values_rejects(body, message):
    assert error(resolved, body) == f't.xml:1: {message}'


def test_builtins_rejects_a_shared_own_key(tmp_path):
    """Themes sharing an own key share its stored value, so they must declare the same values and default;
    each presets it as it likes (as each presets LIGHT)."""
    def put(folder, index, label, presets=''):
        (tmp_path/folder).mkdir(exist_ok=True)
        (tmp_path/folder/'theme.xml').write_text(
            f'<theme format="1" id="{folder}" name="{folder}" index="{index}">{BASE_BODY}'
            f'<option key="NPTIME" label="{label}"{presets}>{V}</option></theme>')
    put('a', 0, 'Art', ' preset="On"'); put('b', 1, 'Art', ' preset="Off" keep="On"')
    assert list(theme.theme_keys(theme.builtins(tmp_path))) == ['NPTIME']  # one key, one slot each
    assert {t['id']: (t['opts'][-1]['preset'], t['opts'][-1]['keep']) for t in theme.builtins(tmp_path)} == {'a': (1, -1), 'b': (0, 1)}
    put('b', 1, 'Row Art')
    assert error(theme.builtins, tmp_path) == "b/theme.xml:1: NPTIME is a's, with other values or default"


def test_emit_own_options(tmp_path):
    inc = geom_inc(tmp_path, theme.builtins())
    opts = inc.split('static const option_t oled_opts[] = {\n')[1].split('};')[0].splitlines()
    assert opts[-3:] == [f'    {{ .setting = SETTING_OWN + {i}, .def = 0, .preset = 0, .keep = -1, .values = 0x0 }},' for i in (1, 2, 3)]
    assert inc.count('.accent_slot = &st.own[0],') == 1 and '.accent_key = "OLED_ACCENT",' in inc
    assert ('const theme_key_t theme_keys[THEME_KEYS] = {\n'
            '    { .key = "OLED_ACCENT", .label = 0, .names = 0, .n = 12, .def = 11 },\n'
            '    { .key = "SONGARTIST", .label = "Song Artist", .names = (const char *const[]){"Inline", "Selected Row", "Right", "Off"}, .n = 4, .def = 0 },\n'
            '    { .key = "SONGLENGTH", .label = "Song Length", .names = (const char *const[]){"Albums", "All Lists", "Off"}, .n = 3, .def = 0 },\n'
            '    { .key = "NPTIME", .label = "Track Time", .names = (const char *const[]){"Remaining", "Total"}, .n = 2, .def = 0 },\n};') in inc
    assert '#define OWN_OLED_ACCENT 0\n#define OWN_SONGARTIST 1\n#define OWN_SONGLENGTH 2\n#define OWN_NPTIME 3\n' in inc


def test_oled_grid_from_its_theme_file():
    """OLED's grid is its theme.xml's <geometry>: it states every Geometry field but a few STOCK_GRID already holds as
    OLED wants them. No tool names it."""
    o = oled()
    g = theme.geometry(o)
    decl, = [e.attrs for e in theme.load(THEMES/'oled'/'theme.xml').children if e.tag == 'geometry']
    unstated = {f.name for f in dataclasses.fields(Geometry)} - {'name'} - set(decl)
    assert all(getattr(g, f) == getattr(STOCK_GRID, f) for f in unstated) and g.name == 'oled'
    assert not hasattr(ipod, 'OLED')


def test_geometry_declared():
    # An omitted field is the extended theme's, or STOCK_GRID's for a theme that extends none; an extending theme
    # that states none keeps its base's.
    t = resolved('<geometry bar="26" text_x="20"/>')
    assert theme.geometry(t) == dataclasses.replace(STOCK_GRID, name='t', right_x=28, bar=26, text_x=20)
    assert theme.built_geometry([t]) == theme.geometry(t)
    u = resolved('', 'id="u" name="U" index="3" extends="oled"')
    assert theme.geometry(u) == theme.geometry(oled())


def test_geometry_ranges():
    """Every Geometry field has a range, so a task that adds a field adds one; STOCK_GRID and OLED's grid lie inside."""
    assert set(theme.GEOM_RANGES) == {f.name for f in dataclasses.fields(Geometry)} - {'name'}
    assert [theme.check_set(g) for g in (STOCK_GRID, theme.geometry(oled()))] == [None, None]
    assert theme.check_set(dataclasses.replace(STOCK_GRID, name='x', set_rows=5)) == 'geometry.set_rows: 5 outside 1..4'


@pytest.mark.parametrize('body, message', [
    ('<geometry/><geometry/>', 'one <geometry> per theme'),
    ('<geometry name="pico"/>', 'unknown key geometry.name'),
    ('<geometry extends="base"/>', 'unknown key geometry.extends'),
    ('<geometry set="ipod"/>', 'unknown key geometry.set'),
    ('<geometry tint="1"/>', 'unknown key geometry.tint'),
    ('<geometry pitch="2x"/>', 'geometry.pitch: expected an integer'),
    ('<geometry bar="24"/>', 'geometry.bar: 24 outside 26..40'),
    ('<geometry pitch="28"/>', 'geometry.body: 68 outside 24..28'),
    ('<geometry art_inset="9"/>', 'geometry.art_inset: 9 outside 0..8'),
    ('<geometry pitch="28" body="28"/>', 'geometry.text_art: 52 outside 0..28'),
    ('<geometry home_row="41"/>', 'geometry.home_row: 41 outside 28..40'),
    ('<geometry split_art="186"/>', 'geometry.split_art: 186 outside 187..260'),
    ('<geometry split_row="204"/>', 'geometry.split_row: 204 outside 120..203'),
    ('<geometry split_art_up="31"/>', 'geometry.split_art_up: 31 outside 0..30'),
    ('<geometry np_art="201"/>', 'geometry.np_art: 201 outside 0..200'),
])
def test_geometry_declare_rejects(body, message):
    assert error(resolved, body) == f't.xml:1: {message}'


def geom_inc(tmp_path, themes):
    theme.emit(themes, tmp_path)
    return (tmp_path/'themes.h').read_text() + (tmp_path/'themes.inc').read_text()


def test_emit_geom_words(tmp_path):
    t = resolved('')  # the test base's grid: STOCK_GRID's, with a right column
    inc = geom_inc(tmp_path, [t])
    names = ipod.VALUE_NAMES
    assert 'enum { ' + ', '.join(f'G_{n.upper()}' for n in names) + ', G_N };' in inc
    row = ', '.join(str(ipod.immediate_values(STOCK_GRID)[n]) for n in names)
    assert f'const volatile unsigned short geom_words[G_N] = {{{row}}};' in inc
    assert all(s in inc for s in geom_vals_text(theme.geometry(t)))
    assert '#define THEME_MOTION_OFF 4\n#define SCHEME_MODE_OFF 4' in inc
    for gone in ('GEOM_N', 'boot_sites', 'boot_values', 'geom_names', 'ui_redirects', '.geom = '):
        assert gone not in inc


def geom_vals_text(g):
    return ('enum { ' + ', '.join(f'V_{n.upper()}' for n in ipod.GEOM_VALS) + ', V_N };',
            'geom_vals[V_N] = {' + ', '.join(str(getattr(g, n)) for n in ipod.GEOM_VALS) + '};')


def test_emit_geom_vals(tmp_path):
    inc = geom_inc(tmp_path, theme.builtins())
    g = theme.geometry(oled())
    assert all(s in inc for s in geom_vals_text(g))
    assert ipod.GEOM_VALS[:3] == ('bar', 'set_row', 'batt_w')
    assert [getattr(g, n) for n in ipod.GEOM_VALS[:3]] == [58, 32, 25]
    words = ', '.join(str(ipod.immediate_values(g)[n]) for n in ipod.VALUE_NAMES)
    assert f'geom_words[G_N] = {{{words}}};' in inc  # OLED's, which patch_code bakes


def test_emit_refuses_a_second_grid(tmp_path):
    t = resolved('')
    with pytest.raises(ValueError, match='2 grids: the build bakes one'):
        geom_inc(tmp_path, [t, dict(t, id='u', index=1, geom=Geometry(name='test', pitch=28))])


def test_easings_are_awtk():
    assert len(theme.EASINGS) == 25 and theme.EASINGS[O['SLIDE_EASING']] == 'sin_inout' \
        and theme.EASINGS[O['EASE_CUBIC_OUT']] == 'cubic_out' and theme.EASINGS[2] == 'quadratic_out' \
        and theme.EASINGS[19] == 'back_out' and theme.EASINGS[23] == 'bounce_inout' \
        and theme.EASINGS[O['EASE_FOLLOW']] == 'follow'  # ours, one past AWTK's 24


def test_motion_today(tmp_path):  # OLED: the asset hint kept (0), offsets.inc's numbers (ROLE_MACROS)
    o = oled()
    assert {k.field: o[k.field] for k in theme.SCHEMA.values() if k.cls == 'motion'} == MOTION_TODAY
    inc = geom_inc(tmp_path, theme.builtins())
    assert inc.count('.motion[MOTION_PAGE].hint = 0,') == 1 and ipod.SLIDE == 'htranslate(duration=220)'
    for f, v in (('motion[MOTION_SWAP].ms', 220), ('motion[MOTION_SWAP].easing', 8), ('motion[MOTION_STEP].ms', 200),
                 ('motion[MOTION_STEP_FAST].ms', 120), ('motion[MOTION_STEP].easing', 8), ('recall_ms', 300)):
        assert inc.count(f'.{f} = {v},') == 1, f
    assert inc.count('.motion[MOTION_COVERFLOW].hint = 0,') == 1 and inc.count('.motion[MOTION_INPLACE].ms = 0,') == 1
    assert '.anim' not in inc
    assert '.hint = "' not in inc  # no window animates beyond stock's own slide: no push, no dialog fade
    # theme.h's order is the emitted kinds', which test/patch.py indexes by
    motions = re.search(r'enum \{\s*MOTION_PAGE.*?\}', (theme.ROOT/'patch/theme.h').read_text(), re.S)
    assert motions
    assert re.findall(r'MOTION_\w+', motions[0]) \
        == [f'MOTION_{k.upper()}' for k in (*theme.MOTION_WINDOW, *theme.MOTION_WIDGET)] + ['MOTION_N']


@pytest.mark.parametrize('kind', [k for k in (*theme.MOTION_WINDOW, *theme.MOTION_WIDGET) if not k.startswith('coverflow_step')])
def test_follow_is_cover_flow_steps_alone(kind):  # stock's easing_get has no such id, and a window's anim_hint no such name
    with pytest.raises(theme.ThemeError, match=rf'motion\.{kind}\.easing: follow is only for coverflow_step and coverflow_step_fast'):
        theme.validate({**oled(), f'motion.{kind}.easing': theme.EASINGS.index('follow')}, 't.xml')


def test_motion_composes_hints(tmp_path):
    t = resolved('<view name="motion"><element name="page" ms="300" easing="sin_inout"/>'
                 '<element name="coverflow" anim="slide_left" ms="230" easing="cubic_out"/>'  # the animator's default: no easing=
                 '<element name="playing" anim="fade" easing="back_out" ms="0"/>'  # 0: the window as it is
                 '<element name="step" ms="250" easing="quadratic_out"/><element name="inplace" ms="300"/>'
                 '<element name="recall" ms="0"/></view>')
    assert t['motion.playing.anim'] == 2 and t['motion.step.easing'] == 2
    inc = geom_inc(tmp_path, [*theme.builtins(), on_oleds_set(t)])
    for line in ('.motion[MOTION_PAGE].hint = "htranslate(duration=300,easing=sin_inout)",',
                 '.motion[MOTION_COVERFLOW].hint = "slide_left(duration=230)",',
                 '.motion[MOTION_STEP].ms = 250,', '.motion[MOTION_STEP].easing = 2,',
                 '.motion[MOTION_INPLACE].ms = 300,', '.recall_ms = 0,'):
        assert inc.count(line) == 1, line
    assert inc.count('.motion[MOTION_PLAYING].hint = 0,') == 2


@pytest.mark.parametrize('element, message', [
    ('<element name="page" easing="springy"/>', r'motion\.page\.easing: springy'),
    ('<element name="page" ms="2001"/>', r'motion\.page\.ms: 2001 outside'),
    ('<element name="coverflow" anim="spin"/>', r'motion\.coverflow\.anim: spin'),
    ('<element name="step" anim="fade"/>', r'motion\.step\.anim'),  # widget kinds take no animator
    ('<element name="bump" px="6"/>', r'unknown element motion\.bump'),  # a list's end has no bump
])
def test_motion_rejects(element, message):
    with pytest.raises(theme.ThemeError, match=message):
        resolved(f'<view name="motion">{element}</view>')


def test_bar_font_size_is_the_grids_bar_px(tmp_path):
    """The bar's label is laid out at its grid's bar_px (tools/ipod.py status_row): its text size must match."""
    xml = (THEMES/'oled/theme.xml').read_text()
    assert xml.count('font="inter-medium" font_size="18"') == 1
    (tmp_path/'oled').mkdir(); (tmp_path/'oled'/'theme.xml').write_text(xml.replace('font="inter-medium" font_size="18"', 'font="inter-medium" font_size="24"'))
    with pytest.raises(theme.ThemeError, match=r"bar.title.font_size 24 is not the geometry's bar_px 18$"):
        theme.builtins(tmp_path)


# The scroll indicator's checks, on a theme with no base (the base states one): they run before the set's right column's
NO_SB = BASE_GEOM + A + CF_LO


@pytest.mark.parametrize('ends, message', [('top="58" bottom="261"', 'list.scrollbar: top 58 is under the glass (inset 4 > right 2 + 1)'),
                                           ('top="59" bottom="263"', 'list.scrollbar: bottom 263 is under the glass (inset 4 > right 2 + 1)')])
def test_scrollbar_clears_the_glass(ends, message):
    t = resolved(NO_SB + f'<view name="list"><element name="scrollbar" w="4" right="2" {ends}/></view>', N)
    assert error(theme.validate, t, 't.xml') == f't.xml:1: {message}'


@pytest.mark.parametrize('attrs, message', [('top="100" bottom="100" min="16" thumb="#8f98a8"', 'list.scrollbar: top 100 is not above bottom 100'),
                                            ('top="200" bottom="100" min="16" thumb="#8f98a8"', 'list.scrollbar: top 200 is not above bottom 100'),
                                            ('top="59" bottom="261" thumb="#8f98a8"', 'list.scrollbar: no min'),
                                            ('top="59" bottom="261" min="16"', 'list.scrollbar: no thumb colour')])
def test_scrollbar_rejects(attrs, message):
    t = resolved(NO_SB + f'<view name="list"><element name="scrollbar" w="4" right="2" {attrs}/></view>', N)
    assert error(theme.validate, t, 't.xml') == f't.xml:1: {message}'


def test_scrollbar_at_the_glass_passes():
    theme.validate(resolved(NO_SB + '<view name="list"><element name="scrollbar" w="4" right="2" top="59" bottom="261" min="16" thumb="#8f98a8"/></view>', N), 't.xml')
    theme.validate(resolved(NO_SB + '<view name="list"><element name="scrollbar" w="4" right="2" top="59" bottom="261" min="16" light.thumb="#8f98a8"/></view>', N), 't.xml')


def test_oled_states_its_grid_type_and_colour(tmp_path):
    """OLED, the one theme (index 0, no base), draws its own art on its own oled set (docs/oled-design.md, the
    mock-ups chosen on 2026-11-13): a status row centred on y 23 in a 58 px bar, 32 px rows from y 58 with the
    text at 24 and the right column 28 from the edge, seven rows ending at y 282; Inter in every row role, at AWTK's
    sizes (1.21 times the design's em); its own Blue, five equal tones, under its own accent key."""
    o = oled()
    g = theme.geometry(o)
    assert (o['id'], o['index'], o['art_id']) == ('oled', 0, 'oled') and g.name == 'oled' and theme.check_set(g) is None
    assert (g.bar, g.status_y, g.pitch, g.set_row, g.home_row, g.text_x, g.right_x, g.list_end) == (58, 23, 32, 32, 32, 24, 28, 224)
    assert g.bar + g.list_end == 282 and g.list_end // g.pitch == 7 and ipod.fit_rows(g, 0, g.art_pitch) == 320 - 58
    assert ipod.corner_inset(g.bar + g.list_end) + ipod.CORNER_SLACK <= g.text_x
    fonts = {k: v for k, v in o.items() if k.startswith('font')}
    inter, medium = (list(theme.FONTS).index(f) for f in ('inter', 'inter-medium'))
    assert fonts == {'font[ROLE_TITLE]': inter, 'font[ROLE_META]': inter, 'font_px[ROLE_TITLE]': 24, 'font_px[ROLE_META]': 18,
                     'font[ROLE_TALL]': inter, 'font_px[ROLE_TALL]': 24, 'font[ROLE_NP]': medium, 'font_px[ROLE_NP]': 27,
                     'font[ROLE_INFO]': inter, 'font_px[ROLE_INFO]': 18, 'font[ROLE_HOME]': inter, 'font_px[ROLE_HOME]': 24,
                     'font[ROLE_BAR]': medium, 'font_px[ROLE_BAR]': 18}
    blue, arterial = o['schemes'][10:]
    assert theme.scheme_names(o)[10:] == ['oled-blue', 'oled-arterial'] and o['accents'] == 3 << 10 and blue['tones'] == (0x2160d8,) * 5
    assert arterial['tones'] == (0xb8141c,) * 5 and theme.accent_default(o) == 11 and arterial['label'] == 'Arterial'
    assert (blue['sel_text'], blue['light']['sel_text'], blue['muted'], blue['paper'], blue['ink']) == (0xffffff, 0xffffff, 0x858585, 0xffffff, 0)
    assert (o['set_text'], o['set_switch'], o['set_value_ink'], o['light']['set_value_ink']) == (1, 1, 0xababab, 0x5c5c5c)
    assert (o['sel_inset'], o['sel_radius']) == (16, 8)
    # song rows: Song Artist and Song Length, Inline and Albums first
    own_ = {x['key']: (x['names'], x['names'][x['def']]) for x in o['opts'] if x.get('key') in ('SONGARTIST', 'SONGLENGTH')}
    assert own_ == {'SONGARTIST': (['Inline', 'Selected Row', 'Right', 'Off'], 'Inline'), 'SONGLENGTH': (['Albums', 'All Lists', 'Off'], 'Albums')}
    assert (o['sb_w'], o['sb_bottom'] - o['sb_top'], (o['sb_top'] + o['sb_bottom']) // 2) == (4, 72, g.bar + g.list_end // 2)
    # Now Playing on the grid: 150 px art with 6 px corners, the bar's top at y 228, 6 px on the Track grey, the artist
    # and album in Inter, the theme's own shuffle and repeat; and every scrolling text's faded ends
    assert (g.np_art, g.np_bar_y, o['np_art_radius'], o['np_bar'], o['np_track_rgb']) == (150, 228, 6, 6, 0x333333)
    assert (o['np_meta_font'], o['np_modes'], o['marquee_fade'], o['reopen_top'], o['shuffle_row'], o['home_lists_full']) == (inter, 1, 1, 1, 1, 1)
    # Home's Split: rows to 204 (the bar to 188, chevrons at 176), the art from x 196 and the top edge
    assert (g.split_row, g.split_art, g.split_art_up, g.split_inset, o['home_label_end[0]']) == (204, 196, 58, 2, 44)
    # its toast and confirm (patch/feedback.c), and the confirmation's glyph
    assert 'oled_confirm_shuffle' in art.images(o) and 'oled_confirm_shadow' not in art.images(o)
    inc = geom_inc(tmp_path, [o])
    assert '#define OWN_OLED_ACCENT' in inc and 'static const scheme_t oled_schemes[] = {' in inc
    assert '.art = "oled_",' in inc
    assert 'theme_rows' not in inc and '.name = ' not in inc  # one theme: no picker, no name in the payload


def test_resolve_rejects_an_unknown_art():
    assert error(resolved, '', 'id="t" name="T" index="2" extends="base" art="pico"') == 't.xml:1: art: unknown theme pico'


@pytest.mark.parametrize('body, message', [
    ('<geometry right_x="0"/>', "the chevrons and song rows need the grid's right_x"),
    ('<view name="list"><element name="scrollbar" w="0"/></view>', "list.scrollbar: the wheel's indicator needs w, top and bottom")])
def test_every_theme_has_what_the_marks_draw_with(body, message):
    assert error(theme.validate, resolved(body), 't.xml') == f't.xml:1: {message}'


def test_accent_text_reads_on_either_half():
    """accent_text, the accent as text (action rows, links): WCAG's 4.5:1 for body text on each half's canvas."""
    def lum(c):
        ch = [(c >> s & 255) / 255 for s in (16, 8, 0)]
        r, g, b = (x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in ch)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    for light in (False, True):
        a, b = lum(theme.token('accent_text', light)), lum(theme.token('canvas', light))
        assert (max(a, b) + 0.05) / (min(a, b) + 0.05) >= 4.5, light


def test_tokens_h_is_written_from_tokens(tmp_path):
    assert (theme.ROOT/'patch/tokens.h').read_text() == theme.token_header(), 'run tools/theme.py write_tokens'
    theme.write_tokens(tmp_path/'tokens.h')
    assert (tmp_path/'tokens.h').read_text() == theme.token_header()
    assert '#define RGB_SECONDARY_L 0x5c5c5c' in theme.token_header()


def test_a_token_without_a_light_half_takes_its_flip():
    assert theme.token('secondary') == 0xababab and theme.token('secondary', True) == 0x5c5c5c
    assert theme.token('raised', True) == theme.flip(0x1c1c1e, *theme.TOKEN_PAPER) == 0xe3e3e1
    xml = (theme.ROOT/'assets/themes/oled/theme.xml').read_text()
    paper = re.search(r'light\.paper="#(\w+)" light\.ink="#(\w+)"', xml)
    assert paper and (int(paper[1], 16), int(paper[2], 16)) == theme.TOKEN_PAPER
    assert theme.token('positive') == theme.token('positive', True) == 0x34c759


# Files with a palette of their own, not the theme's: the games' engine and boards, the EQ editor, and offsets.inc's
# stock-facing values (test/patch.py reads them)
OWN_PALETTE = {'games.c', 'game_popstall.c', 'engine.c', 'engine_demo.c', 'peq_ui.c', 'offsets.inc', 'tokens.h'}


def test_patch_names_tokens_and_type_sizes_rather_than_restating_them():
    """No colour literal equal to a token (black and white aside: they are also every image's ends) outside tokens.h
    and the files with their own palette, and no local table of AWTK sizes (#define PX_15 18): TYPE(15) says it."""
    values = {v for d, l in theme.TOKENS.values() for v in (d, l) if v is not None} - {0, 0xffffff}
    hex_ = '|'.join(f'{v:06x}' for v in values)
    bad = []
    for p in sorted((theme.ROOT/'patch').glob('**/*')):
        if p.suffix not in ('.c', '.h', '.inc') or p.name in OWN_PALETTE:
            continue
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if re.search(rf'\b0x(?:ff)?(?:{hex_})u?\b', line, re.I) or re.match(r'\s*#define PX_\d+\s', line):
                bad.append(f'{p.name}:{n}: {line.strip()}')
    assert not bad, '\n'.join(bad)
