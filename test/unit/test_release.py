"""tools/release.py with fake builds and a fake gh: nothing is packaged for real or sent anywhere."""
import base64
import hashlib
import io
import json
import pathlib
import re
import subprocess
import tarfile
import types

import pytest

import release
import signing


def sha(data):
    return hashlib.sha256(data).hexdigest()


def md5(data):
    return hashlib.md5(data).hexdigest()


TRACKED = release.public_key(release.PUBLIC_KEY.read_bytes()).hex()  # assets/signing/q2-coda-release.pub's bytes
MEMBERS = ['firmware_v20.info', 'recovery-update', 'recovery-update/xImage', 'recovery-update/rootfs.squashfs']
KERNEL, ROOTFS = b'kernel', b'rootfs'


def update_tar(path, info=None, members=None, payload=None):
    info = info if info is not None else (f'Shanling Q2\n{release.TAG}\n{md5(KERNEL)} recovery-update/xImage\n'
                                          f'{md5(ROOTFS)} recovery-update/rootfs.squashfs\n')
    contents = {'firmware_v20.info': info.encode(), 'recovery-update/xImage': KERNEL,
                'recovery-update/rootfs.squashfs': ROOTFS, **(payload or {})}
    with tarfile.open(path, 'w') as t:
        for name in members or MEMBERS:
            entry = tarfile.TarInfo(name)
            if name == 'recovery-update':
                entry.type = tarfile.DIRTYPE
                t.addfile(entry)
            else:
                entry.size = len(contents[name])
                t.addfile(entry, io.BytesIO(contents[name]))


def carried():
    """manifest.json's bundled_apps for the pinned apps, as tools/build.py writes it."""
    return {app_id: dict(version=v, zip=z, sha256=d, builtin=b, bytes=1, binary_sha256='b' * 64, build='-')
            for app_id, (v, z, _, d, b) in release.BUNDLED_APPS.items()}


def built(directory, manifest=None, **tar):
    directory.mkdir(exist_ok=True)
    (directory/'rootfs.squashfs').write_bytes(ROOTFS)
    update_tar(directory/'update.tar', **tar)
    fields = dict(version=release.TAG, update_repo=release.REPO, signing_key=TRACKED, input_zip_sha256=release.ZIP_SHA,
                  stock_demo_sha256=release.DEMO_SHA, rootfs_sha256=sha(ROOTFS), kernel_sha256=sha(KERNEL),
                  update_sha256=sha((directory/'update.tar').read_bytes()), bundled_apps=carried(), **(manifest or {}))
    (directory/'manifest.json').write_text(json.dumps(fields))
    return directory


class Suites(list):
    """Every suite release.suites started, as (args, kwargs). One named in failing exits 1."""
    failing = ()

    def start(self, args, **kwargs):
        self.append((args, kwargs))
        return types.SimpleNamespace(wait=lambda: int(pathlib.Path(args[1]).stem in self.failing))


@pytest.fixture
def scripts(monkeypatch):
    calls = Suites()
    monkeypatch.setattr(subprocess, 'Popen', calls.start)
    return calls


def test_validate_runs_every_suite(tmp_path, scripts):
    d = built(tmp_path/'b')
    release.validate(d)
    names = [(pathlib.Path(a[1]).relative_to(release.ROOT/'test').as_posix(), a[2:]) for a, _ in scripts]
    s = str(d)
    assert names == [('peq.py', []), ('build.py', ['--build', s]), ('patch.py', [s]), ('playback.py', [s]), ('m4b.py', [s]),
                     ('ldac.py', [s]), ('headset_io.py', [s]), ('bluealsa.py', [s]), ('coverflow.py', []),
                     ('player/run.py', []), ('remote.py', [])]
    assert all(k == {} for _, k in scripts)
    assert all(a[0] == release.sys.executable and a[1] == release.ROOT/'test'/n for (a, _), (n, _) in zip(scripts, names))


def test_validate_names_the_suites_that_fail(tmp_path, scripts):
    scripts.failing = ('patch', 'remote')
    with pytest.raises(ValueError, match=r'^Failed: test/patch\.py, test/remote\.py$'):
        release.validate(built(tmp_path/'b'))
    assert len(scripts) == 11  # all were started, and waited for, before any failure was reported


