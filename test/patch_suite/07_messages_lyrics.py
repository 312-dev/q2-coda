# An A-B marker's x, stock's on the built bar (np_bar_x and np_bar_w), is the same share of the slider
# where the page has it (ringnav_ab_x); a point left of the bar, and 0 (no point to mark), stay as they are.
class ABMachine(QueueMachine):
    ab=0
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='ab:x': return super().hook(u,address,size,unused)
        self.calls.append(('ab_x',u.reg_read(UC_MIPS_REG_A0))); self.returns(self.ab,clobber=True)
m=ABMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'; m.handlers[0x53b9d8]='ab:x'
m.record_syscalls(); m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'],16)]='stock_ui_load'
m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0],args=(m.string('system_bar'),0,0,0),gap=0)  # the first system_bar load, as on a unit (accent, theme_boot)
slider=m.node('slider','slider_play',max=225,value=100); win=m.node('window','playing_page',[slider]); m.word(win+O['W_PARENT'],m.wm); m.top=win
def geo(w): return tuple(signed(m.get(w+O[k])) for k in ('W_X','W_Y','W_W','W_H'))
assert m.call(address=playing,args=(win,7,0,0),gap=0)==0
from ipod import VALUE_NAMES
BX,BW=(m.call(address=payload_syms['geom'],args=(VALUE_NAMES.index(n),0,0,0),gap=0) for n in ('np_bar_x','np_bar_w'))
from build import AB_X_CALLS  # the placement's three calls of the x go through ringnav_ab_x
assert [struct.unpack_from('<I',demo,fileoff(demo,a))[0] for a in AB_X_CALLS]==[jal(payload_syms['ringnav_ab_x'])]*3
def ab_x(x): m.ab=x; m.calls=[]; r=signed(m.call(address=payload_syms['ringnav_ab_x'],args=(77,0,0,0),gap=0)); assert m.calls[0]==('ab_x',77); return r
for k,v in zip(('W_X','W_Y','W_W','W_H'),(BX,233,BW,44)): m.word(slider+O[k],v)
assert BW>0 and [ab_x(x) for x in (BX,BX+89,BX+BW-1,0)]==[BX,BX+89,BX+BW-1,0]; passed()  # on the built bar: stock's own
for k,v in zip(('W_X','W_W'),(87,201)): m.word(slider+O[k],v)
assert [ab_x(x) for x in (BX,BX+89,BX+BW-1,BX-1,0)]==[87,87+89*201//BW,87+(BW-1)*201//BW,BX-1,0]; passed()

# Helpers for blocks further down the file: stock's loader answering a window (ui_loaded), widget geometry, the volume
# over Now Playing, the scan dialog's and USB page's machine (MsgMachine, msg_win, usb_page).
class UiLoad:  # the stock loader answers root
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='load:stock_ui_load': return super().hook(u,address,size,unused)
        self.calls.append(('stock_ui_load',u.reg_read(REGS[0]))); self.returns(self.root)
class LoadQueue(UiLoad,QueueMachine):
    ready=0
    def bootvol(self): return self.ready or super().bootvol()  # the page ui_loaded made, as navigator_to's load did
class LoadPlain(UiLoad,Machine): pass
def ui_loaded(m,name,root):
    m.root=root; m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'],16)]='load:stock_ui_load'
    m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0],args=(m.string(name),0,0,0),gap=0)
def place(m,w,*g):
    for k,v in zip(('W_X','W_Y','W_W','W_H'),g): m.word(w+O[k],v)
def wbox(m,w): return tuple(signed(m.get(w+O[k])) for k in ('W_X','W_Y','W_W','W_H'))
def parents(m,w):
    for c in m.nodes[w]['children']: m.word(c+O['W_PARENT'],w); parents(m,c)
def bg(m,w): m.call(address=HOOKS['widget_on_paint_background'][0],args=(w,m.canvas,0,0),gap=0)
def art_size(name): return struct.unpack('>II',(B/f'{name}.png').read_bytes()[16:24])
def vol_np(light='1',remain=1,**config):
    SETUP[:]=[lambda k:(k.config.update(LIGHT=light,**config),k)]
    try: q=LoadQueue(queue=3,pos=1)
    finally: SETUP.clear()
    q.handlers[playing+12]='stock_playing'
    q.slider=q.node('slider','slider_play',max=300,value=100); q.elapsed,q.remain=q.node('label','label_playtime'),q.node('label','label_ipod_remain')
    q.win=q.top=q.node('window','playing_page',[q.slider,q.elapsed,q.remain]); q.word(q.win+O['W_PARENT'],q.wm); parents(q,q.win)
    place(q,q.win,0,26,375,294); place(q,q.slider,63,213,248,43); place(q,q.elapsed,18,240,42,16)
    q.word(syms['system_bar'],q.node('window','system_bar')); assert q.call(address=playing,args=(q.win,7,0,0),gap=0)==0
    q.nodes[q.remain]['visible']=remain
    q.sv,q.lv=q.node('slider','slider_vol',max=100,value=40,visible=0),q.node('label','label_vol',visible=0)
    q.dlg=q.node('dialog','volume_dialog',[q.sv,q.lv]); q.word(q.dlg+O['W_PARENT'],q.wm); place(q,q.dlg,0,0,375,320)
    q.nodes[q.wm]={'children':[q.win,q.dlg]}; q.top=q.dlg; q.mock('window_close')
    ui_loaded(q,'dialog/volume_dialog',q.dlg)
    return q
def vol_closed(q):
    q.nodes[q.wm]['children']=[q.win]; q.top=q.win; q.calls=[]; q.advance(O['VOL_POLL_MS'])
    assert not q.timers and [q.nodes[w]['visible'] for w in (q.elapsed,q.remain)]==[1,1]
# Every slider page's slider is a plain one, with no knob (slider_open), whatever form the page then takes.
def slider_win(m,name,slider,view,label,ends,y=0,**kw):
    s=m.node('slider',slider,**kw); lab=m.node('label',label,text='5'); l,r=(m.node('image',n) for n in ends)
    dec,add=m.node('image','img_dec'),m.node('image','img_add')
    v=m.node('view',view,[lab,s,l,r,dec,add]); nav=m.node('view','view_navbar',visible=0)
    win=m.node('window',name,[nav,v]); parents(m,win); m.word(win+O['W_PARENT'],m.wm)
    place(m,win,0,26,375,294); place(m,v,0,y,375,240); place(m,s,31,60,313,70); place(m,lab,100,15,175,32)
    m.top=win; m.nodes[m.wm]={'children':[win]}; ui_loaded(m,name,win); return win,s,v,lab,nav,l,r,dec,add
for a in (('backlight_page','slider_backlight','view_backlight','label_backlight',('img_left','img_right')),('maxvol_page','slider_maxvol','view_maxvol','label_vol',('img_volleft','img_volright')),('balance_page','slider_balance','view_balance','label_balance',('img_left','img_right'))):
    pm=LoadPlain(); pwin,psl,*_=slider_win(pm,*a,min=1,max=10,value=4)
    assert all(pm.nodes[psl][f'style:{s}:icon']==0 for s in ('normal','pressed','over','disable','focused')); passed()
class MsgMachine(LoadPlain):
    songs=704
    def __init__(self):
        super().__init__(); self.mock('sqlite3_open','sqlite3_close','strstr@GLIBC_2.0'); self.sqls=[]; self.threads=[]; self.thread_fails=self.busy=self.thread_first=False
        for n,h in (('sqlite3_exec','msg:exec'),('strdup@GLIBC_2.0','msg:strdup'),('pthread_create@GLIBC_2.2','msg:thread')): self.handlers[syms[n]]=h
        self.word(0xa3a270,0x1000010); self.handlers[0x1000010]='msg:free'  # free's GOT slot
    def hook(self,u,address,size,unused):
        h=self.handlers.get(address)
        if h=='msg:strdup': self.returns(self.string(self.text(u.reg_read(REGS[0])))); return
        if h=='msg:thread':
            if not self.thread_fails: self.word(u.reg_read(REGS[0]),77); self.threads.append((u.reg_read(REGS[2]),u.reg_read(REGS[3])))
            self.calls.append(('pthread_create',))
            if self.thread_first and not self.thread_fails: fn,arg=self.threads.pop(); self.nested(fn,arg)
            self.returns(11 if self.thread_fails else 0); return
        if h=='msg:free': self.returns(0); return
        if h!='msg:exec': return super().hook(u,address,size,unused)
        sql,row=self.text(u.reg_read(REGS[1])),u.reg_read(REGS[2]); self.sqls.append(sql)
        if self.busy: self.returns(5); return
        if row:
            argv=self.alloc(4); self.word(argv,self.string(str(self.songs))); self.nested(row,u.reg_read(REGS[3]),1,argv,0)
        self.returns(0)
    def counted(self):  # the worker runs as its thread would
        while self.threads:
            fn,arg=self.threads.pop(0); self.call(address=fn,args=(arg,0,0,0),gap=0,count=5_000_000,clear=False)
def msg_win(m,text):
    anim,msg,scan,btn=m.node('image_animation','img_animation'),m.node('label','label_msg',text=text),m.node('label','label_scanfolder'),m.node('button','btn_no',text='Cancel')
    win=m.node('dialog','updatemusic_dialog',[anim,msg,scan,btn]); parents(m,win); m.word(win+O['W_PARENT'],m.wm)
    place(m,win,0,0,375,320); place(m,btn,44,266,287,40); m.top=win; m.nodes[m.wm]={'children':[win]}; ui_loaded(m,'dialog/updatemusic_dialog',win)
    return win,anim,msg,scan,btn
def usb_page(mode,light='1',y=26,machine=None):
    m=(machine or MsgMachine)(); m.config.update(LIGHT=light); m.byte(syms['g_usbmode'],mode)
    ret,img,info,charge,dacset=m.node('image','img_return'),m.node('image','img_usbmode'),m.node('hscroll_label','scrlabel_info'),m.node('image','img_daccharge'),m.node('image','img_dacset')
    win=m.node('window','usbmode_page',[ret,img,info,charge,dacset]); parents(m,win); m.word(win+O['W_PARENT'],m.wm)
    place(m,win,0,y,375,320-y); place(m,ret,20,0,80,60); place(m,charge,247,0,50,50); place(m,dacset,305,0,50,50); m.top=win; m.nodes[m.wm]={'children':[win]}; ui_loaded(m,'usbmode_page',win)
    return m,win,ret,img,info,charge,dacset
# The art's corners: after the image (the border hook), the theme's playing.art.radius (np_art_radius; NP_ART_RADIUS at
# 166 px without one) rounded into the page's black by corner_mask, each corner pixel at the alpha the arc leaves.
m=QueueMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'
cover=m.node('image','img_cover'); win=m.node('window','playing_page',[cover]); m.word(win+O['W_PARENT'],m.wm); m.top=win
put(cover,16,10,166,166); m.call(address=playing,args=(win,7,0,0),gap=0)
m.word(m.lcd+O['LCD_FILL_COLOR'],0x12345678)
m.bands.clear(); m.call(address=HOOKS['widget_on_paint_border'][0],args=(cover,m.canvas,0,0))
R=theme_builtins()[0]['np_art_radius']; want=corner_mask_bands(0,0,166,166,R,color_t(0))
assert R and [b[:5] for b in m.bands]==want,want[:2]
assert m.get(m.lcd+O['LCD_FILL_COLOR'])==0x12345678; passed()  # restored
m.bands.clear(); m.call(address=HOOKS['widget_on_paint_border'][0],args=(win,m.canvas,0,0)); assert not m.bands; passed()

# Full-screen corners use destination pixels, independent of source-image scaling.
from ipod import BC_ART
bigart=m.node('image','img_bigcover')
bx,by=(375-BC_ART)//2,(320-BC_ART)//2
put(bigart,bx,by,BC_ART,BC_ART)
bigwin=m.node('window','bigcover_page',[bigart]); m.handlers[big+12]='stock_bigcover'
m.call(address=big,args=(bigwin,7,0,0),gap=0)  # ringnav_bigcover keeps the window
m.bands.clear(); m.call(address=HOOKS['widget_on_paint_border'][0],args=(bigart,m.canvas,0,0))
assert not m.bands  # rounded after all children, including the title's shade
m.call(address=HOOKS['widget_on_paint_border'][0],args=(bigwin,m.canvas,0,0))
pixels={}
for x,y,w,h,color,*_ in m.bands:
    for xx in range(x,x+w): pixels[xx-bx,y-by]=color>>24
assert any(0<a<255 for a in pixels.values())
for (x,y),alpha in pixels.items():
    assert pixels.get((BC_ART-1-x,y))==alpha
    assert pixels.get((x,BC_ART-1-y))==alpha
assert m.get(m.lcd+O['LCD_FILL_COLOR'])==0x12345678; passed()

# Stock's play-state glyph and the album label take the theme's playing.state.opacity and its album's (np_theme):
# OLED hides the glyph and shows the album.
q=QueueMachine(queue=3,pos=1); q.handlers[playing+12]='stock_playing'
glyph,album=q.node('image','img_playstate'),q.node('label','label_ipod_album')
for w in (glyph,album): q.byte(w+0x34,0x80)
win=q.node('window','playing_page',[q.node('slider','slider_play',max=225,value=100,bar_size=4),glyph,album])
q.word(win+O['W_PARENT'],q.wm); q.top=win
assert q.call(address=playing,args=(win,7,0,0),gap=0)==0
OLED=theme_builtins()[0]; assert [q.u.mem_read(w+0x34,1)[0] for w in (glyph,album)]==[OLED['np_state_op'],OLED['np_album_op']]==[0,255]; passed()

# Scrub: Centre on Now Playing starts it at once and the next press ends it (np_centre); the wheel then
# moves a target of SCRUB_STEP seconds times the ramp, previewed on the slider and both labels however far
# apart the ticks, and committed once through player_seek_time (track seconds) when the scrub ends. Stock's
# page timer stops meanwhile. The press that ends it puts the Centre cycle's ring on shuffle (OLED's
# playing.modes pair), which takes the wheel until NP_STAGE_MS (SCRUB_MS) without a key.
CONFIG.clear(); FILL_HI=signed(color_t(O['MINIMAL_FILL'])); FG='style:normal:fg_color'  # FILL_HI: MINIMAL_FILL, which a block further down checks
def scrub_page(value=100,mx=225,m=None):  # m: Now Playing opened again on that machine
    if not m: m=QueueMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'
    m.slider=m.node('slider','slider_play',max=mx,value=value)
    m.elapsed,m.remain=m.node('label','label_playtime'),m.node('label','label_ipod_remain')
    m.win=m.top=m.node('window','playing_page',[m.slider,m.elapsed,m.remain])
    m.word(m.win+O['W_PARENT'],m.wm)
    assert m.call(address=playing,args=(m.win,7,0,0),gap=0)==0
    return m
def did(m,name): return [c[1] for c in m.calls if c[0]==name]
def keep(m):
    m.seeks=getattr(m,'seeks',[])+did(m,'player_seek_time'); m.starts=getattr(m,'starts',[])+did(m,'playing_timer_start')
def step(m,*a,**k):  # one input or timer step, keeping every seek and timer restart it makes
    r=(m.advance if k.pop('wait',False) else m.call)(*a,**k); keep(m); return r
def centre(m,gap=1000):  # a double press
    assert m.call(O['KEY_CENTER'],gap=gap)==11 and not did(m,'playing_timer_clear')
    assert m.call(O['KEY_CENTER'],gap=100,clear=False)==11
    m.advance(DC,clear=False); keep(m); assert not m.screens  # no single press left behind
def oled_scrub(**kw):  # Now Playing past the first system_bar load, as on a unit (accent, theme_boot)
    m=QueueMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'
    m.record_syscalls(); m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'],16)]='stock_ui_load'
    m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0],args=(m.string('system_bar'),0,0,0),gap=0)
    return scrub_page(m=m,**kw)
OLED_FILL=signed(color_t(next(s for s in theme_builtins()[0]['schemes'] if s.get('name')=='oled-blue')['tones'][2]))  # the accent's light tone (TONE_LIGHT)
def np_press(m,gap=1000):  # one Centre press, kept as step keeps it
    assert step(m,O['KEY_CENTER'],gap=gap)==11 and not m.screens
