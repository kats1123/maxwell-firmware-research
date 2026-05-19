# FOTA Process & Why Firmware Patching Is Blocked

## TL;DR

You can **freely flash** any of Audeze's officially-released firmware files
to a Maxwell (this is what the [downgrader tool](https://github.com/kats1123/maxwell-firmware-downgrader)
does — bypasses the in-app rollback restriction). But you **cannot flash
modified firmware** because the Maxwell's bootloader verifies a signature that
isn't present in the firmware image and was likely generated with a private
key only Audeze possesses.

## The FOTA process at a high level

1. Host PC opens HID interface to the Maxwell (PID `0x4B1E` for USB-C, or
   `0x4B18` for Xbox dongle)
2. Host calls `initializeAirohaSDK(VID, PID)` from `AirohaHidCoreLib.dll`
3. Host calls `setTargetDevice(0)`, `registerUpdateResultCallback(cb)`
4. Host calls `requestDFUInfo()` → SDK exchanges info with device, prepares
   FOTA mode
5. Host calls `setDfuMode(0)`, `setBatteryLevel(20)`, `setDfuAgentFilepath(path)`,
   `setPingTimerFlag(0)`
6. Host calls `startDataTransfer()` → SDK streams firmware to device in pages
7. Device buffers received pages into the **inactive flash bank** (dual-bank
   design)
8. After all pages are received, host receives callback with status `5`
   (`READY_TO_APPLY`)
9. Host calls `applyNewFirmware(20)`
10. Device's bootloader **verifies the received firmware**, and if OK:
    - Marks the new bank as active
    - Reboots into the new firmware
11. Host receives callback status `1` (`DFU_SUCCESS`)

If verification fails at step 10, the host **never receives** the success
callback — it just sits at "Transferring" indefinitely or hits a timeout.
That's exactly what happens when trying to flash modified firmware.

## What we know about the verification

In the decompressed firmware we found:

1. **SHA-256 K-table** at file offset `0x26F9EC` — so the chip can compute
   SHA-256 hashes
2. Strings: `SHA256`, `ECC`, `auth_param`, `authorization_check_result` — so
   there's authentication code that uses ECC
3. **No ECC curve constants** (NIST P-256 prime, generator, etc.) in the
   firmware binary — meaning the verification keys/code aren't in this file

The most likely explanation: the **bootloader is in immutable ROM** that's not
part of the firmware update. The bootloader contains:
- An ECDSA public key (Audeze's, baked into chip during manufacture)
- The SHA-256 + ECDSA verification routine
- The branch from "boot" → "verify" → "execute firmware"

When you flash, the bootloader runs first, computes SHA-256 of the received
firmware, then verifies a signature against its known public key. If the
signature is invalid, the bootloader refuses to swap the active bank.

## The hash field at file offset 0x00 isn't the signature

The 32-byte field at the start of the firmware is a **plain SHA-256 hash** of
the rest of the file (`file[0x100:]`). It's a *checksum*, not a *signature* —
recomputing it is trivial.

```python
import hashlib
with open("Maxwell_v1.0.1.74_XBOX_headset.bin", "rb") as f:
    fw = f.read()

stored_hash   = fw[:32].hex()
computed_hash = hashlib.sha256(fw[0x100:]).hexdigest()
assert stored_hash == computed_hash  # True for any unmodified firmware
```

So this hash is recomputable after patching — but it's **not the thing that's
blocking patched firmware**. There's a separate signature check we don't see.

## What we observed when trying to flash patched firmware

| Patch attempt | Result |
|---------------|--------|
| Patched + recompressed with wrong LZMA params (8 MB dict) | **FAIL_TIMEOUT at 30s** (device couldn't even start decompression) |
| Patched + correct LZMA params (16 KB dict) + recomputed SHA-256 | **Hung at "Transferring" until manually canceled** (got past initial checks; failed final verification) |
| Original unmodified firmware | **Works perfectly** (~3-5 min, then `DFU_SUCCESS`) |

The "hung at transferring" result strongly suggests the device received and
buffered the firmware but rejected it at the verify-and-apply step.

## What it would take to enable firmware patching

In order of feasibility (least to most):

### 1. Find a signature verification bypass / vulnerability

Many embedded bootloaders have implementation bugs (timing attacks, length
issues, type confusion in ASN.1 parsing). A determined attacker with proper
tools could:
- Dump the bootloader from RAM during execution (requires JTAG/SWD debug access)
- Analyze the verification routine for flaws
- Craft a payload that passes verification despite being modified

This is *research-level* work. Tools like ChipWhisperer for side-channel
analysis, or voltage glitching to skip the verification branch, are the kind
of attacks used.

### 2. Find Audeze's signing key

Extremely unlikely without insider access or a leak. Embedded signing keys
are usually kept in HSMs at the manufacturer.

### 3. Use the Maxwell V2

If the V2 uses a different SoC or has weaker security, this whole problem
might be sidestepped. Worth checking once V2 firmware can be obtained and
analyzed.

## What you CAN do without firmware mod

A lot, actually. The RACE protocol exposes most user-configurable behaviors
without needing to modify the firmware:

- L/R balance per source (different values for USB-C vs BT)
- Chatmix and sidetone levels
- EQ preset selection
- Audio source state (which NVDM key the firmware uses for balance)
- Battery / version queries

See [PROTOCOL.md](PROTOCOL.md) for full command reference.

**Things that genuinely require firmware mod** (and thus aren't possible
without solving the bootloader problem):

- Concurrent playback of multiple sources (BT + USB-C at once) — requires
  removing the audio router reset
- Changing the per-source EQ preset switching logic
- Adding new RACE commands
- Disabling Audeze's signature verification (irony)

## How the downgrader tool works

The [`maxwell-firmware-downgrader`](https://github.com/kats1123/maxwell-firmware-downgrader)
tool flashes **original, unmodified** Audeze firmware files. The signatures
are intact, so the bootloader accepts them. The only "trick" is that this
tool uses the FOTA SDK directly to send any version, bypassing the Audeze
app's in-app rollback restriction.

## Stuck FOTA recovery

If a flash attempt hangs and subsequent flashes also fail (we hit this):

1. Unplug USB-C entirely
2. Power off the headset (hold power button)
3. Wait 10 seconds
4. Power on the headset
5. Wait 20 seconds (full boot)
6. Plug USB-C back in
7. Try flashing again

This clears whatever state the device's FOTA module got stuck in. Don't try
software-level recovery scripts before doing this — the SDK functions that
*could* abort a stuck FOTA (`cancelFota`, `ClearResourceEx`, etc.) require
internal SDK state that's only valid mid-transfer, so they crash if called
standalone.

## Related security research

- [ERNW Whitepaper 74](https://static.ernw.de/whitepaper/ERNW_White_Paper_74_1.0.pdf):
  CVE-2025-20700, CVE-2025-20701, CVE-2025-20702 — Airoha RACE protocol
  vulnerabilities (RAM/flash read, no authentication on some commands). These
  are different bugs than the firmware signature issue but illustrate that
  Airoha-based devices have been successfully attacked at the hardware level.
- The Sony WH-1000XM4 (also Airoha) had its AES firmware key extracted via
  the ERNW attacks. Maxwell may be susceptible to similar attacks for
  *reading* its bootloader, even if signing remains protected.
