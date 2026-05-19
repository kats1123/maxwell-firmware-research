# First-Stage Bootloader

The Maxwell's actual first-stage bootloader lives in **flash**, not in
read-only chip ROM as we'd originally assumed. We dumped 76 KB of it
(`0x08000000`–`0x08013000`) via RACE flash reads (`cmd 0x0403`), which works
on the headset over USB HID — no JTAG needed.

## Chip-level partition descriptor table

At `0x08000000`, the bootloader stores an 8-entry partition descriptor
table. Each entry is 48 bytes with the layout:

```
+0x00  index   (4 bytes, LE)
+0x04  reserved (8 bytes, zeros)
+0x0C  flash_ptr (4 bytes, LE) — absolute flash address
+0x10  reserved (4 bytes, zeros)
+0x14  size    (4 bytes, LE)
+0x18  reserved (24 bytes, zeros)
```

Full table as it appears on a v1.0.1.74 unit:

| Entry off | idx | flash_ptr  | size       | What it is |
|-----------|-----|------------|------------|------------|
| 0x000 | 0 | 0x08001000 | 0x001000   | Boot state page 1 (4 KB) — sentinel/flag |
| 0x030 | 8 | 0x08002000 | 0x001000   | Boot state page 2 (4 KB) — sentinel/flag |
| 0x060 | 1 | 0x08003000 | 0x010000   | First-stage bootloader code (64 KB) |
| 0x090 | 3 | 0x08133000 | 0x1F9000   | Main firmware code partition 2 (2 MB) — contains the audio/BT/RACE code |
| 0x0C0 | 4 | 0x08013000 | 0x120000   | Main firmware code partition 1 (1.1 MB) |
| 0x0F0 | 6 | 0x084A1000 | 0x355000   | **FOTA inactive bank** (3.4 MB) — new firmware staged here |
| 0x120 | 7 | 0x087F6000 | 0x00A000   | **NVDM partition** (40 KB) — persistent user settings |
| 0x150 | 9 | 0x08450000 | 0x04B000   | Main firmware code partition 4 (300 KB) — overflow / auxiliary code |

(Note: partitions are not stored in flash-address order. The descriptor
table indexes them logically; their relative positions on the chip are as
shown by `flash_ptr`.)

This is **the partition table the bootloader uses**, distinct from the TLV
`0x0012` partition table inside firmware update files (which tells the
bootloader how to slice the decompressed image).

## Code regions inside the dump

Entropy analysis of the 76 KB dump:

| Range (runtime) | Type | Notes |
|-----------------|------|-------|
| `0x08000000`–`0x080001E0` | Data | Partition descriptor table (above) |
| `0x080001E0`–`0x08001000` | Empty (`0xFF`) | Padding |
| `0x08001000`–`0x08003000` | Empty (`0xFF`) | Boot state pages (zeroed in our dump — these get rewritten during boot for state tracking) |
| `0x08003000`–`0x08008700` | Code + data | First-stage bootloader's **text section** (~22 KB). Contains FOTA TLV parser, SHA-256, integrity check |
| `0x08008700`–`0x08008B00` | Data | Strings and small constants |
| `0x08008B00`–`0x0800B500` | Mixed | More code + a constant pool. Contains SHA-256 init constants at `0x08009A84`, debug strings for FOTA at `0x08008E30`–`0x08009800` |
| `0x0800B500`–`0x08013000` | Empty (`0xFF`) | Padding to end of partition |

## What the bootloader does (from its debug strings)

The bootloader's string table makes its job clear without needing full
disassembly:

```
'TLV_BASIC_INFO process'                  ← parses TLV 0x0011
'TLV_MOVER_INFO process'                  ← parses TLV 0x0012 (partition list)
'sha info process'                        ← parses TLV 0x0014 (per-partition SHAs)
'fota version process'                    ← parses TLV 0x0013 (version string)
'g_number_of_movers:%x, sha_number:%x'    ← validates count match
'g_sha_info_start_address:%x'             ← records where SHA table is
'is_basic_info_found:%x, is_mover_info_found:%x is_sha_info_found:%x is_version_info_found:%x is_nvdm_change_found:%x'
'Read record length in FOTA header failed.'
'Unknown FOTA TLV type value: %x'
'wrong tlv_length:%d, should be=%d'
'fota_check_fota_package_integrity:read fota partition info fail!'
'fota_check_fota_package_integrity:read flash fail!'
'fota_check_fota_package_integrity:%x,%x,%x,%x'
'fota_check_fota_package_integrity:read buffer fail!'
'fota_check_fota_package_integrity:package integrity pass'
'fota_data_length:address=%x,length=%x'
'fota_data_start_address:address=%x,length=%x'
'Integrity Check fail'
'version checksum does not match.'
'upgrade_flag is not set.'
'Begin FOTA upgrade'
'bl_fota_init fail'
'bl_fota_move_data'
'mover info is valid'
'mover is not valid'
'destination_address check failed. i:%d'
'source_address check failed. i:%d'
'OTA is 0'
'Erase is done. However, something wrong happened. ret_val:%d'
'Before erase start_addr:%x'
'hal_flash_erase ret:%d'
'hal_flash_write ret:%d'
'External flash is not supported!'
'g_fota_partition_start_address[%x]'
'flash ID =0x%x, 0x%x, 0x%x '
'NOR_init'
'Partition(%d) %x'
'Jump to addr %x'
'[CLK] Dynamic Clock Management: Enable'
'check partition table is valid or not partition_valid[%d] syncword[%x]'
'FOTA state read error'
'FOTA erase package not finish'
'sfi_index=%d'
'g_compression_type = %d'
```

