# FOTA Process & Custom Firmware Flashing

## TL;DR

**You CAN flash custom (modified) firmware on the original Audeze Maxwell.**

Earlier versions of this writeup said this was blocked by an ECDSA signature
verification in the bootloader. **That was wrong.** The bootloader only
performs **SHA-256 integrity checks**, which are recomputable. With the right
recipe (updating both the outer SHA *and* the per-partition SHAs *and* the
LZMA stream-size field), a modified firmware passes integrity verification
and boots normally.

This was demonstrated end-to-end: a v1.0.1.74 image with patched L/R balance
defaults and a NOP'd "concurrent playback router-reset" was successfully
flashed and verified live via RACE flash reads.

## Verification chain (what we now know)

There is **no asymmetric signature** on the Maxwell firmware. We confirmed
this by reverse-engineering the first-stage bootloader at flash address
`0x08005000` (76 KB dumped via RACE `0x0403` page reads). Its debug-string
table makes the entire verification flow obvious:

```
'fota_check_fota_package_integrity:%x,%x,%x,%x'
'fota_check_fota_package_integrity:package integrity pass'
'Integrity Check fail'
'g_number_of_movers:%x, sha_number:%x'
'g_sha_info_start_address:%x'
'TLV_BASIC_INFO process'
'TLV_MOVER_INFO process'
'sha info process'
'fota version process'
```

No mention of ECC/ECDSA/RSA/secp256/ed25519 anywhere. The bootloader's SHA-256
constant table is at flash offset `0x08009A84` (the constant `0x6A09E667` is
the well-known SHA-256 H[0] initial value).

The verification is purely:

1. Read the FOTA package from flash
2. Parse its TLV header
3. For each "mover" (partition), compute SHA-256 of the decompressed bytes
4. Compare against the SHA hashes stored in TLV `0x0014`
5. If all hashes match → accept and swap banks

Since SHA-256 is keyless, we can recompute the hashes after modifying the
payload, and the bootloader has no way to know the firmware was patched.

## File format (relevant for custom builds)

```
0x0000-0x001F  SHA-256 of file[0x100:]                  (recomputable)
0x0020-0x00FF  0xFF padding
0x0100-0x010D  TLV 0x0011 = basic info (10 bytes)
               bytes 0-1:  01 01                          (flags)
               bytes 2-5:  00 10 00 00 = LE 0x1000        (LZMA stream offset)
               bytes 6-9:  ac 40 1f 00 = LE 0x1F40AC      (LZMA STREAM SIZE — must match actual)
0x010E-0x0129  TLV 0x0013 = version string (28 bytes)
0x012E-0x0165  TLV 0x0012 = partition table (52 bytes)
               4 entries × 12 bytes: src_off, dec_size, dst_flash_addr
0x0166-0x01E9  TLV 0x0014 = per-partition SHA-256 hashes (132 bytes)
               4 bytes count + 4 × 32-byte SHA-256
0x01EE-0x01FC  TLV 0x0020 = chip ID ("AB1568_Headset")
0x0200-0x0214  TLV 0x0021 = design name ("headset_ref_design")
0x0216-0x021A  TLV 0x00F0 = misc flag (1 byte: 0x01)
0x021B-0x0FFF  0xFF padding
0x1000+        LZMA-Alone compressed stream
```

The two fields that everyone misses (and which broke previous custom-firmware
attempts):

1. **TLV `0x0014` per-partition SHA-256 hashes.** Updating only `file[0:32]`
   isn't enough — each partition has its own SHA stored here, and the
   bootloader checks all of them.
