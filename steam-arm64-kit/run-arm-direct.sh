#!/bin/bash
# Valve's native linuxarm64 client started directly: steam.sh (even the updated one) hardcodes PLATFORM=ubuntu12_32,
# while SteamOS on the Frame launches the arm64 binary itself.
R=/home/siwal/y700-steam-arm64/root

# Only one Steam client at a time. The x86 (FEX) and arm64 clients have separate HOMEs but share the emulated System V
# namespace in /dev/shm/y700-sysv, and a game's lsteamclient connects to whichever client registered the IPC port - a
# game started under the wrong one hangs in the Steam API with no error. Refuse instead of producing that mess.
_other_client() {
  for p in $(pgrep -x steam 2>/dev/null); do
    case "$(readlink -f /proc/$p/cwd 2>/dev/null)" in /home/siwal/y700-steam-arm64*) echo arm64; return;; esac
  done
  pgrep -f '/home/siwal/.local/share/Steam/ubuntu12_32/stea[m]' >/dev/null 2>&1 && echo x86
}
_refuse() {
  gdbus call --session --dest org.freedesktop.Notifications --object-path /org/freedesktop/Notifications \
    --method org.freedesktop.Notifications.Notify "Steam" 0 "dialog-warning" "$1" "$2" "[]" "{}" 8000 >/dev/null 2>&1
  echo "$1: $2" >&2
  exit 1
}
[ "$(_other_client)" = x86 ] && _refuse "arm64 Steam을 시작할 수 없습니다" \
  "x86(FEX) 클라이언트가 실행 중입니다. 먼저 'Steam 종료'로 정리한 뒤 다시 시도하세요."
export HOME=/home/siwal/y700-steam-arm64/home
export DISPLAY=:0 XDG_RUNTIME_DIR=/run/y700-desktop WAYLAND_DISPLAY=wayland-0
export VK_DRIVER_FILES=/home/siwal/y700-gpu/turnip-kgsl-26.2.3-wsi/share/vulkan/icd.d/freedreno_icd.aarch64.json
export MESA_VK_WSI_DEBUG=sw
# /dev is a plain tmpfs here and the desktop service creates the input nodes itself, so udev knows nothing about
# them: with udev discovery on, the client never sees a gamepad (Big Picture then loads basicui_neptune.vdf for
# "no controller" and the pad cannot drive the UI, while games that read evdev directly still work). Without it the
# client scans /dev/input and registers the pad, and Big Picture switches to basicui_gamepad.vdf.
export SDL_JOYSTICK_DISABLE_UDEV=1
export LD_PRELOAD="/home/siwal/y700-steam-arm64/sysvipc/libsysvipc-emu-aarch64.so${LD_PRELOAD:+ $LD_PRELOAD}"
export LD_LIBRARY_PATH="$R/steamrtarm64:$R/linuxarm64:/home/siwal/y700-gpu/extra-libs/x/usr/lib/aarch64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cd "$R"
# -cef-use-vulkan: steamclient.so option "Use vulkan ANGLE backend" - without it CEF renders the whole UI on
# llvmpipe (the client forces LIBGL_KOPPER_DISABLE=true for the webhelper), which made Big Picture crawl.
# -cef-disable-hang-timeouts: when a game saturates the CPU the web helper misses the client's heartbeat and the
# client kills and restarts it ("Restart webhelper process"), which blanks the whole Steam UI for a few seconds.
exec ./steamrtarm64/steam -cef-use-vulkan -cef-disable-hang-timeouts "$@"
