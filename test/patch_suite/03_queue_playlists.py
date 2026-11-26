class QueueMachine(Machine):
    def __init__(self,page='allmusic_page',rows=20,queue=3,pos=0,mode=0,cls=0xf001):
        super().__init__()
        self.deqs={}; self.toasts=[]; self.sent=[]; self.picks=[]; self.airplay=0
        self.imports=[]; self.import_ret=1; self.m3u_files=[]; self.uncreated=set(); self.on_toast=None; self.on_open=None
        for n in ('_create_deque','deque_init','deque_init_copy','deque_size','deque_at','_deque_push_back',
                  'deque_assign','deque_clear','deque_destroy','deque_pop_back','send@GLIBC_2.0','window_manager_get_input_device_status',
                  'navigator_to','navigator_to_with_context','window_close','widget_on','widget_destroy_children',
                  'getMusicByAlbum','getMusicByAlbumAndSonger','getMusicByAlbumAndAlbumSonger','toolsLoadDirectory',
                  'getMusicBySonger','getMusicByAlbumArtist','getMusicByComposer','getMusicByGenre',
                  'getMusicByAlbumAndComposer','getMusicByAlbumAndGenre','getMusicByPlayList','checkFavExist','navigator_window_is_exist',
                  'mcl_shuffle_pick','airplayGetFlag','calloc@GLIBC_2.0','qsort@GLIBC_2.0',
                  'importPlayList','toolsGetDirectoryM3u8'): self.handlers[syms[n]]='q:'+n
        self.favs=set(); self.open_pages=set(); self.opened=[]
        self.handlers.pop(syms['mclLoadPlayList'])
        self.mock('strlen@GLIBC_2.0','snprintf@GLIBC_2.0'); self.handlers[syms['strcmp@GLIBC_2.0']]='tk_strcmp'
        self.mock('mclStartPlayer','mclStop','mclSetPause','mclSetResume','mclSetSeek')
        self.status=self.alloc(0x200); self.word(syms['g_class_type'],cls)
        self.surface,_,_=self.table_page(4,page,rebind=True); self.word(self.surface+O['TABLE_ROWS'],rows)
        self.word(syms['p_deque_showlist'],self.deque([self.song(f'Row {i}') for i in range(rows)]))
        self.word(syms['mcl_pdeqplaylist'],self.deque([self.song(c) for c in 'ABC'[:queue]]))
        self.word(syms['tools_pdeq_directory'],self.deque([self.song('staged')]))
        self.word(O['MCL_POOL'],self.deque(list(range(queue)),'int'))
        for k,v in (('MCL_POS',pos),('MCL_MODE',mode),('MCL_TYPE',cls),('MCL_LASTPOS',-1),('MCL_PRELOAD',0),('MCL_FD',-1)): self.word(O[k],v)
        self.found=[self.song('T1'),self.song('dir',4),self.song('T2')]; self.listings={}; self.stack=[self.top]
        self.folder('/mnt/sd/Music'); self.paint(self.surface)
    def song(self,name,kind=8):
        r=self.alloc(0x60)
        for off,v in (('REC_ID',1),('REC_NAME',self.string(name)),('REC_PATH',self.string('/p/'+name)),
                      ('REC_ALBUM',self.string('Album')),('REC_ARTIST',self.string('Artist')),('REC_TYPE',kind)): self.word(r+O[off],v)
        return r
    def deque(self,items,kind='stSongInfo'):
        h=self.alloc(0x20); self.deqs[h]=[kind,[self.copy(kind,e) for e in items]]; return h
    def copy(self,kind,e):
        a=self.alloc(0x60); self.u.mem_write(a,struct.pack('<I',e&0xffffffff) if kind=='int' else bytes(self.u.mem_read(e,0x60)))
        return a
    def items(self,h): return self.deqs[h][1]
    def row(self,i): return self.items(self.get(syms['p_deque_showlist']))[i]
    def names(self,h=None): return [self.text(self.get(e+O['REC_NAME'])) for e in self.items(h or self.get(syms['mcl_pdeqplaylist']))]
    def pool(self): return [signed(self.get(e)) for e in self.items(self.get(O['MCL_POOL']))]
    def mcl(self,k): return signed(self.get(O[k]))
    def dialog(self):
        self.view=self.node('scroll_view','scroll_view',[self.node('list_item') for _ in range(5)])
        self.title=self.node('hscroll_label','scrlabel_title'); self.dlg_return=self.node('image','img_return')  # not back: a Machine's back() is Return
        return self.node('dialog','sortselect_dialog',[self.node('view','view_navbar',[self.title,self.dlg_return]),
                                                       self.node('list_view','list_view',[self.view])])
    def bootvol(self):  # stock's Boot volume page, which crossfade.c re-dresses
        self.slider=self.node('slider','slider_bootvol'); self.vol=self.node('label','label_vol')
        self.dec,self.add=self.node('image','img_dec'),self.node('image','img_add')
        self.title,self.switch=self.node('label','label_title'),self.node('image','img_bootswitch')
        view=self.node('view','view_bootvol',[self.slider,self.dec,self.add,self.vol],visible=0)
        self.nodes[view]['enable']=0  # stock hides and disables it while its switch is off
        return self.node('window','bootvol_page',[self.node('view','view_navbar',[self.title,self.switch]),view])
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('q:'): return super().hook(u,address,size,unused)
        assert u.reg_read(UC_MIPS_REG_T9)==address, name
        name=name[2:]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        self.calls.append((name,a,b,c))
        if name=='_create_deque': ret=self.alloc(0x20); self.deqs[ret]=[self.text(a),[]]
        elif name in ('deque_init','deque_clear'): self.deqs[a][1]=[]
        elif name in ('deque_init_copy','deque_assign'): self.deqs[a][1]=[self.copy(self.deqs[a][0],e) for e in self.items(b)]
        elif name=='deque_size': ret=len(self.items(a))
        elif name=='deque_at': ret=self.items(a)[b]
        elif name=='_deque_push_back': self.items(a).append(self.copy(self.deqs[a][0],b))
        elif name=='deque_destroy': del self.deqs[a]
        elif name=='deque_pop_back': self.items(a).pop()
        elif name=='send@GLIBC_2.0': self.sent.append((a,bytes(u.mem_read(b,c)),c,d)); ret=c
        elif name=='window_manager_get_input_device_status': ret=self.status
        elif name=='airplayGetFlag': ret=self.airplay
        elif name=='navigator_to':
            page=self.text(a); self.top=self.dialog() if page=='dialog/sortselect_dialog' else self.bootvol() if page=='playset/bootvol_page' else self.node('window',page)
            self.stack.append(self.top); self.opened.append((page,None))
        elif name=='navigator_to_with_context':
            page=self.text(a)
            if page=='dialog/msginfo_dialog': self.toasts.append((page,self.get(b),self.get(b+4),self.text(b+8)))
            elif page=='playing_page': self.opened.append((page,self.names(self.get(b)),self.get(b+4),self.get(b+8),self.get(b+12)))
            elif page=='localmusic/playlist_page': self.opened.append((page,b))
            elif page=='localmusic/playlistsmore_page': self.opened.append((page,b))
            else: self.opened.append((page,self.get(b),self.get(b+4))); self.top=self.node('window',page); self.stack.append(self.top)
        elif name=='importPlayList':  # Playlists' list reloaded with the files' names, but a create that failed (-1)
            only=self.get(payload_syms['libup']); names=[]  # libup.only: each file's op and name, NUL-ended, an empty one last
            while only and self.text(only): names+=[self.text(only)[1:]]*(self.text(only)[0]=='i'); only+=len(self.raw(only))+1
            self.imports.append(''.join(f'/mnt/mmc/_explaylist_data/{n}\n' for n in names) if self.get(payload_syms['libup']) else None)
            ret=self.import_ret; names=[n.rsplit('.',1)[0] for n in names]  # addon_m3u_dir's
            if ret!=-2: self.word(syms['p_deque_playlist'],self.deque([self.song(n) for n in names if n not in self.uncreated]))
        elif name=='toolsGetDirectoryM3u8': self.deqs[b][1]=[self.copy('stSongInfo',r) for r in self.m3u_files]; ret=len(self.m3u_files)
        elif name=='checkFavExist': ret=self.text(self.get(a+O['REC_NAME'])) in self.favs
        elif name=='navigator_window_is_exist': ret=self.text(a) in self.open_pages
        elif name=='window_close': self.stack.remove(a); self.top=self.stack[-1]
        elif name=='widget_destroy_children': self.nodes[a]['children']=[]
        elif name=='widget_on':
            self.nodes[a].setdefault('handlers',[]).append((b,c,d)); ret=1
            if b==O['EVT_CLICK']:
                em=self.alloc(4); it=self.alloc(0x28); self.word(a+O['W_EMITTER'],em); self.word(em,it); self.word(it+O['EMIT_TYPE'],b)
        elif name=='mcl_shuffle_pick':  # as stock: back to the previous index once, else forward from it
            self.picks.append(a); last=self.mcl('MCL_LASTPOS'); self.word(O['MCL_LASTPOS'],-1 if not a else self.mcl('MCL_POS'))
            self.word(O['MCL_POS'],last if not a and last!=-1 else len(self.names())-1); ret=1
        elif name=='calloc@GLIBC_2.0': ret=self.alloc(a*b+4)  # the album ratings' table
        elif name=='qsort@GLIBC_2.0': self.qsort(a,b,c,d)  # Albums' sorted lookups, the payload's comparator run nested
        else:  # the album/folder queries fill the staging deque
            self.query=(name,a if name in ('getMusicByPlayList','sort_playlist_addtime','sort_playlist_first_alphabet') else self.text(a) if a else None,(self.text(b) if b else None) if 'And' in name else None,c)
            found=self.listings.get(self.text(a),self.found) if name=='toolsLoadDirectory' else self.found
            if name=='toolsLoadDirectory': self.__dict__.setdefault('loaded_dirs',[]).append(self.text(a))  # read now: a stack buffer
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in found]; ret=3
            if name=='getMusicByAlbum' and a:  # songs of that album name
                for e in self.items(self.get(syms['tools_pdeq_directory'])): self.word(e+O['REC_ALBUM'],a)
        self.returns(ret,clobber=True)
    def press(self,t):
        self.word(self.status+O['INPUT_KEYS'],O['KEY_PLAY']); self.word(self.status+O['INPUT_KEYS']+O['INPUT_KEY_TIME'],t)
    def hold(self):
        ret=self.call(O['KEY_PLAY'],address=syms['on_wm_keylong_fun'],event_type=O['EVT_KEY_LONG'],gap=0)
        self.advance(0,clear=False)
        return ret
    def release(self):
        """Hook, then the real stock key-up; AWTK clears the key record afterwards."""
        if self.call(O['KEY_PLAY'],gap=0)==0: self.call(O['KEY_PLAY'],address=syms['on_wm_keyup_fun'],gap=0,clear=False)
        self.u.mem_write(self.status+O['INPUT_KEYS'],bytes(O['INPUT_KEY_SIZE']))
        return sum(c[0]=='playpause_quick_click' for c in self.calls)
    def handler(self,w,kind): return next((f,ctx) for t,f,ctx in self.nodes[w]['handlers'] if t==kind)
    def at(self,label):  # a menu row's place among the view's children, by its words
        kids=self.nodes[self.view]['children']
        return next(i for i,c in enumerate(kids) if self.nodes[c]['children'] and self.nodes[self.nodes[c]['children'][0]]['text']==label)
    def pick(self,row):
        """Click a menu row, by its index among the view's children or by its label."""
        if isinstance(row,str): row=self.at(row)
        f,ctx=self.handler(self.nodes[self.view]['children'][row],O['EVT_CLICK'])
        assert self.call(address=f,args=(ctx,self.event,0,0),gap=0)==0
    def run(self,row,steps=0):
        """Wheel down steps rows, hold, pick a menu row and let the deferred action run."""
        for _ in range(steps): self.call()
        n=len(self.toasts); self.t=getattr(self,"t",7000)+1; self.press(self.t); assert self.hold()==11 and self.release()==0
        self.pick(row); self.advance(0)
        return self.toasts[-1][3] if len(self.toasts)>n else None
    def labels(self): return [self.nodes[self.nodes[i]['children'][0]]['text'] for i in self.nodes[self.view]['children'] if self.nodes[i]['children']]  # the rows: the sheet's header and gaps hold none
    def playback(self): return [c for c in self.calls if c[0] in ('mclStartPlayer','mclStop','mclSetPause','mclSetResume','mclSetSeek','playpause_quick_click')]

