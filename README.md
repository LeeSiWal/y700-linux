# Lenovo Legion Y700 (TB323FU) — Ubuntu bring-up snapshot (2026-09-19)

Research snapshot of running Ubuntu 26.04 (arm64) on the Lenovo Legion Y700 gen-4 tablet (TB323FU, Snapdragon 8
Elite / Adreno 840) with its stock **Android GKI 6.12 kernel and vendor modules**, a touch desktop, x86 apps through
FEX-Emu, Vulkan through turnip (KGSL), and **Steam running games**: a native Linux game (Dead Cells, OpenGL via zink
over turnip) through the x86 client, and a **Windows x86-64 game (Deep Rock Galactic: Survivor) through Valve's
native arm64 Steam client and ARM64 Proton** (ARM64 wine + bundled FEX + DXVK on turnip).

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
| Display | 1904x3040@120 dual-DSI panel driven from user space: DRM owner process + two-plane presenter; live render resolution 50–100 % with plane scaling; the scanout buffers come from the vendor dma-buf heap and are handed to `wl_shm`, so the compositor paints straight into them (no per-frame copy); display sleep turns the backlight and the CRTC off (4.16 W → 1.53 W) and any touch wakes it |
| Desktop | Phosh (phoc) or labwc on a headless wlroots output, shown on the panel by `desktop-service.py` (systemd), rotation, touch calibration (uinput proxy for phoc), brightness, settings app |
| Input | Touch (NVT), keys; on-screen keyboard; an X input guard closes a hidden second window of an X app (Steam's Friends/Settings), which otherwise swallows every tap because phoc never restacks Xwayland windows; USB/Bluetooth input hot-plug (the desktop service creates `/dev/input` nodes for external devices: `/dev` is a plain tmpfs here, not devtmpfs); Razer Kishi V3 Ultra verified in Dead Cells |
| GPU | turnip (Mesa 26.2.3, KGSL backend, x11/wayland WSI) — native and x86 (FEX Vulkan thunk) vkcube on the panel |
| Network | Wi-Fi (cnss/QCA), USB Ethernet; Bluetooth (custom HCI firmware loader + BlueZ) |
| Audio | Speakers through a custom GPR/AudioReach client (`speakerd.py`) fed by a PipeWire pipe sink; apps and x86 games use PulseAudio/PipeWire as usual. No speaker protection algorithm (fixed −6 dB), no headset/mic yet |
| Sensors / power | ADC thermals, charger limiter, suspend (freezer / device pass) experiments |
| x86 | FEX-Emu 2609 with an Ubuntu 24.04 x86 RootFS; X11/Wayland GUI apps |
| **Steam (x86)** | client updates, logs in, library UI renders (software); native Linux games start through the `y700_direct` compatibility tool — see `steam-kit/` |
| **Steam (arm64)** | Valve's native linuxarm64 client: login, store, library and Big Picture, CEF UI on the GPU (`-cef-use-vulkan`, ANGLE on turnip) — see `steam-arm64-kit/` |
| **Games (Linux)** | Dead Cells (x86-64, OpenGL) renders on the Adreno 840: OpenGL -> zink (x86 Mesa) -> FEX Vulkan thunk -> turnip; GPU busy ~75 %; playable with a USB gamepad (Kishi V3 Ultra) **with sound** |
| **Games (Windows)** | Deep Rock Galactic: Survivor (Unity 6, D3D11) through Valve's ARM64 Proton without the runtime container: 60-78 fps, GPU ~80 %, Steam API/cloud working — see `steam-arm64-kit/` |

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

## Thermal control (`design/thermal-20260920/`)
The kernel's CPU thermal zones have passive trips but no cooling device bound (Android's vendor thermal-engine does that
work there), so nothing throttles below the 125 C "hot" trip and the prime cores go 66 -> 100 C in two seconds at 4.6 GHz.
`thermald.py` (systemd unit included) reads the hottest zone per domain every second and moves that domain's frequency cap
(`policy0`/`policy6` `scaling_max_freq`, `kgsl-3d0/max_clock_mhz`) one step at a time, with profiles quiet/balanced/performance
selected from the settings app; the original maxima are restored on exit. Measured with the 3.4 GHz balanced cap: 68-78 C
instead of 86-102 C under the same load, with no throughput loss.

