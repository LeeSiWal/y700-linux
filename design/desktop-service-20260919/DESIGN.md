# y700-desktop.service — design (2026-09-19, boot 684daae1). Nothing installed yet.

Goal: the labwc desktop (headless output + two-plane presenter, r5..r8) runs for the whole boot, starts after bring-up,
survives SSH logout, stops cleanly (native colour-bar layout restored), recovers after a presenter crash.

## Components
1. Unit /etc/systemd/system/y700-desktop.service (root; approval)
   - After=y700-bringup.service; ConditionPathExists=!/home/siwal/y700-agent/desktop.disable (kill switch)
   - ExecStartPre=+/bin/chmod 0666 /dev/kgsl-3d0   (same change already approved per boot; now automatic)
   - ExecStart=python3 -B .../desktop-service.py
   - RuntimeDirectory=y700-desktop, RuntimeDirectoryPreserve=yes (FB state survives restarts, cleared at reboot)
   - Restart=on-failure, RestartSec=10, StartLimitBurst=3 / StartLimitIntervalSec=600
   - KillMode=mixed (SIGTERM to the presenter only, it stops labwc itself), TimeoutStopSec=30
   - NOT RefuseManualStop: stopping the desktop is safe (the DRM owner lives in y700-bringup.service)
   - WantedBy=multi-user.target (enable = separate approval, after manual tests)
2. desktop-service.py (from presenter-run.py; same reviewed DRM path, no new DRM master, owner fd only borrowed)
   - Wait (<= 15 min) until bringup-<boot8>.json shows owner=ok and gpu=ok for THIS boot, else exit (no restart loop)
   - Guards unchanged: display state native/rebased, owner sole DRM master, card0 via pidfd_getfd
   - Run dir /run/y700-desktop (owned siwal, 0700) instead of /run/user/1000 (linger=no: deleted at SSH logout)
   - Runs until SIGTERM/SIGINT (stop flag -> finally: stop labwc, restore native layout, RMFB + DESTROY_DUMB)
   - Crash recovery: after creating the two FBs write /run/y700-desktop/fbs.json {boot_id, owner pid, fb ids, handles}.
     Next start: if the display is not native AND fbs.json matches this boot+owner AND the planes show exactly those
     FB ids -> commit native layout (TEST_ONLY first), RMFB/DESTROY those ids, delete fbs.json. Any other non-native
     state -> refuse (unknown owner of the screen). (Our FBs live on the owner's open file, so a SIGKILLed presenter
     leaves them on screen until removed.)
   - Status every 60 s to journal + /run/y700-desktop/status.json: frames, fps, copy ms, underrun delta, new kernel
     fault lines (bootguard FAULT regex). Underruns / faults are logged as WARNING, not auto-acted on.
   - labwc without -d (r7 debug log: 244 scan-out lines per exit); stdout/stderr to the journal.
   - flock /run/y700-desktop/lock; presenter-run.py/desktop-run.py one-shot tools refuse while it is held.
3. Session config (labwc -C desktop-service-20260919/labwc-config)
   - environment: PATH += FEX bin; VK_DRIVER_FILES = turnip-kgsl-26.2.3-wsi ICD; LD_LIBRARY_PATH = extra-libs (only
     libxcb-keysyms); MESA_VK_WSI_DEBUG=sw
   - autostart: empty at first (no keyboard yet; apps are started remotely)
4. y700-run helper (siwal, over SSH): runs a command inside the session
   (XDG_RUNTIME_DIR=/run/y700-desktop WAYLAND_DISPLAY=wayland-0 DISPLAY=:0 + the session env), detached, log to
   ~/y700-agent/y700-run-<boot8>.log. Options: --x86 (FEXBash -c), --wl-thunk (FEX_THUNKCONFIG=thunks-vkwl.json).

## Interactions
- Suspend tools (S-1/S-2) and display one-shots see a non-native state while the desktop is up and refuse: stop the
  desktop first (systemctl stop y700-desktop).
