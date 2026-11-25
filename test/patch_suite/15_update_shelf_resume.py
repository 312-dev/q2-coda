class UpdateMachine(RadioMachine):
    """RadioMachine whose transfers each take their own reply: update.tar.sig's (sig) or any other's (reply)."""
    def __init__(self): super().__init__(); self.sig=b''
    def hook(self,u,address,size,unused):
        if self.handlers.get(address,'')=='r:curl_easy_perform' and str(self.opts.get(10002,'')).endswith('.sig'):
            self.reply,tar=self.sig,self.reply; super().hook(u,address,size,unused); self.reply=tar; return
        return super().hook(u,address,size,unused)
m=UpdateMachine(); test_key(m); resume=int(manifest['patch_symbols']['stock_fwupdate_trampoline'],16); m.handlers[resume]='stock_fwupdate'
m.u.hook_add(UC_HOOK_CODE,m.hook,begin=resume,end=resume)  # fast(): payload addresses hook one by one
m.handlers[0x4c7eb4]='fwupdate_tf'; m.mock('strcpy@GLIBC_2.0','widget_set_tr_text')
view=m.node('scroll_view','scroll_view_fwupdate'); m.top=m.node('window','fwupdate_page',[view])
for i in range(2):
    row=m.node('list_item'); m.nodes[view]['children'].append(row)
    assert m.call(address=0x4c8660,args=(row,i,0,0),gap=0)==0
tf,online=(m.nodes[r]['children'][0] for r in m.nodes[view]['children'])
assert m.call(address=HOOKS['systemset_fwupdate_page_init'][0],args=(m.top,5,0,0),gap=0,count=5_000_000)==0
assert m.calls[0][:3]==('stock_fwupdate',m.top,5) and m.nodes[tf]['name']=='0' and m.nodes[online]['name']=='2'
assert [f for t,f,c in m.nodes[online]['handlers'] if t==O['EVT_CLICK']][-1]==payload_syms['sw_update_open']; passed()  # after stock's own
UPDATE='/mnt/mmc/update.tar'; ASSET='update.tar'
RELEASES=f"https://github.com/{manifest['update_repo']}/releases"  # build.py --update-repo
m.files[UPDATE]=bytearray(b'old')
NU_RUNNING,NU_DONE=1,2; out=m.alloc(8)
def poll(): return m.call(address=payload_syms['netupdate_poll'],args=(out,out+4,0,0),gap=0)
def start(): return m.call(address=payload_syms['netupdate_start'],args=(0,0,0,0),gap=0)
def said(state): return m.text(m.call(address=payload_syms['netupdate_said'],args=(state,0,0,0),gap=0))
def fetch(codes,tag,reply=b'',sig=None):
    """Software Update's Download and Install (sw_update.c drives netupdate_start and netupdate_poll): its state."""
    assert start()==1
    m.codes=list(codes); m.location=f'{RELEASES}/tag/{tag}'; m.reply=reply; m.sig=update_sig(reply,tag) if sig is None else sig
    assert poll()==NU_RUNNING and ('reset_poweroptions_timer',1,1,0) in [c[:4] for c in m.calls]  # standby waits
    worker,arg=m.threads[-1]; assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=200_000_000)==0
    assert start()==0  # ended, not yet joined: no second
    state=poll(); assert UPDATE+'.part' not in m.files; return state
assert fetch([302,200,200],'9.9.9',b'TAR'*100)==NU_DONE
assert m.opts[10002]==f'{RELEASES}/download/9.9.9/{ASSET}' and m.opts[52]==1
assert m.opts[10065]=='/etc/scrobble-ca.pem' and m.opts[64]==1 and m.opts[81]==2 and m.opts[99]==1 and 20108 not in m.opts
assert bytes(m.files[UPDATE])==b'TAR'*100
m.files[UPDATE]=bytearray(b'old')
assert said(fetch([302,200,404],'9.9.9'))=='No update for this build' and m.files[UPDATE]==b'old'
assert said(fetch([302,404],'9.9.9'))=='Release not signed: not installed' and m.files[UPDATE]==b'old'
assert said(fetch([302,200,200],'9.9.9',b'TAR'*100,sig=update_sig(b'TAR'*99,'9.9.9')))=='Signature check failed: not installed'
assert m.files[UPDATE]==b'old'
assert said(fetch([302,200,200],'9.9.9',b'TAR'*100,sig=update_sig(b'TAR'*100,'9.9.8')))=='Signature check failed: not installed'
assert m.files[UPDATE]==b'old'  # another release's: refused
assert said(fetch([200],'9.9.9'))=="Can't reach GitHub"  # no redirect: no tag
if not manifest['dev']:  # --dev builds have no release tag, so always fetch
    assert said(fetch([302],VERSION))=='Latest firmware installed' and m.files[UPDATE]==b'old'
    assert said(fetch([302],'1.0.0'))=='Latest firmware installed'  # older: never a downgrade
# A cancel (the page left) before the download starts: the card may be unmounted or shared next.
assert start()==1; m.codes=[302,200,200]; m.location=f'{RELEASES}/tag/9.9.9'; m.reply=b'NEW'; m.sig=update_sig(b'NEW','9.9.9')
m.call(address=payload_syms['netupdate_cancel'],args=(0,0,0,0),gap=0)
worker,arg=m.threads[-1]; assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=5_000_000)==0
assert said(poll())=='Download canceled' and m.files[UPDATE]==b'old'; passed()

# Q2 Shelf (netupdate.c, docs/internals.md#q2-shelf): System settings' Apps page, Check for Updates
# then a row per app with its state from .q2/apps/<id>.tag against the list's pin, a manual install's
# binary and, with Wi-Fi, a check on opening (the signed list alone, docs/internals.md#release-signatures),
# kept SH_STALE_MS, its end toasting how many apps can be updated. Install and Update start a worker at
# once, which takes the zip the list pins and nothing else; Remove, Erase and Rockbox's first Install (the
# power-on default) want a second press. A press on the running install cancels it; one during the
# opening check stops the check. The lists here are signed with the run's test key (test_key).
SPOT_TAG,SPOT_FILES='/mnt/mmc/.q2/apps/spotify.tag','/mnt/mmc/.q2/apps/spotify.files'
REPO_LIST=signing.signed_list((ROOT/'sources.list').read_bytes())[0]  # the card's copy: a check leaves it unsigned
SPOT_PIN=REPO_LIST.decode().split('DiamondBond/q2-librespot ')[1].split(' ')[0]
ZIP=b'PK\x03\x04 the zip'  # every app download's bytes here (ShelfMachine's reply)
def sign_list(body): return body+b'signature '+signing.sign(signing.list_message(body),TEST_PEM).hex().encode()+b'\n'
def shelf_list(body=REPO_LIST,**tags):
    """body signed by the test key, the apps named (repo: tag) pinned to ZIP at tag."""
    lines=[]
    for line in body.decode().splitlines(keepends=True):
        repo=line.split(' ')[0]
        if repo in tags:
            words=line.split(' ',5); words[1:5]=[tags[repo],'app.zip',str(len(ZIP)),hashlib.sha256(ZIP).hexdigest()]; line=' '.join(words)
        lines.append(line)
    return sign_list(''.join(lines).encode())
USB_RECORD_READS=tuple('/sys/bus/platform/devices/13500000.otg/'+n for n in ('id_status','dwc2_mode','connected'))+(
    '/sys/class/power_supply/usb/online','/sys/class/udc/13500000.otg/state','/sys/bus/usb/devices/1-1/idVendor',
    '/mnt/mmc/.q2pod/q2-usb.log')  # and its own file, on the card

class ShelfMachine(RadioMachine):
    """RadioMachine with busybox's side scripted: unzip -l prints listing, unzip -o stages staged
    ((path, bytes), in find's order), find lists them; statfs has free bytes; renames kept in order;
    listed, once set, answers every sources.list fetch in reply's place. The test key is the release key."""
    def __init__(self,files=None):
        super().__init__(files); self.free=1<<32; self.moves=[]; self.listing=''; self.staged=[]; self.listed=None
        self.mock('rmdir@GLIBC_2.0'); test_key(self)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='r:curl_easy_perform' and self.listed is not None and self.opts.get(10002,'').endswith('/sources.list'):
            reply,self.reply=self.reply,self.listed
            try: return super().hook(u,address,size,unused)
            finally: self.reply=reply
        if name=='c:statfs@GLIBC_2.0':
            b=u.reg_read(UC_MIPS_REG_A1); self.word(b+4,4096); self.word(b+28,self.free>>12); self.returns(0,clobber=True); return
        if name=='b:rename@GLIBC_2.0': self.moves.append(self.text(u.reg_read(UC_MIPS_REG_A1)))
        if name=='s:system@GLIBC_2.0':
            cmd=self.text(u.reg_read(UC_MIPS_REG_A0)); out=cmd.split(' > ')[-1]
            if 'unzip -l' in cmd: self.files[out]=bytearray(self.listing.encode())
            elif 'unzip -o' in cmd:
                for path,data in self.staged: self.files[cmd.split('-d ')[1].split(' ')[0]+'/'+path]=bytearray(data)
            elif ' find ' in cmd: self.files[out]=bytearray(''.join(path+'\n' for path,_ in self.staged).encode())
            elif cmd.startswith('rm -rf /mnt/mmc/.q2/staging'):
                for f in [f for f in self.files if f.startswith('/mnt/mmc/.q2/staging')]: del self.files[f]
        return super().hook(u,address,size,unused)
def apps_open(m):
    assert m.call(address=payload_syms['shelf_open'],args=(0,m.event,0,0),gap=0,count=5_000_000)==0
    m.advance(0,clear=False); m.page=m.top; assert m.nodes[m.page]['name']=='apps_page'
def shelf_job(m,ms=500):
    worker,arg=m.threads[-1]; assert m.call(address=worker,args=(arg,0,0,0),gap=0,count=300_000_000)==0; m.advance(ms,clear=False)
