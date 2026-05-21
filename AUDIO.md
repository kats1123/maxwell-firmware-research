# Audio Pipeline & L/R Balance

This is the most important document for anyone trying to understand or fix the
L/R balance issue. The audio mixer architecture and per-source gain system are
fully reverse-engineered here.

> **READ FIRST (May 2026):** the section **"Per-source balance: full
> investigation (May 2026)"** at the bottom of this file is the current,
> empirically-verified picture — every claim there was tested on real
> hardware. Where the older sections above it disagree, the May 2026
> section wins. Key correction: the per-source balance mechanism is real
> and works, but it is **gated on `NVDM 0xF702`, which nothing ever
> sets** — so the correction never matches the actual source. The older
> sections' RE detail (mixer, audio-context struct, EQ system) is still
> good; only the "how the source state gets set" claims were wrong.

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
RACE command (sub-command `0x2F` of command `0x0900`).

> **CORRECTION (May 2026, empirically verified):** an earlier version of
> this section said "the Audeze app sets this when connection state
> changes." **That is wrong — tested and disproven.** The Windows Audeze
> app does NOT write `0xF702` (verified by opening it, fresh-connecting
> the headset, and reading `0xF702` — unchanged). Neither does the dongle
> nor a Bluetooth connection. **Nothing observed ever sets `0xF702`.**
> See the "Per-source balance: full investigation (May 2026)" section at
> the bottom of this file for the complete, tested picture. The
> consequence: the loader always runs against a stale `0xF702`, so the
> per-source balance correction never actually matches the real source.

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

---

# Runtime balance behavior — empirical findings (December 2025 session)

After successfully flashing custom firmware with patched balance defaults,
extensive on-device testing revealed that **the per-source NVDM design
described above is real in flash but effectively unused at runtime**. This
section documents what we proved empirically vs. what's just code-level
description.

## The actual runtime balance buffer

There is a **single 4-byte runtime buffer** at `0x142039AC` in SRAM. Its
layout (decoded empirically from observed writes and reader functions):

| Offset | Field | Source |
|--------|-------|--------|
| `+0` | LEFT gain byte | Read by `FUN_81DE080(r0=1)` (RACE 0x0901 sub 0x29) |
| `+1` | RIGHT gain byte | Read by `FUN_81DE080(r0=2)` (RACE 0x0901 sub 0x2A) |
| `+2` | Balance slider position (-6..+6, written by RACE 0x0900 sub 0x25) | code at `0x081DE008`+ |
| `+3` | Balance slider direction | same |

**This single buffer is used by both USB-C and BT/dongle audio paths.**
There is no per-source runtime buffer. Verified by: setting `0x142039AC`
to `93 8D` (L=147 R=141) via RACE while on USB-C, then switching to
dongle audio — user heard "L much louder" via dongle, matching the values
set for USB-C. Confirmed the buffer is shared.

## How the buffer gets loaded (CONFIRMED May 2026)

> An earlier version of this section described two "loader functions" and
> said the boot loader was untraced/dead. **That has been corrected.** The
> boot loader is found and confirmed — see below.

**The boot loader:** the code at `0x081DE2E4` reads `NVDM 0xF665` (USB-C)
or `0xF668` (dongle) and writes the 4-byte result into `0x142039AC`,
then DSP-applies it. It is **not a standalone function** — it is the
*tail* of `FUN_0x081DE120`, the boot audio-routing init that `main()`
calls. Execution falls straight through into it (no return in between).
So it runs **at every reboot**:

```
reboot → main() [FUN_0x081D9354] → FUN_0x081DE120 → (fall-through)
       → read NVDM 0xF665/0xF668 → write 0x142039AC → DSP-apply
```

Empirically proven: flashing stock v1.0.1.63 with no host software
running left `0x142039AC` holding the NVDM `0xF665` value (`8D 95`),
not the `.data`-init value (`88 88`).

**`FUN_0x081DDFD4`** (formerly mislabeled "Loader A"): the *slider*
handler, not a loader. One `BL` caller (`0x0817B250`), in the cmd 0x0900
RACE path. Writes `0x142039AC[2]/[3]` (slider/dir) and persists to NVDM.

