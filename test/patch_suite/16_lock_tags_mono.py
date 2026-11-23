class LockMachine(Machine):
    def hook(self,u,address,size,_):
        if self.handlers.get(address)=='stock_input': self.locks.append(self.u.mem_read(syms['g_keylock_flag'],1)[0])
        return super().hook(u,address,size,_)
l=LockMachine(); l.handlers[inp+12]='stock_input'; l.handlers[post+12]='stock_post_key'
l.byte(syms['g_backlight_status'],0); l.byte(syms['g_keylock_flag'],1)
def lock_seen(key,kind,posted=None,gap=0):
    if posted: l.call(address=post,args=(0x1000400,int(kind==DOWN),posted,0),gap=1000)
    l.advance(gap,clear=False); l.word(l.event+O['EVENT_TIME'],l.now); l.locks=[]
    l.call(key,address=inp,args=(l.wm,l.event,0,0),event_type=kind,gap=0)
    return l.locks
for key in (O['KEY_PLAY'],O['KEY_BACK_BTN'],O['KEY_FWD_BTN']):
    assert lock_seen(key,DOWN,key)==[0] and lock_seen(key,UP,key)==[0]
    assert lock_seen(key,DOWN,gap=1000)==[] and lock_seen(key,UP,gap=1000)==[]
assert lock_seen(O['KEY_FWD_BTN'],DOWN,O['KEY_PLAY'])==[] and lock_seen(O['KEY_PLAY'],DOWN)==[0]  # by key
assert lock_seen(O['KEY_PLAY'],UP,O['KEY_PLAY'],gap=1)==[1]  # and by its stamp
assert l.u.mem_read(syms['g_keylock_flag'],1)==b'\1'; passed()
# Play/Pause and Return hold after KEY_HOLD_MS (HOLD_KEYS), every other key at stock's time.
from build import HOLD_KEYS
assert all(struct.unpack_from('<I',demo,fileoff(demo,a))[0]==(new or jal(payload_syms[fn])) for a,_,new,fn in HOLD_KEYS)
for key,ms in ((O['KEY_PLAY'],700),(O['KEY_RETURN'],700),(O['KEY_CENTER'],1000)):
    l.timers={}; l.call(address=payload_syms['ringnav_hold_timer'],args=(0x1234,l.wm,1000,key),gap=0)
    assert [t[3] for t in l.timers.values()]==[ms]
for key in (O['KEY_PLAY'],O['KEY_RETURN']):
    info=l.call(address=payload_syms['ringnav_hold_info'],args=(l.wm,key),gap=0)
    assert struct.unpack('<2i',l.u.mem_read(info,8))==(key,700)
passed()
# Lighting the screen stalls the UI loop about WAKE_MS (stock's on_wm_keyup_fun, the paint), so a
# blind double press's last press is dispatched only after the first lit loop pass: still dropped.
for double in ('0','1'):
    w=Machine(); w.config['WAKEDOUBLE']=double; w.handlers[inp+12]='stock_input'; w.handlers[sleep_hook+12]='stock_sleep'
    def press(kind,gap):
        w.calls=[]; assert w.call(O['KEY_CENTER'],address=inp,args=(w.wm,w.event,0,0),event_type=kind,gap=gap,clear=False)==0
        return any(c[0]=='stock_input' for c in w.calls)
    def loop_pass(): w.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0,clear=False)
    w.byte(syms['g_backlight_status'],0); loop_pass()
    assert double=='0' or not press(DOWN,1000) and not press(UP,50)
    assert press(DOWN,100) and press(UP,50)  # it wakes the screen, the loop stalls, then its first lit pass
    w.byte(syms['g_backlight_status'],1); w.now+=O['WAKE_MS']; loop_pass()
    assert not press(DOWN,0) and not press(UP,50) and press(DOWN,O['WAKE_MS']) and press(UP,50),double
passed()
m,rows,texts,*_=settings_page('power','scroll_view_powermanager',{'CHARGELIMIT':'1','WAKEDOUBLE':'1','VOLGUARD':'1','STANDBYLIMIT':'2'})
assert texts()==[limits[1],f"Charge Limit: {O['CHARGE_STOP']}%",'USB DAC Charging','Smart Wi-Fi','system_carmode','Battery History','Boot to: Rockbox']
m,rows,texts,*_=settings_page('display','scroll_view_display',{'WAKEDOUBLE':'1','VOLGUARD':'1'},stock_rows=3,sub=3)
assert texts()[4:]==['Wake: Double Press']; passed()  # VOLGUARD, Volume Guard's key in 1.8.2 and before: no row
m,rows,texts,*_=settings_page('power','scroll_view_powermanager',{'STANDBYLIMIT':'4'})
assert texts()[0]=='Standby Limit: Off'; passed()  # out of range: Off
# Playback: stock V1.36's thirteen rows less its last two (Volume and Album Display, whose page's rows are here, on
# Display and on Library > Folders, and Folder skip, Library > Folders'), then ours, EQ and the Library first; stock's
# Balance (10) stays, hidden, for the inline one, and Headphones Required (8), which is always on. The Sleep Timer, Volume (its slider page, below) and the Library open
# from their rows; Max Volume, Balance and Default Volume's value are inline sliders (inline_slider.c), Default Volume a
# switch with its slider row under it; Smart Crossfade is one switch (crossfade.c xfade_row).
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13)
assert len(rows)==11 and icons==['playset_gapless','playset_memplay','playset_balance','playset_balance','system_powermanager','playset_maxvol','playset_maxvol','playset_bootvol','playset_bootvol','playset_balance','playset_folderjump'],icons
assert texts()==['Smart Crossfade','Resume Songs: 30 min','Mono Audio','Swap L/R','Sleep Timer','Volume: 0','Max Volume','Default Volume','Level','Balance','Library'],texts()
assert len(m.nodes[m.settings_view]['children'])==22 and not set(m.entries[11:])&set(m.nodes[m.settings_view]['children'])
assert [(m.entries+rows).index(c) for c in m.nodes[m.settings_view]['children']]==[2,23,3,7,13,6,14,8,17,18,19,20,21,0,9,22,10,15,16,1,4,5]  # EQ, Library first
assert m.nodes[m.entries[10]].get('visible',1)==0 and m.nodes[rows[8]].get('visible',1)==0  # stock's Balance; the slider of a switch that is off
assert m.nodes[m.entries[8]].get('visible',1)==0  # Headphones Required: always on (output_rule.c), no row
assert [click(4),m.opened[-1]]==[[],('systemset/sleepshutdown_page',None)]
n=len(m.opened); assert click(6)==[] and len(m.opened)==n  # Max Volume: grabbed in place, no page
assert click(7)==[(1,'PLAYSET','BOOTVOLFLAG')] and m.nodes[rows[8]].get('visible',1)==1  # Default Volume on: its Level shows
assert click(10)==[] and m.opened[-1]==('playset/playset_page',None) and m.get(payload_syms['playset_sub'])==0
passed()
# Playback > Library (library_settings.c): stock's Playback page again, its rows gone, ours built; a chevron row opens
# its page the same way (playset_sub naming it while its init runs).
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13,sub=1)
assert texts()==['On-The-Go','Sorting','Artists: Artist','Artist View: Cover Flow','Playlists','Folders','Scan Song Durations']  # no Streaming Songs: a signed-in server's songs always join
assert icons==['playset_folderjump']*7 and m.nodes[m.settings_view]['children']==rows
assert click(0)==[(0,'Q2POD','OTG')] and click(0)==[(1,'Q2POD','OTG')]  # On-The-Go, on unless chosen
for i in (1,4,5):
    assert click(i)==[] and m.opened[-1]==('playset/playset_page',None) and m.get(payload_syms['playset_sub'])==0
