# Scan Folders' row click (#254): a tap past stock's tick column on a folder with no folders inside moves to x 0, which
# ticks; one holding folders opens as it was, and a tap already in the column, or off any row, is never listed.
class SpecFolderMachine(Machine):
    LISTINGS={'/m/0':(3,1),'/m/1':(5,0),'/m/2':(0,0)}  # url: (toolsLoadDirectory's count, a folder among them)
    def __init__(self):
        super().__init__(True); self.listed=[]; self.seen=[]; self.row=self.alloc(0x100)
        for n in ('table_row_of','get_specfolderurl','toolsLoadDirectory','toolsCheckTypeDeque'): self.handlers[syms[n]]='spec:'+n
        self.handlers[int(manifest['patch_symbols']['stock_specfolder_click_trampoline'],16)]='spec:stock'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('spec:'): return super().hook(u,address,size,unused)
        name=name[5:]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='table_row_of': ret=a and self.row
        elif name=='get_specfolderurl': u.mem_write(b,f'/m/{a}'.encode()+b'\0')
        elif name=='toolsLoadDirectory': self.listed.append(self.text(a)); ret=self.LISTINGS[self.text(a)][0]
        elif name=='toolsCheckTypeDeque': assert b==4; ret=self.LISTINGS[self.listed[-1]][1]
        else: self.seen.append((a,self.get(b+O['EVENT_X'])))
        self.returns(ret)
    def tap(self,index,x,on_row=True):
        ev=self.alloc(0x30); self.word(ev+O['EVENT_X'],x); self.word(ev+O['EVENT_TARGET'],0x1000 if on_row else 0)
        self.word(self.row+O['ROW_INDEX'],index); self.call(address=HOOKS['specfolder_click'][0],args=(0x77,ev,0,0),gap=0); return self.listed,self.seen[-1]
for index,x,want,listed in ((0,200,200,['/m/0']),(1,200,0,['/m/1']),(2,81,0,['/m/2']),(1,80,80,[])):
    m=SpecFolderMachine(); assert m.tap(index,x)==(listed,(0x77,want)),(index,x,m.listed,m.seen)
