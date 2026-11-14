"""tools/shelf.py: sources.list's lines, its serial, pinning against fake releases, and signing with a key made per
test (the release key's private half is never here). No network: pin's lookups and downloads are stand-ins."""
import base64
import hashlib
import subprocess

import pytest

import shelf
import signing

SHA = 'ab' * 32
PINNED = f'me/q2-foo 1.0 foo.zip 5 {SHA} Foo'


@pytest.fixture
def key(tmp_path, monkeypatch):
    """A release key for this test: its PEM, with signing.PUBLIC_KEY its public half."""
    pem, pub = tmp_path/'k.pem', tmp_path/'k.pub'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'ed25519', '-out', str(pem)], check=True, capture_output=True)
    subprocess.run(['openssl', 'pkey', '-in', str(pem), '-pubout', '-out', str(pub)], check=True, capture_output=True)
    monkeypatch.setattr(signing, 'PUBLIC_KEY', pub)
    monkeypatch.setattr(shelf, 'ROOT', tmp_path)
    return pem.read_text()


def test_a_pinned_line_and_back():
    app, = shelf.parse(PINNED)
    assert app == shelf.App('me/q2-foo', 'Foo', False, '1.0', 'foo.zip', 5, SHA) and app.line() == PINNED


@pytest.mark.parametrize('line,app', [
    ('me/q2-foo Foo Bar', shelf.App('me/q2-foo', 'Foo Bar')),                       # not pinned (yet)
    ('me/q2-foo', shelf.App('me/q2-foo')),
    ('-me/q2-old Old', shelf.App('me/q2-old', 'Old', True)),
    (f'-{PINNED}', shelf.App('me/q2-foo', 'Foo', True, '1.0', 'foo.zip', 5, SHA)),
    (f'me/q2-foo 1.0 foo.zip 5 {SHA}', shelf.App('me/q2-foo', '', False, '1.0', 'foo.zip', 5, SHA)),
])
def test_lines(line, app):
    assert shelf.parse(line) == [app]


def test_a_dropped_app_is_written_without_its_pin():
    assert shelf.parse(f'-{PINNED}')[0].line() == '-me/q2-foo Foo'


@pytest.mark.parametrize('line', ['# me/q2-foo', 'serial x', 'serial 1 2', ' me/q2-foo'])
def test_what_is_no_app_stays_as_written(line):
    assert shelf.parse(line) == [line]


@pytest.mark.parametrize('pin', [f'1.0 foo.tar 5 {SHA}', f'1.0 foo.zip 5 {SHA}0', f'1.0 foo.zip x {SHA}', f'1.0 foo.zip 5 {SHA.upper()}'])
def test_a_pin_that_is_no_pin_is_part_of_the_name(pin):
    app, = shelf.parse(f'me/q2-foo {pin}')
    assert app.tag == '' and app.name == pin


def test_the_serial():
    assert shelf.parse('serial 7\n') == [7]
    assert shelf.bump(['# x', 7, shelf.App('a/b')]) == ['# x', 8, shelf.App('a/b')]
    assert shelf.bump(['# x', shelf.App('a/b')]) == ['# x', 1, shelf.App('a/b')]


def test_what_a_q2_would_refuse():
    assert shelf.problems([1, *shelf.parse(PINNED)]) == []
    assert shelf.problems(shelf.parse(PINNED)) == ['0 serial lines, not one']
    assert shelf.problems([1, 2]) == ['2 serial lines, not one']
    assert shelf.problems([1, shelf.App('me/q2-foo', 'Foo')]) == ['me/q2-foo: not pinned (tools/shelf.py pin me/q2-foo)']
    assert shelf.problems([1, shelf.App('me/q2-foo', dropped=True)]) == []
    assert shelf.problems([1, *shelf.parse(PINNED), shelf.App('you/foo', dropped=True)]) == ['you/foo: another app has the id foo']
    big = shelf.App('me/q2-foo', '', False, '1', 'f.zip', (1 << 30) + 1, SHA)
    assert shelf.problems([1, big]) == [f'me/q2-foo: {(1 << 30) + 1} bytes, over {1 << 30}']


def releases(zips):
    def resolve(repo):
        name, data = zips[repo]
        return '2.0', dict(name=name, size=len(data), url=f'https://h/{repo}', digest=f'sha256:{hashlib.sha256(data).hexdigest()}')
    def hash_url(url, size):
        data = zips[url[len('https://h/'):]][1]
        return hashlib.sha256(data).hexdigest(), len(data)
    return resolve, hash_url


def test_pin_takes_each_latest_zip_and_raises_the_serial(tmp_path):
    lst = tmp_path/'sources.list'
    lst.write_text(f'# apps\nserial 4\nme/q2-foo Foo\n-me/q2-old Old\nyou/q2-bar 1.0 bar.zip 3 {SHA} Bar\nsignature {"00" * 64}\n')
    resolve, hash_url = releases({'me/q2-foo': ('foo.zip', b'FOO'), 'you/q2-bar': ('bar-2.zip', b'BARS')})
    changed = shelf.pin(lst, resolve=resolve, hash_url=hash_url)
    assert [a.repo for a in changed] == ['me/q2-foo', 'you/q2-bar']
    assert lst.read_text() == (f'# apps\nserial 5\nme/q2-foo 2.0 foo.zip 3 {hashlib.sha256(b"FOO").hexdigest()} Foo\n-me/q2-old Old\n'
                               f'you/q2-bar 2.0 bar-2.zip 4 {hashlib.sha256(b"BARS").hexdigest()} Bar\n')  # the old signature gone