assert click(2)==[(1,'PLAYSET','ARTISTTYPE')] and m.get(syms['artist_type'])==1 and texts()[2]=='Artists: Album Artist'
assert click(2)==[(0,'PLAYSET','ARTISTTYPE')] and m.get(syms['artist_type'])==0
# Artist View (Q2POD ARTISTCF), Cover Flow unless chosen: List, Grid, then Cover Flow again
assert [click(3) for _ in range(3)]==[[(2,'Q2POD','ARTISTCF')],[(0,'Q2POD','ARTISTCF')],[(1,'Q2POD','ARTISTCF')]] and texts()[3]=='Artist View: Cover Flow'; passed()
# Library > Sorting: Songs (its picker's row), Artist's Albums (ARTISTAZ) and Years (YEAROLD)
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13,sub=2)
assert texts()==['Songs: Title \u2191',"Artist's Albums: Year",'Years: Newest First']
assert click(1)==[(1,'Q2POD','ARTISTAZ')] and texts()[1]=="Artist's Albums: A–Z"
assert click(1)==[(0,'Q2POD','ARTISTAZ')] and texts()[1]=="Artist's Albums: Year"
assert click(2)==[(1,'Q2POD','YEAROLD')] and texts()[2]=='Years: Oldest First'
assert click(2)==[(0,'Q2POD','YEAROLD')] and texts()[2]=='Years: Newest First'
assert click(0)==[] and m.opened[-1]==('systemset/keylock_page',None); passed()
# Library > Playlists: Playlist View (PLAYLISTCF, List unless chosen), a gap, then Import and Export Playlists
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13,sub=3)
assert texts()==['Playlist View: List','Import Playlists','Export Playlists']
kids=m.nodes[m.settings_view]['children']; assert len(kids)==4 and m.nodes[kids[1]]['type']=='label' and m.get(kids[1]+O['W_H'])==12
assert click(0)==[(1,'Q2POD','PLAYLISTCF')] and texts()[0]=='Playlist View: Cover Flow'
assert click(0)==[(0,'Q2POD','PLAYLISTCF')] and texts()[0]=='Playlist View: List'; passed()
# Library > Folders: Folder Root, stock's Folder skip kept, Folder Album Cover (stock's own byte and key)
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13,sub=4)
assert texts()==['Folder Root: Card','playset_foldercover'] and icons==['playset_folderjump']*2
assert [(m.entries+rows).index(c) for c in m.nodes[m.settings_view]['children']]==[13,12,14]
m.byte(syms['g_foldercover_flag'],0); assert click(1)==[(1,'PLAYSET','FOLDERCOVER')] and m.u.mem_read(syms['g_foldercover_flag'],1)==b'\1' and switch_image(m,rows[1])=='switch_on'
assert click(1)==[(0,'PLAYSET','FOLDERCOVER')] and switch_image(m,rows[1])=='switch_off'; passed()
# Display: Enlarged Album Art, then Reduce Motion last; Enlarged Album Art flips stock's own byte and key
m,rows,texts,icons,click=settings_page('display','scroll_view_display',stock_rows=4)
assert texts()[-2:]==['playset_covermode','Reduce Motion'],texts()
m.byte(syms['g_bigcovermode'],0); assert click(len(rows)-2)==[(1,'PLAYSET','COVERMODE')] and m.u.mem_read(syms['g_bigcovermode'],1)==b'\1' and switch_image(m,rows[-2])=='switch_on'
assert click(len(rows)-2)==[(0,'PLAYSET','COVERMODE')] and m.u.mem_read(syms['g_bigcovermode'],1)==b'\0'; passed()
# Artists is Album Artist until a choice is saved (read_artistype_value); a saved Artist stays.
for config,want in (({},1),({'ARTISTTYPE':'0'},0),({'ARTISTTYPE':'1'},1)):
    a=Machine(); a.config.update(config); a.word(syms['artist_type'],7)
    a.call(address=HOOKS['read_artistype_value'][0],args=(0,0,0,0),gap=0)
    assert a.get(syms['artist_type'])==want and a.config_reads[-1][1:3]==('PLAYSET','ARTISTTYPE'),config
passed()
# A FLAC's "ALBUM ARTIST" comment (foobar2000's spelling) when stock's libtag found no ALBUMARTIST (ringnav_music_info).
class TagMachine(CardMachine):
    def __init__(self,files):
        super().__init__({},files); self.stock_artist=b''
        self.handlers.pop(syms['toolsGetMusicInfo'],None)  # run the hook; only stock's body is mocked
        tramp=int(manifest['patch_symbols']['stock_music_info_trampoline'],16)
        self.handlers[tramp]='stock_music_info'; self.u.hook_add(UC_HOOK_CODE,self.hook,begin=tramp,end=tramp)
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='stock_music_info': return super().hook(u,address,size,unused)
        u.mem_write(u.reg_read(UC_MIPS_REG_A1)+0x580,self.stock_artist+b'\0')
        self.returns(1)
    def album_artist(self,path,stock=b''):
        self.stock_artist=stock; info=self.alloc(0x6a0); p=self.alloc(256); self.u.mem_write(p,path.encode()+b'\0')
        assert self.call(address=HOOKS['toolsGetMusicInfo'][0],args=(0,info,p,0),gap=0)==1
        return self.text(info+0x580)
def flac(*comments,picture=0):
    vc=struct.pack('<I',6)+b'vendor'+struct.pack('<I',len(comments))+b''.join(struct.pack('<I',len(c))+c for c in comments)
    blocks=[(0,bytes(34))]+([(6,bytes(picture))] if picture else [])+[(4,vc)]
    return bytearray(b'fLaC'+b''.join(bytes([t|(0x80 if i==len(blocks)-1 else 0)])+len(d).to_bytes(3,'big')+d
                                      for i,(t,d) in enumerate(blocks))+b'audio')
t=TagMachine({'/mnt/mmc/a.flac':flac(b'ARTIST=Chikoi The Maid',b'Album Artist=Chikoi The Maid',picture=70000),
              '/mnt/mmc/b.flac':flac(b'ARTIST=Evan Call'),'/mnt/mmc/c.FLAC':flac(b'ALBUM ARTIST='+b'x'*300),
              '/mnt/mmc/d.mp3':flac(b'ALBUM ARTIST=No')})
t.files['/mnt/mmc/e.flac']=flac(b'METADATA_BLOCK_PICTURE='+b'A'*100000,b'ALBUM ARTIST=Past the cover')  # a block over 64 KB
assert t.album_artist('/mnt/mmc/a.flac')=='Chikoi The Maid'
assert t.album_artist('/mnt/mmc/a.flac',b'Stock')=='Stock'  # stock's ALBUMARTIST wins
assert t.album_artist('/mnt/mmc/b.flac')=='' and t.album_artist('/mnt/mmc/c.FLAC')=='x'*255
assert t.album_artist('/mnt/mmc/d.mp3')=='' and t.album_artist('/mnt/mmc/none.flac')==''
assert t.album_artist('/mnt/mmc/e.flac')=='Past the cover'
assert not t.handles  # every file closed
passed()
# An Ogg file whose comment header is over OGG_TAGS_MAX (4 MB; MusicBee's base64 pictures) is read by
# xiph.c, not stock's TagLib, which held it ~4 times over and got demo OOM-killed in a scan: each text
# field through stock's toolsFixId3ErrorCode, as libtag_c's (isLatin1, Latin-1, UTF-8) would reach it.
def ogg_pages(*packets):  # each packet from a fresh page, 255 lacing values a page
    out=bytearray(); seq=0
    for p in packets:
        lac=[255]*(len(p)//255)+[len(p)%255]; at=0
        for j in range(0,len(lac),255):
            seg=lac[j:j+255]; n=sum(seg)
            out+=b'OggS\0'+bytes([(1 if j else 0)|(0 if seq else 2)])+bytes(8)+struct.pack('<III',1,seq,0)+bytes([len(seg)])+bytes(seg)+p[at:at+n]
            at+=n; seq+=1
    return out
def opus(*comments,picture=0):
    items=([b'METADATA_BLOCK_PICTURE='+b'A'*picture] if picture else [])+list(comments)
    tags=b'OpusTags'+struct.pack('<I',4)+b'test'+struct.pack('<I',len(items))+b''.join(struct.pack('<I',len(c))+c for c in items)
    return ogg_pages(b'OpusHead'+bytes([1,2])+struct.pack('<HIhB',312,48000,0,0),tags,b'\xfc'*40)
class OggTagMachine(TagMachine):
    def __init__(self,files):
        super().__init__(files); self.fixes=[]; self.stock_calls=0
        self.handlers[syms['toolsFixId3ErrorCode']]='fix_id3'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address)
        if name=='stock_music_info': self.stock_calls+=1
        if name!='fix_id3': return super().hook(u,address,size,unused)
        a,b,c,d=[u.reg_read(r) for r in REGS]
        self.fixes.append((self.get(u.reg_read(UC_MIPS_REG_SP)+16),a,self.raw(b),self.raw(c)))
        u.mem_write(d,self.raw(c)[:255]+b'\0'); self.returns(0,clobber=True)  # stock's snprintf of the UTF-8 copy
    def info(self,path,a=0):
        info=self.alloc(0x6a0); self.u.mem_write(info,bytes(0x6a0)); self.fixes=[]; self.stock_calls=0
        assert self.call(address=HOOKS['toolsGetMusicInfo'][0],args=(a,info,self.string(path),0),gap=0)==1
        return info
big=opus(b'TITLE=Born Dead Buried Alive',b'ARTIST=Hypocrisy','artist=ピーター'.encode(),b'ALBUM=The Arrival',b'GENRE='+b'x'*200,
         'COMPOSER=Peter Tägtgren'.encode(),b'ALBUMARTIST=Hypocrisy',b'DATE=2004',b'TRACKNUMBER=1/11',b'DISCNUMBER=2',
         b'MQAENCODER=MQAEncode',picture=4500000)
