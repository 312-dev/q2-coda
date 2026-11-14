# Backlog

Work planned for q2-coda once the security and efficiency branches have merged, in rough order.
Each item names where its plan or findings are. Remove an item when it lands.

| Item | Plan or findings | Notes |
|---|---|---|
| Efficiency fixes | [inefficiencies.md](inefficiencies.md), and a per-entry plan with implementation briefs being written | Fixes run as separate branches in the order the plan sets, at most four at once |
| OLED theme | [oled-design.md](oled-design.md) | The engine work (fourth theme, font table with Inter, own accents, selection shape) is in; the theme itself is a placeholder that looks like Classic |
| EQ: measure the DSP's cost | [internals.md: Parametric EQ](internals.md#parametric-eq) | Count instructions per sample for 10, 20 and 30 bands at 48 and 192 kHz before changing anything. Candidates once measured: block-wise processing per band, `-O2` for the DSP file, per-block instead of per-sample branches, a precomputed crossfade step |
| EQ: presets as Equalizer APO text on the card | [internals.md: Parametric EQ](internals.md#parametric-eq) | Saved presets as `EQ/<name>.txt`, the folder imports already use, with Q2-only settings (name, Auto preamp, balance) in `# q2:` comments that other tools ignore; the active EQ stays on internal storage; existing binary presets written out once and kept for downgrades; export follows for free |
| EQ: more APO filter types | | Low-pass, high-pass, notch, band-pass and all-pass biquads, so more community files load; AutoEQ's `GraphicEQ:` fitted to bands or refused clearly |
| EQ: findings of a comparison with other players' EQs | Pending | Rockbox, Equalizer APO, AutoEQ, EasyEffects, CamillaDSP and FFmpeg compared with Q2 Pod's EQ; its ranked improvements are added here when it is done |
| Server library (Local + Server) | [server-library.md](server-library.md) | A card-app catalogue extension; about three to four weeks of firmware and one week in the Subsonic app |
| Desktop companion | | A paired, PIN-authenticated service, off by default, with a narrow API (EQ presets as APO text, library scan, now playing, playlists), never a shell. Needs its own design |
| Feature gaps | [feature-gaps.md](feature-gaps.md) | Save Queue as Playlist, Crossfeed and Smart Shuffle are the quick wins |
| Pre-existing check failures | | `test/patch.py` stops at its PAINT_BOUND instruction count (about 5.18 M against 5.1 M) and `test/build.py` at `scrsaver_playcover.png`'s corner alpha, on unmodified code too; trace why on this machine's toolchain before changing either limit |