sel=lambda m:[c[:2] for c in m.calls if c[0].startswith('batch_')]

# Lookup playback must unwind animated pages synchronously: navigator_back only schedules a
# close, so a stock loop waiting for the top window to change starves the animation forever.
class LookupMachine(QueueMachine):
    def __init__(self, pages, selected=1, lookup=True):
        super().__init__(rows=3)
        self.selected=selected; self.closed=[]; self.backs=0
        self.stack=[self.node('window',name) for name in pages]; self.top=self.stack[-1]
        for w in self.stack: self.word(w+0x10,self.string(self.nodes[w]['name']))
        self.byte(syms['g_songerinfo_flag'],int(lookup)); self.byte(syms['g_navbar_status'],0)
        for name in ('navigator_back','navigator_close'): self.handlers.pop(syms[name],None)
        self.mock('widget_get_window','widget_child','window_manager_back','window_manager_close_window_force',
                  'window_close','navigator_window_is_exist','strstr@GLIBC_2.0','strcpy@GLIBC_2.0',
                  'strtok@GLIBC_2.0','access@GLIBC_2.0','player_load_songlist','table_row_of',prefix='lookup:')
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('lookup:'): return super().hook(u,address,size,unused)
        assert u.reg_read(UC_MIPS_REG_T9)==address
        name=name[7:]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='widget_get_window': ret=self.top
        elif name=='widget_child': ret=next((w for w in self.stack if self.nodes[w]['name']==self.text(b)),0)
        elif name=='navigator_window_is_exist': ret=int(any(self.nodes[w]['name']==self.text(a) for w in self.stack))
        elif name=='window_manager_back':
            self.backs+=1
            # iPod close animation needs the main loop: no top-window change inside this callback.
        elif name in ('window_manager_close_window_force','window_close'):
            w=b if name=='window_manager_close_window_force' else a
            self.closed.append(self.nodes[w]['name']); self.stack.remove(w); self.top=self.stack[-1]
        elif name=='strstr@GLIBC_2.0':
            i=self.text(a).find(self.text(b)); ret=a+i if i>=0 else 0
        elif name=='strcpy@GLIBC_2.0': self.u.mem_write(a,self.text(b).encode()+b'\0'); ret=a
        elif name=='strtok@GLIBC_2.0':
            if a: self.token=a
            ret=self.token
            s=self.text(ret); i=s.find(self.text(b))
            if i>=0: self.byte(ret+i,0); self.token=ret+i+1
        elif name=='table_row_of': ret=a
        elif name=='player_load_songlist':
            assert self.text(b)==f'Row {self.selected}'
            self.deqs[a][1]=[self.copy('stSongInfo',e) for e in self.items(self.get(syms['p_deque_showlist']))]
            ret=self.selected
        self.returns(ret,clobber=True)

for callback,button,pages in (
    (0x4af498,'track', ['artistinfo_page']),
    (0x4a7b28,'albuminfo', ['artistinfo_page','albuminfo_page']),
    (0x537cc0,'', ['playerjumpinfo_page']),
):
    for lookup in (False,True):
        for selected in (0,2):
            m=LookupMachine(['home_page','playing_page']+pages,selected,lookup)
            w=m.node('button',f'{button}_{selected}'); m.word(w+0x10,m.string(f'{button}_{selected}'))
            m.word(w+0x78,selected); m.word(m.event+0x10,w)
            assert m.call(address=callback,args=(w,m.event,0,0),gap=0)==0
            assert m.opened[-1]==('playing_page',['Row 0','Row 1','Row 2'],selected,0xf001,2)
            unwind=lookup or callback==0x537cc0
            assert [m.nodes[w]['name'] for w in m.stack]==(['home_page'] if unwind else ['home_page','playing_page']+pages)
            assert m.backs==0
            assert m.closed==(list(reversed(pages))+['playing_page'] if unwind else [])
            passed()

