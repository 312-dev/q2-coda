class CardMachine(DepthMachine):
    """The card as a tree of {path: [(name, d_type)]} and files as {path: bytearray}, with stdio
    handles over them, and a fixed-pitch font: every character TEXT_PITCH wide."""
    def __init__(self,tree,files):
        super().__init__()
        self.tree=tree; self.files=files; self.handles={}; self.dirs={}; self.lines=[]
        for n in ('opendir@GLIBC_2.0','readdir@GLIBC_2.0','closedir@GLIBC_2.0','qsort@GLIBC_2.0','fopen@GLIBC_2.2',
                  'fread@GLIBC_2.0','fwrite@GLIBC_2.0','fseek@GLIBC_2.0','ftell@GLIBC_2.0','fclose@GLIBC_2.2',
                  'access@GLIBC_2.0','rename@GLIBC_2.0','unlink@GLIBC_2.0','mkdir@GLIBC_2.0','memcmp@GLIBC_2.0',
                  'strlen@GLIBC_2.0','strstr@GLIBC_2.0','canvas_measure_text','canvas_draw_text',
                  'fork@GLIBC_2.0','execl@GLIBC_2.0','exit@GLIBC_2.0','waitpid@GLIBC_2.0','socket@GLIBC_2.0',
                  'sendto@GLIBC_2.0','close@GLIBC_2.0','open@GLIBC_2.0','setenv@GLIBC_2.0','connect@GLIBC_2.0'):
            self.handlers[syms[n]]='b:'+n
        self.handlers[syms['mclGetOutputWay']]='b:mclGetOutputWay'
        self.handlers[payload_syms['child_exit']]='b:exit'
        self.u.hook_add(UC_HOOK_CODE,self.hook,begin=payload_syms['child_exit'],end=payload_syms['child_exit'])
        self.forked=4242; self.waited=0; self.way=0; self.execs=[]; self.sent=[]
        self.write_cap=None; self.close_fails=0  # a full disk: a file holds write_cap bytes at most; the next close_fails closes of a .tmp fail
        self.on_fopen=None  # called with each path fopen opens, before it does
        tramp=int(manifest['patch_symbols']['stock_localmusic_trampoline'],16)
        self.handlers[tramp]='stock_localmusic'; self.u.hook_add(UC_HOOK_CODE,self.hook,begin=tramp,end=tramp)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='c:calloc@GLIBC_2.0':
            ret=self.alloc((a*b+7)&~3) if a*b<=0x10000 else self.big_alloc(a*b)  # word-aligned
            if hasattr(self,'poison'): self.poison[ret]=a*b  # zeroed again when freed: a use after free reads ""
        elif name=='b:qsort@GLIBC_2.0' and c!=4:  # not names (Tidal's and a card app's songs): the payload's comparator
            self.handlers[address]='c:qsort@GLIBC_2.0'
            try: return super().hook(u,address,size,unused)
            finally: self.handlers[address]=name
        elif not name.startswith('b:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]
        if name=='opendir':
            path=self.text(a); ret=self.alloc(4) if path in self.tree else 0; self.dirs[ret]=list(self.tree.get(path,[]))
        elif name=='readdir' and self.dirs.get(a):
            n,t=self.dirs[a].pop(0); ret=self.alloc(268); self.byte(ret+10,t); self.u.mem_write(ret+11,n.encode()+b'\0')
        elif name=='qsort':
            ptrs=[self.get(a+4*i) for i in range(b)]
            for i,p in enumerate(sorted(ptrs,key=self.text)): self.word(a+4*i,p)
        elif name=='fopen':
            path,mode=self.text(a),self.text(b)
            if self.on_fopen: self.on_fopen(path)
            if 'w' in mode: self.files[path]=bytearray()
            if path in self.files: ret=self.alloc(4); self.handles[ret]=[path,0]
        elif name in ('fread','fwrite'):
            path,pos=self.handles[d]; f=self.files[path]
            if name=='fread': chunk=bytes(f[pos:pos+b*c]); self.u.mem_write(a,chunk)
            else:
                chunk=bytes(u.mem_read(a,b*c))
                if self.write_cap is not None: chunk=chunk[:max(0,self.write_cap-pos)]
                f[pos:pos+len(chunk)]=chunk
            self.handles[d][1]+=len(chunk); ret=len(chunk)//b
        elif name=='fseek':
            h=self.handles[a]; h[1]=signed(b)+(0,h[1],len(self.files[h[0]]))[c]; ret=0
        elif name=='ftell': ret=self.handles[a][1]
        elif name=='fclose':
            path=self.handles.pop(a)[0]
            if self.close_fails and path.endswith('.tmp'): self.close_fails-=1; ret=-1
        elif name=='access': ret=0 if self.text(a) in self.files or self.text(a) in self.tree else -1
        elif name=='rename': self.files[self.text(b)]=self.files.pop(self.text(a))
        elif name=='unlink': self.files.pop(self.text(a),None)
        elif name=='memcmp': ret=0 if bytes(u.mem_read(a,c))==bytes(u.mem_read(b,c)) else 1
        elif name=='strlen': ret=len(self.raw(a))  # bytes: a buffer may end inside a UTF-8 character
        elif name=='strstr':
            i=self.raw(a).find(self.raw(b)); ret=a+i if i>=0 else 0
        elif name=='canvas_measure_text': u.reg_write(UC_MIPS_REG_F0,struct.unpack('<I',struct.pack('<f',(getattr(self,'char_em',0)*self.font[1] or TEXT_PITCH)*c))[0])
        elif name=='mclGetOutputWay': ret=self.way
        elif name=='open': ret=0xffffffff  # the child's oom_score_adj write: refused here, so nothing is written
        elif name=='fork': ret=self.forked
        elif name=='setenv': self.env=(self.text(a),self.text(b),c); ret=0  # a card game's Q2_POPUP, in the child
        elif name=='execl':
            sp=u.reg_read(UC_MIPS_REG_SP); e=self.get(sp+16)  # a volume argv, then its terminator
            if not c: self.execs.append((self.text(a),self.text(b))); ret=-1  # no arguments: the terminator third
            else: self.execs.append((self.text(a),self.text(b),self.text(c),self.text(d),e and (self.text(e),self.get(sp+20)))); ret=-1
        elif name=='waitpid': assert a==self.forked and c==1; ret=self.waited
        elif name=='socket': assert (a,b,c) in ((1,1,0),(1,2,0)); self.sock_type=b; ret=7  # AF_UNIX, MIPS SOCK_DGRAM 1 or SOCK_STREAM 2
        elif name=='connect': assert self.text(b+2)=='/tmp/q2adb-ui' and self.sock_type==2, 'adbd listens on a stream socket (MIPS SOCK_STREAM is 2)'; ret=0xffffffff  # adbd's UI socket (a dev build's usb_ui.c auth_poll): not listening
        elif name=='sendto':
            sp=u.reg_read(UC_MIPS_REG_SP); to=self.get(sp+16)
            self.sent.append((a,bytes(self.u.mem_read(b,c)).decode('latin1'),c,d,self.get(to)&0xffff,self.text(to+2),self.get(sp+20)))
        elif name=='canvas_draw_text':
            sp=u.reg_read(UC_MIPS_REG_SP)
            self.lines.append((''.join(chr(self.get(b+4*j)) for j in range(c)),signed(d),signed(self.get(sp+16)),
                               self.get(self.lcd+O['LCD_TEXT_COLOR']),self.font))
        self.calls.append((name,a,b,c))
        if name=='exit':
            for i,r in enumerate(SAVED): u.reg_write(r,0x12340000+i)
            u.reg_write(UC_MIPS_REG_V0,0);u.reg_write(UC_MIPS_REG_SP,0x7000f000);u.reg_write(UC_MIPS_REG_PC,0x1000000);return
        self.returns(ret,clobber=True)

# Games (patch/games.c, iPod): Home's Games row, always there, opens a chooser of the games the build ships: the
# engine's (Bricks, Cabin Solitaire), games.c's Sudoku, Vortex, Solitaire and Snake left out of the payload by peq.h's
# GAME_* until the games in progress are finished. Their checks below run again once peq.h turns them all back on.
# A game's keys never reach stock's filter or the media keys; Return goes game -> chooser -> Library; closing frees
# the state.
OWN_GAMES=re.findall(r'^#define GAME_(\w+) (\d)',(ROOT/'patch/peq.h').read_text(),re.M)
OWN=[n.capitalize() for n,on in OWN_GAMES if on=='1']; SHIPPED=['Games',*OWN,'Bricks','Cabin Solitaire']
m=CardMachine({},{})
G_SCREEN,G_PICKING,G_PAUSED,G_CELL,G_CUR,G_VX=36,44,56,76,157,164
# labels(w): the texts of the hscroll_labels under w, in node order
def labels(w): return [m.nodes[x].get('text') for x in m.nodes if m.nodes[x]['type']=='hscroll_label' and m.get(x+O['W_PARENT']) in m.nodes and x in m.nodes[m.get(x+O['W_PARENT'])]['children'] and under(x,w)]
def under(x,w):
    while x and x!=w: x=m.get(x+O['W_PARENT']) if m.get(x+O['W_PARENT']) in m.nodes else 0
    return x==w
def games_open():  # Home's Games row (2026-11-19; the Library's until then): games.c's games_open, its click
    m.top=m.node('window','home_page',[])
    assert m.call(address=payload_syms['games_open'],args=(0,0,0,0),gap=0,count=5_000_000)==0
    m.page=m.top; assert m.nodes[m.page]['name']=='games_page' and m.nodes[m.page]['style:normal:bg_color']==-0x1000000
    return m.nodes[m.page]['children']
sheet,chooser=games_open(); G=m.get(payload_syms['g'])
assert labels(chooser)==SHIPPED and not m.nodes[sheet]['visible']
rows=m.nodes[m.find('scroll_view',chooser)]['children']
# The wheel stops at both ends of the real chooser, however long the pause.
w=m.find('scroll_view',chooser); m.paint(w,gap=0)
LAST=len(SHIPPED)-2  # the chooser's last row
for _ in range(6):
    if m.selected(w)!=LAST: assert m.call(gap=1000)==11; m.paint(w,gap=0)
assert m.selected(w)==LAST
for gap in (100,300,1000): assert m.call(gap=gap)==11 and m.selected(w)==LAST; m.paint(w,gap=0)
for _ in range(LAST): assert m.call(PREV,gap=1000)==11; m.paint(w,gap=0)
assert m.selected(w)==0
for gap in (100,300,1000): assert m.call(PREV,gap=gap)==11 and m.selected(w)==0; m.paint(w,gap=0)
passed()
def pick(i):
    f,ctx=m.handler(rows[i],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.advance(0)
def paint(): m.call(address=HOOKS['widget_on_paint_border'][0],args=(sheet,m.canvas,0,0),gap=0,clear=False,count=50_000_000)
stock=[]; tramp=int(manifest['patch_symbols']['stock_keyup_trampoline'],16)
for a in (tramp,payload_syms['media_key']): m.u.hook_add(UC_HOOK_CODE,lambda u,a,n,_: stock.append(a),begin=a,end=a)
def key(k): r=m.call(k,count=2_000_000); assert r==11 and not stock,(k,stock); return r
byte=lambda o: m.u.mem_read(G+o,1)[0]; word=lambda o: signed(m.get(G+o))
frames=lambda: [t for t in m.timers.values() if t[1]==payload_syms['frame']]
if len(OWN)==4:  # Sudoku, Vortex, Solitaire and Snake, each built in only while it ships
    pick(0); assert m.nodes[sheet]['visible'] and not m.nodes[chooser]['visible'] and word(G_SCREEN)==1; paint()
    key(O['KEY_NEXT']); assert byte(G_CUR)==1
    key(O['KEY_PREV']); key(O['KEY_PREV']); assert byte(G_CUR)==80
    key(O['KEY_FWD_BTN']); assert byte(G_CUR)==8
    key(O['KEY_BACK_BTN']); assert byte(G_CUR)==80; passed()
    # Centre on a blank cell picks its digit with the wheel, Centre writes it and saves; Return cancels; a
    # clue takes no digit. Play/Pause then asks before a new game.
    cells=lambda: bytes(m.u.mem_read(G+G_CELL,81))
    blank=cells().index(0); clue=next(i for i,c in enumerate(cells()) if c&16)
    m.byte(G+G_CUR,clue); key(O['KEY_CENTER']); assert not word(G_PICKING)
    m.byte(G+G_CUR,blank); key(O['KEY_CENTER']); assert word(G_PICKING)
    for _ in range(3): key(O['KEY_NEXT'])
    paint(); key(O['KEY_CENTER']); assert cells()[blank]==3 and not word(G_PICKING)
    m.advance(2000); saved=bytes(m.files['/mnt/data/ringnav-sudoku']); assert len(saved)==92 and saved[4:85]==cells()
    key(O['KEY_CENTER']); key(O['KEY_NEXT']); key(O['KEY_RETURN']); assert cells()[blank]==3 and not word(G_PICKING) and word(G_SCREEN)==1
    before=cells(); key(O['KEY_PLAY']); assert cells()==before and word(52); paint()
    key(O['KEY_RETURN']); assert cells()==before and not word(52)
    key(O['KEY_RETURN']); m.advance(0); assert word(G_SCREEN)==0 and m.nodes[chooser]['visible'] and not m.nodes[sheet]['visible']
    assert m.key()==11; passed()
    # Vortex: the frame timer and the raw wheel while it plays; the wheel's travel turns the paddle.
    m.page=m.top; pick(1); assert word(G_SCREEN)==2 and len(frames())==1 and m.get(payload_syms['wheel_raw'])==1
    m.word(payload_syms['wheel_travel'],-50&0xffffffff); m.advance(34); assert word(G_VX+16)==0 and m.get(payload_syms['wheel_travel'])==0
    made=lambda *a: [c[0] for c in m.calls if c[0] in a]
    assert made('reset_poweroptions_timer') and made('widget_invalidate_force')
    # Idle, waiting to launch: no repaint and the screen's timeout runs; the ball in play repaints, still
    # leaving the screen to time out; a key the game ignores paints nothing.
    m.advance(500); assert not made('reset_poweroptions_timer','widget_invalidate_force')
    key(O['KEY_CENTER']); m.advance(500); assert word(G_VX)==1 and made('widget_invalidate_force') and not made('reset_poweroptions_timer'); paint()
    key(O['KEY_FWD_BTN']); assert not made('widget_invalidate_force')
    key(O['KEY_PLAY']); assert word(G_PAUSED)==1 and not frames() and m.get(payload_syms['wheel_raw'])==0
    x=word(G_VX+20),word(G_VX+24); m.advance(500); assert (word(G_VX+20),word(G_VX+24))==x; paint()
    key(O['KEY_PLAY']); assert word(G_PAUSED)==0 and len(frames())==1 and m.get(payload_syms['wheel_raw'])==1
    m.byte(syms['g_backlight_status'],0); m.advance(40); m.byte(syms['g_backlight_status'],1)
    assert word(G_PAUSED)==1 and not frames() and m.get(payload_syms['wheel_raw'])==0
    key(O['KEY_RETURN']); assert word(G_PAUSED)==2; key(O['KEY_NEXT']); paint(); key(O['KEY_CENTER']); m.advance(0)
    assert word(G_SCREEN)==0 and not frames() and m.get(payload_syms['wheel_raw'])==0
    assert m.key()==11; passed()  # Return in the chooser after Vortex goes back to the Library
    # Solitaire (piles 0 stock, 1 waste, 2-5 foundations, 6-12 columns; a card suit*13+rank, 64 up): Centre on
    # the stock draws and saves; the wheel walks the piles, Previous/Next jump between the rows; Centre picks a
    # run up and drops it where it fits, turning the card under it; Centre twice sends a card home; Return
    # drops the held run, then leaves.
    G_SOL=220; sol=lambda: bytes(m.u.mem_read(G+G_SOL,336)); n=lambda p: byte(G_SOL+316+p); cur=lambda: byte(G_SOL+329)
    def put(p,cards): m.u.mem_write(G+G_SOL+4+24*p,bytes(cards)); m.byte(G+G_SOL+316+p,len(cards))
    m.page=m.top; pick(2); assert word(G_SCREEN)==3 and cur()==0 and [n(p) for p in range(13)]==[24,0,0,0,0,0,1,2,3,4,5,6,7]; paint()
    key(O['KEY_CENTER']); m.advance(2000); assert (n(0),n(1))==(23,1) and bytes(m.files['/mnt/data/ringnav-solitaire'])==sol()
    key(O['KEY_NEXT']); assert cur()==1; key(O['KEY_FWD_BTN']); assert cur()==7; key(O['KEY_BACK_BTN']); assert cur()==1
    key(O['KEY_PREV']); key(O['KEY_PREV']); assert cur()==12; key(O['KEY_BACK_BTN']); assert cur()==5; passed()
    put(6,[64|13+11]); put(7,[3,64|10]); m.byte(G+G_SOL+329,7)  # Q of hearts; a face-down 4 under the J of spades
    key(O['KEY_CENTER']); assert word(G_PICKING) and word(48)==7; paint()
    put(1,[]); key(O['KEY_FWD_BTN']); key(O['KEY_CENTER']); assert word(G_PICKING) and word(48)==7  # not onto the waste, empty: still held
    key(O['KEY_FWD_BTN']); key(O['KEY_PREV']); key(O['KEY_CENTER']); assert not word(G_PICKING); m.advance(2000)
    assert (n(6),n(7),byte(G_SOL+4+24*6+1),byte(G_SOL+4+24*7))==(2,1,64|10,64|3) and bytes(m.files['/mnt/data/ringnav-solitaire'])==sol()
    put(1,[64|26]); m.byte(G+G_SOL+329,1); key(O['KEY_CENTER']); key(O['KEY_CENTER'])  # the Ace of diamonds, home
    assert not word(G_PICKING) and n(1)==0 and n(2)==1 and byte(G_SOL+4+48)==64|26
    m.byte(G+G_SOL+329,6); key(O['KEY_CENTER']); key(O['KEY_CENTER']); assert not word(G_PICKING) and n(6)==2  # nothing home: put back
    key(O['KEY_CENTER']); key(O['KEY_RETURN']); assert not word(G_PICKING) and word(G_SCREEN)==3
    before_sol=sol(); key(O['KEY_PLAY']); assert word(52); paint(); key(O['KEY_RETURN']); assert not word(52) and sol()==before_sol
    key(O['KEY_RETURN']); m.advance(0); assert word(G_SCREEN)==0 and m.nodes[chooser]['visible']; passed()
    # Snake (the body a ring of cells y*20+x, head last; dir 0 up 1 right 2 down 3 left): its frame timer steps it
    # with side keys only; wheel ticks are consumed without changing the game. One turn waits for the next step. Play/Pause
    # pauses, Return's menu quits; a wall ends it, a record saves Best and says so, Centre plays again.
    G_SN=G_SOL+336+52+12; sn=lambda o: m.get(G+G_SN+o)&0xffff; body=lambda: [sn(2*((sn(800)+i)%400)) for i in range(sn(802))]
    def skey(k): r=m.call(k,gap=0,count=2_000_000); assert r==11 and not stock,(k,stock)  # no time between keys
    sdir=lambda: byte(G_SN+806); sstate=lambda: byte(G_SN+808)
    m.page=m.top; pick(3); assert word(G_SCREEN)==4 and len(frames())==1 and m.get(payload_syms['wheel_raw'])==0
    assert body()==[207,208,209] and sdir()==1 and sstate()==1; paint()
    snake_before=bytes(m.u.mem_read(G+G_SN,820))
    skey(O['KEY_NEXT']); skey(O['KEY_PREV']); assert bytes(m.u.mem_read(G+G_SN,820))==snake_before
    skey(O['KEY_FWD_BTN']); skey(O['KEY_FWD_BTN']); assert sdir()==2
    m.advance(170); assert body()[-1]==229 and sdir()==3 and made('widget_invalidate_force')  # the second turn, waiting
    skey(O['KEY_BACK_BTN']); assert sdir()==3  # waits for the next step
    skey(O['KEY_PLAY']); assert word(G_PAUSED)==1 and not frames(); was=body(); m.advance(500); assert body()==was; paint()
    skey(O['KEY_NEXT']); assert sdir()==3  # no turning while paused
    skey(O['KEY_PLAY']); assert word(G_PAUSED)==0 and len(frames())==1
    m.word(G+G_SN+812,5); m.advance(170*12); assert sstate()==2 and not frames() and body()[-1]==388  # left, then down to the wall
    best=bytes(m.files['/mnt/data/ringnav-snake']); assert len(best)==12 and best[:8]==b'SNK1'+struct.pack('<I',5)
    paint(); skey(O['KEY_CENTER']); assert sstate()==1 and body()==[207,208,209] and len(frames())==1
    skey(O['KEY_BACK_BTN']); assert sdir()==0  # side Previous turns left, without the wheel
    m.advance(170); assert body()[-1]==189
    skey(O['KEY_FWD_BTN']); assert sdir()==1  # side Next turns right
    skey(O['KEY_PLAY']); skey(O['KEY_BACK_BTN']); skey(O['KEY_FWD_BTN']); assert sdir()==1
    skey(O['KEY_PLAY'])
    skey(O['KEY_RETURN']); assert word(G_PAUSED)==2 and word(60)==0
    skey(O['KEY_NEXT']); assert word(60)==1  # the wheel picks in the menu
    skey(O['KEY_PREV']); assert word(60)==0
    skey(O['KEY_BACK_BTN']); assert word(60)==1
    skey(O['KEY_FWD_BTN']); assert word(60)==0
    skey(O['KEY_FWD_BTN']); paint(); skey(O['KEY_CENTER']); m.advance(0)
    assert word(G_SCREEN)==0 and not frames(); passed()
    # Closing frees the state, also mid-game; opening again starts clean and resumes the Sudoku.
    m.close(); assert m.get(payload_syms['g'])==0 and not frames()
    sheet,chooser=games_open(); G=m.get(payload_syms['g']); rows=m.nodes[m.find('scroll_view',chooser)]['children']
    pick(0); assert cells()==before
    key(O['KEY_RETURN']); m.advance(0); pick(2); assert sol()[:-4]==before_sol[:-4]  # and the cursor, kept on leaving
    # Moves save once, SAVE_MS after the first unsaved one, not per move; leaving with nothing new writes nothing.
    saves=[]; m.u.hook_add(UC_HOOK_CODE,lambda u,a,n,_: saves.append(a),begin=syms['rename@GLIBC_2.0'],end=syms['rename@GLIBC_2.0'])
    m.byte(G+G_SOL+329,0)
    for _ in range(10): m.call(O['KEY_CENTER'],gap=100,count=2_000_000)
    assert not saves; m.advance(2000); assert len(saves)==1 and bytes(m.files['/mnt/data/ringnav-solitaire'])==sol()
    key(O['KEY_RETURN']); m.advance(0); pick(0); key(O['KEY_RETURN']); m.advance(0); assert len(saves)==1; pick(2)
    key(O['KEY_RETURN']); m.advance(0); pick(1); assert len(frames())==1
    m.close(); assert m.get(payload_syms['g'])==0 and not frames() and m.get(payload_syms['wheel_raw'])==0; passed()
else:
    m.close(); assert m.get(payload_syms['g'])==0 and not frames(); passed()

# The game engine (patch/engine.c, docs/games-engine.md): its demo scene, which a --dev build lists after Games' own (none shipped now)
# and the engine's games
# with [Q2POD] ENGINEDEMO=1. A frame is one step and the rects it changed; the demo draws into the screen's own buffer,
# only inside the rect being painted. What a frame costs is counted in the payload's instructions and bounded, a little
# over what it measured, so a change cannot quietly make a frame dearer: the engine's budget for a whole frame is
# 2,000,000 (docs/games-engine.md#budgets).
CONFIG['ENGINEDEMO']='1'; m=CardMachine({},{}); del CONFIG['ENGINEDEMO']
sheet,chooser=games_open(); G=m.get(payload_syms['g']); rows=m.nodes[m.find('scroll_view',chooser)]['children']
if not manifest.get('dev'): assert labels(chooser)==SHIPPED; m.close()
else:
    assert labels(chooser)==[*SHIPPED,'Engine Demo']
    eframes=lambda: [t for t in m.timers.values() if t[1]==payload_syms['eng_frame']]
    O_ENG_RECT,O_ENG_STEPS=80,208  # struct eng's rect and steps (patch/engine.c), on MIPS
    ENG_STEP_BOUND,ENG_FRAME_BOUND,ENG_WHOLE_BOUND=600,70_000,780_000  # measured: 499, 61,511 and 703,036
    pick(len(SHIPPED)-1); EN=m.get(payload_syms['en']); assert word(G_SCREEN)==5+len(SHIPPED)-1 and EN and len(eframes())==1 and m.get(payload_syms['wheel_raw'])==1
    FB=0x30000000; m.u.mem_map(FB,0x80000); m.word(m.canvas+O['CANVAS_LCD'],m.lcd)
    for o,v in (('LCD_W',375),('LCD_H',320),('LCD_LINE_LENGTH',1500),('LCD_FORMAT',O['LCD_BGRA8888']),('LCD_OFFLINE_FB',FB)): m.word(m.lcd+O[o],v)
    n=[0]; counter=m.u.hook_add(UC_HOOK_BLOCK,lambda u,a,size,x: n.__setitem__(0,n[0]+size//4),begin=BASE,end=SCRATCH-1)
    pixel=lambda x,y: m.get(FB+4*(y*375+x))
    def eframe(ms=16):  # the frame timer's pass ms after the last: its return, the rects it invalidated, its instructions
        m.now+=ms; m.calls=[]; n[0]=0; r=m.call(address=payload_syms['eng_frame'],args=(0,0,0,0),gap=0,clear=False)
        made=[c for c in m.calls if c[0]=='widget_invalidate_force']; assert all(c[1]==sheet for c in made)
        box=lambda k: [signed(m.get(EN+O_ENG_RECT+16*k+4*j)) for j in range(4)]  # x0 y0 x1 y1, as the step left them
        return r,[(x0,y0,x1-x0,y1-y0) for x0,y0,x1,y1 in map(box,range(len(made)))],n[0]
    def epaint(rect):  # stock paints one changed rect: the canvas clipped to it
        m.clip=rect; n[0]=0; paint(); return n[0]
    SH=320-OLED_G.bar  # the sheet's height, the window's (games.c: client_h)
    whole=epaint((0,0,375,320)); assert pixel(187,SH-44)==0xffffffff and pixel(5,100)==0xff000000 and pixel(187,70)==0xff2160d8  # the paddle D_PAD_UP above the end, the ball
    assert pixel(187,SH+4)==0  # the sheet ends at its height: nothing below it
    r,rects,cost=eframe(); assert r==8 and rects==[(180,63,15,15),(182,65,15,15)],rects
    before=bytes(m.u.mem_read(FB,375*320*4)); part=[epaint(x) for x in rects]; after=bytes(m.u.mem_read(FB,375*320*4))
    changed={(k//4%375,k//4//375) for k in range(0,len(after),4) if after[k:k+4]!=before[k:k+4]}
    assert changed and all(180<=x<197 and 63<=y<80 for x,y in changed)  # the ball moved; nothing outside its two rects
    # A second of play, the wheel turning the paddle every frame: the dearest frame, its step and its rects' paints.
    worst=step_worst=0; steps=m.get(EN+O_ENG_STEPS)
    for k in range(60):
        m.word(payload_syms['wheel_travel'],(-3 if k<30 else 3)&0xffffffff)
        r,rects,cost=eframe(17 if k%3==0 else 16); assert r==8 and 2<=len(rects)<=5
        total=cost+sum(epaint(x) for x in rects); worst=max(worst,total); step_worst=max(step_worst,cost)
    assert m.get(EN+O_ENG_STEPS)==steps+60  # a step a frame, whatever the millisecond clock made of each
    r,rects,late=eframe(50); assert m.get(EN+O_ENG_STEPS)==steps+63  # a late frame catches up
    assert whole<ENG_WHOLE_BOUND and worst<ENG_FRAME_BOUND and step_worst<ENG_STEP_BOUND,(whole,worst,step_worst)
    print(f'Game engine on MIPS: the demo scene {step_worst} instructions for a frame\'s step at most, {worst} with its changed rects painted, {whole} to paint the whole sheet (payload only; stock\'s text and flush mocked)')
    m.u.hook_del(counter)
    # Return pauses under Games' menu: no timer, the wheel ticks again; the screen going off pauses too; Quit frees it.
    key(O['KEY_RETURN']); assert word(G_PAUSED)==2 and not eframes() and m.get(payload_syms['wheel_raw'])==0; m.clip=(0,0,375,320); paint()
    key(O['KEY_RETURN']); assert word(G_PAUSED)==0 and len(eframes())==1 and m.get(payload_syms['wheel_raw'])==1
    m.byte(syms['g_backlight_status'],0); m.advance(20); m.byte(syms['g_backlight_status'],1)
    assert word(G_PAUSED)==1 and not eframes() and m.get(payload_syms['wheel_raw'])==0
    key(O['KEY_RETURN']); key(O['KEY_NEXT']); key(O['KEY_CENTER']); m.advance(0)
    assert word(G_SCREEN)==0 and m.get(payload_syms['en'])==0 and not eframes()
    m.close(); assert m.get(payload_syms['g'])==0
passed()

# Bricks (patch/game_bricks.c, docs/games-engine.md#bricks), the engine's first game, in every build: a scripted second
# of play, then the dearest frames it can have, each made on purpose: the keystone taking its six neighbours in one
# frame, and each frame of the next level's fade. Bounded in the payload's instructions a little over what they
# measured, under the brief's 600,000 a frame (docs/games-engine.md#budgets).
m=CardMachine({},{})
sheet,chooser=games_open(); G=m.get(payload_syms['g']); rows=m.nodes[m.find('scroll_view',chooser)]['children']
eframes=lambda: [t for t in m.timers.values() if t[1]==payload_syms['eng_frame']]
O_ENG_RECT,O_ENG_STATE=80,216  # struct eng's rect and the game's state after it (patch/engine.c), on MIPS
BR_X,BR_Y,BR_SPD,BR_ANG,BR_PAD,BR_TOP,BR_MODE,BR_T,BR_LEVEL,BR_LEFT,BR_SCORE,BR_CELL=0,4,8,12,16,24,32,36,40,52,68,76  # struct bricks
ENG_RECTS=int(re.search(r'^#define ENG_RECTS (\d+)',(ROOT/'patch/engine.h').read_text(),re.M)[1])  # a frame's rects at most (engine.c eng_dirty)
BR_STEP_BOUND,BR_FRAME_BOUND,BR_KEY_BOUND,BR_FADE_BOUND,BR_WHOLE_BOUND=2_500,250_000,200_000,270_000,1_450_000  # measured on OLED's 262 px sheet: 2,157, 216,412 (a frame's play can break a brick, move the paddle and score at once), 174,268, 233,143 and 1,177,926
# Bricks seeds its layouts from the shared RNG (playback.c rand_below), which whatever ran before draws from: set
# to what it held here when the bounds were measured (Games' own four ran first then), so the layouts are those.
m.word(payload_syms['rng'],0x9c3d619b)
pick(len(OWN)); EN=m.get(payload_syms['en']); BR=EN+O_ENG_STATE
assert word(G_SCREEN)==5 and EN and len(eframes())==1 and m.get(payload_syms['wheel_raw'])==1 and m.get(BR+BR_MODE)==0
FB=0x30000000; m.u.mem_map(FB,0x80000); m.word(m.canvas+O['CANVAS_LCD'],m.lcd)
for o,v in (('LCD_W',375),('LCD_H',320),('LCD_LINE_LENGTH',1500),('LCD_FORMAT',O['LCD_BGRA8888']),('LCD_OFFLINE_FB',FB)): m.word(m.lcd+O[o],v)
n=[0]; counter=m.u.hook_add(UC_HOOK_BLOCK,lambda u,a,size,x: n.__setitem__(0,n[0]+size//4),begin=BASE,end=SCRATCH-1)
pixel=lambda x,y: m.get(FB+4*(y*375+x))
def eframe(ms=16):  # the frame timer's pass: the rects it invalidated, its instructions
    m.now+=ms; m.calls=[]; n[0]=0; assert m.call(address=payload_syms['eng_frame'],args=(0,0,0,0),gap=0,clear=False)==8
    made=[c for c in m.calls if c[0]=='widget_invalidate_force']; assert all(c[1]==sheet for c in made)
    box=lambda k: [signed(m.get(EN+O_ENG_RECT+16*k+4*j)) for j in range(4)]
    return [(x0,y0,x1-x0,y1-y0) for x0,y0,x1,y1 in map(box,range(len(made)))],n[0]
def epaint(rect): m.clip=rect; n[0]=0; paint(); return n[0]
def eplay(rects,cost): return cost+sum(epaint(x) for x in rects)
cell_x=lambda r,c: 37.5+(r&1)*10+20*c
cells=lambda: bytes(m.u.mem_read(BR+BR_CELL,112))
lattice=[(r,c) for r in range(7) for c in range(16-(r&1))]
whole=epaint((0,0,375,320)); top=m.get(BR+BR_TOP); pad=m.get(BR+BR_PAD)
assert pixel(pad,top+4)==0xffffffff and pixel(5,120)==0xff000000
key_at=next(r*16+c for r,c in lattice if cells()[r*16+c]==3); assert pixel(int(cell_x(key_at//16,key_at%16)),48+17*(key_at//16))==0xff2160d8
eframe(); rects,cost=eframe(); assert rects==[]  # ready and still, once the first frame painted the start: nothing to paint
key(O['KEY_CENTER']); eframe(); assert m.get(BR+BR_MODE)==1  # the press reaches the next step: launched
# A second of play, the wheel turning the paddle every frame.
worst=step_worst=0; broke=cells()
for k in range(60):
    m.word(payload_syms['wheel_travel'],(-3 if k<30 else 3)&0xffffffff)
    rects,cost=eframe(17 if k%3==0 else 16); assert 1<=len(rects)<=ENG_RECTS,rects  # the frame cost bounds below hold whatever their number
    worst=max(worst,eplay(rects,cost)); step_worst=max(step_worst,cost)
# The keystone with all six neighbours: the ball under it, rising at top speed.
m.u.mem_write(BR+BR_CELL,bytes(1 if (i//16,i%16) in lattice else 0 for i in range(112)))
m.u.mem_write(BR+BR_CELL+3*16+7,b'\3'); m.word(BR+BR_LEFT,109)
for o,v in ((BR_X,int(187.5*65536)),(BR_Y,(48+51+15)*65536),(BR_ANG,768),(BR_SPD,5*65536),(BR_MODE,1)): m.word(BR+o,v)
epaint((0,0,375,320)); rects,cost=eframe(); keystone=eplay(rects,cost)
assert m.get(BR+BR_LEFT)==102 and sum(cells()[i]==0 for i,_ in enumerate(cells()) if (i//16,i%16) in lattice)==7 and len(rects)<=4,rects
# The level's end: its last cell, then the next lattice's fade, every frame of it.
m.u.mem_write(BR+BR_CELL,bytes(112)); m.u.mem_write(BR+BR_CELL+6*16+7,b'\1'); m.word(BR+BR_LEFT,1)
for o,v in ((BR_X,int(177.5*65536)),(BR_Y,(48+102+14)*65536),(BR_ANG,768),(BR_SPD,2*65536),(BR_MODE,1)): m.word(BR+o,v)  # under it
rects,cost=eframe(); fade=eplay(rects,cost); assert m.get(BR+BR_MODE)==2 and m.get(BR+BR_LEFT)>=12
for k in range(20):
    rects,cost=eframe(); assert len(rects)<=4; fade=max(fade,eplay(rects,cost))
assert m.get(BR+BR_MODE)==0 and m.get(BR+BR_LEVEL)>=2
whole=max(whole,epaint((0,0,375,320)))
assert whole<BR_WHOLE_BOUND and worst<BR_FRAME_BOUND and step_worst<BR_STEP_BOUND and keystone<BR_KEY_BOUND and fade<BR_FADE_BOUND,(whole,worst,step_worst,keystone,fade)
print(f'Bricks on MIPS: {step_worst} instructions for a frame\'s step at most, {worst} with its changed rects painted, {keystone} for the keystone\'s frame, {fade} for the dearest frame of a fade, {whole} to paint the whole sheet (payload only; stock\'s text and flush mocked)')
m.u.hook_del(counter)
# Return pauses under Games' menu; Quit frees it.
key(O['KEY_RETURN']); assert word(G_PAUSED)==2 and not eframes() and m.get(payload_syms['wheel_raw'])==0
key(O['KEY_NEXT']); key(O['KEY_CENTER']); m.advance(0)
assert word(G_SCREEN)==0 and m.get(payload_syms['en'])==0 and not eframes()
m.close(); assert m.get(payload_syms['g'])==0
passed()

# Cabin Solitaire (patch/game_solitaire.c, docs/games-engine.md#cabin-solitaire), the engine's second game: it opens
# over the whole screen (ENG_FULL), its art read from the root filesystem, a saved table offered on its start board.
# Then the frames it can make, each made on purpose: the whole sheet (the start board, then a table), the deal, a
# scripted second of play (the cursor, a card picked up and hovered, dropped, undone, a draw), the dearest move there
# is (a king-to-ace run of 13 at the tightest fan, lifted to an empty column), the auto-finish and the win board's
# drop. Bounded in the payload's instructions a little over what they measured, under the brief's 600,000 a frame.
SOL_ART,SOL_SAVE='/usr/share/q2games/solitaire.bin','/mnt/data/game-solitaire'
SO_MODE,SO_SEL,SO_ART,SO_NSRC,SO_CUR,SO_HAND,SO_SRC=256,260,264,268,272,276,296  # struct sol (patch/game_solitaire.c)
SO_FAN_U,SO_NFL,SO_T=544,1604,1648
SOL_WHOLE_BOUND,SOL_FRAME_BOUND,SOL_RUN_BOUND,SOL_DEAL_BOUND,SOL_AUTO_BOUND,SOL_WIN_BOUND=1,1,1,1,1,1
def sol_table(piles,waste=(),found=(0,0,0,0),draw3=0,stock=True):
    """A sealed save of a table, the game in progress: piles as (face-down count, face-up cards), cards as
    suit*13+rank (hearts, spades, diamonds, clubs; ace 0); what is left over is the stock (or face down in the
    last column, stock=False)."""
    rec=bytearray(244); used=set(waste)|{k*13+r for k in range(4) for r in range(found[k])}|{c for _,up in piles for c in up}
    spare=[c for c in range(52) if c not in used]
    for k,(down,up) in enumerate(piles):
        col=[spare.pop() for _ in range(down)]+[c|0x40 for c in up]
        rec[4+20*k:4+20*k+len(col)]=bytes(col); rec[144+k]=len(col)
    if not stock: assert not spare,spare
    rec[151:151+len(spare)]=bytes(spare); rec[203]=len(spare)
    rec[175:175+len(waste)]=bytes(waste); rec[204]=len(waste); rec[199:203]=bytes(found); rec[205]=draw3; rec[206]=1
    struct.pack_into('<IIIII',rec,212,1,23,754000,0,1); struct.pack_into('<I',rec,0,0x31534343)
    h=2166136261
    for c in rec[:240]: h=((h^c)*16777619)&0xffffffff
    struct.pack_into('<I',rec,240,h); return rec
H,S,D,CL=0,13,26,39  # a suit's first card
DEFAULT=sol_table([(0,[S+12]),(1,[D+6]),(2,[H+11,CL+10]),(3,[D+9,CL+8,H+7]),(4,[S+4]),(3,[H+5,CL+4,D+3]),(6,[S+8])],
                  [CL+3,D+10,S+6],(3,1,3,2))
m=CardMachine({},{SOL_ART:bytearray((ROOT/'assets/games/solitaire.bin').read_bytes()),SOL_SAVE:bytearray(DEFAULT)})
sheet,chooser=games_open(); G=m.get(payload_syms['g']); rows=m.nodes[m.find('scroll_view',chooser)]['children']
assert labels(chooser)==SHIPPED
FB=0x30000000; m.u.mem_map(FB,0x80000); m.word(m.canvas+O['CANVAS_LCD'],m.lcd)
for o,v in (('LCD_W',375),('LCD_H',320),('LCD_LINE_LENGTH',1500),('LCD_FORMAT',O['LCD_BGRA8888']),('LCD_OFFLINE_FB',FB)): m.word(m.lcd+O[o],v)
n=[0]; counter=m.u.hook_add(UC_HOOK_BLOCK,lambda u,a,size,x: n.__setitem__(0,n[0]+size//4),begin=BASE,end=SCRATCH-1)
pixel=lambda x,y: m.get(FB+4*(y*375+x))
def sopen():  # Games' row for Cabin Solitaire: its state on the heap
    pick(len(OWN)+1); en=m.get(payload_syms['en']); assert word(G_SCREEN)==6 and en; return en,en+O_ENG_STATE
def squit():  # Return, then Quit: the game paused (saved) and closed
    key(O['KEY_RETURN']); key(O['KEY_NEXT']); key(O['KEY_CENTER']); m.advance(0); assert word(G_SCREEN)==0 and m.get(payload_syms['en'])==0
def sframe(ms=16):  # the frame timer's pass: the rects it invalidated, its instructions (the step and the marks)
    m.now+=ms; m.calls=[]; n[0]=0; m.call(address=payload_syms['eng_frame'],args=(0,0,0,0),gap=0,clear=False)
    made=[c for c in m.calls if c[0]=='widget_invalidate_force']; assert all(c[1]==sheet for c in made)
    box=lambda k: [signed(m.get(EN+O_ENG_RECT+16*k+4*j)) for j in range(4)]
    return [(x0,y0,x1-x0,y1-y0) for x0,y0,x1,y1 in map(box,range(len(made)))],n[0]
SLOG=[]  # each frame splay made: its number, rects and cost
SWITCH=[0]  # the dearest frame that changed the game's screen (the start board, the deal, the win): the whole sheet's bound
def splay(frames,keys={}):  # frames of play, keys[k] pressed before frame k: the dearest frame, painted
    worst=0
    for k in range(frames):
        if k in keys: key(keys[k])
        mode=m.get(SO+SO_MODE); rects,cost=sframe(17 if k%3==0 else 16); assert len(rects)<=ENG_RECTS,rects
        total=cost+sum(epaint(x) for x in rects); SLOG.append((k,rects,total))
        if m.get(SO+SO_MODE)!=mode: SWITCH[0]=max(SWITCH[0],total)
        else: worst=max(worst,total)
    return worst
EN,SO=sopen()
assert m.get(sheet+O['W_Y'])==signed(-m.get(m.page+O['W_Y'])) and m.get(sheet+O['W_H'])==320  # the whole screen
assert m.get(SO+SO_ART)==1 and m.get(SO+SO_MODE)==0 and m.u.mem_read(SO+SO_MODE,1)[0]==0  # the start board, the art read
whole_start=epaint((0,0,375,320)); assert pixel(187,50)!=pixel(187,200)  # the board's title on wood, over the felt
key(O['KEY_NEXT']); key(O['KEY_NEXT']); sframe(); key(O['KEY_CENTER']); sframe()  # Resume, the start board's third row
assert m.get(SO+SO_MODE)==2 and m.get(SO+SO_NSRC)>0
whole_table=epaint((0,0,375,320)); assert pixel(19+21,88+20)&0xffffff!=0x1b5c3e  # a card in the first column
# A second of play: the cursor round, a card picked up, hovered to its other place, dropped, undone, a draw.
play=splay(60,{0:O['KEY_NEXT'],3:O['KEY_PREV'],6:O['KEY_CENTER'],10:O['KEY_NEXT'],20:O['KEY_CENTER'],32:O['KEY_BACK_BTN'],46:O['KEY_PLAY']})
assert m.get(SO+SO_HAND)==0
squit()
# The dearest move: a king to an ace over six face-down cards (the fan at its tightest, 11 px) lifted to an empty column.
m.files[SOL_SAVE]=bytearray(sol_table([(0,[]),(1,[S+1]),(1,[D+1]),(1,[S+2]),(1,[D+2]),(0,[S+3]),
                                       (6,[(CL if i%2==0 else H)+12-i for i in range(13)])],stock=True))
EN,SO=sopen(); key(O['KEY_NEXT']); key(O['KEY_NEXT']); sframe(); key(O['KEY_CENTER']); sframe()
srcs=[tuple(m.u.mem_read(SO+SO_SRC+2*k,2)) for k in range(m.get(SO+SO_NSRC))]; assert (12,13) in srcs,srcs
assert m.get(SO+SO_FAN_U+4*6)==11
m.word(SO+SO_CUR,srcs.index((12,13)))
run=splay(30,{0:O['KEY_CENTER'],12:O['KEY_CENTER']})
assert [m.u.mem_read(SO+4+20*k,1)[0] for k in (0,)]==[(CL+12)|0x40] and m.get(SO+SO_HAND)==0  # the king on the empty column
squit()
# The deal, from the start board with no game to resume.
del m.files[SOL_SAVE]; EN,SO=sopen(); epaint((0,0,375,320))
dealt=splay(80,{0:O['KEY_PLAY']}); assert m.get(SO+SO_MODE)==2
squit()
# The auto-finish, every card face up; then the win board's drop.
m.files[SOL_SAVE]=bytearray(sol_table([(0,[S+12,H+11,S+10]),(0,[H+12,CL+11]),(0,[]),(0,[D+12,S+11,D+10,S+9,S+8]),
                                       (0,[CL+12,D+11,CL+10,H+9,CL+8,CL+7]),(0,[]),(0,[H+10,CL+9,D+9])],
                                      found=(9,8,9,7),stock=False))
EN,SO=sopen(); key(O['KEY_NEXT']); key(O['KEY_NEXT']); sframe(); key(O['KEY_CENTER']); sframe()
auto=splay(4*19+12,{0:O['KEY_PLAY']}); assert m.get(SO+SO_MODE)==4
won=splay(60); assert m.get(SO+SO_T)>24+10
whole_won=epaint((0,0,375,320))
squit(); m.close(); assert m.get(payload_syms['g'])==0
m.u.hook_del(counter)
print(f'Cabin Solitaire on MIPS: the whole sheet {whole_start} (start board), {whole_table} (a table), {whole_won} (won), a screen change {SWITCH[0]}; the dearest frame of a second of play {play}, of a 13-card run lifted and dropped {run}, of the deal {dealt}, of the auto-finish {auto}, of the win board\'s showing {won} (payload only; stock\'s text and flush mocked)')
assert max(whole_start,whole_table,whole_won,SWITCH[0])<SOL_WHOLE_BOUND and play<SOL_FRAME_BOUND and run<SOL_RUN_BOUND and dealt<SOL_DEAL_BOUND and auto<SOL_AUTO_BOUND and won<SOL_WIN_BOUND
passed()

# Videos (patch/videos.c, docs/internals.md#videos): the card's Videos folder (case aside) is browsed
# in Folder view; a video's row stops the music and starts /usr/bin/q2video with
# the headphone DAC's PCM and the path as plain argv, never through a shell.
R='/mnt/mmc/VIDEOS'
tree={'/mnt/mmc':[('Music',4),('VIDEOS',4)],R:[('b.MKV',8),('a.mp4',8),('notes.txt',8),('._a.mp4',8),('Trip',4)],R+'/Trip':[('c.avi',8)]}
m=CardMachine(tree,{})
view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)]); m.top=m.node('window','localmusic_page',[view])
assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
def icon_of(r):  # a row's button's first child's image, '' for a stock row with none
    kids=m.nodes[r].get('children') or [0]; b=m.nodes.get(kids[0],{}); c=(b.get('children') or [0])[0]
    return m.nodes.get(c,{}).get('image','')
button=next(m.nodes[r]['children'][0] for r in m.nodes[view]['children'] if icon_of(r)=='local_videos')
icon,label,into=m.nodes[button]['children']
assert m.nodes[label]['text']=='Videos' and len(m.nodes[view]['children'])==17,len(m.nodes[view]['children'])  # no Search or Games
f,ctx=m.handler(button,O['EVT_CLICK']); m.calls=[]
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and [m.text(c[1]) for c in m.calls if c[0]=='navigator_to']==['folder_page']; m.page=m.node('window','folder_page'); passed()
# Folder view's video (video_click) starts the same q2video, with no Videos page open; anything else is left to stock.
for path,ok in ((R+'/b.MKV',1),(R+'/notes.txt',0),(R+'/._a.mp4',0)):
    v=CardMachine(tree,{}); assert v.call(address=payload_syms['video_click'],args=(v.string(path),0,0,0),gap=0)==ok,path
    v=CardMachine(tree,{}); v.forked=0; v.call(address=payload_syms['video_click'],args=(v.string(path),0,0,0),gap=0)  # the child
    assert v.execs==([('/usr/bin/q2video','/usr/bin/q2video','plughw:0,0',path,0)] if ok else []),path
passed()
def play(path):  # Folder view's row (video_click), with the child run as the fork's
    m.calls=[]; m.call(address=payload_syms['video_click'],args=(m.string(path),0,0,0),gap=0)
    return [c[0] for c in m.calls]
# The headphone output is readied as stock's player_start readies it (config_outputchannel, PCM
# mode, unmuted, the volume) before q2video starts; a DAC check_dacoff_state powered off comes on with it. Bluetooth (way 1) plays on hciplayer's
# plug:bluealsa with the volume for the helper's soft volume. A USB DAC (2) plays on "usb" (q2video
# finds its card), through plughw: with the volume marked h (the DAC's own control, or a fixed USB volume),
# or plain when the USB volume is variable and the DAC has no control (USB_MIXER -2: hciplayer's soft volume).
m.forked=0; m.word(syms['g_dacoff_time'],0xffffffff); m.byte(syms['g_headset_output'],1); names=play(R+'/b.MKV')
take=[n for n in names if n in ('player_stop','config_outputchannel','mclSetPcmMode','mclSetMute','device_set_volume','fork')]
assert take==['player_stop','config_outputchannel','mclSetPcmMode','mclSetMute','device_set_volume','fork'] and m.execs[-1][2:5]==('plughw:0,0',R+'/b.MKV',0)
assert ('config_outputchannel',1,2) in [c[:3] for c in m.calls]
m.word(syms['g_dacoff_time'],5); m.byte(syms['g_volume'],42)
m.way=1; names=play(R+'/Trip/c.avi'); assert 'mclSetDacPwr' not in names and 'config_outputchannel' not in names and m.execs[-1][2:]==('plug:bluealsa',R+'/Trip/c.avi',('42',0))
m.way=2; m.byte(syms['g_usbvol_mode'],0); m.word(O['USB_MIXER'],0xfffffffe)
assert 'mclSetDacPwr' not in play(R+'/Trip/c.avi') and m.execs[-1][2:]==('usb',R+'/Trip/c.avi',('h42',0))
m.byte(syms['g_usbvol_mode'],1); m.word(O['USB_MIXER'],0); play(R+'/Trip/c.avi'); assert m.execs[-1][2:]==('usb',R+'/Trip/c.avi',('h42',0))
m.word(O['USB_MIXER'],0xfffffffe); play(R+'/Trip/c.avi'); assert m.execs[-1][2:]==('usb',R+'/Trip/c.avi',('42',0))
passed()
# Playing: no key or touch reaches the UI; a key's release goes to the player's socket as a datagram:
# Play/Pause pauses, the side buttons seek, Return quits.
m.way=0; m.forked=4242; play(R+'/Trip/c.avi')
# The helper starts at the output's level: the first poll sends nothing (no volume bar flash); a change sends v then g.
def vpoll(): m.now+=500; m.sent=[]; m.call(address=payload_syms['video_poll'],args=(0,0,0,0),gap=0,clear=False); return [x[1] for x in m.sent]
assert vpoll()==[]; m.byte(syms['g_volume'],43); assert vpoll()==['v+','g+']; passed()
inp=HOOKS['window_manager_dispatch_input_event'][0]; m.handlers[inp+12]='stock_input'
def event(kind,key=0):
    m.calls=[]; m.sent=[]
    assert m.call(key,address=inp,args=(m.wm,m.event,0,0),event_type=kind,gap=0,clear=False)==0
    return [c[0] for c in m.calls if c[0]!='time_now_ms']  # the hook times the idle, not input
for key,c in ((O['KEY_PLAY'],'p'),(O['KEY_FWD_BTN'],'f'),(O['KEY_BACK_BTN'],'b'),(O['KEY_RETURN'],'q')):
    assert 'stock_input' not in event(O['EVT_KEY_UP'],key) and m.sent==[(7,c,1,0x40,1,'/tmp/q2video.sock',110)]
assert event(0x110,O['KEY_CENTER'])==[] and event(O['EVT_POINTER_DOWN'])==[] and not m.sent; passed()
# The wheel is the volume, as stock's volume dialog steps it: 1 a tick through device_set_volume, saved as
# PLAYSET VOLUME, up to g_maxvolume (and 100) and down to 0; each tick sends v and the volume, at an end too.
def key(k):
    names=event(O['EVT_KEY_UP'],k); assert 'stock_input' not in names and m.sent[0][2]==len(m.sent[0][1])
    assert len(m.sent)==(2 if m.sent[0][1].startswith('v') else 1)
    if len(m.sent)==2: assert m.sent[1][1]=='g'+m.sent[0][1][1]
    vol=[c[1:3] for c in m.calls if c[0]=='device_set_volume']; saved=[(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']
    assert len(vol)==len(saved)<=1 and all(v==(s[0],1) and s[1:]==('PLAYSET','VOLUME') for v,s in zip(vol,saved))
    return m.sent[0][1],vol and vol[0][0]
m.byte(syms['g_volume'],50); m.byte(syms['g_maxvolume'],52)
assert [key(O['KEY_NEXT']) for _ in range(3)]==[('v\x33',51),('v\x34',52),('v\x34',[])] and m.u.mem_read(syms['g_volume'],1)[0]==52
m.byte(syms['g_maxvolume'],100); m.byte(syms['g_volume'],100); assert key(O['KEY_NEXT'])==('v\x64',[])
m.byte(syms['g_volume'],1); assert [key(O['KEY_PREV']) for _ in range(2)]==[('v\0',0),('v\0',[])]; passed()
# Centre toggles the wheel to seeking (s, then f and b, no volume) and back (v); seeking also ends
# SCRUB_MS after the last tick, on the UI loop's poll.
m.handlers[HOOKS['main_loop_sleep_default'][0]+12]='stock_sleep'
def poll(ms):
    m.now+=ms; m.call(address=HOOKS['main_loop_sleep_default'][0],args=(0x1234,0,0,0),gap=0,clear=False)
m.byte(syms['g_volume'],30)
assert key(O['KEY_CENTER'])==('s',[]) and key(O['KEY_NEXT'])==('f',[]) and key(O['KEY_PREV'])==('b',[])
poll(O['SCRUB_MS']-1); assert key(O['KEY_NEXT'])==('f',[]) and key(O['KEY_FWD_BTN'])==('f',[]) and key(O['KEY_PLAY'])==('p',[])
poll(O['SCRUB_MS']-1); assert key(O['KEY_NEXT'])==('f',[]); poll(O['SCRUB_MS']); assert key(O['KEY_NEXT'])==('v\x1f',31)
assert key(O['KEY_CENTER'])==('s',[]) and key(O['KEY_CENTER'])==('v\x1f',[]) and key(O['KEY_PREV'])==('v\x1e',30); passed()
# Nor does the window manager paint over it; the screen, standby and DAC power-off timeouts are held off.
vt=m.alloc(16); m.word(m.wm+0x94,vt); m.word(vt+0xc,0x400100); m.handlers[0x400100]='wm_vt_paint'
def wm_paint():
    m.calls=[]; m.call(address=WM_PAINT_LEAF[1],args=(m.wm,0,0,0),gap=0,clear=False); return [c[0] for c in m.calls]
assert wm_paint()==[]
sleep_hook=HOOKS['main_loop_sleep_default'][0]; m.handlers[sleep_hook+12]='stock_sleep'
m.calls=[]; m.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0,clear=False)
assert ('reset_poweroptions_timer',1,1,1) in [c[:4] for c in m.calls] and m.get(syms['g_dacoff_time'])==0 and 'widget_invalidate_force' not in [c[0] for c in m.calls]
# Its end: the socket closes, the whole screen repaints once, and the UI has its input and paint again.
m.waited=4242; m.calls=[]; m.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0,clear=False)
assert ('close',7) in [c[:2] for c in m.calls] and ('widget_invalidate_force',m.wm,0) in [c[:3] for c in m.calls]
assert 'stock_input' in event(O['EVT_KEY_UP'],O['KEY_RETURN']) and not m.sent and wm_paint()==['time_now_ms','wm_vt_paint']  # the frame's time for its glyphs (ringnav_glyph_stamp)
m.calls=[]; m.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0,clear=False); assert 'waitpid' not in [c[0] for c in m.calls]
passed()

# Spotify (patch/spotify.c, docs/internals.md#spotify): librespot from the card's .spotify folder, its
# state from the q2-librespot fork's --status-file, its commands to --control-socket.
STATE,SOCK='/tmp/q2-librespot.state','/tmp/q2-librespot.sock'
class SpotMachine(CardMachine):
    """CardMachine's card and files, plus librespot's side: system() starts, the monotonic clock is
    self.now, the player's status, and the thumbnailer (a .jpg it makes is a file)."""
    def __init__(self,files):
        super().__init__({},files)
        for n in ('system@GLIBC_2.0','clock_gettime@GLIBC_2.2','toolsThumbSpecCover','pthread_mutex_lock@GLIBC_2.0',
                  'pthread_mutex_unlock@GLIBC_2.0','sleep_ms','mclGetPlayStatus','connect@GLIBC_2.0'):
            self.handlers[syms[n]]='s:'+n
        self.alive=True  # librespot's control socket takes a connection (spot_alive)
        for x in ('stream','start_player'):
            tramp=int(manifest['patch_symbols'][f'stock_{x}_trampoline'],16)
            self.handlers[tramp]='stock_'+x; self.u.hook_add(UC_HOOK_CODE,self.hook,begin=tramp,end=tramp)
        self.play=1; self.systems=[]; self.thumbs=[]; self.on_sleep=None
        self.handlers.pop(syms['mclStartPlayer'])  # its hook runs, down to the trampoline
        self.handlers[HOOKS['main_loop_sleep_default'][0]+12]='stock_sleep'
        self.handlers[HOOKS['window_manager_dispatch_input_event'][0]+12]='stock_input'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('s:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='system': self.systems.append(self.text(a))
        elif name=='clock_gettime': assert a in (1,7); self.word(b,self.now//1000); self.word(b+4,self.now%1000*1000000)
        elif name=='toolsThumbSpecCover':
            self.thumbs.append((self.text(a),self.text(b),c,d)); ret=1 if self.text(a) in self.files else 0
            if ret: self.files[self.text(b)]=bytearray(b'jpeg')
        elif name=='sleep_ms':
            self.now+=a
            if self.on_sleep: self.on_sleep()
        elif name=='mclGetPlayStatus': ret=self.play
        elif name=='connect' and self.text(b+2)=='/tmp/q2adb-ui': ret=-1  # adbd's UI socket (a dev build's usb_ui.c auth_poll): not listening
        elif name=='connect': assert self.text(b+2)==SOCK; ret=0 if self.alive else -1
        self.calls.append((name,a,b,c))
        self.returns(ret,clobber=True)
    def state(self,state,position=0,at=None,cover=0,track='t1',title='Song',artist='A, B',album='LP',duration=200000):
        self.files[STATE]=bytearray((f'state={state}\ntrack={track}\ntitle={title}\nartist={artist}\nalbum={album}\n'
            f'duration={duration}\nposition={position}\nat={self.now if at is None else at}\ncover={cover}\n').encode())
    def poll(self,ms=500):  # the page in use: input each pass, so no screen-on idle pass moves the clock
        self.now+=ms; self.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(self.wm,self.event,0,0),gap=0)
        self.calls=[]; self.sent=[]
        self.call(address=HOOKS['main_loop_sleep_default'][0],args=(0x1234,0,0,0),gap=0,clear=False)
        return [c[0] for c in self.calls]
    def key(self,k):
        self.sent=[]; self.calls=[]; return self.call(k,gap=0,clear=False)

# The reentrant clock import resolves tzset's lazy GOT slot once (no shared buffer written), then
# calls localtime_r with the caller's buffer, preserving both arguments and the return address.
from build import run
clock_got=next(int(w[0],16) for line in run('readelf','-AW',B/'stock-demo').splitlines()
               if len(w:=line.split())>=6 and 'UND' in w and w[-1].split('@')[0]=='tzset')
class ClockImportMachine(Machine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='tzset@GLIBC_2.0':
            self.word(clock_got,syms['tzset@GLIBC_2.0']+4+7724)  # loader's resolved tzset
        super().hook(u,address,size,unused)
m=ClockImportMachine(); del m.handlers[payload_syms['peq_lib_localtime_r']]
m.handlers[syms['tzset@GLIBC_2.0']+4]='localtime_r'
now=m.alloc(4); buf=m.alloc(44); m.word(now,m.wall[1])
assert m.call(address=payload_syms['peq_lib_localtime_r'],args=(now,buf,0,0),gap=0)==buf
assert m.get(buf+8)==18 and m.get(buf+4)==14
m.word(clock_got,0); m.clock=(7,30)
assert m.call(address=payload_syms['peq_lib_localtime_r'],args=(now,buf,0,0),gap=0)==buf
assert m.get(buf+8)==7 and m.get(buf+4)==30; passed()

# Every saved timezone index applies its TZ; stock's indices keep their city.
from build import TIMEZONES, TIMEZONE_ORDER, timezone_offset
class TimezoneMachine(Machine):
    def __init__(self):
        super().__init__()
        self.mock('strcpy@GLIBC_2.0')
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='strcpy@GLIBC_2.0': return super().hook(u,address,size,unused)
        a,b=[u.reg_read(r) for r in REGS[:2]]
        self.u.mem_write(a,self.text(b).encode()+b'\0'); self.returns(a,clobber=True)
zones=TimezoneMachine()
assert TIMEZONES[22]==('GMT+8 Shanghai','Asia/Shanghai') and TIMEZONES[6][1]=='CST6' and TIMEZONES[27][1]=='Pacific/Marquesas'
for index, (_, name) in enumerate(TIMEZONES):
    zones.byte(syms['g_time_zone'],index)
    zones.call(address=HOOKS['select_timezone'][0],args=(0,0,0,0),gap=0)
    assert [zones.text(c[1]) for c in zones.calls if c[0]=='toolsSetTimeZone']==[name]
    assert len([c for c in zones.calls if c[0]=='tzset@GLIBC_2.0'])==1
passed()
# autotime_page's zone rows show each zone's city and, as its value, the offset it has now: the saved zone first,
# then the rest west to east by it, ties in TIMEZONE_ORDER (by city); each is built, ticked and named (image_choice)
# by its saved index, then the saved zone is set again. Here New York and Halifax are on daylight saving. A label too
# long for the row keeps its ellipsis rather than scrolling once.
assert [TIMEZONES[i][0].split(' ',1)[1] for i in TIMEZONE_ORDER]==sorted(l.split(' ',1)[1] for l,_ in TIMEZONES)
SUMMER={'America/New_York','America/Halifax'}
class ZoneRowMachine(TimezoneMachine):
    tz=TIMEZONES[0][1]
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address)
        if name=='toolsSetTimeZone': self.tz=self.text(u.reg_read(REGS[0]))
        if name!='localtime_r': return super().hook(u,address,size,unused)
        self.calls.append((name,)); label=next(l for l,z in TIMEZONES if z==self.tz); tm=u.reg_read(REGS[1])
        self.word(tm+36,(timezone_offset(label)*60+3600*(self.tz in SUMMER))&0xffffffff)
        self.returns(tm,clobber=True)
zones=ZoneRowMachine(); zones.tz=None
zones.mock('image_create','hscroll_label_create','widget_use_style','widget_set_name','toolsSetTimeZone',
           'image_set_draw_type','image_base_set_image','set_hscroll_label_attribute','widget_set_tr_text','widget_on','tk_snprintf')
zone_now={i:timezone_offset(l)+60*(z in SUMMER) for i,(l,z) in enumerate(TIMEZONES)}
zone_rows=sorted(TIMEZONE_ORDER,key=lambda i:zone_now[i])
def zone_label(i):
    m=abs(zone_now[i]); return TIMEZONES[i][0].split(' ',1)[1],f"UTC{'-' if zone_now[i]<0 else '+'}{m//60}{f':{m%60:02d}' if m%60 else ''}"
saved=zone_rows[5]; zones.byte(syms['g_time_zone'],saved)
zone_rows=[saved]+[i for i in zone_rows if i!=saved]  # pinned at the top
for row in range(1,len(TIMEZONES)+1):
    item=zones.node('list_item'); zones.calls=[]; zones.call(address=HOOKS['timezone_row'][0],args=(item,row,0,0),gap=0)
    if row==1:
        assert len([c for c in zones.calls if c[0]=='tzset@GLIBC_2.0'])==len(TIMEZONES)+1
        assert [zones.text(c[1]) for c in zones.calls if c[0]=='toolsSetTimeZone']==[z for _,z in TIMEZONES]+[TIMEZONES[saved][1]]
    index=zone_rows[row-1]; button,=zones.nodes[item]['children']; label,image,value=zones.nodes[button]['children']
    assert zones.nodes[button]['name']==str(index+1)  # the click handler's atoi, less one, is the saved index
    assert (zones.nodes[label]['text'],zones.nodes[value]['text'])==zone_label(index)
    assert zones.nodes[label]['hscroll_label_set_only_focus']==1 and zones.nodes[value]['name']=='_setting_value'
    assert zones.nodes[image]['name']==f'image_choice{index}' and zones.nodes[image]['image']==('select' if index==saved else 'unselect')
zone_labels=[zone_label(i) for i in zone_rows]
assert zone_labels.index(('New York','UTC-4'))<zone_labels.index(("St. John's",'UTC-3:30'))<zone_labels.index(('Buenos Aires','UTC-3'))<zone_labels.index(('Halifax','UTC-3'))
assert ('Chicago','UTC-5') not in zone_labels and ('Chicago','UTC-6') in zone_labels
passed()
# Stock's caption, in the row-high item between row 0 and row 1, sits at y 20 of it, over row 1, and the row hook leaves
# it there, as it leaves row 0 with no caption (Automatic off).
def zone_caption(text=1):
    z=ZoneRowMachine(); oled_set(z)
    z.mock('image_create','hscroll_label_create','widget_use_style','widget_set_name','toolsSetTimeZone','image_set_draw_type','image_base_set_image','set_hscroll_label_attribute','widget_set_tr_text','widget_on','tk_snprintf')
    cap=z.node('hscroll_label','',text='Time Zone Selection') if text else z.node('button','0'); first=z.node('list_item'); capitem=z.node('list_item','',[cap]); item=z.node('list_item')
    sv=z.node('scroll_view','scroll_view',[first,capitem,item]); parents(z,sv); place(z,cap,0,20,375,20)
    z.call(address=HOOKS['timezone_row'][0],args=(item,1,0,0),gap=0); return z,cap
z,zc=zone_caption(); assert wbox(z,zc)==(0,20,375,20)
z,zc=zone_caption(0); assert wbox(z,zc)==(0,20,375,20); passed()
def stream_rows(m):
    """Streaming's page over stock's one Tidal row: the hook's rows' buttons by their labels, in order."""
    tidal=m.node('list_item','',[m.node('image',image='list_tidal')])
    view=m.node('scroll_view','scroll_view_streamsset',[tidal]); m.top=m.node('window','stream_page',[view])
    assert m.call(address=HOOKS['stream_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
    rows={}
    for r in m.nodes[view]['children'][1:]:
        b=m.nodes[r]['children'][0]; rows[m.nodes[m.nodes[b]['children'][1]]['text']]=b
    return rows
def stream_page(m):
    """Stock Streaming's one Tidal row, then the hook's Spotify, Tidal Downloads, Internet Radio and AirPlay
    rows; returns Spotify's button."""
    view=m.node('scroll_view','scroll_view_streamsset',[m.node('list_item')]); m.top=m.node('window','stream_page',[view])
    assert m.call(address=HOOKS['stream_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0 and m.calls[0][0]=='stock_stream'
    assert len(m.nodes[view]['children'])==6
    # Tidal Downloads, after Spotify: the card's cached Tidal tracks, here none.
    tidal=m.nodes[m.nodes[view]['children'][2]]['children'][0]; icon,label=m.nodes[tidal]['children'][:2]
    assert m.nodes[icon]['image']=='list_tidal' and m.nodes[label]['text']=='Tidal Downloads'
    top=m.top; f,ctx=m.handler(tidal,O['EVT_CLICK']); m.calls=[]; assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
    assert [m.text(c[1]) for c in m.calls if c[0]=='toolsLoadDirectory']==['/mnt/mmc/.tidal']
    assert m.nodes[m.top]['name']=='tidal_downloads' and empty_titles(m)==['No Downloads Yet']
    f,ctx=m.handler(m.top,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.top=top
    # AirPlay, last: stock's own receiver, its switch on System settings' Network service page, one press away.
    airplay=m.nodes[m.nodes[view]['children'][4]]['children'][0]; icon,label=m.nodes[airplay]['children'][:2]
    assert m.nodes[icon]['image']=='stream_airplay' and m.nodes[label]['text']=='AirPlay'
    top=m.top; f,ctx=m.handler(airplay,O['EVT_CLICK']); m.calls=[]; assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
    assert [m.text(c[1]) for c in m.calls if c[0]=='navigator_to']==['systemset/netservice_page']; m.top=top
    return m.nodes[m.nodes[view]['children'][1]]['children'][0]
def spot_open(m,button):
    """The row, then (librespot on the card: the browser) its Now Playing page."""
    f,ctx=m.handler(button,O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
    if BIN in m.files:
        if m.nodes[m.top]['name']=='spotbrowse_page': m.browser=m.top  # once: the row brings it back
        assert m.call(address=payload_syms['spot_np'],args=(0,0,0,0),gap=0,count=5_000_000)==0
    m.page=m.top; assert m.nodes[m.page]['name']=='spotify_page'; return m.page
def texts(w): return [t for t in labels(w) if t is not None]
BIN='/mnt/mmc/.spotify/librespot'
# The row follows stock's, with its icon. Without librespot on the card the page says so and
# nothing starts.
m=SpotMachine({}); button=stream_page(m)
icon,label=m.nodes[button]['children'][:2]
assert m.nodes[icon]['image']=='stream_spotify' and m.nodes[label]['text']=='Spotify' and not m.nodes[button].get('name')
page=spot_open(m,button); info,msg=m.nodes[page]['children']
assert not m.nodes[info]['visible'] and m.nodes[msg]['visible'] and texts(msg)==["Spotify isn't on the card",'See the Q2 Muse guide to add it']
m.poll(); assert not m.systems; m.close(); passed()
# With it, the first open starts it once (its launcher backgrounds itself); with no state yet, or no
# Connect session, the page waits for a confirmed authentication response.
m=SpotMachine({BIN:bytearray()}); button=stream_page(m); page=spot_open(m,button); info,msg=m.nodes[page]['children']
assert m.systems==['/bin/sh /mnt/mmc/.spotify/run'] and texts(msg)==['Loading…','Waiting for Spotify']
m.state('none',track=''); m.poll(); m.advance(250); assert m.nodes[msg]['visible'] and not m.nodes[info]['visible']
m.close(); spot_open(m,button); assert len(m.systems)==1; m.close(); passed()
# Until the row is opened nothing Spotify runs, a saved login on the card or not: no start, no
# state read, no held timers.
bare=SpotMachine({}); m=SpotMachine({BIN:bytearray(),'/mnt/mmc/.spotify/cache/credentials.json':bytearray()})
m.state('playing')
for _ in range(3): assert m.poll()==bare.poll()  # the same calls as a card without Spotify
assert not m.systems; passed()
# Playing: local music stops and the headphone output is set up as stock's AirPlay page does it,
# once; meanwhile standby and auto power-off (not the screen's timer) are held and the DAC kept on.
m=SpotMachine({BIN:bytearray()}); button=stream_page(m); page=spot_open(m,button); info,msg=m.nodes[page]['children']
m.byte(syms['g_headset_output'],1); m.byte(syms['g_volume'],37); m.play=3; m.word(syms['g_dacoff_time'],5)
m.state('playing',position=10000); names=m.poll()
take=[(c[0],*c[1:3]) for c in m.calls if c[0] in ('player_stop','config_outputchannel','mclSetPcmMode','mclSetMute','device_set_volume','mclSetDacPwr')]
assert [t[0] for t in take]==['player_stop','config_outputchannel','mclSetPcmMode','mclSetMute','device_set_volume']
assert take[1][1:]==(1,2) and take[3][1]==0 and take[4][1:]==(37,1)
assert ('reset_poweroptions_timer',1,1,0) in [c[:4] for c in m.calls] and m.get(syms['g_dacoff_time'])==0
m.play=1; m.word(syms['g_dacoff_time'],3); names=m.poll()
assert 'config_outputchannel' not in names and 'reset_poweroptions_timer' in names and m.get(syms['g_dacoff_time'])==0
# Bluetooth's way is left alone: a DAC check_dacoff_state powered off is only powered on.
m.state('paused'); m.poll(); m.byte(syms['g_headset_output'],2); m.word(syms['g_dacoff_time'],0xffffffff); m.state('playing'); m.poll()
assert 'config_outputchannel' not in [c[0] for c in m.calls] and ('mclSetDacPwr',1) in [c[:2] for c in m.calls]; passed()
# The card's aplay.sh plays where Videos would: no SPOT_OUT on the headphone DAC; Bluetooth's
# plug:bluealsa and the volume, rewritten when it changes, which a running q2video sink also gets.
OUT='/tmp/q2-librespot.out'; assert OUT not in m.files
m.way=1; m.byte(syms['g_volume'],42); m.poll(); assert m.files[OUT]==b'plug:bluealsa 42\n' and m.sent[-1][1::4]==('v*','/tmp/q2sink.sock')
m.poll(); assert not m.sent; m.byte(syms['g_volume'],43); m.poll(); assert m.files[OUT]==b'plug:bluealsa 43\n'
m.way=0; m.poll(); assert OUT not in m.files; passed()
# Swap alone selects the same headphone PCM helper as Mono Audio.
sw=SpotMachine({BIN:bytearray(b'bin')}); sw.config['SWAPLR']='1'; sw.byte(syms['g_volume'],42)
spot_open(sw,stream_page(sw)); sw.now+=500
sw.call(address=payload_syms['spot_poll'],args=(0,0,0,0),gap=0)
assert sw.files[OUT]==b'plughw:0,0 h42\n'; passed()
# The page: the art (librespot's cover sized by Coverflow's thumbnailer, once a track), title, artist
# and album, the position moving on from at= and the remaining time, all clear of the glass.
m.state('playing',position=10000,at=m.now+500,cover=1); m.files[STATE+'.jpg']=bytearray(b'cover'); m.poll(); m.advance(0)
assert m.nodes[info]['visible'] and not m.nodes[msg]['visible']
assert m.thumbs==[(STATE+'.jpg','/tmp/q2spot.jpg',166,166)] and 'file:///tmp/q2spot.jpg' in [m.text(c[2]) for c in m.calls if c[0]=='widget_load_image']
art=next(w for w in m.nodes if under(w,info) and m.nodes[w]['type']=='image')
assert ('widget_invalidate_force',art) in [c[:2] for c in m.calls]
assert texts(info)==['Spotify','Song','A, B','LP','0:10','-3:10']
for value in ('Song','A, B','LP'):
    w=next(w for w in m.nodes if under(w,info) and m.nodes[w].get('text')==value)
    assert m.nodes[w]['hscroll_label_set_only_focus']==0 and m.nodes[w]['hscroll_label_set_ellipses']==0
for text, rgb in [('Song', 0xffffff), ('A, B', O['NP_ARTIST_RGB']), ('LP', O['NP_ALBUM_RGB'])]:
    label=next(w for w in m.nodes if under(w,info) and m.nodes[w].get('text')==text)
    assert m.nodes[label]['style:normal:text_color']==signed(color_t(rgb))
m.advance(2500); assert texts(info)[4:]==['0:12','-3:08'] and len(m.thumbs)==1
# Every replacement at the same file URL repaints the entire image, even if the URL is unchanged.
for track in ('t2','t3'):
    m.state('playing',position=12500,at=m.now+500,cover=1,track=track); m.poll()
    m.call(address=payload_syms['spot_refresh'],args=(0,0,0,0),gap=0)
    assert ('widget_invalidate_force',art) in [c[:2] for c in m.calls]
assert len(m.thumbs)==3
for w in [x for x in m.nodes if m.nodes[x]['type'] in ('hscroll_label','image','view') and under(x,info) and x!=info]:
    x,y,bw,bh=cf_geometry(m,w); inset=max(corner_inset(30+y),corner_inset(30+y+bh))  # the window starts at y 30
    assert inset<=x and x+bw<=375-inset,(m.nodes[w].get('text'),x,y,bw,bh)
# The bar (spotify.c spot_paint): a capsule in the theme's scrubber track and the elapsed share in the accent's
# third tone, the theme's scrubber height (playing.scrubber) at most the 8 px view's, at its top.
bar_height=min(OLT['np_bar'],8)
bar=[w for w in m.nodes if m.nodes[w]['type']=='view' and cf_geometry(m,w)==(21,251,333,8)][0]
m.rounded=[]; m.call(address=HOOKS['widget_on_paint_border'][0],args=(bar,m.canvas,0,0),gap=0,clear=False)
track,fill=m.rounded
assert track['rect']==(0,0,333,bar_height) and track['color']==color_t(OLT['np_track_rgb']) and fill['rect']==(0,0,(12500>>4)*333//((200000>>4)|1),bar_height)
assert fill['color']==color_t(OLED_TONES[2]); passed()
# Play/Pause and the side buttons are Spotify's, on any page, after stock's key-lock filter, while it
# is the last thing played; stock's downstream handler never sees them.
m.top=m.node('window','home_page')
for k,c in ((O['KEY_PLAY'],'p'),(O['KEY_FWD_BTN'],'n'),(O['KEY_BACK_BTN'],'b')):
    assert m.key(k)==11 and m.sent==[(m.sent[0][0],c,1,0x40,1,SOCK,110)]
m.state('paused'); m.poll(); assert m.key(O['KEY_PLAY'])==11 and m.sent[0][1]=='p'
# Local music playing takes them back.
m.play=2; m.poll(); assert m.key(O['KEY_PLAY'])!=11 and not m.sent; passed()
# Local music starting (mclStartPlayer) while Spotify plays pauses it first and waits until
# librespot's aplay is gone (it says paused); otherwise it starts at once.
m.play=1; m.state('playing'); m.poll()
def start():
    m.calls=[]; m.sent=[]; m.call(address=HOOKS['mclStartPlayer'][0],args=(0,0,0,0),gap=0,clear=False,count=5_000_000)
    return [c[0] for c in m.calls]
m.on_sleep=lambda: m.state('paused') if m.now>=t0+60 else None; t0=m.now
names=start(); assert m.sent[0][1]=='s' and names[-1]=='stock_start_player' and 60<=m.now-t0<=100
m.on_sleep=None; names=start(); assert not m.sent and names.count('sleep_ms')==0 and names[-1]=='stock_start_player'
# A librespot that never answers holds local music back at most SPOT_YIELD_MS.
m.state('playing'); m.play=1; m.poll(); t0=m.now; start(); assert 1500<=m.now-t0<=1540; passed()
# On the page, a double centre press starts a scrub: the wheel moves it 5 s a tick (not the volume),
# shown on the bar in white, and a press or SCRUB_MS later seeks there. A single press otherwise turns
# the screen off, as on Now Playing. Without a scrub the wheel is stock's volume.
m.state('paused',position=60000); m.poll(); m.top=page
assert m.key(O['KEY_NEXT'])!=11
m.mock('on_wm_keyup_fun')  # stock's screen toggle
def double(): return m.key(O['KEY_CENTER'])==11 and m.key(O['KEY_CENTER'])==11
assert double() and m.key(O['KEY_NEXT'])==11 and m.key(O['KEY_NEXT'])==11 and m.key(O['KEY_PREV'])==11 and not m.sent
m.advance(250); assert texts(info)[4]=='1:05' and 'on_wm_keyup_fun' not in [c[0] for c in m.calls]
m.poll(O['SCRUB_MS']-260); assert not m.sent; m.poll(); assert m.sent[0][1]=='S65000'  # 250 ms already passed
assert double() and m.key(O['KEY_PREV'])==11 and m.key(O['KEY_CENTER'])==11 and m.sent[0][1]=='S55000'
m.advance(O['DOUBLE_CLICK_MS']); assert m.key(O['KEY_CENTER'])==11; m.advance(O['DOUBLE_CLICK_MS']); assert 'on_wm_keyup_fun' in [c[0] for c in m.calls] and not m.sent
# Scrubbing, a single press seeks at once and the screen stays on.
m.advance(200); assert double() and m.key(O['KEY_PREV'])==11; m.sent=[]; assert m.key(O['KEY_CENTER'])==11 and m.sent[0][1]=='S55000'
m.advance(O['DOUBLE_CLICK_MS']); assert 'on_wm_keyup_fun' not in [c[0] for c in m.calls]
# Return goes back to Streaming.
assert m.call(O['KEY_RETURN'],address=m.handler(page,O['EVT_KEY_UP'])[0],args=(0,m.event,0,0),event_type=O['EVT_KEY_UP'],gap=0)==11
m.advance(0); assert 'navigator_back' in [c[0] for c in m.calls]; m.close(); passed()
# SPOT_IDLE_MS (30 s) not playing after it has played, the browser shut, stops librespot and its
# loop; before the first play only after SPOT_CAST_MS (150 s), never while the browser is open (Now Playing is on it), and playing
# again in time restarts the wait. The keys are local music's again, and the row starts it again.
m=SpotMachine({BIN:bytearray()}); button=stream_page(m); page=spot_open(m,button)
m.state('none'); m.poll(40000); assert len(m.systems)==1
m.state('playing'); m.poll(); m.state('paused'); m.poll(60000); assert len(m.systems)==1
m.close(); m.page=m.browser; m.close(); m.top=m.node('window','home_page')
m.state('playing'); m.poll(); m.state('paused'); m.poll(20000); m.state('playing'); m.poll(); m.state('paused')
m.poll(29000); assert len(m.systems)==1 and m.key(O['KEY_PLAY'])==11
m.poll(1000); assert 'killall -9 librespot aplay' in m.systems[-1] and STATE in m.systems[-1]
assert m.key(O['KEY_PLAY'])!=11
spot_open(m,button); assert m.systems[-1]=='/bin/sh /mnt/mmc/.spotify/run'; m.close(); passed()
# Never played: the browser shut, SPOT_CAST_MS (150 s) leaves time to cast from the phone, then it stops.
m=SpotMachine({BIN:bytearray()}); button=stream_page(m); page=spot_open(m,button); m.state('none'); m.poll()
m.close(); m.page=m.browser; m.close(); m.top=m.node('window','home_page')
m.poll(149000); assert len(m.systems)==1
m.poll(1000); assert 'killall -9 librespot aplay' in m.systems[-1]; passed()

# Internet Radio (patch/radio.c, docs/internals.md#internet-radio): favourites on the card and
# radio-browser.info's directory, a station played by q2video -r, its tags from RADIO_STATE.
RSTATE,RSOCK,RDIR='/tmp/q2radio.state','/tmp/q2radio.sock','/mnt/mmc/Radio'
