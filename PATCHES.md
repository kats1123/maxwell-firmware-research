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
   new defaults are written into the NVDM partition. **However, see the
   important caveat below about whether NVDM values are actually applied
   to runtime audio.**

## NEW (May 2026) — proposed `.data` patch for true firmware-only balance

**The cleanest firmware-only fix** for L/R balance is to patch the `.data`
section bytes at file `0x287F48` (which the reset handler memcpys into
runtime buffer `0x142039AC` at boot):

| Field | Current bytes | Patched bytes |
|-------|---------------|---------------|
| Stock (file `0x287F48`) | `88 88 00 00` (L=136, R=136, slider=0, dir=0) | `<L> <R> 00 00` (your preferred L/R) |

Example for L=141, R=143: change file `0x287F48` from `88 88 00 00` to
`8D 8F 00 00`.

This patch:
- Affects boot init directly — the reset handler's `.data` memcpy copies
  these bytes from flash to SRAM. No code execution needed.
- Combined with **SRAM retention** (see [FIRMWARE.md](FIRMWARE.md) §SRAM
  retention), the value persists across every "power off" until the next
  true cold reset.
- Survives factory reset (Audeze HQ's factory reset still works as
  before via NVDM defaults + host-side RACE writes; AND now the firmware's
  boot init also produces the same values, so the buffer is correct
  regardless of host involvement).
- Requires re-computing the partition-2 SHA-256 and outer SHA-256 (the
  patch is inside partition 2's decompressed region — file `0x287F48` is
  past the partition 2 start at `0x114000`).

The existing patches at `0x186C72` and `0x186CA4` (NVDM defaults) are
still useful — they ensure Audeze HQ's "load defaults" UI shows the right
values — but they are now understood to be **secondary** to the `.data`
patch, not primary.

## CRITICAL CAVEAT — NVDM balance patches may not actually change audio

(December 2025 finding via static analysis — see [FIRMWARE.md](FIRMWARE.md)
§How `0x142039AC` is actually initialized and §Loader-cluster function map.)

The two balance-default patches above (`0x186C72` and `0x186CA4`) only
modify what gets WRITTEN to NVDM `0xF665`/`0xF668` during factory init.
**They do NOT modify the values that get loaded into the runtime audio
buffer at `0x142039AC` at boot or source switch.**

Specifically:

- The runtime balance buffer `0x142039AC` is initialized at every cold
  boot to `0x88 0x88 0x00 0x00` (L=136, R=136, slider=0, dir=0) via a
  .data-section flash-to-SRAM copy in the reset handler. This value
  comes from flash address `0x082A6F48` (file offset `0x287F48`), NOT
  from NVDM.
- The "NVDM-to-runtime balance loader" function `FUN_0x081DE2E4` exists
  in firmware but has **zero callers** anywhere — it's dead code.
- The other loader-cluster function (`FUN_0x081DDFD4`) only fires on a
  RACE balance write — it's not invoked at boot or on source switch.
- The balance-writer function `FUN_0x081DE094` has **separately
  hardcoded** default values for its `0x88` and `0x8E` sentinel inputs
  (at runtime offsets `0x081DE0F0`, `0x081DE0F8`, `0x081DE104`,
  `0x081DE108`, `0x081DE114`, `0x081DE118`). These constants are NOT
  patched by any current patch. They contain stock values like 0x8D/0x95
  regardless of how we patched NVDM defaults.

**Practical implication**: if you flash patches `0x186C72` and `0x186CA4`
and observe that runtime audio is affected, the mechanism is not the
direct NVDM-to-runtime path. It must go through some other code path
that we have not yet identified, OR the persistent buffer state from a
previous RACE write is what you're observing.

To make balance patches reliably affect runtime audio at every boot
without depending on host-side intervention, additional patch sites in
the loader cluster (around `0x081DE094`) need to be identified — or a
new patch needs to be designed to make `FUN_0x081DE2E4` (the dead-code
loader) actually get called during boot. This is the highest-priority
open research question on this project.
