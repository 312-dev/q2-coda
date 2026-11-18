# Execute stock navigator, Now Playing initialization/key-up, and native window switch/close.
# Resource loading, array storage, paint and animation services remain mocked.
class NavigationMachine(Machine):
    def __init__(self):
        super().__init__()
        self.order=[]; self.block_open=False
        self.word(self.wm+0x94,0x9b42b0)
        for name in ('navigator_switch_to_with_context','navigator_back_to_home','navigator_to_with_context',
                     'navigator_to','window_close'):
            self.handlers.pop(syms[name],None)
        self.mock('widget_child','window_open_and_close','widget_restack','widget_get_window',
                  'widget_is_keyboard','widget_is_dialog','widget_is_window','widget_is_normal_window',
                  'widget_remove_child','widget_destroy','window_manager_dispatch_window_event',
                  'widget_foreach','widget_on','playing_timer_start','access@GLIBC_2.0',
                  'remove@GLIBC_2.0','idle_add','netdisk_folder_clear','awake_screen',prefix='nav:')
        self.rockbox_mode=0o100755; self.groovebox_mode=0  # the launchers' binaries: Rockbox on the card, Groovebox not
        self.handlers[syms['__xstat@GLIBC_2.0']]='rockbox_stat'
        self.handlers[syms['strcmp@GLIBC_2.0']]='tk_strcmp'
        for address in (0x53cf08,0x6989cc,0x69bb70,0x699784): self.handlers[address]='nav:'+hex(address)
    def sync_order(self):
        self.top=self.order[-1] if self.order else 0
        arr=self.alloc(12); items=self.alloc(max(4,len(self.order)*4))
        self.word(self.wm+0x5c,arr); self.word(arr,len(self.order)); self.word(arr+8,items)
        for i,w in enumerate(self.order): self.word(items+4*i,w); self.word(w+O['W_PARENT'],self.wm)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('nav:'): return super().hook(u,address,size,unused)
        assert u.reg_read(UC_MIPS_REG_T9)==address
        name=name[4:]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        self.calls.append((name,a,b,c))
        if name=='widget_child':ret=next((w for w in self.order if self.nodes[w]['name']==self.text(b)),0)
        elif name=='window_open_and_close':
            if not self.block_open:
                ret=self.node('window',self.text(a),stage=3); self.order.append(ret); self.sync_order()
        elif name=='widget_restack':
            self.order.remove(a); self.order.insert(min(b,len(self.order)),a); self.sync_order()
        elif name=='widget_get_window': ret=a
        elif name in ('widget_is_window','widget_is_normal_window'): ret=int(a in self.order)
        elif name=='widget_remove_child': self.order.remove(b); self.sync_order()
        elif name=='0x69bb70': ret=1  # stock no-window-animation completion branch
        elif name=='0x699784':ret=self.top
        elif name=='access@GLIBC_2.0':ret=-1
        self.returns(ret)

for existing in (False,True):
    for empty in (False,True):
        m=NavigationMachine(); w,es=m.page_list(20,name='folder_page'); browse=m.top
        home=m.node('window','home_page',stage=3)
        playing=m.node('window','playing_page',stage=3) if existing else 0
        m.order=[home]+([playing] if playing else [])+[browse]; m.sync_order()
        m.folder('/sd/music'); m.paint(w)
        for _ in range(6):m.call()
        selected,offset=m.selected(w),m.get(w+O['SCROLL_Y'])
        for visit in range(3):
            long_return(m)
            assert m.nodes[m.top]['name']=='playing_page'
            assert not any(c[0]=='player_start' for c in m.calls)
            # The switch leaves the hold-release latch armed: stock's filter swallows the hold's
            # release where it is delivered (let go before the page's open event), and the first short Return goes back.
            assert m.u.mem_read(O['RETURN_RELEASE_LATCH'],1)==b'\1'
            assert m.call(170,gap=0)==11 and m.nodes[m.top]['name']=='playing_page'
            assert m.u.mem_read(O['RETURN_RELEASE_LATCH'],1)==b'\0'
            m.call(170,address=0x53da5c,args=(m.top,m.event,0,0),gap=0)
            assert m.top==browse, (existing,visit,[m.nodes[p]['name'] for p in m.order])
            m.paint(w,gap=0); assert (m.selected(w),m.get(w+O['SCROLL_Y']))==(selected,offset)
        passed()
