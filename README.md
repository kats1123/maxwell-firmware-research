# Audeze Maxwell — Firmware Reverse Engineering

A reference document for the **Audeze Maxwell** (original, not V2) firmware internals.

Audeze hasn't released a firmware update for the original Maxwell in roughly
two years (the most recent, v1.0.1.74, introduced an L/R audio balance issue
that affects many users).
This is documentation from reverse-engineering the firmware to understand *why*
that happens and what can (and cannot) be done about it.

> **Companion repo**: [maxwell-firmware-downgrader](https://github.com/kats1123/maxwell-firmware-downgrader) — pre-built flasher tool to switch between firmware versions.

---

## Document map

| Document | What's in it |
|----------|--------------|
| **[FIRMWARE.md](FIRMWARE.md)** | File format (LZMA-Alone), chip (AB1568), memory map, how to decompress and analyze |
| **[AUDIO.md](AUDIO.md)** | Audio pipeline, gain registers, channel selectors, EQ preset system — **the L/R balance explanation lives here** |
| **[PROTOCOL.md](PROTOCOL.md)** | RACE protocol details, HID interface, command dispatch table, NVDM key reference |
| **[VERSIONS.md](VERSIONS.md)** | Concrete byte-level differences between v1.0.1.63 and v1.0.1.74 |
| **[FLASHING.md](FLASHING.md)** | How the FOTA update process works, why firmware patching is blocked by signature verification |
| **[COMMUNITY.md](COMMUNITY.md)** | What we couldn't figure out, suggested paths forward, tools we used |

---

## TL;DR for the L/R balance issue

**Why does my Maxwell sound unbalanced on USB-C and balanced on the dongle (or vice versa)?**

The firmware stores **per-source** gain calibration in two different NVDM keys:

- `NVDM 0xF665` is used when "audio source state" = 10 (USB-C wired)
- `NVDM 0xF668` is used when state ≠ 10 (BT/dongle)

Audeze's **factory defaults**:
- v1.0.1.63: USB-C `L=141, R=149` (+8 R boost) | Dongle `L=141, R=141`
- v1.0.1.74: USB-C `L=141, R=149` (unchanged)   | Dongle `L=147, R=147` (+6 both)

The USB-C `+8 R` is a **deliberate hardware compensation** — Audeze decided the
average Maxwell unit's USB-C audio path attenuates R slightly. Units that don't
need the full +8 compensation (or need more than +8) will sound unbalanced on
USB-C. The opposite applies for BT/dongle where the default is 0 compensation.

These values can be modified at **runtime** via RACE commands without flashing.
See [PROTOCOL.md](PROTOCOL.md) and [AUDIO.md](AUDIO.md).

---

## Status of this research

| Area | Status |
|------|--------|
| Firmware decompression | ✅ Solved (LZMA-Alone, no encryption) |
| Memory map / chip identification | ✅ Solved (Airoha AB1568, Cortex-M4F) |
| RACE protocol structure | ✅ Mapped (dispatch table, sub-commands, NVDM keys) |
| Audio mixer architecture | ✅ Mapped (gain registers, channel selectors, EQ swap) |
| L/R balance root cause | ✅ Understood (per-source NVDM, hardware variance) |
| Runtime control via RACE | ✅ Working (can set L/R balance without flashing) |
| Firmware patching | ❌ **Blocked** by bootloader signature verification |
| Concurrent playback (USB-C + BT) | ❌ **Blocked** — requires firmware mod |
| Bootloader signature bypass | ❌ Not attempted (requires hardware-level work) |

---

## Hardware

| | |
|--|--|
| **SoC** | Airoha AB1568 (= MediaTek MT2822) |
| **Core** | ARM Cortex-M4F @ ~260 MHz |
| **SDK** | Airoha IoT_SDK_for_BT_Audio v3.4.1 |
| **Datasheet** | NDA-locked (not public) |
| **Flash** | 2 MB compressed firmware → 3.2 MB decompressed |
| **Bootloader** | In immutable ROM (not in firmware binary) |
| **Drivers** | 90 mm planar magnetic |

---

## License

The documents in this repo are licensed CC BY 4.0 (Creative Commons Attribution).
You're free to use, share, and adapt for any purpose, including commercially —
just credit this repo.

This is not affiliated with, endorsed by, or sponsored by Audeze LLC.
"Audeze" and "Maxwell" are trademarks of their respective owners.
