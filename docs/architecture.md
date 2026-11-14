# Architecture

How the pieces fit: what a build makes, where the payload lives in stock's `demo`, how its C reaches stock's functions, and where to start for each kind of change. [internals.md](internals.md) explains each feature; [source-map.md](source-map.md) lists every file.

## What is patched

Q2 Pod ships no program of Shanling's and no source of theirs. `tools/build.py` takes the stock V1.36 ZIP, checks its SHA-256 (`ZIP_SHA` in `tools/build.py`, compared in `build()`), changes these files inside its `rootfs.squashfs` and packs `update.tar` again. The kernel (`xImage`) is byte-identical.

| File on the Q2                                | Change                                                                  | Where                                                          |
| --------------------------------------------- | ----------------------------------------------------------------------- | -------------------------------------------------------------- |
| `release/bin/demo` (the UI)                   | hooks, single words, and the payload mapped at `0xb00000`               | `build()` in `tools/build.py`; `patch_code` in `tools/ipod.py` |
| `usr/bin/hciplayer` (the player)              | the PEQ filter, gapless, crossfade and FLAC paths, mapped at `0xe20000` | `patch_player` in `tools/peq.py`                               |
| `usr/bin/bluealsa`                            | AAC capability, DROP handling, silence through a skip                   | `patch_bluealsa` in `tools/build.py`                           |
| `usr/lib/libtag_c.so.2.0.1`                   | M4B covers                                                              | `patch_m4b_cover` in `tools/build.py`                          |
| UI assets, styles, strings and icons          | rewritten in place from the audit in `patch/ipod.json`                  | `patch_asset` in `tools/ipod.py`; `tools/art.py`               |
| `etc/init.d/S90play`, `S11logo_display_shell` | dual boot, the crash log, the splash                                    | `BOOT_HOOK`, `LOGO_HOOK` in `tools/build.py`                   |
| added programs                                | `q2video`, `q2boot` and, in `--dev`, `q2remote`                         | `compile_helper` in `tools/build.py`                           |
| kernel modules (`--dev` flags only)           | `soc_aic.ko`, `soc_rotator.ko`, the PWM pair                            | `tools/aic.py`, `tools/rotator.py`, `tools/pwm.py`             |

## The payload

The payload is one MIPS o32 image linked by `patch/link.ld`: `.text` and `.rodata` from `0xb00000` (`BASE`), and `.scratch`, zero-filled writable state, at `0xc00000` (`SCRATCH`). `append_payload` (`tools/build.py`) maps it through demo's last, unused `PT_NULL` program header. A variable that must keep its value goes in `.scratch`: `static int x __attribute__((section(".scratch")));`, as `st`, the UI's shared state (`scratch_t` in `patch/navigation.h`), does.

`check_layout` (`tools/build.py`) holds the two limits: the code and constants fit the 1 MB below `SCRATCH`, and the state fits 128 KB. demo's heap begins where the scratch state ends, so `SCRATCH` can move up without colliding with anything of stock's.

It is compiled from two lists, and a new file in neither is not part of it:

- **`NAVIGATION`** (`tools/build.py`): `navigation.c` and the files split out of it. They include `patch/navigation.h`.
- **`UI_SOURCES`** (`tools/peq.py`): the features that stand apart (Cover Flow, the EQ page, Spotify, card apps, Books, Games and the rest). They include `patch/peq.h`.

The player's payload is a third list, in `compile_common` (`tools/peq.py`): `peq.c`, `peq_player.c`, `mp3_gapless.c`, `lavf_gapless.c` and the chosen `flac_*.c`. `peq.c` is in both programs.

### Generated headers

A build writes these into `--out`, and the C includes them from there (`-I out`). None is in the repository.

| Header                   | Written by                            | Holds                                                                                              |
| ------------------------ | ------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `stock.h`                | `stock_header` in `tools/build.py`    | the version strings, `THEME_N`, `THEME_KEYS`, the fonts' ranges, the time zones, stock's data      |
| `peq_platform.h`         | `compile_common` in `tools/peq.py`    | each libc import as `peq_lib_<name>`, each stock function in `FUNCTIONS` as a macro at its address |
| `themes.inc`, `themes.h` | `emit` in `tools/theme.py`            | the theme table from `assets/themes/*/theme.xml`                                                   |
| `trampoline.S`           | `compile_payload` in `tools/build.py` | `stock_<name>_trampoline` for each hook that resumes stock                                         |

`test/fw/host.py` writes the same headers for this machine, from the same functions, so a constant added to `stock_header` reaches the host tests too.

### Calling stock

- **A stock function with a symbol:** add its name and C signature to `FUNCTIONS` (`tools/build.py`). `function_macros` (`tools/peq.py`) turns it into a macro that calls demo's address, read from the stock symbol table at build time.
- **A stock function with no symbol:** add its address to `PRIVATE_FUNCTIONS` (`tools/build.py`) and its name to `tools/stock_map.tsv`, which [rebasing](rebasing.md) carries to the next stock version.
- **A libc function:** `LIBC` (`tools/peq.py`). The call goes through demo's own GOT slot; a name demo does not import is left a plain `extern`, which the payload must define itself.
- **A stock global or a widget's field:** `patch/offsets.inc`, which the test mocks parse too.