m=NavigationMachine(); m.page('folder_page'); browse=m.top; m.order=[browse]; m.sync_order()
m.block_open=True; long_return(m); assert m.top==browse
assert m.call(170,gap=0)==11
m.block_open=False; m.animating=1; long_return(m); assert m.top==browse
passed()

# Pull-to-search observes the page's native before-children callbacks and stock dispatch.
class PullMachine(Machine):
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a,b,c,d=[u.reg_read(r) for r in REGS]
        if name=='widget_on' and b==getattr(self,'pull_bind_fail',None):
            self.returns(0); return
        if name=='widget_off_by_func':
            self.nodes[a]['handlers']=[h for h in self.nodes[a].get('handlers',[]) if h!=(b,c,d)]
        return super().hook(u,address,size,unused)
    def child(self,page,name):
        def walk(w):
            if self.nodes[w]['name']==name:return w
            for child in self.nodes[w]['children']:
                found=walk(child)
                if found:return found
            return 0
        return walk(page)
    def emit(self,w,typ,x=100,y=90):
        self.word(self.event+O['EVENT_TARGET'],w)
        self.word(self.event+O['EVENT_Y'],y)
        ret=0
        for t,callback,ctx in list(self.nodes[w].get('handlers',[])):
            if t==typ:
                ret=self.call(key=x,address=callback,args=(ctx,self.event,0,0),event_type=typ,gap=0,clear=False)
                if ret==11:break
        return ret
    def start_pull(self,page,w,x=100,y=60):
        self.top=page; self.word(w+O['W_PARENT'],page);self.word(page+O['W_Y'],30)
        self.word(self.event+O['EVENT_Y'],y)
        return self.call(key=x,address=HOOKS['on_wm_tsdown_before_fun'][0],event_type=O['EVT_POINTER_DOWN'],gap=0)
    def prompt(self,page):
        w=self.child(page,'_pull_prompt')
        return self.nodes[w] if w else {'visible':0}
    # Local Songs' search: OLED's letter strip (list.entry ipod, search_strip.c library_search_open), its page made by
    # page_open's window_create, which the harness leaves failing: the strip's opens.
    def searches(self):return [c for c in self.calls if c[0]=='window_create']

# Stock constructors/dispatch establish these event numbers and fields, independently of mocks.
m=Machine()
for n in ('pointer_event_init','event_init'):m.handlers.pop(syms[n],None)
p=m.alloc(48);m.call(address=syms['pointer_event_init'],args=(p,O['EVT_POINTER_DOWN'],m.wm,37),stack=(91,))
assert m.get(p+O['EVENT_TARGET'])==m.wm and m.get(p+O['EVENT_X'])==37 and m.get(p+O['EVENT_Y'])==91
assert m.get(p+4)==48;passed()

# The stock wm registration fixes the type that reaches the hooked down callback. The
# move-before thunk forwards 0x102; 0xff belongs to the separate stock on_wm_tsdown_fun.
def wm_event_type(address):
    return struct.unpack('<I',m.u.mem_read(address,4))[0]&0xffff
assert wm_event_type(0x534e08)==O['EVT_POINTER_DOWN']==0x100
assert wm_event_type(0x534dec)==O['EVT_POINTER_MOVE_BEFORE']==0x102
assert wm_event_type(0x534e40)==0xff
assert wm_event_type(0x534d84)==O['EVT_KEY_DOWN_BEFORE']==0x112
assert wm_event_type(0x534dd0)==O['EVT_KEY_UP']==0x114
assert wm_event_type(0x534d98)==O['EVT_KEY_UP_BEFORE']==0x115
passed()

