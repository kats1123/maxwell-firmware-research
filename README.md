# Audeze Maxwell — Firmware Reverse Engineering

A reference document for the **Audeze Maxwell** (original, not V2) firmware
internals — and a working recipe for flashing custom firmware.

Audeze hasn't released a firmware update for the original Maxwell in roughly
two years (the most recent, v1.0.1.74, introduced an L/R audio balance issue
that affects many users). This is documentation from reverse-engineering the
firmware to understand *why* that happens and *what can be done about it* —
both via runtime control and via flashing modified firmware.

> **Companion repo**: [maxwell-firmware-downgrader](https://github.com/kats1123/maxwell-firmware-downgrader) — pre-built flasher tool to switch between firmware versions (and also flash custom builds, if you're brave).

---

## Document map

| Document | What's in it |
|----------|--------------|
| **[FIRMWARE.md](FIRMWARE.md)** | File format (LZMA-Alone), chip (AB1568), memory map, how to decompress and analyze |
| **[AUDIO.md](AUDIO.md)** | Audio pipeline, gain registers, channel selectors, EQ preset system — **the L/R balance explanation lives here** |
| **[PROTOCOL.md](PROTOCOL.md)** | RACE protocol details, HID interface, command dispatch table, NVDM key reference |
| **[VERSIONS.md](VERSIONS.md)** | Concrete byte-level differences between v1.0.1.63 and v1.0.1.74 |
| **[FLASHING.md](FLASHING.md)** | FOTA process internals + **working custom-firmware flash recipe** |
| **[BOOTLOADER.md](BOOTLOADER.md)** | First-stage bootloader (in flash, not ROM), TLV parsing, integrity check details |
| **[COMMUNITY.md](COMMUNITY.md)** | Remaining open questions, suggested paths forward, tools we used |

---

## Headline finding (December 2025)

**Custom firmware IS flashable.** Earlier versions of this writeup said it was
blocked by ECDSA signature verification in a bootloader ROM. That was wrong —
there is **no asymmetric signature** on the Maxwell firmware. The bootloader
only performs **SHA-256 integrity checks**, which are keyless and recomputable.

Two header fields that prior custom-firmware attempts overlooked:

1. **TLV `0x0014`** — per-partition SHA-256 hashes (4 × 32 bytes). All four
   must be updated when partition content changes.
2. **TLV `0x0011` bytes 6-9** — LZMA stream length. Recompressing with a
   different LZMA encoder changes the stream size; this field must match.

With both correctly updated, plus the outer SHA-256 over `file[0x100:]`, a
modified firmware passes integrity verification and boots normally. See
[FLASHING.md](FLASHING.md) for the full recipe and protocol details.

---

## TL;DR for the L/R balance issue

**Why does my Maxwell sound unbalanced on USB-C and balanced on the dongle (or vice versa)?**

The firmware stores **per-source** gain calibration in two different NVDM keys:

- `NVDM 0xF665` is used when "audio source state" = 10 (USB-C wired)
- `NVDM 0xF668` is used when state ≠ 10 (BT/dongle)

Audeze's **factory defaults**:
- v1.0.1.63: USB-C `L=141, R=149` (+8 R boost) | Dongle `L=141, R=141`
- v1.0.1.74: USB-C `L=141, R=149` (unchanged)   | Dongle `L=147, R=147` (+6 both)

The USB-C `+8 R` is a **deliberate hardware compensation** — Audeze decided
the average Maxwell unit's USB-C audio path attenuates R slightly. Units
that don't need the full +8 compensation (or need more than +8) will sound
unbalanced on USB-C. The opposite applies for BT/dongle where the default
is 0 compensation.

**Two ways to fix this for your specific unit:**

1. **Runtime** via RACE commands — see [PROTOCOL.md](PROTOCOL.md). Doesn't
   survive factory reset, but no flashing required.
2. **Persistently** via patched firmware — change the `movw r3, #imm16`
   instructions at file offsets `0x186C72` (BT/dongle default, v74) and
   `0x186CA4` (USB-C default, v74) to your chosen values. Survives reset.
   See [FLASHING.md](FLASHING.md).

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
| L/R balance root cause | ✅ Understood (per-source NVDM, hardware variance) |
| Runtime control via RACE | ✅ Working (can set L/R, EQ, chatmix without flashing) |
| **Custom firmware flashing** | ✅ **Working** (TLV `0x0014` + TLV `0x0011` recipe) |
| Concurrent playback (USB-C + BT) | 🟡 Partial — one NOP at `0x135C66` enables some source combos; BT+dongle path needs more |
| Boot ROM contents (the *true* ROM at `0x00000000`) | ❌ Unread — RACE memory reads to `0x0` crash the chip. Likely just a tiny IPL stub that jumps to the first-stage in flash. |

---

## Hardware

| | |
|--|--|
| **SoC** | Airoha AB1568 (= MediaTek MT2822) |
| **Core** | ARM Cortex-M4F, CPUID `0x410FC241` (r0p1+FPU), confirmed live |
| **SDK** | Airoha IoT_SDK_for_BT_Audio v3.4.1 |
| **Datasheet** | NDA-locked (not public) |
| **Flash** | 8 MB SPI XIP at `0x08000000`–`0x087FFFFF` |
| **First-stage bootloader** | In flash at `0x08000000`–`0x08013000` (~76 KB, dumpable) |
| **Active firmware bank** | Maps to flash `0x08013000`+ |
| **FOTA partition** | Flash `0x084A1000`–`0x087F5000` (~3.4 MB inactive bank) |
| **SRAM** | `0x14000000`+ (audio buffers, mixer state) |
| **TCM (fast RAM)** | `0x04000000`–`0x0402C000` (vector table + hot-path code) |
| **VTOR (vector table reg)** | Confirmed live: `0x04000000` (vectors copied to RAM at boot) |
| **Audio HW regs** | `0x42000000`–`0x4200FFFF` (memory-mapped I/O, reads crash chip) |
| **Drivers** | 90 mm planar magnetic |

---

## License

The documents in this repo are licensed CC BY 4.0 (Creative Commons Attribution).
You're free to use, share, and adapt for any purpose, including commercially —
just credit this repo.

This is not affiliated with, endorsed by, or sponsored by Audeze LLC.
"Audeze" and "Maxwell" are trademarks of their respective owners.
