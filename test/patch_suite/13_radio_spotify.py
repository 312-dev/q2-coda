class RadioMachine(SpotMachine):
    """SpotMachine's side, plus Wi-Fi and demo's libcurl: perform hands reply to the write callback."""
    def __init__(self,files=None):
        super().__init__(files or {})
        for n in ('curl_easy_init','curl_easy_setopt','curl_easy_perform','curl_easy_getinfo','curl_easy_cleanup','get_wifisignal',
                  'mkdir@GLIBC_2.0'):
            self.handlers[syms[n]]='r:'+n
        self.mock('fflush@GLIBC_2.0','fsync@GLIBC_2.0','fileno@GLIBC_2.0','ferror@GLIBC_2.0')
        self.wifi=3; self.reply=b''; self.code=200; self.codes=[]; self.location=''; self.opts={}
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('r:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='get_wifisignal': ret=self.wifi
        elif name=='mkdir': self.tree[self.text(a)]=[]
        elif name=='curl_easy_init': ret=0x5150; self.opts={}  # each handle starts with curl's defaults
        elif name=='curl_easy_setopt': self.opts[b]=self.text(c) if b in (10002,10018,10065) else c
        elif name=='curl_easy_perform' and 20011 not in self.opts: ret=0  # NOBODY: headers only
        elif name=='curl_easy_perform':  # the write callback, run nested as qsort's comparator is
            data=self.alloc((len(self.reply)+7)&~3); u.mem_write(data,self.reply)
            took=self.nested(self.opts[20011],data,1,len(self.reply),self.opts.get(10001,0))
            ret=0 if took==len(self.reply) and self.code else 23  # CURLE_WRITE_ERROR
        elif name=='curl_easy_getinfo' and b==0x10001f:  # REDIRECT_URL
            text=self.alloc(len(self.location)+4); u.mem_write(text,self.location.encode()+b'\0'); self.word(c,text)
        elif name=='curl_easy_getinfo': self.word(c,self.codes.pop(0) if self.codes else self.code)
        self.calls.append((name,a,b,c))
        self.returns(ret,clobber=True)
    def stream(self):
        view=self.node('scroll_view','scroll_view_streamsset',[self.node('list_item')]); self.top=self.node('window','stream_page',[view])
        assert self.call(address=HOOKS['stream_page_init'][0],args=(self.top,5,0,0),gap=0,count=5_000_000)==0
        return self.nodes[self.nodes[view]['children'][3]]['children'][0]
    def click(self,w):
        f,ctx=self.handler(w,O['EVT_CLICK']); assert self.call(address=f,args=(ctx,self.event,0,0),gap=0,count=5_000_000)==0
        self.advance(0,clear=False)
    def rows(self): return self.nodes[self.find('scroll_view',self.page)]['children']
    def row(self,i): self.click(self.rows()[i])
    def labels(self,w=None):
        """Texts still linked below w (page_list's destroy_children mock only unlinks)."""
        def linked(x,w):
            while x!=w:
                parent=self.get(x+O['W_PARENT'])
                if parent not in self.nodes or x not in self.nodes[parent]['children']: return False
                x=parent
            return True
        w=w or self.page
        return [n.get('text','') for x,n in self.nodes.items() if n['type']=='hscroll_label' and linked(x,w)]  # a new label's text is ''
    def back(self,w=None):
        """Return on page w (the radio list by default), and its timers."""
        w=w or self.page; f,ctx=self.handler(w,O['EVT_KEY_UP']); ev=self.alloc(0x40)
        self.word(ev,O['EVT_KEY_UP']); self.word(ev+O['EVENT_KEY'],O['KEY_RETURN'])
        assert self.call(address=f,args=(ctx,ev,0,0),gap=0,count=5_000_000)==11; self.advance(0,clear=False)
    def destroy(self,w):
        f,ctx=self.handler(w,O['EVT_DESTROY']); self.call(address=f,args=(ctx,self.event,0,0),gap=0)
    def worker(self):
        worker,arg=self.threads[-1]; assert self.call(address=worker,args=(arg,0,0,0),gap=0,count=5_000_000)==0; self.advance(250)
def radio_open(m):
    button=m.stream(); icon,label=m.nodes[button]['children'][:2]
    assert m.nodes[icon]['image']=='stream_radio' and m.nodes[label]['text']=='Internet Radio' and not m.nodes[button].get('name')
    m.click(button); m.page=m.top; assert m.nodes[m.page]['name']=='radio_page'; return m.page
# The row follows Spotify's. The first open makes the card's Radio folder but leaves Favourites
# empty, the user's to fill; the starter stations are Featured. The menu has no Now Playing until
# something has played. An empty list is the empty state (feedback.c empty_list): the hidden caption holds the
# page's name, and the empty state says "No Stations".
m=RadioMachine(); page=radio_open(m)
assert RDIR in m.tree and RDIR+'/favourites.m3u' not in m.files
assert m.labels()==['Internet Radio','Favorites','Featured','Top Stations','By Country','By Genre'] and m.nodes[m.find('scroll_view')]['_ringnav_index']==0
m.row(0); assert m.labels()==['Favorites'] and empty_titles(m)[-1:]==['No Stations']; m.back()
m.row(1); featured=m.labels(); assert len(featured)==7 and featured[:3]==['Featured','Radio Paradise','SomaFM Groove Salad']; m.back()
# The user's favourites.m3u is never touched by opening the page.
m.close(); m.files[RDIR+'/favourites.m3u']=bytearray(b'#EXTM3U\n'); radio_open(m); assert RDIR+'/favourites.m3u' in m.files
# Search, from the page's pull-down (radio_search): a T9 box over the title, focused for its keyboard.
# Its text, %-encoded, is the directory's byname query; Return goes back to the menu. Left empty,
# or without a search, the page is as it was.
def search(text,event='EVT_VALUE_CHANGED'):
    assert m.call(address=payload_syms['radio_search'],gap=0,count=5_000_000)==0
    edit=m.find('edit'); assert m.nodes[edit]['focused']==1
    y=m.get(edit+O['W_Y'])-OLED_G.bar  # the full-bleed page's rows from the status row's foot (bar_foot)
    assert 0<=y and y+m.get(edit+O['W_H'])<=30 and not m.find('scroll_view')  # above the keyboard, alone
    m.nodes[edit]['text']=text; f,ctx=m.handler(edit,O[event])
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0; m.advance(0,clear=False)
search('jazz fm'); assert m.labels()[0]=='Internet Radio' and 'Loading…' not in m.labels() and not m.find('edit')  # the box closed onto the menu, the loading overlay over it
m.reply=b'#EXTM3U\n#EXTINF:1,Jazz FM\nhttp://jazz/fm\n'; m.worker()
assert m.opts[10002]=='https://all.api.radio-browser.info/m3u/stations/byname/jazz%20fm?order=votes&reverse=true&limit=100&hidebroken=true'
assert m.labels()==['jazz fm','Jazz FM']; m.back(); assert m.labels()[0]=='Internet Radio'
search('café'); m.worker(); assert '/byname/caf%C3%A9?' in m.opts[10002] and m.labels()[0]=='café'; m.back()  # UTF-8, not narrowed wchar
for text,event in (('','EVT_VALUE_CHANGED'),('','EVT_IM_ACTION'),('abc','EVT_BLUR')):
    search(text,event); assert m.labels()[:2]==['Internet Radio','Favorites'] and not m.find('edit')
# Return with the keyboard open empties and blurs the box, never destroying it under its keyboard; the blur shuts it.
assert m.call(address=payload_syms['radio_search'],gap=0,count=5_000_000)==0; edit=m.find('edit'); m.nodes[edit]['text']='jazz'
m.back(); assert m.nodes[edit]['focused']==0 and m.nodes[edit]['text']==''; f,ctx=m.handler(edit,O['EVT_BLUR'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0; m.advance(0,clear=False)
assert m.labels()[:2]==['Internet Radio','Favorites'] and not m.find('edit')
search('é'*31); m.worker(); assert m.labels()[0]=='é'*31; m.back()  # 62 UTF-8 bytes fit
m.row(1); assert m.labels()==featured
for event in ('EVT_VALUE_CHANGED','EVT_IM_ACTION'):
    before=len(m.toasts)
    search('',event); assert m.labels()==featured and not m.find('edit') and len(m.toasts)==before
for query in ('x'*64,'é'*32):
    before=len(m.toasts); workers=len(m.threads)
    search(query)
    assert m.labels()==featured and not m.find('edit') and len(m.threads)==workers
    assert len(m.toasts)==before+1 and m.toasts[-1][3]=='Search is too long'
m.back()
search('zzz'); m.reply=b'#EXTM3U\n'; m.worker(); assert m.labels()==['Internet Radio'] and empty_titles(m)[-1:]==['No Stations']; m.close(); passed()
# An empty list is the empty state, and the bar reads the entry that opened it: Favourites only from Favourites.
m=RadioMachine(); rbar=page_bar(m); radio_open(m)
def rbar_reads():  # the old caption's destroy, which the mock's unlink doesn't fire, and the bar asked again
    m.call(address=payload_syms['title_gone'],args=(0,0,0,0),gap=0); m.call(address=payload_syms['bar_title_sync'],args=(m.top,0,0,0),gap=0); return m.nodes[rbar]['text']
m.row(0); assert empty_titles(m)[-1:]==['No Stations'] and rbar_reads()=='Favorites',(m.labels(),rbar_reads())
search('zzz'); m.reply=b'#EXTM3U\n'; m.worker(); assert empty_titles(m)[-1:]==['No Stations'] and rbar_reads()=='Internet Radio',(m.labels(),rbar_reads())
m.back(); assert empty_titles(m)[-1:]==['No Stations'] and rbar_reads()=='Internet Radio',(m.labels(),rbar_reads()); m.close(); passed()  # the search's empty list again
# Favourites: favourites.m3u, then the folder's other .m3u and .pls files (a .pls's TitleN names its FileN).
m=RadioMachine({RDIR+'/favourites.m3u':bytearray(b'#EXTM3U\r\n#EXTINF:-1,One\r\nhttp://one/a\r\nhttp://two/b\r\n'),
                RDIR+'/x.pls':bytearray(b'[playlist]\nFile1=https://three/c?x=1\nTitle1=Three\nNumberOfEntries=1\n'),
                RDIR+'/notes.txt':bytearray(b'http://no/')})
m.tree[RDIR]=[('favourites.m3u',8),('x.pls',8),('notes.txt',8),('._x.pls',8)]; page=radio_open(m)
m.row(0); assert m.labels()==['Favorites','One','http://two/b','Three']; passed()
# A station: local music stops, then q2video -r on the headphone DAC's PCM with the URL as plain
# argv, Now Playing over the list, and the station kept as the last. The page says it connects.
m.forked=0; m.row(2)  # the child
names=[c[0] for c in m.calls]; assert names.index('player_stop')<names.index('config_outputchannel')<names.index('mclSetMute')<names.index('fork')
assert m.execs==[('/usr/bin/q2video','/usr/bin/q2video','-r','plughw:0,0',('https://three/c?x=1',0))]
m.forked=4343; m.row(2); np=m.top; assert m.nodes[np]['name']=='radionp_page' and m.files[RDIR+'/.last']==b'#EXTM3U\n#EXTINF:-1,Three\nhttps://three/c?x=1\n'
m.advance(500); assert m.labels(np)==['Internet Radio','Three','','','Connecting…','']
# The heart, at local Now Playing's place: Three is no favourite.
np_body=lambda np: next(c for c in m.nodes[np]['children'] if m.nodes[c]['type']=='view')  # Spotify's view, from the status row's foot (bar_foot)
assert m.get(np_body(np)+O['W_Y'])==OLED_G.bar
heart=next(w for w in m.nodes[np_body(np)]['children'] if m.nodes[w].get('image') in ('play_fav','play_unfav'))
heart_y=O['NP_ART_TOP']+O['SPOT_ART_PX']-50+O['NP_ICON_PAD']
from ipod import NP_ICONS_END, NP_ICON
heart_x=NP_ICONS_END-NP_ICON
assert m.nodes[heart]['image']=='play_unfav' and [m.get(heart+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[heart_x,heart_y,50,50]; passed()
# Playing: its tags (the StreamTitle as artist - title), the stream and the time since the sound
# started; standby and auto power-off held (not the screen's timer) and the DAC kept on.
m.word(syms['g_dacoff_time'],5)
m.files[RSTATE]=bytearray(f'state=playing\ntitle=Some Artist - A Song\ncodec=MP3\nbitrate=128\nat={m.now}\n'.encode())
m.poll(); assert ('reset_poweroptions_timer',1,1,0) in [c[:4] for c in m.calls] and m.get(syms['g_dacoff_time'])==0
m.advance(3000); assert m.labels(np)==['Internet Radio','Three','Some Artist','A Song','MP3  128 kbps','0:03']
# The volume reaches q2video for Bluetooth's and USB's soft volume, once a change.
m.byte(syms['g_volume'],44); m.poll(); assert m.sent==[(m.sent[0][0],'v,',2,0x40,1,RSOCK,110)]; m.poll(); assert not m.sent; passed()
# Play/Pause stops it (q, then its end) and starts it again; the side buttons step through the
# list it was played from, round the ends; on any page.
def last(): return m.files[RDIR+'/.last'].decode().split('\n')[2]
def forks(): return [c[0] for c in m.calls].count('fork')
m.top=m.node('window','home_page'); m.waited=4343
assert m.key(O['KEY_PLAY'])==11 and m.sent[0][1]=='q' and RSTATE not in m.files and not forks()
assert m.key(O['KEY_PLAY'])==11 and forks()==1 and last()=='https://three/c?x=1'
assert m.key(O['KEY_FWD_BTN'])==11 and m.sent[0][1]=='q' and forks()==1 and last()=='http://one/a'
assert m.key(O['KEY_BACK_BTN'])==11 and last()=='https://three/c?x=1'; passed()
# Media keys target the active radio even with local Now Playing visible.
m.top=m.node('window','playing_page')
assert m.key(O['KEY_FWD_BTN'])==11 and forks()==1
m.top=m.node('window','home_page'); passed()
# Local music starting takes the output: q2video quits first, and the keys are local music's again.
names=start(); assert m.sent[0][1]=='q' and names.index('waitpid')<names.index('stock_start_player')
assert m.key(O['KEY_PLAY'])!=11 and not forks(); passed()
# A q2video that does not quit within RADIO_QUIT_MS is killed.
m.top=page; m.row(1); m.waited=0; m.top=m.node('window','home_page')
m.handlers[syms['waitpid@GLIBC_2.0']]='s:waitpid'  # WNOHANG polls say running; the blocking wait reaps
t0=m.now; assert m.key(O['KEY_PLAY'])==11 and 1500<=m.now-t0<=1540 and m.systems[-1]=='kill -9 %d'%m.forked
m.handlers[syms['waitpid@GLIBC_2.0']]='b:waitpid@GLIBC_2.0'; passed()
# One that gives up says so.
assert m.key(O['KEY_PLAY'])==11; m.files[RSTATE]=bytearray(b'state=error\n'); m.waited=4343; m.poll()
m.advance(500); assert m.labels(np)[4]=="Can't play this station" and 'reset_poweroptions_timer' not in [c[0] for c in m.calls]
# Its ALSA reason, when it had no device, is shown.
assert m.key(O['KEY_PLAY'])==11; m.files[RSTATE]=bytearray(b'state=error\nerror=open: Device or resource busy\n'); m.waited=4343; m.poll()
m.advance(500); assert m.labels(np)[4]=="Can't play: open: Device or resource busy"
# Holding Play/Pause on Now Playing saves the station playing, and again takes it out.
m.top=np; m.status=m.alloc(0x200); m.press(77)
playing=last().encode(); before=bytes(m.files[RDIR+'/favourites.m3u']); assert playing in before  # http://two/b, a favourite
assert m.hold()==11 and m.toasts[-1][3]=='Removed from Favorites' and playing not in m.files[RDIR+'/favourites.m3u']
m.press(77); assert m.hold()==11 and m.toasts[-1][3]=='Added to Favorites' and m.files[RDIR+'/favourites.m3u'].endswith(playing+b'\n')
# Tapping the heart toggles it too, and the heart follows the file.
heart=next(w for w in m.nodes[np_body(np)]['children'] if m.nodes[w].get('image') in ('play_fav','play_unfav'))
m.advance(500); assert m.nodes[heart]['image']=='play_fav'
f,ctx=m.handler(heart,O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
assert m.toasts[-1][3]=='Removed from Favorites' and playing not in m.files[RDIR+'/favourites.m3u']
m.advance(500); assert m.nodes[heart]['image']=='play_unfav'
m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000); m.advance(500)
assert m.toasts[-1][3]=='Added to Favorites' and m.nodes[heart]['image']=='play_fav'; passed()
# Holding Play/Pause on a station takes it out of favourites.m3u, or adds it; the release is
# swallowed, the list follows, and elsewhere the hold is stock's.
m.back(np); assert 'navigator_back' in [c[0] for c in m.calls]; m.destroy(np); m.top=page
rows=m.find('scroll_view'); m.nodes[rows]['_ringnav_index']=0; m.status=m.alloc(0x200); m.press(77)
assert m.hold()==11 and m.toasts[-1][3]=='Removed from Favorites'
assert m.files[RDIR+'/favourites.m3u']==b'#EXTM3U\n#EXTINF:-1,http://two/b\nhttp://two/b\n' and m.labels()==['Favorites','http://two/b','Three']
m.nodes[m.find('scroll_view')]['_ringnav_index']=1; assert m.hold()==11 and m.toasts[-1][3]=='Added to Favorites'
assert m.files[RDIR+'/favourites.m3u'].endswith(b'#EXTINF:-1,Three\nhttps://three/c?x=1\n')
assert m.call(O['KEY_PLAY'],gap=0)==11 and not m.sent; passed()
# Editing a capped or partially read list must never discard unseen favourites. Exactly 200
# stations still supports removal; adding to a full list keeps the existing limit.
for count, padding, saved in ((200, b'', True), (201, b'', False),
                              (1, b'#' + b'x' * (256 << 10), False), (1, b'\0http://hidden\n', False)):
    original = b'#EXTM3U\n' + b''.join(f'http://station/{i}\n'.encode() for i in range(count)) + padding
    large = RadioMachine({RDIR+'/favourites.m3u': bytearray(original)})
    large.budget = 5_000_000
    radio_open(large); large.row(0)
    large.nodes[large.find('scroll_view')]['_ringnav_index'] = 0
    large.status = large.alloc(0x200); large.press(77); assert large.hold() == 11
    assert large.toasts[-1][3] == ('Removed from Favorites' if saved else 'Could not save Favorites')
    assert bytes(large.files[RDIR+'/favourites.m3u']) == (b'#EXTM3U\n' + b''.join(
        f'#EXTINF:-1,http://station/{i}\nhttp://station/{i}\n'.encode() for i in range(1, count)) if saved else original)
passed()
# Top Stations: the directory's m3u, fetched on the worker over verified TLS, the menu under the loading overlay
# meanwhile.
# Return goes up a level at a time, then back to Streaming. Without Wi-Fi it says so.
m.back(); assert m.labels()[0]=='Internet Radio' and m.labels()[1]=='Now Playing'
m.row(3); assert m.labels()[:2]==['Internet Radio','Now Playing'] and len(m.threads)==1
m.reply=b'#EXTM3U\n#RADIOBROWSERUUID:7\n#EXTINF:1,MANGORADIO\nhttps://mango/r\n\n#EXTINF:1,Dance Wave!\nhttps://dance/w.mp3\n'
m.worker(); assert m.labels()==['Top Stations','MANGORADIO','Dance Wave!']
assert m.opts[10002]=='https://all.api.radio-browser.info/m3u/stations/topvote/100?hidebroken=true'
assert m.opts[10065]=='/etc/scrobble-ca.pem' and m.opts[64]==1 and m.opts[81]==2 and m.opts[99]==1
assert 20108 not in m.opts  # no SSL_CTX_FUNCTION: libcurl's mbedTLS checks no dates (docs/internals.md#2038)
m.back(); assert m.labels()[0]=='Internet Radio'
m.wifi=-1; m.row(3); assert m.labels()==['Internet Radio','No Wi-Fi',''] and len(m.threads)==1; m.back(); m.wifi=3; passed()
# By Genre: the tags' CSV, then a tag's stations by its name, %-encoded. Both A-Z, a leading article
# skipped as in the library; a search pulled over them keeps the directory's vote order.
m.row(5); m.reply=b'name,stationcount\npop,6387\nclassic rock,3312\n'; m.worker()
assert m.labels()==['Genres','classic rock','pop']
m.row(0); m.reply=b'#EXTM3U\n#EXTINF:1,Rock FM\nhttp://rock/fm\n#EXTINF:1,The Arrow\nhttp://arrow/fm\n#EXTINF:1,Absolute\nhttp://abs/fm\n'; m.worker()
assert m.opts[10002]=='https://all.api.radio-browser.info/m3u/stations/bytagexact/classic%20rock?order=votes&reverse=true&limit=100&hidebroken=true'
assert m.labels()==['classic rock','Absolute','The Arrow','Rock FM']
search('rock'); m.reply=b'#EXTM3U\n#EXTINF:1,Rock FM\nhttp://rock/fm\n#EXTINF:1,Absolute\nhttp://abs/fm\n'; m.worker()
assert m.labels()==['rock','Rock FM','Absolute']; m.back(); assert m.labels()[0]=='Genres'
# By Country: by code; a name may be quoted. A failed fetch says so; Return leaves the note.
m.back(); m.row(4); m.reply=b'name,iso_3166_1,stationcount\n"Taiwan, Republic Of China",TW,214\nGermany,DE,6496\nThe Netherlands,NL,1200\n'; m.worker()
assert m.labels()==['Countries','Germany','The Netherlands','Taiwan, Republic Of China']
m.row(2); m.code=0; m.worker(); assert m.labels()==['Internet Radio','Directory unavailable',''] and 'bycountrycodeexact/TW?' in m.opts[10002]
m.back(); assert m.labels()==['Countries','Germany','The Netherlands','Taiwan, Republic Of China']
m.back(); m.calls=[]; m.back(); assert 'navigator_back' in [c[0] for c in m.calls]; m.close(); passed()

# Download Cover & Lyrics' lyrics (onlinematch.c ringnav_lyric_search, at kugou_obtainlyric's entry): LRCLIB
# alone. keyinfo's title and artist are searched; the first result with synced lyrics goes to out with a BOM,
# less its opening credits. None found, the names without accents are searched, if that changes them; a busy
# reply is asked again, three requests in all. Found or not, it returns 1, so online_lyricthd skips NetEase,
# and Kugou's own search (past the entry's jump) never runs. demo's cJSON is stood in for by Python's json.
class LyricSearchMachine(RadioMachine):
    def __init__(self):
        super().__init__(); self.written={}; self.json={}
        for n in ('fopen@GLIBC_2.2','fputs@GLIBC_2.0','fclose@GLIBC_2.2','cJSON_Parse','cJSON_GetArraySize',
                  'cJSON_GetArrayItem','cJSON_GetObjectItem','cJSON_Delete'):
            self.handlers[syms[n]]='ls:'+n
        self.handlers[HOOKS['kugou_obtainlyric'][0]+12]='ls:kugou'  # past the hook's jump: Kugou's search itself
        self.handlers.pop(syms['online_getkeyword_info'],None)  # stock's copy of keyinfo runs, under its (mocked) lock
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')
    def item(self,v):
        a=self.alloc(0x30); self.json[a]=v; self.word(a+16,self.string(v) if isinstance(v,str) else 0); return a  # +16: valuestring
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='r:curl_easy_perform': self.reply=self.replies.pop(0) if self.replies else b'[]'; self.urls.append(self.opts[10002])
        if not name.startswith('ls:'): return super().hook(u,address,size,unused)
        name=name[3:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='fopen': self.fpath=self.text(a); self.written[self.fpath]=b''; ret=0x2000100
        elif name=='fputs': self.written[self.fpath]+=self.raw(a)
        elif name=='cJSON_Parse': ret=self.item(json.loads(self.text(a)))
        elif name=='cJSON_GetArraySize': ret=len(self.json[a])
        elif name=='cJSON_GetArrayItem': ret=self.item(self.json[a][b])
        elif name=='cJSON_GetObjectItem': v=self.json[a]; key=self.text(b); ret=self.item(v[key]) if isinstance(v,dict) and key in v else 0
        self.calls.append((name,a,b,c))
        self.returns(ret,clobber=True)
    def search(self,title,artist,*replies,code=200):
        """replies: one a request, in turn (then [])."""
        key=0xa545f4  # stock's keyinfo: file name, title (+0x200), album (+0x400), artist (+0x500)
        self.u.mem_write(key,bytes(0x600)); self.u.mem_write(key+0x200,title.encode()); self.u.mem_write(key+0x500,artist.encode())
        self.replies,self.code,self.written,self.calls,self.urls=list(replies),code,{},[],[]
        # Each request's 512 KB reply buffer is freed, but the mock's free keeps it: start over, zeroed as calloc's.
        self.u.mem_write(BIG,bytes(self.big-BIG)); self.big=BIG
        return self.call(address=HOOKS['kugou_obtainlyric'][0],args=(self.string(f'{title}/{artist}'),self.string('/tmp/oslyric_7'),0,0),gap=0,count=5_000_000)
m=LyricSearchMachine()
found=json.dumps([{'syncedLyrics':None,'plainLyrics':'x'},{'syncedLyrics':'[00:00.00] Lyrics : A\n[00:12.00]Look at the stars'},
                  {'syncedLyrics':'[00:01.00]Second'}]).encode()
stars='﻿[00:12.00]Look at the stars'.encode()  # the first synced result, its credit cut
LRCLIB='https://lrclib.net/api/search?track_name=%s&artist_name=%s'
busy=b'{"message":"The server is busy, please retry in a moment","name":"ServerOverloaded","statusCode":503}'
def kugou(): return 'kugou' in [c[0] for c in m.calls]
assert m.search('Yellow','Coldplay',found)==1 and m.urls==[LRCLIB%('Yellow','Coldplay')] and m.written=={'/tmp/oslyric_7':stars}
assert m.search('Yellow','Coldplay',b'[]')==1 and len(m.urls)==1 and not m.written and not kugou()  # nothing to fold
# Hataałii: none under its own name, so its folded one, Hataalii; an instrumental counts as none.
plain=LRCLIB%('Rains%20Over%20Dzil','Hataalii')
assert m.search('Rains Over Dził','Hataałii',b'[]',found)==1 and m.urls[1:]==[plain] and m.written=={'/tmp/oslyric_7':stars}
assert m.urls[0]==LRCLIB%('Rains%20Over%20Dzi%C5%82','Hataa%C5%82ii')
for none in (b'[]',json.dumps([{'syncedLyrics':None,'instrumental':True}]).encode()):
    assert m.search('Rains Over Dził','Hataałii',none,none)==1 and len(m.urls)==2 and not m.written and not kugou()
# Busy: asked again, three requests at most, the fold only after an answer; offline: one request.
assert m.search('Yellow','Coldplay',busy,found)==1 and len(m.urls)==2 and m.written=={'/tmp/oslyric_7':stars}
assert m.search('Rains Over Dził','Hataałii',b'[]',busy,b'[]',found)==1 and m.urls[1::2]==[plain,plain] and m.written
assert m.search('Yellow','Coldplay',busy,busy,busy,found)==1 and len(m.urls)==3 and not m.written and not kugou()
assert m.search('Yellow','Coldplay',found,code=0)==1 and len(m.urls)==1 and not m.written and not kugou()
assert m.search('','Coldplay',found)==1 and not m.written and not m.urls; passed()  # no title: no request

# Its accept (onlinematch.c ringnav_lyric_save, at LYRIC_SAVE_CALL): stock's snprintf of cp -rf /tmp/oslyric_N
# "/mnt/mmc/.downpic/<name>" for system() becomes a copy beside the song, through .part, as Get Album Lyrics
# saves, the .downpic copy stock's loader would show first goes, and system() gets ":". The song is the queue's
# whose file name stock turns into name. Not in the queue, a CUE track's, or a card that can't be written there: the
# copy goes to stock's .downpic/<name> instead, never stock's command, whose name the shell would run (GHSA-qp92-mq7c-6r64).
from build import LYRIC_SAVE_CALL
class LyricSaveMachine(AlbumLyricsMachine):
    def __init__(self,files):
        super().__init__(); self.files=dict(files); self.open_files={}; self.unlinked=[]; self.fail_write=False
        for n in ('fopen@GLIBC_2.2','fread@GLIBC_2.0','fwrite@GLIBC_2.0','fclose@GLIBC_2.2','rename@GLIBC_2.0','unlink@GLIBC_2.0'):
            self.handlers[syms[n]]='lv:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('lv:'): return super().hook(u,address,size,unused)
        name=name[3:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='fopen':
            path,mode=self.text(a),self.text(b)
            if mode=='rb' and path in self.files or mode=='wb' and not self.fail_write:
                ret=0x2000000+16*len(self.open_files); self.open_files[ret]=[path,mode,0,b'']
        elif name=='fread':
            f=self.open_files[d]; data=self.files[f[0]][f[2]:f[2]+b*c]; f[2]+=len(data); u.mem_write(a,data); ret=len(data)//b
        elif name=='fwrite': self.open_files[d][3]+=bytes(u.mem_read(a,b*c)); ret=c
        elif name=='fclose':
            path,mode,_,data=self.open_files.pop(a)
            if mode=='wb': self.files[path]=data
        elif name=='rename': self.files[self.text(b)]=self.files.pop(self.text(a))
        elif name=='unlink': self.unlinked.append(self.text(a)); self.files.pop(self.text(a),None)
        self.returns(ret,clobber=True)
    def save(self,name):
        cmd=self.alloc(0x400); fmt=0x7867f0  # stock's cp -rf %s "/mnt/mmc/.downpic/%s"
        self.call(address=payload_syms['ringnav_lyric_save'],args=(cmd,0x400,fmt,self.string('/tmp/oslyric_7')),stack=(self.string(name),),gap=0)
        return self.text(cmd)
assert m.text(0x7867f0)=='cp -rf %s "/mnt/mmc/.downpic/%s"' and m.get(LYRIC_SAVE_CALL)==jal(payload_syms['ringnav_lyric_save'])
LRC=b'\xef\xbb\xbf[00:12.00]Look at the stars\n'*700  # over one 4 KB read
def lyric_save(**rec):
    m=LyricSaveMachine({'/tmp/oslyric_7':LRC,'/mnt/mmc/.downpic/01 Yellow.lrc':b'[00:00.00]Kugou'}); r=m.playing()
    for off,v in (('REC_NAME','01 Yellow.flac'),('REC_PATH','/mnt/mmc/Coldplay/01 Yellow.flac'))+tuple(rec.items()):
        m.word(r+O[off],m.string(v) if isinstance(v,str) else v)
    return m
m=lyric_save(); assert m.save('01 Yellow.lrc')==':' and m.files['/mnt/mmc/Coldplay/01 Yellow.lrc']==LRC
assert m.unlinked==['/mnt/mmc/.downpic/01 Yellow.lrc'] and '/mnt/mmc/.downpic/01 Yellow.lrc' not in m.files
assert not [p for p in m.files if p.endswith('.part')]; passed()
m=lyric_save(); assert m.save('02 Other.lrc')==':' and m.files['/mnt/mmc/.downpic/02 Other.lrc']==LRC and not m.unlinked; passed()  # not in the queue
m=lyric_save(); assert m.save('x$(touch p)`id`".lrc')==':' and m.files['/mnt/mmc/.downpic/x$(touch p)`id`".lrc']==LRC; passed()
m=lyric_save(); assert m.save('../../x.lrc')==':' and set(m.files)=={'/tmp/oslyric_7','/mnt/mmc/.downpic/01 Yellow.lrc'}; passed()  # a '/' leaves .downpic
m=lyric_save(REC_CUE_END=90); assert m.save('01 Yellow.lrc')==':' and '/mnt/mmc/Coldplay/01 Yellow.lrc' not in m.files
assert m.files['/mnt/mmc/.downpic/01 Yellow.lrc']==LRC; passed()
m=lyric_save(); m.fail_write=True; assert m.save('01 Yellow.lrc')==':' and m.files['/mnt/mmc/.downpic/01 Yellow.lrc']==b'[00:00.00]Kugou'; passed()  # can't write the card
# No lyrics chosen there (ringnav_lyric_drop, at LYRIC_DROP_CALL): stock's snprintf of rm -rf "/mnt/mmc/.downpic/<name>"
# for the same system() becomes an unlink of that copy, and system() gets ":" again, whatever the name holds.
from build import LYRIC_DROP_CALL
def lyric_drop(m,name):
    cmd=m.alloc(0x400); m.call(address=payload_syms['ringnav_lyric_drop'],args=(cmd,0x3ff,0x786814,m.string(name)),gap=0)
    return m.text(cmd)
assert m.text(0x786814)=='rm -rf "/mnt/mmc/.downpic/%s"' and m.get(LYRIC_DROP_CALL)==jal(payload_syms['ringnav_lyric_drop'])
m=lyric_save(); assert lyric_drop(m,'01 Yellow.lrc')==':' and m.unlinked==['/mnt/mmc/.downpic/01 Yellow.lrc']; passed()
m=lyric_save(); assert lyric_drop(m,'x$(touch p)`id`".lrc')==':' and m.unlinked==['/mnt/mmc/.downpic/x$(touch p)`id`".lrc']; passed()
m=lyric_save(); assert lyric_drop(m,'../../x.lrc')==':' and lyric_drop(m,'y'*0x201)==':' and not m.unlinked  # a '/', or too long
assert lyric_drop(m,'y'*0x200)==':' and m.unlinked==['/mnt/mmc/.downpic/'+'y'*0x200]; passed()  # the longest that fits
# Now Playing's cover thread showing that pick (ringnav_cover_copy, at COVER_PICK_CALL): stock's snprintf of
# cp -f "/mnt/mmc/.downpic/<file name>.jpg" '/tmp/externpic.jpg' for system() becomes the copy, through .part.
from build import COVER_PICK_CALL
def cover_copy(m,src):
    cmd=m.alloc(0x600); m.call(address=payload_syms['ringnav_cover_copy'],args=(cmd,0x600,0x782974,m.string(src)),stack=(m.string('/tmp/externpic.jpg'),),gap=0)
    return m.text(cmd)
assert m.text(0x782974)=='cp -f "%s" \'%s\'' and m.get(COVER_PICK_CALL)==jal(payload_syms['ringnav_cover_copy'])
src='/mnt/mmc/.downpic/x$(touch p)`id`.flac.jpg'
m=LyricSaveMachine({src:LRC}); assert cover_copy(m,src)==':' and m.files=={src:LRC,'/tmp/externpic.jpg':LRC}; passed()
m=LyricSaveMachine({}); assert cover_copy(m,src)==':' and not m.files; passed()  # gone since stock's access()
# A folder's delete (folder_delete.c ringnav_folder_rm, at FOLDER_RM_CALL): remove_file's rm -rf "<folder>" puts the
# path in single quotes instead, so the shell expands nothing in it; test/fw runs the command through /bin/sh.
from build import FOLDER_RM_CALL
assert m.text(0x782ff8)=='rm -rf "%s"' and m.get(FOLDER_RM_CALL)==jal(payload_syms['ringnav_folder_rm'])
cmd=m.alloc(0x61b); m.call(address=payload_syms['ringnav_folder_rm'],args=(cmd,0x61b,0x782ff8,m.string("/mnt/mmc/$NOT/Guns N' Roses")),gap=0)
assert m.text(cmd)=="rm -rf '/mnt/mmc/$NOT/Guns N'\\'' Roses'"; passed()

# Spotify's browser (patch/spotify.c, docs/librespot.md's Q2SPOT/1): the row opens spotbrowse_page,
# each level a request on the fork's --browse-socket, sent and read on a pthread.
BROWSE='/tmp/q2-librespot.browse.sock'
class BrowseMachine(RadioMachine):
    """RadioMachine's helpers, librespot on the card and a fake backend on its browse socket: serve(op,
    args) answers (status, message, cursor, rows), or raw bytes; no serve, no socket. A subclass names
    another app: its socket, its Streaming row and the page it opens."""
    browse,bin,page_name=BROWSE,BIN,'spotbrowse_page'
    def __init__(self,files=None):
        super().__init__({self.bin:bytearray(),**(files or {})})
        for n in ('socket@GLIBC_2.0','connect@GLIBC_2.0','send@GLIBC_2.0','select@GLIBC_2.0','recv@GLIBC_2.0','fcntl@GLIBC_2.28'):
            self.handlers[syms[n]]='x:'+n
        shut=payload_syms['spot_shut']; self.handlers[shut]='x:spot_shut'; self.u.hook_add(UC_HOOK_CODE,self.hook,begin=shut,end=shut)  # fast(): payload hooks are per address
        self.serve=None; self.requests=[]; self.wire=[]; self.out=b''; self.pending=b''; self.stalled=False
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='widget_set_prop_int' and self.text(u.reg_read(REGS[1]))=='virtual_h':
            self.word(u.reg_read(REGS[0])+O['VIEW_CONTENT_H'],u.reg_read(REGS[2]))
        if not name.startswith('x:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='socket':
            assert a==1 and b in (1,2); ret=9 if b==2 else 7
            if b==2: self.out=b''; self.pending=b''
        elif name=='connect' and self.text(b+2)=='/tmp/q2adb-ui': ret=-1  # adbd's UI socket (a dev build's usb_ui.c auth_poll)
        elif name=='connect' and a!=9: assert self.text(b+2)==SOCK; ret=0 if self.alive else -1  # spot_alive's
        elif name=='connect': assert self.text(b+2)==self.browse; ret=0 if self.serve else -1
        elif name=='fcntl': assert (a,b,c)==(9,4,0x80)  # nonblocking before connect
        elif name=='send':
            if a==9: self.out+=bytes(u.mem_read(b,c))
            else: self.sent.append((a,bytes(u.mem_read(b,c)),c,d))  # QueueMachine's
            ret=c
        elif name=='spot_shut':  # the request is whole: answered
            self.wire.append(self.out); head,line,end=self.out.decode().split('\n'); assert head=='Q2SPOT/1' and end==''
            rid,op,*args=line.split('\t'); self.requests.append((op,*args))
            got=self.serve(op,args)
            if isinstance(got,bytes): self.pending=got.replace(b'{id}',rid.encode())
            else:
                status,msg,cursor,rows=got
                self.pending=f'Q2SPOT/1\n{rid}\t{status}\t{msg}\t{cursor}\n'.encode()+b''.join(('\t'.join(r)+'\n').encode() for r in rows)
        elif name=='select':
            assert a==10 and bool(b)!=bool(c)
            if self.stalled: self.now+=100; ret=0
            else: ret=1
        elif name=='recv': chunk=self.pending[:min(c,1000)]; self.pending=self.pending[len(chunk):]; u.mem_write(b,chunk); ret=len(chunk)
        self.calls.append((name,a,b,c))
        self.returns(ret,clobber=True)
    def add_on_row(self):
        """Its Streaming row, as RadioMachine.stream: Spotify's."""
        return stream_rows(self)['Spotify']
    def open(self):
        if not getattr(self,'button',0): self.button=self.add_on_row()
        self.click(self.button); self.page=self.top; assert self.nodes[self.page]['name']==self.page_name
    def asked(self):
        """The request the worker is about to send (spotify.c's wk: buf at 32, len at 28), split."""
        wk=payload_syms['wk']; head,line,_=bytes(self.u.mem_read(self.get(wk+32),self.get(wk+28))).decode().split('\n')
        assert head=='Q2SPOT/1'; return tuple(line.split('\t')[1:])
    def answer(self,*reply):
        """The request in flight answered with reply, and the page's tick."""
        self.serve=(lambda op,args: reply[0]) if len(reply)==1 else (lambda op,args: reply); self.worker()
        if self.page_name == 'spotbrowse_page' and self.get(payload_syms['wk']+32) and self.asked() == ('caps',):
            self.serve=lambda op,args: ('ok','','',[('op',name,'','','','') for name in getattr(self,'caps',('download','downloadstatus','downloaded','playdownload'))])
            self.worker()
    def pick(self,i):
        self.nodes[self.find('scroll_view')]['_ringnav_index']=i; self.row(i)
OK=('ok','','')
def chevron(m,i):
    """Row i ends in stock's list_into (page_row_detail_into), its labels ending before the glyph (x 322)."""
    *labels,last=m.nodes[m.rows()[i]]['children']
    return m.nodes[last].get('image')=='list_into' and all(m.get(l+O['W_X'])+m.get(l+O['W_W'])<=316 for l in labels)
def switch(m,i): return m.nodes[m.nodes[m.rows()[i]]['children'][-1]].get('image')
ME=OK+([('account','spotify:user:alice','Alice','Premium','','')],)
def track(uri,title,ctx='',pos=''): return ('track',uri,title,'An Artist',ctx,pos)
# The row opens the browser and asks for the account, written, shut for writing and read on the worker;
# signed in, the menu: Now Playing, Queue and Playback settings only with a track.
m=BrowseMachine(); m.open(); assert m.labels()==['Loading…'] and m.systems==['/bin/sh /mnt/mmc/.spotify/run']
m.answer(*ME); assert m.wire==[b'Q2SPOT/1\n1\taccount\n',b'Q2SPOT/1\n2\tcaps\n']
MENU=['Spotify','Liked Songs','Playlists','Albums','Artists','Search','Account','Downloaded Tracks','Downloads']
assert m.labels()==MENU
m.state('playing'); m.poll(); m.advance(250)
assert m.labels()==['Spotify','Now Playing','Liked Songs','Playlists','Albums','Artists','Search','Queue','Playback Settings','Account','Downloaded Tracks','Downloads']
m.row(0); assert m.nodes[m.top]['name']=='spotify_page'; m.destroy(m.top); m.top=m.page; m.state('none',track=''); m.poll(); m.advance(250); assert m.labels()==MENU; passed()
m.row(7)  # an older add-on: its message-only Download Status stays reachable
assert m.labels()==['Downloads','Downloaded Tracks','','Download Status',"The latest download's progress",'Update Spotify on the card','for saved playlists and download controls']
assert [chevron(m,i) for i in range(3)]==[True,False,False]
m.row(1); assert m.asked()==('downloadstatus',); m.answer('ok','Mix B: 3 of 10 saved','',[])
assert m.toasts[-1][3]=='Mix B: 3 of 10 saved' and m.labels()[0]=='Downloads'
m.back(); assert m.labels()==MENU; passed()
# Downloaded tracks stay reachable while the service cannot answer catalogue requests, and a row
# uses the cache-only play operation.
m.row(6); assert m.asked()==('downloaded',)
m.answer(*OK,[track('spotify:track:cached','Cached')])
m.row(0); assert m.asked()==('playdownload','spotify:track:cached')
m.answer(*OK,[]); assert m.nodes[m.top]['name']=='spotify_page'; m.destroy(m.top); m.top=m.page; m.back(); passed()
# Loading, an error with Retry (offline, throttled, error, or no answer), one without, and empty.
m.row(0); assert m.labels()==MENU and m.asked()==('liked',)  # under the loading overlay: the menu stays
m.answer('offline',"Spotify can't be reached",'',[]); assert m.labels()==["Spotify can't be reached",'Retry']
m.row(0); assert m.labels()==["Spotify can't be reached",'Retry'] and m.asked()==('liked',)  # Retry under the overlay
m.answer('premium','Spotify Premium is needed','',[]); assert m.labels()==['Spotify Premium is needed']
m.back(); m.row(0); m.answer(*OK,[]); assert m.labels()==['Nothing here']
# A short or empty page with a cursor (the backend's paging cap) still offers More….
m.back(); m.row(0); m.answer('ok','','c%251',[]); assert m.labels()==['Liked Songs','More…','']
m.row(0); assert m.asked()==('liked','c%251'); m.answer(*OK,[track('spotify:track:9','Nine','spotify:user:alice:collection','50')])
assert m.labels()==['Liked Songs','Nine','An Artist']; passed()
# A reply without the request's id (a late one, or id 0 from a backend that couldn't read it) is
# dropped: its rows never show.
m.back(); m.back(); m.row(0); m.answer(b'Q2SPOT/1\n0\tok\t\t\ntrack\tspotify:track:1\tOne\tA\t\t\n')
assert m.labels()==["Spotify didn't answer",'Retry']
# Malformed or oversized ones too.
m.row(0); m.answer(b'Q2SPOT/1\n{id}\tok\t%zz\t\n'); assert m.labels()==["Spotify didn't answer",'Retry']
m.row(0); m.answer(b'x'*(65537)); assert m.labels()==["Spotify didn't answer",'Retry']; m.back(); passed()
# Playlists, a playlist's tracks by its URI exactly as received, More… by the cursor, and Back
# through them to the row each was left on. A track plays in its context at its position (and its
# URI), and once it plays Now Playing opens; a failure is toasted. Alone (empty context), by its URI.
m.row(1); assert m.asked()==('playlists',)
m.answer('ok','','next%09page',[('playlist','spotify:playlist:a','Mix A','alice','',''),('playlist','spotify:playlist:b%25','Mix B','bob','','')])
assert m.labels()==['Playlists','Mix A','alice','Mix B','bob','More…','']
m.pick(1); assert m.asked()==('playlist','spotify:playlist:b%25')
m.answer(*OK,[track('spotify:track:1','One','spotify:playlist:b%25','0'),track('spotify:track:1','One','spotify:playlist:b%25','1'),track('spotify:track:3','Three')])
assert m.labels()==['Mix B','One','An Artist','One','An Artist','Three','An Artist','Download Playlist','For offline listening']
m.pick(3); assert m.asked()==('download','spotify:playlist:b%25')
m.answer('ok','Playlist download started','',[])
assert m.toasts[-1][3]=='Playlist download started' and m.top==m.page
m.pick(3); m.answer('offline','Connect to Wi-Fi to download playlists','',[])
assert m.toasts[-1][3]=='Connect to Wi-Fi to download playlists' and m.labels()[0]=='Mix B'
m.pick(1); assert m.asked()==('play','spotify:playlist:b%25','1','spotify:track:1')
m.answer('unavailable','This track is unavailable','',[]); assert m.toasts[-1][3]=='This track is unavailable' and m.top==m.page
m.pick(2); assert m.asked()==('play','spotify:track:3'); n=len(m.toasts); m.answer(*OK,[])
assert len(m.toasts)==n and m.nodes[m.top]['name']=='spotify_page'; m.destroy(m.top); m.top=m.page
m.back(); assert m.labels()[:2]==['Playlists','Mix A'] and m.nodes[m.find('scroll_view')]['_ringnav_index']==1
m.pick(2); assert m.asked()==('playlists','next%09page'); m.answer(*OK,[('playlist','spotify:playlist:c','Mix C','carol','','')])
m.back(); assert m.nodes[m.find('scroll_view')]['_ringnav_index']==2; passed()
# Return while a list loads under the overlay cancels it: the fetch is dropped, the list stays.
m.pick(0); assert m.labels()[:2]==['Playlists','Mix A']; t=len(m.threads); m.back(); assert m.labels()[:2]==['Playlists','Mix A']
assert len(m.threads)==t and ('pthread_join' in [c[0] for c in m.calls]); passed()
# Artists open an artist: top tracks, then albums; an album opens its tracks. The history keeps the
# top SPOT_KEEP levels' rows; Back to an older one fetches it again.
m.back(); m.row(3); m.answer(*OK,[('artist','spotify:artist:x','X','','','')]); m.pick(0); assert m.asked()==('artist','spotify:artist:x')
m.answer(*OK,[track('spotify:track:t','Top'),('album','spotify:album:y','Y','X','','')]); assert m.labels()==['X','Top','An Artist','Y','X']
m.pick(1); assert m.asked()==('album','spotify:album:y'); m.answer('ok','','p2',[track('spotify:track:z','Z','spotify:album:y','0')])
m.pick(0); assert m.asked()==('play','spotify:album:y','0','spotify:track:z'); m.answer(*OK,[]); m.destroy(m.top); m.top=m.page
m.pick(1); assert m.asked()==('album','spotify:album:y','p2'); m.answer(*OK,[])
asked=len(m.threads); m.back(); m.back(); assert len(m.threads)==asked and m.labels()[:2]==['X','Top']
m.back(); assert m.labels()[:2]==['X','Top'] and m.asked()==('artists',)  # asked again under the overlay, the page stays
m.answer(*OK,[('artist','spotify:artist:x','X','','','')]); assert m.labels()==['Artists','X',''] and m.nodes[m.find('scroll_view')]['_ringnav_index']==0
m.back(); assert m.labels()==MENU; passed()
# No saved login: the pairing prompt, asking again every 2 s while it is open, so a pairing from the
# phone opens the menu in place.
m=BrowseMachine(); m.open(); m.answer('login','Open Spotify on your phone','',[])
PAIR=['Spotify','Open Spotify on your phone and choose Q2','Both on the same Wi-Fi']
assert m.labels()==PAIR; t=len(m.threads); m.advance(1500); assert len(m.threads)==t
m.advance(750); assert len(m.threads)==t+1 and m.asked()==('account',); m.answer('login','','',[]); assert m.labels()==PAIR
m.advance(2000); m.answer(*ME); assert m.labels()==MENU; passed()
# An old add-on: librespot runs (its state file) but no browse socket after SPOT_OLD_MS. Now Playing
# still works, and the page asks for the update; until then it waits. No state file either: not running.
m=BrowseMachine({STATE:bytearray(b'state=none\n')}); m.open(); m.worker(); assert m.labels()==['Loading…']
m.advance(500); m.worker(); assert m.labels()==['Loading…']
m.advance(2500); m.worker(); assert m.labels()==['Spotify','Now Playing','','Update Spotify on the card','to browse on the Q2']
m.row(0); assert m.nodes[m.top]['name']=='spotify_page'; m.destroy(m.top); m.top=m.page; passed()
m=BrowseMachine(); m.open(); m.advance(3000); m.worker(); assert m.labels()==["Spotify isn't running",'Retry']
m.row(0); m.answer(*ME); assert m.labels()==MENU; passed()
# Search: the T9 box over the title, run on its OK only. Empty, left or Returned from, the menu as it was; else
# the term's categories, each searched by its name and the term, escaped.
def search(text,event='EVT_VALUE_CHANGED'):
    m.row(4); edit=m.find('edit'); assert m.nodes[edit]['focused']==1 and m.nodes[edit]['tips']=='Search Spotify'
    y=m.get(edit+O['W_Y'])-OLED_G.bar  # the full-bleed page's rows from the status row's foot (bar_foot)
    assert 0<=y and y+m.get(edit+O['W_H'])<=30 and not m.find('scroll_view')  # above the keyboard, alone
    m.nodes[edit]['text']=text; f,ctx=m.handler(edit,O[event])
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0; m.advance(0,clear=False)
asked=len(m.threads)
for text,event in (('','EVT_VALUE_CHANGED'),('','EVT_IM_ACTION'),('abc','EVT_BLUR')): search(text,event); assert m.labels()==MENU and not m.find('edit')
m.row(4); edit=m.find('edit'); m.nodes[edit]['text']='abc'  # Return: emptied, no search; blurred, its keyboard shut first
m.back(); assert m.nodes[edit]['focused']==0 and m.nodes[edit]['text']==''
f,ctx=m.handler(edit,O['EVT_BLUR']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
m.advance(0,clear=False); assert m.labels()==MENU and not m.find('edit')
search('50%\toff'); assert len(m.threads)==asked and m.labels()==['50%\toff','Tracks','Albums','Artists','Playlists']
m.row(1); assert m.asked()==('search','albums','50%25%09off'); m.answer(*OK,[('album','spotify:album:q','Q','R','','')])
assert m.labels()==['Albums','Q','R']; m.back(); m.back(); assert m.labels()==MENU; passed()
# Holding Play/Pause on a track row adds it to the queue, on a playlist row downloads it, its reply
# toasted; on another row, or another page, the hold is stock's.
m.row(0); m.answer(*OK,[track('spotify:track:1','One','c','0'),('album','spotify:album:a','A','B','',''),
                        ('playlist','spotify:playlist:p','P','Q','','')])
m.top=m.page; m.status=m.alloc(0x200); m.nodes[m.find('scroll_view')]['_ringnav_index']=0; m.press(77)
toasts=len(m.toasts)
assert m.hold()==11 and m.asked()==('enqueue','spotify:track:1'); m.answer(*OK,[]); assert len(m.toasts)==toasts
m.press(78); assert m.hold()==11; m.answer('unavailable','Nothing is playing on Q2','',[]); assert m.toasts[-1][3]=='Nothing is playing on Q2'
m.nodes[m.find('scroll_view')]['_ringnav_index']=2; m.press(80)
assert m.hold()==11 and m.asked()==('download','spotify:playlist:p'); m.answer('ok','Downloading P','',[]); assert m.toasts[-1][3]=='Downloading P'
m.nodes[m.find('scroll_view')]['_ringnav_index']=1; m.press(79); asked=len(m.threads); m.hold(); assert len(m.threads)==asked; m.back(); passed()
# Playing: Queue's rows play by their place and URI, then Now Playing. Playback settings: shuffle and
# repeat as they are, a press setting the next and reading them again; never sent again on failure.
m.state('playing'); m.poll(); m.advance(250); m.row(6); assert m.asked()==('queue',)
m.answer(*OK,[track('spotify:track:q','Q',pos='3')]); m.row(0); assert m.asked()==('skip','3','spotify:track:q')
m.answer(*OK,[]); assert m.nodes[m.top]['name']=='spotify_page'; m.destroy(m.top); m.top=m.page; m.back()
m.row(7); m.answer(*OK,[('setting','shuffle','off','','',''),('setting','repeat','context','','','')])
assert m.labels()==['Playback Settings','Shuffle','','Repeat','All'] and switch(m,0)=='switch_off' and switch(m,1)!='switch_off'
m.row(0); assert m.asked()==('shuffle','on'); m.answer(*OK,[]); assert m.labels()==['Playback Settings','Shuffle','','Repeat','All'] and m.asked()==('settings',)  # under the overlay
freed=m.freed
m.answer(*OK,[('setting','shuffle','on','','',''),('setting','repeat','track','','','')]); assert m.labels()==['Playback Settings','Shuffle','','Repeat','One'] and switch(m,0)=='switch_on' and m.freed==freed+1
m.row(1); assert m.asked()==('repeat','off'); t=len(m.threads); m.answer('offline',"Spotify didn't answer in time",'',[]); m.advance(1000)
assert m.toasts[-1][3]=="Spotify didn't answer in time" and len(m.threads)==t and m.labels()[1]=='Shuffle'; m.back(); passed()
# Account: the name, Premium, and Sign out after a confirmation; then the pairing prompt.
m.row(8); assert m.asked()==('account',); m.answer(*ME); assert m.labels()==['Account','Alice','Premium','Sign Out','']
# Sign Out asks on the confirmation card (feedback.c confirm); its answer (spotify.c signout_answer) runs once it has closed
def card(m,answer,row):
    if m.nodes[m.top].get('name')=='sortselect_dialog':  # the card closes first, as on the unit: the page is on top again
        if m.top in m.stack: m.stack.remove(m.top)
        m.top=m.page
    m.call(address=payload_syms[answer],args=(0,row,0,0),gap=0,clear=False)
m.row(1); card(m,'signout_answer',0); assert m.labels()[:2]==['Account','Alice']
t=len(m.threads); m.row(1); card(m,'signout_answer',1); assert m.asked()==('signout',) and len(m.threads)==t+1
m.answer(*OK,[]); assert m.labels()==PAIR
# librespot exits on signout and its launcher restarts it: meanwhile no socket is no old add-on.
m.serve=None; m.advance(2000); m.worker(); assert m.labels()==PAIR; m.advance(5000); m.worker(); assert m.labels()==PAIR
# A login lost while browsing goes to pairing too.
m.advance(2000); m.answer(*ME); m.row(1); m.answer('login','','',[]); assert m.labels()==PAIR; passed()
# Browsing keeps librespot: paused past SPOT_IDLE_MS with the browser open, it runs on; once the
# browser is shut the wait starts, and the row starts it again, waiting for its socket.
m.state('playing'); m.poll(); m.state('paused'); m.poll(60000); assert len(m.systems)==1
m.destroy(m.page); m.poll(29500); assert len(m.systems)==1; m.poll(500); assert 'killall -9 librespot aplay' in m.systems[-1]
m.serve=None; m.open(); assert m.systems[-1]=='/bin/sh /mnt/mmc/.spotify/run'; m.worker(); assert m.labels()==['Loading…']
m.advance(500); m.answer(*ME); assert m.labels()==MENU; passed()
# Left open with the screen off, the browser holds nothing: SPOT_IDLE_MS on, it shuts and librespot stops.
m.state('playing'); m.poll(); m.state('paused'); m.poll(); m.byte(syms['g_backlight_status'],0); kills=len(m.systems)
m.poll(29500); assert len(m.systems)==kills
names=m.poll(500); assert 'window_close' in names and 'killall -9 librespot aplay' in m.systems[-1]
m.byte(syms['g_backlight_status'],1); m.destroy(m.page); passed()

# Downloads outlive the browser without taking the audio output or stopping local playback.
m=BrowseMachine(); m.open(); m.answer(*ME); m.state('playing'); m.poll()
m.state('paused'); m.poll(); m.destroy(m.page)
m.files['/tmp/q2-librespot.download']=bytearray(b'downloading=1\n')
m.play=2; names=m.poll(60000)
assert len(m.systems)==1 and 'reset_poweroptions_timer' in names and 'player_stop' not in names
assert not m.call(address=payload_syms['spot_status'],gap=0)
m.files['/tmp/q2-librespot.download']=bytearray(b'downloading=0\n')
m.poll(500); m.poll(30000); assert 'killall -9 librespot aplay' in m.systems[-1]; passed()
# A crashed librespot's files still say playing and downloading: once its socket refuses, no hold,
# and it idles out; a new state (the restart) or a socket that answers again trusts them again.
m=BrowseMachine(); m.open(); m.answer(*ME); m.state('playing'); m.poll(); m.destroy(m.page)
m.files['/tmp/q2-librespot.download']=bytearray(b'downloading=1\n')
m.alive=False; names=m.poll(10000); assert 'reset_poweroptions_timer' not in names
names=m.poll(); assert 'reset_poweroptions_timer' not in names
m.state('playing',position=1); names=m.poll(); assert 'reset_poweroptions_timer' in names
m.poll(10000); m.alive=True; names=m.poll(10000); assert 'reset_poweroptions_timer' in names
m.alive=False
m.alive=False; m.poll(10000); m.poll(30000); assert 'killall -9 librespot aplay' in m.systems[-1]; passed()

# Back reveals the saved More row, and paint keeps it selected: the list scrolled to its end, the 51st
# 52 px row (OLED's page.rows.detail_height) ending on the view's last whole row.
m=BrowseMachine(); m.open(); m.answer(*ME); m.row(0)
m.answer('ok','','next',[track('spotify:track:'+str(i),str(i)) for i in range(50)])
m.pick(50); m.answer(*OK,[]); m.back(); view=m.find('scroll_view')
vh=m.get(view+O['W_H']); assert m.nodes[view]['_ringnav_index']==50 and m.get(view+O['SCROLL_Y'])==51*52-vh//52*52  # OLED's whole rows: the 51st's foot on the view's last whole row (lists.c reveal)
m.paint(view); assert m.nodes[view]['_ringnav_index']==50; passed()
# More beyond the history limit keeps the operation, context and title while recycling a level.
m=BrowseMachine(); m.open(); m.answer(*ME); m.row(2)
m.answer(*OK,[('album','spotify:album:a','Album A','Artist','','')]); m.row(0)
for i in range(12):
    assert m.asked()==(('album','spotify:album:a') if not i else ('album','spotify:album:a',str(i)))
    m.answer('ok','',str(i+1),[track('spotify:track:1','One','spotify:album:a',str(i*50))])
    assert m.labels()[0]=='Album A'
    m.pick(1)
m.answer(*OK,[]); m.back(); assert m.labels()[0]=='Album A'; passed()
# A stalled write times out without sending the operation; Back also abandons a pending play.
m=BrowseMachine(); m.open(); m.stalled=True; start=m.now; m.answer(*ME)
assert m.now-start>=15000 and not m.wire and m.labels()==["Spotify didn't answer",'Retry']
m.stalled=False; m.row(0); m.answer(*ME); m.row(0); m.answer(*OK,[track('spotify:track:1','One')])
m.row(0); assert m.asked()==('play','spotify:track:1'); m.back(); assert m.labels()==MENU
assert m.get(payload_syms['wk']+32)==0; m.advance(1000); assert m.top==m.page; passed()

# A successful play waits for the requested track, including offline playback. Missing metadata
# and another track's position updates cannot flash pairing or reveal the previous track.
for size in (1, 2, 3, 4, 255, 256):
    m=SpotMachine({}); out=m.alloc(300); title='A汉字é'*80
    m.call(address=payload_syms['field'],args=(m.string('\ntitle='+title+'\n'),m.string('title'),out,size),gap=0)
    assert m.text(out)==title.encode()[:size-1].decode('utf-8',errors='ignore')
passed()
for op in ('liked','downloaded'):
    m=BrowseMachine(); m.open(); m.answer(*ME)
    m.state('paused',track='old'); m.poll(); m.advance(250)
    m.row(1 if op=='liked' else 9)
    m.answer(*OK,[track('spotify:track:new','New')]); m.row(0); m.answer(*OK,[])
    np=m.top; info,msg=m.nodes[np]['children']
    assert texts(msg)==['Loading…','Starting playback'] and not m.nodes[info]['visible']
    m.state('playing',track='old',position=2000); m.poll(); m.advance(250)
    assert m.nodes[msg]['visible'] and texts(msg)[0]=='Loading…'
    m.state('none',track=''); m.poll(); m.advance(250)
    assert texts(msg)[0]=='Loading…'
    m.state('playing',track='new',title='相同的标题 Long title '*6,artist='Long artist 艺术家 '*6,album='Long album 专辑 '*6)
    m.poll(); m.advance(250)
    assert m.nodes[info]['visible'] and not m.nodes[msg]['visible']
    assert texts(info)[1:4]==['相同的标题 Long title '*6,'Long artist 艺术家 '*6,'Long album 专辑 '*6]
    # Same title, different artist and album: all three fields have their own change detection.
    m.state('paused',track='next',title='相同的标题 Long title '*6,artist='Other 艺术家',album='Other 专辑')
    m.poll(); m.advance(250); assert texts(info)[2:4]==['Other 艺术家','Other 专辑']
    fields=[w for w in m.nodes if under(w,info) and m.nodes[w].get('text') in texts(info)[1:4]]
    assert all(m.nodes[w]['xoffset']==0 for w in fields)
    m.state('stopped',track=''); m.poll(); m.advance(250)
    assert texts(msg)==['Nothing playing','Choose music in Spotify']
    m.state('none',track=''); m.poll(); m.advance(250)
    assert 'Open Spotify on your phone' not in texts(msg)
    passed()

# Metadata can also precede the acknowledgement: do not wait for a second state change.
m=BrowseMachine(); m.open(); m.answer(*ME); m.row(0)
m.answer(*OK,[track('spotify:track:early','Early')]); m.row(0)
m.state('playing',track='early',title='Early'); m.poll(); m.answer(*OK,[])
info,msg=m.nodes[m.top]['children']; assert m.nodes[info]['visible'] and texts(info)[1]=='Early'
passed()
# A relinked track (another id than asked for) is the play's too.
m=BrowseMachine(); m.open(); m.answer(*ME); m.row(0)
m.answer(*OK,[track('spotify:track:asked','Asked')]); m.row(0); m.answer(*OK,[])
m.state('playing',track='relinked',title='Asked'); m.poll(); m.advance(250)
info,msg=m.nodes[m.top]['children']; assert m.nodes[info]['visible'] and texts(info)[1]=='Asked'
passed()

# The loading wait is bounded, does not repeat play, and accepts late metadata after a timeout.
m=BrowseMachine(); m.open(); m.answer(*ME); m.row(0)
m.answer(*OK,[track('spotify:track:late','Late')]); m.row(0); m.answer(*OK,[])
np=m.top; info,msg=m.nodes[np]['children']; requests=len(m.requests)
m.advance(15250)
assert texts(msg)==["Playback didn't start",'Go back and try again'] and len(m.requests)==requests
m.state('playing',track='late'); m.poll(); m.advance(250); assert m.nodes[info]['visible']
m.destroy(np); m.top=m.page; m.back(); m.row(8); m.answer(*ME); m.row(1); card(m,'signout_answer',1); m.answer(*OK,[])  # Sign Out's card
m.call(address=payload_syms['spot_np'],args=(0,0,0,0),gap=0,count=5_000_000)
info,msg=m.nodes[m.top]['children']; assert texts(msg)==['Open Spotify on your phone','and choose Q2']
passed()

# A refused key (librespot-org/librespot#1649): librespot logs it and skips on through the list, which
# no pause stops. At the next poll the page says why and librespot is killed (run starts it again),
# once, without sending play again; a line from before the play is not this play's.
LOG='/mnt/mmc/.spotify/spot.log'; REFUSED=b'ERROR librespot_core::audio_key] error audio key 0 1\n'
m=BrowseMachine({LOG:bytearray(b'[old] '+REFUSED)}); m.open(); m.answer(*ME); m.row(0)
m.answer(*OK,[track('spotify:track:shut','Shut')]); m.row(0); m.answer(*OK,[])
np=m.top; info,msg=m.nodes[np]['children']; requests=len(m.requests); m.systems=[]
m.poll(); m.advance(250); assert texts(msg)==['Loading…','Starting playback'] and not m.systems
m.files[LOG]+=b'[new] INFO  librespot_playback::player] Loading <Shut>\n[new] '+REFUSED
m.poll(); m.advance(250)
assert texts(msg)==['Spotify refused this account',"Its tracks can't play on the Q2"]
assert m.systems==['killall -9 librespot aplay'] and len(m.requests)==requests
m.files[LOG]+=b'[new] '+REFUSED; m.poll(); m.advance(15250)
assert m.systems==['killall -9 librespot aplay'] and texts(msg)[0]=='Spotify refused this account'
m.state('playing',track='shut'); m.poll(); m.advance(250); assert m.nodes[info]['visible']
passed()
# The log line cut between two polls is still found; a log without one times out as before.
m=BrowseMachine({LOG:bytearray()}); m.open(); m.answer(*ME); m.row(0)
m.answer(*OK,[track('spotify:track:cut','Cut')]); m.row(0); m.answer(*OK,[]); msg=m.nodes[m.top]['children'][1]
m.systems=[]; m.files[LOG]+=b'x'*5000+REFUSED[:40]; m.poll(); assert not m.systems
m.files[LOG]+=REFUSED[40:]; m.poll(); m.advance(250); assert m.systems==['killall -9 librespot aplay']
m=BrowseMachine({LOG:bytearray()}); m.open(); m.answer(*ME); m.row(0)
m.answer(*OK,[track('spotify:track:slow','Slow')]); m.row(0); m.answer(*OK,[]); msg=m.nodes[m.top]['children'][1]
m.systems=[]; m.files[LOG]+=b'[new] WARN  something else\n'; m.poll(); m.advance(15250)
assert texts(msg)==["Playback didn't start",'Go back and try again'] and not m.systems
passed()

def run(m,name,*args): return m.call(address=payload_syms[name],args=(*args,0,0,0,0)[:4],gap=0,clear=False)

# Saved-collection capabilities replace the legacy flat Downloads action with a persistent hub.
m=BrowseMachine(); m.caps=('collections','collection','playcollection','downloadliked','jobs','job','remove','download')
m.open(); m.answer(*ME)
assert m.labels()==['Spotify','Liked Songs','Playlists','Albums','Artists','Search','Account','Downloads']
m.row(0); m.answer(*OK,[])
assert m.labels()==['Liked Songs','Download Liked Songs','For offline listening']
m.row(0); assert m.asked()==('downloadliked',); m.answer('ok','Queued','',[]); m.back()
m.row(6); assert m.labels()==['Downloads','Downloaded Playlists','','Downloaded Tracks','','Download Progress','','Storage Management','']
assert all(chevron(m,i) for i in range(4))  # each opens a page
m.row(0); assert m.asked()==('collections',)
m.answer(*OK,[('collection','liked','Liked Songs','2/3 · partial','','')]); m.row(0)
assert m.asked()==('collection','liked')
m.answer('ok','One unavailable','next',[track('spotify:track:one','One','liked','49'),('missing','local:missing','Missing','Unavailable','liked','50'),track('spotify:track:one','One','liked','51')])
m.row(2); assert m.asked()==('playcollection','liked','51')
m.answer(*OK,[]); m.destroy(m.top); m.top=m.page
m.row(1); assert m.toasts[-1][3]=='Unavailable'
m.row(3); assert m.asked()==('collection','liked','next'); m.answer(*OK,[])
m.back(); m.row(4); assert m.labels()==['Download Controls','Pause','Resume','Cancel Download','Retry Failed','Update Download']
assert [(m.get(r+O['W_Y']),m.get(r+O['W_H'])) for r in m.rows()]==[(32*i,32) for i in range(5)]  # page.rows.height, 32 in OLED
m.row(3); assert m.asked()==('job','liked','retry'); m.answer('ok','Retry queued','',[])
m.back(); m.back(); m.back()
assert m.labels()[0]=='Downloads'
m.row(2); assert m.asked()==('jobs',)
m.answer(*OK,[('job','liked','paused','2/3 · Choose Resume','Liked Songs','2')])
m.advance(2250); assert m.asked()==('jobs',)
m.answer('ok','','p2',[('job','liked','downloading','2/3 · Saving 3','Liked Songs','2')])
# A failed refresh keeps the list, its fields (the cursor too) still on the reply it shows, not the
# failed one, which is freed.
shown=m.labels(); m.poison={}; m.advance(2250); assert m.asked()==('jobs',); m.answer('offline','No Wi-Fi','',[])
assert m.labels()==shown and shown[-2]=='More…'; del m.poison
m.row(1); assert m.asked()==('jobs','p2'); m.answer(*OK,[]); m.back()
m.row(0); m.row(0)
assert m.asked()==('job','liked','pause'); m.answer('ok','Paused','',[])
m.back(); m.back(); m.row(3); assert m.labels()[0]=='Storage Management'
m.row(0); assert m.asked()==('collections',)
m.answer('ok','','p2',[('collection','a','A','1/1','',''),('collection','b','B','1/1','','')])
m.row(0); assert 'Selected' in m.labels()
m.row(1); m.row(0); assert m.labels().count('Selected')==1  # toggle A off, B remains
m.row(2); assert m.asked()==('collections','p2')
m.answer(*OK,[('collection','c','C','1/1','','')]); m.row(0); m.row(1)
assert m.asked()==('remove','collections','b,c')
m.answer('ok','Remove 2 playlists, 1 tracks?','',[])
card(m,'remove_answer',1); assert m.asked()==('remove','collections','b,c','confirm')
m.answer('ok','Removed','',[]); assert m.labels()[0]=='Storage Management'
m.row(2); assert m.asked()==('remove','all','')
m.answer('ok','Remove 1 playlists, 3 tracks?','',[]); card(m,'remove_answer',0)  # Cancel preserves downloads
assert m.labels()[0]=='Storage Management'
# Return before Clear All's preview answers: a later action's ok reply never opens Remove Downloads?.
m.row(2); assert m.asked()==('remove','all',''); m.back(); assert m.labels()[0]=='Downloads'
m.row(2); m.answer(*OK,[('job','liked','paused','2/3','Liked Songs','2')]); assert chevron(m,0); m.row(0); m.row(0)
assert m.asked()==('job','liked','pause'); m.answer('ok','Paused','',[])
assert m.labels()[0]=='Liked Songs' and m.toasts[-1][3]=='Paused'
m.back(); m.back(); m.row(3); assert m.labels()[0]=='Storage Management'
m.row(1); m.answer(*OK,[track('spotify:track:a','A')]); m.row(0); m.row(1)
assert m.asked()==('remove','tracks','spotify:track:a')
m.answer('ok','Remove 0 playlists, 1 tracks? 2 playlists may become incomplete','',[])
assert b'Remove 0 playlists, 1 tracks? 2 playlists may become incomplete\nPlaylists can be left incomplete.\0' in bytes(m.u.mem_read(payload_syms['br'],0x2000))  # the card's body (spotify.c br.remove_note)
card(m,'remove_answer',1); assert m.asked()==('remove','tracks','spotify:track:a','confirm'); passed()
# caps answered after Downloads opened (asked again once the base was idle, a request having cancelled
# it): the open level is laid out again, so its rows and what a press opens agree.
HUB=('collections','collection','playcollection','downloadliked','jobs','job','remove','download')
m=BrowseMachine(); m.caps=HUB; m.open(); m.answer(*ME)
br=payload_syms['br']; m.word(br+96,0); m.word(br+100,0)  # caps_checked, downloads: as a cancelled caps leaves them
m.advance(2250); assert m.asked()==('caps',)
m.row(6); assert m.labels()[:4]==['Downloads','Downloaded Tracks','','Download Status']
m.serve=lambda op,args: ('ok','','',[('op',n,'','','','') for n in HUB]); m.worker()
assert m.labels()==['Downloads','Downloaded Playlists','','Downloaded Tracks','','Download Progress','','Storage Management',''],m.labels()
m.row(3); assert m.labels()[0]=='Storage Management'; passed()
# A transport or temporary caps failure retries after two seconds; a valid unsupported response
# is terminal for an older add-on, without repeatedly probing it.
for failure in (None, b'Q2SPOT/1\nmalformed', ('offline','Starting','',[]), ('throttled','Wait','',[])):
    m=BrowseMachine(); m.open(); m.serve=lambda op,args: ME; m.worker(); assert m.asked()==('caps',)
    m.serve=(lambda op,args: failure) if failure is not None else None; m.worker()
    assert m.labels()==MENU and m.get(br+96)==0
    threads=len(m.threads); m.advance(1750); assert len(m.threads)==threads
    m.advance(250); assert len(m.threads)==threads+1 and m.asked()==('caps',)
    m.serve=lambda op,args: ('ok','','',[('op',n,'','','','') for n in HUB]); m.worker()
    m.row(6); assert m.labels()[1]=='Downloaded Playlists'; passed()
m=BrowseMachine(); m.open(); m.serve=lambda op,args: ME; m.worker()
m.serve=lambda op,args: ('error','Unsupported operation','',[]); m.worker()
threads=len(m.threads); m.advance(5000); assert m.get(br+96)==1 and len(m.threads)==threads; passed()
# A jobs refresh that shrinks the list must cancel a click queued against its previous rows.
m=BrowseMachine(); m.caps=HUB; m.open(); m.answer(*ME); m.row(6); m.row(2)
jobs=[('job',k,'paused','1/2',k,'1') for k in ('a','b')]
m.answer(*OK,jobs); m.advance(2250); assert m.asked()==('jobs',)
f,ctx=m.handler(m.rows()[1],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
m.serve=lambda op,args: (*OK,jobs[:1]); worker,arg=m.threads[-1]
m.call(address=worker,args=(arg,0,0,0),gap=0,count=5_000_000)
m.call(address=payload_syms['br_tick'],args=(0,0,0,0),gap=0); m.advance(0)
assert m.labels()==['Download Progress','paused','1/2'] and m.get(br+16)==0; passed()
# Backends that don't advertise download support hide the action, with an update explanation in Downloads.
m=BrowseMachine(); m.caps=(); m.open(); m.answer(*ME); m.row(1)
m.answer(*OK,[('playlist','spotify:playlist:a','A','','','')]); m.row(0); m.answer(*OK,[])
assert m.labels()==['Nothing here']; passed()

# Card apps (patch/addon.c): programs on the card Vamp knows only by their folder, .<id> holding its
# manifest q2app, its launcher run and its binary q2-<id>; Streaming lists them after Spotify, by name,
# and a row's page is the app's list over Q2SPOT/1 on its own socket, each row's kind saying what a
# press does.
def card_app(m,app,manifest):
    """App `app` on the card: its folder in the card's listing, its manifest and its binary."""
    m.tree.setdefault('/mnt/mmc',[]).append(('.'+app,4))
    m.files[f'/mnt/mmc/.{app}/q2app']=bytearray(manifest.encode()); m.files[f'/mnt/mmc/.{app}/q2-{app}']=bytearray()

# The Library's Now Playing while Radio, a station or a video holds its song (now_source.c now_hold): stock's own
# update (playing_page_update, 0x53cf08) reads the state and time through now_page_status and now_page_time
# (NP_TIME_CALLS), so the song paused at 0:07 shows 0:07 and its bar there, not a stopped player's 0:00; the hold
# ended, the player's own state and time are read again.
def np_update(playtime, status):
    m=Machine(); m.handlers.pop(syms['mclGetPlayStatus'],None); m.mock('player_save_stop','player_save_start')
    m.np_slider=m.node('slider','slider_play',max=225,value=100)
    m.np_el=m.node('label','label_playtime'); m.top=m.node('window','playing_page',[m.np_slider,m.np_el,m.node('label','label_ipod_remain')])
    m.word(m.top+O['W_PARENT'],m.wm); m.playtime=playtime; m.word(PLAY_STATUS_WORD,status); return m
def np_tick(m): m.calls.clear(); m.call(address=0x53cf08,args=(m.top,0,0,0),gap=0); return m.nodes[m.np_slider]['value'],m.nodes[m.np_el].get('text')
from build import NP_TIME_CALLS
PLAY_STATUS_WORD=0xa50084  # mclGetPlayStatus's word: 1 stopped, 2 playing, 3 paused
for address,_,fn in NP_TIME_CALLS: assert struct.unpack_from('<I',demo,fileoff(demo,address))[0]==jal(payload_syms[fn]),fn
m=np_update(7,3); m.call(address=payload_syms['now_hold'],args=(0,0,0,0),gap=0)
m.word(PLAY_STATUS_WORD,1); m.playtime=0  # player_save_stop stopped it
assert np_tick(m)==(7,'0:07') and 'player_playtime_and_length' not in [c[0] for c in m.calls]; passed()
m.call(address=payload_syms['now_drop'],args=(0,0,0,0),gap=0); assert np_tick(m)==(0,'0:00'); passed()  # stopped, as stock shows it
m.word(PLAY_STATUS_WORD,2); m.playtime=12; assert np_tick(m)==(12,'0:12'); passed()  # playing: the player's
