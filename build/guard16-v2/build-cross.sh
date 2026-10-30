#!/usr/bin/env bash
# Offline-first, isolated ARMv7/EABI5 soft-float build. NEVER deploys to modem/NAS.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
LOCAL="$ROOT/_local"
CACHE="$LOCAL/cache"
OUTDIR="$LOCAL/output"
SYSROOT="$LOCAL/sysroot"
OUT="$OUTDIR/bridge-d5-v2.armv7"
VER='2.19-18+deb8u10'
DEBBASE='https://archive.debian.org/debian/pool/main/g/glibc'
GCC="${ARM_CC:-arm-linux-gnueabi-gcc}"
READELF="${ARM_READELF:-arm-linux-gnueabi-readelf}"
usage(){
  cat <<'HELP'
Usage:
  bash build-cross.sh --fetch-sysroot  # Explicit network download of pinned Debian armel .debs
  bash build-cross.sh --build          # Offline build; requires local compiler + cached verified .debs

Linux x86_64 cross-build host only. No sudo; no calls to NAS, ADB or modem.
Output is under _local/output/, which is gitignored. Never install blindly.
HELP
}
[[ $# -eq 1 ]] || { usage; exit 2; }
case "$1" in --fetch-sysroot|--build) ACTION="$1";; -h|--help) usage; exit 0;; *) usage; exit 2;; esac
if [[ -e /etc/synoinfo.conf ]] || [[ -e /usr/syno/synoinfo.conf ]]; then
  echo 'REFUSE_TO_BUILD_ON_PRODUCTION_SYNOLOGY=1' >&2; exit 3
fi
if [[ "$(id -u)" == 0 ]]; then echo 'RUN_UNPRIVILEGED_NO_SUDO=1' >&2; exit 3; fi
[[ -d "$LOCAL" && ! -L "$LOCAL" ]] || { [[ ! -e "$LOCAL" ]] && mkdir -m 700 "$LOCAL" || { echo 'UNSAFE_LOCAL_BUILD_DIR' >&2; exit 3; }; }
mkdir -p "$CACHE"
for package in libc6 libc6-dev; do
  if [[ "$package" == libc6 ]]; then SHA='0b53023e421022a6b25b0e63cbfe1bd15df01cd41bf8cc62a1de8e375a674ebd'
  else SHA='fe1beac9ff010229e4d075430a89d6f19bdac4510c14c47f7717927c51ad2399'; fi
  DEB="$CACHE/${package}_${VER}_armel.deb"
  if [[ "$ACTION" == --fetch-sysroot && ! -s "$DEB" ]]; then
    command -v curl >/dev/null || { echo 'curl REQUIRED' >&2; exit 4; }
    TMP=$(mktemp "$CACHE/download.XXXXXXXX")
    trap 'rm -f "${TMP:-}"' EXIT
    curl -fL --retry 0 --connect-timeout 15 --max-time 180       "$DEBBASE/${package}_${VER}_armel.deb" -o "$TMP"
    echo "$SHA  $TMP" | sha256sum -c - >/dev/null || { echo 'DEBIAN_PACKAGE_HASH_INVALID' >&2; exit 5; }
    mv "$TMP" "$DEB"
    trap - EXIT
  fi
  [[ -f "$DEB" && ! -L "$DEB" ]] || { echo "MISSING_VERIFIED_DEB=$DEB" >&2; exit 5; }
  echo "$SHA  $DEB" | sha256sum -c - >/dev/null || { echo "DEBIAN_PACKAGE_HASH_INVALID=$DEB" >&2; exit 5; }
  [[ "$(dpkg-deb --field "$DEB" Package)" == "$package" ]] || exit 6
  [[ "$(dpkg-deb --field "$DEB" Architecture)" == armel ]] || exit 6
  [[ "$(dpkg-deb --field "$DEB" Version)" == "$VER" ]] || exit 6
done
if [[ "$ACTION" == --fetch-sysroot ]]; then
 echo 'VERIFIED_DEBIAN_ARMEL_SYSROOT_PACKAGES_AVAILABLE=1'; echo 'NO_BUILD_NO_DEVICE_WRITES=1'; exit 0
fi
for p in "$GCC" "$READELF" dpkg-deb sha256sum python3; do
  command -v "$p" >/dev/null || { echo "MISSING_BUILD_TOOL=$p" >&2; exit 7; }
