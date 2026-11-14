"""The q2-coda release key's signatures (docs/releasing.md#signing-a-release, docs/internals.md#release-signatures).

Ed25519 over a short ASCII message, the same as patch/release_sig.c builds on the Q2:

- update.tar: "q2-coda-update-v1 TAG SHA512\\n", TAG the release's tag (so an older release's file can't pass as a
  newer one's), SHA512 the file's digest in hex; the 64-byte signature is the release's update.tar.sig.
- sources.list: "q2-coda-sources-v1 SHA512\\n" over every byte before its last line, "signature <128 hex>".

The private key never touches the disk: sign() hands it to openssl through a pipe, and broker_command() is a
command for a secret broker that has it in an environment variable and prints the signature as base64. Only
openssl (1.1.1 or later) is needed, no Python package."""
import base64
import hashlib
import os
import pathlib
import re
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC_KEY = ROOT/'assets/signing/q2-coda-release.pub'  # the release key's public half, which build.py compiles in
ENV = 'Q2_SIGNING_KEY'  # the private key's PEM, for sign() and broker_command()
SPKI = bytes.fromhex('302a300506032b6570032100')  # an Ed25519 SubjectPublicKeyInfo, before its 32 key bytes
SIG_LINE = 'signature '  # sources.list's last line, then the signature in hex


def public_key(pem):
    """The 32 key bytes of an Ed25519 public key in PEM (openssl pkey -pubout's)."""
    found = re.fullmatch(rb'\s*-----BEGIN PUBLIC KEY-----\s+([A-Za-z0-9+/=\s]+?)\s*-----END PUBLIC KEY-----\s*', pem)
    if not found:
        raise ValueError('Not a PEM public key')
    der = base64.b64decode(b''.join(found[1].split()), validate=True)
    if len(der) != len(SPKI) + 32 or not der.startswith(SPKI):
        raise ValueError('Not an Ed25519 public key')
    return der[len(SPKI):]


def file_sha512(path):
    h = hashlib.sha512()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def update_message(tag, path):
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,31}', tag):
        raise ValueError(f'Not a release tag: {tag!r}')
    return f'q2-coda-update-v1 {tag} {file_sha512(path)}\n'.encode()


def list_message(body):
    return f'q2-coda-sources-v1 {hashlib.sha512(body).hexdigest()}\n'.encode()


def _openssl(*args, key=None):
    """openssl with key's text (if any) readable at /dev/fd/N through a pipe, N in place of {key} in args."""
    if key is None:
        return subprocess.run(['openssl', *args], capture_output=True)
    read, write = os.pipe()
    try:
        proc = subprocess.Popen(['openssl', *(a.replace('{key}', f'/dev/fd/{read}') for a in args)],
                                pass_fds=(read,), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        os.close(read)
        read = -1
        with os.fdopen(write, 'w') as w:
            write = -1
            w.write(key.strip() + '\n')
        out, err = proc.communicate()
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
    finally:
        for fd in (read, write):
            if fd >= 0:
                os.close(fd)


def sign(message, key_pem):
    """Ed25519 signature of message by the private key key_pem (a PEM's text)."""
    with tempfile.NamedTemporaryFile(prefix='q2-sign-') as m:  # openssl's -rawin needs a sized file
        m.write(message)
        m.flush()
        r = _openssl('pkeyutl', '-sign', '-rawin', '-inkey', '{key}', '-in', m.name, key=key_pem)
    if r.returncode or len(r.stdout) != 64:
        raise ValueError('openssl could not sign with that key: ' + r.stderr.decode(errors='replace').strip())
    return r.stdout


def verify(message, signature, public=PUBLIC_KEY):
    """Whether signature is the Ed25519 signature of message by the public key in the PEM file public."""
    if len(signature) != 64:
        return False
    public_key(pathlib.Path(public).read_bytes())  # an Ed25519 key, not just any openssl takes
    with tempfile.TemporaryDirectory(prefix='q2-verify-') as tmp:
        (pathlib.Path(tmp)/'m').write_bytes(message)
        (pathlib.Path(tmp)/'s').write_bytes(signature)
        r = _openssl('pkeyutl', '-verify', '-pubin', '-inkey', str(public), '-rawin', '-in', f'{tmp}/m', '-sigfile', f'{tmp}/s')
    return r.returncode == 0


def broker_command(message, env=ENV):
    """A command for /bin/sh that signs message with the PEM in $env and prints the signature as base64: openssl
    -rawin wants a file it can size, so the message (ASCII, never secret) goes to a temporary file, and the key
    through a pipe."""
    text = message.decode('ascii')
    if not re.fullmatch(r'[A-Za-z0-9 ._-]+\n', text) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', env):
        raise ValueError('Unexpected message or variable name')
    return ("bash -c 'set -o pipefail; m=$(mktemp) && printf \"%s\\n\" \"" + text[:-1] + "\" > \"$m\" && "
            "openssl pkeyutl -sign -rawin -inkey <(printf \"%s\\n\" \"$" + env + "\") -in \"$m\" | base64 -w0; "
            "r=$?; rm -f \"$m\"; exit $r'")


def signature_b64(text):
    """A broker's base64 output as the 64 signature bytes."""
    sig = base64.b64decode(''.join(text.split()), validate=True)
    if len(sig) != 64:
        raise ValueError(f'A signature is 64 bytes, not {len(sig)}')
    return sig


def signed_list(text):
    """sources.list's bytes before its signature line and the signature, or (text, None) without one."""
    lines = text.splitlines(keepends=True)
    last = lines[-1].rstrip(b'\r\n') if lines else b''
    if not last.startswith(SIG_LINE.encode()):
        return text, None
    hexsig = last[len(SIG_LINE):].decode('ascii', 'replace')
    if not re.fullmatch(r'[0-9a-f]{128}', hexsig):
        return text, None
    return b''.join(lines[:-1]), bytes.fromhex(hexsig)