**Notice what's NOT here**: no `ECC`, `ECDSA`, `RSA`, `secp256`, `ed25519`,
`signature`, `verify_signature`, `public_key`, or any other asymmetric crypto
reference. The verification is purely `g_compression_type` (decompress)
→ `g_number_of_movers` × SHA-256 (hash) → compare against `g_sha_info_start_address`
table. **There is no signature.**

## Confirmed crypto primitives in the bootloader

SHA-256 H[0..7] initial state values are at `0x08009A84`–`0x08009AA0`,
contiguous, matching the well-known constants:

```
0x08009A84:  67 E6 09 6A   ← H[0] = 0x6A09E667 (LE)
0x08009A88:  85 AE 67 BB   ← H[1] = 0xBB67AE85
0x08009A8C:  72 F3 6E 3C   ← H[2] = 0x3C6EF372
0x08009A90:  3A F5 4F A5   ← H[3] = 0xA54FF53A
0x08009A94:  7F 52 0E 51   ← H[4] = 0x510E527F
0x08009A98:  8C 68 05 9B   ← H[5] = 0x9B05688C
0x08009A9C:  AB D9 83 1F   ← H[6] = 0x1F83D9AB
0x08009AA0:  19 CD E0 5B   ← H[7] = 0x5BE0CD19
```

The SHA-256 K[0..63] round constants are immediately above this at
`0x08009A84 - 0x100` = `0x08009984`. The full standard SHA-256 constant
table is present, confirming a textbook implementation.

No other crypto constants (P-256 curve, Ed25519 base point, AES Sbox, etc.)
appear anywhere in the 76 KB dump. The chip can hash but cannot verify
signatures (at least not with anything baked into this code blob).

## Boot flow (from inferred state machine)

1. Chip resets at `0x00000000` (CPU vector). True boot ROM at `0x0` is
   unreadable via RACE — likely just a tiny IPL stub that:
   - Initializes critical hardware (clocks, flash controller, basic GPIO)
   - Jumps to the first-stage bootloader at `0x08003000`
2. First-stage bootloader (`0x08003000`–`0x08008700`) runs:
   - Reads the partition descriptor table at `0x08000000`
   - Reads the FOTA state pages (`0x08001000` and `0x08002000`) to determine
     if an upgrade is pending
   - If `upgrade_flag` is set (string `'Begin FOTA upgrade'`):
     - Run `fota_check_fota_package_integrity` on the staged firmware at
       `0x084A1000`
     - If integrity passes:
       - Erase the active partitions
       - Copy each "mover" (TLV-described chunk) from FOTA bank to active
       - Mark FOTA partition as consumed (clear `upgrade_flag`)
   - In any case (post-FOTA or normal boot), `Jump to addr %x` to the
     main firmware's reset vector (`0x08133000` per the partition table)
3. Main firmware runs from then on.

## Key NVDM partition (`0x087F6000`–`0x08800000`, 40 KB)

This is where Audeze stores persistent settings. The bootloader's
`'g_fota_partition_start_address[%x]'` debug message implies a global var
tracks each partition's start address. The NVDM partition specifically is
where keys like `0xF665` (USB-C balance), `0xF668` (BT/dongle balance) live
persistently.

After a factory reset, the active firmware re-initializes NVDM entries by
calling `nvdm_write_default(key, buf, len)` for each documented key (see
[FIRMWARE.md](FIRMWARE.md) NVDM key list).

We have **not** yet dumped the live NVDM contents — reads at `0x087F6000+`
worked for 36 KB but failed at `0x087FEB00` (likely flash region boundary).
A community contributor with another Maxwell could try a slower scan with
short timeouts to map out the working range.

## What this means for custom firmware

- The bootloader is **plaintext code in flash**, theoretically readable AND
  potentially writable. However, **flash writes via the RACE `0x402`
  PageProgram command are restricted to the FOTA partition** (`0x084A1000`–
  `0x087F5000`); writes to the bootloader region appear to be silently
  rejected or ignored. Without a flash-write privilege escalation, the
  bootloader itself can't be modified at runtime.
- Even if it could, modifying the bootloader is high-risk — a broken
  bootloader bricks the device permanently (no DFU mode, no recovery USB).
- The good news: we don't NEED to modify the bootloader for any of the
  patches we've done. SHA-256 verification is content-addressed and
  recomputable.

## Open questions

1. **What's in the boot state pages** (`0x08001000`, `0x08002000`)? They
   were all `0xFF` in our dump, meaning they were either freshly erased
   or hadn't been touched yet. After a few boots they likely contain the
   upgrade flag, last-good firmware index, boot counter, etc.
2. **Can flash writes outside the FOTA partition** be triggered through any
   path? E.g. via an exploit in the main firmware that calls
   `hal_flash_write` directly with a non-FOTA address. The bootloader
   string `'External flash is not supported!'` implies internal flash is
   the only target.
3. **What does the bootloader do on signature failure** (or rather,
   integrity failure)? Does it boot the previous firmware, or refuse to
   boot at all? We've only observed it refusing to commit the FOTA;
   normal boot of valid firmware always succeeds.
4. **Is there any anti-rollback protection?** The string
   `'version checksum does not match.'` hints at *some* version-related
   check, but it's not clear if downgrades are blocked. The
   `maxwell-firmware-downgrader` tool successfully downgrades all the way
   to v1.0.1.61, so any rollback protection is either off or trivial to
   defeat.