@pytest.mark.parametrize('field,value,message', [
    ('version', 'V000I', '^Wrong build identity$'),
    ('update_repo', 'someone/fork', f"^Network Update fetches someone/fork's releases, not {release.REPO}'s$"),
    ('signing_key', '00' * 32, '^Built with another release key than assets/signing/q2-coda-release.pub$'),
    ('signing_key', None, '^Built with another release key than'),
    ('input_zip_sha256', 'x', '^Wrong stock identity$'),
    ('stock_demo_sha256', 'x', '^Wrong stock identity$'),
    ('rootfs_sha256', 'x', 'rootfs.squashfs: hash mismatch$'),
    ('update_sha256', 'x', 'update.tar: hash mismatch$'),
    ('kernel_sha256', 'x', '^Update payload mismatch$'),
    ('bundled_apps', {}, '^The build carries \\[\\], not the pinned'),
    ('bundled_apps', {i: {**a, 'sha256': '0' * 64} for i, a in carried().items()}, 'is not the pinned'),
    ('bundled_apps', {i: {**a, 'binary_sha256': ''} for i, a in carried().items()}, 'is not the pinned'),
])
def test_validate_refuses_a_manifest(tmp_path, scripts, field, value, message):
    d = built(tmp_path/'b', {})
    fields = json.loads((d/'manifest.json').read_text())
    fields[field] = value
    (d/'manifest.json').write_text(json.dumps(fields))
    with pytest.raises(ValueError, match=message):
        release.validate(d)
    assert not scripts


def test_validate_refuses_extra_members(tmp_path, scripts):
    d = built(tmp_path/'b', members=MEMBERS[::-1])
    with pytest.raises(ValueError, match='^Unexpected update members$'):
        release.validate(d)


@pytest.mark.parametrize('info', [
    'Shanling Q3\nV\nx\ny\n',
    f'Shanling Q2\nV000I\n{md5(KERNEL)} recovery-update/xImage\n{md5(ROOTFS)} recovery-update/rootfs.squashfs\n',
    f'Shanling Q2\n{release.TAG}\n{md5(KERNEL)} recovery-update/xImage\n',
    f'Shanling Q2\n{release.TAG}\n{md5(KERNEL)} recovery-update/xImage\n{md5(ROOTFS)} recovery-update/rootfs.squashfs\nmore\n',
])
def test_validate_refuses_a_wrong_identity(tmp_path, scripts, info):
    d = built(tmp_path/'b', info=info)
    with pytest.raises(ValueError, match='^Wrong update identity$'):
        release.validate(d)


@pytest.mark.parametrize('line2,line3', [
    (f'{md5(b"x")} recovery-update/xImage', f'{md5(ROOTFS)} recovery-update/rootfs.squashfs'),
    (f'{md5(KERNEL)} other', f'{md5(ROOTFS)} recovery-update/rootfs.squashfs'),
    (f'{md5(KERNEL)} recovery-update/xImage', f'{md5(ROOTFS)} recovery-update/xImage'),
])
def test_validate_refuses_wrong_checksum_lines(tmp_path, scripts, line2, line3):
    d = built(tmp_path/'b', info=f'Shanling Q2\n{release.TAG}\n{line2}\n{line3}\n')
    with pytest.raises(ValueError, match='^Update payload mismatch$'):
        release.validate(d)


KEY = None


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """release's module globals pointed at a temporary checkout with this version's changelog entry, a release key
    made for the test (KEY its private PEM) and a signed sources.list."""
    global KEY
    (tmp_path/'docs').mkdir()
    (tmp_path/'docs/changelog.md').write_text(f'- **{release.VERSION}**: One.\n')
    monkeypatch.setattr(release, 'ROOT', tmp_path)
    monkeypatch.setattr(release, 'source_sha256', lambda: 'source')
    pem, pub = tmp_path/'k.pem', tmp_path/'assets/signing/q2-coda-release.pub'
    pub.parent.mkdir(parents=True)
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'ed25519', '-out', str(pem)], check=True, capture_output=True)
    subprocess.run(['openssl', 'pkey', '-in', str(pem), '-pubout', '-out', str(pub)], check=True, capture_output=True)
    KEY = pem.read_text()
    monkeypatch.setattr(signing, 'PUBLIC_KEY', pub)
    monkeypatch.setattr(release, 'PUBLIC_KEY', pub)
    monkeypatch.setattr(release.shelf, 'verify', lambda: True)
    return tmp_path


