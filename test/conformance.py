"""Vamp API 1's conformance transcripts (docs/platform/README.md#conformance): the `conformance` blocks of a protocol
page, replayed against the real service. `> line` is sent; `< line` must be the next answer byte for byte, `< ~ regex`
must match it whole; `<* ~ regex` passes over any answers that match. Each suite (test/playd.py, test/sysd.py,
test/netd.py) calls blocks() and replay() from its own test_conformance with its stand-in Q2; test/api.py runs
the three."""
import os
import pathlib
import re
import socket

DOCS = pathlib.Path(os.environ.get('Q2API_DOCS') or pathlib.Path(__file__).resolve().parents[1]/'docs/platform')  # another copy, to show a gap fails


def blocks(page):
    """the page's conformance blocks, each a list of (kind, text): kind '>', '<', '<~' or '<*'"""
    out, cur = [], None
    for line in (DOCS/page).read_text().splitlines():
        if line.strip() == '```conformance':
            cur = []
        elif line.strip() == '```' and cur is not None:
            out.append(cur)
            cur = None
        elif cur is not None and line:
            if line.startswith('> '):
                cur.append(('>', line[2:]))
            elif line.startswith('<* ~ '):
                cur.append(('<*', line[5:]))
            elif line.startswith('< ~ '):
                cur.append(('<~', line[4:]))
            elif line.startswith('< '):
                cur.append(('<', line[2:]))
            else:
                raise ValueError(f'{page}: not a transcript line: {line!r}')
    assert out, f'{page} has no conformance block'
    return out


def caps_words(page):
    """the words its transcript's caps answer names"""
    for b in blocks(page):
        for i, (k, t) in enumerate(b):
            if k == '>' and t == 'caps':
                return b[i + 1][1].split()[1:]
    raise AssertionError(f'{page} has no caps exchange')


def check_coverage(page):
    """every capability word is sent as a command by some block, so no command is served undocumented; a word with a
    dash names a command's first words (usb-dac: `usb dac`)"""
    sent = [t for b in blocks(page) for k, t in b if k == '>']
    missing = [w for w in caps_words(page) if not any(f'{t} '.startswith(w.replace('-', ' ') + ' ') for t in sent)]
    assert not missing, f'{page}: caps words never sent by a transcript: {missing}'


def replay(sock_path, block, timeout=8):
    """one block against the service at sock_path; an AssertionError naming the step that differs"""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(sock_path))
    s.settimeout(timeout)
    f = s.makefile('rw')
    ahead = []

    def nxt():
        return ahead.pop(0) if ahead else f.readline().rstrip('\n')
    try:
        for n, (kind, text) in enumerate(block, 1):
            if kind == '>':
                f.write(text + '\n')
                f.flush()
            elif kind == '<*':
                while True:
                    line = nxt()
                    if not re.fullmatch(text, line):
                        ahead.insert(0, line)
                        break
            else:
                line = nxt()
                ok = line == text if kind == '<' else re.fullmatch(text, line) is not None
                assert ok, f'step {n}: expected {"" if kind == "<" else "~ "}{text!r}, the service said {line!r}'
    finally:
        s.close()


def schemas(page='library.md'):
    """the page's schema blocks: {file: {table: [columns]}}"""
    out, cur = {}, None
    for line in (DOCS/page).read_text().splitlines():
        if line.startswith('```schema '):
            cur = out.setdefault(line.split()[1], {})
        elif line.strip() == '```':
            cur = None
        elif cur is not None and line:
            t, cols = line.split(':', 1)
            cur[t] = cols.split()
    assert out, f'{page} has no schema block'
    return out
