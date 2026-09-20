#!/bin/bash
# Start Steam (x86-64 client under FEX) on the Y700. Valve's files are not modified (Steam verifies them and would
# re-extract + restart forever); the kernel gaps are covered from the outside:
#  - no CONFIG_USER_NS: steam-runtime-check-requirements tests bwrap -> PRESSURE_VESSEL_BWRAP points to a namespace-less
#    stand-in (~/y700-steam/fake-bwrap/bwrap) that runs the command directly
#  - no CONFIG_SYSVIPC: LD_PRELOAD of the user-space System V semaphores/shared memory (i686 + x86-64 builds)

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
[ "$(_other_client)" = arm64 ] && _refuse "x86 Steam을 시작할 수 없습니다" \
  "네이티브 arm64 클라이언트가 실행 중입니다. 먼저 'Steam 종료'로 정리한 뒤 다시 시도하세요."

export PRESSURE_VESSEL_BWRAP=/home/siwal/y700-steam/fake-bwrap/bwrap
export Y700_FAKE_BWRAP_LOG=/home/siwal/y700-agent/steam-fake-bwrap.log
#  - steamwebhelper always starts through the steamrt runtime entry point (a pressure-vessel container): point
#    STEAM_RUNTIME_STEAMRT at a stand-in whose _v2-entry-point runs the web helper directly
export STEAM_RUNTIME_STEAMRT=/home/siwal/y700-steam/no-container-rt
# The kernel also lacks CONFIG_SYSVIPC (semget/shmget -> ENOSYS; the 32-bit steam binary asserts and stalls):
# user-space System V semaphores + shared memory, i686 and x86-64 builds sharing /dev/shm/y700-sysv.
# Each process loads the build of its own ELF class; the other one is skipped by ld.so with a warning.
L=/home/siwal/y700-steam/sysvipc/lib
export LD_PRELOAD="$L/i386-linux-gnu/libsysvipc-emu.so $L/x86_64-linux-gnu/libsysvipc-emu.so${LD_PRELOAD:+ $LD_PRELOAD}"
exec /home/siwal/y700-steam/launcher/usr/lib/steam/bin_steam.sh -no-cef-sandbox -cef-disable-gpu "$@"