class Packaging:
    def __init__(self, monkeypatch, sources=('source',) * 2, revisions=('rev',) * 2, status='', repeat=b'tar', sites=0):
        self.calls, self.builds, self.validated, self.scripts, self.sites = [], [], [], [], []
        sources, revisions = iter(sources), iter(revisions)
        def run(*args):
            self.calls.append(args)
            return status if 'status' in args else next(revisions)
        def builder(stock, directory, logo):
            self.builds.append((stock, directory, logo))
            directory.mkdir()
            (directory/'update.tar').write_bytes(repeat if directory.name.endswith('-repeat') else b'tar')
            (directory/'manifest.json').write_text(json.dumps({'bundled_apps': carried()}))
        def script(args, check=False):
            if args[1] == '-I':  # sitenotes.py --check
                self.sites.append(args)
                return subprocess.CompletedProcess(args, sites)
            self.scripts.append((args, check))
            pathlib.Path(args[2]).write_bytes(b'%PDF')
        monkeypatch.setattr(release, 'run', run)
        monkeypatch.setattr(release, 'build', builder)
        monkeypatch.setattr(release, 'validate', self.validated.append)
        monkeypatch.setattr(subprocess, 'run', script)
        monkeypatch.setattr(release, 'source_sha256', lambda: next(sources))


def test_package(repo, monkeypatch, tmp_path, capsys):
    p = Packaging(monkeypatch)
    out = tmp_path/'nested/release'
    release.package(pathlib.Path('stock.zip'), out)
    assert p.calls[0] == ('git', '-C', repo, 'status', '--porcelain', '--', 'patch', 'tools', 'assets', 'docs', 'test', 'requirements.txt')
    assert p.calls[1] == p.calls[2] == ('git', '-C', repo, 'rev-parse', 'HEAD')
    logo = repo/'assets/boot-logo.jpg'
    assert p.builds == [(pathlib.Path('stock.zip'), out/'build', logo), (pathlib.Path('stock.zip'), out/'build-repeat', logo)]
    assert p.validated == [out/'build']
    assert p.sites == [[release.sys.executable, '-I', repo/'docs/rebasing/sitenotes.py', pathlib.Path('stock.zip'), '--check']]
    assert (out/release.ASSET).read_bytes() == b'tar'
    assert p.scripts == [([release.sys.executable, str(repo/'docs/manual/build.py'), str(out/release.GUIDE)], True)]
    assert (out/'SHA256SUMS').read_text() == f'{sha(b"tar")}  update.tar\n'
    apps = ''.join(f'- {z} (carried as usr/lib/q2apps/{i}): `{d}`\n' for i, (_, z, _, d, _) in sorted(release.BUNDLED_APPS.items()))
    notes = f'**{release.VERSION}**\n- One.\n\nSHA-256:\n- update.tar ({release.TAG}): `{sha(b"tar")}`\n' + apps
    assert (out/'release-notes.md').read_text() == notes
    record = json.loads((out/'release.json').read_text())
    pinned = {i: {k: a[k] for k in ('zip', 'sha256', 'binary_sha256', 'build')} for i, a in carried().items()}
    assert record == dict(tag=release.VERSION, revision='rev', source_sha256='source', assets={'update.tar': sha(b'tar')},
                          bundled_apps=pinned, guide_sha256=sha(b'%PDF'), notes_sha256=sha(notes.encode()))
    assert (out/'release.json').read_text().endswith('}\n') and '\n  "tag"' in (out/'release.json').read_text()
    assert capsys.readouterr().out == f'Validated and reproducible: {out}\n'


