class BtInitMachine(Machine):
    def hook(self,u,addr,size,_):
        if self.handlers.get(addr)=='btctl_create_daemon_thread':
            self.calls.append(('btctl_create_daemon_thread',u.reg_read(REGS[0]),0))
            self.word(u.reg_read(REGS[0]),0x12345678)
            self.returns(self.result,clobber=True)
            return
        super().hook(u,addr,size,_)
for result in (0,-7):
    bt_init=BtInitMachine(); bt_init.result=result; thread=bt_init.alloc(4)
    bt_init.mock('system@GLIBC_2.0')  # bt_name.c's Alias at Bluetooth on (dbus-send), between the callbacks
    assert bt_init.call(address=payload_syms['headset_bt_init'],args=(thread,0,0,0),gap=0)==result
    assert bt_init.calls[0][0]=='initBluetoothParam'
    assert [c[:3] for c in bt_init.calls[1:] if c[0].startswith('btctl')]==[
        ('btctl_set_prompt_callback',payload_syms['bt_pair_prompt'],0),  # bt_pair.c's, in place of stock's 0x4f9d20
        ('btctl_device_status_callback',payload_syms['headset_bt_status'],0),
        ('btctl_set_a2dp_callback',syms['btrecvA2dpFunc'],0x01020304),
        ('btctl_create_daemon_thread',thread,0)]
    assert bt_init.get(thread)==0x12345678
    passed()
# An incoming link while a link thread waits for the saved device (BT_STATUS) is adopted; auto-link
# skips while a listed one is connected (BT_AUTOLINK).
from build import BT_STATUS, BT_AUTOLINK, BT_PROMPT
# openBluetooth sets stock's prompt callback (0x4f9d20) at every Bluetooth on, after headset_bt_init: its call is
# bt_pair_register's, which sets bt_pair.c's whatever it is handed (docs/internals.md#bluetooth-pairing-card).
assert struct.unpack_from('<I',demo,fileoff(demo,BT_PROMPT[0]))[0]==jal(payload_syms['bt_pair_register'])
on=Machine(); on.mock('system@GLIBC_2.0')
on.call(address=payload_syms['bt_pair_register'],args=(0x4f9d20,0,0,0),gap=0)
assert [c[:3] for c in on.calls if c[0]=='btctl_set_prompt_callback']==[
    ('btctl_set_prompt_callback',payload_syms['bt_pair_prompt'],0)]
passed()
assert struct.unpack_from('<I',demo,fileoff(demo,BT_STATUS[0]))[0]==jal(payload_syms['headset_status_register'])
assert struct.unpack_from('<I',demo,fileoff(demo,BT_AUTOLINK[0]))[0]==jal(payload_syms['headset_bt_autolink'])
class BtLinkMachine(Machine):
    def __init__(self,links,paired=True,connecting=1,saved='AA:AA:AA:AA:AA:AA'):
        super().__init__(); self.links=[list(x) for x in links]; self.paired=paired
        self.u.mem_write(0xa4c0d8,saved.encode()+b'\0')  # BTLINKMAC
        self.word(syms['bt_connectstatus'],connecting); self.handlers[0x4fc180]='device_status'
        self.mock('system@GLIBC_2.0')  # bt_name.c's Alias as Bluetooth comes on (dbus-send)
    def hook(self,u,addr,size,_):
        name=self.handlers.get(addr)
        if name not in ('getBtLinkStatus','getBtLinkSize','getBtLinkItem','addBtLinkItem','updateBtLinkStatus','checkPairDev','device_status'):
            return super().hook(u,addr,size,_)
        assert u.reg_read(UC_MIPS_REG_T9)==addr, (name,'PIC call missing t9')
        a,b,c,d=[u.reg_read(r) for r in REGS]; self.calls.append((name,a,b,c,d)); ret=0
        if name=='getBtLinkStatus': ret=next((st for x,st in self.links if x==self.text(a)),0)
        elif name=='getBtLinkSize': ret=len(self.links)
        elif name=='getBtLinkItem' and 0<=signed(a)<len(self.links):
            self.word(b,self.links[a][1]); self.u.mem_write(c,self.links[a][0].encode()+b'\0')
        elif name=='addBtLinkItem': self.links.append([self.text(a),b])
        elif name=='updateBtLinkStatus':
            for x in self.links:
                if x[0]==self.text(b): x[1]=a; ret=1
        elif name=='checkPairDev': ret=self.paired
        self.returns(ret,clobber=True)