t=OggTagMachine({'/mnt/mmc/big.opus':big,'/mnt/mmc/cut.opus':big[:big.rfind(b'OggS',0,big.rfind(b'OggS'))],'/mnt/mmc/small.opus':opus(b'TITLE=Small',picture=100000),
                 '/mnt/mmc/flac.opus':flac(b'TITLE=no')})
for a in (0,1):
    info=t.info('/mnt/mmc/big.opus',a)
    assert t.stock_calls==0 and [t.text(info+o) for o in (0x280,0x100,0,0x200,0x480,0x580)]==['Born Dead Buried Alive',
        'Hypocrisy / ピーター','The Arrival','x'*127,'Peter Tägtgren','Hypocrisy']  # the genre through GENRE_COPY
    assert [t.get(info+o) for o in (0x680,0x684,0x688,0x68c,0x690)]==[2004,2,1,0x110 if a else 0x100,48000 if a else 0]  # disc, track
    assert [f[:2] for f in t.fixes]==[(k,k!=2) for k in range(1,7)] and t.fixes[1][2]==b'Hypocrisy / \xd4\xfc\xbf\xfc' and t.fixes[4][2:]==(b'Peter T\xe4gtgren','Peter Tägtgren'.encode())
info=t.info('/mnt/mmc/cut.opus',1)  # its last pages gone, the header never ends: TagLib's no tag, info untouched
assert t.stock_calls==0 and not t.fixes and t.u.mem_read(info,0x6a0)==bytes(0x6a0)
for path in ('/mnt/mmc/small.opus','/mnt/mmc/flac.opus','/mnt/mmc/none.opus'):  # under the cap, not Ogg, missing: stock's
    t.info(path); assert t.stock_calls==1 and not t.fixes,path
assert not t.handles; passed()
# Embedded art (toolsGetAlbumCover's entry, so Now Playing's, the Albums grids' and Coverflow's): an .ogg whose
# comment header is over OGG_TAGS_MAX gets none from stock (get_oggfile_albumpic parses TagLib's whole header),
# so the grid's fallback hands it to ffmpeg; one under the cap goes to stock, as an .opus does, never measured.
assert struct.unpack_from('<I',demo,fileoff(demo,HOOKS['toolsGetAlbumCover'][0]))[0]==j(payload_syms['ringnav_album_cover'])
class OggCoverMachine(TagMachine):
    def __init__(self,files):
        super().__init__(files); self.stock_covers=[]; self.ffmpeg=0
        self.handlers.pop(syms['toolsGetAlbumCover'],None)  # the hook runs; stock's body is mocked
        tramp=int(manifest['patch_symbols']['stock_album_cover_trampoline'],16)
        self.handlers[tramp]='stock_album_cover'; self.u.hook_add(UC_HOOK_CODE,self.hook,begin=tramp,end=tramp)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address)
        if name=='stock_album_cover': self.stock_covers.append(self.text(u.reg_read(REGS[0]))); return self.returns(1,clobber=True)
        if name!='b:waitpid@GLIBC_2.0': return super().hook(u,address,size,unused)
        self.ffmpeg+=1; self.files[self.dst+'.ff']=bytearray(b'jpeg')  # ffmpeg wrote its picture and exited 0
        self.word(u.reg_read(REGS[1]),0); self.returns(u.reg_read(REGS[0]),clobber=True)
    def cover(self,track):
        self.stock_covers=[]; self.ffmpeg=0; self.dst='/mnt/mmc/.sldp/x0.jpg'
        return self.call(address=payload_syms['coverflow_grid_cover'],args=(self.string(track),self.string(self.dst),110,110),gap=0)
t=OggCoverMachine({'/mnt/mmc/big.ogg':big,'/mnt/mmc/small.ogg':opus(b'TITLE=Small',picture=100000),'/mnt/mmc/big.opus':big})
assert t.cover('/mnt/mmc/big.ogg')==1 and not t.stock_covers and t.ffmpeg==1 and t.files[t.dst]==b'jpeg'
for path in ('/mnt/mmc/small.ogg','/mnt/mmc/big.opus'):
    assert t.cover(path)==1 and t.stock_covers==[path] and not t.ffmpeg,path
assert not t.handles; passed()
# The crash log's scan record (tags.c crash_note): each tag read names its file in /tmp/q2-scan-file
# before stock reads it, and every 500th appends "<count> <VmRSS kB> <path>" to /tmp/q2-scan.log, which
# each demo starts afresh and stops past 64 KB.
class ScanNoteMachine(TagMachine):
    STATM=b'30000 22750 1200 1 0 25000 0\n'  # 22,750 resident pages: 91,000 kB
    def __init__(self,files):
        super().__init__(files); self.fds={}; self.seen=[]
        for n in ('open','read','write','close'): self.handlers[syms[n+'@GLIBC_2.0']]='s:'+n
        self.mock('snprintf@GLIBC_2.0')
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        a,b,c=(u.reg_read(r) for r in REGS[:3]); ret=0
        if name=='stock_music_info': self.seen.append(bytes(self.files.get('/tmp/q2-scan-file',b'')))
        if not name.startswith('s:'): return super().hook(u,address,size,unused)
        if name=='s:open':
            path=self.text(a); ret=0xffffffff
            if path=='/proc/self/statm' and b&3==0: self.files[path]=bytearray(self.STATM)
            if path in self.files or b&0x100:
                if b&0x200 or path not in self.files: self.files[path]=bytearray()
                ret=100+len(self.fds); self.fds[ret]=[path,b]
        elif name=='s:read': data=bytes(self.files[self.fds[a][0]][:c]); u.mem_write(b,data); ret=len(data)
        elif name=='s:write': assert self.fds[a][1]&3==1; self.files[self.fds[a][0]]+=bytes(u.mem_read(b,c)); ret=c
        elif name=='s:close': self.fds.pop(a,None)
        self.returns(ret,clobber=True)
t=ScanNoteMachine({'/mnt/mmc/a.flac':flac(b'ALBUM ARTIST=A'),'/tmp/q2-scan.log':bytearray(b'a past demo\n')})
assert t.album_artist('/mnt/mmc/a.flac')=='A' and t.seen==[b'/mnt/mmc/a.flac\n']  # named before stock reads it
assert t.files['/tmp/q2-scan-file']==b'/mnt/mmc/a.flac\n' and t.files['/tmp/q2-scan.log']==b'a past demo\n'
for i in range(2,500): t.album_artist(f'/mnt/mmc/{i}.opus')
assert t.files['/tmp/q2-scan-file']==b'/mnt/mmc/499.opus\n' and t.files['/tmp/q2-scan.log']==b'a past demo\n'
t.album_artist('/mnt/mmc/500.opus')
assert t.files['/tmp/q2-scan.log']==b'500 91000 /mnt/mmc/500.opus\n'  # this demo's first line starts the file
t.STATM=b'30000 23000 1200 1 0 25000 0\n'
for i in range(501,1001): t.album_artist(f'/mnt/mmc/{i}.opus')
assert t.files['/tmp/q2-scan.log']==b'500 91000 /mnt/mmc/500.opus\n1000 92000 /mnt/mmc/1000.opus\n'
long='/mnt/mmc/'+'x'*700  # a line holds 500 bytes of path at most
t.word(payload_syms['scan_counts'],1499); t.album_artist(long)
assert t.files['/tmp/q2-scan.log'].split(b'\n')[2]==b'1500 92000 '+long[:500].encode()
t.word(payload_syms['scan_counts']+4,65536); before=bytes(t.files['/tmp/q2-scan.log'])
t.word(payload_syms['scan_counts'],1999); t.album_artist('/mnt/mmc/2000.opus')
assert t.files['/tmp/q2-scan.log']==before and t.files['/tmp/q2-scan-file']==b'/mnt/mmc/2000.opus\n'  # capped
assert not t.fds and not t.handles; passed()
# Coverflow's shared folders (coverflow.c folders_shared, one sorted pass): exactly the old rule, an album
# shares when another album key has a track in its folder, the folder compared whole, so an FNV collision
# never shares.
def fnv1a(b,h=2166136261):
    for c in b: h=((h^c)*16777619)&0xffffffff
    return h
assert fnv1a(b'/m/1062789')==fnv1a(b'/m/1279192')  # one folder hash, two folders
m=CoverflowMachine()
fixture=[('/m/Mix/a.mp3','A','X'),('/m/Mix/b.mp3','B','Y'),  # two albums in one folder
         ('/m/Purple/CD1/1.flac','Baroness','Purple'),('/m/Purple/CD1/2.flac','Baroness','Purple'),  # one album, one folder
         ('/m/Purple/Disc 2/1.flac','Baroness','Purple'),('/m/Other Purple/1.flac','STP','Purple'),  # disc folders; same name elsewhere
         (None,'C','Z'),('loose.mp3','D','Z'),('/top.mp3','E','W'),('/top2.mp3','E','W'),  # no path, no folder, the root's
         ('/m/Three/1.mp3','F','V'),('/m/Three/2.mp3','F','V'),('/m/Three/3.mp3','G','V'),  # two keys among three
         ('/m/1062789/a.mp3','H','U'),('/m/1279192/b.mp3','I','U')]  # colliding folders
