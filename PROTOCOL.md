# RACE Protocol Reference

The Maxwell uses Airoha's **RACE protocol** (Remote Audio Control Engine) over
USB HID for all configuration. RACE is documented somewhat in [ERNW's white
paper on Airoha vulnerabilities](https://static.ernw.de/whitepaper/ERNW_White_Paper_74_1.0.pdf).

## HID interface

| Field | Value |
|-------|-------|
| **VID** | `0x3329` (Audeze) |
| **PID** (Xbox dongle) | `0x4B18` |
| **PID** (PS dongle) | `0x4B19` |
| **PID** (PS USB-C cable) | `0x4B1A` |
| **PID** (Xbox USB-C cable) | `0x4B1E` |
| **HID usage page** | `0xFF13` (vendor-defined) |
| **Interface** | `mi_00 col02` (the second HID collection) |
| **Output report ID** | `0x06` (writes from host, 62-byte fixed length) |
| **Input report ID** | `0x07` (reads back from device, 62-byte fixed length) |

The HID Report descriptor caps Output reports at **62 bytes total** (1 byte
report ID + 61 bytes data). Any RACE packet bigger than 59 bytes (62 - 1
report ID - 2 length bytes) must be **fragmented across multiple HID
reports** — first report carries the total length, continuation reports
carry chunks. See [FLASHING.md](FLASHING.md) for the WriteFlashPage case
where this matters most (each page write is ~263+ bytes per chunk).

Reads use **polled `HidD_GetInputReport`**, NOT interrupt `ReadFile`. The
device fills its input report when it has data; the host polls.

## Packet structure

All write commands are HID output reports of fixed 62-byte length, padded with
zeros:

```
06 [LEN] 80 05 5A [PLEN] 00 [PAYLOAD...]
└─┘ └──┘ └─┘ └─┘ └─┘ └────┘ └─┘ └────────┘
HID  HID  ?    ?   RACE  payload  ?   command + args
rpt  len  ?    ?  magic  length

[LEN]  = total length of remaining bytes (HID-level length)
[PLEN] = length of the RACE payload section
[PAYLOAD] = 16-bit command + sub-command + parameters
```

Example — write master gain to value 0x8D:

```
06 09 80 05 5A 05 00 00 09 28 00 8D
└─┘                       └─┘ └─┘    └─┘
HID                       cmd sub    val
rpt                      0x0900 0x28 0x8D
```

## Command dispatch table

Located at file offset `0x26B850` in v1.0.1.63 and **`0x26B8E0`** in v1.0.1.74
(decompressed firmware). The table consists of 30 entries of 8 bytes each:

```
[cmd_start:u16] [cmd_end:u16] [handler_fn_ptr:u32]
```

| Cmd Range | Purpose | Handler |
|-----------|---------|---------|
| `0x0200-0x0201` | Shared handler (4 entries point here) | `FUN_0017CD81` |
| `0x0220-0x0221` | (same handler) | ditto |
| `0x020E-0x020F` | (same handler) | ditto |
| `0x0240-0x025F` | (same handler) | ditto |
| `0x020C-0x020D` |  | `FUN_0017957D` |
| `0x0210-0x0211` |  | `FUN_0017A2F5` |
| `0x0300-0x0301` |  | `FUN_0017B9A9` |
| `0x0305-0x0307` |  | `FUN_0017CF95` |
| `0x0400-0x0433` |  | `FUN_00176615` |
| **`0x0900-0x09FF`** | **Register R/W, NVDM access** | `FUN_0017B91D` (with `FUN_0015C91C` as sub-dispatcher) |
| `0x0A00-0x0AFF` |  | `FUN_00176A8D` |
| `0x0CC0-0x0CDF` |  | `FUN_0017998D` |
| `0x0D00-0x0D01` |  | `FUN_0017E1CD` |
| `0x0E01-0x0EFF` |  | `FUN_001794FD` |
| `0x0F14-0x0F20` |  | `FUN_0017C0CD` |
| `0x1000-0x100F` |  | `FUN_0017D6A1` |
| `0x1100-0x1101`, `0x1110-0x111F` | Battery / sync | `FUN_0017BEA9`, `FUN_0017BF85` |
| `0x1680-0x16FF` |  | `FUN_0017A295` |
| `0x1700-0x1700` |  | `FUN_0017A045` |
| **`0x1C00-0x1C1F`** | **FOTA (firmware update)** | `FUN_001774A7` |
| `0x1D00-0x1D0F` |  | `FUN_0017BC2D` |
| `0x1E00-0x1E02` |  | `FUN_0017BBBD` |
| `0x1E03-0x1E07` |  | `FUN_0017C5F1` |
| `0x1E08-0x1E0F` |  | `FUN_0017CAB1` |
| `0x1F00-0x1F3F` |  | `FUN_0017BD45` |
| `0x2200-0x22FF` |  | `FUN_0017E0BD` |
| **`0x2C00-0x2C01`** | **Chatmix / sidetone** | `FUN_0017BA8D` |
| `0x2E20-0x2E21` |  | `FUN_0017E169` |
| `0x5008-0x500F` |  | `FUN_0017D36D` |

The two ranges in **bold** are the most-commonly-used: `0x0900` for gain/balance/NVDM
and `0x2C00` for chatmix/sidetone.

## Cmd 0x0900 range internals

The dispatch table entry `0x0900-0x09FF` (handler at runtime `0x0817B92C`)
internally dispatches on the **full 16-bit cmd_id**:

| Cmd | Handler addr | What it does |
|-----|-------------|--------------|
| `0x0900` | `0x0817B0E4` | Has its own sub-command dispatcher (TBB at `0x0817B122`). 12 sub-cmd slots (`0x00`–`0x0B`), but only **4 are functionally distinct**: `0x00` and `0x0B` share `0x0817B15C`; `0x04` → `0x0817B19A`; `0x0A` → `0x0817B1AE`. Others return without action. Response is 3 bytes. All undocumented. |
| `0x0901` | `0x0817B410` | **Big sub-command dispatcher — see below** (50 sub-commands `0x00`–`0x31`) |
| `0x0910` | `0x0817B374` | Status query — returns 10-byte struct. Each byte is read from a different getter (`FUN_81AFDDC`, `FUN_8136C70`, `FUN_8144D38`, `FUN_81C9C3C`, `FUN_81C9528`, `FUN_81C956C`, `FUN_81CCB4C`). Likely telemetry (state flags, possibly temperature, battery level, source state). |
| `0x09FD` | `0x0817B8A0` | (undocumented) |

## Sub-commands of 0x0901 (audio register R/W)

The `0x0901` handler at `0x0817B410` switches on a sub-command byte
(read from `[r0+6]`) via TBH jump table at `0x0817B436`. **50 sub-commands
are recognized (`0x00`–`0x31`)**, of which only ~10 were previously
documented. Sub-commands that route to `0x0817B89A` are unhandled
(return 0).

| Sub | Target addr | Known purpose / notes |
|-----|-------------|----------------------|
| `0x00` | `0x0817B49A` | (undocumented, 4-byte response) |
| `0x01` | `0x0817B89A` | unhandled |
| `0x02` | `0x0817B506` | (undocumented, calls `0x0817B008`) |
| `0x03` | `0x0817B570` | (undocumented, 4-byte response) |
| `0x04` | `0x0817B89A` | unhandled |
| `0x05` | `0x0817B4E2` | (undocumented, **6-byte** response) |
| `0x06` | `0x0817B5C4` | (undocumented, 4-byte response) |
| `0x07` | `0x0817B5A0` | (undocumented, **5-byte** response) |
| `0x08`-`0x09` | `0x0817B89A` | unhandled |
| `0x0A` | `0x0817B624` | (undocumented, 4-byte response) |
| `0x0B` | `0x0817B4C2` | (undocumented, 4-byte response) |
| `0x0C`-`0x1F` | `0x0817B89A` | unhandled (most of low range) |
| `0x20` | `0x0817B66A` | Write 1-byte value to `NVDM 0xF666` (source preference flag) |
| `0x21` | `0x0817B89A` | unhandled |
| `0x22` | `0x0817B68C` | (undocumented, 4-byte response) |
| `0x23` | `0x0817B6E4` | (undocumented, 4-byte response) |
| `0x24` | `0x0817B714` | Audio reset (calls `FUN_001AAB74`+`FUN_001AABF8`) |
| `0x25` | `0x0817B734` | **Balance slider** (-6 to +6 from 0x80) — `FUN_001BEFB4` |
| `0x26` | `0x0817B7FA` | (undocumented, 4-byte response) |
| `0x27` | `0x0817B81A` | Volume (returns 1) — `FUN_001AA5C0` |
| **`0x28`** | `0x0817B754` | **MASTER gain → SRAM byte 0+1** — `FUN_001BF04C(val, 3)` |
| **`0x29`** | `0x0817B754` | **LEFT gain → SRAM byte 0** — `FUN_001BF04C(val, 1)` |
| **`0x2A`** | `0x0817B754` | **RIGHT gain → SRAM byte 1** — `FUN_001BF04C(val, 2)` |
| `0x2B` | `0x0817B79A` | (undocumented, 4-byte response) |
| `0x2C` | `0x0817B7BA` | Audio config write — `FUN_001ADB48` |
| `0x2D` | `0x0817B7DA` | (undocumented, 4-byte response) |
| `0x2E` | `0x0817B83A` | Audio config write — `FUN_001BF768` |
| **`0x2F`** | `0x0817B85A` | **Set audio source state in `NVDM 0xF702`** (state=10 = USB-C) |
| `0x30` | `0x0817B87A` | (undocumented, 4-byte response) |
| **`0x31`** | `0x0817B754` | **(undocumented) — uses gain handler but takes a different code path (calls `0x081DE058` instead of `0x081DE080`). Likely a READ counterpart or alternate channel selector.** |

**Important correction about read/write direction** (Dec 2025 empirical
testing): cmd `0x0900` is the **WRITE** namespace; cmd `0x0901` is the
**READ** namespace. They share the same sub-cmd numbering but do opposite
things:

| RACE | Effect |
|------|--------|
| `cmd 0x0900 sub 0x29` + value byte | **WRITE** new LEFT gain. Updates both `0x142039AC[0]` AND `NVDM 0xF665` (the USB-C key, regardless of physical source). Empirically confirmed persistent across full power-cycle. |
| `cmd 0x0900 sub 0x2A` + value byte | WRITE new RIGHT gain. Same persistence. |
| `cmd 0x0901 sub 0x29` (no value) | **READ** current LEFT gain. Returns `0x142039AC[0]` via `FUN_81DE080`. |
| `cmd 0x0901 sub 0x2A` (no value) | READ current RIGHT gain. Returns `0x142039AC[1]`. |

The write path also writes to `NVDM 0xF665` regardless of the current
source state — meaning even if you write balance while on dongle, the
USB-C NVDM key is what gets updated. The dongle key (`NVDM 0xF668`) is
effectively write-only from factory init code; the runtime never writes
it. See [AUDIO.md](AUDIO.md) §Runtime balance behavior for the full
empirical observation log.

**Validation rejection — DECODED** (December 2025, via static analysis
of `FUN_0x081DE094` — the actual balance-write function):

The write function at `0x081DE094` (called from `0x0817B27A` inside
the cmd 0x0900 sub-dispatcher) starts by comparing the incoming value
against two specific sentinels BEFORE writing to `0x142039AC`:

```
0x081de0b4  cmp r3, #0x88
0x081de0b6  beq #0x81de0f4   ; if val == 0x88, take SPECIAL branch
0x081de0b8  cmp r3, #0x8e
0x081de0ba  beq #0x81de0ec   ; if val == 0x8e, take REJECT branch
0x081de0bc  ... normal write path (writes byte to 0x142039AC[0] and/or [1]) ...
```

| Value | Behavior |
|-------|----------|
| `0x88` (136) | **Special-cased** — does NOT take the normal write path. Likely "reset to default" code path. This is also the **boot-init default** value the .data section loads into `0x142039AC` (see [FIRMWARE.md](FIRMWARE.md) §How 0x142039AC is initialized). |
| `0x8E` (142) | **Silently rejected** — branches away from the writer entirely. No NVDM update either. |
| any other value | Normal write: stores to `0x142039AC[0]` (if LEFT bit set in route mask) and/or `0x142039AC[1]` (if RIGHT bit set), then calls internal helper `FUN_0x081DDF78`, then writes the new 4-byte struct to either `NVDM 0xF665` (state==10) or `NVDM 0xF668`. |

**Why these sentinels?** Speculation: `0x88` is reserved as the "default
identity" value (since boot loads it directly), and `0x8E` may be
reserved for an internal signaling purpose — possibly tied to the
audio gain look-up table where some specific entries are mapped to
non-balance behaviors. This remains the most likely high-value next
investigation.

The gain sub-commands (`0x28`/`0x29`/`0x2A`) all share the same handler at
`0x0817B754`, which routes by sub-cmd value to `FUN_001BF04C(val, channel)`
with `channel` = 3 (master), 1 (left), or 2 (right). Sub `0x31` enters the
same handler but jumps to `FUN_001DE058` instead — likely a getter or an
alternate channel write.

**40+ sub-commands are undocumented but reachable.** Each handler follows
the same skeleton (allocate response buffer, call a specific function,
return). Tracing each would expand RACE coverage significantly. Format:
all responses are 4 bytes except sub `0x05` (6 bytes) and `0x07` (5 bytes).

| Sub | Action | Notes |
|-----|--------|-------|
| `0x20` | Write 1-byte value to `NVDM 0xF666` | Source preference flag |
| `0x24` | Audio reset | Calls `FUN_001AAB74` + `FUN_001AABF8` |
| `0x25` | **Balance slider (-6 to +6 from 0x80)** | `FUN_001BEFB4` |
| `0x27` | Volume (returns 1) | `FUN_001AA5C0` |
| `0x28` | **MASTER gain → SRAM byte 0+1** | `FUN_001BF04C(val, 3)` |
| `0x29` | **LEFT gain → SRAM byte 0**     | `FUN_001BF04C(val, 1)` |
| `0x2A` | **RIGHT gain → SRAM byte 1**    | `FUN_001BF04C(val, 2)` |
| `0x2C` | Audio config write | `FUN_001ADB48` |
| `0x2E` | Audio config write | `FUN_001BF768` |
| `0x2F` | **Set audio source state in `NVDM 0xF702`** | Direct write (state=10 means USB-C) |

The gain sub-commands (`0x28`/`0x29`/`0x2A`) go through `FUN_001BF04C` which
internally picks the right NVDM key (`0xF665` or `0xF668`) based on the current
source state — see [AUDIO.md](AUDIO.md).

## Common RACE commands (HID byte sequences)

### Write right channel gain to 0x8F (143)

```
06 09 80 05 5A 05 00 00 09 2A 00 8F
```

### Write left channel gain to 0x8D (141)

```
06 09 80 05 5A 05 00 00 09 29 00 8D
```

### Write master gain to 0x8D (141)

```
06 09 80 05 5A 05 00 00 09 28 00 8D
```

### Set source state to USB-C (10)

```
06 09 80 05 5A 05 00 00 09 2F 00 0A
```

### Read right channel gain

```
06 08 80 05 5A 04 00 01 09 2A
```

Then poll input report 0x07; the response value is at byte index 12.

### Set chatmix (0=full game, 10=balanced, 20=full chat)

```
06 09 80 05 5A 05 00 82 2C 0B 00 [VAL]
```

### Read battery level

```
06 07 80 05 5A 03 00 D6 0C
```

Response value at byte index 12.

## NVDM key reference

NVDM (Non-Volatile Data Manager) keys are 16-bit integers used by the Airoha
SDK for persistent settings storage.

### Audio configuration keys

| Key | Purpose | Written by | Read by |
|-----|---------|-----------|---------|
| `0xF665` | USB-C balance (state==10): 4 bytes (L, R, slider_L, slider_R) | factory init, sub-cmd 0x25/0x28/0x29/0x2A | source switch handler |
| `0xF666` | Source preference flag | sub-cmd 0x20 | boot init |
| `0xF667` | Audio path config | sub-cmd 0x24 reset | boot init |
| `0xF668` | BT/dongle balance (state≠10): 4 bytes | factory init, sub-cmd 0x25/0x28/0x29/0x2A | source switch handler |
| `0xF66B` | Audio config (unknown) | `FUN_001ADB48` |  |
| `0xF66C` | Codec/audio state | `FUN_001B00C8` |  |
| `0xF66D` | Audio config (unknown) | `FUN_001ADB48` |  |
| `0xF66E` | Sidetone-related | factory init | `FUN_001ADD04` |
| `0xF670` | Unknown audio setting | `FUN_001BF530` |  |
| `0xF702` | **Audio source state** (10=USB-C, else BT) | sub-cmd 0x2F | `FUN_0015C2F4` |
| `0xF703` | Last-processed EQ source state | EQ switcher | EQ switcher |
| `0xF704` | BT EQ preset | (external command) | EQ switcher |
| `0xF705` | USB-C EQ preset | (external command) | EQ switcher |
| `0xF706` | BT secondary EQ | (external command) | EQ switcher |
| `0xF707` | USB-C secondary EQ | (external command) | EQ switcher |
| `0xE400` | DSP coefficient defaults (20 bytes) | factory init |  |
| `0xE401` | EQ preset index table | (external command) | EQ data reader |
| `0xE42A` | **Active EQ filter slot** | EQ switcher | DSP |
| `0xE05B`, `0xE066`, `0xE068` | Audio config (loaded for mode 1/4) |  |  |
| `0xE033`, `0xE043`, `0xE031` | Audio config (loaded for mode 6/7) |  |  |
| `0xF082` | **Factory init flag** (`'U'` = trigger factory reset NVDM init) |  | `FUN_001BB010` |

## AT+EAUDIO command vocabulary

The firmware contains 80+ AT-command-style strings used by an internal debug
interface (the actual RACE commands above are what the host sends; these AT
strings are for in-firmware debugging). Selected interesting ones:

- `AT+EAUDIO=AUD_SET_DEVICE_LEFT` / `AUD_SET_DEVICE_RIGHT`
- `AT+EAUDIO=AUD_HWGAIN_SET_FADE_TIME_AND_GAIN`
- `AT+EAUDIO=VOL_STREAM_2A2D` — explicit reference to registers 0x2A-0x2D
- `AT+EAUDIO=VOL_STREAM_6A6D` — secondary stream registers
- `AT+EAUDIO=DL_NM` / `DL_HP` / `UL_NM` / `UL_HP` — downlink/uplink modes
- `AT+EAUDIO=REG_SET,` / `REG_GET,` / `REG_DEBUG_DUMP`
- `AT+EAUDIO=PEQ_MODE,` / `PEQ_SYNC,` / `CONFIG_DIS_PEQ` / `CONFIG_ENA_PEQ`
- `AT+EAUDIO=VOLUME_PARAM_NVDM`
- `AT+EAUDIO=SW_GAIN_Enable` / `SW_GAIN_Disable`

## Python example: read battery level

```python
import ctypes, pywinusb.hid as pwhid, time

kernel32 = ctypes.windll.kernel32
hid_dll = ctypes.windll.hid

# Find the col02 HID interface
path = None
for d in pwhid.HidDeviceFilter(vendor_id=0x3329).get_devices():
    if "col02" in d.device_path:
        path = d.device_path
        break

h = kernel32.CreateFileW(path, 0xC0000000, 0x03, None, 3, 0, None)

# Send battery query
cmd = bytes([0x06, 0x07, 0x80, 0x05, 0x5A, 0x03, 0x00, 0xD6, 0x0C])
buf = bytearray(62); buf[:len(cmd)] = cmd
hid_dll.HidD_SetOutputReport(h, bytes(buf), 62)
time.sleep(0.2)

# Read response
rbuf = (ctypes.c_ubyte * 62)()
rbuf[0] = 0x07
hid_dll.HidD_GetInputReport(h, rbuf, 62)
r = bytes(rbuf)
for i in range(len(r) - 4):
    if r[i] == 0xD6 and r[i+1] == 0x0C:
        print(f"Battery: {r[i+4]}%")
        break

kernel32.CloseHandle(h)
```

## Related work / further reading

- [ERNW White Paper 74](https://static.ernw.de/whitepaper/ERNW_White_Paper_74_1.0.pdf) — Airoha RACE vulnerabilities (CVE-2025-20700/20701/20702)
- [auracast-research/race-toolkit](https://github.com/auracast-research/race-toolkit) — Python CLI for RACE over BLE/USB
- [ramikg/airoha-firmware-parser](https://github.com/ramikg/airoha-firmware-parser) — firmware decompressor
- [HelgeSverre/sony-vp-extract](https://github.com/HelgeSverre/sony-vp-extract) — Airoha key extractor (for AES-encrypted firmware variants)
