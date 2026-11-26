# Mock only player endpoints, exercising the actual routing and ear/AVRCP state machine.
class HeadsetMachine(Machine):
    def __init__(self,owner=1):
        super().__init__(); self.owner=owner; self.playing=True; self.session=42; self.actions=[]; self.saved=0
        self.mock('strstr@GLIBC_2.0')  # guest.c guest_key reads Guest Play's state fields
        self.mock('snprintf@GLIBC_2.0')  # headset.c avrcp_say's line, which ulog (no-op here) takes
        self.handlers[payload_syms['ulog']]='hs:ulog'
        self.handlers[syms['mclGetSavePlayStatus']]='hs:saved'
        for name in ('spot_status','radio_status','video_status','spot_session','radio_session','video_session',
                     'spot_set_playing','radio_set_playing','video_set_playing','spot_media','radio_media','video_key'):
            self.handlers[payload_syms[name]]='hs:'+name
        self.handlers[syms['mclGetPlayStatus']]='hs:local_status'
        self.handlers[syms['player_play_pause']]='hs:local_toggle'
        self.handlers[syms['player_next_music']]='hs:local_next'
        self.handlers[syms['player_prev_music']]='hs:local_prev'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('hs:'): return super().hook(u,address,size,unused)
        name=name[3:]; a=u.reg_read(REGS[0]); ret=0
        owner=4 if name.startswith('video') else 3 if name.startswith('radio') else 2 if name.startswith('spot') else 1
        if name=='ulog': pass
        elif name=='saved': ret=self.saved
        elif name.endswith('_status'): ret=(2 if self.playing else 3) if self.owner==owner else 0
        elif name.endswith('_session'): ret=self.session
        elif name.endswith('_set_playing'): self.playing=bool(a); self.actions.append((owner,'play' if a else 'pause'))
        elif name=='local_toggle': self.playing=not self.playing; self.actions.append((1,'play' if self.playing else 'pause'))
        else: self.actions.append((owner,a if owner!=1 else name)); ret=int(name.endswith('_media') and self.owner==owner)
        self.returns(ret)
    def fn(self,name,value=0,gap=0): return self.call(address=payload_syms[name],args=(value,0,0,0),gap=gap)
    def ears(self,mask,gap=100): return self.fn('headset_ears',4|mask,gap)
    def command(self,code,gap=200): return self.fn('headset_command',code,gap)
for owner in (1,2,3,4):
    m=HeadsetMachine(owner); m.byte(syms['g_backlight_status'],0)
    m.ears(3); assert m.playing and not m.actions
    m.ears(2); assert not m.playing and m.actions==[(owner,'pause')]
    m.command(201); m.command(164); assert not m.playing and len(m.actions)==1
    m.ears(0); m.ears(1); assert not m.playing  # other bud alone does not resume
    m.ears(3); assert m.playing and m.actions[-1]==(owner,'play')
    m.ears(2); m.fn('headset_manual'); m.ears(3); assert not m.playing
    m=HeadsetMachine(owner); m.ears(3); m.command(201); m.ears(2); m.ears(3); assert m.playing
    m=HeadsetMachine(owner); m.ears(0); m.ears(3); assert not m.playing  # initial report establishes state
    m=HeadsetMachine(owner); m.ears(3); m.ears(2); m.owner=1 if owner!=1 else 2; m.ears(3); assert not m.playing
    m=HeadsetMachine(owner); m.ears(3); m.ears(2)
    if owner!=1: m.session+=1
    else: m.word(O['MCL_POS'],1)
    m.ears(3); assert not m.playing
    m=HeadsetMachine(owner); m.command(163); m.command(163,gap=1); assert len(m.actions)==1
    m.command(165); assert len(m.actions)==2 and all(a[0]==owner for a in m.actions)
    passed()
m=HeadsetMachine(); good=bytes([4,0,4,0,6,0,0,2])
for packet in (b'',good[:7],good+b'\0',bytes([4,0,4,0,6,0,3,0]),bytes([4,1,4,0,6,0,0,0])):
    p=m.alloc(max(1,len(packet))); m.u.mem_write(p,packet)
    assert m.call(address=payload_syms['ringnav_aap_parse'],args=(p,len(packet),0,0),gap=0)==0
p=m.alloc(8); m.u.mem_write(p,good); assert m.call(address=payload_syms['ringnav_aap_parse'],args=(p,8,0,0),gap=0)==2; passed()
# Ears are counted: the report's primary/secondary slots swap. One worn AirPod out pauses and back
# (now the other slot) resumes; a swap alone does nothing; a manual command still cancels resume.
for owner in (1,2,3,4):
    m=HeadsetMachine(owner); m.ears(1); m.ears(2); assert m.playing and not m.actions
    m.ears(0); assert not m.playing and m.actions==[(owner,'pause')]
    m.ears(2); assert m.playing and m.actions[-1]==(owner,'play')
    m.ears(1); m.ears(3); m.ears(2); assert not m.playing  # two worn: primary out, slots swapped
    m.ears(3); assert m.playing
    m.ears(1); m.fn('headset_manual'); m.ears(3); assert not m.playing
    passed()

# The headset's level reads as the Q2 volume nearest by bt_vol_q2 (docs/internals.md#headset-controls-and-volume);
# past the limit the headset is pulled back to the limit's level (40 is 51), else its own level stays.
for absolute,limit,want,level in ((127,40,40,51),(0,100,0,0),(64,100,50,64),(1,100,1,1),(126,100,99,126)):
    b=BtVolumeMachine(); b.poll(); b.byte(syms['g_maxvolume'],limit)
    b.absolute=absolute; b.poll()
    assert b.u.mem_read(syms['g_volume'],1)[0]==want
    assert b.absolute==level
    b.poll(); assert not b.writes()
    passed()
# A headset step that already reads as the Q2 volume stays: 2 is volume 1 (as near as 2, the lower), never rewritten.
b=BtVolumeMachine(); b.poll(); b.absolute=2; b.poll()
assert b.u.mem_read(syms['g_volume'],1)[0]==1 and not b.writes() and b.absolute==2
assert b.volume(1,1)==[(100,100)] and not b.writes(); b.poll(); assert not b.writes(); passed()
print('M4B recognition/resume and headset routing, ear masks, manual cancellation, malformed packets and volume limits passed.')

# Real helper APIs: explicit pause/resume is observable and repeatable, with shared gain changes.
def endpoint(m,name,value=0):
    return m.call(address=payload_syms[name],args=(value,0,0,0),gap=0,clear=False)
def ready_gain(m,ready,volume):
    m.way=1; m.config['BTVOLLINK']='1'; m.word(payload_syms['bt_vol_link'],1)  # Link Volume on, read or not
    for name in ('g_bluetoothflag','bt_linkstatus'): m.byte(syms[name],1)
    m.word(syms['bt_showcoding'],1); m.byte(syms['g_volume'],volume)
    m.word(payload_syms['btvol']+4 if 'btvol' in payload_syms else payload_syms['btvol.1'],ready)  # clang may split scalar fields
m=SpotMachine({BIN:bytearray()}); spot_open(m,stream_page(m)); m.state('playing'); m.poll()
for playing,command in ((0,'s'),(1,'P')):
    m.sent=[]; endpoint(m,'spot_set_playing',playing); endpoint(m,'spot_set_playing',playing)
    assert [x[1] for x in m.sent]==[command] and endpoint(m,'spot_status')==(2 if playing else 3)
