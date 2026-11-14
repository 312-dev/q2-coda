"""tools/shelf.py: sources.list's lines, its serial, pinning against fake releases, and signing with a key made per
test (the release key's private half is never here). No network: pin's lookups and downloads are stand-ins."""
import base64
import hashlib
import io
import json
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


def test_the_repos_list_is_pinned_and_numbered():
    """Every app the shipped list offers has a pin; a signature, if there is one yet, is the release key's."""
    body, sig = signing.signed_list(shelf.LIST.read_bytes())
    items = shelf.parse(body.decode())
    assert shelf.problems(items) == [] and [x for x in items if isinstance(x, shelf.App)]
    assert sig is None or signing.verify(signing.list_message(body), sig)


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


def test_sign_refuses_a_list_a_q2_would_not_take(tmp_path, key):
    lst = tmp_path/'sources.list'
    lst.write_text('serial 1\nme/q2-foo Foo\n')
    with pytest.raises(ValueError, match='not pinned'):
        shelf.sign(lst, key=key)
    assert lst.read_text() == 'serial 1\nme/q2-foo Foo\n'


class Response(io.BytesIO):
    """urlopen's answer: its bytes, read in the chunks asked for."""
    def __init__(self, data):
        super().__init__(data)
        self.reads = []

    def read(self, n=-1):
        self.reads.append(n)
        return super().read(n)


def test_get_asks_github_as_the_shelf_with_a_timeout(monkeypatch):
    asked = []
    monkeypatch.setattr(shelf.urllib.request, 'urlopen', lambda request, timeout: asked.append((request, timeout)) or 'r')
    assert shelf.get('https://h/x') == 'r' and shelf.get('https://h/y', 'application/octet-stream') == 'r'
    (one, t1), (two, t2) = asked
    assert (one.full_url, one.get_header('User-agent'), one.get_header('Accept'), t1) == (
        'https://h/x', 'q2-coda-shelf', 'application/vnd.github+json', 60)
    assert (two.full_url, two.get_header('Accept'), t2) == ('https://h/y', 'application/octet-stream', 60)


def test_latest_takes_the_first_zip(monkeypatch):
    urls = []
    assets: list[dict] = [
        {'name': 'notes.txt', 'size': 1, 'browser_download_url': 'u0'},
        {'name': 'a.zip', 'size': 7, 'browser_download_url': 'u1', 'digest': 'sha256:' + SHA},
        {'name': 'b.zip', 'size': 8, 'browser_download_url': 'u2'}]
    release = {'tag_name': 'v2', 'assets': assets}
    monkeypatch.setattr(shelf, 'get', lambda url: urls.append(url) or Response(json.dumps(release).encode()))
    assert shelf.latest('me/q2-foo') == ('v2', dict(name='a.zip', size=7, url='u1', digest='sha256:' + SHA))
    assert urls == ['https://api.github.com/repos/me/q2-foo/releases/latest']
    del assets[1]
    assert shelf.latest('me/q2-foo') == ('v2', dict(name='b.zip', size=8, url='u2', digest=None))  # GitHub gave no digest


@pytest.mark.parametrize('assets', [[], [{'name': 'a.tar', 'size': 1, 'browser_download_url': 'u'}], None])
def test_latest_refuses_a_release_with_no_zip(monkeypatch, assets):
    release = {'tag_name': 'v3'} if assets is None else {'tag_name': 'v3', 'assets': assets}
    monkeypatch.setattr(shelf, 'get', lambda url: Response(json.dumps(release).encode()))
    with pytest.raises(ValueError, match=r'^me/q2-foo: its latest release, v3, has no \.zip$'):
        shelf.latest('me/q2-foo')


def test_fetch_sha256_hashes_and_counts_in_chunks(monkeypatch):
    data = bytes(range(256)) * 9000  # past one chunk
    answers = []
    def get(url, accept):
        answers.append((url, accept, Response(data)))
        return answers[-1][2]
    monkeypatch.setattr(shelf, 'get', get)
    assert shelf.fetch_sha256('https://h/a.zip', len(data)) == (hashlib.sha256(data).hexdigest(), len(data))
    (url, accept, r), = answers
    assert (url, accept) == ('https://h/a.zip', 'application/octet-stream') and set(r.reads) == {1 << 20}
    assert r.closed


def test_fetch_sha256_refuses_a_size_the_release_did_not_say(monkeypatch):
    monkeypatch.setattr(shelf, 'get', lambda url, accept: Response(b'four'))
    with pytest.raises(ValueError, match=r'^https://h/a.zip: 4 bytes, the release says 5$'):
        shelf.fetch_sha256('https://h/a.zip', 5)
    assert shelf.fetch_sha256('https://h/a.zip', 4) == (hashlib.sha256(b'four').hexdigest(), 4)


