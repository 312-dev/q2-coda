# Shuffle and Most Played: after stock's 11 Local Music rows, more in the same widgets and styles,
# moved first. The Shuffle row opens shuffle_page; its Shuffle Songs row saves shuffle as the
# play-mode setting does and plays every song (library class 0xf001) from a random track, leaving the staging deque
# as it was. Picks leave the page open; an empty library only says so. Most Played plays the
# counted songs, most played first, and leaves the play mode alone.
class ShuffleMachine(CoverflowMachine):
    def __init__(self):
        super().__init__()
        self.counts=b''; self.queued=None; self.wifi=-1
        for n in ('getAllMusic','toolsRandnum','widget_restack','fread@GLIBC_2.0','fclose@GLIBC_2.2','get_wifisignal',
                  'opendir@GLIBC_2.0','readdir@GLIBC_2.0','closedir@GLIBC_2.0','navigator_to'): self.handlers[syms[n]]='s:'+n
        self.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0','memcmp@GLIBC_2.0')
        self.handlers[syms['fopen@GLIBC_2.2']]='s:fopen'
        self.handlers[int(manifest['patch_symbols']['stock_localmusic_trampoline'],16)]='stock_localmusic'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if address==syms['navigator_to_with_context'] and self.text(u.reg_read(REGS[0]))=='playing_page':
            self.queued=self.names(self.get(u.reg_read(REGS[1])))
        if not name.startswith('s:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b=u.reg_read(REGS[0]),u.reg_read(REGS[1]); ret=0; self.calls.append((name,a,b))
        # no card (/proc/mounts doesn't open): the counts an earlier release left on the flash (card_store.c)
        if name=='fopen': ret=1 if self.text(a)=='/mnt/data/ringnav-plays' and self.counts else 0
        elif name=='fread': self.u.mem_write(a,self.counts); ret=len(self.counts)//b if b else 0  # items of b bytes
        elif name=='getAllMusic':
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in self.found]; ret=len(self.found)
            self.calls[-1]=(name,a,self.get(payload_syms['library_music']))  # scoped to music (MUSIC_WHERE)
        elif name=='toolsRandnum': ret=a-1  # stock: rand() % a
        elif name=='memcmp':
            n=u.reg_read(UC_MIPS_REG_A2); x,y=bytes(self.u.mem_read(a,n)),bytes(self.u.mem_read(b,n)); ret=(x>y)-(x<y)
        elif name=='get_wifisignal': ret=self.wifi
        elif name=='opendir': ret=0x3000000 if self.text(a)=='/mnt/mmc' else 0; self.listed=list(self.card)
        elif name=='readdir' and self.listed:
            n,t=self.listed.pop(0); ret=self.alloc(268); self.byte(ret+10,t); self.u.mem_write(ret+11,n.encode()+b'\0')
        elif name=='widget_restack':
            kids=next(n['children'] for n in self.nodes.values() if a in n['children']); kids.remove(a); kids.insert(b,a)
        self.returns(ret,clobber=True)
m=ShuffleMachine(); stock=[m.node('list_item') for _ in range(11)]
view=m.node('scroll_view','scroll_view_localmusic',stock); m.top=m.node('window','localmusic_page',[view])
assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)==0
assert m.calls[0][:3]==('stock_localmusic',m.top,5)
# Library order: the rows in library_edit.c's lr_default order (its one table), each row known by its letter: stock's
# by get_localmusic_showinfo index (home.c's stocks table), the added ones by their icon (no Search or Games row since
# 2026-11-19). No card media folders here and no scrobbles, so no Podcasts and the rest.
LR_DEFAULT=re.search(r'lr_default\[\] = "(\w+)"',(ROOT/'patch/library_edit.c').read_text()).group(1)
STOCK_LETTER={'u':0,'s':1,'l':2,'a':3,'g':4,'i':5,'f':6,'q':7,'c':8,'n':9,'p':10}  # home.c L_* by letter
ADDED_LETTER={'local_shuffle':'h','local_era':'e','local_fav':'r','local_frequentplay':'m','local_longsongs':'L'}
def letter_of(k):
    if k in stock: return next(c for c,i in STOCK_LETTER.items() if stock[i]==k)
    first=m.nodes[m.nodes[m.nodes[k]['children'][0]]['children'][0]]
    return ADDED_LETTER[first['image']]
kids=m.nodes[view]['children']; letters=''.join(letter_of(k) for k in kids)
assert letters==''.join(c for c in LR_DEFAULT if c in letters) and len(set(letters))==len(kids)==16,letters
by=dict(zip(letters,kids)); row,era,rated,top,long=by['h'],by['e'],by['r'],by['m'],by['L']
assert m.nodes[row]['style']=='s_listitem_black'
icon,label,into=m.nodes[m.nodes[era]['children'][0]]['children']
assert m.nodes[icon]['image']=='local_era' and m.nodes[label]['text']=='Years' and m.nodes[into]['image']=='list_into'
icon,label,into=m.nodes[m.nodes[long]['children'][0]]['children']
assert m.nodes[icon]['image']=='local_longsongs' and m.nodes[label]['text']=='Long Songs'
# hidden: My Fav (Ratings' Loved), Frequent (Most Played is its list), the menu review's Shuffle, Hi-Res, Long Songs,
# and Update Local Music (Settings > General > Storage's Update Library since 2026-11-19)
assert {k for k in kids if not m.nodes[k].get('visible',1)}=={stock[6],stock[7],row,stock[5],long,stock[0]}
icon,label,into=m.nodes[m.nodes[top]['children'][0]]['children']
assert m.nodes[icon]['image']=='local_frequentplay' and m.nodes[label]['text']=='Most Played'
icon,label,into=m.nodes[m.nodes[rated]['children'][0]]['children']
assert m.nodes[icon]['image']=='local_fav' and m.nodes[label]['text']=='Ratings' and m.nodes[into]['image']=='list_into'
# The Shuffle row: the local_shuffle icon, its label and stock's list_into trailing image, as the
# stock Local Music categories place them. Its button has no name, so stock's row click ignores it.
button=m.nodes[row]['children'][0]; icon,label,into=m.nodes[button]['children']
# Compare with the shipped constructor, whose body and icon x are OLED's grid's (lib_body, lib_inset), the icon stock's.
demo=(B/'demo').read_bytes()
library_body,library_icon_w,library_icon_x,library_draw_type=[struct.unpack_from('<I',demo,fileoff(demo,a))[0]&0xffff
                                                           for a in (0x535540,0x53566c,0x535678,0x5356a4)]