m=SpecFolderMachine(); assert m.tap(1,200,on_row=False)==([],(0x77,200)); passed()
# Update Local Music: the stock scanner, nested folders and all, has no song limit: stock's 20,000 went with V1.36, and
# Vamp's own 65,000 with it. Only the folder listing, paths and database insert are mocked.
SONGS,STOP,LISTING=0xa50524,0xa50528,0xa50118  # toolsGetMusicNum's count, toolsStopUpdateMusic's flag, the listing deque
class ScanMachine(Machine):
    def __init__(self,tree,songs,patched=True,stop_at=None):
        super().__init__(patched); self.tree=tree; self.added=[]; self.stop_at=stop_at
        for n in ('toolsSetScanFolderPath','toolsCheckItemExist','deque_size','deque_at','sprintf@GLIBC_2.0'): self.handlers[syms[n]]='scan:'+n
        self.handlers[0x5d6520]='scan:list'; self.handlers[0x5da07c]='scan:insert'  # private: lists a folder, adds a song
        self.word(SONGS,songs); self.word(LISTING,self.alloc(0x20))
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('scan:'): return super().hook(u,address,size,unused)
        assert u.reg_read(UC_MIPS_REG_T9)==address
        name=name[5:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if name=='list':
            self.path=self.text(a); self.listing=[]
            for n in self.tree[self.path]:
                r=self.alloc(0x60); self.word(r+O['REC_NAME'],self.string(n))
                self.word(r+O['REC_TYPE'],4 if f'{self.path}/{n}' in self.tree else 8); self.listing.append(r)
        elif name=='deque_size': assert a==self.get(LISTING); ret=len(self.listing)
        elif name=='deque_at': ret=self.listing[b]
        elif name=='sprintf': assert self.text(b)=='%s/%s'; u.mem_write(a,f'{self.text(c)}/{self.text(d)}'.encode()+b'\0')
        elif name=='insert':
            self.added.append(f'{self.path}/{self.text(self.get(a+O["REC_NAME"]))}'); self.word(SONGS,self.get(SONGS)+1)
            if len(self.added)==self.stop_at: self.byte(STOP,1)  # Back on the Update Local Music dialog
        self.returns(ret)
def scan(songs,tree={'/m':['a']},**kw):
    """(return, songs added, stop flag, count): 1 finished, 0 stopped."""
    m=ScanMachine(tree,songs,**kw)
    return m.call(address=syms['toolsLoadAllFile'],args=(m.string('/m'),0,0,0),gap=0),m.added,m.u.mem_read(STOP,1)[0],m.get(SONGS)
for patched in (True,False):
    for songs in (19999,20000,32767,32768,64999,65000,65001):
        assert scan(songs,patched=patched)==(1,['/m/a'],0,songs+1),(patched,songs); passed()
nested={'/m':['a','sub','b'],'/m/sub':['c','d']}
assert scan(0,nested)==(1,['/m/a','/m/sub/c','/m/sub/d','/m/b'],0,4); passed()
assert scan(64998,nested)==(1,['/m/a','/m/sub/c','/m/sub/d','/m/b'],0,65002); passed()  # past 65,000 inside a subfolder
assert scan(0,nested,stop_at=2)==(0,['/m/a','/m/sub/c'],1,2); passed()  # stock's look at the stop flag before each entry
assert scan(0,{'/m':['a','b','c']},stop_at=1)==(0,['/m/a'],1,1); passed()
# A folder rescan refreshes existing tags in place, including quoted paths/tags and CUE rows.
# Run the actual recursive scanner and refresh wrapper against SQLite; full scans still add songs.
from build import SCAN_REFRESH
assert struct.unpack_from('<I',demo,fileoff(demo,SCAN_REFRESH[0]))[0]==jal(payload_syms['ringnav_scan_refresh'])
class FolderTagsMachine(ScanMachine):
    def __init__(self,patched=True,cue=False,valid=True,songs=1,title="New artist's tags"):
        super().__init__({'/m':["it's.flac"]},songs,patched=patched)
        self.valid=valid; self.cue=cue; self.title=title; self.reads=0; self.updates=[]
        self.db=sqlite3.connect(':memory:')
        self.db.execute('create table songtable(id primary key,filename,fileurl,album,songer,style,name,composer,albumsonger,year,track,cd_num,time,time_end,time_create,Initial)')
        self.old=(7,"it's.flac","/m/it's.flac",'Old album','Old artist','Old genre','Old title','Old composer','Old album artist',1990,1,1,10 if cue else 0,20 if cue else 0,123,ord('O'))
        self.db.execute('insert into songtable values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',self.old)
        # Another CUE segment in the same file must keep its own tags.
        self.db.execute('insert into songtable values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(8,*self.old[1:12],30,40,456,ord('O')))
        for n in ('taglib_file_new','taglib_file_tag','taglib_file_free','toolsGetMusicInfo','toolsUpdateDbTable','toolsTrimLeft','toolsTrimRight','toolsHanziToPinyin'):
            self.handlers[syms[n]]='refresh:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        a,b,c,d=[u.reg_read(r) for r in REGS]
        if name=='scan:toolsCheckItemExist':
            # The real folder listing includes the path and any parsed CUE tags.
            self.word(a+O['REC_PATH'],self.string("/m/it's.flac"))
            if self.cue:
                for off,v in ((0x48,10),(0x4c,20),(0x30,2026),(0x3c,3),(0x40,2)): self.word(a+off,v)
                for off in (0x14,0x18,0x1c,0x10,0x20,0x24): self.word(a+off,self.string("CUE's tags"))
            ret=self.text(self.get(a+O['REC_NAME']))!="new.flac"
        elif name.startswith('refresh:'):
            name=name[8:]; ret=0
            if name=='taglib_file_new':
                assert b==0  # TagLib's vendor ABI: tags only, no audio-properties scan.
                ret=123 if self.valid else 0
            elif name=='taglib_file_tag': assert a==123; ret=456
            elif name in ('toolsTrimLeft','toolsTrimRight'): u.mem_write(a,self.text(a).strip().encode()+b'\0')
            elif name=='toolsHanziToPinyin': u.mem_write(b,self.text(a).encode()+b'\0')
            elif name=='toolsGetMusicInfo':
                self.reads+=1; assert (a,self.text(c))==(0,"/m/it's.flac"); ret=1
                for off in (0,0x100,0x200,0x280,0x480,0x580): u.mem_write(b+off,(self.title if off==0x280 else "New artist's tags").encode()+b'\0')
                for off,v in ((0x680,2026),(0x684,3),(0x688,2)): self.word(b+off,v)
            elif name=='toolsUpdateDbTable':
                assert self.text(a)=='/mnt/data/database.db'; self.updates.append(self.text(b)); self.db.execute(self.text(b)); ret=1
        else: return super().hook(u,address,size,unused)
        self.returns(ret)
    def rescan(self):
        return self.call(address=syms['toolsLoadAllFile'],args=(self.string('/m'),self.string('songtable'),1,0),gap=0)
for patched in (False,True):
    for cue in (False,True):
        for valid in (False,True):
            m=FolderTagsMachine(patched,cue,valid); assert m.rescan()==1 and not m.added and m.get(SONGS)==1
            row=m.db.execute('select * from songtable where id=7').fetchone()
            changed=patched and (cue or valid)
            assert row==((7,*m.old[1:3],*(["CUE's tags" if cue else "New artist's tags"]*6),2026,3,2,*m.old[12:15],ord('C' if cue else 'N')) if changed else m.old)
            assert m.db.execute('select * from songtable where id=8').fetchone()==(8,*m.old[1:12],30,40,456,ord('O'))
            assert len(m.updates)==changed and m.reads==(patched and not cue and valid); passed()
for title,letter in (("  lower title  ",'L'),('7 songs','*'),('!song','#'),('','I'),('(null)','I')):
    m=FolderTagsMachine(title=title); assert m.rescan()==1
    assert m.db.execute('select Initial from songtable where id=7').fetchone()==(ord(letter),); passed()
m=FolderTagsMachine(songs=65000)  # a folder scan adds a new file at any count, and refreshes the old one beside it
m.tree['/m']=['new.flac',"it's.flac"]
assert m.rescan()==1 and m.added==['/m/new.flac'] and m.get(SONGS)==65001 and not m.u.mem_read(STOP,1)[0]
assert m.db.execute('select albumsonger from songtable where id=7').fetchone()==("New artist's tags",)
passed()
# A song's date (toolsGetFileCreateTime) is the card's creation stamp from statx, which reads never
# move (#170, #188); stock's own date stands in without one: a failed call, no btime or a zero stamp.
for btime,mask,error,expect in ((1700000000,0x800,0,1700000000),(1700000000,0x7ff,0,0),(0,0x800,0,0),(1700000000,0x800,2,0)):
    m=Machine(); m.handlers[HOOKS['toolsGetFileCreateTime'][0]+12]='stock_created'; seen=[]
    def statx(u,interrupt,unused):
        a,b,c,d=[u.reg_read(r) for r in REGS]; buf=m.get(u.reg_read(UC_MIPS_REG_SP)+16)  # o32's fifth argument
        seen.append((u.reg_read(UC_MIPS_REG_V0),a,m.text(b),c,d))
        if error: u.reg_write(UC_MIPS_REG_A3,1); u.reg_write(UC_MIPS_REG_V0,error); return
        u.mem_write(buf,bytes(256)); u.mem_write(buf,struct.pack('<I',mask)); u.mem_write(buf+80,struct.pack('<Q',btime))  # the kernel writes all 256
        u.reg_write(UC_MIPS_REG_A3,0); u.reg_write(UC_MIPS_REG_V0,0)
    m.u.hook_add(UC_HOOK_INTR,statx); path=m.string('/m/a.flac')
    assert m.call(address=payload_syms['ringnav_file_created'],args=(path,0,0,0),gap=0)==expect
    assert seen==[(4366,0xffffff9c,'/m/a.flac',0,0x800)] and [c[1] for c in m.calls if c[0]=='stock_created']==([] if expect else [path]); passed()
# Library Artists rows hide the head picture (CLASS_ICON); Composers and Genres, sharing the rebound
# rows, hide theirs too under OLED's one-line rows (text_art 0), and stock's setter never runs.
from build import CLASS_ICON
assert struct.unpack_from('<I',demo,fileoff(demo,CLASS_ICON))[0]==jal(payload_syms['ringnav_class_icon'])
m=Machine(); m.mock('class_icon'); icon=m.node('image','img_icon'); row=m.node('view','',[icon])
for cls,shown in ((0xf004,0),(0xf006,0),(0xf005,0),(0xf004,0)):  # no icon on a one-line row
    m.word(syms['g_class_type'],cls); m.calls=[]
    m.call(address=payload_syms['ringnav_class_icon'],args=(row,3,0,0),gap=0)
    assert m.nodes[icon]['visible']==shown and [c[1:3] for c in m.calls if c[0]=='class_icon']==([(row,3)] if shown else [])
passed()
# A song with no artist tag (an empty REC_ARTIST): under OLED's set (text_art 0) the second line is empty
# (ringnav_song_unknown), and a rebound row loses the last song's artist; a record that says Unknown reads so.
from build import SONG_UNKNOWN_CALL
assert struct.unpack_from('<I',demo,fileoff(demo,SONG_UNKNOWN_CALL))[0]==jal(payload_syms['ringnav_song_unknown'])
class SongBindMachine(Machine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='b:format':
            self.formats.append(self.text(u.reg_read(UC_MIPS_REG_A0))); self.u.mem_write(u.reg_read(UC_MIPS_REG_A1),b'Unknown Artist\0'); self.returns(0); return
        return super().hook(u,address,size,unused)
assert OLED_G.text_art==0
m=SongBindMachine(); oled_boot(m); m.formats=[]; m.handlers[syms['getFormatString']]='b:format'
m.mock('deque_at','tk_snprintf','widget_set_text_utf8','widget_use_style','file_is_playing','toolsTrimLeft','toolsTrimRight')
title,meta,badge=(m.node('hscroll_label','scrlabel_name'),m.node('hscroll_label','scrlabel_artist'),m.node('image','img_pic'))
view_info=m.node('view','view_info',[title,meta,badge]); button=m.node('button','',[view_info]); m.row_record=m.alloc(0x80)
m.mock('list_get_img_pic'); m.word(m.row_record+8,m.string('Song')); m.word(m.row_record+12,m.string('/mnt/mmc/x.flac')); m.word(m.row_record+0x48,1)
for artist,text in (('Mac Quayle','Mac Quayle'),('',''),('Unknown','Unknown'),('','')):
    m.word(m.row_record+0x18,m.string(artist))
    rc=m.call(address=0x4ab208,args=(button,0,0,0)); assert rc==0,(rc,artist,[c[:2] for c in m.calls][-6:])
    assert m.nodes[meta].get('text')==text,(artist,m.nodes[meta].get('text'))
assert m.formats==[],m.formats
passed()
# Library sorts: both stock name comparators, on the patched trim calls, skip a leading article.
from build import SORT_TRIMS
for a in SORT_TRIMS: assert struct.unpack_from('<I',demo,fileoff(demo,a))[0]==jal(payload_syms['ringnav_sort_key'])
class SortMachine(Machine):
    def __init__(self):
        super().__init__(); self.handlers.pop(syms['toolsTrimLeft']); self.mock('strlen@GLIBC_2.0','snprintf@GLIBC_2.0')
        for n in ('__ctype_b_loc@GLIBC_2.3','__ctype_tolower_loc@GLIBC_2.3','strncmp@GLIBC_2.0','atoi@GLIBC_2.0',
                  'strcasecmp@GLIBC_2.0','strcmp@GLIBC_2.0','strncpy@GLIBC_2.0'): self.handlers[syms[n]]='s:'+n
        b=self.alloc(0x400); t=self.alloc(0x400)  # glibc's tables from -128: the digit class, ASCII lowercase
        for c in range(-128,256):
            self.u.mem_write(b+256+2*c,struct.pack('<H',0x800*(48<=c<58)))
            self.word(t+512+4*c,ord(chr(c).lower()) if 0<=c<128 else c)
        self.word(b,b+256); self.word(t,t+512); self.tables=(b,t)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('s:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c=[u.reg_read(r) for r in REGS[:3]]
        s=lambda x,n=None: bytes(u.mem_read(x,4096)).split(b'\0')[0][:n]
        if name.startswith('__ctype'): ret=self.tables[name=='__ctype_tolower_loc']
        elif name=='atoi': ret=int(re.match(rb'\d*',s(a))[0] or 0)
        elif name=='strncpy': u.mem_write(a,s(b,c).ljust(c,b'\0')); ret=a
        else: x,y=(s(a,c),s(b,c)) if name=='strncmp' else (s(a),s(b)); x,y=(x.lower(),y.lower()) if name=='strcasecmp' else (x,y); ret=(x>y)-(x<y)
        self.returns(ret)
m=SortMachine()
def sort_rec(name):
    r=m.alloc(0x60); m.word(r+0x10,m.string(name)); m.word(r+8,m.string(name+'.flac')); return r
def ordered(names,cmp):
    out=m.alloc(4)
    def less(x,y):  # the comparator stores x < y
        m.call(address=cmp,args=(x,y,out,0),gap=0,count=2_000_000); return m.get(out)
    rs={sort_rec(n):n for n in names}
    return [rs[r] for r in sorted(rs,key=cmp_to_key(lambda x,y: -1 if less(x,y) else 1 if less(y,x) else 0))]
names=['Daft Punk','The Cure','Coldplay','Them Crooked Vultures','A Tribe Called Quest','Anthrax','The','an Orchestra','Toto']
for cmp in (0x5cd188,0x5cc870):
    assert ordered(names,cmp)==['Anthrax','Coldplay','The Cure','Daft Punk','an Orchestra','The','Them Crooked Vultures','Toto','A Tribe Called Quest'],ordered(names,cmp)
    passed()
key=lambda t: (m.u.mem_write(0x1100000,t.encode()+b'\0'), m.call(address=payload_syms['ringnav_sort_key'],args=(0x1100000,0,0,0),gap=0), m.text(0x1100000))[2]
assert [key(t) for t in ('  The Cure','THE CURE','The ','The  Cure','A','An ','Ant','周杰伦','The 周杰伦')]==['Cure','CURE','The ','The  Cure','A','An ','Ant','周杰伦','周杰伦']; passed()
# iPod boots to Home: home_page_init's memory-play resume, run from its stock context build, starts
# the restored queue paused (mode 3) through player_start as playing_page_init would, and opens no
# page; a 0xff class starts nothing, as the page's init. Car mode (mode 2) opens Now Playing as stock.
boot=payload_syms['ringnav_boot']
assert struct.unpack_from('<I',demo,fileoff(demo,0x534cf8))[0]==jal(boot)
for car,cls,want in ((0,1,[(0x5550,4,1,3)]),(0,0xff,[]),(1,0xf003,[])):
    m=Machine(); m.word(syms['g_memory_info'],cls); m.byte(syms['g_carmode'],car)
    m.u.reg_write(UC_MIPS_REG_GP,0xa3aae0); m.u.reg_write(UC_MIPS_REG_SP,0x7000f000)
    m.u.reg_write(UC_MIPS_REG_S0,0x5550); m.u.reg_write(UC_MIPS_REG_S1,4)
    # Headphones Required on: stock's test ahead of the resume (0x534c44) skipped it, whatever was connected; it
    # falls through to Memory Playback's own test now (0x534c54), and the context's build runs as with it off.
    m.byte(syms['g_block_withoutphone_flag'],1); m.u.emu_start(0x534c44,0x534c58,count=10)
    assert 0x534c50<=m.u.reg_read(UC_MIPS_REG_PC)<0x534c5c  # not 0x534d40, past the resume
    m.u.emu_start(0x534cc4,0x534d00,count=1000)
    nav=[c for c in m.calls if c[0]=='navigator_to_with_context']
    assert m.started==want
    if car:
        assert len(nav)==1 and m.text(nav[0][1])=='playing_page' and nav[0][2]==0x7000f018
        assert [m.get(0x7000f018+4*i) for i in range(4)]==[0x5550,4,0xf003,2]
    else: assert not nav
    passed()
# Refusals leave the queue alone: a stream queue, or a row whose record changed under the menu.
m=QueueMachine(); m.word(O['MCL_TYPE'],2); assert m.run(0)=='Queue unchanged' and m.names()==['A','B','C']
m=QueueMachine(); m.press(1); m.hold(); m.release(); m.word(m.row(0)+O['REC_NAME'],m.string('other'))
m.pick(0); m.advance(0); assert m.toasts[-1][3]=='Queue unchanged' and m.names()==['A','B','C']; passed()
# Unsupported pages and states keep the stock long key: no menu, and the release is stock.
for setup,toggles in ((lambda m:m.byte(syms['g_lockscreen_pageflag'],1),1),(lambda m:m.byte(syms['g_backlight_status'],0),1),
        (lambda m:m.byte(syms['g_navbar_status'],1),1),
        (lambda m:m.word(m.surface+O['TABLE_ROWS'],7),1),(lambda m:m.word(m.row(0)+O['REC_TYPE'],4),1),
        (lambda m:setattr(m,'airplay',2),0),
        (lambda m:setattr(m,'top',m.node('window','home_page',[m.node()])),1)):
    m=QueueMachine(); setup(m); m.press(3)
    assert m.hold()==0 and len(m.stack)==1 and m.release()==toggles
    passed()
# Unknown rows (docs/internals.md#unknown-rows): stock's list (here the showlist as built, its size
# returned) keeps its trailing Unknown row (id -1) only when the query its press runs finds a song;
# the staging deque is left cleared. Other rows and classes stay stock.
class ClassMachine(QueueMachine):
    tramp=int(manifest['patch_symbols']['stock_localclass_trampoline'],16)
    split=[]  # Albums' own grouping (split), which toolsQueryDbTable stages
    def __init__(self,**kw): super().__init__(**kw); self.word(0xa3a270,0x1000010)  # free's GOT slot, as Coverflow's
    def hook(self,u,address,size,unused):
        if address==syms['toolsQueryDbTable']:
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in self.split]
            self.returns(len(self.split)); return
        libc={syms['strdup@GLIBC_2.0']:'strdup',0x1000010:'free'}
        if address in libc:  # the untagged rows' titles (ringnav_localclass)
            a=u.reg_read(REGS[0]); self.returns(self.string(self.text(a)) if libc[address]=='strdup' else 0); return
        if address!=self.tramp: return super().hook(u,address,size,unused)
        self.calls.append(('stock_localclass',u.reg_read(REGS[0]),0,0))
        assert self.get(payload_syms['library_music']) == 1
        self.returns(len(self.items(self.get(syms['p_deque_showlist']))))
def unknown(cls,found,last=-1,artist=0,rows=3):
    m=ClassMachine(page='localclass_page',cls=0xf001,rows=rows); m.found=[m.song('T1')]*found; m.query=None
    m.word(syms['artist_type'],artist); m.word(syms['g_artist_type'],1-artist)
    if rows: m.word(m.row(rows-1)+O['REC_ID'],last)
    n=m.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)
    assert m.get(payload_syms['library_music']) == 0
    shown=m.names(m.get(syms['p_deque_showlist']))
    # Artists follow the artist page's own switch (artist_type), which stock never copied to g_artist_type
    assert n==len(shown) and m.calls[0][:2]==('stock_localclass',cls) and m.get(syms['g_artist_type'])==artist
    return shown,m.query,m.items(m.get(syms['tools_pdeq_directory']))
for cls,artist,query in ((0xf003,0,'getMusicByAlbum'),(0xf004,0,'getMusicBySonger'),(0xf004,1,'getMusicByAlbumArtist'),
                         (0xf005,0,'getMusicByComposer'),(0xf006,0,'getMusicByGenre')):
    shown,q,staging=unknown(cls,0,artist=artist)
    assert shown==['Row 0','Row 1'] and q[:2]==(query,None) and not staging, (cls,artist)
    shown,q,staging=unknown(cls,1,artist=artist)
    assert shown==['Row 0','Row 1','Row 2'] and q[:2]==(query,None) and not staging, (cls,artist); passed()
for cls,last,rows in ((0xf004,1,3),(0xff01,-1,3),(0xff10,-1,3),(0xff11,-1,3),(0xf007,-1,3),(0xf004,-1,0)):
    shown,q,_=unknown(cls,0,last=last,rows=rows)
    assert len(shown)==rows and q is None, (cls,last,rows); passed()
# The check needs one song, not all of them: its query (stock's where tag="", through the Library
# wrapper, its SQL run here) reads the first at most, and the row stays exactly when a music song
# lacks the tag. The Queue menu's query on that row still reads every one.
class FirstMachine(ClassMachine):
    dbquery=int(manifest['patch_symbols']['stock_dbquery_trampoline'],16)
    def __init__(self,untagged,**kw):
        super().__init__(**kw); self.mock('strstr@GLIBC_2.0'); self.read=[]
        self.db=sqlite3.connect(':memory:'); self.db.execute('create table songtable(id,album,songer,albumsonger,composer,style,fileurl)')
        self.db.executemany('insert into songtable values(?,?,?,?,?,?,?)',[(1,'A','B','C','D','E','/mnt/mmc/Music/a.flac'),
            (2,'','','','','','/mnt/mmc/Audiobooks/b.mp3')]+[(3+i,'','','','','',f'/mnt/mmc/Music/{i}.flac') for i in range(untagged)])
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name.startswith('q:getMusicBy') and not u.reg_read(REGS[0]):
            tag={'Album':'album','Songer':'songer','AlbumArtist':'albumsonger','Composer':'composer','Genre':'style'}[name[12:]]
            sql=self.string(f'select * from songtable where {tag}=""')
            self.returns(self.nested(payload_syms['ringnav_library_query'],self.string('/mnt/data/database.db'),sql,0,0)); return
        if address!=self.dbquery: return super().hook(u,address,size,unused)
        rows=self.db.execute(self.text(u.reg_read(REGS[1]))).fetchall(); self.read.append(len(rows))
        self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',self.song(f'S{r[0]}')) for r in rows]
        self.returns(len(rows))
for cls,artist in ((0xf003,0),(0xf004,0),(0xf004,1),(0xf005,0),(0xf006,0)):
    for untagged in (0,1,2):
        m=FirstMachine(untagged,page='localclass_page',cls=0xf001,rows=3); m.word(m.row(2)+O['REC_ID'],-1)
        m.word(syms['artist_type'],artist)
        assert m.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)==2+(untagged>0)
        assert m.read==[min(untagged,1)],(cls,artist,untagged,m.read)
    passed()
m=FirstMachine(2,page='localclass_page',cls=0xf004,rows=3); m.word(m.row(2)+O['REC_ID'],-1)  # the flag ends with its check
assert m.call(address=HOOKS['load_localclass_list'][0],args=(0xf004,0,0,0),gap=0)==3
m.word(m.row(0)+O['REC_ID'],-1); m.run(1)
assert m.read==[1,2],m.read; passed()
# The empty state's titles shown (feedback.c empty_state: its view's _es_title), in the order made.
def empty_titles(m): return [n.get('_es_title') for w,n in m.nodes.items() if n.get('name')=='_empty_state' and n.get('visible',1) and (not callable(getattr(m,'alive',None)) or m.alive(w))]  # SpotMachine's alive is its socket's flag
# An empty class list: its page, once the window manager marks it opened (stage 3, after the open
# animation), is force-closed from the next loop pass, then the empty state's page (navigation.c empty_class,
# feedback.c empty_open) says so. Until then it is polled (EMPTY_POLL_MS) until it opens or stops being on top, then
# the empty state's page alone; a page already left, or another class's, keeps that page only.
class EmptyClassMachine(ClassMachine):
    """A class list whose empty state opens a page of its own (feedback.c empty_open): window_create and the widget
    factories make nodes, as CoverflowMachine's do."""
    MAKE=('window_create','widget_factory_create_widget','list_view_create','scroll_view_create')
    def __init__(self,**kw):
        super().__init__(**kw)
        for n in self.MAKE: self.handlers[syms[n]]='c:'+n
        self.present.add('/tmp/mmc_add')  # a card in: an empty list says why (feedback.c empty_card)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')[2:]
        if name not in self.MAKE: return super().hook(u,address,size,unused)
        a,b,c,d=[u.reg_read(r) for r in REGS]; sp=u.reg_read(UC_MIPS_REG_SP); self.calls.append((name,a,b,c))
        kind,parent,h={'window_create':('window',0,0),'widget_factory_create_widget':(self.text(b),c,self.get(sp+24))}.get(
            name,(name.removesuffix('_create'),a,self.get(sp+16)))
        ret=self.node(kind)
        if kind=='window': self.top=ret; self.stack.append(ret); self.word(ret+O['W_W'],375); self.word(ret+O['W_H'],320)  # full-bleed (FULL_BLEED)
        else: self.nodes[parent]['children'].append(ret); self.word(ret+O['W_PARENT'],parent); self.word(ret+O['W_H'],h)
        if name=='widget_factory_create_widget':  # x in a3; y, w on the stack
            for off,v in (('W_X',d),('W_Y',self.get(sp+16)),('W_W',self.get(sp+20))): self.word(ret+O[off],v)
        if kind=='list_view': self.word(ret+O['W_W'],d)
        self.returns(ret,clobber=True)
def empty(cls,page,stage=O['WINDOW_STAGE_OPENED'],leave=False,shown=None):
    m=EmptyClassMachine(page=page,cls=cls,rows=0); m.found=[]; m.query=None
    assert m.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)==0 and len(m.timers)==1
    m.nodes[m.top]['stage']=stage
    if shown is not None: m.word(syms['g_class_type'],shown)
    if leave: m.top=m.node('window','localmusic_page')
    m.calls=[]; m.advance(0)
    return m