**Why a live source switch does nothing:** the loader is part of
`main()`. A physical USB↔dongle switch or a RACE `0x2F` sub-cmd does not
re-run `main()`, so the buffer is not reloaded mid-session. It only
reloads on a genuine reboot (firmware update / factory reset).

## What we observed across source/state events

Test sequence (post-factory-reset, custom firmware with patched `0xF665`
and `0xF668`):

| Event | Buffer `0x142039AC` after | RACE read of L/R |
|-------|---------------------------|------------------|
| **Baseline** (USB-C, just plugged) | `8D 9A 00 00` | L=141 R=154 |
| Played USB-C audio | unchanged | L=141 R=154 |
| User reported "L much quieter than R" via USB-C | (consistent with `R=154 > L=141`) | — |
| RACE write L=141 R=143 (our `0xF665` patched values) | `8D 8F 00 00` | L=141 R=143 |
| User reported "L louder, R quieter, but L still quieter" | — | — |
| Iterative RACE writes to find balance: L=147 R=141 ("balanced on USB-C") | `93 8D 00 00` | L=147 R=141 |
| **Pause audio playback** | unchanged | L=147 R=141 |
| **Unplug + replug USB-C** | unchanged | L=147 R=141 |
| **Plug in dongle (USB-C disconnected)** | unchanged (read via dongle's RACE forwarding) | L=147 R=141 |
| **Play audio via dongle** | unchanged; user heard "L much louder" via dongle | L=147 R=141 |
| **Send RACE sub 0x2F state=0** (BT/dongle) | unchanged | L=147 R=141 |
| **Send RACE balance write to trigger loader** | unchanged | L=147 R=141 |
| **Send RACE sub 0x2F state=10** (USB-C) | unchanged | L=147 R=141 |
| **Send RACE balance write again** | unchanged | L=147 R=141 |
| **Full headset power-cycle** (off → wait → on) | unchanged (RACE via dongle reports same) | L=147 R=141 |
| **Factory reset** (NVDM 0xF082 = 'U') | `8D 8F 00 00` | L=141 R=143 |

## What this proves and disproves

### Proven

- **`0x142039AC[0]`/`[1]` is the live runtime gain that the audio mixer
  actually uses for output.** Changing those bytes audibly changes
  perceived L/R balance — for both USB-C and dongle audio.
- **The buffer is shared across all sources.** USB-C and dongle audio
  both consume the same two bytes; there is no per-source runtime gain
  buffer in SRAM.
- **Source-change does NOT auto-reload from NVDM.** Neither physical
  source change (USB-C unplug / dongle plug) nor RACE source-state
  change (sub 0x2F) triggers either loader function. The buffer just
  keeps whatever was last written to it.
- **RACE balance writes (RACE 0x0900 sub 0x29/0x2A) are persistent.**
  They survive full power-cycle. Strong evidence that the write path
  updates BOTH `0x142039AC` AND `NVDM 0xF665` (the active-source key).
  Confirmed via: tuned values survived headset power-off > 10s, then
  on, then re-pair via dongle.
- **Factory reset reliably re-applies patched firmware defaults.**
  After reset, `0x142039AC` snapped to `8D 8F` = `0xF665` patched
  value (141/143). This is the strongest evidence that the custom
  firmware patches are live in the device.
- **Some balance values are rejected by validation.** Specifically,
  writing R=`0x8E` (142) is silently rejected — the buffer reverts to
  the previous value (or to NVDM, unclear). Values 0x8C, 0x8D, 0x8F,
  0x90, 0x93, 0x9A all worked. `0x8E` specifically is blocked. Cause
  unknown — could be a reserved sentinel, a saturation gate, or some
  encoding-specific check in the writer at `0x817B132+`.

### Disproven

- **Per-source NVDM keys are NOT actually used as such at runtime.**
  After factory reset, on dongle hardware, the buffer loaded
  `0xF665` (USB-C key) value, not `0xF668` (dongle key). Through all
  test events while on dongle, `0xF668` value never appeared in the
  runtime buffer.
- **The Audeze app sending `0x2F sub` to "tell the chip about source
  changes" does not cause a per-source reload.** We sent state=0 and
  state=10 via RACE; nothing changed.

### Resolved (May 2026)

- **What triggers the loader** — `main()` calls `FUN_0x081DE120` at
  every reboot; the loader is that function's tail. No mysterious
  indirect caller — it runs by fall-through. It is NOT triggered by a
  live source switch (that doesn't re-run `main()`).
- **Why the loader picks `0xF665`** — it reads the current source state
  via `FUN_0x0817B2F4` (NVDM `0xF702`) and picks `0xF665` when state is
  USB-C (`0xA`), `0xF668` otherwise. On the test unit the boot source
  state was USB-C so `0xF665` was used.
- **The buffer IS reloaded from NVDM on reboot** — confirmed
  empirically. The earlier "dual-NVDM design is broken / hookup
  incomplete" hypothesis was wrong and is retracted.

### Still open

- Whether `NVDM 0xF668` (dongle) actually gets exercised — the loader's
  `0xF668` branch is real, but to load it the boot source state must be
  non-USB-C. Not yet tested on a dongle-first boot.
- The `0x8E` write-rejection cause (see validation section below).

## RACE balance value validation rejection

Empirically tested write values (sent via RACE 0x0900 sub 0x29 LEFT and
sub 0x2A RIGHT):

| Value | Result |
|-------|--------|
| `0x8C` (140) | accepted, buffer updated |
| `0x8D` (141) | accepted |
| **`0x8E` (142)** | **REJECTED, buffer not updated (silently reverted)** |
| `0x8F` (143) | accepted |
| `0x90` (144) | accepted |
| `0x93` (147) | accepted |
| `0x9A` (154) | accepted |

A more thorough sweep is needed to map the full rejected set. The validation
logic is somewhere in the cmd 0x0900 sub 0x29/0x2A write handler
(starting at `0x817B132` per the dispatcher analysis); decoding it would
explain why `0x8E` specifically is blocked.

---

# Per-source balance: full investigation (May 2026)

This section is the complete, empirically-verified picture of the
Maxwell's per-source L/R balance system — why the headset sounds
imbalanced and why the built-in correction never applies. Every claim
here was tested on real hardware.

## Measured imbalance (the actual defect)

With the runtime buffer set to a **symmetric** value (L==R, so no
balance correction applied), the headset's own output was measured:

| Source | Left | Right | Imbalance |
|--------|------|-------|-----------|
| USB-C  | 57.5 | 64.3  | **R louder by 6.8 dB** |
| Dongle | 53.1 | 55.3  | **R louder by 2.2 dB** |
| Bluetooth | not measured | | (owner does not use BT) |

Both sources lean the **same direction** (right louder) but by very
different amounts. So the imbalance has two components:
- a **consistent** part (right always louder) — a base headset/hardware
  imbalance, source-independent;
- a **per-source** part — the USB-C path adds ~4.6 dB more right-bias
  on top of the dongle.

A single correction value therefore cannot fix both sources. They
genuinely need different corrections (same direction, different size).

## The correction mechanism (and why it never applies)

The firmware *has* a per-source balance correction system. It works
like this:

```
NVDM 0xF665  = USB-C balance   (4 bytes: L, R, slider, dir)
NVDM 0xF668  = wireless balance (dongle AND bluetooth share this)
NVDM 0xF702  = "source state"  (0x0A = USB-C, anything else = wireless)

at every restart:
  main() -> FUN_0x081DE120 -> (tail) loader:
     read 0xF702
     if 0xF702 == 0x0A:  load NVDM 0xF665 -> buffer 0x142039AC
     else:               load NVDM 0xF668 -> buffer 0x142039AC
     DSP-apply the buffer
```

Five findings, each empirically proven, explain why this never works
in practice:

1. **The loader works** — set `0xF702=0`, restart → the buffer loaded
   `0xF668`'s value; set `0xF702=0x0A`, restart → loaded `0xF665`. The
   pick logic is functional.
2. **The loader runs only at a restart** (power down/up re-runs
   `main()`). A live source switch with the headset staying powered
   does NOT reload the buffer (proven: a directly-written marker
   survived a USB-C reconnect).
3. **The loader keys off `0xF702` ONLY — never the physical source.**
   Restarting while physically on the dongle, with `0xF702=0x0A`, still
   loaded the USB-C key `0xF665`.
4. **Nothing ever sets `0xF702`.** Tested and ruled out: the Windows
   Audeze app (opened, and fresh-connected), the dongle (connect /
   round-trip), a Bluetooth connect + audio session. Code-traced:
   `main()` never writes `0xF702`; the only writer in the whole
   firmware is the RACE sub-`0x2F` handler (`0x0817B2B2`), which needs
   a host command. The physical-source detection (transition dispatcher
   `FUN_0x08154BA8`) runs later, in task context, *after* the loader —
   and never writes `0xF702`.
5. **Only 2 slots for 3 sources.** The loader's choice is binary
   (USB-C vs not-USB-C), so the dongle and Bluetooth are forced to
   share `0xF668` and cannot be corrected independently.

**Net effect:** `0xF702` holds whatever stale value it was last left
with; the loader copies one fixed NVDM key into the buffer on every
restart; the correction is never matched to the actual source. The
two working halves of the feature — physical-source detection and the
balance loader — were never wired together.

## Audeze knew: the factory defaults prove it

The stock factory-default values are:
- `NVDM 0xF665` (USB-C)    = `(141, 149)` — **asymmetric**
- `NVDM 0xF668` (wireless) = `(147, 147)` — **symmetric**

An asymmetric default is a deliberate correction; you do not ship one
by accident. Audeze knew the USB-C path has an inherent L/R imbalance
and baked a correction into its default. The per-source balance system
is a real, intended feature — it is simply not wired up.

There is **no balance control in the Audeze app**, so this was never a
user-facing setting — it was meant to be a transparent, automatic
correction.

## Version history

Firmware `v1.0.1.61`, `.63`, and `.74` are identical in the
source-state and balance code (same NVDM-key sites, same instruction
sequences — `tools/compare_versions.py`). Nothing changed across these
three. Firmwares before `.61` exist but were not available, so an
earlier change cannot be ruled out.

## Practical notes for testing

- The runtime buffer `0x142039AC` can only be read reliably over
  **USB-C** (PID 0x4B1E). Over the **dongle** (PID 0x4B18) a RACE
  `0x1680` RAM read hits the *dongle's own* memory (the dongle is a
  separate Airoha chip) and always returns `00 00 00 00` — it does not
  reflect the headset's buffer.
- Source state is read with `cmd 0x0901 sub 0x2F`, written with
  `cmd 0x0900 sub 0x2F` (see PROTOCOL.md).
- A direct RAM write (`cmd 0x1681`) changes only the SRAM buffer and
  does NOT persist to NVDM — useful to make the buffer differ from
  NVDM for restart tests.

## The fix (direction)

A real fix must be firmware-side (the headset is used on phones and
consoles with no host software). The cleanest approach: patch the
**source-transition handler** — which already knows the source (it
receives a transition code) — to write `NVDM 0xF702` with the matching
value. Since switching sources already restarts the headset, the next
restart's loader then reads the corrected `0xF702` and loads the right
key. Combined with proper per-source correction values in `0xF665`/
`0xF668`, this makes the per-source balance actually function.

Open item: confirm the transition-code → source mapping and find a
code-injection slot for the `0xF702` write.

## The per-source DSP profile system (May 2026)

NOTE: this mechanism was twice wrongly written up as the "root cause"
of the L/R imbalance. It is NOT — see "Why this is not the root cause"
below. This section documents a real firmware structure; it does not
explain the imbalance.

### What `FUN_0x081DE120` actually does

The boot audio init reads the source state once (`r7 = FUN_0x0817B2F4()`
= NVDM `0xF702`, `0x0A` = USB-C, anything else / absent = wireless) and
then drives the audio DSP three ways, **all gated on that one `r7`**:

1. **A per-source coefficient table at `0x082938CE`.** 550 six-byte
   entries: `[coeff:u16-LE, wireless_L, wireless_R, usbc_L, usbc_R]`.
   A loop walks the table; for each entry it writes `coeff` to the DSP
   with the **wireless** pair (offsets -4/-3) when `r7 != 0x0A`, or the
   **USB-C** pair (offsets -2/-1) when `r7 == 0x0A`. Channel `0x38` =
   LEFT, `0x39` = RIGHT, via `FUN_0x081DDF54` → `FUN_0x081DDEEC` →
   `FUN_0x08138D68` (a retried hardware-bus transaction).

2. **A standalone USB-C-only write:** `cmp r7,#0xa` then, only if USB-C,
   `coeff 0x23E0 = (L=0, R=0x20)`. Wireless never writes `0x23E0`.

3. **The balance loader** (function tail): loads NVDM `0xF665` (USB-C)
   or `0xF668` (wireless) into `0x142039AC`, then `FUN_0x081DDF78`
   applies it to **DSP coeff `0x23BA`**: `0x23BA L = buf[0]+buf[2]`,
   `0x23BA R = buf[1]+buf[3]`. So "balance" = coeff `0x23BA` L/R gain;
   the 4-byte buffer is `[L_base, R_base, L_trim, R_trim]`.

### The decisive measurement (table, all 550 entries)

| column | asymmetric (L≠R) entries | zeroed (L=R=0) entries |
|--------|--------------------------|------------------------|
| wireless | **1** / 550 | 248 / 550 |
| USB-C    | **101** / 550 | far fewer |

The table is triplet-structured (groups of 3 consecutive coeffs +
Q-format shift byte) — a multi-band biquad filter bank, i.e. the EQ /
voicing.

- **Wireless profile**: symmetric (L = R for all but one coeff) and
  sparse — ~248 coeffs bypassed.
- **USB-C profile**: heavily **per-channel-distinct** (101 asymmetric
  coeffs) and denser.

So Audeze *did* engineer a separate, per-channel-corrected filter for
the USB-C path. The asymmetry IS a deliberate L/R correction baked into
the USB-C voicing. They knew the inputs differ and built a fix for it.

### Why this is not the root cause

The selector is NVDM `0xF702`. Exhaustive whole-firmware scan (BL
callers, literal pools, raw data — not just `movw` immediates):
`0xF702` is touched by **exactly two** instructions — `FUN_0x0817B2F4`
(the *reader*) and one *writer* inside the RACE `sub 0x2F` host-command
handler (`0x0817B2B2` → `nvdm_write`). No registered factory default.
The headset's own runtime never writes it; empirically, neither the
Windows app, dongle-connect nor BT-connect write it.

So in practice `0xF702` is **effectively constant** (absent → reader
returns 0) — every unit, every boot, picks the same column. A
configuration that is identical on every unit cannot explain a defect
that only *some* users get, *sometimes*, with varying severity. A
constant cannot produce a variable.

**This mechanism is therefore NOT the root cause of the L/R imbalance.**
It was promoted to "root cause" twice in earlier drafts (first as
"applies wireless to everyone", then as "path-dependent / drifts") —
both were errors of the same kind: declaring a firmware structure the
cause without checking it can produce the observed *variation*.

### Root cause — still unknown; what the field reports constrain it to

Community reports (per the project owner): *some* people, *sometimes*,
experience the imbalance; for some a reset (sometimes specifically
resetting the connected phone) fixes it; for others no reset helps.
Direction is consistent (one channel quieter — left, on the owner's
unit).

That shape — intermittent, sometimes cleared by a reset, sometimes
stuck — rules out **both** a fixed hardware mismatch and a fixed
firmware config (both are constant). It points to a **stateful value
that is normally volatile but can be persisted to storage**:

- goes bad at runtime → intermittent;
- a restart reloads it from storage → "any reset works" when storage
  is still clean;
- bad value reaches storage → restart reloads it → "no way to fix it";
- a host re-pushing a clean value → "reset the phone and it fixes it".

The only subsystem with all of those properties is the **runtime
balance buffer `0x142039AC`** — volatile SRAM, reloaded from NVDM
(`0xF665`/`0xF668`) on every restart, writable at runtime, persistable
back to NVDM. This is a *search direction*, not a conclusion: the next
real step is to enumerate every runtime writer of `0x142039AC` and of
`0xF665`/`0xF668` and find what can write them asymmetrically.

**Still genuinely open. Do not write up a root cause until a writer
that can produce the imbalance is actually identified.**