def ended(m,seeks,label=''):  # the scrub is over, the cycle's stage too: fill, stock timer and the wheel's volume are back
    assert m.seeks==seeks and m.starts==[m.win], (label,m.seeks,m.starts)
    assert m.nodes[m.slider][FG]==OLED_FILL and not m.timers and m.call()==0 and not did(m,'player_seek_time'), label
# The status row's set keeps the accent's fill and marks the scrub with a white NP_KNOB (14) px knob, centred
# on the fill's end and on the slider's middle row (paint_scrubber); gone once it ends.
m=oled_scrub()
for k,v in zip(('W_X','W_Y','W_W','W_H'),(24,240,327,30)): m.word(m.slider+O[k],v)
def knob(m):
    m.rounded.clear(); m.call(address=HOOKS['widget_on_paint_border'][0],args=(m.slider,m.canvas,0,0),gap=0)
    return [(r['rect'],r['radius'],r['color']) for r in m.rounded if r['rect'][2:]==(14,14)]
assert m.nodes[m.slider][FG]==OLED_FILL and knob(m)==[]; np_press(m)
assert did(m,'playing_timer_clear')==[m.win] and m.nodes[m.slider][FG]==OLED_FILL and knob(m)==[((327*100//225-7,8,14,14),7,color_t(0xffffff))],knob(m)
for key,x in ((O['KEY_PREV'],0),(O['KEY_NEXT'],327-14)):  # kept inside the bar at either end
    for _ in range(40): step(m,key,gap=20)
    assert [k[0][0] for k in knob(m)]==[x],(key,knob(m))
np_press(m,50); assert knob(m)==[]; step(m,O['SCRUB_MS'],wait=True); ended(m,[225]); passed()
m=scrub_page(); np_press(m)
assert did(m,'playing_timer_clear')==[m.win] and not m.screens; passed()
# Ticks a second apart, far past the old 150 ms seek, each preview at once and none seeks.
assert step(m)==11 and m.nodes[m.slider]['value']==105 and m.nodes[m.elapsed]['text']=='1:45'
assert m.nodes[m.remain]['text']=='-2:00'
assert step(m,gap=1500)==11 and m.nodes[m.slider]['value']==110 and m.nodes[m.elapsed]['text']=='1:50'
assert m.nodes[m.remain]['text']=='-1:55'
step(m,O['SCRUB_MS']-100,wait=True); assert m.seeks==[] and m.nodes[m.slider]['value']==110; passed()
# A spin ramps the step as in a long list and still only previews.
for _ in range(12): assert step(m,gap=20)==11
assert m.nodes[m.slider]['value']-110>12*O['SCRUB_STEP'] and m.seeks==[]; passed()
# The target stops at both ends while the wheel keeps turning; it reverses at once.
for key,end in ((O['KEY_NEXT'],225),(O['KEY_PREV'],0)):
    for _ in range(40): step(m,key,gap=20)
    assert m.nodes[m.slider]['value']==end and m.seeks==[]
assert step(m,gap=300)==11 and m.nodes[m.slider]['value']==5 and m.seeks==[]; passed()
# Centre commits the target once, at once, and the screen stays on; nothing seeks afterwards.
np_press(m,50); assert m.seeks==[5]
step(m,DC+O['SCRUB_MS'],wait=True); assert not m.screens and m.u.mem_read(syms['g_backlight_status'],1)[0]; ended(m,[5]); passed()
# Paused (mclGetPlayStatus 3), the seek alone, in file seconds (a CUE track's start added), not
# player_seek_time, which resumes the track: the player's second stands still until Play, the bar
# keeps the target and stock's timer stays off until it plays; playing, SETTLE_PLAY_MS without
# reaching it gives the bar back to stock's timer.
PLAY_STATUS=0xa50084
m=scrub_page(); m.handlers.pop(syms['mclGetPlayStatus']); m.word(PLAY_STATUS,3); np_press(m); step(m)
m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[1]+O['REC_CUE_START'],30); np_press(m,50)
assert did(m,'mclSetSeek')==[135] and not m.seeks and not did(m,'mclSetResume') and not did(m,'stock_press_stamp')  # Play waits, in dac_resume
step(m,O['SCRUB_MS']+5000,wait=True); assert not m.seeks and not m.starts and m.nodes[m.slider]['value']==105
m.word(m.event+O['EVENT_Y'],0)  # call's key is EVENT_X: a touch right of the slider
m.call(374,address=HOOKS['on_wm_tsdown_before_fun'][0],event_type=O['EVT_POINTER_DOWN'])
assert not m.starts and m.nodes[m.slider]['value']==105 and m.timers
m.word(PLAY_STATUS,2); step(m,1000,wait=True); assert not m.starts
step(m,1500,wait=True); assert m.starts==[m.win] and not m.timers; passed()
# Leaving and reopening Now Playing, still paused on that track and second, puts the bar back on
# the paused seek's target and waits for it again, so a new scrub starts from it; resumed (the
# second moved), the bar is stock's.
def reopen(m):
    f,ctx=m.handler(m.win,O['EVT_DESTROY']); step(m,address=f,args=(ctx,m.event,0,0),gap=0)
    m.starts=[]; m.calls.clear(); scrub_page(m=m); keep(m)
m=scrub_page(); m.handlers.pop(syms['mclGetPlayStatus']); m.word(PLAY_STATUS,3); m.playtime=100
np_press(m); step(m); np_press(m,50); assert did(m,'mclSetSeek')==[105]
reopen(m); assert m.nodes[m.slider]['value']==105 and m.nodes[m.elapsed]['text']=='1:45'
assert did(m,'playing_timer_clear')==[m.win]; step(m,5000,wait=True); assert not m.starts
np_press(m); step(m); assert m.nodes[m.slider]['value']==110; passed()
np_press(m,50); m.playtime=111; reopen(m); assert m.nodes[m.slider]['value']==111 and m.starts==[]
m.playtime=100; reopen(m); assert m.nodes[m.slider]['value']==100; passed()
# SCRUB_MS without input commits once and gives the wheel back to the volume, not a tick sooner.
m=scrub_page(); np_press(m); step(m)
step(m,O['SCRUB_MS']-1,wait=True); assert m.seeks==[] and not m.starts
step(m,1,wait=True); assert m.seeks==[105] and not m.starts  # settle_poll restarts it
step(m,O['SCRUB_MS'],wait=True); ended(m,[105]); assert m.nodes[m.slider]['value']==105; passed()
# Reversal: the target is where the wheel left it, committed once.
m=scrub_page(); np_press(m); step(m); step(m); step(m,O['KEY_PREV'])
assert m.nodes[m.slider]['value']==105; np_press(m,50); step(m,O['SCRUB_MS'],wait=True); ended(m,[105]); passed()
# No movement, including turning against an end, seeks nothing on any exit.
for end in ('centre','timeout','bound'):
    m=scrub_page(value=225 if end=='bound' else 100); np_press(m)
    if end=='bound': assert step(m)==11 and m.nodes[m.slider]['value']==225
    if end=='timeout': step(m,O['SCRUB_MS'],wait=True)
    else: np_press(m,DC)  # a second press, not a double: within DOUBLE_CLICK_MS it turns the screen off
    step(m,O['SCRUB_MS'],wait=True); ended(m,[],end); passed()
# Another press, Return (swallowed) and touch each end it, committing the target once.
for end in ('centre','return','touch'):
    m=scrub_page(); np_press(m); step(m)
    if end=='centre': np_press(m,50)
    elif end=='return': assert step(m,O['KEY_RETURN'],gap=50)==11
    else: step(m,address=HOOKS['on_wm_tsdown_before_fun'][0],event_type=O['EVT_POINTER_DOWN'],gap=50)
    step(m,O['SCRUB_MS'],wait=True); ended(m,[105],end); passed()
# A double press turns the screen off, as on every other page (the user's, 2026-11-19): a second press within
# DOUBLE_CLICK_MS of the one that started the scrub, no turn between, undoes it unseeked; a turn between makes
# the second press the scrub's own, which ends it.
m=scrub_page(); np_press(m); assert step(m,O['KEY_CENTER'],gap=100)==11 and m.screens and m.seeks==[]
step(m,O['SCRUB_MS'],wait=True); ended(m,[]); passed()
m=scrub_page(); np_press(m); step(m,O['KEY_PREV'],gap=20); np_press(m,50); step(m,DC,wait=True)
assert not m.screens; step(m,O["SCRUB_MS"],wait=True); ended(m,[95]); passed()
# Centre over the volume's band on Now Playing is Now Playing's: starting the scrub closes the dialog, so
# the wheel scrubs; a double press over the band turns the screen off, as on the page.
def vol_up(m):
    m.dlg=m.node('dialog','volume_dialog'); m.word(m.dlg+O['W_PARENT'],m.wm)
    m.nodes[m.wm]={'children':[m.win,m.dlg]}; m.stack=[m.win,m.dlg]; m.top=m.dlg; m.word(m.wm+O['W_KEY_TARGET'],m.dlg)
m=scrub_page(); vol_up(m); np_press(m); m.advance(DC,clear=False)
assert did(m,'window_close')==[m.dlg] and m.top==m.win and did(m,'playing_timer_clear')==[m.win] and not m.screens
assert step(m)==11 and m.nodes[m.slider]['value']==105; np_press(m)
step(m,DC+O['SCRUB_MS'],wait=True); assert not m.screens; ended(m,[105]); passed()
m=scrub_page(); vol_up(m)
assert m.call(O['KEY_CENTER'])==11 and m.call(O['KEY_CENTER'],gap=100,clear=False)==11 and m.screens
m.advance(DC,clear=False); assert did(m,'window_close')==[m.dlg] and not did(m,'player_seek_time'); passed()
# Not directly over Now Playing (the panel), Centre is stock's at once and nothing closes.
m=scrub_page(); vol_up(m); other=m.node('window','home_page'); m.word(other+O['W_PARENT'],m.wm)
m.nodes[m.wm]['children']=[m.win,other,m.dlg]; assert m.call(O['KEY_CENTER'])==0
m.advance(DC,clear=False); assert not did(m,'window_close') and not did(m,'playing_timer_clear') and not m.screens; passed()
# Another window on top ends it with the one commit; the page's destruction drops the target and
# keeps the timer off.
m=scrub_page(); np_press(m); step(m)
m.top=m.node('window','home_page'); step(m); assert m.seeks==[105] and not m.starts
step(m,O['SCRUB_MS'],wait=True); assert m.seeks==[105] and m.starts==[m.win] and not m.timers; passed()
m=scrub_page(); np_press(m); step(m)
f,ctx=m.handler(m.win,O['EVT_DESTROY']); step(m,address=f,args=(ctx,m.event,0,0),gap=0)
assert m.seeks==[] and not m.starts and not m.timers; passed()
# A track change since the scrub began drops the target: the next tick (without moving it), the
# timeout or an exit each end the scrub without seeking.
for end in ('tick','timeout','return'):
    m=scrub_page(); np_press(m); step(m); m.word(O['MCL_POS'],2)
    if end=='tick': assert step(m)==11 and m.nodes[m.slider]['value']==105
    elif end=='timeout': step(m,O['SCRUB_MS'],wait=True)
    else: assert step(m,O['KEY_RETURN'],gap=50)==11
    ended(m,[],end); passed()
# The lyrics page (slide value 1) with lyrics, on stock's rows, which the page keeps when it has no
# memory for its table (lyrics.c; its own rows below). Each UI loop pass (ringnav_sleep) takes the wheel
# raw (wheel_raw: nothing ticks) and glides scroll_lrc by its travel, a pixel at a time: a tick's
# 28 units at 50% are a 20 px row, eased in over the passes, within the content padded to a row
# top. Stock's timer keeps the bar going, but its follow leaves lyrics the wheel moved where they
# are until the slide_view leaves the lyrics page or another song starts, at the top. Let go, the
# wheel snaps them to the nearest row top; until then a finger stays on it (wheel_held). Another
# page, no lyrics, another window, the screen off or a finger on the screen gives the wheel back
# to its ticks and the volume.
ROWS=(20,50,20,20,25)+(20,)*7  # tops 0 20 70 90 110 ...; 275 px of content, at most 97 down
sleep_hook=HOOKS['main_loop_sleep_default'][0]
def lyric_page(page=1,size=9,lines=len(ROWS),config=None):  # stock's rows: no memory for the page's table
    m=scrub_page(); m.mock('calloc@GLIBC_2.0','pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')
    m.fail_calloc=True  # preserve this fixture's allocation-failure path with scope's allocator mock
    m.word(0xa3a270,0x1000010); m.handlers[0x1000010]='free'  # free's GOT slot, as Coverflow's
    m.lyric_size=size; m.handlers[sleep_hook+12]='stock_sleep'; m.config.update(config or {})
    rows=[m.node('label') for _ in range(lines)]
    for i,r in enumerate(rows): m.word(r+O['W_Y'],sum(ROWS[:i])); m.word(r+O['W_H'],ROWS[i])
    m.lrc=m.node('scroll_view','scroll_lrc',rows)
    m.word(m.lrc+O['W_H'],178); m.word(m.lrc+O['VIEW_CONTENT_H'],sum(ROWS))
    m.slide=m.node('slide_view','slide_view',[m.lrc],value=page)
    m.nodes[m.win]['children'].append(m.slide)
    assert m.call(address=playing,args=(m.win,7,0,0),gap=0)==0; m.calls=[]
    m.word(payload_syms['wheel_held'],1)
    return m
def y(m): return m.get(m.lrc+O['SCROLL_Y'])
def raw(m): return m.get(payload_syms['wheel_raw'])
def loop(m,travel=None,ms=8):  # a UI loop pass ms on, the wheel turned travel raw units since the last
    if travel is not None: m.word(payload_syms['wheel_travel'],travel&0xffffffff)
    n=len(m.calls); m.advance(ms,clear=False); m.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0,clear=False)
    for name,seen in (('player_seek_time','seeks'),('playing_timer_start','starts')):
        setattr(m,seen,getattr(m,seen,[])+[c[1] for c in m.calls[n:] if c[0]==name])
    return y(m)
def settle(m,n=60): return [loop(m) for _ in range(n)]
def follow(m,to): m.call(address=payload_syms['ringnav_lyric_follow'],args=(m.lrc,0,to,10),gap=0,clear=False); return y(m)
def hold(m): loop(m,-56); loop(m,56); settle(m)  # LYRIC_PUSH_DETENTS turned there and back: held at the top
for kw in ({'page':0},{'size':0},{'lines':0}):
    m=lyric_page(**kw); loop(m); assert raw(m)==0 and m.get(payload_syms['wheel_travel'])==0
    assert m.call()==0 and not did(m,'playing_timer_clear'),kw; passed()
# Enlarged Album Art (#153): off the art page each pass holds stock's countdown full, so the art never
# opens there; on the art page it runs.
for page,left in ((1,O['BC_IDLE_FULL']),(0,3)):
    m=lyric_page(page=page); m.byte(O['BC_IDLE'],3); loop(m); assert m.u.mem_read(O['BC_IDLE'],1)[0]==left,page
passed()
# Smart Awake (#57), a song playing, holds the screen on the lyrics page and over the full-screen
# art; the art page, or paused, times out as usual.
def awake(page,art=False,status=2):
    CONFIG['KEEPLIT']='1'; m=lyric_page(page=page); del CONFIG['KEEPLIT']; m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(PLAY_STATUS,status)
    if art:
        bw=m.node('window','bigcover_page'); m.handlers[HOOKS['bigcover_page_init'][0]+12]='stock_bigcover'
        m.call(address=HOOKS['bigcover_page_init'][0],args=(bw,7,0,0),gap=0); m.top=bw
    m.advance(1000); m.calls=[]; loop(m); return ('reset_poweroptions_timer',1,1,1) in [c[:4] for c in m.calls]
assert awake(1) and awake(0,art=True) and not awake(0) and not awake(0,art=True,status=3); passed()
# Following, a lone tick meets a list end's resistance: it nudges them BUMP_PX while the finger
# stays, and they spring back on release, still following; slower ticks only nudge again.
m=lyric_page(); assert loop(m)==0 and raw(m)==1; m.calls=[]
loop(m,-28); assert settle(m)[-1]==O['BUMP_PX'] and follow(m,0)==O['BUMP_PX']
assert ('reset_poweroptions_timer',1,1,1) in [c[:4] for c in m.calls]  # the screen stays on
m.word(payload_syms['wheel_held'],0); assert settle(m)[-1]==0; m.word(payload_syms['wheel_held'],1)
follow(m,20); assert settle(m)[-1]==20; m.advance(O['LYRIC_PUSH_MS'])
loop(m,28); assert settle(m)[-1]==20-O['BUMP_PX'] and follow(m,20)==20-O['BUMP_PX']; passed()
# A second tick within LYRIC_PUSH_MS takes over, from the nudge; let go, it snaps and stays.
m=lyric_page(); loop(m); loop(m,-28); m.advance(500); loop(m,-28)
assert settle(m)[-1]==26; m.word(payload_syms['wheel_held'],0); assert settle(m)[-1]==20 and follow(m,0)==20; passed()
# Clockwise (x falling) scrolls down: a tick's travel glides one row, never a jump, slowing in.
m=lyric_page(); assert loop(m)==0 and raw(m)==1; hold(m); m.calls=[]
ys=[loop(m,-28)]+settle(m); steps=[b-a for a,b in zip([0]+ys,ys)]
assert ys[-1]==20 and 0<ys[0]<5 and all(0<=s<=4 for s in steps) and steps[-1]==0,ys
assert ('reset_poweroptions_timer',1,1,1) in [c[:4] for c in m.calls]  # the screen stays on
assert not did(m,'playing_timer_clear') and not did(m,'sleep_ms'); passed()  # nor does the loop idle
# A pixel's worth moves a pixel, fractions carried: three single units are 34/16 px.
for _ in range(3): loop(m,-1)
settle(m); assert y(m)==22; passed()
# A spin keeps gliding and stops at the padded end, a row top: 110 + 178, not 275 - 178 = 97.
for _ in range(60): loop(m,-12)
assert settle(m)[-1]==110 and m.get(m.lrc+O['VIEW_CONTENT_H'])==288; passed()
# Anticlockwise back up, to the top.
for _ in range(60): loop(m,12)
assert settle(m)[-1]==0; passed()
# Wheel Sensitivity sets the travel per row as per tick: 14 units at 100%.
m=lyric_page(config={'WHEELSENSITIVITY':'100'}); loop(m); hold(m); loop(m,-14); assert settle(m)[-1]==20; passed()
# Held: stock's follow leaves them, also after SCRUB_MS, while the bar keeps going.
m=lyric_page(); loop(m); loop(m,-56); settle(m); assert y(m)==40
assert follow(m,0)==40; m.advance(O['SCRUB_MS']+1000); assert settle(m)[-1]==40 and follow(m,0)==40
assert not did(m,'playing_timer_clear') and not m.starts; passed()
# Swiped away and back, the hold stays: the follow leaves them, hidden or shown.
m.nodes[m.slide]['value']=0; loop(m); assert raw(m)==0 and follow(m,70)==40
m.nodes[m.slide]['value']=1; loop(m); assert raw(m)==1 and follow(m,90)==40 and settle(m)[-1]==40; passed()
# Now Playing gone and opened again, the hold is gone: the follow moves them.
f,ctx=m.handler(m.win,O['EVT_DESTROY']); step(m,address=f,args=(ctx,m.event,0,0),gap=0)
scrub_page(m=m); m.nodes[m.win]['children'].append(m.slide); loop(m); follow(m,90); assert settle(m)[-1]==90; passed()
# Another Now Playing opened before this one goes (np_gone leaves a page it replaces alone): the hold is gone too.
m=lyric_page(); loop(m); loop(m,-56); settle(m); assert follow(m,0)==40
scrub_page(m=m); m.nodes[m.win]['children'].append(m.slide); loop(m); follow(m,90); assert settle(m)[-1]==90; passed()
# Another song starts at the top, on the page or off it, the hold gone: its follow glides to its line.
for off in (0,1):
    m=lyric_page(); loop(m); loop(m,-56); settle(m); assert follow(m,0)==40
    if off: m.top=m.node('window','home_page')
    m.word(O['MCL_POS'],2); assert loop(m)==0; m.top=m.win; loop(m)
    follow(m,20); assert settle(m)[-1]==20; passed()
# The same song (its queue place and file) leaves them where they are.
m=lyric_page(); loop(m); loop(m,-56); settle(m); m.word(O['MCL_POS'],1); assert loop(m)==40 and follow(m,0)==40; passed()
# The page's row rebuild (LYRIC_ROWS, stock's mocked) keeps the same song's place; another song's
# rows start at the top whatever moved the view since, and drop the hold.
def rows(m): m.call(address=payload_syms['ringnav_lyric_rows'],args=(m.win,0,0,0),gap=0,clear=False); return y(m)
m=lyric_page(); m.handlers[0x53b67c]='stock_lyric_rows'; loop(m); loop(m,-56); settle(m)
assert rows(m)==40 and follow(m,0)==40; passed()
m.word(O['MCL_POS'],2); m.word(m.lrc+O['SCROLL_Y'],60); assert rows(m)==0 and loop(m)==0
follow(m,20); assert settle(m)[-1]==20; passed()  # the follow is back
# A swipe that brings the lyrics in holds nothing, even while something else moves them (stock's
# scroll animation, ending after the screen comes on); a finger that lands on them stops that.
m=lyric_page(page=0); loop(m); m.pressed=1; loop(m); m.nodes[m.slide]['value']=1; loop(m)
m.word(m.lrc+O['SCROLL_Y'],60); loop(m); m.pressed=0; loop(m); follow(m,0); assert settle(m)[-1]==0; passed()
m=lyric_page(); loop(m); a=m.alloc(0x80); m.word(m.lrc+O['VIEW_ANIMATOR'],a); m.pressed=1; loop(m)
assert m.get(m.lrc+O['VIEW_ANIMATOR'])==0 and ('widget_animator_destroy',a) in [c[:2] for c in m.calls]; passed()
# The report: scroll, swipe away, screen off, next song, screen on, swipe back: the new song's top.
m=lyric_page(); m.handlers[0x53b67c]='stock_lyric_rows'; loop(m); loop(m,-56); settle(m)
m.nodes[m.slide]['value']=0; loop(m); assert follow(m,20)==40  # off the page, still held
m.byte(syms['g_backlight_status'],0); loop(m); m.word(O['MCL_POS'],2); assert loop(m)==0
m.word(m.lrc+O['SCROLL_Y'],60); assert rows(m)==0  # a stale follow, then the new song's rows
m.byte(syms['g_backlight_status'],1); loop(m); m.pressed=1; loop(m); m.nodes[m.slide]['value']=1; loop(m)
m.word(m.lrc+O['SCROLL_Y'],20); loop(m); m.pressed=0; loop(m)
follow(m,0); assert settle(m)[-1]==0; passed()
# Let go, the wheel snaps them to the nearest row top, down or up, gliding; held, they stay.
m=lyric_page(); loop(m); hold(m); loop(m,-36); assert settle(m,200)[-1]==26  # tops 0 20 70: 26 is nearer 20
m.word(payload_syms['wheel_held'],0); ys=settle(m); assert ys[-1]==20 and ys[0]>20,ys
m.word(payload_syms['wheel_held'],1); loop(m,-50); assert settle(m)[-1]==56  # nearer 70
m.word(payload_syms['wheel_held'],0); assert settle(m)[-1]==70 and follow(m,0)==70; passed()  # still held
# Let go during the glide, it snaps from the glide's target; a still finger let go moves nothing.
m=lyric_page(); loop(m); hold(m); loop(m,-36); m.word(payload_syms['wheel_held'],0); assert settle(m)[-1]==20
m.word(payload_syms['wheel_held'],1); m.word(m.lrc+O['SCROLL_Y'],30); loop(m); m.word(payload_syms['wheel_held'],0)
assert settle(m)[-1]==30; passed()  # not the wheel's doing: no snap
# Another window, the screen off, a finger down or no lyrics hand the wheel back to the ticks,
# dropping travel from before; on the page again the wheel is the lyrics' once more.
def other(m): m.top=m.node('window','home_page')
def back(m): m.top=m.win
def dark(m,on=0): m.byte(syms['g_backlight_status'],on)
def touch(m,on=1): m.pressed=on
def bare(m,on=0): m.lyric_size=9 if on else 0
for off,on in ((other,back),(dark,lambda m: dark(m,1)),(touch,lambda m: touch(m,0)),(bare,lambda m: bare(m,1))):
    m=lyric_page(); loop(m); assert raw(m)==1
    off(m); loop(m,-28); settle(m); assert raw(m)==0 and y(m)==0,off
    m.word(payload_syms['wheel_travel'],-99&0xffffffff); on(m); loop(m); assert raw(m)==1 and settle(m)[-1]==0,off
    hold(m); loop(m,-28); assert settle(m)[-1]==20,off; passed()
# A finger that drags them holds them as the wheel does; the follow waits while it is down. A tap
# that moves nothing holds nothing.
m=lyric_page(); loop(m); m.pressed=1; loop(m); m.word(m.lrc+O['SCROLL_Y'],40)
assert follow(m,0)==40; m.pressed=0; loop(m); assert follow(m,0)==40 and settle(m)[-1]==40; passed()
m=lyric_page(); loop(m); m.pressed=1; loop(m); m.pressed=0; loop(m); follow(m,20); assert settle(m)[-1]==20; passed()
m=lyric_page(); loop(m); m.top=m.node('window','home_page'); m.pressed=1; loop(m); m.word(m.lrc+O['SCROLL_Y'],40)
m.pressed=0; loop(m); m.top=m.win; loop(m); follow(m,0); assert settle(m)[-1]==0; passed()  # another window's touch
# A tick (before a pass took the wheel raw) glides to the next or previous row's top, quick ticks
# adding up from the glide's target, ahead of scrub and the volume.
m=lyric_page()
assert step(m)==11 and y(m)==0 and settle(m)[-1]==20
ys=[]
for _ in range(5): assert step(m,gap=200)==11; ys.append(settle(m)[-1])
assert ys==[70,90,110,110,110],ys
for _ in range(3): assert step(m,O['KEY_PREV'],gap=200)==11
assert settle(m)[-1]==20 and not did(m,'playing_timer_clear') and not m.starts and follow(m,0)==20; passed()
# A scrub begun on the lyrics page (a Centre press, np_centre) ends at the next tick, committing once,
# and the tick scrolls; the seek's settle wait brings stock's timer back.
m=lyric_page(); assert m.call(O['KEY_CENTER'],gap=1000)==11 and did(m,'playing_timer_clear')==[m.win]
m.nodes[m.slide]['value']=0; step(m); m.nodes[m.slide]['value']=1
assert step(m)==11 and m.seeks==[105] and settle(m)[-1]==20
step(m,O['SCRUB_MS'],wait=True); assert m.starts==[m.win] and not m.timers; passed()
# The scrub's own touch end (np_cancel) keeps the hold; the page's destruction drops it and the
# wheel's raw travel.
m=lyric_page(); loop(m); hold(m); loop(m,-28); settle(m)
step(m,address=HOOKS['on_wm_tsdown_before_fun'][0],event_type=O['EVT_POINTER_DOWN'],gap=50)
assert follow(m,0)==20 and not m.starts and not m.timers; passed()
f,ctx=m.handler(m.win,O['EVT_DESTROY']); step(m,address=f,args=(ctx,m.event,0,0),gap=0)
loop(m); assert raw(m)==0 and not m.starts and not m.timers; passed()

# Lyrics from tags (lyrics.c ringnav_lyric): stock's loader (mocked) runs first; only when it left no
# lines for a local song does TagLib open the file, LYRICS then UNSYNCEDLYRICS, a line each into stock's
# list at a time no track reaches. Online queues, other rows and an unbound GOT entry ask nothing.
import subprocess
from build import GP
got=next(int(l.split()[0],16) for l in subprocess.run(['readelf','-AW',B/'stock-demo'],capture_output=True,text=True).stdout.splitlines() if l.endswith(' taglib_file_new'))
lib=symbols(B/'libtag_c.so.2.0.1')
assert got==O['TAGLIB_NEW_GOT'] and [lib[n] for n in ('taglib_file_new','taglib_property_get','taglib_property_free')]==[O['TAGLIB_FILE_NEW'],O['TAGLIB_PROPERTY_GET'],O['TAGLIB_PROPERTY_FREE']]
m=Machine(); assert m.get(GP-0x7f44)==0xa50000 and m.get(0x5bd354)==0x8c44ff84 and m.get(0x5bd340)==0x2624ff90; passed()  # -0x68c, -0x680
TAGLIB=0x1180000  # libtag_c's load address here
class LyricMachine(QueueMachine):
    def __init__(self,tags,lrc=(),cls=0xf001,kind=8,bound=True):
        super().__init__(cls=cls); self.tags=tags; self.lrc=list(lrc); self.bound=bound; self.lines=[]; self.asked=[]; self.tag_calls=[]
        self.word(self.items(self.get(syms['mcl_pdeqplaylist']))[0]+O['REC_TYPE'],kind)
        self.word(O['MCL_LYRICS'],self.deque([],'stLyricInfo'))
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')
        self.word(0xa3a270,0x1000010); self.handlers[0x1000010]='lyric:libc_free'  # free's GOT slot, as Coverflow's (the stamps' table)
        self.handlers[int(manifest['patch_symbols']['stock_lyric_trampoline'],16)]='lyric:stock'
        for n,off in (('get',O['TAGLIB_PROPERTY_GET']),('pfree',O['TAGLIB_PROPERTY_FREE'])): self.handlers[TAGLIB+off]='lyric:'+n
        for n in ('taglib_file_new','taglib_file_free','mclGetLyricSize','_deque_push_back'): self.handlers[syms[n]]='lyric:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('lyric:'): return super().hook(u,address,size,unused)
        name=name[6:]; a,b=[u.reg_read(r) for r in REGS[:2]]; ret=0
        if name=='stock': self.tag_calls.append('stock'); self.lines=list(self.lrc); ret=1 if self.lrc else -1
        elif name=='mclGetLyricSize': ret=len(self.lines)
        elif name=='_deque_push_back' and a==self.get(O['MCL_LYRICS']): self.lines.append((signed(self.get(b)),self.text(self.get(b+4))))
        elif name=='_deque_push_back': return super().hook(u,address,size,unused)
        elif name=='libc_free':
            if a and getattr(self,'zero_freed',0): self.u.mem_write(a,b'\0'*len(self.raw(a)))  # a use after free reads nothing
        elif name=='taglib_file_new':
            assert b==0  # Embedded lyrics need tags only.
            self.tag_calls.append(('new',self.text(a))); ret=0x1170000
            if self.bound: self.word(O['TAGLIB_NEW_GOT'],TAGLIB+O['TAGLIB_FILE_NEW'])
        elif name=='get':
            assert a==0x1170000; key=self.text(b); self.asked.append(key)
            if key in self.tags: ret=self.alloc(8); self.word(ret,self.string(self.tags[key]) if self.tags[key] is not None else 0); self.word(ret+4,0)
        else: assert name=='pfree' or a==0x1170000; self.tag_calls.append(name.removeprefix('taglib_file_'))
        self.returns(ret,clobber=True)
    def load(self): return self.call(address=HOOKS['mclLoadExLyric'][0],args=(0,0,0,0),count=5000000)
FAR=0x7fffffff
m=LyricMachine({'LYRICS':'x'},lrc=['[00:01.00]a']); assert m.load()==1 and m.tag_calls==['stock'] and m.lines==['[00:01.00]a']; passed()
m=LyricMachine({'LYRICS':'First\r\n\nThird\n'}); assert m.load()==1 and m.asked==['LYRICS']
assert m.lines==[(FAR,'First'),(FAR,'Third')] and m.tag_calls==['stock',('new','/p/A'),'pfree','free'],m.tag_calls; passed()
m=LyricMachine({'UNSYNCEDLYRICS':'Only\rline'}); assert m.load()==1 and m.asked==['LYRICS','UNSYNCEDLYRICS'] and m.lines==[(FAR,'Only'),(FAR,'line')]; passed()
for empty in ('',None):
    m=LyricMachine({'LYRICS':empty,'UNSYNCEDLYRICS':'Fallback'}); assert m.load()==1 and m.asked==['LYRICS','UNSYNCEDLYRICS'] and m.lines==[(FAR,'Fallback')]
    assert m.tag_calls==['stock',('new','/p/A'),'pfree','pfree','free']; passed()  # each property list freed once
m=LyricMachine({}); assert m.load()==-1 and m.asked==['LYRICS','UNSYNCEDLYRICS'] and not m.lines and m.tag_calls==['stock',('new','/p/A'),'free']; passed()
for kw in ({'cls':0xf1},{'cls':0xf2},{'kind':4}):
    m=LyricMachine({'LYRICS':'x'},**kw); assert m.load()==-1 and m.tag_calls==['stock'] and not m.lines,kw; passed()
m=LyricMachine({'LYRICS':'x'},bound=False); assert m.load()==-1 and not m.asked and not m.lines and m.tag_calls[-1]=='free'; passed()
m=LyricMachine({'LYRICS':('a'*99+'\n')*700}); assert m.load()==1 and len(m.lines)==656 and m.lines[-1]==(FAR,'a'*99); passed()  # 64 KB
# A tag in LRC form (q2-deezer's synced lyrics): each stamped line at its stamp's whole seconds, as stock's
# .lrc parser keeps them, in time order, a line with two stamps twice; "[ar:...]" and the lines before the
# first stamp, and the unstamped ones after it, left out, and so is a stamp alone.
m=LyricMachine({'LYRICS':'[ar:Someone]\nIntro\n[00:12.50]Two\r\n[00:03]One\n[01:02.00][00:20.10]Both\nloose\n[00:30.00]\n'})
assert m.load()==1 and m.lines==[(3,'One'),(12,'Two'),(20,'Both'),(62,'Both')],m.lines; passed()
# Bracketed text that is no stamp leaves the lyrics plain.
m=LyricMachine({'LYRICS':'[Chorus]\nLa la\n[1:2]\n[00:61]x'}); assert m.load()==1
assert m.lines==[(FAR,'[Chorus]'),(FAR,'La la'),(FAR,'[1:2]'),(FAR,'[00:61]x')],m.lines; passed()

# Millisecond stamps and A2 word stamps ("<mm:ss.xx>" before a word): stock's list keeps whole seconds and
# the text without the word stamps, in millisecond order.
m=LyricMachine({'LYRICS':'[00:12.50]<00:12.50>Hel<00:12.90>lo <00:13.40>world<00:14.00>\n[00:15.05][00:40.00]<00:15.10>Twice\n[00:16]<00:16.00>\n'})
assert m.load()==1 and m.lines==[(12,'Hello world'),(15,'Twice'),(40,'Twice')],m.lines; passed()
m=LyricMachine({'LYRICS':'[00:01.999]a\n[00:01.5]b\n[00:01:25]c\n[00:01.012345]d\n'})
assert m.load()==1 and m.lines==[(1,'d'),(1,'c'),(1,'b'),(1,'a')],m.lines; passed()
m=LyricMachine({'LYRICS':'a<b>c <1:2> <00:61.00>x <00:01.00\n'}); assert m.load()==1 and m.lines==[(FAR,'a<b>c <1:2> <00:61.00>x <00:01.00')]; passed()
# Even spacing: no line keeps the spaces at its ends (ASCII's, no-break, ideographic, zero-width, a byte-order
# mark), and a blank one goes, an empty line between verses, a line of spaces, a stamp alone or CR CR LF's;
# lyrics that are all blank leave stock's result.
m=LyricMachine({'LYRICS':'One\r\r\nTwo\r\r\n\r\r\n\n\nThree\r\r\n'}); assert m.load()==1 and m.lines==[(FAR,'One'),(FAR,'Two'),(FAR,'Three')],m.lines; passed()
m=LyricMachine({'LYRICS':'  \t\n\u3000\n\u00a0x\u00a0 \n\u200b\n\ufeff  y  \v\n'}); assert m.load()==1 and m.lines==[(FAR,'x'),(FAR,'y')],m.lines; passed()
m=LyricMachine({'LYRICS':'[00:01.00]A\n[00:02.00]\n[00:03.00]  \u3000\n\n[00:04.00]  B  \n'}); assert m.load()==1 and m.lines==[(1,'A'),(4,'B')],m.lines; passed()
m=LyricMachine({'LYRICS':'[00:01.00] <00:01.00>Hi <00:01.50>there <00:02.00>\n'}); assert m.load()==1 and m.lines==[(1,'Hi there')]; passed()
m=LyricMachine({'LYRICS':' \n\r\n[00:02.00]\n'}); assert m.load()==-1 and not m.lines and m.tag_calls==['stock',('new','/p/A'),'pfree','free']; passed()

# A .lrc (stock's loader mocked: its fopen comes here, LYRIC_OPEN, then its parse) is read again, as a tag:
# its synced lines replace stock's whole seconds, word stamps cut out, blank lines gone, a UTF-8 mark at its
# start skipped; unsynced, stock's stay.
from build import LYRIC_OPEN
assert struct.unpack_from('<I',demo,fileoff(demo,LYRIC_OPEN[0]))[0]==jal(payload_syms['ringnav_lrc_open'])
class LrcMachine(LyricMachine):
    def __init__(self,tags={},lrc=(),path=None,files={}):
        super().__init__(tags); self.k_stock=list(lrc); self.k_path=path; self.k_files=dict(files)
        for n in ('fopen@GLIBC_2.2','fread@GLIBC_2.0','fclose@GLIBC_2.2','deque_clear'): self.handlers[syms[n]]='k:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        a,b,c,d=[u.reg_read(r) for r in REGS[:4]]; ret=0
        if name=='lyric:stock':
            self.tag_calls.append('stock')
            if self.k_path: self.nested(payload_syms['ringnav_lrc_open'],self.string(self.k_path),self.string('r'))
            self.lines=list(self.k_stock); return self.returns(1 if self.k_stock else -1,clobber=True)
        if name=='k:fopen@GLIBC_2.2': ret=0x1170100 if self.text(a) in self.k_files else 0; self.k_opened=self.text(a)
        elif name=='k:fread@GLIBC_2.0': data=self.k_files[self.k_opened][:b*c]; self.u.mem_write(a,data); ret=len(data)//b
        elif name=='k:deque_clear': assert a==self.get(O['MCL_LYRICS']); self.lines=[]
        elif name!='k:fclose@GLIBC_2.2': return super().hook(u,address,size,unused)
        self.returns(ret,clobber=True)
lrc='\ufeff[ar:x]\n[00:01.20]<00:01.20>A<00:01.70>b\n[00:01.90]\n\n[00:00.50]  Zero\r\r\n'.encode()
m=LrcMachine(path='/mnt/mmc/a.lrc',files={'/mnt/mmc/a.lrc':lrc},lrc=[(1,'<00:01.20>A<00:01.70>b'),(1,' '),(0,'  Zero')])
assert m.load()==1 and m.lines==[(0,'Zero'),(1,'Ab')],m.lines; passed()
m=LrcMachine(path='/mnt/mmc/a.lrc',files={'/mnt/mmc/a.lrc':b'plain\nwords'},lrc=[(FAR,'plain'),(FAR,'words')])
assert m.load()==1 and m.lines==[(FAR,'plain'),(FAR,'words')]; passed()
# Stock's test of whether a .lrc is UTF-8 before it converts it from GB18030 (mclIsUtf8WithoutBOM) is
# ringnav_lrc_utf8's, over the file: stock's read 6 bytes from the first non-ASCII one and no 4-byte
# character, so an emoji, or a character starting 4 or 5 bytes on (Korean's one-syllable words), failed it.
m=LrcMachine(files={'/e':'[00:01.00]Hi 🎵\n'.encode(),'/q':'[00:01.00]Café da’s\n'.encode(),
                    '/k':'[00:01.00]나 는\n'.encode(),'/a':b'[00:01.00]plain\n',
                    '/g':'[00:01.00]你好\n'.encode('gb18030'),'/l':'[00:01.00]Café\n'.encode('latin-1')})
def is_utf8(path): return m.call(address=HOOKS['mclIsUtf8WithoutBOM'][0],args=(m.string(path),0,0,0),gap=0)
assert [is_utf8(p) for p in ('/e','/q','/k','/a','/g','/l','/none')]==[1,1,1,1,0,0,0]; passed()
# A card app's streamed track (a 0xf1 queue, id app:<gen>:<n>; tidal.c app_urls_play) reads the .lrc the app left
# beside its queue (app_lyrics: /tmp/q2-<id>.lyrics/<n>.lrc), synced or plain, for the latest queue only. au:
# lock[16], urls, n, gen at 72, play, lyr at 80.
def app_lrc(rec_id,gen=3,body=b'[00:01.00]Hi\n[00:02.50]there\n'):
    m=LrcMachine(files={'/tmp/q2-jellyfin.lyrics/2.lrc':body}); m.word(O['MCL_TYPE'],0xf1)
    m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[0]+O['REC_TIDAL_ID'],m.string(rec_id))
    au=payload_syms['au']; m.word(au+72,gen); m.u.mem_write(au+80,b'/tmp/q2-jellyfin.lyrics\0')
    return m
