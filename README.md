# Audeze Maxwell — Firmware Reverse Engineering

A reference for the **Audeze Maxwell** (original, not V2) firmware internals:
how the audio pipeline works, why many units have an L/R balance issue, and a
working recipe for flashing custom firmware.

Audeze has not shipped a firmware update for the original Maxwell in roughly
two years. The current firmware, **v1.0.1.74**, left many units with an
audible left/right loudness imbalance. This repo documents the
reverse-engineering done to understand *why* — and what can be done about it.

> **The tool:** [**maxwell-balance-and-firmware-tool**](https://github.com/kats1123/maxwell-balance-and-firmware-tool)
> — a pre-built Windows app, built from this research, that measures and
> corrects the L/R balance, bakes the fix into a custom firmware, and
> flashes / downgrades firmware.

---

## The L/R balance issue — what we found

**The symptom.** Many original Maxwells play one channel louder than the
other. It is usually the right side, the severity varies from unit to unit,
and it is often *different* between USB-C and the wireless dongle. There is no
balance control in the Audeze app, so an affected user has no built-in way to
correct it.

### Our best reconstruction of how it got this way

The firmware shows that Audeze **built a per-source balance/correction system,
then removed the master event router that drove it**, leaving the rest as
orphaned dead code. Updated narrative based on the May 2026 deep dive — the
key change from earlier drafts is that we now have **positive evidence of
removed code**, not just an "unfinished hookup" inference:

1. **They corrected per source.** The firmware was designed to apply a
   *different* audio correction depending on whether you listen over USB-C or
   wirelessly — because the two paths measurably differ, and Audeze evidently
   knew the USB-C path needed per-channel (left ≠ right) correction.
2. **They built the runtime switching machinery.** The firmware contains a
   complete event-routing infrastructure: a state handler that reads `0xF702`,
   compares it to a `0xF703` last-applied memo, runs per-source cleanup, and
   updates F703 to match. The state handler is reached via a 22-entry dispatch
   table at `0x081C3134`. Per-source helper functions, event-bus keys
   (`0xE42A`, `0xEE23`), and a cluster of per-state config keys (`0xF704`
   through `0xF707`) all exist as built code.
3. **They removed the master event router.** The dispatch table at
   `0x081C3134` has **zero loaders** in the entire firmware — no `ldr`
   instruction anywhere references its base address, no literal pool contains
   it, no fn-pointer table points at it. The 22 dispatcher functions and the
   state handler are reachable only via the table; with no driver, they sit
   in flash as orphaned dead code. Adjacent clusters of `bx lr` stubs at
   `0x081567CC` (6 in a row) and `0x0820D264` (20+ in a row, several
   hardcoding `return 0` or `return 1`) are the empty shells of removed
   state-query functions — the exact pattern of "we deleted the logic and
   left the stubs as table slot-fillers."
4. **`0xF702` became a frozen factory value.** With the runtime driver gone,
   nothing in the firmware ever writes `0xF702` in normal operation
   (verified: `0xF702` has exactly one writer, the RACE `0x0900 sub 0x2F`
   host-command handler; factory reset doesn't touch it; the Audeze app,
   dongle pairing, and Bluetooth connection don't write it either). Whatever
   Audeze's QC bench wrote at end-of-line is what the headset is stuck on
   for life.
5. **The factory provisioning varies.** Some units shipped with `0xF702 = 0x0A`
   (USB-C profile — the asymmetric `141/149` balance default plus the
   per-channel-corrected EQ). **Those are the units whose owners hear the
   imbalance**, regardless of how they actually connect, because the per-unit
   hardware doesn't need the per-channel correction the firmware applies.
   Other units shipped with `0xF702 = 0x00` (wireless profile, `147/147`
   symmetric) and never notice anything wrong. The two populations explain
   the observed pattern in community reports: some users complain of
   imbalance, others don't, factory reset never fixes it for either group.
6. **Audeze keeps shipping wireless-profile patches.** v74 rewrote 115 of
   118 entries in the wireless DSP coefficient column (zero entries changed
   in the USB-C column), and the patch notes mention "Fixed a bug that would
   cause EQ issues when updating previous versions of firmware." That work
   is meaningful only if a real population of users is on the wireless
   profile — confirming the factory-provisioning-varies model.

The companion tool fixes both populations of users: a one-shot RACE write
flips stuck units to wireless (the value persists because nothing else writes
F702), and the custom-firmware patch makes the F702 reader always return 0
so the choice is forced regardless of NVDM state.

### The evidence

**A real per-source correction system — that never runs.**

- **A per-source DSP filter bank.** A 550-entry coefficient table at
  `0x082938CE` holds, for every coefficient, *two* left/right pairs — one for
  USB-C, one for wireless. At boot the firmware loads one column into the DSP.
  - The **USB-C column is per-channel-corrected**: 101 of 550 coefficients
    have left ≠ right. An asymmetry that deliberate is a built-in L/R
    correction baked into the USB-C voicing.
  - The **wireless column is symmetric**: only 1 of 550 coefficients differs
    left/right, and ~248 are bypassed entirely — flat and simple.
- **Per-source balance defaults.** Two NVDM keys hold a 4-byte L/R balance —
  `0xF665` for USB-C, `0xF668` for wireless. Factory defaults:
  `0xF665 = (L 141, R 149)` — **asymmetric**; `0xF668 = (L 147, R 147)` —
  **symmetric**. You do not ship an asymmetric factory default by accident;
  it is a deliberate correction.
- **Which profile loads is chosen by NVDM `0xF702`** (`0x0A` = USB-C, anything
  else = wireless), read once at every boot.
- **`0xF702` has exactly one writer** in the firmware: the RACE `0x0900 sub
  0x2F` host-command handler. Verified by exhaustive whole-firmware scan
  across all four NVDM-write functions (`0x814fed8`, `0x814ff40`, `0x814ff68`,
  `0x821f804`), every `movw #0xF702` instruction, and every fn-pointer table.
  No firmware-internal code ever updates it. The Windows Audeze app, the
  dongle, factory reset, and a Bluetooth connection were each tested — none
  write it.
- **The runtime switcher was removed.** A 22-entry function-pointer dispatch
  table at `0x081C3134` contains the state-handler dispatcher (entry 11) plus
  21 other per-event dispatchers. The table has **zero loaders anywhere in
  the firmware** — no ldr instruction loads its base, no literal pool entry
  points at it. The whole event-routing subsystem (table + 22 handlers +
  state handler at `0x081C96F0`) is unreachable orphan code. Nearby clusters
  of `bx lr` stubs at `0x081567CC` (6 consecutive) and `0x0820D264` (20+,
  several hardcoded to return 0 or 1) are removed state-query functions left
  as empty slot-fillers. **This is the smoking-gun evidence that Audeze
  built and then removed the per-source switching system.**
- **`0xF702` is therefore frozen at the factory value** on every shipped
  unit. Some units shipped with `0x0A` (USB-C profile), others with `0x00`
  (wireless). The factory-set value persists for the life of the headset
  unless a host explicitly overwrites it via RACE.

**The runtime balance.** The live L/R gain is a single 4-byte SRAM buffer at
`0x142039AC`, shared by all sources, reloaded from `0xF665`/`0xF668` on every
reboot (the loader is the tail of the boot audio-init that `main()` calls).
RACE protocol commands can read and write this buffer and persist it to NVDM —
that is the runtime correction the tool uses.

**Measured.** On the test unit, with no correction applied (a symmetric
buffer), the headset's own output measured **R louder by 6.8 dB on USB-C** and
**R louder by 2.2 dB on the dongle** — same direction, very different
magnitude. A single correction value cannot fix both; the two paths genuinely
need different corrections.

**Versions.** Firmware `v1.0.1.61`, `.63` and `.74` are byte-for-byte
identical in the source-state and balance code. Nothing changed there across
the three.

### What's evidenced, and what's still open

- **Evidenced:** the per-source correction system exists; its USB-C profile is
  per-channel-corrected and its wireless profile is symmetric; `0xF702` has
  exactly one writer (the RACE host-command handler) and is never updated
  by the firmware itself in normal operation; the master event dispatcher
  that would have driven runtime switching has been removed (table at
  `0x081C3134` has zero loaders, surrounding stubs at `0x081567CC` and
  `0x0820D264`); two field-tested units both read `141/149` (= `0xF702 = 0x0A`,
  USB-C profile) and complain of imbalance.
- **Evidenced (v74 patch notes alignment):** v63 → v74 changed 115 of 118
  entries in the wireless DSP coefficient column and zero in the USB-C
  column. v74 release notes mention "Fixed a bug that would cause EQ issues
  when updating previous versions of firmware." Audeze actively maintains
  the wireless profile, which only makes sense if a real population of
  shipped units is on it — corroborating the factory-provisioning-varies
  model.
- **Inference:** *why* Audeze removed the master driver — unclear from the
  firmware alone. A bug they couldn't fix in time, a refactor that wasn't
  finished, or a deliberate de-scoping for QA reasons are all consistent.
  The orphaned-code pattern alone doesn't distinguish between these.
- **Still open — the exact factory-provisioning logic.** We've established
  that `0xF702` is a frozen factory value, but not what determines whether
  any given unit gets `0x00` or `0x0A`. Candidates: per-SKU defaults that
  vary by production line, per-batch QC-bench differences, or the AB1568
  boot ROM doing hardware-detect on first power-on. Without access to a
  brand-new sealed unit (read F702 before any host activity) or the boot
  ROM image, we can't disambiguate.

### The practical fix

The companion tool fixes both populations of users:

1. **A one-shot RACE write** ("Set Audio Source" button) sets `0xF702 = 0x00`,
   moving the headset to the wireless profile. Persists for life because
   nothing in the firmware ever overwrites it.
2. **The custom firmware** also patches the F702-reader function
   (`FUN_0x0817B2F4`, 4-byte patch: `07 B5 FF 23` → `00 20 70 47`) so the
   reader always returns 0. After flashing, the firmware ignores `0xF702`
   entirely and the wireless profile is pinned regardless of NVDM state.
3. **Per-unit balance correction** continues to write both NVDM keys
   (`0xF665` *and* `0xF668`) to the user's calibrated symmetric values,
   so balance is correct regardless of which profile happens to be active.

Full detail in **[AUDIO.md](AUDIO.md)**.

---

## Custom firmware IS flashable

Earlier versions of this writeup said custom firmware was blocked by ECDSA
signature verification in a bootloader ROM. **That was wrong** — there is no
asymmetric signature on the Maxwell firmware. The bootloader only performs
**SHA-256 integrity checks**, which are keyless and recomputable.

Three things a valid modified firmware must update:

1. **TLV `0x0014`** — per-partition SHA-256 hashes (4 × 32 bytes). All four
   must be recomputed when partition content changes.
2. **TLV `0x0011`** — the LZMA stream length; recompressing changes it.
3. **The outer SHA-256** over `file[0x100:]`.

With all three correct, a modified firmware passes verification and boots
normally. The Maxwell is also **dual-bank** — a flash writes the inactive bank
and the device swaps on reboot — so a failed flash should fall back to the
previous firmware. See **[FLASHING.md](FLASHING.md)** for the full recipe.

---

## Document map

| Document | What's in it |
|----------|--------------|
| **[AUDIO.md](AUDIO.md)** | Audio pipeline, the per-source balance system, gain registers, EQ — **the full L/R balance investigation lives here** |
| **[FIRMWARE.md](FIRMWARE.md)** | File format (LZMA-Alone), chip (AB1568), memory map, how to decompress and analyze |
| **[PROTOCOL.md](PROTOCOL.md)** | RACE protocol, HID interface, command dispatch table, NVDM key reference |
| **[VERSIONS.md](VERSIONS.md)** | Byte-level differences between v1.0.1.63 and v1.0.1.74 |
| **[FLASHING.md](FLASHING.md)** | FOTA process internals + working custom-firmware flash recipe |
| **[PATCHES.md](PATCHES.md)** | Cookbook of known custom-firmware patches (L/R balance, concurrent playback) |
| **[BOOTLOADER.md](BOOTLOADER.md)** | First-stage bootloader (in flash, not ROM), TLV parsing, integrity checks |
| **[COMMUNITY.md](COMMUNITY.md)** | Open questions, suggested paths forward, tools used |

---

## Status of this research

| Area | Status |
|------|--------|
| Firmware decompression | ✅ Solved (LZMA-Alone, no encryption) |
| Memory map / chip identification | ✅ Solved (Airoha AB1568, Cortex-M4F, code at `0x08000000+`) |
| Firmware file TLV format | ✅ Mapped (basic_info, mover, sha, version, chip ID) |
| RACE protocol structure | ✅ Mapped (dispatch table, sub-commands, NVDM keys) |
| Bootloader (first-stage in flash) | ✅ Dumped (76 KB) + partially decoded |
| Audio mixer architecture | ✅ Mapped (gain registers, channel selectors, EQ swap) |
| L/R balance — firmware mechanism | ✅ Mapped (per-source profile system, NVDM keys, runtime buffer, loader) |
| L/R balance — per-unit root cause | ❌ **Open** — the per-source system is inert and constant; the true cause of the per-unit, intermittent variance is unidentified |
| Runtime control via RACE | ✅ Working (read/write L/R balance, EQ, chatmix; persists to NVDM) |
| Custom firmware flashing | ✅ **Working** (TLV `0x0014` + TLV `0x0011` + outer SHA-256 recipe) |
| Concurrent playback (USB-C + BT) | 🟡 Partial — one NOP at `0x135C66` enables some source combos; BT↔dongle needs more |
| Boot ROM (the true ROM at `0x00000000`) | ❌ Unread — RACE memory reads to `0x0` crash the chip; likely a tiny IPL stub |

---

## Hardware

| | |
|--|--|
| **SoC** | Airoha AB1568 (= MediaTek MT2822) |
| **Core** | ARM Cortex-M4F, CPUID `0x410FC241` (r0p1 + FPU), confirmed live |
| **SDK** | Airoha IoT_SDK_for_BT_Audio v3.4.1 |
| **Flash** | 8 MB SPI XIP at `0x08000000`–`0x087FFFFF` |
| **First-stage bootloader** | In flash at `0x08000000`–`0x08013000` (~76 KB, dumpable) |
| **Active firmware bank** | Maps to flash `0x08013000`+ |
| **FOTA partition** | Flash `0x084A1000`–`0x087F5000` (~3.4 MB inactive bank) |
| **SRAM** | `0x14000000`+ (audio buffers, mixer state, the `0x142039AC` balance buffer) |
| **TCM (fast RAM)** | `0x04000000`–`0x0402C000` (vector table + hot-path code) |
| **Audio HW regs** | `0x42000000`–`0x4200FFFF` (memory-mapped I/O; reads crash the chip) |
| **Drivers** | 90 mm planar magnetic |

---

## License

The documents in this repo are licensed **CC BY 4.0** (Creative Commons
Attribution). You are free to use, share and adapt them for any purpose,
including commercially — just credit this repo.

This is not affiliated with, endorsed by, or sponsored by Audeze LLC.
"Audeze" and "Maxwell" are trademarks of their respective owners.
