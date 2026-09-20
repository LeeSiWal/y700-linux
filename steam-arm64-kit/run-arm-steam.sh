#!/bin/bash
# Valve's native linuxarm64 Steam client (client-update manifest steam_client_linuxarm64), kept apart from the x86 install:
# its own HOME so ~/.steam and ~/.local/share/Steam of the FEX x86 client stay untouched.
export HOME=/home/siwal/y700-steam-arm64/home
export DISPLAY=:0 XDG_RUNTIME_DIR=/run/y700-desktop WAYLAND_DISPLAY=wayland-0
export VK_DRIVER_FILES=/home/siwal/y700-gpu/turnip-kgsl-26.2.3-wsi/share/vulkan/icd.d/freedreno_icd.aarch64.json
export MESA_VK_WSI_DEBUG=sw
export LD_LIBRARY_PATH=/home/siwal/y700-gpu/extra-libs/x/usr/lib/aarch64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export PATH=/home/siwal/y700-steam-arm64/bin:$PATH
# this kernel has no CONFIG_SYSVIPC: the client's semget/semop/shmget need the user-space emulation (aarch64 build of
# y700-steam/sysvipc/sysvipc-emu.c, same on-disk format in /dev/shm/y700-sysv as the x86 builds)
export LD_PRELOAD="/home/siwal/y700-steam-arm64/sysvipc/libsysvipc-emu-aarch64.so${LD_PRELOAD:+ $LD_PRELOAD}"
export STEAM_RUNTIME_SCOUT=/home/siwal/y700-steam-arm64/root/ubuntu12_32/steam-runtime
cd /home/siwal/y700-steam-arm64/root
exec ./steam.sh "$@"