def test_pin_with_nothing_new_changes_nothing(tmp_path):
    lst = tmp_path/'sources.list'
    text = f'serial 4\nme/q2-foo 2.0 foo.zip 3 {hashlib.sha256(b"FOO").hexdigest()} Foo\nsignature {"00" * 64}\n'
    lst.write_text(text)
    assert shelf.pin(lst, resolve=releases({'me/q2-foo': ('foo.zip', b'FOO')})[0], hash_url=releases({'me/q2-foo': ('foo.zip', b'FOO')})[1]) == []
    assert lst.read_text() == text


def test_pin_takes_only_the_repos_named(tmp_path):
    lst = tmp_path/'sources.list'
    lst.write_text('serial 1\nme/q2-foo Foo\nyou/q2-bar Bar\n')
    resolve, hash_url = releases({'me/q2-foo': ('foo.zip', b'FOO')})
    assert [a.repo for a in shelf.pin(lst, ['me/q2-foo'], resolve, hash_url)] == ['me/q2-foo']
    assert 'you/q2-bar Bar\n' in lst.read_text()


def test_pin_refuses_a_download_github_hashes_otherwise(tmp_path):
    lst = tmp_path/'sources.list'
    lst.write_text('serial 1\nme/q2-foo Foo\n')
    resolve, _ = releases({'me/q2-foo': ('foo.zip', b'FOO')})
    with pytest.raises(ValueError, match='GitHub says'):
        shelf.pin(lst, resolve=resolve, hash_url=lambda url, size: ('00' * 32, size))
    assert lst.read_text() == 'serial 1\nme/q2-foo Foo\n'


@pytest.mark.parametrize('tag,name', [('v 1', 'a.zip'), ('v1', 'a.tar'), ('v1', 'a b.zip'), ('x' * 32, 'a.zip'), ('v1', 'x' * 60 + '.zip')])
def test_pin_refuses_what_a_q2_could_not_parse(tmp_path, tag, name):
    lst = tmp_path/'sources.list'
    lst.write_text('serial 1\nme/q2-foo Foo\n')
    with pytest.raises(ValueError, match='would not parse on a Q2'):
        shelf.pin(lst, resolve=lambda repo: (tag, dict(name=name, size=3, url='u')), hash_url=lambda u, s: ('0' * 64, 3))


def test_digest_sign_verify(tmp_path, key):
    lst = tmp_path/'sources.list'
    lst.write_text(f'# apps\nserial 3\n{PINNED}\n')
    command = shelf.digest(lst)
    assert command == signing.broker_command(signing.list_message(lst.read_bytes()))
    assert lst.read_text() == f'# apps\nserial 3\n{PINNED}\n' and not shelf.verify(lst)  # numbered already: kept
    sig = signing.sign(signing.list_message(lst.read_bytes()), key)
    shelf.sign(lst, base64.b64encode(sig).decode())
    assert lst.read_text() == f'# apps\nserial 3\n{PINNED}\nsignature {sig.hex()}\n' and shelf.verify(lst)
    assert shelf.digest(lst) is None  # signed already
    lst.write_text(lst.read_text().replace('Foo', 'Fooo'))  # edited after signing
    assert not shelf.verify(lst)
    shelf.digest(lst)
    assert lst.read_text() == f'# apps\nserial 4\n{PINNED}o\n'  # numbered past the signed list, unsigned


def test_digest_numbers_a_list_with_no_serial(tmp_path, key):
    lst = tmp_path/'sources.list'
    lst.write_text(f'# apps\n{PINNED}\n')
    shelf.digest(lst)
    assert lst.read_text() == f'# apps\nserial 1\n{PINNED}\n'


def test_digest_refuses_a_list_a_q2_would_not_take(tmp_path, key):
    lst = tmp_path/'sources.list'
    lst.write_text('serial 1\nme/q2-foo Foo\n')
    with pytest.raises(ValueError, match='not pinned'):
        shelf.digest(lst)


def test_sign_with_the_key_itself(tmp_path, key):
    lst = tmp_path/'sources.list'
    lst.write_text(f'serial 1\n{PINNED}\n')
    shelf.sign(lst, key=key)
    assert shelf.verify(lst)


def test_sign_refuses_another_keys_signature(tmp_path, key):
    lst = tmp_path/'sources.list'
    lst.write_text(f'serial 1\n{PINNED}\n')
    other = tmp_path/'o.pem'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'ed25519', '-out', str(other)], check=True, capture_output=True)
    sig = signing.sign(signing.list_message(lst.read_bytes()), other.read_text())
    with pytest.raises(ValueError, match='does not check against'):
        shelf.sign(lst, base64.b64encode(sig).decode())
    assert lst.read_text() == f'serial 1\n{PINNED}\n'


def test_verify_refuses_a_signed_list_a_q2_would_not_take(tmp_path, key):
    lst = tmp_path/'sources.list'
    body = b'serial 1\nme/q2-foo Foo\n'
    lst.write_bytes(body + b'signature ' + signing.sign(signing.list_message(body), key).hex().encode() + b'\n')
    assert not shelf.verify(lst)
