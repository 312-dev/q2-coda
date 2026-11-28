class DepthMachine(CoverflowMachine):
    budget=50_000_000  # decoding and rendering run in the payload, at native emulator speed (fast)
    def __init__(self,**kw):
        super().__init__(**kw)
        self.u.mem_map(BIG,BIG_SIZE); self.big=BIG
        self.frames=[]; self.destroyed=[]; self.draws=[]; self.loads=[]; self.unloads=0; self.pixels={}
        self.art_size=(160,160)  # the covers' decoded size; the placeholder is always 160 square
        self.art_sizes={}  # by URL ending, sizes other than art_size
        self.handlers[syms['sem_post@GLIBC_2.2']]='loader_wake'  # the loader's thread can't run here: its body, at each wake-up
        self.mock('strstr@GLIBC_2.0')  # mem_available_kb's, before the letters' pool: no /proc/meminfo here, so no estimate
        self.fast()
    def big_alloc(self,n):
        a=self.big; self.big+=(n+15)&~15; assert self.big<=BIG+BIG_SIZE; return a
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        a,b,c,d=[u.reg_read(r) for r in REGS]
        if name=='c:calloc@GLIBC_2.0' and a*b>0x10000: ret=self.big_alloc(a*b); self.live[ret]=a*b
        elif name=='bitmap_create_ex':
            ret=self.big_alloc(0x48); data=self.big_alloc(b*c)
            self.word(ret,a); self.word(ret+4,b); self.word(ret+8,c); self.u.mem_write(ret+0xc,struct.pack('<HH',0,d)); self.word(ret+0x14,data)
            self.frames.append(ret)
        elif name=='bitmap_destroy': self.destroyed.append(a); ret=0
        elif name=='loader_wake':
            self.wakes=getattr(self,'wakes',0)+1
            while self.nested(payload_syms['ld_step']): pass
            ret=0
        elif name=='bitmap_lock_buffer_for_read': ret=self.get(a+0x14)
        elif name=='widget_load_image':
            url=self.text(b); self.loads.append(url) if a==self.top else None  # Coverflow's, not iPod Home's
            self.image_loads.append(url)
            if url in getattr(self,'image_fail',()): self.returns(1,clobber=True); return
            w,h=(160,160) if url=='default_album_flow' else next((v for k,v in self.art_sizes.items() if url.endswith(k)),self.art_size)
            if url not in self.pixels:
                self.pixels[url]=self.big_alloc(w*h*4); self.u.mem_write(self.pixels[url],cover_pixels(len(self.pixels),w*h))
            self.word(c,w); self.word(c+4,h); self.word(c+8,w*4); self.u.mem_write(c+0xc,struct.pack('<HH',2,1)); self.word(c+0x14,self.pixels[url]); ret=0
        elif name=='widget_unload_image': self.unloads+=a==self.top; ret=0
        elif name=='slide_menu_set_spacer': self.word(a+O['SLIDE_SPACER'],b); ret=0
        elif name=='canvas_draw_image':
            src,dst=[tuple(signed(self.get(r+4*i)) for i in range(4)) for r in (c,d)]
            line=self.get(b+8); data=self.get(b+0x14)
            frame=b''.join(bytes(self.u.mem_read(data+(src[1]+y)*line+4*src[0],4*dst[2])) for y in range(dst[3]))  # src's rows
            self.draws.append((a,b,src,dst,struct.unpack_from('<H',bytes(self.u.mem_read(b+0xc,2)))[0],frame)); ret=0
        else: return super().hook(u,address,size,unused)
        self.calls.append((name,a,b,c))
        self.returns(ret,clobber=True)

def host_render(textures,cases,style=None,paper=None,windows=False,still=False):
    """The same renderer source built for the host: frames for (frac, mask of drawn slots) cases. style: a theme's cf.* values; paper: the light half's.
    15 textures make the thumb's wide ring; a case's (kind, lean, a, b) then sets the thumb: 1 letters, the row held as an entry from the row at
    lead a/1000 leaves it, 2 a ms into the fold from that row held at lead b/1000, 3 the entry, lead a/1000 and b/1000 albums to go, 4 letters
    with the row held as the case's next five numbers have it (hold_lead, hold_run, hold_xrun, hold_xs, hold_k).
    windows: also each case's window middle from the album at the middle (cf_frame_window), as a second list.
    still: nothing moving, as the page's own frame at rest (the shelf's seven); else moving, as a direct render is."""
    import subprocess, tempfile
    from coverflow import CF_SOURCES, SHIM_H
    from theme import ROLE_MACROS, builtins
    style=style or {f:v for f,v in builtins()[0].items() if f.startswith('cf.')}  # the theme's, as the payload has it: a thumb theme, its shelf draws seven covers a side
    if len(textures)==7:  # the seven round the centre, the wide ring's ends left out
        textures=[textures[0]]*4+list(textures)+[textures[0]]*4; cases=[(c[0],c[1]<<4,*c[2:]) for c in cases]
    cf=', '.join(f'.{f[3:]} = {v}' for f,v in style.items()); rows=style['cf.view_h']
    main=r"""#include "theme.h"
static theme_t t = { .cf = { %s } };
int client_h(void) { return 290; }
const theme_t *theme(void) { return &t; }
#include <stdio.h>
#undef calloc /* the shim's name for the payload's calloc (corner_cover.c's tables): libc's here */
void *calloc(size_t, size_t);
void *shim_calloc(size_t n, size_t size) { return calloc(n, size); }
void coverflow_render(unsigned *, int, int, const unsigned *const[7]);
int ipod_light(void) { return %d; }
int ipod_square_art(void) { return 0; }
unsigned time_now_ms(void) { return 0; } /* the fold's clock: fold_at counts back from 0 */
int ringnav_big_library(void) { return 0; } /* the MIPS machines' own: Fast Navigation off */
void cf_thumb_lead(float), cf_thumb_lean(int), cf_thumb_fold(int, unsigned), cf_thumb_enter(int, float, float);
void cf_thumb_speed(float, float), cf_thumb_held(float, float, float, float, int);
float cf_thumb_hold(int, float, int);
int cf_frame_window(int);
void cf_shelf_still(int);
const scheme_t *scheme(void) { static scheme_t s = { .paper = %d }; return &s; }
#define N %d
static unsigned tex[N][160 * 160], frame[%d * %d];
int main(void) {
    const unsigned *ring[N];
    cf_shelf_still(%d);
    if (fread(tex, 4, N * 160 * 160, stdin) != N * 160 * 160) return 1;
    int frac, mask, kind, lean, a, b;
    while (scanf("%%d %%d %%d %%d %%d %%d", &frac, &mask, &kind, &lean, &a, &b) == 6) {
        for (int j = 0; j < N; ++j) ring[j] = mask >> j & 1 ? tex[j] : 0;
        float h[4];
        int k;
        if (kind == 4 && scanf("%%f %%f %%f %%f %%d", h, h + 1, h + 2, h + 3, &k) != 5) return 1;
        cf_thumb_enter(0, 0, 0), cf_thumb_lead(-1), cf_thumb_speed(0, 0);
        if (kind == 1 || kind == 2) cf_thumb_hold(lean, (kind == 1 ? a : b) / 1000.f, 0);
        if (kind == 4) cf_thumb_held(h[0], h[1], h[2], h[3], k);
        if (kind == 1 || kind == 4) cf_thumb_lean(lean);
        if (kind == 2) cf_thumb_fold(lean, -(unsigned)a);
        if (kind == 3) cf_thumb_enter(lean, a / 1000.f, b / 1000.f);
        coverflow_render(frame, %d, frac, ring + (N - 7) / 2);
        fwrite(frame, 4, sizeof(frame) / 4, stdout);
        fprintf(stderr, "window %%d\n", cf_frame_window(frac));
        fflush(stdout);
    }
    return 0;
}""" % (cf,paper is not None,paper or 0,len(textures),rows,O['CF_VIEW_W'],int(still),O['CF_VIEW_W'])
    with tempfile.TemporaryDirectory(prefix='q2-render-') as d:
        d=pathlib.Path(d); (d/'shim.h').write_text(SHIM_H); (d/'main.c').write_text(main); (d/'coverflow_all.c').write_text(CF_SOURCES)
        subprocess.run(['cc','-O1','-DPEQ_HOST','-DPEQ_ROOT=""','-D_GNU_SOURCE',
                        '-ffunction-sections','-fdata-sections','-Wl,--gc-sections','-I',str(ROOT/'patch'),'-include',str(d/'shim.h'),
                        str(d/'coverflow_all.c'),str(d/'main.c'),'-o',str(d/'render')],check=True)
        feed=b''.join(textures)+''.join(' '.join('%.9g'%x if isinstance(x,float) else str(x) for x in (*c,0,0,0,0)[:max(6,len(c))])+'\n' for c in cases).encode()
        run=subprocess.run([str(d/'render')],input=feed,capture_output=True,check=True); out=run.stdout
    size=4*O['CF_VIEW_W']*rows
    frames=[out[i*size:(i+1)*size] for i in range(len(cases))]
    return (frames,[int(l.split()[1]) for l in run.stderr.decode().splitlines() if l.startswith('window ')]) if windows else frames

ps=payload_syms
textures=[cover_pixels(100+j) for j in range(7)]
cases=[(0,127),(16384,127),(-16384,127),(32767,127),(-32768,127),(5000,0b0111110),(0,0)]
want=host_render(textures,cases)
m=DepthMachine(); tex=m.big_alloc(7*160*160*4); m.u.mem_write(tex,b''.join(textures))
ring=m.big_alloc(60)+16; frame=m.big_alloc(4*O['CF_VIEW_W']*O['CF_VIEW_H'])  # a thumb theme's ring reaches 4 more each way
for (frac,mask),host in zip(cases,want):
    for k in range(7): m.word(ring+4*k,tex+k*160*160*4 if mask>>k&1 else 0)
    m.call(address=ps['coverflow_render'],args=(frame,O['CF_VIEW_W'],frac,ring),gap=0,count=50_000_000)
    assert bytes(m.u.mem_read(frame,len(host)))==host,(frac,mask)
    passed()

# On the page: the slide_menu spans the frame at CF_STRIDE per album, its children stay empty, and a
# paint of it draws the frame 1:1 at its origin, marked opaque; the covers around the position are
# decoded once, each load dropped at once. What is drawn is what the renderer draws for that ring.
m=DepthMachine(); page=m.open(); s=m.slide
assert len(m.frames)==1 and cf_geometry(m,s)==(0,0,O['CF_VIEW_W'],O['CF_VIEW_H'])
stride=signed(m.get(s+O['SLIDE_SPACER']))+O['CF_VIEW_H']; assert stride==O['CF_STRIDE']
assert all('image' not in m.nodes[c] for c in m.nodes[s]['children'])
def paint(m):
    return m.call(address=HOOKS['widget_on_paint_border'][0],args=(m.slide,m.canvas,0,0),gap=0,clear=False,count=50_000_000)
instructions=[0]
def count_block(u,address,size,unused): instructions[0]+=size//4
counter=m.u.hook_add(UC_HOOK_BLOCK,count_block,begin=BASE,end=SCRATCH-1)
paint(m); rest_cost=instructions[0]
assert len(m.draws)==1 and m.draws[0][1]==m.frames[0] and m.draws[0][2]==m.draws[0][3]==(0,0,O['CF_VIEW_W'],O['CF_VIEW_H'])
assert m.draws[0][4]&1  # BITMAP_FLAG_OPAQUE
assert struct.unpack('<H',bytes(m.u.mem_read(m.frames[0]+0xe,2)))[0]==O['LCD_BGRA8888']  # the screen's format: stock copies it, not blends
# The placeholder first (fx_open), then the ring round album 0 of three, nearest first, which repeats as
# the slide_menu wraps: 0, 1, 2, 0, 1, 2, 0 from -3, each album decoded once.
assert len(m.loads)==4 and m.loads[0]=='default_album_flow' and all(u.startswith('file://') for u in m.loads[1:]) and m.unloads==3
tex_of={u:bytes(m.u.mem_read(m.pixels[u],160*160*4)) for u in m.pixels}
ring_tex=[tex_of[m.loads[1+(i+2)%3]] for i in range(15)]  # its wide ring: album i - 7
assert host_render(ring_tex,[(0,(1<<15)-1)],still=True)[0]==m.draws[0][5]; passed()
# The shelf on the page in both halves: the theme's frame height and stride, drawn 1:1 from the payload as the host
# renders it, on the scheme's paper (the light half's white, light.paper in theme.xml), the dark half's on black.
OLED_CF={k:v for k,v in theme_builtins()[0].items() if k.startswith('cf.')}
for light,paper in (('0',0),('1',0xff)):
    CONFIG['LIGHT']=light
    try: c=DepthMachine()
    finally: del CONFIG['LIGHT']
    c.open(); cs=c.slide; assert cf_geometry(c,cs)==(0,0,O['CF_VIEW_W'],OLED_CF['cf.view_h'])
    assert signed(c.get(cs+O['SLIDE_SPACER']))+OLED_CF['cf.view_h']==OLED_CF['cf.stride']
    paint(c); assert c.draws[0][2]==c.draws[0][3]==(0,0,O['CF_VIEW_W'],OLED_CF['cf.view_h'])
    frame_bytes=c.draws[0][5]; assert frame_bytes[0:3]==bytes([paper])*3
    c_tex={u:bytes(c.u.mem_read(c.pixels[u],160*160*4)) for u in c.pixels}  # its ring of 15 loads nearest first: album 0, 1, 2
    assert host_render([c_tex[c.loads[1+(i+2)%3]] for i in range(15)],[(0,(1<<15)-1)],paper=0xffffff if light=='1' else None,still=True)[0]==frame_bytes
    passed()
