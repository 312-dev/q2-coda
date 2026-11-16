# Hot swap discovery

Can a rebuilt payload be written into a running emulated `demo` in two or three seconds, keeping its state, instead of `tools/ci/q2live.py`'s restart and route replay (17.8 s, [building: Live reload](building.md#live-reload))? A spike on 2026-11-16, on branch `infra/q2-hotswap-mem`, with one pool emulator (slot 1, fast boot), qemu-mipsel 8.2.2 (Ubuntu 24.04's `qemu-user-binfmt`). The spike's tool is `tools/ci/memswap.py`; its guest side is `hotswap_poll` in `patch/navigation.c` (a `--dev` build only), called first in `ringnav_sleep`.

**Answer: yes.** A one-constant painter change went from save to screen in 3.7 s, 0.15 s of it the swap, on the page it was on (Settings, scrolled, its row selected). The build is the rest. The limit is layout: a swap that moves a function something still points at crashed `demo` within 2 s. The design below keeps the swap to edits that move nothing live, and falls back to `q2live.py`'s restart for the others.

## 1. Where the payload is in the qemu process

qemu-user maps guest address G at host G + `guest_base`. For our `demo`, `guest_base` is **0**: `/proc/PID/maps` shows `/release/bin/demo` mapped at `00b00000` from file offset `0x6a0000`, the payload's `PT_LOAD` (`readelf -l demo`: offset `0x6a0000`, vaddr `0xb00000`). Reading `0xf7d58` bytes at host `0xb00000` through `/proc/PID/mem` gave the build's payload with 0 words different. `memswap.py` does not assume 0: `guest_base()` takes the mapping of the payload's file offset and subtracts `0xb00000`, and `info` checks the bytes.

The pid is ours by `demo_pid()`: each `demo` qemu's parents are walked up to the bwrap whose `--bind` is our copy's `rootfs` (from `tools/ci/gdbrsp.py` on `infra/q2-hotswap`), never by command line, which every copy's `demo` shares.

## 2. Stopping and writing

**`/proc/PID/mem` works without ptrace.** Yama is at `ptrace_scope` 1 (descendants only), and the same open of a same-uid process outside a namespace, not our descendant, fails with `EPERM` (measured). It works on `demo` because bwrap's `--unshare-user` puts qemu in a user namespace our uid owns, so we hold `CAP_SYS_PTRACE` over it, the exception Yama allows (`yama_ptrace_access_check`, `security/yama/yama_lsm.c`; the kernel source was not re-read for this). The write goes through even to pages qemu made read-only: `/proc/PID/mem` writes with `FOLL_FORCE`.

`Stopped` in `memswap.py` sends `SIGSTOP` to the process, waits until every thread in `/proc/PID/task` is in state `T`, and sends `SIGCONT` after the write. Measured over the whole payload text and rodata (`bench`, 1,015,112 bytes, unchanged bytes):

| Run | All 27 threads stopped | Write | Paused in all |
| --- | --- | --- | --- |
| 1 | 2.30 ms | 2.62 ms | 5.20 ms |
| 2 | 2.56 ms | 0.49 ms | 3.19 ms |
| 3 to 5 | 1.1 to 1.8 ms | 0.26 to 0.44 ms | 1.4 to 2.7 ms |

`demo` kept running and took wheel input after five of these. A real swap writes only the changed runs: a 3,248-byte change took 66 ms to stop and write, most of it Python start-up and reading the two `demo` files.

## 3. qemu keeps running the old code until told

**Shown:** `hotswap_poll` stores, each UI loop pass, an immediate from its own code (`li $a0, 0x1111`) to `hotswap_cell[5]` and a word of a string literal (`"HS01"`) to `hotswap_cell[6]`. After `poke` rewrote both in memory, cell 6 read `HS02` on the next pass, but cell 5 stayed `0x1111` for as long as watched (2.5 s, a hundred-odd passes), though memory held the new instruction. Data is read from memory every time; code runs from qemu's translation blocks (TBs) until they are invalidated.

**Why, from qemu 8.2.2's source:**

- Translating code from a page calls `tb_lock_page0` → `page_protect` (`accel/tcg/internal-target.h`, `accel/tcg/user-exec.c` `page_protect`), which, for a page the guest may write, clears `PAGE_WRITE` and `mprotect`s the host page read-only. In `/proc/PID/maps` the payload is a patchwork of `r--p` (pages with TBs) and `rw-p` pages: the payload's `PT_LOAD` is RWX (`append_payload` in `tools/build.py`), so every page is guest-writable.
- A guest store to such a page faults; the SIGSEGV handler calls `handle_sigsegv_accerr_write` → `page_unprotect`, which drops that page's TBs (`tb_invalidate_phys_page_unwind`, `accel/tcg/tb-maint.c`) and makes it writable again. This is qemu's self-modifying-code detection.
- A `/proc/PID/mem` write is the kernel's, not a store by the process, so it never faults: qemu does not learn of it, and the TBs stay. After the poke the page was still `r--p`.