recs=[]
for path,artist,album in fixture:
    r=m.song('t'); m.word(r+O['REC_PATH'],m.string(path) if path else 0)
    m.word(r+O['REC_ARTIST'],m.string(artist)); m.word(r+O['REC_ALBUM'],m.string(album)); recs.append(r)
def folder(p): return p[:p.rfind('/')] if p and '/' in p else None
old=[int(folder(p) is not None and any(folder(q)==folder(p) and (a2,b2)!=(a,b) for q,a2,b2 in fixture)) for p,a,b in fixture]
assert old==[1,1,0,0,0,0,0,0,0,0,1,1,1,0,0]
marks=m.call(address=payload_syms['folders_shared'],args=(m.deque(recs),len(recs),0,0),gap=0)
assert list(m.u.mem_read(marks,len(recs)))==old
assert m.call(address=payload_syms['folders_shared'],args=(m.deque([]),0,0,0),gap=0); passed()  # no albums: no marks, not a failure
# Boot reads it too (ringnav_bootmode), so Audio settings shows it before any Library page has opened.
for config,text in (({},'Artists: Album Artist'),({'ARTISTTYPE':'0'},'Artists: Artist')):
    shown=settings_page('audioset','scroll_view_playset',config,stock_rows=13,boot=True,sub=1)[2]
    assert shown()[2]==text,config  # Library's third row (library_settings.c)
passed()
# Mono Audio (crossfade.c mono_click): saved as Q2POD MONO, the row's text following, and the int
# hciplayer and q2video map (MONO_FILE) set live. No file until it is first turned on.
class MonoMachine(QueueMachine):
    def __init__(self):
        super().__init__(); self.flag=self.alloc(4); self.word(syms['g_equalizer_flag'],1)  # the filter is in
        for n in ('open','write','rename','mmap','close'): self.handlers[syms[n+'@GLIBC_2.0']]='mono:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('mono:'): return super().hook(u,address,size,unused)
        self.calls.append((name,)); self.returns(self.flag if name=='mono:mmap' else 3 if name=='mono:open' else 4 if name=='mono:write' else 0)
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13,cls=MonoMachine)
assert not [c for c in m.calls if c[0]=='mono:open']
assert click(2)==[(1,'Q2POD','MONO')] and texts()[2]=='Mono Audio' and m.get(m.flag)==1  # after Smart Crossfade and Resume Songs
assert click(2)==[(0,'Q2POD','MONO')] and texts()[2]=='Mono Audio' and m.get(m.flag)==0 and switch_image(m,rows[2])=='switch_off'
assert [c[0] for c in m.calls if c[0]=='mono:open']==[]; passed()  # mapped once
# A failed map must never publish an inode that consumers could retain forever.
class FailedMapMachine(MonoMachine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='mono:mmap':
            self.calls.append(('mono:mmap',)); self.returns(0xffffffff); return
        return super().hook(u,address,size,unused)
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13,cls=FailedMapMachine)
assert click(2)==[(1,'Q2POD','MONO')]
assert 'mono:mmap' in [c[0] for c in m.calls] and 'mono:rename' not in [c[0] for c in m.calls]
passed()
# Mono Audio with the EQ off and the player stopped (mono_sync): the filter is not attached until something
# plays (peq_chain), then is attached; once the filter is in (peq_chained) a later sync does not attach it again.
class MonoStoppedMachine(MonoMachine):
    def __init__(self):
        super().__init__(); self.word(syms['g_equalizer_flag'],0); self.handlers.pop(syms['mclGetPlayStatus'],None)
        self.word(PLAY_STATUS,1); self.handlers[payload_syms['peq_attach']]='mono:peq_attach'
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13,cls=MonoStoppedMachine)
assert click(2)==[(1,'Q2POD','MONO')] and not [c for c in m.calls if c[0]=='mono:peq_attach']
m.word(PLAY_STATUS,2)
m.call(address=payload_syms['mono_sync'],args=(0,0,0,0),gap=0)
assert [c[0] for c in m.calls].count('mono:peq_attach')==1
m.word(payload_syms['peq_chained'],1)  # what the attach's peq_stock_eq leaves, as any EQ switch this boot
m.call(address=payload_syms['mono_sync'],args=(0,0,0,0),gap=0)
assert 'mono:peq_attach' not in [c[0] for c in m.calls]; passed()
# Playback Setting restacks by use (navigation.c playset_order): stock's rows by id, then ours.
def stacked(m,view_name): return [(m.entries+rows).index(c) for c in m.nodes[named(m,m.top,view_name)]['children'] if c in m.entries+rows]
assert stacked(m,'scroll_view_playset')==[2,23,3,7,13,6,14,8,17,18,19,20,21,0,9,22,10,15,16,1,4,5]; passed()  # EQ, Library first
assert all(r in m.entries+rows for r in m.nodes[m.settings_view]['children'])  # no headings
for i in (1,4,5,10):  # Resume Songs, Sleep Timer, Volume and Library open pages; the switches and sliders don't
    button=m.nodes[rows[i]]['children'][0]
    assert 'list_into' in [m.nodes[c].get('image') for c in m.nodes[button]['children']]  # a value row's _setting_value follows it