closes=lambda m: [c[:3] for c in m.calls if c[0]=='window_manager_close_window_force']
for cls,page in ((0xf003,'album_page'),(0xf003,'localclass_page'),(0xf004,'localclass_page'),(0xf005,'localclass_page'),(0xf006,'localclass_page')):
    m=empty(cls,page); top=next(w for w in m.stack if m.nodes[w]['name']==page)  # the class page, under the empty state's
    assert closes(m)==[('window_manager_close_window_force',m.wm,top)]
    assert not m.toasts and empty_titles(m)==[{0xf003:'No Albums',0xf004:'No Artists',0xf005:'No Composers',0xf006:'No Genres'}[cls]] and not m.timers
    passed()
m=empty(0xf003,'album_page',stage=2)  # created, its open animation still running
assert not closes(m) and not empty_titles(m) and len(m.timers)==1
m.advance(O['EMPTY_POLL_MS']*5); assert not empty_titles(m) and len(m.timers)==1
m.nodes[m.top]['stage']=O['WINDOW_STAGE_OPENED']; m.calls=[]; m.advance(O['EMPTY_POLL_MS'])
assert closes(m) and empty_titles(m) and not m.timers; passed()
m=empty(0xf003,'album_page',stage=2); m.advance(O['EMPTY_POLL_MS']*100); assert not empty_titles(m) and len(m.timers)==1  # slow to open: still waits
m.top=m.node('window','localmusic_page'); m.calls=[]; m.advance(O['EMPTY_POLL_MS'])
assert not closes(m) and empty_titles(m) and not m.timers; passed()  # left before it opened: the empty state's page alone
m=empty(0xf003,'album_page',leave=True)
assert not closes(m) and empty_titles(m) and not m.timers; passed()
m=empty(0xf004,'localclass_page',shown=0xf006)  # Genres' page on top by now: not Artists' to close
assert not closes(m) and empty_titles(m)==['No Artists']; passed()
# Library -> Songs reads straight into the showlist: stock's load holds its copy and the staging deque
# together, the library twice. The staging pointer comes back, its deque cleared as stock leaves it.
class SongsMachine(ClassMachine):
    def hook(self,u,address,size,unused):
        if address==syms['getAllMusic']:
            self.calls.append(('getAllMusic',u.reg_read(REGS[0]),self.get(payload_syms['library_music'])))
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in self.found]
            self.returns(len(self.found)); return
        if address==syms['music_nagvation_title']:
            self.calls.append(('title',u.reg_read(REGS[0]),self.names(self.get(syms['p_deque_showlist'])))); self.returns(0); return
        super().hook(u,address,size,unused)
m=SongsMachine(page='localclass_page',cls=0xf001,rows=3); m.found=[m.song('T1'),m.song('T2')]; m.word(syms['g_sort_flag'],2)
staging=m.get(syms['tools_pdeq_directory']); m.deqs[staging][1]=[m.song('stale')]; m.calls=[]
assert m.call(address=HOOKS['load_localclass_list'][0],args=(0xf001,0,0,0),gap=0)==2
assert m.names(m.get(syms['p_deque_showlist']))==['T1','T2'] and m.get(syms['tools_pdeq_directory'])==staging and not m.items(staging)
assert ('getAllMusic',2,1) in m.calls and ('title',0xf001,['T1','T2']) in m.calls
assert not [c for c in m.calls if c[0] in ('stock_localclass','deque_assign','deque_init_copy')]; passed()
# Songs' cover tasks: stock's albumcoverinfo_init at the page's open (0x4acda8) and both branches of
# its return (0x4ab788 reloads, 0x4ab830 keeps the list) malloc'd each row's name and path for a task
# that the cover list's copy (0x5c639c) duplicates, and never freed them. With malloc and free
# counted, and the list's elements copied and destroyed (0x5c5c48) as libcstl does, what stays
# allocated after each is what the list holds.
class SongCoversMachine(QueueMachine):
    def __init__(self):
        super().__init__(rows=0)
        self.live=set(); self.result=self.alloc(4)
        self.handlers.pop(syms['albumcoverinfo_init'])
        self.word(0xa3a0e8,0x1000014); self.handlers[0x1000014]='c:malloc'  # lazy-bound, as RolloverMachine's
        self.handlers[0x1000010]='c:free'; self.handlers[syms['strcpy@GLIBC_2.0']]='c:strcpy'
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0','clear_albumcover_task')
        self.word(syms['pdeq_albumcoverlist'],self.deque([],'stAlbumCoverInfo'))
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a,b=u.reg_read(REGS[0]),u.reg_read(REGS[1])
        cover=name in ('q:_deque_push_back','q:deque_clear') and self.deqs[a][0]=='stAlbumCoverInfo'
        if not cover and not name.startswith('c:'): return super().hook(u,address,size,unused)
        ret=0
        if name=='c:malloc': ret=self.alloc(a); self.live.add(ret)
        elif name=='c:free': assert not a or a in self.live, 'freed twice'; self.live.discard(a)
        elif name=='c:strcpy': u.mem_write(a,self.raw(b)+b'\0'); ret=a
        elif name=='q:_deque_push_back':
            e=self.alloc(12); u.mem_write(e,bytes(12)); self.nested(0x5c639c,e,b,self.result); self.items(a).append(e)
        else:
            for e in self.items(a): self.nested(0x5c5c48,e,self.result)
            self.deqs[a][1]=[]
        self.returns(ret,clobber=True)
m=SongCoversMachine(); rows=[m.song(f'Row {i}') for i in range(3)]
for i in (0,2): m.word(rows[i]+O['REC_ALBUM'],m.string(f'Album {i}'))
m.word(rows[1]+O['REC_ALBUM'],0)  # no name: no copy
m.word(syms['p_deque_showlist'],m.deque(rows))
for start,end in ((0x4acda4,0x4acdb0),(0x4ab780,0x4ab790),(0x4ab828,0x4ab838)):
    m.u.reg_write(UC_MIPS_REG_SP,0x7000f000); m.u.reg_write(UC_MIPS_REG_GP,0xa3aae0)
    m.u.reg_write(UC_MIPS_REG_T9,syms['albumcoverinfo_init'])
    m.u.emu_start(start,end,count=m.budget); assert m.u.reg_read(UC_MIPS_REG_PC)==end
    covers=m.items(m.get(syms['pdeq_albumcoverlist']))
    assert [(m.get(c+4) and m.text(m.get(c+4)),m.text(m.get(c+8))) for c in covers]==[
        ('Album 0','/p/Row 0'),(0,'/p/Row 1'),('Album 2','/p/Row 2')]
    held={m.get(c+off) for c in covers for off in (4,8)}-{0}
    assert m.live==held, (hex(end-8),len(m.live-held),'blocks nothing holds')
passed()
# Folder view's foldercoverinfo_init builds the same task for a folder's or a song's row, at its own push.
m=SongCoversMachine(); m.handlers.pop(syms['foldercoverinfo_init'],None)
rows=m.deque([m.song('Sub',kind=4),m.song('Song'),m.song('Skipped',kind=1)])
m.call(address=syms['foldercoverinfo_init'],args=(rows,0,0,0),gap=0)
covers=m.items(m.get(syms['pdeq_albumcoverlist']))
assert [(m.text(m.get(c+4)),m.text(m.get(c+8))) for c in covers]==[('Sub','/p/Sub'),('Song','/p/Song')]
assert m.live=={m.get(c+off) for c in covers for off in (4,8)},(len(m.live),'blocks nothing holds'); passed()
# Every Library class and detail load scopes SQL, including empty results and failures, and
# restores an already-scoped caller. The trampoline supplies stock's displayed copies.
class ScopedClassMachine(ClassMachine):
    def hook(self,u,address,size,unused):
        super().hook(u,address,size,unused)
        if address==self.tramp and getattr(self,'failed',False): self.returns(-1)
for cls in (*range(0xf001,0xf00c),*range(0xff01,0xff15),0xff20):
    for scoped,rows,failed in ((0,1,False),(1,0,False),(1,0,True),(0,0,True)):
        m=ScopedClassMachine(rows=rows); m.failed=failed
        m.word(payload_syms['library_music'],scoped)
        m.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)
        assert m.get(payload_syms['library_music'])==scoped
# Saved Favourites and playlist entries stay stored, in order, including spoken-only lists.
paths=['/mnt/mmc/pOdCaStS/Show/episode.mp3','/mnt/mmc/Music/song.flac',
       '/mnt/mmc/AUDIOBOOKS/chapter.mp3','/mnt/mmc/elsewhere/BOOK.M4B',
       '/mnt/mmc/Podcasts Extra/song.mp3','/mnt/mmc/Music/Podcasts/song.mp3']
for cls in (0xf00a,0xff20):
    for selected in (paths,[paths[0],paths[2],paths[3]]):
        m=ClassMachine(rows=len(selected))
        for i,path in enumerate(selected):
            m.word(m.row(i)+O['REC_PATH'],m.string(path))
            m.word(m.row(i)+O['REC_TITLE'],m.string('Tagged'))
        stored=m.deque(m.items(m.get(syms['p_deque_showlist'])))
        before=[bytes(m.u.mem_read(r,0x60)) for r in m.items(stored)]
        n=m.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)
        want=[f'Row {i}' for i,path in enumerate(selected) if cls==0xf00a or path in paths[1:2]+paths[4:]]
        assert m.names(m.get(syms['p_deque_showlist']))==want and n==len(want)
        assert [bytes(m.u.mem_read(r,0x60)) for r in m.items(stored)]==before
# Discovery stays playable in explicit playlists but is hidden from stored library views.
discovery_paths=['/mnt/mmc/Discovery/weekly/song.flac', '/mnt/mmc/dIsCoVeRy/track.flac',
                 '/mnt/mmc/Discovery Extra/song.flac', '/mnt/mmc/Music/Discovery/song.flac',
                 '/mnt/mmc/Discovery.flac', '/mnt/mmc/Music/keeper.flac']
for cls in (0xf00a,0xf008,0xf007,0xff20):
    for selected in (discovery_paths,discovery_paths[:2]):
        m=ClassMachine(rows=len(selected))
        m.scope_tree={'/mnt/mmc':[('Discovery',4)],'/mnt/mmc/Discovery':[]}
        m.scope_markers={'/mnt/mmc/Discovery/database.ignore'}; m.scope_scan()
        for i,path in enumerate(selected):
            m.word(m.row(i)+O['REC_PATH'],m.string(path))
            m.word(m.row(i)+O['REC_TITLE'],m.string('Tagged'))
        stored=m.deque(m.items(m.get(syms['p_deque_showlist'])))
        before=[bytes(m.u.mem_read(r,0x60)) for r in m.items(stored)]
        n=m.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)
        want=[f'Row {i}' for i in range(len(selected)) if cls==0xff20 or i>=2]
        assert n==len(want) and m.names(m.get(syms['p_deque_showlist']))==want
        assert [bytes(m.u.mem_read(r,0x60)) for r in m.items(stored)]==before
        assert m.get(payload_syms['library_music'])==0
passed()
# A playlist container remains even when its name/path looks like spoken audio.
m=ClassMachine(rows=1); m.word(m.row(0)+O['REC_TYPE'],4)
m.word(m.row(0)+O['REC_PATH'],m.string(paths[0]))
assert m.call(address=HOOKS['load_localclass_list'][0],args=(0xf00b,0,0,0),gap=0)==1
passed()
# Playlists' rows go to p_deque_playlist, so stock's count can outrun the showlist (empty after boot):
# the untitled-row pass reads the showlist's own rows only (also library_poll's import, which loads it).
class PlaylistsMachine(ClassMachine):
    def hook(self,u,address,size,unused):
        super().hook(u,address,size,unused)
        if address==self.tramp: self.returns(18)
for rows in (0,2):
    m=PlaylistsMachine(rows=rows)
    if rows: m.word(m.row(0)+O['REC_NAME'],m.string('a.flac')); m.word(m.row(0)+O['REC_PATH'],m.string('/p/a.flac'))
    assert m.call(address=HOOKS['load_localclass_list'][0],args=(0xf00b,0,0,0),gap=0)==18
    if rows: assert m.text(m.get(m.row(0)+O['REC_TITLE']))=='a'
passed()
# iPod song rows: an untagged song's title is its file name without the extension; a tag stays.
m=SongsMachine(page='localclass_page',cls=0xf001,rows=2); m.found=[m.song('a.flac'),m.song('Row 1')]
m.word(m.found[1]+O['REC_TITLE'],m.string('Tagged')); m.word(m.found[0]+O['REC_PATH'],m.string('/p/a.flac'))
m.call(address=HOOKS['load_localclass_list'][0],args=(0xf001,0,0,0),gap=0)
assert [m.text(m.get(m.row(i)+O['REC_TITLE'])) for i in (0,1)]==['a','Tagged']; passed()
# My Fav, Frequent and Recent: a row stock copied without its title takes the library's, by path and CUE
# start, not its file name, asked of the database for those rows' paths alone (fav_titles, era_sql).
class FavMachine(ClassMachine):
    def __init__(self,**kw):
        super().__init__(**kw)
        self.db=sqlite3.connect(':memory:',isolation_level=None); self.sql=[]
        self.db.execute('create table songtable(fileurl,time,name)')
        self.db.executemany('insert into songtable values(?,?,?)',[('/p/05-song.flac',1,'Other Track'),('/p/05-song.flac',0,'Song')])
    def hook(self,u,address,size,unused):
        if address not in (syms['sqlite3_open'],syms['sqlite3_exec'],syms['sqlite3_close']): return super().hook(u,address,size,unused)
        a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0
        if address==syms['sqlite3_open']: self.word(b,123)
        elif address==syms['sqlite3_exec'] and self.text(b).startswith('select'):
            self.sql.append(self.text(b))
            for row in self.db.execute(self.text(b)).fetchall():
                values=self.alloc(4*len(row)); names=self.alloc(4*len(row))
                for i,v in enumerate(row): self.word(values+4*i,0 if v is None else self.string(str(v)))
                self.nested(c,d,len(row),values,names)
        self.returns(ret)