ready_gain(m,1,32); m.now+=500; m.sent=[]; endpoint(m,'spot_poll'); assert m.sent[-1][1]=='v'+chr(100)
ready_gain(m,1,0); m.now+=500; endpoint(m,'spot_poll'); assert m.sent[-1][1]=='v\0'
ready_gain(m,0,32); m.now+=500; endpoint(m,'spot_poll'); assert m.sent[-1][1]=='v'+chr(32)
passed()
m=RadioMachine(); radio_open(m); m.row(1); m.row(0)
assert endpoint(m,'radio_status')==2
m.waited=m.forked
endpoint(m,'media_set_playing',0); endpoint(m,'media_set_playing',0); assert endpoint(m,'radio_status')==3
m.waited=0
endpoint(m,'media_set_playing',1); assert endpoint(m,'radio_status')==2
ready_gain(m,1,32); m.sent=[]; m.now+=500; endpoint(m,'radio_poll'); assert m.sent[-1][1]=='v'+chr(100)
ready_gain(m,0,32); m.sent=[]; m.now+=500; endpoint(m,'radio_poll'); assert m.sent[-1][1]=='v'+chr(32)
passed()
# Streaming's Configuration (docs/internals.md#streaming-configuration), after AirPlay: Tidal's and
# Spotify's cache caps cycle 512 MB to 8 GB from 2 GB, kept in their folders' .cache-size in
# librespot's form, which tidal.c's trim and the Spotify launcher read. Stop Streaming Services ends
# librespot and its loop, a station and a stock stream, and the keys are local music's again.
TCAP,SCAP='/mnt/mmc/.tidal/.cache-size','/mnt/mmc/.spotify/.cache-size'
m=RadioMachine({BIN:bytearray()}); spot_open(m,stream_page(m)); m.state('paused'); m.poll()
radio_open(m); m.row(1); m.row(0); assert endpoint(m,'radio_status')==2
m.top=m.node('window','home_page'); m.stream(); view=m.nodes[m.top]['children'][0]
conf=m.nodes[m.nodes[view]['children'][5]]['children'][0]; icon,label=m.nodes[conf]['children'][:2]
assert m.nodes[icon]['image']=='stream_config' and m.nodes[label]['text']=='Configuration'
m.click(conf); m.page=m.top; assert m.nodes[m.page]['name']=='streamconf_page'
assert m.labels()[:9]==['Configuration','Stop Streaming Services','Download Ahead','Services','TIDAL','Spotify','Show in Streaming','Internet Radio','AirPlay']
conf=m.page; m.row(3); m.page=m.top; assert m.nodes[m.page]['name']=='streamsvc_page'  # TIDAL's page
assert m.labels()==['TIDAL','Show in Streaming','Show Downloads','Cache','2 GB']
m.row(2); assert m.files[TCAP]==b'4096M' and m.labels()[-1]=='4 GB' and endpoint(m,'stream_cache',0)==3
m.row(2); m.row(2); assert m.files[TCAP]==b'512M' and m.labels()[-1]=='512 MB'
f,ctx=m.handler(m.page,O['EVT_DESTROY']); m.call(address=f,args=(ctx,0,0,0),gap=0,clear=False)
m.page=conf; m.row(4); m.page=m.top  # Spotify's page
m.row(1); assert m.files[SCAP]==b'4096M' and '4 GB' in m.labels() and endpoint(m,'stream_cache',1)==3
m.files[SCAP]=bytearray(b'9G'); assert endpoint(m,'stream_cache',1)==2  # not a choice: 2 GB
m.word(O['MCL_TYPE'],5); m.play=2; m.waited=m.forked
m.page=conf; m.row(0); m.top=m.node('window','home_page')
assert 'killall -9 librespot aplay' in m.systems[-1] and 'player_stop' in [c[0] for c in m.calls]
assert [m.text(c[2]+8) for c in m.calls if c[0]=='navigator_to_with_context']==['Streaming stopped']
assert endpoint(m,'radio_status')==0 and endpoint(m,'spot_status')==0 and m.key(O['KEY_PLAY'])!=11; m.close(); passed()
# Erase Spotify Data, under Cache on Spotify's page, asks on the confirmation card (feedback.c confirm; its answer,
# stream_config.c sc_erase_answer, run as feedback.c would once the card has closed). Erase erases Spotify's login and
# cache (and .cache-size, so Spotify's Cache reads 2 GB again) on the App Store's thread, Spotify stopped first; a press
# while it runs says to wait.
def conf_open(m):
    m.top=m.node('window','home_page'); m.stream(); view=m.nodes[m.top]['children'][0]
    m.click(m.nodes[m.nodes[view]['children'][5]]['children'][0]); m.page=m.top; m.row(4); m.page=m.top  # Spotify's page
def erase_answer(m,row): m.call(address=payload_syms['sc_erase_answer'],args=(0,row,0,0),gap=0,clear=False)
m=ShelfMachine({SCAP:bytearray(b'4096M')}); conf_open(m)
assert m.labels()==['Spotify','Show in Streaming','Cache','Erase Spotify Data','4 GB']
m.row(2); assert m.labels()[3]=='Erase Spotify Data' and not m.threads  # asked, nothing erased yet
erase_answer(m,0); assert not m.threads  # Cancel
erase_answer(m,1); assert m.labels()[3]=='Erasing…' and len(m.threads)==1 and KILL in m.systems[-1]
m.row(2); assert m.toasts[-1][3]=='Wait for the one in progress' and len(m.threads)==1
worker,arg=m.threads[-1]; assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=50_000_000)==0
assert 'rm -rf /mnt/mmc/.spotify/cache /mnt/mmc/.spotify/.cache-size /mnt/mmc/.spotify/spot.log /mnt/mmc/.spotify/aplay.log /mnt/mmc/.spotify/load.log' in m.systems; del m.files[SCAP]; m.advance(500,clear=False)
assert m.toasts[-1][3]=="Spotify's login and cache erased" and KILL in m.systems[-1]
assert m.labels()[2:]==['Cache','Erase Spotify Data','2 GB']; passed()
# The wheel drives only allowlisted windows (patch/contexts.inc), so every runtime list page needs an
# entry; the two streaming Now Playing pages, a card app's QR code page, Now Playing's About (np_about.c, its
# Return its own), Guest Play's Now Playing, failure and connect pages (guest_ui.c) and Bluetooth Stopped (bt_stopped.c),
# none with a list, handle their own keys. The empty state's own page (feedback.c empty_open, its
# window a caller's) is in the list for its one row.
pages=set(re.findall(r'page_open\("(\w+)"',''.join(p.read_text() for p in (ROOT/'patch').glob('*.c'))))
assert pages-set(re.findall(r'"(\w+)"',(ROOT/'patch/contexts.inc').read_text()))=={'radionp_page','spotify_page','qrcode_page','npabout_page','guestnp_page','guestfail_page','guestconnect_page','btstopped_page'} and '"empty_page"' in (ROOT/'patch/contexts.inc').read_text(); passed()
m=CardMachine({},{}); vid=payload_syms['vid']; m.word(vid+12,4242); m.word(vid+16,7)
for playing,command in ((0,'a'),(1,'r')):
    m.sent=[]; endpoint(m,'video_set_playing',playing); endpoint(m,'video_set_playing',playing)
    assert [x[1] for x in m.sent]==[command,command] and endpoint(m,'video_status')==(2 if playing else 3)
ready_gain(m,1,32); m.now+=1500; m.sent=[]; endpoint(m,'video_poll')
assert [x[1] for x in m.sent]==['v'+chr(32),'g'+chr(100)]
ready_gain(m,0,32); m.now+=500; m.sent=[]; endpoint(m,'video_poll'); assert m.sent[-1][1]=='g'+chr(32)
m.files['/tmp/q2video.state']=bytearray(struct.pack('<i',1)); m.now+=500; endpoint(m,'video_poll'); assert endpoint(m,'video_status')==3
passed()
# Manual video pause must take effect before the helper's next state report.
m=CardMachine({},{}); m.word(payload_syms['vid']+12,4242); m.word(payload_syms['vid']+16,7)
endpoint(m,'headset_ears',7); endpoint(m,'video_key',O['KEY_PLAY'])
assert endpoint(m,'video_status')==3
endpoint(m,'headset_ears',6); endpoint(m,'headset_ears',7)
assert endpoint(m,'video_status')==3
m.files['/tmp/q2video.state']=bytearray(struct.pack('<i',0)); m.now+=400; endpoint(m,'video_poll')
assert endpoint(m,'video_status')==3  # stale helper state cannot undo the manual pause
endpoint(m,'video_key',O['KEY_PLAY']); assert endpoint(m,'video_status')==2
passed()
# Stock Tidal shares the native queue controls and session cancellation.
m=HeadsetMachine(); m.word(O['MCL_TYPE'],2); m.ears(3); m.ears(1); assert not m.playing
m.ears(3); assert m.playing; m.command(163); assert m.actions[-1]==(1,'local_next'); passed()
# A focused streaming page owns the keys: Spotify's while it runs, else the owner's; Tidal's are local's.
for owner,page,want in ((2,'spotbrowse_page',[(2,O['KEY_PLAY'])]),(1,'spotify_page',[(2,O['KEY_PLAY']),(1,'pause')]),
                        (3,'radionp_page',[(3,O['KEY_PLAY'])]),(2,'tidal_downloads',[(1,'pause')]),(2,'home_page',[(2,O['KEY_PLAY'])])):
    m=HeadsetMachine(owner); m.top=m.node('window',page,[])
    m.fn('media_key',O['KEY_PLAY']); assert m.actions==want,(page,m.actions); passed()
# Headphones Required (docs/internals.md): stock V1.36's switch lets nothing start with no output connected, and Vamp's
# own starts ask stock's check_forbid_play_no_headset, as stock's do. Everywhere else this suite and test/fw answer for
# that function; from here to Resume on connect stock's own code answers. First the gate alone, on both builds: it
# refuses only with the switch on, outside test mode, and no output, which is a Bluetooth link with its codec up, a
# USB DAC (g_otg_status 2, not 1) or either jack plugged. Refusing, it opens stock's 2 s message when asked with 0, as
# Vamp asks, and calls notifyPrompt when asked with 1.
GATE=syms['check_forbid_play_no_headset']
def hp_on(m,**state):
    """m with stock's gate run, not answered, and the switch on; state's bytes are an output, test mode or the switch off."""
    del m.handlers[GATE]; m.handlers.pop(syms['has_valid_output_device'],None)  # stock's own outputs check too
    m.byte(syms['g_block_withoutphone_flag'],1)
    for name,v in state.items(): (m.word if name=='bt_showcoding' else m.byte)(syms[name],v)
    return m
def forbids(patched,ask=0,**state):
    c=hp_on(Machine(patched),**state); c.mock('notifyPrompt'); ret=c.call(address=GATE,args=(ask,0,0,0),gap=0)
    return ret,[(c.text(x[1]),c.get(x[2]),c.get(x[2]+4)) if x[0]=='navigator_to_with_context' else x[0] for x in c.calls if x[0]!='memset']
NOTICE=['getFormatString',('dialog/msginfo_dialog',1,2000)]
for patched in (True,False):
    assert forbids(patched)==(1,NOTICE) and forbids(patched,1)==(1,['getFormatString','notifyPrompt'])
    for state in (dict(g_block_withoutphone_flag=0),dict(g_testmode_flag=1),dict(bt_linkstatus=1,bt_showcoding=1),
                  dict(g_otg_status=2),dict(g_po_status=1),dict(g_bal_status=1)):
        assert forbids(patched,**state)==(0,[]),state
    for state in (dict(bt_linkstatus=1),dict(bt_showcoding=1),dict(g_otg_status=1)):
        assert forbids(patched,**state)==(1,NOTICE),state