for page_name in ('folder_page','localmusic_page','allmusic_page','radio_page'):
    for empty in (False,True):
        for distance in (0,7,8,47,48,49):
            m=PullMachine(); m.handlers[payload_syms['radio_search']]='stock_radio_search'  # a payload call: no t9
            if page_name=='folder_page':
                w,rows,es=m.table_page(name=page_name)
                if empty:m.word(w+O['TABLE_ROWS'],0);m.nodes[w]['children']=[]
            else:w,es=m.page_list(0 if empty else 5,name=page_name)
            page=m.top;m.start_pull(page,w)
            m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=60+distance)
            # Local Songs' search is the iPod letter strip; Internet Radio's is its own.
            enabled=page_name in ('radio_page','localmusic_page')
            assert bool(m.prompt(page)['visible'])==(enabled and distance>=8)
            if enabled and distance>=8:
                assert m.prompt(page)['text']==('Release to search' if distance>=48 else 'Pull to search')
                assert m.click(es[0])==11 if es else True
            # click() normally uses a one-second gap; gesture state still belongs to the page.
            m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=60+distance)
            radio=lambda: [c for c in m.calls if c[0]=='stock_radio_search']
            assert len(m.searches()+radio())==(1 if enabled and distance>=48 else 0) and not (radio() and page_name!='radio_page')
            m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=120)
            assert len(m.searches()+radio())==(1 if enabled and distance>=48 else 0)
            assert not m.prompt(page)['visible']
            passed()

# Home's own list takes no pull under OLED's set (home_lib 0: no Library list on Home's window).
assert OLED_G.home_lib==0
m=PullMachine(); oled_boot(m); w,es=m.page_list(5,name='home_page'); m.nodes[w]['name']='scroll_view_home'; page=m.top
m.start_pull(page,w); m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=120)
assert not m.prompt(page)['visible']
m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=120)
assert not m.searches() and not m.prompt(page)['visible']
passed()
# A failed registration leaves no partial page callbacks; retry binds each event once.
for fail in ('EVT_WINDOW_BACKGROUND','EVT_POINTER_UP_BEFORE'):
    m=PullMachine();m.handlers[payload_syms['radio_search']]='stock_radio_search'
    w,es=m.page_list(5,name='radio_page');page=m.top
    before=list(m.nodes[page].get('handlers',[]));m.pull_bind_fail=O[fail]
    m.start_pull(page,w)
    assert m.nodes[page].get('handlers',[])==before and not m.nodes[page].get('_pull_bound',0)
    m.pull_bind_fail=None;m.start_pull(page,w)
    handlers=m.nodes[page]['handlers'];assert len(handlers)==len(before)+6 and len(handlers)==len(set(handlers))
    m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=120);m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=120)
    assert len([c for c in m.calls if c[0]=='stock_radio_search'])==1
    passed()

# Swipe back (swipe_back.c) on MIPS: a press at the left edge of a page over another, a drag right that moves the
# page window with the finger once past the claim, and a lift past 40% that slides it on and gives stock's input a
# Return press and release. A move's and a glide step's payload instructions are each frame's share of the budget.
m=PullMachine(); sb_n=[0]; h=m.u.hook_add(UC_HOOK_BLOCK,lambda u,a,size,x: sb_n.__setitem__(0,sb_n[0]+size//4),begin=BASE,end=SCRATCH-1)  # before any code runs
under=m.node('window','home_page'); w,es=m.page_list(5,name='albums_page'); page=m.top
m.nodes[m.wm]=dict(type='window_manager',name='',children=[under,page],visible=1,enable=1)
m.word(page+O['W_W'],375); m.handlers[payload_syms['stock_input_trampoline']]='stock_input'
m.start_pull(page,w,x=5,y=150)
def sb_inputs(): return [c for c in m.calls if c[0]=='stock_input']
sb_moves=[]
for x,want in ((12,0),(20,11),(60,11),(200,11)):
    m.now+=16; sb_n[0]=0; assert m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],x=x,y=150)==want; sb_moves.append(sb_n[0])