assert (library_body,library_icon_w,library_icon_x,library_draw_type)==(OLED_G.lib_body,52,OLED_G.lib_inset,1)
assert m.nodes[button]['style']=='s_btn_listitem' and [m.get(button+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[20,0,335,library_body]
assert m.nodes[icon]['image']=='local_shuffle'
def library_geometry(m,k):
    b=m.nodes[k]['children'][0]; icon=m.nodes[b]['children'][0]
    assert [m.get(icon+O[f]) for f in ('W_X','W_Y','W_W','W_H')]==[library_icon_x,0,library_icon_w,library_body] and m.nodes[icon]['draw_type']==library_draw_type
    assert all(m.get(c+O['W_H'])==library_body for c in [b,*m.nodes[b]['children']])
for k in (row,rated,top,long): library_geometry(m,k)
assert m.nodes[label]['style']=='s_scrlabel_white24l' and m.nodes[label]['text']=='Shuffle' and not m.nodes[button].get('name')
assert m.nodes[into]['image']=='list_into' and [m.get(into+O[k]) for k in ('W_X','W_Y','W_W','W_H')]==[282,0,50,library_body]
f,ctx=m.handler(button,O['EVT_CLICK'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
# It opens the menu: a black shuffle_page holding the three rows, at the theme's page row (oled/theme.xml page.rows), and
# the grey note under them.
# The note is no tap target, so the wheel never lands on it. A second press opens nothing new.
page=m.top; assert m.nodes[page]['name']=='shuffle_page' and m.nodes[page]['style:normal:bg_color']==-0x1000000
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.top==page
m.page=page; menu=m.find('scroll_view'); rows=m.nodes[menu]['children']
def caption(r): return m.nodes[m.nodes[r]['children'][0]]['text']
assert [m.get(r+O['W_H']) for r in rows]==[OLT['page_row']]*3 and [caption(r) for r in rows]==['Shuffle Songs','Shuffle Albums','Shuffle Folders']
assert m.texts()==['Shuffle']+[caption(r) for r in rows]+['Albums and folders take a moment to shuffle']
note=m.nodes[page]['children'][-1]
assert m.nodes[note]['type']=='hscroll_label' and m.nodes[note]['style:normal:text_color']==-0x555556 and m.nodes[note]['style:normal:font_size']==16 and not m.nodes[note].get('handlers')
# Shuffle Songs from the menu: the stock play-mode save, a random start, the staging deque kept. OLED's confirmation
# shows over Now Playing (feedback.c feedback_shuffled: the status row's name, the glyph, what and how many) and goes
# after CONF_MS, 1.2 s, back to the page.
def confirmed(what):
    cp=m.top; assert m.nodes[cp]['name']=='shuffle_confirm',m.nodes[cp].get('name')
    assert [m.nodes[c].get('text') or m.nodes[c].get('image') for c in m.nodes[cp]['children']]==['Now Playing','oled_confirm_shuffle',what,f'{len(m.found)} songs']
    m.advance(1199); assert m.top==cp; m.advance(1); assert m.top==page
def called(n): return [c[1:3] for c in m.calls if c[0]==n]
f0,ctx0=m.handler(rows[0],O['EVT_CLICK']); m.handlers[payload_syms['playback_shuffle']]='stock_playback_shuffle'  # a payload call: no t9
assert m.call(address=f0,args=(ctx0,m.event,0,0),gap=0)==0
m.advance(64,clear=False)  # the loading wait's frames (list_marks.c load_wait_sync): the library's read, then the play
assert called('config_playmode')==[(2,1)] and called('stock_playback_shuffle') and len(m.plays)==1 and m.plays[0][0]=='playing_page' and m.plays[0][2] in (0,1) and m.plays[0][3:]==(0xf001,2)
assert called('getAllMusic')==[(3,1)] and m.get(payload_syms['library_music'])==0
assert m.names(m.get(syms['tools_pdeq_directory']))==['staged'] and not m.toasts; confirmed('Shuffled Songs'); passed()
# The note: centred at the page's foot, 28 px up from the window's (library_lists.c shuffle_menu: OLED's set has no title
# row, so bar_foot + client_h() - 28, 292 in the full-bleed window, where it rested 234 under its 58 px bar), its text in the info role's face at 16 px, the muted grey. The set's
# Library's Shuffle row (hidden until Edit Library shows it, home.c ringnav_localmusic) opens the menu.
nm=oled_set(ShuffleMachine()); view=nm.node('scroll_view','scroll_view_localmusic',[nm.node('list_item') for _ in range(11)]); nm.top=nm.node('window','localmusic_page',[view])
assert nm.call(address=HOOKS['localmusic_page_init'][0],args=(nm.top,5,0,0),gap=0)==0
shuffle_row,=[r for r in nm.nodes[view]['children'] if nm.nodes[r]['children'] and 'Shuffle' in [nm.nodes[k].get('text') for k in nm.nodes[nm.nodes[r]['children'][0]]['children']]]
f_,ctx_=nm.handler(nm.nodes[shuffle_row]['children'][0],O['EVT_CLICK']); assert nm.call(address=f_,args=(ctx_,nm.event,0,0),gap=0)==0
note=nm.nodes[nm.top]['children'][-1]; assert nm.nodes[note]['text']=='Albums and folders take a moment to shuffle'
x,y,w,h=[nm.get(note+O[k]) for k in ('W_X','W_Y','W_W','W_H')]; assert (x,y,w,h)==(46,320-28,283,24)==(46,OLG.bar+234,283,24) and x+w/2==375/2
assert (nm.nodes[note]['style:normal:font_name'],nm.nodes[note]['style:normal:font_size'],nm.nodes[note]['style:normal:text_color'])==(FACE_NAMES[OLT['font[ROLE_INFO]']],16,-0x555556); passed()
m.found=[]; m.plays=[]; m.calls=[]
assert m.call(address=f0,args=(ctx0,m.event,0,0),gap=0)==0
m.advance(64,clear=False)
assert not called('config_playmode') and not m.plays and m.toasts[-1][0]=='dialog/msginfo_dialog' and m.toasts[-1][3]=='Update Local Music first' and m.top==page; passed()
# The other two rows queue and group the whole library, then play from the first shuffled group;
# the same handler runs with the row's index, and the page stays for another pick.
m.found=[m.song('T1'),m.song('T2')]; m.toasts=[]
for i in (1,2):
    m.plays=[]; m.calls=[]
    f2,ctx2=m.handler(rows[i],O['EVT_CLICK']); assert f2==f0 and ctx2==i
    assert m.call(address=f2,args=(ctx2,m.event,0,0),gap=0)==0 and m.top==page
    m.advance(64,clear=False)
    assert called('getAllMusic')==[(3,1)] and m.get(payload_syms['library_music'])==0
    assert m.plays and m.plays[-1][0]=='playing_page' and signed(m.plays[-1][3])==0xf001 and not m.toasts; passed()
# Return leaves the menu; a later press opens a fresh one. (key()'s advance clears calls, so the
# navigator_back check drives the page's own handler.)
m.plays=[]
assert m.key()==11
fk,ck=m.handler(page,O['EVT_KEY_UP'])
ev=m.alloc(0x40); m.word(ev,O['EVT_KEY_UP']); m.word(ev+O['EVENT_KEY'],O['KEY_RETURN'])
assert m.call(address=fk,args=(ck,ev,0,0),gap=0)==11 and [c[0] for c in m.calls].count('navigator_back')==1
m.close(); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.top!=page and m.nodes[m.top]['name']=='shuffle_page'; passed()
def home_settings(full):
        # Home's rows in HOME_ROWS' order (tools/ipod.py home_list), Now Playing's and Folders' shown here.
        CONFIG['HOME']=str(full); m=ShuffleMachine(); del CONFIG['HOME']; view,imgs=home_list(m)
        assert m.call(address=home_hook[0],args=(m.top,0,0,0),gap=0)==0
        order=lambda: [m.nodes[r]['name'][4:] for r in m.nodes[view]['children']]
        assert order()==HOME_ROWS; passed()
        # Settings' click is bound at Home init. Settings slides the Settings list, stock's
        # Playback and System setting rows (stock binds them), in from the right as a full-width page
        # (oled/theme.xml home.lists full), the art hidden in Split too; the wheel walks it, and Return slides the list back from the left with
        # its row kept.
        m.mock('widget_animator_prop_create','widget_animator_prop_set_params','widget_animator_start')
        lst,sets,art=(named(m,m.top,n) for n in ('list_view_home','list_view_homeset','img_homeart'))
        sview=named(m,m.top,'scroll_view_homeset'); assert not m.nodes[sets]['visible']
        assert signed(m.get(lst+O['W_Y']))==(OLT['home_top']+OLED_G.bar if full else SPLIT_TOP) and m.get(sets+O['W_Y'])==OLT['home_top']+OLED_G.bar  # home.rows top under the row's foot; Split's menu rises into the status row (09's SPLIT_TOP); Home's window is the screen
        assert m.nodes[sets].get('sensitive')==0  # hidden rows take no taps on the top level
        for img in (named(m,m.top,'img_playset'),named(m,m.top,'img_sysset')): click_target(m,img)
        m.paint(view)
        for _ in range(6): m.call()
        assert m.selected(view)==6
        m.calls=[]; f,ctx=m.handler(named(m,m.top,'img_settings'),O['EVT_CLICK'])
        assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
        assert m.nodes[sets]['visible'] and not m.nodes[lst]['visible'] and not m.nodes[art]['visible'] and m.nodes[sets]['animated']=='x'
        assert m.nodes[sets]['sensitive']==1 and m.nodes[lst]['sensitive']==0
        assert [c[1:3] for c in m.calls if c[0]=='widget_move_resize'][:1]==[(sets,375)]; passed()
        assert next(c[2] for c in m.calls if c[0]=='widget_animator_prop_create')==O['PAGE_SLIDE_MS']==220
        m.advance(219); assert signed(m.get(sets+O['W_X']))>0
        m.advance(1); assert m.get(sets+O['W_X'])==0 and not m.props
        m.paint(sview); assert m.selected(sview)==0 and m.call()==11 and m.selected(sview)==1; passed()
        m.calls=[]; assert m.call(O['KEY_RETURN'])==11
        assert m.nodes[lst]['visible'] and not m.nodes[sets]['visible'] and m.nodes[lst]['animated']=='x'
        assert m.nodes[lst]['sensitive']==1 and m.nodes[sets]['sensitive']==0
        assert [c[1:3] for c in m.calls if c[0]=='widget_move_resize'][:1]==[(lst,-(375 if full else OLED_G.split_row)&0xffffffff)]  # the main list from its own width (split_row)
        assert next(c[2] for c in m.calls if c[0]=='widget_animator_prop_create')==220
        m.advance(220); assert m.get(lst+O['W_X'])==0 and not m.props
        m.paint(view); assert m.selected(view)==6; passed()
        # Return on Home's main list selects Now Playing; Return there opens it without starting a track.
        m.calls=[]; assert m.call(O['KEY_RETURN'])==11 and m.selected(view)==0 and not destinations(m)
        m.calls=[]; assert m.call(O['KEY_RETURN'])==11 and m.selected(view)==0
        dest=destinations(m); assert len(dest)==1 and m.text(dest[0][1])=='playing_page'
        assert [m.get(dest[0][2]+4*i) for i in range(4)]==[0,0,255,2]
        assert not any(c[0]=='player_start' for c in m.calls); passed()

def home_back(full):
    CONFIG['HOME']=str(full); m=NavigationMachine(); del CONFIG['HOME']
    view,imgs=home_list(m); home=m.top; m.order=[home]; m.sync_order(); m.paint(view)
    assert m.call(O['KEY_RETURN'])==11 and m.nodes[m.top]['name']=='playing_page'
    assert not any(c[0]=='player_start' for c in m.calls)
    m.call(O['KEY_RETURN'],address=0x53da5c,args=(m.top,m.event,0,0),gap=0)
    assert m.top==home; passed()
    # Screen, lock and held-release gates still precede the shortcut; animation also blocks it.
    for flag,value in GATES+[(None,1)]:
        m=Machine(); view,imgs=home_list(m); m.paint(view)
        if flag: m.byte(syms[flag],value)
        else: m.animating=value
        m.call(O['KEY_RETURN'])
        assert not destinations(m), flag
    m=long_machine(); view,imgs=home_list(m); m.paint(view)
    long_return(m); m.call(O['KEY_RETURN'],gap=0)
    assert not destinations(m); passed()
def home_carousel():
    # A stored HOME=2, or any value but 0 and 1: Split at init, the
    # main list and the art shown, stock's carousel and arrows hidden and taking no taps, none of its cards
    # bound by the payload.
    CONFIG.update(HOME='2',SHORTCUT='5'); m=ShuffleMachine(); del CONFIG['HOME'],CONFIG['SHORTCUT']; view,imgs=home_list(m)
    assert m.call(address=home_hook[0],args=(m.top,0,0,0),gap=0)==0
    for name,on in (('slide_menu',0),('img_left',0),('img_right',0),('list_view_home',1),('list_view_homeset',0)):
        n=m.nodes[named(m,m.top,name)]; taps=n.get('sensitive',int(n['asset'].get('sensitive')!='false'))  # the asset's, untouched
        assert n['visible']==taps==on,name
    assert m.nodes[named(m,m.top,'img_homeart')]['visible']
    slide=named(m,m.top,'slide_menu')
    def under(w): return [w]+[d for c in m.nodes[w].get('children',[]) for d in under(c)]
    assert not [w for w in under(slide) if any(t==O['EVT_CLICK'] for t,*_ in m.nodes[w].get('handlers',[]))]
    passed()
home_carousel()
for full in (0,1):
    home_settings(full); home_back(full)
# The theme's swap: Home's Settings list pushes in over its ms and easing; a list still sliding starts
# again from its rest, and at ms 0 the list is simply there.
def home_swap_motion():
    CONFIG.update(SHORTCUT='0',HOME='0'); m=ShuffleMachine(); del CONFIG['SHORTCUT'],CONFIG['HOME']; m.rockbox_mode=0; view,imgs=home_list(m)
    assert m.call(address=home_hook[0],args=(m.top,0,0,0),gap=0)==0
    m.mock('widget_animator_prop_create','widget_animator_prop_set_params','widget_animator_start'); sets=named(m,m.top,'list_view_homeset')
    set_motion(m,'swap',300,2); f,ctx=m.handler(named(m,m.top,'img_settings'),O['EVT_CLICK'])
    def push(): m.calls=[]; m.call(address=f,args=(ctx,m.event,0,0),gap=0); return [c for c in m.calls if c[0]=='widget_animator_prop_create']
    assert push()[0][2]==300 and m.nodes[sets]['animated_easing']==2
    m.advance(299); assert signed(m.get(sets+O['W_X']))>0; m.advance(1); assert m.get(sets+O['W_X'])==0
    m.call(O['KEY_RETURN']); push(); m.advance(100); x=signed(m.get(sets+O['W_X'])); assert 0<x<375
    m.call(O['KEY_RETURN']); assert push(); assert signed(m.get(sets+O['W_X']))==375
    m.advance(300); assert m.get(sets+O['W_X'])==0
    m.call(O['KEY_RETURN']); m.advance(300); set_motion(m,'swap',0,0); assert not push() and m.nodes[sets]['visible'] and m.get(sets+O['W_X'])==0; passed()
home_swap_motion()
# Most Played opens a black mostplayed_page list (nothing played: the empty state, "No Plays Yet", no rows); a second
# press while it is open does nothing. Its rows are the top PLAYS_TOP, most played first and, among
# equal counts, the most recently counted (earlier slot) first; a row plays that ranked list as a
# library queue (0xf001) from itself, leaving the play mode alone.
f,ctx=m.handler(m.nodes[top]['children'][0],O['EVT_CLICK'])
m.found=[m.song(n) for n in ('T1','T2','T3')]; m.calls=[]; toasts=len(m.toasts)
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and not m.plays and len(m.toasts)==toasts
m.page=m.top; assert m.nodes[m.page]['name']=='mostplayed_page' and m.nodes[m.page]['style:normal:bg_color']==-0x1000000
assert empty_titles(m)==['No Plays Yet'] and not [c for c in m.nodes[m.page]['children'] if m.nodes[c]['type']=='hscroll_label' and m.nodes[c]['visible']] and not m.nodes[m.find('scroll_view')]['children']
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.top==m.page; m.close(); passed()
# page_list on OLED's set: the status row names the page, so it has no title row (page.title height 0): the list from y 0
# down to client_h(), the set's bar under the screen's 320, cut to its whole rows above list_fit_end (coverflow.c
# fit_view). Rows at the theme's page.rows: plain ones a row high, a detail row two lines at detail_height, all at its
# text column. The mock measures 10 px a character.
def oled_client(pm):  # oled_set, then client_h()
    oled_set(pm); return pm.call(address=payload_syms['client_h'],args=(0,0,0,0),gap=0)
def page_bar(pm):  # the bar's title: 94 px of text between hscroll_label's 2 px margins; its paint finds it
    t=pm.node('hscroll_label','label_title'); pm.word(t+O['W_W'],98); b=bar_of(pm,t)
    pm.call(address=HOOKS['widget_on_paint_background'][0],args=(b,pm.canvas,0,0),gap=0); return t
def oled_page_bar(pm):  # page_bar on OLED's status row
    t=pm.node('hscroll_label','label_title'); pm.word(t+O['W_W'],98); b=oled_bar(pm,t)
    pm.call(address=HOOKS['widget_on_paint_background'][0],args=(b,pm.canvas,0,0),gap=0); return t
def bar_reads(pm,bar):  # the old caption's destroy, which the mock's unlink doesn't fire, and the bar asked again
    pm.call(address=payload_syms['title_gone'],args=(0,0,0,0),gap=0); pm.call(address=payload_syms['bar_title_sync'],args=(pm.top,0,0,0),gap=0); return pm.nodes[bar]['text']
def plist(pm,caption,n,item_h,keep=False):
    out=pm.alloc(4) if keep else 0
    return pm.call(address=payload_syms['page_list'],args=(pm.top,pm.top,out,pm.string(caption)),stack=(n,item_h),gap=0)&0xffffffff
def prow(pm,view,i,caption,detail=None,into=False):
    f=payload_syms['page_row_detail_into' if into else 'page_row_detail']
    return pm.call(address=f,args=(view,i,pm.string(caption),pm.string(detail) if detail is not None else 0),stack=(0,),gap=0)&0xffffffff
def box(pm,w): return [signed(pm.get(w+O[k])) for k in ('W_X','W_Y','W_W','W_H')]
def page_kids(pm,kind): return [c for c in pm.nodes[pm.top]['children'] if pm.nodes[c]['type']==kind]
def full_page(pm,name):  # a page's window as the device has it: the whole screen (full-bleed), under the status row
    w=pm.node('window',name); pm.word(w+O['W_W'],375); pm.word(w+O['W_H'],320); pm.word(w+O['W_PARENT'],pm.wm); return w
PX,PW=OLT['page_x'],375-2*OLT['page_x']
pm=CoverflowMachine(); assert oled_client(pm)==320-OLG.bar; pbar=oled_page_bar(pm)
# An empty page: its message in OLED's empty note (feedback.c feedback_empty), its box holding the font; the bar keeps
# the page's name.
pm.top=full_page(pm,'tidal_downloads'); plist(pm,'No downloads yet',0,48)
pm.call(address=payload_syms['page_empty'],args=(pm.top,pm.string('Tidal Downloads'),0,0),gap=0)
pe_msg=[c for c in pm.nodes[pm.top]['children'] if pm.nodes[c]['type']=='hscroll_label' and pm.nodes[c]['visible']]
assert [pm.nodes[c]['text'] for c in pe_msg]==['No downloads yet'] and box(pm,pe_msg[0])[3]>=pm.nodes[pe_msg[0]]['style:normal:font_size'] and pm.nodes[pe_msg[0]].get('name','')=='_empty'
assert pm.nodes[pbar]['text']=='Tidal Downloads',pm.nodes[pbar]['text']; passed()
pm.top=full_page(pm,'shuffle_page'); pm.calls=[]
view=plist(pm,'Shuffle',20,48)
lv,=page_kids(pm,'list_view'); title,=page_kids(pm,'hscroll_label')
assert [c[:4] for c in pm.calls if c[0]=='list_view_create']==[('list_view_create',pm.top,0,OLG.bar)]  # from the row's foot (bar_foot)
assert signed(pm.get(lv+O['W_H']))==320-OLG.bar and pm.nodes[lv]['item_height']==OLT['page_row'] and pm.nodes[view]['virtual_h']==20*OLT['page_row']  # to the window's foot: rows past the list end fade (list_fade)
assert pm.nodes[title]['text']=='Shuffle' and pm.nodes[title]['visible']==0 and box(pm,title)[3]==0 and pm.nodes[pbar]['text']=='Shuffle'
row=prow(pm,view,3,'Shuffle Songs'); assert box(pm,row)==[0,3*OLT['page_row'],375,OLT['page_row']]
cap=pm.nodes[row]['children'][0]; assert box(pm,cap)==[PX,0,PW,OLT['page_row']]
assert (pm.nodes[cap]['style:normal:font_name'],pm.nodes[cap]['style:normal:font_size'])==(TFACE,TPX)
# A detail row: the caption in the tall title role, the detail in the meta role, in the theme's boxes (page.rows title_y,
# title_h, detail_y, detail_h).
pm.top=full_page(pm,'mostplayed_page'); dview=plist(pm,'Played',4,64); d=prow(pm,dview,1,'Song','12 plays')
cap,det=pm.nodes[d]['children'][:2]; DH=OLT['page_detail']
assert box(pm,d)==[0,DH,375,DH] and box(pm,cap)==[PX,OLT['page_cap_y'],PW,OLT['page_cap_h']] and box(pm,det)==[PX,OLT['page_det_y'],PW,OLT['page_det_h']] and pm.nodes[dview]['virtual_h']==4*DH
assert (pm.nodes[cap]['style:normal:font_name'],pm.nodes[cap]['style:normal:font_size'])==TALL and (pm.nodes[det]['style:normal:font_name'],pm.nodes[det]['style:normal:font_size'])==(FACE_NAMES[OLT['font[ROLE_META]']],OLT['font_px[ROLE_META]'])
assert pm.nodes[det]['text']=='12 plays' and pm.nodes[det]['style:normal:text_color']==-0x555556
# An empty detail with a chevron: the title the row's whole height, ending before the chevron (row_into: 316); plain, in the caption's box.
assert box(pm,pm.nodes[prow(pm,dview,2,'Cancel','')]['children'][0])==[PX,OLT['page_cap_y'],PW,OLT['page_cap_h']]
assert box(pm,pm.nodes[prow(pm,dview,3,'Download','',into=True)]['children'][0])==[PX,0,316-PX,DH]
passed()
# A caption that says what the page is, where the status row doesn't: its own first line, a row high, the list under it,
# left-aligned in the row's face. The status row holds any caption (ending a long one in an ellipsis), so that is with no
# rows (n 0) or a title its caller keeps and writes again.
pl=CoverflowMachine(); oled_client(pl); plbar=oled_page_bar(pl)
for caption,n,keep,shown in (('Remove Downloads?',3,False,False),('Exactly90',3,False,False),('No plays',0,False,True),('Cover',3,True,True)):
    pl.top=full_page(pl,'prompt_page'); plist(pl,caption,n,64,keep)
    pt,=page_kids(pl,'hscroll_label'); ll,=page_kids(pl,'list_view')
    assert pl.nodes[pt]['text']==caption and pl.nodes[pt]['visible']==shown,caption
    top=next(c[3] for c in pl.calls if c[0]=='list_view_create')
    if shown:
        assert box(pl,pt)==[PX,OLG.bar,PW,OLT['page_row']] and top==OLG.bar+OLT['page_row'] and pl.nodes[pt]['style:normal:font_size']==TPX,caption
        assert box(pl,ll)[3]==min(n*DH,(OLG.list_end-OLT['page_row'])//DH*DH)
    else: assert top==OLG.bar  # the full-bleed page's rows from the status row's foot
# page_retitle writes the caption and the bar's.
pl.top=full_page(pl,'measure_page'); plist(pl,'Measuring',1,48,True); mt,=page_kids(pl,'hscroll_label')
pl.call(address=payload_syms['page_retitle'],args=(pl.top,mt,pl.string('Measured 1/2'),0),gap=0)
assert pl.nodes[mt]['text']=='Measured 1/2' and pl.nodes[plbar]['text']=='Measured 1/2'; passed()
# The bar names the page from a hidden caption. A page of two lists, a level and one opened over it: the level's, then,
# the other on show over it and the source dropped (title_gone), the other's; a list on show wins over a hidden one that
# comes first.
pn=CoverflowMachine(); oled_client(pn); nbar=oled_page_bar(pn); pn.top=pn.node('window','twolist_page')
pa=pn.node('view','albums'); pg_=pn.node('view','grid',visible=0); pn.nodes[pn.top]['children']=[pa,pg_]
psync=lambda: pn.call(address=payload_syms['bar_title_sync'],args=(pn.top,0,0,0),gap=0)
pn.call(address=payload_syms['page_title'],args=(pa,pn.string('Albums'),1,0),gap=0); psync(); assert pn.nodes[nbar]['text']=='Albums'
pn.call(address=payload_syms['page_title'],args=(pg_,pn.string('Summer'),1,0),gap=0); pn.nodes[pa]['visible']=0; pn.nodes[pg_]['visible']=1
pn.call(address=payload_syms['title_gone'],args=(0,0,0,0),gap=0); psync(); assert pn.nodes[nbar]['text']=='Summer'; passed()
# A page under Now Playing that writes its caption ("Preparing artwork" four times a second) leaves the bar alone (on Now
# Playing the status row has no title: the clock alone); on top again, the bar reads it.
pu=CoverflowMachine(); oled_client(pu); ubar=oled_page_bar(pu); uunder=pu.top=pu.node('window','measure_page'); plist(pu,'Measuring',1,48,True); ucap,=page_kids(pu,'hscroll_label')
usync=lambda w: pu.call(address=payload_syms['bar_title_sync'],args=(w,0,0,0),gap=0)
usync(uunder); assert pu.nodes[ubar]['text']=='Measuring'; unp=pu.top=pu.node('window','playing_page'); usync(unp); assert pu.nodes[ubar]['text']==''; pu.calls=[]
pu.call(address=payload_syms['page_retitle'],args=(uunder,ucap,pu.string('Preparing artwork'),0),gap=0); plist_under=pu.call(address=payload_syms['page_list'],args=(uunder,uunder,0,pu.string('Preparing')),stack=(1,48),gap=0)
assert pu.nodes[ubar]['text']=='' and not [c for c in pu.calls if c[0]=='widget_set_text_utf8' and c[1]==ubar]
pu.top=uunder; usync(uunder); assert pu.nodes[ubar]['text']=='Preparing'; passed()
# Streaming on OLED's set: its rows lay out as settings rows at stream_row, the set's settings row, so with no icon
# (set_icon 0) and the label's box at the text column (navigation.c set_child, set_text_x). A service's own icon is
# hidden for the theme's mark painted in its place (home.c stream_marks).
def prep(sm):
    oled_set(sm); sm.handlers[int(manifest['patch_symbols']['stock_stream_trampoline'],16)]='stock_stream'
row=OLG.stream_row; sm,sv=settings(HOOKS['stream_page_init'][0],default=row,boot=prep)
assert lay(sm,sv)==0
items=[i for i in sm.nodes[sv]['children'] if sm.nodes[i]['visible']]
assert items and all(geometry(sm,i)[3]==row for i in items)
for i in items:
    b=sm.nodes[i]['children'][0]; kids=sm.nodes[b]['children']
    lead=[c for c in kids if sm.nodes[c]['type']=='image' and geometry(sm,c)[0]<SET['TEXT_X']+SET['ICON_X']]
    label=next(c for c in kids if sm.nodes[c]['type']=='hscroll_label')
    assert geometry(sm,i)[0]==geometry(sm,b)[0]==0 and lead and not [c for c in lead if sm.nodes[c]['visible']]
    assert geometry(sm,label)[0]==OLG.text_x-2
passed()
# Edit Playlist's row follows geom()'s pitch and body (geom_words), as the page's asset rows do.
m=PlaylistEditMachine(); set_value(m,'pitch',28); set_value(m,'body',26); m.open_editor(28,26); passed()
# Home's Settings list pushes in over the theme's swap motion: PAGE_SLIDE_MS, SLIDE_EASING (tools/theme.py MOTION_ROLES).
def swap_values(m,ms,easing):
    f,ctx=m.handler(named(m,m.top,'img_settings'),O['EVT_CLICK']); m.calls=[]; m.call(address=f,args=(ctx,m.event,0,0),gap=0)
    assert [c[2] for c in m.calls if c[0]=='widget_animator_prop_create'][:1]==[ms] and m.nodes[named(m,m.top,'list_view_homeset')]['animated_easing']==easing
CONFIG.update(SHORTCUT='0',HOME='0'); m=ShuffleMachine(); del CONFIG['SHORTCUT'],CONFIG['HOME']; m.rockbox_mode=0; home_list(m)
assert m.call(address=home_hook[0],args=(m.top,0,0,0),gap=0)==0; m.mock('widget_animator_prop_create','widget_animator_prop_set_params','widget_animator_start')
swap_values(m,O['PAGE_SLIDE_MS'],O['SLIDE_EASING']); passed()
# Windows from the theme: what each kind's hint does to a window as it loads or opens.
OTHER_HINT='htranslate(duration=270,easing=linear)'; FADE_HINT='fade(duration=300,easing=quadratic_out)'; ASSET_HINT=f"htranslate(duration={O['PAGE_SLIDE_MS']})"
HINT_PROPS=('anim_hint','open_anim_hint','close_anim_hint','highlight')
EXTRA=('playerqueue_page','playermore_page','playerjumpinfo_page')
PAYLOAD=('shuffle_page','mostplayed_page','ratings_page','rated_page','era_page','era_albums_page','upnext_page','playlistedit_page','streamconf_page','games_page','apps_page')
DIALOGS=('confirminfo_dialog','addplaylist_dialog','sortselect_dialog')
class MotionMachine(UiLoad,CoverflowMachine): pass
def motion_machine():  # the light half, as the confirm's dim depends on none
    SETUP[:]=[lambda k:(k.config.update(LIGHT='1'),k)]
    try: m=MotionMachine()
    finally: SETUP.clear()
    m.handlers[syms['widget_set_prop_str']]='widget_set_prop_str'; return m
def hint_writes(m): return sum(c[0]=='widget_set_prop_str' and m.text(c[2]) in HINT_PROPS for c in m.calls)
def motion_load(m,name,kind='window',**props):  # as stock's loader hands it over; ui_loaded's call clears m.calls
    w=m.node(kind,name.rsplit('/')[-1]); m.nodes[w].update(props); ui_loaded(m,name,w); return w
def motion_open(m,name): return m.call(address=payload_syms['page_open'],args=(m.string(name),0,0,0),gap=0)&0xffffffff
def motion_over(m,name): m.nodes[m.wm]={'children':[m.node('window',name),m.node('window','opening')]}  # stock has added what opens by then
# OLED states no page hint: a stock page with a slide keeps the asset's, over Home or anything else, and nothing is written.
for below in ('home_page','album_page',None):
    m=motion_machine()
    if below: motion_over(m,below)
    w=motion_load(m,'localmusic/albuminfo_page',anim_hint=ASSET_HINT); assert m.nodes[w]['anim_hint']==ASSET_HINT and hint_writes(m)==0,below
passed()
# A theme's page hint holds over any window, Home included.
for below in ('album_page','home_page'):
    m=motion_machine(); set_hint(m,'page',FADE_HINT); motion_over(m,below); assert m.nodes[motion_load(m,'localmusic/albuminfo_page',anim_hint=ASSET_HINT)]['anim_hint']==FADE_HINT,below
passed()
# Queue, More and Go to, a page with no slide, and the payload's own pages stay instant, over Home too.
m=motion_machine(); assert all('anim_hint' not in m.nodes[motion_load(m,n)] for n in (*EXTRA,'wifi_page'))
for name in (*PAYLOAD,'addon_page','radionp_page','spotify_page','chapters_page','coverflow_page'):
    m=motion_machine(); assert 'anim_hint' not in m.nodes[motion_open(m,name)],name
    motion_over(m,'home_page'); assert 'anim_hint' not in m.nodes[motion_open(m,name)],name
passed()
# A theme with a Cover Flow hint: its page takes it, and no other page does.
m=motion_machine(); set_hint(m,'coverflow',FADE_HINT); assert m.nodes[motion_open(m,'coverflow_page')]['anim_hint']==FADE_HINT; passed()
# Now Playing stays instant under OLED, which states no hint for it. A theme with a hint pushes it back on Return only.
m=motion_machine(); np=motion_load(m,'playing_page'); assert not {'anim_hint','open_anim_hint','close_anim_hint'}&set(m.nodes[np])
m=motion_machine(); set_hint(m,'playing',OTHER_HINT); np=motion_load(m,'playing_page')
assert m.nodes[np]['close_anim_hint']==OTHER_HINT and not {'anim_hint','open_anim_hint'}&set(m.nodes[np]); passed()
# The list under a close-hinted Now Playing draws its held row while Now Playing slides out.
def under_np(hint):
    q=vol_np(); vol_closed(q)
    if hint: set_hint(q,'playing',hint)
    w,_=q.page_list(3); q.paint(w); assert q.call()==11 and q.selected(w)==1; q.word(w+O['W_PARENT'],q.top); q.word(q.top+O['W_PARENT'],q.wm)
    q.top=q.win; bare(q); q.strokes=[]; q.paint(w,gap=0); return q,w
q,w=under_np(OTHER_HINT); assert q.drawn() and q.selected(w)==1
q,w=under_np(None); assert not q.drawn(); passed()
# Dialogs show at once: a confirm, add to playlist and the hold menu take no hint, and keep their dim as the asset has it.
for name in DIALOGS:
    m=motion_machine(); d=motion_load(m,name,'dialog',highlight='default(alpha=200)')
    assert 'anim_hint' not in m.nodes[d] and m.nodes[d]['highlight']=='default(alpha=200)' and hint_writes(m)==0,name
passed()
# The volume dialog is no window slide, and a dialog not named here stays as it is.
m=motion_machine(); d=motion_load(m,'dialog/volume_dialog','dialog'); assert 'anim_hint' not in m.nodes[d]
m=motion_machine(); d=motion_load(m,'dialog/other_dialog','dialog',highlight='default(alpha=200)'); assert 'anim_hint' not in m.nodes[d] and m.nodes[d]['highlight']=='default(alpha=200)'; passed()
# A sliding page of ours holds wheel ticks as a stock page does.
m=motion_machine(); p=motion_open(m,'shuffle_page'); m.nodes[p]['anim_hint']=ASSET_HINT
w,es=m.page_list(10); m.top=p; m.nodes[p]['children']=[w]; m.word(w+O['W_PARENT'],p); m.paint(w); m.animating=1
for gap in (1000,20,20): assert m.call(gap=gap)==11
assert m.selected(w)==0; m.animating=0
for i in (1,2,3): m.advance(O['HOLD_POLL_MS']); assert m.selected(w)==i
assert {t[1] for t in m.timers.values()}=={payload_syms['sb_due']}; passed()  # only the scrollbar's fade after the steps
# No load or open writes a hint or a highlight under OLED's own motion.
m=motion_machine(); hints=0; motion_over(m,'home_page')
for n,kw in (('playing_page',{}),('localmusic/albuminfo_page',{'anim_hint':ASSET_HINT}),*((n,{'highlight':'default(alpha=200)'}) for n in (*DIALOGS,*EXTRA))):
    motion_load(m,n,**kw); hints+=hint_writes(m)
motion_open(m,'shuffle_page'); hints+=hint_writes(m); motion_open(m,'coverflow_page'); hints+=hint_writes(m); assert hints==0
passed()
# The Library rows follow the constructor's live body and icon x (rows_settings.c list_row_core reads the immediates
# LIBRARY_ROW_BODY and LIBRARY_ROW_ICON_X), whichever group the audit gave them: both poked in the code here. Search is
# the Library's first row there; Shuffle (hidden until Edit Library shows it) keeps its (hidden) icon.
m=ShuffleMachine(); oled_set(m)
for a,v in ((O['LIBRARY_ROW_BODY'],60),(O['LIBRARY_ROW_ICON_X'],6)): m.u.mem_write(a,struct.pack('<H',v))
stock=[m.node('list_item') for _ in range(11)]; view=m.node('scroll_view','scroll_view_localmusic',stock); m.top=m.node('window','localmusic_page',[view])
assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)==0
button,=[b for r in m.nodes[view]['children'] for b in m.nodes[r]['children'][:1] if 'Shuffle' in [m.nodes[k].get('text') for k in m.nodes[b]['children'][1:2]]]
icon=m.nodes[button]['children'][0]
assert m.get(button+O['W_H'])==60 and m.get(icon+O['W_X'])==6; passed()
# OLED's Library is text: on its set the Library's constructor sites take the set's lib_ values (lib_body, rows with no
# icon), and Albums' (0x4a69e0) its art_body.
lm=oled_set(ShuffleMachine()); lstock=[lm.node('list_item') for _ in range(11)]; lv=lm.node('scroll_view','scroll_view_localmusic',lstock)
lm.top=lm.node('window','localmusic_page',[lv])
assert lm.call(address=HOOKS['localmusic_page_init'][0],args=(lm.top,5,0,0),gap=0)==0
lbutton=lm.nodes[lm.nodes[lv]['children'][0]]['children'][0]
assert lm.get(lbutton+O['W_H'])==OLG.lib_body and SITES[0x535540]=='lib_body' and lm.get(0x535540)&0xffff==OLG.lib_body
assert SITES[0x4a69e0]=='art_body' and lm.get(0x4a69e0)&0xffff==OLG.art_body; passed()
# ... and stock's own rows (0x535508: an icon at x 8, a label at x 72, list_into at x 282, all the row's height) take
# the same route as the added ones: no row shows an icon, every label starts at text_x on screen (the button sits at 20)
# and its right edge stays.
def library_page():
    lm=oled_set(ShuffleMachine()); lstock=[]
    for i in range(11):
        kids=[lm.node('image'),lm.node('hscroll_label',text=f'Row {i}'),lm.node('image')]; h=28
        for c,(x,w) in zip(kids,((8,52),(72,253),(282,50))):
            for k,v in zip(('W_X','W_W','W_H'),(x,w,h)): lm.word(c+O[k],v)
        lb=lm.node('button',children=kids); lm.word(lb+O['W_X'],20); lstock.append(lm.node('list_item',children=[lb]))
    lv=lm.node('scroll_view','scroll_view_localmusic',lstock); lm.top=lm.node('window','localmusic_page',[lv])
    assert lm.call(address=HOOKS['localmusic_page_init'][0],args=(lm.top,5,0,0),gap=0)==0
    return lm,[lm.nodes[r]['children'][0] for r in lm.nodes[lv]['children']]
def library_labels(lm,buttons):
    return [next(c for c in lm.nodes[b]['children'] if lm.nodes[c]['type']=='hscroll_label') for b in buttons]
lm,lbuttons=library_page(); lkids=library_labels(lm,lbuttons); assert len(lkids)>11
for lb,ll in zip(lbuttons,lkids):
    lleading=lm.nodes[lb]['children'][:lm.nodes[lb]['children'].index(ll)]
    assert not [c for c in lleading if lm.nodes[c]['visible']],lm.nodes[ll].get('text')
    assert signed(lm.get(lb+O['W_X']))+signed(lm.get(ll+O['W_X']))==OLG.text_x,lm.nodes[ll].get('text')
assert {m_x+m_w for m_x,m_w in [(signed(lm.get(ll+O['W_X'])),lm.get(ll+O['W_W'])) for ll in lkids if lm.nodes[ll].get('text','').startswith('Row')]}=={325}
passed()
# Every Library label, stock's and the added ones, takes the list titles' role: OLED's face at its title px.
for ll in lkids:
    for st_ in STATES:
        assert (lm.nodes[ll].get(f'style:{st_}:font_name'),lm.nodes[ll].get(f'style:{st_}:font_size'))==(TFACE,TPX),(st_,lm.nodes[ll].get('text'))
passed()
m=ShuffleMachine(); m.found=[m.song(f'T{i}') for i in range(130)]
m.word(m.found[1]+O['REC_ARTIST'],0)  # untagged: the count alone
slots=[(fnv('/p/T3'),5),(fnv('/p/T1'),2),(fnv('/p/T7'),5)]+[(fnv(f'/p/T{i}'),1) for i in range(8,130)]
m.counts=b''.join(struct.pack('<2I',*e) for e in slots).ljust(8*O['PLAYS_SLOTS'],b'\0')
m.calls=[]
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=50_000_000)==0; m.page=m.top
# The library is read once, unsorted and uncopied: a copy OOM-killed demo on a 47,000-song library.
assert [c[1] for c in m.calls if c[0]=='getAllMusic']==[3]
assert not [c for c in m.calls if c[0] in ('deque_init_copy','deque_assign')]
assert m.names(m.get(syms['tools_pdeq_directory']))==['staged']
ranked=['T3','T7','T1']+[f'T{i}' for i in range(8,130)][:O['PLAYS_TOP']-3]
# Each row the theme's page detail height: the title over its artist (when tagged) and play count, in stock's grey at
# the meta role's size.
details=['Artist · 5 plays','Artist · 5 plays','2 plays']+['Artist · 1 play']*(O['PLAYS_TOP']-3)
assert m.texts()==['Most Played']+[x for p in zip(ranked,details) for x in p]
rows=m.nodes[m.find('scroll_view')]['children']; assert len(rows)==O['PLAYS_TOP']
assert [(m.get(rows[1]+O['W_Y']),m.get(rows[1]+O['W_H']))]==[(OLT['page_detail'],OLT['page_detail'])]
title,detail=m.nodes[rows[1]]['children']
assert m.nodes[title]['style']==m.nodes[detail]['style'] and m.nodes[title]['style']=='s_scrlabel_white20l' and m.get(title+O['W_Y'])+m.get(title+O['W_H'])<=m.get(detail+O['W_Y'])
assert m.nodes[detail]['style:normal:text_color']==-0x555556 and m.nodes[detail]['style:normal:font_size']==OLT['font_px[ROLE_META]']
assert m.get(detail+O['W_Y'])+m.get(detail+O['W_H'])<=OLT['page_detail']
r=m.nodes[m.find('scroll_view')]['children'][2]; g,c=m.handler(r,O['EVT_CLICK'])
assert m.call(address=g,args=(c,m.event,0,0),gap=0)==0
assert m.queued==ranked and m.plays==[('playing_page',m.plays[0][1],2,0xf001,2)] and not called('config_playmode'); passed()
# A Play/Pause hold on a row opens the song menu, as Coverflow's tracks do, over the ranked list.
m.handlers[syms['navigator_to']]='q:navigator_to'; view=m.find('scroll_view'); m.paint(view); m.call()
m.press(100); assert m.hold()==11 and m.nodes[m.title]['text']=='T7' and m.release()==0
assert m.labels()==[r for r in SONG_MENU if r!='Go to Album']; passed()  # the menu sheet's song rows (03's SONG_MENU), as before no Go to Album
# Opened again, the kept tracks are ranked again without reading the library; a library change reads it again.
m.close(); m.found=m.found[:8]; m.calls=[]
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=50_000_000)==0 and not called('getAllMusic')
m.page=m.top; assert m.texts()==['Most Played']+[x for p in zip(ranked,details) for x in p]
m.close(); m.calls=[]; m.rescan(); m.calls=[]
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=50_000_000)==0 and called('getAllMusic')
m.page=m.top; assert m.texts()==['Most Played']+[x for p in zip(ranked[:3],details[:3]) for x in p]; passed()

# View Queue (Now Playing's hold menu) opens Up Next (upnext.c) in the menu sheet over the queue, its header the playing
# song; its rows, holds, lifts and Clear are test/fw/test_upnext.py's.
m=CoverflowMachine(page='playing_page',pos=1); q=m.deque([m.song(c) for c in 'ABCDE'])
m.word(syms['mcl_pdeqplaylist'],q); m.word(O['MCL_POOL'],m.deque(list(range(5)),'int'))
assert m.call(address=payload_syms['playback_set'],args=(0,1,0,0),gap=0)==1
m.press(10); assert m.hold()==11 and 'Up Next' not in m.labels(); m.release()
m.pick(m.labels().index('View Queue')); m.advance(0); m.page=m.top
assert m.nodes[m.page]['name']=='upnext_page' and m.nodes[m.page]['type']=='dialog' and any(n.get('name')=='_mm_head' for n in m.nodes.values())
m.close(); passed()

# Ratings (ratings.c rate_*): a song's 1-5 stars are kept in /mnt/data/ringnav-ratings by listen_key with the stars in
# its low three bits, set on Now Playing's rating page (rate_now_set); the heart is stock's My Fav, the like alone.
# Now Playing's heart: stock's click (0x53c968, bound to img_fav with the window) is swapped for one that toggles the
# like through it and leaves the stars as they are; a press held on it is a tap, and there is no rating picker.
FAV_CLICK=0x53c968
class RateMachine(FileMocks,ShuffleMachine):
    def __init__(self,saved=b''):
        super().__init__()
        self.budget=2_000_000  # a RATE_SLOTS table's walk or two a call
        self.word(O['MCL_POS'],1)  # B of ABC plays
        self.file_mocks('r:',{'/mnt/data/ringnav-ratings':saved} if saved else {}); self.fav_songs=[]
        for n in ('getAllFav','fseek@GLIBC_2.0','ftell@GLIBC_2.0','taglib_file_new','taglib_file_audioproperties','taglib_file_free'):
            self.handlers[syms[n]]='r:'+n
        self.lengths={}; self.probed=[]; duration_syscalls(self); self.handlers[0x1000030]='r:length'  # AudioProperties::length(), vtable +0xc
        self.handlers[FAV_CLICK]='r:fav_click'
        self.fav=self.node('image','img_fav'); self.word(self.fav+O['W_X'],303); self.word(self.fav+O['W_Y'],176)
        self.word(self.fav+O['W_W'],50); self.word(self.fav+O['W_H'],50); self.nodes[self.fav]['image']='play_unfav'
        self.np=self.node('window','playing_page',[self.node('view','view_album',[self.fav])])
        self.handlers[syms['navigator_to']]='q:navigator_to'  # the menu sheet's dialog, the picker's and the hold menu's
        self.word(self.np+O['W_W'],375); self.word(self.np+O['W_H'],320); self.word(self.np+O['W_PARENT'],self.wm)
        for w in self.nodes[self.np]['children']: self.word(self.fav+O['W_PARENT'],w); self.word(w+O['W_PARENT'],self.np)
        em=self.alloc(4); it=self.alloc(0x28); self.word(self.fav+O['W_EMITTER'],em); self.word(em,it)
        self.word(it+O['EMIT_TYPE'],O['EVT_CLICK']); self.word(it+12,FAV_CLICK); self.word(it,self.np)
        playing=HOOKS['playing_page_init'][0]; self.handlers[playing+12]='stock_playing'
        self.top=self.np; self.stack=[self.np]
        assert self.call(address=playing,args=(self.np,0,0,0),gap=0)==0
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('r:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; ret=0; self.calls.append((name,a,b,c))
        if name in ('fopen','fread','fwrite','fclose','rename'): ret=self.file_op(u,name,a,b,c,d)
        elif name=='fseek': ret=0
        elif name=='ftell': ret=len(self.files[self.open_files[a][0]])
        elif name=='taglib_file_new':
            assert b==1  # Long Songs requires audio properties.
            path=self.text(a); self.probed.append(path)
            if path in self.lengths: ret=self.alloc(8); props=self.alloc(8); vt=self.alloc(16); self.word(props,vt); self.word(vt+12,0x1000030); self.word(ret,props); self.word(props+4,self.lengths[path])
        elif name=='taglib_file_audioproperties': ret=self.get(a)
        elif name=='length': ret=self.get(a+4)
        elif name=='pick': self.word(O['MCL_POS'],self.seq.pop(0)); ret=1
        elif name=='getAllFav':
            self.deqs[self.get(syms['tools_pdeq_directory'])][1]=[self.copy('stSongInfo',e) for e in self.fav_songs]; ret=len(self.fav_songs)
        elif name=='fav_click':  # stock: My Fav toggled for the playing track, and the heart's image
            assert a==self.np; t=self.names()[self.get(O['MCL_POS'])]; self.favs^={t}
            self.nodes[self.fav]['image']='play_fav' if t in self.favs else 'play_unfav'
        self.returns(ret,clobber=True)
    def kept(self):
        data=self.files.get('/mnt/data/ringnav-ratings',b'')
        return sorted(v for v in struct.unpack(f'<{len(data)//4}I',data) if 0<v&7<6)  # 7: one taken out
    def alone(self):  # the heart is the favourite alone: nothing added beside it (the rating page has the stars)
        return self.nodes[self.get(self.fav+O['W_PARENT'])]['children']==[self.fav]
    def tap(self):  # the heart's click
        f,ctx=self.handler(self.fav,O['EVT_CLICK']); return self.call(address=f,args=(ctx,self.event,0,0),gap=0)
    def held(self,ms=1000):  # a press held ms on the heart, then its click
        self.touch(); self.pressed=1; self.advance(ms,clear=False); self.pressed=0; return self.tap()
    def rate(self,n):  # n stars from the rating page (np_rate.c nr_keep): 1 kept
        return self.call(address=payload_syms['rate_now_set'],args=(n,0,0,0),gap=0)
def key_of(name): k=fnv('/p/'+name); return k|(k==0)
m=RateMachine()
# Bound: stock's click off img_fav, the payload's click on and nothing else (no hold), no badge beside it.
assert ('widget_off_by_func',m.fav,O['EVT_CLICK'],FAV_CLICK) in m.calls and m.alone()
assert [t for t,f,ctx in m.nodes[m.fav]['handlers']]==[O['EVT_CLICK']]; passed()
# A tap is stock's heart: B (playing) into My Fav, nothing kept; again, out. Its click returns 0, as stock's:
# a STOP would skip the slide_view's own release and leave its grab on the window.
assert m.tap()==0 and m.favs=={'B'} and m.nodes[m.fav]['image']=='play_fav' and m.kept()==[]
assert m.tap()==0 and m.favs==set(); passed()
# A press held a second is still the tap: no sheet, no picker.
m.calls=[]; assert m.held()==0 and m.favs=={'B'} and not [c for c in m.calls if c[0]=='navigator_to']; passed()
# Stars are kept apart from the like: five stars leave B liked, none leave it liked; a tap takes the like away and
# keeps the stars. Each makes the card's copy due.
rc_due=payload_syms['rc_due']; m.word(rc_due,0)
assert m.rate(5)==1 and m.favs=={'B'} and m.kept()==[(key_of('B')&~7)|5] and m.get(rc_due)==1
m.word(rc_due,0); assert m.tap()==0 and m.favs==set() and m.kept()==[(key_of('B')&~7)|5] and m.get(rc_due)==1
assert m.rate(0)==1 and m.kept()==[] and m.favs==set()
assert m.rate(2)==1 and m.alone(); passed()
# Kept across boots, the heart's UI tick adding nothing beside it.
m2=RateMachine(m.files['/mnt/data/ringnav-ratings']); assert m2.kept()==[(key_of('B')&~7)|2]
m2.word(O['MCL_POS'],0); m2.advance(500); assert m2.alone(); passed()
# Hold Play/Pause on Now Playing: no Rate row (the rating page is the one place to rate), and no Next/Previous album
# outside Shuffle Albums/Folders.
m2.word(O['MCL_POS'],1); m2.press(100)
assert m2.hold()==11 and m2.labels()[-1]=='Group by' and not [l for l in m2.labels() if l.startswith('Rate')] and m2.release()==0; passed()
# More…: last, while Now Playing has its img_more; its row clicks that as a tap would
m3=RateMachine(); more=m3.node('image','img_more'); m3.nodes[m3.np]['children'].append(more); m3.word(more+O['W_PARENT'],m3.np)
m3.press(100)
assert m3.hold()==11 and m3.labels()[-2:]==['Group by','More…'] and m3.release()==0
m3.pick(m3.labels()[-1]); m3.advance(0)
assert [m3.get(c[2]) for c in m3.dispatched() if c[1]==more]==[O['EVT_CLICK']]; passed()
# Ratings: Top Rated (4-5 stars), Loved (the liked songs, My Fav), All Rated and Disliked (1), most stars first, then
# title order. Each row's stars are OLED's glyphs, painted before its detail (ratings.c rate_row: the count in the
# row's "_stars", the detail moved RATE_ROOM, 5 x 14 px glyphs 2 px apart and a 6 px space, to the right); a liked
# song with no stars has none, its detail where it was.
m=RateMachine(b''.join(struct.pack('<I',(fnv(path)&~7)|h) for path,h in (('/p/A',1),('/p/B',5),('/mnt/mmc/Audiobooks/C.m4b',4),('/mnt/mmc/Podcasts/E.mp3',2))))
m.found=[m.song(n) for n in ('A','B','C','D','E')]; m.fav_songs=[m.song('D')]
m.word(m.found[2]+O['REC_PATH'],m.string('/mnt/mmc/Audiobooks/C.m4b'))
m.word(m.found[4]+O['REC_PATH'],m.string('/mnt/mmc/Podcasts/E.mp3'))
m.top=m.node('window','localmusic_page'); view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)])
m.nodes[m.top]['children']=[view]; m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)
def lib_row(m,view,text):  # the Library's added row by its words (home.c ringnav_localmusic orders them by lr_default)
    return next(k for k in m.nodes[view]['children'] if m.nodes[k]['children'] and any(m.nodes[c].get('text')==text for c in m.nodes[m.nodes[k]['children'][0]]['children']))
