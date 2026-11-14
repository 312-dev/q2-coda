"""patch/release_sig.c: SHA-256 and SHA-512 of a file read a chunk at a time, the messages tools/signing.py signs,
and the release key's Ed25519 checks of update.tar and sources.list (docs/internals.md#release-signatures). The
library is built with a key made for it (host.signing_key), so these sign with its private half; a second key
stands in for anyone else's."""
import ctypes as C
import hashlib
import subprocess

import pytest

import host

signing = host.tools_signing
SIG_OK, SIG_BAD, SIG_IO, SIG_CANCELLED = range(4)
PATH = b'/mnt/mmc/update.tar.part'


class Files:
    """fopen and fread over a dict, fread handing back at most `most` bytes a call."""
    def __init__(self, fw, files, most=None):
        self.files, self.at, self.names, self.reads = files, {}, {}, 0
        def fopen(path, mode, *_):
            name = C.string_at(path)
            if name not in self.files:
                return 0
            f = len(self.names) + 1
            self.names[f], self.at[f] = name, 0
            return f
        def fread(buf, size, n, f, *_):
            self.reads += 1
            want = min(size * n, most or size * n)
            piece = self.files[self.names[f]][self.at[f]:self.at[f] + want]
            C.memmove(buf, piece, len(piece))
            self.at[f] += len(piece)
            return len(piece) // size
        fw.mock('fw_fopen', fopen)
        fw.mock('fw_fread', fread)


def digest(fw, data, sha512, most=None, cancel=None):
    files = Files(fw, {PATH: data}, most)
    out = (C.c_ubyte * 64)()
    flag = C.c_int(cancel or 0)
    r = fw.fn('sig_hash_file', C.c_int, C.c_char_p, C.c_int, C.c_void_p, C.c_void_p)(PATH, sha512, out, C.byref(flag))
    return r, bytes(out)[:64 if sha512 else 32], files


@pytest.mark.parametrize('n', [0, 1, 55, 56, 63, 64, 65, 119, 120, 1000, (64 << 10) * 3 + 7])
def test_sha256_of_a_file_is_hashlibs(fw, n):
    data = bytes((i * 7 + 3) & 255 for i in range(n))
    assert digest(fw, data, 0)[:2] == (SIG_OK, hashlib.sha256(data).digest())


@pytest.mark.parametrize('most', [None, 1, 63, 65, 4096])
def test_any_split_of_the_reads_hashes_the_same(fw, most):
    data = bytes(range(256)) * 41
    r, d, _ = digest(fw, data, 0, most)
    assert (r, d) == (SIG_OK, hashlib.sha256(data).digest())
    r, d, _ = digest(fw, data, 1, most)
    assert (r, d) == (SIG_OK, hashlib.sha512(data).digest())


def test_a_large_file_is_read_in_64_kb_chunks(fw):
    data = b'x' * ((64 << 10) * 4 + 1)
    r, d, files = digest(fw, data, 1)
    assert (r, d) == (SIG_OK, hashlib.sha512(data).digest()) and files.reads == 6  # five pieces and the end


def test_a_missing_file_is_an_io_failure(fw):
    Files(fw, {})
    out = (C.c_ubyte * 64)()
    assert fw.fn('sig_hash_file', C.c_int, C.c_char_p, C.c_int, C.c_void_p, C.c_void_p)(PATH, 1, out, None) == SIG_IO


def test_a_read_error_is_an_io_failure(fw):
    fw.mock('fw_ferror', lambda *_: 1)
    assert digest(fw, b'abc', 1)[0] == SIG_IO


def test_a_cancel_stops_the_hash(fw):
    assert digest(fw, b'abc' * 100000, 1, cancel=1)[0] == SIG_CANCELLED


def message(fw, kind, tag, d):
    out = C.create_string_buffer(256)
    n = fw.fn('sig_message', C.c_uint, C.c_void_p, C.c_uint, C.c_char_p, C.c_char_p, C.c_char_p)(out, 256, kind, tag, d)
    return out.raw[:n]


def test_the_messages_are_tools_signings(fw, tmp_path):
    data = b'the update'
    (tmp_path/'u').write_bytes(data)
    assert message(fw, b'update', b'1.8.3', hashlib.sha512(data).digest()) == signing.update_message('1.8.3', tmp_path/'u')
    assert message(fw, b'sources', None, hashlib.sha512(b'list').digest()) == signing.list_message(b'list')


def test_a_message_that_doesnt_fit_is_none(fw):
    out = C.create_string_buffer(64)
    fn = fw.fn('sig_message', C.c_uint, C.c_void_p, C.c_uint, C.c_char_p, C.c_char_p, C.c_char_p)
    assert fn(out, 64, b'update', b'1.0', bytes(64)) == 0


def other_key(tmp_path):
    pem = tmp_path/'other.pem'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'ed25519', '-out', str(pem)], check=True, capture_output=True)
    return pem.read_text()


TAR = bytes(range(256)) * 300


def update(fw, data, tag, sig):
    Files(fw, {PATH: data})
    return fw.fn('sig_update', C.c_int, C.c_char_p, C.c_char_p, C.c_char_p, C.c_void_p)(PATH, tag, sig, None)


