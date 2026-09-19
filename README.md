# Lenovo Legion Y700 (TB323FU) — Ubuntu bring-up snapshot (2026-09-19)

Research snapshot of running Ubuntu 26.04 (arm64) on the Lenovo Legion Y700 gen-4 tablet (TB323FU, Snapdragon 8
Elite / Adreno 840) with its stock **Android GKI 6.12 kernel and vendor modules**, a touch desktop, x86 apps through
FEX-Emu, Vulkan through turnip (KGSL), and — as of this snapshot — **the Steam client up to the login window**.

> **Status: experimental, device-specific, not an installer.** Everything here was developed and verified on one
> device. Paths are hard-coded (`/home/siwal/...`), several guards check values of that device, and device-specific
> values (MAC addresses, LAN IPs, SSID, USB NIC names) were replaced by `<MAC>`, `<LAN-IP>`, `<SSID>`, `enx<MAC>` in
> this copy — those spots must be adapted before anything runs. Read the per-directory READMEs first.

> **Risk.** Parts of this work load vendor kernel modules into a running kernel, drive the display controller,
> charging, and write to sysfs as root. Mistakes can hang or damage the device. The original workflow never flashes
> anything except `init_boot_a` with explicit human approval; no slot switching, no bootloader/XBL/ABL changes.

## What works (verified on the device)
| Area | State |
|---|---|
| Boot | Ubuntu userspace on the stock kernel; staged bring-up service loads reviewed module bundles after boot (`design/nextboot-impl`, `bringup.py`) |
| Display | 1904x3040@120 dual-DSI panel driven from user space: DRM owner process + two-plane presenter; live render resolution 50–100 % with plane scaling |
| Desktop | Phosh (phoc) or labwc on a headless wlroots output, shown on the panel by `desktop-service.py` (systemd), rotation, touch calibration (uinput proxy for phoc), brightness, settings app |
| Input | Touch (NVT), keys; on-screen keyboard |
| GPU | turnip (Mesa 26.2.3, KGSL backend, x11/wayland WSI) — native and x86 (FEX Vulkan thunk) vkcube on the panel |
| Network | Wi-Fi (cnss/QCA), USB Ethernet; Bluetooth (custom HCI firmware loader + BlueZ) |
| Audio | Speaker playback through a custom GPR/AudioReach client (no ALSA/PipeWire path yet) |
| Sensors / power | ADC thermals, charger limiter, suspend (freezer / device pass) experiments |
| x86 | FEX-Emu 2609 with an Ubuntu 24.04 x86 RootFS; X11/Wayland GUI apps |
| **Steam** | client updates, logs in, UI renders (software) — see `steam-kit/` |

## Steam on a kernel without user namespaces and System V IPC (`steam-kit/`)
The GKI kernel has `CONFIG_USER_NS`, `CONFIG_PID_NS`, `CONFIG_SYSVIPC` and `CONFIG_POSIX_MQUEUE` disabled. Valve's
files are **not modified** (Steam verifies them and re-extracts/restarts forever otherwise); the gaps are covered from
outside by `steam-kit/y700-steam.sh`:
1. `PRESSURE_VESSEL_BWRAP=steam-kit/fake-bwrap/bwrap` — a namespace-less bubblewrap stand-in (runs the command
   directly) so `steam-runtime-check-requirements` passes.
2. `LD_PRELOAD` of `sysvipc-emu` (`steam-kit/sysvipc/`) — user-space System V semaphores + shared memory (i686 and
   x86-64 builds share one on-disk format in `/dev/shm/y700-sysv`); the 32-bit `steam` binary otherwise asserts
   (`semaphore creation failed`) and stalls. Build with `build-sysvipc.sh` in an arm64 ubuntu:24.04 container
   (i686 + x86-64 cross compilers); `sysv-test.c` covers wait/wake, timeouts, RMID, shm and 32<->64-bit interop.
3. `STEAM_RUNTIME_STEAMRT=steam-kit/no-container-rt` — its `_v2-entry-point` runs `steamwebhelper` directly (no
   pressure-vessel container), with FEX thunks off, `VK_DRIVER_FILES` unset, `--disable-gpu*`, and **without** the
   sysvipc preload (an emulated `shmget` id breaks X11 MIT-SHM in the CEF GPU process -> SIGSEGV).
4. `-no-cef-sandbox -cef-disable-gpu`.
Known open issues: touch input in the Steam (X11/CEF) window, Korean glyphs in CEF until the CJK fonts are visible to
the x86 RootFS, games not tested yet, games inheriting the sysvipc preload may hit the MIT-SHM problem.

## Layout
- `design/nextboot-impl/` — bring-up orchestrator, bundle builder/loader, guards (bootguard, registry), DRM helpers
- `design/desktop-20260919/`, `design/desktop-service-20260919/` — presenter, Wayland client code (screencopy, output
  management), desktop service, Phosh/labwc session, UI (waybar, launchers, display settings app), touch proxy
- `design/turnip-kgsl-20260919/` — turnip KGSL + WSI build scripts
- `design/*-20260919/` — per-subsystem notes and tools (display, touch, wifi, bt, audio, sensors, power, suspend, ...)
- `design/bootfix-20260919/` — systemd units and drop-ins (bring-up service, dbus, Wi-Fi naming, upower without userns)
- `steam-kit/` — Steam launcher wrapper, fake bwrap, container-less runtime entry point, sysvipc-emu sources/tests
- `MANIFEST.sha256` — checksums of every file in this snapshot

## Not included (do not redistribute; extract from your own device / obtain from the vendor)
Vendor kernel modules (`*.ko`), firmware, partition images, Qualcomm/Lenovo audio configuration XML, Steam/Valve files,
FEX binaries and RootFS, Mesa builds, logs, run reports and anything with device identifiers.

## License
Apache License 2.0 (see `LICENSE`) for the files in this repository. Third-party components (Linux kernel, vendor
modules and firmware, Mesa, FEX-Emu, Steam, Phosh, wlroots, ...) are not included and keep their own licenses.