for cls in (0xf00a,0xf008,0xf007):
    m=FavMachine(page='localclass_page',cls=0xf001,rows=2)
    m.word(m.row(0)+O['REC_NAME'],m.string('05-song.flac')); m.word(m.row(0)+O['REC_PATH'],m.string('/p/05-song.flac'))
    m.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)
    t=m.get(m.row(1)+O['REC_TITLE'])  # no library title: as before
    assert m.text(m.get(m.row(0)+O['REC_TITLE']))=='Song' and m.text(t)=='Row 1', hex(cls)
    assert m.sql==["select fileurl,time,name from songtable where fileurl in ('/p/05-song.flac','/p/Row 1')"], m.sql  # the untitled rows
passed()
# An empty category list says so once its page is up, instead of a black page.
m=EmptyClassMachine(page='localclass_page',cls=0xf001,rows=0)
assert m.call(address=HOOKS['load_localclass_list'][0],args=(0xf006,0,0,0),gap=0)==0 and not empty_titles(m)
m.advance(0); assert empty_titles(m)==['No Genres'] and not m.toasts; passed()
# Albums: each stock row (a name) becomes that name's albums, told apart by album artist, else folder.
m=ClassMachine(page='localclass_page',cls=0xf001,rows=2); m.word(m.row(1)+O['REC_ALBUM'],m.string('Other'))
m.split=[m.song('Baroness'),m.song('STP')]; m.word(m.split[1]+O['REC_PATH'],m.string('/q/STP'))
assert m.call(address=HOOKS['load_localclass_list'][0],args=(0xf003,0,0,0),gap=0)==3
assert m.names(m.get(syms['p_deque_showlist']))==['Baroness','STP','Row 1']; passed()
# Run the actual Library query wrapper against SQLite, with mixed music/book/podcast tags and Unknowns.
# The flag is scoped by ringnav_localclass above; outside it (Coverflow, folders, Podcasts) SQL stays intact.
class QueryDone(Exception): pass
class LibraryQueryMachine(Machine):
    def __init__(self):
        super().__init__(); self.mock('strstr@GLIBC_2.0'); self.handlers.pop(syms['toolsQueryDbTable'])
        self.word(payload_syms['ui_thread'],1)  # another thread's loads: stock's own sorted call (name_order.c sorts the UI thread's)
        self.db=sqlite3.connect(':memory:')
        self.db.execute('create table songtable(id,album,songer,albumsonger,fileurl,track,cd_num)')
        self.db.executemany('insert into songtable(id,album,songer,albumsonger,fileurl) values(?,?,?,?,?)',[
            (1,'Shared','Shared','Shared','/mnt/mmc/Audiobooks/chapter.mp3'),
            (2,'Shared','Shared','Shared','/mnt/mmc/Music/track.flac'),
            (3,'Book','Narrator','Author','/mnt/mmc/elsewhere/BOOK.M4B'),
            (4,'Folder book','Reader','Writer','/mnt/mmc/AUDIOBOOKS/book.flac'),
            (5,'Podcast','Host','Host','/mnt/mmc/Podcasts/show/episode.mp3'),
            (6,'','','','/mnt/mmc/Audiobooks/untagged.mp3'),
        ])
    tramps={int(manifest['patch_symbols'][f'stock_{x}_trampoline'],16):x for x in ('dbquery','content_query','sorted_query')}
    def hook(self,u,address,size,unused):
        if address in (syms['strcpy@GLIBC_2.0'],syms['sprintf@GLIBC_2.0']):  # getAlbumByAlbumArtist's SQL: %s only
            a,b,*v=[u.reg_read(r) for r in REGS]; v=iter(v)
            u.mem_write(a,re.sub('%s',lambda _:self.text(next(v)),self.text(b)).encode()+b'\0')
            self.returns(a); return
        if address not in self.tramps: return super().hook(u,address,size,unused)
        a,b,c,d=[u.reg_read(r) for r in REGS]; self.sql=self.text(b)
        if self.tramps[address]=='content_query': self.content=(c,self.text(d),self.get(u.reg_read(UC_MIPS_REG_SP)+16))
        elif self.tramps[address]=='dbquery': assert self.text(a)=='/mnt/data/database.db' and (c,d)==(123,1)
        self.results=self.db.execute(self.sql).fetchall()
        if self.tramps[address]=='content_query': raise QueryDone  # stock's row handling after it isn't emulated
        self.returns(len(self.results))
lm=LibraryQueryMachine(); AA="ifnull(nullif(albumsonger,''),songer)"
for query in ('select id,songer from songtable group by songer COLLATE NOCASE',
              'select id,albumsonger from songtable group by albumsonger COLLATE NOCASE',
              'select id,album,songer,fileurl from songtable group by album COLLATE NOCASE',
              "select id,album,songer,fileurl,albumsonger from songtable group by album COLLATE NOCASE,ifnull(albumsonger,'') COLLATE NOCASE,case ifnull(albumsonger,'') when '' then rtrim(fileurl,replace(fileurl,'/','')) end",
              "select * from songtable where ifnull(songer,'')='' order by id"):
    for scoped in (0,1,0):
        lm.word(payload_syms['library_music'],scoped)
        lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string(query),123,1),gap=0)
        assert (lm.sql==(query.replace('albumsonger',AA) if 'by albumsonger' in query else query)) == (not scoped)
        assert {r[0] for r in lm.results} == ({2} if scoped and 'group by' in query else set() if scoped else
                                             {1,3,4,5,6} if 'group by' in query else {6}),lm.results
# The stock Songs title sorter uses the separate AndSort helper.
lm.word(payload_syms['library_music'],1)
lm.call(address=syms['sort_first_alphabet'],args=(0,0,0,0),gap=0)
assert [r[0] for r in lm.results]==[2] and 'where '+ 'fileurl' in lm.sql
passed()
# Podcast paths are case insensitive and bounded by the top-level folder. Shared tags must use
# the music row for artwork, and untagged episodes must not create Unknown Artist/Album rows.
lm.db.executemany('insert into songtable(id,album,songer,albumsonger,fileurl) values(?,?,?,?,?)',[
    (10,'Shared','Shared','Shared','/mnt/mmc/pOdCaStS/Show/episode.mp3'),
    (11,'','','','/mnt/mmc/PODCASTS/untagged.mp3'),
    (12,'Music','Singer','Singer','/mnt/mmc/Podcasts Extra/song.mp3'),
    (13,'Nested','Singer','Singer','/mnt/mmc/Music/Podcasts/song.mp3'),
])
for scoped in (1,0):
    lm.word(payload_syms['library_music'],scoped)
    for column in ('album','songer','albumsonger'):
        query=f'select id,{column},fileurl from songtable group by {column} COLLATE NOCASE'
        lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string(query),123,1),gap=0)
        if scoped:
            assert {r[0] for r in lm.results} == ({2,12,13} if column=='album' else {2,12}),lm.results
        else:
            assert any(r[0]==5 for r in lm.results) and any(not r[1] for r in lm.results)
# Genre/composer lists, their albums and song detail queries use the same pre-group scope.
for column in ('genre','composer'):
    lm.db.execute(f'alter table songtable add column {column}')
    lm.db.execute(f"update songtable set {column}=case when id in (6,11) then '' else album end")
    for query,want in ((f'select id,{column},fileurl from songtable group by {column} COLLATE NOCASE',{2,12,13}),
                       (f"select id,album,fileurl from songtable where {column}='Shared' group by album COLLATE NOCASE",{2}),
                       (f"select id,fileurl from songtable where {column}='Shared' and album='Shared'",{2}),
                       (f"select id,fileurl from songtable where ifnull({column},'')=''",set())):
        lm.word(payload_syms['library_music'],1)
        lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string(query),123,1),gap=0)
        assert {r[0] for r in lm.results}==want,(query,lm.results)
# Direct song/history/Hi-Res loads retain only the music rows, in the requested order.
for order in ('id','id desc'):
    query=f'select id,fileurl from songtable order by {order}'
    lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string(query),123,1),gap=0)
    assert [r[0] for r in lm.results]==sorted([2,12,13],reverse=order.endswith('desc'))
for column in ('genre','composer'): lm.db.execute(f'alter table songtable drop column {column}')
lm.db.execute('delete from songtable where id >= 10')
# Music staging applies the scope for stock queries and restores either caller state.
class MusicStageMachine(QueueMachine):
    def hook(self,u,address,size,unused):
        if address==syms['getMusicByAlbum']:
            assert self.get(payload_syms['library_music'])==1
        return super().hook(u,address,size,unused)
for scoped in (0,1):
    ms=MusicStageMachine(); ms.word(payload_syms['library_music'],scoped)
    rows=ms.call(address=payload_syms['staged_music'],args=(syms['getMusicByAlbum'],ms.string('Album'),ms.alloc(4),0),gap=0)
    assert ms.get(payload_syms['library_music'])==scoped and ms.query[:2]==('getMusicByAlbum','Album')
class MusicLoadMachine(QueueMachine):
    def __init__(self):
        super().__init__(); self.handlers[syms['getAllMusic']]='music_load'
    def hook(self,u,address,size,unused):
        if address!=syms['getAllMusic']: return super().hook(u,address,size,unused)
        assert self.get(payload_syms['library_music'])==1
        self.returns(self.result)
for loader,args in (('all_songs',(1,)),('songs_unsorted',(1,))):
    for scoped in (0,1):
        for result in (-1,0,2):
            ms=MusicLoadMachine(); ms.result=result; ms.word(payload_syms['library_music'],scoped)
            assert ms.call(address=payload_syms[loader],args=args,gap=0)==result
            assert ms.get(payload_syms['library_music'])==scoped
passed()
# Album Artist: an album without the tag lists under its artist (stock's albumsonger queries), a
# tagged one under its album artist; Coverflow's own grouping keeps the tag as it is.
lm.db.executemany('insert into songtable(id,album,songer,albumsonger,fileurl) values(?,?,?,?,?)',[(7,'Disintegration','The Cure','','/mnt/mmc/Music/D/CD1/1.flac'),
                  (8,'Wish','The Cure','The Cure','/mnt/mmc/Music/W/1.flac'),(9,'Guest','The Cure','Other','/mnt/mmc/Music/G/1.flac')])
lm.word(payload_syms['library_music'],0)
for query,want in (("select id,album,songer,fileurl,albumsonger from songtable where albumsonger like 'the cure' group by album COLLATE NOCASE",{7,8}),
                   ("select * from songtable where album like 'Disintegration' and albumsonger like 'The Cure'",{7}),
                   ('select * from songtable where albumsonger=""',{6}),
                   ("select id,album,songer,fileurl,albumsonger from songtable group by album COLLATE NOCASE,ifnull(albumsonger,'') COLLATE NOCASE,case ifnull(albumsonger,'') when '' then rtrim(fileurl,replace(fileurl,'/','')) end having album like 'Disintegration'",{7})):
    lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string(query),123,1),gap=0)
    assert {r[0] for r in lm.results}==want and (lm.sql==query)==('ifnull(albumsonger' in query),(query,lm.sql,lm.results)
lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string('select id,albumsonger from songtable group by albumsonger COLLATE NOCASE'),123,1),gap=0)
assert sorted(r[1] for r in lm.results)==['','Author','Host','Other','Shared','The Cure','Writer'],lm.results
# An artist's albums tab: getAlbumByAlbumArtist runs its two queries through toolsQueryContentDbTable,
# rewritten and scoped the same way: Unknown Artist's albums are its songs' (neither tag).
for scoped,artist,want in ((0,'The Cure',{7,8}),(0,'',{6}),(1,'The Cure',{7,8}),(1,'',set())):
    lm.word(payload_syms['library_music'],scoped)
    try: lm.call(address=syms['getAlbumByAlbumArtist'],args=(lm.string(artist),),gap=0)
    except QueryDone: pass
    assert lm.content[1:]==(artist,1) and AA in lm.sql and {r[0] for r in lm.results}==want,(artist,lm.sql,lm.results)
lm.word(payload_syms['library_music'],1); lm.sql=None
assert lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string('select * from songtable'+' '*2048),123,1),gap=0)==-1
assert lm.sql is None
# Discovery is filtered before grouping, even when its tags collide with a main-library album.
# The unscoped path retains every row for explicit folder/playlist use; moving a keeper back
# into Music makes it visible on the next query, without deleting or retagging a database row.
lm.scope_tree={'/mnt/mmc':[('Discovery',4)],'/mnt/mmc/Discovery':[]}
lm.scope_markers={'/mnt/mmc/Discovery/database.ignore'}
lm.scope_scan()
lm.db.execute('delete from songtable')
for i,path in enumerate(discovery_paths):
    lm.db.execute('insert into songtable(id,album,songer,albumsonger,fileurl) values(?,?,?,?,?)',
                  (i,'Shared' if i in (0,5) else f'Album {i}','Artist','Artist',path))
queries=['select id,fileurl from songtable order by id',
         'select id,album,fileurl from songtable group by album COLLATE NOCASE',
         "select id,fileurl from songtable where songer='Artist' order by id"]
for scoped in (1,0,1):
    lm.word(payload_syms['library_music'],scoped)
    for query in queries:
        lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string(query),123,1),gap=0)
        ids={r[0] for r in lm.results}
        assert ids==({2,3,4,5} if scoped else {0,1,2,3,4} if 'group by' in query else set(range(6))),lm.results
