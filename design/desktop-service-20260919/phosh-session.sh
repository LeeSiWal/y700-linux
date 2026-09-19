#!/bin/sh
# Phosh session inside y700-desktop.service (started by phoc -E, as siwal, under dbus-run-session).
# gnome-session is not used: it needs a systemd user manager, which this session does not have.
squeekboard >/dev/null 2>&1 &
# the reviewed Phosh preferences (lock off at start, no password swipe, no idle blank, show all apps)
gsettings set sm.puri.phosh.lockscreen require-unlock false
gsettings set org.gnome.desktop.screensaver lock-enabled false
gsettings set org.gnome.desktop.session idle-delay 0
gsettings set sm.puri.phosh app-filter-mode "[]"
gsettings set org.gnome.desktop.interface show-battery-percentage true
gsettings set org.gnome.desktop.interface clock-show-weekday true
# audio: PipeWire + WirePlumber (ALSA off, audio/50-y700-no-alsa.conf) + pulse server for x86 games (libpulse under FEX),
# and the "Y700 Speakers" pipe sink (audio/y700-speaker.conf) that speakerd.py (root) plays on the speakers
AUDIOLOG=/home/siwal/y700-agent/audio-session.log
echo "$(date +%T) session audio start" >> "$AUDIOLOG"
pipewire >> "$AUDIOLOG" 2>&1 &
i=0; while [ ! -S "$XDG_RUNTIME_DIR/pipewire-0" ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
wireplumber >> "$AUDIOLOG" 2>&1 &
pipewire-pulse >> "$AUDIOLOG" 2>&1 &
pipewire -c /home/siwal/y700-design/desktop-service-20260919/audio/y700-speaker.conf >> "$AUDIOLOG" 2>&1 &
exec /usr/libexec/phosh --unlocked
