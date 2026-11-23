#!/usr/bin/env python3
"""A coda-rc client over TCP (docs/remote-protocol.md): pair with a Q2, browse, search, play, and watch Now Playing.

usage: coda_rc_client.py [--host H] [--port P] [--keys DIR] COMMAND ...
  pair [--code CODE] [--name NAME]   pair (the Q2 shows a 6-digit code; type it, or pass --code)
  state                              Now Playing, position and queue once
  watch [SECONDS]                    State pushes as they come
  browse [NODE] [--sort KEY] [--offset N] [--limit N]
  search TEXT
  play NODE [--sort KEY] [--start SONG_ID] [--shuffle] [--resume]
  queue [--offset N] [--limit N]
  jump INDEX SONG_ID                 play the queue's song at INDEX (its id from queue)
  shuffle off|on | repeat off|one|all
  rate HEARTS SONG_ID                0..5 hearts for the playing song (5 is Favorites)
  radio [SEED_ID|stop]               a radio of the playing song, a "song:" id or an "artist:" node; or stop it
  key play|pause|toggle|next|previous|volume-up|volume-down
  seek SECONDS | volume 0..100
  cover ART_ID OUT [--px N]
  remote                             the Q2's iroh endpoint id and relay, for reaching it from another network
  frame hello                        a Hello frame's hex, to write by hand to the BLE control characteristic

The key from a pairing is kept in --keys (default ~/.config/coda-rc), one file per Q2, mode 0600. Pure Python:
tools/coda_rc_crypto.py has the cryptography. test/coda_rc_e2e.py drives an emulated Q2 with this module."""
import argparse
import json
import os
import pathlib
import secrets
import socket
import struct
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import coda_rc_crypto as cc  # noqa: E402

PORT = 7720
VERSION = 1
SEALED = 1
T = dict(HELLO=0x01, HELLO_RESP=0x02, PAIR_START=0x03, PAIR_CHALLENGE=0x04, PAIR_FINISH=0x05, PAIR_RESULT=0x06,
         AUTH=0x07, AUTH_RESP=0x08, ERROR=0x0F, SUBSCRIBE=0x10, STATE=0x11, GET_STATE=0x12, TRANSPORT=0x20, ACK=0x21,
         SET_MODE=0x22, RATE=0x24, BROWSE=0x30, BROWSE_RESP=0x31, SEARCH=0x32, SEARCH_RESP=0x33, PLAY=0x40,
         GET_QUEUE=0x42, QUEUE_RESP=0x43, PLAY_QUEUE_ITEM=0x44, START_RADIO=0x46, GET_COVER=0x50, COVER=0x51, GET_REMOTE=0x60,
         REMOTE_INFO=0x61, REGISTER_REMOTE=0x62, PING=0x7E, PONG=0x7F)
ERRORS = {1: "UNSUPPORTED", 2: "UNAUTHORIZED", 3: "BAD_REQUEST", 4: "NOT_FOUND", 5: "BUSY", 6: "PAIRING_FAILED",
          7: "PAIRING_LOCKED", 8: "TOO_LARGE", 9: "INTERNAL", 10: "VERSION", 11: "UNAVAILABLE", 12: "PAIRING_CANCELLED",
          13: "PAIRING_FULL"}
SHUFFLES, REPEATS = ("off", "on"), ("off", "one", "all")
COMMANDS = {"play": 1, "pause": 2, "toggle": 3, "next": 4, "previous": 5, "seek": 6, "volume-up": 7, "volume-down": 8,
            "volume": 9}


# ---- protobuf: the subset coda-rc uses (varints and length-delimited fields)

def varint(v):
    out = bytearray()
    while True:
        b = v & 127
        v >>= 7
        out.append(b | (128 if v else 0))
        if not v:
            return bytes(out)


def encode(fields):
    """[(field, value)] -> bytes; int and bool are varints, str and bytes length-delimited."""
    out = bytearray()
    for f, v in fields:
        if v is None:
            continue
        if isinstance(v, (bool, int)):
            out += varint(f << 3) + varint(int(v))
        else:
            v = v.encode() if isinstance(v, str) else v
            out += varint(f << 3 | 2) + varint(len(v)) + v
    return bytes(out)


