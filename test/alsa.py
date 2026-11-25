#!/usr/bin/env python3
"""Run the helper against stock MIPS ALSA. Usage: test/alsa.py BUILD QEMU_MIPSEL.
The virtual ioplug replaces Bluetooth hardware, preserving the plug/prepare lock path.
"""
import os, pathlib, re, subprocess, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from build import FLAGS, ROOT

# ALSA 1.1.6 ioplug ABI, as shipped on the Q2. No cross-compiler sysroot needed.
PLUGIN = r'''
typedef struct io io;
struct callbacks {
    int (*start)(io *), (*stop)(io *);
    long (*pointer)(io *);
    void *transfer, *close, *hw_params, *hw_free, *sw_params;
    int (*prepare)(io *);
    void *rest[11];
};
struct io {
    unsigned version;
    const char *name;
    unsigned flags;
    int poll_fd;
    unsigned poll_events, mmap_rw;
    const struct callbacks *callback;
    void *private_data, *pcm;
    int stream, state;
    volatile unsigned long appl_ptr, hw_ptr;
    int nonblock, access, format;
    unsigned channels, rate;
    unsigned long period_size, buffer_size;
};
void *calloc(unsigned, unsigned);
int pipe(int *);
int snd_pcm_ioplug_create(io *, const char *, int, int);
int snd_pcm_ioplug_set_param_list(io *, int, unsigned, const unsigned *);
int snd_pcm_ioplug_set_param_minmax(io *, int, unsigned, unsigned);
static int action(io *p) { return 0; }
#ifdef POLL_PIPE /* ...and finds its writes played at once, started or not */
static long pointer(io *p) { return p->appl_ptr % p->buffer_size; }
#else
static long pointer(io *p) { return p->state==3 || p->state==5 ? p->appl_ptr % p->buffer_size : 0; }
#endif
static const struct callbacks callbacks={.start=action,.stop=action,.pointer=pointer,.prepare=action};
int _snd_pcm_mock_open(void **out, const char *name, void *root, void *conf, int stream, int mode) {
    io *p=calloc(1,sizeof *p);
    if (!p) return -12;
    p->version=0x010002; p->name="test"; p->mmap_rw=1; p->callback=&callbacks;
#ifdef POLL_PIPE /* a writer that never stops (the tuner's) waits on poll_fd for room: a pipe's end, always writable */
    int fds[2];
    if (pipe(fds)) return -12;
    p->poll_fd=fds[1]; p->poll_events=4; /* POLLOUT */
#endif
    int r=snd_pcm_ioplug_create(p,name,stream,mode);
    if (r<0) return r;
    unsigned access[]={0,3}, format[]={2};
    if ((r=snd_pcm_ioplug_set_param_list(p,0,2,access))<0 ||
        (r=snd_pcm_ioplug_set_param_list(p,1,1,format))<0 ||
        (r=snd_pcm_ioplug_set_param_minmax(p,6,2,1024))<0 ||
        (r=snd_pcm_ioplug_set_param_minmax(p,4,PCM_RATE/100*4,16384))<0 ||
        (r=snd_pcm_ioplug_set_param_minmax(p,2,2,2))<0 ||
        (r=snd_pcm_ioplug_set_param_minmax(p,3,PCM_RATE,PCM_RATE))<0) return r;
    *out=p->pcm; return 0;
}
char __snd_pcm_mock_open_dlsym_pcm_001;
'''