@pytest.fixture
def signed(signing_pem, tmp_path):
    (tmp_path/'u').write_bytes(TAR)
    return signing.sign(signing.update_message('1.8.3', tmp_path/'u'), signing_pem)


def test_a_signed_update_passes(fw, signed):
    assert update(fw, TAR, b'1.8.3', signed) == SIG_OK


def test_a_flipped_byte_in_the_file_fails(fw, signed):
    for at in (0, len(TAR) // 2, len(TAR) - 1):
        bad = bytearray(TAR)
        bad[at] ^= 1
        assert update(fw, bytes(bad), b'1.8.3', signed) == SIG_BAD


def test_a_shorter_or_longer_file_fails(fw, signed):
    assert update(fw, TAR[:-1], b'1.8.3', signed) == SIG_BAD
    assert update(fw, TAR + b'\0', b'1.8.3', signed) == SIG_BAD


def test_a_flipped_byte_in_the_signature_fails(fw, signed):
    for at in (0, 31, 32, 63):
        bad = bytearray(signed)
        bad[at] ^= 0x80
        assert update(fw, TAR, b'1.8.3', bytes(bad)) == SIG_BAD


def test_another_tags_signature_fails(fw, signed):
    """An older release's update.tar and its signature can't pass as a newer release's."""
    assert update(fw, TAR, b'1.8.4', signed) == SIG_BAD


def test_a_wrong_key_fails(fw, tmp_path):
    (tmp_path/'u').write_bytes(TAR)
    sig = signing.sign(signing.update_message('1.8.3', tmp_path/'u'), other_key(tmp_path))
    assert update(fw, TAR, b'1.8.3', sig) == SIG_BAD


def test_no_signature_at_all_fails(fw):
    assert update(fw, TAR, b'1.8.3', bytes(64)) == SIG_BAD


def test_an_unreadable_update_is_an_io_failure(fw, signed):
    Files(fw, {})
    assert fw.fn('sig_update', C.c_int, C.c_char_p, C.c_char_p, C.c_char_p, C.c_void_p)(PATH, b'1.8.3', signed, None) == SIG_IO


def sign_list(body, pem):
    return body + b'signature ' + signing.sign(signing.list_message(body), pem).hex().encode() + b'\n'


def sig_list(fw, text):
    return fw.fn('sig_list', C.c_uint, C.c_char_p, C.c_uint)(text, len(text))


BODY = b'serial 3\nme/q2-foo 1.0 foo.zip 5 ' + b'ab' * 32 + b' Foo\n'


def test_a_signed_list_gives_its_body(fw, signing_pem):
    text = sign_list(BODY, signing_pem)
    assert sig_list(fw, text) == len(BODY)
    assert sig_list(fw, text[:-1]) == len(BODY)  # no last newline
    assert sig_list(fw, text[:-1] + b'\r\n') == len(BODY)


def test_a_changed_list_fails(fw, signing_pem):
    text = bytearray(sign_list(BODY, signing_pem))
    text[BODY.index(b'1.0')] = ord('2')
    assert sig_list(fw, bytes(text)) == 0


def test_a_list_signed_by_another_key_fails(fw, tmp_path):
    assert sig_list(fw, sign_list(BODY, other_key(tmp_path))) == 0


def test_a_flipped_signature_digit_fails(fw, signing_pem):
    text = bytearray(sign_list(BODY, signing_pem))
    text[-2] = ord('0') if text[-2] != ord('0') else ord('1')
    assert sig_list(fw, bytes(text)) == 0


@pytest.mark.parametrize('mangle', [
    lambda t: t[:t.rindex(b'signature ')],                       # unsigned
    lambda t: t[:-3] + b'\n',                                     # a digit short
    lambda t: t[:-1] + b'0\n',                                    # a digit over
    lambda t: t.replace(b'signature ', b'signature  '),          # spaced
    lambda t: t.replace(b'signature ', b'Signature '),
    lambda t: t + b'\n',                                          # not the last line
    lambda t: t + b'me/q2-bar 1.0 bar.zip 5 ' + b'cd' * 32 + b'\n',  # a line added after it
    lambda t: t[t.rindex(b'signature '):],                        # nothing signed
    lambda t: t.upper(),
])
def test_a_list_without_its_signature_last_fails(fw, signing_pem, mangle):
    assert sig_list(fw, mangle(sign_list(BODY, signing_pem))) == 0


def test_hex_both_ways(fw):
    out = C.create_string_buffer(9)
    fw.fn('sig_hex', None, C.c_char_p, C.c_uint, C.c_void_p)(b'\x00\x9f\xa0\xff', 4, out)
    assert out.value == b'009fa0ff'
    raw = (C.c_ubyte * 4)()
    unhex = fw.fn('sig_unhex', C.c_int, C.c_char_p, C.c_void_p, C.c_uint)
    assert unhex(b'009fa0ff', raw, 4) == 1 and bytes(raw) == b'\x00\x9f\xa0\xff'
    for bad in (b'009fa0fF', b'009fa0f', b'009fa0fg', b'0x9fa0ff'):
        assert unhex(bad, raw, 4) == 0
