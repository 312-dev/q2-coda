"""tools/signing.py and the release key in tools/build.py: messages, keys, signing through openssl, the broker's
command, and the build's refusal of a release without the tracked key. Keys are made per test; none is real but
the tracked public key."""
import base64
import hashlib
import os
import subprocess

import pytest

import build
import conftest
import signing

TRACKED = '83aada66f5dc0be8c12db899d290bd817a21b24b7fc0637dc3ddfa57a3065f93'  # assets/signing/q2-coda-release.pub


def keypair(tmp_path, name='k'):
    pem = tmp_path/f'{name}.pem'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'ed25519', '-out', str(pem)], check=True, capture_output=True)
    pub = tmp_path/f'{name}.pub'
    subprocess.run(['openssl', 'pkey', '-in', str(pem), '-pubout', '-out', str(pub)], check=True, capture_output=True)
    return pem.read_text(), pub


def test_the_tracked_key_is_the_release_keys():
    assert signing.public_key(signing.PUBLIC_KEY.read_bytes()).hex() == TRACKED


def test_a_public_key_from_its_pem(tmp_path):
    pem, pub = keypair(tmp_path)
    der = subprocess.run(['openssl', 'pkey', '-pubin', '-in', str(pub), '-outform', 'DER'], capture_output=True, check=True).stdout
    assert signing.public_key(pub.read_bytes()) == der[-32:] and len(der) == 44


@pytest.mark.parametrize('pem', [
    b'', b'garbage', b'-----BEGIN PUBLIC KEY-----\n-----END PUBLIC KEY-----\n',
    b'-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAg6raZvXcC+jBLbiZ0pC9gXohskt/wGN9w936V6MGX5M\n-----END PUBLIC KEY-----\n',  # cut
    # an X25519 key: the same length, another algorithm
    b'-----BEGIN PUBLIC KEY-----\n' + base64.b64encode(bytes.fromhex('302a300506032b656e032100') + bytes(32)) + b'\n-----END PUBLIC KEY-----\n',
])
def test_anything_but_an_ed25519_public_key_is_refused(pem):
    with pytest.raises(ValueError):
        signing.public_key(pem)


def test_a_private_key_is_no_public_key(tmp_path):
    pem, _ = keypair(tmp_path)
    with pytest.raises(ValueError, match='^Not a PEM public key$'):
        signing.public_key(pem.encode())


def test_an_ec_key_is_refused(tmp_path):
    pem = tmp_path/'ec.pem'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256', '-out', str(pem)], check=True, capture_output=True)
    pub = subprocess.run(['openssl', 'pkey', '-in', str(pem), '-pubout'], check=True, capture_output=True).stdout
    with pytest.raises(ValueError, match='^Not an Ed25519 public key$'):
        signing.public_key(pub)


def test_the_update_message(tmp_path):
    (tmp_path/'u').write_bytes(b'tar' * 1000000)
    digest = hashlib.sha512(b'tar' * 1000000).hexdigest()
    assert signing.update_message('1.8.3', tmp_path/'u') == f'q2-coda-update-v1 1.8.3 {digest}\n'.encode()


@pytest.mark.parametrize('tag', ['', 'a b', '1.0"', "1.0'", 'x' * 32, '1.0\n', '../x', '$(x)'])
def test_a_tag_that_is_no_tag_is_refused(tmp_path, tag):
    (tmp_path/'u').write_bytes(b'')
    with pytest.raises(ValueError, match='^Not a release tag'):
        signing.update_message(tag, tmp_path/'u')


def test_the_list_message():
    assert signing.list_message(b'abc') == b'q2-coda-sources-v1 ' + hashlib.sha512(b'abc').hexdigest().encode() + b'\n'


def test_sign_and_verify(tmp_path):
    pem, pub = keypair(tmp_path)
    m = b'q2-coda-update-v1 1.0 ' + b'0' * 128 + b'\n'
    sig = signing.sign(m, pem)
    assert len(sig) == 64 and signing.verify(m, sig, pub)
    assert not signing.verify(m[:-1] + b'x', sig, pub)
    assert not signing.verify(m, bytes([sig[0] ^ 1]) + sig[1:], pub)
    assert not signing.verify(m, sig[:-1], pub)
    other, other_pub = keypair(tmp_path, 'other')
    assert not signing.verify(m, sig, other_pub)
    assert not signing.verify(m, signing.sign(m, other), pub)


def test_a_bad_private_key_fails_to_sign():
    with pytest.raises(ValueError, match='^openssl could not sign'):
        signing.sign(b'm', 'not a key')


