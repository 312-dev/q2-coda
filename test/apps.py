#!/usr/bin/env python3
"""Vamp API 1's app manifest (docs/platform/packages.md): q2pkgd app-check, built for this machine, on stand-in app
folders: an API 0 app as today's, each kind's good manifest, and each rule's refusal with its reason; and every
example manifest on the page checked as the page says. Run it as python3 test/apps.py (pytest). Q2PKGD_SOURCE builds
another copy, to show a broken one fails here."""
import os
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = pathlib.Path(os.environ.get('Q2PKGD_SOURCE') or ROOT/'pkgd/q2pkgd.c')
PAGE = pathlib.Path(os.environ.get('Q2API_DOCS') or ROOT/'docs/platform')/'packages.md'


@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp('pkgd')/'q2pkgd'
    mono = ROOT/'patch/monocypher'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', f'-I{mono}', '-I', str(ROOT/'perm'), '-DRELEASE_KEY={0}', '-o', str(out), str(SOURCE),
                    str(mono/'monocypher.c'), str(mono/'monocypher-ed25519.c')], check=True)
    return out


def app(tmp_path, manifest, folder='.radio', files=('q2-radio',)):
    d = tmp_path/folder
    d.mkdir(parents=True)
    (d/'q2app').write_text(manifest)
    for f in files:
        (d/f).write_text('#!/bin/sh\n')
    return d


def check(binary, d):
    r = subprocess.run([str(binary), 'app-check', str(d)], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


GOOD = 'name=Radio\napi=1\nversion=1.2.0\nkind=browse\nentry=q2-radio --quiet\npermissions=playback,network-hold\n'


def test_an_api_0_app_is_run_as_today(binary, tmp_path):
    assert check(binary, app(tmp_path, 'name=Subsonic\ncatalogue=1\ncovers=1\n')) == (0, 'app radio: Subsonic, API 0 (a card app as today)')


def test_the_notice_permissions_are_known(binary, tmp_path):
    assert check(binary, app(tmp_path, GOOD.replace('playback,network-hold', 'notices,ask'))) == (
        0, 'app radio: Radio 1.2.0, a browse for API 1, may use: notices,ask')


def test_a_good_manifest_says_what_the_app_may_do(binary, tmp_path):
    assert check(binary, app(tmp_path, GOOD.replace('\n', '\r\n'))) == (
        0, 'app radio: Radio 1.2.0, a browse for API 1, may use: playback,network-hold')


@pytest.mark.parametrize('change, why', [
    (('api=1', 'api=2'), 'needs API 2; this Vamp OS serves API 1'),
    (('api=1', 'api=x'), 'api= is the lowest Vamp API it needs'),
    (('version=1.2.0', 'version=1..2'), 'version= is dotted numbers'),
    (('kind=browse', 'kind=widget'), 'kind=widget is not a kind'),
    (('entry=q2-radio --quiet', 'entry=gone'), 'entry= names gone, which is not in the folder'),
    (('entry=q2-radio --quiet', 'entry=q2-radio; rm -rf /'), 'run with no shell'),
    (('entry=q2-radio --quiet', 'entry=../q2-radio'), 'run with no shell'),
    (('entry=q2-radio --quiet\n', ''), 'a browse needs entry='),
    (('network-hold', 'root'), 'root is not a permission'),
    (('playback,network-hold', 'playback,screen'), "screen is a game's permission alone"),
    (('kind=browse', 'kind=service'), 'a service runs in the background'),
    (('name=Radio\n', ''), 'no name='),
])
def test_each_rule_refuses_with_its_reason(binary, tmp_path, change, why):
    code, said = check(binary, app(tmp_path, GOOD.replace(*change, 1)))
    assert code == 1 and why in said, said


def test_a_theme_is_data_alone(binary, tmp_path):
    theme = 'name=Dusk\napi=1\nversion=1\nkind=theme\n'
    assert check(binary, app(tmp_path, theme, files=()))[0] == 0
    code, said = check(binary, app(tmp_path/'b', theme + 'entry=q2-radio\n'))
    assert code == 1 and 'a theme is data and has no entry=' in said


def test_the_folder_is_an_app_id(binary, tmp_path):
    code, said = check(binary, app(tmp_path, GOOD, folder='Radio'))
    assert code == 1 and 'an app\'s folder is .<id>' in said


def test_the_page_s_examples(binary, tmp_path):
    """each ```q2app block on the page, its folder and files named on its first line, gives what its comment says"""
    text = PAGE.read_text()
    found = re.findall(r'```q2app (\S+) (ok|refused)((?: \S+)*)\n(.*?)```', text, re.S)
    assert found, 'the page has no example manifests'
    for i, (folder, want, files, body) in enumerate(found):
        code, said = check(binary, app(tmp_path/str(i), body, folder=folder, files=files.split()))
        assert (code == 0) == (want == 'ok'), f'{folder}: {said}'


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
