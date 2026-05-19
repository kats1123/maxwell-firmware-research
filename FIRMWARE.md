# Firmware Format & Architecture

## TL;DR

- Firmware files are LZMA-Alone compressed (NO encryption)
- Decompresses to a 3.2 MB ARM Cortex-M4 image
- Header at offset 0–0x21A contains version info, partition table, per-partition SHA hashes, LZMA stream-size field, and a top-level SHA-256 hash of the rest of the file
- All integrity is **SHA-256 only** — no asymmetric signatures — so the firmware is **modifiable and reflashable** if you correctly recompute the right hash fields (see [FLASHING.md](FLASHING.md))

## File layout (complete)

A raw `Maxwell_v1.0.1.NN_XBOX_headset.bin` file is structured as:

```
0x0000-0x001F : 32-byte top-level SHA-256 hash of bytes[0x100:]   ← recomputable
0x0020-0x00FF : 0xFF padding
0x0100-0x010D : TLV 0x0011 = basic info (10 bytes value)
                bytes 0-1: 01 01            (flags)
                bytes 2-5: 00 10 00 00      (LE 0x1000, LZMA stream start)
                bytes 6-9: ac 40 1f 00      (LE LZMA STREAM SIZE — must be exact)
0x010E-0x0129 : TLV 0x0013 = version string ("vN.N.N.NN\0" + 0xFF padding)
0x012E-0x0165 : TLV 0x0012 = partition table (52-byte value)
0x0166-0x01ED : TLV 0x0014 = per-partition SHA-256 hashes (132 bytes)
0x01EE-0x01FC : TLV 0x0020 = chip identifier ("AB1568_Headset")
0x0200-0x0214 : TLV 0x0021 = design name ("headset_ref_design")
0x0216-0x021A : TLV 0x00F0 = misc flag (1 byte: 0x01)
0x021B-0x0FFF : 0xFF padding
0x1000+       : LZMA-Alone compressed stream
```

The 3 fields that must be updated correctly when modifying firmware:

1. **TLV `0x0014`** (per-partition hashes) — for each modified partition,
   recompute SHA-256 over its decompressed bytes and overwrite the
   corresponding 32-byte slot.
2. **TLV `0x0011` LZMA stream size** (bytes 6-9 of value) — recompression
   changes the compressed stream length; this field must match exactly or
   the bootloader reads past actual data and decompression fails.
3. **`file[0:32]`** — top-level SHA-256 of `file[0x100:]`. Recompute LAST,
   after all other changes.

### Partition table

At offset `0x12E` (TLV `0x0012`, value length `0x34`):

```
04 00 00 00                    count = 4 entries

Entry 1: 00 10 00 00  00 40 11 00  00 30 01 00
         src=0x001000  size=0x114000  dst_flash=0x013000
Entry 2: 00 50 11 00  00 b0 19 00  00 30 13 00
         src=0x115000  size=0x19b000  dst_flash=0x133000
Entry 3: 00 00 2b 00  00 40 01 00  00 c0 32 00
         src=0x2b0000  size=0x014000  dst_flash=0x32c000
Entry 4: 00 40 2c 00  00 b0 04 00  00 00 45 00
         src=0x2c4000  size=0x04b000  dst_flash=0x450000
```

- `src` = offset into the (decompressed image + header) — i.e. position of
  this partition's start within the source stream (with 0x1000 base offset
  for the file header). For the first partition, `src=0x1000` = start of
  LZMA stream in the file.
- `size` = number of bytes in this partition's decompressed content
- `dst_flash` = where this partition lives on the SPI flash chip after
  unpacking (`0x08013000`, `0x08133000`, etc. at runtime — XIP base
  `0x08000000` + this offset)

Partition `size` sums to `0x30E000` = full decompressed firmware. The
mapping `flash_addr = 0x0801F000 + decompressed_offset` (confirmed live
via RACE reads) tells us:

- Partition 1 decompressed runs from `0x08013000` to `0x08127000` at runtime
- Partition 2 decompressed runs from `0x08133000` to `0x082CE000` (contains
  reset vector at `0x08133000`)
