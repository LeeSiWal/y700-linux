#!/bin/bash
# Start Steam (x86-64 client under FEX) on the Y700. Valve's files are not modified (Steam verifies them and would
# re-extract + restart forever); the kernel gaps are covered from the outside:
#  - no CONFIG_USER_NS: steam-runtime-check-requirements tests bwrap -> PRESSURE_VESSEL_BWRAP points to a namespace-less
#    stand-in (~/y700-steam/fake-bwrap/bwrap) that runs the command directly
#  - no CONFIG_SYSVIPC: LD_PRELOAD of the user-space System V semaphores/shared memory (i686 + x86-64 builds)
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
