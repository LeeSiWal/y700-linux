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
exec /usr/libexec/phosh --unlocked
