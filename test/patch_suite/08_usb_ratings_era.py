class UsbMachine(FileMocks,MsgMachine):
    def __init__(self): super().__init__(); self.file_mocks('u:'); self.widget_timers=True
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('u:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; self.calls.append((name,a,b,c)); self.returns(self.file_op(u,name,a,b,c,d),clobber=True)
m,win,_,_,info,*_=usb_page(0,light='0',machine=UsbMachine)
said=lambda: {n.get('text') for n in m.nodes.values() if isinstance(n,dict) and n.get('visible',1)} & {'Connected','OK to Disconnect'}
for data in (None,b'',b'/dev/mmcblk0p1\n'):
    if data is not None: m.files[USB_LUN]=data
    m.advance(500); assert 'OK to Disconnect' not in said() and m.timers,data
m.files.pop(USB_LUN); m.advance(500); assert 'OK to Disconnect' not in said() and m.timers  # torn down after a path: no eject
m.files[USB_LUN]=b''; m.advance(500); assert not m.timers and [c[1] for c in m.calls if c[0]=='widget_invalidate_force']==[win]
m,*_=usb_page(1,light='0',machine=UsbMachine); assert not m.timers
passed()
class AlbumRateMachine(FileMocks,CoverflowMachine):
    def __init__(self,files=None,**kw): super().__init__(**kw); self.file_mocks('a:',files)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('a:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; self.calls.append((name,a,b,c))
        ret=self.file_op(u,name,a,b,c,d)
        self.returns(ret,clobber=True)
    def kept(self):
        data=self.files.get(ALBUM_RATES,b''); return sorted(v for v in struct.unpack(f'<{len(data)//4}I',data) if 0<v&7<6)  # 7: one taken out
    def aid(self,r): return self.call(address=payload_syms['album_id'],args=(r,0,0,0),gap=0)&0xffffffff
    def menu(self):  # the hold's rows, then Return closes the dialog
        self.t=getattr(self,'t',7000)+1; self.press(self.t); assert self.hold()==11 and self.release()==0
        rows=self.labels(); f,ctx=self.handler(self.dlg_return,O['EVT_CLICK']); self.call(O['KEY_RETURN'],address=f,args=(ctx,self.event,0,0),gap=0)
        return rows
m=AlbumRateMachine(); m.open(); m.word(m.slide+O['SLIDE_INDEX'],1); a1=m.aid(m.albums[1])
assert m.menu()==[*COVER_ALBUM,'Go to Artist'] and m.top==m.page; passed()
# A hold menu's rows act on the page under it: every row, either Light Mode half, Cover Flow's cover and Library's Albums.
def hold_pick(m,row):
    n=len(m.toasts); m.t=getattr(m,'t',7000)+1; m.press(m.t); assert m.hold()==11 and m.release()==0
    m.pick(row); m.advance(0)
    return m.toasts[-1][3] if len(m.toasts)>n else None
def light_half(machine,light,**kw):  # LIGHT stored before the constructor reads the config
    SETUP[:]=[lambda k:(k.config.update(LIGHT=light),k)]
    try: fm=machine(**kw)
    finally: SETUP.clear()
    fm.mock('strstr@GLIBC_2.0'); return fm
# An album's menu has no Rate Album (the rating page is the one place to rate); its Favorite likes it, the
# album's own table keeping 5 and no song's stars touched, in either Light Mode half.
for light in '01':
    fm=light_half(AlbumRateMachine,light); fm.open(); fm.word(fm.slide+O['SLIDE_INDEX'],1); a1=fm.aid(fm.albums[1])
    assert hold_pick(fm,3) is None and fm.kept()==[(a1&~7)|5] and '/mnt/data/ringnav-ratings' not in fm.files  # Favorite
    fm=light_half(AlbumRateMachine,light); fm.open(); fm.word(fm.slide+O['SLIDE_INDEX'],1)
    assert hold_pick(fm,0)=='Playing Next' and fm.top==fm.page and fm.query[:1]==('getMusicByAlbum',); passed()  # Play Next finds the cover's album
# A Play/Pause hold with no menu: stock has none, so its release plays or pauses.
def bare_hold(m,row=None):
    if row is not None: m.word(m.row(row)+O['REC_TYPE'],9)
    m.t=7001; m.press(m.t); return m.hold(),m.release()
assert bare_hold(QueueMachine(page='sysset_page'))==(0,1); passed()
assert bare_hold(QueueMachine(page='folder_page'),0)==(0,1); passed()  # a row no menu holds, as a playlist file in Folder
# Favorite, then Unfavorite: 5 hearts, then none; the other covers stay unrated.
def fav_calls(m,name): return [c for c in m.calls if c[0]==name]
assert m.run(3) is None and m.kept()==[(a1&~7)|5] and [c[2] for c in fav_calls(m,'batch_add_file')]==[0xf00a] and not fav_calls(m,'deleteMusicFromFav')
assert m.menu()[3]=='Unfavorite'
assert m.run(3) is None and m.kept()==[] and fav_calls(m,'deleteMusicFromFav') and not fav_calls(m,'batch_add_file')
m.word(m.slide+O['SLIDE_INDEX'],0); assert m.menu()[3]=='Favorite'
m.word(m.slide+O['SLIDE_INDEX'],1); passed()
# Kept across boots, apart from the songs' table.
assert m.run(3) is None
m2=AlbumRateMachine(files={ALBUM_RATES:m.files[ALBUM_RATES]}); m2.open(); m2.word(m2.slide+O['SLIDE_INDEX'],1)
assert m2.menu()[3]=='Unfavorite' and '/mnt/data/ringnav-ratings' not in m2.files; passed()
# A full table and a failed write report it, the album as it was.
full=b''.join(struct.pack('<I',(i+1)<<3|1) for i in range(((O['RATE_SLOTS'] - 2) // 4 * 3))).ljust(4*O['RATE_SLOTS'],b'\0')
m=AlbumRateMachine(files={ALBUM_RATES:full}); m.open(); m.word(m.slide+O['SLIDE_INDEX'],1)
m.budget=5_000_000  # the table's first use lays its ratings out at their places, once (a test's file: no layout mark)
assert m.run(3)=='Ratings are full' and m.files[ALBUM_RATES]==full; passed()
m=AlbumRateMachine(); m.open(); m.word(m.slide+O['SLIDE_INDEX'],1); m.fail_write=True
assert m.run(3)=='Rating not saved' and m.menu()[3]=='Favorite' and ALBUM_RATES not in m.files; passed()
# A malformed slot (6 or 7) reads as none; a truncated file reads empty.
for data in (struct.pack('<I',(a1&~7)|7).ljust(4*O['RATE_SLOTS'],b'\0'),b'\x05\0'):
    m=AlbumRateMachine(files={ALBUM_RATES:data}); m.open(); m.word(m.slide+O['SLIDE_INDEX'],1)
    m.budget=1_000_000  # a full-size file with no layout mark (none this firmware writes) is laid out again, once
    assert m.menu()[3]=='Favorite'; passed()
# An album is album_cmp's: its name and album artist, else its folder (disc folders as one), ASCII case aside but
# the folder's; the song artist never counts, so a compilation is one album. The Unknown card is one album.
def rec(album='Album',aa=None,path='/m/A/1.mp3',artist='Artist',rid=1):
    r=m.song('x'); m.word(r+O['REC_ID'],rid&0xffffffff); m.word(r+O['REC_PATH'],m.string(path)); m.word(r+O['REC_ARTIST'],m.string(artist))
    m.word(r+O['REC_ALBUM'],m.string(album) if album is not None else 0); m.word(r+O['REC_ALBUM_ARTIST'],m.string(aa) if aa else 0)
    return r
same=[(rec(aa='Band'),rec(album='ALBUM',aa='band',path='/x/2.mp3',artist='Other')),
      (rec(path='/m/A/CD1/1.mp3'),rec(path='/m/A/Disc 2/1.mp3')),
      (rec(path='/m/A/1.mp3',artist='One'),rec(path='/m/A/2.mp3',artist='Two')),
      (rec(album='',path='/m/A/1.mp3'),rec(album=None,path='/m/A/2.mp3')),
      (rec(rid=-1,album='Unknown Album'),rec(rid=-1,album=None,path='/z/9.mp3'))]
apart=[(rec(aa='Band'),rec(aa='Other')),(rec(path='/m/A/1.mp3'),rec(path='/m/B/1.mp3')),(rec(path='/m/A/1.mp3'),rec(path='/m/a/1.mp3')),
       (rec(),rec(album='Other')),(rec(aa='Band'),rec()),(rec(rid=-1),rec())]
assert all(m.aid(x)==m.aid(y)!=0 for x,y in same) and all(m.aid(x)!=m.aid(y) for x,y in apart); passed()
# A theme with an inplace motion (120 ms cubic_out here; OLED's has none, so its levels stay still): a sub-folder
# pushes stock's table in, and Back the folder above from the left. A click that queues a file and the Back that leaves
# the page don't move it, nor does a push still running throw it off.
def pushing(m,on=True):  # the inplace motion set (or OLED's none), x animators recorded
    if on: set_motion(m,'inplace',120,5)
    m.mock('widget_animator_prop_create','widget_animator_prop_set_params','widget_animator_start'); return m
class FolderPush(FolderClickMachine):  # stock's click and Back as their step of the layer
    step=0
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'')!='stock_folder_step': return super().hook(u,address,size,unused)
        self.byte(syms['g_folder_layer'],self.u.mem_read(syms['g_folder_layer'],1)[0]+self.step); self.returns(0,clobber=True)
def folder_push(on=True):
    m=FolderPush(['home_page','folder_page'],1,False); m.top=asset_tree(m,'folder_page.bin'); m.stack[-1]=m.top
    for n in ('click','back'): m.handlers[int(manifest['patch_symbols'][f'stock_folder_{n}_trampoline'],16)]='stock_folder_step'
    pushing(m,on); m.byte(syms['g_folder_layer'],3); m.table=named(m,m.top,'tableview_folder')
    w=m.node('button','row'); m.word(w+O['ROW_INDEX'],1); m.word(m.event+O['EVENT_TARGET'],w)
    return m,w
def folder_step(m,w,step,back=False):
    m.step=step; m.calls=[]
    if back: m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
    else: m.call(address=payload_syms['ringnav_folder_click'],args=(w,m.event,0,0),gap=0)
for on,want in ((False,[]),(True,[(375,0),(-375,0)])):
    m,w=folder_push(on); folder_step(m,w,0); assert m.x_pushes()==[]  # a file: queued, the layer as it was
    folder_step(m,w,1); assert m.x_pushes()==want[:1],(on,m.x_pushes()); folder_step(m,w,-1,True); assert m.x_pushes()==want,(on,m.x_pushes())
    assert on or not any(c[0]=='widget_lookup' for c in m.calls); passed()
m,w=folder_push(); m.byte(syms['g_folder_layer'],1); folder_step(m,w,-1,True); assert m.x_pushes()==[]; passed()
m,w=folder_push(); folder_step(m,w,1); m.advance(50); assert signed(m.get(m.table+O['W_X']))>0
folder_step(m,w,1); assert m.x_pushes()==[(375,0)]*2; m.advance(120); assert m.get(m.table+O['W_X'])==0; passed()
# The covers' tracks, which have no list under them, don't push.
m=pushing(CoverflowMachine()); m.open(); m.tracks(0); assert m.key()==11 and m.x_pushes()==[]; passed()
# Go to Album from a song played in Coverflow: one coverflow_page, on the album's tracks with no covers under them
# (the open one closes); Return goes back to where you were. An album with no tracks opens nothing.
m=CoverflowMachine(); old=m.open(); m.tracks(); m.mock('navigator_back','window_manager_close_window_force',prefix='c:')
assert m.call(address=payload_syms['coverflow_goto_album'],args=(m.albums[1],0,0,0),gap=0)==1
m.page=m.top; assert m.page!=old and [c[2] for c in m.calls if c[0]=='window_manager_close_window_force']==[old]
assert not m.find('slide_menu') and m.texts()[:3]==['Album 1','T1','T2']
assert m.call(address=payload_syms['coverflow_shows_album'],args=(m.albums[1],0,0,0),gap=0)==1
assert m.call(address=payload_syms['coverflow_shows_album'],args=(m.albums[0],0,0,0),gap=0)==0
f,ctx=m.handler(m.page,O['EVT_KEY_UP']); assert m.call(O['KEY_RETURN'],address=f,args=(ctx,m.event,0,0),gap=0)==11
assert [c[0] for c in m.calls].count('navigator_back')==1 and not m.find('slide_menu'); passed()
# The covers' caption is the album artist with Artists: Album Artist, when tagged (a compilation's "Various Artists" or
# its compiler), else the artist.
for kind,aa,want in ((1,'Various Artists','Various Artists'),(2,'Various Artists','Bobby Womack'),(1,'','Bobby Womack')):
    m=CoverflowMachine(); m.word(syms['artist_type'],kind)
    m.word(m.albums[0]+O['REC_ARTIST'],m.string('Bobby Womack')); m.word(m.albums[0]+O['REC_ALBUM_ARTIST'],m.string(aa))
    m.open(); assert m.texts()[:2]==['Album 0',want],m.texts(); passed()
m=CoverflowMachine(); m.found=[]; top=m.top
assert m.call(address=payload_syms['coverflow_goto_album'],args=(m.albums[0],0,0,0),gap=0)==0 and m.top==top; passed()
# Its hold menu row: Coverflow open, or Artist albums: Coverflow, goes there; else, or with no tracks, playerjumpinfo_page.
for cf_open,view,found,want in ((True,'0',True,'coverflow_page'),(False,'1',True,'coverflow_page'),(False,'0',True,'playerjumpinfo_page'),
                                (True,'1',False,'playerjumpinfo_page')):
    m=CoverflowMachine(cls=0xf001); m.config['ARTISTCF']=view
    if cf_open: m.open_pages.add('coverflow_page')
    if not found: m.found=[]
    page=m.top; assert m.run('Go to Album',steps=2) is None and m.top!=page
    assert m.nodes[m.top].get('name')==want or m.opened[-1][0]==want; passed()
# Library Albums rows like as covers do, their album split as Coverflow's.
m=AlbumRateMachine(page='album_page',cls=O['CLASS_ALBUMS']); m.page=m.top; a0=m.aid(m.row(0))
assert m.menu()==[*COVER_ALBUM,'Add to Playlist','Go to Artist']
assert m.run(3) is None and m.kept()==[(a0&~7)|5]; passed()
# Library > Years (Era's page): a row per decade with dated music, oldest first, its albums counted (an album's year its tracks'
# latest); undated albums, audiobooks, podcasts and M4B left out. A decade's page: Play All, Shuffle, then its
# albums by year, each "Artist • year".
class EraMachine(PlaylistEditMachine):
    def __init__(self,songs=()):
        super().__init__()
        self.mock('strstr@GLIBC_2.0')
        self.db.execute('create table songtable(id integer primary key,album,songer,albumsonger,fileurl,year,track,cd_num)')
        self.db.executemany('insert into songtable(album,songer,albumsonger,fileurl,year) values(?,?,?,?,?)',songs)
    def open_era(self):
        self.call(address=payload_syms['era_menu'],args=(0,self.event,0,0),gap=0); self.page=self.top
        return self.texts()
    def decade(self,i):
        row=self.nodes[self.find('scroll_view')]['children'][i]; f,ctx=self.handler(row,O['EVT_CLICK'])
        self.call(address=f,args=(ctx,self.event,0,0),gap=0); self.page=self.top
        return self.texts()
SONGS=[('Alpha','A1','','/mnt/mmc/m/a/1.mp3',1990),('Beta','B1','Beta Band','/mnt/mmc/m/b/1.mp3',1998),
       ('Beta','B2','Beta Band','/mnt/mmc/m/b/2.mp3',1999),('Gamma','C1','','/mnt/mmc/m/c/1.mp3',2000),
       ('Delta','D1','','/mnt/mmc/m/d/1.mp3',0),('Eta','E1','','/mnt/mmc/m/e/1.mp3',1989),
       ('Book','W1','','/mnt/mmc/audiobooks/w/1.mp3',1995),('Tale','T1','','/mnt/mmc/m/t.m4b',1993),
       ('Pod','P1','','/mnt/mmc/podcasts/p/1.mp3',1996),('','U1','','/mnt/mmc/m/u.mp3',1994)]
m=EraMachine(SONGS); assert m.open_era()==['Years','80s','1 album','90s','2 albums','2000s','1 album']
assert m.nodes[m.top]['name']=='era_page' and m.closed_db==1; passed()
into=lambda: [m.nodes[m.nodes[r]['children'][-1]].get('image')=='list_into' for r in m.nodes[m.find('scroll_view')]['children']]
assert into()==[True]*3  # a decade opens its albums
assert m.decade(1)==['90s','Play All','2 albums','Shuffle','Every song, shuffled','Alpha','A1 \u2022 1990','Beta','Beta Band \u2022 1999']
assert m.nodes[m.top]['name']=='era_albums_page' and into()==[False,False,True,True]; passed()  # an album opens Coverflow
# Era's direct SQLite queries respect markers before counting/grouping and nested exceptions.
for restore in (False,True):
    m=EraMachine(SONGS+[('Hidden','Artist','Artist','/mnt/mmc/Hidden/a.flac',1970),
                        ('Restored','Artist','Artist','/mnt/mmc/Hidden/Keep/a.flac',1985)])
    m.scope_tree={'/mnt/mmc':[('Hidden',4)],'/mnt/mmc/Hidden':[('Keep',4)],'/mnt/mmc/Hidden/Keep':[]}
    m.scope_markers={'/mnt/mmc/Hidden/database.ignore'}
    if restore: m.scope_markers.add('/mnt/mmc/Hidden/Keep/database.unignore')
    m.scope_scan()
    assert m.open_era()==['Years','80s','2 albums' if restore else '1 album','90s','2 albums','2000s','1 album']
    shown=m.decade(0)
    assert ('Restored' in shown)==restore and 'Hidden' not in shown
    passed()
# The full library is counted past stock's old 20,000-song limit, with a bounded SQLite cache.
m=EraMachine((f'Large {i//10}','Artist','Artist',f'/mnt/mmc/music/large/{i}.flac',2020) for i in range(65000))
assert m.open_era()==['Years','2020s','6500 albums']
assert m.db.execute('PRAGMA cache_size').fetchone()==(64,)
assert m.db.execute('PRAGMA temp_store').fetchone()==(1,); passed()
for failure in ('open','PRAGMA','select'):
    m=EraMachine(SONGS); m.fail=failure; top=m.top; m.open_era()
    assert m.top==top and m.toasts[-1][3]=='Years unavailable' and m.closed_db==1; passed()
# The ratings' copy on the card (ratings.c rc_*): the tables' hashes named again from the library, a page a poll.
RC_FILE='/mnt/mmc/.ratings.tsv'; RC_HEAD='#song\tstars\tliked\tpath\tcue_start\n#album\thearts\tname\talbum_artist\tfolder\n'
class RatingsCopyMachine(FileMocks,EraMachine):
    def __init__(self,songs,favs=()):
        super().__init__(); self.budget=2_000_000; self.file_mocks('rc:',{'/proc/mounts':b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n'}); self.writes=0
        self.db.execute('drop table songtable')
        for t in ('songtable','myfav'): self.db.execute(f'create table {t}(id integer primary key,fileurl,time,album,albumsonger)')
        self.db.executemany('insert into songtable(fileurl,time,album,albumsonger) values(?,?,?,?)',songs)
        self.db.executemany('insert into myfav(fileurl,time,album,albumsonger) values(?,?,?,?)',favs)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('rc:'): return super().hook(u,address,size,unused)
        name=name[3:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; self.writes+=name=='rename' and self.text(b)==RC_FILE
        self.returns(self.file_op(u,name,a,b,c,d),clobber=True)
    def rec(self,path,cue=0,album=None,artist=None):
        r=self.record((1,0,path,cue,0,'x')); self.word(r+O['REC_ALBUM'],self.string(album) if album is not None else 0)
        self.word(r+O['REC_ALBUM_ARTIST'],self.string(artist) if artist is not None else 0); return r
    def key(self,f,*a,**k): return self.call(address=payload_syms[f],args=(self.rec(*a,**k),0,0,0),gap=0)&0xffffffff
    def rate(self,songs=(),albums=()):  # (key, stars or hearts) each, as the tables keep them
        for path,rows in (('/mnt/data/ringnav-ratings',songs),('/mnt/data/ringnav-album-ratings',albums)):
            self.files[path]=b''.join(struct.pack('<I',(k&~7)|h) for k,h in rows)
    def poll(self,ms=5000):  # that long on, then pages until the pass ends
        self.now+=ms; f=payload_syms['ratings_copy_poll']; self.call(address=f,args=(0,0,0,0),gap=0)
        while self.get(payload_syms['rc']): self.now+=1000; self.call(address=f,args=(0,0,0,0),gap=0)
    def copy_text(self): return self.files[RC_FILE].decode()
SONGS_RC=[('/mnt/mmc/m/a.flac',0,'Alpha','Band'),('/mnt/mmc/m/a2.flac',0,'Alpha','Band'),('/mnt/mmc/m/cue.flac',0,'Live','Band'),
          ('/mnt/mmc/m/cue.flac',30,'Live','Band'),('/mnt/mmc/CD1/1.flac',0,'Solo',None),('/mnt/mmc/CD2/1.flac',0,'Solo',None),
          ('/mnt/mmc/m/t\tab.flac',0,'Ta\tb','Ban\nd')]
FAV_RC=[('/mnt/mmc/m/fav.flac',0,'Other','Band')]
m=RatingsCopyMachine(SONGS_RC,FAV_RC)
aa=m.key('album_id','/mnt/mmc/m/a.flac',album='Alpha',artist='Band'); ab=m.key('album_id','/mnt/mmc/CD2/1.flac',album='Solo',artist='')
unknown=m.rec('/x'); m.word(unknown+O['REC_ID'],0xffffffff); au=m.call(address=payload_syms['album_id'],args=(unknown,0,0,0),gap=0)&0xffffffff
m.rate([(m.key('listen_key','/mnt/mmc/m/cue.flac',cue=30),3),(m.key('listen_key','/mnt/mmc/m/t\tab.flac'),1),
        (m.key('listen_key','/mnt/mmc/m/fav.flac'),2),(m.key('listen_key','/mnt/mmc/gone.flac'),4)],[(aa,5),(ab,2),(au,1)])
# Five seconds after boot: My Fav's liked with any stars kept for them, the songs with a CUE start, an album once, the
# folder only where no album artist tells it apart, tabs and newlines as spaces, a song no longer in the library left out.
m.poll(0); assert RC_FILE not in m.files; m.poll(4999); assert RC_FILE not in m.files
m.poll(1); text=m.copy_text()
assert text==RC_HEAD+'album\t1\tUnknown Album\t\t\nsong\t2\t1\t/mnt/mmc/m/fav.flac\t\nalbum\t5\tAlpha\tBand\t\nsong\t3\t0\t/mnt/mmc/m/cue.flac\t30\n' \
    'album\t2\tSolo\t\t/mnt/mmc\nsong\t1\t0\t/mnt/mmc/m/t ab.flac\t\n',text
assert m.writes==1 and m.closed_db==1 and not m.get(payload_syms['rc']+4) and '/mnt/mmc/.ratings.tsv.tmp' not in m.files; passed()
# Nothing due, nothing read; the same copy again isn't written; a change is, 5 s after the last of a run.
sql=len(m.sql); m.poll(); m.poll(); assert len(m.sql)==sql
due=payload_syms['ratings_copy_due']; m.call(address=due,args=(0,0,0,0),gap=0); m.poll(); assert len(m.sql)>sql and m.writes==1; passed()
m.db.execute('delete from myfav'); m.call(address=due,args=(0,0,0,0),gap=0); m.poll(4999); assert m.writes==1
m.call(address=due,args=(0,0,0,0),gap=0); m.poll(4999); assert m.writes==1; m.poll(1); assert m.writes==2 and 'fav.flac' not in m.copy_text(); passed()
# The card away (USB storage): it waits, then writes.
m.db.execute("insert into myfav(fileurl,time) values('/mnt/mmc/m/a.flac',0)"); mounts=m.files['/proc/mounts']; m.files['/proc/mounts']=b'none /mnt/data ubifs rw 0 0\n'
sql=len(m.sql); m.call(address=due,args=(0,0,0,0),gap=0); m.poll(); assert len(m.sql)==sql and m.writes==2
m.files['/proc/mounts']=mounts; m.poll(); assert m.writes==3 and 'song\t0\t1\t/mnt/mmc/m/a.flac\t\n' in m.copy_text(); passed()
# USB storage taking the card during a pass: nothing written, and due again once it's back.
m.db.execute("insert into myfav(fileurl,time) values('/mnt/mmc/m/u.flac',0)"); m.call(address=due,args=(0,0,0,0),gap=0); m.now+=5000
f=payload_syms['ratings_copy_poll']; m.call(address=f,args=(0,0,0,0),gap=0); assert m.get(payload_syms['rc'])
m.files['/proc/mounts']=b'none /mnt/data ubifs rw 0 0\n'; writes=m.writes
while m.get(payload_syms['rc']): m.now+=1000; m.call(address=f,args=(0,0,0,0),gap=0)
assert m.writes==writes; m.files['/proc/mounts']=mounts; m.poll(); assert m.writes==writes+1 and 'u.flac' in m.copy_text()
m.db.execute("delete from myfav where fileurl like '%u.flac'"); m.call(address=due,args=(0,0,0,0),gap=0); m.poll(); passed()
# A scan holds a pass back and ends one under way, unwritten; the library rewritten makes the copy due again.
writes=m.writes
m.db.execute('delete from myfav'); m.call(address=due,args=(0,0,0,0),gap=0); m.word(O['SCAN_THREAD'],1); m.word(O['SCAN_DONE'],0); m.poll(); assert m.writes==writes
f=payload_syms['ratings_copy_poll']; m.word(O['SCAN_THREAD'],0); m.now+=5000; m.call(address=f,args=(0,0,0,0),gap=0); assert m.get(payload_syms['rc'])
m.word(O['SCAN_THREAD'],1); m.word(O['SCAN_DONE'],0); m.now+=1000; m.call(address=f,args=(0,0,0,0),gap=0); assert not m.get(payload_syms['rc']) and m.writes==writes
m.word(O['SCAN_DONE'],1); m.word(payload_syms['library_gen'],m.get(payload_syms['library_gen'])+1); m.poll(); assert m.writes==writes+1; passed()
# Fewer lines than the card's copy: the old one is kept as .1, so a lost table can't take the only copy.
kept=m.files[RC_FILE+'.1'].decode(); assert kept.count('\n')>m.copy_text().count('\n') and 'song\t0\t1\t/mnt/mmc/m/a.flac' in kept; passed()
# Input holds pages back; the pass then goes on.
m.call(address=due,args=(0,0,0,0),gap=0); m.db.execute("insert into myfav(fileurl,time) values('/mnt/mmc/m/q.flac',0)"); m.now+=5000
f=payload_syms['ratings_copy_poll']; m.call(address=f,args=(0,0,0,0),gap=0); assert m.get(payload_syms['rc'])
sql=len(m.sql); m.call(address=payload_syms['ringnav_input'],args=(m.wm,m.event,0,0),gap=0); m.call(address=f,args=(0,0,0,0),gap=0); assert len(m.sql)==sql
m.poll(0); assert 'q.flac' in m.copy_text(); passed()
# A failed write leaves the copy, and no .tmp on the card.
before=m.copy_text(); m.db.execute("insert into myfav(fileurl,time) values('/mnt/mmc/m/w.flac',0)"); m.fail_write=True; writes=m.writes
m.call(address=due,args=(0,0,0,0),gap=0); m.poll(); m.fail_write=False; assert m.writes==writes and m.copy_text()==before; passed()
# A read that fails leaves the copy as it was, the connection closed.
before=m.copy_text(); writes=m.writes; m.db.execute("insert into myfav(fileurl,time) values('/mnt/mmc/m/a2.flac',0)"); m.fail='select'; closed=m.closed_db
m.call(address=due,args=(0,0,0,0),gap=0); m.poll(); assert m.writes==writes and m.copy_text()==before and m.closed_db==closed+1; m.fail=''; passed()
# So does a failed look for songtable, which isn't "no scan yet": the copy isn't cut to My Fav's alone.
m=RatingsCopyMachine(SONGS_RC,FAV_RC); m.rate([(m.key('listen_key','/mnt/mmc/m/a.flac'),3)]); m.poll(0); m.poll(); before=m.copy_text(); assert 'a.flac' in before
m.db.execute("insert into myfav(fileurl,time) values('/mnt/mmc/m/a2.flac',0)"); m.fail='select 1 from sqlite_master'; closed=m.closed_db
m.call(address=payload_syms['ratings_copy_due'],args=(0,0,0,0),gap=0); m.poll(); assert m.writes==1 and m.copy_text()==before and m.closed_db==closed+1; m.fail=''; passed()
# The longest copy stays as .1: a rating given and cleared after a lost table can't take it. A shorter copy
# whose write fails leaves the last one where it was, with no .tmp.
m=RatingsCopyMachine(SONGS_RC,FAV_RC+[(f'/mnt/mmc/m/f{i}.flac',0,'','') for i in range(3)]); m.poll(0); m.poll(); full=m.copy_text()
def rc_change(sql=''):
    if sql: m.db.execute(sql)
    m.call(address=payload_syms['ratings_copy_due'],args=(0,0,0,0),gap=0); m.poll()
rc_change('delete from myfav'); assert m.files[RC_FILE+'.1'].decode()==full and m.copy_text()==RC_HEAD
rc_change("insert into myfav(fileurl,time) values('/mnt/mmc/m/n.flac',0)"); one=m.copy_text(); assert 'n.flac' in one
m.db.execute('delete from myfav'); m.fail_write=True; rc_change(); m.fail_write=False
assert m.copy_text()==one and m.files[RC_FILE+'.1'].decode()==full and RC_FILE+'.tmp' not in m.files
rc_change(); assert m.copy_text()==RC_HEAD and m.files[RC_FILE+'.1'].decode()==full; passed()
# Out of memory ends the pass with the last copy kept, not one with rows missing.
long=[(f'/mnt/mmc/{"x"*1000}/{i}.flac',0,'','') for i in range(80)]
m=RatingsCopyMachine(SONGS_RC,long); m.poll(0); m.files[RC_FILE]=b'kept\n'; m.fail_realloc=True; m.poll(); assert m.files[RC_FILE]==b'kept\n' and m.writes==0
m.fail_realloc=False; m.call(address=payload_syms['ratings_copy_due'],args=(0,0,0,0),gap=0); m.poll(); assert m.copy_text().count('\n')==82; passed()
# No scan yet, so no songtable: My Fav's alone still get their copy.
m=RatingsCopyMachine([],FAV_RC); m.db.execute('drop table songtable'); m.poll(0); m.poll()
assert m.copy_text()==RC_HEAD+'song\t0\t1\t/mnt/mmc/m/fav.flac\t\n' and m.closed_db==1; passed()
# A library past a page: every page is read, and a pass stops at the last rated found.
many=[(f'/mnt/mmc/l/{i}.flac',0,'','') for i in range(200)]
m=RatingsCopyMachine(many); m.rate([(m.key('listen_key','/mnt/mmc/l/100.flac'),4)]); m.poll(0); m.poll()
assert m.copy_text()==RC_HEAD+'song\t4\t0\t/mnt/mmc/l/100.flac\t\n' and not [q for q in m.sql if 'id>128' in q]; passed()
# Full-date tags belong to their year; normalize each track before picking the album's latest.
dates=[('Dates','Artist','','/mnt/mmc/music/d1.flac',20220510),
       ('Dates','Artist','','/mnt/mmc/music/d2.flac',2023),
       ('ISO','Artist','','/mnt/mmc/music/i.flac','2021-05-10'),
       ('Month','Artist','','/mnt/mmc/music/m.flac',202004),
       ('Bad','Artist','','/mnt/mmc/music/b.flac','unknown')]
m=EraMachine(dates); assert m.open_era()==['Years','2020s','3 albums']
assert m.decade(0)==['2020s','Play All','3 albums','Shuffle','Every song, shuffled',
                     'Month','Artist \u2022 2020','ISO','Artist \u2022 2021','Dates','Artist \u2022 2023']; passed()
# Play All plays the decade's albums from the first song, each in album order; an album opens its tracks in Coverflow.
class EraPlay(EraMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='q:getMusicByAlbum': self.found=self.by_album.get(self.text(u.reg_read(REGS[0])),[])
        return super().hook(u,address,size,unused)
def era_track(m,album,aa,*t):
    r=track(m,*t); m.word(r+O['REC_ALBUM'],m.string(album)); m.word(r+O['REC_ALBUM_ARTIST'],m.string(aa)); return r
m=EraPlay(SONGS); m.by_album={'Alpha':[era_track(m,'Alpha','','a1','/mnt/mmc/m/a/1.mp3',1,1)],
                              'Beta':[era_track(m,'Beta','Beta Band','b2','/mnt/mmc/m/b/2.mp3',1,2),era_track(m,'Beta','Beta Band','b1','/mnt/mmc/m/b/1.mp3',1,1)]}
# Play All's songs are one query's, ordered as the page (year, then name) and each album by disc, track and path, matched
# to the library's records by song-table id (SONGS' rows 1-3), whatever the library's own order.
m.found=[era_track(m,'Beta','Beta Band','b2','/mnt/mmc/m/b/2.mp3',1,2),era_track(m,'Alpha','','a1','/mnt/mmc/m/a/1.mp3',1,1),
         era_track(m,'Beta','Beta Band','b1','/mnt/mmc/m/b/1.mp3',1,1)]
for r,ident in zip(m.found,(3,1,2)): m.word(r+O['REC_ID'],ident)
m.open_era(); m.decade(1); albums=m.page
row=lambda i: m.handler(m.nodes[m.find('scroll_view',albums)]['children'][i],O['EVT_CLICK'])
f,ctx=row(0); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
assert m.plays[-1][2:]==(0,0xf001,2) and m.play_names==['a1','b1','b2']; passed()
f,ctx=row(3); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.page=m.top
assert m.nodes[m.top]['name']=='coverflow_page' and m.texts()[:3]==['Beta','b1','b2']; passed()
# Two valid paths collide under the old FNV32 membership check: only the dated song belongs.
paths=('/mnt/mmc/Music/z4dmr4cvs8lw.flac','/mnt/mmc/Music/mhf1gqxzbiuf.flac')
m=EraMachine([('Old','Artist','Artist',paths[0],1990),('New','Artist','Artist',paths[1],2020)])
m.found=[m.song('old'),m.song('new')]
for ident,(r,path) in enumerate(zip(m.found,paths),1):
    m.word(r+O['REC_ID'],ident); m.word(r+O['REC_PATH'],m.string(path))
m.open_era(); m.decade(0)
f,ctx=m.handler(m.nodes[m.find('scroll_view')]['children'][1],O['EVT_CLICK'])
m.call(address=f,args=(ctx,m.event,0,0),gap=0)
assert m.play_names==['old'] and m.names(m.get(syms['tools_pdeq_directory']))==['staged']; passed()
m=EraMachine(); top=m.top; assert m.open_era() is not None and m.top!=top and m.nodes[m.top]['name']=='era_page' and empty_titles(m)==['No Dated Albums Yet']; passed()
m=EraMachine([s for s in SONGS if s[4]==0]); top=m.top; m.open_era(); assert m.top!=top and empty_titles(m)==['No Dated Albums Yet'] and not m.toasts; passed()
# Library > Ratings > Albums: the rated albums as Coverflow, most hearts first, with the rated Sort card alone
# (Rating, Album, Artist, Year, kept); closing it brings the library's covers back, with no card.
def ratings_albums(m):
    m.call(address=payload_syms['ratings_menu'],args=(0,m.event,0,0),gap=0)  # the Library's Ratings row
    menu_page=m.top; assert m.nodes[menu_page]['name']=='ratings_page'; row=m.nodes[m.find('scroll_view',menu_page)]['children'][2]; f,ctx=m.handler(row,O['EVT_CLICK'])
    m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=50_000_000); m.advance(0)
    return menu_page
m=AlbumRateMachine(); ids=[m.aid(r) for r in m.albums]
assert ratings_albums(m)==m.top and m.toasts[-1][3]=='No rated albums yet'; passed()
data=b''.join(struct.pack('<I',(ids[i]&~7)|h) for i,h in ((0,2),(2,5)))
m=AlbumRateMachine(files={ALBUM_RATES:data}); menu_page=ratings_albums(m)
m.page=m.top; m.slide=m.find('slide_menu'); assert m.page!=menu_page and m.nodes[m.page]['name']=='coverflow_page'
def shown(m,i): m.word(m.slide+O['SLIDE_INDEX'],i); f,ctx=m.handler(m.slide,O['EVT_VALUE_CHANGED']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); return m.texts()[0]
assert len(m.nodes[m.slide]['children'])==3 and [shown(m,i) for i in range(3)]==['Album 2','Album 0','Sort: Rating']
shown(m,0); rows=m.menu(); assert rows[3]=='Unfavorite' and not [r for r in rows if r.startswith('Rate')]  # the cover's menu, as Coverflow's
f,ctx=m.handler(m.nodes[m.slide]['children'][2],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.advance(0)
m.slide=m.find('slide_menu'); assert [shown(m,i) for i in range(3)]==['Album 0','Album 2','Sort: Album']
assert [v for k,v in m.files.items() if k.endswith('ringnav-ratedsort')]==[struct.pack('<i',0)]; passed()
# Then Artist, Year and Most Played (saved as the library's SORT_PLAYED, 3), round again to Rating.
for want,saved in (('Sort: Artist',1),('Sort: Year',6),('Sort: Most Played',3),('Sort: Rating',5)):
    f,ctx=m.handler(m.nodes[m.slide]['children'][2],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=50_000_000); m.advance(0)
    m.slide=m.find('slide_menu'); assert shown(m,2)==want
    assert [v for k,v in m.files.items() if k.endswith('ringnav-ratedsort')]==[struct.pack('<i',saved)]
passed()
m.close(); m.stack.remove(m.page); m.top=m.stack[-1]; m.open(); assert len(m.nodes[m.slide]['children'])==3
assert shown(m,2)=='Album 2'; passed()
# Display > Cover Flow's Sort picker (library_settings.c): a pick of Album, Artist, Year, Recently Added, Most Played
# or Random is saved, which Coverflow takes at its next open; the next album hold follows the reordered deque.
m=AlbumRateMachine(); m.word(m.albums[2]+O['REC_ARTIST'],m.string('Aardvark'))
sort_name=lambda: m.text(m.call(address=payload_syms['coverflow_sort_name'],args=(0,0,0,0),gap=0)&0xffffffff)
assert sort_name()=='Sort: Album'
for i,want in ((1,'Sort: Artist'),(2,'Sort: Year'),(3,'Sort: Recently Added'),(4,'Sort: Most Played'),(5,'Sort: Random'),(0,'Sort: Album'),(1,'Sort: Artist')):
    m.call(address=payload_syms['coverflow_sort_set'],args=(i,0,0,0),gap=0); assert sort_name()==want
assert [v for k,v in m.files.items() if k.endswith('ringnav-coversort')]==[struct.pack('<i',1)]  # SORT_ARTIST
m.open(); m.slide=m.find('slide_menu'); m.word(m.slide+O['SLIDE_INDEX'],0); m.press(101); assert m.hold()==11
assert m.nodes[m.title]['text']=='Album 2'; m.release(); m.pick(1); m.advance(0)
assert m.query[:2]==('getMusicByAlbum','Album 2'); passed()

# Coverflow's own song deque feeds the shared queue menu, independently of stock browsing state.
for action,want in ((0,['A','T2','B','C']),(1,None)):
    m=CoverflowMachine(cls=O['CLASS_ALBUMS']); m.open(); view=m.tracks()
    m.call(); assert m.selected(view)==1
    m.press(100); assert m.hold()==11 and m.nodes[m.title]['text']=='T2' and m.release()==0
    assert m.labels()==[*SONG_MENU[:5],'Go to Artist']  # the menu sheet's own song, Go to Album aside
    assert m.hold()==0  # a repeated hold while the dialog is open cannot open another
    m.pick(action); m.pick(action); m.advance(0)
    assert m.names()==(want or played_last(m,['T2'])) and m.top==m.page and m.selected(view)==1 and not m.playback() and not m.plays
    assert m.names(m.get(syms['p_deque_showlist']))==['Row 0','Row 1','Row 2','Row 3']
    m.press(200); assert m.release()==1; passed()
# Its favourite is batch-select's over Coverflow's own deque, its songs taken as All Songs rows; each in-place
# action says what it did in a toast (navigation.c, docs/oled-design.md: "Added to Favorites", "Added to Queue").
m=CoverflowMachine(cls=O['CLASS_ALBUMS']); m.open(); view=m.tracks(); assert m.run('Favorite')=='Added to Favorites'
assert [c[1:3] for c in m.calls if c[0]=='batch_add_file']==[(0xf001,0xf00a)] and [c[1] for c in m.calls if c[0]=='batch_init_selectrecord']==[2]
assert [c[3] for c in m.calls if c[0]=='batch_add_file'][0]!=m.get(syms['p_deque_showlist']); passed()
m=CoverflowMachine(queue=0,cls=O['CLASS_ALBUMS']); m.open(); m.tracks()
assert m.run(1)=='Playing Last' and m.names()==['T1'] and m.mcl('MCL_TYPE')==0xff10 and not m.playback(); passed()
m=CoverflowMachine(mode=2); m.open(); m.tracks(); m.run(0)
m.call(address=syms['mclNextSong'],args=(0,0,0,0),gap=0)
assert m.names()==['A','T1','B','C'] and m.mcl('MCL_POS')==1; passed()
# Return cancels without losing the highlighted song. Changed or closed sources refuse.
m=CoverflowMachine(); m.open(); view=m.tracks(); m.call(); m.press(100); m.hold(); m.release()
f,ctx=m.handler(m.top,O['EVT_KEY_UP']); ev=m.alloc(0x40); m.word(ev,O['EVT_KEY_UP']); m.word(ev+O['EVENT_KEY'],O['KEY_RETURN'])
assert m.call(address=f,args=(ctx,ev,0,0),gap=0)==11 and m.top==m.page
assert m.names()==['A','B','C'] and m.selected(view)==1 and not m.playback(); passed()
for invalidate in ('changed','closed','covers'):
    m=CoverflowMachine(); m.open(); m.tracks(); m.press(100); m.hold(); m.release()
    if invalidate=='changed':
        tracks=next(d for d in m.deqs if m.names(d)==['T1','T2'] and m.deqs[d][0]=='stSongInfo')
        m.word(m.items(tracks)[0]+O['REC_NAME'],m.string('changed'))
    elif invalidate=='closed': m.close()
    else: m.key()  # simulate the underlying page returning to covers before the action
    m.pick(0); m.advance(0)
    assert m.toasts[-1][3]=='Queue unchanged' and m.names()==['A','B','C'] and not m.playback(); passed()
# Album identity survives closing/reopening and library reordering; song positions are per album.
m=CoverflowMachine(); m.open()
m.call(address=syms['slide_menu_set_value'],args=(m.slide,2,0,0),gap=0)
f,ctx=m.handler(m.slide,O['EVT_VALUE_CHANGED']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
m.close(); m.albums.reverse(); m.rescan(); m.open()
assert m.get(m.slide+O['SLIDE_INDEX'])==0 and 'Album 2' in m.texts(); passed()
view=m.tracks(0); m.call(); assert m.selected(view)==1
m.key(); other=m.tracks(1); assert m.selected(other)==0
m.key(); view=m.tracks(0); assert m.selected(view)==1
m.close(); m.open(); view=m.tracks(0); assert m.selected(view)==1; passed()
# Long metadata and track titles use the stock scrolling label, with the complete text: the captions loop; a
# track's title rests on its ellipsis and scrolls only while selected (coverflow.c to_tracks).
m=CoverflowMachine(); long='A very long title or artist name ' * 8
m.word(m.albums[0]+O['REC_ALBUM'],m.string(long)); m.word(m.albums[0]+O['REC_ARTIST'],m.string(long))
m.word(m.found[0]+O['REC_NAME'],m.string(long)); m.open(); assert m.texts().count(long)==2
view=m.tracks(); assert m.texts().count(long)==4  # covers stay alive, hidden behind the tracks; the page's title, hidden
row=m.nodes[m.nodes[view]['children'][0]]['children'][0]
labels=[w for w,n in m.nodes.items() if m.alive(w) and n.get('text')==long]
assert row in labels and all(m.nodes[w]['type']=='hscroll_label' and m.nodes[w]['set_hscroll_label_attribute'] for w in labels)
assert all(m.nodes[w]['loop']==1 for w in labels if w!=row)
assert m.nodes[row]['loop']==0 and m.nodes[row]['hscroll_label_set_only_focus']==1 and m.nodes[row]['hscroll_label_set_ellipses']==1; passed()
# Wheel only: the covers' slide_menu takes no touch.
m=CoverflowMachine(); m.open()
assert m.nodes[m.slide].get('sensitive')==0; passed()
# Text geometry, past the first system_bar load as on a unit, and the page, full-bleed, its covers and rows in views from
# the status row's foot (page_open, bar_foot), so a view's y counts from BAR on the screen:
# album over artist under the covers (the theme's coverflow caption: text_y, artist_y, name_px, artist_px), the album white and the artist grey (CF_GREY), CF_EDGE from the sides and clear of the rounded glass. The
# tracks are the theme's one-line song rows, page_x from the sides, the album named in the bar, not over them, the list
# running on to the window's foot, its rows past the set's list end (y 282) fading (docs/oled-design.md), the seventh
# clear of the glass too.
from ipod import corner_inset
def cf_geometry(m,w): return tuple(signed(m.get(w+O[k])) for k in ('W_X','W_Y','W_W','W_H'))
def clear(x,top,w,px):  # a label's text band, top in screen rows
    return max(corner_inset(top),corner_inset(top+px))<=x and x+w<=375-max(corner_inset(top),corner_inset(top+px))
OLED=theme_builtins()[0]
m=CoverflowMachine(); m.found=[m.song(f'T{i}') for i in range(12)]
m.record_syscalls(); m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'],16)]='stock_ui_load'
m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0],args=(m.string('system_bar'),0,0,0),gap=0)
BAR=320-m.call(address=payload_syms['client_h'],args=(0,0,0,0),gap=0); assert BAR==58
page=m.open(); labels=[w for w in m.nodes[m.get(m.slide+O['W_PARENT'])]['children'] if m.nodes[w]['type']=='hscroll_label']
name,artist=labels
E=O['CF_EDGE']; y=OLED['cf.text_y']; ay=y+OLED['cf.artist_y']
assert y>=OLED['cf.top']+160 and ay+O['CF_ARTIST_H']<=320-BAR
assert [cf_geometry(m,w) for w in labels]==[(E,y,375-2*E,O['CF_NAME_H']),(E,ay,375-2*E,O['CF_ARTIST_H'])]
assert m.nodes[name]['style:normal:font_size']==OLED['cf.name_px']==27 and m.nodes[artist]['style:normal:font_size']==OLED['cf.artist_px']==22
from theme import TOKENS
# the artist in the Artist role's Text Secondary (dark), never stock's #AAAAAA, which the theme paints Tertiary
assert m.nodes[artist]['style:normal:text_color']==signed(0xff000000|TOKENS['secondary'][0]) and 'style:normal:text_color' not in m.nodes[name]
for w,px in ((name,OLED['cf.name_px']),(artist,OLED['cf.artist_px'])):
    x,top,wd,h=cf_geometry(m,w); assert h>=px and clear(x,BAR+top+(h-px)//2,wd,px)
view=m.tracks(); lv=m.get(view+O['W_PARENT']); title=next(w for w in m.nodes[m.get(lv+O['W_PARENT'])]['children'] if m.nodes[w]['type']=='hscroll_label')
assert not m.nodes[title]['visible']
X,ROW,px=OLED['page_x'],OLED['page_row'],OLED['font_px[ROLE_TITLE]']; fit=m.call(address=payload_syms['list_fit_end'],args=(0,0,0,0),gap=0)
assert fit==282 and cf_geometry(m,lv)[1::2]==(0,320-BAR),cf_geometry(m,lv)  # on to the window's foot, its rows past fit fading (navigation.c list_fade)
for i,item in enumerate(m.nodes[view]['children']):
    label=m.nodes[item]['children'][0]; assert cf_geometry(m,item)[1]==i*ROW and cf_geometry(m,label)==(X,0,375-2*X,ROW)
    assert m.nodes[label]['style:normal:font_size']==px
assert clear(X,BAR+6*ROW+(ROW-px)//2,375-2*X,px)  # the lowest visible row
passed()
# Execute stock sibling selection and playback scheduling; mock filesystem enumeration,
# tag decoder and image codec boundaries, never the resulting player globals.
class RolloverMachine(CoverflowMachine):
    def __init__(self,sizes=(2,2,1,1)):
        super().__init__(queue=0,cls=1)
        self.handlers.pop(syms['player_mainscheduling'])  # runs stock's, as tick() calls it
        self.native_trace={syms[n]:n for n in ('mclAutoChange','mclNextSong','on_player_autochange','toolsLoadNextDir',
                          'player_refresh_playqueue','mclLoadPlayList','player_get_id3info','player_set_coverinfo','mclClearChangeFlag')}
        self.native_trace[syms['open@GLIBC_2.0']]='press_open'  # only a stamp opens a file here; it fails at the write
        self.mock('open@GLIBC_2.0','write@GLIBC_2.0','close@GLIBC_2.0')
        self.trace=[]; self.files={}; self.dirs={}; self.artfiles=set(); self.worker=False; self.forks=0
        self.handlers[0x5d5614]='r:directory'; self.handlers[0x5d58e4]='r:directory'
        self.handlers[0x5bf504]='r:decoder_start'
        # Lazy-bound malloc, as used by stock player_refresh_playqueue.
        self.word(0xa3a0e8,0x1000014); self.handlers[0x1000014]='r:malloc'
        for name in ('strcpy@GLIBC_2.0','strncmp@GLIBC_2.0','strstr@GLIBC_2.0','strchr@GLIBC_2.0',
                     'toolsGetMusicInfo','toolsGetFileSize','toolsGetAlbumCover','toolsGetExternCover',
                     'access@GLIBC_2.0','remove@GLIBC_2.0','usleep@GLIBC_2.0','fork@GLIBC_2.0'):
            self.handlers[syms[name]]='r:'+name.split('@')[0]
        # the thread's sleep between rounds (COVER_WAITS: cover_wake.c's futex wait), where artwork() ends a round
        self.handlers[payload_syms['ringnav_play_cover_wait']]='r:usleep'
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0','toolsFreeStSongInfo',
                  'mclLoadExLyric','sendBtHeadsetPlayStatus','notifyPlayInfo','notifyPlayStatus','notifyRefreshLyric',
                  'dmrNotifyPlayStatus','dlnaRenderSaveUrlMetadata','reset_repeatinfo','initializeDmrQCurrentInfo',
                  'player_reconfig','add_playrecord','toolsTrimLeft')
        self.handlers.pop(syms['mclStartPlayer'],None)
        for i,n in enumerate(sizes):
            folder=f'/mnt/mmc/{i}'; entries=[]
            for j in range(n):
                path=f'{folder}/{j}.flac'; r=self.song(f'{j}.flac')
                self.word(r+O['REC_PATH'],self.string(path)); self.word(r+0x38,1)
                for off in ('REC_ALBUM','REC_ARTIST'): self.word(r+O[off],0)
                entries.append(r); self.files[path]=(f'Album {i}' if i!=2 else '',i!=2)
            self.dirs[folder]=entries
        self.dirs['/mnt/mmc']=[]
        for folder in list(self.dirs)[:-1]:
            r=self.song(folder.rsplit('/',1)[1],4); self.word(r+O['REC_PATH'],self.string(folder)); self.dirs['/mnt/mmc'].append(r)
        self.word(0xa4ff80,syms['on_player_autochange']) # registered by stock player startup
        self.byte(0xa5002b,1) # mclSetJumpFolder's flag
        self.call(address=syms['mclLoadPlayList'],args=(self.deque(self.dirs['/mnt/mmc/0']),0,1,0),gap=0)
        self.call(address=syms['mclStartPlayer'],args=(0,0,0,0),gap=0)
        self.call(address=syms['player_get_id3info'],args=(0,0,0,0),gap=0)
        self.handlers.pop(payload_syms['press_stamp'])  # the natural changes from here run the real one
    def hook(self,u,address,size,unused):
        if address in getattr(self,'native_trace',{}): self.trace.append((self.native_trace[address],None))
        name=self.handlers.get(address,'')
        if name=='widget_load_image':
            path=self.text(u.reg_read(UC_MIPS_REG_A1))
            self.image_size=None if path.startswith('file://') and path[7:] not in self.artfiles else (50,50)
        if not name.startswith('r:'): return super().hook(u,address,size,unused)
        name=name[2:]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        self.trace.append((name,self.text(a) if name in ('directory','decoder_start','toolsGetAlbumCover') else a))
        if name=='directory':
            path=self.text(a); self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',r) for r in self.dirs.get(path,[])]; ret=len(self.dirs.get(path,[]))
        elif name=='decoder_start': self.byte(0xa5002d,1)
        elif name=='malloc': ret=self.alloc((a+3)&~3)
        elif name=='strcpy': u.mem_write(a,self.text(b).encode()+b'\0'); ret=a
        elif name=='strncmp': x,y=self.text(a)[:c],self.text(b)[:c]; ret=(x>y)-(x<y)
        elif name in ('strchr','strstr'):
            i=self.text(a).find(chr(b) if name=='strchr' else self.text(b)); ret=a+i if i>=0 else 0
        elif name=='toolsGetMusicInfo':
            path=self.text(c); album,_=self.files[path]; self.trace.append(('tags',path))
            if album:
                u.mem_write(b,album.encode()+b'\0'); u.mem_write(b+0x100,b'Artist\0')
                u.mem_write(b+0x280,path.rsplit('/',1)[1].encode()+b'\0')
            ret=int(bool(album))
        elif name=='toolsGetFileSize': ret=4096
        elif name=='toolsGetAlbumCover':
            ret=int(self.files[self.text(a)][1])
            if ret: self.artfiles.add(self.text(b))
        elif name=='access': ret=0 if self.text(a) in self.artfiles else -1
        elif name=='remove': self.artfiles.discard(self.text(a))
        elif name=='fork': self.forks+=1; ret=-1  # coverflow_player_cover's ffmpeg, refused
        elif name=='usleep' and self.worker:
            u.reg_write(UC_MIPS_REG_PC,0x1000000); u.emu_stop(); return
        self.returns(ret,clobber=True)
    def tick(self): self.call(address=syms['player_mainscheduling'],args=(0,0,0,0),gap=0)
    def artwork(self):
        self.worker=True
        self.u.reg_write(UC_MIPS_REG_SP,0x7000f000)
        self.u.reg_write(UC_MIPS_REG_T9,syms['player_parsecover_thd'])
        self.u.emu_start(syms['player_parsecover_thd'],0x1000000,count=self.budget)
        assert self.u.reg_read(UC_MIPS_REG_PC)==0x1000000
        self.worker=False


    def ui(self):
        self.top=self.win
        self.call(address=0x53d810,args=(self.info,0,0,0),gap=0) # stock playing_timer_start callback
        self.paint(self.win,gap=0)
        self.top=self.home; self.paint(self.home,gap=0); self.top=self.win
    def setup_ui(self):
        bar=self.node('window','system_bar'); self.word(syms['system_bar'],bar); self.word(bar+O['W_PARENT'],self.wm)
        home_list(self); self.home=self.top; self.art=named(self,self.home,'img_homeart')
        self.call(address=home_hook[0],args=(self.home,0,0,0),gap=0)
        self.cover=self.node('image','img_cover'); self.songlabel=self.node('hscroll_label','scrlabel_title')
        self.albumlabel=self.node('label','label_ipod_album'); self.poslabel=self.node('label','label_ipod_pos')
        self.win=self.node('window','playing_page',[self.cover,self.songlabel,self.albumlabel,self.poslabel])
        self.word(self.win+O['W_PARENT'],self.wm); self.top=self.win
        self.info=self.alloc(0x40); self.word(self.info+0x20,self.win)
        playing=HOOKS['playing_page_init'][0]; self.handlers[playing+12]='stock_playing'
        self.call(address=playing,args=(self.win,0,0,0),gap=0)
        self.byte(syms['g_forcerefresh_flag'],1)

from build import PLAYER_COVER
assert struct.unpack_from('<I',demo,fileoff(demo,PLAYER_COVER[0]))[0]==jal(payload_syms['coverflow_player_cover'])
for sizes in ((2,2,1,1),(1,1,1,1)):
    m=RolloverMachine(sizes); m.setup_ui(); m.artwork(); m.ui()
    paths=[path for path in m.files]
    assert m.text(syms['g_play_id3_info'])==paths[0]
    for index,path in enumerate(paths[1:],1):
        old=m.text(syms['g_play_id3_info']); m.byte(0xa5002d,0) # hciplayer EOF
        # Delay the UI scheduler after stock queue replacement on alternate transitions.
        if index%2:
            m.call(address=syms['mclAutoChange'],args=(0,0,0,0),gap=0)
            assert m.text(syms['g_play_id3_info'])==old
            m.ui()
            assert m.nodes[m.albumlabel]['text']=='' and m.nodes[m.art]['image']=='default_album_home'
        m.tick() # native auto-change, sibling traversal, queue reload, parsing and notification
        assert m.text(syms['g_play_id3_info'])==path
        assert m.text(syms['g_play_id3_info']+O['ID3_ALBUM'])==m.files[path][0]
        assert m.text(syms['g_play_cover_info']+8)==path
        assert m.text(syms['g_lastcover_url'])==old # worker has not completed yet
        m.ui()
        assert m.nodes[m.albumlabel]['text']==m.files[path][0]
        assert m.nodes[m.art]['image']=='default_album_home'
        forks=m.forks; m.artwork()
        assert m.text(syms['g_lastcover_url'])==path
        assert m.u.mem_read(syms['g_playcover_finishflag'],1)==b'\1'
        m.ui()
        # No art: the folder-image call went through coverflow_player_cover to ffmpeg (refused here).
        assert (m.forks>forks)!=m.files[path][1]
        want='file:///tmp/coverpic.jpg' if m.files[path][1] else 'default_album_home'
        assert m.nodes[m.cover]['image']==(want if m.files[path][1] else 'play_defaultcover'),(path,m.nodes[m.cover])
        assert m.nodes[m.songlabel]['text']==path.rsplit('/',1)[1]
        assert m.nodes[m.art]['image']==want
        queue=m.items(m.get(syms['mcl_pdeqplaylist']))
        assert 'text' not in m.nodes[m.poslabel] and m.text(m.get(queue[m.mcl('MCL_POS')]+O['REC_PATH']))==path  # no "n of m"
        assert all(not m.get(r+O['REC_ALBUM']) for r in queue) # tags came from parsing, not library rows
        assert ('tags',path) in m.trace and ('decoder_start',path) in m.trace
        passed()
    assert [path for event,path in m.trace if event=='directory']==['/mnt/mmc','/mnt/mmc/1','/mnt/mmc','/mnt/mmc/2','/mnt/mmc','/mnt/mmc/3']
    events=[event for event,_ in m.trace]
    for event in ('on_player_autochange','toolsLoadNextDir','player_refresh_playqueue'): assert events.count(event)==3
    assert events.count('mclLoadPlayList')==4 and events.count('mclClearChangeFlag')==len(paths)-1
    # Folder Skip's rollover is a natural change: its load stamps no press (docs/internals.md#wired-start).
    assert 'press_open' not in events
# A CUE track (issue #59): its record's PERFORMER is kept, a missing sheet TITLE takes the file's album.
from build import ID3_PATH
assert struct.unpack_from('<I',demo,fileoff(demo,ID3_PATH[0]))[0]==jal(payload_syms['ringnav_id3_path'])
m=RolloverMachine((1,1)); m.handlers.pop(syms['mclGetCueFlag'])  # stock's, on the queue
r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; id3=syms['g_play_id3_info']
m.word(r+O['REC_CUE_START'],10); m.word(r+O['REC_TITLE'],m.string('Chapter')); m.word(r+O['REC_ARTIST'],m.string('Author'))
m.call(address=syms['player_get_id3info'],args=(0,0,0,0),gap=0)
assert [m.text(id3+O[k]) for k in ('ID3_TITLE','ID3_ARTIST','ID3_ALBUM')]==['Chapter','Author','Album 0']
assert m.text(id3)=='/mnt/mmc/0/0.flac'; passed()
# ...and a sheet TITLE without PERFORMER keeps the album and takes the file's artist, not Unknown Artist.
m=RolloverMachine((1,1)); m.handlers.pop(syms['mclGetCueFlag'])
r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]
m.word(r+O['REC_CUE_START'],10); m.word(r+O['REC_TITLE'],m.string('Chapter')); m.word(r+O['REC_ALBUM'],m.string('Book'))
m.call(address=syms['player_get_id3info'],args=(0,0,0,0),gap=0)
assert [m.text(id3+O[k]) for k in ('ID3_TITLE','ID3_ARTIST','ID3_ALBUM')]==['Chapter','Artist','Book']; passed()

# Tag text (TAG_TEXT): stock toolsFixId3ErrorCode, run as patched, took a field TagLib found all Latin-1
# for GBK when its Latin-1 bytes went to UTF-8 and back unchanged ("Lo que la música me enseñó" does) and
# showed Chinese; now only when they also read as GBK (ringnav_gbk_tag): a GBK-tagged MP3's Latin-1 frame
# still shows Chinese, Western text its UTF-8 copy. Either is stored through its tidy-up (0x5c84c0) into
# the 256-byte field.
from build import TAG_TEXT, GENRE_COPY, CUE_TEXT, SCAN_DIRS
assert struct.unpack_from('<I',demo,fileoff(demo,TAG_TEXT[0]))[0]==jal(payload_syms['ringnav_gbk_tag'])
class TagTextMachine(Machine):
    def __init__(self):
        super().__init__()
        self.word(0xa3a0e8,0x1000014); self.handlers[0x1000014]='t:malloc'  # lazy-bound, as RolloverMachine's
        self.word(0xa3a270,0x1000010); self.handlers[0x1000010]='t:free'
        self.handlers[syms['strcpy@GLIBC_2.0']]='t:strcpy'; self.handlers[syms['toolsIconvConvert']]='t:iconv'
        self.handlers[syms['strcmp@GLIBC_2.0']]='t:strcmp'  # on bytes: GBK isn't UTF-8
        self.mock('snprintf@GLIBC_2.0'); self.iconv=0
        self.handlers.pop(syms['toolsFixId3ErrorCode'])  # stock's own runs here (ogg_music_info calls it)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('t:'): return super().hook(u,address,size,unused)
        a,b,c=[u.reg_read(r) for r in REGS[:3]]; ret=0
        if name=='t:malloc': ret=self.alloc((a+3)&~3)
        elif name=='t:strcpy': u.mem_write(a,self.raw(b)+b'\0'); ret=a
        elif name=='t:strcmp': x,y=self.raw(a),self.raw(b); ret=(x>y)-(x<y)
        elif name=='t:iconv':  # toolsIconvConvert(s, to, from) in place; a failed one leaves s
            self.iconv+=1
            try: u.mem_write(a,self.raw(a).decode(self.text(c)).encode(self.text(b))+b'\0'); ret=1
            except UnicodeError: pass
        self.returns(ret,clobber=True)
for utf8,raw in (('Lo que la música me enseñó',None),('Ciencia Ficción',None),('Mateo Nieto',None),('今','今'.encode()),
                 ('Ölüdeniz',None),('Canção do Mar',None),('Informações',None),('Grüße',None),('周杰伦','周杰伦'.encode('gbk')),('Jay 周杰伦 - 晴天','Jay 周杰伦 - 晴天'.encode('gbk'))):
    m=TagTextMachine(); out=m.alloc(0x100); latin1=raw is None or raw!=utf8.encode()
    raw=utf8.encode('latin-1') if raw is None else raw  # taglib_tag_<field>(tag, 0): its Latin-1 bytes
    copy=raw.decode('latin-1') if latin1 else utf8  # (tag, 1): TagLib's UTF-8, of the bytes as Latin-1
    rawp=m.alloc(len(raw)+4); m.u.mem_write(rawp,raw+b'\0')
    m.call(address=0x5d8950,args=(int(latin1),rawp,m.string(copy),out),stack=(1,),gap=0)
    assert m.raw(out).decode()==utf8,(utf8,m.raw(out)); passed()
# Multi-value genres (GENRE_COPY): TagLib's " / " join must fit style's 128 bytes without touching
# the title at +0x80. Keep whole entries; an oversized first entry alone is cut at a whole character
# within 127 bytes.
assert struct.unpack_from('<I',demo,fileoff(demo,GENRE_COPY[0]))[0]==jal(payload_syms['ringnav_genre_copy'])
genres = ['Sludge Metal', 'Alternative Metal', 'Country', 'Death Metal', 'Gothic Rock',
          'Groove Metal', 'Grunge', 'Metal', 'Rock', 'Stoner Metal', 'Alternative Country',
          'Gothic Country']
joined = ' / '.join(genres)
m = Machine()
for given, want in (('', ''), ('Rock', 'Rock'), ('A / B', 'A / B'),
                    ('x' * 127, 'x' * 127), ('x' * 128, 'x' * 127),
                    ('A / ' + 'x' * 124, 'A'), (joined, ' / '.join(genres[:10])),
                    ('語' * 60, '語' * 42), ('x' + '語' * 60, 'x' + '語' * 42),
                    ('xx' + '語' * 60, 'xx' + '語' * 41), ('😀' * 40, '😀' * 31),
                    ('語' * 42 + ' / ' + 'x' * 9, '語' * 42)):
    out = m.alloc(0x100)
    m.u.mem_write(out, b'\xa5' * 0x100)
    assert m.call(address=payload_syms['ringnav_genre_copy'],
                  args=(out, m.string(given), 0, 0), gap=0) == out
    assert m.raw(out) == want.encode(), (given, m.raw(out))
    assert m.u.mem_read(out + 0x80, 0x80) == b'\xa5' * 0x80
bad = m.alloc(0x100); m.u.mem_write(bad, b'\x80' * 200 + b'\0')  # no character starts: nothing to keep
out = m.alloc(0x100); m.u.mem_write(out, b'\xa5' * 0x100)
assert m.call(address=payload_syms['ringnav_genre_copy'], args=(out, bad, 0, 0), gap=0) == out and m.raw(out) == b''
passed()
# CUE sheets (CUE_TEXT): its seven field conversions go to ringnav_cue_text: UTF-8 stays, GBK goes through
# stock's conversion, any other text is Windows-1252 re-encoded as UTF-8, cut at a whole character to 511 bytes.
for address in CUE_TEXT: assert struct.unpack_from('<I',demo,fileoff(demo,address))[0]==jal(payload_syms['ringnav_cue_text'])
for given,want in ((b'Caf\xc3\xa9 \xc3\x91and\xc3\xba','Café Ñandú'),(b'Caf\xe9 \x93Hi\x94 \x80','Café “Hi” €'),
                   (b'\xc3(','Ã('),(b'a\xe9'*200,'aé'*170+'a'),(b'plain','plain'),(b'\xed\xa0\x80','í\xa0€'),
                   ('周杰伦 晴天'.encode('gbk'),'周杰伦 晴天'),('Lo que la música'.encode('cp1252'),'Lo que la música'),
                   ('Canção'.encode('cp1252'),'Canção'),('Beyoncé’s'.encode('cp1252'),'Beyoncé’s'),('周'.encode('gbk')*171,('ÖÜ'*171)[:255])):
    m=TagTextMachine(); buf=m.alloc(512); m.u.mem_write(buf,given+b'\0')
    assert m.call(address=payload_syms['ringnav_cue_text'],args=(buf,m.string('utf-8'),m.string('gbk'),0),gap=0)==1 and m.raw(buf).decode()==want,(given,m.raw(buf)); passed()
# The library scan (SCAN_DIRS) skips every hidden folder: q2-deezer's .deezer/stream never joins the Library.
assert struct.unpack_from('<I',demo,fileoff(demo,SCAN_DIRS[0]))[0]==jal(payload_syms['ringnav_scan_dir'])
m=Machine(); dot=m.string('.')
assert [m.call(address=payload_syms['ringnav_scan_dir'],args=(m.string(n),dot,0,0),gap=0)!=0 for n in ('.','..','.deezer','.Trashes','Music','Deezer')]==[False,False,False,False,True,True]; passed()

# Now Playing's lyric follow (LYRIC_FOLLOW): stock's scroll_to past the end clamps to a row top, the
# content padded to it, not to 275 - 178 = 97 through a row; repeated it stays; a short page is left.
from build import LYRIC_FOLLOW
assert struct.unpack_from('<I',demo,fileoff(demo,LYRIC_FOLLOW[0]))[0]==jal(payload_syms['ringnav_lyric_follow'])
m=Machine(); rows=[m.node('label') for _ in range(12)]
for i,(y,h) in enumerate(zip((0,20,70,90,110,135,155,175,195,215,235,255),(20,50,20,20,25)+(20,)*7)):
    m.word(rows[i]+O['W_Y'],y); m.word(rows[i]+O['W_H'],h)
lrc=m.node('scroll_view','scroll_lrc',rows); m.word(lrc+O['W_H'],178); m.word(lrc+O['VIEW_CONTENT_H'],275)
ys=[]
for y in (195,0,20,195): m.call(address=payload_syms['ringnav_lyric_follow'],args=(lrc,0,y,10),gap=0); ys.append(m.get(lrc+O['SCROLL_Y']))
assert ys==[110,0,20,110] and m.get(lrc+O['VIEW_CONTENT_H'])==288,ys
m.word(lrc+O['VIEW_CONTENT_H'],100); m.call(address=payload_syms['ringnav_lyric_follow'],args=(lrc,0,0,10),gap=0)
assert m.get(lrc+O['VIEW_CONTENT_H'])==100 and m.get(lrc+O['SCROLL_Y'])==0; passed()

# A looping label's lull (HSCROLL_LULL) comes to the payload; its holds are tested with the marquee's.
from build import HSCROLL_LULL
assert struct.unpack_from('<I',demo,fileoff(demo,HSCROLL_LULL[0]))[0]==jal(payload_syms['ringnav_hscroll_lull']); passed()

# A refresh's lyric rebuild (LYRIC_ROWS), which stock ends at offset 0, keeps the view where it was.
from build import LYRIC_ROWS
assert struct.unpack_from('<I',demo,fileoff(demo,LYRIC_ROWS[0]))[0]==jal(payload_syms['ringnav_lyric_rows'])
class LyricRowsMachine(Machine):
    def __init__(self): super().__init__(); self.handlers[0x53b67c]='rows'
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='rows': return super().hook(u,address,size,unused)
        self.word(self.lrc+O['SCROLL_Y'],0); self.returns(0,clobber=True)
for y in (110,0):
    m=LyricRowsMachine(); m.lrc=m.node('scroll_view','scroll_lrc'); win=m.node('window','playing_page',[m.lrc])
    m.word(m.lrc+O['SCROLL_Y'],y); m.call(address=payload_syms['ringnav_lyric_rows'],args=(win,),gap=0)
    assert m.get(m.lrc+O['SCROLL_Y'])==y; passed()

# Albums grids and Folder view (GRID_COVER): stock's embedded art first; ffmpeg only for an Ogg track
# stock found none in, never for an MP3 or FLAC, run with parse_cover_mutex dropped. It writes dst.ff,
# renamed onto dst only when non-empty (an empty one, left on exit 0, is removed and dst kept); waitpid
# is retried on EINTR; a miss is remembered, so the track's next row forks nothing. The Albums grid's
# call reads the folder image (toolsGetExternCover) before ffmpeg, which then never runs for it.
from build import GRID_COVER
for call,_,fn in GRID_COVER:
    assert struct.unpack_from('<I',demo,fileoff(demo,call))[0]==jal(payload_syms[fn])
class GridCoverMachine(Machine):
    MOCKS=('toolsGetAlbumCover','toolsGetExternCover','fork@GLIBC_2.0','waitpid@GLIBC_2.0','__errno_location@GLIBC_2.0',
           'fopen@GLIBC_2.2','fseek@GLIBC_2.0','ftell@GLIBC_2.0','fclose@GLIBC_2.2','rename@GLIBC_2.0',
           'unlink@GLIBC_2.0','access@GLIBC_2.0','pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0',
           'calloc@GLIBC_2.0')
    def __init__(self):
        super().__init__(); self.mock(*self.MOCKS); self.errno=self.alloc(4); self.word(self.errno,4)  # EINTR
        self.files={}; self.handles={}; self.found=self.extern=0; self.out=None  # ffmpeg's output; None: no fork
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name not in self.MOCKS: return super().hook(u,address,size,unused)
        name=name.split('@')[0]; a,b=(u.reg_read(r) for r in REGS[:2]); ret=0; self.seen.append(name)
        if name=='toolsGetAlbumCover': ret=self.found
        elif name=='toolsGetExternCover': ret=self.extern
        elif name=='fork':
            ret=-1 if self.out is None else 7
            if self.out is not None: self.files[self.dst+'.ff']=self.out
        elif name=='waitpid':
            ret=-1 if self.seen.count('waitpid')==1 else a  # interrupted once, then reaped
            if ret==a: self.word(b,0)
        elif name=='__errno_location': ret=self.errno
        elif name=='fopen': ret=self.alloc(4) if self.text(a) in self.files else 0; self.handles[ret]=self.text(a)
        elif name=='ftell': ret=len(self.files[self.handles[a]])
        elif name=='rename': self.files[self.text(b)]=self.files.pop(self.text(a))
        elif name=='unlink': self.files.pop(self.text(a),None)
        elif name=='access': ret=0 if self.text(a).startswith('/mnt/mmc/A/') else -1
        elif name=='calloc': ret=self.alloc(a*b)
        self.returns(ret,clobber=True)
    def cover(self,track,fn='coverflow_grid_cover',**kw):
        for k,v in kw.items(): setattr(self,k,v)
        self.seen=[]; self.dst='/mnt/mmc/.sldp/x0.jpg'
        args=(self.string('/mnt/mmc/A/'+track),self.string(self.dst),110,110)
        return self.call(address=payload_syms[fn],args=args,gap=0)
m=GridCoverMachine()
for track,found in (('01.opus',1),('01.mp3',0),('01.flac',0),('opus',0)):
    assert m.cover(track,found=found)==found and m.seen==['toolsGetAlbumCover'],(track,m.seen)
m.files={m.dst:b'old'}
assert m.cover('01.OGG')==0 and m.seen==['toolsGetAlbumCover','calloc','pthread_mutex_unlock','fork','unlink',
                                         'pthread_mutex_lock'],m.seen  # fork refused: not a miss
assert m.cover('01.OGG')==0 and 'fork' in m.seen,m.seen  # so the next request tries again
assert m.cover('02.opus',out=b'')==0 and m.files=={m.dst:b'old'} and m.seen.count('waitpid')==2,(m.files,m.seen)
assert m.cover('02.opus',out=b'')==0 and m.seen==['toolsGetAlbumCover'],m.seen  # the miss is remembered
assert m.cover('03.opus',out=b'jpeg')==1 and m.files=={m.dst:b'jpeg'} and 'toolsGetExternCover' not in m.seen
assert m.seen.index('pthread_mutex_unlock')<m.seen.index('fork')<m.seen.index('pthread_mutex_lock')
assert m.cover('04.opus','coverflow_album_cover',extern=1)==1 and m.seen==['toolsGetAlbumCover','toolsGetExternCover']
assert m.cover('05.opus','coverflow_album_cover',extern=0)==1 and 'fork' in m.seen
passed()
# Folder view's folder row (coverflow_folder_cover): stock asks for each song in directory order (the order
# they were copied) until one has art; the folder's first song by name, as Folder view lists it, is tried
# first: a regular file (or one of unknown type) with the extension of stock's, whatever its case, and no
# dot file (a Mac's ._ copies). Through the walk the folder isn't listed again and its first song, without
# art, isn't tried twice; a hit ends the walk, and the next one lists the folder again. A path too long to
# keep, or with no extension, goes to stock as it is.
class FolderCoverMachine(GridCoverMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='toolsGetAlbumCover': return super().hook(u,address,size,unused)
        track=self.text(u.reg_read(REGS[0])); self.tried.append(track); self.returns(int(track in self.art),clobber=True)
    def folder(self,track):
        self.tried=[]; self.seen=[]
        dst=self.string('/mnt/mmc/.downpic/fd_x.jpg')
        return self.call(address=payload_syms['coverflow_folder_cover'],args=(self.string(track),dst,80,80),gap=0)
m=FolderCoverMachine(); A='/mnt/mmc/Музыка/Альбом'
m.scope_tree={A:[('05 Five.flac',8),('04 Four.flac',8),('cover.jpg',8),('00 Intro.mp3',8),('CD2',4),
                 ('._01 One.flac',0),('01 One.FLAC',0),('02 Two.flac',8)]}
m.art={A+'/01 One.FLAC',A+'/04 Four.flac'}
assert m.folder(A+'/05 Five.flac')==1 and m.tried==[A+'/01 One.FLAC'],m.tried
m.scope_tree[A].append(('00 Zero.flac',8)); m.art.add(A+'/00 Zero.flac')
assert m.folder(A+'/05 Five.flac')==1 and m.tried==[A+'/00 Zero.flac']  # the next walk lists again
far='/mnt/mmc/'+'x'*600+'/1.flac'; m.art.add(far)
assert m.folder(far)==1 and m.tried==[far]  # too long to keep: stock's song alone
assert m.folder('/mnt/mmc/Vol.2/notes')==0 and m.tried==['/mnt/mmc/Vol.2/notes']
m.art={A+'/04 Four.flac'}
assert m.folder(A+'/05 Five.flac')==0 and m.tried==[A+'/00 Zero.flac',A+'/05 Five.flac']
m.scope_tree[A].append(('0 New.flac',8)); m.art.add(A+'/0 New.flac')
assert m.folder(A+'/00 Zero.flac')==0 and m.tried==[]  # the same walk: not listed again, the first not tried twice
assert m.folder(A+'/04 Four.flac')==1 and m.tried==[A+'/04 Four.flac']
assert m.folder(A+'/05 Five.flac')==1 and m.tried==[A+'/0 New.flac']
m.art={'/mnt/mmc/Other/9.flac'}
assert m.folder('/mnt/mmc/Other/9.flac')==1 and m.tried==['/mnt/mmc/Other/9.flac']  # no listing: stock's song
passed()

# Coverflow depth (docs/internals.md#coverflow-depth): the payload's renderer, run as MIPS, draws
# byte for byte what the host build of the same source draws (test/coverflow.py checks that
# one's geometry); on the page it spans the frame, draws through the stock canvas and hit-tests taps.
BIG,BIG_SIZE=0x2000000,0x3000000  # the frames, the thumb's 6.8 MB texture pool and Home's large covers
def cover_pixels(seed,n=160*160):
    return b''.join(struct.pack('<I',0xff000000|((i*2654435761+seed*40503)>>7)&0xffffff) for i in range(n))