lm.db.execute("update songtable set fileurl='/mnt/mmc/Music/new-keeper.flac' where id=1")
lm.call(address=HOOKS['toolsQueryDbTable'][0],args=(lm.string('/mnt/data/database.db'),lm.string(queries[0]),123,1),gap=0)
assert {r[0] for r in lm.results}=={1,2,3,4,5}
assert lm.db.execute('select count(*) from songtable').fetchone()[0]==6
lm.db.close(); passed()
# The compiled marker walker and SQL wrapper share nearest-marker rules; filenames are literal
# prefixes (quotes, SQL wildcard characters and UTF-8), and scoped Coverflow SQL expands even
# outside a normal-library call. A library update refreshes both SQL and stored-list snapshots.
qm=LibraryQueryMachine()
qm.db.execute('delete from songtable')
marker_paths=['Music/a.flac','Singles/a.flac','Singles/Keep/a.flac','Singles Extra/a.flac',
              'Needledrop/a.flac',"Odd ' %_/a.flac",'日本語/a.flac','Discovery/a.flac']
qm.db.executemany('insert into songtable(id,album,songer,albumsonger,fileurl) values(?,?,?,?,?)',
    [(i,'Shared' if i in (0,1) else str(i),'Artist','Artist','/mnt/mmc/'+p) for i,p in enumerate(marker_paths)])
qm.scope_tree={'/mnt/mmc':[('Singles',4),('Needledrop',4),("Odd ' %_",4),('日本語',4)],
    '/mnt/mmc/Singles':[('Keep',4)],'/mnt/mmc/Singles/Keep':[],
    '/mnt/mmc/Needledrop':[],"/mnt/mmc/Odd ' %_":[],'/mnt/mmc/日本語':[]}
qm.scope_markers={p+'/database.ignore' for p in ('/mnt/mmc/Singles','/mnt/mmc/Needledrop',"/mnt/mmc/Odd ' %_",'/mnt/mmc/日本語')}
qm.scope_markers.add('/mnt/mmc/Singles/Keep/database.unignore'); qm.scope_scan()
assert sorted(qm.scope_files[SCOPE_RULES].decode().splitlines())==["0/mnt/mmc/Needledrop","0/mnt/mmc/Odd ' %_",
    '0/mnt/mmc/Singles','0/mnt/mmc/日本語','1/mnt/mmc/Singles/Keep'] and SCOPE_RULES+'.tmp' not in qm.scope_files
def marker_query(sql,scoped=1):
    qm.word(payload_syms['library_music'],scoped)
    qm.call(address=HOOKS['toolsQueryDbTable'][0],args=(qm.string('/mnt/data/database.db'),qm.string(sql),123,1),gap=0)
    return {r[0] for r in qm.results}
assert marker_query('select id,fileurl from songtable order by id')=={0,2,3,7}
assert marker_query('select id,fileurl from songtable order by id',0)==set(range(8))
assert marker_query('select id,fileurl from songtable where /*q2-library-scope*/1 order by id',0)=={0,2,3,7}
assert marker_query('select id,album from songtable group by album')=={0,2,3,7}
qm.scope_markers.remove('/mnt/mmc/Singles/database.ignore')
assert marker_query('select id,fileurl from songtable')=={0,2,3,7}
qm.scope_scan()
assert marker_query('select id,fileurl from songtable')=={0,1,2,3,7}
assert qm.db.execute('select count(*) from songtable').fetchone()[0]==8
qm.db.close(); passed()
for cls in (0xf00a,0xf008,0xf007,0xff20):
    q=ClassMachine(rows=3)
    q.scope_tree={'/mnt/mmc':[('Singles',4)],'/mnt/mmc/Singles':[('Keep',4)],'/mnt/mmc/Singles/Keep':[]}
    q.scope_markers={'/mnt/mmc/Singles/database.ignore','/mnt/mmc/Singles/Keep/database.unignore'}; q.scope_scan()
    for i,path in enumerate(('Singles/a.flac','Singles/Keep/a.flac','Music/a.flac')):
        q.word(q.row(i)+O['REC_PATH'],q.string('/mnt/mmc/'+path))
        q.word(q.row(i)+O['REC_TITLE'],q.string('Tagged'))
    q.call(address=HOOKS['load_localclass_list'][0],args=(cls,0,0,0),gap=0)
    assert q.names(q.get(syms['p_deque_showlist']))==(['Row 0','Row 1','Row 2'] if cls==0xff20 else ['Row 1','Row 2'])
    passed()
# Their stock art cache names (/mnt/mmc/.sldp/<md5>0.jpg): a shared name adds the album artist,
# a name alone stays; the album page's big cover follows its row's cover task name.
for e in m.items(m.get(syms['p_deque_showlist']))[:2]: m.word(e+O['REC_ALBUM_ARTIST'],m.get(e+O['REC_NAME']))
art=payload_syms; buf=m.alloc(0x100)
keys=['Album\x1fBaroness','Album\x1fSTP','Other']
for i,want in enumerate(keys):
    m.call(address=art['ringnav_art_name'],args=(buf,0x100,i,0),gap=0); assert m.text(buf)==want
covers=[m.alloc(12) for _ in keys]
for c,k in zip(covers,keys): m.word(c+4,m.string(k))
m.word(syms['pdeq_albumcoverlist'],m.deque(covers))
for row,want in ((1,'Album\x1fSTP'),(2,'Album'),(9,'Album')):
    m.word(O['ALBUMINFO_ROW'],row); m.call(address=art['ringnav_art_header'],args=(buf,0x100,m.string('%s'),m.string('Album')),gap=0)
    assert m.text(buf)==want, row
passed()
# An artist's albums tab has one row of a shared name: it takes its album's key in the split
# order, by folder, else by album artist; a name alone, and the songs tab, stay stock's.
class ArtistMachine(ClassMachine):
    tramp=int(manifest['patch_symbols']['stock_artist_trampoline'],16)
    def hook(self,u,address,size,unused):
        if address==syms['albumcoverinfo_init']:
            covers=[]
            for r in self.items(u.reg_read(REGS[0])):
                c=self.alloc(0x60)
                self.word(c,getattr(self,'cover_flags',0))
                self.word(c+4,self.string(self.text(self.get(r+O['REC_ALBUM']))))
                self.word(c+8,self.get(r+O['REC_PATH'])); covers.append(c)
            self.word(syms['pdeq_albumcoverlist'],self.deque(covers))
            self.returns(0); return
        a,b=u.reg_read(REGS[0]),u.reg_read(REGS[1]); libc={syms[n+'@GLIBC_2.0']:n for n in ('calloc','strdup')}|{0x1000010:'free'}
        if address not in libc: return super().hook(u,address,size,unused)
        self.returns({'calloc':lambda:self.alloc((a*b+7)&~3),'strdup':lambda:self.string(self.text(a)),'free':lambda:0}[libc[address]]())
for path,artist,want in (('/p/x','Artist','Album\x1fBaroness'),('/z/x','STP','Album\x1fSTP'),('/z/x','Artist','Album')):
    m=ArtistMachine(page='localclass_page',cls=0xff01,rows=2); m.word(m.row(1)+O['REC_ALBUM'],m.string('Other'))
    m.word(m.row(0)+O['REC_PATH'],m.string(path)); m.word(m.row(0)+O['REC_ARTIST'],m.string(artist))
    m.split=[m.song('Baroness'),m.song('STP')]; m.word(m.split[1]+O['REC_PATH'],m.string('/q/STP'))
    for e in m.split: m.word(e+O['REC_ALBUM_ARTIST'],m.get(e+O['REC_NAME']))
    assert m.call(address=HOOKS['load_localartist_list'][0],args=(1,0,0,0),gap=0)==2
    # Run the stock Album-tab and page-return call sites, not just the key helper: 9.8
    # left these unhooked, so the worker wrote the old cache name the renderer never read.
    m.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')
    m.cover_flags=0x101  # an old job completes between stock init and key renaming
    for site in (0x4aeb70,0x4b07f0,0x4b12ac):
        m.u.reg_write(UC_MIPS_REG_SP,0x7000f000)
        m.u.reg_write(UC_MIPS_REG_V0,syms['p_deque_showlist'])
        m.u.reg_write(UC_MIPS_REG_T9,syms['albumcoverinfo_init'])
        m.u.emu_start(site,site+8,count=m.budget)
        assert m.u.reg_read(UC_MIPS_REG_PC)==site+8
        covers=m.items(m.get(syms['pdeq_albumcoverlist']))
        assert [m.text(m.get(c+4)) for c in covers]==[want,'Other'], hex(site)
        assert [m.get(c)&0xffff for c in covers]==[0 if want!='Album' else 0x101,0x101]
        assert [m.text(m.get(c+8)) for c in covers]==[path,'/p/Row 1']
    for i,k in enumerate((want,'Other')):
        m.u.reg_write(UC_MIPS_REG_GP,0xa3aae0)
        m.u.reg_write(UC_MIPS_REG_S1,buf); m.u.reg_write(UC_MIPS_REG_S2,i)
        m.u.reg_write(UC_MIPS_REG_V0,m.row(i)); m.u.reg_write(UC_MIPS_REG_A1,0x100)
        m.u.emu_start(0x4adb3c,0x4adb54,count=m.budget)
        assert m.u.reg_read(UC_MIPS_REG_PC)==0x4adb54
        assert m.text(buf)==k, (path,artist,i,m.text(buf))
    m.call(address=HOOKS['load_localartist_list'][0],args=(0,0,0,0),gap=0)
    m.call(address=art['ringnav_artist_art_name'],args=(buf,0x100,0,m.string('Album')),gap=0); assert m.text(buf)=='Album'
passed()
# A local art job that finishes after a list rebuild must not mark the new row ready.
# Run the worker's real completion call site, including its stack-buffer argument, then stock's
# readiness check (used by the renderer and by the request queue to skip completed covers).
class CoverQueueMachine(ArtistMachine):
    def __init__(self):
        super().__init__(rows=12)
        self.pending=[]
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')
    def hook(self,u,address,size,unused):
        if address not in (syms['list_clear'],syms['_list_push_back']):
            return super().hook(u,address,size,unused)
        if address==syms['list_clear']: self.pending.clear()
        else:
            p=u.reg_read(REGS[1])
            self.pending.append(tuple(self.get(p+i) for i in (0,4,8)))
        self.returns(0)
m=CoverQueueMachine()
def cover_requests(start,big):
    name='local_albumcover_task' if big else 'local_albumsmallcover_task'
    m.call(address=syms[name],args=(start,9,0,0),gap=0)
    return [row for row,kind,size in m.pending if kind==0 and size==big]
def cover_ready(row,big):
    return m.call(address=syms['check_albumcover_flag'],args=(row,big,0,0),gap=0)
def cover_done(row,big,key):
    sp=0x7000d000
    m.u.mem_write(sp+0x1688,key.encode()+b'\0')
    m.word(sp+0x1828,row); m.word(sp+0x1830,big); m.word(sp+0x18,0xa3aae0)
    m.u.reg_write(UC_MIPS_REG_SP,sp); m.u.reg_write(UC_MIPS_REG_GP,0xa3aae0)
    m.u.emu_start(0x4f84b8,0x4f84c8,count=m.budget)
    assert m.u.reg_read(UC_MIPS_REG_PC)==0x4f84c8