assert signed(m.get(page+O['W_X']))==195 and [c[0] for c in m.calls].count('widget_move')==3
m.now+=16; assert m.emit(page,O['EVT_POINTER_UP_BEFORE'],x=200,y=150)==11 and not sb_inputs()
sb_steps=[]
while not sb_inputs() and len(sb_steps)<40:
    m.now+=8; sb_n[0]=0; m.call(address=payload_syms['swipe_back_poll'],args=(0,0,0,0),gap=0,clear=False); sb_steps.append(sb_n[0])
m.u.hook_del(h)
assert len(sb_inputs())==2 and signed(m.get(page+O['W_X']))==0  # Return's press and release; the page back in its place
sb_move,sb_step=max(sb_moves[1:]),max(sb_steps[:-1]); assert sb_move<600_000 and sb_step<600_000; passed()
print(f'Swipe back on MIPS: {sb_move} instructions a move at most (the page moved with the finger), {sb_step} a glide step (payload only; stock\'s repaint of the moved window is its own)')

# Local Songs' virtual table keeps its rows and top offset across a search.
m=PullMachine();w,rows,es=m.table_page(name='localmusic_page');page=m.top
m.start_pull(page,w);count=m.get(w+O['TABLE_ROWS'])
m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=120);m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=120)
assert len(m.searches())==1 and m.get(w+O['TABLE_ROWS'])==count and m.get(w+O['TABLE_TOP'])==0
# The search opens over this page; resuming it needs no index translation.
m.start_pull(page,w);m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=60)
assert m.click(es[0])==0
passed()
for cancel in ('below','horizontal','upward','midlist','outside','edge0','edge30','edge31',
               'wheel','button','screen','navigate','background','close','destroy','surface','abort','move_hook'):
    m=PullMachine();w,es=m.page_list(8,height=240,name='localmusic_page');page=m.top
    y=30 if cancel=='edge30' else 0 if cancel=='edge0' else 31 if cancel=='edge31' else 60
    if cancel=='midlist':m.word(w+O['SCROLL_Y'],20)
    m.start_pull(page,w,x=250 if cancel=='outside' else 100,y=y)
    if cancel=='horizontal':m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],x=170,y=y+20)
    elif cancel=='upward':m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=y-10)
    m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=y+60)
    if cancel=='below':m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=y+47)
    elif cancel in ('wheel','button'):m.call(O['KEY_NEXT'] if cancel=='wheel' else O['KEY_PLAY'],gap=0)
    elif cancel=='screen':m.byte(syms['g_backlight_status'],0);m.paint(w,gap=0)
    elif cancel=='navigate':m.page('home_page');m.paint(m.top,gap=0)
    elif cancel in ('background','close','destroy','surface','abort'):
        typ=O[{'background':'EVT_WINDOW_BACKGROUND','close':'EVT_WINDOW_CLOSE','destroy':'EVT_DESTROY',
               'surface':'EVT_DESTROY','abort':'EVT_POINTER_ABORT'}[cancel]]
        m.emit(w if cancel=='surface' else page,typ)
    elif cancel=='move_hook':
        # The native wm move-before (0x102) tail-calls the down-before hook (0x100).
        # The move type must not restart the pull.
        m.word(m.event+O['EVENT_Y'],y+60)
        m.call(key=100,address=syms['on_wm_tsmove_before_fun'],event_type=O['EVT_POINTER_MOVE_BEFORE'],gap=0,clear=False)
    m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=y+(47 if cancel=='below' else 60))
    assert len(m.searches())==(1 if cancel in ('edge31','move_hook') else 0),cancel
    assert not m.prompt(page)['visible'],cancel
    passed()

