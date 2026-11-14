"""tools/scan.py with no scanner, no uvx and no network: run() and tool() are stand-ins that write each tool's
JSON report, downloads come from a fake urlopen into a cache under tmp_path, and strings and unsquashfs are
fakes too."""
import hashlib
import io
import json
import pathlib
import subprocess
import tarfile
import types

import pytest

import scan


class Run:
    """Stands in for scan.run(): records each command and writes the report its --output or --file names."""
    def __init__(self, reports=None, stdout=''):
        self.calls, self.reports, self.stdout = [], reports or {}, stdout

    def __call__(self, cmd, ok=(0,), **kw):
        self.calls.append((cmd, ok, kw))
        for flag in ('--output', '--report-path', '--file'):
            if flag in cmd:
                path = cmd[cmd.index(flag) + 1]
                reply = self.reports[path.rsplit('/', 1)[-1]]
                pathlib.Path(path).write_text(reply if isinstance(reply, str) else json.dumps(reply))
        for arg in cmd:
            if arg.startswith('syft-json='):
                pathlib.Path(arg[len('syft-json='):]).write_text(json.dumps(self.reports['sbom.syft.json']))
        return types.SimpleNamespace(stdout=self.stdout)


@pytest.fixture
def run(monkeypatch):
    def install(reports=None, stdout=''):
        fake = Run(reports, stdout)
        monkeypatch.setattr(scan, 'run', fake)
        return fake
    return install


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setattr(scan, 'tool', lambda name: f'/cache/{name}')


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, 'CACHE', tmp_path/'cache')
    return tmp_path/'cache'


@pytest.fixture
def fetch(monkeypatch):
    """A fake urlopen serving `data`; returns the list of (url, timeout) it was asked for."""
    asked = []
    def install(data):
        def urlopen(url, timeout):
            asked.append((url, timeout))
            return io.BytesIO(data)
        monkeypatch.setattr(scan.urllib.request, 'urlopen', urlopen)
        return asked
    return install