a_mac,b_mac='AA:AA:AA:AA:AA:AA','BB:BB:BB:BB:BB:BB'
link=BtLinkMachine([]); link.call(address=payload_syms['headset_status_register'],args=(0,0,0,0),gap=0)
assert [c[:3] for c in link.calls if c[0].startswith('btctl')]==[('btctl_device_status_callback',payload_syms['headset_bt_status'],0)]
link=BtLinkMachine([(a_mac,5)])  # 5 while the thread's Connect runs
link.call(address=payload_syms['headset_bt_status'],args=(3,link.string(b_mac),0,0),gap=0)
assert link.links==[[a_mac,4],[b_mac,3]] and 'device_status' not in [c[0] for c in link.calls]; passed()
# Otherwise stock's callback gets the event as libbtctl sent it: not connecting, thread ended or judged,
# already listed, unpaired, empty list, target connected or not yet taken by the thread (1), a thread
# for another device, other events.
for event,links,paired,connecting,saved in ((3,[(a_mac,5)],True,0,a_mac),(3,[(a_mac,5)],True,2,a_mac),
        (3,[(a_mac,5)],True,3,a_mac),(3,[(a_mac,5),(b_mac,1)],True,1,a_mac),(3,[(a_mac,5)],False,1,a_mac),
        (3,[],True,1,''),(3,[(a_mac,3)],True,1,a_mac),(3,[(a_mac,1)],True,1,a_mac),(3,[(a_mac,5)],True,1,'CC:CC:CC:CC:CC:CC'),
        (4,[(a_mac,5)],True,1,a_mac)):
    link=BtLinkMachine(links,paired,connecting,saved); addr=link.string(b_mac)
    link.call(address=payload_syms['headset_bt_status'],args=(event,addr,0x1234,0x5678),gap=0)
    assert link.links==[list(x) for x in links] and link.calls[-1]==('device_status',event,addr,0x1234,0x5678)
    passed()
for links,paired,want in (([],True,1),([],False,0),([(b_mac,3)],True,0),([(b_mac,4)],True,1),
                          ([(b_mac,5)],True,1),([(b_mac,3),(a_mac,5)],True,0)):
    link=BtLinkMachine(links,paired)
    assert link.call(address=payload_syms['headset_bt_autolink'],args=(link.string(a_mac),0,0,0),gap=0)==want
    passed()
