#!/bin/sh
# FEX x86-64 GUI test inside the labwc session (DISPLAY / WAYLAND_DISPLAY are exported by labwc).
export PATH=/home/siwal/y700-fex/root/usr/bin:$PATH
L=/home/siwal/y700-design/desktop-20260919/x86-demo.log
echo "=== $(date) DISPLAY=$DISPLAY WAYLAND_DISPLAY=$WAYLAND_DISPLAY" >> $L
weston-simple-touch &
FEXBash -c 'echo xterm guest: $(uname -m); xterm -fa Monospace -fs 11 -geometry 60x12 -e bash -c "uname -m; head -1 /etc/os-release; echo FEX x86-64 xterm on Y700; exec bash"' >> $L 2>&1 &
FEXBash -c 'glxgears -info 2>&1 | head -40' >> $L 2>&1 &
FEXBash -c 'zenity --info --text="Hello from x86_64 (FEX) on the Lenovo Y700"' >> $L 2>&1 &
