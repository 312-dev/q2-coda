class CoverflowMachine(QueueMachine):
    FREE=0x1000010  # stock free's GOT slot is 0 until lazy binding; give it a stub
    def __init__(self,albums=3,cached=True,**queue):
        super().__init__(rows=4,**queue)
        self.cached=cached; self.missing=False; self.joins=[]; self.threads=[]; self.plays=[]; self.homes=0; self.freed=0; self.live={}
        for n in ('window_create','widget_factory_create_widget','image_base_set_image','getAllAlbum','list_view_create',
                  'scroll_view_create','navigator_back_to_home','navigator_to_with_context','access@GLIBC_2.0',
                  'calloc@GLIBC_2.0','strdup@GLIBC_2.0','mkdir@GLIBC_2.0','statfs@GLIBC_2.0','pthread_create@GLIBC_2.2',
                  'pthread_join@GLIBC_2.0','qsort@GLIBC_2.0','toolsQueryDbTable'): self.handlers[syms[n]]='c:'+n
        self.word(0xa3a270,self.FREE); self.handlers[self.FREE]='c:free'
        self.handlers[home_hook[0]+12]='stock_home'
        for n,stub in WRITERS.items(): self.handlers[HOOKS[n][0]+12]=stub  # the writers' stock bodies
        self.albums=[self.song('T0') for _ in range(albums)]
        for i,r in enumerate(self.albums): self.word(r+O['REC_ALBUM'],self.string(f'Album {i}'))
        self.found=[self.song('T1'),self.song('T2')]
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('c:'): return super().hook(u,address,size,unused)
        assert u.reg_read(UC_MIPS_REG_T9)==address, name
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; sp=u.reg_read(UC_MIPS_REG_SP); ret=0
        self.calls.append((name,a,b,c))
        if name in ('window_create','widget_factory_create_widget','list_view_create','scroll_view_create'):
            kind,parent,h={'window_create':('window',0,0),'widget_factory_create_widget':(self.text(b),c,self.get(sp+24))}.get(
                name,(name.removesuffix('_create'),a,self.get(sp+16)))
            ret=self.node(kind)
            if kind=='window': self.top=ret; self.stack.append(ret); self.word(ret+O['W_W'],375); self.word(ret+O['W_H'],320)  # full-bleed (FULL_BLEED)
            else: self.nodes[parent]['children'].append(ret); self.word(ret+O['W_PARENT'],parent); self.word(ret+O['W_H'],h)
            if name=='widget_factory_create_widget':  # x in a3; y, w on the stack
                for off,v in (('W_X',d),('W_Y',self.get(sp+16)),('W_W',self.get(sp+20))): self.word(ret+O[off],v)
            if kind=='list_view': self.word(ret+O['W_W'],d)  # x, y and w in a1 to a3
            if kind=='slide_menu': self.word(ret+O['SLIDE_INDEX'],0); self.word(ret+0x5c,self.alloc())
        elif name=='image_base_set_image': self.nodes[a]['image']=self.text(b)
        elif name in ('toolsGetMusicInfo','stock_music_info') and getattr(self,'music_tags',None) is not None:  # a card song's file (through ringnav_music_info)
            t=self.music_tags.get(self.text(c)); self.calls.append(('toolsGetMusicInfo',self.text(c)))
            if t:
                for off,v in zip((0x280,0x100,0,0x580),t[:4]): u.mem_write(b+off,v.encode()+b'\0')
                self.word(b+0x684,t[4]); self.word(b+0x688,t[5])
            ret=int(bool(t))
        elif name=='getAllAlbum':
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in self.albums]; ret=len(self.albums)
        elif name=='navigator_back_to_home': self.homes+=1
        elif name=='navigator_to_with_context':
            if self.text(a)=='playing_page':
                self.play_names=self.names(self.get(b))
                self.play_recs=list(self.items(self.get(b)))  # its records, for their fields
                field=lambda e,k: self.text(self.get(e+O[k])) if self.get(e+O[k]) else ''
                self.play_tags=[(field(e,'REC_TITLE'),field(e,'REC_ARTIST'),field(e,'REC_ALBUM'),field(e,'REC_ALBUM_ARTIST'),
                                 self.get(e+O['REC_TRACK']),self.get(e+O['REC_DISC'])) for e in self.items(self.get(b))]
                self.plays.append((self.text(a),*[signed(self.get(b+4*i)) for i in range(4)])); self.natural=self.get(payload_syms['press_natural'])
                # stock_load: loads its queue at once, as stock's playing_page_init does (the real mclLoadPlayList)
                if getattr(self,'stock_load',0): self.nested(syms['mclLoadPlayList'],self.get(b),self.get(b+4),self.get(b+8),0)
            elif self.text(a)=='dialog/msginfo_dialog':
                self.toasts.append((self.text(a),self.get(b),self.get(b+4),self.text(b+8)))
                if self.on_toast: self.on_toast()  # what a nested poll under the toast's own loop would see
            else:
                self.opened.append((self.text(a),self.get(b),self.get(b+4))); self.top=self.node('window',self.text(a)); self.stack.append(self.top)
                if self.on_open: self.on_open(self.text(a))
        elif name=='access': path=self.text(a); ret=0 if (self.cached if '/mnt/mmc/.coverflow/' in path else not self.missing) else -1
        elif name=='toolsQueryDbTable':
            rows=self.albums if 'fileurl not like' in self.text(b) else []
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in rows]
            ret=len(rows)
        elif name=='qsort': self.qsort(a,b,c,d)  # sorted by the payload's comparator, run nested
        elif name=='calloc': ret=self.alloc(a*b+4); self.live[ret]=a*b
        elif name=='strdup': ret=self.string(self.text(a))
        elif name=='free':
            self.freed+=a!=0; self.live.pop(a,0)
            if a in getattr(self,'poison',{}): self.u.mem_write(a,b'\0'*self.poison.pop(a))
        elif name=='statfs': self.word(b+4,4096); self.word(b+28,1<<20 if getattr(self,'room',True) else 1024)  # 4 GB free, or 4 MB
        elif name=='pthread_create':  # an open's query step (coverflow.c step_turning) runs at once, as thread 78
            step=c==payload_syms['step_thread']; self.word(a,78 if step else 77)
            if step: self.nested(c,d)
            else: self.threads.append((c,d))
        elif name=='pthread_join':
            if a!=78: self.joins.append(a)  # the step's join is step_turning's own
        self.returns(ret,clobber=True)
    def open(self):
        """Home with its Cover Flow row selected: centre confirms its image, whose click opens Coverflow."""
        cf=HOME_ROWS.index('coverflow'); view,self.imgs=home_list(self); self.img=self.imgs[cf]; self.home=self.top; self.stack=[self.top]
        self.art=named(self,self.top,'img_homeart'); self.list=named(self,self.top,'list_view_home')
        assert self.call(address=home_hook[0],args=(self.top,0,0,0),gap=0)==0
        self.paint(view); self.nodes[view]['_ringnav_index']=cf
        self.clicks=[]; assert self.confirm()==11 and self.clicks==[self.img]
        f,ctx=self.handler(self.img,O['EVT_CLICK']); assert self.call(address=f,args=(ctx,self.event,0,0),gap=0)==0
        self.page=self.top; self.slide=self.find('slide_menu')
        return self.page
    def find(self,kind,w=None):
        w=w or self.page
        if self.nodes[w]['type']==kind: return w
        return next((f for c in self.nodes[w]['children'] if (f:=self.find(kind,c))),0)
    def texts(self,kind='hscroll_label'): return [self.nodes[w].get('text') for w in self.nodes if self.nodes[w]['type']==kind and self.alive(w)]
    def tracks(self,album=0):
        f,ctx=self.handler(self.nodes[self.slide]['children'][album],O['EVT_CLICK'])
        self.call(address=f,args=(ctx,self.event,0,0),gap=0); self.advance(0)
        view=self.find('scroll_view'); self.paint(view)
        return view
    def rescan(self,writer='scanAllMusicFile'):
        """A songtable writer runs: its hook, then its (mocked) stock body. A scan walks the folder
        markers into SCOPE_RULES first and indexes songtable after it, a delete doesn't."""
        assert self.call(address=HOOKS[writer][0],args=(1,2,0,0),gap=0)==0
        assert [c[:3] for c in self.calls if c[0].startswith('stock_')]==[(WRITERS[writer],1,2)]
        walked=[c for c in self.calls if c[0].startswith('fopen') and self.text(c[1])==SCOPE_RULES+'.tmp']
        assert bool(walked)==(writer!='deleteMusicFromMusicDb')
        body=[c[0] for c in self.calls].index(WRITERS[writer])
        indexed=[self.text(c[1]) for c in self.calls if c[0]=='toolsUpdateDbTable']
        assert indexed==[self.text(c[1]) for c in self.calls[body:] if c[0]=='toolsUpdateDbTable']
        assert indexed==([] if writer=='deleteMusicFromMusicDb' else ['/mnt/data/database.db'])*(
            2 if writer=='scanAllMusicFile' and getattr(self,'room',True) else 1)
        # a full scan with 8 MB free on /mnt/data adds the album grouping's and the Unknown checks' indexes,
        # before songtable_album, which SQLite then still takes for the album groupings
        sql=[self.text(c[2]) for c in self.calls if c[0]=='toolsUpdateDbTable']
        assert all('songtable_album on' in q and 'songtable_group' not in q for q in sql[-1:])
        assert [q.count('create index if not exists songtable_') for q in sql[:-1]]==[3]*(len(sql)-1)
        assert all(q.index('songtable_group')<q.index('songtable_album_exact')<q.index('songtable_artist') for q in sql[:-1])
    def queried(self): return sum(c[0]=='getAllAlbum' for c in self.calls)
    def close(self):
        f,ctx=self.handler(self.page,O['EVT_DESTROY'])
        self.call(address=f,args=(ctx,self.event,0,0),gap=0)
    def alive(self,w):
        """Still attached below the page (the destroy_children mock only unlinks)."""
        while w!=self.page:
            parent=self.get(w+O['W_PARENT'])
            if parent not in self.nodes or w not in self.nodes[parent]['children']: return False
            w=parent
        return True
    def key(self,k=O['KEY_RETURN'],run=True):
        f,ctx=self.handler(self.page,O['EVT_KEY_UP']); ev=self.alloc(0x40); self.word(ev,O['EVT_KEY_UP']); self.word(ev+O['EVENT_KEY'],k)
        ret=self.call(address=f,args=(ctx,ev,0,0),gap=0)
        if run: self.advance(0)
        return ret