m=app_lrc('app:3:2'); assert m.load()==1 and m.lines==[(1,'Hi'),(2,'there')] and m.k_opened=='/tmp/q2-jellyfin.lyrics/2.lrc',m.lines; passed()
m=app_lrc('app:3:2'); m.zero_freed=1; assert m.load()==1; t=m.get(payload_syms['ly.1']); assert t and [m.get(m.get(t+16)+i*4) for i in range(2)]==[72,105]; passed()  # the published table's words, read before the text is freed
m=app_lrc('app:3:2',body=b'\xef\xbb\xbfPlain\nwords\n'); assert m.load()==1 and m.lines==[(FAR,'Plain'),(FAR,'words')],m.lines; passed()
m=app_lrc('app:2:2'); assert m.load()==-1 and not m.lines; passed()  # an older queue's track
m=app_lrc('app:3:1'); assert m.load()==-1 and not m.lines; passed()  # the app left none for it
m=app_lrc('12345'); assert m.load()==-1 and not m.lines; passed()  # a Tidal track: none

# The lyrics page (lyrics.c, iPod). Each load sends its lines, milliseconds
# and word stamps, to the page; stock's rows (its builder mocked: a label a line, its text and lrc_time
# seconds, offset 0; its page init builds them too) become blank rows as high as their lines' rows of
# Inter at LYRIC_OLED_PX, LY_PITCH apart, plus LYRIC_GAP, which the list layout (mocked) stacks after a blank
# LYRIC_SLOT, each painted with its line from LYRIC_OLED_X; the page centres its playing line by its own clock,
# the player's whole seconds and time_now_ms. The canvas measures a character as half its font's pixels here.
SLOT=O['LYRIC_SLOT']; REST,LIT=color_t(0xababab),color_t(O['LYRIC_LIT'])  # REST: lyrics.c LYRIC_OLED_REST, Text Secondary
LPX,LX=27,24; PITCH=LPX+LPX//8; ROW=PITCH+SLOT-(34+34//8)  # lyrics.c LYRIC_OLED_PX, LYRIC_OLED_X, LY_PITCH; LYRIC_GAP (LYRIC_PX 34)
def mid(i,rows=None):  # the offset that centres line i (one-row lines before it, or rows: their heights) in the 178 px view
    hs=rows or [ROW]*(i+1); return max(0,SLOT+sum(hs[:i])+int((hs[i]-178)/2))  # C's division
