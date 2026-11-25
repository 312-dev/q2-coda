class AppMachine(BrowseMachine):
    """BrowseMachine with a card app in Spotify's place: on the card, its socket, its row, its page."""
    app,manifest,page_name='soulseek','name=Soulseek\nsearch=Search Soulseek\nqr=0\n','addon_page'
    def __init__(self,files=None):
        self.bin,self.browse=f'/mnt/mmc/.{self.app}/q2-{self.app}',f'/tmp/q2-{self.app}.browse.sock'
        super().__init__(files); card_app(self,self.app,self.manifest)
        self.row_text=re.search(r'name=(.*)',self.manifest)[1]
    def add_on_row(self): return stream_rows(self)[self.row_text]
    def gone(self):
        """Its row, then its binary gone from the card before the press."""
        self.button=self.add_on_row(); self.files.pop(self.bin)
# Streaming: the card apps after Spotify, by name, each with its icon and no name (stock's row
# click never sees it); a folder without its binary or its manifest, or an id that can't be one (it
# goes into shell commands), is no app.
m=AppMachine(); card_app(m,'filebrowser','name=File Browser\nqr=1\n'); card_app(m,'deezer','name=Deezer\n')
card_app(m,'gone','name=Gone\n'); m.files.pop('/mnt/mmc/.gone/q2-gone'); card_app(m,'Bad_Id','name=Bad\n')
m.tree['/mnt/mmc']+=[('.spotify',4),('Music',4)]; m.files['/mnt/mmc/.spotify/librespot']=bytearray()
# Its own icon when it ships one: the rows' 40 px, else the 52 px one (addon_scan reads each one's size).
for f in ('/mnt/mmc/.deezer/icon.png','/mnt/mmc/.deezer/icon-40.png','/mnt/mmc/.soulseek/icon.png'): m.files[f]=bytearray(png_head(40,40))
rows=stream_rows(m)
assert list(rows)==['Spotify','Deezer','File Browser','Soulseek','Tidal Downloads','Internet Radio','AirPlay','Configuration'],list(rows)
icons={'Deezer':'file:///mnt/mmc/.deezer/icon-40.png',
       'Soulseek':'file:///mnt/mmc/.soulseek/icon.png','File Browser':'stream_app'}
for t,want in icons.items():
    icon=m.nodes[rows[t]]['children'][0]; assert m.nodes[icon]['image']==want and not m.nodes[rows[t]].get('name'),(t,m.nodes[icon]['image'])
passed()
# Capabilities (addon.c addon_scan, docs/internals.md): every Streaming scan writes /tmp/q2-addon.caps, the
# card-app features this firmware has as words on one line, a set; a stale file is written over.
CAPS='/tmp/q2-addon.caps'
m=AppMachine(); stream_rows(m); caps=bytes(m.files[CAPS])
assert caps.endswith(b'\n') and caps.count(b'\n')==1,caps
assert set(caps.split())=={b'playlist',b'progress',b'pages',b'library',b'goto',b'sleep',b'game',b'audio',b'sections',b'art',b'window',b'urls',b'sort',b'field',b'more',b'fav',b'now',b'catalogue',b'ratings',b'likes',b'artist',b'album',b'playlists',b'storage',b'signin',b'libsource',b'ahead',b'radio',b'status',b'holdmenu',b'clip'},caps
m.files[CAPS]=bytearray(b'stale\n'); stream_rows(m); assert m.files[CAPS]==caps; passed()
# Card games (addon.c, q2app game=1): Library's Games lists them, Streaming doesn't. A row runs the binary
# over the screen; each input event reaches its socket as a text datagram instead of the UI (a held Return
# ends it), but the music's keys stay stock's; the wheel's raw travel goes once a poll, and its end gives
# the wheel back.
CONFIG['KEEPLIT']='0'; m=AppMachine(); del CONFIG['KEEPLIT']  # Smart Awake off: the idle quit below
card_app(m,'fidget','name=Buddy\ngame=1\n'); assert 'Buddy' not in stream_rows(m)
def fn(name,*args): return m.call(address=payload_syms[name],args=(args+(0,0,0,0))[:4],gap=0,clear=False,count=5_000_000)
assert fn('addon_games')==1 and m.text(fn('addon_game_name',0))=='Buddy' and fn('addon_game_on')==0
m.forked=0; m.execs=[]; fn('addon_game_start',0); assert m.execs[-1][:2]==('/mnt/mmc/.fidget/q2-fidget',)*2  # the child
assert m.env[0]=='Q2_POPUP' and m.env[1].split()[0]=='modern' and m.env[2]==1  # the theme's pop-up (games.c popup_env: modern, the one style sent), set before its exec
m.forked=4243; assert fn('addon_game_start',0)==1 and fn('addon_game_on')==4243 and m.get(payload_syms['wheel_raw'])==1
inp=HOOKS['window_manager_dispatch_input_event'][0]; m.handlers[inp+12]='stock_input'; GS='/tmp/q2-fidget.input.sock'
def game_event(kind,key=0,x=0,y=0,stock=False):
    m.calls=[]; m.sent=[]; m.word(m.event+O['EVENT_X'],x); m.word(m.event+O['EVENT_Y'],y)
    m.call(x or key,address=inp,args=(m.wm,m.event,0,0),event_type=kind,gap=0,clear=False)  # EVENT_KEY is EVENT_X's word
    assert ('stock_input' in [c[0] for c in m.calls])==stock,(kind,key)
    return [(t[1],t[5]) for t in m.sent]
assert game_event(O['EVT_KEY_UP'],O['KEY_CENTER'])==[('k u 218',GS)] and game_event(O['EVT_KEY_DOWN'],O['KEY_RETURN'])==[('k d 170',GS)]
assert game_event(O['EVT_KEY_UP'],O['KEY_RETURN'])==[('k u 170',GS)]  # let go: not a hold
for k in ('KEY_PLAY','KEY_BACK_BTN','KEY_FWD_BTN'):
    for kind in ('EVT_KEY_DOWN','EVT_KEY_UP'): assert game_event(O[kind],O[k],stock=True)==[],(k,kind)
for kind,want in ((O['EVT_POINTER_DOWN']-1,'d'),(O['EVT_POINTER_MOVE_BEFORE']-1,'m'),(O['EVT_POINTER_UP_BEFORE']-1,'u'),(O['EVT_POINTER_ABORT'],'u')):
    assert game_event(kind,x=120,y=88)==[(f't {want} 88 199',GS)],kind  # the panel's (x, y) turned into the view's
m.word(payload_syms['wheel_travel'],0xffffffdb); m.sent=[]; m.waited=0; fn('addon_poll')
assert [t[1] for t in m.sent]==['w -37'] and m.get(payload_syms['wheel_travel'])==0 and fn('addon_game_on')==4243
def poll(m):  # one pass: how many kills it sent (their text's stack buffer is reused by then)
    m.calls=[]; m.call(address=payload_syms['addon_poll'],args=(0,0,0,0),gap=0,clear=False,count=5_000_000)
    return [c[0] for c in m.calls].count('system')
# Return held 700 ms ends it, timed here from its down (stock makes the long press after this hook); one
# that ignores the TERM is KILLed 3 s on; the held Return's release, after it has gone, stays out of Games.
assert game_event(O['EVT_KEY_DOWN'],O['KEY_RETURN'])==[('k d 170',GS)]
for _ in range(12): m.now+=50; game_event(O['EVT_KEY_DOWN'],O['KEY_RETURN'])  # its repeats: still the first down's hold
assert poll(m)==0
m.now+=101; assert poll(m)==1 and poll(m)==0  # 701: the stamps are odd (| 1), 0 being none
m.now+=3000; assert poll(m)==1
m.waited=4243; poll(m); assert fn('addon_game_on')==0 and m.get(payload_syms['wheel_raw'])==0
assert game_event(O['EVT_KEY_DOWN'],O['KEY_RETURN'])==[] and game_event(O['EVT_KEY_UP'],O['KEY_RETURN'])==[]
assert game_event(O['EVT_KEY_UP'],O['KEY_RETURN'],stock=True)==[]
# Released before then: the game's Return, nothing ended.
m.waited=0; assert fn('addon_game_start',0)==1
game_event(O['EVT_KEY_DOWN'],O['KEY_RETURN']); m.now+=300; assert game_event(O['EVT_KEY_UP'],O['KEY_RETURN'])==[('k u 170',GS)]
m.now+=1000; assert poll(m)==0
# A press stamped on an even millisecond rounds up (| 1), a poll in that same millisecond is before it: no hold.
m.now+=m.now&1; game_event(O['EVT_KEY_DOWN'],O['KEY_RETURN']); assert poll(m)==0
game_event(O['EVT_KEY_UP'],O['KEY_RETURN'])
# Ended by a hold on an even millisecond, its TERM gets its time before any KILL.
m.now+=2; game_event(O['EVT_KEY_DOWN'],O['KEY_RETURN']); m.now+=702; assert poll(m)==1
m.now+=2999; assert poll(m)==0; m.now+=3; assert poll(m)==1
m.waited=4243; poll(m); game_event(O['EVT_KEY_UP'],O['KEY_RETURN'],stock=False); m.waited=0; assert fn('addon_game_start',0)==1
# Two minutes without input end it (TERM), counted from the last input, unless Smart Awake (IPOD KEEPLIT)
# holds it open and lit.
m.now+=90_000; game_event(O['EVT_KEY_UP'],O['KEY_CENTER']); m.now+=60_000; assert poll(m)==0
m.now+=60_000; assert poll(m)==1
m.waited=4243; poll(m); assert fn('addon_game_on')==0
def idle(m):
    m.now+=120_000; m.waited=0; return poll(m)>0
CONFIG['KEEPLIT']='1'; a=AppMachine(); del CONFIG['KEEPLIT']; card_app(a,'fidget','name=Buddy\ngame=1\n'); a.forked=4243
assert a.call(address=payload_syms['addon_games'],args=(0,0,0,0),gap=0,clear=False,count=5_000_000)==1
assert a.call(address=payload_syms['addon_game_start'],args=(0,0,0,0),gap=0,clear=False,count=5_000_000)==1
assert not idle(a) and not idle(a) and any(c[0]=='reset_poweroptions_timer' for c in a.calls); passed()
# A card game with the sound (q2app audio=1): the output is handed over before the fork as for a video (now_hold,
# the jack readied by dac_take) and the binary gets q2video's arguments: the device, and on Bluetooth or USB the
# volume (h: the DAC's own); Play/Pause and the side buttons reach it; the DAC stays on and nothing ends it for
# idling.
g=AppMachine(); g.handlers.pop(syms['mclGetPlayStatus'],None); g.word(0xa50084,2); g.mock('player_save_stop','player_save_start')  # the Library playing
card_app(g,'groovebox','name=Groovebox\ngame=1\naudio=1\n'); GB='/mnt/mmc/.groovebox/q2-groovebox'; GS2='/tmp/q2-groovebox.input.sock'
def gn(name,*args): return g.call(address=payload_syms[name],args=(args+(0,0,0,0))[:4],gap=0,clear=False,count=5_000_000)
def start(way):
    g.way=way; g.forked=0; g.execs=[]; g.calls=[]; gn('addon_game_start',0)  # the child
    return [c[0] for c in g.calls]
assert gn('addon_games')==1 and gn('addon_game_audio')==0  # scanned: the game is listed, none runs
g.word(syms['g_dacoff_time'],0xffffffff); g.byte(syms['g_headset_output'],1); g.byte(syms['g_volume'],42); names=start(0)
take=[n for n in names if n in ('player_save_stop','config_outputchannel','mclSetPcmMode','mclSetMute','device_set_volume','fork')]
assert take==['player_save_stop','config_outputchannel','mclSetPcmMode','mclSetMute','device_set_volume','fork'] and g.execs[-1]==(GB,GB,'plughw:0,0','',0)
names=start(1); assert 'player_stop' not in names and 'config_outputchannel' not in names  # the song still held and g.execs[-1]==(GB,GB,'plug:bluealsa','42',0)
g.byte(syms['g_usbvol_mode'],0); g.word(O['USB_MIXER'],0xfffffffe); start(2); assert g.execs[-1]==(GB,GB,'usb','h42',0)
g.way=0; g.forked=4243; assert gn('addon_game_start',0)==1 and gn('addon_game_on')==4243 and gn('addon_game_audio')==1
g.handlers[inp+12]='stock_input'
def gevent(kind,key):
    g.calls=[]; g.sent=[]; g.word(g.event+O['EVENT_X'],0); g.word(g.event+O['EVENT_Y'],0)
    g.call(key,address=inp,args=(g.wm,g.event,0,0),event_type=kind,gap=0,clear=False)
    assert 'stock_input' not in [c[0] for c in g.calls],(kind,key)
    return [(t[1],t[5]) for t in g.sent]
assert gevent(O['EVT_KEY_DOWN'],O['KEY_PLAY'])==[('k d 171',GS2)] and gevent(O['EVT_KEY_UP'],O['KEY_PLAY'])==[('k u 171',GS2)]
assert gevent(O['EVT_KEY_DOWN'],O['KEY_BACK_BTN'])==[('k d 222',GS2)] and gevent(O['EVT_KEY_UP'],O['KEY_FWD_BTN'])==[('k u 223',GS2)]
assert gevent(O['EVT_KEY_LONG'],O['KEY_PLAY'])==[]  # eaten: no Play-hold action of stock's
g.word(syms['g_dacoff_time'],5); g.now+=120_001; g.waited=0; assert poll(g)==0 and g.get(syms['g_dacoff_time'])==0
g.now+=120_001; assert poll(g)==0 and any(c[0]=='reset_poweroptions_timer' for c in g.calls)
g.waited=4243; g.calls=[]; poll(g); assert gn('addon_game_on')==0 and gn('addon_game_audio')==0
assert [c[1] for c in g.calls if c[0]=='player_save_start']==[1]; passed()  # the held song back, paused (now_release)
# Stock's Baidu Netdisk row (a Chinese cloud drive; list_baidu's icon) is gone from Streaming, destroyed; its
# TIDAL row stays first.
m=AppMachine(); view=m.node('scroll_view','scroll_view_streamsset')
def stock_row(icon,label):
    item,b=m.node('list_item'),m.node('button'); m.nodes[b]['children']=[m.node('image',image=icon),m.node('hscroll_label',text=label)]
    m.nodes[item]['children']=[b]; return item
