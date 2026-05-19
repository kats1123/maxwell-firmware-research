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
| **RTOS** | **FreeRTOS** (confirmed by string literals: `freertos`, `Tmr Svc`, `stack overflow: %x %s`, `port.c`) |
| **Code load base** | `0x08000000` (standard Cortex-M flash region) |
| **SRAM region** | `0x14000000` |
| **Audio HW regs** | `0x42000000`–`0x4200FFFF` (78 distinct addresses used) |
| **Total code** | ~3.2 MB decompressed |

## FreeRTOS task model

### Central task-def array

There is a static `task_def_t[]` array at file offset `0x2741DC` (runtime
`0x082917DC`) with 20-byte entries:

```c
struct task_def {
  void (*entry_fn)(void *param);  // +0x00  (Thumb-mode pointer, low bit set)
  const char *name;               // +0x04
  uint32_t stack_size;            // +0x08  (in words — multiply by 4 for bytes)
  uint32_t reserved;              // +0x0C  (always 0 in observed entries)
  uint32_t priority;              // +0x10
};
```

Six tasks are created from this array. These are the **core dispatch tasks**:

| Task | Entry function | Stack (words) | Priority | Likely role |
|------|---------------|---------------|----------|-------------|
| `UI_realtime` | `0x081d27f0` | 0x177 (375) | 7 | Real-time UI / button handling |
| `bt_task` | `0x0818012c` | 0x300 (768) | 6 | Bluetooth stack main loop |
| `ATCI` | `0x081d96f0` | 0x300 (768) | 5 | AT-command interpreter (handles `AT+ECHAR=...`, `AT+EAUDIO=...`) |
| `race command` | `0x081757c8` | 0x200 (512) | 4 | **RACE protocol task** — every HID RACE packet ends up here |
| `AM_Task` | `0x081a9448` | 0x380 (896) | 7 | Audio Manager — likely the main audio routing/mixing task |
| `controler_test_task` | `0x081da20c` | 0x280 (640) | 6 | Controller test mode |

**Implication for the runtime balance behavior**: All RACE balance writes
(cmd `0x0900` sub `0x29`/`0x2A`) are processed by `race command` task at
priority 4 — i.e. they run cooperatively with audio (`AM_Task`, pri 7) and
BT (`bt_task`, pri 6). Audio is higher priority so RACE writes can be
preempted. The balance write does NOT happen in interrupt context.

### Per-subsystem tasks (separate creation paths)

Additional named tasks exist outside the central array — each subsystem
creates its own via separate calls (likely `xTaskCreate` or a wrapper):

| Name | Where loaded | Notes |
|------|--------------|-------|
| `charger_task` | file `0x140ab4` (data ref), other ref at `0x19bec8` | Charging subsystem |
| `audio_codec_task` | file `0x197e68` | Audio codec subsystem |
| `Linear_task` | file `0x19bed4` | Unknown — likely Linear PCM stream task |
| `battery_charger_task` | file `0x19c664` | Battery management |
| `ui_shell_task` | file `0x1a04cc` | UI shell / event dispatch |
| `DTM_TASK`, `DPR_TASK`, `DAV_TASK`, `DHP_TASK` | string cluster at file `0xeb7ba`–`0xeb7d8`, followed by a function-pointer table starting at `0xeb7e0` (entries like `0x08038680`, `0x08038694`, ...) | Lower-layer Bluetooth subsystem tasks. DTM=Direct Test Mode, DPR=?, DAV=Audio/Video Distribution Profile?, DHP=? — likely BT controller / HCI layer |

Static task creation logging format string `xCreate task %s, pri %d` at
runtime `0x08291e18` is referenced once by code at `0x081d975e` — that
caller specifically logs `bt_task` creation. Other task creates are not
logged through this format string.

### Implications

1. **RACE handling is task-context, not ISR-context.** Any RACE handler
   can do work that blocks (NVDM read/write, mutex acquire), and runs at
   priority 4 — below audio and BT.
2. **The 'race command' task is the single point where RACE-driven state
   changes happen.** It pulls messages off a queue (probably built from
   USB HID + BT SPP transport adapters) and dispatches via the table at
   `0x0828A8E0` (see PROTOCOL.md).
