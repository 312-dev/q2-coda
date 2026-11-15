"""coda-rc's primitives in pure Python (docs/remote-protocol.md#cryptography), so the client needs no packages.

X25519 (RFC 7748), Elligator 2 as Monocypher 4's crypto_elligator_map computes it, ChaCha20-Poly1305 as RFC 8439
specifies it, and the protocol's key schedule over HMAC-SHA-512. The daemon uses Monocypher for each of these;
test/unit/test_coda_rc.py checks every function here against it, byte for byte. Slow but small: a session seals a
few hundred frames, and a pairing does three scalar multiplications."""
import hashlib
import hmac
import struct

P = 2**255 - 19
A = 486662
INFO_PAKE, INFO_PAIR, INFO_AUTH, INFO_SESSION = b"coda-rc pake v1", b"coda-rc pair v1", b"coda-rc auth v1", b"coda-rc session v1"


def _clamp(k):
    k = bytearray(k)
    k[0] &= 248
    k[31] = (k[31] & 127) | 64
    return int.from_bytes(k, "little")


def x25519(scalar, point):
    """RFC 7748's ladder: scalar (32 bytes, clamped here) times the u coordinate point."""
    k, u = _clamp(scalar), int.from_bytes(point, "little") & (2**255 - 1)
    x1, x2, z2, x3, z3, swap = u, 1, 0, u, 1, 0
    for t in range(254, -1, -1):
        bit = (k >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b = (x2 + z2) % P, (x2 - z2) % P
        aa, bb = a * a % P, b * b % P
        e, c, d = (aa - bb) % P, (x3 + z3) % P, (x3 - z3) % P
        da, cb = d * a % P, c * b % P
        x3, z3 = (da + cb) ** 2 % P, u * (da - cb) ** 2 % P
        x2, z2 = aa * bb % P, e * (aa + 121665 * e) % P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, P - 2, P) % P).to_bytes(32, "little")


def elligator_map(hidden):
    """Monocypher's crypto_elligator_map: a 254-bit representative to a curve point (non-square 2)."""
    r = int.from_bytes(hidden, "little") & (2**254 - 1)
    w = -A * pow(1 + 2 * r * r, P - 2, P) % P
    e = pow((w**3 + A * w * w + w) % P, (P - 1) // 2, P)
    u = w if e != P - 1 else (-w - A) % P
    return u.to_bytes(32, "little")


def _rotl(v, n):
    return ((v << n) & 0xFFFFFFFF) | (v >> (32 - n))


def _block(key, counter, nonce):
    s = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574, *struct.unpack("<8I", key), counter, *struct.unpack("<3I", nonce)]
    x = s[:]
    for _ in range(10):
        for a, b, c, d in ((0, 4, 8, 12), (1, 5, 9, 13), (2, 6, 10, 14), (3, 7, 11, 15),
                           (0, 5, 10, 15), (1, 6, 11, 12), (2, 7, 8, 13), (3, 4, 9, 14)):
            x[a] = (x[a] + x[b]) & 0xFFFFFFFF; x[d] = _rotl(x[d] ^ x[a], 16)
            x[c] = (x[c] + x[d]) & 0xFFFFFFFF; x[b] = _rotl(x[b] ^ x[c], 12)
            x[a] = (x[a] + x[b]) & 0xFFFFFFFF; x[d] = _rotl(x[d] ^ x[a], 8)
            x[c] = (x[c] + x[d]) & 0xFFFFFFFF; x[b] = _rotl(x[b] ^ x[c], 7)
    return struct.pack("<16I", *((x[i] + s[i]) & 0xFFFFFFFF for i in range(16)))


def chacha20(key, counter, nonce, data):
    out = bytearray()
    for i in range(0, len(data), 64):
        ks = _block(key, counter + i // 64, nonce)
        out += bytes(a ^ b for a, b in zip(data[i:i + 64], ks))
    return bytes(out)


def poly1305(key, msg):
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s, acc, p = int.from_bytes(key[16:], "little"), 0, 2**130 - 5
    for i in range(0, len(msg), 16):
        acc = (acc + int.from_bytes(msg[i:i + 16] + b"\1", "little")) * r % p
    return ((acc + s) & (2**128 - 1)).to_bytes(16, "little")


def _pad(n):
    return bytes(-n % 16)


def _mac_data(ad, ct):
    return ad + _pad(len(ad)) + ct + _pad(len(ct)) + struct.pack("<QQ", len(ad), len(ct))


def seal(key, nonce, ad, plain):
    """RFC 8439 ChaCha20-Poly1305: ciphertext then its 16-byte tag."""
    ct = chacha20(key, 1, nonce, plain)
    return ct + poly1305(chacha20(key, 0, nonce, bytes(32)), _mac_data(ad, ct))


def open_(key, nonce, ad, sealed):
    """seal's inverse; None when the tag does not match."""
    if len(sealed) < 16:
        return None
    ct, tag = sealed[:-16], sealed[-16:]
    if not hmac.compare_digest(tag, poly1305(chacha20(key, 0, nonce, bytes(32)), _mac_data(ad, ct))):
        return None
    return chacha20(key, 1, nonce, ct)


def nonce(counter):
    """A frame's nonce: four zero bytes then its direction's 64-bit little-endian frame counter."""
    return bytes(4) + struct.pack("<Q", counter)


def mac(key, *parts):
    return hmac.new(key, b"".join(parts), hashlib.sha512).digest()


def pake_generator(code, sid):
    """The pairing's base point, from the code shown on the Q2 and the session's two nonces."""
    return elligator_map(hashlib.sha512(INFO_PAKE + code.encode() + sid).digest()[:32])


def pair_prk(shared, sid, client_point, device_point):
    """The pairing's key, from its X25519 result and the transcript."""
    return mac(shared, INFO_PAIR, sid, client_point, device_point)


def client_confirm(prk):
    return mac(prk, b"client confirm")[:32]


def device_confirm(prk, peer_id):
    return mac(prk, b"device confirm", peer_id)[:32]


def long_term_key(prk):
    return mac(prk, b"long-term key")[:32]


def auth_proofs(ltk, sid):
    """(client proof, device proof) of a paired key for this session."""
    return mac(ltk, INFO_AUTH, b"client", sid)[:32], mac(ltk, INFO_AUTH, b"device", sid)[:32]


def session_keys(ltk, sid):
    """(client-to-device key, device-to-client key)."""
    k = mac(ltk, INFO_SESSION, sid)
    return k[:32], k[32:]
