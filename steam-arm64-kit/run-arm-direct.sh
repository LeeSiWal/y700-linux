#!/bin/bash
# Valve's native linuxarm64 client started directly: steam.sh (even the updated one) hardcodes PLATFORM=ubuntu12_32,
# while SteamOS on the Frame launches the arm64 binary itself.
R=/home/siwal/y700-steam-arm64/root
export HOME=/home/siwal/y700-steam-arm64/home
export DISPLAY=:0 XDG_RUNTIME_DIR=/run/y700-desktop WAYLAND_DISPLAY=wayland-0
export VK_DRIVER_FILES=/home/siwal/y700-gpu/turnip-kgsl-26.2.3-wsi/share/vulkan/icd.d/freedreno_icd.aarch64.json
export MESA_VK_WSI_DEBUG=sw
export LD_PRELOAD="/home/siwal/y700-steam-arm64/sysvipc/libsysvipc-emu-aarch64.so${LD_PRELOAD:+ $LD_PRELOAD}"
export LD_LIBRARY_PATH="$R/steamrtarm64:$R/linuxarm64:/home/siwal/y700-gpu/extra-libs/x/usr/lib/aarch64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cd "$R"
# -cef-use-vulkan: steamclient.so option "Use vulkan ANGLE backend" - without it CEF renders the whole UI on
# llvmpipe (the client forces LIBGL_KOPPER_DISABLE=true for the webhelper), which made Big Picture crawl.
exec ./steamrtarm64/steam -cef-use-vulkan "$@"