3. **Looking for who else can mutate audio state**: now we know to look
   for tasks that share state with `AM_Task` (priority 7). If a separate
   task or callback can rewrite `0x142039AC`, it must coordinate with
   AM_Task through a mutex or queue. Mapping the synchronization between
   AM_Task and the loader functions (`0x081DDFD4`/`0x081DE2E4`) is the
   next step.

### Memory map (best-known)

```
0x08000000+    ROM/Flash, code           (~2.1 MB used)
0x14000000+    SRAM (data, audio mixer)
0x14201xxx     Per-stream gain config structs
0x14229E58     Audio context base (channel selectors at +0x3B/+0x6C/+0xCB/+0xFC)
0x14203900+    Audio sub-region: per-channel slot table (32 bytes per slot, marker 0x43 every 32 bytes)
0x142039AC     ★ LIVE L/R BALANCE BUFFER (4 bytes: L, R, slider, dir) — single shared buffer for USB-C AND dongle audio
0x14230Bxx     Factory EQ defaults (RAM mirror)
0x4200xxxx     Audio hardware registers (memory-mapped I/O)
```

### NVDM-to-runtime balance loader functions — exhaustive caller map

Two functions in firmware load `0x142039AC` from NVDM based on current
source state (`NVDM 0xF702`):

| Function | Code addr | Inner NVDM read | Direct BL callers | Indirect (literal-pool) callers |
|----------|----------|------------------|-------------------|--------------------------------|
| Loader A (async) | `FUN_0x081DDFD4` | `bl 0x814fed8` | **1** — exactly `0x817B250` (inside cmd 0x0900 handler) | **0** |
| Loader B (sync)  | `FUN_0x081DE2E4` | `bl 0x814feac` | **0** | **0** |

**Loader B is dead code.** Zero callers anywhere in the firmware (verified
by exhaustive scan of BL/BLX instructions and 4-byte-aligned literal-pool
entries containing the function address with or without the Thumb bit set
— see `tools/find_loader_callers.py`).

**Loader A has exactly one caller**: the cmd 0x0900 balance-write handler.
It is never invoked from any other code path (no boot init, no source
switch, no message dispatch, no function pointer table).

Both loaders pick `NVDM 0xF665` if state==10 (USB-C), else `0xF668`, and
write the result into `0x142039AC`. Since Loader B is unreachable, the
`NVDM 0xF668` (BT/dongle balance) key is effectively **write-only from
factory init** — nothing in the firmware ever reads it at runtime.

### Source-state machine (December 2025 — task #6 findings)

There is no in-RAM cached "current source" variable. Source state is
read from **NVDM `0xF702`** every time something consults it.

**`FUN_0x0817B2F4` — the canonical "get source state" function**:

```c
uint8_t get_source_state(void) {
  uint8_t state = 0xFF;  // default if read fails
  if (nvdm_read(0xF702, &state, 1) == 0)
    return state;
  return 0;  // log + return 0 on failure
}
```

**6 callers of this getter** (recovered via exhaustive BL/B.W scan):

| Call site | Where (context) |
|-----------|-----------------|
| `0x0817B874` | RACE handler area (likely cmd 0x0900 sub 0x2F path) |
| `0x081C96F8` | Unidentified — high-priority next investigation |
| `0x081DDFD8` | Inside slider handler `FUN_0x081DDFD4` (Loader A) |
| `0x081DE09C` | Inside balance writer `FUN_0x081DE094` |
| `0x081DE160` | Also inside balance writer (different code path) |
| `0x081DE2E8` | Inside dead-code Loader B `FUN_0x081DE2E4` |

A sister function `FUN_0x0817B334` reads **NVDM `0xF700`** (note: a
DIFFERENT key). 0xF700 is currently undocumented and only used here —
add to NVDM key inventory open questions.

**The source-state DISPATCHER `FUN_0x08154BA8`** is invoked from
5 sites (4 B.W tail calls + 1 BL):

| Call site | Kind |
|-----------|------|
| `0x08152D78` | B.W (tail) |
| `0x08152DEA` | B.W (tail) |
| `0x08152E70` | B.W (tail) |
| `0x08152EF2` | B.W (tail) |
| `0x081530EA` | BL |

