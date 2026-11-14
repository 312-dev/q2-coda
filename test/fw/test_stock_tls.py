"""stock_tls.c (docs/internals.md#stock-https): the setopt calls with which stock's HTTPS helpers turned certificate
checks off jump to ringnav_stock_tls, which turns them on against the firmware's Mozilla bundle."""
import ctypes as C

import pytest

CURLOPT_CAINFO, CURLOPT_SSL_VERIFYPEER, CURLOPT_SSL_VERIFYHOST, CURLOPT_SSL_CTX_FUNCTION = 10065, 64, 81, 20108


@pytest.mark.parametrize('option', [CURLOPT_SSL_VERIFYPEER, CURLOPT_SSL_VERIFYHOST])
def test_a_check_stock_turned_off_is_on_against_the_bundle(fw, option):
    opts, handles = {}, set()
    fw.mock('fw_curl_easy_setopt', lambda c, opt, value, *_: handles.add(c) or opts.__setitem__(opt, value) or 0)
    # stock's own call: curl_easy_setopt(c, option, 0)
    assert fw.fn('ringnav_stock_tls', C.c_int, C.c_void_p, C.c_int, C.c_long)(0x1234, option, 0) == 0
    assert C.string_at(opts[CURLOPT_CAINFO]) == b'/etc/ssl/certs/ca-certificates.crt'
    assert opts[CURLOPT_SSL_VERIFYPEER] == 1 and opts[CURLOPT_SSL_VERIFYHOST] == 2
    assert opts[CURLOPT_SSL_CTX_FUNCTION]  # tls_untimed: the clock may be wrong before network time
    assert set(opts) == {CURLOPT_CAINFO, CURLOPT_SSL_VERIFYPEER, CURLOPT_SSL_VERIFYHOST, CURLOPT_SSL_CTX_FUNCTION}
    assert handles == {0x1234}  # all on stock's handle
