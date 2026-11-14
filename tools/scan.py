#!/usr/bin/env python3
"""Local security scan: the code, the repository's history, the Python dependencies and a build's firmware.

usage: python3 tools/scan.py [--only NAMES] [--build DIR] [--out DIR]

  code      Semgrep: the project's rules (.semgrep/q2pod.yml) and Semgrep's C and Python packs, over patch/ and tools/
  secrets   gitleaks over the whole git history
  deps      osv-scanner over the requirements files, against the OSV vulnerability database
  firmware  syft lists the software in a build's root filesystem (an SBOM); grype checks it for known vulnerabilities

--only takes a comma-separated subset (default: all four). --build is a build.py output directory (default
/tmp/q2-dev); its rootfs.squashfs is unpacked under --out, which defaults to scan-out/ (git-ignored). Each tool
writes its raw JSON there and SUMMARY.md collects the counts and the findings worth reading. The exit status is
1 when a scan finds something at its failing level (any secret, any dependency vulnerability, a Semgrep ERROR,
a Critical firmware vulnerability), else 0.

The scanners are fetched on first use into ~/.cache/q2-scan, each a pinned release whose SHA-256 is checked
against the value below before it runs, so a changed upstream file stops the scan instead of running. Semgrep
runs through uvx at a pinned version. The tools and their databases need the network; nothing else does.
"""
import argparse
import re
import collections
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE = pathlib.Path(os.environ.get('Q2_SCAN_CACHE', pathlib.Path.home() / '.cache/q2-scan'))
SEMGREP = 'semgrep==1.183.0'
# name: (GitHub repo, tag, release asset, its SHA-256, the executable inside a .tar.gz or None for a bare binary)
TOOLS = {
    'gitleaks': ('gitleaks/gitleaks', 'v8.30.1', 'gitleaks_8.30.1_linux_x64.tar.gz',
                 '551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb', 'gitleaks'),
    'osv-scanner': ('google/osv-scanner', 'v2.7.0', 'osv-scanner_linux_amd64',
                    '160c6253e2922e9e0a6a9771c5c4c40a4b3c6090b34a3cdd5e80c96cc2e4218b', None),
    'syft': ('anchore/syft', 'v1.55.0', 'syft_1.55.0_linux_amd64.tar.gz',
             'bb65249542db5fedeeb4d3f9b018b75e7264dbfbbb10486f044581a92f90490d', 'syft'),
    'grype': ('anchore/grype', 'v0.121.0', 'grype_0.121.0_linux_amd64.tar.gz',
              '64e07133d3486115de647a66581461dccf5c732748a0a3119077395565f15519', 'grype'),
}
SCANS = ('code', 'secrets', 'deps', 'firmware')
# Components syft cannot name from a buildroot rootfs (no package database): each is found by a version string in
# its files and handed to grype by CPE. (name, CPE vendor:product, files, regex whose group 1 is the version)
PROBES = (
    ('openssl', 'openssl:openssl', ('usr/lib/libcrypto.so.1.1',), r'OpenSSL (\d+\.\d+\.\d+[a-z]*) '),
    ('curl', 'haxx:libcurl', ('usr/lib/libcurl.so.4',), r'libcurl/(\d+\.\d+\.\d+)'),
    ('bluez', 'bluez:bluez', ('usr/bin/bluetoothd',), r'^(5\.\d+)$'),
    ('wpa_supplicant', 'w1.fi:wpa_supplicant', ('usr/sbin/wpa_supplicant',), r'wpa_supplicant v(\d+\.\d+(?:\.\d+)?)'),
    ('thttpd', 'acme:thttpd', ('usr/bin/thttpd',), r'thttpd/(\d+\.\d+[a-z]?)'),
    ('sqlite', 'sqlite:sqlite', ('usr/lib/libsqlite3.so.0',), r'^(3\.\d+\.\d+)$'),
    ('zlib', 'zlib:zlib', ('usr/lib/libz.so.1', 'lib/libz.so.1'), r'deflate (\d+\.\d+\.\d+) Copyright'),
    ('libpng', 'libpng:libpng', ('usr/lib/libpng12.so.0',), r'^(1\.2\.\d+)$'),
    ('glibc', 'gnu:glibc', ('lib/libc.so.6',), r'release version (\d+\.\d+)\.'),
    ('dbus', 'freedesktop:dbus', ('usr/lib/libdbus-1.so.3',), r'\bdbus (\d+\.\d+\.\d+)'),
)