done
python3 "$ROOT/verify-source.py"
# Keep all temporary compilation inputs inside the ignored, non-shared local build folder.
[[ ! -L "$SYSROOT" ]] || { echo 'UNSAFE_SYSROOT_LINK' >&2; exit 8; }
mkdir -p "$SYSROOT" "$OUTDIR"
for package in libc6 libc6-dev; do
  dpkg-deb -x "$CACHE/${package}_${VER}_armel.deb" "$SYSROOT"
done
L="$SYSROOT/usr/lib/arm-linux-gnueabi/libdl.so"
if [[ -L "$L" && "$(readlink "$L")" == '/lib/arm-linux-gnueabi/libdl.so.2' ]]; then
  rm "$L"
  ln -s ../../../lib/arm-linux-gnueabi/libdl.so.2 "$L"
fi
CRT="$SYSROOT/usr/lib/arm-linux-gnueabi"
LIB="$SYSROOT/lib/arm-linux-gnueabi"
for f in crt1.o crti.o crtn.o libc.so libpthread.so libdl.so; do
  [[ -e "$CRT/$f" ]] || { echo "SYSROOT_MISSING=$f" >&2; exit 8; }
done
[[ -e "$LIB/libc.so.6" ]] || { echo 'SYSROOT_MISSING_LIBC' >&2; exit 8; }
GCC_INCLUDE="$($GCC -print-file-name=include)"
EXTRA_INC=()
[[ -d /usr/arm-linux-gnueabi/include ]] && EXTRA_INC=(-isystem /usr/arm-linux-gnueabi/include)
echo '=== ARM compiler version (record for reproducibility) ==='
"$GCC" --version | head -1
"$GCC" -std=gnu11 -O2 -g0 -Wall -Wextra -D_GNU_SOURCE   -march=armv7-a -mfloat-abi=soft -fno-stack-protector -fno-pie   -nostdinc -isystem "$GCC_INCLUDE"   -isystem "$SYSROOT/usr/include/arm-linux-gnueabi"   -isystem "$SYSROOT/usr/include" "${EXTRA_INC[@]}"   -c "$ROOT/mavo_pcm_bridge_16k_clock8_guard16.c" -o "$LOCAL/bridge.o"
CRTBEGIN="$($GCC -print-file-name=crtbegin.o)"
CRTEND="$($GCC -print-file-name=crtend.o)"
"$GCC" -march=armv7-a -mfloat-abi=soft   -nostdlib -no-pie -Wl,--export-dynamic   -Wl,--sysroot="$SYSROOT" -Wl,--dynamic-linker=/lib/ld-linux.so.3   -Wl,-z,relro -Wl,-z,now   -Wl,-rpath-link,"$LIB" -Wl,-rpath-link,"$CRT"   -L"$CRT" -L"$LIB" -o "$OUT"   "$CRT/crt1.o" "$CRT/crti.o" "$CRTBEGIN" "$LOCAL/bridge.o"   -Wl,--start-group -lpthread -ldl -lrt -lc -lgcc -Wl,--end-group   "$CRTEND" "$CRT/crtn.o"
"$READELF" -h "$OUT" > "$OUTDIR/elf-header.txt"
"$READELF" -l "$OUT" > "$OUTDIR/elf-programs.txt"
"$READELF" -V "$OUT" > "$OUTDIR/elf-versions.txt"
"$READELF" -d "$OUT" > "$OUTDIR/elf-dynamic.txt"
python3 "$ROOT/verify-elf.py" "$OUT"   "$OUTDIR/elf-header.txt" "$OUTDIR/elf-programs.txt" "$OUTDIR/elf-versions.txt" "$OUTDIR/elf-dynamic.txt"
ACTUAL="$(sha256sum "$OUT" | awk '{print $1}')"
REFERENCE='0e9272cdd832759c1704857d4a2c4abd0323a3af41d2529f84058b4431edc913'
echo "BUILD_OUTPUT_SHA256=$ACTUAL"
if [[ "$ACTUAL" == "$REFERENCE" ]]; then
  echo 'BYTE_IDENTICAL_TO_RECORDED_V2=1'
else
  echo 'BYTE_IDENTICAL_TO_RECORDED_V2=0'
  echo 'ABI_AUDIT_ALONE_IS_NOT_DEPLOYMENT_APPROVAL=1'
fi
echo "BUILD_SUCCESS_NO_DEPLOY=$OUT"
