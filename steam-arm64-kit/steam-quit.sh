#!/bin/bash
# Stop Steam from the app grid: there is no tray here and the client's own Exit is several taps deep (and unreachable
# while a game has the screen). Order: the running game first, then a graceful client shutdown, then signals.
export HOME=/home/siwal/y700-steam-arm64/home
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/y700-desktop}
export DISPLAY=${DISPLAY:-:0}
R=/home/siwal/y700-steam-arm64/root
PROTON=/home/siwal/.local/share/Steam/ubuntu12_32/steamapps/content/app_4628740/depot_4628741
LOG=/home/siwal/y700-agent/steam-quit.log

log() { echo "$(date +%T) $*" >> "$LOG"; }
notify() {
  gdbus call --session --dest org.freedesktop.Notifications --object-path /org/freedesktop/Notifications \
    --method org.freedesktop.Notifications.Notify "Steam" 0 "application-exit" "$1" "$2" "[]" "{}" 5000 >/dev/null 2>&1
}
alive() { pgrep -x steam >/dev/null || pgrep -f 'steamrtarm64/steamwebhelpe[r]' >/dev/null; }

log "quit requested"
notify "Steam 종료 중" "게임과 클라이언트를 정리하고 있습니다."

# 1. any game running under our ARM64 Proton prefixes: ask wine to end the session
for pfx in "$R"/steamapps/compatdata/*/pfx; do
  [ -d "$pfx" ] || continue
  pgrep -f "$(basename "$(dirname "$pfx")")" >/dev/null 2>&1 || true
  WINEPREFIX="$pfx" timeout 15 "$PROTON/files/bin-arm64/wineserver" -k >/dev/null 2>&1
done

# 2. the client's own shutdown (writes config, closes the session cleanly)
if pgrep -x steam >/dev/null; then
  timeout 25 /home/siwal/y700-steam-arm64/run-arm-direct.sh -shutdown >/dev/null 2>&1
  for _ in $(seq 1 15); do alive || break; sleep 1; done
fi

# 3. it does not always take: TERM, then the web helpers it leaves behind
if alive; then
  log "graceful shutdown did not finish, sending TERM"
  for p in $(pgrep -x steam); do kill "$p" 2>/dev/null; done
  for _ in $(seq 1 10); do alive || break; sleep 1; done
  for p in $(pgrep -f 'steamrtarm64/steamwebhelpe[r]'); do kill "$p" 2>/dev/null; done
  sleep 2
  for p in $(pgrep -f 'steamrtarm64/steamwebhelpe[r]'); do kill -9 "$p" 2>/dev/null; done
fi

# 4. the x86 client, if that is the one running
for p in $(pgrep -f '/home/siwal/.local/share/Steam/ubuntu12_32/stea[m]'); do kill "$p" 2>/dev/null; done

if alive; then
  log "still running after all steps"
  notify "Steam 종료 실패" "프로세스가 남아 있습니다 (로그: steam-quit.log)."
else
  log "stopped"
  notify "Steam 종료됨" "클라이언트와 게임이 모두 정리되었습니다."
fi
