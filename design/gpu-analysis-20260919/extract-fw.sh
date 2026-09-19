#!/bin/bash
# Read-only extraction of the four gen8_2_1 firmware files from an ext4 vendor image.
# debugfs without -w opens the image read-only; nothing is mounted or installed.
set -euo pipefail
IMG=${1:?usage: extract-fw.sh vendor-probe.img}
OUT=$(dirname "$0")/firmware
test ! -e "$OUT" || { echo "STOP: $OUT exists; inspect instead of overwriting"; exit 1; }
sha256sum "$IMG"
dumpe2fs -h "$IMG" 2>/dev/null | grep -E '^(Filesystem volume name|Filesystem UUID|Block count|Block size):'
echo "== firmware candidates in image =="
debugfs -R 'ls -p /firmware' "$IMG" 2>/dev/null | awk -F/ 'NF>5 && $6 ~ /^(gen8|a8|gmu_gen8)/ {print $6, $7}' || true
mkdir "$OUT"
for f in gen80200_sqe.fw gen80200_aqe.fw gen80200_gmu.bin gen80200_zap.mbn; do
  debugfs -R "dump -p /firmware/$f $OUT/$f" "$IMG" 2>/dev/null
  if [ -s "$OUT/$f" ]; then echo "OK $f $(stat -c %s "$OUT/$f") $(sha256sum "$OUT/$f" | cut -c1-64)"; else echo "MISSING /firmware/$f"; rm -f "$OUT/$f"; fi
done