# Quality's click on a live link (BT_QUALITY): stock's own two patched instructions run, from the read of
# bt_linkstatus to the branch that takes its answer, with the choice in $s0 as the click holds it. A choice of
# another codec starts the switch in place and answers 0, stock's tail for no link (no dialog, no btDisconnect);
# LDAC to LDAC answers the link, stock's reconnect. $gp comes back as the click's frame saved it, whatever the
# calls on the way left in it: the click's next instruction reads bt_showcoding through it.
from build import BT_QUALITY
assert struct.unpack_from('<II',demo,fileoff(demo,BT_QUALITY[0][0]))==(jal(payload_syms['bt_quality_site']),0x02002025)
class QualityMachine(Machine):
    def __init__(self,linked,codec):
        super().__init__(); self.commands=[]
        self.byte(syms['bt_linkstatus'],linked); self.word(syms['bt_showcoding'],codec)
        self.handlers[syms['system@GLIBC_2.0']]='system'
        self.handlers[syms['pthread_create@GLIBC_2.2']]='thread:run'  # bt_shell's thread, run at once
    def hook(self,u,addr,size,_):
        if self.handlers.get(addr)!='system': return super().hook(u,addr,size,_)
        self.commands.append(self.text(u.reg_read(REGS[0]))); u.reg_write(UC_MIPS_REG_GP,0x5a5a5a5a)  # libc's own $gp
        self.returns(0,clobber=True)
    def pick(self,row):
        u=self.u; u.reg_write(UC_MIPS_REG_SP,0x7000f000); self.word(0x7000f010,0xa3aae0)  # the click's saved $gp
        u.reg_write(UC_MIPS_REG_GP,0x11111111); u.reg_write(UC_MIPS_REG_S0,row)
        u.emu_start(BT_QUALITY[0][0],BT_QUALITY[1][0]+4,count=self.budget)
        assert u.reg_read(UC_MIPS_REG_PC)==BT_QUALITY[1][0]+4 and u.reg_read(UC_MIPS_REG_SP)==0x7000f000
        assert u.reg_read(UC_MIPS_REG_GP)==0xa3aae0 and u.reg_read(UC_MIPS_REG_S0)==row
        return u.reg_read(UC_MIPS_REG_V0)
for linked,codec,row,answer,order in ((1,13,4,0,'aptX LDAC aptX-HD AAC SBC'),(1,13,7,0,'SBC'),(1,1,3,0,'aptX-HD LDAC aptX AAC SBC'),
                                      (1,13,3,0,None),(1,4,0,1,None),(1,1,6,1,None),(0,13,4,0,None),(1,0,4,0,None)):
    q=QualityMachine(linked,codec)
    assert q.pick(row)==answer
    assert [c for c in q.commands if 'q2bt-codec' in c]==([] if order is None else [c for c in q.commands if f'for c in {order}; do q2bt-codec "$p" $c' in c])
    assert len(q.commands)==(order is not None)
    passed()
# Bluetooth volume runs through the shared hook. Mock only headset I/O
# and stock gain; device_set_volume itself executes its native output routing.
class OutputVolumeMachine(Machine):
    def __init__(self,config=()):
        super().__init__(); self.way=0; self.config.update(config)
        self.byte(syms['g_volume'],20); self.byte(syms['g_maxvolume'],100)
        self.handlers.pop(syms['write_int_config'])
        for name,label in (('mclSetOutputWay','output'),('write_int_config','write_config')):
            self.handlers[HOOKS[name][0]+12]='stock_'+label
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name not in ('mclGetOutputWay','stock_output','stock_write_config'):
            return super().hook(u,address,size,unused)
        a,b,c=[u.reg_read(r) for r in REGS[:3]]
        self.calls.append((name,a,b,c))
        if name=='stock_output': self.way=a; ret=1
        elif name=='stock_write_config': self.config[self.text(c)]=str(a); ret=0
        else: ret=self.way
        self.returns(ret)
    def output(self,way):
        changed=(self.way==1)!=(way==1)
        assert self.call(address=syms['mclSetOutputWay'],args=(way,0,0,0),gap=0)==1
        volume=self.u.mem_read(syms['g_volume'],1)[0]
        assert [c[1:3] for c in self.calls if c[0]=='device_set_volume']==([(volume,1)] if changed else [])
        return volume
    def save(self,volume,section='PLAYSET',key='VOLUME',settle=True):
        self.byte(syms['g_volume'],volume)
        ret=self.call(address=syms['write_int_config'],args=(volume,self.string(section),self.string(key),0),gap=0)
        if settle: self.call(address=payload_syms['config_settle'],args=(1,0,0,0),gap=0)  # held until it settles (config_ini.c)
        return ret

