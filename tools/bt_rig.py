#!/usr/bin/env python3
"""The Bluetooth receiver rig: this laptop as an A2DP headset for a Q2, recording what it hears (docs/bt-rig.md).

Runs in WSL on a Windows laptop whose Bluetooth radio Windows owns. The receiving and recording is
tools/bt_rig.cs, built into a Windows program outside the repository; the Q2 is driven over the
Windows adb (USB) and its coda-rc daemon through an adb forward.

usage: bt_rig.py COMMAND ...
  build                              compile btrig.exe (Roslyn from the .NET SDK, .NET Framework 4.8, no NuGet)
  status                             the laptop's endpoints and paired sources, the Q2's link, player and settings
  pair, unpair                       pair the laptop with the Q2 (answering its dialog by input events), or forget it
  record --seconds N --out F.wav     receive from the Q2 and record N seconds; F.json beside it
  play ...                           drive the Q2 through coda-rc (see `play --help`)
  ends N [--before S]                let N songs end by themselves (each sought to S s before its end), with the log
  log --from ISO --to ISO            the Q2's unified log lines in a time window
  analyse F.wav [--ref A ...]        song changes in a recording: gap, overlap, envelope, clipping
  calibrate DIR [--codec C]          known changes (cut, 5 s fade, 2 s gap) through a codec, analysed and checked
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import time

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

ADB_EXE = os.environ.get("Q2_ADB", "/mnt/c/Users/gray/AppData/Local/Android/Sdk/platform-tools/adb.exe")
SERIAL = os.environ.get("Q2_SERIAL", "Q2CODA")
WIN_DIR = os.environ.get("BTRIG_DIR", "/mnt/c/Users/gray/AppData/Local/q2-bt-rig")  # build output, outside the repo
Q2_MAC = os.environ.get("Q2_BT_MAC", "A4:E8:8D:7B:40:31")
CODA_PORT = int(os.environ.get("BTRIG_CODA_PORT", "17720"))  # Windows' end of `adb forward` to the Q2's 7720


# ---- the Q2 over adb

def adb(*args, timeout=30, data=None):
    """Windows adb for this Q2 (Linux adb is not used here); stdout as bytes."""
    r = subprocess.run([ADB_EXE, "-s", SERIAL, *args], input=data, capture_output=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"adb {' '.join(args[:2])}: {r.stderr.decode(errors='replace').strip() or r.returncode}")
    return r.stdout


def q2sh(script, timeout=30):
    return adb("exec-out", script, timeout=timeout).decode("utf-8", "replace")


def win_path(p):
    """A /mnt/c path as Windows writes it."""
    p = str(pathlib.Path(p).resolve())
    m = re.match(r"/mnt/([a-z])/(.*)", p)
    if not m:
        raise ValueError(f"{p} is not on a Windows drive (/mnt/X/...): btrig.exe writes there")
    return f"{m.group(1).upper()}:\\" + m.group(2).replace("/", "\\")


# ---- btrig.exe

BUILD_REFS = [r"C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8.1\{}".format(n)
              for n in ("mscorlib.dll", "System.dll", "System.Core.dll", r"Facades\System.Runtime.dll",
                        r"Facades\System.Threading.Tasks.dll", r"Facades\System.Runtime.InteropServices.WindowsRuntime.dll")]
BUILD_REFS += [r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\System.Runtime.WindowsRuntime.dll",
               r"C:\Program Files (x86)\Windows Kits\10\UnionMetadata\10.0.26100.0\Windows.winmd"]
DOTNET = "/mnt/c/Program Files/dotnet/dotnet.exe"


def roslyn():
    """The newest SDK's Roslyn csc.dll, as a Windows path."""
    sdks = sorted(pathlib.Path("/mnt/c/Program Files/dotnet/sdk").glob("*/Roslyn/bincore/csc.dll"),
                  key=lambda p: [int(x) if x.isdigit() else 0 for x in re.split(r"[.-]", p.parts[-4])])
    if not sdks:
        raise RuntimeError("no .NET SDK with Roslyn under C:\\Program Files\\dotnet\\sdk")
    return win_path(sdks[-1])