def test_the_private_key_is_never_on_disk_or_on_the_command_line(tmp_path, monkeypatch):
    """It goes through a pipe: openssl's arguments name /dev/fd/N, and the only file written is the message."""
    pem, pub = keypair(tmp_path)
    seen = []
    real = subprocess.Popen

    def popen(args, *rest, **kw):
        seen.append(list(map(str, args)))
        return real(args, *rest, **kw)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    signing.sign(b'msg\n', pem)
    args, = seen
    assert args[args.index('-inkey') + 1].startswith('/dev/fd/') and not any('PRIVATE' in a for a in args)


def test_a_verify_against_something_not_an_ed25519_key_is_refused(tmp_path):
    (tmp_path/'p').write_text('nope')
    with pytest.raises(ValueError):
        signing.verify(b'm', bytes(64), tmp_path/'p')


def test_the_broker_command_signs_with_the_variable_and_prints_base64(tmp_path, monkeypatch):
    """Run as the broker runs it, under /bin/sh with the key only in its environment."""
    pem, pub = keypair(tmp_path)
    m = b'q2-coda-update-v1 1.8.3 ' + b'ab' * 64 + b'\n'
    monkeypatch.setattr(subprocess, 'Popen', conftest.REAL_POPEN)
    env = {**os.environ, 'SECRET_PEM': pem}
    out = subprocess.run(['/bin/sh', '-c', signing.broker_command(m, 'SECRET_PEM')], env=env, capture_output=True, text=True, cwd=tmp_path)
    assert out.returncode == 0 and signing.verify(m, signing.signature_b64(out.stdout), pub)
    assert sorted(p.name for p in tmp_path.iterdir()) == ['k.pem', 'k.pub']  # nothing left behind
    bad = subprocess.run(['/bin/sh', '-c', signing.broker_command(m, 'SECRET_PEM')], env={**env, 'SECRET_PEM': 'x'}, capture_output=True, text=True)
    assert bad.returncode != 0 and bad.stdout == ''


@pytest.mark.parametrize('message,env', [(b'a"b\n', 'K'), (b"a'b\n", 'K'), (b'a$(b)\n', 'K'), (b'ab', 'K'), (b'ab\n', 'K; rm'), (b'ab\n', '1K')])
def test_the_broker_command_takes_only_a_plain_message_and_name(message, env):
    with pytest.raises(ValueError):
        signing.broker_command(message, env)


def test_a_signature_from_base64():
    assert signing.signature_b64(base64.b64encode(bytes(range(64))).decode() + '\n') == bytes(range(64))
    with pytest.raises(ValueError):
        signing.signature_b64(base64.b64encode(bytes(63)).decode())
    with pytest.raises(ValueError):
        signing.signature_b64('not base64!')


def test_a_signed_list_splits_at_its_last_line():
    sig = bytes(range(64))
    body = b'serial 1\nme/q2-foo\n'
    assert signing.signed_list(body + b'signature ' + sig.hex().encode() + b'\n') == (body, sig)
    assert signing.signed_list(body + b'signature ' + sig.hex().encode()) == (body, sig)
    assert signing.signed_list(body) == (body, None)
    assert signing.signed_list(b'') == (b'', None)
    short = body + b'signature ' + sig.hex()[:-1].encode() + b'\n'
    assert signing.signed_list(short) == (short, None)


def test_a_release_build_takes_the_tracked_key_only(tmp_path):
    assert build.signing_key(None, False).hex() == TRACKED
    assert build.signing_key(None, 1).hex() == TRACKED
    with pytest.raises(ValueError, match='^--signing-key requires --dev'):
        build.signing_key(tmp_path/'k.pub', False)


def test_a_dev_build_may_take_another_key(tmp_path):
    _, pub = keypair(tmp_path)
    assert build.signing_key(pub, 1) == signing.public_key(pub.read_bytes())


def test_no_key_no_build(tmp_path, monkeypatch):
    monkeypatch.setattr(signing, 'PUBLIC_KEY', tmp_path/'missing.pub')
    with pytest.raises(ValueError, match='missing.pub: no release public key'):
        build.signing_key(None, False)
    with pytest.raises(ValueError, match='gone.pub: no release public key'):
        build.signing_key(tmp_path/'gone.pub', 1)


def test_the_key_is_in_stock_h():
    from theme import builtins
    from test_build import RANGES
    context = {name: '0' for name in build.CONTEXT_DATA}
    header = build.stock_header(context, RANGES, builtins(), key=bytes(range(32)))
    assert '#define Q2POD_SIGN_KEY {' + ','.join(f'0x{b:02x}' for b in range(32)) + '}' in header.splitlines()
    tracked = build.stock_header(context, RANGES, builtins())
    assert '#define Q2POD_SIGN_KEY {' + ','.join(f'0x{b:02x}' for b in bytes.fromhex(TRACKED)) + '}' in tracked.splitlines()