2. **TLV `0x0011` LZMA stream size field at bytes 6-9.** When you recompress
   with a different encoder (e.g. Python's `lzma`), the compressed stream
   length changes; this field must be updated to match, otherwise the
   bootloader reads beyond the actual stream and decompression fails.

## Working custom-firmware recipe

```python
import hashlib, lzma, struct

with open("Maxwell_v1.0.1.74_XBOX_headset.bin", "rb") as f:
    raw = f.read()

# 1. Decompress
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
decompressed = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
header = bytearray(raw[:0x1000])

# 2. Apply your byte-level patches to `decompressed`
# (e.g. L/R balance immediate values, NOPs, etc.)
decompressed_bytes = bytearray(decompressed)
decompressed_bytes[0x186C72:0x186C76] = b'\x49\xf6\x8d\x23'  # example: BT balance L=141 R=154

# 3. Recompute TLV 0x0014 per-partition hashes
#    Walk the partition table (TLV 0x0012 at 0x12e), slice decompressed by
#    each partition's size, SHA-256 each, write back into TLV 0x0014 slots.
pt_count = struct.unpack("<I", header[0x132:0x136])[0]
hash_tlv_offset = 0x166
offset = 0
for i in range(pt_count):
    sz = struct.unpack("<I", header[0x136 + i*12 + 4 : 0x136 + i*12 + 8])[0]
    h = hashlib.sha256(decompressed_bytes[offset:offset+sz]).digest()
    header[hash_tlv_offset + 8 + i*32 : hash_tlv_offset + 8 + (i+1)*32] = h
    offset += sz

# 4. Recompress with the exact Audeze LZMA params
filters = [{"id": lzma.FILTER_LZMA1, "dict_size": 16384, "lc": 3, "lp": 0, "pb": 2}]
compressed = lzma.compress(bytes(decompressed_bytes), format=lzma.FORMAT_ALONE, filters=filters)
compressed = bytearray(compressed)
# Patch LZMA header's decompressed-size field (Python writes 0xFF...; Audeze writes actual)
compressed[5:13] = struct.pack("<Q", len(decompressed_bytes))

# 5. Update TLV 0x0011 LZMA stream size (THE STEP EVERYONE MISSES)
header[0x10A:0x10E] = struct.pack("<I", len(compressed))

# 6. Reassemble + recompute outer SHA-256
out = bytes(header) + bytes(compressed)
out_h = hashlib.sha256(out[0x100:]).digest()
out = out_h + out[32:]

with open("Maxwell_v1.0.1.74_CUSTOM.bin", "wb") as f:
    f.write(out)
```

A working implementation of this is at
`tools/firmware_patcher_v5.py` in the [audeze-tray]
(https://github.com/kats1123/audeze-tray) project (when published).

## Transport-layer requirements (the other gotcha)

If you're not using Audeze's `MaxwellFlasherGUI.exe` (which wraps
`AirohaHidCoreLib.dll`), the RACE FOTA flow itself has Audeze-specific
quirks not documented in the public [ERNW race-toolkit]
(https://github.com/auracast-research/race-toolkit) (which targets Sony
devices):

### 1. State machine init

ERNW's Sony flow does `FotaWriteState 0x0102` and `0x1002` *after* `FotaStart`.
**Audeze requires `FotaWriteState 0x0101` BEFORE `FotaStart`** — otherwise
`FotaStart` returns `rc=2` ("already started" / lock not released). The
correct opening sequence is:

| Step | Cmd ID | Payload | Purpose |
|------|--------|---------|---------|
| 1 | `0x1C00` | `00`         | PartitionInfoQuery — returns FOTA target addr/length |
| 2 | `0x1C06` | `01 01`      | **Init state (Audeze-specific)** |
| 3 | `0x1C08` | (empty)      | FotaStart — should return `rc=0` |
| 4 | `0x1C0A` | (empty)      | FotaStartTransaction |
| 5 | `0x1C06` | `01 02`      | State transition |
| 6 | `0x1C06` | `10 02`      | State transition |
| 7 | `0x0404` | erase params | ErasePartition |
| 8 | `0x0402` | per page     | WriteFlashPage (looped) |
| 9 | `0x1C01` | (empty)      | FotaIntegrityCheck — bootloader verifies SHA-256 |
| 10 | `0x1C06` | `11 02`     | State transition |
| 11 | `0x1C02` | (empty)      | FotaCommit — reboot |

### 2. HID fragmentation

Maxwell's HID Output Report is hard-capped at **62 bytes** (Report ID `0x06` +
61 data bytes per the report descriptor). The full WriteFlashPage payload for
even one 256-byte page is `2 + 1 + 4 + 256 = 263` bytes, requiring 5
fragments.

Each fragment is a separate HID Output Report containing **a length byte
prefix** indicating how many actual RACE bytes follow:

```
HID Output Report (62 bytes):
  byte 0:   0x06              (HID Report ID)
  byte 1-2: <H length          (number of valid RACE bytes in this report)
  byte 3+:  RACE data           (up to 59 bytes)
  rest:     0x00 padding to 62
```

The chip reassembles fragments based on the RACE header's `length` field in
the first fragment and the per-fragment HID length bytes.

(ERNW's `transport.py` `device.write(outbuf)` works *only* if the OS HID
driver auto-fragments large writes. On Windows + hidapi, writes >62 bytes
are silently truncated — manual fragmentation is required.)

### 3. WriteFlashPage payload structure

```
storage_type   (1 byte, 0)
num_pages      (1 byte)
per page (num_pages × 261 bytes):
  checksum     (1 byte — see below)
  address      (4 bytes, LE — absolute flash address)
  data         (256 bytes — page contents)
```

The **checksum is NOT a simple XOR.** It's a CRC-style algorithm using two
256-byte lookup tables. Implementation from `librace/util.py`:

```python
def fota_checksum(data: bytes) -> int:
    cs = 0
    for b in data:
        cs = TBL1[cs ^ b]
    high = TBL2[cs >> 4]
    low  = TBL2[cs & 0xF]
    return (high | (low << 4)) & 0xFF
```

A wrong checksum causes `WriteFlashPage` to return `rc=0x0A` ("checksum
error at <addr>"). This is what you'll see if you copy ERNW's tool and assume
the checksum is XOR.

## High-level FOTA flow (with our findings)

1. Host PC opens HID interface — use **`0x4B18`** (Xbox dongle), **`0x4B19`**
   (PS dongle), **`0x4B1A`** (PS USB-C headset), or **`0x4B1E`** (Xbox USB-C
   headset)
2. Host calls `initializeAirohaSDK(VID, PID)` from `AirohaHidCoreLib.dll`, OR
   talks RACE directly over HID
3. RACE FOTA flow as above
4. After commit: device reboots, runs new firmware from previously-inactive
   bank, marks it active

The dual-bank design means a successful flash writes to the inactive bank
and the device swaps banks on reboot; a power loss mid-flash should leave
you on the previous firmware rather than bricked. (Don't take this as a
guarantee.)

## Stuck FOTA recovery

If a flash attempt hangs (or your custom firmware has a bug that causes
boot to fail) and subsequent flashes also fail:

1. Unplug USB-C entirely
2. Power off the headset (hold power button)
3. Wait 10 seconds
4. Power on the headset
5. Wait 20 seconds (full boot)
6. Plug USB-C back in
7. Try flashing again

This clears whatever state the device's FOTA module got stuck in. SDK
recovery functions (`cancelFota`, `ClearResourceEx`, `setFactoryReset`)
require internal SDK state that's only valid mid-transfer, so they crash
if called standalone — don't try them before doing the hardware power-cycle.

## What this opens up

Now that custom firmware is flashable, the following becomes possible
without any signing-key access or hardware-level attacks:

- ✅ **L/R balance per-unit tuning** — patch the factory-default NVDM
  initialization (movw immediate values at file offsets `0x186C72`/`0x186CA4`).
  Survives factory reset.
- ✅ **Concurrent playback** (partial) — NOP the router-reset BL at file
  offset `0x135C66`. (USB-C+BT path works; BT+dongle path appears to have
  additional reset call sites still to identify.)
- 🟡 **EQ preset modification** — possible but requires DSP coefficient
  knowledge
- 🟡 **LED behavior** — patch the LED state machine
- 🟡 **Codec / sample rate changes** — modify the audio init code
- 🟡 **Button remapping** — find input handler dispatch table and patch
- 🟡 **Chatmix curve** — modify the chatmix→gain mapping
- 🟡 **Boot logo / startup chime** — if these live in flash as data
- ❌ **Bootloader changes** — the first-stage bootloader at `0x08000000` is
  in flash (and thus theoretically writable) but writes to that region are
  outside the FOTA-writable partition, so it's still read-only via FOTA. The
  true chip ROM at `0x00000000` is also unreadable (RACE memory reads to it
  crash the chip; reads work for `0x14xxxxxx` SRAM and `0x08xxxxxx` flash).

## What about the GUI flasher path?

If you replace `Maxwell_v1.0.1.74_XBOX_headset.bin` in the bundled
[downgrader tool](https://github.com/kats1123/maxwell-firmware-downgrader)
folder with a properly-patched custom build, the existing
`MaxwellFlasherGUI.exe` will flash it for you — the DLL handles fragmentation
and the state-machine init transparently. (Yes, this means Audeze's own SDK
will happily flash your custom firmware. The "blocking" was always just the
SHA-256 integrity check in the bootloader, which we now correctly recompute.)

Same-version reflashes (v74 → v74) may be refused by the DLL; downgrade to
v63 first to force a flash on the next v74 upgrade.

## Related security context

- [ERNW Whitepaper 74](https://static.ernw.de/whitepaper/ERNW_White_Paper_74_1.0.pdf)
  and [CVE-2025-20702](https://nvd.nist.gov/vuln/detail/cve-2025-20702):
  unrelated to firmware patching but useful for understanding the
  Airoha-family bootloader / RACE protocol design.
- ERNW's [race-toolkit](https://github.com/auracast-research/race-toolkit)
  is the basis for the FOTA primitives but their Sony-derived flow doesn't
  work as-is on Audeze (see the state-machine init quirk above).
