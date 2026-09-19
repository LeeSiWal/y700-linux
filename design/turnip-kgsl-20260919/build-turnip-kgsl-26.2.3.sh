#!/bin/bash
# Build headless turnip (Vulkan, freedreno) with the KGSL kernel backend from upstream Mesa 26.2.3.
# Intended to run as root inside a disposable arm64 ubuntu:26.04 container (glibc 2.43, same as the Y700 rootfs).
# Inputs : /work/mesa-26.2.3.tar.xz (sha256 pinned; GPG-verified on the Y700: Good signature, Eric Engestrom 57551DE15B968F6341C248F68D8E31AFC32428A6)
# Outputs: /work/out/turnip-kgsl-26.2.3.tar.gz + .sha256 + build-info.txt. Nothing is installed on the Y700.
set -euo pipefail
TARBALL=/work/mesa-26.2.3.tar.xz
TARBALL_SHA=1628058a8d2c0615975de5a15ab7bbb9638c50000b5bed9456ff423ea034a81f
PREFIX=/home/siwal/y700-gpu/turnip-kgsl-26.2.3     # path the generated ICD json will point to on the Y700
OUT=/work/out
[ "$(uname -m)" = aarch64 ] || { echo "STOP: must build natively on arm64"; exit 1; }
grep -q 'VERSION_ID="26.04"' /etc/os-release || { echo "STOP: expected Ubuntu 26.04 userspace"; exit 1; }
echo "$TARBALL_SHA  $TARBALL" | sha256sum -c -
test ! -e "$OUT" || { echo "STOP: $OUT exists; use a fresh output directory"; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends build-essential meson ninja-build pkg-config python3-mako \
  python3-packaging python3-yaml glslang-tools bison flex zlib1g-dev libzstd-dev libexpat1-dev libarchive-dev libxml2-dev xz-utils ca-certificates file

mkdir -p /build && cd /build
tar xf "$TARBALL"
cd mesa-26.2.3
meson setup build --wrap-mode=nodownload \
  --prefix="$PREFIX" --libdir=lib --buildtype=release -Db_ndebug=true \
  -Dplatforms= -Dvulkan-drivers=freedreno -Dfreedreno-kmds=kgsl -Dgallium-drivers= \
  -Dopengl=false -Dgles1=disabled -Dgles2=disabled -Degl=disabled -Dglx=disabled -Dgbm=disabled \
  -Dllvm=disabled -Dvalgrind=disabled -Dlibunwind=disabled -Dvulkan-layers= -Dtools= -Dbuild-tests=false \
  -Dzstd=enabled -Dexpat=enabled 2>&1 | tee /build/meson-setup.log
grep -E "freedreno-kmds|Vulkan drivers|Platforms" /build/meson-setup.log || true
ninja -C build
DESTDIR=/build/stage ninja -C build install

LIB=/build/stage$PREFIX/lib/libvulkan_freedreno.so
test -f "$LIB" || { echo "STOP: turnip library not produced"; exit 1; }
grep -q '/dev/kgsl-3d0' "$LIB" || { echo "STOP: KGSL backend not compiled in"; exit 1; }
grep -q 'Adreno (TM) 840' "$LIB" || { echo "STOP: A840 entry missing"; exit 1; }
mkdir -p "$OUT"
{
  echo "mesa 26.2.3 upstream, tarball sha256 $TARBALL_SHA"
  echo "built $(date -u +%FT%TZ) on $(uname -m) $(. /etc/os-release; echo "$PRETTY_NAME")"
  gcc --version | head -1; meson --version; glslangValidator --version | head -1
  echo "NEEDED:"; readelf -d "$LIB" | awk '/NEEDED/{print "  "$5}'
  echo "files:"; (cd /build/stage && find . -type f | sort | xargs sha256sum)
} > "$OUT/build-info.txt"
tar -C /build/stage -czf "$OUT/turnip-kgsl-26.2.3.tar.gz" .
(cd "$OUT" && sha256sum turnip-kgsl-26.2.3.tar.gz > turnip-kgsl-26.2.3.tar.gz.sha256)
cat "$OUT/build-info.txt"; cat "$OUT/turnip-kgsl-26.2.3.tar.gz.sha256"
