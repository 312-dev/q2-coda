# STATUS: fix/uac1-hid-interface

Goal: with c_chmask=0 the UAC1 module takes interfaces 0 (AC) and 1 (AS IN) only, so the HID in configuration 2
is interface 2 (BMW 2026-11-20 stalled GET_DESCRIPTOR(HID) i0002 in f_uac1's setup).

- [x] branch from 290b5fcf
- [x] upstream 254cb1e0d78c (and its parent a59c68a6a3d1) as tools/uac1_module/patches/, applied by uac1_build.py
- [ ] test/uac1_bind.py: bind the built .ko under Unicorn, parse the fs descriptors; red on old .ko
      (copy at scratchpad uac1fix/old-usb_f_uac1.ko), green on new
- [ ] rebuild (blocked while /mnt/c < 25 GB free; run through q2-heavy, which waits)
- [ ] docs/iphone-mode.md, q2-iap-probe.sh comment, source-map, gate.py SCRIPTS
- [ ] runbook step in ~/q2-emu/design/car-experiments-runbook.md
- [ ] push
