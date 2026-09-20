# Native arm64 Steam client + Valve's ARM64 Proton on the Y700

Valve ships a **linuxarm64** Steam client and an **ARM64 Proton** (the Steam Frame stack). Both run on this device
without FEX for the client itself: the client is aarch64, and Windows games go through ARM64 wine (arm64ec) with
Proton's bundled FEX only for the game's x86-64 code. This directory is the glue that was missing on a plain
Ubuntu + GKI kernel.

Verified 2026-09-21 on boot `684daae1`: client logs in, store/library/Big Picture render on the GPU, and
**Deep Rock Galactic: Survivor (Windows x86-64, Unity 6, D3D11) plays at 60-78 fps** with the Adreno 840 at ~80 %.

> Valve's files are **not** included here and must not be redistributed. `fetch.py` downloads them from Valve's
> client-update CDN and verifies every component against the sha256 in the manifest.

## Layout

| Path | What |
|---|---|
| `fetch.py` | downloads the components named in `steam_client_linuxarm64` (the client manifest), sha256-checked |
| `run-arm-direct.sh` | starts the native arm64 client directly (see *Why not steam.sh* below) |
| `run-arm-steam.sh` | the earlier route through Valve's `steam.sh`, kept for reference |
| `run-drgs.sh` | starts one game by hand with the environment the client would pass - for debugging without the UI |
| `compat-tool/y700-proton-arm64/` | Steam compatibility tool: Valve's ARM64 Proton, without the container |
| `bin/` | stand-ins the client shells out to: `lsof`, `xz`, `arm64-exec` |
| `sysvipc/` | the aarch64 build of the System V IPC emulation (source shared with `steam-kit/sysvipc`) |

Everything the client writes lives under `root/` (install) and `home/` (its own `HOME`), which are not in this
snapshot: the x86 install in `~/.local/share/Steam` stays untouched, and the two clients never share state.

## What the client needs on this system

1. **`lsof`.** The client validates every WebUI socket with `lsof -P -F upnR -i TCP@127.0.0.1:<port>`
   (`GetIPCConnectionDetails`). Without it every connection is rejected and the UI never appears - the symptom is
   *"Steamwebhelper is not responding"*, which looks nothing like a missing tool. `bin/lsof` is a `/proc`-based
   stand-in for systems where it cannot be installed; the real package is better.
2. **`libgtk2.0-0t64`** and an **`xz`** (`bin/xz` is a small Python stand-in for images without `xz-utils`).
3. **`sysvipc-emu` preloaded.** The kernel has no `CONFIG_SYSVIPC`; Valve's tier0 asserts
   `src/tier0/threadtools.cpp (2526) : Assertion Failed: Function not implemented` without it.
4. **`~/.steam/sdkarm64`** pointing at `root/linuxarm64`. Games are launched through
   `sdkarm64/steam-launch-wrapper`; if the link is missing the launch dies instantly with no message. That directory
   also holds the **native aarch64 `steamclient.so`**, which is what lets ARM64 Proton's `lsteamclient` talk to the
   client - with the x86-64 one, games start and then idle forever in the Steam API.

## Why not `steam.sh`

`steam.sh` hard-codes `PLATFORM=ubuntu12_32` and the bootstrapper rewrites `ubuntu12_32/steam` on every launch, so
the arm64 binary is started directly (`run-arm-direct.sh`) instead of patching Valve files, which Steam undoes.

`-cef-use-vulkan` matters: the client forces `LIBGL_KOPPER_DISABLE=true` on the web helper, so CEF otherwise renders
the whole UI on llvmpipe (measured: web helper 181 % CPU, GPU idle). With the flag, `GL_RENDERER` becomes
`ANGLE (Qualcomm, Vulkan 1.4.354 (Adreno (TM) 840), turnip)` and the renderer drops to ~9 % CPU.

## The compatibility tool

`compat-tool/y700-proton-arm64` runs Valve's Proton 11.0-2c-arm64 **without** the Steam Linux Runtime container
(`require_tool_appid` is dropped: pressure-vessel needs user namespaces, which this kernel does not have). Proton's
own `proton` script still does everything else - prefix from `default_pfx_arm64`, DXVK/vkd3d, the bundled FEX,
`lsteamclient`. Install it by copying the directory into `root/compatibilitytools.d/` and picking it in the game's
*Properties -> Compatibility*.

What the wrapper adds on top of the environment Steam passes:

| Knob | Default | Why |
|---|---|---|
| `Y700_FEX_TSO` | `0` | x86 memory-ordering emulation. On (Valve's default) this game ran single-threaded at 100 % CPU, 2-6 fps, GPU idle, with the wine log full of `Handled unaligned atomic` traps. Off: 60-78 fps, GPU ~80 %. It weakens ordering guarantees, so set `1` if a game misbehaves. |
| `Y700_FEX_MULTIBLOCK` | `1` | larger FEX translation blocks |
| `Y700_FPS_CAP` | `0` (off) | DXVK's limiter derives pacing from swapchain timing, which the software WSI path reports wrongly here: `dxgi.maxFrameRate = 40` measured **1.9 fps**. Cap in-engine instead. |
| `Y700_OSK` | `ignore` | wine's `tabtip.exe` keeps unfolding phosh's on-screen keyboard over the game through the Wayland text-input protocol. The game's app-id (`steam_app_<id>`) is added to phosh's `sm.puri.phosh.osk ignore-activation` list, so the OSK no longer unfolds by itself for that game and stays available everywhere else (including Steam's own text fields). Nothing to restore if the process is killed. `keep` skips it. |
| - | `dxgi.syncInterval = 0` | FIFO/vsync presents fall back to a ~1 s timer on this WSI path (0.8 fps, and the game's loading is gated on it). |
| - | `LD_PRELOAD` rebuilt | Steam prepends its x86 overlay libraries and joins the list without a separator, which swallows the entry after it - including `sysvipc-emu`. |
| - | `VK_DRIVER_FILES`, `MESA_VK_WSI_DEBUG=sw` | turnip on KGSL, software present path (no DRI3/dma-buf here) |

## Gotchas

- **Never run the x86 (FEX) client at the same time.** They have separate `HOME`s but share the emulated System V
  namespace in `/dev/shm/y700-sysv`, and a game's `lsteamclient` will happily connect to the wrong client's IPC port
  and then wait forever.
- The app grid has two entries (`design/desktop-service-20260919/ui/applications/`): *Steam* (this one) and
  *x86 Steam (FEX)*.
- A second client window (Friends, Settings) steals all pointer and touch input from the visible one, because phoc
  never restacks Xwayland windows; `design/desktop-service-20260919/xguard.py` closes such a window after 2 s.
