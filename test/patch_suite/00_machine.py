#!/usr/bin/env python3
"""Execute the actual patched MIPS callback with stock filter and mocked UI services.
Requires unicorn==2.1.4. Does not emulate the entire device or flash hardware.
"""
import ctypes, hashlib, io, json, math, os, pathlib, re, sqlite3, struct, subprocess, sys, tempfile
from functools import cmp_to_key
from unicorn import Uc, UcError, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_CODE, UC_HOOK_BLOCK, UC_HOOK_INTR
from unicorn.unicorn_py3.unicorn import uclib, uccallback, uc_hook_h, HOOK_CODE_CFUNC  # 2.1.4's own: Machine.hooked registers a C hook
from unicorn.mips_const import *
sys.path.insert(0, sys.path[0] + '/../tools')  # tools/ first
from build import jal, j, segments, load_elf, symbols, BASE, SCRATCH, HOOKS, WM_PAINT_LEAF, FUNCTIONS, GLOBALS, CONTEXT_DATA, ROOT, source_sha256, sha, PRIVATE_FUNCTIONS, TAG, VERSION, BETA, PRODUCT_NAME, dev_tag
B=pathlib.Path(sys.argv[1] if len(sys.argv)>1 else 'build')
payload_syms=symbols(B/'patch.elf')
manifest=json.loads((B/'manifest.json').read_text())
if manifest.get('source_sha256') != source_sha256():
    raise SystemExit(f'{B}/manifest.json does not match the current patch sources; rebuild into a fresh directory and pass it here')
for name,key in (('demo','demo_sha256'),('stock-demo','stock_demo_sha256'),('patch.bin','patch_sha256')):
    if sha((B/name).read_bytes()) != manifest.get(key):
        raise SystemExit(f'{B/name} does not match manifest.json; rebuild into a fresh directory')
assert manifest['version'] == (dev_tag(manifest['dev']) if manifest.get('dev') else TAG), 'Wrong version'
assert manifest.get('compact_code')
from ipod import GEOM_VALS, INC, O, VALUE_NAMES  # patch/offsets.inc and its integer #defines; the geometry tables' columns
from theme import MOTION_WIDGET, MOTION_WINDOW, SCHEME_MODE_OFF, THEME_MOTION_OFF, builtins as theme_builtins, built_geometry
from theme import theme_keys  # tools/theme.py: the theme's own keys
CONFIG_READS=7+len(theme_keys(theme_builtins()))  # settings_read: the payload's seven keys (REDUCEMOTION last), then the theme's own
DC=O['DOUBLE_CLICK_MS']  # centre double-press window
# iPod accent presets: {gradient top, bottom, light tone, red tone, highlight} per Accent setting value.
ACCENTS=[tuple(int(v,16) for v in g) for g in re.findall(r'\{ 0x(\w+), 0x(\w+), 0x(\w+), 0x(\w+), 0x(\w+) \}',INC)]
# MINIMAL_ACCENT's row (Ivory, last in the Accent picker's order) draws as Graphite.
assert ACCENTS[O['MINIMAL_ACCENT']]==ACCENTS[0]
def color_t(rgb): return 0xff000000|(rgb&255)<<16|(rgb>>8&255)<<8|rgb>>16
O_GLYPH=re.search(r'#define BT_GLYPH "(\w+)"',INC)[1]  # the plain Bluetooth glyph a codec badge fades into
CONFIG={}  # config.ini [IPOD] keys a new Machine starts with; the payload reads them on first use
SEED={}  # values a Machine reads for a missing key in place of the payload's default
# Reduce Motion's flat selection bar, for the checks that pin the bar's shape and place: the bar's rest lift and press
# (tilt.c, docs/internals.md#row-press) widen it and cast a shadow, and test/fw/test_tilt.py pins those.
FLAT={'REDUCEMOTION':'1'}
SETUP=[]  # functions a new Machine runs before anything can read config (QueueMachine's constructor does)
FILL,RADIUS=(0x40<<24)|O['FILL_RGB'],9  # the stock rounded-rect checks' translucent fill and radius
LCD_COLORS=(0x9abcdef0,0x12345678)
syms=symbols(B/'stock-demo')
syms.update(PRIVATE_FUNCTIONS)
VG_MOCKS=[n for n in syms if n.startswith(('vgcanvas_','vg_gradient_'))]

def _fp64_fix():
    """The stock binary is -mfp64 (FR=1); Unicorn's MIPS32 FPU only implements FR=0, so
    L-format conversions are rewritten to their W equivalents when an image is loaded. Every
    value the traversed canvas code converts fits 32 bits, so the results are identical. An mthc1
    just before one (the 64-bit integer's high word, as hscroll_label_step's 0x5fb3f8) is dropped:
    the W form doesn't read it, and under FR=0 it would overwrite the odd register beside it.
    The payload's constants (.rodata on, in patch.elf) are read past: a packed mask's bytes can look like one."""
    elf=(B/'patch.elf').read_bytes(); shoff,=struct.unpack_from('<I',elf,0x20); size,count,names=struct.unpack_from('<3H',elf,0x2e)
    table=[struct.unpack_from('<10I',elf,shoff+size*k) for k in range(count)]
    name=lambda h: elf[table[names][4]+h[0]:].split(b'\0',1)[0]
    rodata=next(h[3] for h in table if name(h)==b'.rodata')
    data=(B/'demo').read_bytes(); fixes=[]
    for _,(t,o,v,_,f,m,flags,_) in segments(data):
        if t!=1 or not flags&1: continue
        words=struct.unpack_from(f'<{f//4}I',data,o)
        for i,w in enumerate(words):
            if rodata<=v+4*i<SCRATCH: continue
            if w>>26&0x3f==0x11 and w>>21&0x1f==0x15:
                fixes.append((v+4*i,(w&~(0x1f<<21))|(0x14<<21)))
            elif w>>26&0x3f==0x11 and w>>21&0x1f==0x07 and any(x>>26&0x3f==0x11 and x>>21&0x1f==0x15 for x in words[i+1:i+4]):
                fixes.append((v+4*i,0))
    return fixes
FP64_FIX=_fp64_fix()
REGS=[UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3]
SAVED=[UC_MIPS_REG_S0,UC_MIPS_REG_S1,UC_MIPS_REG_S2,UC_MIPS_REG_S3,
       UC_MIPS_REG_S4,UC_MIPS_REG_S5,UC_MIPS_REG_S6,UC_MIPS_REG_S7,UC_MIPS_REG_FP]
# What a mocked call may spoil, as the arrays uc_reg_write_batch takes: Machine.returns writes them in one call.
SPOILED=[UC_MIPS_REG_V1,*REGS,UC_MIPS_REG_T0,UC_MIPS_REG_T1,UC_MIPS_REG_T2,UC_MIPS_REG_T3,UC_MIPS_REG_T4,
         UC_MIPS_REG_T5,UC_MIPS_REG_T6,UC_MIPS_REG_T7,UC_MIPS_REG_T8,UC_MIPS_REG_T9]
SPOIL=ctypes.c_uint32(0xdeadbeef)
SPOILED_IDS=(ctypes.c_int*len(SPOILED))(*SPOILED); SPOILED_VALUES=(ctypes.c_void_p*len(SPOILED))(*[ctypes.addressof(SPOIL)]*len(SPOILED))
# A machine's hook is Python, and unicorn calling it at every instruction was most of a run's time. Unicorn calls
# FILTER.filter there instead, which calls the hook only where it has something to do: at each symbol's address, at
# each address a mock was ever set at (Handlers) or a test named with watch(), and anywhere from the mocks' own
# region (0x1000000) up. One marked for one machine is marked for all, which costs those only the call.
def _filter():
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run(['cc','-O2','-shared','-fPIC','-x','c','-','-o',directory+'/filter.so'],check=True,input=b'''
#include <stdint.h>
static uint8_t marked[0x1000000 >> 5];  /* a bit an instruction */
void watch(uint32_t a) { if (a < 0x1000000) marked[a >> 5] |= 1 << (a >> 2 & 7); }
void filter(void *uc, uint64_t a, uint32_t size, void *hook) {
    if (a >= 0x1000000 || marked[a >> 5] >> (a >> 2 & 7) & 1) ((void (*)(void *, uint64_t, uint32_t, void *))hook)(uc, a, size, 0);
}''')
        return ctypes.CDLL(directory+'/filter.so')
FILTER=_filter()
watch=FILTER.watch; watch.argtypes=[ctypes.c_uint32]; watch.restype=None
ENTRIES={*syms.values(),*payload_syms.values(),*(int(v,16) for v in manifest['patch_symbols'].values())}
for a in ENTRIES: watch(a)

def signed(x): return x if x<0x80000000 else x-0x100000000
SCOPE_RULES='/mnt/data/q2-library-scope'  # library_scope.c's folder marker rules, written by a library update

# Q2_TRACE=FILE logs every draw and widget-property call, each hook's return and each passed case, by value.
TRACE=open(os.environ['Q2_TRACE'],'w') if os.environ.get('Q2_TRACE') else None
def trace(*fields):
    if TRACE: TRACE.write('\t'.join(fields)+'\n')
TRACE_ARGS={'canvas_get_clip_rect':'_','canvas_set_clip_rect':'_r','canvas_set_global_alpha':'_i','canvas_translate':'_ii','canvas_offline_create':'iii',
    **dict.fromkeys(('canvas_offline_begin_draw','canvas_offline_end_draw','canvas_offline_destroy'),'_'),
    **dict.fromkeys(('canvas_set_fill_color','canvas_set_stroke_color','canvas_set_text_color'),'_x'),
    **dict.fromkeys(('canvas_fill_rect','canvas_stroke_rect'),'_iiii'),'canvas_draw_icon':'_xii','canvas_set_font':'_si',
    'canvas_draw_text_in_rect':'_wr','canvas_draw_text':'_wii','canvas_measure_text':'_w','canvas_draw_image':'_xrr',
    'canvas_fill_rounded_rect':'_rrdi','canvas_stroke_rounded_rect':'_rrdii',
    **{'vg:vgcanvas_'+n:c for n,c in (('save','_'),('restore','_'),('begin_path','_'),('close_path','_'),('fill','_'),
       ('stroke','_'),('translate','_ff'),('move_to','_ff'),('line_to','_ff'),('arc','_fffffi'),('set_fill_color','_x'),
       ('set_stroke_color','_x'),('set_fill_linear_gradient','_ffffxx'),('set_line_width','_f'),('set_line_cap','_s'))},
    'widget_set_prop_int':'nsi','widget_set_prop_str':'nss','widget_move_resize':'niiii','widget_move':'nii','widget_resize':'nii',
    'widget_set_opacity':'ni','widget_use_style':'ns','widget_set_text_utf8':'ns','widget_set_tr_text':'ns',
    'widget_set_visible':'nii','widget_set_sensitive':'ni','widget_factory_create_widget':'_sniiii',
    **dict.fromkeys(('image_create','list_item_create','button_create','hscroll_label_create','label_create'),'niiii'),
    'image_set_draw_type':'ni','image_base_set_image':'ns',
    'image_manager':'','image_manager_unload_all':'_','image_manager_unload_bitmap_by_name':'_s'}
HOOK_NAMES={a:n for a,n,_ in HOOKS.values()}
PAYLOAD_NAMES={v:n for n,v in sorted(payload_syms.items())}
def render(m,code,v):
    """One traced value; 'w' takes (UTF-32 pointer, length). Addresses never appear, so traces survive a relink."""
    if code=='w': return json.dumps(''.join(chr(m.get(v[0]+4*k)) for k in range(v[1])))
    if code in 'srd' and not v: return 'null'
    if code=='s': return json.dumps(m.text(v))
    if code=='r': return ','.join(str(signed(m.get(v+4*k))) for k in range(4))
    if code=='d': return '%08x'%m.get(v)
    if code=='f': return '%g'%struct.unpack('<f',struct.pack('<I',v))[0]
    if code=='n' and v in m.nodes: return 'n%d'%list(m.nodes).index(v)
    # ponytail: an alpha-0 colour 0x00b00000-0x00b9ffff reads as an address (P); none is drawn today
    if BASE<=v<SCRATCH+0x20000: return PAYLOAD_NAMES.get(v,'P')
    if 0x70000000<=v<0x70010000: return 'S'
    return str(signed(v)) if code=='i' else '%08x'%v


class Handlers(dict):
    """A machine's mocks, by address: one at an address no symbol names is marked for FILTER."""
    def __setitem__(self,a,name):
        if a not in ENTRIES: watch(a)
        super().__setitem__(a,name)
    def update(self,other):
        for a,name in other.items(): self[a]=name