passed()
# Swap L/R uses its own persistent flag, immediately below Mono Audio.
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13,cls=MonoMachine)
assert texts()[3]=='Swap L/R'
assert click(3)==[(1,'Q2POD','SWAPLR')] and texts()[3]=='Swap L/R' and m.get(m.flag)==1
assert click(3)==[(0,'Q2POD','SWAPLR')] and texts()[3]=='Swap L/R' and m.get(m.flag)==0
m,rows,texts,*_=settings_page('audioset','scroll_view_playset',{'SWAPLR':'1'},stock_rows=13)
assert texts()[3]=='Swap L/R'; passed()
# Folder Root (#49): the row cycles the card's top-level folders, case aside, then the card, saving Q2POD
# FOLDERROOT each time. Not offered: files, dot-folders, names with an edge space or over 200 bytes (a
# config.ini line can't keep them). A saved folder gone moves on to the next name after it.
card=[('Zeta',4),('.Trash',4),('Música ñ',4),('alpha',4),('notes.txt',8),(' Edge',4),('B'*200,4),('C'*201,4),('Unknown',0)]
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',{'FOLDERROOT':'Deleted'},stock_rows=13,sub=4,cls=lambda:RootMachine(card=card))
assert texts()[0]=='Folder Root: Deleted'
saved=[]
for _ in range(6):
    assert click(0)==[] and [(m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='toolsWriteConfig']==[('Q2POD','FOLDERROOT')]
    saved.append((m.config['FOLDERROOT'],texts()[0]))
assert [v for v,_ in saved]==['Música ñ','Unknown','Zeta','','alpha','B'*200]
assert [t for _,t in saved]==['Folder Root: '+(v or 'Card') for v,_ in saved]; passed()
# Smart Crossfade (crossfade.c xfade_row): one switch row, Q2POD XFADE; a config saved with the length picker before
# it reads On whenever Crossfade was on, at any length (XFADESEC is not read).
for config,on in (({'XFADE':'1','XFADESEC':'7'},1),({'XFADE':'1','XFADESEC':'16'},1),({'XFADE':'0','XFADESEC':'7'},0),({'XFADESEC':'7'},0),({},0)):
    m,rows,texts,*_=settings_page('audioset','scroll_view_playset',config,stock_rows=13)
    assert texts()[:2]==['Smart Crossfade','Resume Songs: 30 min'] and switch_image(m,rows[0])==('switch_on' if on else 'switch_off'),config
passed()
# Resume Songs (crossfade.c resume_min): the row shows Q2POD RESUMEMIN, 30 min unless 0 or a five in 20-120.
for config,text in (({},'30 min'),({'RESUMEMIN':'0'},'Off'),({'RESUMEMIN':'120'},'120 min'),({'RESUMEMIN':'125'},'30 min'),
                    ({'RESUMEMIN':'22'},'30 min'),({'RESUMEMIN':'15'},'30 min'),({'RESUMEMIN':'x'},'30 min')):
    m,rows,texts,*_=settings_page('audioset','scroll_view_playset',config,stock_rows=13)
    assert texts()[1]=='Resume Songs: '+text and m.nodes[m.nodes[m.nodes[rows[1]]['children'][0]]['children'][1]].get('name')=='label_resumemin',config
passed()
# Wheel sensitivity (playback.c wheel_pct, test/playback.py's travel): Display → Controls' last row in both
# builds, Q2POD WHEELSENSITIVITY as 1.0.6 saved it, 50% unless a canonical ten in 10-200.
for config,pct in (({},50),({'WHEELSENSITIVITY':'130'},130),({'WHEELSENSITIVITY':'200'},200),({'WHEELSENSITIVITY':'40'},40),({'WHEELSENSITIVITY':'0'},50),
                   ({'WHEELSENSITIVITY':'135'},50),({'WHEELSENSITIVITY':'210'},50),({'WHEELSENSITIVITY':'x'},50)):
    m,rows,texts,icons,click=settings_page('display','scroll_view_display',config,stock_rows=3,sub=3)
    label=m.nodes[m.nodes[rows[0]]['children'][0]]['children'][1]
    assert texts()[0]==f'Wheel Sensitivity: {pct}%' and icons[0]=='system_display' and m.nodes[label]['name']=='label_wheel'
    assert m.nodes[named(m,m.top,'scroll_view_display')]['children'][0]==rows[0],config  # the list's start
passed()
# Hold Return (Q2POD HOLDRETURN; compact_now_playing): Controls' row after Wheel sensitivity,
# Recent unless set; Centre or tap toggles and saves it.
for config,text in (({},'Recent'),({'HOLDRETURN':'1'},'Home')):
    m,rows,texts,icons,click=settings_page('display','scroll_view_display',config,stock_rows=3,sub=3)
    assert texts()[1]=='Hold Return: '+text and icons[1]=='playset_folderjump'
    assert m.nodes[named(m,m.top,'scroll_view_display')]['children'][1]==rows[1],config
assert click(1)==[(0,'Q2POD','HOLDRETURN')] and texts()[1]=='Hold Return: Recent'
assert click(1)==[(1,'Q2POD','HOLDRETURN')] and texts()[1]=='Hold Return: Home'
passed()
# Wheel QS is gone: a saved Q2POD WHEELQS=1 is a key nobody reads, no row and nothing written.
m,rows,texts,icons,click=settings_page('display','scroll_view_display',{'WHEELQS':'1'},stock_rows=3,sub=3)
assert 'Wheel QS' not in texts() and texts()[:2]==['Wheel Sensitivity: 50%','Hold Return: Recent'] and click(1)==[(1,'Q2POD','HOLDRETURN')]; passed()
# Its row opens stock's Boot volume page re-dressed (crossfade.c): titled, no switch, the percent
# beside the slider, which runs 50-200 in tens. The wheel and +/- step 10%, live and saved, the
# slider snaps to a ten, a drag is live but saved once at its release, and Centre goes back (edit_key, as on Brightness).
m,rows,texts,icons,click=settings_page('display','scroll_view_display',stock_rows=3,sub=3)
display_page=m.top; m.stack=[m.top]; m.nodes[m.wm]=dict(type='window_manager',name='',children=m.stack,visible=1)
assert click(0)==[] and m.top!=display_page and m.nodes[m.top]['name']=='bootvol_page'
assert m.nodes[m.title]['text']=='Wheel Sensitivity' and m.nodes[m.switch]['visible']==0 and m.nodes[m.vol]['text']=='50%'
assert m.nodes[named(m,m.top,'view_bootvol')]['visible']==1 and m.nodes[named(m,m.top,'view_bootvol')]['enable']==1  # touch (#140)
def press(w,kind=O['EVT_CLICK']):
    m.calls=[]; f,ctx=m.handler(w,kind); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
    return [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']
def turn(key=O['KEY_NEXT']):
    assert m.call(key)==11; return [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']
W='Q2POD','WHEELSENSITIVITY'
assert turn()==[(60,*W)] and m.nodes[m.vol]['text']=='60%' and texts()[0]=='Wheel Sensitivity: 60%'
assert turn(O['KEY_PREV'])==[(50,*W)] and m.nodes[m.vol]['text']=='50%' and turn(O['KEY_PREV'])==[(40,*W)] and turn()==[(50,*W)]  # 50% is the default, not the bottom
assert press(m.add)==[(60,*W)] and m.nodes[m.vol]['text']=='60%' and texts()[0]=='Wheel Sensitivity: 60%'
assert press(m.dec)==[(50,*W)] and texts()[0]=='Wheel Sensitivity: 50%'
m.nodes[m.slider]['value']=3; assert press(m.slider,O['EVT_VALUE_CHANGED'])==[(10,*W)] and m.nodes[m.vol]['text']=='10%' and press(m.dec)==[] and turn(O['KEY_PREV'])==[]  # 10% is the bottom
m.nodes[m.slider]['value']=127; assert press(m.slider,O['EVT_VALUE_CHANGED'])==[(130,*W)] and m.nodes[m.vol]['text']=='130%'
m.nodes[m.slider]['value']=300; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and m.nodes[m.vol]['text']=='200%'
assert press(m.slider,O['EVT_VALUE_CHANGED'])==[(200,*W)] and press(m.add)==[] and turn()==[]
# A drag over several values saves once, at the release; one whose release sends nothing (stock: a
# drag back to its start, an aborted press) is saved by the next step, which saves as ever.
m.nodes[m.slider]['value']=127; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and m.nodes[m.vol]['text']=='130%'
m.nodes[m.slider]['value']=163; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and press(m.slider,O['EVT_VALUE_CHANGED'])==[(160,*W)]
m.nodes[m.slider]['value']=143; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and m.nodes[m.vol]['text']=='140%'
assert press(m.dec)==[(130,*W)] and turn()==[(140,*W)]
m.echoes=0; m.nodes[m.slider]['value']=157; assert press(m.slider,O['EVT_VALUE_CHANGED'])==[(160,*W)] and m.echoes==1  # its snap to 160 echoes, unsaved
m.nodes[m.slider]['value']=300; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and press(m.add)==[(200,*W)]
assert texts()[0]=='Wheel Sensitivity: 200%' and m.nodes[m.switch]['visible']==0
assert m.confirm()==11 and [c[0] for c in m.calls].count('navigator_back')==1; passed()
# Resume Songs' row opens the same page: no switch, Off below 20, then 20-120 min in fives, live and
# saved (a drag at its release); Centre goes back.
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13)
m.stack=[m.top]; m.nodes[m.wm]=dict(type='window_manager',name='',children=m.stack,visible=1)
assert click(1)==[] and m.nodes[m.top]['name']=='bootvol_page' and m.nodes[m.title]['text']=='Resume Songs'
assert m.nodes[m.switch]['visible']==0 and m.nodes[m.vol]['text']=='30 min'
R='Q2POD','RESUMEMIN'
assert turn()==[(35,*R)] and m.nodes[m.vol]['text']=='35 min' and texts()[1]=='Resume Songs: 35 min'
assert press(m.dec)==[(30,*R)] and turn(O['KEY_PREV'])==[(25,*R)] and turn(O['KEY_PREV'])==[(20,*R)]
assert turn(O['KEY_PREV'])==[(0,*R)] and m.nodes[m.vol]['text']=='Off' and texts()[1]=='Resume Songs: Off'
assert turn(O['KEY_PREV'])==[] and press(m.add)==[(20,*R)]  # Off is the bottom; up from it is 20
m.nodes[m.slider]['value']=63; assert press(m.slider,O['EVT_VALUE_CHANGED'])==[(65,*R)] and m.nodes[m.vol]['text']=='65 min'
m.nodes[m.slider]['value']=300; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and m.nodes[m.vol]['text']=='120 min'
assert press(m.slider,O['EVT_VALUE_CHANGED'])==[(120,*R)] and turn()==[]
m.nodes[m.slider]['value']=15; assert press(m.slider,O['EVT_VALUE_CHANGED'])==[(0,*R)] and texts()[1]=='Resume Songs: Off'
assert m.confirm()==11 and [c[0] for c in m.calls].count('navigator_back')==1
# A page closed mid-drag saves the dragged value once, as it closes.
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13)
m.stack=[m.top]; m.nodes[m.wm]=dict(type='window_manager',name='',children=m.stack,visible=1)
assert click(1)==[]
m.nodes[m.slider]['value']=53; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and texts()[1]=='Resume Songs: 55 min'
assert press(m.top,O['EVT_DESTROY'])==[(55,*R)] and texts()[1]=='Resume Songs: 55 min'; passed()
# Playback's Volume row opens it on the volume (crossfade.c xfade_open 4): no switch, 0 to Max Volume, heard and saved
# at once (a drag at its release), the wheel vol_next's steps (1 a slow tick); Centre goes back.
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13)
m.mock('device_set_volume'); m.byte(syms['g_volume'],30); m.byte(syms['g_maxvolume'],60)
m.stack=[m.top]; m.nodes[m.wm]=dict(type='window_manager',name='',children=m.stack,visible=1)
assert click(5)==[] and m.nodes[m.top]['name']=='bootvol_page' and m.nodes[m.title]['text']=='Volume'
assert m.nodes[m.switch]['visible']==0 and m.nodes[m.vol]['text']=='30'
V='PLAYSET','VOLUME'
assert turn()==[(31,*V)] and m.nodes[m.vol]['text']=='31' and texts()[5]=='Volume: 31' and press(m.dec)==[(30,*V)]
m.nodes[m.slider]['value']=90; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and m.nodes[m.vol]['text']=='60'
assert press(m.slider,O['EVT_VALUE_CHANGED'])==[(60,*V)] and turn()==[] and texts()[5]=='Volume: 60'
assert m.confirm()==11 and [c[0] for c in m.calls].count('navigator_back')==1; passed()
# Smart Crossfade's row is one switch (crossfade.c xfade_click): no page. On with stock's Gapless off turns Gapless on
# as stock's own row does (PLAYSET GAPLESS and stock's mclSetGaplessMode, the byte it sets); off leaves Gapless be.
m,rows,texts,icons,click=settings_page('audioset','scroll_view_playset',stock_rows=13)
m.stack=[m.top]; m.nodes[m.wm]=dict(type='window_manager',name='',children=m.stack,visible=1)
m.byte(O['MCL_GAPLESS'],0)
gapless=lambda: [c[1] for c in m.calls if c[0]=='mclSetGaplessMode']
assert click(0)==[(1,'Q2POD','XFADE'),(1,'PLAYSET','GAPLESS')] and gapless()==[1]
m.byte(O['MCL_GAPLESS'],1)  # what stock's setter stores (0x5bda90)
assert m.nodes[m.top]['name']!='bootvol_page' and texts()[0]=='Smart Crossfade' and switch_image(m,rows[0])=='switch_on'
assert click(0)==[(0,'Q2POD','XFADE')] and not gapless() and switch_image(m,rows[0])=='switch_off'
assert click(0)==[(1,'Q2POD','XFADE')] and not gapless(); passed()  # Gapless already on: left alone
# Night Shift (nightshift.c): the main Display page's last row, after Appearance,
# Coverflow and Controls; Q2POD NIGHTSHIFT and NIGHTK, 3400K unless a hundred in 2000-5000.
for config,text in (({},'Off'),({'NIGHTSHIFT':'1'},'3400 K'),({'NIGHTSHIFT':'1','NIGHTK':'2700'},'2700 K'),({'NIGHTSHIFT':'1','NIGHTK':'5000'},'5000 K'),
                    ({'NIGHTSHIFT':'1','NIGHTK':'2750'},'3400 K'),({'NIGHTSHIFT':'1','NIGHTK':'6500'},'3400 K'),({'NIGHTK':'2000'},'Off')):
    m,rows,texts,icons,click=settings_page('display','scroll_view_display',config,stock_rows=3)
    label=m.nodes[m.nodes[rows[2]]['children'][0]]['children'][1]
    assert texts()[2]=='Night Shift: '+text and icons[2]=='display_backlight' and m.nodes[label]['name']=='label_night',config
    assert m.nodes[named(m,m.top,'scroll_view_display')]['children'][4]==rows[2],config  # after Brightness (first), stock's hidden row and Smart Awake
