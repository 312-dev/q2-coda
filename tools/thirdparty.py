#!/usr/bin/env python3
"""Rebuild the rootfs's outdated third-party libraries and programs from pinned upstream sources.

Shanling built OpenSSL, curl, zlib and the rest years before V1.36 shipped (docs/third-party.md). Each
component here is fetched from its upstream site, checked against the SHA-256 below, compiled with
the toolchain Shanling used (Codescape GNU Tools 2020.06-01 for MIPS MTI Linux, glibc 2.28, MIPS32r2
little-endian, o32, -mfp64, -mnan=2008) and installed under the stock file names, so every symlink
and every consumer's DT_NEEDED resolves to it. Results are cached under ~/.cache/q2-thirdparty,
keyed by the source hash, the recipe's text and the toolchain, so a rebuild only recompiles what
changed. `check_abi` refuses a replacement that drops a symbol any stock program imports.

    python3 tools/thirdparty.py build [NAME...]   # build (or reuse) components, print their outputs
    python3 tools/thirdparty.py check STOCKFS     # check_abi of every INSTALL entry against an unpacked stock rootfs
"""
import ast, hashlib, inspect, json, os, pathlib, re, shutil, subprocess, sys, tarfile, urllib.request

CACHE = pathlib.Path(os.environ.get('Q2_THIRDPARTY_CACHE') or pathlib.Path.home()/'.cache/q2-thirdparty')
JOBS = os.environ.get('Q2_THIRDPARTY_JOBS', '2')

# The toolchain stock was built with (its glibc is the same 2.28 build as the rootfs's). Its
# mipsel-r2-hard-nan2008 multilib is chosen by -EL -mnan=2008; -mfp64 gives stock's FP ABI.
TOOLCHAIN = ('https://codescape.mips.com/components/toolchain/2020.06-01/'
             'Codescape.GNU.Tools.Package.2020.06-01.for.MIPS.MTI.Linux.CentOS-6.x86_64.tar.gz',
             'da43736458a13de323d198a8d17316898e7bf15c2a19023d1d83b7afa7213b9f')
TRIPLE = 'mips-mti-linux-gnu'
ARCH = ['-EL', '-march=mips32r2', '-mabi=32', '-mhard-float', '-mfp64', '-mnan=2008', '-mabs=2008']
# The same bytes from the same sources: no build dates (SOURCE_DATE_EPOCH, the release's own date, is
# read by OpenSSL's buildinf.h and busybox's banner) and no build paths in debug info or __FILE__.
# OpenSSL's and FFmpeg's version strings still name the compiler's path, so two machines agree only
# with the cache at the same path (Q2_THIRDPARTY_CACHE).
REPRODUCIBLE = [f'-ffile-prefix-map={CACHE}=/q2-thirdparty']

# name: (version, url, sha256). Each upstream signs its releases; docs/third-party.md lists the keys
# the hashes were checked against.
SOURCES = {
    'openssl': ('1.1.1w', 'https://github.com/openssl/openssl/releases/download/OpenSSL_1_1_1w/openssl-1.1.1w.tar.gz',
                'cf3098950cb4d853ad95c0841f1f9c6d3dc102dccfcacd521d93925208b76ac8'),
}


def run(*cmd, cwd=None, env=None, log=None):
    """Run a build command, its output to log (or inherited); stop the build on failure."""
    out = open(log, 'a') if log else None
    try:
        subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, check=True, stdout=out, stderr=subprocess.STDOUT if out else None)
    except subprocess.CalledProcessError as e:
        tail = pathlib.Path(log).read_text(errors='replace')[-3000:] if log else ''
        sys.exit(f'thirdparty: {" ".join(map(str, cmd))} failed ({e.returncode})\n{tail}')
    finally:
        if out: out.close()


def need(value, message):
    """value, or stop with message when it is None."""
    if value is None:
        sys.exit(f'thirdparty: {message}')
    return value


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def fetch(url, digest, name):
    """The file at url, downloaded once into the cache and checked against digest."""
    path = CACHE/'dl'/name
    if path.exists() and sha256(path) == digest:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f'thirdparty: fetching {url}', file=sys.stderr)
    part = path.with_suffix(path.suffix + '.part')
    with urllib.request.urlopen(url, timeout=600) as r, open(part, 'wb') as f:
        shutil.copyfileobj(r, f, 1 << 20)
    got = sha256(part)
    if got != digest:
        part.unlink()
        sys.exit(f'thirdparty: {url} has SHA-256 {got}, expected {digest}')
    part.rename(path)
    return path


