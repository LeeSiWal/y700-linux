# Steam on Y700 (FEX + turnip) — read-only survey 2026-09-19 (boot 684daae1)

Ready:
- FEX rootfs Ubuntu 24.04 (1.9 GB) has i386 + x86_64 multiarch: libc, GL, X11, Xrandr, dbus, asound, pulse, udev, vulkan
  (i386 libnss3 absent; Steam's own runtime ships it). FEX has 32-bit guest support (ld-linux.so.2, GuestThunks_32).
- 64-bit x86 Vulkan -> host turnip KGSL via thunk works with WSI (vkcube r8 on panel).
- Kernel 4k pages; CPU atomics/lrcpc/lrcpc3/uscat (good for FEX TSO emulation). RAM 10 GiB, disk 436 GB free, /dev/shm 5.5 GB.

Blockers / risks (kernel is Android GKI 6.12, config fixed):
- CONFIG_USER_NS off, CONFIG_PID_NS off: unprivileged bwrap/unshare fail. Steam Linux Runtime (pressure-vessel,
  required by Proton >= 5.13 via "sniper") cannot start its container; CEF sandbox cannot use userns.
  Plan: steam -no-cef-sandbox; run Proton through a custom compat tool (compatibilitytool.vdf without
  require_tool_appid) so games start outside the container; native Linux games via the LD_LIBRARY_PATH (scout) runtime.
- CONFIG_SYSVIPC off, CONFIG_POSIX_MQUEUE off: programs using SysV shm/sem or mqueue fail (risk for some games/anti-cheat;
  Wine itself does not need them).
- 32-bit guests: GuestThunks_32 has no Vulkan thunk -> 32-bit Vulkan/DXVK has no GPU (only lvp). 32-bit GL thunk exists
  but the host has no GL driver for Adreno (possible later: host zink over turnip). => target 64-bit games first.
- Presentation is CPU copy (MESA_VK_WSI_DEBUG=sw), no /dev/dma_heap/system.

Proposed steps (each needs approval where noted):
S-1 download steam launcher deb from repo.steampowered.com, verify signature/hash, extract (no install).
S-2 bootstrap Steam client under FEX inside the presenter desktop (X11/Xwayland), -no-cef-sandbox; log in (user).
S-3 run a free 64-bit native Vulkan game / demo.
S-4 Proton (GE-Proton or Valve Proton) via custom compat tool without pressure-vessel; DXVK 64-bit game.
