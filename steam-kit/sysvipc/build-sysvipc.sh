#!/bin/bash
# Build sysvipc-emu (i686 + x86-64) for x86 programs under FEX on the Y700.
# Runs as root inside a disposable arm64 ubuntu:24.04 container (glibc 2.39 = the FEX RootFS Ubuntu 24.04).
# Inputs : /work/sysvipc-emu.c /work/sysv-test.c.  Outputs: /work/out/sysvipc-emu.tar.gz + .sha256 + build-info.txt
set -euo pipefail
OUT=/work/out
[ "$(uname -m)" = aarch64 ] || { echo "STOP: expected an arm64 build container"; exit 1; }
grep -q 'VERSION_ID="24.04"' /etc/os-release || { echo "STOP: expected Ubuntu 24.04 (glibc of the FEX RootFS)"; exit 1; }
test ! -e "$OUT" || { echo "STOP: $OUT exists"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends gcc-i686-linux-gnu gcc-x86-64-linux-gnu libc6-dev-i386-cross libc6-dev-amd64-cross binutils-i686-linux-gnu binutils-x86-64-linux-gnu
mkdir -p "$OUT/stage/i386" "$OUT/stage/x86_64"
CFLAGS="-O2 -g0 -Wall -Wextra -Wno-unused-result -Wno-misleading-indentation -fvisibility=default"
i686-linux-gnu-gcc   $CFLAGS -fPIC -shared -o "$OUT/stage/i386/libsysvipc-emu.so"   /work/sysvipc-emu.c -lpthread
x86_64-linux-gnu-gcc $CFLAGS -fPIC -shared -o "$OUT/stage/x86_64/libsysvipc-emu.so" /work/sysvipc-emu.c -lpthread
i686-linux-gnu-gcc   $CFLAGS -o "$OUT/stage/i386/sysv-test"   /work/sysv-test.c
x86_64-linux-gnu-gcc $CFLAGS -o "$OUT/stage/x86_64/sysv-test" /work/sysv-test.c
check() {   # $1 readelf prefix, $2 lib: ELF class, exported symbols, highest GLIBC version needed (<= 2.39)
  local re=$1 lib=$2
  "$re-readelf" -h "$lib" | grep -E 'Class|Machine'
  for s in semget semop semtimedop semctl shmget shmat shmdt shmctl; do
    "$re-readelf" --dyn-syms -W "$lib" | awk '$7 != "UND" {print $8}' | grep -qx "$s" || { echo "STOP: $s not exported by $lib"; exit 1; }
  done
  local maxv; maxv=$("$re-objdump" -T "$lib" | grep -o 'GLIBC_2\.[0-9]*' | sort -t. -k2 -n | tail -1)
  echo "$lib needs up to $maxv"
  [ "${maxv#GLIBC_2.}" -le 39 ] || { echo "STOP: $lib needs $maxv (> 2.39 of the RootFS)"; exit 1; }
}
check i686-linux-gnu   "$OUT/stage/i386/libsysvipc-emu.so"
check x86_64-linux-gnu "$OUT/stage/x86_64/libsysvipc-emu.so"
{ echo "sysvipc-emu build $(date -u +%FT%TZ) on $(. /etc/os-release; echo "$PRETTY_NAME")"
  i686-linux-gnu-gcc --version | head -1; sha256sum /work/sysvipc-emu.c /work/sysv-test.c
  (cd "$OUT/stage" && sha256sum */*); } > "$OUT/build-info.txt"
cp /work/sysvipc-emu.c /work/sysv-test.c "$OUT/stage/"
tar -C "$OUT/stage" -czf "$OUT/sysvipc-emu.tar.gz" .
(cd "$OUT" && sha256sum sysvipc-emu.tar.gz > sysvipc-emu.tar.gz.sha256)
cat "$OUT/build-info.txt" "$OUT/sysvipc-emu.tar.gz.sha256"
