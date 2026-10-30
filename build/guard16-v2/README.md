# Guard16 v2 — ARMv7 bridge build & source package (2026-10-30)

**Scope: only the D5 uplink bounded-drain change and the recovery integration reference.**
This is a public, offline-auditable source snapshot; it does **not** install
anything on a modem, Synology NAS, Docker container, or Asterisk.

## Provenance / license

- Upstream project: [MaVo](https://github.com/moluncn/mavo), MIT licensed,
  source revision `0443dfdaf8aec086fd76ba2ee9152fd908114524`, path `module/mavo_pcm_bridge.c`.
  The repository's original bridge code is **not** the same as `original.c` here:
  `original.c` is the subsequently developed, already Guard16 v1 paced-bridge
  baseline from this QDC507 project. It is retained to audit the v1 → v2 delta.
- `mavo_pcm_bridge_16k_clock8_guard16.c` is the complete v2 source derived from
  `original.c` using `guard16-v2.patch`. Both remain subject to the upstream MIT
  attribution / notice in `LICENSE-MAVO.txt`.
- `reference/bridge-d5-v2.armv7` is the **reference ARMv7 executable** from the
  isolated 2026-10-28 build. It was built from the v2 source; it is not vendor
  firmware, a driver module, nor a complete QDC507 deployment image. Its SHA256
  is the same as the binary subsequently observed as Guard16 v2 on the device.
- No proprietary `libql_lib_audio.so.1`, modem firmware, SIM content, host
  configuration, device-specific recovery manifest, ADB credentials, logs, or
  private v1 backup binary is bundled.

## Exact reference hashes

| Item | SHA256 |
| --- | --- |
| Guard16 v1 device binary (not distributed) | `7375b61dc6e8661d02ef17f98097ccae3598782c466b06a50c90ff5454515e71` |
| Guard16 v2 ARM reference binary | `0e9272cdd832759c1704857d4a2c4abd0323a3af41d2529f84058b4431edc913` |
| v1 complete source (`original.c`) | `d7540f4d2054fe2d483b0b9868a00e45a03cf89917394a7df35f6e75be0aac41` |
| v2 complete source | `17d1afdec5d3a152fd59e1f0b598051dda4ae4fce3ae9d002c57580ec7fc4ba8` |

`source-manifest.json` records SHA256 + byte length for the other distributed files.

## Change and bounded behavior

The v1 D5 uplink used a single nonblocking read service per PCM deadline.
The v2 patch preserves the 8 ms period and D6 downlink but permits a bounded
post-playback TTY drain of **at most 256 bytes and 256 read attempts per outer
iteration**, with a nonblocking poll after the first attempt. No perpetual
polling, generic busy-spin, unlimited retry, or change to the D6 path.

Measured in deterministic 60-frame **simulations** at 8 ms PCM writes:
127-byte fragmentation had 27 zero-padded frames in v1 and 0 in v2;
1/127/255-byte fragmentation cases each had 0 zero-padded frames in v2.
These are simulated regression outcomes, **not** a real-time latency guarantee.
At sustained PCM writes >8 ms, upstream read scheduling alone cannot fix
throughput or queue buildup.

## Verify offline (Linux/macOS with Python 3 and patch)

```sh
python3 verify-source.py
sh run_tests.sh
# On a toolchain with sanitizer trap support:
EXTRA_CFLAGS='-fsanitize=undefined -fsanitize-undefined-trap-on-error' sh run_tests.sh
```

Local recheck on 2026-10-30: 157 PASS regular and 157 PASS UBSan trap,
including original negative controls, byte-for-byte D6 unchanged checks,
fragmentation and error/stop simulations. These results do not test on hardware.

## Cross-build (isolated Linux workstation only)

Prerequisites: `bash`, `python3`, `patch`, `curl`, `dpkg-deb`, `sha256sum`,
`arm-linux-gnueabi-gcc` and `arm-linux-gnueabi-readelf` (cross compiler for ARM
**soft-float EABI**, not `gnueabihf` or AArch64). Never run on the production NAS
or QDC507 itself.

```sh
bash build-cross.sh --fetch-sysroot     # explicit network fetch, pinned Debian .deb hashes
bash build-cross.sh --build             # offline build, no network, no deployment
```

The historical successful isolated build used GCC 9.4.0, Debian Jessie 2.19
armel `libc6` and `libc6-dev` version `2.19-18+deb8u10` extracted (not installed)
into a private sysroot. The script applies ARMv7-A `-mfloat-abi=soft`, EABI5,
`/lib/ld-linux.so.3`, and links against the old sysroot C runtime; the original
lab executable required only `GLIBC_2.4` and `GLIBC_2.17`, with `libpthread.so.0`,
`libdl.so.2`, `libc.so.6` as runtime NEEDED libraries.

**The portable script here has been statically checked but not rerun through an
ARM cross-build in this publication environment.** Compiler/toolchain versions,
linker differences and build paths may change the resulting SHA256. A successful
ABI check is not proof of exact binary equivalence. The included reference ELF
has the above pinned SHA and readelf properties.

## Restoring production safely

See [Guard16 v2 recovery notes](../../docs/guard16-v2-build-recovery.md).
The actual NAS recovery helpers already reside in
[`checkpoint/2026-10-30/recovery/`](../../checkpoint/2026-10-30/recovery/).
Their hashes, A/B backups and baseline manifests are intentionally pinned to
one exact production environment. **Publishing this code/binary does not make a
fresh NAS or Docker container fully restorable from GitHub alone.**