def test_fetch_sha256_stops_past_the_cap(monkeypatch):
    monkeypatch.setattr(shelf, 'CAP', 3)
    monkeypatch.setattr(shelf, 'get', lambda url, accept: Response(b'four'))
    with pytest.raises(ValueError, match=r'^https://h/a.zip: over 3 bytes$'):
        shelf.fetch_sha256('https://h/a.zip', 4)
    monkeypatch.setattr(shelf, 'CAP', 4)  # the cap itself is allowed
    assert shelf.fetch_sha256('https://h/a.zip', 4)[1] == 4


def test_main_pin_prints_each_new_pin(tmp_path, monkeypatch, capsys):
    lst = tmp_path/'sources.list'
    asked = []
    new = shelf.App('me/q2-foo', 'Foo', False, '2.0', 'foo.zip', 3, SHA)
    monkeypatch.setattr(shelf, 'pin', lambda path, repos: asked.append((path, repos)) or [new])
    assert shelf.main(['--list', str(lst), 'pin', 'me/q2-foo', 'you/q2-bar']) == 0
    assert asked == [(lst, ['me/q2-foo', 'you/q2-bar'])]
    assert capsys.readouterr().out == f'me/q2-foo: 2.0 foo.zip 3 {SHA}\n'
    assert shelf.main(['--list', str(lst), 'pin']) == 0 and asked[-1] == (lst, [])


def test_main_digest_sign_verify(tmp_path, key, monkeypatch, capsys):
    lst = tmp_path/'sources.list'
    lst.write_text(f'serial 1\n{PINNED}\n')
    assert shelf.main(['--list', str(lst), 'verify']) == 1
    assert capsys.readouterr().out == f'{lst}: NOT signed by the release key\n'
    assert shelf.main(['--list', str(lst), 'digest', '--env', 'MY_PEM']) == 0
    out = capsys.readouterr()
    assert out.out == signing.broker_command(signing.list_message(lst.read_bytes()), 'MY_PEM') + '\n' and not out.err
    monkeypatch.delenv(signing.ENV, raising=False)
    with pytest.raises(SystemExit) as exit:
        shelf.main(['--list', str(lst), 'sign'])
    assert exit.value.code == 2 and f'--signature, or the private key in ${signing.ENV}' in capsys.readouterr().err
    monkeypatch.setenv(signing.ENV, key)
    assert shelf.main(['--list', str(lst), 'sign']) == 0 and capsys.readouterr().out == f'{lst}: signed\n'
    assert shelf.main(['--list', str(lst), 'verify']) == 0
    assert capsys.readouterr().out == f'{lst}: signed by the release key\n'
    assert shelf.main(['--list', str(lst), 'digest']) == 0
    out = capsys.readouterr()
    assert out.err == f'{lst}: signed already\n' and not out.out


def test_main_sign_with_the_brokers_signature(tmp_path, key, monkeypatch, capsys):
    lst = tmp_path/'sources.list'
    lst.write_text(f'serial 1\n{PINNED}\n')
    monkeypatch.delenv(signing.ENV, raising=False)
    sig = signing.sign(signing.list_message(lst.read_bytes()), key)
    assert shelf.main(['--list', str(lst), 'sign', '--signature', base64.b64encode(sig).decode()]) == 0
    assert shelf.verify(lst) and capsys.readouterr().out == f'{lst}: signed\n'


@pytest.mark.parametrize('command', [['verify'], ['digest'], ['sign', '--signature', 'AAAA'], ['pin']])
def test_main_says_what_failed_and_exits_2(tmp_path, command, capsys):
    lst = tmp_path/'missing.list'
    with pytest.raises(SystemExit) as exit:
        shelf.main(['--list', str(lst), *command])
    assert exit.value.code == 2 and 'missing.list' in capsys.readouterr().err.splitlines()[-1]


def test_main_turns_a_value_error_into_a_usage_error(tmp_path, capsys):
    lst = tmp_path/'sources.list'
    lst.write_text('serial 1\nme/q2-foo Foo\n')
    with pytest.raises(SystemExit) as exit:
        shelf.main(['--list', str(lst), 'digest'])
    assert exit.value.code == 2 and capsys.readouterr().err.splitlines()[-1].endswith(
        'error: me/q2-foo: not pinned (tools/shelf.py pin me/q2-foo)')


def test_main_needs_a_command(capsys):
    with pytest.raises(SystemExit) as exit:
        shelf.main([])
    assert exit.value.code == 2
