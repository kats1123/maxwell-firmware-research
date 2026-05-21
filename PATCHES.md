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

**Requires a FACTORY RESET to take effect.** A flash updates the firmware
*code* (this `movw` included) but does NOT write NVDM. The patched default
reaches NVDM only when the firmware's default-registration runs — which
happens on a **factory reset**. Confirmed May 2026 by direct test: flashed
a custom `.74` with balance baked to 143/148; a live-flash read showed the
custom `movw` present, yet the headset still read the old balance — a
factory reset then applied 143/148. (A mid-session note claimed the flash
itself applies it; that was a misread of a messy `.63` sequence — retracted.)

### `0x186CA4` — USB-C default L/R balance

Default written to NVDM `0xF665` during factory init. Loaded into the
audio mixer when source state == 10 (USB-C wired).

- **Original**: `49 F2 8D 53` (`movw r3, #0x958D` = L=141, R=149)
- **Patched**: encode `(R << 8) | L` as `movw r3, #imm16`

Example for L=141, R=143: `48 F6 8D 73` (`movw r3, #0x8F8D`).

Use `firmware_patcher.py --usb-l N --usb-r N`.

**Requires a FACTORY RESET to take effect** — see `0x186C72` above.

### `0x135C66` — Concurrent playback site 1

NOPs `BL FUN_00137F48` (router_reset) in the source-dispatch function.
Enables USB-C and BT to play simultaneously (without one killing the other).

- **Original**: `02 F0 6F F9` (BL to `0x08156F48`)
- **Patched**: `00 BF 00 BF` (two NOPs)
- **Status**: APPLIED in `maxwell_v74_custom_v2.bin` — verified against the
  binary (May 2026): bytes at `0x135C66` are `00 BF 00 BF`.

### `0x135CC4` — Concurrent playback site 2

NOPs `BL FUN_00137F9C` (bigger_router_reset) in the state-dispatch function.
This is the function called when transitioning between two wireless sources
(state == 1 or state == 0xF1). Expected to enable BT↔dongle concurrent
playback, though empirical testing on a real device is pending.

- **Original**: `02 F0 6A F9` (BL to `0x08156F9C`)
- **Patched**: `00 BF 00 BF` (two NOPs)
- **Status**: NOT applied in `maxwell_v74_custom_v2.bin` — verified against
  the binary (May 2026): bytes at `0x135CC4` are still the original
  `02 F0 6A F9`. Despite this section's title, site 2 was never shipped —
  treat it as an untested candidate, not a current patch.

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
3. If verifying balance patches specifically: after a factory reset or a
   firmware reflash, the firmware reboots, re-runs `main()`, and the boot
   loader copies the patched NVDM `0xF665`/`0xF668` default into the
   runtime buffer `0x142039AC`. You can confirm by reading `0x142039AC`
   over RACE (cmd `0x1680`) — it should hold your patched L/R.

## How the balance patches actually work (CONFIRMED May 2026)

The two patches `0x186C72` (NVDM `0xF668`) and `0x186CA4` (NVDM `0xF665`)
change the **factory-default values** the firmware writes to NVDM during
a factory reset.

The firmware then **loads those NVDM values into the runtime audio buffer
`0x142039AC` on every reboot** — this was empirically confirmed (flashed
stock v1.0.1.63 with no host software running; `0x142039AC` came up
holding the NVDM `0xF665` value, not the `.data` default). The loader is
the tail of `FUN_0x081DE120`, the boot audio-init function called by
`main()`. See [FIRMWARE.md](FIRMWARE.md) §NVDM-to-runtime balance loader.

So the patch chain is:

```
patch NVDM 0xF665 default  ─┐
                            ├─ factory reset → NVDM 0xF665 = patched value
patch NVDM 0xF668 default  ─┘
                            └─ every reboot → main() → FUN_0x081DE120 tail
                                              → 0x142039AC = NVDM value
                                              → DSP-applied
```

This is a **fully device-side fix** — no host software, works on iPhone,
console, anything. **But the patched balance takes effect only after a
FACTORY RESET**, not on the flash itself. Sequence: flash the custom
firmware (updates the code), then factory-reset the headset — the reset
runs the default-registration that writes the patched `0xF665`/`0xF668`
into NVDM, and the boot loader then copies that into the runtime buffer
`0x142039AC` on every reboot.

CONFIRMED May 2026 by direct test: flashed a custom `.74` with balance
baked to 143/148; a live-flash read showed the custom `movw` present, yet
the headset still read the old balance (147/147) — a factory reset then
applied 143/148. (A mid-session revision wrongly claimed the flash itself
applies it, from a misread of a messy `.63` flash sequence — retracted;
the factory-reset requirement is correct and now empirically confirmed.)

### The `.data` patch (`0x287F48`) — optional and redundant

`firmware_patcher.py` can also patch the `.data` bytes at file `0x287F48`
(the compile-time initial value of `0x142039AC`, stock `88 88 00 00`).
**This patch is redundant**: the boot loader overwrites `0x142039AC` with
the NVDM value a few instructions later, so whatever `.data` put there
does not survive boot. It is harmless (the patcher still includes it by
default) but it accomplishes nothing the NVDM patches don't already do.
Use `--no-data-patch` to skip it; it makes no functional difference.

### Retraction

An earlier revision of this file claimed the NVDM balance patches "may
not actually change audio" because "`FUN_0x081DE2E4` is dead code." That
was wrong — `0x081DE2E4` is not a separate function, it is the tail of
`FUN_0x081DE120` and runs on every reboot. The NVDM patches **do** work
device-side. That section has been removed.