# Songs of one file name: two server songs of one title (a server row's file name is its title, catalogue.c imp_song)
# or two card files of one name in two folders. Stock's own player_load_songlist and load_searchsong_list run here:
# each answers the first song of its list with the pressed one's file name and CUE start, so a press on the later
# song started the earlier. The press starts its own row (playback.c ringnav_songlist, ringnav_searchlist), from
# Songs, an artist's tracks and Search's songs; a CUE image's tracks, which share their file and differ by start, and
# a name that differs only in case each keep theirs, and a folder row before the songs moves none of them.
class SameNameMachine(LookupMachine):
    def __init__(self,rows,selected,search=False):
        super().__init__(['home_page','playing_page','allmusic_page'],selected,False)
        for n in ('player_load_songlist','load_searchsong_list'): self.handlers.pop(syms[n],None)  # stock's: copy, find
        self.word(0xa3a0e8,0x1000014); self.handlers[0x1000014]='lookup:malloc'  # lazy-bound malloc, as the Era's
        self.word(0xa3a270,0x1000010); self.handlers[0x1000010]='lookup:free'
        self.handlers[payload_syms['catalogue_access']]='lookup:playable'  # a server row that can play now (online)
        recs=[]
        for name,path,cue,*kind in rows:
            r=self.song(name,*kind); self.word(r+O['REC_PATH'],self.string(path)); self.word(r+O['REC_CUE_START'],cue); recs.append(r)
        self.word(syms['p_deque_searchshowlist' if search else 'p_deque_showlist'],self.deque(recs))
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='lookup:malloc': self.returns(self.alloc((u.reg_read(REGS[0])+3)&~3),clobber=True); return
        if name=='lookup:free': self.returns(0,clobber=True); return
        if name=='lookup:playable': self.returns(0,clobber=True); return
        return super().hook(u,address,size,unused)
    def start_at(self):  # where the list Now Playing was opened with starts
        page,_,at,_,_=self.opened[-1]; assert page=='playing_page'
        return at
SERVER='/mnt/mmc/.subsonic/cat'
for rows in ([('Downfall',f'{SERVER}/al1/s1.flac',0),('Downfall',f'{SERVER}/al2/s2.flac',0),('Other',f'{SERVER}/al3/s3.flac',0)],
             [('Downfall',f'{SERVER}/al1/s1.flac',0),('Downfall',f'{SERVER}/al1/s2.flac',0)],  # one album's name and id
             [('Downfall.flac','/mnt/mmc/A/Downfall.flac',0),('Other.flac','/mnt/mmc/C/Other.flac',0),
              ('Downfall.flac','/mnt/mmc/B/Downfall.flac',0)],
             [('Downfall','/mnt/mmc/a.flac',0),('DOWNFALL','/mnt/mmc/b.flac',0)],
             [('Live.cue','/mnt/mmc/Live.flac',0),('Live.cue','/mnt/mmc/Live.flac',245)],
             [('Live','/mnt/mmc/Live',0,4),('Downfall','/mnt/mmc/a.flac',0),('Downfall','/mnt/mmc/b.flac',0)]):
    songs=[i for i,r in enumerate(rows) if r[3:]!=(4,)]  # what stock copies, and what a start indexes
    for callback,button,search in ((0x4aac14,'row',False),(0x4af498,'track',False),(0x49d084,'track',True)):
        for selected in songs:
            m=SameNameMachine(rows,selected,search)
            w=m.node('button',f'{button}_{selected}'); m.word(w+0x10,m.string(f'{button}_{selected}'))
            m.word(w+O['ROW_INDEX'],selected); m.word(m.event+O['EVENT_TARGET'],w)
            assert m.call(address=callback,args=(w,m.event,0,0),gap=0)==0
            assert m.start_at()==songs.index(selected),(rows[selected],callback,m.start_at())
            passed()

# Now Playing More's Artist info (stock's 0x5398fc, build.py MORE_ARTIST_CLOSE, #303) closes the artist page and the
# album page the song was played from before it opens the artist's: stock asked for both by name and closed them by
# their asset paths, which no window is named, so a second artist page shared the first's record and each close freed it.
m=LookupMachine(['home_page','localclass_page','artistinfo_page','albuminfo_page','playing_page','playermore_page'],lookup=False)
m.byte(syms['g_play_id3_info']+0x504,1); m.word(O['MCL_POS'],1)  # the playing track names an artist
assert m.call(address=0x5398fc,args=(m.top,m.event,0,0),gap=0)==0
assert m.closed==['playermore_page','artistinfo_page','albuminfo_page'] and m.backs==0
assert m.opened==[('localmusic/artistinfo_page',0xf001,m.items(m.get(syms['mcl_pdeqplaylist']))[1])]
assert m.u.mem_read(syms['g_songerinfo_flag'],1)==b'\1'; passed()

# Folder view's row click (ringnav_folder_click over stock's 0x532414): a video file goes to video_click,
# which plays it, and stock never queues it as a song; a song row, a video video_click
# refuses and any row in select mode stay stock's (folder_enter's 8, then the queue and Now Playing).
class FolderClickMachine(LookupMachine):
    def __init__(self,*a):
        super().__init__(*a); self.videos=[]
        self.handlers[payload_syms['video_click']]='lookup:video_click'; self.handlers[syms['folder_enter']]='lookup:folder_enter'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='lookup:video_click':
            path=self.text(u.reg_read(UC_MIPS_REG_A0)); self.videos.append(path); self.returns(int(path.endswith('.mp4')),clobber=True); return
        if name=='lookup:folder_enter': self.returns(8,clobber=True); return
        return super().hook(u,address,size,unused)
for selected,name,navbar,played in ((1,'clip.mp4',0,True),(1,'clip.mp4',1,True),(0,'Row 0',0,False)):  # select mode selects
    m=FolderClickMachine(['home_page','folder_page'],selected,False); m.byte(syms['g_navbar_status'],navbar)
    m.items(m.get(syms['p_deque_showlist']))[1]=m.song('clip.mp4')
    w=m.node('button','row'); m.word(w+O['ROW_INDEX'],selected); m.word(m.event+O['EVENT_TARGET'],w)
    m.call(address=payload_syms['ringnav_folder_click'],args=(w,m.event,0,0),gap=0)
    assert m.videos==([] if navbar else ['/p/'+name]),(name,navbar,m.videos)
    assert played==(not any(o[0]=='playing_page' for o in m.opened)),(name,navbar)
passed()

# A Library song's menu sheet (hold_menu.c's own song; On-The-Go is on in these machines), and the
# song rows Now Playing's own menu keeps around its View Queue and settings.
SONG_MENU=['Play Next','Play Last','Add to On-The-Go','Add to Playlist…','Favorite','Go to Album','Go to Artist']
NP_SONG=['Play Next','Play Last','Favorite','Add to Playlist','Go to Album','Go to Artist']
# Now Playing targets the queue even when the last browsed list has unrelated rows.
def playing_menu(**kw):
    m=QueueMachine(page='playing_page',pos=1,**kw)
    return m
for state in (3,2):  # paused and playing both expose the same actions
    m=playing_menu(); m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(0xa50084,state)
    m.press(5000); assert m.release()==1
    m.press(6000); assert m.hold()==11 and m.labels()==NP_SONG[:2]+['View Queue']+NP_SONG[2:]+["Shuffle","Repeat","Group by"]
    assert m.nodes[m.title]['text']=='B' and m.release()==0
    m.press(7000); assert m.release()==1 and m.get(0xa50084)==state; passed()
# A tagged song's menu is titled by its tag, not its file name.
m=playing_menu(); m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[1]+O['REC_TITLE'],m.string('Bee'))
m.press(6000); assert m.hold()==11 and m.nodes[m.title]['text']=='Bee'; passed()
# A song in a hidden folder isn't in the Library: no Go to rows, unless its app's q2app has goto=1 (addon_goes).
m=playing_menu(); m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[1]+O['REC_PATH'],m.string('/mnt/mmc/.podcasts/Show/B.mp3'))
m.press(6000); assert m.hold()==11 and m.labels()==NP_SONG[:2]+['View Queue']+NP_SONG[2:4]+["Shuffle","Repeat","Group by"]; passed()
# A folder play's untagged record: the player's parsed title once it has parsed that file.
m=playing_menu(); id3=syms['g_play_id3_info']; m.u.mem_write(id3,b'/p/B\0'); m.u.mem_write(id3+O['ID3_TITLE'],b'Parsed\0')
m.press(6000); assert m.hold()==11 and m.nodes[m.title]['text']=='Parsed'; passed()
# View Queue opens Up Next (upnext.c, in the menu sheet), not stock's queue page, and plays nothing
m=playing_menu(); assert m.run(2) is None and not any(o[0]=='playerqueue_page' for o in m.opened) and m.names()==['A','B','C'] and not m.playback(); passed()
# OLED says what an in-place action did (navigation.c's toasts): Playing Next, Added to Queue, the favourites' own.
# Play Last lands at the end of Playing Next (playback.c), here empty: right after the playing song, as Play Next does
for action,expected,said in ((0,['A','B','B','C'],'Playing Next'),(1,['A','B','B','C'],'Playing Last')):
    m=playing_menu(); assert m.run(action)==said and m.names()==expected and not m.playback(); passed()
