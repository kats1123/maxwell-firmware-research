# Community Continuation Guide

What we know, what's still unknown, and how to pick up where we left off.

## Status summary (December 2025)

| What | Status |
|------|--------|
| Decompress firmware files | ✅ Solved |
| Map firmware sections / functions | ✅ Mostly mapped |
| Reverse-engineer RACE protocol (read paths) | ✅ Done |
| Reverse-engineer RACE FOTA flow (Audeze-specific) | ✅ Done — needs `0x0101` init before `FotaStart` |
| Dump first-stage bootloader (in flash) | ✅ Done — 76 KB at `0x08000000`+ |
| Identify chip-level partition map | ✅ Done (8 entries, including NVDM at `0x087F6000`) |
| Identify all 0x42xxxxxx hardware registers | 🟡 Block-level mapped; specific register meanings unknown (no datasheet) |
| Find L/R balance root cause | ✅ Solved (per-source NVDM + hardware variance) |
| Modify runtime behavior via RACE | ✅ Working |
| **Modify firmware itself (custom firmware)** | ✅ **Working** — SHA-256-only integrity check, fully recomputable |
| Find correct LZMA stream size TLV (was missing piece) | ✅ Solved — TLV `0x0011` bytes 6-9 |
| Concurrent playback patches | 🟡 Two BL sites identified (`0x135C66`, `0x135CC4`); empirical confirmation that this fixes BT+dongle still pending |
| Boot ROM extraction | ❌ Not done. The TRUE boot ROM at `0x00000000` is unreadable (RACE memory reads crash the chip there). The first-stage bootloader is in flash at `0x08000000`+ and dumpable. |
| L/R balance live runtime address | ✅ **Confirmed at `0x142039AC`** — reads + writes verified affect audio mixer output for BOTH USB-C and dongle audio |
| Per-source NVDM design (`0xF665` USB-C / `0xF668` dongle) works at runtime | ❌ **Disproven** — `0x142039AC` is a single shared buffer; source-change does not trigger reload; chip uses whichever NVDM key was loaded at boot init (typically `0xF665`) for ALL sources |
| RACE balance writes persist across reboot | ✅ **Proven** — they write to NVDM `0xF665` in addition to RAM; survive power-cycle |
| Factory reset re-runs patched factory init | ✅ **Proven** — values snap to patched NVDM defaults after reset |

## Confirmed open questions

These are things we encountered but didn't fully resolve. Good starting
points for further work:

### 0. The NVDM-to-runtime balance loader — RESOLVED (May 2026)

The boot loader is found and confirmed. The code at `0x081DE2E4` reads
`NVDM 0xF665` (USB-C) or `0xF668` (dongle) and writes the result into the
runtime buffer `0x142039AC`. It is the **tail of `FUN_0x081DE120`** (the
boot audio-routing init that `main()` calls) — reached by fall-through,
which is why earlier `BL`-caller scans found nothing.

It runs **on every genuine reboot** (firmware update, factory reset,
battery-drain cold start). It does NOT run on a normal "power off / on"
— that is a deep-sleep, SRAM is battery-retained, `main()` does not
re-run. And it does NOT run on a live source switch.

Empirically confirmed: flashed stock v1.0.1.63 with no host software
running; `0x142039AC` came up holding the NVDM `0xF665` value, not the
`.data` default. See [FIRMWARE.md](FIRMWARE.md) §NVDM-to-runtime balance
loader and [AUDIO.md](AUDIO.md) §How the buffer gets loaded.

**Implication for the community's "balance suddenly shifted" reports:**
the loader uses the source state from `NVDM 0xF702` to pick which key to
load. If that state byte is ever stale/wrong at boot, a reboot could load
the wrong-source balance. Also, since the buffer is battery-retained
across sleep, a one-off bad RACE write (e.g. from buggy host software)
sticks until the next true reboot. Both are plausible mechanisms.