passed()
# Its row opens the same page with its switch, Off, its value the warmth in every mode (3400 K). The switch turns it on and off and saves it; the
# wheel, +/- and the slider step 100K, warmer to the right, a move turning it on; each change repaints
# the whole screen at once, a drag's every step too, but a drag saves once, at its release.
m,rows,texts,icons,click=settings_page('display','scroll_view_display',stock_rows=3)
m.stack=[m.top]; m.nodes[m.wm]=dict(type='window_manager',name='',children=m.stack,visible=1)
assert click(2)==[] and m.nodes[m.top]['name']=='bootvol_page' and m.nodes[m.title]['text']=='Night Shift'
assert m.nodes[m.switch]['visible']!=0 and m.nodes[m.vol]['text']=='3400 K'
N,K=('Q2POD','NIGHTSHIFT'),('Q2POD','NIGHTK')
def repaints(): return [c[1:3] for c in m.calls if c[0]=='widget_invalidate_force']==[(m.wm,0)]
assert press(m.switch)==[(1,*N)] and repaints() and m.nodes[m.vol]['text']=='3400 K' and texts()[2]=='Night Shift: 3400 K'
assert m.nodes[m.switch]['image']=='switch_on'
assert turn()==[(3300,*K)] and repaints() and m.nodes[m.vol]['text']=='3300 K'
assert turn(O['KEY_PREV'])==[(3400,*K)] and press(m.add)==[(3300,*K)] and press(m.dec)==[(3400,*K)]
m.nodes[m.slider]['value']=30; assert press(m.slider,O['EVT_VALUE_CHANGED'])==[(2000,*K)] and turn()==[] and press(m.add)==[]
m.nodes[m.slider]['value']=10; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and repaints() and texts()[2]=='Night Shift: 4000 K'
m.nodes[m.slider]['value']=0; assert press(m.slider,O['EVT_VALUE_CHANGING'])==[] and repaints() and texts()[2]=='Night Shift: 5000 K'
assert press(m.slider,O['EVT_VALUE_CHANGED'])==[(5000,*K)] and turn(O['KEY_PREV'])==[]  # the release saves, once
# The switch cycles Off, On and Auto; a move in Auto keeps Auto.
assert texts()[2]=='Night Shift: 5000 K' and press(m.switch)==[(2,*N)] and repaints() and m.nodes[m.vol]['text']=='5000 K'
assert texts()[2]=='Night Shift: Auto' and m.nodes[m.switch]['image']=='switch_on'
assert turn()==[(4900,*K)] and texts()[2]=='Night Shift: Auto' and turn(O['KEY_PREV'])==[(5000,*K)]
assert press(m.switch)==[(0,*N)] and repaints() and m.nodes[m.vol]['text']=='5000 K'
assert texts()[2]=='Night Shift: Off' and m.nodes[m.switch]['image']=='switch_off'
assert turn(O['KEY_PREV'])==[(1,*N)] and texts()[2]=='Night Shift: 5000 K'  # at an end too
assert press(m.switch)==[(2,*N)] and press(m.switch)==[(0,*N)]
assert turn()==[(1,*N),(4900,*K)] and texts()[2]=='Night Shift: 4900 K'; passed()  # a move turns it on
# The tint (nightshift.c night_end, stock lcd_end_frame calling it): on, as boot read it, the first
# normal frame's paint gives the screen's lcd night_end, reading no setting. Each frame warms the
# pixels drawn in its rect (alpha not 0xf7): red kept, green and blue scaled, alpha 0xf7; one
# already warmed, and any outside the rect, stay. Rows not w pixels long, a layout stock's lcd never
# has, are left as drawn. Off gives stock's end_frame back.
def tint_machine(config,w=8,h=4,row=None,mode=0,fmt=3,clock=None):  # clock: the mock's at boot
    m=QueueMachine(); m.config.update(config); m.handlers[payload_syms['peq_boot']]='stock_peq_boot'
    if clock: m.clock=clock
    m.call(address=payload_syms['ringnav_bootmode'],args=(3,0,0,0),gap=0)  # config_init's: boot reads the setting
    fb=m.alloc(4*w*h+64); end=syms['widget_invalidate_force']  # stock's end_frame, as a recorded call
    for off,v in ((0xb0,w),(0xb4,h),(0x124,4*(row or w)),(0x128,fmt),(0xcc,mode),(0x110,fb),(0x7c,end),
                  (0xf8,2),(0xfc,1),(0x100,3),(0x104,2)):
        m.word(m.lcd+off,v)
    for i in range(w*h): m.word(fb+4*i,0xff000000|i*0x070503)
    m.word(fb+4*(1*w+3),0xf7123456)  # in the rect, warmed already
    return m,fb,end
def tint_frame(m):
    m.calls=[]; m.call(address=syms['lcd_end_frame'],args=(m.lcd,0,0,0),gap=0)
    return [c[1] for c in m.calls if c[0]=='widget_invalidate_force']  # stock's end_frame ran, with the lcd
def warmed(c,k=3400):
    i=(k-2000)//100; g,b=[137,142,146,151,155,159,163,167,170,174,177,180,184,187,190,193,195,198,201,203,206,208,211,213,215,218,220,222,224,226,228][i],[14,27,39,50,61,70,79,87,95,103,110,117,123,129,135,141,146,151,157,161,166,171,175,179,183,187,191,195,199,202,206][i]
    return 0xf7000000|c&0xff0000|((c>>8&255)*g+127)//255<<8|((c&255)*b+127)//255