f,ctx=m.handler(m.nodes[lib_row(m,view,'Ratings')]['children'][0],O['EVT_CLICK'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0; menu=m.top; m.page=menu
assert m.nodes[menu]['name']=='ratings_page' and m.texts()==['Ratings','Top Rated','Loved','Albums','All Rated','Disliked']
want={0:['B','C'],1:['D'],2:['B','C','E','A'],3:['A']}
stars={'B':5,'C':4,'E':2,'A':1}
for i,names in want.items():
    row=m.nodes[m.find('scroll_view',menu)]['children'][i+(i>=2)]; g,c=m.handler(row,O['EVT_CLICK'])  # Albums aside
    assert m.call(address=g,args=(c,m.event,0,0),gap=0,count=50_000_000)==0; m.page=m.top
    assert m.nodes[m.page]['name']=='rated_page'
    assert [c for c in m.calls if c[0]=='getAllMusic'][-1][1:]==(0,2)  # LIBRARY_ALL (navigation.h): every card song, and the server's
    assert m.texts()==[('Top Rated','Loved','All Rated','Disliked')[i]]+[x for n in names for x in (n,'· Artist')],m.texts()
    rrows=m.nodes[m.find('scroll_view')]['children']
    assert [m.nodes[r].get('_stars') for r in rrows]==[stars.get(n) for n in names]
    assert [m.get(m.nodes[r]['children'][1]+O['W_X']) for r in rrows]==[OLT['page_x']+(5*14+4*2+6 if n in stars else 0) for n in names]
    if i==2:  # a row plays the list from there, as a library queue
        r=m.nodes[m.find('scroll_view')]['children'][1]; g,c=m.handler(r,O['EVT_CLICK'])
        assert m.call(address=g,args=(c,m.event,0,0),gap=0)==0 and m.queued==names and m.plays[-1][2:]==(1,0xf001,2)
    m.close(); m.page=menu
m.found=[m.song('Z')]; m.fav_songs=[]; m.calls=[]; row=m.nodes[m.find('scroll_view',menu)]['children'][0]; g,c=m.handler(row,O['EVT_CLICK'])
m.call(address=g,args=(c,m.event,0,0),gap=0); m.page=m.top
assert m.texts()==['Top Rated'] and empty_titles(m)==['No Rated Songs Yet']; passed()  # the name hidden for the status row

# Long Songs: over LONG_SONG_S, longest first, whatever Scan Song Durations is. Each file is statted on a worker and
# one whose signature the cache lacks probed once by TagLib (a CUE track with both ends among them: the file's length
# serves its other tracks), kept by path key in the shared duration cache; 0 for an unreadable file.
cached=duration_cache(('/p/E',1200))
m=RateMachine(); m.files[DUR_CACHE]=cached
m.found=[m.song(n) for n in 'ABCDE']; m.word(m.found[2]+O['REC_CUE_END'],900)
m.lengths={'/p/A':700,'/p/B':300}
m.top=m.node('window','localmusic_page'); view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)])
m.nodes[m.top]['children']=[view]; m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)
f,ctx=m.handler(m.nodes[lib_row(m,view,'Long Songs')]['children'][0],O['EVT_CLICK'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0; m.page=m.top
assert m.nodes[m.page]['name']=='longsongs_page' and m.texts()==['Measuring songs…','Cancel'] and len(m.threads)==1
worker,arg=m.threads[0]; assert m.call(address=worker,args=(arg,0,0,0),gap=0)==0 and sorted(m.probed)==['/p/A','/p/B','/p/C','/p/D']
m.advance(250)
want=['Long Songs','E','Artist · 20:00','C','Artist · 15:00','A','Artist · 11:40']
assert m.texts()==want,m.texts()
saved=duration_pairs(m.files[DUR_CACHE],['/p/'+n for n in 'ABCDE']); assert saved=={'/p/A':700,'/p/B':300,'/p/C':0,'/p/D':0,'/p/E':1200}
r=m.nodes[m.find('scroll_view')]['children'][2]; g,c=m.handler(r,O['EVT_CLICK'])
assert m.call(address=g,args=(c,m.event,0,0),gap=0)==0 and m.queued==['E','C','A']; passed()
# Again: a whole pass covered this library, so no worker: the list at once. Cancel keeps what was measured.
m.close(); m.probed=[]; m.threads=[]; m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.page=m.top
assert not m.threads and not m.probed and m.texts()==want; m.close(); passed()
m=RateMachine(); m.found=[m.song(n) for n in 'AB']; m.lengths={'/p/A':700,'/p/B':900}
m.top=m.node('window','localmusic_page'); view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)])
m.nodes[m.top]['children']=[view]; m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)
f,ctx=m.handler(m.nodes[lib_row(m,view,'Long Songs')]['children'][0],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.page=m.top
cancel=m.nodes[m.find('scroll_view')]['children'][0]; g,c=m.handler(cancel,O['EVT_CLICK']); m.call(address=g,args=(c,m.event,0,0),gap=0)
worker,arg=m.threads[0]; m.call(address=worker,args=(arg,0,0,0),gap=0); assert m.probed==[]  # cancelled before the first
m.advance(250); assert not [m.nodes[w]['text'] for w in m.nodes if m.nodes[w]['type']=='hscroll_label' and m.alive(w) and m.nodes[w]['visible']] and empty_titles(m)==['No Long Songs'] and m.joins==[77]; passed()  # empty_list: the empty state, the name hidden for the status row
# Two CUE tracks to the end of one file probe it once and keep one cache entry.
m=RateMachine(); m.found=[m.song('A'),m.song('A')]; m.lengths={'/p/A':1300}; m.word(m.found[1]+O['REC_CUE_START'],600)
m.top=m.node('window','localmusic_page'); view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)])
m.nodes[m.top]['children']=[view]; m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)
f,ctx=m.handler(m.nodes[lib_row(m,view,'Long Songs')]['children'][0],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0); m.page=m.top
worker,arg=m.threads[0]; m.call(address=worker,args=(arg,0,0,0),gap=0); m.advance(250)
assert duration_pairs(m.files[DUR_CACHE],['/p/A'])=={'/p/A':1300} and len(m.files[DUR_CACHE])==8+24 and m.probed==['/p/A']
assert m.texts()==['Long Songs','A','Artist · 21:40','A','Artist · 11:40']; passed()