### Hooks

A hook is a row of `HOOKS` (`tools/build.py`): `'stock_name': (address, 'replacement', 'trampoline' or None)`. The build checks the symbol's address and its PIC prologue, then writes a jump to the replacement over it (`build()` in `tools/build.py`). With a trampoline name, `stock_<name>_trampoline` sets `$gp` and resumes stock after the prologue, so the replacement can call the original; with `None` the replacement stands in for it. A function with no PIC prologue is a `*_LEAF` tuple in `tools/build.py` (`WM_PAINT_LEAF`, `GRADIENT_LEAF`, `QUEUE_LEAF`), replaced whole.

One call or instruction inside a stock function is changed with `patch_word(data, address, old, new, purpose)` (`tools/ipod.py`): it refuses a word that is not `old`, and the manifest's `compact_code` lists each. The constants above `FUNCTIONS` in `tools/build.py` are these sites. A site that is not a function's start also needs its row in `tools/stock_sites.tsv` (`docs/rebasing/sitenotes.py --check` fails without it).

## The UI thread

demo is AWTK. Everything the payload draws or reads from a widget runs on the UI thread, from these entries:

| Entry                                | Function                                             | At                   |
| ------------------------------------ | ---------------------------------------------------- | -------------------- |
| a key or wheel tick                  | `ringnav`, after `ringnav_input`'s gates             | `patch/navigation.c` |
| a touch                              | `ringnav_touch`, `ringnav_dispatch`                  | `patch/navigation.c` |
| a widget painted, after its children | `ringnav_paint`, which calls each feature's painter  | `patch/navigation.c` |
| a widget's background                | `ringnav_paint_bg` (selection bar, status bar)       | `patch/navigation.c` |
| every pass of the main loop          | `ringnav_sleep`, which calls each feature's `*_poll` | `patch/navigation.c` |
| a stock page opening                 | the page's `*_page_init` hook in `HOOKS`             | `tools/build.py`     |

The wheel's ticks are made on stock's encoder thread (`wheel_sample` in `patch/playback.c`) and posted to the loop. Slow work (art, network, scans) runs on a pthread that touches only files and its own counters, and a `*_poll` on the UI thread takes the result: `patch/coverflow.c`'s head states the rule, and `scrobble.c`, `durations.c`, `playlist_art.c` and `radio.c` follow it.

A page the wheel can move on is named in `patch/contexts.inc`; the build refuses a name that is neither a stock window nor one of `PAYLOAD_WINDOWS` (`tools/build.py`). A page of the payload's own is built with `page_list` (`patch/coverflow.c`).

## Settings

Settings are keys in stock's `/mnt/data/config.ini`, read once and kept in `st`: the theme's under `[IPOD]` (`settings_read` in `patch/theme_engine.c`), the rest under `[Q2POD]` (`pod_keys` in `patch/navigation.c`). `config_value` (`patch/theme_engine.c`) reads one, stock's `write_int_config` saves one. [ipod.md](ipod.md#display-settings) lists the keys.

## Where to start

| To change                         | Start at                                                                                                                                                                                                                                |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| a new feature                     | a new `patch/<name>.c`, in `NAVIGATION` or `UI_SOURCES`; its poll in `ringnav_sleep`, its painter in `ringnav_paint`, its declarations in `navigation.h` under its file's name ([CONTRIBUTING](../CONTRIBUTING.md#where-new-code-goes)) |
| a stock page's rows               | that page's `*_page_init` hook; `ringnav_display` (`patch/navigation.c`) is a worked example                                                                                                                                            |
| a stock page's layout             | `patch_asset` in `tools/ipod.py`, and the page's hash in `patch/ipod.json`                                                                                                                                                              |
| a label                           | `LABELS` (`tools/build.py`), or `ENGLISH` (`tools/build.py`) for a Chinese string                                                                                                                                                       |
| a theme's colours or sizes        | `assets/themes/<id>/theme.xml`, checked by `tools/theme.py` ([Themes](internals.md#themes))                                                                                                                                             |
| a list row's geometry             | `Geometry` (`tools/ipod.py`) and `GEOM_RANGES` in `tools/theme.py`                                                                                                                                                                      |
| what the player does to the sound | `patch/peq_player.c`, hooked from `tools/peq.py`                                                                                                                                                                                        |
| the boot scripts                  | `BOOT_HOOK` (`tools/build.py`), checked by `test/build.py` under the host `sh`                                                                                                                                                          |

## Two host builds of the C

- **`PEQ_HOST`:** the host suites (`test/peq.py`, `test/coverflow.py`, `test/chapters.py`, `test/library_scope.py`, `test/mp3_gapless.py`) compile one or a few files with `-DPEQ_HOST`, which swaps stock's calls for the file's own stand-ins (the `PEQ_HOST` branch of `patch/peq.h`).
- **`test/fw`:** the whole payload as it is for the Q2, with every stock call a recording stub ([building.md](building.md#firmware-tests)).

`test/patch.py` and the scripts that take a `BUILD` run the built MIPS under Unicorn, so they need a build and the stock ZIP.