**Still open:**
- Confirm the `0xF668` (dongle) branch by booting dongle-first.
- Whether patched `NVDM 0xF668` value is stored after factory reset
  (`nvdm_write_default` won't overwrite an existing key).

### 0b. Why is `0x8E` (142) specifically rejected by the balance writer?

Empirical: writing R=`0x8E` via RACE 0x0900 sub 0x2A silently rejects;
values 0x8C, 0x8D, 0x8F, 0x90, 0x93, 0x9A all work. Decoding the
validation logic in the cmd 0x0900 writer (around `0x817B132+`) would
reveal whether this is:
- A reserved sentinel
- A saturation/range check (unlikely given range continues past)
- An encoding-specific check
- Some interaction with the balance-slider field (`0x142039AC[2]`)

A full sweep of every byte 0x00-0xFF would map all rejected values.

### 1. Concurrent playback patch verification

The custom-firmware build now NOPs both `BL FUN_00137F48` (file `0x135C66`)
and `BL FUN_00137F9C` (file `0x135CC4`). The first was empirically
confirmed to work for USB-C+BT; the second has NOT yet been tested on a
device. If a tester reports BT+dongle still kills previous source, the
next patch candidate is `BL FUN_0815_90BC` at file `0x135CCA` (state 2
in the state-dispatch function). That call may also need to be NOPped,
though it's riskier because state 2 may be a required normal transition.

### 2. NVDM 0xF66E meaning

This 2-byte key is written by the same factory-init function that writes
the per-source balance defaults (`0xF665`, `0xF668`). Default value
`0x0901`. Three other reads/writes exist at file offsets `0x1ADD42` and
`0x1ADD72`. What audio behavior does this control? Possibly: source-state
mask, codec selector, default audio routing mode. Patchable in firmware
if we figure out the semantics.

### 3. NVDM 0xE301 / 0xE304 — PEQ coefficient layout

The 564-byte (`0xE301`) and 194-byte (`0xE304`) NVDM defaults are very
likely DSP/PEQ coefficient blocks. Decoding the format (probably biquad:
five floats per section × N sections × M filter bands) would let
community members ship "tuned" firmwares with different baseline sound.
The 10 EQ presets shown in the Audeze app are likely host-side coefficient
sets that the app pushes to the device via RACE — not stored per-preset in
firmware.

### 4. LE Audio (LC3) activation

Strings `LC3I_Enc_Prcs` and `LC3I_Dec_Prcs` exist in firmware, implying
LC3 codec code is compiled in. But Maxwell isn't marketed as LE-Audio-
capable. Investigate whether LE Audio could be activated via a hidden
config flag or via firmware patch. Would offer better latency and
ecosystem support (Auracast, LE Audio earbuds compatibility).

### 5. The 4th channel selector at `+0xFC`

In `bigger_router_reset`, an additional channel selector at audio-context
offset `+0xFC` is also reset to `0xFF` (alongside the three documented at
`+0x3B`, `+0x6C`, `+0xCB`). What stream type does this 4th selector
correspond to? Knowing this would clarify the full channel-mixing matrix.

### 6. Boot state pages `0x08001000` and `0x08002000`

The chip-level partition table reserves two 4 KB regions for boot state
(see BOOTLOADER.md). They were all-`0xFF` in our flash dump (because the
device hadn't done a recent FOTA when we read). Dumping these after a
successful FOTA would reveal what state info the bootloader persists —
likely the `upgrade_flag`, last-good-firmware index, and possibly a
boot-counter.

### 7. What's in the NVDM partition?

`0x087F6000`–`0x08800000` is the live NVDM partition. Our flash-read scan
got 36 KB into this region (`0x087F5000` start) before encountering a
read fault at `0x087FEB00`. A more careful scan with smaller per-read
timeouts might map the entire NVDM region's record format and let us
read NVDM values directly via flash reads (instead of needing the SDK's
`CustomReadNvEx` which we couldn't get to work).

### 8. What kind of attack-surface exists in the FOTA module?

We have plaintext access to the first-stage bootloader. Are there any
input-validation bugs in the TLV parser that could be exploited to write
to non-FOTA-partition flash regions? E.g. a bad `mover_info` with
out-of-bounds `dst_flash_addr` — does the bootloader range-check this
strictly? If not, it might be possible to overwrite the bootloader itself.

## High-ROI projects for the community

In order of estimated impact vs. effort:

### Already shipped: Custom-firmware patcher

The current [audeze-tray] (when published) `firmware_patcher_v5.py`
produces flashable v1.0.1.74 custom builds. Patches included:

- BT/dongle balance defaults (`movw r3, #imm16` at file offset `0x186C72`)
- USB-C balance defaults (file offset `0x186CA4`)
- Concurrent playback (NOPs at file offsets `0x135C66` and `0x135CC4`)

Iteration loop: change L/R values in the patcher's defaults, rebuild,
flash, listen, repeat. Each cycle is ~6 minutes (v74→v63→v74-custom).

### Highest ROI: Runtime tray app for per-source balance + custom-firmware patcher

A small PC app that:
- Watches for USB device events (PID `0x4B18` Xbox dongle, `0x4B19` PS
  dongle, `0x4B1A` PS USB-C, `0x4B1E` Xbox USB-C)
- On connect, queries current L/R balance via RACE
- Lets user tune live (sliders for L and R), persisting via NVDM writes
- Optionally bakes their final L/R into a custom firmware build for them

This solves the actual user-facing problem (per-unit balance variance)
end-to-end. ~few hundred lines of Python or C#.

### Medium ROI: Decode the EQ preset coefficient format

NVDM `0xE301`/`0xE304` clearly hold DSP coefficients. Understanding the
format (likely biquad with normalized fixed-point floats) would let users
modify default sound signature. Trickier than balance because the
coefficients have to be mathematically valid (or audio breaks).

### Medium ROI: USB-pcap captures of Audeze app behavior

Some host-side behaviors (EQ preset coefficient pushes, advanced sidetone
settings, etc.) are sent over HID by the Audeze app. Capturing and
documenting these commands would expose all the hidden runtime config
knobs without requiring firmware mods.

### Lower ROI / harder: Bootloader-writable exploitation

If a TLV-parser bug or out-of-bounds write existed in the first-stage
bootloader, you could overwrite the bootloader itself and remove the
SHA-256 check entirely (or add backdoors, sign-key support, etc.). Useful
for academic research but probably won't pan out — Audeze's TLV parser
appears to have proper bounds checking based on the strings.

### Lowest ROI but potentially fun: Maxwell V2 cross-port

If Maxwell V2 firmware can be obtained and analyzed:
- Same Airoha SoC family? Same partition layout? Same RACE protocol?
- Could the V1 community tools (downgrader, patcher) work on V2?
- What features did Audeze add that we could backport to V1 via firmware
  patches?

## Tools used in this research

| Tool | Use |
|------|-----|
| **Ghidra 12.0.4** | Initial heavy disassembly/decompilation. Imported decompressed firmware as ARM Cortex-M little-endian, ran auto-analysis. |
| **Capstone (Python)** | Scriptable inline disassembly for hunting BL targets, decoding patch sites, and matching NVDM-write call sites. |
| **Python 3.x + lzma module** | Firmware decompression and recompression |
| **airoha-firmware-parser** (ramikg) | Drop-in decompressor — useful baseline before we built our own |
| **pywinusb / hidapi (Python)** | HID access on Windows for RACE protocol over USB |
| **AirohaHidCoreLib.dll** | Audeze app's bundled Airoha SDK — used by GUI flasher for the canonical FOTA path |
| **ERNW [race-toolkit](https://github.com/auracast-research/race-toolkit)** | Sony-derived RACE Python tooling — useful but doesn't work as-is on Audeze (state-machine quirks) |
| **Wireshark + USBPcap** | (Recommended) Capture device HID traffic |

## Code & analysis scripts

Tooling scripts used during this research will go in the [tools/](tools/)
directory in a follow-up commit. They're a starting point, not polished
tools — they reflect exploration, not a finished product.

## Where to discuss / collaborate

- This repo's [Issues](../../issues) and [Discussions](../../discussions) tabs
- The companion [maxwell-firmware-downgrader](https://github.com/kats1123/maxwell-firmware-downgrader) repo
- r/AudezeMaxwell on Reddit
- head-fi.org Audeze threads

## Acknowledgments

- [ramikg](https://github.com/ramikg) for the airoha-firmware-parser
- [auracast-research](https://github.com/auracast-research) for the race-toolkit
  and ERNW research that established the RACE protocol baseline
- ERNW for the public December 2025 disclosure of CVE-2025-20700/20701/20702
  which clarified the Airoha attack surface
- The Maxwell community for documenting the L/R balance issue extensively
  enough that "fix this" became a clear goal