m=playing_menu(); assert m.run(3)=='Added to Favorites'
assert [c[2:] for c in m.calls if c[0]=='batch_add_file']==[(0xf00a,m.get(syms['mcl_pdeqplaylist']))]
assert sel(m)[:2]==[('batch_init_selectrecord',3),('batch_set_selectitem',1)]; passed()
m=playing_menu(); m.favs.add('B'); assert m.run(3)=='Removed from Favorites'
assert [c[1] for c in m.calls if c[0]=='deleteMusicFromFav']==[m.items(m.get(syms['mcl_pdeqplaylist']))[1]]; passed()
m=playing_menu(); m.run(4)
assert m.opened[-1]==('localmusic/playlist_page',0x2f001)
assert sel(m)==[('batch_init_selectrecord',3),('batch_set_selectitem',1)]; passed()
for action,page in ((5,'playerjumpinfo_page'),(6,'localmusic/artistinfo_page')):
    m=playing_menu(); m.config['ARTISTCF']='0'; m.run(action); assert m.opened[-1][0]==page
    if action==6: assert m.opened[-1][2]==m.items(m.get(syms['mcl_pdeqplaylist']))[1]
    passed()
for stage in ('open','action'):
    for change in ('advance','replace','resize','other','song','cue','stream'):
        m=playing_menu(); m.press(100)
        if stage=='action': assert m.hold()==11; m.release(); m.pick(0)
        else: assert m.call(O['KEY_PLAY'],address=syms['on_wm_keylong_fun'],event_type=O['EVT_KEY_LONG'],gap=0)==11
        q=m.get(syms['mcl_pdeqplaylist'])
        if change=='advance': m.word(O['MCL_POS'],2)
        elif change=='replace': m.word(syms['mcl_pdeqplaylist'],m.deque([m.song(c) for c in 'ABC']))
        elif change=='resize': m.items(q).append(m.song('D'))
        elif change=='other': m.word(m.items(q)[0]+O['REC_PATH'],m.string('/different'))
        elif change=='song': m.word(m.items(q)[1]+O['REC_PATH'],m.string('/different'))
        elif change=='cue': m.word(m.items(q)[1]+O['REC_CUE_START'],123)
        else: m.word(O['MCL_TYPE'],5)
        m.advance(0)
        if stage=='open': assert m.nodes[m.top]['name']=='playing_page'
        else: assert m.names(q)==(['A','B','C','D'] if change=='resize' else ['A','B','C'])
        assert not m.playback(); passed()
for kw in ({'queue':0},{'queue':1},{'queue':3}):
    m=playing_menu(**kw)
    if kw['queue']==3: m.word(O['MCL_TYPE'],5)
    m.press(100); assert m.hold()==0 and m.nodes[m.top]['name']=='playing_page'; passed()
m=playing_menu(); m.airplay=2; m.press(100); assert m.hold()==0; passed()

# The existing non-pooled playlist rows are guarded at their final click dispatch.
class PlayingPlaylistMachine(QueueMachine):
    STOCK_PICK=0x4b4840
    def __init__(self):
        super().__init__(page='playing_page',pos=1)
        self.added=[]
        self.handlers[self.STOCK_PICK]='playlist_pick'
    def hook(self,u,address,size,unused):
        if address==self.STOCK_PICK and address in self.handlers:
            self.added.append(self.names()[1])
            self.returns(0); return
        opening=self.handlers.get(address)=='q:navigator_to_with_context' and self.text(u.reg_read(UC_MIPS_REG_A0))=='localmusic/playlist_page'
        super().hook(u,address,size,unused)
        if opening:
            self.playlist_rows=[self.node('button',f'playlist_{i}') for i in range(3)]
            view=self.node('scroll_view',children=self.playlist_rows)
            page=self.node('window','playlist_page',[view]); self.top=page; self.stack.append(page)
            for row in self.playlist_rows:
                em,it=self.alloc(4),self.alloc(0x28)
                self.word(row+O['W_EMITTER'],em); self.word(em,it)
                self.word(it,row); self.word(it+O['EMIT_TYPE'],O['EVT_CLICK']); self.word(it+12,self.STOCK_PICK)
