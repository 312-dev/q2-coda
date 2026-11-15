"""patch/cgi_root.c, Wi-Fi transfer's set-uid CGI (docs/internals.md#wi-fi-transfer-root), built for this machine
with a probe script in q2cgi's place. Unprivileged, as CI runs: a caller that is not the server's user, and one that
is but whose program could not become root, both get a 500 and the script never runs. As root (sudo pytest), it
also checks what the script is given: only the variables q2cgi reads and a fixed PATH, no other descriptor, init's
limits, every signal at its default and none blocked, umask 022."""
import os
import pathlib
import resource
import signal
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROBE = r"""#!/bin/sh
echo ran
for v in PATH SCRIPT_NAME REQUEST_METHOD QUERY_STRING CONTENT_LENGTH CONTENT_TYPE HTTP_COOKIE LD_PRELOAD EVIL; do
  eval "[ -n \"\${$v+x}\" ] && echo \"env $v=\${$v}\""
done
grep -E '^(Uid|Gid|SigBlk|SigIgn)' /proc/self/status
echo "umask $(umask) cwd $(pwd) fsize $(ulimit -f)"
for f in /proc/$$/fd/*; do echo "fd ${f##*/} $(readlink $f)"; done
"""


@pytest.fixture
def cgi(tmp_path):
    def build(uid):
        probe = tmp_path / 'probe.sh'
        probe.write_text(PROBE)
        probe.chmod(0o755)
        exe = tmp_path / f'cgi_root.{uid}'
        subprocess.run(['cc', '-O1', '-w', f'-DQ2CGI="{probe}"', f'-DWWW_UID={uid}', f'-DCGI_DIR="{tmp_path}"',
                        str(ROOT / 'patch/cgi_root.c'), '-o', str(exe)], check=True)
        return exe
    return build


def run(exe, env=None, **kw):
    return subprocess.run([str(exe)], env=env if env is not None else {'SCRIPT_NAME': '/list'}, capture_output=True,
                          text=True, **kw)


def refused(r):
    assert r.returncode == 1 and r.stdout.startswith('Status: 500 ') and 'ran' not in r.stdout


@pytest.mark.skipif(os.getuid() == 0, reason='root may always run it')
def test_a_caller_that_is_not_the_servers_user_is_refused(cgi):
    refused(run(cgi(os.getuid() + 1)))


@pytest.mark.skipif(os.getuid() == 0, reason='as root, becoming root works')
def test_the_server_user_without_root_is_refused(cgi):
    refused(run(cgi(os.getuid())))  # not set-uid here: setgid(0) fails, and nothing runs


@pytest.mark.skipif(os.getuid() != 0, reason='needs root to become root')
def test_the_script_gets_only_what_thttpd_gives(cgi, tmp_path):
    exe = cgi(33)
    env = {'SCRIPT_NAME': '/cgi-bin/list.cgi', 'REQUEST_METHOD': 'GET', 'QUERY_STRING': 'path=%2Fmnt%2Fmmc',
           'HTTP_COOKIE': 'q2s=1', 'LD_PRELOAD': '/nonexistent.so', 'IFS': 'x', 'EVIL': '1', 'PATH': str(tmp_path)}
    exe.chmod(0o4755)  # root's, built by root
    for d in (tmp_path, tmp_path.parent):  # pytest makes them 0700: the server user must reach the program
        d.chmod(0o755)
    def hostile():
        os.umask(0o077)
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024, 1024))
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        os.setgid(33), os.setuid(33)
    (tmp_path / 'marker').write_text('')
    extra = os.open(tmp_path / 'marker', os.O_RDONLY)  # a descriptor the caller left open
    try:
        r = run(exe, env, pass_fds=(extra,), preexec_fn=hostile)
    finally:
        os.close(extra)
    out = r.stdout.splitlines()
    assert out[0] == 'ran', r.stdout
    assert [l for l in out if l.startswith('env ')] == [
        'env PATH=/bin:/usr/bin:/sbin:/usr/sbin', 'env SCRIPT_NAME=/cgi-bin/list.cgi', 'env REQUEST_METHOD=GET',
        'env QUERY_STRING=path=%2Fmnt%2Fmmc', 'env HTTP_COOKIE=q2s=1']
    assert 'Uid:\t0\t0\t0\t0' in out and 'Gid:\t0\t0\t0\t0' in out
    assert 'SigBlk:\t0000000000000000' in out and 'SigIgn:\t0000000000000000' in out
    assert f'umask 0022 cwd {tmp_path} fsize unlimited' in out
    fds = [l.split(' ', 2)[1:] for l in out if l.startswith('fd ')]
    assert {'0', '1', '2'} <= {n for n, _ in fds} and not [f for _, f in fds if f.endswith('/marker')], fds


@pytest.mark.skipif(os.getuid() != 0, reason='needs root to install it set-uid')
def test_only_the_server_user_may_run_it_set_uid(cgi, tmp_path):
    exe = cgi(33)
    exe.chmod(0o4755)
    for d in (tmp_path, tmp_path.parent):
        d.chmod(0o755)
    refused(run(exe, preexec_fn=lambda: (os.setgid(34), os.setuid(34))))
    assert run(exe, preexec_fn=lambda: (os.setgid(33), os.setuid(33))).stdout.startswith('ran')