def toolchain():
    """The toolchain's bin directory, unpacked on first use."""
    root = CACHE/'toolchain'
    bindir = root/TRIPLE/'2020.06-01/bin'
    stamp = root/'.sha256'
    if not (stamp.exists() and stamp.read_text() == TOOLCHAIN[1]):
        archive = fetch(*TOOLCHAIN, 'mti-linux-2020.06-01.tar.gz')
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        run('tar', '-C', root, '-xzf', archive)
        stamp.write_text(TOOLCHAIN[1])
    return bindir


def unpack(name, work):
    """A fresh copy of name's source tree in work/src."""
    version, url, digest = SOURCES[name]
    archive = fetch(url, digest, url.rsplit('/', 1)[-1])
    src = work/'src'
    shutil.rmtree(src, ignore_errors=True)
    src.mkdir(parents=True)
    with tarfile.open(archive) as t:
        top = {m.name.split('/', 1)[0] for m in t.getmembers()}
        if len(top) != 1:
            sys.exit(f'thirdparty: {archive.name} has no single top directory')
        t.extractall(src, filter='data')
    return src/top.pop()


def epoch(name):
    """The newest modification time in name's source archive: its release date."""
    version, url, digest = SOURCES[name]
    with tarfile.open(fetch(url, digest, url.rsplit('/', 1)[-1])) as t:
        return max(m.mtime for m in t.getmembers())


class Env:
    """Cross-compilation settings for a recipe: tool paths and flags, staged dependencies."""
    def __init__(self, deps, epoch):
        bindir = toolchain()
        self.bin = bindir
        self.cc = f'{bindir}/{TRIPLE}-gcc ' + ' '.join(ARCH + REPRODUCIBLE)
        self.deps = deps
        inc = ' '.join(f'-I{d}/usr/include' for d in deps.values())
        lib = ' '.join(f'-L{d}/usr/lib -Wl,-rpath-link,{d}/usr/lib' for d in deps.values())
        self.vars = dict(os.environ, CC=self.cc, CPP=self.cc + ' -E', AR=f'{bindir}/{TRIPLE}-ar',
                         RANLIB=f'{bindir}/{TRIPLE}-ranlib', STRIP=f'{bindir}/{TRIPLE}-strip',
                         NM=f'{bindir}/{TRIPLE}-nm', LD=f'{bindir}/{TRIPLE}-ld',
                         CFLAGS='-O2 -pipe', CPPFLAGS=inc, LDFLAGS=lib, SOURCE_DATE_EPOCH=str(epoch), TZ='UTC',
                         PKG_CONFIG_LIBDIR=':'.join(f'{d}/usr/lib/pkgconfig' for d in deps.values()) or '/nonexistent',
                         PKG_CONFIG_SYSROOT_DIR='')
        self.vars.pop('PKG_CONFIG_PATH', None)
        self.host = 'mipsel-linux-gnu'


# Recipes: each builds one component from src and installs it under stage (a DESTDIR with prefix
# /usr). A recipe's source text is part of its cache key, so editing one rebuilds that component.

# Parts of OpenSSL no program in the rootfs calls (check_abi proves it: each takes its exports with
# it), and where most advisories since 1.1.1w's end of life are: binary-field curves, SM2, DTLS, CMS,
# time stamping, certificate transparency. (Stock libcurl imports SRP, so it stays.)
OPENSSL_OMIT = ['no-ec2m', 'no-sm2', 'no-dtls', 'no-cms', 'no-ts', 'no-ct']


def build_openssl(src, stage, env, log):
    # Stock's configuration (strings in its libcrypto): linux-generic32, so no assembly, -O3.
    # OPENSSLDIR is /etc/ssl, where the rootfs keeps openssl.cnf; stock's named its build host.
    run('./Configure', 'linux-generic32', 'shared', '--prefix=/usr', '--openssldir=/etc/ssl',
        '--libdir=lib', '-DOPENSSL_USE_NODELETE', 'no-tests', *OPENSSL_OMIT, *ARCH, *REPRODUCIBLE, '-O3',
        cwd=src, env=dict(env.vars, CC=f'{env.bin}/{TRIPLE}-gcc', CFLAGS='', CPPFLAGS='', LDFLAGS=''), log=log)
    run('make', f'-j{JOBS}', cwd=src, env=env.vars, log=log)
    run('make', 'install_sw', f'DESTDIR={stage}', cwd=src, env=env.vars, log=log)


RECIPES = {'openssl': (build_openssl, [])}