for stale in (False,True):
    m=PlayingPlaylistMachine(); m.run(4)
    assert m.nodes[m.top]['name']=='playlist_page'
    for row in m.playlist_rows:
        f,ctx=m.handler(row,O['EVT_CLICK']); assert f!=m.STOCK_PICK and ctx==row
    if stale: m.word(O['MCL_POS'],2)
    f,ctx=m.handler(m.playlist_rows[2],O['EVT_CLICK'])
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0  # a STOP would leave the scroll_view its grab
    assert m.added==([] if stale else ['B'])
    if not stale: assert sel(m)==[('batch_init_selectrecord',3),('batch_set_selectitem',1)]
    f,ctx=m.handler(m.top,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
    m.top=m.stack[0]; m.press(200); assert m.hold()==11; passed()

# Execute stock's rebuild and naming callback; only UI/dialog and database services are mocked.
class PlaylistMachine(PlayingPlaylistMachine):
    def __init__(self, count=0, mode=1):
        super().__init__()
        self.mock('button_create','list_item_create','image_create','view_create','hscroll_label_create',
                  'widget_use_style','widget_set_name','widget_set_text_utf8','widget_set_visible',
                  'image_set_draw_type','image_base_set_image','set_hscroll_label_attribute',
                  'hscroll_label_set_only_focus','hscroll_label_set_ellipses','hscroll_label_set_speed',
                  'gif_image_play','batch_get_selectitem','file_is_playing','getFormatString',
                  'getMusicByPlayList','get_albumcover_listsize','mclGetPlayStatus','list_get_img_pic',
                  'toolsTrimLeft','toolsTrimRight','tk_snprintf')
        self.handlers[0x4ad020]='row_toolbar'
        self.handlers[0x4b3f0c]='playlist_load'
        self.handlers[syms['widget_restack']]='picker_restack'
        self.handlers[syms['navigator_to']]='picker_dialog'
        self.word(syms['p_deque_playlist'],self.deque([self.song(f'List {i}') for i in range(count)]))
        # Stock's private playlist add-mode global, loaded through its GOT.
        data=(B/'stock-demo').read_bytes()
        from build import fileoff, GP
        self.mode=struct.unpack_from('<I',data,fileoff(data,GP-0x5810))[0]-0x482c
        assert self.mode==O['PLAYLIST_ADD_MODE']
        self.word(self.mode,mode)
        # playlist_art.c (test/coverflow.py tests it): art ready for the playlists (cd_num) in self.art, each ask kept
        self.art={}; self.asks=[]; self.refreshes=self.stops=0
        for n in ('playlist_art','playlist_art_refresh','playlist_art_stop'):
            if n in payload_syms: self.handlers[payload_syms[n]]='pl:'+n
        self.view=self.node('scroll_view','scroll_view')
        self.top=self.node('window','playlist_page',[self.view])
        self.word(self.view+O['W_PARENT'],self.top); self.word(self.top+O['W_PARENT'],self.wm)
    def rebuild(self):
        assert self.call(address=0x4b5444,args=(self.top,0,0,0),gap=0)==0
        kids=self.nodes[self.view]['children']
        for i,row in enumerate(kids): self.word(row+O['W_Y'],i*48)
        self.word(self.view+O['VIEW_CONTENT_H'],len(kids)*48)
        return kids
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a,b,c,d=[u.reg_read(r) for r in REGS]
        if name.startswith('pl:'):
            ret=0
            if name=='pl:playlist_art':
                self.asks.append((a,self.text(b),c)); url=self.art.get(a)
                assert self.get(u.reg_read(UC_MIPS_REG_SP)+16)>len(url or '')
                if url: self.u.mem_write(d,url.encode()+b'\0'); ret=1
            elif name=='pl:playlist_art_refresh': self.refreshes+=1
            else: self.stops+=1
            self.returns(ret); return
        if name=='picker_dialog' and self.text(a)!='dialog/addplaylist_dialog':
            self.handlers[address]='q:navigator_to'
            super().hook(u,address,size,unused)
            self.handlers[address]=name
            return
        if name in ('picker_restack','picker_dialog'):
            if name=='picker_restack':
                kids=self.nodes[self.view]['children']; kids.remove(a); kids.insert(b,a); ret=0
            else:
                assert self.text(a)=='dialog/addplaylist_dialog'
                ret=0
            self.returns(ret); return
        if name=='widget_off_by_func':
            self.nodes[a]['handlers']=[h for h in self.nodes[a].get('handlers',[]) if h!=(b,c,d)]
        super().hook(u,address,size,unused)
        if name=='q:widget_on' and b==O['EVT_CLICK']:
            it=self.get(self.get(a+O['W_EMITTER'])); self.word(it,d); self.word(it+12,c)

# OLED's Playlists (its set has no toolbar, toolbar 0): rows over stock's own page. Stock's toolbar builder, row builder,
# row click and its handlers run for real; each hidden widget's click runs its real handlers, nested, as stock's dispatch.
# It makes the first system_bar load (oled_boot: no THEME stored, so theme_boot takes OLED's preset once, as on a unit
# an older build left).
class PlaylistPageMachine(PlaylistMachine):
    REBUILD_DONE=0x4b58d4
    def __init__(self,count=3,mode=0):
        super().__init__(count,mode)
        oled_boot(self)
        self.handlers.pop(self.STOCK_PICK); self.handlers.pop(0x4b3f0c)
        self.handlers[syms['widget_restack']]='picker_restack'
        self.mock('batch_get_selectitem','batch_set_selectitem','batch_init_selectrecord','batch_clear_selectrecord','batch_empty_selectrecord',
                  'batch_set_allselect','batch_delete_file','exportPlayList','list_selectbox_visiable','list_selectbox_value','set_allchoice_img',
                  'widget_layout','widget_set_children_layout','load_localclass_list','navbar_show_playgif','hscroll_label_set_only_focus',
                  'getFormatString','gif_image_create','widget_add_timer','widget_set_tr_text','image_animation_play','image_animation_pause','navigator_to_with_context')
        self.nav=self.node('view','view_navbar',visible=0); self.nodes[self.nav]['enable']=0; self.word(self.nav+O['W_PARENT'],self.top)
        self.nodes[self.top]['children'].insert(0,self.nav)
        # Stock's page init binds Return as widget_on(win, EVT_KEY_UP, on_common_keyup, win) at 0x4b6024; the bytes and the
        # GOT entries it loads ($25 widget_on, $6 on_common_keyup) pin this fixture's ctx to what the device runs.
        gp=0x4b5fac+0x584b34
        assert [self.get(0x4b6024+4*i) for i in range(7)]==[0x24050114,0x8fbc0010,0x02003825,0x8f99a7bc,0x8f86a7e0,0x0320f809,0x02002025]
        assert self.get(gp-0x5844)==syms['widget_on'] and self.get(gp-0x5820)==syms['on_common_keyup']==0x4f2394
        self.nodes[self.top].setdefault('handlers',[]).append((O['EVT_KEY_UP'],0x4f2394,self.top))
        self.word(syms['g_class_type'],0xf00b)
        self.marks=[]; self.answer=10; self.confirms=[]; self.exports=[]; self.deletes=[]; self.dialogs=[]; self.mores=[]
        # AWTK's emitter_on puts a handler first, and emitter_dispatch runs from the first: the newest runs first
        self.on_click=lambda w,e:[self.nested(f,ctx,e,0,0) for t,f,ctx in reversed(list(self.nodes[w].get('handlers',()))) if t==O['EVT_CLICK']]
        self.stack=[self.node('window','localmusic_page'),self.top]; self.lists(*[f'List {i}' for i in range(count)])
        self.word(syms['tools_pdeq_directory'],self.deque([self.song(c) for c in 'ABC']))  # a playlist's songs, as stock's query leaves them
    def lists(self,*names):
        records=[self.song(n) for n in names]
        for i,(r,n) in enumerate(zip(records,names)): self.word(r+O['REC_TITLE'],self.string(n)); self.word(r+O['REC_DISC'],10+i)  # cd_num
        self.word(syms['p_deque_playlist'],self.deque(records))
    def rebuild(self):
        assert self.call(address=0x4b5444,args=(self.top,0,0,0),gap=0,clear=False)==0
        self.stack_rows(); return self.nodes[self.view]['children']
    def stack_rows(self):  # stock's list layout: the shown rows in order, each its own height
        y=0
        for row in self.nodes[self.view]['children']:
            if self.nodes[row]['visible']: self.word(row+O['W_Y'],y); y+=self.get(row+O['W_H'])
        self.word(self.view+O['VIEW_CONTENT_H'],y); self.word(self.view+O['W_H'],294); self.word(self.view+O['W_W'],375)
    def rows(self):  # the shown rows' first lines
        return [self.row_text(r) for r in self.nodes[self.view]['children'] if self.nodes[r]['visible']]
    def row_text(self,row):
        button=self.nodes[row]['children'][0]; kids=self.nodes[button]['children']
        info=next((k for k in kids if self.nodes[k]['name']=='view_info'),None)
        return self.nodes[self.nodes[info]['children'][0] if info else kids[0]].get('text')
    def row_button(self,text):
        return next(self.nodes[r]['children'][0] for r in self.nodes[self.view]['children'] if self.row_text(r)==text)
    def navbar(self): return self.u.mem_read(syms['g_navbar_status'],1)[0]
    def bar(self): return self.nodes[self.nodes[self.nav]['children'][0]].get('text')
    def tap(self,button,x=0):
        ev=self.alloc(0x30); self.word(ev,O['EVT_CLICK']); self.word(ev+O['EVENT_X'],x)
        self.on_click(button,ev); self.advance(0,clear=False)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a,b,c,d=[u.reg_read(r) for r in REGS]
        ret=None
        if name=='widget_set_name': self.word(a+0x10,self.string(self.text(b)))  # stock's row click reads the field itself
        if name=='batch_init_selectrecord': self.marks=[0]*a; ret=0
        elif name=='batch_clear_selectrecord': self.marks=[]; ret=0
        elif name=='batch_empty_selectrecord': ret=int(not any(self.marks))
        elif name=='batch_set_selectitem': self.marks[a]^=1; ret=int(all(self.marks))
        elif name=='batch_get_selectitem': ret=self.marks[a] if a<len(self.marks) else 0
        elif name=='batch_set_allselect': self.marks=[a]*len(self.marks); ret=0
        elif name=='batch_delete_file': self.deletes.append(list(self.marks)); ret=0
        elif name=='exportPlayList': self.exports.append((a,list(self.marks))); ret=0
        elif name=='list_selectbox_visiable' or name=='list_selectbox_value':
            def walk(w):
                for k in self.nodes[w]['children']:
                    nm=self.nodes[k].get('name','') or ''
                    if nm.startswith('img_choice'):
                        if name.endswith('visiable'): self.nodes[k]['visible']=b; self.nodes[k]['image']='unselect'
                        else: self.nodes[k]['image']='mulselect' if b else 'unselect'
                    if nm.startswith('img_more') and name.endswith('visiable'): self.nodes[k]['visible']=int(not b)
                    walk(k)
            walk(a); ret=0
        elif name=='widget_layout': self.stack_rows(); ret=0
        elif name=='getFormatString': self.u.mem_write(b,b'Total %d songs\0'); ret=0
        elif name=='navigator_to_with_context':
            page=self.text(a)
            if page=='dialog/confirminfo_dialog': self.confirms.append(self.get(b)); ret=self.answer
            elif page=='localmusic/playlistsmore_page': self.mores.append(b); ret=0
            else: ret=0
        elif name=='picker_dialog' and self.text(a)=='dialog/addplaylist_dialog':
            self.dialogs.append(self.text(a)); ret=0
        if ret is not None:
            self.calls.append((name,a,b,c)); self.returns(ret); return
        super().hook(u,address,size,unused)
class RowPlaylistMachine(PlaylistPageMachine):  # OLED's page, past the first system_bar load as on a unit (oled_boot)
    def __init__(self,count=3,mode=0):
        super().__init__(count,mode)
def cpm_wheel(m,key,n=1):
    for _ in range(n): m.call(key); m.stack_rows()
    m.paint(m.view)
def cpm_centre(m):  # Centre on the selected row, the row's click running as stock's dispatch does
    m.confirm(); m.advance(0,clear=False); m.stack_rows(); m.paint(m.view)
NEW='New Playlist…'
LISTS=['List 0','List 1','List 2']
PL_ART=lambda cd: f'file:///mnt/mmc/.coverflow/pl_{cd}_0000abcd_s.jpg'
def pl_art_of(m,button): return next(k for k in m.nodes[button]['children'] if m.nodes[k]['type']=='image' and not (m.nodes[k].get('name') or '').startswith('img_'))

# A playlist row's click runs pl_pick alone, which counts the marks after stock's: every dress is on stock's fresh rows,
# PLAYLIST_PICK their one.
m=RowPlaylistMachine(); m.rebuild(); m.rebuild()
for t in LISTS: assert [f for e,f,c in m.nodes[m.row_button(t)]['handlers'] if e==O['EVT_CLICK']]==[payload_syms['pl_pick']]
passed()
# The Add to Playlist picker shares the page and keeps its text rows: their art hidden, the text from page.rows text_x, and none built for them.
m=RowPlaylistMachine(3,1); m.art={11:PL_ART(11)}; m.rebuild()
for name in LISTS:
    button=m.row_button(name); info=next(k for k in m.nodes[button]['children'] if m.nodes[k]['name']=='view_info')
    assert not m.nodes[pl_art_of(m,button)]['visible'] and (m.get(info+O['W_X']),m.get(info+O['W_W']))==(OLED_T['page_x'],289)
assert not m.asks and not m.refreshes; passed()

# Stock's add mode is one global, left by a picker however it closes: a page coming back rebuilds as what it opened as.
m=RowPlaylistMachine(3,0); kids=m.rebuild(); home=m.top; shown=len(kids)
picker=m.node('window','playlist_page',[m.node('scroll_view','scroll_view')]); m.word(m.nodes[picker]['children'][0]+O['W_PARENT'],picker)
m.word(m.mode,1); m.top,m.view=picker,m.nodes[picker]['children'][0]
m.nav=m.node('view','view_navbar',visible=0); m.nodes[picker]['children'].insert(0,m.nav)
kids=m.rebuild(); assert m.get(m.mode)==1 and len(kids)==4
m.top,m.view=home,next(c for c in m.nodes[home]['children'] if m.nodes[c]['name']=='scroll_view')  # past OLED's note (feedback_empty)
m.nav=m.nodes[home]['children'][0]
kids=m.rebuild(); assert m.get(m.mode)==0 and len(kids)==shown; passed()

# Playlist View: Cover Flow (Display, PLAYLISTCF): the page opens unseen (opacity 0, no slide) and its playlists go to
# Cover Flow on the next pass (coverflow_playlists, test/coverflow.py), the page shown under them for their Return; once
# per page, so coming back to it shows the list. Library's row and Home's list open it through this
# rebuild. Off, the Add to Playlist picker, no playlists, or the page gone or covered first: the list, as before. The
# covers are the playlists' art.
PL_HINT='htranslate(duration=200)'
def pl_cf(base,count=2,mode=0,on='1',**kw):
    class M(base):
        def hook(self,u,address,size,unused):
            if address!=payload_syms['coverflow_playlists']: return super().hook(u,address,size,unused)
            self.handed.append(self.names(u.reg_read(REGS[0]))); self.returns(1)
    m=M(count,mode,**kw)
    m.handlers[syms['widget_set_prop_str']]='widget_set_prop_str'; m.handed=[]; m.config['PLAYLISTCF']=on
    m.nodes[m.top]['anim_hint']=PL_HINT; m.byte(m.top+0x34,0xff)
    return m
pl_veiled=lambda m: m.u.mem_read(m.top+0x34,1)[0]==0 and m.nodes[m.top]['anim_hint']==''
pl_shown=lambda m: m.u.mem_read(m.top+0x34,1)[0]==0xff and m.nodes[m.top]['anim_hint']==PL_HINT
m=pl_cf(RowPlaylistMachine); m.rebuild()
assert pl_veiled(m) and not m.handed and m.refreshes==1; m.advance(0,clear=False)  # one read of the songs, rows and covers
assert m.asks  # the rows' art
assert m.handed==[['List 0','List 1']] and pl_shown(m)
m.rebuild(); m.advance(0,clear=False); assert len(m.handed)==1 and pl_shown(m); passed()  # back on the page: the list
for count,mode,on in ((2,0,'0'),(2,1,'1'),(2,2,'1'),(0,0,'1')):
    m=pl_cf(RowPlaylistMachine,count,mode,on); m.rebuild(); m.advance(0,clear=False)
    assert not m.handed and pl_shown(m),(count,mode,on); passed()
# ... or another window over it by the next pass: no covers, the page shown under it.
m=pl_cf(RowPlaylistMachine); m.rebuild(); page=m.top; m.top=m.node('window','other_page'); m.stack.append(m.top)
m.advance(0,clear=False); m.top=page; assert not m.handed and pl_shown(m); passed()
# Centre on a playlist under the covers (coverflow.c): a click at x 0 on its row on the Playlists page under
# the covers, which stay; its list opens over them, so Return comes back to them, as from an album. The row is found
# by the playlist's cd_num: the list may have been read again, reordered, since the covers copied it.
def pl_under(m):  # the covers over the page, reordered after they opened; the page
    lists=m.items(m.get(syms['p_deque_playlist'])); m.word(syms['p_deque_playlist'],m.deque(lists[::-1])); m.rebuild()
    page=m.top; m.top=m.node('window','coverflow_page'); m.stack.append(m.top)
    return page
m=pl_cf(RowPlaylistMachine,count=3,on='0'); m.rebuild(); page=pl_under(m); m.clicks=[]; xs=[]
m.on_click=lambda w,e,f=m.on_click: (xs.append(m.get(e+O['EVENT_X'])),f and f(w,e))
m.call(address=payload_syms['playlist_open'],args=(page,10,0,0),gap=0,clear=False)  # List 0, now the third row
assert [m.nodes[c]['name'] for c in m.clicks]==['2'] and xs==[0],m.clicks; passed()
# ... stock finds the row by the playlist's place in the live list, in name order, and the page's rows are the list as it
# was built: one past them (a playlist added since sorts in, a song's Add to Playlist → New under the covers, and pushes
# the last ones past) has no row there, and the covers close, for the page to build its rows again as it comes to the
# front. Appended last, the new one is that one.
opens=lambda m: [(c[0],m.text(c[1])) for c in m.calls if c[0]=='navigator_to_with_context']
class PlShut(RowPlaylistMachine):
    shut=0
    def hook(self,u,address,size,unused):
        if address!=payload_syms['coverflow_playlists_close']: return super().hook(u,address,size,unused)
        self.shut+=1; self.returns(0)
m=pl_cf(PlShut,count=3,on='0'); m.rebuild(); page=pl_under(m); m.calls.clear()
made=m.song('Made',4); m.word(made+O['REC_TITLE'],m.string('Made')); m.word(made+O['REC_DISC'],13)
m.word(syms['p_deque_playlist'],m.deque(m.items(m.get(syms['p_deque_playlist']))+[made]))
m.call(address=payload_syms['playlist_open'],args=(page,13,0,0),gap=0,clear=False); m.advance(0,clear=False)
assert m.shut==1 and not opens(m),(m.shut,opens(m)); passed()
# Short press toggles once; a hold opens one menu titled by its row, its release is swallowed and
# the next short press toggles again. A repeated long event of the same press opens nothing.
m=QueueMachine(); page=m.top
m.press(5000); assert m.release()==1
m.press(6000); assert m.hold()==11 and m.nodes[m.top]['name']=='sortselect_dialog'
assert {c[1:] for c in m.calls if c[0]=='widget_off_by_func'}=={(m.top,O['EVT_KEY_UP'],O['SORTSELECT_KEYUP']),(m.dlg_return,O['EVT_CLICK'],O['SORTSELECT_CLOSE'])}
assert m.nodes[m.title]['text']=='Row 0' and m.labels()==SONG_MENU,(m.nodes[m.title]['text'],m.labels())
assert m.hold()==0 and not any(c[0]=='navigator_to' for c in m.calls) and len(m.stack)==2
assert m.release()==0
m.press(6500); assert m.release()==1; passed()
# Return (the replaced dialog key-up) dismisses without the stock sort flag; other keys pass.
f,ctx=m.handler(m.top,O['EVT_KEY_UP']); ev=m.alloc(0x40); m.word(ev,O['EVT_KEY_UP']); m.word(ev+O['EVENT_KEY'],O['KEY_NEXT'])
assert m.call(address=f,args=(ctx,ev,0,0),gap=0)==0 and m.top!=page
m.word(ev+O['EVENT_KEY'],O['KEY_RETURN']); assert m.call(address=f,args=(ctx,ev,0,0),gap=0)==11
assert m.top==page and not m.toasts and m.names()==['A','B','C'] and m.u.mem_read(syms['g_sort_changeflag'],1)==b'\0'
passed()
# A dropped release (AWTK aborts keys when windows change) leaves a latch that must not eat a later
# press: the press time differs.
m=QueueMachine(); m.press(1); assert m.hold()==11
m.u.mem_write(m.status+O['INPUT_KEYS'],bytes(O['INPUT_KEY_SIZE'])); m.press(2); assert m.release()==1; passed()
# Play Last ends Playing Next, empty in these machines, so under an ordered stock mode (List or Repeat All) its
# songs land just after the one playing (MCL_POS, playback_queue_insert), not at the queue's end.
def played_last(m,add,queue=('A','B','C')):
    pos=m.mcl('MCL_POS'); assert m.mcl('MCL_MODE') in (0,3),m.mcl('MCL_MODE')
    return [*queue[:pos+1],*add,*queue[pos+1:]]
# The hold cancels a pending centre click. Wheel and centre then act on the menu only and the list
# keeps its selection. Touch and centre arrive as the same click and run once.
m=QueueMachine(); m.call(); m.call(); assert m.selected(m.surface)==2
m.call(O['KEY_CENTER']); m.press(100); m.hold(); m.release(); m.advance(300); assert not m.dispatched()
m.paint(m.view); assert m.call()==11 and m.selected(m.view)==1 and m.selected(m.surface)==2
assert m.confirm()==11 and m.dispatched()[0][1]==m.nodes[m.view]['children'][1]
m.pick(1); m.pick(1); m.advance(0)
assert m.names()==played_last(m,['Row 2']) and [t[3] for t in m.toasts]==['Playing Last'],m.names()  # once, said once
assert m.top==page and m.selected(m.surface)==2 and not m.playback(); passed()
# Play next lands after the playing track, on the logical row of a recycled table. Later tracks,
# the shuffle pool and the previous index shift; duplicates stay; playback is never touched.
m=QueueMachine(); m.word(O['MCL_LASTPOS'],2)
assert m.run(0,steps=4)=='Playing Next' and m.names()==['A','Row 4','B','C']
assert m.pool()==[0,2,3,1] and m.mcl('MCL_LASTPOS')==3 and m.mcl('MCL_POS')==0
m.run(0); assert m.names()==['A','Row 4','Row 4','B','C'] and not m.playback(); passed()
# An artist's albums use the stock detail queries (Albums, below Coverflow's); a folder row loads
# like folder_enter, then its subfolders in listing order, dot folders skipped. Only songs join, in
# order, and the staging deque is restored.
for cls,artist_type,page,query in ((0xff01,1,'album_page',('getMusicByAlbumAndAlbumSonger','Album','Artist')),
        (0xff01,0,'localclass_page',('getMusicByAlbumAndSonger','Album','Artist')),
        (0xf001,0,'folder_page',('toolsLoadDirectory','/mnt/sd/Music/Row 0/dir',None))):
    m=QueueMachine(page=page,cls=cls,pos=2); m.word(syms['g_artist_type'],artist_type)
    if page=='folder_page':
        m.word(m.row(0)+O['REC_TYPE'],4); m.listings['/mnt/sd/Music/Row 0/dir']=[m.song('T3'),m.song('.hidden',4)]
    want=['A','B','C','T1','T3','T2'] if page=='folder_page' else ['A','B','C','T1','T2']
    assert m.run(1)=='Playing Last' and m.names()==want and m.query[:3]==query, m.query
    assert page!='folder_page' or m.loaded_dirs==['/mnt/sd/Music/Row 0','/mnt/sd/Music/Row 0/dir']  # the toast's call reuses their stack
    assert m.names(m.get(syms['tools_pdeq_directory']))==['staged'] and m.pool()==list(range(len(want)))
    passed()
# Videos never enter an audio queue, including through a folder's hold menu.
for action in (0,1):
    m=QueueMachine(page='folder_page'); m.word(m.row(0)+O['REC_TYPE'],4)
    m.found=[m.song('film.mp4'),m.song('song.flac'),m.song('film.MKV')]
    assert m.run(action)==('Playing Next','Playing Last')[action]
    assert m.names()==(['A','song.flac','B','C'] if action==0 else played_last(m,['song.flac'])); passed()
m=QueueMachine(page='folder_page'); m.word(m.row(0)+O['REC_PATH'],m.string('/mnt/mmc/Videos/film.mp4'))
m.press(100); m.hold(); assert m.nodes[m.top]['name']=='folder_page' and m.names()==['A','B','C']; passed()
m=QueueMachine(page='folder_page'); m.word(m.row(0)+O['REC_TYPE'],4)
m.word(m.row(0)+O['REC_PATH'],m.string('/mnt/mmc/Videos/Album.mp4'))
m.press(100); assert m.hold()==11 and m.nodes[m.top]['name']=='sortselect_dialog'; passed()
# Artist, composer and genre rows, and their album lists, use batch_add_file's per-class queries.
for cls,artist_type,query in ((0xf004,0,('getMusicBySonger','Artist',None)),(0xf004,1,('getMusicByAlbumArtist','Artist',None)),
        (0xf005,0,('getMusicByComposer','Comp',None)),(0xf006,0,('getMusicByGenre','Pop',None)),
        (0xff02,0,('getMusicByAlbumAndComposer','Album','Comp')),(0xff03,0,('getMusicByAlbumAndGenre','Album','Pop'))):
    m=QueueMachine(page='localclass_page',cls=cls,pos=2); m.word(syms['g_artist_type'],artist_type)
    m.word(m.row(0)+0x20,m.string('Comp')); m.word(m.row(0)+0x1c,m.string('Pop'))
    assert m.run(1)=='Playing Last' and m.names()==['A','B','C','T1','T2'] and m.query[:3]==query, m.query
    assert m.query[3]==0 or cls<0xff00; passed()
# Queue category expansion uses music scope; folder expansion still includes spoken tracks.
class ScopedQueueMachine(QueueMachine):
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name.startswith('q:getMusic') or name=='q:toolsLoadDirectory':
            scope=self.get(payload_syms['library_music'])
            assert scope==int(name!='q:toolsLoadDirectory')
            found=self.found
            if scope: self.found=found[:1]  # the SQL hook's music-only result, checked against SQLite below
            super().hook(u,address,size,unused)
            self.found=found
            return
        return super().hook(u,address,size,unused)
for cls in (0xf004,0xf005,0xf006,0xff01,0xff02,0xff03,1):
    for action in (0,1):
        m=ScopedQueueMachine(page='folder_page' if cls==1 else 'localclass_page',cls=cls)
        m.found=[m.song('Music'),m.song('Episode'),m.song('Book')]
        for r,path in zip(m.found,('/mnt/mmc/Music/song.flac','/mnt/mmc/pOdCaStS/episode.mp3','/mnt/mmc/elsewhere/BOOK.M4B')):
            m.word(r+O['REC_PATH'],m.string(path))
        m.word(m.row(0)+0x20,m.string('Comp')); m.word(m.row(0)+0x1c,m.string('Pop'))
        if cls==1: m.word(m.row(0)+O['REC_TYPE'],4)
        assert m.run(action)==('Playing Next','Playing Last')[action]
        added=['Music','Episode','Book'] if cls==1 else ['Music']
        assert m.names()==(['A']+added+['B','C'] if action==0 else played_last(m,added))
        assert m.get(payload_syms['library_music'])==0
        assert m.names(m.get(syms['tools_pdeq_directory']))==['staged']
passed()
# An Unknown row (id -1) queries the empty name; an artist's All songs row (-2) sets the flag.
m=QueueMachine(page='localclass_page',cls=0xf004); m.word(m.row(0)+O['REC_ID'],-1); m.run(1)
assert m.query[:2]==('getMusicBySonger',None); passed()
m=QueueMachine(page='localclass_page',cls=0xff01); m.word(m.row(0)+O['REC_ID'],-2); m.run(1)
assert m.query==('getMusicByAlbumAndSonger','Album','Artist',1); passed()
# Each row gets the menu that fits it. Songs: favourite as stock's heart, add to a playlist and go
# to album and artist; collections shuffle instead; folders have no favourite; Coverflow (below)
# has no playlist. Go to is left out where it would land on the page itself or a second instance.
def menu(m,setup=None):
    if setup: setup(m)
    m.press(5); assert m.hold()==11; t=m.nodes[m.title]['text']; got=m.labels(); m.release(); return t,got
GROUP=['Play Next','Play Last','Shuffle','Add to Playlist']
for kw,setup,want in (({},None,('Row 0',SONG_MENU)),
        ({},lambda m:m.favs.add('Row 0'),('Row 0',[*SONG_MENU[:4],'Unfavorite',*SONG_MENU[5:]])),
        ({},lambda m:m.open_pages.update({'artistinfo_page','playerjumpinfo_page'}),('Row 0',SONG_MENU[:5])),
        ({'cls':0xff10,'page':'album_page'},None,('Row 0',[*SONG_MENU[:5],'Go to Artist'])),
        ({},lambda m:m.word(m.row(0)+O['REC_ALBUM'],0),('Row 0',[*SONG_MENU[:5],'Go to Artist'])),
        ({'cls':0xf003,'page':'album_page'},None,('Album',[*GROUP[:3],'Favorite',GROUP[3],'Go to Artist'])),
        ({'cls':0xff01,'page':'album_page'},None,('Album',GROUP)),
        # An artist's tabs set classinfo +0, not g_class_type: Albums first, then Songs after an album.
        ({'cls':0xff07,'page':'artistinfo_page'},lambda m:m.word(syms['g_local_classinfo_save'],0xff01),('Album',GROUP)),
        ({'cls':0xff01,'page':'artistinfo_page'},lambda m:(m.word(syms['g_local_classinfo_save'],0xff07),m.open_pages.add('artistinfo_page')),('Row 0',SONG_MENU[:6])),
        ({'cls':0xf004,'page':'localclass_page'},None,('Artist',GROUP)),
        ({'cls':0xf006,'page':'localclass_page'},lambda m:m.word(m.row(0)+0x1c,m.string('Pop')),('Pop',GROUP)),
        ({'page':'folder_page','cls':1},lambda m:m.word(m.row(0)+O['REC_TYPE'],4),('Row 0',GROUP))):
    assert menu(QueueMachine(**kw),setup)==want, (kw,want); passed()
# Favourites: Add runs batch-select's Add to My Fav for the row alone, which tags a folder file;
# Remove deletes it, and on My Fav itself flags the list to reload as Now Playing's heart does.
m=QueueMachine(); assert m.run('Favorite',steps=3)=='Added to Favorites' and m.names()==['A','B','C']
assert sel(m)==[('batch_init_selectrecord',20),('batch_set_selectitem',3),('batch_add_file',0xf001)]
assert [c[2:] for c in m.calls if c[0]=='batch_add_file']==[(0xf00a,m.get(syms['p_deque_showlist']))]; passed()
for cls,flag in ((0xf001,0),(0xf00a,1)):
    m=QueueMachine(cls=cls); m.favs.add('Row 0'); assert m.run('Unfavorite')=='Removed from Favorites'
    assert [c[1] for c in m.calls if c[0]=='deleteMusicFromFav']==[m.row(0)] and m.u.mem_read(syms['g_delete_flag'],1)[0]==flag
    passed()
# Add to Playlist, a song's or an album's, opens the menu sheet's playlist picker (playlists.c
# playlist_pick_open) for the held row, not stock's playlist page; the queue is left alone.
class PickMachine(QueueMachine):
    def __init__(self,**kw):
        super().__init__(**kw); self.handlers[payload_syms['playlist_pick_open']]='pick:'; self.picked=[]
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'')!='pick:': return super().hook(u,address,size,unused)
        self.picked.append(self.text(self.get(u.reg_read(REGS[0])+O['REC_NAME']))); return self.returns(0)