def test_package_wants_a_signed_app_list(repo, monkeypatch, tmp_path):
    p = Packaging(monkeypatch)
    monkeypatch.setattr(release.shelf, 'verify', lambda: False)
    with pytest.raises(ValueError, match='^sources.list is not signed by the release key'):
        release.package(pathlib.Path('stock.zip'), tmp_path/'out')
    assert not (tmp_path/'out').exists() and not p.builds


def test_package_wants_current_stock_sites(repo, monkeypatch, tmp_path):
    # checked against the stock ZIP before anything is built or written
    p = Packaging(monkeypatch, sites=1)
    with pytest.raises(ValueError, match='^' + re.escape("tools/stock_sites.tsv is not current: run python3 -I docs/rebasing/sitenotes.py 'stock.zip' and commit it") + '$'):
        release.package(pathlib.Path('stock.zip'), tmp_path/'out')
    assert len(p.sites) == 1 and not (tmp_path/'out').exists() and not p.builds


def test_package_wants_a_fresh_directory(repo, monkeypatch, tmp_path):
    p = Packaging(monkeypatch)
    with pytest.raises(ValueError, match='^Release output must be a fresh directory$'):
        release.package(pathlib.Path('stock.zip'), tmp_path)
    assert not p.calls


def test_package_wants_three_single_digits(repo, monkeypatch, tmp_path):
    # refused before the output folder is made, so a rerun with a fixed VERSION finds it fresh
    p = Packaging(monkeypatch)
    monkeypatch.setattr(release, 'VERSION', '1.76')
    with pytest.raises(ValueError, match='^VERSION must be three single digits'):
        release.package(pathlib.Path('stock.zip'), tmp_path/'out')
    assert not (tmp_path/'out').exists() and not p.calls


def test_package_wants_committed_inputs(repo, monkeypatch, tmp_path):
    p = Packaging(monkeypatch, status=' M tools/build.py\n')
    with pytest.raises(ValueError, match='^Commit the build inputs before packaging$'):
        release.package(pathlib.Path('stock.zip'), tmp_path/'out')
    assert not (tmp_path/'out').exists() and not p.builds


def test_package_wants_the_newest_changelog_entry(repo, monkeypatch, tmp_path):
    # A VERSION behind docs/changelog.md's newest entry is refused before anything is built or written.
    (repo/'docs/changelog.md').write_text(f'# Changelog\n\n- **9.9.9**: Newer.\n- **{release.VERSION}**: One.\n')
    p = Packaging(monkeypatch)
    with pytest.raises(ValueError, match='^' + re.escape(f"{release.VERSION} is not docs/changelog.md's newest entry") + '$'):
        release.package(pathlib.Path('stock.zip'), tmp_path/'out')
    assert not (tmp_path/'out').exists() and not p.builds


def test_package_ignores_blank_status(repo, monkeypatch, tmp_path):
    p = Packaging(monkeypatch, status=' \n')
    release.package(pathlib.Path('stock.zip'), tmp_path/'out')
    assert p.builds


def test_package_wants_identical_builds(repo, monkeypatch, tmp_path):
    p = Packaging(monkeypatch, repeat=b'other')
    with pytest.raises(ValueError, match='^Non-reproducible update$'):
        release.package(pathlib.Path('stock.zip'), tmp_path/'out')
    assert not (tmp_path/'out/update.tar').exists()


@pytest.mark.parametrize('kwargs', [dict(sources=('a', 'b')), dict(revisions=('a', 'b'))])
def test_package_wants_a_steady_source(repo, monkeypatch, tmp_path, kwargs):
    Packaging(monkeypatch, **kwargs)
    with pytest.raises(ValueError, match='^Source changed during release build$'):
        release.package(pathlib.Path('stock.zip'), tmp_path/'out')
    assert not (tmp_path/'out/release.json').exists()


def packaged(root, signed=True):
    """A package as release.package leaves it, then signed with the test's key (release.sign) unless signed is off."""
    out = root/'package'
    (out/'build').mkdir(parents=True)
    (out/'build/update.tar').write_bytes(b'pod')
    (out/release.ASSET).write_bytes(b'pod')
    (out/release.GUIDE).write_bytes(b'%PDF')
    (out/'SHA256SUMS').write_text(f'{sha(b"pod")}  {release.ASSET}\n')
    (out/'release-notes.md').write_bytes(b'notes\n')
    (out/'release.json').write_text(json.dumps(dict(tag=release.VERSION, revision='rev', source_sha256='source',
                                                    assets={release.ASSET: sha(b'pod')}, guide_sha256=sha(b'%PDF'),
                                                    notes_sha256=sha(b'notes\n'))))
    if signed:
        release.sign(out, key=KEY)
    return out