def pixels(m,fb,n): return [m.get(fb+4*i) for i in range(n)]
m,fb,end=tint_machine({'NIGHTSHIFT':'1'}); before=pixels(m,fb,32)
assert ('Q2POD','NIGHTSHIFT') in [r[1:3] for r in m.config_reads]; reads=len(m.config_reads)
m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0)
assert m.get(m.lcd+0x7c)==payload_syms['night_end'] and len(m.config_reads)==reads and tint_frame(m)==[m.lcd]
inside={y*8+x for y in (1,2) for x in (2,3,4)}-{1*8+3}
assert pixels(m,fb,32)==[warmed(c) if i in inside else c for i,c in enumerate(before)]
once=pixels(m,fb,32); assert tint_frame(m)==[m.lcd] and pixels(m,fb,32)==once  # nothing warmed twice
m.word(fb+4*(2*8+2),0xff808080); assert tint_frame(m)==[m.lcd] and m.get(fb+4*(2*8+2))==warmed(0xff808080); passed()  # drawn again
# Stock's own image pixel (0x6c8520 from an RGBA image, 0x6c5140 a BGRA one: dst, src, alpha, premultiplied) drawn
# over a warmed row, a source pixel of each alpha: every one stock touches comes out warmed, once. It copies a
# source above 0xf8 alpha and all, so the mark is below that: at 0xfe the icons' rims (alpha 0xfe) stayed bright.
for pixel in (0x6c8520,0x6c5140):
    m,fb,end=tint_machine({'NIGHTSHIFT':'1'},w=256,h=1)
    for off,v in ((0xf8,0),(0xfc,0),(0x100,256),(0x104,1)): m.word(m.lcd+off,v)
    m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0); tint_frame(m); under=pixels(m,fb,256)
    src=m.alloc(4)
    for a in range(256): m.word(src,a<<24|0x30a0ff); m.call(address=pixel,args=(fb+4*a,src,0xff,0),gap=0)
    drawn=pixels(m,fb,256); assert drawn[0]==under[0] and all(d!=u for d,u in zip(drawn[1:],under[1:]))
    tint_frame(m); assert pixels(m,fb,256)==drawn[:1]+[warmed(c) for c in drawn[1:]],pixel
    once=pixels(m,fb,256); tint_frame(m); assert pixels(m,fb,256)==once
passed()
m.calls=[]; m.call(address=payload_syms['night_set'],args=(0,3400,0,0),gap=0)
assert m.get(m.lcd+0x7c)==end and [c[1:3] for c in m.calls if c[0]=='widget_invalidate_force']==[(m.wm,0)]
m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0)
assert m.get(m.lcd+0x7c)==end; passed()  # off: the next paint leaves it
for row in (4,9):  # rows of h (rotated), padded rows
    m,fb,end=tint_machine({'NIGHTSHIFT':'1'},row=row); before=pixels(m,fb,32)
    m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0)
    assert tint_frame(m)==[m.lcd] and pixels(m,fb,32)==before,row
passed()
for config,kw in (({},{}),({'NIGHTSHIFT':'1'},{'mode':4}),({'NIGHTSHIFT':'1'},{'fmt':5})):  # off; an offline canvas; not BGRA
    m,fb,end=tint_machine(config,**kw); m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0)
    assert m.get(m.lcd+0x7c)==end,(config,kw)
passed()
# Auto (NIGHTSHIFT 2) warms the screen in Light Mode's dark hours, 19:00 to 7:00. Booted by day (the mock's
# clock is 18:14) a paint leaves the lcd alone; the crossing (clock_show's night_clock) repaints the screen and
# the next paint takes it, warmed as On's; the same hour again changes nothing, and day gives stock's
# end_frame back. Booted in the dark hours it starts warm; off while the clock can't be read.
m,fb,end=tint_machine({'NIGHTSHIFT':'2'}); before=pixels(m,fb,32)
m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0); assert m.get(m.lcd+0x7c)==end
m.calls=[]; m.call(address=payload_syms['night_clock'],args=(1,0,0,0),gap=0)
assert [c[1:3] for c in m.calls if c[0]=='widget_invalidate_force']==[(m.wm,0)]
m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0)
assert m.get(m.lcd+0x7c)==payload_syms['night_end'] and tint_frame(m)==[m.lcd]
assert pixels(m,fb,32)==[warmed(c) if i in inside else c for i,c in enumerate(before)]
m.calls=[]; m.call(address=payload_syms['night_clock'],args=(1,0,0,0),gap=0)
assert m.get(m.lcd+0x7c)==payload_syms['night_end'] and not m.calls
m.call(address=payload_syms['night_clock'],args=(0,0,0,0),gap=0); assert m.get(m.lcd+0x7c)==end; passed()
for clock,hooked in (((22,0),True),((6,59),True),((7,0),False),('localtime',False)):
    m,fb,end=tint_machine({'NIGHTSHIFT':'2'},clock=clock); m.call(address=payload_syms['night_paint'],args=(m.canvas,0,0,0),gap=0)
    assert (m.get(m.lcd+0x7c)==payload_syms['night_end'])==hooked,clock
passed()
# Bluetooth's list (btmenu_rows, rebuilt on every search) gains Resume on connect and Link Volume with Bluetooth
# on, last in bt_ui.c's Audio group (docs/internals.md#bluetooth-page), as stock's rows: no icon, 78 high. Both off unless Q2POD RESUME / BTVOLLINK is 1 (docs/internals.md#resume-on-connect,
# #headset-controls-and-volume); off, the Q2's volume is its own gain and the device keeps its own volume.
# Stock's second row, its Bluetooth Name, is bt_name.c's Name row in its place, the name its value, as high as stock's
# row (docs/internals.md#bluetooth-name).
m,rows,texts,icons,click=settings_page('btrows','scroll_view_bluetooth',stock_rows=7)  # 5 rows, 2 devices
assert len(rows)==4 and icons==[None]*4 and texts(kv=False)==['Resume on Connect','Link Volume','Name','Guest Play']  # Guest Play's row (guest_ui.c); Name's value the device's name
view=named(m,m.top,'scroll_view_bluetooth'); name_row=rows[2]
assert m.nodes[view]['children'].index(name_row)==1 and m.get(name_row+O['W_H'])==m.get(m.entries[1]+O['W_H']) and m.entries[1] not in m.nodes[view]['children']
assert 'Q2 Muse' in [m.nodes[c].get('text') for c in m.nodes[m.nodes[name_row]['children'][0]]['children']]
rows=rows[:2]
for k,row in enumerate(rows):
    kids=m.nodes[m.nodes[row]['children'][0]]['children']  # the label and its switch
    assert len(kids)==2 and m.get(kids[0]+O['W_X'])==10 and m.get(row+O['W_H'])==78 and switch_image(m,row)=='switch_off'
    kids=m.nodes[named(m,m.top,'scroll_view_bluetooth')]['children']
    assert kids.index(row)==len(kids)-2+k
assert click(0)==[(1,'Q2POD','RESUME')] and switch_image(m,rows[0])=='switch_on'
assert click(0)==[(0,'Q2POD','RESUME')] and switch_image(m,rows[0])=='switch_off'
assert click(1)==[(1,'Q2POD','BTVOLLINK')] and switch_image(m,rows[1])=='switch_on'
assert click(1)==[(0,'Q2POD','BTVOLLINK')] and switch_image(m,rows[1])=='switch_off'
assert texts(kv=False)==['Resume on Connect','Link Volume','Name','Guest Play']; passed()
m,rows,texts,*_=settings_page('btrows','scroll_view_bluetooth',{'RESUME':'1','BTVOLLINK':'1'},stock_rows=7)
assert [switch_image(m,r) for r in rows[:2]]==['switch_on','switch_on']; passed()
m,rows,texts,*_=settings_page('btrows','scroll_view_bluetooth',stock_rows=1,bt=0)  # off: the switch, Name and a line (bt_ui.c)
assert texts(kv=False)==['Name']; passed()

def oled_set(m):  # the first system_bar load, as on a unit (accent, theme_boot); the calls it made cleared
    m.record_syscalls(); m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'],16)]='stock_ui_load'
    m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0],args=(m.string('system_bar'),0,0,0),gap=0); m.calls=[]
    return m
# The sleep timer has no list: the row sits over its fields, set_row (32) high, its title at text_x in the row's
# font (theme_font ROLE_TITLE: Inter 24). With the switch off only the row shows, and Centre is its click; on,
# Centre walks the fields as stock's page has it.
def sleep_page():
    m,sels,ok,slide=time_page('sleepshutdown_page'); oled_set(m); sw=m.node('image','img_switch',image='switch_off'); fields_=m.node('view','view_sleep',[slide,ok],visible=0)
    m.top=m.node('window','sleepshutdown_page',[m.node('view','view_navbar',[sw],visible=0),fields_]); m.word(m.top+O['W_PARENT'],m.wm); m.nodes[m.wm]={'children':[m.top]}
    m.handlers[int(manifest['patch_symbols']['stock_sleeptimer_trampoline'],16)]='stock_sleeptimer'
    m.mock('button_create','hscroll_label_create','widget_use_style','set_hscroll_label_attribute','widget_set_name','widget_on')
    assert m.call(address=HOOKS['systemset_sleepshutdown_page_init'][0],args=(m.top,0,0,0),gap=0)==0 and m.calls[0][0]=='stock_sleeptimer'
    return m,sw,fields_,ok
