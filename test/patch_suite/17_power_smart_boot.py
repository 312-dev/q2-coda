# The power poll, on the UI loop. Charge limit: the charger stops (switch_charge_enable(0), the
# active-low charge enable) at CHARGE_STOP, again whenever it charges meanwhile, and is handed
# back at CHARGE_RESUME: on, or USB mode's own choice while its page is open. Every CHARGE_POLL_MS.
class PowerMachine(ResumeMachine):
    def __init__(self,config={},files=None):
        super().__init__(files); self.config.update(config); self.writes=[]; self.records=[]  # records: the state record's
        for n in ('fflush@GLIBC_2.0','fsync@GLIBC_2.0','fileno@GLIBC_2.0','unlink@GLIBC_2.0','open@GLIBC_2.0','close@GLIBC_2.0',
                  'read@GLIBC_2.0','write@GLIBC_2.0'):
            self.handlers[syms[n]]='p:'+n  # read, write: heatguard_poll's (the base machine's access finds no q2guard)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a=u.reg_read(REGS[0])
        if name.startswith('r:fclose') and self.open.get(a,[0,0])[1]=='w':
            path,_,_,data=self.open.pop(a); self.files[path]=data; (self.records if path==RECORD else self.writes).append((path,data)); self.calls.append(('fclose',a,0,0))
            self.returns(0); return
        if name.startswith(('p:fflush','p:fileno','p:fsync')) and (a==RECORD_FD or self.open.get(a,[''])[0]==RECORD):
            self.returns(RECORD_FD if name.startswith('p:fileno') else 0); return  # the record's sync, apart from the marker's
        if name.startswith('r:fopen') and self.text(u.reg_read(REGS[1]))=='w':
            ret=0x2000000+len(self.calls); self.open[ret]=[self.text(a),'w',0,b'']; self.calls.append(('fopen',a,0,0))
            self.returns(ret); return
        if not name.startswith('p:'): return super().hook(u,address,size,unused)
        self.calls.append((name[2:].split('@')[0],a,0,0))
        if name.startswith('p:unlink'): self.files.pop(self.text(a),None)
        self.returns(0)
    def pass_(self,ms=O['CHARGE_POLL_MS']):
        self.now+=ms; self.calls=[]
        assert self.call(address=sleep_hook,args=(0x1234,0,0,0),gap=0)==0
        return [c[1] for c in self.calls if c[0]=='switch_charge_enable'],[c[1] for c in self.calls if c[0]=='sleep_ms']
def level(m,pct,charging=0): m.word(syms['g_power_capacity'],pct); m.word(syms['g_power_chargestate'],charging)
m=PowerMachine({'CHARGELIMIT':'1'})
for pct,charging,want in ((79,2,[]),(80,2,[0]),(79,0,[]),(78,2,[0]),(76,0,[]),(75,0,[1]),(79,2,[])):
    level(m,pct,charging); assert m.pass_()[0]==want,(pct,charging); passed()
level(m,90); assert m.pass_(O['CHARGE_POLL_MS']-1)[0]==[] and m.pass_(1)[0]==[0]; passed()  # polled, not every pass
m.open_pages=['usbmode_page']; m.byte(syms['g_usbdac_chargeflag'],0); level(m,70); assert m.pass_()[0]==[0]; passed()
m=PowerMachine(); level(m,95,2); assert m.pass_()[0]==[]; passed()  # the limit off: stock charges
# The screen-off pass idles SCREEN_OFF_SLEEP_MS, and a screen-on pass with no input or animation for
# IDLE_MS idles IDLE_SLEEP_MS. No file is touched.
inp_hook=HOOKS['window_manager_dispatch_input_event'][0]
m=PowerMachine()
m.byte(syms['g_backlight_status'],0); assert m.pass_(1)[1]==[O['SCREEN_OFF_SLEEP_MS']] and not m.writes and not m.opens; passed()
# An update from a build with Low Power on: LOWPOWER=1 left in config.ini and its marker on flash do nothing.
m=PowerMachine({'LOWPOWER':'1'},files={'/mnt/data/q2pod-cpu1':b''}); m.byte(syms['g_backlight_status'],0)
assert m.pass_(1)[1]==[O['SCREEN_OFF_SLEEP_MS']] and not m.writes and not m.opens and '/mnt/data/q2pod-cpu1' in m.files; passed()
# Dark with nothing playing and the DAC off (check_dacoff_state's -1), a pass idles DARK_IDLE_SLEEP_MS;
# playing, a DAC still counting, or a Wake press that may get its second, SCREEN_OFF_SLEEP_MS.
m=PowerMachine(); m.handlers.pop(syms['mclGetPlayStatus'],None); m.byte(syms['g_backlight_status'],0)
off=O['SCREEN_OFF_SLEEP_MS']
for play,dac,want in ((3,-1,O['DARK_IDLE_SLEEP_MS']),(2,-1,off),(3,5,off),(1,-1,O['DARK_IDLE_SLEEP_MS'])):
    m.word(0xa50084,play); m.word(syms['g_dacoff_time'],dac&0xffffffff)
    assert m.pass_(1)[1]==[want],(play,dac); passed()