class Gh:
    def __init__(self, monkeypatch, out, releases='[]', corrupt=None, tag=''):
        self.calls, self.out = [], out
        self.releases, self.corrupt, self.tag = releases, corrupt, tag
        monkeypatch.setattr(release, 'run', self)

    def __call__(self, *args):
        self.calls.append(args)
        if args[3:5] == ('release', 'list') or args[4:5] == ('list',):
            return self.releases
        if args[:2] == ('git', 'ls-remote'):
            return self.tag
        if 'download' in args:
            target = pathlib.Path(args[-1])
            for name in (release.ASSET, release.SIGNATURE, release.GUIDE):
                (target/name).write_bytes(b'bad' if name == self.corrupt else (self.out/name).read_bytes())
        return ''


def test_upload_draft(repo, monkeypatch, capsys):
    out = packaged(repo)
    gh = Gh(monkeypatch, out)
    release.upload(out)
    prefix = ('gh', '--repo', release.REPO, 'release')
    assert gh.calls[0] == (*prefix, 'list', '--limit', '1000', '--json', 'tagName,isDraft')
    assert gh.calls[1] == ('git', 'ls-remote', f'https://github.com/{release.REPO}.git', f'refs/tags/{release.VERSION}',
                           f'refs/tags/{release.VERSION}^{{}}')
    assert gh.calls[2] == (*prefix, 'create', release.VERSION, '--draft', '--target', 'rev', '--title', release.VERSION,
                           '--notes-file', out/'release-notes.md')
    assert gh.calls[3] == (*prefix, 'upload', release.VERSION, out/release.ASSET, out/release.SIGNATURE, out/release.GUIDE, '--clobber')
    assert gh.calls[4][:6] == (*prefix, 'download', release.VERSION) and gh.calls[4][6] == '--dir' and len(gh.calls) == 5
    assert (out/'release-notes.md').read_bytes() == b'notes\n'  # sent as packaged
    assert capsys.readouterr().out == f'Verified draft: https://github.com/{release.REPO}/releases/tag/{release.VERSION}\n'


def test_upload_sends_the_packaged_notes(repo, monkeypatch, tmp_path):
    # The notes are the changelog's at packaging: a newer entry, or an edit, before upload changes nothing sent.
    Packaging(monkeypatch)
    out = tmp_path/'out'
    release.package(pathlib.Path('stock.zip'), out)
    release.sign(out, key=KEY)
    body = (out/'release-notes.md').read_text()
    (repo/'docs/changelog.md').write_text(f'- **9.9.9**: Newer.\n- **{release.VERSION}**: Edited.\n')
    monkeypatch.setattr(release, 'source_sha256', lambda: 'source')
    gh = Gh(monkeypatch, out)
    release.upload(out)
    assert gh.calls[2][-2:] == ('--notes-file', out/'release-notes.md')
    assert (out/'release-notes.md').read_text() == body and '- One.' in body


def test_upload_publish(repo, monkeypatch, capsys):
    monkeypatch.setattr(release, 'BETA', 0)
    out = packaged(repo)
    gh = Gh(monkeypatch, out)
    release.upload(out, publish=True)
    assert gh.calls[-1] == ('gh', '--repo', release.REPO, 'release', 'edit', release.VERSION, '--draft=false')
    assert capsys.readouterr().out.startswith('Published: https://github.com/')


def test_upload_never_publishes_a_beta(repo, monkeypatch):
    monkeypatch.setattr(release, 'BETA', 2)
    out = packaged(repo)
    gh = Gh(monkeypatch, out)
    with pytest.raises(ValueError, match='^A beta is a pre-release'):
        release.upload(out, publish=True)
    assert not gh.calls


