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

The firmware shows that Audeze **built a per-source balance/correction system
— and then never wired it up.** Our best-guess narrative, with the parts that
are evidenced and the parts that are inference marked honestly:

1. **They corrected per source.** The firmware was designed to apply a
   *different* audio correction depending on whether you listen over USB-C or
   wirelessly — because the two paths measurably differ, and Audeze evidently
   knew the USB-C path needed per-channel (left ≠ right) correction.
2. **They converged the sources.** The most likely reason the per-source
   system ended up inert is that the paths were brought close enough together
   that Audeze stopped relying on it — the wireless profile shipped symmetric
   and largely flat, and the wireless balance default is left = right.
   *(This step is informed guesswork — see "What's evidenced" below.)*
3. **So the shipped firmware effectively runs one "equal" profile for
   everyone.** The selector that is supposed to pick the USB-C-corrected
   profile is never set, so every unit, on every source, falls back to the
   symmetric profile.
4. **But individual headsets still vary.** Driver and analog-path tolerances
   mean a given unit can still be audibly off — and with the per-source
   correction inert, nothing in the firmware compensates. That residual,
   per-unit imbalance is what the companion tool exists to fix.

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
- **`0xF702` is never set.** An exhaustive whole-firmware scan (BL callers,
  literal pools, raw data) found `0xF702` touched by exactly two instructions:
  a reader, and one writer buried in a RACE host-command handler that only
  runs on an external command. `main()` never sets it; the Windows Audeze app,
  the dongle, and a Bluetooth connection were each tested — none write it.
  With `0xF702` never set it is effectively a constant, so **every unit always
  loads the wireless (symmetric) column — even on USB-C.** The corrected
  USB-C profile Audeze built never runs.

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
  per-channel-corrected and its wireless profile is symmetric; the selector
  `0xF702` is never set, so the corrected profile never loads; real units are
  measurably imbalanced, by different amounts per source.
- **Inference:** *why* the system ended up disconnected — step 2 above. The
  firmware shows the feature half-built, but not whether that was a deliberate
  de-scoping ("we converged the paths, so we dropped it") or simply an
  unfinished hookup. We cannot tell which from the firmware alone.
- **Still open — the per-unit root cause.** The per-source system is a real
  structure, but it is **not** the root cause of the imbalance: it is
  effectively a constant — identical on every unit, every boot — and a
  constant cannot explain a defect that only *some* users get, *sometimes*,
  with varying severity. Community reports describe it as intermittent —
  sometimes cleared by a reset, sometimes stuck. That points to a *stateful*
  value (the runtime balance buffer is the prime suspect), but the mechanism
  that actually corrupts it has not been identified. **No definitive root
  cause is claimed here, and none will be until a concrete writer is found.**

### The practical fix

Whatever the deeper cause, the runtime balance buffer is directly writable and
the firmware's NVDM balance defaults are patchable. The companion tool measures
your unit's imbalance, lets you correct it by ear, and bakes the correction
into a custom firmware so it survives reboots and a factory reset — applied on
every device (phone, console, PC) with no software running. Full detail in
**[AUDIO.md](AUDIO.md)**.

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