- Partitions 3 and 4 live at `0x0832C000` and `0x08450000` respectively

The "gap" between partitions is intentional (boot region, NVDM region, FOTA
inactive bank). See [BOOTLOADER.md](BOOTLOADER.md) for the full chip-level
partition map.

## LZMA-Alone parameters

The LZMA stream at offset `0x1000` uses:

| Field | Value |
|-------|-------|
| Properties byte | `0x5D` → `lc=3, lp=0, pb=2` (standard) |
| Dictionary size | `0x4000` = 16 KB (small — chip is RAM-constrained) |
| Decompressed size | `0x30E000` = 3,203,072 bytes (explicit, NOT 0xFFFF...) |

Python's `lzma` module is strict about the size field — it rejects streams with
explicit size. The actual Maxwell decoder is more lenient. To decompress with
Python you have to patch the size to `0xFFFFFFFFFFFFFFFF` first.

## Decompression

```python
import lzma, struct

with open("Maxwell_v1.0.1.74_XBOX_headset.bin", "rb") as f:
    raw = f.read()

payload = raw[0x1000:]
# Patch size field to streaming mode so Python lzma accepts it
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
decompressed = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)

# decompressed is now 3,203,072 bytes - the raw ARM Cortex-M4 firmware
with open("fw_decompressed.bin", "wb") as f:
    f.write(decompressed)
```

