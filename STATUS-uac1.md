# STATUS: fix/uac1-hid-interface

Goal: with c_chmask=0 the UAC1 module takes interfaces 0 (AC) and 1 (AS IN) only, so the HID in configuration 2
is interface 2 (BMW 2026-11-20 stalled GET_DESCRIPTOR(HID) i0002 in f_uac1's setup).

- [x] branch from 290b5fcf
- [x] upstream 254cb1e0d78c (and its parent a59c68a6a3d1) as tools/uac1_module/patches/, applied by uac1_build.py
- [x] test/uac1_bind.py: bind the built .ko under Unicorn, parse the fs descriptors; red on old .ko
      (copy at scratchpad uac1fix/old-usb_f_uac1.ko): RED, "HID is interface 3 ... UAC1 took [0, 1, 2]"; green on new: pending build
- [ ] rebuild: `q2-heavy python3 tools/uac1_module/uac1_build.py` started detached, log ~/.cache/q2-uac1/build-hidfix.log;
      waiting (/mnt/c 24 GB free, needs 25). Then: test/uac1_bind.py on the new .ko; check doc's '46 symbols' count
- [x] docs/iphone-mode.md, q2-iap-probe.sh comment, source-map, building.md, gate.py SCRIPTS
- [x] runbook step (section 'Desk check after the 2026-11-20 drive') in ~/q2-emu/design/car-experiments-runbook.md
- [ ] push