# Upload Scrobbles: the second-last row, above Update Local Music, only with an account in the card's .scrobble.ini (a
# ListenBrainz token or all four Last.fm keys). Without Wi-Fi it only says so; otherwise scrobble.c's thread
# starts, a second press finds it running, and a timer reports the result once the thread ends.
m=ShuffleMachine(); m.config.update(USER='u',PASSWORD='p',API_KEY='k')  # no API_SECRET: no row
view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)]); m.top=m.node('window','localmusic_page',[view])
assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)==0 and len(m.nodes[view]['children'])==16  # no Search or Games row (2026-11-19)
assert ('/mnt/mmc/.scrobble.ini','LASTFM','API_SECRET','') in m.config_reads
m.config['TOKEN']='tok'; m.nodes[view]['children']=[m.node('list_item') for _ in range(11)]
assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)==0 and len(m.nodes[view]['children'])==17
button=m.nodes[m.nodes[view]['children'][-2]]['children'][0]; icon,label=m.nodes[button]['children']
assert m.nodes[icon]['image']=='local_scrobble' and m.nodes[label]['text']=='Upload Scrobbles' and not m.nodes[button].get('name')
library_geometry(m,m.nodes[view]['children'][-2])
f,ctx=m.handler(button,O['EVT_CLICK'])
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.toasts[-1][3]=='Connect to Wi-Fi first' and not m.threads
m.wifi=3; assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.toasts[-1][3]=='Uploading scrobbles' and len(m.threads)==1
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.toasts[-1][3]=='Already uploading' and len(m.threads)==1
m.advance(2000); assert m.toasts[-1][3]=='Already uploading' and not m.joins  # still uploading
worker,arg=m.threads[0]; assert m.call(address=worker,args=(arg,0,0,0),gap=0)==0  # no log on the card
m.advance(600); assert m.toasts[-1][3]=='Nothing to upload' and m.joins==[77] and not m.timers; passed()
# [SCROBBLE] AUTO=1 (scrobble_auto): the oldest unsent listen waits 3 h, 4 min less per listen, never under 1 h.
auto=lambda n: m.call(address=payload_syms['scrobble_auto_ms'],args=(n,0,0,0),gap=0)
assert [auto(n)//60000 for n in (0,1,10,30,31,1000)]==[180,176,140,60,60,60]; passed()

# Podcasts and Audiobooks: a row each, above upkeep, only for the card's top-level folder of that name (case
# aside, a directory). A press opens folder_page there as a deeper folder (layer 3, stock reload, title
# and rebuild); Back there leaves the page (layer 1 before stock's step down), and only for that root.
m=ShuffleMachine(); m.card=[('Music',4),('PODCASTS',4),('Audiobooks',8)]
m.handlers[int(manifest['patch_symbols']['stock_folder_trampoline'],16)]='stock_folder'
m.handlers[int(manifest['patch_symbols']['stock_folder_back_trampoline'],16)]='stock_folder_back'
view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)]); m.top=m.node('window','localmusic_page',[view])
assert m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)==0 and len(m.nodes[view]['children'])==17
button=m.nodes[m.nodes[view]['children'][-2]]['children'][0]; icon,label,into=m.nodes[button]['children']  # before Update
assert m.nodes[icon]['image']=='local_podcasts' and m.nodes[label]['text']=='Podcasts' and not m.nodes[button].get('name')
f,ctx=m.handler(button,O['EVT_CLICK']); m.calls=[]
assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and [m.text(c[1]) for c in m.calls if c[0]=='navigator_to']==['folder_page']
win=m.node('window','folder_page'); m.calls=[]
assert m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)==0
assert m.text(syms['g_folder_path'])=='/mnt/mmc/PODCASTS' and m.u.mem_read(syms['g_folder_layer'],1)[0]==3
assert [c[0] for c in m.calls]==['stock_folder','folder_reset_variable','strlen@GLIBC_2.0','memcpy','folder_reload_data','folder_reinit_navbarname','folder_refresh'] and m.calls[-1][1]==win
assert m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)==0 and m.calls[-1][0]=='stock_folder_back'
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==1; passed()
m.byte(syms['g_folder_layer'],3); assert m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)==0  # the Folder view
m.folder('/mnt/mmc/PODCASTS'); m.byte(syms['g_folder_layer'],3); m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==3; passed()