passed()
# Vamp's two starts of its own, with stock's gate run: each asks with 0 and starts nothing it refuses. Play/Pause
# when not playing (ringnav_play_pause, the hook on stock's player_play_pause, whose body is the toggle here; a pause
# always goes through), the side buttons' and a headset's skip (media_key, playing or not). The toggle answers at +12,
# past the 3-word PIC prologue the hook's trampoline skips (build.py), so ringnav_play_pause and the real gate run.
def hp_keys(key,playing=False,**state):
    m=hp_on(HeadsetMachine(),**state); toggle=syms['player_play_pause']; del m.handlers[toggle]; m.handlers[toggle+12]='hs:local_toggle'
    m.playing=playing; m.fn('media_key',O[key]); return m.actions,sum(x[0]=='navigator_to_with_context' for x in m.calls)
for key,does in (('KEY_PLAY','play'),('KEY_FWD_BTN','local_next'),('KEY_BACK_BTN','local_prev')):
    assert hp_keys(key)==([],1) and hp_keys(key,g_po_status=1)==hp_keys(key,g_block_withoutphone_flag=0)==([(1,does)],0),key
assert hp_keys('KEY_PLAY',True)==([(1,'pause')],0) and hp_keys('KEY_FWD_BTN',True)==([],1); passed()
# Stock's own start runs through Vamp's hook: Now Playing opened with a track to start, the way a list row's tap
# goes. Stock's playing_page_init, whole: refused, it shows the message, closes the window it was given and returns
# STOP before it starts or builds anything, and ringnav_playing binds nothing to a window that is gone. With an
# output the track starts and the labels bind, as they do opened with nothing to start (0xff), which asks nothing.
def np_opens(start=1,**state):
    m=hp_on(QueueMachine(queue=3,pos=1),**state); win=m.node('window','playing_page'); m.word(win+O['W_PARENT'],m.wm)
    m.stack.append(win); m.top=win; ctx=m.alloc(16)
    for i,v in enumerate((m.get(syms['mcl_pdeqplaylist']),0,start,0)): m.word(ctx+4*i,v)
    ret=m.call(address=HOOKS['playing_page_init'][0],args=(win,ctx,0,0),gap=0)
    return ret,[x[0] for x in m.calls if x[0] in ('player_start','window_close')],any(x[0]=='widget_lookup' for x in m.calls),len(m.toasts)
assert np_opens()==(11,['window_close'],False,1) and np_opens(0xff)==(0,[],True,0)
assert np_opens(g_po_status=1)==np_opens(g_block_withoutphone_flag=0)==(0,['player_start'],True,0); passed()
# The same question asked before the page exists (output_rule.c ringnav_nav_to): navigator_to_with_context, the way a list
# row's tap or Centre, a song in Search or Cover Flow and play_folder open Now Playing to start a track. Refused, only
# stock's message opens; the page with its placeholder title and artist (Shanling's slogan) never does (the owner's
# report, 2026-11-22: that page slid in under the toast and out again). Now Playing to show what plays (0xff), another
# page, and a start with an output reach stock's navigator as before. NAV_OPEN is the navigator's opening of a page.
NAV,NAV_OPEN=syms['navigator_to_with_context'],0x4f52f0
def nav_opens(page='playing_page',start=1,**state):
    m=hp_on(QueueMachine(queue=3,pos=1),**state); del m.handlers[NAV]; m.handlers[NAV_OPEN]='nav_open'; ctx=m.alloc(16)
    for i,v in enumerate((m.get(syms['mcl_pdeqplaylist']),0,start,2)): m.word(ctx+4*i,v)
    m.call(address=NAV,args=(m.string(page),ctx,0,0),gap=0)
    return [m.text(c[1]) for c in m.calls if c[0]=='nav_open'],[c[0] for c in m.calls if c[0]=='player_start']
assert nav_opens()==(['dialog/msginfo_dialog'],[])
assert nav_opens(start=0xff)==nav_opens(g_po_status=1)==nav_opens(g_block_withoutphone_flag=0)==(['playing_page'],[])
assert nav_opens('localmusic/playlist_page')==(['localmusic/playlist_page'],[]); passed()
# Resume on connect (docs/internals.md#resume-on-connect), local music and Spotify. Off (default): a
# headset's PLAY within CONNECT_HOLD_MS (5 s) of the link starts nothing, a later one plays. On: it plays.
for owner in (1,2):
    for off in (0,1):
        m=HeadsetMachine(owner); m.playing=False
        if not off: m.config['RESUME']='1'
        for name in ('g_bluetoothflag','bt_linkstatus'): m.byte(syms[name],1)
        m.fn('headset_poll',gap=200); m.command(200)
        if not off: assert m.playing; passed(); continue
        m.command(164); m.command(207); assert not m.playing and not m.actions
        m.command(200,gap=5000); assert m.playing and m.actions==[(owner,'play')]; passed()
# Stock's restart once A2DP comes up (BT_CONNECT_START): on, player_save_start(0) as stock. Off (default): a
# playing queue goes on, a paused one comes back paused, a stopped one or a stream's turn starts nothing.
from build import BT_CONNECT_START
assert struct.unpack_from('<I',demo,fileoff(demo,BT_CONNECT_START[0]))[0]==jal(payload_syms['ringnav_bt_connect_start'])
for off,owner,saved,want in ((0,1,3,[0]),(0,2,0,[0]),(1,1,2,[0]),(1,1,3,[1]),(1,1,0,[]),(1,2,3,[])):
    m=HeadsetMachine(owner); m.saved=saved
    if not off: m.config['RESUME']='1'
    m.fn('ringnav_bt_connect_start')
    assert [c[1] for c in m.calls if c[0]=='player_save_start']==want; passed()
print('Real Spotify/radio/video play-state APIs, helper gain updates and stock Tidal routing passed.')