for kw,row in (({},'Add to Playlist…'),({'cls':0xf003,'page':'album_page'},'Add to Playlist')):
    m=PickMachine(**kw); assert m.run(row,steps=1) is None and m.picked==['Row 1']
    assert not any(o[0]=='localmusic/playlist_page' for o in m.opened) and m.names()==['A','B','C']; passed()
# Shuffle plays the collection's songs from a random one with shuffle saved, as Shuffle Songs.
# Then the payload's Shuffle Songs takes the queue over (test/playback.py), so Up Next has an order.
m=QueueMachine(cls=0xf004,page='localclass_page'); m.handlers[payload_syms['playback_shuffle']]='stock_playback_shuffle'  # a payload call: no t9
assert m.run(2) is None
assert m.opened[-1][:2]==('playing_page',['T1','T2']) and m.opened[-1][2] in (0,1) and m.opened[-1][3:]==(0xf001,2) and [c[1:3] for c in m.calls if c[0]=='config_playmode']==[(2,1)]
assert [c[0] for c in m.calls].count('stock_playback_shuffle')==1; passed()
m=QueueMachine(cls=0xf004,page='localclass_page'); m.found=[]; assert m.run(2)=='Queue unchanged' and m.opened[-1][0]!='playing_page'; passed()
# The Queue menu's Shuffle drawing a long track that's playing isn't a pick of it: another song, the queue replaced as for
# any draw. Alone in the list it opens Now Playing, with no mode change, no load and no "Queue unchanged".
class KeepsMachine(QueueMachine):
    def __init__(self,**kw):
        super().__init__(**kw); self.handlers[payload_syms['playback_shuffle']]='stock_playback_shuffle'; self.handlers[payload_syms['playback_keeps']]='keeps:'; self.drawn=[]
        self.handlers[payload_syms['playback_np']]='keeps:np'; self.nps=0
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='keeps:': self.drawn.append(u.reg_read(REGS[1])); return self.returns(len(self.drawn)==1)
        if name=='keeps:np': self.nps+=1; return self.returns(0)
        return super().hook(u,address,size,unused)