# Native before-children dispatch invokes the page callback before it can activate a row.
class PullDispatchMachine(PullMachine):
    def __init__(self):
        super().__init__()
        self.mock('widget_ref','widget_unref','widget_on_pointer_move_children','widget_on_pointer_up_children',
                  'widget_on_event_before_children','widget_vtable_on_pointer_move','widget_vtable_on_pointer_up','emitter_dispatch',prefix='input:')
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('input:'):return super().hook(u,address,size,unused)
        assert u.reg_read(UC_MIPS_REG_T9)==address
        a,b=[u.reg_read(r) for r in REGS[:2]];self.calls.append((name,a,b,0))
        if name=='input:emitter_dispatch':
            matches=[(cb,ctx) for typ,cb,ctx in self.nodes[a].get('handlers',[]) if typ==self.get(b)]
            assert len(matches)<=1
            if matches:
                cb,ctx=matches[0];u.reg_write(UC_MIPS_REG_A0,ctx)
                u.reg_write(UC_MIPS_REG_T9,cb);u.reg_write(UC_MIPS_REG_PC,cb);return
        self.returns(0)

for distance in (7,48):
    m=PullDispatchMachine();w,es=m.page_list(5,height=240,name='localmusic_page');page=m.top
    m.start_pull(page,w)
    m.word(page+0x74,1);m.word(page+O['W_EMITTER'],page)
    m.word(m.event+O['EVENT_Y'],60+distance)
    for fn,typ in (('widget_on_pointer_move',0x101),('widget_on_pointer_up',0x103)):
        m.calls=[]
        ret=m.call(key=100,address=syms[fn],args=(page,m.event,0,0),event_type=typ,gap=0,clear=False)
        child_calls=[c for c in m.calls if c[0]=='input:'+fn+'_children']
        assert (ret==11 and not child_calls) if distance>=8 else bool(child_calls)
    assert len(m.searches())==(distance>=48)
    passed()
# Native abort delivery follows the pressed target chain; it is not an EVT_CLICK.
m=PullMachine();w,es=m.page_list(3,height=240,name='localmusic_page');page=m.top
m.start_pull(page,w);m.handlers.pop(syms['widget_dispatch_event_to_target_recursive'])
m.word(page+0x4c,w);m.word(w+0x4c,es[0])
m.calls=[];m.emit(page,O['EVT_POINTER_MOVE_BEFORE'],y=120)
assert [c[1] for c in m.dispatched()]==[w,es[0]] and not m.clicks
m.emit(page,O['EVT_POINTER_UP_BEFORE'],y=100);assert not m.searches()
assert m.click(es[0])==11
passed()

# Artist detail opens on Albums: the real init reaches the stock Albums tab handler, which
# selects tab 1 and queries albums before building; stock builds Songs instead.
for patched in (True, False):
    m=Machine(patched)
    for address,name in ((0x4af8ac,'songs_view'),(0x4ae3cc,'albums_view'),(0x4b0374,'timer_idex'),(0x4f77e4,'query_run')):
        m.handlers[address]=name
    m.mock('load_artistinfo_list','load_localartist_list','widget_get_window','widget_foreach','strcpy@GLIBC_2.0')
    arg=m.alloc(8); m.word(arg+4,m.alloc(0x40))
    assert m.call(address=syms['localmusic_artistinfo_page_init'],args=(m.node('window','artistinfo_page'),arg,0,0))==0
    names=[c[0] for c in m.calls if c[0] in ('songs_view','albums_view','load_localartist_list','query_run')]
    tab=m.get(0xa3b5ac)  # the artist page's tab, which the Albums tab's handler sets (0x4aeb60)
    if patched:
        assert names==['query_run','load_localartist_list','query_run','albums_view'] and tab==1, names
        assert [c[1] for c in m.calls if c[0]=='load_localartist_list']==[1]
    else: assert names==['query_run','songs_view'] and tab==0, names
    passed()

# Play/Pause hold queue menu. libcstl deques are Python lists of element addresses; the stock
# mclLoadPlayList, mclNextSong and key filters run for real.
from build import SHUFFLE_CALL, fileoff