def build():
    out = pathlib.Path(WIN_DIR)
    out.mkdir(parents=True, exist_ok=True)
    src = TOOLS / "bt_rig.cs"
    (out / "BtRig.cs").write_bytes(src.read_bytes())
    cmd = [DOTNET, roslyn(), "-nologo", "-noconfig", "-nostdlib", "-langversion:latest", "-platform:x64", "-optimize",
           "-out:btrig.exe", *[f"-r:{r}" for r in BUILD_REFS], "BtRig.cs"]
    r = subprocess.run(cmd, cwd=out, capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise RuntimeError(f"csc failed:\n{r.stdout}{r.stderr}")
    return out / "btrig.exe"


def btrig_exe():
    exe = pathlib.Path(WIN_DIR) / "btrig.exe"
    src = TOOLS / "bt_rig.cs"
    built = pathlib.Path(WIN_DIR) / "BtRig.cs"
    if not exe.exists() or not built.exists() or built.read_bytes() != src.read_bytes():
        build()
    return exe


def btrig(*args, timeout=120):
    """btrig.exe's JSON lines."""
    r = subprocess.run([str(btrig_exe()), *args], capture_output=True, text=True, timeout=timeout,
                       cwd=WIN_DIR)
    out = [json.loads(l) for l in r.stdout.splitlines() if l.startswith("{")]
    if r.returncode:
        raise RuntimeError(f"btrig {args[0]}: {out[-1] if out else r.stderr.strip()}")
    return out


# ---- the Q2: input (docs/remote.md#input, written to /dev/input over adb), screen, settings, log

def q2_input(kind, *args):
    """A key, wheel turn or tap as tools/q2remote.py makes it, written by the Q2's own shell over adb."""
    import q2remote as qr
    devs = qr.parse_devices(q2sh("cat /proc/bus/input/devices"))
    need = {"key": "md-gpio-keys", "tap": "hyn_ts", "wheel": "hyn_sliderbar"}[kind]
    path = next(f"/dev/input/event{d['event']}" for d in devs if d["name"] == need)
    if kind == "key":
        script = qr.event_writer(path, qr.key_events(qr.KEYS[args[0]]), 80000)
    elif kind == "tap":
        script = qr.event_writer(path, qr.touch_batches([(int(args[0]), int(args[1]))]))
    else:
        ticks = int(args[0])
        units = qr.wheel_units(q2sh("cat /mnt/data/config.ini"))
        batches = [qr.event(qr.EV_ABS, qr.ABS_X, 100) + qr.event(qr.EV_ABS, qr.ABS_Y, 100) +
                   qr.event(qr.EV_ABS, qr.ABS_PRESSURE, 1) + qr.event(qr.EV_KEY, qr.BTN_TOUCH, 1) + qr.event(qr.EV_SYN, 0, 0)]
        batches += [qr.event(qr.EV_ABS, qr.ABS_X, x) + qr.event(qr.EV_ABS, qr.ABS_Y, 100 + i % 2) + qr.event(qr.EV_SYN, 0, 0)
                    for i, x in enumerate(qr.wheel_positions(ticks, units))]
        batches.append(qr.event(qr.EV_ABS, qr.ABS_PRESSURE, 0) + qr.event(qr.EV_KEY, qr.BTN_TOUCH, 0) + qr.event(qr.EV_SYN, 0, 0))
        script = qr.event_writer(path, batches)
    q2sh(script)


def q2_shot(out):
    """The Q2's screen as a PNG (q2remote --shot over adb, as tools/q2remote.py shot --adb)."""
    import gzip
    import q2remote as qr
    raw = gzip.decompress(adb("exec-out", "/usr/bin/q2remote --shot 2>/dev/null | gzip -1"))
    pathlib.Path(out).write_bytes(qr.framebuffer_png(raw))


def q2_backlight():
    """demo's backlight byte, read as tools/q2remote.py `state` reads it (nothing written)."""
    import q2remote as qr
    _, addr = qr.GLOBALS["backlight"]
    v = q2sh(f"dd if=/proc/$(pidof -s demo)/mem bs=1 skip={addr} count=1 2>/dev/null | xxd -p").strip()
    return int(v, 16) if v else None


SETTINGS = ("VOLUME", "BT_VOLUME", "BTLINKMAC", "BTCODING", "BTVOLLINK", "GAPLESS", "XFADE", "PLAYMODE")


def q2_settings():
    """The config.ini keys the rig may touch, coda-rc's Now Playing file, the build and the paired and connected
    Bluetooth devices."""
    ini = q2sh("cat /mnt/data/config.ini")
    cfg = {k: v for k, v in re.findall(r"^(\w+)=(.*?)\s*$", ini, re.M) if k in SETTINGS}
    now = dict(l.split("=", 1) for l in q2sh("cat /tmp/coda-rc.now").splitlines() if "=" in l)
    return {"build": q2sh("q2update version 2>/dev/null").split()[2:3], "config": cfg, "now": now,
            "bluetooth": q2_bluez()}


def q2_bluez():
    """BlueZ's devices: address, name, paired, trusted, connected."""
    out = q2sh("dbus-send --system --print-reply --dest=org.bluez / org.freedesktop.DBus.ObjectManager.GetManagedObjects")
    devs = {}
    cur = None
    for line in out.splitlines():
        m = re.search(r'object path "/org/bluez/hci0/dev_([0-9A-F_]{17})"$', line)
        if m:
            cur = devs.setdefault(m.group(1).replace("_", ":"), {})
            continue
        m = re.search(r'object path "', line)
        if m:
            cur = None
            continue
        if cur is not None:
            k = re.search(r'string "(Name|Paired|Trusted|Connected)"', line)
            if k:
                cur["_k"] = k.group(1)
                continue
            if "_k" in cur:
                v = re.search(r'variant\s+(?:string "(.*)"|boolean (true|false))', line)
                if v:
                    key = cur.pop("_k")
                    cur.setdefault(key.lower(), v.group(1) if v.group(1) is not None else v.group(2) == "true")
    return devs


def q2_codec():
    """bluealsa's A2DP PCMs: their device, codec and rate, from its D-Bus manager."""
    out = q2sh("dbus-send --system --print-reply --dest=org.bluealsa /org/bluealsa org.bluealsa.Manager1.GetPCMs 2>&1")
    pcms = []
    for block in re.split(r'object path "(?=/org/bluealsa/)', out)[1:]:
        path = block.split('"', 1)[0]
        info = {"pcm": path}
        for key in ("Codec", "Sampling", "Channels", "Mode", "Format", "Delay", "Volume"):
            m = re.search(r'string "%s"\s*variant\s+\w+ "?([^"\n]*)"?' % key, block)
            if m:
                info[key.lower()] = m.group(1).strip()
        pcms.append(info)
    return pcms


def q2_clock_offset():
    """The Q2's clock less this machine's, in seconds (to about half a second: busybox date has whole seconds)."""
    t0 = time.time()
    q = int(q2sh("date -u +%s").strip())
    t1 = time.time()
    return q + 0.5 - (t0 + t1) / 2


def parse_utc(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def q2_log(t_from, t_to, offset=0.0):
    """The unified log's lines (q2guard --dump) whose time is within [t_from, t_to] (this machine's Unix seconds;
    offset is the Q2's clock less this machine's)."""
    out = []
    for line in q2sh("q2guard --dump 2>/dev/null", timeout=60).splitlines():
        m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z?)\s", line)
        if not m:
            continue
        t = parse_utc(m.group(1)) - offset
        if t_from <= t <= t_to:
            out.append(line)
    return out


# ---- coda-rc (docs/remote-protocol.md), the Q2's Car Remote, for Now Playing and transport

KEYS_DIR = pathlib.Path(os.environ.get("BTRIG_KEYS", pathlib.Path.home() / ".config/q2-bt-rig/coda-rc"))
CLIENT_NAME = "bt-rig"


class PipeSock:
    """A TCP connection made on the Windows side (btrig.exe pipe HOST PORT) and spoken through its stdin and stdout:
    WSL cannot reach an adb forward, which listens on Windows' loopback, and may have no route to the Q2's Wi-Fi."""

    def __init__(self, host, port):
        import select
        self.select = select
        self.p = subprocess.Popen([str(btrig_exe()), "pipe", host, str(port)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, cwd=WIN_DIR)
        assert self.p.stdin is not None and self.p.stdout is not None
        self.w, self.r = self.p.stdin, self.p.stdout
        self.timeout = None

    def settimeout(self, t):
        self.timeout = t

    def sendall(self, b):
        self.w.write(b)
        self.w.flush()

    def recv(self, n):
        fd = self.r.fileno()
        if not self.select.select([fd], [], [], self.timeout)[0]:
            raise TimeoutError("coda-rc: no answer")
        return os.read(fd, n)

    def close(self):
        self.w.close()
        self.p.wait(timeout=10)


def coda_sock():
    """A connection to the Q2's coda-rc: `adb forward` of CODA_PORT on Windows to the Q2's 7720, through PipeSock."""
    adb("forward", f"tcp:{CODA_PORT}", "tcp:7720")
    return PipeSock("127.0.0.1", CODA_PORT)


def coda(pair=False):
    """An authenticated coda-rc session (tools/coda_rc_client.py's connect, over coda_sock). A first pairing reads the
    code from the daemon's /tmp/coda-rc.pair over adb, so no one types it; the Q2 then lists `bt-rig` under
    Settings > Car Remote."""
    import coda_rc_client as cr

    def code():
        for _ in range(50):
            m = re.match(r"(\d{6})\n", q2sh("cat /tmp/coda-rc.pair 2>/dev/null"))
            if m:
                return m.group(1)
            time.sleep(0.2)
        raise RuntimeError("the Q2 showed no pairing code (/tmp/coda-rc.pair)")
    s = cr.Session(coda_sock(), CLIENT_NAME)
    info = s.hello()
    known = cr.load_key(KEYS_DIR, info["device_id"])
    if known and not pair:
        s.sock.close()
        s = cr.Session(coda_sock(), CLIENT_NAME)
        info = s.hello(known[0])
        if info["paired"]:
            s.auth(known[1])
            s.info = info
            return s
    point = s.pair_start()
    peer_id, ltk = s.pair_finish(code(), point)
    cr.save_key(KEYS_DIR, info["device_id"], peer_id, ltk)
    s.auth(ltk)
    s.info = info
    return s


# ---- pairing

def guard_start():
    """btrig guard, for the pairing's length: Windows makes a newly linked audio device its default output, and the
    Q2 is one (its Bluetooth receiver offers A2DP Sink); each role's default goes back at once to what it was."""
    stop = pathlib.Path(WIN_DIR) / "guard.stop"
    stop.unlink(missing_ok=True)
    return subprocess.Popen([str(btrig_exe()), "guard", "Q2", "600"], stdout=subprocess.PIPE, text=True, cwd=WIN_DIR)


def guard_stop(g):
    """Ends the guard; its lines (what it put back, and when)."""
    (pathlib.Path(WIN_DIR) / "guard.stop").write_text("")
    out, _ = g.communicate(timeout=30)
    (pathlib.Path(WIN_DIR) / "guard.stop").unlink(missing_ok=True)
    return [json.loads(l) for l in out.splitlines() if l.startswith("{")]


OK_TAP = (187, 280)  # the lower of the two rows on the Bluetooth Code dialog's 375 x 320 screen (V1UQi)


def pair(shots=None):
    """Pair this laptop with the Q2. Windows starts it (btrig pair: WinRT custom pairing, accepted there, no Windows
    dialog); the Q2 asks with a numeric comparison dialog, Cancel lit. Once the laptop is the one device linked to
    the Q2, the wheel lights the second row and a tap presses it, both written as input events (docs/remote.md#input),
    with a screenshot before each. Afterwards Windows' use of the Q2 as speakers (its Audio Sink service) is turned
    off, and btrig guard keeps the default output where it was throughout."""
    laptop = next((e["adapter"] for e in btrig("info") if "adapter" in e), None)
    shots = pathlib.Path(shots or pathlib.Path(WIN_DIR) / "pair")
    shots.mkdir(parents=True, exist_ok=True)
    g = guard_start()
    try:
        p = subprocess.Popen([str(btrig_exe()), "pair", Q2_MAC], stdout=subprocess.PIPE, text=True, cwd=WIN_DIR)
        assert p.stdout is not None
        answered = False
        for line in p.stdout:
            if not line.startswith("{"):
                continue
            ev = json.loads(line)
            print(json.dumps(ev), flush=True)
            if ev.get("event") != "pairing_requested" or answered:
                continue
            linked = {m for m, d in q2_bluez().items() if d.get("connected")}
            if linked != {laptop}:
                print(json.dumps({"event": "not_answered", "why": f"devices linked to the Q2: {sorted(linked)}"}), flush=True)
                continue
            time.sleep(1.5)  # the dialog opens on demo's next pass
            q2_shot(shots / "1-dialog.png")
            q2_input("wheel", 1)
            time.sleep(0.5)
            q2_shot(shots / "2-second-row-lit.png")
            q2_input("tap", *OK_TAP)
            answered = True
        rc = p.wait()
        if rc == 0:
            btrig("service", Q2_MAC, A2DP_SINK, "off")
    finally:
        restored = [e for e in guard_stop(g) if e.get("event") == "default_restored"]
    paired = q2_bluez().get(laptop, {}).get("paired")
    print(json.dumps({"event": "result", "windows_exit": rc, "q2_paired": paired, "screens": str(shots),
                      "defaults_put_back": restored}), flush=True)
    return 0 if rc == 0 and paired else 1


def unpair():
    """Both sides forget each other: Windows (btrig unpair) and the Q2's BlueZ (Adapter1.RemoveDevice, the laptop's
    address alone)."""
    laptop = next(e["adapter"] for e in btrig("info") if "adapter" in e)
    btrig("unpair", Q2_MAC)
    path = "/org/bluez/hci0/dev_" + laptop.replace(":", "_")
    q2sh(f"dbus-send --system --print-reply --dest=org.bluez /org/bluez/hci0 org.bluez.Adapter1.RemoveDevice objpath:{path}")
    return 0


A2DP_SINK = "0000110b-0000-1000-8000-00805f9b34fb"
ROUTE = os.environ.get("BTRIG_ROUTE", "Steam Streaming Speakers")  # the inaudible endpoint btrig.exe plays into


# ---- record

def record(seconds, out, extra=()):
    """Receive from the Q2 and record `seconds` into out (a WAV), with out.json (the recording's facts, the Q2's
    settings and link before and after) and out.q2.log (the Q2's unified log over the recording)."""
    out = pathlib.Path(out).resolve()
    tmp = pathlib.Path(WIN_DIR) / "rec" / (out.stem + ".wav")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    side = {"seconds_asked": seconds, "q2_before": q2_settings(), "clock_offset_s": q2_clock_offset(), "events": []}
    p = subprocess.Popen([str(btrig_exe()), "receive", "--device", Q2_MAC, "--seconds", str(seconds),
                          "--out", win_path(tmp), *(extra or ["--route", ROUTE])], stdout=subprocess.PIPE, text=True,
                         cwd=WIN_DIR)
    assert p.stdout is not None
    codec_read = False
    for line in p.stdout:
        if not line.startswith("{"):
            continue
        ev = json.loads(line)
        if ev.get("event") != "tick":
            side["events"].append(ev)
            print(json.dumps(ev), flush=True)
        elif not codec_read and ev.get("seconds", 0) >= 2:
            side["link"] = q2_codec()  # the stream is up by now
            codec_read = True
    rc = p.wait()
    done = next((e for e in side["events"] if e.get("event") == "done"), None)
    side["q2_after"] = q2_settings()
    if rc or not done:
        side["error"] = f"btrig exited {rc}"
        out.with_suffix(".json").write_text(json.dumps(side, indent=1))
        raise RuntimeError(side["error"])
    if tmp.resolve() != out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(tmp.read_bytes())
        tmp.unlink()
    side.update({"wav": str(out), "start_utc": done["start_utc"], "first_packet_utc": done["first_packet_utc"],
                 "rate": done["rate"], "frames": done["frames"], "seconds": done["frames"] / done["rate"],
                 "gap_frames": done["gap_frames"], "capture": done["mode"]})
    t0 = parse_utc(done["start_utc"])
    log = q2_log(t0 - 2, t0 + side["seconds"] + 2, side["clock_offset_s"])
    out.with_suffix(".q2.log").write_text("\n".join(log) + "\n")
    side["q2_log"] = str(out.with_suffix(".q2.log"))
    out.with_suffix(".json").write_text(json.dumps(side, indent=1))
    return side




# ---- natural song ends

def ends(count, before_s, timeout_s=None):
    """Let `count` songs end by themselves: each time, seek the playing song to before_s seconds from its end, wait
    for the next song to start, and keep the unified log's player lines over that time. A seek, unlike Next (which
    never fades), leaves the change to the player. Returns one dict per change."""
    import coda_rc_client as cr
    s = coda()
    st = s.get_state()
    if st["now_playing"]["play_state"] != 1:  # resume; from a stop, the queue's own song again (Resume is refused)
        try:
            s.transport("play")
        except cr.RemoteError:
            s.jump(st["queue"]["index"], st["now_playing"]["track_id"])
        time.sleep(3)
    out = []
    for _ in range(count):
        st = s.get_state()
        np_ = st["now_playing"]
        dur = np_["duration_ms"] / 1000
        t0 = time.time()
        s.transport("seek", max(0.0, dur - before_s))
        deadline = t0 + (timeout_s or before_s + 30)
        nxt = None
        while time.time() < deadline:
            time.sleep(1)
            cur = s.get_state()["now_playing"]
            if cur["track_id"] != np_["track_id"]:
                nxt = cur
                break
        time.sleep(5)  # the fade and the next song's first seconds, then the log
        off = q2_clock_offset()
        lines = q2_log(t0 - 1, time.time(), off)
        player = [l for l in lines if " player " in l or "smart crossfade" in l or "xfade" in l]
        out.append({"from": f'{np_["artist"]} - {np_["title"]}', "to": nxt and f'{nxt["artist"]} - {nxt["title"]}',
                    "seek_utc": dt.datetime.fromtimestamp(t0, dt.timezone.utc).isoformat(), "log": player})
        print(json.dumps(out[-1], indent=1, ensure_ascii=False), flush=True)
        if nxt is None:
            break
    return out


# ---- the command line

def main(argv):
    ap = argparse.ArgumentParser(prog="bt_rig.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("status")
    sub.add_parser("pair")
    sub.add_parser("unpair")
    r = sub.add_parser("record")
    r.add_argument("--seconds", type=float, required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--endpoint", action="store_true", help="record the default endpoint's loopback (audible)")
    en = sub.add_parser("ends", help="let songs end by themselves (seek near each end) and show the player's log")
    en.add_argument("count", type=int)
    en.add_argument("--before", type=float, default=50.0, help="seconds before each end to seek to (default 50)")
    lg = sub.add_parser("log")
    lg.add_argument("--from", dest="t_from", required=True, help="ISO time (UTC)")
    lg.add_argument("--to", dest="t_to", required=True)
    cal = sub.add_parser("calibrate", help="known changes through a codec, analysed: the analysis's own check")
    cal.add_argument("outdir")
    cal.add_argument("--codec", choices=["sbc", "aac", "none"], default="sbc")
    an = sub.add_parser("analyse")
    an.add_argument("wav")
    an.add_argument("--ref", action="append", default=[])
    an.add_argument("--at", action="append", type=float, default=[])
    an.add_argument("--rows", action="store_true")
    pl = sub.add_parser("play", help="drive the Q2 through coda-rc")
    pl.add_argument("action", choices=["state", "queue", "browse", "search", "start", "resume", "pause", "next",
                                       "previous", "seek", "seek-end", "volume", "shuffle", "repeat", "jump", "pair"])
    pl.add_argument("args", nargs="*")
    a = ap.parse_args(argv)

    if a.cmd == "build":
        print(build())
    elif a.cmd == "status":
        print(json.dumps({"laptop": btrig("info"), "q2": q2_settings(), "q2_link": q2_codec()}, indent=1))
    elif a.cmd == "pair":
        return pair()
    elif a.cmd == "unpair":
        return unpair()
    elif a.cmd == "record":
        side = record(a.seconds, a.out, ["--endpoint"] if a.endpoint else [])
        print(json.dumps({k: side[k] for k in ("wav", "start_utc", "seconds", "gap_frames", "capture", "q2_log")}))
    elif a.cmd == "ends":
        ends(a.count, a.before)
    elif a.cmd == "log":
        off = q2_clock_offset()
        print("\n".join(q2_log(parse_utc(a.t_from), parse_utc(a.t_to), off)))
    elif a.cmd == "analyse":
        import bt_rig_analyse
        json.dump(bt_rig_analyse.analyse(a.wav, a.ref, a.at, a.rows), sys.stdout, indent=1)
        print()
    elif a.cmd == "calibrate":
        import bt_rig_analyse
        rows = bt_rig_analyse.calibrate(a.outdir, a.codec)
        for case, overlap, gap, ok in rows:
            print(f"{case:6} overlap {overlap} s  gap {gap} s  {'pass' if ok else 'FAIL'}")
        return 0 if all(r[3] for r in rows) else 1
    else:  # play
        return play(a.action, a.args)
    return 0


def play(action, args):
    if action == "pair":
        s = coda(pair=True)
        print(json.dumps({**s.info, "paired": True}))
        return 0
    s = coda()
    if action == "state":
        out = s.get_state()
    elif action == "queue":
        out = s.queue(0, int(args[0]) if args else 50)
    elif action == "browse":
        out = s.browse(args[0] if args else "root")
    elif action == "search":
        out = s.search(" ".join(args))
    elif action == "start":  # a node (browse's ids), optionally from a song id
        s.play(args[0], None, args[1] if len(args) > 1 else None)
        out = {"ok": True}
    elif action in ("resume", "pause", "next", "previous"):
        s.transport({"resume": "play"}.get(action, action))
        out = {"ok": True}
    elif action == "seek":
        s.transport("seek", float(args[0]))
        out = {"ok": True}
    elif action == "seek-end":  # to N seconds before the playing song's end
        dur = s.get_state()["now_playing"]["duration_ms"] / 1000
        s.transport("seek", max(0.0, dur - float(args[0])))
        out = {"ok": True, "to_s": max(0.0, dur - float(args[0]))}
    elif action == "volume":
        s.transport("volume", float(args[0]))
        out = {"ok": True}
    elif action == "shuffle":
        s.set_mode(shuffle=("off", "on").index(args[0]))
        out = {"ok": True}
    elif action == "repeat":
        s.set_mode(repeat=("off", "one", "all").index(args[0]))
        out = {"ok": True}
    else:  # jump
        s.jump(int(args[0]), args[1])
        out = {"ok": True}
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