# Audiobooks uses the same shortcut path, even with OTG connected; a nested Back stays in Folder.
m=ShuffleMachine(); m.card=[('Audiobooks',4)]; m.byte(syms['g_otg_status'],1)
m.handlers[int(manifest['patch_symbols']['stock_folder_trampoline'],16)]='stock_folder'
m.handlers[int(manifest['patch_symbols']['stock_folder_back_trampoline'],16)]='stock_folder_back'
view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)])
m.top=m.node('window','localmusic_page',[view])
m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)
button=m.nodes[m.nodes[view]['children'][-2]]['children'][0]
f,ctx=m.handler(button,O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
win=m.node('window','folder_page'); m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)
assert m.text(syms['g_folder_path'])=='/mnt/mmc/Audiobooks' and m.u.mem_read(syms['g_folder_layer'],1)[0]==3
m.folder('/mnt/mmc/Audiobooks/Author'); m.byte(syms['g_folder_layer'],4)
m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==4
m.folder('/mnt/mmc/Audiobooks'); m.byte(syms['g_folder_layer'],3)
m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==1; passed()

# Folder visits bypass the storage selector only when OTG was absent and the card present on entry
# (stock's check_tfstatus: /tmp/mmc_add).
for otg,card in ((0,1),(1,1),(0,0)):
    m=Machine()
    if card: m.present.add('/tmp/mmc_add')
    m.handlers[int(manifest['patch_symbols']['stock_folder_trampoline'],16)]='stock_folder'
    m.handlers[int(manifest['patch_symbols']['stock_folder_back_trampoline'],16)]='stock_folder_back'
    win=m.node('window','folder_page'); m.byte(syms['g_otg_status'],otg); m.byte(syms['g_folder_layer'],1)
    assert m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)==0
    direct=card and not otg; calls=[c[0] for c in m.calls if not c[0].startswith('access')]
    assert m.u.mem_read(syms['g_folder_layer'],1)[0]==(2 if direct else 1)
    if direct:
        assert m.text(syms['g_folder_path'])=='/mnt/mmc'
        assert calls==['stock_folder','memcpy','toolsReadConfig','tk_snprintf','folder_reset_variable','strlen@GLIBC_2.0','memcpy',
                       'folder_reload_data','folder_reinit_navbarname','folder_refresh'],calls
    else: assert calls==['stock_folder','toolsReadConfig','tk_snprintf'][:1+2*card],calls
    # Nested Back stays stock. Plugging/unplugging OTG mid-visit does not change root Back.
    m.folder('/mnt/mmc/Music'); m.byte(syms['g_folder_layer'],3)
    m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
    assert m.u.mem_read(syms['g_folder_layer'],1)[0]==3
    m.folder('/mnt/mmc'); m.byte(syms['g_folder_layer'],2); m.byte(syms['g_otg_status'],not otg)
    m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
    assert m.u.mem_read(syms['g_folder_layer'],1)[0]==(1 if direct else 2)
    passed()
