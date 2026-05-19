# Audio Pipeline & L/R Balance

This is the most important document for anyone trying to understand or fix the
L/R balance issue. The audio mixer architecture and per-source gain system are
fully reverse-engineered here.

## TL;DR

The Maxwell maintains a small **audio context struct in SRAM** that stores
master/left/right gains and per-stream selectors. Audio output is computed by a
mixer that reads gain bytes from this struct, scales them per active stream,
and writes to the audio FIFO.

The struct values are loaded at boot from **two different NVDM keys** based on
which audio source is active:

- **USB-C wired** → loads from `NVDM 0xF665`
- **Bluetooth/dongle** → loads from `NVDM 0xF668`

The two keys can hold **different gain values** — this is intentional. Audeze
calibrated USB-C with a built-in `+8 R` boost (to compensate for what they
measured as the average Maxwell's USB-C audio path imbalance) and BT/dongle
with no boost. **For unit-specific hardware variance, these values can be
modified at runtime via RACE commands** (see [PROTOCOL.md](PROTOCOL.md)) and
the changes persist in NVDM across reboots.

## The SRAM audio context struct

Located at `SRAM 0x14229E58` (pointed to by `DAT_001350A0` in firmware).

| Offset | Field | Notes |
|--------|-------|-------|
| `+0x21` | u8 channel_count | Loop count for the mixer |
| `+0x22` | u8 counter | Internal counter |
| `+0x26` | u16 gain_header | Loaded from NVDM, byte 0 of key |
| `+0x28` | u8 **MASTER gain** | Default 141 |
| `+0x29` | u8 **LEFT gain**   | Default 141 |
| `+0x2A` | u8 **RIGHT gain**  | Default 149 (USB-C) / 141 (BT v63) / 147 (BT v74) |
| `+0x2B` | u8 unknown | Default 2 |
| `+0x2C–0x30` | u16×3 sidetone config | Sidetone level + 2 others |
| `+0x3B` | u8 selector A | Channel that gets stream type 1 (or 0xFF if disabled) |
| `+0x6C` | u8 selector C | Channel that gets stream type 3 (also stream state) |
| `+0x6D–0x6F` | u8×3 stream state | `+0x6F = 'F'` (0x46) when active |
| `+0xCB` | u8 selector B | Channel that gets stream type 2 |

> The "registers" we write via RACE commands (`0x28`, `0x29`, `0x2A`) are
> **offsets into this SRAM struct**, NOT hardware register addresses.

## The mixer

`FUN_00135268` (and read-twin `FUN_00134BC0`) implements this loop:

```c
for (channel = 0; channel < ctx->channel_count; channel++) {
    for (substream = 0; substream < 3; substream++) {
        gain = 0;
        if (ctx->selector_A == channel)  // 0x3B
            gain = FUN_00138764(0, substream + 1);   // stream type A handler
        if (ctx->selector_B == channel)  // 0xCB
            gain = FUN_00138764(1, substream + 1);   // stream type B handler
        if (ctx->selector_C == channel)  // 0x6C
            gain = FUN_00138D9C(0, substream + 1);   // stream type C handler

        write_to_mixer_buffer(channel, substream, gain);
    }
}
```

Three "channel selectors" (`+0x3B`, `+0x6C`, `+0xCB`) each store a small
integer that picks which output channel that stream type maps to. The mixer
loops over all output channels (typically 2: L and R) and for each substream
(3 deep), checks which stream type — if any — is currently routed there.

When no audio source is active, all three selectors are `0xFF` (disabled) so
the mixer produces silence.

## Per-stream gain calculation

`FUN_00138764(mode, sub)` and `FUN_00138D9C(mode, sub)`:

```c
gain = byte[5] * byte[4] * (FUN_0013868C() / 1000);
```

Where `byte[4]` and `byte[5]` are pulled from per-stream config structs in
SRAM (one per substream of each stream type). Audeze sets these to default
unity-ish multipliers; the values for each stream are:

| Stream type | Substream | Config ptr (SRAM) |
|-------------|-----------|--------------------|
| A (selector 0x3B) | 1 | `0x14201B5F` |
| A | 2 | `0x14201B16` |
| A | 3 | `0x14201B36` |
| B (selector 0xCB) | 1 | `0x14201C9B` |
| B | 2 | `0x14201C72` |
| C (selector 0x6C) | 1 | `0x14201A57` |
| C | 2 | `0x14201A2E` |

So 7 per-substream gain configs, each contributing a multiplier to the final
mixer output.

## The L/R balance system

This is the heart of the L/R issue. **Two separate NVDM keys** store the gain
values, and which one is used depends on a stored "source state".

### How the source state determines the key

`FUN_0015C2F4` reads `NVDM 0xF702` (one byte). If the value is `10` (`'\n'`),
the audio is treated as USB-C; otherwise it's treated as BT/dongle.

`FUN_001BF04C` is called whenever you write the master/L/R gain via RACE:

```c
state = FUN_0015C2F4();          // get state from NVDM 0xF702
if (state == 10) {
    nvdm_key = 0xF665;           // USB-C
} else {
    nvdm_key = 0xF668;           // BT/dongle
}
write_nvdm(nvdm_key, balance_struct, 4);
```

So when you connect via USB-C and tweak the balance, it stores to `0xF665`.
When connected via dongle, it stores to `0xF668`. The two paths have
independent gain calibration.

### How the source state gets set

`NVDM 0xF702` is **not** auto-detected from hardware. It's set by an explicit
RACE command (sub-command `0x2F` of command `0x0900`). The Audeze app sets this
when connection state changes. If something goes wrong and `0xF702` is stale
or wrong, you can end up loading the wrong balance for your current source.

### Audeze's factory defaults

| NVDM Key | Used when | v1.0.1.63 default | v1.0.1.74 default |
|----------|-----------|-------------------|-------------------|
| `0xF665` | state == 10 (USB-C) | `L=141, R=149` (+8 R) | `L=141, R=149` (unchanged) |
| `0xF668` | state ≠ 10 (BT/dongle) | `L=141, R=141` (no comp) | `L=147, R=147` (+6 both) |

These defaults are written by `FUN_00186C60` during factory reset (which is
triggered by `NVDM 0xF082 == 'U'`).

The `+8 R` for USB-C is **deliberate compensation** — Audeze decided that the
average Maxwell's USB-C audio path attenuates R slightly. For individual units
that deviate from this average, the result is perceptible L/R imbalance.

### Why USB-C and BT can need very different compensation

Our test unit needed:
- USB-C: `R = 143` (+2 over L = 141) — *less* than Audeze's +8 default
- Dongle: `R = 154` (+13 over L) — *much more* than Audeze's 0 default

This isn't a state desync — it's because the **DSP paths physically differ**:
USB-C audio goes through a different DAC pipeline (and possibly sample rate
conversion) than wireless audio decoded from BT codecs (SBC/AAC/LC3). Each
path has its own analog behavior, and your specific drivers respond
differently to each.

## EQ preset system (also per-source)

The Maxwell has **separate EQ filter coefficients per audio source**. When the
source state changes, `FUN_001AA6E0` swaps the active EQ filter:

```c
last_state = read_nvdm(0xF703);    // previously processed state
curr_state = read_nvdm(0xF702);    // current source state
if (last_state == curr_state) return;  // no change

if (curr_state == 0) {           // moved to BT
    eq_data = read_nvdm(0xF704); // BT EQ preset
    write_nvdm(0xE42A, eq_data); // active EQ slot
    eq_data2 = read_nvdm(0xF706);
    write_nvdm(0xEE23, eq_data2);
}
else if (curr_state == 10) {     // moved to USB-C
    eq_data = read_nvdm(0xF705); // USB-C EQ preset
    write_nvdm(0xE42A, eq_data);
    eq_data2 = read_nvdm(0xF707);
    write_nvdm(0xEE23, eq_data2);
}
write_nvdm(0xF703, curr_state);  // record processed state
```

So in addition to per-source *gain*, the firmware applies per-source *EQ
filters*. This is part of why USB-C and BT sound different beyond just the
balance.

## Gain value mapping tables

At file offset `0x26DD09` in decompressed firmware:

```
"gain_value_mapping\0audio_nvdm\0" + header + table data
```

Two lookup tables of int16 values follow:

**Table A** (16 entries, step 2, fine-grained):
```
-23, -21, -19, -17, -15, -13, -11, -9, -7, -5, -3, -1, 1, 3, 5, 7
```

**Table B** (16 entries, step 4, coarse):
```
-72, -68, -64, -60, -56, -52, -48, -44, -40, -36, -32, -28, -24, -20, -16, -12
```

Likely interpreted as dB values (or quarter-dB). Probably split as
high-nibble = Table B index, low-nibble = Table A index, but the exact lookup
function wasn't traced.

## Multi-USB-device exposure

The Maxwell appears as **multiple USB devices** at the same time:

| USB device name | Purpose |
|-----------------|---------|
| `Audeze Maxwell XBOX Headset` | Main HID interface (RACE protocol over `vid_3329 pid_4b1e`) |
| `Audeze Maxwell Chat` | USB audio input (chat audio stream — Discord etc.) |
| `Audeze Maxwell Game` | USB audio input (game audio stream) |
| `Mic TX` | Microphone transmit |
| `Microphone` | Microphone input |

The **chatmix** feature is implemented by independently controlling the volume
of the "Chat" and "Game" USB audio streams in the firmware mixer. Both flow
into the same physical drivers.

## Why "concurrent playback" (BT + USB-C at the same time) doesn't work

When the firmware detects an audio mode change, `FUN_00135BA8` calls
`FUN_00137F48` which **resets ALL three channel selectors to `0xFF`** (kills
all active streams) before setting up the new mode. This is a software-only
restriction — the hardware mixer can route up to 3 different streams
simultaneously to different channels.

**Status (Dec 2025)**: There are actually TWO patches needed for full
concurrent playback across all source combos. Both live in the same
*audio-source dispatch* code region (file offsets `0x135Cxx`):

### Patch 1: `0x135C66` — fixes USB-C↔BT

`BL FUN_00137F48` (router_reset) — the simpler reset, just sets all
three channel selectors to `0xFF` (disabled). Function body decoded:

```
push {r3, lr}
bl  printf("source reset")
bl  FUN_8155D28               ; sub-helper (more state cleanup)
ldr r3, [audio_ctx_base]
movs r2, #0xff
strb r2, [r3, #0x3b]          ; selector A = 0xFF (disabled)
strb r2, [r3, #0xcb]          ; selector B = 0xFF
strb r2, [r3, #0x6c]          ; selector C = 0xFF
strb r2, [r3, #0xfc]          ; selector D = 0xFF  (a 4th selector we hadn't seen)
str  r1, [r3, #0x40]          ; zero ctx[+0x40]
str  r1, [r3, #0xd0]          ; zero ctx[+0xd0]
str  r1, [r3, #0x70]          ; zero ctx[+0x70]
str  r1, [r3, #0x100]         ; zero ctx[+0x100]
ldr r3, [pc, #0x18]
movw r2, #0x101
strh r2, [r3]                  ; flag = 0x0101
strh r2, [r3, #0x44]
pop  {r3, pc}
```

This patch alone enables some source-pair combos but **not BT↔dongle**.

### Patch 2: `0x135CC4` — fixes BT↔dongle

`BL FUN_00137F9C` (`bigger_router_reset`) — a separate, more
aggressive reset. Lives in a *state-dispatch function* at `0x08154C9C`
that switches on a "transition state byte":

```
ldrb r3, [state_ptr]
cmp  r3, #4
beq  state4_handler   ; just calls printf
bhi  check_F1         ; if r3 > 4: check for 0xF1
cmp  r3, #1
beq  state1_handler   ; calls bigger_router_reset
cmp  r3, #2
beq  state2_handler   ; calls FUN_00139_0BC (different reset)
bl   default_handler  ; FUN_00133_A3C (small no-op)

check_F1:
cmp  r3, #0xf1
bne  default_handler
state1_handler:
bl   FUN_00137F9C     ; bigger_router_reset
```

State 1 and state 0xF1 both call `bigger_router_reset`. The function does:
- Iterates 4 stream pointers in the state struct (at `+0x40`, `+0xD0`,
  `+0x70`, `+0x100`)
- For each non-null pointer, calls `FUN_0013E3EC` (a stream-stop helper)
- Then calls back into `FUN_00137F48` (the original router_reset)

So patching `0x135CC4` (NOPping the BL to `bigger_router_reset`) skips this
whole "stop all wireless streams" cascade. This is suspected to be the
BT↔dongle case (state 1 or 0xF1 = "switching between wireless sources").

### Patch 3 (optional/aggressive): `0x135CCA` — state 2 reset

Same dispatch function, BL to `FUN_00139_0BC` (state 2 = "switching to
dongle"?). That function kills streams at struct offsets `+0x34` and
`+0x98` (a DIFFERENT set than bigger_router_reset). Patching it might
enable even more concurrent combos but is riskier — state 2 might be a
required normal transition path.

Bytes at `0x135CCA`: `04 F0 F7 F9` (BL `0x081590BC`) → patch to
`00 BF 00 BF` (NOPs).

### What we haven't done

The `FUN_0815_90BC` (state 2) call site isn't included in the default
patcher build. Empirical testing with patch 1 + patch 2 enabled should
clarify whether patch 3 is also needed. If a user reports "still no
BT↔dongle concurrent after both patches applied", patch 3 is the next
candidate.

## Audio codecs supported

Strings in the firmware confirm support for:

- **SBC** — standard BT audio codec (low quality, universal)
- **AAC** — Apple/Bluetooth high-quality codec
  (strings: `AAC_ON`/`AAC_OFF`)
- **mSBC** — modified SBC for HFP wideband speech (mic)
- **CVSD** — legacy HFP narrowband speech codec
- **LC3** — Bluetooth LE Audio codec (`LC3I_Enc_Prcs`, `LC3I_Dec_Prcs`)

NOT present in firmware: **aptX**, **aptX HD**, **LDAC**. Those would require
licensed codec implementations which Audeze evidently doesn't ship.

The LC3 strings imply LE Audio support is at least partially compiled in,
even though Maxwell isn't marketed as an LE Audio headset. This is a
potential research target — if LE Audio works (or could be enabled via
configuration), it would offer better latency and lower bitrate options.

## Parametric EQ (PEQ) — the preset system

Strings reveal two PEQ instances:

- `PEQ_SVN_version:0x%x ` — main PEQ version tag
- `PEQ2_INIT:0x%x ` / `PEQ2_PROC:0x%x ` — secondary PEQ (used for second
  filter bank, possibly mic processing or per-channel)
- `PEQ phase:%d enable:%d realtime` — runtime PEQ enable
- `PEQ phase:%d enable:%d nvkey:0x%x` — **PEQ is configured via NVDM keys**

The factory-init NVDM defaults at file offset `0x24B970`+ write:

- **`NVDM 0xE304`** (194 bytes) — first PEQ coefficient block
- **`NVDM 0xE301`** (16 bytes + 564 bytes via two writes) — second
  coefficient block (the 564 bytes likely covers all 10 EQ presets' filter
  coefficients — ~56 bytes per preset)

Patching these NVDM defaults in firmware would change the baseline EQ
behavior. Decoding the exact coefficient format (likely biquad: 5 floats
per section × N sections × M bands) is a research target for anyone wanting
to customize Maxwell's sound signature.