def test_upload_updates_a_draft(repo, monkeypatch):
    out = packaged(repo)
    gh = Gh(monkeypatch, out, json.dumps([{'tagName': 'old', 'isDraft': False}, {'tagName': release.VERSION, 'isDraft': True}]))
    release.upload(out)
    assert gh.calls[2][4] == 'edit'


@pytest.mark.parametrize('tag', ['rev\trefs/tags/{v}\n', 'object\trefs/tags/{v}\nrev\trefs/tags/{v}^{{}}\n'])
def test_upload_takes_a_tag_on_its_own_commit(repo, monkeypatch, tag):
    out = packaged(repo)
    gh = Gh(monkeypatch, out, tag=tag.format(v=release.VERSION))
    release.upload(out)
    assert gh.calls[2][4] == 'create'


@pytest.mark.parametrize('tag', ['beta\trefs/tags/{v}\n', 'rev\trefs/tags/{v}\nbeta\trefs/tags/{v}^{{}}\n'])
def test_upload_refuses_a_tag_left_on_another_commit(repo, monkeypatch, tag):
    # GitHub would keep the tag, a deleted pre-release's, and ignore --target
    out = packaged(repo)
    gh = Gh(monkeypatch, out, tag=tag.format(v=release.VERSION))
    with pytest.raises(ValueError, match=f'^Tag {release.VERSION} is on another commit: gh release delete'):
        release.upload(out)
    assert len(gh.calls) == 2


def test_upload_never_overwrites_a_published_release(repo, monkeypatch):
    out = packaged(repo)
    gh = Gh(monkeypatch, out, json.dumps([{'tagName': release.VERSION, 'isDraft': False}]))
    with pytest.raises(ValueError, match='^Refusing to overwrite a published release$'):
        release.upload(out)
    assert len(gh.calls) == 1


@pytest.mark.parametrize('name', [release.ASSET, release.SIGNATURE, release.GUIDE])
def test_upload_checks_what_landed(repo, monkeypatch, name):
    monkeypatch.setattr(release, 'BETA', 0)
    out = packaged(repo)
    gh = Gh(monkeypatch, out, corrupt=name)
    with pytest.raises(ValueError, match=f'^Remote asset mismatch: {name}$'):
        release.upload(out, publish=True)
    assert not any('--draft=false' in c for c in gh.calls)


@pytest.mark.parametrize('change,message', [
    (dict(tag='0.0.0'), '^Wrong release source/tag$'),
    (dict(source_sha256='other'), '^Wrong release source/tag$'),
    (dict(assets={release.ASSET: 'x', 'extra.zip': 'y'}), '^The release is update.tar alone$'),
    (dict(assets={release.ASSET: 'x'}), '^Release update mismatch$'),
    (dict(guide_sha256='x'), '^User guide mismatch$'),
    (dict(notes_sha256='x'), '^Release notes mismatch, '),
])
def test_upload_checks_the_record_first(repo, monkeypatch, change, message):
    out = packaged(repo)
    record = json.loads((out/'release.json').read_text())
    (out/'release.json').write_text(json.dumps({**record, **change}))
    gh = Gh(monkeypatch, out)
    with pytest.raises(ValueError, match=message):
        release.upload(out)
    assert not gh.calls


def test_upload_of_a_record_without_notes_hash_says_to_package_again(repo, monkeypatch):
    out = packaged(repo)
    record = json.loads((out/'release.json').read_text())
    del record['notes_sha256']
    (out/'release.json').write_text(json.dumps(record))
    gh = Gh(monkeypatch, out)
    with pytest.raises(ValueError, match='package again'):
        release.upload(out)
    assert not gh.calls


def test_upload_wants_the_build_tree_unchanged(repo, monkeypatch):
    out = packaged(repo)
    (out/'build/update.tar').write_bytes(b'other')
    gh = Gh(monkeypatch, out)
    with pytest.raises(ValueError, match='^Release update mismatch$'):
        release.upload(out)
    assert not gh.calls


def test_upload_wants_matching_checksums(repo, monkeypatch):
    out = packaged(repo)
    (out/'SHA256SUMS').write_text('wrong\n')
    gh = Gh(monkeypatch, out)
    with pytest.raises(ValueError, match='^Release checksums mismatch$'):
        release.upload(out)
    assert not gh.calls