**Two triggers, both made by the guest, both measured working** (`inval`, cell 2 picks the way; time is from the host's request to the hook's answer, which includes waiting for the next UI pass):

| Way | What the hook does | qemu path | Measured |
| --- | --- | --- | --- |
| 1 | a store of each payload page's first word back to itself | each store to an `r--p` page faults → `page_unprotect` → that page's TBs dropped | 21.6 ms |
| 2 | `mprotect(text, R+X)` then `mprotect(text, R+W+X)` (syscall 4125 through `cf_syscall`) | `target_mprotect` (`linux-user/mmap.c`) → `page_set_flags` → `pageflags_set_clear`, which flushes when a region holding code "removes exec, or adds write" → one `tb_invalidate_phys_range` over the whole range | 2.2 to 24.8 ms, both calls return 0 |

Way 2 is the one to keep: two syscalls, one range flush, and the payload never loses exec, so the code asking for it can live inside the range it flushes (dropping exec instead, `R+W`, would also flush but fault on the hook's own next instruction). Only `target/i386` and `target/s390x` define `TARGET_HAS_PRECISE_SMC`, so on MIPS `tb_invalidate_phys_page_unwind` never stops the TB that is running; way 2 does not depend on it, as each `syscall` already ends its TB and the next instruction is looked up afresh.

The hook then calls `widget_invalidate_force(window_manager(), 0)`, so every painter runs again with the new code without an input event.

## 4. Safety

### What the layout pins

A swap is safe when nothing that still holds an address into changed code reaches it. Measured on a booted `--dev` build (`ptrs`, `stacks`, `uistack` and `risk` in `memswap.py`):

| Holder | Count | Effect of a move |
| --- | --- | --- |
| stock code jumping into the payload (hooks and patched calls: `j` or `jal` in stock's text with a payload target) | 227 sites, 176 entry points (`HOOKS` has 111 rows) | in `demo`'s file, so a swap that also writes stock's text from the new `demo` follows them (the spike's `swap` writes the payload only) |
| payload function entries held in writable memory (AWTK handlers, timers, `pthread_create` arguments, tables built at run time) | 57 words, 30 functions (`library_search_open` 14, `set_open` 6, `clock_due`, `tone_worker`, ...) | dangling: called later at the old address |
| words pointing into payload `.rodata` from writable memory (widget texts, tables) | 1,094 | read the wrong bytes if `.rodata` moves |
| threads parked inside payload functions (return addresses near the top of their stacks): the encoder (`wheel_sample`, `ringnav_ts_read`, `ringnav_queue_event`), the sleep hooks of stock threads (`ringnav_dark_usleep`, `ringnav_dark_cover_usleep` twice), `ringnav_baidu_poll`, `ringnav_tidal_poll`, `tone_worker` (under `logo_sync`) | 7 threads besides the UI thread | return into whatever is now at the old address |

`.rodata` follows `.text` (`patch/link.ld`), so a change in size moves it unless an object's alignment padding absorbs the change: a 4-byte growth in `ringnav_paint_bg` moved 244 functions, every one after it in `navigation.c`, and nothing past `navigation.o`, whose 16-byte alignment took the slack; `.rodata` did not move.

**The unsafe case, run:** swapping a build in which 244 `navigation.c` functions moved by 4 bytes (114,169 bytes in 303 runs, among them `set_open`, `tone_worker` and `ipod_list_layout`, held as pointers, and `ringnav_dark_usleep`, `ringnav_dark_cover_usleep` and `tone_worker`, on parked threads' stacks) ended with `qemu: uncaught target signal 10 (Bus error)` within 2 s. A move of only `switch_mark`, `grip_mark`, `lift_card_paint` and `check_circle` (8 bytes, reached only by direct calls the same swap rewrote) was fine, and the toggles they draw painted.

`memswap.py risk COPY OLD NEW` is that check: it said safe for the battery change (10 functions changed, none held as a pointer or on a stack) and unsafe from the current base to the build that crashed (2,839 functions differ, as the base's hook has grown since; 30 of them held as pointers, 33 with return addresses on stacks). Its stack half scans whole stacks, dead frames too, so it refuses more than it must: with `navigation.c`'s functions moving it flags `ringnav_wm_paint` and `bar_status`, which are stale slots, not frames (see the idle point below).

So **a fixed jump table is needed** for anything other than edits that keep every function in place, and it is not enough on its own: the parked threads' frames and `.rodata` pointers also pin code.

### The idle point

**The UI thread is at a safe point there.** `hotswap_poll` records its `$sp` (cell 8), and `memswap.py uistack` walks the frames from it, each frame's size and return-address slot read from its prologue. On Home and two pages deep (Settings, then Controls) the chain was the same:

```
#0 hotswap_poll   (payload, frame 40)
#1 ringnav_sleep  (payload, frame 1440; stock calls it at the main_loop_sleep_default hook)
#2 0x69e960       (stock, the main loop)
#3 0x476268       (stock, main)
#4 0x2ca061fc     (libc, __libc_start_main)
```

The only payload frames are the hook's own two, so the swap must leave `hotswap_poll` and `ringnav_sleep` in place and unchanged, or return through a fixed stub (below). A flat scan of the same stack finds payload return addresses higher up (`ringnav_wm_paint`, `bar_status`, `coverflow_home`), but those are stale slots inside stock's frames, not frames. The payload runs no nested main loop (no `dialog_modal`), so the idle point is never inside payload code. A stock page that runs one (a stock modal dialog) was not checked.

**Other threads are not.** Of 27 threads, 7 besides the UI thread run payload code and sit in it while they block (table above). They are not stopped at a known point and cannot be: the swap leaves them alone, so the functions on their stacks must not change. The conservative scan found no payload return address on any other stack (41 candidate stack regions, 8 with any: the UI thread's and these 7).

## 5. End to end

A size-neutral painter change, `battery_slim`'s full-charge ink `0xffffff` to `0x30d158` (`patch/list_marks.c`), on the running base build at Settings, list scrolled, Battery selected:

| Step | Time |
| --- | --- |
| `tools/build.py` with `Q2_DEMO_ONLY=1`, ccache warm (as `q2live.py`'s `demo_build`) | 3.45 s |
| `memswap.py swap ... 2`: diff, stop, write 13 runs (3,248 bytes), continue, way 2, repaint | 0.145 s (stop and write 66 ms, invalidate 15 ms) |
| screenshot | 0.10 s |
| **save to screen** | **3.7 s** |

The battery turned green on the same page, scroll and selection kept. Two more cycles on a later boot, the change and then its revert, took 3.55 s and 3.34 s (build 3.18 and 3.03 s, swap 0.21 and 0.18 s); the battery's fill read `(48, 209, 88)`, `0x30d158`, in the first screenshot and white in the second. The same flow with a 2-byte change (an `sltiu` immediate in `ringnav_paint_bg`) wrote one run. The first demo-only build in a fresh directory took 14.4 s (ccache cold for it).

## Recommended design

1. **Guest side, `--dev` only:** a hook at the top of `ringnav_sleep` (the spike's `hotswap_poll`) that, when the host bumps a request cell in `.scratch`, runs way 2 over the payload's text and rodata and repaints. It runs on the UI thread at stock's idle point, where its frames are the only payload frames live on that thread.
2. **Host side, in `q2live.py`:** after the demo-only build, diff old and new `demo`. Swap when the change is safe, else restart as now. Safe means, measured on the live process at the stop: no function moved or changed whose entry is held in writable memory or that holds a live return address on any thread's stack (from each thread's sp; the spike's scan is conservative and also counts dead frames), and `.rodata` did not move or every pointer into the moved part is gone. Write stock's text too, from the new `demo`, so hooks follow a moved target.
3. **Make "safe" the common case:** in `--dev` builds, put `.rodata` at a fixed address above the text with slack (link.ld), and pad each object's text (or each function, `-falign-functions` with a large value) so a small edit is absorbed in place; route the payload's registered callbacks (`timer_add`, `widget_on`, `pthread_create`) through a fixed table of stubs at the start of `.text`.
4. **What a swap cannot do:** change `.scratch`'s layout (state keeps its old addresses: a new or resized field needs a restart), change stock's data, or re-run init code (a page already open keeps the widgets its `*_page_init` made).

Effort, estimated: 1 and 2 without 3 is about a day (`memswap.py` is most of it; the safety check needs per-thread sp, which `hotswap_poll` gives for the UI thread and the others need either the gdbstub or a conservative scan). 3 is another one to two days, mostly link.ld and a callback table, and is what decides how often a save takes the 3.7 s path instead of 17.8 s.