build, qemu = pathlib.Path(sys.argv[1]).resolve(), str(pathlib.Path(sys.argv[2]).resolve())
with tempfile.TemporaryDirectory(prefix='q2-alsa-') as directory:
    tmp=pathlib.Path(directory)
    runtime=tmp/'runtime'
    subprocess.run(['unsquashfs','-no-progress','-d',str(runtime),str(build/'stock.squashfs'),
                    'lib','usr/lib'],check=True,stdout=subprocess.DEVNULL)
    flags=[f for f in FLAGS if f not in ('-mno-abicalls','-G0')]+['-mnan=2008','-mabs=2008','-mabicalls']
    libs=[build/n for n in ('libc-2.28.so','libpthread-2.28.so','libasound.so.2.0.0','libm-2.28.so')]
    def compile(source, target, *extra):
        subprocess.run(['clang',*flags,*extra,'-c',str(source),'-o',str(target)],check=True)
    # Identical writer without the workaround must reproduce the first-write deadlock.
    noop=tmp/'noop.c'
    noop.write_text('int test_setenv(const char *a,const char *b,int c) {return 0;}\n')
    compile(noop,tmp/'noop.o')
    compile(ROOT/'patch/video.c',tmp/'baseline.o','-Dsetenv=test_setenv')
    for name in ('fmrx','tuner_core'): compile(ROOT/f'patch/{name}.c',tmp/f'{name}.o')  # the tuner's (HELPER_SOURCES)
    baseline=tmp/'baseline'
    subprocess.run(['ld.lld','-m','elf32ltsmip','-e','__start','--dynamic-linker','/lib/ld-linux-mipsn8.so.1',
                    str(tmp/'baseline.o'),str(tmp/'fmrx.o'),str(tmp/'tuner_core.o'),str(tmp/'noop.o'),str(build/'start.o'),
                    *map(str,libs),'-o',str(baseline)],check=True)
    source=tmp/'plugin.c'; source.write_text(PLUGIN)
    plugin=tmp/'mock.so'
    config=tmp/'alsa.conf'
    data=bytes(2048*4)
    env=os.environ|{'ALSA_CONFIG_PATH':str(config),'LIBASOUND_THREAD_SAFE':'1'}
    def command(binary):
        return [qemu,'-strace','-L',str(runtime),str(binary),'-s','test','100']
    for rate, nested in ((44100,False),(44100,True),(48000,True)):
        compile(source,tmp/'plugin.o','-fPIC',f'-DPCM_RATE={rate}')
        subprocess.run(['ld.lld','-m','elf32ltsmip','-shared',str(tmp/'plugin.o'),*map(str,libs),'-o',str(plugin)],check=True)
        slave='{ type plug slave.pcm mock }' if nested else 'mock'
        config.write_text(f'pcm_type.mock {{ lib "{plugin}" }}\npcm.mock {{ type mock }}\n'
                          f'pcm.test {{ type plug slave.pcm {slave} }}\n')
        try:
            subprocess.run(command(baseline),input=data,env=env,capture_output=True,timeout=3,check=True)
        except subprocess.TimeoutExpired as exc:
            assert rate==44100, 'Rate-converting control unexpectedly blocked'
            trace=exc.stderr.decode()
            assert len(re.findall(r'read\(0,.*2048\) = 2048',trace))==1, trace[-3000:]
            assert 'FUTEX_WAIT' in trace, trace[-3000:]
        else:
            assert rate!=44100, 'Unfixed stock ALSA did not reproduce the first-write lock'
        result=subprocess.run(command(build/'q2video'),input=data,env=env,capture_output=True,timeout=3,check=True)
        trace=result.stderr.decode()
        assert len(re.findall(r'read\(0,.*2048\) = 2048',trace))==4, trace[-3000:]
        assert re.search(r'read\(0,.*2048\) = 0',trace), trace[-3000:]
        control='old writer blocks' if rate==44100 else 'rate-converting control passes'
        print(f'Stock ALSA: {rate} Hz, {1+int(nested)} plug layers: {control}; fixed helper writes all PCM.')

    # Radio's receiver (q2video -t, docs/internals.md#output-failure) paused lets its PCM go once it has had nothing to
    # play for TUNER_RELEASE_MS, and takes it again on Play. Before, it dropped the PCM and kept it open, as the one
    # read on a Q2 on 2026-11-25 (state SETUP, appl_ptr 512, hw_ptr 0) that the Library's player met as busy for 80
    # minutes. demo's datagrams on the receiver's socket, /tmp/q2tuner.sock: p's fifth field is the gain, 0 a pause.
    import socket, threading, time
    compile(source,tmp/'plugin.o','-fPIC','-DPCM_RATE=48000','-DPOLL_PIPE')
    subprocess.run(['ld.lld','-m','elf32ltsmip','-shared',str(tmp/'plugin.o'),*map(str,libs),'-o',str(plugin)],check=True)
    sock_path='/tmp/q2tuner.sock'
    receiver=subprocess.Popen([qemu,'-L',str(runtime),str(build/'q2video'),'-t','test'],env=env,stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
    said=[]
    reader=threading.Thread(target=lambda: said.extend(receiver.stderr),daemon=True); reader.start()
    def send(text):
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as s: s.sendto(text.encode(),sock_path)
    def wait(line,seconds):
        end=time.monotonic()+seconds
        while time.monotonic()<end and not any(line in l for l in said) and receiver.poll() is None: time.sleep(0.05)
        return any(line in l for l in said)
    try:
        end=time.monotonic()+10
        while not os.path.exists(sock_path) and time.monotonic()<end: time.sleep(0.05)
        assert os.path.exists(sock_path) and receiver.poll() is None, f'the receiver did not start: {said}'
        time.sleep(0.5)  # the hiss playing
        assert not any('let go' in l for l in said), said
        paused=time.monotonic(); send('p-40 0 0 0 0')
        assert wait('the receiver let go of test: nothing to play',60), f'a paused receiver kept its PCM: {said} (exit {receiver.poll()})'
        held=time.monotonic()-paused
        assert held>=1.9, f'let go {held:.1f} s after the pause, before TUNER_RELEASE_MS'
        send('p-40 0 0 1 0')
        assert wait('the receiver took test again',60), f'the receiver did not take its PCM again on Play: {said}'
    finally:
        send('q') if os.path.exists(sock_path) else None
        try: receiver.wait(timeout=10)
        except subprocess.TimeoutExpired: receiver.kill(); receiver.wait()
    print(f'Radio\'s receiver paused let go of its PCM after {held:.1f} s and took it again on Play.')