KILL='killall -9 librespot aplay'
LIST='/mnt/mmc/.q2/sources.list'
LIST_URL=f"https://raw.githubusercontent.com/{manifest['update_repo']}/main/sources.list"  # build.py --update-repo
m=ShelfMachine({LIST:bytearray(REPO_LIST),SPOT_TAG:bytearray(SPOT_PIN.encode()),SPOT_FILES:bytearray(b'.spotify/run\n.spotify/librespot\n'),
                '/mnt/mmc/.spotify/run':bytearray(b'sh'),'/mnt/mmc/.spotify/librespot':bytearray(b'ELF'),
                '/proc/mounts':bytearray(b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n')})  # the card, for card_mounted before a remove
m.rockbox_mode=0; m.wifi=-1; apps_open(m)
BLANK,NOTE='',['','']  # Check for Updates' detail is blank; the note under the rows and its hint (feedback_empty), hidden: there is a list
assert m.labels()==['App Store','Check for Updates',BLANK,'Rockbox','Install','Spotify','Installed','Deezer','Install','Soulseek','Install','File Browser','Install','Podcasts','Install','Live Music Archive','Install','Buddy','Install','Groovebox','Install','AirPlay','Install']+NOTE and not m.threads; passed()  # no themes without Rockbox; the card apps last
m.close(); m.wifi=3; m.rockbox_mode=0o100755; m.dirs['/mnt/mmc/.spotify']=[]; apps_open(m)
assert m.labels()==['App Store','Check for Updates','Checking…','Rockbox','Update','Rockbox Themes','Install','Spotify','Installed','Deezer','Install','Soulseek','Install','File Browser','Install','Podcasts','Install','Live Music Archive','Install','Buddy','Install','Groovebox','Install','AirPlay','Install']+NOTE
m.listed=shelf_list(**{'DiamondBond/q2-librespot':'q2-v9.9','DiamondBond/q2-rockbox':'v3.2'}); m.reply=ZIP; shelf_job(m)
assert m.opts[10002]==LIST_URL and m.opts[10018]=='Q2Muse'  # the list, the check's one request
assert m.opts[10065]=='/etc/scrobble-ca.pem' and m.opts[64]==1 and m.opts[81]==2 and 20108 not in m.opts
assert m.labels()[8]=='Update' and m.labels()[2]==BLANK and '/tmp/q2-shelf.json' not in m.files  # the pin's q2-v9.9 against the card's tag
assert m.toasts[-1][3]=='1 app can be updated'; passed()  # the opening check's end, the page on top
assert bytes(m.files[LIST]).startswith(b'serial %d\n' % next(int(x[7:]) for x in REPO_LIST.splitlines() if x.startswith(b'serial '))) and b'DiamondBond/q2-librespot q2-v9.9 app.zip ' in bytes(m.files[LIST]) and b'signature' not in bytes(m.files[LIST]); passed()
m.row(0); assert m.toasts[-1][3]=='1 app can be updated' and len(m.threads)==1; passed()  # within SH_RECHECK_MS: no requests
toasts=len(m.toasts); m.close(); m.now+=9*60*1000; apps_open(m); assert len(m.threads)==1 and m.labels()[8]=='Update' and len(m.toasts)==toasts; passed()  # still fresh: no requests
m.row(3); assert m.labels()[8]=='Checking…' and len(m.threads)==2 and KILL in m.systems[-1]; m.row(3); assert m.labels()[8]=='Canceling…'
shelf_job(m); assert m.toasts[-1][3]=='Download canceled' and m.labels()[8]=='Update'; passed()
m.wifi=-1; m.row(3); assert m.toasts[-1][3]=='Connect to Wi-Fi first' and len(m.threads)==2; m.wifi=3; passed()
m.codes=[429]; m.row(3); shelf_job(m); assert m.toasts[-1][3]=='GitHub is busy, try later'; passed()  # the list's host's rate limit
good_list=m.listed; m.listed=good_list[:good_list.index(b"signature ")]; m.row(3); shelf_job(m)  # the list unsigned: nothing installs
assert m.toasts[-1][3]=='App list not signed: not used' and bytes(m.files['/mnt/mmc/.spotify/librespot'])==b'ELF'; m.listed=good_list; passed()
m.files[SPOT_TAG]=bytearray(b'q2-v9.9'); m.close(); m.wifi=-1; apps_open(m); assert m.labels()[8]=='Installed' and len(m.threads)==4
m.row(3); assert m.labels()[8]=='Press again to remove' and len(m.threads)==4
m.row(1); assert m.labels()[8]=='Installed' and m.toasts[-1][3]=='Connect to Wi-Fi first'  # another row's press drops it
m.row(3); m.row(3); assert m.labels()[8]=='Removing…'; shelf_job(m)
assert m.toasts[-1][3]=='Spotify removed' and KILL in m.systems[-3] and m.systems[-2]=='sync' and KILL in m.systems[-1]  # spot_stop: before and after
assert not {SPOT_TAG,SPOT_FILES,'/mnt/mmc/.spotify/run','/mnt/mmc/.spotify/librespot'}&set(m.files) and m.labels()[8]=='Install'; passed()
# A stale check, stopped by a press (it needs no answer); then Rockbox from its pin to "ready".
m.close(); m.wifi=3; m.now+=11*60*1000; apps_open(m); assert len(m.threads)==6
m.row(2); assert m.toasts[-1][3]=='Stopped checking for updates'; shelf_job(m); assert len(m.threads)==6 and m.toasts[-1][3]=='Stopped checking for updates'; passed()
m.close(); m.rockbox_mode=0; m.dirs.pop('/mnt/mmc/.spotify')
toasts=len(m.toasts); apps_open(m); shelf_job(m); assert len(m.toasts)==toasts  # the opening check: nothing to update, nothing said
assert m.labels()==['App Store','Check for Updates',BLANK,'Rockbox','Install','Spotify','Install','Deezer','Install','Soulseek','Install','File Browser','Install','Podcasts','Install','Live Music Archive','Install','Buddy','Install','Groovebox','Install','AirPlay','Install']+NOTE  # not stopped: checked again
m.row(1); assert m.labels()[4]=='Starts at power-on; Boot to changes that. Press again to install' and len(m.threads)==7
m.row(1); assert len(m.threads)==8 and m.labels()[4]=='Checking…'; m.row(1); shelf_job(m); assert m.toasts[-1][3]=='Download canceled'
m.free=10<<20; m.row(1); m.row(1); shelf_job(m)  # the pinned zip's size and 64 MB left over
assert m.toasts[-1][3]==f'Needs {(len(ZIP)+(54<<20)+(1<<20)-1)>>20} MB more space' and not [f for f in m.files if '.q2/staging/' in f]; passed()
m.free=1<<32; m.staged=[('.rockbox/rockbox',b'ELF3.2'),('.rockbox/codecs/flac.codec',b'c'),('.rockbox/fonts/a b.fnt',b'f')]
m.listing=''.join(f'{len(d):9}  10-06-2026 13:18   {f}\n' for f,d in m.staged)
m.reply=ZIP+b'!'; m.row(1); m.row(1); m.moves=[]; shelf_job(m)  # another zip at the pinned tag: refused before unzip
assert m.toasts[-1][3]=="Download doesn't match the list" and not [x for x in m.moves if x.startswith('/mnt/mmc/.rockbox/')]
assert not [c for c in m.systems if 'unzip' in c] and not [f for f in m.files if '.q2/staging' in f]; passed()
m.reply=ZIP; m.row(1); m.row(1); m.rockbox_mode=0o100755; m.moves=[]; shelf_job(m)  # Rockbox on the card by the end
assert m.toasts[-1][3]=='Rockbox is ready. Find it on Home'
assert [x for x in m.moves if x.startswith('/mnt/mmc/.rockbox/')][-1]=='/mnt/mmc/.rockbox/rockbox'  # the binary last
assert m.moves[-2:]==['/mnt/mmc/.q2/apps/rockbox.files','/mnt/mmc/.q2/apps/rockbox.tag'] and m.files['/mnt/mmc/.q2/apps/rockbox.tag']==b'v3.2'
assert m.files['/mnt/mmc/.rockbox/rockbox']==b'ELF3.2' and m.files['/mnt/mmc/.rockbox/fonts/a b.fnt']==b'f'
assert m.files['/mnt/mmc/.q2/apps/rockbox.files']==b'.rockbox/rockbox\n.rockbox/codecs/flac.codec\n.rockbox/fonts/a b.fnt\n'
assert m.opts[10002]=='https://github.com/DiamondBond/q2-rockbox/releases/download/v3.2/app.zip'  # the pin's own asset
assert not [f for f in m.files if '.q2/staging' in f] and 'sync' in m.systems; passed()
assert m.labels()==['App Store','Check for Updates',BLANK,'Rockbox','Installed','Rockbox Themes','Install','Spotify','Install','Deezer','Install','Soulseek','Install','File Browser','Install','Podcasts','Install','Live Music Archive','Install','Buddy','Install','Groovebox','Install','AirPlay','Install']+NOTE; passed()  # the rows built again
m.listing='        1  10-06-2026 13:18   .rockbox/../../x\n'; m.files.pop('/mnt/mmc/.q2/apps/rockbox.tag'); m.row(1); shelf_job(m)
assert m.toasts[-1][3]=='The download is not a usable app' and m.files['/mnt/mmc/.rockbox/rockbox']==b'ELF3.2'; passed()
m.close(); passed()
# No list on the card yet: Check for Updates alone, which loads it with Wi-Fi. The note under the row says so
# (No apps listed, then its hint), the row's detail blank; the fetched list (its bad lines skipped) is kept
# on the card and its rows shown; a list with no app, or one the release key didn't sign, leaves the one in use.
NO_LIST=['No apps listed','Connect to Wi-Fi, then Check for Updates']  # sh_text's feedback_empty
m=ShelfMachine(); m.rockbox_mode=0; m.wifi=-1; apps_open(m)
assert m.labels()==['App Store','Check for Updates','']+NO_LIST and not m.threads
m.row(0); assert m.toasts[-1][3]=='Connect to Wi-Fi first' and not m.threads; passed()
m.wifi=3; m.row(0); assert m.labels()[2]=='Checking…' and len(m.threads)==1; m.row(0); assert len(m.threads)==1  # checking already
m.codes=[404]; shelf_job(m); assert m.toasts[-1][3]=="Can't reach GitHub" and m.labels()[1:]==['Check for Updates','']+NO_LIST
m.row(0); assert len(m.threads)==2; passed()  # failed: the next press fetches again
FOO_PIN=b'1.0 f.zip 5 '+b'ab'*32
m.reply=sign_list(b'serial 2\n# apps\nme/q2-foo '+FOO_PIN+b' Foo\nme/q2-Bad '+FOO_PIN+b'\nme/q2-bar Bar\n'); shelf_job(m)
assert m.labels()==['App Store','Check for Updates',BLANK,'Foo','Install']+NOTE and m.files[LIST]==b'serial 2\nme/q2-foo '+FOO_PIN+b' Foo\n'
assert m.toasts[-1][3]=='All apps are up to date'; passed()  # pressed: said
m.close(); m.now+=11*60*1000; m.reply=b'nothing'; apps_open(m); shelf_job(m)
assert m.labels()==['App Store','Check for Updates',BLANK,'Foo','Install']+NOTE and m.files[LIST]==b'serial 2\nme/q2-foo '+FOO_PIN+b' Foo\n'; passed()
m.close(); m.now+=11*60*1000; m.reply=sign_list(b'serial 1\nme/q2-old '+FOO_PIN+b' Old\n'); apps_open(m); shelf_job(m)  # an older signed list
assert m.labels()==['App Store','Check for Updates',BLANK,'Foo','Install']+NOTE and m.files[LIST]==b'serial 2\nme/q2-foo '+FOO_PIN+b' Foo\n'
m.row(0); shelf_job(m); assert m.toasts[-1][3]=='App list not signed: not used'; m.close(); passed()

# Resume: once a second the UI loop polls the playing track; one of Q2POD RESUMEMIN minutes (30) or
# longer keeps its place in a ring written whole to /mnt/data (a .tmp, renamed), every RESUME_SAVE_S
# of play, after a pause and on a track change, and jumps back there when it next starts playing near
# its beginning.
# A place near either end is forgotten; a short or CUE track, or a paused start, is left alone.
RECORD='/mnt/data/q2-state'  # system.c state_poll's record, written on the first pass and on a change
RECORD_FD=77  # PowerMachine's fileno for it, so its fsync is told apart
class ResumeMachine(QueueMachine):
    def __init__(self,files=None):
        super().__init__()
        self.files={} if files is None else files; self.open={}; self.play=(0,0); self.seeks=[]; self.opens=self.renames=0
        self.files.setdefault('/proc/mounts',b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n')  # the card in: places and counts are on it (card_store.c)
        self.handlers[sleep_hook+12]='stock_sleep'
        for n in ('fopen@GLIBC_2.2','fread@GLIBC_2.0','fwrite@GLIBC_2.0','fclose@GLIBC_2.2','rename@GLIBC_2.0',
                  'access@GLIBC_2.0','time@GLIBC_2.0','player_playtime_and_length',
                  'fseek@GLIBC_2.0','ftell@GLIBC_2.0'): self.handlers[syms[n]]='r:'+n  # scrobble_append's checks
        self.handlers[payload_syms['stock_seek_time_trampoline']]='r:player_seek_time'
        self.epoch=1700000000
        self.mock('tk_snprintf','strstr@GLIBC_2.0')
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')  # scrobble.c's log lock
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('r:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0; self.calls.append((name,a,b,c))
        if name=='fopen':
            path,mode=self.text(a),self.text(b)
            # the boot logo's lit-only mount poll, and Guest Play's state read (guest.c guest_poll, each GUEST_READ_MS
            # from the headset poll whether or not a guest plays); dark, any other open counts
            # and the USB record at a Standby (usb_record.c): the controller's state, and its line on the card, not the flash;
            # and bluetoothd's position file in /tmp (now_source.c), written when the song, state or position jumps
            self.opens+=path not in (RECORD,'/tmp/q2-guest.state','/tmp/q2-bt.play.tmp',*USB_RECORD_READS) and (path!='/proc/mounts' or not self.u.mem_read(syms['g_backlight_status'],1)[0])
            if mode=='rb' and path not in self.files: ret=0
            else: ret=0x2000000+len(self.calls); self.open[ret]=[path,mode,0,b'' if mode=='wb' else self.files.get(path,b'')]
        elif name=='fread':
            f=self.open[d]; data=f[3][f[2]:f[2]+b*c]; f[2]+=len(data); self.u.mem_write(a,data); ret=len(data)//b
        elif name=='fwrite': self.open[d][3]+=bytes(self.u.mem_read(a,b*c)); ret=c
        elif name=='fseek': f=self.open[a]; f[2]=signed(b)+(0,f[2],len(f[3]))[c]
        elif name=='ftell': ret=self.open[a][2]
        elif name=='fclose':
            path,mode,_,data=self.open.pop(a)
            if mode in ('wb','ab'): self.files[path]=data
        elif name=='access': ret=0 if self.text(a) in self.files else -1
        elif name=='time': ret=self.epoch
        elif name=='rename':  # the resume file's; bluetoothd's position file (now_source.c) is not counted
            self.renames+=self.text(b)!='/tmp/q2-bt.play'; self.files[self.text(b)]=self.files.pop(self.text(a))
        elif name=='player_playtime_and_length':
            if self.play[1]: self.word(a,self.play[0]); self.word(b,self.play[1]); ret=1
            else: ret=-1
        elif name=='player_seek_time':  # a jump nobody pressed: past the hook, no stamp (docs/internals.md#wired-start)
            assert not isinstance(self,ResumeMachine) or not any(c[0]=='stock_press_stamp' for c in self.calls)
            self.seeks.append(signed(a)); self.play=(signed(a),self.play[1])
        self.returns(ret,clobber=True)
    def poll(self,sec,total=3600,pos=None,n=1):  # n polls a second apart, the time standing at sec
        if pos is not None: self.word(O['MCL_POS'],pos)
        for _ in range(n):
            self.play=(sec,total); self.now+=1000
            assert self.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0)==0
            sec=self.play[0]  # where a seek left it
        return self
    def places(self):
        data=self.files.get('/mnt/mmc/.q2pod/ringnav-resume',b'')
        return [(k,signed(v)) for k,v in struct.iter_unpack('<II',data) if k]
def playing(m,sec,total=3600,pos=None,n=1):  # n polls a second apart while it plays on from sec
    m.poll(sec,total,pos)
    for _ in range(n-1): m.poll(m.play[0]+1,total)
    return m
m=ResumeMachine().poll(0,100,pos=1,n=3)  # a short track: no file read or write
assert not m.opens; m=ResumeMachine()
playing(m,0,pos=0,n=70)
assert len(m.places())==1 and m.places()[0][1]==61 and m.renames==1 and not m.seeks; passed()
m.poll(80,n=3); assert m.places()[0][1]==80 and m.renames==2; passed()  # paused: saved once it stands still
m.poll(80,n=5); assert m.renames==2  # still paused: nothing more to write
playing(m,81,n=5); m.poll(0,pos=1,n=3)  # another track: A's last second is kept
assert m.places()[0][1]==85 and not m.seeks; passed()
m.poll(0,pos=0,n=3); assert not m.seeks  # back to A, paused at its start: waits for Play
playing(m,1,n=3); assert m.seeks==[85] and m.places()[0][1]==85; passed()
m=ResumeMachine(m.files); m.poll(0,pos=0,n=2); playing(m,2,n=1); assert m.seeks==[85]; passed()  # after a reboot
m=ResumeMachine(m.files); m.poll(0,pos=0,n=2); playing(m,O['RESUME_START_S'],n=1); assert not m.seeks; passed()  # already under way
m.poll(3590,n=3); m.poll(0,pos=1,n=2); assert not m.places(); passed()  # finished: forgotten
m=ResumeMachine(); cue=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; m.word(cue+O['REC_CUE_START'],1200)
playing(m,0,pos=0,n=70); assert not m.places() and not m.renames; passed()
m=ResumeMachine()
for i in range(O['RESUME_SLOTS']+2):  # the last is saved by no later change
    m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[0]+O['REC_PATH'],m.string(f'/p/long{i}')); m.poll(100+i,pos=0,n=3)
assert len(m.places())==O['RESUME_SLOTS']>=512 and m.places()[0][1]==100+O['RESUME_SLOTS'] and m.places()[-1][1]==101; passed()
old32=struct.pack('<II',fnv('/p/old'),700)+bytes(8*31)  # an earlier release's 32-slot file keeps its places
m=ResumeMachine({'/mnt/mmc/.q2pod/ringnav-resume':old32}); m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[0]+O['REC_PATH'],m.string('/p/old'))
m.poll(0,pos=0,n=2); playing(m,1,n=3); assert m.seeks==[700]; passed()
# Q2POD RESUMEMIN (crossfade.c resume_min): the minutes a music track needs, 30 unless 0 (Off) or a
# five in 20-120; Off leaves music alone while Podcasts/Audiobooks still resume.
for config,total,kept in (({},1799,0),({},1800,1),({'RESUMEMIN':'20'},1199,0),({'RESUMEMIN':'20'},1200,1),
                          ({'RESUMEMIN':'0'},7200,0),({'RESUMEMIN':'125'},1800,1),({'RESUMEMIN':'25'},1500,1),
                          ({'RESUMEMIN':'22'},1799,0),({'RESUMEMIN':'x'},1800,1)):
    m=ResumeMachine(); m.config.update(config); playing(m,0,total,pos=0,n=70)
    assert len(m.places())==kept and ('/mnt/data/config.ini','Q2POD','RESUMEMIN','30') in m.config_reads,(config,total)
m=ResumeMachine(); m.config['RESUMEMIN']='0'; r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]
m.word(r+O['REC_PATH'],m.string('/mnt/mmc/podcasts/Show/01.mp3')); playing(m,0,200,pos=0,n=70)
assert m.places()==[(fnv('/mnt/mmc/podcasts/Show/01.mp3'),61)]; passed()

m=ResumeMachine(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]  # Podcasts/Audiobooks: any length, never counted
m.word(r+O['REC_PATH'],m.string('/mnt/mmc/audiobooks/Book/01.mp3')); playing(m,0,200,pos=0,n=110)
assert m.places()==[(fnv('/mnt/mmc/audiobooks/Book/01.mp3'),61)] and '/mnt/mmc/.q2pod/ringnav-plays' not in m.files
assert '/mnt/mmc/.scrobbler.log' not in m.files; passed()

# resume_held: a long track past RESUME_EDGE_S holds a place a restart would lose; ringnav_change asks then.
def held(m): return m.call(address=payload_syms['resume_held'],args=(0,0,0,0),gap=0)
m=ResumeMachine(); playing(m,0,pos=0,n=O['RESUME_EDGE_S']); assert not held(m)
playing(m,O['RESUME_EDGE_S'],n=2); assert held(m)
m=ResumeMachine(); playing(m,100,200,pos=0,n=3); assert not held(m); passed()  # short: nothing kept
m=ResumeMachine(); playing(m,0,pos=0,n=3); playing(m,3600-O['RESUME_EDGE_S']-1); assert held(m)
playing(m,3600-O['RESUME_EDGE_S']); assert not held(m); passed()  # the last seconds keep no place either

# A spoken CUE book: a chapter keeps its file's place (CUE start + seconds) and never jumps by itself;
# a chapter row's hold menu starts with Resume, which plays the list's songs from the chapter holding
# the place and seeks into it once that chapter starts. A chapter within RESUME_EDGE_S of the file's
# start keeps the book's place; the last chapter's end forgets it. A music CUE image gets neither.
book='/mnt/mmc/Book/book.m4b'
def chapter(m,r,start,end,path=book):
    m.word(r+O['REC_PATH'],m.string(path)); m.word(r+O['REC_CUE_START'],start); m.word(r+O['REC_CUE_END'],end)
def resume_at(sec,path=book): return {'/mnt/mmc/.q2pod/ringnav-resume':struct.pack('<II',fnv(path),sec)+bytes(8*O['RESUME_SLOTS']-8)}
m=ResumeMachine(); chapter(m,m.items(m.get(syms['mcl_pdeqplaylist']))[0],600,1200)
playing(m,0,600,pos=0,n=70); assert m.places()==[(fnv(book),661)] and '/mnt/mmc/.q2pod/ringnav-plays' not in m.files
assert not held(m)  # a chapter's restart leaves the book's place alone: nothing to ask
m=ResumeMachine(m.files); chapter(m,m.items(m.get(syms['mcl_pdeqplaylist']))[0],600,1200)
m.poll(0,600,pos=0,n=2); playing(m,1,600,n=3); assert not m.seeks; passed()  # picking a chapter plays it
for start,end,sec,kept in ((0,600,10,[(fnv(book),700)]),(1200,0,590,[])):  # the book's first or last 30 s
    m=ResumeMachine(resume_at(700)); chapter(m,m.items(m.get(syms['mcl_pdeqplaylist']))[0],start,end)
    playing(m,sec-5,600,pos=0,n=6); m.poll(0,pos=1,n=2); assert m.places()==kept; passed()
m=ResumeMachine(resume_at(700)); rows=m.items(m.get(syms['p_deque_showlist']))
for r,(s0,s1) in zip(rows,((0,600),(600,1200),(1200,0))): chapter(m,r,s0,s1)
m.run(0); assert m.labels()[0]=='Resume' and m.opened[-1][2:]==(1,0xf001,2) and len(m.opened[-1][1])==20
q=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; chapter(m,q,600,1200)  # the chapter at 600 starts
m.poll(0,600,pos=0,n=2); playing(m,1,600,n=2); assert m.seeks==[100]; passed()
m=ResumeMachine(resume_at(700)); rows=m.items(m.get(syms['p_deque_showlist']))  # chapter 1 paused at 10 s,
for r,(s0,s1) in zip(rows,((0,600),(600,1200),(1200,0))): chapter(m,r,s0,s1)  # the queue switching late
q=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; chapter(m,q,0,600); playing(m,0,600,pos=0,n=11); m.poll(10,600,n=3)
assert m.places()==[(fnv(book),700)]; m.run(0); m.poll(10,600,n=2); assert not m.seeks
chapter(m,q,600,1200); m.poll(0,600,n=2); playing(m,1,600,n=2); assert m.seeks==[100]; passed()
m=ResumeMachine(resume_at(700)); q=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; chapter(m,q,2400,3000)  # chapter 5
playing(m,0,600,pos=0,n=11); m.poll(10,600,n=3); assert m.places()==[(fnv(book),700)]  # paused at 10 s: kept
playing(m,11,600,n=30); m.poll(40,600,n=3); assert m.places()==[(fnv(book),2440)]; passed()  # past 30 s: moved
m=ResumeMachine(resume_at(700)); rows=m.items(m.get(syms['p_deque_showlist']))  # the chapter never comes: no seek
for r,(s0,s1) in zip(rows,((0,600),(600,1200),(1200,0))): chapter(m,r,s0,s1)
q=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; chapter(m,q,0,600); playing(m,40,600,pos=0,n=3)
m.run(0); m.poll(43,600,n=9); chapter(m,q,600,1200); m.poll(0,600,n=2); playing(m,1,600,n=2); assert not m.seeks; passed()
m=ResumeMachine(resume_at(700)); rows=m.items(m.get(syms['p_deque_showlist']))  # Resume onto the chapter playing
for r,(s0,s1) in zip(rows,((0,600),(600,1200),(1200,0))): chapter(m,r,s0,s1)
q=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; chapter(m,q,600,1200); playing(m,40,600,pos=0,n=3)
m.run(0); m.poll(43,600); assert not m.seeks  # still the old position
m.poll(0,600); playing(m,1,600,n=1); assert m.seeks==[100]; passed()
m=ResumeMachine(resume_at(700,'/p/album.flac'))
for r,(s0,s1) in zip(m.items(m.get(syms['p_deque_showlist'])),((0,600),(600,0))): chapter(m,r,s0,s1,'/p/album.flac')
m.run(0); assert 'Resume' not in m.labels()
m=ResumeMachine(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; chapter(m,r,600,1200,'/p/album.flac')
playing(m,0,600,pos=0,n=70); assert not m.places(); passed()

# Play counts: a track over LISTEN_MIN_S counts once half of it, or LISTEN_MAX_S, has been heard,
# seeks aside; a repeat counts again. Counts are written whole to /mnt/data, most recent first, the
# least recently played of the lowest counts replaced when full.
def counts(m): return [(k,n) for k,n in struct.iter_unpack('<II',m.files.get('/mnt/mmc/.q2pod/ringnav-plays',b'')) if k]
m=ResumeMachine(); playing(m,0,200,pos=0,n=101); assert not counts(m)
playing(m,100,200,n=50); assert counts(m)==[(fnv('/p/A'),1)]; passed()
m.poll(0,200); playing(m,1,200,n=101); assert counts(m)==[(fnv('/p/A'),2)]; passed()  # repeat-one
playing(m,0,200,pos=1,n=5); m.poll(150,200); playing(m,151,200,n=40); assert len(counts(m))==1; passed()  # seeked past half
playing(m,0,20,pos=2,n=25); assert len(counts(m))==1; passed()  # too short
playing(m,0,3600,pos=1,n=242); assert counts(m)[0]==(fnv('/p/B'),1); passed()  # 4 minutes of a long track
log=m.files['/mnt/mmc/.scrobbler.log'].decode().split('\n')  # the same listens, scrobbled
assert log[:3]==['#AUDIOSCROBBLER/1.1','#TZ/UTC','#CLIENT/Vamp'] and log[-1]=='' and len(log)==7
assert log[3]==f'Artist\tAlbum\tA\t\t200\tL\t{1700000000-100}\t' and log[4]==log[3] and log[5].startswith('Artist\tAlbum\tB\t\t3600\tL\t'); passed()
m=ResumeMachine(); m.epoch=2**32-1000; playing(m,0,200,pos=0,n=101); playing(m,100,200,n=50)  # 2106: time() wrapped
assert m.files['/mnt/mmc/.scrobbler.log'].decode().split('\n')[3].endswith(f'\tL\t{2**32-1000-100}\t'); passed()
m=ResumeMachine(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]
m.word(r+O['REC_ARTIST'],m.string('')); m.word(r+O['REC_TITLE'],m.string('Tab\tbed')); m.word(r+O['REC_TRACK'],7)
playing(m,0,200,pos=0,n=110); assert counts(m) and '/mnt/mmc/.scrobbler.log' not in m.files  # no artist: counted only
m.word(r+O['REC_ARTIST'],m.string('X')); m.poll(0,200); playing(m,1,200,n=110)  # the tag's title, not the file name
assert m.files['/mnt/mmc/.scrobbler.log'].decode().split('\n')[3].startswith('X\tAlbum\tTab bed\t7\t200\t'); passed()
m.word(r+O['REC_TITLE'],m.string('')); m.word(r+O['REC_NAME'],m.string('Song.flac')); m.word(r+O['REC_PATH'],m.string('/p/Song.flac'))
m.poll(0,200); playing(m,1,200,n=110)  # untitled: the file name without its extension
assert m.files['/mnt/mmc/.scrobbler.log'].decode().split('\n')[4].startswith('X\tAlbum\tSong\t7\t200\t'); passed()
# ALBUM_ARTIST=1 (issue #15): the parsed album artist, as a folder queue's record has none; else the record's
m.config['ALBUM_ARTIST']='1'; id3=syms['g_play_id3_info']; m.u.mem_write(id3,b'/p/Song.flac\0')
m.u.mem_write(id3+O['ID3_ARTIST'],b'X\0'); m.u.mem_write(id3+O['ID3_ALBUM']+O['MUSIC_INFO_ALBUM_ARTIST'],b'AA\0')
m.poll(0,200); playing(m,1,200,n=110); assert m.files['/mnt/mmc/.scrobbler.log'].decode().split('\n')[5].startswith('AA\t'); passed()
m.u.mem_write(id3+O['ID3_ALBUM']+O['MUSIC_INFO_ALBUM_ARTIST'],b'\0'); m.word(r+O['REC_ALBUM_ARTIST'],m.string('RA'))
m.poll(0,200); playing(m,1,200,n=110); assert m.files['/mnt/mmc/.scrobbler.log'].decode().split('\n')[6].startswith('RA\t'); passed()
del m.config['ALBUM_ARTIST']
# Issue #240: a Folder play's record has no title: the player's parsed one, not the file name; a CUE's is the chapter's.
m.u.mem_write(id3+O['ID3_ARTIST'],b'X\0'); m.u.mem_write(id3+O['ID3_ALBUM'],b'Album\0'); m.u.mem_write(id3+O['ID3_TITLE'],b'Parsed\0')
m.poll(0,200); playing(m,1,200,n=110); assert m.files['/mnt/mmc/.scrobbler.log'].decode().split('\n')[-2].startswith('X\tAlbum\tParsed\t7\t200\t'); passed()
m.word(r+O['REC_TITLE'],m.string('Song')); m.poll(0,200); playing(m,1,200,n=110)  # a row that took its file name as title (ringnav_localclass)
assert m.files['/mnt/mmc/.scrobbler.log'].decode().split('\n')[-2].startswith('X\tAlbum\tParsed\t7\t200\t'); passed()
for parsed in (b'\0',b'(null)\0'):  # parsed but untitled: back to the file's
    m.u.mem_write(id3+O['ID3_TITLE'],parsed); m.poll(0,200); playing(m,1,200,n=110)
    assert m.files['/mnt/mmc/.scrobbler.log'].decode().split('\n')[-2].startswith('X\tAlbum\tSong\t7\t200\t'),parsed; passed()
# PERCENT=25: a quarter of the song scrobbles, while the play count still waits for half; 100 needs
# all but the last 2 s; out of range keeps half or LISTEN_MAX_S.
def logged(m): return len(m.files.get('/mnt/mmc/.scrobbler.log',b'').split(b'\n'))-4
m=ResumeMachine(); m.config['PERCENT']='25'; playing(m,0,200,pos=0,n=45); assert logged(m)==-3
playing(m,45,200,n=15); assert logged(m)==1 and not counts(m)
playing(m,60,200,n=50); assert logged(m)==1 and counts(m); passed()
m=ResumeMachine(); m.config['PERCENT']='100'; playing(m,0,200,pos=0,n=190); assert logged(m)==-3
playing(m,190,200,n=10); assert logged(m)==1; passed()
m=ResumeMachine(); m.config['PERCENT']='0'; playing(m,0,200,pos=0,n=90); assert logged(m)==-3
playing(m,90,200,n=20); assert logged(m)==1; passed()
m=ResumeMachine(); m.epoch=86400; playing(m,0,200,pos=0,n=110)  # clock never set: counted only
assert counts(m) and '/mnt/mmc/.scrobbler.log' not in m.files; passed()
# q2app scrobble=0 (addon_scrobbles): a song in that app's folder counts but logs nothing, wherever under
# it. A missing key or file, any other value, another app's song or one outside any app still logs.
def app_listen(q2app,path='/mnt/mmc/.lma/cache/show/01 - Song.ogg',app='.lma'):
    m=ResumeMachine(); m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[0]+O['REC_PATH'],m.string(path))
    if q2app is not None: m.files[f'/mnt/mmc/{app}/q2app']=q2app
    playing(m,0,200,pos=0,n=110); assert counts(m)==[(fnv(path),1)],(q2app,path)
    return logged(m)
for q2app in (b'scrobble=0\n',b'name=LMA\nscrobble=0',b'name=LMA\r\nscrobble=0\r\nqr=0\r\n'):
    assert app_listen(q2app)==-3,q2app
assert app_listen(b'scrobble=0\n','/mnt/mmc/.lma/a.flac')==-3; passed()
for q2app in (None,b'',b'name=LMA\nqr=0\n',b'scrobble=1\n',b'scrobble=\n',b'scrobble=no\n',b'scrobble=00\n',b'scrobble=0x\n',b'noscrobble=0\n'):
    assert app_listen(q2app)==1,q2app
for path in ('/mnt/mmc/Music/a.flac','/mnt/mmc/.other/a.flac','/mnt/mmc/.lma','/p/.lma/a.flac'):
    assert app_listen(b'scrobble=0\n',path)==1,path
assert app_listen(b'scrobble=0\n','/mnt/mmc/.Bad_Id/a.flac','.Bad_Id')==1  # not an app's id, manifest or not
passed()
m=ResumeMachine(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]  # CUE tracks of one image: apart
playing(m,0,200,pos=0,n=110); m.word(r+O['REC_CUE_START'],300); m.poll(0,200); playing(m,1,200,n=110)
assert len(counts(m))==2 and counts(m)[1]==(fnv('/p/A'),1); passed()
m=ResumeMachine(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]  # a skipped CUE track's time stays its own
playing(m,0,200,pos=0,n=26); m.word(r+O['REC_CUE_START'],300); playing(m,0,200,n=80); assert not counts(m); passed()
full=[(i+1,1 if i==5 else 2) for i in range(O['PLAYS_SLOTS'])]
m=ResumeMachine({'/mnt/mmc/.q2pod/ringnav-plays':b''.join(struct.pack('<II',*e) for e in full)})
playing(m,0,200,pos=0,n=110); assert counts(m)==[(fnv('/p/A'),1)]+full[:5]+full[6:]; passed()

# Both lock UIs use stock's persistent flag/mode, Off or On (stock's mode 2, the buttons), and stock's key gates enforce
# it: with the screen off On drops Return, Play/Pause and the skip keys, never the wheel (which sets nothing dark).
LOCK_NAMES=['Off','On']
m=QueueMachine(); view=m.node('scroll_view','scroll_view_keylock')
m.byte(syms['g_keylock_flag'],1); m.byte(syms['g_keylock_mode'],2)
m.top=m.node('window','keylock_page',[view])
assert m.call(address=HOOKS['keylock_rows'][0],args=(m.top,0,0,0),gap=0)==0
rows=m.nodes[view]['children']; buttons=[m.nodes[r]['children'][0] for r in rows]
labels=[next(c for c in m.nodes[b]['children'] if m.nodes[c]['type']=='hscroll_label') for b in buttons]; icons=[m.nodes[b]['children'][0] for b in buttons]
assert [m.nodes[l]['text'] for l in labels]==LOCK_NAMES
assert [m.nodes[i]['image'] for i in icons]==['unselect','select']
assert not [c for c in m.calls if c[0]=='write_int_config']
for choice in (0,1):
    f,ctx=m.handler(buttons[choice],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
    assert [m.nodes[i]['image'] for i in icons]==['select' if j==choice else 'unselect' for j in range(2)]
    assert [c[1:2]+(m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[
        (2,'SYSSET','LOCKMODE'),(choice,'SYSSET','LOCKFLAG')]
passed()
# An earlier version's mode is made Off or On at boot (keylock.c keylock_boot, from ringnav_bootmode): Volume only (1)
# is Off, Everything (3) On.
for mode,want in (('1',[(0,'SYSSET','LOCKFLAG')]),('3',[(2,'SYSSET','LOCKMODE')]),('2',[])):
    b=Machine(); b.config.update({'LOCKFLAG':'1','LOCKMODE':mode})
    b.call(address=payload_syms['keylock_boot'],args=(0,0,0,0),gap=0)
    assert [(c[1],b.text(c[2]),b.text(c[3])) for c in b.calls if c[0]=='write_int_config']==want,mode
passed()
m.byte(syms['g_keylock_flag'],0)
img=m.node('image','img_lock'); label=m.node('label','label_lock')
m.top=m.node('window','statusbar_dialog',[img,label])
m.handlers[int(manifest['patch_symbols']['stock_quicksettings_trampoline'],16)]='stock_quicksettings'
for choice in (1,0):
    assert m.call(address=HOOKS['quick_lock'][0],args=(m.top,m.event,0,0),gap=0)==0
    assert m.nodes[label]['text']==LOCK_NAMES[choice]
    assert m.nodes[img]['image']==('drop_keylockopen' if choice else 'drop_keylock')
    # Reopening quick settings keeps the saved choice and paints its label immediately.
    m.call(address=HOOKS['dialog_statusbar_dialog_init'][0],args=(m.top,0,0,0),gap=0)
    assert m.nodes[label]['text']==LOCK_NAMES[choice]
    for light in (0,1):
        for key in (O['KEY_PREV'],O['KEY_NEXT'],O['KEY_PLAY'],O['KEY_BACK_BTN'],O['KEY_FWD_BTN']):
            gate=Machine(); gate.byte(syms['g_backlight_status'],light)
            gate.byte(syms['g_keylock_flag'],choice); gate.byte(syms['g_keylock_mode'],2)
            blocked=not light and choice and key not in (O['KEY_PREV'],O['KEY_NEXT'])
            for address in (HOOKS['on_wm_keydown_before_fun'][0],HOOKS['on_wm_keyup_before_fun'][0]):
                assert (gate.call(key,address=address)==11)==blocked,(choice,light,key,address)
passed()

# Quick settings (#70): a tap passes unchanged; a stationary hold opens settings (Playback: the EQ page) without a click.
for radio,page in (('wifiswitch','systemset/wifi_page'),('btswitch','systemset/bluetooth_page'),('playset','playset/equalizer_page')):
    def quick_page():
        m=QueueMachine(); m.mock('dialog_quit','widget_dispatch_event_to_target_recursive')
        m.handlers[int(manifest['patch_symbols']['stock_quicksettings_trampoline'],16)]='stock_quicksettings'
        icon=m.node('image','img_'+radio)
        win=m.node('dialog','statusbar_dialog',[icon,m.node('image','img_lock'),m.node('label','label_lock')])
        m.top=win
        m.call(address=HOOKS['dialog_statusbar_dialog_init'][0],args=(win,0,0,0),gap=0)
        def down():
            m.pressed=1
            m.word(m.event+O['EVENT_X'],30); m.word(m.event+O['EVENT_Y'],40)
            f,ctx=m.handler(icon,O['EVT_POINTER_DOWN'])
            m.call(key=30,address=f,args=(ctx,m.event,0,0),event_type=O['EVT_POINTER_DOWN'],gap=0)
        def dispatch(kind,x=30):
            m.word(m.event+O['EVENT_X'],x); m.word(m.event+O['EVENT_Y'],40)
            return m.call(key=x,address=HOOKS['widget_dispatch'][0],args=(icon,m.event,0,0),event_type=kind,gap=0)
        return m,icon,win,down,dispatch
    m,icon,win,down,dispatch=quick_page(); down()
    m.advance(O['QUICK_HOLD_MS']-1); assert not m.opened
    dispatch(O['EVT_POINTER_UP_BEFORE']); assert not m.timers
    assert dispatch(O['EVT_CLICK'])==0 and any(c[0]=='stock_dispatch' for c in m.calls)
    m.advance(O['QUICK_HOLD_MS']); assert not m.opened; passed()
    m,icon,win,down,dispatch=quick_page(); down(); m.advance(O['QUICK_HOLD_MS'])
    assert m.opened==[(page,None)] and [c[1:3] for c in m.calls if c[0]=='dialog_quit']==[(win,0)]
    assert any(c[0]=='widget_dispatch_event_to_target_recursive' for c in m.calls)
    assert dispatch(O['EVT_CLICK'])==11 and not any(c[0]=='stock_dispatch' for c in m.calls)
    # A later touch can tap the same icon normally.
    m.top=win; down(); dispatch(O['EVT_POINTER_UP_BEFORE']); assert dispatch(O['EVT_CLICK'])==0; passed()
    if radio=='playset':  # pulled down over the EQ page: the hold only closes the panel, no second EQ page
        m,icon,win,down,dispatch=quick_page(); m.open_pages.add('equalizer_page'); down(); m.advance(O['QUICK_HOLD_MS'])
        assert not m.opened and [c[1:3] for c in m.calls if c[0]=='dialog_quit']==[(win,0)]; passed()
    for kind in (O['EVT_POINTER_MOVE_BEFORE'],O['EVT_POINTER_ABORT'],O['EVT_KEY_DOWN_BEFORE']):
        m,icon,win,down,dispatch=quick_page(); down(); dispatch(kind,x=50)
        m.advance(O['QUICK_HOLD_MS']); assert not m.opened, (kind,m.opened)
    for kind in (O['EVT_DESTROY'],O['EVT_WINDOW_CLOSE'],O['EVT_WINDOW_BACKGROUND']):
        m,icon,win,down,dispatch=quick_page(); down(); f,ctx=m.handler(win,kind)
        m.call(address=f,args=(ctx,m.event,0,0),event_type=kind,gap=0)
        m.advance(O['QUICK_HOLD_MS']); assert not m.opened
    m,icon,win,down,dispatch=quick_page(); down(); m.top=m.node('window','other')
    m.advance(O['QUICK_HOLD_MS']); assert not m.opened
    m,icon,win,down,dispatch=quick_page(); m.timer_fail=True; down()
    assert dispatch(O['EVT_CLICK'])==0 and not m.opened; passed()

# Settings rows (docs/internals.md#charge-limit, #album-artists): Power management gains
# Charge limit, Audio settings gains Artists, after the stock rows in the same widgets
# and styles; Centre or tap toggles and saves each. Artists is stock's own PLAYSET ARTISTTYPE.
def settings_page(hook,view_name,config={},stock_rows=2,bt=1,boot=False,cls=QueueMachine,sub=0):
    m=cls(); m.config.update(config); m.restack=True
    if hook=='display': m.word(payload_syms['display_sub'],sub)  # 3: Controls
    if hook=='audioset': m.word(payload_syms['playset_sub'],sub)  # 1 to 4: Library, Sorting, Playlists, Folders
    if boot:
        m.handlers[payload_syms['peq_boot']]='stock_peq_boot'  # FP64 storage checked by test/peq.py
        m.call(address=payload_syms['ringnav_bootmode'],args=(3,0,0,0),gap=0)  # config_init's play mode
    if hook=='btrows': m.byte(syms['g_bluetoothflag'],bt)
    m.handlers[int(manifest['patch_symbols'][f'stock_{hook}_trampoline'],16)]='stock_'+hook
    entries=[m.entry(0) for _ in range(stock_rows)]
    view=m.node('scroll_view',view_name,list(entries))
    for e in m.nodes[view]['children']: m.word(e+O['W_PARENT'],view)
    m.top=m.node('window','page',[m.node('list_view','list_view',[view])])
    target={'power':'systemset_powermanager_page_init','audioset':'playset_playset_page_init','btrows':'btmenu_rows','display':'systemset_display_page_init'}[hook]
    assert m.call(address=HOOKS[target][0],args=(m.top,5,0,0),gap=0)==0 and m.calls[0][:3]==('stock_'+hook,m.top,5)
    rows=sorted(r for r in set(m.nodes[view]['children'])-set(entries) if m.nodes[r]['children'] and m.nodes[m.nodes[r]['children'][0]]['type']=='button')  # ours, as made (the page may restack)
    buttons=[m.nodes[r]['children'][0] for r in rows]; labels=[next(c for c in m.nodes[b]['children'] if m.nodes[c]['type']=='hscroll_label') for b in buttons]
    icons=[m.nodes[m.nodes[b]['children'][0]].get('image') for b in buttons]  # None: no icon
    def click(i):
        m.calls=[]; f,ctx=m.handler(buttons[i],O['EVT_CLICK'])
        assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
        return [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']
    m.entries=entries
    m.settings_view=view
    def texts(kv=True):  # each row's label, kv: "title: value" where its value stands on the right column (list_value)
        vals=[[m.nodes[c]['text'] for c in m.nodes[b]['children'] if m.nodes[c].get('name')=='_setting_value'] for b in buttons]
        return [m.nodes[l]['text']+(f': {v[0]}' if kv and v else '') for l,v in zip(labels,vals)]
    return m,rows,texts,icons,click
m,rows,texts,icons,click=settings_page('power','scroll_view_powermanager')
def switch_image(m,row):
    button=m.nodes[row]['children'][0]
    images=[c for c in m.nodes[button]['children'] if m.nodes[c].get('name')=='_setting_switch']
    assert len(images)==1
    return m.nodes[images[0]]['image']
assert all(switch_image(m,rows[i])=='switch_off' for i in (2,3,4))  # Charge Limit, a key/value row on the theme's switches (pod_text)
assert not [c for c in m.nodes[m.nodes[rows[1]]['children'][0]]['children'] if m.nodes[c].get('name')=='_setting_switch']
assert len(rows)==7 and icons==['system_powermanager','usb_chargeswitch','usb_chargeswitch','wifiset_wifi','system_carmode','system_powermanager','system_powermanager'] and all(m.nodes[r]['style']=='s_listitem_black' for r in rows)
assert texts()==['Standby Limit: Off','Charge Limit: Off','USB DAC Charging','Smart Wi-Fi','system_carmode','Battery History','Boot to: Rockbox']
assert click(1)==[(1,'Q2POD','CHARGELIMIT')] and texts()[1]==f"Charge Limit: {O['CHARGE_STOP']}%"
assert click(1)==[(0,'Q2POD','CHARGELIMIT')] and texts()==['Standby Limit: Off','Charge Limit: Off','USB DAC Charging','Smart Wi-Fi','system_carmode','Battery History','Boot to: Rockbox']; passed()
# In-Vehicle Mode, System's stock row before (0x4d34c0): its byte, its key, and the state byte cleared.
m.byte(syms['g_carmode_state'],1)
assert click(4)==[(1,'SYSSET','CARMODE')] and m.u.mem_read(syms['g_carmode'],1)==b'\1' and m.u.mem_read(syms['g_carmode_state'],1)==b'\0' and switch_image(m,rows[4])=='switch_on'
assert click(4)==[(0,'SYSSET','CARMODE')] and m.u.mem_read(syms['g_carmode'],1)==b'\0' and switch_image(m,rows[4])=='switch_off'; passed()
# Standby limit (its alarm with the power poll's tests): each click steps to the next limit, then Off.
limits=[f"Standby Limit: {O[f'STANDBY_LIMIT_{i}']}h" for i in (1,2,3)]
for v,want in ((1,limits[0]),(2,limits[1]),(3,limits[2]),(0,'Standby Limit: Off')):
    assert click(0)==[(v,'Q2POD','STANDBYLIMIT')] and texts()[0]==want
passed()
# Controls: the wheel's and the buttons' rows (Buttons Lock stock's by key, for its translation).
m,rows,texts,icons,click=settings_page('display','scroll_view_display',stock_rows=3,sub=3)
assert texts()==['Wheel Sensitivity: 50%','Hold Return: Recent','Clicker: Off','system_keylock','Wake: Any Button']
assert icons==['system_display','playset_folderjump','system_keytone','system_keylock','system_keylock']; passed()
# Fast Navigation (Display, Cover Flow's page, after Sort, before Position Bar and Refresh Cover Flow): off by default;
# the row saves BIGLIBRARY, and Cover Flow reads it at once (ringnav_big_library: letters, years and half copies); a
# stored value other than 1 is off.
bm,brows,btexts,bicons,bclick=settings_page('display','scroll_view_display',stock_rows=3,sub=2)
assert btexts()==['Sort: Album','Fast Navigation','Position Bar','Refresh Cover Flow'] and bicons[1]=='playset_covermode',(btexts(),bicons)
big=lambda bm: bm.call(address=payload_syms['ringnav_big_library'],args=(0,0,0,0),gap=0)
k=1
assert big(bm)==0 and bclick(k)==[(1,'Q2POD','BIGLIBRARY')] and btexts()[k]=='Fast Navigation' and big(bm)==1
assert bclick(k)==[(0,'Q2POD','BIGLIBRARY')] and btexts()[k]=='Fast Navigation' and big(bm)==0; passed()
for v,on in (('1',1),('2',0),('0',0)):
    bm,*_=settings_page('display','scroll_view_display',{'BIGLIBRARY':v},stock_rows=3,sub=2)
    assert big(bm)==on,v
passed()
# The independent duration switch, Playback > Library's last row, is off, persists on/off, and cancels on off.
dm,drows,dtexts,dicons,dclick=settings_page('audioset','scroll_view_playset',stock_rows=13,sub=1)
assert dtexts()[-1]=='Scan Song Durations'
assert dclick(len(drows)-1)==[(1,'Q2POD','SCANDURATIONS')]
assert dclick(len(drows)-1)==[(0,'Q2POD','SCANDURATIONS')]; passed()
# The browsing-position indicator is optional, off by default, and persists independently.
pm,prows,ptexts,picons,pclick=settings_page('display','scroll_view_display',stock_rows=3,sub=2)
assert ptexts()[-2]=='Position Bar'
position_on=lambda pm: pm.call(address=payload_syms['coverflow_position_enabled'],args=(0,0,0,0),gap=0)
assert position_on(pm)==0 and pclick(len(prows)-2)==[(1,'Q2POD','CFPOSITION')] and position_on(pm)==1
assert pclick(len(prows)-2)==[(0,'Q2POD','CFPOSITION')] and position_on(pm)==0
for v,on in (('1',1),('2',0),('0',0)):
    pm,*_=settings_page('display','scroll_view_display',{'CFPOSITION':v},stock_rows=3,sub=2)
    assert position_on(pm)==on,v
passed()
# Buttons Lock opens stock's page, as its row on System did. Key Tone, System's stock row before
# (0x4cc49c), is the Clicker: Off is its byte and key at 0, the others its byte at 1 and IPOD CLICKER.
assert click(3)==[] and m.opened[-1]==('systemset/keylock_page',None)
for want,saved,on in (('Speaker',[(1,'SYSSET','KEYTONE'),(1,'IPOD','CLICKER')],1),('Headphones',[(2,'IPOD','CLICKER')],1),
                      ('Both',[(0,'IPOD','CLICKER')],1),('Off',[(0,'SYSSET','KEYTONE')],0)):
    assert click(2)==saved and texts()[2]=='Clicker: '+want and m.u.mem_read(syms['g_keytone_flag'],1)==bytes([on]),want
passed()
# Wake (docs/internals.md#wake): screen off, a lone centre press and its release never reach the UI;
# a second press within WAKE_MS passes, press and release. Other keys, the screen on, or the
# setting off pass untouched, except one more centre press within WAKE_MS of the waking one (#45).
inp=HOOKS['window_manager_dispatch_input_event'][0]; m.handlers[inp+12]='stock_input'
def wake(kind,key=O['KEY_CENTER'],gap=0):
    m.calls=[]; assert m.call(key,address=inp,args=(m.wm,m.event,0,0),event_type=kind,gap=gap,clear=False)==0
    return any(c[0]=='stock_input' for c in m.calls)
DOWN,UP=O['EVT_KEY_DOWN'],O['EVT_KEY_UP']
m.byte(syms['g_backlight_status'],0)
assert wake(DOWN,gap=1000) and wake(UP)  # setting off: as stock
RET,LONG=O['KEY_RETURN'],O['EVT_KEY_LONG']
def back_dark(): return not any(wake(t,RET,gap=1000) for t in (DOWN,LONG,UP))  # Back does nothing dark (#28)
assert back_dark()
m.byte(O['BOOT_KEY_GUARD'],1); assert wake(DOWN,gap=1000) and m.u.mem_read(O['BOOT_KEY_GUARD'],1)==b'\0' and wake(UP)  # Single press wakes after Standby
# md-gpio-keys repeats DOWN at ~258 ms, then every 50 ms. AWTK generates LONG downstream
# of ringnav_input from the first DOWN it receives, never through wake_gate itself.
class WakeMachine(Machine):
    def __init__(self,double=False):
        super().__init__(); self.config['WAKEDOUBLE']=str(int(double)); self.held_at=None
        self.handlers[inp+12]='stock_input'; self.handlers[sleep_hook+12]='stock_sleep'
        self.mock('awake_screen','getFormatString','navigator_to_with_context')
        self.byte(syms['g_backlight_status'],0)
        self.call(address=sleep_hook,args=(0,0,0,0),gap=0)
    def key(self,kind,gap=0):
        self.calls=[]
        self.call(O['KEY_CENTER'],address=inp,args=(self.wm,self.event,0,0),event_type=kind,gap=gap,clear=False)
        passed=any(c[0]=='stock_input' for c in self.calls)
        if passed and kind==DOWN and self.held_at is None: self.held_at=self.now
        if kind==UP: self.held_at=None
        return passed
    def long(self):
        # Stock's default long timer is 1000 ms after an accepted down. Its callback bypasses inp.
        assert self.held_at is not None and self.now-self.held_at>=1000
        self.calls=[]
        self.call(O['KEY_CENTER'],address=syms['on_wm_keylong_fun'],event_type=LONG,gap=0,clear=False)
        assert any(c[0]=='awake_screen' for c in self.calls) and any(c[0]=='navigator_to_with_context' for c in self.calls)
for double in (False,True):
    for duration in (258,458,708):
        w=WakeMachine(double)
        if double: assert not w.key(DOWN,1000) and not w.key(UP,50)
        assert w.key(DOWN,100)
        assert w.key(DOWN,258), 'a waking hold must keep its first repeat'
        for _ in range(258,duration,50): assert w.key(DOWN,50)
        assert w.key(UP,10), 'a waking hold must keep the release that lights the screen'
    # A fresh Standby guard identifies an accepted down whose release never arrived.
    w=WakeMachine(double)
    if double: assert not w.key(DOWN,1000) and not w.key(UP,50)
    assert w.key(DOWN,100) and w.key(DOWN,258)
    w.byte(O['BOOT_KEY_GUARD'],1)
    assert w.key(DOWN,1000)==(not double) and w.key(UP,50)==(not double)
    assert w.u.mem_read(O['BOOT_KEY_GUARD'],1)==b'\0'
    # Standby may also lose a blocked press's release; consume its new guard once, not on repeats.
    w=WakeMachine(double)
    if not double:
        assert w.key(DOWN,100) and w.key(UP,50)
        w.byte(syms['g_backlight_status'],1)
    assert not w.key(DOWN,100)
    w.byte(syms['g_backlight_status'],0); w.byte(O['BOOT_KEY_GUARD'],1)
    assert w.key(DOWN,1000)==(not double)
    assert w.u.mem_read(O['BOOT_KEY_GUARD'],1)==b'\0'
    for gap in (258,50,750): assert w.key(DOWN,gap)==(not double)
    assert w.key(UP,10)==(not double)
    if double:
        assert not w.key(DOWN,100) and not w.key(UP,50)
        assert w.key(DOWN,100) and w.key(UP,50)
    # A blocked press stays blocked beyond WAKE_MS: holding adds no new wake gesture.
    for blind_second in (False,True):
        if not double and not blind_second: continue
        w=WakeMachine(double)
        if blind_second:
            if double: assert not w.key(DOWN,1000) and not w.key(UP,50)
            assert w.key(DOWN,100) and w.key(UP,50)
            w.byte(syms['g_backlight_status'],1)
        assert not w.key(DOWN,100) and not w.key(DOWN,258)
        for _ in range(25): assert not w.key(DOWN,50)
        assert w.held_at is None and not w.key(UP,10)
    # Accepted Single presses and eligible Double presses retain stock long-press power-off.
    w=WakeMachine(double)
    if double: assert not w.key(DOWN,1000) and not w.key(UP,50)
    assert w.key(DOWN,100) and w.key(DOWN,258)
    for _ in range(15): assert w.key(DOWN,50)
    w.long()
    assert w.key(UP,10)
passed()
def eaten():  # blind presses and their repeats stay dropped with the release
    m.byte(syms['g_backlight_status'],1)
    r=not wake(DOWN,gap=100) and not wake(UP,gap=50) and not wake(DOWN,gap=50) and not wake(DOWN,gap=258)
    r=r and not wake(DOWN,gap=750) and not wake(UP)
    m.byte(syms['g_backlight_status'],0); return r
assert eaten() and wake(DOWN,gap=O['WAKE_MS']) and wake(UP)  # a blind double press only wakes
m.byte(syms['g_backlight_status'],1); assert not wake(DOWN,gap=100) and not wake(UP,gap=50); m.byte(syms['g_backlight_status'],0)
assert wake(DOWN,gap=1000) and wake(UP)  # a new press does not inherit the dropped press's flag
assert click(4)==[(1,'Q2POD','WAKEDOUBLE')] and texts()[4]=='Wake: Double Press' and back_dark()
assert not wake(DOWN,gap=1000) and not wake(UP,gap=50)  # a pocket bump
assert wake(DOWN,gap=100) and wake(UP,gap=50) and eaten()  # the double press; a third only wakes
assert not wake(DOWN,gap=O['WAKE_MS']) and not wake(UP) and not wake(DOWN,gap=O['WAKE_MS']) and not wake(UP)  # too slow
assert wake(DOWN,O['KEY_PLAY'],gap=1000) and wake(UP,O['KEY_PLAY']) and wake(UP,O['KEY_NEXT'])  # media and wheel work dark
m.byte(syms['g_backlight_status'],1); assert wake(DOWN,gap=1000) and wake(UP) and all(wake(t,RET,gap=1000) for t in (DOWN,LONG,UP)); passed()
# A loose wheel's Centre under a held Return (chord_gate): the press is dropped whole, Return passes;
# Centre held first, or long after Return's press (a lost release), passes.
assert wake(DOWN,RET,gap=1000) and not wake(DOWN,gap=30) and not wake(DOWN,gap=258) and not wake(UP,gap=50) and wake(UP,RET)
assert wake(DOWN,gap=1000) and wake(DOWN,RET,gap=30) and wake(UP) and wake(UP,RET)
assert wake(DOWN,RET,gap=1000) and wake(DOWN,gap=600) and wake(UP) and wake(UP,RET)
assert wake(DOWN,RET,gap=1000) and not wake(DOWN,gap=30) and wake(UP,RET) and wake(DOWN,gap=1000)  # its release lost
assert wake(DOWN,gap=1000) and wake(UP); passed()
# A Return hold's release can ghost a press: one starting within RETURN_GHOST_MS is dropped whole;
# later, or after a short press, it passes.
hold=700
assert wake(DOWN,RET,gap=1000) and wake(UP,RET,gap=hold) and not wake(DOWN,RET,gap=60) and not wake(UP,RET,gap=30)
assert wake(DOWN,RET,gap=1000) and wake(UP,RET,gap=hold) and wake(DOWN,RET,gap=200) and wake(UP,RET,gap=30)
assert wake(DOWN,RET,gap=1000) and wake(UP,RET,gap=100) and wake(DOWN,RET,gap=60) and wake(UP,RET,gap=30)
# The stamp rounds up (| 1): a ghost in the same even millisecond as the release is still inside the window.
assert wake(DOWN,RET,gap=1000); m.now+=hold; m.now+=m.now&1
assert wake(UP,RET,gap=0) and not wake(DOWN,RET,gap=0) and not wake(UP,RET,gap=30); passed()
# A hold whose release stock never saw (the windows closing under it) leaves its release latch armed, which would swallow
# the next short Return: a new press clears it, a held press's repeats and a ghost (dropped whole) do not.
LATCH=O['RETURN_RELEASE_LATCH']
assert wake(DOWN,RET,gap=1000); m.byte(LATCH,1); assert wake(DOWN,RET,gap=100) and m.u.mem_read(LATCH,1)==b'\1'
assert wake(UP,RET,gap=hold) and not wake(DOWN,RET,gap=60) and m.u.mem_read(LATCH,1)==b'\1' and not wake(UP,RET,gap=30)
assert wake(DOWN,RET,gap=200) and m.u.mem_read(LATCH,1)==b'\0' and wake(UP,RET,gap=30); passed()
# The symptom: left armed, stock's filter eats a short Return's release and Home does nothing; the new press clears it, and that first release reaches Home.
def home_after_hold(press):
    h=NavigationMachine(); view,_=home_list(h); h.order=[h.top]; h.sync_order(); h.paint(view)
    for _ in range(3): h.call()
    assert h.selected(view)==3; h.byte(LATCH,1); h.handlers[inp+12]='stock_input'
    if press: h.call(RET,address=inp,args=(h.wm,h.event,0,0),event_type=DOWN,gap=1000,clear=False)
    h.call(RET); return h.selected(view)
assert home_after_hold(False)==3 and home_after_hold(True)==0; passed()
# Every edge button guards Centre as Return does: a thumb on the rim closes Centre with Play/Pause,
# Previous or Next too. Centre pressed first passes, but an edge press within CHORD_MS of it ends
# Centre's hold timer, so stock's Centre hold (Shut Down) never comes; a later one leaves it.
for k in (O['KEY_PLAY'],O['KEY_BACK_BTN'],O['KEY_FWD_BTN']):
    assert wake(DOWN,k,gap=1000) and not wake(DOWN,gap=30) and not wake(UP,gap=50) and wake(UP,k),k
hold_timer=payload_syms['ringnav_hold_timer']
def centre_hold(): return m.call(address=hold_timer,args=(0x1234,m.wm,1000,O['KEY_CENTER']),gap=0,clear=False)
FWD=O['KEY_FWD_BTN']
assert wake(DOWN,gap=1000); t=centre_hold(); assert t in m.timers
assert wake(DOWN,FWD,gap=100) and t not in m.timers and wake(UP,gap=50) and wake(UP,FWD)
assert wake(DOWN,gap=1000); t=centre_hold(); assert wake(DOWN,FWD,gap=600) and t in m.timers
m.timers.pop(t); assert wake(UP,gap=50) and wake(UP,FWD)
# The stamp rounds up (| 1): an edge press in the same even millisecond as Centre's still ends the hold.
m.now+=1000; m.now+=m.now&1; assert wake(DOWN,gap=0); t=centre_hold()
assert wake(DOWN,FWD,gap=0) and t not in m.timers and wake(UP,gap=50) and wake(UP,FWD); passed()
# A lost release (Standby while held) leaves the guards whole: a press long after its button's last
# event is a new press, while repeats within 258 ms of each other are still the first press.
assert wake(DOWN,FWD,gap=1000) and wake(DOWN,FWD,gap=1000) and not wake(DOWN,gap=30) and not wake(UP,gap=50) and wake(UP,FWD)
assert wake(DOWN,FWD,gap=1000) and wake(DOWN,FWD,gap=258) and wake(DOWN,FWD,gap=258) and wake(DOWN,gap=100) and wake(UP,gap=50) and wake(UP,FWD)
assert wake(DOWN,gap=1000) and wake(DOWN,gap=1000); t=centre_hold()  # Centre's release lost
assert wake(DOWN,FWD,gap=100) and t not in m.timers and wake(UP,gap=50) and wake(UP,FWD); passed()
# Standby's resume (STANDBY_WAKE, device_standby's awake_screen) stays dark in both modes, so the
# resuming key acts as dark (#27); a wake press clears stock's resume guard, which would drop its release.
from build import STANDBY_WAKE
assert struct.unpack_from('<I',demo,fileoff(demo,STANDBY_WAKE[0]))[0]==STANDBY_WAKE[2]
m.byte(syms['g_backlight_status'],0)
m.byte(O['BOOT_KEY_GUARD'],1); assert not wake(DOWN,gap=1000) and not wake(UP,gap=50) and m.u.mem_read(O['BOOT_KEY_GUARD'],1)==b'\0'
assert wake(DOWN,gap=100) and m.u.mem_read(O['BOOT_KEY_GUARD'],1)==b'\0' and wake(UP,gap=50)
assert click(4)==[(0,'Q2POD','WAKEDOUBLE')]; passed()
# Stock V1.36's Show Navigation Bar and Wheel Function stay where Vamp's theme and wheel need them (build.py
# NAV_SHOWN, WHEEL_MODE): config_init's own code, from its NAVHIDE read to past its WHEELMODE store, leaves the bar
# shown and Volume Mode whatever the keys hold, missing included (stock reads a missing WHEELMODE as 0). Neither is written.
for held in ({},{'WHEELMODE':'0','NAVHIDE':'1'},{'WHEELMODE':'1','NAVHIDE':'0'},{'WHEELMODE':'7','NAVHIDE':'7'}):
    for patched in (True,False):
        c=Machine(patched); c.config.update(held); before=dict(c.config); c.byte(syms['g_navhide_flag'],1); c.byte(syms['g_wheelmode'],0)
        for r,v in ((UC_MIPS_REG_SP,0x7000f000),(UC_MIPS_REG_S0,c.alloc(0x100)),(UC_MIPS_REG_S1,0x780000),(UC_MIPS_REG_S4,0x780000),(UC_MIPS_REG_S2,c.string('0'))):
            c.u.reg_write(r,v)
        c.word(0x7000f000+0x18,GP); c.u.emu_start(0x504afc,0x504bc0,count=100000)
        assert c.u.reg_read(UC_MIPS_REG_PC)==0x504bc0 and [r[:3] for r in c.config_reads]==[('/mnt/data/config.ini','SYSSET',k) for k in ('NAVHIDE','WHEELMODE')]
        stock=tuple(int(v) if v in ('0','1') else 0 for v in (held.get('NAVHIDE','0'),held.get('WHEELMODE','0')))
        assert (c.u.mem_read(syms['g_navhide_flag'],1)[0],c.u.mem_read(syms['g_wheelmode'],1)[0])==((0,1) if patched else stock),(held,patched)
        assert c.config==before
passed()
# Home binds no key-up of its own (build.py HOME_KEYUP): stock V1.36's home_page_init gives the page one, whose Return
# opens Now Playing, where Home's keys are ringnav's. The word that goes is a `jalr $25`, which tens of thousands of
# stock's calls are, so the build's check can't tell this call from another. home_page_init's own code, from the
# event's load to the call's delay slot, which loads the handler: stock binds EVT_KEY_UP to it here (one of the page's
# two binds; the other, at 0x534dd0, is the window manager's dispatch); patched, the same handler is loaded and nothing
# is bound. The window starts at the call's argument setup (HOME_KEYUP[0]-0x18): re-derive it if the site moves. Its
# Return is on_home_playing_onclick.
from build import HOME_KEYUP
def home_keyup(patched):
    c=Machine(patched); home=c.node('window','home_page')
    for r,v in ((UC_MIPS_REG_SP,0x7000f000),(UC_MIPS_REG_S0,home)): c.u.reg_write(r,v)
    c.word(0x7000f000+0x10,GP); c.u.emu_start(HOME_KEYUP[0]-0x18,HOME_KEYUP[0]+8,count=20)
    return [x[2:] for x in c.calls if x[:2]==('widget_on',home)],c.u.reg_read(UC_MIPS_REG_A2)  # a mocked call clobbers $a2
def home_return(handler):
    c=Machine(False); c.mock('on_home_playing_onclick')
    return c.call(O['KEY_RETURN'],address=handler,args=(c.node('window','home_page'),c.event,0,0),gap=0),[x[0] for x in c.calls]
assert home_keyup(True)[0]==[] and home_keyup(False)[0]==[(O['EVT_KEY_UP'],home_keyup(True)[1])]
assert home_return(home_keyup(True)[1])==(0xb,['on_home_playing_onclick']); passed()
# Buttons lock acts only with the screen off, as on V1.32 (build.py KEYLOCK_LIT): stock V1.36's on_wm_keyup_before_fun
# applies it on the lit lock window too, where Vamp's wheel and keys already have their own rules. Its own test, from
# the backlight's byte, with Next under lock mode 1: locked, it leaves by the STOP exit; else it goes on to 0x4f276c.
def lock_applies(patched,lit,window,on=1):
    c=Machine(patched)
    for name,v in (('g_backlight_status',lit),('g_lockscreen_pageflag',window),('g_keylock_flag',on),('g_keylock_mode',1)): c.byte(syms[name],v)
    for r,v in ((UC_MIPS_REG_SP,0x7000f000),(UC_MIPS_REG_GP,GP),(UC_MIPS_REG_V0,syms['g_backlight_status']),(UC_MIPS_REG_V1,0xac)): c.u.reg_write(r,v)
    c.u.emu_start(0x4f2714,0x4f276c,count=12)
    return c.u.reg_read(UC_MIPS_REG_PC)!=0x4f276c
assert [lock_applies(True,*s) for s in ((0,0),(0,1),(1,0),(1,1))]==[True,True,False,False] and not lock_applies(True,0,0,on=0)
assert [lock_applies(False,*s) for s in ((0,0),(0,1),(1,0),(1,1))]==[True,True,False,True]; passed()
# Stock's lock screen never opens (build.py SAVER_OFF, docs/internals.md#screen-off-and-wake): config_init's store of
# SYSSET SCREENSAVER's 0 or 1 into g_screensaver_mode, which its two wakes test, stores 0 whatever it read; stock's
# stores what it read.
def saver_mode(patched,read):
    c=Machine(patched); c.byte(syms['g_screensaver_mode'],0xaa)
    for r,v in ((UC_MIPS_REG_V0,read),(UC_MIPS_REG_V1,syms['g_screensaver_mode'])): c.u.reg_write(r,v)
    c.u.emu_start(0x504c78,0x504c7c,count=1)
    return c.u.mem_read(syms['g_screensaver_mode'],1)[0]
assert [saver_mode(True,v) for v in (0,1)]==[0,0] and [saver_mode(False,v) for v in (0,1)]==[0,1]; passed()
# The volume dialog's wheel tick, up at 10: lit, it stores 11, sets and saves it and stops the key (RET_STOP), patched
# or not. Dark, stock V1.36's volume_dialog_on_keyup returns 0 at once, so the tick went on to on_wm_keyup_fun, which
# opened one more volume dialog over the last (#303). Patched, the dialog's step is stock's dark too (build.py VOL_DARK)
# but its hook (volume.c vol_keyup) returns 0 first, and on_wm_keyup_fun's hook drops the tick (vol_wm_keyup, below):
# the wheel sets nothing with the screen off.
def vol_steps(patched,lit):
    c=Machine(patched); c.mock('device_set_volume','write_int_config','widget_get_prop','value_uint32','timer_count','widget_remove_timer','value_set_uint32','widget_set_prop','widget_add_timer')
    for name,v in (('g_backlight_status',lit),('g_volume',10),('g_maxvolume',100)): c.byte(syms[name],v)
    ret=c.call(O['KEY_NEXT'],address=0x4a3688,args=(c.node('dialog','volume_dialog'),c.event,0,0),gap=0)
    return ret,c.u.mem_read(syms['g_volume'],1)[0],[x[0] for x in c.calls if x[0] in ('device_set_volume','write_int_config')]
assert vol_steps(True,1)==(0xb,11,['device_set_volume','write_int_config']) and vol_steps(True,0)==(0,10,[])
assert vol_steps(False,1)==(0xb,11,['device_set_volume','write_int_config']) and vol_steps(False,0)==(0,10,[]); passed()
# The wheel's volume is a lit playback screen's (volume.c vol_wm_keyup, on_wm_keyup_fun's hook): stock's own
# on_wm_keyup_fun opens its volume dialog on a wheel tick no page took on Now Playing, AirPlay's and Internet Radio's
# pages (and Spotify's showing a track, test/fw), a toast over them too, and on nothing else, nor dark; stock's opens it
# on every page, lit or dark. The keys it does not drop stay stock's (Play/Pause's own test above).
def wheel_volume(name,patched=True,lit=1,toast=False,key=O['KEY_NEXT']):
    m=Machine(patched); m.mock('navigator_to')
    stack=[m.node('window',name)]+([m.node('dialog','msginfo_dialog')] if toast else [])
    m.nodes[m.wm]={'children':stack}; m.top=stack[-1]; m.byte(syms['g_backlight_status'],lit)
    m.call(key,address=syms['on_wm_keyup_fun'],gap=0)
    return [m.text(c[1]) for c in m.calls if c[0]=='navigator_to']
VOL=['dialog/volume_dialog']
for name in ('playing_page','airplay_page','radionp_page'):
    assert wheel_volume(name)==VOL and wheel_volume(name,key=O['KEY_PREV'])==VOL and wheel_volume(name,lit=0)==[],name
assert wheel_volume('playing_page',toast=True)==VOL
for name in ('sysset_page','qrcode_page','empty_page','spotify_page','spotbrowse_page','npabout_page'):
    assert wheel_volume(name)==[] and wheel_volume(name,patched=False)==VOL,name
assert wheel_volume('sysset_page',patched=False,lit=0)==VOL; passed()
# A press the window in front changed under stays ended until its release, as on V1.32 (build.py KEY_ABORTED, #303).
# Stock's own input_device_status, a held Play/Pause: its press, its hold, the window change (the abort a window's open
# event makes), the button's next repeat, time for another hold, its release. Stock V1.36 takes the repeat as a new press:
# a key-down with a new time, a second hold, and the release delivered, which played or paused under the hold's menu.
# Patched, the window gets none of the three and the record is free again at the release; with no window change a
# repeat and the release go through, on either.
def held_key(patched,changed=True):
    c=Machine(patched); c.mock('widget_on_keydown','widget_on_keyup'); c.mock('log_get_log_level',prefix='one:')
    c.handlers[syms['widget_get_window_manager']]='window_manager'; c.handlers.pop(syms['key_event_init'],None)
    ids=c.alloc(0x200); record=ids+O['INPUT_KEYS']
    sent=lambda: [x[0] for x in c.calls if x[0] in ('widget_on_keydown','widget_on_keyup')]
    def key(kind,gap): c.call(O['KEY_PLAY'],address=syms['input_device_status_on_input_event'],args=(ids,c.wm,c.event,0),event_type=kind,gap=gap); return sent()
    def wait(ms): c.advance(ms); return sent()  # its long-press timer runs, stock's 1000 ms or KEY_HOLD_MS
    out=[key(O['EVT_KEY_DOWN'],0),wait(1000)]; pressed=c.get(record+O['INPUT_KEY_TIME'])
    if changed: c.call(address=syms['input_device_status_abort_all_pressed_keys'],args=(ids,0,0,0),gap=0)
    return out+[key(O['EVT_KEY_DOWN'],250),c.get(record+O['INPUT_KEY_TIME'])-pressed,wait(2000),key(O['EVT_KEY_UP'],0),c.get(record)]
down,up=['widget_on_keydown'],['widget_on_keyup']
assert held_key(True)==[down,down,[],0,[],[],0] and held_key(False)==[down,down,down,1250,down,up,0]
for patched in (True,False): assert held_key(patched,changed=False)==[down,down,down,0,[],up,0],patched
passed()
# A headset's keys are read once (build.py BT_INPUT): stock V1.36's own reader of BlueZ's AVRCP device, which would post
# each press as a button beside headset.c's worker, opens nothing and starts no thread; stock's does both.
for patched in (True,False):
    b=Machine(patched); b.mock('tk_thread_create','tk_thread_start','tk_strdup','close@GLIBC_2.0'); b.mock('tk_calloc','tk_free',prefix='alloc:')
    started=b.call(address=syms['btinput_thread_run'],args=(b.string('/dev/input/event4'),0x1234,0,0),gap=0)
    assert [c[0] for c in b.calls if c[0] in ('open@GLIBC_2.0','tk_thread_create')]==([] if patched else ['open@GLIBC_2.0','tk_thread_create']) and (not started or not patched)
passed()
# What stock's own key-up handlers do with a key ringnav lets through, on the pages and dialogs Vamp keeps: stop it,
# or pass it on to on_wm_keyup_fun (stock's volume, screen off and track keys), and the stock function that acts on it.
# V1.34 changed two things here, in functions that hold no patch site: the toast and the confirm stop the wheel and
# Centre themselves (how the Shut Down prompt lost its Centre, #303), and with the screen off a handler passes every
# key on untouched, as the volume dialog's did before VOL_DARK (above). V1.36 changed a third: Now Playing's handler
# takes Centre, for a screen-off of its own 350 ms on, which NP_CENTRE undoes (below). Each runs alone, lit then dark,
# with g_wheelmode as WHEEL_MODE holds it; whatever it calls answers 0 (no widget, nothing playing) and is named if
# stock exports it, widget lookups aside. Vamp patches none of the others, so stock's own answer the same.
def keyups(patched,handler):
    """{key: (lit, dark)} for each key handler doesn't pass untouched both ways: the functions it calls, then stop if it stops the key."""
    c=Machine(patched); c.handlers.clear(); end=handler+4; names={a:n for n,a in syms.items()}
    while c.get(end)>>16!=0x3c1c: end+=4  # the next function's PIC prologue (lui $gp)
    c.byte(syms['g_wheelmode'],1)
    def stub(u,address,size,_):
        if not handler<=address<end: c.calls.append(names.get(address)); c.returns(0)
    c.u.hook_add(UC_HOOK_CODE,stub); out={}
    for key in (O['KEY_RETURN'],O['KEY_PLAY'],O['KEY_PREV'],O['KEY_NEXT'],O['KEY_CENTER'],O['KEY_BACK_BTN'],O['KEY_FWD_BTN']):
        row=[]
        for lit in (1,0):
            c.byte(syms['g_backlight_status'],lit); ret=c.call(key,address=handler,args=(c.alloc(0x100),c.event,0,0),gap=0)
            assert ret in (0,0xb),(hex(handler),key,ret)
            row.append(' '.join([n for n in c.calls if n not in (None,'widget_lookup','widget_focused_widget_in_window')]+['stop']*(ret==0xb)))
        if any(row): out[key]=tuple(row)
    return out
def stock_keyups():
    ret,play,prev,nxt,centre,rew,fwd=(O['KEY_'+k] for k in ('RETURN','PLAY','PREV','NEXT','CENTER','BACK_BTN','FWD_BTN'))
    stop=('stop',''); wheel={prev:stop,nxt:stop,centre:stop}; every={**wheel,ret:stop,play:stop,rew:stop,fwd:stop}
    for want,handlers in (
            # Return goes back by on_return_onclick: on_common_keyup (most pages), allmusic_page, album_page, artistinfo_page,
            # localclass_page, playerqueue_page, playerjumpinfo_page and search_dialog
            ({ret:('on_return_onclick stop','')},(syms['on_common_keyup'],0x4abc34,0x4a608c,0x4b0f50,0x4b2ab8,0x53a930,0x538a88,0x49eb70)),
            # ... or by a function of the window's own: folder_page, specfolder_page and sortselect_dialog
            ({ret:stop},(0x533310,syms['on_specfolder_keyup'],0x4a0b54)),
            # confirminfo_dialog: Return quits it and goes on; the wheel and Centre end here
            ({**wheel,ret:('dialog_quit','')},(0x499b9c,)),
            # msginfo_dialog, the toast: as the confirm, and Play/Pause and the side buttons end here too
            ({**every,ret:('dialog_quit','')},(0x49b7c8,)),
            # updatemusic_dialog, the library scan's: Return, the wheel and Centre
            ({**wheel,ret:stop},(0x4a2a94,)),
            # checkfw_dialog, the update's: every key
            (every,(0x499528,))):
        for handler in handlers: assert keyups(True,handler)==keyups(False,handler)==want,hex(handler)
    # playing_page: Return as the first group's. Stock V1.36 ends Centre there too (the screen off 350 ms on, or at once on
    # a second); under NP_CENTRE it goes on to on_wm_keyup_fun, which ringnav's passed-on releases are for.
    back={ret:('on_return_onclick stop','')}
    assert keyups(True,0x53da5c)==back and keyups(False,0x53da5c)=={**back,centre:('power_keyup_detect power_keyup_pending_custom stop','')}
stock_keyups(); passed()
# SyncLink (#113): Eddict Player's keys, which stock posts with main_loop_post_key_event, reach
# stock's lock filters dark with the Buttons lock off; the same keys from the buttons stay locked: the lock card
# (keylock.c keylock_gate) takes their press and release before stock's filters see them.
post=HOOKS['main_loop_post_key_event'][0]