The dispatcher receives a **transition code in `r4`** (values 1, 2, 4,
0xF1, 0xF2 are recognized; others fall to default) — distinct from the
NVDM `0xF702` value. Each transition code triggers a specific reset
function:

| Transition code | Action |
|-----------------|--------|
| `1` | Call `0x08156F48` (audio context reset — **this is the call the user's patch NOPs at `0x08154C66`**) |
| `2` | Call `0x081590A8` |
| `4` | Call `0x0815A508` |
| `0xF2` | Call `0x08156F48` (same as state 1) |

A second nearby dispatcher at `0x08154C9C+` handles a related state set
(values 1, 2, 4, 0xF1) and calls different reset functions
(`0x08156F9C`, `0x081590BC`, `0x0815A51C`). The user's PATCHES.md
references this as the "site 2" patch (NOP at `0x135CC4`), though in
the current custom build only the `0x135C66` patch is applied.

**Implication for runtime balance**: nothing in the source-state
dispatcher reads `0x142039AC` or calls the loader cluster. So even
when the chip undergoes a source transition (USB plugged, BT connected,
etc.), the runtime balance buffer is NOT reloaded from NVDM. Confirms
empirical observation that physical source switch doesn't change the
runtime balance.

### Boot chain — full sequence

**Discovered December 2025 via reset-handler tail-call analysis.** The reset
handler's tail-call uses `BX r0` (not `BL`) so it was invisible to BL/B.W
scans. The address `0x081D9355` (Thumb) lives in the reset-handler literal
pool at runtime `0x08133154` and is loaded into `r0` immediately before the
final `BX r0` at `0x081330B8`.

```
Reset vector (chip ROM)
  → bootloader at 0x08003000 (validates, decompresses LZMA, loads partitions)
  → partition 2 reset handler at 0x08133000
    - CPSID i (disable interrupts)
    - Setup MSP, NVIC table base
    - Zero-init a peripheral region (~0x04025xxx)
    - 6× memcpy from flash to SRAM/peripheral RAM (.data init — see SRAM map)
    - BLX 0x081DF03C (hardware register clock/power init)
    - BX  0x081D9355 ← TAIL CALL TO MAIN()
                       ┴── never returns
```

### Main entry: `FUN_0x081D9354`

This is the firmware's actual `main()`. It's never `BL`-called from
anywhere — it's only reached via the reset-handler's final `BX r0`.
Function ends in an infinite branch-to-self at `0x081D941A` (post-
scheduler-start unreachable code).

Execution sequence at boot:

| Order | Code | Purpose |
|-------|------|---------|
| 1 | `BL 0x081DB048` | (TBD — unidentified subsystem init) |
| 2 | `BL 0x081C2FB8` | (TBD — unidentified subsystem init) |
| 3 | Read **NVDM `0xF666`** (1 byte) | sub-config — low 4 bits passed to `BL 0x081AFE04` (stores to a 1-byte SRAM flag) |
| 4 | Read **NVDM `0xF667`** (1 byte) | sub-config — full byte passed to `BL 0x081C9B84` |
| 5 | `BL 0x081D40B0` | Check (returns 0/non-0 — affects branching) |
| 6 | `BL 0x081DDD74` | FreeRTOS task / queue creation wrapper |
| 7 | **`BL 0x081DE120`** | **AUDIO ROUTING INIT — writes DSP registers directly with HARDCODED constants** (not NVDM) |
| 8 | Read **NVDM `0xF66C`** (3 bytes) | sub-config — second byte passed to `BL 0x081DEE88` |
| 9 | `BL 0x08138988(0, 2)` | Likely `vTaskStartScheduler()` — never returns |
| 10 | (unreachable) | `B 0x081D941A` infinite loop |

### The boot DSP-init function `FUN_0x081DE120`

Decoded December 2025. This function writes many DSP registers via
`FUN_0x081DDF54(reg_outer_id, value, reg_inner_id)`. The register-IDs
it writes include `0x38`/`0x39` (LEFT/RIGHT — the SAME IDs used by the
DSP-apply function `FUN_0x081DDF78`), with outer-IDs `0x23FF`, `0x203A`,
`0x23E1`, `0x23E0`, `0x23BA`, `0x2084`, `0x226`, `0x225`.

Crucially: the values written are HARDCODED in the function (`0`, `0x80`,
`0x81`, `1`, `0x20`), NOT loaded from NVDM `0xF665` or `0xF668`. After
boot, the DSP runs with these hardcoded gains — until a RACE balance
write triggers `FUN_0x081DE094` → `FUN_0x081DDF78`, which then pushes
`0x142039AC`'s value (initially `0x88 0x88`) to the DSP.

**This is the architectural explanation** for why:
- At boot, the runtime buffer (`0x142039AC` = `0x88 0x88`) and the actual
  DSP gain values are NOT in sync.
- Physical source switching has no effect on either the buffer or the
  DSP gain (no code path reloads either from NVDM).
- The NVDM `0xF665`/`0xF668` defaults patched by the custom firmware
  are NEVER read into runtime at boot — they only affect what's
  persisted to NVDM the next time a RACE write happens (and only
  if Loader A's NVDM-write path is triggered, which it is via
  `0x0817B250`).

### Boot-time SRAM map (full)

The reset handler at runtime `0x08133000` calls a memcpy helper at
`0x08133158` six times to populate SRAM regions from flash. The helper
signature is `memcpy(r1=src, r2=dst, r3=dst_end)`. Recovered by
`tools/parse_data_copies.py`:

| # | src flash | dst start | dst end | size | Purpose (inferred) |
|---|-----------|-----------|---------|------|--------------------|
| 1 | `0x082A7E50` | `0x04000000` | `0x04025F3C` | 155 KB | "Near" peripheral SRAM (DSP code/RAM, or L2 cache) |
| 2 | `0x082A359C` | `0x14200000` | `0x142015E8` | 5544 B | Main SRAM .data block 1 |
| 3 | `0x082A4B84` | `0x142015E8` | `0x142044F4` | 12044 B | **Main SRAM .data block 2 — contains `0x142039AC`** |
| 4 | `0x082A7A90` | `0x04243860` | `0x04243904` | 164 B | Peripheral config region (DMA descriptors?) |
| 5 | `0x082A7B34` | `0x04245740` | `0x04245740` | 0 B | Empty copy (placeholder?) |
| 6 | `0x082A7B34` | `0x0425C000` | `0x0425C31C` | 796 B | Another peripheral config region |

Total: ~173 KB of initialized data copied at boot from flash to SRAM.

The 0x14000000-region SRAM gets ~17.5 KB of .data plus presumably .bss
zero-init elsewhere. The 0x04000000-region is much larger — 155 KB —
suggesting that's where the DSP firmware/configuration lives.

### How `0x142039AC` is actually initialized at boot

**Discovered via boot-trace** (December 2025 — see
`tools/find_address_refs.py` + reset-handler disassembly):

The reset handler at runtime `0x08133000` (file offset `0x114000`) runs
a sequence of .data-section copies from flash to SRAM via a memcpy helper
at `0x08133158`. One of those copies is:

```
src     = 0x082A4B84  (flash)
dst     = 0x142015E8  → 0x142044F4  (SRAM, 12,044 bytes)
```

`0x142039AC` falls inside this destination range. The bytes copied to
`0x142039AC` from flash offset `0x082A4B84 + 0x23C4 = 0x082A6F48`
(file `0x287F48`) are:

| Byte | Value | Field |
|------|-------|-------|
| `0x142039AC` | `0x88` | L (initial = 136) |
| `0x142039AD` | `0x88` | R (initial = 136) |
| `0x142039AE` | `0x00` | slider |
| `0x142039AF` | `0x00` | dir |

**Implications**:

1. **At every cold boot, `0x142039AC` initializes to `0x88 0x88 0x00 0x00` (L=136, R=136)** — NOT from any NVDM key.
2. The NVDM-based runtime balance config (`0xF665` for USB-C, `0xF668` for dongle) is **never loaded at boot**.
3. The buffer only changes from `0x88 0x88` when something specifically triggers Loader A — and we have proven the ONLY trigger is a RACE balance write (cmd `0x0900` sub `0x29`/`0x2A`).
4. This explains why after-factory-reset reads of `0x142039AC` matching patched NVDM values must come from a **different code path** that runs during factory reset (not via the .data init). Possibly the factory-reset handler synthesizes an internal RACE balance write to itself, or there is a yet-undiscovered call site for a similar loader function — high-priority next investigation.

### `0x142039AC` reference inventory (exhaustive)

Total references to `0x142039AC` in the firmware: **6 literal-pool entries**,
all clustered in the loader-function code range (file `0x1BEFC8`-`0x1BF3E4`,
runtime `0x081DDFC8`-`0x081DE3E4`). Zero `movw`+`movt` pair refs anywhere
else. **No function outside this cluster touches `0x142039AC` directly.**

### Loader-cluster function map (4 distinct functions)

| Function | Role | Direct BL callers |
|----------|------|-------------------|
| `FUN_0x081DDF78` | **DSP-APPLY function** (December 2025 — decoded). Reads all 4 bytes of `0x142039AC` (L, R, slider, dir), then writes them to the DSP via two tail-calls to `FUN_0x081DDF54(reg_id, value, ?)`: first call uses reg_id `0x38` with value `L + slider`, second uses reg_id `0x39` with value `R + dir`. The constant `0x23BA` is passed as the first arg — likely a DSP context/device handle. **This is the bridge between the runtime balance buffer and the actual audio hardware** — until something calls this, changes to `0x142039AC` have no audible effect. Called only from inside the loader cluster (3 internal sites: `0x081DDFEA` inside slider handler, `0x081DE0CC` inside balance writer, `0x081DE318` inside dead-code loader B). | (internal only — no external callers) |
| `FUN_0x081DDFD4` (= Loader A) | **NVDM-to-runtime async loader.** Reads `NVDM 0xF665` (if state==10) or `0xF668` and copies into `0x142039AC`. The NVDM read uses `bl 0x814fed8` (`nvdm_read`). | **1** — `0x0817B250` (inside cmd 0x0900 handler) |
| `FUN_0x081DE058` | **NVDM 0xF778 reader.** Reads `NVDM key 0xF778` via `nvdm_read_lock_protect`. The purpose of `0xF778` is currently unknown — see [open question](#unknown-nvdm-keys). | **1** — `0x0817B794` (likely sub `0x31` per PROTOCOL.md) |
| `FUN_0x081DE094` | **Main balance writer.** Validates input value (`0x88` → special branch, `0x8E` → silent reject), writes byte 0 and/or byte 1 of `0x142039AC` based on a route mask, then writes new state to `NVDM 0xF665` (state==10) or `NVDM 0xF668`. **This is the actual `FUN_001BF04C` from older docs**, but exposed at its real address `0x081DE094`. | **1** — `0x0817B27A` (sub `0x29`/`0x2A`/`0x28` handler) |
| `FUN_0x081DE2E4` (= Loader B) | **DEAD CODE.** Zero callers anywhere in firmware. Reads `NVDM 0xF665` via `nvdm_read_lock_protect`. Probably an earlier version of Loader A that was superseded but never removed. | **0** |

### Unknown NVDM keys (newly discovered)

`NVDM 0xF778` is read by `FUN_0x081DE058` (caller `0x0817B794`). This key
is **not** in the previously-documented NVDM inventory. Hypotheses:

- A separate audio-config key paired with `0xF665`/`0xF668` — possibly stores
  "live channel routing mask" or "slider/dir" data corresponding to the
  byte 2/3 fields of `0x142039AC`.
- The cmd `0x0901` sub `0x31` was tagged in PROTOCOL.md as "likely a READ
  counterpart or alternate channel selector" — `0xF778` may be exactly that.

Next-investigation: dump `NVDM 0xF778` over RACE and compare to other
known keys; also look for `0xF778` movw refs in the firmware to find
who writes the default.

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
| `0xF665` | 4 | `0x958D` (L=141 R=149) | `0x186CAC` | **USB-C audio source balance** (state=10) — 4 movw refs across firmware. Loaded by Loader A only. |
| `0xF668` | 4 | `0x9393` (L=147 R=147) | `0x186C7A` | **BT/dongle audio source balance** (state≠10) — 4 movw refs. **Never read at runtime** (Loader B is dead code; Loader A's branch for state≠10 IS reachable but appears never triggered in practice — physical source switch does not invoke loader). Effectively write-only from factory init. |
| `0xF666` | ? | ? | (read only) | Unknown — 2 movw refs, likely paired with 0xF665 (USB-C-related sub-config) |
| `0xF667` | ? | ? | (read only) | Unknown — 2 movw refs |
| `0xF669` | ? | ? | (read only) | Unknown — 1 movw ref |
| `0xF66A` | ? | ? | (read only) | Unknown — 1 movw ref |
| `0xF66B` | ? | ? | (read only) | Unknown — 2 movw refs |
| `0xF66C` | ? | ? | (read only) | Unknown — 2 movw refs |
| `0xF66D` | ? | ? | (read only) | Unknown — 2 movw refs |
| `0xF66E` | 2 | `0x0901` | `0x186CDE` | **Unknown audio flag** — written by same factory init function. Two-byte value `09 01`. Possibly source-state mask, codec selector, or audio-routing flag. 3 movw refs total. |
| `0xF670` | ? | ? | (read only) | Unknown — 2 movw refs |
| `0xF778` | 1 | ? | (read only) | **Newly discovered (Dec 2025).** Read by `FUN_0x081DE058` (caller `0x0817B794`, sub `0x31`). **Also WRITTEN by `FUN_0x081DE094` at the START of every balance write** (`movw r0, #0xf778; bl 0x814fed8` — runtime `0x081DE0AC`). So 0xF778 holds the last-written balance byte. Acts as a "last operation log" for balance writes. |
| `0xF66C` | 3 | ? | (boot read) | **Discovered via boot init trace.** Read by `FUN_0x081DEFE0` (called from main `FUN_0x081D9354` at boot). Second byte passed to `FUN_0x081DEE88` (purpose TBD). Likely an audio sub-config. |
| `0xF666` | 1 | `0x0X` (low 4 bits) | (boot read) | **Discovered via boot init trace.** Read by main `FUN_0x081D9354` at boot. Low 4 bits passed to `FUN_0x081AFE04` which stores to a 1-byte SRAM flag. Probably a routing/mode flag. |
| `0xF667` | 1 | ? | (boot read) | **Discovered via boot init trace.** Read by main `FUN_0x081D9354` at boot. Passed to `FUN_0x081C9B84` (purpose TBD). |
| `0xF700` | 1 | `0xFF` (default if missing) | (read only) | **Newly discovered.** Read 3× (`0x0817B30A`, `0x0817B34A`, `0x081C460E`). The `FUN_0x0817B334` sister to source-state-getter. Purpose unknown but appears to be a state byte parallel to `0xF702`. |
| `0xF702` | 1 | `0xFF` | (RACE-set) | **Source state** (10 = USB-C, 0x88 etc per ERNW notes). Read only by `FUN_0x0817B2F4`. Written by RACE cmd 0x0900 sub 0x2F via `0x0817B2B2`. **No internal code consults this for audio routing** (i.e. the firmware does not auto-load NVDM balance based on physical source — it's user-settable but unused inside audio path). |
| `0xF703` | 10 | ? | (read only) | Read at `0x081C970C`. Likely related to source state cluster (0xF702-0xF706). |
| `0xF704` | ? | ? | (read only) | Read at `0x081C9774`. |
| `0xF705` | ? | ? | (read only) | Read at `0x081C983E`. |
| `0xF706` | 1 | ? | (read only) | Read at `0x081C97C8`. |
| `0xF600` | 1 | ? | (read only) | Read at `0x081C9CA0`. |

**Total NVDM inventory** (from `tools/find_all_nvdm_reads.py` + `find_all_nvdm_defaults.py`): 164 read sites across **~70 unique keys**. Most are infrequently-accessed audio/BT config. Highlights for balance/audio specifically:

- `0xF665` (USB-C balance): read at exactly 1 BL — `0x081DE2FC` inside dead-code Loader B.
- `0xF668` (BT/dongle balance): read shares the same BL (r0 reassigned). Not separately read.
- `0xF666`, `0xF667`, `0xF66C`: each read once at boot init chain — these ARE live config keys.
- `0xF66B`, `0xF66D`, `0xF669`, `0xF66A`, `0xF670`: each read once elsewhere (specific subsystems).


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