class Machine:
    def __init__(self, patched=True):
        self.u=Uc(UC_ARCH_MIPS, UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
        data=(B/('demo' if patched else 'stock-demo')).read_bytes()
        load_elf(self.u,data)
        # Payload text is execute-only; its top page holds the scratch cell.
        if patched: self.u.mem_protect(BASE,SCRATCH-BASE,5)
        for v,w in FP64_FIX: self.u.mem_write(v,struct.pack('<I',w))
        self.u.mem_map(0x1000000,0x200000)
        self.u.mem_map(0x70000000,0x10000)
        self.next=0x1001000; self.nodes={}; self.calls=[]; self.animating=0; self.pressed=0; self.ups=[]
        self.top=0; self.wm=0x1000000; self.event=0x1000100
        self.char_px=10.0; self.strokes=[]; self.rounded=[]; self.bands=[]; self.icons=[]; self.letters=[]; self.font=None; self.vg_calls=[]; self.fake_vg=0; self.global_alpha=0
        self.rounded_fail=False; self.count=self.budget  # call()'s instruction limit, which nested() shares
        self.allocs={}; self.config=dict(CONFIG); self.seed=SEED; self.config_reads=[]; self.card=(); self.dirs=set()  # /mnt/mmc's (name, d_type) entries; deeper folders
        for f in SETUP: f(self)
        self.rebind=None; self.on_click=None; self.glide=True
        self.timers={}; self.next_timer=1; self.timer_fail=False; self.clicks=[]; self.started=[]
        self.screens=[]
        self.props={}; self.x_log=[]; self.prop_fail=False; self.slides={}; self.slide_fail=False; self.slide_on_fail=False; self.slide_easing=O['SLIDE_EASING']; self.slide_callbacks={}
        self.canvas=0x1000200; self.lcd=0x1000300; self.now=1000
        self.word(self.canvas+O['CANVAS_LCD'],self.lcd)
        for off,v in zip(('LCD_FILL_COLOR','LCD_STROKE_COLOR'),LCD_COLORS): self.word(self.lcd+O[off],v)
        self.clip=(0,0,240,240)
        self.handlers=Handlers()
        if patched:  # a file and a clock; test/playback.py tests them
            self.handlers[payload_syms['press_stamp']]='stock_press_stamp'
            self.handlers[payload_syms['lw_syscall']]='stock_lw_syscall'  # 0: no eventfd, so the UI loop idles in sleep_ms (loop_wake.c)
            # 0 from each: no inotify (card_watch.c), no pipe's descriptors (coda_rc_shim.c), and the push socket's
            # socket() already refused (player_push.c), so each keeps its clock
            for name in ('cw_syscall', 'cr_syscall', 'pp_syscall'): self.handlers[payload_syms[name]]='stock_'+name
        for name in FUNCTIONS: self.handlers[syms[name]]=name
        self.handlers[syms['strstr@GLIBC_2.0']]='strstr@GLIBC_2.0'  # libc's, which the payload's state-file reads call
        # canvas_get_vgcanvas runs stock, down to the mocked lcd_get_vgcanvas, as the rounded wrappers need
        for name in ('slide_menu_item_width','slide_menu_on_scroll_done','canvas_get_vgcanvas',
                     'widget_animator_scroll_set_params','slide_menu_set_value','toolsTimeItoa','on_wm_keyup_fun','table_client_get_virtual_h',
                     'mclGetPlayType','networkGetConnectStatus','wifi_switch'):  # Smart Wi-Fi's: stock's, as before it named them
            self.handlers.pop(syms[name],None)
        self.mock('widget_is_instance_of','widget_animator_scroll_create','widget_animator_on',
                  'widget_set_focused','widget_layout_children','event_init','value_set_int')
        if patched:
            for name in ('paint','dispatch','paint_bg'):
                self.handlers[int(manifest['patch_symbols']['stock_'+name+'_trampoline'],16)]='stock_'+name
        self.mock('pthread_create@GLIBC_2.2','pthread_detach@GLIBC_2.0','pthread_self@GLIBC_2.0')  # pthread_self: playback_poll's UI thread
        self.handlers[syms['pthread_create@GLIBC_2.2']]='thread:queue'  # playback.c's queue writer runs at once; any other thread never
        self.mock('mkdir@GLIBC_2.0')  # card_store.c's folder on the card
        self.mock('write@GLIBC_2.0','clock_gettime@GLIBC_2.2')  # ulog.c's line on stderr (a clean end's shutdown line)
        self.mock('reset_poweroptions_timer','screen_action','enable_fb','usleep@GLIBC_2.0','sprintf@GLIBC_2.0',
                  'airplayGetFlag','playpause_quick_click','time@GLIBC_2.0','localtime@GLIBC_2.0','tzset@GLIBC_2.0',
                  'strlen@GLIBC_2.0','strncpy@GLIBC_2.0','strrchr@GLIBC_2.0','strcasecmp@GLIBC_2.0','strncasecmp@GLIBC_2.0','strncmp@GLIBC_2.0',
                  'strcasestr@GLIBC_2.2',
                  'unlink@GLIBC_2.0','atoi@GLIBC_2.0','strtol@GLIBC_2.0','memcmp@GLIBC_2.0','access@GLIBC_2.0',
                  'open@GLIBC_2.0','fflush@GLIBC_2.0','fsync@GLIBC_2.0','fileno@GLIBC_2.0',  # the watchdog and the state record
                  'ioctl@GLIBC_2.0',  # usb_event.c's line request on its first pass: refused, stock's cable poll alone
                  'fopen@GLIBC_2.2', 'opendir@GLIBC_2.0', 'readdir@GLIBC_2.0',
                  'closedir@GLIBC_2.0', 'pthread_mutex_lock@GLIBC_2.0',
                  'pthread_mutex_unlock@GLIBC_2.0', 'calloc@GLIBC_2.0', 'realloc@GLIBC_2.0',
                  'strdup@GLIBC_2.0',  # no /proc/mounts: the boot logo's poll sees no card
                  'fread@GLIBC_2.0','fwrite@GLIBC_2.0','fclose@GLIBC_2.2','rename@GLIBC_2.0')
        self.word(0xa3a270,0x1000010); self.handlers[0x1000010]='scope_free'
        self.resized=[]  # the bytes each realloc kept
        self.sized={}  # a block's size, for realloc to spoil only that
        self.scope_tree={}; self.scope_markers=set(); self.scope_handles={}
        self.scope_files={}; self.scope_open={}  # SCOPE_RULES (and its .tmp): path -> bytes; handle -> [path, writing, bytes]
        self.present=set()  # paths access finds (the UI loop's Spotify poll asks for its login)
        self.stats={}  # paths stat finds, with their mtime (songs on the card: a card app's)
        self.image_size=(50,50)  # what widget_load_image decodes
        self.image_draws=[]; self.image_loads=[]
        self.style_ints={}  # (a style, its name): what style_get_int answers; else the fallback
        self.clock=(18,14)  # local (hour, minute) for time/localtime, or the one of them that fails
        self.clock_minutes=False; self.marquee=False
        self.widget_timers=False  # widget_add_timer schedules (real_hscroll), else it is only recorded
        self.loop_ms=0  # advance: timers run on a loop pass every loop_ms; 0, on their deadlines
        self.wall=(86400,86400)  # time's result, and the seconds localtime must be given
        self.rockbox_mode=0o100755; self.groovebox_mode=0  # the launchers' binaries: Rockbox on the card, Groovebox not
        self.handlers[syms['__xstat@GLIBC_2.0']]='rockbox_stat'
        self.handlers[syms['strcmp@GLIBC_2.0']]='tk_strcmp'
        self.handlers[syms['memcpy@GLIBC_2.0']]='memcpy'
        self.handlers[syms['memmove@GLIBC_2.0']]='memcpy'  # memcpy's read then write is memmove's: overlap holds
        self.handlers[syms['memset@GLIBC_2.0']]='memset'
        # the UI loop's pass asks adbd's socket for a question each second (usb_ui.c auth_poll): no adbd here
        self.handlers[syms['socket@GLIBC_2.0']]='no_socket'
        self.mock('canvas_set_global_alpha')
        self.mock('sqrtf@GLIBC_2.0','sinf@GLIBC_2.0','acosf@GLIBC_2.0',prefix='float:')
        self.mock(*VG_MOCKS,prefix='vg:')
        self.handlers[payload_syms['peq_lib_localtime_r']]='localtime_r'
        self.code_hook=self.hooked()
        for name in GLOBALS: self.byte(syms[name],0)
        for name,size in CONTEXT_DATA.items(): self.u.mem_write(syms[name],bytes(size))
        self.word(syms['g_class_type'],0xf001)
        self.byte(syms['g_backlight_status'],1)
    def probe_canvas(self, lcd_type=1, vg=0, rect=(10,10,100,40), color=None):
        """Real stock canvas code runs; mock only the services it reaches."""
        for n in ('canvas_fill_rounded_rect','canvas_stroke_rounded_rect','canvas_set_fill_color'):
            del self.handlers[syms[n]]
        self.mock('lcd_get_vgcanvas'); self.mock('tk_calloc','tk_free',prefix='alloc:')
        for off,val in ((0x10,0),(0x14,0),(0x18,239),(0x1c,239)): self.word(self.canvas+off,val)
        self.word(self.lcd+0xd0,lcd_type)
        self.rounded_fail=False; self.fake_vg=vg
        self.rect=self.alloc(16); self.color=self.alloc(4)
        for i,val in enumerate(rect): self.word(self.rect+4*i,val)
        self.word(self.color,FILL if color is None else color)
    def lcd_colors(self): return tuple(self.get(self.lcd+O[off]) for off in ('LCD_FILL_COLOR','LCD_STROKE_COLOR'))
    def x_pushes(self): return self.x_log  # every x animator's (from, to), run or still running
    def mock(self,*names,prefix=''):
        for n in names: self.handlers[syms[n]]=prefix+n
    def record_syscalls(self,fail=lambda page:False):
        """The payload's o32 syscalls, answered 0; mprotect (4125) takes effect, except RWX on a page
        fail(page) picks, answered EACCES."""
        self.syscalls=[]
        def syscall(u,intno,unused):
            n,a,b,c=(u.reg_read(r) for r in (UC_MIPS_REG_V0,*REGS[:3])); self.syscalls.append((n,a,b,c))
            if n==4125 and c==7 and fail(a): u.reg_write(UC_MIPS_REG_A3,1); u.reg_write(UC_MIPS_REG_V0,13); return
            if n==4125: u.mem_protect(a,b,c)
            u.reg_write(UC_MIPS_REG_A3,0); u.reg_write(UC_MIPS_REG_V0,0)
        self.u.hook_add(UC_HOOK_INTR,syscall)
    def scope_scan(self):
        """A library update's marker walk over scope_tree and scope_markers, into SCOPE_RULES."""
        self.call(address=payload_syms['library_scope_scan'],args=(0,0,0,0),gap=0,clear=False)
    def folder(self,path,w=None):
        """Set the browsed folder; with a surface, also scroll it home and repaint."""
        self.u.mem_write(syms['g_folder_path'],path.encode()+b'\0')
        if w: self.word(w+O['SCROLL_Y'],0); self.paint(w)
    def label(self,es,specs):
        """Give each entry None, a text, or a (text, subtitle label) pair."""
        for e,spec in zip(es,specs):
            if spec is None: continue
            text,*sub=spec if isinstance(spec,tuple) else (spec,)
            self.nodes[e]['text']=text
            if sub: self.nodes[e]['children']=[self.node('label',text=sub[0])]
    def panes(self,height=None):
        """Two four-entry scroll panes on album_page; returns (a, b, a entries, b entries)."""
        a=self.node(); b=self.node(); es=[]
        for s in (a,b):
            if height: self.word(s+O['W_H'],height)
            es.append([self.entry(s,i*48) for i in range(4)]); self.nodes[s]['children']=es[-1]
        self.top=self.node('window','album_page',[a,b])
        return a,b,*es
    def byte(self,a,v): self.u.mem_write(a,bytes([v]))
    def word(self,a,v): self.u.mem_write(a,struct.pack('<I',v&0xffffffff))
    def get(self,a): return struct.unpack('<I',self.u.mem_read(a,4))[0]
    def alloc(self,n=0x200): a=self.next; self.next+=(n+3)&~3; return a  # word-aligned, as glibc
    def string(self,s):
        data=s.encode()+b'\0'
        a=self.alloc((len(data)+3)&~3); self.u.mem_write(a,data); return a
    def wide_string(self,s):
        data=(s+'\0').encode('utf-32-le')
        a=self.alloc(len(data)); self.u.mem_write(a,data); return a
    def raw(self,a):
        out=bytearray()
        while a:  # to the page's end at a time: a page is mapped whole, so this faults where a read by the byte would
            page=self.u.mem_read(a,0x1000-(a&0xfff)); end=page.find(b'\0')
            if end>=0: return bytes(out+page[:end])
            out+=page; a+=len(page)
        return bytes(out)
    def text(self,a): return self.raw(a).decode()
    def node(self,t='scroll_view',name='',children=(),visible=1,**kw):
        a=self.alloc(); self.nodes[a]=dict(type=t,name=name,children=list(children),visible=visible,enable=1,**kw)
        self.word(a+O['W_W'],240); self.word(a+O['W_H'],240); self.word(a+O['ROW_HEIGHT'],48)
        self.word(a+(O['VIEW_CONTENT_H'] if t=='scroll_view' else O['TABLE_ROWS']),960 if t=='scroll_view' else 100)
        self.byte(a+O['VIEW_VERTICAL'],1)
        return a
    def entry(self,parent,y=0):
        """A leafless tap target: emitter with one EVT_CLICK item, widget_y offset y, height 48."""
        a=self.node('list_item')
        em=self.alloc(4); it=self.alloc(0x28)
        self.word(a+O['W_EMITTER'],em); self.word(em,it); self.word(it+O['EMIT_TYPE'],O['EVT_CLICK'])
        self.word(a+O['W_PARENT'],parent); self.word(a+O['W_Y'],y); self.word(a+O['W_H'],48)
        return a
    def selected(self,w): return self.nodes[w].get('_ringnav_index',-1)
    def paint(self,w,gap=1000):
        """Stock order: background, then (children and) border; iPod hooks both."""
        self.call(address=HOOKS['widget_on_paint_background'][0],args=(w,self.canvas,0,0),gap=gap); gap=0
        return self.call(address=HOOKS['widget_on_paint_border'][0],args=(w,self.canvas,0,0),gap=gap,clear=False)
    def drawn(self): return self.rounded or self.strokes or self.bands
    def sel(self):
        """The selected row's rectangle, from the outline (inset 1) or the bar's bands."""
        if self.rounded and self.rounded[0]['kind']=='fill' and self.rounded[0]['color']==color_t(0xeeeeec):
            x,y,w,h=self.rounded[0]['rect']
            return (x-O['SEL_INSET'],y,w+2*O['SEL_INSET'],h) if x else (x,y,w,h)  # an inset card; a square list row's spans the surface
        ys=[b[1] for b in self.bands]; x=min(b[0] for b in self.bands); w=max(b[2] for b in self.bands)
        if x==O['SEL_INSET']: x,w=0,w+2*O['SEL_INSET']  # a list bar inset SEL_INSET: the row it marks spans the surface
        return (x,min(ys),w,max(ys)-min(ys)+1)
        x,y,w,h=self.rounded[0]['rect'] if self.rounded else self.strokes[0][:4]
        return (x-1,y-1,w+2,h+2)
    def touch(self): return self.call(address=HOOKS['on_wm_tsdown_before_fun'][0],event_type=O['EVT_POINTER_DOWN'])
    def click(self,w,gap=1000): return self.call(address=HOOKS['widget_dispatch'][0],args=(w,self.event,0,0),event_type=O['EVT_CLICK'],gap=gap)
    def hook(self,u,address,size,_):
        if address==syms['widget_animator_scroll_set_params']:
            assert u.reg_read(UC_MIPS_REG_A0) not in self.slides, 'retarget must pause first'
            self.calls.append(('widget_animator_scroll_set_params',*[u.reg_read(r) for r in REGS[:3]]))
        if address not in self.handlers: return
        name=self.handlers[address]
        if not name.startswith('stock_') and address!=payload_syms['peq_lib_localtime_r']:
            assert u.reg_read(UC_MIPS_REG_T9)==address, (name,'PIC call missing t9')
        a,b,c,d=[u.reg_read(r) for r in REGS]
        n=self.nodes.get(a,{})
        self.calls.append((name,a,b,c))
        if TRACE and name in TRACE_ARGS:
            sp=u.reg_read(UC_MIPS_REG_SP); it=iter([a,b,c,d]+[self.get(sp+16+4*k) for k in range(4)]); out=[]
            for k in TRACE_ARGS[name]:
                v=(next(it),next(it)) if k=='w' else next(it)
                if k!='_': out.append(render(self,k,v))
            trace(name,*out)
        if name=='memcpy': self.u.mem_write(a,bytes(self.u.mem_read(b,c))); ret=a
        elif name=='strncpy@GLIBC_2.0': self.u.mem_write(a,self.raw(b)[:c].ljust(c,b'\0')); ret=a
        elif name=='memset': self.u.mem_write(a,bytes([b&255])*c); ret=a
        elif name=='thread:run': self.word(a,77); self.nested(c,d); ret=0  # its id written, as pthread_create does
        elif name=='thread:queue':  # the queue's records are in their files when the save returns (playback.c store_post)
            if c==payload_syms.get('w_thread'): self.word(a,77); self.nested(c,d)
            ret=0
        elif name in ('table_row_create', 'list_item_create', 'button_create', 'image_create', 'view_create',
                       'hscroll_label_create', 'gif_image_create', 'label_create'):
            kind = {'gif_image_create': 'gif'}.get(name, name.removesuffix('_create'))
            ret = self.node(kind)
            for off, value in zip((O['W_X'], O['W_Y'], O['W_W'], O['W_H']),
                                  (b, c, d, self.get(u.reg_read(UC_MIPS_REG_SP)+16))):
                self.word(ret+off, value)
            self.word(ret+O['W_PARENT'], a)
            self.nodes[a]['children'].append(ret)
        elif name=='widget_factory_create_widget':
            ret=self.node(self.text(b)); self.word(ret+O['W_PARENT'],c); self.nodes[c]['children'].append(ret)
            for off,value in zip(('W_X','W_Y','W_W','W_H'),(d,self.get(u.reg_read(UC_MIPS_REG_SP)+16),self.get(u.reg_read(UC_MIPS_REG_SP)+20),self.get(u.reg_read(UC_MIPS_REG_SP)+24))): self.word(ret+O[off],value)
        elif name=='widget_set_name': n['name']=self.text(b); ret=0
        elif name=='widget_set_enable': n['enable']=b; ret=0
        elif name=='widget_set_self_layout_params':
            n['self_layout']=tuple(self.text(x) for x in (b,c,d,self.get(u.reg_read(UC_MIPS_REG_SP)+16))); ret=0
        elif name=='widget_on': n.setdefault('handlers',[]).append((b,c,d)); ret=len(n['handlers'])
        elif name=='widget_to_local':
            x,y=signed(self.get(b)),signed(self.get(b+4))
            while a:  # stock's: each one's own offset (widget_get_offset) too, the first one's included
                if self.nodes.get(a,{}).get('type')=='scroll_view': x+=signed(self.get(a+O['SCROLL_X'])); y+=signed(self.get(a+O['SCROLL_Y']))
                x-=signed(self.get(a+O['W_X'])); y-=signed(self.get(a+O['W_Y']))
                a=self.get(a+O['W_PARENT'])
            self.word(b,x); self.word(b+4,y); ret=0
        elif name in ('widget_set_text_utf8','widget_set_tr_text'): n['text']=self.text(b); ret=0  # no language has our keys
        elif name=='image_base_set_image': n['image']=self.text(b); ret=0
        elif name=='widget_use_style': n['style']=self.text(b); ret=0
        elif name=='widget_set_text':
            chars=[]
            while self.get(b+4*len(chars)): chars.append(chr(self.get(b+4*len(chars))))
            n['text']=''.join(chars); ret=0
        elif name=='hscroll_label_cast': ret=a
        elif name=='hscroll_label_set_only_focus': self.byte(a+O['HSCROLL_ONLY_FOCUS'],b); n[name]=b; ret=0
        elif name in ('hscroll_label_start','hscroll_label_stop'):
            # stock's timer (0x5fb7ac) stops an only_focus label that isn't focused on its first tick
            n['scrolling']=name.endswith('start') and not self.u.mem_read(a+O['HSCROLL_ONLY_FOCUS'],1)[0]; ret=0
        elif name.startswith('hscroll_label_set_') or name=='set_hscroll_label_attribute':
            n[name]=b if name!='set_hscroll_label_attribute' else True; ret=0
        elif name=='image_set_draw_type': n['draw_type']=b; ret=a
        elif name=='deque_at': ret=self.row_record
        elif name=='deque_size': ret=1
        elif name in ('tk_snprintf','snprintf@GLIBC_2.0'):
            fmt=self.text(c); values=iter([d]+[self.get(u.reg_read(UC_MIPS_REG_SP)+off) for off in range(16,80,4)])
            params=[]  # %.*s takes a length, then the string (its bytes)
            for star,kind in re.findall(r'%(?:%|(\.\*)?\d*(?:\.\d+)?([sdxXuc]))',fmt):  # %.500s: Python's % applies the precision
                if not kind: continue  # %%, a literal % (strftime's '%%s' in SQL), takes no argument
                if star: n=next(values); params.append(bytes(self.u.mem_read(next(values),n)).split(b'\0')[0].decode())
                else: value=next(values); params.append(self.text(value) if kind=='s' else chr(value&255) if kind=='c' else value if kind in 'xXu' else signed(value))
            result=(re.sub(r'%\.\*s','%s',fmt) % tuple(params)).encode(); self.u.mem_write(a,result[:b-1]+b'\0'); ret=len(result)
        elif name=='atoi@GLIBC_2.0': ret=int(re.match(r'[+-]?\d+',self.text(a))[0]) if re.match(r'[+-]?\d+',self.text(a)) else 0
        elif name=='access@GLIBC_2.0': ret=0 if self.text(a) in self.present else -1
        elif name=='send@GLIBC_2.0': ret=c  # a mocked send takes it all
        elif name=='open@GLIBC_2.0': ret=0xffffffff  # no /dev/jz_watchdog here: the watchdog stays off
        elif name=='calloc@GLIBC_2.0':
            ret=0 if getattr(self,'fail_calloc',False) else self.alloc(a*b)
            self.sized[ret]=a*b
        elif name=='realloc@GLIBC_2.0' and getattr(self,'fail_realloc',False): ret=0
        elif name=='realloc@GLIBC_2.0' and not a: ret=self.alloc(b); self.sized[ret]=b  # realloc(NULL): a malloc
        elif name=='realloc@GLIBC_2.0':  # moved, as glibc may, the old block spoilt
            kept=bytes(self.u.mem_read(a,b)); ret=self.alloc(b); self.u.mem_write(ret,kept); self.u.mem_write(a,b'\xff'*min(b,self.sized.get(a,b))); self.resized.append(kept); self.sized[ret]=b
        elif name=='strdup@GLIBC_2.0': ret=self.string(self.text(a))
        elif name=='scope_free': ret=0
        elif name=='opendir@GLIBC_2.0':
            path=self.text(a); ret=self.alloc(4) if path in self.scope_tree else 0
            if ret: self.scope_handles[ret]=list(self.scope_tree[path])
        elif name=='readdir@GLIBC_2.0':
            ret=0
            if self.scope_handles.get(a):
                n,t=self.scope_handles[a].pop(0); ret=self.alloc(268)
                self.byte(ret+10,t); self.u.mem_write(ret+11,n.encode()+b'\0')
        elif name=='closedir@GLIBC_2.0': self.scope_handles.pop(a,None); ret=0
        elif name=='fopen@GLIBC_2.2':  # the marker rules file alone; any other file is missing
            path,mode=self.text(a),self.text(b); ret=0
            if path.startswith(SCOPE_RULES) and (mode=='wb' or path in self.scope_files):
                ret=self.alloc(4); self.scope_open[ret]=[path,mode=='wb',b'']
        elif name=='fwrite@GLIBC_2.0': self.scope_open[d][2]+=bytes(u.mem_read(a,b*c)); ret=c
        elif name=='fread@GLIBC_2.0': data=self.scope_files[self.scope_open[d][0]][:b*c]; u.mem_write(a,data); ret=len(data)//b
        elif name=='fclose@GLIBC_2.2':
            path,writing,data=self.scope_open.pop(a); ret=0
            if writing: self.scope_files[path]=data
        elif name=='rename@GLIBC_2.0':
            ret=0 if self.text(a) in self.scope_files else -1
            if not ret: self.scope_files[self.text(b)]=self.scope_files.pop(self.text(a))
        elif name=='unlink@GLIBC_2.0': self.scope_files.pop(self.text(a),None); ret=0
        elif name=='no_socket': ret=-1
        elif name=='rockbox_stat':
            assert a==3
            if self.text(b) in self.scope_markers: self.word(c+20,0o100644); ret=0
            elif self.text(b) in ['/mnt/mmc/'+n for n,t in self.card if t==4] or self.text(b) in self.dirs: self.word(c+20,0o40755); ret=0  # a card folder
            elif self.text(b) in self.stats: self.word(c+64,self.stats[self.text(b)]); ret=0  # its mtime: info[16]
            elif self.text(b)=='/mnt/mmc/.groovebox/q2-groovebox': self.word(c+20,self.groovebox_mode); ret=0 if self.groovebox_mode else -1
            elif self.text(b)!='/mnt/mmc/.rockbox/rockbox': ret=-1  # Tidal Downloads' rows: no such files here
            else: self.word(c+20,self.rockbox_mode); ret=0 if self.rockbox_mode else -1
        elif name=='strlen@GLIBC_2.0': ret=len(self.raw(a))  # bytes: stock strips a path's UTF-8 name byte by byte
        elif name=='strrchr@GLIBC_2.0': i=self.text(a).encode().rfind(bytes([b&255])); ret=a+i if i>=0 else 0
        elif name=='memcmp@GLIBC_2.0': x,y=bytes(u.mem_read(a,c)),bytes(u.mem_read(b,c)); ret=(x>y)-(x<y)
        elif name=='strncmp@GLIBC_2.0':
            assert a and b,'strncmp(NULL) faults in libc'
            x,y=(bytes(self.raw(v))[:c] for v in (a,b)); ret=(x>y)-(x<y)
        elif name in ('strcasecmp@GLIBC_2.0','strncasecmp@GLIBC_2.0'):  # ASCII case, as the C locale
            x,y=(bytes(self.raw(v)).lower()[:c if name.startswith('strn') else None] for v in (a,b)); ret=(x>y)-(x<y)
        elif name=='sprintf@GLIBC_2.0':  # stock toolsTimeItoa's "%02d:%02d[:%02d]"
            fmt=self.text(b); values=(c,d,self.get(u.reg_read(UC_MIPS_REG_SP)+16))
            result=(fmt % tuple(signed(v) for v in values[:fmt.count('%')])).encode(); self.u.mem_write(a,result+b'\0'); ret=len(result)
        elif name=='time@GLIBC_2.0':
            ret=-1 if self.clock=='time' else self.wall[0]
            if a and ret!=-1: self.word(a,ret)
        elif name in ('localtime@GLIBC_2.0','localtime_r'):  # struct tm: sec, min, hour, ...
            ret=0
            if hasattr(self,'epoch'): # ResumeMachine's time(): navigation.c's clock_back of it
                back=self.epoch
                while back>0x7fffffff: back-=883612800
                self.wall=(self.epoch,back)
            if self.clock!='localtime':
                assert self.get(a)==self.wall[1]; ret=b if name=='localtime_r' else self.alloc(44)
                for i,v in enumerate((59,self.clock[1],self.clock[0])): self.word(ret+4*i,v)
        elif name=='widget_set_children_layout':
            n['children_layout']=self.text(b)
            layout=self.alloc(32)
            self.word(layout+O['CHILDREN_LAYOUT_VTABLE'],O['DEFAULT_LAYOUT_VTABLE'])
            self.u.mem_write(layout+0x14,struct.pack('<HH',1,0))
            for param,off in (('xm','DEFAULT_LAYOUT_X_MARGIN'),('s','DEFAULT_LAYOUT_SPACING')):
                match=re.search(r'\b'+param+r'=(\d+)',self.text(b))
                self.byte(layout+O[off],int(match[1]) if match else 0)
            self.word(a+O['W_CHILDREN_LAYOUT'],layout); ret=0
        elif name=='widget_resize':
            self.word(a+O['W_W'],b); self.word(a+O['W_H'],c); ret=0
        elif name=='widget_vtable_on_layout_children': ret=3
        elif name in ('darray_init','darray_deinit','widget_layout_floating_children','widget_layout_self'): ret=0
        elif name=='widget_get_children_for_layout':
            children=[x for x in n['children'] if self.nodes[x]['visible']]
            values=self.alloc(4*len(children)+4)
            for i,child in enumerate(children): self.word(values+4*i,child)
            self.word(b,len(children)); self.word(b+8,values); ret=0
        elif name=='toolsWriteConfig': self.config[self.text(c)]=self.text(d); ret=0
        elif name=='toolsReadConfig':
            key=self.text(c)
            self.config_reads.append((self.text(a),self.text(b),key,self.text(self.get(u.reg_read(UC_MIPS_REG_SP)+16))))
            value=self.config.get(key,self.seed.get(key,self.config_reads[-1][3]))  # stock copies the default when the key is missing
            self.u.mem_write(d,value.encode()+b'\0'); ret=1 if key in self.config else -1
        elif name=='tk_str_end_with': ret=self.text(a).endswith(self.text(b))
        elif name=='tk_str_start_with': ret=self.raw(a).startswith(self.raw(b))  # bytes, as C: a cut UTF-8 tail is no error
        elif name=='strtol@GLIBC_2.0':  # saturates, as a 32-bit long's does; *endptr past the digits (nptr when none)
            t=re.match(r'\s*[-+]?\d+',self.text(a)); ret=max(-2**31,min(2**31-1,int(t[0]))) if t else 0
            if b: self.word(b,a+(len(t[0]) if t else 0))
        elif name=='strstr@GLIBC_2.0': i=self.text(a).find(self.text(b)); ret=a+i if i>=0 else 0
        elif name=='strcasestr@GLIBC_2.2': i=self.raw(a).lower().find(self.raw(b).lower()); ret=a+i if i>=0 else 0
        elif name=='bitmap_get_line_length': ret=self.get(a+8)
        elif name=='bitmap_lock_buffer_for_write': ret=self.get(a+0x14)  # a test bitmap keeps its pixels' address there
        elif name=='image_manager': ret=0x1000500
        elif name=='widget_move': self.word(a+O['W_X'],b); self.word(a+O['W_Y'],c); ret=0
        elif name in ('widget_move_resize_ex','widget_move_resize'):
            for off,value in zip(('W_X','W_Y','W_W','W_H'),(b,c,d,self.get(u.reg_read(UC_MIPS_REG_SP)+16))):
                self.word(a+O[off],value)
            ret=0
        elif name=='widget_lookup':
            def lookup(w):
                if self.nodes[w].get('name')==self.text(b): return w
                for child in self.nodes[w]['children']:
                    found=lookup(child) if c else (child if self.nodes[child].get('name')==self.text(b) else 0)
                    if found: return found
                return 0
            ret=lookup(a) if a in self.nodes else 0  # the window manager (self.wm) is no node: nothing found in it
        elif name=='scroll_bar_cast': ret=a
        elif name=='scroll_bar_is_mobile': ret=n.get('type')=='scroll_bar_m'
        elif name=='widget_set_opacity': self.byte(a+0x34,b); ret=0
        elif name in ('widget_set_visible','widget_set_visible_only','widget_set_sensitive'):
            n['sensitive' if name=='widget_set_sensitive' else 'visible']=b; ret=0
        elif name=='widget_animator_prop_create':
            ret=0 if self.prop_fail else self.alloc(0x80)
            if ret:
                self.word(ret+4,a); self.word(ret+O['ANIM_DURATION'],b)
                n['animated_ms']=b; n['animated_easing']=d; self.word(ret+0x24,c); n['animated']=self.text(self.get(u.reg_read(UC_MIPS_REG_SP)+16)); assert n['animated'] in ('opacity','x')
        elif name=='widget_animator_prop_set_params':
            assert self.get(u.reg_read(UC_MIPS_REG_SP)+16)==0 and self.get(u.reg_read(UC_MIPS_REG_SP)+20)==0
            if self.nodes[self.get(a+4)].get('animated')=='x':
                self.props[a]=(struct.unpack('<d',struct.pack('<II',c,d))[0],0)
                self.x_log.append((int(self.props[a][0]),0))
            ret=0
        elif name=='window_manager': ret=self.wm
        elif name=='window_manager_get_top_window': ret=self.top
        elif name=='window_manager_is_animating': ret=self.animating
        elif name=='window_manager_get_pointer_pressed': ret=self.pressed
        elif name=='widget_get_visible': ret=n.get('visible',0)
        elif name=='widget_get_type': ret=self.string(n.get('type',''))
        elif name=='widget_get_prop_str':
            # Stock text is VALUE_TYPE_WSTRING; value_str does not convert it to UTF-8.
            # An unnamed widget's name is NULL too, whatever the default: stock answers its value_str.
            ret=0 if self.text(b)=='text' else self.string(n.get(self.text(b),''))
            if self.text(b)=='name' and not n.get('name'): ret=0
        elif name=='widget_get_text': ret=self.wide_string(n.get('text',''))
        elif name=='player_playtime_and_length' and hasattr(self,'playtime'): self.word(a,self.playtime); self.word(b,225); ret=0
        elif name=='widget_get_text_utf8':
            text=n.get('text','').encode()
            self.u.mem_write(b,bytes(c))  # stock's wide-string conversion writes nothing unless all bytes fit
            if len(text)<c: self.u.mem_write(b,text+b'\0')
            ret=0
        elif name in ('widget_get_prop_bool','widget_get_prop_int'): ret=n.get(self.text(b),c)
        elif name=='mclGetLyricSize': ret=getattr(self,'lyric_size',0)
        elif name=='widget_destroy':  # out of its parent's children
            for p in self.nodes.values():
                if a in p.get('children',()): p['children'].remove(a)
            n['destroyed']=True; ret=0
        elif name=='widget_count_children': ret=0 if a==self.wm and a not in self.nodes else len(n.get('children',()))  # a case with no window manager's tree: Auto's turn walks it
        elif name=='widget_get_child': ret=n['children'][b] if b<len(n['children']) else 0
        elif name=='widget_set_prop_int': n[self.text(b)]=signed(c); ret=0
        elif name=='slider_set_value':  # stock dispatches the value change at once (o32: the double in a2:a3)
            n['value']=int(struct.unpack('<d',struct.pack('<II',c,d))[0]); ret=0
            for t,f,ctx in list(n.get('handlers',())):
                if t==O['EVT_VALUE_CHANGED']: self.echoes=getattr(self,'echoes',0)+1; self.nested(f,ctx,self.event,0,0)
        elif name=='widget_set_prop_str': n[self.text(b)]=self.text(c); ret=0
        elif name=='text_selector_count_options': ret=n.get('options',0)
        elif name=='text_selector_set_selected_index': self.word(a+O['SELECTOR_INDEX'],b); ret=0
        elif name=='widget_restack' and getattr(self,'restack',False):  # AWTK clamps the index
            kids=next((v['children'] for v in self.nodes.values() if a in v.get('children',())),None)
            if kids is not None: kids.remove(a); kids.insert(min(b,len(kids)),a)
            ret=0
        elif name=='pointer_event_init':
            self.word(a,b); self.word(a+0x10,c); self.word(a+O['EVENT_X'],d); self.word(a+O['EVENT_Y'],self.get(u.reg_read(UC_MIPS_REG_SP)+16)); ret=a
        elif name=='time_now_ms': ret=self.now & 0xffffffff
        elif name=='timer_add':
            ret=0 if self.timer_fail else self.next_timer
            if ret:
                self.next_timer+=1
                # the bar's clock (clock_due) runs only where a check asks for it (clock_minutes)
                # likewise the selected row's marquee (marq_moved): marquee
                if (a!=payload_syms.get('clock_due') or self.clock_minutes) and (self.marquee or a!=payload_syms.get('marq_moved')): self.timers[ret]=(self.now+c,a,b,c)
        elif name=='widget_add_timer' and self.widget_timers: ret=self.next_timer; self.next_timer+=1; self.timers[ret]=(self.now+c,b,a,c)
        elif name=='timer_remove': self.timers.pop(a,None); ret=0
        elif name=='screen_action': self.screens.append(a); ret=1
        elif name=='player_start': self.started.append((a,b,c,d)); ret=1
        elif name in ('tk_strcmp','strcmp@GLIBC_2.0'): x,y=self.text(a),self.text(b); ret=0 if a and b and x==y else -1 if x<=y else 1
        elif name=='stock_dispatch':
            if self.get(b)==O['EVT_CLICK']:
                self.clicks.append(a)
                if self.on_click: self.on_click(a,b)
            elif self.get(b)==O['EVT_POINTER_UP_BEFORE']-1: self.ups.append(a)
            ret=0
        elif name=='table_client_stop_animator_scroll': self.word(a+O['TABLE_ANIMATOR'],0); ret=0
        elif name=='widget_is_instance_of': ret=1
        elif name=='widget_set_focused': n['focused']=b; ret=0
        elif name=='event_init': self.word(a,b); ret=a
        elif name=='value_set_int': self.word(a,b); ret=a
        elif name=='widget_animator_scroll_create':
            ret=0 if self.slide_fail else self.alloc(0x80)
            if ret:
                self.word(ret+4,a); self.word(ret+O['ANIM_DURATION'],b)
                if self.nodes.get(a,{}).get('type')=='slide_menu': assert c==0 and d==self.slide_easing
        elif name=='widget_animator_on':
            ret=0 if self.slide_on_fail else 1
            if ret: self.slide_callbacks[a]=(c,d)
        elif name=='widget_animator_pause': self.slides.pop(a,None); ret=0
        elif name=='widget_animator_destroy':
            self.slides.pop(a,None); self.slide_callbacks.pop(a,None); ret=0
        elif name=='widget_animator_start':
            if a in self.props and self.nodes[self.get(a+4)].get('animated')=='x':
                origin,goal=self.props[a]
                self.props[a]=(self.now,self.get(a+O['ANIM_DURATION']),self.get(a+4),origin,goal)
            if a in self.slide_callbacks:
                w=self.get(a+4)
                if self.nodes.get(w,{}).get('type')=='slide_menu':
                    assert self.get(a+O['ANIM_ELAPSED'])==self.get(a+O['ANIM_START_TIME'])==0
                    self.slides[a]=(self.now,self.get(a+O['ANIM_DURATION']),w,
                                    signed(self.get(a+0x68)),signed(self.get(a+O['ANIM_X_TO'])))
            ret=0
        elif name=='table_client_set_yoffset':
            assert self.get(a+O['TABLE_ANIMATOR'])==0
            self.word(a+O['TABLE_TOP'],b)
            if self.rebind: self.rebind(a,b)
            ret=0
        elif name=='scroll_view_set_offset':
            assert self.get(a+O['VIEW_ANIMATOR'])==0
            self.word(a+O['SCROLL_X'],b); self.word(a+O['SCROLL_Y'],c)
            ret=0
        elif name=='scroll_view_scroll_to':  # stock's clamps the end to the content (0x603008)
            end=signed(self.get(a+O['VIEW_CONTENT_H']))-signed(self.get(a+O['W_H']))
            self.word(a+O['SCROLL_X'],b); self.word(a+O['SCROLL_Y'],max(0,min(signed(c),end))); ret=0
        elif name=='table_client_scroll_to':
            if self.glide:
                self.word(a+O['TABLE_TOP'],b)
                if self.rebind: self.rebind(a,b)
            else: self.word(a+O['TABLE_ANIMATOR'],0x1234)
            ret=0
        elif name=='canvas_get_clip_rect':
            for j,v in enumerate(self.clip): self.word(b+4*j,v)
            ret=0
        elif name=='canvas_set_clip_rect': self.clip=tuple(signed(self.get(b+4*j)) for j in range(4)); ret=0
        elif name=='canvas_set_fill_color': self.word(self.lcd+O['LCD_FILL_COLOR'],b); ret=0
        elif name=='canvas_set_stroke_color': self.word(self.lcd+O['LCD_STROKE_COLOR'],b); ret=0
        elif name=='canvas_set_global_alpha': self.global_alpha+=1; ret=0
        elif name in ('canvas_fill_rounded_rect','canvas_stroke_rounded_rect'):
            sp=u.reg_read(UC_MIPS_REG_SP)
            # The stock rounded calls set the matching LCD color on their CPU branch; model that
            # stricter side effect so a missing restore fails the state assertions below.
            kind='fill' if name=='canvas_fill_rounded_rect' else 'stroke'
            radius=self.get(sp+16); width=None if kind=='fill' else self.get(sp+20)
            self.rounded.append(dict(kind=kind,rect=tuple(signed(self.get(b+4*j)) for j in range(4)),
                bg=signed(c),color=self.get(d),radius=radius,width=width,clip=self.clip))
            if kind=='stroke' and (self.rounded_fail is True or self.rounded_fail==radius):
                ret=2   # a backend that declines to draw, as the stock no-vgcanvas path does
            else:
                self.word(self.lcd+(O['LCD_FILL_COLOR'] if kind=='fill' else O['LCD_STROKE_COLOR']),self.get(d))
                ret=0
        elif name=='style_get_int': ret=self.style_ints.get((a,self.text(b)),c)
        elif name=='canvas_draw_image': self.image_draws.append(tuple(tuple(signed(self.get(r+4*i)) for i in range(4)) for r in (c,d))); ret=0
        elif name=='widget_load_image':  # list_into is 50x50; image_size None fails the load
            self.image_loads.append(self.text(b)); ret=1 if self.image_size is None or self.text(b) in getattr(self,'image_fail',()) else 0
            if not ret: self.word(c,self.image_size[0]); self.word(c+4,self.image_size[1]); self.u.mem_write(c+8,bytes(16))  # no pixels
        elif name=='canvas_draw_icon': self.icons.append((signed(c),signed(d),self.clip)); ret=0
        elif name=='canvas_set_font': self.font=(self.text(b) if b else 'default',c); ret=0
        elif name=='canvas_set_text_color': self.word(self.lcd+O['LCD_TEXT_COLOR'],b); ret=0
        elif name=='canvas_draw_text_in_rect':
            self.letters.append(dict(text=''.join(chr(self.get(b+4*j)) for j in range(c)),
                rect=tuple(signed(self.get(d+4*j)) for j in range(4)),color=self.get(self.lcd+O['LCD_TEXT_COLOR']),font=self.font,
                align=(self.get(a+O['CANVAS_ALIGN_H']),self.get(a+O['CANVAS_ALIGN_V'])),clip=self.clip)); ret=0
        elif name=='canvas_measure_text': u.reg_write(UC_MIPS_REG_F0,struct.unpack('<I',struct.pack('<f',self.char_px*c))[0]); ret=0
        elif name=='widget_get_canvas': ret=self.canvas
        elif name=='canvas_draw_text':
            self.letters.append(dict(text=''.join(chr(self.get(b+4*j)) for j in range(c)),x=signed(d),
                y=signed(self.get(u.reg_read(UC_MIPS_REG_SP)+16)),color=self.get(self.lcd+O['LCD_TEXT_COLOR']),font=self.font)); ret=0
        elif name=='canvas_fill_rect':
            self.bands.append((signed(b),signed(c),signed(d),signed(self.get(u.reg_read(UC_MIPS_REG_SP)+16)),
                               self.get(self.lcd+O['LCD_FILL_COLOR']),self.clip)); ret=0
        elif name in ('canvas_stroke_rect','lcd_stroke_rect'):
            h=self.get(u.reg_read(UC_MIPS_REG_SP)+16)
            clip=self.clip if name=='canvas_stroke_rect' else (self.get(self.canvas+0x10),self.get(self.canvas+0x14),self.get(self.canvas+0x18)-self.get(self.canvas+0x10)+1,self.get(self.canvas+0x1c)-self.get(self.canvas+0x14)+1)
            self.strokes.append((signed(b),signed(c),signed(d),signed(h),clip,self.get(self.lcd+O['LCD_STROKE_COLOR']))); ret=0
        elif name=='scroll_view_scroll_delta_to':
            n['glide_ms']=d
            if self.glide:
                self.word(a+O['SCROLL_X'],self.get(a+O['SCROLL_X'])+b); self.word(a+O['SCROLL_Y'],self.get(a+O['SCROLL_Y'])+c)
            else: self.word(a+O['VIEW_ANIMATOR'],0x1234)
            ret=0
        elif name=='lcd_get_vgcanvas': ret=self.fake_vg
        elif name.startswith('vg:'):
            self.vg_calls.append((name[3:],signed(a),signed(b)))
            ret=1 if name[3:]=='vgcanvas_save' else 0
        elif name.startswith('float:'):
            x=struct.unpack('<f',struct.pack('<I',u.reg_read(UC_MIPS_REG_F12)))[0]
            fn={'sqrtf@GLIBC_2.0':math.sqrt,'sinf@GLIBC_2.0':math.sin,'acosf@GLIBC_2.0':math.acos}[name[6:]]
            ret=struct.unpack('<I',struct.pack('<f',fn(x)))[0]
            u.reg_write(UC_MIPS_REG_F0,ret)
        elif name in ('alloc:tk_calloc','alloc:tk_free'):
            if name=='alloc:tk_calloc':
                size=a*b; p=self.alloc(size+16); self.allocs[p]=size; ret=p
            else:
                self.allocs.pop(a,None); ret=0
        elif name=='stock_tidal_load': self.word(payload_syms['playback_loads'],self.get(payload_syms['playback_loads'])+1); ret=0
        elif name.startswith('one:'): ret=1
        else: ret=0
        self.returns(ret,clobber=True)
    def nested(self,f,*args):
        """Run guest f(*args) from inside a mock, on a stack below the caller's, and keep the caller's registers."""
        regs=[UC_MIPS_REG_PC,*range(UC_MIPS_REG_0,UC_MIPS_REG_31+1)]; saved=[self.u.reg_read(r) for r in regs]
        for r,v in ((UC_MIPS_REG_SP,self.u.reg_read(UC_MIPS_REG_SP)-0x400),(UC_MIPS_REG_RA,0x1000000),(UC_MIPS_REG_T9,f),*zip(REGS,args)):
            self.u.reg_write(r,v)
        # unicorn's count is one per engine: a nested run's limit stands for the rest of the caller's call too
        self.u.emu_start(f,0x1000000,count=self.count); ret=signed(self.u.reg_read(UC_MIPS_REG_V0))
        for r,v in zip(regs,saved): self.u.reg_write(r,v)
        return ret
    def qsort(self,base,n,size,compare):
        pair=self.alloc(2*size)
        def cmp(x,y): self.u.mem_write(pair,x); self.u.mem_write(pair+size,y); return self.nested(compare,pair,pair+size)
        for i,v in enumerate(sorted([bytes(self.u.mem_read(base+size*i,size)) for i in range(n)],key=cmp_to_key(cmp))): self.u.mem_write(base+size*i,v)
    def returns(self,ret,clobber=False):
        """Return ret from a mocked call; clobber caller-saved registers to catch accidental ABI assumptions."""
        if clobber:  # unicorn 2.1.4's private call: its public reg_write_batch builds the arrays anew, at fifteen writes' cost
            assert not self.u._do_reg_write_batch(SPOILED_IDS,SPOILED_VALUES,len(SPOILED))
        self.u.reg_write(UC_MIPS_REG_V0,ret&0xffffffff)
        self.u.reg_write(UC_MIPS_REG_PC,self.u.reg_read(UC_MIPS_REG_RA))
    def hooked(self,begin=1,end=0):
        """Hook begin..end (everything, as unicorn reads 1..0) with the hook behind FILTER; returns unicorn's handle."""
        hook=self.hook; python=uccallback(self.u,HOOK_CODE_CFUNC)(lambda u,address,size,_: hook(u,address,size,None))
        handle=uc_hook_h()
        assert not uclib.uc_hook_add(self.u._uch,ctypes.byref(handle),UC_HOOK_CODE,FILTER.filter,ctypes.cast(python,ctypes.c_void_p),begin,end)
        self.u._callbacks[handle.value]=python  # kept alive as Uc.hook_add's are, where hook_del looks
        return handle.value
    def fast(self):
        """Hook stock code and the payload's trampolines only, so the payload's own loops (the
        Coverflow renderer) run at native emulator speed."""
        self.u.hook_del(self.code_hook)
        self.hooked(0,BASE-1)
        self.hooked(SCRATCH,0xffffffff)
        for a in list(self.handlers):
            if BASE<=a<SCRATCH: self.u.hook_add(UC_HOOK_CODE,self.hook,begin=a,end=a)
    budget=100000  # instructions per call
    def call(self,key=O['KEY_NEXT'],address=HOOKS['on_wm_keyup_before_fun'][0],args=None,event_type=0x114,gap=1000,stack=(),clear=True,debounce=False,count=None):
        # Independent input steps occur after the stock key debounce timer expires.
        if gap: self.advance(gap,clear=False)
        if not debounce: self.byte(0xa4bbf8,0)  # stock key filter latch
        if clear: self.calls=[]; self.strokes=[]; self.rounded=[]; self.bands=[]; self.icons=[]; self.letters=[]; self.vg_calls=[]
        self.word(self.event+O['EVENT_KEY'],key)
        self.word(self.event+O['EVENT_TYPE'],event_type)
        self.u.reg_write(UC_MIPS_REG_SP,0x7000f000)
        self.u.reg_write(UC_MIPS_REG_RA,0x1000000)
        self.u.reg_write(UC_MIPS_REG_T9,address)
        for i,v in enumerate(stack): self.word(0x7000f010+4*i,v)
        for r,v in zip(REGS,args or (self.wm,self.event,0,0)): self.u.reg_write(r,v&0xffffffff)
        for i,r in enumerate(SAVED): self.u.reg_write(r,0x12340000+i)
        self.count=count or self.budget
        self.u.emu_start(address,0x1000000,count=self.count)
        assert self.u.reg_read(UC_MIPS_REG_PC)==0x1000000, 'Instruction limit reached'
        assert self.u.reg_read(UC_MIPS_REG_SP)==0x7000f000
        assert [self.u.reg_read(r) for r in SAVED]==[0x12340000+i for i in range(len(SAVED))]
        if TRACE:
            v0=self.u.reg_read(UC_MIPS_REG_V0); name=HOOK_NAMES.get(address) or render(self,'p',address)
            px=[] if name!='ringnav_image_add' or args is None else [hashlib.sha1(bytes(self.u.mem_read(self.get(args[2]+0x14),self.get(args[2]+4)*self.get(args[2]+8)))).hexdigest()[:12]]
            trace('ret',name,render(self,'d' if name=='ringnav_style_color' else 'x',v0),*px)
        return signed(self.u.reg_read(UC_MIPS_REG_V0))
    def advance(self,ms,clear=True):
        """Run due UI timers deterministically, including the exact deadline; RET_REPEAT re-arms from the time it
        ran, as AWTK's timer_manager_dispatch sets the timer's start to now. With loop_ms a timer runs on the UI
        loop's next pass at or after its deadline, a pass every loop_ms, as a real loop's idle passes run them."""
        if clear: self.calls=[]
        end=self.now+ms
        while self.timers:
            tid,(due,callback,ctx,period)=min(self.timers.items(),key=lambda item:item[1][0])
            at=-(-due//self.loop_ms)*self.loop_ms if self.loop_ms else due
            if at>end: break
            self.now=at
            del self.timers[tid]
            info=self.alloc(0x58)
            self.word(info+0x20,ctx); self.word(info+0x28,tid); self.word(info+0x40,period)  # timer_info_t's duration
            # its now (timer_info_on_timer, 0x739b70) and start, u64s: when it was added or last ran
            self.u.mem_write(info+0x30,struct.pack('<QQ',at,due-period))
            ret=self.call(address=callback,args=(info,0,0,0),gap=0,clear=False)
            assert ret in (0,7,8) and (ret!=8 or period>0)  # RET_OK or RET_REMOVE ends it; RET_REPEAT re-arms
            if ret==8 and tid not in self.timers: self.timers[tid]=(at+period,callback,ctx,period)
        self.now=end
        for a,values in list(self.props.items()):
            if len(values)!=5: continue
            start,duration,w,origin,goal=values
            elapsed=min(end-start,duration)
            self.word(w+O['W_X'],round(origin+(goal-origin)*elapsed/duration))
            if elapsed==duration: del self.props[a]
        for a,(start,duration,w,origin,goal) in list(self.slides.items()):
            elapsed=min(end-start,duration)
            self.word(w+O['SLIDE_OFFSET'],round(origin+(goal-origin)*elapsed/duration))
            self.word(a+O['ANIM_ELAPSED'],elapsed)
            if elapsed==duration:
                del self.slides[a]
                callback,ctx=self.slide_callbacks.pop(a)
                assert self.call(address=callback,args=(ctx,0,0,0),gap=0,clear=False)==7
    def confirm(self):
        """Single centre release followed by its full confirmation delay."""
        ret=self.call(O['KEY_CENTER'])
        self.advance(O['DOUBLE_CLICK_MS'],clear=False)
        return ret
    def release(self,gap=0):
        """Execute both the hook and the real stock downstream screen-toggle handler."""
        ret=self.call(O['KEY_CENTER'],gap=gap)
        if ret==0:
            self.call(O['KEY_CENTER'],address=syms['on_wm_keyup_fun'],gap=0,clear=False)
        return ret
    def page(self,name='sysset_page',t='scroll_view'):
        # full-bleed (tools/ipod.py full_bleed): the window is the whole screen, its child rests at y 58
        child=self.node(t); self.word(child+O['W_Y'],58)
        self.top=self.node('window',name,[child]); self.word(self.wm+O['W_KEY_TARGET'],self.top)  # opened: its keys' target
        if t=='slide_menu':
            self.word(child+O['SLIDE_INDEX'],0)
            self.nodes[child]['children']=[self.entry(child) for _ in range(7)]
            self.word(child+0x5c,self.alloc()) # stock completion checks the child array
        return child
    def page_list(self,n=10,height=96,extent=960,name='sysset_page'):
        """A page holding one scroll view of n 48px entries; returns (surface, entries)."""
        w=self.page(name); self.word(w+O['W_H'],height); self.word(w+O['VIEW_CONTENT_H'],extent)
        es=[self.entry(w,i*48) for i in range(n)]
        self.nodes[w]['children']=es
        return w,es
    def bind(self,rows,offset=0):
        """Reindex a recycled row pool the way the stock table rebind does."""
        for j,r in enumerate(rows):
            self.word(r+O['ROW_INDEX'],offset//48+j); self.word(r+O['W_Y'],(offset//48+j)*48)
    def table_page(self,n=4,name='allmusic_page',rebind=False):
        """A table_client page of n recycled rows, optionally rebound; returns (surface, rows, entries)."""
        w=self.page(name,'table_client')
        self.word(w+O['ROW_HEIGHT'],48); self.word(w+O['TABLE_ROWS'],20); self.word(w+O['W_H'],96)
        rs=[self.node('table_row') for _ in range(n)]
        es=[self.entry(r) for r in rs]; self.nodes[w]['children']=rs
        for r,e in zip(rs,es):
            self.nodes[r]['children']=[e]; self.word(r+O['W_PARENT'],w)
        self.bind(rs)
        if rebind: self.rebind=lambda a,offset: self.bind(rs,offset)
        return w,rs,es
    def list_surface(self,virtual,n=20,rebind=True):
        """A plain list of n 48px rows or a four-row virtual table; optionally rebound."""
        if not virtual:
            w,es=self.page_list(n,extent=n*48)
            return w,None
        w,rs,es=self.table_page(rebind=rebind)
        return w,rs
    def moved(self): return [x for x in self.calls if x[0] in ('scroll_view_set_offset','table_client_set_yoffset','scroll_view_scroll_delta_to','table_client_scroll_to','slide_menu_scroll_to_next','slide_menu_scroll_to_prev','widget_animator_scroll_set_params')]
    def dispatched(self): return [x for x in self.calls if x[0]=='stock_dispatch']

checks=0
def passed():
    global checks
    checks+=1
    trace(f'== {checks}')
def theme_at(m): return m.call(address=payload_syms['theme'],args=(0,0,0,0),gap=0)&0xffffffff
def set_motion(m,kind,ms,easing):  # the active theme's motion row for a widget kind (motion_t, THEME_MOTION_OFF)
    m.u.mem_write(theme_at(m)+THEME_MOTION_OFF+8*(MOTION_WINDOW+MOTION_WIDGET).index(kind)+4,struct.pack('<HB',ms,easing))
def set_hint(m,kind,hint): m.word(theme_at(m)+THEME_MOTION_OFF+8*MOTION_WINDOW.index(kind),m.string(hint))  # a window kind's hint, as a theme with one has

# The tk_snprintf mock formats %c from its int argument (np_bar.c builds the heart's image name so).
t=Machine(); t.mock('tk_snprintf'); buf=t.alloc(32)
t.call(address=syms['tk_snprintf'],args=(buf,32,t.string('%sunfav_%c'),t.string('oled_np_')),stack=(ord('d'),),gap=0)
assert t.text(buf)=='oled_np_unfav_d',t.text(buf); passed()
# Q2_TRACE: draw calls and hook answers by value: widgets as mock node ids, strings by content, payload
# addresses by symbol (else P), stack addresses as S; cmp of two builds' traces survives a layout change.
saved,TRACE=TRACE,io.StringIO()
t=Machine(); w=t.node('label'); rect=t.alloc(16); text=t.alloc(8); paint=payload_syms['ringnav_paint']
for o,v in enumerate((1,2,-3,4)): t.word(rect+4*o,v)
t.word(text,ord('O')); t.word(text+4,ord('K'))
assert [render(t,c,v) for c,v in (('n',w),('n',0x1234),('s',t.string('Songs')),('s',0),('i',0xffffffe2),('x',0xff112233),
        ('r',rect),('r',0),('d',rect),('f',0x3fc00000),('p',paint),('i',paint),('x',SCRATCH+0x1ffff),('x',0x7000eff0),
        ('x',BASE-4),('x',SCRATCH+0x20000))]==['n0','00001234','"Songs"','null','-30','ff112233','1,2,-3,4','null','00000001',
        '1.5','ringnav_paint','ringnav_paint','P','S','%08x'%(BASE-4),'%08x'%(SCRATCH+0x20000)]
for r,v in zip((*REGS,UC_MIPS_REG_SP,UC_MIPS_REG_T9),(t.canvas,text,2,10,0x7000f000,syms['canvas_draw_text'])): t.u.reg_write(r,v)
t.word(0x7000f010,-20); t.hook(t.u,syms['canvas_draw_text'],4,None)
t.call(address=payload_syms['clicker_text'],args=(0,0,0,0),gap=0)  # "Clicker: Both", a literal: P
assert TRACE.getvalue().splitlines()==['canvas_draw_text\t"OK"\t10\t-20','ret\tclicker_text\tP']
COVER=r'(vg:vgcanvas_|canvas_|widget_set_prop|widget_move_resize$|widget_resize$|widget_set_opacity$|widget_use_style$|image_)'
assert {n for n in t.handlers.values() if re.match(COVER,n) and n.removeprefix('vg:') in FUNCTIONS}|{'canvas_set_global_alpha'}<=TRACE_ARGS.keys()
n=checks; TRACE.seek(0); TRACE.truncate(); passed(); assert TRACE.getvalue()==f'== {n+1}\n'
TRACE=saved

# The failed-exec path exits through the kernel, without libc's copied buffers or handlers.
m=Machine(); exited=[]
def child_syscall(u,number,unused):
    exited.append((u.reg_read(UC_MIPS_REG_V0),u.reg_read(UC_MIPS_REG_A0)))
    u.reg_write(UC_MIPS_REG_PC,0x1000000)
m.u.hook_add(UC_HOOK_INTR,child_syscall)
m.call(address=payload_syms['child_exit'],args=(127,0,0,0),gap=0)
assert exited==[(4001,127)];passed()

# Speed's four 42x61 image buttons: the wheel walks them and Centre clicks the last one (#135). They are picture
# tiles, which OLED leaves unpainted (paint_selection).
m=Machine(); w=m.page('playermore_page'); row=m.node('button',style='s_btn_forwardspeed')
m.word(row+O['W_PARENT'],w); m.nodes[w]['children']=[row]
buttons=[]
for x in (120,177,231,283):
    e=m.entry(row,9); m.nodes[e]['type']='image'
    m.word(e+O['W_X'],x); m.word(e+O['W_W'],42); m.word(e+O['W_H'],61); buttons.append(e)
m.nodes[row]['children']=buttons; m.word(w+O['W_W'],375)
m.paint(w)
for i in range(4):
    if i: assert m.call()==11
    m.paint(w)
    assert m.selected(w)==i and not m.drawn()
assert m.confirm()==11 and m.dispatched()[0][1]==buttons[-1]; passed()

# Execute the stock offset setter after cancelling an animator headed to either boundary.
# Cover selected rows and the pixel-scroll fallback.
for populated in (False,True):
    for endpoint,key,selected,want in ((0,O['KEY_NEXT'],4,192),
                                      (864,O['KEY_PREV'],5,192)):  # OLED's whole rows: either way the offset lands on a whole row
        m=Machine(); w,es=m.page_list(20 if populated else 0)
        m.word(w+0x74,syms['g_scroll_view_vtable'])
        m.paint(w)
        m.nodes[w]['_ringnav_index']=selected if populated else -1
        top=120 if endpoint==0 else 240
        m.word(w+O['SCROLL_Y'],top)
        animator=m.alloc(0x80); m.word(animator+0x64,endpoint)
        m.word(w+O['VIEW_ANIMATOR'],animator)
        del m.handlers[syms['scroll_view_set_offset']]
        m.mock('widget_animator_scroll_create','widget_animator_on',
               'widget_animator_start','widget_dispatch_simple_event')
        assert m.call(key)==11
        active=m.get(w+O['VIEW_ANIMATOR'])
        expected=want if populated else top+(48 if endpoint==0 else -48)
        assert active==0 and m.get(w+O['SCROLL_Y'])==expected
        assert any(c[0]=='widget_animator_destroy' and c[1]==animator for c in m.calls)
        passed()

for t,off in [('scroll_view',O['SCROLL_Y']),('table_client',O['TABLE_TOP'])]:
    m=Machine(); w=m.page(t=t)
    for _ in range(20): assert m.call()==11
    assert m.get(w+off)==min(20*manifest['ring_step_pixels'],736 if t=='scroll_view' else 4560)  # OLED's list end, the pad past it scrolling too
    assert m.call(O['KEY_PREV'])==11 and m.moved()
    for _ in range(120): m.call(O['KEY_PREV'])
    assert m.get(w+off)==0
    # Empty/short lists consume input without turning into volume changes.
    m.word(w+(O['VIEW_CONTENT_H'] if t=='scroll_view' else O['TABLE_ROWS']),0)
    assert m.call()==11 and m.get(w+off)==0
    passed()

for name in ['playing_page','volume_dialog','saverscreen_page','usbmode_page','unknown_page']:
    m=Machine(); m.page(name); assert m.call()==0 and not m.moved(); passed()
# A volume dialog in front that is not yet the window manager's key target (its open event comes on the pass's idle): the
# tick would pass it and open another dialog over it (#303). The filter steps the volume and the dialog's slider and stops
# the key, at the limit too. Dark it moves nothing and goes on, to on_wm_keyup_fun's hook, which drops it
# (volume.c vol_wm_keyup).
for lit in (1,0):
    m=Machine(); m.mock('device_set_volume','write_int_config'); slider=m.node('slider','slider_vol',value=30)
    m.top=m.node('dialog','volume_dialog',[slider])
    for name,v in (('g_backlight_status',lit),('g_volume',30),('g_maxvolume',31)): m.byte(syms[name],v)
    if not lit:
        assert m.call(O['KEY_NEXT'])==0 and m.u.mem_read(syms['g_volume'],1)[0]==30 and m.nodes[slider]['value']==30
        assert not any(c[0]=='device_set_volume' for c in m.calls); passed(); continue
    assert [m.call(O['KEY_NEXT']) for _ in range(3)]==[11]*3 and m.u.mem_read(syms['g_volume'],1)[0]==31 and m.nodes[slider]['value']==31
    assert m.call(O['KEY_PREV'])==11 and m.u.mem_read(syms['g_volume'],1)[0]==30 and m.nodes[slider]['value']==30; passed()
GATES=[('g_backlight_status',0),('g_lockscreen_pageflag',1),('g_testmode_flag',1),
       ('g_guideflag',1),('g_poweroff_state',2),('g_usblink_status',2)]
for flag,value in GATES:
    m=Machine(); m.page(); m.byte(syms[flag],value); assert m.call()==0 and not m.moved(); passed()
# The Bluetooth receiver's gear opens Audio settings: its wheel navigates, while the player
# and its volume dialog still pass wheel input to stock volume handling.
for flag in (1,2):
    m=Machine(); m.page('audiosetting_page'); m.byte(syms['bt__recv_pageflag'],flag)
    assert m.call()==11 and m.moved()
    assert m.call(O['KEY_PREV'])==11 and m.moved(); passed()
    for name in ('bluetoothrecv_page','volume_dialog'):
        m=Machine(); m.page(name); m.byte(syms['bt__recv_pageflag'],flag)
        assert m.call()==0 and not m.moved(); passed()
for field in ['animating','pressed']:
    m=Machine(); m.page(); setattr(m,field,1); assert m.call()==11 and not m.moved(); passed()

m=Machine(); w=m.page('home_page','slide_menu')
assert m.call()==11 and m.get(m.get(w+O['SLIDE_ANIMATOR'])+O['ANIM_X_TO'])==(-240&0xffffffff)
assert m.call(O['KEY_PREV'])==11 and len(m.slides)==1; passed()
m=Machine(); hidden=m.node(visible=0); shown=m.node(); pages=m.node('pages',children=[hidden,shown],active=1)
m.top=m.node('window','artistinfo_page',[pages]); assert m.call()==11 and m.moved()[0][1]==shown; passed()
m.nodes[hidden]['visible']=1
assert m.call()==11 and m.moved()[0][1]==shown; passed()
m.top=m.node('window','sysset_page',[hidden,shown]); assert m.call()==11 and m.moved()[-1][1]==hidden; passed()
# Run the stock setters so incorrect shared offsets cannot make the mocks agree with a bug.
for setter,field in (('scroll_view_set_xslidable','VIEW_HORIZONTAL'),
                     ('scroll_view_set_yslidable','VIEW_VERTICAL'),
                     ('scroll_view_set_snap_to_page','VIEW_SNAP')):
    m=Machine(); vert=m.node(); excluded=m.node()
    m.word(excluded+0x74,syms['g_scroll_view_vtable'])
    value=0 if field=='VIEW_VERTICAL' else 1
    assert m.call(address=syms[setter],args=(excluded,value,0,0))==0
    assert m.u.mem_read(excluded+O[field],1)==bytes([value])
    m.top=m.node('window','sysset_page',[excluded,vert])
    assert m.call()==11 and m.moved()[0][1]==vert
    passed()
# Two navigable panes: only the pane that already holds the selection is used.
m=Machine(); a,b,_,_=m.panes(96)
m.nodes[a]['_ringnav_index']=0
assert m.call()==11 and m.call()==11
assert m.moved()[-1][1]==a
m.nodes[b]['_ringnav_index']=0
assert m.call()==11 and not m.moved(); passed()
for attribute in ['visible','enable']:
    m=Machine(); w=m.page(); m.nodes[w][attribute]=0; assert m.call()==11 and not m.moved(); passed()
# Wheel scrolling sets the offset immediately from the current viewport.
m=Machine(); w=m.page(); m.word(w+O['SCROLL_Y'],100)
assert m.call()==11 and m.moved()[0][0]=='scroll_view_set_offset' and m.moved()[0][3]==148
assert m.get(w+O['SCROLL_Y'])==148 and m.get(w+O['VIEW_ANIMATOR'])==0; passed()

# Painting establishes selection without a sacrificial button press or native focus.
m=Machine(); m.config.update(FLAT); w,entries=m.page_list(5,extent=1000)
assert m.paint(w)==0 and m.selected(w)==0
# The bar is painted behind the rows: every band precedes the border hook, which draws nothing.
names=[c[0] for c in m.calls]
bg,border=names.index('stock_paint_bg'),names.index('stock_paint')
assert all(bg<i<border for i,n in enumerate(names) if n=='canvas_fill_rect')
assert not m.rounded and not m.strokes and m.global_alpha==0
# OLED's bar (paint_selection, gradient): the accent's one tone, its list.selection.inset in from each side of the
# surface, its ends list.selection.radius (under the row's half height, pill_radius), in one-pixel bands; each end's
# corner pixels the band's colour at their coverage (corner_cover.c, tools/art.py corner_cover) as alpha.
OLED_T=theme_builtins()[0]  # assets/themes/oled/theme.xml, the one theme
OLED_G=built_geometry(theme_builtins()); assert OLED_G==OLED_T['geom']  # its grid, baked into the build
OLED_SCHEME=OLED_T['schemes'][next(o['def'] for o in OLED_T['opts'] if o['setting']=='SETTING_ACCENT')]  # Accent's default, oled-blue
OLED_TONES=OLED_SCHEME['tones']
assert len(set(OLED_TONES))==1  # Accent Blue, one value for every tone: the bar is solid
OLED_BLUE,inset,radius=color_t(OLED_TONES[0]),OLED_T['sel_inset'],OLED_T['sel_radius']
def corner_mask_bands(x,y,w,h,R,color):
    """corner_mask's fills (nav/row_paint.c) rounding the box x, y, w, h by R into color (a color_t, opaque): each
    corner row's clear pixels in one fill, then each part pixel at the alpha the arc leaves; corners in the order top
    left, bottom left, top right, bottom right."""
    from art import corner_cover
    cover,want=corner_cover(R),[]
    for i in range(R):
        row=cover[i*R:(i+1)*R]; n=next((k for k,c in enumerate(row) if c),R)
        for c in range(4):
            yy=y+(h-1-i if c&1 else i); right=c&2
            if n: want.append((x+w-n if right else x,yy,n,1,color))
            want+=[(x+w-1-k if right else x+k,yy,1,1,(color&0xffffff)|(255-row[k])<<24) for k in range(n,R) if row[k]<255]
    return want
def bar_bands(x,y,w,h,radius,color=OLED_BLUE):
    from art import corner_cover
    cover=corner_cover(radius) if radius else b''
    want=[]
    for j in range(h):
        cr=j if j<radius else h-1-j if j>=h-radius else -1
        row=cover[cr*radius:(cr+1)*radius] if cr>=0 else b''
        n=next((k for k,c in enumerate(row) if c==255),len(row))
        want.append((x+n,y+j,w-2*n,1,color))
        want.extend(e for k in range(n) if row[k] for e in ((x+k,y+j,1,1,(color&0xffffff)|row[k]<<24),(x+w-1-k,y+j,1,1,(color&0xffffff)|row[k]<<24)))
    return want
assert [b[:5] for b in m.bands]==bar_bands(inset,0,240-2*inset,48,radius) and radius<48//2
assert all(b[5]==(0,0,240,96) for b in m.bands)
# Coverage and geometry mirror at both ends and across the horizontal centre.
rows=[[b[:4:2] for b in m.bands if b[1]==j] for j in range(48)]
assert all(rows[j]==rows[47-j] for j in range(48)) and all(2*r[0][0]+r[0][1]==240 and (len(r)==1 or r[1][0]+r[2][0]==239) for r in rows)
assert m.clip==(0,0,240,240) and m.lcd_colors()==LCD_COLORS
assert not any(m.get(e+O['W_FOCUS'])&0x80 for e in entries); passed()
# A left image the round end would cut shrinks the radius (pill_radius); hidden (Folder's multi-select check) it doesn't.
for visible,want in ((1,2),(0,radius)):
    k=Machine(); k.config.update(FLAT); kw,es=k.page_list(5,extent=1000)
    check=k.node('image','img_choice',visible=visible); k.nodes[es[0]]['children']=[check]
    for o,v in zip(('W_X','W_Y','W_W','W_H'),(inset,0,50,48)): k.word(check+O[o],v)
    k.paint(kw); assert [b[:5] for b in k.bands]==bar_bands(inset,0,240-2*inset,48,want),visible
passed()
# Cards stay two pixels inside all three columns, including scrolled first/last rows: a grid tile's card, square,
# in the accent (paint_selection, TILE_INSET).
for offset in (0, 24):
    for selected in range(6):
        g=Machine(); gw=g.page('album_page'); tiles=[g.entry(gw,(i//3)*48) for i in range(6)]
        g.nodes[gw]['children']=tiles
        for i,t in enumerate(tiles): g.word(t+O['W_X'],80*(i%3)); g.word(t+O['W_W'],80)
        g.word(gw+O['W_H'],96); g.word(gw+O['SCROLL_Y'],offset)
        g.paint(gw)
        g.nodes[gw]['_ringnav_index']=selected
        g.paint(gw)
        x,y,width,height=g.rounded[-1]['rect']
        assert g.selected(gw)==selected, (offset,selected,g.selected(gw),g.rounded)
        assert (x,width,height)==(80*(selected%3)+O['TILE_INSET'],80-2*O['TILE_INSET'],48-2*O['TILE_INSET'])
        assert y==48*(selected//3)-g.get(gw+O['SCROLL_Y'])+O['TILE_INSET']
        assert g.rounded[-1]['radius']==0
        assert g.rounded[-1]['color']==OLED_BLUE; passed()
# Touch mode draws nothing; the wheel brings the bar back.
c=Machine(); c.config.update(FLAT); cw,_=c.page_list(5,extent=1000); c.paint(cw)
c.touch(); c.paint(cw); assert not c.drawn() and c.clip==(0,0,240,240)
c.call(); c.paint(cw); assert c.selected(cw)==1 and c.sel()==(inset,48,240-2*inset,48); passed()  # OLED's lists keep no reading margin (reveal)
# Status bar: an overlay on full-bleed pages, no ground of its own and no highlight, LCD fill restored.
s=Machine(); s.clock_minutes=True; title=s.node('hscroll_label','label_clock')
def top_level(t,name,children=()):
    w=s.node(t,name,children); s.word(w+O['W_PARENT'],s.wm); return w
bar=top_level('system_bar','system_bar',[title]); s.word(bar+O['W_W'],375); s.word(bar+O['W_H'],30)
s.word(syms['system_bar'],bar)
def bg(w): return s.call(address=HOOKS['widget_on_paint_background'][0],args=(w,s.canvas,0,0),gap=0)
assert bg(bar)==0 and not s.bands
assert s.lcd_colors()==LCD_COLORS; passed()
# A pressed grid tile (the album grid's three 110 px buttons in a table_row) greys its selection
# card inside the tile, square (pressed_pill); the row draws nothing, the tile no stock box.
t=Machine()
tiles=[t.node('button','button1',style='s_btn_listitem') for _ in range(3)]
row=t.node('table_row','table_row',tiles,style='s_tablerow_black'); t.word(row+O['W_W'],365); t.word(row+O['W_H'],160)
for x,b in zip((14,132,250),tiles):
    t.word(b+O['W_PARENT'],row); t.word(b+O['W_X'],x); t.word(b+O['W_W'],110); t.word(b+O['W_H'],160)
def stock(w):  # paints w's background; whether stock's box was drawn
    t.call(address=HOOKS['widget_on_paint_background'][0],args=(w,t.canvas,0,0),gap=0)
    return any(c[0]=='stock_paint_bg' for c in t.calls)
card=(O['TILE_INSET'],)*2+(110-2*O['TILE_INSET'],160-2*O['TILE_INSET'])
for b in tiles:
    t.nodes[b]['state_for_style']='pressed'
    assert stock(row) and not t.rounded
    assert not stock(b) and [(r['rect'],r['radius'],r['color']) for r in t.rounded]==[(card,0,O['PH_GREY'])]
    t.nodes[b]['state_for_style']=''
passed()
# The clock: local time as 12-hour h:mm AM/PM, written only when the minute shown changes, from a
# timer due at the next minute (clock_show). A paint reads no time; the bar's first paint and a new
# 24-hour setting run the clock on the next pass. The mock's localtime is always at :59, so the
# next minute is a second away.
def clock(): return s.nodes[title].get('text')
def writes(): return [c for c in s.calls if c[0]=='widget_set_text_utf8']
def reads(): return [c for c in s.calls if c[0] in ('time@GLIBC_2.0','localtime_r')]
def minute(ms=1000): s.advance(ms)
s.advance(0); assert clock()=='6:14 PM'; passed()  # the gradient check's paint above started it
for h,mi,want in ((0,0,'12:00 AM'),(0,5,'12:05 AM'),(9,7,'9:07 AM'),(11,59,'11:59 AM'),(12,0,'12:00 PM'),
                  (12,59,'12:59 PM'),(13,0,'1:00 PM'),(23,59,'11:59 PM')):
    s.clock=(h,mi); minute(); assert clock()==want,(h,mi,clock())
passed()
# Repaints read no time and leave the label alone; the next minute rewrites it.
bg(bar); assert not reads() and not writes(); passed()
s.clock=(0,0); minute(); assert clock()=='12:00 AM' and len(writes())==1; passed()
# Stock's 24-hour toggle updates on the pass after the next paint, including leading zeroes.
s.u.mem_write(syms['g_time24h_flag'], b'\x01')
bg(bar); s.advance(0); assert clock()=='00:00' and len(writes())==1
bg(bar); s.advance(0); assert not writes()
for h,mi,want in ((0,5,'00:05'),(9,7,'09:07'),(12,0,'12:00'),(18,14,'18:14'),(23,59,'23:59')):
    s.clock=(h,mi); minute(); assert clock()==want,(h,mi,clock())
s.u.mem_write(syms['g_time24h_flag'], b'\x00')
bg(bar); s.advance(0); assert clock()=='11:59 PM' and len(writes())==1
bg(bar); s.advance(0); assert not writes(); passed()
# A failed time or localtime shows --:-- once and tries again a minute later.
for fail in ('time','localtime'):
    s.clock=fail; minute(); assert clock()=='--:--' and len(writes())==1
    minute(); assert not writes()
    s.clock=(7,30); minute(60000); assert clock()=='7:30 AM'
passed()
# Past 2038 time() wraps; read unsigned and taken 28 years back, localtime keeps the 32-bit range.
s.wall=(86400+3*883612800,86400+2*883612800); s.clock=(7,31); minute(); assert clock()=='7:31 AM'
s.wall=(86400,86400); s.clock=(7,30); minute(); assert clock()=='7:30 AM'; passed()
# Pages, a dialog on top and pages with their own navbar all keep the clock; their paints read no
# time and write nothing.
heading=s.node('hscroll_label',text='System Setting')
nav=s.node('view','view_navbar',[s.node('image','img_return'),heading],visible=0)
pages=[top_level('window',n,[nav] if n=='sysset_page' else []) for n in
       ('sysset_page','home_page','playing_page','coverflow_page','tidal_main_page')]+[top_level('dialog','sortselect_dialog')]
for pg in pages:
    s.top=pg; bg(pg)
    assert clock()=='7:30 AM' and not writes() and not reads()
passed()
s.clock=(19,31); s.top=pages[-1]; minute(); assert clock()=='7:31 PM' and len(writes())==1; passed()
# The codec badge and the Battery setting (bar_sync, on each bar paint): stock sets img_bt's image
# each second; a new badge shows CODEC_MS, fades out and BT_GLYPH fades in over CODEC_STEPS steps
# each way, and BT_GLYPH then replaces the badge stock sets again. The battery shows the icon or the
# percentage in view_battery, and the icon while the group's ink would squeeze the title out (navigation.c batt_room).
# The percentage is sized to its digits once the first paint measured them (10 px each here, 11 rounded up).
def battery_checks():
    def battery_bar(mode,**config):
        b=Machine(); b.config={'BATTERY':str(mode),**config}
        level=b.node('progress_bar','progress_battery',value=50)
        w={n:b.node(t,n,list(c),visible=v,**kw) for n,t,c,v,kw in (
            ('img_bt','image',(),1,dict(image='bar_ldac')),('img_wifi','image',(),1,{}),
            ('label_battery','label',(),0,{}),('view_battery','view',(),0,{}),
            ('img_battery','image',(level,),1,dict(image='bar_battery')))}
        b.word(w['img_wifi']+O['W_W'],14); b.word(w['view_battery']+O['W_H'],30)
        row=b.node('view','view_right',list(w.values())); b.word(row+O['W_H'],30); b.word(w['view_battery']+O['W_PARENT'],row)
        bar=b.node('system_bar','system_bar',[row])
        w['progress_battery']=level
        b.word(bar+O['W_PARENT'],b.wm); b.word(syms['system_bar'],bar)
        def paint(x=bar):  # a frame, then the bar_repaint it may arm (timer 0: right after the frame)
            b.call(address=HOOKS['widget_on_paint_background'][0],args=(x,b.canvas,0,0),gap=0)
            b.advance(0,clear=False)
        return b,bar,w,paint
    def shown(b,w): return [n for n in ('img_battery','label_battery','view_battery') if b.nodes[w[n]]['visible']]
    def alpha(b,w): return b.get(w['img_bt']+0x34)&255
    b,bar,w,paint=battery_bar(1); paint()
    # LDAC's badge (36px of ink) and Wi-Fi leave no room for the percentage: the icon, until it fades.
    assert b.nodes[w['img_bt']]['image']=='bar_ldac' and alpha(b,w)==255 and shown(b,w)==['view_battery'] and len(b.timers)==1
    b.advance(O['CODEC_MS']-1); paint(); assert b.nodes[w['img_bt']]['image']=='bar_ldac' and alpha(b,w)==255
    steps=[]
    for _ in range(2*O['CODEC_STEPS']):
        b.advance(O['CODEC_STEP_MS'] if steps else 1); steps.append((alpha(b,w),b.nodes[w['img_bt']]['image']))
    n=O['CODEC_STEPS']
    assert steps==[(255*abs(n-k)//n,'bar_ldac' if k<n else O_GLYPH) for k in range(1,2*n+1)] and not b.timers, steps
    paint(); assert shown(b,w)==['view_battery'] and b.get(w['view_battery']+O['W_W'])==O['BATT_PCT_W']; passed()
    # Stock's next tick sets the badge again: the glyph goes straight back, with no new fade.
    b.nodes[w['img_bt']]['image']='bar_ldac'; paint()
    assert b.nodes[w['img_bt']]['image']==O_GLYPH and not b.timers and shown(b,w)==['view_battery']; passed()
    # A new codec flashes again; AAC's narrow badge leaves room, so the percentage stays.
    b.nodes[w['img_bt']]['image']='bar_aac'; paint()
    assert b.nodes[w['img_bt']]['image']=='bar_aac' and len(b.timers)==1 and shown(b,w)==['view_battery']; passed()
    # Bluetooth off ends the flash; a reconnect with the same codec flashes again.
    b.nodes[w['img_bt']]['visible']=0; paint(); assert not b.timers and alpha(b,w)==255
    b.nodes[w['img_bt']]['visible']=1; paint(); assert len(b.timers)==1; passed()
    # Percent: the digits alone, in a slot their width, the charge colour, after the first paint; canvas state restored.
    b,bar,w,paint=battery_bar(1); b.nodes[w['img_bt']]['visible']=0
    lcd=lambda: (b.lcd_colors(),b.get(b.lcd+O['LCD_TEXT_COLOR']),b.get(b.canvas+O['CANVAS_ALIGN_V']),b.get(b.canvas+O['CANVAS_ALIGN_H']))
    def slot(): b.bands=[]; b.letters=[]; paint(w['view_battery']); assert lcd()==before; return b.bands,b.letters
    for image,value,rgb in (('bar_battery',100,0xffffff),('bar_battery',88,0xffffff),('bar_charge',7,O['BATT_CHARGE_RGB']),('bar_lowcharge',5,O['BATT_LOW_RGB'])):
        b.nodes[w['img_battery']]['image']=image; b.nodes[w['label_battery']]['text']=f'{value}%'
        paint(); paint(w['view_battery']); paint(); before=lcd(); bands,letters=slot()
        pct=11*len(str(value))+11  # each 10 px character measured up to a whole pixel and one more
        assert b.get(w['view_battery']+O['W_W'])==pct and not bands
        # one line, never a wrapping rect: centred across the slot, the line's top px/2 above the 30 px row's centre
        assert [(l['text'],l['x'],l['y'],l['color']) for l in letters]==[
            (f'{value}%',(pct-10*len(f'{value}%'))//2,(30+1-letters[0]['font'][1])//2,color_t(rgb))]
    passed()
    # A codec badge (aptX HD's) is off OLED's row, which has the room: it never takes the percentage's place, and the
    # badge's fade changes nothing of the slot.
    b,bar,w,paint=battery_bar(1); b.nodes[w['label_battery']]['text']='100%'
    b.nodes[w['img_bt']]['image']='bar_aptxhd'
    paint(); assert b.get(w['view_battery']+O['W_W'])==O['BATT_PCT_W']
    b.advance(O['CODEC_MS'])
    for _ in range(O['CODEC_STEPS']): b.advance(O['CODEC_STEP_MS'])
    b.calls=[]; paint()
    assert b.get(w['view_battery']+O['W_W'])==O['BATT_PCT_W']
    # OLED offers Icon and Percent (theme.xml battery values): a stored 2 (Icon + Percent) or 3 (Percent Inside) reads
    # as its default, the icon, as an invalid value does; the body the set's (geom batt_w) with its nub outside.
    for invalid in ('2','3','4','9','-1','33','bad',''):
        b,bar,w,paint=battery_bar(invalid); paint()
        assert b.get(w['view_battery']+O['W_W'])==OLED_G.batt_w+O['BATT_NUB_W'],invalid
    for mode in range(2):
        for bt in (0,1):
            for wifi in (0,1):
                b,bar,w,paint=battery_bar(mode)
                b.nodes[w['img_bt']].update(visible=bt,image=O_GLYPH)
                b.nodes[w['img_wifi']]['visible']=wifi
                b.nodes[w['label_battery']]['text']='100%'
                paint()
                assert shown(b,w)==['view_battery']
                assert b.get(w['view_battery']+O['W_W'])==(O['BATT_PCT_W'] if mode==1 else OLED_G.batt_w+O['BATT_NUB_W'])
    passed()
    # The slot takes the set's bar height (geom_vals[V_BAR], OLED's 58): 26 poked there sizes it 26, then 58 again.
    b,bar,w,paint=battery_bar(1); assert b.u.mem_read(payload_syms['geom_vals']+2*GEOM_VALS.index('bar'),2)==struct.pack('<h',OLED_G.bar)
    for bar_h in (26,OLED_G.bar):
        b.u.mem_write(payload_syms['geom_vals']+2*GEOM_VALS.index('bar'),struct.pack('<h',bar_h)); b.word(w['view_battery']+O['W_H'],0); paint()
        assert b.get(w['view_battery']+O['W_H'])==bar_h
    passed()
battery_checks()
assert m.confirm()==11 and m.dispatched()[0][1]==entries[0]; passed()
assert m.call()==11 and m.selected(w)==1 and m.get(w+O['SCROLL_Y'])==0  # OLED's set: whole rows, no reading margin, the list ending at list_end
assert m.call()==11 and m.selected(w)==2 and m.get(w+O['SCROLL_Y'])==48
assert m.confirm()==11 and m.dispatched()[0][1]==entries[2]; passed()
# The separate Play/Pause key remains native even with an active selection.
assert m.call(O['KEY_PLAY'])==0 and not m.dispatched(); passed()
# Touching a different row selects it before the native callback runs; no extra click.
assert m.touch()==0
m.on_click=lambda a,b: (None if m.selected(w)==1 else (_ for _ in ()).throw(AssertionError('late selection')))
assert m.click(entries[1])==0 and len(m.dispatched())==1 and m.selected(w)==1
m.on_click=None
assert m.confirm()==11 and m.dispatched()[0][1]==entries[1]; passed()
# Native touch focus may move anywhere without altering the logical selection.
m.word(entries[4]+O['W_FOCUS'],0x80)
assert m.confirm()==11 and m.dispatched()[0][1]==entries[1]; passed()
# Swipe preserves selection during momentum; settle adopts the visible row nearest the centre.
m.touch(); m.word(w+O['SCROLL_Y'],110); m.word(w+O['VIEW_ANIMATOR'],0x1234)
m.paint(w); assert m.selected(w)==1
m.word(w+O['VIEW_ANIMATOR'],0); m.paint(w); assert m.selected(w)==3
assert m.confirm()==11 and m.dispatched()[0][1]==entries[3]; passed()
# Wheel interrupts touch momentum, and centre while a finger is down is consumed without a click.
m.touch(); m.word(w+O['VIEW_ANIMATOR'],0x1234)
assert m.call(O['KEY_PREV'])==11 and m.get(w+O['VIEW_ANIMATOR'])==0 and m.selected(w)==2
m.pressed=1
assert m.confirm()==11 and not m.dispatched()
assert m.call()==11 and m.selected(w)==2
m.pressed=0; passed()
# A press with no pointer event for STALE_TOUCH_MS is healed once per pass of ringnav_sleep: an abort to the
# top window's targets, so a row can't click, then the release stock never got, at the touch's last place. Only
# events that reach the window manager count, pointer events only: a key is no finger, and a move is. A --dev
# build also notes it in q2-wheel.log; a release build opens no file.
m.handlers[payload_syms['stock_input_trampoline']]='stock_input'
m.handlers[HOOKS['main_loop_sleep_default'][0]+12]='stock_sleep'
m.mock('widget_dispatch_event_to_target_recursive')
def wm_event(kind): return m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),event_type=kind,gap=0)
def heal():
    m.calls=[]; m.call(address=HOOKS['main_loop_sleep_default'][0],args=(0x1234,0,0,0),gap=0)
    return [c for c in m.calls if c[0] in ('widget_dispatch_event_to_target_recursive','stock_input')]
def opens(): return [c for c in m.calls if c[0]=='fopen@GLIBC_2.2' and m.text(c[1])=='/mnt/data/q2-wheel.log']
m.word(m.event+O['EVENT_Y'],44); m.call(33,address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),event_type=O['EVT_POINTER_FIRST'],gap=0); m.pressed=1  # the event's x is its key's word
assert not heal(); m.now+=O['STALE_TOUCH_MS']-1; assert not heal() and not opens()
m.now+=1; steps=heal()
assert [c[0] for c in steps]==['widget_dispatch_event_to_target_recursive','stock_input']
assert steps[0][1]==m.top and m.get(steps[0][2])==O['EVT_POINTER_ABORT']
assert steps[1][1]==m.wm and m.get(steps[1][2])==O['EVT_POINTER_UP_BEFORE']-1 and (m.get(steps[1][2]+O['EVENT_X']),m.get(steps[1][2]+O['EVENT_Y']))==(33,44)
assert len(opens())==(1 if manifest['dev'] else 0)
if manifest['dev']: assert m.text(opens()[0][2])=='ab'
assert not heal()  # still pressed (stock's flag not what it reads): one try per STALE_TOUCH_MS, not every pass
m.now+=O['STALE_TOUCH_MS']; assert len(heal())==2
m.pressed=0; m.now+=O['STALE_TOUCH_MS']; assert not heal()  # nothing pressed: nothing to do
assert m.call()==11 and m.selected(w)==3; m.paint(w); assert m.drawn()
wm_event(O['EVT_POINTER_FIRST']); m.pressed=1; m.now+=O['STALE_TOUCH_MS']-1; wm_event(O['EVT_POINTER_FIRST']+2); m.now+=1; assert not heal()  # a move
wm_event(O['EVT_KEY_DOWN']); m.now+=O['STALE_TOUCH_MS']; assert len(heal())==2  # a key is no finger
m.pressed=0; passed()
# With three rows in view the difference shows: the middle one wins, not the top edge.
m=Machine(); w,es=m.page_list(8,height=192,extent=1000)
m.paint(w); m.touch(); m.word(w+O['SCROLL_Y'],200); m.word(w+O['VIEW_ANIMATOR'],0x1234)
m.paint(w); assert m.selected(w)==0
m.word(w+O['VIEW_ANIMATOR'],0); m.paint(w); assert m.selected(w)==6; passed()
# Native click may destroy the current page. Nothing dereferences its target afterwards.
def destroy(a,b):
    m.nodes.clear(); m.top=0
m.on_click=destroy
assert m.confirm()==11 and len(m.dispatched())==1; passed()

def native_row_layout(m, button):
    # Run the real stock layout dispatcher and horizontal layouter. Mock only toolkit
    # collection/geometry services, not the width arithmetic or native child positioning.
    m.handlers.pop(syms['widget_layout_children'],None)
    m.mock('widget_vtable_on_layout_children', 'widget_layout_self',
           'widget_layout_floating_children', 'widget_get_children_for_layout',
           'darray_init', 'darray_deinit', 'widget_move_resize_ex')
    for node, props in m.nodes.items():
        children=props['children']
        if children:
            array=m.alloc(12); values=m.alloc(4*len(children))
            for i,child in enumerate(children): m.word(values+4*i,child)
            m.word(array,len(children)); m.word(array+8,values); m.word(node+0x5c,array)
    assert m.call(address=syms['widget_layout_children'],args=(button,0,0,0))==0

def check_album_tile(m, button):
    def descendants(w):
        return [c for child in m.nodes[w]['children'] for c in [child,*descendants(child)]]
    def geometry(w): return tuple(signed(m.get(w+O[k])) for k in ('W_X','W_Y','W_W','W_H'))
    children=descendants(button)
    original={c:geometry(c) for c in children}
    draw_types=[c for c in children if m.nodes[c]['type']=='image' and original[c][2:]==(110,110)]
    tile=geometry(button)
    for c in children:
        if m.nodes[c]['type']=='hscroll_label': m.nodes[c]['text']='A very long album or artist label '*10
    for _ in range(3):
        # The cover sits inset inside the selection card; repeated paints never compound it.
        m.call(address=payload_syms['album_children'],args=(button,0,0,0),gap=0)
        assert geometry(button)==tile
        for c in draw_types: assert m.nodes[c].get('draw_type',0)==O['IMAGE_DRAW_SCALE_DOWN']
        for c,(x,y,w,h) in original.items():
            actual=geometry(c)
            if w==h==110:
                inset=6 if m.get(c+O['W_PARENT'])==button else 0
                assert actual==(inset,inset,98,98), actual
            elif m.nodes[c]['type']=='hscroll_label':
                assert actual==(6,y-2,98,h), actual
                assert actual[1]+h<=154
                assert m.nodes[c]['text'].startswith('A very long')
            else: assert actual==(x,y,w,h)
    m.calls=[]; m.call(address=payload_syms['album_children'],args=(button,0,0,0),gap=0)
    assert not any(c[0] in ('widget_move_resize','image_set_draw_type') for c in m.calls)
    # The public paint hook keeps the same geometry.
    m.paint(button)
    assert all(geometry(c)[2]==98 for c in children if original[c][2]==110)
    passed()

from ipod import decode
def asset_node(m,n,parent):
    kind,rect,props,children=n
    w=m.node(kind,props.get('name',''))
    m.word(w+O['W_PARENT'],parent)
    for k,v in zip(('W_X','W_Y','W_W','W_H'),rect): m.word(w+O[k],v)
    m.nodes[w]['children']=[asset_node(m,c,w) for c in children]
    return w
m=Machine(); root=asset_node(m,decode((B/'ui/localmusic/album_page.bin').read_bytes()),m.wm); m.top=root
for button in [w for w,n in m.nodes.items() if n['type']=='button' and n['children']]:
    check_album_tile(m,button)

# The album grid's branch of the native row-pool constructor (0x4a6910 with album_modetype 1), untouched by the set:
# 210 px rows of 160 px tiles, stock's layout, and a second prepare reuses the pool.
m = Machine(); w = m.page('artistinfo_page', 'table_client')
m.mock('table_row_create', 'button_create', 'image_create', 'view_create',
       'hscroll_label_create', 'gif_image_create', 'widget_use_style',
       'widget_set_name', 'widget_set_children_layout', 'image_set_draw_type',
       'image_base_set_image', 'set_hscroll_label_attribute')
m.word(syms['album_modetype'], 1)
assert m.call(address=0x4a6910, args=(w, w, 4, 0)) == 0
rows = m.nodes[w]['children']
assert len(rows) == 4
for row in rows:
    assert m.get(row+O['W_H']) == 210
    for button in m.nodes[row]['children']:
        assert m.get(button+O['W_H']) == 160
m.word(w+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm)
for row in rows:
    for button in m.nodes[row]['children']: check_album_tile(m,button)
before = len(m.nodes)
assert m.call(address=0x4a6910, args=(w, w, 4, 0)) == 0 and len(m.nodes) == before
assert all(m.get(n+O['W_CHILDREN_LAYOUT'])==0 for row in rows for n in m.nodes[row]['children'])
passed()

def oled_boot(m):  # the first system_bar load, as on a unit (accent, theme_boot's migration): the windows stock_ui_load was asked for
    m.record_syscalls(); m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'],16)]='stock_ui_load'
    m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0],args=(m.string('system_bar'),0,0,0),gap=0)
    return [m.text(c[1]) for c in m.calls if c[0]=='stock_ui_load']
# OLED's Artists/Composers/Genres rows are one line at its set's lib_pitch (32 px): the leading artwork (lib_art 0)
# and playing gif and the second line hide, and the title sits centred in the row.
assert OLED_G.lib_pitch==32 and OLED_G.lib_art==0
for address in (0x4b39f4,):
    m=Machine(); oled_boot(m); w=m.page('folder_page','table_client')
    m.mock('table_row_create','button_create','image_create','view_create','hscroll_label_create','gif_image_create',
           'widget_use_style','widget_set_name','widget_set_children_layout','image_set_draw_type','image_base_set_image',
           'set_hscroll_label_attribute','widget_set_visible')
    assert m.call(address=address,args=(w,w,4,0))==0
    rows=m.nodes[w]['children']; button=m.nodes[rows[0]]['children'][0]
    assert {m.get(r+O['W_H']) for r in rows}=={32} and m.get(button+O['W_H'])==32
    native_row_layout(m,button)
    kids=m.nodes[button]['children']
    text=next(c for c in kids if m.nodes[c]['type']=='hscroll_label' or any(m.nodes[t]['type']=='hscroll_label' for t in m.nodes[c]['children']))
    title=next((c for c in m.nodes[text]['children'] if m.nodes[c]['type']=='hscroll_label'),text)
    assert [m.nodes[c]['name'] for c in kids[:kids.index(text)] if m.nodes[c]['visible'] and not m.nodes[c]['name'].startswith('img_choice')]==[],address
    assert [c for c in m.nodes[text]['children'] if c!=title and m.nodes[c]['visible']]==[],address
    y,h=(signed(m.get(title+O[k])) for k in ('W_Y','W_H')); assert 2*y+h==32,(address,y,h)
    # Outside multi-select (img_choice hidden) the title container starts at the set's text_x on screen, clear of
    # the glass (compact_row_layout): the button sits at 20. In multi-select stock's box keeps its place and the text
    # its own.
    kx=lambda c: signed(m.get(c+O['W_X']))
    multi=kx(text); assert multi>8 and kx(button)==20,address
    for c in kids:
        if m.nodes[c]['name'].startswith('img_choice'): m.nodes[c]['visible']=0
    native_row_layout(m,button); assert kx(button)+kx(text)==OLED_G.text_x,address
    m.word(button+O['W_X'],30); native_row_layout(m,button); assert kx(text)==0,address  # a row further in than text_x: its text at its start
    m.word(button+O['W_X'],20); native_row_layout(m,button)
    edge=kx(text)+m.get(text+O['W_W'])  # the right edge stays where the layout put it
    for c in kids:
        if m.nodes[c]['name'].startswith('img_choice'): m.nodes[c]['visible']=1
    native_row_layout(m,button); assert kx(text)==multi and kx(text)+m.get(text+O['W_W'])==edge,address
    for c in kids:
        if m.nodes[c]['name'].startswith('img_choice'): m.nodes[c]['visible']=0
    # A leading child stock left unnamed (a Playlists row's) hides like the others, and does not fault.
    unnamed=m.node('image'); m.nodes[button]['children'].insert(0,unnamed); native_row_layout(m,button)
    assert not m.nodes[unnamed]['visible'],address
passed()

# OLED's Albums row (its set's art_pitch, 52 px): the title takes the tall role (list.row tall_font, tall_px) and the
# second line, nested in stock's view_artist, the meta role (meta_font at meta_px), and stock's secondary grey in its
# one state a row uses, which the style hook turns to the theme's muted. Its colour is written once.
def album_row(boot):
    m=Machine(); boot(m); w=m.page('folder_page','table_client')
    m.mock('table_row_create','button_create','image_create','view_create','hscroll_label_create','gif_image_create',
           'widget_use_style','widget_set_name','widget_set_children_layout','image_set_draw_type','image_base_set_image',
           'set_hscroll_label_attribute','widget_set_visible')
    assert m.call(address=0x4a6910,args=(w,w,1,0))==0
    return m,m.nodes[m.nodes[w]['children'][0]]['children'][0]
m,button=album_row(oled_boot); native_row_layout(m,button); assert m.get(button+O['W_H'])==OLED_G.art_pitch
title,meta=(next(n for n,v in m.nodes.items() if v['name']==name) for name in ('scrlabel1_name','scrlabel2_name'))
assert (OLED_T['font_px[ROLE_TALL]'],OLED_T['font_px[ROLE_META]'])==(24,18)  # theme.xml list.row: inter at 24 and 18
for st_ in ('normal','focused','disable'):
    assert (m.nodes[title][f'style:{st_}:font_name'],m.nodes[title][f'style:{st_}:font_size'])==('inter',24),st_
    assert (m.nodes[meta][f'style:{st_}:font_name'],m.nodes[meta][f'style:{st_}:font_size'])==('inter',18),st_
assert m.nodes[meta]['style:normal:text_color']==signed(0xffaaaaaa) and 'style:normal:text_color' not in m.nodes[title]
# OLED's columns (list.art), on screen: the cover at art_x 24 (the row sits at 20), the text's box from text_x less
# hscroll_label's 2 px (70: text at 72) to text_end and its margin (321), whichever stock's layout gave them.
info=next(n for n,v in m.nodes.items() if v['name']=='view_info'); art_=next(n for n,v in m.nodes.items() if v['name']=='img_icon')
rx=m.get(button+O['W_X'])
assert rx+m.get(art_+O['W_X'])!=OLED_T['art_x']  # stock's choice box showing (multi-select): stock's places
m.nodes[next(n for n,v in m.nodes.items() if v['name']=='img_choice')]['visible']=0; native_row_layout(m,button)
assert rx+m.get(art_+O['W_X'])==OLED_T['art_x'] and rx+m.get(info+O['W_X'])==OLED_T['art_text_x']-2 and m.get(info+O['W_W'])==OLED_T['art_text_end']-OLED_T['art_text_x']+4,(rx,m.get(art_+O['W_X']),m.get(info+O['W_X']),m.get(info+O['W_W']))
m.nodes[meta]['style:normal:text_color']=0; native_row_layout(m,button); assert m.nodes[meta]['style:normal:text_color']==0; passed()

# The three non-pooled constructors: album tracks, artist tracks and playlists, each building its one row.
for address,page in ((0x4a7f44,'artistinfo_page'),(0x4af8ac,'artistinfo_page'),(0x4b4ee8,'artistinfo_page'),(0x4b4ee8,'playlist_page')):
    for reopen in range(2):
        m=Machine(); w=m.page(page,'scroll_view')
        m.word(w+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm)
        m.nodes[w]['name']='scroll_view_track'
        m.row_record=m.alloc(0x80)
        for off in (8,12,16,24): m.word(m.row_record+off,m.string('日本語 Title'))
        m.word(m.row_record+0x48,1)
        m.mock('button_create','list_item_create','image_create','view_create','hscroll_label_create',
               'gif_image_create','widget_use_style','widget_set_name','widget_set_text_utf8',
               'widget_set_visible','widget_on','widget_destroy_children','image_set_draw_type',
               'image_base_set_image','set_hscroll_label_attribute','hscroll_label_set_only_focus',
               'hscroll_label_set_ellipses','hscroll_label_set_speed','gif_image_play','deque_at',
               'deque_size','tk_snprintf','batch_get_selectitem','file_is_playing','getFormatString',
               'getMusicByPlayList','get_albumcover_listsize','mclGetPlayStatus','list_get_img_pic',
               'strcmp@GLIBC_2.0','toolsTrimLeft','toolsTrimRight')
        m.handlers[0x4ad020]='row_toolbar'
        assert m.call(address=address,args=(w,0,0,0))==0
        buttons=[n for n,v in m.nodes.items() if v['type']=='button']
        assert len(buttons)==1
        if address==0x4b4ee8:
            button=buttons[0]
            more=next(c for c in m.nodes[button]['children'] if m.nodes[c]['name'].startswith('img_more'))
            native_row_layout(m,button)
            # Stock opens More for pointer x >= 286: the dots must keep that trailing position.
            assert m.get(button+O['W_X'])+m.get(more+O['W_X'])>=286
passed()

# Long Return executes the stock gates and release filter.
def long_machine():
    m = Machine()
    m.mock('netdisk_folder_clear', 'navigator_back_to_home', 'awake_screen',
           'getFormatString', 'navigator_to_with_context', 'navigator_window_is_exist')
    return m

def long_return(m):
    return m.call(170, address=syms['on_wm_keylong_fun'], event_type=0x111, gap=0)

def destinations(m):
    return [c for c in m.calls if c[0] in ('navigator_back_to_home', 'navigator_switch_to_with_context')]

def ui_pass(m, gap=0):
    """One UI loop pass (ringnav_sleep), gap ms on; stock's own sleep is stubbed."""
    m.handlers[HOOKS['main_loop_sleep_default'][0] + 12] = 'stock_sleep'
    assert m.call(address=HOOKS['main_loop_sleep_default'][0], args=(0x1234, 0, 0, 0), gap=gap) == 0

for page in ('home_page', 'folder_page', 'playing_page', 'sysset_page'):
    m = long_machine(); m.page(page)
    # No playback data is initialized: the switch must work with an empty queue as well.
    assert long_return(m) == 0
    dest = destinations(m)
    assert len(dest) == 1
    assert dest[0][0] == ('navigator_switch_to_with_context' if page != 'playing_page' else 'navigator_back_to_home')
    if page != 'playing_page':
        assert m.text(dest[0][1]) == 'playing_page'
        assert [m.get(dest[0][2] + 4*i) for i in range(4)] == [0, 0, 255, 2]
    # Here the navigator is only recorded, so the switch never lands and the latch is left
    # armed; the NavigationMachine checks below cover the landed switch that drops it.
    held = 11
    assert m.call(170, gap=0) == held
    assert m.call(170, gap=0) == 0   # next short Return still reaches stock Back
    for _ in range(3):
        assert long_return(m) == 0
        assert len(destinations(m)) == 1
    assert m.call(170, gap=0) == held
passed()

# The stock long-key gates stay effective, with the shared navigation restrictions added.
for flag, value in GATES:
    m = long_machine(); m.page('folder_page'); m.byte(syms[flag], value)
    long_return(m)
    # Stock itself blocks power-off, guide and test mode; usable()'s shared restrictions block the rest.
    assert not destinations(m), flag
for light in (0, 1):
    for lock in (0, 1):
        for mode in range(4):
            m = long_machine(); m.page('folder_page')
            m.byte(syms['g_backlight_status'], light)
            m.byte(syms['g_keylock_flag'], lock); m.byte(syms['g_keylock_mode'], mode)
            long_return(m)
            blocked = not light
            assert bool(destinations(m)) == (not blocked)
passed()
m = long_machine(); m.page_list()
assert m.call(O['KEY_CENTER']) == 11 and m.timers
long_return(m)
assert not m.timers
m.advance(201)
assert not m.dispatched()
# A hold blocked elsewhere still swallows its release, so no delayed page action runs.
m = long_machine(); m.page('folder_page'); m.byte(syms['g_usblink_status'], 2)
long_return(m)
assert not destinations(m)
assert m.call(170, gap=0) == 11
passed()
# Hold Return on Now Playing goes back to the page it opened over while that lies right under it; otherwise,
# or with no such page, Home.
for under,want in ((True,'navigator_back'),(False,'navigator_back_to_home')):
    m = long_machine(); m.mock('navigator_back'); m.page('folder_page'); folder = m.top
    assert long_return(m) == 0 and destinations(m)[0][0] == 'navigator_switch_to_with_context'
    np = m.node('window', 'playing_page'); other = m.node('window', 'sysset_page')
    m.nodes[m.wm] = {'children': [folder, np] if under else [folder, other, np]}; m.top = np
    m.call(170, gap=0); m.calls = []
    assert long_return(m) == 0 and [c[0] for c in m.calls if c[0] in ('navigator_back', 'navigator_back_to_home')] == [want]
    m.call(170, gap=0); m.calls = []
    assert long_return(m) == 0 and [c[0] for c in m.calls if c[0] in ('navigator_back', 'navigator_back_to_home')] == ['navigator_back_to_home']
passed()
# Hold Return: Home (Q2POD HOLDRETURN 1) goes Home from any page but Home, Now Playing included;
# Home still opens Now Playing. Going Home leaves the stock latch armed, as the stock Home call did:
# the hold's release is swallowed and the next short Return passes.
for page in ('folder_page', 'playing_page', 'sysset_page', 'home_page'):
    m = long_machine(); m.mock('navigator_back'); m.config['HOLDRETURN'] = '1'; m.page(page)
    want = 'navigator_switch_to_with_context' if page == 'home_page' else 'navigator_back_to_home'
    assert long_return(m) == 0 and [c[0] for c in m.calls if c[0] in ('navigator_back', 'navigator_back_to_home', 'navigator_switch_to_with_context')] == [want], page
    if page != 'home_page': assert m.call(170, gap=0) == 11 and m.call(170, gap=0) == 0, page
passed()
# Over quick settings the hold closes it, as Return does, and acts on the page under it once the dialog
# has gone (quick_poll, every pass): Now Playing opened over it left it open underneath, and stock's
# pull-down starts none while one is. The release goes to that latch, not to closing it; a hold whose
# dialog is still up QS_HOLD_MS later is forgotten.
def quits(m): return [c[1:3] for c in m.calls if c[0] == 'dialog_quit']
for hold_home, want in (('0', 'navigator_switch_to_with_context'), ('1', 'navigator_back_to_home')):
    m = long_machine(); m.mock('dialog_quit'); m.config['HOLDRETURN'] = hold_home
    m.page('folder_page'); page = m.top; quick = m.top = m.node('dialog', 'statusbar_dialog')
    assert long_return(m) == 0 and not destinations(m) and quits(m) == [(quick, 0)], hold_home
    assert m.call(170, gap=0) == 11 and not quits(m)
    ui_pass(m); assert not destinations(m) and not quits(m)  # still up: its own idle closes it
    m.top = page; m.animating = 1; ui_pass(m, 20); assert not destinations(m)  # its close still moving
    m.animating = 0; ui_pass(m, 20); assert [c[0] for c in destinations(m)] == [want], hold_home
    ui_pass(m, 20); assert not destinations(m)  # once
m = long_machine(); m.mock('dialog_quit'); m.page('folder_page'); page = m.top; m.top = m.node('dialog', 'statusbar_dialog')
assert long_return(m) == 0 and len(quits(m)) == 1
ui_pass(m, O['QS_HOLD_MS']); m.top = page; ui_pass(m, 20); assert not destinations(m)
# a short Return there closes it
m = long_machine(); m.mock('dialog_quit'); m.top = m.node('dialog', 'statusbar_dialog')
assert m.call(170, gap=0) == 11 and quits(m) == [(m.top, 0)]
passed()
# Return or Centre (Cancel) on the scan's dialog: stock's own while the scan runs (it stops it), closes it at once after.
for done, key in ((0, 170), (1, 170), (0, O['KEY_CENTER']), (1, O['KEY_CENTER'])):
    m = Machine(); m.mock('dialog_quit'); m.top = m.node('dialog', 'updatemusic_dialog'); m.word(O['SCAN_DONE'], done)
    m.call(key, gap=0); assert [c[1:3] for c in m.calls if c[0] == 'dialog_quit'] == ([(m.top, 0)] if done else []), (done, key)
passed()
# Songs over an empty library: stock hides the list and shows its note over Scan Music Now (btn_upmusic). Centre presses
# it, as a tap does, and stops there, short of on_wm_keyup_fun, whose Centre turns the screen off. The wheel leaves it alone,
# and over a list (the note's view hidden) Centre does not press it.
def empty_songs(shown):
    m = Machine(); btn = m.node('button', 'btn_upmusic')
    view = m.node('view', 'view', [m.node('image', 'image'), m.node('label', 'label_scanfolder'), btn], visible=shown)
    m.top = m.node('window', 'allmusic_page', [m.node('view', 'view_navbar_allplay'), view, m.node('table_view', 'table_view', visible=1 - shown)])
    for w in (m.top, view):
        for c in m.nodes[w]['children']: m.word(c + O['W_PARENT'], w)
    m.word(m.top + O['W_PARENT'], m.wm); m.nodes[m.wm] = {'children': [m.top]}; m.word(m.wm + O['W_KEY_TARGET'], m.top)
    return m, btn
m, btn = empty_songs(1)
r = m.call(O['KEY_CENTER']); assert r == 11 and [c[1] for c in m.dispatched()] == [btn], (r, m.dispatched())
assert not [c for c in m.calls if c[0] == 'screen_action']
m.call(); assert not m.dispatched()
m, btn = empty_songs(0); m.call(O['KEY_CENTER']); assert btn not in [c[1] for c in m.dispatched()]
passed()
# Now Playing opened over Home, which lies right under it: Recent would go Back, Home goes Home.
m = long_machine(); m.mock('navigator_back'); m.config['HOLDRETURN'] = '1'; m.page('home_page'); home = m.top
long_return(m); np = m.node('window', 'playing_page')
m.nodes[m.wm] = {'children': [home, np]}; m.top = np; m.call(170, gap=0); m.calls = []
assert long_return(m) == 0 and [c[0] for c in m.calls if c[0] in ('navigator_back', 'navigator_back_to_home')] == ['navigator_back_to_home']
passed()
# Other long-key paths remain byte-for-byte stock; exercise the inert keys and power gate.
# Home is not a local list, so a Play/Pause hold there stays stock too (queue menu below).
for key in (171, 172, 173, 222, 223, 218):
    m = long_machine(); m.page('home_page')
    if key == 218: m.byte(syms['g_poweroff_state'], 2)
    m.call(key, address=syms['on_wm_keylong_fun'], event_type=0x111)
    assert not destinations(m)
passed()

# Four complete compact rows resolve the same target for touch and centre at every row.
for index in range(4):
    m = Machine(); w, es = m.page_list(4, height=288, extent=288, name='folder_page')
    for i, e in enumerate(es):
        m.word(e+O['W_Y'], i*72); m.word(e+O['W_H'], 68)
    m.touch(); m.click(es[index])
    assert m.confirm() == 11 and m.dispatched()[0][1] == es[index]
passed()

# Recycle a small row pool: selection belongs to the logical index, never the widget.
m=Machine(); w,rows,entries=m.table_page(rebind=True)
m.paint(w); assert m.selected(w)==0
assert m.call()==11 and m.selected(w)==1
assert m.call()==11 and m.selected(w)==2 and m.get(w+O['TABLE_TOP'])==48  # whole rows (OLED's whole rows)
assert m.moved()[-1][0]=='table_client_set_yoffset'
assert m.confirm()==11 and m.dispatched()[0][1]==entries[1]; passed()
# A wheel step ends its scroll as a touch scroll does, so stock lists load the covers in view.
m=Machine(); w,rows,entries=m.table_page(rebind=True); m.mock('widget_dispatch_simple_event')
m.paint(w); m.call(); m.call()
assert ('widget_dispatch_simple_event',w,O['EVT_SCROLL_END']) in [c[:3] for c in m.calls]; passed()
# A touch click in a rebound row immediately changes what centre opens.
m.touch(); m.click(entries[0]); assert m.selected(w)==1
assert m.confirm()==11 and m.dispatched()[0][1]==entries[0]; passed()
# Swipe out of the old pool, then centre: settle/re-resolve before dispatch.
m.touch(); m.word(w+O['TABLE_TOP'],480); m.bind(rows,480); m.word(w+O['TABLE_ANIMATOR'],0x9876)
assert m.confirm()==11 and m.selected(w)==10 and m.dispatched()[0][1]==entries[0]
assert m.get(w+O['TABLE_ANIMATOR'])==0; passed()
# Returning to a surviving menu keeps a valid selection; shrinking data repairs it.
oldtop=m.top; m.page('playing_page'); assert m.confirm()==0
m.top=oldtop; m.paint(w); assert m.selected(w)==10
m.word(w+O['TABLE_ROWS'],1); m.word(w+O['TABLE_TOP'],0); m.bind(rows); m.paint(w)
assert m.selected(w)==0; passed()

# A recreated page recalls the last selected row and reveals it without a sacrificial press.
def walk(n,steps,texts=(),extent=1000,flat=False,**kw):
    """Paint a fresh labelled list of n entries, then wheel down steps rows; flat: its bar FLAT's."""
    m=Machine(); flat and m.config.update(FLAT); w,es=m.page_list(n,extent=extent,**kw); m.label(es,texts); m.paint(w)
    for _ in range(steps): assert m.call()==11
    assert m.selected(w)==steps
    return m,w,es
m,w,es=walk(10,5,flat=True); assert m.get(w+O['SCROLL_Y'])==192  # OLED's whole rows: the row a whole row under the top
w2,es2=m.page_list(10,extent=1000)
assert m.paint(w2)==0 and m.selected(w2)==5 and m.get(w2+O['SCROLL_Y'])==192
assert m.sel()==(inset,48,240-2*inset,48)
assert m.confirm()==11 and m.dispatched()[0][1]==es2[5]; passed()
# The theme's recall (recall_ms, GLIDE_MS for OLED): the remembered row glides in over its 300 ms.
ms=OLED_T['recall_ms']; assert ms==O['GLIDE_MS']==300
rm,_,_=walk(10,5)
rw,_=rm.page_list(10,extent=1000); rm.calls=[]
assert rm.paint(rw)==0 and rm.selected(rw)==5 and rm.get(rw+O['SCROLL_Y'])==192
assert rm.moved()[0][0]=='scroll_view_scroll_delta_to' and rm.nodes[rw].get('glide_ms',0)==ms
passed()

# Memory is per audited context: visiting another page leaves it alone.
w3,es3=m.page_list(10,extent=1000,name='display_page')
assert m.paint(w3)==0 and m.selected(w3)==0
w4,es4=m.page_list(10,extent=1000)
assert m.paint(w4)==0 and m.selected(w4)==5; passed()

# A stale remembered row is ignored when the new list is shorter.
w5,es5=m.page_list(2,extent=300)
assert m.paint(w5)==0 and m.selected(w5)==0
assert m.confirm()==11 and m.dispatched()[0][1]==es5[0]; passed()

# A settled swipe stores the row the user sees, not the pre-swipe selection.
m=Machine(); w,es=m.page_list(10,extent=1000)
m.paint(w); m.touch(); m.word(w+O['SCROLL_Y'],240); m.word(w+O['VIEW_ANIMATOR'],0x1234)
m.paint(w); assert m.selected(w)==0
m.word(w+O['VIEW_ANIMATOR'],0); m.paint(w); assert m.selected(w)==5
w2,es2=m.page_list(10,extent=1000)
assert m.paint(w2)==0 and m.selected(w2)==5 and m.get(w2+O['SCROLL_Y'])==192; passed()

# Position memory follows row text across a recreation, not just the index.
m,w,es=walk(6,4,[f'track {i}' for i in range(6)])
w2,es2=m.page_list(6,extent=1000); m.label(es2,[f'track {i}' for i in (4,0,1,2,3,5)])
assert m.paint(w2)==0 and m.selected(w2)==0
assert m.confirm()==11 and m.dispatched()[0][1]==es2[0]; passed()
# The same recall with (rows, detents, texts before, texts after recreation, recalled row).
for n,steps,before,after,want in (
        (6,3,[],[],3),  # rows without text fall back to the remembered index
        # Duplicate row text restores the occurrence the user left, not the first duplicate.
        (6,4,['Intro']*6,['Intro']*6,4),
        # After a re-sort, equal-text rows resolve to the occurrence nearest the old position.
        (6,4,[None]*4+['dup'],[None,'dup',None,None,None,'dup'],5),
        (6,3,[None]*3+['dup'],[None,None,'dup',None,'dup'],2),  # equal distance keeps the earlier
        # Two rows share a title; the second text decides before proximity does.
        (4,1,[('Intro','Artist A'),('Intro','Artist B'),'Intro','Intro'],
             ['Intro','Intro',('Intro','Artist A'),('Intro','Artist B')],3),
        # A subtitle missing from the recreated rows never blocks the primary match.
        (4,1,['Intro',('Intro','Artist B'),'Intro','Intro'],['Intro',None,None,'Intro'],0)):
    m,w,es=walk(n,steps,before)
    w2,es2=m.page_list(n,extent=1000); m.label(es2,after)
    assert m.paint(w2)==0 and m.selected(w2)==want
    passed()

# Run the actual stock label text accessor, including value_wstr and the label vtable.
# U+0100 and U+0200 share a low zero byte; titles/subtitles must use complete code points.
m=Machine(); w,es=m.page_list(4,extent=1000)
del m.handlers[syms['widget_get_text']]
m.handlers[syms['strcmp@GLIBC_2.0']]='tk_strcmp'
def stock_text(e,text):
    m.word(e+0x74,syms['g_label_vtable'])
    m.word(e+0x38,len(text)); m.word(e+0x40,m.wide_string(text))
for i,e in enumerate(es):
    stock_text(e,'\u0100 Intro')
    sub=m.node('label'); stock_text(sub,['\u0100','\u0200','\U0001f600','音楽'][i])
    m.nodes[e]['children']=[sub]
# The narrow accessor really returns NULL, even though widget_get_text sees the label.
del m.handlers[syms['widget_get_prop_str']]
assert m.call(address=syms['widget_get_prop_str'],args=(es[0],m.string('text'),0,0))==0
m.mock('widget_get_prop_str')
m.paint(w); m.call(); assert m.selected(w)==1
w2,es2=m.page_list(4,extent=1000)
for i,e in enumerate(es2):
    stock_text(e,'\u0100 Intro')
    sub=m.node('label'); stock_text(sub,['\u0100','\U0001f600','音楽','\u0200'][i])
    m.nodes[e]['children']=[sub]
assert m.paint(w2)==0 and m.selected(w2)==3
assert m.confirm()==11 and m.dispatched()[0][1]==es2[3]; passed()

# Folder memory belongs to the full path, even when every visible row title is identical.
m=Machine(); m.folder('/sd/Folder A')
w,es=m.page_list(8,name='folder_page'); m.label(es,['Intro']*8)
m.paint(w)
for _ in range(5): m.call()
w2,es2=m.page_list(8,name='folder_page'); m.label(es2,['Intro']*8)
m.paint(w2); assert m.selected(w2)==5  # same path still recalls
m.folder('/sd/Folder B')
w3,es3=m.page_list(8,name='folder_page'); m.label(es3,['Intro']*8)
m.paint(w3); assert m.selected(w3)==0
# A surviving surface rebound to another folder also resets, even at the same row count.
for _ in range(3): m.call()
m.folder('/sd/Folder C')
m.word(w3+O['SCROLL_Y'],0); m.paint(w3); assert m.selected(w3)==0; passed()
# Nested folder returns restore each level on recreated or rebound surfaces.
for reuse in (False,True):
    m=Machine(); w,es=m.page_list(10,name='folder_page')
    for path,wanted in (('/sd',6),('/sd/child',4),('/sd/child/grandchild',2)):
        m.folder(path)
        if not reuse: w,es=m.page_list(10,name='folder_page')
        m.word(w+O['SCROLL_Y'],0); m.paint(w); assert m.selected(w)==0
        for _ in range(wanted): m.call()
    for path,wanted in (('/sd/child',4),('/sd',6),('/sd/child/grandchild',2)):
        m.folder(path)
        if not reuse: w,es=m.page_list(10,name='folder_page')
        m.word(w+O['SCROLL_Y'],0); m.paint(w)
        assert m.selected(w)==wanted and m.get(w+O['SCROLL_Y'])==(wanted-1)*48  # a whole row under the top (OLED's whole rows)
    passed()

# Exactly 64 scopes fit. Selection promotes; restoration alone does not change recency.
for promote in (False,True):
    m=Machine(); w,es=m.page_list(4,name='folder_page')
    for i in range(64):
        m.folder(f'/sd/{i}',w); m.call(); m.call()
    m.folder('/sd/0',w); assert m.selected(w)==2
    if promote: m.call()  # update and protect the oldest entry
    m.folder('/sd/64',w); m.call()
    for i,wanted in ((0,3),(2,2)) if promote else ((1,2),(63,2)):
        m.folder(f'/sd/{i}',w); assert m.selected(w)==wanted
    evicted=1 if promote else 0
    m.folder(f'/sd/{evicted}',w); assert m.selected(w)==0
    passed()

# A full history must not add work to steady painting or turns in the current scope.
costs=[]
for occupancy in (1,64):
    m=Machine(); w,es=m.page_list(20,height=192,extent=960,name='folder_page')
    for i in range(occupancy):
        m.folder(f'/sd/{i:02}',w); m.call()
    m.paint(w)  # the first paint of a selection arms its marquee (marq_poll)
    instructions=[0]
    def count_payload(*args): instructions[0]+=1
    hook=m.u.hook_add(UC_HOOK_CODE,count_payload,begin=BASE,end=SCRATCH-1)
    m.paint(w); paint_cost=instructions[0]; instructions[0]=0
    m.call(); costs.append((paint_cost,instructions[0]))
    m.u.hook_del(hook)
assert costs[0]==costs[1],costs
passed()
# A long scroll view's first paint fits the paint hook's budget: 500 rows of OLED's 32 px rows or two-line 52 px ones,
# scrolled to row 100 or at the top. Every row is walked (load_rows); nothing else may walk them all again (pl_first
# answers at once with no playlist just made).
def long_view(h,top=100):
    lm=Machine(); lw,les=lm.page_list(500,height=266,extent=500*h); lm.word(lw+O['W_W'],375)
    for i,e in enumerate(les): lm.word(e+O['W_Y'],i*h); lm.word(e+O['W_H'],h)
    # each corner radius's coverage table is made once a boot, at its first paint (corner_cover.c): made first, so this
    # counts the paint alone
    for r in range(1,17): lm.call(address=payload_syms['corner_cover'],args=(r,0,0,0),gap=0)
    lm.word(lw+O['SCROLL_Y'],top*h); count=[0]; hook=lm.u.hook_add(UC_HOOK_CODE,lambda *a: count.__setitem__(0,count[0]+1))
    lm.call(address=HOOKS['widget_on_paint_background'][0],args=(lw,lm.canvas,0,0),gap=0); lm.u.hook_del(hook)
    print(f'OLED scroll view of 500 {h} px rows from row {top}: {count[0]} instructions for its first paint')
    assert count[0]<100000,count[0]
for h,top in ((32,0),(32,100),(52,100)): long_view(h,top)
passed()

# Returning after another scope still applies text matching and stale-index rejection.
for shortened in (False,True):
    m=Machine(); m.folder('/sd/parent')
    w,es=m.page_list(8,name='folder_page'); m.label(es,[f'track {i}' for i in range(8)]); m.paint(w)
    for _ in range(6): m.call()
    m.folder('/sd/child')
    w,es=m.page_list(8,name='folder_page'); m.paint(w); m.call()
    m.folder('/sd/parent')
    w,es=m.page_list(3 if shortened else 8,name='folder_page'); m.label(es,[f'track {i}' for i in range(8)])
    if not shortened: m.nodes[es[3]]['text']='track 6'; m.nodes[es[6]]['text']='other'
    m.paint(w); assert m.selected(w)==(0 if shortened else 3)
    passed()

# An unavailable or unterminated path cannot supply a content identity.
for path in (b'\0',b'x'*1024):
    m=Machine(); m.u.mem_write(syms['g_folder_path'],path)
    w,es=m.page_list(8,name='folder_page'); m.paint(w); m.call(); m.call()
    w2,es2=m.page_list(8,name='folder_page'); m.paint(w2)
    assert m.selected(w2)==0; passed()

# Root collections keep selection after a detail page overwrites the shared query.
for cls in (O['CLASS_ALBUMS'],0xf004,0xf005,0xf006):
    for reuse in (False,True):
        m=Machine(); m.word(syms['g_class_type'],cls)
        w,rs,es=m.table_page(n=12,name='localclass_page',rebind=True)
        m.word(w+O['W_H'],288); m.paint(w)
        for _ in range(7): m.call()
        before=(m.selected(w),m.get(w+O['TABLE_TOP'])); assert before==(7,160)  # OLED's 32 px rows
        page=m.top; m.page('artistinfo_page')
        m.u.mem_write(syms['g_local_classinfo_save']+0x10d,b'Selected Artist\0')
        m.top=page
        if not reuse: w,rs,es=m.table_page(n=12,name='localclass_page',rebind=True)
        m.word(w+O['W_H'],288); m.paint(w)
        assert (m.selected(w),m.get(w+O['TABLE_TOP']))==before
        # Switching Artist / Album Artist still opens a separate collection.
        m.word(syms['g_artist_type'],1)
        w,rs,es=m.table_page(n=12,name='localclass_page',rebind=True); m.paint(w)
        assert m.selected(w)==0
        passed()

# Same-sized local lists must not inherit selection across query or browsing-mode changes.
for changed in ('g_class_type','g_local_classinfo_save','g_artist_type','album_modetype'):
    m=Machine(); w,rs,es=m.table_page(); m.paint(w); m.call(); m.call()
    w2,rs2,es2=m.table_page(); m.paint(w2); assert m.selected(w2)==2
    m.word(syms[changed],m.get(syms[changed])+1)
    w3,rs3,es3=m.table_page(); m.paint(w3); assert m.selected(w3)==0
    m.call(); m.call()
    m.word(syms[changed],m.get(syms[changed])+1)
    m.word(w3+O['TABLE_TOP'],0); m.paint(w3); assert m.selected(w3)==0
    passed()
# Each query can be revisited, including on a surface reused for other queries.
for reuse in (False,True):
    for changed in ('g_class_type','g_local_classinfo_save','g_artist_type','album_modetype'):
        m=Machine(); w,rs,es=m.table_page(n=20)
        for query,wanted in ((10,7),(20,4),(30,2)):
            m.word(syms[changed],query)
            if not reuse: w,rs,es=m.table_page(n=20)
            m.word(w+O['TABLE_TOP'],0); m.paint(w); assert m.selected(w)==0
            for _ in range(wanted): m.call()
        for query,wanted in ((10,7),(20,4),(30,2)):
            m.word(syms[changed],query)
            if not reuse: w,rs,es=m.table_page(n=20)
            m.word(w+O['TABLE_TOP'],0); m.paint(w)
            assert m.selected(w)==wanted and m.get(w+O['TABLE_TOP'])==(wanted-1)*48
        passed()
# Without an audited content identity, a recreated detail, dialog or network page starts fresh.
for name in ('netdiskfolder_page','tidal_albuminfo_page','playerqueue_page',
             'search_dialog','tidal_search_dialog'):
    m=Machine(); w,es=m.page_list(8,name=name); m.paint(w)
    for _ in range(3): m.call()
    m.paint(w); assert m.selected(w)==3
    w2,es2=m.page_list(8,name=name); m.paint(w2); assert m.selected(w2)==0
    passed()

# Search result dialogs navigate their list and centre opens the highlighted result.
for name in ('search_dialog','tidal_search_dialog'):
    m=Machine(); w,es=m.page_list(8,name=name)
    m.paint(w)
    for _ in range(2): assert m.call()==11
    assert m.selected(w)==2
    assert m.confirm()==11 and m.dispatched()[0][1]==es[2]
    passed()
# The search input dialogs have no navigable pane: they stay off the allowlist so the wheel
# keeps changing volume instead of being consumed by a window that cannot scroll.
for name in ('searchbox_dialog','tidal_searchbox_dialog'):
    m=Machine(); m.top=m.node('window',name,[m.node('view')])
    assert m.call()==0 and not m.moved(); passed()
