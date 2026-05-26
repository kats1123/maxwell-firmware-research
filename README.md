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

### What's actually happening

The L/R imbalance is a bug Audeze introduced in `v1.0.1.61`. Pre-v61
firmware doesn't have it. This is the v56 → v61 → v74 timeline established
from the cross-version binary analysis and empirical hardware testing:

1. **v1.0.1.56 (and earlier) — no per-source machinery exists.** v56 has a
   single balance NVDM key (`0xF668`) with factory default `142/142`
   (symmetric). No `0xF665`, no `0xF702`, no `0xF703`-`0xF707`, no state
   handler, no dispatch table — none of it exists in v56 as a literal
   reference anywhere in the firmware. **The L/R imbalance bug literally
   does not exist in v56.** This is why users who roll back to v56 report
   the imbalance disappears: there is nothing for any unit to be "stuck on."
2. **v1.0.1.61 (April 2024) — Audeze adds a per-source balance system.**
   The new code introduces:
   - A second balance NVDM key `0xF665` with factory default `141/149`
     (asymmetric — a per-channel correction for the USB-C signal path).
   - The original symmetric default `0xF668 = 147/147` stays in place for
     wireless.
   - A selector NVDM byte `0xF702` that picks `0xF665` (when `0x0A`) or
     `0xF668` (otherwise) for the boot balance loader.
   - Per-source helper functions, event-bus keys (`0xE42A`, `0xEE23`), a
     cluster of per-state config keys (`0xF703`-`0xF707`), and a second
     column on the per-source DSP coefficient table (USB-C
     per-channel-corrected EQ; wireless flatter and symmetric).
3. **`0xF702` ends up frozen at its factory value.** The headset reads
   `0xF702` exactly once at boot to pick the active balance. What's missing
   is *anything in normal operation that automatically updates `0xF702`
   when the audio source changes*. The system has the structure of a
   feature meant to switch the profile live (the `0xF703`-`0xF707` cluster
   reads like per-state cleanup memos) but no firmware-internal code path
   ever writes `0xF702`. We disassembled every read and write of the key,
   traced every caller and dispatch table, in both the headset and the
   dongle, across v56/v61/v63/v74. **The only writer of `0xF702` in the
   entire firmware is the RACE `0x0900 sub 0x2F` host-command handler.**
   In normal operation — playing audio, switching between USB-C and
   wireless, plugging and unplugging, factory-resetting — nothing in the
   firmware ever updates `0xF702`.
4. **Different units shipped with different `0xF702` values.** Some
   production units have `0xF702 = 0x0A` and load the asymmetric `141/149`
   default at every boot. **Those are the units whose owners hear the
   imbalance**, regardless of how they actually connect. Other units have
   `0xF702 = 0x00` and load `147/147` symmetric and never notice anything
   wrong. This explains the community-report pattern: some users complain,
   others don't, factory reset never fixes it for either group (factory
   reset doesn't touch `0xF702`).
5. **v1.0.1.63 / v1.0.1.74 carry forward the same architecture.** v74
   rewrote 115 of 118 entries in the wireless DSP coefficient column and
   zero in the USB-C column; the v74 patch notes mention "Fixed a bug
   that would cause EQ issues when updating previous versions of
   firmware." Audeze actively maintains the wireless profile and never
   touches the USB-C asymmetric one. The wireless profile is clearly the
   intended one.

The fix the companion tool provides is therefore principled rather than
heuristic: flip `0xF702 = 0` so the boot loader picks the wireless
(maintained, symmetric) profile on units that shipped on the USB-C
asymmetric one. The write persists — verified by writing a probe value
(`0x55`) and reading it back unchanged after a full power cycle. For
users who want belt-and-suspenders, the custom v61+ firmware also patches
the `0xF702` reader to always return `0`, and the custom v56 firmware is
literally a rollback to the pre-bug firmware with the user's balance
correction baked in.

### Two things we still cannot explain

This is a complete reverse-engineering of how the bug *manifests* and how
to *fix* it on any individual unit. There are two questions about the
broader system that we could not answer from the firmware alone:

* **Is there meant to be an in-firmware auto-switcher for `0xF702`?**
  Audeze almost certainly tested the per-source system internally before
  shipping. Either they intended the Audeze app (or a separate dev/QC
  tool) to drive `0xF702` over RACE, or they intended an in-firmware
  trigger that we could not find. We spent roughly fifty hours looking
  for such a trigger — every NVDM write path, every USB/BT connection
  event handler, every dispatch table, every event-bus key, in headset
  and dongle, across all four firmware versions. We found none. It's
  possible such a path exists in some encoding (computed-pointer
  indirection, runtime-installed handlers in RAM, dongle→headset BT
  vendor messages we don't recognize) that we missed. We can't claim a
  proof of absence; we can only report that an exhaustive search by
  multiple methods turned up nothing.
* **Why do shipped units have different `0xF702` values?** Per-SKU
  defaults set by different production lines, a QC bench that left a
  test-mode value in NVDM on some batches, a manufacturing step that
  wrote the byte on some units and not others — all consistent with the
  community pattern, none provable without access to Audeze's
  provisioning process or a population of brand-new sealed units we
  could survey.

### Earlier write-ups: what was wrong

This section has been rewritten several times as the investigation found
new evidence. The corrections, for transparency:

* A draft said the per-source feature **shipped inert** — that the
  dispatch chain leading to the `0xF702` writer had zero callers anywhere
  in the firmware. That was wrong. The write path is reachable: RACE cmd
  `0x09xx` is registered as handler `0x0817B92C` in the master dispatch
  table at `0x0828A8E0`, and `0x0817B92C` reaches the `0x0817B0E4` SET
  dispatcher (and from there the `0xF702` writer) via a `B.W`
  unconditional branch (tail call), not a `BL` — the earlier static-ref
  search only looked for `BL`/`BL.W` and missed it. Empirically: writing
  `0xF702 = 0x55` and power-cycling the headset, the value reads back
  unchanged. So the host-command path is alive. What is *not* present is
  an in-firmware path that automatically issues that write on its own.
* An earlier draft said Audeze **removed** the master event router
  (treated an orphan-looking 22-entry dispatch table at `0x081C3134` and
  surrounding `bx lr` stub clusters as evidence of removal). The
  cross-version diff against v56 disproved this: v56 has no F702, F665,
  F703-F707, E42A, EE23, F668-via-selector, dispatch table, OR stub
  clusters. The entire system appears for the first time in v61. The
  apparent orphan status of the `0x081C3134` table is also no longer
  reliable evidence on its own; the same kind of B.W tail-call we missed
  for `0x0817B0E4` could equally hide its caller. So that table may or
  may not be live — we can't tell from static analysis.
* A still-earlier draft said "they converged the sources and dropped the
  per-source system" (informed guess). v56 falsifies that too — v56's
  single profile is `142/142` (not `147/147`), and the per-source system
  is added in v61 *with* the new asymmetric default, not removed in favor
  of convergence.

### The evidence

**A per-source DSP filter bank.** A 550-entry coefficient table at
`0x082938CE` holds, for every coefficient, *two* left/right pairs — one for
USB-C, one for wireless. At boot the firmware loads one column into the DSP.
* The **USB-C column is per-channel-corrected**: 101 of 550 coefficients
  have left ≠ right. An asymmetry that deliberate is a built-in L/R
  correction baked into the USB-C voicing.
* The **wireless column is symmetric**: only 1 of 550 coefficients differs
  left/right, and ~248 are bypassed entirely — flat and simple.

**Per-source balance defaults.** Two NVDM keys hold a 4-byte L/R balance —
`0xF665` for USB-C, `0xF668` for wireless. Factory defaults:
`0xF665 = (L 141, R 149)` — **asymmetric**; `0xF668 = (L 147, R 147)` —
**symmetric**. You do not ship an asymmetric factory default by accident;
it is a deliberate correction.

**Which profile loads is chosen by NVDM `0xF702`** (`0x0A` = USB-C, anything
else = wireless), read once at every boot by the balance loader at
`0x081de120`, which is called once from the main boot init at `0x081d9406`
just before scheduler start.

**`0xF702` has exactly one writer in the entire firmware**: the RACE
`0x0900 sub 0x2F` host-command handler at `0x0817B2B2` (sub-id case
`0x2F` of the SET dispatcher at `0x0817B0E4`). Verified by exhaustive
whole-firmware scan across both `movw` encodings of `0xF702`, every
NVDM-write function (`0x814fed8`, `0x814ff40`, `0x814ff68`), and every
function-pointer table, in headset and dongle, across v56/v61/v63/v74.
No other code path writes the key.

**The RACE write chain is alive — verified empirically.** RACE cmd `0x09xx`
is registered in the master dispatch table at `0x0828A8E0` (entry pairs
`{u32 cmd_id_range, u32 handler}`, 8-byte stride; the entry for range
`0x0900..0x09FF` points to handler `0x0817B92C`). `0x0817B92C` dispatches
by exact cmd-id and tail-calls (`B.W`) to `0x0817B0E4` for cmd `0x0900`.
The tail-call is why earlier `BL`/`BL.W`-only static searches found zero
direct callers and concluded the dispatcher was orphan. Empirical
confirmation: writing `0xF702 = 0x55` via the tool, then a full power
cycle of the headset, then reading `0xF702` again returns `0x55` —
unchanged. The host-command write path persists to NVDM.

**No in-firmware code ever issues that write itself.** The host-command
handler is the only writer of `0xF702`, and we found no firmware path that
synthesizes the RACE frame or calls the dispatcher internally. Searched
methods: BL/BL.W direct callers, B/B.W direct callers, aligned and
unaligned 32-bit pointer literals, MOVW+MOVT pair assembling the target
pointer, ADR.W / ADD-PC PC-relative computations, function-pointer
registration sites, and the v56→v61 NVDM-key delta for both the headset
and the dongle. Dongle is byte-for-byte identical across v56/v61/v63/v74
in every reference to RACE cmd `0x0900` and `0x0901`; the dongle was not
changed when v61 added the per-source system. Estimated effort: ~50 hours
of targeted analysis. A path may exist in some encoding we missed — we
can't prove absence — but we couldn't find one.

**Field measurements.** On the test unit, with no correction applied (a
symmetric buffer), the headset's own output measured **R louder by 6.8 dB
on USB-C** and **R louder by 2.2 dB on the dongle** — same direction, very
different magnitude. A single correction value cannot fix both source
paths simultaneously; each path needs its own correction baked into its
own NVDM key.

**Versions.** Firmware `v1.0.1.61`, `.63` and `.74` are essentially
identical in the source-state and balance code — the system was introduced
in v61 and has carried forward with only small tunings since. `v1.0.1.56`,
by contrast, **predates the system entirely**: zero `movw` references to
`F665`, `F702`, `F703`-`F707`, `E42A`, `EE23` exist in v56's code. v56 has
exactly one balance NVDM key (`F668`) with a single symmetric default
(`142/142`). The L/R imbalance bug literally does not exist in v56. The
v56→v61 NVDM-key delta on the headset is exactly: `F006`, `F665`, `F702`,
`F703`, `F704`, `F705`, `F706`, `F707` — these are the new keys that make
up the entire per-source switching surface.

### What's still open

* **Whether an in-firmware auto-switcher for `0xF702` exists at all.** We
  searched exhaustively and didn't find one. Audeze almost certainly
  tested the per-source system before shipping; either they used external
  RACE tooling to set `0xF702` in QA, or they intended an in-firmware
  trigger we couldn't locate. The orphan-looking 22-entry table at
  `0x081C3134` and surrounding `bx lr` stub clusters at `0x081567CC` and
  `0x0820D264` looked at first like proof the trigger was never wired up,
  but the same kind of `B.W` tail-call we missed for `0x0817B0E4` could
  equally hide their caller, so we can't treat their static orphan status
  as proof. The truthful answer is: we couldn't find one, but we can't
  rule one out either.
* **How shipped units end up with one `0xF702` value versus the other.**
  Some have `0x0A`, others have `0x00`. We have no visibility into
  Audeze's provisioning process. Candidates: per-SKU defaults set by
  different production lines, per-batch QC bench differences (a test step
  that wrote `0x0A` and forgot to clear it), a manufacturing step that
  wrote the byte on some batches and not others. Without access to a
  brand-new sealed unit we could read before any host activity, or to
  Audeze's QC tooling, we can't disambiguate.

### The practical fix

The companion tool fixes both populations of users:

1. **A one-shot RACE write** ("Set Audio Source" button) sets `0xF702 = 0x00`,
   moving the headset to the wireless (Audeze-maintained, symmetric)
   profile. Verified to persist across a full power cycle on the test unit
   — we wrote a probe value of `0x55`, fully powered down the headset, and
   on power-up `0xF702` read back as `0x55` unchanged.
2. **The custom v61+ firmware** also patches the F702-reader function
   (`FUN_0x0817B2F4`, 4-byte patch: `07 B5 FF 23` → `00 20 70 47`) so
   the reader always returns 0. After flashing, the firmware ignores
   `0xF702` entirely and the wireless profile is pinned regardless of
   NVDM state. This effectively reduces v74 to a single-path
   architecture — the wireless profile is the only one that ever
   loads, the same way v56 worked before the per-source machinery was
   added.
3. **The custom v56 firmware** is a rollback to the pre-bug firmware —
   no `0xF702`, no per-source switching, no asymmetric default at all.
   Same end result as option 2 architecturally (one profile, one
   balance default, no source-selection logic), just achieved by going
   back rather than patching forward.
4. **Per-unit balance correction** continues to write both NVDM keys
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
| L/R balance — firmware mechanism | ✅ Mapped (per-source profile system, NVDM keys, runtime buffer, boot loader, RACE write path verified live) |
| L/R balance — per-unit root cause | ✅ Mapped (`0xF702` factory value selects asymmetric vs symmetric balance default; nothing in firmware updates it after factory provisioning) |
| L/R balance — in-firmware auto-switcher for `0xF702` | ❌ **Open** — searched exhaustively (~50h), couldn't find one. May exist in an encoding we missed. |
| L/R balance — factory provisioning logic | ❌ **Open** — what determines whether a unit ships with `0xF702 = 0x00` vs `0x0A`. Unknown without access to Audeze's QC process. |
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