for visit in range(5):
    keys=[f'Artist {visit} album {i}' for i in range(12)]
    covers=[]
    for key in keys:
        c=m.alloc(0x60); m.word(c+4,m.string(key)); covers.append(c)
    m.word(syms['pdeq_albumcoverlist'],m.deque(covers))
    if visit==0:
        fixed=bytes(m.u.mem_read(0x4f84bc,8))
        m.u.mem_write(0x4f84bc,struct.pack('<II',0x8f99b760,0x0411ff44))
        m.u.ctl_remove_cache(0x4f84b8,0x4f84c8)
        cover_done(11,1,'previous artist')
        assert cover_ready(11,1)==1  # stock falsely completes the last album
        assert cover_requests(9,1)==[9,10]  # scrolling cannot request its missing art
        m.byte(m.items(m.get(syms['pdeq_albumcoverlist']))[11]+1,0)
        m.u.mem_write(0x4f84bc,fixed)
        m.u.ctl_remove_cache(0x4f84b8,0x4f84c8)
    for row in (0,8,9,11):  # first screen, scroll boundary and last album
        for big in (0,1):
            cover_done(row,big,f'Artist {visit-1} album {row}')
            assert cover_ready(row,big)==0, (visit,row,big)
            assert row in cover_requests(row//9*9,big), (visit,row,big)
            cover_done(row,big,keys[row])
            assert cover_ready(row,big)==1, (visit,row,big)
            assert row not in cover_requests(row//9*9,big), (visit,row,big)
    for row in (-1,12): cover_done(row,1,'gone')
# The folder-cover path (0x4f94fc) reaches the same completion call with its own copied key, not
# whatever its last call left in a2.
def folder_done(row,key,a2):
    sp=0x7000d000
    m.u.mem_write(sp+0x1688,key.encode()+b'\0'); m.word(sp+0x1828,row); m.word(sp+0x18,0xa3aae0)
    m.u.reg_write(UC_MIPS_REG_SP,sp); m.u.reg_write(UC_MIPS_REG_GP,0xa3aae0)
    m.u.reg_write(UC_MIPS_REG_S2,1); m.u.reg_write(UC_MIPS_REG_A2,m.string(a2))
    m.u.emu_start(0x4f94f4,0x4f84c8,count=m.budget)
    assert m.u.reg_read(UC_MIPS_REG_PC)==0x4f84c8
for key,a2,ready in ((keys[3],'left over',1),('previous folder',keys[3],0)):
    m.byte(m.items(m.get(syms['pdeq_albumcoverlist']))[3],0)
    folder_done(3,key,a2); assert cover_ready(3,0)==ready, (key,a2)
passed()
# Scrolling never leaves the shared cover worker paused (albumcover_taskstate 2): stock resumed it only
# when scroll end saw a new first row, so a wheel tick or a return to a scrolled grid stopped every
# grid's art. An artist's albums scroll end takes rows as 160 px, three a row, not 256.
class ScrollEndMachine(CoverQueueMachine):
    def hook(self,u,address,size,unused):
        if address!=syms['toolsGetSysMemFree']: return super().hook(u,address,size,unused)
        self.returns(1<<20)
m=ScrollEndMachine(); m.mock('widget_lookup','widget_get_prop_int')
for pause in (0x4ace30,0x4a3910):  # an artist's albums, Library Albums
    m.word(syms['albumcover_taskstate'],1)
    m.call(address=pause,args=(0,0,0,0),gap=0); assert m.get(syms['albumcover_taskstate'])==1, hex(pause)
covers=[]
for i in range(30):
    c=m.alloc(0x60); m.word(c+4,m.string(f'album {i}')); covers.append(c)
m.word(syms['pdeq_albumcoverlist'],m.deque(covers))
view=m.node('scroll_view','scroll_view_album',yoffset=800); win=m.node('window','artistinfo_page',[view])
m.word(0xa3b5ac,1); m.word(0xa3b5b0,0)  # the Albums tab; its last requested first row
for tick in range(2):  # the second, at the same offset, requests nothing and leaves the worker running
    m.pending.clear(); m.call(address=0x4b0a24,args=(win,0,0,0),gap=0)
    assert [row for row,kind,size in m.pending]==([] if tick else list(range(15,24))), m.pending
    assert m.get(0xa3b5b0)==15 and m.get(syms['albumcover_taskstate'])==1
passed()
# A row opens its name's songs (0xff10, getMusicByAlbum): only its own album's stay.
m=ClassMachine(page='album_page',cls=0xf003,rows=2); m.word(m.row(1)+O['REC_PATH'],m.string('/q/Row 1'))
assert m.call(address=HOOKS['load_album_detaillist'][0],args=(0xf003,m.song('Album'),0,0),gap=0)==1
assert m.names(m.get(syms['p_deque_showlist']))==['Row 0'] and ('stock_localclass',0xff10,0,0) in m.calls; passed()

# Coverflow (docs/internals.md): the Home card, the runtime coverflow_page over a stock slide_menu,
# the tracks query and handoff. The art thread itself runs on the host (test/coverflow.py).
from ipod import HOME_TEXT_X, HOME_LIST_W, HOME_OURS, HOME_PAGE, HOME_ROWS, HOME_TOP, decode
HOME_ROW=OLED_G.home_row  # Home's rows as built (OLED's set)
cards=[c[2]['name'] for c in decode((B/'ui'/HOME_PAGE).read_bytes())[3][1][3][0][3]]  # list_view_home's scroll_view's rows
assert cards==[f'btn_{r}' for r in HOME_ROWS] and 'btn_coverflow' in cards, cards  # Home's rows, in tools/ipod.py's order
home_hook=HOOKS['home_page_init']
assert struct.unpack_from('<I',demo,fileoff(demo,home_hook[0]))[0]==j(payload_syms['coverflow_home'])
passed()
def asset_tree(m,path):
    """A built UI asset as mock widgets, the window's parent the window manager, with what the payload
    reads: type, name, visible, enable, geometry and, for a scroll view, its content height and the
    slidable flags as AWTK sets them. scroll_view_create (0x6042fc) leaves both off, the asset's
    xslidable/yslidable props set them (0x604bc4), and a list_view's layout (0x5fd234) clears x and
    sets y only when the list holds a mobile scroll bar."""
    def build(n,parent,parent_kind='',siblings=()):
        kind,g,props,children=n
        a=m.node(kind,props.get('name',''),visible=int(props.get('visible')!='false'))
        m.nodes[a].update(enable=int(props.get('enable')!='false'),asset=props)
        for off,v in zip(('W_X','W_Y','W_W','W_H'),[0,0,375,290] if kind=='window' else g): m.word(a+O[off],v)
        m.word(a+O['W_PARENT'],parent)
        if kind=='scroll_view':
            m.byte(a+O['VIEW_HORIZONTAL'],props.get('xslidable')=='true' and parent_kind!='list_view')
            m.byte(a+O['VIEW_VERTICAL'],props.get('yslidable')=='true' or parent_kind=='list_view' and 'scroll_bar_m' in siblings)
            m.word(a+O['VIEW_CONTENT_H'],max((c[1][1]+c[1][3] for c in children),default=0))
        m.nodes[a]['children']=[build(c,a,kind,[c[0] for c in children]) for c in children]
        return a
    return build(decode((B/'ui'/path).read_bytes()),m.wm)
def named(m,w,name):
    if m.nodes[w]['name']==name: return w
    return next((f for c in m.nodes[w]['children'] if (f:=named(m,c,name))),0)
def click_target(m,w):
    em=m.alloc(4); it=m.alloc(0x28); m.word(w+O['W_EMITTER'],em); m.word(em,it); m.word(it+O['EMIT_TYPE'],O['EVT_CLICK'])
def home_list(m):
    """iPod Home as built (ui/home_page.bin) on top: its scroll view and the rows' tap images. Stock
    binds every image but Coverflow's, which the payload binds. A song is paused (mclGetPlayStatus 3),
    so Now Playing's row shows (coverflow.c np_loaded), and the card's root lists a folder, so Folders
    shows (home.c home_folders): the rows keep HOME_ROWS' order."""
    m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(0xa50084,3)
    m.stats['/mnt/mmc']=1; m.mock('toolsLoadDirectory',prefix='one:')
    m.top=asset_tree(m,HOME_PAGE)
    imgs=[named(m,m.top,'img_'+HOME_OURS.get(r,r)) for r in HOME_ROWS]
    cf=HOME_ROWS.index('coverflow')
    for img in imgs[:cf]+imgs[cf+1:]: click_target(m,img)
    return named(m,m.top,'scroll_view_home'),imgs

# Return is a button too: after a touch, the page it goes back to shows its selection again.
m=Machine(); w,es=m.page_list(3); m.paint(w); m.touch(); m.paint(w,gap=0); assert not m.drawn()
assert m.call(O['KEY_RETURN'])==0; m.advance(0); m.paint(w,gap=0); assert m.drawn(); passed()

# Page slides (navigation.c slides()): the page under a sliding top window draws the row it holds; no load, recall or scroll.
HINT='htranslate'  # any non-empty anim_hint
def under_slide(hint=HINT):
    m=Machine(); w,es=m.page_list(3); m.paint(w); assert m.call()==11 and m.selected(w)==1
    m.word(w+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm)
    w2,_=m.page_list(3,name='display_page'); m.nodes[m.top]['anim_hint']=hint
    return m,w,w2
m,w,w2=under_slide(); m.paint(w,gap=0)
assert m.drawn() and m.sel()[1]==48-m.get(w+O['SCROLL_Y']) and m.selected(w)==1 and not m.moved(); passed()
m,w,w2=under_slide(''); m.paint(w,gap=0); assert not m.drawn(); passed()  # under a page that does not slide
# Touch mode hides it there too, until a Return by button, which shows the page behind its row
# while the closing page keeps hiding its own.
m,w,w2=under_slide(); m.paint(w2); m.touch(); m.paint(w,gap=0); assert not m.drawn()
assert m.call(O['KEY_RETURN'])==0; m.paint(w,gap=0); assert m.drawn()
m.paint(w2,gap=0); assert not m.drawn(); passed()
# A sliding page's first paint is its snapshot, so a recalled row is revealed at once, not by a glide.
m,w,es=walk(10,5); w2,es2=m.page_list(10,extent=1000); m.nodes[m.top]['anim_hint']=HINT
assert m.paint(w2)==0 and m.selected(w2)==5 and m.get(w2+O['SCROLL_Y'])==192
assert [c[0] for c in m.moved()]==['scroll_view_set_offset']; passed()

# Home is an ordinary list: the wheel walks its rows (HOME_ROWS) one by one, those that end above OLED's list_end and
# then any it scrolls in by whole rows, so the last rests where the last shown did (this Home is not
# coverflow.c's, whose home_rest would rest it on its seven); it stops at both ends however long the pause, Return selects
# Now Playing, the bar spans the list's width and centre clicks the image.
m=Machine(); m.config.update(FLAT); view,imgs=home_list(m); click_target(m,imgs[HOME_ROWS.index('coverflow')])
# The built (full-bleed) Home's box runs to y 290 on the screen, past the list end (282), so list_fade darkens the rows
# past it in black after the bar (the Q2's Home rests on coverflow.c home_rest instead and has no fade): the bar is its
# accent bands, the rest the fade's.
def bar_drawn():
    bands=[b[:5] for b in m.bands]
    assert all(b[4]&0xffffff==0 for b in bands if b[4]&0xffffff!=OLED_BLUE&0xffffff)
    return [b for b in bands if b[4]&0xffffff==OLED_BLUE&0xffffff]
m.paint(view)
assert m.selected(view)==0
last=len(HOME_ROWS)-1; shown=min(len(HOME_ROWS),(OLED_G.list_end-HOME_TOP)//HOME_ROW)
for i in range(1,last+1): assert m.call()==11 and m.selected(view)==i
assert m.get(view+O['SCROLL_Y'])==(last+1-shown)*HOME_ROW
m.paint(view); assert not m.rounded and bar_drawn()==bar_bands(inset,(shown-1)*HOME_ROW,HOME_LIST_W-2*inset,HOME_ROW,radius)
for gap in (50,300,1000):
    assert m.call(gap=gap)==11 and m.selected(view)==last and m.get(view+O['SCROLL_Y'])==(last+1-shown)*HOME_ROW
for i in range(last-1,-1,-1): assert m.call(O['KEY_PREV'])==11 and m.selected(view)==i
for gap in (50,300,1000): assert m.call(O['KEY_PREV'],gap=gap)==11 and m.selected(view)==0
for i in range(1,last+1): assert m.call()==11 and m.selected(view)==i
for i in range(last-1,2,-1): assert m.call(O['KEY_PREV'])==11 and m.selected(view)==i
assert m.call(O['KEY_RETURN'])==11 and m.selected(view)==0 and not destinations(m)
for i in (1,2): assert m.call(gap=1000)==11 and m.selected(view)==i
assert not m.slides and m.confirm()==11 and m.dispatched()[0][1]==imgs[2]; passed()

# Boot: if Home's first paint comes before the screen is usable, no row is chosen or drawn. The
# status bar's next paint repaints the list, once, and Now Playing gets the bar.
m=Machine(); m.config.update(FLAT); view,imgs=home_list(m); bar=m.node('window','system_bar'); m.word(syms['system_bar'],bar); m.word(bar+O['W_PARENT'],m.wm)
paint_bar=lambda: m.call(address=HOOKS['widget_on_paint_background'][0],args=(bar,m.canvas,0,0))
m.byte(syms['g_backlight_status'],0); m.paint(view); assert m.selected(view)==-1 and not m.drawn()
paint_bar(); assert ('widget_invalidate_force',view) not in [c[:2] for c in m.calls]
m.touch(); m.byte(syms['g_backlight_status'],1)
paint_bar(); assert ('widget_invalidate_force',view) in [c[:2] for c in m.calls]
m.paint(view); assert m.selected(view)==0 and bar_drawn()==bar_bands(inset,0,HOME_LIST_W-2*inset,HOME_ROW,radius)
paint_bar(); assert ('widget_invalidate_force',view) not in [c[:2] for c in m.calls]; passed()

# Chevrons: OLED's own (list_marks.c chevron_icon), on each visible row of a drill window (contexts.inc), centred
# CHEV_SHIFT (5 px) left of the set's right column, clipped to the surface, white on the selection; Home's too
# (home.chevron visible), but for Shuffle Songs, which acts rather than opening a list. Stock draws its own on folder,
# category, album-list and Local Music rows, so those windows, song lists and grids get none from here. The names are
# read as each loads (Machine.image_loads): theme_art's answer is one buffer, rewritten by the next.
def window(m,name,w):
    m.top=m.node('window',name,[w]); m.word(w+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm)
def loaded(m): return [m.text(c[2]) for c in m.calls if c[0]=='widget_load_image']
m=Machine(); view,imgs=home_list(m); click_target(m,imgs[HOME_ROWS.index('coverflow')]); m.clip=(0,0,375,320)
m.image_loads=[]; m.paint(view)
CHEV_X=HOME_LIST_W-OLED_G.right_x-5
def bar(sel):  # Home's chevrons, their row's selected one white
    rows=[i for i,r in enumerate(HOME_ROWS) if r!='shuffle_songs']
    return ([(CHEV_X,i*HOME_ROW+HOME_ROW//2-m.get(view+O['SCROLL_Y']),(0,0,HOME_LIST_W,7*HOME_ROW)) for i in rows],
            ['oled_chevron_w' if i==sel else 'oled_chevron_d' for i in rows])
assert (m.icons,m.image_loads)==bar(0) and m.clip==(0,0,375,320); passed()
m.call(); m.image_loads=[]; m.paint(view); assert m.selected(view)==1 and (m.icons,m.image_loads)==bar(1); passed()  # follows the bar
# Drawn in touch mode too, as Home's bar, which touch never hides; none while stock
# multi-select hides its own.
m.touch(); m.image_loads=[]; m.paint(view); assert (m.icons,m.image_loads)==bar(1) and m.selected(view)==1; passed()
m.byte(syms['g_navbar_status'],1); m.image_loads=[]; m.paint(view); assert not m.icons and not m.image_loads; passed()
# Playlists: every row follows the scroll, clipped to the viewport; the half-width
# Import/Export tiles get none.
m=Machine(); w,es=m.page_list(10,height=96,extent=480,name='playlist_page'); window(m,'playlist_page',w)
tile=m.entry(w,0); m.word(tile+O['W_W'],100); m.nodes[w]['children'].insert(0,tile)
for top in (0,24):
    m.word(w+O['SCROLL_Y'],top); m.paint(w)
    assert m.icons==[(240-OLED_G.right_x-5,48*i+24-top,(0,0,240,96)) for i in range(10)]; passed()
# Leaf lists, grids and windows whose rows stock already marks draw none.
for name in ('allmusic_page','albuminfo_page','artistinfo_page','album_page','folder_page',
             'localclass_page','localmusic_page','sysset_page'):
    m=Machine(); w,_=m.page_list(5,extent=240,name=name); window(m,name,w); m.paint(w)
    assert not m.icons and not loaded(m), name; passed()

# The status bar as built (ui/system_bar.bin), OLED's status row, laid out by the stock row layouter
# (children_layouter_default, 0x63bda8) with every icon shown: the left group empty (its glyphs off-screen
# for stock to update), the right one the battery's, STATUS_ROW_ICON high and ending STATUS_ROW_INSET from
# the screen's edge, and the title's and clock's box starting on that inset (ipod.py status_row), the title hidden: the
# row names no page.
from ipod import STATUS_BAR, STATUS_ROW_ICON
m=Machine(); m.mock('strtol@GLIBC_2.0','strstr@GLIBC_2.0'); m.mock('tk_calloc','tk_free',prefix='alloc:')
bar=asset_tree(m,STATUS_BAR); m.word(bar+O['W_W'],375)
views=[named(m,bar,n) for n in ('view_left','view_right')]
for v in views:
    layout=m.call(address=syms['children_layouter_default_create'],args=(0,0,0,0),gap=0)
    for param in re.fullmatch(r'default\((.*)\)',m.nodes[v]['asset']['children_layout'])[1].split(','):
        assert m.call(address=syms['children_layouter_set_param_str'],args=(layout,*map(m.string,param.split('=')),0),gap=0)==0
    m.word(v+O['W_CHILDREN_LAYOUT'],layout)
def laid_out():
    for v in views: native_row_layout(m,v)
    return [(signed(m.get(v+O['W_X']))+signed(m.get(c+O['W_X'])),m.get(c+O['W_W']),m.get(c+O['W_H'])) for v in views
            for c in m.nodes[v]['children'] if m.nodes[c]['visible']]
cells=laid_out()
assert not m.nodes[views[0]]['children'] and cells and all(h==STATUS_ROW_ICON for x,w,h in cells), cells
assert cells[-1][0]+cells[-1][1]==375-O['STATUS_ROW_INSET'], cells
assert not any(m.get(c+O['W_Y']) for v in views for c in m.nodes[v]['children'] if m.nodes[c]['visible'])  # at y 0 of their group's box
for name in ('label_title','label_clock'): assert m.get(named(m,bar,name)+O['W_X'])==O['STATUS_ROW_INSET']-2, name  # hscroll_label's 2 px
assert [m.nodes[named(m,bar,n)]['visible'] for n in ('label_title','label_clock')]==[0,1]
passed()
# The battery's slot (bar_sync shows view_battery alone, stock's img_battery and label_battery hidden): the layout skips
# the hidden two, and the slot as built (ipod.py BATT_H_W, its widest) ends on the row's inset.
from ipod import BATT_H_W
batt=[named(m,bar,n) for n in ('img_battery','label_battery','view_battery')]
for i,b in enumerate(batt): m.nodes[b]['visible']=int(i==2)
cells=laid_out()
assert cells[-1][0]+cells[-1][1]==375-O['STATUS_ROW_INSET'] and cells[-1][1]==BATT_H_W==O['BATT_BODY_W']+O['BATT_NUB_W']+O['BATT_GAP']+O['BATT_PCT_W'], cells
passed()

# Cycle the live Battery setting with the actual parent row layouter: the slot stays visible from Percent to Icon, so a
# width change must reposition it.
m.config={'BATTERY':'0'}
m.word(bar+O['W_PARENT'],m.wm); m.word(syms['system_bar'],bar)
m.nodes[named(m,bar,'img_bt')]['visible']=0
m.nodes[named(m,bar,'img_wifi')]['visible']=0
slot=named(m,bar,'view_battery')
m.nodes[named(m,bar,'label_battery')]['text']='50%'
def paint_live_bar():
    m.call(address=HOOKS['widget_on_paint_background'][0],args=(bar,m.canvas,0,0),gap=0)
paint_live_bar()
idle=named(m,bar,'label_idle'); state=named(m,bar,'img_state'); eq=named(m,bar,'label_eq')
assert m.get(eq+O['W_W'])==28
assert all(m.nodes[eq]['asset'][f'style:{s}:font_size']=='16' for s in ('normal','disable','focused'))
clock_box=tuple(m.get(title+O[k]) for k in ('W_X','W_Y','W_W','W_H'))
# bar_sync shows the play state (here local's, mclGetPlayStatus: 2 playing, 3 paused) itself.
m.handlers.pop(syms['mclGetPlayStatus'],None)
m.advance(0); shown=(m.nodes[state]['visible'],m.nodes[state].get('image'))
# The icon is the theme's own art, by state and half (oled_play_d, oled_pause_d: tools/art.py).
for status,equalizer,icon in ((0,0,''),(2,0,'oled_play_d'),(3,0,'oled_pause_d'),(3,0,'oled_pause_d'),(0,1,''),(2,1,'oled_play_d'),(0,0,'')):
    m.word(0xa50084,status); m.nodes[eq]['visible']=equalizer
    paint_live_bar(); playing=int(bool(icon))
    # #116: a change made mid-paint repaints the whole bar once, after the frame.
    m.advance(0)
    now=(playing,m.nodes[state].get('image'))
    changed=shown[0]!=playing or (playing and shown[1]!=now[1])
    assert (('widget_invalidate_force',bar) in [c[:2] for c in m.calls])==changed, (status,icon)
    shown=now
    assert m.nodes[state]['visible']==playing and (not playing or m.nodes[state]['image']==icon)
    assert m.nodes[idle]['visible']==int(not playing and not equalizer)
    assert tuple(m.get(title+O[k]) for k in ('W_X','W_Y','W_W','W_H'))==clock_box
passed()
# Stock's 1 s tick (systembar_showface) hides img_state while its own player is stopped, as it
# is under Spotify or radio; the hook shows the payload's state again before any paint.
m.handlers[payload_syms['stock_showface_trampoline']]='stock_showface'
m.word(0xa50084,2); m.nodes[eq]['visible']=1; m.nodes[state]['visible']=0
assert m.call(address=HOOKS['systembar_showface'][0],args=(bar,0,0,0),gap=0)==0
assert ('stock_showface',bar) in [c[:2] for c in m.calls] and m.nodes[state]['visible']==1 and m.nodes[state]['image']=='oled_play_d'
passed()
# Battery's row click (setting_click) steps OLED's two values, Icon and Percent; its row's label (list_value on the
# Display page, none here) is left out.
m.handlers[payload_syms['list_value']]='stock_list_value'  # a payload call: no t9
for mode in (1,0,1,0):
    m.call(address=payload_syms['setting_click'],args=(2,m.event,0,0),gap=0)
    assert [c[1] for c in m.calls if c[0]=='write_int_config']==[mode]
    m.calls=[]; paint_live_bar(); m.advance(0,clear=False)
    assert ('widget_invalidate_force',bar) in [c[:2] for c in m.calls]  # the whole bar, after the frame
    if mode != 1:
        assert m.nodes[slot]['visible']
        assert m.get(slot+O['W_W'])==OLED_G.batt_w+O['BATT_NUB_W']
        assert signed(m.get(views[1]+O['W_X']))+signed(m.get(slot+O['W_X']))+m.get(slot+O['W_W'])==375-O['STATUS_ROW_INSET']
passed()

# Fast-scroll letter (iPod): once the wheel ramp moves more than one row per detent on a long list,
# the selected row's first character (a-z upper-cased, leading spaces skipped) is drawn centred over
# the list on a translucent dark rounded square, LETTER_MS after the last detent a timer repaints it
# away, and every canvas text/fill/clip state it touched is restored. Stock draws none.
def canvas_state(m):
    return (m.lcd_colors(),m.get(m.lcd+O['LCD_TEXT_COLOR']),m.get(m.canvas+O['CANVAS_ALIGN_V']),
            m.get(m.canvas+O['CANVAS_ALIGN_H']),m.clip)
def letter_machine(virtual,n=40,**config):
    m=Machine(); m.config.update(FLAT); m.config.update(config); m.word(m.canvas+O['CANVAS_ALIGN_V'],2)
    m.word(m.canvas+O['CANVAS_ALIGN_H'],3); m.word(m.lcd+O['LCD_TEXT_COLOR'],0x11223344)
    if virtual:
        w,rs,es=m.table_page(rebind=True); m.word(w+O['TABLE_ROWS'],n)
    else:
        w,es=m.page_list(n,extent=n*48,name='allmusic_page'); rs=es
    for e in es: m.nodes[e]['text']='row'
    m.paint(w,gap=0); return m,w,rs,es
def selected_entry(m,w,rs,es):
    return next(e for r,e in zip(rs,es) if (m.get(r+O['ROW_INDEX']) if r!=e else es.index(e))==m.selected(w))
box=(120-O['LETTER_BOX']//2,48-O['LETTER_BOX']//2,O['LETTER_BOX'],O['LETTER_BOX'])
for virtual in (False,True):
    m,w,rs,es=letter_machine(virtual); before=canvas_state(m)
    slow=3  # one-row ticks 100ms apart before the ramp's second row
    for _ in range(slow): assert m.call(gap=100)==11; m.paint(w,gap=0); assert not m.letters   # step 1
    assert m.call(gap=100)==11 and m.selected(w)==slow+2; m.paint(w,gap=0)     # step 2
    assert m.letters==[dict(text='R',rect=box,color=0xffffffff,font=('inter',O['LETTER_PX']),align=(1,1),  # draw_centred: the theme's face
                            clip=(0,0,240,96))]
    fills=[r for r in m.rounded if r['kind']=='fill']  # the selection's bar is bands: the letter's square alone
    assert fills==[dict(kind='fill',rect=box,bg=0,color=O['LETTER_ALPHA']<<24|O['FILL_RGB'],
                        radius=O['LETTER_RADIUS'],width=None,clip=(0,0,240,96))]
    assert canvas_state(m)==before; passed()
    # A library list shows the letter its row sorts under: a leading article is skipped (ringnav_sort_key).
    for text,glyph in (('zeta','Z'),('  apple','A'),('Émile','Émile'[0]),('東京','東'),('9 lives','9'),('The Cure','C'),
                       ('Them Crooked Vultures','T'),('a Tribe','T'),('An Émile','Émile'[0]),('Anthrax','A'),('The','T')):
        m.nodes[selected_entry(m,w,rs,es)]['text']=text; m.paint(w,gap=0)
        assert [l['text'] for l in m.letters]==[glyph],text; passed()
    m.nodes[selected_entry(m,w,rs,es)]['text']='   '; m.paint(w,gap=0)
    assert not m.letters and canvas_state(m)==before; passed()
    m.nodes[selected_entry(m,w,rs,es)]['text']='row'
    # A further fast detent re-arms; the letter clears LETTER_MS after the last one.
    assert m.call(gap=100)==11; m.advance(O['LETTER_MS']-1); m.paint(w,gap=0); assert m.letters
    m.advance(1); assert any(c[:2]==('widget_invalidate_force',w) for c in m.calls)
    m.paint(w,gap=0); assert not m.letters and canvas_state(m)==before; passed()
# Only a name-sorted list arms it (az_index.c name_sorted): not Library's category list beside Home's pane, Wi-Fi's
# networks (by signal), Internet Radio (file order) or a settings list; the same spin on Songs shows it.
for name,shown in (('home_page',False),('wifi_page',False),('radio_page',False),('sysset_page',False),
                   ('allmusic_page',True)):
    m=Machine(); w,es=m.page_list(40,extent=40*48,name=name)
    for e in es: m.nodes[e]['text']='row'
    m.paint(w,gap=0)
    for _ in range(4): m.call(gap=100)
    m.paint(w,gap=0); assert bool(m.letters)==shown,name; passed()
# Nor over a page whose list is in an order of its own (name_sorted says no): Up Next, Chapters,
# and Library's song list by the class it shows and Songs' Sort. That list in name order, and another page left with
# the song list's class and Sort, keep the letter.
# A playlist's Sort is its own flag (0 by name, 1 Time added), read in sort's place for 0xff13.
for name,cls,sort,shown in (('upnext_page',0xf001,0,False),('chapters_page',0xf001,0,False),('allmusic_page',0xf009,0,False),
                            ('allmusic_page',0xf008,0,False),('allmusic_page',0xf007,0,False),('allmusic_page',0xf006,0,True),
                            ('allmusic_page',0xf00a,0,True),('allmusic_page',0xff13,1,False),('allmusic_page',0xff13,0,True),
                            ('allmusic_page',0xf001,3,False),('allmusic_page',0xf001,4,False),('allmusic_page',0xf001,1,True),
                            ('allmusic_page',0xf001,2,True),('allmusic_page',0xf001,5,True),('allmusic_page',0xf002,3,True),
                            ('localclass_page',0xf009,3,True)):
    m=Machine(); m.word(syms['g_class_type'],cls); m.word(syms['g_playlistsort_flag' if cls==0xff13 else 'g_sort_flag'],sort)
    w,es=m.page_list(40,extent=40*48,name=name)
    for e in es: m.nodes[e]['text']='row'
    m.paint(w,gap=0)
    for _ in range(4): m.call(gap=100)
    m.paint(w,gap=0); assert bool(m.letters)==shown,(name,hex(cls),sort); passed()
# Nor over Edit Playlist's songs (ple.mode 0, in the order they were added); its Add Songs picker (mode 1) is A to Z.
for mode,shown in ((0,False),(1,True)):
    m=Machine(); m.word(payload_syms['ple'],1); m.word(payload_syms['ple']+28,mode)  # ple.page, ple.mode
    w,es=m.page_list(40,extent=40*48,name='playlistedit_page')
    for e in es: m.nodes[e]['text']='row'
    m.paint(w,gap=0)
    for _ in range(4): m.call(gap=100)
    m.paint(w,gap=0); assert bool(m.letters)==shown,mode; passed()
# Never at one row per detent, nor on a list of at most SHORT_LIST_MAX (16) rows.
for n,gap in ((40,141),(16,100)):
    m,w,rs,es=letter_machine(False,n)
    for _ in range(6): m.call(gap=gap)
    m.paint(w,gap=0); assert not m.letters and hold_done(m); passed()  # the scroll indicator's timer alone
# Stock's own scroll tick (0x5fb744), executed from the image, not mocked: it stops an unfocused
# only_focus label at once and steps any other, which is why marq_moved clears only_focus.
for only_focus,want,stepped in ((1,7,False),(0,None,True)):
    m=Machine(); t=m.node('hscroll_label'); m.byte(t+O['HSCROLL_ONLY_FOCUS'],only_focus)
    m.handlers[0x5fad3c]='hscroll_label_cast'; m.handlers[0x5fb330]='hscroll_label_step'
    info=m.alloc(0x58); m.word(info+0x20,t)
    ret=m.call(address=0x5fb744,args=(info,0,0,0),gap=0); names=[c[0] for c in m.calls]
    assert ('hscroll_label_step' in names)==stepped and ('hscroll_label_stop' in names)==(not stepped) and want in (None,ret),(names,ret); passed()
# The rows this polish renamed or added fit stock's ellipsis: the settings text's 275 px less its 4 px of margins.
from fontTools.ttLib import TTFont
font=TTFont(B/'default.ttf'); advance=lambda s: sum(font['hmtx'][font.getBestCmap()[ord(c)]][0] for c in s)*24/font['head'].unitsPerEm
assert all(advance(s)<=271 for s in ('Artist View: Cover Flow','Refresh Cover Flow','Sort: Recently Added',"Artist's Albums: Year",'Years: Newest First','Time in Title')); passed()
# Every scrolling label's holds and pace (ringnav_hscroll_start, ringnav_hscroll_lull) through stock's
# own hscroll_label code, run from the image: start, its delay, tick (0x5fb744), step, lull and stop.
# A label shows its offset while it runs; stopped, stock paints its start with the ellipsis ('rest').
def hscroll_machine():
    m=Machine(); m.widget_timers=True
    for name in ('hscroll_label_start','hscroll_label_stop','hscroll_label_set_xoffset'): m.handlers.pop(syms[name])
    m.mock('widget_invalidate'); return m
def hscroll_label(m,text_w=300,w=200,speed=0.1,delay=0,lull=2000,loop=1,yoyo=0,only_focus=0):
    """Stock's defaults (0x5fb968) after set_hscroll_label_attribute (speed 0.1, lull 2000); range text_w+4-w."""
    t=m.node('hscroll_label'); m.u.mem_write(t+0x78,bytes(0x38)); m.word(t+O['W_W'],w); m.word(t+O['HSCROLL_TEXT_W'],text_w)
    m.word(t+0x9c,0xffffffff); m.word(t+0x84,5000); m.word(t+0x80,lull); m.word(t+O['HSCROLL_DELAY'],delay)
    m.byte(t+O['HSCROLL_LOOP'],loop); m.byte(t+O['HSCROLL_YOYO'],yoyo); m.byte(t+O['HSCROLL_ONLY_FOCUS'],only_focus)
    m.u.mem_write(t+O['HSCROLL_SPEED'],struct.pack('<f',speed)); return t
def shown(m,t,over=104):
    if not m.get(t+O['HSCROLL_TIMER']): return 'rest'
    x=m.get(t+0x90); return 'start' if x==0 else 'end' if x==over else 'moving'
def timeline(m,t,ms):
    """What t shows each ms for ms, as [state, ms] runs."""
    runs=[]
    for _ in range(ms):
        m.advance(1); v=shown(m,t)
        if runs and runs[-1][0]==v: runs[-1][1]+=1
        else: runs.append([v,1])
    return runs
hold=O['MARQUEE_HOLD_MS']; scroll=int(104/struct.unpack('<f',struct.pack('<I',O['HSCROLL_PACE']))[0])  # 2079: NP's pace
assert scroll==2079
def pass_ok(runs,yoyo=False):
    """Its start held hold (until the second 16 ms tick moves it a whole pixel), a scroll at the pace, its end held hold."""
    assert len(runs)>=3, runs; (s,start),(v,moving),(e,end)=runs[:3]
    assert (s,v,e)==('start','moving','end') and hold<=start<hold+40 and abs(moving+start+end-2*hold-scroll)<40, runs
    assert end==hold+16*yoyo, runs  # a yoyo label's end moves on its first tick back
# A delay-0 label at stock's row pace (0.1 px/ms) and lull, Now Playing's title (lull and the old delay 1500,
# 0.05) and a label with no speed (its 5 s duration) run the same: hold, scroll, hold, snap back, hold, scroll.
seen=[]
for kw in (dict(),dict(speed=0.05,delay=1500,lull=1500),dict(speed=0.0)):
    m=hscroll_machine(); t=hscroll_label(m,**kw)
    m.call(address=syms['hscroll_label_start'],args=(t,),gap=0); runs=timeline(m,t,2*(hold+scroll+hold)+100)
    pass_ok(runs); pass_ok(runs[3:]); assert runs[6][0]=='start'
    assert m.get(t+O['HSCROLL_DELAY'])==hold and m.get(t+O['HSCROLL_SPEED'])==O['HSCROLL_PACE']; seen.append(runs)
assert seen[0]==seen[1]==seen[2]; passed()
# A slower label keeps its pace.
m=hscroll_machine(); t=hscroll_label(m,speed=0.02); m.call(address=syms['hscroll_label_start'],args=(t,),gap=0)
runs=timeline(m,t,hold+5300); assert runs[1][0]=='moving' and runs[1][1]>5000; passed()
# A yoyo label scrolls back, holding hold at each end, not its lull as well.
m=hscroll_machine(); t=hscroll_label(m,yoyo=1); m.call(address=syms['hscroll_label_start'],args=(t,),gap=0)
runs=timeline(m,t,2*(hold+scroll)+2*hold+100); pass_ok(runs,yoyo=True)
assert runs[3][0]=='moving' and runs[4][0]=='start' and hold<=runs[4][1]<hold+60,runs; passed()  # its last tick lands on 0
# An unfocused only_focus label stops on its first tick, as stock's, and rests on its start.
m=hscroll_machine(); t=hscroll_label(m,only_focus=1); m.call(address=syms['hscroll_label_start'],args=(t,),gap=0)
assert timeline(m,t,hold+100)==[['start',hold+15],['rest',85]] and not m.timers and m.get(t+0x90)==0; passed()
# A label that doesn't loop (HSCROLL_ONCE) scrolls once, holds its end, and rests on its start; a yoyo one
# too, where stock parked it on its end; a running one isn't restarted.
from build import HSCROLL_ONCE
assert struct.unpack_from('<I',demo,fileoff(demo,HSCROLL_ONCE[0]))[0]==HSCROLL_ONCE[2]
for yoyo in (0,1):
    m=hscroll_machine(); t=hscroll_label(m,loop=0,yoyo=yoyo); m.call(address=syms['hscroll_label_start'],args=(t,),gap=0)
    m.advance(100); m.call(address=syms['hscroll_label_start'],args=(t,),gap=0)
    runs=timeline(m,t,hold+scroll+hold+100); runs[0][1]+=100; pass_ok(runs)
    assert runs[3:]==[['rest',200]] and not m.timers and m.get(t+0x90)==0,runs; passed()
# Stock's label attributes (set_hscroll_label_attribute, run from the image): its speed, lull and ellipsis. Since V1.36 it
# also gives the label a loop_interval_distance of its width, with which a pass has no end to hold: HSCROLL_GAP drops that
# call, stock makes it. In Menu Mode alone it sets only_parent_focus, and WHEEL_MODE keeps stock out of Menu Mode.
from build import HSCROLL_GAP, ALBUM_LABELS, FOCUS_RESTORE
for patched in (True,False):
    m=Machine(patched); m.handlers.pop(syms['set_hscroll_label_attribute'],None)
    m.mock('hscroll_label_set_speed','hscroll_label_set_lull','hscroll_label_set_ellipses','widget_set_prop_int','hscroll_label_set_only_parent_focus')
    t=m.node('hscroll_label'); m.word(t+O['W_W'],200)
    for mode in (1,0):
        m.calls=[]; m.byte(syms['g_wheelmode'],mode); m.call(address=syms['set_hscroll_label_attribute'],args=(t,0,0,0),gap=0)
        want=[('hscroll_label_set_speed',t,O['HSCROLL_PACE']),('hscroll_label_set_lull',t,2000),('hscroll_label_set_ellipses',t,1)]
        gap=[c for c in m.calls if c[0]=='widget_set_prop_int']
        assert [c[:3] for c in m.calls if c[0]!='widget_set_prop_int']==want+[('hscroll_label_set_only_parent_focus',t,1)]*(not mode),(patched,mode,m.calls)
        assert [(m.text(c[2]),c[3]) for c in gap]==[('loop_interval_distance',200)]*(not patched),(patched,gap)
passed()
# Albums' artist line and grid titles keep a focus flag in Volume Mode too (ALBUM_LABELS; test/fw has the hook's two calls).
# Each site is the call for the label stock names beside it, not the list row's title, whose call is the same function's.
def named_near(at):
    """The names stock gives widgets within 0x30 bytes of at: addiu $a1, $a1, a string's low half."""
    words=struct.unpack_from('<25I',demo,fileoff(demo,at-0x30))
    return {demo[o:demo.index(b'\0',o)].decode() for o in (fileoff(demo,0x780000+signed16(w&0xffff)) for w in words if w>>16==0x24a5)}
def signed16(x): return x-0x10000 if x&0x8000 else x
for at,name in zip(ALBUM_LABELS,('scrlabel2_name','scrlabel1_name1','scrlabel1_name2','scrlabel1_name3')):
    assert struct.unpack_from('<I',demo,fileoff(demo,at))[0]==jal(payload_syms['ringnav_album_label']) and name in named_near(at),(hex(at),named_near(at))
assert 'scrlabel2_name' not in named_near(0x4a6bfc)  # the list row's title's call
passed()
# Stock's focus doesn't come back to a reloaded page's row (FOCUS_RESTORE): with a focused widget in $v0, V1.36's album page and
# Playlists go on to read its name; patched, both go on as with none, their row index -1.
def falls(m,start,stops):
    sp=m.alloc(0x100); m.word(sp+0x10,0xa3aae0); reached=[]
    for r,v in ((UC_MIPS_REG_SP,sp),(UC_MIPS_REG_GP,0xa3aae0),(UC_MIPS_REG_V0,m.alloc(0x40)),(UC_MIPS_REG_S0,7)): m.u.reg_write(r,v)
    def stop(u,address,size,unused):
        if address in stops: reached.append(address); u.emu_stop()
    hook=m.u.hook_add(UC_HOOK_CODE,stop); m.u.emu_start(start,0x1000000,count=40); m.u.hook_del(hook)
    return reached[0],signed(m.u.reg_read(UC_MIPS_REG_S0))
for patched in (True,False):
    m=Machine(patched)
    assert falls(m,FOCUS_RESTORE[1][0],(0x4b5954,0x4b5ad0))==((0x4b5954,-1) if patched else (0x4b5ad0,7)),patched
    assert falls(m,FOCUS_RESTORE[0][0],(0x4a8eec,0x4a8f18))[0]==(0x4a8f18 if patched else 0x4a8eec),patched
passed()
# The selected row's long title scrolls as soon as the selection settles, MARQUEE_PASSES of those
# passes, then rests on its start; it stops as the selection moves or the screen darkens; a title
# that fits or stock scrolls by itself is left alone; in a table's pool, a new row in the same
# widget counts as a move.
def marquee_list(over=True,only_focus=1):
    m=hscroll_machine(); m.marquee=True; w,es=m.page_list(4,height=192,extent=192,name='folder_page')
    labels=[]
    for e in es:
        t=hscroll_label(m,text_w=300 if over else 150,loop=0,only_focus=only_focus); m.nodes[e]['children']=[t]; m.word(t+O['W_PARENT'],e); labels.append(t)
    m.call(); m.paint(w,gap=0)
    return m,w,labels
running=lambda m,labels: [t for t in labels if m.get(t+O['HSCROLL_TIMER'])]
focus_only=lambda m,t: m.u.mem_read(t+O['HSCROLL_ONLY_FOCUS'],1)[0]
m,w,labels=marquee_list(); sel=m.selected(w); assert sel>=0
runs=timeline(m,labels[sel],O['MARQUEE_PASSES']*(hold+scroll+hold)+100)
for k in range(O['MARQUEE_PASSES']): pass_ok(runs[3*k:])
assert runs[3*O['MARQUEE_PASSES']:]==[['rest',runs[-1][1]]] and focus_only(m,labels[sel])==1 and not m.timers
assert running(m,labels)==[] and all(m.get(t+0x90)==0 for t in labels)
m.paint(w,gap=0); m.advance(1); assert not m.timers; passed()  # passes spent: nothing until the selection moves
m,w,labels=marquee_list(); m.advance(hold+100); on=running(m,labels); assert on==[labels[m.selected(w)]] and shown(m,on[0])=='moving'
m.call(); m.paint(w,gap=0); m.advance(1); assert focus_only(m,on[0])==1 and shown(m,on[0])=='rest'
assert running(m,labels)==[labels[m.selected(w)]] and shown(m,labels[m.selected(w)])=='start'; passed()
m.byte(syms['g_backlight_status'],0); m.advance(hold+scroll+hold); assert running(m,labels)==[] and not m.timers
m.byte(syms['g_backlight_status'],1); m.paint(w,gap=0); m.advance(1); assert len(running(m,labels))==1; passed()
for kw in (dict(over=False),dict(only_focus=0)):
    m,w,labels=marquee_list(**kw); m.advance(hold+1); assert not running(m,labels) and not m.timers; passed()
m=hscroll_machine(); m.marquee=True; w,rs,es=m.table_page(rebind=False); labels={}
for e in es:
    t=hscroll_label(m,loop=0,only_focus=1); m.nodes[e]['children']=[t]; m.word(t+O['W_PARENT'],e); labels[e]=t
m.call(); m.paint(w,gap=0); m.advance(hold+100); on=running(m,labels.values()); assert len(on)==1 and shown(m,on[0])=='moving'
first=m.selected(w); m.call(); m.bind(rs,48); m.paint(w,gap=0); assert m.selected(w)==first+1  # the pool moved with it: the same widget
m.advance(1); assert running(m,labels.values())==on and shown(m,on[0])=='start'; passed()  # stopped, then started on its new row
# Touch drops the spin, and with it the letter.
m,w,rs,es=letter_machine(False)
for _ in range(5): m.call(gap=100)
m.paint(w,gap=0); assert m.letters
m.touch(); m.paint(w,gap=0); assert not m.letters; passed()
# Elsewhere (a folder sorts by its file name) the first character stays.
m=Machine(); w,es=m.page_list(40,extent=40*48,name='folder_page')
for e in es: m.nodes[e]['text']='The Cure'
for _ in range(5): m.call(gap=100)
m.paint(w,gap=0); assert [l['text'] for l in m.letters]==['T']; passed()
# A settings menu is not alphabetical: no letter however long.
m=Machine(); w,es=m.page_list(40,extent=40*48,name='playset_page')
for e in es: m.nodes[e]['text']='row'
for _ in range(5): m.call(gap=100)
m.paint(w,gap=0); assert not m.letters; passed()

WRITERS={'scanAllMusicFile':'stock_scan_all','scanSpecFolder':'stock_scan_folder','deleteMusicFromMusicDb':'stock_delete_song'}