## Windows games on ARM (`steam-arm64-kit/`)
Valve's **native linuxarm64 Steam client** and **Proton 11.0 (ARM64)** (the Steam Frame stack) both run here, and a
Windows x86-64 game plays at 60-78 fps. The client is fetched from Valve's client-update CDN (sha256-verified, not
redistributed); Proton ARM64 and its tools come from the Steam console (`download_depot 4628740 4628741 <manifest>`;
Proton ARM64 4628740, FEX 3127680, Steam Linux Runtime 4.0 arm64 4185400 — the runtime container itself is unusable
without user namespaces, so the compatibility tool drops `require_tool_appid` and calls Proton's own `proton` script).

What it took, beyond the container:
- `lsof` — the client validates each WebUI socket with it; without it every connection is rejected and the UI never
  appears ("Steamwebhelper is not responding").
- `~/.steam/sdkarm64` — holds `steam-launch-wrapper` (a game launch dies instantly without it) and the **native
  aarch64 `steamclient.so`**, which is what finally let ARM64 `lsteamclient` talk to the client. With the x86-64 one,
  games start and idle forever in the Steam API — the wall this hit in the previous snapshot.
- `sysvipc-emu` preloaded into the game — Valve's tier0 asserts `threadtools.cpp (2526): Function not implemented`
  without System V semaphores; Steam's own `LD_PRELOAD` handling drops the entry, so the tool rebuilds the list.
- FEX tuning: with Valve's defaults the engine ran single-threaded at 100 % CPU with the GPU idle (2-6 fps) and the
  wine log full of `Handled unaligned atomic` traps. `STEAM_FEX_TSOENABLED=0` + `STEAM_FEX_MULTIBLOCK=1` gives
  60-78 fps with the GPU at ~80 % (weaker memory ordering: revertible per game).
- Presentation: FIFO/vsync presents fall back to a ~1 s timer on this software WSI path (0.8 fps, with the game's
  loading gated on it), and DXVK's own frame limiter inherits the same bad timing (`maxFrameRate = 40` measured
  1.9 fps). The tool runs with `dxgi.syncInterval = 0` and leaves pacing to the panel presenter.

The x86 (FEX) client and the arm64 client must never run at the same time: separate `HOME`s, but one emulated System V
namespace, and a game's `lsteamclient` will connect to the wrong client's IPC port and hang.

## Layout
- `design/nextboot-impl/` — bring-up orchestrator, bundle builder/loader, guards (bootguard, registry), DRM helpers
- `design/desktop-20260919/`, `design/desktop-service-20260919/` — presenter, Wayland client code (screencopy, output
  management), desktop service, Phosh/labwc session, UI (waybar, launchers, display settings app), touch proxy
- `design/turnip-kgsl-20260919/` — turnip KGSL + WSI build scripts
- `design/*-20260919/` — per-subsystem notes and tools (display, touch, wifi, bt, audio, sensors, power, suspend, ...)
- `design/bootfix-20260919/` — systemd units and drop-ins (bring-up service, dbus, Wi-Fi naming, upower without userns)
- `steam-kit/` — Steam launcher wrapper, fake bwrap, container-less runtime entry point, sysvipc-emu sources/tests
- `steam-arm64-kit/` — native arm64 Steam client launcher, ARM64 Proton compatibility tool, client stand-ins (`lsof`, `xz`)
- `MANIFEST.sha256` — checksums of every file in this snapshot

## Not included (do not redistribute; extract from your own device / obtain from the vendor)
Vendor kernel modules (`*.ko`), firmware, partition images, Qualcomm/Lenovo audio configuration XML, Steam/Valve files,
FEX binaries and RootFS, Mesa builds, logs, run reports and anything with device identifiers.

## License
Apache License 2.0 (see `LICENSE`) for the files in this repository. Third-party components (Linux kernel, vendor
modules and firmware, Mesa, FEX-Emu, Steam, Phosh, wlroots, ...) are not included and keep their own licenses.