def test_sign_writes_a_signature_that_checks_and_records_it(repo):
    out = packaged(repo, signed=False)
    release.sign(out, key=KEY)
    sig = (out/release.SIGNATURE).read_bytes()
    assert len(sig) == 64 and signing.verify(signing.update_message(release.VERSION, out/release.ASSET), sig)
    assert json.loads((out/'release.json').read_text())['signature_sha256'] == sha(sig)


def test_sign_takes_the_brokers_base64(repo):
    out = packaged(repo, signed=False)
    sig = signing.sign(signing.update_message(release.VERSION, out/release.ASSET), KEY)
    release.sign(out, base64.b64encode(sig).decode() + '\n')
    assert (out/release.SIGNATURE).read_bytes() == sig


def other_key(tmp_path):
    pem = tmp_path/'other.pem'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'ed25519', '-out', str(pem)], check=True, capture_output=True)
    return pem.read_text()


def test_sign_refuses_a_signature_that_doesnt_check(repo, tmp_path):
    out = packaged(repo, signed=False)
    message = signing.update_message(release.VERSION, out/release.ASSET)
    for sig in (signing.sign(message, other_key(tmp_path)), signing.sign(message.replace(b'-v1 ', b'-v1 0'), KEY), bytes(64)):
        with pytest.raises(ValueError, match='^That signature does not check against assets/signing/q2-coda-release.pub$'):
            release.sign(out, base64.b64encode(sig).decode())
    assert not (out/release.SIGNATURE).exists()


def test_sign_and_digest_want_the_packaged_update(repo):
    out = packaged(repo, signed=False)
    (out/release.ASSET).write_bytes(b'changed')
    with pytest.raises(ValueError, match='^Wrong release tag or update$'):
        release.sign(out, key=KEY)
    with pytest.raises(ValueError, match='^Wrong release tag or update$'):
        release.digest(out)


def test_digest_is_the_brokers_command_for_the_update(repo):
    out = packaged(repo, signed=False)
    assert release.digest(out, 'K') == signing.broker_command(signing.update_message(release.VERSION, out/release.ASSET), 'K')


@pytest.mark.parametrize('how', ['missing', 'forged', 'unrecorded'])
def test_upload_refuses_an_unsigned_release(repo, monkeypatch, tmp_path, how):
    out = packaged(repo, signed=how != 'missing')
    if how == 'forged':
        (out/release.SIGNATURE).write_bytes(signing.sign(signing.update_message(release.VERSION, out/release.ASSET), other_key(tmp_path)))
        record = json.loads((out/'release.json').read_text())
        record['signature_sha256'] = sha((out/release.SIGNATURE).read_bytes())
        (out/'release.json').write_text(json.dumps(record))
    if how == 'unrecorded':
        (out/release.SIGNATURE).write_bytes(bytes(64))
    gh = Gh(monkeypatch, out)
    with pytest.raises(ValueError, match='^No update.tar.sig that checks against'):
        release.upload(out)
    assert not gh.calls


def test_notes_one_bullet_a_sentence(repo):
    (repo/'docs/changelog.md').write_text(f'# Changelog\n\n- **{release.VERSION}**: Covers load. **Coverflow** opens faster! '
                                          '"Wake" works? iPod mode stays on, e.g. after a restart, as in 1.6.3. Done.\n'
                                          '- **1.0.0**: Old.\n')
    assert release.notes() == (f'**{release.VERSION}**\n- Covers load.\n- **Coverflow** opens faster!\n- "Wake" works?\n'
                               '- iPod mode stays on, e.g. after a restart, as in 1.6.3.\n- Done.')


def test_notes_needs_this_versions_entry(repo):
    (repo/'docs/changelog.md').write_text('- **1.0.0**: Old.\n')
    with pytest.raises(ValueError, match='^' + re.escape(f'docs/changelog.md has no - **{release.VERSION}** entry') + '$'):
        release.notes()


def test_release_body(repo):
    assert release.release_body(release.notes(), {release.ASSET: 'abc'}) == (
        f'**{release.VERSION}**\n- One.\n\nSHA-256:\n- update.tar ({release.TAG}): `abc`\n')
