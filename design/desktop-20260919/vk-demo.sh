#!/bin/bash
# x86-64 Vulkan (FEX + Vulkan thunk -> host turnip KGSL with WSI) inside the labwc session.
# A: vkcube-wayland: Wayland WSI needs the FEX WaylandClient thunk too (guest wl_display/wl_surface must be host objects;
#    without it the host WSI dereferences guest pointers -> segfault, run r7). wl_shm path: MESA_VK_WSI_DEBUG=sw.
# B: vkcube (xcb via Xwayland): X11 WSI without DRI3 needs MESA_VK_WSI_DEBUG=sw (r7: "No DRI3 support").
# Verified first on a headless labwc without DRM (A ~61 fps vsync-bound, B ~1300 fps unthrottled; screenshots OK).
export PATH=/home/siwal/y700-fex/root/usr/bin:$PATH
export VK_DRIVER_FILES=/home/siwal/y700-gpu/turnip-kgsl-26.2.3-wsi/share/vulkan/icd.d/freedreno_icd.aarch64.json
export LD_LIBRARY_PATH=/home/siwal/y700-gpu/extra-libs/x/usr/lib/aarch64-linux-gnu
export MESA_VK_WSI_DEBUG=sw
L=/home/siwal/y700-design/desktop-20260919/vk-demo.log
echo "=== $(date) DISPLAY=$DISPLAY WAYLAND_DISPLAY=$WAYLAND_DISPLAY" >> $L
(t0=$(date +%s.%N); FEX_THUNKCONFIG=/home/siwal/.fex-emu/thunks-vkwl.json FEXBash -c 'vkcube-wayland --c 5400' > $L.A 2>&1; echo "A vkcube-wayland rc=$? frames=5400 t0=$t0 t1=$(date +%s.%N)" >> $L) &
sleep 3
(t0=$(date +%s.%N); FEXBash -c 'vkcube --c 100000' > $L.B 2>&1; echo "B vkcube-xcb rc=$? frames=100000 t0=$t0 t1=$(date +%s.%N)" >> $L) &
