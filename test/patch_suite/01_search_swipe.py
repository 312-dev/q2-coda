# The keyboard is a separate top window. Use the input method's owner, not that window's
# focus chain. Run stock input_method_request, mocking only its backend close operation.
class SearchMachine(Machine):
    def __init__(self,name='search_dialog',keyboard=True):
        super().__init__()
        self.dialog=self.node('dialog',name); self.edit=self.node('edit',name='search_edit')
        self.nodes[self.dialog]['children']=[self.edit]; self.word(self.edit+O['W_PARENT'],self.dialog)
        self.nodes[self.edit]['focused']=1
        self.top=self.node('keyboard','keyboard') if keyboard else self.dialog
        self.im=self.alloc(0x90); self.word(self.im+O['IM_WIDGET'],self.edit)
        self.word(self.im+0x7c,0x1000800); self.handlers[0x1000800]='search:close'
        self.handlers.pop(syms['input_method_request'],None)
        self.mock('input_method',prefix='search:')
        self.mock('widget_dispatch_simple_event')
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='search:input_method':
            self.calls.append((name,)); self.returns(self.im)
        elif name=='search:close':
            a,b=[u.reg_read(r) for r in REGS[:2]]
            assert a==self.im and b==0
            self.calls.append((name,a,b)); self.word(self.im+O['IM_WIDGET'],0); self.top=self.dialog
            self.returns(0)
        else: super().hook(u,address,size,unused)

for name in ('search_dialog','tidal_search_dialog','searchbox_dialog','tidal_searchbox_dialog'):
    for keyboard in (False,True):
        m=SearchMachine(name,keyboard)
        assert m.call(O['KEY_RETURN'],gap=0)==11 and m.nodes[m.edit]['focused']==0
        assert m.top==m.dialog and m.get(m.im+O['IM_WIDGET'])==0
        assert len([c for c in m.calls if c[0]=='search:close'])==1
        assert m.call(O['KEY_RETURN'],gap=0)==0 and not [c for c in m.calls if c[0]=='search:close']
        passed()
# Our T9 edits (peq_ui.c edit_create, EDIT_NAME): Return empties the box before the blur, so its
# EVT_VALUE_CHANGED is a cancel, then detaches the keyboard; stock's and the playlist search keep their text.
for name,text in (('q2_edit',''),('search_edit','query')):
    m=SearchMachine(); m.nodes[m.edit]['name']=name; m.nodes[m.edit]['text']='query'
    assert m.call(O['KEY_RETURN'],gap=0)==11 and m.nodes[m.edit]['focused']==0 and m.nodes[m.edit]['text']==text
    assert m.get(m.im+O['IM_WIDGET'])==0 and len([c for c in m.calls if c[0]=='search:close'])==1; passed()
# An idle input method, a different editor and unrelated keys leave the keyboard alone.
for case in ('idle','other','key','missing'):
    m=SearchMachine()
    if case=='idle': m.word(m.im+O['IM_WIDGET'],0)
    elif case=='other': m.nodes[m.edit]['name']='password'
    elif case=='missing': m.im=0
    key=O['KEY_NEXT'] if case=='key' else O['KEY_RETURN']
    assert m.call(key,gap=0)==0 and m.nodes[m.edit]['focused']==1
    assert not [c for c in m.calls if c[0]=='search:close']; passed()

# iPod: a confirm dialog's buttons are its rows (contexts.inc BUTTONS). The wheel moves between the
# side-by-side buttons without scrolling anything, the bar is the button's own tile, the ends are
# hard and Centre clicks the selected button. The Stock build leaves the dialog stock: the wheel is volume.
m=Machine(); m.config.update(FLAT); d=m.node('dialog','confirminfo_dialog'); m.word(d+O['W_PARENT'],m.wm)
m.word(d+O['W_W'],375); m.word(d+O['W_H'],320); m.clip=(0,0,375,320); m.top=d
buttons=[m.entry(d,220) for _ in range(2)]; m.nodes[d]['children']=buttons
pair=(53,242)  # a pair of tiles side by side, as Tidal's confirm keeps
for b,x in zip(buttons,pair): m.word(b+O['W_X'],x); m.word(b+O['W_W'],80); m.word(b+O['W_H'],80)
m.paint(d); assert m.selected(d)==0 and m.sel()==(53,220,80,80)
# The focused tile is framed in constant white (no accent lookup), over the accent's bar; stroke color restored.
frame=[r for r in m.rounded if r['kind']=='stroke']
assert m.bands and not m.strokes and [(r['rect'],r['radius'],r['color']) for r in frame]==[((53,220,80,80),O['SEL_RADIUS'],0xffffffff),((54,221,78,78),O['SEL_RADIUS']-1,0xffffffff)]
assert m.lcd_colors()==LCD_COLORS
assert m.call()==11 and m.selected(d)==1 and not m.moved()
m.paint(d); assert m.sel()==(242,220,80,80) and m.clip==(0,0,375,320)
assert not m.strokes and [r['rect'] for r in m.rounded if r['kind']=='stroke']==[(242,220,80,80),(243,221,78,78)]
assert m.call()==11 and m.selected(d)==1 and not m.moved()
assert m.call(O['KEY_PREV'])==11 and m.selected(d)==0
assert m.confirm()==11 and m.dispatched()[0][1]==buttons[0]; passed()
# A wide button (autoshutdown's Cancel) gets a list row's bar across the surface, unframed.
m.nodes[d]['name']='autoshutdown_dialog'; m.nodes[d]['children']=[buttons[0]]; m.word(buttons[0]+O['W_W'],287)
m.paint(d); assert [b[:5] for b in m.bands]==bar_bands(inset,220,375-2*inset,80,radius) and not m.strokes and not m.rounded; passed()
# iPod's confirminfo_dialog.bin: Cancel and OK are full-width rows, so the bar is a list's, unframed.
m=Machine(); m.config.update(FLAT); d=m.node('dialog','confirminfo_dialog'); m.word(d+O['W_PARENT'],m.wm)
m.word(d+O['W_W'],375); m.word(d+O['W_H'],320); m.clip=(0,0,375,320); m.top=d
rows=[m.entry(d,208+48*i) for i in range(2)]; m.nodes[d]['children']=rows
for r in rows: m.word(r+O['W_W'],375)
m.paint(d); assert m.selected(d)==0 and [b[:5] for b in m.bands]==bar_bands(inset,208,375-2*inset,48,radius) and not m.strokes
assert m.call()==11 and m.selected(d)==1; m.paint(d); assert [b[:5] for b in m.bands]==bar_bands(inset,256,375-2*inset,48,radius)
assert m.confirm()==11 and m.clicks==[rows[1]]; passed()

# USB mode's exit asks whether to scan before it leaves the mode, the cable out or still in (the mode
# page's Stop): the prompt takes the wheel (iPod: its buttons) either way.
for cable in (0,1):
    m=Machine(); d=m.node('dialog','confirminfo_dialog',[]); m.word(d+O['W_PARENT'],m.wm); m.top=d
    m.nodes[d]['children']=[m.entry(d,220) for _ in range(2)]
    m.byte(syms['g_usblink_status'],2); m.byte(syms['g_usbdet_value'],cable)
    assert m.call()==11; passed()

# The real input entry handles USB confirmation before native USB/volume routing.
m=Machine(); d=m.node('dialog','confirminfo_dialog',[]); m.word(d+O['W_PARENT'],m.wm); m.top=d
m.nodes[d]['children']=[m.entry(d,220) for _ in range(2)]
m.byte(syms['g_usblink_status'],2); m.byte(syms['g_usbdet_value'],0)
m.handlers[payload_syms['stock_input_trampoline']]='stock_input'
assert m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=0)==0
assert m.selected(d)==1 and not any(c[0]=='stock_input' for c in m.calls)
assert any(c[0]=='reset_poweroptions_timer' for c in m.calls)
m.call(O['KEY_NEXT'],address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),event_type=O['EVT_POINTER_DOWN'],gap=0)
assert any(c[0]=='stock_input' for c in m.calls)
m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=1000)
assert m.selected(d)==1
m.byte(syms['g_usbdet_value'],1)  # the cable in again: still the prompt's key
stock=sum(c[0]=='stock_input' for c in m.calls)
m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=0)
assert sum(c[0]=='stock_input' for c in m.calls)==stock; passed()
# Transition-time volume applies once, without a queued overlay; fresh input stays native.
m=Machine(); m.top=m.node('window','playing_page'); m.animating=1
m.byte(syms['g_volume'],20); m.byte(syms['g_maxvolume'],21)
m.handlers[payload_syms['stock_input_trampoline']]='stock_input'
for key,want in ((O['KEY_NEXT'],21),(O['KEY_NEXT'],21),(O['KEY_PREV'],20)):
    m.call(key,address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=0)
    assert m.u.mem_read(syms['g_volume'],1)[0]==want
    assert not any(c[0] in ('stock_input','navigator_to') for c in m.calls)
m.byte(syms['g_volume'],100); m.byte(syms['g_maxvolume'],200)
m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=0)
assert m.u.mem_read(syms['g_volume'],1)[0]==100
m.animating=0
m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),gap=0)
assert any(c[0]=='stock_input' for c in m.calls) and not any(c[0]=='device_set_volume' for c in m.calls); passed()

# Slider settings: the wheel presses the page's own +/-, Centre goes back once the double-press
# window has passed, and a double Centre is stock's screen toggle instead.
for name in ('backlight_page','maxvol_page','bootvol_page','balance_page'):
    m=Machine(); add,dec=m.node('image','img_add'),m.node('image','img_dec')
    m.top=m.node('window',name,[m.node('view','view_body',[dec,add])]); m.mock('on_wm_keyup_fun')
    assert m.call()==11 and m.clicks==[add]
    assert m.call(O['KEY_PREV'])==11 and m.clicks==[add,dec]
    m.byte(syms['bt__recv_pageflag'],2)
    assert m.call()==11 and m.call(O['KEY_PREV'])==11 and m.clicks==[add,dec,add,dec]
    m.byte(syms['bt__recv_pageflag'],0)
    assert m.confirm()==11 and [c[0] for c in m.calls].count('navigator_back')==1
    assert m.call(O['KEY_CENTER'])==11 and m.call(O['KEY_CENTER'],gap=50)==11
    m.advance(300,clear=False); names=[c[0] for c in m.calls]
    assert 'on_wm_keyup_fun' in names and 'navigator_back' not in names; passed()
