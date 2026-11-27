"""q2adb-mdns (patch/adb_mdns.c): coda-rc's responder (patch/coda_rc_mdns.c) with its service type set to _adb,
checked against tools/vamp.py's own mDNS reader (docs/adb.md#adb-over-wi-fi). The responder is built for this
machine with test/unit/coda_rc_mdns_host.c around it, as test_coda_rc_mdns.py builds it: vamp's query is
answered, the announcement reads back in vamp as the Q2 (name, address, port, id), and _q2muse's is left alone
once the type is _adb. adb_mdns.c itself is compiled for this machine so its calls match coda_rc.h."""
import ctypes
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATCH = ROOT / 'patch'
sys.path.insert(0, str(ROOT / 'tools'))
import vamp  # noqa: E402
from test_coda_rc_mdns import Self  # noqa: E402

MD_PTR, MD_ALL = 1, 31


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
    so = tmp_path_factory.mktemp('adb_mdns') / 'mdns.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DCODA_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    '-I', str(PATCH), str(PATCH / 'coda_rc_mdns.c'), str(ROOT / 'test/unit/coda_rc_mdns_host.c'),
                    '-o', str(so)], check=True)
    L = ctypes.CDLL(str(so))
    L.mdns_service.argtypes = [ctypes.c_char_p]
    L.mdns_query.argtypes = [ctypes.POINTER(Self), ctypes.c_char_p, ctypes.c_size_t]
    L.mdns_reply.argtypes = [ctypes.POINTER(Self), ctypes.c_uint, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
    L.mdns_reply.restype = ctypes.c_size_t
    return L


def me():
    s = Self()
    s.ip[:] = (192, 168, 1, 23)
    s.id[:] = bytes.fromhex('1a2b3c4d5e6f7081')
    s.port = 5555
    s.name = b'Desk Q2'
    s.host = b'q2muse-1a2b3c'
    return s


def test_vamp_reads_the_q2s_announcement(lib):
    assert lib.mdns_service(b'_adb') == 1
    buf = ctypes.create_string_buffer(1500)
    n = lib.mdns_reply(ctypes.byref(me()), MD_ALL & ~16, 120, buf, 1500)
    assert vamp.instances(vamp.mdns_records(buf.raw[:n])) == [
        {'name': 'Desk Q2', 'ip': '192.168.1.23', 'port': 5555, 'id': '1a2b3c4d5e6f7081', 'host': 'q2muse-1a2b3c.local'}]


def test_vamps_query_is_answered_and_q2muses_is_not(lib):
    assert lib.mdns_service(b'_adb') == 1
    q = vamp.mdns_query(vamp.SERVICE)
    assert lib.mdns_query(ctypes.byref(me()), q, len(q)) == MD_PTR
    old = vamp.mdns_query('_q2muse._tcp.local')
    assert lib.mdns_query(ctypes.byref(me()), old, len(old)) == 0
    assert lib.mdns_service(b'_q2muse') == 1  # put back for anything after this in the process
    assert lib.mdns_query(ctypes.byref(me()), old, len(old)) == MD_PTR


@pytest.mark.parametrize('label', [b'adb', b'', b'_' + b'x' * 64])
def test_a_service_label_it_cannot_take_is_refused(lib, label):
    assert lib.mdns_service(label) == 0


def test_adb_mdns_compiles_for_this_machine(tmp_path):
    subprocess.run(['clang', '-c', '-DCODA_HOST', '-Wall', '-Werror', '-Wno-unused-parameter', '-I', str(PATCH),
                    str(PATCH / 'adb_mdns.c'), '-o', str(tmp_path / 'adb_mdns.o')], check=True)
