# v1.0.1.63 vs v1.0.1.74 — Byte-Level Differences

Audeze released v1.0.1.74 as the "latest" firmware for the Maxwell, but many
users found L/R balance worse on it than on v1.0.1.63. Here's a precise
diff of what changed.

## TL;DR

- **Hardware register usage**: Identical (both versions use the same 78 audio
  registers)
- **RACE protocol structure**: Identical (same dispatch table, same command IDs)
- **Audio mixer code**: Identical (functions in same place)
- **Factory default gain values**: **Changed for BT/dongle** — the visible
  behavior change that broke balance for some users
- **Function offsets**: A handful of functions moved by 0x10–0x280 bytes
- **Total differing bytes**: **~1.3 million** (~42% of the 3.2 MB
  decompressed image) — Audeze made substantial changes beyond just the
  balance defaults. Most diffs are in partition 2 (the main code, file
  offsets `0x114000`+). What those changes actually do remains
  unresearched — community RE welcome.

## File-level comparison

| Property | v1.0.1.63 | v1.0.1.74 |
|----------|-----------|-----------|
| Compressed file size | 2,052,331 bytes | 2,052,268 bytes |
| Decompressed size | 3,203,072 bytes | 3,203,072 bytes |
| Header SHA-256 | `64c33d36...` | `f657e702...` |
| LZMA dictionary size | 16 KB | 16 KB |
| LZMA properties | `0x5D` (lc=3, lp=0, pb=2) | same |

Both versions use the same LZMA-Alone format and decompressed size — they only
differ in actual byte content.

## Factory default gain values (the user-visible change)

Located in `FUN_00186C60` (v63) / `FUN_00186C70` (v74) — the function called
during factory reset to populate NVDM keys.

### USB-C wired (`NVDM 0xF665`)

**Unchanged** between versions:
```
L = 141 (0x8D)
R = 149 (0x95)   ← +8 R "compensation"
```

The `+8 R` was already there in v63 and remained in v74.

### BT/Dongle (`NVDM 0xF668`)

**This is what changed**:

| Version | L | R | Description |
|---------|---|---|-------------|
| v1.0.1.63 | 141 | 141 | No compensation, both equal |
| v1.0.1.74 | **147** | **147** | Both boosted by +6 (still balanced) |

So v74 raised the overall BT/dongle volume by 6 units without changing L/R
relative balance. Speculation: Audeze tuned the BT path to be slightly louder
to match the perceived loudness of USB-C, since BT typically goes through
codec compression that can slightly attenuate perceived volume.

### Why this matters for the L/R balance issue

For units that already had imbalance complaints, v74 didn't *fix* anything —
the USB-C `+8 R` compensation is the same. It just raised BT volume. Users who
preferred BT in v63 (where it was un-compensated) may notice it's now louder
but with the same balance characteristics relative to USB-C.

## Code byte-level diff (patch offsets)

The factory init function moved by `+0x10` bytes:

| Symbol | v63 file offset | v74 file offset | Notes |
|--------|----------------:|----------------:|-------|
| `FUN_00186C60` entry | `0x186C60` | `0x186C70` | Factory init for gains |
| `movw r3, #0x8D8D` / `0x9393` (BT default) | `0x186C62` | `0x186C72` | **Value changed**: `0x8D8D` → `0x9393` |
| `movw r3, #0x958D` (USB-C default) | `0x186C94` | `0x186CA4` | Same value, different offset |

Specifically:

**v63 bytes at `0x186C62`** (`movw r3, #0x8D8D`):
```
48 F6 8D 53
```

**v74 bytes at `0x186C72`** (`movw r3, #0x9393`):
```
49 F2 93 33
```

**v63 bytes at `0x186C94`** (`movw r3, #0x958D`):
```
49 F2 8D 53
```

**v74 bytes at `0x186CA4`** (same `movw r3, #0x958D`):
```
49 F2 8D 53
```

## Function offsets summary

| Function | v63 | v74 | Δ |
|----------|----:|----:|---|
| `FUN_00130EAC` (NVDM read) | `0x130EAC` | `0x130EAC` | 0 |
| `FUN_00135268` (gain mixer) | `0x135268` | `0x135268` | 0 |
| `FUN_00135BA8` (mode switcher) | `0x135BA8` | `0x135BA8` | 0 |
| `FUN_00137F48` (router reset) | `0x137F48` | `0x137F48` | 0 |
| `FUN_001AA6E0` (EQ switcher) | `0x1AA6E0` | `0x1AA6E0` | 0 |
| `FUN_0015C2F4` (state reader) | `0x15C2F4` | `0x15C2F4` | 0 |
| `FUN_00186C60` (factory init) | `0x186C60` | `0x186C70` | +0x10 |
| `FUN_001BEFB4` (balance slider) | `0x1BEFB4` | `0x1BEFF0` | +0x3C |
| `FUN_001BF04C` (gain byte writer) | `0x1BF04C` | `0x1BF0CC` | +0x80 |
| `FUN_001BF08C` (balance loader) | `0x1BF08C` | `0x1BF310` | +0x284 |

Most core functions are at the *exact same offset* in both versions — only the
factory init and the per-source balance handlers moved (and they share a code
region that was probably re-laid-out together).

## RACE protocol differences

**None observed.** All RACE commands, NVDM keys, and audio dispatch behaviors
are identical between v63 and v74. The protocol-level behavior of the headset
is unchanged.

## Hardware register usage

Both versions reference essentially the same set of memory-mapped audio
registers (78 distinct addresses in v74, 79 in v63 with the one extra being a
false positive from random data). No new hardware features were added or
removed.

## Practical implication

If you experience L/R balance issues on v74, downgrading to v63 *may* help
because:

1. The BT path in v63 has L=R=141 (no boost) which can be a better fit for
   some units than v74's L=R=147
2. v63 was the firmware most users had during the "good era" of Maxwell — your
   subjective familiarity with how it should sound is calibrated to v63

But the **USB-C balance defaults are identical between v63 and v74**, so if
your imbalance is on USB-C, downgrading won't fix it. Use runtime RACE
commands (see [PROTOCOL.md](PROTOCOL.md)) to adjust gains directly.

## Diff method (for those wanting to verify)

```bash
# Decompress both
python decompress.py Maxwell_v1.0.1.63_XBOX_headset.bin v63.bin
python decompress.py Maxwell_v1.0.1.74_XBOX_headset.bin v74.bin

# Find differing bytes
python -c "
v63 = open('v63.bin', 'rb').read()
v74 = open('v74.bin', 'rb').read()
diff_count = 0
for i in range(min(len(v63), len(v74))):
    if v63[i] != v74[i]:
        diff_count += 1
print(f'Total differing bytes: {diff_count} of {len(v63)}')
"
```