ov=OutputVolumeMachine(); assert ov.output(1)==20  # first connection inherits the current level
ov.save(65,settle=False); assert ov.config=={'VOLUME':'20'}  # a wheel step's level is held, not written yet
ov.call(address=payload_syms['config_settle'],args=(1,0,0,0),gap=0); assert ov.config=={'VOLUME':'20','BT_VOLUME':'65'}
assert ov.output(0)==20; ov.save(13)
assert ov.output(1)==65 and ov.output(1)==65 and ov.output(2)==13
assert ov.output(0)==13 and ov.output(1)==65; passed()
# A fresh boot uses wired VOLUME; reconnect restores Bluetooth, including muted and capped levels.
for value,limit,want in (('0',100,0),('100',40,40),('37',100,37)):
    ov=OutputVolumeMachine({'VOLUME':'13','BT_VOLUME':value}); ov.byte(syms['g_volume'],13)
    ov.byte(syms['g_maxvolume'],limit)
    assert ov.output(1)==want and ov.output(0)==13; passed()
for value in ('','-1','101','999999999999999999','20junk',' 20'):
    ov=OutputVolumeMachine({'BT_VOLUME':value}); assert ov.output(1)==20; passed()
ov=OutputVolumeMachine(); ov.output(1); ov.save(7,'OTHER','VOLUME'); ov.save(8,'PLAYSET','OTHER')
assert ov.config=={'VOLUME':'7','OTHER':'8'}; passed()  # other writes keep their section/key

class BtVolumeMachine(Machine):
    def __init__(self,link=True):
        super().__init__()
        if link: self.config['BTVOLLINK']='1'  # Bluetooth's Link Volume
        self.absolute=10; self.write_result=0; self.way=1
        self.byte(syms['g_bluetoothflag'],1); self.byte(syms['bt_linkstatus'],1)
        self.word(syms['bt_showcoding'],1); self.byte(syms['g_volume'],20); self.byte(syms['g_maxvolume'],100)
        self.handlers[HOOKS['mclSetBtVol'][0]+12]='stock_btvol'
        self.handlers[HOOKS['main_loop_sleep_default'][0]+12]='stock_sleep'
        self.mock('mclSetLocalVol','mclUsbAudioSetVol','notifyVolume','airplaySendVol')
        self.handlers.pop(syms['device_set_volume'],None)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name not in ('btctl_transport_get_volume','btctl_transport_set_volume','mclGetOutputWay'):
            return super().hook(u,address,size,unused)
        a=u.reg_read(REGS[0]); self.calls.append((name,a,0,0))
        if name=='btctl_transport_get_volume': ret=self.absolute
        elif name=='mclGetOutputWay': ret=self.way
        else:
            ret=self.write_result
            if ret==0: self.absolute=a
        self.returns(ret)
    def volume(self,left,right):
        self.call(address=HOOKS['mclSetBtVol'][0],args=(left,right,0,0),gap=0)
        return [(signed(c[1]),signed(c[2])) for c in self.calls if c[0]=='stock_btvol']
    def writes(self): return [c[1] for c in self.calls if c[0]=='btctl_transport_set_volume']
    def poll(self,gap=400):
        self.call(address=HOOKS['main_loop_sleep_default'][0],args=(self.wm,0,0,0),gap=gap)