# Default Volume's page has its switch (img_bootswitch), its row selected: Centre turns it on or off, as a tap on the row
# does, and stays on the page; Return goes back.
m=Machine(); sw=m.node('image','img_bootswitch')
m.top=m.node('window','bootvol_page',[m.node('view','view_navbar',[sw]),m.node('view','view_body',[m.node('image','img_dec'),m.node('image','img_add')])])
assert m.confirm()==11 and m.clicks==[sw] and 'navigator_back' not in [c[0] for c in m.calls]; passed()
# Control Center (control_center.c cc_key): Centre on Brightness gives the wheel the slider (QS_ADJUST), which it sets
# within its range; Return gives it back, and Return off a slider closes it, as its swipe up does (dialog_quit), not
# stock's screen off and Home.
QS_ADJUST='_cc_adjust'  # control_center.c CC_ADJUST: 1 while a slider has the wheel
CC_BRIGHT=7  # control_center.c's Brightness stop
RING='_cc_seen'  # control_center.c CC_SEEN: the wheel's last tick, time_now_ms + 1, so its ring shows and a key acts
m=Machine(); bright=m.node('slider','slider_backlight',value=99)
m.top=m.node('dialog','statusbar_dialog',[m.node('view','view_backlight',[bright])],_cc_focus=CC_BRIGHT,**{RING:m.now+1}); m.mock('dialog_quit')
assert m.call(O['KEY_CENTER'])==11 and m.nodes[m.top][QS_ADJUST]==1
assert m.call()==11 and m.nodes[bright]['value']==100
assert m.call()==11 and m.nodes[bright]['value']==100
assert m.call(O['KEY_PREV'])==11 and m.nodes[bright]['value']==99
assert m.call(O['KEY_RETURN'])==11 and m.nodes[m.top][QS_ADJUST]==0 and not [c for c in m.calls if c[0]=='dialog_quit']
assert m.call(O['KEY_RETURN'])==11 and [c[1:3] for c in m.calls if c[0]=='dialog_quit']==[(m.top,0)]
passed()
# Control Center's landing (control_center.c cc_land, cc_spring): a frame of it, 50 ms in with the blocks 8 px down,
# stays within the 600,000 payload instructions a frame may take (docs/games-engine.md), stock's drawing aside.
m=Machine(); bright=m.node('slider','slider_backlight',value=50)
m.top=m.node('dialog','statusbar_dialog',[m.node('view','view_backlight',[bright])],_cc_landed=m.now+1-50,**{RING:m.now+1})
instructions=[0]
def count_payload(*args): instructions[0]+=1
hook=m.u.hook_add(UC_HOOK_CODE,count_payload,begin=BASE,end=SCRATCH-1)
m.call(address=payload_syms['cc_paint'],args=(m.top,m.canvas,0,0),gap=0,count=5_000_000)
m.u.hook_del(hook)
print('Control Center landing frame:',instructions[0],'payload instructions')
assert any(r['rect']==(20,38,158,210) for r in m.rounded),m.rounded  # cc_paint ran: the toggles' block
assert instructions[0]<600_000,instructions; passed()
# A swipe down whose release the pull-down never got (stock state 1 or 2, no touch, no slide timer)
# leaves it on top off screen, the page under it stopped (#189): the next UI pass hands it the
# release, which stock's handler slides; an open panel, a live drag and a running slide are left.
for state,pressed,timer,stuck in ((1,0,None,1),(2,0,-1,1),(3,0,None,0),(0,0,None,0),(1,1,None,0),(1,0,7,0)):
    m=Machine(); m.top=m.node('dialog','statusbar_dialog',**({} if timer is None else {'timer_idex':timer}))
    m.word(syms['g_dropdown_menu_state'],state); m.pressed=pressed
    m.handlers[payload_syms['stock_input_trampoline']]='stock_input'
    m.call(address=HOOKS['window_manager_dispatch_input_event'][0],args=(m.wm,m.event,0,0),event_type=O['EVT_POINTER_FIRST'],gap=0)  # a touch just now: a finger
    m.handlers[HOOKS['main_loop_sleep_default'][0]+12]='stock_sleep'
    m.call(address=HOOKS['main_loop_sleep_default'][0],args=(0x1234,0,0,0),gap=0)
    assert m.ups==[m.top]*stuck,(state,pressed,timer)
passed()
# While the pull-down slides (stock state 1 or 2, or its timer_idex slide timer running) the wheel
# does nothing there, on the slider too: only the rested, open panel takes a tick.
for state,timer in ((1,7),(2,7),(3,7)):
    m=Machine(); bright=m.node('slider','slider_backlight',value=50)
    m.top=m.node('dialog','statusbar_dialog',[m.node('view','view_backlight',[bright])],timer_idex=timer,_cc_focus=CC_BRIGHT,**{QS_ADJUST:1,RING:m.now+1})
    m.word(syms['g_dropdown_menu_state'],state)
    assert m.call()==11 and m.nodes[bright]['value']==50, (state,timer)
for state,timer,want in ((1,-1,50),(2,-1,50),(3,-1,51)):  # the state alone gates it, and at rest it ticks
    m=Machine(); bright=m.node('slider','slider_backlight',value=50)
    m.top=m.node('dialog','statusbar_dialog',[m.node('view','view_backlight',[bright])],timer_idex=timer,_cc_focus=CC_BRIGHT,**{QS_ADJUST:1,RING:m.now+1})
    m.word(syms['g_dropdown_menu_state'],state)
    assert m.call()==11 and m.nodes[bright]['value']==want, (state,timer)
passed()
# A swipe up that closes it may start on the brightness slider: the slider gets its press only once the
# touch goes 10 px more sideways than up or down, or with a tap's release; a touch that goes up first,
# or any while stock slides the pull-down (g_dropdown_menu_state 1, 2), never reaches it, and a press
# it has is aborted. Withheld events return 0, not STOP, so the window manager still sees the touch.
def slider_touch(steps,state=3,dialog='statusbar_dialog'):
    m=Machine(); bright=m.node('slider','slider_backlight',value=50)
    view=m.node('view','view_backlight',[bright]); m.top=m.node('dialog',dialog,[view])
    m.word(bright+O['W_PARENT'],view); m.word(view+O['W_PARENT'],m.top)
    m.word(syms['g_dropdown_menu_state'],3); got=[]
    for kind,x,y in steps:
        if kind=='up': m.word(syms['g_dropdown_menu_state'],state)
        m.word(m.event+O['EVENT_Y'],y)
        typ={'down':O['EVT_POINTER_DOWN']-1,'move':O['EVT_POINTER_MOVE_BEFORE']-1,'up':O['EVT_POINTER_UP_BEFORE']-1}[kind]
        ret=m.call(x,address=HOOKS['widget_dispatch'][0],args=(bright,m.event,0,0),event_type=typ,gap=0)  # EVENT_X is EVENT_KEY's word
        got.append((ret,[m.get(c[2]) for c in m.dispatched() if c[1]==bright]))
    return got
