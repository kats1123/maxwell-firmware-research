# Patch Cookbook

All known custom-firmware patches for the Audeze Maxwell (v1.0.1.74),
collected in one place. Use with [tools/firmware_patcher.py](tools/firmware_patcher.py)
or apply manually.

## Patch format

Each patch is described as:
- **File offset** in the decompressed firmware (not the compressed `.bin`)
- **Original bytes** (verify these match before patching — if they don't,
  you've got the wrong firmware version or a non-stock build)
- **Patched bytes**
- **What it does**

After applying any patch(es):
1. Recompute SHA-256 of each affected partition (TLV `0x0014`)
2. Re-compress with LZMA params: dict=16384, lc=3, lp=0, pb=2
3. Update TLV `0x0011` byte 6-9 with new LZMA stream size
4. Recompute outer SHA-256 over `file[0x100:]`

`firmware_patcher.py` does all of this automatically.

## Currently-shipped patches

### `0x186C72` — BT/dongle default L/R balance

Default written to NVDM `0xF668` during factory init. Loaded into the
audio mixer at boot whenever source state != 10 (i.e. BT or dongle).

- **Original**: `49 F2 93 33` (`movw r3, #0x9393` = L=147, R=147)
- **Patched**: encode `(R << 8) | L` as `movw r3, #imm16` Thumb-2

Example for L=141, R=154: `49 F6 8D 23` (`movw r3, #0x9A8D`).

Use `firmware_patcher.py --bt-l N --bt-r N` to set values.

**Requires factory reset** to take effect (otherwise old NVDM persists).

### `0x186CA4` — USB-C default L/R balance

Default written to NVDM `0xF665` during factory init. Loaded into the
audio mixer when source state == 10 (USB-C wired).

- **Original**: `49 F2 8D 53` (`movw r3, #0x958D` = L=141, R=149)
- **Patched**: encode `(R << 8) | L` as `movw r3, #imm16`

Example for L=141, R=143: `48 F6 8D 73` (`movw r3, #0x8F8D`).

Use `firmware_patcher.py --usb-l N --usb-r N`.

**Requires factory reset** to take effect.

### `0x135C66` — Concurrent playback site 1

NOPs `BL FUN_00137F48` (router_reset) in the source-dispatch function.
Enables USB-C and BT to play simultaneously (without one killing the other).

- **Original**: `02 F0 6F F9` (BL to `0x08156F48`)
- **Patched**: `00 BF 00 BF` (two NOPs)

### `0x135CC4` — Concurrent playback site 2

NOPs `BL FUN_00137F9C` (bigger_router_reset) in the state-dispatch function.
This is the function called when transitioning between two wireless sources
(state == 1 or state == 0xF1). Expected to enable BT↔dongle concurrent
playback, though empirical testing on a real device is pending.

- **Original**: `02 F0 6A F9` (BL to `0x08156F9C`)
- **Patched**: `00 BF 00 BF` (two NOPs)

## Candidate patches (untested)

### `0x135CCA` — Concurrent playback site 3 (aggressive)

NOPs the state-2 reset (`BL FUN_0815_90BC`). State 2 may be a normal
transition path for some source combinations; NOPing it may have side
effects. Try only if patches 1+2 don't fully solve concurrent playback.

- **Original**: `04 F0 F7 F9` (BL to `0x081590BC`)
- **Patched**: `00 BF 00 BF`

### `0x186CDE` — NVDM `0xF66E` default

Mystery 2-byte audio flag. Default `0x0901`. Patchable but its meaning
is unknown — touch at your own risk.

- **Original**: `40 F6 01 13` (`movw r3, #0x901`)
- **Patched**: `movw r3, #<your_value>` (16-bit immediate)

### Auto-power-off timeout

Candidate constant `900` (15 minutes in seconds) appears at file
offsets `0x6FFD4` and `0x81ED4`. These MAY control auto-power-off
or idle timeouts but are unverified. Changing them is speculative.

To patch: locate the 32-bit LE value `0x84 03 00 00` (= 900) at the
target offset and replace with your desired timeout in seconds. Each
occurrence may need separate patching.

## Patches that DON'T work

### Sample rate, bitrate, audio quality

These are determined by host (PC/console) and codec negotiation. No
firmware patch will increase audio resolution beyond what the
A2DP/USB-Audio link provides.

### Adding aptX / LDAC

The codec implementations aren't in the firmware. Adding them would
require reverse-engineering the audio pipeline and writing new
encoder/decoder code in ARM — a major effort.

### Activating LE Audio (Auracast/BAP/PACS)

LC3 codec code is present (`LC3I_Enc_Prcs`, `LC3I_Dec_Prcs` strings)
but no BAP/PACS/ASCS/Auracast infrastructure exists in firmware. LE
Audio activation would require adding all those subsystems from
scratch — likely impractical via patching alone.

### Bootloader modifications

The first-stage bootloader is in flash but the FOTA `PageProgram` (RACE
`0x0402`) command restricts writes to the FOTA partition (`0x084A1000`–
`0x087F5000`). Attempts to write outside that range are silently
rejected. Modifying the bootloader would require finding a flash-write
privilege escalation in the active firmware.

## Verifying a patch worked

1. Flash the patched firmware (see [FLASHING.md](FLASHING.md))
2. Once booted, use RACE `0x0403` (PageRead) to read the patch site from
   live flash:
   ```
   flash_addr = 0x0801F000 + decompressed_offset
   ```
   For patch `0x186C72`: read flash at `0x081A5C72`, confirm bytes match
   your patched values.
3. If verifying balance patches specifically: after factory reset, the
   new defaults should be loaded into the audio context at runtime. The
   exact runtime address where they land is `0x142039AC` (per fallback
   path xref), though async NVDM loading makes timing tricky — listen
   to determine if the new defaults are active rather than relying on
   live reads.
