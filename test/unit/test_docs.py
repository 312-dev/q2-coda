"""What the docs and the sources cite: doc anchors that resolve, and docs/architecture.md's symbols
named by file, never by line. Reads the tree as it is on disk, tracked or not."""
import os
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKIP = {'graphify-out', 'build', '.venv', 'node_modules', '__pycache__'}
# Under mutmut, ROOT is mutants/: its tools/ holds every mutant and it has no docs/ but docs/manual.
pytestmark = pytest.mark.skipif('MUTANT_UNDER_TEST' in os.environ, reason="mutmut's copy of the tree has no docs")


def files(folder):
    return [p for p in sorted((ROOT/folder).rglob('*')) if p.is_file() and not SKIP & set(p.relative_to(ROOT).parts)]


def slug(heading):
    """GitHub's anchor for a heading: lowercased, all but letters, digits, spaces, - and _ dropped, spaces as -."""
    return re.sub(r'[^\w\- ]', '', heading.strip().lower()).replace(' ', '-')


def anchors(path, seen={}):
    """Every heading's anchor in path (a repeated one -1, -2, ...) and every <a id>; none inside a code fence."""
    if path not in seen:
        found, counts, fence = set(), {}, False
        for line in path.read_text().splitlines():
            if re.match(r'\s*(```|~~~)', line):
                fence = not fence
            elif not fence:
                if m := re.match(r'#{1,6} (.*)', line):
                    a = slug(m[1])
                    found.add(f'{a}-{counts[a]}' if a in counts else a)
                    counts[a] = counts.get(a, 0) + 1
                found.update(re.findall(r'<a id="([^"]+)"', line))
        seen[path] = found
    return seen[path]


def citations():
    """(file, line, target, anchor): a doc's path under docs/ and its anchor in the sources; Markdown
    links in the docs, their path from the file that holds them."""
    for folder in ('patch', 'tools', 'test'):
        for p in files(folder):
            for i, line in enumerate(p.read_text(errors='replace').splitlines(), 1):
                for m in re.finditer(r'docs/([\w/.-]+?\.md)#([\w-]*\w)', line):
                    yield p, i, ROOT/'docs'/m[1], m[2]
    for p in sorted((ROOT/'docs').glob('*.md')) + [ROOT/n for n in ('AGENTS.md', 'CONTRIBUTING.md', 'README.md')]:
        for i, line in enumerate(p.read_text().splitlines(), 1):
            for m in re.finditer(r'\]\(([^()\s#:]*\.md)?#([^()\s]+)\)', line):
                yield p, i, (p.parent/m[1]).resolve() if m[1] else p, m[2]


def where(p):
    return p.relative_to(ROOT) if p.is_relative_to(ROOT) else p


def test_every_cited_anchor_resolves():
    cited = list(citations())
    assert len(cited) > 500  # the patterns still match: about 770 at the time of writing
    broken = [f'{where(p)}:{i}: {where(t)}#{a}' for p, i, t, a in cited if not t.is_file() or a not in anchors(t)]
    assert not broken, '\n'.join(broken)


def test_slug_is_github_s():
    assert slug('Now Playing (iPod)') == 'now-playing-ipod' and slug('`format.sh` and CI') == 'formatsh-and-ci'
    assert slug('Wi-Fi transfer') == 'wi-fi-transfer' and slug('MP3 gapless_playback') == 'mp3-gapless_playback'


ARCHITECTURE = ROOT/'docs/architecture.md'


def test_architecture_names_no_line():
    """A line number there is stale within a day; the symbol and its file are not."""
    text = ARCHITECTURE.read_text()
    lines = [f'docs/architecture.md:{i}: {m[0]}' for i, line in enumerate(text.splitlines(), 1)
             for m in re.finditer(r'[\w/.-]+\.\w+:\d+|`:\d+`', line)]
    assert not lines, '\n'.join(lines)


def test_architecture_s_symbols_are_in_their_files():
    """Each `name` in `file` or `name` (`file`) it writes names something that file holds."""
    pairs = [(i, m[1], m[2]) for i, line in enumerate(ARCHITECTURE.read_text().splitlines(), 1)
             for m in re.finditer(r'`(\w+)(?:\([^`]*\))?`\**(?: in |\s*\()`([\w.-]+/[\w./-]+\.\w+)`', line)]
    assert len(pairs) > 30  # the pattern still matches: about 40 at the time of writing
    missing = [f'docs/architecture.md:{i}: {name} not in {path}' for i, name, path in pairs
               if not re.search(rf'\b{name}\b', (ROOT/path).read_text())]
    assert not missing, '\n'.join(missing)


def test_internals_hooks_table_agrees_with_build():
    """Each row of internals.md's Hooks table names a hooked stock function at its address: a row of
    HOOKS, or a *_LEAF, hooked whole as it has no PIC prologue."""
    import build
    text = (ROOT/'docs/internals.md').read_text().split('\n## Hooks\n', 1)[1].split('\n## ', 1)[0]
    body = re.findall(r'^\| (?!Stock callback|-).*', text, re.M)
    rows = [m.groups() if m else (line, '') for line in body
            for m in [re.match(r'\| `(\w+)`(?: \(unnamed\))? +\| `(0x[0-9a-f]+)` +\|', line)]]
    assert len(rows) > 40  # the table is still read: 46 rows at the time of writing
    hooked = {name: address for name, (address, _, _) in build.HOOKS.items()}
    hooked.update((leaf[0], leaf[1]) for leaf in (build.WM_PAINT_LEAF, build.GRADIENT_LEAF, build.QUEUE_LEAF))
    wrong = [f'docs/internals.md#hooks: {name} {address}: {hex(hooked[name]) if name in hooked else "no hook of that name"}'
             for name, address in rows if not address or hooked.get(name) != int(address, 16)]
    assert not wrong, '\n'.join(wrong)