Alternatively, use [airoha-firmware-parser](https://github.com/ramikg/airoha-firmware-parser):

```
python airoha_decrypt.py --no-decrypt --from Maxwell_v1.0.1.74_XBOX_headset.bin --to fw.dec.bin
```

## Architecture

| Property | Value |
|----------|-------|
| **SoC** | Airoha AB1568 (= MediaTek MT2822) |
| **CPU** | ARM Cortex-M4F |
| **SDK** | Airoha IoT_SDK_for_BT_Audio v3.4.1 |
| **Code load base** | `0x08000000` (standard Cortex-M flash region) |
| **SRAM region** | `0x14000000` |
| **Audio HW regs** | `0x42000000`–`0x4200FFFF` (78 distinct addresses used) |
| **Total code** | ~3.2 MB decompressed |

### Memory map (best-known)

```
0x08000000+    ROM/Flash, code           (~2.1 MB used)
0x14000000+    SRAM (data, audio mixer)
0x14201xxx     Per-stream gain config structs
0x14229E58     Audio context base (gain bytes, channel selectors)
0x14230Bxx     Factory EQ defaults (RAM mirror)
0x4200xxxx     Audio hardware registers (memory-mapped I/O)
```

### Audio hardware register heat map

| Block | Inferred purpose | Distinct regs | Total refs |
|-------|------------------|--------------:|-----------:|
| `0x42000xxx` | Audio TOP / clock + main FIFO | 15 | **102** |
| `0x42001xxx` | DAC / output stage | 6 | 6 |
| `0x42002xxx` | I2S external interface | 6 | 12 |
| `0x42003xxx` | Audio FIFO 2 / DMA | 2 | 2 |
| `0x42004xxx` | Codec interconnect | 8 | **53** |
| `0x42005xxx` | Echo cancellation (mic) | 9 | 9 |
| `0x42006xxx` | DSP coefficient memory | 6 | 11 |
| `0x42007xxx` | PEQ filter bank 2 | 1 | 2 |
| `0x42008xxx` | Power management | 2 | 3 |
| `0x4200Axxx` | Bluetooth audio interface | 3 | 3 |
| `0x4200Cxxx` | LE Audio (LC3) | 2 | 2 |
| `0x4200Dxxx` | Game/Chat USB endpoint | 1 | 1 |
| `0x4200Exxx` | Audio routing / DAC tuning | 4 | 9 |
| `0x4200Fxxx` | DMA descriptors / buffer pointers | 13 | 21 |

> **Note**: block labels are *inferred* from address patterns and reference
> frequency. AB1568 datasheet is not public. Most-referenced registers
> (`0x420008E0` with 78 refs, `0x42004136` with 43 refs) are almost certainly
> the main audio FIFO write/read ports.

### Strings in decompressed firmware

Some useful debug strings in the decompressed payload:

| Offset | String | What it tells us |
|--------|--------|------------------|
| ~0x267E10 | `IoT_SDK_for_BT_Audio_V3.4.1.AB1565_AB1568` | SDK version |
| ~0x272495 | `Audeze Maxwell XBOX Headset` | Product USB descriptor |
| ~0x2724C0 | `Audeze Maxwell Chat` | Chat USB audio device name |
| ~0x2724D8 | `Audeze Maxwell Game` | Game USB audio device name |
| ~0x26988X | `AT+EAUDIO=VOL_STREAM_2A2D` | Volume command for regs 0x2A-0x2D |
| ~0x26993X | `AT+EAUDIO=VOL_STREAM_6A6D` | Volume command for regs 0x6A-0x6D |
| ~0x26DD9X | `gain_value_mapping` + `audio_nvdm` | NVDM key names for gain LUT |
| ~0xED8FC | `clk_skew_compensate_by_sw_algorithm` | Clock drift compensation |

## NVDM key inventory

The firmware writes "factory defaults" to NVDM via
`nvdm_write_default(key, buf, len)` at runtime address `0x081AF824` (i.e.
file offset `0x190824`). We scanned the firmware for all BL call sites to
this function and recovered 17 distinct call sites covering 11 unique NVDM
keys. After a factory reset, the patched code at these sites is what writes
new defaults — so patching the immediate values lets us change shipped
defaults persistently.

| NVDM key | Length | Default | File offset of `movw r0,#key` | Purpose |
|----------|--------|---------|-------------------------------|---------|
| `0xF665` | 4 | `0x958D` (L=141 R=149) | `0x186CAC` | **USB-C audio source balance** (state=10) |
| `0xF668` | 4 | `0x9393` (L=147 R=147) | `0x186C7A` | **BT/dongle audio source balance** (state≠10) |
| `0xF66E` | 2 | `0x0901` | `0x186CDE` | **Unknown audio flag** — written by same factory init function. Two-byte value `09 01`. Possibly source-state mask, codec selector, or audio-routing flag. |
| `0xE091` | 6 | ? | `0x18CA3E` (?) | Unknown 6-byte struct |
| `0xE1E0` | 12 | first 2 bytes `0x7FFF` | `0x248A6C` | Likely **volume / gain limiter struct**. `0x7FFF` = INT16 max — classic max-cap value. 12 bytes suggests {max_left, max_right, current_left, current_right, ...} or biquad coefficients. |
| `0xE1E1` | ? | ? | (related) | Companion to `0xE1E0` |
| `0xE1E5` | 8 | ? | `0x247B32` | Unknown 8-byte struct |
| `0xE301` | 16 / 564 | varies (two writes) | `0x24B9B6` / `0x24BA50` | Probably **EQ preset / DSP coefficients**. 564 bytes = 141 floats or 564 bytes ≈ enough for ~14 biquad sections (40 bytes each) |
| `0xE304` | 194 | ? | `0x24B97C` | Companion to `0xE301`, another **DSP coefficient block** |
| `0xE400` | 20 | (error code) | `0x18669C`, `0x186D40` | **Error log** — written when factory init operations fail |
| `0x0012` | (varies) | ? | `0x1904E2` | Probably misidentified — overlaps with partition table TLV tag |

> **Open questions**: NVDM `0xF66E`, `0xE1E0`, `0xE301`, and `0xE304` are
> particularly interesting because they live in the same factory-init code
> path as the L/R balance values. Decoding what these control is a high-ROI
> RE target. If `0xE1E0` is the volume cap (very likely given the `0x7FFF`
> default), patching it could enable louder-than-stock output for users who
> want it. If `0xE301`/`0xE304` are EQ coefficients, patching them could
> change the headset's baseline sound signature.

## Tools used

- **Ghidra** 12.0.4 — disassembly and decompilation, ARM Cortex-M little-endian
- **Python lzma** module — decompression
- **Capstone** disassembler — for scriptable scanning (BL target hunting, etc.)
- **airoha-firmware-parser** — drop-in decompression script