m=KeepsMachine(cls=0xf004,page='localclass_page'); assert m.run(2) is None and m.opened[-1][0]=='playing_page' and m.opened[-1][2]!=m.drawn[0] and not m.nps
assert [c[1:3] for c in m.calls if c[0]=='config_playmode']==[(2,1)]; passed()
m=KeepsMachine(cls=0xf004,page='localclass_page'); m.found=m.found[:1]; assert m.run(2) is None and m.nps==1 and not [c for c in m.calls if c[0]=='config_playmode'] and m.opened[-1][0]!='playing_page'; passed()
# Go to album fills the album query as Now Playing's Album info and opens playerjumpinfo_page; Go to
# artist opens artistinfo_page with {class, record}. Closing either puts the browsing state back.
info=syms['g_local_classinfo_save']
for row,want in (('Go to Album','playerjumpinfo_page'),('Go to Artist','localmusic/artistinfo_page')):
    m=QueueMachine(); m.config['ARTISTCF']='0'; m.u.mem_write(info,bytes(range(256))*3+bytes(144)); before=bytes(m.u.mem_read(info,912))
    page=m.top; assert m.run(row,steps=2) is None and m.opened[-1][0]==want and m.top!=page
    if row=='Go to Album':
        assert m.get(info)==0xff10 and m.u.mem_read(info+9,1)==b'\0' and m.text(info+0xd)=='Album' and m.text(info+0x10d)=='Artist'
    else: assert m.opened[-1][1:]==(0xf001,m.row(2))
    f,ctx=m.handler(m.top,O['EVT_WINDOW_CLOSE']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
    assert bytes(m.u.mem_read(info,912))==before and m.names()==['A','B','C']; passed()
# An empty queue is filled by the stock loader without starting playback.
m=QueueMachine(queue=0); assert m.run(0)=='Playing Next' and m.names()==['Row 0'] and m.mcl('MCL_POS')==0
assert m.mcl('MCL_TYPE')==0xf001 and m.pool()==[0] and not m.playback(); passed()
# Gapless: a preload of pos+1 is closed exactly when the new tracks land there: Play Next always, Play Last under
# List with nothing in Playing Next (played_last), or at the queue's end under a mode that is not List or Repeat All.
for row,pos,mode,closed in ((0,0,0,True),(1,0,0,True),(1,0,1,False),(1,2,1,True)):
    m=QueueMachine(pos=pos,mode=mode); m.word(O['MCL_PRELOAD'],1); m.word(O['MCL_FD'],9); m.run(row)
    assert (m.sent==[(9,b'{mcl-closegapless\\null}',23,0)] and m.mcl('MCL_PRELOAD')==-1) if closed else (not m.sent and m.mcl('MCL_PRELOAD')==1)
    passed()
# Shuffle: the patched mclNextSong call runs the stock pick, then plays the Play next track once.
demo=(B/'demo').read_bytes()
assert struct.unpack_from('<I',demo,fileoff(demo,SHUFFLE_CALL[0]))[0]==jal(payload_syms['ringnav_shuffle'])
m=QueueMachine(mode=2); m.run(0,steps=5); assert m.names()==['A','Row 5','B','C']
# LDAC: stock switchBtPriority sends quality 0x16 for both HQ and Standard; patched HQ sends 0x15.
for patched,want in ((True,(0x15,0x16,0x18)),(False,(0x16,0x16,0x18))):
    bt=Machine(patched); bt.mock('btctl_set_codec_priority','btctl_set_ldac_quality','btctl_set_sbc_quality')
    for row,quality in enumerate(want):
        assert bt.call(address=syms['switchBtPriority'],args=(row,0,0,0),gap=0)==1
        assert [c[1] for c in bt.calls if c[0]=='btctl_set_ldac_quality']==[quality], (patched,row)
    passed()
# A boot reconnect can arrive as the control thread starts, before openBluetooth's GPIO write.
from build import BT_INIT, BT_PARAMS
assert struct.unpack_from('<I',demo,fileoff(demo,BT_INIT[0]))[0]==jal(payload_syms['headset_bt_init'])
assert struct.unpack_from('<I',demo,fileoff(demo,BT_PARAMS[0]))[0]==0
# The old radio-power hook is gone: startup owns registration, and stock retains its toggle path.
assert struct.unpack_from('<I',demo,fileoff(demo,0x4fa3dc))[0]==0x0320f809
