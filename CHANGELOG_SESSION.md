# Session Changelog — December 2025

A summary of the major research wave that took the project from
"firmware patching is blocked by ECDSA signatures" to working custom
firmware, plus expanded RE coverage.

## Headline change

**Custom firmware flashing is no longer blocked.** Previous belief (that
the Maxwell bootloader requires ECDSA-signed images) was wrong. The
bootloader's integrity check is **SHA-256 only**, recomputable, and we
have the recipe and tooling to produce flashable modified firmware.

## What changed in this session

### Documentation

| File | Status | What changed |
|------|--------|--------------|
| [FLASHING.md](FLASHING.md) | Rewritten | Full custom-firmware recipe; Audeze-specific `0x0101` FOTA-init quirk; HID fragmentation requirement; correct `fota_checksum` lookup-table algorithm |
| [BOOTLOADER.md](BOOTLOADER.md) | New | First-stage bootloader dumped (76 KB) from flash `0x08000000`; chip-level partition table (8 entries including NVDM at `0x087F6000`); explains why there's no asymmetric signature |
| [README.md](README.md) | Updated | New status table, headline finding callout, hardware section expanded with confirmed live readings |
| [FIRMWARE.md](FIRMWARE.md) | Updated | Complete TLV layout including the `0x0011` LZMA stream-size field that everyone missed; partition table with confirmed runtime addresses; full NVDM key inventory (17 write sites, 11 keys with defaults, 8 more read-only keys) |
| [AUDIO.md](AUDIO.md) | Updated | Second router-reset patch site identified (`0x135CC4` for BT↔dongle case); PEQ system mapped to NVDM `0xE301`/`0xE304`; codec list (SBC, AAC, mSBC, CVSD, LC3 — no aptX/LDAC) |
| [PROTOCOL.md](PROTOCOL.md) | Updated | PS USB-C PID `0x4B1A` added; HID fragmentation note; v74 dispatch table offset corrected |
| [VERSIONS.md](VERSIONS.md) | Corrected | v74 differs from v63 in ~1.3M bytes (~42%), not just the balance defaults |
| [COMMUNITY.md](COMMUNITY.md) | Rewritten | Status reflects custom-firmware capability; new open questions; reorganized "high-ROI" project list |
| [PATCHES.md](PATCHES.md) | New | Cookbook of all known patches in one reference doc |

### Tools

| Script | What it does |
|--------|--------------|
| [tools/firmware_patcher.py](tools/firmware_patcher.py) | Builds custom v74 firmware with L/R balance + concurrent playback patches; handles TLV `0x0014` and `0x0011` recomputation |
| [tools/dump_bootloader.py](tools/dump_bootloader.py) | Dumps 76 KB of first-stage bootloader from live device via RACE `0x0403` |
| [tools/parse_dispatch.py](tools/parse_dispatch.py) | Parses the 30-entry RACE command dispatch table |
| [tools/find_all_nvdm_defaults.py](tools/find_all_nvdm_defaults.py) | Scans firmware for `BL nvdm_write_default` to find all patchable NVDM defaults |

### Companion repo updates

The [maxwell-firmware-downgrader](https://github.com/kats1123/maxwell-firmware-downgrader)
shipped **v1.1** adding PS USB-C support (PID `0x4B1A`) so PS Maxwell
users can flash their headset via USB-C cable, not just dongle.

## What was empirically verified

- Custom v74 firmware with patched L/R defaults flashed and booted
  successfully via the standard FOTA path
- All 3 patches (BT balance, USB-C balance, concurrent-playback site 1)
  verified live in flash via RACE `0x0403` reads at the expected offsets
- User behaviorally confirmed that post-factory-reset, the new
  balance defaults take effect (consistent with patched values)

## What was discovered but not yet device-tested

- Second concurrent-playback patch site at `0x135CC4` (for BT↔dongle
  case) — patched but not yet flashed/listened
- Live runtime L/R RAM address `0x142039AC` — fallback path writes
  there but actual NVDM-loaded values appear to land elsewhere
- 8 additional NVDM keys in the `0xF66x` range whose semantics are
  unknown
- 1.3 MB of differences between v63 and v74 firmware beyond what's
  documented in VERSIONS.md