# ... and mid-step in the light half, along coverflow.shelf.swing's path: the payload's arithmetic, the host's frames.
cases=[(16384,127),(-16384,127),(26214,127),(32767,127),(-32768,127)]
want=host_render(textures,cases,paper=0xffffff)
tex=c.big_alloc(7*160*160*4); c.u.mem_write(tex,b''.join(textures)); ring=c.big_alloc(60)+16; frame=c.big_alloc(4*O['CF_VIEW_W']*OLED_CF['cf.view_h'])
for k in range(7): c.word(ring+4*k,tex+k*160*160*4)
for (frac,_),host in zip(cases,want):
    c.call(address=ps['coverflow_render'],args=(frame,O['CF_VIEW_W'],frac,ring),gap=0,count=50_000_000)
    assert bytes(c.u.mem_read(frame,len(host)))==host,frac
    passed()
# A non-square cover is fitted whole and centred between black bars, not cropped (#74).
def fitted(px,w,h):
    side=max(w,h); out=[]
    for y in range(160):
        sy=y*side//160-(side-h)//2
        for x in range(160):
            sx=x*side//160-(side-w)//2
            out.append(px[(sy*w+sx)*4:(sy*w+sx)*4+4] if 0<=sx<w and 0<=sy<h else struct.pack('<I',0xff000000))
    return b''.join(out)
for size in ((200,100),(60,150)):
    n=DepthMachine(); n.art_size=size; n.open(); paint(n)
    tex={u:fitted(bytes(n.u.mem_read(n.pixels[u],size[0]*size[1]*4)),*size) for u in n.loads[1:]}
    tex['default_album_flow']=bytes(n.u.mem_read(n.pixels['default_album_flow'],160*160*4))
    ring_tex=[tex[n.loads[1+(i+2)%3]] for i in range(15)]
    assert host_render(ring_tex,[(0,(1<<15)-1)],still=True)[0]==n.draws[0][5],size; passed()
# A repaint in place draws again without rendering; a quarter turn renders from the live offset.
instructions[0]=0; paint(m); assert len(m.draws)==2 and instructions[0]<rest_cost//20
m.word(s+O['SLIDE_OFFSET'],-O['CF_STRIDE']//4); instructions[0]=0; paint(m); turn_cost=instructions[0]
assert m.draws[-1][5]!=m.draws[0][5] and len(m.loads)==4 and turn_cost>rest_cost//2; passed()
m.u.hook_del(counter)
m.word(s+O['SLIDE_OFFSET'],0); paint(m)
# The wheel's centre press clicks the centre child, which opens its tracks.
m.word(s+O['SLIDE_INDEX'],2); m.tracks(2)
assert m.query[:2]==('getMusicByAlbum','Album 2') and not m.nodes[m.get(s+O['W_PARENT'])]['visible']; passed()
# Closing releases the frame and the textures; allocation failure keeps the flat covers.
m.close(); assert m.destroyed==m.frames; passed()
class Starved(DepthMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'')=='bitmap_create_ex':
            self.returns(0); return
        return super().hook(u,address,size,unused)
m=Starved(); m.open(); s=m.slide
assert cf_geometry(m,s)==(0,24,375,160) and all(m.nodes[c].get('image') for c in m.nodes[s]['children'])
paint(m); assert not m.draws; passed()

# The covers held by the thumb (docs/internals.md#coverflow-thumb): each UI pass reads the wheel raw, and nothing it
# did with ticks is lost. test/coverflow.py has the follower's numbers on the host; here, on the real payload, who owns
# the wheel and what guards it. The pages here are the light half's (LIGHT=1): its paper under the covers.
def light_new(cls,**kw):  # a cls machine in the light half: LIGHT is stored before its constructor reads config
    CONFIG['LIGHT']='1'
    try: return cls(**kw)
    finally: del CONFIG['LIGHT']
def thumb_page(cls=None,**kw):
    m=light_new(cls or DepthMachine,albums=9,**kw); m.open(); m.handlers[sleep_hook+12]='stock_sleep'; m.fresh=1
    return m
def tp(m,travel=0,ms=17):  # a UI loop pass ms on, the wheel turned travel raw units since the last
    if travel: m.word(ps['wheel_travel'],travel&0xffffffff)
    m.advance(ms,clear=False)
    if m.fresh: m.word(ps['wheel_frame_at'],m.now)  # as its frames stamp it
    m.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0,clear=False,debounce=True)
def traw(m): return m.get(ps['wheel_raw'])
def thold(m):  # a thumb down, past a stray contact
    m.word(ps['wheel_held'],1); tp(m); tp(m,ms=50)
def tspin(m,ms=600,units=-20):
    for _ in range(0,ms,17): tp(m,units)
def trest(m):
    m.word(ps['wheel_held'],0)
    for _ in range(200): tp(m)
def tindex(m): return m.get(m.slide+O['SLIDE_INDEX'])
def toff(m): return signed(m.get(m.slide+O['SLIDE_OFFSET']))
def tcaption(m): return m.texts()[0]
CF_FIELDS=[f for l in re.findall(r'^\s*short ([^;]+);',re.search(r'typedef struct \{([^}]*)\} cf_style_t;',(ROOT/'patch/theme.h').read_text())[1],re.M) for f in re.split(r',\s*',l)]
def no_letters(m): m.u.mem_write(ps['cfs']+2*CF_FIELDS.index('jump_ticks'),b'\0\0')  # the open page's cfs: jump_ticks 0, the cap alone
CF_DEFINES={k:int(v) for k,v in re.findall(r'^#define (\w+) (\d+)',(ROOT/'patch/coverflow.h').read_text(),re.M)}  # LD_SLOTS, CF_FOLD_MS ...
TH_AT={f:4*i for i,f in enumerate(f for l in re.findall(r'^\s*\w+ ([^;]+);',re.sub(r'/\*.*?\*/','',re.search(r'typedef struct \{([^}]*)\} cf_thumb_t;',(ROOT/'patch/coverflow.h').read_text())[1],flags=re.S),re.M) for f in re.split(r',\s*',l))}  # every field 4 bytes
def tfloat(m,f): return struct.unpack('<f',m.u.mem_read(ps['th']+TH_AT[f],4))[0]
def tpaint(m): m.call(address=HOOKS['widget_on_paint_border'][0],args=(m.slide,m.canvas,0,0),gap=0,clear=False,count=50_000_000,debounce=True)
# Raw on the covers, each pass; a turn writes the offset itself and no tick or animator is about.
# The shelf's texture pool is allocated at the open: going by letters adds the rest (below).
m=DepthMachine(albums=9); m.open(); m.handlers[sleep_hook+12]='stock_sleep'; m.fresh=1
tp(m); assert traw(m)==2 and not m.slides
assert (15+1)*160*160*4 in m.live.values()  # CF_WIDE and the placeholder, whatever Fast Navigation says
thold(m); tp(m,-30); assert toff(m)<0 and not m.slides and m.get(m.slide+O['SLIDE_ANIMATOR'])==0 and not m.get(ps['wheel_travel'])
trest(m); assert toff(m)==0; passed()
# wheel_raw never stays set: each way off the covers gives the wheel back at the next pass, the travel gone, the
# covers landed on what was shown (a finger on the screen lets them run down), and coming back takes it again.
def to_other(m): m.top=m.node('window','home_page')
def to_dialog(m): m.top=m.node('dialog','msginfo_dialog')
def to_np(m): m.top=m.node('window','playing_page')
def thumb_exits():
    def flag(name,on,off): return (lambda m:m.byte(syms[name],on),lambda m:m.byte(syms[name],off))
    back=lambda m: setattr(m,'top',m.page)
    yield 'window',to_other,back; yield 'dialog',to_dialog,back; yield 'now playing',to_np,back
    yield ('dark',)+flag('g_backlight_status',0,1); yield ('locked',)+flag('g_lockscreen_pageflag',1,0)
    yield ('usb',)+flag('g_usblink_status',2,0); yield ('power',)+flag('g_poweroff_state',2,0); yield ('test',)+flag('g_testmode_flag',1,0)
    yield 'sliding',(lambda m:setattr(m,'animating',1)),(lambda m:setattr(m,'animating',0))
    yield 'finger',(lambda m:setattr(m,'pressed',1)),(lambda m:setattr(m,'pressed',0))
for name,off,on in thumb_exits():
    m=thumb_page(); m.scheduling=0; tp(m); assert traw(m)==2,name
    thold(m); tspin(m,300,-30); assert toff(m)<0,name; m.word(ps['wheel_travel'],-99&0xffffffff)
    off(m); tp(m); assert traw(m)==0 and not m.get(ps['wheel_travel']),name
    if name=='finger':
        for _ in range(150): tp(m)
    assert toff(m)==0 and m.get(m.slide+O['SLIDE_ANIMATOR'])==0,name
    index=tindex(m); m.word(ps['wheel_travel'],-99&0xffffffff); tp(m); assert toff(m)==0 and traw(m)==0 and tindex(m)==index,name
    on(m); tp(m); assert traw(m)==2 and not m.get(ps['wheel_travel']) and tindex(m)==index,name; passed()
# Closing the page, Return's way: the wheel's back with it; the album named is the one kept.
m=thumb_page(); tp(m); thold(m); tspin(m,300,-30); shown=tcaption(m); m.close(); m.slide=0; to_other(m); tp(m); assert traw(m)==0
tp(m); assert m.get(ps['wheel_raw'])==0; passed()
# Whoever owns the wheel next keeps it: Vortex's and a card game's flag, left alone once the covers are gone...
m=thumb_page(); tp(m); to_other(m); tp(m); assert traw(m)==0; m.word(ps['wheel_raw'],1)
for _ in range(5): tp(m)
assert traw(m)==1; m.word(ps['wheel_raw'],0); passed()
# ... and Now Playing's lyrics, in the same pass, either way round, each writing what it decides.
m=thumb_page(); tp(m); assert traw(m)==2; page=m.page
m.handlers[playing+12]='stock_playing'; scrub_page(m=m); m.lyric_size=9; m.fail_calloc=True
rows=[m.node('label') for _ in ROWS]
for i,r in enumerate(rows): m.word(r+O['W_Y'],sum(ROWS[:i])); m.word(r+O['W_H'],ROWS[i])
m.lrc=m.node('scroll_view','scroll_lrc',rows); m.word(m.lrc+O['W_H'],178); m.word(m.lrc+O['VIEW_CONTENT_H'],sum(ROWS))
m.np=m.node('slide_view','slide_view',[m.lrc],value=1); m.nodes[m.win]['children'].append(m.np)
assert m.call(address=playing,args=(m.win,7,0,0),gap=0)==0
tp(m); assert traw(m)==1,traw(m)  # the lyrics'
for _ in range(3): tp(m)
assert traw(m)==1
m.top=page; tp(m); tp(m); assert traw(m)==2  # back, past the pass where lyric_poll lets go
for _ in range(3): tp(m)
assert traw(m)==2; m.top=m.win; tp(m); assert traw(m)==1; passed()
# Stock's lockout after Centre or Return keeps its first two tenths, as ringnav does for ticks; Return's stop;
# no frame in WHEEL_FRESH_MS: their travel moves nothing, and is gone.
for gate,clear in ((lambda m:m.byte(O['KEY_LOCKOUT'],8),lambda m:m.byte(O['KEY_LOCKOUT'],8-O['LOCKOUT_KEEP'])),
                   (lambda m:m.word(ps['wheel_mute'],1),lambda m:m.word(ps['wheel_mute'],0)),
                   (lambda m:setattr(m,'fresh',0) or m.word(ps['wheel_frame_at'],m.now-O['WHEEL_FRESH_MS']),lambda m:setattr(m,'fresh',1))):
    m=thumb_page(); tp(m); thold(m); gate(m); tp(m,-50); assert toff(m)==0 and not m.get(ps['wheel_travel']) and traw(m)==2
    clear(m); tp(m,-50); tp(m); assert toff(m)<0; passed()  # a pass on: one pass's burst is held to the cap
# A turn times the idle loop and the screen itself, and clicks once an album; the thumb holding still doesn't keep
# the screen on. 60 passes of 2 units at 60 ms (one taken by the dead band) are 119 x 1800 / 10000: 21 ticks, each
# 180 ms or so from the last, past WA_RUN_MS, so an album each (the lists' momentum, wheel_accel.c).
m=thumb_page(); tp(m); thold(m); m.calls=[]
for _ in range(20*3): tp(m,-2,ms=60)
assert len(did(m,'reset_poweroptions_timer'))>=20*3-2 and len(did(m,'buzzeer_switch'))==21,len(did(m,'buzzeer_switch'))
m.calls=[]
for _ in range(10): tp(m)
assert not did(m,'reset_poweroptions_timer') and not did(m,'buzzeer_switch'); passed()
# chord_gate: Centre under a held Return is dropped whole while the wheel's raw, the covers not landed for it.
def twake(m,kind,key=O['KEY_CENTER'],gap=30):
    inp=HOOKS['window_manager_dispatch_input_event'][0]; m.handlers[inp+12]='stock_input'; n=len(m.calls)
    assert m.call(key,address=inp,args=(m.wm,m.event,0,0),event_type=kind,gap=gap,clear=False,debounce=True)==0
    return any(c[0]=='stock_input' for c in m.calls[n:])