def tool(name):
    """The pinned executable, downloaded and checked on first use."""
    repo, tag, asset, sha, member = TOOLS[name]
    exe = CACHE / f'{name}-{tag}' / name
    if exe.exists():
        return str(exe)
    exe.parent.mkdir(parents=True, exist_ok=True)
    url = f'https://github.com/{repo}/releases/download/{tag}/{asset}'
    print(f'fetching {name} {tag}', file=sys.stderr)
    data = urllib.request.urlopen(url, timeout=120).read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        sys.exit(f'{name}: {asset} has SHA-256 {got}, expected {sha}; not running it')
    if member is None:
        exe.write_bytes(data)
    else:
        tmp = exe.parent / asset
        tmp.write_bytes(data)
        with tarfile.open(tmp) as tf:
            info = tf.getmember(member)
            if not info.isfile():
                sys.exit(f'{name}: {member} in {asset} is not a regular file')
            exe.write_bytes(tf.extractfile(info).read())
        tmp.unlink()
    exe.chmod(0o755)
    return str(exe)


def run(cmd, ok=(0,), **kw):
    """Run a scanner; a status outside `ok` is a failure of the tool, not a finding."""
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kw)
    if p.returncode not in ok:
        sys.exit(f'{cmd[0]} failed ({p.returncode}):\n{p.stderr[-2000:]}')
    return p


def scan_code(out):
    report = out / 'semgrep.json'
    uvx = shutil.which('uvx') or sys.exit('code: needs uvx (https://docs.astral.sh/uv/) to run Semgrep')
    run([uvx, '--quiet', SEMGREP, 'scan', '--metrics=off', '--disable-version-check', '--quiet',
         '--config', '.semgrep/q2pod.yml', '--config', 'p/c', '--config', 'p/python',
         '--json', '--output', str(report), 'patch', 'tools'], ok=(0, 1))
    data = json.loads(report.read_text())
    rows = sorted(((r['extra']['severity'], r['check_id'].split('.')[-1], r['path'], r['start']['line'])
                   for r in data['results']), key=lambda r: ({'ERROR': 0, 'WARNING': 1}.get(r[0], 2), r[2], r[3]))
    by_rule = collections.Counter((r[0], r[1]) for r in rows)
    lines = [f'{len(rows)} findings, {len(data.get("errors", []))} files Semgrep could not fully parse.', '',
             '| Severity | Rule | Count |', '|---|---|---|']
    lines += [f'| {s} | `{rule}` | {n} |' for (s, rule), n in sorted(by_rule.items(), key=lambda x: (x[0][0] != 'ERROR', -x[1]))]
    lines += ['', 'Errors and warnings (info-level findings are in semgrep.json):', '']
    lines += [f'- {s} `{rule}` {path}:{line}' for s, rule, path, line in rows if s in ('ERROR', 'WARNING')][:80]
    return lines, any(r[0] == 'ERROR' for r in rows)


def scan_secrets(out):
    report = out / 'gitleaks.json'
    p = run([tool('gitleaks'), 'git', '--redact', '--no-banner', '--report-format', 'json',
             '--report-path', str(report), '.'], ok=(0, 1))
    leaks = json.loads(report.read_text() or '[]')
    lines = [f'{len(leaks)} possible secrets in the git history (values redacted).']
    lines += [f'- `{l["RuleID"]}` {l["File"]}:{l["StartLine"]} in {l["Commit"][:8]}' for l in leaks[:50]]
    return lines, bool(leaks)


def scan_deps(out):
    report = out / 'osv.json'
    reqs = [str(p.relative_to(ROOT)) for p in sorted(ROOT.glob('**/requirements*.txt')) if '.venv' not in p.parts]
    cmd = [tool('osv-scanner'), 'scan', 'source', '--format', 'json', '--output', str(report)]
    for r in reqs:
        cmd += ['-L', r]
    run(cmd, ok=(0, 1, 128))  # 1: vulnerabilities found; 128: no packages found
    data = json.loads(report.read_text() or '{}')
    vulns = [(pkg['package']['name'], pkg['package']['version'], v['id'], res['source']['path'])
             for res in data.get('results', []) for pkg in res.get('packages', []) for v in pkg.get('vulnerabilities', [])]
    lines = [f'Scanned {", ".join(reqs) or "no requirements files"}: {len(vulns)} known vulnerabilities.']
    lines += [f'- {name} {ver}: {vid} ({src})' for name, ver, vid, src in vulns]
    return lines, bool(vulns)