def decode(b):
    """bytes -> {field: [values]} (ints and bytes); raises ValueError when malformed."""
    out, i = {}, 0

    def rv():
        nonlocal i
        v = shift = 0
        while True:
            if i >= len(b) or shift > 63:
                raise ValueError("varint")
            c = b[i]
            i += 1
            v |= (c & 127) << shift
            shift += 7
            if not c & 128:
                return v
    while i < len(b):
        key = rv()
        f, wire = key >> 3, key & 7
        if wire == 0:
            v = rv()
        elif wire == 2:
            n = rv()
            if i + n > len(b):
                raise ValueError("length")
            v, i = bytes(b[i:i + n]), i + n
        elif wire in (1, 5):
            n = 8 if wire == 1 else 4
            v, i = bytes(b[i:i + n]), i + n
        else:
            raise ValueError("wire type")
        out.setdefault(f, []).append(v)
    return out


def one(m, f, default=None):
    return m.get(f, [default])[0]


def text(m, f):
    v = one(m, f, b"")
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else ""


def item(raw):
    m = decode(raw)
    it = {"id": text(m, 1), "title": text(m, 2)}
    for k, f in (("subtitle", 3), ("art", 4), ("group", 8)):
        if text(m, f):
            it[k] = text(m, f)
    for k, f in (("icon", 5), ("duration_ms", 9), ("number", 10)):
        if one(m, f):
            it[k] = one(m, f)
    it["browsable"], it["playable"] = bool(one(m, 6, 0)), bool(one(m, 7, 0))
    return it


def state(raw):
    m, out = decode(raw), {}
    if 1 in m:
        n = decode(m[1][0])
        out["now_playing"] = {"track_id": text(n, 1), "title": text(n, 2), "artist": text(n, 3), "album": text(n, 4),
                              "duration_ms": one(n, 5, 0), "art": text(n, 6), "play_state": one(n, 7, 0),
                              "shuffle": one(n, 8, 0), "repeat": one(n, 9, 0), "volume": one(n, 10, 0),
                              "hearts": one(n, 11, 0), "rateable": bool(one(n, 12, 0)),
                              "buffering": bool(one(n, 13, 0)), "buffered_bytes": one(n, 14, 0),
                              "total_bytes": one(n, 15, 0), "buffered_ms": one(n, 16, 0),
                              "radio": bool(one(n, 17, 0)), "notice": text(n, 18), "notice_id": one(n, 19, 0),
                              "album_id": text(n, 20), "artist_id": text(n, 21)}
    if 2 in m:
        p = decode(m[2][0])
        out["position"] = {"position_ms": one(p, 1, 0), "at_ms": one(p, 2, 0)}
    if 3 in m:
        q = decode(m[3][0])
        out["queue"] = {"version": one(q, 1, 0), "index": one(q, 2, 0), "total": one(q, 3, 0)}
    return out


class RemoteError(Exception):
    def __init__(self, code, message):
        super().__init__(f"{ERRORS.get(code, code)}: {message}")
        self.code, self.name = code, ERRORS.get(code, str(code))