# Each top-level function's and class's text as this module was imported (inspect.getsource rereads
# the file, which may have been edited since), for the cache key.
_TEXT = pathlib.Path(__file__).read_text()
CODE = {n.name: ast.get_source_segment(_TEXT, n) for n in ast.parse(_TEXT).body
        if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def key(name, deps):
    """The cache key: the source, the recipe's text and that of the module-level helpers and constants
    it names (autotools, BUSYBOX_APPLETS), the toolchain and flags, and each dependency's key."""
    fn, _ = RECIPES[name]
    parts = [SOURCES[name][2], TOOLCHAIN[1], ' '.join(ARCH + REPRODUCIBLE), CODE['Env'], *sorted(deps.items())]
    seen, todo = set(), [fn]
    while todo:
        f = todo.pop()
        if f.__name__ in seen:
            continue
        seen.add(f.__name__)
        parts.append(CODE[f.__name__])
        for n in f.__code__.co_names:
            v = globals().get(n)
            if inspect.isfunction(v) and v.__module__ == __name__ and n not in ('run', 'toolchain'):
                todo.append(v)
            elif isinstance(v, (str, list, tuple, dict)) and n not in ('SOURCES', 'TOOLCHAIN', 'ARCH', 'CACHE', 'JOBS'):
                parts.append(f'{n}={v!r}')
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part).encode() + b'\0')
    return h.hexdigest()[:16]


def build(name, done=None):
    """name's staged install tree, built (with its dependencies) unless cached."""
    done = {} if done is None else done
    if name in done:
        return done[name]
    fn, needs = RECIPES[name]
    deps = {d: build(d, done) for d in needs}
    k = key(name, {d: p.name for d, p in deps.items()})
    stage = CACHE/'out'/f'{name}-{SOURCES[name][0]}-{k}'
    if not (stage/'.done').exists():
        work = CACHE/'work'/name
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        log = work/'build.log'
        print(f'thirdparty: building {name} {SOURCES[name][0]} (log {log})', file=sys.stderr)
        src = unpack(name, work)
        fn(src, stage, Env(deps, epoch(name)), log)
        (stage/'.done').write_text(json.dumps(dict(version=SOURCES[name][0], sha256=SOURCES[name][2])))  # the stage is whole
        shutil.rmtree(work)
    done[name] = stage
    return stage


class Elf:
    """What the dynamic linker sees of one ELF file: its flags, NEEDED, and dynamic symbols."""
    def __init__(self, path):
        self.path = path
        header = subprocess.run(['readelf', '-hdA', '-W', path], capture_output=True, text=True).stdout
        self.flags = int(need(re.search(r'Flags:\s+(0x[0-9a-f]+)', header), f'{path}: not an ELF file').group(1), 16)
        fp = re.search(r'^FP ABI: (.+)$', header, re.M) or re.search(r'Tag_GNU_MIPS_ABI_FP: (.+)$', header, re.M)
        self.fp = fp.group(1).strip() if fp else None
        self.needed = re.findall(r'\(NEEDED\)\s+Shared library: \[(.+)\]', header)
        self.runpath = [p for m in re.findall(r'\((?:RPATH|RUNPATH)\)\s+Library r(?:un)?path: \[(.*)\]', header)
                        for p in m.split(':') if p]
        self.defined, self.undefined = {}, []
        table = subprocess.run(['readelf', '--dyn-syms', '-W', path], capture_output=True, text=True).stdout
        for line in table.splitlines():
            f = line.split()
            if len(f) < 8 or not f[0][:-1].isdigit() or f[3] in ('SECTION', 'FILE') or f[4] == 'LOCAL':
                continue
            name, version = need(re.match(r'([^@\s]+)(?:@@?([^\s]+))?', f[7]), f'{path}: {line}').groups()
            if f[6] == 'UND':
                self.undefined.append((name, version, f[4] == 'WEAK', f[3], int(f[2], 0)))
            else:
                self.defined.setdefault(name, []).append((version, f[3], int(f[2], 0)))


def resolve(root, elfs, name, runpath):
    """The real path (relative to root) of library name as the dynamic linker would find it."""
    for d in [*runpath, '/lib', '/usr/lib']:
        p = root/d.lstrip('/')/name
        while p.is_symlink():
            target = os.readlink(p)
            p = (root/target.lstrip('/')) if target.startswith('/') else p.parent/target
        if p.exists():
            rel = os.path.relpath(os.path.normpath(p), root)
            if rel in elfs:
                return rel
    return None


def unresolved(root, elfs):
    """Every (file, symbol@version) a file imports that no library in its NEEDED closure defines, and every
    imported data object whose size differs from its definition's (a copy relocation would truncate it)."""
    problems = set()
    for rel, elf in elfs.items():
        closure, queue = [], [rel]
        while queue:
            cur = queue.pop(0)
            for n in elfs[cur].needed:
                lib = resolve(root, elfs, n, elfs[cur].runpath)
                if lib is None:
                    problems.add((rel, f'missing library {n}'))
                elif lib not in closure:
                    closure.append(lib)
                    queue.append(lib)
        for name, version, weak, kind, size in elf.undefined:
            defs = [d for lib in closure for d in elfs[lib].defined.get(name, ())
                    if version is None or d[0] == version]
            if not defs:
                if not weak:
                    problems.add((rel, f'{name}@{version}' if version else name))
            elif kind == 'OBJECT' and size and defs[0][2] != size:
                problems.add((rel, f'{name}: object size {size}, defined as {defs[0][2]}'))
    return problems


