"""patch/ui_kit.c: the design's shared components, starting with its colour tokens in each half."""
import ctypes as C

import pytest

import host
from test_nav_painters import scratch, st  # noqa: F401 (fixtures)

OLED = next(t['index'] for t in host.tools_theme.builtins() if t['id'] == 'oled')


@pytest.mark.parametrize('light', [0, 1])
def test_a_token_is_tools_theme_pys_value_in_the_half_light_mode_picks(fw, st, light):
    def config(path, section, key, out, default, *_):
        value = b'%d' % OLED if C.string_at(key) == b'THEME' else C.string_at(default)
        C.memmove(out, value + b'\0', len(value) + 1)
    fw.mock('toolsReadConfig', config)
    fw.fn('ipod_light', C.c_int)()  # reads the settings, OLED's theme among them
    st('light').value = light
    assert fw.fn('ipod_light', C.c_int)() == light
    tok = fw.fn('tok', C.c_uint, C.c_int)
    assert [tok(i) for i in range(len(host.tools_theme.TOKENS))] == [host.tools_theme.token(n, bool(light))
                                                                     for n in host.tools_theme.TOKENS]