# Now Playing's info page: its one fill (NP_INFO_CALL) goes through ringnav_np_info after stock's, which
# writes "Label:value" and the bare folder: ": " after each label, units spaced, the folder labelled
# from player_location, en_US's first unknown ("Unknown Artist") plain "Unknown"; a second pass changes nothing.
from build import NP_INFO_CALL
assert struct.unpack_from('<I',demo,fileoff(demo,NP_INFO_CALL))[0]==jal(payload_syms['ringnav_np_info'])
class InfoMachine(Machine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='i:format':
            self.u.mem_write(u.reg_read(UC_MIPS_REG_A1),{'player_location':getattr(self,'location',b'Location\0')}.get(self.text(u.reg_read(UC_MIPS_REG_A0)),b'?\0'))
            self.returns(0); return
        return super().hook(u,address,size,unused)
m=InfoMachine(); m.handlers[syms['getFormatString']]='i:format'
stock_lines={'label_album':'Album:Kid A','label_genre':'Genre:Unknown Artist','label_size':'File Size:35.92MB',
             'label_year':'Year:2000','label_encode':'Encoding:FLAC','label_bits':'Bit Depth:16bit','label_kbps':'Bitrate:950kbps',
             'label_sample':'Sample Rate:44.1kHz','label_location':'SD:/Music/Radiohead'}
lines={k:m.node('label',k,text=v) for k,v in stock_lines.items()}
win=m.node('window','playing_page',list(lines.values()))
for l in lines.values(): m.word(l+O['W_PARENT'],win)
want=['Album: Kid A','Genre:Unknown Artist','File Size: 35.92 MB','Year: 2000','Encoding: FLAC','Bit Depth: 16-bit',
      'Bitrate: 950 kbps','Sample Rate: 44.1 kHz','Location: SD:/Music/Radiohead']
for _ in range(2):
    m.call(address=payload_syms['ringnav_np_info'],args=(win,0,0,0),gap=0)
    assert [c[1] for c in m.calls if c[0]=='np_info']==[win] and [m.nodes[l]['text'] for l in lines.values()]==want, [m.nodes[l]['text'] for l in lines.values()]
assert next(n for n in m.nodes.values() if n.get('name')=='ringnav_genre_scroll')['text']=='Genre: Unknown'
passed()
# OLED's hidden details (tools/ipod.py np_info_values, About's grid's source): beside each of stock's labels a
# <name>_value one; stock's keeps the name, the value goes beside it, the genre with them (no scroller). Stock's
# English "Encoding", "Bitrate" and "File Size" read "Format", "Bit Rate" and "Size", the grid's names.
CONFIG.clear(); CONFIG.update(LIGHT='0')
m=InfoMachine(); m.handlers[syms['getFormatString']]='i:format'
lines={k:m.node('label',k,text=v) for k,v in stock_lines.items()}
values={k:m.node('hscroll_label',k+'_value') for k in stock_lines}
win=m.node('window','playing_page',[*lines.values(),*values.values()])
for l in (*lines.values(),*values.values()): m.word(l+O['W_PARENT'],win)
m.call(address=payload_syms['ringnav_np_info'],args=(win,0,0,0),gap=0)
assert [m.nodes[l]['text'] for l in lines.values()]==['Album','Genre','Size','Year','Format','Bit Depth','Bit Rate','Sample Rate','Location']
assert [m.nodes[v]['text'] for v in values.values()]==['Kid A','Unknown','35.92 MB','2000','FLAC','16-bit','950 kbps','44.1 kHz','SD:/Music/Radiohead']
assert {m.nodes[v]['style:normal:text_color'] for v in values.values()}=={signed(color_t(0xffffff))}
assert not any(n.get('name')=='ringnav_genre_scroll' for n in m.nodes.values()); passed()
CONFIG.clear()
# Genre shows in a private scroller, stock's label hidden as its source and left alone; its text is set once,
# so stock's next 250 ms fill resets nothing. Only a value wider than the label moves, on the album marquee's
# chain, and only in front and lit, on Now Playing's About (np_about.c), which holds the details while open.
class GenreMachine(InfoMachine,QueueMachine):
    about=0
    def hook(self,u,address,size,unused):  # About's page_open, in front, and the details it takes and gives back
        name=self.handlers.get(address,'')
        if name not in ('window_create','widget_add_child','widget_remove_child'): return super().hook(u,address,size,unused)
        a,b=u.reg_read(REGS[0]),u.reg_read(REGS[1])
        if name=='window_create': self.about=self.top=self.node('window','npabout_page'); self.stack.append(self.top); self.returns(self.about); return
        if name=='widget_remove_child': self.nodes[a]['children'].remove(b)
        else: self.nodes[a]['children'].append(b); self.word(b+O['W_PARENT'],a)
        self.returns(0)
np_page=HOOKS['playing_page_init'][0]
def genre_page(genre,text_w,about=True):
    m=GenreMachine(queue=3,pos=1); m.handlers[syms['getFormatString']]='i:format'; m.handlers[np_page+12]='stock_playing'
    for n in ('window_create','widget_add_child','widget_remove_child'): m.handlers[syms[n]]=n
    source=m.node('label','label_genre',text='Genre:'+genre); m.word(source+O['W_X'],75); m.word(source+O['W_W'],225)
    page=m.node('view','view_info',[source]); sv=m.node('slide_view','slide_view',value=2)
    win=m.node('window','playing_page',[sv,page,m.node('slider','slider_play',max=225,value=100)])
    for w,parent in ((source,page),(page,win),(sv,win),(win,m.wm)): m.word(w+O['W_PARENT'],parent)
    m.top=win; m.call(address=np_page,args=(win,7,0,0),gap=0); m.timers.clear()
    if about:  # ⋯'s About row: the details move into its page, in front
        m.call(address=payload_syms['np_about_open'],args=(0,0,0,0),gap=0)
        assert m.about and m.top==m.about and m.nodes[page].get('visible')==1; m.timers.clear()
    def fill(): m.call(address=payload_syms['ringnav_np_info'],args=(win,0,0,0),gap=0)
    fill()  # stock measures on the first paint: nothing to scroll yet
    s=next(w for w,n in m.nodes.items() if n.get('name')=='ringnav_genre_scroll')
    assert not m.timers and m.get(s+O['HSCROLL_TEXT_W'])==0
    m.word(s+O['HSCROLL_TEXT_W'],text_w)
    return m,win,sv,source,s,fill
m,win,sv,source,s,fill=genre_page('Rock',300,about=False); fill(); fill(); assert not m.timers; passed()  # About closed: still
m,win,sv,source,s,fill=genre_page('流行',100); fill()
assert m.nodes[source]['visible']==0 and m.nodes[source]['text']=='Genre:流行' and m.nodes[s]['text']=='Genre: 流行'
assert m.nodes[s]['type']=='hscroll_label' and m.nodes[s]['style']=='s_scrlabel_white16c' and m.get(s+O['W_X'])==75 and m.get(s+O['W_W'])==225
assert m.nodes[s]['hscroll_label_set_only_focus']==1
assert not m.timers and not [c for c in m.calls if c[0]=='widget_set_text_utf8' and c[1]==s]; passed()
# 79 px over (2 px at a step): MARQUEE_HOLD_MS, 40 steps to the end, a hold there, back to the start, a hold.
m,win,sv,source,s,fill=genre_page('Rock',300); pause,ms=O['MARQUEE_HOLD_MS'],50
def shift(): return m.nodes[s]['hscroll_label_set_xoffset']
fill(); assert len(m.timers)==1 and shift()==0; fill(); assert len(m.timers)==1
m.advance(pause-1); assert shift()==0; m.advance(1); assert shift()==2; m.advance(ms); assert shift()==4
m.advance(ms*37); assert shift()==78; m.advance(ms); assert shift()==79  # never past the end
m.advance(pause-1); assert shift()==79; m.advance(1); assert shift()==0
m.advance(pause-1); assert shift()==0; m.advance(1); assert shift()==2; passed()
# Dark, or covered (Now Playing back in front among them): the chain ends where it is, a fill doesn't start it,
# and one in front does.
front=m.top
for off_front,on_front in ((lambda:m.byte(syms['g_backlight_status'],0),lambda:m.byte(syms['g_backlight_status'],1)),
                           (lambda:setattr(m,'top',m.node('window','dialog')),lambda:setattr(m,'top',front)),
                           (lambda:setattr(m,'top',win),lambda:setattr(m,'top',front))):
    was=shift(); off_front(); m.advance(ms); assert not m.timers and shift()==was
    fill(); assert not m.timers
    on_front(); fill(); assert len(m.timers)==1; m.advance(pause); assert shift()==was+2; passed()
# A short genre after a long one, on the same page: back at the start, its timer gone once measured, none again.
m.nodes[source]['text']='Genre:Pop'; fill(); assert m.nodes[s]['text']=='Genre: Pop' and shift()==0 and len(m.timers)==1
m.word(s+O['HSCROLL_TEXT_W'],60); m.advance(pause); assert shift()==0 and not m.timers
fill(); assert not m.timers; passed()
# A long one after a long one holds at its start; a page closing, or a new one replacing it, ends the chain.
m,win,sv,source,s,fill=genre_page('Rock',300); fill(); m.advance(pause+ms); assert shift()==4
m.nodes[source]['text']='Genre:Metal'; fill(); assert shift()==0 and len(m.timers)==1 and min(t[0] for t in m.timers.values())==m.now+pause
m.call(address=np_page,args=(m.node('window','playing_page'),7,0,0),gap=0); assert not [t for t in m.timers.values() if t[1]==payload_syms['genre_step']]
m,win,sv,source,s,fill=genre_page('Rock',300); fill(); assert len(m.timers)==1
f,ctx=m.handler(win,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); assert not m.timers
m.top=0; was=len(m.calls); m.call(address=payload_syms['genre_step'],args=(0,0,0,0),gap=0)
assert not m.timers and not any(c[0]=='hscroll_label_set_xoffset' for c in m.calls); passed()
# A missing player_location ("(key)") reads Location; a long (CJK) folder, past 512 bytes, stays whole.
m=InfoMachine(); m.handlers[syms['getFormatString']]='i:format'; m.location=b'(player_location)\0'
folder='SD:/Music/'+'\u97f3\u697d'*150; label=m.node('label','label_location',text=folder)
m.call(address=payload_syms['ringnav_np_info'],args=(m.node('window','playing_page',[label]),0,0,0),gap=0)
assert m.nodes[label]['text']=='Location: '+folder; passed()
# on_wm_idle_status's player_mainscheduling (its status round trip) runs every MAINSCHEDULING_MS, a second
# while dark_slow (dark, and nothing follows the position: playback_power.c dark_slow_ok).
from build import MAINSCHEDULING_CALL
assert struct.unpack_from('<I',demo,fileoff(demo,MAINSCHEDULING_CALL[0]))[0]==jal(payload_syms['ringnav_mainscheduling'])
m=Machine()
def scheduled(): return sum(c[0]=='player_mainscheduling' for c in m.calls)
def schedule(): m.call(address=payload_syms['ringnav_mainscheduling'],args=(0,0,0,0),gap=0,clear=False)
for wait,runs in ((0,1),(50,0),(50,1),(99,0),(1,1)):
    m.now+=wait; m.calls=[]; schedule(); assert scheduled()==runs,(wait,runs)
m.word(payload_syms['dark_slow'],1); m.now+=100; m.calls=[]; schedule(); assert scheduled()==0
m.now+=900; schedule(); assert scheduled()==1; m.word(payload_syms['dark_slow'],0)
# Playing, from a second before the track's end (player_playtime_and_length: 225 s) until
# MAINSCHEDULING_END_MS after, every pass: a change without Gapless starts as soon as stock's did.
# Paused there, it stays paced.
m.handlers.pop(syms['mclGetPlayStatus']); m.word(PLAY_STATUS,2)
m.playtime=223; m.now+=100; schedule(); m.calls=[]; m.now+=10; schedule(); assert scheduled()==0
m.playtime=224; m.now+=10; m.calls=[]; schedule(); schedule(); assert scheduled()==2
del m.playtime; m.now+=1900; schedule(); m.calls=[]; m.now+=10; schedule(); assert scheduled()==1
m.now+=90; m.calls=[]; schedule(); assert scheduled()==0
m.playtime=224; m.word(PLAY_STATUS,3); m.now+=100; schedule(); m.calls=[]; m.now+=10; schedule()
assert scheduled()==0; passed()

# Chapter subprocess syscalls use Linux o32 and pass the filename through argv.
for name,number,args,error in (('chapter_pipe2',4328,(0x1001000,0x80000,0,0),False),
                                ('chapter_pipe2',4328,(0x1001000,0x80000,0,0),True),
                                ('chapter_kill',4037,(123,9,0,0),False),
                                ('chapter_execv',4011,(0x1001000,0x1002000,99,0),False)):
    m=Machine(); seen=[]
    def chapter_syscall(u,interrupt,unused):
        seen.append((u.reg_read(UC_MIPS_REG_V0),*[u.reg_read(r) for r in REGS[:3]]))
        u.reg_write(UC_MIPS_REG_A3,int(error)); u.reg_write(UC_MIPS_REG_V0,13 if error else 0)
    m.u.hook_add(UC_HOOK_INTR,chapter_syscall)
    result=m.call(address=payload_syms[name],args=args,gap=0)
    assert result==(-1 if error else 0)
    assert seen==[(number,args[0],args[1],0 if name=='chapter_execv' else args[2])]; passed()

# Chapter extraction is tested with real FFmpeg in chapters.py; here only that boundary is mocked.
class ChapterMachine(CoverflowMachine):
    def __init__(self, count=3, failed=False, ready=True, path='/mnt/mmc/Book ; $x.M4B'):
        super().__init__(page='playing_page', pos=1)
        self.ready=ready; self.cancelled=0; self.playtime=70; self.fits=1; self.path=path
        self.job=self.alloc(32+512*260)
        for off,v in ((12,0),(16,int(failed)),(20,count)): self.word(self.job+off,v)
        for i,(sec,title) in enumerate(((0,'Opening'),(60,''),(120,'日本語'))):
            self.word(self.job+32+i*260,sec)
            self.u.mem_write(self.job+36+i*260,title.encode()+b'\0')
        for name in ('chapters_start','chapters_poll','chapters_cancel','mp4_fits'):
            self.handlers[payload_syms[name]]='chapter:'+name
        self.mock('navigator_back')
        self.handlers.pop(syms['mclGetPlayStatus'],None); self.word(0xa50084,2)
        self.track=self.items(self.get(syms['mcl_pdeqplaylist']))[1]
        self.word(self.track+O['REC_PATH'],self.string(path))
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('chapter:'): return super().hook(u,address,size,unused)
        a=u.reg_read(REGS[0]); ret=0
        if name=='chapter:chapters_start':
            assert self.text(a)==self.path; ret=self.job
        elif name=='chapter:mp4_fits':
            assert self.text(a)==self.path; ret=self.fits
        elif name=='chapter:chapters_poll':
            ret=int(self.ready)
            if ret: self.word(self.job+12,1)
        else: self.cancelled+=1; self.ready=True
        self.returns(ret)
    def open_chapters(self):
        self.press(6000); assert self.hold()==11
        assert 'Chapters' in self.labels(); self.release()
        self.pick(self.labels().index('Chapters')); self.advance(0)
        self.page=self.top
        assert self.nodes[self.page]['name']=='chapters_page'
    def rows(self): return self.nodes[self.find('scroll_view')]['children']
    def choose(self,i):
        f,ctx=self.handler(self.rows()[i],O['EVT_CLICK'])
        self.call(address=f,args=(ctx,self.event,0,0),gap=0)

for ext,shown in (('m4b',True),('M4B',True),('M4b',True),('mp3',False),('m4b.mp3',False)):
    m=playing_menu(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[1]
    m.word(r+O['REC_PATH'],m.string('/p/book.'+ext))
    m.press(6000); assert m.hold()==11 and ('Chapters' in m.labels())==shown; passed()
# A spoken file in any format has Chapters (ffmpeg reads ID3, FLAC and Ogg chapters too); a CUE track
# other than an M4B's doesn't, the first (start 0, end set) included, as its sheet lists them.
for path,cue,end,shown in (('/mnt/mmc/Audiobooks/Book/01.mp3',0,0,True),('/mnt/mmc/podcasts/Show/e.MP3',0,0,True),
                           ('/mnt/mmc/AUDIOBOOKS/b.flac',0,0,True),('/mnt/mmc/Music/book.mp3',0,0,False),
                           ('/mnt/mmc/AudiobooksX/b.mp3',0,0,False),('/p/Audiobooks/b.mp3',0,0,False),
                           ('/mnt/mmc/Audiobooks/b.mp3',10,0,False),('/mnt/mmc/Audiobooks/b.mp3',0,300,False),
                           ('/p/b.m4b',10,0,True),('/p/b.m4b',0,300,True)):
    m=playing_menu(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[1]
    m.word(r+O['REC_PATH'],m.string(path)); m.word(r+O['REC_CUE_START'],cue); m.word(r+O['REC_CUE_END'],end)
    m.press(6000); assert m.hold()==11 and ('Chapters' in m.labels())==shown,path; passed()
# An M4B CUE track: rows on the track's clock (playtime 70 is CUE-relative), as the seek is. The chapter
# playing at the cue start shows at 0:00 and earlier ones go; from the cue end on, none.
for cue,end,want,gone,index,to in ((70,0,('Chapter 2','0:00:00','日本語','0:00:50'),'Opening',1,50),
                                   (50,100,('Opening','0:00:00','Chapter 2','0:00:10'),'日本語',1,10)):
    m=ChapterMachine(); m.word(m.track+O['REC_CUE_START'],cue); m.word(m.track+O['REC_CUE_END'],end)
    m.open_chapters(); m.advance(50); view=m.find('scroll_view'); m.paint(view)
    assert all(t in m.texts() for t in want) and gone not in m.texts() and len(m.rows())==2,m.texts()
    assert m.nodes[view]['_ringnav_index']==index
    m.choose(index)
    assert [(c[0],c[1]) for c in m.calls if c[0] in ('player_seek_time','mclSetSeek')]==[('player_seek_time',to)]
    passed()
m=ChapterMachine(path='/mnt/mmc/Audiobooks/Book ; $x/01.mp3'); m.open_chapters(); m.advance(50)
assert 'Chapter 2' in m.texts() and '日本語' in m.texts(); passed()
for state in (2,3):
    m=ChapterMachine(); m.word(0xa50084,state); m.open_chapters()
    assert 'Loading chapters…' in m.texts()
    m.advance(50); view=m.find('scroll_view'); m.paint(view)
    assert m.nodes[view]['_ringnav_index']==1
    assert 'Chapter 2' in m.texts() and '0:01:00' in m.texts() and '日本語' in m.texts()
    m.choose(2)
    seeks=[c for c in m.calls if c[0] in ('player_seek_time','mclSetSeek','mclSetResume')]
    assert [(c[0],c[1]) for c in seeks]==[('player_seek_time' if state==2 else 'mclSetSeek',120)]
    assert any(c[0]=='navigator_back' for c in m.calls); passed()
# A book whose MP4 index would not fit in RAM: a toast, and no ffmpeg.
m=ChapterMachine(); m.fits=0; m.press(6000); assert m.hold()==11; m.release()
m.pick(m.labels().index('Chapters')); m.advance(0)
assert m.nodes[m.top]['name']!='chapters_page' and m.toasts[-1][3]=='Too long to read chapters on the Q2'; passed()
m=ChapterMachine(); m.open_chapters(); m.advance(50); m.word(O['MCL_POS'],2); m.choose(1)
assert not any(c[0] in ('player_seek_time','mclSetSeek') for c in m.calls)
assert any(c[0]=='navigator_back' for c in m.calls); passed()
for count,failed in ((0,False),(0,True)):
    m=ChapterMachine(count,failed); m.open_chapters(); m.advance(50)
    assert empty_titles(m)==['No Chapters'] and not m.rows(); passed()
# An empty page, one per module: the message is OLED's note on the page (page_empty), or the empty state (empty_list),
# and the bar reads the page's name.
def shows_empty(m,bar,message,name): assert message in [n.get('text') for n in m.nodes.values() if n.get('name')=='_empty']+empty_titles(m) and bar_reads(m,bar)==name,(message,m.nodes[bar]['text'])
def empty_row(m,row):  # Local Music's row, opened
    view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)]); m.top=m.node('window','localmusic_page',[view])
    assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
    f,ctx=m.handler(m.nodes[lib_row(m,view,row) if isinstance(row,str) else m.nodes[view]['children'][row]]['children'][0],O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0; m.page=m.top
for count_failed,title in (((0,False),'No Chapters'),((0,True),'No Chapters')):
    m=ChapterMachine(count=count_failed[0],failed=count_failed[1]); ebar=page_bar(m); m.open_chapters(); m.advance(50); shows_empty(m,ebar,title,'Chapters'); passed()
m=ShuffleMachine(); ebar=page_bar(m); empty_row(m,'Most Played'); shows_empty(m,ebar,'No Plays Yet','Most Played'); passed()
m=SpotMachine(files={}); ebar=page_bar(m); erows=stream_rows(m); f,ctx=m.handler(erows['Tidal Downloads'],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000); shows_empty(m,ebar,'No Downloads Yet','Tidal Downloads'); passed()
m=CoverflowMachine(page='playing_page',pos=0); ebar=page_bar(m); q=m.deque([m.song('A')]); m.word(syms['mcl_pdeqplaylist'],q); m.word(O['MCL_POOL'],m.deque([0],'int')); assert m.call(address=payload_syms['playback_set'],args=(0,1,0,0),gap=0)==1
m.press(10); assert m.hold()==11; m.release(); m.pick(m.labels().index('View Queue')); m.advance(0); m.page=m.top
assert m.nodes[m.page]['name']=='upnext_page' and any(n.get('name')=='_mm_head' for n in m.nodes.values()); passed()  # never empty: the playing song heads the sheet (upnext.c)
m=ChapterMachine(ready=False); m.open_chapters(); m.advance(50)
f,ctx=m.handler(m.page,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
assert m.cancelled==1
m.advance(50); assert m.freed==1 and not m.timers; passed()

# Centre/wheel and touch reach the same guarded row handler.
for touch in (False,True):
    m=ChapterMachine(); m.open_chapters(); m.advance(50); view=m.find('scroll_view'); m.paint(view)
    if touch:
        m.choose(2)
    else:
        assert m.call(O['KEY_NEXT'],gap=0)==11
        assert m.nodes[view]['_ringnav_index']==2
        m.on_click=lambda w,e: m.nested(m.handler(w,O['EVT_CLICK'])[0],m.handler(w,O['EVT_CLICK'])[1],e)
        assert m.confirm()==11
    assert any(c[0]=='player_seek_time' and c[1]==120 for c in m.calls); passed()

class ChapterResumeMachine(ChapterMachine):
    def __init__(self):
        super().__init__()
        self.files=resume_at(85,'/mnt/mmc/Book ; $x.M4B'); self.open={}; self.play=(0,225)
        self.seeks=[]; self.opens=self.renames=0; self.epoch=1700000000
        self.handlers[sleep_hook+12]='stock_sleep'
        for name in ('fopen@GLIBC_2.2','fread@GLIBC_2.0','fwrite@GLIBC_2.0','fclose@GLIBC_2.2',
                     'rename@GLIBC_2.0','access@GLIBC_2.0','time@GLIBC_2.0',
                     'player_playtime_and_length','player_seek_time'):
            self.handlers[syms[name]]='r:'+name
        self.handlers[payload_syms['stock_seek_time_trampoline']]='r:player_seek_time'
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'').startswith('r:'): return ResumeMachine.hook(self,u,address,size,unused)
        return super().hook(u,address,size,unused)
    poll=ResumeMachine.poll
for polls in (0,1,2):  # before first resume poll, during settling, or with a restore pending
    m=ChapterResumeMachine()
    if polls: m.poll(0,225,pos=1,n=polls)
    m.open_chapters(); m.advance(50); m.choose(0)
    assert m.seeks==[0]
    m.poll(0,225,n=2); m.poll(1,225,n=3)
    assert m.seeks==[0], (polls,m.seeks); passed()

# Stream threads (tidal.c): Tidal's poll always parks on its gate, lit or dark, whatever the queue;
# a Tidal track asked for posts it once stock's get_tidal_trackurl has run. Baidu's poll only ever
# waits, so it never wakes.
m=Machine(); m.mock('usleep@GLIBC_2.0','sem_wait@GLIBC_2.2','sem_post@GLIBC_2.2')
m.handlers[payload_syms['stock_tidaltrack_trampoline']]='stock_tidaltrack'
def stream_calls(name,*args):
    m.calls=[]; m.call(address=payload_syms[name],args=(*args,0,0,0,0)[:4],gap=0,clear=False)
    return [c[0].split('@')[0] for c in m.calls]
for lit,queue in ((1,0x20),(0,0xf001),(0,0x20)):
    m.byte(syms['g_backlight_status'],lit); m.word(O['MCL_TYPE'],queue)
    assert stream_calls('ringnav_tidal_poll',196608)==['sem_wait']
assert stream_calls('ringnav_tidal_track')==['stock_tidaltrack','sem_post']
assert stream_calls('ringnav_baidu_poll',10000)==['sem_wait']
passed()

# EQ per Output (headset.c eq_output_key): what plays names the output; nothing is asked while the
# setting is off, and with no output in use the EQ stays where it is.
class EqOutputMachine(BtLinkMachine):
    def __init__(self,links=(),way=0,headset=0,po=0,bal=0,on=1):
        super().__init__(links); self.way=way; self.on=on; self.keys=[]; self.requests=0
        self.handlers.update({syms['mclGetOutputWay']:'eo:way',payload_syms['ringnav_eq_output']:'eo:on',payload_syms['eq_output']:'eo:key'})
        if 'q2eq_poll' in payload_syms: self.handlers[payload_syms['q2eq_poll']]='eo:q2eq'  # a --dev build's (docs/adb.md#q2eq)
        self.byte(syms['g_headset_output'],headset); self.byte(syms['g_po_status'],po); self.byte(syms['g_bal_status'],bal)
    def hook(self,u,addr,size,_):
        name=self.handlers.get(addr,'')
        if not name.startswith('eo:'): return super().hook(u,addr,size,_)
        if name=='eo:key': self.keys.append(self.text(u.reg_read(REGS[0])))
        if name=='eo:q2eq': self.requests+=1
        self.returns({'eo:way':self.way,'eo:on':self.on}.get(name,0))
    def poll(self): self.call(address=payload_syms['headset_eq_poll'],args=(0,0,0,0),gap=1000); return self.keys
for kw,want in ((dict(headset=0,po=1),['3.5mm']),(dict(headset=1,bal=1),['4.4mm']),(dict(headset=1,po=1,bal=1),['4.4mm']),
                (dict(headset=0,po=1,bal=1),['3.5mm']),(dict(headset=0),[]),(dict(headset=1),[]),(dict(headset=0,bal=1),[]),
                (dict(way=2,headset=3),['usb']),(dict(way=2,headset=3,po=1),['usb']),
                (dict(way=1,headset=2,links=[(a_mac,3)]),[a_mac]),
                (dict(way=1,headset=2,links=[(a_mac,3),(b_mac,5)]),[a_mac]),(dict(way=1,headset=2,links=[(a_mac,5)]),[]),
                (dict(way=1,headset=2),[]),(dict(headset=0,po=1,on=0),[])):
    assert EqOutputMachine(**kw).poll()==want,kw
m=EqOutputMachine(headset=0,po=1); m.poll(); m.call(address=payload_syms['headset_eq_poll'],args=(0,0,0,0),gap=100); assert m.keys==['3.5mm']
# q2eq's requests are looked for on the same 500 ms, with EQ per Output on or off, in a --dev build alone
m=EqOutputMachine(headset=0,po=1,on=0); m.poll(); m.call(address=payload_syms['headset_eq_poll'],args=(0,0,0,0),gap=100)
dev=bool(json.loads((B/'manifest.json').read_text())['dev']); assert m.requests==dev and ('q2eq_poll' in payload_syms)==dev; passed()
# Advanced EQ's row (peq_ui.c): off unless set, and turning it off forgets whose EQ plays.
class EqRowMachine(QueueMachine):  # a payload call has no t9 for Machine's handler check
    forgets=0
    def hook(self,u,addr,size,_):
        if self.handlers.get(addr)!='eq_output_forget': return super().hook(u,addr,size,_)
        self.forgets+=1; self.returns(0)
for config,on in (({},0),({'EQOUTPUT':'1'},1)):
    m=EqRowMachine(); m.config.update(config); m.handlers[payload_syms['eq_output_forget']]='eq_output_forget'
    assert m.call(address=payload_syms['ringnav_eq_output'],args=(0,0,0,0),gap=0)==on,config
    m.calls=[]; m.call(address=payload_syms['ringnav_eq_output_toggle'],args=(0,0,0,0),gap=0)
    assert [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[(1-on,'Q2POD','EQOUTPUT')],config
    assert m.forgets==on and m.call(address=payload_syms['ringnav_eq_output'],args=(0,0,0,0),gap=0)==1-on,config
passed()

# Light Mode's inverse flip (games.c's card faces) follows the scheme's paper and ink (OLED's light half: #ffffff
# paper, #000000 ink), clamped as tools/theme.py's, so ui_ink gives a face's grey back exactly; dark: v itself.
for v,want in ((0x40,(0xff-0x40)*255//(0xff-0)),(0,0xff),(0xff,0),(0x80,0x7f)):
    um=Machine(); um.config.update(LIGHT='1')
    assert um.call(address=payload_syms['unflip'],args=(v,0,0,0),gap=0)==want,v
um=Machine(); um.config.update(LIGHT='1'); face=um.call(address=payload_syms['unflip'],args=(0xf2,0,0,0),gap=0)
assert um.call(address=payload_syms['ui_ink'],args=(0xff000000|face*0x010101,0,0,0),gap=0)&0xffffffff==0xfff2f2f2
um=Machine(); um.config.update(LIGHT='0'); assert um.call(address=payload_syms['unflip'],args=(0x40,0,0,0),gap=0)==0x40; passed()
# OLED's scrollbar is the wheel's indicator (list_marks.c wheel_sb_alpha, paint_scrollbar from the list's border
# paint): stock's mobile bar is clear, fg and bg; with the wheel not turning the list nothing is drawn, and a tick
# (wheel_sb_arm) shows theme.xml's track, 4 px wide 5 px in from the list's right edge, screen y 134 to 206, with the
# thumb on it in proportion to the page and at its place. Its own h is not the list's: a list of 96 under a bar of 240.
def sb_machine(content,value,light='1',y=60,table=False,h=96):
    m=Machine(); m.config['LIGHT']=light
    if table: w,_,_=m.table_page(); m.word(w+O['TABLE_ROWS'],content//48)
    else: w,_=m.page_list(20,height=h,extent=content)
    bar=m.node('scroll_bar_m'); m.word(bar+O['W_H'],240); m.word(bar+O['BAR_MAX'],max(content,240)); m.word(bar+O['BAR_VALUE'],value)
    parent=m.node('list_view' if not table else 'table_view',children=[w,bar]); m.word(parent+O['W_W'],240); m.word(parent+O['W_H'],h)
    m.word(w+O['W_PARENT'],parent); m.word(bar+O['W_PARENT'],parent); m.nodes[m.top]['children']=[parent]
    m.word(m.canvas+O['CANVAS_Y'],y); return m,parent,bar
def sb_stock(m,bar,name='fg_color'):  # stock's grey asked for while its bar paints
    begin(m,bar); m.handlers[int(manifest['patch_symbols']['stock_color_trampoline'],16)]='stock_color'; out=m.alloc(4); m.word(out,0xff808080)
    m.call(address=HOOKS['style_get_color'][0],args=(out,0x1234,m.string(name),0),gap=0); return m.get(out)
def sb_drawn(m,parent): m.bands=[]; m.call(address=HOOKS['widget_on_paint_border'][0],args=(parent,m.canvas,0,0),gap=0); return [b[:5] for b in m.bands if b[0]>=229]
for light in '10':
    for table in (False,True):
        m_,parent,bar=sb_machine(960,480,light,table=table); before=m_.lcd_colors()
        assert sb_drawn(m_,parent)==[] and m_.lcd_colors()==before,(light,table)
        assert sb_stock(m_,bar)==0x808080 and sb_stock(m_,bar,'bg_color')==0x808080,(light,table)
        m_.call(address=payload_syms['wheel_sb_arm'],args=(m_.nodes[parent]['children'][0],0,0,0),gap=0); drawn=sb_drawn(m_,parent)
        track,thumb=(0xc5c5c5,0x1a1a1a) if light=='1' else (0x3a3a3a,0xe5e5e5)
        at=lambda y:[b_[4]&0xffffffff for b_ in drawn if b_[1]==y and b_[0]<=232<b_[0]+b_[2]][-1]  # the row's middle, the last paint
        assert {b_[1] for b_ in drawn}==set(range(134-60,206-60)) and min(b_[0] for b_ in drawn)==240-5-4 and max(b_[0]+b_[2] for b_ in drawn)==240-5,(light,table)
        assert at(80)==color_t(track) and at(74+72*480//960+3)==color_t(thumb) and at(74+72*480//960+72*96//960+2)==color_t(track),(light,table)
passed()
# A list with no bar, a bar that has no range yet: nothing, and no crash.
m_,parent,bar=sb_machine(960,0); m_.nodes[parent]['children']=m_.nodes[parent]['children'][:1]; assert sb_drawn(m_,parent)==[]
m_,parent,bar=sb_machine(960,0); m_.word(bar+O['BAR_MAX'],0); assert sb_drawn(m_,parent)==[]; passed()
# A playlist's More, a dialog over Playlists under OLED's modal set (tools/ipod.py MODAL_PAGES): stock's Rename and
# Delete rows, then Edit Playlist (ringnav_playlistsmore), which opens the editor.
def pl_more():
    m=oled_set(PlaylistEditMachine()); more=m.node('dialog','playlistsmore_page'); m.word(more+O['W_PARENT'],m.wm)
    m.word(more+O['W_W'],375); m.word(more+O['W_H'],290); m.top=more; m.stack.append(more); rows=[]
    for i,(item,name) in enumerate((('item_rename','label_rename'),('item_del','label_del'))):
        button=m.node('button'); label=m.node('hscroll_label',name); m.nodes[button]['children'].append(label); m.word(label+O['W_PARENT'],button)
        row=m.node('list_item',item,children=[button]); m.word(button+O['W_PARENT'],row); m.word(row+O['W_PARENT'],more); m.word(row+O['W_Y'],i*32); m.nodes[more]['children'].append(row); rows.append(row)
    m.call(address=HOOKS['localmusic_playlistsmore_page_init'][0],args=(more,0,0,0),gap=0)
    edit=[c for c in m.nodes[more]['children'] if m.nodes[c]['type']=='list_item' and c not in rows]
    return m,more,rows,edit
m,more,rows,edit=pl_more()
assert len(edit)==1 and [m.nodes[k].get('text') for k in m.nodes[m.nodes[edit[0]]['children'][0]]['children'] if m.nodes[k]['type']=='hscroll_label']==['Edit Playlist']; passed()

# Add Songs: the bar holds the task's name and the list starts at its Search row, its long rows still only as selected.
m,more,rows,edit=pl_more(); f,ctx=m.handler(m.nodes[edit[0]]['children'][0],O['EVT_CLICK'])
m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.page=m.top; m.view=m.find('scroll_view')
m.choose(0); assert m.showlabels()[0]=='Search' and m.nodes[m.nodes[m.page]['children'][0]]['text']=='Add Songs'
assert any(c[0]=='hscroll_label_set_only_focus' for c in m.calls); passed()
# SD Card Update (sw_update.c): stock's fwupdate_tf is the payload's whole. Stock's own checks (check_updatefw_exist, its
# answer the machine's) are mode pages at once, each its words and glyph; a found file is checked on a thread under the
# spinner, whose answer opens Install Update? (OK) or its page; Install and Restart runs stock's checkfw_dialog after the
# click, whose answer is a page too; and stock's dialog is drawn as Restarting to Install. The tar check itself is
# test/fw's (test_sw_update.py).
SW_CHECKING,SW_OK,SW_LATEST,SW_NONE,SW_NOCARD,SW_LOW,SW_BAD,SW_FULL=range(8)
class SwMachine(CoverflowMachine):
    def __init__(self,code=1,quit=0):
        super().__init__(); oled_set(self); self.code,self.quit,self.idles=code,quit,[]
        self.handlers.pop(HOOKS['fwupdate_tf'][0],None)  # FUNCTIONS' stand-in: run the hooked entry (sw_update.c)
        for n in ('check_updatefw_exist','navigator_to','idle_add'): self.handlers[syms[n]]='sw:'+n
        self.word(syms['g_power_capacity'],12)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('sw:'): return super().hook(u,address,size,unused)
        name=name[3:]; a,b=[u.reg_read(r) for r in REGS[:2]]; self.calls.append((name,a,b)); ret=0
        if name=='check_updatefw_exist': assert (self.text(a),self.text(b))==('/mnt/mmc','/mnt/mmc/update.tar'); ret=self.code
        elif name=='navigator_to': self.opened.append((self.text(a),)); ret=self.quit
        elif name=='idle_add': self.idles.append((a,b)); ret=1
        self.returns(ret,clobber=True)
    def sw(self,field): return signed(self.get(payload_syms['swu']+{'state':4,'result':12,'frame':20}[field]))
    def said(self,w):
        self.letters.clear(); self.image_loads.clear(); self.paint(w,gap=0)
        return [l['text'] for l in self.letters],[i.rsplit('_',1)[-1] if 'swspin' not in i else i.split('swspin_')[1] for i in self.image_loads if 'swspin' in i or 'modepage' in i]
def sw_open(code=1,quit=0):
    m=SwMachine(code,quit); m.call(address=syms['fwupdate_tf'],args=(0,0,0,0),gap=0); return m
for code,result,words,glyph in ((-15,SW_LOW,['Charge to Update','Needs 16% or a charger \u00b7 now 12%'],'low'),
                                (-2,SW_NOCARD,['No SD Card','Insert the card with update.tar'],'none'),
                                (-11,SW_NONE,['No Update Found','Put update.tar on the SD card'],'none'),
                                (-12,SW_FULL,['Not Enough Space','Free 100 MB on the SD card'],'bad'),
                                (-3,SW_BAD,["Update Can't Be Used",'The file is damaged or not for the Q2'],'bad')):
    m=sw_open(code); page=m.top
    assert m.nodes[page]['name']=='swupdate_page' and m.sw('result')==result and not m.threads and not m.timers,code
    texts,images=m.said(page); assert texts==words and images==[glyph],(code,texts,images)
    assert len(m.rounded)==1 and m.rounded[0]['rect']==(375//2-46,40+(320-40-158)//2-signed(m.get(page+O['W_Y'])),92,92),m.rounded
    passed()
# A found file: the thread, the spinner turning a spoke each 80 ms, no circle; Return leaves and cancels it.
m=sw_open(); page=m.top
assert m.sw('result')==SW_CHECKING and m.sw('state')==SW_CHECKING and len(m.threads)==1 and m.threads[0][0]==payload_syms['sw_check']
texts,images=m.said(page); assert texts==['Checking for an Update\u2026','Looking on the SD card'] and images==['d_0'] and not m.rounded,(texts,images)
m.advance(160); texts,images=m.said(page); assert images==['d_2'],images
m.call(address=syms['fwupdate_tf'],args=(0,0,0,0),gap=0); assert len(m.threads)==1 and m.top==page  # one at a time
passed()
# Its answer: OK asks Install Update? on the confirmation card over it (feedback.c confirm, which opens stock's
# sortselect_dialog); another, its page. The card's answer (sw_update.c sw_answer) closes the page, Install and Restart
# then running stock's dialog from an idle.
for state in (SW_OK,SW_LATEST,SW_BAD):
    m=sw_open(); page=m.top; m.word(payload_syms['swu']+4,state); m.u.mem_write(payload_syms['swu']+0x30,b'V1830\0')
    m.advance(80); assert m.joins and not m.timers,state
    if state!=SW_OK: assert m.top==page and m.sw('result')==state; passed(); continue
    assert ('dialog/sortselect_dialog',) in m.opened or m.top!=page,m.opened
    m.call(address=payload_syms['sw_answer'],args=(0,1,0,0),gap=0,clear=False)
    assert len(m.idles)==1 and not m.alive(page); passed()
# After the click, stock's dialog; its answer (no file 9, up to date 13, damaged 18) is a page, a success never comes back.
for quit,result in ((9,SW_NONE),(13,SW_LATEST),(18,SW_BAD)):
    m=SwMachine(quit=quit); m.call(address=payload_syms['sw_install'],args=(0,0,0,0),gap=0)
    assert ('dialog/checkfw_dialog',) in m.opened and m.nodes[m.top]['name']=='swupdate_page' and m.sw('result')==result,quit
passed()
# Stock's checkfw_dialog: its label hidden, the dialog drawn as Restarting to Install with no title over it (the status
# row names no page), and the bar the unpacked images' share (none here: no files).
m=SwMachine(); label=m.node('hscroll_label','scrlabel_checkfw',text='Checking FW...'); dlg=m.node('dialog','checkfw_dialog',[label])
parents(m,dlg); m.word(dlg+O['W_PARENT'],m.wm); place(m,dlg,0,0,375,320); m.top=dlg
m.handlers[payload_syms['stock_checkfw_trampoline']]='stock_checkfw'
m.call(address=syms['dialog_checkfw_dialog_init'],args=(dlg,0,0,0),gap=0)
assert not m.nodes[label]['visible']; texts,images=m.said(dlg)
assert texts==['Restarting to Install',"Don't turn off the Q2"] and images==['restart'],(texts,images)
top=40+(320-40-158)//2; assert [r['rect'] for r in m.rounded]==[(375//2-46,top,92,92),(375//2-90,top+172,180,6)],m.rounded; passed()
# Jack detect (docs/internals.md#jack-detect): stock's own check_headset_status, run here, reads PA07 (3.5 mm) and
# PA08 (4.4 mm) through toolsGpioGetValue, 0 plugged. Powering the DAC up at Play read PA07 unplugged for about 120 ms
# with a cable in (the user's Q2, 2026-11-25, Build I): a poll inside it cleared g_po_status, the headphones glyph
# went, and config_po_output(0) paused the song just started. An unplug read now counts only when it is still read
# JACK_UNPLUG_MS later, at stock's next poll; a plug, and the second of two unplugged reads, go through at once.
class JackMachine(Machine):
    def __init__(self):
        super().__init__(); self.pins={'PA07':0,'PA08':1}; self.outputs=[]; self.logs=[]
        self.handlers.pop(syms['toolsGpioGetValue'],None)  # the patched read runs: its hook, then stock's read, mocked here
        self.handlers[int(manifest['patch_symbols']['stock_gpio_value_trampoline'],16)]='jk:pin'
        self.handlers[syms['config_po_output']]='jk:3.5'; self.handlers[syms['config_bal_output']]='jk:4.4'
        self.handlers[payload_syms['ulog']]='jk:ulog'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('jk:'): return super().hook(u,address,size,unused)
        name=name[3:]; a=u.reg_read(REGS[0]); ret=0
        if name=='pin': ret=self.pins.get(self.text(a),1)
        elif name=='ulog': self.logs.append((self.text(u.reg_read(REGS[1])),self.text(u.reg_read(REGS[2]))))
        else: self.outputs.append((name,a)); ret=1  # config_po_output(0) at an unplug: player_save_stop while it plays
        self.returns(ret)
    def poll(self,gap=1000):  # on_wm_idle_status's call, once a second
        self.outputs=[]; self.call(address=syms['check_headset_status'],args=(0,0,0,0),gap=gap); return self.outputs
    def po(self): return self.u.mem_read(syms['g_po_status'],1)[0]
m=JackMachine(); m.byte(syms['g_po_status'],1)
assert m.poll()==[] and m.po()==1
m.pins['PA07']=1; assert m.poll()==[] and m.po()==1, (m.outputs, m.po())  # Play's 120 ms glitch, read once: no pause
m.pins['PA07']=0; assert m.poll()==[] and m.po()==1  # back at the next poll
assert ('jack','3.5 mm jack: an unplugged read that did not last, ignored') in m.logs; passed()
m.logs=[]; m.pins['PA07']=1; assert m.poll()==[] and m.po()==1  # a real unplug: the first read is held
assert m.poll(O['JACK_UNPLUG_MS'])==[('3.5',0)] and m.po()==0  # still unplugged a poll later: stock pauses
assert m.logs==[('jack','3.5 mm jack unplugged')]; passed()
m.logs=[]; m.pins['PA07']=0; assert m.poll()==[('3.5',1)] and m.po()==1 and m.logs==[('jack','3.5 mm jack plugged')]; passed()
m.logs=[]; m.pins['PA08']=0; assert m.poll()==[('4.4',1)] and m.logs==[('jack','4.4 mm jack plugged')]
m.pins['PA08']=1; assert m.poll()==[]; assert m.poll(O['JACK_UNPLUG_MS']-1)==[]  # read again too soon: still held
assert m.poll(1)==[('4.4',0)] and m.logs[-1]==('jack','4.4 mm jack unplugged'); passed()
# A plug back in after an unplug's pause (docs/internals.md#jack-detect). Measured on the user's Q2 (2026-11-25, Build
# J): once PA07 has read unplugged with the DAC powered, it reads unplugged with the cable back in for as long as the
# DAC stays on and muted, and reads true again about 0.6 s after the DAC powers off (or once Play unmutes it). Stock's
# check_dacoff_state (0x4f3f98), run before the jack poll in each once-a-second on_wm_idle_status pass, powers the DAC
# off (mclSetDacPwr(0), g_dacoff_time -1) only when its count of seconds not playing reaches 31, so the plug was seen
# about 31 s late. Both stock functions run here with the pin modelled so; the DAC now powers off at the pass after
# an unplug that leaves no jack in, and a normal pause keeps stock's 31 s.
class DacJackMachine(JackMachine):
    def __init__(self):
        super().__init__(); self.plugged={'PA07':True,'PA08':False}; self.dac=1; self.off_at=0; self.latched=False
        self.status=2; self.powers=[]
        for name,h in (('mclGetPlayStatus','status'),('mclSetDacPwr','dacpwr'),('printf@GLIBC_2.0','nop'),
                       ('mclGetOutputWay','nop'),('mclSetOutputWay','nop'),('mclSetHeadsetMode','nop')):
            self.handlers[syms[name]]='jk:'+h
        self.handlers.pop(syms['config_outputchannel'],None)  # stock's runs: Play's power-up
        for name in ('bt__recv_pageflag','g_usblink_status','g_usbmode','g_po_status','g_bal_status'): self.byte(syms[name],0)
        self.byte(syms['g_po_status'],1); self.word(syms['g_dacoff_time'],0)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name not in ('jk:pin','jk:status','jk:dacpwr','jk:nop','jk:3.5'): return super().hook(u,address,size,unused)
        a=u.reg_read(REGS[0]); ret=0
        if name=='jk:pin':
            pin=self.text(a); ret=0 if self.plugged[pin] else 1
            if pin=='PA07':
                if not self.plugged[pin] and self.dac: self.latched=True
                if self.latched and self.plugged[pin] and (self.status==2 or (not self.dac and self.now-self.off_at>=600)): self.latched=False
                ret|=self.latched
        elif name=='jk:status': ret=self.status
        elif name=='jk:dacpwr': self.powers.append(a); self.dac=a; self.off_at=self.now
        elif name=='jk:3.5':
            self.outputs.append(('3.5',a)); ret=1
            if not a and self.status==2: self.status=3  # stock's mclSetPause: the DAC stays on, muted
        self.returns(ret)
    def tick(self):  # one on_wm_idle_status pass: check_dacoff_state, then check_headset_status
        self.call(address=syms['check_dacoff_state'],args=(0,0,0,0),gap=1000); return self.poll(gap=0)
    def dacoff(self): return signed(self.get(syms['g_dacoff_time']))
    def unplug_pause(self):  # playing, the cable pulled: held one pass, through at the next, and stock pauses
        self.plugged['PA07']=False; assert self.tick()==[]; assert self.tick()==[('3.5',0)] and self.status==3
def seconds_to_plug(m):
    for n in range(1,41):
        if m.tick()==[('3.5',1)]: return n
m=DacJackMachine(); m.tick(); m.tick(); assert m.po()==1 and m.dacoff()==0 and m.powers==[]
m.logs=[]; m.unplug_pause(); m.plugged['PA07']=True  # straight back in, nothing pressed
n=seconds_to_plug(m); assert n is not None and n<=2, f'the plug back in was seen {n} s after the pause'
assert m.powers==[0] and m.dacoff()==-1 and m.po()==1
assert ('jack','3.5 mm jack unplugged with no jack left: the DAC powered off now, not 31 s on, so a plug back in is read') in m.logs, m.logs
# the next Play: stock's player_play_pause resumes through config_outputchannel(g_headset_output, 2), which powers
# the DAC (mclSetDacPwr(1)) and starts the count again, as after stock's own 31 s
m.call(address=syms['config_outputchannel'],args=(0,2,0,0),gap=0); assert m.powers==[0,1] and m.dacoff()==0
m.status=2; assert m.tick()==[] and m.po()==1 and m.dacoff()==0; passed()
m=DacJackMachine(); m.tick(); m.status=3  # a person's pause, the cable in: the DAC stays on for stock's 31 s
for s in range(1,31): assert m.tick()==[] and m.powers==[] and m.dacoff()==s
assert m.tick()==[] and m.powers==[0] and m.dacoff()==-1 and m.po()==1; passed()
m=DacJackMachine(); m.plugged['PA08']=True; m.tick(); m.tick(); m.unplug_pause()  # the 4.4 mm jack still in
for s in range(3): m.tick()
assert m.powers==[] and m.dacoff()==3; passed()
print(f'{checks} MIPS execution scenarios passed; toolkit services mocked, stock lock filter executed.')