class KaraokeMachine(LrcMachine):
    def __init__(self,tags={}):
        super().__init__(tags)
        self.k_inks=[]  # self.lrc: the page's scroll_lrc
        self.handlers[playing+12]='stock_playing'; self.handlers[0x53b67c]='k:rows'
        for n in ('widget_layout_children','widget_get_canvas','canvas_draw_text','canvas_measure_text'):
            self.handlers[syms[n]]='k:'+n
        self.handlers.pop(syms['mclGetPlayStatus']); self.word(PLAY_STATUS,2)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        a,b,c,d=[u.reg_read(r) for r in REGS[:4]]; ret=0
        if name not in ('k:rows','k:widget_layout_children','k:widget_get_canvas','k:canvas_draw_text','k:canvas_measure_text'):
            return super().hook(u,address,size,unused)
        name=name[2:]
        if name=='rows': self.build()
        elif name=='widget_layout_children':  # list_view(x=X,y=Y,s=0): stacked from Y
            at=int(re.search(r'y=(\d+)',self.nodes[a]['children_layout'])[1])
            for k in self.nodes[a]['children']: self.word(k+O['W_Y'],at); at+=self.get(k+O['W_H'])
        elif name=='widget_get_canvas': ret=self.canvas
        elif name=='canvas_measure_text': u.reg_write(UC_MIPS_REG_F0,struct.unpack('<I',struct.pack('<f',c*self.font[1]/2))[0])
        elif name=='canvas_draw_text':
            self.k_inks.append((''.join(chr(self.get(b+4*j)) for j in range(c)),signed(d),signed(self.get(u.reg_read(UC_MIPS_REG_SP)+16)),
                              self.get(self.lcd+O['LCD_TEXT_COLOR']),self.font[1],self.clip,self.font[0]))
        self.returns(ret,clobber=True)
    def build(self):  # stock's rows: a 225 px label a line, 20 px high
        kids=[self.node('label',text=t,lrc_time=sec) for sec,t in self.lines]
        for k in kids: self.word(k+O['W_PARENT'],self.lrc); self.word(k+O['W_W'],225); self.word(k+O['W_H'],20)
        self.nodes[self.lrc]['children']=kids; self.word(self.lrc+O['SCROLL_Y'],0)
    def show(self,playtime=0,lrc=None,bar=False):  # Now Playing opened: stock's init builds the rows, then ringnav_playing
        self.playtime=playtime; self.lrc=lrc or self.node('scroll_view','scroll_lrc')  # lrc: AWTK reusing its memory
        self.word(self.lrc+O['W_W'],375); self.word(self.lrc+O['W_H'],178); self.build()
        self.slide=self.node('slide_view','slide_view',[self.lrc],value=1)
        self.slider=bar and self.node('slider','slider_play',max=225,value=playtime)  # bar: the progress bar
        kids=[self.slide]+[self.slider]*bool(bar)
        self.win=self.top=self.node('window','playing_page',kids); self.word(self.win+O['W_PARENT'],self.wm)
        assert self.call(address=playing,args=(self.win,7,0,0),gap=0)==0
        self.handlers[sleep_hook+12]='stock_sleep'; self.word(payload_syms['wheel_held'],1)
        return y(self)
    def rows(self): self.call(address=payload_syms['ringnav_lyric_rows'],args=(self.win,0,0,0),gap=0,clear=False); return y(self)
    def lyric_row(self,i): return self.nodes[self.lrc]['children'][i]
    def at_ms(self,ms=0): self.advance(ms,clear=False); return self.call(address=payload_syms['lyrics_now'],args=(0,0,0,0),gap=0,clear=False)
    def ink(self,i):  # row i's draws from the text column (self.k_inks: all of them)
        self.k_inks=[]; self.clip=(0,0,375,SLOT); self.paint(self.lyric_row(i),gap=0); return [x for x in self.k_inks if x[1]==LX]
    def dirty(self): return [c[1] for c in self.calls if c[0]=='widget_invalidate_force' and c[1] in self.nodes[self.lrc]['children']]
def karaoke(lines=((5,'One'),(9,'Two two'),(12,'Three'),(20,'Four'),(30,'Five')),**kw):
    m=KaraokeMachine(**kw); m.lines=list(lines); return m