# Position bar against OLED's shelf geometry in each half, dark and light.
def position_checks():
    for theme_id in ('3',):  # OLED's stored THEME
        for light in ('0','1'):
            def position_setup(machine): machine.config.update(THEME=theme_id,LIGHT=light,CFPOSITION='1')
            SETUP[:]=[position_setup]
            try: m=CoverflowMachine()
            finally: SETUP.clear()
            m.open()
            covers=m.get(m.slide+O['W_PARENT'])
            paint_position=lambda w: m.call(address=payload_syms['coverflow_position_paint'],args=(w,m.canvas,0,0),gap=0)
            paint_position(m.slide)
            xs=[]
            for index in (1,2,0):
                m.word(m.slide+O['SLIDE_INDEX'],index); m.bands=[]
                paint_position(m.slide); paint_position(covers)
                assert len(m.bands)==5,(theme_id,light,m.bands)
                track,*thumb=m.bands
                labels=[w for w in m.nodes[covers]['children'] if m.nodes[w]['type']=='hscroll_label']
                bottom=max(m.get(w+O['W_Y'])+m.get(w+O['W_H']) for w in labels)
                assert track[:4]==(107,(bottom+m.get(covers+O['W_H']))//2-1,160,2),(theme_id,track,bottom)
                xs.append(min(b[0] for b in thumb))
                assert len(thumb)==4 and [b[2:4] for b in thumb]==[(7,1),(9,1),(9,1),(7,1)]
                assert 2*track[1]+track[3] == thumb[0][1]+thumb[-1][1]+1
            assert xs[1]>xs[0]>xs[2],(theme_id,light,xs)
            passed()

position_checks()


# Local playlist editor: real MIPS UI/control code and real SQLite transactions at its ABI boundary.
class PlaylistEditMachine(CoverflowMachine):
    def __init__(self,count=3):
        super().__init__()
        self.db=sqlite3.connect(':memory:',isolation_level=None)
        self.db.execute('create table playlist(id integer primary key,cd_num,fileurl,time,time_end,filename)')
        self.db.execute("insert into playlist values(0,0,'',0,0,'Mix')")
        self.db.executemany('insert into playlist values(?,?,?,?,?,?)',[(101+i,0,'/p/'+x,0,0,x) for i,x in enumerate('ABC'[:count])])
        self.db.execute("insert into playlist values(200,1,'/p/other',0,0,'Other')")
        self.fail=''; self.sql=[]; self.closed_db=0
        for n in ('sqlite3_open','sqlite3_close','sqlite3_exec','sort_playlist_addtime','getAllMusic','checkPlayListExist','batch_add_file','navigator_back','_create_vector','vector_init','_vector_push_back'):
            self.handlers[syms[n]]='edit:'+n
        self.handlers[int(manifest['patch_symbols']['stock_playlistsmore_trampoline'],16)]='edit:more'
        r=self.song('Mix',4); self.word(r+O['REC_TITLE'],self.string('Mix')); self.word(r+O['REC_DISC'],0)
        self.word(syms['p_deque_playlist'],self.deque([r]))
        self.before_queue=self.names()
    def record(self,row):
        ident,dest,path,start,end,name=row
        r=self.song(name); self.word(r+O['REC_ID'],ident); self.word(r+O['REC_DISC'],dest)
        self.word(r+O['REC_PATH'],self.string(path)); self.word(r+O['REC_CUE_START'],start); self.word(r+O['REC_CUE_END'],end)
        return r
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('edit:'): return super().hook(u,address,size,unused)
        a,b,c,d=[u.reg_read(r) for r in REGS]; name=name[5:]; ret=0
        if name=='sqlite3_open': self.word(b,123); ret=1 if self.fail=='open' else 0
        elif name=='sqlite3_close': self.closed_db+=1
        elif name=='sqlite3_exec':
            assert a==123
            try:
                for sql in self.text(b).split(';'):
                    self.sql.append(sql)
                    if self.fail and sql.startswith(self.fail): raise sqlite3.OperationalError('injected write failure')
                    # The firmware's SQLite 3.19.3 refuses this; the host's (3.39 and later) runs it.
                    if re.search(r'\bhaving\b',sql,re.I) and not re.search(r'\bgroup\s+by\b',sql,re.I):
                        raise sqlite3.OperationalError('a GROUP BY clause is required before HAVING')
                    cur=self.db.execute(sql)
                    if cur.description and c:
                        for row in cur.fetchall():
                            values=self.alloc(4*len(row)); names=self.alloc(4*len(row))
                            for i,v in enumerate(row): self.word(values+4*i,0 if v is None else self.string(str(v)))
                            if self.nested(c,d,len(row),values,names): raise sqlite3.OperationalError('callback aborted')
            except sqlite3.Error: ret=1
        elif name=='_create_vector': assert self.text(a)=='int'; ret=self.alloc(64)
        elif name=='_vector_push_back': self.word(a,b)
        elif name=='vector_init': pass
        elif name=='getAllMusic':
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',r) for r in self.found]; ret=len(self.found)
        elif name=='sort_playlist_addtime':
            rows=self.db.execute('select * from playlist where id>100 and cd_num=? order by id',(a,)).fetchall()
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.record(row) for row in rows]; ret=len(rows)
        elif name=='checkPlayListExist':
            ret=self.db.execute('select count(*) from playlist where id>100 and cd_num=? and fileurl=? and time=?',
                                (b,self.text(self.get(a+O['REC_PATH'])),self.get(a+O['REC_CUE_START']))).fetchone()[0]
        elif name=='batch_add_file':
            assert (a,b)==(0xf001,0xf00b) and d!=self.get(syms['p_vector_select_record']) and self.get(d)==1
            dest=self.get(u.reg_read(UC_MIPS_REG_SP)+16); r=self.items(c)[0]
            if not self.fail:
                ident=self.db.execute('select max(id)+1 from playlist').fetchone()[0]
                self.db.execute('insert into playlist values(?,?,?,?,?,?)',(ident,dest,self.text(self.get(r+O['REC_PATH'])),
                                self.get(r+O['REC_CUE_START']),self.get(r+O['REC_CUE_END']),self.text(self.get(r+O['REC_NAME']))))
            ret=1
        elif name=='navigator_back':
            self.nested(*self.handler(self.page,O['EVT_DESTROY']),self.event)
            self.stack.remove(self.page); self.top=self.stack[-1]
        self.returns(ret,clobber=True)
    def open_editor(self,h=OLED_G.pitch,inner=OLED_G.body):
        # Edit Playlist's row (playlists.c ringnav_playlistsmore) under OLED's grid: no toolbar, so the third row from the
        # top, h high; no settings icons (set_icon 0), so its icon hidden, the button the row's width, the label at text_x.
        more=self.node('window','playlistsmore_page'); self.word(more+O['W_PARENT'],self.wm)
        self.top=more; self.stack.append(more)
        deleted=self.node('hscroll_label',name='label_del')
        self.word(deleted+O['W_PARENT'],more); self.nodes[more]['children'].append(deleted)
        self.call(address=HOOKS['localmusic_playlistsmore_page_init'][0],args=(more,0,0,0),gap=0)
        assert self.nodes[deleted]['text']=='Delete Playlist'
        row=self.nodes[more]['children'][-1]; button=self.nodes[row]['children'][0]
        image,label=self.nodes[button]['children']
        assert OLED_G.set_icon==0
        assert [self.get(row+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[0,2*h,375,h]
        assert self.nodes[row]['style']=='s_listitem_black'
        assert [self.get(button+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[0,0,375,h]
        assert self.nodes[button]['style']=='s_btn_listitem'
        assert [self.get(image+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[10,0,52,inner] and not self.nodes[image]['visible']
        assert self.nodes[image]['image']=='rename' and self.nodes[image]['draw_type']==O['IMAGE_DRAW_CENTER']
        assert [self.get(label+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[OLED_G.text_x,0,253,inner]
        assert self.nodes[label]['style']=='s_scrlabel_white20l' and self.nodes[label]['text']=='Edit Playlist'
        f,ctx=self.handler(button,O['EVT_CLICK'])
        self.call(address=f,args=(ctx,self.event,0,0),gap=0)
        self.page=self.top; self.view=self.find('scroll_view'); self.paint(self.view)
        assert self.nodes[self.page]['name']=='playlistedit_page'
    def showlabels(self):
        self.view=self.find('scroll_view'); return self.labels()
    def ticks(self):  # rows holding stock's "select" check: songs already in the playlist
        return [any(self.nodes[c].get('image')=='select' for c in self.nodes[r]['children'])
                for r in self.nodes[self.view]['children']]
    def choose(self,i):
        self.view=self.find('scroll_view'); self.pick(i); self.advance(0)
        self.view=self.find('scroll_view')
    def move(self,key=O['KEY_FWD_BTN']):
        assert self.call(key,address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
        assert self.call(key,gap=0)==11
        self.view=self.find('scroll_view')
    def order(self,dest=0): return [r[0] for r in self.db.execute('select filename from playlist where id>100 and cd_num=? order by id',(dest,))]
    def mutate(self,a=101,b=102,dest=0):
        records=[self.record(self.db.execute('select * from playlist where id=?',(i,)).fetchone()) if i else 0 for i in (a,b)]
        return self.call(address=payload_syms['ple_mutate'],args=(dest,*records,0),gap=0)

# iPod: the wheel reaches Edit Playlist below stock's Rename and Delete, and Centre opens the editor.
m=PlaylistEditMachine(); more=m.node('window','playlistsmore_page'); m.word(more+O['W_PARENT'],m.wm)
m.word(more+O['W_W'],375); m.word(more+O['W_H'],290); m.top=more; m.stack.append(more)
for i,name in enumerate(('label_rename','label_del')):
    row=m.node('list_item',children=[m.entry(0)]); m.word(row+O['W_Y'],10+i*72); m.word(row+O['W_PARENT'],more)
    m.word(m.nodes[row]['children'][0]+O['W_PARENT'],row); m.nodes[more]['children'].append(row)
m.call(address=HOOKS['localmusic_playlistsmore_page_init'][0],args=(more,0,0,0),gap=0)
m.on_click=lambda w,e: m.nested(*m.handler(w,O['EVT_CLICK']),e)
assert m.call()==11 and m.call()==11 and m.selected(more)==2
assert m.confirm()==11 and m.nodes[m.top]['name']=='playlistedit_page'; passed()

m=PlaylistEditMachine(); m.open_editor()
# The wheel still selects rows and Centre opens the selected song's actions.
m.on_click=lambda w,e: m.nested(*m.handler(w,O['EVT_CLICK']),e)
assert m.call()==11 and m.selected(m.view)==3
assert m.confirm()==11 and m.showlabels()==['Move Up','Move Down','Remove from Playlist']
assert m.key()==11 and m.selected(m.find('scroll_view'))==3
m.close()
m=PlaylistEditMachine(); m.open_editor()
assert m.showlabels()==['Add Songs','Done','A','B','C']
assert m.nodes[m.nodes[m.page]['children'][0]]['text']=='Editing: Mix'
# A repeat and long press cannot move again, and releases never reach playback.
for key,want in ((O['KEY_FWD_BTN'],['B','A','C']),(O['KEY_FWD_BTN'],['B','C','A']),(O['KEY_BACK_BTN'],['B','A','C'])):
    assert m.call(key,address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
    for _ in range(2):
        assert m.call(key,address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
        assert m.call(key,address=HOOKS['on_wm_keylong_fun'][0],event_type=O['EVT_KEY_LONG'],gap=0)==11
    assert m.order()==want
    assert m.call(key,gap=0)==11
    m.view=m.find('scroll_view'); assert m.selected(m.view)==want.index('A')+2
assert m.names()==m.before_queue and not m.playback(); passed()
# Touch actions and confirmation (Remove on the confirmation card, its answer feedback.c runs once
# the card has closed: playlists.c ple_remove_answer); cancel and Back leave the entry intact.
def ple_answer(m,row): m.call(address=payload_syms['ple_remove_answer'],args=(0,row,0,0),gap=0,clear=False); m.advance(0); m.view=m.find('scroll_view')
m.choose(3); assert m.showlabels()==['Move Up','Move Down','Remove from Playlist']
m.choose(2); ple_answer(m,0)
assert m.order()==['B','A','C']; m.choose(3); m.choose(0); assert m.order()==['A','B','C']
m.choose(2); m.choose(1); assert m.order()==['B','A','C']
m.choose(3); m.choose(2); ple_answer(m,1); assert m.order()==['B','C'] and m.selected(m.view)==3
assert m.order(1)==['Other']; passed()
# The fixed destination picker stays open; failed writes never report success.
saved=m.alloc(16); m.word(saved,0x12345678); m.word(syms['p_vector_select_record'],saved)
m.choose(0); assert m.showlabels()==['Search','T1','T2']; m.choose(1)
assert m.get(syms['p_vector_select_record'])==saved and m.get(saved)==0x12345678
assert not any(c[0].startswith('batch_') for c in m.calls)
assert m.toasts[-1][3]=='Added to Playlist' and m.showlabels()==['Search','T1','T2']
m.choose(1); assert m.toasts[-1][3]=='Already in playlist'
m.fail='write'; m.choose(2); assert m.toasts[-1][3]=='Could not save playlist'; m.fail=''
assert m.ticks()==[False,True,False]  # ticked on add, not on a failed write
assert m.key()==11 and m.showlabels()==['Add Songs','Done','B','C','T1']
m.choose(1); assert m.top!=m.page and not m.get(payload_syms['ple'])
assert m.names()==m.before_queue; passed()
# Add Songs' Search row filters by tagged title, artist or album, case-insensitively; the query
# stays for adding several and an empty one restores the whole picker.
def ple_search(m,text,event='EVT_IM_ACTION'):
    m.choose(0)
    edit=m.find('edit'); assert m.nodes[edit]['name']=='search_edit' and m.nodes[edit]['focused']==1
    y=m.get(edit+O['W_Y'])-OLED_G.bar  # the full-bleed page's rows from the status row's foot (bar_foot)
    assert 0<=y and y+m.get(edit+O['W_H'])<=30 and not m.find('scroll_view')
    m.nodes[edit]['text']=text; f,ctx=m.handler(edit,O[event])
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0; m.advance(0)
    m.view=m.find('scroll_view')
m=PlaylistEditMachine(); m.word(m.found[0]+O['REC_ARTIST'],m.string('Beatles'))
m.word(m.found[1]+O['REC_ALBUM'],m.string('Zebra')); m.open_editor(); m.choose(0)
assert m.showlabels()==['Search','T1','T2']
ple_search(m,'T2'); assert m.showlabels()==['Search: T2','T2'] and not m.find('edit')
m.choose(0); assert m.nodes[m.find('edit')]['text']=='T2'  # prefilled for editing
m.nodes[m.find('edit')]['text']='beat'; f,ctx=m.handler(m.find('edit'),O['EVT_IM_ACTION'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0; m.advance(0); m.view=m.find('scroll_view')
assert m.showlabels()==['Search: beat','T1']
m.choose(1); assert m.toasts[-1][3]=='Added to Playlist' and m.showlabels()==['Search: beat','T1']
ple_search(m,'zeb'); assert m.showlabels()==['Search: zeb','T2']
ple_search(m,''); assert m.showlabels()==['Search','T1','T2'] and m.ticks()==[False,True,False]
ple_search(m,'zzz'); assert m.showlabels()==['Search: zzz'] and empty_titles(m)==['No Matches']  # compact, under Search
# Return from the box applies its text, and a filtered pick can close the page cleanly.
m.choose(0); m.nodes[m.find('edit')]['text']='t1'; assert m.key()==11
assert m.showlabels()==['Search: t1','T1']; m.close(); passed()
# Header creation uses the database IDs even when the displayed list is alphabetized.
class PlaylistLifecycleMachine(PlaylistEditMachine):
    def __init__(self):
        super().__init__()
        self.db.execute('drop table playlist')
        self.db.execute('create table playlist(id integer primary key,filename,fileurl,album,songer,style,name,composer,albumsonger,year,track,cd_num,time,time_end,time_create,Initial,bitdepth,samprate)')
        for ident,name in enumerate(('Zulu','Alpha','Echo','Bravo','Delta','Charlie')):
            self.db.execute('insert into playlist(id,filename,name,cd_num) values(?,?,?,?)',(ident,name,name,ident))
        self.db.execute("insert into playlist(id,filename,cd_num) values(101,'song',0),(102,'other',1)")
        records=[]
        for ident,name in self.db.execute('select id,name from playlist where id<=100 order by name'):
            r=self.song(name,4); self.word(r+O['REC_ID'],ident); self.word(r+O['REC_DISC'],ident); records.append(r)
        self.word(syms['p_deque_playlist'],self.deque(records))
        self.more=self.node('window','playlistsmore_page'); self.nodes[self.more]['_playlist']=5
        self.answer=10; self.backs=0
        self.handlers[syms['navigator_back']]='life:back'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='life:back': self.backs+=1; self.returns(0); return
        if name=='c:navigator_to_with_context' and self.text(u.reg_read(REGS[0]))=='dialog/confirminfo_dialog':
            assert self.text(u.reg_read(REGS[1])+8)=='Delete Playlist?'
            self.returns(self.answer); return
        super().hook(u,address,size,unused)
    def create(self,name):
        return signed(self.call(address=HOOKS['createPlayListItem'][0],args=(self.get(syms['p_deque_playlist']),self.string(name),0,0),gap=0))
    def delete(self):
        return self.call(address=HOOKS['playlist_delete_click'][0],args=(self.more,0,0,0),gap=0)
    def reload_pending(self):
        # Run stock's parent-show gate with the playlist category unchanged after More closes. Since V1.36 the page loads
        # its list again whatever the gate says (the jalr at 0x4b59ac); the gate's own branch only clears the flag.
        self.word(syms['g_class_type'],0xf00b); self.u.reg_write(UC_MIPS_REG_GP,0xa3aae0)
        reached=[]
        def stop(u,address,size,unused):
            if address in (0x4b59a0,0x4b59ac): reached.append(address)
            if address==0x4b59ac: u.emu_stop()
        hook=self.u.hook_add(UC_HOOK_CODE,stop)
        self.u.emu_start(0x4b5974,0x1000000,count=100)
        self.u.hook_del(hook)
        assert reached[-1]==0x4b59ac
        return reached[0]==0x4b59a0

m=PlaylistLifecycleMachine(); assert m.create("O'Brien")==6
assert m.db.execute('select id,filename,name,cd_num from playlist where id=6').fetchone()==(6,"O'Brien","O'Brien",6)
assert m.create("O'Brien")==-2 and not m.db.in_transaction; passed()
# A name stored cut to 63 bytes never matches the whole one, as in stock: an M3U import takes any
# result but -1 as the playlist's ID.
m=PlaylistLifecycleMachine(); assert m.create('L'*70)==6 and m.create('L'*70)==7; passed()
for failure in ('open','begin','insert','commit'):
    m=PlaylistLifecycleMachine(); before=m.db.execute('select * from playlist').fetchall(); m.fail=failure
    assert m.create('New')==-1
    assert m.db.execute('select * from playlist').fetchall()==before and not m.db.in_transaction; passed()
m=PlaylistLifecycleMachine(); m.db.executemany('insert into playlist(id) values(?)',[(i,) for i in range(6,100)])
assert m.create('Full')==-1; passed()
for failure in ('','open','begin','delete','delete from playlist where id>100','commit'):
    m=PlaylistLifecycleMachine(); before=m.db.execute('select * from playlist').fetchall(); m.fail=failure; m.delete()
    if failure:
        assert m.db.execute('select * from playlist').fetchall()==before and m.backs==0
        assert m.toasts[-1][3]=='Could not delete playlist'
    else:
        assert m.db.execute('select id from playlist where id=0 or id=101').fetchall()==[]
        assert m.db.execute('select id from playlist where id=1 or id=102').fetchall()==[(1,),(102,)]
        assert m.backs==1 and bytes(m.u.mem_read(syms['g_delete_flag'],1))==b'\x01' and m.toasts[-1][3]=='Playlist deleted'
    assert m.reload_pending()==(not failure)
    assert not m.db.in_transaction; passed()
m=PlaylistLifecycleMachine(); m.db.execute('delete from playlist where id=101'); m.delete()
assert not m.db.execute('select id from playlist where id=0').fetchall() and m.toasts[-1][3]=='Playlist deleted'
assert m.create('Replacement')==0; passed()
m=PlaylistLifecycleMachine(); m.db.execute('delete from playlist where id=0'); m.delete()
assert m.db.execute('select id from playlist where id=101').fetchall()==[(101,)] and m.toasts[-1][3]=='Could not delete playlist'; passed()
m=PlaylistLifecycleMachine(); m.answer=0; before=m.db.execute('select * from playlist').fetchall(); m.delete()
assert m.db.execute('select * from playlist').fetchall()==before and not m.backs and not m.reload_pending(); passed()

# Holding Play/Pause on a playlist row opens its menu sheet: Play, Shuffle, Rename and Delete (hold_menu.c's
# playlist). Play plays its songs (getMusicByPlayList of its cd_num, spoken ones and folders left out as its
# opened list shows them) as its own list (0xff13). The Add to Playlist picker shares playlist_page's name and
# keeps stock's hold. The playlist is the index in the dots' name: row positions also count Import/Export.
def playlist_hold(steps=2):
    m=QueueMachine(page='playlist_page',rows=3); m.word(O['PLAYLIST_ADD_MODE'],0)
    for i,e in enumerate(m.nodes[r]['children'][0] for r in m.nodes[m.surface]['children']):
        if i: dots=m.node('image',f'img_more{i-1}'); m.word(dots+O['W_PARENT'],e); m.nodes[e]['children'].append(dots)
    lists=[m.song(f'List {i}',4) for i in range(2)]
    for i,r in enumerate(lists): m.word(r+O['REC_DISC'],7+i)
    m.word(syms['p_deque_playlist'],m.deque(lists)); m.found.append(m.song('Book.m4b'))
    m.word(m.surface+O['W_H'],144); m.paint(m.surface)  # every row on screen: the dots stay on their rows
    for _ in range(steps): m.call(); m.paint(m.surface)
    return m
m=playlist_hold(0); m.press(7000); assert m.hold()!=11 and not m.opened  # Import: no dots, stock keeps the hold
m=playlist_hold(); m.press(7100); assert m.hold()==11 and m.release()==0  # the locked hold's release never toggles playback
assert m.labels()==['Play','Shuffle','Rename','Delete'] and m.nodes[m.title]['text']=='List 1'; passed()
m=playlist_hold(); assert m.run('Play') is None and m.query[:2]==('getMusicByPlayList',8)
assert m.opened[-1]==('playing_page',['T1','T2'],0,0xff13,2) and m.get(syms['g_local_classinfo_save']+4)==8; passed()
m=playlist_hold(1); m.found=[m.song('dir',4)]; assert m.run('Play')=='Queue unchanged' and m.names()==['A','B','C']; passed()
m=playlist_hold(1); m.nodes[m.top]['_pl_mode']=1; m.press(7300)  # the page's own mode, not the global
assert m.hold()!=11 and not [o for o in m.opened if o[0]=='localmusic/playlistsmore_page']; passed()
for count in (0,1):
    m=PlaylistEditMachine(count); m.open_editor(); before=m.order()
    m.move(O['KEY_BACK_BTN']); m.move(); assert m.order()==before
    assert m.key()==11 and not m.get(payload_syms['ple']); passed()
# Roll back failed statements/commits, stale IDs, wrong playlist, CUE/path changes and ignored writes.
for failure in ('open','begin','update','commit'):
    m=PlaylistEditMachine(); m.fail=failure
    assert m.mutate()==0 and m.order()==list('ABC') and not m.db.in_transaction
    assert m.closed_db==1; passed()
m=PlaylistEditMachine(); m.db.execute("create trigger no_move before update on playlist begin select raise(ignore); end")
assert m.mutate()==0 and m.order()==list('ABC'); passed()
for change in ("delete from playlist where id=101", "update playlist set cd_num=1 where id=101",
               "update playlist set fileurl='/new' where id=101", "update playlist set time=30 where id=101"):
    m=PlaylistEditMachine(); a=m.record(m.db.execute('select * from playlist where id=101').fetchone()); b=m.record(m.db.execute('select * from playlist where id=102').fetchone())
    m.db.execute(change); before=m.db.execute('select * from playlist order by id').fetchall()
    assert m.call(address=payload_syms['ple_mutate'],args=(0,a,b,0),gap=0)==0
    assert m.db.execute('select * from playlist order by id').fetchall()==before; passed()
# Duplicate paths and CUE segments are removed by entry ID; hidden records keep their positions.
m=PlaylistEditMachine(); m.db.execute("update playlist set fileurl='/same',time=id,time_end=id+1 where cd_num=0 and id>100")
m.db.execute("insert into playlist values(150,0,'/hidden',0,0,'Hidden')")
assert m.mutate(101,103)==1 and m.order()==['C','B','A','Hidden']
assert m.mutate(102,0)==1 and m.order()==['C','A','Hidden'] and m.order(1)==['Other']
# Reopening uses the database order and leaves the live queue unchanged.
m.open_editor(); assert m.showlabels()==['Add Songs','Done','C','A','Hidden'] and m.names()==m.before_queue
assert m.mutate(101,200,0)==0
m.db.execute('update playlist set id=2147483647 where id=200'); assert m.mutate(101,103)==0
passed()
# Back cancels a deferred confirmation before returning to the editor.
m=PlaylistEditMachine(); m.open_editor(); m.choose(2); m.choose(2)
m.pick(0); assert m.key()==11 and m.order()==list('ABC')
assert m.showlabels()==['Add Songs','Done','A','B','C']; passed()
# A side press during a deferred click cannot change the row that click refers to.
m.choose(2); assert m.key()==11
m.view=m.find('scroll_view')
m.pick(3); m.move(); m.advance(0)
assert m.order()==list('ABC') and m.showlabels()==['Move Up','Move Down','Remove from Playlist']; passed()
# Simultaneous side-button presses retain independent repeat/release latches.
m=PlaylistEditMachine(); m.open_editor()
for key in (O['KEY_FWD_BTN'],O['KEY_BACK_BTN']):
    assert m.call(key,address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
assert m.order()==list('ABC')
assert m.call(O['KEY_BACK_BTN'],gap=0)==11
assert m.call(O['KEY_FWD_BTN'],address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
assert m.order()==list('ABC') and m.call(O['KEY_FWD_BTN'],gap=0)==11; passed()
# Failed removal and identical occurrences still target one entry only.
for failure in ('delete','commit'):
    m=PlaylistEditMachine(); m.fail=failure
    assert m.mutate(101,0)==0 and m.order()==list('ABC') and not m.db.in_transaction; passed()
m=PlaylistEditMachine(); m.db.execute("update playlist set fileurl='/same',time=0,time_end=0 where id in (101,102)")
assert m.mutate(102,0)==1 and m.order()==['A','C']; passed()
# Native delivery is consumed too; closing during a press consumes its remaining release.
m=PlaylistEditMachine(); m.open_editor(); key=O['KEY_FWD_BTN']
assert m.call(key,address=HOOKS['widget_dispatch'][0],args=(m.view,m.event,0,0),event_type=O['EVT_KEY_LONG'],gap=0)==11
assert m.call(key,address=HOOKS['on_wm_keydown_before_fun'][0],event_type=O['EVT_KEY_DOWN_BEFORE'],gap=0)==11
m.close(); assert m.call(key,gap=0)==11
assert m.call(address=payload_syms['ple_key'],args=(key,3,0,0),gap=0)==0
assert not m.playback(); passed()
# Reopen an on-disk database, independently of the editor's record cache.
import tempfile
with tempfile.TemporaryDirectory() as directory:
    path=str(pathlib.Path(directory)/'database.db'); disk=sqlite3.connect(path)
    m.db.backup(disk); disk.close(); disk=sqlite3.connect(path)
    assert [r[0] for r in disk.execute('select filename from playlist where cd_num=0 and id>100 order by id')]==['B','A','C']
    disk.close()
passed()
# A success/error toast covering the editor also consumes media keys, without moving songs.
m=PlaylistEditMachine(); m.open_editor(); page=m.top; m.top=m.node('dialog','msginfo_dialog')
m.move(); assert m.order()==list('ABC') and not m.playback()
m.top=page; m.move(); assert m.order()==['B','A','C']; passed()
# A subsequent stock playback load takes the saved order; editing itself left the queue alone.
m=PlaylistEditMachine(); assert m.mutate()==1; m.open_editor()
assert m.names()==m.before_queue
songs=m.get(payload_syms['ple']+8); loads=m.get(payload_syms['playback_loads'])
m.call(address=syms['mclLoadPlayList'],args=(songs,0,0xff13,0),gap=0)
assert m.names()==['B','A','C'] and m.get(payload_syms['playback_loads'])==loads+1; passed()  # a card app's stream counts loads
# A Videos folder's listed videos never join a queue (#128): stock loads a copy without them, its start
# on the same song, or the next after a video; the caller's list stays whole. Videos alone leave an empty queue.
for names,at,queued,pos in ((('a.mp3','v.mp4','b.flac','w.MKV'),2,['a.mp3','b.flac'],1),
                            (('a.mp3','v.mp4','b.flac'),1,['a.mp3','b.flac'],1),
                            (('v.mp4','a.mp3'),0,['a.mp3'],0),(('a.mp3','b.flac'),1,['a.mp3','b.flac'],1),
                            (('v.mp4','w.mkv'),1,[],0)):
    m=QueueMachine(); src=m.deque([m.song(n) for n in names])
    m.call(address=syms['mclLoadPlayList'],args=(src,at,1,0),gap=0)
    assert m.names()==queued and m.get(O['MCL_POS'])==pos and len(m.items(src))==len(names),(names,m.names(),m.get(O['MCL_POS']))
    passed()
# Explicit stock loading order also reaches export and later playback loads through the shared query hook.
m=LibraryQueryMachine(); m.db.execute('create table playlist(id,cd_num)')
m.db.executemany('insert into playlist values(?,?)',[(103,0),(101,0),(102,0),(100,0),(104,1)])
m.call(address=HOOKS['toolsQueryDbTable'][0],args=(m.string('/mnt/data/database.db'),m.string('select * from playlist where id > 100 and cd_num=0'),123,1),gap=0)
assert m.sql.endswith('order by id asc') and m.results==[(101,0),(102,0),(103,0)]; passed()

# Home in Split (CONFIG has no HOME here, so the Split default): the art itself follows the playing track, when Home or
# the status bar paints, in the right panel beside the Split list: the Coverflow thumbnail until the player has parsed
# this track, then the player's cover. Nothing loads or draws for Now Playing.
CONFIG.clear(); m=CoverflowMachine(); m.open(); m.top=m.home
bar=m.node('window','system_bar'); m.word(syms['system_bar'],bar)
for w in (bar,m.home): m.word(w+O['W_PARENT'],m.wm)
SPLIT,PX=OLED_G.split_row,OLED_G.split_art; PW,BAR=375-PX,OLED_G.bar; CH=320-BAR  # OLED's Split: the list's width, the art's pane, its client
def art_image(w):  # no time passes, so the cover's pan (DRIFT_*) holds its phase until advanced
    m.call(address=HOOKS['widget_on_paint_background'][0],args=(w,m.canvas,0,0),gap=0)
    return m.nodes[m.art].get('image')
m.byte(syms['g_playcover_type'],1)
assert art_image(m.home).startswith('file:///mnt/mmc/.coverflow/') and m.get(m.list+O['W_W'])==SPLIT
m.u.mem_write(syms['g_lastcover_url'],b'/p/A\0'); assert art_image(bar)=='file:///tmp/coverpic.jpg'
m.nodes[m.art]['image']='unchanged'; assert art_image(m.home)=='unchanged'  # same track and cover
np=m.node('window','playing_page'); m.word(np+O['W_PARENT'],m.wm); m.top=np
m.u.mem_write(syms['g_lastcover_url'],b'/p/B\0'); art_image(np); art_image(bar)
assert not [c for c in m.calls if c[0] in ('widget_load_image','canvas_draw_image')]; m.top=m.home
m.u.mem_write(syms['g_lastcover_url'],b'/p/A\0')
m.byte(syms['g_playcover_type'],3); assert art_image(m.home).startswith('file:///mnt/mmc/.coverflow/'); passed()
# A cover with no pixels to pan (here none decode; on the device a 16-bit decode or one too long
# for DRIFT_TEXELS) stays still: sized to its proportions, just covering the panel (native fill
# then draws it whole), centred, and clipped to the panel from the background hook to the
# border hook, so it crops evenly and never stretches.
assert m.nodes[m.art].get('sensitive')==0
def geometry(): return [signed(m.get(m.art+O[k])) for k in ('W_X','W_Y','W_W','W_H')]
def cover(w,h):
    m.image_size=(w,h)
    m.u.mem_write(syms['g_lastcover_url'],b'/p/other\0'); art_image(m.home)
    m.u.mem_write(syms['g_lastcover_url'],b'/p/A\0'); art_image(m.home)
    return geometry()
def fit(w=CH,h=CH): return [PX+int((PW-w)/2),BAR+int((CH-h)/2),w,h]  # Home's window is the screen (full-bleed)
for (w,h),want in (((300,300),fit()),((2*PW,2*CH),fit(PW)),((600,400),fit(CH*3//2)),((100,400),fit(PW,4*PW))):
    assert cover(w,h)==want,((w,h),geometry(),want)
passed()
m.clip=(0,0,375,320); m.word(m.canvas+O['CANVAS_X'],want[0]); m.word(m.canvas+O['CANVAS_Y'],want[1])
m.call(address=HOOKS['widget_on_paint_background'][0],args=(m.art,m.canvas,0,0),gap=0)
assert m.clip==(PX,0,PW,320),m.clip  # the pane, up under the status row (coverflow.c pane_y): the cover inside it
m.call(address=HOOKS['widget_on_paint_border'][0],args=(m.art,m.canvas,0,0),gap=0); assert m.clip==(0,0,375,320)
# The list paints only left of the panel, raised to screen row HOME_SPLIT_TOP (coverflow.c: 40, beside a pane that
# runs up under the bar) and its box run on to the window's foot (home.c home_overflow: OLED's Home rows past the rest
# fade into the ground), clipped at the screen's bottom; the placeholder (no size) fills the panel.
TOP=40; m.word(m.canvas+O['CANVAS_X'],0); m.word(m.canvas+O['CANVAS_Y'],TOP)
m.call(address=HOOKS['widget_on_paint_background'][0],args=(m.list,m.canvas,0,0),gap=0)
assert signed(m.get(m.list+O['W_Y']))==TOP and m.get(m.list+O['W_H'])==320-TOP and m.clip==(0,TOP,PX,320-TOP),m.clip  # short of the pane
m.call(address=HOOKS['widget_on_paint_border'][0],args=(m.list,m.canvas,0,0),gap=0,count=50_000_000); assert m.clip==(0,0,375,320)  # OLED's fades: past the call budget
m.image_size=None
m.u.mem_write(syms['g_lastcover_url'],b'/p/none\0')
art_image(m.home); assert m.nodes[m.art]['image']=='default_album_home' and geometry()==[PX,BAR,PW,CH]; passed()  # the screen's rows (Home is full-bleed)
# Neither a still cover nor the placeholder pans (the pan itself: after DepthMachine).
def drifts(): return [t for t in m.timers.values() if t[1]==payload_syms['home_drift']]
m.advance(O['DRIFT_TICK_MS']); art_image(m.home); assert not drifts()
assert cover(300,300)==fit(); art_image(m.home); m.advance(O['DRIFT_TICK_MS']); assert not drifts() and cover(300,300)==fit()
passed()
# Full: the art is hidden on the panel and never pans.
CONFIG.clear(); CONFIG.update(HOME='1'); m=CoverflowMachine(); m.open(); m.top=m.home; CONFIG.clear()
m.image_size=(300,300); m.byte(syms['g_playcover_type'],1); m.u.mem_write(syms['g_lastcover_url'],b'/p/A\0')
for _ in range(3): m.call(address=HOOKS['widget_on_paint_background'][0],args=(m.home,m.canvas,0,0))
assert not [t for t in m.timers.values() if t[1]==payload_syms['home_drift']] and not m.nodes[m.art]['visible']
assert [signed(m.get(m.art+O[k])) for k in ('W_X','W_Y','W_W','W_H')]==[PX,0,PW,320]; passed()  # hidden, on the pane's box (coverflow.c pane_y)

# Now Playing: stock init runs first, then the album and "-remaining" (slider max less value, in
# seconds) fill in, and no place in the queue ("2 of 3"): a label of that name stays as it was; later
# paints rewrite a label only when its source changed.
playing=HOOKS['playing_page_init'][0]
assert struct.unpack_from('<I',demo,fileoff(demo,playing))[0]==j(payload_syms['ringnav_playing'])
m=QueueMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'
for i,r in enumerate(m.items(m.get(syms['mcl_pdeqplaylist']))): m.word(r+O['REC_ALBUM'],m.string(f'Album {i}'))
bar=m.node('window','system_bar'); m.word(syms['system_bar'],bar)
pos,album,remain=(m.node('label',n) for n in ('label_ipod_pos','label_ipod_album','label_ipod_remain'))
slider=m.node('slider','slider_play',max=225,value=100)
win=m.node('window','playing_page',[m.node('view','view_buttons',[pos]),album,slider,remain])
for w in (bar,win): m.word(w+O['W_PARENT'],m.wm)
m.top=win
def shown(): return [m.nodes[w].get('text') for w in (pos,album,remain)]
def written(): return [m.nodes[c[1]]['name'] for c in m.calls if c[0]=='widget_set_text_utf8']
def repaint(w=win): m.call(address=HOOKS['widget_on_paint_background'][0],args=(w,m.canvas,0,0)); return written()
assert m.call(address=playing,args=(win,7,0,0),gap=0)==0 and m.calls[0][:3]==('stock_playing',win,7)
assert shown()==[None,'Album 1','-2:05']; passed()
assert repaint()==[] and repaint(bar)==[]; passed()
m.nodes[slider]['value']=101; assert repaint()==['label_ipod_remain'] and shown()[2]=='-2:04'; passed()
m.word(O['MCL_POS'],2); assert repaint(bar)==['label_ipod_album'] and shown()[:2]==[None,'Album 2']; passed()
# A rebuilt queue can reuse the same string address with new text; the text itself is hashed.
m.u.mem_write(m.get(m.items(m.get(syms['mcl_pdeqplaylist']))[2]+O['REC_ALBUM']),b'Other\0')
assert 'label_ipod_album' in repaint() and shown()[1]=='Other'; passed()
# Issue #7: once the player has parsed the playing file (g_play_id3_info starts with its path)
# the label shows the album it parsed, which a next folder's untagged records lack.
id3=syms['g_play_id3_info']; m.u.mem_write(id3+O['ID3_ALBUM'],b'Parsed\0')
m.u.mem_write(id3,b'/p/other\0'); assert repaint()==[] and shown()[1]=='Other'
m.u.mem_write(id3,b'/p/C\0'); assert repaint()==['label_ipod_album'] and shown()[1]=='Parsed'
m.u.mem_write(id3,b'\0'); assert repaint()==['label_ipod_album'] and shown()[1]=='Other'; passed()
for mx,v,want in ((3725,0,'-1:02:05'),(3600,0,'-1:00:00'),(3599,0,'-59:59'),(90,90,'-0:00'),(90,95,'-0:00')):
    m.nodes[slider].update(max=mx,value=v); repaint(); assert shown()[2]==want,(mx,v)
passed()
queue=m.deqs[m.get(syms['mcl_pdeqplaylist'])][1]
del queue[1:]; m.word(O['MCL_POS'],0); repaint(); assert shown()[:2]==[None,'Album 0']
queue.clear(); repaint(); assert shown()[:2]==[None,'']; passed()
# Another top window, or the page once destroyed, leaves the labels alone.
queue.append(m.copy('stSongInfo',m.song('X'))); m.top=m.node('window','home_page'); m.word(m.top+O['W_PARENT'],m.wm)
assert repaint(m.top)==[] and repaint(bar)==[]; m.top=win
f,ctx=m.handler(win,O['EVT_DESTROY']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
assert repaint()==[] and shown()[1]==''; passed()
# Now Playing's art follows the player's cover each lit pass (np_art_poll), whatever stock's
# one-shot flags did: the playing track's file once g_lastcover_url is its path, its cached
# bitmap dropped first, or stock's placeholder for a track with none; nothing for the cover init
# loaded, the same one again, one not yet this track's, or the screen off.
m=QueueMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'; m.handlers[sleep_hook+12]='stock_sleep'
cover=m.node('image','img_cover',image='play_defaultcover'); win=m.node('window','playing_page',[cover])
m.word(win+O['W_PARENT'],m.wm); m.top=win
def cover_is(path,kind): m.u.mem_write(syms['g_lastcover_url'],path+b'\0'); m.byte(syms['g_playcover_type'],kind)
def art_pass():
    m.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0)
    return [c[0] for c in m.calls if c[0] in ('image_manager_unload_bitmap_by_name','image_base_set_image')],m.nodes[cover].get('image')
reload=['image_manager_unload_bitmap_by_name','image_base_set_image']
cover_is(b'/p/B',1); m.call(address=playing,args=(win,7,0,0),gap=0); assert art_pass()==([],'play_defaultcover')
m.word(O['MCL_POS'],2); cover_is(b'/p/B',0); assert art_pass()==([],'play_defaultcover')
cover_is(b'/p/C',2); assert art_pass()==(reload,'file:///tmp/externpic.jpg') and art_pass()[0]==[]
m.word(O['MCL_POS'],1); cover_is(b'/p/B',1); assert art_pass()==(reload,'file:///tmp/coverpic.jpg')
m.word(O['MCL_POS'],2); cover_is(b'/p/C',3); assert art_pass()==(reload,'play_defaultcover')
cover_is(b'/p/C',4); m.byte(syms['g_backlight_status'],0); assert art_pass()[0]==[]
m.byte(syms['g_backlight_status'],1); assert art_pass()==(reload,'file:///tmp/externpic.jpg'); passed()

# Now Playing's swipe is its art and lyrics pages: ringnav_playing adds no page of its own.
m=QueueMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'
vslide=m.node('slide_view','slide_view',value=0); win=m.node('window','playing_page',[vslide])
for w,parent in ((vslide,win),(win,m.wm)): m.word(w+O['W_PARENT'],parent)
m.top=win; m.call(address=playing,args=(win,7,0,0),gap=0)
assert m.nodes[vslide]['children']==[]; passed()

# The full-screen art: stock init runs first; the song name and its shade show BC_MS, then fade
# out together over BC_STEPS steps. Closing it mid-fade stops the fade.
big=HOOKS['bigcover_page_init'][0]
assert struct.unpack_from('<I',demo,fileoff(demo,big))[0]==j(payload_syms['ringnav_bigcover'])
def bigcover():
    b=QueueMachine(queue=3,pos=1); b.handlers[big+12]='stock_bigcover'
    title,shade=b.node('hscroll_label','scrlabel_title'),b.node('view','view_shade')
    bw=b.node('window','bigcover_page',[shade,title])
    for w in (title,shade): b.byte(w+0x34,255) # the asset's opacity
    assert b.call(address=big,args=(bw,7,0,0),gap=0)==0 and b.calls[0][:3]==('stock_bigcover',bw,7)
    return b,bw,lambda: [b.get(w+0x34)&255 for w in (title,shade)]
b,bw,alpha=bigcover()
b.advance(O['BC_MS']-1); assert alpha()==[255,255] and len(b.timers)==1
steps=[]
for _ in range(O['BC_STEPS']):
    b.advance(O['BC_STEP_MS'] if steps else 1); steps.append(alpha()[0]); assert alpha()[0]==alpha()[1]
n=O['BC_STEPS']; assert steps==[255*(n-k)//n for k in range(1,n+1)] and not b.timers,steps; passed()
# A new song while it is open shows its name again and restarts the fade; the same song does not.
b.handlers[HOOKS['main_loop_sleep_default'][0]+12]='stock_sleep'
tick=lambda: b.call(address=HOOKS['main_loop_sleep_default'][0],args=(0x1234,0,0,0),gap=0,clear=False)
tick(); assert alpha()==[0,0] and not b.timers
b.word(O['MCL_POS'],2); tick(); assert alpha()==[255,255] and len(b.timers)==1
b.advance(O['BC_MS']-1); tick(); assert alpha()==[255,255] and len(b.timers)==1; passed()
b,bw,alpha=bigcover(); b.advance(O['BC_MS']+O['BC_STEP_MS'])
f,ctx=b.handler(bw,O['EVT_DESTROY']); b.call(address=f,args=(ctx,b.event,0,0),gap=0)
assert not b.timers; passed()
# bc_fit: a 16:9 cover takes the widest box clear of the glass, the name and shade on its foot.
# A player file counts once it is its type's and this track's, decoded past the image manager's
# cached copy; until then the art keeps the shape of g_lastcover_url's cover (a skip), or the
# square once that is another's (as Tidal, whose URL is never a queue path). Stock's placeholder
# is the square. An unchanged paint decodes and bc_mv nothing; a layout back to the square
# refits without a decode.
from ipod import BC_ART
b,bw,alpha=bigcover(); shade,title=b.nodes[bw]['children']; b.word(bw+O['W_PARENT'],b.wm)
img=b.node('image','img_bigcover'); b.nodes[bw]['children'].append(img); b.word(img+O['W_PARENT'],bw)
def put(w,*g):
    for k,v in zip(('W_X','W_Y','W_W','W_H'),g): b.word(w+O[k],v)
def geo(w): return [signed(b.get(w+O[k])) for k in ('W_X','W_Y','W_W','W_H')]
def bc_track(i): return b.text(b.get(b.items(b.get(syms['mcl_pdeqplaylist']))[i]+O['REC_PATH'])).encode()+b'\0'
sq=[(375-BC_ART)//2,(320-BC_ART)//2,BC_ART,BC_ART]
put(img,*sq); put(shade,sq[0],sq[1]+BC_ART-60,BC_ART,60); put(title,sq[0]+12,sq[1]+BC_ART-45,BC_ART-24,30)
b.nodes[img]['image']='file:///tmp/coverpic.jpg'; b.image_size=(1600,900)
bc_dec=['image_manager_unload_bitmap_by_name','widget_load_image']; bc_mv=['widget_move_resize']*3
def fit():
    b.call(address=HOOKS['widget_on_paint_background'][0],args=(bw,b.canvas,0,0),gap=0)
    return [c[0] for c in b.calls if c[0] in bc_dec+bc_mv]
b.byte(syms['g_playcover_type'],1); b.u.mem_write(syms['g_lastcover_url'],b'/p/other\0')
assert fit()==[] and geo(img)==sq  # the last track's cover file
b.u.mem_write(syms['g_lastcover_url'],bc_track(1)); assert fit()==bc_dec+bc_mv
x,y,w,h=geo(img); assert w>BC_ART and h==w*900//1600 and x==(375-w)//2 and y==(320-h)//2,geo(img)
assert geo(shade)==[x,y+h-60,w,60] and geo(title)==[x+12,y+h-45,w-24,30] and len(b.timers)==2
assert fit()==[]
put(img,*sq); assert fit()==bc_mv and geo(img)==[x,y,w,h]
b.word(O['MCL_POS'],2); assert fit()==[] and geo(img)==[x,y,w,h]  # a skip: still the last cover
b.byte(syms['g_playcover_type'],1); b.nodes[img]['image']='file:///tmp/externpic.jpg'
b.u.mem_write(syms['g_lastcover_url'],bc_track(2)); assert fit()==bc_mv and geo(img)==sq  # not type 1's file
b.image_size=(900,1600); b.byte(syms['g_playcover_type'],2); assert fit()==bc_dec+bc_mv
x,y,w,h=geo(img); assert h>BC_ART and w==h*900//1600 and x==(375-w)//2,geo(img)
b.nodes[img]['image']='file:///tmp/album_tidal.jpg'; b.u.mem_write(syms['g_lastcover_url'],b'\0')
assert fit()==bc_mv and geo(img)==sq  # Tidal
b.u.mem_write(syms['g_lastcover_url'],bc_track(2)); b.nodes[img]['image']='file:///tmp/externpic.jpg'; fit()
b.nodes[img]['image']='default_bigcover'; assert fit()==bc_mv and geo(img)==sq; passed()
# Volume: with the stock volume dialog directly over Now Playing, the dialog's own paint draws
# the band from the progress bar to the times (vol_paint): black, then the theme's bar (np_bar, playing.scrubber
# height) inset VOL_SPK_INSET at each end, its track (np_track_rgb) and a white fill to the volume, a speaker
# VOL_SPK_X in from each end of the progress bar and no number. Its slider and label hide, the slider moved
# onto the band so stock's invalidation repaints it. Over any other window it draws a compact panel.
OLED=theme_builtins()[0]; bh=OLED['np_bar']
m=QueueMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'
m.record_syscalls(); m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'],16)]='stock_ui_load'  # the first system_bar load
m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0],args=(m.string('system_bar'),0,0,0),gap=0)  # as on a unit (accent, theme_boot)
slider,elapsed,remain=m.node('slider','slider_play',max=225,value=100,bar_size=4),m.node('label','label_playtime'),m.node('label','label_ipod_remain')
win=m.node('window','playing_page',[slider,elapsed,remain]); m.word(win+O['W_PARENT'],m.wm); m.top=win
def put(w,*g):
    for k,v in zip(('W_X','W_Y','W_W','W_H'),g): m.word(w+O[k],v)
put(win,0,30,375,290); put(slider,24,240,327,30); put(elapsed,16,265,80,16)
m.word(syms['system_bar'],m.node('window','system_bar'))
assert m.call(address=playing,args=(win,7,0,0),gap=0)==0
sv,lv=m.node('slider','slider_vol',max=100,value=40),m.node('label','label_vol')
for w in (sv,lv): m.nodes[w]['visible']=0  # hidden by the asset before any paint
dlg=m.node('dialog','volume_dialog',[sv,lv]); m.word(dlg+O['W_PARENT'],m.wm); put(dlg,0,0,375,320)
def paint(w): m.call(address=HOOKS['widget_on_paint_background'][0],args=(w,m.canvas,0,0))
def did(n): return [c[1:] for c in m.calls if c[0]==n]
band=(0,270,375,30+265+16-270); lo=(24,270+(30-bh)//2,327,bh); bar=(lo[0]+O['VOL_SPK_INSET'],lo[1],lo[2]-2*O['VOL_SPK_INSET'],bh)
m.nodes[m.wm]={'children':[win,dlg]}; m.top=dlg; paint(dlg)
assert [m.nodes[w]['visible'] for w in (sv,lv,slider,elapsed)]==[0,0,1,1]
assert [signed(m.get(sv+O[k])) for k in ('W_X','W_Y','W_W','W_H')]==list(band)
assert m.nodes[slider]['bar_size']==bh and m.nodes[slider]['style:pressed:round_radius']==max(bh//2,4)  # stock's slider rounds from 4
assert [b[:5] for b in m.bands]==[(*band,color_t(0))],m.bands
assert [(r['rect'],r['radius'],r['color']) for r in m.rounded]==[(bar,bh//2,color_t(OLED['np_track_rgb'])),((*bar[:2],bar[2]*40//100,bh),bh//2,color_t(0xffffff))],m.rounded
cy=lo[1]+bh//2-O['VOL_SPK_PX']//2
assert not m.letters and m.image_loads==['oled_vol_lo_d','oled_vol_hi_d'] and [d[:2] for _,d in m.image_draws]==[(lo[0]+O['VOL_SPK_X']-O['VOL_SPK_PX']//2,cy),(lo[0]+lo[2]-O['VOL_SPK_X']-O['VOL_SPK_PX']//2,cy)],(m.image_loads,m.image_draws); passed()  # the dark half's speakers
vol=[t for t,v in m.timers.items() if v[1]==payload_syms['vol_poll']]; assert len(vol)==1; poll=vol[0]  # beside the shared loop's (ui_kit.c motion_loop: the status row's bars)
inv=lambda: [c[0] for c in did('widget_invalidate_force') if c[0]==dlg]  # the status row's bars repaint their own box
m.advance(O['VOL_POLL_MS']); assert not inv() and poll in m.timers  # unchanged: nothing
m.nodes[sv]['value']=41; m.advance(O['VOL_POLL_MS'])  # changed: the whole dialog repaints
assert inv()==[dlg]; passed()
m.nodes[sv]['value']=100; paint(dlg)
assert m.rounded[-1]['rect'][2]==bar[2] and not m.letters and len(m.timers)==1; passed()
m.nodes[m.wm]['children']=[win]; m.top=win; m.advance(O['VOL_POLL_MS']); assert not m.timers; passed()  # closed: it stops
# Over any other window (Quick Settings included) a rounded panel in the fast-scroll letter's
# style holds a pill bar on a grey track and the number, clear of the glass corners.
other=m.node('window','home_page'); m.word(other+O['W_PARENT'],m.wm); sv2,lv2=m.node('slider','slider_vol',max=100,value=7),m.node('label','label_vol')
dlg2=m.node('dialog','volume_dialog',[sv2,lv2]); m.word(dlg2+O['W_PARENT'],m.wm); put(dlg2,0,0,375,320)
m.nodes[m.wm]['children']=[win,other,dlg2]; m.top=dlg2; paint(dlg2)
X,Y,H,P,T=(O['VOL_PANEL_'+k] for k in ('X','Y','H','PAD','TEXT')); panel=(X,Y,375-2*X,H); bw=panel[2]-2*P-T
assert [m.nodes[w]['visible'] for w in (sv2,lv2)]==[0,0] and [signed(m.get(sv2+O[k])) for k in ('W_X','W_Y','W_W','W_H')]==list(panel)
BH=O['VOL_PANEL_BAR']; bar=(X+P,Y+(H-BH)//2,bw,BH)
assert [(r['rect'],r['radius'],r['color']) for r in m.rounded]==[(panel,O['LETTER_RADIUS'],(O['LETTER_ALPHA']<<24)|O['FILL_RGB']),
    (bar,BH//2,color_t(O['VOL_PANEL_TRACK'])),((*bar[:2],bw*7//100,BH),BH//2,color_t(0xffffff))],m.rounded
assert not m.bands and [(t['text'],t['rect'],t['font'][1]) for t in m.letters]==[('7',(X+P+bw,Y,T,H),O['VOL_PANEL_PX'])]; passed()
m.nodes[sv2]['value']=1; paint(dlg2)  # a sliver is still a round dot
assert m.rounded[2]['rect']==(*bar[:2],BH,BH),m.rounded; passed()
R=O['LETTER_RADIUS']; assert math.hypot(80-(X+R),Y+H-R-240)+R<=80  # its rounded corner clears the glass's

# Stock's PNG reader (png_load_file) calls png_read_png with PNG_TRANSFORM_EXPAND (0x10) and, from PNG_STRIP16,
# PNG_TRANSFORM_STRIP_16 (1) beside it: a 16-bit cover is decoded at 8 bits a channel, the stride the reader's rows
# assume, rather than as twice as many three-byte pixels in stripes (tools/build.py PNG_STRIP16).
from build import PNG_STRIP16
assert struct.unpack_from('<I',demo,fileoff(demo,PNG_STRIP16[0]))[0]==0x24060011; passed()