- bringup.py unchanged; y700-bringup.service keeps owning the DRM master for the boot.

## Test plan
T-1 host: fbs.json recovery decision table, stop flag, bringup wait (fake registry) — unit tests.
T-2 device: sudo systemctl start (not enabled) -> desktop visible; y700-run weston-terminal / --x86 vkcube; stop -> colour bars.
T-3 device: kill -9 the presenter -> Restart recovers leftover FBs -> desktop back, no faults.
T-4 SSH logout/login while running -> session unaffected.
T-5 1 h run: CPU %, temperature (adc), underruns, faults.
T-6 enable + reboot -> desktop appears after bring-up without any command.

## Approvals needed (in order)
A-1 install the unit file (not enabled)            -> T-2..T-5
A-2 systemctl enable y700-desktop                   -> T-6
Later, separate: on-screen keyboard / launcher packages (apt), Steam.

## Implementation (2026-09-19)
- desktop-service.py (sha 868967f7…): as designed; logs also to ~/y700-agent/desktop-<boot8>.log and
  desktop-status-<boot8>.json (siwal cannot read the journal). kgsl chmod is done in the script after bring-up (the node
  appears with the gpu stage), not in ExecStartPre.
- desktop-20260919/wlcapture.py: a copy request that times out stays pending and is resumed (idle screen no longer piles up
  one pending copy per second). Verified on a headless labwc: 3 timeouts -> 1 request; damage -> frame.
- labwc-config/{environment,autostart}; y700-run (sha 5fd02150…); y700-desktop.service (sha e8e92b1e…).
- test_desktop_service.py: 19 tests OK (recovery decision table, bring-up readiness incl. the real bringup-684daae1.json).
- Update: add_fb_noleak (GETFB2 readback handle closed; drmkms.add_fb leaks one GEM handle per FB when called by root on
  the master file: this boot ~10 x 23 MB stay pinned until reboot); rotation via desktop.json (default 90: output
  transform + generated rc.xml with the libinput touch calibration matrix); 21 tests OK.
- Panel-verified 2026-09-19 21:20: rotation 270 (landscape as the user holds it); touch matrix 270 = '0 -1 1 1 0 0'
  (first derivation swapped 90/270 -> touch 180 degrees off); mouseEmulation="no": with "yes" taps on GTK layer-shell
  surfaces (waybar) did nothing, native wl_touch works (GTK synthesizes the click; Xwayland emulates the pointer for X11).
  UI: swaybg, waybar (Apps/Keyboard/Terminal/x86 Term, taskbar, clock, CPU/MEM/WiFi/BAT), mako, wvkbd; labwc runs under
  dbus-run-session (waybar/mako need a session bus); labwc process group is stopped as a whole.
- 21:37: apps installed by the user (foot, thunar, mousepad, ristretto, gnome-calculator, gnome-system-monitor, epiphany,
  mpv, nm-connection-editor, htop; gnome-control-center was already present). GNOME Settings refuses non-GNOME desktops ->
  launched with XDG_CURRENT_DESKTOP=GNOME (ui/applications/org.gnome.Settings.desktop override); stray launchers hidden via
  NoDisplay overrides (our ui dir is first in XDG_DATA_DIRS). Bar: Apps Keyboard Terminal(foot) Files Web Settings x86Term.
  Root-owned dconf/ and dbus-1/ (21:14, origin unknown) in /run/y700-desktop blocked dconf -> moved aside live;
  desktop-service.py fix_run_ownership() lchowns everything under RUN to siwal at each start.