# The rows stock's init built turn blank rows the page's width, ROW (a pitch and LYRIC_GAP) for a line of one
# row, line i's top LYRIC_SLOT and i rows down; with no load's table, the labels' (whole seconds, no word stamps).
# Before the first line the first sits in the middle, unlit, all Text Secondary; the playing line glides there.
m=karaoke(); assert m.show(4)==0 and m.nodes[m.lrc]['children_layout']==f'list_view(x=0,y={SLOT},s=0)'
assert [(m.nodes[r]['text'],m.get(r+O['W_H']),m.get(r+O['W_W']),m.get(r+O['W_Y'])) for r in m.nodes[m.lrc]['children']]==[('',ROW,375,SLOT+i*ROW) for i in range(5)]
assert [(x[0],x[3],x[6]) for x in m.ink(0)]==[('One',REST,'inter')]; passed()  # Inter Regular
assert settle(m)[-1]==0 and m.get(m.lrc+O['VIEW_CONTENT_H'])==SLOT+4*ROW+int((ROW-178)/2)+178; passed()  # the last line can reach the middle
m.playtime=5; assert settle(m)[-1]==0; m.playtime=9; ys=settle(m); assert ys[-1]==mid(1) and 0<ys[0]<mid(1),ys; passed()
# Off the page it jumps, at stock's timer's pace (LYRIC_FOLLOW_MS); the wheel moves them a line a tick's
# worth (past a lone tick's nudge) and holds them, off the follow, until the page swipes away and back.
m.nodes[m.slide]['value']=0; m.playtime=12; assert loop(m)==mid(1) and loop(m,ms=250)==mid(2)
m.nodes[m.slide]['value']=1; loop(m)
loop(m,-28); loop(m,-28); m.word(payload_syms['wheel_held'],0); assert settle(m)[-1]==SLOT+2*ROW  # two ticks take over: line 2's top
m.word(payload_syms['wheel_held'],1); m.playtime=30; assert settle(m)[-1]==SLOT+2*ROW; passed()
m.nodes[m.slide]['value']=0; assert loop(m,ms=250)==SLOT+2*ROW; m.nodes[m.slide]['value']=1; assert settle(m)[-1]==SLOT+2*ROW; passed()
# Now Playing gone and opened again, they follow: the playing line.
f,ctx=m.handler(m.win,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.show(30); assert settle(m)[-1]==mid(4); passed()
# Dark, the page in front follows at that pace too, by the whole second, reading no clock between.
m.byte(syms['g_backlight_status'],0); m.playtime=20; assert loop(m)==mid(4) and loop(m,ms=250)==mid(3)
m.byte(syms['g_backlight_status'],1); m.playtime=30; assert settle(m)[-1]==mid(4); passed()
# Another song starts at the top, and its first rows too; a refresh's rows for the same song keep the table
# and the place; a finger on the screen keeps them where they are.
m.word(O['MCL_POS'],2); m.playtime=0; assert loop(m)==0 and settle(m)[-1]==0 and m.rows()==0; passed()
m.playtime=12; assert settle(m)[-1]==mid(2) and m.rows()==mid(2) and settle(m)[-1]==mid(2); passed()
m.touch(); m.pressed=1; m.playtime=20; assert settle(m)[-1]==mid(2); m.pressed=0; assert settle(m)[-1]==mid(3); passed()  # a touch, then its press
# The clock: the first second seen puts it anywhere in that second (its middle, with a poll's slack);
# the next second narrows it to within a poll after that change; it never runs a poll past its second;
# paused it stays, and on Play goes on from there until the next second; a seek starts it again.
m=karaoke(); m.show(); POLL=250
m.playtime=8; assert m.at_ms()==8000+(999+POLL)//2 and m.at_ms(1000)==8999+POLL
m.playtime=9; assert m.at_ms()==9000+POLL//2 and m.at_ms(275)==9400
m.word(PLAY_STATUS,3); assert m.at_ms(500)==9400; m.word(PLAY_STATUS,2); assert m.at_ms()==9400 and m.at_ms(100)==9500
m.playtime=10; assert m.at_ms(10)==10000+POLL//2; m.playtime=60; assert m.at_ms(10)==60000+(999+POLL)//2; passed()
# The playing line, timed by the line alone (these lyrics have no word stamps), lit LYRIC_LIT whole as it
# starts, in Inter Medium drawn once: "Two two" one LYRIC_OLED_PX row (pitch 30) from the text column,
# centred in its row. The other lines are Inter Regular in Text Secondary; the clip comes back.
m=karaoke(); m.show(8); m.at_ms(); m.at_ms(1000); m.playtime=9; m.at_ms(); m.at_ms(275); loop(m,ms=0)  # 9400
assert m.ink(1)==[('Two two',LX,(ROW-PITCH)//2+(PITCH-LPX)//2,LIT,LPX,(0,0,375,SLOT),'inter-medium')] and [x[1] for x in m.k_inks]==[LX],m.k_inks
assert {(x[3],x[6]) for x in m.ink(0)+m.ink(2)}=={(REST,'inter')} and m.clip==(0,0,375,SLOT); passed()
m.advance(600,clear=False); loop(m,ms=0); assert [(x[3],x[5]) for x in m.ink(1)]==[(LIT,(0,0,375,SLOT))]; passed()
# Its rows repaint as the lit width moves, and the last line's as the next one starts.
m.calls=[]; loop(m); assert not m.dirty(); m.playtime=12; m.calls=[]; loop(m); assert m.dirty()[:2]==[m.lyric_row(1),m.lyric_row(2)]; passed()
# The view's border paint fades its top and foot 32 rows (lyrics_fade), each a fill of the page's black from
# opaque at the edge to clear inside, at the edges of what its ancestors show of it.
m.word(m.slide+O['W_H'],178); m.word(m.win+O['W_H'],262); m.bands=[]; m.paint(m.lrc,gap=0)
fades=sorted((b[1],b[4]) for b in m.bands if b[3]==1 and b[2]==375)
want=sorted([(i,(255*(32-i)//32)<<24) for i in range(32)]+[(177-i,(255*(32-i)//32)<<24) for i in range(32)])
assert fades==want,fades[:4]; passed()
# A line too wide for a row wraps at its last space, at the same LYRIC_OLED_PX (here 14 px a character, so 23
# in the 327 px between the text column's margins, pitch 30), its row on the page as high as its rows plus
# LYRIC_GAP, its text centred in it; CJK breaks anywhere; past LYRIC_ROWS (4) rows the rest is cut.
m=karaoke([(10,'abcdefghi '*3+'abcdefghi'),(20,'字'*40),(30,'x'*60),(40,'One'),(50,'y'*150)]); m.show()
gap=ROW-PITCH; hs=[2*PITCH+gap,2*PITCH+gap,3*PITCH+gap,ROW,4*PITCH+gap]
assert [m.get(m.lyric_row(i)+O['W_H']) for i in range(5)]==hs
assert [m.get(m.lyric_row(i)+O['W_Y']) for i in range(5)]==[SLOT+sum(hs[:i]) for i in range(5)]
def whole(i):  # its row at the top; each row's draw
    m.word(m.lrc+O['SCROLL_Y'],m.get(m.lyric_row(i)+O['W_Y'])); return m.ink(i)
top=gap//2+(PITCH-LPX)//2  # the first row's y in its line's row
assert [(x[0],x[2],x[4],x[6]) for x in whole(0)]==[('abcdefghi abcdefghi',top,LPX,'inter'),('abcdefghi abcdefghi',top+PITCH,LPX,'inter')]
assert [(x[0],x[4],x[6]) for x in whole(1)]==[('字'*23,LPX,'inter'),('字'*17,LPX,'inter')] and [x[1] for x in m.k_inks]==[LX]*2
assert [(len(x[0]),x[2]) for x in whole(2)]==[(23,top),(23,top+PITCH),(14,top+2*PITCH)] and [x[2] for x in whole(3)]==[top]
assert [len(x[0]) for x in whole(4)]==[23]*4; m.word(m.lrc+O['SCROLL_Y'],0); passed()
# The playing line is centred, however many rows it has: line 2's 111 px centre on the page's.
m.playtime=30; settle(m); off=SLOT+hs[0]+hs[1]-(178-hs[2])//2; assert y(m)==off==mid(2,hs); passed()  # C's division
# A row the page's edge cuts is painted at full strength (lyrics_fade fades the view's edges); out of view, nothing.
def seen(i): top=SLOT+sum(hs[:i])-off; return max(0,min(top+hs[i],178)-max(top,0))
assert [seen(i) for i in range(5)]==[0,33,111,34,0]
assert [{x[3]>>24 for x in m.ink(i)} for i in range(5)]==[set(),{255},{255},{255},set()]; passed()
# Let go, the wheel leaves a row at the top, its lines all Text Secondary; Play/Pause held there moves nothing and opens
# the song menu, as anywhere on Now Playing.
SEEK='[00:05.00]One\n[00:09.70]'+'abcdefghi '*3+'abcdefghi\n[00:12.00]Three\n[00:20.00]Four\n'
def seek_page(status=2,tag=SEEK):
    m=karaoke([],tags={'LYRICS':tag}); m.load(); m.show(5,bar=True); m.word(PLAY_STATUS,status); settle(m); return m
def moves(m): return [(c[0],c[1]) for c in m.calls if c[0] in ('player_seek_time','mclSetSeek','mclSetResume')]
def long_play(m):  # Play/Pause held and let go: whether it was taken, Play/Pause's own presses after it, and the seeks
    m.t=getattr(m,'t',7000)+1; m.press(m.t); took=m.hold()==11; seen=moves(m); return took,m.release(),seen
def song_menu(m): return any(p[0]=='dialog/sortselect_dialog' for p in m.opened)
def tone(m,i): return {x[3]&0xffffff for x in m.ink(i)}
m=seek_page(); assert y(m)==0 and tone(m,1)=={REST&0xffffff}
m.calls=[]; loop(m,-28); loop(m,-28); m.word(payload_syms['wheel_held'],0); ys=settle(m)
assert ys[-1] in [0]+[m.get(m.lyric_row(i)+O['W_Y']) for i in range(4)] and tone(m,1)=={REST&0xffffff},ys
assert long_play(m)==(True,0,[]) and song_menu(m); passed()
# Unsynced lyrics, or the art page, keep the song menu.
m=seek_page(tag='plain\nwords\n'); assert long_play(m)==(True,0,[]) and song_menu(m); passed()
m=seek_page(); m.nodes[m.slide]['value']=0; loop(m); assert long_play(m)==(True,0,[]) and song_menu(m); passed()
# With no memory for its table the page keeps stock's rows, their text and layout, and paints nothing over them.
m=karaoke(); m.mock('calloc@GLIBC_2.0'); m.fail_calloc=True; m.show(9)
assert [m.nodes[r]['text'] for r in m.nodes[m.lrc]['children']]==['One','Two two','Three','Four','Five']
assert 'children_layout' not in m.nodes[m.lrc]
assert m.ink(1)==[] and loop(m)==0 and m.ink(1)==[]; passed()
# Now Playing opened again in the last page's scroll view (AWTK's memory reused): its rows become the
# page's the same way, no stock text left.
m=karaoke(); m.show(9); f,ctx=m.handler(m.win,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
m.show(9,lrc=m.lrc)
assert {m.nodes[r]['text'] for r in m.nodes[m.lrc]['children']}=={''} and [x[3] for x in m.ink(1)]; passed()
# A load's table: its milliseconds and word stamps light each word as it is sung, stamp to stamp ("Hello
# world" 154 px, "Hel" 42, "Hello " 84: CW a character), the last word at the sung pace (LYRIC_UNIT_MS a letter,
# LYRIC_MIN_MS at least); a line without word stamps lights whole; the page takes the table for the rows
# built from those lines, before them or after, and never for others: those get the labels' table.
tag='[00:12.50]<00:12.50>Hel<00:12.90>lo <00:13.40>world\n[00:15.00]Next\n'
CW=(LPX+1)//2  # a character's advance here: half LYRIC_OLED_PX, rounded (ly_layout)
def lit(m): return m.ink(0)[-1][5][2]-LX  # the lit draw's clip, from the text column
def at13125(m): m.at_ms(); m.at_ms(275); m.playtime=13; m.at_ms(); loop(m,ms=0)
m=karaoke([],tags={'LYRICS':tag}); m.load(); m.show(12); at13125(m); assert lit(m)==3*CW+3*CW*225//500
m.advance(675,clear=False); loop(m,ms=0); assert lit(m)==6*CW+5*CW*400//800; passed()  # 800 ms: "world" is 450
# Its row repaints as the lit width moves, at most every LYRIC_PAINT_MS (66 ms): 33 passes 8 ms apart, at
# the first pass past each, 3 times (it moved on most).
m.calls=[]; [loop(m) for _ in range(33)]; assert m.dirty().count(m.lyric_row(0))==3,m.dirty(); passed()
m.playtime=15; loop(m,ms=0); assert [(x[0],x[3],x[5]) for x in m.ink(1)]==[('Next',LIT,(0,0,375,SLOT))]; passed()
m=karaoke([(12,'Hello world'),(15,'Next')],tags={'LYRICS':tag}); m.show(12); m.load(); at13125(m); assert lit(m)==3*CW+3*CW*225//500; passed()
m=karaoke([],tags={'LYRICS':tag}); m.load(); m.lines=[(12,'Hello world'),(15,'Nest')]; m.show(12); at13125(m)
assert [x[3] for x in m.ink(0)]==[LIT]; passed()  # from 12 s, 900 ms: all sung
# Spaces trimmed from a line's ends take its word stamps with them: "Hi " is 3 CW, "Hi there" 8, its last
# stamp at its end.
m=karaoke([],tags={'LYRICS':'[00:13.00] <00:13.00>Hi <00:13.50>there <00:14.00>\n[00:15.00]Next\n'}); m.load(); m.show(12)
at13125(m); assert lit(m)==3*CW*125//500; m.advance(625,clear=False); loop(m,ms=0); assert lit(m)==3*CW+5*CW*250//500; passed()
# Full-screen lyrics (np_lyric_touch, np_full): a tap on the lyrics page, a touch that lifts within TAP_PX (10) of
# where it came down, puts the lyrics full screen: stock's column (slide_view_view, under the 40 px top row and over
# the bar) goes over the page's other widgets, black, the whole window; its slide_view down to NP_FULL_FOOT (20)
# above the window's foot, the dots there and view_lrc from the top to NP_FULL_FOOT above them, both by their
# self_layout, back on the playing line, centred in the taller view. A tap, Return, the slide_view taking a swipe
# (its abort), another page or another window once it is open bring the column back, in its place before the
# progress bar; not the full-screen art Enlarged Album Art opened, closing this pass. A swipe or a drag is no tap,
# no lyrics nothing to show.
class FullMachine(KaraokeMachine):
    def __init__(self):
        super().__init__(); self.restack=True; self.playing=None  # the playing line's index (full_page sets it)
        for n in ('widget_layout','widget_index_of'): self.handlers[syms[n]]='c:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('c:'): return super().hook(u,address,size,unused)
        a=u.reg_read(REGS[0]); ret=0
        if name=='c:widget_layout':  # the column's: the lyrics take its slide's size, 20 px short (tools/ipod.py),
            assert a==self.col       # and the scroll view's relayout clamps their offset (to the top here)
            self.word(self.lrc+O['W_W'],self.get(self.slide+O['W_W'])); self.word(self.lrc+O['W_H'],self.get(self.slide+O['W_H'])-20)
            self.word(self.lrc+O['SCROLL_Y'],0)
        else: ret=self.nodes[self.win]['children'].index(a)
        self.returns(ret,clobber=True)
    def show(self,playtime=0,lrc=None,slide='slide_view'):  # slide: the slide_view's name (another: a page without one); stock's page: the title, the column (its slide_view and dots), the bar
        self.playtime=playtime; self.lrc=self.node('scroll_view','scroll_lrc'); self.build()
        self.view=self.node('list_view','view_lrc',[self.lrc]); self.lyric=self.node('view','view_lyric',[self.view]); self.word(self.lrc+O['W_PARENT'],self.view)
        self.slide=self.node('slide_view',slide,[self.lyric],value=1)
        self.dots=self.node('slide_indicator','slide_indicator1'); self.word(self.dots+O['W_Y'],182)
        self.col=self.node('view','slide_view_view',[self.slide,self.dots]); self.word(self.slide+O['W_PARENT'],self.col)
        self.bar=self.node('slider','slider_play')
        self.win=self.top=self.node('window','playing_page',[self.node('hscroll_label','scrlabel_title'),self.col,self.bar])
        for w,box in ((self.win,(0,30,375,290)),(self.col,(0,40,375,192)),(self.slide,(0,0,375,186)),(self.view,(0,0,375,178)),(self.lrc,(0,0,375,178))):
            for k,v in zip(('W_X','W_Y','W_W','W_H'),box): self.word(w+O[k],v)
        self.word(self.win+O['W_PARENT'],self.wm); self.stack=[self.win]
        assert self.call(address=playing,args=(self.win,7,0,0),gap=0)==0
        self.handlers[sleep_hook+12]='stock_sleep'; self.word(payload_syms['wheel_held'],1)
    def touch(self,kind,x=150,y=100):  # a pointer event on the lyrics page: its handler's answer
        f,ctx=self.handler(self.lyric,O[kind]); ev=self.alloc(0x20)
        for k,v in (('EVENT_TYPE',O[kind]),('EVENT_X',x),('EVENT_Y',y)): self.word(ev+O[k],v)
        r=self.call(address=f,args=(ctx,ev,0,0),gap=0,clear=False)
        assert r==0,(kind,r)  # never STOP: the slide_view's grab, and its swipe from the first move
        return r
    def line_y(self,i):  # the screen's y of line i's row's middle, where it shows (its view's scroll counted)
        r=self.lyric_row(i); y=self.get(r+O['W_Y'])+self.get(r+O['W_H'])//2-signed(self.get(self.lrc+O['SCROLL_Y']))
        w=self.lrc
        while w and w!=self.wm: y+=signed(self.get(w+O['W_Y'])); w=self.get(w+O['W_PARENT'])
        return y
    def playing_y(self):  # on the playing line (np_lyric_touch: a tap there takes them full screen, any other seeks)
        return self.line_y(self.playing) if self.nodes[self.lrc]['children'] and self.playing is not None else 100
    def tap(self,x=150,y=None,dx=0,dy=0):  # a finger down and up, a UI loop pass with each: no drag of the lines
        if y is None: y=self.playing_y()
        self.touch('EVT_POINTER_DOWN',x,y); self.pressed=1; loop(self)
        self.touch('EVT_POINTER_UP_BEFORE',x+dx,y+dy); self.pressed=0; loop(self)
    def full(self):  # the column's place, x and width, its slide_view's size and background, the rows' width
        return (self.nodes[self.win]['children'].index(self.col),self.get(self.col+O['W_X']),self.get(self.col+O['W_W']),
                self.get(self.slide+O['W_W']),self.get(self.slide+O['W_H']),self.nodes[self.col].get('style:normal:bg_color'),
                {self.get(r+O['W_W']) for r in self.nodes[self.lrc]['children']})
    def where(self):  # full() but the rows, the column's y and height, the dots' and view_lrc's self_layouts
        return (self.full()[:6],[self.get(self.col+O[k]) for k in ('W_Y','W_H')],self.nodes[self.dots].get('self_layout'),self.nodes[self.view].get('self_layout'))
COLUMN,FULL=(1,0,375,375,186,'#00000000',{375}),(2,0,375,375,290-20,'#000000',{375})  # FULL: the window's last child, its foot less NP_FULL_FOOT
PLACED=((1,0,375,375,186,'#00000000'),[40,192],('0','182','100%','10'),('0','0','100%','178'))  # back: the column's place and self_layouts
def full_page(lines=((5,'x'*15),(9,'Two two'),(12,'Three')),playtime=9):
    m=FullMachine(); m.lines=list(lines)
    timed=[i for i,(t,_) in enumerate(m.lines) if t!=FAR and t<=playtime]
    m.playing=timed[-1] if timed else (0 if m.lines else None); m.show(playtime); return m
def centre_of(m,i,view):  # the offset that centres line i's row in a view that high (C's division)
    r=m.lyric_row(i); return m.get(r+O['W_Y'])+int((m.get(r+O['W_H'])-view)/2)
m=full_page(); assert m.full()[1:3]==(0,375)
assert settle(m)[-1]==centre_of(m,1,178); loop(m,-56); m.word(payload_syms['wheel_held'],0); held=settle(m)[-1]  # the wheel holds them
m.tap(dx=8,dy=-8); assert m.full()==FULL,m.full()
assert m.where()==((2,0,375,375,290-20,'#000000'),[0,290],('0','270','100%','10'),('0','0','100%','-20')),m.where()
assert settle(m)[-1]==centre_of(m,1,290-20-20)!=held; passed()  # the playing line, in the taller view
m.tap(); assert m.full()==COLUMN and m.where()==PLACED and settle(m)[-1]==centre_of(m,1,186-20); passed()  # another tap: back
for end in ('return','abort','page'):
    m=full_page(); m.tap(); assert m.full()==FULL,end
    if end=='return': assert m.call(O['KEY_RETURN'],gap=50)==11  # Return stays on the page
    elif end=='abort': m.touch('EVT_POINTER_DOWN'); m.pressed=1; m.touch('EVT_POINTER_ABORT',175,100); m.pressed=0  # the slide_view's, past the box
    else: m.nodes[m.slide]['value']=2; loop(m)
    assert m.full()==COLUMN and m.where()==PLACED,end; passed()
# Another window over the page leaves them full screen; a swipe the slide_view took (its abort)
# that comes back to where it began is no tap at the lift.
m=full_page(); m.tap(); m.top=m.node('window','home_page'); loop(m); m.top=m.win; loop(m); assert m.full()==FULL
m.touch('EVT_POINTER_DOWN'); m.pressed=1; m.touch('EVT_POINTER_ABORT',220,100)
m.touch('EVT_POINTER_UP_BEFORE'); m.pressed=0; assert m.full()==COLUMN; passed()
for dx,dy in ((70,0),(0,40),(41,0),(0,11)):  # a swipe, a drag of the lines, past TAP_PX: the column untouched
    m=full_page(); m.tap(dx=dx,dy=dy); assert m.full()[:6]==COLUMN[:5]+(None,),(dx,dy,m.full()); passed()
m=full_page(lines=()); m.tap(); assert m.full()[:6]==COLUMN[:5]+(None,),m.full(); passed()  # no lyrics
# The lyrics take np_lyric_touch.
m=full_page(); want=[(O[k],payload_syms['np_lyric_touch'],0) for k in ('EVT_POINTER_DOWN','EVT_POINTER_UP_BEFORE','EVT_POINTER_ABORT')]
assert m.nodes[m.lyric].get('handlers',[])==want,m.nodes[m.lyric].get('handlers'); passed()
# A tap goes full screen however long the finger stayed down, and another, wherever it lands, brings the column back
# with the song where it was. A touch the slide_view or scroll_lrc took (its abort) or a lift out of the box is no
# tap, and one they took in full screen ends it.
def tap_line(m,y=None,dy=0,abort=False,ms=0,dx=0):
    if y is None: y=m.playing_y()
    m.calls=[]; m.touch('EVT_POINTER_DOWN',150,y); m.pressed=1; loop(m)
    if abort: m.touch('EVT_POINTER_ABORT',150+dx,y+dy)  # where the finger is
    if ms: m.advance(ms,clear=False)
    m.touch('EVT_POINTER_UP_BEFORE',150+dx,y+dy); m.pressed=0; loop(m); return moves(m)
def full_on(m): return m.full()[5]=='#000000'
m=full_page(); settle(m)
for kw in ({'dx':11,'abort':True},{'dy':10,'abort':True},{'dy':40},{'dx':-25}):
    assert tap_line(m,**kw)==[] and not full_on(m),kw
assert tap_line(m,ms=500)==[] and full_on(m); settle(m); passed()  # a long press is a tap
assert tap_line(m)==[] and not full_on(m); passed()  # on a line: the column back, no seek
m.tap(); settle(m); assert full_on(m)
assert tap_line(m,dy=10,abort=True)==[] and not full_on(m); passed()  # the lines' drag ends it
m.tap(); settle(m); assert full_on(m)
assert tap_line(m,dx=11,abort=True)==[] and not full_on(m); passed()  # the slide_view's swipe ends it
# Unsynced lines: full screen and back as any lyrics.
m=full_page(lines=((FAR,'x'*15),(FAR,'Two two'),(FAR,'Three'))); settle(m); m.tap(); settle(m); assert full_on(m)
assert tap_line(m)==[] and not full_on(m); passed()
# A tap on a timed line other than the playing one (np_lyric_line) seeks to its start, in either layout, and the
# view goes to it; a drag of it seeks nowhere; the playing line, off the lines and a stopped player toggle full screen.
m=full_page(); settle(m)
assert tap_line(m,y=m.line_y(2))==[('player_seek_time',12)] and not full_on(m)
assert m.get(m.lrc+O['SCROLL_Y'])!=centre_of(m,2,178) and settle(m)[-1]==centre_of(m,2,178); passed()  # before the player's 12 s
m=full_page(); settle(m)
assert tap_line(m,y=m.line_y(2),dy=10,abort=True)==[] and tap_line(m,y=m.line_y(2),dy=40)==[] and not full_on(m); passed()
m=full_page(); settle(m); m.tap(); settle(m); assert full_on(m)
assert tap_line(m,y=m.line_y(0))==[('player_seek_time',5)] and full_on(m); passed()  # full screen: seeks and stays
m=full_page(); settle(m); m.word(PLAY_STATUS,1)
assert tap_line(m,y=m.line_y(2))==[] and full_on(m); passed()  # stopped: no seek, full screen as before
# A page without stock's slide_view (a layout of its own) has no column: a tap on its lyrics leaves
# everything as it was, where np_full read the column off a NULL slide_view (W_PARENT at 0x48).
m=FullMachine(); m.lines=[(5,'x'*15),(9,'Two two'),(12,'Three')]; m.playing=1; m.show(9,slide='pages')
m.tap(); assert m.full()[:6]==COLUMN[:5]+(None,),m.full()
m.tap(); assert m.full()[:6]==COLUMN[:5]+(None,),m.full(); passed()
# Get Album Lyrics (onlinematch.c ringnav_playermore): after stock's 8 ⋯ rows (its init mocked), one
# more in stock's row widgets and 20 px label, under Download Cover & Lyrics (PLAYMORE_ROWS' kind 4), its
# button unnamed so stock's row click (an atoi of the name) never sees it; only for a playing song on the
# card with an album. A press asks for Wi-Fi first, copies the album's tracks off stock's records (a CUE
# track left out) and starts the worker; the row shows its progress, a press while it runs cancels, the
# page's end leaves it running, and the timer joins it and toasts the summary. USB storage stops it quietly.
# The job's own steps are test/peq.py's album_check. AirPlay's row (airplay.c) goes over stock's, and About's
# (np_about.c) under them all, for any song.
assert [m.get(O['PLAYMORE_ROWS']+4*i) for i in range(O['PLAYMORE_COUNT'])]==[2,3,4,5,6,7,0,1]  # Download Cover & Lyrics third
class AlbumLyricsMachine(CoverflowMachine):
    def __init__(self,**queue):
        super().__init__(**queue); self.wifi=3; self.restack=True; self.accessed=[]
        self.handlers[int(manifest['patch_symbols']['stock_playermore_trampoline'],16)]='stock_playermore'
        self.handlers[syms['get_wifisignal']]='al:get_wifisignal'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='c:access@GLIBC_2.0': self.accessed.append(self.text(u.reg_read(REGS[0])))
        if name!='al:get_wifisignal': return super().hook(u,address,size,unused)
        self.returns(self.wifi,clobber=True)
    def playing(self): return self.items(self.get(syms['mcl_pdeqplaylist']))[0]
    def more(self,rows=8):
        self.stock=[self.node('list_item') for _ in range(rows)]
        view=self.node('scroll_view','scroll_view_more',list(self.stock)); self.top=self.node('window','playermore_page',[view])
        assert self.call(address=HOOKS['playermore_page_init'][0],args=(self.top,0,0,0),gap=0)==0
        assert self.calls[0][:2]==('stock_playermore',self.top)
        return self.nodes[view]['children']
m=AlbumLyricsMachine(); kids=m.more(); row=kids[4]
assert len(kids)==11 and [k for k in kids if k not in (row,kids[0],kids[-1])]==m.stock and m.nodes[row]['style']=='s_listitem_black'
air=m.nodes[m.nodes[kids[0]]['children'][0]]['children']
assert m.nodes[air[0]]['image']=='airplay' and m.nodes[air[1]]['text']=='AirPlay'
about=m.nodes[m.nodes[kids[-1]]['children'][0]]['children']
assert m.nodes[about[0]]['image']=='system_about' and m.nodes[about[1]]['text']=='About'
button=m.nodes[row]['children'][0]; icon,label=m.nodes[button]['children']
assert m.nodes[button]['style']=='s_btn_listitem' and not m.nodes[button].get('name')
assert [m.get(button+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[20,0,335,70]
assert m.nodes[icon]['image']=='player_onlinesearch' and [m.get(icon+O[k]) for k in ('W_X','W_W','W_H')]==[10,52,70]
assert m.nodes[label]['style']=='s_scrlabel_white24l' and m.nodes[label]['text']=='Get Album Lyrics'
assert [m.get(label+O[k]) for k in ('W_X','W_W','W_H')]==[72,253,70]; passed()
m=AlbumLyricsMachine(cls=0xf1); assert len(m.more())==10; passed()  # an online queue: AirPlay's and About's rows alone
for off,v in (('REC_TYPE',4),('REC_ALBUM',0),('REC_PATH',0)):  # a folder, no album tag, no path
    m=AlbumLyricsMachine(); m.word(m.playing()+O[off],v); assert len(m.more())==10,off; passed()
m=AlbumLyricsMachine(); m.word(m.playing()+O['REC_PATH'],m.string('http://x/y')); assert len(m.more())==10; passed()
m=AlbumLyricsMachine(); assert len(m.more(7))==7; passed()  # not stock's rows: left as they are
def album_lyrics_page(**queue):
    m=AlbumLyricsMachine(**queue); button=m.nodes[m.more()[4]]['children'][0]; label=m.nodes[button]['children'][1]
    cue=m.song('Cue'); m.word(cue+O['REC_CUE_END'],90)
    m.found=[m.song('T1'),cue,m.song('T2')]
    def press():
        f,ctx=m.handler(button,O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
    return m,label,press
m,label,press=album_lyrics_page()
m.wifi=-1; press(); assert m.toasts[-1][3]=='Connect to Wi-Fi first' and not m.threads; m.wifi=3
press(); assert len(m.threads)==1 and m.nodes[label]['text']=='Downloading 1 of 2'
worker,arg=m.threads[-1]; press(); assert len(m.threads)==1  # a second press: cancelling, no second job
assert m.nodes[label]['text']=='Canceling…'; passed()
m,label,press=album_lyrics_page(); press(); worker,arg=m.threads[-1]
m.advance(500,clear=False); assert ('reset_poweroptions_timer',1,1,0) in [c[:4] for c in m.calls] and m.nodes[label]['text']=='Downloading 1 of 2'
f,ctx=m.handler(label,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)  # the page closes: the job goes on
m.missing=False; assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=5_000_000)==0
assert m.accessed==['/p/T1','/p/T1.lrc','/p/T2','/p/T2.lrc'],m.accessed  # each song's own .lrc found: none fetched
n=len(m.toasts); m.advance(500,clear=False)
assert len(m.toasts)==n+1 and m.toasts[-1][3]=='Album lyrics: 2 already had lyrics' and m.joins==[77]; passed()
press_count=len(m.threads); m.advance(1000); assert len(m.threads)==press_count and len(m.toasts)==n+1; passed()  # the timer has ended
m,label,press=album_lyrics_page(); press(); worker,arg=m.threads[-1]; press()  # cancelled before its first song
assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=5_000_000)==0 and not m.accessed
m.advance(500,clear=False); assert m.toasts[-1][3]=='Canceled' and m.nodes[label]['text']=='Get Album Lyrics'; passed()
m,label,press=album_lyrics_page(); press(); worker,arg=m.threads[-1]; n=len(m.toasts)
assert m.call(address=payload_syms['album_lyrics_stop'],args=(0,0,0,0),gap=0)==0 and m.joins==[77]  # USB storage: joined first
m.advance(500,clear=False); assert len(m.toasts)==n and m.nodes[label]['text']=='Get Album Lyrics'; passed()
m.found=[]; press(); assert m.toasts[-1][3]=='No album songs on the card' and len(m.threads)==1; passed()

# Sparse populated initials: one destination per sustained tick, both directions, utilities
# and wrap edges. Album and Artist share article handling; nonalphabetical sorts stay single-step.
def cf_jump(m,at,direction):
    letter=m.alloc(4)
    steps=m.call(address=payload_syms['coverflow_jump'],args=(m.slide,at,direction,letter),gap=0)
    return steps,m.get(letter)
m=CoverflowMachine(albums=8)
for r,name in zip(m.albums,['Apple One','The Apple','Dawn','Dusk','Zulu','9 Lives','-Noise','Été']):
    m.word(r+O['REC_ALBUM'],m.string(name))
m.open()
assert cf_jump(m,0,1)==(2,ord('D')) and cf_jump(m,3,-1)==(3,ord('A'))
assert cf_jump(m,2,-1)==(2,ord('A')) and cf_jump(m,4,1)==(1,ord('#'))
assert cf_jump(m,5,1)==(2,ord('É')) and cf_jump(m,7,1)==(1,0)
assert cf_jump(m,0,-1)==(1,0) and cf_jump(m,1,-1)==(2,0)
assert cf_jump(m,8,1)==(1,0) and cf_jump(m,9,1)==(1,0); passed()
# Unicode punctuation/numbers and missing names share #; letters retain their codepoints.
for name,want in (('١ Song',ord('#')),('० Song',ord('#')),('-Song',ord('#')),('',ord('#')),
                  ('Жизнь',ord('Ж')),('東京',ord('東')),('Ａ Song',ord('Ａ')),('Ｂ Song',ord('Ｂ')),
                  ('Ⰰ Song',ord('Ⰰ'))):
    m=CoverflowMachine(albums=2)
    m.word(m.albums[1]+O['REC_ALBUM'],m.string(name)); m.open()
    assert cf_jump(m,0,1)==(1,want); passed()
m=CoverflowMachine(albums=2); m.word(m.albums[1]+O['REC_ALBUM'],m.string('Unknown Album'))
m.word(m.albums[1]+O['REC_ID'],0xffffffff); m.open(); assert cf_jump(m,0,1)==(1,ord('#')); passed()
# Cover Flow's wheel step is its own kind, coverflow_step and coverflow_step_fast; OLED declares none, so they are
# HOME_SLIDE_MS and HOME_FAST_SLIDE_MS with SLIDE_EASING, as the carousel's.
m=CoverflowMachine(albums=3); m.open(); step_ticks(m,m.slide,(0,O['HOME_SLIDE_MS'],O['SLIDE_EASING']),(50,O['HOME_FAST_SLIDE_MS'],O['SLIDE_EASING']))
set_motion(m,'coverflow_step',250,2); set_motion(m,'coverflow_step_fast',170,4); set_motion(m,'step',90,1)
step_ticks(m,m.slide,(500,250,2),(50,170,4)); passed()
# A step whose easing is follow (EASE_FOLLOW, theme.xml easing="follow"; 81 ms here) is a follow of the goal, its ms
# the time constant. The easing is what stock's animator calls
# (widget_animator_time_elapse: k in $f12, the share back in $f0). A tick moves the goal, carries the speed, and
# the run still ends through home_done on ANIM_X_TO.
def follow_share(m,k):
    m.u.reg_write(UC_MIPS_REG_F12,struct.unpack('<I',struct.pack('<f',k))[0])
    m.call(address=payload_syms['coverflow_follow'],args=(0,0,0,0),gap=0,clear=False)
    return struct.unpack('<f',struct.pack('<I',m.u.reg_read(UC_MIPS_REG_F0)&0xffffffff))[0]
def follow_to(m,w,ms):  # the animator ms into its run, the offset as stock's update writes it: whole px, truncated
    a,origin,goal,T=slide(m,w)
    m.word(a+O['ANIM_ELAPSED'],ms); m.word(w+O['SLIDE_OFFSET'],int(origin+(goal-origin)*follow_share(m,ms/T)))
    return signed(m.get(w+O['SLIDE_OFFSET']))
def follow_speed(m,w,ms):  # px a ms, at ms into the run
    a,origin,goal,T=slide(m,w)
    return (goal-origin)*(follow_share(m,(ms+1)/T)-follow_share(m,ms/T))
def follow_page():  # Cover Flow open, its steps the follow
    m=CoverflowMachine(albums=9); m.open()
    for kind in ('coverflow_step','coverflow_step_fast'): set_motion(m,kind,81,O['EASE_FOLLOW'])
    m.slide_easing=0; return m  # the follow's animator is made linear (0), its easing set after
m=follow_page(); w=m.slide; index=m.get(w+O['SLIDE_INDEX'])
assert m.call(gap=1000)==11
a,origin,goal,T=slide(m,w); stride=-goal
assert origin==0 and stride>0 and m.get(a+O['ANIM_EASING'])==payload_syms['coverflow_follow']
assert 6*81<=T<=8*81  # within a px of the goal: about 7 time constants
for ms,want in ((81,.2642),(162,.5940),(243,.8009),(405,.9596)): assert abs(follow_share(m,ms/T)-want)<.002
assert follow_share(m,1.0)==1.0 and follow_share(m,0.0)==0.0
at=follow_to(m,w,100); speed=follow_speed(m,w,100)
assert m.call(gap=0)==11  # a tick mid-step: the goal moves one album, from the live offset, the speed goes on
a2,origin,goal,T2=slide(m,w)
assert a2==a and origin==at and goal==-2*stride and T2>T*.9 and m.get(a+O['ANIM_ELAPSED'])==0
assert abs(follow_speed(m,w,0)-speed)<.05 and signed(m.get(a+O['ANIM_X_TO']))==-2*stride
m.advance(T2); assert not m.slides and m.get(w+O['SLIDE_ANIMATOR'])==0 and m.get(w+O['SLIDE_OFFSET'])==0
assert m.get(w+O['SLIDE_INDEX'])==(index+2)%9; passed()
# A tick on a touch's glide starts the follow from rest: that speed isn't the follow's to read. The glide itself
# keeps its own easing; a ms of 0 stays the immediate move.
m=follow_page(); w=m.slide
cubic=m.call(address=syms['easing_get'],args=(O['EASE_CUBIC_OUT'],0,0,0),gap=0)
assert m.call(gap=1000)==11; a=m.get(w+O['SLIDE_ANIMATOR'])
assert m.call(address=payload_syms['carousel_glide'],args=(w,0,300,O['EASE_CUBIC_OUT']),gap=0)==1 and m.get(a+O['ANIM_EASING'])==cubic and m.get(a+O['ANIM_DURATION'])==300
m.word(a+O['ANIM_ELAPSED'],100); m.word(w+O['SLIDE_OFFSET'],-40)
assert m.call(gap=0)==11 and m.get(a+O['ANIM_EASING'])==payload_syms['coverflow_follow'] and abs(follow_speed(m,w,0))<.02; passed()
# Coverflow owns both halves of a wheel tick: the stock press filter runs, then stops before
# native focus navigation can move the Artists list underneath; release still moves the covers.
m=CoverflowMachine(albums=3)
for page,want in (('localclass_page',0),('coverflow_page',11)):
    if page=='coverflow_page': m.open()
    else: m.top=m.node('window',page)
    for key in (O['KEY_PREV'],O['KEY_NEXT'],O['KEY_CENTER']):
        ret=m.call(key,address=HOOKS['on_wm_keydown_before_fun'][0],
                   event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)
        assert ret==(want if key!=O['KEY_CENTER'] else 0)
        assert any(c[0]=='reset_poweroptions_timer' for c in m.calls)
assert m.call()==11; m.advance(300); assert m.get(m.slide+O['SLIDE_INDEX'])==1; passed()

# Native delivery also reaches the old focused widget after the before-filter. Stop every
# wheel-key phase at the covered page/child, before its handler or native vtable can navigate.
m=CoverflowMachine(); under=m.node('window','artistinfo_page'); row=m.node('list_item')
m.word(row+O['W_PARENT'],under); m.word(under+O['W_PARENT'],m.wm); m.open()
m.word(m.page+O['W_PARENT'],m.wm)
for typ in (O['EVT_KEY_DOWN'],0x111,O['EVT_KEY_DOWN_BEFORE'],O['EVT_KEY_UP'],O['EVT_KEY_UP_BEFORE']):
    for target in (under,row):
        for key in (O['KEY_PREV'],O['KEY_NEXT']):
            assert m.call(key,address=HOOKS['widget_dispatch'][0],args=(target,m.event,0,0),event_type=typ,gap=0)==11
            assert not m.dispatched()
for target in (m.page,m.slide,m.wm):
    assert m.call(address=HOOKS['widget_dispatch'][0],args=(target,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0)==0
    assert m.dispatched()
for key in (O['KEY_CENTER'],O['KEY_RETURN']):
    assert m.call(key,address=HOOKS['widget_dispatch'][0],args=(row,m.event,0,0),event_type=O['EVT_KEY_UP'],gap=0)==0
    assert m.dispatched()
# Execute stock's dispatch-then-vtable keydown helper (0x672bf8), with the native navigation
# callback replaced by a recorded service; STOP must prevent it from running at all.
m.handlers[0x67af24]='reset_poweroptions_timer'
assert m.call(address=0x672bf8,args=(row,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0)==11
assert not m.dispatched() and not any(c[0]=='reset_poweroptions_timer' for c in m.calls)
m.top=under
assert m.call(address=0x672bf8,args=(row,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0)==0
assert m.dispatched() and any(c[0]=='reset_poweroptions_timer' for c in m.calls)
# The artist page on top: the Artists list under it gets no wheel key either.
artists=m.node('window','localclass_page'); m.word(artists+O['W_PARENT'],m.wm)
low=m.node('list_item'); m.word(low+O['W_PARENT'],artists)
for key in (O['KEY_PREV'],O['KEY_NEXT']):
    assert m.call(key,address=HOOKS['widget_dispatch'][0],args=(low,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0)==11
    assert not m.dispatched()
passed()

# Ticks (the covers' wheel when the thumb isn't on, and Home's carousel): the lists' momentum (wheel_accel.c), rows only.
# 50 ms ticks move an album a tick for WA_FIRST_MS (300), then one more each WA_RAMP_MS (200): 14 ticks (runs 0 to 650 ms)
# are 6 x 1 + 4 x 2 + 4 x 3 = 26 albums, the 15th (700 ms) 4 more; never a letter's jump and no letter over the covers.
m=CoverflowMachine(albums=19)
for r,name in zip(m.albums,['A1','A2','A3','D1','D2','D3']+[f'G{i}' for i in range(1,12)]+['Z1','Z2']): m.word(r+O['REC_ALBUM'],m.string(name))
m.open()
stride=m.call(address=syms['slide_menu_item_width'],args=(m.slide,0,0,0),gap=0)+m.get(m.slide+O['SLIDE_SPACER'])
for _ in range(14): assert m.call(gap=50)==11
assert m.get(m.slide+O['SLIDE_INDEX'])==0 and slide(m,m.slide)[2]==-26*stride
assert m.call(gap=50)==11
assert slide(m,m.slide)[2]==-30*stride
m.paint(m.slide,gap=0); assert not any(len(t['text'])==1 for t in m.letters)
m.advance(400); assert m.get(m.slide+O['SLIDE_INDEX'])==30%19
assert m.call(O['KEY_PREV'],gap=50)==11  # reversal starts over: one album back
m.advance(200); assert m.get(m.slide+O['SLIDE_INDEX'])==30%19-1; passed()
# A reversal while moving reuses the animator and returns to the adjacent live cover.
m=CoverflowMachine(albums=10); m.open(); m.call(gap=0); m.call(gap=20)
a=slide(m,m.slide)[0]; assert m.call(O['KEY_PREV'],gap=20)==11 and slide(m,m.slide)[0]==a
m.advance(300); assert m.get(m.slide+O['SLIDE_INDEX'])==0; passed()
# Single stops through the wrap and back, even with a sustained run: the library has no cards after
# its albums (its Sort and Refresh are Display settings' rows).
m=CoverflowMachine(albums=3); m.open()
for expected in (1,2,0):
    assert m.call(gap=50)==11; m.advance(200)
    assert m.get(m.slide+O['SLIDE_INDEX'])==expected
for expected in (2,1,0):
    assert m.call(O['KEY_PREV'],gap=50)==11; m.advance(200)
    assert m.get(m.slide+O['SLIDE_INDEX'])==expected
passed()


# The Home card is index 2 of seven; its click opens coverflow_page with every album as a cover
# and nothing after them, the wheel steps the stock slide_menu and centre confirms the cover.
m=CoverflowMachine(); page=m.open()
assert m.nodes[page]['name']=='coverflow_page' and m.slide and not m.threads
covers=m.nodes[m.slide]['children']; assert len(covers)==3
assert all(m.nodes[c]['image'].startswith('file:///mnt/mmc/.coverflow/') for c in covers)
assert 'Album 0' in m.texts(); passed()
m.press(3); assert m.hold()==11 and m.nodes[m.title]['text']=='Album 0'
f,ctx=m.handler(m.dlg_return,O['EVT_CLICK']); m.call(O['KEY_RETURN'],address=f,args=(ctx,m.event,0,0),gap=0)
assert m.top==page and m.release()==0; passed()
# Covers step like Home: fast ticks retarget one animator, never stock next/previous, whose
# scroll_to orphans the running animator.
assert m.call(gap=0)==11; a,_,to,dur=slide(m,m.slide); assert to<0 and dur==200
assert m.call(gap=20)==11 and slide(m,m.slide)[0]==a and slide(m,m.slide)[2:]==(2*to,120)
m.advance(300); assert m.get(m.slide+O['SLIDE_INDEX'])==2 and not m.slides and not m.get(m.slide+O['SLIDE_OFFSET'])
assert m.call(O['KEY_PREV'],gap=20)==11 and slide(m,m.slide)[2]==-to; m.advance(300)
assert m.get(m.slide+O['SLIDE_INDEX'])==1 and not m.slides
assert not any(c[0].startswith('slide_menu_scroll_to_') for c in m.calls)
m.clicks=[]; m.word(m.slide+O['SLIDE_INDEX'],1); assert m.confirm()==11 and m.clicks==[covers[1]]; passed()
m.clicks=[]; assert Machine.release(m)==11 and Machine.release(m,100)==0 and m.screens==[0] and not m.clicks; passed()
# The cover's click queries its tracks (staging restored) and lists them; a track hands playing_page
# our deque with its index as an album queue (class 0xff10, mode 2) and the album for stock resume;
# a missing file refuses.
m.byte(syms['g_backlight_status'],1); f,ctx=m.handler(covers[1],O['EVT_CLICK'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0; m.advance(0)
assert m.query[:2]==('getMusicByAlbum','Album 1') and m.names(m.get(syms['tools_pdeq_directory']))==['staged']
rows=[w for w in m.nodes if m.nodes[w]['type']=='list_item' and m.alive(w)]; assert len(rows)==2
assert not m.nodes[m.get(m.slide+O['W_PARENT'])]['visible']
f,ctx=m.handler(rows[1],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
assert len(m.plays)==1 and m.plays[0][0]=='playing_page' and m.plays[0][2:]==(1,0xff10,2)
assert m.text(syms['g_memory_info']+0x611)=='Album 1' and m.u.mem_read(syms['g_memory_info']+0x60d,1)==b'\0'
dq=m.plays[-1][1]&0xffffffff; assert m.names(dq)==['T1','T2']; passed()
m.missing=True; m.call(address=f,args=(ctx,m.event,0,0),gap=0); assert len(m.plays)==1 and 'Storage unavailable' in m.texts(); passed()
# Return: tracks -> covers on the same album, covers -> Home.
assert m.key()==11 and m.nodes[m.get(m.slide+O['W_PARENT'])]['visible'] and m.get(m.slide+O['SLIDE_INDEX'])==1 and not m.homes
assert m.key(O['KEY_NEXT'])==0 and m.key()==11 and m.homes==1; passed()
# The album list outlives the page: a reopen queries nothing until one of the songtable writers runs.
m=CoverflowMachine(); m.open(); m.close(); m.calls=[]
m.open(); assert not m.queried() and len(m.nodes[m.slide]['children'])==3; m.close(); passed()
for writer in WRITERS:
    m.rescan(writer); m.calls=[]; m.open(); assert m.queried()==1; m.close()
    m.calls=[]; m.open(); assert not m.queried(); m.close(); passed()
m.room=False; m.rescan('scanAllMusicFile'); m.room=True; passed()  # under 8 MB free: the three larger indexes wait
# Update Local Music while the dialog's count reads songtable on its own connection: stock's
# scanAllMusicFile and toolsInitDatabase run on a real SQLite file. The scan's thread waits for the
# count, so the drop commits and the scan runs. Back ends the wait and the scan: no drop is tried.
class ScanLockMachine(UiLoad,CoverflowMachine):
    def __init__(self,path):
        super().__init__(); self.mock('strstr@GLIBC_2.0')
        self.db=sqlite3.connect(path,isolation_level=None,timeout=0)  # stock sets no busy handler
        self.handlers.pop(HOOKS['scanAllMusicFile'][0]+12); self.handlers.pop(syms['toolsGetUserStop'])  # stock's own code
        for n in ('sqlite3_open','sqlite3_exec','sqlite3_close','sqlite3_free','sprintf@GLIBC_2.0','toolsClearDbCount','toolsLoadAllFile','usleep@GLIBC_2.0'):
            self.handlers[syms[n]]='lock:'+n
        self.sqls=[]
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('lock:'): return super().hook(u,address,size,unused)
        a,b,c,d=[u.reg_read(r) for r in REGS]; name=name[5:].split('@')[0]; ret=0
        self.calls.append((name,a,b,c))
        if name=='sqlite3_open': assert self.text(a)=='/mnt/data/database.db'; self.word(b,123)
        elif name=='sprintf': v=iter((c,d)); u.mem_write(a,re.sub('%s',lambda _:self.text(next(v)),self.text(b)).encode()+b'\0')
        elif name=='usleep':  # the count ends meanwhile: its read lock goes, then its thread answers
            self.reading.close()
            while self.threads: self.nested(*self.threads.pop(0))
        elif name=='sqlite3_exec':
            assert a==123; self.sqls.append(self.text(b)); err=self.get(u.reg_read(UC_MIPS_REG_SP)+16)
            try:
                for sql in filter(str.strip,self.text(b).split(';')):  # each statement, as sqlite3_exec runs them
                    for row in self.db.execute(sql).fetchall():
                        values=self.alloc(4*len(row))
                        for i,x in enumerate(row): self.word(values+4*i,self.string(str(x)))
                        if c: self.nested(c,d,len(row),values,0)
                if err: self.word(err,0)
            except sqlite3.OperationalError as e:
                ret=5 if 'locked' in str(e) else 1  # SQLITE_BUSY
                if err: self.word(err,self.string(str(e)))
        self.returns(ret)
with tempfile.TemporaryDirectory() as directory:
    path=str(pathlib.Path(directory)/'database.db'); disk=sqlite3.connect(path)
    disk.execute('create table songtable(id INTEGER PRIMARY KEY AUTOINCREMENT,filename)')
    disk.executemany('insert into songtable(filename) values(?)',[(f'{i}.flac',) for i in range(1000)]); disk.commit(); disk.close()
    for back in (1,0):
        m=ScanLockMachine(path=path); msg_win(m,'Updating No.0'); assert len(m.threads)==1  # the dialog's count, its thread not yet run
        m.reading=sqlite3.connect(path,timeout=0).execute('select id from songtable'); m.reading.fetchone()  # its read lock
        m.byte(STOP,back)  # toolsStopUpdateMusic's flag
        m.call(address=HOOKS['scanAllMusicFile'][0],args=(m.string('/mnt/mmc'),1,0,0),gap=0); m.reading.close()
        ran=[c[0] for c in m.calls if c[0].startswith('tools') or c[0]=='usleep']
        assert ran==([] if back else ['usleep','toolsClearDbCount','toolsLoadAllFile','toolsUpdateDbTable','toolsUpdateDbTable']),(ran,m.sqls)  # the larger indexes, then the rest
        assert m.sqls if not back else not m.sqls,m.sqls
        assert m.db.execute('select count(*) from songtable').fetchone()[0]==(1000 if back else 0); m.db.close()  # kept, or made again
passed()
# Albums without art start the thread behind the covers, which show at once; destroy joins it and drops its poll.
m=CoverflowMachine(cached=False); page=m.open()
assert len(m.threads)==1 and m.timers and m.find('slide_menu') and not any((t or '').startswith('Preparing') for t in m.texts()) and 'Cancel' not in m.texts()
f,ctx=m.handler(page,O['EVT_DESTROY']); m.freed=0; assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
assert m.joins==[77] and not m.timers and m.freed==4; passed()  # three job paths and the job array
# Tracks list and play in album order (disc, track, then path, then CUE start), named without
# their file's extension; a CUE title keeps its dot.
def track(m,name,path,disc=0,no=0,cue=0):
    r=m.song(name); m.word(r+O['REC_PATH'],m.string(path))
    for k,v in (('REC_DISC',disc),('REC_TRACK',no),('REC_CUE_START',cue)): m.word(r+O[k],v)
    return r
for found,want in (((('b.mp3','/p/b.mp3',2,1),('a.mp3','/p/a.mp3',1,2),('c.mp3','/p/c.mp3',1,1)),['c','a','b']),
                   ((('Mr. Blue','/p/img.flac',0,0,300),('Intro','/p/img.flac'),('01 x.flac','/p/01 x.flac')),['01 x','Intro','Mr. Blue'])):
    m=CoverflowMachine(); m.found=[track(m,*t) for t in found]; m.open(); view=m.tracks()
    assert [m.nodes[m.nodes[i]['children'][0]].get('text') for i in m.nodes[view]['children']]==want
    assert all(m.nodes[m.nodes[i]['children'][0]]['style']=='s_scrlabel_white20l' for i in m.nodes[view]['children'])  # left-aligned (page_row_detail)
    body=next(n for n in m.nodes.values() if m.find('list_view') in n.get('children',())); assert m.nodes[body['children'][0]]['style:normal:font_size']==O['CF_NAME_PX']
    f,ctx=m.handler(m.nodes[view]['children'][0],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
    assert [n.rsplit('.',1)[0] if n.endswith(('.mp3','.flac')) else n for n in m.names(m.plays[-1][1]&0xffffffff)]==want; passed()

COVER_ALBUM=['Play Next','Play Last','Shuffle','Favorite']  # a cover's menu, Go to artist aside
# Album holds use the live carousel before tracks have ever been opened, and the same ordering.
for action,want in ((0,['A','early','late','B','C']),(1,None)):
    m=CoverflowMachine(); m.found=[track(m,'late','/p/z',2,1),track(m,'early','/p/a',1,2)]
    m.open(); m.word(m.slide+O['SLIDE_INDEX'],2)
    m.press(100); assert m.hold()==11 and m.release()==0
    assert m.nodes[m.title]['text']=='Album 2' and m.labels()==[*COVER_ALBUM,'Go to Artist']
    assert m.hold()==0
    m.pick(action); m.pick(action); m.advance(0)
    assert m.names()==(want or played_last(m,['early','late'])) and not m.playback() and not m.plays and m.top==m.page
    assert m.query[:2]==('getMusicByAlbum','Album 2') and m.names(m.get(syms['tools_pdeq_directory']))==['staged']; passed()
m=CoverflowMachine(); m.open(); assert len(m.nodes[m.slide]['children'])==3; passed()  # no Sort or Refresh card to hold
for kwargs in ({'albums':0},{'cached':False}):
    m=CoverflowMachine(**kwargs); m.open(); m.press(100); assert m.hold()==0; passed()
for invalidate in ('selection','closed','tracks'):
    m=CoverflowMachine(); m.open(); m.press(100); assert m.hold()==11; m.release()
    if invalidate=='selection': m.word(m.slide+O['SLIDE_INDEX'],1)
    elif invalidate=='closed': m.close()
    else: m.tracks(1)
    m.pick(0); m.advance(0)
    assert m.names()==['A','B','C'] and m.toasts[-1][3]=='Queue unchanged'; passed()
m=CoverflowMachine(); m.open(); m.tracks(2); m.key(); m.word(m.slide+O['SLIDE_INDEX'],1)
assert m.run(2) is None and m.plays[-1][2] in (0,1) and m.plays[-1][3:]==(0xf001,2) and m.play_names==['T1','T2']; passed()
# A Library Albums row queues its own album alone, as a cover does: getMusicByAlbum finds every
# album of the name, here T1 in another folder.
m=CoverflowMachine(page='album_page',cls=O['CLASS_ALBUMS'],pos=2); m.word(m.found[0]+O['REC_PATH'],m.string('/q/T1'))
assert m.run(1)=='Playing Last' and m.names()==['A','B','C','T2'] and m.query[:3]==('getMusicByAlbum','Album',None)  # qm_run says what it did
assert m.names(m.get(syms['tools_pdeq_directory']))==['staged'] and m.pool()==[0,1,2,3]; passed()

# Go to artist uses the album record directly; an absent artist omits that action.
m=CoverflowMachine(); m.open(); m.run(4)
assert m.opened[-1][:2]==('localmusic/artistinfo_page',O['CLASS_ALBUMS']); passed()
m=CoverflowMachine(); m.word(m.albums[0]+O['REC_ARTIST'],0); m.open(); m.press(100); assert m.hold()==11
assert m.labels()==COVER_ALBUM; passed()
# Closing the source before the deferred open never creates a dialog.
m=CoverflowMachine(); m.open(); m.press(100)
assert m.call(O['KEY_PLAY'],address=syms['on_wm_keylong_fun'],event_type=O['EVT_KEY_LONG'],gap=0)==11
m.close(); m.advance(0); assert not any(p[0]=='dialog/sortselect_dialog' for p in m.opened); passed()

# Hold Play/Pause on a playlist's cover (navigation.c qm_hold): the menu sheet's playlist menu, Play, Shuffle, Rename
# and Delete. The playlist is found in the live list by its cd_num, as Rename and Delete read that list again under
# the covers after they copied it, and a playlist gone from it holds nothing.
PL_COVER_MENU=['Play','Shuffle','Rename','Delete']
class PlaylistCoverMachine(CoverflowMachine):
    def __init__(self):
        super().__init__()
        self.handlers[syms['navigator_to_with_context']]='q:navigator_to_with_context'  # the More page's index
        for n in ('playlist_art','playlist_art_refresh','playlist_art_stop','playlist_songs'):
            if n in payload_syms: self.handlers[payload_syms[n]]='pl:'+n
        self.handlers[syms['getAllPlayList']]='pl:getAllPlayList'  # stock's headers, as the page reads them
        self.edits=[]; self.lists=[self.song(f'List {i}',4) for i in range(3)]; self.heads=self.lists
        for i,r in enumerate(self.lists): self.word(r+O['REC_TITLE'],self.string(f'List {i}')); self.word(r+O['REC_DISC'],10+i)
        self.word(syms['p_deque_playlist'],self.deque(self.lists))
        self.under=self.node('window','playlist_page'); self.stack=[self.under]; self.top=self.under
        self.nodes[self.wm]={'children':self.stack}
        assert self.call(address=payload_syms['coverflow_playlists'],args=(self.get(syms['p_deque_playlist']),0,0,0),gap=0)==1
        self.page=self.top; self.slide=self.find('slide_menu')
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('pl:'): return super().hook(u,address,size,unused)
        if name=='pl:getAllPlayList':
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',r) for r in self.heads]
            return self.returns(len(self.heads))
        self.returns(0)
def bar_load(m):  # the first system_bar load, as on a unit (ringnav_ui_load: accent, theme_boot)
    m.record_syscalls(); m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'],16)]='stock_ui_load'
    m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0],args=(m.string('system_bar'),0,0,0),gap=0)
m=PlaylistCoverMachine(); bar_load(m)
m.word(m.slide+O['SLIDE_INDEX'],1); m.press(100); assert m.hold()==11 and m.release()==0
rows=[m.nodes[m.nodes[i]['children'][0]]['text'] for i in m.nodes[m.view]['children'] if m.nodes[i]['type']=='list_item']
assert rows==PL_COVER_MENU and m.nodes[m.title]['text']=='List 1',rows; passed()
m=PlaylistCoverMachine(); m.word(syms['p_deque_playlist'],m.deque(m.lists[::-1]))  # read again, reordered
m.press(100); assert m.hold()==11 and m.nodes[m.title]['text']=='List 0'; passed()
m=PlaylistCoverMachine(); m.word(syms['p_deque_playlist'],m.deque(m.lists[::-1]))  # Play: its songs, by cd_num 10's query
assert m.run('Play') is None and m.query[:2]==('getMusicByPlayList',10) and m.opened[-1]==('playing_page',['T1','T2'],0,0xff13,2); passed()
m=PlaylistCoverMachine(); m.word(syms['p_deque_playlist'],m.deque(m.lists[1:]))  # List 0 deleted under the covers
m.press(100); assert m.hold()!=11 and not any(p[0]=='dialog/sortselect_dialog' for p in m.opened); passed()
# More's Rename or Delete over the covers: as the covers come back to the front the playlists are read again
# (getAllPlayList, the page under them keeping its list), a deleted one's cover gone, the centre on its playlist; nothing
# changed, the covers stay; none left, the covers close for the list.
def pl_front(m):
    f,ctx=m.handler(m.page,O['EVT_WINDOW_FOREGROUND']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.advance(0)
    return m.find('slide_menu')
cover_cd=lambda m: m.get(m.call(address=payload_syms['coverflow_playlist'],args=(m.page,0,0,0),gap=0)+O['REC_DISC'])
m=PlaylistCoverMachine(); m.word(m.slide+O['SLIDE_INDEX'],1); m.advance(0)
assert pl_front(m)==m.slide and len(m.nodes[m.slide]['children'])==3; passed()
m.heads=m.lists[1:]; s=pl_front(m)  # List 0 deleted
assert len(m.nodes[s]['children'])==2 and m.get(s+O['SLIDE_INDEX'])==0 and cover_cd(m)==11,(m.get(s+O['SLIDE_INDEX'])); passed()
m.heads=[]; closes=sum(c[0]=='window_manager_close_window_force' for c in m.calls); pl_front(m)
assert sum(c[0]=='window_manager_close_window_force' for c in m.calls)==closes+1; passed()
# A Rename under the covers: the covers show the new name and so does the hold's title, from the cover's own record, the
# live list (p_deque_playlist) keeping the old name until the page is on top.
m=PlaylistCoverMachine(); m.word(m.slide+O['SLIDE_INDEX'],1); m.advance(0)
new_one=m.song('Renamed',4); m.word(new_one+O['REC_TITLE'],m.string('Renamed')); m.word(new_one+O['REC_DISC'],11)
m.heads=[m.lists[0],new_one,m.lists[2]]; pl_front(m); assert cover_cd(m)==11
m.press(100); assert m.hold()==11 and m.release()==0 and m.nodes[m.title]['text']=='Renamed',m.nodes[m.title]['text']; passed()

# Album favourites and ratings (ratings.c rate_*, coverflow.c album_id): a cover keeps 0-5 hearts of its own in
# /mnt/data/ringnav-album-ratings by album_id, the low three bits its hearts, 5 its favourite and its songs My Fav's; songs'
# ratings and the queue are untouched. Hold Play/Pause on a cover: Favorite or Unfavorite and Rate album, whose picker is Now
# Playing's on Coverflow's page: the wheel moves it, Centre or a heart saves, Return leaves it as it was.
ALBUM_RATES='/mnt/data/ringnav-album-ratings'
class FileMocks:
    """stdio over self.files (path -> bytes): a write lands on fclose, then rename; fail_write refuses 'wb'."""
    FILE_FNS=('fopen@GLIBC_2.2','fread@GLIBC_2.0','fwrite@GLIBC_2.0','fclose@GLIBC_2.2','rename@GLIBC_2.0')
    def file_mocks(self,prefix,files=None):
        self.files=dict(files or {}); self.open_files={}; self.fail_write=False
        for n in self.FILE_FNS: self.handlers[syms[n]]=prefix+n
    def file_op(self,u,name,a,b,c,d):
        if name=='fopen':
            path,mode=self.text(a),self.text(b)
            if (mode=='wb' and not self.fail_write) or (mode=='rb' and path in self.files):
                f=0x2000000+16*next(i for i in range(64) if 0x2000000+16*i not in self.open_files); self.open_files[f]=[path,b'',0]; return f
        elif name=='fread':
            o=self.open_files[d]; data=self.files[o[0]][o[2]:]; n=min(c,len(data)//b); o[2]+=n*b
            u.mem_write(a,data[:b*c].ljust(b*c,b'\0')); return n
        elif name=='fwrite': self.open_files[d][1]+=bytes(u.mem_read(a,b*c)); return c
        elif name=='fclose':
            path,data,_=self.open_files.pop(a)
            if data: self.files[path]=data
        elif name=='rename': self.files[self.text(b)]=self.files.pop(self.text(a))
        return 0
# USB Storage's page (navigation.c usb_poll): the card's LUN read open, then empty, is the computer's eject: the poll
# ends and the mode page (usb_ui.c usb_ui_ejected) is drawn again, saying OK to Disconnect (test/fw test_nav_paint.py
# checks its paint). Empty before it was ever open, or no file (no gadget), is no eject; Audio and DAC have no poll.
USB_LUN='/sys/kernel/config/usb_gadget/storage_demo/functions/mass_storage.msg/lun.0/file'