tidal,baidu=stock_row('list_tidal','TIDAL'),stock_row('list_baidu','Baidu Netdisk'); m.nodes[view]['children']=[tidal,baidu]
m.top=m.node('window','stream_page',[view])
assert m.call(address=HOOKS['stream_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
kids=m.nodes[view]['children']; assert kids[0]==tidal and baidu not in kids and m.nodes[baidu].get('destroyed') and len(kids)==7,len(kids); passed()
# Streaming's Configuration: after Stop Streaming Services, a switch row per service Streaming lists
# (stock's TIDAL, Spotify, the card apps, Tidal Downloads, Internet Radio, AirPlay), all on when
# Q2POD STREAMHIDE is missing. Centre switches one off and hides its Streaming row at once, again shows
# it; card apps are kept by id, the others by a fixed upper-case key. Configuration always shows.
m=AppMachine(); card_app(m,'deezer','name=Deezer\n'); rows=stream_rows(m); stream=m.top
m.nodes[m.wm]=dict(type='window_manager',name='',children=[stream],visible=1)
view=m.find('scroll_view',stream)
def streaming():
    kids=m.nodes[view]['children']
    return [m.nodes[kids[0]]['visible']]+[m.nodes[m.nodes[m.nodes[r]['children'][0]]['children'][1]]['text'] for r in kids[1:] if m.nodes[r]['visible']]
assert streaming()==[1,'Spotify','Deezer','Soulseek','Tidal Downloads','Internet Radio','AirPlay','Configuration']
m.click(rows['Configuration']); m.page=m.top; assert m.nodes[m.page]['name']=='streamconf_page'
SVC=['Deezer','Soulseek','Internet Radio','AirPlay']  # the lone switches: TIDAL's, Spotify's and Tidal Downloads' are on their pages
assert m.labels()[:11]==['Configuration','Stop Streaming Services','Download Ahead','Services','TIDAL','Spotify','Show in Streaming',*SVC]
cap=m.rows()[5]; assert len(m.nodes[cap]['children'])==1 and not m.nodes[cap].get('handlers')  # the section header: no press
def ticks(): return [switch(m,i)=='switch_on' for i in range(6,len(m.rows()))]  # Shanling's switches
assert ticks()==[True]*4
m.row(6); assert m.config['STREAMHIDE']=='deezer' and ticks()==[False,True,True,True]
assert streaming()==[1,'Spotify','Soulseek','Tidal Downloads','Internet Radio','AirPlay','Configuration']
conf=m.page; m.row(8); m.row(3); sub=m.page=m.top; m.row(0); m.page=conf  # TIDAL's on its page
assert m.config['STREAMHIDE']=='deezer,RADIO,TIDAL' and m.labels().count('Hidden')==1  # its row on Configuration
assert streaming()==[0,'Spotify','Soulseek','Tidal Downloads','AirPlay','Configuration']
m.row(6); assert m.config['STREAMHIDE']=='RADIO,TIDAL' and ticks()==[True,True,False,True]
assert streaming()==[0,'Spotify','Deezer','Soulseek','Tidal Downloads','AirPlay','Configuration']
m.row(8); m.page=sub; m.row(0); m.page=conf; assert m.config['STREAMHIDE']=='' and ticks()==[True]*4; passed()
# Saved: the next Streaming leaves them out, an app by its id whatever its place by name.
m=AppMachine(); card_app(m,'deezer','name=Deezer\n'); m.config['STREAMHIDE']='soulseek,AIRPLAY,SPOTIFY'
rows=stream_rows(m); view=m.find('scroll_view',m.top)
assert streaming()==[1,'Deezer','Tidal Downloads','Internet Radio','Configuration']
m.click(rows['Configuration']); m.page=m.top; assert ticks()==[True,False,True,False] and m.labels().count('Hidden')==1; passed()
# Soulseek: q2-soulseek from the card's .soulseek folder.
SBIN,SBROWSE,SSTATE,SPID='/mnt/mmc/.soulseek/q2-soulseek','/tmp/q2-soulseek.browse.sock','/tmp/q2-soulseek.state','/tmp/q2-soulseek'
class SlskMachine(AppMachine): pass
SMENU=[('remote','','Phone Remote','192.168.1.23:8080','',''),('search','','Search','Online as alice','',''),
       ('list','downloads','Downloads','None yet','',''),('action','set','Sharing: Off','Nothing is shared','sharing',''),
       ('action','stop','Stop Soulseek','','','')]
SLABELS=['Soulseek','Phone Remote','192.168.1.23:8080','Search','Online as alice','Downloads','None yet','Sharing: Off',
         'Nothing is shared','Stop Soulseek']  # a row with no subtitle is one line, no empty second label
# A card app's levels don't slide: OLED's motion.inplace is 0 (theme.py MOTION_ROLES gives it no macro), so
# page_push moves nothing. The next level's rows, Return's and a refresh show at x 0 at once, no animator made.
m=SlskMachine(); m.mock('widget_animator_prop_create','widget_animator_prop_set_params','widget_animator_start')
m.open(); m.answer('ok','refresh','',SMENU); assert m.x_pushes()==[]
m.pick(2); assert m.labels()==SLABELS and m.x_pushes()==[]  # its level asked for under the loading overlay: the menu stays
m.answer('ok','','',[('info','','No downloads yet','','','')]); assert m.x_pushes()==[],m.x_pushes()
lv=m.get(m.find('scroll_view',m.page)+O['W_PARENT']); assert signed(m.get(lv+O['W_X']))==0
t=len(m.threads); m.back(); assert m.labels()==SLABELS and len(m.threads)==t+1 and m.x_pushes()==[]  # the menu it kept, asked again
m.answer('ok','refresh','',SMENU); assert m.x_pushes()==[]
m.advance(2000); m.answer('ok','refresh','',[*SMENU[:2],('list','downloads','Downloads','1 downloading','',''),*SMENU[3:]])
assert '1 downloading' in m.labels() and m.x_pushes()==[]; passed()
# Sections (addon.c sec_take, swipe; docs/internals.md): a first reply whose message is "sections" makes
# its list rows the sections (other kinds left out), and the first opens at once in its place, titled
# with its name and position, no push. A sideways swipe on it opens the next (to the left) or the previous (to the
# right), in place as any level under OLED (no motion.inplace: page_push moves nothing); none past the ends. The swipe's moves and lift are the
# page's (STOP), the rows told the touch is gone once; a short one, an up-and-down drag or a touch on a
# deeper level changes nothing, and Back from a deeper level returns to the section.
SECS=[('list','downloads','Downloads','','',''),('info','','Not a section','','',''),('list','shares','Shared','','bob',''),
      ('list','search','Search','','','')]
def sec_touch(m,kind,x,y):
    f,ctx=m.handler(m.page,O[kind]); m.word(m.event+O['EVENT_Y'],y)
    return m.call(key=x,address=f,args=(ctx,m.event,0,0),event_type=O[kind],gap=0,clear=False)
def sec_swipe(m,dx,dy=0):
    """A touch down at (200, 100), a move halfway and a lift at dx, dy on: the move's and the lift's answers."""
    sec_touch(m,'EVT_POINTER_DOWN',200,100)
    got=sec_touch(m,'EVT_POINTER_MOVE_BEFORE',200+dx//2,100+dy//2),sec_touch(m,'EVT_POINTER_UP_BEFORE',200+dx,100+dy)
    m.advance(0,clear=False); return got
def sec_aborts(m):  # the rows told the touch is gone: an abort event made, and sent down the page
    return [c[3] for c in m.calls if c[0]=='pointer_event_init' and c[2]==O['EVT_POINTER_ABORT']],[c[1] for c in m.calls if c[0]=='widget_dispatch_event_to_target_recursive']
m=SlskMachine(); m.mock('widget_animator_prop_create','widget_animator_prop_set_params','widget_animator_start','widget_dispatch_event_to_target_recursive')
m.open(); m.answer('ok','sections','',SECS); assert m.requests[-1]==('menu',) and m.labels()==['Loading…']
m.answer('ok','','',[('info','','No downloads yet','','','')]); assert m.requests[-1]==('downloads',) and m.labels()[:2]==['Downloads 1/3','No downloads yet'] and m.x_pushes()==[]; passed()
m.calls=[]; assert sec_swipe(m,-100)==(11,11) and sec_aborts(m)==([m.page],[m.page]) and m.labels()[:2]==['Downloads 1/3','No downloads yet']  # under the overlay
m.answer('ok','','',[('list','share','Music','','',''),('info','','Films','','','')]); assert m.requests[-1]==('shares','bob') and m.labels()[:2]==['Shared 2/3','Music'] and m.x_pushes()==[]
sec_swipe(m,-100); m.answer('ok','','',[('info','','Find files','','','')]); assert m.labels()[:2]==['Search 3/3','Find files'] and m.x_pushes()==[]
asked=len(m.threads); assert sec_swipe(m,-100)==(11,11) and len(m.threads)==asked and m.labels()[0]=='Search 3/3'  # past the last: nothing
sec_swipe(m,100); m.answer('ok','','',[('list','share','Music','','',''),('info','','Films','','','')])
assert m.requests[-1]==('shares','bob') and m.labels()[:2]==['Shared 2/3','Music'] and m.x_pushes()==[]; passed()
asked=len(m.threads)
assert sec_swipe(m,-50)==(11,11) and len(m.threads)==asked  # taken (25 px sideways), too short at the lift
m.calls=[]; assert sec_swipe(m,-70,100)==(0,0) and len(m.threads)==asked and sec_aborts(m)==([],[])  # the list's
m.pick(0); m.answer('ok','','',[('info','','Song','','','')]); assert m.labels()[:2]==['Music','Song']
asked=len(m.threads); assert sec_swipe(m,-100)==(0,0) and len(m.threads)==asked and m.labels()[0]=='Music'
m.back(); m.answer('ok','','',[('list','share','Music','','','')]); assert m.requests[-1]==('shares','bob') and m.labels()[:2]==['Shared 2/3','Music']; passed()
# The previous before the first: nothing. An app whose first reply has no "sections" never swipes.
sec_swipe(m,100); m.answer('ok','','',[('info','','No downloads yet','','','')]); asked=len(m.threads)
assert sec_swipe(m,100)==(11,11) and len(m.threads)==asked and m.labels()[0]=='Downloads 1/3'
m=SlskMachine(); m.open(); m.answer('ok','','',SMENU); asked=len(m.threads); assert sec_swipe(m,-100)==(0,0) and len(m.threads)==asked and m.labels()==SLABELS; passed()
# Sections are taken once: a section that answers "sections" as well shows its rows as a list, not asked again.
m=SlskMachine(); m.open(); m.answer('ok','sections','',SECS); asked=len(m.threads); m.answer('ok','sections','',SECS)
assert m.requests[-1]==('downloads',) and len(m.threads)==asked and m.labels()[:2]==['Downloads 1/3','Downloads'],m.labels(); passed()
# Gone from the card since Streaming listed it: the page says so and nothing starts.
m=SlskMachine(); m.gone(); m.open()
assert m.labels()==["Soulseek isn't on the card",'Install it again in System settings → App Store'] and not m.systems and not m.threads
m.row(0); assert not m.threads; m.poll(); assert not m.systems; m.close(); passed()
# With it: started, the menu asked; no socket yet is the add-on starting, asked again; its rows.
m=SlskMachine(); m.open(); assert m.systems==['/bin/sh /mnt/mmc/.soulseek/run &'] and m.labels()==['Loading…']
m.worker(); assert m.labels()==['Starting Soulseek…']; m.advance(500)
m.answer('ok','refresh','',SMENU); assert m.requests[-1]==('menu',) and m.labels()==SLABELS; passed()
# A live level is asked again every 2 s while on top; the same rows aren't rebuilt, and changed texts in the
# same rows are written in place (relabel), their selected row kept.
rows=m.rows(); m.nodes[m.find('scroll_view')]['_ringnav_index']=2; m.advance(2000); m.worker()
assert len(m.requests)==2 and m.requests[-1]==("menu",) and m.rows()==rows
changed=[*SMENU[:2],('list','downloads','Downloads','1 downloading','',''),*SMENU[3:]]
m.advance(2000); m.answer('ok','refresh','',changed)
assert m.rows()==rows and m.labels()[6]=='1 downloading' and m.nodes[m.find('scroll_view')]['_ringnav_index']==2,(m.labels(),m.nodes[m.find('scroll_view')].get('_ringnav_index'))
# A row losing its detail changes the rows: rebuilt.
gone=[*SMENU[:2],('list','downloads','Downloads','','',''),*SMENU[3:]]; m.advance(2000); m.answer('ok','refresh','',gone)
assert m.rows()!=rows and 'None yet' not in m.labels() and '1 downloading' not in m.labels(),m.labels(); passed()
# A list row opens its level; Back asks for the menu again, the row kept.
m.pick(2); m.answer('ok','refresh','',[('info','','No downloads yet','Search, then press a file','','')])
assert m.requests[-1]==('downloads',) and m.labels()==['Downloads','No downloads yet','Search, then press a file'] and not m.rows()  # OLED's empty state: no row to press
m.back(); m.answer('ok','refresh','',changed)
assert m.requests[-1]==('menu',) and m.nodes[m.find('scroll_view')]['_ringnav_index']==2,(m.requests[-1],m.nodes[m.find('scroll_view')].get('_ringnav_index'))
# An action row sends its op with its context, toasts the reply's message and asks for the level again.
m.pick(3); m.answer('ok','Sharing: While charging','',[])
assert m.requests[-1]==('set','sharing') and m.toasts[-1][3]=='Sharing: While charging',(m.requests[-1],m.toasts[-1:])
m.answer('ok','refresh','',SMENU); assert m.requests[-1]==('menu',) and m.labels()==SLABELS; passed()
# A progress row (kind progress, position permille): its title left and subtitle right on one line over a
# flat 8 px bar the caption's width, square, no knob: a dim track, then the accent's light tone filled
# position/1000 of it from the left; 1500 or a number beyond a long draws full, a negative or unparsable one
# or one beyond a long's negative empty (strtol saturates, so the clamp is defined); a press does nothing,
# nor does a held Play/Pause. (A title's % comes escaped, %25, as any Q2SPOT/1 field's.)
PROG=[('progress','','47%25','112 of 240 songs','','470'),('progress','','Done','','','1500'),('progress','','Queued','','','x'),('progress','','Behind','','','-50'),
      ('progress','','Huge','','','99999999999999999999'),('progress','','Deep','','','-99999999999999999999')]
m.pick(2); m.answer('ok','refresh','',PROG); assert m.labels()[:3]==['Downloads','47%','112 of 240 songs'],m.labels()
def prog_bar(i): return next(v for v in m.nodes if m.nodes[v]['type']=='view' and m.get(v+O['W_PARENT'])==m.rows()[i])
def prog_paint(i): m.rounded.clear(); m.bands.clear(); m.call(address=HOOKS['widget_on_paint_border'][0],args=(prog_bar(i),m.canvas,0,0)); return [b[:4] for b in m.bands]
x,w=24,375-2*24  # the detail's box, OLED's page.rows: text_x 24 from each side, detail_y 31, detail_h 20
assert cf_geometry(m,prog_bar(0))==(x,31+(20-8)//2,w,8),cf_geometry(m,prog_bar(0))  # centred in it (progress_row)
assert prog_paint(0)==[(0,0,w,8),(0,0,w*470//1000,8)] and not m.rounded and m.bands[0][4]==0xff3a3a3a!=m.bands[1][4],(m.bands,m.rounded)
assert prog_paint(1)==[(0,0,w,8),(0,0,w,8)] and prog_paint(2)==prog_paint(3)==[(0,0,w,8)] and prog_paint(4)==prog_paint(1) and prog_paint(5)==prog_paint(2) and not m.rounded,(m.bands,m.rounded)
# A live refresh with the same rows moves the bar and the texts in place, the rows kept.
PROG2=[('progress','','51%25','122 of 240 songs','','510'),*PROG[1:]]; prog_rows=m.rows()
m.advance(2000); m.answer('ok','refresh','',PROG2)
assert m.rows()==prog_rows and m.labels()[:3]==['Downloads','51%','122 of 240 songs'] and prog_paint(0)==[(0,0,w,8),(0,0,w*510//1000,8)],m.labels()
asked=len(m.threads); m.pick(0); assert len(m.threads)==asked
m.top=m.page; m.status=m.alloc(0x200); m.nodes[m.find('scroll_view')]['_ringnav_index']=0; m.press(80); assert m.hold()!=11 and len(m.threads)==asked
m.back(); m.answer('ok','refresh','',SMENU); passed()
# Covers (addon.c art_rows, art_row): a row's cover is /tmp/q2-<id>.art/<key>.jpg, key the FNV-1a 32 of its URI, a
# tab and its context, in 8 lowercase hex digits, at most 128 px either way. One cover in a list draws all its rows as stock Tidal's: 78 px
# high, the 64 px cover (scaled to fit) at the theme's left, the title over the subtitle beside it, a row without
# a file keeping the room empty and a row that opens a page its chevron. Without a file for any row (another
# row's key is no match), or with a progress row, the list is as before.
def art_file(uri,ctx):
    h=2166136261
    for c in f'{uri}\t{ctx}'.encode(): h=((h^c)*16777619)&0xffffffff
    return f'/tmp/q2-soulseek.art/{h:08x}.jpg'
ART_JPEG=bytes.fromhex('ffd8ffc0001108004000400301')+bytes(16)  # all picture_size reads
COVERS=[('list','album','One','An Artist','al-1','playalbum'),('local','/mnt/mmc/.soulseek/Two.flac','Two','','','')]
def covers(files=(),rows=COVERS,msg='',cursor=''):
    c=SlskMachine(); c.open(); c.answer('ok','refresh','',SMENU)
    for f in files: c.files[f]=bytearray(ART_JPEG)
    c.pick(2); c.answer('ok',msg,cursor,rows); return c
def heights(c): return [c.get(r+O['W_H']) for r in c.rows()]
def cover_images(c): return [k for r in c.rows() for k in c.nodes[r]['children'] if c.nodes[k].get('image','').startswith('file://')]
# (A list without covers has page_h(64) rows, OLED's page.rows.detail_height, 52, for a row with a subtitle, and a
# one-line row, 32, for one without: stack_rows.)
for files in ((),(art_file('album','al-2'),)):
    c=covers(files); assert c.labels()==['Downloads','One','An Artist','Two'] and heights(c)==[52,32] and not cover_images(c),(c.labels(),heights(c))
c=covers((art_file('album','al-1'),),[COVERS[0],('progress','','Half','','','500')])
assert heights(c)==[52,52] and not cover_images(c); passed()
c=covers((art_file('album','al-1'),)); x=24; tx=x+76; plain=covers()  # x: page_x, OLED's page.rows.text_x; tx: x + ART_PX + 12
assert c.labels()==['Downloads','One','An Artist','Two',''],c.labels()
assert c.nodes[c.get(c.find('scroll_view')+O['W_PARENT'])]['item_height']==78 and heights(c)==[78,78] and [c.get(r+O['W_Y']) for r in c.rows()]==[0,78]
title,detail,img,_=c.nodes[c.rows()[0]]['children']; cap,det=[box(plain,k) for k in plain.nodes[plain.rows()[0]]['children'][:2]]; dy=(78-heights(plain)[0])//2
assert c.nodes[img]['image']=='file://'+art_file('album','al-1') and c.nodes[img]['draw_type']==4 and box(c,img)==[x,7,64,64],box(c,img)
assert box(c,title)==[tx,cap[1]+dy,316-tx,cap[3]] and box(c,detail)==[tx,det[1]+dy,316-tx,det[3]] and chevron(c,0),(box(c,title),box(c,detail))
two,_=c.nodes[c.rows()[1]]['children']; assert box(c,two)==[tx,0,375-x-tx,78],box(c,two); passed()
# Its later pages (page_next) join it as 78 px rows, the trailing Loading… one too, the rows shown kept.
more=[(*COVERS,*[('info','',f'Row {i}','','','') for i in range(8)]),[('info','','Last','','','')]]
c=covers((art_file('album','al-1'),),more[0],'pages','c1'); rows=c.rows()
assert c.labels()[-2]=='Loading…' and heights(c)==[78]*11,(c.labels()[-3:],heights(c))
c.answer('ok','pages','',more[1]); assert c.requests[-1]==('downloads','c1') and c.rows()[:10]==rows[:10] and c.labels()[-2]=='Last'
assert heights(c)==[78]*11 and c.get(c.find('scroll_view')+O['VIEW_CONTENT_H'])==11*78 and len(cover_images(c))==1; passed()
# Search: the T9 box, its OK opening "search" for the text, escaped.
m.pick(1); edit=m.find('edit'); assert m.nodes[edit]['tips']=='Search Soulseek'
m.nodes[edit]['text']='aphex\ttwin'; f,ctx=m.handler(edit,O['EVT_VALUE_CHANGED'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0; m.advance(0,clear=False)
m.answer('ok','refresh','50',[('action','download','01 Xtal.flac','bob · FLAC 16/44.1 · 31 MB · free','12.3','downloadfolder')])
assert m.wire[-1].endswith(b'\tsearch\taphex%09twin\n'),m.wire[-1]
assert m.labels()==['aphex\ttwin','01 Xtal.flac','bob · FLAC 16/44.1 · 31 MB · free','More…'],m.labels()
# A press downloads the file; Play/Pause held, the hold op (its whole folder); More… the next page.
m.pick(0); m.answer('ok','Downloading 01 Xtal.flac','',[]); assert m.requests[-1]==('download','12.3') and m.toasts[-1][3]=='Downloading 01 Xtal.flac'
m.answer('ok','refresh','50',[('action','download','01 Xtal.flac','bob','12.3','downloadfolder')])
m.top=m.page; m.status=m.alloc(0x200); m.nodes[m.find('scroll_view')]['_ringnav_index']=0; m.press(80)
assert m.hold()==11; m.answer('ok','Downloading the folder','',[]); assert m.requests[-1]==('downloadfolder','12.3') and m.toasts[-1][3]=='Downloading the folder'
m.answer('ok','refresh','50',[('action','download','01 Xtal.flac','bob','12.3','downloadfolder')])
m.pick(1); m.answer('ok','','100',[('action','download','02 Second.flac','bob','12.4','downloadfolder')]); assert m.requests[-1]==('search','aphex%09twin','50'),m.requests[-1]
# More… again: the list's own arguments and the newest cursor alone, Q2SPOT/1's last argument (to 1.4.1 each
# cursor followed the ones before, "search aphex 50 100", which q2-deezer read as the second page again).
def more_row(m):  # the More… row's place among the rows, by its words
    def texts(w): return [m.nodes[w].get('text'),*(t for c in m.nodes[w]['children'] for t in texts(c))]
    return next(i for i,r in enumerate(m.rows()) if 'More…' in texts(r))
m.pick(more_row(m)); m.answer('ok','','',[]); assert m.requests[-1]==('search','aphex%09twin','100'),m.requests[-1]
m.back(); m.answer('ok','','100',[('info','','Second','','','')]); assert m.requests[-1]==('search','aphex%09twin','50'); passed()
# Full lists (addon.c page_next): a reply whose message is "pages" (the app opts in) and that has a cursor is
# followed by its next pages, each asked with the list's own arguments and the newest cursor (as More… asks),
# their rows added to the same list under a Loading… row that does nothing, the rows on screen, the selected row
# and the scroll left as they are, until no cursor is left. Any other reply with a cursor keeps More… (above), a
# live one as well: its 2 s refresh asks the first page alone.
def arts(k,n=50): return [('list','artist',f'Artist {k*50+i:03}','',f'ar-{k*50+i}','playartist') for i in range(n)]
s=SlskMachine(); s.open(); s.answer('ok','refresh','',SMENU); s.pick(2); s.answer('ok','pages','c1',arts(0))
view=s.find('scroll_view'); rows=s.rows(); assert len(rows)==51 and s.labels()[-1:]==['Loading…'],s.labels()[-2:]
asked=len(s.threads); s.pick(50); assert len(s.threads)==asked
s.nodes[view]['_ringnav_index']=7; s.word(view+O['SCROLL_Y'],6*32); y=s.get(view+O['SCROLL_Y'])  # row 7 on screen (one-line 32 px rows: no subtitles)
s.answer('ok','pages','c2',arts(1)); assert s.requests[-1]==('downloads','c1') and s.rows()[:50]==rows[:50] and len(s.rows())==101,s.requests[-1]
assert s.nodes[view]['_ringnav_index']==7 and s.get(view+O['SCROLL_Y'])==y and s.get(view+O['VIEW_CONTENT_H'])==101*32
s.answer('ok','pages','',arts(2,20)); assert s.requests[-1]==('downloads','c2') and len(s.rows())==120 and s.labels()[-1:]==['Artist 119']
t=len(s.threads); s.advance(2000); assert len(s.threads)==t; passed()
# A page landing while another window is on top leaves ringnav's list state (its load) to that window's list:
# the rows, count and selection are kept for when the page is on top again.
s=SlskMachine(); s.open(); s.answer('ok','refresh','',SMENU); s.pick(2); s.answer('ok','pages','c1',arts(0))
view=s.find('scroll_view'); s.nodes[view]['_ringnav_index']=7; s.word(s.page+O['W_PARENT'],s.wm)
other=s.node('window','playing_page'); s.word(other+O['W_PARENT'],s.wm); s.top=other
loads=[]; s.u.hook_add(UC_HOOK_CODE,lambda u,a,n,_: loads.append(a),begin=payload_syms['load'],end=payload_syms['load'])
s.answer('ok','pages','c2',arts(1)); assert not loads and len(s.rows())==101
assert s.nodes[view]['_ringnav_count']==101 and s.nodes[view]['_ringnav_index']==7; passed()
# A press on Loading… stays nothing when its page lands before the press's timer runs (go() then finds a row at
# its index): the page's tick takes the reply first, the press's timer (if any) held back till after.
s=SlskMachine(); s.open(); s.answer('ok','refresh','',SMENU); s.pick(2); s.answer('ok','pages','c1',arts(0))
s.nodes[s.find('scroll_view')]['_ringnav_index']=50; before=s.next_timer; f,ctx=s.handler(s.rows()[50],O['EVT_CLICK'])
assert s.call(address=f,args=(ctx,s.event,0,0),gap=0,count=5_000_000)==0
for g in [g for g in s.timers if g>=before]: s.timers[g]=(s.now+1000,*s.timers[g][1:])
s.serve=lambda op,args:('ok','pages','',arts(1)); worker,arg=s.threads[-1]; assert s.call(address=worker,args=(arg,0,0,0),gap=0,count=5_000_000)==0
t=len(s.threads); s.advance(500); assert len(s.rows())==100; s.advance(1000)
assert len(s.threads)==t and len(s.rows())==100 and s.labels()[1]=='Artist 000',(len(s.threads)-t,s.labels()[:3]); passed()
# Play/Pause held on an appended row sends its hold op; the level is asked again from its first page (the action
# may change it) and the row comes back once its page is there, as after Back from a row's level.
s.top=s.page; s.status=s.alloc(0x200); s.nodes[view]['_ringnav_index']=70; s.press(80); assert s.hold()==11
s.answer('ok','Playing Artist 070','',[]); assert s.requests[-1]==('playartist','ar-70'),s.requests[-1]
s.answer('ok','pages','c1',arts(0)); view=s.find('scroll_view'); assert s.nodes[view]['_ringnav_index']==50
s.answer('ok','pages','c2',arts(1)); assert s.nodes[view]['_ringnav_index']==70; s.answer('ok','pages','',arts(2,20))
s.pick(105); s.answer('ok','','',[]); s.back()  # every page it kept, at once, its row selected, the first page asked again
assert len(s.rows())==120 and s.nodes[s.find('scroll_view')]['_ringnav_index']==105
rows=s.rows(); s.answer('ok','pages','c1',arts(0)); t=len(s.threads)  # as it was: nothing redrawn or paged again
assert s.requests[-1]==('downloads',) and s.rows()==rows and s.nodes[s.find('scroll_view')]['_ringnav_index']==105
s.advance(2000); assert len(s.threads)==t; passed()
# Without "pages" (every app before it: q2-deezer, q2-soulseek, q2-filebrowser) a reply with a cursor keeps
# More… and nothing more is asked; a later page without it ends the list at More… as well.
s.back(); s.answer('ok','refresh','',SMENU); s.pick(2); t=len(s.threads); s.answer('ok','','c1',arts(0))
assert len(s.threads)==t and len(s.rows())==51 and s.labels()[-1:]==['More…'],s.labels()[-2:]
s.back(); s.answer('ok','refresh','',SMENU); s.pick(2); s.answer('ok','pages','c1',arts(0)); t=len(s.threads); s.answer('ok','','c2',arts(1))
assert s.requests[-1]==('downloads','c1') and len(s.threads)==t and len(s.rows())==101 and s.labels()[-1:]==['More…'],s.labels()[-2:]; passed()
# At ADDON_PAGES replies (500 rows, under ringnav's 512) the list stops at More…, which asks for the rest as a
# level of its own; a page that fails ends the list at More… too, its rows kept, nothing asked again. (A fresh
# machine: the mock's memory isn't freed.)
s=SlskMachine(); s.open(); s.answer('ok','refresh','',SMENU); s.pick(2)
for k in range(10): s.answer('ok','pages',f'c{k+1}',arts(k))
assert len(s.rows())==501 and s.labels()[-1:]==['More…'] and s.requests[-1]==('downloads','c9'); t=len(s.threads); s.advance(2000); assert len(s.threads)==t
s.pick(500); s.answer('ok','pages','c11',arts(10,30)); assert s.requests[-1]==('downloads','c10')
s.answer('error','Server down','',[]); assert s.requests[-1]==('downloads','c11') and len(s.rows())==31 and s.labels()[-1:]==['More…']
t=len(s.threads); s.advance(2000); assert len(s.threads)==t; passed()
# Windows (caps word "window", addon.c page_to): a page row shows the level's own list from its context's
# cursor in the level's place, not as a level over it. Next page selects the row after Previous page,
# Previous page the list's last row once its pages are in, and Back leaves the list (the menu asked again).
s=SlskMachine(); s.open(); s.answer('ok','refresh','',SMENU); s.pick(2)
s.answer('ok','pages','c1',arts(0)); s.answer('ok','','',[*arts(1,49),('page','next','Next page','','c200','')])
assert len(s.rows())==100 and s.labels()[-1:]==['Next page'],s.labels()[-2:]  # a plain row, its subtitle empty: one line
s.pick(99); assert len(s.rows())==100 and s.labels()[-1:]==['Next page'],s.labels()  # under the loading overlay
s.answer('ok','pages','c250',[('page','prev','Previous page','','c0',''),*arts(4,49)]); assert s.requests[-1]==('downloads','c200'),s.requests[-1]
assert s.labels()[:4]==['Downloads','Previous page','Artist 200','Artist 201'] and s.nodes[s.find('scroll_view')]['_ringnav_index']==1,(s.labels()[:4],s.nodes[s.find('scroll_view')]['_ringnav_index'])
s.answer('ok','','',arts(5,10)); assert s.requests[-1]==('downloads','c250') and len(s.rows())==60
s.pick(0); assert len(s.rows())==60 and s.labels()[1]=='Previous page'
s.answer('ok','pages','c1',arts(0)); assert s.requests[-1]==('downloads','c0'),s.requests[-1]; s.answer('ok','','',[*arts(1,49),('page','next','Next page','','c200','')])
assert s.nodes[s.find('scroll_view')]['_ringnav_index']==99,s.nodes[s.find('scroll_view')]['_ringnav_index']
s.back(); s.answer('ok','refresh','',SMENU); assert s.requests[-1]==('menu',) and s.labels()==SLABELS; passed()
# In a list drawn with covers a page row is a row of that list: 78 px, its caption centred, the cover's room empty.
page_c=covers((art_file('album','al-1'),),[COVERS[0],('page','next','Next page','','c2','')]); page_tx=24+76  # page_x + ART_PX + 12
assert heights(page_c)==[78,78] and [page_c.get(r+O['W_Y']) for r in page_c.rows()]==[0,78] and len(cover_images(page_c))==1,(heights(page_c),cover_images(page_c))
page_t=page_c.nodes[page_c.rows()[1]]['children'][0]; assert page_c.nodes[page_t]['text']=='Next page' and box(page_c,page_t)==[page_tx,0,375-24-page_tx,78],box(page_c,page_t); passed()
# A page kept holds its own size, its bytes and their NUL: realloc'd before its fields are cut (the mock moves it
# and spoils the old block), not the request's 64 KB.
s=SlskMachine(); s.open(); s.answer('ok','refresh','',SMENU); s.pick(2); s.resized.clear(); s.answer('ok','pages','c1',arts(0)); s.answer('ok','pages','',arts(1,3))
assert [k[:9] for k in s.resized]==[b'Q2SPOT/1\n']*2 and s.resized[0].endswith(b'\tar-49\tplayartist\n\0') and s.resized[1].endswith(b'\tar-52\tplayartist\n\0'),s.resized
assert s.labels()[1]=='Artist 000' and s.labels()[-1:]==['Artist 052'],s.labels(); passed()
# An empty last page (offset paging: 50 rows and a cursor, then none) leaves the selection on the last row, not
# past the end where the Loading… row was.
s=SlskMachine(); s.open(); s.answer('ok','refresh','',SMENU); s.pick(2); s.answer('ok','pages','c1',arts(0))
view=s.find('scroll_view'); s.nodes[view]['_ringnav_index']=50; s.answer('ok','pages','',[])
assert s.requests[-1]==('downloads','c1') and len(s.rows())==50 and s.labels()[-1:]==['Artist 049']
assert s.nodes[view]['_ringnav_index']==49,s.nodes[view]['_ringnav_index']; passed()
# A reload's row comes back once: a user who moved off the Loading… row meanwhile (to row 10), then onto a later
# one (row 100), stays there as its page comes, not pulled on toward the old row (120).
s=SlskMachine(); s.open(); s.answer('ok','refresh','',SMENU); s.pick(2)
for k in range(3): s.answer('ok','pages',f'c{k+1}' if k<2 else '',arts(k))
view=s.find('scroll_view'); s.top=s.page; s.status=s.alloc(0x200); s.nodes[view]['_ringnav_index']=120; s.press(80); assert s.hold()==11
s.answer('ok','Playing Artist 120','',[]); s.answer('ok','pages','c1',arts(0)); view=s.find('scroll_view'); assert s.nodes[view]['_ringnav_index']==50
s.nodes[view]['_ringnav_index']=10; s.answer('ok','pages','c2',arts(1)); assert s.nodes[view]['_ringnav_index']==10
s.nodes[view]['_ringnav_index']=100; s.answer('ok','pages','',arts(2)); assert s.nodes[view]['_ringnav_index']==100,s.nodes[view]['_ringnav_index']; passed()
# A first page shorter than the screen (page_list sized its view to it) is rebuilt when the next page comes: a new
# list as tall as the screen allows, the selection kept.
s=SlskMachine(); s.open(); s.answer('ok','refresh','',SMENU); s.pick(2); s.answer('ok','pages','c1',arts(0,2))
view=s.find('scroll_view'); h=s.get(view+O['W_H']); s.nodes[view]['_ringnav_index']=1; s.answer('ok','pages','',arts(1))
new=s.find('scroll_view'); assert new!=view and s.get(new+O['W_H'])>h==3*52 and len(s.rows())==52 and s.nodes[new]['_ringnav_index']==1,(h,s.get(new+O['W_H'])); passed()
# A list's first row of kind search stays a row (addon.c take_find makes it the title bar's search icon only for a
# theme with a title row, page.title.height, and OLED's is 0: the status row names the page); it opens the T9 box.
def find_icon(s): return next((w for w in s.nodes[s.page]['children'] if s.nodes[w].get('style')=='s_img_search'),0)
# Pages that come while the search box is open (no list under it) are kept and the next asked for; leaving the box
# shows them all, the first row selected.
s=SlskMachine(); s.open(); s.answer('ok','refresh','',SMENU); s.pick(2); s.answer('ok','pages','c1',[('search','','Search','','','')]+arts(0,49))
assert not find_icon(s) and len(s.rows())==51 and s.labels()[1]=='Search',s.labels()[:3]
s.pick(0); edit=s.find('edit'); assert edit and not s.find('scroll_view')
s.answer('ok','pages','c2',arts(1)); assert s.requests[-1]==('downloads','c1') and not s.find('scroll_view')
s.answer('ok','pages','',arts(2)); assert s.requests[-1]==('downloads','c2') and not s.find('scroll_view')
f,ctx=s.handler(edit,O['EVT_BLUR']); s.call(address=f,args=(ctx,s.event,0,0),gap=0,count=5_000_000); s.advance(0,clear=False)
assert not s.find('edit') and len(s.rows())==150 and s.labels()[-1:]==['Artist 149'] and s.nodes[s.find('scroll_view')]['_ringnav_index']==0
assert not find_icon(s); passed()
# A Sort row after the Search row stays a list row as well (take_sort follows a taken search only), and its press
# opens it.
SORTED=[('search','','Search','','',''),('sort','sorts','Sort','','by','')]
s=SlskMachine(); s.open(); s.answer('ok','refresh','',SMENU); s.pick(2); s.answer('ok','pages','',SORTED+arts(0,3))
assert not find_icon(s) and not [w for w in s.nodes[s.page]['children'] if s.nodes[w].get('style')=='s_img_order_navbar']
assert len(s.rows())==5 and 'Sort' in s.labels(),s.labels()
s.pick(1); s.answer('ok','','',[]); assert s.requests[-1]==('sorts','by'),s.requests[-1]; passed()  # served on the worker
# An action sent while a page is on its way takes its place: its reply toasted, the level is asked again from its
# first page (a new list, not appended to the old one) and paged again, its row selected.
s=SlskMachine(); s.open(); s.answer('ok','refresh','',SMENU); s.pick(2); s.answer('ok','pages','c1',arts(0))
view=s.find('scroll_view'); s.top=s.page; s.status=s.alloc(0x200); s.nodes[view]['_ringnav_index']=7; s.press(80); assert s.hold()==11
s.answer('ok','Playing Artist 007','',[]); assert s.requests[-1]==('playartist','ar-7') and s.toasts[-1][3]=='Playing Artist 007',s.requests[-1]
s.answer('ok','pages','c1',arts(0)); assert s.requests[-1]==('downloads',) and len(s.rows())==51 and s.labels()[-1:]==['Loading…']
s.answer('ok','pages','',arts(1)); view=s.find('scroll_view')
assert s.requests[-1]==('downloads','c1') and len(s.rows())==100 and s.nodes[view]['_ringnav_index']==7; passed()
# Back twice to the menu; Phone Remote shows the QR code page from the add-on's status file.
m.back(); m.answer('ok','refresh','50',[]); m.back(); m.answer('ok','refresh','',SMENU); assert m.labels()==SLABELS
code=[[(x*7+y*3)%5<2 for x in range(21)] for y in range(21)]
hexed=''.join(''.join('%x'%sum(8>>b for b in range(4) if d*4+b<21 and row[d*4+b]) for d in range(6)) for row in code)
m.files[SSTATE]=bytearray(f'addr=192.168.1.23:8080\ndl=0\nhold=0\nnote=Online as alice\npin=548001\nqr={hexed}\nqrsize=21\n'.encode())
m.pick(0); qp=m.top; assert m.nodes[qp]['name']=='qrcode_page'
# One view the size of the page, painted (addon.c qr_paint): the code on its plate centred under the status row, the
# app's note under it and the address and spaced PIN under that.
view=next(w for w in m.nodes if m.nodes[w]['type']=='view' and m.get(w+O['W_PARENT'])==qp)
def qr_painted():
    m.bands.clear(); m.rounded.clear(); m.letters.clear(); m.call(address=HOOKS['widget_on_paint_border'][0],args=(view,m.canvas,0,0))
    return [l['text'] for l in m.letters]
said=qr_painted(); assert said==['Online as alice','or open 192.168.1.23:8080  \u00b7  PIN 548 001'],said
side,x0,y0=166,(375-166)//2,4; mod,off=side//25,(side-side//25*21)//2; want=[]
for y,row in enumerate(code):
    x=0
    while x<21:
        r=0
        while x+r<21 and row[x+r]: r+=1
        if r: want.append((x0+off+x*mod,y0+off+y*mod,r*mod,mod))
        x+=r or 1
assert [b[:4] for b in m.bands]==want and {b[4] for b in m.bands}=={color_t(0)} and m.rounded[0]['rect']==(x0,y0,side,side),(m.bands[:3],want[:3],m.rounded[:1]); passed()
# No address (no Wi-Fi): no code, and the page says so; a second later it follows the file.
m.files[SSTATE]=bytearray(b'dl=0\nhold=0\nnote=Connect the Q2 to Wi-Fi\npin=548001\nqr=\nqrsize=0\n'); m.advance(1000)
said=qr_painted(); assert not m.bands and 'Connect the Q2 to Wi-Fi' in said,said
# A step (q2-subsonic's sign-in) is a mode page: a solid circle, its glyph, the title and the line.
m.files[SSTATE]=bytearray(f'addr=192.168.1.23:8080\npin=548001\nqr={hexed}\nqrsize=21\nstep=check\ntitle=Signing In\u2026\nline=Checking with music.example.com\n'.encode()); m.advance(1000)
said=qr_painted(); assert said==['Signing In\u2026','Checking with music.example.com'] and len(m.rounded)==1 and not m.bands,said
assert any(i.endswith('modepage_check') for i in m.image_loads),m.image_loads[-3:]
m.destroy(qp); m.top=m.page; passed()
# Holding standby off: while the add-on asks (hold=1), each poll resets standby and auto power-off
# (not the screen's); once it stops by itself (its loop's pid gone), it is let go.
m.files[SSTATE]=bytearray(b'addr=x\ndl=1\nhold=1\n'); names=m.poll()
assert ('reset_poweroptions_timer',1,1,0) in [c[:4] for c in m.calls]
m.files[SSTATE]=bytearray(b'addr=x\ndl=0\nhold=0\n'); m.poll(); assert 'reset_poweroptions_timer' not in [c[0] for c in m.calls]
m.files[SPID]=bytearray(b'123'); m.poll(11000); m.files[SSTATE]=bytearray(b'hold=1\n'); m.files.pop(SPID); m.poll(); m.poll()
assert 'reset_poweroptions_timer' not in [c[0] for c in m.calls]; passed()
# USB mode, before stock unmounts the card: the add-on asked to end and waited for (its loop's pid
# file gone), then stock's page; started again once USB mode is over and the card is back.
m=SlskMachine(); m.open(); m.answer('ok','refresh','',SMENU); m.destroy(m.page); m.files[SPID]=bytearray(b'123')
m.on_sleep=lambda: m.files.pop(SPID,None) if m.now>=t0+300 else None; t0=m.now; m.systems=[]
tramp=int(manifest['patch_symbols']['stock_usbmode_trampoline'],16); m.handlers[tramp]='stock_usbmode'; m.u.hook_add(UC_HOOK_CODE,m.hook,begin=tramp,end=tramp)
m.call(address=HOOKS['usbmode_page_init'][0],args=(0x55,0x66,0,0),gap=0,count=5_000_000); m.on_sleep=None
names=[c[0] for c in m.calls]
assert m.systems==['kill $(cat /tmp/q2-soulseek) 2>/dev/null || rm -f /tmp/q2-soulseek'] and names.index('stock_usbmode')>names.index('sleep_ms') and SPID not in m.files,(m.systems,names[-12:])
m.open_pages={'usbmode_page'}; m.poll(); assert m.systems==['kill $(cat /tmp/q2-soulseek) 2>/dev/null || rm -f /tmp/q2-soulseek']
m.open_pages=set(); m.poll(); assert m.systems[-1]=='/bin/sh /mnt/mmc/.soulseek/run &'; passed()
# Stop Streaming Services stops it too.
m.systems=[]; run(m,'addon_stop'); assert m.systems==['kill $(cat /tmp/q2-soulseek) 2>/dev/null || rm -f /tmp/q2-soulseek']; passed()
# Requests no longer answered while the app may still run (its socket gone, or the app frozen): the
# error page offers Stop Soulseek beside Retry. It ends the app through its loop, which a frozen app
# can't refuse as it could its own stop, toasts and leaves the page; nothing starts it again.
SKILL='kill $(cat /tmp/q2-soulseek) 2>/dev/null || rm -f /tmp/q2-soulseek'
def slsk_gone():
    """Soulseek's menu, then its loop still running (its pid file) but its socket gone."""
    m=SlskMachine(); m.open(); m.answer('ok','refresh','',SMENU); m.files[SPID]=bytearray(b'123')
    m.serve=None; m.advance(10000); m.worker(); return m
m=slsk_gone(); assert m.labels()==["Soulseek isn't running",'Retry','Stop Soulseek'],m.labels()
m.systems=[]; m.row(1)
assert m.systems==[SKILL] and m.toasts[-1][3]=='Soulseek stopped' and 'navigator_back' in [c[0] for c in m.calls],(m.systems,m.toasts[-1:])
m.close(); m.files.pop(SPID); m.poll(); m.poll(); assert m.systems==[SKILL]; passed()
# Retry there restarts it: its loop asked to end, the error page waiting under the loading overlay as
# for a start, and the app started again once the loop's pid file is gone; then its rows.
m=slsk_gone(); m.systems=[]; m.row(0); m.worker()
assert m.systems==[SKILL] and m.labels()==["Soulseek isn't running",'Retry','Stop Soulseek'],(m.systems,m.labels())
m.poll(); assert m.systems==[SKILL]  # the old loop still there
m.files.pop(SPID); m.poll(); assert m.systems==[SKILL,'/bin/sh /mnt/mmc/.soulseek/run &'],m.systems
m.advance(500); m.answer('ok','refresh','',SMENU); assert m.labels()==SLABELS,m.labels(); passed()
# A level loading for 3 s (a frozen app holding the request, which gives up at 15 s) keeps the menu under the
# loading overlay, with no Stop row of its own: Return cancels it, and the menu's Stop Soulseek ends the app.
m=SlskMachine(); m.open(); m.answer('ok','refresh','',SMENU); m.files[SPID]=bytearray(b'123'); m.pick(2)
assert m.labels()==SLABELS; m.advance(3300); assert m.labels()==SLABELS,m.labels()
m.close(); passed()
# An app that isn't running (it stopped by itself) has nothing to stop: Retry alone, which starts it.
m=SlskMachine(); m.open(); m.answer('ok','refresh','',SMENU); m.serve=None; m.advance(10000); m.poll(); m.worker()
assert m.labels()==["Soulseek isn't running",'Retry'],m.labels()
m.systems=[]; m.row(0); assert m.systems==['/bin/sh /mnt/mmc/.soulseek/run &'],m.systems; m.close(); passed()

# File Browser: q2-filebrowser from the card's .filebrowser folder, the same page on its own socket.
# Its manifest's qr=1: turned on by its row, its QR code page opens once it answers, the menu behind
# it; opened again while it runs, the menu alone.
FBIN,FBROWSE,FSTATE,FPID='/mnt/mmc/.filebrowser/q2-filebrowser','/tmp/q2-filebrowser.browse.sock','/tmp/q2-filebrowser.state','/tmp/q2-filebrowser'
class FileBrowserMachine(AppMachine):
    app,manifest='filebrowser','name=File Browser\nsearch=Search\nqr=1\n'
FMENU=[('remote','','Open on Your Phone','http://192.168.1.23:8000 · PIN 548 001','',''),('info','','Card: 12.3 GB free','of 59.4 GB','',''),
       ('action','readonly','Read Only: Off','The phone can upload, rename, move and delete','',''),
       ('action','stop','Turn Off File Browser','It also turns off after 15 minutes unused','','')]
FLABELS=['File Browser','Open on Your Phone','http://192.168.1.23:8000 · PIN 548 001','Card: 12.3 GB free','of 59.4 GB','Read Only: Off',
         'The phone can upload, rename, move and delete','Turn Off File Browser','It also turns off after 15 minutes unused']
m=FileBrowserMachine(); m.gone(); m.open()
assert m.labels()==["File Browser isn't on the card",'Install it again in System settings → App Store'] and not m.systems; m.close(); passed()
m=FileBrowserMachine(); m.open(); assert m.systems==['/bin/sh /mnt/mmc/.filebrowser/run &'] and m.labels()==['Loading…']
m.worker(); assert m.labels()==['Starting File Browser…']; m.advance(500)
m.files[FSTATE]=bytearray(f"addr=192.168.1.23:8000\nhold=0\nnote=Manage the card's files there\npin=548001\nqr={hexed}\nqrsize=21\nversion=q2f-v1.0\n".encode())
m.answer('ok','refresh','',FMENU); assert m.requests[-1]==('menu',) and m.labels()==FLABELS
qp=m.top; assert m.nodes[qp]['name']=='qrcode_page'
view=next(w for w in m.nodes if m.nodes[w]['type']=='view' and m.get(w+O['W_PARENT'])==qp)
m.letters.clear(); m.call(address=HOOKS['widget_on_paint_border'][0],args=(view,m.canvas,0,0))
assert [l['text'] for l in m.letters]==["Manage the card's files there",'or open 192.168.1.23:8000  \u00b7  PIN 548 001'],m.letters
m.destroy(qp); m.top=m.page
# Asked again while on top: the same rows, and no second code page.
m.advance(2000); m.worker(); assert m.requests[-1]==('menu',) and m.top==m.page and m.labels()==FLABELS; passed()
# Read Only from the Q2; Open on Your Phone shows the code again.
m.pick(2); m.answer('ok','Read only','',[]); assert m.requests[-1]==('readonly',) and m.toasts[-1][3]=='Read only'
m.answer('ok','refresh','',FMENU); m.pick(0); assert m.nodes[m.top]['name']=='qrcode_page'; m.destroy(m.top); m.top=m.page; passed()
# Opened again while it runs (its loop's pid there): nothing started, the menu alone.
m.close(); m.systems=[]; m.files[FPID]=bytearray(b'9'); m.open(); assert not m.systems
m.answer('ok','refresh','',FMENU); assert m.top==m.page and m.labels()==FLABELS; passed()
# Turn Off: its reply toasted, the page left.
m.calls=[]; m.pick(3); m.answer('ok','File Browser off','',[])
assert m.requests[-1]==('stop',) and m.toasts[-1][3]=='File Browser off' and 'navigator_back' in [c[0] for c in m.calls]; m.close()
# Stop Streaming Services stops it too.
m.systems=[]; run(m,'addon_stop'); assert m.systems==['kill $(cat /tmp/q2-filebrowser) 2>/dev/null || rm -f /tmp/q2-filebrowser']; passed()

# Go to Album or Go to Artist on a song in a card app's folder (navigation.c qm_goto, addon.c
# addon_goto): with goto=1 in its manifest, its page opens on the song's album or artist, op "goto"
# (album or artist, then the song's path), titled by the song's tag; More… keeps both, Back leaves
# the page. Its page open already (the song played from it) is closed first, its late destroy leaving
# the new one alone. Without goto=1, or a song elsewhere, nothing: stock's pages.
class QobuzMachine(AppMachine): app,manifest='qobuz','name=Qobuz\nsearch=Search Qobuz\nqr=0\ngoto=1\n'
QSONG='/mnt/mmc/.qobuz/cache/album-a1/01 First [t1].flac'
m=QobuzMachine(); m.top=m.node('window','playing_page'); m.mock('navigator_back')
assert run(m,'addon_goto',m.string(QSONG),m.string('Record'),1)==1; m.page=m.top
assert m.nodes[m.page]['name']=='addon_page' and m.systems==['/bin/sh /mnt/mmc/.qobuz/run &'] and m.labels()==['Loading…']
m.answer('ok','','40',[('list','artist','The Band','Artist','5',''),('action','play','First','3:20','album-a1:t1','dl:album-a1:t1')])
assert m.requests[-1]==('goto','album',QSONG) and m.labels()==['Record','The Band','Artist','First','3:20','More…'],m.labels()
m.pick(2); m.answer(*OK,[]); assert m.requests[-1]==('goto','album',QSONG,'40'),m.requests[-1]
m.back(); m.answer('ok','','40',[]); m.back(); assert 'navigator_back' in [c[0] for c in m.calls]; passed()
m=QobuzMachine(); m.open(); m.answer(*OK,[]); old=m.page; m.mock('window_manager_close_window_force')
m.top=m.node('window','playing_page')
assert run(m,'addon_goto',m.string(QSONG),m.string('The Band'),0)==1 and m.top!=old
assert [c[2] for c in m.calls if c[0]=='window_manager_close_window_force']==[old]
m.page=m.top; m.destroy(old); m.answer(*OK,[('info','','No albums','','','')])
assert m.requests[-1]==('goto','artist',QSONG) and m.labels()==['The Band','No albums',''],(m.requests[-1],m.labels()); passed()  # the page's note, its hint hidden (page_empty)
m=AppMachine(); assert run(m,'addon_goto',m.string('/mnt/mmc/.soulseek/a.flac'),m.string('A'),1)==0
m=QobuzMachine()
for path in ('/mnt/mmc/Music/a.flac','/mnt/mmc/.qobuz','/mnt/mmc/.Bad_Id/a.flac','/mnt/mmc/.waytoolongforanidxx/a.flac',
             '/mnt/mmc/.abcdefghijklmnop/a.flac'):  # 16 bytes: an id fits 15 and its NUL
    assert run(m,'addon_goto',m.string(path),m.string('A'),1)==0,path
assert not m.systems and m.top!=m.page; passed()

# Sleep timers (q2-podcasts' Sleep Timer, addon.c sleep_read and sleep_check): an app's .sleep holds
# "until <time>" or "end"; Q2 Muse keeps the timer and pauses whatever plays when it is due, or as the
# local track ends, then removes the file (the app's sign it is done). The app removing it cancels;
# playback stopping lets "end" go without pausing.
class PodMachine(AppMachine):
    app,manifest='podcasts','name=Podcasts\nsearch=Search Podcasts\nqr=0\n'
    def poll(self,ms=500):  # time() follows the clock, jump on (Wi-Fi setting it); the boot clock is self.now
        self.wms=getattr(self,'wms',0)+ms; self.wall=(86400+getattr(self,'jump',0)+self.wms//1000,self.wall[1]); return super().poll(ms)
    def hook(self,u,address,size,unused):  # unlink's path read as it is called: a stack buffer
        if self.handlers.get(address)=='unlink@GLIBC_2.0': self.unlinked.append(self.text(u.reg_read(UC_MIPS_REG_A0)))
        return super().hook(u,address,size,unused)
SLEEP='/tmp/q2-podcasts.sleep'
def pod_sleep():
    m=PodMachine(); m.unlinked=[]; m.open(); m.answer(*OK,[]); m.mock('player_play_pause','unlink@GLIBC_2.0'); m.play=2
    return m
paused=lambda names: names.count('player_play_pause')
unlinked=lambda m: m.unlinked
m=pod_sleep(); m.files[SLEEP]=bytearray(b'until 86460\n')  # time() is 86400: a minute on
assert not paused(m.poll()); assert not paused(m.poll(30000))
names=m.poll(31000); assert paused(names)==1 and unlinked(m)==[SLEEP],(names,unlinked(m))
m.files.pop(SLEEP); assert not paused(m.poll(60000)); passed()
m=pod_sleep(); m.files[SLEEP]=bytearray(b'until 86460\n'); m.poll(); m.files.pop(SLEEP); m.poll()
assert not paused(m.poll(61000)) and not unlinked(m); passed()  # cancelled by the app
m=pod_sleep(); m.files[SLEEP]=bytearray(b'until 86390\n'); assert paused(m.poll())==1; passed()  # already due, 10 s ago
m=pod_sleep(); m.files[SLEEP]=bytearray(b'until 86000\n'); names=m.poll()  # a minute or more past (a stale file): let go
assert not paused(names) and unlinked(m)==[SLEEP],(names,unlinked(m)); passed()
m=pod_sleep(); m.files[SLEEP]=bytearray(b'until 86460\n'); m.poll(); names=m.poll(200000)  # a Standby past it
assert not paused(names) and unlinked(m)==[SLEEP],(names,unlinked(m)); passed()
m=pod_sleep(); m.files[SLEEP]=bytearray(b'until 86460\n'); m.poll(); m.jump=86400  # Wi-Fi sets the clock a day on
assert not paused(m.poll()) and not unlinked(m); assert paused(m.poll(61000))==1; passed()  # the time left kept
# No time, a number too long, or more than a week away (an app's mistake): dropped, nothing paused.
for bad in (b'until \n',b'until soon\n',b'until 999999999999\n',b'until 9000000\n',b'until 86460s\n'):
    m=pod_sleep(); m.files[SLEEP]=bytearray(bad); names=m.poll()
    assert not paused(names) and unlinked(m)==[SLEEP] and not paused(m.poll(600000)),bad
passed()
# "end" pauses as the track leaves its last 2 s (each is 225 s here) for the next one, which waits at its
# start, or starts over (repeat one). Another track picked, or a seek back, waits for that one's end
# instead: stock refills the same queue for a new one, so a pick looks the same as Next.
def end_sleep(before=None):  # before: the app's request before "end"
    m=pod_sleep(); m.word(syms['mcl_pdeqplaylist'],m.deque([m.song(n) for n in 'ABC'])); m.word(O['MCL_POS'],0)
    m.playtime=100
    if before: m.files[SLEEP]=bytearray(before); m.poll()
    m.files[SLEEP]=bytearray(b'end\n'); assert not paused(m.poll()); return m
m=end_sleep(); m.playtime=224; assert not paused(m.poll(5000))
m.word(O['MCL_POS'],1); m.playtime=0; names=m.poll(); assert paused(names)==1 and unlinked(m)==[SLEEP],names; passed()
m=end_sleep(); m.playtime=224; assert not paused(m.poll()); m.playtime=0  # repeat one: the same one over
names=m.poll(); assert paused(names)==1 and unlinked(m)==[SLEEP],names; passed()
m=end_sleep(); m.word(O['MCL_POS'],2); m.playtime=0; names=m.poll()  # another picked, mid-way
assert not paused(names) and not unlinked(m)
m.playtime=30; assert not paused(m.poll()); m.playtime=5; assert not paused(m.poll())  # a seek back
m.playtime=223; assert not paused(m.poll()); m.word(O['MCL_POS'],0); m.playtime=0
names=m.poll(); assert paused(names)==1 and unlinked(m)==[SLEEP],names; passed()  # that one's end
# Ended by itself (mclNextSong's automatic call, auto_nexts) before its stated length's last seconds: a VBR
# file's length can be overstated.
m=end_sleep(); m.playtime=200; m.poll(); a=payload_syms['auto_nexts']; m.word(a,m.get(a)+1)
m.word(O['MCL_POS'],1); m.playtime=0; names=m.poll(); assert paused(names)==1 and unlinked(m)==[SLEEP],names; passed()
# Stopped a moment (between tracks) keeps it; stopped 3 s lets it go.
m=end_sleep(); m.play=1; assert not paused(m.poll()) and not unlinked(m)
m.play=2; m.playtime=224; m.poll(); m.word(O['MCL_POS'],1); m.playtime=0; assert paused(m.poll())==1; passed()
m=end_sleep(); m.play=1; m.poll(); names=m.poll(3500); assert not paused(names) and unlinked(m)==[SLEEP],names; passed()  # stopped
# A new request replaces the app's own: a time, then "end", pauses at the end only.
m=end_sleep(b'until 86460\n'); assert not paused(m.poll(61000)) and not unlinked(m)
m.playtime=224; m.poll(); m.word(O['MCL_POS'],1); m.playtime=0; assert paused(m.poll())==1; passed()

# Deezer: q2-deezer from the card's .deezer folder. Nothing streams: a press downloads, to Deezer/ on
# the card, and a song on the card, a local row, plays from it as local music (library_lists.c songs_open).
DZBIN,DZBROWSE,DZSTATE,DZPID='/mnt/mmc/.deezer/q2-deezer','/tmp/q2-deezer.browse.sock','/tmp/q2-deezer.state','/tmp/q2-deezer'
class DeezerMachine(AppMachine):
    app,manifest='deezer','name=Deezer\nsearch=Search Deezer\nqr=0\n'
m=DeezerMachine(); m.gone(); m.open()
assert m.labels()==["Deezer isn't on the card",'Install it again in System settings → App Store'] and not m.systems; m.close(); passed()
# Started, the menu asked; signed out, its sign-in row opens the code over its status file (and only
# then: Deezer's row doesn't open it by itself).
m=DeezerMachine(); m.open(); assert m.systems==['/bin/sh /mnt/mmc/.deezer/run &'] and m.labels()==['Loading…']
m.worker(); assert m.labels()==['Starting Deezer…']; m.advance(500)
DZOUT=[('remote','','Sign In from Your Phone','Press to show the code','',''),('action','stop','Stop Deezer','Downloads carry on next time','','')]
m.files[DZSTATE]=bytearray(f"addr=192.168.1.23:8090\nhold=0\nnote=Paste your Deezer ARL there\npin=135790\nqr={hexed}\nqrsize=21\n".encode())
m.answer('ok','refresh','',DZOUT); assert m.requests[-1]==('menu',) and m.top==m.page
assert m.labels()==['Deezer','Sign In from Your Phone','Press to show the code','Stop Deezer','Downloads carry on next time']
assert [chevron(m,i) for i in range(2)]==[True,False]  # remote opens the QR page
m.pick(0); qp=m.top; assert m.nodes[qp]['name']=='qrcode_page'
view=next(w for w in m.nodes if m.nodes[w]['type']=='view' and m.get(w+O['W_PARENT'])==qp)
m.letters.clear(); m.call(address=HOOKS['widget_on_paint_border'][0],args=(view,m.canvas,0,0))
assert [l['text'] for l in m.letters]==['Paste your Deezer ARL there','or open 192.168.1.23:8090  \u00b7  PIN 135 790'],m.letters
m.destroy(qp); m.top=m.page; passed()
# Signed in: Play/Pause held on a list downloads all of it; a list's song row, pressed, downloads it.
SONGS='/mnt/mmc/Deezer'
DZMENU=[('local',SONGS+'/','Downloaded Songs','2 songs on the card','',''),('list','tracks','Liked Songs','','liked','download'),
        ('search','','Search Deezer','','',''),('action','stop','Stop Deezer','Downloads carry on next time','','')]
m.advance(2000); m.answer('ok','refresh','',DZMENU)
assert m.labels()==['Deezer','Downloaded Songs','2 songs on the card','Liked Songs','Search Deezer','Stop Deezer','Downloads carry on next time'],m.labels()
assert [chevron(m,i) for i in range(4)]==[True,True,False,False]  # a folder and a list open pages; search and actions don't
m.top=m.page; m.status=m.alloc(0x200); m.nodes[m.find('scroll_view')]['_ringnav_index']=1; m.press(80)
assert m.hold()==11; m.answer('ok','Downloading 12 songs','',[])
assert m.requests[-1]==('download','liked') and m.toasts[-1][3]=='Downloading 12 songs'
m.answer('ok','refresh','',DZMENU); m.pick(2); assert m.nodes[m.find('edit')]['tips']=='Search Deezer'
f,ctx=m.handler(m.find('edit'),O['EVT_BLUR']); m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000); m.advance(0,clear=False)
LIKED=[('action','download','Download All','12 songs','liked',''),('action','download','Valerie','Broadcast','liked#5','download'),
       ('local',SONGS+'/Corporeal - Broadcast [6].flac','Corporeal','Broadcast · on the card','','')]
m.pick(1); m.answer('ok','refresh','',LIKED); assert m.requests[-1]==('tracks','liked') and m.labels()[:3]==['Liked Songs','Download All','12 songs'],m.labels()
m.pick(1); m.answer('ok','Downloading','',[]); assert m.requests[-1]==('download','liked#5') and m.toasts[-1][3]=='Downloading'
m.answer('ok','refresh','',LIKED); passed()
# A song on the card plays its folder from it, newest first, as local music: the card's songs only.
names=('Valerie - Broadcast [5].flac','Corporeal - Broadcast [6].flac','.Valerie - Broadcast [5]-1.flac.part')
recs=[m.song(n) for n in names]
for r,n,t in zip(recs,names,(100,200,300)):
    m.word(r+O['REC_PATH'],m.string(f'{SONGS}/{n}')); m.files[f'{SONGS}/{n}']=bytearray(); m.stats[f'{SONGS}/{n}']=t
m.word(recs[2]+O['REC_TYPE'],0)  # a download's .part isn't a song
m.found=recs; m.plays=[]; m.music_tags={f'{SONGS}/{names[1]}':('Corporeal','Broadcast','Tender Buttons','Broadcast',6,1)}
m.handlers[syms['toolsGetMusicInfo']]='c:toolsGetMusicInfo'  # the files' tags, as the mock above reads them
reads=lambda: [c[1] for c in m.calls if c[0]=='toolsGetMusicInfo' and isinstance(c[1],str)]
m.pick(2)
assert [p[2:4] for p in m.plays]==[(0,0xf001)] and m.play_names==['Corporeal - Broadcast [6].flac','Valerie - Broadcast [5].flac'],(m.plays,m.play_names)
assert [m.text(c[1]) for c in m.calls if c[0]=='toolsLoadDirectory'][-1]==SONGS; passed()
# The press reads no tags: each song takes its file's as it starts (card_tags, from ringnav_id3_path, after
# stock copied a library queue's record), the playing record and the parsed tags both, once a queue. A
# folder play's (stock parses its files), a CUE track's (its sheet's, issue #59) and a song without tags are left.
assert not reads(),reads()
q=m.deque([recs[1],recs[0]]); [m.word(r+O['REC_ALBUM'],0) for r in m.items(q)]  # a listing's records
m.word(syms['mcl_pdeqplaylist'],q); m.word(O['MCL_POS'],0); m.word(O['MCL_TYPE'],0xf001)
id3=m.alloc(0xa9c); m.mock('mclGetCueFlag')
def parse(path):
    m.calls=[]; m.call(address=payload_syms['ringnav_id3_path'],args=(id3,m.string(path),0,0),gap=0); return reads()
tags=lambda r: [m.text(m.get(r+O[k])) if m.get(r+O[k]) else '' for k in ('REC_TITLE','REC_ARTIST','REC_ALBUM','REC_ALBUM_ARTIST')]
r=m.items(q)[0]; song=f'{SONGS}/{names[1]}'
assert parse(song)==[song] and m.text(id3)==song
assert [m.text(id3+o) for o in (O['ID3_TITLE'],O['ID3_ARTIST'],O['ID3_ALBUM'],O['ID3_ALBUM']+0x580)]==['Corporeal','Broadcast','Tender Buttons','Broadcast']  # 0x580: the album artist
assert tags(r)==['Corporeal','Broadcast','Tender Buttons','Broadcast'] and (m.get(r+O['REC_TRACK']),m.get(r+O['REC_DISC']))==(6,1)
assert parse(song)==[]; passed()  # once: the record has its album
m.word(O['MCL_POS'],1); r=m.items(q)[1]; before=tags(r)
assert parse(f'{SONGS}/{names[0]}')==[f'{SONGS}/{names[0]}'] and tags(r)==before  # no tags: its record's
m.word(O['MCL_TYPE'],1); assert parse(f'{SONGS}/{names[0]}')==[]  # a folder play
m.word(O['MCL_TYPE'],0xf001); assert parse(f'{SONGS}/{names[1]}')==[]  # not the playing record's path
m.mock('mclGetCueFlag',prefix='one:'); assert parse(f'{SONGS}/{names[0]}')==[]; m.mock('mclGetCueFlag')  # a CUE track
m.music_tags=None; m.handlers[syms['toolsGetMusicInfo']]='toolsGetMusicInfo'; passed()
# Downloaded Songs (its uri a folder) lists them, newest first, labelled as OpenDeezer names them,
# and a row plays from it; a song gone from the card says so.
m.back(); m.answer('ok','refresh','',DZMENU); m.plays=[]; m.pick(0); songs=m.top
assert m.nodes[songs]['name']=='app_songs' and [l for l in m.labels(songs) if l]==['Downloaded Songs','Corporeal - Broadcast','Valerie - Broadcast'],m.labels(songs)
m.click(m.nodes[m.find('scroll_view',songs)]['children'][1])
assert [p[2:4] for p in m.plays]==[(1,0xf001)] and m.play_names==['Corporeal - Broadcast [6].flac','Valerie - Broadcast [5].flac'],(m.plays,m.play_names)
m.destroy(songs); m.top=m.page; m.found=recs[:1]; m.advance(2000); m.answer('ok','refresh','',DZMENU)
m.pick(1); m.answer('ok','refresh','',LIKED); n=len(m.toasts); m.pick(2)
assert m.toasts[n:] and m.toasts[-1][3]=="It isn't on the card any more",m.toasts[n:]; passed()
# Tidal Downloads ends in Clear All Downloads…, which asks first, as Spotify's Storage Management does:
# Cancel or Return keeps the songs; Remove deletes the files in .tidal, a .tmp too, but not .cache-size
# (Configuration's cap), and the page lists what is left: none.
T='/mnt/mmc/.tidal'; names=('11-2 A - One.flac','12-2 B - Two.m4a'); recs=[m.song(n) for n in names]
for r,n,t in zip(recs,names,(100,200)):
    m.word(r+O['REC_PATH'],m.string(f'{T}/{n}')); m.files[f'{T}/{n}']=bytearray(); m.stats[f'{T}/{n}']=t
m.files[T+'/13-2 C.flac.tmp']=bytearray(); m.files[T+'/.cache-size']=bytearray(b'2048M'); m.files['/mnt/mmc/Music/keep.flac']=bytearray()
m.tree[T]=[(n,8) for n in (*names,'13-2 C.flac.tmp','.cache-size')]; m.found=recs
assert run(m,'tidal_downloads',0,0)==0; td=m.top; tdrows=lambda: m.nodes[m.find('scroll_view',td)]['children']
LIST=['Tidal Downloads','B - Two','A - One','Clear All Downloads…']
assert m.nodes[td]['name']=='tidal_downloads' and [l for l in m.labels(td) if l]==LIST,m.labels(td)
# Clear All Downloads asks on the confirmation card; its answer (library_lists.c td_answer) runs once it has closed
def td_answer(row): m.call(address=payload_syms['td_answer'],args=(0,row,0,0),gap=0,clear=False); m.advance(1)
m.click(tdrows()[2]); td_answer(0); assert [l for l in m.labels(td) if l]==LIST  # Cancel, back on the Clear row
m.click(tdrows()[2]); m.found=[]  # stock's listing of the emptied folder
td_answer(1); assert [p for p in m.files if p.startswith(T)]==[T+'/.cache-size'] and '/mnt/mmc/Music/keep.flac' in m.files
assert [l for l in m.labels(td) if l]==['Tidal Downloads'] and empty_titles(m)[-1:]==['No Downloads Yet']; m.destroy(td); passed()  # empty_list: the name kept hidden, the empty state
# Stream mode (q2-deezer's stream.go, addon.c stream_poll): its .play file names a session's folder; its
# first song, once there, plays as a local album does (Now Playing opening); songs that come later join the
# queue's end, as Add to Queue does, while the queue is still the session's; the song playing is left in
# .playing for the app; once another queue plays, the session is left alone.
STREAM='/mnt/mmc/.deezer/stream/1700'
def stream_songs(*names):  # the folder's mtime steps with each change: stream_newer scans only then
    m.tree[STREAM]=[(n,8) for n in names]; rs=[]; m.stats[STREAM]=m.stats.get(STREAM,0)+1
    for n in names:
        if n.endswith('.part'): continue
        r=m.song(n); m.word(r+O['REC_PATH'],m.string(f'{STREAM}/{n}')); m.stats[f'{STREAM}/{n}']=1; rs.append(r)
    m.found=rs; return rs
m.files[DZPID]=bytearray(b'4242')  # running: its loop's pid
m.plays=[]; m.files['/tmp/q2-deezer.play']=bytearray(f'1700 {STREAM}\n'.encode()); stream_songs('01 - Snow [12].flac.part')
m.poll(); m.advance(0); assert not m.plays  # nothing whole yet
recs=stream_songs('01 - Snow [12].flac','02 - Oakwood [13].flac.part')
m.poll(); m.advance(0)
assert [p[2:4] for p in m.plays]==[(0,0xf001)] and m.play_names==['01 - Snow [12].flac'],(m.plays,m.play_names); passed()
m.word(syms['mcl_pdeqplaylist'],m.deque(recs)); m.word(O['MCL_POOL'],m.deque([0],'int')); m.word(O['MCL_POS'],0)
m.poll(); assert m.files['/tmp/q2-deezer.playing']==f'{STREAM}/01 - Snow [12].flac\n'.encode() and len(m.plays)==1
assert m.natural==1 and m.get(payload_syms['press_natural'])==0  # the app's poll, not a press: no wait for the DAC
seen=m.stats[STREAM]; stream_songs('01 - Snow [12].flac','02 - Oakwood [13].flac'); m.stats[STREAM]=seen
m.poll(); assert len(m.names())==1  # the folder's mtime unchanged: not scanned
stream_songs('01 - Snow [12].flac','02 - Oakwood [13].flac','03 - Chateau [14].flac'); m.poll()
assert m.names()==['01 - Snow [12].flac','02 - Oakwood [13].flac','03 - Chateau [14].flac'] and len(m.plays)==1,m.names()
m.word(O['MCL_POS'],1); m.poll(); assert m.files['/tmp/q2-deezer.playing']==f'{STREAM}/02 - Oakwood [13].flac\n'.encode(); passed()
# Played on past the first ones, which the app deleted: a later song still joins, after the last queued.
stream_songs('03 - Chateau [14].flac','04 - Cellar Door [15].flac'); m.poll()
assert m.names()[-2:]==['03 - Chateau [14].flac','04 - Cellar Door [15].flac'] and len(m.names())==4; passed()
# A load of the same queue (stock reloads it) leaves a folder's session going: only a queue whose first song
# is out of its folder ends it, as before playlists came.
m.call(address=syms['mclLoadPlayList'],args=(m.deque(m.items(m.get(syms['mcl_pdeqplaylist']))),0,0xf001,0),gap=0)
stream_songs('04 - Cellar Door [15].flac','05 - Lantern [17].flac'); m.poll()
assert m.names()[-2:]==['04 - Cellar Door [15].flac','05 - Lantern [17].flac'] and len(m.names())==5,m.names(); passed()
# Another queue: no song joins it, and a new session plays again.
m.word(syms['mcl_pdeqplaylist'],m.deque([m.song('Valerie - Broadcast [5].flac')])); stream_songs('05 - Snow [16].flac'); m.poll()
assert m.names()==['Valerie - Broadcast [5].flac'] and len(m.plays)==1
STREAM='/mnt/mmc/.deezer/stream/1800'; m.files['/tmp/q2-deezer.play']=bytearray(f'1800 {STREAM}\n'.encode())
stream_songs('01 - Bloodhound [20].flac'); m.poll(); m.advance(0)
assert len(m.plays)==2 and m.play_names==['01 - Bloodhound [20].flac']; passed()
# Only a hidden folder of the card is played.
m.files['/tmp/q2-deezer.play']=bytearray(b'1900 /mnt/mmc/Music\n'); m.tree['/mnt/mmc/Music']=[('a.flac',8)]; m.poll(); m.advance(0)
assert len(m.plays)==2; passed()
# Any song stock's listing takes streams, not only .flac and .mp3: a new name wakes the scan (but a .part or
# .tmp, still being written), and stock's listing (REC_TYPE 8) says what is a song.
STREAM='/mnt/mmc/.deezer/stream/2000'; m.files['/tmp/q2-deezer.play']=bytearray(f'2000 {STREAM}\n'.encode())
stream_songs('01 - Valerie [5].m4a.tmp'); m.poll(); m.advance(0); assert len(m.plays)==2
stream_songs('01 - Valerie [5].m4a'); m.poll(); m.advance(0)
assert len(m.plays)==3 and m.play_names==['01 - Valerie [5].m4a'],(m.plays,m.play_names); passed()
# A playlist (q2-subsonic's queue.m3u): .play names an .m3u on the card, its lines songs anywhere on the card
# (synced ones in their album folders, streamed ones in the app's folder), played in its order. A song not
# there yet is waited for, and nothing after it is queued before it; # lines, a BOM, CRLF and a path off the
# card are passed over.
SYNC,STR='/mnt/mmc/Navidrome/Broadcast/Tender Buttons','/mnt/mmc/.deezer/stream'; M3U=STR+'/queue.m3u'
def card_song(path,listing=True):
    d,n=path.rsplit('/',1); r=m.song(n); m.word(r+O['REC_PATH'],m.string(path))
    m.files[path]=bytearray(); m.stats[path]=1
    if listing: m.listings.setdefault(d,[]).append(r)
    return r
def m3u(*lines): m.files[M3U]=bytearray(('﻿#EXTM3U\r\n'+''.join(l+'\r\n' for l in lines)).encode()); m.stats[M3U]=m.stats.get(M3U,0)+1
S1,S2,S3,S4=f'{SYNC}/01 - Tears.flac',f'{STR}/02 - Valerie [5].opus',f'{SYNC}/03 - Corporeal.flac',f'{SYNC}/04 - Lunch.flac'
ra,rc=card_song(S1),card_song(S3); m3u(S1,'#EXTINF:182,Broadcast - Valerie',S2,'/etc/passwd',S3)
# From here Now Playing loads the queue it opens with at once, through the real mclLoadPlayList (ringnav_load
# counts it in playback_loads), as stock's playing_page_init does: no queue is written by hand.
m.stock_load=1; loads=lambda: m.get(payload_syms['playback_loads'])
m.plays=[]; m.files['/tmp/q2-deezer.play']=bytearray(f'2100 {M3U}\n'.encode()); m.poll(); was=loads(); m.advance(0)
assert [p[2:4] for p in m.plays]==[(0,0xf001)] and m.play_names==['01 - Tears.flac'],(m.plays,m.play_names)
assert m.names()==['01 - Tears.flac'] and loads()==was+1,(m.names(),loads(),was)
m.poll(); assert m.names()==['01 - Tears.flac'] and m.files['/tmp/q2-deezer.playing']==f'{S1}\n'.encode(); passed()
# The streamed song lands (renamed into place whole): it and the one after it join the queue's end, in order.
card_song(S2); m.poll(); assert m.names()==['01 - Tears.flac','02 - Valerie [5].opus','03 - Corporeal.flac'],m.names(); passed()
# The app appends to the file (written aside and renamed): the new line joins; queued ones aren't again.
card_song(S4); m3u(S1,S2,'/etc/passwd',S3,S4); m.poll()
assert m.names()==['01 - Tears.flac','02 - Valerie [5].opus','03 - Corporeal.flac','04 - Lunch.flac'],m.names()
m.word(O['MCL_POS'],1); m.poll(); assert m.files['/tmp/q2-deezer.playing']==f'{S2}\n'.encode(); passed()
# A file there that stock's listing doesn't take as a song is passed over, not waited for.
S5,S6=f'{SYNC}/05 - cover.jpg',f'{SYNC}/06 - Dream.flac'; card_song(S5,listing=False); card_song(S6); m3u(S1,S2,'/etc/passwd',S3,S4,S5,S6); m.poll()
assert m.names()[-1]=='06 - Dream.flac' and len(m.names())==5,m.names(); passed()
# An entry too long to be a path here (cut, it would be waited for forever) is passed over; a long one that
# fits (about 400 bytes: an app's Artist/Album/Title folders) plays and is left in .playing.
LONG,FIT=f'{SYNC}/{"x"*600}.flac',f'{SYNC}/{"y"*120}/{"z"*120}/{"w"*100}.flac'
card_song(FIT); m3u(S1,S2,'/etc/passwd',S3,S4,S5,S6,LONG,FIT); m.poll()
assert m.names()[-1]==FIT.rsplit('/',1)[1] and len(m.names())==6,m.names()
m.word(O['MCL_POS'],5); m.poll(); assert m.files['/tmp/q2-deezer.playing']==f'{FIT}\n'.encode(); passed()
# A playlist longer than M3U_MAX (256 KB) is read that far and a byte on: its last line whose newline wasn't
# read is dropped, not taken as a path, even when what was read of it names a song. One of M3U_MAX bytes
# exactly is whole: its last line plays, newline or not.
CUT=f'{SYNC}/08 - Cut.flac'; card_song(CUT); m3u(S1,S2,'/etc/passwd',S3,S4,S5,S6,LONG,FIT); body=m.files[M3U]
def padded(size,tail):  # body, a # line, then CUT's line, the whole size bytes, then tail
    pad=size-len(body)-2-len(CUT.encode()); return body+b'#'+b'p'*(pad-1)+b'\r\n'+CUT.encode()+tail
m.files[M3U]=padded((256<<10)+1,b'.part\r\n'); m.stats[M3U]+=1; m.poll(); assert len(m.names())==6,m.names()
m.files[M3U]=padded(256<<10,b''); m.stats[M3U]+=1; m.poll(); assert m.names()[-1]=='08 - Cut.flac' and len(m.names())==7,m.names(); passed()
# One missing entry doesn't stall the rest: the app rewrites the lines from the one waited for on (a download
# that failed, a synced file deleted), dropping or replacing it, and the songs after it play.
MISS,S8,REPL,S9=f'{STR}/11 - Gone [7].opus',f'{SYNC}/12 - Eight.flac',f'{STR}/13 - Instead [9].opus',f'{SYNC}/14 - Nine.flac'
card_song(S8); card_song(S9); PASSED=(S1,S2,'/etc/passwd',S3,S4,S5,S6,LONG,FIT,CUT)
m3u(*PASSED,MISS,S8); m.poll(); assert len(m.names())==7,m.names()  # waits for MISS
m3u(*PASSED,S8); m.poll(); assert m.names()[-1]=='12 - Eight.flac' and len(m.names())==8,m.names()
m3u(*PASSED,S8,MISS,S9); m.poll(); assert len(m.names())==8,m.names()
card_song(REPL); m3u(*PASSED,S8,REPL,S9); m.poll()
assert m.names()[-3:]==['12 - Eight.flac','13 - Instead [9].opus','14 - Nine.flac'] and len(m.names())==10,m.names(); passed()
# The user plays that album from the Library (a queue loaded since the session's, its first song the
# playlist's): it is the user's. Nothing joins it, and .playing no longer follows it.
m.call(address=syms['mclLoadPlayList'],args=(m.deque([ra,rc]),1,0xf001,0),gap=0)
m3u(*PASSED,S8,REPL,S9,S4); m.poll()
assert m.names()==['01 - Tears.flac','03 - Corporeal.flac'] and m.files['/tmp/q2-deezer.playing']==f'{FIT}\n'.encode(); passed()
# Another queue: the playlist's session is left. An .m3u off the card (or with ..) is never played.
m.word(syms['mcl_pdeqplaylist'],m.deque([m.song('Other.flac')])); m3u(S1,S2,S3,S4,S6,f'{SYNC}/07.flac'); m.poll(); assert m.names()==['Other.flac']
m.files['/tmp/q2-deezer.play']=bytearray(b'2200 /tmp/evil.m3u\n'); m.files['/tmp/evil.m3u']=bytearray(f'{S1}\n'.encode()); m.poll(); m.advance(0)
m.files['/tmp/q2-deezer.play']=bytearray(f'2300 {STR}/../x.m3u\n'.encode()); m.poll(); m.advance(0); assert len(m.plays)==1; passed()
# A playlist named .M3U in capitals plays too. A long one lists M3U_BATCH (32) folders a poll at most (a song's
# folder, unless the song before it was in the same one), the rest on the polls after, in its order; songs that
# share a folder cost one listing.
def listed(): return len([c for c in m.calls if c[0]=='toolsLoadDirectory'])
M3U=STR+'/Queue.M3U'; many=[f'{SYNC}/x{i:02}/x{i:02} - Song.flac' for i in range(40)]; recs=[card_song(p) for p in many]; m3u(*many)
m.files['/tmp/q2-deezer.play']=bytearray(f'2400 {M3U}\n'.encode()); m.poll(); m.advance(0)  # advance() clears m.calls first
assert len(m.plays)==2 and m.play_names==[p.rsplit('/',1)[1] for p in many[:32]] and listed()==32,(m.play_names,listed())
m.poll(); assert m.names()==[p.rsplit('/',1)[1] for p in many],m.names()
M3U=STR+'/One album.m3u'; one=[f'{SYNC}/x{i:02} - Track.flac' for i in range(40)]; [card_song(p) for p in one]; m3u(*one)
m.files['/tmp/q2-deezer.play']=bytearray(f'2450 {M3U}\n'.encode()); m.poll(); m.advance(0)
assert len(m.plays)==3 and m.play_names==[p.rsplit('/',1)[1] for p in one] and listed()==1,(m.play_names,listed()); passed()
# Only a real .. segment is off the card: a name with an ellipsis plays, in a session named with one too; a
# path through .. is passed over, not waited for.
M3U=STR+'/Hello... queue.m3u'; E=f'{SYNC}... (Live)/01 - Hello... (Live).flac'; card_song(E); m3u(f'{SYNC}/../x.flac',f'{SYNC}/..',E)
m.files['/tmp/q2-deezer.play']=bytearray(f'2500 {M3U}\n'.encode()); m.poll(); m.advance(0)
assert len(m.plays)==4 and m.play_names==['01 - Hello... (Live).flac'],m.play_names; passed()
# A session still waiting for its first song leaves a queue the user starts meanwhile alone: the song that
# lands after it never replaces it, whether it lands before the next poll or once its start is armed.
M3U=STR+'/wait.m3u'; W1,W2=f'{STR}/01 - Late [30].opus',f'{STR}/01 - Later [31].opus'; USER=['01 - Tears.flac','03 - Corporeal.flac']
m3u(W1); m.files['/tmp/q2-deezer.play']=bytearray(f'2600 {M3U}\n'.encode()); m.poll(); m.advance(0); assert len(m.plays)==4
m.call(address=syms['mclLoadPlayList'],args=(m.deque([ra,rc]),0,0xf001,0),gap=0)  # the user's album
card_song(W1); m.poll(); m.advance(0); assert len(m.plays)==4 and m.names()==USER,(m.plays[4:],m.names())
m3u(W2); m.files['/tmp/q2-deezer.play']=bytearray(f'2700 {M3U}\n'.encode()); m.poll(); m.advance(0); assert len(m.plays)==4
card_song(W2); m.poll()  # its start armed
m.call(address=syms['mclLoadPlayList'],args=(m.deque([ra,rc]),0,0xf001,0),gap=0); m.advance(0)
assert len(m.plays)==4 and m.names()==USER,(m.plays[4:],m.names()); passed()
# While a library scan runs (its thread started, not done: library_scanning), no folder is listed, the scan
# dialog's own loop included, as a listing refills tools_pdeq_directory, which the scan fills too: a first
# song waits for its end, a start armed before it began waits too, and later songs join after it; .playing
# still follows the song playing.
M3U=STR+'/scan.m3u'; X1,X2=f'{SYNC}/x1/01 - Scan.flac',f'{SYNC}/x2/02 - Scan.flac'; card_song(X1); m3u(X1)
m.word(O['SCAN_THREAD'],1); m.files['/tmp/q2-deezer.play']=bytearray(f'2800 {M3U}\n'.encode())
m.poll(); assert not listed(); m.advance(0); assert len(m.plays)==4 and not listed()
m.word(O['SCAN_THREAD'],0); m.poll()  # done: its start armed
m.word(O['SCAN_THREAD'],1); m.advance(0); assert len(m.plays)==4 and not listed()  # another began first
m.word(O['SCAN_THREAD'],0); m.poll(); m.advance(0); assert len(m.plays)==5 and m.play_names==['01 - Scan.flac'],m.play_names
card_song(X2); m3u(X1,X2); m.word(O['SCAN_THREAD'],1); m.poll()
assert not listed() and m.names()==['01 - Scan.flac'] and m.files['/tmp/q2-deezer.playing']==f'{X1}\n'.encode(),m.names()
m.word(O['SCAN_THREAD'],0); m.poll(); assert m.names()==['01 - Scan.flac','02 - Scan.flac'],m.names()
m.word(O['SCAN_THREAD'],0); m.word(O['SCAN_DONE'],0); passed()
# Nor while the scan's dialog (Update Local Music, updatemusic_page; a library request's, updatemusic_dialog, opens
# without the page) is still open once the scan is done: the first song would open Now Playing over it, so it
# waits, an armed start too, and plays once the dialog closes.
for seq,win in ((2850,'updatemusic_page'),(2851,'updatemusic_dialog')):
    n=len(m.plays); m.word(O['SCAN_THREAD'],1); m.word(O['SCAN_DONE'],1); M3U=STR+'/dialog.m3u'; m3u(X1); m.open_pages={win}
    m.files['/tmp/q2-deezer.play']=bytearray(f'{seq} {M3U}\n'.encode()); m.poll(); m.advance(0); assert len(m.plays)==n and not listed(),win
    m.open_pages=set(); m.poll()  # closed: its start armed
    m.open_pages={win}; m.advance(0); assert len(m.plays)==n and not listed(),win  # opened again first
    m.open_pages=set(); m.poll(); m.advance(0); assert len(m.plays)==n+1 and m.play_names==['01 - Scan.flac'],(win,m.play_names)
    m.word(O['SCAN_THREAD'],0); m.word(O['SCAN_DONE'],0)
passed()
# A .play path too long for sm.dir (160 bytes or more) is refused, never cut: a cut path names another (here
# a folder of songs), which would play instead. One that fits plays.
CUTDIR=f'{STR}/{"q"*(159-len(STR)-1)}'; assert len(CUTDIR)==159; m.tree[CUTDIR]=[('01 - Wrong.flac',8)]; m.stats[CUTDIR]=1
card_song(f'{CUTDIR}/01 - Wrong.flac'); m.files['/tmp/q2-deezer.play']=bytearray(f'2900 {CUTDIR}q.m3u\n'.encode())
m.poll(); m.advance(0); m.poll(); m.advance(0); assert len(m.plays)==7,m.play_names
M3U=f'{STR}/{"q"*(159-len(STR)-1-4)}.m3u'; assert len(M3U)==159; m3u(X1)
m.files['/tmp/q2-deezer.play']=bytearray(f'3000 {M3U}\n'.encode()); m.poll(); m.advance(0)
assert len(m.plays)==8 and m.play_names==['01 - Scan.flac'],m.play_names; passed()
# A song that lands while the listing an earlier entry of its folder took is in hand isn't passed over as no song:
# its folder is listed again on the next poll, and it joins.
M3U=STR+'/race.m3u'; R1,R2=f'{STR}/r/01 - Early [40].opus',f'{STR}/r/02 - Late [41].opus'; card_song(R1); r2=card_song(R2,listing=False)
m3u(R1,R2); m.files['/tmp/q2-deezer.play']=bytearray(f'3100 {M3U}\n'.encode()); m.poll(); m.advance(0)
assert len(m.plays)==9 and m.play_names==['01 - Early [40].opus'],m.play_names
m.listings[f'{STR}/r'].append(r2); m.poll(); assert m.names()==['01 - Early [40].opus','02 - Late [41].opus'],m.names(); passed()
# A song playing whose path .playing can't hold (512 bytes or more, added to the queue by hand) leaves it as it was.
m.word(O['MCL_POS'],0); m.poll(); assert m.files['/tmp/q2-deezer.playing']==f'{R1}\n'.encode()
big=m.song('Big.flac'); m.word(big+O['REC_PATH'],m.string(f'{SYNC}/{"b"*600}.flac'))
m.word(syms['mcl_pdeqplaylist'],m.deque([*m.items(m.get(syms['mcl_pdeqplaylist'])),big])); m.word(O['MCL_POS'],2); m.poll()
assert m.files['/tmp/q2-deezer.playing']==f'{R1}\n'.encode(),bytes(m.files['/tmp/q2-deezer.playing'][:40]); passed()
# An album's folder (Organize Files: Artist/Album/07 - Title [id].flac), its row's context "album":
# its songs listed and played in track order, by name, whatever their dates.
ALBUM=SONGS+'/Broadcast/Tender Buttons'
names=('02 - Valerie [5].flac','01 - Tears in the Typing Pool [4].flac','03 - Corporeal [6].flac')
recs=[m.song(n) for n in names]
for r,n,t in zip(recs,names,(300,200,100)):
    m.word(r+O['REC_PATH'],m.string(f'{ALBUM}/{n}')); m.files[f'{ALBUM}/{n}']=bytearray(); m.stats[f'{ALBUM}/{n}']=t
m.found=recs; m.back(); m.answer('ok','refresh','',[('local',ALBUM+'/','Tender Buttons','3 songs','album','')])
m.plays=[]; m.pick(0); songs=m.top
assert m.nodes[songs]['name']=='app_songs' and [l for l in m.labels(songs) if l]==['Tender Buttons','01 - Tears in the Typing Pool','02 - Valerie','03 - Corporeal'],m.labels(songs)
m.click(m.nodes[m.find('scroll_view',songs)]['children'][1])
assert [p[2:4] for p in m.plays]==[(1,0xf001)] and m.play_names==list(sorted(names)),(m.plays,m.play_names)
m.destroy(songs); m.top=m.page; m.advance(2000); m.answer('ok','refresh','',[('local',f'{ALBUM}/{names[0]}','Valerie','Broadcast · on the card','album','')])
m.plays=[]; m.pick(0); assert [p[2:4] for p in m.plays]==[(1,0xf001)] and m.play_names==list(sorted(names)),(m.plays,m.play_names); passed()
# Stop Streaming Services, USB storage and Rockbox stop it as the other apps (it writes to the card).
m.close(); m.files[DZPID]=bytearray(b'7'); m.systems=[]; run(m,'addon_stop')
assert 'kill $(cat /tmp/q2-deezer) 2>/dev/null || rm -f /tmp/q2-deezer' in m.systems,m.systems; passed()
# Two apps running, each .play left in /tmp (q2-deezer's and q2-subsonic's, kept while each runs): only a change
# to an app's own starts a session, so the newer one plays once and neither takes the queue back at each poll.
card_app(m,'subsonic','name=Subsonic\n'); m.files['/tmp/q2-subsonic']=bytearray(b'4343'); n=len(m.plays)
for t in ('Deezer','Subsonic'): m.click(stream_rows(m)[t]); m.page=m.top; m.close()  # started again: polled
STREAM='/mnt/mmc/.subsonic/stream/1'; m.files['/tmp/q2-subsonic.play']=bytearray(f'2600 {STREAM}'.encode())
stream_songs('01 - Lunch.flac')
for _ in range(4): m.poll(); m.advance(0)
assert len(m.plays)==n+1 and m.play_names==['01 - Lunch.flac'],(len(m.plays),m.play_names)
assert m.files['/tmp/q2-subsonic.playing']==f'{STREAM}/01 - Lunch.flac\n'.encode(); passed()
# URL queues ("urls" in the caps; tidal.c app_urls_play): .play naming the app's own /tmp/q2-<id>.urls, a Q2URLS/1
# queue, plays it at once through stock's Tidal player: stock Tidal records (REC_TYPE 8, +0x38 0xf1), ids
# app:<gen>:<n>, the title as the name (Unknown for none), artist, album and cover URL when given, and REC_PATH ""
# for a URL or the card path for a song on the card; played as a Tidal page plays one (0xf1), from start=. A line
# with no URL is passed over. The session then ends: the queue is stock's.
URLS='/tmp/q2-deezer.urls'
m=DeezerMachine(); m.open(); m.files[DZPID]=bytearray(b'4242'); m.plays=[]
m.files[URLS]=bytearray(('#Q2URLS/1\tstart=1\nhttps://jf/a.flac?ApiKey=k\tOne\tBroadcast\tTender Buttons\thttps://jf/c.jpg\t200\n'
                         '/mnt/mmc/Music/b.flac\tTwo\n\tNo URL\t\t\t\t9\nhttp://jf/c.mp3\t\t\t\t\t\n').encode())
m.files['/tmp/q2-deezer.play']=bytearray(f'3200 {URLS}\n'.encode()); m.poll(); m.advance(0)
assert [p[2:4] for p in m.plays]==[(1,0xf1)] and m.natural==1 and m.get(payload_syms['press_natural'])==0,m.plays
def field(e,k): v=m.get(e+O[k]); return m.text(v) if v else None
urecs=m.play_recs
assert [(field(e,'REC_TIDAL_ID'),field(e,'REC_NAME'),field(e,'REC_TITLE'),field(e,'REC_ARTIST'),field(e,'REC_ALBUM'),
         field(e,'REC_COVER'),field(e,'REC_PATH')) for e in urecs]==[
    ('app:1:0','One','One','Broadcast','Tender Buttons','https://jf/c.jpg',''),
    ('app:1:1','Two','Two',None,None,None,'/mnt/mmc/Music/b.flac'),('app:1:2','Unknown','Unknown',None,None,None,'')],urecs
assert all(m.get(e+O['REC_TYPE'])==8 and m.get(e+0x38)==0xf1 for e in urecs)
m.poll(); m.advance(0); assert len(m.plays)==1; passed()
# Only the app's own queue in /tmp: another app's, or one elsewhere, is never read.
for seq,path in ((3201,'/tmp/q2-subsonic.urls'),(3202,'/tmp/evil.urls')):
    m.files[path]=m.files[URLS]; m.files['/tmp/q2-deezer.play']=bytearray(f'{seq} {path}\n'.encode()); m.poll(); m.advance(0)
assert len(m.plays)==1; passed()
# A track starting (get_tidal_trackurl, ringnav_tidal_track) never takes an app's record to stock's, so Tidal's API
# and its stream thread never see the id. Offline it gives up as stock does; online the player stops and the URL
# goes to stock's player (past tidal_start) on a thread of its own, which then pauses a start that was to stay
# paused and arms Now Playing's online tick (transposition). A later start drops an earlier one still waiting.
for a,n in ((payload_syms['stock_tidaltrack_trampoline'],'stock_tidaltrack'),(payload_syms['stock_tidal_trampoline'],'stock_tidal')):
    m.handlers[a]=n; m.u.hook_add(UC_HOOK_CODE,m.hook,begin=a,end=a)
m.mock('sem_post@GLIBC_2.2'); m.handlers[syms['tidal_playurl']]='tidal_playurl'; NET=syms['networkGetConnectStatus']
tidal_rec=m.song('Tidal'); m.word(tidal_rec+O['REC_TIDAL_ID'],m.string('12345'))
m.word(syms['mcl_pdeqplaylist'],m.deque([*urecs,tidal_rec]))
def track(pos):
    m.word(O['MCL_POS'],pos); m.threads=[]; m.calls=[]
    ret=m.call(address=HOOKS['get_tidal_trackurl'][0],args=(0,0,0,0),gap=0,clear=False)
    return ret,[c[0].split('@')[0] for c in m.calls if c[0] in ('networkGetConnectStatus','one:net','tidal_playurl','mclStop','stock_tidaltrack','sem_post@GLIBC_2.2')]
m.handlers[NET]='networkGetConnectStatus'  # 0: no link
assert track(0)==(0,['networkGetConnectStatus']) and not m.threads
m.handlers[NET]='one:net'; assert track(0)==(1,['one:net','tidal_playurl','mclStop']) and len(m.threads)==1
start,job=m.threads[0]; assert start==payload_syms['app_start']
m.byte(syms['savestartbt'],1); m.byte(syms['transposition'],0); m.calls=[]; m.call(address=start,args=(job,0,0,0),gap=0,clear=False)
assert [m.text(c[1]) for c in m.calls if c[0]=='stock_tidal']==['https://jf/a.flac?ApiKey=k'] and 'mclSetPause' in [c[0] for c in m.calls]
assert m.u.mem_read(syms['savestartbt'],1)[0]==0 and m.u.mem_read(syms['transposition'],1)[0]==1
assert track(0)[0]==1; first=m.threads[0]; assert track(2)[0]==1; m.calls=[]
m.call(address=first[0],args=(first[1],0,0,0),gap=0,clear=False); assert 'stock_tidal' not in [c[0] for c in m.calls]
m.call(address=m.threads[0][0],args=(m.threads[0][1],0,0,0),gap=0,clear=False)
assert [m.text(c[1]) for c in m.calls if c[0]=='stock_tidal']==['http://jf/c.mp3']; passed()
# A song on the card arms the auto-advance flag and returns at once: mclStartPlayer plays its REC_PATH as a local file.
assert track(1)==(1,['tidal_playurl']) and [c[1] for c in m.calls if c[0]=='tidal_playurl']==[1] and not m.threads; passed()
# A Tidal fetch in flight (tidalfurlstart) is stopped as stock does, whichever start this is, so it can't start over this one.
m.handlers[syms['curl_stop_httpget']]='curl_stop_httpget'; m.byte(syms['tidalfurlstart'],1); track(1)
assert 'curl_stop_httpget' in [c[0] for c in m.calls] and m.u.mem_read(syms['tidalfurlstart'],1)[0]==0; passed()
# A start still waiting is dropped when a queue has loaded since (the user's album), and by any later start, a card song's too.
assert track(0)[0]==1; job=m.threads[0]; m.word(payload_syms['playback_loads'],m.get(payload_syms['playback_loads'])+1)
m.calls=[]; m.call(address=job[0],args=(job[1],0,0,0),gap=0,clear=False); assert 'stock_tidal' not in [c[0] for c in m.calls]
assert track(0)[0]==1; job=m.threads[0]; assert track(1)[0]==1
m.calls=[]; m.call(address=job[0],args=(job[1],0,0,0),gap=0,clear=False); assert 'stock_tidal' not in [c[0] for c in m.calls]; passed()
# A queue loaded while the job is inside stock's start: it isn't paused, nor Now Playing's tick armed, over that queue.
assert track(0)[0]==1; job=m.threads[0]; m.handlers[payload_syms['stock_tidal_trampoline']]='stock_tidal_load'
m.byte(syms['savestartbt'],1); m.byte(syms['transposition'],0); m.calls=[]; m.call(address=job[0],args=(job[1],0,0,0),gap=0,clear=False)
assert 'mclSetPause' not in [c[0] for c in m.calls] and m.u.mem_read(syms['savestartbt'],1)[0]==1 and m.u.mem_read(syms['transposition'],1)[0]==0
m.handlers[payload_syms['stock_tidal_trampoline']]='stock_tidal'; m.byte(syms['savestartbt'],0); passed()
# A Tidal track is stock's, as before.
assert track(3)==(0,['stock_tidaltrack','sem_post']) and not m.threads; passed()
# A queue replaced since has no URL for the old ids: the player stays stopped.
m.files[URLS]=bytearray(b'#Q2URLS/1\nhttps://jf/d.flac\tThree\n'); m.call(address=payload_syms['app_urls_play'],args=(m.string(URLS),0,0,0),gap=0)
assert [p[2:4] for p in m.plays][-1]==(0,0xf1) and field(m.play_recs[0],'REC_TIDAL_ID')=='app:2:0'
assert track(0)==(1,['one:net','tidal_playurl','mclStop']) and not m.threads; passed()
# A queue whose header says now=1 tells its app which song plays (app_urls_at, for addon.c's .now): the n of the
# record at MCL_POS in the latest such 0xf1 queue; none (-1) for an older queue's song, a Tidal track, another
# queue class, or a queue that didn't ask.
NONE=0xffffffff; mtype=m.get(O['MCL_TYPE'])
m.files[URLS]=bytearray(b'#Q2URLS/1\tstart=0\tnow=1\nhttps://jf/d.flac\tThree\nhttps://jf/e.flac\tFour\n')
assert m.call(address=payload_syms['app_urls_play'],args=(m.string(URLS),0,0,0),gap=0)==1
m.word(syms['mcl_pdeqplaylist'],m.deque([*m.play_recs,urecs[0],tidal_rec])); m.word(O['MCL_TYPE'],0xf1)
def now_at(pos): m.word(O['MCL_POS'],pos); return m.call(address=payload_syms['app_urls_at'],args=(0,0,0,0),gap=0)&NONE
assert [now_at(p) for p in range(4)]==[0,1,NONE,NONE]
m.word(O['MCL_TYPE'],0xf001); assert now_at(1)==NONE; m.word(O['MCL_TYPE'],0xf1)
m.files[URLS]=bytearray(b'#Q2URLS/1\nhttps://jf/d.flac\tThree\n'); m.call(address=payload_syms['app_urls_play'],args=(m.string(URLS),0,0,0),gap=0)
m.word(syms['mcl_pdeqplaylist'],m.deque(m.play_recs)); assert now_at(0)==NONE
m.word(O['MCL_TYPE'],mtype); passed()
# Not a queue (no #Q2URLS/1 first), or no song in it: nothing plays.
n=len(m.plays)
for body in (b'https://jf/d.flac\tThree\n',b'#Q2URLS/1\tstart=0\n',b''):
    m.files[URLS]=bytearray(body); assert m.call(address=payload_syms['app_urls_play'],args=(m.string(URLS),0,0,0),gap=0)==0,body
assert len(m.plays)==n; passed()
# A URL or card path of 1024 bytes or more never reaches stock's strcpy into its 0x600 stack buffer (a } counted as the
# five it's expanded to, in place); start= counts the songs kept.
n=len(m.plays); m.files[URLS]=bytearray(('#Q2URLS/1\tstart=1\n'+f'https://jf/{"u"*1024}.flac\tBig\n'+f'/mnt/mmc/{"p"*1024}.flac\tBig\n'
    +f'https://jf/{"}"*300}\tBrace\n'+f'https://jf/{"u"*1012}\tOne\n'+f'/mnt/mmc/{"p"*1014}\tTwo\n').encode())
assert m.call(address=payload_syms['app_urls_play'],args=(m.string(URLS),0,0,0),gap=0)==1
assert m.text(payload_syms['au']+80)==URLS[:-5]+'.lyrics'
assert [p[2:4] for p in m.plays][n:]==[(1,0xf1)] and [field(e,'REC_TITLE') for e in m.play_recs]==['One','Two'],m.play_recs; passed()
# get_onlineid3info's cover request takes an app's REC_COVER as a URL (type 2), a Tidal record's as stock's (4).
m.handlers[syms['player_set_coverinfo']]='player_set_coverinfo'
def cover(r): m.calls=[]; m.call(address=payload_syms['app_coverinfo'],args=(m.string('c'),m.string('n'),4,r),gap=0,clear=False); return [c[3] for c in m.calls if c[0]=='player_set_coverinfo']
assert cover(urecs[0])==[2] and cover(tidal_rec)==[4] and cover(0)==[4]; passed()
# A line's item and heart (its 7th and 8th fields; tidal.c app_fav): the song's start sets stock's isFav_playing, which
# Now Playing's update (0x53d178) draws for a 0xf1 track; a song whose line named no item has none (0). Stock sets
# the flag only when a Tidal page checks its favourites, so a queue loading clears an app song's (app_fav_load).
m.files[URLS]=bytearray(b'#Q2URLS/1\nhttps://jf/e.flac\tFive\t\t\t\t\tid-e\t1\nhttps://jf/f.flac\tSix\n')
assert m.call(address=payload_syms['app_urls_play'],args=(m.string(URLS),0,0,0),gap=0)==1
m.word(syms['mcl_pdeqplaylist'],m.deque(m.play_recs)); m.byte(syms['isFav_playing'],0)
assert track(0)[0]==1 and m.u.mem_read(syms['isFav_playing'],1)[0]==1
assert track(1)[0]==1 and m.u.mem_read(syms['isFav_playing'],1)[0]==0
assert track(0)[0]==1 and m.u.mem_read(syms['isFav_playing'],1)[0]==1
m.call(address=payload_syms['app_fav_load'],args=(0,0,0,0),gap=0); assert m.u.mem_read(syms['isFav_playing'],1)[0]==0; passed()
# Library update (addon_library.c library_poll): the app's /tmp/q2-<id>.library, "<seq>" then scan and import lines,
# runs stock's Update Local Music (its dialog, which stops the player: not while one plays, nor while one is paused
# with the screen on), then stock's playlist import (which replaces a same-named playlist) for the listed files
# alone, once no Library list is open under it; each seq once, its result in .library.done.
LIB,DONE,EXP='/tmp/q2-deezer.library','/tmp/q2-deezer.library.done','/mnt/mmc/_explaylist_data'
def windows(m,*names):
    """The window manager's children: the status bar, Home, Streaming, the app's page, then names (open too)."""
    bar=m.get(syms['system_bar']) or m.node('system_bar','system_bar'); m.word(syms['system_bar'],bar)
    home=[m.node('window',n) for n in ('home_page','stream_page')]
    m.nodes[m.wm]=dict(type='window_manager',name='',children=[bar,*home,m.page,*(m.node('window',n) for n in names)],visible=1)
    m.open_pages=set(names)
m=DeezerMachine(); m.open(); windows(m); m.files[DZPID]=bytearray(b'4242'); m.files['/tmp/mmc_add']=bytearray()
m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(0xa50084,2); m.files[f'{EXP}/Navidrome - Mix.m3u']=bytearray(b'#EXTM3U\n')
m.loads=[]; m.u.hook_add(UC_HOOK_CODE,lambda u,a,s,x: (m.loads.append((u.reg_read(REGS[0]),len(m.imports))),m.returns(0)),
                         begin=(lc:=int(manifest['patch_symbols']['stock_localclass_trampoline'],16)),end=lc)
m.files[LIB]=bytearray(f'7\nscan\nimport {EXP}/Navidrome - Mix.m3u\n'.encode()); m.poll()
assert not [o for o in m.opened if o[0]=='dialog/updatemusic_dialog'] and DONE not in m.files  # playing: waits
m.word(0xa50084,3); m.poll()  # paused, the screen on: waits, so the song keeps its place
assert not [o for o in m.opened if o[0]=='dialog/updatemusic_dialog'] and DONE not in m.files
m.byte(syms['g_backlight_status'],0); m.poll(); m.byte(syms['g_backlight_status'],1)  # the screen off: it goes ahead
assert [o for o in m.opened if o[0]=='dialog/updatemusic_dialog'][-1]==('dialog/updatemusic_dialog',0,0)
m.word(O['SCAN_THREAD'],0x1234); m.word(O['SCAN_DONE'],0); windows(m,'updatemusic_page'); m.poll(); assert not m.imports
m.word(O['SCAN_DONE'],1); windows(m,'playlist_page'); m.poll(); assert not m.imports  # its list is reloaded: waits
windows(m); m.poll(); assert m.imports==[f'{EXP}/Navidrome - Mix.m3u\n'] and m.files[DONE]==b'7 ok\n',(m.imports,m.files.get(DONE))
assert m.loads==[(0xf00b,0)],m.loads  # Playlists' list loaded first: stock's replace looks a name up there alone
assert m.get(payload_syms['libup'])==0; m.poll(); assert len(m.imports)==1; passed()
def scan_starts(page):  # from here on the scan dialog's open starts its thread; a test ends it (SCAN_DONE)
    if page=='dialog/updatemusic_dialog': m.word(O['SCAN_THREAD'],0x1234); m.word(O['SCAN_DONE'],0)
m.on_open=scan_starts
# Import alone: no scan, the player left as it is (paused, the screen on, which holds a scan alone); it too waits
# while one plays.
m.word(0xa50084,2); n=len(m.opened); m.files[LIB]=bytearray(f'8\nimport {EXP}/Navidrome - Mix.m3u\n'.encode()); m.poll()
assert len(m.imports)==1; m.word(0xa50084,3); m.poll()
assert len(m.opened)==n and len(m.imports)==2 and m.files[DONE]==b'8 ok\n' and m.toasts[-1][3]=='Playlists updated',m.toasts[-1:]; passed()
# Errors, each at once and nothing run: a line it doesn't know, a file outside the folder or not an .m3u, one
# not there, no card.
for seq,body,why in (('9','rescan\n','Unknown request: rescan'),('10','import /mnt/mmc/Music/x.m3u\n','Not a playlist in /mnt/mmc/_explaylist_data: /mnt/mmc/Music/x.m3u'),
                     ('11',f'import {EXP}/../x.m3u\n',f'Not a playlist in /mnt/mmc/_explaylist_data: {EXP}/../x.m3u'),('12',f'import {EXP}/a/b.m3u\n',f'Not a playlist in /mnt/mmc/_explaylist_data: {EXP}/a/b.m3u'),
                     ('13',f'import {EXP}/Gone.m3u\n',f'Not found: {EXP}/Gone.m3u')):
    m.files[LIB]=bytearray(f'{seq}\n{body}'.encode()); m.poll(); assert m.files[DONE]==f'{seq} error {why}\n'.encode(),(seq,m.files[DONE]); passed()
assert len(m.imports)==2; m.import_ret=-2; m.files[LIB]=bytearray(f'14\nimport {EXP}/Navidrome - Mix.m3u\n'.encode()); m.poll()
assert m.files[DONE]==b'14 error No memory card\n'; m.import_ret=1
m.files.pop('/tmp/mmc_add'); m.files[LIB]=bytearray(b'15\nscan\n'); m.poll(); assert m.files[DONE]==b'15 error No memory card\n'; passed()
# Neither the import nor the scan starts while a scan runs or its dialog is open (the user's own Update Local
# Music), so no import races the scan's database writes and no second dialog opens; nor the import under a
# library page whose list it replaces (album_page among them).
m.files['/tmp/mmc_add']=bytearray(); m.word(0xa50084,1); m.word(O['SCAN_THREAD'],0x1234); m.word(O['SCAN_DONE'],0)  # stopped
k,n=len(m.imports),len(m.opened); m.files[LIB]=bytearray(f'16\nimport {EXP}/Navidrome - Mix.m3u\n'.encode()); m.poll(); assert len(m.imports)==k
m.word(O['SCAN_DONE'],1); windows(m,'updatemusic_page'); m.poll(); assert len(m.imports)==k  # its OK not pressed yet
windows(m,'album_page'); m.poll(); assert len(m.imports)==k
windows(m); m.poll(); assert len(m.imports)==k+1 and m.files[DONE]==b'16 ok\n',m.files[DONE]
windows(m,'updatemusic_page'); m.files[LIB]=bytearray(b'17\nscan\n'); m.poll(); assert len(m.opened)==n
windows(m); m.poll(); assert len(m.opened)==n+1; m.word(O['SCAN_DONE'],1); m.poll(); assert m.files[DONE]==b'17 ok\n'; passed()
# One check (library_safe) holds the scan and the import alike: a dialog (a toast's or a confirm's own loop polls
# too, and no request starts inside it), a page listing the library (stock's, whose lists the import reloads, and
# Q2 Muse's own, whose records the scan's database rebuild leaves stale), or something playing from any source
# other than the local player: the radio, AirPlay, the Bluetooth receiver.
radio=[0]; rs=payload_syms['radio_status']
m.u.hook_add(UC_HOOK_CODE,lambda u,a,s,x: radio[0] and m.returns(2),begin=rs,end=rs)
def busy(c,on):
    if c=='dialog': windows(m,*['msginfo_dialog']*on)
    elif c=='radio': radio[0]=on
    elif c=='airplay': m.airplay=2 if on else 0
    elif c=='bt': m.byte(syms['bt__recv_pageflag'],2 if on else 0)
    else: windows(m,*[c]*on)
seq=100
for c in ('dialog','album_page','playlist_page','playlistedit_page','coverflow_page','allmusic_page','artistinfo_page','mostplayed_page',
          'rated_page','longsongs_page','era_page','era_albums_page','radio','airplay','bt',
          'albuminfo_page','playerjumpinfo_page'):  # an album's songs (Albums', an artist's, Search's); Album info and Go to Album
    for body in ('scan\n',f'import {EXP}/Navidrome - Mix.m3u\n'):
        seq+=1; k,n=len(m.imports),len(m.opened); busy(c,1); m.files[LIB]=bytearray(f'{seq}\n{body}'.encode()); m.poll(); m.poll()
        assert (len(m.imports),len(m.opened))==(k,n) and not m.files[DONE].startswith(f'{seq} '.encode()),(c,body,m.files[DONE])
        busy(c,0); m.poll(); m.word(O['SCAN_DONE'],1); m.poll()
        assert m.files[DONE]==f'{seq} ok\n'.encode() and (len(m.imports),len(m.opened))==((k,n+1) if body=='scan\n' else (k+1,n)),(c,body,m.files[DONE])
passed()
# A seq alone asks for nothing: ok at once, no import and no toast. A playlist's .M3U in capitals imports.
k,t=len(m.imports),len(m.toasts); m.files[LIB]=bytearray(b'200\n'); m.poll(); assert m.files[DONE]==b'200 ok\n' and len(m.imports)==k and len(m.toasts)==t
m.files[f'{EXP}/Upper.M3U']=bytearray(b'#EXTM3U\n'); m.files[LIB]=bytearray(f'201\nimport {EXP}/Upper.M3U\n'.encode()); m.poll()
assert m.files[DONE]==b'201 ok\n' and m.imports[-1]==f'{EXP}/Upper.M3U\n',(m.files[DONE],m.imports[-1:]); passed()
# demo restarted (Rockbox from Home) keeps /tmp but not libup.taken: a seq .done already answers isn't run again.
tk=payload_syms['libup']+100; assert m.text(tk)=='deezer',m.text(tk)  # taken[0].id, after 100 bytes of libup
for o in range(0,40*8,4): m.word(tk+o,0)  # taken[APPS_MAX], each id[16] and seq[24]
k=len(m.imports); m.poll(); assert len(m.imports)==k and m.files[DONE]==b'201 ok\n' and m.text(tk)=='deezer'; passed()
# A playlist the import couldn't create (stock stops at a create that fails, a database error or the 100
# playlists' cap, and returns as if done) is no ok: Playlists' list, reloaded, must hold each file's name.
m.uncreated={'Navidrome - Mix'}; m.files[LIB]=bytearray(f'18\nimport {EXP}/Navidrome - Mix.m3u\n'.encode()); m.poll()
assert m.files[DONE]==b"18 error Couldn't import Navidrome - Mix\n" and m.toasts[-1][3]=="Couldn't import Navidrome - Mix",(m.files[DONE],m.toasts[-1:])
m.uncreated=set(); passed()
# Several files: each one's name checked on its own line (a dot of a later line isn't its own).
for n in ('Andreas','Gaming Nostalgia','X (Subsonic)'): m.files[f'{EXP}/{n}.m3u']=bytearray(b'#EXTM3U\n')
three=''.join(f'import {EXP}/{n}.m3u\n' for n in ('Andreas','Gaming Nostalgia','X (Subsonic)'))
m.files[LIB]=bytearray(f'19\n{three}'.encode()); m.poll(); assert m.files[DONE]==b'19 ok\n',m.files[DONE]
m.uncreated={'Gaming Nostalgia'}; m.files[LIB]=bytearray(f'20\n{three}'.encode()); m.poll()
assert m.files[DONE]==b"20 error Couldn't import Gaming Nostalgia\n",m.files[DONE]; m.uncreated=set(); passed()
# Its toast (stock's msginfo_dialog, 1.5 s as toast() opens it, which runs a loop of its own that polls again) comes once the result
# is written and while library_poll is busy, so a nested poll runs nothing: each seq once, ok or error.
snaps=[]; m.on_toast=lambda: snaps.append((bytes(m.files.get(DONE,b'')),m.get(payload_syms['libup']+4)))
m.files[LIB]=bytearray(f'21\nimport {EXP}/Andreas.m3u\n'.encode()); m.poll(); assert snaps==[(b'21 ok\n',1)],snaps
m.uncreated={'Andreas'}; m.files[LIB]=bytearray(f'22\nimport {EXP}/Andreas.m3u\n'.encode()); m.poll(); n=len(m.imports)
assert snaps[-1]==(b"22 error Couldn't import Andreas\n",1) and m.toasts[-1][1:]==(1,1500,"Couldn't import Andreas"),(snaps,m.toasts[-1])
m.poll(); m.poll(); assert len(m.imports)==n and len(snaps)==2 and m.get(payload_syms['libup']+4)==0; m.on_toast=None; m.uncreated=set(); passed()
# The import's listing (stock importPlayList's toolsGetDirectoryM3u8 call, patched to addon_m3u_dir): stock's
# whole folder, as its own Import Playlists row has it, unless a card app's request is importing: then only
# the files it named, so the user's own exports there aren't imported over playlists edited since.
from build import M3U_DIR_CALL
recs=[m.song(n) for n in ('Navidrome - Mix.m3u','Mine.m3u')]
for r in recs: m.word(r+O['REC_PATH'],m.string(f'{EXP}/'+m.text(m.get(r+O['REC_NAME']))))
m.m3u_files=recs; m.u.hook_add(UC_HOOK_CODE,lambda u,a,s,x: u.reg_write(UC_MIPS_REG_PC,0x1000000),begin=M3U_DIR_CALL+8,end=M3U_DIR_CALL+8)
def listing(): dq=m.deque([]); m.call(address=M3U_DIR_CALL,args=(m.string(EXP)-0x12e0,dq,0,0),gap=0); return [m.text(m.get(e+O['REC_NAME'])) for e in m.items(dq)]
assert listing()==['Navidrome - Mix.m3u','Mine.m3u']
def only(*paths): return m.string(''.join('i'+p.rsplit('/',1)[1]+'\0' for p in paths))  # libup.only, an empty name last
m.word(payload_syms['libup'],only(f'{EXP}/Navidrome - Mix.m3u')); assert listing()==['Navidrome - Mix.m3u']; m.word(payload_syms['libup'],0); passed()
# add and import (R35's request lines): import replaces a playlist of the same name (the app's own, imported
# before); add imports a name no playlist has yet, else leaves that playlist alone (its file isn't listed for
# stock's import, which never runs when nothing is left, so its rows stay as they are) and reports it, one
# "exists <path>" line after the result. Playlists' list is the one loaded for the import (mocked: set here).
def playlists(*names): m.word(syms['p_deque_playlist'],m.deque([m.song(n) for n in names]))
def imported(): m.word(payload_syms['libup'],only(*m.imports[-1].split('\n')[:-1])); got=listing(); m.word(payload_syms['libup'],0); return got
recs=[m.song(n) for n in ('Mine.m3u','New.m3u','Mix.m3u')]
for r in recs: m.word(r+O['REC_PATH'],m.string(f'{EXP}/'+m.text(m.get(r+O['REC_NAME'])))); m.files[m.text(m.get(r+O['REC_PATH']))]=bytearray(b'#EXTM3U\n')
m.m3u_files=recs
playlists('Mine'); k=len(m.imports); m.files[LIB]=bytearray(f'300\nadd {EXP}/New.m3u\n'.encode()); m.poll()
assert m.files[DONE]==b'300 ok\n' and len(m.imports)==k+1 and imported()==['New.m3u'],(m.files[DONE],m.imports[k:]); passed()
playlists('Mine'); pl=m.get(syms['p_deque_playlist']); k,t=len(m.imports),len(m.toasts)
m.files[LIB]=bytearray(f'301\nadd {EXP}/Mine.m3u\n'.encode()); m.poll()
assert m.files[DONE]==f'301 ok\nexists {EXP}/Mine.m3u\n'.encode() and len(m.imports)==k and len(m.toasts)==t,(m.files[DONE],m.imports[k:])
assert m.get(syms['p_deque_playlist'])==pl and m.names(pl)==['Mine']; passed()
playlists('Mine'); m.files[LIB]=bytearray(f'302\nimport {EXP}/Mine.m3u\n'.encode()); m.poll()
assert m.files[DONE]==b'302 ok\n' and imported()==['Mine.m3u'],m.files[DONE]; passed()
# A mixed request: the scan, then the import's files (an import, an add whose name is new) in one import, the
# add whose name a playlist has left alone and reported; the toast as for any import.
playlists('Mine','Mix'); n,k=len(m.opened),len(m.imports)
m.files[LIB]=bytearray(f'303\nscan\nimport {EXP}/Mix.m3u\nadd {EXP}/New.m3u\nadd {EXP}/Mine.m3u\n'.encode()); m.poll(); m.word(O['SCAN_DONE'],1); m.poll(); m.poll()
assert len(m.opened)==n+1 and len(m.imports)==k+1 and imported()==['New.m3u','Mix.m3u'] and m.toasts[-1][3]=='Playlists updated'
assert m.files[DONE]==f'303 ok\nexists {EXP}/Mine.m3u\n'.encode(),m.files[DONE]; passed()
# The exists lines follow an error too: a file the import couldn't create.
playlists('Mine'); m.uncreated={'New'}; m.files[LIB]=bytearray(f'304\nadd {EXP}/New.m3u\nadd {EXP}/Mine.m3u\n'.encode()); m.poll()
assert m.files[DONE]==f"304 error Couldn't import New\nexists {EXP}/Mine.m3u\n".encode(),m.files[DONE]; m.uncreated=set(); passed()
# A seq is 23 bytes at most: a longer one is answered "<seq> error Seq too long" and never run, its answer written
# once (not every poll), and another long seq gets its own even when they start alike. A seq with no newline after
# it ends at the file's end: a seq alone, ok.
LONG='9'*24; n=len(m.opened); m.files[LIB]=bytearray(f'{LONG}\nscan\n'.encode()); m.poll()
assert m.files[DONE]==f'{LONG} error Seq too long\n'.encode() and len(m.opened)==n,m.files[DONE]
d=m.files[DONE]; m.poll(); assert m.files[DONE] is d
m.files[LIB]=bytearray(f'{LONG}8\nscan\n'.encode()); m.poll(); assert m.files[DONE]==f'{LONG}8 error Seq too long\n'.encode() and len(m.opened)==n
m.files[LIB]=bytearray(('8'*23+'\n').encode()); m.poll(); assert m.files[DONE]==('8'*23+' ok\n').encode(),m.files[DONE]
m.files[LIB]=bytearray(b'305'); m.poll(); assert m.files[DONE]==b'305 ok\n',m.files[DONE]; passed()
# A request holds up to LIB_MAX (32 KB): 200 imports of the longest names (63 bytes, 93-byte paths) run (about 20 KB,
# which the old 2 KB of paths refused whole); a longer one is "Request too long", nothing run. A name with an
# ellipsis is a playlist like any other (only a "/" would leave the folder).
many=[f'Playlist {i:03} '+'n'*50 for i in range(200)]
for x in many: m.files[f'{EXP}/{x}.m3u']=bytearray(b'#EXTM3U\n')
body=''.join(f'import {EXP}/{x}.m3u\n' for x in many); m.files[LIB]=bytearray(f'306\n{body}'.encode()); m.poll()
assert len(f'{EXP}/{many[0]}.m3u')==93 and len(body)>20000 and m.files[DONE]==b'306 ok\n' and m.imports[-1]==body.replace('import ',''),m.files[DONE]
k=len(m.imports); m.files[LIB]=bytearray(b'307\n'+b'scan\n'*7000); m.poll(); assert m.files[DONE]==b'307 error Request too long\n' and len(m.imports)==k
m.files[f'{EXP}/Hello... (Live).m3u']=bytearray(b'#EXTM3U\n'); m.files[LIB]=bytearray(f'308\nimport {EXP}/Hello... (Live).m3u\n'.encode()); m.poll()
assert m.files[DONE]==b'308 ok\n' and m.imports[-1]==f'{EXP}/Hello... (Live).m3u\n',m.files[DONE]; passed()
# A full /tmp (a write that comes up short, or a close that fails, as a buffered write's flush does): the answer
# is renamed only whole, else its exists lines could be cut off and the app would take a user's same-named playlist
# for its own. The temp goes and "<seq> error Couldn't write the answer" is published the same way; if that can't
# be written either, nothing is published (the old answer stays).
TMP='/tmp/q2-library.tmp'; cut=b"error Couldn't write the answer\n"
for seq,cap,closes,want in (('309',40,0,cut),('310',1000,1,cut),('311',10,0,None),('312',1000,2,None)):
    playlists('Mine'); old=bytes(m.files[DONE]); m.write_cap,m.close_fails=cap,closes
    m.files[LIB]=bytearray(f'{seq}\nadd {EXP}/New.m3u\nadd {EXP}/Mine.m3u\n'.encode()); m.poll()
    assert m.files[DONE]==(f'{seq} '.encode()+want if want else old) and TMP not in m.files,(seq,m.files[DONE],TMP in m.files)
m.write_cap,m.close_fails=None,0; m.word(O['SCAN_THREAD'],0); m.word(O['SCAN_DONE'],0); passed()  # no scan left running
# library_poll runs once each addon_poll pass for every app on the card, launched or not: an app that exits right
# after its sync (no pid file for ADDON_START_MS: poll_one lets it go and polls it no more) still has its request run.
def lib_machine():
    """Deezer's page open, stopped, a card in, Mix.m3u in the folder, stock's Playlists load mocked."""
    m=DeezerMachine(); m.open(); m.files['/tmp/mmc_add']=bytearray(); m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(0xa50084,1)
    lc=int(manifest['patch_symbols']['stock_localclass_trampoline'],16); m.u.hook_add(UC_HOOK_CODE,lambda u,a,s,x: m.returns(0),begin=lc,end=lc)
    m.files[f'{EXP}/Mix.m3u']=bytearray(b'#EXTM3U\n'); windows(m)
    return m
m=lib_machine(); m.poll(11000); assert DZPID not in m.files
m.files[LIB]=bytearray(f'400\nimport {EXP}/Mix.m3u\n'.encode()); m.poll()
assert m.files.get(DONE)==b'400 ok\n' and m.imports==[f'{EXP}/Mix.m3u\n'],(m.files.get(DONE),m.imports); passed()
# A new seq's request is read whole once, and that read's own first line must be the seq. One replaced between the
# poll's cheap first-line read and the whole read runs its own lines under its own seq, at the next poll; one gone
# between them is answered nothing (either was answered "<seq> ok": seq 500 with 501's lines, or with none).
m=lib_machine(); scans=lambda: [o for o in m.opened if o[0]=='dialog/updatemusic_dialog']
def second_open(new):
    opens=[]
    def hook(path):
        if path!=LIB: return
        opens.append(path)
        if len(opens)==2 and new is None: m.files.pop(LIB)
        elif len(opens)==2: m.files[LIB]=bytearray(new)
    m.on_fopen=hook
second_open(f'501\nimport {EXP}/Mix.m3u\n'.encode()); m.files[LIB]=bytearray(b'500\nscan\n'); m.poll()
assert DONE not in m.files and not m.imports and not scans(),(m.files.get(DONE),m.imports)
m.poll(); assert m.files[DONE]==b'501 ok\n' and m.imports==[f'{EXP}/Mix.m3u\n'] and not scans(),(m.files[DONE],m.imports)
second_open(None); m.files[LIB]=bytearray(b'502\nscan\n'); m.poll(); m.poll()
assert m.files[DONE]==b'501 ok\n' and not scans(),m.files[DONE]; m.on_fopen=None; passed()
# CRLF lines are read as LF lines.
m.files[LIB]=bytearray(f'503\r\nimport {EXP}/Mix.m3u\r\n'.encode()); m.poll()
assert m.files[DONE]==b'503 ok\n' and len(m.imports)==2,m.files[DONE]; passed()
# An empty first line has no seq to run under: answered " error Missing seq" (the seq empty) once, as a seq too long
# is, never run.
m.files[LIB]=bytearray(b'\r\nscan\n'); m.poll(); assert m.files[DONE]==b' error Missing seq\n' and not scans(),m.files[DONE]
d=m.files[DONE]; m.poll(); assert m.files[DONE] is d; passed()
# A playlist's name (its file's less .m3u) of 64 bytes or more is refused, nothing run: stock's create cuts it into
# char title[64] and skips its duplicate check, so the cut name never matches the file again and every sync would
# add a copy. 63 bytes import, as do 21 three-byte characters; 22 don't.
for x in ('n'*63,'n'*64,'音'*21,'音'*22): m.files[f'{EXP}/{x}.m3u']=bytearray(b'#EXTM3U\n')
k,n=len(m.imports),len(scans())
m.files[LIB]=bytearray(f'504\nscan\nimport {EXP}/{"n"*64}.m3u\n'.encode()); m.poll()
assert m.files[DONE]==f'504 error Name too long: {"n"*64}\n'.encode() and len(scans())==n,m.files[DONE]
m.files[LIB]=bytearray(f'505\nadd {EXP}/{"音"*22}.m3u\n'.encode()); m.poll(); assert m.files[DONE]==f'505 error Name too long: {"音"*22}\n'.encode()
m.files[LIB]=bytearray(f'506\nimport {EXP}/{"n"*63}.m3u\nadd {EXP}/{"音"*21}.m3u\n'.encode()); m.poll()
assert m.files[DONE]==b'506 ok\n' and m.imports[k:]==[f'{EXP}/{"n"*63}.m3u\n{EXP}/{"音"*21}.m3u\n'],(m.files[DONE],m.imports[k:]); passed()
# A scan's request ends only once its scan was seen, its thread running or its dialog open (right after the
# dialog's call, which returns at once here; on a device the dialog's own loop polls while it runs): a dialog that
# never opened, no scan seen, is "<seq> error Couldn't start the scan", not an ok without a scan.
m=lib_machine(); m.files[LIB]=bytearray(b'507\nscan\n'); m.poll(); assert len(scans())==1 and DONE not in m.files
m.poll(); assert m.files[DONE]==b"507 error Couldn't start the scan\n",m.files[DONE]
m.on_open=lambda page: m.open_pages.add('updatemusic_dialog')  # stays open after the call: seen at once
m.files[LIB]=bytearray(b'508\nscan\n'); m.poll(); m.poll(); assert m.files[DONE].startswith(b'507 ')
m.open_pages.discard('updatemusic_dialog'); m.poll(); assert m.files[DONE]==b'508 ok\n',m.files[DONE]; m.on_open=None; passed()

# The scan and the import run only while every open window (the window manager's children, the status bar aside)
# is one known to hold no library records: Home, Streaming and its Configuration, a card app's page and its QR code,
# Now Playing, and System and Playback settings' own pages, all together here. Any other holds the request until it
# closes: a page this list doesn't know (a new one's), the App Store, the Library menu, an app's
# songs and Up Next among them.
SAFE=['home_page','stream_page','streamconf_page','streamsvc_page','addon_page','qrcode_page','playing_page','sysset_page','display_page',
      'backlight_page','lighttime_page','keylock_page','language_page','datetime_page','autotime_page','manualtime_page',
      'standby_page','powermanager_page','sleepshutdown_page','about_page','bluetooth_page',
      'btquality_page','btadjustvol_page','wifi_page','wifiset_page','netservice_page','playset_page','audiosetting_page',
      'playmode_page','replaygain_page','filter_page','dsdoutput_page','memplay_page','usbvol_page','maxvol_page',
      'bootvol_page','balance_page','equalizer_page']
m=lib_machine(); seq=600
for page in ('mystery_page','apps_page','localmusic_page','app_songs','upnext_page'):
    seq+=1; windows(m,page); k=len(m.imports); m.files[LIB]=bytearray(f'{seq}\nimport {EXP}/Mix.m3u\n'.encode()); m.poll(); m.poll()
    assert len(m.imports)==k and not m.files.get(DONE,b'').startswith(f'{seq} '.encode()),(page,m.files.get(DONE))
    windows(m); m.poll(); assert m.files[DONE]==f'{seq} ok\n'.encode() and len(m.imports)==k+1,(page,m.files[DONE])
windows(m,*SAFE); m.files[LIB]=bytearray(f'700\nimport {EXP}/Mix.m3u\n'.encode()); m.poll(); assert m.files[DONE]==b'700 ok\n',m.files[DONE]; passed()

# About: Firmware (stock's FW. Version) shows the stock firmware's version again, not the updater tag in demo's
# literal, and a row named for the product (tools/build.py PRODUCT_NAME) with this build's version follows it. Stock's own row builder (0x4c0de4) builds Model and FW.
# Version, so the added row is checked against the real stock widgets, geometry and styles. A page of another
# count of rows (V1.32's seven) is left as stock built it. Stock's thanks, V1.36's eighth child, is dropped.
m=ShuffleMachine(); m.handlers[int(manifest['patch_symbols']['stock_about_trampoline'],16)]='stock_about'
m.mock('strcpy@GLIBC_2.0','widget_set_tr_text','strstr@GLIBC_2.0')  # strstr: About's value words (settings_pages.c)
def tree(w):
    n=m.nodes[w]
    return (n['type'],[m.get(w+O[k]) for k in ('W_X','W_Y','W_W','W_H')],n.get('style'),
            n.get('hscroll_label_set_only_focus'),n.get('hscroll_label_set_ellipses'),[tree(c) for c in n['children']])
view=m.node('scroll_view','scroll_view_about'); m.top=m.node('window','about_page',[view])
for i in range(8):  # stock's seven rows and its thanks, the eighth child
    row=m.node('list_item'); m.nodes[view]['children'].append(row)
    if i<2: assert m.call(address=0x4c0de4,args=(row,i,0,0),gap=0)==0
rows=list(m.nodes[view]['children'])
assert m.call(address=HOOKS['systemset_about_page_init'][0],args=(m.top,5,0,0),gap=0)==0 and m.calls[0][:3]==('stock_about',m.top,5)
kids=m.nodes[view]['children'][1 if manifest['dev'] else 0:]  # a --dev build's Save Diagnostics row heads it (diagnostics.c)
assert kids[:2]==rows[:2] and kids[3:]==rows[2:7]
fw=m.nodes[m.nodes[rows[1]]['children'][0]]['children'][1]; assert m.nodes[fw]['text']=='V1.36'
item=kids[2]; button=m.nodes[item]['children'][0]; title,value=m.nodes[button]['children']
# Stock's row, title and value widgets alike.
want=tree(m.nodes[rows[1]]['children'][0])
assert m.nodes[item]['style']=='s_listitem_black' and tree(button)==want
assert m.nodes[title]['text']==PRODUCT_NAME and m.nodes[value]['text']==f"{VERSION}{f" dev {manifest['dev']}" if manifest['dev'] else f' beta {BETA}' if BETA else ''}"
assert not m.nodes[button].get('handlers') and not m.nodes[button].get('name'); passed()
m.nodes[view]['children']=rows[:7]; assert m.call(address=HOOKS['systemset_about_page_init'][0],args=(m.top,5,0,0),gap=0)==0 and m.nodes[view]['children']==rows[:7]; passed()

# Software Update's download (netupdate.c): stock's System Update page's second row, built by stock's own
# row builder (0x4c8660), is renamed "2" so stock's click (atoi of the name: 0 TF card, 1 Shanling) skips
# it, and opens Software Update (sw_update.c). Its download fetches the latest GitHub release's
# update.tar.sig, then its update.tar over verified TLS to the card (a .part, renamed once its signature
# checks, docs/internals.md#release-signatures). A failure keeps the old update.tar.
# The payload's release key is the manifest's; these checks swap in a key made for this run, whose private half signs.
import signing
assert (B/'patch.bin').read_bytes()[payload_syms['release_key']-BASE:][:32].hex()==manifest['signing_key']
KEY_DIR=pathlib.Path(tempfile.mkdtemp(prefix='q2-patch-key-'))
subprocess.run(['openssl','genpkey','-algorithm','ed25519','-out',str(KEY_DIR/'k.pem')],check=True,capture_output=True)
TEST_PEM=(KEY_DIR/'k.pem').read_text()
TEST_PUB=signing.public_key(subprocess.run(['openssl','pkey','-in',str(KEY_DIR/'k.pem'),'-pubout'],check=True,capture_output=True).stdout)
(KEY_DIR/'k.pem').unlink(); KEY_DIR.rmdir()
def test_key(m): m.u.mem_write(payload_syms['release_key'],TEST_PUB)
def update_sig(tar,tag): return signing.sign(f'q2-coda-update-v1 {tag} {hashlib.sha512(tar).hexdigest()}\n'.encode(),TEST_PEM)