# A direct visit left without Back (as by Home), then a selector visit with OTG: the old root is gone, so
# Back at the card's root on the selector stays stock.
m=Machine(); m.present.add('/tmp/mmc_add')
m.handlers[int(manifest['patch_symbols']['stock_folder_trampoline'],16)]='stock_folder'
m.handlers[int(manifest['patch_symbols']['stock_folder_back_trampoline'],16)]='stock_folder_back'
win=m.node('window','folder_page'); m.byte(syms['g_otg_status'],0); m.byte(syms['g_folder_layer'],1)
m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)
assert m.text(syms['g_folder_path'])=='/mnt/mmc' and m.u.mem_read(syms['g_folder_layer'],1)[0]==2
m.byte(syms['g_otg_status'],1); m.byte(syms['g_folder_layer'],1)
m.call(address=HOOKS['folder_page_init'][0],args=(win,0,0,0),gap=0)
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==1
m.folder('/mnt/mmc'); m.byte(syms['g_folder_layer'],2)
m.call(address=HOOKS['folder_back'][0],args=(0,0,0,0),gap=0)
assert m.u.mem_read(syms['g_folder_layer'],1)[0]==2; passed()

# Folder Root (#49): with a card, Folder opens in the saved top-level folder (Q2POD FOLDERROOT) as a
# layer-3 folder, a fresh visit (stock folder_reset_variable, run here: no enter history, no M3U).
# Stock folder_back, run here, walks up to the card at layer 2 with stock's title; its history being
# empty, the position stays the caller's (the list's top). Back there leaves without a drive and
# reaches the storage selector with one. A missing, unsafe or file root opens as before.
class RootMachine(ShuffleMachine):
    def __init__(self,root=None,otg=0,card=(('Music',4),('Podcasts',4),('notes.txt',8))):
        super().__init__(); self.card=list(card); self.byte(syms['g_otg_status'],otg)  # access finds /tmp/mmc_add
        if root is not None: self.config['FOLDERROOT']=root
        self.stacks={}
        for g in ('g_yoffset_save','g_index_save'): h=self.alloc(4); self.word(syms[g],h); self.stacks[h]=[]
        for n in ('stack_empty','stack_top','stack_pop','strcpy@GLIBC_2.0'): self.handlers[syms[n]]='r:'+n
        self.handlers.pop(syms['folder_reset_variable']); self.mock('strstr@GLIBC_2.0')
        for n in ('folder','folder_top'): self.handlers[int(manifest['patch_symbols'][f'stock_{n}_trampoline'],16)]='stock_'+n  # Back: stock's own
        self.win=self.node('window','folder_page'); self.byte(syms['g_folder_layer'],1)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('r:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b=u.reg_read(REGS[0]),u.reg_read(REGS[1]); ret=0; self.calls.append((name,a,b))
        if name=='stack_empty': ret=int(not self.stacks[a])
        elif name=='stack_top': ret=self.stacks[a][-1]
        elif name=='stack_pop': self.stacks[a].pop()
        elif name=='strcpy': self.u.mem_write(a,self.text(b).encode()+b'\0'); ret=a
        self.returns(ret,clobber=True)
    def push(self,yoffset,index):  # an enter's history entry, as folder_enter's stack_push
        for g,v in (('g_yoffset_save',yoffset),('g_index_save',index)):
            e=self.alloc(4); self.word(e,v); self.stacks[self.get(syms[g])].append(e)
    def open(self):
        self.calls=[]; assert self.call(address=HOOKS['folder_page_init'][0],args=(self.win,0,0,0),gap=0)==0
        return self.text(syms['g_folder_path']),self.u.mem_read(syms['g_folder_layer'],1)[0]
    def back(self):  # the folder page's Back handler zeroes both before stock fills them from the history
        out=self.alloc(8); self.word(out,7); self.word(out+4,9)
        ret=self.call(address=HOOKS['folder_back'][0],args=(out,out+4,0,0),gap=0)
        return ret,self.text(syms['g_folder_path']),self.u.mem_read(syms['g_folder_layer'],1)[0],(self.get(out),self.get(out+4))
for otg in (0,1):
    m=RootMachine('Music',otg=otg); m.push(5,6); m.u.mem_write(syms['g_m3u_path'],b'/mnt/mmc/Music/list.m3u\0')  # a visit left deep
    assert m.open()==('/mnt/mmc/Music',3) and not any(m.stacks.values()) and m.text(syms['g_m3u_path'])==''
    calls=[c[0] for c in m.calls if c[0] in ('stock_folder','folder_reload_data','folder_reinit_navbarname','folder_refresh')]
    assert calls==['stock_folder','folder_reload_data','folder_reinit_navbarname','folder_refresh'] and m.calls[-1][1]==m.win
    m.calls=[]; assert m.back()==(1,'/mnt/mmc',2,(7,9)) and m.text(syms['g_navbar_name'])=='SD'
    assert [c[1] for c in m.calls if c[0]=='toolsLoadDirectory']==[syms['g_folder_path']]
    assert m.back()[::2]==((1,1) if otg else (0,0)); passed()  # the selector, or out of the page
# Gone, a file, hidden, a path, or no card: as before Folder Root (the card at layer 2, or stock's page).
for root in ('Gone','notes.txt','.Trash','Music/..',''):
    assert RootMachine(root).open()==('/mnt/mmc',2),root
for m in (RootMachine('Gone',otg=1),RootMachine('Music')):
    m.missing=m.config['FOLDERROOT']=='Music'  # no card: CoverflowMachine's access finds nothing
    assert m.open()[1]==1 and 'folder_reload_data' not in [c[0] for c in m.calls]
passed()
# Podcasts keeps its own root, Back there leaving the page, whatever Folder Root says.
m=RootMachine('Music'); view=m.node('scroll_view','scroll_view_localmusic',[m.node('list_item') for _ in range(11)])
m.top=m.node('window','localmusic_page',[view]); m.call(address=HOOKS['localmusic_page_init'][0],args=(m.top,5,0,0),gap=0)
f,ctx=m.handler(m.nodes[m.nodes[view]['children'][-2]]['children'][0],O['EVT_CLICK']); m.call(address=f,args=(ctx,m.event,0,0),gap=0)
assert m.open()==('/mnt/mmc/Podcasts',3) and m.back()[::2]==(0,0); passed()

# Go to Folder: a card song's hold menu row, after Go to Artist, opens folder_page in the song's folder as a
# fresh visit at the layer stock's enters would have reached (the card 2, each folder one more), so stock
# folder_back, run here, climbs a folder at a time to the card and out of the page, or to the storage
# selector with a drive. The path is copied as it is: percent signs, format directives and UTF-8 stay literal.
class GoMachine(RootMachine):
    def __init__(self,path,dirs=(),otg=0):
        super().__init__(otg=otg); self.handlers[syms['navigator_to']]='q:navigator_to'; self.dirs=set(dirs)
        self.word(self.row(0)+O['REC_PATH'],self.string(path))
    def hook(self,u,address,size,unused):
        folder=address==syms['navigator_to'] and self.text(u.reg_read(REGS[0]))=='folder_page'
        super().hook(u,address,size,unused)
        if folder: self.win=self.top; self.nested(HOOKS['folder_page_init'][0],self.win,0)  # stock's init is synchronous
    def menu(self): self.press(5); assert self.hold()==11; got=self.nodes[self.title]['text'],self.labels(); self.release(); return got
    def go(self): self.calls=[]; self.pick('Go to Folder'); self.advance(0)  # from the open menu
GO=[*SONG_MENU,'Go to Folder']
DEEP='/mnt/mmc/Music/100% %s%n/Ünï cødé'
for otg in (0,1):
    m=GoMachine(DEEP+'/song.flac',['/mnt/mmc/Music/100% %s%n',DEEP],otg=otg); m.push(5,6)  # a visit left deep
    assert m.menu()==('Row 0',GO); m.go(); assert m.opened[-1][0]=='folder_page'
    assert (m.text(syms['g_folder_path']),m.u.mem_read(syms['g_folder_layer'],1)[0])==(DEEP,5) and not any(m.stacks.values())
    for want,layer in (('/mnt/mmc/Music/100% %s%n',4),('/mnt/mmc/Music',3),('/mnt/mmc',2)): assert RootMachine.back(m)[:3]==(1,want,layer)
    assert RootMachine.back(m)[::2]==((1,1) if otg else (0,0)); passed()  # the selector, or out of the page
# A song at the card's root opens the card itself.
m=GoMachine('/mnt/mmc/x.mp3',['/mnt/mmc']); assert m.menu()[1]==GO; m.go()
assert (m.text(syms['g_folder_path']),m.u.mem_read(syms['g_folder_layer'],1)[0])==('/mnt/mmc',2) and RootMachine.back(m)[::2]==(0,0); passed()
# No row off the card, for a path stock's enters never build, a gone folder, one too long for g_folder_path
# (1023 bytes fit), or while a folder_page is open.
for path,dirs,row in (('/mnt/mmcx/a.mp3',['/mnt/mmcx'],0),('/mnt/mmc/A/../a.mp3',['/mnt/mmc/A/..'],0),
        ('/mnt/mmc/./a.mp3',['/mnt/mmc/.'],0),('/mnt/mmc/A//a.mp3',['/mnt/mmc/A/'],0),('/mnt/mmc/Gone/a.mp3',[],0),
        ('/mnt/mmc/'+'d'*1015+'/a.mp3',['/mnt/mmc/'+'d'*1015],0),('/mnt/mmc/'+'d'*1014+'/a.mp3',['/mnt/mmc/'+'d'*1014],1),
        ('/mnt/mmc/..a/.b/a.mp3',['/mnt/mmc/..a/.b'],1)):
    assert GoMachine(path,dirs).menu()[1]==(GO if row else SONG_MENU),path; passed()
m=GoMachine(DEEP+'/a.mp3',[DEEP]); m.open_pages.add('folder_page'); assert m.menu()[1]==SONG_MENU; passed()
# Gone between the menu and the pick: a toast, no page, and the next Folder visit opens as before.
m=GoMachine(DEEP+'/a.mp3',[DEEP]); m.menu(); m.dirs.clear(); m.go()
assert m.toasts[-1][3]=='Folder not found' and m.opened[-1][0]!='folder_page' and m.open()==('/mnt/mmc',2); passed()
# The card gone as the page opens: stock's page, the pending folder dropped.
m=GoMachine(DEEP+'/a.mp3',[DEEP]); m.menu(); m.missing=True; m.go()
assert m.opened[-1][0]=='folder_page' and m.u.mem_read(syms['g_folder_layer'],1)[0]==1; m.missing=False
assert m.open()==('/mnt/mmc',2); passed()
# Now Playing: the playing song's row sits after Go to Artist, before the play modes.
m=playing_menu(); m.dirs.add('/mnt/mmc/Music'); m.word(m.items(m.get(syms['mcl_pdeqplaylist']))[1]+O['REC_PATH'],m.string('/mnt/mmc/Music/B'))
m.press(6000); assert m.hold()==11 and m.labels()==NP_SONG[:2]+['View Queue']+NP_SONG[2:]+['Go to Folder','Shuffle','Repeat','Group by']
passed()

# thumb (coverflow.c), Cover Flow's, the playlists' and Spotify's: the card's files by path and stock's
# thumbnailer, whose output it notes; ffmpeg's fork is refused (-1) or, with ff_pid 99, fails by itself.
JFIF=b'\xff\xe0'+struct.pack('>H',16)+b'JFIF\0'+bytes(9)
R='/mnt/mmc/pictures'
class ThumbMachine(DepthMachine):
    def __init__(self,data):
        super().__init__()
        self.data=data; self.thumbs=[]; self.forks=0; self.ff_pid=-1; self.open_files={}
        for n in ('fopen@GLIBC_2.2','fread@GLIBC_2.0','fseek@GLIBC_2.0','fclose@GLIBC_2.2','access@GLIBC_2.0','memcmp@GLIBC_2.0',
                  'toolsThumbSpecCover','fork@GLIBC_2.0','waitpid@GLIBC_2.0','pthread_mutex_lock@GLIBC_2.0',
                  'pthread_mutex_unlock@GLIBC_2.0'):
            self.handlers[syms[n]]='p:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        a,b,c,d=[u.reg_read(r) for r in REGS]
        if not name.startswith('p:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; ret=0
        if name=='fopen':
            if self.text(a) in self.data: ret=self.alloc(4); self.open_files[ret]=[self.data[self.text(a)],0]  # bytes, position
        elif name=='fread':
            f=self.open_files.get(d,[b'',0]); chunk=f[0][f[1]:f[1]+b*c]; f[1]+=len(chunk); self.u.mem_write(a,chunk); ret=len(chunk)//b
        elif name=='fseek': assert c==0; self.open_files[a][1]=b  # thumb's header read: SEEK_SET only
        elif name=='access': ret=0 if self.text(a) in self.data else -1
        elif name=='memcmp': ret=0 if bytes(self.u.mem_read(a,c))==bytes(self.u.mem_read(b,c)) else 1
        elif name=='toolsThumbSpecCover': self.thumbs.append((self.text(a),self.text(b),c,d)); ret=1
        elif name=='fork': self.forks+=1; ret=self.ff_pid
        elif name=='waitpid': self.u.mem_write(b,struct.pack('<i',1<<8)); ret=a  # exit 1: the picture's fault
        self.calls.append((name,a,b,c))
        self.returns(ret,clobber=True)
# thumb reads the header first: a PNG over 1024 px, or a progressive JPEG over 1024 px, goes to ffmpeg alone, as
# stock would decode it whole in demo (#216); a smaller one, or a baseline JPEG of any size, stays with stock.
def png_head(w,h): return b'\x89PNG\r\n\x1a\n'+struct.pack('>I4sII',13,b'IHDR',w,h)+bytes(9)
def frame_jpeg(sof,w,h): return b'\xff\xd8'+JFIF+b'\xff'+bytes([sof])+struct.pack('>HBHHB',17,8,h,w,3)+bytes(9)+b'\xff\xda'+bytes(12)
heads={R+'/a.png':(png_head(4000,4000),1),R+'/b.png':(png_head(1024,1024),0),R+'/c.jpg':(frame_jpeg(0xc2,3000,3000),1),
       R+'/d.jpg':(frame_jpeg(0xc2,1024,1024),0),R+'/e.jpg':(frame_jpeg(0xc0,4000,4000),0)}
thumb_m=ThumbMachine({p:d for p,(d,_) in heads.items()})
for p,(_,ff) in heads.items():
    thumb_m.thumbs=[]; thumb_m.forks=0  # ffmpeg's fork refused, so thumb answers 0
    assert thumb_m.call(address=ps['thumb'],args=(thumb_m.string(p),thumb_m.string('/tmp/x.jpg'),160,160),gap=0,count=5_000_000)==1-ff,p
    assert (len(thumb_m.thumbs),thumb_m.forks)==(1-ff,ff),p
passed()

TEXT_PITCH=9