m=PowerMachine({'WAKEDOUBLE':'1'}); m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(0xa50084,3)
m.byte(syms['g_backlight_status'],0); m.word(syms['g_dacoff_time'],0xffffffff); m.handlers[inp_hook+12]='stock_input'
assert m.pass_(1)[1]==[O['DARK_IDLE_SLEEP_MS']]  # reads Wake
m.call(O['KEY_CENTER'],address=inp_hook,args=(m.wm,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0,clear=False)
assert m.pass_(1)[1]==[O['SCREEN_OFF_SLEEP_MS']] and m.pass_(O['WAKE_MS'])[1]==[O['DARK_IDLE_SLEEP_MS']]; passed()
m=PowerMachine(); m.animating=1; m.pass_(1)  # idle pacing
assert m.pass_(O['IDLE_MS'])[1]==[]; m.animating=0
assert m.pass_(1)[1]==[O['IDLE_SLEEP_MS']] and not m.writes; passed()
inp=HOOKS['window_manager_dispatch_input_event'][0]; m.handlers[inp+12]='stock_input'
m.call(address=inp,args=(m.wm,m.event,0,0),gap=0); assert m.pass_(1)[1]==[]; passed()  # input: full speed again
# A wheel press tick_gate drops is no input to the idle clock; one with a frame behind it is.
m=PowerMachine(); m.handlers[inp+12]='stock_input'; m.animating=1; m.pass_(1); m.pass_(O['IDLE_MS']); m.animating=0; assert m.pass_(1)[1]==[O['IDLE_SLEEP_MS']]
m.call(address=inp,args=(m.wm,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0); assert m.pass_(1)[1]==[O['IDLE_SLEEP_MS']]
m.word(payload_syms['wheel_frame_at'],m.now); m.call(address=inp,args=(m.wm,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0); assert m.pass_(1)[1]==[]; passed()
# A dark wheel turn keeps stock's pace: no idle for DARK_TURN_MS after a tick tick_gate passes, the same
# millisecond included; a tick it drops (no ring frame) leaves the dark idle.
m=PowerMachine(); m.handlers[inp+12]='stock_input'; m.byte(syms['g_backlight_status'],0); assert m.pass_(1)[1]==[O['SCREEN_OFF_SLEEP_MS']]
m.word(payload_syms['wheel_frame_at'],(m.now-O['WHEEL_FRESH_MS'])&0xffffffff)  # no finger behind this one
m.call(address=inp,args=(m.wm,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0,clear=False); assert m.pass_(1)[1]==[O['SCREEN_OFF_SLEEP_MS']]
m.word(payload_syms['wheel_frame_at'],m.now); m.call(address=inp,args=(m.wm,m.event,0,0),event_type=O['EVT_KEY_DOWN'],gap=0,clear=False)
assert m.pass_(0)[1]==[] and m.pass_(O['DARK_TURN_MS']-1)[1]==[] and m.pass_(1)[1]==[O['SCREEN_OFF_SLEEP_MS']]; passed()
# Now Playing with its lyrics at rest idles like any page.
m=PowerMachine(); m.top=m.node('window','playing_page'); m.pass_(1); m.pass_(O['IDLE_MS'])
assert m.pass_(1)[1]==[O['IDLE_SLEEP_MS']]; passed()
# Standby limit (docs/internals.md#standby-limit): with a limit, toolsIntoStandby's suspend runs
# between the RTC wake alarm's set (the limit past since_epoch) and its clear. A resume at or past the
# alarm powers off on the next pass (poweroff_page), once, unless a cable is in or the music plays; a
# key's resume before it, an unreadable RTC or a refused alarm leave it on. Off, no file is touched.
RTC_NOW,RTC_ALARM='/sys/class/rtc/rtc0/since_epoch','/sys/class/rtc/rtc0/wakealarm'
class StandbyMachine(PowerMachine):
    def __init__(self,config={},key=None,readable=True,refuse=False,rtc=1792363806):
        super().__init__(config,files={RTC_NOW:f'{rtc}\n'.encode()} if readable else {})
        self.rtc,self.key,self.readable,self.refuse,self.alarm,self.events=rtc,key,readable,refuse,0,[]
        self.handlers[int(manifest['patch_symbols']['stock_standby_trampoline'],16)]='stock_standby'
        self.handlers.pop(syms['mclGetPlayStatus'],None); self.word(0xa50084,3)
        # the USB record's lines either side of the suspend (usb_record.c): the pins' reads, and PD18's pull-up again
        self.mock('toolsGpioGetValue','system@GLIBC_2.0')
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a=u.reg_read(REGS[0])
        if name=='stock_standby':  # asleep until the key (seconds in) or the alarm, whichever is first
            ends=[t for t in (self.alarm,self.key and self.rtc+self.key) if t]; assert ends,'nothing wakes it'
            self.rtc=min(ends)
            if self.rtc==self.alarm: self.alarm=0  # it fired
            if self.readable: self.files[RTC_NOW]=f'{self.rtc}\n'.encode()
            self.events.append('suspend'); self.returns(0); return
        if name.startswith('r:fclose') and self.open.get(a,[''])[0]==RTC_ALARM:
            v=int(self.open.pop(a)[3])  # rtc-sysfs: a time not ahead clears it; a set alarm refuses another
            ok=not self.refuse and (v<=self.rtc or not self.alarm)
            if ok: self.alarm=v if v>self.rtc else 0
            self.events.append(('alarm',v)); self.returns(0 if ok else 0xffffffff); return
        return super().hook(u,address,size,unused)
    def standby(self):
        self.events=[]; assert self.call(address=HOOKS['toolsIntoStandby'][0],args=(0,0,0,0),gap=0)==0
        return self.events
    def off(self):  # the next UI loop pass powers off
        self.opened=[]; self.pass_(1); return [p for p,*_ in self.opened]==['poweroff_page']
start=1792363806
m=StandbyMachine(key=600); m.calls=[]; assert m.standby()==['suspend'] and not m.opens
assert [m.text(c[1]) for c in m.calls if c[0]=='system@GLIBC_2.0']==['/usr/bin/cmd_gpio set_func PD18 input pull_up >/dev/null 2>&1',
    'devmem 0x1000003c 32 $(( $(devmem 0x1000003c) | 0x20000000 )); devmem 0x10000040 32 $(( $(devmem 0x10000040) | 0x01800000 ))']  # usb_otg.c otg_hold
assert not m.off(); passed()
for i in (1,2,3):
    alarm=start+O[f'STANDBY_LIMIT_{i}']*3600; m=StandbyMachine({'STANDBYLIMIT':str(i)})
    assert m.standby()==[('alarm',0),('alarm',alarm),'suspend',('alarm',0)] and m.rtc==alarm
    assert m.off() and not m.off(); passed()
limit=O['STANDBY_LIMIT_2']*3600; m=StandbyMachine({'STANDBYLIMIT':'2'},key=600)
assert m.standby()==[('alarm',0),('alarm',start+limit),'suspend',('alarm',0)] and not m.alarm and not m.off()
m.key=None; assert m.standby()[1]==('alarm',start+600+limit) and m.off(); passed()  # the next one counts from zero
m=StandbyMachine({'STANDBYLIMIT':'1'}); m.alarm=start+60; assert m.standby()[:2]==[('alarm',0),('alarm',start+3600)]; passed()
m=StandbyMachine({'STANDBYLIMIT':'1'},key=7200,readable=False); assert m.standby()==['suspend'] and not m.off(); passed()
m=StandbyMachine({'STANDBYLIMIT':'1'},key=7200,refuse=True); assert m.standby()==[('alarm',0),'suspend'] and not m.off(); passed()
for usb,play in ((1,3),(0,2)):  # a cable in, or playing: it stays on, and no later pass powers off
    m=StandbyMachine({'STANDBYLIMIT':'1'}); m.standby(); m.byte(syms['g_usbdet_value'],usb); m.word(0xa50084,play)
    assert not m.off() and not m.off(); passed()
# Hardware watchdog and the state record (docs/internals.md#watchdog). The first pass opens
# /dev/jz_watchdog and starts it at WDT_MS, every pass feeds it, Standby stops it around the suspend
# and starts it again, and a clean end stops it and removes the record; nothing is fed or written
# after. No device, or a refused start: tried once, and the record is kept all the same. The record
# is rewritten only when it changes, a burst once it settles, and as suspending just before Standby's suspend.
WDT_START,WDT_STOP,WDT_FEED=0x8004570d,0x2000570e,0x2000570f
class WatchdogMachine(StandbyMachine):
    def __init__(self,config={},fd=3,refuse_start=False,**k):
        super().__init__(config,**k); self.fd,self.refuse_start,self.ioctls,self.wdt_opens=fd,refuse_start,[],0
        self.handlers[syms['ioctl@GLIBC_2.0']]='d:ioctl'
        self.byte(syms['g_backlight_status'],1); self.byte(syms['g_usbdet_value'],0)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,''); a,b,c=[u.reg_read(r) for r in REGS[:3]]
        if name=='p:open@GLIBC_2.0' and self.text(a)=='/dev/gpiochip3':  # usb_event.c's cable line: none here
            self.returns(0xffffffff); return
        if name=='p:open@GLIBC_2.0' and self.text(a)=='/tmp/q2guard.state': self.returns(0xffffffff); return  # no q2guard here
        if name=='p:open@GLIBC_2.0':
            assert self.text(a)=='/dev/jz_watchdog' and b==0x80002; self.wdt_opens+=1; self.returns(self.fd&0xffffffff); return
        if name=='d:ioctl':
            self.ioctls.append((a,b,self.get(c)) if b==WDT_START else (a,b))
            if b!=WDT_FEED: self.events.append(('wdt',b))
            self.returns(0xffffffff if b==WDT_START and self.refuse_start else 0); return
        return super().hook(u,address,size,unused)
    def record(self): return self.files.get(RECORD)
m=WatchdogMachine(key=600); m.pass_(1)
assert m.wdt_opens==1 and m.ioctls==[(3,WDT_START,O['WDT_MS'])],(m.ioctls)
assert m.record()==b'screen:on power:running cable:out battery:0 play:paused out:jack\n',m.record()
m.pass_(1); m.pass_(1); assert m.ioctls[1:]==[(3,WDT_FEED)]*2 and m.wdt_opens==1 and len(m.records)==1; passed()
# a change is written once it has held STATE_SETTLE_MS (5 s, system.c): a burst is one write of its latest line
n=len(m.records); level(m,56,0); m.pass_(1); level(m,57,0); m.pass_(1); assert len(m.records)==n
m.pass_(5000); assert m.records[-1]==(RECORD,b'screen:on power:running cable:out battery:57 play:paused out:jack\n') and len(m.records)==n+1
n=len(m.records); m.pass_(1); m.pass_(6000); assert len(m.records)==n  # unchanged: not rewritten
m.byte(syms['g_backlight_status'],0); m.byte(syms['g_usbdet_value'],1); m.pass_(1); m.pass_(5000)
assert m.records[-1][1]==b'screen:off power:running cable:in battery:57 play:paused out:jack\n'; passed()
m.ioctls=[]; assert m.standby()==[('wdt',WDT_STOP),'suspend',('wdt',WDT_START)],m.events
assert m.records[-1][1]==b'screen:off power:suspending cable:in battery:57 play:paused out:jack\n'
m.pass_(1); assert m.records[-1][1].startswith(b'screen:off power:running') and m.ioctls[-1]==(3,WDT_FEED); passed()
m=WatchdogMachine({'STANDBYLIMIT':'1'}); m.pass_(1)
assert m.standby()==[('alarm',0),('alarm',start+3600),('wdt',WDT_STOP),'suspend',('wdt',WDT_START),('alarm',0)],m.events; passed()
# a known long pass (an import, a folder add) counts WDT_LONG_MS; the next pass, after it, WDT_MS again
m=WatchdogMachine(key=600); m.pass_(1); m.ioctls=[]
for _ in range(2): m.call(address=payload_syms['ringnav_wdt_long'],args=(0,0,0,0),gap=0)  # once while it runs
assert m.ioctls==[(3,WDT_STOP),(3,WDT_START,O['WDT_LONG_MS'])],m.ioctls; m.ioctls=[]
m.pass_(1); assert m.ioctls==[(3,WDT_STOP),(3,WDT_START,O['WDT_MS'])],m.ioctls; m.ioctls=[]
m.pass_(1); assert m.ioctls==[(3,WDT_FEED)] and O['WDT_LONG_MS']>O['WDT_MS']==60000; passed()
# a clean end: the watchdog stops, the record goes, and nothing is fed or written after it
m=WatchdogMachine(key=600); m.pass_(1); m.ioctls=[]
m.call(address=payload_syms['ringnav_clean_end'],args=(0,0,0,0),gap=0)
assert m.ioctls==[(3,WDT_STOP)] and RECORD not in m.files; n=len(m.records); m.ioctls=[]
level(m,5,0); m.pass_(1); assert not m.ioctls and len(m.records)==n and RECORD not in m.files; passed()
m=WatchdogMachine(key=600,fd=-1); m.pass_(1); m.pass_(1)
assert m.wdt_opens==1 and not m.ioctls and m.record() and m.standby()==['suspend']; passed()
m=WatchdogMachine(key=600,refuse_start=True); m.pass_(1); m.pass_(1)
assert m.ioctls==[(3,WDT_START,O['WDT_MS'])] and m.record() and m.standby()==['suspend']; passed()

# The Wi-Fi signal (system.c ringnav_wifi_signal): networkGetWifiSignal, which the status bar and
# the network monitor thread each call about once a second, reads the driver's rx_signal and never
# sends stock's SIOCGIWSTATS, whose TX report takes the radio out of power save. It answers stock's
# dBm (the ioctl's unsigned level less 256, get_station's signal_strength less 100), except that
# 100% is 0 dBm, where stock wraps to -256; a missing file is 0, as stock's failed ioctl. The file
# is read again only after WIFI_SIGNAL_MS.
RX_SIGNAL='/proc/net/rtl8733bs/wlan0/rx_signal'
class WifiMachine(Machine):
    def __init__(self,pct,patched=True):
        super().__init__(patched); self.pct=pct
        self.mock('strstr@GLIBC_2.0','strncpy@GLIBC_2.0')
        for n in ('open','read','close','socket','ioctl'): self.handlers[syms[n+'@GLIBC_2.0']]='w:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('w:'): return super().hook(u,address,size,unused)
        assert u.reg_read(UC_MIPS_REG_T9)==address
        name=name[2:]; a,b,c=(u.reg_read(r) for r in REGS[:3]); ret=0
        self.calls.append((name,a,b,c))
        if name=='open': ret=3 if self.pct is not None and self.text(a)==RX_SIGNAL else -1
        elif name=='read':  # the driver's proc_get_rx_signal, as far as it matters
            data=f'rssi:{self.pct}\nsignal_strength:{self.pct}\nsignal_qual:100\n'.encode()[:c]
            u.mem_write(b,data); ret=len(data)
        elif name=='socket': ret=5
        elif name=='ioctl':  # cfg80211_wireless_stats: iw_statistics behind iwreq's data pointer
            assert (a,b)==(5,0x8b0f); u.mem_write(self.get(c+16),bytes([0,0,70,(self.pct-100)&255]))
        self.returns(ret,clobber=True)
    def signal(self): return self.call(address=HOOKS['networkGetWifiSignal'][0],args=(0,0,0,0),gap=0)
    def used(self): return [c[0] for c in self.calls if c[0] in ('open','read','close','socket','ioctl')]
for pct in (0,20,35,64,99,100):
    stock,m=WifiMachine(pct,patched=False),WifiMachine(pct)
    assert stock.signal()==(pct-100 if pct<100 else -256) and stock.used()==['socket','ioctl','close']
    assert m.signal()==pct-100 and m.used()==['open','read','close']
    assert [m.text(c[1]) for c in m.calls if c[0]=='open']==[RX_SIGNAL]; passed()
m=WifiMachine(None); assert m.signal()==0 and m.used()==['open']; passed()  # no driver: as a failed ioctl
m=WifiMachine(60); assert m.signal()==-40; m.pct=30; m.calls=[]
m.now+=O['WIFI_SIGNAL_MS']-1; assert m.signal()==-40 and m.used()==[]
m.now+=1; assert m.signal()==-70 and m.used()==['open','read','close']; passed()



# Smart Wi-Fi (docs/internals.md#smart-wi-fi): with Power's row on and the user's Wi-Fi on, the radio
# goes off once nothing has needed the network for SMART_IDLE_MS, through stock's own switch (wifi_switch
# runs here, as Quick Settings and the Wi-Fi page call it) with its WIFI= write dropped in the payload's
# write_int_config hook, and comes back when something needs it. The link and its signal are mocked;
# everything else is stock's or the payload's.
class SmartMixin:
    def __init__(self,*a,smart='1',**k):
        super().__init__(*a,**k)
        if smart: self.config['SMARTWIFI']=smart
        # Automatic Time Zone off: its lookup at a new link (autozone.c) is a use of the link of its own, kept
        # out of these idle counts (test/fw/test_autozone.py has it)
        self.config['AUTOZONE']='0'
        self.handlers.pop(syms['write_int_config'],None)  # the payload's hook runs; the write past it is recorded
        self.handlers[HOOKS['write_int_config'][0]+12]='w:write'
        self.mock('remove@GLIBC_2.0','stop_wifisynclink','networkStart','networkStop')
        for n in ('networkGetConnectStatus','get_wifisignal'): self.handlers[syms[n]]='w:'+n
        self.link=0; self.status=None  # status: stock's connect status when not the link's (its own poll)
        self.nodes[self.wm]=dict(type='window_manager',name='',children=[],visible=1,enable=1)
        self.byte(syms['g_wififlag'],1); self.word(O['NET_TYPE'],2)  # the user's Wi-Fi on, the module up
        # a --dev build's q2remote needs the network for REMOTE_BOOT_MS after demo's first poll (smart_wifi.c
        # remote_poll): this Q2 started long before, so these idle counts are a release build's (test/fw has the hold)
        if manifest['dev']: self.word(payload_syms['remote_boot_at'],1)
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if not name.startswith('w:'): return super().hook(u,address,size,unused)
        if name=='w:write':  # stock's write past the hook (its trampoline jumps, no t9): recorded as the mock's
            self.calls.append(('write_int_config',*[u.reg_read(r) for r in REGS[:3]])); self.returns(0); return
        self.calls.append((name[2:],0,0,0))
        status=self.link if self.status is None else self.status
        self.returns(status if name=='w:networkGetConnectStatus' else 3 if self.link else -1,clobber=True)
    def windows(self,*names):
        self.nodes[self.wm]['children']=[self.node('window',n) for n in names]
        return self.nodes[self.wm]['children']
    def switch(self): return [c[0] for c in self.calls if c[0] in ('networkStart','networkStop','write_int_config')]
    def flag(self): return self.u.mem_read(syms['g_wififlag'],1)[0]
class SmartMachine(SmartMixin,PowerMachine):
    def __init__(self,smart='1'): super().__init__(smart=smart); self.mock('system@GLIBC_2.0')
SW_IDLE,SW_POLL,SW_CONNECT=O['SMART_IDLE_MS'],O['SMART_POLL_MS'],O['SMART_CONNECT_MS']
SW_INIT='/tmp/wpasupplicant_initsuccess'
SW_WAIT='Connecting to Wi-Fi…'
def wifi_calls(m):  # wifi_switch's own: its file, SyncLink, the name, the network and the write; the first argument
    calls=[(c[0].split('@')[0],c[1]) for c in m.calls]
    keep=lambda c: (c[0] in ('networkStop','networkStart','write_int_config','stop_wifisynclink') or
                    (c[0] in ('access','remove') and m.text(c[1])==SW_INIT) or (c[0]=='memset' and c[1]==syms['wifi_name']))
    return [c if c[0] in ('access','remove','memset','write_int_config') else c[:1] for c in calls if keep(c)]
def wifi_state(m):
    return [m.u.mem_read(a,1)[0] for a in (syms['g_wififlag'],syms['wifi_connectstatus'],syms['wifi_initstatus'],0xa4db3a,
                                           syms['wifilink_timeout'])]+[bytes(m.u.mem_read(syms['wifi_name'],4))]
def connected(m):  # stock's state on a link: its name, the connect state, the init file, SyncLink over Bluetooth
    m.files[SW_INIT]=b''; m.byte(syms['g_synclinkflag'],1); m.byte(syms['wifi_connectstatus'],2)
    m.byte(0xa4db3a,5); m.byte(syms['wifilink_timeout'],7); m.u.mem_write(syms['wifi_name'],b'Home\0')
# The user's switch, for the record: Quick Settings' tap with Wi-Fi on, saved through the hook.
user=SmartMachine(smart=''); connected(user); user.calls=[]
assert user.call(address=syms['wifi_switch'],args=(1,0,0,0),gap=0)==0; user_calls=wifi_calls(user)
assert [c[0] for c in user_calls]==['access','remove','stop_wifisynclink','memset','networkStop','write_int_config']
assert [(c[1],user.text(c[2]),user.text(c[3])) for c in user.calls if c[0]=='write_int_config']==[(0,'SYSSET','WIFI')]; passed()
# Smart Wi-Fi: armed on the first poll, nothing before SMART_IDLE_MS (no link here, as an unassociated
# radio), then exactly that switch, in its order, without the write; the same state after.
m=SmartMachine(); connected(m)
m.pass_(1); assert not m.switch()
m.pass_(SW_IDLE-SW_POLL); assert not m.switch() and m.flag()==1
m.pass_(SW_POLL); assert wifi_calls(m)==user_calls[:-1]
assert wifi_state(m)==wifi_state(user)==[0,0,0,0,30,b'\0\0\0\0']; passed()
# network_checkstatus (stock's 500 ms timer) then finds Wi-Fi's flag clear and no link, and with the
# latch its connect set (it sent ips=0 and lps=0) sends ips=1, as after the user's switch.
m.byte(0xa4db39,1); m.calls=[]; m.call(address=syms['network_checkstatus'],args=(0,0,0,0),gap=0)
assert [m.text(c[1]) for c in m.calls if c[0]=='system@GLIBC_2.0']==['rtwpriv wlan0 pm_set ips=1'] and m.u.mem_read(0xa4db39,1)==b'\0'; passed()
# A page needing the network wakes it at the next poll: the switch's on branch, networkStart's
# background connect, still without the write. Tidal Downloads (the card's) does not.
m.windows('home_page','tidal_downloads'); m.pass_(SW_POLL); assert not m.switch() and m.flag()==0
m.windows('home_page','addon_page'); m.pass_(SW_POLL)
assert m.switch()==['networkStart'] and [c[1] for c in m.calls if c[0]=='networkStart']==[1] and m.flag()==1
# A new link counts as a use: SMART_IDLE_MS from it, not from the wake.
m.windows('home_page'); m.link=1; m.pass_(SW_POLL); assert not m.switch()
m.pass_(SW_IDLE-SW_POLL); assert not m.switch(); m.pass_(SW_POLL); assert m.switch()==['networkStop'] and m.flag()==0; passed()
# The user's own tap after that (Quick Settings shows Wi-Fi off: the radio is): Wi-Fi on and saved as
# stock saves it, Smart Wi-Fi's hold gone, and SMART_IDLE_MS from the tap before it acts again.
m.link=0; m.calls=[]; assert m.call(address=syms['wifi_switch'],args=(1,0,0,0),gap=0)==0
assert [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[(1,'SYSSET','WIFI')]
assert 'networkStart' in m.switch() and m.flag()==1
m.pass_(SW_IDLE-1); assert not m.switch(); m.pass_(SW_POLL); assert m.switch()==['networkStop']; passed()
# Any other write passes the hook untouched, Smart Wi-Fi's radio off or not.
m.calls=[]; assert m.call(address=syms['write_int_config'],args=(3,m.string('SYSSET'),m.string('BACKLIGHT'),0),gap=0)==0
assert [(c[1],m.text(c[2]),m.text(c[3])) for c in m.calls if c[0]=='write_int_config']==[(3,'SYSSET','BACKLIGHT')] and m.flag()==0; passed()
# The row, after Charge Limit: off by default and saved as SMARTWIFI. Turned off while Smart Wi-Fi has
# the radio off, the user's Wi-Fi comes back, unsaved, and is left alone.
m,rows,texts,icons,click=settings_page('power','scroll_view_powermanager',cls=SmartMachine,config={'SMARTWIFI':'0'})
assert texts()==['Standby Limit: Off','Charge Limit: Off','USB DAC Charging','Smart Wi-Fi','system_carmode','Battery History','Boot to: Rockbox'] and icons[3]=='wifiset_wifi'
assert switch_image(m,rows[3])=='switch_off' and click(3)==[(1,'Q2POD','SMARTWIFI')] and switch_image(m,rows[3])=='switch_on'
m.pass_(1); m.pass_(SW_IDLE); assert m.flag()==0
assert click(3)==[(0,'Q2POD','SMARTWIFI')] and switch_image(m,rows[3])=='switch_off'
m.pass_(SW_POLL); assert m.switch()==['networkStart'] and m.flag()==1
m.pass_(SW_IDLE); m.pass_(SW_IDLE); assert not m.switch(); passed()
# The user's own off: saved, and Smart Wi-Fi never brings it back.
m=SmartMachine(); m.pass_(1); m.calls=[]; m.call(address=syms['wifi_switch'],args=(1,0,0,0),gap=0)
assert m.switch()==['networkStop','write_int_config'] and m.flag()==0
m.windows('stream_page'); m.pass_(SW_IDLE); m.pass_(SW_IDLE); assert not m.switch(); passed()
# Off by default: nothing, however long.
m=SmartMachine(smart=''); m.pass_(1); m.pass_(SW_IDLE); assert not m.switch(); m.pass_(SW_IDLE); assert not m.switch() and m.flag()==1; passed()
# What keeps it on (or, for a few, what doesn't): USB in, AirPlay or DLNA on, SyncLink over Wi-Fi or a
# SyncLink session (not SyncLink over Bluetooth); a stream queue (Tidal's: the types
# stock stops offline), paused or stopped, but not a local one; a network page (LocalSend's too); a time sync pending on
# a link (not without one); and stock's own gates on its switch.
def stream_queue(kind,status):
    def setup(m):
        m.handlers.pop(syms['mclGetPlayStatus'],None); m.word(O['MCL_TYPE'],kind); m.word(0xa50084,status)
    return setup
def setb(name,value): return lambda m: m.byte(syms[name],value)
CASES=[('usb',setb('g_usbdet_value',1),True),('airplay',setb('g_airplayflag',1),True),('dlna',setb('g_dlnaflag',1),True),
       ('synclink wifi',setb('g_synclinkflag',2),True),('synclink session',setb('g_syncLink_status',1),True),
       ('synclink bt',setb('g_synclinkflag',1),False),('localsend',lambda m: m.windows('localsend_page'),True),
       ('paused tidal',stream_queue(0xf1,3),True),('stopped stream',stream_queue(2,1),True),
       ('paused local',stream_queue(0xf001,3),False),('streaming page',lambda m: m.windows('home_page','stream_page'),True),
       ('tidal page',lambda m: m.windows('tidal_main_page'),True),('wifi page',lambda m: m.windows('wifi_page'),True),
       ('card app page',lambda m: m.windows('addon_page'),True),('card app qr',lambda m: m.windows('qrcode_page'),True),
       ('apps',lambda m: m.windows('sysset_page','apps_page'),True),
       ('configuration',lambda m: m.windows('streamconf_page'),False),
       ('time sync',lambda m: (m.byte(syms['g_syncnettime_flag'],1),setattr(m,'link',1)),True),
       ('time sync, no link',setb('g_syncnettime_flag',1),False),
       ('module down',lambda m: m.word(O['NET_TYPE'],1),True),('first scan',setb('wifi_initstatus',2),True),
       ('connecting',setb('wifi_connectstatus',1),True),('test mode',setb('g_testmode_flag',1),True),
       ('Wi-Fi off',setb('g_wififlag',0),True),
       ('now playing more',lambda m: m.windows('playing_page','playermore_page'),False),
       ('firmware update',lambda m: m.windows('fwupdate_page'),False)]
for case,setup,kept in CASES:
    m=SmartMachine(); setup(m); m.pass_(1); m.pass_(SW_IDLE)
    assert (not m.switch())==kept and (kept or m.flag()==0),case
passed()
# The payload's own users hold it on while they run: librespot, a station, a card app, an upload,
# Network Update's download or an Apps job, Get Album Lyrics, a Tidal download and Car Remote while a phone may
# use the network (coda_rc_wants_wifi: a link, and used within 10 minutes; test/fw/test_coda_rc_shim.py has the rule).
class SmartBusy(SmartMachine):
    def __init__(self,name,busy):
        super().__init__(); self.busy=busy; self.handlers[payload_syms[name]]='b:'+name  # every address is hooked here
        # Car Remote's: its shim's poll (test/fw/test_coda_rc_shim.py's) left out, as it would start the daemon
        if name=='coda_rc_wants_wifi': self.handlers[payload_syms['coda_rc_poll']]='skip'
    def hook(self,u,address,size,unused):
        if self.handlers.get(address)=='skip': self.returns(0); return
        if self.handlers.get(address,'').startswith('b:'): self.returns(self.busy); return
        return super().hook(u,address,size,unused)
for name in ('spot_running','radio_status','addon_running','scrobble_busy','netupdate_busy','album_lyrics_busy','tidal_busy',
             'coda_rc_wants_wifi'):
    for busy in (0,2):  # radio_status: 2 a station playing
        m=SmartBusy(name,busy); m.pass_(1); m.pass_(SW_IDLE); assert (not m.switch())==bool(busy),(name,busy)
passed()
# The Wi-Fi page builds its switch from g_wififlag: its init (systemset_wifi_page_init) brings the
# radio back first, so it shows the user's setting.
m=SmartMachine(); m.pass_(1); m.pass_(SW_IDLE); assert m.flag()==0
m.handlers[int(manifest['patch_symbols']['stock_wifi_page_trampoline'],16)]='stock_wifi_page'
m.calls=[]; assert m.call(address=HOOKS['systemset_wifi_page_init'][0],args=(m.top,0,0,0),gap=0)==0
names=[c[0] for c in m.calls]; assert names.index('networkStart')<names.index('stock_wifi_page') and 'write_int_config' not in names
assert m.flag()==1; passed()
# An automatic scrobble upload due while Smart Wi-Fi has the radio off: [SCROBBLE] AUTO read from the
# card, the radio woken, and the next check (a minute on) uploads on the link; a wake that brings no
# link, or AUTO off, waits SA_RETRY_MIN as an upload that failed.
SA=payload_syms.get('sa') or payload_syms['sa.0']  # the compiler may split the static struct into its fields, in order
assert all(payload_syms.get(f'sa.{i}',SA+4*i)==SA+4*i for i in range(5))
def sa_due(m): m.word(SA,1); m.word(SA+4,m.now-4*3600*1000); m.word(SA+8,m.now-120000)  # pending, first_at, check_at
def auto(m,ms=60000): m.pass_(ms)  # ringnav_sleep's pass runs scrobble_auto
class SmartScrobble(SmartMixin,PowerMachine):
    def __init__(self,on=True):
        super().__init__(); self.mock('system@GLIBC_2.0'); self.handlers[payload_syms['scrobble_start']]='s:start'
        self.handlers[payload_syms['scrobble_flag']]='s:flag'; self.on=on; self.starts=0; self.flags=0
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='s:start': self.starts+=1; self.returns(1); return
        if name=='s:flag': self.flags+=1; self.returns(int(self.on)); return
        return super().hook(u,address,size,unused)
m=SmartScrobble(); m.pass_(1); m.pass_(SW_IDLE); assert m.flag()==0
sa_due(m); auto(m,0); assert m.flags==1 and m.flag()==1 and not m.starts  # woken, nothing sent yet
m.link=1; auto(m); assert m.starts==1; passed()
m=SmartScrobble(); m.pass_(1); m.pass_(SW_IDLE); sa_due(m); auto(m,0); assert m.flag()==1
auto(m); assert not m.starts and m.get(SA+4)==(m.now-m.call(address=payload_syms['scrobble_auto_ms'],args=(1,0,0,0),gap=0)+30*60000)&0xffffffff
m.pass_(SW_IDLE); assert m.flag()==0; auto(m); assert m.flag()==0 and m.flags==1; passed()  # not again before the retry
m=SmartScrobble(on=False); m.pass_(1); m.pass_(SW_IDLE); sa_due(m); auto(m,0); assert m.flags==1 and m.flag()==0 and not m.starts; passed()
# Without Smart Wi-Fi's radio off, no Wi-Fi reads nothing from the card, as before.
m=SmartScrobble(); m.config.pop('SMARTWIFI'); m.byte(syms['g_wififlag'],0); m.pass_(1); sa_due(m); auto(m,0); assert not m.flags; passed()

# Actions that check the link once, held while Smart Wi-Fi brings the radio back (smart_hold): stock's
# Streaming's Tidal row and Now Playing More's Download Cover & Lyrics. A press toasts and wakes the radio; stock's own action runs once from a timer when the link is
# up, or SMART_CONNECT_MS on, and only while its page is still the one in use (toasts and the volume
# overlay aside).
class SmartRadio(SmartMixin,RadioMachine): pass
HELD=[('stream_row','stream_row','stream_page','0'),('playermore_online','online_match','playermore_page',None)]
def held_machine(page,smart='1',mock=True):
    """Smart Wi-Fi's radio off, and page on top with stock's rows: a list_item per index holding a button
    of the same name, both named at +0x10 as stock's widget_set_name leaves them."""
    m=SmartRadio(smart=smart); m.poll(1); m.poll(SW_IDLE)
    items=[]
    for n in '012':
        b=m.node('button',n); item=m.node('list_item',n,[b]); items.append(item)
        for w in (item,b): m.word(w+0x10,m.string(n))
    win=m.node('window',page,[m.node('scroll_view','scroll_view_streamsset' if page=='stream_page' else 'scroll_view',items)])
    m.nodes[m.wm]['children']=[m.node('window','home_page'),win]
    for t in ('stream_row','netservice_row','online_match')*mock:
        a=int(manifest['patch_symbols'][f'stock_{t}_trampoline'],16); m.handlers[a]='stock_'+t
        m.u.hook_add(UC_HOOK_CODE,m.hook,begin=a,end=a)
    return m,win
def held_row(m,win,row): return m.nodes[m.nodes[win]['children'][0]]['children'][int(row)]  # the list_item
def held_press(m,hook,win,row):
    """Press as stock does: a row's button (its click's ctx) or More's window. The held action is later
    given the row's list_item (found by name) or the window."""
    arg=win if hook=='playermore_online' else m.nodes[held_row(m,win,row)]['children'][0]
    assert m.call(address=HOOKS[hook][0],args=(arg,m.event,0,0),gap=0)==0
    return held_row(m,win,row) if row else arg
def held_runs(m,t): return [c[1] for c in m.calls if c[0]=='stock_'+t]
for hook,t,page,row in HELD:
    # Waits: toasted, the radio woken, no stock action; a second press toasts again, runs nothing and
    # keeps the one timer; once the link is up, stock's action runs once.
    m,win=held_machine(page); assert m.flag()==0
    target=held_press(m,hook,win,row)
    assert m.toasts[-1][3]==SW_WAIT and m.flag()==1 and not held_runs(m,t) and len(m.timers)==1,hook
    m.advance(500); assert not held_runs(m,t)
    n=len(m.toasts); held_press(m,hook,win,row)
    assert len(m.toasts)==n+1 and m.toasts[-1][3]==SW_WAIT and not held_runs(m,t) and len(m.timers)==1,hook
    m.link=1; m.advance(500,clear=False); runs=held_runs(m,t)
    assert runs==[target] and not m.timers,hook
    m.advance(2000); assert not held_runs(m,t); passed()
    # A press after the link is up but before the tick joins the wait: stock's action still runs once.
    m,win=held_machine(page); held_press(m,hook,win,row); m.advance(500); m.link=1
    held_press(m,hook,win,row); assert not held_runs(m,t) and len(m.timers)==1
    m.advance(500,clear=False); assert len(held_runs(m,t))==1 and not m.timers,hook; passed()
    # The bars up before stock's connect status (each its own poll): it waits for both, as stock's
    # action checks the one and the payload's the other.
    m,win=held_machine(page); held_press(m,hook,win,row); m.link=1; m.status=0
    m.advance(500,clear=False); assert not held_runs(m,t) and len(m.timers)==1,hook
    m.status=None; m.advance(500,clear=False); assert len(held_runs(m,t))==1 and not m.timers,hook; passed()
    # No link SMART_CONNECT_MS on: stock's action anyway (its own msg_notnetwork).
    m,win=held_machine(page); held_press(m,hook,win,row); m.advance(SW_CONNECT-500); assert not held_runs(m,t)
    m.advance(500); assert len(held_runs(m,t))==1 and not m.timers; passed()
    # Another page opened over it, or it closed: dropped at the next tick, link or not, never run when
    # the link comes, and the radio let go.
    for cover in ('playing_page','closed'):
        m,win=held_machine(page); held_press(m,hook,win,row)
        if cover=='closed': m.nodes[m.wm]['children'].remove(win)
        else: m.nodes[m.wm]['children'].append(m.node('window',cover))
        m.advance(500); assert not held_runs(m,t) and not m.timers,(hook,cover)
        m.link=1; m.advance(500); assert not held_runs(m,t)
        m.link=0; m.nodes[m.wm]['children']=[m.node('window','home_page')]; m.poll(SW_IDLE); m.poll(SW_POLL)
        assert m.flag()==0,(hook,cover); passed()
    # A toast, or stock's volume overlay (a wheel turn over the toast), over it is no other page.
    for overlay in ('msginfo_dialog','volume_dialog'):
        m,win=held_machine(page); held_press(m,hook,win,row); m.nodes[m.wm]['children'].append(m.node('dialog',overlay))
        m.link=1; m.advance(500); assert len(held_runs(m,t))==1,(hook,overlay); passed()
    # A page sliding (iPod's close, Back mid-wait) waits for the slide: closed, it is dropped;
    # still there, it runs.
    for closed in (True,False):
        m,win=held_machine(page); held_press(m,hook,win,row); m.animating=1; m.link=1
        m.advance(500); assert not held_runs(m,t) and len(m.timers)==1
        if closed: m.nodes[m.wm]['children'].remove(win)
        m.animating=0; m.advance(500); assert len(held_runs(m,t))==(not closed) and not m.timers,(hook,closed); passed()
    # Not Smart Wi-Fi's radio to wait for (the row off), or the link already up: stock's, at once; except
    # Tidal's row with no link, which opens the page saying so (feedback_offline) in place of stock's toast.
    for smart,link in (('',0),('1',1)):
        m,win=held_machine(page,smart=smart); m.link=link; n=len(m.toasts); held_press(m,hook,win,row)
        offline=hook=='stream_row' and not link
        assert len(held_runs(m,t))==(not offline) and not m.timers and SW_WAIT not in [x[3] for x in m.toasts[n:]],(hook,smart,link)
        assert empty_titles(m)==(['Not Connected'] if offline else []),(hook,smart,link)
        passed()
# Network service's DLNA and AirPlay switches never wait, nor wake the radio: stock starts their
# services once there is a link. Stock gets the pressed button. Its third row, stock's Wi-Fi File
# Transfer, opens LocalSend (localsend_ui.c), never stock's page, and holds nothing.
for row in '01':
    m,win=held_machine('netservice_page'); held_press(m,'netservice_row',win,row)
    assert held_runs(m,'netservice_row')==[m.nodes[held_row(m,win,row)]['children'][0]] and not m.timers and m.flag()==0; passed()
m,win=held_machine('netservice_page'); m.mock('window_create'); n=len(m.toasts); held_press(m,'netservice_row',win,'2')
assert [c for c in m.calls if c[0]=='window_create'] and not held_runs(m,'netservice_row') and not m.timers and len(m.toasts)==n; passed()
# A press from a page that is not the one expected (no page in use, say) is not held: stock's at once with a link,
# and with none, the page saying so (feedback_offline), as an unheld Tidal press has it.
for link in (1,0):
    m,win=held_machine('stream_page'); m.nodes[m.wm]['children']=[]; m.link=link; n=len(m.toasts); held_press(m,'stream_row',win,'0')
    assert len(held_runs(m,'stream_row'))==link and not m.timers and SW_WAIT not in [x[3] for x in m.toasts[n:]],link
    assert empty_titles(m)==([] if link else ['Not Connected']),link
passed()
# Home's Split Streaming list (home.c stream_rows) has stock's Tidal row too, in Home's window beside
# its Library list, whose rows stock also names by index: held the same, and stock is given the
# Streaming list's row.
m,home=held_machine('home_page')  # the Library list's rows
b=m.node('button','0'); tidal=m.node('list_item','0',[b])
for w in (tidal,b): m.word(w+0x10,m.string('0'))
m.nodes[home]['children'].append(m.node('view','list_view_homestream',[m.node('scroll_view','scroll_view_streamsset',[tidal])]))
m.nodes[m.wm]['children']=[home]
assert m.call(address=HOOKS['stream_row'][0],args=(b,m.event,0,0),gap=0)==0
assert m.toasts[-1][3]==SW_WAIT and not held_runs(m,'stream_row') and len(m.timers)==1
m.link=1; m.advance(500,clear=False); assert held_runs(m,'stream_row')==[tidal] and not m.timers; passed()
# Stock's own code after the wait (the trampolines unmocked), given the row's list_item: Streaming's
# Tidal row opens its login page with the link, and after SMART_CONNECT_MS without one, stock's
# msg_notnetwork.
m,win=held_machine('stream_page',mock=False); m.mock('widget_get_window','navigator_switch_to')
held_press(m,'stream_row',win,'0'); m.link=1; m.advance(500)
assert [m.text(c[1]) for c in m.calls if c[0]=='navigator_to']==['tidal/tidal_login_page']; passed()
m,win=held_machine('stream_page',mock=False); m.mock('widget_get_window'); held_press(m,'stream_row',win,'0')
n=len(m.toasts); m.advance(SW_CONNECT)
assert [m.text(c[1]) for c in m.calls if c[0]=='getFormatString']==['msg_notnetwork'] and m.toasts[n:]==[('dialog/msginfo_dialog',1,2000,'')]
assert not [c for c in m.calls if c[0]=='navigator_to']; passed()
# The payload's own presses wait the same way: Upload Scrobbles (Library's row), from its page.
m=SmartRadio(); m.config['TOKEN']='t'; m.poll(1); m.poll(SW_IDLE); assert m.flag()==0
m.windows('localmusic_page'); m.calls=[]
assert m.call(address=payload_syms['upload_scrobbles'],args=(0,0,0,0),gap=0)==0
assert m.toasts[-1][3]==SW_WAIT and m.flag()==1 and not m.threads
m.advance(500); assert not m.threads
m.link=1; m.advance(500); assert m.toasts[-1][3]=='Uploading scrobbles' and len(m.threads)==1; passed()
# A press after the link is up but before the tick joins the wait: one upload, started once (a
# second run would have answered Already uploading, or cancelled Network Update's or Apps' job).
m=SmartRadio(); m.config['TOKEN']='t'; m.poll(1); m.poll(SW_IDLE); m.windows('localmusic_page'); m.calls=[]
m.call(address=payload_syms['upload_scrobbles'],args=(0,0,0,0),gap=0); m.advance(500); m.link=1
assert m.call(address=payload_syms['upload_scrobbles'],args=(0,0,0,0),gap=0)==0 and not m.threads and m.toasts[-1][3]==SW_WAIT
m.advance(500); assert len(m.threads)==1 and [x[3] for x in m.toasts].count('Uploading scrobbles')==1; passed()
# The same for the other presses that start a job: an Apps install and Get Album Lyrics each start
# once, not cancelled by the held press ("Canceling…").
class SmartShelf(SmartMixin,ShelfMachine): pass
m=SmartShelf({LIST:bytearray(REPO_LIST)}); m.rockbox_mode=0
m.poll(1); m.poll(SW_IDLE); assert m.flag()==0
apps_open(m); m.nodes[m.wm]['children']=[m.page]
deezer=(m.labels().index('Deezer')-1)//2; m.row(deezer); assert m.toasts[-1][3]==SW_WAIT and not m.threads
m.advance(500); m.link=1; m.row(deezer); assert m.toasts[-1][3]==SW_WAIT and not m.threads
m.advance(500,clear=False); assert len(m.threads)==1 and m.labels()[2*deezer+2]=='Checking…'; passed()
class SmartAlbumLyrics(SmartMixin,AlbumLyricsMachine): pass
m=SmartAlbumLyrics(); button=m.nodes[m.more()[4]]['children'][0]; label=m.nodes[button]['children'][1]
m.found=[m.song('T1'),m.song('T2')]
for ms in (1,SW_IDLE):  # power_poll's: its settings, then Smart Wi-Fi's
    m.now+=ms; m.call(address=payload_syms['pod_settings'],args=(0,0,0,0),gap=0); m.call(address=payload_syms['smart_poll'],args=(0,0,0,0),gap=0)
assert m.flag()==0; m.nodes[m.wm]['children']=[m.top]
def al_press():
    f,ctx=m.handler(button,O['EVT_CLICK']); assert m.call(address=f,args=(ctx,m.event,0,0),gap=0,count=5_000_000)==0
al_press(); assert m.toasts[-1][3]==SW_WAIT and not m.threads
m.advance(500); m.link=1; al_press(); assert m.toasts[-1][3]==SW_WAIT and not m.threads
m.advance(500,clear=False); assert len(m.threads)==1 and m.nodes[label]['text']=='Downloading 1 of 2'; passed()
# A radio that can't come back (stock's own gate shut: the first scan not taken) still runs the press
# SMART_CONNECT_MS on, unheld, so it fails as without Smart Wi-Fi instead of waiting again.
m=SmartRadio(); m.config['TOKEN']='t'; m.poll(1); m.poll(SW_IDLE); assert m.flag()==0
m.byte(syms['wifi_initstatus'],2); m.windows('localmusic_page')
assert m.call(address=payload_syms['upload_scrobbles'],args=(0,0,0,0),gap=0)==0 and m.toasts[-1][3]==SW_WAIT and m.flag()==0
m.advance(SW_CONNECT-500); assert m.toasts[-1][3]==SW_WAIT
m.advance(500); assert m.toasts[-1][3]=='Connect to Wi-Fi first' and not m.timers and not m.threads
m.advance(SW_CONNECT); assert m.toasts[-1][3]=='Connect to Wi-Fi first' and not m.timers; passed()
# Spotify: Streaming's page wakes the radio; its row then waits for the link before starting librespot
# (which never retries a Connect login or mDNS that failed) and opening the browser. A running
# librespot keeps the radio on.
class SmartBrowse(SmartMixin,BrowseMachine): pass
m=SmartBrowse(); m.poll(1); m.poll(SW_IDLE); assert m.flag()==0
button=m.add_on_row(); assert m.flag()==1  # stream_page_init woke it
m.windows('stream_page'); m.click(button)
assert m.toasts[-1][3]==SW_WAIT and not m.systems and m.top!=None and m.nodes[m.top]['name']!='spotbrowse_page'
m.advance(500); assert not m.systems
m.link=1; m.advance(500); assert m.systems==['/bin/sh /mnt/mmc/.spotify/run'] and m.nodes[m.top]['name']=='stream_page'  # under the loading overlay
m.answer(*ME); assert m.nodes[m.top]['name']=='spotbrowse_page'  # the browser, once it answers
m.link=0; m.windows('home_page'); m.poll(SW_IDLE); m.poll(SW_IDLE); assert not m.switch() and m.flag()==1; passed()
# Without a link SMART_CONNECT_MS on, it starts anyway, as Spotify's row would.
m=SmartBrowse(); m.poll(1); m.poll(SW_IDLE); button=m.add_on_row(); m.windows('stream_page'); m.click(button)
m.advance(SW_CONNECT-500); assert not m.systems
m.advance(500); assert m.systems==['/bin/sh /mnt/mmc/.spotify/run']; passed()
# Internet Radio: a directory list waits for the link under the loading overlay (a "Connecting to Wi-Fi…" toast), then fetches; a station
# started meanwhile waits for it too before q2video runs, the local track stopped at the press.
m=SmartRadio(); m.poll(1); m.poll(SW_IDLE); assert m.flag()==0
page=radio_open(m); assert m.flag()==1
menu=m.labels(); m.row(2); assert m.labels()==menu and m.toasts[-1][3]==SW_WAIT and not m.threads  # the menu under the overlay
m.advance(250); assert m.labels()==menu and not m.threads
m.link=1; m.advance(250); assert m.labels()==menu and len(m.threads)==1; passed()
m=SmartRadio(); m.poll(1); m.poll(SW_IDLE); page=radio_open(m); m.row(1); m.forked=4343
m.row(1); np=m.top; assert m.nodes[np]['name']=='radionp_page' and 'fork' not in [c[0] for c in m.calls]
assert 'player_stop' in [c[0] for c in m.calls]  # the local track stops now, not once the station starts
m.advance(500); assert m.labels(np)[4]=='Connecting…'
m.poll(); assert 'fork' not in [c[0] for c in m.calls]
m.link=1; m.poll(); assert [c[0] for c in m.calls].count('fork')==1; passed()

# Boot logo: a card mount (boot, USB storage's end) starts a thread that keeps the card's logo as the
# splash's /mnt/data copy; the poll toasts its failure. The thread is run here once the poll starts it.
LOGO_SRC,LOGO_DST,LOGO_ROT='/mnt/mmc/Q2Muse/boot/boot-logo.jpg','/mnt/data/boot-logo.jpg','/tmp/boot-logo.jpg'
LOGO_BAD,LOGO_FAIL='Boot logo must be a 320x375 JPEG','Could not save boot logo'
LOGO_SEEN='/mnt/data/boot-logo.bad'  # the refused card file's size and mtime: told once
def jpg(w,h,sof=0xc0,bits=8,n=3,pad=0):  # an APP0 behind a fill byte, then the frame header
    return b'\xff\xd8\xff\xff\xe0\x00\x04ab'+bytes([0xff,sof])+struct.pack('>HBHHB',8+3*n,bits,h,w,n)+bytes(3*n)+b'\xff\xd9'+bytes(pad)
class LogoMachine(PowerMachine):
    failure=''
    def __init__(self,files=None):
        super().__init__(files=files); self.threads=[]; self.execs=[]; self.toasts=[]; self.rotated=None; self.child=False; self.exits=[]
        self.handlers[payload_syms['child_exit']]='l:exit'
        self.word(0xa3a270,0x1000010); self.handlers[0x1000010]='l:free'  # free's GOT slot, as Coverflow's
        for n in ('fseek@GLIBC_2.0','ftell@GLIBC_2.0','memcmp@GLIBC_2.0','strstr@GLIBC_2.0','pthread_create@GLIBC_2.2',
                  'fork@GLIBC_2.0','execl@GLIBC_2.0','exit@GLIBC_2.0','waitpid@GLIBC_2.0','open@GLIBC_2.0','navigator_to_with_context'): self.handlers[syms[n]]='l:'+n
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name.startswith('r:'+self.failure+'@') and (self.failure!='fopen' or self.text(u.reg_read(REGS[1]))=='wb'):
            self.returns(0 if self.failure in ('fopen','fwrite') else 0xffffffff); return
        if not name.startswith('l:'): return super().hook(u,address,size,unused)
        name=name[2:].split('@')[0]; a,b,c,d=[u.reg_read(r) for r in REGS]; self.calls.append((name,a,b,c)); ret=0
        if name=='fseek': f=self.open[a]; f[2]=signed(b)+(len(f[3]) if c==2 else 0)
        elif name=='ftell': ret=self.open[a][2]
        elif name=='memcmp': ret=int(bytes(u.mem_read(a,c))!=bytes(u.mem_read(b,c)))
        elif name=='strstr': i=self.text(a).find(self.text(b)); ret=a+i if i>=0 else 0
        elif name=='pthread_create': self.word(a,77); self.threads.append(c)
        elif name=='open': ret=0xffffffff  # the child's oom_score_adj write: refused here
        elif name=='fork': ret=0 if self.child else 4242
        elif name=='exit':  # the child ends: back to call()'s return, as if the thread had
            self.exits.append(a)
            for i,r in enumerate(SAVED): u.reg_write(r,0x12340000+i)
            u.reg_write(UC_MIPS_REG_SP,0x7000f000); u.reg_write(UC_MIPS_REG_PC,0x1000000); return
        elif name=='execl':
            sp=u.reg_read(UC_MIPS_REG_SP); args=[self.text(x) for x in (a,b,c,d)]
            while (x:=self.get(sp+16+4*(len(args)-4))): args.append(self.text(x))
            self.execs.append(args)
        elif name=='waitpid':
            assert a==4242; self.word(b,0); ret=a
            if self.rotated is not None: self.files[LOGO_ROT]=self.rotated
        elif name=='navigator_to_with_context':
            self.toasts.append(self.text(b+8)); assert len(self.toasts)<4,'a toast that keeps toasting'
            self.nested(sleep_hook,0x1234)  # its dialog's loop
        self.returns(ret,clobber=True)
    def sync(self,mounted=True):  # a poll with the card in or out; returns the threads it started
        self.files['/proc/mounts']=b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n' if mounted else b'none /mnt/data ubifs rw 0 0\n'
        self.threads=[]; self.pass_()
        for t in self.threads: self.call(address=t,args=(0,0,0,0),gap=0,clear=False)
        started=len(self.threads); self.pass_(1)  # the next pass, too soon to poll, toasts a failure
        return started
m=LogoMachine({LOGO_SRC:jpg(320,375)})
assert m.sync()==1 and m.files[LOGO_DST]==jpg(320,375) and m.renames==1 and LOGO_DST+'.tmp' not in m.files and not m.toasts; passed()
assert m.sync()==0 and m.renames==1; passed()  # still mounted: no edge, no sync
assert m.sync(False)==0 and m.sync()==1 and m.renames==1 and not m.toasts; passed()  # remounted, the same bytes: no write
m.files[LOGO_SRC]=jpg(320,375,pad=1); m.sync(False); m.sync(); assert m.files[LOGO_DST]==jpg(320,375,pad=1) and m.renames==2; passed()
del m.files[LOGO_SRC]; m.sync(False); m.sync(); assert LOGO_DST not in m.files and not m.toasts; passed()  # gone: the stock logo
m=LogoMachine(); assert m.sync()==1 and LOGO_DST not in m.files and not m.toasts and not m.execs; passed()
for bad in (jpg(375,375),jpg(320,375,n=1),jpg(320,375,sof=0xc2),jpg(320,375,bits=12),jpg(320,375,pad=512*1024),jpg(0,375),
            b'\xff\xd8\xff\xda\x00\x02',jpg(320,375)[:12],b'not a jpeg',b''):
    m=LogoMachine({LOGO_SRC:bad,LOGO_DST:b'old'}); m.sync()
    assert m.files[LOGO_DST]==b'old' and m.toasts==[LOGO_BAD] and not m.execs,bad[:16]
    assert not m.renames and LOGO_SEEN not in m.files,bad[:16]; passed()  # the emulator's stat fails: nothing recorded
    m.sync(False); m.sync(); assert m.toasts==[LOGO_BAD]*2,bad[:16]; passed()  # so a remount reports it again
m=LogoMachine({LOGO_SRC:jpg(375,320)}); m.child=True; m.sync()
assert m.exits==[127] and LOGO_DST not in m.files and m.execs==[['/usr/bin/ffmpeg','/usr/bin/ffmpeg','-nostdin','-v','quiet','-i',LOGO_SRC,'-vf','transpose=1','-pix_fmt',
                  'yuvj444p','-q:v','2','-f','mjpeg','-y',LOGO_ROT]],m.execs; passed()
m=LogoMachine({LOGO_SRC:jpg(375,320)}); m.rotated=jpg(320,375,pad=2); m.sync()
assert not m.execs and not m.exits
assert m.files[LOGO_DST]==m.rotated and LOGO_ROT not in m.files and not m.toasts; passed()
for rotated in (None,jpg(375,320)):  # ffmpeg failed, or wrote something else
    m=LogoMachine({LOGO_SRC:jpg(375,320),LOGO_DST:b'old'}); m.rotated=rotated; m.sync()
    assert m.files[LOGO_DST]==b'old' and m.toasts==[LOGO_BAD]; passed()
for failure in ('fopen','fwrite','fclose','rename'):
    m=LogoMachine({LOGO_SRC:jpg(320,375),LOGO_DST:b'old'}); m.failure=failure; m.sync()
    assert m.files[LOGO_DST]==b'old' and LOGO_DST+'.tmp' not in m.files and m.toasts==[LOGO_FAIL],failure; passed()
print('Boot logo: mount edges, validation, rotation, unchanged bytes and failed writes passed.')

# System boot row: regular-file gating, persisted choice and transactional failures.
class BootMachine(ResumeMachine):
    failure=''
    def hook(self,u,address,size,unused):
        name=self.handlers.get(address,'')
        if name=='boot_memcmp':
            a,b,c=[u.reg_read(r) for r in REGS[:3]]
            self.returns(int(bytes(u.mem_read(a,c))!=bytes(u.mem_read(b,c)))); return
        if name=='unlink@GLIBC_2.0': self.files.pop(self.text(u.reg_read(UC_MIPS_REG_A0)),None)
        if name in ('r:fopen@GLIBC_2.2','r:fwrite@GLIBC_2.0','r:fclose@GLIBC_2.2','r:rename@GLIBC_2.0','fflush@GLIBC_2.0','fsync@GLIBC_2.0'):
            a=u.reg_read(UC_MIPS_REG_A0)
            fail=name.removeprefix('r:').split('@')[0]
            if self.failure==fail and (fail!='fopen' or self.text(u.reg_read(UC_MIPS_REG_A1))=='wb'):
                self.returns(0 if fail in ('fopen','fwrite') else 0xffffffff); return
        return super().hook(u,address,size,unused)
def boot_row(value=None,mode=0o100755):
    path='/mnt/data/boot-target'
    m=BootMachine({} if value is None else {path:value}); m.rockbox_mode=mode; m.restack=True
    m.handlers[syms['memcmp@GLIBC_2.0']]='boot_memcmp'
    m.handlers[int(manifest['patch_symbols']['stock_power_trampoline'],16)]='stock_power'
    m.mock('ferror@GLIBC_2.0','fflush@GLIBC_2.0','fsync@GLIBC_2.0','fileno@GLIBC_2.0','navigator_to_with_context')
    entries=[m.entry(0) for _ in range(2)]
    view=m.node('scroll_view','scroll_view_powermanager',entries.copy())
    m.top=m.node('window','powermanager_page',[m.node('list_view','list_view_powermanager',[view])])
    assert m.call(address=HOOKS['systemset_powermanager_page_init'][0],args=(m.top,5,0,0),gap=0)==0
    assert m.calls[0][:3]==('stock_power',m.top,5)
    kids=m.nodes[view]['children']
    if mode!=0o100755:  # no Boot to
        assert len(kids)==8  # stock's two, then Standby Limit, Charge Limit, USB DAC Charging, Smart Wi-Fi, In-Vehicle and Battery History (Restart and Shut Down are General's)
        return m,None,None
    assert len(kids)==9  # Boot to last
    button=m.nodes[kids[8]]['children'][0]
    label=m.nodes[button]['children'][1]
    return m,button,label
def boot_said(m,button,label):  # the row as it reads: the title, and its value on the right column (row_value_text, list_value)
    vals=[m.nodes[c]['text'] for c in m.nodes[button]['children'] if m.nodes[c].get('name')=='_setting_value']
    return m.nodes[label]['text']+(f': {vals[0]}' if vals else '')
def toggle_boot(m,button):
    f,ctx=m.handler(button,O['EVT_CLICK']); m.calls=[]
    assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0
for value,expected in ((None,'Rockbox'),(b'bad','Rockbox'),(b'q2pod','Q2 Muse'),(b'rockbox','Rockbox'),
                       (b'q2pod\n','Q2 Muse'),(b'q2pod\0bad','Rockbox')):
    m,button,label=boot_row(value)
    assert boot_said(m,button,label)=='Boot to: '+expected
    toggle_boot(m,button)
    next_value=b'rockbox' if expected=='Q2 Muse' else b'q2pod'
    assert m.files['/mnt/data/boot-target']==next_value
    assert boot_said(m,button,label)=='Boot to: '+('Rockbox' if next_value==b'rockbox' else 'Q2 Muse')
    restarted,b2,l=boot_row(next_value); assert boot_said(restarted,b2,l)==boot_said(m,button,label)
    passed()
for mode in (0,0o040755,0o020644): boot_row(mode=mode); passed()
for failure in ('fopen','fwrite','fflush','fsync','fclose','rename'):
    m,button,label=boot_row(b'rockbox'); m.failure=failure
    toggle_boot(m,button)
    assert m.files['/mnt/data/boot-target']==b'rockbox' and boot_said(m,button,label)=='Boot to: Rockbox'
    assert '/mnt/data/boot-target.tmp' not in m.files
    assert any(c[0]=='navigator_to_with_context' for c in m.calls)
    passed()
m,button,label=boot_row(); m.rockbox_mode=0; toggle_boot(m,button)
assert boot_said(m,button,label)=='Boot to: Rockbox' and '/mnt/data/boot-target' not in m.files; passed()
# Wheel centre reaches Boot to through the existing selection/dispatch path.
m,button,label=boot_row(); view=m.nodes[m.top]['children'][0]; view=m.nodes[view]['children'][0]
m.paint(view)
for _ in range(8): m.call()  # stock's two, our six, then Boot to
assert m.confirm()==11 and m.dispatched()[0][1]==button; passed()
# General (Settings' System page, ringnav_systemset): About, System Update, Storage, USB, Date & Time, App Store, stock's
# Network row, Reset Settings, then stock's other eight hidden (the wheel skips them), a --dev build's Developer Options
# (adb_trust.c), Rockbox while the card has it (rockbox_open, docs/boot.md#rockbox-from-home), a 12 px gap, Restart and
# Shut Down (power_rows.c). The rows stock moved to its System page open stock's pages; Car Remote and Controls are Settings' own.
for mode in (0o100755,0):
    m=BootMachine({}); m.rockbox_mode=mode; m.restack=True
    m.handlers[int(manifest['patch_symbols']['stock_systemset_trampoline'],16)]='stock_systemset'
    entries=[m.entry(0) for _ in range(9)]
    view=m.node('scroll_view','scroll_view_sysset',entries.copy())
    m.top=m.node('window','sysset_page',[m.node('list_view','list_view_sysset',[view])])
    assert m.call(address=HOOKS['systemset_sysset_page_init'][0],args=(m.top,5,0,0),gap=0)==0
    dev=1 if manifest['dev'] else 0; rb=1 if mode else 0
    kids=m.nodes[view]['children']; assert len(kids)==19+dev+rb
    assert [kids.index(e) for e in entries]==[7,8,6,9,10,11,12,13,14]  # Network (stock's third) in place, the rest hidden; Reset is last
    assert [m.nodes[e]['visible'] for e in entries]==[0,0,1,0,0,0,0,0,0]
    about,update,storage,usb,date,apps,reset=(m.nodes[kids[i]]['children'][0] for i in (0,1,2,3,4,5,18+dev+rb))
    if rb:
        rockbox=m.nodes[kids[15+dev]]['children'][0]
        assert m.nodes[m.nodes[rockbox]['children'][1]]['text']=='Rockbox' and m.handler(rockbox,O['EVT_CLICK'])[0]==payload_syms['rockbox_open']
    if dev:
        devopts=m.nodes[kids[15]]['children'][0]
        assert m.nodes[m.nodes[devopts]['children'][1]]['text']=='Developer Options' and m.handler(devopts,O['EVT_CLICK'])[0]==payload_syms['adb_dev_open']
    assert m.nodes[m.nodes[usb]['children'][1]]['text']=='USB' and m.handler(usb,O['EVT_CLICK'])[0]==payload_syms['usb_pick_open']
    assert m.nodes[m.nodes[apps]['children'][1]]['text']=='App Store' and m.handler(apps,O['EVT_CLICK'])[0]==payload_syms['shelf_open']
    assert m.nodes[m.nodes[storage]['children'][1]]['text']=='Storage' and m.handler(storage,O['EVT_CLICK'])[0]==payload_syms['storage_open']
    assert m.nodes[m.nodes[update]['children'][1]]['text']=='Software Update' and m.handler(update,O['EVT_CLICK'])[0]==payload_syms['sw_update_open']
    for b,key,page in ((date,'system_time','systemset/datetime_page'),(reset,'system_reset','systemset/reset_page'),(about,'system_about','systemset/about_page')):
        f,ctx=m.handler(b,O['EVT_CLICK']); assert m.nodes[m.nodes[b]['children'][1]]['text']==key and m.nodes[m.nodes[b]['children'][0]]['image']==key
        m.calls=[]; assert m.call(address=f,args=(ctx,m.event,0,0),gap=0)==0 and m.opened[-1]==(page,None),key
    assert m.nodes[kids[15+dev+rb]]['type']=='label' and [m.nodes[m.nodes[m.nodes[k]['children'][0]]['children'][1]]['text'] for k in kids[16+dev+rb:18+dev+rb]]==['Restart','Shut Down']  # then Reset Settings, last
    passed()
print('Boot settings: conditional row/order, restart, card removal and failed atomic writes passed.')

from build import AUDIO_EXTENSION_CALLS
for site in AUDIO_EXTENSION_CALLS:
    for ext, accepted in (('.m4b',1),('.M4B',1),('.m4B',1),('.M4b',1),('.m4a',1),('.M4A',1),('.flac',1),('.mp3',1),('.m4b.tmp',0),('.pdf',0)):
        m=Machine(); m.mock('strstr@GLIBC_2.0')
        formats=m.string('.mp3 .flac .m4a .M4A'); suffix=m.string(ext)
        m.word(0x7000f000+(0xdd4 if site==0x5d4cac else 0xd64),formats)
        def extension_return(u,address,size,unused):
            u.reg_write(UC_MIPS_REG_PC,0x1000000)
        m.u.hook_add(UC_HOOK_CODE,extension_return,begin=site+8,end=site+8)
        assert bool(m.call(address=site,args=(formats,suffix,0,0),gap=0))==bool(accepted)
        passed()
    # Folder view lists videos in the card's Videos folder (#128); the library scan and other folders never do.
    for folder in (b'/mnt/mmc/vIdEoS/Films',b'/mnt/mmc/Videos',b'/mnt/mmc/Music',b'/mnt/mmc/VideosOld',b'/mnt/sda1/Videos'):
        for ext in ('.mp4','.MKV','.pdf'):
            m=Machine(); m.mock('strstr@GLIBC_2.0'); m.u.mem_write(syms['g_folder_path'],folder+b'\0')
            formats=m.string('.mp3 .flac'); suffix=m.string(ext)
            m.word(0x7000f000+(0xdd4 if site==0x5d4cac else 0xd64),formats)
            m.u.hook_add(UC_HOOK_CODE,extension_return,begin=site+8,end=site+8)
            listed=site==0x5d4cac and folder in (b'/mnt/mmc/vIdEoS/Films',b'/mnt/mmc/Videos') and ext!='.pdf'
            assert bool(m.call(address=site,args=(formats,suffix,0,0),gap=0))==listed,(site,folder,ext)
            passed()

# M4B is spoken audio anywhere, at short duration; persistence and CUE rules stay shared.
for path in ('/mnt/mmc/Novel.m4b','/mnt/mmc/Music/Novel.M4B','/mnt/mmc/Novel.m4B'):
    m=ResumeMachine(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]
    m.word(r+O['REC_PATH'],m.string(path)); playing(m,0,200,pos=0,n=110)
    assert m.places()==[(fnv(path),61)] and '/mnt/mmc/.q2pod/ringnav-plays' not in m.files and '/mnt/mmc/.scrobbler.log' not in m.files
    m.poll(110,200,n=3)
    files=m.files
    m=ResumeMachine(files); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]; m.word(r+O['REC_PATH'],m.string(path))
    m.poll(0,200,pos=0,n=2); playing(m,1,200,n=2); assert m.seeks==[110]
    m.poll(195,200,n=3); assert not m.places()
    passed()
m=ResumeMachine(); r=m.items(m.get(syms['mcl_pdeqplaylist']))[0]
m.word(r+O['REC_PATH'],m.string('/mnt/mmc/cue.m4b')); m.word(r+O['REC_CUE_START'],200)
playing(m,0,200,pos=0,n=110); assert m.places()==[(fnv('/mnt/mmc/cue.m4b'),261)] and not m.seeks; passed()  # its file's place