m,sw,fields_,ok=sleep_page(); button=m.nodes[m.top]['children'][-1]; label,switch=m.nodes[button]['children'][:2]
assert geometry(m,button)==(0,0,375,32) and geometry(m,label)[3]==32 and geometry(m,switch)[3]==32 and m.nodes[label]['text']=='Sleep Timer' and m.nodes[switch]['image']=='switch_off'
assert m.nodes[label]['style:normal:font_name']=='inter' and m.nodes[label]['style:normal:font_size']==24 and geometry(m,label)[0]==24
m.nodes[sw]['image']='switch_on'; assert m.confirm()==11 and [c[1] for c in m.dispatched()]==[sw] and m.nodes[switch]['image']=='switch_on' and not m.nodes[m.top].get('_edit_field')
m.nodes[fields_]['visible']=1; assert m.confirm()==11 and m.nodes[m.top]['_edit_field']==1; passed()
# With the switch on it stays its two fields, OK stop and outline.
m,sw,fields_,ok=sleep_page(); m.nodes[fields_]['visible']=1
assert m.confirm()==11 and m.nodes[m.top]['_edit_field']==1 and m.confirm()==11 and m.nodes[m.top]['_edit_field']==2 and not m.clicks and m.confirm()==11 and m.clicks==[ok]; passed()
# A widget's box (put), a fresh paint record (blank) and a widget's box on the screen (screen_box).
def put(m,w,*g):
    for k,v in zip(('W_X','W_Y','W_W','W_H'),g): m.word(w+O[k],v)
def blank(m): m.bands=[]; m.letters=[]; m.rounded=[]; m.image_draws=[]; m.calls=[]
def screen_box(m,w):
    x,y,ww,h=wbox(m,w)
    for p in iter(lambda p=[w]: p.__setitem__(0,m.get(p[0]+O['W_PARENT'])) or p[0],0):
        if p==m.top or p==m.wm: break
        x,y=x+signed(m.get(p+O['W_X'])),y+signed(m.get(p+O['W_Y']))
    return x,y,ww,h
# A ring at any radius has antialiased corners (corner_cover.c), its sides mirrored; at radius 0, square.
def paint_canvas():  # a machine, Light on, to paint on
    SETUP[:]=[lambda k:(k.config.update(LIGHT='1'),k)]
    try: return QueueMachine()
    finally: SETUP.clear()
def ring(R):
    lk=paint_canvas(); r=lk.alloc(16)
    for i,v in enumerate((20,40,60,30)): lk.word(r+4*i,v)
    blank(lk); lk.call(address=payload_syms['round_ring'],args=(lk.canvas,r,R,0xff112233),gap=0); return lk
for R in (14,5,4,0):  # 14 and 4 had no table before corner_cover.c, 5 had
    rows={}; [rows.setdefault(b[1],[]).append(b) for b in ring(R).bands]; assert sorted(rows)==list(range(40,70))
    for y in range(41,69):
        xs=sorted(b[0] for b in rows[y]); assert xs==sorted(20+(20+60-1)-x for x in xs),(R,y)  # its sides, mirrored about the ring's centre
    assert sorted(b[0] for b in rows[55])==[20,79]  # the straight sides, a pixel each
    assert (R==0)==all(b[4]>>24==0xff for y in range(40,44) for b in rows[y]),R  # part pixels round a corner
passed()
# Every Search row site holds OLED's set's value (theme.xml's search_* geometry), as the build bakes it.
SEARCH_SITES={0x49d470:52,0x49e7b0:52,0x49f320:52,0x49d4a8:52,0x49e7e8:52,0x49f35c:52,0x49f6fc:52,0x49cdd8:52,0x49cec4:52,0x49d62c:52,0x49e970:52,0x49f164:52,0x49cde0:0,0x49d67c:4,0x49e9c0:4,0x49d670:28,0x49e9b4:28,0x49d724:31,0x49ea28:31,0x49d718:20,0x49ea1c:20}
imm=lambda m,a: m.get(a)&0xffff
k=Machine(); assert {a:imm(k,a) for a in SEARCH_SITES}==SEARCH_SITES; passed()

# The frame's turn onto the panel (fb_rotate.c, docs/internals.md#frame-rotation): fast_rotate_bgra8888 lays down the
# bytes stock's own turn does, every pixel in place, for each angle and rect, aligned or not, with padded rows, in 270's
# bands of 8 rows and their remainder, and in stock's order where the source and destination share bytes; what it leaves
# to stock (another angle) is stock's to the byte. Both run on bitmaps made by stock's own code.
import random
def rotate_case(m,o,sw,sh,rect,pad,shift,seed,shared=False):
    """(stock's result, fast's result) for src sw x sh turned by o into a fresh dst (or, shared, into src's own bytes),
    rows pad bytes longer, data shift bytes in."""
    dw,dh=(sw,sh) if o in (0,180) else (sh,sw); top=m.next  # the case's memory, given back at its end
    def bitmap(w,h,fill,data=None):
        ll=4*w+pad
        if data is None: data=m.alloc(ll*h+8)+shift; m.u.mem_write(data-shift,bytes(ll*h+8)); m.u.mem_write(data,fill(ll*h))
        bm=m.alloc(0x48); m.u.mem_write(bm,bytes(0x48)); m.word(bm,w); m.word(bm+4,h); m.u.mem_write(bm+0xe,struct.pack('<H',3))
        gb=m.call(address=syms['graphic_buffer_create_with_data'],args=(data,w,h,3),gap=0,clear=False)&0xffffffff
        m.word(bm+0x14,gb); m.call(address=syms['bitmap_set_line_length'],args=(bm,ll,0,0),gap=0,clear=False)
        return bm,data,ll*h
    r=m.alloc(16); m.u.mem_write(r,struct.pack('<4i',*rect))
    out=[]
    for f in (payload_syms['stock_rotate_bgra8888_trampoline'],syms['rotate_bgra8888_image']):
        rnd=random.Random(seed)
        src,sdata,_=bitmap(sw,sh,lambda n: bytes(rnd.getrandbits(8) for _ in range(n)))
        dst,data,n=bitmap(dw,dh,lambda n: bytes([0x5a])*n,sdata if shared else None)
        ret=m.call(address=f,args=(dst,src,r,o),gap=0,clear=False,count=2000000)
        out.append((ret,bytes(m.u.mem_read(data-shift,n+8))))
    m.next=top
    return out
m=Machine()
for n in ('bitmap_get_bpp','bitmap_get_physical_width','bitmap_get_physical_height','bitmap_get_physical_line_length',
          'bitmap_lock_buffer_for_write','bitmap_lock_buffer_for_read','bitmap_unlock_buffer','bitmap_get_line_length'):
    m.handlers.pop(syms[n],None)
m.mock('tk_calloc','tk_free',prefix='alloc:')
rnd=random.Random(1120); cases=0
for o in (90,180,270,0,45):
    for sw,sh in ((23,17),(17,23),(31,9)):
        rects=[(0,0,sw,sh),(sw-1,sh-1,1,1),(0,sh-1,sw,1),(sw-1,0,1,sh),(3,2,0,5),(3,2,5,0),(2,3,-1,4)]
        rects+=[(x,y,rnd.randint(1,sw-x),rnd.randint(1,sh-y)) for x,y in ((rnd.randrange(sw),rnd.randrange(sh)) for _ in range(6))]
        for rect in rects:
            for pad,shift,shared in ((0,0,False),(8,0,False),(4,1,False),(12,2,False),(0,0,True)):
                if rect[2]<0 and o in (90,180,270): continue  # stock's loop runs to the int's wrap: both hand it to stock
                (a_ret,a),(b_ret,b)=rotate_case(m,o,sw,sh,rect,pad,shift,cases,shared)
                assert a_ret==b_ret and a==b,(o,sw,sh,rect,pad,shift,shared); cases+=1
# the Q2's own: the 375 x 320 frame turned 270 onto the 320 x 375 page, whole, a status row's band and Home's art pane
for rect in ((0,0,375,320),(0,0,375,40),(196,141,179,179)):
    (a_ret,a),(b_ret,b)=rotate_case(m,270,375,320,rect,0,0,cases); assert a_ret==b_ret==0 and a==b,rect; cases+=1
assert cases>500,cases; passed()
