# General's Rockbox row (docs/boot.md#rockbox-from-home): its click saves the queue as power-off does,
# stops the player, leaves S90play's flag and kills demo; without Rockbox on the card it says so.
class RockboxMachine(Machine):
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('r:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a=u.reg_read(UC_MIPS_REG_A0); self.calls.append((name,a))
        self.returns(0x2000000 if name=='fopen' else 0)
def rockbox(card):
    m=RockboxMachine(); m.rockbox_mode=0o100755 if card else 0
    for n in ('fopen@GLIBC_2.2','fclose@GLIBC_2.2','system@GLIBC_2.0'): m.handlers[syms[n]]='r:'+n
    m.mock('save_memoryplay_info','player_stop','switch_charge_enable','navigator_to_with_context')
    assert m.call(address=payload_syms['rockbox_open'],args=(0,m.event,0,0),gap=0)==0
    return m,[c[0] for c in m.calls]
m,names=rockbox(True)
texts={c[0]:m.text(c[1]) for c in m.calls if c[0] in ('fopen','system')}
assert texts=={'fopen':'/tmp/q2pod-rockbox',
               'system':'killall -9 hciplayer; kill -9 $(cat /tmp/q2-librespot); '
                         'killall -9 librespot aplay; rm -f /tmp/q2-librespot /tmp/q2-librespot.state /tmp/q2-librespot.out /tmp/q2-librespot.download; sync; kill -9 $PPID'}
assert [n for n in names if n in ('fclose','save_memoryplay_info','player_stop','system')]==['fclose','save_memoryplay_info','player_stop','system']; passed()
m,names=rockbox(False)
assert not {'fopen','player_stop','system'} & set(names)
assert [m.text(c[1]) for c in m.calls if c[0]=='navigator_to_with_context']==['dialog/msginfo_dialog']; passed()
# The wheel walks onto the new rows and Centre clicks them, as any fixed settings list.
m,view,_=display({},sub=0)
m.paint(view)
for _ in range(3): m.call()
assert m.selected(view)==3 and m.confirm()==11 and m.dispatched()[0][1]==m.nodes[m.nodes[view]['children'][4]]['children'][0]; passed()  # the fourth shown row, Night Shift: stock's Brightness row (children[1]) is hidden under the inline one
# PEQ's value edit has action_text "done": stock edit_on_event reports the keyboard's OK only for it (or "next").
stock=(B/'stock-demo').read_bytes()
def at(va,n): return next(stock[o+va-v:o+va-v+n] for _,(t,o,v,_,f,*_) in segments(stock) if t==1 and v<=va<v+f)
assert at(0x626d78,4)==bytes.fromhex('8c0aa524') and at(0x790a8c,5)==b'done\0'; passed()  # edit_on_event: strcmp(action_text, "done")

# Settings rows (docs/ipod.md#settings). The real stock builders create their rows; the build
# points the list_view layouter's vtable slot at ipod_list_layout, which normalises stock 78px
# items, runs the stock layout (stacking modelled here: item_height, else the item's own height,
# else default_item_height, as 0x5fd454 onward) and maps each row's children. Stock's
# list_view(m=0,s=0) has no keep_invisible, so widget_get_children_for_layout (0x5fd074) drops a
# hidden row: it is neither placed nor sized.
from ipod import corner_inset
class SettingsMachine(Machine):
    def hook(self,u,address,size,x):
        name=self.handlers.get(address,'')
        if name=='stock_list_layout':
            assert u.reg_read(UC_MIPS_REG_T9)==address
            view=u.reg_read(UC_MIPS_REG_A1); lst=self.get(view+O['W_PARENT']); y=0
            ih,dh=(self.get(lst+O[k]) for k in ('ROW_HEIGHT','LIST_DEFAULT_ITEM_HEIGHT'))
            for c in self.nodes[view]['children']:
                if not self.nodes[c]['visible']: continue
                if not self.get(c+O['W_W']): self.word(c+O['W_W'],self.get(view+O['W_W']))
                h=ih or self.get(c+O['W_H']) or dh
                self.word(c+O['W_Y'],y); self.word(c+O['W_H'],h); y+=h
            self.word(view+O['VIEW_CONTENT_H'],y); self.layouts+=1
            self.returns(0); return
        if name=='widget_on' and u.reg_read(UC_MIPS_REG_A1)==O['EVT_CLICK']:  # a clickable row
            a=u.reg_read(UC_MIPS_REG_A0); em=self.alloc(4); it=self.alloc(0x28)
            self.word(a+O['W_EMITTER'],em); self.word(em,it); self.word(it+O['EMIT_TYPE'],O['EVT_CLICK'])
            self.nodes[a]['click']=(u.reg_read(UC_MIPS_REG_A2),u.reg_read(UC_MIPS_REG_A3))
        if name=='image_set_draw_type': self.nodes[u.reg_read(UC_MIPS_REG_A0)]['draw_type']=u.reg_read(UC_MIPS_REG_A1)
        return super().hook(u,address,size,x)
SET={k:O['SET_'+k] for k in ('ROW','TOP','ROWS','ICON','ICON_X','GAP','TEXT_X','EDGE','STOCK_ROW')}
def geometry(m,w): return tuple(signed(m.get(w+O[k])) for k in ('W_X','W_Y','W_W','W_H'))
def settings(builder,default=SET['ROW'],sub=0,boot=None,flat=False):
    m=SettingsMachine(); flat and m.config.update(FLAT); m.layouts=0; boot and boot(m)
    m.mock('list_item_create','button_create','image_create','hscroll_label_create','label_create','widget_use_style',
           'widget_set_name','image_set_draw_type','image_base_set_image','set_hscroll_label_attribute','widget_on',
           'widget_set_text_utf8','widget_set_visible','widget_move_resize')
    m.handlers[syms['widget_destroy_children']]='widget_destroy_children'
    for n in syms:  # string helpers the builders format their names with
        if n.endswith('@GLIBC_2.0') and syms[n] not in m.handlers: m.handlers[syms[n]]=n
    m.handlers[O['LIST_VIEW_LAYOUT']]='stock_list_layout'
    view=m.node('scroll_view'); lst=m.node('list_view','list_view',[view]); m.top=m.node('window','display_page' if builder=='display' else 'sysset_page',[lst])
    m.word(view+O['W_PARENT'],lst); m.word(lst+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm)
    m.word(lst+O['ROW_HEIGHT'],0); m.word(lst+O['LIST_DEFAULT_ITEM_HEIGHT'],default)
    m.word(lst+O['W_Y'],SET['TOP']); m.word(lst+O['W_H'],SET['ROWS']*SET['ROW'])
    m.nodes[m.wm]={'type':'window_manager','children':[m.top]}
    m.word(view+O['W_W'],375); m.word(view+O['W_H'],SET['ROWS']*SET['ROW'])
    if builder=='display':  # the payload's Accent and Home rows, after three stock-shaped ones
        m.handlers[tramp['display']]='stock_display'; m.nodes[view]['name']='scroll_view_display'; m.word(payload_syms['display_sub'],sub)
        assert m.call(address=HOOKS['systemset_display_page_init'][0],args=(m.top,5,0,0),gap=0)==0
    else:
        for unused in range(2):  # learn the names it looks up, its scroll view's and its list view's: it stops at the first one missing
            m.call(address=builder,args=(m.top,0,0,0),gap=0)
            for c in m.calls:
                if c[0]=='widget_lookup': m.nodes[view if m.text(c[2]).startswith('scroll_view') else lst]['name']=m.text(c[2])
            m.calls.clear()
        assert m.call(address=builder,args=(m.top,0,0,0),gap=0)==0
    return m,view
def lay(m,view): return m.call(address=m.get(O['LIST_VIEW_LAYOUT_SLOT']),args=(0x1234,view,0,0),gap=0)
def tree(m,view):
    return [(geometry(m,i),[(geometry(m,b),[geometry(m,c) for c in m.nodes[b]['children']]) for b in m.nodes[i]['children']])
            for i in m.nodes[view]['children']]
# Stock's own lists under OLED's set take its settings rows, the same rule as any page of 78 px rows: Update Library's three, More's, each 32 px, its labels holding the
# title role's font, the selection on the row. More's speed row keeps 78 px: its four 42 x 61 buttons don't fit, and set_child would hide them as leading images. Its icon
# goes, its labels take the second-line role, in boxes that hold them, and its buttons stay stock's. A switch row's click stays stock's button's.
def stock_list(boot,default):
    m=SettingsMachine(); m.config.update(FLAT); m.layouts=0; boot(m); m.handlers[O['LIST_VIEW_LAYOUT']]='stock_list_layout'
    m.mock('widget_move_resize','image_set_draw_type')
    view=m.node('scroll_view','scroll_view_more'); lst=m.node('list_view','list_view_more',[view]); m.top=m.node('window','playermore_page',[lst])
    m.word(view+O['W_PARENT'],lst); m.word(lst+O['W_PARENT'],m.top); m.word(m.top+O['W_PARENT'],m.wm); m.nodes[m.wm]={'type':'window_manager','children':[m.top]}
    m.word(lst+O['ROW_HEIGHT'],0); m.word(lst+O['LIST_DEFAULT_ITEM_HEIGHT'],default); m.word(view+O['W_W'],375); m.word(view+O['W_H'],294)
    return m,view
def stock_row(m,view,children,h=0):
    item=m.node('list_item'); button=m.node('button','0',children); m.nodes[item]['children']=[button]; m.word(item+O['W_W'],0); m.word(item+O['W_PARENT'],view); m.word(button+O['W_PARENT'],item)
    m.word(item+O['W_H'],h); m.nodes[view]['children'].append(item)
    em=m.alloc(4); it=m.alloc(0x28); m.word(button+O['W_EMITTER'],em); m.word(em,it); m.word(it+O['EMIT_TYPE'],O['EVT_CLICK'])  # stock's builder gives the row's button its click
    for k,v in zip(('W_X','W_Y','W_W','W_H'),(20,0,335,70)): m.word(button+O[k],v)
    for c in children: m.word(c+O['W_PARENT'],button)
    return item,button
def box(m,kind,name,x,y,w,h):
    c=m.node(kind,name)
    for k,v in zip(('W_X','W_Y','W_W','W_H'),(x,y,w,h)): m.word(c+O[k],v)
    return c
def speed_row(m,view):
    labels=[box(m,'hscroll_label','title',72,12,40,20),box(m,'hscroll_label','note',72,44,253,16)]; images=[box(m,'image',f'img_forward_{i}',x,9,42,61) for i,x in enumerate((120,177,231,283))]
    icon=box(m,'image','image',10,0,52,70); item,button=stock_row(m,view,[icon,*labels,*images]); return item,button,icon,labels,images
m,view=stock_list(oled_on,32)
label=box(m,'hscroll_label','hscroll_label',72,0,204,70); sw=box(m,'image','image1',276,0,50,70); ab,ab_button=stock_row(m,view,[box(m,'image','image',10,0,52,70),label,sw],78)
plain,_=stock_row(m,view,[box(m,'hscroll_label','hscroll_label_1',10,0,266,70)],0)
item,button,icon,labels,images=speed_row(m,view)
m.handlers[int(manifest['patch_symbols']['stock_playermore_trampoline'],16)]='stock_playermore'
assert m.call(address=HOOKS['playermore_page_init'][0],args=(m.top,0,0,0),gap=0)==0; lay(m,view)
assert geometry(m,ab)[3]==32 and geometry(m,plain)[3]==32 and geometry(m,item)[3]==78 and geometry(m,item)[1]==64
OG=OLED['geom']; TITLE_PX,META_PX=OLED['font_px[ROLE_TITLE]'],OLED['font_px[ROLE_META]']; SEL_IN=OLED['sel_inset']  # OLED's grid and roles
assert geometry(m,ab_button)==(0,0,375,32) and geometry(m,label)[3]==32 and m.nodes[label]['style:normal:font_size']==TITLE_PX and geometry(m,sw)[3]==32
assert geometry(m,button)==(20,0,335,70) and [geometry(m,i) for i in images]==[(x,9,42,61) for x in (120,177,231,283)] and all(m.nodes[i]['visible'] for i in images)
sx=OG.text_x-20  # the set's text column, in the speed row's button at x 20 (navigation.c stock_more_speed)
assert not m.nodes[icon]['visible'] and geometry(m,labels[0])==(sx,8,120-sx-2,28) and m.nodes[labels[0]]['style:normal:font_size']==META_PX
assert geometry(m,labels[1])==(sx,43,253,18) and m.nodes[labels[1]]['style:normal:font_size']==META_PX; passed()
# The selection reaches each laid-out row at the row's height: More's, its speed row's whole 78 px, and Update Library's;
# OLED's bar is sel_inset in from each side.
m.paint(view); assert m.selected(view)==0 and m.sel()==(SEL_IN,0,375-2*SEL_IN,32),(m.selected(view),m.sel())
m.call(); m.paint(view); assert m.selected(view)==1 and m.sel()==(SEL_IN,32,375-2*SEL_IN,32),(m.selected(view),m.sel())
m.call(); m.paint(view); assert m.selected(view)==2 and m.sel()==(SEL_IN,64,375-2*SEL_IN,78),(m.selected(view),m.sel())
for page in ('updatemusic_page',):
    m,view=stock_list(oled_on,32); m.nodes[m.top]['name']=page
    for _ in range(3): stock_row(m,view,[box(m,'hscroll_label','hscroll_label',72,0,204,70)],78)
    lay(m,view); m.paint(view); assert m.selected(view)==0 and m.sel()==(SEL_IN,0,375-2*SEL_IN,32),(page,m.selected(view),m.sel())
    m.call(); m.paint(view); assert m.selected(view)==1 and m.sel()==(SEL_IN,32,375-2*SEL_IN,32),(page,m.selected(view),m.sel())
passed()
# The artist page's Albums grid under OLED's set (stock 0x4ae3cc: three 110 x 160 tiles to a 160 px list_item of
# list_view_album). Its item_height, the set's pitch, made each row one pitch high: the rows overlapped, and stock's
# list painter, culling a row by its own box (0x5fe6a8), left the tiles' covers and names unpainted under the
# selection card. The rows keep their height for the layout and the item_height is back after it; text rows in such
# a list still take the pitch. The wheel moves the card from tile to tile inside each tile, the selected album's name
# in the selection's text colour over it, the others' as the page's.
def grid_rows(m,view,n):
    rows,tiles,names=[],[],[]
    for r in range(n):
        item=m.node('list_item'); m.word(item+O['W_PARENT'],view); m.word(item+O['W_H'],160); m.nodes[view]['children'].append(item); rows.append(item)
        for x in (14,132,250):
            cover=box(m,'image',f'imgalbum_{len(tiles)}',0,0,110,110); name=box(m,'hscroll_label','',0,116,110,20)
            b=box(m,'button',f'album_{len(tiles)}',x,0,110,160); m.nodes[b]['children']=[cover,name]; m.word(b+O['W_PARENT'],item)
            for c in (cover,name): m.word(c+O['W_PARENT'],b)
            em=m.alloc(4); it=m.alloc(0x28); m.word(b+O['W_EMITTER'],em); m.word(em,it); m.word(it+O['EMIT_TYPE'],O['EVT_CLICK'])
            m.nodes[item]['children'].append(b); tiles.append(b); names.append(name)
    return rows,tiles,names
m,view=stock_list(oled_on,32); m.nodes[m.top]['name']='artistinfo_page'
lst=m.get(view+O['W_PARENT']); m.word(lst+O['ROW_HEIGHT'],32); m.word(lst+O['W_W'],375)
rows,tiles,names=grid_rows(m,view,2)
lay(m,view)
assert [geometry(m,r)[1::2] for r in rows]==[(0,160),(160,160)] and m.get(view+O['VIEW_CONTENT_H'])==320
assert m.get(lst+O['ROW_HEIGHT'])==32
assert all(geometry(m,n)[1]+geometry(m,n)[3]<=geometry(m,m.get(m.get(n+O['W_PARENT'])+O['W_PARENT']))[3] for n in names)
t,_=stock_row(m,view,[box(m,'hscroll_label','hscroll_label',72,0,204,70)],78); m.nodes[view]['children']=[t]; lay(m,view); assert geometry(m,t)[3]==32; passed()
m,view=stock_list(oled_on,32); m.nodes[m.top]['name']='artistinfo_page'
lst=m.get(view+O['W_PARENT']); m.word(lst+O['ROW_HEIGHT'],32); m.word(lst+O['W_W'],375)
rows,tiles,names=grid_rows(m,view,2); lay(m,view); i=O['TILE_INSET']
m.paint(view); assert m.selected(view)==0 and m.rounded[-1]['rect']==(14+i,i,110-2*i,160-2*i),m.rounded[-1]
for k in (1,2,3):
    m.call(); m.paint(view); assert m.selected(view)==k and m.rounded[-1]['rect']==(14+118*(k%3)+i,160*(k//3)-m.get(view+O['SCROLL_Y'])+i,110-2*i,160-2*i),(k,m.rounded[-1],m.get(view+O['SCROLL_Y']))
for n,want in ((names[3],SEL_TEXT),(names[0],MUTED)):  # stock's #aaaaaa: the selection's text on it, the muted grey off it
    m.paint(n); got=style_color(m,0xffaaaaaa)[1]; assert got==want,(hex(got),)
passed()
# The artist page's title: load_artistinfo_list writes "%s" of the record's artist, glibc's "(null)" for an artist
# with no name; the tab's load writes the language's Unknown Artist over it before the navbar is built from it, and
# leaves a named artist's alone.
class TitleMachine(ArtistMachine):
    def hook(self,u,address,size,unused):
        if address==syms['getFormatString']:
            self.keys.append(self.text(u.reg_read(REGS[0]))); self.u.mem_write(u.reg_read(REGS[1]),'Unknown Artist'.encode()+b'\0'); self.returns(0); return
        return super().hook(u,address,size,unused)
for stock,want,keys in (('(null)','Unknown Artist',['player_unknown']),('Baroness','Baroness',[]),('(null) Jazz','(null) Jazz',[])):
    m=TitleMachine(page='artistinfo_page',cls=0xff01,rows=2); m.keys=[]; m.word(syms['g_local_classinfo_save'],0xff01)
    m.split=[m.song('Baroness')]; m.word(m.split[0]+O['REC_ALBUM_ARTIST'],m.string('Baroness'))
    m.u.mem_write(O['ARTIST_TITLE'],stock.encode()+b'\0')
    m.top=m.node('window','x_page')  # over the page as its tabs load: stock's tabs, not the design's page (artist_local)
    for tab in (1,0): m.call(address=HOOKS['load_localartist_list'][0],args=(tab,0,0,0),gap=0)
    assert m.text(O['ARTIST_TITLE'])==want and m.keys==keys,(stock,m.text(O['ARTIST_TITLE']),m.keys)
passed()
# The selection reaches Queue's table rows at OLED's pitch; the chevrons of a list of one-line rows stay off it.
qm=oled_on(Machine()); qw,qr,qe=qm.table_page(name='playerqueue_page'); qm.word(qw+O['ROW_HEIGHT'],32); qm.word(qw+O['W_W'],375)
for i,(r,e) in enumerate(zip(qr,qe)): qm.word(r+O['W_Y'],i*32); qm.word(r+O['W_H'],32); qm.word(e+O['W_H'],32)
qm.paint(qw); assert qm.selected(qw)==0 and qm.sel()[1::2]==(0,32) and not qm.icons; passed()
# Stock's own table rows: Queue's, Go to's and Special Folders' titles find their face as they're bound, a CJK title in stock's, under OLED's set; Queue's alone goes to the
# text column (the set's text_x, in its button at 20), its right edge kept. A row's layout is the compact one.
m=oled_on(Machine()); m.mock('widget_set_text_utf8','widget_move_resize','widget_set_children_layout')
q=box(m,'hscroll_label','scrlabel_name',10,0,315,28); folder=box(m,'hscroll_label','listsscrlabel',128,0,140,28)
bind=lambda w,t: m.call(address=payload_syms['stock_row_text'],args=(w,m.string(t),0,0),gap=0)
assert bind(q,'Billie Jean')==0 and bind(folder,'Music')==0
assert m.nodes[q]['text']=='Billie Jean'
sx=OG.text_x-20
assert geometry(m,q)==(sx,0,325-sx,28) and geometry(m,folder)==(128,0,140,28) and m.nodes[q]['style:normal:font_name']=='inter' and m.nodes[q]['style:normal:font_size']==TITLE_PX
m.nodes[q]['text']=''; bind(q,'\u5468\u6770\u502b'); assert m.nodes[q]['style:normal:font_name']=='default'
row=m.node('button'); m.call(address=payload_syms['stock_set_row_layout'],args=(row,m.string('default(r=1,c=0,s=14)'),0,0),gap=0)
vtable=m.get(m.get(row+O['W_CHILDREN_LAYOUT'])+O['CHILDREN_LAYOUT_VTABLE'])
assert m.get(vtable+8)==payload_syms['compact_row_layout']
passed()
# Queue's playing row: the build makes the red style white, so under a re-pitched set (OLED's) the row says so inline, red for the playing row, white (the ink) on any
# other, each bind; stock's use_style call comes here. The style hook then treats it as any stock red.
assert struct.unpack_from('<I',demo,fileoff(demo,0x53aeac))[0]==0x0c000000|payload_syms['stock_row_style']>>2
m=oled_on(Machine()); q=box(m,'hscroll_label','scrlabel_name',10,0,315,28); use=lambda n: m.call(address=payload_syms['stock_row_style'],args=(q,m.string(n),0,0),gap=0)
use('s_scrlabel_red24l'); on=m.nodes[q].get('style:normal:text_color'); use('s_scrlabel_white24l'); off=m.nodes[q].get('style:normal:text_color')
assert m.nodes[q]['style']=='s_scrlabel_white24l'
assert (on&0xffffffff,off&0xffffffff)==(color_t(O['STOCK_RED']),0xffffffff)
passed()
# The playlist More page under a set without settings icons (OLED's): no 50 px icons on its 28 px rows, the text at the column, in the row's face.
def pl_more(boot):
    m=Machine(); boot(m); m.handlers[int(manifest['patch_symbols']['stock_playlistsmore_trampoline'],16)]='stock_playlistsmore'
    b=[(box(m,'image',i,10,0,52,28),box(m,'hscroll_label',l,72,0,253,28)) for i,l in (('img_rename','label_rename'),('img_del','label_del'))]
    win=m.top=m.node('window','playlistsmore_page',[x for p in b for x in p]); m.word(win+O['W_PARENT'],m.wm)
    assert m.call(address=HOOKS['localmusic_playlistsmore_page_init'][0],args=(win,1,0,0),gap=0)==0
    return m,win,b
m,win,pl=pl_more(oled_on)
for img,lab in pl: assert m.nodes[img]['visible']==0 and geometry(m,lab)[0]==OG.text_x-O['SET_STOCK_X'] and m.nodes[lab]['style:normal:font_size']==TITLE_PX
passed()
# A picker opened by a tap: its first paint comes with the pointer still down, which keeps other lists from recalling, but a
# picker (Language, its stored row's select image) arrives on its option all the same, so a Centre
# there does not take the first row; another list starts at the top. Appearance's rows, settings rows of a set without
# settings icons, hide their leading image.
m=oled_on(Machine()); m.pressed=1; w,es=m.page_list(4,name='language_page'); m.nodes[es[2]]['children']=[m.node('image',image='select')]
m.paint(w); assert m.selected(w)==2,m.selected(w)
m=oled_on(Machine()); m.pressed=1; w,es=m.page_list(4,name='display_page'); m.paint(w); assert m.selected(w)==0,m.selected(w)
m,view=settings('display',default=32,sub=1,boot=oled_on); lay(m,view)
assert not any(m.nodes[m.nodes[m.nodes[r]['children'][0]]['children'][0]]['visible'] for r in m.nodes[view]['children'])
passed()
# A stock row with a second text line under its title (Bluetooth's Quality, 0x4c39e8: the title at y 12, the
# detail at y 44 of its 70 px body), under a set whose row is too short for both (OLED's 32), takes two rows, so
# nothing overlaps the next row. Its one-line neighbours keep the set's row.
def stacked_row(m,view):
    title,detail=m.node('hscroll_label'),m.node('hscroll_label')
    for c,box in ((title,(10,12,266,20)),(detail,(10,44,266,16))):
        for k,v in zip(('W_X','W_Y','W_W','W_H'),box): m.word(c+O[k],v)
    b=m.node('button',children=[title,detail])
    for k,v in zip(('W_X','W_W','W_H'),(20,335,70)): m.word(b+O[k],v)
    item=m.node('list_item',children=[b]); m.word(item+O['W_H'],78); m.nodes[view]['children'].insert(1,item)
    return item,title,detail
m,view=settings(0x4c617c,default=32,boot=oled_on); item,title,detail=stacked_row(m,view); assert lay(m,view)==0
rows=[i for i in m.nodes[view]['children'] if m.nodes[i]['visible']]; hs=[geometry(m,i)[3] for i in rows]
assert hs[1]==64 and set(hs[:1]+hs[2:])=={32},hs
ys=[geometry(m,i)[1] for i in rows]; assert all(y+h==ny for y,h,ny in zip(ys,hs,ys[1:])),(ys,hs)
_,ty,_,th=geometry(m,title); _,dy,_,dh=geometry(m,detail); assert 0<=ty and ty+th<=dy and dy+dh<=64,(ty,th,dy,dh)
after=tree(m,view); assert lay(m,view)==0 and tree(m,view)==after; passed()  # a later layout changes nothing
# Its stock icon goes under OLED's set: 64 px is no Streaming row (52), the only one with an icon there. The text starts on
# the set's text column less hscroll_label's 2 px margin, as a set with a right column has it (navigation.c set_text_x).
assert OG.right_x; OLED_SET_TEXT=OG.text_x-2
m,view=settings(0x4c617c,default=32,boot=oled_on); item,title,detail=stacked_row(m,view)
b=m.nodes[item]['children'][0]; lead=m.node('image')
for k,v in zip(('W_X','W_Y','W_W','W_H'),(10,0,O['SET_STOCK_ICON'],70)): m.word(lead+O[k],v)
m.nodes[b]['children'][:0]=[lead]; assert lay(m,view)==0 and geometry(m,item)[3]==64 and not m.nodes[lead]['visible']
assert geometry(m,title)[0]==OLED_SET_TEXT; passed()
# A paired device's row (0x4c365c): a 50 px icon at x 0, its two lines at x 52, a trailing image. A set without settings
# icons hides that icon too, and the lines start at the text column, not left of it. The trailing image stays.
m,view=settings(0x4c617c,default=32,boot=oled_on); item,title,detail=stacked_row(m,view)
b=m.nodes[item]['children'][0]; lead,trail=m.node('image'),m.node('image')
for c,box in ((lead,(0,0,50,70)),(trail,(282,0,50,70))):
    for k,v in zip(('W_X','W_Y','W_W','W_H'),box): m.word(c+O[k],v)
for c in (title,detail): m.word(c+O['W_X'],52)
m.nodes[b]['children'][:0]=[lead]; m.nodes[b]['children'].append(trail); assert lay(m,view)==0
assert not m.nodes[lead]['visible'] and m.nodes[trail]['visible'] and geometry(m,item)[3]==64
assert geometry(m,title)[0]==geometry(m,detail)[0]==OLED_SET_TEXT,(geometry(m,title),OLED_SET_TEXT); passed()
# A list whose default_item_height is no settings row (local pages, Home) keeps its rows as built.
m,view=settings(0x4d3594,default=72); before=tree(m,view); lay(m,view); after=tree(m,view)
assert [r[1] for r in after]==[r[1] for r in before] and all(r[0][3]==72 for r in after); passed()
# OLED's own set (oled/theme.xml geometry, the one the build bakes: tools/theme.py built_geometry). oled_set: the first
# system_bar load, as on a unit, with THEME 3 stored, as on any unit past its migration, so no preset is applied.
OLT=theme_builtins()[0]; OLG=OLED_G
def oled_set(m):
    m.config.setdefault('THEME','3'); m.record_syscalls(); m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'],16)]='stock_ui_load'
    m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0],args=(m.string('system_bar'),0,0,0),gap=0); return m
# OLED's settings rows: set_row from the list's top (set_top 0; its list is found by the set's row), stock's icons hidden
# (set_icon 0), the text's box at text_x less hscroll_label's 2 px margin (navigation.c set_text_x); a list of stock's
# 68 px settings rows (SET_ROW) is not a settings list on that set.
for builder in (0x4ca914, 0x4d3594, 'display'):  # language, System settings, Display
    CONFIG.clear(); m,view=settings(builder,default=OLG.set_row,boot=oled_set); items=[i for i in m.nodes[view]['children'] if m.nodes[i]['visible']]
    # set_top counts from the status row's foot: in the full-bleed window (at 0), the bar's height down (settings_layout)
    assert lay(m,view)==0 and m.get(m.get(view+O['W_PARENT'])+O['W_Y'])==OLG.set_top+OLG.bar
    assert [geometry(m,i)[1] for i in items]==[OLG.set_row*k for k in range(len(items))] and {geometry(m,i)[3] for i in items}=={OLG.set_row},builder
    for i in items:
        for c in m.nodes[m.nodes[i]['children'][0]]['children']:
            x,y,w_,h=geometry(m,c)
            if m.nodes[c]['type']=='image' and w_==O['SET_STOCK_ICON']: assert not m.nodes[c]['visible'],builder
            elif m.nodes[c]['type']=='hscroll_label' and m.nodes[c]['name']!='_setting_value': assert x==OLG.text_x-2 and 0<=y and y+h<=OLG.set_row,(builder,x,y,h)
passed()
CONFIG.clear(); m,view=settings(0x4d3594,boot=oled_set); before=tree(m,view); lay(m,view)
after=tree(m,view); assert [r[1] for r in after]==[r[1] for r in before] and all(r[0][3]==SET['ROW'] for r in after); passed()  # rows as built, stacked at the list's 68
# A 78px item some other builder made (no stock settings button) keeps its height and children.
CONFIG.clear(); m,view=settings(0x4d3594,default=OLG.set_row,boot=oled_set); odd=m.node('list_item'); other=m.node('button')
m.nodes[odd]['children']=[other]; m.word(odd+O['W_H'],78); m.word(odd+O['W_W'],0)
for k,v in (('W_X',8),('W_Y',0),('W_W',359),('W_H',70)): m.word(other+O[k],v)
m.nodes[view]['children'].append(odd); lay(m,view)
assert geometry(m,odd)[3]==78 and geometry(m,other)==(8,0,359,70); passed()
# The wheel walks the laid-out rows, OLED's flat bar spans the whole set_row row less the selection's inset each side
# (oled/theme.xml list.selection), the list scrolls to keep the row in view, and Centre and a tap reach the same button.
CONFIG.clear(); m,view=settings(0x4d3594,default=OLG.set_row,boot=oled_set,flat=True); lay(m,view); items=m.nodes[view]['children']
buttons=[m.nodes[i]['children'][0] for i in items]
m.paint(view); assert m.selected(view)==0
for k in range(1,len(items)):
    m.call(); m.paint(view); i=k; assert m.selected(view)==i
    y=geometry(m,items[i])[1]-signed(m.get(view+O['SCROLL_Y']))
    assert 0<=y and y+OLG.set_row<=SET['ROWS']*SET['ROW'], (k,y)
    # the list's box runs past the list end (282 on the screen, as on the Q2), so list_fade's black rows follow the bar's
    m.bands=[b for b in m.bands if b[4]&0xffffff]
    assert m.bands and m.sel()[2:]==(375-2*OLT['sel_inset'],OLG.set_row)
assert m.confirm()==11 and m.dispatched()[0][1]==buttons[-1]; passed()
m.click(buttons[2]); assert m.selected(view)==2; passed()

# The payload's own accent drawing: the selection is OLED's flat bar in its one scheme, Blue (oled/theme.xml
# colorScheme oled-blue, its five tones one colour), whatever OLED_ACCENT holds (Blue is all OLED offers) or ACCENT, a
# key OLED does not read, says.
from build import FACES
FACE_NAMES=[FACES[k].name for k in FACES]  # theme_t.font's FONT_* by index
STATES=('normal','focused','disable')
OLED_ACCENT=next(o['def'] for o in OLT['opts'] if o['setting']=='SETTING_ACCENT'); OLED_TONES=OLT['schemes'][OLED_ACCENT]['tones']; OLED_BLUE=OLED_TONES[0]
def appearance(config):  # Display's Appearance page (display_sub 1) and its rows
    m,_,_=display(config,sub=0); view=display_page(m,1); return m,view,m.nodes[view]['children']
for cfg in ({},{'OLED_ACCENT':'2'},{'ACCENT':'2'}):
    m,view,_=appearance(cfg); m.paint(view)
    assert m.bands and {b[4]&0xffffff for b in m.bands}=={color_t(OLED_BLUE)&0xffffff},cfg
passed()
# Appearance is OLED's options in theme.xml's order, with no Theme row, whatever THEME an older build stored (none
# runs the migration too; here nothing boots, so no preset is applied and Home reads as stored, Split).
for stored in (None,'0','1','2','3','9'):
    m,view,kids=appearance({} if stored is None else {'THEME':stored})
    assert [row_text(r) for r in kids]==['Light Mode: Off','Accent: Blue','Home: Split','Battery: Icon','Song Artist: Inline','Song Length: Albums','Track Time: Remaining'],stored
passed()
# Songs' action bar on OLED's set (its action_row; navigation.c action_row): what stock builds in it (it does again on a
# refresh) takes the plain row's shape: the label reads Shuffle after the theme's glyph (list.shuffle, SHUFFLE_LABEL_DX
# past the text column) in the row's face and size, Play All's image a hit area over the left with no icon, Sort's bars
# in the pill the selection's inset ends (SORT_PILL_W), hidden on the modal set (its hold menu holds Sorting). All stay
# stock's own widgets, their handlers kept.
def oled_bar(nm,*children):  # the system bar under OLED's set: 375 wide, the set's bar high
    b=nm.node('system_bar','system_bar',list(children)); nm.word(b+O['W_PARENT'],nm.wm)
    nm.word(b+O['W_W'],375); nm.word(b+O['W_H'],OLG.bar); nm.word(syms['system_bar'],b); return b
def action_page(nm,sorts=True):
    alabel=nm.node('hscroll_label',text='Play All'); aplay=nm.node('image'); asort=nm.node('image')
    for c,box in ((alabel,(70,0,130,50)),(aplay,(16,0,50,50)),(asort,(306,0,50,50))):
        for off,v in zip(('W_X','W_Y','W_W','W_H'),box): nm.word(c+O[off],v)
    aview=nm.node('view','view_navbar_allplay',[alabel,aplay]+[asort]*sorts); nm.word(aview+O['W_H'],28)
    awin=nm.node('window','allmusic_page',[aview]); nm.top=awin; nm.word(awin+O['W_PARENT'],nm.wm)
    atitle=nm.node('hscroll_label','label_title'); abar=oled_bar(nm,atitle)
    nm.call(address=HOOKS['widget_on_paint_background'][0],args=(abar,nm.canvas,0,0),gap=0)
    return alabel,aplay,asort,abar
am=oled_set(Machine()); al,ap,asort,abar=action_page(am)
boxof=lambda w: tuple(am.get(w+O[k]) for k in ('W_X','W_Y','W_W','W_H'))
assert boxof(al)==(OLG.text_x+28,0,280,OLG.action_row) and boxof(ap)==(0,0,300,OLG.action_row) and boxof(asort)==(375-OLT['sel_inset']-44,0,44,OLG.action_row)
assert am.nodes[asort]['image']=='sort_bars' and am.nodes[asort]['visible']==0 and am.nodes[al]['text']=='Shuffle'
assert [(am.nodes[al][f'style:{s}:font_name'],am.nodes[al][f'style:{s}:font_size']) for s in STATES]==[(FACE_NAMES[OLT['font[ROLE_TITLE]']],OLT['font_px[ROLE_TITLE]'])]*3
am.calls=[]; am.call(address=HOOKS['widget_on_paint_background'][0],args=(abar,am.canvas,0,0),gap=0); assert not [c for c in am.calls if c[0]=='widget_move_resize'],am.calls  # shaped once
am.word(al+O['W_H'],50); am.call(address=HOOKS['widget_on_paint_background'][0],args=(abar,am.canvas,0,0),gap=0); assert boxof(al)==(OLG.text_x+28,0,280,OLG.action_row)  # stock's refresh: again
am=oled_set(Machine()); al,ap,asort,abar=action_page(am,sorts=False)
assert boxof(al)==(OLG.text_x+28,0,280,OLG.action_row) and boxof(ap)==(0,0,300,OLG.action_row); passed()  # Recently Added's has no Sort
# On OLED's set the selection's scroll stops where list_end says, a row of the client under the bar (below it the glass
# hides a row's text), and a view stops with a row's top at its own (navigation.c sel_height, fit_top): the
# wheel's last row ends there, past stock's own end. A list lower in its window stops at the same screen row.
for ly in (0,32):
    lm=oled_set(Machine())
    lw,les=lm.page_list(14,height=294-ly,extent=672,name='playlist_page'); window(lm,'playlist_page',lw); lm.word(lw+O['W_Y'],OLG.bar+ly); lm.paint(lw)  # ly under the row's foot (full-bleed)
    tops=[]
    for _ in range(16):
        lm.call(); lm.paint(lw); tops.append(lm.get(lw+O['SCROLL_Y']))
    lend=-(-(672-(OLG.list_end-ly))//48)*48  # the first 48 px row's top at or past the end
    assert max(tops)==lend,(ly,max(tops),lend)
passed()
# The status row names no page (the user's layout D, 2026-11-25), but its hidden title still holds the page's name for
# the Search bar, the letter strip and Up Next: a payload page's 48 px caption, the first in any of the window's children
# (Games' list follows its empty sheet), followed as an app's page rebuilds it under the live window, written only when
# it changes, and never repainted for it (nothing shows it).
tm=oled_set(Machine()); tl=tm.node('hscroll_label','title',text='Games'); tm.word(tl+O['W_H'],48)
tsheet=tm.node('view','sheet'); tlist=tm.node('view','list',[tl]); tm.top=tm.node('window','games_page',[tsheet,tlist]); tm.word(tm.top+O['W_PARENT'],tm.wm)
tbar_label=tm.node('hscroll_label','label_title'); tbar=oled_bar(tm,tbar_label)
tpaint=lambda: (tm.call(address=HOOKS['widget_on_paint_background'][0],args=(tbar,tm.canvas,0,0),gap=0),tm.nodes[tbar_label].get('text'))[1]
twrites=lambda: [c for c in tm.calls if c[:2]==('widget_set_text_utf8',tbar_label)]
tshown=lambda: [c for c in tm.calls if c[1]==tbar_label and c[0] in ('widget_invalidate_force','widget_set_visible','widget_set_opacity','widget_move_resize','widget_resize')]
tm.calls=[]; assert tpaint()=='Games' and len(twrites())==1 and not tshown()
tm.advance(1,clear=True); assert tpaint()=='Games' and not twrites() and not tshown()  # no change, nothing written
tm.nodes[tl]['text']='Renamed'; assert tpaint()=='Renamed' and len(twrites())==1 and not tshown(); passed()
# The clock stands at the row's left on every page, its text from STATUS_ROW_INSET (the user's layout D, 2026-11-25):
# Home, Now Playing, a titled page, Settings and Control Center alike, the title hidden and never moved or sized. The
# clock is in the bar role's face (theme.xml bar.title) and follows the minute.
def tt_bar(cls=Machine):
    ttm=cls(); oled_set(ttm); ttm.clock_minutes=True
    ttt=ttm.node('hscroll_label','label_title',visible=0); ttc=ttm.node('hscroll_label','label_clock')
    slot=ttm.node('view','view_battery'); row=ttm.node('view','view_right',[slot])
    for w,box in ((ttt,(60,11,240,24)),(ttc,(0,11,240,24)),(row,(0,15,313,16)),(slot,(288,0,25,16))):  # the clock anywhere
        for off,v in zip(('W_X','W_Y','W_W','W_H'),box): ttm.word(w+O[off],v)
    ttb=oled_bar(ttm,ttt,ttc,row); ttm.word(row+O['W_PARENT'],ttb); ttm.word(slot+O['W_PARENT'],row)
    def show(win):
        ttm.top=win; ttm.word(win+O['W_PARENT'],ttm.wm)
        for _ in range(2): ttm.call(address=HOOKS['widget_on_paint_background'][0],args=(ttb,ttm.canvas,0,0),gap=0); ttm.advance(1)
        return ttm.nodes[ttt].get('text',''),ttm.nodes[ttc].get('text'),signed(ttm.get(ttc+O['W_X']))
    return ttm,ttt,ttc,show
ttm,ttt,ttc,show=tt_bar()
caption=lambda t: (lambda l: (ttm.word(l+O['W_H'],48),l)[1])(ttm.node('hscroll_label','title',text=t))
pages=[('playing_page',[],''),('bare_page',[],''),('games_page',[caption('Games')],'Games'),
       ('sysset_page',[caption('General')],'General'),('statusbar_dialog',[],'')]
for name,kids,title in pages:
    assert show(ttm.node('window',name,kids))==(title,'6:14',O['STATUS_ROW_INSET']-2),name  # hscroll_label's 2 px
    assert ttm.nodes[ttt].get('visible',1)==0 and signed(ttm.get(ttt+O['W_X']))==60 and ttm.get(ttt+O['W_W'])==240,name
assert not [c for c in ttm.calls if c[1]==ttt and c[0] in ('widget_set_visible','widget_set_opacity','widget_move_resize','widget_resize')]
ttm.clock=(18,15); ttm.advance(61000); assert ttm.nodes[ttc]['text']=='6:15'
assert [(ttm.nodes[ttc][f'style:{s}:font_name'],ttm.nodes[ttc][f'style:{s}:font_size']) for s in STATES]==[(FACE_NAMES[OLT['font[ROLE_BAR]']],OLT['font_px[ROLE_BAR]'])]*3
passed()
# Wired headphones (the user's, 2026-11-25): their glyph, 14 px and SR_GAP (6) more left of the battery, while the player's output is the Q2's DAC with a jack in (3.5 mm g_po_status, 4.4 mm g_bal_status) or a
# USB DAC (way 2); nothing otherwise, with no output connected too (no deaf ear: the owner's, 2026-11-27).
class WayMachine(Machine):
    def __init__(self):
        super().__init__(); self.way=0; self.handlers[syms['mclGetOutputWay']]='w:way'
    def hook(self,u,address,size,_):
        if self.handlers.get(address)!='w:way': return super().hook(u,address,size,_)
        self.calls.append(('mclGetOutputWay',0,0,0)); self.returns(self.way,clobber=True)
wm_,_,_,wshow=tt_bar(WayMachine)
def wleft(way,po,bal):
    wm_.way=way; wm_.byte(syms['g_po_status'],po); wm_.byte(syms['g_bal_status'],bal)
    wshow(wm_.node('window','bare_page'))
    return signed(wm_.call(address=payload_syms['status_slots_left'],args=(0,0,0,0),gap=0))
wbase=wleft(3,0,0); assert 0<wbase<=288,wbase
for way,po,bal,width in ((0,1,0,14),(0,0,1,14),(2,0,0,14),(1,1,0,0),(3,0,0,0),(0,0,0,0)):
    assert wleft(way,po,bal)==wbase-(6+width if width else 0),(way,po,bal)
passed()
# Home on the status row names none of its own lists' and shows the clock, whether or not music plays: no flip as music
# starts or stops.
hm=Machine(); oled_set(hm); hm.clock_minutes=True; hm.handlers.pop(syms['mclGetPlayStatus'],None)
htt,hcl=hm.node('hscroll_label','label_title'),hm.node('hscroll_label','label_clock')
hbar=oled_bar(hm,htt,hcl,hm.node('image','img_state',visible=0))
hhome=hm.node('window','home_page'); hm.top=hhome; hm.word(hhome+O['W_PARENT'],hm.wm)
for status in (0,2,0):
    hm.word(0xa50084,status)
    for _ in range(2): hm.call(address=HOOKS['widget_on_paint_background'][0],args=(hbar,hm.canvas,0,0),gap=0); hm.advance(1)
    assert (hm.nodes[htt].get('text',''),hm.nodes[hcl].get('text'))==('','6:14'),status
passed()
# OLED's list.art.placeholder: stock's 52 px grey tiles (Folder view's folder and song) are drawn that size in their
# middle, the rest clear (navigation.c shrink_centred), their source's first column at its left edge; a cover by path keeps
# the row's size, its corners rounded to list.art.radius (round_corners); another list_ image is left alone.
PH=OLT['art_placeholder']; PH0=(52-PH)//2
fm=Machine(); fm.handlers[tramp['image']]='stock_image'
def ftile(name):
    bm=fm.alloc(0x60); data=fm.alloc(4*52*52)
    fm.word(bm,52); fm.word(bm+4,52); fm.word(bm+8,4*52); fm.u.mem_write(bm+0xe,struct.pack('<H',3)); fm.word(bm+0x14,data)
    fm.u.mem_write(data,(bytes([0,0,255,255])+bytes([0x80,0x80,0x80,255])*51)*52)  # column 0 red (BGRA): scaled, it lands at PH0
    fm.call(address=HOOKS['image_manager_add'][0],args=(0x1000500,fm.string(name),bm,0),gap=0,count=20_000_000)
    raw=bytes(fm.u.mem_read(data,4*52*52)); return lambda x,y,c=3: raw[4*(52*y+x)+c]
for fname in ('list_folder','list_song'):
    fa=ftile(fname); assert [fa(x,26) for x in (PH0-1,PH0,PH0+PH-1,PH0+PH)]==[0,255,255,0] and [fa(26,y) for y in (PH0-1,PH0,PH0+PH-1,PH0+PH)]==[0,255,255,0],fname
    assert fa(PH0,26,2)!=fa(PH0+1,26,2)==fa(PH0+2,26,2),fname  # the red column, the tile's edge
fa=ftile('file:///mnt/mmc/.downpic/fd1.jpg'); assert fa(0,0)==0 and fa(51,26)==fa(26,0)==255
for fname in ('list_tf','list_album'): fa=ftile(fname); assert fa(0,0)==fa(51,26)==255,fname
passed()
# With no row to select the wheel scrolls the view itself, on OLED's grid until the content's foot is on the
# list end, as a drag stops (navigation.c max_top).
lm=oled_set(Machine())
lw,les=lm.page_list(14,height=294,extent=672,name='playlist_page'); window(lm,'playlist_page',lw); lm.nodes[lw]['children']=[]; lm.paint(lw)
tops=[]
for _ in range(40):
    lm.call(); lm.paint(lw); tops.append(lm.get(lw+O['SCROLL_Y']))
assert max(tops)==672-OLG.list_end,max(tops)
passed()
# Text on the bar is sel_text, white, whatever the row's own ink.
sk=Machine(); sw,srows=sk.page_list(3); slabels=[]
for row in srows:
    l=sk.node('hscroll_label'); sk.nodes[row]['children'].append(l); sk.word(l+O['W_PARENT'],row); slabels.append(l)
begin(sk,sw); begin(sk,srows[0]); begin(sk,slabels[0])
assert style_color(sk,0xffaaaaaa)[1]==0xffffffff and style_color(sk,0x80000000)[1]==0x80ffffff
end(sk,slabels[0]); end(sk,srows[0]); end(sk,sw); passed()
# A row's second line: the scheme's muted grey off the bar (#858585), in the light half its flip on white paper and black
# ink (tools/theme.py flip); on the bar it is sel_text's white, in both halves.
OLED_MUTED=OLT['schemes'][OLED_ACCENT]['muted']
for mlight,mwant in (('1',0xff000000|(0xffffff-OLED_MUTED)),('0',0xff000000|OLED_MUTED)):
    sk=Machine(); sk.config['LIGHT']=mlight; sw,srows=sk.page_list(3); slabels=[]
    for row in srows:
        l=sk.node('hscroll_label'); sk.nodes[row]['children'].append(l); sk.word(l+O['W_PARENT'],row); slabels.append(l)
    begin(sk,sw); begin(sk,srows[1]); begin(sk,slabels[1]); assert style_color(sk,0xffaaaaaa)[1]==mwant,mlight
    end(sk,slabels[1]); end(sk,srows[1]); begin(sk,srows[0]); begin(sk,slabels[0]); assert style_color(sk,0xffaaaaaa)[1]==0xffffffff,mlight
    end(sk,slabels[0]); end(sk,srows[0]); end(sk,sw)
passed()
# OLED's halves, each its own scheme row: LIGHT=0 the dark half (no flip: stock's black and white), LIGHT=1 the light
# half on #ffffff paper with #000000 ink (theme.xml light.paper, light.ink). The image hook's glyph pass takes the half's
# ink: a white glyph turns black in the light half, stays white in the dark.
def half_of(k): return k.call(address=payload_syms['scheme'],args=(0,0,0,0),gap=0)&0xffffffff
def lit(k): return k.call(address=payload_syms['ipod_light'],args=(0,0,0,0),gap=0)
nk=Machine(); nk.config['LIGHT']='0'
assert lit(nk)==0 and style_color(nk,0xff000000,'bg_color')[1]==0xff000000 and style_color(nk,0xffffffff)[1]==0xffffffff
nl=Machine(); nl.config['LIGHT']='1'
assert lit(nl)==1 and half_of(nl)!=half_of(nk)
assert style_color(nl,0xff000000,'bg_color')[1]==0xffffffff and style_color(nl,0xffffffff)[1]==0xff000000
assert light_image({'LIGHT':'1'},'scover_wait0',False)[:4]==bytes([0,0,0,0xff])
assert light_image({'LIGHT':'0'},'scover_wait0',False)[:4]==bytes([0xff]*4); passed()
# Light Mode's Auto: by day (the mock's 18:14) the light half; when the clock's minute crosses 19:00 (clock_due, as the
# Light Mode Auto case drives it) the images drop once and the dark half draws.
au=Machine(); au.config['LIGHT']='2'; day=half_of(au)
assert lit(au)==1 and style_color(au,0xff000000,'bg_color')[1]==0xffffffff
au.clock=(19,0); au.calls=[]; au.call(address=payload_syms['clock_due'],args=(0,0,0,0),gap=0,clear=False)
assert [c[:2] for c in au.calls if c[0]=='image_manager_unload_all']==[('image_manager_unload_all',0x1000500)]
assert lit(au)==0 and half_of(au)!=day and style_color(au,0xff000000,'bg_color')[1]==0xff000000; passed()
# Accent is Blue, OLED's one scheme, by default and for a stored value OLED does not offer (Graphite's 7) or cannot read
# (12); the row's click writes Blue again under OLED's key (theme.xml accent key OLED_ACCENT).
for stored in (None,'7','12'):
    m,view,kids=appearance({} if stored is None else {'OLED_ACCENT':stored})
    assert row_text(kids[1])=='Accent: Blue',stored
f,ctx=m.handler(m.nodes[kids[1]]['children'][0],O['EVT_CLICK'])
for _ in range(2):
    m.calls=[]; assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
    assert [(c[1],m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[(OLED_ACCENT,'OLED_ACCENT')] and row_text(kids[1])=='Accent: Blue'
passed()
# OLED's own keys are read after the payload's, OLED_ACCENT then its options in theme.xml's order, each at its default.
m,view,kids=appearance({}); reads=[r[2:] for r in m.config_reads if r[1]=='IPOD']; at=reads.index(('OLED_ACCENT',str(OLED_ACCENT)))
assert reads[at:at+4]==[('OLED_ACCENT',str(OLED_ACCENT))]+[(o['key'],str(o['def'])) for o in OLT['opts'] if o['setting']=='SETTING_OWN']; passed()
# Battery takes Icon or Percent (theme.xml's values): the row cycles those two and writes the key as usual. A stored
# value OLED does not offer (Icon + Percent, Percent Inside) reads as Icon, OLED's default, and stays stored until the
# row moves it on; a key that is not stored reads as Icon.
m,view,kids=appearance({}); row=kids[3]; f,ctx=m.handler(m.nodes[row]['children'][0],O['EVT_CLICK'])
assert row_text(row)=='Battery: Icon'
for value,name in ((1,'Percent'),(0,'Icon'),(1,'Percent')):
    m.calls=[]; assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
    assert [(c[1],m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[(value,'BATTERY')] and row_text(row)=='Battery: '+name
for stored in ('2','3'):
    m,view,kids=appearance({'BATTERY':stored}); row=kids[3]; f,ctx=m.handler(m.nodes[row]['children'][0],O['EVT_CLICK'])
    assert row_text(row)=='Battery: Icon' and not [c for c in m.calls if c[0]=='write_int_config'] and m.config['BATTERY']==stored
    m.calls=[]; m.call(address=f,args=(ctx,m.event,0,0),gap=0)  # from what it reads as: on to Percent
    assert [(c[1],m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[(1,'BATTERY')] and row_text(row)=='Battery: Percent'
passed()
# OLED's rows take its list title face inline in every state, at its title size, once (a second layout writes nothing):
# Inter, whose subset keeps Latin and Cyrillic (tools/build.py FACES); a title outside it (CJK) falls back to stock's
# face at Inter's fallback_pct of the size. On OLED's set or not.
def font_row(setup,text):
    k=Machine()
    if setup: setup(k)
    w=k.page('artistinfo_page','scroll_view'); k.word(w+O['W_PARENT'],k.top); k.word(k.top+O['W_PARENT'],k.wm)
    k.nodes[w]['name']='scroll_view_track'; k.row_record=k.alloc(0x80)
    for off in (8,12,16,24): k.word(k.row_record+off,k.string(text))
    k.word(k.row_record+0x48,1)
    k.mock('button_create','list_item_create','image_create','view_create','hscroll_label_create',
           'gif_image_create','widget_use_style','widget_set_name','widget_set_text_utf8',
           'widget_set_visible','widget_on','widget_destroy_children','image_set_draw_type',
           'image_base_set_image','set_hscroll_label_attribute','hscroll_label_set_only_focus',
           'hscroll_label_set_ellipses','hscroll_label_set_speed','gif_image_play','deque_at',
           'deque_size','tk_snprintf','batch_get_selectitem','file_is_playing','getFormatString',
           'getMusicByPlayList','get_albumcover_listsize','mclGetPlayStatus','list_get_img_pic',
           'strcmp@GLIBC_2.0','toolsTrimLeft','toolsTrimRight')
    k.handlers[0x4ad020]='row_toolbar'
    assert k.call(address=0x4a7f44,args=(w,0,0,0))==0
    button,=[n for n,v in k.nodes.items() if v['type']=='button']
    native_row_layout(k,button)
    kids=k.nodes[button]['children']  # the title as compact_row_layout finds it: the first hscroll_label, or the view holding one
    text=next(c for c in kids if k.nodes[c]['type']=='hscroll_label' or any(k.nodes[t]['type']=='hscroll_label' for t in k.nodes[c]['children']))
    return k,button,next((c for c in k.nodes[text]['children'] if k.nodes[c]['type']=='hscroll_label'),text)
TFACE,TPX=FACE_NAMES[OLT['font[ROLE_TITLE]']],OLT['font_px[ROLE_TITLE]']
INTER_PCT=FACES[TFACE].fallback_pct; CJK=('default',(TPX*INTER_PCT+50)//100)
fk,fb,ft=font_row(oled_set,'Title')
assert [(fk.nodes[ft][f'style:{s}:font_name'],fk.nodes[ft][f'style:{s}:font_size']) for s in STATES]==[(TFACE,TPX)]*3
fk.calls=[]; native_row_layout(fk,fb); assert not [c for c in fk.calls if c[0]=='widget_set_prop_str']
for text in ('Навстречу ветру','Moscow Москва'):
    fk,fb,ft=font_row(oled_set,text); assert [(fk.nodes[ft][f'style:{s}:font_name'],fk.nodes[ft][f'style:{s}:font_size']) for s in STATES]==[(TFACE,TPX)]*3,text
fk,fb,ft=font_row(oled_set,'日本語 Title'); assert (fk.nodes[ft]['style:normal:font_name'],fk.nodes[ft]['style:normal:font_size'])==CJK  # one face a label: its Latin letters too
# One label from Latin to CJK and back: the answer changes with the text, so nothing stale stays.
fw=fk.node('hscroll_label',text='Songs'); fl=lambda t,want: (fk.nodes[fw].__setitem__('text',t),fk.call(address=payload_syms['theme_font'],args=(fw,0,-2,0),gap=0),[(fk.nodes[fw][f'style:{s}:font_name'],fk.nodes[fw][f'style:{s}:font_size']) for s in STATES]==[want]*3)[-1]
assert fl('Songs',(TFACE,TPX)) and fl('歌',CJK) and fl('Песни',(TFACE,TPX)) and fl('Songs',(TFACE,TPX)); passed()
# A virtual table binds a pooled row's text with no relayout (table_client's load loop, #217): ringnav_table_load runs stock's on_load_data (a PIC
# call), then each label its layout gave a face finds it again for the new text, at its own role's size (a title and a tall title, each
# Inter at its px; CJK stock's face at Inter's fallback share); a label never themed stays as it was.
from build import TABLE_LOAD_CALL, TABLE_SCROLL_CALLS
tdemo=(B/'demo').read_bytes(); assert struct.unpack_from('<I',tdemo,fileoff(tdemo,TABLE_LOAD_CALL))[0]==jal(payload_syms['ringnav_table_load'])
assert all(struct.unpack_from('<I',tdemo,fileoff(tdemo,a))[0]==jal(payload_syms['ringnav_table_scroll']) for a,_ in TABLE_SCROLL_CALLS)  # a table's release clamps to the list end
class LoadMachine(Machine):
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)!='tbl:load': return super().hook(u,address,size,unused)
        self.calls.append(('tbl:load',*[u.reg_read(r) for r in REGS[:3]]))
        for w,t in self.bound: self.nodes[w]['text']=t
        self.returns(0)
lk=LoadMachine()
title,tall,plain=(lk.node('hscroll_label',text='WurtS') for _ in range(3))
box=lk.node('view',children=[tall,plain]); row=lk.node('table_row',children=[title,box]); client=lk.node('table_client',children=[row])  # as stock's rows: labels in a box too
for w,parent in ((title,row),(box,row),(tall,box),(plain,box)): lk.word(w+O['W_PARENT'],parent)
lk.word(row+O['W_PARENT'],client); lk.word(client+O['TABLE_LOAD_DATA'],0x1000900); lk.handlers[0x1000900]='tbl:load'
for w,role in ((title,0),(tall,4)): lk.call(address=payload_syms['theme_font'],args=(w,role,-2,0),gap=0)
def tload(*texts):
    lk.bound=list(zip((title,tall,plain),texts)); lk.calls=[]
    assert lk.call(address=payload_syms['ringnav_table_load'],args=(0x1234,7,row,0),gap=0)==0 and lk.calls[0]==('tbl:load',0x1234,7,row)
    return [(lk.nodes[w].get('style:normal:font_name'),lk.nodes[w].get('style:normal:font_size')) for w in (title,tall,plain)]
TALL=(FACE_NAMES[OLT['font[ROLE_TALL]']],OLT['font_px[ROLE_TALL]'])
assert tload('WurtS','yama','x')==[(TFACE,TPX),TALL,(None,None)]
assert tload('星野 源','須田景凪','原因は自分にある。')==[CJK,('default',(TALL[1]*INTER_PCT+50)//100),(None,None)]
assert tload('YOASOBI','Навстречу','x')==[(TFACE,TPX),TALL,(None,None)]
passed()
# Now Playing's title role (ROLE_NP: Inter Medium, its px) holds Cyrillic in its own face.
ck=Machine(); cw=ck.node('hscroll_label',text='Песни'); ck.call(address=payload_syms['theme_font'],args=(cw,3,-1,0),gap=0)
assert (ck.nodes[cw]['style:normal:font_name'],ck.nodes[cw]['style:normal:font_size'])==(FACE_NAMES[OLT['font[ROLE_NP]']],OLT['font_px[ROLE_NP]']); passed()
# Home's rows take the theme's home.rows face and size.
hk=QueueMachine(); home=asset_tree(hk,HOME_PAGE)
hk.call(address=HOOKS['home_page_init'][0],args=(home,0,0,0),gap=0)
labels=[n for n,v in hk.nodes.items() if v['type']=='hscroll_label' and v.get('style:normal:font_name')]
assert labels and {(hk.nodes[l]['style:normal:font_name'],hk.nodes[l]['style:normal:font_size']) for l in labels}=={(FACE_NAMES[OLT['font[ROLE_HOME]']],OLT['font_px[ROLE_HOME]'])}; passed()
# The fast-scroll letter (draw_centred) takes the list title face (theme_face).
lm=oled_set(Machine()); lm.word(lm.canvas+O['CANVAS_ALIGN_V'],2); lm.word(lm.canvas+O['CANVAS_ALIGN_H'],3)
lw,les=lm.page_list(40,extent=40*48,name='allmusic_page')
for e in les: lm.nodes[e]['text']='row'
lm.paint(lw,gap=0)
for _ in range(4): lm.call(gap=100)
lm.paint(lw,gap=0); assert [l['font'][0] for l in lm.letters]==[TFACE],lm.letters
passed()
# The scrubber's fill is OLED's accent whatever ACCENT, a key OLED does not read, holds; a scrub (Centre, one press, the
# next ends it) keeps it, the status row's grid marking the scrub with paint_scrubber's knob (np_bar.c np_fill); a touch
# keeps it in every state, and no state gets a knob.
CONFIG.clear(); CONFIG.update(ACCENT='3'); m=scrub_page()
assert m.nodes[m.slider][FG]==signed(color_t(OLED_TONES[2])); passed()
CONFIG.clear(); m=QueueMachine(queue=3,pos=1); m.handlers[playing+12]='stock_playing'
m.slider=m.node('slider','slider_play',max=225,value=100)
m.win=m.top=m.node('window','playing_page',[m.slider,m.node('label','label_playtime')]); m.word(m.win+O['W_PARENT'],m.wm)
m.call(address=playing,args=(m.win,7,0,0),gap=0)
assert m.nodes[m.slider][FG]==signed(color_t(OLED_TONES[2])); assert m.call(O['KEY_CENTER'],gap=1000)==11
assert m.nodes[m.slider][FG]==signed(color_t(OLED_TONES[2])); assert m.call(O['KEY_CENTER'],gap=1000)==11; assert m.nodes[m.slider][FG]==signed(color_t(OLED_TONES[2]))
assert {m.nodes[m.slider][f'style:{s}:fg_color'] for s in ('normal','pressed','over','focused')}=={m.nodes[m.slider][FG]}; passed()  # a touch keeps the accent
assert all(m.nodes[m.slider][f'style:{s}:icon']==0 for s in ('normal','pressed','over','focused','disable')); passed()  # no knob: stock's default slider style would supply rounded_n/_o/_p

# Home at init: Split gives the menu the set's split_row and shows the art; Full widens the list to the screen and its
# rows' tap targets to the theme's home.rows full_row, and hides it.
for config,listw,roww,shown in (({'HOME':'1'},375,OLT['home_full_row'],0),({},OLG.split_row,OLG.split_row,1)):
    CONFIG.clear(); CONFIG.update(config); m=CoverflowMachine(); m.open()
    rowsw=[m.get(w+O['W_W']) for w in [m.list,*m.imgs]]
    assert rowsw==[listw]+[roww]*len(HOME_ROWS) and m.nodes[m.art].get('visible',1)==shown,(config,rowsw); passed()
# A page list on the half's paper: page_list writes black inline, the colour hook answers the half's paper for it and the page.
for light,paper in (('1',0xffffffff),('0',0xff000000)):
    SETUP[:]=[lambda k:k.config.update(LIGHT=light)]; pp=CoverflowMachine(); SETUP.clear(); oled_set(pp); pp.top=pp.node('window','era_page')
    lv=pp.call(address=payload_syms['page_list'],args=(pp.top,pp.top,0,pp.string('Era')),stack=(4,64),gap=0)&0xffffffff; lv=pp.get(lv+O['W_PARENT'])
    assert pp.nodes[lv]['type']=='list_view' and pp.nodes[lv]['style:normal:bg_color']==-0x1000000
    for w in (pp.top,lv): begin(pp,w); assert style_color(pp,0xff000000,'bg_color')[1]==paper,light
passed()
Machine.hook=orig_hook; CONFIG.clear()

# Wheel precision (docs/internals.md, Wheel movement): every tick moves, the list ends, and Key Tone
# moved from the press to the row change.
KEYDOWN=HOOKS['on_wm_keydown_before_fun'][0]
NEXT,PREV,CENTER,RETURN=O['KEY_NEXT'],O['KEY_PREV'],O['KEY_CENTER'],O['KEY_RETURN']
TONE=syms['g_keytone_flag']; SOUND_LATCH=0xa4bc00; KEY_LATCH=0xa4bbf8  # stock: one click a press; wheel lockout
def audible(m,tone=1):
    """Key Tone as on the device: the real stock buzzeer_switch runs, and only the system() command it
    hands the MCU, the hardware boundary, is mocked, and the clock the Clicker reads when it looks for
    q2player (patch/clicker.c; no /tmp/q2clicks here unless a test adds it to m.present)."""
    m.handlers.pop(syms['buzzeer_switch'],None); m.mock('system@GLIBC_2.0','clock_gettime@GLIBC_2.2'); m.byte(TONE,tone)
    m.handlers[syms['pthread_create@GLIBC_2.2']]='thread:run'  # the click's worker, run at once
    return m
def buzzes(m): return sum(c[0]=='system@GLIBC_2.0' and m.text(c[1])=='cmd_mcu write_str buzzer' for c in m.calls)
def press(m,key=NEXT,gap=1000):
    """The window manager's key-down-before through the stock entry (iPod: the hook), leaving the stock
    latches as stock does. Returns its buzzer commands; the Key Tone byte must come back as it was."""
    tone=bytes(m.u.mem_read(TONE,1))
    m.down=m.call(key,address=KEYDOWN,event_type=O['EVT_KEY_DOWN_BEFORE'],gap=gap,debounce=True)
    assert bytes(m.u.mem_read(TONE,1))==tone
    return buzzes(m)
def lift(m,key=NEXT):
    """Its key-up-before, at once; m.ret is the hook's result."""
    tone=bytes(m.u.mem_read(TONE,1))
    m.ret=m.call(key,event_type=O['EVT_KEY_UP_BEFORE'],gap=0,debounce=True)
    assert bytes(m.u.mem_read(TONE,1))==tone and m.u.mem_read(SOUND_LATCH,1)==b'\0'
    return buzzes(m)
def tick(m,key=NEXT,gap=1000): return press(m,key,gap)+lift(m,key)
def debounced(m):
    """Stock's wheel lockout after a button runs out: on_wm_timer_setup_state counts it down."""
    for _ in range(8): assert m.call(address=syms['on_wm_timer_setup_state'],args=(0,0,0,0),gap=0,debounce=True)==8
    assert m.u.mem_read(KEY_LATCH,1)==b'\0'
PLAY_STATUS,OUTPUT_WAY=0xa50084,0xa50088  # mclGetPlayStatus, mclGetOutputWay: their stock leaves read these
def routed(m,status=3,way=0,po=0,bal=0):
    """Key Tone's routing (ringnav_buzzer): the stock leaves run on these play status, output way and jacks."""
    for n in ('mclGetPlayStatus','mclGetOutputWay'): m.handlers.pop(syms[n],None)
    m.word(PLAY_STATUS,status); m.word(OUTPUT_WAY,way); m.byte(syms['g_po_status'],po); m.byte(syms['g_bal_status'],bal)
    return m
# Every Key Tone click (stock's three and ringnav()'s) goes through buzzeer_switch, the Clicker's route
# (patch/clicker.c). Speaker (IPOD CLICKER 1): with nothing plugged in, the buzzer as stock, playing or not;
# with either jack plugged in or a Bluetooth (1) or USB (2) output, none; Key Tone off, none.
def clicker(value=None,tone=1,**kw):
    m=Machine()
    if value is not None: m.config['CLICKER']=value
    m.mock('send@GLIBC_2.0')
    return routed(audible(m,tone=tone),**kw)
def sent(m): return [m.text(c[2]) for c in m.calls if c[0]=='send@GLIBC_2.0']
for kw,want in (({'status':1},1),({},1),({'status':2},1),({'po':1},0),({'bal':1},0),({'way':1},0),
                ({'way':2},0),({'status':2,'po':1},0)):
    m=clicker('1',**kw); m.call(address=syms['buzzeer_switch'],args=(1,0,0,0))
    assert buzzes(m)==want and not sent(m),kw
m=clicker('1',tone=0); m.call(address=syms['buzzeer_switch'],args=(1,0,0,0)); assert not buzzes(m); passed()
# Both, the default (no CLICKER, whatever an old KEYTONEHP holds): the buzzer with either jack or BT/USB too,
# as Click With Headphones did; Key Tone off is still silent. Headphones (2) never sounds the buzzer: with no
# q2player (no /tmp/q2clicks) there is no click; with q2player, playing (status 2) or paused (3), it sends the click
# (q2player plays it with nothing playing too); Both sends it and buzzes.
for value,kw,tone,want in ((None,{'po':1},1,1),(None,{'bal':1},1,1),(None,{'way':1},1,1),(None,{'way':2},1,1),(None,{'po':1},0,0),
                           (None,{},0,0),('2',{},1,0),('2',{'po':1},1,0)):
    m=clicker(value,tone=tone,**kw)
    if value is None: m.config['KEYTONEHP']='0'
    m.call(address=syms['buzzeer_switch'],args=(1,0,0,0)); assert buzzes(m)==want and not sent(m),(value,kw,tone)
for value,status,buzz,mixed in (('2',2,0,1),('2',3,0,1),('0',2,1,1)):
    m=clicker(value,status=status); m.present.add('/tmp/q2clicks')
    m.call(address=syms['buzzeer_switch'],args=(1,0,0,0))
    assert buzzes(m)==buzz and sent(m)==['{mcl-click\\wheel}'][:mixed],(value,status)
passed()
# A reboot keeps the place (playback.c ringnav_reboot): Restart (device_reboot) and the firmware update's
# dialog, which stops the player before it flashes, save as power-off does (save_memoryplay_info, which
# ringnav_savequeue hooks) before stock runs, with the dialog's arguments; a stopped player (1) saves nothing.
for entry,stock in (('device_reboot','reboot'),('dialog_checkfw_dialog_init','checkfw')):
    for status in (1,2,3):
        m=Machine(); m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(PLAY_STATUS,status)
        m.handlers[payload_syms[f'stock_{stock}_trampoline']]='stock_'+stock
        win=m.node('window')  # the dialog's window: sw_update_restarting looks for stock's label in it
        m.call(address=syms[entry],args=(win,0x5678,0,0),gap=0)
        got=[c for c in m.calls if c[0] in ('save_memoryplay_info','stock_'+stock)]
        assert [c[0] for c in got]==['save_memoryplay_info'][:status!=1]+['stock_'+stock],(entry,status)
        assert stock!='checkfw' or got[-1][1:3]==(win,0x5678)
passed()

def long_list(n=40,m=None):
    m=m or Machine(); w,es=m.page_list(n,extent=n*48,name='allmusic_page'); m.paint(w); return m,w,es
def turn(m,w,gaps,key=NEXT):
    got=[]
    for gap in gaps: assert m.call(key,gap=gap)==11; got.append(m.selected(w))
    return got
# 1. Every tick moves, however soon after the last (the wheel's ticks are real travel).
# Short and long lists and virtual tables, from row 0.
for table in (False,True):
    for count in (16,40):
        for gaps in ((1000,80),(1000,140),(1000,100,100),(1000,50,100),(1000,100,141,100,100)):
            m=Machine()
            if table:
                w,rs,es=m.table_page(n=4,name='playerqueue_page',rebind=True); m.word(w+O['TABLE_ROWS'],count)
            else: w,es=m.page_list(count,extent=count*48)
            m.paint(w); assert turn(m,w,gaps)==list(range(1,len(gaps)+1)),(table,count,gaps)
            passed()
# Home as built: the same, its ends stop it however long the pause, and Centre opens the row that is highlighted.
m=Machine(); view,imgs=home_list(m); click_target(m,imgs[HOME_ROWS.index('coverflow')]); m.paint(view); last=len(HOME_ROWS)-1
assert turn(m,view,(1000,100,100,1000,100,100,100,100)[:last]+(100,400))==[*range(1,last+1),last,last]
assert turn(m,view,(1000,400,100,100),PREV)==[last-1,last-2,last-3,last-4]
assert m.confirm()==11 and m.dispatched()[0][1]==imgs[last-4]; passed()
# A BUTTONS dialog: the same between its buttons, and nothing scrolls.
m=Machine(); d=m.node('dialog','confirminfo_dialog'); m.word(d+O['W_PARENT'],m.wm); m.top=d
buttons=[m.entry(d,220) for _ in range(3)]; m.nodes[d]['children']=buttons
for b,x in zip(buttons,(20,147,274)): m.word(b+O['W_X'],x); m.word(b+O['W_W'],80); m.word(b+O['W_H'],80)
m.paint(d); got=[]
for gap in (1000,100,100): assert m.call(gap=gap)==11 and not m.moved(); got.append(m.selected(d))
assert got==[1,2,2] and m.confirm()==11 and m.dispatched()[0][1]==buttons[2]; passed()

# 4. List ends. Reaching an end ends the run, and a tick against an end changes nothing, however long the pause
# before it: no bump, no wrap. The bar is OLED's, in from each side by the selection's inset (oled/theme.xml
# list.selection).
SI=OLT['sel_inset']
def ring(name='playlist_page'):
    m=Machine(); m.config.update(FLAT); w,es=m.page_list(6,height=96,extent=288,name=name); m.paint(w)
    assert turn(m,w,(1000,)*5)==[1,2,3,4,5]
    return m,w,es
m,w,es=ring()
for gap in (100,100,200,200,299,300,1000):
    assert m.call(gap=gap)==11 and m.selected(w)==5 and m.get(w+O['SCROLL_Y'])==192,gap
    m.paint(w,gap=0); assert m.sel()==(SI,48,240-2*SI,48)  # on its row
assert turn(m,w,(100,)*5,PREV)==[4,3,2,1,0]
for gap in (100,300,1000):
    assert m.call(PREV,gap=gap)==11 and m.selected(w)==0 and m.get(w+O['SCROLL_Y'])==0
    m.paint(w,gap=0); assert m.sel()==(SI,0,240-2*SI,48)
assert turn(m,w,(100,100,100))==[1,2,3]; passed()
# Settings the same.
m,w,es=ring('display_page')
for gap in (100,300,1000):
    assert m.call(gap=gap)==11 and m.selected(w)==5
    m.paint(w,gap=0); assert m.sel()==(SI,48,240-2*SI,48)
passed()

# 5. Key Tone follows the row. Each tick is the pair the window manager delivers: key-down-before
# through the hook and the stock callback, then key-up-before. The count is the buzzer commands
# the real buzzeer_switch issues. On Home as built, four ticks leave rows 1, 2, 3, 4 and click
# once each; stock's one-click-per-press latch is set by the press and cleared by the
# release, as without the hook.
def home_machine(tone=1):
    m=audible(Machine(),tone); view,imgs=home_list(m); click_target(m,imgs[HOME_ROWS.index('coverflow')]); m.paint(view)
    return m,view
m,view=home_machine(); got=[]
for gap in (1000,100,100,1000):
    n=press(m,gap=gap); assert n==0 and m.down==0 and m.u.mem_read(SOUND_LATCH,1)==b'\x01'
    got.append((n+lift(m),m.selected(view))); assert m.ret==11
assert got==[(1,1),(1,2),(1,3),(1,4)],got; passed()
# Slow ticks click on every row; Home's end is silent however long the pause; a repaint and a long wait add
# nothing.
m,view=home_machine()
last=len(HOME_ROWS)-1
assert [tick(m) for _ in range(last)]==[1]*last and m.selected(view)==last
assert [tick(m,gap=gap) for gap in (1000,50,400,100)]==[0,0,0,0] and m.selected(view)==last
m.paint(view); assert not buzzes(m)
m.advance(10000); assert not buzzes(m); passed()
# buzzeer_switch also runs when it is silent, so its calls are not the count: stock always calls
# it on the press, with Key Tone held off; a row change adds the release's call.
m,view=home_machine(); seen=[]
m.u.hook_add(UC_HOOK_CODE,lambda u,a,size,x: seen.append(u.mem_read(TONE,1)[0]),begin=syms['buzzeer_switch'],end=syms['buzzeer_switch'])
assert tick(m)==1 and seen==[0,1] and tick(m,gap=100)==1 and seen==[0,1,0,1]; passed()
# An accelerated step is one click however many rows it takes (100ms ticks step
# 1,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8 rows).
m,w,es=long_list(100,audible(Machine()))
assert [tick(m,gap=100) for _ in range(16)]==[1]*16 and m.selected(w)==65; passed()
# A tick at an end is silent, however long the pause; the tick back off it clicks.
m=audible(Machine()); w,es=m.page_list(6,height=96,extent=288,name='playlist_page'); m.paint(w)
assert sum(tick(m) for _ in range(5))==5 and m.selected(w)==5
assert [tick(m,gap=gap) for gap in (100,200,200,300,1000)]==[0,0,0,0,0] and m.selected(w)==5
assert tick(m,PREV,gap=100)==1 and m.selected(w)==4; passed()
# Rejected input is silent: a finger down, a window animation (held, its tick plays after),
# stock's wheel lockout after a button (until its countdown ends), and a turn while the screen
# is locked still clicks as stock.
m,w,es=long_list(8,audible(Machine()))
for field in ('pressed','animating'):
    setattr(m,field,1); assert tick(m)==0 and m.ret==11 and m.selected(w)==0; setattr(m,field,0)
m.advance(O['HOLD_POLL_MS']); assert m.selected(w)==1 and {t[1] for t in m.timers.values()}=={payload_syms['sb_due']}  # the slide's tick plays after it; OLED's scrollbar fades after the step
assert press(m,RETURN)==1 and lift(m,RETURN)==0 and m.ret==0 and m.u.mem_read(KEY_LATCH,1)==b'\x08'
assert tick(m)==0 and m.ret==11 and m.selected(w)==1
debounced(m); assert tick(m)==1 and m.selected(w)==2; passed()
# Restoring the selection is not a step: a recreated page recalls its row on paint silently.
m,w,es=long_list(20,audible(Machine()))
assert [tick(m) for _ in range(6)]==[1]*6 and m.selected(w)==6
w2,es2=m.page_list(20,extent=960,name='allmusic_page'); m.paint(w2)
assert m.selected(w2)==6 and not buzzes(m); passed()
# Centre is a button: its press clicks as stock, and the delayed confirmation adds nothing.
m,w,es=long_list(8,audible(Machine()))
assert press(m,CENTER)==1 and lift(m,CENTER)==0 and m.ret==11
m.advance(O['DOUBLE_CLICK_MS']); assert m.clicks==[es[0]] and not buzzes(m); passed()

# 6. The Key Tone setting. Off: everything above is silent and the rows still move. The click
# uses the setting at the moment of the step, and the byte is never left changed: press() and
# lift() check it after every call, whatever its value.
m,view=home_machine(tone=0)
assert [tick(m) for _ in range(4)]==[0,0,0,0] and m.selected(view)==4 and m.u.mem_read(TONE,1)==b'\0'; passed()
m,view=home_machine()
assert press(m)==0; m.byte(TONE,0); assert lift(m)==0 and m.selected(view)==1      # switched off in between
assert press(m)==0; m.byte(TONE,1); assert lift(m)==1 and m.selected(view)==2      # and back on
m.byte(TONE,0x5a); assert tick(m)==1 and m.u.mem_read(TONE,1)==b'\x5a'; passed()
# The paths outside row navigation sound exactly as the stock binary does, press for press,
# and do what they did: the volume, a scrub, the carousels, the pixel-scroll fallback, the
# buttons, a held key, a double press, and the wheel with the screen off or locked.
def stock(keys,**flags):
    s=audible(Machine(patched=False))
    for flag,value in flags.items(): s.byte(syms[flag],value)
    out=[]
    for key in keys:
        out.append(press(s,key)); s.call(key,address=HOOKS['on_wm_keyup_before_fun'][0],gap=0,debounce=True)
    return out
m=audible(Machine()); m.page('playing_page')
assert [tick(m,key) for key in (NEXT,PREV,NEXT)]==stock((NEXT,PREV,NEXT))==[1,1,1] and m.ret==0; passed()
m=audible(scrub_page()); assert m.call(O['KEY_CENTER'],gap=1000)==11; debounced(m)  # one press scrubs (the ticks below move the value); the press's stock wheel lockout runs out
assert [tick(m,gap=100) for _ in range(3)]==stock((NEXT,)*3)==[1,1,1] and m.nodes[m.slider]['value']==130; passed()
m=audible(CoverflowMachine()); m.open()
assert [tick(m,gap=20) for _ in range(2)]==[1,1] and slide(m,m.slide)[3]==120; passed()
m=audible(Machine()); m.page('sysset_page','slide_menu')
assert tick(m)==1 and [c[0] for c in m.moved()]==['slide_menu_scroll_to_next']; passed()
m=audible(Machine()); w=m.page(t='table_client')
assert [tick(m,gap=100) for _ in range(3)]==[1,1,1] and m.get(w+O['TABLE_TOP'])==288; passed()
keys=(CENTER,RETURN,O['KEY_PLAY'],222,223)
m,w,es=long_list(8,audible(Machine()))
assert [tick(m,key) for key in keys]==stock(keys)==[1]*5 and m.selected(w)==0; passed()
# Either jack or Bluetooth in: under Speaker (IPOD CLICKER 1) the buttons and a row change are silent; under
# Both, the default, the buzzer clicks them as before.
for kw in ({'po':1},{'bal':1},{'way':1}):
    for value,want in (('1',0),(None,1)):
        mm=Machine()
        if value: mm.config['CLICKER']=value
        m,w,es=long_list(8,routed(audible(mm),**kw))
        assert tick(m)==want and m.selected(w)==1 and [tick(m,key) for key in keys]==[want]*5,(kw,value)
        assert m.u.mem_read(TONE,1)==b'\x01'
passed()
# Music playing with nothing plugged in: the speaker still clicks.
m,w,es=long_list(8,routed(audible(Machine()),status=2))
assert tick(m)==1 and m.selected(w)==1 and [tick(m,key) for key in keys]==[1]*5; passed()
# A held key clicks once: stock's latch holds through its repeats and the long-press event.
m,w,es=long_list(8,audible(long_machine()))
assert [press(m,RETURN),press(m,RETURN,gap=500)]==[1,0]; long_return(m); assert not buzzes(m) and lift(m,RETURN)==0
assert len(destinations(m))==0 and press(m,RETURN)==1; passed()
m,w,es=long_list(3,audible(Machine()))
assert press(m,CENTER)==1 and m.release()==11 and press(m,CENTER,gap=100)==1 and m.release()==0 and m.screens==[0]; passed()
for flags in (dict(g_backlight_status=0),dict(g_backlight_status=0,g_keylock_flag=1,g_keylock_mode=1),
              dict(g_lockscreen_pageflag=1),dict(g_testmode_flag=1)):
    m,w,es=long_list(8,audible(Machine()))
    for flag,value in flags.items(): m.byte(syms[flag],value)
    locked='g_keylock_flag' in flags
    first=tick(m); ui_pass(m)  # each key its own pass, as its 1000 ms gap makes it: dark, a pass clicks once
    assert [first,tick(m,CENTER)]==stock((NEXT,CENTER),**flags)==[0 if locked else 1,1],flags
    assert m.selected(w)==0; passed()
# Screen off, the ticks one idle slept through are dispatched in one pass and click once (tone_dark); the
# next pass clicks again. Lit, each clicks, as stock.
for lit,want in ((0,[1,0,1]),(1,[1,1,1])):
    m,w,es=long_list(8,audible(Machine())); m.byte(syms['g_backlight_status'],lit)
    got=[tick(m),tick(m)]; ui_pass(m); assert got+[tick(m)]==want,lit
passed()
# Between a press and its release the page may change. List to volume: the silenced press is
# spent, the volume step is stock's, and the next press there clicks. Volume to list: stock
# already clicked, so the row change adds none.
m,w,es=long_list(8,audible(Machine())); lst=m.top
assert press(m)==0; m.page('playing_page'); assert lift(m)==0 and m.ret==0 and m.selected(w)==0
assert tick(m)==1 and m.ret==0
assert press(m)==1; m.top=lst; assert lift(m)==0 and m.ret==11 and m.selected(w)==1
assert tick(m)==1 and m.selected(w)==2; passed()
# A recreated page between the two, or a surface rebuilt at the same address: one click for the
# one row change on the live list.
m,w,es=long_list(8,audible(Machine()))
assert press(m)==0; w2,es2=m.page_list(8,extent=8*48,name='allmusic_page'); m.paint(w2,gap=0)
assert lift(m)==1 and m.selected(w2)==1 and m.selected(w)==0
wiped(m,w2); m.paint(w2,gap=0); assert m.selected(w2)==1 and tick(m)==1 and m.selected(w2)==2; passed()
# A dropped release (AWTK aborts pressed keys when windows change) leaves nothing behind. The
# next pair on the list clicks once. A new press elsewhere is stock's again: stock's own latch,
# still set by the dropped press, skips one click there, then the volume clicks as before. A
# release without its press moves the row silently, and a button's press takes the ownership
# back (stock's lockout then rejects that wheel release). Key Tone is never left off.
m,w,es=long_list(8,audible(Machine())); lst=m.top
assert press(m)==0 and tick(m)==1 and m.selected(w)==1
assert press(m)==0; m.page('playing_page'); assert [tick(m),tick(m)]==[0,1] and m.ret==0
m.top=lst; assert lift(m)==0 and m.ret==11 and m.selected(w)==2
assert press(m)==0 and press(m,RETURN)==0 and lift(m)==0 and m.ret==11 and m.selected(w)==2
assert lift(m,RETURN)==0 and press(m,RETURN)==1 and lift(m,RETURN)==0
debounced(m); assert tick(m)==1 and m.selected(w)==3
m.advance(10000); assert not buzzes(m) and m.u.mem_read(TONE,1)==b'\x01'; passed()
CONFIG.clear()

def fnv(s,h=2166136261):
    for c in s.encode(): h=((h^c)*16777619)&0xffffffff
    return (h*16777619)&0xffffffff