class Session:
    """One coda-rc session over a TCP socket (or any object with sendall and recv)."""

    def __init__(self, sock, name="coda_rc_client.py"):
        self.sock, self.name, self.buf = sock, name, b""
        self.kc = self.kd = None
        self.tx = self.rx = 0
        self.req = 0
        self.pushes = []
        self.sid = None
        self.info = {}  # connect's: the Hello answer of the session it returns

    # -- frames

    def send(self, type_, payload=b"", req_id=None):
        if req_id is None:
            self.req = self.req % 65535 + 1
            req_id = self.req
        sealed = self.kc is not None
        body = len(payload) + 4 + (16 if sealed else 0)
        hdr = struct.pack(">HBBH", body, type_, SEALED if sealed else 0, req_id)
        if sealed:
            payload = cc.seal(self.kc, cc.nonce(self.tx), hdr, payload)
            self.tx += 1
        self.sock.sendall(hdr + payload)
        return req_id

    def frame(self, timeout=10):
        """The next frame: (type, req_id, payload), opened when the session is sealed."""
        self.sock.settimeout(timeout)
        while True:
            if len(self.buf) >= 2:
                n = struct.unpack(">H", self.buf[:2])[0]
                if len(self.buf) >= 2 + n:
                    raw, self.buf = self.buf[:2 + n], self.buf[2 + n:]
                    _, type_, flags, req = struct.unpack(">HBBH", raw[:6])
                    payload = raw[6:]
                    if flags & SEALED:
                        if self.kd is None:
                            raise ValueError("a sealed frame before Auth")
                        payload = cc.open_(self.kd, cc.nonce(self.rx), raw[:6], payload)
                        self.rx += 1
                        if payload is None:
                            raise ValueError("a frame that does not open")
                    elif self.kd is not None:
                        raise ValueError("a plain frame after Auth")
                    return type_, req, payload
            got = self.sock.recv(65536)
            if not got:
                raise ConnectionError("closed")
            self.buf += got

    def call(self, type_, payload=b"", want=None, timeout=10):
        """Send a request and return its answer's payload; pushes that come first are kept in self.pushes."""
        req = self.send(type_, payload)
        while True:
            t, r, p = self.frame(timeout)
            if r == 0:
                if t == T["ERROR"]:
                    m = decode(p)
                    raise RemoteError(one(m, 1, 0), text(m, 2))
                self.pushes.append((t, p))
                continue
            if r != req:
                continue
            if t == T["ERROR"]:
                m = decode(p)
                raise RemoteError(one(m, 1, 0), text(m, 2))
            if want is not None and t != want:
                raise ValueError(f"answer type {t:#x}, not {want:#x}")
            return p

    # -- the handshake

    def hello(self, peer_id=None):
        nonce = secrets.token_bytes(16)
        r = decode(self.call(T["HELLO"], encode([(1, VERSION), (2, self.name), (3, nonce), (4, peer_id)]), T["HELLO_RESP"]))
        self.sid = nonce + one(r, 3)
        return {"version": one(r, 1, 0), "paired": bool(one(r, 4, 0)), "caps": one(r, 5, 0), "model": text(r, 6),
                "device_id": one(r, 7, b"").hex()}

    def pair_start(self):
        r = decode(self.call(T["PAIR_START"], encode([(1, self.name)]), T["PAIR_CHALLENGE"], timeout=30))
        return one(r, 1)

    def pair_finish(self, code, device_point):
        a = secrets.token_bytes(32)
        g = cc.pake_generator(code, self.sid)
        client_point = cc.x25519(a, g)
        shared = cc.x25519(a, device_point)
        if shared == bytes(32):
            raise ValueError("low-order device point")
        prk = cc.pair_prk(shared, self.sid, client_point, device_point)
        r = decode(self.call(T["PAIR_FINISH"], encode([(1, client_point), (2, cc.client_confirm(prk))]), T["PAIR_RESULT"]))
        peer_id, confirm = one(r, 1), one(r, 2)
        if confirm != cc.device_confirm(prk, peer_id):
            raise ValueError("the Q2's confirmation does not match: not the Q2 that showed the code")
        return peer_id, cc.long_term_key(prk)

    def auth(self, ltk):
        proof_c, proof_d = cc.auth_proofs(ltk, self.sid)
        r = decode(self.call(T["AUTH"], encode([(1, proof_c)]), T["AUTH_RESP"]))
        if one(r, 1) != proof_d:
            raise ValueError("the Q2's proof does not match")
        self.kc, self.kd = cc.session_keys(ltk, self.sid)
        self.tx = self.rx = 0

    # -- requests

    def subscribe(self, position=True):
        self.call(T["SUBSCRIBE"], encode([(1, position)]), T["ACK"])

    def get_state(self):
        return state(self.call(T["GET_STATE"], b"", T["STATE"]))

    def browse(self, node="root", sort=None, offset=0, limit=50):
        m = decode(self.call(T["BROWSE"], encode([(1, node), (2, sort), (3, offset), (4, limit)]), T["BROWSE_RESP"]))
        return {"node": text(m, 1), "title": text(m, 2), "total": one(m, 3, 0), "offset": one(m, 4, 0),
                "items": [item(x) for x in m.get(5, [])], "sort": text(m, 6),
                "sorts": [{"key": text(decode(x), 1), "label": text(decode(x), 2)} for x in m.get(7, [])],
                "actions": [{1: "play", 2: "shuffle", 3: "resume"}.get(a, a) for a in m.get(8, [])]}

    def search(self, query, limit=20):
        m = decode(self.call(T["SEARCH"], encode([(1, query), (2, limit)]), T["SEARCH_RESP"]))
        return [{"title": text(g, 1), "total": one(g, 3, 0), "items": [item(x) for x in g.get(2, [])]}
                for g in map(decode, m.get(1, []))]

    def play(self, node, sort=None, start=None, shuffle=False, resume=False):
        self.call(T["PLAY"], encode([(1, node), (2, sort), (3, start), (4, shuffle), (5, resume or None)]), T["ACK"])

    def set_mode(self, shuffle=None, repeat=None):
        """SetMode: each of shuffle and repeat set when given (Shuffle and Repeat enum numbers)."""
        fields = [(1, True), (2, shuffle)] if shuffle is not None else []
        fields += [(3, True), (4, repeat)] if repeat is not None else []
        self.call(T["SET_MODE"], encode(fields), T["ACK"])

    def rate(self, track_id, hearts):
        self.call(T["RATE"], encode([(1, track_id), (2, hearts)]), T["ACK"])

    def jump(self, index, track_id):
        self.call(T["PLAY_QUEUE_ITEM"], encode([(1, index), (2, track_id)]), T["ACK"])

    def start_radio(self, seed_id="", stop=False):
        """StartRadio (CAP_RADIO): seed_id "" is the playing song, else a "song:" id or an "artist:" node."""
        self.call(T["START_RADIO"], encode([(1, seed_id or None), (2, stop or None)]), T["ACK"])

    def remote(self):
        """GetRemote (CAP_REMOTE): the Q2's q2-iroh endpoint id and home relay (docs/remote.md#car-remote-across-networks)."""
        m = decode(self.call(T["GET_REMOTE"], b"", T["REMOTE_INFO"]))
        return {"endpoint_id": text(m, 1), "relay_url": text(m, 2)}

    def queue(self, offset=0, limit=50):
        m = decode(self.call(T["GET_QUEUE"], encode([(1, offset), (2, limit)]), T["QUEUE_RESP"]))
        return {"version": one(m, 1, 0), "index": one(m, 2, 0), "total": one(m, 3, 0), "offset": one(m, 4, 0),
                "items": [item(x) for x in m.get(5, [])]}

    def transport(self, cmd, value=None):
        fields = [(1, COMMANDS[cmd])]
        if cmd == "seek":
            fields.append((2, int(value * 1000)))
        if cmd == "volume":
            fields.append((3, int(value)))
        self.call(T["TRANSPORT"], encode(fields), T["ACK"])

    def cover(self, art, px=160):
        data, etag, total = b"", None, None
        while total is None or len(data) < total:
            m = decode(self.call(T["GET_COVER"], encode([(1, art), (2, len(data)), (3, 4096), (4, px)]), T["COVER"]))
            if etag is not None and one(m, 5) != etag:
                data, etag, total = b"", None, None  # changed mid-fetch: again from the start
                continue
            etag, total = one(m, 5), one(m, 1, 0)
            data += one(m, 3, b"")
        return data, text(m, 4)

    def wait_push(self, timeout=10):
        """The next State push's parts."""
        if self.pushes:
            t, p = self.pushes.pop(0)
            return state(p) if t == T["STATE"] else {"type": t}
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError("no push")
            t, r, p = self.frame(left)
            if r == 0 and t == T["STATE"]:
                return state(p)
            if r == 0 and t == T["ERROR"]:
                m = decode(p)
                raise RemoteError(one(m, 1, 0), text(m, 2))