def tar_gz(**members):
    """A .tar.gz with a regular file per keyword (bytes) or a directory (None)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            if body is None:
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            else:
                info.size = len(body)
                tf.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def pin(monkeypatch, name, data, member):
    monkeypatch.setitem(scan.TOOLS, name, ('me/thing', 'v1.2', 'thing.bin', hashlib.sha256(data).hexdigest(), member))


# tool()

def test_a_cached_tool_is_not_fetched_again(cache, monkeypatch):
    exe = cache/'gitleaks-v8.30.1'/'gitleaks'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'x')
    monkeypatch.setattr(scan.urllib.request, 'urlopen', None)
    assert scan.tool('gitleaks') == str(exe)


def test_a_bare_binary_is_fetched_checked_and_made_executable(cache, fetch, monkeypatch, capsys):
    pin(monkeypatch, 'thing', b'\x7fELF thing', None)
    asked = fetch(b'\x7fELF thing')
    exe = cache/'thing-v1.2'/'thing'
    assert scan.tool('thing') == str(exe)
    assert asked == [('https://github.com/me/thing/releases/download/v1.2/thing.bin', 120)]
    assert exe.read_bytes() == b'\x7fELF thing' and exe.stat().st_mode & 0o777 == 0o755
    assert capsys.readouterr().err == 'fetching thing v1.2\n'
    assert sorted(p.name for p in exe.parent.iterdir()) == ['thing']


def test_a_tarball_gives_its_member_alone(cache, fetch, monkeypatch):
    data = tar_gz(README=b'readme', thing=b'\x7fELF inside')
    pin(monkeypatch, 'thing', data, 'thing')
    fetch(data)
    exe = cache/'thing-v1.2'/'thing'
    assert scan.tool('thing') == str(exe)
    assert exe.read_bytes() == b'\x7fELF inside' and exe.stat().st_mode & 0o777 == 0o755
    assert sorted(p.name for p in exe.parent.iterdir()) == ['thing']  # the downloaded tarball is gone


def test_a_changed_download_is_not_run(cache, fetch, monkeypatch):
    pin(monkeypatch, 'thing', b'the pinned one', None)
    fetch(b'something else')
    got = hashlib.sha256(b'something else').hexdigest()
    with pytest.raises(SystemExit) as e:
        scan.tool('thing')
    assert e.value.code == (f'thing: thing.bin has SHA-256 {got}, expected '
                            f'{hashlib.sha256(b"the pinned one").hexdigest()}; not running it')
    assert not (cache/'thing-v1.2'/'thing').exists()


def test_a_member_that_is_no_regular_file_is_refused(cache, fetch, monkeypatch):
    data = tar_gz(thing=None)
    pin(monkeypatch, 'thing', data, 'thing')
    fetch(data)
    with pytest.raises(SystemExit) as e:
        scan.tool('thing')
    assert e.value.code == 'thing: thing in thing.bin is not a regular file'
    assert not (cache/'thing-v1.2'/'thing').exists()


@pytest.mark.parametrize('name,url', [
    ('gitleaks', 'https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz'),
    ('osv-scanner', 'https://github.com/google/osv-scanner/releases/download/v2.7.0/osv-scanner_linux_amd64'),
    ('syft', 'https://github.com/anchore/syft/releases/download/v1.55.0/syft_1.55.0_linux_amd64.tar.gz'),
    ('grype', 'https://github.com/anchore/grype/releases/download/v0.121.0/grype_0.121.0_linux_amd64.tar.gz'),
])
def test_each_scanner_comes_from_its_pinned_release(cache, fetch, name, url):
    asked = fetch(b'not the release')
    with pytest.raises(SystemExit, match=f'expected {scan.TOOLS[name][3]}; not running it'):
        scan.tool(name)
    assert asked == [(url, 120)]


# run()

def test_run_runs_in_the_repository_and_returns_the_process(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 1, 'out', 'err')
    monkeypatch.setattr(scan.subprocess, 'run', fake)
    p = scan.run(['tool', 'x'], ok=(0, 1), env={'A': '1'})
    assert p.stdout == 'out'
    assert calls == [(['tool', 'x'], {'cwd': scan.ROOT, 'capture_output': True, 'text': True, 'env': {'A': '1'}})]


def test_a_status_outside_ok_stops_with_the_end_of_stderr(monkeypatch):
    monkeypatch.setattr(scan.subprocess, 'run', lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, '', 'x' * 5 + 'y' * 2000))
    with pytest.raises(SystemExit) as e:
        scan.run(['/cache/tool', 'x'])
    assert e.value.code == '/cache/tool failed (2):\n' + 'y' * 2000
    monkeypatch.setattr(scan.subprocess, 'run', lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, '', ''))
    scan.run(['tool'])  # 0 is ok by default


# scan_code()

def finding(severity, rule, path, line):
    return {'extra': {'severity': severity}, 'check_id': f'q2pod.rules.{rule}', 'path': path, 'start': {'line': line}}


def test_code_runs_semgrep_and_fails_on_an_error(tmp_path, run, monkeypatch):
    monkeypatch.setattr(scan.shutil, 'which', {'uvx': '/bin/uvx'}.get)
    fake = run({'semgrep.json': {'errors': [{}, {}], 'results': [
        finding('INFO', 'note', 'tools/z.py', 1),
        finding('WARNING', 'warn', 'tools/b.py', 1),
        finding('ERROR', 'err', 'patch/c.c', 5),
        finding('WARNING', 'warn', 'tools/a.py', 9),
        finding('ERROR', 'err', 'patch/c.c', 2),
        finding('WARNING', 'warn', 'tools/a.py', 3),
    ]}})
    lines, fail = scan.scan_code(tmp_path)
    assert fake.calls == [(['/bin/uvx', '--quiet', 'semgrep==1.183.0', 'scan', '--metrics=off', '--disable-version-check',
                            '--quiet', '--config', '.semgrep/q2pod.yml', '--config', 'p/c', '--config', 'p/python',
                            '--json', '--output', str(tmp_path/'semgrep.json'), 'patch', 'tools'], (0, 1), {})]
    # Errors first however few, then by count; the list by severity, path and line, without INFO.
    assert lines == [
        '6 findings, 2 files Semgrep could not fully parse.', '',
        '| Severity | Rule | Count |', '|---|---|---|',
        '| ERROR | `err` | 2 |',
        '| WARNING | `warn` | 3 |',
        '| INFO | `note` | 1 |',
        '', 'Errors and warnings (info-level findings are in semgrep.json):', '',
        '- ERROR `err` patch/c.c:2',
        '- ERROR `err` patch/c.c:5',
        '- WARNING `warn` tools/a.py:3',
        '- WARNING `warn` tools/a.py:9',
        '- WARNING `warn` tools/b.py:1',
    ]
    assert fail is True


def test_code_warnings_alone_pass_and_the_list_stops_at_80(tmp_path, run, monkeypatch):
    monkeypatch.setattr(scan.shutil, 'which', {'uvx': '/bin/uvx'}.get)
    run({'semgrep.json': {'results': [finding('WARNING', 'warn', 'tools/a.py', n) for n in range(1, 82)]
                                     + [finding('INFO', 'note', 'tools/a.py', 1)]}})
    lines, fail = scan.scan_code(tmp_path)
    assert fail is False
    assert lines[0] == '82 findings, 0 files Semgrep could not fully parse.'
    assert lines[4:6] == ['| WARNING | `warn` | 81 |', '| INFO | `note` | 1 |']
    listed = [l for l in lines if l.startswith('- ')]
    assert listed == [f'- WARNING `warn` tools/a.py:{n}' for n in range(1, 81)] and lines[-1] == listed[-1]


def test_code_needs_uvx(tmp_path, run, monkeypatch):
    monkeypatch.setattr(scan.shutil, 'which', {}.get)
    fake = run()
    with pytest.raises(SystemExit) as e:
        scan.scan_code(tmp_path)
    assert e.value.code == 'code: needs uvx (https://docs.astral.sh/uv/) to run Semgrep' and fake.calls == []


# scan_secrets()

def leak(n):
    return {'RuleID': f'rule-{n}', 'File': f'f{n}.txt', 'StartLine': n, 'Commit': f'{n:02d}abcdef0123456789'}


def test_secrets_runs_gitleaks_over_the_history_and_any_leak_fails(tmp_path, run, tools):
    fake = run({'gitleaks.json': [leak(1), leak(2)]})
    lines, fail = scan.scan_secrets(tmp_path)
    assert fake.calls == [(['/cache/gitleaks', 'git', '--redact', '--no-banner', '--report-format', 'json',
                            '--report-path', str(tmp_path/'gitleaks.json'), '.'], (0, 1), {})]
    assert lines == ['2 possible secrets in the git history (values redacted).',
                     '- `rule-1` f1.txt:1 in 01abcdef', '- `rule-2` f2.txt:2 in 02abcdef']
    assert fail is True


def test_secrets_an_empty_report_is_clean(tmp_path, run, tools):
    run({'gitleaks.json': ''})
    assert scan.scan_secrets(tmp_path) == (['0 possible secrets in the git history (values redacted).'], False)


def test_secrets_lists_the_first_50(tmp_path, run, tools):
    run({'gitleaks.json': [leak(n) for n in range(60)]})
    lines, fail = scan.scan_secrets(tmp_path)
    assert lines[0].startswith('60 possible') and len(lines) == 51 and lines[-1] == '- `rule-49` f49.txt:49 in 49abcdef'


# scan_deps()

def test_deps_scans_each_requirements_file_but_a_venvs(tmp_path, run, tools, monkeypatch):
    monkeypatch.setattr(scan, 'ROOT', tmp_path)
    for f in ('requirements.txt', 'test/unit/requirements.txt', 'sub/requirements-dev.txt', '.venv/x/requirements.txt',
              'notes.txt'):
        (tmp_path/f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/f).write_text('')
    out = tmp_path/'out'
    out.mkdir()
    fake = run({'osv.json': {'results': [
        {'source': {'path': '/r/requirements.txt'}, 'packages': [
            {'package': {'name': 'pillow', 'version': '9.0'}, 'vulnerabilities': [{'id': 'GHSA-1'}, {'id': 'PYSEC-2'}]},
            {'package': {'name': 'clean', 'version': '1.0'}},
        ]},
        {'source': {'path': '/r/sub/requirements-dev.txt'}},
    ]}})
    lines, fail = scan.scan_deps(out)
    assert fake.calls == [(['/cache/osv-scanner', 'scan', 'source', '--format', 'json', '--output', str(out/'osv.json'),
                            '-L', 'requirements.txt', '-L', 'sub/requirements-dev.txt', '-L', 'test/unit/requirements.txt'],
                           (0, 1, 128), {})]
    assert lines == ['Scanned requirements.txt, sub/requirements-dev.txt, test/unit/requirements.txt: 2 known vulnerabilities.',
                     '- pillow 9.0: GHSA-1 (/r/requirements.txt)', '- pillow 9.0: PYSEC-2 (/r/requirements.txt)']
    assert fail is True


def test_deps_with_nothing_to_scan_pass(tmp_path, run, tools, monkeypatch):
    monkeypatch.setattr(scan, 'ROOT', tmp_path)
    fake = run({'osv.json': ''})
    assert scan.scan_deps(tmp_path) == (['Scanned no requirements files: 0 known vulnerabilities.'], False)
    assert fake.calls[0][0] == ['/cache/osv-scanner', 'scan', 'source', '--format', 'json', '--output', str(tmp_path/'osv.json')]


def test_deps_without_vulnerabilities_pass(tmp_path, run, tools, monkeypatch):
    monkeypatch.setattr(scan, 'ROOT', tmp_path)
    run({'osv.json': {'results': [{'source': {'path': 'x'}, 'packages': [{'package': {'name': 'a', 'version': '1'},
                                                                          'vulnerabilities': []}]}]}})
    assert scan.scan_deps(tmp_path)[1] is False


# scan_firmware()

def match(name, version, severity, vid):
    return {'artifact': {'name': name, 'version': version}, 'vulnerability': {'severity': severity, 'id': vid}}


@pytest.fixture
def unsquash(tmp_path, monkeypatch):
    """A build with a rootfs.squashfs, and a fake unsquashfs that unpacks nothing; returns its calls."""
    (tmp_path/'build').mkdir()
    (tmp_path/'build'/'rootfs.squashfs').write_bytes(b'hsqs')
    calls = []
    def fake(cmd, **kw):
        assert not (tmp_path/'out'/'rootfs').exists()  # a previous scan's rootfs goes first
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(scan.subprocess, 'run', fake)
    (tmp_path/'out'/'rootfs'/'old').mkdir(parents=True)
    return calls


def test_firmware_without_a_build_is_skipped(tmp_path, run, tools):
    fake = run()
    assert scan.scan_firmware(tmp_path, str(tmp_path/'none')) == (
        [f'Skipped: no {tmp_path/"none"/"rootfs.squashfs"}. Pass --build with a build.py output directory.'], False)
    assert fake.calls == []


def test_firmware_checks_syfts_components_and_the_probed_ones(tmp_path, run, tools, unsquash, monkeypatch):
    out, sq = tmp_path/'out', tmp_path/'build'/'rootfs.squashfs'
    fs = out/'rootfs'
    probed = []
    found = [('openssl', 'openssl:openssl', '1.1.1k'), ('zlib', 'zlib:zlib', '1.2.11')]
    monkeypatch.setattr(scan, 'probe', lambda d: probed.append(d) or (found, ['curl', 'bluez']))
    fake = run({
        'sbom.syft.json': {'artifacts': [{'name': 'busybox', 'version': '1.0'}]},
        'grype.json': {'matches': [match('busybox', '1.0', 'High', f'CVE-B{n}') for n in range(1, 7)]
                       + [match('busybox', '1.0', 'Medium', 'CVE-BM'), match('busybox', '1.0', 'Low', 'CVE-BL')]},
        'grype.probed.json': {'matches': [match('openssl', '1.1.1k', 'Critical', f'CVE-O{n}') for n in range(1, 4)]
                              + [match('openssl', '1.1.1k', 'High', f'CVE-O{n}') for n in range(4, 8)]
                              + [match('zlib', '1.2.11', 'Critical', 'CVE-Z1')] * 2},
    })
    lines, fail = scan.scan_firmware(out, str(tmp_path/'build'))
    assert unsquash == [(['unsquashfs', '-q', '-no-xattrs', '-d', str(fs), str(sq)], {'check': True, 'capture_output': True})]
    assert probed == [fs]
    sbom, cdx = out/'sbom.syft.json', out/'sbom.probed.cdx.json'
    assert fake.calls == [
        (['/cache/syft', 'scan', f'dir:{fs}', '-q', '-o', f'syft-json={sbom}'], (0,), {}),
        (['/cache/grype', f'sbom:{sbom}', '-q', '-o', 'json', '--file', str(out/'grype.json')], (0,), {}),
        (['/cache/grype', f'sbom:{cdx}', '-q', '-o', 'json', '--file', str(out/'grype.probed.json')], (0,), {}),
    ]
    assert json.loads(cdx.read_text()) == {'bomFormat': 'CycloneDX', 'specVersion': '1.5', 'version': 1, 'components': [
        {'type': 'library', 'name': 'openssl', 'version': '1.1.1k', 'cpe': 'cpe:2.3:a:openssl:openssl:1.1.1k:*:*:*:*:*:*:*'},
        {'type': 'library', 'name': 'zlib', 'version': '1.2.11', 'cpe': 'cpe:2.3:a:zlib:zlib:1.2.11:*:*:*:*:*:*:*'}]}
    assert lines == [
        f'{sq}: 3 components identified, 17 vulnerability matches (High 10, Critical 5, Medium 1, Low 1).',
        'Probed by version string: openssl 1.1.1k, zlib 1.2.11. Not found: curl, bluez.',
        'A component neither syft nor the probes can name is not checked, so a clean result is not proof.', '',
        '| Component | Version | Critical and High |', '|---|---|---|',
        '| openssl | 1.1.1k | 7: CVE-O1, CVE-O2, CVE-O3, CVE-O4, CVE-O5, CVE-O6 ... |',
        '| busybox | 1.0 | 6: CVE-B1, CVE-B2, CVE-B3, CVE-B4, CVE-B5, CVE-B6 |',
        '| zlib | 1.2.11 | 1: CVE-Z1 |',
    ]
    assert fail is True


def test_firmware_high_alone_passes_and_nothing_probed_runs_no_second_grype(tmp_path, run, tools, unsquash, monkeypatch):
    out = tmp_path/'out'
    monkeypatch.setattr(scan, 'probe', lambda d: ([], []))
    fake = run({'sbom.syft.json': {}, 'grype.json': {'matches': [match('busybox', '1.0', 'High', 'CVE-1')]}})
    lines, fail = scan.scan_firmware(out, str(tmp_path/'build'))
    assert fail is False and len(fake.calls) == 2 and not (out/'sbom.probed.cdx.json').exists()
    assert lines[:2] == [f'{tmp_path/"build"/"rootfs.squashfs"}: 0 components identified, 1 vulnerability matches (High 1).',
                         'Probed by version string: none.']
    assert lines[-1] == '| busybox | 1.0 | 1: CVE-1 |'


def test_firmware_with_no_matches_and_no_earlier_rootfs(tmp_path, run, tools, unsquash, monkeypatch):
    (tmp_path/'out'/'rootfs'/'old').rmdir()
    (tmp_path/'out'/'rootfs').rmdir()
    monkeypatch.setattr(scan, 'probe', lambda d: ([], ['curl']))
    run({'sbom.syft.json': {}, 'grype.json': {}})
    lines, fail = scan.scan_firmware(tmp_path/'out', str(tmp_path/'build'))
    assert fail is False and lines[0].endswith(': 0 components identified, 0 vulnerability matches ().')
    assert lines[1] == 'Probed by version string: none. Not found: curl.' and lines[-1] == '|---|---|---|'


# probe()

STRINGS = {
    'usr/lib/libcrypto.so.1.1': 'OpenSSL 1.1.1k  25 Mar 2021\n',
    'usr/lib/libcurl.so.4': 'CLIENT libcurl/7.88.1 OpenSSL\n',
    'usr/bin/bluetoothd': 'bluetoothd\n5.50\nver 5.50x\n',
    'usr/sbin/wpa_supplicant': 'wpa_supplicant v2.10\n',
    'usr/bin/thttpd': 'thttpd/2.29 23May2018\n',
    'usr/lib/libsqlite3.so.0': '3.40.1\n',
    'lib/libz.so.1': ' deflate 1.2.11 Copyright 1995-2017 Jean-loup Gailly and Mark Adler \n',
    'usr/lib/libpng12.so.0': 'libpng\n1.2.59\n',
    'lib/libc.so.6': 'GNU C Library (Buildroot) release version 2.28.\n',
    'usr/lib/libdbus-1.so.3': 'libdbus 1.0 dbus 1.12.20\n',
}


@pytest.fixture
def strings(tmp_path, monkeypatch):
    """A rootfs with `files` ({path: its strings}) and a fake strings; returns the paths it read."""
    read = []
    def install(files):
        for f in files:
            (tmp_path/f).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path/f).write_bytes(b'')
        def fake(cmd, **kw):
            assert cmd[:4] == ['strings', '-a', '-n', '4'] and kw == {'capture_output': True, 'text': True}
            read.append(cmd[4])
            return subprocess.CompletedProcess(cmd, 0, files[str(pathlib.Path(cmd[4]).relative_to(tmp_path))], '')
        monkeypatch.setattr(scan.subprocess, 'run', fake)
        return read
    return install


def test_probe_finds_each_component_by_its_version_string(tmp_path, strings):
    strings(STRINGS)
    assert scan.probe(tmp_path) == ([
        ('openssl', 'openssl:openssl', '1.1.1k'), ('curl', 'haxx:libcurl', '7.88.1'), ('bluez', 'bluez:bluez', '5.50'),
        ('wpa_supplicant', 'w1.fi:wpa_supplicant', '2.10'), ('thttpd', 'acme:thttpd', '2.29'),
        ('sqlite', 'sqlite:sqlite', '3.40.1'), ('zlib', 'zlib:zlib', '1.2.11'), ('libpng', 'libpng:libpng', '1.2.59'),
        ('glibc', 'gnu:glibc', '2.28'), ('dbus', 'freedesktop:dbus', '1.12.20'),
    ], [])


def test_probe_names_what_it_cannot_find(tmp_path, strings):
    read = strings({'usr/bin/bluetoothd': 'version 5.50 here\n', 'usr/lib/libz.so.1': 'no version\n'})
    assert scan.probe(tmp_path) == ([], [name for name, *_ in scan.PROBES])
    assert read == [str(tmp_path/'usr/bin/bluetoothd'), str(tmp_path/'usr/lib/libz.so.1')]  # what is absent is not read


def test_probe_tries_the_next_file_and_stops_at_a_match(tmp_path, strings):
    zlib = ' deflate 1.2.13 Copyright\n'
    read = strings({'usr/lib/libz.so.1': 'nothing\n', 'lib/libz.so.1': zlib})
    assert scan.probe(tmp_path)[0] == [('zlib', 'zlib:zlib', '1.2.13')]
    read = strings({'usr/lib/libz.so.1': zlib, 'lib/libz.so.1': zlib})
    read.clear()
    assert scan.probe(tmp_path)[0] == [('zlib', 'zlib:zlib', '1.2.13')]
    assert read == [str(tmp_path/'usr/lib/libz.so.1')]


# main()

@pytest.fixture
def scans(monkeypatch):
    """Each scan_* a stand-in returning one line and whether it fails, as `fails` names; returns their calls."""
    calls = []
    def install(*fails):
        for name in scan.SCANS:
            def fake(*args, name=name):
                calls.append((name, args))
                return [f'{name} line'], name in fails
            monkeypatch.setattr(scan, f'scan_{name}', fake)
        return calls
    return install


def test_main_runs_every_scan_and_writes_the_summary(tmp_path, run, scans, monkeypatch, capsys):
    monkeypatch.setattr(scan, 'ROOT', tmp_path)
    fake = run(stdout='abc1234\n')
    calls = scans('secrets', 'firmware')
    assert scan.main([]) == 1
    out = tmp_path/'scan-out'
    assert fake.calls == [(['git', 'rev-parse', '--short', 'HEAD'], (0,), {})]
    assert calls == [('code', (out,)), ('secrets', (out,)), ('deps', (out,)), ('firmware', (out, '/tmp/q2-dev'))]
    assert (out/'SUMMARY.md').read_text() == '\n'.join([
        '# Security scan of abc1234', '',
        '## code', '', 'code line', '',
        '## secrets (fails)', '', 'secrets line', '',
        '## deps', '', 'deps line', '',
        '## firmware (fails)', '', 'firmware line', ''])
    std = capsys.readouterr()
    assert std.out == f'{out/"SUMMARY.md"}: failing: secrets, firmware\n'
    assert std.err == 'scanning: code\nscanning: secrets\nscanning: deps\nscanning: firmware\n'


def test_main_only_some_scans_in_the_order_given(tmp_path, run, scans, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    run(stdout='abc1234\n')
    calls = scans()
    assert scan.main(['--only', ' firmware , deps,,', '--build', 'b', '--out', 'o/x']) == 0
    out = tmp_path/'o'/'x'
    assert calls == [('firmware', (out, 'b')), ('deps', (out,))]
    assert capsys.readouterr().out == f'{out/"SUMMARY.md"}: nothing at a failing level\n'
    assert (out/'SUMMARY.md').read_text().startswith('# Security scan of abc1234\n\n## firmware\n')


def test_main_refuses_an_unknown_scan(tmp_path, run, scans, capsys):
    fake = run()
    calls = scans()
    with pytest.raises(SystemExit) as e:
        scan.main(['--only', 'code,bogus,nope', '--out', str(tmp_path/'o')])
    assert e.value.code == 2 and fake.calls == [] and calls == [] and not (tmp_path/'o').exists()
    assert 'error: unknown scan bogus, nope; choose from code, secrets, deps, firmware' in capsys.readouterr().err
