# Tools

Working scripts used during research. Treat as starting points, not
polished products.

## `firmware_patcher.py` — build custom v1.0.1.74 firmware

Decompresses a stock Maxwell v74 firmware, applies byte patches (L/R
balance, concurrent playback), recomputes the per-partition SHA-256 hashes
(TLV `0x0014`), updates the LZMA stream size field (TLV `0x0011`),
recompresses, and recomputes the outer SHA-256. Output is a firmware
that flashes successfully via the Audeze tool or any RACE FOTA path.

```bash
python firmware_patcher.py \
    --in /path/to/Maxwell_v1.0.1.74_XBOX_headset.bin \
    --out /path/to/my_custom_v74.bin \
    --usb-l 141 --usb-r 143 \
    --bt-l  141 --bt-r  154
```

Options:

- `--usb-l N --usb-r N` — USB-C L/R defaults (factory: 141/149)
- `--bt-l N --bt-r N` — BT/dongle L/R defaults (factory: 147/147)
- `--no-concurrent` — skip concurrent playback patches
- `--dry-run` — apply patches but don't write output
- `--verify-only` — just check the input firmware's integrity, don't patch

Then flash the resulting `.bin` via `MaxwellFlasherGUI.exe` from the
[downgrader tool](https://github.com/kats1123/maxwell-firmware-downgrader)
(replace the bundled `Maxwell_v1.0.1.74_XBOX_headset.bin` with your patched
build before launching).

After a successful flash, **do a factory reset on the headset** so the
new NVDM defaults take effect. Without a reset, the old balance values
stay loaded; the patches only execute during NVDM (re-)initialization.

## `dump_bootloader.py` — read 76 KB of bootloader from live device

Uses RACE `0x0403` page reads over USB HID to dump the first-stage
bootloader at flash address `0x08000000`–`0x08013000`. Requires the
headset connected via USB-C cable (PID `0x4B1E` for Xbox, `0x4B1A` for
PS). Saves to `maxwell_bootloader.bin` in the working directory.

Read-only — safe to run.

## `parse_dispatch.py` — print the RACE command dispatch table

Reads a decompressed Maxwell firmware and prints the 30-entry RACE
command dispatch table with known commands annotated. Useful for
finding undocumented command ranges to investigate.

## `find_all_nvdm_defaults.py` — scan factory NVDM writes

Disassembles the firmware to find every `BL nvdm_write_default` call
site, identifying the NVDM key, default value, and length. Shows what
settings are patchable persistently (will be applied after a factory
reset).

## Dependencies

```bash
pip install hidapi capstone
```

Some scripts also require:
- `lzma` (standard library, Python 3.x)
- `pefile` (for DLL analysis scripts, not included here)
