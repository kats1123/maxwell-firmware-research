# Community Continuation Guide

What we know, what's still unknown, and how to pick up where we left off.

## Status summary

| What | Status |
|------|--------|
| Decompress firmware files | ✅ Solved |
| Map firmware sections / functions | ✅ Mostly mapped |
| Reverse-engineer RACE protocol | ✅ Done |
| Identify all 0x42xxxxxx hardware registers | 🟡 Block-level mapped; specific register meanings unknown (no datasheet) |
| Find L/R balance root cause | ✅ Solved (per-source NVDM + hardware variance) |
| Modify runtime behavior via RACE | ✅ Working |
| Modify firmware itself | ❌ Blocked by bootloader signature verification |
| Enable concurrent BT + USB-C playback | ❌ Requires firmware mod (above) |

## Confirmed open questions

These are things I encountered but didn't fully resolve. If you're picking up
this work, these are good starting points:

### 1. What kind of signature is the bootloader checking?

We see `ECC` strings in the firmware but no ECC curve constants. The
verification code and public key are almost certainly in the bootloader ROM.
Determining whether it's ECDSA P-256, P-384, Ed25519, or something else, and
what curve/parameters are used, would require dumping the bootloader.

**How to investigate**: JTAG/SWD on the AB1568 chip (if debug pins are
accessible on the Maxwell PCB) → dump ROM → analyze.

### 2. What goes in the third field of partition table entries?

Each partition table entry at file offset `0x130` has the format:

```
4 bytes addr | 4 bytes size | 4 bytes ???
```

The third field has values like `0x13000`, `0x133000`, `0x32C000`, `0x450000`
for the 4 entries. They look like file offsets but don't match where the
data actually lives. Could be:
- Per-partition CRC32 (the values are too "round" to be CRCs though)
- Offset into a separate signature blob
- Bank flash address (where the partition will go in flash after copy)

### 3. How does the EQ preset switcher really get triggered?

`FUN_001AA6E0` swaps EQ presets when source state changes, but we found 0
direct callers and 0 absolute function-pointer references to it. It must be
invoked indirectly (RTOS task callback, ARM exception vector, function table
indexed by computed offset). Tracing this would clarify whether the EQ swap
happens automatically when audio sources change, or whether the Audeze app
manually invokes it.

### 4. What do the 80+ AT+EAUDIO commands actually do?

We found the string list but didn't trace what each command does. They appear
to be a debug interface used during development. Many are obvious from the
name (`AUD_SET_DEVICE_LEFT`, `PEQ_SYNC`, `VOL_STREAM_2A2D`). Some are not
(`VENDOR_SE`, `DL_NM`). If the firmware exposes any of these via RACE, they
could provide additional configuration knobs.

### 5. What's in the 20-byte "factory EQ defaults" block at `0x281AC3`?

`FUN_00186D04` (factory init) writes 20 bytes from this offset to `NVDM 0xE400`.
The bytes look like they could be biquad filter coefficients (4-byte stride,
"random" values typical of DSP coefficients). But we didn't decode the format
or determine which DSP filter section they populate.

### 6. Why does the L/R imbalance change "randomly" on some users' devices?

Multiple users report that their balance shifts during normal use, sometimes
fixed by reset, sometimes by reconnecting from a phone. Our investigation
identified plausible mechanisms (source state desync, EQ preset modification
via background app commands) but couldn't pin down a single definitive cause.

**How to investigate**: USB-pcap captures of the device's HID traffic during
normal operation. The Audeze app may be sending commands that aren't visible
to the user. Reverse-engineering the app's behavior would clarify this.

## High-ROI projects for the community

In order of estimated impact vs. effort:

### Highest ROI: Runtime tray app for per-source balance

A small PC app that:
- Watches for USB device events (PID `0x4B18` = Xbox dongle, `0x4B1E` = USB-C)
- On connect, sends RACE command `0x900 sub 0x2F` to set the source state
  correctly (10 for USB-C, 0 for BT)
- Optionally re-applies the user's preferred L/R values via sub 0x29/0x2A

This solves the actual user-facing problem without any firmware risk and
without needing the Audeze app running. Should be doable in a couple
hundred lines of Python or C#.

### Medium ROI: Decode remaining RACE sub-commands

`FUN_0015C91C` handles sub-commands of `0x0900` up to at least `0x2F` but
the switch statement likely continues to higher values. Decoding all of them
might reveal commands we missed that:
- Write the audio context channel selectors directly (would enable concurrent
  playback at runtime, NO firmware mod needed!)
- Modify EQ coefficients directly
- Provide other useful runtime hooks

This is just more Ghidra time on the same firmware we have.

### Medium ROI: USB-pcap captures of normal usage

Run `USBPcap` while:
- Audeze app starts
- Connecting to different devices
- Switching EQ presets
- Adjusting volume / chatmix
- Powering off / on

This would reveal commands the Audeze app sends that we didn't observe.

### Lower ROI / harder: Bootloader extraction

JTAG/SWD debug access to the AB1568 chip would allow:
- Dumping the bootloader ROM
- Reverse-engineering the verification routine
- Possibly finding a bypass

Requires hardware skills and access to debug pads on the Maxwell PCB.

### Lowest ROI but potentially fun: Maxwell V2 comparison

If/when Maxwell V2 firmware can be obtained:
- Different SoC? Different security model?
- Same RACE protocol or evolved?
- May reveal what Audeze fixed/changed/regressed

## Tools used in this research

| Tool | Use |
|------|-----|
| **Ghidra 12.0.4** | Primary disassembler/decompiler. Imported decompressed firmware as ARM Cortex-M little-endian, ran auto-analysis. |
| **Python 3.x + lzma module** | Firmware decompression and recompression experiments |
| **airoha-firmware-parser** (ramikg) | Drop-in decompressor for Airoha firmware |
| **pywinusb** | Python HID access on Windows |
| **AirohaHidCoreLib.dll** | Audeze app's bundled Airoha SDK — what `MaxwellFlasherGUI.exe` uses |
| **Wireshark + USBPcap** | (Recommended) Capture device HID traffic |

## Code & analysis scripts

The Ghidra scripts and Python helpers used in this research are in the [tools/](tools/)
directory. They're a starting point, not polished tools — they reflect
exploration, not a finished product.

## Where to discuss / collaborate

- This repo's [Issues](../../issues) and [Discussions](../../discussions) tabs
- r/AudezeMaxwell on Reddit
- head-fi.org Audeze threads

## Acknowledgments

- [ramikg](https://github.com/ramikg) for the airoha-firmware-parser
- [auracast-research](https://github.com/auracast-research) for the race-toolkit
  and ERNW research
- The Maxwell community for documenting the L/R balance issue extensively
  enough that "fix this" became a clear goal