D,M,U,A=O['EVT_POINTER_DOWN']-1,O['EVT_POINTER_MOVE_BEFORE']-1,O['EVT_POINTER_UP_BEFORE']-1,O['EVT_POINTER_ABORT']
assert slider_touch([('down',100,300),('move',103,290),('move',104,270),('move',140,250),('up',140,200)])==[(0,[])]*5  # a swipe up
assert slider_touch([('down',100,300),('move',105,301),('move',120,302),('up',150,302)])==[(0,[]),(0,[]),(0,[D,M]),(0,[U])]  # a drag
assert slider_touch([('down',100,300),('up',101,301)])==[(0,[]),(0,[D,U])]  # a tap
assert slider_touch([('down',100,300),('move',120,300),('up',120,250)],state=1)==[(0,[]),(0,[D,M]),(0,[A])]  # then stock slides it
assert slider_touch([('down',100,300),('up',100,300)],state=2)==[(0,[]),(0,[])]
assert slider_touch([('down',100,300),('move',103,280)],dialog='backlight_page')==[(0,[D]),(0,[M])]  # Settings' slider is stock's
passed()
# Run stock's slider event handler with the packaged hit rectangle. Position arithmetic is
# stock FP64 (unsupported by Unicorn); this checks capture and delivery, including outside drags.
from ipod import decode,walk as ui_walk
node=next(n for n in ui_walk(decode((B/'ui/playing_page.bin').read_bytes())) if n[2].get('name')=='slider_play')
bx,by,bw,bh=node[1]  # the target's ends and middle, each way
for x in (bx,bx+bw//2,bx+bw-1):
  for y in (by,by+bh//2,by+bh-1):
   for end in (U,A):
    m=Machine(); slider=m.node('slider','slider_play'); parent=m.node('window','playing_page',[slider])
    m.word(slider+O['W_PARENT'],parent)
    for k,v in zip(('W_X','W_Y','W_W','W_H'),node[1]): m.word(slider+O[k],v)
    assert node[1][0]<=x<node[1][0]+node[1][2] and node[1][1]<=y<node[1][1]+node[1][3]
    m.word(slider+0x98,int(node[2]['bar_size'])); m.byte(slider+0xa6,node[2]['slide_with_bar']=='true')
    m.handlers[0x62d284]='stock_slider_preview'
    m.mock('widget_find_animator','widget_set_state','widget_grab','widget_ungrab')
    def pointer(typ,x,y):
        m.byte(m.event+0x24,1); m.word(m.event+O['EVENT_Y'],y)
        return m.call(x,address=0x62d7c4,args=(slider,m.event,0,0),event_type=typ,gap=0)
    assert pointer(D,x,y)==11 and bytes(m.u.mem_read(slider+0xac,1))==b'\x01'
    assert any(c[0]=='widget_grab' and c[1:3]==(parent,slider) for c in m.calls)
    pointer(M,400,210)  # drift well above and beyond the bar without losing the drag
    assert bytes(m.u.mem_read(slider+0xad,1))==b'\x01'
    assert any(c[0]=='stock_slider_preview' for c in m.calls)
    pointer(end,400,210)
    assert bytes(m.u.mem_read(slider+0xac,2))==b'\x00\x00'
    assert any(c[0]=='widget_ungrab' and c[1:3]==(parent,slider) for c in m.calls)
    passed()

# Set date and time: the wheel turns the outlined field, Centre moves on (to the time page after
# the day) and then to OK, which Centre presses; a turn on OK goes back to the fields.
def time_page(name):
    m=Machine(); names=['tselector_year','tselector_month','tselector_day','tselector_hour','tselector_min']
    if name=='sleepshutdown_page': names=names[3:]
    sels=[m.node('text_selector',n,options=12) for n in names]
    for w in sels: m.word(w+O['SELECTOR_INDEX'],5); m.word(w+O['W_W'],80); m.word(w+O['W_H'],170)
    ok=m.node('button','btn_enter'); slide=m.node('slide_view','slide_view',sels,value=0)
    m.top=m.node('window',name,[slide,ok]); return m,sels,ok,slide
m,sels,ok,slide=time_page('manualtime_page')
assert m.call()==11 and m.get(sels[0]+O['SELECTOR_INDEX'])==6
m.paint(sels[0]); assert not m.strokes and m.lcd_colors()==LCD_COLORS
assert [(r['kind'],r['rect'],r['radius'],r['color']) for r in m.rounded]==[('stroke',(0,0,80,170),O['SEL_RADIUS'],0xffffffff),('stroke',(1,1,78,168),O['SEL_RADIUS']-1,0xffffffff)]
m.rounded_fail=True; m.paint(sels[0]); assert [s[:4] for s in m.strokes]==[(0,0,80,170),(1,1,78,168)] and m.lcd_colors()==LCD_COLORS
m.rounded_fail=None; m.paint(sels[1]); assert not m.strokes and not m.rounded
for f in range(1,6): assert m.confirm()==11 and m.nodes[m.top]['_edit_field']==f
assert m.nodes[slide]['value']==1 and not m.clicks
# OK, when next, is a selected row: the off-white card with its label dark, not a frame.
m.word(ok+O['W_W'],287); m.word(ok+O['W_H'],40); m.nodes[ok]['text']='OK'; m.paint(ok)
assert not m.strokes and [(r['rect'],r['radius']) for r in m.rounded]==[((0,0,287,40),O['SEL_RADIUS'])]
assert [l['text'] for l in m.letters]==['OK'] and m.lcd_colors()==LCD_COLORS
backs=lambda: [c[0] for c in m.calls].count('navigator_back')
assert m.confirm()==11 and m.clicks==[ok] and backs()==1  # OK sets the time, then back to Time
assert m.call(O['KEY_PREV'])==11 and m.nodes[m.top]['_edit_field']==4
for _ in range(9): m.call()
assert m.get(sels[4]+O['SELECTOR_INDEX'])==11
m.nodes[slide]['value']=0; assert m.call(O['KEY_PREV'])==11 and m.get(sels[0]+O['SELECTOR_INDEX'])==5; passed()
# The side keys move between the fields and OK, stopping at the ends; a tap on OK goes back too.
m,sels,ok,slide=time_page('manualtime_page'); field=lambda: m.nodes[m.top].get('_edit_field',0)
assert m.call(O['KEY_BACK_BTN'])==11 and field()==0
for f in (1,2,3,4,5,5): assert m.call(O['KEY_FWD_BTN'])==11 and field()==f
assert m.nodes[slide]['value']==1 and not m.clicks
assert m.call(O['KEY_BACK_BTN'])==11 and field()==4 and m.call(O['KEY_BACK_BTN'])==11 and field()==3
assert not any(c[0] in ('media_key','stock_input') for c in m.calls)
m.call(address=HOOKS['widget_dispatch'][0],args=(ok,m.event,0,0),event_type=O['EVT_CLICK'],gap=0)
assert m.clicks==[ok] and [c[0] for c in m.calls].count('navigator_back')==1; passed()
m,sels,ok,slide=time_page('sleepshutdown_page')
assert m.call()==11 and m.get(sels[0]+O['SELECTOR_INDEX'])==6
assert m.call(O['KEY_FWD_BTN'])==11 and m.nodes[m.top]['_edit_field']==1 and m.call(O['KEY_BACK_BTN'])==11
assert m.confirm()==11 and m.confirm()==11 and m.confirm()==11 and m.clicks==[ok]
assert 'navigator_back' not in [c[0] for c in m.calls]; passed()  # the sleep timer's OK stays stock's

# A picker opens on its checked option, not the row last chosen there.
m=Machine(); w,es=m.page_list(4,name='playmode_page')
m.nodes[es[2]]['children']=[m.node('image',image='select')]
m.paint(w); assert m.selected(w)==2; passed()
# Stock's radio rings are emptied (paint_radio): a checked row gets the theme's check, centred on the image's right
# end (oled_check_w, white, on the selection's bar; oled_check_a, the accent's, off it), an unchecked one nothing. A
# playlist's quiet More (img_more, unselect) is three 4 px dots 7 px apart in the theme's muted grey, off the selection.
OLED_MUTED=color_t(OLED_SCHEME['muted'])
for row,image,name,moved,want in ((2,'select','',0,'oled_check_w'),(2,'select','',1,'oled_check_a'),(2,'unselect','',0,None),
                                 (1,'unselect','img_more0',0,'dots'),(0,'unselect','img_more0',0,None)):
    m=Machine(); w,es=m.page_list(4,name='playmode_page')
    radio=m.node('image',name,image=image); m.nodes[es[row]]['children']=[radio]
    m.word(radio+O['W_PARENT'],es[row]); m.word(radio+O['W_W'],50); m.word(radio+O['W_H'],70)
    m.paint(w); assert m.selected(w)==(row if image=='select' else 0)
    if moved: m.call(); m.paint(w); assert m.selected(w)==row+1
    m.paint(radio)
    if want=='dots': assert [(r['rect'],r['radius'],r['color']) for r in m.rounded]==[((16+7*i,33,4,4),2,OLED_MUTED) for i in range(3)] and not m.icons
    else: assert not m.rounded and m.image_loads==([want] if want else []) and m.icons==([(25,35,(0,0,240,240))] if want else []),(row,image,name)
    assert m.lcd_colors()==LCD_COLORS
    passed()

# iPod: a playlist's More page (Rename, Delete) is a BUTTONS page: the wheel moves between them.
m=Machine(); top=m.node('window','playlistsmore_page'); m.word(top+O['W_PARENT'],m.wm); m.top=top
m.word(top+O['W_W'],375); m.word(top+O['W_H'],290)
rows=[m.node('list_item',children=[m.entry(0)]) for _ in range(2)]
for i,r in enumerate(rows): m.word(r+O['W_Y'],10+i*78); m.word(m.nodes[r]['children'][0]+O['W_PARENT'],r); m.word(r+O['W_PARENT'],top)
m.nodes[top]['children']=rows
assert m.call()==11 and m.selected(top)==1 and m.call()==11 and m.selected(top)==1
assert m.confirm()==11 and m.clicks==[m.nodes[rows[1]]['children'][0]]; passed()

# An interrupted recall glide keeps the remembered row instead of adopting a visible one.
m,w,es=walk(10,5); w2,es2=m.page_list(10,extent=1000); m.glide=False
assert m.touch()==0
m.paint(w2); assert m.selected(w2)==5
w3,es3=m.page_list(10,extent=1000)
assert m.paint(w3)==0 and m.selected(w3)==5; passed()
# A wheel detent after recreation interrupts recall and sets the offset immediately.
m,w,es=walk(10,5); w2,es2=m.page_list(10,extent=1000); m.glide=False
assert m.call()==11 and m.selected(w2)==6
assert m.moved()[-1][0]=='scroll_view_set_offset' and m.moved()[-1][3]==240; passed()  # whole rows (OLED's whole rows)

# A live list whose row count changes resets the selection without re-reading the table.
m,w,es=walk(10,5)
m.nodes[w]['children']=es[:6]; m.word(w+O['VIEW_CONTENT_H'],6*48); m.word(w+O['SCROLL_Y'],0)
m.paint(w); assert m.selected(w)==0; passed()

# Virtual music tables recall a logical row and scroll to it on recreation.
m=Machine(); w,_,entries=m.table_page()
m.paint(w)
for _ in range(2): assert m.call()==11
assert m.selected(w)==2 and m.get(w+O['TABLE_TOP'])==48  # a whole row under the top (OLED's whole rows)
m.rebind=None
w2,_,entries2=m.table_page()
m.paint(w2); assert m.selected(w2)==2 and m.get(w2+O['TABLE_TOP'])==48
assert m.confirm()==11 and m.dispatched()[0][1]==entries2[2]; passed()

# Table recall survives interruption even when the target is outside the recycled row pool.
for wanted in (2,12):
    m=Machine(); w,rs,es=m.table_page(n=20); m.paint(w)
    for _ in range(wanted): m.call()
    w2,rs2,es2=m.table_page(); m.glide=False
    m.touch(); m.paint(w2)
    assert m.selected(w2)==wanted and m.moved()[-1][2]==(wanted-1)*48
    # Rebind the pool as the restarted glide completes.
    top=(wanted-1)*48+12
    m.word(w2+O['TABLE_TOP'],top); m.word(w2+O['TABLE_ANIMATOR'],0)
    m.bind(rs2,top)
    m.paint(w2); assert m.selected(w2)==wanted
    m.confirm(); assert m.dispatched()[0][1]==es2[1]
    passed()
# A wheel during recall advances the logical target; an explicit tap instead replaces it.
m=Machine(); w,rs,es=m.table_page(n=20); m.paint(w)
for _ in range(12): m.call()
w2,rs2,es2=m.table_page(rebind=True); m.glide=False
m.touch(); m.call(); assert m.selected(w2)==13
m.click(es2[0]); assert m.get(w2+O['TABLE_ANIMATOR'])==0
m.paint(w2)
assert m.selected(w2)==12
m.confirm(); assert m.dispatched()[0][1]==es2[0]; passed()

# A synchronous restore rebind must discard the pre-scroll pool before centre dispatch.
m=Machine(); w,rs,es=m.table_page(n=20); m.paint(w)
for _ in range(12): m.call()
w2,rs2,es2=m.table_page(rebind=True)
m.paint(w2); assert m.selected(w2)==12
m.confirm(); assert m.dispatched()[0][1]==es2[1]; passed()

# Home keeps its native carousel presentation and value (including touch changes).
m=Machine(); w=m.page('home_page','slide_menu'); m.word(w+O['SLIDE_INDEX'],1)
child=[m.entry(w),m.entry(w)]; m.nodes[w]['children']=child
assert m.paint(w)==0 and not m.drawn()
assert any(c[0]=='stock_paint' for c in m.calls)
assert m.confirm()==11 and m.dispatched()[0][1]==child[1]
m.word(w+O['SLIDE_INDEX'],0)
assert m.confirm()==11 and m.dispatched()[0][1]==child[0]; passed()
# Long-press/boot release must reach stock cleanup, never activate a menu item.
for addr in [syms['g_power_longkey'],syms['g_ingore_bootkey_flag'],O['BOOT_KEY_GUARD']]:
    m.byte(addr,1); assert m.confirm()==0 and not m.dispatched(); m.byte(addr,0); passed()
# A single release confirms exactly once at DOUBLE_CLICK_MS, never a millisecond before.
m=Machine(); w,es=m.page_list(3)
assert m.release()==11 and not m.clicks
m.advance(DC-1); assert not m.clicks
m.advance(1); assert m.clicks==[es[0]] and not m.timers and not m.screens
m.advance(1000); assert m.clicks==[es[0]]; passed()
# Stock's wheel lockout after the centre key ends when the press opens its row: the next tick moves.
m=Machine(); w,es=m.page_list(3)
assert m.release()==11 and m.u.mem_read(O['KEY_LOCKOUT'],1)==b'\x08'
m.advance(DC); assert m.clicks==[es[0]] and m.u.mem_read(O['KEY_LOCKOUT'],1)==b'\0'
assert m.call(gap=0,debounce=True)==11 and m.selected(w)==1; passed()
# Before the deadline, the second release cancels the click and executes stock screen-off.
for gap in (0,100,DC-1):
    m=Machine(); w,es=m.page_list(3)
    assert m.release()==11 and m.release(gap)==0
    assert m.screens==[0] and not m.u.mem_read(syms['g_backlight_status'],1)[0]
    m.advance(1000); assert not m.clicks and not m.timers
    # A release while off follows the real stock wake path, with no menu activation.
    assert m.release()==0 and m.screens==[0,1]
    assert m.u.mem_read(syms['g_backlight_status'],1)[0]==1 and not m.clicks; passed()
# A page still sliding in takes Centre, but a double press there still turns the screen off; a slow
# second press does not, and the first press alone never does.
m=Machine(); w,es=m.page_list(3); m.animating=1
assert m.release()==11 and not m.screens and m.release(DC-1)==11 and m.screens==[0]
m=Machine(); w,es=m.page_list(3); m.animating=1
assert m.release()==11 and m.release(DC)==11 and not m.screens; passed()
# At/after expiry, the first single has dispatched; the next release starts a new single.
for gap in (DC,DC+1):
    m=Machine(); w,es=m.page_list(3)
    assert m.release()==11 and m.release(gap)==11 and m.clicks==[es[0]]
    m.advance(300); assert m.clicks==[es[0],es[0]] and not m.screens; passed()
# Touch and either wheel direction cancel pending confirmation, including invalid navigation.
for action in ('touch','next','prev'):
    m=Machine(); w,es=m.page_list(3); m.release()
    if action=='touch': m.call(address=HOOKS['on_wm_tsdown_before_fun'][0],gap=100)
    else: m.call(O['KEY_NEXT'] if action=='next' else O['KEY_PREV'],gap=100)
    m.advance(1000); assert not m.clicks and not m.timers
    assert m.release()==11
    m.advance(300); assert len(m.clicks)==1 and not m.screens; passed()
# Changed, destroyed, or address-reused targets cannot receive a delayed click.
for change in ('top','surface','scope','selection','count','text','hidden','disabled',
               'animating','pressed','screen_off','locked','long','boot','reused_top','reused_surface'):
    m=Machine(); w,es=m.page_list(3,name='folder_page')
    m.folder('/first')
    m.nodes[es[0]]['text']='First'
    m.release()
    if change=='top': m.page_list(3,name='display_page')
    elif change=='surface': m.nodes[m.top]['children']=[m.node()]
    elif change=='scope': m.folder('/other')
    elif change=='selection': m.nodes[w]['_ringnav_index']=1
    elif change=='count': m.nodes[w]['children']=es[:2]
    elif change=='text': m.nodes[es[0]]['text']='Rebound'
    elif change=='hidden': m.nodes[w]['visible']=0
    elif change=='disabled': m.nodes[es[0]]['enable']=0
    elif change=='animating': m.animating=1
    elif change=='pressed': m.pressed=1
    elif change=='screen_off': m.byte(syms['g_backlight_status'],0)
    elif change=='locked': m.byte(syms['g_lockscreen_pageflag'],1)
    elif change=='long': m.byte(syms['g_power_longkey'],1)
    elif change=='boot': m.byte(O['BOOT_KEY_GUARD'],1)
    else: m.nodes[m.top if change=='reused_top' else w].pop('_ringnav_confirm')
    m.advance(300); assert not m.clicks and not m.timers,change; passed()
# Non-virtual rows cannot inherit confirmation merely by sharing an index and text.
for kind in ('scroll_view','slide_menu'):
    for text in ('','Same title'):
        for reused in (False,True):
            m=Machine(); w,es=m.page_list(3)
            m.nodes[w]['type']=kind
            if kind=='slide_menu': m.word(w+O['SLIDE_INDEX'],0)
            m.nodes[es[0]]['text']=text
            m.release()
            if reused:
                m.nodes[es[0]].pop('_ringnav_confirm',None)
            else:
                replacement=m.entry(w)
                m.nodes[replacement]['text']=text
                m.nodes[w]['children'][0]=replacement
            m.advance(300)
            assert not m.clicks and not m.timers,(kind,text,reused)
            passed()

# A recycled table pool resolves the original logical index from the live row mapping.
m=Machine(); w,rs,es=m.table_page(); m.release()
m.word(rs[0]+O['ROW_INDEX'],1); m.word(rs[1]+O['ROW_INDEX'],0)
m.advance(300); assert m.clicks==[es[1]]; passed()
m=Machine(); w,rs,es=m.table_page(); m.release(); m.bind(rs,480)
m.advance(300); assert not m.clicks; passed()
# Invalid state observed before expiry stays cancelled even if the same page returns. The held tick, replayed once
# the animation ends, moves the list, so only OLED's scroll indicator (list_marks.c sb_due) has a timer.
m=Machine(); w,es=m.page_list(3); m.release(); m.animating=1
m.call(gap=100); m.animating=0; m.advance(300)
assert not m.clicks and [t[1] for t in m.timers.values()]==[payload_syms['sb_due']]; passed()
# Timer allocation failure consumes both releases without activation; the double press still turns
# the screen off, as on any page that keeps Centre.
m=Machine(); w,es=m.page_list(3); m.timer_fail=True
assert m.release()==11 and m.release(100)==11
m.advance(1000); assert not m.clicks and m.screens==[0] and not m.timers; passed()
# Empty menus never arm confirmation; a double press there still turns the screen off.
m=Machine(); m.page()
assert m.release()==11 and m.release(100)==11
assert not m.timers and not m.clicks and m.screens==[0]; passed()
# Audit the stock creation path independently: live offset, duration, easing,
# completion address and deselection agree with the fields used by the patch.
m=Machine(); w=m.page('home_page','slide_menu')
m.call(address=0x606290,args=(w,-240,0,0),gap=0)
a=m.get(w+O['SLIDE_ANIMATOR'])
assert m.get(a+O['ANIM_DURATION'])==150 and signed(m.get(a+O['ANIM_X_TO']))==-240
assert m.slide_callbacks[a]==(syms['slide_menu_on_scroll_done'],w)
assert m.nodes[m.nodes[w]['children'][0]]['focused']==0
m.advance(150); assert m.get(w+O['SLIDE_INDEX'])==1 and not m.slides; passed()

# Execute native parameter writes and stock completion; only the scheduler is
# deterministic. Check live origin, destination, duration, focus and one active animator.
def slide(m,w):
    a=m.get(w+O['SLIDE_ANIMATOR'])
    assert a and list(m.slides)==[a]
    return a,signed(m.get(a+0x68)),signed(m.get(a+O['ANIM_X_TO'])),m.get(a+O['ANIM_DURATION'])

for start in (0,0xfffffff0):
    for key,direction in ((O['KEY_NEXT'],1),(O['KEY_PREV'],-1)):
        for count in (3,4,5):
            m=Machine(); m.now=start; w=m.page('home_page','slide_menu')
            assert m.call(key,gap=0)==11
            a,origin,goal,duration=slide(m,w)
            assert (origin,goal,duration)==(0,-direction*240,200)
            for i in range(1,count):
                m.advance(30); live=signed(m.get(w+O['SLIDE_OFFSET']))
                assert m.call(key,gap=0)==11
                assert slide(m,w)==(a,live,-direction*240*(i+1),120)
            m.advance(120)
            assert not m.slides and m.get(w+O['SLIDE_ANIMATOR'])==0
            assert m.get(w+O['SLIDE_OFFSET'])==0
            assert m.get(w+O['SLIDE_INDEX'])==(direction*count)%7
            assert m.nodes[m.nodes[w]['children'][(direction*count)%7]]['focused']==1
            m.advance(126); m.call(key,gap=0)
            assert slide(m,w)[3]==200
            m.advance(200); settled=m.get(w+O['SLIDE_INDEX']); m.advance(500)
            assert m.get(w+O['SLIDE_INDEX'])==settled and not m.slides
            passed()
# The theme's carousel steps: a glide takes its kind's ms and easing, fast ticks too.
# OLED's (motion.step and step_fast) are 200 and 120 ms, SLIDE_EASING; a theme's other rows are read the same way.
def step_ticks(m,w,*ticks):
    for gap,ms,e in ticks:
        m.slide_easing=e; assert m.call(gap=gap)==11 and slide(m,w)[3]==ms
        assert m.get(m.get(w+O['SLIDE_ANIMATOR'])+O['ANIM_EASING'])==m.call(address=syms['easing_get'],args=(e,0,0,0),gap=0)
OLED_STEPS=((0,OLED_T['motion.step.ms'],OLED_T['motion.step.easing']),(50,OLED_T['motion.step_fast.ms'],OLED_T['motion.step_fast.easing']))
assert OLED_STEPS==((0,200,8),(50,120,8))
m=Machine(); w=m.page('home_page','slide_menu'); step_ticks(m,w,*OLED_STEPS)
m=Machine(); w=m.page('home_page','slide_menu'); set_motion(m,'step',250,2); set_motion(m,'step_fast',170,4)
step_ticks(m,w,(0,250,2),(50,170,4)); passed()
# A step's ms of 0 is an immediate move: no animator, a running one gone, the index there at once.
m=Machine(); w=m.page('home_page','slide_menu'); set_motion(m,'step',0,0); m.calls=[]
assert m.call(gap=500)==11 and not m.slides and m.get(w+O['SLIDE_ANIMATOR'])==0 and m.get(w+O['SLIDE_INDEX'])==1 and m.get(w+O['SLIDE_OFFSET'])==0
assert 'widget_animator_scroll_create' not in [c[0] for c in m.calls]
set_motion(m,'step',250,8); assert m.call(gap=500)==11 and slide(m,w)[3]==250
set_motion(m,'step_fast',0,0); assert m.call(gap=50)==11 and m.get(w+O['SLIDE_ANIMATOR'])==0 and m.get(w+O['SLIDE_OFFSET'])==0; passed()
# Reversal at either speed starts at the live position and actually travels backward,
# even when multiple intended destinations have accumulated.
for fast in (False,True):
    for key,reverse,sign in ((O['KEY_NEXT'],O['KEY_PREV'],1),(O['KEY_PREV'],O['KEY_NEXT'],-1)):
        m=Machine(); w=m.page('home_page','slide_menu'); m.call(key,gap=0)
        if fast:
            for _ in range(3): m.call(key,gap=20)
        m.advance(30); live=signed(m.get(w+O['SLIDE_OFFSET']))
        a=m.get(w+O['SLIDE_ANIMATOR']); m.call(reverse,gap=0)
        active,origin,goal,duration=slide(m,w)
        assert active==a and origin==live and duration==200 and (goal-live)*sign>0
        m.advance(200)
        assert m.get(w+O['SLIDE_INDEX'])==(-goal//240)%7 and not m.slides
        assert m.nodes[m.nodes[w]['children'][(-goal//240)%7]]['focused']==1
        passed()
# Exact fast-window boundary, stock rejection, and interaction resets.
for gap,want in ((200,120),(201,200)):
    m=Machine(); w=m.page('home_page','slide_menu'); m.call(gap=0); m.call(gap=gap)
    assert slide(m,w)[3]==want; passed()
for action in ('touch','center','click','leave','animating','pressed','unusable','missing'):
    m=Machine(); w=m.page('home_page','slide_menu'); home=m.top; m.call(gap=0)
    if action=='touch': m.call(address=HOOKS['on_wm_tsdown_before_fun'][0],gap=20)
    elif action=='center': m.release(20)
    elif action=='click': m.click(m.nodes[w]['children'][0],gap=20)
    elif action=='leave': m.page('playing_page'); m.call(gap=20); m.top=home
    elif action=='unusable':
        m.byte(syms['g_backlight_status'],0); m.call(gap=20); m.byte(syms['g_backlight_status'],1)
    elif action=='missing': m.call(args=(m.wm,0,0,0),gap=20)
    else: setattr(m,action,1); m.call(gap=20); setattr(m,action,0)
    assert m.call(gap=1)==11 and slide(m,w)[3]==200; passed()
# Stock's wheel lockout after Centre guards only its first LOCKOUT_KEEP x 100 ms, then clears.
m=Machine(); w=m.page('home_page','slide_menu'); m.call(gap=0)
m.byte(0xa4bbf8,8); assert m.call(O['KEY_PREV'],gap=20,debounce=True)==11
assert not m.moved(); m.byte(0xa4bbf8,0); m.call(gap=20); assert slide(m,w)[3]==120; passed()
m=Machine(); w=m.page('home_page','slide_menu'); m.call(gap=0)
m.byte(0xa4bbf8,8-O['LOCKOUT_KEEP']); m.call(gap=20)
assert m.u.mem_read(O['KEY_LOCKOUT'],1)==b'\0' and slide(m,w)[3]==120; passed()
# Animator and callback allocation failures settle through stock selection.
for failure in ('slide_fail','slide_on_fail'):
    m=Machine(); w=m.page('home_page','slide_menu'); setattr(m,failure,True)
    m.call(gap=0)
    assert m.get(w+O['SLIDE_INDEX'])==1 and not m.get(w+O['SLIDE_ANIMATOR'])
    assert not m.slides and not m.slide_callbacks
    assert m.nodes[m.nodes[w]['children'][1]]['focused']==1
    setattr(m,failure,False); m.call(gap=20); assert slide(m,w)[3]==200; passed()
# Centre arms the intended final icon while its slide is still unfinished.
m=Machine(); w=m.page('home_page','slide_menu')
for _ in range(5): m.call(gap=20)
m.release(0); m.advance(75); m.advance(O['DOUBLE_CLICK_MS']-75)
assert m.clicks==[m.nodes[w]['children'][5]] and not m.slides, (m.clicks,m.get(w+O['SLIDE_INDEX']),m.timers,m.slides); passed()
m=Machine(); w=m.page('home_page','slide_menu'); m.release(0); m.call(gap=20)
m.advance(300); assert not m.clicks; passed()
# Other slide menus retain stock next/previous handling.
m=Machine(); m.page('sysset_page','slide_menu')
assert m.call(gap=0)==11 and m.call(gap=1)==11 and m.moved(); passed()
# Even if the UI services a release before an overdue timer, both singles confirm once.
m=Machine(); w,es=m.page_list(3); m.release(); m.now+=O['DOUBLE_CLICK_MS']+1
assert m.release()==11 and m.clicks==[es[0]] and len(m.timers)==1
m.advance(300); assert m.clicks==[es[0],es[0]] and not m.screens; passed()
# Deadline expiry may navigate: the following release must resolve the new menu.
m=Machine(); w,es=m.page_list(3)
def navigate(a,b):
    m.on_click=None
    m.page_list(3,name='display_page')
m.on_click=navigate; m.release(); old=m.top
assert m.release(O['DOUBLE_CLICK_MS']+1)==11 and m.top!=old and len(m.clicks)==1
m.advance(300); assert len(m.clicks)==2 and m.clicks[0]!=m.clicks[1] and not m.screens; passed()
# The downstream stock cleanup still owns long-press and boot-key releases.
for addr in (syms['g_power_longkey'],syms['g_ingore_bootkey_flag'],O['BOOT_KEY_GUARD']):
    m=Machine(); m.page_list(3); m.byte(addr,1)
    assert m.release()==0
    m.advance(300)
    assert not m.clicks and not m.screens and not m.timers and m.u.mem_read(addr,1)==b'\0'; passed()
# Play/Pause reaches the real stock downstream handler, including on a supported page.
m=Machine(); m.page_list(3)
assert m.call(O['KEY_PLAY'])==0
m.call(O['KEY_PLAY'],address=syms['on_wm_keyup_fun'],gap=0)
assert any(c[0]=='playpause_quick_click' for c in m.calls) and not m.clicks; passed()
# A root-window paint observes leaving navigation even when the new page has no pane.
m=Machine(); w,es=m.page_list(3); m.release(); old=m.top
m.top=m.node('window','playing_page'); m.paint(m.top,gap=100)
m.top=old; m.advance(300); assert not m.clicks and not m.timers; passed()
m=Machine(); w=m.page('home_page','slide_menu'); home=m.top; m.call(gap=0)
m.top=m.node('window','playing_page'); m.paint(m.top,gap=20)
m.top=home; assert m.call(gap=1)==11 and m.moved(); passed()
# Local query and home selection changes invalidate pending confirmation too.
m=Machine(); w,rs,es=m.table_page(); m.release()
m.word(syms['g_class_type'],0xf002); m.advance(300); assert not m.clicks; passed()
m=Machine(); w=m.page('home_page','slide_menu')
m.word(w+O['SLIDE_INDEX'],0); m.nodes[w]['children']=[m.entry(w),m.entry(w)]
m.release(); m.word(w+O['SLIDE_INDEX'],1); m.advance(300); assert not m.clicks; passed()
# Unsigned milliseconds may wrap while confirmation is pending.
m=Machine(); m.now=0xfffffff0; m.page_list(3)
assert m.release()==11 and m.release(DC-1)==0
m.advance(300); assert not m.clicks and m.screens==[0]; passed()
# Short lists stay precise; virtual tables use total logical rows, not their row pool.
for table in (False,True):
    for count in (0,1,16,17):
        m=Machine()
        if table:
            # A dynamic page keeps hard ends, so this stays an acceleration check.
            w,rs,es=m.table_page(n=min(count,4),name='playerqueue_page',rebind=True)
            m.word(w+O['TABLE_ROWS'],count)
        else: w,es=m.page_list(count,extent=count*48)
        m.paint(w)
        if count==17:
            # Seventeen rows accelerate even at a 50ms cadence: the second tick is still one
            # row (50ms of spin), the third crosses 100ms and steps two. iPod's row lists wait
            # for 300ms of spin: six one-row ticks, then the seventh steps two.
            for want in (1,2,3,4,5,6,8):
                assert m.call(gap=50)==11 and m.selected(w)==want
            for _ in range(20): m.call(gap=50)
            assert m.selected(w)==count-1      # held at the end
            assert m.call(O['KEY_PREV'],gap=50)==11 and m.selected(w)==count-2 # ramp resets there
        else:
            for want in (1,2,3):
                assert m.call(gap=50)==11 and m.selected(w)==min(want,count-1)
        if count==16:
            for want in range(4,16):
                assert m.call(gap=50)==11 and m.selected(w)==want
            for want in range(14,-1,-1):
                assert m.call(O['KEY_PREV'],gap=50)==11 and m.selected(w)==want
            assert m.call(gap=50)==11 and m.selected(w)==1 # and at the start
        passed()

# Resizing across the short-list boundary cannot carry a previous fast run with it.
m=Machine(); w,es=m.page_list(17,height=960,extent=17*48)
m.nodes[w]['children']=es; m.paint(w)
for want in (1,2,3,5,7):  # iPod: two rows from 300ms of spin
    assert m.call(gap=100)==11 and m.selected(w)==want
# The new count's paint selects the row in view (OLED's whole rows leave the list scrolled); from there:
m.nodes[w]['children']=es[:16]; m.paint(w); at=m.selected(w)
for want in (1,2,3):  # a fresh one-row run on the short list
    assert m.call(gap=100)==11 and m.selected(w)==at+want
m.nodes[w]['children']=es; m.paint(w); at=m.selected(w)
for want in (1,2,3,5):  # and again after growing back
    assert m.call(gap=100)==11 and m.selected(w)==at+want
passed()

# Sustained ticks ramp one row per 100ms of same-direction spin up to eight rows, then hold;
# pauses, spacing past the 140ms window and reversal reset. Byte-exact boundaries, wrapped clock.
# iPod's row lists ramp gently (navigation.c LIST_FIRST_MS, LIST_RAMP_MS): one row until 300ms of
# spin, two from there and one more per further 200ms, eight from 1500ms. IPOD_SPIN is (gap since
# the last tick, rows per step then); every gap is inside the 140ms window, so the run is one.
IPOD_SPIN=((0,1),(100,1),(100,1),(99,1),(1,2),(100,2),(99,2),(1,3),(100,3),(99,3),(1,4),(100,4),(99,4),
           (1,5),(100,5),(99,5),(1,6),(100,6),(99,6),(1,7),(100,7),(99,7),(1,8),(100,8))
TICK_500=400_000  # a tick on OLED's set over this 500-row list runs past call()'s 100,000 instructions
for table in (False,True):
    for start in (0,0xfffffff0):
        m=Machine(); m.now=start
        w,rs=m.list_surface(table,n=500)
        if table: m.word(w+O['TABLE_ROWS'],500)
        # 99ms of spin is still one row; the next millisecond enters two-row speed, and each
        # further 100ms adds one row until the eight-row ceiling holds.
        seq=((0,1),(99,2),(1,4),(99,6),(1,9),(99,12),(1,16),(99,20),(1,25),(99,30),(1,36),
             (99,42),(1,49),(99,56),(1,64),(99,72),(1,80))
        # 299/300, 499/500 ... 1499/1500ms
        seq=[(gap,sum(g for _,g in IPOD_SPIN[:i+1])) for i,(gap,_) in enumerate(IPOD_SPIN)]
        for gap,want in seq:
            assert m.call(gap=gap,count=TICK_500)==11 and m.selected(w)==want,(table,gap,want,m.selected(w))
        # 140ms still counts as spinning, 141ms breaks the run and drops back to one row.
        assert m.call(gap=140,count=TICK_500)==11 and m.selected(w)==want+8
        assert m.call(gap=141,count=TICK_500)==11 and m.selected(w)==want+9
        assert m.call(O['KEY_PREV'],gap=1,count=TICK_500)==11 and m.selected(w)==want+8
        passed()
# Pixel-scroll fallback keeps the 100ms ramp, and immediate offsets.
m=Machine(); w=m.page(t='table_client')
for want in (48,144,288,480,720,1008,1344,1728):
    assert m.call(gap=100)==11 and m.get(w+O['TABLE_TOP'])==want
assert m.call(gap=141)==11 and m.get(w+O['TABLE_TOP'])==1776; passed()
def wiped(m,w):  # a destroyed surface recreated at the same address has no widget-owned props
    for k in [k for k in m.nodes[w] if k.startswith('_ringnav')]: del m.nodes[w][k]
def disturb(m,w,change,n):
    """One interruption of the n-row list w between wheel ticks; returns the live surface."""
    if change=='window': w,_=m.page_list(n,extent=n*48,name='allmusic_page')
    elif change=='pane':
        old=m.top; w,_=m.page_list(n,extent=n*48,name='allmusic_page'); m.nodes[old]['children']=[w]; m.top=old
    elif change=='scope': m.word(syms['g_class_type'],0xf002)
    elif change=='context': m.nodes[m.top]['name']='display_page'
    elif change=='count': m.nodes[w]['children'].pop()
    elif change=='recreated': wiped(m,w)
    elif change in ('gesture','animating'):
        field='pressed' if change=='gesture' else 'animating'
        setattr(m,field,1); assert m.call(gap=10)==11; setattr(m,field,0)
    elif change=='screen':
        m.byte(syms['g_backlight_status'],0); assert m.call(gap=10)==0; m.byte(syms['g_backlight_status'],1)
    elif change=='unsupported':  # the same tick is a volume step on Now Playing
        m.nodes[m.top]['name']='playing_page'; assert m.call(gap=10)==0; m.nodes[m.top]['name']='allmusic_page'
    elif change=='rejected': m.byte(0xa4bbf8,8); assert m.call(gap=10,debounce=True)==11
    elif change=='touch': m.call(address=HOOKS['on_wm_tsdown_before_fun'][0],event_type=O['EVT_POINTER_DOWN'],gap=10)
    elif change=='click': m.click(m.node('button'),gap=10)
    elif change=='missing': m.call(args=(0,0,0,0),gap=10)
    elif change=='centre': m.call(O['KEY_CENTER'],gap=10)
    elif change=='return': assert m.call(O['KEY_RETURN'],gap=10)==0
    elif change=='play': assert m.call(O['KEY_PLAY'],gap=10)==0
    elif change=='long': long_return(m)
    elif change=='reverse': assert m.call(O['KEY_PREV'],gap=10)==11 and m.selected(w)==1
    return w
# A fast spin belongs to its live menu, pane and browsing scope.
for change in ('window','pane','scope','context','count','gesture','screen','unsupported','touch','click','missing','centre'):
    m=Machine(); w,es=m.page_list(40,extent=40*48,
        name='sysset_page' if change=='context' else 'allmusic_page')
    for _ in range(7): m.call(gap=100)
    assert m.selected(w)==13  # iPod: 1,1,1,2,2,3,3 rows
    w=disturb(m,w,change,40)
    m.paint(w,gap=0)
    before=m.selected(w)
    assert m.call(gap=50)==11 and m.selected(w)==before+1,change
    passed()
# A tick while a page slides is held and plays into it once the slide ends, leaving no timer but OLED's scroll
# indicator's (list_marks.c sb_due, armed by each tick that moves the list); Centre or Return ends a held turn, and a
# held turn expires after HOLD_MS.
def hold_done(m): return all(t[1]==payload_syms['sb_due'] for t in m.timers.values())
m=Machine(); w,es=m.page_list(20,extent=960,name='allmusic_page'); m.paint(w)
assert m.call(gap=1000)==11 and m.selected(w)==1
m.animating=1; assert m.call(gap=1000)==11 and m.call(gap=20)==11 and m.selected(w)==1
m.advance(O['HOLD_POLL_MS']*3); assert m.selected(w)==1
m.animating=0; m.advance(O['HOLD_POLL_MS']); assert m.selected(w)==2
m.advance(O['HOLD_POLL_MS']); assert m.selected(w)==3 and hold_done(m); passed()
for key in ('KEY_RETURN','KEY_CENTER',None):
    m=Machine(); w,es=m.page_list(20,extent=960,name='allmusic_page'); m.paint(w)
    assert m.call(gap=1000)==11 and m.selected(w)==1
    m.animating=1; m.call(gap=1000)
    if key: m.call(O[key],gap=0)
    else: m.advance(O['HOLD_MS']+O['HOLD_POLL_MS'])
    m.animating=0; m.advance(1000); assert m.selected(w)==1 and hold_done(m),key; passed()
# Rejected stock wheel input resets a sustained list run.
m=Machine(); w,es=m.page_list(40,extent=40*48)
for _ in range(7): m.call(gap=100)
fast=13
assert m.selected(w)==fast
m.byte(0xa4bbf8,8)
assert m.call(gap=10,debounce=True)==11 and m.selected(w)==fast
assert m.call(gap=40)==11 and m.selected(w)==fast+1; passed()
# A click on a clickable child selects its collected ancestor, not a stale row.
m=Machine(); w=m.page(); m.word(w+O['W_H'],96)
row1=m.entry(w,0); row2=m.entry(w,96); deep=m.entry(row1,0)
m.nodes[row1]['children']=[deep]; m.nodes[w]['children']=[row1,row2]
m.paint(w); assert m.selected(w)==0
assert m.call(O['KEY_NEXT'])==11 and m.selected(w)==1
m.click(deep); assert m.selected(w)==0
m.click(m.node('button')); assert m.selected(w)==0; passed()
# Real canvas ABI, translation and clip code execute; only the LCD rectangle sink is mocked.
# A 20px row at canvas origin (7,20) under a (10,30)-(229,199) clip: OLED's bar (its ends list.selection.radius, under
# the row's half height) reaches the LCD with only its row pixels inside the clip.
want=[(x+7,y+20) for x,y,*_ in bar_bands(inset,0,240-2*inset,20,radius) if 10<=x+7<=229 and y+20>=30]
assert radius<20//2 and want
m=Machine(); m.config.update(FLAT); w=m.page(); m.word(w+O['W_H'],96)
e=m.entry(w,0); m.word(e+O['W_H'],20); m.nodes[w]['children']=[e]
sink='lcd_fill_rect'
for name in ('canvas_get_clip_rect','canvas_set_clip_rect',
             'canvas_fill_rect'):
    del m.handlers[syms[name]]
m.mock(sink)
m.word(m.lcd+0x3c,1); m.word(m.lcd+0xb0,240); m.word(m.lcd+0xb4,240)
m.word(m.canvas+O['CANVAS_X'],7); m.word(m.canvas+O['CANVAS_Y'],20)
for off,val in [(0x10,10),(0x14,30),(0x18,229),(0x1c,199)]: m.word(m.canvas+off,val)
m.paint(w)
assert [(c[2],c[3]) for c in m.calls if c[0]==sink]==want
assert [m.get(m.canvas+off) for off in (0x10,0x14,0x18,0x1c)]==[10,30,229,199]
assert m.lcd_colors()==LCD_COLORS
passed()
# Wheel selection is immediate even when restoration animations would still be running.
m=Machine(); w,es=m.page_list(6)
m.paint(w); m.glide=False
m.call(); m.call(); assert m.selected(w)==2 and m.get(w+O['SCROLL_Y'])==48  # whole rows (OLED's whole rows)
m.paint(w); assert m.selected(w)==2
assert m.confirm()==11 and m.dispatched()[0][1]==es[2]
m.call(); assert m.selected(w)==3
m.call(O['KEY_PREV']); assert m.selected(w)==2
m.call(O['KEY_PREV']); assert m.selected(w)==1 and m.get(w+O['SCROLL_Y'])==48  # its row already whole at the top: no move
m.call(O['KEY_PREV']); assert m.selected(w)==0 and m.get(w+O['VIEW_ANIMATOR'])==0; passed()
# Empty menus never activate or turn off the screen; touch doesn't swallow its first event.
m=Machine(); w=m.page(); assert m.confirm()==11 and not m.dispatched()
assert m.touch()==0 and not m.dispatched(); passed()
# Clip an oversized target to its surface without losing its selection.
m=Machine(); m.config.update(FLAT); w=m.page(); m.word(w+O['W_H'],96)
e=m.entry(w); m.word(e+O['W_H'],140); m.nodes[w]['children']=[e]
m.paint(w)
assert m.selected(w)==0 and m.sel()==(inset,0,240-2*inset,140)
assert {r['clip'] for r in m.rounded}|{b[5] for b in m.bands}=={(0,0,240,96)}; passed()
# Entering a tall row from either direction reveals its title, including after recreation.
m=Machine(); w,es=m.page_list(3,height=96,extent=400)
m.word(es[1]+O['W_H'],140); m.word(es[2]+O['W_Y'],188)
m.call(); assert m.selected(w)==1 and m.get(w+O['SCROLL_Y'])==48
m.call(); m.call(O['KEY_PREV'])
assert m.selected(w)==1 and m.get(w+O['SCROLL_Y'])==48
w,es=m.page_list(3,height=96,extent=400)
m.word(es[1]+O['W_H'],140); m.word(es[2]+O['W_Y'],188)
m.paint(w); assert m.selected(w)==1 and m.get(w+O['SCROLL_Y'])==48; passed()
# Row 0 brings back what heads the list above it (the EQ page's graph) when both fit; a head too tall for that
# leaves row 0 at the top, never pushed off.
for height,top in ((192,0),(144,0),(120,96)):
    m=Machine(); w,es=m.page_list(6,height=height,extent=96+6*48)
    for i,e in enumerate(es): m.word(e+O['W_Y'],96+i*48)
    m.paint(w)
    for _ in range(4): m.call()
    assert m.selected(w)==4 and m.get(w+O['SCROLL_Y'])>top
    for _ in range(4): m.call(O['KEY_PREV'])
    assert m.selected(w)==0 and m.get(w+O['SCROLL_Y'])==top; passed()

m=Machine(); w=m.page(); m.word(w+O['W_H'],96)
e=m.entry(w,0); m.word(e+O['W_W'],4); m.word(e+O['W_H'],4); m.nodes[w]['children']=[e]
assert m.paint(w)==0 and not m.drawn() and m.clip==(0,0,240,240); passed()
# A page with no entries paints nothing and leaves every saved property alone.
m=Machine(); w=m.page()
assert m.paint(w)==0 and m.selected(w)==-1
assert not m.drawn() and m.global_alpha==0 and m.clip==(0,0,240,240)
assert m.lcd_colors()==LCD_COLORS; passed()

# Every saved value is unusual: the rounded path restores fill, stroke and clip exactly and
# never touches the canvas or LCD global-alpha bytes.
m=Machine(); w,es=m.page_list(3,extent=1000)
m.word(m.lcd+O['LCD_FILL_COLOR'],0x00000001); m.word(m.lcd+O['LCD_STROKE_COLOR'],0x80ffffff)
m.byte(m.canvas+0x0e,0x7f); m.byte(m.lcd+0xe4,0x11)
m.paint(w)
assert m.get(m.lcd+O['LCD_FILL_COLOR'])==0x00000001 and m.get(m.lcd+O['LCD_STROKE_COLOR'])==0x80ffffff
assert m.u.mem_read(m.canvas+0x0e,1)[0]==0x7f and m.u.mem_read(m.lcd+0xe4,1)[0]==0x11
assert m.global_alpha==0 and m.clip==(0,0,240,240); passed()

# Taps choose their pane even with no initial owner or with both panes previously selected.
for seeded in ((),(0,),(0,1)):
    m=Machine(); a,b,ae,be=m.panes()
    nested=m.entry(be[1]); m.nodes[be[1]]['children']=[nested]
    for i in seeded: m.nodes[(a,b)[i]]['_ringnav_index']=0
    m.touch(); m.click(nested)
    assert m.selected(a)==-1 and m.selected(b)==1
    m.paint(a); assert not m.drawn()
    m.paint(b); assert not m.drawn()
    m.confirm(); assert m.dispatched()[0][1]==be[1]
    m.call(); assert m.selected(b)==2
    m.touch(); m.click(ae[0]); m.confirm()
    assert m.selected(b)==-1 and m.dispatched()[0][1]==ae[0]
    passed()

# An inactive pane is never painted, so the outline cannot appear on two panes at once.
m=Machine(); m.config.update(FLAT); a,b,_,_=m.panes(96); m.nodes[a]['_ringnav_index']=0
m.paint(b); assert not m.drawn()
m.paint(a); assert m.sel()==(inset,0,240-2*inset,48); passed()

# The real stock rounded wrappers run with canvas services mocked. This firmware declines with
# RET_FAIL when the canvas has no vgcanvas, and changes no fill/stroke color or alpha byte.
m=Machine(); m.probe_canvas(rect=(1,1,238,46))
assert m.call(address=syms['canvas_fill_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(RADIUS,))==2
assert m.call(address=syms['canvas_stroke_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(RADIUS,1))==2
assert m.lcd_colors()==LCD_COLORS
assert m.global_alpha==0 and not m.vg_calls; passed()

# With a vgcanvas present the same wrappers take the AGGE-facing path the Q2 firmware links:
# the color pointer and the radius/border-width stack slots reach the backend, canvas colors and
# the canvas alpha byte stay put. (The fill call then enters stock FP64 dither math that Unicorn's
# MIPS32 FR=0 FPU cannot execute; its vgcanvas calls up to that point are still asserted.)
m=Machine(); m.probe_canvas(vg=0x1000400,color=0xffffffff)
assert m.call(address=syms['canvas_stroke_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(RADIUS,2))==0
names=[c[0] for c in m.vg_calls]
assert 'vgcanvas_set_stroke_color' in names and 'vgcanvas_set_line_width' in names
assert [c[2]&0xffffffff for c in m.vg_calls if c[0]=='vgcanvas_set_stroke_color']==[0xffffffff]
assert [struct.unpack('<f',struct.pack('<I',c[2]&0xffffffff))[0] for c in m.vg_calls if c[0]=='vgcanvas_set_line_width']==[2.0]
assert m.lcd_colors()==LCD_COLORS
assert m.global_alpha==0 and m.u.mem_read(m.canvas+0x0e,1)[0]==0; passed()
m=Machine(); m.probe_canvas(vg=0x1000400)
try: m.call(address=syms['canvas_fill_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(RADIUS,))
except UcError: pass
assert [c[2] for c in m.vg_calls if c[0]=='vgcanvas_set_fill_color']==[FILL]
assert m.get(m.lcd+O['LCD_FILL_COLOR'])==0x9abcdef0 and m.global_alpha==0
assert not m.allocs; passed()

# CPU-LCD branch: the stock fill has a real rasterizer that Unicorn can run end to end. With a
# non-color LCD type it fills the rounded rect through LCD sinks, frees every allocation, and
# declines radius <= 2 exactly as the stock code does.
m=Machine(); m.probe_canvas(lcd_type=0)
m.mock('lcd_fill_rect','lcd_draw_hline','lcd_draw_vline',prefix='sink:')
assert m.call(address=syms['canvas_fill_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(RADIUS,))==0
ops=[c for c in m.calls if c[0].startswith('sink:')]
assert ops and all(10<=signed(c[2])<=109 and 10<=signed(c[3])<=49 for c in ops)
assert not m.allocs
assert m.call(address=syms['canvas_fill_rounded_rect'],args=(m.canvas,m.rect,0,m.color),stack=(2,))==2
assert not [c for c in m.calls if c[0].startswith('sink:')]; passed()
# The actual stock filter runs first, including each screen-off lock mode.
for backlight in [0,1]:
    for mode in range(4):
        for key in [170,O['KEY_PLAY'],O['KEY_PREV'],O['KEY_NEXT'],O['KEY_CENTER'],222,223,42]:
            results=[]
            for patched in [False,True]:
                m=Machine(patched); m.page('playing_page')
                m.byte(syms['g_backlight_status'],backlight)
                m.byte(syms['g_keylock_flag'],1); m.byte(syms['g_keylock_mode'],mode)
                results.append(m.call(key))
            assert results[0]==results[1],(backlight,mode,key,results)
            passed()
# Non-ring keys on supported pages must pass through unchanged.
for key in [0,13,170,O['KEY_PLAY'],222,223,0xffffffff]:
    m=Machine(); m.page(); assert m.call(key)==0 and not m.moved(); passed()
# A tap on a recreated table must not recall/rebind its target before native delivery.
for pointer_down in (False,True):
    for virtual in (False,True):
        m=Machine()
        if virtual: w,rs,es=m.table_page(n=20)
        else: w,es=m.page_list(20)
        m.paint(w)
        for _ in range(12): m.call()
        if virtual: w,rs,es=m.table_page(rebind=True)
        else: w,es=m.page_list(20)
        off=O['TABLE_TOP'] if virtual else O['SCROLL_Y']
        if pointer_down:
            m.touch()
            assert m.get(w+off)==0 and not m.moved(), 'pointer-down recalled selection'
            m.pressed=1; m.paint(w)
            assert m.get(w+off)==0 and not m.moved(), 'paint recalled under pointer'
            m.pressed=0
        def check_tapped_row(a,b):
            if virtual: assert m.get(rs[0]+O['ROW_INDEX'])==0, 'tap rebound'
        m.on_click=check_tapped_row
        m.click(es[0])
        assert m.selected(w)==0 and m.get(w+off)==0 and m.clicks==[es[0]]
        assert not m.moved(); passed()

# Saturate before adding: large valid tables cannot wrap their viewport or logical selection.
for pooled in (False,True):
    m=Machine(); w,rs,es=m.table_page(n=1 if pooled else 0,name='playerqueue_page')
    m.word(w+O['ROW_HEIGHT'],1); m.word(w+O['TABLE_ROWS'],0x7fffffff)
    m.word(w+O['W_H'],1); m.word(w+O['TABLE_TOP'],0x7ffffffe)
    if pooled:
        m.word(rs[0]+O['ROW_INDEX'],0x7ffffffe)
        m.word(rs[0]+O['W_Y'],0x7ffffffe); m.word(es[0]+O['W_H'],1)
    m.paint(w)
    for _ in range(9):
        assert m.call(gap=50)==11 and m.get(w+O['TABLE_TOP'])==0x7ffffffe
        if pooled: assert m.selected(w)==0x7ffffffe
    passed()

# A pages widget inside a navigation surface exposes only its active child's targets.
for active in (-1,0,1,2):
    m=Machine(); w=m.page()
    tabs=m.node('pages',active=active); m.word(tabs+O['W_PARENT'],w)
    es=[m.entry(tabs) for _ in range(2)]
    m.nodes[tabs]['children']=es; m.nodes[w]['children']=[tabs]
    m.paint(w); m.confirm()
    assert m.clicks==([es[active]] if active in (0,1) else []),active
    passed()

# An overdue confirmation can change power state before the next release is processed.
for flag in ('g_backlight_status','g_power_longkey','g_ingore_bootkey_flag'):
    m=Machine(); w,es=m.page_list(3)
    m.on_click=lambda a,b: m.byte(syms[flag],0 if flag=='g_backlight_status' else 1)
    m.release(); m.now+=O['DOUBLE_CLICK_MS']+1
    assert m.call(O['KEY_CENTER'],gap=0)==0,flag
    assert m.clicks==[es[0]] and not m.timers
    passed()

# Reject a missing key event before calling the stock filter, cancelling pending input.
m=Machine(); w,es=m.page_list(3); m.release()
assert m.call(args=(0,0,0,0),gap=0)==0
m.advance(300); assert not m.clicks and not m.timers; passed()

# Any native click supersedes delayed confirmation, even without a pointer-down callback.
for target_kind in ('same', 'nested', 'outside'):
    m=Machine(); w,es=m.page_list(3); m.release()
    target=es[0] if target_kind=='same' else m.entry(es[0]) if target_kind=='nested' else m.node('button')
    m.click(target,gap=100)
    m.advance(300)
    assert m.clicks==[target] and not m.timers,target_kind
    passed()

# A native activation also ends the prior wheel gesture when no pointer-down was delivered.
for home in (False,True):
    m=Machine(); w,es=m.page_list(40,extent=1920)
    if home:
        m.nodes[m.top]['name']='home_page'; m.nodes[w]['type']='slide_menu'
        m.word(w+O['SLIDE_INDEX'],0)
    m.call(gap=0)
    if not home: m.call(gap=50)
    m.click(es[0 if home else 2],gap=50)
    assert m.call(gap=50)==11
    if home: assert slide(m,w)[3]==200
    else: assert m.selected(w)==3
    passed()

# Centre consumes a touch interruption once; its second release must not stop the recall
# glide again and replace the armed row with a different visible row.
m=Machine(); w,es=m.page_list(10); m.paint(w)
for _ in range(5): m.call()
w,es=m.page_list(10); m.touch(); m.glide=False
assert m.release()==11 and m.selected(w)==5
assert m.release(100)==0 and m.screens==[0]
m.advance(300)
assert not m.clicks and not m.timers
passed()

# Rejecting an expired confirmation must not restore or consume the new scope's recall.
for virtual in (False,True):
    m=Machine()
    if virtual: w,rs,es=m.table_page(n=20)
    else: w,es=m.page_list(20,name='allmusic_page')
    off=O['TABLE_TOP'] if virtual else O['SCROLL_Y']
    m.paint(w)
    for _ in range(12): m.call()
    m.word(syms['g_class_type'],2); m.word(w+off,0); m.paint(w); m.release()
    before=dict(m.nodes[w])
    m.word(syms['g_class_type'],0xf001); m.advance(300)
    assert not m.clicks and not m.moved() and m.get(w+off)==0
    assert m.nodes[w]==before, 'rejected confirmation changed live selection properties'
    m.paint(w)
    assert m.selected(w)==12 and m.get(w+off)==528  # a whole row under the top (OLED's whole rows)
    passed()

# Touch mode survives settling, unsupported pages, recreation and native clicks anywhere.
for virtual in (False,True):
    for click_only in (False,True):
        m=Machine()
        w,rs=m.list_surface(virtual)
        m.paint(w); assert m.drawn()
        if click_only: m.click(m.node('button'))
        else: m.touch()
        assert any(c[0]=='widget_invalidate_force' and c[1]==m.top for c in m.calls)
        off=O['TABLE_TOP'] if virtual else O['SCROLL_Y']
        anim=O['TABLE_ANIMATOR'] if virtual else O['VIEW_ANIMATOR']
        m.word(w+off,110)
        if virtual: m.bind(rs,110)
        m.word(w+anim,0x1234)
        m.paint(w); assert not m.drawn()
        m.word(w+anim,0); m.paint(w)
        assert m.selected(w)==3 and not m.drawn()
        old=m.top; m.page('playing_page'); m.touch(); m.call()
        m.top=old; m.paint(w); assert not m.drawn()
        # Returning to a recreated list preserves remembered selection, still without drawing.
        w,rs=m.list_surface(virtual)
        m.paint(w); assert m.selected(w)==3 and not m.drawn()
        m.confirm(); m.paint(w); assert m.drawn()  # centre-generated click stays visible
        m.touch(); m.pressed=1; m.call(); m.paint(w)
        assert not m.drawn()  # rejected wheel does not restore drawing
        m.pressed=0; m.call(); m.paint(w); assert m.drawn()
        passed()
# Boundary wheel turns restore drawing and cancel momentum even without selection movement.
for virtual in (False,True):
    m=Machine()
    w,rs=m.list_surface(virtual,rebind=False)
    m.paint(w); m.touch()
    anim=O['TABLE_ANIMATOR'] if virtual else O['VIEW_ANIMATOR']
    m.word(w+anim,0x1234)
    assert m.call(O['KEY_PREV'])==11 and m.selected(w)==0 and m.get(w+anim)==0
    m.paint(w); assert m.drawn()
    passed()
# Touch on a page without a supported pane also hides the next page's outline.
m=Machine(); m.page('playing_page'); m.touch(); w,es=m.page_list(3)
m.paint(w); assert not m.drawn()
m.call(O['KEY_PLAY']); m.paint(w); assert not m.drawn()
m.call(); m.paint(w); assert m.drawn(); passed()

# Immediate table rebinding replaces widgets, not just their indices. Centre resolves new rows.
m=Machine(); w,rs,es=m.table_page(); m.glide=False
old=list(es)
def replace_pool(a,offset):
    global rs,es
    rs=[m.node('table_row') for _ in range(4)]
    es=[m.entry(r) for r in rs]
    m.nodes[a]['children']=rs
    for r,e in zip(rs,es):
        m.nodes[r]['children']=[e]; m.word(r+O['W_PARENT'],a)
    m.bind(rs,offset)
    for e in old: m.nodes.pop(e,None)
m.rebind=replace_pool
m.word(w+O['TABLE_ANIMATOR'],0x1234)
m.call(); m.call()
assert m.selected(w)==2 and m.get(w+O['TABLE_TOP'])==48  # whole rows (OLED's whole rows)
assert m.get(w+O['TABLE_ANIMATOR'])==0
m.confirm(); assert m.clicks==[es[1]] and m.clicks[0] not in old
passed()

# Every list ends at its ends: a detent past either one leaves the selection, the bar and the view
# where they are, however long the pause before it (a spin, a run's gap, a second). No bump, no wrap.
PAST_END=(100,0,300,301,1000,2000)
for name in ('playlist_page','display_page','playerqueue_page',
             'playermore_page','sortselect_dialog','playset_page','sysset_page'):
    m,w,es=walk(6,5,height=96,extent=288,name=name,flat=True); assert m.get(w+O['SCROLL_Y'])==192
    for gap in PAST_END:
        assert m.call(gap=gap)==11 and m.selected(w)==5 and m.get(w+O['SCROLL_Y'])==192,(name,gap,m.selected(w))
        m.paint(w,gap=0); assert m.sel()==(inset,48,240-2*inset,48),(name,gap,m.sel())   # on its row
    for _ in range(5): assert m.call(O['KEY_PREV'],gap=100)==11
    assert m.selected(w)==0 and m.get(w+O['SCROLL_Y'])==0
    for gap in PAST_END:
        assert m.call(O['KEY_PREV'],gap=gap)==11 and m.selected(w)==0 and m.get(w+O['SCROLL_Y'])==0,(name,gap)
        m.paint(w,gap=0); assert m.sel()==(inset,0,240-2*inset,48),(name,gap,m.sel())
    passed()

# Virtual music tables end on their last logical row.
m=Machine(); w,rs,es=m.table_page(n=4,rebind=True)
m.word(w+O['W_H'],96); m.word(w+O['TABLE_ROWS'],20)
m.paint(w)
for _ in range(19): assert m.call()==11
assert m.selected(w)==19 and m.get(w+O['TABLE_TOP'])==864
for gap in PAST_END:
    assert m.call(gap=gap)==11 and m.selected(w)==19 and m.get(w+O['TABLE_TOP'])==864
passed()

# The Library's Albums grid (album_page, 0xf003) and an album's songs (0xff10) on the same page
# both end at their ends.
for cls in (0xf003,0xff10):
    m=Machine(); m.word(syms['g_class_type'],cls)
    w,es=m.page_list(6,height=96,extent=288,name='album_page'); m.paint(w)
    for _ in range(5): assert m.call()==11
    for gap in PAST_END: assert m.call(gap=gap)==11 and m.selected(w)==5,(cls,gap,m.selected(w))
    passed()
m=Machine(); m.config.update(FLAT); w,es=m.page_list(1,height=96,extent=96,name='playlist_page')
m.paint(w)
for _ in range(3): assert m.call(gap=100)==11 and m.selected(w)==0
m.paint(w,gap=0); assert m.sel()==(inset,0,240-2*inset,48)
passed()

# Run the stock list callback and thumb setter: immediate wheel moves must sync the value.
for populated in (False,True):
    m=Machine(); w,es=m.page_list(20 if populated else 0,height=96,extent=960)
    bar=m.node('scroll_bar_m'); m.word(bar+0x78,1000)  # native bar range
    parent=m.node('list_view',children=[w,bar])
    m.word(parent+0x84,w); m.word(parent+0x88,bar)
    m.word(w+O['W_PARENT'],parent); m.nodes[m.top]['children']=[parent]
    m.word(w+O['VIEW_ON_SCROLL'],0x5fef00)  # installed by stock list_view
    m.word(w+0x74,syms['g_scroll_view_vtable'])
    m.handlers.pop(syms['scroll_view_set_offset'])
    m.mock('scroll_bar_cast','scroll_bar_is_mobile','widget_set_opacity','widget_set_visible_only',
           'widget_dispatch_simple_event')
    m.paint(w)
    for key in [O['KEY_NEXT']]*20+[O['KEY_PREV']]*20:
        assert m.call(key)==11
        offset=m.get(w+O['SCROLL_Y'])
        assert m.get(bar+O['BAR_VALUE'])==int(offset/864*1000)
        events=[c[2] for c in m.calls if c[0]=='widget_dispatch_simple_event' and c[1]==w]
        if m.moved(): assert events[-2:]==[O['EVT_SCROLL'],O['EVT_SCROLL_END']]
    assert m.get(w+O['SCROLL_Y'])==m.get(bar+O['BAR_VALUE'])==0
    passed()

# A wheel tick leaves stock's mobile bar asleep: the theme draws its own indicator (list_marks.c wheel_sb_alpha), and
# woken (scroll_bar_scroll_to at its own value) stock's runs a 300 ms wait and a 500 ms opacity fade as a widget
# animator, its invisible column repainted every loop pass, ~45 frames a tick (docs/internals.md#song-rows-cost).
for kind in ('scroll_view','table_client'):
    m=Machine()
    w,es=m.page_list(20,extent=960) if kind=='scroll_view' else (lambda t:(t[0],t[2]))(m.table_page(20))
    bar=m.node('scroll_bar_m',visible=0); m.byte(bar+0x8d,1); m.word(bar+O['BAR_VALUE'],17)
    parent=m.node('list_view' if kind=='scroll_view' else 'table_view',children=[w,bar])
    m.word(w+O['W_PARENT'],parent); m.nodes[m.top]['children']=[parent]
    m.handlers.pop(syms['scroll_bar_scroll_to'])
    m.mock('scroll_bar_cast','scroll_bar_is_mobile','widget_set_opacity','widget_set_sensitive',
           'widget_set_visible_only','widget_animator_prop_create','widget_animator_prop_set_params')
    m.paint(w); m.call(gap=0); m.call(gap=0)
    assert not m.get(bar+0x98) and not m.nodes[bar]['visible']  # no animator, still hidden
    assert not any(c[0] in ('scroll_bar_scroll_to','widget_animator_prop_create') for c in m.calls)
    passed()
for count,bar_present in ((2,True),(20,False)):
    m=Machine(); w,es=m.page_list(count,height=96,extent=count*48)
    parent=m.node('list_view',children=[w]+([m.node('scroll_bar_m')] if bar_present else []))
    m.word(w+O['W_PARENT'],parent); m.paint(w); m.call()
    assert not any(c[0]=='scroll_bar_scroll_to' for c in m.calls)
    passed()