def scan_firmware(out, build):
    sq = pathlib.Path(build) / 'rootfs.squashfs'
    if not sq.exists():
        return [f'Skipped: no {sq}. Pass --build with a build.py output directory.'], False
    fs = out / 'rootfs'
    if fs.exists():
        shutil.rmtree(fs)
    subprocess.run(['unsquashfs', '-q', '-no-xattrs', '-d', str(fs), str(sq)], check=True, capture_output=True)
    sbom = out / 'sbom.syft.json'
    run([tool('syft'), 'scan', f'dir:{fs}', '-q', '-o', f'syft-json={sbom}'])
    report = out / 'grype.json'
    run([tool('grype'), f'sbom:{sbom}', '-q', '-o', 'json', '--file', str(report)])
    pkgs = json.loads(sbom.read_text()).get('artifacts', [])
    matches = json.loads(report.read_text()).get('matches', [])
    found, missing = probe(fs)
    if found:  # the probed components, as a CycloneDX SBOM grype matches by CPE
        cdx = out / 'sbom.probed.cdx.json'
        cdx.write_text(json.dumps({'bomFormat': 'CycloneDX', 'specVersion': '1.5', 'version': 1, 'components': [
            {'type': 'library', 'name': n, 'version': v, 'cpe': f'cpe:2.3:a:{c}:{v}:*:*:*:*:*:*:*'} for n, c, v in found]}))
        probed = out / 'grype.probed.json'
        run([tool('grype'), f'sbom:{cdx}', '-q', '-o', 'json', '--file', str(probed)])
        matches += json.loads(probed.read_text()).get('matches', [])
        pkgs += [{'name': n, 'version': v} for n, _, v in found]
    sev = collections.Counter(m['vulnerability']['severity'] for m in matches)
    worst = collections.defaultdict(set)
    for m in matches:
        if m['vulnerability']['severity'] in ('Critical', 'High'):
            a = m['artifact']
            worst[(a['name'], a['version'])].add(m['vulnerability']['id'])
    lines = [f'{sq}: {len(pkgs)} components identified, {len(matches)} vulnerability matches '
             f'({", ".join(f"{k} {v}" for k, v in sev.most_common())}).',
             f'Probed by version string: {", ".join(f"{n} {v}" for n, _, v in found) or "none"}.'
             + (f' Not found: {", ".join(missing)}.' if missing else ''),
             'A component neither syft nor the probes can name is not checked, so a clean result is not proof.', '',
             '| Component | Version | Critical and High |', '|---|---|---|']
    lines += [f'| {n} | {v} | {len(ids)}: {", ".join(sorted(ids)[:6])}{" ..." if len(ids) > 6 else ""} |'
              for (n, v), ids in sorted(worst.items(), key=lambda x: -len(x[1]))]
    return lines, sev.get('Critical', 0) > 0


def probe(fs):
    """PROBES' components present in the unpacked rootfs: ([(name, cpe vendor:product, version)], [names not found])."""
    found, missing = [], []
    for name, cpe, files, pattern in PROBES:
        version = None
        for f in files:
            path = fs / f
            if path.exists():
                text = subprocess.run(['strings', '-a', '-n', '4', str(path)], capture_output=True, text=True).stdout
                m = re.search(pattern, text, re.M)
                if m:
                    version = m.group(1)
                    break
        (found.append((name, cpe, version)) if version else missing.append(name))
    return found, missing


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--only', default=','.join(SCANS))
    ap.add_argument('--build', default='/tmp/q2-dev')
    ap.add_argument('--out', default=str(ROOT / 'scan-out'))
    a = ap.parse_args(argv)
    only = [s.strip() for s in a.only.split(',') if s.strip()]
    bad = [s for s in only if s not in SCANS]
    if bad:
        ap.error(f'unknown scan {", ".join(bad)}; choose from {", ".join(SCANS)}')
    out = pathlib.Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    head = run(['git', 'rev-parse', '--short', 'HEAD']).stdout.strip()
    summary, failed = [f'# Security scan of {head}', ''], []
    for name in only:
        print(f'scanning: {name}', file=sys.stderr)
        lines, fail = (scan_firmware(out, a.build) if name == 'firmware' else globals()[f'scan_{name}'](out))
        summary += [f'## {name}{" (fails)" if fail else ""}', '', *lines, '']
        if fail:
            failed.append(name)
    (out / 'SUMMARY.md').write_text('\n'.join(summary))
    print(f'{out / "SUMMARY.md"}: {"failing: " + ", ".join(failed) if failed else "nothing at a failing level"}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
