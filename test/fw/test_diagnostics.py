"""Save Diagnostics (patch/diagnostics.c, docs/internals.md#save-diagnostics): a --dev build's About row that writes
what a device problem is read from to one file on the SD card and toasts where it went; a release build has no row."""
import ctypes as C
import pathlib
import subprocess

import pytest

from test_coverflow import Ui, themed

FILE = '/mnt/mmc/q2-diagnostics.txt'


class About:
    """About's list as ringnav_about leaves it, on test_coverflow's widgets; the commands run and the toasts."""
    def __init__(self, fw):
        themed(fw)
        self.ui = Ui(fw)
        self.commands, self.toasts, self.status = [], [], 0
        fw.mock('button_create', lambda parent, x, y, w, h, *_: self.ui.make(parent, 'button', x, y, w, h))
        fw.mock('fw_system', lambda cmd, *_: self.commands.append(C.string_at(cmd).decode()) or self.status)
        fw.mock('navigator_to_with_context', lambda name, info, *_: self.toasts.append(C.string_at(info + 8).decode()) or 0)
        self.view = self.ui.make(0, 'scroll_view', name='scroll_view_about')
        self.stock = self.ui.make(self.view, 'list_item')  # stock's rows, CFW. Version's
        fw.fn('diag_about_row', None, C.c_void_p)(self.view)

    def rows(self):
        return [self.ui[w].text for w in self.ui.of('hscroll_label')]

    def save(self, status):
        self.status = status
        button, = self.ui.of('button')
        self.ui.click(button)


def test_a_release_build_has_no_save_diagnostics(fw):
    about = About(fw)
    assert about.rows() == [] and about.ui[about.view].kids == [about.stock]


def test_a_dev_build_s_about_starts_with_save_diagnostics_which_keeps_its_click(fw_dev):
    """The row, a settings row with its click, first so it is in sight as the page opens, marked so about_rows leaves
    its click alone while it gives each of stock's rows about_click, which does nothing."""
    about = About(fw_dev)
    assert about.rows() == ['Save Diagnostics']
    item, stock = about.ui[about.view].kids
    assert stock == about.stock
    stock_button = about.ui.make(stock, 'button')
    bound = []
    fw_dev.mock('widget_on', lambda w, evt, fn, ctx, *_: bound.append(w) or 1)
    fw_dev.fn('about_rows', None, C.c_void_p)(about.view)
    item_button, = about.ui[item].kids
    assert bound == [stock_button] and item_button not in bound and fw_dev.fn('diag_row', C.c_int, C.c_void_p)(item) == 1


@pytest.mark.parametrize('status, toast', [(0, 'Saved to SD card: q2-diagnostics.txt'), (2 << 8, 'No SD card'),
                                           (1 << 8, "Couldn't save diagnostics"), (9, "Couldn't save diagnostics"),
                                           (-1, "Couldn't save diagnostics")])
def test_saving_runs_one_command_and_toasts_where_the_file_went(fw_dev, status, toast):
    """system's status: exit 0 saved, the script's 2 no card mounted, any other exit or a signal a failure."""
    about = About(fw_dev)
    about.save(status)
    assert len(about.commands) == 1 and about.toasts == [toast]


def test_the_file_holds_the_device_s_state_and_nothing_personal(fw_dev, tmp_path):
    """The command run under this machine's sh, its device paths moved under tmp_path: one file with each section,
    the UDC's state, each gadget's UDC, functions and LUN file, and the logs' tails; no config.ini, Wi-Fi settings or
    card listing is read. Without the card in /proc/mounts it writes nothing and exits 2."""
    about = About(fw_dev)
    about.save(0)
    command, = about.commands
    for word in ('config.ini', 'wpa', 'adb_keys', 'ls /mnt/mmc'):
        assert word not in command
    root = tmp_path
    for d in ('mmc', 'udc/13500000.otg', 'gadget/storage_demo/functions/mass_storage.msg/lun.0',
              'gadget/storage_demo/configs/c.1', 'log'):
        (root/d).mkdir(parents=True)
    (root/'udc/13500000.otg/state').write_text('configured\n')
    (root/'gadget/storage_demo/UDC').write_text('13500000.otg\n')
    (root/'gadget/storage_demo/functions/mass_storage.msg/lun.0/file').write_text('/dev/mmcblk0p1\n')
    (root/'gadget/storage_demo/configs/c.1/mass_storage.msg').symlink_to(root/'gadget/storage_demo/functions/mass_storage.msg')
    (root/'log/q2-demo.log').write_text(''.join(f'line {i}\n' for i in range(400)))
    (root/'log/q2-demo.err').write_text('q2player: started\n')
    mounts = root/'mounts'
    for old, new in (('/mnt/mmc', f'{root}/mmc'), ('/proc/mounts', str(mounts)), ('/sys/class/udc', f'{root}/udc'),
                     ('/sys/kernel/config/usb_gadget', f'{root}/gadget'), ('/tmp/q2-demo', f'{root}/log/q2-demo')):
        command = command.replace(old, new)
    mounts.write_text('')
    assert subprocess.run(['sh', '-c', command]).returncode == 2 and not list((root/'mmc').iterdir())
    mounts.write_text(f'/dev/mmcblk0p1 {root}/mmc vfat rw 0 0\n')
    assert subprocess.run(['sh', '-c', command]).returncode == 0
    assert [p.name for p in (root/'mmc').iterdir()] == [pathlib.Path(FILE).name]
    text = (root/'mmc'/pathlib.Path(FILE).name).read_text()
    heads = [line for line in text.splitlines() if line.startswith('== ')]
    assert heads == ['== version', '== date', '== uptime', '== udc', '== gadgets', '== mounts', '== battery (CW2015, used; AXP2101, unused)', '== ps', '== dmesg',
                     '== q2-demo.err', '== q2-demo.log (demo and q2player)']
    assert f'{root}/udc/13500000.otg/state: configured' in text and 'UDC=13500000.otg' in text
    assert 'mass_storage.msg' in text and 'lun.0/file: /dev/mmcblk0p1' in text and 'q2player: started' in text
    assert 'line 399' in text and 'line 99\n' not in text  # the log's last 300 lines
    assert ' dev ' in text.split('== version\n')[1].split('\n')[0]  # the build, a --dev one's