def elf_files(root):
    """Every regular ELF file under root, by path relative to it."""
    found = {}
    for dirpath, _, files in os.walk(root):
        for f in files:
            p = pathlib.Path(dirpath)/f
            if p.is_symlink() or not p.is_file():
                continue
            with open(p, 'rb') as fh:
                if fh.read(4) != b'\x7fELF':
                    continue
            found[os.path.relpath(p, root)] = p
    return found


def check_abi(stock_root, replacements):
    """Problems with replacing stock_root's files by replacements ({rootfs path: new file}): a header
    or FP ABI that differs from the stock file's, a symbol some file imports that it no longer finds,
    a data object whose size changed. Problems stock already had (a plugin's import from its host
    program) are not counted. An empty list means the swap keeps every stock import satisfied."""
    stock_root = pathlib.Path(stock_root)
    files = elf_files(stock_root)
    before = {rel: Elf(p) for rel, p in files.items()}
    after = dict(before)
    problems = []
    for rel, new in replacements.items():
        e = Elf(new)
        old = before.get(rel)
        if old is None:
            problems.append(f'{rel}: no stock ELF file to replace')
            continue
        if e.flags != old.flags:
            problems.append(f'{rel}: ELF flags {e.flags:#x}, stock {old.flags:#x}')
        if e.fp != old.fp:
            problems.append(f'{rel}: FP ABI {e.fp}, stock {old.fp}')
        after[rel] = e
    known = unresolved(stock_root, before)
    problems += sorted(f'{rel}: {what}' for rel, what in unresolved(stock_root, after) - known)
    return problems


# Where each component's files go: {rootfs path (stock's name, which every symlink and DT_NEEDED
# reaches): path in the component's staged install}. A library keeps stock's file name whatever its
# new version, as the repack keeps every stock inode's name.
INSTALL = {
    'openssl': {'usr/lib/libssl.so.1.1': 'usr/lib/libssl.so.1.1', 'usr/lib/libcrypto.so.1.1': 'usr/lib/libcrypto.so.1.1',
                **{f'usr/lib/engines-1.1/{e}.so': f'usr/lib/engines-1.1/{e}.so' for e in ('afalg', 'capi', 'padlock')}},
}


def stripped(src, dst):
    """src without symbols or debug sections, written to dst."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(f'{toolchain()}/{TRIPLE}-strip', '--strip-unneeded', '-R', '.comment', '-R', '.note.gnu.build-id', '-o', dst, src)
    return dst


def replacements(out):
    """{rootfs path: stripped file under out/thirdparty} for every INSTALL entry, and the version of each
    component built for them, those linked in (mbedTLS) or against (libnl) included."""
    files, done = {}, {}
    for name, entries in INSTALL.items():
        stage = build(name, done)
        for path, rel in entries.items():
            files[path] = stripped(stage/rel, out/'thirdparty'/path)
    return files, {name: SOURCES[name][0] for name in done}


def install(p, out, cat, swap_inode, squashfs):
    """The pseudo file p with stock's outdated components swapped for their rebuilds, and the
    manifest's record of them. Stops the build when check_abi finds a problem."""
    files, versions = replacements(out)
    root = out/'stockfs'
    shutil.rmtree(root, ignore_errors=True)
    run('unsquashfs', '-q', '-no-xattrs', '-d', root, squashfs)
    problems = check_abi(root, files)
    if problems:
        sys.exit('thirdparty: the rebuilt libraries would break stock programs:\n' + '\n'.join(problems))
    record = {}
    for path, f in files.items():
        p = swap_inode(p, path.encode(), f)
        record[path] = dict(original_sha256=hashlib.sha256(cat(path)).hexdigest(), sha256=sha256(f))
    shutil.rmtree(root)
    return p, dict(versions=versions, files=record)


def main(argv):
    if argv[:1] == ['build']:
        for name in argv[1:] or list(RECIPES):
            print(name, build(name))
        return 0
    if argv[:1] == ['check'] and len(argv) == 2:
        import tempfile
        with tempfile.TemporaryDirectory(prefix='q2-thirdparty-') as d:
            problems = check_abi(argv[1], replacements(pathlib.Path(d))[0])
        print('\n'.join(problems) or 'ok')
        return 1 if problems else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