# The headset's level is the volume's share of 127 (bt_vol_avrcp, docs/internals.md#headset-controls-and-volume): 20 is
# 25, 30 is 38, 40 is 51, 50 is 64, 60 is 76.
for left,right,want in ((0,0,0),(0,20,25),(20,0,25),(1,1,1),(20,20,25),(40,30,51),(50,50,64),(100,100,127),
                        (-5,-1,0),(101,200,127)):
    gain=(max(0,left)*100//max(left,right),max(0,right)*100//max(left,right)) if want else (left,right)  # AVRCP 0 isn't silent on every headset: keep stock's mute
    b=BtVolumeMachine(); assert b.volume(left,right)==[gain] and b.writes()==[want]
    assert b.volume(left,right)==[gain] and not b.writes(); passed()
# Link Volume off (the default): the Q2's volume is stock's gain alone, the device's own
# volume is never read or written, and the poll leaves it be.
b=BtVolumeMachine(link=False); assert b.volume(20,20)==[(20,20)] and b.volume(40,30)==[(40,30)]
b.poll(); b.poll(); assert not [c for c in b.calls if c[0].startswith('btctl_')]; passed()
# Volume 0 and back up: the headset gets 0 then 1, stock gain 0 then full.
b=BtVolumeMachine(); assert b.volume(0,0)==[(0,0)] and b.writes()==[0]
assert b.volume(1,1)==[(100,100)] and b.writes()==[1]; passed()
b=BtVolumeMachine(); b.byte(syms['g_volume'],0); b.poll()
assert b.writes()==[0] and [c[1:3] for c in b.calls if c[0]=='stock_btvol']==[(0,0)]; passed()
for cur in (-1,128,255):
    b=BtVolumeMachine(); b.absolute=cur
    assert b.volume(20,30)==[(20,30)] and not b.writes(); passed()
b=BtVolumeMachine(); b.write_result=-1; b.absolute=40  # not 30's level already: a write it refuses
assert b.volume(20,30)==[(20,30)] and b.writes()==[38]; passed()
for name,value in (('g_bluetoothflag',0),('bt_linkstatus',0),('bt__recv_pageflag',1),('bt_showcoding',0)):
    b=BtVolumeMachine()
    (b.word if name=='bt_showcoding' else b.byte)(syms[name],value)
    assert b.volume(20,30)==[(20,30)] and not b.writes(); passed()
for way in (0,2):
    b=BtVolumeMachine(); b.way=way
    assert b.volume(20,30)==[(20,30)] and not b.writes(); passed()
b=BtVolumeMachine(); b.absolute=-1; b.poll()
assert not b.writes()
b.absolute=10; b.poll(O['BT_VOLUME_SLOW_MS']-1); assert not b.writes()  # no absolute volume yet: the slow pace
b.poll(1); assert b.writes()==[25]
b.absolute=5; b.poll(); assert not b.writes()  # a level of the headset's own reads as its nearest volume, 4
b.byte(syms['bt_linkstatus'],0); b.poll(); assert not b.writes()
b.byte(syms['bt_linkstatus'],1); b.word(syms['bt_showcoding'],0); b.poll(); assert not b.writes()
b.word(syms['bt_showcoding'],1); b.poll(); assert not b.writes(); passed()
b=BtVolumeMachine(); b.write_result=-1; b.poll()
assert b.writes()==[25] and [c[1:3] for c in b.calls if c[0]=='stock_btvol']==[(20,20)]
b.write_result=0; b.poll(); assert not b.writes()  # a refusal retries at the slow pace
b.poll(O['BT_VOLUME_SLOW_MS']-400); assert b.writes()==[25]; passed()
# Dark, the read-back slows down.
b=BtVolumeMachine(); b.poll(); b.calls=[]; b.byte(syms['g_backlight_status'],0); b.absolute=50
b.poll(); assert not any(c[0]=='btctl_transport_get_volume' for c in b.calls)
b.poll(O['BT_VOLUME_SLOW_MS']-400); assert any(c[0]=='btctl_transport_get_volume' for c in b.calls); passed()
# Dark with a volume limit, the headset's own keys are still held to it, every BT_VOLUME_LIMIT_MS.
b=BtVolumeMachine(); b.byte(syms['g_maxvolume'],60); b.poll(); b.byte(syms['g_backlight_status'],0); b.absolute=127
b.poll(); assert not b.writes()
b.poll(O['BT_VOLUME_LIMIT_MS']-400); assert b.writes()==[76] and b.u.mem_read(syms['g_volume'],1)[0]==60; passed()
# A Bluetooth device's Volume Limit (bt_vol_limit.c): the headset's own keys are pulled back to it, Link Volume on, as
# to Max Volume, and the lower of the two holds; another device connected has none. Stock's link list's last item is
# the device, at status 3.
class BtLimitMachine(BtVolumeMachine):
    def __init__(self,mac='AA:BB:CC:DD:EE:01'):
        super().__init__(); self.mac=mac
        self.handlers[syms['getBtLinkSize']]='getBtLinkSize'; self.handlers[syms['getBtLinkItem']]='getBtLinkItem'
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='getBtLinkSize': self.returns(1); return
        if name=='getBtLinkItem':
            self.word(u.reg_read(REGS[1]),3); u.mem_write(u.reg_read(REGS[2]),self.mac.encode()+b'\0'); self.returns(0); return
        return super().hook(u,address,size,unused)
    def limit(self,v,mac='AA:BB:CC:DD:EE:01'):
        self.call(address=payload_syms['btlim_set'],args=(self.string(mac),v,0,0),gap=0)
for top,limit,want in ((100,40,40),(30,40,30),(100,100,100)):
    b=BtLimitMachine(); b.byte(syms['g_maxvolume'],top); b.limit(limit); b.poll(); b.calls=[]; b.absolute=127  # its keys, up to the top
    b.poll(); assert b.u.mem_read(syms['g_volume'],1)[0]==want and b.writes()==([] if want==100 else [(127*want+50)//100]),(top,limit)
    passed()
b=BtLimitMachine(mac='AA:BB:CC:DD:EE:02'); b.limit(40); b.poll(); b.absolute=127; b.poll()
assert b.u.mem_read(syms['g_volume'],1)[0]==100 and not b.writes(); passed()  # another device: no limit
# A device's Volume Curve (bt_vol_limit.c): Gentle sends the 40 dB curve's level (50 is 16, 40 is 13, 20 is 6), and
# the choice reaches the connected headset at once through stock's device_set_volume; another device stays Even.
def gentle(b,mac='AA:BB:CC:DD:EE:01',c=1):
    b.call(address=payload_syms['btlim_curve_set'],args=(b.string(mac),c,0,0),gap=0)
b=BtLimitMachine(); assert b.volume(50,50)==[(100,100)] and b.writes()==[64]
b.byte(syms['g_volume'],50); b.calls=[]; gentle(b); assert b.writes()==[16] and b.absolute==16
b.calls=[]; assert b.volume(20,20)==[(100,100)] and b.writes()==[6]
b.calls=[]; gentle(b,c=0); assert b.writes()==[64]; passed()  # back to Even, sent again
b=BtLimitMachine(mac='AA:BB:CC:DD:EE:02'); gentle(b); assert b.volume(50,50)==[(100,100)] and b.writes()==[64]; passed()
# The limit is on the Q2's scale on either curve: the headset's keys at its top held to 40, sent 40's level.
for curve,level in ((0,51),(1,13)):
    b=BtLimitMachine(); gentle(b,c=curve); b.limit(40); b.poll(); b.calls=[]; b.absolute=127
    b.poll(); assert b.u.mem_read(syms['g_volume'],1)[0]==40 and b.writes()==[level],(curve,b.writes()); passed()
# A headset that can't report absolute volume is asked again at the slow pace, lit too, not every 400 ms.
b=BtVolumeMachine(); b.absolute=-1; asks=[]
for _ in range(2*O['BT_VOLUME_SLOW_MS']//400): b.poll(); asks.append(sum(c[0]=='stock_btvol' for c in b.calls))
assert asks==[1,0,0,0,0]*2; passed()
# Native device_set_volume calls LocalVol and BtVol for the DAC/Bluetooth, USB's own
# setter for USB. g_output_way=0 avoids stock's fixed line-out branch; notify is covered too.
for way,soft in ((0,0),(1,0),(2,0),(2,1)):
    b=BtVolumeMachine(); b.handlers.pop(syms['device_set_volume'],None)
    b.byte(syms['g_output_way'],0); b.way=way; b.byte(syms['g_usbvol_mode'],soft)
    b.call(address=syms['device_set_volume'],args=(20,1,0,0),gap=0)
    gains=[c[1:3] for c in b.calls if c[0]=='stock_btvol']
    local=[c[1:3] for c in b.calls if c[0]=='mclSetLocalVol']
    usb=[c[1:3] for c in b.calls if c[0]=='mclUsbAudioSetVol']
    assert gains==([(100,100)] if way==1 else [(20,20)] if way==0 else [])
    assert local==([(20,20)] if way in (0,1) else [])
    assert usb==([(20,20)] if soft else [(100,100)]) if way==2 else not usb
    assert b.writes()==([25] if way==1 else [])
    assert any(c[0]=='notifyVolume' for c in b.calls); passed()

# Screen off, the UI loop idles SCREEN_OFF_SLEEP_MS before stock's own pacing; screen on, stock alone.
# Stock's sleep is the pass's last call but the UI pass watchdog's clock read (ui_watch.c ui_watch_woke).
sleep_hook=HOOKS['main_loop_sleep_default'][0]
for light,want in ((1,[]),(0,[O['SCREEN_OFF_SLEEP_MS']])):
    s=Machine(); s.handlers[sleep_hook+12]='stock_sleep'; s.byte(syms['g_backlight_status'],light)
    assert s.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0)==0
    slept=max(i for i,c in enumerate(s.calls) if c[0]=='stock_sleep')
    assert [c[1] for c in s.calls if c[0]=='sleep_ms']==want and s.calls[slept][:2]==('stock_sleep',0x1234)
    assert {c[0] for c in s.calls[slept+1:]}<={'time_now_ms'}, s.calls[slept+1:]; passed()
    # The next pass with the backlight on repaints the whole screen once; staying on, nothing more.
    s.byte(syms['g_backlight_status'],1)
    for wake in (not light,False):
        s.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0)
        assert [c[1] for c in s.calls if c[0]=='widget_invalidate_force']==([s.wm] if wake else [])
    passed()
# Screen off, the window manager does not paint: stock blanks the framebuffer but paints on. A window
# animation still paints.
for light,animating in ((1,0),(0,0),(0,1)):
    s=Machine(); s.handlers[sleep_hook+12]='stock_sleep'
    vt=s.alloc(16); s.word(s.wm+0x94,vt); s.word(vt+0xc,0x400100); s.handlers[0x400100]='wm_vt_paint'
    s.byte(syms['g_backlight_status'],light); s.animating=animating
    s.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0)  # the pass that sees the screen go off
    s.calls=[]; s.call(address=WM_PAINT_LEAF[1],args=(s.wm,0,0,0),gap=0,clear=False)
    assert ('wm_vt_paint' in [c[0] for c in s.calls])==bool(light or animating), (light,animating)
passed()
for want in (1,3):
    m.call(address=syms['mclNextSong'],args=(0,0,0,0),gap=0)
    assert m.picks[-1]==1 and m.mcl('MCL_POS')==want and any(c[0]=='mclStartPlayer' for c in m.calls)
passed()
# Previous then Next in stock Shuffle returns to the track Previous left, not a new random pick.
m=QueueMachine(mode=2); m.mock('pthread_mutex_lock@GLIBC_2.0','pthread_mutex_unlock@GLIBC_2.0')
m.call(address=syms['mclNextSong'],args=(0,0,0,0),gap=0); m.word(O['MCL_POS'],1)
m.call(address=syms['mclPrevSong'],args=(0,0,0,0),gap=0); assert m.picks[-1]==0 and m.mcl('MCL_POS')==0
for want in (1,2):
    m.call(address=syms['mclNextSong'],args=(0,0,0,0),gap=0); assert m.mcl('MCL_POS')==want
passed()
