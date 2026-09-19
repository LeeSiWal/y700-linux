# Lenovo Legion Y700 (TB323FU) — Ubuntu bring-up snapshot (2026-09-19)

Research snapshot of running Ubuntu 26.04 (arm64) on the Lenovo Legion Y700 gen-4 tablet (TB323FU, Snapdragon 8
Elite / Adreno 840) with its stock **Android GKI 6.12 kernel and vendor modules**, a touch desktop, x86 apps through
FEX-Emu, Vulkan through turnip (KGSL), and **Steam with a native Linux game (Dead Cells) running GPU-accelerated
(OpenGL via zink over turnip)**.

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
| Input | Touch (NVT), keys; on-screen keyboard; USB/Bluetooth input hot-plug (the desktop service creates `/dev/input` nodes for external devices: `/dev` is a plain tmpfs here, not devtmpfs); Razer Kishi V3 Ultra verified in Dead Cells |
| GPU | turnip (Mesa 26.2.3, KGSL backend, x11/wayland WSI) — native and x86 (FEX Vulkan thunk) vkcube on the panel |
| Network | Wi-Fi (cnss/QCA), USB Ethernet; Bluetooth (custom HCI firmware loader + BlueZ) |
| Audio | Speakers through a custom GPR/AudioReach client (`speakerd.py`) fed by a PipeWire pipe sink; apps and x86 games use PulseAudio/PipeWire as usual. No speaker protection algorithm (fixed −6 dB), no headset/mic yet |
| Sensors / power | ADC thermals, charger limiter, suspend (freezer / device pass) experiments |
| x86 | FEX-Emu 2609 with an Ubuntu 24.04 x86 RootFS; X11/Wayland GUI apps |
| **Steam** | client updates, logs in, library UI renders (software); native Linux games start through the `y700_direct` compatibility tool — see `steam-kit/` |
| **Games** | Dead Cells (x86-64, OpenGL) renders on the Adreno 840: OpenGL -> zink (x86 Mesa) -> FEX Vulkan thunk -> turnip; GPU busy ~75 %; playable with a USB gamepad (Kishi V3 Ultra) **with sound** |

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
5. Games: Steam wraps native games in the Steam Linux Runtime container, which cannot start here either.
   `steam-kit/compat-tool/y700-direct/` is a Steam compatibility tool (copy it to
   `~/.local/share/Steam/compatibilitytools.d/`) that runs the game command directly; map a game to it in
   Properties -> Compatibility, or in `config/config.vdf` `CompatToolMapping` (edit only while Steam is closed).
   The tool sets `LIBGL_KOPPER_DRI2=1 MESA_LOADER_DRIVER_OVERRIDE=zink` (x86 OpenGL on the GPU through the FEX Vulkan
   thunk; `Y700_GL=llvmpipe` to disable), the host turnip ICD and `MESA_VK_WSI_DEBUG=sw`.
   Host requirement found on the way: the WSI turnip build needs `libxcb-keysyms1` installed system-wide (games reset
   `LD_LIBRARY_PATH`, e.g. Dead Cells' `deadcells.sh`, which otherwise hides it from the thunk's host side).
6. Fonts: CEF and x86 apps use the FEX RootFS fontconfig; copy Noto CJK into `~/.local/share/fonts` for Hangul/CJK.
7. `steam-kit/fex-config/`: FEX thunk configs (`thunks-vkwl.json` adds the WaylandClient thunk for Wayland Vulkan
   apps, `thunks-none.json` is used for the web helper).
Known open issues: touch input in the Steam (X11/CEF) window; `steam://rungameid` requests are ignored (click Play);
the hidden main window turns black under Phosh after it is closed; one USB controller switches between the two USB-C ports (the
first-connected port wins: unplug the LAN adapter to use a gamepad on the side port; Wi-Fi then carries the network); Proton (Windows games) not done yet; high SoC temperature under load.

## Game audio (`design/nextboot-impl/speakerd.py`, `design/desktop-service-20260919/audio/`)
The vendor sound card only exposes AudioReach backends; playback needs a DSP graph built from user space. The session
runs PipeWire + WirePlumber (ALSA monitor disabled: `50-y700-no-alsa.conf`) + `pipewire-pulse`, plus a small
`pipewire -c y700-speaker.conf` client whose pipe-tunnel sink "Y700 Speakers" writes s16le/2ch/48 kHz into
`$XDG_RUNTIME_DIR/y700-speaker.fifo`. `speakerd.py` (root) opens the verified speaker graph (shared-memory endpoint ->
PCM converter -> I2S LPAIF primary, data line SD1, 32-bit slots) when data arrives and closes it when idle. x86 games reach
it through the x86 libpulse in the FEX RootFS. Verified: Dead Cells with sound and gamepad (248 s, 0 DSP errors).
Gamepads: the desktop service also creates `/dev/hidrawN` for USB/BT HID devices (Steam Big Picture / Steam Input read
controllers through hidraw); the compatibility tool sets `SDL_JOYSTICK_DISABLE_UDEV=1` so SDL games pick up pads plugged in
later (inotify on `/dev/input`). Kishi V3 Ultra: buttons, sticks, triggers and D-pad work in games; in Steam Big Picture
the D-pad does not react although Steam's SDL mapping is correct (`dpup:h0.1`...) — open. `padproxy.py` (grab the pad and
re-emit it as a uinput Xbox 360 pad) is included but disabled (`REMAPS = {}`): it did not change the Big Picture D-pad.

## Boot order (not yet verified by a reboot)
`y700-bringup.service` runs the stages up to `audio-c` (display, GPU, touch, ADC, Wi-Fi, Bluetooth, ADSP, audio; ~31 min).
The desktop starts as soon as the display owner and GPU are ready and the remaining stages load underneath it: the display
guard accepts a state that differs from the native one only by the registered desktop presenter (`registry.desktop_ok`:
live presenter process of this boot/owner, desktop planes showing only its framebuffers, all other planes, CRTCs,
connectors and positions unchanged), and underruns are counted from each stage's start. Stages watch the management link
(USB Ethernet, else Wi-Fi, else none). `y700-speakerd.service` waits for the audio stages.

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