- 21:50 panel-verified: control socket RUN/ctl.sock (siwal only, SO_PEERCRED): brightness up/down/get/0..100 (min 200/4095)
  and rotate next|0|90|180|270 (live: output transform + rc.xml + labwc SIGHUP; saved to desktop.json). Bar buttons:
  Wi-Fi (nm-connection-editor), BT (blueman-manager), - Light% +, Rotate. y700-ctl client. 27 tests OK.
  Touch matrix only takes effect after a labwc reconfigure -> the service SIGHUPs labwc ~1 s after start.
  Installed by the user: bluez + blueman (bluetoothd inactive: no hci0 until bt-a/bt-b/btkeeper), polkit rule
  /etc/polkit-1/rules.d/50-y700-desktop.rules (nmcli permissions now yes for scan/modify/network-control).
  Root-owned dconf/dbus-1/pulse reappeared in RUN (probably sudo from a desktop terminal); fixed at each start.
- 21:53 panel-verified: two bars (top controls incl. Rotate at left, bottom apps); all rotations usable, buttons visible.
- 22:10 Phosh shell (user choice): desktop.json {"shell": "phosh"|"labwc"}. phoc -S -C RUN/phoc/phoc.ini (HEADLESS-1
  1904x3040 scale 2, xwayland) -E phosh-session.sh (squeekboard; gsettings: require-unlock false, lock-enabled false,
  idle-delay 0, app-filter-mode []; phosh --unlocked). gnome-session not used (needs a systemd user manager). Headless
  verified: unlocked app grid with all apps incl. x86 entries, quick settings (Wi-Fi/BT/rotation/volume), foot + OSK.
  Rotation under phosh: output transform only (touch mapping to be verified on the panel). 31 tests OK.
- 22:14 Epiphany: WebKitGTK sandbox needs bwrap user namespaces (kernel has no USER_NS) -> abort; launcher override runs it with WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1 (web content not sandboxed).
- 22:25 touch under phosh: phoc has no calibration setting and does not rotate touch for the headless output ->
  touchproxy.py: root reads the real NVT touchscreen (MT protocol B) and re-emits through uinput 'y700-rotated-touch'
  with the normalized rotation matrix verified under labwc; the seat shim hands out only the uinput device
  (desktop.json touch_proxy, default on for phosh). Rotation changes from our ctl or from the shell's own rotate tile
  (wlr-output-management head transform event) update the proxy matrix and the saved default. test_touchproxy.py 7 OK.
- 22:35 phosh touch 180 deg off at 270 with the labwc matrix -> per-rotation touch_offsets (default phosh {0:0,90:180,180:0,270:180}), live 'y700-ctl touch <deg>' for the current rotation.
- 22:37 panel-verified: phosh rotation 270 + touch proxy offset 180 -> touch correct (saved touch_offsets {'270': 180}).
- 22:40 fonts-noto-cjk installed by the user: Hangul renders (foot capture). LANG=C.UTF-8 added to the session environment (y700-run apps had LANG unset).
- 22:45 Epiphany blank/grey pages: GTK4 on turnip cannot allocate exportable (dma-buf) memory (ION_IOC_ALLOC, no /dev/dma_heap) -> WEBKIT_DISABLE_DMABUF_RENDERER=1 in the launcher. Headless: local UTF-8 page + ko.wikipedia render with Hangul (Noto CJK KR via ~/.config/fontconfig/fonts.conf aliases for Malgun/Gulim/Dotum/Batang/Apple SD Gothic Neo).
- 22:55 heat: 100-105 C with Epiphany (WebKitWebProcess 312 % CPU, software rendering at 3040x1904). Render resolution
  (desktop.json resolution 100/90/80/67/50): framebuffers + compositor output at the lower size, logical desktop stays
  952x1520 (scale = w/952), the two plane scalers (scaler_v2 present on 95/128) upscale to the panel. Live switch
  'y700-ctl resolution <pct>': TEST_ONLY first, compositor mode, old FBs freed after the first commit on the new ones,
  underruns within 6 s -> automatic revert, kept value saved. Settings app ui/y700-display.py (resolution, rotation,
  brightness). Phosh bar: show-battery-percentage + clock-show-weekday. Not yet run on the panel.