m=thumb_page(); tp(m); thold(m); tspin(m,300,-30); off=toff(m)
assert twake(m,O['EVT_KEY_DOWN'],O['KEY_RETURN']) and not twake(m,O['EVT_KEY_DOWN']) and toff(m)==off and m.top==m.page; passed()
# hold_wheel: ticks while the page slides in wait, raw off, and play in as a step; the turn going on takes over from
# that step, its goal kept. A tick the queue still held as the wheel went raw goes nowhere.
m=thumb_page(); m.animating=1; tp(m); assert traw(m)==0 and m.call(gap=100)==11 and m.call(gap=20)==11 and not m.slides and toff(m)==0
m.animating=0; m.advance(40); a=m.get(m.slide+O['SLIDE_ANIMATOR']); assert a and signed(m.get(a+O['ANIM_X_TO']))<0
tp(m,ms=1); assert traw(m)==2 and m.get(m.slide+O['SLIDE_ANIMATOR'])==a
held=signed(m.get(a+O['ANIM_X_TO'])); thold(m)
m.u.mem_write(m.slide+O['SLIDE_OFFSET'],struct.pack('<i',-20)); tp(m,-10)
assert m.get(m.slide+O['SLIDE_ANIMATOR'])==0 and did(m,'widget_animator_destroy') and -held//O['CF_STRIDE']>=0 and not m.slides
off=toff(m); assert m.call(gap=0)==11 and m.get(m.slide+O['SLIDE_ANIMATOR'])==0 and toff(m)==off
trest(m); assert toff(m)==0; passed()
# The arch's cost, in instructions: fully formed, within a mid-turn frame's ceiling. What a frame costs on the device is measured
# with the --dev gaps; this keeps a change from quietly doubling it.
m=thumb_page(); n=[0]; h=m.u.hook_add(UC_HOOK_BLOCK,lambda u,a,size,x: n.__setitem__(0,n[0]+size//4),begin=BASE,end=SCRATCH-1)
tp(m); tpaint(m); assert m.draws; m.word(m.slide+O['SLIDE_OFFSET'],-O['CF_STRIDE']//4); n[0]=0
m.word(m.slide+O['SLIDE_ANIMATOR'],1)  # a step's frame, the covers moving (no animator is ever called by a paint)
tpaint(m); turn_cost=n[0]; m.word(m.slide+O['SLIDE_ANIMATOR'],0); m.word(m.slide+O['SLIDE_OFFSET'],0); thold(m); no_letters(m); tspin(m,600,-110); assert struct.unpack('<f',m.u.mem_read(ps['th']+TH_AT['arch'],4))[0]>.9  # th.arch: formed (Fast Navigation off)
n[0]=0; draws=len(m.draws); tpaint(m); row_cost=n[0]; m.u.hook_del(h); assert len(m.draws)==draws+1 and turn_cost>1000000 and row_cost<6_450_000,row_cost; passed()  # 6.02 million measured, the facing cover's 123 px columns over its neighbours': within the ceiling a turn's frame has below
assert turn_cost<6_450_000,turn_cost  # a frame a quarter turn on, the covers moving, a little over the 6.38 million it measured: a dearer one fails
# The release build carries no dev-only code (the gap log's).
class HalfMachine(FileMocks,DepthMachine):
    def __init__(self,**kw): super().__init__(**kw); self.file_mocks('h:'); self.opened=[]; self.cut=False
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('h:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; self.calls.append((name,a,b,c))
        if name=='fopen' and self.text(a).endswith('.jpg.half') and self.text(b)=='rb':  # each album's own copy
            path=self.text(a); self.opened.append(path)
            self.files.setdefault(path,cover_pixels(300+len(self.files),80*80)[:100 if self.cut else None])
        self.returns(self.file_op(u,name,a,b,c,d),clobber=True)
assert ('gaps' in payload_syms)==bool(manifest['dev'])  # the gap log
# A thread lowers itself (thread_lower(idle)): nice 19 through setpriority (o32 4097) on its own tid (gettid, 4222), and its
# reads, with idle (the loader, its first call), in the idle I/O class (ioprio_set, 4314, IOPRIO_WHO_PROCESS), best
# effort's lowest level when the kernel refuses idle; without (playlist art's worker, which holds stock's art locks) in
# best effort's lowest level alone. It returns what took (1: nice 19, plus the class << 1). Playlist art's worker does so
# first, once, so its reads and the ffmpeg runs it forks (which inherit both) stay below the player and the UI.
lower=payload_syms['thread_lower']
for address,idle,refused in ((lower,1,False),(lower,1,True),(lower,0,False),(lower,0,True),(payload_syms['pl_worker'],0,False)):
    m=Machine(); sc=[]
    def syscall(u,intno,unused,sc=sc,refused=refused,idle=idle):
        n,a,b,c=(u.reg_read(r) for r in (UC_MIPS_REG_V0,*REGS[:3])); sc.append((n,a,b,c)); bad=n==4314 and refused and (c==3<<13 or not idle)
        u.reg_write(UC_MIPS_REG_A3,int(bad)); u.reg_write(UC_MIPS_REG_V0,1 if bad else 321 if n==4222 else 0)
    m.u.hook_add(UC_HOOK_INTR,syscall)
    took=m.call(address=address,args=(idle,0,0,0),gap=0)
    tries=[(4314,1,321,3<<13)]*idle+[(4314,1,321,2<<13|7)]*(not idle or refused)
    assert [x[0] for x in sc[:1]]==[4222] and sc[1:2+len(tries)]==[(4097,0,321,19)]+tries,sc
    if address==lower: assert took==1|(0 if refused and not idle else 2 if refused or not idle else 3)<<1,took
    passed()

# Playlist art's SQLite stays off the UI thread: a rebuild's refresh only asks the worker (a thread, no sqlite3_open or
# _exec here), and an ask before its first read lands answers none and opens nothing. USB Storage joins that worker
# before stock's page takes the card, and none starts again while its page is there. Stock's lazy open of its shared connection (0x5c8d30) calls stock_db_open from
# 0x5c8d64, which sets the busy timeout on the connection it opened, so stock's writers wait out the worker's read.
class PlArtMachine(Machine):
    def __init__(self):
        super().__init__(); self.sql=[]; self.threads=0; self.order=[]; self.usb=0
        self.mock('mkdir@GLIBC_2.0')
        for n in ('sqlite3_open','sqlite3_exec','sqlite3_close','pthread_join@GLIBC_2.0','pthread_create@GLIBC_2.2','navigator_window_is_exist'): self.handlers[syms[n]]='pa:'+n
        self.handlers[int(manifest['patch_symbols']['stock_usbmode_trampoline'],16)]='pa:usb'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('pa:'): return super().hook(u,address,size,unused)
        a,b,c,d=[u.reg_read(r) for r in REGS]; name=name[3:]
        if name=='sqlite3_open': self.sql.append(('open',self.text(a))); self.word(b,0xdb)
        elif name=='sqlite3_exec': self.sql.append(('exec',a,self.text(b)))
        elif name.startswith('pthread_create'): self.threads+=1; self.word(a,0x77)
        elif name.startswith('pthread_join'): self.order.append(('join',a))
        elif name=='usb': self.order.append(('stock',self.get(payload_syms['pl']+20)))  # pl.running
        elif name=='navigator_window_is_exist': assert self.text(a)=='usbmode_page'; self.returns(self.usb); return
        self.returns(0)
m=PlArtMachine(); url=m.alloc(600)
m.call(address=payload_syms['playlist_art_refresh'],args=(0,0,0,0),gap=0)
assert m.call(address=payload_syms['playlist_art'],args=(3,m.string('Mix'),1,url),stack=(600,),gap=0)==0
assert not m.sql and m.threads==1 and m.get(payload_syms['pl']+20)==1; passed()
m.call(address=HOOKS['usbmode_page_init'][0],args=(0,0,0,0),gap=0)
assert m.order==[('join',0x77),('stock',0)],m.order
m.usb=1; m.call(address=payload_syms['playlist_art_refresh'],args=(0,0,0,0),gap=0)
assert m.call(address=payload_syms['playlist_art'],args=(3,m.string('Mix'),1,url),stack=(600,),gap=0)==0
assert m.threads==1 and m.get(payload_syms['pl']+20)==0; passed()
from build import fileoff, STOCK_DB_OPEN
demo=(B/'demo').read_bytes(); assert STOCK_DB_OPEN[0]==0x5c8d64
assert struct.unpack_from('<I',demo,fileoff(demo,0x5c8d64))[0]==jal(payload_syms['stock_db_open'])
m=PlArtMachine(); m.u.reg_write(UC_MIPS_REG_GP,0xa3aae0)
assert m.call(address=0x5c8d30,args=(m.string('/mnt/data/database.db'),0,0,0),gap=0)==1
# stock's own pragma (since V1.36) on the connection it has just opened, after the hook's
assert m.sql==[('open','/mnt/data/database.db'),('exec',0xdb,'PRAGMA busy_timeout=500'),('exec',0xdb,'PRAGMA auto_vacuum=INCREMENTAL;')],m.sql; passed()

# Centre opens the album the caption names, at once, however far ahead of the covers that is (letters off).
m=thumb_page(); tp(m); thold(m); no_letters(m); tspin(m,600,-110); shown=tcaption(m); assert toff(m)!=0
assert m.confirm()==11 and toff(m)==0 and tcaption(m)==shown
index=tindex(m); kids=m.nodes[m.slide]['children']; assert shown==f'Album {index}' and m.dispatched()[-1][1]==kids[index%len(kids)]; passed()  # this machine's slide holds 8 nodes for its 9 albums

# The duration cache's file (DUR_CACHE): keys, a cache's bytes from (path, seconds) pairs and back, and the duration
# worker's syscalls, for the duration blocks further on (RateMachine's lengths among them).
DUR_CACHE='/mnt/data/ringnav-durations-v1'
def dur_key(p):
    h=0xcbf29ce484222325
    for c in p.encode(): h=(h^c)*0x100000001b3&(1<<64)-1
    return h
def duration_cache(*pairs):
    return b'Q2DUR2\0\0'+b''.join(struct.pack('<Q3Ii',k,1000,2000,0,v) for k,v in sorted((dur_key(p),v) for p,v in pairs))
def duration_pairs(data,paths):
    assert data[:8]==b'Q2DUR2\0\0'
    keys={k:sec for k,_,_,_,sec in struct.iter_unpack('<Q3Ii',data[8:])}
    return {p:keys[dur_key(p)] for p in paths if dur_key(p) in keys}
def duration_syscalls(m):
    """The duration worker's syscalls: thread_lower's, answered 0, and ringnav_statx's (4366), a regular file of
    1000 bytes at mtime 2000 (struct statx: stx_mode at 28, stx_size 40, stx_mtime 112), its buffer the fifth argument."""
    def syscall(u,intno,unused):
        if u.reg_read(UC_MIPS_REG_V0)==4366:
            buf=m.get(u.reg_read(UC_MIPS_REG_SP)+16); u.mem_write(buf,bytes(256))
            m.word(buf+28,0o100644); m.word(buf+40,1000); m.word(buf+112,2000)
        u.reg_write(UC_MIPS_REG_A3,0); u.reg_write(UC_MIPS_REG_V0,0)
    m.u.hook_add(UC_HOOK_INTR,syscall)

print(f'Coverflow depth on MIPS: {rest_cost} instructions for the first paint (decoding the covers in reach, then the frame), {turn_cost} for a frame a quarter turn on (payload only; stock drawing mocked)')

# Home's Split pan (DRIFT_*): the cover is box filtered once to its fit zoomed DRIFT_ZOOM_PCT, sharpened
# [-1,6,-1]/4 a side (home_sharpen) and bordered by its edges, and each paint of the art (its own
# image empty) draws the panel from that at the pan's 1/256 px position, cubic B-spline, so it
# moves by fractions of a pixel at a sharpness that hardly changes with the phase. Each axis is a
# linear ping-pong of its own period across the zoom's margin, from the unzoomed crop. One timer
# runs it while Home is on top, lit and still; a step repaints only the panel.
PX=OLED_G.split_art; PW,CH=375-PX,320-OLED_G.bar; PH=CH+OLED_G.split_art_up  # OLED's pane: its rows under the row's foot and its rows under the row (pane_h), all on Home's full-bleed window
X,Y,TICK=O['DRIFT_X_MS'],O['DRIFT_Y_MS'],O['DRIFT_TICK_MS']
m=DepthMachine(); pan_counter=m.u.hook_add(UC_HOOK_BLOCK,count_block,begin=BASE,end=SCRATCH-1)  # before any code is cached
m.budget=80_000_000  # filtering and sharpening a 2112 px cover passes 50 million
m.open(); m.top=m.home; m.word(m.home+O['W_PARENT'],m.wm); np=m.node('window','playing_page')
m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(0xa50084,2)  # playing: the pane shows the playing cover (art_source)
m.byte(syms['g_playcover_type'],1); m.u.mem_write(syms['g_lastcover_url'],b'/p/A\0')
def paint_home(): m.call(address=HOOKS['widget_on_paint_background'][0],args=(m.home,m.canvas,0,0),gap=0)
def drifts(): return [t for t in m.timers.values() if t[1]==payload_syms['home_drift']]
def invalidated(): return [(c[1],c[2] and tuple(signed(m.get(c[2]+4*i)) for i in range(4))) for c in m.calls if c[0]=='widget_invalidate_force']
def tex_of(w,h):  # the last cover loaded (opaque) as slide_load leaves it, and its zoomed size
    url,=m.pixels; px=struct.unpack(f'<{w*h}I',bytes(m.u.mem_read(m.pixels[url],w*h*4)))
    fw,fh=(PW,-(-PW*h//w)) if PW*h>PH*w else (-(-PH*w//h),PH); tw,th=fw*O['DRIFT_ZOOM_PCT']//100,fh*O['DRIFT_ZOOM_PCT']//100
    out=[]
    for y in range(th):
        y0=y*h//th; y1=max((y+1)*h//th,y0+1)
        for x in range(tw):
            x0=x*w//tw; x1=max((x+1)*w//tw,x0+1); n=r=g=b=0
            for sy in range(y0,y1,(y1-y0+3)//4):  # at most 4 x 4 samples
                for p in px[sy*w+x0:sy*w+x1:(x1-x0+3)//4]: r+=p&255; g+=p>>8&255; b+=p>>16&255; n+=1
            out.append(0xff000000|r//n|g//n<<8|b//n<<16)
    def sharpen(line):  # home_sharpen: [-1,6,-1]/4 a channel, rounded and clamped, the ends their own neighbours
        return [0xff000000|sum(min(max((6*q-p-n_+2)>>2,0),255)<<s for s,p,q,n_ in ((s,a>>s&255,b>>s&255,c_>>s&255) for s in (0,8,16)))
                for a,b,c_ in zip([line[0]]+line[:-1],line,line[1:]+[line[-1]])]
    rows=[sharpen(out[y*tw:(y+1)*tw]) for y in range(th)]  # across, then down
    cols=[sharpen([rows[y][x] for y in range(th)]) for x in range(tw)]
    rows=[[c_[y] for c_ in cols[:1]+cols+cols[-1:]*2] for y in range(th)]  # the edges a texel left and above, two right and below
    rows=rows[:1]+rows+rows[-1:]*2
    (u0,rx),(v0,ry)=drift_axis(fw,tw,PW),drift_axis(fh,th,PH)
    return dict(px=[p for r in rows for p in r],ts=tw+3,u0=u0,v0=v0,rx=rx,ry=ry)
def drift_axis(f,t,p):  # drift_axis: from the old crop across the zoom's margin, split_inset percent of t clear at each edge
    ins=t*OLED_G.split_inset//100; lo=max((f-p)//2,ins); hi=min((f-p)//2+t-f,t-p-ins)
    if hi<lo: lo=hi=(t-p)//2
    return lo,hi-lo
def at(r,period,t):  # drift_at: smoothstep ping-pong, s in 1/65536
    t%=2*period; s=((t if t<period else 2*period-t)<<16)//period; return r*256*((s*s*(3*65536-2*s))>>24)>>24
def bspline(t):  # bspline_w: the cubic B-spline's 4 weights at phase t/256, in 1/256
    s=256-t; w0=(s**3+196608)//393216; w3=(t**3+196608)//393216; w1=(3*t**3+4*16777216-6*256*t*t+196608)//393216
    return w0,w1,256-w0-w1-w3,w3
def tap4(a,b,c_,d,w):  # tap4: 4 texels weighted, rounded
    return (0xff000000|((a&0xff00ff)*w[0]+(b&0xff00ff)*w[1]+(c_&0xff00ff)*w[2]+(d&0xff00ff)*w[3]+0x800080)>>8&0xff00ff
            |((a&0xff00)*w[0]+(b&0xff00)*w[1]+(c_&0xff00)*w[2]+(d&0xff00)*w[3]+0x8000)>>8&0xff00)
def framed(c,t):  # the panel at phase t, as home_render filters it: across each cover row, then down
    u,v=c['u0']*256+at(c['rx'],X,t),c['v0']*256+at(c['ry'],Y,t); ts,px=c['ts'],c['px']
    wx,wy=bspline(u&255),bspline(v&255); i=(v>>8)*ts+(u>>8)  # texel -1, -1 from u, v
    rows=[[tap4(*px[j+x:j+x+4],wx) for x in range(PW)] for j in range(i,i+(PH+3)*ts,ts)]
    out=[vig(tap4(*k,wy),VIG(y)) for y in range(PH) for k in zip(*rows[y:y+4])]
    return struct.pack(f'<{len(out)}I',*out)  # every row: Home's window is the whole screen (home_paint)
# Split's vignette (coverflow.c vig_k, vig): the frame's rows darkened at its top and bottom edges, in 256ths, by the
# tables the C holds (read from the source, which states how they were made); the frame's row y is the screen's y + 320 - PH.
VIG_T={n:[int(v) for v in re.search(rf'{n}\[VIG_\w+\] = {{([^}}]*)}}',(ROOT/'patch/coverflow.c').read_text()).group(1).split(',')]
       for n in ('vig_top','vig_bot')}
def VIG(y):
    y+=320-PH; return VIG_T['vig_top'][max(y,0)] if y<len(VIG_T['vig_top']) else VIG_T['vig_bot'][319-y] if y>=320-len(VIG_T['vig_bot']) else 256
def vig(c,k): return c if k>=256 else c&0xff000000|((c&0xff00ff)*k>>8&0xff00ff)|((c&0xff00)*k>>8&0xff00)
def drawn():
    n=len(m.draws); m.call(address=HOOKS['widget_on_paint_border'][0],args=(m.art,m.canvas,0,0),gap=0)
    assert len(m.draws)==n+1 and m.draws[-1][3]==(0,0,PW,PH) and m.draws[-1][4]&1,m.draws[n:]
    return m.draws[-1][5]
# 352 square: fits the pane's PH (320) and zooms to 352, a texel a pixel; 704 square: each texel 2 x 2 averaged;
# 2112 square: 6 x 6 sampled 3 x 3.
m.art_size=(352,352); m.pixels.clear(); paint_home(); c=tex_of(352,352)
assert m.nodes[m.art]['image']=='' and [signed(m.get(m.art+O[k])) for k in ('W_X','W_Y','W_W','W_H')]==[PX,0,PW,PH]
INS=352*OLED_G.split_inset//100; assert 0<INS<16  # the inset leaves the 32 texel margin some pan
assert (c['u0'],c['v0'],c['rx'],c['ry'])==((PH-PW)//2,INS,32,32-2*INS) and not drifts()
assert drawn()==framed(c,0) and ('bitmap_lock_buffer_for_write',) in [x[:1] for x in m.calls]
assert drawn()==framed(c,0) and ('bitmap_lock_buffer_for_write',) not in [x[:1] for x in m.calls]  # unmoved: not rendered again
paint_home(); m.advance(0); assert len(drifts())==1  # it arms on the next paint
m.calls.clear(); m.advance(TICK); assert set(invalidated())=={(m.home,(PX,0,PW,PH))},invalidated()
assert drawn()==framed(c,TICK)!=framed(c,0)  # a tick moves it by a fraction of a pixel
m.advance(1000-TICK); assert drawn()==framed(c,1000) and len(drifts())==1; passed()
m.advance(TICK); instructions[0]=0; assert drawn()==framed(c,1000+TICK); frame_cost=instructions[0]
# The bar's rows over the pane (home_ink), as a step leaves them: the ground, and on it a pause's two bars and a
# battery in white, IH rows (OLED's split_art_up). Each glyph comes out in one ink over the art (white over this dark art), every other pixel the art.
IH=58; ground=0xff000000; glyph={(x,y) for y in range(20,38) for x in (*range(100,104),*range(108,112),*range(140,170))}
ink_fb,ink_art,ink_held=m.alloc(PW*IH*4),m.alloc(PW*IH*4),m.alloc(PW)
art_px=[0xff000000|(x*7+y*3)%48*0x010101 for y in range(IH) for x in range(PW)]
m.u.mem_write(ink_fb,struct.pack(f'<{PW*IH}I',*(0xffffffff if (x,y) in glyph else ground for y in range(IH) for x in range(PW))))
m.u.mem_write(ink_art,struct.pack(f'<{PW*IH}I',*art_px)); m.u.mem_write(ink_held,bytes(PW))
instructions[0]=0; m.call(address=payload_syms['home_ink'],args=(ink_fb,PW,ink_art,PW),stack=(PW,IH,ground,ink_held,0),gap=0); ink_cost=instructions[0]
bgra=lambda p:p&0xff00ff00|(p&255)<<16|p>>16&255
assert struct.unpack(f'<{PW*IH}I',bytes(m.u.mem_read(ink_fb,PW*IH*4)))==tuple(0xffffffff if (x,y) in glyph else bgra(art_px[y*PW+x]) for y in range(IH) for x in range(PW)); passed()
# A stalled loop steps at most DRIFT_MAX_STEP_MS.
tid=next(t for t,v in m.timers.items() if v[1]==payload_syms['home_drift']); m.timers[tid]=(m.now+5000,*m.timers[tid][1:])
m.advance(5000); t=1000+TICK+O['DRIFT_MAX_STEP_MS']; assert drawn()==framed(c,t); passed()
# Another window on top, the screen off, the lock screen or a slide: the next step removes the
# timer and nothing repaints until Home paints again, resuming at the same phase with one timer.
for cover_up in (lambda: setattr(m,'top',np),lambda: m.byte(syms['g_backlight_status'],0),
                 lambda: m.byte(syms['g_lockscreen_pageflag'],1),lambda: setattr(m,'animating',1)):
    cover_up(); m.advance(TICK); assert not drifts()
    m.calls.clear(); m.advance(2000); assert not invalidated()
    paint_home(); assert not drifts()  # still not showing
    m.top=m.home; m.byte(syms['g_backlight_status'],1); m.byte(syms['g_lockscreen_pageflag'],0); m.animating=0; added=0
    for _ in range(5): m.calls.clear(); paint_home(); added+=sum(x[:2]==('timer_add',payload_syms['home_drift']) for x in m.calls)
    assert len(drifts())==1 and added==1 and drawn()==framed(c,t)
    m.advance(400); t+=400; assert drawn()==framed(c,t)
passed()
# Layout (Home changed, a row coming or going) drops the timer and reloads the cover; a new cover keeps the phase,
# and a large one is box filtered.
m.call(address=payload_syms['coverflow_home_layout'],args=(0,0,0,0),gap=0); assert not drifts()
paint_home(); assert drawn()==framed(c,t); paint_home(); assert len(drifts())==1
# A new cover fades in over the last (SLIDE_FADE_MS), both panning on at the same phase, the incoming laid over the
# outgoing by its share, the timer stepping every SLIDE_TICK_MS while it runs; then it shows alone, at DRIFT_TICK_MS.
FADE,FTICK=O['SLIDE_FADE_MS'],O['SLIDE_TICK_MS']
fade_cost=[]  # a fade frame's instructions, its two covers rendered
def blended(a,b,k):  # lerp: tap4 of the two by 256 - k and k
    a,b=struct.unpack(f'<{len(a)//4}I',a),struct.unpack(f'<{len(b)//4}I',b)
    return struct.pack(f'<{len(a)}I',*(tap4(x,y,0,0,(256-k,k,0,0)) for x,y in zip(a,b)))
def faded(old,new):
    global t
    assert drawn()==blended(framed(old,t),framed(new,t),1)  # a 256th in
    m.advance(TICK); t+=TICK; f=1+TICK
    instructions[0]=0; got=drawn(); fade_cost.append(instructions[0])
    assert got==blended(framed(old,t),framed(new,t),f*256//FADE)
    while f<FADE: m.advance(FTICK); t+=FTICK; f+=FTICK
    assert drawn()==framed(new,t) and len(drifts())==1
    assert next(v[3] for v in m.timers.values() if v[1]==payload_syms['home_drift'])==TICK  # the pan's period again
m.art_size=(704,704); m.pixels.clear(); m.byte(syms['g_playcover_type'],2)
instructions[0]=0; paint_home(); filter_cost=instructions[0]; old,c=c,tex_of(704,704)
faded(old,c)
m.art_size=(2112,2112); m.pixels.clear(); m.byte(syms['g_playcover_type'],1)
instructions[0]=0; paint_home(); big_cost=instructions[0]; m.u.hook_del(pan_counter); old,c=c,tex_of(2112,2112); faded(old,c); passed()
# A player cover too long for DRIFT_TEXELS stays still, even with a Coverflow thumbnail that
# could pan; neither arms the timer.
m.art_sizes={'externpic.jpg':(4000,100)}; m.byte(syms['g_playcover_type'],2); m.advance(TICK); paint_home(); m.advance(TICK); paint_home()
assert not drifts() and m.nodes[m.art]['image'].endswith('externpic.jpg') and m.get(m.art+O['W_W'])==CH*40; passed()
print(f'Home pan on MIPS: {frame_cost} instructions a frame (every DRIFT_TICK_MS), {ink_cost} to ink the bar\'s {PW} x {IH} rows over the pane, {fade_cost[0]} a cross-fade frame (every SLIDE_TICK_MS), {filter_cost} and {big_cost} to filter a 704 and a 2112 px cover once (payload only)')

# Accent (docs/internals.md#accent). The mapping: stock red blended with a neutral becomes the same
# blend of the preset's red tone, alpha kept, under every preset, Crimson's too; greys, other hues and the
# accents' own colors (but Crimson's, which is stock's red) stay.
ps=payload_syms; RED=(0xff,0x14,0x48)
# WCAG contrast of each preset: white text on the bar's top, the progress fill on its track, and
# white on the red tone (Graphite's silver is chosen for its lit look instead; Crimson is stock).
def lum(c):
    v=[(c>>s&255)/255 for s in (16,8,0)]; v=[x/12.92 if x<=0.04045 else ((x+0.055)/1.055)**2.4 for x in v]
    return 0.2126*v[0]+0.7152*v[1]+0.0722*v[2]
def ratio(a,b): return (max(lum(a),lum(b))+0.05)/(min(lum(a),lum(b))+0.05)
for i,(top,bottom,light,tone,_) in enumerate(ACCENTS):
    assert ratio(0xffffff,top)>=4.5 and ratio(0xffffff,bottom)>=4.5 and ratio(light,O['TRACK_COLOR'])>=3,i
    assert i in (0,O['CRIMSON'],O['MINIMAL_ACCENT']) or ratio(0xffffff,tone)>=3,i
assert ACCENTS[0][:2] == (0x424242, 0x424242)
assert all(top==bottom for top,bottom,*_ in ACCENTS)
assert ratio(O['NP_ARTIST_RGB'], 0) > ratio(0xaaaaaa, 0) >= 7
passed()
def mapped(c,preset,tone=3): return Machine().call(address=ps['accent_map'],args=(c,preset,tone,0),gap=0)&0xffffffff
def rgba(r,g,b,a=255): return r|g<<8|b<<16|a<<24
def blend(t,k,to,a=255): return rgba(*(round(t*x+k) for x in to),a)
def close(x,y): return x>>24==y>>24 and all(abs((x>>s&255)-(y>>s&255))<=2 for s in (0,8,16))
# OLED's own schemes, after the ACCENTS rows: Blue and Arterial in all five tones (assets/themes/oled/theme.xml oled-blue,
# oled-arterial)
OLED_ROWS=((len(ACCENTS),(0x2160d8,)*5),(len(ACCENTS)+1,(0xb8141c,)*5))
for (preset,accent),column in ((p,c) for p in (*enumerate(ACCENTS),*OLED_ROWS) for c in (2,3)):  # light and red tones
    hi=(accent[column]>>16,accent[column]>>8&255,accent[column]&255)
    for c in (rgba(0,0,0),rgba(255,255,255),rgba(0x80,0x80,0x80),rgba(0x2b,0x2b,0x2b,0x40),  # greys
              rgba(0xff,0x9f,0x0a),rgba(0x16,0x9a,0xa6),rgba(0xff,0,0xff),rgba(0x0a,0x84,0xff),  # other hues
              *(color_t(c) for a in ACCENTS for c in a if a is not ACCENTS[1])):  # accents never map again
        assert mapped(c,preset,column)==c,(preset,hex(c))
    table=[(rgba(*RED),blend(1,0,hi)),                         # pure red
           (rgba(*RED,0x40),blend(1,0,hi,0x40)),               # translucent #FF144840: alpha kept
           (rgba(0x7f,0x0a,0x24),blend(0.5,0,hi)),             # red on black, half coverage
           (rgba(*(round((x+255)/2) for x in RED)),blend(0.5,127.5,hi)),  # anti-aliased onto white
           (rgba(0x3d,0x19,0x20),blend(0.153,21.6,hi))]        # the pressed tint
    for c,want in table:
        got=mapped(c,preset,column)
        assert close(got,want),(preset,hex(c),hex(got),hex(want))
        # mapped once: no longer stock's red, except under Crimson and Arterial (its ACCENTS row and OLED's own), whose
        # tones are reds near it and map again
        if preset not in (O['CRIMSON'],len(ACCENTS)-1,len(ACCENTS)+1): assert mapped(got,preset,column)==got
    passed()

# Settings: IPOD/ACCENT, HOME and BATTERY come from the stock config.ini once, a missing or bad value
# is the default; the style color hook maps stock's red to OLED's accent and leaves other colors alone.
red=color_t(0xff1448); grey=color_t(0x808080)
tramp={n:int(manifest['patch_symbols'][f'stock_{n}_trampoline'],16) for n in ('color','image','display')}
def style_color(m,stock,name='text_color'):
    m.handlers[tramp['color']]='stock_color'; out=m.alloc(4); m.stock_color=stock
    return m.call(address=HOOKS['style_get_color'][0],args=(out,0x1234,m.string(name),0),gap=0)&0xffffffff,m.get(out),out
orig_hook=Machine.hook; GET=0x1000600  # a style vtable's get_gradient
def hook(self,u,address,size,x):
    if self.handlers.get(address)=='stock_color':
        a=u.reg_read(UC_MIPS_REG_A0); self.word(a,self.stock_color); self.calls.append(('stock_color',a))
        self.returns(a); return
    if address==GET and getattr(self,'get_gradient',None): return self.get_gradient(u)
    return orig_hook(self,u,address,size,x)
Machine.hook=hook
# OLED's accent is its own key, OLED_ACCENT, whose values are Blue and Arterial, its schemes after the ten ACCENTS rows
# (oled/theme.xml oled-blue, oled-arterial; the option's values mask 0xc00), Arterial the default. Any stored value
# OLED does not offer reads as Arterial (9 too, the ACCENTS row Arterial), the payload's ACCENT and THEME included. Its
# five tones are one colour, so text and every other color property take it.
OLED=theme_builtins()[0]; DEF_ROW=len(ACCENTS)+1; DEF=OLED['schemes'][DEF_ROW]
assert DEF['name']=='oled-arterial' and theme_keys(theme_builtins())['OLED_ACCENT'][1]==DEF_ROW
OLED_BLUE_ROW=len(ACCENTS); assert OLED['schemes'][OLED_BLUE_ROW]['name']=='oled-blue'
DEF_TONES=DEF['tones']; assert len(set(DEF_TONES))==1
for config in ({},{'OLED_ACCENT':'9'},{'OLED_ACCENT':'0'},{'OLED_ACCENT':'1'},{'OLED_ACCENT':'12'},{'ACCENT':'1'},
               {'ACCENT':'3','HOME':'1'},{'THEME':'1'},{'THEME':'0','OLED_ACCENT':'4'}):
    m=Machine(); m.config=config
    ret,got,out=style_color(m,red)
    assert ret==out and got==color_t(DEF_TONES[3]),(config,hex(got))
    for name in ('highlight_text_color','bg_color','fg_color','border_color','selected_fg_color'):
        want=DEF_TONES[3 if name.endswith('text_color') else 2]
        assert style_color(m,red,name)[1]==color_t(want),(config,name)
    assert style_color(m,grey)[1]==grey
    assert len(m.config_reads)==CONFIG_READS  # every key, once, on first use
    passed()
# Real paint order, no persistent widget styles: the selected row's text takes the scheme's sel_text (white) over
# the Arterial bar, alpha kept; stock's #aaaaaa second lines elsewhere take its muted (Text Tertiary); recycled text,
# touch hiding and Home's rows follow the selection.
def begin(m,w): m.call(address=HOOKS['widget_on_paint_background'][0],args=(w,m.canvas,0,0),gap=0)
def end(m,w): m.call(address=HOOKS['widget_on_paint_border'][0],args=(w,m.canvas,0,0),gap=0)
SEL_TEXT,MUTED=0xff000000|DEF['sel_text'],0xff000000|DEF['muted']
m=Machine(); w,rows=m.page_list(3)
labels=[]
for row in rows:
    l=m.node('hscroll_label'); m.nodes[row]['children'].append(l); m.word(l+O['W_PARENT'],row); labels.append(l)
begin(m,w); begin(m,rows[0]); begin(m,labels[0])
assert style_color(m,0xffffffff)[1]==SEL_TEXT
assert style_color(m,0xffaaaaaa)[1]==SEL_TEXT
assert style_color(m,0x66ffffff)[1]==0x66000000|DEF['sel_text']
end(m,labels[0]); end(m,rows[0]); begin(m,rows[1]); begin(m,labels[1])
assert style_color(m,0xffffffff)[1]==0xffffffff and style_color(m,0xffaaaaaa)[1]==MUTED
end(m,labels[1]); end(m,rows[1]); end(m,w)
m.call(); begin(m,w); begin(m,labels[0]); assert style_color(m,0xffaaaaaa)[1]==MUTED
begin(m,labels[1]); assert style_color(m,0xffaaaaaa)[1]==SEL_TEXT
end(m,labels[1]); end(m,w)
m.touch(); begin(m,w); begin(m,labels[1]); assert style_color(m,0xffaaaaaa)[1]==MUTED
m=Machine(); view,imgs=home_list(m); begin(m,view)
labels=[m.nodes[m.get(img+O['W_PARENT'])]['children'][0] for img in imgs]
begin(m,labels[0]); assert style_color(m,0xffaaaaaa)[1]==SEL_TEXT
end(m,labels[0]); begin(m,labels[1]); assert style_color(m,0xffaaaaaa)[1]==MUTED
end(m,labels[1]); end(m,view); passed()
# Decorative action labels may be disabled themselves; availability belongs to the button. A disabled one is stock's
# #aaaaaa secondary text, which OLED draws in its muted grey.
for label_name in ('label_del','label_rename'):
    m=Machine(); label=m.node('hscroll_label',label_name); m.nodes[label]['enable']=0
    button=m.node('button','button',[label]); item=m.node('list_item','item',[button])
    m.word(label+O['W_PARENT'],button); m.word(button+O['W_PARENT'],item)
    for enabled in (1,0,1):
        m.nodes[button]['enable']=enabled
        begin(m,label)
        assert style_color(m,0xffaaaaaa)[1]==(0xffffffff if enabled else MUTED)
    m.nodes[item]['enable']=0; begin(m,label)
    assert style_color(m,0xffffffff)[1]==MUTED
passed()

m=Machine(); style_color(m,red)
# config_value passes its default in decimal (LIGHT's Auto, 2); IPOD SHORTCUT is not a setting and is never read
assert m.config_reads==[('/mnt/data/config.ini','IPOD',key,'0') for key in ('ACCENT','HOME','BATTERY')]+[('/mnt/data/config.ini','IPOD','LIGHT','2'),('/mnt/data/config.ini','IPOD','KEEPLIT','1'),('/mnt/data/config.ini','IPOD','CLICKER','0'),('/mnt/data/config.ini','IPOD','REDUCEMOTION','0')]+[('/mnt/data/config.ini','IPOD',key,str(d)) for key,(_,d,_) in theme_keys(theme_builtins()).items()]; passed()  # the theme's own, in table order
# theme()/scheme(): OLED, the one built-in (themes[0]), and its Arterial scheme, settings read on first use; THEME is no
# setting the engine reads. Light Mode's On picks the light half's row (oled_light), Auto by the clock, Off the dark
# one; both point at oled_tones and derive their half (SCHEME_DERIVE, scheme_t.mode @4).
SCHEME_SIZE=(payload_syms['oled_light']-payload_syms['oled_schemes'])//len(OLED['schemes'])
for config,half in (({},'oled_schemes'),({'OLED_ACCENT':'7'},'oled_schemes'),({'THEME':'1'},'oled_schemes'),({'THEME':'0','ACCENT':'3'},'oled_schemes'),
                    ({'OLED_ACCENT':'12'},'oled_schemes'),({'LIGHT':'1'},'oled_light'),({'LIGHT':'1','OLED_ACCENT':'1'},'oled_light')):
    k=Machine(); k.config=dict(config); k.seed={'LIGHT':'0'}  # no LIGHT key is Auto, by the host's clock: these are Off's
    t=k.call(address=payload_syms['theme'],args=(0,0,0,0),gap=0)&0xffffffff
    assert t==payload_syms['themes'] and len(k.config_reads)==CONFIG_READS,config
    s=k.call(address=payload_syms['scheme'],args=(0,0,0,0),gap=0)&0xffffffff
    assert s==payload_syms[half]+SCHEME_SIZE*DEF_ROW and k.get(s)==payload_syms['oled_tones']+20 and k.u.mem_read(s+4,1)[0]==0,(config,hex(s))
passed()
# A paint before any config read draws the built-in theme, never zeroed scratch: the selection is OLED's Arterial bar.
k=Machine(); k.config={}; kw,_=k.page_list(5,extent=1000); assert k.config_reads==[]
k.paint(kw); assert len(k.config_reads)==CONFIG_READS and k.bands and {b[4]&0xffffff for b in k.bands}=={color_t(DEF_TONES[0])&0xffffff},k.bands[:2]
assert k.bands[0][4]==color_t(DEF_TONES[0]); passed()
# Gradients: the leaf's null checks, then the caller's stops mapped (nr @8, stops @0xc).
def gradient(config,stops,same_out=True,vt_get=True,style=True):
    m=Machine(); m.config=config
    out=m.alloc(0x4c); other=m.alloc(0x4c); vt=m.alloc(0x20); st=m.alloc(8)
    m.word(st,vt); m.word(vt+0x18,GET if vt_get else 0)
    def get(u):
        assert u.reg_read(UC_MIPS_REG_T9)==GET and u.reg_read(UC_MIPS_REG_A0)==st and u.reg_read(UC_MIPS_REG_A2)==out
        g=out if same_out else other
        m.word(g+8,len(stops))
        for i,c in enumerate(stops): m.word(g+0xc+8*i,c); m.word(g+0x10+8*i,i)
        m.returns(g)
    m.get_gradient=get
    ret=m.call(address=ps['ringnav_style_gradient'],args=(st if style else 0,0,out,0),gap=0)&0xffffffff
    g=out if same_out else other
    return ret,[m.get(g+0xc+8*i) for i in range(len(stops))],out,other
ret,cols,out,_=gradient({},[red,grey,color_t(0x7f0a24)])
half=color_t(DEF_TONES[2]>>1&0x7f7f7f)  # 0x7f0a24 is half of stock's red over black: half the tone, per channel
assert ret==out and cols[:2]==[color_t(DEF_TONES[2]),grey] and cols[2]==mapped(color_t(0x7f0a24),DEF_ROW,2) and all(abs(a-b)<=2 for a,b in zip(cols[2].to_bytes(4,'little'),half.to_bytes(4,'little'))),[hex(c) for c in cols]; passed()
assert gradient({'ACCENT':'1'},[red])[1]==[color_t(DEF_TONES[2])]; passed()  # the payload's ACCENT is not OLED's accent
ret,cols,_,other=gradient({},[red],same_out=False); assert ret==other and cols==[red]; passed()
assert gradient({},[red],vt_get=False)[0]==0 and gradient({},[red],style=False)[0]==0; passed()
# Through stock style_get_color, which asks style_get_gradient first for every color: its call
# is left unmapped, so the color hook still gives text the red tone and fills the light tone.
for name,column in (('text_color',3),('bg_color',2)):
    m=Machine(); vt=m.alloc(0x20); st=m.alloc(8); m.word(st,vt); m.word(vt+0x14,0x1000700); m.word(vt+0x18,GET)
    def get(u):
        g=u.reg_read(UC_MIPS_REG_A2); m.word(g+8,1); m.word(g+0xc,red)
        m.returns(g)
    m.get_gradient=get; out=m.alloc(4)
    m.call(address=HOOKS['style_get_color'][0],args=(out,st,m.string(name),0),gap=0)
    assert m.get(out)==color_t(DEF_TONES[column]),(name,hex(m.get(out))); passed()

# Light Mode: neutral colours flip between the light half's paper and ink (OLED's own, white and black: oled/theme.xml
# light.paper and light.ink), other hues stay; red takes Arterial, which is no neutral, so it stays Arterial in both halves;
# a red tint over black becomes the same tint over paper; gradient stops alike.
P,K=DEF['paper'],DEF['ink']; assert (P,K)==(0xffffff,0)
P,K=P&255,K&255
flipv=lambda v: P-v*(P-K)//255
paper,ink=0xff000000|P*0x010101,0xff000000|K*0x010101
for config in ({'LIGHT':'1'},{'LIGHT':'1','OLED_ACCENT':'4'},{'LIGHT':'1','ACCENT':'1'}):
    m=Machine(); m.config=config
    assert style_color(m,red)[1]==color_t(DEF_TONES[3]) and style_color(m,red,'bg_color')[1]==color_t(DEF_TONES[2]),config
    assert style_color(m,0xff000000)[1]==paper and style_color(m,0xffffffff)[1]==ink and style_color(m,grey)[1]==0xff000000|flipv(0x80)*0x010101
    assert style_color(m,0xffff8000)[1]==0xffff8000  # another hue
    tint=style_color(m,color_t(0x3d1920),'bg_color')[1]  # stock's pressed tint: light now, of the tone
    assert all(c>0x90 for c in tint.to_bytes(4,'little')[:3]),hex(tint)
    assert gradient(config,[0xff000000,0xffffffff])[1]==[paper,ink]; passed()
# Theme images flip in Light Mode when colourless and mostly glyph; a disc or a coloured image,
# and stock's, stay. 4x4 BGRA: one white pixel on transparent, or on a grey plate.
def light_image(config,name,plate,prep=None):
    m=Machine(); m.config=config
    if prep: prep(m)
    m.handlers[tramp['image']]='stock_image'
    bm=m.alloc(0x60); data=m.alloc(64)
    m.word(bm,4); m.word(bm+4,4); m.word(bm+8,16); m.u.mem_write(bm+0xe,struct.pack('<H',3)); m.word(bm+0x14,data)
    m.u.mem_write(data,bytes([0xff,0xff,0xff,0xff])+(bytes([0x44,0x44,0x44,0xff]) if plate else bytes(4))*15)
    m.call(address=HOOKS['image_manager_add'][0],args=(0x1000500,m.string(name),bm,0),gap=0)
    return bytes(m.u.mem_read(data,8))
assert light_image({'LIGHT':'1'},'scover_wait0',False)==bytes([K,K,K,0xff])+bytes(4); passed()
assert light_image({'LIGHT':'1'},'scover_wait0',True)==bytes([0xff,0xff,0xff,0xff,0x44,0x44,0x44,0xff]); passed()
assert light_image({},'scover_wait0',False)==bytes([0xff]*4)+bytes(4); passed()
assert light_image({'LIGHT':'1'},'file:///tmp/coverpic.jpg',False)[:4]==bytes([0xff]*4); passed()
# Gain's switch, as switch_on and switch_off, is emptied whole, alpha too in OLED's light half (light.paper, light.ink):
# OLED draws its own switch (settings.values switch, list_marks.c switch_mark).
for gain in ('gain_low','gain_high','switch_on','switch_off'):
    assert light_image({'LIGHT':'1'},gain,False)==bytes(8) and light_image({},gain,True)==bytes(8),gain
passed()

# Images: decoded 32-bit theme bitmaps are mapped in place before stock caches them; covers by
# path or URL and other formats are left alone. bitmap_t w @0, h @4, line_length @8, format @0xe.
def image(config,name,fmt=3,prep=None,px=None):
    m=Machine(); m.config=config; m.handlers[tramp['image']]='stock_image'
    if prep: prep(m)
    bm=m.alloc(0x60); data=m.alloc(64)
    m.word(bm,2); m.word(bm+4,2); m.word(bm+8,12); m.u.mem_write(bm+0xe,struct.pack('<H',fmt)); m.word(bm+0x14,data)
    px=px or [bytes([0x48,0x14,0xff,0x80]),bytes([0x80,0x80,0x80,0xff])]  # BGRA: translucent red, grey
    for y in range(2): m.u.mem_write(data+12*y,px[0]+px[1]+b'\xee'*4)  # 4 bytes of row padding
    ret=m.call(address=HOOKS['image_manager_add'][0],args=(0x1000500,m.string(name),bm,0),gap=0)
    assert ret==0 and [c[1:] for c in m.calls if c[0]=='stock_image'][0][2]==bm
    return [bytes(m.u.mem_read(data+12*y,12)) for y in range(2)]
hi=DEF_TONES[3]; want=bytes([hi&255,hi>>8&255,hi>>16,0x80])+bytes([0x80,0x80,0x80,0xff])+b'\xee'*4
assert image({},'eqdrop_dot')==[want,want]; passed()
# Q2_TRACE's hook answers: the style hook's colour (the word at v0); the image hook's v0 and the pixels it mapped.
saved,TRACE=TRACE,io.StringIO(); m=Machine(); m.config={}; style_color(m,color_t(0xff1448))
assert TRACE.getvalue().splitlines()[-1]=='ret\tringnav_style_color\t%08x'%color_t(DEF_TONES[3])
px=image({},'eqdrop_dot')
assert TRACE.getvalue().splitlines()[-1]=='ret\tringnav_image_add\t00000000\t'+hashlib.sha1(b''.join(px)).hexdigest()[:12]
TRACE=saved; passed()
stock=bytes([0x48,0x14,0xff,0x80,0x80,0x80,0x80,0xff])+b'\xee'*4
for config,name,fmt in (({},'file:///tmp/coverpic.jpg',3),({},'/mnt/mmc/a/cover.jpg',3),
                        ({},'https://resources.tidal.com/images/a/320x320.jpg',3),({},'switch_on',5)):
    assert image(config,name,fmt)==[stock,stock],(config,name,fmt); passed()
# Artwork is rounded before caching, including the cached opacity flag. Straight RGB and row
# padding survive; every byte order and an already transparent source corner are supported. A playlist's row art (its
# 2x2 or its own picture, in Cover Flow's cache, the 52 px _s.jpg) is rounded as an album's; Cover Flow's own covers,
# a playlist's 160 px one among them, are not: Cover Flow insets their corners itself.
ROUNDED_ART=('file:///mnt/mmc/.sldp/','file:///mnt/mmc/.coverflow/pl')
for name in ('file:///mnt/mmc/.sldp/album0.jpg', 'file:///tmp/coverpic.jpg',
             'file:///tmp/externpic.jpg', 'file:///tmp/album_tidal.jpg', 'file:///mnt/mmc/photo.jpg',
             'file:///mnt/mmc/.coverflow/pl_3_0000abcd_s.jpg', 'file:///mnt/mmc/.coverflow/plc_3_0000abcd_s.jpg',
             'file:///mnt/mmc/.coverflow/pl_3_0000abcd.jpg', 'file:///mnt/mmc/.coverflow/plc_3_0000abcd.jpg',
             'file:///mnt/mmc/.coverflow/0000abcd.jpg'):
    for fmt in range(1,5):
        m=Machine(); m.handlers[tramp['image']]='stock_image'
        w,h,stride=60,52,248; bm=m.alloc(0x60); data=m.alloc(stride*h)
        m.word(bm,w); m.word(bm+4,h); m.word(bm+8,stride)
        m.u.mem_write(bm+0xc,struct.pack('<HH',3,fmt)); m.word(bm+0x14,data)
        alpha=(3,0,3,0)[fmt-1]
        pixel=bytearray([47]*4); pixel[alpha]=255
        source=bytearray((pixel*w+b'\xee'*8)*h); source[alpha]=0
        m.u.mem_write(data,bytes(source))
        args=(0x1000500,m.string(name),bm,0)
        m.call(address=HOOKS['image_manager_add'][0],args=args,gap=0)
        rounded=bytes(m.u.mem_read(data,stride*h))
        if not name.startswith(ROUNDED_ART) or ('/.coverflow/' in name and not name.endswith('_s.jpg')):
            assert rounded==source and m.get(bm+0xc)&1,name
        else:
            assert not m.get(bm+0xc)&1 and m.get(bm+0xc)&2,name
            assert all(rounded[y*stride+4*x+alpha]==0 for x,y in ((0,0),(w-1,0),(0,h-1),(w-1,h-1)))
            assert rounded[4*(w//2)+alpha]==rounded[(h//2)*stride+alpha]==255
            assert all(rounded[i]==value for i,value in enumerate(source) if i%stride>=w*4 or i%4!=alpha)
            assert any(0<rounded[y*stride+4*x+alpha]<255 for y in range(4) for x in range(4))
            m.call(address=HOOKS['image_manager_add'][0],args=args,gap=0)
            assert bytes(m.u.mem_read(data,stride*h))==rounded
        passed()

# The confirm pop-up's red discs take the dark CONFIRM_SURFACE, not the accent:
# the stock OK disc (#FF1448, white glyph) and Cancel's tint (#FF4871 with a #FFE4EA glyph) keep
# their glyphs at 4.5:1 or more; the pressed images darken; other images keep the accent's tone.
def rgba(config,name,pixels_,fmt=3):
    m=Machine(); m.config=config; m.handlers[tramp['image']]='stock_image'
    bm=m.alloc(0x60); data=m.alloc(4*len(pixels_))
    m.word(bm,len(pixels_)); m.word(bm+4,1); m.word(bm+8,4*len(pixels_)); m.u.mem_write(bm+0xe,struct.pack('<H',fmt)); m.word(bm+0x14,data)
    at=((0,1,2,3),(3,2,1,0),(2,1,0,3),(1,2,3,0))[fmt-1]
    def packed(c,a):
        p=bytearray(4)
        for offset,value in zip(at,(c>>16,c>>8&255,c&255,a)): p[offset]=value
        return p
    m.u.mem_write(data,b''.join(packed(c,a) for c,a in pixels_))
    m.call(address=HOOKS['image_manager_add'][0],args=(0x1000500,m.string(name),bm,0),gap=0)
    raw=bytes(m.u.mem_read(data,4*len(pixels_)))
    return [(raw[4*i+at[0]]<<16|raw[4*i+at[1]]<<8|raw[4*i+at[2]],raw[4*i+at[3]]) for i in range(len(pixels_))]
def pixels(config,name,colors):
    return [c for c,_ in rgba(config,name,[(c,255) for c in colors])]
S=O['CONFIRM_SURFACE']
# OLED's Arterial, any stored accent value.
for config in ({},{'OLED_ACCENT':'1'},{'ACCENT':'1'}):
    disc,glyph=pixels(config,'confirm_ok',[0xff1448,0xffffff])
    assert disc==S and glyph==0xffffff and ratio(glyph,disc)>=4.5,(config,hex(disc))
    disc,glyph=pixels(config,'confirm_cancel',[0xff4871,0xffe4ea])
    assert ratio(glyph,disc)>=4.5 and ratio(disc,0)<ratio(glyph,0),(config,hex(disc),hex(glyph))
    assert pixels(config,'confirm_okdown',[0x7f0a24])[0]<S  # pressed: darker than the disc
    assert pixels(config,'sleepdot',[0xff1448])==[DEF_TONES[3]]
    passed()

# OLED's icons are mono (oled/theme.xml list.icons mono): the settings and list icons' discs turn the scheme's chevron
# grey, their glyphs stay white, edges blend by saturation against the disc's, alpha kept. Stock's list_into is
# emptied (OLED's caret is paint_into's). Navigation and control images (Quick Settings' discs among them) take the
# grey of their mapped colour, codec badges the muted grey's; other images keep Arterial whatever ACCENT holds.
ink=DEF['chevron']; assert ink==O['MINIMAL_CHEVRON']
def grey_of(c): return ((c>>16)*299+(c>>8&255)*587+(c&255)*114)//1000*0x010101  # the payload's mono grey of 0xRRGGBB
edge=255-(255-(ink&255))*(0xff-0x8a)//(0xff-0x14); edge*=0x010101  # #ff8aa4's saturation against the disc's
px=[(0xff1448,255),(0xff8aa4,255),(0xffffff,255),(0xff1448,128),(0xffffff,0)]
disc=[(0xff1448,255),(0xffffff,255),(0xffc0d0,255),(0xff1448,0),(0x000000,0)]  # disc, glyph, glyph edge, transparent
for icon in (px,[(0x00a0ff,255),(0xffffff,255)],[(0xc425ff,255),(0xffffff,255)]):
    got=rgba({},'system_netservice',icon)
    assert got[0]==(ink,255) and (0xffffff,255) in got,got
for config in ({},{'ACCENT':'2'}):
    assert rgba(config,'system_netservice',px)==[(ink,255),(edge,255),(0xffffff,255),(ink,128),(0xffffff,0)]
    assert rgba(config,'list_into',px)==[(0,0)]*len(px) and rgba(config,'list_intodown',px)==[(0,0)]*len(px)
    assert rgba(config,'list_delete',[(0xffffff,255),(0xffffff,0)])==[(0xffffff,255),(0xffffff,0)]
    # Codec badges take the muted grey (#858585), legible on black and on the Arterial bar; other img_ art doesn't.
    assert rgba(config,'img_flac',[(0xff1448,255),(0xff1448,0)])==[(grey_of(DEF['muted']),255),(grey_of(DEF['muted']),0)]
    assert rgba(config,'img_delete',[(0xff1448,255)])==[(grey_of(DEF_TONES[3]),255)]
    assert rgba(config,'sleepdot',disc[:2])==[(DEF_TONES[2],255),(0xffffff,255)]
    assert rgba(config,'file:///mnt/mmc/system_netservice',px)==px
    # Active Delete keeps stock's white; the pressed/disabled glyph keeps its grey.
    assert rgba(config,'navbar_delete',[(0xffffff,255)])==[(0xffffff,255)]
    assert rgba(config,'navbar_deletedown',[(0x777777,255)])==[(0x777777,255)]
    for name in ('navbar_add','home_stream','img_edit','stream_spotify','stream_radio','tidal_fav_add','play_fav','btn_blue','drop_wifiopen','drop_wifi'):
        got=rgba(config,name,[(0x158bcd,255),(0xff1448,128),(0xffffff,0)])
        assert all(c>>16 == (c>>8&255) == (c&255) for c,a in got), (name,got)
        assert [a for c,a in got]==[255,128,0]
    # A card app's Streaming icon (addon.c, by its path on the card) greys as stream_spotify does,
    # its white glyph white and alpha kept, and the app's other images stay.
    for name in ('file:///mnt/mmc/.deezer/icon-40.png','file:///mnt/mmc/.deezer/icon.png'):
        got=rgba(config,name,[(0xa238ff,255),(0xffffff,255),(0x2800ff,128)])
        assert all(c>>16 == (c>>8&255) == (c&255) for c,a in got) and got[1]==(0xffffff,255),(name,got)
        assert [a for c,a in got]==[255,255,128] and got[0]!=got[2]
    for name in ('file:///mnt/mmc/.deezer/cover.png','file:///mnt/mmc/Music/icon.png','file:///mnt/mmc/.deezer/icon.png.jpg'):
        assert rgba(config,name,[(0xa238ff,255)])==[(0xa238ff,255)],name
    for name in ('local_album','local_shuffle','list_tidal','small_stream','player_playlist'):
        assert rgba(config,name,px)==[(ink,255),(edge,255),(0xffffff,255),(ink,128),(0xffffff,0)],name
        grey=[(0x777777,255),(0xffffff,255),(0xbcbcbc,128),(0xffffff,0)]
        assert rgba(config,name,grey)==grey,name
    for name in ('default_album_big','play_defaultcover','playlist_default','tidal_user','tidal_shanling',
                 'bar_charge','bar_lowcharge','/mnt/mmc/local_photo.png','https://example.org/play_fav.png'):
        colors=[(0x12a3ce,128),(0xffffff,0)]
        assert rgba(config,name,colors)==colors,name
passed()

# Packaged icon pixels: monochrome, alpha preserved and existing grey glyphs unchanged. Quick Settings' active discs
# are Arterial's grey under a white glyph (Arterial is no brighter than GLYPH_LIGHT_MAX), the inactive ones stock's #444444.
for fmt in (1,2,3,4):
    assert rgba({},'local_album',px,fmt)==rgba({},'local_album',px)
    assert rgba({},'stream_spotify',px,fmt)==rgba({},'stream_spotify',px)
assert grey_of(DEF_TONES[3])&255<=O['GLYPH_LIGHT_MAX']
for name in ('drop_wifiopen','drop_btopen','drop_keylockopen','drop_highgain','drop_lo','drop_usbaudio','drop_usbdac'):
    active=rgba({},name,disc[:2]); inactive=rgba({},'drop_wifi',[(0x444444,255),(0xffffff,255)])
    assert active==[(grey_of(DEF_TONES[3]),255),(0xffffff,255)] and inactive==[(0x444444,255),(0xffffff,255)],(name,active)
    assert ratio(active[0][0],active[1][0])>=3
passed()
import subprocess
from ipod import imagemagick
for name in ('local_album','local_longsongs','list_folder','list_tidal','player_playlist',
             'small_stream','stream_spotify','stream_radio','tidal_fav_add','play_fav'):
    png=subprocess.check_output(['unsquashfs','-cat',str(B/'rootfs.squashfs'),
                                 'release/assets/default/raw/images/xx/'+name+'.png'])
    raw=imagemagick('png:-','-depth','8','rgba:-',data=png)
    colors=list(dict.fromkeys((raw[i]<<16|raw[i+1]<<8|raw[i+2],raw[i+3]) for i in range(0,len(raw),4)))
    sample=colors[::max(1,len(colors)//32)]
    got=rgba({},name,sample)
    assert all(c>>16==(c>>8&255)==(c&255) for c,a in got),name
    assert [a for c,a in got]==[a for c,a in sample],name
    if name in ('list_folder','list_tidal','player_playlist'): assert got==sample,name
passed()

# Display settings: after the stock rows, Appearance, Cover Flow and Controls, each opening stock's page
# again (display_sub) with its stock rows dropped and its own rows instead. Appearance holds OLED's declared options
# (Light Mode, Accent, Home, Battery, then its own Song Artist, Song Length and Track Time) in the native row
# widgets and styles, each a title with its value on the right (settings.values text), with the Display, cover mode
# and power manager icons; Centre or tap cycles and saves each; a new accent or Light Mode drops the image cache and
# repaints, a new Battery mode repaints the bar.
def display_page(m,sub):
    m.word(payload_syms['display_sub'],sub)
    view=m.node('scroll_view','scroll_view_display',[m.entry(0) for _ in range(4)])  # stock's four: Brightness, Screen Off, Lock Screen, Show Navigation Bar
    for e in m.nodes[view]['children']: m.word(e+O['W_PARENT'],view)
    m.top=m.node('window','display_page',[m.node('list_view','list_view_display',[view])])
    m.nodes[m.wm]={'type':'window_manager','children':[m.top]}
    m.calls=[]; assert m.call(address=HOOKS['systemset_display_page_init'][0],args=(m.top,5,0,0),gap=0)==0
    assert m.calls[0][:3]==('stock_display',m.top,5)
    m.icons_set=[m.text(c[2]) for c in m.calls if c[0]=='image_base_set_image']
    return view
def display(config,card=True,sub=1,cls=None,setup=None):
    CONFIG.clear(); CONFIG.update(config); SETUP[:]=[setup] if setup else []; m=(cls or QueueMachine)(); SETUP.clear(); m.restack=True; m.rockbox_mode=0o100755 if card else 0; m.handlers[tramp['display']]='stock_display'
    view=display_page(m,sub)
    return m,view,setting_rows(m,view) if sub==1 else None
def setting_rows(m,view):  # Accent, Home, Battery: as ringnav_display's index
    lab=lambda r: m.nodes[m.nodes[m.nodes[r]['children'][0]]['children'][1]].get('text','')
    kids=m.nodes[view]['children']
    return [next(r for r in kids if lab(r).startswith(k)) for k in ('Accent','Home','Battery')]
def row_text(r):  # a row's label; a value row (OLED's settings.values text) reads "title: value"
    b=m.nodes[m.nodes[r]['children'][0]]; t=m.nodes[b['children'][1]]['text']
    v=[m.nodes[k]['text'] for k in b['children'] if m.nodes[k].get('name')=='_setting_value']
    return f'{t}: {v[0]}' if v else t
m,main,_=display({},sub=0)
kids=m.nodes[main]['children']
assert len(kids)==9 and row_text(kids[0])=='Brightness' and [row_text(r) for r in kids[3:]]==['Smart Awake','Night Shift: Off','Appearance','Cover Flow','playset_covermode','Reduce Motion']  # Brightness inline first (inline_slider.c), stock's row hidden; no Lock Screen; Enlarged Album Art, then Reduce Motion last
keep_lit=kids[3]
assert m.icons_set==['display_backlight','system_display','switch_on','display_backlight','list_into','system_display','list_into','playset_covermode','list_into','playset_covermode','switch_off','system_display','switch_off']  # Smart Awake on: its default; Reduce Motion off
f,ctx=m.handler(m.nodes[kids[5]]['children'][0],O['EVT_CLICK']); m.calls=[]
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.opened[-1]==('systemset/display_page',None) and m.get(payload_syms['display_sub'])==0; passed()
# Cover Flow: what changes Cover Flow alone, Sort (its picker's row, a chevron), Fast Navigation, Position Bar, then
# Refresh Cover Flow after a 12 px gap; Controls, System's row: the wheel, the buttons and what they do.
cfv=display_page(m,2); cf_sort,cf_big,cf_position,cf_gap,cf_refresh=m.nodes[cfv]['children']
assert m.icons_set==['playset_covermode','list_into']+['playset_covermode','switch_off']*2+['system_update'],m.icons_set
assert m.nodes[cf_gap]['type']=='label' and m.get(cf_gap+O['W_H'])==12
ctl=display_page(m,3); wheel,hold,tone,lock,wake=m.nodes[ctl]['children']  # tone: the Clicker
assert [row_text(r) for r in (hold,tone)]==['Hold Return: Recent','Clicker: Off']; passed()
view=display_page(m,1); rows=setting_rows(m,view)
# the theme's options (assets/themes/oled/theme.xml): no Shortcut row
kids=m.nodes[view]['children']; light,song_artist,song_length,np_time=kids[0],kids[4],kids[5],kids[6]
assert [row_text(r) for r in kids]==['Light Mode: Off','Accent: Arterial','Home: Split','Battery: Icon','Song Artist: Inline','Song Length: Albums','Track Time: Remaining'],[row_text(r) for r in kids]
assert kids==[light,*rows,song_artist,song_length,np_time] and m.nodes[rows[0]].get('visible',1)==1
assert m.icons_set==['system_display','system_display','playset_covermode','system_powermanager']+['system_display']*3,m.icons_set and all(m.nodes[r]['type']=='list_item' and m.nodes[r]['style']=='s_listitem_black' for r in rows)
assert not {'switch_on','switch_off'} & set(m.icons_set)  # values, not switches
buttons=[m.nodes[r]['children'][0] for r in rows]; labels=[m.nodes[b]['children'][1] for b in buttons]
for b,l in zip(buttons,labels):
    icon=m.nodes[b]['children'][0]  # stock's 0x4c6b94 icon geometry, which the row layouter maps
    assert m.nodes[icon]['type']=='image' and [m.get(icon+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[10,0,52,70]
    assert m.nodes[b]['style']=='s_btn_listitem' and [m.get(b+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[20,0,335,70]
    assert m.nodes[l]['type']=='hscroll_label' and m.nodes[l]['style']=='s_scrlabel_white24l' and m.get(l+O['W_X'])==72
def texts(): return [row_text(r) for r in rows]
# Smart Awake (IPOD KEEPLIT, #57): a switch, on unless chosen, that only saves.
keep_label=m.nodes[m.nodes[keep_lit]['children'][0]]['children'][1]; assert m.nodes[keep_label]['text']=='Smart Awake'
f,ctx=m.handler(m.nodes[keep_lit]['children'][0],O['EVT_CLICK']); m.calls=[]
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and not did(m,'image_manager_unload_all')
assert [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[(0,'IPOD','KEEPLIT')]
m.call(address=f,args=(ctx,m.event,0,0),gap=0)
# Time in Title has no row and no key: the status row shows its clock at the left and names no page.
assert not [r for r in kids if row_text(r).startswith('Time in Title')]
# The window loader loads each window by the name asked.
load=HOOKS['ui_loader_load_widget_with_parent'][0]; m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'],16)]='stock_ui_load'
def loaded(name):
    m.calls=[]; m.call(address=load,args=(m.string(name),0,0,0),gap=0)
    return [m.text(c[1]) for c in m.calls if c[0]=='stock_ui_load']
assert loaded('playing_page')==['playing_page'] and loaded('home_page')==['home_page']; passed()
settings_m=m  # the Appearance cases go on with it below
from ipod import AUDIT, immediate_values
SITES={int(a,16):g['value'] for g in AUDIT['immediates'] if isinstance(g['value'],str) for a,_ in g['sites']}
def set_value(m,name,v): m.u.mem_write(payload_syms['geom_words']+2*VALUE_NAMES.index(name),struct.pack('<H',v))  # geom(G_<name>)'s word
# The build bakes OLED's set (tools/theme.py built_geometry) into the stock immediates, and geom_words holds the same values.
gm=Machine(); words=struct.unpack(f'<{len(VALUE_NAMES)}H',gm.u.mem_read(payload_syms['geom_words'],2*len(VALUE_NAMES)))
assert words==tuple(immediate_values(OLED_G)[n] for n in VALUE_NAMES)
assert {a:gm.get(a)&0xffff for a in SITES}=={a:words[VALUE_NAMES.index(v)] for a,v in SITES.items()}
assert {gm.get(a)&0xffff for a,v in SITES.items() if v=='pitch'}=={OLED_G.pitch}; passed()
# The first system_bar load loads stock's system_bar.
nm=Machine(); nm.record_syscalls(); nm.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'],16)]='stock_ui_load'
nm.call(address=load,args=(nm.string('system_bar'),0,0,0),gap=0)
assert [nm.text(c[1]) for c in nm.calls if c[0]=='stock_ui_load']==['system_bar']; passed()
# client_h() is 320 less OLED's 58 px bar.
assert OLED_G.bar==58 and Machine().call(address=payload_syms['client_h'],args=(0,0,0,0),gap=0)==320-OLED_G.bar; passed()
# Migration (navigation.c theme_boot): a unit an older build left on another theme (IPOD THEME other than 3, or no
# key) takes OLED's preset once at its first system_bar load, as choosing OLED did, then THEME 3 is written; with
# THEME 3 nothing is written. The preset is theme.xml's (theme_preset): Light Mode off unless Auto (keep), Arterial,
# Home Split, and its own options' presets; Battery is kept, and only a change is saved. A second load
# does nothing again.
OPT_KEY={'SETTING_LIGHT':'LIGHT','SETTING_ACCENT':'OLED_ACCENT','SETTING_HOME':'HOME','SETTING_BATTERY':'BATTERY'}
def preset_writes(config):  # theme_preset's saves for config, from OLED's options (oled/theme.xml), in their order
    out=[]
    for o in OLED['opts']:
        key=o.get('key') or OPT_KEY[o['setting']]
        n,d=(theme_keys(theme_builtins())[key][:2] if o.get('key') or key=='OLED_ACCENT' else
             {'LIGHT':(3,0),'HOME':(2,0),'BATTERY':(4,0)}[key])  # HOME: Split, Full; any other value reads Split
        s=config.get(key,''); v=int(s) if s.isdigit() and int(s)<n else d
        if o['values'] and o['def']>=0 and not o['values']>>v&1: v=o['def']
        if o['preset']>=0 and v!=o['keep'] and v!=o['preset']: out.append((o['preset'],'IPOD',key))
    return out
assert preset_writes({})==[]  # a fresh unit: already OLED's preset (Home Split, Light Mode off, Arterial)
for config in ({},{'THEME':'1'},{'THEME':'0','LIGHT':'1','HOME':'2','SONGARTIST':'2','SONGLENGTH':'1','NPTIME':'1','OLED_ACCENT':'4'},
               {'THEME':'2','LIGHT':'2','HOME':'1','BATTERY':'1','SHORTCUT':'5'},{'THEME':'12'},
               {'THEME':'3'},{'THEME':'3','LIGHT':'1','HOME':'2'}):
    mm=Machine(); mm.config=dict(config); mm.record_syscalls()
    mm.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'],16)]='stock_ui_load'; mm.calls=[]
    mm.call(address=load,args=(mm.string('system_bar'),0,0,0),gap=0)
    writes=[(c[1],mm.text(c[2]),mm.text(c[3])) for c in mm.calls if c[0]=='write_int_config']
    assert writes==([] if config.get('THEME')=='3' else preset_writes(config)+[(3,'IPOD','THEME')]),(config,writes)
    mm.calls=[]; mm.call(address=load,args=(mm.string('system_bar'),0,0,0),gap=0)
    assert not [c for c in mm.calls if c[0]=='write_int_config'],config  # once a boot
passed()
def bar_of(nm,*children):  # a 375x26 system bar holding children
    b=nm.node('system_bar','system_bar',list(children)); nm.word(b+O['W_PARENT'],nm.wm)
    nm.word(b+O['W_W'],375); nm.word(b+O['W_H'],26); nm.word(syms['system_bar'],b); return b
def channels(c): return (c&255,c>>8&255,c>>16&255)  # color_t's R, G, B
def oled_on(m):  # the first system_bar load, as on a unit (accent, theme_boot)
    m.record_syscalls(); m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'],16)]='stock_ui_load'
    m.call(address=load,args=(m.string('system_bar'),0,0,0),gap=0); return m
# Radio rows: stock's ring images (select, unselect) are emptied; select draws OLED's check (theme art check_a, the
# accent's) ending on the image's right end, centred on its height; unselect draws nothing.
rm=Machine(); rimg=rm.node('image','img_sel',image='select'); rm.word(rimg+O['W_W'],50); rm.word(rimg+O['W_H'],28)
rm.paint(rimg)
assert 'oled_check_a' in [rm.text(c[2]) for c in rm.calls if c[0]=='widget_load_image'] and len(rm.icons)==1 and not rm.bands,rm.icons
rm.icons=[]; rm.nodes[rimg]['image']='unselect'; rm.paint(rimg); assert not rm.bands and not rm.icons
assert image({},'select')==image({},'unselect')==[bytes(8)+b'\xee'*4]*2; passed()
m=settings_m
# Cover Flow's Sort and Refresh Cover Flow (coverflow.c), the library Coverflow's Sort and Refresh cards before: no
# saved order is Album; Refresh empties the art cache for the next open, and stock's Library covers with AWTK's cached
# bitmaps, with a toast.
refresh_label=m.nodes[m.nodes[cf_refresh]['children'][0]]['children'][1]
assert row_text(cf_sort)=='Sort: Album' and m.nodes[refresh_label]['text']=='Refresh Cover Flow'  # Sort's value on the right column (list_value)
f,ctx=m.handler(m.nodes[cf_refresh]['children'][0],O['EVT_CLICK']); m.mock('opendir@GLIBC_2.0'); m.calls=[]
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.toasts[-1][3]=='Cover Flow rebuilds artwork next time'
assert [m.text(c[1]) for c in m.calls if c[0]=='opendir@GLIBC_2.0']==['/mnt/mmc/.coverflow','/mnt/mmc/.sldp']
assert did(m,'image_manager_unload_all') and m.nodes[refresh_label]['text']=='Refresh Cover Flow'; passed()
assert texts()==['Accent: Arterial','Home: Split','Battery: Icon']; passed()
def click(i):
    m.calls=[]; f,ctx=m.handler(buttons[i],O['EVT_CLICK'])
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
    return [c[1:] for c in m.calls if c[0]=='write_int_config']
# Initialize the real Home asset on this machine, then toggle its cached settings in Display.
home=asset_tree(m,HOME_PAGE)
m.handlers[HOOKS['home_page_init'][0]+12]='stock_home'
m.call(address=HOOKS['home_page_init'][0],args=(home,0,0,0),gap=0)
art=named(m,home,'img_homeart')
# Home's lists from OLED's home.rows (oled/theme.xml): labels from text_x to label_end short of the row. Split's menu
# is the set's split_row wide, its art from split_art; Full widens the menu and its rows to full_row. Home's other lists are full
# width in either (home.lists full), on full_row rows with Full's label end. Shuffle Songs, which opens nothing and has
# no chevron, takes the chevron's room to the set's right column (home.c home_in_place).
# Split's main menu (coverflow.c home_width): its top HOME_SPLIT_TOP on the screen, under the art's split_art_up
SPLIT_TOP=int(re.search(r'^#define HOME_SPLIT_TOP (\d+)',(ROOT/'patch/coverflow.c').read_text(),re.M)[1]); assert OLED_G.split_art_up
def home_bounds(full):
    split,right=OLED_G.split_row,OLED_G.right_x
    for name,sub in (('list_view_home',full),('list_view_homeset',True)):
        x0,end,row=OLED['home_text_x'],OLED[f'home_label_end[{int(sub)}]'],OLED['home_full_row'] if sub else split
        menu=named(m,home,name)
        # Home's window is the whole screen (full-bleed): Split's menu rises into the status row, the others start home_top
        # under its foot
        top=SPLIT_TOP if name=='list_view_home' and not full else OLED['home_top']+OLED_G.bar
        assert m.get(menu+O['W_W'])==(375 if sub else split) and signed(m.get(menu+O['W_Y']))==top,name
        for r in m.nodes[m.nodes[menu]['children'][0]]['children']:
            label=m.nodes[r]['children'][0]
            x,y,width,height=cf_geometry(m,label)
            e=min(end,right) if m.nodes[label].get('name')=='label_shuffle_songs' else end
            assert x==x0 and width>0 and x+width==row-e and m.get(r+O['W_W'])==row,(name,x,width,row)
    assert m.nodes[art]['visible']==(not full)
    assert tuple(signed(m.get(art+O[k])) for k in ('W_X','W_Y','W_W','W_H'))==(OLED_G.split_art,0,375-OLED_G.split_art,320)  # the pane, up under the status row (coverflow.c pane_y)
def carousel():
    """Stock's carousel and arrows hidden and taking no taps in every layout (tools/ipod.py ipod_home); the main
    list shown, the Settings list not until opened."""
    def shown(name):
        w=named(m,home,name); n=m.nodes[w]
        taps=n.get('sensitive',int(n['asset'].get('sensitive')!='false'))
        assert n['visible']==taps,name
        return n['visible']
    assert [shown(n) for n in ('slide_menu','img_left','img_right','list_view_home','list_view_homeset')]==[0]*3+[1,0]
# (Appearance has no Shortcut row.)
home_bounds(False); carousel()
writes=click(1); assert [(w[0],m.text(w[2])) for w in writes]==[(1,'HOME')] and texts()[1]=='Home: Full'
home_bounds(True); carousel()
assert not [c for c in m.calls if c[0]=='image_manager_unload_all']; passed()
# Home has two layouts: Full's next click is Split, written as 0 (a saved 2, Carousel's, reads as Split: test/fw)
writes=click(1); assert [(w[0],m.text(w[2])) for w in writes]==[(0,'HOME')] and texts()[1]=='Home: Split'
home_bounds(False); carousel(); passed()
# Light Mode: no key is Off; a click saves On, then Auto (LIGHT=2), then Off; each drops the cached images and repaints.
light_label=m.nodes[m.nodes[light]['children'][0]]['children'][1]; assert row_text(light)=='Light Mode: Off'
f,ctx=f_light,ctx_light=m.handler(m.nodes[light]['children'][0],O['EVT_CLICK'])
for value,text in ((1,'On'),(2,'Auto'),(0,'Off')):
    m.calls=[]; assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and row_text(light)=='Light Mode: '+text
    assert [(c[1],m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[(value,'LIGHT')]
    assert ('image_manager_unload_all',0x1000500) in [c[:2] for c in m.calls] and ('widget_invalidate_force',m.wm) in [c[:2] for c in m.calls]
    if value==2:  # Auto: the clock's minute drops the images and repaints as the hour crosses, once
        for clock,flip in (((6,59),1),((6,59),0),((7,0),1),((7,1),0),((19,0),1)):
            m.clock=clock; m.calls=[]
            m.call(address=payload_syms['clock_due'],args=(0,0,0,0),gap=0,clear=False)
            assert [c[:2] for c in m.calls if c[0]=='image_manager_unload_all']==[('image_manager_unload_all',0x1000500)]*flip,clock
        m.clock=(18,14)
        m.call(address=payload_syms['clock_due'],args=(0,0,0,0),gap=0,clear=False)
passed()
# Accent: from Arterial a click moves to Blue, OLED's other value (values mask 0xc00), and saves it as OLED_ACCENT,
# dropping the images and repainting; the selection is then Blue's bar.
writes=click(0)
assert [(w[0],m.text(w[1]),m.text(w[2])) for w in writes]==[(OLED_BLUE_ROW,'IPOD','OLED_ACCENT')] and texts()[0]=='Accent: Blue'
assert ('image_manager_unload_all',0x1000500) in [c[:2] for c in m.calls] and ('widget_invalidate_force',m.wm) in [c[:2] for c in m.calls]
m.paint(view); assert m.bands and {b[4]&0xffffff for b in m.bands}=={color_t(OLED['schemes'][OLED_BLUE_ROW]['tones'][0])&0xffffff}
writes=click(0); assert [(w[0],m.text(w[2])) for w in writes]==[(DEF_ROW,'OLED_ACCENT')] and texts()[0]=='Accent: Arterial'; passed()
# Battery: OLED offers Icon and Percent (values="icon percent"); each click saves and repaints the bar alone.
bar=m.node('window','system_bar'); m.word(syms['system_bar'],bar)
for value,name in ((1,'Percent'),(0,'Icon')):
    writes=click(2); assert [(w[0],m.text(w[2])) for w in writes]==[(value,'BATTERY')] and texts()[2]=='Battery: '+name
    assert ('widget_invalidate_force',bar) in [c[:2] for c in m.calls] and not [c for c in m.calls if c[0]=='image_manager_unload_all']
passed()
# A stored value outside an option's values reads as its default, the key untouched: ACCENT and OLED_ACCENT are Arterial,
# Battery's Icon + Percent and Percent Inside are Icon.
m,view,rows=display({'ACCENT':'2','OLED_ACCENT':'3','HOME':'1','BATTERY':'2','SHORTCUT':'1'}); assert [row_text(r) for r in rows]==['Accent: Arterial','Home: Full','Battery: Icon']
m,view,rows=display({'BATTERY':'3'}); assert row_text(rows[2])=='Battery: Icon'; passed()
