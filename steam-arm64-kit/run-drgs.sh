#!/bin/bash
# Deep Rock Galactic: Survivor through our ARM64 Proton compat tool, started by hand with the same environment the
# running Steam client passes (so the Steam API still talks to the client) - for fast iteration without the UI.
R=/home/siwal/y700-steam-arm64/root
export HOME=/home/siwal/y700-steam-arm64/home
export DISPLAY=:0 XDG_RUNTIME_DIR=/run/y700-desktop WAYLAND_DISPLAY=wayland-0
export SteamAppId=2321470 SteamGameId=2321470 STEAM_COMPAT_APP_ID=2321470
export STEAM_COMPAT_CLIENT_INSTALL_PATH=$R
export STEAM_COMPAT_DATA_PATH=$R/steamapps/compatdata/2321470
export STEAM_COMPAT_INSTALL_PATH="$R/steamapps/common/Deep Rock Survivor"
export STEAM_COMPAT_LIBRARY_PATHS=$R/steamapps
export STEAM_COMPAT_SHADER_PATH=$R/steamapps/shadercache/2321470
export STEAM_COMPAT_MEDIA_PATH=$R/steamapps/shadercache/2321470/fozmediav1
export STEAM_COMPAT_FLAGS=search-cwd STEAM_COMPAT_PROTON=1
cd "$STEAM_COMPAT_INSTALL_PATH"
exec "$R/compatibilitytools.d/y700-proton-arm64/y700-proton-arm64-run" waitforexitandrun \
     "$STEAM_COMPAT_INSTALL_PATH/DRG Survivor.exe" "$@"
