_SOFT = []
try:
    'Execute the actual patched MIPS callback with stock filter and mocked UI services.\nRequires unicorn==2.1.4. Does not emulate the entire device or flash hardware.\n'
except BaseException as _e:
    _SOFT.append((2, repr(_e)[:200]))
    print('FAIL line', 2, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
import ctypes, hashlib, io, json, math, os, pathlib, re, sqlite3, struct, subprocess, sys, tempfile
from functools import cmp_to_key
from unicorn import Uc, UcError, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_CODE, UC_HOOK_BLOCK, UC_HOOK_INTR
from unicorn.unicorn_py3.unicorn import uclib, uccallback, uc_hook_h, HOOK_CODE_CFUNC
from unicorn.mips_const import *
try:
    sys.path.insert(0, sys.path[0] + '/../tools')
except BaseException as _e:
    _SOFT.append((10, repr(_e)[:200]))
    print('FAIL line', 10, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from build import jal, j, segments, load_elf, symbols, BASE, SCRATCH, HOOKS, WM_PAINT_LEAF, FUNCTIONS, GLOBALS, CONTEXT_DATA, ROOT, source_sha256, sha, PRIVATE_FUNCTIONS, TAG, VERSION, BETA, dev_tag
try:
    B = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'build')
except BaseException as _e:
    _SOFT.append((12, repr(_e)[:200]))
    print('FAIL line', 12, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    payload_syms = symbols(B / 'patch.elf')
except BaseException as _e:
    _SOFT.append((13, repr(_e)[:200]))
    print('FAIL line', 13, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    manifest = json.loads((B / 'manifest.json').read_text())
except BaseException as _e:
    _SOFT.append((14, repr(_e)[:200]))
    print('FAIL line', 14, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    if manifest.get('source_sha256') != source_sha256():
        raise SystemExit(f'{B}/manifest.json does not match the current patch sources; rebuild into a fresh directory and pass it here')
except BaseException as _e:
    _SOFT.append((15, repr(_e)[:200]))
    print('FAIL line', 15, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    for name, key in (('demo', 'demo_sha256'), ('stock-demo', 'stock_demo_sha256'), ('patch.bin', 'patch_sha256')):
        if sha((B / name).read_bytes()) != manifest.get(key):
            raise SystemExit(f'{B / name} does not match manifest.json; rebuild into a fresh directory')
except BaseException as _e:
    _SOFT.append((17, repr(_e)[:200]))
    print('FAIL line', 17, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    assert manifest['version'] == (dev_tag(manifest['dev']) if manifest.get('dev') else TAG), 'Wrong version'
except BaseException as _e:
    _SOFT.append((20, repr(_e)[:200]))
    print('FAIL line', 20, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    assert manifest.get('compact_code')
except BaseException as _e:
    _SOFT.append((21, repr(_e)[:200]))
    print('FAIL line', 21, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from ipod import INC, O
from theme import MOTION_WIDGET, MOTION_WINDOW, SCHEME_MODE_OFF, THEME_GEOM_OFF, THEME_MOTION_OFF, builtins as theme_builtins, geometry_sets
from theme import theme_keys
try:
    CONFIG_READS = 8 + len(theme_keys(theme_builtins()))
except BaseException as _e:
    _SOFT.append((25, repr(_e)[:200]))
    print('FAIL line', 25, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    DC = O['DOUBLE_CLICK_MS']
except BaseException as _e:
    _SOFT.append((26, repr(_e)[:200]))
    print('FAIL line', 26, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ACCENTS = [tuple((int(v, 16) for v in g)) for g in re.findall('\\{ 0x(\\w+), 0x(\\w+), 0x(\\w+), 0x(\\w+), 0x(\\w+) \\}', INC)]
except BaseException as _e:
    _SOFT.append((28, repr(_e)[:200]))
    print('FAIL line', 28, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    assert ACCENTS[O['MINIMAL_ACCENT']] == ACCENTS[0]
except BaseException as _e:
    _SOFT.append((30, repr(_e)[:200]))
    print('FAIL line', 30, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def color_t(rgb):
    return 4278190080 | (rgb & 255) << 16 | (rgb >> 8 & 255) << 8 | rgb >> 16
try:
    O_GLYPH = re.search('#define BT_GLYPH "(\\w+)"', INC)[1]
except BaseException as _e:
    _SOFT.append((32, repr(_e)[:200]))
    print('FAIL line', 32, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    CONFIG = {}
except BaseException as _e:
    _SOFT.append((33, repr(_e)[:200]))
    print('FAIL line', 33, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    SEED = {}
except BaseException as _e:
    _SOFT.append((34, repr(_e)[:200]))
    print('FAIL line', 34, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    SETUP = []
except BaseException as _e:
    _SOFT.append((35, repr(_e)[:200]))
    print('FAIL line', 35, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    FILL, RADIUS = (64 << 24 | O['FILL_RGB'], 9)
except BaseException as _e:
    _SOFT.append((36, repr(_e)[:200]))
    print('FAIL line', 36, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    LCD_COLORS = (2596069104, 305419896)
except BaseException as _e:
    _SOFT.append((37, repr(_e)[:200]))
    print('FAIL line', 37, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    syms = symbols(B / 'stock-demo')
except BaseException as _e:
    _SOFT.append((38, repr(_e)[:200]))
    print('FAIL line', 38, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    syms.update(PRIVATE_FUNCTIONS)
except BaseException as _e:
    _SOFT.append((39, repr(_e)[:200]))
    print('FAIL line', 39, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    VG_MOCKS = [n for n in syms if n.startswith(('vgcanvas_', 'vg_gradient_'))]
except BaseException as _e:
    _SOFT.append((40, repr(_e)[:200]))
    print('FAIL line', 40, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def _fp64_fix():
    """The stock binary is -mfp64 (FR=1); Unicorn's MIPS32 FPU only implements FR=0, so
    L-format conversions are rewritten to their W equivalents when an image is loaded. Every
    value the traversed canvas code converts fits 32 bits, so the results are identical. An mthc1
    just before one (the 64-bit integer's high word, as hscroll_label_step's 0x5fb3f8) is dropped:
    the W form doesn't read it, and under FR=0 it would overwrite the odd register beside it."""
    data = (B / 'demo').read_bytes()
    fixes = []
    for _, (t, o, v, _, f, m, flags, _) in segments(data):
        if t != 1 or not flags & 1:
            continue
        words = struct.unpack_from(f'<{f // 4}I', data, o)
        for i, w in enumerate(words):
            if w >> 26 & 63 == 17 and w >> 21 & 31 == 21:
                fixes.append((v + 4 * i, w & ~(31 << 21) | 20 << 21))
            elif w >> 26 & 63 == 17 and w >> 21 & 31 == 7 and any((x >> 26 & 63 == 17 and x >> 21 & 31 == 21 for x in words[i + 1:i + 4])):
                fixes.append((v + 4 * i, 0))
    return fixes
try:
    FP64_FIX = _fp64_fix()
except BaseException as _e:
    _SOFT.append((58, repr(_e)[:200]))
    print('FAIL line', 58, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    REGS = [UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3]
except BaseException as _e:
    _SOFT.append((59, repr(_e)[:200]))
    print('FAIL line', 59, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    SAVED = [UC_MIPS_REG_S0, UC_MIPS_REG_S1, UC_MIPS_REG_S2, UC_MIPS_REG_S3, UC_MIPS_REG_S4, UC_MIPS_REG_S5, UC_MIPS_REG_S6, UC_MIPS_REG_S7, UC_MIPS_REG_FP]
except BaseException as _e:
    _SOFT.append((60, repr(_e)[:200]))
    print('FAIL line', 60, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    SPOILED = [UC_MIPS_REG_V1, *REGS, UC_MIPS_REG_T0, UC_MIPS_REG_T1, UC_MIPS_REG_T2, UC_MIPS_REG_T3, UC_MIPS_REG_T4, UC_MIPS_REG_T5, UC_MIPS_REG_T6, UC_MIPS_REG_T7, UC_MIPS_REG_T8, UC_MIPS_REG_T9]
except BaseException as _e:
    _SOFT.append((63, repr(_e)[:200]))
    print('FAIL line', 63, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    SPOIL = ctypes.c_uint32(3735928559)
except BaseException as _e:
    _SOFT.append((65, repr(_e)[:200]))
    print('FAIL line', 65, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    SPOILED_IDS = (ctypes.c_int * len(SPOILED))(*SPOILED)
except BaseException as _e:
    _SOFT.append((66, repr(_e)[:200]))
    print('FAIL line', 66, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    SPOILED_VALUES = (ctypes.c_void_p * len(SPOILED))(*[ctypes.addressof(SPOIL)] * len(SPOILED))
except BaseException as _e:
    _SOFT.append((66, repr(_e)[:200]))
    print('FAIL line', 66, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def _filter():
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run(['cc', '-O2', '-shared', '-fPIC', '-x', 'c', '-', '-o', directory + '/filter.so'], check=True, input=b'\n#include <stdint.h>\nstatic uint8_t marked[0x1000000 >> 5];  /* a bit an instruction */\nvoid watch(uint32_t a) { if (a < 0x1000000) marked[a >> 5] |= 1 << (a >> 2 & 7); }\nvoid filter(void *uc, uint64_t a, uint32_t size, void *hook) {\n    if (a >= 0x1000000 || marked[a >> 5] >> (a >> 2 & 7) & 1) ((void (*)(void *, uint64_t, uint32_t, void *))hook)(uc, a, size, 0);\n}')
        return ctypes.CDLL(directory + '/filter.so')
try:
    FILTER = _filter()
except BaseException as _e:
    _SOFT.append((81, repr(_e)[:200]))
    print('FAIL line', 81, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    watch = FILTER.watch
except BaseException as _e:
    _SOFT.append((82, repr(_e)[:200]))
    print('FAIL line', 82, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    watch.argtypes = [ctypes.c_uint32]
except BaseException as _e:
    _SOFT.append((82, repr(_e)[:200]))
    print('FAIL line', 82, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    watch.restype = None
except BaseException as _e:
    _SOFT.append((82, repr(_e)[:200]))
    print('FAIL line', 82, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ENTRIES = {*syms.values(), *payload_syms.values(), *(int(v, 16) for v in manifest['patch_symbols'].values())}
except BaseException as _e:
    _SOFT.append((83, repr(_e)[:200]))
    print('FAIL line', 83, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    for a in ENTRIES:
        watch(a)
except BaseException as _e:
    _SOFT.append((84, repr(_e)[:200]))
    print('FAIL line', 84, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def signed(x):
    return x if x < 2147483648 else x - 4294967296
try:
    SCOPE_RULES = '/mnt/data/q2-library-scope'
except BaseException as _e:
    _SOFT.append((87, repr(_e)[:200]))
    print('FAIL line', 87, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    TRACE = open(os.environ['Q2_TRACE'], 'w') if os.environ.get('Q2_TRACE') else None
except BaseException as _e:
    _SOFT.append((90, repr(_e)[:200]))
    print('FAIL line', 90, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def trace(*fields):
    if TRACE:
        TRACE.write('\t'.join(fields) + '\n')
try:
    TRACE_ARGS = {'canvas_get_clip_rect': '_', 'canvas_set_clip_rect': '_r', 'canvas_set_global_alpha': '_i', 'canvas_translate': '_ii', 'canvas_offline_create': 'iii', **dict.fromkeys(('canvas_offline_begin_draw', 'canvas_offline_end_draw', 'canvas_offline_destroy'), '_'), **dict.fromkeys(('canvas_set_fill_color', 'canvas_set_stroke_color', 'canvas_set_text_color'), '_x'), **dict.fromkeys(('canvas_fill_rect', 'canvas_stroke_rect'), '_iiii'), 'canvas_draw_icon': '_xii', 'canvas_set_font': '_si', 'canvas_draw_text_in_rect': '_wr', 'canvas_draw_text': '_wii', 'canvas_measure_text': '_w', 'canvas_draw_image': '_xrr', 'canvas_fill_rounded_rect': '_rrdi', 'canvas_stroke_rounded_rect': '_rrdii', **{'vg:vgcanvas_' + n: c for n, c in (('save', '_'), ('restore', '_'), ('begin_path', '_'), ('close_path', '_'), ('fill', '_'), ('stroke', '_'), ('translate', '_ff'), ('move_to', '_ff'), ('line_to', '_ff'), ('arc', '_fffffi'), ('set_fill_color', '_x'), ('set_stroke_color', '_x'), ('set_fill_linear_gradient', '_ffffxx'), ('set_line_width', '_f'), ('set_line_cap', '_s'))}, 'widget_set_prop_int': 'nsi', 'widget_set_prop_str': 'nss', 'widget_move_resize': 'niiii', 'widget_move': 'nii', 'widget_resize': 'nii', 'widget_set_opacity': 'ni', 'widget_use_style': 'ns', 'widget_set_text_utf8': 'ns', 'widget_set_tr_text': 'ns', 'widget_set_visible': 'nii', 'widget_set_sensitive': 'ni', 'widget_factory_create_widget': '_sniiii', **dict.fromkeys(('image_create', 'list_item_create', 'button_create', 'hscroll_label_create', 'label_create'), 'niiii'), 'image_set_draw_type': 'ni', 'image_base_set_image': 'ns', 'image_manager': '', 'image_manager_unload_all': '_', 'image_manager_unload_bitmap_by_name': '_s'}
except BaseException as _e:
    _SOFT.append((93, repr(_e)[:200]))
    print('FAIL line', 93, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    HOOK_NAMES = {a: n for a, n, _ in HOOKS.values()}
except BaseException as _e:
    _SOFT.append((108, repr(_e)[:200]))
    print('FAIL line', 108, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    PAYLOAD_NAMES = {v: n for n, v in sorted(payload_syms.items())}
except BaseException as _e:
    _SOFT.append((109, repr(_e)[:200]))
    print('FAIL line', 109, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def render(m, code, v):
    """One traced value; 'w' takes (UTF-32 pointer, length). Addresses never appear, so traces survive a relink."""
    if code == 'w':
        return json.dumps(''.join((chr(m.get(v[0] + 4 * k)) for k in range(v[1]))))
    if code in 'srd' and (not v):
        return 'null'
    if code == 's':
        return json.dumps(m.text(v))
    if code == 'r':
        return ','.join((str(signed(m.get(v + 4 * k))) for k in range(4)))
    if code == 'd':
        return '%08x' % m.get(v)
    if code == 'f':
        return '%g' % struct.unpack('<f', struct.pack('<I', v))[0]
    if code == 'n' and v in m.nodes:
        return 'n%d' % list(m.nodes).index(v)
    if BASE <= v < SCRATCH + 131072:
        return PAYLOAD_NAMES.get(v, 'P')
    if 1879048192 <= v < 1879113728:
        return 'S'
    return str(signed(v)) if code == 'i' else '%08x' % v

class Handlers(dict):
    """A machine's mocks, by address: one at an address no symbol names is marked for FILTER."""

    def __setitem__(self, a, name):
        if a not in ENTRIES:
            watch(a)
        super().__setitem__(a, name)

    def update(self, other):
        for a, name in other.items():
            self[a] = name

class Machine:

    def __init__(self, patched=True):
        self.u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        data = (B / ('demo' if patched else 'stock-demo')).read_bytes()
        load_elf(self.u, data)
        if patched:
            self.u.mem_protect(BASE, SCRATCH - BASE, 5)
        for v, w in FP64_FIX:
            self.u.mem_write(v, struct.pack('<I', w))
        self.u.mem_map(16777216, 2097152)
        self.u.mem_map(1879048192, 65536)
        self.next = 16781312
        self.nodes = {}
        self.calls = []
        self.animating = 0
        self.pressed = 0
        self.ups = []
        self.top = 0
        self.wm = 16777216
        self.event = 16777472
        self.char_px = 10.0
        self.strokes = []
        self.rounded = []
        self.bands = []
        self.icons = []
        self.letters = []
        self.font = None
        self.vg_calls = []
        self.fake_vg = 0
        self.global_alpha = 0
        self.rounded_fail = False
        self.count = self.budget
        self.allocs = {}
        self.config = dict(CONFIG)
        self.seed = SEED
        self.config_reads = []
        self.card = ()
        self.dirs = set()
        for f in SETUP:
            f(self)
        self.rebind = None
        self.on_click = None
        self.glide = True
        self.timers = {}
        self.next_timer = 1
        self.timer_fail = False
        self.clicks = []
        self.started = []
        self.screens = []
        self.props = {}
        self.x_log = []
        self.prop_fail = False
        self.slides = {}
        self.slide_fail = False
        self.slide_on_fail = False
        self.slide_easing = O['SLIDE_EASING']
        self.slide_callbacks = {}
        self.canvas = 16777728
        self.lcd = 16777984
        self.now = 1000
        self.word(self.canvas + O['CANVAS_LCD'], self.lcd)
        for off, v in zip(('LCD_FILL_COLOR', 'LCD_STROKE_COLOR'), LCD_COLORS):
            self.word(self.lcd + O[off], v)
        self.clip = (0, 0, 240, 240)
        self.handlers = Handlers()
        if patched:
            self.handlers[payload_syms['press_stamp']] = 'stock_press_stamp'
            self.handlers[payload_syms['mute_stamp']] = 'stock_mute_stamp'
        for name in FUNCTIONS:
            self.handlers[syms[name]] = name
        for name in ('slide_menu_item_width', 'slide_menu_on_scroll_done', 'canvas_get_vgcanvas', 'widget_animator_scroll_set_params', 'slide_menu_set_value', 'toolsTimeItoa', 'on_wm_keyup_fun', 'table_client_get_virtual_h', 'mclGetPlayType', 'networkGetConnectStatus', 'wifi_switch'):
            self.handlers.pop(syms[name], None)
        self.mock('widget_is_instance_of', 'widget_animator_scroll_create', 'widget_animator_on', 'widget_set_focused', 'widget_layout_children', 'event_init', 'value_set_int')
        if patched:
            for name in ('paint', 'dispatch', 'paint_bg'):
                self.handlers[int(manifest['patch_symbols']['stock_' + name + '_trampoline'], 16)] = 'stock_' + name
        self.mock('pthread_create@GLIBC_2.2', 'pthread_detach@GLIBC_2.0', 'pthread_self@GLIBC_2.0')
        self.mock('reset_poweroptions_timer', 'screen_action', 'enable_fb', 'usleep@GLIBC_2.0', 'sprintf@GLIBC_2.0', 'airplayGetFlag', 'playpause_quick_click', 'time@GLIBC_2.0', 'localtime@GLIBC_2.0', 'tzset@GLIBC_2.0', 'strlen@GLIBC_2.0', 'strncpy@GLIBC_2.0', 'strrchr@GLIBC_2.0', 'strcasecmp@GLIBC_2.0', 'strncasecmp@GLIBC_2.0', 'strncmp@GLIBC_2.0', 'strcasestr@GLIBC_2.2', 'unlink@GLIBC_2.0', 'atoi@GLIBC_2.0', 'strtol@GLIBC_2.0', 'memcmp@GLIBC_2.0', 'access@GLIBC_2.0', 'open@GLIBC_2.0', 'fflush@GLIBC_2.0', 'fsync@GLIBC_2.0', 'fileno@GLIBC_2.0', 'fopen@GLIBC_2.2', 'opendir@GLIBC_2.0', 'readdir@GLIBC_2.0', 'closedir@GLIBC_2.0', 'pthread_mutex_lock@GLIBC_2.0', 'pthread_mutex_unlock@GLIBC_2.0', 'calloc@GLIBC_2.0', 'realloc@GLIBC_2.0', 'strdup@GLIBC_2.0', 'fread@GLIBC_2.0', 'fwrite@GLIBC_2.0', 'fclose@GLIBC_2.2', 'rename@GLIBC_2.0')
        self.word(10723952, 16777232)
        self.handlers[16777232] = 'scope_free'
        self.resized = []
        self.sized = {}
        self.scope_tree = {}
        self.scope_markers = set()
        self.scope_handles = {}
        self.scope_files = {}
        self.scope_open = {}
        self.present = set()
        self.stats = {}
        self.image_size = (50, 50)
        self.image_draws = []
        self.image_loads = []
        self.style_ints = {}
        self.clock = (18, 14)
        self.clock_minutes = False
        self.marquee = False
        self.widget_timers = False
        self.wall = (86400, 86400)
        self.rockbox_mode = 33261
        self.groovebox_mode = 0
        self.handlers[syms['__xstat@GLIBC_2.0']] = 'rockbox_stat'
        self.handlers[syms['strcmp@GLIBC_2.0']] = 'tk_strcmp'
        self.handlers[syms['memcpy@GLIBC_2.0']] = 'memcpy'
        self.handlers[syms['memset@GLIBC_2.0']] = 'memset'
        self.handlers[syms['socket@GLIBC_2.0']] = 'no_socket'
        self.mock('canvas_set_global_alpha')
        self.mock('sqrtf@GLIBC_2.0', 'sinf@GLIBC_2.0', 'acosf@GLIBC_2.0', prefix='float:')
        self.mock(*VG_MOCKS, prefix='vg:')
        self.handlers[payload_syms['peq_lib_localtime_r']] = 'localtime_r'
        self.code_hook = self.hooked()
        for name in GLOBALS:
            self.byte(syms[name], 0)
        for name, size in CONTEXT_DATA.items():
            self.u.mem_write(syms[name], bytes(size))
        self.word(syms['g_class_type'], 61441)
        self.byte(syms['g_backlight_status'], 1)

    def probe_canvas(self, lcd_type=1, vg=0, rect=(10, 10, 100, 40), color=None):
        """Real stock canvas code runs; mock only the services it reaches."""
        for n in ('canvas_fill_rounded_rect', 'canvas_stroke_rounded_rect', 'canvas_set_fill_color'):
            del self.handlers[syms[n]]
        self.mock('lcd_get_vgcanvas')
        self.mock('tk_calloc', 'tk_free', prefix='alloc:')
        for off, val in ((16, 0), (20, 0), (24, 239), (28, 239)):
            self.word(self.canvas + off, val)
        self.word(self.lcd + 208, lcd_type)
        self.rounded_fail = False
        self.fake_vg = vg
        self.rect = self.alloc(16)
        self.color = self.alloc(4)
        for i, val in enumerate(rect):
            self.word(self.rect + 4 * i, val)
        self.word(self.color, FILL if color is None else color)

    def lcd_colors(self):
        return tuple((self.get(self.lcd + O[off]) for off in ('LCD_FILL_COLOR', 'LCD_STROKE_COLOR')))

    def x_pushes(self):
        return self.x_log

    def mock(self, *names, prefix=''):
        for n in names:
            self.handlers[syms[n]] = prefix + n

    def record_syscalls(self, fail=lambda page: False):
        """The payload's o32 syscalls, answered 0; mprotect (4125) takes effect, except RWX on a page
        fail(page) picks, answered EACCES."""
        self.syscalls = []

        def syscall(u, intno, unused):
            n, a, b, c = (u.reg_read(r) for r in (UC_MIPS_REG_V0, *REGS[:3]))
            self.syscalls.append((n, a, b, c))
            if n == 4125 and c == 7 and fail(a):
                u.reg_write(UC_MIPS_REG_A3, 1)
                u.reg_write(UC_MIPS_REG_V0, 13)
                return
            if n == 4125:
                u.mem_protect(a, b, c)
            u.reg_write(UC_MIPS_REG_A3, 0)
            u.reg_write(UC_MIPS_REG_V0, 0)
        self.u.hook_add(UC_HOOK_INTR, syscall)

    def scope_scan(self):
        """A library update's marker walk over scope_tree and scope_markers, into SCOPE_RULES."""
        self.call(address=payload_syms['library_scope_scan'], args=(0, 0, 0, 0), gap=0, clear=False)

    def folder(self, path, w=None):
        """Set the browsed folder; with a surface, also scroll it home and repaint."""
        self.u.mem_write(syms['g_folder_path'], path.encode() + b'\x00')
        if w:
            self.word(w + O['SCROLL_Y'], 0)
            self.paint(w)

    def label(self, es, specs):
        """Give each entry None, a text, or a (text, subtitle label) pair."""
        for e, spec in zip(es, specs):
            if spec is None:
                continue
            text, *sub = spec if isinstance(spec, tuple) else (spec,)
            self.nodes[e]['text'] = text
            if sub:
                self.nodes[e]['children'] = [self.node('label', text=sub[0])]

    def panes(self, height=None):
        """Two four-entry scroll panes on album_page; returns (a, b, a entries, b entries)."""
        a = self.node()
        b = self.node()
        es = []
        for s in (a, b):
            if height:
                self.word(s + O['W_H'], height)
            es.append([self.entry(s, i * 48) for i in range(4)])
            self.nodes[s]['children'] = es[-1]
        self.top = self.node('window', 'album_page', [a, b])
        return (a, b, *es)

    def byte(self, a, v):
        self.u.mem_write(a, bytes([v]))

    def word(self, a, v):
        self.u.mem_write(a, struct.pack('<I', v & 4294967295))

    def get(self, a):
        return struct.unpack('<I', self.u.mem_read(a, 4))[0]

    def alloc(self, n=512):
        a = self.next
        self.next += n + 3 & ~3
        return a

    def string(self, s):
        data = s.encode() + b'\x00'
        a = self.alloc(len(data) + 3 & ~3)
        self.u.mem_write(a, data)
        return a

    def wide_string(self, s):
        data = (s + '\x00').encode('utf-32-le')
        a = self.alloc(len(data))
        self.u.mem_write(a, data)
        return a

    def raw(self, a):
        out = bytearray()
        while a:
            page = self.u.mem_read(a, 4096 - (a & 4095))
            end = page.find(b'\x00')
            if end >= 0:
                return bytes(out + page[:end])
            out += page
            a += len(page)
        return bytes(out)

    def text(self, a):
        return self.raw(a).decode()

    def node(self, t='scroll_view', name='', children=(), visible=1, **kw):
        a = self.alloc()
        self.nodes[a] = dict(type=t, name=name, children=list(children), visible=visible, enable=1, **kw)
        self.word(a + O['W_W'], 240)
        self.word(a + O['W_H'], 240)
        self.word(a + O['ROW_HEIGHT'], 48)
        self.word(a + (O['VIEW_CONTENT_H'] if t == 'scroll_view' else O['TABLE_ROWS']), 960 if t == 'scroll_view' else 100)
        self.byte(a + O['VIEW_VERTICAL'], 1)
        return a

    def entry(self, parent, y=0):
        """A leafless tap target: emitter with one EVT_CLICK item, widget_y offset y, height 48."""
        a = self.node('list_item')
        em = self.alloc(4)
        it = self.alloc(40)
        self.word(a + O['W_EMITTER'], em)
        self.word(em, it)
        self.word(it + O['EMIT_TYPE'], O['EVT_CLICK'])
        self.word(a + O['W_PARENT'], parent)
        self.word(a + O['W_Y'], y)
        self.word(a + O['W_H'], 48)
        return a

    def selected(self, w):
        return self.nodes[w].get('_ringnav_index', -1)

    def paint(self, w, gap=1000):
        """Stock order: background, then (children and) border; iPod hooks both."""
        self.call(address=HOOKS['widget_on_paint_background'][0], args=(w, self.canvas, 0, 0), gap=gap)
        gap = 0
        return self.call(address=HOOKS['widget_on_paint_border'][0], args=(w, self.canvas, 0, 0), gap=gap, clear=False)

    def drawn(self):
        return self.rounded or self.strokes or self.bands

    def sel(self):
        """The selected row's rectangle, from the outline (inset 1) or the bar's bands."""
        if self.rounded and self.rounded[0]['kind'] == 'fill' and (self.rounded[0]['color'] == color_t(15658732)):
            x, y, w, h = self.rounded[0]['rect']
            return (x - O['SEL_INSET'], y, w + 2 * O['SEL_INSET'], h) if x else (x, y, w, h)
        ys = [b[1] for b in self.bands]
        x = min((b[0] for b in self.bands))
        w = max((b[2] for b in self.bands))
        if x == O['SEL_INSET']:
            x, w = (0, w + 2 * O['SEL_INSET'])
        return (x, min(ys), w, max(ys) - min(ys) + 1)
        x, y, w, h = self.rounded[0]['rect'] if self.rounded else self.strokes[0][:4]
        return (x - 1, y - 1, w + 2, h + 2)

    def touch(self):
        return self.call(address=HOOKS['on_wm_tsdown_before_fun'][0], event_type=O['EVT_POINTER_DOWN'])

    def click(self, w, gap=1000):
        return self.call(address=HOOKS['widget_dispatch'][0], args=(w, self.event, 0, 0), event_type=O['EVT_CLICK'], gap=gap)

    def hook(self, u, address, size, _):
        if address == syms['widget_animator_scroll_set_params']:
            assert u.reg_read(UC_MIPS_REG_A0) not in self.slides, 'retarget must pause first'
            self.calls.append(('widget_animator_scroll_set_params', *[u.reg_read(r) for r in REGS[:3]]))
        if address not in self.handlers:
            return
        name = self.handlers[address]
        if not name.startswith('stock_') and address != payload_syms['peq_lib_localtime_r']:
            assert u.reg_read(UC_MIPS_REG_T9) == address, (name, 'PIC call missing t9')
        a, b, c, d = [u.reg_read(r) for r in REGS]
        n = self.nodes.get(a, {})
        self.calls.append((name, a, b, c))
        if TRACE and name in TRACE_ARGS:
            sp = u.reg_read(UC_MIPS_REG_SP)
            it = iter([a, b, c, d] + [self.get(sp + 16 + 4 * k) for k in range(4)])
            out = []
            for k in TRACE_ARGS[name]:
                v = (next(it), next(it)) if k == 'w' else next(it)
                if k != '_':
                    out.append(render(self, k, v))
            trace(name, *out)
        if name == 'memcpy':
            self.u.mem_write(a, bytes(self.u.mem_read(b, c)))
            ret = a
        elif name == 'strncpy@GLIBC_2.0':
            self.u.mem_write(a, self.raw(b)[:c].ljust(c, b'\x00'))
            ret = a
        elif name == 'memset':
            self.u.mem_write(a, bytes([b & 255]) * c)
            ret = a
        elif name == 'thread:run':
            self.nested(c, d)
            ret = 0
        elif name in ('table_row_create', 'list_item_create', 'button_create', 'image_create', 'view_create', 'hscroll_label_create', 'gif_image_create', 'label_create'):
            kind = {'gif_image_create': 'gif'}.get(name, name.removesuffix('_create'))
            ret = self.node(kind)
            for off, value in zip((O['W_X'], O['W_Y'], O['W_W'], O['W_H']), (b, c, d, self.get(u.reg_read(UC_MIPS_REG_SP) + 16))):
                self.word(ret + off, value)
            self.word(ret + O['W_PARENT'], a)
            self.nodes[a]['children'].append(ret)
        elif name == 'widget_factory_create_widget':
            ret = self.node(self.text(b))
            self.word(ret + O['W_PARENT'], c)
            self.nodes[c]['children'].append(ret)
            for off, value in zip(('W_X', 'W_Y', 'W_W', 'W_H'), (d, self.get(u.reg_read(UC_MIPS_REG_SP) + 16), self.get(u.reg_read(UC_MIPS_REG_SP) + 20), self.get(u.reg_read(UC_MIPS_REG_SP) + 24))):
                self.word(ret + O[off], value)
        elif name == 'widget_set_name':
            n['name'] = self.text(b)
            ret = 0
        elif name == 'widget_set_enable':
            n['enable'] = b
            ret = 0
        elif name == 'widget_set_self_layout_params':
            n['self_layout'] = tuple((self.text(x) for x in (b, c, d, self.get(u.reg_read(UC_MIPS_REG_SP) + 16))))
            ret = 0
        elif name == 'widget_on':
            n.setdefault('handlers', []).append((b, c, d))
            ret = len(n['handlers'])
        elif name == 'widget_to_local':
            x, y = (signed(self.get(b)), signed(self.get(b + 4)))
            while a:
                if self.nodes.get(a, {}).get('type') == 'scroll_view':
                    x += signed(self.get(a + O['SCROLL_X']))
                    y += signed(self.get(a + O['SCROLL_Y']))
                x -= signed(self.get(a + O['W_X']))
                y -= signed(self.get(a + O['W_Y']))
                a = self.get(a + O['W_PARENT'])
            self.word(b, x)
            self.word(b + 4, y)
            ret = 0
        elif name in ('widget_set_text_utf8', 'widget_set_tr_text'):
            n['text'] = self.text(b)
            ret = 0
        elif name == 'image_base_set_image':
            n['image'] = self.text(b)
            ret = 0
        elif name == 'widget_use_style':
            n['style'] = self.text(b)
            ret = 0
        elif name == 'widget_set_text':
            chars = []
            while self.get(b + 4 * len(chars)):
                chars.append(chr(self.get(b + 4 * len(chars))))
            n['text'] = ''.join(chars)
            ret = 0
        elif name == 'hscroll_label_cast':
            ret = a
        elif name == 'hscroll_label_set_only_focus':
            self.byte(a + O['HSCROLL_ONLY_FOCUS'], b)
            n[name] = b
            ret = 0
        elif name in ('hscroll_label_start', 'hscroll_label_stop'):
            n['scrolling'] = name.endswith('start') and (not self.u.mem_read(a + O['HSCROLL_ONLY_FOCUS'], 1)[0])
            ret = 0
        elif name.startswith('hscroll_label_set_') or name == 'set_hscroll_label_attribute':
            n[name] = b if name != 'set_hscroll_label_attribute' else True
            ret = 0
        elif name == 'image_set_draw_type':
            n['draw_type'] = b
            ret = a
        elif name == 'deque_at':
            ret = self.row_record
        elif name == 'deque_size':
            ret = 1
        elif name in ('tk_snprintf', 'snprintf@GLIBC_2.0'):
            fmt = self.text(c)
            values = iter([d] + [self.get(u.reg_read(UC_MIPS_REG_SP) + off) for off in range(16, 80, 4)])
            params = []
            for star, kind in re.findall('%(\\.\\*)?\\d*(?:\\.\\d+)?([sdxXuc])', fmt):
                if star:
                    n = next(values)
                    params.append(bytes(self.u.mem_read(next(values), n)).split(b'\x00')[0].decode())
                else:
                    value = next(values)
                    params.append(self.text(value) if kind == 's' else chr(value & 255) if kind == 'c' else value if kind in 'xXu' else signed(value))
            result = (re.sub('%\\.\\*s', '%s', fmt) % tuple(params)).encode()
            self.u.mem_write(a, result[:b - 1] + b'\x00')
            ret = len(result)
        elif name == 'atoi@GLIBC_2.0':
            ret = int(re.match('[+-]?\\d+', self.text(a))[0]) if re.match('[+-]?\\d+', self.text(a)) else 0
        elif name == 'access@GLIBC_2.0':
            ret = 0 if self.text(a) in self.present else -1
        elif name == 'send@GLIBC_2.0':
            ret = c
        elif name == 'open@GLIBC_2.0':
            ret = 4294967295
        elif name == 'calloc@GLIBC_2.0':
            ret = 0 if getattr(self, 'fail_calloc', False) else self.alloc(a * b)
            self.sized[ret] = a * b
        elif name == 'realloc@GLIBC_2.0' and getattr(self, 'fail_realloc', False):
            ret = 0
        elif name == 'realloc@GLIBC_2.0' and (not a):
            ret = self.alloc(b)
            self.sized[ret] = b
        elif name == 'realloc@GLIBC_2.0':
            kept = bytes(self.u.mem_read(a, b))
            ret = self.alloc(b)
            self.u.mem_write(ret, kept)
            self.u.mem_write(a, b'\xff' * min(b, self.sized.get(a, b)))
            self.resized.append(kept)
            self.sized[ret] = b
        elif name == 'strdup@GLIBC_2.0':
            ret = self.string(self.text(a))
        elif name == 'scope_free':
            ret = 0
        elif name == 'opendir@GLIBC_2.0':
            path = self.text(a)
            ret = self.alloc(4) if path in self.scope_tree else 0
            if ret:
                self.scope_handles[ret] = list(self.scope_tree[path])
        elif name == 'readdir@GLIBC_2.0':
            ret = 0
            if self.scope_handles.get(a):
                n, t = self.scope_handles[a].pop(0)
                ret = self.alloc(268)
                self.byte(ret + 10, t)
                self.u.mem_write(ret + 11, n.encode() + b'\x00')
        elif name == 'closedir@GLIBC_2.0':
            self.scope_handles.pop(a, None)
            ret = 0
        elif name == 'fopen@GLIBC_2.2':
            path, mode = (self.text(a), self.text(b))
            ret = 0
            if path.startswith(SCOPE_RULES) and (mode == 'wb' or path in self.scope_files):
                ret = self.alloc(4)
                self.scope_open[ret] = [path, mode == 'wb', b'']
        elif name == 'fwrite@GLIBC_2.0':
            self.scope_open[d][2] += bytes(u.mem_read(a, b * c))
            ret = c
        elif name == 'fread@GLIBC_2.0':
            data = self.scope_files[self.scope_open[d][0]][:b * c]
            u.mem_write(a, data)
            ret = len(data) // b
        elif name == 'fclose@GLIBC_2.2':
            path, writing, data = self.scope_open.pop(a)
            ret = 0
            if writing:
                self.scope_files[path] = data
        elif name == 'rename@GLIBC_2.0':
            ret = 0 if self.text(a) in self.scope_files else -1
            if not ret:
                self.scope_files[self.text(b)] = self.scope_files.pop(self.text(a))
        elif name == 'unlink@GLIBC_2.0':
            self.scope_files.pop(self.text(a), None)
            ret = 0
        elif name == 'no_socket':
            ret = -1
        elif name == 'rockbox_stat':
            assert a == 3
            if self.text(b) in self.scope_markers:
                self.word(c + 20, 33188)
                ret = 0
            elif self.text(b) in ['/mnt/mmc/' + n for n, t in self.card if t == 4] or self.text(b) in self.dirs:
                self.word(c + 20, 16877)
                ret = 0
            elif self.text(b) in self.stats:
                self.word(c + 64, self.stats[self.text(b)])
                ret = 0
            elif self.text(b) == '/mnt/mmc/.groovebox/q2-groovebox':
                self.word(c + 20, self.groovebox_mode)
                ret = 0 if self.groovebox_mode else -1
            elif self.text(b) != '/mnt/mmc/.rockbox/rockbox':
                ret = -1
            else:
                self.word(c + 20, self.rockbox_mode)
                ret = 0 if self.rockbox_mode else -1
        elif name == 'strlen@GLIBC_2.0':
            ret = len(self.raw(a))
        elif name == 'strrchr@GLIBC_2.0':
            i = self.text(a).encode().rfind(bytes([b & 255]))
            ret = a + i if i >= 0 else 0
        elif name == 'memcmp@GLIBC_2.0':
            x, y = (bytes(u.mem_read(a, c)), bytes(u.mem_read(b, c)))
            ret = (x > y) - (x < y)
        elif name == 'strncmp@GLIBC_2.0':
            assert a and b, 'strncmp(NULL) faults in libc'
            x, y = (bytes(self.raw(v))[:c] for v in (a, b))
            ret = (x > y) - (x < y)
        elif name in ('strcasecmp@GLIBC_2.0', 'strncasecmp@GLIBC_2.0'):
            x, y = (bytes(self.raw(v)).lower()[:c if name.startswith('strn') else None] for v in (a, b))
            ret = (x > y) - (x < y)
        elif name == 'sprintf@GLIBC_2.0':
            fmt = self.text(b)
            values = (c, d, self.get(u.reg_read(UC_MIPS_REG_SP) + 16))
            result = (fmt % tuple((signed(v) for v in values[:fmt.count('%')]))).encode()
            self.u.mem_write(a, result + b'\x00')
            ret = len(result)
        elif name == 'time@GLIBC_2.0':
            ret = -1 if self.clock == 'time' else self.wall[0]
            if a and ret != -1:
                self.word(a, ret)
        elif name in ('localtime@GLIBC_2.0', 'localtime_r'):
            ret = 0
            if hasattr(self, 'epoch'):
                back = self.epoch
                while back > 2147483647:
                    back -= 883612800
                self.wall = (self.epoch, back)
            if self.clock != 'localtime':
                assert self.get(a) == self.wall[1]
                ret = b if name == 'localtime_r' else self.alloc(44)
                for i, v in enumerate((59, self.clock[1], self.clock[0])):
                    self.word(ret + 4 * i, v)
        elif name == 'widget_set_children_layout':
            n['children_layout'] = self.text(b)
            layout = self.alloc(32)
            self.word(layout + O['CHILDREN_LAYOUT_VTABLE'], O['DEFAULT_LAYOUT_VTABLE'])
            self.u.mem_write(layout + 20, struct.pack('<HH', 1, 0))
            for param, off in (('xm', 'DEFAULT_LAYOUT_X_MARGIN'), ('s', 'DEFAULT_LAYOUT_SPACING')):
                match = re.search('\\b' + param + '=(\\d+)', self.text(b))
                self.byte(layout + O[off], int(match[1]) if match else 0)
            self.word(a + O['W_CHILDREN_LAYOUT'], layout)
            ret = 0
        elif name == 'widget_resize':
            self.word(a + O['W_W'], b)
            self.word(a + O['W_H'], c)
            ret = 0
        elif name == 'widget_vtable_on_layout_children':
            ret = 3
        elif name in ('darray_init', 'darray_deinit', 'widget_layout_floating_children', 'widget_layout_self'):
            ret = 0
        elif name == 'widget_get_children_for_layout':
            children = [x for x in n['children'] if self.nodes[x]['visible']]
            values = self.alloc(4 * len(children) + 4)
            for i, child in enumerate(children):
                self.word(values + 4 * i, child)
            self.word(b, len(children))
            self.word(b + 8, values)
            ret = 0
        elif name == 'toolsWriteConfig':
            self.config[self.text(c)] = self.text(d)
            ret = 0
        elif name == 'toolsReadConfig':
            key = self.text(c)
            self.config_reads.append((self.text(a), self.text(b), key, self.text(self.get(u.reg_read(UC_MIPS_REG_SP) + 16))))
            value = self.config.get(key, self.seed.get(key, self.config_reads[-1][3]))
            self.u.mem_write(d, value.encode() + b'\x00')
            ret = 1 if key in self.config else -1
        elif name == 'tk_str_end_with':
            ret = self.text(a).endswith(self.text(b))
        elif name == 'tk_str_start_with':
            ret = self.raw(a).startswith(self.raw(b))
        elif name == 'strtol@GLIBC_2.0':
            t = re.match('\\s*[-+]?\\d+', self.text(a))
            ret = max(-2 ** 31, min(2 ** 31 - 1, int(t[0]))) if t else 0
            if b:
                self.word(b, a + (len(t[0]) if t else 0))
        elif name == 'strstr@GLIBC_2.0':
            i = self.text(a).find(self.text(b))
            ret = a + i if i >= 0 else 0
        elif name == 'strcasestr@GLIBC_2.2':
            i = self.raw(a).lower().find(self.raw(b).lower())
            ret = a + i if i >= 0 else 0
        elif name == 'bitmap_get_line_length':
            ret = self.get(a + 8)
        elif name == 'bitmap_lock_buffer_for_write':
            ret = self.get(a + 20)
        elif name == 'image_manager':
            ret = 16778496
        elif name == 'widget_move':
            self.word(a + O['W_X'], b)
            self.word(a + O['W_Y'], c)
            ret = 0
        elif name in ('widget_move_resize_ex', 'widget_move_resize'):
            for off, value in zip(('W_X', 'W_Y', 'W_W', 'W_H'), (b, c, d, self.get(u.reg_read(UC_MIPS_REG_SP) + 16))):
                self.word(a + O[off], value)
            ret = 0
        elif name == 'widget_lookup':

            def lookup(w):
                if self.nodes[w].get('name') == self.text(b):
                    return w
                for child in self.nodes[w]['children']:
                    found = lookup(child) if c else child if self.nodes[child].get('name') == self.text(b) else 0
                    if found:
                        return found
                return 0
            ret = lookup(a) if a else 0
        elif name == 'scroll_bar_cast':
            ret = a
        elif name == 'scroll_bar_is_mobile':
            ret = n.get('type') == 'scroll_bar_m'
        elif name == 'widget_set_opacity':
            self.byte(a + 52, b)
            ret = 0
        elif name in ('widget_set_visible', 'widget_set_visible_only', 'widget_set_sensitive'):
            n['sensitive' if name == 'widget_set_sensitive' else 'visible'] = b
            ret = 0
        elif name == 'widget_animator_prop_create':
            ret = 0 if self.prop_fail else self.alloc(128)
            if ret:
                self.word(ret + 4, a)
                self.word(ret + O['ANIM_DURATION'], b)
                n['animated_ms'] = b
                n['animated_easing'] = d
                self.word(ret + 36, c)
                n['animated'] = self.text(self.get(u.reg_read(UC_MIPS_REG_SP) + 16))
                assert n['animated'] in ('opacity', 'x')
        elif name == 'widget_animator_prop_set_params':
            assert self.get(u.reg_read(UC_MIPS_REG_SP) + 16) == 0 and self.get(u.reg_read(UC_MIPS_REG_SP) + 20) == 0
            if self.nodes[self.get(a + 4)].get('animated') == 'x':
                self.props[a] = (struct.unpack('<d', struct.pack('<II', c, d))[0], 0)
                self.x_log.append((int(self.props[a][0]), 0))
            ret = 0
        elif name == 'window_manager':
            ret = self.wm
        elif name == 'window_manager_get_top_window':
            ret = self.top
        elif name == 'window_manager_is_animating':
            ret = self.animating
        elif name == 'window_manager_get_pointer_pressed':
            ret = self.pressed
        elif name == 'widget_get_visible':
            ret = n.get('visible', 0)
        elif name == 'widget_get_type':
            ret = self.string(n.get('type', ''))
        elif name == 'widget_get_prop_str':
            ret = 0 if self.text(b) == 'text' else self.string(n.get(self.text(b), ''))
            if self.text(b) == 'name' and (not n.get('name')):
                ret = 0
        elif name == 'widget_get_text':
            ret = self.wide_string(n.get('text', ''))
        elif name == 'player_playtime_and_length' and hasattr(self, 'playtime'):
            self.word(a, self.playtime)
            self.word(b, 225)
            ret = 0
        elif name == 'widget_get_text_utf8':
            text = n.get('text', '').encode()
            self.u.mem_write(b, bytes(c))
            if len(text) < c:
                self.u.mem_write(b, text + b'\x00')
            ret = 0
        elif name in ('widget_get_prop_bool', 'widget_get_prop_int'):
            ret = n.get(self.text(b), c)
        elif name == 'mclGetLyricSize':
            ret = getattr(self, 'lyric_size', 0)
        elif name == 'widget_destroy':
            for p in self.nodes.values():
                if a in p.get('children', ()):
                    p['children'].remove(a)
            n['destroyed'] = True
            ret = 0
        elif name == 'widget_count_children':
            ret = 0 if a == self.wm and a not in self.nodes else len(n.get('children', ()))
        elif name == 'widget_get_child':
            ret = n['children'][b] if b < len(n['children']) else 0
        elif name == 'widget_set_prop_int':
            n[self.text(b)] = signed(c)
            ret = 0
        elif name == 'slider_set_value':
            n['value'] = int(struct.unpack('<d', struct.pack('<II', c, d))[0])
            ret = 0
            for t, f, ctx in list(n.get('handlers', ())):
                if t == O['EVT_VALUE_CHANGED']:
                    self.echoes = getattr(self, 'echoes', 0) + 1
                    self.nested(f, ctx, self.event, 0, 0)
        elif name == 'widget_set_prop_str':
            n[self.text(b)] = self.text(c)
            ret = 0
        elif name == 'text_selector_count_options':
            ret = n.get('options', 0)
        elif name == 'text_selector_set_selected_index':
            self.word(a + O['SELECTOR_INDEX'], b)
            ret = 0
        elif name == 'widget_restack' and getattr(self, 'restack', False):
            kids = next((v['children'] for v in self.nodes.values() if a in v.get('children', ())), None)
            if kids is not None:
                kids.remove(a)
                kids.insert(min(b, len(kids)), a)
            ret = 0
        elif name == 'pointer_event_init':
            self.word(a, b)
            self.word(a + 16, c)
            self.word(a + O['EVENT_X'], d)
            self.word(a + O['EVENT_Y'], self.get(u.reg_read(UC_MIPS_REG_SP) + 16))
            ret = a
        elif name == 'time_now_ms':
            ret = self.now & 4294967295
        elif name == 'timer_add':
            ret = 0 if self.timer_fail else self.next_timer
            if ret:
                self.next_timer += 1
                if (a != payload_syms.get('clock_due') or self.clock_minutes) and (self.marquee or a != payload_syms.get('marq_moved')):
                    self.timers[ret] = (self.now + c, a, b, c)
        elif name == 'widget_add_timer' and self.widget_timers:
            ret = self.next_timer
            self.next_timer += 1
            self.timers[ret] = (self.now + c, b, a, c)
        elif name == 'timer_remove':
            self.timers.pop(a, None)
            ret = 0
        elif name == 'screen_action':
            self.screens.append(a)
            ret = 1
        elif name == 'player_start':
            self.started.append((a, b, c, d))
            ret = 1
        elif name in ('tk_strcmp', 'strcmp@GLIBC_2.0'):
            x, y = (self.text(a), self.text(b))
            ret = 0 if a and b and (x == y) else -1 if x <= y else 1
        elif name == 'stock_dispatch':
            if self.get(b) == O['EVT_CLICK']:
                self.clicks.append(a)
                if self.on_click:
                    self.on_click(a, b)
            elif self.get(b) == O['EVT_POINTER_UP_BEFORE'] - 1:
                self.ups.append(a)
            ret = 0
        elif name == 'table_client_stop_animator_scroll':
            self.word(a + O['TABLE_ANIMATOR'], 0)
            ret = 0
        elif name == 'widget_is_instance_of':
            ret = 1
        elif name == 'widget_set_focused':
            n['focused'] = b
            ret = 0
        elif name == 'event_init':
            self.word(a, b)
            ret = a
        elif name == 'value_set_int':
            self.word(a, b)
            ret = a
        elif name == 'widget_animator_scroll_create':
            ret = 0 if self.slide_fail else self.alloc(128)
            if ret:
                self.word(ret + 4, a)
                self.word(ret + O['ANIM_DURATION'], b)
                if self.nodes.get(a, {}).get('type') == 'slide_menu':
                    assert c == 0 and d == self.slide_easing
        elif name == 'widget_animator_on':
            ret = 0 if self.slide_on_fail else 1
            if ret:
                self.slide_callbacks[a] = (c, d)
        elif name == 'widget_animator_pause':
            self.slides.pop(a, None)
            ret = 0
        elif name == 'widget_animator_destroy':
            self.slides.pop(a, None)
            self.slide_callbacks.pop(a, None)
            ret = 0
        elif name == 'widget_animator_start':
            if a in self.props and self.nodes[self.get(a + 4)].get('animated') == 'x':
                origin, goal = self.props[a]
                self.props[a] = (self.now, self.get(a + O['ANIM_DURATION']), self.get(a + 4), origin, goal)
            if a in self.slide_callbacks:
                w = self.get(a + 4)
                if self.nodes.get(w, {}).get('type') == 'slide_menu':
                    assert self.get(a + O['ANIM_ELAPSED']) == self.get(a + O['ANIM_START_TIME']) == 0
                    self.slides[a] = (self.now, self.get(a + O['ANIM_DURATION']), w, signed(self.get(a + 104)), signed(self.get(a + O['ANIM_X_TO'])))
            ret = 0
        elif name == 'table_client_set_yoffset':
            assert self.get(a + O['TABLE_ANIMATOR']) == 0
            self.word(a + O['TABLE_TOP'], b)
            if self.rebind:
                self.rebind(a, b)
            ret = 0
        elif name == 'scroll_view_set_offset':
            assert self.get(a + O['VIEW_ANIMATOR']) == 0
            self.word(a + O['SCROLL_X'], b)
            self.word(a + O['SCROLL_Y'], c)
            ret = 0
        elif name == 'scroll_view_scroll_to':
            end = signed(self.get(a + O['VIEW_CONTENT_H'])) - signed(self.get(a + O['W_H']))
            self.word(a + O['SCROLL_X'], b)
            self.word(a + O['SCROLL_Y'], max(0, min(signed(c), end)))
            ret = 0
        elif name == 'table_client_scroll_to':
            if self.glide:
                self.word(a + O['TABLE_TOP'], b)
                if self.rebind:
                    self.rebind(a, b)
            else:
                self.word(a + O['TABLE_ANIMATOR'], 4660)
            ret = 0
        elif name == 'canvas_get_clip_rect':
            for j, v in enumerate(self.clip):
                self.word(b + 4 * j, v)
            ret = 0
        elif name == 'canvas_set_clip_rect':
            self.clip = tuple((signed(self.get(b + 4 * j)) for j in range(4)))
            ret = 0
        elif name == 'canvas_set_fill_color':
            self.word(self.lcd + O['LCD_FILL_COLOR'], b)
            ret = 0
        elif name == 'canvas_set_stroke_color':
            self.word(self.lcd + O['LCD_STROKE_COLOR'], b)
            ret = 0
        elif name == 'canvas_set_global_alpha':
            self.global_alpha += 1
            ret = 0
        elif name in ('canvas_fill_rounded_rect', 'canvas_stroke_rounded_rect'):
            sp = u.reg_read(UC_MIPS_REG_SP)
            kind = 'fill' if name == 'canvas_fill_rounded_rect' else 'stroke'
            radius = self.get(sp + 16)
            width = None if kind == 'fill' else self.get(sp + 20)
            self.rounded.append(dict(kind=kind, rect=tuple((signed(self.get(b + 4 * j)) for j in range(4))), bg=signed(c), color=self.get(d), radius=radius, width=width, clip=self.clip))
            if kind == 'stroke' and (self.rounded_fail is True or self.rounded_fail == radius):
                ret = 2
            else:
                self.word(self.lcd + (O['LCD_FILL_COLOR'] if kind == 'fill' else O['LCD_STROKE_COLOR']), self.get(d))
                ret = 0
        elif name == 'style_get_int':
            ret = self.style_ints.get((a, self.text(b)), c)
        elif name == 'canvas_draw_image':
            self.image_draws.append(tuple((tuple((signed(self.get(r + 4 * i)) for i in range(4))) for r in (c, d))))
            ret = 0
        elif name == 'widget_load_image':
            self.image_loads.append(self.text(b))
            ret = 1 if self.image_size is None or self.text(b) in getattr(self, 'image_fail', ()) else 0
            if not ret:
                self.word(c, self.image_size[0])
                self.word(c + 4, self.image_size[1])
                self.u.mem_write(c + 8, bytes(16))
        elif name == 'canvas_draw_icon':
            self.icons.append((signed(c), signed(d), self.clip))
            ret = 0
        elif name == 'canvas_set_font':
            self.font = (self.text(b) if b else 'default', c)
            ret = 0
        elif name == 'canvas_set_text_color':
            self.word(self.lcd + O['LCD_TEXT_COLOR'], b)
            ret = 0
        elif name == 'canvas_draw_text_in_rect':
            self.letters.append(dict(text=''.join((chr(self.get(b + 4 * j)) for j in range(c))), rect=tuple((signed(self.get(d + 4 * j)) for j in range(4))), color=self.get(self.lcd + O['LCD_TEXT_COLOR']), font=self.font, align=(self.get(a + O['CANVAS_ALIGN_H']), self.get(a + O['CANVAS_ALIGN_V'])), clip=self.clip))
            ret = 0
        elif name == 'canvas_measure_text':
            u.reg_write(UC_MIPS_REG_F0, struct.unpack('<I', struct.pack('<f', self.char_px * c))[0])
            ret = 0
        elif name == 'widget_get_canvas':
            ret = self.canvas
        elif name == 'canvas_draw_text':
            self.letters.append(dict(text=''.join((chr(self.get(b + 4 * j)) for j in range(c))), x=signed(d), y=signed(self.get(u.reg_read(UC_MIPS_REG_SP) + 16)), color=self.get(self.lcd + O['LCD_TEXT_COLOR']), font=self.font))
            ret = 0
        elif name == 'canvas_fill_rect':
            self.bands.append((signed(b), signed(c), signed(d), signed(self.get(u.reg_read(UC_MIPS_REG_SP) + 16)), self.get(self.lcd + O['LCD_FILL_COLOR']), self.clip))
            ret = 0
        elif name in ('canvas_stroke_rect', 'lcd_stroke_rect'):
            h = self.get(u.reg_read(UC_MIPS_REG_SP) + 16)
            clip = self.clip if name == 'canvas_stroke_rect' else (self.get(self.canvas + 16), self.get(self.canvas + 20), self.get(self.canvas + 24) - self.get(self.canvas + 16) + 1, self.get(self.canvas + 28) - self.get(self.canvas + 20) + 1)
            self.strokes.append((signed(b), signed(c), signed(d), signed(h), clip, self.get(self.lcd + O['LCD_STROKE_COLOR'])))
            ret = 0
        elif name == 'scroll_view_scroll_delta_to':
            n['glide_ms'] = d
            if self.glide:
                self.word(a + O['SCROLL_X'], self.get(a + O['SCROLL_X']) + b)
                self.word(a + O['SCROLL_Y'], self.get(a + O['SCROLL_Y']) + c)
            else:
                self.word(a + O['VIEW_ANIMATOR'], 4660)
            ret = 0
        elif name == 'lcd_get_vgcanvas':
            ret = self.fake_vg
        elif name.startswith('vg:'):
            self.vg_calls.append((name[3:], signed(a), signed(b)))
            ret = 1 if name[3:] == 'vgcanvas_save' else 0
        elif name.startswith('float:'):
            x = struct.unpack('<f', struct.pack('<I', u.reg_read(UC_MIPS_REG_F12)))[0]
            fn = {'sqrtf@GLIBC_2.0': math.sqrt, 'sinf@GLIBC_2.0': math.sin, 'acosf@GLIBC_2.0': math.acos}[name[6:]]
            ret = struct.unpack('<I', struct.pack('<f', fn(x)))[0]
            u.reg_write(UC_MIPS_REG_F0, ret)
        elif name in ('alloc:tk_calloc', 'alloc:tk_free'):
            if name == 'alloc:tk_calloc':
                size = a * b
                p = self.alloc(size + 16)
                self.allocs[p] = size
                ret = p
            else:
                self.allocs.pop(a, None)
                ret = 0
        elif name == 'stock_tidal_load':
            self.word(payload_syms['playback_loads'], self.get(payload_syms['playback_loads']) + 1)
            ret = 0
        elif name.startswith('one:'):
            ret = 1
        else:
            ret = 0
        self.returns(ret, clobber=True)

    def nested(self, f, *args):
        """Run guest f(*args) from inside a mock, on a stack below the caller's, and keep the caller's registers."""
        regs = [UC_MIPS_REG_PC, *range(UC_MIPS_REG_0, UC_MIPS_REG_31 + 1)]
        saved = [self.u.reg_read(r) for r in regs]
        for r, v in ((UC_MIPS_REG_SP, self.u.reg_read(UC_MIPS_REG_SP) - 1024), (UC_MIPS_REG_RA, 16777216), (UC_MIPS_REG_T9, f), *zip(REGS, args)):
            self.u.reg_write(r, v)
        self.u.emu_start(f, 16777216, count=self.count)
        ret = signed(self.u.reg_read(UC_MIPS_REG_V0))
        for r, v in zip(regs, saved):
            self.u.reg_write(r, v)
        return ret

    def qsort(self, base, n, size, compare):
        pair = self.alloc(2 * size)

        def cmp(x, y):
            self.u.mem_write(pair, x)
            self.u.mem_write(pair + size, y)
            return self.nested(compare, pair, pair + size)
        for i, v in enumerate(sorted([bytes(self.u.mem_read(base + size * i, size)) for i in range(n)], key=cmp_to_key(cmp))):
            self.u.mem_write(base + size * i, v)

    def returns(self, ret, clobber=False):
        """Return ret from a mocked call; clobber caller-saved registers to catch accidental ABI assumptions."""
        if clobber:
            assert not self.u._do_reg_write_batch(SPOILED_IDS, SPOILED_VALUES, len(SPOILED))
        self.u.reg_write(UC_MIPS_REG_V0, ret & 4294967295)
        self.u.reg_write(UC_MIPS_REG_PC, self.u.reg_read(UC_MIPS_REG_RA))

    def hooked(self, begin=1, end=0):
        """Hook begin..end (everything, as unicorn reads 1..0) with the hook behind FILTER; returns unicorn's handle."""
        hook = self.hook
        python = uccallback(self.u, HOOK_CODE_CFUNC)(lambda u, address, size, _: hook(u, address, size, None))
        handle = uc_hook_h()
        assert not uclib.uc_hook_add(self.u._uch, ctypes.byref(handle), UC_HOOK_CODE, FILTER.filter, ctypes.cast(python, ctypes.c_void_p), begin, end)
        self.u._callbacks[handle.value] = python
        return handle.value

    def fast(self):
        """Hook stock code and the payload's trampolines only, so the payload's own loops (the
        Coverflow renderer) run at native emulator speed."""
        self.u.hook_del(self.code_hook)
        self.hooked(0, BASE - 1)
        self.hooked(SCRATCH, 4294967295)
        for a in list(self.handlers):
            if BASE <= a < SCRATCH:
                self.u.hook_add(UC_HOOK_CODE, self.hook, begin=a, end=a)
    budget = 100000

    def call(self, key=O['KEY_NEXT'], address=HOOKS['on_wm_keyup_before_fun'][0], args=None, event_type=276, gap=1000, stack=(), clear=True, debounce=False, count=None):
        if gap:
            self.advance(gap, clear=False)
        if not debounce:
            self.byte(10796024, 0)
        if clear:
            self.calls = []
            self.strokes = []
            self.rounded = []
            self.bands = []
            self.icons = []
            self.letters = []
            self.vg_calls = []
        self.word(self.event + O['EVENT_KEY'], key)
        self.word(self.event + O['EVENT_TYPE'], event_type)
        self.u.reg_write(UC_MIPS_REG_SP, 1879109632)
        self.u.reg_write(UC_MIPS_REG_RA, 16777216)
        self.u.reg_write(UC_MIPS_REG_T9, address)
        for i, v in enumerate(stack):
            self.word(1879109648 + 4 * i, v)
        for r, v in zip(REGS, args or (self.wm, self.event, 0, 0)):
            self.u.reg_write(r, v & 4294967295)
        for i, r in enumerate(SAVED):
            self.u.reg_write(r, 305397760 + i)
        self.count = count or self.budget
        self.u.emu_start(address, 16777216, count=self.count)
        assert self.u.reg_read(UC_MIPS_REG_PC) == 16777216, 'Instruction limit reached'
        assert self.u.reg_read(UC_MIPS_REG_SP) == 1879109632
        assert [self.u.reg_read(r) for r in SAVED] == [305397760 + i for i in range(len(SAVED))]
        if TRACE:
            v0 = self.u.reg_read(UC_MIPS_REG_V0)
            name = HOOK_NAMES.get(address) or render(self, 'p', address)
            px = [] if name != 'ringnav_image_add' or args is None else [hashlib.sha1(bytes(self.u.mem_read(self.get(args[2] + 20), self.get(args[2] + 4) * self.get(args[2] + 8)))).hexdigest()[:12]]
            trace('ret', name, render(self, 'd' if name == 'ringnav_style_color' else 'x', v0), *px)
        return signed(self.u.reg_read(UC_MIPS_REG_V0))

    def advance(self, ms, clear=True):
        """Run due UI timers deterministically, including the exact deadline; RET_REPEAT re-arms."""
        if clear:
            self.calls = []
        end = self.now + ms
        while self.timers:
            tid, (due, callback, ctx, period) = min(self.timers.items(), key=lambda item: item[1][0])
            if due > end:
                break
            self.now = due
            del self.timers[tid]
            info = self.alloc(88)
            self.word(info + 32, ctx)
            self.word(info + 40, tid)
            self.word(info + 64, period)
            ret = self.call(address=callback, args=(info, 0, 0, 0), gap=0, clear=False)
            assert ret in (0, 7, 8) and (ret != 8 or period > 0)
            if ret == 8 and tid not in self.timers:
                self.timers[tid] = (due + period, callback, ctx, period)
        self.now = end
        for a, values in list(self.props.items()):
            if len(values) != 5:
                continue
            start, duration, w, origin, goal = values
            elapsed = min(end - start, duration)
            self.word(w + O['W_X'], round(origin + (goal - origin) * elapsed / duration))
            if elapsed == duration:
                del self.props[a]
        for a, (start, duration, w, origin, goal) in list(self.slides.items()):
            elapsed = min(end - start, duration)
            self.word(w + O['SLIDE_OFFSET'], round(origin + (goal - origin) * elapsed / duration))
            self.word(a + O['ANIM_ELAPSED'], elapsed)
            if elapsed == duration:
                del self.slides[a]
                callback, ctx = self.slide_callbacks.pop(a)
                assert self.call(address=callback, args=(ctx, 0, 0, 0), gap=0, clear=False) == 7

    def confirm(self):
        """Single centre release followed by its full confirmation delay."""
        ret = self.call(O['KEY_CENTER'])
        self.advance(O['DOUBLE_CLICK_MS'], clear=False)
        return ret

    def release(self, gap=0):
        """Execute both the hook and the real stock downstream screen-toggle handler."""
        ret = self.call(O['KEY_CENTER'], gap=gap)
        if ret == 0:
            self.call(O['KEY_CENTER'], address=syms['on_wm_keyup_fun'], gap=0, clear=False)
        return ret

    def page(self, name='sysset_page', t='scroll_view'):
        child = self.node(t)
        self.top = self.node('window', name, [child])
        self.word(self.wm + O['W_KEY_TARGET'], self.top)
        if t == 'slide_menu':
            self.word(child + O['SLIDE_INDEX'], 0)
            self.nodes[child]['children'] = [self.entry(child) for _ in range(7)]
            self.word(child + 92, self.alloc())
        return child

    def page_list(self, n=10, height=96, extent=960, name='sysset_page'):
        """A page holding one scroll view of n 48px entries; returns (surface, entries)."""
        w = self.page(name)
        self.word(w + O['W_H'], height)
        self.word(w + O['VIEW_CONTENT_H'], extent)
        es = [self.entry(w, i * 48) for i in range(n)]
        self.nodes[w]['children'] = es
        return (w, es)

    def bind(self, rows, offset=0):
        """Reindex a recycled row pool the way the stock table rebind does."""
        for j, r in enumerate(rows):
            self.word(r + O['ROW_INDEX'], offset // 48 + j)
            self.word(r + O['W_Y'], (offset // 48 + j) * 48)

    def table_page(self, n=4, name='allmusic_page', rebind=False):
        """A table_client page of n recycled rows, optionally rebound; returns (surface, rows, entries)."""
        w = self.page(name, 'table_client')
        self.word(w + O['ROW_HEIGHT'], 48)
        self.word(w + O['TABLE_ROWS'], 20)
        self.word(w + O['W_H'], 96)
        rs = [self.node('table_row') for _ in range(n)]
        es = [self.entry(r) for r in rs]
        self.nodes[w]['children'] = rs
        for r, e in zip(rs, es):
            self.nodes[r]['children'] = [e]
            self.word(r + O['W_PARENT'], w)
        self.bind(rs)
        if rebind:
            self.rebind = lambda a, offset: self.bind(rs, offset)
        return (w, rs, es)

    def list_surface(self, virtual, n=20, rebind=True):
        """A plain list of n 48px rows or a four-row virtual table; optionally rebound."""
        if not virtual:
            w, es = self.page_list(n, extent=n * 48)
            return (w, None)
        w, rs, es = self.table_page(rebind=rebind)
        return (w, rs)

    def moved(self):
        return [x for x in self.calls if x[0] in ('scroll_view_set_offset', 'table_client_set_yoffset', 'scroll_view_scroll_delta_to', 'table_client_scroll_to', 'slide_menu_scroll_to_next', 'slide_menu_scroll_to_prev', 'widget_animator_scroll_set_params')]

    def dispatched(self):
        return [x for x in self.calls if x[0] == 'stock_dispatch']
try:
    checks = 0
except BaseException as _e:
    _SOFT.append((817, repr(_e)[:200]))
    print('FAIL line', 817, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def passed():
    global checks
    checks += 1
    trace(f'== {checks}')

def theme_at(m):
    return m.call(address=payload_syms['theme'], args=(0, 0, 0, 0), gap=0) & 4294967295

def set_motion(m, kind, ms, easing):
    m.u.mem_write(theme_at(m) + THEME_MOTION_OFF + 8 * (MOTION_WINDOW + MOTION_WIDGET).index(kind) + 4, struct.pack('<HB', ms, easing))

def set_hint(m, kind, hint):
    m.word(theme_at(m) + THEME_MOTION_OFF + 8 * MOTION_WINDOW.index(kind), m.string(hint))
try:
    t = Machine()
except BaseException as _e:
    _SOFT.append((828, repr(_e)[:200]))
    print('FAIL line', 828, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    buf = t.alloc(32)
except BaseException as _e:
    _SOFT.append((828, repr(_e)[:200]))
    print('FAIL line', 828, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    saved, TRACE = (TRACE, io.StringIO())
except BaseException as _e:
    _SOFT.append((833, repr(_e)[:200]))
    print('FAIL line', 833, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    t = Machine()
except BaseException as _e:
    _SOFT.append((834, repr(_e)[:200]))
    print('FAIL line', 834, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = t.node('label')
except BaseException as _e:
    _SOFT.append((834, repr(_e)[:200]))
    print('FAIL line', 834, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rect = t.alloc(16)
except BaseException as _e:
    _SOFT.append((834, repr(_e)[:200]))
    print('FAIL line', 834, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    text = t.alloc(8)
except BaseException as _e:
    _SOFT.append((834, repr(_e)[:200]))
    print('FAIL line', 834, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    paint = payload_syms['ringnav_paint']
except BaseException as _e:
    _SOFT.append((834, repr(_e)[:200]))
    print('FAIL line', 834, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    t.rockbox_mode = 0
except BaseException as _e:
    _SOFT.append((843, repr(_e)[:200]))
    print('FAIL line', 843, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    COVER = '(vg:vgcanvas_|canvas_|widget_set_prop|widget_move_resize$|widget_resize$|widget_set_opacity$|widget_use_style$|image_)'
except BaseException as _e:
    _SOFT.append((845, repr(_e)[:200]))
    print('FAIL line', 845, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    n = checks
except BaseException as _e:
    _SOFT.append((847, repr(_e)[:200]))
    print('FAIL line', 847, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    TRACE = saved
except BaseException as _e:
    _SOFT.append((848, repr(_e)[:200]))
    print('FAIL line', 848, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((851, repr(_e)[:200]))
    print('FAIL line', 851, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    exited = []
except BaseException as _e:
    _SOFT.append((851, repr(_e)[:200]))
    print('FAIL line', 851, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def child_syscall(u, number, unused):
    exited.append((u.reg_read(UC_MIPS_REG_V0), u.reg_read(UC_MIPS_REG_A0)))
    u.reg_write(UC_MIPS_REG_PC, 16777216)
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((861, repr(_e)[:200]))
    print('FAIL line', 861, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page('playermore_page')
except BaseException as _e:
    _SOFT.append((861, repr(_e)[:200]))
    print('FAIL line', 861, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    row = m.node('button', style='s_btn_forwardspeed')
except BaseException as _e:
    _SOFT.append((861, repr(_e)[:200]))
    print('FAIL line', 861, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[w]['children'] = [row]
except BaseException as _e:
    _SOFT.append((862, repr(_e)[:200]))
    print('FAIL line', 862, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    buttons = []
except BaseException as _e:
    _SOFT.append((863, repr(_e)[:200]))
    print('FAIL line', 863, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[row]['children'] = buttons
except BaseException as _e:
    _SOFT.append((867, repr(_e)[:200]))
    print('FAIL line', 867, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    GATES = [('g_backlight_status', 0), ('g_lockscreen_pageflag', 1), ('g_testmode_flag', 1), ('g_guideflag', 1), ('g_poweroff_state', 2), ('g_usblink_status', 2)]
except BaseException as _e:
    _SOFT.append((925, repr(_e)[:200]))
    print('FAIL line', 925, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((941, repr(_e)[:200]))
    print('FAIL line', 941, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page('home_page', 'slide_menu')
except BaseException as _e:
    _SOFT.append((941, repr(_e)[:200]))
    print('FAIL line', 941, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((944, repr(_e)[:200]))
    print('FAIL line', 944, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    hidden = m.node(visible=0)
except BaseException as _e:
    _SOFT.append((944, repr(_e)[:200]))
    print('FAIL line', 944, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    shown = m.node()
except BaseException as _e:
    _SOFT.append((944, repr(_e)[:200]))
    print('FAIL line', 944, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    pages = m.node('pages', children=[hidden, shown], active=1)
except BaseException as _e:
    _SOFT.append((944, repr(_e)[:200]))
    print('FAIL line', 944, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('window', 'artistinfo_page', [pages])
except BaseException as _e:
    _SOFT.append((945, repr(_e)[:200]))
    print('FAIL line', 945, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[hidden]['visible'] = 1
except BaseException as _e:
    _SOFT.append((946, repr(_e)[:200]))
    print('FAIL line', 946, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('window', 'sysset_page', [hidden, shown])
except BaseException as _e:
    _SOFT.append((948, repr(_e)[:200]))
    print('FAIL line', 948, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((962, repr(_e)[:200]))
    print('FAIL line', 962, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    a, b, _, _ = m.panes(96)
except BaseException as _e:
    _SOFT.append((962, repr(_e)[:200]))
    print('FAIL line', 962, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[a]['_ringnav_index'] = 0
except BaseException as _e:
    _SOFT.append((963, repr(_e)[:200]))
    print('FAIL line', 963, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[b]['_ringnav_index'] = 0
except BaseException as _e:
    _SOFT.append((966, repr(_e)[:200]))
    print('FAIL line', 966, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((971, repr(_e)[:200]))
    print('FAIL line', 971, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page()
except BaseException as _e:
    _SOFT.append((971, repr(_e)[:200]))
    print('FAIL line', 971, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((976, repr(_e)[:200]))
    print('FAIL line', 976, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, entries = m.page_list(5, extent=1000)
except BaseException as _e:
    _SOFT.append((976, repr(_e)[:200]))
    print('FAIL line', 976, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    names = [c[0] for c in m.calls]
except BaseException as _e:
    _SOFT.append((979, repr(_e)[:200]))
    print('FAIL line', 979, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bg, border = (names.index('stock_paint_bg'), names.index('stock_paint'))
except BaseException as _e:
    _SOFT.append((980, repr(_e)[:200]))
    print('FAIL line', 980, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OLED_T = theme_builtins()[0]
except BaseException as _e:
    _SOFT.append((986, repr(_e)[:200]))
    print('FAIL line', 986, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OLED_SCHEME = OLED_T['schemes'][next((o['def'] for o in OLED_T['opts'] if o['setting'] == 'SETTING_ACCENT'))]
except BaseException as _e:
    _SOFT.append((987, repr(_e)[:200]))
    print('FAIL line', 987, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OLED_TONES = OLED_SCHEME['tones']
except BaseException as _e:
    _SOFT.append((988, repr(_e)[:200]))
    print('FAIL line', 988, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OLED_BLUE, inset, radius = (color_t(OLED_TONES[0]), OLED_T['sel_inset'], OLED_T['sel_radius'])
except BaseException as _e:
    _SOFT.append((990, repr(_e)[:200]))
    print('FAIL line', 990, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def chord(j, h, D=None):
    D = h if D is None else D
    d = abs(2 * j + 1 - h) - (h - D)
    out = 8 * D - math.isqrt((D * D - d * d) * 64) if d > 0 else 0
    return ((out + 15) // 16, out & 15)

def bar_bands(x, y, w, h, radius, color=OLED_BLUE):
    want = []
    for j in range(h):
        n, frac = chord(j, h, 2 * radius) if radius else (0, 0)
        want.append((x + n, y + j, w - 2 * n, 1, color))
        if frac:
            edge = color & 16777215 | (16 - frac) * 17 << 24
            want.extend([(x + n - 1, y + j, 1, 1, edge), (x + w - n, y + j, 1, 1, edge)])
    return want
try:
    rows = [[b[:4:2] for b in m.bands if b[1] == j] for j in range(48)]
except BaseException as _e:
    _SOFT.append((1007, repr(_e)[:200]))
    print('FAIL line', 1007, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    c = Machine()
except BaseException as _e:
    _SOFT.append((1036, repr(_e)[:200]))
    print('FAIL line', 1036, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    cw, _ = c.page_list(5, extent=1000)
except BaseException as _e:
    _SOFT.append((1036, repr(_e)[:200]))
    print('FAIL line', 1036, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    s = Machine()
except BaseException as _e:
    _SOFT.append((1040, repr(_e)[:200]))
    print('FAIL line', 1040, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    s.clock_minutes = True
except BaseException as _e:
    _SOFT.append((1040, repr(_e)[:200]))
    print('FAIL line', 1040, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    title = s.node('hscroll_label', 'label_clock')
except BaseException as _e:
    _SOFT.append((1040, repr(_e)[:200]))
    print('FAIL line', 1040, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def top_level(t, name, children=()):
    w = s.node(t, name, children)
    s.word(w + O['W_PARENT'], s.wm)
    return w
try:
    bar = top_level('system_bar', 'system_bar', [title])
except BaseException as _e:
    _SOFT.append((1043, repr(_e)[:200]))
    print('FAIL line', 1043, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def bg(w):
    return s.call(address=HOOKS['widget_on_paint_background'][0], args=(w, s.canvas, 0, 0), gap=0)
try:
    t = Machine()
except BaseException as _e:
    _SOFT.append((1050, repr(_e)[:200]))
    print('FAIL line', 1050, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tiles = [t.node('button', 'button1', style='s_btn_listitem') for _ in range(3)]
except BaseException as _e:
    _SOFT.append((1051, repr(_e)[:200]))
    print('FAIL line', 1051, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    row = t.node('table_row', 'table_row', tiles, style='s_tablerow_black')
except BaseException as _e:
    _SOFT.append((1052, repr(_e)[:200]))
    print('FAIL line', 1052, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def stock(w):
    t.call(address=HOOKS['widget_on_paint_background'][0], args=(w, t.canvas, 0, 0), gap=0)
    return any((c[0] == 'stock_paint_bg' for c in t.calls))
try:
    card = (O['TILE_INSET'],) * 2 + (110 - 2 * O['TILE_INSET'], 160 - 2 * O['TILE_INSET'])
except BaseException as _e:
    _SOFT.append((1058, repr(_e)[:200]))
    print('FAIL line', 1058, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def clock():
    return s.nodes[title].get('text')

def writes():
    return [c for c in s.calls if c[0] == 'widget_set_text_utf8']

def reads():
    return [c for c in s.calls if c[0] in ('time@GLIBC_2.0', 'localtime_r')]

def minute(ms=1000):
    s.advance(ms)

def title_machine(**config):
    CONFIG.update(config)
    t = Machine()
    CONFIG.clear()
    t.clock_minutes = True
    label = t.node('hscroll_label', 'label_clock')
    bar = t.node('system_bar', 'system_bar', [label])
    t.word(bar + O['W_PARENT'], t.wm)
    t.word(syms['system_bar'], bar)

    def show(top):
        t.top = top
        t.call(address=HOOKS['widget_on_paint_background'][0], args=(bar, t.canvas, 0, 0), gap=0)
        t.wrote = any((c[0] == 'widget_set_text' for c in t.calls))
        t.advance(1)
        return t.nodes[label].get('text')

    def page(title, shown=0, name='scrlabel_title'):
        kids = [t.node('hscroll_label', name, text=title)] if title is not None else []
        w = t.node('window', 'some_page', [t.node('view', 'view_navbar', kids, visible=shown)])
        t.word(w + O['W_PARENT'], t.wm)
        return w
    return (t, label, show, page)
try:
    t, label, show, page = title_machine(TIMETITLE='0')
except BaseException as _e:
    _SOFT.append((1088, repr(_e)[:200]))
    print('FAIL line', 1088, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    time = show(t.node('window', 'home_page'))
except BaseException as _e:
    _SOFT.append((1088, repr(_e)[:200]))
    print('FAIL line', 1088, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    t.clock = (6, 15)
except BaseException as _e:
    _SOFT.append((1094, repr(_e)[:200]))
    print('FAIL line', 1094, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    t, label, show, page = title_machine(TIMETITLE='0')
except BaseException as _e:
    _SOFT.append((1096, repr(_e)[:200]))
    print('FAIL line', 1096, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    t, label, show, page = title_machine()
except BaseException as _e:
    _SOFT.append((1098, repr(_e)[:200]))
    print('FAIL line', 1098, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    s.clock = (0, 0)
except BaseException as _e:
    _SOFT.append((1105, repr(_e)[:200]))
    print('FAIL line', 1105, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    s.wall = (86400 + 3 * 883612800, 86400 + 2 * 883612800)
except BaseException as _e:
    _SOFT.append((1122, repr(_e)[:200]))
    print('FAIL line', 1122, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    s.clock = (7, 31)
except BaseException as _e:
    _SOFT.append((1122, repr(_e)[:200]))
    print('FAIL line', 1122, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    s.wall = (86400, 86400)
except BaseException as _e:
    _SOFT.append((1123, repr(_e)[:200]))
    print('FAIL line', 1123, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    s.clock = (7, 30)
except BaseException as _e:
    _SOFT.append((1123, repr(_e)[:200]))
    print('FAIL line', 1123, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    heading = s.node('hscroll_label', text='System Setting')
except BaseException as _e:
    _SOFT.append((1126, repr(_e)[:200]))
    print('FAIL line', 1126, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    nav = s.node('view', 'view_navbar', [s.node('image', 'img_return'), heading], visible=0)
except BaseException as _e:
    _SOFT.append((1127, repr(_e)[:200]))
    print('FAIL line', 1127, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    pages = [top_level('window', n, [nav] if n == 'sysset_page' else []) for n in ('sysset_page', 'home_page', 'playing_page', 'coverflow_page', 'tidal_main_page')] + [top_level('dialog', 'sortselect_dialog')]
except BaseException as _e:
    _SOFT.append((1128, repr(_e)[:200]))
    print('FAIL line', 1128, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    s.clock = (19, 31)
except BaseException as _e:
    _SOFT.append((1134, repr(_e)[:200]))
    print('FAIL line', 1134, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    s.top = pages[-1]
except BaseException as _e:
    _SOFT.append((1134, repr(_e)[:200]))
    print('FAIL line', 1134, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def battery_checks():

    def battery_bar(mode, **config):
        b = Machine()
        b.config = {'BATTERY': str(mode), **config}
        level = b.node('progress_bar', 'progress_battery', value=50)
        w = {n: b.node(t, n, list(c), visible=v, **kw) for n, t, c, v, kw in (('img_bt', 'image', (), 1, dict(image='bar_ldac')), ('img_wifi', 'image', (), 1, {}), ('label_battery', 'label', (), 0, {}), ('view_battery', 'view', (), 0, {}), ('img_battery', 'image', (level,), 1, dict(image='bar_battery')))}
        b.word(w['img_wifi'] + O['W_W'], 14)
        b.word(w['view_battery'] + O['W_H'], 30)
        row = b.node('view', 'view_right', list(w.values()))
        b.word(row + O['W_H'], 30)
        b.word(w['view_battery'] + O['W_PARENT'], row)
        bar = b.node('system_bar', 'system_bar', [row])
        w['progress_battery'] = level
        b.word(bar + O['W_PARENT'], b.wm)
        b.word(syms['system_bar'], bar)

        def paint(x=bar):
            b.call(address=HOOKS['widget_on_paint_background'][0], args=(x, b.canvas, 0, 0), gap=0)
            b.advance(0, clear=False)
        return (b, bar, w, paint)

    def shown(b, w):
        return [n for n in ('img_battery', 'label_battery', 'view_battery') if b.nodes[w[n]]['visible']]

    def alpha(b, w):
        return b.get(w['img_bt'] + 52) & 255
    b, bar, w, paint = battery_bar(1)
    paint()
    assert b.nodes[w['img_bt']]['image'] == 'bar_ldac' and alpha(b, w) == 255 and (shown(b, w) == ['view_battery']) and (len(b.timers) == 1)
    b.advance(O['CODEC_MS'] - 1)
    paint()
    assert b.nodes[w['img_bt']]['image'] == 'bar_ldac' and alpha(b, w) == 255
    steps = []
    for _ in range(2 * O['CODEC_STEPS']):
        b.advance(O['CODEC_STEP_MS'] if steps else 1)
        steps.append((alpha(b, w), b.nodes[w['img_bt']]['image']))
    n = O['CODEC_STEPS']
    assert steps == [(255 * abs(n - k) // n, 'bar_ldac' if k < n else O_GLYPH) for k in range(1, 2 * n + 1)] and (not b.timers), steps
    paint()
    assert shown(b, w) == ['view_battery'] and b.get(w['view_battery'] + O['W_W']) == O['BATT_PCT_W']
    passed()
    b.nodes[w['img_bt']]['image'] = 'bar_ldac'
    paint()
    assert b.nodes[w['img_bt']]['image'] == O_GLYPH and (not b.timers) and (shown(b, w) == ['view_battery'])
    passed()
    b.nodes[w['img_bt']]['image'] = 'bar_aac'
    paint()
    assert b.nodes[w['img_bt']]['image'] == 'bar_aac' and len(b.timers) == 1 and (shown(b, w) == ['view_battery'])
    passed()
    b.nodes[w['img_bt']]['visible'] = 0
    paint()
    assert not b.timers and alpha(b, w) == 255
    b.nodes[w['img_bt']]['visible'] = 1
    paint()
    assert len(b.timers) == 1
    passed()
    b, bar, w, paint = battery_bar(1)
    b.nodes[w['img_bt']]['visible'] = 0
    lcd = lambda: (b.lcd_colors(), b.get(b.lcd + O['LCD_TEXT_COLOR']), b.get(b.canvas + O['CANVAS_ALIGN_V']), b.get(b.canvas + O['CANVAS_ALIGN_H']))

    def slot():
        b.bands = []
        b.letters = []
        paint(w['view_battery'])
        assert lcd() == before
        return (b.bands, b.letters)
    for image, value, rgb in (('bar_battery', 100, 16777215), ('bar_battery', 88, 16777215), ('bar_charge', 7, O['BATT_CHARGE_RGB']), ('bar_lowcharge', 5, O['BATT_LOW_RGB'])):
        b.nodes[w['img_battery']]['image'] = image
        b.nodes[w['label_battery']]['text'] = f'{value}%'
        paint()
        paint(w['view_battery'])
        paint()
        before = lcd()
        bands, letters = slot()
        pct = 11 * len(str(value)) + 11
        assert b.get(w['view_battery'] + O['W_W']) == pct and (not bands)
        assert [(l['text'], l['x'], l['y'], l['color']) for l in letters] == [(f'{value}%', (pct - 10 * len(f'{value}%')) // 2, (30 + 1 - letters[0]['font'][1]) // 2, color_t(rgb))]
    passed()
    b, bar, w, paint = battery_bar(1)
    b.nodes[w['label_battery']]['text'] = '100%'
    b.nodes[w['img_bt']]['image'] = 'bar_aptxhd'
    paint()
    assert b.get(w['view_battery'] + O['W_W']) == O['BATT_BODY_W'] + O['BATT_NUB_W']
    b.advance(O['CODEC_MS'])
    for _ in range(O['CODEC_STEPS']):
        b.advance(O['CODEC_STEP_MS'])
    b.calls = []
    paint()
    assert b.get(w['view_battery'] + O['W_W']) == O['BATT_PCT_W']
    assert ('widget_invalidate_force', bar) in [c[:2] for c in b.calls]
    for invalid in ('2', '3', '4', '9', '-1', '33', 'bad', ''):
        b, bar, w, paint = battery_bar(invalid)
        paint()
        assert b.get(w['view_battery'] + O['W_W']) == O['BATT_BODY_W'] + O['BATT_NUB_W'], invalid
    for mode in range(2):
        for bt in (0, 1):
            for wifi in (0, 1):
                b, bar, w, paint = battery_bar(mode)
                b.nodes[w['img_bt']].update(visible=bt, image=O_GLYPH)
                b.nodes[w['img_wifi']]['visible'] = wifi
                b.nodes[w['label_battery']]['text'] = '100%'
                paint()
                assert shown(b, w) == ['view_battery']
                assert b.get(w['view_battery'] + O['W_W']) == (O['BATT_PCT_W'] if mode == 1 else O['BATT_BODY_W'] + O['BATT_NUB_W'])
    passed()
    b, bar, w, paint = battery_bar(1)
    assert b.u.mem_read(payload_syms['geom_vals'], 2) == struct.pack('<h', 30)
    for bar_h in (26, 30):
        b.u.mem_write(payload_syms['geom_vals'], struct.pack('<h', bar_h))
        b.word(w['view_battery'] + O['W_H'], 0)
        paint()
        assert b.get(w['view_battery'] + O['W_H']) == bar_h
    passed()
try:
    m.on_click = lambda a, b: None if m.selected(w) == 1 else (_ for _ in ()).throw(AssertionError('late selection'))
except BaseException as _e:
    _SOFT.append((1232, repr(_e)[:200]))
    print('FAIL line', 1232, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.on_click = None
except BaseException as _e:
    _SOFT.append((1234, repr(_e)[:200]))
    print('FAIL line', 1234, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 1
except BaseException as _e:
    _SOFT.append((1247, repr(_e)[:200]))
    print('FAIL line', 1247, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 0
except BaseException as _e:
    _SOFT.append((1250, repr(_e)[:200]))
    print('FAIL line', 1250, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[payload_syms['stock_input_trampoline']] = 'stock_input'
except BaseException as _e:
    _SOFT.append((1255, repr(_e)[:200]))
    print('FAIL line', 1255, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[HOOKS['main_loop_sleep_default'][0] + 12] = 'stock_sleep'
except BaseException as _e:
    _SOFT.append((1256, repr(_e)[:200]))
    print('FAIL line', 1256, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def wm_event(kind):
    return m.call(address=HOOKS['window_manager_dispatch_input_event'][0], args=(m.wm, m.event, 0, 0), event_type=kind, gap=0)

def heal():
    m.calls = []
    m.call(address=HOOKS['main_loop_sleep_default'][0], args=(4660, 0, 0, 0), gap=0)
    return [c for c in m.calls if c[0] in ('widget_dispatch_event_to_target_recursive', 'stock_input')]

def opens():
    return [c for c in m.calls if c[0] == 'fopen@GLIBC_2.2' and m.text(c[1]) == '/mnt/data/q2-wheel.log']
try:
    m.pressed = 1
except BaseException as _e:
    _SOFT.append((1263, repr(_e)[:200]))
    print('FAIL line', 1263, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.now += O['STALE_TOUCH_MS'] - 1
except BaseException as _e:
    _SOFT.append((1264, repr(_e)[:200]))
    print('FAIL line', 1264, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.now += 1
except BaseException as _e:
    _SOFT.append((1265, repr(_e)[:200]))
    print('FAIL line', 1265, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    steps = heal()
except BaseException as _e:
    _SOFT.append((1265, repr(_e)[:200]))
    print('FAIL line', 1265, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.now += O['STALE_TOUCH_MS']
except BaseException as _e:
    _SOFT.append((1272, repr(_e)[:200]))
    print('FAIL line', 1272, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 0
except BaseException as _e:
    _SOFT.append((1273, repr(_e)[:200]))
    print('FAIL line', 1273, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.now += O['STALE_TOUCH_MS']
except BaseException as _e:
    _SOFT.append((1273, repr(_e)[:200]))
    print('FAIL line', 1273, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 1
except BaseException as _e:
    _SOFT.append((1275, repr(_e)[:200]))
    print('FAIL line', 1275, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.now += O['STALE_TOUCH_MS'] - 1
except BaseException as _e:
    _SOFT.append((1275, repr(_e)[:200]))
    print('FAIL line', 1275, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.now += 1
except BaseException as _e:
    _SOFT.append((1275, repr(_e)[:200]))
    print('FAIL line', 1275, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.now += O['STALE_TOUCH_MS']
except BaseException as _e:
    _SOFT.append((1276, repr(_e)[:200]))
    print('FAIL line', 1276, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 0
except BaseException as _e:
    _SOFT.append((1277, repr(_e)[:200]))
    print('FAIL line', 1277, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((1279, repr(_e)[:200]))
    print('FAIL line', 1279, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(8, height=192, extent=1000)
except BaseException as _e:
    _SOFT.append((1279, repr(_e)[:200]))
    print('FAIL line', 1279, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def destroy(a, b):
    m.nodes.clear()
    m.top = 0
try:
    m.on_click = destroy
except BaseException as _e:
    _SOFT.append((1286, repr(_e)[:200]))
    print('FAIL line', 1286, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ARTWORK = {5454788: [('img_icon', (4, 9, 52, 52), (0, 8, 52, 52)), ('git_playing', (0, 9, 52, 52), (0, 8, 52, 52))], 4900232: [('img_icon', (48, 0, 50, 70), (48, 0, 52, 68)), ('img_gifbg', (0, 9, 50, 52), (0, 8, 50, 52))], 4930036: [('img_icon', (48, 0, 50, 70), (48, 0, 52, 68))], 4876560: [('img_icon', (48, 0, 50, 70), (48, 0, 52, 68)), ('img_gifbg', (0, 9, 50, 52), (0, 8, 50, 52))]}
except BaseException as _e:
    _SOFT.append((1293, repr(_e)[:200]))
    print('FAIL line', 1293, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def native_row_layout(m, button):
    m.handlers.pop(syms['widget_layout_children'], None)
    m.mock('widget_vtable_on_layout_children', 'widget_layout_self', 'widget_layout_floating_children', 'widget_get_children_for_layout', 'darray_init', 'darray_deinit', 'widget_move_resize_ex')
    for node, props in m.nodes.items():
        children = props['children']
        if children:
            array = m.alloc(12)
            values = m.alloc(4 * len(children))
            for i, child in enumerate(children):
                m.word(values + 4 * i, child)
            m.word(array, len(children))
            m.word(array + 8, values)
            m.word(node + 92, array)
    assert m.call(address=syms['widget_layout_children'], args=(button, 0, 0, 0)) == 0

def check_title_bounds(m, button, drill=False):
    children = m.nodes[button]['children']
    text = next((c for c in children if m.nodes[c]['type'] == 'hscroll_label' or any((m.nodes[t]['type'] == 'hscroll_label' for t in m.nodes[c]['children']))))
    title = next((c for c in m.nodes[text]['children'] if m.nodes[c]['type'] == 'hscroll_label'), text)
    layout = m.get(button + O['W_CHILDREN_LAYOUT'])
    vtable = m.get(layout + O['CHILDREN_LAYOUT_VTABLE'])
    stock_vtable = O['DEFAULT_LAYOUT_VTABLE']
    assert vtable != stock_vtable
    for i in (0, 1, 3, 4, 5, 6, 7):
        assert m.get(vtable + 4 * i) == m.get(stock_vtable + 4 * i)
    margin = m.u.mem_read(layout + O['DEFAULT_LAYOUT_X_MARGIN'], 1)[0]
    gap = m.u.mem_read(layout + O['DEFAULT_LAYOUT_SPACING'], 1)[0]
    widths = {c: m.get(c + O['W_W']) for c in children}
    title_width = m.get(title + O['W_W'])
    title_yh = (m.get(title + O['W_Y']), m.get(title + O['W_H']))
    attributes = {k: v for k, v in m.nodes[title].items() if k == 'style' or 'hscroll' in k}
    for artwork in (0, 1):
        for controls in (0, 1):
            for choice in (0, 1):
                for c in children:
                    if c == text:
                        continue
                    name = m.nodes[c]['name']
                    m.nodes[c]['visible'] = choice if name.startswith('img_choice') else controls if c in children[children.index(text) + 1:] else artwork
                for label in ('Short', 'A very long title ' * 12, '日本語の長い曲名と歌手 - සිංහල - العربية'):
                    m.nodes[title]['text'] = label
                    native_row_layout(m, button)
                    before = children[:children.index(text)]
                    after = children[children.index(text) + 1:]
                    left = margin + sum((widths[c] + gap for c in before if m.nodes[c]['visible']))
                    more = any((m.nodes[c]['name'].startswith('img_more') and m.nodes[c]['visible'] for c in children))
                    edge = O['CHEVRON_W'] + gap if drill and (not more) else margin
                    right = m.get(button + O['W_W']) - edge - sum((widths[c] + gap for c in after if m.nodes[c]['visible']))
                    assert m.get(text + O['W_X']) == left
                    assert m.nodes[title].get('hscroll_label_set_only_focus') == 1
                    assert left + m.get(text + O['W_W']) == right
                    if title != text:
                        assert m.get(title + O['W_X']) + m.get(title + O['W_W']) == m.get(text + O['W_W'])
                    cursor = right + gap
                    for c in after:
                        if m.nodes[c]['visible']:
                            assert m.get(c + O['W_X']) == cursor
                            cursor += widths[c] + gap
                    assert cursor - gap == m.get(button + O['W_W']) - edge
                    assert (m.get(title + O['W_Y']), m.get(title + O['W_H'])) == title_yh
                    assert m.nodes[title]['text'] == label
                    assert all((m.nodes[title][k] == v for k, v in attributes.items()))
                    for c in children:
                        if c != text:
                            assert m.get(c + O['W_W']) == widths[c]

def check_album_tile(m, button):

    def descendants(w):
        return [c for child in m.nodes[w]['children'] for c in [child, *descendants(child)]]

    def geometry(w):
        return tuple((signed(m.get(w + O[k])) for k in ('W_X', 'W_Y', 'W_W', 'W_H')))
    children = descendants(button)
    original = {c: geometry(c) for c in children}
    draw_types = [c for c in children if m.nodes[c]['type'] == 'image' and original[c][2:] == (110, 110)]
    tile = geometry(button)
    for c in children:
        if m.nodes[c]['type'] == 'hscroll_label':
            m.nodes[c]['text'] = 'A very long album or artist label ' * 10
    for _ in range(3):
        m.call(address=payload_syms['album_children'], args=(button, 0, 0, 0), gap=0)
        assert geometry(button) == tile
        for c in draw_types:
            assert m.nodes[c].get('draw_type', 0) == O['IMAGE_DRAW_SCALE_DOWN']
        for c, (x, y, w, h) in original.items():
            actual = geometry(c)
            if w == h == 110:
                inset = 6 if m.get(c + O['W_PARENT']) == button else 0
                assert actual == (inset, inset, 98, 98), actual
            elif m.nodes[c]['type'] == 'hscroll_label':
                assert actual == (6, y - 2, 98, h), actual
                assert actual[1] + h <= 154
                assert m.nodes[c]['text'].startswith('A very long')
            else:
                assert actual == (x, y, w, h)
    m.calls = []
    m.call(address=payload_syms['album_children'], args=(button, 0, 0, 0), gap=0)
    assert not any((c[0] in ('widget_move_resize', 'image_set_draw_type') for c in m.calls))
    m.paint(button)
    assert all((geometry(c)[2] == 98 for c in children if original[c][2] == 110))
    passed()
from ipod import decode

def asset_node(m, n, parent):
    kind, rect, props, children = n
    w = m.node(kind, props.get('name', ''))
    m.word(w + O['W_PARENT'], parent)
    for k, v in zip(('W_X', 'W_Y', 'W_W', 'W_H'), rect):
        m.word(w + O[k], v)
    m.nodes[w]['children'] = [asset_node(m, c, w) for c in children]
    return w
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((1411, repr(_e)[:200]))
    print('FAIL line', 1411, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    root = asset_node(m, decode((B / 'ui/localmusic/album_page.bin').read_bytes()), m.wm)
except BaseException as _e:
    _SOFT.append((1411, repr(_e)[:200]))
    print('FAIL line', 1411, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = root
except BaseException as _e:
    _SOFT.append((1411, repr(_e)[:200]))
    print('FAIL line', 1411, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def oled_boot(m, fail=None):
    m.record_syscalls(fail or (lambda page: False))
    m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'], 16)] = 'stock_ui_load'
    m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0], args=(m.string('system_bar'), 0, 0, 0), gap=0)
    return [m.text(c[1]) for c in m.calls if c[0] == 'stock_ui_load']
try:
    OLED_G = OLED_T['geoms']['oled']
except BaseException as _e:
    _SOFT.append((1474, repr(_e)[:200]))
    print('FAIL line', 1474, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((1515, repr(_e)[:200]))
    print('FAIL line', 1515, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page('allmusic_page', 'table_client')
except BaseException as _e:
    _SOFT.append((1515, repr(_e)[:200]))
    print('FAIL line', 1515, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    button = m.nodes[m.nodes[w]['children'][0]]['children'][0]
except BaseException as _e:
    _SOFT.append((1520, repr(_e)[:200]))
    print('FAIL line', 1520, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def album_row(boot):
    m = Machine()
    boot(m)
    w = m.page('folder_page', 'table_client')
    m.mock('table_row_create', 'button_create', 'image_create', 'view_create', 'hscroll_label_create', 'gif_image_create', 'widget_use_style', 'widget_set_name', 'widget_set_children_layout', 'image_set_draw_type', 'image_base_set_image', 'set_hscroll_label_attribute', 'widget_set_visible')
    assert m.call(address=4876560, args=(w, w, 1, 0)) == 0
    return (m, m.nodes[m.nodes[w]['children'][0]]['children'][0])
try:
    m, button = album_row(oled_boot)
except BaseException as _e:
    _SOFT.append((1533, repr(_e)[:200]))
    print('FAIL line', 1533, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    title, meta = (next((n for n, v in m.nodes.items() if v['name'] == name)) for name in ('scrlabel1_name', 'scrlabel2_name'))
except BaseException as _e:
    _SOFT.append((1534, repr(_e)[:200]))
    print('FAIL line', 1534, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    info = next((n for n, v in m.nodes.items() if v['name'] == 'view_info'))
except BaseException as _e:
    _SOFT.append((1542, repr(_e)[:200]))
    print('FAIL line', 1542, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    art_ = next((n for n, v in m.nodes.items() if v['name'] == 'img_icon'))
except BaseException as _e:
    _SOFT.append((1542, repr(_e)[:200]))
    print('FAIL line', 1542, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rx = m.get(button + O['W_X'])
except BaseException as _e:
    _SOFT.append((1543, repr(_e)[:200]))
    print('FAIL line', 1543, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[next((n for n, v in m.nodes.items() if v['name'] == 'img_choice'))]['visible'] = 0
except BaseException as _e:
    _SOFT.append((1545, repr(_e)[:200]))
    print('FAIL line', 1545, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[meta]['style:normal:text_color'] = 0
except BaseException as _e:
    _SOFT.append((1547, repr(_e)[:200]))
    print('FAIL line', 1547, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def long_machine():
    m = Machine()
    m.mock('netdisk_folder_clear', 'navigator_back_to_home', 'awake_screen', 'getFormatString', 'navigator_to_with_context', 'navigator_window_is_exist')
    return m

def long_return(m):
    return m.call(170, address=syms['on_wm_keylong_fun'], event_type=273, gap=0)

def destinations(m):
    return [c for c in m.calls if c[0] in ('navigator_back_to_home', 'navigator_switch_to_with_context')]

def ui_pass(m, gap=0):
    """One UI loop pass (ringnav_sleep), gap ms on; stock's own sleep is stubbed."""
    m.handlers[HOOKS['main_loop_sleep_default'][0] + 12] = 'stock_sleep'
    assert m.call(address=HOOKS['main_loop_sleep_default'][0], args=(4660, 0, 0, 0), gap=gap) == 0
try:
    m = long_machine()
except BaseException as _e:
    _SOFT.append((1636, repr(_e)[:200]))
    print('FAIL line', 1636, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = long_machine()
except BaseException as _e:
    _SOFT.append((1643, repr(_e)[:200]))
    print('FAIL line', 1643, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def quits(m):
    return [c[1:3] for c in m.calls if c[0] == 'dialog_quit']
try:
    m = long_machine()
except BaseException as _e:
    _SOFT.append((1683, repr(_e)[:200]))
    print('FAIL line', 1683, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    page = m.top
except BaseException as _e:
    _SOFT.append((1683, repr(_e)[:200]))
    print('FAIL line', 1683, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('dialog', 'statusbar_dialog')
except BaseException as _e:
    _SOFT.append((1683, repr(_e)[:200]))
    print('FAIL line', 1683, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = page
except BaseException as _e:
    _SOFT.append((1685, repr(_e)[:200]))
    print('FAIL line', 1685, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = long_machine()
except BaseException as _e:
    _SOFT.append((1687, repr(_e)[:200]))
    print('FAIL line', 1687, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('dialog', 'statusbar_dialog')
except BaseException as _e:
    _SOFT.append((1687, repr(_e)[:200]))
    print('FAIL line', 1687, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def empty_songs(shown):
    m = Machine()
    btn = m.node('button', 'btn_upmusic')
    view = m.node('view', 'view', [m.node('image', 'image'), m.node('label', 'label_scanfolder'), btn], visible=shown)
    m.top = m.node('window', 'allmusic_page', [m.node('view', 'view_navbar_allplay'), view, m.node('table_view', 'table_view', visible=1 - shown)])
    for w in (m.top, view):
        for c in m.nodes[w]['children']:
            m.word(c + O['W_PARENT'], w)
    m.word(m.top + O['W_PARENT'], m.wm)
    m.nodes[m.wm] = {'children': [m.top]}
    m.word(m.wm + O['W_KEY_TARGET'], m.top)
    return (m, btn)
try:
    m, btn = empty_songs(1)
except BaseException as _e:
    _SOFT.append((1706, repr(_e)[:200]))
    print('FAIL line', 1706, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    r = m.call(O['KEY_CENTER'])
except BaseException as _e:
    _SOFT.append((1707, repr(_e)[:200]))
    print('FAIL line', 1707, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, btn = empty_songs(0)
except BaseException as _e:
    _SOFT.append((1710, repr(_e)[:200]))
    print('FAIL line', 1710, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = long_machine()
except BaseException as _e:
    _SOFT.append((1713, repr(_e)[:200]))
    print('FAIL line', 1713, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.config['HOLDRETURN'] = '1'
except BaseException as _e:
    _SOFT.append((1713, repr(_e)[:200]))
    print('FAIL line', 1713, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    home = m.top
except BaseException as _e:
    _SOFT.append((1713, repr(_e)[:200]))
    print('FAIL line', 1713, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    np = m.node('window', 'playing_page')
except BaseException as _e:
    _SOFT.append((1714, repr(_e)[:200]))
    print('FAIL line', 1714, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.wm] = {'children': [home, np]}
except BaseException as _e:
    _SOFT.append((1715, repr(_e)[:200]))
    print('FAIL line', 1715, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = np
except BaseException as _e:
    _SOFT.append((1715, repr(_e)[:200]))
    print('FAIL line', 1715, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((1715, repr(_e)[:200]))
    print('FAIL line', 1715, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((1737, repr(_e)[:200]))
    print('FAIL line', 1737, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, rows, entries = m.table_page(rebind=True)
except BaseException as _e:
    _SOFT.append((1737, repr(_e)[:200]))
    print('FAIL line', 1737, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((1744, repr(_e)[:200]))
    print('FAIL line', 1744, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, rows, entries = m.table_page(rebind=True)
except BaseException as _e:
    _SOFT.append((1744, repr(_e)[:200]))
    print('FAIL line', 1744, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    oldtop = m.top
except BaseException as _e:
    _SOFT.append((1755, repr(_e)[:200]))
    print('FAIL line', 1755, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = oldtop
except BaseException as _e:
    _SOFT.append((1756, repr(_e)[:200]))
    print('FAIL line', 1756, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def walk(n, steps, texts=(), extent=1000, **kw):
    """Paint a fresh labelled list of n entries, then wheel down steps rows."""
    m = Machine()
    w, es = m.page_list(n, extent=extent, **kw)
    m.label(es, texts)
    m.paint(w)
    for _ in range(steps):
        assert m.call() == 11
    assert m.selected(w) == steps
    return (m, w, es)
try:
    m, w, es = walk(10, 5)
except BaseException as _e:
    _SOFT.append((1767, repr(_e)[:200]))
    print('FAIL line', 1767, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w2, es2 = m.page_list(10, extent=1000)
except BaseException as _e:
    _SOFT.append((1768, repr(_e)[:200]))
    print('FAIL line', 1768, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ms = OLED_T['recall_ms']
except BaseException as _e:
    _SOFT.append((1773, repr(_e)[:200]))
    print('FAIL line', 1773, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rm, _, _ = walk(10, 5)
except BaseException as _e:
    _SOFT.append((1774, repr(_e)[:200]))
    print('FAIL line', 1774, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rw, _ = rm.page_list(10, extent=1000)
except BaseException as _e:
    _SOFT.append((1775, repr(_e)[:200]))
    print('FAIL line', 1775, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rm.calls = []
except BaseException as _e:
    _SOFT.append((1775, repr(_e)[:200]))
    print('FAIL line', 1775, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w3, es3 = m.page_list(10, extent=1000, name='display_page')
except BaseException as _e:
    _SOFT.append((1781, repr(_e)[:200]))
    print('FAIL line', 1781, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w4, es4 = m.page_list(10, extent=1000)
except BaseException as _e:
    _SOFT.append((1783, repr(_e)[:200]))
    print('FAIL line', 1783, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w5, es5 = m.page_list(2, extent=300)
except BaseException as _e:
    _SOFT.append((1787, repr(_e)[:200]))
    print('FAIL line', 1787, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((1792, repr(_e)[:200]))
    print('FAIL line', 1792, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(10, extent=1000)
except BaseException as _e:
    _SOFT.append((1792, repr(_e)[:200]))
    print('FAIL line', 1792, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w2, es2 = m.page_list(10, extent=1000)
except BaseException as _e:
    _SOFT.append((1796, repr(_e)[:200]))
    print('FAIL line', 1796, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, w, es = walk(6, 4, [f'track {i}' for i in range(6)])
except BaseException as _e:
    _SOFT.append((1800, repr(_e)[:200]))
    print('FAIL line', 1800, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w2, es2 = m.page_list(6, extent=1000)
except BaseException as _e:
    _SOFT.append((1801, repr(_e)[:200]))
    print('FAIL line', 1801, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((1824, repr(_e)[:200]))
    print('FAIL line', 1824, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(4, extent=1000)
except BaseException as _e:
    _SOFT.append((1824, repr(_e)[:200]))
    print('FAIL line', 1824, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[syms['strcmp@GLIBC_2.0']] = 'tk_strcmp'
except BaseException as _e:
    _SOFT.append((1826, repr(_e)[:200]))
    print('FAIL line', 1826, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def stock_text(e, text):
    m.word(e + 116, syms['g_label_vtable'])
    m.word(e + 56, len(text))
    m.word(e + 64, m.wide_string(text))
try:
    w2, es2 = m.page_list(4, extent=1000)
except BaseException as _e:
    _SOFT.append((1839, repr(_e)[:200]))
    print('FAIL line', 1839, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((1848, repr(_e)[:200]))
    print('FAIL line', 1848, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(8, name='folder_page')
except BaseException as _e:
    _SOFT.append((1849, repr(_e)[:200]))
    print('FAIL line', 1849, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w2, es2 = m.page_list(8, name='folder_page')
except BaseException as _e:
    _SOFT.append((1852, repr(_e)[:200]))
    print('FAIL line', 1852, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w3, es3 = m.page_list(8, name='folder_page')
except BaseException as _e:
    _SOFT.append((1855, repr(_e)[:200]))
    print('FAIL line', 1855, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    costs = []
except BaseException as _e:
    _SOFT.append((1891, repr(_e)[:200]))
    print('FAIL line', 1891, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def long_view(h, top=100):
    lm = Machine()
    lw, les = lm.page_list(500, height=266, extent=500 * h)
    lm.word(lw + O['W_W'], 375)
    for i, e in enumerate(les):
        lm.word(e + O['W_Y'], i * h)
        lm.word(e + O['W_H'], h)
    lm.word(lw + O['SCROLL_Y'], top * h)
    count = [0]
    hook = lm.u.hook_add(UC_HOOK_CODE, lambda *a: count.__setitem__(0, count[0] + 1))
    lm.call(address=HOOKS['widget_on_paint_background'][0], args=(lw, lm.canvas, 0, 0), gap=0)
    lm.u.hook_del(hook)
    print(f'OLED scroll view of 500 {h} px rows from row {top}: {count[0]} instructions for its first paint')
    assert count[0] < 100000, count[0]

class SearchMachine(Machine):

    def __init__(self, name='search_dialog', keyboard=True):
        super().__init__()
        self.dialog = self.node('dialog', name)
        self.edit = self.node('edit', name='search_edit')
        self.nodes[self.dialog]['children'] = [self.edit]
        self.word(self.edit + O['W_PARENT'], self.dialog)
        self.nodes[self.edit]['focused'] = 1
        self.top = self.node('keyboard', 'keyboard') if keyboard else self.dialog
        self.im = self.alloc(144)
        self.word(self.im + O['IM_WIDGET'], self.edit)
        self.word(self.im + 124, 16779264)
        self.handlers[16779264] = 'search:close'
        self.handlers.pop(syms['input_method_request'], None)
        self.mock('input_method', prefix='search:')
        self.mock('widget_dispatch_simple_event')

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if name == 'search:input_method':
            self.calls.append((name,))
            self.returns(self.im)
        elif name == 'search:close':
            a, b = [u.reg_read(r) for r in REGS[:2]]
            assert a == self.im and b == 0
            self.calls.append((name, a, b))
            self.word(self.im + O['IM_WIDGET'], 0)
            self.top = self.dialog
            self.returns(0)
        else:
            super().hook(u, address, size, unused)
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2057, repr(_e)[:200]))
    print('FAIL line', 2057, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    d = m.node('dialog', 'confirminfo_dialog')
except BaseException as _e:
    _SOFT.append((2057, repr(_e)[:200]))
    print('FAIL line', 2057, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.clip = (0, 0, 375, 320)
except BaseException as _e:
    _SOFT.append((2058, repr(_e)[:200]))
    print('FAIL line', 2058, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = d
except BaseException as _e:
    _SOFT.append((2058, repr(_e)[:200]))
    print('FAIL line', 2058, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    buttons = [m.entry(d, 220) for _ in range(2)]
except BaseException as _e:
    _SOFT.append((2059, repr(_e)[:200]))
    print('FAIL line', 2059, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[d]['children'] = buttons
except BaseException as _e:
    _SOFT.append((2059, repr(_e)[:200]))
    print('FAIL line', 2059, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    pair = (53, 242)
except BaseException as _e:
    _SOFT.append((2060, repr(_e)[:200]))
    print('FAIL line', 2060, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    frame = [r for r in m.rounded if r['kind'] == 'stroke']
except BaseException as _e:
    _SOFT.append((2064, repr(_e)[:200]))
    print('FAIL line', 2064, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[d]['name'] = 'autoshutdown_dialog'
except BaseException as _e:
    _SOFT.append((2074, repr(_e)[:200]))
    print('FAIL line', 2074, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[d]['children'] = [buttons[0]]
except BaseException as _e:
    _SOFT.append((2074, repr(_e)[:200]))
    print('FAIL line', 2074, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2077, repr(_e)[:200]))
    print('FAIL line', 2077, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    d = m.node('dialog', 'confirminfo_dialog')
except BaseException as _e:
    _SOFT.append((2077, repr(_e)[:200]))
    print('FAIL line', 2077, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.clip = (0, 0, 375, 320)
except BaseException as _e:
    _SOFT.append((2078, repr(_e)[:200]))
    print('FAIL line', 2078, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = d
except BaseException as _e:
    _SOFT.append((2078, repr(_e)[:200]))
    print('FAIL line', 2078, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rows = [m.entry(d, 208 + 48 * i) for i in range(2)]
except BaseException as _e:
    _SOFT.append((2079, repr(_e)[:200]))
    print('FAIL line', 2079, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[d]['children'] = rows
except BaseException as _e:
    _SOFT.append((2079, repr(_e)[:200]))
    print('FAIL line', 2079, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2094, repr(_e)[:200]))
    print('FAIL line', 2094, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    d = m.node('dialog', 'confirminfo_dialog', [])
except BaseException as _e:
    _SOFT.append((2094, repr(_e)[:200]))
    print('FAIL line', 2094, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = d
except BaseException as _e:
    _SOFT.append((2094, repr(_e)[:200]))
    print('FAIL line', 2094, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[d]['children'] = [m.entry(d, 220) for _ in range(2)]
except BaseException as _e:
    _SOFT.append((2095, repr(_e)[:200]))
    print('FAIL line', 2095, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[payload_syms['stock_input_trampoline']] = 'stock_input'
except BaseException as _e:
    _SOFT.append((2097, repr(_e)[:200]))
    print('FAIL line', 2097, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2109, repr(_e)[:200]))
    print('FAIL line', 2109, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('window', 'playing_page')
except BaseException as _e:
    _SOFT.append((2109, repr(_e)[:200]))
    print('FAIL line', 2109, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.animating = 1
except BaseException as _e:
    _SOFT.append((2109, repr(_e)[:200]))
    print('FAIL line', 2109, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[payload_syms['stock_input_trampoline']] = 'stock_input'
except BaseException as _e:
    _SOFT.append((2111, repr(_e)[:200]))
    print('FAIL line', 2111, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.animating = 0
except BaseException as _e:
    _SOFT.append((2119, repr(_e)[:200]))
    print('FAIL line', 2119, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2139, repr(_e)[:200]))
    print('FAIL line', 2139, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sw = m.node('image', 'img_bootswitch')
except BaseException as _e:
    _SOFT.append((2139, repr(_e)[:200]))
    print('FAIL line', 2139, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('window', 'bootvol_page', [m.node('view', 'view_navbar', [sw]), m.node('view', 'view_body', [m.node('image', 'img_dec'), m.node('image', 'img_add')])])
except BaseException as _e:
    _SOFT.append((2140, repr(_e)[:200]))
    print('FAIL line', 2140, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    QS_ADJUST = '_cc_adjust'
except BaseException as _e:
    _SOFT.append((2145, repr(_e)[:200]))
    print('FAIL line', 2145, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    CC_BRIGHT = 5
except BaseException as _e:
    _SOFT.append((2146, repr(_e)[:200]))
    print('FAIL line', 2146, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    RING = '_cc_seen'
except BaseException as _e:
    _SOFT.append((2147, repr(_e)[:200]))
    print('FAIL line', 2147, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2148, repr(_e)[:200]))
    print('FAIL line', 2148, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bright = m.node('slider', 'slider_backlight', value=99)
except BaseException as _e:
    _SOFT.append((2148, repr(_e)[:200]))
    print('FAIL line', 2148, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('dialog', 'statusbar_dialog', [m.node('view', 'view_backlight', [bright])], _cc_focus=CC_BRIGHT, **{RING: m.now + 1})
except BaseException as _e:
    _SOFT.append((2149, repr(_e)[:200]))
    print('FAIL line', 2149, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2159, repr(_e)[:200]))
    print('FAIL line', 2159, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bright = m.node('slider', 'slider_backlight', value=50)
except BaseException as _e:
    _SOFT.append((2159, repr(_e)[:200]))
    print('FAIL line', 2159, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('dialog', 'statusbar_dialog', [m.node('view', 'view_backlight', [bright])], _cc_landed=m.now + 1 - 50, **{RING: m.now + 1})
except BaseException as _e:
    _SOFT.append((2160, repr(_e)[:200]))
    print('FAIL line', 2160, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    instructions = [0]
except BaseException as _e:
    _SOFT.append((2161, repr(_e)[:200]))
    print('FAIL line', 2161, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def count_payload(*args):
    instructions[0] += 1
try:
    hook = m.u.hook_add(UC_HOOK_CODE, count_payload, begin=BASE, end=SCRATCH - 1)
except BaseException as _e:
    _SOFT.append((2163, repr(_e)[:200]))
    print('FAIL line', 2163, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def slider_touch(steps, state=3, dialog='statusbar_dialog'):
    m = Machine()
    bright = m.node('slider', 'slider_backlight', value=50)
    view = m.node('view', 'view_backlight', [bright])
    m.top = m.node('dialog', dialog, [view])
    m.word(bright + O['W_PARENT'], view)
    m.word(view + O['W_PARENT'], m.top)
    m.word(syms['g_dropdown_menu_state'], 3)
    got = []
    for kind, x, y in steps:
        if kind == 'up':
            m.word(syms['g_dropdown_menu_state'], state)
        m.word(m.event + O['EVENT_Y'], y)
        typ = {'down': O['EVT_POINTER_DOWN'] - 1, 'move': O['EVT_POINTER_MOVE_BEFORE'] - 1, 'up': O['EVT_POINTER_UP_BEFORE'] - 1}[kind]
        ret = m.call(x, address=HOOKS['widget_dispatch'][0], args=(bright, m.event, 0, 0), event_type=typ, gap=0)
        got.append((ret, [m.get(c[2]) for c in m.dispatched() if c[1] == bright]))
    return got
try:
    D, M, U, A = (O['EVT_POINTER_DOWN'] - 1, O['EVT_POINTER_MOVE_BEFORE'] - 1, O['EVT_POINTER_UP_BEFORE'] - 1, O['EVT_POINTER_ABORT'])
except BaseException as _e:
    _SOFT.append((2210, repr(_e)[:200]))
    print('FAIL line', 2210, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from ipod import decode, walk as ui_walk
try:
    node = next((n for n in ui_walk(decode((B / 'ui/playing_page.bin').read_bytes())) if n[2].get('name') == 'slider_play'))
except BaseException as _e:
    _SOFT.append((2221, repr(_e)[:200]))
    print('FAIL line', 2221, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def time_page(name):
    m = Machine()
    names = ['tselector_year', 'tselector_month', 'tselector_day', 'tselector_hour', 'tselector_min']
    if name == 'sleepshutdown_page':
        names = names[3:]
    sels = [m.node('text_selector', n, options=12) for n in names]
    for w in sels:
        m.word(w + O['SELECTOR_INDEX'], 5)
        m.word(w + O['W_W'], 80)
        m.word(w + O['W_H'], 170)
    ok = m.node('button', 'btn_enter')
    slide = m.node('slide_view', 'slide_view', sels, value=0)
    m.top = m.node('window', name, [slide, ok])
    return (m, sels, ok, slide)
try:
    m, sels, ok, slide = time_page('manualtime_page')
except BaseException as _e:
    _SOFT.append((2254, repr(_e)[:200]))
    print('FAIL line', 2254, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.rounded_fail = True
except BaseException as _e:
    _SOFT.append((2258, repr(_e)[:200]))
    print('FAIL line', 2258, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.rounded_fail = None
except BaseException as _e:
    _SOFT.append((2259, repr(_e)[:200]))
    print('FAIL line', 2259, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[ok]['text'] = 'OK'
except BaseException as _e:
    _SOFT.append((2263, repr(_e)[:200]))
    print('FAIL line', 2263, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    backs = lambda: [c[0] for c in m.calls].count('navigator_back')
except BaseException as _e:
    _SOFT.append((2266, repr(_e)[:200]))
    print('FAIL line', 2266, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[slide]['value'] = 0
except BaseException as _e:
    _SOFT.append((2271, repr(_e)[:200]))
    print('FAIL line', 2271, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, sels, ok, slide = time_page('manualtime_page')
except BaseException as _e:
    _SOFT.append((2273, repr(_e)[:200]))
    print('FAIL line', 2273, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    field = lambda: m.nodes[m.top].get('_edit_field', 0)
except BaseException as _e:
    _SOFT.append((2273, repr(_e)[:200]))
    print('FAIL line', 2273, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, sels, ok, slide = time_page('sleepshutdown_page')
except BaseException as _e:
    _SOFT.append((2281, repr(_e)[:200]))
    print('FAIL line', 2281, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2288, repr(_e)[:200]))
    print('FAIL line', 2288, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(4, name='playmode_page')
except BaseException as _e:
    _SOFT.append((2288, repr(_e)[:200]))
    print('FAIL line', 2288, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[es[2]]['children'] = [m.node('image', image='select')]
except BaseException as _e:
    _SOFT.append((2289, repr(_e)[:200]))
    print('FAIL line', 2289, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OLED_MUTED = color_t(OLED_SCHEME['muted'])
except BaseException as _e:
    _SOFT.append((2294, repr(_e)[:200]))
    print('FAIL line', 2294, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2309, repr(_e)[:200]))
    print('FAIL line', 2309, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    top = m.node('window', 'playlistsmore_page')
except BaseException as _e:
    _SOFT.append((2309, repr(_e)[:200]))
    print('FAIL line', 2309, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = top
except BaseException as _e:
    _SOFT.append((2309, repr(_e)[:200]))
    print('FAIL line', 2309, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rows = [m.node('list_item', children=[m.entry(0)]) for _ in range(2)]
except BaseException as _e:
    _SOFT.append((2311, repr(_e)[:200]))
    print('FAIL line', 2311, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[top]['children'] = rows
except BaseException as _e:
    _SOFT.append((2313, repr(_e)[:200]))
    print('FAIL line', 2313, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, w, es = walk(10, 5)
except BaseException as _e:
    _SOFT.append((2318, repr(_e)[:200]))
    print('FAIL line', 2318, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w2, es2 = m.page_list(10, extent=1000)
except BaseException as _e:
    _SOFT.append((2318, repr(_e)[:200]))
    print('FAIL line', 2318, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.glide = False
except BaseException as _e:
    _SOFT.append((2318, repr(_e)[:200]))
    print('FAIL line', 2318, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w3, es3 = m.page_list(10, extent=1000)
except BaseException as _e:
    _SOFT.append((2321, repr(_e)[:200]))
    print('FAIL line', 2321, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, w, es = walk(10, 5)
except BaseException as _e:
    _SOFT.append((2324, repr(_e)[:200]))
    print('FAIL line', 2324, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w2, es2 = m.page_list(10, extent=1000)
except BaseException as _e:
    _SOFT.append((2324, repr(_e)[:200]))
    print('FAIL line', 2324, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.glide = False
except BaseException as _e:
    _SOFT.append((2324, repr(_e)[:200]))
    print('FAIL line', 2324, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, w, es = walk(10, 5)
except BaseException as _e:
    _SOFT.append((2329, repr(_e)[:200]))
    print('FAIL line', 2329, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[w]['children'] = es[:6]
except BaseException as _e:
    _SOFT.append((2330, repr(_e)[:200]))
    print('FAIL line', 2330, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2334, repr(_e)[:200]))
    print('FAIL line', 2334, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, _, entries = m.table_page()
except BaseException as _e:
    _SOFT.append((2334, repr(_e)[:200]))
    print('FAIL line', 2334, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.rebind = None
except BaseException as _e:
    _SOFT.append((2338, repr(_e)[:200]))
    print('FAIL line', 2338, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w2, _, entries2 = m.table_page()
except BaseException as _e:
    _SOFT.append((2339, repr(_e)[:200]))
    print('FAIL line', 2339, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2358, repr(_e)[:200]))
    print('FAIL line', 2358, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, rs, es = m.table_page(n=20)
except BaseException as _e:
    _SOFT.append((2358, repr(_e)[:200]))
    print('FAIL line', 2358, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w2, rs2, es2 = m.table_page(rebind=True)
except BaseException as _e:
    _SOFT.append((2360, repr(_e)[:200]))
    print('FAIL line', 2360, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.glide = False
except BaseException as _e:
    _SOFT.append((2360, repr(_e)[:200]))
    print('FAIL line', 2360, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2368, repr(_e)[:200]))
    print('FAIL line', 2368, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, rs, es = m.table_page(n=20)
except BaseException as _e:
    _SOFT.append((2368, repr(_e)[:200]))
    print('FAIL line', 2368, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w2, rs2, es2 = m.table_page(rebind=True)
except BaseException as _e:
    _SOFT.append((2370, repr(_e)[:200]))
    print('FAIL line', 2370, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2375, repr(_e)[:200]))
    print('FAIL line', 2375, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page('home_page', 'slide_menu')
except BaseException as _e:
    _SOFT.append((2375, repr(_e)[:200]))
    print('FAIL line', 2375, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    child = [m.entry(w), m.entry(w)]
except BaseException as _e:
    _SOFT.append((2376, repr(_e)[:200]))
    print('FAIL line', 2376, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[w]['children'] = child
except BaseException as _e:
    _SOFT.append((2376, repr(_e)[:200]))
    print('FAIL line', 2376, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2386, repr(_e)[:200]))
    print('FAIL line', 2386, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3)
except BaseException as _e:
    _SOFT.append((2386, repr(_e)[:200]))
    print('FAIL line', 2386, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2392, repr(_e)[:200]))
    print('FAIL line', 2392, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3)
except BaseException as _e:
    _SOFT.append((2392, repr(_e)[:200]))
    print('FAIL line', 2392, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2407, repr(_e)[:200]))
    print('FAIL line', 2407, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3)
except BaseException as _e:
    _SOFT.append((2407, repr(_e)[:200]))
    print('FAIL line', 2407, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.animating = 1
except BaseException as _e:
    _SOFT.append((2407, repr(_e)[:200]))
    print('FAIL line', 2407, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2409, repr(_e)[:200]))
    print('FAIL line', 2409, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3)
except BaseException as _e:
    _SOFT.append((2409, repr(_e)[:200]))
    print('FAIL line', 2409, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.animating = 1
except BaseException as _e:
    _SOFT.append((2409, repr(_e)[:200]))
    print('FAIL line', 2409, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2467, repr(_e)[:200]))
    print('FAIL line', 2467, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, rs, es = m.table_page()
except BaseException as _e:
    _SOFT.append((2467, repr(_e)[:200]))
    print('FAIL line', 2467, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2470, repr(_e)[:200]))
    print('FAIL line', 2470, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, rs, es = m.table_page()
except BaseException as _e:
    _SOFT.append((2470, repr(_e)[:200]))
    print('FAIL line', 2470, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2474, repr(_e)[:200]))
    print('FAIL line', 2474, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3)
except BaseException as _e:
    _SOFT.append((2474, repr(_e)[:200]))
    print('FAIL line', 2474, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.animating = 1
except BaseException as _e:
    _SOFT.append((2474, repr(_e)[:200]))
    print('FAIL line', 2474, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.animating = 0
except BaseException as _e:
    _SOFT.append((2475, repr(_e)[:200]))
    print('FAIL line', 2475, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2479, repr(_e)[:200]))
    print('FAIL line', 2479, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3)
except BaseException as _e:
    _SOFT.append((2479, repr(_e)[:200]))
    print('FAIL line', 2479, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.timer_fail = True
except BaseException as _e:
    _SOFT.append((2479, repr(_e)[:200]))
    print('FAIL line', 2479, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2483, repr(_e)[:200]))
    print('FAIL line', 2483, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2488, repr(_e)[:200]))
    print('FAIL line', 2488, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page('home_page', 'slide_menu')
except BaseException as _e:
    _SOFT.append((2488, repr(_e)[:200]))
    print('FAIL line', 2488, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    a = m.get(w + O['SLIDE_ANIMATOR'])
except BaseException as _e:
    _SOFT.append((2490, repr(_e)[:200]))
    print('FAIL line', 2490, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def slide(m, w):
    a = m.get(w + O['SLIDE_ANIMATOR'])
    assert a and list(m.slides) == [a]
    return (a, signed(m.get(a + 104)), signed(m.get(a + O['ANIM_X_TO'])), m.get(a + O['ANIM_DURATION']))

def step_ticks(m, w, *ticks):
    for gap, ms, e in ticks:
        m.slide_easing = e
        assert m.call(gap=gap) == 11 and slide(m, w)[3] == ms
        assert m.get(m.get(w + O['SLIDE_ANIMATOR']) + O['ANIM_EASING']) == m.call(address=syms['easing_get'], args=(e, 0, 0, 0), gap=0)
try:
    OLED_STEPS = ((0, OLED_T['motion.step.ms'], OLED_T['motion.step.easing']), (50, OLED_T['motion.step_fast.ms'], OLED_T['motion.step_fast.easing']))
except BaseException as _e:
    _SOFT.append((2530, repr(_e)[:200]))
    print('FAIL line', 2530, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2532, repr(_e)[:200]))
    print('FAIL line', 2532, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page('home_page', 'slide_menu')
except BaseException as _e:
    _SOFT.append((2532, repr(_e)[:200]))
    print('FAIL line', 2532, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2533, repr(_e)[:200]))
    print('FAIL line', 2533, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page('home_page', 'slide_menu')
except BaseException as _e:
    _SOFT.append((2533, repr(_e)[:200]))
    print('FAIL line', 2533, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2536, repr(_e)[:200]))
    print('FAIL line', 2536, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page('home_page', 'slide_menu')
except BaseException as _e:
    _SOFT.append((2536, repr(_e)[:200]))
    print('FAIL line', 2536, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((2536, repr(_e)[:200]))
    print('FAIL line', 2536, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2572, repr(_e)[:200]))
    print('FAIL line', 2572, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page('home_page', 'slide_menu')
except BaseException as _e:
    _SOFT.append((2572, repr(_e)[:200]))
    print('FAIL line', 2572, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2575, repr(_e)[:200]))
    print('FAIL line', 2575, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page('home_page', 'slide_menu')
except BaseException as _e:
    _SOFT.append((2575, repr(_e)[:200]))
    print('FAIL line', 2575, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2587, repr(_e)[:200]))
    print('FAIL line', 2587, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page('home_page', 'slide_menu')
except BaseException as _e:
    _SOFT.append((2587, repr(_e)[:200]))
    print('FAIL line', 2587, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2591, repr(_e)[:200]))
    print('FAIL line', 2591, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page('home_page', 'slide_menu')
except BaseException as _e:
    _SOFT.append((2591, repr(_e)[:200]))
    print('FAIL line', 2591, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2594, repr(_e)[:200]))
    print('FAIL line', 2594, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2597, repr(_e)[:200]))
    print('FAIL line', 2597, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3)
except BaseException as _e:
    _SOFT.append((2597, repr(_e)[:200]))
    print('FAIL line', 2597, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.now += O['DOUBLE_CLICK_MS'] + 1
except BaseException as _e:
    _SOFT.append((2597, repr(_e)[:200]))
    print('FAIL line', 2597, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2601, repr(_e)[:200]))
    print('FAIL line', 2601, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3)
except BaseException as _e:
    _SOFT.append((2601, repr(_e)[:200]))
    print('FAIL line', 2601, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def navigate(a, b):
    m.on_click = None
    m.page_list(3, name='display_page')
try:
    m.on_click = navigate
except BaseException as _e:
    _SOFT.append((2605, repr(_e)[:200]))
    print('FAIL line', 2605, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    old = m.top
except BaseException as _e:
    _SOFT.append((2605, repr(_e)[:200]))
    print('FAIL line', 2605, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2615, repr(_e)[:200]))
    print('FAIL line', 2615, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2620, repr(_e)[:200]))
    print('FAIL line', 2620, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3)
except BaseException as _e:
    _SOFT.append((2620, repr(_e)[:200]))
    print('FAIL line', 2620, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    old = m.top
except BaseException as _e:
    _SOFT.append((2620, repr(_e)[:200]))
    print('FAIL line', 2620, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('window', 'playing_page')
except BaseException as _e:
    _SOFT.append((2621, repr(_e)[:200]))
    print('FAIL line', 2621, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = old
except BaseException as _e:
    _SOFT.append((2622, repr(_e)[:200]))
    print('FAIL line', 2622, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2623, repr(_e)[:200]))
    print('FAIL line', 2623, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page('home_page', 'slide_menu')
except BaseException as _e:
    _SOFT.append((2623, repr(_e)[:200]))
    print('FAIL line', 2623, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    home = m.top
except BaseException as _e:
    _SOFT.append((2623, repr(_e)[:200]))
    print('FAIL line', 2623, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('window', 'playing_page')
except BaseException as _e:
    _SOFT.append((2624, repr(_e)[:200]))
    print('FAIL line', 2624, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = home
except BaseException as _e:
    _SOFT.append((2625, repr(_e)[:200]))
    print('FAIL line', 2625, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2627, repr(_e)[:200]))
    print('FAIL line', 2627, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, rs, es = m.table_page()
except BaseException as _e:
    _SOFT.append((2627, repr(_e)[:200]))
    print('FAIL line', 2627, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2629, repr(_e)[:200]))
    print('FAIL line', 2629, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page('home_page', 'slide_menu')
except BaseException as _e:
    _SOFT.append((2629, repr(_e)[:200]))
    print('FAIL line', 2629, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[w]['children'] = [m.entry(w), m.entry(w)]
except BaseException as _e:
    _SOFT.append((2630, repr(_e)[:200]))
    print('FAIL line', 2630, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2633, repr(_e)[:200]))
    print('FAIL line', 2633, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.now = 4294967280
except BaseException as _e:
    _SOFT.append((2633, repr(_e)[:200]))
    print('FAIL line', 2633, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2667, repr(_e)[:200]))
    print('FAIL line', 2667, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(17, height=960, extent=17 * 48)
except BaseException as _e:
    _SOFT.append((2667, repr(_e)[:200]))
    print('FAIL line', 2667, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[w]['children'] = es
except BaseException as _e:
    _SOFT.append((2668, repr(_e)[:200]))
    print('FAIL line', 2668, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[w]['children'] = es[:16]
except BaseException as _e:
    _SOFT.append((2671, repr(_e)[:200]))
    print('FAIL line', 2671, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[w]['children'] = es
except BaseException as _e:
    _SOFT.append((2675, repr(_e)[:200]))
    print('FAIL line', 2675, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    IPOD_SPIN = ((0, 1), (100, 1), (100, 1), (99, 1), (1, 2), (100, 2), (99, 2), (1, 3), (100, 3), (99, 3), (1, 4), (100, 4), (99, 4), (1, 5), (100, 5), (99, 5), (1, 6), (100, 6), (99, 6), (1, 7), (100, 7), (99, 7), (1, 8), (100, 8))
except BaseException as _e:
    _SOFT.append((2686, repr(_e)[:200]))
    print('FAIL line', 2686, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2707, repr(_e)[:200]))
    print('FAIL line', 2707, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page(t='table_client')
except BaseException as _e:
    _SOFT.append((2707, repr(_e)[:200]))
    print('FAIL line', 2707, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def wiped(m, w):
    for k in [k for k in m.nodes[w] if k.startswith('_ringnav')]:
        del m.nodes[w][k]

def disturb(m, w, change, n):
    """One interruption of the n-row list w between wheel ticks; returns the live surface."""
    if change == 'window':
        w, _ = m.page_list(n, extent=n * 48, name='allmusic_page')
    elif change == 'pane':
        old = m.top
        w, _ = m.page_list(n, extent=n * 48, name='allmusic_page')
        m.nodes[old]['children'] = [w]
        m.top = old
    elif change == 'scope':
        m.word(syms['g_class_type'], 61442)
    elif change == 'context':
        m.nodes[m.top]['name'] = 'display_page'
    elif change == 'count':
        m.nodes[w]['children'].pop()
    elif change == 'recreated':
        wiped(m, w)
    elif change in ('gesture', 'animating'):
        field = 'pressed' if change == 'gesture' else 'animating'
        setattr(m, field, 1)
        assert m.call(gap=10) == 11
        setattr(m, field, 0)
    elif change == 'screen':
        m.byte(syms['g_backlight_status'], 0)
        assert m.call(gap=10) == 0
        m.byte(syms['g_backlight_status'], 1)
    elif change == 'unsupported':
        m.nodes[m.top]['name'] = 'playing_page'
        assert m.call(gap=10) == 0
        m.nodes[m.top]['name'] = 'allmusic_page'
    elif change == 'rejected':
        m.byte(10796024, 8)
        assert m.call(gap=10, debounce=True) == 11
    elif change == 'touch':
        m.call(address=HOOKS['on_wm_tsdown_before_fun'][0], event_type=O['EVT_POINTER_DOWN'], gap=10)
    elif change == 'click':
        m.click(m.node('button'), gap=10)
    elif change == 'missing':
        m.call(args=(0, 0, 0, 0), gap=10)
    elif change == 'centre':
        m.call(O['KEY_CENTER'], gap=10)
    elif change == 'return':
        assert m.call(O['KEY_RETURN'], gap=10) == 0
    elif change == 'play':
        assert m.call(O['KEY_PLAY'], gap=10) == 0
    elif change == 'long':
        long_return(m)
    elif change == 'reverse':
        assert m.call(O['KEY_PREV'], gap=10) == 11 and m.selected(w) == 1
    return w

def hold_done(m):
    return all((t[1] == payload_syms['sb_due'] for t in m.timers.values()))
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2754, repr(_e)[:200]))
    print('FAIL line', 2754, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(20, extent=960, name='allmusic_page')
except BaseException as _e:
    _SOFT.append((2754, repr(_e)[:200]))
    print('FAIL line', 2754, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.animating = 1
except BaseException as _e:
    _SOFT.append((2756, repr(_e)[:200]))
    print('FAIL line', 2756, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.animating = 0
except BaseException as _e:
    _SOFT.append((2758, repr(_e)[:200]))
    print('FAIL line', 2758, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2768, repr(_e)[:200]))
    print('FAIL line', 2768, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(40, extent=40 * 48)
except BaseException as _e:
    _SOFT.append((2768, repr(_e)[:200]))
    print('FAIL line', 2768, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    fast = 13
except BaseException as _e:
    _SOFT.append((2770, repr(_e)[:200]))
    print('FAIL line', 2770, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2776, repr(_e)[:200]))
    print('FAIL line', 2776, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page()
except BaseException as _e:
    _SOFT.append((2776, repr(_e)[:200]))
    print('FAIL line', 2776, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    row1 = m.entry(w, 0)
except BaseException as _e:
    _SOFT.append((2777, repr(_e)[:200]))
    print('FAIL line', 2777, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    row2 = m.entry(w, 96)
except BaseException as _e:
    _SOFT.append((2777, repr(_e)[:200]))
    print('FAIL line', 2777, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    deep = m.entry(row1, 0)
except BaseException as _e:
    _SOFT.append((2777, repr(_e)[:200]))
    print('FAIL line', 2777, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[row1]['children'] = [deep]
except BaseException as _e:
    _SOFT.append((2778, repr(_e)[:200]))
    print('FAIL line', 2778, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[w]['children'] = [row1, row2]
except BaseException as _e:
    _SOFT.append((2778, repr(_e)[:200]))
    print('FAIL line', 2778, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    want = [(x + 7, y + 20) for x, y, *_ in bar_bands(inset, 0, 240 - 2 * inset, 20, radius) if 10 <= x + 7 <= 229 and y + 20 >= 30]
except BaseException as _e:
    _SOFT.append((2786, repr(_e)[:200]))
    print('FAIL line', 2786, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2788, repr(_e)[:200]))
    print('FAIL line', 2788, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page()
except BaseException as _e:
    _SOFT.append((2788, repr(_e)[:200]))
    print('FAIL line', 2788, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    e = m.entry(w, 0)
except BaseException as _e:
    _SOFT.append((2789, repr(_e)[:200]))
    print('FAIL line', 2789, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[w]['children'] = [e]
except BaseException as _e:
    _SOFT.append((2789, repr(_e)[:200]))
    print('FAIL line', 2789, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sink = 'lcd_fill_rect'
except BaseException as _e:
    _SOFT.append((2790, repr(_e)[:200]))
    print('FAIL line', 2790, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2804, repr(_e)[:200]))
    print('FAIL line', 2804, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(6)
except BaseException as _e:
    _SOFT.append((2804, repr(_e)[:200]))
    print('FAIL line', 2804, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.glide = False
except BaseException as _e:
    _SOFT.append((2805, repr(_e)[:200]))
    print('FAIL line', 2805, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2814, repr(_e)[:200]))
    print('FAIL line', 2814, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page()
except BaseException as _e:
    _SOFT.append((2814, repr(_e)[:200]))
    print('FAIL line', 2814, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2817, repr(_e)[:200]))
    print('FAIL line', 2817, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page()
except BaseException as _e:
    _SOFT.append((2817, repr(_e)[:200]))
    print('FAIL line', 2817, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    e = m.entry(w)
except BaseException as _e:
    _SOFT.append((2818, repr(_e)[:200]))
    print('FAIL line', 2818, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[w]['children'] = [e]
except BaseException as _e:
    _SOFT.append((2818, repr(_e)[:200]))
    print('FAIL line', 2818, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2843, repr(_e)[:200]))
    print('FAIL line', 2843, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3, height=96, extent=400)
except BaseException as _e:
    _SOFT.append((2843, repr(_e)[:200]))
    print('FAIL line', 2843, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3, height=96, extent=400)
except BaseException as _e:
    _SOFT.append((2848, repr(_e)[:200]))
    print('FAIL line', 2848, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2862, repr(_e)[:200]))
    print('FAIL line', 2862, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page()
except BaseException as _e:
    _SOFT.append((2862, repr(_e)[:200]))
    print('FAIL line', 2862, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    e = m.entry(w, 0)
except BaseException as _e:
    _SOFT.append((2863, repr(_e)[:200]))
    print('FAIL line', 2863, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[w]['children'] = [e]
except BaseException as _e:
    _SOFT.append((2863, repr(_e)[:200]))
    print('FAIL line', 2863, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2866, repr(_e)[:200]))
    print('FAIL line', 2866, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.page()
except BaseException as _e:
    _SOFT.append((2866, repr(_e)[:200]))
    print('FAIL line', 2866, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2873, repr(_e)[:200]))
    print('FAIL line', 2873, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3, extent=1000)
except BaseException as _e:
    _SOFT.append((2873, repr(_e)[:200]))
    print('FAIL line', 2873, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2897, repr(_e)[:200]))
    print('FAIL line', 2897, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    a, b, _, _ = m.panes(96)
except BaseException as _e:
    _SOFT.append((2897, repr(_e)[:200]))
    print('FAIL line', 2897, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[a]['_ringnav_index'] = 0
except BaseException as _e:
    _SOFT.append((2897, repr(_e)[:200]))
    print('FAIL line', 2897, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2903, repr(_e)[:200]))
    print('FAIL line', 2903, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2913, repr(_e)[:200]))
    print('FAIL line', 2913, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    names = [c[0] for c in m.vg_calls]
except BaseException as _e:
    _SOFT.append((2915, repr(_e)[:200]))
    print('FAIL line', 2915, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2921, repr(_e)[:200]))
    print('FAIL line', 2921, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((2931, repr(_e)[:200]))
    print('FAIL line', 2931, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ops = [c for c in m.calls if c[0].startswith('sink:')]
except BaseException as _e:
    _SOFT.append((2934, repr(_e)[:200]))
    print('FAIL line', 2934, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((3012, repr(_e)[:200]))
    print('FAIL line', 3012, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3)
except BaseException as _e:
    _SOFT.append((3012, repr(_e)[:200]))
    print('FAIL line', 3012, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((3041, repr(_e)[:200]))
    print('FAIL line', 3041, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(10)
except BaseException as _e:
    _SOFT.append((3041, repr(_e)[:200]))
    print('FAIL line', 3041, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(10)
except BaseException as _e:
    _SOFT.append((3043, repr(_e)[:200]))
    print('FAIL line', 3043, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.glide = False
except BaseException as _e:
    _SOFT.append((3043, repr(_e)[:200]))
    print('FAIL line', 3043, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((3105, repr(_e)[:200]))
    print('FAIL line', 3105, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3)
except BaseException as _e:
    _SOFT.append((3105, repr(_e)[:200]))
    print('FAIL line', 3105, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((3111, repr(_e)[:200]))
    print('FAIL line', 3111, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, rs, es = m.table_page()
except BaseException as _e:
    _SOFT.append((3111, repr(_e)[:200]))
    print('FAIL line', 3111, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.glide = False
except BaseException as _e:
    _SOFT.append((3111, repr(_e)[:200]))
    print('FAIL line', 3111, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    old = list(es)
except BaseException as _e:
    _SOFT.append((3112, repr(_e)[:200]))
    print('FAIL line', 3112, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def replace_pool(a, offset):
    global rs, es
    rs = [m.node('table_row') for _ in range(4)]
    es = [m.entry(r) for r in rs]
    m.nodes[a]['children'] = rs
    for r, e in zip(rs, es):
        m.nodes[r]['children'] = [e]
        m.word(r + O['W_PARENT'], a)
    m.bind(rs, offset)
    for e in old:
        m.nodes.pop(e, None)
try:
    m.rebind = replace_pool
except BaseException as _e:
    _SOFT.append((3122, repr(_e)[:200]))
    print('FAIL line', 3122, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    PAST_END = (100, 0, 300, 301, 1000, 2000)
except BaseException as _e:
    _SOFT.append((3132, repr(_e)[:200]))
    print('FAIL line', 3132, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((3147, repr(_e)[:200]))
    print('FAIL line', 3147, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, rs, es = m.table_page(n=4, rebind=True)
except BaseException as _e:
    _SOFT.append((3147, repr(_e)[:200]))
    print('FAIL line', 3147, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((3164, repr(_e)[:200]))
    print('FAIL line', 3164, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(1, height=96, extent=96, name='playlist_page')
except BaseException as _e:
    _SOFT.append((3164, repr(_e)[:200]))
    print('FAIL line', 3164, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class NavigationMachine(Machine):

    def __init__(self):
        super().__init__()
        self.order = []
        self.block_open = False
        self.word(self.wm + 148, 10175152)
        for name in ('navigator_switch_to_with_context', 'navigator_back_to_home', 'navigator_to_with_context', 'navigator_to', 'window_close'):
            self.handlers.pop(syms[name], None)
        self.mock('widget_child', 'window_open_and_close', 'widget_restack', 'widget_get_window', 'widget_is_keyboard', 'widget_is_dialog', 'widget_is_window', 'widget_is_normal_window', 'widget_remove_child', 'widget_destroy', 'window_manager_dispatch_window_event', 'widget_foreach', 'widget_on', 'playing_timer_start', 'access@GLIBC_2.0', 'remove@GLIBC_2.0', 'idle_add', 'netdisk_folder_clear', 'awake_screen', prefix='nav:')
        self.rockbox_mode = 33261
        self.groovebox_mode = 0
        self.handlers[syms['__xstat@GLIBC_2.0']] = 'rockbox_stat'
        self.handlers[syms['strcmp@GLIBC_2.0']] = 'tk_strcmp'
        for address in (5492488, 6916556, 6929264, 6920068):
            self.handlers[address] = 'nav:' + hex(address)

    def sync_order(self):
        self.top = self.order[-1] if self.order else 0
        arr = self.alloc(12)
        items = self.alloc(max(4, len(self.order) * 4))
        self.word(self.wm + 92, arr)
        self.word(arr, len(self.order))
        self.word(arr + 8, items)
        for i, w in enumerate(self.order):
            self.word(items + 4 * i, w)
            self.word(w + O['W_PARENT'], self.wm)

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('nav:'):
            return super().hook(u, address, size, unused)
        assert u.reg_read(UC_MIPS_REG_T9) == address
        name = name[4:]
        a, b, c, d = [u.reg_read(r) for r in REGS]
        ret = 0
        self.calls.append((name, a, b, c))
        if name == 'widget_child':
            ret = next((w for w in self.order if self.nodes[w]['name'] == self.text(b)), 0)
        elif name == 'window_open_and_close':
            if not self.block_open:
                ret = self.node('window', self.text(a), stage=3)
                self.order.append(ret)
                self.sync_order()
        elif name == 'widget_restack':
            self.order.remove(a)
            self.order.insert(min(b, len(self.order)), a)
            self.sync_order()
        elif name == 'widget_get_window':
            ret = a
        elif name in ('widget_is_window', 'widget_is_normal_window'):
            ret = int(a in self.order)
        elif name == 'widget_remove_child':
            self.order.remove(b)
            self.sync_order()
        elif name == '0x69bb70':
            ret = 1
        elif name == '0x699784':
            ret = self.top
        elif name == 'access@GLIBC_2.0':
            ret = -1
        self.returns(ret)
try:
    m = NavigationMachine()
except BaseException as _e:
    _SOFT.append((3291, repr(_e)[:200]))
    print('FAIL line', 3291, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    browse = m.top
except BaseException as _e:
    _SOFT.append((3291, repr(_e)[:200]))
    print('FAIL line', 3291, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.order = [browse]
except BaseException as _e:
    _SOFT.append((3291, repr(_e)[:200]))
    print('FAIL line', 3291, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.block_open = True
except BaseException as _e:
    _SOFT.append((3292, repr(_e)[:200]))
    print('FAIL line', 3292, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.block_open = False
except BaseException as _e:
    _SOFT.append((3294, repr(_e)[:200]))
    print('FAIL line', 3294, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.animating = 1
except BaseException as _e:
    _SOFT.append((3294, repr(_e)[:200]))
    print('FAIL line', 3294, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class PullMachine(Machine):

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        a, b, c, d = [u.reg_read(r) for r in REGS]
        if name == 'widget_on' and b == getattr(self, 'pull_bind_fail', None):
            self.returns(0)
            return
        if name == 'widget_off_by_func':
            self.nodes[a]['handlers'] = [h for h in self.nodes[a].get('handlers', []) if h != (b, c, d)]
        return super().hook(u, address, size, unused)

    def child(self, page, name):

        def walk(w):
            if self.nodes[w]['name'] == name:
                return w
            for child in self.nodes[w]['children']:
                found = walk(child)
                if found:
                    return found
            return 0
        return walk(page)

    def emit(self, w, typ, x=100, y=90):
        self.word(self.event + O['EVENT_TARGET'], w)
        self.word(self.event + O['EVENT_Y'], y)
        ret = 0
        for t, callback, ctx in list(self.nodes[w].get('handlers', [])):
            if t == typ:
                ret = self.call(key=x, address=callback, args=(ctx, self.event, 0, 0), event_type=typ, gap=0, clear=False)
                if ret == 11:
                    break
        return ret

    def start_pull(self, page, w, x=100, y=60):
        self.top = page
        self.word(w + O['W_PARENT'], page)
        self.word(page + O['W_Y'], 30)
        self.word(self.event + O['EVENT_Y'], y)
        return self.call(key=x, address=HOOKS['on_wm_tsdown_before_fun'][0], event_type=O['EVT_POINTER_DOWN'], gap=0)

    def prompt(self, page):
        w = self.child(page, '_pull_prompt')
        return self.nodes[w] if w else {'visible': 0}

    def searches(self):
        return [c for c in self.calls if c[0] == 'window_create']
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((3335, repr(_e)[:200]))
    print('FAIL line', 3335, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    p = m.alloc(48)
except BaseException as _e:
    _SOFT.append((3337, repr(_e)[:200]))
    print('FAIL line', 3337, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def wm_event_type(address):
    return struct.unpack('<I', m.u.mem_read(address, 4))[0] & 65535
try:
    m = PullMachine()
except BaseException as _e:
    _SOFT.append((3403, repr(_e)[:200]))
    print('FAIL line', 3403, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sb_n = [0]
except BaseException as _e:
    _SOFT.append((3403, repr(_e)[:200]))
    print('FAIL line', 3403, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    h = m.u.hook_add(UC_HOOK_BLOCK, lambda u, a, size, x: sb_n.__setitem__(0, sb_n[0] + size // 4), begin=BASE, end=SCRATCH - 1)
except BaseException as _e:
    _SOFT.append((3403, repr(_e)[:200]))
    print('FAIL line', 3403, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    under = m.node('window', 'home_page')
except BaseException as _e:
    _SOFT.append((3404, repr(_e)[:200]))
    print('FAIL line', 3404, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(5, name='albums_page')
except BaseException as _e:
    _SOFT.append((3404, repr(_e)[:200]))
    print('FAIL line', 3404, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    page = m.top
except BaseException as _e:
    _SOFT.append((3404, repr(_e)[:200]))
    print('FAIL line', 3404, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.wm] = dict(type='window_manager', name='', children=[under, page], visible=1, enable=1)
except BaseException as _e:
    _SOFT.append((3405, repr(_e)[:200]))
    print('FAIL line', 3405, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[payload_syms['stock_input_trampoline']] = 'stock_input'
except BaseException as _e:
    _SOFT.append((3406, repr(_e)[:200]))
    print('FAIL line', 3406, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def sb_inputs():
    return [c for c in m.calls if c[0] == 'stock_input']
try:
    sb_moves = []
except BaseException as _e:
    _SOFT.append((3409, repr(_e)[:200]))
    print('FAIL line', 3409, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.now += 16
except BaseException as _e:
    _SOFT.append((3413, repr(_e)[:200]))
    print('FAIL line', 3413, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sb_steps = []
except BaseException as _e:
    _SOFT.append((3414, repr(_e)[:200]))
    print('FAIL line', 3414, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sb_move, sb_step = (max(sb_moves[1:]), max(sb_steps[:-1]))
except BaseException as _e:
    _SOFT.append((3419, repr(_e)[:200]))
    print('FAIL line', 3419, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PullMachine()
except BaseException as _e:
    _SOFT.append((3423, repr(_e)[:200]))
    print('FAIL line', 3423, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, rows, es = m.table_page(name='localmusic_page')
except BaseException as _e:
    _SOFT.append((3423, repr(_e)[:200]))
    print('FAIL line', 3423, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    page = m.top
except BaseException as _e:
    _SOFT.append((3423, repr(_e)[:200]))
    print('FAIL line', 3423, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    count = m.get(w + O['TABLE_ROWS'])
except BaseException as _e:
    _SOFT.append((3424, repr(_e)[:200]))
    print('FAIL line', 3424, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class PullDispatchMachine(PullMachine):

    def __init__(self):
        super().__init__()
        self.mock('widget_ref', 'widget_unref', 'widget_on_pointer_move_children', 'widget_on_pointer_up_children', 'widget_on_event_before_children', 'widget_vtable_on_pointer_move', 'widget_vtable_on_pointer_up', 'emitter_dispatch', prefix='input:')

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('input:'):
            return super().hook(u, address, size, unused)
        assert u.reg_read(UC_MIPS_REG_T9) == address
        a, b = [u.reg_read(r) for r in REGS[:2]]
        self.calls.append((name, a, b, 0))
        if name == 'input:emitter_dispatch':
            matches = [(cb, ctx) for typ, cb, ctx in self.nodes[a].get('handlers', []) if typ == self.get(b)]
            assert len(matches) <= 1
            if matches:
                cb, ctx = matches[0]
                u.reg_write(UC_MIPS_REG_A0, ctx)
                u.reg_write(UC_MIPS_REG_T9, cb)
                u.reg_write(UC_MIPS_REG_PC, cb)
                return
        self.returns(0)
try:
    m = PullMachine()
except BaseException as _e:
    _SOFT.append((3490, repr(_e)[:200]))
    print('FAIL line', 3490, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3, height=240, name='localmusic_page')
except BaseException as _e:
    _SOFT.append((3490, repr(_e)[:200]))
    print('FAIL line', 3490, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    page = m.top
except BaseException as _e:
    _SOFT.append((3490, repr(_e)[:200]))
    print('FAIL line', 3490, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((3493, repr(_e)[:200]))
    print('FAIL line', 3493, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from build import SHUFFLE_CALL, fileoff

class QueueMachine(Machine):

    def __init__(self, page='allmusic_page', rows=20, queue=3, pos=0, mode=0, cls=61441):
        super().__init__()
        self.deqs = {}
        self.toasts = []
        self.sent = []
        self.picks = []
        self.airplay = 0
        self.imports = []
        self.import_ret = 1
        self.m3u_files = []
        self.uncreated = set()
        self.on_toast = None
        self.on_open = None
        for n in ('_create_deque', 'deque_init', 'deque_init_copy', 'deque_size', 'deque_at', '_deque_push_back', 'deque_assign', 'deque_clear', 'deque_destroy', 'deque_pop_back', 'send@GLIBC_2.0', 'window_manager_get_input_device_status', 'navigator_to', 'navigator_to_with_context', 'window_close', 'widget_on', 'widget_destroy_children', 'getMusicByAlbum', 'getMusicByAlbumAndSonger', 'getMusicByAlbumAndAlbumSonger', 'toolsLoadDirectory', 'getMusicBySonger', 'getMusicByAlbumArtist', 'getMusicByComposer', 'getMusicByGenre', 'getMusicByAlbumAndComposer', 'getMusicByAlbumAndGenre', 'getMusicByPlayList', 'checkFavExist', 'navigator_window_is_exist', 'mcl_shuffle_pick', 'airplayGetFlag', 'calloc@GLIBC_2.0', 'qsort@GLIBC_2.0', 'importPlayList', 'toolsGetDirectoryM3u8'):
            self.handlers[syms[n]] = 'q:' + n
        self.favs = set()
        self.open_pages = set()
        self.opened = []
        self.handlers.pop(syms['mclLoadPlayList'])
        self.mock('strlen@GLIBC_2.0', 'snprintf@GLIBC_2.0')
        self.handlers[syms['strcmp@GLIBC_2.0']] = 'tk_strcmp'
        self.mock('mclStartPlayer', 'mclStop', 'mclSetPause', 'mclSetResume', 'mclSetSeek')
        self.status = self.alloc(512)
        self.word(syms['g_class_type'], cls)
        self.surface, _, _ = self.table_page(4, page, rebind=True)
        self.word(self.surface + O['TABLE_ROWS'], rows)
        self.word(syms['p_deque_showlist'], self.deque([self.song(f'Row {i}') for i in range(rows)]))
        self.word(syms['mcl_pdeqplaylist'], self.deque([self.song(c) for c in 'ABC'[:queue]]))
        self.word(syms['tools_pdeq_directory'], self.deque([self.song('staged')]))
        self.word(O['MCL_POOL'], self.deque(list(range(queue)), 'int'))
        for k, v in (('MCL_POS', pos), ('MCL_MODE', mode), ('MCL_TYPE', cls), ('MCL_LASTPOS', -1), ('MCL_PRELOAD', 0), ('MCL_FD', -1)):
            self.word(O[k], v)
        self.found = [self.song('T1'), self.song('dir', 4), self.song('T2')]
        self.listings = {}
        self.stack = [self.top]
        self.folder('/mnt/sd/Music')
        self.paint(self.surface)

    def song(self, name, kind=8):
        r = self.alloc(96)
        for off, v in (('REC_ID', 1), ('REC_NAME', self.string(name)), ('REC_PATH', self.string('/p/' + name)), ('REC_ALBUM', self.string('Album')), ('REC_ARTIST', self.string('Artist')), ('REC_TYPE', kind)):
            self.word(r + O[off], v)
        return r

    def deque(self, items, kind='stSongInfo'):
        h = self.alloc(32)
        self.deqs[h] = [kind, [self.copy(kind, e) for e in items]]
        return h

    def copy(self, kind, e):
        a = self.alloc(96)
        self.u.mem_write(a, struct.pack('<I', e & 4294967295) if kind == 'int' else bytes(self.u.mem_read(e, 96)))
        return a

    def items(self, h):
        return self.deqs[h][1]

    def row(self, i):
        return self.items(self.get(syms['p_deque_showlist']))[i]

    def names(self, h=None):
        return [self.text(self.get(e + O['REC_NAME'])) for e in self.items(h or self.get(syms['mcl_pdeqplaylist']))]

    def pool(self):
        return [signed(self.get(e)) for e in self.items(self.get(O['MCL_POOL']))]

    def mcl(self, k):
        return signed(self.get(O[k]))

    def dialog(self):
        self.view = self.node('scroll_view', 'scroll_view', [self.node('list_item') for _ in range(5)])
        self.title = self.node('hscroll_label', 'scrlabel_title')
        self.back = self.node('image', 'img_return')
        return self.node('dialog', 'sortselect_dialog', [self.node('view', 'view_navbar', [self.title, self.back]), self.node('list_view', 'list_view', [self.view])])

    def bootvol(self):
        self.slider = self.node('slider', 'slider_bootvol')
        self.vol = self.node('label', 'label_vol')
        self.dec, self.add = (self.node('image', 'img_dec'), self.node('image', 'img_add'))
        self.title, self.switch = (self.node('label', 'label_title'), self.node('image', 'img_bootswitch'))
        view = self.node('view', 'view_bootvol', [self.slider, self.dec, self.add, self.vol], visible=0)
        self.nodes[view]['enable'] = 0
        return self.node('window', 'bootvol_page', [self.node('view', 'view_navbar', [self.title, self.switch]), view])

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('q:'):
            return super().hook(u, address, size, unused)
        assert u.reg_read(UC_MIPS_REG_T9) == address, name
        name = name[2:]
        a, b, c, d = [u.reg_read(r) for r in REGS]
        ret = 0
        self.calls.append((name, a, b, c))
        if name == '_create_deque':
            ret = self.alloc(32)
            self.deqs[ret] = [self.text(a), []]
        elif name in ('deque_init', 'deque_clear'):
            self.deqs[a][1] = []
        elif name in ('deque_init_copy', 'deque_assign'):
            self.deqs[a][1] = [self.copy(self.deqs[a][0], e) for e in self.items(b)]
        elif name == 'deque_size':
            ret = len(self.items(a))
        elif name == 'deque_at':
            ret = self.items(a)[b]
        elif name == '_deque_push_back':
            self.items(a).append(self.copy(self.deqs[a][0], b))
        elif name == 'deque_destroy':
            del self.deqs[a]
        elif name == 'deque_pop_back':
            self.items(a).pop()
        elif name == 'send@GLIBC_2.0':
            self.sent.append((a, bytes(u.mem_read(b, c)), c, d))
            ret = c
        elif name == 'window_manager_get_input_device_status':
            ret = self.status
        elif name == 'airplayGetFlag':
            ret = self.airplay
        elif name == 'navigator_to':
            page = self.text(a)
            self.top = self.dialog() if page == 'dialog/sortselect_dialog' else self.bootvol() if page == 'playset/bootvol_page' else self.node('window', page)
            self.stack.append(self.top)
            self.opened.append((page, None))
        elif name == 'navigator_to_with_context':
            page = self.text(a)
            if page == 'dialog/msginfo_dialog':
                self.toasts.append((page, self.get(b), self.get(b + 4), self.text(b + 8)))
            elif page == 'playing_page':
                self.opened.append((page, self.names(self.get(b)), self.get(b + 4), self.get(b + 8), self.get(b + 12)))
            elif page == 'localmusic/playlist_page':
                self.opened.append((page, b))
            elif page == 'localmusic/playlistsmore_page':
                self.opened.append((page, b))
            else:
                self.opened.append((page, self.get(b), self.get(b + 4)))
                self.top = self.node('window', page)
                self.stack.append(self.top)
        elif name == 'importPlayList':
            only = self.get(payload_syms['libup'])
            names = []
            while only and self.text(only):
                names += [self.text(only)[1:]] * (self.text(only)[0] == 'i')
                only += len(self.raw(only)) + 1
            self.imports.append(''.join((f'/mnt/mmc/_explaylist_data/{n}\n' for n in names)) if self.get(payload_syms['libup']) else None)
            ret = self.import_ret
            names = [n.rsplit('.', 1)[0] for n in names]
            if ret != -2:
                self.word(syms['p_deque_playlist'], self.deque([self.song(n) for n in names if n not in self.uncreated]))
        elif name == 'toolsGetDirectoryM3u8':
            self.deqs[b][1] = [self.copy('stSongInfo', r) for r in self.m3u_files]
            ret = len(self.m3u_files)
        elif name == 'checkFavExist':
            ret = self.text(self.get(a + O['REC_NAME'])) in self.favs
        elif name == 'navigator_window_is_exist':
            ret = self.text(a) in self.open_pages
        elif name == 'window_close':
            self.stack.remove(a)
            self.top = self.stack[-1]
        elif name == 'widget_destroy_children':
            self.nodes[a]['children'] = []
        elif name == 'widget_on':
            self.nodes[a].setdefault('handlers', []).append((b, c, d))
            ret = 1
            if b == O['EVT_CLICK']:
                em = self.alloc(4)
                it = self.alloc(40)
                self.word(a + O['W_EMITTER'], em)
                self.word(em, it)
                self.word(it + O['EMIT_TYPE'], b)
        elif name == 'mcl_shuffle_pick':
            self.picks.append(a)
            last = self.mcl('MCL_LASTPOS')
            self.word(O['MCL_LASTPOS'], -1 if not a else self.mcl('MCL_POS'))
            self.word(O['MCL_POS'], last if not a and last != -1 else len(self.names()) - 1)
            ret = 1
        elif name == 'calloc@GLIBC_2.0':
            ret = self.alloc(a * b + 4)
        elif name == 'qsort@GLIBC_2.0':
            self.qsort(a, b, c, d)
        else:
            self.query = (name, a if name == 'getMusicByPlayList' else self.text(a) if a else None, (self.text(b) if b else None) if 'And' in name else None, c)
            found = self.listings.get(self.text(a), self.found) if name == 'toolsLoadDirectory' else self.found
            if name == 'toolsLoadDirectory':
                self.__dict__.setdefault('loaded_dirs', []).append(self.text(a))
            self.deqs[self.get(syms['tools_pdeq_directory'])][1] = [self.copy('stSongInfo', e) for e in found]
            ret = 3
            if name == 'getMusicByAlbum' and a:
                for e in self.items(self.get(syms['tools_pdeq_directory'])):
                    self.word(e + O['REC_ALBUM'], a)
        self.returns(ret, clobber=True)

    def press(self, t):
        self.word(self.status + O['INPUT_KEYS'], O['KEY_PLAY'])
        self.word(self.status + O['INPUT_KEYS'] + O['INPUT_KEY_TIME'], t)

    def hold(self):
        ret = self.call(O['KEY_PLAY'], address=syms['on_wm_keylong_fun'], event_type=O['EVT_KEY_LONG'], gap=0)
        self.advance(0, clear=False)
        return ret

    def release(self):
        """Hook, then the real stock key-up; AWTK clears the key record afterwards."""
        if self.call(O['KEY_PLAY'], gap=0) == 0:
            self.call(O['KEY_PLAY'], address=syms['on_wm_keyup_fun'], gap=0, clear=False)
        self.u.mem_write(self.status + O['INPUT_KEYS'], bytes(O['INPUT_KEY_SIZE']))
        return sum((c[0] == 'playpause_quick_click' for c in self.calls))

    def handler(self, w, kind):
        return next(((f, ctx) for t, f, ctx in self.nodes[w]['handlers'] if t == kind))

    def pick(self, row):
        f, ctx = self.handler(self.nodes[self.view]['children'][row], O['EVT_CLICK'])
        assert self.call(address=f, args=(ctx, self.event, 0, 0), gap=0) == 0

    def run(self, row, steps=0):
        """Wheel down steps rows, hold, pick a menu row and let the deferred action run."""
        for _ in range(steps):
            self.call()
        n = len(self.toasts)
        self.t = getattr(self, 't', 7000) + 1
        self.press(self.t)
        assert self.hold() == 11 and self.release() == 0
        self.pick(row)
        self.advance(0)
        return self.toasts[-1][3] if len(self.toasts) > n else None

    def labels(self):
        return [self.nodes[self.nodes[i]['children'][0]]['text'] for i in self.nodes[self.view]['children']]

    def playback(self):
        return [c for c in self.calls if c[0] in ('mclStartPlayer', 'mclStop', 'mclSetPause', 'mclSetResume', 'mclSetSeek', 'playpause_quick_click')]
try:
    sel = lambda m: [c[:2] for c in m.calls if c[0].startswith('batch_')]
except BaseException as _e:
    _SOFT.append((3651, repr(_e)[:200]))
    print('FAIL line', 3651, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class LookupMachine(QueueMachine):

    def __init__(self, pages, selected=1, lookup=True):
        super().__init__(rows=3)
        self.selected = selected
        self.closed = []
        self.backs = 0
        self.stack = [self.node('window', name) for name in pages]
        self.top = self.stack[-1]
        for w in self.stack:
            self.word(w + 16, self.string(self.nodes[w]['name']))
        self.byte(syms['g_songerinfo_flag'], int(lookup))
        self.byte(syms['g_navbar_status'], 0)
        for name in ('navigator_back', 'navigator_close'):
            self.handlers.pop(syms[name], None)
        self.mock('widget_get_window', 'widget_child', 'window_manager_back', 'window_manager_close_window_force', 'window_close', 'navigator_window_is_exist', 'strstr@GLIBC_2.0', 'strcpy@GLIBC_2.0', 'strtok@GLIBC_2.0', 'access@GLIBC_2.0', 'player_load_songlist', 'table_row_of', prefix='lookup:')

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('lookup:'):
            return super().hook(u, address, size, unused)
        assert u.reg_read(UC_MIPS_REG_T9) == address
        name = name[7:]
        a, b, c, d = [u.reg_read(r) for r in REGS]
        ret = 0
        if name == 'widget_get_window':
            ret = self.top
        elif name == 'widget_child':
            ret = next((w for w in self.stack if self.nodes[w]['name'] == self.text(b)), 0)
        elif name == 'navigator_window_is_exist':
            ret = int(any((self.nodes[w]['name'] == self.text(a) for w in self.stack)))
        elif name == 'window_manager_back':
            self.backs += 1
        elif name in ('window_manager_close_window_force', 'window_close'):
            w = b if name == 'window_manager_close_window_force' else a
            self.closed.append(self.nodes[w]['name'])
            self.stack.remove(w)
            self.top = self.stack[-1]
        elif name == 'strstr@GLIBC_2.0':
            i = self.text(a).find(self.text(b))
            ret = a + i if i >= 0 else 0
        elif name == 'strcpy@GLIBC_2.0':
            self.u.mem_write(a, self.text(b).encode() + b'\x00')
            ret = a
        elif name == 'strtok@GLIBC_2.0':
            if a:
                self.token = a
            ret = self.token
            s = self.text(ret)
            i = s.find(self.text(b))
            if i >= 0:
                self.byte(ret + i, 0)
                self.token = ret + i + 1
        elif name == 'table_row_of':
            ret = a
        elif name == 'player_load_songlist':
            assert self.text(b) == f'Row {self.selected}'
            self.deqs[a][1] = [self.copy('stSongInfo', e) for e in self.items(self.get(syms['p_deque_showlist']))]
            ret = self.selected
        self.returns(ret, clobber=True)
try:
    m = LookupMachine(['home_page', 'localclass_page', 'artistinfo_page', 'albuminfo_page', 'playing_page', 'playermore_page'], lookup=False)
except BaseException as _e:
    _SOFT.append((3716, repr(_e)[:200]))
    print('FAIL line', 3716, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class FolderClickMachine(LookupMachine):

    def __init__(self, *a):
        super().__init__(*a)
        self.videos = []
        self.handlers[payload_syms['video_click']] = 'lookup:video_click'
        self.handlers[syms['folder_enter']] = 'lookup:folder_enter'

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if name == 'lookup:video_click':
            path = self.text(u.reg_read(UC_MIPS_REG_A0))
            self.videos.append(path)
            self.returns(int(path.endswith('.mp4')), clobber=True)
            return
        if name == 'lookup:folder_enter':
            self.returns(8, clobber=True)
            return
        return super().hook(u, address, size, unused)
try:
    SONG_MENU = ['Play Next', 'Play Last', 'Add to Favorites', 'Add to Playlist', 'Go to Album', 'Go to Artist']
except BaseException as _e:
    _SOFT.append((3745, repr(_e)[:200]))
    print('FAIL line', 3745, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def playing_menu(**kw):
    m = QueueMachine(page='playing_page', pos=1, **kw)
    return m
try:
    m = playing_menu()
except BaseException as _e:
    _SOFT.append((3757, repr(_e)[:200]))
    print('FAIL line', 3757, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = playing_menu()
except BaseException as _e:
    _SOFT.append((3760, repr(_e)[:200]))
    print('FAIL line', 3760, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = playing_menu()
except BaseException as _e:
    _SOFT.append((3763, repr(_e)[:200]))
    print('FAIL line', 3763, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    id3 = syms['g_play_id3_info']
except BaseException as _e:
    _SOFT.append((3763, repr(_e)[:200]))
    print('FAIL line', 3763, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = playing_menu()
except BaseException as _e:
    _SOFT.append((3766, repr(_e)[:200]))
    print('FAIL line', 3766, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = playing_menu()
except BaseException as _e:
    _SOFT.append((3771, repr(_e)[:200]))
    print('FAIL line', 3771, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = playing_menu()
except BaseException as _e:
    _SOFT.append((3774, repr(_e)[:200]))
    print('FAIL line', 3774, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = playing_menu()
except BaseException as _e:
    _SOFT.append((3776, repr(_e)[:200]))
    print('FAIL line', 3776, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = playing_menu()
except BaseException as _e:
    _SOFT.append((3804, repr(_e)[:200]))
    print('FAIL line', 3804, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.airplay = 2
except BaseException as _e:
    _SOFT.append((3804, repr(_e)[:200]))
    print('FAIL line', 3804, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class PlayingPlaylistMachine(QueueMachine):
    STOCK_PICK = 4933696

    def __init__(self):
        super().__init__(page='playing_page', pos=1)
        self.added = []
        self.handlers[self.STOCK_PICK] = 'playlist_pick'

    def hook(self, u, address, size, unused):
        if address == self.STOCK_PICK and address in self.handlers:
            self.added.append(self.names()[1])
            self.returns(0)
            return
        opening = self.handlers.get(address) == 'q:navigator_to_with_context' and self.text(u.reg_read(UC_MIPS_REG_A0)) == 'localmusic/playlist_page'
        super().hook(u, address, size, unused)
        if opening:
            self.playlist_rows = [self.node('button', f'playlist_{i}') for i in range(3)]
            view = self.node('scroll_view', children=self.playlist_rows)
            page = self.node('window', 'playlist_page', [view])
            self.top = page
            self.stack.append(page)
            for row in self.playlist_rows:
                em, it = (self.alloc(4), self.alloc(40))
                self.word(row + O['W_EMITTER'], em)
                self.word(em, it)
                self.word(it, row)
                self.word(it + O['EMIT_TYPE'], O['EVT_CLICK'])
                self.word(it + 12, self.STOCK_PICK)

class PlaylistMachine(PlayingPlaylistMachine):

    def __init__(self, count=0, mode=1):
        super().__init__()
        self.mock('button_create', 'list_item_create', 'image_create', 'view_create', 'hscroll_label_create', 'widget_use_style', 'widget_set_name', 'widget_set_text_utf8', 'widget_set_visible', 'image_set_draw_type', 'image_base_set_image', 'set_hscroll_label_attribute', 'hscroll_label_set_only_focus', 'hscroll_label_set_ellipses', 'hscroll_label_set_speed', 'gif_image_play', 'batch_get_selectitem', 'file_is_playing', 'getFormatString', 'getMusicByPlayList', 'get_albumcover_listsize', 'mclGetPlayStatus', 'list_get_img_pic', 'toolsTrimLeft', 'toolsTrimRight', 'tk_snprintf')
        self.handlers[4902944] = 'row_toolbar'
        self.handlers[4931340] = 'playlist_load'
        self.handlers[syms['widget_restack']] = 'picker_restack'
        self.handlers[syms['navigator_to']] = 'picker_dialog'
        self.word(syms['p_deque_playlist'], self.deque([self.song(f'List {i}') for i in range(count)]))
        data = (B / 'stock-demo').read_bytes()
        from build import fileoff, GP
        self.mode = struct.unpack_from('<I', data, fileoff(data, GP - 22544))[0] - 18476
        assert self.mode == O['PLAYLIST_ADD_MODE']
        self.word(self.mode, mode)
        self.art = {}
        self.asks = []
        self.refreshes = self.stops = 0
        for n in ('playlist_art', 'playlist_art_refresh', 'playlist_art_stop'):
            if n in payload_syms:
                self.handlers[payload_syms[n]] = 'pl:' + n
        self.view = self.node('scroll_view', 'scroll_view')
        self.top = self.node('window', 'playlist_page', [self.view])
        self.word(self.view + O['W_PARENT'], self.top)
        self.word(self.top + O['W_PARENT'], self.wm)

    def rebuild(self):
        assert self.call(address=4936772, args=(self.top, 0, 0, 0), gap=0) == 0
        kids = self.nodes[self.view]['children']
        for i, row in enumerate(kids):
            self.word(row + O['W_Y'], i * 48)
        self.word(self.view + O['VIEW_CONTENT_H'], len(kids) * 48)
        return kids

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        a, b, c, d = [u.reg_read(r) for r in REGS]
        if name.startswith('pl:'):
            ret = 0
            if name == 'pl:playlist_art':
                self.asks.append((a, self.text(b), c))
                url = self.art.get(a)
                assert self.get(u.reg_read(UC_MIPS_REG_SP) + 16) > len(url or '')
                if url:
                    self.u.mem_write(d, url.encode() + b'\x00')
                    ret = 1
            elif name == 'pl:playlist_art_refresh':
                self.refreshes += 1
            else:
                self.stops += 1
            self.returns(ret)
            return
        if name == 'picker_dialog' and self.text(a) != 'dialog/addplaylist_dialog':
            self.handlers[address] = 'q:navigator_to'
            super().hook(u, address, size, unused)
            self.handlers[address] = name
            return
        if name in ('picker_restack', 'picker_dialog'):
            if name == 'picker_restack':
                kids = self.nodes[self.view]['children']
                kids.remove(a)
                kids.insert(b, a)
                ret = 0
            else:
                assert self.text(a) == 'dialog/addplaylist_dialog'
                ret = 0
            self.returns(ret)
            return
        if name == 'widget_off_by_func':
            self.nodes[a]['handlers'] = [h for h in self.nodes[a].get('handlers', []) if h != (b, c, d)]
        super().hook(u, address, size, unused)
        if name == 'q:widget_on' and b == O['EVT_CLICK']:
            it = self.get(self.get(a + O['W_EMITTER']))
            self.word(it, d)
            self.word(it + 12, c)

class PlaylistPageMachine(PlaylistMachine):
    REBUILD_DONE = 4937940

    def __init__(self, count=3, mode=0, booted=True):
        super().__init__(count, mode)
        if booted:
            oled_boot(self)
        self.handlers.pop(self.STOCK_PICK)
        self.handlers.pop(4931340)
        self.handlers[syms['widget_restack']] = 'picker_restack'
        self.mock('batch_get_selectitem', 'batch_set_selectitem', 'batch_init_selectrecord', 'batch_clear_selectrecord', 'batch_empty_selectrecord', 'batch_set_allselect', 'batch_delete_file', 'exportPlayList', 'list_selectbox_visiable', 'list_selectbox_value', 'set_allchoice_img', 'widget_layout', 'widget_set_children_layout', 'load_localclass_list', 'navbar_show_playgif', 'hscroll_label_set_only_focus', 'getFormatString', 'gif_image_create', 'widget_add_timer', 'widget_set_tr_text', 'image_animation_play', 'image_animation_pause', 'navigator_to_with_context')
        self.nav = self.node('view', 'view_navbar', visible=0)
        self.nodes[self.nav]['enable'] = 0
        self.word(self.nav + O['W_PARENT'], self.top)
        self.nodes[self.top]['children'].insert(0, self.nav)
        gp = 4939692 + 5786420
        assert [self.get(4939812 + 4 * i) for i in range(7)] == [604307732, 2411462672, 33568805, 2409211836, 2407966688, 52492297, 33562661]
        assert self.get(gp - 22596) == syms['widget_on'] and self.get(gp - 22560) == syms['on_common_keyup'] == 5186452
        self.nodes[self.top].setdefault('handlers', []).append((O['EVT_KEY_UP'], 5186452, self.top))
        self.word(syms['g_class_type'], 61451)
        self.marks = []
        self.answer = 10
        self.confirms = []
        self.exports = []
        self.deletes = []
        self.dialogs = []
        self.mores = []
        self.on_click = lambda w, e: [self.nested(f, ctx, e, 0, 0) for t, f, ctx in reversed(list(self.nodes[w].get('handlers', ()))) if t == O['EVT_CLICK']]
        self.stack = [self.node('window', 'localmusic_page'), self.top]
        self.lists(*[f'List {i}' for i in range(count)])
        self.word(syms['tools_pdeq_directory'], self.deque([self.song(c) for c in 'ABC']))

    def lists(self, *names):
        records = [self.song(n) for n in names]
        for i, (r, n) in enumerate(zip(records, names)):
            self.word(r + O['REC_TITLE'], self.string(n))
            self.word(r + O['REC_DISC'], 10 + i)
        self.word(syms['p_deque_playlist'], self.deque(records))

    def rebuild(self):
        assert self.call(address=4936772, args=(self.top, 0, 0, 0), gap=0, clear=False) == 0
        self.stack_rows()
        return self.nodes[self.view]['children']

    def stack_rows(self):
        y = 0
        for row in self.nodes[self.view]['children']:
            if self.nodes[row]['visible']:
                self.word(row + O['W_Y'], y)
                y += self.get(row + O['W_H'])
        self.word(self.view + O['VIEW_CONTENT_H'], y)
        self.word(self.view + O['W_H'], 294)
        self.word(self.view + O['W_W'], 375)

    def rows(self):
        return [self.row_text(r) for r in self.nodes[self.view]['children'] if self.nodes[r]['visible']]

    def row_text(self, row):
        button = self.nodes[row]['children'][0]
        kids = self.nodes[button]['children']
        info = next((k for k in kids if self.nodes[k]['name'] == 'view_info'), None)
        return self.nodes[self.nodes[info]['children'][0] if info else kids[0]].get('text')

    def row_button(self, text):
        return next((self.nodes[r]['children'][0] for r in self.nodes[self.view]['children'] if self.row_text(r) == text))

    def navbar(self):
        return self.u.mem_read(syms['g_navbar_status'], 1)[0]

    def bar(self):
        return self.nodes[self.nodes[self.nav]['children'][0]].get('text')

    def tap(self, button, x=0):
        ev = self.alloc(48)
        self.word(ev, O['EVT_CLICK'])
        self.word(ev + O['EVENT_X'], x)
        self.on_click(button, ev)
        self.advance(0, clear=False)

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        a, b, c, d = [u.reg_read(r) for r in REGS]
        ret = None
        if name == 'widget_set_name':
            self.word(a + 16, self.string(self.text(b)))
        if name == 'batch_init_selectrecord':
            self.marks = [0] * a
            ret = 0
        elif name == 'batch_clear_selectrecord':
            self.marks = []
            ret = 0
        elif name == 'batch_empty_selectrecord':
            ret = int(not any(self.marks))
        elif name == 'batch_set_selectitem':
            self.marks[a] ^= 1
            ret = int(all(self.marks))
        elif name == 'batch_get_selectitem':
            ret = self.marks[a] if a < len(self.marks) else 0
        elif name == 'batch_set_allselect':
            self.marks = [a] * len(self.marks)
            ret = 0
        elif name == 'batch_delete_file':
            self.deletes.append(list(self.marks))
            ret = 0
        elif name == 'exportPlayList':
            self.exports.append((a, list(self.marks)))
            ret = 0
        elif name == 'list_selectbox_visiable' or name == 'list_selectbox_value':

            def walk(w):
                for k in self.nodes[w]['children']:
                    nm = self.nodes[k].get('name', '') or ''
                    if nm.startswith('img_choice'):
                        if name.endswith('visiable'):
                            self.nodes[k]['visible'] = b
                            self.nodes[k]['image'] = 'unselect'
                        else:
                            self.nodes[k]['image'] = 'mulselect' if b else 'unselect'
                    if nm.startswith('img_more') and name.endswith('visiable'):
                        self.nodes[k]['visible'] = int(not b)
                    walk(k)
            walk(a)
            ret = 0
        elif name == 'widget_layout':
            self.stack_rows()
            ret = 0
        elif name == 'getFormatString':
            self.u.mem_write(b, b'Total %d songs\x00')
            ret = 0
        elif name == 'navigator_to_with_context':
            page = self.text(a)
            if page == 'dialog/confirminfo_dialog':
                self.confirms.append(self.get(b))
                ret = self.answer
            elif page == 'localmusic/playlistsmore_page':
                self.mores.append(b)
                ret = 0
            else:
                ret = 0
        elif name == 'picker_dialog' and self.text(a) == 'dialog/addplaylist_dialog':
            self.dialogs.append(self.text(a))
            ret = 0
        if ret is not None:
            self.calls.append((name, a, b, c))
            self.returns(ret)
            return
        super().hook(u, address, size, unused)

class RowPlaylistMachine(PlaylistPageMachine):

    def __init__(self, count=3, mode=0):
        super().__init__(count, mode)

def cpm_wheel(m, key, n=1):
    for _ in range(n):
        m.call(key)
        m.stack_rows()
    m.paint(m.view)

def cpm_centre(m):
    m.confirm()
    m.advance(0, clear=False)
    m.stack_rows()
    m.paint(m.view)
try:
    NEW = 'New Playlist…'
except BaseException as _e:
    _SOFT.append((4025, repr(_e)[:200]))
    print('FAIL line', 4025, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    LISTS = ['List 0', 'List 1', 'List 2']
except BaseException as _e:
    _SOFT.append((4026, repr(_e)[:200]))
    print('FAIL line', 4026, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    PL_ART = lambda cd: f'file:///mnt/mmc/.coverflow/pl_{cd}_0000abcd_s.jpg'
except BaseException as _e:
    _SOFT.append((4027, repr(_e)[:200]))
    print('FAIL line', 4027, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def pl_art_lists(m, n):
    records = [m.song(f'List {i}') for i in range(n)]
    for i, r in enumerate(records):
        m.word(r + O['REC_TITLE'], m.string(f'List {i}'))
        m.word(r + O['REC_DISC'], 10 + i)
    m.word(syms['p_deque_playlist'], m.deque(records))

def pl_art_of(m, button):
    return next((k for k in m.nodes[button]['children'] if m.nodes[k]['type'] == 'image' and (not (m.nodes[k].get('name') or '').startswith('img_'))))
try:
    m = RowPlaylistMachine()
except BaseException as _e:
    _SOFT.append((4036, repr(_e)[:200]))
    print('FAIL line', 4036, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = RowPlaylistMachine(3, 1)
except BaseException as _e:
    _SOFT.append((4046, repr(_e)[:200]))
    print('FAIL line', 4046, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.art = {11: PL_ART(11)}
except BaseException as _e:
    _SOFT.append((4046, repr(_e)[:200]))
    print('FAIL line', 4046, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    PL_HINT = 'htranslate(duration=200)'
except BaseException as _e:
    _SOFT.append((4068, repr(_e)[:200]))
    print('FAIL line', 4068, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def pl_cf(base, count=2, mode=0, on='1', **kw):

    class M(base):

        def hook(self, u, address, size, unused):
            if address != payload_syms['coverflow_playlists']:
                return super().hook(u, address, size, unused)
            self.handed.append(self.names(u.reg_read(REGS[0])))
            self.returns(1)
    m = M(count, mode, **kw)
    m.handlers[syms['widget_set_prop_str']] = 'widget_set_prop_str'
    m.handed = []
    m.config['PLAYLISTCF'] = on
    if base is PlaylistMachine:
        pl_art_lists(m, count)
    m.nodes[m.top]['anim_hint'] = PL_HINT
    m.byte(m.top + 52, 255)
    return m
try:
    pl_veiled = lambda m: m.u.mem_read(m.top + 52, 1)[0] == 0 and m.nodes[m.top]['anim_hint'] == ''
except BaseException as _e:
    _SOFT.append((4079, repr(_e)[:200]))
    print('FAIL line', 4079, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    pl_shown = lambda m: m.u.mem_read(m.top + 52, 1)[0] == 255 and m.nodes[m.top]['anim_hint'] == PL_HINT
except BaseException as _e:
    _SOFT.append((4080, repr(_e)[:200]))
    print('FAIL line', 4080, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = pl_cf(PlaylistMachine)
except BaseException as _e:
    _SOFT.append((4091, repr(_e)[:200]))
    print('FAIL line', 4091, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.top, O['EVT_DESTROY'])
except BaseException as _e:
    _SOFT.append((4092, repr(_e)[:200]))
    print('FAIL line', 4092, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = pl_cf(PlaylistMachine)
except BaseException as _e:
    _SOFT.append((4095, repr(_e)[:200]))
    print('FAIL line', 4095, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    page = m.top
except BaseException as _e:
    _SOFT.append((4095, repr(_e)[:200]))
    print('FAIL line', 4095, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('window', 'other_page')
except BaseException as _e:
    _SOFT.append((4095, repr(_e)[:200]))
    print('FAIL line', 4095, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = page
except BaseException as _e:
    _SOFT.append((4096, repr(_e)[:200]))
    print('FAIL line', 4096, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def pl_under(m):
    lists = m.items(m.get(syms['p_deque_playlist']))
    m.word(syms['p_deque_playlist'], m.deque(lists[::-1]))
    m.rebuild()
    page = m.top
    m.top = m.node('window', 'coverflow_page')
    m.stack.append(m.top)
    return page
try:
    opens = lambda m: [(c[0], m.text(c[1])) for c in m.calls if c[0] == 'navigator_to_with_context']
except BaseException as _e:
    _SOFT.append((4113, repr(_e)[:200]))
    print('FAIL line', 4113, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class PlShut(RowPlaylistMachine):
    shut = 0

    def hook(self, u, address, size, unused):
        if address != payload_syms['coverflow_playlists_close']:
            return super().hook(u, address, size, unused)
        self.shut += 1
        self.returns(0)
try:
    m = pl_cf(PlShut, count=3, on='0')
except BaseException as _e:
    _SOFT.append((4119, repr(_e)[:200]))
    print('FAIL line', 4119, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    page = pl_under(m)
except BaseException as _e:
    _SOFT.append((4119, repr(_e)[:200]))
    print('FAIL line', 4119, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    made = m.song('Made', 4)
except BaseException as _e:
    _SOFT.append((4120, repr(_e)[:200]))
    print('FAIL line', 4120, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine()
except BaseException as _e:
    _SOFT.append((4126, repr(_e)[:200]))
    print('FAIL line', 4126, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    page = m.top
except BaseException as _e:
    _SOFT.append((4126, repr(_e)[:200]))
    print('FAIL line', 4126, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.top, O['EVT_KEY_UP'])
except BaseException as _e:
    _SOFT.append((4135, repr(_e)[:200]))
    print('FAIL line', 4135, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ev = m.alloc(64)
except BaseException as _e:
    _SOFT.append((4135, repr(_e)[:200]))
    print('FAIL line', 4135, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine()
except BaseException as _e:
    _SOFT.append((4142, repr(_e)[:200]))
    print('FAIL line', 4142, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def played_last(m, add, queue=('A', 'B', 'C')):
    pos = m.mcl('MCL_POS')
    assert m.mcl('MCL_MODE') in (0, 3), m.mcl('MCL_MODE')
    return [*queue[:pos + 1], *add, *queue[pos + 1:]]
try:
    m = QueueMachine()
except BaseException as _e:
    _SOFT.append((4151, repr(_e)[:200]))
    print('FAIL line', 4151, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine()
except BaseException as _e:
    _SOFT.append((4160, repr(_e)[:200]))
    print('FAIL line', 4160, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine(page='folder_page')
except BaseException as _e:
    _SOFT.append((4184, repr(_e)[:200]))
    print('FAIL line', 4184, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine(page='folder_page')
except BaseException as _e:
    _SOFT.append((4186, repr(_e)[:200]))
    print('FAIL line', 4186, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class ScopedQueueMachine(QueueMachine):

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if name.startswith('q:getMusic') or name == 'q:toolsLoadDirectory':
            scope = self.get(payload_syms['library_music'])
            assert scope == int(name != 'q:toolsLoadDirectory')
            found = self.found
            if scope:
                self.found = found[:1]
            super().hook(u, address, size, unused)
            self.found = found
            return
        return super().hook(u, address, size, unused)
try:
    m = QueueMachine(page='localclass_page', cls=61444)
except BaseException as _e:
    _SOFT.append((4225, repr(_e)[:200]))
    print('FAIL line', 4225, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine(page='localclass_page', cls=65281)
except BaseException as _e:
    _SOFT.append((4227, repr(_e)[:200]))
    print('FAIL line', 4227, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def menu(m, setup=None):
    if setup:
        setup(m)
    m.press(5)
    assert m.hold() == 11
    t = m.nodes[m.title]['text']
    got = m.labels()
    m.release()
    return (t, got)
try:
    GROUP = ['Play Next', 'Play Last', 'Shuffle', 'Add to Playlist']
except BaseException as _e:
    _SOFT.append((4235, repr(_e)[:200]))
    print('FAIL line', 4235, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine()
except BaseException as _e:
    _SOFT.append((4252, repr(_e)[:200]))
    print('FAIL line', 4252, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine(cls=61444, page='localclass_page')
except BaseException as _e:
    _SOFT.append((4267, repr(_e)[:200]))
    print('FAIL line', 4267, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[payload_syms['playback_shuffle']] = 'stock_playback_shuffle'
except BaseException as _e:
    _SOFT.append((4267, repr(_e)[:200]))
    print('FAIL line', 4267, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine(cls=61444, page='localclass_page')
except BaseException as _e:
    _SOFT.append((4271, repr(_e)[:200]))
    print('FAIL line', 4271, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.found = []
except BaseException as _e:
    _SOFT.append((4271, repr(_e)[:200]))
    print('FAIL line', 4271, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class KeepsMachine(QueueMachine):

    def __init__(self, **kw):
        super().__init__(**kw)
        self.handlers[payload_syms['playback_shuffle']] = 'stock_playback_shuffle'
        self.handlers[payload_syms['playback_keeps']] = 'keeps:'
        self.drawn = []
        self.handlers[payload_syms['playback_np']] = 'keeps:np'
        self.nps = 0

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if name == 'keeps:':
            self.drawn.append(u.reg_read(REGS[1]))
            return self.returns(len(self.drawn) == 1)
        if name == 'keeps:np':
            self.nps += 1
            return self.returns(0)
        return super().hook(u, address, size, unused)
try:
    m = KeepsMachine(cls=61444, page='localclass_page')
except BaseException as _e:
    _SOFT.append((4283, repr(_e)[:200]))
    print('FAIL line', 4283, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = KeepsMachine(cls=61444, page='localclass_page')
except BaseException as _e:
    _SOFT.append((4285, repr(_e)[:200]))
    print('FAIL line', 4285, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.found = m.found[:1]
except BaseException as _e:
    _SOFT.append((4285, repr(_e)[:200]))
    print('FAIL line', 4285, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    info = syms['g_local_classinfo_save']
except BaseException as _e:
    _SOFT.append((4288, repr(_e)[:200]))
    print('FAIL line', 4288, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine(queue=0)
except BaseException as _e:
    _SOFT.append((4298, repr(_e)[:200]))
    print('FAIL line', 4298, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    demo = (B / 'demo').read_bytes()
except BaseException as _e:
    _SOFT.append((4307, repr(_e)[:200]))
    print('FAIL line', 4307, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine(mode=2)
except BaseException as _e:
    _SOFT.append((4309, repr(_e)[:200]))
    print('FAIL line', 4309, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from build import BT_INIT, BT_PARAMS

class BtInitMachine(Machine):

    def hook(self, u, addr, size, _):
        if self.handlers.get(addr) == 'btctl_create_daemon_thread':
            self.calls.append(('btctl_create_daemon_thread', u.reg_read(REGS[0]), 0))
            self.word(u.reg_read(REGS[0]), 305419896)
            self.returns(self.result, clobber=True)
            return
        super().hook(u, addr, size, _)
from build import BT_STATUS, BT_AUTOLINK

class BtLinkMachine(Machine):

    def __init__(self, links, paired=True, connecting=1, saved='AA:AA:AA:AA:AA:AA'):
        super().__init__()
        self.links = [list(x) for x in links]
        self.paired = paired
        self.u.mem_write(10797272, saved.encode() + b'\x00')
        self.word(syms['bt_connectstatus'], connecting)
        self.handlers[5226880] = 'device_status'
        self.mock('system@GLIBC_2.0')

    def hook(self, u, addr, size, _):
        name = self.handlers.get(addr)
        if name not in ('getBtLinkStatus', 'getBtLinkSize', 'getBtLinkItem', 'addBtLinkItem', 'updateBtLinkStatus', 'checkPairDev', 'device_status'):
            return super().hook(u, addr, size, _)
        assert u.reg_read(UC_MIPS_REG_T9) == addr, (name, 'PIC call missing t9')
        a, b, c, d = [u.reg_read(r) for r in REGS]
        self.calls.append((name, a, b, c, d))
        ret = 0
        if name == 'getBtLinkStatus':
            ret = next((st for x, st in self.links if x == self.text(a)), 0)
        elif name == 'getBtLinkSize':
            ret = len(self.links)
        elif name == 'getBtLinkItem' and 0 <= signed(a) < len(self.links):
            self.word(b, self.links[a][1])
            self.u.mem_write(c, self.links[a][0].encode() + b'\x00')
        elif name == 'addBtLinkItem':
            self.links.append([self.text(a), b])
        elif name == 'updateBtLinkStatus':
            for x in self.links:
                if x[0] == self.text(b):
                    x[1] = a
                    ret = 1
        elif name == 'checkPairDev':
            ret = self.paired
        self.returns(ret, clobber=True)
try:
    a_mac, b_mac = ('AA:AA:AA:AA:AA:AA', 'BB:BB:BB:BB:BB:BB')
except BaseException as _e:
    _SOFT.append((4370, repr(_e)[:200]))
    print('FAIL line', 4370, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    link = BtLinkMachine([])
except BaseException as _e:
    _SOFT.append((4371, repr(_e)[:200]))
    print('FAIL line', 4371, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    link = BtLinkMachine([(a_mac, 5)])
except BaseException as _e:
    _SOFT.append((4373, repr(_e)[:200]))
    print('FAIL line', 4373, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class OutputVolumeMachine(Machine):

    def __init__(self, config=()):
        super().__init__()
        self.way = 0
        self.config.update(config)
        self.byte(syms['g_volume'], 20)
        self.byte(syms['g_maxvolume'], 100)
        self.handlers.pop(syms['write_int_config'])
        for name, label in (('mclSetOutputWay', 'output'), ('write_int_config', 'write_config')):
            self.handlers[HOOKS[name][0] + 12] = 'stock_' + label

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if name not in ('mclGetOutputWay', 'stock_output', 'stock_write_config'):
            return super().hook(u, address, size, unused)
        a, b, c = [u.reg_read(r) for r in REGS[:3]]
        self.calls.append((name, a, b, c))
        if name == 'stock_output':
            self.way = a
            ret = 1
        elif name == 'stock_write_config':
            self.config[self.text(c)] = str(a)
            ret = 0
        else:
            ret = self.way
        self.returns(ret)

    def output(self, way):
        changed = (self.way == 1) != (way == 1)
        assert self.call(address=syms['mclSetOutputWay'], args=(way, 0, 0, 0), gap=0) == 1
        volume = self.u.mem_read(syms['g_volume'], 1)[0]
        assert [c[1:3] for c in self.calls if c[0] == 'device_set_volume'] == ([(volume, 1)] if changed else [])
        return volume

    def save(self, volume, section='PLAYSET', key='VOLUME'):
        self.byte(syms['g_volume'], volume)
        return self.call(address=syms['write_int_config'], args=(volume, self.string(section), self.string(key), 0), gap=0)
try:
    ov = OutputVolumeMachine()
except BaseException as _e:
    _SOFT.append((4421, repr(_e)[:200]))
    print('FAIL line', 4421, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ov = OutputVolumeMachine()
except BaseException as _e:
    _SOFT.append((4433, repr(_e)[:200]))
    print('FAIL line', 4433, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class BtVolumeMachine(Machine):

    def __init__(self, link=True):
        super().__init__()
        if link:
            self.config['BTVOLLINK'] = '1'
        self.absolute = 10
        self.write_result = 0
        self.way = 1
        self.byte(syms['g_bluetoothflag'], 1)
        self.byte(syms['bt_linkstatus'], 1)
        self.word(syms['bt_showcoding'], 1)
        self.byte(syms['g_volume'], 20)
        self.byte(syms['g_maxvolume'], 100)
        self.handlers[HOOKS['mclSetBtVol'][0] + 12] = 'stock_btvol'
        self.handlers[HOOKS['main_loop_sleep_default'][0] + 12] = 'stock_sleep'
        self.mock('mclSetLocalVol', 'mclUsbAudioSetVol', 'notifyVolume', 'airplaySendVol')
        self.handlers.pop(syms['device_set_volume'], None)

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if name not in ('btctl_transport_get_volume', 'btctl_transport_set_volume', 'mclGetOutputWay'):
            return super().hook(u, address, size, unused)
        a = u.reg_read(REGS[0])
        self.calls.append((name, a, 0, 0))
        if name == 'btctl_transport_get_volume':
            ret = self.absolute
        elif name == 'mclGetOutputWay':
            ret = self.way
        else:
            ret = self.write_result
            if ret == 0:
                self.absolute = a
        self.returns(ret)

    def volume(self, left, right):
        self.call(address=HOOKS['mclSetBtVol'][0], args=(left, right, 0, 0), gap=0)
        return [(signed(c[1]), signed(c[2])) for c in self.calls if c[0] == 'stock_btvol']

    def writes(self):
        return [c[1] for c in self.calls if c[0] == 'btctl_transport_set_volume']

    def poll(self, gap=400):
        self.call(address=HOOKS['main_loop_sleep_default'][0], args=(self.wm, 0, 0, 0), gap=gap)
try:
    b = BtVolumeMachine(link=False)
except BaseException as _e:
    _SOFT.append((4472, repr(_e)[:200]))
    print('FAIL line', 4472, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b = BtVolumeMachine()
except BaseException as _e:
    _SOFT.append((4475, repr(_e)[:200]))
    print('FAIL line', 4475, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b = BtVolumeMachine()
except BaseException as _e:
    _SOFT.append((4477, repr(_e)[:200]))
    print('FAIL line', 4477, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b = BtVolumeMachine()
except BaseException as _e:
    _SOFT.append((4482, repr(_e)[:200]))
    print('FAIL line', 4482, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.write_result = -1
except BaseException as _e:
    _SOFT.append((4482, repr(_e)[:200]))
    print('FAIL line', 4482, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b = BtVolumeMachine()
except BaseException as _e:
    _SOFT.append((4491, repr(_e)[:200]))
    print('FAIL line', 4491, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.absolute = -1
except BaseException as _e:
    _SOFT.append((4491, repr(_e)[:200]))
    print('FAIL line', 4491, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.absolute = 10
except BaseException as _e:
    _SOFT.append((4493, repr(_e)[:200]))
    print('FAIL line', 4493, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.absolute = 5
except BaseException as _e:
    _SOFT.append((4495, repr(_e)[:200]))
    print('FAIL line', 4495, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b = BtVolumeMachine()
except BaseException as _e:
    _SOFT.append((4499, repr(_e)[:200]))
    print('FAIL line', 4499, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.write_result = -1
except BaseException as _e:
    _SOFT.append((4499, repr(_e)[:200]))
    print('FAIL line', 4499, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.write_result = 0
except BaseException as _e:
    _SOFT.append((4501, repr(_e)[:200]))
    print('FAIL line', 4501, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b = BtVolumeMachine()
except BaseException as _e:
    _SOFT.append((4504, repr(_e)[:200]))
    print('FAIL line', 4504, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.calls = []
except BaseException as _e:
    _SOFT.append((4504, repr(_e)[:200]))
    print('FAIL line', 4504, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.absolute = 50
except BaseException as _e:
    _SOFT.append((4504, repr(_e)[:200]))
    print('FAIL line', 4504, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b = BtVolumeMachine()
except BaseException as _e:
    _SOFT.append((4508, repr(_e)[:200]))
    print('FAIL line', 4508, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.absolute = 127
except BaseException as _e:
    _SOFT.append((4508, repr(_e)[:200]))
    print('FAIL line', 4508, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b = BtVolumeMachine()
except BaseException as _e:
    _SOFT.append((4512, repr(_e)[:200]))
    print('FAIL line', 4512, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.absolute = -1
except BaseException as _e:
    _SOFT.append((4512, repr(_e)[:200]))
    print('FAIL line', 4512, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    asks = []
except BaseException as _e:
    _SOFT.append((4512, repr(_e)[:200]))
    print('FAIL line', 4512, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sleep_hook = HOOKS['main_loop_sleep_default'][0]
except BaseException as _e:
    _SOFT.append((4531, repr(_e)[:200]))
    print('FAIL line', 4531, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine(mode=2)
except BaseException as _e:
    _SOFT.append((4557, repr(_e)[:200]))
    print('FAIL line', 4557, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class SpecFolderMachine(Machine):
    LISTINGS = {'/m/0': (3, 1), '/m/1': (5, 0), '/m/2': (0, 0)}

    def __init__(self):
        super().__init__(True)
        self.listed = []
        self.seen = []
        self.row = self.alloc(256)
        for n in ('table_row_of', 'get_specfolderurl', 'toolsLoadDirectory', 'toolsCheckTypeDeque'):
            self.handlers[syms[n]] = 'spec:' + n
        self.handlers[int(manifest['patch_symbols']['stock_specfolder_click_trampoline'], 16)] = 'spec:stock'

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('spec:'):
            return super().hook(u, address, size, unused)
        name = name[5:]
        a, b, c, d = [u.reg_read(r) for r in REGS]
        ret = 0
        if name == 'table_row_of':
            ret = a and self.row
        elif name == 'get_specfolderurl':
            u.mem_write(b, f'/m/{a}'.encode() + b'\x00')
        elif name == 'toolsLoadDirectory':
            self.listed.append(self.text(a))
            ret = self.LISTINGS[self.text(a)][0]
        elif name == 'toolsCheckTypeDeque':
            assert b == 4
            ret = self.LISTINGS[self.listed[-1]][1]
        else:
            self.seen.append((a, self.get(b + O['EVENT_X'])))
        self.returns(ret)

    def tap(self, index, x, on_row=True):
        ev = self.alloc(48)
        self.word(ev + O['EVENT_X'], x)
        self.word(ev + O['EVENT_TARGET'], 4096 if on_row else 0)
        self.word(self.row + O['ROW_INDEX'], index)
        self.call(address=HOOKS['specfolder_click'][0], args=(119, ev, 0, 0), gap=0)
        return (self.listed, self.seen[-1])
try:
    m = SpecFolderMachine()
except BaseException as _e:
    _SOFT.append((4586, repr(_e)[:200]))
    print('FAIL line', 4586, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    SONGS, STOP, LISTING = (10814756, 10814760, 10813720)
except BaseException as _e:
    _SOFT.append((4589, repr(_e)[:200]))
    print('FAIL line', 4589, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class ScanMachine(Machine):

    def __init__(self, tree, songs, patched=True, stop_at=None):
        super().__init__(patched)
        self.tree = tree
        self.added = []
        self.stop_at = stop_at
        for n in ('toolsSetScanFolderPath', 'toolsCheckItemExist', 'deque_size', 'deque_at', 'sprintf@GLIBC_2.0'):
            self.handlers[syms[n]] = 'scan:' + n
        self.handlers[6120736] = 'scan:list'
        self.handlers[6135932] = 'scan:insert'
        self.word(SONGS, songs)
        self.word(LISTING, self.alloc(32))

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('scan:'):
            return super().hook(u, address, size, unused)
        assert u.reg_read(UC_MIPS_REG_T9) == address
        name = name[5:].split('@')[0]
        a, b, c, d = [u.reg_read(r) for r in REGS]
        ret = 0
        if name == 'list':
            self.path = self.text(a)
            self.listing = []
            for n in self.tree[self.path]:
                r = self.alloc(96)
                self.word(r + O['REC_NAME'], self.string(n))
                self.word(r + O['REC_TYPE'], 4 if f'{self.path}/{n}' in self.tree else 8)
                self.listing.append(r)
        elif name == 'deque_size':
            assert a == self.get(LISTING)
            ret = len(self.listing)
        elif name == 'deque_at':
            ret = self.listing[b]
        elif name == 'sprintf':
            assert self.text(b) == '%s/%s'
            u.mem_write(a, f'{self.text(c)}/{self.text(d)}'.encode() + b'\x00')
        elif name == 'insert':
            self.added.append(f'{self.path}/{self.text(self.get(a + O['REC_NAME']))}')
            self.word(SONGS, self.get(SONGS) + 1)
            if len(self.added) == self.stop_at:
                self.byte(STOP, 1)
        self.returns(ret)

def scan(songs, tree={'/m': ['a']}, **kw):
    """(return, songs added, stop flag, count): 1 finished, 0 stopped."""
    m = ScanMachine(tree, songs, **kw)
    return (m.call(address=syms['toolsLoadAllFile'], args=(m.string('/m'), 0, 0, 0), gap=0), m.added, m.u.mem_read(STOP, 1)[0], m.get(SONGS))
try:
    nested = {'/m': ['a', 'sub', 'b'], '/m/sub': ['c', 'd']}
except BaseException as _e:
    _SOFT.append((4620, repr(_e)[:200]))
    print('FAIL line', 4620, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from build import SCAN_REFRESH

class FolderTagsMachine(ScanMachine):

    def __init__(self, patched=True, cue=False, valid=True, songs=1, title="New artist's tags"):
        super().__init__({'/m': ["it's.flac"]}, songs, patched=patched)
        self.valid = valid
        self.cue = cue
        self.title = title
        self.reads = 0
        self.updates = []
        self.db = sqlite3.connect(':memory:')
        self.db.execute('create table songtable(id primary key,filename,fileurl,album,songer,style,name,composer,albumsonger,year,track,cd_num,time,time_end,time_create,Initial)')
        self.old = (7, "it's.flac", "/m/it's.flac", 'Old album', 'Old artist', 'Old genre', 'Old title', 'Old composer', 'Old album artist', 1990, 1, 1, 10 if cue else 0, 20 if cue else 0, 123, ord('O'))
        self.db.execute('insert into songtable values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', self.old)
        self.db.execute('insert into songtable values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (8, *self.old[1:12], 30, 40, 456, ord('O')))
        for n in ('taglib_file_new', 'taglib_file_tag', 'taglib_file_free', 'toolsGetMusicInfo', 'toolsUpdateDbTable', 'toolsTrimLeft', 'toolsTrimRight', 'toolsHanziToPinyin'):
            self.handlers[syms[n]] = 'refresh:' + n

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        a, b, c, d = [u.reg_read(r) for r in REGS]
        if name == 'scan:toolsCheckItemExist':
            self.word(a + O['REC_PATH'], self.string("/m/it's.flac"))
            if self.cue:
                for off, v in ((72, 10), (76, 20), (48, 2026), (60, 3), (64, 2)):
                    self.word(a + off, v)
                for off in (20, 24, 28, 16, 32, 36):
                    self.word(a + off, self.string("CUE's tags"))
            ret = self.text(self.get(a + O['REC_NAME'])) != 'new.flac'
        elif name.startswith('refresh:'):
            name = name[8:]
            ret = 0
            if name == 'taglib_file_new':
                assert b == 0
                ret = 123 if self.valid else 0
            elif name == 'taglib_file_tag':
                assert a == 123
                ret = 456
            elif name in ('toolsTrimLeft', 'toolsTrimRight'):
                u.mem_write(a, self.text(a).strip().encode() + b'\x00')
            elif name == 'toolsHanziToPinyin':
                u.mem_write(b, self.text(a).encode() + b'\x00')
            elif name == 'toolsGetMusicInfo':
                self.reads += 1
                assert (a, self.text(c)) == (0, "/m/it's.flac")
                ret = 1
                for off in (0, 256, 512, 640, 1152, 1408):
                    u.mem_write(b + off, (self.title if off == 640 else "New artist's tags").encode() + b'\x00')
                for off, v in ((1664, 2026), (1668, 3), (1672, 2)):
                    self.word(b + off, v)
            elif name == 'toolsUpdateDbTable':
                assert self.text(a) == '/mnt/data/database.db'
                self.updates.append(self.text(b))
                self.db.execute(self.text(b))
                ret = 1
        else:
            return super().hook(u, address, size, unused)
        self.returns(ret)

    def rescan(self):
        return self.call(address=syms['toolsLoadAllFile'], args=(self.string('/m'), self.string('songtable'), 1, 0), gap=0)
try:
    m = FolderTagsMachine(songs=65000)
except BaseException as _e:
    _SOFT.append((4681, repr(_e)[:200]))
    print('FAIL line', 4681, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.tree['/m'] = ['new.flac', "it's.flac"]
except BaseException as _e:
    _SOFT.append((4682, repr(_e)[:200]))
    print('FAIL line', 4682, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from build import CLASS_ICON
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((4703, repr(_e)[:200]))
    print('FAIL line', 4703, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    icon = m.node('image', 'img_icon')
except BaseException as _e:
    _SOFT.append((4703, repr(_e)[:200]))
    print('FAIL line', 4703, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    row = m.node('view', '', [icon])
except BaseException as _e:
    _SOFT.append((4703, repr(_e)[:200]))
    print('FAIL line', 4703, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from build import SONG_UNKNOWN_CALL

class SongBindMachine(Machine):

    def hook(self, u, address, size, unused):
        if self.handlers.get(address) == 'b:format':
            self.formats.append(self.text(u.reg_read(UC_MIPS_REG_A0)))
            self.u.mem_write(u.reg_read(UC_MIPS_REG_A1), b'Unknown Artist\x00')
            self.returns(0)
            return
        return super().hook(u, address, size, unused)
from build import SORT_TRIMS

class SortMachine(Machine):

    def __init__(self):
        super().__init__()
        self.handlers.pop(syms['toolsTrimLeft'])
        self.mock('strlen@GLIBC_2.0', 'snprintf@GLIBC_2.0')
        for n in ('__ctype_b_loc@GLIBC_2.3', '__ctype_tolower_loc@GLIBC_2.3', 'strncmp@GLIBC_2.0', 'atoi@GLIBC_2.0', 'strcasecmp@GLIBC_2.0', 'strcmp@GLIBC_2.0', 'strncpy@GLIBC_2.0'):
            self.handlers[syms[n]] = 's:' + n
        b = self.alloc(1024)
        t = self.alloc(1024)
        for c in range(-128, 256):
            self.u.mem_write(b + 256 + 2 * c, struct.pack('<H', 2048 * (48 <= c < 58)))
            self.word(t + 512 + 4 * c, ord(chr(c).lower()) if 0 <= c < 128 else c)
        self.word(b, b + 256)
        self.word(t, t + 512)
        self.tables = (b, t)

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('s:'):
            return super().hook(u, address, size, unused)
        name = name[2:].split('@')[0]
        a, b, c = [u.reg_read(r) for r in REGS[:3]]
        s = lambda x, n=None: bytes(u.mem_read(x, 4096)).split(b'\x00')[0][:n]
        if name.startswith('__ctype'):
            ret = self.tables[name == '__ctype_tolower_loc']
        elif name == 'atoi':
            ret = int(re.match(b'\\d*', s(a))[0] or 0)
        elif name == 'strncpy':
            u.mem_write(a, s(b, c).ljust(c, b'\x00'))
            ret = a
        else:
            x, y = (s(a, c), s(b, c)) if name == 'strncmp' else (s(a), s(b))
            x, y = (x.lower(), y.lower()) if name == 'strcasecmp' else (x, y)
            ret = (x > y) - (x < y)
        self.returns(ret)
try:
    m = SortMachine()
except BaseException as _e:
    _SOFT.append((4755, repr(_e)[:200]))
    print('FAIL line', 4755, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def sort_rec(name):
    r = m.alloc(96)
    m.word(r + 16, m.string(name))
    m.word(r + 8, m.string(name + '.flac'))
    return r

def ordered(names, cmp):
    out = m.alloc(4)

    def less(x, y):
        m.call(address=cmp, args=(x, y, out, 0), gap=0, count=2000000)
        return m.get(out)
    rs = {sort_rec(n): n for n in names}
    return [rs[r] for r in sorted(rs, key=cmp_to_key(lambda x, y: -1 if less(x, y) else 1 if less(y, x) else 0))]
try:
    names = ['Daft Punk', 'The Cure', 'Coldplay', 'Them Crooked Vultures', 'A Tribe Called Quest', 'Anthrax', 'The', 'an Orchestra', 'Toto']
except BaseException as _e:
    _SOFT.append((4764, repr(_e)[:200]))
    print('FAIL line', 4764, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    key = lambda t: (m.u.mem_write(17825792, t.encode() + b'\x00'), m.call(address=payload_syms['ringnav_sort_key'], args=(17825792, 0, 0, 0), gap=0), m.text(17825792))[2]
except BaseException as _e:
    _SOFT.append((4768, repr(_e)[:200]))
    print('FAIL line', 4768, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    boot = payload_syms['ringnav_boot']
except BaseException as _e:
    _SOFT.append((4773, repr(_e)[:200]))
    print('FAIL line', 4773, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine()
except BaseException as _e:
    _SOFT.append((4792, repr(_e)[:200]))
    print('FAIL line', 4792, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine()
except BaseException as _e:
    _SOFT.append((4793, repr(_e)[:200]))
    print('FAIL line', 4793, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class ClassMachine(QueueMachine):
    tramp = int(manifest['patch_symbols']['stock_localclass_trampoline'], 16)
    split = []

    def __init__(self, **kw):
        super().__init__(**kw)
        self.word(10723952, 16777232)

    def hook(self, u, address, size, unused):
        if address == syms['toolsQueryDbTable']:
            self.deqs[self.get(syms['tools_pdeq_directory'])][1] = [self.copy('stSongInfo', e) for e in self.split]
            self.returns(len(self.split))
            return
        libc = {syms['strdup@GLIBC_2.0']: 'strdup', 16777232: 'free'}
        if address in libc:
            a = u.reg_read(REGS[0])
            self.returns(self.string(self.text(a)) if libc[address] == 'strdup' else 0)
            return
        if address != self.tramp:
            return super().hook(u, address, size, unused)
        self.calls.append(('stock_localclass', u.reg_read(REGS[0]), 0, 0))
        assert self.get(payload_syms['library_music']) == 1
        self.returns(len(self.items(self.get(syms['p_deque_showlist']))))

def unknown(cls, found, last=-1, artist=0, rows=3):
    m = ClassMachine(page='localclass_page', cls=61441, rows=rows)
    m.found = [m.song('T1')] * found
    m.query = None
    m.word(syms['artist_type'], artist)
    m.word(syms['g_artist_type'], 1 - artist)
    if rows:
        m.word(m.row(rows - 1) + O['REC_ID'], last)
    n = m.call(address=HOOKS['load_localclass_list'][0], args=(cls, 0, 0, 0), gap=0)
    assert m.get(payload_syms['library_music']) == 0
    shown = m.names(m.get(syms['p_deque_showlist']))
    assert n == len(shown) and m.calls[0][:2] == ('stock_localclass', cls) and (m.get(syms['g_artist_type']) == artist)
    return (shown, m.query, m.items(m.get(syms['tools_pdeq_directory'])))

class FirstMachine(ClassMachine):
    dbquery = int(manifest['patch_symbols']['stock_dbquery_trampoline'], 16)

    def __init__(self, untagged, **kw):
        super().__init__(**kw)
        self.mock('strstr@GLIBC_2.0')
        self.read = []
        self.db = sqlite3.connect(':memory:')
        self.db.execute('create table songtable(id,album,songer,albumsonger,composer,style,fileurl)')
        self.db.executemany('insert into songtable values(?,?,?,?,?,?,?)', [(1, 'A', 'B', 'C', 'D', 'E', '/mnt/mmc/Music/a.flac'), (2, '', '', '', '', '', '/mnt/mmc/Audiobooks/b.mp3')] + [(3 + i, '', '', '', '', '', f'/mnt/mmc/Music/{i}.flac') for i in range(untagged)])

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if name.startswith('q:getMusicBy') and (not u.reg_read(REGS[0])):
            tag = {'Album': 'album', 'Songer': 'songer', 'AlbumArtist': 'albumsonger', 'Composer': 'composer', 'Genre': 'style'}[name[12:]]
            sql = self.string(f'select * from songtable where {tag}=""')
            self.returns(self.nested(payload_syms['ringnav_library_query'], self.string('/mnt/data/database.db'), sql, 0, 0))
            return
        if address != self.dbquery:
            return super().hook(u, address, size, unused)
        rows = self.db.execute(self.text(u.reg_read(REGS[1]))).fetchall()
        self.read.append(len(rows))
        self.deqs[self.get(syms['tools_pdeq_directory'])][1] = [self.copy('stSongInfo', self.song(f'S{r[0]}')) for r in rows]
        self.returns(len(rows))
try:
    m = FirstMachine(2, page='localclass_page', cls=61444, rows=3)
except BaseException as _e:
    _SOFT.append((4868, repr(_e)[:200]))
    print('FAIL line', 4868, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def empty_titles(m):
    return [n.get('_es_title') for w, n in m.nodes.items() if n.get('name') == '_empty_state' and n.get('visible', 1) and (not callable(getattr(m, 'alive', None)) or m.alive(w))]

class EmptyClassMachine(ClassMachine):
    """A class list whose empty state opens a page of its own (feedback.c empty_open): window_create and the widget
    factories make nodes, as CoverflowMachine's do."""
    MAKE = ('window_create', 'widget_factory_create_widget', 'list_view_create', 'scroll_view_create')

    def __init__(self, **kw):
        super().__init__(**kw)
        for n in self.MAKE:
            self.handlers[syms[n]] = 'c:' + n
        self.present.add('/tmp/mmc_add')

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')[2:]
        if name not in self.MAKE:
            return super().hook(u, address, size, unused)
        a, b, c, d = [u.reg_read(r) for r in REGS]
        sp = u.reg_read(UC_MIPS_REG_SP)
        self.calls.append((name, a, b, c))
        kind, parent, h = {'window_create': ('window', 0, 0), 'widget_factory_create_widget': (self.text(b), c, self.get(sp + 24))}.get(name, (name.removesuffix('_create'), a, self.get(sp + 16)))
        ret = self.node(kind)
        if kind == 'window':
            self.top = ret
            self.stack.append(ret)
        else:
            self.nodes[parent]['children'].append(ret)
            self.word(ret + O['W_PARENT'], parent)
            self.word(ret + O['W_H'], h)
        if name == 'widget_factory_create_widget':
            for off, v in (('W_X', d), ('W_Y', self.get(sp + 16)), ('W_W', self.get(sp + 20))):
                self.word(ret + O[off], v)
        if kind == 'list_view':
            self.word(ret + O['W_W'], d)
        self.returns(ret, clobber=True)

def empty(cls, page, stage=O['WINDOW_STAGE_OPENED'], leave=False, shown=None):
    m = EmptyClassMachine(page=page, cls=cls, rows=0)
    m.found = []
    m.query = None
    assert m.call(address=HOOKS['load_localclass_list'][0], args=(cls, 0, 0, 0), gap=0) == 0 and len(m.timers) == 1
    m.nodes[m.top]['stage'] = stage
    if shown is not None:
        m.word(syms['g_class_type'], shown)
    if leave:
        m.top = m.node('window', 'localmusic_page')
    m.calls = []
    m.advance(0)
    return m
try:
    closes = lambda m: [c[:3] for c in m.calls if c[0] == 'window_manager_close_window_force']
except BaseException as _e:
    _SOFT.append((4907, repr(_e)[:200]))
    print('FAIL line', 4907, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = empty(61443, 'album_page', stage=2)
except BaseException as _e:
    _SOFT.append((4913, repr(_e)[:200]))
    print('FAIL line', 4913, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.top]['stage'] = O['WINDOW_STAGE_OPENED']
except BaseException as _e:
    _SOFT.append((4916, repr(_e)[:200]))
    print('FAIL line', 4916, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((4916, repr(_e)[:200]))
    print('FAIL line', 4916, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = empty(61443, 'album_page', stage=2)
except BaseException as _e:
    _SOFT.append((4918, repr(_e)[:200]))
    print('FAIL line', 4918, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('window', 'localmusic_page')
except BaseException as _e:
    _SOFT.append((4919, repr(_e)[:200]))
    print('FAIL line', 4919, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((4919, repr(_e)[:200]))
    print('FAIL line', 4919, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = empty(61443, 'album_page', leave=True)
except BaseException as _e:
    _SOFT.append((4921, repr(_e)[:200]))
    print('FAIL line', 4921, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = empty(61444, 'localclass_page', shown=61446)
except BaseException as _e:
    _SOFT.append((4923, repr(_e)[:200]))
    print('FAIL line', 4923, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class SongsMachine(ClassMachine):

    def hook(self, u, address, size, unused):
        if address == syms['getAllMusic']:
            self.calls.append(('getAllMusic', u.reg_read(REGS[0]), self.get(payload_syms['library_music'])))
            self.deqs[self.get(syms['tools_pdeq_directory'])][1] = [self.copy('stSongInfo', e) for e in self.found]
            self.returns(len(self.found))
            return
        if address == syms['music_nagvation_title']:
            self.calls.append(('title', u.reg_read(REGS[0]), self.names(self.get(syms['p_deque_showlist']))))
            self.returns(0)
            return
        super().hook(u, address, size, unused)
try:
    m = SongsMachine(page='localclass_page', cls=61441, rows=3)
except BaseException as _e:
    _SOFT.append((4936, repr(_e)[:200]))
    print('FAIL line', 4936, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.found = [m.song('T1'), m.song('T2')]
except BaseException as _e:
    _SOFT.append((4936, repr(_e)[:200]))
    print('FAIL line', 4936, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    staging = m.get(syms['tools_pdeq_directory'])
except BaseException as _e:
    _SOFT.append((4937, repr(_e)[:200]))
    print('FAIL line', 4937, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.deqs[staging][1] = [m.song('stale')]
except BaseException as _e:
    _SOFT.append((4937, repr(_e)[:200]))
    print('FAIL line', 4937, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((4937, repr(_e)[:200]))
    print('FAIL line', 4937, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class SongCoversMachine(QueueMachine):

    def __init__(self):
        super().__init__(rows=0)
        self.live = set()
        self.result = self.alloc(4)
        self.handlers.pop(syms['albumcoverinfo_init'])
        self.word(10723560, 16777236)
        self.handlers[16777236] = 'c:malloc'
        self.handlers[16777232] = 'c:free'
        self.handlers[syms['strcpy@GLIBC_2.0']] = 'c:strcpy'
        self.mock('pthread_mutex_lock@GLIBC_2.0', 'pthread_mutex_unlock@GLIBC_2.0', 'clear_albumcover_task')
        self.word(syms['pdeq_albumcoverlist'], self.deque([], 'stAlbumCoverInfo'))

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        a, b = (u.reg_read(REGS[0]), u.reg_read(REGS[1]))
        cover = name in ('q:_deque_push_back', 'q:deque_clear') and self.deqs[a][0] == 'stAlbumCoverInfo'
        if not cover and (not name.startswith('c:')):
            return super().hook(u, address, size, unused)
        ret = 0
        if name == 'c:malloc':
            ret = self.alloc(a)
            self.live.add(ret)
        elif name == 'c:free':
            assert not a or a in self.live, 'freed twice'
            self.live.discard(a)
        elif name == 'c:strcpy':
            u.mem_write(a, self.raw(b) + b'\x00')
            ret = a
        elif name == 'q:_deque_push_back':
            e = self.alloc(12)
            u.mem_write(e, bytes(12))
            self.nested(6054812, e, b, self.result)
            self.items(a).append(e)
        else:
            for e in self.items(a):
                self.nested(6052936, e, self.result)
            self.deqs[a][1] = []
        self.returns(ret, clobber=True)
try:
    m = SongCoversMachine()
except BaseException as _e:
    _SOFT.append((4970, repr(_e)[:200]))
    print('FAIL line', 4970, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rows = [m.song(f'Row {i}') for i in range(3)]
except BaseException as _e:
    _SOFT.append((4970, repr(_e)[:200]))
    print('FAIL line', 4970, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = SongCoversMachine()
except BaseException as _e:
    _SOFT.append((4985, repr(_e)[:200]))
    print('FAIL line', 4985, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rows = m.deque([m.song('Sub', kind=4), m.song('Song'), m.song('Skipped', kind=1)])
except BaseException as _e:
    _SOFT.append((4986, repr(_e)[:200]))
    print('FAIL line', 4986, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    covers = m.items(m.get(syms['pdeq_albumcoverlist']))
except BaseException as _e:
    _SOFT.append((4988, repr(_e)[:200]))
    print('FAIL line', 4988, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class ScopedClassMachine(ClassMachine):

    def hook(self, u, address, size, unused):
        super().hook(u, address, size, unused)
        if address == self.tramp and getattr(self, 'failed', False):
            self.returns(-1)
try:
    paths = ['/mnt/mmc/pOdCaStS/Show/episode.mp3', '/mnt/mmc/Music/song.flac', '/mnt/mmc/AUDIOBOOKS/chapter.mp3', '/mnt/mmc/elsewhere/BOOK.M4B', '/mnt/mmc/Podcasts Extra/song.mp3', '/mnt/mmc/Music/Podcasts/song.mp3']
except BaseException as _e:
    _SOFT.append((5004, repr(_e)[:200]))
    print('FAIL line', 5004, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    discovery_paths = ['/mnt/mmc/Discovery/weekly/song.flac', '/mnt/mmc/dIsCoVeRy/track.flac', '/mnt/mmc/Discovery Extra/song.flac', '/mnt/mmc/Music/Discovery/song.flac', '/mnt/mmc/Discovery.flac', '/mnt/mmc/Music/keeper.flac']
except BaseException as _e:
    _SOFT.append((5020, repr(_e)[:200]))
    print('FAIL line', 5020, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = ClassMachine(rows=1)
except BaseException as _e:
    _SOFT.append((5040, repr(_e)[:200]))
    print('FAIL line', 5040, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class PlaylistsMachine(ClassMachine):

    def hook(self, u, address, size, unused):
        super().hook(u, address, size, unused)
        if address == self.tramp:
            self.returns(18)
try:
    m = SongsMachine(page='localclass_page', cls=61441, rows=2)
except BaseException as _e:
    _SOFT.append((5057, repr(_e)[:200]))
    print('FAIL line', 5057, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.found = [m.song('a.flac'), m.song('Row 1')]
except BaseException as _e:
    _SOFT.append((5057, repr(_e)[:200]))
    print('FAIL line', 5057, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = EmptyClassMachine(page='localclass_page', cls=61441, rows=0)
except BaseException as _e:
    _SOFT.append((5073, repr(_e)[:200]))
    print('FAIL line', 5073, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = ClassMachine(page='localclass_page', cls=61441, rows=2)
except BaseException as _e:
    _SOFT.append((5077, repr(_e)[:200]))
    print('FAIL line', 5077, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.split = [m.song('Baroness'), m.song('STP')]
except BaseException as _e:
    _SOFT.append((5078, repr(_e)[:200]))
    print('FAIL line', 5078, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class QueryDone(Exception):
    pass

class LibraryQueryMachine(Machine):

    def __init__(self):
        super().__init__()
        self.mock('strstr@GLIBC_2.0')
        self.handlers.pop(syms['toolsQueryDbTable'])
        self.word(payload_syms['ui_thread'], 1)
        self.db = sqlite3.connect(':memory:')
        self.db.execute('create table songtable(id,album,songer,albumsonger,fileurl,track,cd_num)')
        self.db.executemany('insert into songtable(id,album,songer,albumsonger,fileurl) values(?,?,?,?,?)', [(1, 'Shared', 'Shared', 'Shared', '/mnt/mmc/Audiobooks/chapter.mp3'), (2, 'Shared', 'Shared', 'Shared', '/mnt/mmc/Music/track.flac'), (3, 'Book', 'Narrator', 'Author', '/mnt/mmc/elsewhere/BOOK.M4B'), (4, 'Folder book', 'Reader', 'Writer', '/mnt/mmc/AUDIOBOOKS/book.flac'), (5, 'Podcast', 'Host', 'Host', '/mnt/mmc/Podcasts/show/episode.mp3'), (6, '', '', '', '/mnt/mmc/Audiobooks/untagged.mp3')])
    tramps = {int(manifest['patch_symbols'][f'stock_{x}_trampoline'], 16): x for x in ('dbquery', 'content_query', 'sorted_query')}

    def hook(self, u, address, size, unused):
        if address in (syms['strcpy@GLIBC_2.0'], syms['sprintf@GLIBC_2.0']):
            a, b, *v = [u.reg_read(r) for r in REGS]
            v = iter(v)
            u.mem_write(a, re.sub('%s', lambda _: self.text(next(v)), self.text(b)).encode() + b'\x00')
            self.returns(a)
            return
        if address not in self.tramps:
            return super().hook(u, address, size, unused)
        a, b, c, d = [u.reg_read(r) for r in REGS]
        self.sql = self.text(b)
        if self.tramps[address] == 'content_query':
            self.content = (c, self.text(d), self.get(u.reg_read(UC_MIPS_REG_SP) + 16))
        elif self.tramps[address] == 'dbquery':
            assert self.text(a) == '/mnt/data/database.db' and (c, d) == (123, 1)
        self.results = self.db.execute(self.sql).fetchall()
        if self.tramps[address] == 'content_query':
            raise QueryDone
        self.returns(len(self.results))
try:
    lm = LibraryQueryMachine()
except BaseException as _e:
    _SOFT.append((5111, repr(_e)[:200]))
    print('FAIL line', 5111, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    AA = "ifnull(nullif(albumsonger,''),songer)"
except BaseException as _e:
    _SOFT.append((5111, repr(_e)[:200]))
    print('FAIL line', 5111, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class MusicStageMachine(QueueMachine):

    def hook(self, u, address, size, unused):
        if address == syms['getMusicByAlbum']:
            assert self.get(payload_syms['library_music']) == 1
        return super().hook(u, address, size, unused)

class MusicLoadMachine(QueueMachine):

    def __init__(self):
        super().__init__()
        self.handlers[syms['getAllMusic']] = 'music_load'

    def hook(self, u, address, size, unused):
        if address != syms['getAllMusic']:
            return super().hook(u, address, size, unused)
        assert self.get(payload_syms['library_music']) == 1
        self.returns(self.result)
try:
    lm.sql = None
except BaseException as _e:
    _SOFT.append((5207, repr(_e)[:200]))
    print('FAIL line', 5207, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    lm.scope_tree = {'/mnt/mmc': [('Discovery', 4)], '/mnt/mmc/Discovery': []}
except BaseException as _e:
    _SOFT.append((5213, repr(_e)[:200]))
    print('FAIL line', 5213, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    lm.scope_markers = {'/mnt/mmc/Discovery/database.ignore'}
except BaseException as _e:
    _SOFT.append((5214, repr(_e)[:200]))
    print('FAIL line', 5214, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    queries = ['select id,fileurl from songtable order by id', 'select id,album,fileurl from songtable group by album COLLATE NOCASE', "select id,fileurl from songtable where songer='Artist' order by id"]
except BaseException as _e:
    _SOFT.append((5220, repr(_e)[:200]))
    print('FAIL line', 5220, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    qm = LibraryQueryMachine()
except BaseException as _e:
    _SOFT.append((5237, repr(_e)[:200]))
    print('FAIL line', 5237, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    marker_paths = ['Music/a.flac', 'Singles/a.flac', 'Singles/Keep/a.flac', 'Singles Extra/a.flac', 'Needledrop/a.flac', "Odd ' %_/a.flac", '日本語/a.flac', 'Discovery/a.flac']
except BaseException as _e:
    _SOFT.append((5239, repr(_e)[:200]))
    print('FAIL line', 5239, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    qm.scope_tree = {'/mnt/mmc': [('Singles', 4), ('Needledrop', 4), ("Odd ' %_", 4), ('日本語', 4)], '/mnt/mmc/Singles': [('Keep', 4)], '/mnt/mmc/Singles/Keep': [], '/mnt/mmc/Needledrop': [], "/mnt/mmc/Odd ' %_": [], '/mnt/mmc/日本語': []}
except BaseException as _e:
    _SOFT.append((5243, repr(_e)[:200]))
    print('FAIL line', 5243, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    qm.scope_markers = {p + '/database.ignore' for p in ('/mnt/mmc/Singles', '/mnt/mmc/Needledrop', "/mnt/mmc/Odd ' %_", '/mnt/mmc/日本語')}
except BaseException as _e:
    _SOFT.append((5246, repr(_e)[:200]))
    print('FAIL line', 5246, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def marker_query(sql, scoped=1):
    qm.word(payload_syms['library_music'], scoped)
    qm.call(address=HOOKS['toolsQueryDbTable'][0], args=(qm.string('/mnt/data/database.db'), qm.string(sql), 123, 1), gap=0)
    return {r[0] for r in qm.results}
try:
    art = payload_syms
except BaseException as _e:
    _SOFT.append((5277, repr(_e)[:200]))
    print('FAIL line', 5277, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    buf = m.alloc(256)
except BaseException as _e:
    _SOFT.append((5277, repr(_e)[:200]))
    print('FAIL line', 5277, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    keys = ['Album\x1fBaroness', 'Album\x1fSTP', 'Other']
except BaseException as _e:
    _SOFT.append((5278, repr(_e)[:200]))
    print('FAIL line', 5278, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    covers = [m.alloc(12) for _ in keys]
except BaseException as _e:
    _SOFT.append((5281, repr(_e)[:200]))
    print('FAIL line', 5281, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class ArtistMachine(ClassMachine):
    tramp = int(manifest['patch_symbols']['stock_artist_trampoline'], 16)

    def hook(self, u, address, size, unused):
        if address == syms['albumcoverinfo_init']:
            covers = []
            for r in self.items(u.reg_read(REGS[0])):
                c = self.alloc(96)
                self.word(c, getattr(self, 'cover_flags', 0))
                self.word(c + 4, self.string(self.text(self.get(r + O['REC_ALBUM']))))
                self.word(c + 8, self.get(r + O['REC_PATH']))
                covers.append(c)
            self.word(syms['pdeq_albumcoverlist'], self.deque(covers))
            self.returns(0)
            return
        a, b = (u.reg_read(REGS[0]), u.reg_read(REGS[1]))
        libc = {syms[n + '@GLIBC_2.0']: n for n in ('calloc', 'strdup')} | {16777232: 'free'}
        if address not in libc:
            return super().hook(u, address, size, unused)
        self.returns({'calloc': lambda: self.alloc(a * b + 7 & ~3), 'strdup': lambda: self.string(self.text(a)), 'free': lambda: 0}[libc[address]]())
try:
    SLIDE_HINT = 'htranslate(duration=200)'
except BaseException as _e:
    _SOFT.append((5339, repr(_e)[:200]))
    print('FAIL line', 5339, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class ArtistCoverflow(ArtistMachine):

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.handlers[syms['widget_set_prop_str']] = 'widget_set_prop_str'

    def artist_page(self, name):
        self.top = self.node('window', name)
        self.nodes[self.top]['anim_hint'] = SLIDE_HINT
        self.byte(self.top + 52, 255)

    def veiled(self):
        return self.u.mem_read(self.top + 52, 1)[0] == 0 and self.nodes[self.top]['anim_hint'] == ''

    def shown(self):
        return self.u.mem_read(self.top + 52, 1)[0] == 255 and self.nodes[self.top]['anim_hint'] == SLIDE_HINT

    def hook(self, u, address, size, unused):
        if address != payload_syms['coverflow_artist']:
            return super().hook(u, address, size, unused)
        self.handed.append(len(self.items(u.reg_read(REGS[0]))))
        self.covers = self.names(u.reg_read(REGS[0]))
        self.returns(1)
try:
    m = ArtistCoverflow(page='localclass_page', cls=65281, rows=1)
except BaseException as _e:
    _SOFT.append((5404, repr(_e)[:200]))
    print('FAIL line', 5404, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handed = []
except BaseException as _e:
    _SOFT.append((5404, repr(_e)[:200]))
    print('FAIL line', 5404, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.config['ARTISTCF'] = '1'
except BaseException as _e:
    _SOFT.append((5404, repr(_e)[:200]))
    print('FAIL line', 5404, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.found = []
except BaseException as _e:
    _SOFT.append((5404, repr(_e)[:200]))
    print('FAIL line', 5404, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class CoverQueueMachine(ArtistMachine):

    def __init__(self):
        super().__init__(rows=12)
        self.pending = []
        self.mock('pthread_mutex_lock@GLIBC_2.0', 'pthread_mutex_unlock@GLIBC_2.0')

    def hook(self, u, address, size, unused):
        if address not in (syms['list_clear'], syms['_list_push_back']):
            return super().hook(u, address, size, unused)
        if address == syms['list_clear']:
            self.pending.clear()
        else:
            p = u.reg_read(REGS[1])
            self.pending.append(tuple((self.get(p + i) for i in (0, 4, 8))))
        self.returns(0)
try:
    m = CoverQueueMachine()
except BaseException as _e:
    _SOFT.append((5424, repr(_e)[:200]))
    print('FAIL line', 5424, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def cover_requests(start, big):
    name = 'local_albumcover_task' if big else 'local_albumsmallcover_task'
    m.call(address=syms[name], args=(start, 9, 0, 0), gap=0)
    return [row for row, kind, size in m.pending if kind == 0 and size == big]

def cover_ready(row, big):
    return m.call(address=syms['check_albumcover_flag'], args=(row, big, 0, 0), gap=0)

def cover_done(row, big, key):
    sp = 1879101440
    m.u.mem_write(sp + 5768, key.encode() + b'\x00')
    m.word(sp + 6184, row)
    m.word(sp + 6192, big)
    m.word(sp + 24, 10726112)
    m.u.reg_write(UC_MIPS_REG_SP, sp)
    m.u.reg_write(UC_MIPS_REG_GP, 10726112)
    m.u.emu_start(5211320, 5211336, count=m.budget)
    assert m.u.reg_read(UC_MIPS_REG_PC) == 5211336

def folder_done(row, key, a2):
    sp = 1879101440
    m.u.mem_write(sp + 5768, key.encode() + b'\x00')
    m.word(sp + 6184, row)
    m.word(sp + 24, 10726112)
    m.u.reg_write(UC_MIPS_REG_SP, sp)
    m.u.reg_write(UC_MIPS_REG_GP, 10726112)
    m.u.reg_write(UC_MIPS_REG_S2, 1)
    m.u.reg_write(UC_MIPS_REG_A2, m.string(a2))
    m.u.emu_start(5215476, 5211336, count=m.budget)
    assert m.u.reg_read(UC_MIPS_REG_PC) == 5211336

class ScrollEndMachine(CoverQueueMachine):

    def hook(self, u, address, size, unused):
        if address != syms['toolsGetSysMemFree']:
            return super().hook(u, address, size, unused)
        self.returns(1 << 20)
try:
    m = ScrollEndMachine()
except BaseException as _e:
    _SOFT.append((5483, repr(_e)[:200]))
    print('FAIL line', 5483, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    covers = []
except BaseException as _e:
    _SOFT.append((5487, repr(_e)[:200]))
    print('FAIL line', 5487, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    view = m.node('scroll_view', 'scroll_view_album', yoffset=800)
except BaseException as _e:
    _SOFT.append((5491, repr(_e)[:200]))
    print('FAIL line', 5491, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    win = m.node('window', 'artistinfo_page', [view])
except BaseException as _e:
    _SOFT.append((5491, repr(_e)[:200]))
    print('FAIL line', 5491, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = ClassMachine(page='album_page', cls=61443, rows=2)
except BaseException as _e:
    _SOFT.append((5499, repr(_e)[:200]))
    print('FAIL line', 5499, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from ipod import HOME_TEXT_X, HOME_LIST_W, HOME_OURS, HOME_PAGE, HOME_ROW, HOME_ROWS, HOME_TOP, decode
try:
    cards = [c[2]['name'] for c in decode((B / 'ui' / HOME_PAGE).read_bytes())[3][1][3][0][3]]
except BaseException as _e:
    _SOFT.append((5506, repr(_e)[:200]))
    print('FAIL line', 5506, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    home_hook = HOOKS['home_page_init']
except BaseException as _e:
    _SOFT.append((5508, repr(_e)[:200]))
    print('FAIL line', 5508, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def asset_tree(m, path):
    """A built UI asset as mock widgets, the window's parent the window manager, with what the payload
    reads: type, name, visible, enable, geometry and, for a scroll view, its content height and the
    slidable flags as AWTK sets them. scroll_view_create (0x6042fc) leaves both off, the asset's
    xslidable/yslidable props set them (0x604bc4), and a list_view's layout (0x5fd234) clears x and
    sets y only when the list holds a mobile scroll bar."""

    def build(n, parent, parent_kind='', siblings=()):
        kind, g, props, children = n
        a = m.node(kind, props.get('name', ''), visible=int(props.get('visible') != 'false'))
        m.nodes[a].update(enable=int(props.get('enable') != 'false'), asset=props)
        for off, v in zip(('W_X', 'W_Y', 'W_W', 'W_H'), [0, 0, 375, 290] if kind == 'window' else g):
            m.word(a + O[off], v)
        m.word(a + O['W_PARENT'], parent)
        if kind == 'scroll_view':
            m.byte(a + O['VIEW_HORIZONTAL'], props.get('xslidable') == 'true' and parent_kind != 'list_view')
            m.byte(a + O['VIEW_VERTICAL'], props.get('yslidable') == 'true' or (parent_kind == 'list_view' and 'scroll_bar_m' in siblings))
            m.word(a + O['VIEW_CONTENT_H'], max((c[1][1] + c[1][3] for c in children), default=0))
        m.nodes[a]['children'] = [build(c, a, kind, [c[0] for c in children]) for c in children]
        return a
    return build(decode((B / 'ui' / path).read_bytes()), m.wm)

def named(m, w, name):
    if m.nodes[w]['name'] == name:
        return w
    return next((f for c in m.nodes[w]['children'] if (f := named(m, c, name))), 0)

def click_target(m, w):
    em = m.alloc(4)
    it = m.alloc(40)
    m.word(w + O['W_EMITTER'], em)
    m.word(em, it)
    m.word(it + O['EMIT_TYPE'], O['EVT_CLICK'])

def home_list(m):
    """iPod Home as built (ui/home_page.bin) on top: its scroll view and the rows' tap images. Stock
    binds every image but Coverflow's, which the payload binds. A song is paused (mclGetPlayStatus 3),
    so Now Playing's row shows (coverflow.c np_loaded), and the card's root lists a folder, so Folders
    shows (home.c home_folders): the rows keep HOME_ROWS' order."""
    m.handlers.pop(syms['mclGetPlayStatus'], None)
    m.word(10813572, 3)
    m.stats['/mnt/mmc'] = 1
    m.mock('toolsLoadDirectory', prefix='one:')
    m.top = asset_tree(m, HOME_PAGE)
    imgs = [named(m, m.top, 'img_' + HOME_OURS.get(r, r)) for r in HOME_ROWS]
    cf = HOME_ROWS.index('coverflow')
    for img in imgs[:cf] + imgs[cf + 1:]:
        click_target(m, img)
    return (named(m, m.top, 'scroll_view_home'), imgs)
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((5549, repr(_e)[:200]))
    print('FAIL line', 5549, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(3)
except BaseException as _e:
    _SOFT.append((5549, repr(_e)[:200]))
    print('FAIL line', 5549, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    HINT = 'htranslate'
except BaseException as _e:
    _SOFT.append((5553, repr(_e)[:200]))
    print('FAIL line', 5553, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def under_slide(hint=HINT):
    m = Machine()
    w, es = m.page_list(3)
    m.paint(w)
    assert m.call() == 11 and m.selected(w) == 1
    m.word(w + O['W_PARENT'], m.top)
    m.word(m.top + O['W_PARENT'], m.wm)
    w2, _ = m.page_list(3, name='display_page')
    m.nodes[m.top]['anim_hint'] = hint
    return (m, w, w2)
try:
    m, w, w2 = under_slide()
except BaseException as _e:
    _SOFT.append((5559, repr(_e)[:200]))
    print('FAIL line', 5559, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, w, w2 = under_slide('')
except BaseException as _e:
    _SOFT.append((5561, repr(_e)[:200]))
    print('FAIL line', 5561, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, w, w2 = under_slide()
except BaseException as _e:
    _SOFT.append((5564, repr(_e)[:200]))
    print('FAIL line', 5564, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, w, es = walk(10, 5)
except BaseException as _e:
    _SOFT.append((5568, repr(_e)[:200]))
    print('FAIL line', 5568, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w2, es2 = m.page_list(10, extent=1000)
except BaseException as _e:
    _SOFT.append((5568, repr(_e)[:200]))
    print('FAIL line', 5568, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.top]['anim_hint'] = HINT
except BaseException as _e:
    _SOFT.append((5568, repr(_e)[:200]))
    print('FAIL line', 5568, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((5575, repr(_e)[:200]))
    print('FAIL line', 5575, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    view, imgs = home_list(m)
except BaseException as _e:
    _SOFT.append((5575, repr(_e)[:200]))
    print('FAIL line', 5575, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    last = len(HOME_ROWS) - 1
except BaseException as _e:
    _SOFT.append((5578, repr(_e)[:200]))
    print('FAIL line', 5578, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    shown = min(len(HOME_ROWS), 7)
except BaseException as _e:
    _SOFT.append((5578, repr(_e)[:200]))
    print('FAIL line', 5578, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((5594, repr(_e)[:200]))
    print('FAIL line', 5594, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    view, imgs = home_list(m)
except BaseException as _e:
    _SOFT.append((5594, repr(_e)[:200]))
    print('FAIL line', 5594, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bar = m.node('window', 'system_bar')
except BaseException as _e:
    _SOFT.append((5594, repr(_e)[:200]))
    print('FAIL line', 5594, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    paint_bar = lambda: m.call(address=HOOKS['widget_on_paint_background'][0], args=(bar, m.canvas, 0, 0))
except BaseException as _e:
    _SOFT.append((5595, repr(_e)[:200]))
    print('FAIL line', 5595, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from ipod import SET0

def window(m, name, w):
    m.top = m.node('window', name, [w])
    m.word(w + O['W_PARENT'], m.top)
    m.word(m.top + O['W_PARENT'], m.wm)

def loaded(m):
    return [m.text(c[2]) for c in m.calls if c[0] == 'widget_load_image']
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((5612, repr(_e)[:200]))
    print('FAIL line', 5612, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    view, imgs = home_list(m)
except BaseException as _e:
    _SOFT.append((5612, repr(_e)[:200]))
    print('FAIL line', 5612, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.clip = (0, 0, 375, 320)
except BaseException as _e:
    _SOFT.append((5612, repr(_e)[:200]))
    print('FAIL line', 5612, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.image_loads = []
except BaseException as _e:
    _SOFT.append((5613, repr(_e)[:200]))
    print('FAIL line', 5613, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    CHEV_X = HOME_LIST_W - SET0.right_x - 5
except BaseException as _e:
    _SOFT.append((5614, repr(_e)[:200]))
    print('FAIL line', 5614, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def bar(sel):
    rows = [i for i, r in enumerate(HOME_ROWS) if r != 'shuffle_songs']
    return ([(CHEV_X, i * HOME_ROW + HOME_ROW // 2 - m.get(view + O['SCROLL_Y']), (0, 0, HOME_LIST_W, 7 * HOME_ROW)) for i in rows], ['oled_chevron_w' if i == sel else 'oled_chevron_d' for i in rows])
try:
    m.image_loads = []
except BaseException as _e:
    _SOFT.append((5620, repr(_e)[:200]))
    print('FAIL line', 5620, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.image_loads = []
except BaseException as _e:
    _SOFT.append((5623, repr(_e)[:200]))
    print('FAIL line', 5623, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.image_loads = []
except BaseException as _e:
    _SOFT.append((5624, repr(_e)[:200]))
    print('FAIL line', 5624, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((5627, repr(_e)[:200]))
    print('FAIL line', 5627, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(10, height=96, extent=480, name='playlist_page')
except BaseException as _e:
    _SOFT.append((5627, repr(_e)[:200]))
    print('FAIL line', 5627, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tile = m.entry(w, 0)
except BaseException as _e:
    _SOFT.append((5628, repr(_e)[:200]))
    print('FAIL line', 5628, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from ipod import STATUS_BAR, corner_inset
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((5642, repr(_e)[:200]))
    print('FAIL line', 5642, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bar = asset_tree(m, STATUS_BAR)
except BaseException as _e:
    _SOFT.append((5643, repr(_e)[:200]))
    print('FAIL line', 5643, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    views = [named(m, bar, n) for n in ('view_left', 'view_right')]
except BaseException as _e:
    _SOFT.append((5644, repr(_e)[:200]))
    print('FAIL line', 5644, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def laid_out():
    for v in views:
        native_row_layout(m, v)
    return [(signed(m.get(v + O['W_X'])) + signed(m.get(c + O['W_X'])), m.get(c + O['W_W']), m.get(c + O['W_H'])) for v in views for c in m.nodes[v]['children'] if m.nodes[c]['visible']]
try:
    cells = laid_out()
except BaseException as _e:
    _SOFT.append((5654, repr(_e)[:200]))
    print('FAIL line', 5654, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    inset = corner_inset((30 - 16) // 2)
except BaseException as _e:
    _SOFT.append((5655, repr(_e)[:200]))
    print('FAIL line', 5655, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    title = named(m, bar, 'label_clock')
except BaseException as _e:
    _SOFT.append((5658, repr(_e)[:200]))
    print('FAIL line', 5658, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    x, w = (m.get(title + O['W_X']), m.get(title + O['W_W']))
except BaseException as _e:
    _SOFT.append((5658, repr(_e)[:200]))
    print('FAIL line', 5658, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    left, right = (cells[len(m.nodes[views[0]]['children']) - 1], cells[len(m.nodes[views[0]]['children'])])
except BaseException as _e:
    _SOFT.append((5659, repr(_e)[:200]))
    print('FAIL line', 5659, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from ipod import CLOCK_TEXT, CLOCK_GAP, STATUS_MARGIN, BATT_H_W, corner_x
try:
    batt = [named(m, bar, n) for n in ('img_battery', 'label_battery', 'view_battery')]
except BaseException as _e:
    _SOFT.append((5665, repr(_e)[:200]))
    print('FAIL line', 5665, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    cells = laid_out()
except BaseException as _e:
    _SOFT.append((5667, repr(_e)[:200]))
    print('FAIL line', 5667, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bt = cells[len(m.nodes[views[0]]['children'])]
except BaseException as _e:
    _SOFT.append((5667, repr(_e)[:200]))
    print('FAIL line', 5667, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ink = bt[0] + bt[1] - O['BT_REACH']
except BaseException as _e:
    _SOFT.append((5670, repr(_e)[:200]))
    print('FAIL line', 5670, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.config = {'BATTERY': '0'}
except BaseException as _e:
    _SOFT.append((5676, repr(_e)[:200]))
    print('FAIL line', 5676, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[named(m, bar, 'img_bt')]['visible'] = 0
except BaseException as _e:
    _SOFT.append((5678, repr(_e)[:200]))
    print('FAIL line', 5678, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[named(m, bar, 'img_wifi')]['visible'] = 0
except BaseException as _e:
    _SOFT.append((5679, repr(_e)[:200]))
    print('FAIL line', 5679, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    slot = named(m, bar, 'view_battery')
except BaseException as _e:
    _SOFT.append((5680, repr(_e)[:200]))
    print('FAIL line', 5680, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[named(m, bar, 'label_battery')]['text'] = '50%'
except BaseException as _e:
    _SOFT.append((5681, repr(_e)[:200]))
    print('FAIL line', 5681, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def paint_live_bar():
    m.call(address=HOOKS['widget_on_paint_background'][0], args=(bar, m.canvas, 0, 0), gap=0)
try:
    idle = named(m, bar, 'label_idle')
except BaseException as _e:
    _SOFT.append((5685, repr(_e)[:200]))
    print('FAIL line', 5685, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    state = named(m, bar, 'img_state')
except BaseException as _e:
    _SOFT.append((5685, repr(_e)[:200]))
    print('FAIL line', 5685, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    eq = named(m, bar, 'label_eq')
except BaseException as _e:
    _SOFT.append((5685, repr(_e)[:200]))
    print('FAIL line', 5685, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    clock_box = tuple((m.get(title + O[k]) for k in ('W_X', 'W_Y', 'W_W', 'W_H')))
except BaseException as _e:
    _SOFT.append((5688, repr(_e)[:200]))
    print('FAIL line', 5688, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    shown = (m.nodes[state]['visible'], m.nodes[state].get('image'))
except BaseException as _e:
    _SOFT.append((5691, repr(_e)[:200]))
    print('FAIL line', 5691, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[payload_syms['stock_showface_trampoline']] = 'stock_showface'
except BaseException as _e:
    _SOFT.append((5708, repr(_e)[:200]))
    print('FAIL line', 5708, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[eq]['visible'] = 1
except BaseException as _e:
    _SOFT.append((5709, repr(_e)[:200]))
    print('FAIL line', 5709, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[state]['visible'] = 0
except BaseException as _e:
    _SOFT.append((5709, repr(_e)[:200]))
    print('FAIL line', 5709, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[payload_syms['list_value']] = 'stock_list_value'
except BaseException as _e:
    _SOFT.append((5715, repr(_e)[:200]))
    print('FAIL line', 5715, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def canvas_state(m):
    return (m.lcd_colors(), m.get(m.lcd + O['LCD_TEXT_COLOR']), m.get(m.canvas + O['CANVAS_ALIGN_V']), m.get(m.canvas + O['CANVAS_ALIGN_H']), m.clip)

def letter_machine(virtual, n=40, **config):
    m = Machine()
    m.config.update(config)
    m.word(m.canvas + O['CANVAS_ALIGN_V'], 2)
    m.word(m.canvas + O['CANVAS_ALIGN_H'], 3)
    m.word(m.lcd + O['LCD_TEXT_COLOR'], 287454020)
    if virtual:
        w, rs, es = m.table_page(rebind=True)
        m.word(w + O['TABLE_ROWS'], n)
    else:
        w, es = m.page_list(n, extent=n * 48, name='allmusic_page')
        rs = es
    for e in es:
        m.nodes[e]['text'] = 'row'
    m.paint(w, gap=0)
    return (m, w, rs, es)

def selected_entry(m, w, rs, es):
    return next((e for r, e in zip(rs, es) if (m.get(r + O['ROW_INDEX']) if r != e else es.index(e)) == m.selected(w)))
try:
    box = (120 - O['LETTER_BOX'] // 2, 48 - O['LETTER_BOX'] // 2, O['LETTER_BOX'], O['LETTER_BOX'])
except BaseException as _e:
    _SOFT.append((5745, repr(_e)[:200]))
    print('FAIL line', 5745, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, w, rs, es = letter_machine(False)
except BaseException as _e:
    _SOFT.append((5770, repr(_e)[:200]))
    print('FAIL line', 5770, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[w]['name'] = 'scroll_view_localmusic'
except BaseException as _e:
    _SOFT.append((5770, repr(_e)[:200]))
    print('FAIL line', 5770, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[w]['name'] = ''
except BaseException as _e:
    _SOFT.append((5773, repr(_e)[:200]))
    print('FAIL line', 5773, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from fontTools.ttLib import TTFont
try:
    font = TTFont(B / 'default.ttf')
except BaseException as _e:
    _SOFT.append((5813, repr(_e)[:200]))
    print('FAIL line', 5813, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    advance = lambda s: sum((font['hmtx'][font.getBestCmap()[ord(c)]][0] for c in s)) * 24 / font['head'].unitsPerEm
except BaseException as _e:
    _SOFT.append((5813, repr(_e)[:200]))
    print('FAIL line', 5813, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def hscroll_machine():
    m = Machine()
    m.widget_timers = True
    for name in ('hscroll_label_start', 'hscroll_label_stop', 'hscroll_label_set_xoffset'):
        m.handlers.pop(syms[name])
    m.mock('widget_invalidate')
    return m

def hscroll_label(m, text_w=300, w=200, speed=0.1, delay=0, lull=2000, loop=1, yoyo=0, only_focus=0):
    """Stock's defaults (0x5fb968) after set_hscroll_label_attribute (speed 0.1, lull 2000); range text_w+4-w."""
    t = m.node('hscroll_label')
    m.u.mem_write(t + 120, bytes(56))
    m.word(t + O['W_W'], w)
    m.word(t + O['HSCROLL_TEXT_W'], text_w)
    m.word(t + 156, 4294967295)
    m.word(t + 132, 5000)
    m.word(t + 128, lull)
    m.word(t + O['HSCROLL_DELAY'], delay)
    m.byte(t + O['HSCROLL_LOOP'], loop)
    m.byte(t + O['HSCROLL_YOYO'], yoyo)
    m.byte(t + O['HSCROLL_ONLY_FOCUS'], only_focus)
    m.u.mem_write(t + O['HSCROLL_SPEED'], struct.pack('<f', speed))
    return t

def shown(m, t, over=104):
    if not m.get(t + O['HSCROLL_TIMER']):
        return 'rest'
    x = m.get(t + 144)
    return 'start' if x == 0 else 'end' if x == over else 'moving'

def timeline(m, t, ms):
    """What t shows each ms for ms, as [state, ms] runs."""
    runs = []
    for _ in range(ms):
        m.advance(1)
        v = shown(m, t)
        if runs and runs[-1][0] == v:
            runs[-1][1] += 1
        else:
            runs.append([v, 1])
    return runs
try:
    hold = O['MARQUEE_HOLD_MS']
except BaseException as _e:
    _SOFT.append((5839, repr(_e)[:200]))
    print('FAIL line', 5839, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    scroll = int(104 / struct.unpack('<f', struct.pack('<I', O['HSCROLL_PACE']))[0])
except BaseException as _e:
    _SOFT.append((5839, repr(_e)[:200]))
    print('FAIL line', 5839, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def pass_ok(runs, yoyo=False):
    """Its start held hold (until the second 16 ms tick moves it a whole pixel), a scroll at the pace, its end held hold."""
    assert len(runs) >= 3, runs
    (s, start), (v, moving), (e, end) = runs[:3]
    assert (s, v, e) == ('start', 'moving', 'end') and hold <= start < hold + 40 and (abs(moving + start + end - 2 * hold - scroll) < 40), runs
    assert end == hold + 16 * yoyo, runs
try:
    seen = []
except BaseException as _e:
    _SOFT.append((5848, repr(_e)[:200]))
    print('FAIL line', 5848, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = hscroll_machine()
except BaseException as _e:
    _SOFT.append((5856, repr(_e)[:200]))
    print('FAIL line', 5856, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    t = hscroll_label(m, speed=0.02)
except BaseException as _e:
    _SOFT.append((5856, repr(_e)[:200]))
    print('FAIL line', 5856, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    runs = timeline(m, t, hold + 5300)
except BaseException as _e:
    _SOFT.append((5857, repr(_e)[:200]))
    print('FAIL line', 5857, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = hscroll_machine()
except BaseException as _e:
    _SOFT.append((5859, repr(_e)[:200]))
    print('FAIL line', 5859, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    t = hscroll_label(m, yoyo=1)
except BaseException as _e:
    _SOFT.append((5859, repr(_e)[:200]))
    print('FAIL line', 5859, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    runs = timeline(m, t, 2 * (hold + scroll) + 2 * hold + 100)
except BaseException as _e:
    _SOFT.append((5860, repr(_e)[:200]))
    print('FAIL line', 5860, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = hscroll_machine()
except BaseException as _e:
    _SOFT.append((5863, repr(_e)[:200]))
    print('FAIL line', 5863, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    t = hscroll_label(m, only_focus=1)
except BaseException as _e:
    _SOFT.append((5863, repr(_e)[:200]))
    print('FAIL line', 5863, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from build import HSCROLL_ONCE
from build import HSCROLL_GAP, ALBUM_LABELS, FOCUS_RESTORE

def named_near(at):
    """The names stock gives widgets within 0x30 bytes of at: addiu $a1, $a1, a string's low half."""
    words = struct.unpack_from('<25I', demo, fileoff(demo, at - 48))
    return {demo[o:demo.index(b'\x00', o)].decode() for o in (fileoff(demo, 7864320 + signed16(w & 65535)) for w in words if w >> 16 == 9381)}

def signed16(x):
    return x - 65536 if x & 32768 else x

def falls(m, start, stops):
    sp = m.alloc(256)
    m.word(sp + 16, 10726112)
    reached = []
    for r, v in ((UC_MIPS_REG_SP, sp), (UC_MIPS_REG_GP, 10726112), (UC_MIPS_REG_V0, m.alloc(64)), (UC_MIPS_REG_S0, 7)):
        m.u.reg_write(r, v)

    def stop(u, address, size, unused):
        if address in stops:
            reached.append(address)
            u.emu_stop()
    hook = m.u.hook_add(UC_HOOK_CODE, stop)
    m.u.emu_start(start, 16777216, count=40)
    m.u.hook_del(hook)
    return (reached[0], signed(m.u.reg_read(UC_MIPS_REG_S0)))

def marquee_list(over=True, only_focus=1):
    m = hscroll_machine()
    m.marquee = True
    w, es = m.page_list(4, height=192, extent=192, name='folder_page')
    labels = []
    for e in es:
        t = hscroll_label(m, text_w=300 if over else 150, loop=0, only_focus=only_focus)
        m.nodes[e]['children'] = [t]
        m.word(t + O['W_PARENT'], e)
        labels.append(t)
    m.call()
    m.paint(w, gap=0)
    return (m, w, labels)
try:
    running = lambda m, labels: [t for t in labels if m.get(t + O['HSCROLL_TIMER'])]
except BaseException as _e:
    _SOFT.append((5925, repr(_e)[:200]))
    print('FAIL line', 5925, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    focus_only = lambda m, t: m.u.mem_read(t + O['HSCROLL_ONLY_FOCUS'], 1)[0]
except BaseException as _e:
    _SOFT.append((5926, repr(_e)[:200]))
    print('FAIL line', 5926, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, w, labels = marquee_list()
except BaseException as _e:
    _SOFT.append((5927, repr(_e)[:200]))
    print('FAIL line', 5927, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sel = m.selected(w)
except BaseException as _e:
    _SOFT.append((5927, repr(_e)[:200]))
    print('FAIL line', 5927, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    runs = timeline(m, labels[sel], O['MARQUEE_PASSES'] * (hold + scroll + hold) + 100)
except BaseException as _e:
    _SOFT.append((5928, repr(_e)[:200]))
    print('FAIL line', 5928, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, w, labels = marquee_list()
except BaseException as _e:
    _SOFT.append((5933, repr(_e)[:200]))
    print('FAIL line', 5933, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    on = running(m, labels)
except BaseException as _e:
    _SOFT.append((5933, repr(_e)[:200]))
    print('FAIL line', 5933, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = hscroll_machine()
except BaseException as _e:
    _SOFT.append((5940, repr(_e)[:200]))
    print('FAIL line', 5940, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.marquee = True
except BaseException as _e:
    _SOFT.append((5940, repr(_e)[:200]))
    print('FAIL line', 5940, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, rs, es = m.table_page(rebind=False)
except BaseException as _e:
    _SOFT.append((5940, repr(_e)[:200]))
    print('FAIL line', 5940, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    labels = {}
except BaseException as _e:
    _SOFT.append((5940, repr(_e)[:200]))
    print('FAIL line', 5940, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    on = running(m, labels.values())
except BaseException as _e:
    _SOFT.append((5943, repr(_e)[:200]))
    print('FAIL line', 5943, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    first = m.selected(w)
except BaseException as _e:
    _SOFT.append((5944, repr(_e)[:200]))
    print('FAIL line', 5944, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, w, rs, es = letter_machine(False)
except BaseException as _e:
    _SOFT.append((5947, repr(_e)[:200]))
    print('FAIL line', 5947, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((5952, repr(_e)[:200]))
    print('FAIL line', 5952, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(40, extent=40 * 48, name='folder_page')
except BaseException as _e:
    _SOFT.append((5952, repr(_e)[:200]))
    print('FAIL line', 5952, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((5957, repr(_e)[:200]))
    print('FAIL line', 5957, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(40, extent=40 * 48, name='playset_page')
except BaseException as _e:
    _SOFT.append((5957, repr(_e)[:200]))
    print('FAIL line', 5957, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    WRITERS = {'scanAllMusicFile': 'stock_scan_all', 'scanSpecFolder': 'stock_scan_folder', 'deleteMusicFromMusicDb': 'stock_delete_song'}
except BaseException as _e:
    _SOFT.append((5962, repr(_e)[:200]))
    print('FAIL line', 5962, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class CoverflowMachine(QueueMachine):
    FREE = 16777232

    def __init__(self, albums=3, cached=True, **queue):
        super().__init__(rows=4, **queue)
        self.cached = cached
        self.missing = False
        self.joins = []
        self.threads = []
        self.plays = []
        self.homes = 0
        self.freed = 0
        self.live = {}
        for n in ('window_create', 'widget_factory_create_widget', 'image_base_set_image', 'getAllAlbum', 'list_view_create', 'scroll_view_create', 'navigator_back_to_home', 'navigator_to_with_context', 'access@GLIBC_2.0', 'calloc@GLIBC_2.0', 'strdup@GLIBC_2.0', 'mkdir@GLIBC_2.0', 'statfs@GLIBC_2.0', 'pthread_create@GLIBC_2.2', 'pthread_join@GLIBC_2.0', 'qsort@GLIBC_2.0', 'toolsQueryDbTable'):
            self.handlers[syms[n]] = 'c:' + n
        self.word(10723952, self.FREE)
        self.handlers[self.FREE] = 'c:free'
        self.handlers[home_hook[0] + 12] = 'stock_home'
        for n, stub in WRITERS.items():
            self.handlers[HOOKS[n][0] + 12] = stub
        self.albums = [self.song('T0') for _ in range(albums)]
        for i, r in enumerate(self.albums):
            self.word(r + O['REC_ALBUM'], self.string(f'Album {i}'))
        self.found = [self.song('T1'), self.song('T2')]

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('c:'):
            return super().hook(u, address, size, unused)
        assert u.reg_read(UC_MIPS_REG_T9) == address, name
        name = name[2:].split('@')[0]
        a, b, c, d = [u.reg_read(r) for r in REGS]
        sp = u.reg_read(UC_MIPS_REG_SP)
        ret = 0
        self.calls.append((name, a, b, c))
        if name in ('window_create', 'widget_factory_create_widget', 'list_view_create', 'scroll_view_create'):
            kind, parent, h = {'window_create': ('window', 0, 0), 'widget_factory_create_widget': (self.text(b), c, self.get(sp + 24))}.get(name, (name.removesuffix('_create'), a, self.get(sp + 16)))
            ret = self.node(kind)
            if kind == 'window':
                self.top = ret
                self.stack.append(ret)
            else:
                self.nodes[parent]['children'].append(ret)
                self.word(ret + O['W_PARENT'], parent)
                self.word(ret + O['W_H'], h)
            if name == 'widget_factory_create_widget':
                for off, v in (('W_X', d), ('W_Y', self.get(sp + 16)), ('W_W', self.get(sp + 20))):
                    self.word(ret + O[off], v)
            if kind == 'list_view':
                self.word(ret + O['W_W'], d)
            if kind == 'slide_menu':
                self.word(ret + O['SLIDE_INDEX'], 0)
                self.word(ret + 92, self.alloc())
        elif name == 'image_base_set_image':
            self.nodes[a]['image'] = self.text(b)
        elif name in ('toolsGetMusicInfo', 'stock_music_info') and getattr(self, 'music_tags', None) is not None:
            t = self.music_tags.get(self.text(c))
            self.calls.append(('toolsGetMusicInfo', self.text(c)))
            if t:
                for off, v in zip((640, 256, 0, 1408), t[:4]):
                    u.mem_write(b + off, v.encode() + b'\x00')
                self.word(b + 1668, t[4])
                self.word(b + 1672, t[5])
            ret = int(bool(t))
        elif name == 'getAllAlbum':
            self.deqs[self.get(syms['tools_pdeq_directory'])][1] = [self.copy('stSongInfo', e) for e in self.albums]
            ret = len(self.albums)
        elif name == 'navigator_back_to_home':
            self.homes += 1
        elif name == 'navigator_to_with_context':
            if self.text(a) == 'playing_page':
                self.play_names = self.names(self.get(b))
                self.play_recs = list(self.items(self.get(b)))
                field = lambda e, k: self.text(self.get(e + O[k])) if self.get(e + O[k]) else ''
                self.play_tags = [(field(e, 'REC_TITLE'), field(e, 'REC_ARTIST'), field(e, 'REC_ALBUM'), field(e, 'REC_ALBUM_ARTIST'), self.get(e + O['REC_TRACK']), self.get(e + O['REC_DISC'])) for e in self.items(self.get(b))]
                self.plays.append((self.text(a), *[signed(self.get(b + 4 * i)) for i in range(4)]))
                self.natural = self.get(payload_syms['press_natural'])
                if getattr(self, 'stock_load', 0):
                    self.nested(syms['mclLoadPlayList'], self.get(b), self.get(b + 4), self.get(b + 8), 0)
            elif self.text(a) == 'dialog/msginfo_dialog':
                self.toasts.append((self.text(a), self.get(b), self.get(b + 4), self.text(b + 8)))
                if self.on_toast:
                    self.on_toast()
            else:
                self.opened.append((self.text(a), self.get(b), self.get(b + 4)))
                self.top = self.node('window', self.text(a))
                self.stack.append(self.top)
                if self.on_open:
                    self.on_open(self.text(a))
        elif name == 'access':
            path = self.text(a)
            ret = 0 if (self.cached if '/mnt/mmc/.coverflow/' in path else not self.missing) else -1
        elif name == 'toolsQueryDbTable':
            rows = self.albums if 'fileurl not like' in self.text(b) else []
            self.deqs[self.get(syms['tools_pdeq_directory'])][1] = [self.copy('stSongInfo', e) for e in rows]
            ret = len(rows)
        elif name == 'qsort':
            self.qsort(a, b, c, d)
        elif name == 'calloc':
            ret = self.alloc(a * b + 4)
            self.live[ret] = a * b
        elif name == 'strdup':
            ret = self.string(self.text(a))
        elif name == 'free':
            self.freed += a != 0
            self.live.pop(a, 0)
            if a in getattr(self, 'poison', {}):
                self.u.mem_write(a, b'\x00' * self.poison.pop(a))
        elif name == 'statfs':
            self.word(b + 4, 4096)
            self.word(b + 28, 1 << 20 if getattr(self, 'room', True) else 1024)
        elif name == 'pthread_create':
            self.word(a, 77)
            self.threads.append((c, d))
        elif name == 'pthread_join':
            self.joins.append(a)
        self.returns(ret, clobber=True)

    def open(self):
        """Home with its Cover Flow row selected: centre confirms its image, whose click opens Coverflow."""
        cf = HOME_ROWS.index('coverflow')
        view, self.imgs = home_list(self)
        self.img = self.imgs[cf]
        self.home = self.top
        self.stack = [self.top]
        self.art = named(self, self.top, 'img_homeart')
        self.list = named(self, self.top, 'list_view_home')
        assert self.call(address=home_hook[0], args=(self.top, 0, 0, 0), gap=0) == 0
        self.paint(view)
        self.nodes[view]['_ringnav_index'] = cf
        self.clicks = []
        assert self.confirm() == 11 and self.clicks == [self.img]
        f, ctx = self.handler(self.img, O['EVT_CLICK'])
        assert self.call(address=f, args=(ctx, self.event, 0, 0), gap=0) == 0
        self.page = self.top
        self.slide = self.find('slide_menu')
        return self.page

    def find(self, kind, w=None):
        w = w or self.page
        if self.nodes[w]['type'] == kind:
            return w
        return next((f for c in self.nodes[w]['children'] if (f := self.find(kind, c))), 0)

    def texts(self, kind='hscroll_label'):
        return [self.nodes[w].get('text') for w in self.nodes if self.nodes[w]['type'] == kind and self.alive(w)]

    def tracks(self, album=0):
        f, ctx = self.handler(self.nodes[self.slide]['children'][album], O['EVT_CLICK'])
        self.call(address=f, args=(ctx, self.event, 0, 0), gap=0)
        self.advance(0)
        view = self.find('scroll_view')
        self.paint(view)
        return view

    def rescan(self, writer='scanAllMusicFile'):
        """A songtable writer runs: its hook, then its (mocked) stock body. A scan walks the folder
        markers into SCOPE_RULES first and indexes songtable after it, a delete doesn't."""
        assert self.call(address=HOOKS[writer][0], args=(1, 2, 0, 0), gap=0) == 0
        assert [c[:3] for c in self.calls if c[0].startswith('stock_')] == [(WRITERS[writer], 1, 2)]
        walked = [c for c in self.calls if c[0].startswith('fopen') and self.text(c[1]) == SCOPE_RULES + '.tmp']
        assert bool(walked) == (writer != 'deleteMusicFromMusicDb')
        body = [c[0] for c in self.calls].index(WRITERS[writer])
        indexed = [self.text(c[1]) for c in self.calls if c[0] == 'toolsUpdateDbTable']
        assert indexed == [self.text(c[1]) for c in self.calls[body:] if c[0] == 'toolsUpdateDbTable']
        assert indexed == ([] if writer == 'deleteMusicFromMusicDb' else ['/mnt/data/database.db']) * (2 if writer == 'scanAllMusicFile' and getattr(self, 'room', True) else 1)
        sql = [self.text(c[2]) for c in self.calls if c[0] == 'toolsUpdateDbTable']
        assert all(('songtable_album on' in q and 'songtable_group' not in q for q in sql[-1:]))
        assert [q.count('create index if not exists songtable_') for q in sql[:-1]] == [3] * (len(sql) - 1)
        assert all((q.index('songtable_group') < q.index('songtable_album_exact') < q.index('songtable_artist') for q in sql[:-1]))

    def queried(self):
        return sum((c[0] == 'getAllAlbum' for c in self.calls))

    def close(self):
        f, ctx = self.handler(self.page, O['EVT_DESTROY'])
        self.call(address=f, args=(ctx, self.event, 0, 0), gap=0)

    def alive(self, w):
        """Still attached below the page (the destroy_children mock only unlinks)."""
        while w != self.page:
            parent = self.get(w + O['W_PARENT'])
            if parent not in self.nodes or w not in self.nodes[parent]['children']:
                return False
            w = parent
        return True

    def key(self, k=O['KEY_RETURN'], run=True):
        f, ctx = self.handler(self.page, O['EVT_KEY_UP'])
        ev = self.alloc(64)
        self.word(ev, O['EVT_KEY_UP'])
        self.word(ev + O['EVENT_KEY'], k)
        ret = self.call(address=f, args=(ctx, ev, 0, 0), gap=0)
        if run:
            self.advance(0)
        return ret

def position_checks():
    for theme_id in ('3',):
        for light in ('0', '1'):

            def position_setup(machine):
                machine.config.update(THEME=theme_id, LIGHT=light, CFPOSITION='1')
            SETUP[:] = [position_setup]
            try:
                m = CoverflowMachine()
            finally:
                SETUP.clear()
            m.open()
            covers = m.get(m.slide + O['W_PARENT'])
            paint_position = lambda w: m.call(address=payload_syms['coverflow_position_paint'], args=(w, m.canvas, 0, 0), gap=0)
            paint_position(m.slide)
            xs = []
            for index in (1, 2, 0):
                m.word(m.slide + O['SLIDE_INDEX'], index)
                m.bands = []
                paint_position(m.slide)
                paint_position(covers)
                assert len(m.bands) == 5, (theme_id, light, m.bands)
                track, *thumb = m.bands
                labels = [w for w in m.nodes[covers]['children'] if m.nodes[w]['type'] == 'hscroll_label']
                bottom = max((m.get(w + O['W_Y']) + m.get(w + O['W_H']) for w in labels))
                assert track[:4] == (107, (bottom + m.get(covers + O['W_H'])) // 2 - 1, 160, 2), (theme_id, track, bottom)
                xs.append(min((b[0] for b in thumb)))
                assert len(thumb) == 4 and [b[2:4] for b in thumb] == [(7, 1), (9, 1), (9, 1), (7, 1)]
                assert 2 * track[1] + track[3] == thumb[0][1] + thumb[-1][1] + 1
            assert xs[1] > xs[0] > xs[2], (theme_id, light, xs)
            passed()

class PlaylistEditMachine(CoverflowMachine):

    def __init__(self, count=3):
        super().__init__()
        self.db = sqlite3.connect(':memory:', isolation_level=None)
        self.db.execute('create table playlist(id integer primary key,cd_num,fileurl,time,time_end,filename)')
        self.db.execute("insert into playlist values(0,0,'',0,0,'Mix')")
        self.db.executemany('insert into playlist values(?,?,?,?,?,?)', [(101 + i, 0, '/p/' + x, 0, 0, x) for i, x in enumerate('ABC'[:count])])
        self.db.execute("insert into playlist values(200,1,'/p/other',0,0,'Other')")
        self.fail = ''
        self.sql = []
        self.closed_db = 0
        for n in ('sqlite3_open', 'sqlite3_close', 'sqlite3_exec', 'sort_playlist_addtime', 'getAllMusic', 'checkPlayListExist', 'batch_add_file', 'navigator_back', '_create_vector', 'vector_init', '_vector_push_back'):
            self.handlers[syms[n]] = 'edit:' + n
        self.handlers[int(manifest['patch_symbols']['stock_playlistsmore_trampoline'], 16)] = 'edit:more'
        r = self.song('Mix', 4)
        self.word(r + O['REC_TITLE'], self.string('Mix'))
        self.word(r + O['REC_DISC'], 0)
        self.word(syms['p_deque_playlist'], self.deque([r]))
        self.before_queue = self.names()

    def record(self, row):
        ident, dest, path, start, end, name = row
        r = self.song(name)
        self.word(r + O['REC_ID'], ident)
        self.word(r + O['REC_DISC'], dest)
        self.word(r + O['REC_PATH'], self.string(path))
        self.word(r + O['REC_CUE_START'], start)
        self.word(r + O['REC_CUE_END'], end)
        return r

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('edit:'):
            return super().hook(u, address, size, unused)
        a, b, c, d = [u.reg_read(r) for r in REGS]
        name = name[5:]
        ret = 0
        if name == 'sqlite3_open':
            self.word(b, 123)
            ret = 1 if self.fail == 'open' else 0
        elif name == 'sqlite3_close':
            self.closed_db += 1
        elif name == 'sqlite3_exec':
            assert a == 123
            try:
                for sql in self.text(b).split(';'):
                    self.sql.append(sql)
                    if self.fail and sql.startswith(self.fail):
                        raise sqlite3.OperationalError('injected write failure')
                    if re.search('\\bhaving\\b', sql, re.I) and (not re.search('\\bgroup\\s+by\\b', sql, re.I)):
                        raise sqlite3.OperationalError('a GROUP BY clause is required before HAVING')
                    cur = self.db.execute(sql)
                    if cur.description and c:
                        for row in cur.fetchall():
                            values = self.alloc(4 * len(row))
                            names = self.alloc(4 * len(row))
                            for i, v in enumerate(row):
                                self.word(values + 4 * i, 0 if v is None else self.string(str(v)))
                            if self.nested(c, d, len(row), values, names):
                                raise sqlite3.OperationalError('callback aborted')
            except sqlite3.Error:
                ret = 1
        elif name == '_create_vector':
            assert self.text(a) == 'int'
            ret = self.alloc(64)
        elif name == '_vector_push_back':
            self.word(a, b)
        elif name == 'vector_init':
            pass
        elif name == 'getAllMusic':
            self.deqs[self.get(syms['tools_pdeq_directory'])][1] = [self.copy('stSongInfo', r) for r in self.found]
            ret = len(self.found)
        elif name == 'sort_playlist_addtime':
            rows = self.db.execute('select * from playlist where id>100 and cd_num=? order by id', (a,)).fetchall()
            self.deqs[self.get(syms['tools_pdeq_directory'])][1] = [self.record(row) for row in rows]
            ret = len(rows)
        elif name == 'checkPlayListExist':
            ret = self.db.execute('select count(*) from playlist where id>100 and cd_num=? and fileurl=? and time=?', (b, self.text(self.get(a + O['REC_PATH'])), self.get(a + O['REC_CUE_START']))).fetchone()[0]
        elif name == 'batch_add_file':
            assert (a, b) == (61441, 61451) and d != self.get(syms['p_vector_select_record']) and (self.get(d) == 1)
            dest = self.get(u.reg_read(UC_MIPS_REG_SP) + 16)
            r = self.items(c)[0]
            if not self.fail:
                ident = self.db.execute('select max(id)+1 from playlist').fetchone()[0]
                self.db.execute('insert into playlist values(?,?,?,?,?,?)', (ident, dest, self.text(self.get(r + O['REC_PATH'])), self.get(r + O['REC_CUE_START']), self.get(r + O['REC_CUE_END']), self.text(self.get(r + O['REC_NAME']))))
            ret = 1
        elif name == 'navigator_back':
            self.nested(*self.handler(self.page, O['EVT_DESTROY']), self.event)
            self.stack.remove(self.page)
            self.top = self.stack[-1]
        self.returns(ret, clobber=True)

    def open_editor(self, h=72, inner=68):
        more = self.node('window', 'playlistsmore_page')
        self.word(more + O['W_PARENT'], self.wm)
        self.top = more
        self.stack.append(more)
        deleted = self.node('hscroll_label', name='label_del')
        self.word(deleted + O['W_PARENT'], more)
        self.nodes[more]['children'].append(deleted)
        self.call(address=HOOKS['localmusic_playlistsmore_page_init'][0], args=(more, 0, 0, 0), gap=0)
        assert self.nodes[deleted]['text'] == 'Delete Playlist'
        row = self.nodes[more]['children'][-1]
        button = self.nodes[row]['children'][0]
        image, label = self.nodes[button]['children']
        y = 10 + 2 * h
        assert [self.get(row + O[k]) for k in ('W_X', 'W_Y', 'W_W', 'W_H')] == [0, y, 375, h]
        assert self.nodes[row]['style'] == 's_listitem_black'
        assert [self.get(button + O[k]) for k in ('W_X', 'W_Y', 'W_W', 'W_H')] == [20, 0, 335, inner]
        assert self.nodes[button]['style'] == 's_btn_listitem'
        assert [self.get(image + O[k]) for k in ('W_X', 'W_Y', 'W_W', 'W_H')] == [10, 0, 52, inner]
        assert self.nodes[image]['image'] == 'rename' and self.nodes[image]['draw_type'] == O['IMAGE_DRAW_CENTER']
        assert [self.get(label + O[k]) for k in ('W_X', 'W_Y', 'W_W', 'W_H')] == [72, 0, 253, inner]
        assert self.nodes[label]['style'] == 's_scrlabel_white20l' and self.nodes[label]['text'] == 'Edit Playlist'
        f, ctx = self.handler(button, O['EVT_CLICK'])
        self.call(address=f, args=(ctx, self.event, 0, 0), gap=0)
        self.page = self.top
        self.view = self.find('scroll_view')
        self.paint(self.view)
        assert self.nodes[self.page]['name'] == 'playlistedit_page'

    def showlabels(self):
        self.view = self.find('scroll_view')
        return self.labels()

    def ticks(self):
        return [any((self.nodes[c].get('image') == 'select' for c in self.nodes[r]['children'])) for r in self.nodes[self.view]['children']]

    def choose(self, i):
        self.view = self.find('scroll_view')
        self.pick(i)
        self.advance(0)
        self.view = self.find('scroll_view')

    def move(self, key=O['KEY_FWD_BTN']):
        assert self.call(key, address=HOOKS['on_wm_keydown_before_fun'][0], event_type=O['EVT_KEY_DOWN_BEFORE'], gap=0) == 11
        assert self.call(key, gap=0) == 11
        self.view = self.find('scroll_view')

    def order(self, dest=0):
        return [r[0] for r in self.db.execute('select filename from playlist where id>100 and cd_num=? order by id', (dest,))]

    def mutate(self, a=101, b=102, dest=0):
        records = [self.record(self.db.execute('select * from playlist where id=?', (i,)).fetchone()) if i else 0 for i in (a, b)]
        return self.call(address=payload_syms['ple_mutate'], args=(dest, *records, 0), gap=0)
try:
    m = PlaylistEditMachine()
except BaseException as _e:
    _SOFT.append((6226, repr(_e)[:200]))
    print('FAIL line', 6226, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    more = m.node('window', 'playlistsmore_page')
except BaseException as _e:
    _SOFT.append((6226, repr(_e)[:200]))
    print('FAIL line', 6226, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = more
except BaseException as _e:
    _SOFT.append((6227, repr(_e)[:200]))
    print('FAIL line', 6227, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.on_click = lambda w, e: m.nested(*m.handler(w, O['EVT_CLICK']), e)
except BaseException as _e:
    _SOFT.append((6232, repr(_e)[:200]))
    print('FAIL line', 6232, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistEditMachine()
except BaseException as _e:
    _SOFT.append((6236, repr(_e)[:200]))
    print('FAIL line', 6236, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.on_click = lambda w, e: m.nested(*m.handler(w, O['EVT_CLICK']), e)
except BaseException as _e:
    _SOFT.append((6238, repr(_e)[:200]))
    print('FAIL line', 6238, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistEditMachine()
except BaseException as _e:
    _SOFT.append((6243, repr(_e)[:200]))
    print('FAIL line', 6243, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    saved = m.alloc(16)
except BaseException as _e:
    _SOFT.append((6264, repr(_e)[:200]))
    print('FAIL line', 6264, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fail = 'write'
except BaseException as _e:
    _SOFT.append((6270, repr(_e)[:200]))
    print('FAIL line', 6270, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fail = ''
except BaseException as _e:
    _SOFT.append((6270, repr(_e)[:200]))
    print('FAIL line', 6270, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def ple_search(m, text, event='EVT_IM_ACTION'):
    m.choose(0)
    edit = m.find('edit')
    assert m.nodes[edit]['name'] == 'search_edit' and m.nodes[edit]['focused'] == 1
    assert m.get(edit + O['W_Y']) + m.get(edit + O['W_H']) <= 30 and (not m.find('scroll_view'))
    m.nodes[edit]['text'] = text
    f, ctx = m.handler(edit, O[event])
    assert m.call(address=f, args=(ctx, m.event, 0, 0), gap=0) == 0
    m.advance(0)
    m.view = m.find('scroll_view')
try:
    m = PlaylistEditMachine()
except BaseException as _e:
    _SOFT.append((6284, repr(_e)[:200]))
    print('FAIL line', 6284, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.find('edit')]['text'] = 'beat'
except BaseException as _e:
    _SOFT.append((6289, repr(_e)[:200]))
    print('FAIL line', 6289, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.find('edit'), O['EVT_IM_ACTION'])
except BaseException as _e:
    _SOFT.append((6289, repr(_e)[:200]))
    print('FAIL line', 6289, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.view = m.find('scroll_view')
except BaseException as _e:
    _SOFT.append((6290, repr(_e)[:200]))
    print('FAIL line', 6290, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.find('edit')]['text'] = 't1'
except BaseException as _e:
    _SOFT.append((6297, repr(_e)[:200]))
    print('FAIL line', 6297, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class PlaylistLifecycleMachine(PlaylistEditMachine):

    def __init__(self):
        super().__init__()
        self.db.execute('drop table playlist')
        self.db.execute('create table playlist(id integer primary key,filename,fileurl,album,songer,style,name,composer,albumsonger,year,track,cd_num,time,time_end,time_create,Initial,bitdepth,samprate)')
        for ident, name in enumerate(('Zulu', 'Alpha', 'Echo', 'Bravo', 'Delta', 'Charlie')):
            self.db.execute('insert into playlist(id,filename,name,cd_num) values(?,?,?,?)', (ident, name, name, ident))
        self.db.execute("insert into playlist(id,filename,cd_num) values(101,'song',0),(102,'other',1)")
        records = []
        for ident, name in self.db.execute('select id,name from playlist where id<=100 order by name'):
            r = self.song(name, 4)
            self.word(r + O['REC_ID'], ident)
            self.word(r + O['REC_DISC'], ident)
            records.append(r)
        self.word(syms['p_deque_playlist'], self.deque(records))
        self.more = self.node('window', 'playlistsmore_page')
        self.nodes[self.more]['_playlist'] = 5
        self.answer = 10
        self.backs = 0
        self.handlers[syms['navigator_back']] = 'life:back'

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if name == 'life:back':
            self.backs += 1
            self.returns(0)
            return
        if name == 'c:navigator_to_with_context' and self.text(u.reg_read(REGS[0])) == 'dialog/confirminfo_dialog':
            assert self.text(u.reg_read(REGS[1]) + 8) == 'Delete Playlist?'
            self.returns(self.answer)
            return
        super().hook(u, address, size, unused)

    def create(self, name):
        return signed(self.call(address=HOOKS['createPlayListItem'][0], args=(self.get(syms['p_deque_playlist']), self.string(name), 0, 0), gap=0))

    def delete(self):
        return self.call(address=HOOKS['playlist_delete_click'][0], args=(self.more, 0, 0, 0), gap=0)

    def reload_pending(self):
        self.word(syms['g_class_type'], 61451)
        self.u.reg_write(UC_MIPS_REG_GP, 10726112)
        reached = []

        def stop(u, address, size, unused):
            if address in (4938144, 4938156):
                reached.append(address)
            if address == 4938156:
                u.emu_stop()
        hook = self.u.hook_add(UC_HOOK_CODE, stop)
        self.u.emu_start(4938100, 16777216, count=100)
        self.u.hook_del(hook)
        assert reached[-1] == 4938156
        return reached[0] == 4938144
try:
    m = PlaylistLifecycleMachine()
except BaseException as _e:
    _SOFT.append((6340, repr(_e)[:200]))
    print('FAIL line', 6340, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistLifecycleMachine()
except BaseException as _e:
    _SOFT.append((6345, repr(_e)[:200]))
    print('FAIL line', 6345, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistLifecycleMachine()
except BaseException as _e:
    _SOFT.append((6350, repr(_e)[:200]))
    print('FAIL line', 6350, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistLifecycleMachine()
except BaseException as _e:
    _SOFT.append((6363, repr(_e)[:200]))
    print('FAIL line', 6363, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistLifecycleMachine()
except BaseException as _e:
    _SOFT.append((6366, repr(_e)[:200]))
    print('FAIL line', 6366, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistLifecycleMachine()
except BaseException as _e:
    _SOFT.append((6368, repr(_e)[:200]))
    print('FAIL line', 6368, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.answer = 0
except BaseException as _e:
    _SOFT.append((6368, repr(_e)[:200]))
    print('FAIL line', 6368, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    before = m.db.execute('select * from playlist').fetchall()
except BaseException as _e:
    _SOFT.append((6368, repr(_e)[:200]))
    print('FAIL line', 6368, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def playlist_hold(steps=2):
    m = QueueMachine(page='playlist_page', rows=3)
    m.word(O['PLAYLIST_ADD_MODE'], 0)
    for i, e in enumerate((m.nodes[r]['children'][0] for r in m.nodes[m.surface]['children'])):
        if i:
            dots = m.node('image', f'img_more{i - 1}')
            m.word(dots + O['W_PARENT'], e)
            m.nodes[e]['children'].append(dots)
    lists = [m.song(f'List {i}', 4) for i in range(2)]
    for i, r in enumerate(lists):
        m.word(r + O['REC_DISC'], 7 + i)
    m.word(syms['p_deque_playlist'], m.deque(lists))
    m.found.append(m.song('Book.m4b'))
    m.word(m.surface + O['W_H'], 144)
    m.paint(m.surface)
    for _ in range(steps):
        m.call()
        m.paint(m.surface)
    return m
try:
    m = playlist_hold(0)
except BaseException as _e:
    _SOFT.append((6386, repr(_e)[:200]))
    print('FAIL line', 6386, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = playlist_hold()
except BaseException as _e:
    _SOFT.append((6387, repr(_e)[:200]))
    print('FAIL line', 6387, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = playlist_hold(1)
except BaseException as _e:
    _SOFT.append((6393, repr(_e)[:200]))
    print('FAIL line', 6393, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.found = [m.song('dir', 4)]
except BaseException as _e:
    _SOFT.append((6393, repr(_e)[:200]))
    print('FAIL line', 6393, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = playlist_hold()
except BaseException as _e:
    _SOFT.append((6394, repr(_e)[:200]))
    print('FAIL line', 6394, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = playlist_hold(1)
except BaseException as _e:
    _SOFT.append((6397, repr(_e)[:200]))
    print('FAIL line', 6397, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.top]['_pl_mode'] = 1
except BaseException as _e:
    _SOFT.append((6397, repr(_e)[:200]))
    print('FAIL line', 6397, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistEditMachine()
except BaseException as _e:
    _SOFT.append((6408, repr(_e)[:200]))
    print('FAIL line', 6408, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistEditMachine()
except BaseException as _e:
    _SOFT.append((6417, repr(_e)[:200]))
    print('FAIL line', 6417, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistEditMachine()
except BaseException as _e:
    _SOFT.append((6427, repr(_e)[:200]))
    print('FAIL line', 6427, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.view = m.find('scroll_view')
except BaseException as _e:
    _SOFT.append((6432, repr(_e)[:200]))
    print('FAIL line', 6432, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistEditMachine()
except BaseException as _e:
    _SOFT.append((6436, repr(_e)[:200]))
    print('FAIL line', 6436, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistEditMachine()
except BaseException as _e:
    _SOFT.append((6447, repr(_e)[:200]))
    print('FAIL line', 6447, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistEditMachine()
except BaseException as _e:
    _SOFT.append((6450, repr(_e)[:200]))
    print('FAIL line', 6450, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    key = O['KEY_FWD_BTN']
except BaseException as _e:
    _SOFT.append((6450, repr(_e)[:200]))
    print('FAIL line', 6450, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
import tempfile
try:
    m = PlaylistEditMachine()
except BaseException as _e:
    _SOFT.append((6465, repr(_e)[:200]))
    print('FAIL line', 6465, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    page = m.top
except BaseException as _e:
    _SOFT.append((6465, repr(_e)[:200]))
    print('FAIL line', 6465, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('dialog', 'msginfo_dialog')
except BaseException as _e:
    _SOFT.append((6465, repr(_e)[:200]))
    print('FAIL line', 6465, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = page
except BaseException as _e:
    _SOFT.append((6467, repr(_e)[:200]))
    print('FAIL line', 6467, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistEditMachine()
except BaseException as _e:
    _SOFT.append((6469, repr(_e)[:200]))
    print('FAIL line', 6469, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    songs = m.get(payload_syms['ple'] + 8)
except BaseException as _e:
    _SOFT.append((6471, repr(_e)[:200]))
    print('FAIL line', 6471, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    loads = m.get(payload_syms['playback_loads'])
except BaseException as _e:
    _SOFT.append((6471, repr(_e)[:200]))
    print('FAIL line', 6471, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LibraryQueryMachine()
except BaseException as _e:
    _SOFT.append((6485, repr(_e)[:200]))
    print('FAIL line', 6485, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((6493, repr(_e)[:200]))
    print('FAIL line', 6493, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.home
except BaseException as _e:
    _SOFT.append((6493, repr(_e)[:200]))
    print('FAIL line', 6493, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bar = m.node('window', 'system_bar')
except BaseException as _e:
    _SOFT.append((6494, repr(_e)[:200]))
    print('FAIL line', 6494, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    SPLIT = O['HOME_SPLIT_W']
except BaseException as _e:
    _SOFT.append((6496, repr(_e)[:200]))
    print('FAIL line', 6496, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    PW = 375 - SPLIT
except BaseException as _e:
    _SOFT.append((6496, repr(_e)[:200]))
    print('FAIL line', 6496, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def art_image(w):
    m.call(address=HOOKS['widget_on_paint_background'][0], args=(w, m.canvas, 0, 0), gap=0)
    return m.nodes[m.art].get('image')
try:
    m.nodes[m.art]['image'] = 'unchanged'
except BaseException as _e:
    _SOFT.append((6503, repr(_e)[:200]))
    print('FAIL line', 6503, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    np = m.node('window', 'playing_page')
except BaseException as _e:
    _SOFT.append((6504, repr(_e)[:200]))
    print('FAIL line', 6504, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = np
except BaseException as _e:
    _SOFT.append((6504, repr(_e)[:200]))
    print('FAIL line', 6504, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.home
except BaseException as _e:
    _SOFT.append((6506, repr(_e)[:200]))
    print('FAIL line', 6506, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def geometry():
    return [signed(m.get(m.art + O[k])) for k in ('W_X', 'W_Y', 'W_W', 'W_H')]

def cover(w, h):
    m.image_size = (w, h)
    m.u.mem_write(syms['g_lastcover_url'], b'/p/other\x00')
    art_image(m.home)
    m.u.mem_write(syms['g_lastcover_url'], b'/p/A\x00')
    art_image(m.home)
    return geometry()

def fit(w=290, h=290):
    return [SPLIT + int((PW - w) / 2), int((290 - h) / 2), w, h]
try:
    m.clip = (0, 0, 375, 320)
except BaseException as _e:
    _SOFT.append((6524, repr(_e)[:200]))
    print('FAIL line', 6524, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.image_size = None
except BaseException as _e:
    _SOFT.append((6534, repr(_e)[:200]))
    print('FAIL line', 6534, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def drifts():
    return [t for t in m.timers.values() if t[1] == payload_syms['home_drift']]
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((6543, repr(_e)[:200]))
    print('FAIL line', 6543, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.home
except BaseException as _e:
    _SOFT.append((6543, repr(_e)[:200]))
    print('FAIL line', 6543, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.image_size = (300, 300)
except BaseException as _e:
    _SOFT.append((6544, repr(_e)[:200]))
    print('FAIL line', 6544, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    playing = HOOKS['playing_page_init'][0]
except BaseException as _e:
    _SOFT.append((6552, repr(_e)[:200]))
    print('FAIL line', 6552, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine(queue=3, pos=1)
except BaseException as _e:
    _SOFT.append((6554, repr(_e)[:200]))
    print('FAIL line', 6554, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[playing + 12] = 'stock_playing'
except BaseException as _e:
    _SOFT.append((6554, repr(_e)[:200]))
    print('FAIL line', 6554, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bar = m.node('window', 'system_bar')
except BaseException as _e:
    _SOFT.append((6556, repr(_e)[:200]))
    print('FAIL line', 6556, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    pos, album, remain = (m.node('label', n) for n in ('label_ipod_pos', 'label_ipod_album', 'label_ipod_remain'))
except BaseException as _e:
    _SOFT.append((6557, repr(_e)[:200]))
    print('FAIL line', 6557, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    slider = m.node('slider', 'slider_play', max=225, value=100)
except BaseException as _e:
    _SOFT.append((6558, repr(_e)[:200]))
    print('FAIL line', 6558, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    win = m.node('window', 'playing_page', [m.node('view', 'view_buttons', [pos]), album, slider, remain])
except BaseException as _e:
    _SOFT.append((6559, repr(_e)[:200]))
    print('FAIL line', 6559, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = win
except BaseException as _e:
    _SOFT.append((6561, repr(_e)[:200]))
    print('FAIL line', 6561, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def shown():
    return [m.nodes[w].get('text') for w in (pos, album, remain)]

def written():
    return [m.nodes[c[1]]['name'] for c in m.calls if c[0] == 'widget_set_text_utf8']

def repaint(w=win):
    m.call(address=HOOKS['widget_on_paint_background'][0], args=(w, m.canvas, 0, 0))
    return written()
try:
    m.nodes[slider]['value'] = 101
except BaseException as _e:
    _SOFT.append((6568, repr(_e)[:200]))
    print('FAIL line', 6568, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    id3 = syms['g_play_id3_info']
except BaseException as _e:
    _SOFT.append((6575, repr(_e)[:200]))
    print('FAIL line', 6575, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    queue = m.deqs[m.get(syms['mcl_pdeqplaylist'])][1]
except BaseException as _e:
    _SOFT.append((6582, repr(_e)[:200]))
    print('FAIL line', 6582, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('window', 'home_page')
except BaseException as _e:
    _SOFT.append((6586, repr(_e)[:200]))
    print('FAIL line', 6586, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = win
except BaseException as _e:
    _SOFT.append((6587, repr(_e)[:200]))
    print('FAIL line', 6587, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(win, O['EVT_DESTROY'])
except BaseException as _e:
    _SOFT.append((6588, repr(_e)[:200]))
    print('FAIL line', 6588, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine(queue=3, pos=1)
except BaseException as _e:
    _SOFT.append((6594, repr(_e)[:200]))
    print('FAIL line', 6594, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[playing + 12] = 'stock_playing'
except BaseException as _e:
    _SOFT.append((6594, repr(_e)[:200]))
    print('FAIL line', 6594, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[sleep_hook + 12] = 'stock_sleep'
except BaseException as _e:
    _SOFT.append((6594, repr(_e)[:200]))
    print('FAIL line', 6594, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    cover = m.node('image', 'img_cover', image='play_defaultcover')
except BaseException as _e:
    _SOFT.append((6595, repr(_e)[:200]))
    print('FAIL line', 6595, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    win = m.node('window', 'playing_page', [cover])
except BaseException as _e:
    _SOFT.append((6595, repr(_e)[:200]))
    print('FAIL line', 6595, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = win
except BaseException as _e:
    _SOFT.append((6596, repr(_e)[:200]))
    print('FAIL line', 6596, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def cover_is(path, kind):
    m.u.mem_write(syms['g_lastcover_url'], path + b'\x00')
    m.byte(syms['g_playcover_type'], kind)

def art_pass():
    m.call(address=sleep_hook, args=(4660, 0, 0, 0), gap=0)
    return ([c[0] for c in m.calls if c[0] in ('image_manager_unload_bitmap_by_name', 'image_base_set_image')], m.nodes[cover].get('image'))
try:
    reload = ['image_manager_unload_bitmap_by_name', 'image_base_set_image']
except BaseException as _e:
    _SOFT.append((6601, repr(_e)[:200]))
    print('FAIL line', 6601, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    big = HOOKS['bigcover_page_init'][0]
except BaseException as _e:
    _SOFT.append((6644, repr(_e)[:200]))
    print('FAIL line', 6644, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def bigcover():
    b = QueueMachine(queue=3, pos=1)
    b.handlers[big + 12] = 'stock_bigcover'
    title, shade = (b.node('hscroll_label', 'scrlabel_title'), b.node('view', 'view_shade'))
    bw = b.node('window', 'bigcover_page', [shade, title])
    for w in (title, shade):
        b.byte(w + 52, 255)
    assert b.call(address=big, args=(bw, 7, 0, 0), gap=0) == 0 and b.calls[0][:3] == ('stock_bigcover', bw, 7)
    return (b, bw, lambda: [b.get(w + 52) & 255 for w in (title, shade)])
try:
    b, bw, alpha = bigcover()
except BaseException as _e:
    _SOFT.append((6653, repr(_e)[:200]))
    print('FAIL line', 6653, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    steps = []
except BaseException as _e:
    _SOFT.append((6655, repr(_e)[:200]))
    print('FAIL line', 6655, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    n = O['BC_STEPS']
except BaseException as _e:
    _SOFT.append((6658, repr(_e)[:200]))
    print('FAIL line', 6658, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.handlers[HOOKS['main_loop_sleep_default'][0] + 12] = 'stock_sleep'
except BaseException as _e:
    _SOFT.append((6660, repr(_e)[:200]))
    print('FAIL line', 6660, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tick = lambda: b.call(address=HOOKS['main_loop_sleep_default'][0], args=(4660, 0, 0, 0), gap=0, clear=False)
except BaseException as _e:
    _SOFT.append((6661, repr(_e)[:200]))
    print('FAIL line', 6661, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b, bw, alpha = bigcover()
except BaseException as _e:
    _SOFT.append((6665, repr(_e)[:200]))
    print('FAIL line', 6665, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = b.handler(bw, O['EVT_DESTROY'])
except BaseException as _e:
    _SOFT.append((6666, repr(_e)[:200]))
    print('FAIL line', 6666, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from ipod import BC_ART
try:
    b, bw, alpha = bigcover()
except BaseException as _e:
    _SOFT.append((6675, repr(_e)[:200]))
    print('FAIL line', 6675, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    shade, title = b.nodes[bw]['children']
except BaseException as _e:
    _SOFT.append((6675, repr(_e)[:200]))
    print('FAIL line', 6675, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    img = b.node('image', 'img_bigcover')
except BaseException as _e:
    _SOFT.append((6676, repr(_e)[:200]))
    print('FAIL line', 6676, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def put(w, *g):
    for k, v in zip(('W_X', 'W_Y', 'W_W', 'W_H'), g):
        b.word(w + O[k], v)

def geo(w):
    return [signed(b.get(w + O[k])) for k in ('W_X', 'W_Y', 'W_W', 'W_H')]

def bc_track(i):
    return b.text(b.get(b.items(b.get(syms['mcl_pdeqplaylist']))[i] + O['REC_PATH'])).encode() + b'\x00'
try:
    sq = [(375 - BC_ART) // 2, (320 - BC_ART) // 2, BC_ART, BC_ART]
except BaseException as _e:
    _SOFT.append((6681, repr(_e)[:200]))
    print('FAIL line', 6681, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.nodes[img]['image'] = 'file:///tmp/coverpic.jpg'
except BaseException as _e:
    _SOFT.append((6683, repr(_e)[:200]))
    print('FAIL line', 6683, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.image_size = (1600, 900)
except BaseException as _e:
    _SOFT.append((6683, repr(_e)[:200]))
    print('FAIL line', 6683, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bc_dec = ['image_manager_unload_bitmap_by_name', 'widget_load_image']
except BaseException as _e:
    _SOFT.append((6684, repr(_e)[:200]))
    print('FAIL line', 6684, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bc_mv = ['widget_move_resize'] * 3
except BaseException as _e:
    _SOFT.append((6684, repr(_e)[:200]))
    print('FAIL line', 6684, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def fit():
    b.call(address=HOOKS['widget_on_paint_background'][0], args=(bw, b.canvas, 0, 0), gap=0)
    return [c[0] for c in b.calls if c[0] in bc_dec + bc_mv]
try:
    x, y, w, h = geo(img)
except BaseException as _e:
    _SOFT.append((6691, repr(_e)[:200]))
    print('FAIL line', 6691, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.nodes[img]['image'] = 'file:///tmp/externpic.jpg'
except BaseException as _e:
    _SOFT.append((6696, repr(_e)[:200]))
    print('FAIL line', 6696, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.image_size = (900, 1600)
except BaseException as _e:
    _SOFT.append((6698, repr(_e)[:200]))
    print('FAIL line', 6698, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    x, y, w, h = geo(img)
except BaseException as _e:
    _SOFT.append((6699, repr(_e)[:200]))
    print('FAIL line', 6699, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.nodes[img]['image'] = 'file:///tmp/album_tidal.jpg'
except BaseException as _e:
    _SOFT.append((6700, repr(_e)[:200]))
    print('FAIL line', 6700, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.nodes[img]['image'] = 'file:///tmp/externpic.jpg'
except BaseException as _e:
    _SOFT.append((6702, repr(_e)[:200]))
    print('FAIL line', 6702, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b.nodes[img]['image'] = 'default_bigcover'
except BaseException as _e:
    _SOFT.append((6703, repr(_e)[:200]))
    print('FAIL line', 6703, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OLED = theme_builtins()[0]
except BaseException as _e:
    _SOFT.append((6709, repr(_e)[:200]))
    print('FAIL line', 6709, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bh = OLED['np_bar']
except BaseException as _e:
    _SOFT.append((6709, repr(_e)[:200]))
    print('FAIL line', 6709, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = QueueMachine(queue=3, pos=1)
except BaseException as _e:
    _SOFT.append((6710, repr(_e)[:200]))
    print('FAIL line', 6710, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[playing + 12] = 'stock_playing'
except BaseException as _e:
    _SOFT.append((6710, repr(_e)[:200]))
    print('FAIL line', 6710, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'], 16)] = 'stock_ui_load'
except BaseException as _e:
    _SOFT.append((6711, repr(_e)[:200]))
    print('FAIL line', 6711, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    slider, elapsed, remain = (m.node('slider', 'slider_play', max=225, value=100, bar_size=4), m.node('label', 'label_playtime'), m.node('label', 'label_ipod_remain'))
except BaseException as _e:
    _SOFT.append((6713, repr(_e)[:200]))
    print('FAIL line', 6713, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    win = m.node('window', 'playing_page', [slider, elapsed, remain])
except BaseException as _e:
    _SOFT.append((6714, repr(_e)[:200]))
    print('FAIL line', 6714, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = win
except BaseException as _e:
    _SOFT.append((6714, repr(_e)[:200]))
    print('FAIL line', 6714, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def put(w, *g):
    for k, v in zip(('W_X', 'W_Y', 'W_W', 'W_H'), g):
        m.word(w + O[k], v)
try:
    sv, lv = (m.node('slider', 'slider_vol', max=100, value=40), m.node('label', 'label_vol'))
except BaseException as _e:
    _SOFT.append((6720, repr(_e)[:200]))
    print('FAIL line', 6720, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    dlg = m.node('dialog', 'volume_dialog', [sv, lv])
except BaseException as _e:
    _SOFT.append((6722, repr(_e)[:200]))
    print('FAIL line', 6722, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def paint(w):
    m.call(address=HOOKS['widget_on_paint_background'][0], args=(w, m.canvas, 0, 0))

def did(n):
    return [c[1:] for c in m.calls if c[0] == n]
try:
    band = (0, 270, 375, 30 + 265 + 16 - 270)
except BaseException as _e:
    _SOFT.append((6725, repr(_e)[:200]))
    print('FAIL line', 6725, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    lo = (24, 270 + (30 - bh) // 2, 327, bh)
except BaseException as _e:
    _SOFT.append((6725, repr(_e)[:200]))
    print('FAIL line', 6725, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bar = (lo[0] + O['VOL_SPK_INSET'], lo[1], lo[2] - 2 * O['VOL_SPK_INSET'], bh)
except BaseException as _e:
    _SOFT.append((6725, repr(_e)[:200]))
    print('FAIL line', 6725, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.wm] = {'children': [win, dlg]}
except BaseException as _e:
    _SOFT.append((6726, repr(_e)[:200]))
    print('FAIL line', 6726, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = dlg
except BaseException as _e:
    _SOFT.append((6726, repr(_e)[:200]))
    print('FAIL line', 6726, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    cy = lo[1] + bh // 2 - O['VOL_SPK_PX'] // 2
except BaseException as _e:
    _SOFT.append((6732, repr(_e)[:200]))
    print('FAIL line', 6732, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    vol = [t for t, v in m.timers.items() if v[1] == payload_syms['vol_poll']]
except BaseException as _e:
    _SOFT.append((6734, repr(_e)[:200]))
    print('FAIL line', 6734, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    poll = vol[0]
except BaseException as _e:
    _SOFT.append((6734, repr(_e)[:200]))
    print('FAIL line', 6734, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    inv = lambda: [c[0] for c in did('widget_invalidate_force') if c[0] == dlg]
except BaseException as _e:
    _SOFT.append((6735, repr(_e)[:200]))
    print('FAIL line', 6735, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[sv]['value'] = 41
except BaseException as _e:
    _SOFT.append((6737, repr(_e)[:200]))
    print('FAIL line', 6737, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[sv]['value'] = 100
except BaseException as _e:
    _SOFT.append((6739, repr(_e)[:200]))
    print('FAIL line', 6739, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.wm]['children'] = [win]
except BaseException as _e:
    _SOFT.append((6741, repr(_e)[:200]))
    print('FAIL line', 6741, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = win
except BaseException as _e:
    _SOFT.append((6741, repr(_e)[:200]))
    print('FAIL line', 6741, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    other = m.node('window', 'home_page')
except BaseException as _e:
    _SOFT.append((6744, repr(_e)[:200]))
    print('FAIL line', 6744, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sv2, lv2 = (m.node('slider', 'slider_vol', max=100, value=7), m.node('label', 'label_vol'))
except BaseException as _e:
    _SOFT.append((6744, repr(_e)[:200]))
    print('FAIL line', 6744, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    dlg2 = m.node('dialog', 'volume_dialog', [sv2, lv2])
except BaseException as _e:
    _SOFT.append((6745, repr(_e)[:200]))
    print('FAIL line', 6745, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.wm]['children'] = [win, other, dlg2]
except BaseException as _e:
    _SOFT.append((6746, repr(_e)[:200]))
    print('FAIL line', 6746, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = dlg2
except BaseException as _e:
    _SOFT.append((6746, repr(_e)[:200]))
    print('FAIL line', 6746, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    X, Y, H, P, T = (O['VOL_PANEL_' + k] for k in ('X', 'Y', 'H', 'PAD', 'TEXT'))
except BaseException as _e:
    _SOFT.append((6747, repr(_e)[:200]))
    print('FAIL line', 6747, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    panel = (X, Y, 375 - 2 * X, H)
except BaseException as _e:
    _SOFT.append((6747, repr(_e)[:200]))
    print('FAIL line', 6747, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bw = panel[2] - 2 * P - T
except BaseException as _e:
    _SOFT.append((6747, repr(_e)[:200]))
    print('FAIL line', 6747, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    BH = O['VOL_PANEL_BAR']
except BaseException as _e:
    _SOFT.append((6749, repr(_e)[:200]))
    print('FAIL line', 6749, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bar = (X + P, Y + (H - BH) // 2, bw, BH)
except BaseException as _e:
    _SOFT.append((6749, repr(_e)[:200]))
    print('FAIL line', 6749, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[sv2]['value'] = 1
except BaseException as _e:
    _SOFT.append((6753, repr(_e)[:200]))
    print('FAIL line', 6753, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    R = O['LETTER_RADIUS']
except BaseException as _e:
    _SOFT.append((6755, repr(_e)[:200]))
    print('FAIL line', 6755, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class ABMachine(QueueMachine):
    ab = 0

    def hook(self, u, address, size, unused):
        if self.handlers.get(address) != 'ab:x':
            return super().hook(u, address, size, unused)
        self.calls.append(('ab_x', u.reg_read(UC_MIPS_REG_A0)))
        self.returns(self.ab, clobber=True)
try:
    m = ABMachine(queue=3, pos=1)
except BaseException as _e:
    _SOFT.append((6763, repr(_e)[:200]))
    print('FAIL line', 6763, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[playing + 12] = 'stock_playing'
except BaseException as _e:
    _SOFT.append((6763, repr(_e)[:200]))
    print('FAIL line', 6763, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[5487064] = 'ab:x'
except BaseException as _e:
    _SOFT.append((6763, repr(_e)[:200]))
    print('FAIL line', 6763, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'], 16)] = 'stock_ui_load'
except BaseException as _e:
    _SOFT.append((6764, repr(_e)[:200]))
    print('FAIL line', 6764, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    slider = m.node('slider', 'slider_play', max=225, value=100)
except BaseException as _e:
    _SOFT.append((6766, repr(_e)[:200]))
    print('FAIL line', 6766, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    win = m.node('window', 'playing_page', [slider])
except BaseException as _e:
    _SOFT.append((6766, repr(_e)[:200]))
    print('FAIL line', 6766, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = win
except BaseException as _e:
    _SOFT.append((6766, repr(_e)[:200]))
    print('FAIL line', 6766, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def geo(w):
    return tuple((signed(m.get(w + O[k])) for k in ('W_X', 'W_Y', 'W_W', 'W_H')))
from ipod import VALUE_NAMES
try:
    BX, BW = (m.call(address=payload_syms['geom'], args=(VALUE_NAMES.index(n), 0, 0, 0), gap=0) for n in ('np_bar_x', 'np_bar_w'))
except BaseException as _e:
    _SOFT.append((6770, repr(_e)[:200]))
    print('FAIL line', 6770, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from build import AB_X_CALLS

def ab_x(x):
    m.ab = x
    m.calls = []
    r = signed(m.call(address=payload_syms['ringnav_ab_x'], args=(77, 0, 0, 0), gap=0))
    assert m.calls[0] == ('ab_x', 77)
    return r

class UiLoad:

    def hook(self, u, address, size, unused):
        if self.handlers.get(address) != 'load:stock_ui_load':
            return super().hook(u, address, size, unused)
        self.calls.append(('stock_ui_load', u.reg_read(REGS[0])))
        self.returns(self.root)

class LoadQueue(UiLoad, QueueMachine):
    ready = 0

    def bootvol(self):
        return self.ready or super().bootvol()

class LoadPlain(UiLoad, Machine):
    pass

def ui_loaded(m, name, root):
    m.root = root
    m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'], 16)] = 'load:stock_ui_load'
    m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0], args=(m.string(name), 0, 0, 0), gap=0)

def place(m, w, *g):
    for k, v in zip(('W_X', 'W_Y', 'W_W', 'W_H'), g):
        m.word(w + O[k], v)

def wbox(m, w):
    return tuple((signed(m.get(w + O[k])) for k in ('W_X', 'W_Y', 'W_W', 'W_H')))

def parents(m, w):
    for c in m.nodes[w]['children']:
        m.word(c + O['W_PARENT'], w)
        parents(m, c)

def bg(m, w):
    m.call(address=HOOKS['widget_on_paint_background'][0], args=(w, m.canvas, 0, 0), gap=0)

def art_size(name):
    return struct.unpack('>II', (B / f'{name}.png').read_bytes()[16:24])

def vol_np(light='1', remain=1, **config):
    SETUP[:] = [lambda k: (k.config.update(LIGHT=light, **config), k)]
    try:
        q = LoadQueue(queue=3, pos=1)
    finally:
        SETUP.clear()
    q.handlers[playing + 12] = 'stock_playing'
    q.slider = q.node('slider', 'slider_play', max=300, value=100)
    q.elapsed, q.remain = (q.node('label', 'label_playtime'), q.node('label', 'label_ipod_remain'))
    q.win = q.top = q.node('window', 'playing_page', [q.slider, q.elapsed, q.remain])
    q.word(q.win + O['W_PARENT'], q.wm)
    parents(q, q.win)
    place(q, q.win, 0, 26, 375, 294)
    place(q, q.slider, 63, 213, 248, 43)
    place(q, q.elapsed, 18, 240, 42, 16)
    q.word(syms['system_bar'], q.node('window', 'system_bar'))
    assert q.call(address=playing, args=(q.win, 7, 0, 0), gap=0) == 0
    q.nodes[q.remain]['visible'] = remain
    q.sv, q.lv = (q.node('slider', 'slider_vol', max=100, value=40, visible=0), q.node('label', 'label_vol', visible=0))
    q.dlg = q.node('dialog', 'volume_dialog', [q.sv, q.lv])
    q.word(q.dlg + O['W_PARENT'], q.wm)
    place(q, q.dlg, 0, 0, 375, 320)
    q.nodes[q.wm] = {'children': [q.win, q.dlg]}
    q.top = q.dlg
    q.mock('window_close')
    ui_loaded(q, 'dialog/volume_dialog', q.dlg)
    return q

def vol_closed(q):
    q.nodes[q.wm]['children'] = [q.win]
    q.top = q.win
    q.calls = []
    q.advance(O['VOL_POLL_MS'])
    assert not q.timers and [q.nodes[w]['visible'] for w in (q.elapsed, q.remain)] == [1, 1]

def slider_win(m, name, slider, view, label, ends, y=0, **kw):
    s = m.node('slider', slider, **kw)
    lab = m.node('label', label, text='5')
    l, r = (m.node('image', n) for n in ends)
    dec, add = (m.node('image', 'img_dec'), m.node('image', 'img_add'))
    v = m.node('view', view, [lab, s, l, r, dec, add])
    nav = m.node('view', 'view_navbar', visible=0)
    win = m.node('window', name, [nav, v])
    parents(m, win)
    m.word(win + O['W_PARENT'], m.wm)
    place(m, win, 0, 26, 375, 294)
    place(m, v, 0, y, 375, 240)
    place(m, s, 31, 60, 313, 70)
    place(m, lab, 100, 15, 175, 32)
    m.top = win
    m.nodes[m.wm] = {'children': [win]}
    ui_loaded(m, name, win)
    return (win, s, v, lab, nav, l, r, dec, add)

class MsgMachine(LoadPlain):
    songs = 704

    def __init__(self):
        super().__init__()
        self.mock('sqlite3_open', 'sqlite3_close', 'strstr@GLIBC_2.0')
        self.sqls = []
        self.threads = []
        self.thread_fails = self.busy = self.thread_first = False
        for n, h in (('sqlite3_exec', 'msg:exec'), ('strdup@GLIBC_2.0', 'msg:strdup'), ('pthread_create@GLIBC_2.2', 'msg:thread')):
            self.handlers[syms[n]] = h
        self.word(10723952, 16777232)
        self.handlers[16777232] = 'msg:free'

    def hook(self, u, address, size, unused):
        h = self.handlers.get(address)
        if h == 'msg:strdup':
            self.returns(self.string(self.text(u.reg_read(REGS[0]))))
            return
        if h == 'msg:thread':
            if not self.thread_fails:
                self.word(u.reg_read(REGS[0]), 77)
                self.threads.append((u.reg_read(REGS[2]), u.reg_read(REGS[3])))
            self.calls.append(('pthread_create',))
            if self.thread_first and (not self.thread_fails):
                fn, arg = self.threads.pop()
                self.nested(fn, arg)
            self.returns(11 if self.thread_fails else 0)
            return
        if h == 'msg:free':
            self.returns(0)
            return
        if h != 'msg:exec':
            return super().hook(u, address, size, unused)
        sql, row = (self.text(u.reg_read(REGS[1])), u.reg_read(REGS[2]))
        self.sqls.append(sql)
        if self.busy:
            self.returns(5)
            return
        if row:
            argv = self.alloc(4)
            self.word(argv, self.string(str(self.songs)))
            self.nested(row, u.reg_read(REGS[3]), 1, argv, 0)
        self.returns(0)

    def counted(self):
        while self.threads:
            fn, arg = self.threads.pop(0)
            self.call(address=fn, args=(arg, 0, 0, 0), gap=0, count=5000000, clear=False)

def msg_win(m, text):
    anim, msg, scan, btn = (m.node('image_animation', 'img_animation'), m.node('label', 'label_msg', text=text), m.node('label', 'label_scanfolder'), m.node('button', 'btn_no', text='Cancel'))
    win = m.node('dialog', 'updatemusic_dialog', [anim, msg, scan, btn])
    parents(m, win)
    m.word(win + O['W_PARENT'], m.wm)
    place(m, win, 0, 0, 375, 320)
    place(m, btn, 44, 266, 287, 40)
    m.top = win
    m.nodes[m.wm] = {'children': [win]}
    ui_loaded(m, 'dialog/updatemusic_dialog', win)
    return (win, anim, msg, scan, btn)

def usb_page(mode, light='1', y=26, machine=None):
    m = (machine or MsgMachine)()
    m.config.update(LIGHT=light)
    m.byte(syms['g_usbmode'], mode)
    ret, img, info, charge, dacset = (m.node('image', 'img_return'), m.node('image', 'img_usbmode'), m.node('hscroll_label', 'scrlabel_info'), m.node('image', 'img_daccharge'), m.node('image', 'img_dacset'))
    win = m.node('window', 'usbmode_page', [ret, img, info, charge, dacset])
    parents(m, win)
    m.word(win + O['W_PARENT'], m.wm)
    place(m, win, 0, y, 375, 320 - y)
    place(m, ret, 20, 0, 80, 60)
    place(m, charge, 247, 0, 50, 50)
    place(m, dacset, 305, 0, 50, 50)
    m.top = win
    m.nodes[m.wm] = {'children': [win]}
    ui_loaded(m, 'usbmode_page', win)
    return (m, win, ret, img, info, charge, dacset)
try:
    m = QueueMachine(queue=3, pos=1)
except BaseException as _e:
    _SOFT.append((6866, repr(_e)[:200]))
    print('FAIL line', 6866, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[playing + 12] = 'stock_playing'
except BaseException as _e:
    _SOFT.append((6866, repr(_e)[:200]))
    print('FAIL line', 6866, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    cover = m.node('image', 'img_cover')
except BaseException as _e:
    _SOFT.append((6867, repr(_e)[:200]))
    print('FAIL line', 6867, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    win = m.node('window', 'playing_page', [cover])
except BaseException as _e:
    _SOFT.append((6867, repr(_e)[:200]))
    print('FAIL line', 6867, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = win
except BaseException as _e:
    _SOFT.append((6867, repr(_e)[:200]))
    print('FAIL line', 6867, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    R = theme_builtins()[0]['np_art_radius']
except BaseException as _e:
    _SOFT.append((6871, repr(_e)[:200]))
    print('FAIL line', 6871, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    want = []
except BaseException as _e:
    _SOFT.append((6871, repr(_e)[:200]))
    print('FAIL line', 6871, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from ipod import BC_ART
try:
    bigart = m.node('image', 'img_bigcover')
except BaseException as _e:
    _SOFT.append((6883, repr(_e)[:200]))
    print('FAIL line', 6883, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bx, by = ((375 - BC_ART) // 2, (320 - BC_ART) // 2)
except BaseException as _e:
    _SOFT.append((6884, repr(_e)[:200]))
    print('FAIL line', 6884, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bigwin = m.node('window', 'bigcover_page', [bigart])
except BaseException as _e:
    _SOFT.append((6886, repr(_e)[:200]))
    print('FAIL line', 6886, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[big + 12] = 'stock_bigcover'
except BaseException as _e:
    _SOFT.append((6886, repr(_e)[:200]))
    print('FAIL line', 6886, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    pixels = {}
except BaseException as _e:
    _SOFT.append((6891, repr(_e)[:200]))
    print('FAIL line', 6891, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    q = QueueMachine(queue=3, pos=1)
except BaseException as _e:
    _SOFT.append((6902, repr(_e)[:200]))
    print('FAIL line', 6902, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    q.handlers[playing + 12] = 'stock_playing'
except BaseException as _e:
    _SOFT.append((6902, repr(_e)[:200]))
    print('FAIL line', 6902, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    glyph, album = (q.node('image', 'img_playstate'), q.node('label', 'label_ipod_album'))
except BaseException as _e:
    _SOFT.append((6903, repr(_e)[:200]))
    print('FAIL line', 6903, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    win = q.node('window', 'playing_page', [q.node('slider', 'slider_play', max=225, value=100, bar_size=4), glyph, album])
except BaseException as _e:
    _SOFT.append((6905, repr(_e)[:200]))
    print('FAIL line', 6905, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    q.top = win
except BaseException as _e:
    _SOFT.append((6906, repr(_e)[:200]))
    print('FAIL line', 6906, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OLED = theme_builtins()[0]
except BaseException as _e:
    _SOFT.append((6908, repr(_e)[:200]))
    print('FAIL line', 6908, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    FILL_HI = signed(color_t(O['MINIMAL_FILL']))
except BaseException as _e:
    _SOFT.append((6915, repr(_e)[:200]))
    print('FAIL line', 6915, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    FG = 'style:normal:fg_color'
except BaseException as _e:
    _SOFT.append((6915, repr(_e)[:200]))
    print('FAIL line', 6915, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def scrub_page(value=100, mx=225, m=None):
    if not m:
        m = QueueMachine(queue=3, pos=1)
        m.handlers[playing + 12] = 'stock_playing'
    m.slider = m.node('slider', 'slider_play', max=mx, value=value)
    m.elapsed, m.remain = (m.node('label', 'label_playtime'), m.node('label', 'label_ipod_remain'))
    m.win = m.top = m.node('window', 'playing_page', [m.slider, m.elapsed, m.remain])
    m.word(m.win + O['W_PARENT'], m.wm)
    assert m.call(address=playing, args=(m.win, 7, 0, 0), gap=0) == 0
    return m

def did(m, name):
    return [c[1] for c in m.calls if c[0] == name]

def keep(m):
    m.seeks = getattr(m, 'seeks', []) + did(m, 'player_seek_time')
    m.starts = getattr(m, 'starts', []) + did(m, 'playing_timer_start')

def step(m, *a, **k):
    r = (m.advance if k.pop('wait', False) else m.call)(*a, **k)
    keep(m)
    return r

def centre(m, gap=1000):
    assert m.call(O['KEY_CENTER'], gap=gap) == 11 and (not did(m, 'playing_timer_clear'))
    assert m.call(O['KEY_CENTER'], gap=100, clear=False) == 11
    m.advance(DC, clear=False)
    keep(m)
    assert not m.screens

def oled_scrub(**kw):
    m = QueueMachine(queue=3, pos=1)
    m.handlers[playing + 12] = 'stock_playing'
    m.record_syscalls()
    m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'], 16)] = 'stock_ui_load'
    m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0], args=(m.string('system_bar'), 0, 0, 0), gap=0)
    return scrub_page(m=m, **kw)
try:
    OLED_FILL = signed(color_t(next((s for s in theme_builtins()[0]['schemes'] if s.get('name') == 'oled-blue'))['tones'][2]))
except BaseException as _e:
    _SOFT.append((6938, repr(_e)[:200]))
    print('FAIL line', 6938, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def np_press(m, gap=1000):
    assert step(m, O['KEY_CENTER'], gap=gap) == 11 and (not m.screens)

def ended(m, seeks, label=''):
    assert m.seeks == seeks and m.starts == [m.win], (label, m.seeks, m.starts)
    assert m.nodes[m.slider][FG] == OLED_FILL and (not m.timers) and (m.call() == 0) and (not did(m, 'player_seek_time')), label
try:
    m = oled_scrub()
except BaseException as _e:
    _SOFT.append((6946, repr(_e)[:200]))
    print('FAIL line', 6946, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def knob(m):
    m.rounded.clear()
    m.call(address=HOOKS['widget_on_paint_border'][0], args=(m.slider, m.canvas, 0, 0), gap=0)
    return [(r['rect'], r['radius'], r['color']) for r in m.rounded if r['rect'][2:] == (14, 14)]
try:
    m = scrub_page()
except BaseException as _e:
    _SOFT.append((6957, repr(_e)[:200]))
    print('FAIL line', 6957, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    PLAY_STATUS = 10813572
except BaseException as _e:
    _SOFT.append((6980, repr(_e)[:200]))
    print('FAIL line', 6980, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = scrub_page()
except BaseException as _e:
    _SOFT.append((6981, repr(_e)[:200]))
    print('FAIL line', 6981, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def reopen(m):
    f, ctx = m.handler(m.win, O['EVT_DESTROY'])
    step(m, address=f, args=(ctx, m.event, 0, 0), gap=0)
    m.starts = []
    m.calls.clear()
    scrub_page(m=m)
    keep(m)
try:
    m = scrub_page()
except BaseException as _e:
    _SOFT.append((6996, repr(_e)[:200]))
    print('FAIL line', 6996, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 100
except BaseException as _e:
    _SOFT.append((6996, repr(_e)[:200]))
    print('FAIL line', 6996, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 111
except BaseException as _e:
    _SOFT.append((7001, repr(_e)[:200]))
    print('FAIL line', 7001, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 100
except BaseException as _e:
    _SOFT.append((7002, repr(_e)[:200]))
    print('FAIL line', 7002, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = scrub_page()
except BaseException as _e:
    _SOFT.append((7004, repr(_e)[:200]))
    print('FAIL line', 7004, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = scrub_page()
except BaseException as _e:
    _SOFT.append((7009, repr(_e)[:200]))
    print('FAIL line', 7009, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = scrub_page()
except BaseException as _e:
    _SOFT.append((7026, repr(_e)[:200]))
    print('FAIL line', 7026, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def vol_up(m):
    m.dlg = m.node('dialog', 'volume_dialog')
    m.word(m.dlg + O['W_PARENT'], m.wm)
    m.nodes[m.wm] = {'children': [m.win, m.dlg]}
    m.stack = [m.win, m.dlg]
    m.top = m.dlg
    m.word(m.wm + O['W_KEY_TARGET'], m.dlg)
try:
    m = scrub_page()
except BaseException as _e:
    _SOFT.append((7033, repr(_e)[:200]))
    print('FAIL line', 7033, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = scrub_page()
except BaseException as _e:
    _SOFT.append((7037, repr(_e)[:200]))
    print('FAIL line', 7037, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = scrub_page()
except BaseException as _e:
    _SOFT.append((7041, repr(_e)[:200]))
    print('FAIL line', 7041, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    other = m.node('window', 'home_page')
except BaseException as _e:
    _SOFT.append((7041, repr(_e)[:200]))
    print('FAIL line', 7041, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.wm]['children'] = [m.win, other, m.dlg]
except BaseException as _e:
    _SOFT.append((7042, repr(_e)[:200]))
    print('FAIL line', 7042, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = scrub_page()
except BaseException as _e:
    _SOFT.append((7046, repr(_e)[:200]))
    print('FAIL line', 7046, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('window', 'home_page')
except BaseException as _e:
    _SOFT.append((7047, repr(_e)[:200]))
    print('FAIL line', 7047, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = scrub_page()
except BaseException as _e:
    _SOFT.append((7049, repr(_e)[:200]))
    print('FAIL line', 7049, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.win, O['EVT_DESTROY'])
except BaseException as _e:
    _SOFT.append((7050, repr(_e)[:200]))
    print('FAIL line', 7050, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ROWS = (20, 50, 20, 20, 25) + (20,) * 7
except BaseException as _e:
    _SOFT.append((7069, repr(_e)[:200]))
    print('FAIL line', 7069, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sleep_hook = HOOKS['main_loop_sleep_default'][0]
except BaseException as _e:
    _SOFT.append((7070, repr(_e)[:200]))
    print('FAIL line', 7070, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def lyric_page(page=1, size=9, lines=len(ROWS), config=None):
    m = scrub_page()
    m.mock('calloc@GLIBC_2.0', 'pthread_mutex_lock@GLIBC_2.0', 'pthread_mutex_unlock@GLIBC_2.0')
    m.fail_calloc = True
    m.word(10723952, 16777232)
    m.handlers[16777232] = 'free'
    m.lyric_size = size
    m.handlers[sleep_hook + 12] = 'stock_sleep'
    m.config.update(config or {})
    rows = [m.node('label') for _ in range(lines)]
    for i, r in enumerate(rows):
        m.word(r + O['W_Y'], sum(ROWS[:i]))
        m.word(r + O['W_H'], ROWS[i])
    m.lrc = m.node('scroll_view', 'scroll_lrc', rows)
    m.word(m.lrc + O['W_H'], 178)
    m.word(m.lrc + O['VIEW_CONTENT_H'], sum(ROWS))
    m.slide = m.node('slide_view', 'slide_view', [m.lrc], value=page)
    m.nodes[m.win]['children'].append(m.slide)
    assert m.call(address=playing, args=(m.win, 7, 0, 0), gap=0) == 0
    m.calls = []
    m.word(payload_syms['wheel_held'], 1)
    return m

def y(m):
    return m.get(m.lrc + O['SCROLL_Y'])

def raw(m):
    return m.get(payload_syms['wheel_raw'])

def loop(m, travel=None, ms=8):
    if travel is not None:
        m.word(payload_syms['wheel_travel'], travel & 4294967295)
    n = len(m.calls)
    m.advance(ms, clear=False)
    m.call(address=sleep_hook, args=(4660, 0, 0, 0), gap=0, clear=False)
    for name, seen in (('player_seek_time', 'seeks'), ('playing_timer_start', 'starts')):
        setattr(m, seen, getattr(m, seen, []) + [c[1] for c in m.calls[n:] if c[0] == name])
    return y(m)

def settle(m, n=60):
    return [loop(m) for _ in range(n)]

def follow(m, to):
    m.call(address=payload_syms['ringnav_lyric_follow'], args=(m.lrc, 0, to, 10), gap=0, clear=False)
    return y(m)

def hold(m):
    loop(m, -56)
    loop(m, 56)
    settle(m)

def awake(page, art=False, status=2):
    CONFIG['KEEPLIT'] = '1'
    m = lyric_page(page=page)
    del CONFIG['KEEPLIT']
    m.handlers.pop(syms['mclGetPlayStatus'], None)
    m.word(PLAY_STATUS, status)
    if art:
        bw = m.node('window', 'bigcover_page')
        m.handlers[HOOKS['bigcover_page_init'][0] + 12] = 'stock_bigcover'
        m.call(address=HOOKS['bigcover_page_init'][0], args=(bw, 7, 0, 0), gap=0)
        m.top = bw
    m.advance(1000)
    m.calls = []
    loop(m)
    return ('reset_poweroptions_timer', 1, 1, 1) in [c[:4] for c in m.calls]
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7115, repr(_e)[:200]))
    print('FAIL line', 7115, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((7115, repr(_e)[:200]))
    print('FAIL line', 7115, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7122, repr(_e)[:200]))
    print('FAIL line', 7122, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7125, repr(_e)[:200]))
    print('FAIL line', 7125, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((7125, repr(_e)[:200]))
    print('FAIL line', 7125, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ys = [loop(m, -28)] + settle(m)
except BaseException as _e:
    _SOFT.append((7126, repr(_e)[:200]))
    print('FAIL line', 7126, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    steps = [b - a for a, b in zip([0] + ys, ys)]
except BaseException as _e:
    _SOFT.append((7126, repr(_e)[:200]))
    print('FAIL line', 7126, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page(config={'WHEELSENSITIVITY': '100'})
except BaseException as _e:
    _SOFT.append((7140, repr(_e)[:200]))
    print('FAIL line', 7140, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7142, repr(_e)[:200]))
    print('FAIL line', 7142, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.slide]['value'] = 0
except BaseException as _e:
    _SOFT.append((7146, repr(_e)[:200]))
    print('FAIL line', 7146, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.slide]['value'] = 1
except BaseException as _e:
    _SOFT.append((7147, repr(_e)[:200]))
    print('FAIL line', 7147, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.win, O['EVT_DESTROY'])
except BaseException as _e:
    _SOFT.append((7149, repr(_e)[:200]))
    print('FAIL line', 7149, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7152, repr(_e)[:200]))
    print('FAIL line', 7152, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7161, repr(_e)[:200]))
    print('FAIL line', 7161, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def rows(m):
    m.call(address=payload_syms['ringnav_lyric_rows'], args=(m.win, 0, 0, 0), gap=0, clear=False)
    return y(m)
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7165, repr(_e)[:200]))
    print('FAIL line', 7165, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[5486204] = 'stock_lyric_rows'
except BaseException as _e:
    _SOFT.append((7165, repr(_e)[:200]))
    print('FAIL line', 7165, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page(page=0)
except BaseException as _e:
    _SOFT.append((7171, repr(_e)[:200]))
    print('FAIL line', 7171, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 1
except BaseException as _e:
    _SOFT.append((7171, repr(_e)[:200]))
    print('FAIL line', 7171, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.slide]['value'] = 1
except BaseException as _e:
    _SOFT.append((7171, repr(_e)[:200]))
    print('FAIL line', 7171, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 0
except BaseException as _e:
    _SOFT.append((7172, repr(_e)[:200]))
    print('FAIL line', 7172, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7173, repr(_e)[:200]))
    print('FAIL line', 7173, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    a = m.alloc(128)
except BaseException as _e:
    _SOFT.append((7173, repr(_e)[:200]))
    print('FAIL line', 7173, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 1
except BaseException as _e:
    _SOFT.append((7173, repr(_e)[:200]))
    print('FAIL line', 7173, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7176, repr(_e)[:200]))
    print('FAIL line', 7176, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[5486204] = 'stock_lyric_rows'
except BaseException as _e:
    _SOFT.append((7176, repr(_e)[:200]))
    print('FAIL line', 7176, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.slide]['value'] = 0
except BaseException as _e:
    _SOFT.append((7177, repr(_e)[:200]))
    print('FAIL line', 7177, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 1
except BaseException as _e:
    _SOFT.append((7180, repr(_e)[:200]))
    print('FAIL line', 7180, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.slide]['value'] = 1
except BaseException as _e:
    _SOFT.append((7180, repr(_e)[:200]))
    print('FAIL line', 7180, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 0
except BaseException as _e:
    _SOFT.append((7181, repr(_e)[:200]))
    print('FAIL line', 7181, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7184, repr(_e)[:200]))
    print('FAIL line', 7184, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ys = settle(m)
except BaseException as _e:
    _SOFT.append((7185, repr(_e)[:200]))
    print('FAIL line', 7185, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7189, repr(_e)[:200]))
    print('FAIL line', 7189, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def other(m):
    m.top = m.node('window', 'home_page')

def back(m):
    m.top = m.win

def dark(m, on=0):
    m.byte(syms['g_backlight_status'], on)

def touch(m, on=1):
    m.pressed = on

def bare(m, on=0):
    m.lyric_size = 9 if on else 0
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7206, repr(_e)[:200]))
    print('FAIL line', 7206, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 1
except BaseException as _e:
    _SOFT.append((7206, repr(_e)[:200]))
    print('FAIL line', 7206, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 0
except BaseException as _e:
    _SOFT.append((7207, repr(_e)[:200]))
    print('FAIL line', 7207, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7208, repr(_e)[:200]))
    print('FAIL line', 7208, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 1
except BaseException as _e:
    _SOFT.append((7208, repr(_e)[:200]))
    print('FAIL line', 7208, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 0
except BaseException as _e:
    _SOFT.append((7208, repr(_e)[:200]))
    print('FAIL line', 7208, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7209, repr(_e)[:200]))
    print('FAIL line', 7209, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('window', 'home_page')
except BaseException as _e:
    _SOFT.append((7209, repr(_e)[:200]))
    print('FAIL line', 7209, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 1
except BaseException as _e:
    _SOFT.append((7209, repr(_e)[:200]))
    print('FAIL line', 7209, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 0
except BaseException as _e:
    _SOFT.append((7210, repr(_e)[:200]))
    print('FAIL line', 7210, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.win
except BaseException as _e:
    _SOFT.append((7210, repr(_e)[:200]))
    print('FAIL line', 7210, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7213, repr(_e)[:200]))
    print('FAIL line', 7213, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ys = []
except BaseException as _e:
    _SOFT.append((7215, repr(_e)[:200]))
    print('FAIL line', 7215, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7222, repr(_e)[:200]))
    print('FAIL line', 7222, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.slide]['value'] = 0
except BaseException as _e:
    _SOFT.append((7223, repr(_e)[:200]))
    print('FAIL line', 7223, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.slide]['value'] = 1
except BaseException as _e:
    _SOFT.append((7223, repr(_e)[:200]))
    print('FAIL line', 7223, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = lyric_page()
except BaseException as _e:
    _SOFT.append((7228, repr(_e)[:200]))
    print('FAIL line', 7228, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.win, O['EVT_DESTROY'])
except BaseException as _e:
    _SOFT.append((7231, repr(_e)[:200]))
    print('FAIL line', 7231, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
import subprocess
from build import GP
try:
    got = next((int(l.split()[0], 16) for l in subprocess.run(['readelf', '-AW', B / 'stock-demo'], capture_output=True, text=True).stdout.splitlines() if l.endswith(' taglib_file_new')))
except BaseException as _e:
    _SOFT.append((7239, repr(_e)[:200]))
    print('FAIL line', 7239, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    lib = symbols(B / 'libtag_c.so.2.0.1')
except BaseException as _e:
    _SOFT.append((7240, repr(_e)[:200]))
    print('FAIL line', 7240, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((7242, repr(_e)[:200]))
    print('FAIL line', 7242, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    TAGLIB = 18350080
except BaseException as _e:
    _SOFT.append((7243, repr(_e)[:200]))
    print('FAIL line', 7243, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class LyricMachine(QueueMachine):

    def __init__(self, tags, lrc=(), cls=61441, kind=8, bound=True):
        super().__init__(cls=cls)
        self.tags = tags
        self.lrc = list(lrc)
        self.bound = bound
        self.lines = []
        self.asked = []
        self.tag_calls = []
        self.word(self.items(self.get(syms['mcl_pdeqplaylist']))[0] + O['REC_TYPE'], kind)
        self.word(O['MCL_LYRICS'], self.deque([], 'stLyricInfo'))
        self.mock('pthread_mutex_lock@GLIBC_2.0', 'pthread_mutex_unlock@GLIBC_2.0')
        self.word(10723952, 16777232)
        self.handlers[16777232] = 'lyric:libc_free'
        self.handlers[int(manifest['patch_symbols']['stock_lyric_trampoline'], 16)] = 'lyric:stock'
        for n, off in (('get', O['TAGLIB_PROPERTY_GET']), ('pfree', O['TAGLIB_PROPERTY_FREE'])):
            self.handlers[TAGLIB + off] = 'lyric:' + n
        for n in ('taglib_file_new', 'taglib_file_free', 'mclGetLyricSize', '_deque_push_back'):
            self.handlers[syms[n]] = 'lyric:' + n

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('lyric:'):
            return super().hook(u, address, size, unused)
        name = name[6:]
        a, b = [u.reg_read(r) for r in REGS[:2]]
        ret = 0
        if name == 'stock':
            self.tag_calls.append('stock')
            self.lines = list(self.lrc)
            ret = 1 if self.lrc else -1
        elif name == 'mclGetLyricSize':
            ret = len(self.lines)
        elif name == '_deque_push_back' and a == self.get(O['MCL_LYRICS']):
            self.lines.append((signed(self.get(b)), self.text(self.get(b + 4))))
        elif name == '_deque_push_back':
            return super().hook(u, address, size, unused)
        elif name == 'libc_free':
            if a and getattr(self, 'zero_freed', 0):
                self.u.mem_write(a, b'\x00' * len(self.raw(a)))
        elif name == 'taglib_file_new':
            assert b == 0
            self.tag_calls.append(('new', self.text(a)))
            ret = 18284544
            if self.bound:
                self.word(O['TAGLIB_NEW_GOT'], TAGLIB + O['TAGLIB_FILE_NEW'])
        elif name == 'get':
            assert a == 18284544
            key = self.text(b)
            self.asked.append(key)
            if key in self.tags:
                ret = self.alloc(8)
                self.word(ret, self.string(self.tags[key]) if self.tags[key] is not None else 0)
                self.word(ret + 4, 0)
        else:
            assert name == 'pfree' or a == 18284544
            self.tag_calls.append(name.removeprefix('taglib_file_'))
        self.returns(ret, clobber=True)

    def load(self):
        return self.call(address=HOOKS['mclLoadExLyric'][0], args=(0, 0, 0, 0), count=5000000)
try:
    FAR = 2147483647
except BaseException as _e:
    _SOFT.append((7274, repr(_e)[:200]))
    print('FAIL line', 7274, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({'LYRICS': 'x'}, lrc=['[00:01.00]a'])
except BaseException as _e:
    _SOFT.append((7275, repr(_e)[:200]))
    print('FAIL line', 7275, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({'LYRICS': 'First\r\n\nThird\n'})
except BaseException as _e:
    _SOFT.append((7276, repr(_e)[:200]))
    print('FAIL line', 7276, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({'UNSYNCEDLYRICS': 'Only\rline'})
except BaseException as _e:
    _SOFT.append((7278, repr(_e)[:200]))
    print('FAIL line', 7278, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({})
except BaseException as _e:
    _SOFT.append((7282, repr(_e)[:200]))
    print('FAIL line', 7282, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({'LYRICS': 'x'}, bound=False)
except BaseException as _e:
    _SOFT.append((7285, repr(_e)[:200]))
    print('FAIL line', 7285, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({'LYRICS': ('a' * 99 + '\n') * 700})
except BaseException as _e:
    _SOFT.append((7286, repr(_e)[:200]))
    print('FAIL line', 7286, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({'LYRICS': '[ar:Someone]\nIntro\n[00:12.50]Two\r\n[00:03]One\n[01:02.00][00:20.10]Both\nloose\n[00:30.00]\n'})
except BaseException as _e:
    _SOFT.append((7290, repr(_e)[:200]))
    print('FAIL line', 7290, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({'LYRICS': '[Chorus]\nLa la\n[1:2]\n[00:61]x'})
except BaseException as _e:
    _SOFT.append((7293, repr(_e)[:200]))
    print('FAIL line', 7293, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({'LYRICS': '[00:12.50]<00:12.50>Hel<00:12.90>lo <00:13.40>world<00:14.00>\n[00:15.05][00:40.00]<00:15.10>Twice\n[00:16]<00:16.00>\n'})
except BaseException as _e:
    _SOFT.append((7298, repr(_e)[:200]))
    print('FAIL line', 7298, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({'LYRICS': '[00:01.999]a\n[00:01.5]b\n[00:01:25]c\n[00:01.012345]d\n'})
except BaseException as _e:
    _SOFT.append((7300, repr(_e)[:200]))
    print('FAIL line', 7300, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({'LYRICS': 'a<b>c <1:2> <00:61.00>x <00:01.00\n'})
except BaseException as _e:
    _SOFT.append((7302, repr(_e)[:200]))
    print('FAIL line', 7302, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({'LYRICS': 'One\r\r\nTwo\r\r\n\r\r\n\n\nThree\r\r\n'})
except BaseException as _e:
    _SOFT.append((7306, repr(_e)[:200]))
    print('FAIL line', 7306, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({'LYRICS': '  \t\n\u3000\n\xa0x\xa0 \n\u200b\n\ufeff  y  \x0b\n'})
except BaseException as _e:
    _SOFT.append((7307, repr(_e)[:200]))
    print('FAIL line', 7307, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({'LYRICS': '[00:01.00]A\n[00:02.00]\n[00:03.00]  \u3000\n\n[00:04.00]  B  \n'})
except BaseException as _e:
    _SOFT.append((7308, repr(_e)[:200]))
    print('FAIL line', 7308, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({'LYRICS': '[00:01.00] <00:01.00>Hi <00:01.50>there <00:02.00>\n'})
except BaseException as _e:
    _SOFT.append((7309, repr(_e)[:200]))
    print('FAIL line', 7309, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LyricMachine({'LYRICS': ' \n\r\n[00:02.00]\n'})
except BaseException as _e:
    _SOFT.append((7310, repr(_e)[:200]))
    print('FAIL line', 7310, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from build import LYRIC_OPEN

class LrcMachine(LyricMachine):

    def __init__(self, tags={}, lrc=(), path=None, files={}):
        super().__init__(tags)
        self.k_stock = list(lrc)
        self.k_path = path
        self.k_files = dict(files)
        for n in ('fopen@GLIBC_2.2', 'fread@GLIBC_2.0', 'fclose@GLIBC_2.2', 'deque_clear'):
            self.handlers[syms[n]] = 'k:' + n

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        a, b, c, d = [u.reg_read(r) for r in REGS[:4]]
        ret = 0
        if name == 'lyric:stock':
            self.tag_calls.append('stock')
            if self.k_path:
                self.nested(payload_syms['ringnav_lrc_open'], self.string(self.k_path), self.string('r'))
            self.lines = list(self.k_stock)
            return self.returns(1 if self.k_stock else -1, clobber=True)
        if name == 'k:fopen@GLIBC_2.2':
            ret = 18284800 if self.text(a) in self.k_files else 0
            self.k_opened = self.text(a)
        elif name == 'k:fread@GLIBC_2.0':
            data = self.k_files[self.k_opened][:b * c]
            self.u.mem_write(a, data)
            ret = len(data) // b
        elif name == 'k:deque_clear':
            assert a == self.get(O['MCL_LYRICS'])
            self.lines = []
        elif name != 'k:fclose@GLIBC_2.2':
            return super().hook(u, address, size, unused)
        self.returns(ret, clobber=True)
try:
    lrc = '\ufeff[ar:x]\n[00:01.20]<00:01.20>A<00:01.70>b\n[00:01.90]\n\n[00:00.50]  Zero\r\r\n'.encode()
except BaseException as _e:
    _SOFT.append((7333, repr(_e)[:200]))
    print('FAIL line', 7333, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LrcMachine(path='/mnt/mmc/a.lrc', files={'/mnt/mmc/a.lrc': lrc}, lrc=[(1, '<00:01.20>A<00:01.70>b'), (1, ' '), (0, '  Zero')])
except BaseException as _e:
    _SOFT.append((7334, repr(_e)[:200]))
    print('FAIL line', 7334, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LrcMachine(path='/mnt/mmc/a.lrc', files={'/mnt/mmc/a.lrc': b'plain\nwords'}, lrc=[(FAR, 'plain'), (FAR, 'words')])
except BaseException as _e:
    _SOFT.append((7336, repr(_e)[:200]))
    print('FAIL line', 7336, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = LrcMachine(files={'/e': '[00:01.00]Hi 🎵\n'.encode(), '/q': '[00:01.00]Café da’s\n'.encode(), '/k': '[00:01.00]나 는\n'.encode(), '/a': b'[00:01.00]plain\n', '/g': '[00:01.00]你好\n'.encode('gb18030'), '/l': '[00:01.00]Café\n'.encode('latin-1')})
except BaseException as _e:
    _SOFT.append((7341, repr(_e)[:200]))
    print('FAIL line', 7341, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def is_utf8(path):
    return m.call(address=HOOKS['mclIsUtf8WithoutBOM'][0], args=(m.string(path), 0, 0, 0), gap=0)

def app_lrc(rec_id, gen=3, body=b'[00:01.00]Hi\n[00:02.50]there\n'):
    m = LrcMachine(files={'/tmp/q2-jellyfin.lyrics/2.lrc': body})
    m.word(O['MCL_TYPE'], 241)
    m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[0] + O['REC_TIDAL_ID'], m.string(rec_id))
    au = payload_syms['au']
    m.word(au + 72, gen)
    m.u.mem_write(au + 80, b'/tmp/q2-jellyfin.lyrics\x00')
    return m
try:
    m = app_lrc('app:3:2')
except BaseException as _e:
    _SOFT.append((7354, repr(_e)[:200]))
    print('FAIL line', 7354, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = app_lrc('app:3:2')
except BaseException as _e:
    _SOFT.append((7355, repr(_e)[:200]))
    print('FAIL line', 7355, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.zero_freed = 1
except BaseException as _e:
    _SOFT.append((7355, repr(_e)[:200]))
    print('FAIL line', 7355, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    t = m.get(payload_syms['ly.1'])
except BaseException as _e:
    _SOFT.append((7355, repr(_e)[:200]))
    print('FAIL line', 7355, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = app_lrc('app:3:2', body=b'\xef\xbb\xbfPlain\nwords\n')
except BaseException as _e:
    _SOFT.append((7356, repr(_e)[:200]))
    print('FAIL line', 7356, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = app_lrc('app:2:2')
except BaseException as _e:
    _SOFT.append((7357, repr(_e)[:200]))
    print('FAIL line', 7357, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = app_lrc('app:3:1')
except BaseException as _e:
    _SOFT.append((7358, repr(_e)[:200]))
    print('FAIL line', 7358, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = app_lrc('12345')
except BaseException as _e:
    _SOFT.append((7359, repr(_e)[:200]))
    print('FAIL line', 7359, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    SLOT = O['LYRIC_SLOT']
except BaseException as _e:
    _SOFT.append((7367, repr(_e)[:200]))
    print('FAIL line', 7367, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    REST, LIT = (color_t(11250603), color_t(O['LYRIC_LIT']))
except BaseException as _e:
    _SOFT.append((7367, repr(_e)[:200]))
    print('FAIL line', 7367, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    LPX, LX = (27, 24)
except BaseException as _e:
    _SOFT.append((7368, repr(_e)[:200]))
    print('FAIL line', 7368, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    PITCH = LPX + LPX // 8
except BaseException as _e:
    _SOFT.append((7368, repr(_e)[:200]))
    print('FAIL line', 7368, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ROW = PITCH + SLOT - (34 + 34 // 8)
except BaseException as _e:
    _SOFT.append((7368, repr(_e)[:200]))
    print('FAIL line', 7368, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def mid(i, rows=None):
    hs = rows or [ROW] * (i + 1)
    return max(0, SLOT + sum(hs[:i]) + int((hs[i] - 178) / 2))

class KaraokeMachine(LrcMachine):

    def __init__(self, tags={}):
        super().__init__(tags)
        self.k_inks = []
        self.handlers[playing + 12] = 'stock_playing'
        self.handlers[5486204] = 'k:rows'
        for n in ('widget_layout_children', 'widget_get_canvas', 'canvas_draw_text', 'canvas_measure_text'):
            self.handlers[syms[n]] = 'k:' + n
        self.handlers.pop(syms['mclGetPlayStatus'])
        self.word(PLAY_STATUS, 2)

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        a, b, c, d = [u.reg_read(r) for r in REGS[:4]]
        ret = 0
        if name not in ('k:rows', 'k:widget_layout_children', 'k:widget_get_canvas', 'k:canvas_draw_text', 'k:canvas_measure_text'):
            return super().hook(u, address, size, unused)
        name = name[2:]
        if name == 'rows':
            self.build()
        elif name == 'widget_layout_children':
            at = int(re.search('y=(\\d+)', self.nodes[a]['children_layout'])[1])
            for k in self.nodes[a]['children']:
                self.word(k + O['W_Y'], at)
                at += self.get(k + O['W_H'])
        elif name == 'widget_get_canvas':
            ret = self.canvas
        elif name == 'canvas_measure_text':
            u.reg_write(UC_MIPS_REG_F0, struct.unpack('<I', struct.pack('<f', c * self.font[1] / 2))[0])
        elif name == 'canvas_draw_text':
            self.k_inks.append((''.join((chr(self.get(b + 4 * j)) for j in range(c))), signed(d), signed(self.get(u.reg_read(UC_MIPS_REG_SP) + 16)), self.get(self.lcd + O['LCD_TEXT_COLOR']), self.font[1], self.clip, self.font[0]))
        self.returns(ret, clobber=True)

    def build(self):
        kids = [self.node('label', text=t, lrc_time=sec) for sec, t in self.lines]
        for k in kids:
            self.word(k + O['W_PARENT'], self.lrc)
            self.word(k + O['W_W'], 225)
            self.word(k + O['W_H'], 20)
        self.nodes[self.lrc]['children'] = kids
        self.word(self.lrc + O['SCROLL_Y'], 0)

    def show(self, playtime=0, lrc=None, bar=False):
        self.playtime = playtime
        self.lrc = lrc or self.node('scroll_view', 'scroll_lrc')
        self.word(self.lrc + O['W_W'], 375)
        self.word(self.lrc + O['W_H'], 178)
        self.build()
        self.slide = self.node('slide_view', 'slide_view', [self.lrc], value=1)
        self.slider = bar and self.node('slider', 'slider_play', max=225, value=playtime)
        kids = [self.slide] + [self.slider] * bool(bar)
        self.win = self.top = self.node('window', 'playing_page', kids)
        self.word(self.win + O['W_PARENT'], self.wm)
        assert self.call(address=playing, args=(self.win, 7, 0, 0), gap=0) == 0
        self.handlers[sleep_hook + 12] = 'stock_sleep'
        self.word(payload_syms['wheel_held'], 1)
        return y(self)

    def rows(self):
        self.call(address=payload_syms['ringnav_lyric_rows'], args=(self.win, 0, 0, 0), gap=0, clear=False)
        return y(self)

    def lyric_row(self, i):
        return self.nodes[self.lrc]['children'][i]

    def at_ms(self, ms=0):
        self.advance(ms, clear=False)
        return self.call(address=payload_syms['lyrics_now'], args=(0, 0, 0, 0), gap=0, clear=False)

    def ink(self, i):
        self.k_inks = []
        self.clip = (0, 0, 375, SLOT)
        self.paint(self.lyric_row(i), gap=0)
        return [x for x in self.k_inks if x[1] == LX]

    def dirty(self):
        return [c[1] for c in self.calls if c[0] == 'widget_invalidate_force' and c[1] in self.nodes[self.lrc]['children']]

def karaoke(lines=((5, 'One'), (9, 'Two two'), (12, 'Three'), (20, 'Four'), (30, 'Five')), **kw):
    m = KaraokeMachine(**kw)
    m.lines = list(lines)
    return m
try:
    m = karaoke()
except BaseException as _e:
    _SOFT.append((7420, repr(_e)[:200]))
    print('FAIL line', 7420, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 5
except BaseException as _e:
    _SOFT.append((7424, repr(_e)[:200]))
    print('FAIL line', 7424, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 9
except BaseException as _e:
    _SOFT.append((7424, repr(_e)[:200]))
    print('FAIL line', 7424, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ys = settle(m)
except BaseException as _e:
    _SOFT.append((7424, repr(_e)[:200]))
    print('FAIL line', 7424, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.slide]['value'] = 0
except BaseException as _e:
    _SOFT.append((7427, repr(_e)[:200]))
    print('FAIL line', 7427, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 12
except BaseException as _e:
    _SOFT.append((7427, repr(_e)[:200]))
    print('FAIL line', 7427, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.slide]['value'] = 1
except BaseException as _e:
    _SOFT.append((7428, repr(_e)[:200]))
    print('FAIL line', 7428, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 30
except BaseException as _e:
    _SOFT.append((7430, repr(_e)[:200]))
    print('FAIL line', 7430, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.slide]['value'] = 0
except BaseException as _e:
    _SOFT.append((7431, repr(_e)[:200]))
    print('FAIL line', 7431, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.slide]['value'] = 1
except BaseException as _e:
    _SOFT.append((7431, repr(_e)[:200]))
    print('FAIL line', 7431, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.win, O['EVT_DESTROY'])
except BaseException as _e:
    _SOFT.append((7433, repr(_e)[:200]))
    print('FAIL line', 7433, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 20
except BaseException as _e:
    _SOFT.append((7435, repr(_e)[:200]))
    print('FAIL line', 7435, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 30
except BaseException as _e:
    _SOFT.append((7436, repr(_e)[:200]))
    print('FAIL line', 7436, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 0
except BaseException as _e:
    _SOFT.append((7439, repr(_e)[:200]))
    print('FAIL line', 7439, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 12
except BaseException as _e:
    _SOFT.append((7440, repr(_e)[:200]))
    print('FAIL line', 7440, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 1
except BaseException as _e:
    _SOFT.append((7441, repr(_e)[:200]))
    print('FAIL line', 7441, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 20
except BaseException as _e:
    _SOFT.append((7441, repr(_e)[:200]))
    print('FAIL line', 7441, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 0
except BaseException as _e:
    _SOFT.append((7441, repr(_e)[:200]))
    print('FAIL line', 7441, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = karaoke()
except BaseException as _e:
    _SOFT.append((7445, repr(_e)[:200]))
    print('FAIL line', 7445, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    POLL = 250
except BaseException as _e:
    _SOFT.append((7445, repr(_e)[:200]))
    print('FAIL line', 7445, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 8
except BaseException as _e:
    _SOFT.append((7446, repr(_e)[:200]))
    print('FAIL line', 7446, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 9
except BaseException as _e:
    _SOFT.append((7447, repr(_e)[:200]))
    print('FAIL line', 7447, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 10
except BaseException as _e:
    _SOFT.append((7449, repr(_e)[:200]))
    print('FAIL line', 7449, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 60
except BaseException as _e:
    _SOFT.append((7449, repr(_e)[:200]))
    print('FAIL line', 7449, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = karaoke()
except BaseException as _e:
    _SOFT.append((7453, repr(_e)[:200]))
    print('FAIL line', 7453, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 9
except BaseException as _e:
    _SOFT.append((7453, repr(_e)[:200]))
    print('FAIL line', 7453, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((7458, repr(_e)[:200]))
    print('FAIL line', 7458, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 12
except BaseException as _e:
    _SOFT.append((7458, repr(_e)[:200]))
    print('FAIL line', 7458, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((7458, repr(_e)[:200]))
    print('FAIL line', 7458, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.bands = []
except BaseException as _e:
    _SOFT.append((7461, repr(_e)[:200]))
    print('FAIL line', 7461, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    fades = sorted(((b[1], b[4]) for b in m.bands if b[3] == 1 and b[2] == 375))
except BaseException as _e:
    _SOFT.append((7462, repr(_e)[:200]))
    print('FAIL line', 7462, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    want = sorted([(i, 255 * (32 - i) // 32 << 24) for i in range(32)] + [(177 - i, 255 * (32 - i) // 32 << 24) for i in range(32)])
except BaseException as _e:
    _SOFT.append((7463, repr(_e)[:200]))
    print('FAIL line', 7463, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = karaoke([(10, 'abcdefghi ' * 3 + 'abcdefghi'), (20, '字' * 40), (30, 'x' * 60), (40, 'One'), (50, 'y' * 150)])
except BaseException as _e:
    _SOFT.append((7468, repr(_e)[:200]))
    print('FAIL line', 7468, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    gap = ROW - PITCH
except BaseException as _e:
    _SOFT.append((7469, repr(_e)[:200]))
    print('FAIL line', 7469, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    hs = [2 * PITCH + gap, 2 * PITCH + gap, 3 * PITCH + gap, ROW, 4 * PITCH + gap]
except BaseException as _e:
    _SOFT.append((7469, repr(_e)[:200]))
    print('FAIL line', 7469, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def whole(i):
    m.word(m.lrc + O['SCROLL_Y'], m.get(m.lyric_row(i) + O['W_Y']))
    return m.ink(i)
try:
    top = gap // 2 + (PITCH - LPX) // 2
except BaseException as _e:
    _SOFT.append((7474, repr(_e)[:200]))
    print('FAIL line', 7474, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 30
except BaseException as _e:
    _SOFT.append((7480, repr(_e)[:200]))
    print('FAIL line', 7480, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    off = SLOT + hs[0] + hs[1] - (178 - hs[2]) // 2
except BaseException as _e:
    _SOFT.append((7480, repr(_e)[:200]))
    print('FAIL line', 7480, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def seen(i):
    top = SLOT + sum(hs[:i]) - off
    return max(0, min(top + hs[i], 178) - max(top, 0))
try:
    SEEK = '[00:05.00]One\n[00:09.70]' + 'abcdefghi ' * 3 + 'abcdefghi\n[00:12.00]Three\n[00:20.00]Four\n'
except BaseException as _e:
    _SOFT.append((7487, repr(_e)[:200]))
    print('FAIL line', 7487, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def seek_page(status=2, tag=SEEK):
    m = karaoke([], tags={'LYRICS': tag})
    m.load()
    m.show(5, bar=True)
    m.word(PLAY_STATUS, status)
    settle(m)
    return m

def moves(m):
    return [(c[0], c[1]) for c in m.calls if c[0] in ('player_seek_time', 'mclSetSeek', 'mclSetResume')]

def long_play(m):
    m.t = getattr(m, 't', 7000) + 1
    m.press(m.t)
    took = m.hold() == 11
    seen = moves(m)
    return (took, m.release(), seen)

def song_menu(m):
    return any((p[0] == 'dialog/sortselect_dialog' for p in m.opened))

def tone(m, i):
    return {x[3] & 16777215 for x in m.ink(i)}
try:
    m = seek_page()
except BaseException as _e:
    _SOFT.append((7495, repr(_e)[:200]))
    print('FAIL line', 7495, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((7496, repr(_e)[:200]))
    print('FAIL line', 7496, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ys = settle(m)
except BaseException as _e:
    _SOFT.append((7496, repr(_e)[:200]))
    print('FAIL line', 7496, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = seek_page(tag='plain\nwords\n')
except BaseException as _e:
    _SOFT.append((7500, repr(_e)[:200]))
    print('FAIL line', 7500, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = seek_page()
except BaseException as _e:
    _SOFT.append((7501, repr(_e)[:200]))
    print('FAIL line', 7501, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.slide]['value'] = 0
except BaseException as _e:
    _SOFT.append((7501, repr(_e)[:200]))
    print('FAIL line', 7501, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = karaoke()
except BaseException as _e:
    _SOFT.append((7503, repr(_e)[:200]))
    print('FAIL line', 7503, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fail_calloc = True
except BaseException as _e:
    _SOFT.append((7503, repr(_e)[:200]))
    print('FAIL line', 7503, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = karaoke()
except BaseException as _e:
    _SOFT.append((7509, repr(_e)[:200]))
    print('FAIL line', 7509, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.win, O['EVT_DESTROY'])
except BaseException as _e:
    _SOFT.append((7509, repr(_e)[:200]))
    print('FAIL line', 7509, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tag = '[00:12.50]<00:12.50>Hel<00:12.90>lo <00:13.40>world\n[00:15.00]Next\n'
except BaseException as _e:
    _SOFT.append((7516, repr(_e)[:200]))
    print('FAIL line', 7516, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    CW = (LPX + 1) // 2
except BaseException as _e:
    _SOFT.append((7517, repr(_e)[:200]))
    print('FAIL line', 7517, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def lit(m):
    return m.ink(0)[-1][5][2] - LX

def at13125(m):
    m.at_ms()
    m.at_ms(275)
    m.playtime = 13
    m.at_ms()
    loop(m, ms=0)
try:
    m = karaoke([], tags={'LYRICS': tag})
except BaseException as _e:
    _SOFT.append((7520, repr(_e)[:200]))
    print('FAIL line', 7520, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((7524, repr(_e)[:200]))
    print('FAIL line', 7524, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.playtime = 15
except BaseException as _e:
    _SOFT.append((7525, repr(_e)[:200]))
    print('FAIL line', 7525, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = karaoke([(12, 'Hello world'), (15, 'Next')], tags={'LYRICS': tag})
except BaseException as _e:
    _SOFT.append((7526, repr(_e)[:200]))
    print('FAIL line', 7526, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = karaoke([], tags={'LYRICS': tag})
except BaseException as _e:
    _SOFT.append((7527, repr(_e)[:200]))
    print('FAIL line', 7527, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.lines = [(12, 'Hello world'), (15, 'Nest')]
except BaseException as _e:
    _SOFT.append((7527, repr(_e)[:200]))
    print('FAIL line', 7527, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = karaoke([], tags={'LYRICS': '[00:13.00] <00:13.00>Hi <00:13.50>there <00:14.00>\n[00:15.00]Next\n'})
except BaseException as _e:
    _SOFT.append((7531, repr(_e)[:200]))
    print('FAIL line', 7531, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class FullMachine(KaraokeMachine):

    def __init__(self):
        super().__init__()
        self.restack = True
        for n in ('widget_layout', 'widget_index_of'):
            self.handlers[syms[n]] = 'c:' + n

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('c:'):
            return super().hook(u, address, size, unused)
        a = u.reg_read(REGS[0])
        ret = 0
        if name == 'c:widget_layout':
            assert a == self.col
            self.word(self.lrc + O['W_W'], self.get(self.slide + O['W_W']))
            self.word(self.lrc + O['W_H'], self.get(self.slide + O['W_H']) - 20)
            self.word(self.lrc + O['SCROLL_Y'], 0)
        else:
            ret = self.nodes[self.win]['children'].index(a)
        self.returns(ret, clobber=True)

    def show(self, playtime=0, lrc=None, slide='slide_view'):
        self.playtime = playtime
        self.lrc = self.node('scroll_view', 'scroll_lrc')
        self.build()
        self.view = self.node('list_view', 'view_lrc', [self.lrc])
        self.lyric = self.node('view', 'view_lyric', [self.view])
        self.word(self.lrc + O['W_PARENT'], self.view)
        self.slide = self.node('slide_view', slide, [self.lyric], value=1)
        self.dots = self.node('slide_indicator', 'slide_indicator1')
        self.word(self.dots + O['W_Y'], 182)
        self.col = self.node('view', 'slide_view_view', [self.slide, self.dots])
        self.word(self.slide + O['W_PARENT'], self.col)
        self.bar = self.node('slider', 'slider_play')
        self.win = self.top = self.node('window', 'playing_page', [self.node('hscroll_label', 'scrlabel_title'), self.col, self.bar])
        for w, box in ((self.win, (0, 30, 375, 290)), (self.col, (0, 40, 375, 192)), (self.slide, (0, 0, 375, 186)), (self.view, (0, 0, 375, 178)), (self.lrc, (0, 0, 375, 178))):
            for k, v in zip(('W_X', 'W_Y', 'W_W', 'W_H'), box):
                self.word(w + O[k], v)
        self.word(self.win + O['W_PARENT'], self.wm)
        self.stack = [self.win]
        assert self.call(address=playing, args=(self.win, 7, 0, 0), gap=0) == 0
        self.handlers[sleep_hook + 12] = 'stock_sleep'
        self.word(payload_syms['wheel_held'], 1)

    def touch(self, kind, x=150, y=100):
        f, ctx = self.handler(self.lyric, O[kind])
        ev = self.alloc(32)
        for k, v in (('EVENT_TYPE', O[kind]), ('EVENT_X', x), ('EVENT_Y', y)):
            self.word(ev + O[k], v)
        r = self.call(address=f, args=(ctx, ev, 0, 0), gap=0, clear=False)
        assert r == 0, (kind, r)
        return r

    def tap(self, x=150, y=100, dx=0, dy=0):
        self.touch('EVT_POINTER_DOWN', x, y)
        self.pressed = 1
        loop(self)
        self.touch('EVT_POINTER_UP_BEFORE', x + dx, y + dy)
        self.pressed = 0
        loop(self)

    def full(self):
        return (self.nodes[self.win]['children'].index(self.col), self.get(self.col + O['W_X']), self.get(self.col + O['W_W']), self.get(self.slide + O['W_W']), self.get(self.slide + O['W_H']), self.nodes[self.col].get('style:normal:bg_color'), {self.get(r + O['W_W']) for r in self.nodes[self.lrc]['children']})

    def where(self):
        return (self.full()[:6], [self.get(self.col + O[k]) for k in ('W_Y', 'W_H')], self.nodes[self.dots].get('self_layout'), self.nodes[self.view].get('self_layout'))
try:
    COLUMN, FULL = ((1, 0, 375, 375, 186, '#00000000', {375}), (2, 0, 375, 375, 290 - 20, '#000000', {375}))
except BaseException as _e:
    _SOFT.append((7583, repr(_e)[:200]))
    print('FAIL line', 7583, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    PLACED = ((1, 0, 375, 375, 186, '#00000000'), [40, 192], ('0', '182', '100%', '10'), ('0', '0', '100%', '178'))
except BaseException as _e:
    _SOFT.append((7584, repr(_e)[:200]))
    print('FAIL line', 7584, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def full_page(lines=((5, 'x' * 15), (9, 'Two two'), (12, 'Three')), playtime=9):
    m = FullMachine()
    m.lines = list(lines)
    m.show(playtime)
    return m

def centre_of(m, i, view):
    r = m.lyric_row(i)
    return m.get(r + O['W_Y']) + int((m.get(r + O['W_H']) - view) / 2)
try:
    m = full_page()
except BaseException as _e:
    _SOFT.append((7589, repr(_e)[:200]))
    print('FAIL line', 7589, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    held = settle(m)[-1]
except BaseException as _e:
    _SOFT.append((7590, repr(_e)[:200]))
    print('FAIL line', 7590, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = full_page()
except BaseException as _e:
    _SOFT.append((7603, repr(_e)[:200]))
    print('FAIL line', 7603, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('window', 'home_page')
except BaseException as _e:
    _SOFT.append((7603, repr(_e)[:200]))
    print('FAIL line', 7603, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.win
except BaseException as _e:
    _SOFT.append((7603, repr(_e)[:200]))
    print('FAIL line', 7603, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 1
except BaseException as _e:
    _SOFT.append((7604, repr(_e)[:200]))
    print('FAIL line', 7604, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 0
except BaseException as _e:
    _SOFT.append((7605, repr(_e)[:200]))
    print('FAIL line', 7605, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = full_page(lines=())
except BaseException as _e:
    _SOFT.append((7608, repr(_e)[:200]))
    print('FAIL line', 7608, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = full_page()
except BaseException as _e:
    _SOFT.append((7610, repr(_e)[:200]))
    print('FAIL line', 7610, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    want = [(O[k], payload_syms['np_lyric_touch'], 0) for k in ('EVT_POINTER_DOWN', 'EVT_POINTER_UP_BEFORE', 'EVT_POINTER_ABORT')]
except BaseException as _e:
    _SOFT.append((7610, repr(_e)[:200]))
    print('FAIL line', 7610, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def tap_line(m, y=160, dy=0, abort=False, ms=0, dx=0):
    m.calls = []
    m.touch('EVT_POINTER_DOWN', 150, y)
    m.pressed = 1
    loop(m)
    if abort:
        m.touch('EVT_POINTER_ABORT', 150 + dx, y + dy)
    if ms:
        m.advance(ms, clear=False)
    m.touch('EVT_POINTER_UP_BEFORE', 150 + dx, y + dy)
    m.pressed = 0
    loop(m)
    return moves(m)

def full_on(m):
    return m.full()[5] == '#000000'
try:
    m = full_page()
except BaseException as _e:
    _SOFT.append((7621, repr(_e)[:200]))
    print('FAIL line', 7621, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = full_page(lines=((FAR, 'x' * 15), (FAR, 'Two two'), (FAR, 'Three')))
except BaseException as _e:
    _SOFT.append((7631, repr(_e)[:200]))
    print('FAIL line', 7631, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = FullMachine()
except BaseException as _e:
    _SOFT.append((7635, repr(_e)[:200]))
    print('FAIL line', 7635, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.lines = [(5, 'x' * 15), (9, 'Two two'), (12, 'Three')]
except BaseException as _e:
    _SOFT.append((7635, repr(_e)[:200]))
    print('FAIL line', 7635, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class AlbumLyricsMachine(CoverflowMachine):

    def __init__(self, **queue):
        super().__init__(**queue)
        self.wifi = 3
        self.restack = True
        self.accessed = []
        self.handlers[int(manifest['patch_symbols']['stock_playermore_trampoline'], 16)] = 'stock_playermore'
        self.handlers[syms['get_wifisignal']] = 'al:get_wifisignal'

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if name == 'c:access@GLIBC_2.0':
            self.accessed.append(self.text(u.reg_read(REGS[0])))
        if name != 'al:get_wifisignal':
            return super().hook(u, address, size, unused)
        self.returns(self.wifi, clobber=True)

    def playing(self):
        return self.items(self.get(syms['mcl_pdeqplaylist']))[0]

    def more(self, rows=8):
        self.stock = [self.node('list_item') for _ in range(rows)]
        view = self.node('scroll_view', 'scroll_view_more', list(self.stock))
        self.top = self.node('window', 'playermore_page', [view])
        assert self.call(address=HOOKS['playermore_page_init'][0], args=(self.top, 0, 0, 0), gap=0) == 0
        assert self.calls[0][:2] == ('stock_playermore', self.top)
        return self.nodes[view]['children']
try:
    m = AlbumLyricsMachine()
except BaseException as _e:
    _SOFT.append((7664, repr(_e)[:200]))
    print('FAIL line', 7664, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    kids = m.more()
except BaseException as _e:
    _SOFT.append((7664, repr(_e)[:200]))
    print('FAIL line', 7664, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    row = kids[4]
except BaseException as _e:
    _SOFT.append((7664, repr(_e)[:200]))
    print('FAIL line', 7664, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    air = m.nodes[m.nodes[kids[0]]['children'][0]]['children']
except BaseException as _e:
    _SOFT.append((7666, repr(_e)[:200]))
    print('FAIL line', 7666, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    about = m.nodes[m.nodes[kids[-1]]['children'][0]]['children']
except BaseException as _e:
    _SOFT.append((7668, repr(_e)[:200]))
    print('FAIL line', 7668, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    button = m.nodes[row]['children'][0]
except BaseException as _e:
    _SOFT.append((7670, repr(_e)[:200]))
    print('FAIL line', 7670, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    icon, label = m.nodes[button]['children']
except BaseException as _e:
    _SOFT.append((7670, repr(_e)[:200]))
    print('FAIL line', 7670, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = AlbumLyricsMachine(cls=241)
except BaseException as _e:
    _SOFT.append((7676, repr(_e)[:200]))
    print('FAIL line', 7676, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = AlbumLyricsMachine()
except BaseException as _e:
    _SOFT.append((7679, repr(_e)[:200]))
    print('FAIL line', 7679, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = AlbumLyricsMachine()
except BaseException as _e:
    _SOFT.append((7680, repr(_e)[:200]))
    print('FAIL line', 7680, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def album_lyrics_page(**queue):
    m = AlbumLyricsMachine(**queue)
    button = m.nodes[m.more()[4]]['children'][0]
    label = m.nodes[button]['children'][1]
    cue = m.song('Cue')
    m.word(cue + O['REC_CUE_END'], 90)
    m.found = [m.song('T1'), cue, m.song('T2')]

    def press():
        f, ctx = m.handler(button, O['EVT_CLICK'])
        assert m.call(address=f, args=(ctx, m.event, 0, 0), gap=0, count=5000000) == 0
    return (m, label, press)
try:
    m, label, press = album_lyrics_page()
except BaseException as _e:
    _SOFT.append((7688, repr(_e)[:200]))
    print('FAIL line', 7688, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.wifi = -1
except BaseException as _e:
    _SOFT.append((7689, repr(_e)[:200]))
    print('FAIL line', 7689, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.wifi = 3
except BaseException as _e:
    _SOFT.append((7689, repr(_e)[:200]))
    print('FAIL line', 7689, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    worker, arg = m.threads[-1]
except BaseException as _e:
    _SOFT.append((7691, repr(_e)[:200]))
    print('FAIL line', 7691, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, label, press = album_lyrics_page()
except BaseException as _e:
    _SOFT.append((7693, repr(_e)[:200]))
    print('FAIL line', 7693, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    worker, arg = m.threads[-1]
except BaseException as _e:
    _SOFT.append((7693, repr(_e)[:200]))
    print('FAIL line', 7693, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(label, O['EVT_DESTROY'])
except BaseException as _e:
    _SOFT.append((7695, repr(_e)[:200]))
    print('FAIL line', 7695, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.missing = False
except BaseException as _e:
    _SOFT.append((7696, repr(_e)[:200]))
    print('FAIL line', 7696, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    n = len(m.toasts)
except BaseException as _e:
    _SOFT.append((7698, repr(_e)[:200]))
    print('FAIL line', 7698, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    press_count = len(m.threads)
except BaseException as _e:
    _SOFT.append((7700, repr(_e)[:200]))
    print('FAIL line', 7700, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, label, press = album_lyrics_page()
except BaseException as _e:
    _SOFT.append((7701, repr(_e)[:200]))
    print('FAIL line', 7701, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    worker, arg = m.threads[-1]
except BaseException as _e:
    _SOFT.append((7701, repr(_e)[:200]))
    print('FAIL line', 7701, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, label, press = album_lyrics_page()
except BaseException as _e:
    _SOFT.append((7704, repr(_e)[:200]))
    print('FAIL line', 7704, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    worker, arg = m.threads[-1]
except BaseException as _e:
    _SOFT.append((7704, repr(_e)[:200]))
    print('FAIL line', 7704, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    n = len(m.toasts)
except BaseException as _e:
    _SOFT.append((7704, repr(_e)[:200]))
    print('FAIL line', 7704, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.found = []
except BaseException as _e:
    _SOFT.append((7707, repr(_e)[:200]))
    print('FAIL line', 7707, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def cf_jump(m, at, direction):
    letter = m.alloc(4)
    steps = m.call(address=payload_syms['coverflow_jump'], args=(m.slide, at, direction, letter), gap=0)
    return (steps, m.get(letter))
try:
    m = CoverflowMachine(albums=8)
except BaseException as _e:
    _SOFT.append((7715, repr(_e)[:200]))
    print('FAIL line', 7715, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine(albums=2)
except BaseException as _e:
    _SOFT.append((7731, repr(_e)[:200]))
    print('FAIL line', 7731, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine(albums=3)
except BaseException as _e:
    _SOFT.append((7735, repr(_e)[:200]))
    print('FAIL line', 7735, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def follow_share(m, k):
    m.u.reg_write(UC_MIPS_REG_F12, struct.unpack('<I', struct.pack('<f', k))[0])
    m.call(address=payload_syms['coverflow_follow'], args=(0, 0, 0, 0), gap=0, clear=False)
    return struct.unpack('<f', struct.pack('<I', m.u.reg_read(UC_MIPS_REG_F0) & 4294967295))[0]

def follow_to(m, w, ms):
    a, origin, goal, T = slide(m, w)
    m.word(a + O['ANIM_ELAPSED'], ms)
    m.word(w + O['SLIDE_OFFSET'], int(origin + (goal - origin) * follow_share(m, ms / T)))
    return signed(m.get(w + O['SLIDE_OFFSET']))

def follow_speed(m, w, ms):
    a, origin, goal, T = slide(m, w)
    return (goal - origin) * (follow_share(m, (ms + 1) / T) - follow_share(m, ms / T))

def follow_page():
    m = CoverflowMachine(albums=9)
    m.open()
    for kind in ('coverflow_step', 'coverflow_step_fast'):
        set_motion(m, kind, 81, O['EASE_FOLLOW'])
    m.slide_easing = 0
    return m
try:
    m = follow_page()
except BaseException as _e:
    _SOFT.append((7757, repr(_e)[:200]))
    print('FAIL line', 7757, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.slide
except BaseException as _e:
    _SOFT.append((7757, repr(_e)[:200]))
    print('FAIL line', 7757, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    index = m.get(w + O['SLIDE_INDEX'])
except BaseException as _e:
    _SOFT.append((7757, repr(_e)[:200]))
    print('FAIL line', 7757, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    a, origin, goal, T = slide(m, w)
except BaseException as _e:
    _SOFT.append((7759, repr(_e)[:200]))
    print('FAIL line', 7759, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    stride = -goal
except BaseException as _e:
    _SOFT.append((7759, repr(_e)[:200]))
    print('FAIL line', 7759, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    at = follow_to(m, w, 100)
except BaseException as _e:
    _SOFT.append((7764, repr(_e)[:200]))
    print('FAIL line', 7764, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    speed = follow_speed(m, w, 100)
except BaseException as _e:
    _SOFT.append((7764, repr(_e)[:200]))
    print('FAIL line', 7764, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    a2, origin, goal, T2 = slide(m, w)
except BaseException as _e:
    _SOFT.append((7766, repr(_e)[:200]))
    print('FAIL line', 7766, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = follow_page()
except BaseException as _e:
    _SOFT.append((7773, repr(_e)[:200]))
    print('FAIL line', 7773, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.slide
except BaseException as _e:
    _SOFT.append((7773, repr(_e)[:200]))
    print('FAIL line', 7773, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    cubic = m.call(address=syms['easing_get'], args=(O['EASE_CUBIC_OUT'], 0, 0, 0), gap=0)
except BaseException as _e:
    _SOFT.append((7774, repr(_e)[:200]))
    print('FAIL line', 7774, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    a = m.get(w + O['SLIDE_ANIMATOR'])
except BaseException as _e:
    _SOFT.append((7775, repr(_e)[:200]))
    print('FAIL line', 7775, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine(albums=3)
except BaseException as _e:
    _SOFT.append((7781, repr(_e)[:200]))
    print('FAIL line', 7781, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((7794, repr(_e)[:200]))
    print('FAIL line', 7794, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    under = m.node('window', 'artistinfo_page')
except BaseException as _e:
    _SOFT.append((7794, repr(_e)[:200]))
    print('FAIL line', 7794, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    row = m.node('list_item')
except BaseException as _e:
    _SOFT.append((7794, repr(_e)[:200]))
    print('FAIL line', 7794, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[6795044] = 'reset_poweroptions_timer'
except BaseException as _e:
    _SOFT.append((7810, repr(_e)[:200]))
    print('FAIL line', 7810, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = under
except BaseException as _e:
    _SOFT.append((7813, repr(_e)[:200]))
    print('FAIL line', 7813, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    artists = m.node('window', 'localclass_page')
except BaseException as _e:
    _SOFT.append((7817, repr(_e)[:200]))
    print('FAIL line', 7817, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    low = m.node('list_item')
except BaseException as _e:
    _SOFT.append((7818, repr(_e)[:200]))
    print('FAIL line', 7818, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine(albums=19)
except BaseException as _e:
    _SOFT.append((7825, repr(_e)[:200]))
    print('FAIL line', 7825, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    stride = m.call(address=syms['slide_menu_item_width'], args=(m.slide, 0, 0, 0), gap=0) + m.get(m.slide + O['SLIDE_SPACER'])
except BaseException as _e:
    _SOFT.append((7828, repr(_e)[:200]))
    print('FAIL line', 7828, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    a = slide(m, m.slide)[0]
except BaseException as _e:
    _SOFT.append((7834, repr(_e)[:200]))
    print('FAIL line', 7834, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine(albums=10)
except BaseException as _e:
    _SOFT.append((7840, repr(_e)[:200]))
    print('FAIL line', 7840, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    a = slide(m, m.slide)[0]
except BaseException as _e:
    _SOFT.append((7841, repr(_e)[:200]))
    print('FAIL line', 7841, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine(albums=3)
except BaseException as _e:
    _SOFT.append((7845, repr(_e)[:200]))
    print('FAIL line', 7845, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((7857, repr(_e)[:200]))
    print('FAIL line', 7857, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    page = m.open()
except BaseException as _e:
    _SOFT.append((7857, repr(_e)[:200]))
    print('FAIL line', 7857, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    covers = m.nodes[m.slide]['children']
except BaseException as _e:
    _SOFT.append((7859, repr(_e)[:200]))
    print('FAIL line', 7859, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.back, O['EVT_CLICK'])
except BaseException as _e:
    _SOFT.append((7863, repr(_e)[:200]))
    print('FAIL line', 7863, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    a, _, to, dur = slide(m, m.slide)
except BaseException as _e:
    _SOFT.append((7867, repr(_e)[:200]))
    print('FAIL line', 7867, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.clicks = []
except BaseException as _e:
    _SOFT.append((7873, repr(_e)[:200]))
    print('FAIL line', 7873, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.clicks = []
except BaseException as _e:
    _SOFT.append((7874, repr(_e)[:200]))
    print('FAIL line', 7874, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(covers[1], O['EVT_CLICK'])
except BaseException as _e:
    _SOFT.append((7878, repr(_e)[:200]))
    print('FAIL line', 7878, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rows = [w for w in m.nodes if m.nodes[w]['type'] == 'list_item' and m.alive(w)]
except BaseException as _e:
    _SOFT.append((7881, repr(_e)[:200]))
    print('FAIL line', 7881, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(rows[1], O['EVT_CLICK'])
except BaseException as _e:
    _SOFT.append((7883, repr(_e)[:200]))
    print('FAIL line', 7883, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    dq = m.plays[-1][1] & 4294967295
except BaseException as _e:
    _SOFT.append((7886, repr(_e)[:200]))
    print('FAIL line', 7886, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.missing = True
except BaseException as _e:
    _SOFT.append((7887, repr(_e)[:200]))
    print('FAIL line', 7887, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((7892, repr(_e)[:200]))
    print('FAIL line', 7892, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((7892, repr(_e)[:200]))
    print('FAIL line', 7892, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.room = False
except BaseException as _e:
    _SOFT.append((7897, repr(_e)[:200]))
    print('FAIL line', 7897, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.room = True
except BaseException as _e:
    _SOFT.append((7897, repr(_e)[:200]))
    print('FAIL line', 7897, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class ScanLockMachine(UiLoad, CoverflowMachine):

    def __init__(self, path):
        super().__init__()
        self.mock('strstr@GLIBC_2.0')
        self.db = sqlite3.connect(path, isolation_level=None, timeout=0)
        self.handlers.pop(HOOKS['scanAllMusicFile'][0] + 12)
        self.handlers.pop(syms['toolsGetUserStop'])
        for n in ('sqlite3_open', 'sqlite3_exec', 'sqlite3_close', 'sqlite3_free', 'sprintf@GLIBC_2.0', 'toolsClearDbCount', 'toolsLoadAllFile', 'usleep@GLIBC_2.0'):
            self.handlers[syms[n]] = 'lock:' + n
        self.sqls = []

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('lock:'):
            return super().hook(u, address, size, unused)
        a, b, c, d = [u.reg_read(r) for r in REGS]
        name = name[5:].split('@')[0]
        ret = 0
        self.calls.append((name, a, b, c))
        if name == 'sqlite3_open':
            assert self.text(a) == '/mnt/data/database.db'
            self.word(b, 123)
        elif name == 'sprintf':
            v = iter((c, d))
            u.mem_write(a, re.sub('%s', lambda _: self.text(next(v)), self.text(b)).encode() + b'\x00')
        elif name == 'usleep':
            self.reading.close()
            while self.threads:
                self.nested(*self.threads.pop(0))
        elif name == 'sqlite3_exec':
            assert a == 123
            self.sqls.append(self.text(b))
            err = self.get(u.reg_read(UC_MIPS_REG_SP) + 16)
            try:
                for sql in filter(str.strip, self.text(b).split(';')):
                    for row in self.db.execute(sql).fetchall():
                        values = self.alloc(4 * len(row))
                        for i, x in enumerate(row):
                            self.word(values + 4 * i, self.string(str(x)))
                        if c:
                            self.nested(c, d, len(row), values, 0)
                if err:
                    self.word(err, 0)
            except sqlite3.OperationalError as e:
                ret = 5 if 'locked' in str(e) else 1
                if err:
                    self.word(err, self.string(str(e)))
        self.returns(ret)
try:
    m = CoverflowMachine(cached=False)
except BaseException as _e:
    _SOFT.append((7947, repr(_e)[:200]))
    print('FAIL line', 7947, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    page = m.open()
except BaseException as _e:
    _SOFT.append((7947, repr(_e)[:200]))
    print('FAIL line', 7947, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(page, O['EVT_DESTROY'])
except BaseException as _e:
    _SOFT.append((7949, repr(_e)[:200]))
    print('FAIL line', 7949, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.freed = 0
except BaseException as _e:
    _SOFT.append((7949, repr(_e)[:200]))
    print('FAIL line', 7949, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def track(m, name, path, disc=0, no=0, cue=0):
    r = m.song(name)
    m.word(r + O['REC_PATH'], m.string(path))
    for k, v in (('REC_DISC', disc), ('REC_TRACK', no), ('REC_CUE_START', cue)):
        m.word(r + O[k], v)
    return r
try:
    COVER_ALBUM = ['Play Next', 'Play Last', 'Shuffle', 'Add to Favorites', 'Rate Album']
except BaseException as _e:
    _SOFT.append((7966, repr(_e)[:200]))
    print('FAIL line', 7966, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((7977, repr(_e)[:200]))
    print('FAIL line', 7977, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((7987, repr(_e)[:200]))
    print('FAIL line', 7987, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine(page='album_page', cls=O['CLASS_ALBUMS'], pos=2)
except BaseException as _e:
    _SOFT.append((7991, repr(_e)[:200]))
    print('FAIL line', 7991, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((7996, repr(_e)[:200]))
    print('FAIL line', 7996, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((7998, repr(_e)[:200]))
    print('FAIL line', 7998, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((8001, repr(_e)[:200]))
    print('FAIL line', 8001, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    PL_COVER_MENU = ['Play', 'Shuffle', 'Rename', 'Delete']
except BaseException as _e:
    _SOFT.append((8010, repr(_e)[:200]))
    print('FAIL line', 8010, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class PlaylistCoverMachine(CoverflowMachine):

    def __init__(self):
        super().__init__()
        self.handlers[syms['navigator_to_with_context']] = 'q:navigator_to_with_context'
        for n in ('playlist_art', 'playlist_art_refresh', 'playlist_art_stop', 'playlist_songs', 'playlists_new'):
            if n in payload_syms:
                self.handlers[payload_syms[n]] = 'pl:' + n
        self.handlers[syms['getAllPlayList']] = 'pl:getAllPlayList'
        self.edits = []
        self.lists = [self.song(f'List {i}', 4) for i in range(3)]
        self.heads = self.lists
        for i, r in enumerate(self.lists):
            self.word(r + O['REC_TITLE'], self.string(f'List {i}'))
            self.word(r + O['REC_DISC'], 10 + i)
        self.word(syms['p_deque_playlist'], self.deque(self.lists))
        self.under = self.node('window', 'playlist_page')
        self.stack = [self.under]
        self.top = self.under
        self.nodes[self.wm] = {'children': self.stack}
        assert self.call(address=payload_syms['coverflow_playlists'], args=(self.get(syms['p_deque_playlist']), 0, 0, 0), gap=0) == 1
        self.page = self.top
        self.slide = self.find('slide_menu')

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('pl:'):
            return super().hook(u, address, size, unused)
        if name == 'pl:playlists_new':
            self.edits.append(0)
        if name == 'pl:getAllPlayList':
            self.deqs[self.get(syms['tools_pdeq_directory'])][1] = [self.copy('stSongInfo', r) for r in self.heads]
            return self.returns(len(self.heads))
        self.returns(0)

def geom_boot(m):
    m.record_syscalls()
    m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'], 16)] = 'stock_ui_load'
    m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0], args=(m.string('system_bar'), 0, 0, 0), gap=0)
try:
    m = PlaylistCoverMachine()
except BaseException as _e:
    _SOFT.append((8036, repr(_e)[:200]))
    print('FAIL line', 8036, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rows = [m.nodes[m.nodes[i]['children'][0]]['text'] for i in m.nodes[m.view]['children'] if m.nodes[i]['type'] == 'list_item']
except BaseException as _e:
    _SOFT.append((8038, repr(_e)[:200]))
    print('FAIL line', 8038, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistCoverMachine()
except BaseException as _e:
    _SOFT.append((8040, repr(_e)[:200]))
    print('FAIL line', 8040, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class ClosingCoverMachine(PlaylistCoverMachine):

    def hook(self, u, address, size, unused):
        if address != syms['window_close']:
            return super().hook(u, address, size, unused)
        self.closing.append(u.reg_read(REGS[0]))
        self.returns(0, clobber=True)

def destroyed(m, w):
    if w in m.stack:
        m.stack.remove(w)
        m.top = m.stack[-1]
    for t, f, ctx in list(m.nodes[w].get('handlers', ())):
        if t == O['EVT_DESTROY']:
            m.call(address=f, args=(ctx, m.event, 0, 0), gap=0, clear=False)
try:
    m = ClosingCoverMachine()
except BaseException as _e:
    _SOFT.append((8057, repr(_e)[:200]))
    print('FAIL line', 8057, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.closing = []
except BaseException as _e:
    _SOFT.append((8058, repr(_e)[:200]))
    print('FAIL line', 8058, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    dlg = m.top
except BaseException as _e:
    _SOFT.append((8058, repr(_e)[:200]))
    print('FAIL line', 8058, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistCoverMachine()
except BaseException as _e:
    _SOFT.append((8063, repr(_e)[:200]))
    print('FAIL line', 8063, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def pl_front(m):
    f, ctx = m.handler(m.page, O['EVT_WINDOW_FOREGROUND'])
    m.call(address=f, args=(ctx, m.event, 0, 0), gap=0)
    m.advance(0)
    return m.find('slide_menu')
try:
    cover_cd = lambda m: m.get(m.call(address=payload_syms['coverflow_playlist'], args=(m.page, 0, 0, 0), gap=0) + O['REC_DISC'])
except BaseException as _e:
    _SOFT.append((8071, repr(_e)[:200]))
    print('FAIL line', 8071, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistCoverMachine()
except BaseException as _e:
    _SOFT.append((8072, repr(_e)[:200]))
    print('FAIL line', 8072, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.heads = m.lists[1:]
except BaseException as _e:
    _SOFT.append((8074, repr(_e)[:200]))
    print('FAIL line', 8074, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    s = pl_front(m)
except BaseException as _e:
    _SOFT.append((8074, repr(_e)[:200]))
    print('FAIL line', 8074, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.heads = []
except BaseException as _e:
    _SOFT.append((8076, repr(_e)[:200]))
    print('FAIL line', 8076, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    closes = sum((c[0] == 'window_manager_close_window_force' for c in m.calls))
except BaseException as _e:
    _SOFT.append((8076, repr(_e)[:200]))
    print('FAIL line', 8076, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlaylistCoverMachine()
except BaseException as _e:
    _SOFT.append((8080, repr(_e)[:200]))
    print('FAIL line', 8080, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    new_one = m.song('Renamed', 4)
except BaseException as _e:
    _SOFT.append((8081, repr(_e)[:200]))
    print('FAIL line', 8081, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.heads = [m.lists[0], new_one, m.lists[2]]
except BaseException as _e:
    _SOFT.append((8082, repr(_e)[:200]))
    print('FAIL line', 8082, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def pl_new(covers=True):

    class M(PlaylistPageMachine):

        def hook(self, u, address, size, unused):
            if address != payload_syms['coverflow_playlists_close']:
                return super().hook(u, address, size, unused)
            self.shut += 1
            page = 0
            if self.covers in self.stack:
                self.stack.remove(self.covers)
                self.top = page = self.stack[-1]
            self.returns(page)
    m = M()
    m.rebuild()
    m.paint(m.view)
    m.shut = 0
    m.page = m.top
    m.covers = m.node('window', 'coverflow_page')
    if covers:
        m.stack.append(m.covers)
        m.top = m.covers
    m.before = m.bar()
    m.calls.clear()
    m.call(address=payload_syms['playlists_new'], args=(0, 0, 0, 0), gap=0, clear=False)
    m.advance(0, clear=False)
    m.stack_rows()
    return m
try:
    m = pl_new()
except BaseException as _e:
    _SOFT.append((8100, repr(_e)[:200]))
    print('FAIL line', 8100, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    opened = [c for c in m.calls if c[0] == 'window_create']
except BaseException as _e:
    _SOFT.append((8100, repr(_e)[:200]))
    print('FAIL line', 8100, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = pl_new(covers=False)
except BaseException as _e:
    _SOFT.append((8102, repr(_e)[:200]))
    print('FAIL line', 8102, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ALBUM_RATES = '/mnt/data/ringnav-album-ratings'
except BaseException as _e:
    _SOFT.append((8108, repr(_e)[:200]))
    print('FAIL line', 8108, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class FileMocks:
    """stdio over self.files (path -> bytes): a write lands on fclose, then rename; fail_write refuses 'wb'."""
    FILE_FNS = ('fopen@GLIBC_2.2', 'fread@GLIBC_2.0', 'fwrite@GLIBC_2.0', 'fclose@GLIBC_2.2', 'rename@GLIBC_2.0')

    def file_mocks(self, prefix, files=None):
        self.files = dict(files or {})
        self.open_files = {}
        self.fail_write = False
        for n in self.FILE_FNS:
            self.handlers[syms[n]] = prefix + n

    def file_op(self, u, name, a, b, c, d):
        if name == 'fopen':
            path, mode = (self.text(a), self.text(b))
            if mode == 'wb' and (not self.fail_write) or (mode == 'rb' and path in self.files):
                f = 33554432 + 16 * next((i for i in range(64) if 33554432 + 16 * i not in self.open_files))
                self.open_files[f] = [path, b'', 0]
                return f
        elif name == 'fread':
            o = self.open_files[d]
            data = self.files[o[0]][o[2]:]
            n = min(c, len(data) // b)
            o[2] += n * b
            u.mem_write(a, data[:b * c].ljust(b * c, b'\x00'))
            return n
        elif name == 'fwrite':
            self.open_files[d][1] += bytes(u.mem_read(a, b * c))
            return c
        elif name == 'fclose':
            path, data, _ = self.open_files.pop(a)
            if data:
                self.files[path] = data
        elif name == 'rename':
            self.files[self.text(b)] = self.files.pop(self.text(a))
        return 0
try:
    USB_LUN = '/sys/kernel/config/usb_gadget/storage_demo/functions/mass_storage.msg/lun.0/file'
except BaseException as _e:
    _SOFT.append((8132, repr(_e)[:200]))
    print('FAIL line', 8132, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class UsbMachine(FileMocks, MsgMachine):

    def __init__(self):
        super().__init__()
        self.file_mocks('u:')
        self.widget_timers = True

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('u:'):
            return super().hook(u, address, size, unused)
        name = name[2:].split('@')[0]
        a, b, c, d = [u.reg_read(r) for r in REGS]
        self.calls.append((name, a, b, c))
        self.returns(self.file_op(u, name, a, b, c, d), clobber=True)
try:
    m, win, _, _, info, *_ = usb_page(0, light='0', machine=UsbMachine)
except BaseException as _e:
    _SOFT.append((8139, repr(_e)[:200]))
    print('FAIL line', 8139, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    said = lambda: {n.get('text') for n in m.nodes.values() if isinstance(n, dict) and n.get('visible', 1)} & {'Connected', 'OK to Disconnect'}
except BaseException as _e:
    _SOFT.append((8140, repr(_e)[:200]))
    print('FAIL line', 8140, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.files[USB_LUN] = b''
except BaseException as _e:
    _SOFT.append((8145, repr(_e)[:200]))
    print('FAIL line', 8145, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, *_ = usb_page(1, light='0', machine=UsbMachine)
except BaseException as _e:
    _SOFT.append((8146, repr(_e)[:200]))
    print('FAIL line', 8146, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class AlbumRateMachine(FileMocks, CoverflowMachine):

    def __init__(self, files=None, **kw):
        super().__init__(**kw)
        self.file_mocks('a:', files)

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('a:'):
            return super().hook(u, address, size, unused)
        name = name[2:].split('@')[0]
        a, b, c, d = [u.reg_read(r) for r in REGS]
        self.calls.append((name, a, b, c))
        ret = self.file_op(u, name, a, b, c, d)
        self.returns(ret, clobber=True)

    def kept(self):
        data = self.files.get(ALBUM_RATES, b'')
        return sorted((v for v in struct.unpack(f'<{len(data) // 4}I', data) if v))

    def aid(self, r):
        return self.call(address=payload_syms['album_id'], args=(r, 0, 0, 0), gap=0) & 4294967295

    def panel(self):
        p = [w for w in self.nodes[self.page]['children'] if self.nodes[w].get('name') == 'rate_panel']
        return p[0] if p and self.nodes[p[0]].get('visible', 1) else None

    def caption(self):
        return self.nodes[self.nodes[self.panel()]['children'][0]]['text']

    def hearts(self):
        return [self.nodes[h]['image'] for h in self.nodes[self.panel()]['children'][1:]]

    def menu(self):
        self.t = getattr(self, 't', 7000) + 1
        self.press(self.t)
        assert self.hold() == 11 and self.release() == 0
        rows = self.labels()
        f, ctx = self.handler(self.back, O['EVT_CLICK'])
        self.call(O['KEY_RETURN'], address=f, args=(ctx, self.event, 0, 0), gap=0)
        return rows

    def rate(self, want):
        toasts = len(self.toasts)
        assert self.run(4) is None and self.top == self.page and self.panel()
        for k in [O['KEY_PREV']] * 5 + [O['KEY_NEXT']] * want:
            assert self.call(k) == 11
        assert self.call(O['KEY_CENTER']) == 11 and self.panel() is None
        return self.toasts[-1][3] if len(self.toasts) > toasts else None

def song_side_effects(m):
    return '/mnt/data/ringnav-ratings' in m.files or any((c[0] in ('batch_add_file', 'deleteMusicFromFav') for c in m.calls))
try:
    m = AlbumRateMachine()
except BaseException as _e:
    _SOFT.append((8176, repr(_e)[:200]))
    print('FAIL line', 8176, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    a1 = m.aid(m.albums[1])
except BaseException as _e:
    _SOFT.append((8176, repr(_e)[:200]))
    print('FAIL line', 8176, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def rate_poll(m, left=5):
    m.byte(O['BC_IDLE'], left)
    m.handlers[HOOKS['main_loop_sleep_default'][0] + 12] = 'stock_sleep'
    m.call(address=HOOKS['main_loop_sleep_default'][0], args=(4660, 0, 0, 0), gap=0)
    return m.u.mem_read(O['BC_IDLE'], 1)[0]
try:
    rm = AlbumRateMachine()
except BaseException as _e:
    _SOFT.append((8183, repr(_e)[:200]))
    print('FAIL line', 8183, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def hold_pick(m, row):
    n = len(m.toasts)
    m.t = getattr(m, 't', 7000) + 1
    m.press(m.t)
    assert m.hold() == 11 and m.release() == 0
    m.pick(row)
    m.advance(0)
    return m.toasts[-1][3] if len(m.toasts) > n else None

def light_half(machine, light, **kw):
    SETUP[:] = [lambda k: (k.config.update(LIGHT=light), k)]
    try:
        fm = machine(**kw)
    finally:
        SETUP.clear()
    fm.mock('strstr@GLIBC_2.0')
    return fm

def bare_hold(m, row=None):
    if row is not None:
        m.word(m.row(row) + O['REC_TYPE'], 9)
    m.t = 7001
    m.press(m.t)
    return (m.hold(), m.release())

def fav_calls(m, name):
    return [c for c in m.calls if c[0] == name]
try:
    heart = m.nodes[m.panel()]['children'][2]
except BaseException as _e:
    _SOFT.append((8241, repr(_e)[:200]))
    print('FAIL line', 8241, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(heart, O['EVT_CLICK'])
except BaseException as _e:
    _SOFT.append((8241, repr(_e)[:200]))
    print('FAIL line', 8241, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    d, dctx = m.handler(heart, O['EVT_POINTER_DOWN'])
except BaseException as _e:
    _SOFT.append((8242, repr(_e)[:200]))
    print('FAIL line', 8242, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m2 = AlbumRateMachine(files={ALBUM_RATES: m.files[ALBUM_RATES]})
except BaseException as _e:
    _SOFT.append((8245, repr(_e)[:200]))
    print('FAIL line', 8245, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.stack[-1]
except BaseException as _e:
    _SOFT.append((8253, repr(_e)[:200]))
    print('FAIL line', 8253, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    full = b''.join((struct.pack('<I', i + 1 << 3 | 1) for i in range(O['RATE_SLOTS'])))
except BaseException as _e:
    _SOFT.append((8255, repr(_e)[:200]))
    print('FAIL line', 8255, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = AlbumRateMachine(files={ALBUM_RATES: full})
except BaseException as _e:
    _SOFT.append((8256, repr(_e)[:200]))
    print('FAIL line', 8256, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = AlbumRateMachine()
except BaseException as _e:
    _SOFT.append((8258, repr(_e)[:200]))
    print('FAIL line', 8258, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fail_write = True
except BaseException as _e:
    _SOFT.append((8258, repr(_e)[:200]))
    print('FAIL line', 8258, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def rec(album='Album', aa=None, path='/m/A/1.mp3', artist='Artist', rid=1):
    r = m.song('x')
    m.word(r + O['REC_ID'], rid & 4294967295)
    m.word(r + O['REC_PATH'], m.string(path))
    m.word(r + O['REC_ARTIST'], m.string(artist))
    m.word(r + O['REC_ALBUM'], m.string(album) if album is not None else 0)
    m.word(r + O['REC_ALBUM_ARTIST'], m.string(aa) if aa else 0)
    return r
try:
    same = [(rec(aa='Band'), rec(album='ALBUM', aa='band', path='/x/2.mp3', artist='Other')), (rec(path='/m/A/CD1/1.mp3'), rec(path='/m/A/Disc 2/1.mp3')), (rec(path='/m/A/1.mp3', artist='One'), rec(path='/m/A/2.mp3', artist='Two')), (rec(album='', path='/m/A/1.mp3'), rec(album=None, path='/m/A/2.mp3')), (rec(rid=-1, album='Unknown Album'), rec(rid=-1, album=None, path='/z/9.mp3'))]
except BaseException as _e:
    _SOFT.append((8270, repr(_e)[:200]))
    print('FAIL line', 8270, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    apart = [(rec(aa='Band'), rec(aa='Other')), (rec(path='/m/A/1.mp3'), rec(path='/m/B/1.mp3')), (rec(path='/m/A/1.mp3'), rec(path='/m/a/1.mp3')), (rec(), rec(album='Other')), (rec(aa='Band'), rec()), (rec(rid=-1), rec())]
except BaseException as _e:
    _SOFT.append((8275, repr(_e)[:200]))
    print('FAIL line', 8275, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def pushing(m, on=True):
    if on:
        set_motion(m, 'inplace', 120, 5)
    m.mock('widget_animator_prop_create', 'widget_animator_prop_set_params', 'widget_animator_start')
    return m

class FolderPush(FolderClickMachine):
    step = 0

    def hook(self, u, address, size, unused):
        if self.handlers.get(address, '') != 'stock_folder_step':
            return super().hook(u, address, size, unused)
        self.byte(syms['g_folder_layer'], self.u.mem_read(syms['g_folder_layer'], 1)[0] + self.step)
        self.returns(0, clobber=True)

def folder_push(on=True):
    m = FolderPush(['home_page', 'folder_page'], 1, False)
    m.top = asset_tree(m, 'folder_page.bin')
    m.stack[-1] = m.top
    for n in ('click', 'back'):
        m.handlers[int(manifest['patch_symbols'][f'stock_folder_{n}_trampoline'], 16)] = 'stock_folder_step'
    pushing(m, on)
    m.byte(syms['g_folder_layer'], 3)
    m.table = named(m, m.top, 'tableview_folder')
    w = m.node('button', 'row')
    m.word(w + O['ROW_INDEX'], 1)
    m.word(m.event + O['EVENT_TARGET'], w)
    return (m, w)

def folder_step(m, w, step, back=False):
    m.step = step
    m.calls = []
    if back:
        m.call(address=HOOKS['folder_back'][0], args=(0, 0, 0, 0), gap=0)
    else:
        m.call(address=payload_syms['ringnav_folder_click'], args=(w, m.event, 0, 0), gap=0)
try:
    m, w = folder_push()
except BaseException as _e:
    _SOFT.append((8303, repr(_e)[:200]))
    print('FAIL line', 8303, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, w = folder_push()
except BaseException as _e:
    _SOFT.append((8304, repr(_e)[:200]))
    print('FAIL line', 8304, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = pushing(CoverflowMachine())
except BaseException as _e:
    _SOFT.append((8308, repr(_e)[:200]))
    print('FAIL line', 8308, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.config['ARTISTCF'] = '2'
except BaseException as _e:
    _SOFT.append((8308, repr(_e)[:200]))
    print('FAIL line', 8308, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    dq = m.deque(m.albums[:2])
except BaseException as _e:
    _SOFT.append((8308, repr(_e)[:200]))
    print('FAIL line', 8308, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('window', 'artistinfo_page')
except BaseException as _e:
    _SOFT.append((8308, repr(_e)[:200]))
    print('FAIL line', 8308, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.stack = [m.top]
except BaseException as _e:
    _SOFT.append((8308, repr(_e)[:200]))
    print('FAIL line', 8308, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.page = m.top
except BaseException as _e:
    _SOFT.append((8309, repr(_e)[:200]))
    print('FAIL line', 8309, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    view = m.find('scroll_view')
except BaseException as _e:
    _SOFT.append((8309, repr(_e)[:200]))
    print('FAIL line', 8309, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.nodes[view]['children'][0], O['EVT_CLICK'])
except BaseException as _e:
    _SOFT.append((8310, repr(_e)[:200]))
    print('FAIL line', 8310, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = pushing(CoverflowMachine())
except BaseException as _e:
    _SOFT.append((8312, repr(_e)[:200]))
    print('FAIL line', 8312, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = pushing(CoverflowMachine())
except BaseException as _e:
    _SOFT.append((8313, repr(_e)[:200]))
    print('FAIL line', 8313, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.config['ARTISTCF'] = '2'
except BaseException as _e:
    _SOFT.append((8313, repr(_e)[:200]))
    print('FAIL line', 8313, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    dq = m.deque(m.albums[:2])
except BaseException as _e:
    _SOFT.append((8313, repr(_e)[:200]))
    print('FAIL line', 8313, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('window', 'artistinfo_page')
except BaseException as _e:
    _SOFT.append((8313, repr(_e)[:200]))
    print('FAIL line', 8313, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.stack = [m.top]
except BaseException as _e:
    _SOFT.append((8313, repr(_e)[:200]))
    print('FAIL line', 8313, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((8314, repr(_e)[:200]))
    print('FAIL line', 8314, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = AlbumRateMachine()
except BaseException as _e:
    _SOFT.append((8317, repr(_e)[:200]))
    print('FAIL line', 8317, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    dq = m.deque(m.albums[:2])
except BaseException as _e:
    _SOFT.append((8317, repr(_e)[:200]))
    print('FAIL line', 8317, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.node('window', 'artistinfo_page')
except BaseException as _e:
    _SOFT.append((8317, repr(_e)[:200]))
    print('FAIL line', 8317, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.stack = [m.top]
except BaseException as _e:
    _SOFT.append((8317, repr(_e)[:200]))
    print('FAIL line', 8317, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.page = m.top
except BaseException as _e:
    _SOFT.append((8318, repr(_e)[:200]))
    print('FAIL line', 8318, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.slide = m.find('slide_menu')
except BaseException as _e:
    _SOFT.append((8318, repr(_e)[:200]))
    print('FAIL line', 8318, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w = m.slide
except BaseException as _e:
    _SOFT.append((8322, repr(_e)[:200]))
    print('FAIL line', 8322, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    index = lambda: m.get(w + O['SLIDE_INDEX'])
except BaseException as _e:
    _SOFT.append((8322, repr(_e)[:200]))
    print('FAIL line', 8322, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    _, kick, goal, _ = slide(m, w)
except BaseException as _e:
    _SOFT.append((8323, repr(_e)[:200]))
    print('FAIL line', 8323, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    artist = m.stack[0]
except BaseException as _e:
    _SOFT.append((8329, repr(_e)[:200]))
    print('FAIL line', 8329, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.wm] = {'children': m.stack}
except BaseException as _e:
    _SOFT.append((8329, repr(_e)[:200]))
    print('FAIL line', 8329, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.page, O['EVT_KEY_UP'])
except BaseException as _e:
    _SOFT.append((8330, repr(_e)[:200]))
    print('FAIL line', 8330, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    calls = [c for c in m.calls if c[0] in ('window_manager_close_window_force', 'navigator_back')]
except BaseException as _e:
    _SOFT.append((8332, repr(_e)[:200]))
    print('FAIL line', 8332, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((8336, repr(_e)[:200]))
    print('FAIL line', 8336, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    old = m.open()
except BaseException as _e:
    _SOFT.append((8336, repr(_e)[:200]))
    print('FAIL line', 8336, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.page = m.top
except BaseException as _e:
    _SOFT.append((8338, repr(_e)[:200]))
    print('FAIL line', 8338, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.page, O['EVT_KEY_UP'])
except BaseException as _e:
    _SOFT.append((8342, repr(_e)[:200]))
    print('FAIL line', 8342, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((8350, repr(_e)[:200]))
    print('FAIL line', 8350, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.found = []
except BaseException as _e:
    _SOFT.append((8350, repr(_e)[:200]))
    print('FAIL line', 8350, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    top = m.top
except BaseException as _e:
    _SOFT.append((8350, repr(_e)[:200]))
    print('FAIL line', 8350, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class AllAlbums(CoverflowMachine):

    def hook(self, u, address, size, unused):
        if self.handlers.get(address) == 'q:getMusicByAlbum':
            self.found = self.by_album[self.text(u.reg_read(REGS[0]))]
        return super().hook(u, address, size, unused)
try:
    m = AlbumRateMachine(page='album_page', cls=O['CLASS_ALBUMS'])
except BaseException as _e:
    _SOFT.append((8379, repr(_e)[:200]))
    print('FAIL line', 8379, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.page = m.top
except BaseException as _e:
    _SOFT.append((8379, repr(_e)[:200]))
    print('FAIL line', 8379, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    a0 = m.aid(m.row(0))
except BaseException as _e:
    _SOFT.append((8379, repr(_e)[:200]))
    print('FAIL line', 8379, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = ArtistMachine(page='artistinfo_page', cls=65281, rows=2)
except BaseException as _e:
    _SOFT.append((8387, repr(_e)[:200]))
    print('FAIL line', 8387, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.split = [m.song('Baroness')]
except BaseException as _e:
    _SOFT.append((8388, repr(_e)[:200]))
    print('FAIL line', 8388, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class EraMachine(PlaylistEditMachine):

    def __init__(self, songs=()):
        super().__init__()
        self.mock('strstr@GLIBC_2.0')
        self.db.execute('create table songtable(id integer primary key,album,songer,albumsonger,fileurl,year,track,cd_num)')
        self.db.executemany('insert into songtable(album,songer,albumsonger,fileurl,year) values(?,?,?,?,?)', songs)

    def open_era(self):
        self.call(address=payload_syms['era_menu'], args=(0, self.event, 0, 0), gap=0)
        self.page = self.top
        return self.texts()

    def decade(self, i):
        row = self.nodes[self.find('scroll_view')]['children'][i]
        f, ctx = self.handler(row, O['EVT_CLICK'])
        self.call(address=f, args=(ctx, self.event, 0, 0), gap=0)
        self.page = self.top
        return self.texts()
try:
    SONGS = [('Alpha', 'A1', '', '/mnt/mmc/m/a/1.mp3', 1990), ('Beta', 'B1', 'Beta Band', '/mnt/mmc/m/b/1.mp3', 1998), ('Beta', 'B2', 'Beta Band', '/mnt/mmc/m/b/2.mp3', 1999), ('Gamma', 'C1', '', '/mnt/mmc/m/c/1.mp3', 2000), ('Delta', 'D1', '', '/mnt/mmc/m/d/1.mp3', 0), ('Eta', 'E1', '', '/mnt/mmc/m/e/1.mp3', 1989), ('Book', 'W1', '', '/mnt/mmc/audiobooks/w/1.mp3', 1995), ('Tale', 'T1', '', '/mnt/mmc/m/t.m4b', 1993), ('Pod', 'P1', '', '/mnt/mmc/podcasts/p/1.mp3', 1996), ('', 'U1', '', '/mnt/mmc/m/u.mp3', 1994)]
except BaseException as _e:
    _SOFT.append((8407, repr(_e)[:200]))
    print('FAIL line', 8407, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = EraMachine(SONGS)
except BaseException as _e:
    _SOFT.append((8412, repr(_e)[:200]))
    print('FAIL line', 8412, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    into = lambda: [m.nodes[m.nodes[r]['children'][-1]].get('image') == 'list_into' for r in m.nodes[m.find('scroll_view')]['children']]
except BaseException as _e:
    _SOFT.append((8414, repr(_e)[:200]))
    print('FAIL line', 8414, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = EraMachine(((f'Large {i // 10}', 'Artist', 'Artist', f'/mnt/mmc/music/large/{i}.flac', 2020) for i in range(65000)))
except BaseException as _e:
    _SOFT.append((8431, repr(_e)[:200]))
    print('FAIL line', 8431, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    RC_FILE = '/mnt/mmc/.ratings.tsv'
except BaseException as _e:
    _SOFT.append((8439, repr(_e)[:200]))
    print('FAIL line', 8439, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    RC_HEAD = '#song\thearts\tpath\tcue_start\n#album\thearts\tname\talbum_artist\tfolder\n'
except BaseException as _e:
    _SOFT.append((8439, repr(_e)[:200]))
    print('FAIL line', 8439, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class RatingsCopyMachine(FileMocks, EraMachine):

    def __init__(self, songs, favs=()):
        super().__init__()
        self.budget = 2000000
        self.file_mocks('rc:', {'/proc/mounts': b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n'})
        self.writes = 0
        self.db.execute('drop table songtable')
        for t in ('songtable', 'myfav'):
            self.db.execute(f'create table {t}(id integer primary key,fileurl,time,album,albumsonger)')
        self.db.executemany('insert into songtable(fileurl,time,album,albumsonger) values(?,?,?,?)', songs)
        self.db.executemany('insert into myfav(fileurl,time,album,albumsonger) values(?,?,?,?)', favs)

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('rc:'):
            return super().hook(u, address, size, unused)
        name = name[3:].split('@')[0]
        a, b, c, d = [u.reg_read(r) for r in REGS]
        self.writes += name == 'rename' and self.text(b) == RC_FILE
        self.returns(self.file_op(u, name, a, b, c, d), clobber=True)

    def rec(self, path, cue=0, album=None, artist=None):
        r = self.record((1, 0, path, cue, 0, 'x'))
        self.word(r + O['REC_ALBUM'], self.string(album) if album is not None else 0)
        self.word(r + O['REC_ALBUM_ARTIST'], self.string(artist) if artist is not None else 0)
        return r

    def key(self, f, *a, **k):
        return self.call(address=payload_syms[f], args=(self.rec(*a, **k), 0, 0, 0), gap=0) & 4294967295

    def rate(self, songs=(), albums=()):
        for path, rows in (('/mnt/data/ringnav-ratings', songs), ('/mnt/data/ringnav-album-ratings', albums)):
            self.files[path] = b''.join((struct.pack('<I', k & ~7 | h) for k, h in rows)).ljust(4 * O['RATE_SLOTS'], b'\x00')

    def poll(self, ms=5000):
        self.now += ms
        f = payload_syms['ratings_copy_poll']
        self.call(address=f, args=(0, 0, 0, 0), gap=0)
        while self.get(payload_syms['rc']):
            self.now += 1000
            self.call(address=f, args=(0, 0, 0, 0), gap=0)

    def copy_text(self):
        return self.files[RC_FILE].decode()
try:
    SONGS_RC = [('/mnt/mmc/m/a.flac', 0, 'Alpha', 'Band'), ('/mnt/mmc/m/a2.flac', 0, 'Alpha', 'Band'), ('/mnt/mmc/m/cue.flac', 0, 'Live', 'Band'), ('/mnt/mmc/m/cue.flac', 30, 'Live', 'Band'), ('/mnt/mmc/CD1/1.flac', 0, 'Solo', None), ('/mnt/mmc/CD2/1.flac', 0, 'Solo', None), ('/mnt/mmc/m/t\tab.flac', 0, 'Ta\tb', 'Ban\nd')]
except BaseException as _e:
    _SOFT.append((8463, repr(_e)[:200]))
    print('FAIL line', 8463, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    FAV_RC = [('/mnt/mmc/m/fav.flac', 0, 'Other', 'Band')]
except BaseException as _e:
    _SOFT.append((8466, repr(_e)[:200]))
    print('FAIL line', 8466, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = RatingsCopyMachine(SONGS_RC, FAV_RC)
except BaseException as _e:
    _SOFT.append((8467, repr(_e)[:200]))
    print('FAIL line', 8467, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    aa = m.key('album_id', '/mnt/mmc/m/a.flac', album='Alpha', artist='Band')
except BaseException as _e:
    _SOFT.append((8468, repr(_e)[:200]))
    print('FAIL line', 8468, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ab = m.key('album_id', '/mnt/mmc/CD2/1.flac', album='Solo', artist='')
except BaseException as _e:
    _SOFT.append((8468, repr(_e)[:200]))
    print('FAIL line', 8468, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    unknown = m.rec('/x')
except BaseException as _e:
    _SOFT.append((8469, repr(_e)[:200]))
    print('FAIL line', 8469, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    au = m.call(address=payload_syms['album_id'], args=(unknown, 0, 0, 0), gap=0) & 4294967295
except BaseException as _e:
    _SOFT.append((8469, repr(_e)[:200]))
    print('FAIL line', 8469, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    text = m.copy_text()
except BaseException as _e:
    _SOFT.append((8475, repr(_e)[:200]))
    print('FAIL line', 8475, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sql = len(m.sql)
except BaseException as _e:
    _SOFT.append((8480, repr(_e)[:200]))
    print('FAIL line', 8480, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    due = payload_syms['ratings_copy_due']
except BaseException as _e:
    _SOFT.append((8481, repr(_e)[:200]))
    print('FAIL line', 8481, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    mounts = m.files['/proc/mounts']
except BaseException as _e:
    _SOFT.append((8485, repr(_e)[:200]))
    print('FAIL line', 8485, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.files['/proc/mounts'] = b'none /mnt/data ubifs rw 0 0\n'
except BaseException as _e:
    _SOFT.append((8485, repr(_e)[:200]))
    print('FAIL line', 8485, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sql = len(m.sql)
except BaseException as _e:
    _SOFT.append((8486, repr(_e)[:200]))
    print('FAIL line', 8486, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.files['/proc/mounts'] = mounts
except BaseException as _e:
    _SOFT.append((8487, repr(_e)[:200]))
    print('FAIL line', 8487, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.now += 5000
except BaseException as _e:
    _SOFT.append((8489, repr(_e)[:200]))
    print('FAIL line', 8489, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f = payload_syms['ratings_copy_poll']
except BaseException as _e:
    _SOFT.append((8490, repr(_e)[:200]))
    print('FAIL line', 8490, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.files['/proc/mounts'] = b'none /mnt/data ubifs rw 0 0\n'
except BaseException as _e:
    _SOFT.append((8491, repr(_e)[:200]))
    print('FAIL line', 8491, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    writes = m.writes
except BaseException as _e:
    _SOFT.append((8491, repr(_e)[:200]))
    print('FAIL line', 8491, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.files['/proc/mounts'] = mounts
except BaseException as _e:
    _SOFT.append((8493, repr(_e)[:200]))
    print('FAIL line', 8493, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    writes = m.writes
except BaseException as _e:
    _SOFT.append((8496, repr(_e)[:200]))
    print('FAIL line', 8496, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f = payload_syms['ratings_copy_poll']
except BaseException as _e:
    _SOFT.append((8498, repr(_e)[:200]))
    print('FAIL line', 8498, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.now += 5000
except BaseException as _e:
    _SOFT.append((8498, repr(_e)[:200]))
    print('FAIL line', 8498, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.now += 1000
except BaseException as _e:
    _SOFT.append((8499, repr(_e)[:200]))
    print('FAIL line', 8499, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    kept = m.files[RC_FILE + '.1'].decode()
except BaseException as _e:
    _SOFT.append((8502, repr(_e)[:200]))
    print('FAIL line', 8502, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.now += 5000
except BaseException as _e:
    _SOFT.append((8504, repr(_e)[:200]))
    print('FAIL line', 8504, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f = payload_syms['ratings_copy_poll']
except BaseException as _e:
    _SOFT.append((8505, repr(_e)[:200]))
    print('FAIL line', 8505, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sql = len(m.sql)
except BaseException as _e:
    _SOFT.append((8506, repr(_e)[:200]))
    print('FAIL line', 8506, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    before = m.copy_text()
except BaseException as _e:
    _SOFT.append((8509, repr(_e)[:200]))
    print('FAIL line', 8509, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fail_write = True
except BaseException as _e:
    _SOFT.append((8509, repr(_e)[:200]))
    print('FAIL line', 8509, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    writes = m.writes
except BaseException as _e:
    _SOFT.append((8509, repr(_e)[:200]))
    print('FAIL line', 8509, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fail_write = False
except BaseException as _e:
    _SOFT.append((8510, repr(_e)[:200]))
    print('FAIL line', 8510, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    before = m.copy_text()
except BaseException as _e:
    _SOFT.append((8512, repr(_e)[:200]))
    print('FAIL line', 8512, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    writes = m.writes
except BaseException as _e:
    _SOFT.append((8512, repr(_e)[:200]))
    print('FAIL line', 8512, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fail = 'select'
except BaseException as _e:
    _SOFT.append((8512, repr(_e)[:200]))
    print('FAIL line', 8512, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    closed = m.closed_db
except BaseException as _e:
    _SOFT.append((8512, repr(_e)[:200]))
    print('FAIL line', 8512, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fail = ''
except BaseException as _e:
    _SOFT.append((8513, repr(_e)[:200]))
    print('FAIL line', 8513, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = RatingsCopyMachine(SONGS_RC, FAV_RC)
except BaseException as _e:
    _SOFT.append((8515, repr(_e)[:200]))
    print('FAIL line', 8515, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    before = m.copy_text()
except BaseException as _e:
    _SOFT.append((8515, repr(_e)[:200]))
    print('FAIL line', 8515, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fail = 'select 1 from sqlite_master'
except BaseException as _e:
    _SOFT.append((8516, repr(_e)[:200]))
    print('FAIL line', 8516, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    closed = m.closed_db
except BaseException as _e:
    _SOFT.append((8516, repr(_e)[:200]))
    print('FAIL line', 8516, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fail = ''
except BaseException as _e:
    _SOFT.append((8517, repr(_e)[:200]))
    print('FAIL line', 8517, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = RatingsCopyMachine(SONGS_RC, FAV_RC + [(f'/mnt/mmc/m/f{i}.flac', 0, '', '') for i in range(3)])
except BaseException as _e:
    _SOFT.append((8520, repr(_e)[:200]))
    print('FAIL line', 8520, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    full = m.copy_text()
except BaseException as _e:
    _SOFT.append((8520, repr(_e)[:200]))
    print('FAIL line', 8520, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def rc_change(sql=''):
    if sql:
        m.db.execute(sql)
    m.call(address=payload_syms['ratings_copy_due'], args=(0, 0, 0, 0), gap=0)
    m.poll()
try:
    one = m.copy_text()
except BaseException as _e:
    _SOFT.append((8525, repr(_e)[:200]))
    print('FAIL line', 8525, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fail_write = True
except BaseException as _e:
    _SOFT.append((8526, repr(_e)[:200]))
    print('FAIL line', 8526, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fail_write = False
except BaseException as _e:
    _SOFT.append((8526, repr(_e)[:200]))
    print('FAIL line', 8526, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    long = [(f'/mnt/mmc/{'x' * 1000}/{i}.flac', 0, '', '') for i in range(80)]
except BaseException as _e:
    _SOFT.append((8530, repr(_e)[:200]))
    print('FAIL line', 8530, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = RatingsCopyMachine(SONGS_RC, long)
except BaseException as _e:
    _SOFT.append((8531, repr(_e)[:200]))
    print('FAIL line', 8531, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.files[RC_FILE] = b'kept\n'
except BaseException as _e:
    _SOFT.append((8531, repr(_e)[:200]))
    print('FAIL line', 8531, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fail_realloc = True
except BaseException as _e:
    _SOFT.append((8531, repr(_e)[:200]))
    print('FAIL line', 8531, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fail_realloc = False
except BaseException as _e:
    _SOFT.append((8532, repr(_e)[:200]))
    print('FAIL line', 8532, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = RatingsCopyMachine([], FAV_RC)
except BaseException as _e:
    _SOFT.append((8534, repr(_e)[:200]))
    print('FAIL line', 8534, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    many = [(f'/mnt/mmc/l/{i}.flac', 0, '', '') for i in range(200)]
except BaseException as _e:
    _SOFT.append((8537, repr(_e)[:200]))
    print('FAIL line', 8537, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = RatingsCopyMachine(many)
except BaseException as _e:
    _SOFT.append((8538, repr(_e)[:200]))
    print('FAIL line', 8538, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    dates = [('Dates', 'Artist', '', '/mnt/mmc/music/d1.flac', 20220510), ('Dates', 'Artist', '', '/mnt/mmc/music/d2.flac', 2023), ('ISO', 'Artist', '', '/mnt/mmc/music/i.flac', '2021-05-10'), ('Month', 'Artist', '', '/mnt/mmc/music/m.flac', 202004), ('Bad', 'Artist', '', '/mnt/mmc/music/b.flac', 'unknown')]
except BaseException as _e:
    _SOFT.append((8541, repr(_e)[:200]))
    print('FAIL line', 8541, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = EraMachine(dates)
except BaseException as _e:
    _SOFT.append((8546, repr(_e)[:200]))
    print('FAIL line', 8546, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class EraPlay(EraMachine):

    def hook(self, u, address, size, unused):
        if self.handlers.get(address) == 'q:getMusicByAlbum':
            self.found = self.by_album.get(self.text(u.reg_read(REGS[0])), [])
        return super().hook(u, address, size, unused)

def era_track(m, album, aa, *t):
    r = track(m, *t)
    m.word(r + O['REC_ALBUM'], m.string(album))
    m.word(r + O['REC_ALBUM_ARTIST'], m.string(aa))
    return r
try:
    m = EraPlay(SONGS)
except BaseException as _e:
    _SOFT.append((8556, repr(_e)[:200]))
    print('FAIL line', 8556, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.by_album = {'Alpha': [era_track(m, 'Alpha', '', 'a1', '/mnt/mmc/m/a/1.mp3', 1, 1)], 'Beta': [era_track(m, 'Beta', 'Beta Band', 'b2', '/mnt/mmc/m/b/2.mp3', 1, 2), era_track(m, 'Beta', 'Beta Band', 'b1', '/mnt/mmc/m/b/1.mp3', 1, 1)]}
except BaseException as _e:
    _SOFT.append((8556, repr(_e)[:200]))
    print('FAIL line', 8556, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    albums = m.page
except BaseException as _e:
    _SOFT.append((8558, repr(_e)[:200]))
    print('FAIL line', 8558, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    row = lambda i: m.handler(m.nodes[m.find('scroll_view', albums)]['children'][i], O['EVT_CLICK'])
except BaseException as _e:
    _SOFT.append((8559, repr(_e)[:200]))
    print('FAIL line', 8559, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = row(0)
except BaseException as _e:
    _SOFT.append((8560, repr(_e)[:200]))
    print('FAIL line', 8560, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = row(3)
except BaseException as _e:
    _SOFT.append((8562, repr(_e)[:200]))
    print('FAIL line', 8562, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.page = m.top
except BaseException as _e:
    _SOFT.append((8562, repr(_e)[:200]))
    print('FAIL line', 8562, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    paths = ('/mnt/mmc/Music/z4dmr4cvs8lw.flac', '/mnt/mmc/Music/mhf1gqxzbiuf.flac')
except BaseException as _e:
    _SOFT.append((8565, repr(_e)[:200]))
    print('FAIL line', 8565, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = EraMachine([('Old', 'Artist', 'Artist', paths[0], 1990), ('New', 'Artist', 'Artist', paths[1], 2020)])
except BaseException as _e:
    _SOFT.append((8566, repr(_e)[:200]))
    print('FAIL line', 8566, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.found = [m.song('old'), m.song('new')]
except BaseException as _e:
    _SOFT.append((8567, repr(_e)[:200]))
    print('FAIL line', 8567, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.nodes[m.find('scroll_view')]['children'][1], O['EVT_CLICK'])
except BaseException as _e:
    _SOFT.append((8571, repr(_e)[:200]))
    print('FAIL line', 8571, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = EraMachine()
except BaseException as _e:
    _SOFT.append((8574, repr(_e)[:200]))
    print('FAIL line', 8574, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    top = m.top
except BaseException as _e:
    _SOFT.append((8574, repr(_e)[:200]))
    print('FAIL line', 8574, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = EraMachine([s for s in SONGS if s[4] == 0])
except BaseException as _e:
    _SOFT.append((8575, repr(_e)[:200]))
    print('FAIL line', 8575, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    top = m.top
except BaseException as _e:
    _SOFT.append((8575, repr(_e)[:200]))
    print('FAIL line', 8575, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def ratings_albums(m):
    m.call(address=payload_syms['ratings_menu'], args=(0, m.event, 0, 0), gap=0)
    menu_page = m.top
    assert m.nodes[menu_page]['name'] == 'ratings_page'
    row = m.nodes[m.find('scroll_view', menu_page)]['children'][2]
    f, ctx = m.handler(row, O['EVT_CLICK'])
    m.call(address=f, args=(ctx, m.event, 0, 0), gap=0, count=50000000)
    m.advance(0)
    return menu_page
try:
    m = AlbumRateMachine()
except BaseException as _e:
    _SOFT.append((8583, repr(_e)[:200]))
    print('FAIL line', 8583, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ids = [m.aid(r) for r in m.albums]
except BaseException as _e:
    _SOFT.append((8583, repr(_e)[:200]))
    print('FAIL line', 8583, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    data = b''.join((struct.pack('<I', ids[i] & ~7 | h) for i, h in ((0, 2), (2, 5)))).ljust(4 * O['RATE_SLOTS'], b'\x00')
except BaseException as _e:
    _SOFT.append((8585, repr(_e)[:200]))
    print('FAIL line', 8585, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = AlbumRateMachine(files={ALBUM_RATES: data})
except BaseException as _e:
    _SOFT.append((8586, repr(_e)[:200]))
    print('FAIL line', 8586, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    menu_page = ratings_albums(m)
except BaseException as _e:
    _SOFT.append((8586, repr(_e)[:200]))
    print('FAIL line', 8586, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.page = m.top
except BaseException as _e:
    _SOFT.append((8587, repr(_e)[:200]))
    print('FAIL line', 8587, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.slide = m.find('slide_menu')
except BaseException as _e:
    _SOFT.append((8587, repr(_e)[:200]))
    print('FAIL line', 8587, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def shown(m, i):
    m.word(m.slide + O['SLIDE_INDEX'], i)
    f, ctx = m.handler(m.slide, O['EVT_VALUE_CHANGED'])
    m.call(address=f, args=(ctx, m.event, 0, 0), gap=0)
    return m.texts()[0]
try:
    f, ctx = m.handler(m.nodes[m.slide]['children'][2], O['EVT_CLICK'])
except BaseException as _e:
    _SOFT.append((8591, repr(_e)[:200]))
    print('FAIL line', 8591, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.slide = m.find('slide_menu')
except BaseException as _e:
    _SOFT.append((8592, repr(_e)[:200]))
    print('FAIL line', 8592, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.stack[-1]
except BaseException as _e:
    _SOFT.append((8600, repr(_e)[:200]))
    print('FAIL line', 8600, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = AlbumRateMachine()
except BaseException as _e:
    _SOFT.append((8604, repr(_e)[:200]))
    print('FAIL line', 8604, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sort_name = lambda: m.text(m.call(address=payload_syms['coverflow_sort_name'], args=(0, 0, 0, 0), gap=0) & 4294967295)
except BaseException as _e:
    _SOFT.append((8605, repr(_e)[:200]))
    print('FAIL line', 8605, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.slide = m.find('slide_menu')
except BaseException as _e:
    _SOFT.append((8610, repr(_e)[:200]))
    print('FAIL line', 8610, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine(cls=O['CLASS_ALBUMS'])
except BaseException as _e:
    _SOFT.append((8627, repr(_e)[:200]))
    print('FAIL line', 8627, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    view = m.tracks()
except BaseException as _e:
    _SOFT.append((8627, repr(_e)[:200]))
    print('FAIL line', 8627, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine(queue=0, cls=O['CLASS_ALBUMS'])
except BaseException as _e:
    _SOFT.append((8630, repr(_e)[:200]))
    print('FAIL line', 8630, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine(mode=2)
except BaseException as _e:
    _SOFT.append((8632, repr(_e)[:200]))
    print('FAIL line', 8632, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((8636, repr(_e)[:200]))
    print('FAIL line', 8636, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    view = m.tracks()
except BaseException as _e:
    _SOFT.append((8636, repr(_e)[:200]))
    print('FAIL line', 8636, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.top, O['EVT_KEY_UP'])
except BaseException as _e:
    _SOFT.append((8637, repr(_e)[:200]))
    print('FAIL line', 8637, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ev = m.alloc(64)
except BaseException as _e:
    _SOFT.append((8637, repr(_e)[:200]))
    print('FAIL line', 8637, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((8650, repr(_e)[:200]))
    print('FAIL line', 8650, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.slide, O['EVT_VALUE_CHANGED'])
except BaseException as _e:
    _SOFT.append((8652, repr(_e)[:200]))
    print('FAIL line', 8652, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    view = m.tracks(0)
except BaseException as _e:
    _SOFT.append((8655, repr(_e)[:200]))
    print('FAIL line', 8655, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    other = m.tracks(1)
except BaseException as _e:
    _SOFT.append((8656, repr(_e)[:200]))
    print('FAIL line', 8656, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    view = m.tracks(0)
except BaseException as _e:
    _SOFT.append((8657, repr(_e)[:200]))
    print('FAIL line', 8657, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    view = m.tracks(0)
except BaseException as _e:
    _SOFT.append((8658, repr(_e)[:200]))
    print('FAIL line', 8658, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((8661, repr(_e)[:200]))
    print('FAIL line', 8661, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    long = 'A very long title or artist name ' * 8
except BaseException as _e:
    _SOFT.append((8661, repr(_e)[:200]))
    print('FAIL line', 8661, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    view = m.tracks()
except BaseException as _e:
    _SOFT.append((8664, repr(_e)[:200]))
    print('FAIL line', 8664, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    row = m.nodes[m.nodes[view]['children'][0]]['children'][0]
except BaseException as _e:
    _SOFT.append((8665, repr(_e)[:200]))
    print('FAIL line', 8665, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    labels = [w for w, n in m.nodes.items() if m.alive(w) and n.get('text') == long]
except BaseException as _e:
    _SOFT.append((8666, repr(_e)[:200]))
    print('FAIL line', 8666, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((8671, repr(_e)[:200]))
    print('FAIL line', 8671, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from ipod import corner_inset

def cf_geometry(m, w):
    return tuple((signed(m.get(w + O[k])) for k in ('W_X', 'W_Y', 'W_W', 'W_H')))

def clear(x, top, w, px):
    return max(corner_inset(top), corner_inset(top + px)) <= x and x + w <= 375 - max(corner_inset(top), corner_inset(top + px))
try:
    OLED = theme_builtins()[0]
except BaseException as _e:
    _SOFT.append((8683, repr(_e)[:200]))
    print('FAIL line', 8683, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = CoverflowMachine()
except BaseException as _e:
    _SOFT.append((8684, repr(_e)[:200]))
    print('FAIL line', 8684, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.found = [m.song(f'T{i}') for i in range(12)]
except BaseException as _e:
    _SOFT.append((8684, repr(_e)[:200]))
    print('FAIL line', 8684, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'], 16)] = 'stock_ui_load'
except BaseException as _e:
    _SOFT.append((8685, repr(_e)[:200]))
    print('FAIL line', 8685, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    BAR = 320 - m.call(address=payload_syms['client_h'], args=(0, 0, 0, 0), gap=0)
except BaseException as _e:
    _SOFT.append((8687, repr(_e)[:200]))
    print('FAIL line', 8687, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    page = m.open()
except BaseException as _e:
    _SOFT.append((8688, repr(_e)[:200]))
    print('FAIL line', 8688, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    labels = [w for w in m.nodes[m.get(m.slide + O['W_PARENT'])]['children'] if m.nodes[w]['type'] == 'hscroll_label']
except BaseException as _e:
    _SOFT.append((8688, repr(_e)[:200]))
    print('FAIL line', 8688, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    name, artist = labels
except BaseException as _e:
    _SOFT.append((8689, repr(_e)[:200]))
    print('FAIL line', 8689, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    E = O['CF_EDGE']
except BaseException as _e:
    _SOFT.append((8690, repr(_e)[:200]))
    print('FAIL line', 8690, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    y = OLED['cf.text_y']
except BaseException as _e:
    _SOFT.append((8690, repr(_e)[:200]))
    print('FAIL line', 8690, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ay = y + OLED['cf.artist_y']
except BaseException as _e:
    _SOFT.append((8690, repr(_e)[:200]))
    print('FAIL line', 8690, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    view = m.tracks()
except BaseException as _e:
    _SOFT.append((8697, repr(_e)[:200]))
    print('FAIL line', 8697, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    lv = m.get(view + O['W_PARENT'])
except BaseException as _e:
    _SOFT.append((8697, repr(_e)[:200]))
    print('FAIL line', 8697, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    title = next((w for w in m.nodes[m.get(lv + O['W_PARENT'])]['children'] if m.nodes[w]['type'] == 'hscroll_label'))
except BaseException as _e:
    _SOFT.append((8697, repr(_e)[:200]))
    print('FAIL line', 8697, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    X, ROW, px = (OLED['page_x'], OLED['page_row'], OLED['font_px[ROLE_TITLE]'])
except BaseException as _e:
    _SOFT.append((8699, repr(_e)[:200]))
    print('FAIL line', 8699, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    fit = m.call(address=payload_syms['list_fit_end'], args=(0, 0, 0, 0), gap=0)
except BaseException as _e:
    _SOFT.append((8699, repr(_e)[:200]))
    print('FAIL line', 8699, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class RolloverMachine(CoverflowMachine):

    def __init__(self, sizes=(2, 2, 1, 1)):
        super().__init__(queue=0, cls=1)
        self.handlers.pop(syms['player_mainscheduling'])
        self.native_trace = {syms[n]: n for n in ('mclAutoChange', 'mclNextSong', 'on_player_autochange', 'toolsLoadNextDir', 'player_refresh_playqueue', 'mclLoadPlayList', 'player_get_id3info', 'player_set_coverinfo', 'mclClearChangeFlag')}
        self.native_trace[syms['open@GLIBC_2.0']] = 'press_open'
        self.mock('open@GLIBC_2.0', 'write@GLIBC_2.0', 'close@GLIBC_2.0')
        self.trace = []
        self.files = {}
        self.dirs = {}
        self.artfiles = set()
        self.worker = False
        self.forks = 0
        self.handlers[6116884] = 'r:directory'
        self.handlers[6117604] = 'r:directory'
        self.handlers[6026500] = 'r:decoder_start'
        self.word(10723560, 16777236)
        self.handlers[16777236] = 'r:malloc'
        for name in ('strcpy@GLIBC_2.0', 'strncmp@GLIBC_2.0', 'strstr@GLIBC_2.0', 'strchr@GLIBC_2.0', 'toolsGetMusicInfo', 'toolsGetFileSize', 'toolsGetAlbumCover', 'toolsGetExternCover', 'access@GLIBC_2.0', 'remove@GLIBC_2.0', 'usleep@GLIBC_2.0', 'fork@GLIBC_2.0'):
            self.handlers[syms[name]] = 'r:' + name.split('@')[0]
        self.mock('pthread_mutex_lock@GLIBC_2.0', 'pthread_mutex_unlock@GLIBC_2.0', 'toolsFreeStSongInfo', 'mclLoadExLyric', 'sendBtHeadsetPlayStatus', 'notifyPlayInfo', 'notifyPlayStatus', 'notifyRefreshLyric', 'dmrNotifyPlayStatus', 'dlnaRenderSaveUrlMetadata', 'reset_repeatinfo', 'initializeDmrQCurrentInfo', 'player_reconfig', 'add_playrecord', 'toolsTrimLeft')
        self.handlers.pop(syms['mclStartPlayer'], None)
        for i, n in enumerate(sizes):
            folder = f'/mnt/mmc/{i}'
            entries = []
            for j in range(n):
                path = f'{folder}/{j}.flac'
                r = self.song(f'{j}.flac')
                self.word(r + O['REC_PATH'], self.string(path))
                self.word(r + 56, 1)
                for off in ('REC_ALBUM', 'REC_ARTIST'):
                    self.word(r + O[off], 0)
                entries.append(r)
                self.files[path] = (f'Album {i}' if i != 2 else '', i != 2)
            self.dirs[folder] = entries
        self.dirs['/mnt/mmc'] = []
        for folder in list(self.dirs)[:-1]:
            r = self.song(folder.rsplit('/', 1)[1], 4)
            self.word(r + O['REC_PATH'], self.string(folder))
            self.dirs['/mnt/mmc'].append(r)
        self.word(10813312, syms['on_player_autochange'])
        self.byte(10813483, 1)
        self.call(address=syms['mclLoadPlayList'], args=(self.deque(self.dirs['/mnt/mmc/0']), 0, 1, 0), gap=0)
        self.call(address=syms['mclStartPlayer'], args=(0, 0, 0, 0), gap=0)
        self.call(address=syms['player_get_id3info'], args=(0, 0, 0, 0), gap=0)
        self.handlers.pop(payload_syms['press_stamp'])

    def hook(self, u, address, size, unused):
        if address in getattr(self, 'native_trace', {}):
            self.trace.append((self.native_trace[address], None))
        name = self.handlers.get(address, '')
        if name == 'widget_load_image':
            path = self.text(u.reg_read(UC_MIPS_REG_A1))
            self.image_size = None if path.startswith('file://') and path[7:] not in self.artfiles else (50, 50)
        if not name.startswith('r:'):
            return super().hook(u, address, size, unused)
        name = name[2:]
        a, b, c, d = [u.reg_read(r) for r in REGS]
        ret = 0
        self.trace.append((name, self.text(a) if name in ('directory', 'decoder_start', 'toolsGetAlbumCover') else a))
        if name == 'directory':
            path = self.text(a)
            self.deqs[self.get(syms['tools_pdeq_directory'])][1] = [self.copy('stSongInfo', r) for r in self.dirs.get(path, [])]
            ret = len(self.dirs.get(path, []))
        elif name == 'decoder_start':
            self.byte(10813485, 1)
        elif name == 'malloc':
            ret = self.alloc(a + 3 & ~3)
        elif name == 'strcpy':
            u.mem_write(a, self.text(b).encode() + b'\x00')
            ret = a
        elif name == 'strncmp':
            x, y = (self.text(a)[:c], self.text(b)[:c])
            ret = (x > y) - (x < y)
        elif name in ('strchr', 'strstr'):
            i = self.text(a).find(chr(b) if name == 'strchr' else self.text(b))
            ret = a + i if i >= 0 else 0
        elif name == 'toolsGetMusicInfo':
            path = self.text(c)
            album, _ = self.files[path]
            self.trace.append(('tags', path))
            if album:
                u.mem_write(b, album.encode() + b'\x00')
                u.mem_write(b + 256, b'Artist\x00')
                u.mem_write(b + 640, path.rsplit('/', 1)[1].encode() + b'\x00')
            ret = int(bool(album))
        elif name == 'toolsGetFileSize':
            ret = 4096
        elif name == 'toolsGetAlbumCover':
            ret = int(self.files[self.text(a)][1])
            if ret:
                self.artfiles.add(self.text(b))
        elif name == 'access':
            ret = 0 if self.text(a) in self.artfiles else -1
        elif name == 'remove':
            self.artfiles.discard(self.text(a))
        elif name == 'fork':
            self.forks += 1
            ret = -1
        elif name == 'usleep' and self.worker:
            u.reg_write(UC_MIPS_REG_PC, 16777216)
            u.emu_stop()
            return
        self.returns(ret, clobber=True)

    def tick(self):
        self.call(address=syms['player_mainscheduling'], args=(0, 0, 0, 0), gap=0)

    def artwork(self):
        self.worker = True
        self.u.reg_write(UC_MIPS_REG_SP, 1879109632)
        self.u.reg_write(UC_MIPS_REG_T9, syms['player_parsecover_thd'])
        self.u.emu_start(syms['player_parsecover_thd'], 16777216, count=self.budget)
        assert self.u.reg_read(UC_MIPS_REG_PC) == 16777216
        self.worker = False

    def ui(self):
        self.top = self.win
        self.call(address=5494800, args=(self.info, 0, 0, 0), gap=0)
        self.paint(self.win, gap=0)
        self.top = self.home
        self.paint(self.home, gap=0)
        self.top = self.win

    def setup_ui(self):
        bar = self.node('window', 'system_bar')
        self.word(syms['system_bar'], bar)
        self.word(bar + O['W_PARENT'], self.wm)
        home_list(self)
        self.home = self.top
        self.art = named(self, self.home, 'img_homeart')
        self.call(address=home_hook[0], args=(self.home, 0, 0, 0), gap=0)
        self.cover = self.node('image', 'img_cover')
        self.songlabel = self.node('hscroll_label', 'scrlabel_title')
        self.albumlabel = self.node('label', 'label_ipod_album')
        self.poslabel = self.node('label', 'label_ipod_pos')
        self.win = self.node('window', 'playing_page', [self.cover, self.songlabel, self.albumlabel, self.poslabel])
        self.word(self.win + O['W_PARENT'], self.wm)
        self.top = self.win
        self.info = self.alloc(64)
        self.word(self.info + 32, self.win)
        playing = HOOKS['playing_page_init'][0]
        self.handlers[playing + 12] = 'stock_playing'
        self.call(address=playing, args=(self.win, 0, 0, 0), gap=0)
        self.byte(syms['g_forcerefresh_flag'], 1)
from build import PLAYER_COVER
from build import ID3_PATH
try:
    m = RolloverMachine((1, 1))
except BaseException as _e:
    _SOFT.append((8854, repr(_e)[:200]))
    print('FAIL line', 8854, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    r = m.items(m.get(syms['mcl_pdeqplaylist']))[0]
except BaseException as _e:
    _SOFT.append((8855, repr(_e)[:200]))
    print('FAIL line', 8855, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    id3 = syms['g_play_id3_info']
except BaseException as _e:
    _SOFT.append((8855, repr(_e)[:200]))
    print('FAIL line', 8855, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = RolloverMachine((1, 1))
except BaseException as _e:
    _SOFT.append((8861, repr(_e)[:200]))
    print('FAIL line', 8861, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    r = m.items(m.get(syms['mcl_pdeqplaylist']))[0]
except BaseException as _e:
    _SOFT.append((8862, repr(_e)[:200]))
    print('FAIL line', 8862, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from build import TAG_TEXT, GENRE_COPY, CUE_TEXT, SCAN_DIRS

class TagTextMachine(Machine):

    def __init__(self):
        super().__init__()
        self.word(10723560, 16777236)
        self.handlers[16777236] = 't:malloc'
        self.word(10723952, 16777232)
        self.handlers[16777232] = 't:free'
        self.handlers[syms['strcpy@GLIBC_2.0']] = 't:strcpy'
        self.handlers[syms['toolsIconvConvert']] = 't:iconv'
        self.handlers[syms['strcmp@GLIBC_2.0']] = 't:strcmp'
        self.mock('snprintf@GLIBC_2.0')
        self.iconv = 0
        self.handlers.pop(syms['toolsFixId3ErrorCode'])

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('t:'):
            return super().hook(u, address, size, unused)
        a, b, c = [u.reg_read(r) for r in REGS[:3]]
        ret = 0
        if name == 't:malloc':
            ret = self.alloc(a + 3 & ~3)
        elif name == 't:strcpy':
            u.mem_write(a, self.raw(b) + b'\x00')
            ret = a
        elif name == 't:strcmp':
            x, y = (self.raw(a), self.raw(b))
            ret = (x > y) - (x < y)
        elif name == 't:iconv':
            self.iconv += 1
            try:
                u.mem_write(a, self.raw(a).decode(self.text(c)).encode(self.text(b)) + b'\x00')
                ret = 1
            except UnicodeError:
                pass
        self.returns(ret, clobber=True)
try:
    genres = ['Sludge Metal', 'Alternative Metal', 'Country', 'Death Metal', 'Gothic Rock', 'Groove Metal', 'Grunge', 'Metal', 'Rock', 'Stoner Metal', 'Alternative Country', 'Gothic Country']
except BaseException as _e:
    _SOFT.append((8907, repr(_e)[:200]))
    print('FAIL line', 8907, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    joined = ' / '.join(genres)
except BaseException as _e:
    _SOFT.append((8910, repr(_e)[:200]))
    print('FAIL line', 8910, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((8911, repr(_e)[:200]))
    print('FAIL line', 8911, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bad = m.alloc(256)
except BaseException as _e:
    _SOFT.append((8924, repr(_e)[:200]))
    print('FAIL line', 8924, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    out = m.alloc(256)
except BaseException as _e:
    _SOFT.append((8925, repr(_e)[:200]))
    print('FAIL line', 8925, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((8939, repr(_e)[:200]))
    print('FAIL line', 8939, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    dot = m.string('.')
except BaseException as _e:
    _SOFT.append((8939, repr(_e)[:200]))
    print('FAIL line', 8939, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from build import LYRIC_FOLLOW
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((8946, repr(_e)[:200]))
    print('FAIL line', 8946, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rows = [m.node('label') for _ in range(12)]
except BaseException as _e:
    _SOFT.append((8946, repr(_e)[:200]))
    print('FAIL line', 8946, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    lrc = m.node('scroll_view', 'scroll_lrc', rows)
except BaseException as _e:
    _SOFT.append((8949, repr(_e)[:200]))
    print('FAIL line', 8949, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ys = []
except BaseException as _e:
    _SOFT.append((8950, repr(_e)[:200]))
    print('FAIL line', 8950, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from build import HSCROLL_LULL
from build import LYRIC_ROWS

class LyricRowsMachine(Machine):

    def __init__(self):
        super().__init__()
        self.handlers[5486204] = 'rows'

    def hook(self, u, address, size, unused):
        if self.handlers.get(address) != 'rows':
            return super().hook(u, address, size, unused)
        self.word(self.lrc + O['SCROLL_Y'], 0)
        self.returns(0, clobber=True)
from build import GRID_COVER

class GridCoverMachine(Machine):
    MOCKS = ('toolsGetAlbumCover', 'toolsGetExternCover', 'fork@GLIBC_2.0', 'waitpid@GLIBC_2.0', '__errno_location@GLIBC_2.0', 'fopen@GLIBC_2.2', 'fseek@GLIBC_2.0', 'ftell@GLIBC_2.0', 'fclose@GLIBC_2.2', 'rename@GLIBC_2.0', 'unlink@GLIBC_2.0', 'access@GLIBC_2.0', 'pthread_mutex_lock@GLIBC_2.0', 'pthread_mutex_unlock@GLIBC_2.0', 'calloc@GLIBC_2.0')

    def __init__(self):
        super().__init__()
        self.mock(*self.MOCKS)
        self.errno = self.alloc(4)
        self.word(self.errno, 4)
        self.files = {}
        self.handles = {}
        self.found = self.extern = 0
        self.out = None

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if name not in self.MOCKS:
            return super().hook(u, address, size, unused)
        name = name.split('@')[0]
        a, b = (u.reg_read(r) for r in REGS[:2])
        ret = 0
        self.seen.append(name)
        if name == 'toolsGetAlbumCover':
            ret = self.found
        elif name == 'toolsGetExternCover':
            ret = self.extern
        elif name == 'fork':
            ret = -1 if self.out is None else 7
            if self.out is not None:
                self.files[self.dst + '.ff'] = self.out
        elif name == 'waitpid':
            ret = -1 if self.seen.count('waitpid') == 1 else a
            if ret == a:
                self.word(b, 0)
        elif name == '__errno_location':
            ret = self.errno
        elif name == 'fopen':
            ret = self.alloc(4) if self.text(a) in self.files else 0
            self.handles[ret] = self.text(a)
        elif name == 'ftell':
            ret = len(self.files[self.handles[a]])
        elif name == 'rename':
            self.files[self.text(b)] = self.files.pop(self.text(a))
        elif name == 'unlink':
            self.files.pop(self.text(a), None)
        elif name == 'access':
            ret = 0 if self.text(a).startswith('/mnt/mmc/A/') else -1
        elif name == 'calloc':
            ret = self.alloc(a * b)
        self.returns(ret, clobber=True)

    def cover(self, track, fn='coverflow_grid_cover', **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.seen = []
        self.dst = '/mnt/mmc/.sldp/x0.jpg'
        args = (self.string('/mnt/mmc/A/' + track), self.string(self.dst), 110, 110)
        return self.call(address=payload_syms[fn], args=args, gap=0)
try:
    m = GridCoverMachine()
except BaseException as _e:
    _SOFT.append((9014, repr(_e)[:200]))
    print('FAIL line', 9014, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.files = {m.dst: b'old'}
except BaseException as _e:
    _SOFT.append((9017, repr(_e)[:200]))
    print('FAIL line', 9017, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class FolderCoverMachine(GridCoverMachine):

    def hook(self, u, address, size, unused):
        if self.handlers.get(address) != 'toolsGetAlbumCover':
            return super().hook(u, address, size, unused)
        track = self.text(u.reg_read(REGS[0]))
        self.tried.append(track)
        self.returns(int(track in self.art), clobber=True)

    def folder(self, track):
        self.tried = []
        self.seen = []
        dst = self.string('/mnt/mmc/.downpic/fd_x.jpg')
        return self.call(address=payload_syms['coverflow_folder_cover'], args=(self.string(track), dst, 80, 80), gap=0)
try:
    m = FolderCoverMachine()
except BaseException as _e:
    _SOFT.append((9042, repr(_e)[:200]))
    print('FAIL line', 9042, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    A = '/mnt/mmc/Музыка/Альбом'
except BaseException as _e:
    _SOFT.append((9042, repr(_e)[:200]))
    print('FAIL line', 9042, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.scope_tree = {A: [('05 Five.flac', 8), ('04 Four.flac', 8), ('cover.jpg', 8), ('00 Intro.mp3', 8), ('CD2', 4), ('._01 One.flac', 0), ('01 One.FLAC', 0), ('02 Two.flac', 8)]}
except BaseException as _e:
    _SOFT.append((9043, repr(_e)[:200]))
    print('FAIL line', 9043, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.art = {A + '/01 One.FLAC', A + '/04 Four.flac'}
except BaseException as _e:
    _SOFT.append((9045, repr(_e)[:200]))
    print('FAIL line', 9045, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    far = '/mnt/mmc/' + 'x' * 600 + '/1.flac'
except BaseException as _e:
    _SOFT.append((9049, repr(_e)[:200]))
    print('FAIL line', 9049, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.art = {A + '/04 Four.flac'}
except BaseException as _e:
    _SOFT.append((9052, repr(_e)[:200]))
    print('FAIL line', 9052, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.art = {'/mnt/mmc/Other/9.flac'}
except BaseException as _e:
    _SOFT.append((9058, repr(_e)[:200]))
    print('FAIL line', 9058, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    BIG, BIG_SIZE = (33554432, 50331648)
except BaseException as _e:
    _SOFT.append((9065, repr(_e)[:200]))
    print('FAIL line', 9065, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def cover_pixels(seed, n=160 * 160):
    return b''.join((struct.pack('<I', 4278190080 | i * 2654435761 + seed * 40503 >> 7 & 16777215) for i in range(n)))

class DepthMachine(CoverflowMachine):
    budget = 50000000

    def __init__(self, **kw):
        super().__init__(**kw)
        self.u.mem_map(BIG, BIG_SIZE)
        self.big = BIG
        self.frames = []
        self.destroyed = []
        self.draws = []
        self.loads = []
        self.unloads = 0
        self.pixels = {}
        self.art_size = (160, 160)
        self.art_sizes = {}
        self.handlers[syms['sem_post@GLIBC_2.2']] = 'loader_wake'
        self.mock('strstr@GLIBC_2.0')
        self.fast()

    def big_alloc(self, n):
        a = self.big
        self.big += n + 15 & ~15
        assert self.big <= BIG + BIG_SIZE
        return a

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        a, b, c, d = [u.reg_read(r) for r in REGS]
        if name == 'c:calloc@GLIBC_2.0' and a * b > 65536:
            ret = self.big_alloc(a * b)
            self.live[ret] = a * b
        elif name == 'bitmap_create_ex':
            ret = self.big_alloc(72)
            data = self.big_alloc(b * c)
            self.word(ret, a)
            self.word(ret + 4, b)
            self.word(ret + 8, c)
            self.u.mem_write(ret + 12, struct.pack('<HH', 0, d))
            self.word(ret + 20, data)
            self.frames.append(ret)
        elif name == 'bitmap_destroy':
            self.destroyed.append(a)
            ret = 0
        elif name == 'loader_wake':
            self.wakes = getattr(self, 'wakes', 0) + 1
            while self.nested(payload_syms['ld_step']):
                pass
            ret = 0
        elif name == 'bitmap_lock_buffer_for_read':
            ret = self.get(a + 20)
        elif name == 'widget_load_image':
            url = self.text(b)
            self.loads.append(url) if a == self.top else None
            self.image_loads.append(url)
            if url in getattr(self, 'image_fail', ()):
                self.returns(1, clobber=True)
                return
            w, h = (160, 160) if url == 'default_album_flow' else next((v for k, v in self.art_sizes.items() if url.endswith(k)), self.art_size)
            if url not in self.pixels:
                self.pixels[url] = self.big_alloc(w * h * 4)
                self.u.mem_write(self.pixels[url], cover_pixels(len(self.pixels), w * h))
            self.word(c, w)
            self.word(c + 4, h)
            self.word(c + 8, w * 4)
            self.u.mem_write(c + 12, struct.pack('<HH', 2, 1))
            self.word(c + 20, self.pixels[url])
            ret = 0
        elif name == 'widget_unload_image':
            self.unloads += a == self.top
            ret = 0
        elif name == 'slide_menu_set_spacer':
            self.word(a + O['SLIDE_SPACER'], b)
            ret = 0
        elif name == 'canvas_draw_image':
            src, dst = [tuple((signed(self.get(r + 4 * i)) for i in range(4))) for r in (c, d)]
            line = self.get(b + 8)
            data = self.get(b + 20)
            frame = b''.join((bytes(self.u.mem_read(data + y * line, 4 * dst[2])) for y in range(dst[3])))
            self.draws.append((a, b, src, dst, struct.unpack_from('<H', bytes(self.u.mem_read(b + 12, 2)))[0], frame))
            ret = 0
        else:
            return super().hook(u, address, size, unused)
        self.calls.append((name, a, b, c))
        self.returns(ret, clobber=True)

def host_render(textures, cases, style=None, paper=None, windows=False, still=False):
    """The same renderer source built for the host: frames for (frac, mask of drawn slots) cases. style: a theme's cf.* values; paper: the light half's.
    15 textures make the thumb's wide ring; a case's (kind, lean, a, b) then sets the thumb: 1 letters, the row held as an entry from the row at
    lead a/1000 leaves it, 2 a ms into the fold from that row held at lead b/1000, 3 the entry, lead a/1000 and b/1000 albums to go, 4 letters
    with the row held as the case's next five numbers have it (hold_lead, hold_run, hold_xrun, hold_xs, hold_k).
    windows: also each case's window middle from the album at the middle (cf_frame_window), as a second list.
    still: nothing moving, as the page's own frame at rest (the shelf's seven); else moving, as a direct render is."""
    import subprocess, tempfile
    from coverflow import CF_SOURCES, SHIM_H
    from theme import ROLE_MACROS, builtins
    style = style or {f: v for f, v in builtins()[0].items() if f.startswith('cf.')}
    if len(textures) == 7:
        textures = [textures[0]] * 4 + list(textures) + [textures[0]] * 4
        cases = [(c[0], c[1] << 4, *c[2:]) for c in cases]
    cf = ', '.join((f'.{f[3:]} = {v}' for f, v in style.items()))
    rows = style['cf.view_h']
    main = '#include "theme.h"\nstatic theme_t t = { .cf = { %s } };\nint client_h(void) { return 290; }\nconst theme_t *theme(void) { return &t; }\n#include <stdio.h>\nvoid coverflow_render(unsigned *, int, int, const unsigned *const[7]);\nint ipod_light(void) { return %d; }\nint ipod_square_art(void) { return 0; }\nunsigned time_now_ms(void) { return 0; } /* the fold\'s clock: fold_at counts back from 0 */\nint ringnav_big_library(void) { return 0; } /* the MIPS machines\' own: Fast Navigation off */\nvoid cf_thumb_lead(float), cf_thumb_lean(int), cf_thumb_fold(int, unsigned), cf_thumb_enter(int, float, float);\nvoid cf_thumb_speed(float, float), cf_thumb_held(float, float, float, float, int);\nfloat cf_thumb_hold(int, float, int);\nint cf_frame_window(int);\nvoid cf_shelf_still(int);\nconst scheme_t *scheme(void) { static scheme_t s = { .paper = %d }; return &s; }\n#define N %d\nstatic unsigned tex[N][160 * 160], frame[%d * %d];\nint main(void) {\n    const unsigned *ring[N];\n    cf_shelf_still(%d);\n    if (fread(tex, 4, N * 160 * 160, stdin) != N * 160 * 160) return 1;\n    int frac, mask, kind, lean, a, b;\n    while (scanf("%%d %%d %%d %%d %%d %%d", &frac, &mask, &kind, &lean, &a, &b) == 6) {\n        for (int j = 0; j < N; ++j) ring[j] = mask >> j & 1 ? tex[j] : 0;\n        float h[4];\n        int k;\n        if (kind == 4 && scanf("%%f %%f %%f %%f %%d", h, h + 1, h + 2, h + 3, &k) != 5) return 1;\n        cf_thumb_enter(0, 0, 0), cf_thumb_lead(-1), cf_thumb_speed(0, 0);\n        if (kind == 1 || kind == 2) cf_thumb_hold(lean, (kind == 1 ? a : b) / 1000.f, 0);\n        if (kind == 4) cf_thumb_held(h[0], h[1], h[2], h[3], k);\n        if (kind == 1 || kind == 4) cf_thumb_lean(lean);\n        if (kind == 2) cf_thumb_fold(lean, -(unsigned)a);\n        if (kind == 3) cf_thumb_enter(lean, a / 1000.f, b / 1000.f);\n        coverflow_render(frame, %d, frac, ring + (N - 7) / 2);\n        fwrite(frame, 4, sizeof(frame) / 4, stdout);\n        fprintf(stderr, "window %%d\\n", cf_frame_window(frac));\n        fflush(stdout);\n    }\n    return 0;\n}' % (cf, paper is not None, paper or 0, len(textures), rows, O['CF_VIEW_W'], int(still), O['CF_VIEW_W'])
    with tempfile.TemporaryDirectory(prefix='q2-render-') as d:
        d = pathlib.Path(d)
        (d / 'shim.h').write_text(SHIM_H)
        (d / 'main.c').write_text(main)
        (d / 'coverflow_all.c').write_text(CF_SOURCES)
        subprocess.run(['cc', '-O1', '-DPEQ_HOST', '-DPEQ_ROOT=""', '-D_GNU_SOURCE', '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections', '-I', str(ROOT / 'patch'), '-include', str(d / 'shim.h'), str(d / 'coverflow_all.c'), str(d / 'main.c'), '-o', str(d / 'render')], check=True)
        feed = b''.join(textures) + ''.join((' '.join(('%.9g' % x if isinstance(x, float) else str(x) for x in (*c, 0, 0, 0, 0)[:max(6, len(c))])) + '\n' for c in cases)).encode()
        run = subprocess.run([str(d / 'render')], input=feed, capture_output=True, check=True)
        out = run.stdout
    size = 4 * O['CF_VIEW_W'] * rows
    frames = [out[i * size:(i + 1) * size] for i in range(len(cases))]
    return (frames, [int(l.split()[1]) for l in run.stderr.decode().splitlines() if l.startswith('window ')]) if windows else frames
try:
    ps = payload_syms
except BaseException as _e:
    _SOFT.append((9180, repr(_e)[:200]))
    print('FAIL line', 9180, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    textures = [cover_pixels(100 + j) for j in range(7)]
except BaseException as _e:
    _SOFT.append((9181, repr(_e)[:200]))
    print('FAIL line', 9181, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    cases = [(0, 127), (16384, 127), (-16384, 127), (32767, 127), (-32768, 127), (5000, 62), (0, 0)]
except BaseException as _e:
    _SOFT.append((9182, repr(_e)[:200]))
    print('FAIL line', 9182, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    want = host_render(textures, cases)
except BaseException as _e:
    _SOFT.append((9183, repr(_e)[:200]))
    print('FAIL line', 9183, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = DepthMachine()
except BaseException as _e:
    _SOFT.append((9184, repr(_e)[:200]))
    print('FAIL line', 9184, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tex = m.big_alloc(7 * 160 * 160 * 4)
except BaseException as _e:
    _SOFT.append((9184, repr(_e)[:200]))
    print('FAIL line', 9184, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ring = m.big_alloc(60) + 16
except BaseException as _e:
    _SOFT.append((9185, repr(_e)[:200]))
    print('FAIL line', 9185, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    frame = m.big_alloc(4 * O['CF_VIEW_W'] * O['CF_VIEW_H'])
except BaseException as _e:
    _SOFT.append((9185, repr(_e)[:200]))
    print('FAIL line', 9185, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = DepthMachine()
except BaseException as _e:
    _SOFT.append((9195, repr(_e)[:200]))
    print('FAIL line', 9195, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    page = m.open()
except BaseException as _e:
    _SOFT.append((9195, repr(_e)[:200]))
    print('FAIL line', 9195, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    s = m.slide
except BaseException as _e:
    _SOFT.append((9195, repr(_e)[:200]))
    print('FAIL line', 9195, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    stride = signed(m.get(s + O['SLIDE_SPACER'])) + O['CF_VIEW_H']
except BaseException as _e:
    _SOFT.append((9197, repr(_e)[:200]))
    print('FAIL line', 9197, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def paint(m):
    return m.call(address=HOOKS['widget_on_paint_border'][0], args=(m.slide, m.canvas, 0, 0), gap=0, clear=False, count=50000000)
try:
    instructions = [0]
except BaseException as _e:
    _SOFT.append((9201, repr(_e)[:200]))
    print('FAIL line', 9201, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def count_block(u, address, size, unused):
    instructions[0] += size // 4
try:
    counter = m.u.hook_add(UC_HOOK_BLOCK, count_block, begin=BASE, end=SCRATCH - 1)
except BaseException as _e:
    _SOFT.append((9203, repr(_e)[:200]))
    print('FAIL line', 9203, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rest_cost = instructions[0]
except BaseException as _e:
    _SOFT.append((9204, repr(_e)[:200]))
    print('FAIL line', 9204, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tex_of = {u: bytes(m.u.mem_read(m.pixels[u], 160 * 160 * 4)) for u in m.pixels}
except BaseException as _e:
    _SOFT.append((9211, repr(_e)[:200]))
    print('FAIL line', 9211, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ring_tex = [tex_of[m.loads[1 + (i + 2) % 3]] for i in range(15)]
except BaseException as _e:
    _SOFT.append((9212, repr(_e)[:200]))
    print('FAIL line', 9212, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OLED_CF = {k: v for k, v in theme_builtins()[0].items() if k.startswith('cf.')}
except BaseException as _e:
    _SOFT.append((9216, repr(_e)[:200]))
    print('FAIL line', 9216, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    cases = [(16384, 127), (-16384, 127), (26214, 127), (32767, 127), (-32768, 127)]
except BaseException as _e:
    _SOFT.append((9229, repr(_e)[:200]))
    print('FAIL line', 9229, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    want = host_render(textures, cases, paper=16777215)
except BaseException as _e:
    _SOFT.append((9230, repr(_e)[:200]))
    print('FAIL line', 9230, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tex = c.big_alloc(7 * 160 * 160 * 4)
except BaseException as _e:
    _SOFT.append((9231, repr(_e)[:200]))
    print('FAIL line', 9231, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ring = c.big_alloc(60) + 16
except BaseException as _e:
    _SOFT.append((9231, repr(_e)[:200]))
    print('FAIL line', 9231, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    frame = c.big_alloc(4 * O['CF_VIEW_W'] * OLED_CF['cf.view_h'])
except BaseException as _e:
    _SOFT.append((9231, repr(_e)[:200]))
    print('FAIL line', 9231, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def fitted(px, w, h):
    side = max(w, h)
    out = []
    for y in range(160):
        sy = y * side // 160 - (side - h) // 2
        for x in range(160):
            sx = x * side // 160 - (side - w) // 2
            out.append(px[(sy * w + sx) * 4:(sy * w + sx) * 4 + 4] if 0 <= sx < w and 0 <= sy < h else struct.pack('<I', 4278190080))
    return b''.join(out)
try:
    instructions[0] = 0
except BaseException as _e:
    _SOFT.append((9253, repr(_e)[:200]))
    print('FAIL line', 9253, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    instructions[0] = 0
except BaseException as _e:
    _SOFT.append((9254, repr(_e)[:200]))
    print('FAIL line', 9254, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    turn_cost = instructions[0]
except BaseException as _e:
    _SOFT.append((9254, repr(_e)[:200]))
    print('FAIL line', 9254, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class Starved(DepthMachine):

    def hook(self, u, address, size, unused):
        if self.handlers.get(address, '') == 'bitmap_create_ex':
            self.returns(0)
            return
        return super().hook(u, address, size, unused)
try:
    m = Starved()
except BaseException as _e:
    _SOFT.append((9268, repr(_e)[:200]))
    print('FAIL line', 9268, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    s = m.slide
except BaseException as _e:
    _SOFT.append((9268, repr(_e)[:200]))
    print('FAIL line', 9268, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def light_new(cls, **kw):
    CONFIG['LIGHT'] = '1'
    try:
        return cls(**kw)
    finally:
        del CONFIG['LIGHT']

def thumb_page(cls=None, **kw):
    m = light_new(cls or DepthMachine, albums=9, **kw)
    m.open()
    m.handlers[sleep_hook + 12] = 'stock_sleep'
    m.fresh = 1
    return m

def tp(m, travel=0, ms=17):
    if travel:
        m.word(ps['wheel_travel'], travel & 4294967295)
    m.advance(ms, clear=False)
    if m.fresh:
        m.word(ps['wheel_frame_at'], m.now)
    m.call(address=sleep_hook, args=(4660, 0, 0, 0), gap=0, clear=False, debounce=True)

def traw(m):
    return m.get(ps['wheel_raw'])

def thold(m):
    m.word(ps['wheel_held'], 1)
    tp(m)
    tp(m, ms=50)

def tspin(m, ms=600, units=-20):
    for _ in range(0, ms, 17):
        tp(m, units)

def trest(m):
    m.word(ps['wheel_held'], 0)
    for _ in range(200):
        tp(m)

def tindex(m):
    return m.get(m.slide + O['SLIDE_INDEX'])

def toff(m):
    return signed(m.get(m.slide + O['SLIDE_OFFSET']))

def tcaption(m):
    return m.texts()[0]
try:
    CF_FIELDS = [f for l in re.findall('^\\s*short ([^;]+);', re.search('typedef struct \\{([^}]*)\\} cf_style_t;', (ROOT / 'patch/theme.h').read_text())[1], re.M) for f in re.split(',\\s*', l)]
except BaseException as _e:
    _SOFT.append((9298, repr(_e)[:200]))
    print('FAIL line', 9298, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def no_letters(m):
    m.u.mem_write(ps['cfs'] + 2 * CF_FIELDS.index('jump_ticks'), b'\x00\x00')
try:
    CF_DEFINES = {k: int(v) for k, v in re.findall('^#define (\\w+) (\\d+)', (ROOT / 'patch/coverflow.h').read_text(), re.M)}
except BaseException as _e:
    _SOFT.append((9300, repr(_e)[:200]))
    print('FAIL line', 9300, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    TH_AT = {f: 4 * i for i, f in enumerate((f for l in re.findall('^\\s*\\w+ ([^;]+);', re.sub('/\\*.*?\\*/', '', re.search('typedef struct \\{([^}]*)\\} cf_thumb_t;', (ROOT / 'patch/coverflow.h').read_text())[1], flags=re.S), re.M) for f in re.split(',\\s*', l)))}
except BaseException as _e:
    _SOFT.append((9301, repr(_e)[:200]))
    print('FAIL line', 9301, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def tpaint(m):
    m.call(address=HOOKS['widget_on_paint_border'][0], args=(m.slide, m.canvas, 0, 0), gap=0, clear=False, count=50000000, debounce=True)
try:
    m = DepthMachine(albums=9)
except BaseException as _e:
    _SOFT.append((9305, repr(_e)[:200]))
    print('FAIL line', 9305, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[sleep_hook + 12] = 'stock_sleep'
except BaseException as _e:
    _SOFT.append((9305, repr(_e)[:200]))
    print('FAIL line', 9305, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fresh = 1
except BaseException as _e:
    _SOFT.append((9305, repr(_e)[:200]))
    print('FAIL line', 9305, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def to_other(m):
    m.top = m.node('window', 'home_page')

def to_dialog(m):
    m.top = m.node('dialog', 'msginfo_dialog')

def to_np(m):
    m.top = m.node('window', 'playing_page')

def thumb_exits():

    def flag(name, on, off):
        return (lambda m: m.byte(syms[name], on), lambda m: m.byte(syms[name], off))
    back = lambda m: setattr(m, 'top', m.page)
    yield ('window', to_other, back)
    yield ('dialog', to_dialog, back)
    yield ('now playing', to_np, back)
    yield (('dark',) + flag('g_backlight_status', 0, 1))
    yield (('locked',) + flag('g_lockscreen_pageflag', 1, 0))
    yield (('usb',) + flag('g_usblink_status', 2, 0))
    yield (('power',) + flag('g_poweroff_state', 2, 0))
    yield (('test',) + flag('g_testmode_flag', 1, 0))
    yield ('sliding', lambda m: setattr(m, 'animating', 1), lambda m: setattr(m, 'animating', 0))
    yield ('finger', lambda m: setattr(m, 'pressed', 1), lambda m: setattr(m, 'pressed', 0))
try:
    m = thumb_page()
except BaseException as _e:
    _SOFT.append((9333, repr(_e)[:200]))
    print('FAIL line', 9333, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    shown = tcaption(m)
except BaseException as _e:
    _SOFT.append((9333, repr(_e)[:200]))
    print('FAIL line', 9333, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.slide = 0
except BaseException as _e:
    _SOFT.append((9333, repr(_e)[:200]))
    print('FAIL line', 9333, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = thumb_page()
except BaseException as _e:
    _SOFT.append((9336, repr(_e)[:200]))
    print('FAIL line', 9336, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = thumb_page()
except BaseException as _e:
    _SOFT.append((9340, repr(_e)[:200]))
    print('FAIL line', 9340, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    page = m.page
except BaseException as _e:
    _SOFT.append((9340, repr(_e)[:200]))
    print('FAIL line', 9340, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[playing + 12] = 'stock_playing'
except BaseException as _e:
    _SOFT.append((9341, repr(_e)[:200]))
    print('FAIL line', 9341, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.lyric_size = 9
except BaseException as _e:
    _SOFT.append((9341, repr(_e)[:200]))
    print('FAIL line', 9341, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fail_calloc = True
except BaseException as _e:
    _SOFT.append((9341, repr(_e)[:200]))
    print('FAIL line', 9341, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rows = [m.node('label') for _ in ROWS]
except BaseException as _e:
    _SOFT.append((9342, repr(_e)[:200]))
    print('FAIL line', 9342, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.lrc = m.node('scroll_view', 'scroll_lrc', rows)
except BaseException as _e:
    _SOFT.append((9344, repr(_e)[:200]))
    print('FAIL line', 9344, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.np = m.node('slide_view', 'slide_view', [m.lrc], value=1)
except BaseException as _e:
    _SOFT.append((9345, repr(_e)[:200]))
    print('FAIL line', 9345, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = page
except BaseException as _e:
    _SOFT.append((9350, repr(_e)[:200]))
    print('FAIL line', 9350, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.win
except BaseException as _e:
    _SOFT.append((9352, repr(_e)[:200]))
    print('FAIL line', 9352, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = thumb_page()
except BaseException as _e:
    _SOFT.append((9362, repr(_e)[:200]))
    print('FAIL line', 9362, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((9362, repr(_e)[:200]))
    print('FAIL line', 9362, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((9365, repr(_e)[:200]))
    print('FAIL line', 9365, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def letter_on(m):
    return any((v[1] == ps['cf_letter_expire'] for v in m.timers.values()))

def first(seen):
    return all((FRUIT[i][0] != FRUIT[i - 1][0] for i in seen if i))

def tfloat(m, f):
    return struct.unpack('<f', m.u.mem_read(ps['th'] + TH_AT[f], 4))[0]

def ttarget(m):
    return (tindex(m) + int(tfloat(m, 'goal'))) % len(FRUIT)

def tarrive(m):
    for _ in range(300):
        if tfloat(m, 'pos') == tfloat(m, 'goal'):
            return
        tp(m)
    assert False
try:
    FRUIT = ('Apple', 'Avocado', 'Banana', 'Blueberry', 'Cherry', 'Date', 'Elder', 'Fig', 'Grape')
except BaseException as _e:
    _SOFT.append((9387, repr(_e)[:200]))
    print('FAIL line', 9387, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def plain(m):
    return not m.get(ps['th'] + TH_AT['jump']) and (not m.get(ps['th'] + TH_AT['fold'])) and ((64 - 15) * 160 * 160 * 4 not in m.live.values())
try:
    m = light_new(DepthMachine, albums=9)
except BaseException as _e:
    _SOFT.append((9389, repr(_e)[:200]))
    print('FAIL line', 9389, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[sleep_hook + 12] = 'stock_sleep'
except BaseException as _e:
    _SOFT.append((9391, repr(_e)[:200]))
    print('FAIL line', 9391, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fresh = 1
except BaseException as _e:
    _SOFT.append((9391, repr(_e)[:200]))
    print('FAIL line', 9391, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    seen = {tcaption(m)}
except BaseException as _e:
    _SOFT.append((9393, repr(_e)[:200]))
    print('FAIL line', 9393, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((9393, repr(_e)[:200]))
    print('FAIL line', 9393, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    seen = {tcaption(m)}
except BaseException as _e:
    _SOFT.append((9396, repr(_e)[:200]))
    print('FAIL line', 9396, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    index = tindex(m)
except BaseException as _e:
    _SOFT.append((9399, repr(_e)[:200]))
    print('FAIL line', 9399, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = light_new(DepthMachine, albums=9)
except BaseException as _e:
    _SOFT.append((9402, repr(_e)[:200]))
    print('FAIL line', 9402, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.config['BIGLIBRARY'] = '1'
except BaseException as _e:
    _SOFT.append((9402, repr(_e)[:200]))
    print('FAIL line', 9402, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[sleep_hook + 12] = 'stock_sleep'
except BaseException as _e:
    _SOFT.append((9404, repr(_e)[:200]))
    print('FAIL line', 9404, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.fresh = 1
except BaseException as _e:
    _SOFT.append((9404, repr(_e)[:200]))
    print('FAIL line', 9404, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    seen = [ttarget(m)]
except BaseException as _e:
    _SOFT.append((9412, repr(_e)[:200]))
    print('FAIL line', 9412, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((9412, repr(_e)[:200]))
    print('FAIL line', 9412, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    slid = 0
except BaseException as _e:
    _SOFT.append((9412, repr(_e)[:200]))
    print('FAIL line', 9412, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    seen = [tindex(m)]
except BaseException as _e:
    _SOFT.append((9427, repr(_e)[:200]))
    print('FAIL line', 9427, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    index = ttarget(m)
except BaseException as _e:
    _SOFT.append((9432, repr(_e)[:200]))
    print('FAIL line', 9432, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.letters = []
except BaseException as _e:
    _SOFT.append((9436, repr(_e)[:200]))
    print('FAIL line', 9436, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    index = tindex(m)
except BaseException as _e:
    _SOFT.append((9437, repr(_e)[:200]))
    print('FAIL line', 9437, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.letters = []
except BaseException as _e:
    _SOFT.append((9441, repr(_e)[:200]))
    print('FAIL line', 9441, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def fruit_page(big):
    m = light_new(DepthMachine, albums=9)
    if big:
        m.config['BIGLIBRARY'] = '1'
    for r, name in zip(m.albums, FRUIT):
        m.word(r + O['REC_ALBUM'], m.string(name))
    m.open()
    m.handlers[sleep_hook + 12] = 'stock_sleep'
    m.fresh = 1
    tp(m)
    return m

def cfs(m, f):
    return struct.unpack('<h', m.u.mem_read(ps['cfs'] + 2 * CF_FIELDS.index(f), 2))[0]
try:
    m = fruit_page(1)
except BaseException as _e:
    _SOFT.append((9459, repr(_e)[:200]))
    print('FAIL line', 9459, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    t = 0
except BaseException as _e:
    _SOFT.append((9459, repr(_e)[:200]))
    print('FAIL line', 9459, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    at = None
except BaseException as _e:
    _SOFT.append((9459, repr(_e)[:200]))
    print('FAIL line', 9459, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = DepthMachine(albums=9)
except BaseException as _e:
    _SOFT.append((9468, repr(_e)[:200]))
    print('FAIL line', 9468, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.char_px = 30.0
except BaseException as _e:
    _SOFT.append((9468, repr(_e)[:200]))
    print('FAIL line', 9468, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    LB = O['LETTER_BOX']
except BaseException as _e:
    _SOFT.append((9468, repr(_e)[:200]))
    print('FAIL line', 9468, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    px = O['LETTER_PX']
except BaseException as _e:
    _SOFT.append((9468, repr(_e)[:200]))
    print('FAIL line', 9468, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sw, sh = (signed(m.get(m.slide + O[k])) for k in ('W_W', 'W_H'))
except BaseException as _e:
    _SOFT.append((9468, repr(_e)[:200]))
    print('FAIL line', 9468, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.letters = []
except BaseException as _e:
    _SOFT.append((9475, repr(_e)[:200]))
    print('FAIL line', 9475, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.letters = []
except BaseException as _e:
    _SOFT.append((9478, repr(_e)[:200]))
    print('FAIL line', 9478, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.letters = []
except BaseException as _e:
    _SOFT.append((9479, repr(_e)[:200]))
    print('FAIL line', 9479, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def twake(m, kind, key=O['KEY_CENTER'], gap=30):
    inp = HOOKS['window_manager_dispatch_input_event'][0]
    m.handlers[inp + 12] = 'stock_input'
    n = len(m.calls)
    assert m.call(key, address=inp, args=(m.wm, m.event, 0, 0), event_type=kind, gap=gap, clear=False, debounce=True) == 0
    return any((c[0] == 'stock_input' for c in m.calls[n:]))
try:
    m = thumb_page()
except BaseException as _e:
    _SOFT.append((9485, repr(_e)[:200]))
    print('FAIL line', 9485, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    off = toff(m)
except BaseException as _e:
    _SOFT.append((9485, repr(_e)[:200]))
    print('FAIL line', 9485, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = thumb_page()
except BaseException as _e:
    _SOFT.append((9489, repr(_e)[:200]))
    print('FAIL line', 9489, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.animating = 1
except BaseException as _e:
    _SOFT.append((9489, repr(_e)[:200]))
    print('FAIL line', 9489, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.animating = 0
except BaseException as _e:
    _SOFT.append((9490, repr(_e)[:200]))
    print('FAIL line', 9490, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    a = m.get(m.slide + O['SLIDE_ANIMATOR'])
except BaseException as _e:
    _SOFT.append((9490, repr(_e)[:200]))
    print('FAIL line', 9490, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    held = signed(m.get(a + O['ANIM_X_TO']))
except BaseException as _e:
    _SOFT.append((9492, repr(_e)[:200]))
    print('FAIL line', 9492, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    off = toff(m)
except BaseException as _e:
    _SOFT.append((9495, repr(_e)[:200]))
    print('FAIL line', 9495, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = thumb_page()
except BaseException as _e:
    _SOFT.append((9499, repr(_e)[:200]))
    print('FAIL line', 9499, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    n = [0]
except BaseException as _e:
    _SOFT.append((9499, repr(_e)[:200]))
    print('FAIL line', 9499, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    h = m.u.hook_add(UC_HOOK_BLOCK, lambda u, a, size, x: n.__setitem__(0, n[0] + size // 4), begin=BASE, end=SCRATCH - 1)
except BaseException as _e:
    _SOFT.append((9499, repr(_e)[:200]))
    print('FAIL line', 9499, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    n[0] = 0
except BaseException as _e:
    _SOFT.append((9500, repr(_e)[:200]))
    print('FAIL line', 9500, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    turn_cost = n[0]
except BaseException as _e:
    _SOFT.append((9502, repr(_e)[:200]))
    print('FAIL line', 9502, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    n[0] = 0
except BaseException as _e:
    _SOFT.append((9503, repr(_e)[:200]))
    print('FAIL line', 9503, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    draws = len(m.draws)
except BaseException as _e:
    _SOFT.append((9503, repr(_e)[:200]))
    print('FAIL line', 9503, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    row_cost = n[0]
except BaseException as _e:
    _SOFT.append((9503, repr(_e)[:200]))
    print('FAIL line', 9503, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def th_set(m, kind=0, lean=0, a=0, b=0):
    F = ('lead', 'enter', 'vel', 'lead_v', 'pos')

    def put(**v):
        for f, x in v.items():
            m.u.mem_write(ps['th'] + TH_AT[f], struct.pack('<f', x) if f in F else struct.pack('<I', int(x) & 4294967295))
    put(vel=0, lead_v=0)
    if kind in (1, 2):
        put(pos=0, lean=lean, lead=(a if kind == 1 else b) / 1000)
        m.call(address=ps['coverflow_hold'], args=(0, 0, 0, 0), gap=0)
    put(on=kind > 0, jump=kind in (1, 3), fold=kind == 2, lean=lean, fold_at=m.now - a if kind == 2 else 0, lead=a / 1000 if kind == 3 else 0, enter=b / 1000 if kind == 3 else 0)
try:
    wide = [cover_pixels(200 + j) for j in range(15)]
except BaseException as _e:
    _SOFT.append((9521, repr(_e)[:200]))
    print('FAIL line', 9521, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ALL = (1 << 15) - 1
except BaseException as _e:
    _SOFT.append((9521, repr(_e)[:200]))
    print('FAIL line', 9521, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    FRAME_BOUND = {0: 5600000, 1: 4600000, 2: 4350000, 3: 4650000}
except BaseException as _e:
    _SOFT.append((9521, repr(_e)[:200]))
    print('FAIL line', 9521, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    style = OLED_CF
except BaseException as _e:
    _SOFT.append((9522, repr(_e)[:200]))
    print('FAIL line', 9522, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    cases = [(0, ALL), (16384, ALL)] + [c for lean in (1, -1) for c in ((0, ALL, 1, lean, -3200 * lean), (20000 * lean, ALL, 1, lean, -3200 * lean), (0, ALL, 2, lean, CF_DEFINES['CF_FOLD_MS'] // 2, -3200 * lean), (-8192 * lean, ALL, 3, lean, -2500 * lean, style['cf.enter_albums'] * 500), (-8192 * lean, ALL, 3, lean, -2500 * lean, style['cf.enter_albums'] * 150))]
except BaseException as _e:
    _SOFT.append((9523, repr(_e)[:200]))
    print('FAIL line', 9523, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    want = host_render(wide, cases, style)
except BaseException as _e:
    _SOFT.append((9526, repr(_e)[:200]))
    print('FAIL line', 9526, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    CONFIG['LIGHT'] = '0'
except BaseException as _e:
    _SOFT.append((9527, repr(_e)[:200]))
    print('FAIL line', 9527, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tex = m.big_alloc(15 * 160 * 160 * 4)
except BaseException as _e:
    _SOFT.append((9530, repr(_e)[:200]))
    print('FAIL line', 9530, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ring = m.big_alloc(60)
except BaseException as _e:
    _SOFT.append((9530, repr(_e)[:200]))
    print('FAIL line', 9530, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    frame = m.big_alloc(4 * O['CF_VIEW_W'] * style['cf.view_h'])
except BaseException as _e:
    _SOFT.append((9530, repr(_e)[:200]))
    print('FAIL line', 9530, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    n = [0]
except BaseException as _e:
    _SOFT.append((9532, repr(_e)[:200]))
    print('FAIL line', 9532, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    h = m.u.hook_add(UC_HOOK_BLOCK, lambda u, a, size, x: n.__setitem__(0, n[0] + size // 4), begin=BASE, end=SCRATCH - 1)
except BaseException as _e:
    _SOFT.append((9532, repr(_e)[:200]))
    print('FAIL line', 9532, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class HalfMachine(FileMocks, DepthMachine):

    def __init__(self, **kw):
        super().__init__(**kw)
        self.file_mocks('h:')
        self.opened = []
        self.cut = False

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('h:'):
            return super().hook(u, address, size, unused)
        name = name[2:].split('@')[0]
        a, b, c, d = [u.reg_read(r) for r in REGS]
        self.calls.append((name, a, b, c))
        if name == 'fopen' and self.text(a).endswith('.jpg.half') and (self.text(b) == 'rb'):
            path = self.text(a)
            self.opened.append(path)
            self.files.setdefault(path, cover_pixels(300 + len(self.files), 80 * 80)[:100 if self.cut else None])
        self.returns(self.file_op(u, name, a, b, c, d), clobber=True)

def spread(h):
    rows = [b''.join((h[4 * (y * 80 + x):4 * (y * 80 + x) + 4] * 2 for x in range(80))) for y in range(80)]
    return b''.join((r + r for r in rows))
try:
    PAINT_BOUND = 5600000
except BaseException as _e:
    _SOFT.append((9559, repr(_e)[:200]))
    print('FAIL line', 9559, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    lower = payload_syms['thread_lower']
except BaseException as _e:
    _SOFT.append((9600, repr(_e)[:200]))
    print('FAIL line', 9600, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class PlArtMachine(Machine):

    def __init__(self):
        super().__init__()
        self.sql = []
        self.threads = 0
        self.order = []
        self.usb = 0
        self.mock('mkdir@GLIBC_2.0')
        for n in ('sqlite3_open', 'sqlite3_exec', 'sqlite3_close', 'pthread_join@GLIBC_2.0', 'pthread_create@GLIBC_2.2', 'navigator_window_is_exist'):
            self.handlers[syms[n]] = 'pa:' + n
        self.handlers[int(manifest['patch_symbols']['stock_usbmode_trampoline'], 16)] = 'pa:usb'

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('pa:'):
            return super().hook(u, address, size, unused)
        a, b, c, d = [u.reg_read(r) for r in REGS]
        name = name[3:]
        if name == 'sqlite3_open':
            self.sql.append(('open', self.text(a)))
            self.word(b, 219)
        elif name == 'sqlite3_exec':
            self.sql.append(('exec', a, self.text(b)))
        elif name.startswith('pthread_create'):
            self.threads += 1
            self.word(a, 119)
        elif name.startswith('pthread_join'):
            self.order.append(('join', a))
        elif name == 'usb':
            self.order.append(('stock', self.get(payload_syms['pl'] + 20)))
        elif name == 'navigator_window_is_exist':
            assert self.text(a) == 'usbmode_page'
            self.returns(self.usb)
            return
        self.returns(0)
try:
    m = PlArtMachine()
except BaseException as _e:
    _SOFT.append((9634, repr(_e)[:200]))
    print('FAIL line', 9634, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    url = m.alloc(600)
except BaseException as _e:
    _SOFT.append((9634, repr(_e)[:200]))
    print('FAIL line', 9634, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.usb = 1
except BaseException as _e:
    _SOFT.append((9640, repr(_e)[:200]))
    print('FAIL line', 9640, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from build import fileoff, STOCK_DB_OPEN
try:
    demo = (B / 'demo').read_bytes()
except BaseException as _e:
    _SOFT.append((9644, repr(_e)[:200]))
    print('FAIL line', 9644, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = PlArtMachine()
except BaseException as _e:
    _SOFT.append((9646, repr(_e)[:200]))
    print('FAIL line', 9646, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = thumb_page()
except BaseException as _e:
    _SOFT.append((9652, repr(_e)[:200]))
    print('FAIL line', 9652, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    shown = tcaption(m)
except BaseException as _e:
    _SOFT.append((9652, repr(_e)[:200]))
    print('FAIL line', 9652, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    index = tindex(m)
except BaseException as _e:
    _SOFT.append((9654, repr(_e)[:200]))
    print('FAIL line', 9654, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    DUR_CACHE = '/mnt/data/ringnav-durations-v1'
except BaseException as _e:
    _SOFT.append((9658, repr(_e)[:200]))
    print('FAIL line', 9658, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def dur_key(p):
    h = 14695981039346656037
    for c in p.encode():
        h = (h ^ c) * 1099511628211 & (1 << 64) - 1
    return h

def duration_cache(*pairs):
    return b'Q2DUR2\x00\x00' + b''.join((struct.pack('<Q3Ii', k, 1000, 2000, 0, v) for k, v in sorted(((dur_key(p), v) for p, v in pairs))))

def duration_pairs(data, paths):
    assert data[:8] == b'Q2DUR2\x00\x00'
    keys = {k: sec for k, _, _, _, sec in struct.iter_unpack('<Q3Ii', data[8:])}
    return {p: keys[dur_key(p)] for p in paths if dur_key(p) in keys}

def duration_syscalls(m):
    """The duration worker's syscalls: thread_lower's, answered 0, and ringnav_statx's (4366), a regular file of
    1000 bytes at mtime 2000 (struct statx: stx_mode at 28, stx_size 40, stx_mtime 112), its buffer the fifth argument."""

    def syscall(u, intno, unused):
        if u.reg_read(UC_MIPS_REG_V0) == 4366:
            buf = m.get(u.reg_read(UC_MIPS_REG_SP) + 16)
            u.mem_write(buf, bytes(256))
            m.word(buf + 28, 33188)
            m.word(buf + 40, 1000)
            m.word(buf + 112, 2000)
        u.reg_write(UC_MIPS_REG_A3, 0)
        u.reg_write(UC_MIPS_REG_V0, 0)
    m.u.hook_add(UC_HOOK_INTR, syscall)
try:
    SPLIT = O['HOME_SPLIT_W']
except BaseException as _e:
    _SOFT.append((9687, repr(_e)[:200]))
    print('FAIL line', 9687, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    PW = 375 - SPLIT
except BaseException as _e:
    _SOFT.append((9687, repr(_e)[:200]))
    print('FAIL line', 9687, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    X, Y, TICK = (O['DRIFT_X_MS'], O['DRIFT_Y_MS'], O['DRIFT_TICK_MS'])
except BaseException as _e:
    _SOFT.append((9687, repr(_e)[:200]))
    print('FAIL line', 9687, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = DepthMachine()
except BaseException as _e:
    _SOFT.append((9688, repr(_e)[:200]))
    print('FAIL line', 9688, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    pan_counter = m.u.hook_add(UC_HOOK_BLOCK, count_block, begin=BASE, end=SCRATCH - 1)
except BaseException as _e:
    _SOFT.append((9688, repr(_e)[:200]))
    print('FAIL line', 9688, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.budget = 80000000
except BaseException as _e:
    _SOFT.append((9689, repr(_e)[:200]))
    print('FAIL line', 9689, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.top = m.home
except BaseException as _e:
    _SOFT.append((9690, repr(_e)[:200]))
    print('FAIL line', 9690, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    np = m.node('window', 'playing_page')
except BaseException as _e:
    _SOFT.append((9690, repr(_e)[:200]))
    print('FAIL line', 9690, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def paint_home():
    m.call(address=HOOKS['widget_on_paint_background'][0], args=(m.home, m.canvas, 0, 0), gap=0)

def drifts():
    return [t for t in m.timers.values() if t[1] == payload_syms['home_drift']]

def invalidated():
    return [(c[1], c[2] and tuple((signed(m.get(c[2] + 4 * i)) for i in range(4)))) for c in m.calls if c[0] == 'widget_invalidate_force']

def tex_of(w, h):
    url, = m.pixels
    px = struct.unpack(f'<{w * h}I', bytes(m.u.mem_read(m.pixels[url], w * h * 4)))
    fw, fh = (PW, -(-PW * h // w)) if PW * h > 290 * w else (-(-290 * w // h), 290)
    tw, th = (fw * O['DRIFT_ZOOM_PCT'] // 100, fh * O['DRIFT_ZOOM_PCT'] // 100)
    out = []
    for y in range(th):
        y0 = y * h // th
        y1 = max((y + 1) * h // th, y0 + 1)
        for x in range(tw):
            x0 = x * w // tw
            x1 = max((x + 1) * w // tw, x0 + 1)
            n = r = g = b = 0
            for sy in range(y0, y1, (y1 - y0 + 3) // 4):
                for p in px[sy * w + x0:sy * w + x1:(x1 - x0 + 3) // 4]:
                    r += p & 255
                    g += p >> 8 & 255
                    b += p >> 16 & 255
                    n += 1
            out.append(4278190080 | r // n | g // n << 8 | b // n << 16)

    def sharpen(line):
        return [4278190080 | sum((min(max(6 * q - p - n_ + 2 >> 2, 0), 255) << s for s, p, q, n_ in ((s, a >> s & 255, b >> s & 255, c_ >> s & 255) for s in (0, 8, 16)))) for a, b, c_ in zip([line[0]] + line[:-1], line, line[1:] + [line[-1]])]
    rows = [sharpen(out[y * tw:(y + 1) * tw]) for y in range(th)]
    cols = [sharpen([rows[y][x] for y in range(th)]) for x in range(tw)]
    rows = [[c_[y] for c_ in cols[:1] + cols + cols[-1:] * 2] for y in range(th)]
    rows = rows[:1] + rows + rows[-1:] * 2
    return dict(px=[p for r in rows for p in r], ts=tw + 3, u0=(fw - PW) // 2, v0=(fh - 290) // 2, rx=tw - fw, ry=th - fh)

def at(r, period, t):
    t %= 2 * period
    s = ((t if t < period else 2 * period - t) << 16) // period
    return r * 256 * (s * s * (3 * 65536 - 2 * s) >> 24) >> 24

def bspline(t):
    s = 256 - t
    w0 = (s ** 3 + 196608) // 393216
    w3 = (t ** 3 + 196608) // 393216
    w1 = (3 * t ** 3 + 4 * 16777216 - 6 * 256 * t * t + 196608) // 393216
    return (w0, w1, 256 - w0 - w1 - w3, w3)

def tap4(a, b, c_, d, w):
    return 4278190080 | (a & 16711935) * w[0] + (b & 16711935) * w[1] + (c_ & 16711935) * w[2] + (d & 16711935) * w[3] + 8388736 >> 8 & 16711935 | (a & 65280) * w[0] + (b & 65280) * w[1] + (c_ & 65280) * w[2] + (d & 65280) * w[3] + 32768 >> 8 & 65280

def framed(c, t):
    u, v = (c['u0'] * 256 + at(c['rx'], X, t), c['v0'] * 256 + at(c['ry'], Y, t))
    ts, px = (c['ts'], c['px'])
    wx, wy = (bspline(u & 255), bspline(v & 255))
    i = (v >> 8) * ts + (u >> 8)
    rows = [[tap4(*px[j + x:j + x + 4], wx) for x in range(PW)] for j in range(i, i + 293 * ts, ts)]
    out = [vig(tap4(*k, wy), VIG(y)) for y in range(290) for k in zip(*rows[y:y + 4])]
    return struct.pack(f'<{len(out)}I', *out)
try:
    VIG_T = {n: [int(v) for v in re.search(f'{n}\\[VIG_\\w+\\] = {{([^}}]*)}}', (ROOT / 'patch/coverflow.c').read_text()).group(1).split(',')] for n in ('vig_top', 'vig_bot')}
except BaseException as _e:
    _SOFT.append((9731, repr(_e)[:200]))
    print('FAIL line', 9731, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def VIG(y):
    y += 320 - 290
    return VIG_T['vig_top'][max(y, 0)] if y < len(VIG_T['vig_top']) else VIG_T['vig_bot'][319 - y] if y >= 320 - len(VIG_T['vig_bot']) else 256

def vig(c, k):
    return c if k >= 256 else c & 4278190080 | (c & 16711935) * k >> 8 & 16711935 | (c & 65280) * k >> 8 & 65280

def drawn():
    n = len(m.draws)
    m.call(address=HOOKS['widget_on_paint_border'][0], args=(m.art, m.canvas, 0, 0), gap=0)
    assert len(m.draws) == n + 1 and m.draws[-1][3] == (0, 0, PW, 290) and m.draws[-1][4] & 1, m.draws[n:]
    return m.draws[-1][5]
try:
    m.art_size = (319, 319)
except BaseException as _e:
    _SOFT.append((9742, repr(_e)[:200]))
    print('FAIL line', 9742, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    c = tex_of(319, 319)
except BaseException as _e:
    _SOFT.append((9742, repr(_e)[:200]))
    print('FAIL line', 9742, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    instructions[0] = 0
except BaseException as _e:
    _SOFT.append((9751, repr(_e)[:200]))
    print('FAIL line', 9751, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    frame_cost = instructions[0]
except BaseException as _e:
    _SOFT.append((9751, repr(_e)[:200]))
    print('FAIL line', 9751, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    IH = 58
except BaseException as _e:
    _SOFT.append((9754, repr(_e)[:200]))
    print('FAIL line', 9754, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ground = 4278190080
except BaseException as _e:
    _SOFT.append((9754, repr(_e)[:200]))
    print('FAIL line', 9754, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    glyph = {(x, y) for y in range(20, 38) for x in (*range(100, 104), *range(108, 112), *range(140, 170))}
except BaseException as _e:
    _SOFT.append((9754, repr(_e)[:200]))
    print('FAIL line', 9754, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ink_fb, ink_art, ink_held = (m.alloc(PW * IH * 4), m.alloc(PW * IH * 4), m.alloc(PW))
except BaseException as _e:
    _SOFT.append((9755, repr(_e)[:200]))
    print('FAIL line', 9755, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    art_px = [4278190080 | (x * 7 + y * 3) % 48 * 65793 for y in range(IH) for x in range(PW)]
except BaseException as _e:
    _SOFT.append((9756, repr(_e)[:200]))
    print('FAIL line', 9756, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    instructions[0] = 0
except BaseException as _e:
    _SOFT.append((9759, repr(_e)[:200]))
    print('FAIL line', 9759, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ink_cost = instructions[0]
except BaseException as _e:
    _SOFT.append((9759, repr(_e)[:200]))
    print('FAIL line', 9759, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bgra = lambda p: p & 4278255360 | (p & 255) << 16 | p >> 16 & 255
except BaseException as _e:
    _SOFT.append((9760, repr(_e)[:200]))
    print('FAIL line', 9760, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tid = next((t for t, v in m.timers.items() if v[1] == payload_syms['home_drift']))
except BaseException as _e:
    _SOFT.append((9763, repr(_e)[:200]))
    print('FAIL line', 9763, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.timers[tid] = (m.now + 5000, *m.timers[tid][1:])
except BaseException as _e:
    _SOFT.append((9763, repr(_e)[:200]))
    print('FAIL line', 9763, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    t = 1000 + TICK + O['DRIFT_MAX_STEP_MS']
except BaseException as _e:
    _SOFT.append((9764, repr(_e)[:200]))
    print('FAIL line', 9764, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    FADE, FTICK = (O['SLIDE_FADE_MS'], O['SLIDE_TICK_MS'])
except BaseException as _e:
    _SOFT.append((9783, repr(_e)[:200]))
    print('FAIL line', 9783, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    fade_cost = []
except BaseException as _e:
    _SOFT.append((9784, repr(_e)[:200]))
    print('FAIL line', 9784, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def blended(a, b, k):
    a, b = (struct.unpack(f'<{len(a) // 4}I', a), struct.unpack(f'<{len(b) // 4}I', b))
    return struct.pack(f'<{len(a)}I', *(tap4(x, y, 0, 0, (256 - k, k, 0, 0)) for x, y in zip(a, b)))

def faded(old, new):
    global t
    assert drawn() == blended(framed(old, t), framed(new, t), 1)
    m.advance(TICK)
    t += TICK
    f = 1 + TICK
    instructions[0] = 0
    got = drawn()
    fade_cost.append(instructions[0])
    assert got == blended(framed(old, t), framed(new, t), f * 256 // FADE)
    while f < FADE:
        m.advance(FTICK)
        t += FTICK
        f += FTICK
    assert drawn() == framed(new, t) and len(drifts()) == 1
    assert next((v[3] for v in m.timers.values() if v[1] == payload_syms['home_drift'])) == TICK
try:
    m.art_size = (638, 638)
except BaseException as _e:
    _SOFT.append((9797, repr(_e)[:200]))
    print('FAIL line', 9797, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    instructions[0] = 0
except BaseException as _e:
    _SOFT.append((9798, repr(_e)[:200]))
    print('FAIL line', 9798, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    filter_cost = instructions[0]
except BaseException as _e:
    _SOFT.append((9798, repr(_e)[:200]))
    print('FAIL line', 9798, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    old, c = (c, tex_of(638, 638))
except BaseException as _e:
    _SOFT.append((9798, repr(_e)[:200]))
    print('FAIL line', 9798, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.art_size = (1914, 1914)
except BaseException as _e:
    _SOFT.append((9800, repr(_e)[:200]))
    print('FAIL line', 9800, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    instructions[0] = 0
except BaseException as _e:
    _SOFT.append((9801, repr(_e)[:200]))
    print('FAIL line', 9801, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    big_cost = instructions[0]
except BaseException as _e:
    _SOFT.append((9801, repr(_e)[:200]))
    print('FAIL line', 9801, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    old, c = (c, tex_of(1914, 1914))
except BaseException as _e:
    _SOFT.append((9801, repr(_e)[:200]))
    print('FAIL line', 9801, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.art_sizes = {'externpic.jpg': (4000, 100)}
except BaseException as _e:
    _SOFT.append((9804, repr(_e)[:200]))
    print('FAIL line', 9804, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ps = payload_syms
except BaseException as _e:
    _SOFT.append((9811, repr(_e)[:200]))
    print('FAIL line', 9811, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    RED = (255, 20, 72)
except BaseException as _e:
    _SOFT.append((9811, repr(_e)[:200]))
    print('FAIL line', 9811, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def lum(c):
    v = [(c >> s & 255) / 255 for s in (16, 8, 0)]
    v = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]

def ratio(a, b):
    return (max(lum(a), lum(b)) + 0.05) / (min(lum(a), lum(b)) + 0.05)

def mapped(c, preset, tone=3):
    return Machine().call(address=ps['accent_map'], args=(c, preset, tone, 0), gap=0) & 4294967295

def rgba(r, g, b, a=255):
    return r | g << 8 | b << 16 | a << 24

def blend(t, k, to, a=255):
    return rgba(*(round(t * x + k) for x in to), a)

def close(x, y):
    return x >> 24 == y >> 24 and all((abs((x >> s & 255) - (y >> s & 255)) <= 2 for s in (0, 8, 16)))
try:
    OLED_ROW = (len(ACCENTS), (2187480,) * 5)
except BaseException as _e:
    _SOFT.append((9830, repr(_e)[:200]))
    print('FAIL line', 9830, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    red = color_t(16716872)
except BaseException as _e:
    _SOFT.append((9851, repr(_e)[:200]))
    print('FAIL line', 9851, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    grey = color_t(8421504)
except BaseException as _e:
    _SOFT.append((9851, repr(_e)[:200]))
    print('FAIL line', 9851, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tramp = {n: int(manifest['patch_symbols'][f'stock_{n}_trampoline'], 16) for n in ('color', 'image', 'display')}
except BaseException as _e:
    _SOFT.append((9852, repr(_e)[:200]))
    print('FAIL line', 9852, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def style_color(m, stock, name='text_color'):
    m.handlers[tramp['color']] = 'stock_color'
    out = m.alloc(4)
    m.stock_color = stock
    return (m.call(address=HOOKS['style_get_color'][0], args=(out, 4660, m.string(name), 0), gap=0) & 4294967295, m.get(out), out)
try:
    orig_hook = Machine.hook
except BaseException as _e:
    _SOFT.append((9856, repr(_e)[:200]))
    print('FAIL line', 9856, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    GET = 16778752
except BaseException as _e:
    _SOFT.append((9856, repr(_e)[:200]))
    print('FAIL line', 9856, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def hook(self, u, address, size, x):
    if self.handlers.get(address) == 'stock_color':
        a = u.reg_read(UC_MIPS_REG_A0)
        self.word(a, self.stock_color)
        self.calls.append(('stock_color', a))
        self.returns(a)
        return
    if address == GET and getattr(self, 'get_gradient', None):
        return self.get_gradient(u)
    return orig_hook(self, u, address, size, x)
try:
    Machine.hook = hook
except BaseException as _e:
    _SOFT.append((9863, repr(_e)[:200]))
    print('FAIL line', 9863, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OLED = theme_builtins()[0]
except BaseException as _e:
    _SOFT.append((9867, repr(_e)[:200]))
    print('FAIL line', 9867, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    BLUE_ROW = len(ACCENTS)
except BaseException as _e:
    _SOFT.append((9867, repr(_e)[:200]))
    print('FAIL line', 9867, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    BLUE = OLED['schemes'][BLUE_ROW]
except BaseException as _e:
    _SOFT.append((9867, repr(_e)[:200]))
    print('FAIL line', 9867, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    BLUE_TONES = BLUE['tones']
except BaseException as _e:
    _SOFT.append((9869, repr(_e)[:200]))
    print('FAIL line', 9869, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def begin(m, w):
    m.call(address=HOOKS['widget_on_paint_background'][0], args=(w, m.canvas, 0, 0), gap=0)

def end(m, w):
    m.call(address=HOOKS['widget_on_paint_border'][0], args=(w, m.canvas, 0, 0), gap=0)
try:
    SEL_TEXT, MUTED = (4278190080 | BLUE['sel_text'], 4278190080 | BLUE['muted'])
except BaseException as _e:
    _SOFT.append((9886, repr(_e)[:200]))
    print('FAIL line', 9886, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((9887, repr(_e)[:200]))
    print('FAIL line', 9887, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, rows = m.page_list(3)
except BaseException as _e:
    _SOFT.append((9887, repr(_e)[:200]))
    print('FAIL line', 9887, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    labels = []
except BaseException as _e:
    _SOFT.append((9888, repr(_e)[:200]))
    print('FAIL line', 9888, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((9902, repr(_e)[:200]))
    print('FAIL line', 9902, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    view, imgs = home_list(m)
except BaseException as _e:
    _SOFT.append((9902, repr(_e)[:200]))
    print('FAIL line', 9902, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    labels = [m.nodes[m.get(img + O['W_PARENT'])]['children'][0] for img in imgs]
except BaseException as _e:
    _SOFT.append((9903, repr(_e)[:200]))
    print('FAIL line', 9903, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((9921, repr(_e)[:200]))
    print('FAIL line', 9921, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    SCHEME_SIZE = (payload_syms['oled_light'] - payload_syms['oled_schemes']) // len(OLED['schemes'])
except BaseException as _e:
    _SOFT.append((9927, repr(_e)[:200]))
    print('FAIL line', 9927, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    k = Machine()
except BaseException as _e:
    _SOFT.append((9937, repr(_e)[:200]))
    print('FAIL line', 9937, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    k.config = {}
except BaseException as _e:
    _SOFT.append((9937, repr(_e)[:200]))
    print('FAIL line', 9937, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    kw, _ = k.page_list(5, extent=1000)
except BaseException as _e:
    _SOFT.append((9937, repr(_e)[:200]))
    print('FAIL line', 9937, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def gradient(config, stops, same_out=True, vt_get=True, style=True):
    m = Machine()
    m.config = config
    out = m.alloc(76)
    other = m.alloc(76)
    vt = m.alloc(32)
    st = m.alloc(8)
    m.word(st, vt)
    m.word(vt + 24, GET if vt_get else 0)

    def get(u):
        assert u.reg_read(UC_MIPS_REG_T9) == GET and u.reg_read(UC_MIPS_REG_A0) == st and (u.reg_read(UC_MIPS_REG_A2) == out)
        g = out if same_out else other
        m.word(g + 8, len(stops))
        for i, c in enumerate(stops):
            m.word(g + 12 + 8 * i, c)
            m.word(g + 16 + 8 * i, i)
        m.returns(g)
    m.get_gradient = get
    ret = m.call(address=ps['ringnav_style_gradient'], args=(st if style else 0, 0, out, 0), gap=0) & 4294967295
    g = out if same_out else other
    return (ret, [m.get(g + 12 + 8 * i) for i in range(len(stops))], out, other)
try:
    ret, cols, out, _ = gradient({}, [red, grey, color_t(8325668)])
except BaseException as _e:
    _SOFT.append((9955, repr(_e)[:200]))
    print('FAIL line', 9955, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    half = color_t(BLUE_TONES[2] >> 1 & 8355711)
except BaseException as _e:
    _SOFT.append((9956, repr(_e)[:200]))
    print('FAIL line', 9956, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ret, cols, _, other = gradient({}, [red], same_out=False)
except BaseException as _e:
    _SOFT.append((9959, repr(_e)[:200]))
    print('FAIL line', 9959, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    P, K = (BLUE['paper'], BLUE['ink'])
except BaseException as _e:
    _SOFT.append((9975, repr(_e)[:200]))
    print('FAIL line', 9975, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    P, K = (P & 255, K & 255)
except BaseException as _e:
    _SOFT.append((9976, repr(_e)[:200]))
    print('FAIL line', 9976, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    flipv = lambda v: P - v * (P - K) // 255
except BaseException as _e:
    _SOFT.append((9977, repr(_e)[:200]))
    print('FAIL line', 9977, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    paper, ink = (4278190080 | P * 65793, 4278190080 | K * 65793)
except BaseException as _e:
    _SOFT.append((9978, repr(_e)[:200]))
    print('FAIL line', 9978, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def light_image(config, name, plate, prep=None):
    m = Machine()
    m.config = config
    if prep:
        prep(m)
    m.handlers[tramp['image']] = 'stock_image'
    bm = m.alloc(96)
    data = m.alloc(64)
    m.word(bm, 4)
    m.word(bm + 4, 4)
    m.word(bm + 8, 16)
    m.u.mem_write(bm + 14, struct.pack('<H', 3))
    m.word(bm + 20, data)
    m.u.mem_write(data, bytes([255, 255, 255, 255]) + (bytes([68, 68, 68, 255]) if plate else bytes(4)) * 15)
    m.call(address=HOOKS['image_manager_add'][0], args=(16778496, m.string(name), bm, 0), gap=0)
    return bytes(m.u.mem_read(data, 8))

def image(config, name, fmt=3, prep=None, px=None):
    m = Machine()
    m.config = config
    m.handlers[tramp['image']] = 'stock_image'
    if prep:
        prep(m)
    bm = m.alloc(96)
    data = m.alloc(64)
    m.word(bm, 2)
    m.word(bm + 4, 2)
    m.word(bm + 8, 12)
    m.u.mem_write(bm + 14, struct.pack('<H', fmt))
    m.word(bm + 20, data)
    px = px or [bytes([72, 20, 255, 128]), bytes([128, 128, 128, 255])]
    for y in range(2):
        m.u.mem_write(data + 12 * y, px[0] + px[1] + b'\xee' * 4)
    ret = m.call(address=HOOKS['image_manager_add'][0], args=(16778496, m.string(name), bm, 0), gap=0)
    assert ret == 0 and [c[1:] for c in m.calls if c[0] == 'stock_image'][0][2] == bm
    return [bytes(m.u.mem_read(data + 12 * y, 12)) for y in range(2)]
try:
    hi = BLUE_TONES[3]
except BaseException as _e:
    _SOFT.append((10020, repr(_e)[:200]))
    print('FAIL line', 10020, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    want = bytes([hi & 255, hi >> 8 & 255, hi >> 16, 128]) + bytes([128, 128, 128, 255]) + b'\xee' * 4
except BaseException as _e:
    _SOFT.append((10020, repr(_e)[:200]))
    print('FAIL line', 10020, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    saved, TRACE = (TRACE, io.StringIO())
except BaseException as _e:
    _SOFT.append((10023, repr(_e)[:200]))
    print('FAIL line', 10023, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((10023, repr(_e)[:200]))
    print('FAIL line', 10023, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.config = {}
except BaseException as _e:
    _SOFT.append((10023, repr(_e)[:200]))
    print('FAIL line', 10023, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    px = image({}, 'eqdrop_dot')
except BaseException as _e:
    _SOFT.append((10025, repr(_e)[:200]))
    print('FAIL line', 10025, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    TRACE = saved
except BaseException as _e:
    _SOFT.append((10027, repr(_e)[:200]))
    print('FAIL line', 10027, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    stock = bytes([72, 20, 255, 128, 128, 128, 128, 255]) + b'\xee' * 4
except BaseException as _e:
    _SOFT.append((10028, repr(_e)[:200]))
    print('FAIL line', 10028, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ROUNDED_ART = ('file:///mnt/mmc/.sldp/', 'file:///mnt/mmc/.coverflow/pl')
except BaseException as _e:
    _SOFT.append((10037, repr(_e)[:200]))
    print('FAIL line', 10037, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def rgba(config, name, pixels_, fmt=3):
    m = Machine()
    m.config = config
    m.handlers[tramp['image']] = 'stock_image'
    bm = m.alloc(96)
    data = m.alloc(4 * len(pixels_))
    m.word(bm, len(pixels_))
    m.word(bm + 4, 1)
    m.word(bm + 8, 4 * len(pixels_))
    m.u.mem_write(bm + 14, struct.pack('<H', fmt))
    m.word(bm + 20, data)
    at = ((0, 1, 2, 3), (3, 2, 1, 0), (2, 1, 0, 3), (1, 2, 3, 0))[fmt - 1]

    def packed(c, a):
        p = bytearray(4)
        for offset, value in zip(at, (c >> 16, c >> 8 & 255, c & 255, a)):
            p[offset] = value
        return p
    m.u.mem_write(data, b''.join((packed(c, a) for c, a in pixels_)))
    m.call(address=HOOKS['image_manager_add'][0], args=(16778496, m.string(name), bm, 0), gap=0)
    raw = bytes(m.u.mem_read(data, 4 * len(pixels_)))
    return [(raw[4 * i + at[0]] << 16 | raw[4 * i + at[1]] << 8 | raw[4 * i + at[2]], raw[4 * i + at[3]]) for i in range(len(pixels_))]

def pixels(config, name, colors):
    return [c for c, _ in rgba(config, name, [(c, 255) for c in colors])]
try:
    S = O['CONFIRM_SURFACE']
except BaseException as _e:
    _SOFT.append((10085, repr(_e)[:200]))
    print('FAIL line', 10085, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ink = BLUE['chevron']
except BaseException as _e:
    _SOFT.append((10100, repr(_e)[:200]))
    print('FAIL line', 10100, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def grey_of(c):
    return ((c >> 16) * 299 + (c >> 8 & 255) * 587 + (c & 255) * 114) // 1000 * 65793
try:
    edge = 255 - (255 - (ink & 255)) * (255 - 138) // (255 - 20)
except BaseException as _e:
    _SOFT.append((10102, repr(_e)[:200]))
    print('FAIL line', 10102, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    edge *= 65793
except BaseException as _e:
    _SOFT.append((10102, repr(_e)[:200]))
    print('FAIL line', 10102, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    px = [(16716872, 255), (16747172, 255), (16777215, 255), (16716872, 128), (16777215, 0)]
except BaseException as _e:
    _SOFT.append((10103, repr(_e)[:200]))
    print('FAIL line', 10103, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    disc = [(16716872, 255), (16777215, 255), (16761040, 255), (16716872, 0), (0, 0)]
except BaseException as _e:
    _SOFT.append((10104, repr(_e)[:200]))
    print('FAIL line', 10104, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
import subprocess
from ipod import imagemagick

def display_page(m, sub):
    m.word(payload_syms['display_sub'], sub)
    view = m.node('scroll_view', 'scroll_view_display', [m.entry(0) for _ in range(4)])
    for e in m.nodes[view]['children']:
        m.word(e + O['W_PARENT'], view)
    m.top = m.node('window', 'display_page', [m.node('list_view', 'list_view_display', [view])])
    m.nodes[m.wm] = {'type': 'window_manager', 'children': [m.top]}
    m.calls = []
    assert m.call(address=HOOKS['systemset_display_page_init'][0], args=(m.top, 5, 0, 0), gap=0) == 0
    assert m.calls[0][:3] == ('stock_display', m.top, 5)
    m.icons_set = [m.text(c[2]) for c in m.calls if c[0] == 'image_base_set_image']
    return view

def display(config, card=True, sub=1, cls=None, setup=None):
    CONFIG.clear()
    CONFIG.update(config)
    SETUP[:] = [setup] if setup else []
    m = (cls or QueueMachine)()
    SETUP.clear()
    m.restack = True
    m.rockbox_mode = 33261 if card else 0
    m.handlers[tramp['display']] = 'stock_display'
    view = display_page(m, sub)
    return (m, view, setting_rows(m, view) if sub == 1 else None)

def setting_rows(m, view):
    lab = lambda r: m.nodes[m.nodes[m.nodes[r]['children'][0]]['children'][1]].get('text', '')
    kids = m.nodes[view]['children']
    return [next((r for r in kids if lab(r).startswith(k))) for k in ('Accent', 'Home', 'Battery')]

def row_text(r):
    b = m.nodes[m.nodes[r]['children'][0]]
    t = m.nodes[b['children'][1]]['text']
    v = [m.nodes[k]['text'] for k in b['children'] if m.nodes[k].get('name') == '_setting_value']
    return f'{t}: {v[0]}' if v else t
try:
    m, main, _ = display({}, sub=0)
except BaseException as _e:
    _SOFT.append((10196, repr(_e)[:200]))
    print('FAIL line', 10196, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    kids = m.nodes[main]['children']
except BaseException as _e:
    _SOFT.append((10197, repr(_e)[:200]))
    print('FAIL line', 10197, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    keep_lit = kids[2]
except BaseException as _e:
    _SOFT.append((10199, repr(_e)[:200]))
    print('FAIL line', 10199, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.nodes[kids[4]]['children'][0], O['EVT_CLICK'])
except BaseException as _e:
    _SOFT.append((10201, repr(_e)[:200]))
    print('FAIL line', 10201, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((10201, repr(_e)[:200]))
    print('FAIL line', 10201, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    cfv = display_page(m, 2)
except BaseException as _e:
    _SOFT.append((10205, repr(_e)[:200]))
    print('FAIL line', 10205, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    cf_sort, cf_big, cf_position, cf_gap, cf_refresh = m.nodes[cfv]['children']
except BaseException as _e:
    _SOFT.append((10205, repr(_e)[:200]))
    print('FAIL line', 10205, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ctl = display_page(m, 3)
except BaseException as _e:
    _SOFT.append((10208, repr(_e)[:200]))
    print('FAIL line', 10208, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    wheel, hold, tone, lock, wake = m.nodes[ctl]['children']
except BaseException as _e:
    _SOFT.append((10208, repr(_e)[:200]))
    print('FAIL line', 10208, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    view = display_page(m, 1)
except BaseException as _e:
    _SOFT.append((10210, repr(_e)[:200]))
    print('FAIL line', 10210, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rows = setting_rows(m, view)
except BaseException as _e:
    _SOFT.append((10210, repr(_e)[:200]))
    print('FAIL line', 10210, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    kids = m.nodes[view]['children']
except BaseException as _e:
    _SOFT.append((10212, repr(_e)[:200]))
    print('FAIL line', 10212, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    light, song_artist, song_length, np_time = (kids[0], kids[4], kids[5], kids[6])
except BaseException as _e:
    _SOFT.append((10212, repr(_e)[:200]))
    print('FAIL line', 10212, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    buttons = [m.nodes[r]['children'][0] for r in rows]
except BaseException as _e:
    _SOFT.append((10217, repr(_e)[:200]))
    print('FAIL line', 10217, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    labels = [m.nodes[b]['children'][1] for b in buttons]
except BaseException as _e:
    _SOFT.append((10217, repr(_e)[:200]))
    print('FAIL line', 10217, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def texts():
    return [row_text(r) for r in rows]
try:
    keep_label = m.nodes[m.nodes[keep_lit]['children'][0]]['children'][1]
except BaseException as _e:
    _SOFT.append((10225, repr(_e)[:200]))
    print('FAIL line', 10225, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.nodes[keep_lit]['children'][0], O['EVT_CLICK'])
except BaseException as _e:
    _SOFT.append((10226, repr(_e)[:200]))
    print('FAIL line', 10226, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((10226, repr(_e)[:200]))
    print('FAIL line', 10226, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    load = HOOKS['ui_loader_load_widget_with_parent'][0]
except BaseException as _e:
    _SOFT.append((10234, repr(_e)[:200]))
    print('FAIL line', 10234, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'], 16)] = 'stock_ui_load'
except BaseException as _e:
    _SOFT.append((10234, repr(_e)[:200]))
    print('FAIL line', 10234, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def loaded(name):
    m.calls = []
    m.call(address=load, args=(m.string(name), 0, 0, 0), gap=0)
    return [m.text(c[1]) for c in m.calls if c[0] == 'stock_ui_load']
try:
    settings_m = m
except BaseException as _e:
    _SOFT.append((10240, repr(_e)[:200]))
    print('FAIL line', 10240, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from build import segments
from ipod import AUDIT, VALUE_NAMES
try:
    PAGE = 4096
except BaseException as _e:
    _SOFT.append((10243, repr(_e)[:200]))
    print('FAIL line', 10243, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    TEXT = [(v, (v + m_ + 4095 & ~4095) - v) for _, (t, o, v, _, f, m_, fl, _) in segments((B / 'demo').read_bytes()) if t == 1 and fl & 1 and (v < BASE)][0]
except BaseException as _e:
    _SOFT.append((10244, repr(_e)[:200]))
    print('FAIL line', 10244, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    SITES = {int(a, 16): g['value'] for g in AUDIT['immediates'] if isinstance(g['value'], str) for a, _ in g['sites']}
except BaseException as _e:
    _SOFT.append((10245, repr(_e)[:200]))
    print('FAIL line', 10245, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    GEOM_OLED = [g.name for g in geometry_sets(theme_builtins())].index('oled')
except BaseException as _e:
    _SOFT.append((10246, repr(_e)[:200]))
    print('FAIL line', 10246, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def set_geom(m, g):
    m.byte((m.call(address=payload_syms['theme'], args=(0, 0, 0, 0), gap=0) & 4294967295) + THEME_GEOM_OFF, g)

def set_value(m, name, v):
    m.u.mem_write(payload_syms['boot_values'] + 2 * VALUE_NAMES.index(name), struct.pack('<H', v))

def booted(values=(), fail=lambda page: False, geom=None):
    m = Machine()
    m.u.mem_protect(*TEXT, 5)
    m.record_syscalls(fail)
    m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'], 16)] = 'stock_ui_load'
    for name, v in values:
        set_value(m, name, v)
    if geom is not None:
        set_geom(m, geom)
    before = {a: m.get(a) for a in SITES}
    m.call(address=load, args=(m.string('system_bar'), 0, 0, 0), gap=0)
    return (m, before)

def boot_vals(g):
    return struct.unpack(f'<{len(VALUE_NAMES)}H', m.u.mem_read(payload_syms['boot_values'] + 2 * len(VALUE_NAMES) * g, 2 * len(VALUE_NAMES)))
try:
    m, before = booted(geom=0)
except BaseException as _e:
    _SOFT.append((10260, repr(_e)[:200]))
    print('FAIL line', 10260, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, before = booted((('pitch', 28),), geom=0)
except BaseException as _e:
    _SOFT.append((10262, repr(_e)[:200]))
    print('FAIL line', 10262, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    pages = sorted({a & ~(PAGE - 1) for a, v in SITES.items() if v == 'pitch'})
except BaseException as _e:
    _SOFT.append((10263, repr(_e)[:200]))
    print('FAIL line', 10263, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    gm, gbefore = booted((('pitch', 28),), fail=lambda page: page == pages[1], geom=0)
except BaseException as _e:
    _SOFT.append((10270, repr(_e)[:200]))
    print('FAIL line', 10270, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    om, obefore = booted()
except BaseException as _e:
    _SOFT.append((10275, repr(_e)[:200]))
    print('FAIL line', 10275, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    oled = boot_vals(GEOM_OLED)
except BaseException as _e:
    _SOFT.append((10275, repr(_e)[:200]))
    print('FAIL line', 10275, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    gm, gbefore = booted(fail=lambda page: True)
except BaseException as _e:
    _SOFT.append((10281, repr(_e)[:200]))
    print('FAIL line', 10281, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def gloaded(name, sets):
    gm.word(payload_syms['ui_redirects'], gm.string(name))
    gm.byte(payload_syms['ui_redirects'] + 4, sets)
    gm.calls = []
    gm.call(address=load, args=(gm.string(name), 0, 0, 0), gap=0)
    return [gm.text(c[1]) for c in gm.calls if c[0] == 'stock_ui_load']
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((10289, repr(_e)[:200]))
    print('FAIL line', 10289, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'], 16)] = 'stock_ui_load'
except BaseException as _e:
    _SOFT.append((10289, repr(_e)[:200]))
    print('FAIL line', 10289, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def redirect(name):
    m.word(payload_syms['ui_redirects'], m.string(name))
    m.u.mem_write(payload_syms['ui_redirects'] + 4, b'\x01')
try:
    nm = Machine()
except BaseException as _e:
    _SOFT.append((10294, repr(_e)[:200]))
    print('FAIL line', 10294, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    nm.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'], 16)] = 'stock_ui_load'
except BaseException as _e:
    _SOFT.append((10294, repr(_e)[:200]))
    print('FAIL line', 10294, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    nm.calls = []
except BaseException as _e:
    _SOFT.append((10304, repr(_e)[:200]))
    print('FAIL line', 10304, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OPT_KEY = {'SETTING_LIGHT': 'LIGHT', 'SETTING_ACCENT': 'OLED_ACCENT', 'SETTING_HOME': 'HOME', 'SETTING_SHORTCUT': 'SHORTCUT', 'SETTING_BATTERY': 'BATTERY'}
except BaseException as _e:
    _SOFT.append((10312, repr(_e)[:200]))
    print('FAIL line', 10312, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def preset_writes(config):
    out = []
    for o in OLED['opts']:
        key = o.get('key') or OPT_KEY[o['setting']]
        n, d = theme_keys(theme_builtins())[key][:2] if o.get('key') or key == 'OLED_ACCENT' else {'LIGHT': (3, 0), 'HOME': (3, 0), 'SHORTCUT': (12, 12), 'BATTERY': (4, 0)}[key]
        s = config.get(key, '')
        v = int(s) if s.isdigit() and int(s) < n else d
        if o['values'] and o['def'] >= 0 and (not o['values'] >> v & 1):
            v = o['def']
        if o['preset'] >= 0 and v != o['keep'] and (v != o['preset']):
            out.append((o['preset'], 'IPOD', key))
    return out

def bar_of(nm, *children):
    b = nm.node('system_bar', 'system_bar', list(children))
    nm.word(b + O['W_PARENT'], nm.wm)
    nm.word(b + O['W_W'], 375)
    nm.word(b + O['W_H'], 26)
    nm.word(syms['system_bar'], b)
    return b

def channels(c):
    return (c & 255, c >> 8 & 255, c >> 16 & 255)

def oled_on(m):
    m.record_syscalls()
    m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'], 16)] = 'stock_ui_load'
    m.call(address=load, args=(m.string('system_bar'), 0, 0, 0), gap=0)
    return m
try:
    rm = Machine()
except BaseException as _e:
    _SOFT.append((10344, repr(_e)[:200]))
    print('FAIL line', 10344, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rimg = rm.node('image', 'img_sel', image='select')
except BaseException as _e:
    _SOFT.append((10344, repr(_e)[:200]))
    print('FAIL line', 10344, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rm.icons = []
except BaseException as _e:
    _SOFT.append((10347, repr(_e)[:200]))
    print('FAIL line', 10347, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rm.nodes[rimg]['image'] = 'unselect'
except BaseException as _e:
    _SOFT.append((10347, repr(_e)[:200]))
    print('FAIL line', 10347, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = settings_m
except BaseException as _e:
    _SOFT.append((10349, repr(_e)[:200]))
    print('FAIL line', 10349, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sort_label, refresh_label = (m.nodes[m.nodes[r]['children'][0]]['children'][1] for r in (cf_sort, cf_refresh))
except BaseException as _e:
    _SOFT.append((10353, repr(_e)[:200]))
    print('FAIL line', 10353, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = m.handler(m.nodes[cf_refresh]['children'][0], O['EVT_CLICK'])
except BaseException as _e:
    _SOFT.append((10355, repr(_e)[:200]))
    print('FAIL line', 10355, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.calls = []
except BaseException as _e:
    _SOFT.append((10355, repr(_e)[:200]))
    print('FAIL line', 10355, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    d.rockbox_mode = 33261
except BaseException as _e:
    _SOFT.append((10363, repr(_e)[:200]))
    print('FAIL line', 10363, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    CONFIG['SHORTCUT'] = '7'
except BaseException as _e:
    _SOFT.append((10367, repr(_e)[:200]))
    print('FAIL line', 10367, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    d = Machine()
except BaseException as _e:
    _SOFT.append((10367, repr(_e)[:200]))
    print('FAIL line', 10367, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    d.calls = []
except BaseException as _e:
    _SOFT.append((10369, repr(_e)[:200]))
    print('FAIL line', 10369, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def click(i):
    m.calls = []
    f, ctx = m.handler(buttons[i], O['EVT_CLICK'])
    assert m.call(address=f, args=(ctx, m.event, 0, 0), gap=0) == 0
    return [c[1:] for c in m.calls if c[0] == 'write_int_config']
try:
    home = asset_tree(m, HOME_PAGE)
except BaseException as _e:
    _SOFT.append((10377, repr(_e)[:200]))
    print('FAIL line', 10377, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[HOOKS['home_page_init'][0] + 12] = 'stock_home'
except BaseException as _e:
    _SOFT.append((10378, repr(_e)[:200]))
    print('FAIL line', 10378, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    art = named(m, home, 'img_homeart')
except BaseException as _e:
    _SOFT.append((10380, repr(_e)[:200]))
    print('FAIL line', 10380, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def home_bounds(full):
    split, right = (geometry_sets(theme_builtins())[0].split_row, geometry_sets(theme_builtins())[0].right_x)
    for name, sub in (('list_view_home', full), ('list_view_homeset', True)):
        x0, end, row = (OLED['home_text_x'], OLED[f'home_label_end[{int(sub)}]'], OLED['home_full_row'] if sub else split)
        menu = named(m, home, name)
        assert m.get(menu + O['W_W']) == (375 if sub else split) and m.get(menu + O['W_Y']) == OLED['home_top'], name
        for r in m.nodes[m.nodes[menu]['children'][0]]['children']:
            label = m.nodes[r]['children'][0]
            x, y, width, height = cf_geometry(m, label)
            e = min(end, right) if m.nodes[label].get('name') == 'label_shuffle_songs' else end
            assert x == x0 and width > 0 and (x + width == row - e) and (m.get(r + O['W_W']) == row), (name, x, width, row)
    assert m.nodes[art]['visible'] == (not full)
    assert tuple((signed(m.get(art + O[k])) for k in ('W_X', 'W_Y', 'W_W', 'W_H'))) == (split, 0, 375 - split, 320 - geometry_sets(theme_builtins())[0].bar)

def carousel(on):
    """Home: Carousel: stock's carousel and arrows in the lists' place, the art hidden; else the
    main list, the carousel hidden, neither taking taps while hidden."""

    def shown(name):
        w = named(m, home, name)
        n = m.nodes[w]
        taps = n.get('sensitive', int(n['asset'].get('sensitive') != 'false'))
        assert n['visible'] == taps, name
        return n['visible']
    assert [shown(n) for n in ('slide_menu', 'img_left', 'img_right', 'list_view_home', 'list_view_homeset')] == [on] * 3 + [not on, 0]
    if on:
        assert not m.nodes[art]['visible']
try:
    writes = click(1)
except BaseException as _e:
    _SOFT.append((10410, repr(_e)[:200]))
    print('FAIL line', 10410, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    writes = click(1)
except BaseException as _e:
    _SOFT.append((10413, repr(_e)[:200]))
    print('FAIL line', 10413, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    light_label = m.nodes[m.nodes[light]['children'][0]]['children'][1]
except BaseException as _e:
    _SOFT.append((10417, repr(_e)[:200]))
    print('FAIL line', 10417, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    f, ctx = f_light, ctx_light = m.handler(m.nodes[light]['children'][0], O['EVT_CLICK'])
except BaseException as _e:
    _SOFT.append((10418, repr(_e)[:200]))
    print('FAIL line', 10418, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    writes = click(0)
except BaseException as _e:
    _SOFT.append((10433, repr(_e)[:200]))
    print('FAIL line', 10433, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    bar = m.node('window', 'system_bar')
except BaseException as _e:
    _SOFT.append((10438, repr(_e)[:200]))
    print('FAIL line', 10438, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, view, rows = display({'ACCENT': '2', 'OLED_ACCENT': '3', 'HOME': '1', 'BATTERY': '2', 'SHORTCUT': '1'})
except BaseException as _e:
    _SOFT.append((10445, repr(_e)[:200]))
    print('FAIL line', 10445, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, view, rows = display({'BATTERY': '3'})
except BaseException as _e:
    _SOFT.append((10446, repr(_e)[:200]))
    print('FAIL line', 10446, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class RockboxMachine(Machine):

    def hook(self, u, address, size, unused):
        name = self.handlers.get(address, '')
        if not name.startswith('r:'):
            return super().hook(u, address, size, unused)
        name = name[2:].split('@')[0]
        a = u.reg_read(UC_MIPS_REG_A0)
        self.calls.append((name, a))
        self.returns(33554432 if name == 'fopen' else 0)

def rockbox(card):
    m = RockboxMachine()
    m.rockbox_mode = 33261 if card else 0
    for n in ('fopen@GLIBC_2.2', 'fclose@GLIBC_2.2', 'system@GLIBC_2.0'):
        m.handlers[syms[n]] = 'r:' + n
    m.mock('save_memoryplay_info', 'player_stop', 'switch_charge_enable', 'navigator_to_with_context')
    assert m.call(address=payload_syms['rockbox_open'], args=(0, m.event, 0, 0), gap=0) == 0
    return (m, [c[0] for c in m.calls])
try:
    m, names = rockbox(True)
except BaseException as _e:
    _SOFT.append((10461, repr(_e)[:200]))
    print('FAIL line', 10461, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    texts = {c[0]: m.text(c[1]) for c in m.calls if c[0] in ('fopen', 'system')}
except BaseException as _e:
    _SOFT.append((10462, repr(_e)[:200]))
    print('FAIL line', 10462, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, names = rockbox(False)
except BaseException as _e:
    _SOFT.append((10467, repr(_e)[:200]))
    print('FAIL line', 10467, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, view, _ = display({}, sub=0)
except BaseException as _e:
    _SOFT.append((10471, repr(_e)[:200]))
    print('FAIL line', 10471, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    stock = (B / 'stock-demo').read_bytes()
except BaseException as _e:
    _SOFT.append((10476, repr(_e)[:200]))
    print('FAIL line', 10476, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def at(va, n):
    return next((stock[o + va - v:o + va - v + n] for _, (t, o, v, _, f, *_) in segments(stock) if t == 1 and v <= va < v + f))
from ipod import corner_inset

class SettingsMachine(Machine):

    def hook(self, u, address, size, x):
        name = self.handlers.get(address, '')
        if name == 'stock_list_layout':
            assert u.reg_read(UC_MIPS_REG_T9) == address
            view = u.reg_read(UC_MIPS_REG_A1)
            lst = self.get(view + O['W_PARENT'])
            y = 0
            ih, dh = (self.get(lst + O[k]) for k in ('ROW_HEIGHT', 'LIST_DEFAULT_ITEM_HEIGHT'))
            for c in self.nodes[view]['children']:
                if not self.nodes[c]['visible']:
                    continue
                if not self.get(c + O['W_W']):
                    self.word(c + O['W_W'], self.get(view + O['W_W']))
                h = ih or self.get(c + O['W_H']) or dh
                self.word(c + O['W_Y'], y)
                self.word(c + O['W_H'], h)
                y += h
            self.word(view + O['VIEW_CONTENT_H'], y)
            self.layouts += 1
            self.returns(0)
            return
        if name == 'widget_on' and u.reg_read(UC_MIPS_REG_A1) == O['EVT_CLICK']:
            a = u.reg_read(UC_MIPS_REG_A0)
            em = self.alloc(4)
            it = self.alloc(40)
            self.word(a + O['W_EMITTER'], em)
            self.word(em, it)
            self.word(it + O['EMIT_TYPE'], O['EVT_CLICK'])
            self.nodes[a]['click'] = (u.reg_read(UC_MIPS_REG_A2), u.reg_read(UC_MIPS_REG_A3))
        if name == 'image_set_draw_type':
            self.nodes[u.reg_read(UC_MIPS_REG_A0)]['draw_type'] = u.reg_read(UC_MIPS_REG_A1)
        return super().hook(u, address, size, x)
try:
    SET = {k: O['SET_' + k] for k in ('ROW', 'TOP', 'ROWS', 'ICON', 'ICON_X', 'GAP', 'TEXT_X', 'EDGE', 'STOCK_ROW')}
except BaseException as _e:
    _SOFT.append((10507, repr(_e)[:200]))
    print('FAIL line', 10507, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def geometry(m, w):
    return tuple((signed(m.get(w + O[k])) for k in ('W_X', 'W_Y', 'W_W', 'W_H')))

def settings(builder, default=SET['ROW'], sub=0, boot=None):
    m = SettingsMachine()
    m.layouts = 0
    boot and boot(m)
    m.mock('list_item_create', 'button_create', 'image_create', 'hscroll_label_create', 'label_create', 'widget_use_style', 'widget_set_name', 'image_set_draw_type', 'image_base_set_image', 'set_hscroll_label_attribute', 'widget_on', 'widget_set_text_utf8', 'widget_set_visible', 'widget_move_resize')
    m.handlers[syms['widget_destroy_children']] = 'widget_destroy_children'
    for n in syms:
        if n.endswith('@GLIBC_2.0') and syms[n] not in m.handlers:
            m.handlers[syms[n]] = n
    m.handlers[O['LIST_VIEW_LAYOUT']] = 'stock_list_layout'
    view = m.node('scroll_view')
    lst = m.node('list_view', 'list_view', [view])
    m.top = m.node('window', 'display_page' if builder == 'display' else 'sysset_page', [lst])
    m.word(view + O['W_PARENT'], lst)
    m.word(lst + O['W_PARENT'], m.top)
    m.word(m.top + O['W_PARENT'], m.wm)
    m.word(lst + O['ROW_HEIGHT'], 0)
    m.word(lst + O['LIST_DEFAULT_ITEM_HEIGHT'], default)
    m.word(lst + O['W_Y'], SET['TOP'])
    m.word(lst + O['W_H'], SET['ROWS'] * SET['ROW'])
    m.nodes[m.wm] = {'type': 'window_manager', 'children': [m.top]}
    m.word(view + O['W_W'], 375)
    m.word(view + O['W_H'], SET['ROWS'] * SET['ROW'])
    if builder == 'display':
        m.handlers[tramp['display']] = 'stock_display'
        m.nodes[view]['name'] = 'scroll_view_display'
        m.word(payload_syms['display_sub'], sub)
        assert m.call(address=HOOKS['systemset_display_page_init'][0], args=(m.top, 5, 0, 0), gap=0) == 0
    else:
        for unused in range(2):
            m.call(address=builder, args=(m.top, 0, 0, 0), gap=0)
            for c in m.calls:
                if c[0] == 'widget_lookup':
                    m.nodes[view if m.text(c[2]).startswith('scroll_view') else lst]['name'] = m.text(c[2])
            m.calls.clear()
        assert m.call(address=builder, args=(m.top, 0, 0, 0), gap=0) == 0
    return (m, view)

def lay(m, view):
    return m.call(address=m.get(O['LIST_VIEW_LAYOUT_SLOT']), args=(4660, view, 0, 0), gap=0)

def tree(m, view):
    return [(geometry(m, i), [(geometry(m, b), [geometry(m, c) for c in m.nodes[b]['children']]) for b in m.nodes[i]['children']]) for i in m.nodes[view]['children']]

def stock_list(boot, default):
    m = SettingsMachine()
    m.layouts = 0
    boot(m)
    m.handlers[O['LIST_VIEW_LAYOUT']] = 'stock_list_layout'
    m.mock('widget_move_resize', 'image_set_draw_type')
    view = m.node('scroll_view', 'scroll_view_more')
    lst = m.node('list_view', 'list_view_more', [view])
    m.top = m.node('window', 'playermore_page', [lst])
    m.word(view + O['W_PARENT'], lst)
    m.word(lst + O['W_PARENT'], m.top)
    m.word(m.top + O['W_PARENT'], m.wm)
    m.nodes[m.wm] = {'type': 'window_manager', 'children': [m.top]}
    m.word(lst + O['ROW_HEIGHT'], 0)
    m.word(lst + O['LIST_DEFAULT_ITEM_HEIGHT'], default)
    m.word(view + O['W_W'], 375)
    m.word(view + O['W_H'], 294)
    return (m, view)

def stock_row(m, view, children, h=0):
    item = m.node('list_item')
    button = m.node('button', '0', children)
    m.nodes[item]['children'] = [button]
    m.word(item + O['W_W'], 0)
    m.word(item + O['W_PARENT'], view)
    m.word(button + O['W_PARENT'], item)
    m.word(item + O['W_H'], h)
    m.nodes[view]['children'].append(item)
    em = m.alloc(4)
    it = m.alloc(40)
    m.word(button + O['W_EMITTER'], em)
    m.word(em, it)
    m.word(it + O['EMIT_TYPE'], O['EVT_CLICK'])
    for k, v in zip(('W_X', 'W_Y', 'W_W', 'W_H'), (20, 0, 335, 70)):
        m.word(button + O[k], v)
    for c in children:
        m.word(c + O['W_PARENT'], button)
    return (item, button)

def box(m, kind, name, x, y, w, h):
    c = m.node(kind, name)
    for k, v in zip(('W_X', 'W_Y', 'W_W', 'W_H'), (x, y, w, h)):
        m.word(c + O[k], v)
    return c

def speed_row(m, view):
    labels = [box(m, 'hscroll_label', 'title', 72, 12, 40, 20), box(m, 'hscroll_label', 'note', 72, 44, 253, 16)]
    images = [box(m, 'image', f'img_forward_{i}', x, 9, 42, 61) for i, x in enumerate((120, 177, 231, 283))]
    icon = box(m, 'image', 'image', 10, 0, 52, 70)
    item, button = stock_row(m, view, [icon, *labels, *images])
    return (item, button, icon, labels, images)
try:
    m, view = stock_list(oled_on, 32)
except BaseException as _e:
    _SOFT.append((10564, repr(_e)[:200]))
    print('FAIL line', 10564, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    label = box(m, 'hscroll_label', 'hscroll_label', 72, 0, 204, 70)
except BaseException as _e:
    _SOFT.append((10565, repr(_e)[:200]))
    print('FAIL line', 10565, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sw = box(m, 'image', 'image1', 276, 0, 50, 70)
except BaseException as _e:
    _SOFT.append((10565, repr(_e)[:200]))
    print('FAIL line', 10565, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ab, ab_button = stock_row(m, view, [box(m, 'image', 'image', 10, 0, 52, 70), label, sw], 78)
except BaseException as _e:
    _SOFT.append((10565, repr(_e)[:200]))
    print('FAIL line', 10565, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    plain, _ = stock_row(m, view, [box(m, 'hscroll_label', 'hscroll_label_1', 10, 0, 266, 70)], 0)
except BaseException as _e:
    _SOFT.append((10566, repr(_e)[:200]))
    print('FAIL line', 10566, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    item, button, icon, labels, images = speed_row(m, view)
except BaseException as _e:
    _SOFT.append((10567, repr(_e)[:200]))
    print('FAIL line', 10567, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[int(manifest['patch_symbols']['stock_playermore_trampoline'], 16)] = 'stock_playermore'
except BaseException as _e:
    _SOFT.append((10568, repr(_e)[:200]))
    print('FAIL line', 10568, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OG = OLED['geoms']['oled']
except BaseException as _e:
    _SOFT.append((10571, repr(_e)[:200]))
    print('FAIL line', 10571, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    TITLE_PX, META_PX = (OLED['font_px[ROLE_TITLE]'], OLED['font_px[ROLE_META]'])
except BaseException as _e:
    _SOFT.append((10571, repr(_e)[:200]))
    print('FAIL line', 10571, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    SEL_IN = OLED['sel_inset']
except BaseException as _e:
    _SOFT.append((10571, repr(_e)[:200]))
    print('FAIL line', 10571, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    sx = OG.text_x - 20
except BaseException as _e:
    _SOFT.append((10574, repr(_e)[:200]))
    print('FAIL line', 10574, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def grid_rows(m, view, n):
    rows, tiles, names = ([], [], [])
    for r in range(n):
        item = m.node('list_item')
        m.word(item + O['W_PARENT'], view)
        m.word(item + O['W_H'], 160)
        m.nodes[view]['children'].append(item)
        rows.append(item)
        for x in (14, 132, 250):
            cover = box(m, 'image', f'imgalbum_{len(tiles)}', 0, 0, 110, 110)
            name = box(m, 'hscroll_label', '', 0, 116, 110, 20)
            b = box(m, 'button', f'album_{len(tiles)}', x, 0, 110, 160)
            m.nodes[b]['children'] = [cover, name]
            m.word(b + O['W_PARENT'], item)
            for c in (cover, name):
                m.word(c + O['W_PARENT'], b)
            em = m.alloc(4)
            it = m.alloc(40)
            m.word(b + O['W_EMITTER'], em)
            m.word(em, it)
            m.word(it + O['EMIT_TYPE'], O['EVT_CLICK'])
            m.nodes[item]['children'].append(b)
            tiles.append(b)
            names.append(name)
    return (rows, tiles, names)
try:
    m, view = stock_list(oled_on, 32)
except BaseException as _e:
    _SOFT.append((10605, repr(_e)[:200]))
    print('FAIL line', 10605, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.top]['name'] = 'artistinfo_page'
except BaseException as _e:
    _SOFT.append((10605, repr(_e)[:200]))
    print('FAIL line', 10605, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    lst = m.get(view + O['W_PARENT'])
except BaseException as _e:
    _SOFT.append((10606, repr(_e)[:200]))
    print('FAIL line', 10606, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rows, tiles, names = grid_rows(m, view, 2)
except BaseException as _e:
    _SOFT.append((10607, repr(_e)[:200]))
    print('FAIL line', 10607, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    t, _ = stock_row(m, view, [box(m, 'hscroll_label', 'hscroll_label', 72, 0, 204, 70)], 78)
except BaseException as _e:
    _SOFT.append((10612, repr(_e)[:200]))
    print('FAIL line', 10612, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[view]['children'] = [t]
except BaseException as _e:
    _SOFT.append((10612, repr(_e)[:200]))
    print('FAIL line', 10612, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, view = stock_list(oled_on, 32)
except BaseException as _e:
    _SOFT.append((10613, repr(_e)[:200]))
    print('FAIL line', 10613, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[m.top]['name'] = 'artistinfo_page'
except BaseException as _e:
    _SOFT.append((10613, repr(_e)[:200]))
    print('FAIL line', 10613, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    lst = m.get(view + O['W_PARENT'])
except BaseException as _e:
    _SOFT.append((10614, repr(_e)[:200]))
    print('FAIL line', 10614, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rows, tiles, names = grid_rows(m, view, 2)
except BaseException as _e:
    _SOFT.append((10615, repr(_e)[:200]))
    print('FAIL line', 10615, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    i = O['TILE_INSET']
except BaseException as _e:
    _SOFT.append((10615, repr(_e)[:200]))
    print('FAIL line', 10615, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

class TitleMachine(ArtistMachine):

    def hook(self, u, address, size, unused):
        if address == syms['getFormatString']:
            self.keys.append(self.text(u.reg_read(REGS[0])))
            self.u.mem_write(u.reg_read(REGS[1]), 'Unknown Artist'.encode() + b'\x00')
            self.returns(0)
            return
        return super().hook(u, address, size, unused)
try:
    m, view = stock_list(lambda _: None, 78)
except BaseException as _e:
    _SOFT.append((10637, repr(_e)[:200]))
    print('FAIL line', 10637, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    item, button, icon, labels, images = speed_row(m, view)
except BaseException as _e:
    _SOFT.append((10637, repr(_e)[:200]))
    print('FAIL line', 10637, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.handlers[int(manifest['patch_symbols']['stock_playermore_trampoline'], 16)] = 'stock_playermore'
except BaseException as _e:
    _SOFT.append((10638, repr(_e)[:200]))
    print('FAIL line', 10638, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    qm = oled_on(Machine())
except BaseException as _e:
    _SOFT.append((10642, repr(_e)[:200]))
    print('FAIL line', 10642, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    qw, qr, qe = qm.table_page(name='playerqueue_page')
except BaseException as _e:
    _SOFT.append((10642, repr(_e)[:200]))
    print('FAIL line', 10642, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def pl_more(boot):
    m = Machine()
    boot(m)
    m.handlers[int(manifest['patch_symbols']['stock_playlistsmore_trampoline'], 16)] = 'stock_playlistsmore'
    b = [(box(m, 'image', i, 10, 0, 52, 28), box(m, 'hscroll_label', l, 72, 0, 253, 28)) for i, l in (('img_rename', 'label_rename'), ('img_del', 'label_del'))]
    win = m.top = m.node('window', 'playlistsmore_page', [x for p in b for x in p])
    m.word(win + O['W_PARENT'], m.wm)
    assert m.call(address=HOOKS['localmusic_playlistsmore_page_init'][0], args=(win, 1, 0, 0), gap=0) == 0
    return (m, win, b)
try:
    m, win, pl = pl_more(oled_on)
except BaseException as _e:
    _SOFT.append((10679, repr(_e)[:200]))
    print('FAIL line', 10679, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, win, pl = pl_more(lambda _: None)
except BaseException as _e:
    _SOFT.append((10682, repr(_e)[:200]))
    print('FAIL line', 10682, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def check_row(m, item, slot):
    top = 30 + m.get(m.get(m.get(item + O['W_PARENT']) + O['W_PARENT']) + O['W_Y']) + slot * SET['ROW']
    for b in m.nodes[item]['children']:
        bx, by, bw, bh = geometry(m, b)
        assert (bx, by, bw, bh) == (0, 0, 375, geometry(m, item)[3])
        icon = label = trail = None
        for c in m.nodes[b]['children']:
            x, y, w, h = geometry(m, c)
            kind = m.nodes[c]['type']
            if kind == 'image' and w == SET['ICON']:
                icon = (x, y, w, h)
                box = (x, top + y, w, h)
                assert (x, h) == (SET['ICON_X'], SET['ICON']) and y * 2 + h == bh and (m.nodes[c]['draw_type'] == O['IMAGE_DRAW_SCALE_DOWN'])
            elif kind == 'image':
                assert w == 50 and x + w == 375 - SET['EDGE'], (x, w)
                trail = x
                box = (x, top + y + (h - 50) // 2 + 13, 50, 25)
            else:
                label = (x, w)
                size = 20 if h < 30 else 24
                box = (x, top + y + (h - size) // 2, w, size)
            x, y, w, h = box
            inset = max(corner_inset(y), corner_inset(y + h))
            assert inset <= x and x + w <= 375 - inset, (m.nodes[c]['type'], box, inset)
        if icon and label:
            assert label[0] == icon[0] + icon[2] + SET['GAP']
        elif label:
            assert label[0] == SET['TEXT_X']
        if label and (not trail):
            assert label[0] + label[1] == 375 - SET['TEXT_X']
        if label and trail:
            assert label[0] + label[1] <= trail + 30
try:
    m = oled_on(Machine())
except BaseException as _e:
    _SOFT.append((10714, repr(_e)[:200]))
    print('FAIL line', 10714, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 1
except BaseException as _e:
    _SOFT.append((10714, repr(_e)[:200]))
    print('FAIL line', 10714, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(4, name='language_page')
except BaseException as _e:
    _SOFT.append((10714, repr(_e)[:200]))
    print('FAIL line', 10714, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[es[2]]['children'] = [m.node('image', image='select')]
except BaseException as _e:
    _SOFT.append((10714, repr(_e)[:200]))
    print('FAIL line', 10714, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = oled_on(Machine())
except BaseException as _e:
    _SOFT.append((10716, repr(_e)[:200]))
    print('FAIL line', 10716, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 1
except BaseException as _e:
    _SOFT.append((10716, repr(_e)[:200]))
    print('FAIL line', 10716, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(4, name='display_page')
except BaseException as _e:
    _SOFT.append((10716, repr(_e)[:200]))
    print('FAIL line', 10716, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m = Machine()
except BaseException as _e:
    _SOFT.append((10717, repr(_e)[:200]))
    print('FAIL line', 10717, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.pressed = 1
except BaseException as _e:
    _SOFT.append((10717, repr(_e)[:200]))
    print('FAIL line', 10717, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    w, es = m.page_list(4, name='language_page')
except BaseException as _e:
    _SOFT.append((10717, repr(_e)[:200]))
    print('FAIL line', 10717, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[es[2]]['children'] = [m.node('image', image='select')]
except BaseException as _e:
    _SOFT.append((10717, repr(_e)[:200]))
    print('FAIL line', 10717, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, view = settings('display', default=32, sub=1, boot=oled_on)
except BaseException as _e:
    _SOFT.append((10719, repr(_e)[:200]))
    print('FAIL line', 10719, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def stacked_row(m, view):
    title, detail = (m.node('hscroll_label'), m.node('hscroll_label'))
    for c, box in ((title, (10, 12, 266, 20)), (detail, (10, 44, 266, 16))):
        for k, v in zip(('W_X', 'W_Y', 'W_W', 'W_H'), box):
            m.word(c + O[k], v)
    b = m.node('button', children=[title, detail])
    for k, v in zip(('W_X', 'W_W', 'W_H'), (20, 335, 70)):
        m.word(b + O[k], v)
    item = m.node('list_item', children=[b])
    m.word(item + O['W_H'], 78)
    m.nodes[view]['children'].insert(1, item)
    return (item, title, detail)
try:
    m, view = settings(5005692, default=32, boot=oled_on)
except BaseException as _e:
    _SOFT.append((10733, repr(_e)[:200]))
    print('FAIL line', 10733, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    item, title, detail = stacked_row(m, view)
except BaseException as _e:
    _SOFT.append((10733, repr(_e)[:200]))
    print('FAIL line', 10733, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    rows = [i for i in m.nodes[view]['children'] if m.nodes[i]['visible']]
except BaseException as _e:
    _SOFT.append((10734, repr(_e)[:200]))
    print('FAIL line', 10734, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    hs = [geometry(m, i)[3] for i in rows]
except BaseException as _e:
    _SOFT.append((10734, repr(_e)[:200]))
    print('FAIL line', 10734, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    ys = [geometry(m, i)[1] for i in rows]
except BaseException as _e:
    _SOFT.append((10736, repr(_e)[:200]))
    print('FAIL line', 10736, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    _, ty, _, th = geometry(m, title)
except BaseException as _e:
    _SOFT.append((10737, repr(_e)[:200]))
    print('FAIL line', 10737, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    _, dy, _, dh = geometry(m, detail)
except BaseException as _e:
    _SOFT.append((10737, repr(_e)[:200]))
    print('FAIL line', 10737, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    after = tree(m, view)
except BaseException as _e:
    _SOFT.append((10738, repr(_e)[:200]))
    print('FAIL line', 10738, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, view = settings(5005692)
except BaseException as _e:
    _SOFT.append((10739, repr(_e)[:200]))
    print('FAIL line', 10739, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    item, *_ = stacked_row(m, view)
except BaseException as _e:
    _SOFT.append((10739, repr(_e)[:200]))
    print('FAIL line', 10739, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OLED_SET_TEXT = OG.text_x - 2
except BaseException as _e:
    _SOFT.append((10742, repr(_e)[:200]))
    print('FAIL line', 10742, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, view = settings(5005692, default=32, boot=oled_on)
except BaseException as _e:
    _SOFT.append((10743, repr(_e)[:200]))
    print('FAIL line', 10743, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    item, title, detail = stacked_row(m, view)
except BaseException as _e:
    _SOFT.append((10743, repr(_e)[:200]))
    print('FAIL line', 10743, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b = m.nodes[item]['children'][0]
except BaseException as _e:
    _SOFT.append((10744, repr(_e)[:200]))
    print('FAIL line', 10744, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    lead = m.node('image')
except BaseException as _e:
    _SOFT.append((10744, repr(_e)[:200]))
    print('FAIL line', 10744, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[b]['children'][:0] = [lead]
except BaseException as _e:
    _SOFT.append((10746, repr(_e)[:200]))
    print('FAIL line', 10746, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, view = settings(5005692, default=32, boot=oled_on)
except BaseException as _e:
    _SOFT.append((10750, repr(_e)[:200]))
    print('FAIL line', 10750, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    item, title, detail = stacked_row(m, view)
except BaseException as _e:
    _SOFT.append((10750, repr(_e)[:200]))
    print('FAIL line', 10750, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    b = m.nodes[item]['children'][0]
except BaseException as _e:
    _SOFT.append((10751, repr(_e)[:200]))
    print('FAIL line', 10751, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    lead, trail = (m.node('image'), m.node('image'))
except BaseException as _e:
    _SOFT.append((10751, repr(_e)[:200]))
    print('FAIL line', 10751, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[b]['children'][:0] = [lead]
except BaseException as _e:
    _SOFT.append((10755, repr(_e)[:200]))
    print('FAIL line', 10755, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, view = settings(5059988, default=72)
except BaseException as _e:
    _SOFT.append((10771, repr(_e)[:200]))
    print('FAIL line', 10771, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    before = tree(m, view)
except BaseException as _e:
    _SOFT.append((10771, repr(_e)[:200]))
    print('FAIL line', 10771, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    after = tree(m, view)
except BaseException as _e:
    _SOFT.append((10771, repr(_e)[:200]))
    print('FAIL line', 10771, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OLT = theme_builtins()[0]
except BaseException as _e:
    _SOFT.append((10775, repr(_e)[:200]))
    print('FAIL line', 10775, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OLG = next((g for g in geometry_sets(theme_builtins()) if g.name == 'oled'))
except BaseException as _e:
    _SOFT.append((10775, repr(_e)[:200]))
    print('FAIL line', 10775, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def oled_set(m, fail=lambda page: False):
    m.config.setdefault('THEME', '3')
    m.record_syscalls(fail)
    m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'], 16)] = 'stock_ui_load'
    m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0], args=(m.string('system_bar'), 0, 0, 0), gap=0)
    return m
try:
    m, view = settings(5059988, boot=oled_set)
except BaseException as _e:
    _SOFT.append((10792, repr(_e)[:200]))
    print('FAIL line', 10792, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    before = tree(m, view)
except BaseException as _e:
    _SOFT.append((10792, repr(_e)[:200]))
    print('FAIL line', 10792, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    after = tree(m, view)
except BaseException as _e:
    _SOFT.append((10793, repr(_e)[:200]))
    print('FAIL line', 10793, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, view = settings(5059988)
except BaseException as _e:
    _SOFT.append((10795, repr(_e)[:200]))
    print('FAIL line', 10795, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    odd = m.node('list_item')
except BaseException as _e:
    _SOFT.append((10795, repr(_e)[:200]))
    print('FAIL line', 10795, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    other = m.node('button')
except BaseException as _e:
    _SOFT.append((10795, repr(_e)[:200]))
    print('FAIL line', 10795, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m.nodes[odd]['children'] = [other]
except BaseException as _e:
    _SOFT.append((10796, repr(_e)[:200]))
    print('FAIL line', 10796, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    m, view = settings(5059988)
except BaseException as _e:
    _SOFT.append((10802, repr(_e)[:200]))
    print('FAIL line', 10802, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    items = m.nodes[view]['children']
except BaseException as _e:
    _SOFT.append((10802, repr(_e)[:200]))
    print('FAIL line', 10802, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    buttons = [m.nodes[i]['children'][0] for i in items]
except BaseException as _e:
    _SOFT.append((10803, repr(_e)[:200]))
    print('FAIL line', 10803, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
from build import FACES
try:
    FACE_NAMES = [FACES[k].name for k in FACES]
except BaseException as _e:
    _SOFT.append((10817, repr(_e)[:200]))
    print('FAIL line', 10817, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    STATES = ('normal', 'focused', 'disable')
except BaseException as _e:
    _SOFT.append((10818, repr(_e)[:200]))
    print('FAIL line', 10818, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OLED_ACCENT = next((o['def'] for o in OLT['opts'] if o['setting'] == 'SETTING_ACCENT'))
except BaseException as _e:
    _SOFT.append((10819, repr(_e)[:200]))
    print('FAIL line', 10819, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OLED_TONES = OLT['schemes'][OLED_ACCENT]['tones']
except BaseException as _e:
    _SOFT.append((10819, repr(_e)[:200]))
    print('FAIL line', 10819, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    OLED_BLUE = OLED_TONES[0]
except BaseException as _e:
    _SOFT.append((10819, repr(_e)[:200]))
    print('FAIL line', 10819, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def appearance(config):
    m, _, _ = display(config, sub=0)
    view = display_page(m, 1)
    return (m, view, m.nodes[view]['children'])

def oled_bar(nm, *children):
    b = nm.node('system_bar', 'system_bar', list(children))
    nm.word(b + O['W_PARENT'], nm.wm)
    nm.word(b + O['W_W'], 375)
    nm.word(b + O['W_H'], OLG.bar)
    nm.word(syms['system_bar'], b)
    return b

def action_page(nm, sorts=True):
    alabel = nm.node('hscroll_label', text='Play All')
    aplay = nm.node('image')
    asort = nm.node('image')
    for c, box in ((alabel, (70, 0, 130, 50)), (aplay, (16, 0, 50, 50)), (asort, (306, 0, 50, 50))):
        for off, v in zip(('W_X', 'W_Y', 'W_W', 'W_H'), box):
            nm.word(c + O[off], v)
    aview = nm.node('view', 'view_navbar_allplay', [alabel, aplay] + [asort] * sorts)
    nm.word(aview + O['W_H'], 28)
    awin = nm.node('window', 'allmusic_page', [aview])
    nm.top = awin
    nm.word(awin + O['W_PARENT'], nm.wm)
    atitle = nm.node('hscroll_label', 'label_title')
    abar = oled_bar(nm, atitle)
    nm.call(address=HOOKS['widget_on_paint_background'][0], args=(abar, nm.canvas, 0, 0), gap=0)
    return (alabel, aplay, asort, abar)
try:
    am = oled_set(Machine())
except BaseException as _e:
    _SOFT.append((10849, repr(_e)[:200]))
    print('FAIL line', 10849, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    al, ap, asort, abar = action_page(am)
except BaseException as _e:
    _SOFT.append((10849, repr(_e)[:200]))
    print('FAIL line', 10849, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    boxof = lambda w: tuple((am.get(w + O[k]) for k in ('W_X', 'W_Y', 'W_W', 'W_H')))
except BaseException as _e:
    _SOFT.append((10850, repr(_e)[:200]))
    print('FAIL line', 10850, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    am.calls = []
except BaseException as _e:
    _SOFT.append((10854, repr(_e)[:200]))
    print('FAIL line', 10854, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    am = oled_set(Machine())
except BaseException as _e:
    _SOFT.append((10856, repr(_e)[:200]))
    print('FAIL line', 10856, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    al, ap, asort, abar = action_page(am, sorts=False)
except BaseException as _e:
    _SOFT.append((10856, repr(_e)[:200]))
    print('FAIL line', 10856, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tm = oled_set(Machine())
except BaseException as _e:
    _SOFT.append((10874, repr(_e)[:200]))
    print('FAIL line', 10874, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tl = tm.node('hscroll_label', 'title', text='Games')
except BaseException as _e:
    _SOFT.append((10874, repr(_e)[:200]))
    print('FAIL line', 10874, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tsheet = tm.node('view', 'sheet')
except BaseException as _e:
    _SOFT.append((10875, repr(_e)[:200]))
    print('FAIL line', 10875, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tlist = tm.node('view', 'list', [tl])
except BaseException as _e:
    _SOFT.append((10875, repr(_e)[:200]))
    print('FAIL line', 10875, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tm.top = tm.node('window', 'games_page', [tsheet, tlist])
except BaseException as _e:
    _SOFT.append((10875, repr(_e)[:200]))
    print('FAIL line', 10875, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tbar_label = tm.node('hscroll_label', 'label_title')
except BaseException as _e:
    _SOFT.append((10876, repr(_e)[:200]))
    print('FAIL line', 10876, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tbar = oled_bar(tm, tbar_label)
except BaseException as _e:
    _SOFT.append((10876, repr(_e)[:200]))
    print('FAIL line', 10876, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tpaint = lambda: (tm.call(address=HOOKS['widget_on_paint_background'][0], args=(tbar, tm.canvas, 0, 0), gap=0), tm.nodes[tbar_label].get('text'))[1]
except BaseException as _e:
    _SOFT.append((10877, repr(_e)[:200]))
    print('FAIL line', 10877, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tdue = payload_syms['title_due']
except BaseException as _e:
    _SOFT.append((10878, repr(_e)[:200]))
    print('FAIL line', 10878, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tforced = lambda: ('widget_invalidate_force', tbar_label) in [c[:2] for c in tm.calls]
except BaseException as _e:
    _SOFT.append((10878, repr(_e)[:200]))
    print('FAIL line', 10878, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tadded = lambda: [x[:2] for x in tm.calls if x[:2] == ('timer_add', tdue)]
except BaseException as _e:
    _SOFT.append((10879, repr(_e)[:200]))
    print('FAIL line', 10879, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    tm.nodes[tl]['text'] = 'Renamed'
except BaseException as _e:
    _SOFT.append((10882, repr(_e)[:200]))
    print('FAIL line', 10882, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def tt_bar(timetitle):
    ttm = Machine()
    ttm.config['TIMETITLE'] = timetitle
    oled_set(ttm)
    ttm.clock_minutes = True
    ttt = ttm.node('hscroll_label', 'label_title')
    ttc = ttm.node('hscroll_label', 'label_clock')
    ttb = oled_bar(ttm, ttt, ttc)

    def show(win):
        ttm.top = win
        ttm.word(win + O['W_PARENT'], ttm.wm)
        ttm.call(address=HOOKS['widget_on_paint_background'][0], args=(ttb, ttm.canvas, 0, 0), gap=0)
        ttm.advance(1)
        return (ttm.nodes[ttt].get('text', ''), ttm.nodes[ttc].get('text'))
    return (ttm, ttc, show)
try:
    hm = Machine()
except BaseException as _e:
    _SOFT.append((10902, repr(_e)[:200]))
    print('FAIL line', 10902, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    hm.config['TIMETITLE'] = '0'
except BaseException as _e:
    _SOFT.append((10902, repr(_e)[:200]))
    print('FAIL line', 10902, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    hm.clock_minutes = True
except BaseException as _e:
    _SOFT.append((10902, repr(_e)[:200]))
    print('FAIL line', 10902, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    htt, hcl = (hm.node('hscroll_label', 'label_title'), hm.node('hscroll_label', 'label_clock'))
except BaseException as _e:
    _SOFT.append((10903, repr(_e)[:200]))
    print('FAIL line', 10903, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    hbar = oled_bar(hm, htt, hcl, hm.node('image', 'img_state', visible=0))
except BaseException as _e:
    _SOFT.append((10904, repr(_e)[:200]))
    print('FAIL line', 10904, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    hhome = hm.node('window', 'home_page')
except BaseException as _e:
    _SOFT.append((10905, repr(_e)[:200]))
    print('FAIL line', 10905, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    hm.top = hhome
except BaseException as _e:
    _SOFT.append((10905, repr(_e)[:200]))
    print('FAIL line', 10905, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    PH = OLT['art_placeholder']
except BaseException as _e:
    _SOFT.append((10914, repr(_e)[:200]))
    print('FAIL line', 10914, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    PH0 = (52 - PH) // 2
except BaseException as _e:
    _SOFT.append((10914, repr(_e)[:200]))
    print('FAIL line', 10914, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    fm = Machine()
except BaseException as _e:
    _SOFT.append((10915, repr(_e)[:200]))
    print('FAIL line', 10915, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise
try:
    fm.handlers[tramp['image']] = 'stock_image'
except BaseException as _e:
    _SOFT.append((10915, repr(_e)[:200]))
    print('FAIL line', 10915, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)):
        raise

def ftile(name):
    bm = fm.alloc(96)
    data = fm.alloc(4 * 52 * 52)
    fm.word(bm, 52)
    fm.word(bm + 4, 52)
    fm.word(bm + 8, 4 * 52)
    fm.u.mem_write(bm + 14, struct.pack('<H', 3))
    fm.word(bm + 20, data)
    fm.u.mem_write(data, (bytes([0, 0, 255, 255]) + bytes([128, 128, 128, 255]) * 51) * 52)
    fm.call(address=HOOKS['image_manager_add'][0], args=(16778496, fm.string(name), bm, 0), gap=0, count=20000000)
    raw = bytes(fm.u.mem_read(data, 4 * 52 * 52))
    return lambda x, y, c=3: raw[4 * (52 * y + x) + c]
try:
    for fname in ('list_folder', 'list_song'):
        fa = ftile(fname)
        assert [fa(x, 26) for x in (PH0 - 1, PH0, PH0 + PH - 1, PH0 + PH)] == [0, 255, 255, 0] and [fa(26, y) for y in (PH0 - 1, PH0, PH0 + PH - 1, PH0 + PH)] == [0, 255, 255, 0], fname
        assert fa(PH0, 26, 2) != fa(PH0 + 1, 26, 2) == fa(PH0 + 2, 26, 2), fname
except BaseException as _e:
    _SOFT.append((10922, repr(_e)[:200]))
    print('FAIL line', 10922, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)) or type(_e).__name__ == 'UcError':
        raise
try:
    fa = ftile('file:///mnt/mmc/.downpic/fd1.jpg')
except BaseException as _e:
    _SOFT.append((10925, repr(_e)[:200]))
    print('FAIL line', 10925, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)) or type(_e).__name__ == 'UcError':
        raise
try:
    assert fa(0, 0) == 0 and fa(51, 26) == fa(26, 0) == 255
except BaseException as _e:
    _SOFT.append((10925, repr(_e)[:200]))
    print('FAIL line', 10925, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)) or type(_e).__name__ == 'UcError':
        raise
try:
    for fname in ('list_tf', 'list_album'):
        fa = ftile(fname)
        assert fa(0, 0) == fa(51, 26) == 255, fname
except BaseException as _e:
    _SOFT.append((10926, repr(_e)[:200]))
    print('FAIL line', 10926, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)) or type(_e).__name__ == 'UcError':
        raise
try:
    passed()
except BaseException as _e:
    _SOFT.append((10927, repr(_e)[:200]))
    print('FAIL line', 10927, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)) or type(_e).__name__ == 'UcError':
        raise
try:
    for boot in (1, 0):
        lm = Machine()
        boot and oled_set(lm)
        lw, les = lm.page_list(14, height=294, extent=672, name='playlist_page')
        window(lm, 'playlist_page', lw)
        lm.nodes[lw]['children'] = []
        lm.paint(lw)
        tops = []
        for _ in range(40):
            lm.call()
            lm.paint(lw)
            tops.append(lm.get(lw + O['SCROLL_Y']))
        assert max(tops) == (672 - OLG.list_end if boot else 672 - 294), (boot, max(tops))
except BaseException as _e:
    _SOFT.append((10930, repr(_e)[:200]))
    print('FAIL line', 10930, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)) or type(_e).__name__ == 'UcError':
        raise
try:
    passed()
except BaseException as _e:
    _SOFT.append((10937, repr(_e)[:200]))
    print('FAIL line', 10937, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)) or type(_e).__name__ == 'UcError':
        raise
try:
    sk = Machine()
except BaseException as _e:
    _SOFT.append((10939, repr(_e)[:200]))
    print('FAIL line', 10939, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)) or type(_e).__name__ == 'UcError':
        raise
try:
    sw, srows = sk.page_list(3)
except BaseException as _e:
    _SOFT.append((10939, repr(_e)[:200]))
    print('FAIL line', 10939, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)) or type(_e).__name__ == 'UcError':
        raise
try:
    slabels = []
except BaseException as _e:
    _SOFT.append((10939, repr(_e)[:200]))
    print('FAIL line', 10939, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)) or type(_e).__name__ == 'UcError':
        raise
try:
    for row in srows:
        l = sk.node('hscroll_label')
        sk.nodes[row]['children'].append(l)
        sk.word(l + O['W_PARENT'], row)
        slabels.append(l)
except BaseException as _e:
    _SOFT.append((10940, repr(_e)[:200]))
    print('FAIL line', 10940, repr(_e)[:300], flush=True)
    import traceback
    traceback.print_exc(limit=-3)
    if isinstance(_e, (KeyboardInterrupt, SystemExit)) or type(_e).__name__ == 'UcError':
        raise
print('SOFT FAILURES', len(_SOFT))