# ---- keys on this machine

def key_dir(arg):
    return pathlib.Path(arg or os.environ.get("CODA_RC_KEYS") or pathlib.Path.home() / ".config/coda-rc")


def load_key(d, device_id):
    try:
        k = json.loads((d / f"{device_id}.json").read_text())
        return bytes.fromhex(k["peer_id"]), bytes.fromhex(k["ltk"])
    except (OSError, KeyError, ValueError):
        return None


def save_key(d, device_id, peer_id, ltk):
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = d / f"{device_id}.json"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"peer_id": peer_id.hex(), "ltk": ltk.hex()}, f)


def connect(host, port, keys, name="coda_rc_client.py", code=None, pair=False, ask=input):
    """An authenticated Session, pairing first when this machine holds no key for the Q2 (or pair=True)."""
    s = Session(socket.create_connection((host, port), timeout=10), name)
    info = s.hello()
    known = load_key(keys, info["device_id"])
    if known and not pair:
        s.sock.close()
        s = Session(socket.create_connection((host, port), timeout=10), name)
        info = s.hello(known[0])
        if info["paired"]:
            s.auth(known[1])
            s.info = info
            return s
    point = s.pair_start()
    code = code or ask("Code shown on the Q2: ").strip().replace(" ", "")
    peer_id, ltk = s.pair_finish(code, point)
    save_key(keys, info["device_id"], peer_id, ltk)
    s.auth(ltk)
    s.info = info
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=os.environ.get("Q2_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--keys")
    ap.add_argument("--name", default="coda_rc_client.py")
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--code")
    ap.add_argument("--sort")
    ap.add_argument("--start")
    ap.add_argument("--shuffle", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--px", type=int, default=160)
    a = ap.parse_args(argv)
    if a.cmd == "frame":
        p = encode([(1, VERSION), (2, a.name), (3, secrets.token_bytes(16))])
        print((struct.pack(">HBBH", len(p) + 4, T["HELLO"], 0, 1) + p).hex())
        return 0
    s = connect(a.host, a.port, key_dir(a.keys), a.name, a.code, pair=a.cmd == "pair")
    out = None
    if a.cmd == "pair":
        out = {**s.info, "paired": True}  # info is the first Hello's, from before the pairing
    elif a.cmd == "state":
        out = s.get_state()
    elif a.cmd == "watch":
        s.subscribe(True)
        end = time.monotonic() + (float(a.args[0]) if a.args else 1e9)
        while time.monotonic() < end:
            try:
                print(json.dumps(s.wait_push(min(10, end - time.monotonic()))), flush=True)
            except TimeoutError:
                pass
        return 0
    elif a.cmd == "browse":
        out = s.browse(a.args[0] if a.args else "root", a.sort, a.offset, a.limit)
    elif a.cmd == "search":
        out = s.search(" ".join(a.args))
    elif a.cmd == "play":
        s.play(a.args[0], a.sort, a.start, a.shuffle, a.resume)
        out = {"ok": True}
    elif a.cmd == "jump":
        s.jump(int(a.args[0]), a.args[1])
        out = {"ok": True}
    elif a.cmd in ("shuffle", "repeat"):
        names = SHUFFLES if a.cmd == "shuffle" else REPEATS
        if a.args[0] not in names:
            ap.error(f"{a.cmd}: one of {', '.join(names)}")
        s.set_mode(**{a.cmd: names.index(a.args[0])})
        out = {"ok": True}
    elif a.cmd == "rate":
        s.rate(a.args[1], int(a.args[0]))
        out = {"ok": True}
    elif a.cmd == "radio":
        seed = a.args[0] if a.args else ""
        s.start_radio(stop=True) if seed == "stop" else s.start_radio(seed)
        out = {"ok": True}
    elif a.cmd == "queue":
        out = s.queue(a.offset, a.limit)
    elif a.cmd == "key":
        s.transport(a.args[0])
        out = {"ok": True}
    elif a.cmd in ("seek", "volume"):
        s.transport(a.cmd, float(a.args[0]))
        out = {"ok": True}
    elif a.cmd == "cover":
        data, mime = s.cover(a.args[0], a.px)
        pathlib.Path(a.args[1]).write_bytes(data)
        out = {"bytes": len(data), "mime": mime}
    elif a.cmd == "remote":
        out = s.remote()
    else:
        ap.error(f"unknown command {a.cmd}")
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RemoteError as e:
        sys.exit(f"coda-rc: {e}")
