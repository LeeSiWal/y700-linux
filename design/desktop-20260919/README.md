# Weston desktop design (boot 684daae1, read-only) [로컬 분석/읽기 전용]

## Facts
- weston 14.0.2 in resolute; --no-install-recommends pulls 171 packages (cairo/pango/fonts/glycin/libva/...), full 239.
- DRM: card0 driver msm_drm (vendor SDE), renderD128 present in sysfs (no /dev node), dumb buffers yes, atomic yes.
  Mesa has no GBM/EGL driver for "msm_drm" -> Weston must use the PIXMAN renderer (CPU) at first.
- Seat: logind seat0 CanGraphical=yes, CanTTY=no (no VTs in this kernel) -> no VT switching; fine for libseat.
- /dev is the Bootstrap tmpfs: udev does not create nodes. /dev/dri/card0 exists (made by owner stage, 0600 root);
  /dev/input/eventN do NOT exist (touch stage probes via sysfs; touch-events.py made event1 temporarily).
- The owner (display-owner.py) holds the only DRM file (master) for the whole boot and just sleeps; guards require it.
  borrow_owner.py already dups that file with pidfd_getfd (same drm_file => already master, no SET/DROP_MASTER).
- Current scan-out: CRTC 205, mode 1904x3040@120, TWO primary planes 95 (left) / 128 (right) on one FB.

## Design: seat shim + Weston as the user
1. y700-seatd-shim (root, Python): speaks the seatd protocol (ref/include_protocol.h: header {u16 opcode, u16 size};
   CLIENT_OPEN_SEAT/OPEN_DEVICE/CLOSE_DEVICE/DISABLE_SEAT/CLOSE_SEAT/PING; SERVER_SEAT_OPENED/DEVICE_OPENED(+fd via
   SCM_RIGHTS)/ENABLE_SEAT/PONG/ERROR). OPEN_DEVICE:
   - /dev/dri/card0 -> borrow_fd() of the owner's master file (the owner keeps its own fd; nothing is closed on it)
   - /dev/input/eventN -> only reviewed input devices (NVT touch, gpio-keys); node created with mknod if missing
   - anything else -> ERROR (EPERM). Socket in /run/user/1000/y700-seat.sock, only uid 1000 accepted (SO_PEERCRED).
2. Weston runs as siwal: LIBSEAT_BACKEND=seatd SEATD_SOCK=... weston --backend=drm --renderer=pixman
   (desktop-shell, clock/launcher; weston-terminal needs a keyboard -> SSH/BT keyboard later).
3. Exit/restore: when Weston exits (or is killed), the shim borrows the owner fd again and TEST_ONLY + commits the
   registered native state (FB 335 on planes 95/128) -> colour bars back; the owner never loses its fd.

## Risks / open questions
- Weston drives ONE primary plane per output; today the panel is fed by two planes (95/128, dual-DSI halves).
  Whether a single 1904-wide plane is accepted by SDE (source split) is unknown -> step D-1 TEST_ONLY probe.
- Guards: while Weston owns the picture, the native-state check of later bundles (BT/ADSP/audio) fails ->
  run those before the desktop, or add a reviewed "desktop mode" to registry.display_ok later.
- Pixman at 1904x3040@120 is CPU heavy (Oryon is fast; acceptable for a first desktop). GPU path later (Vulkan).
- Weston shares the owner's drm_file: DRM events go to whoever reads it (the owner never reads) -> OK.

## Steps
- D-0 [apt approval] sudo apt-get install --no-install-recommends weston
- D-1 [sudo one-shot, no visible change] TEST_ONLY atomic probe on the borrowed fd: 1904x3040 XR24 dumb FB on plane 95
  alone (128 disabled), and on plane 128 alone; records which Weston-style layout the kernel accepts.
- D-2 [sudo one-shot] shim + Weston for 120 s with visual/touch check, then restore native state (user watches).
- D-3 desktop mode for guards + bringup integration (after D-2 works).

## D-1 result (boot 684daae1) [API 성공]
- TEST_ONLY native PASS, left_full (plane 95 alone, 1904x3040) PASS, right_full (plane 128 alone) PASS.
- Display state hash + underruns identical before/after; only the borrowed duplicate fd was closed.
- => a single primary plane can scan out the whole frame: Weston's one-plane-per-output model is accepted by SDE.

## D-2 prepared (weston 14.0.2 installed after apt-get update; bubblewrap 404 on the stale index first)
- desktop-run.py: seat shim + Weston (siwal) for SECS (default 120) + restore of the native picture + report
  desktop-run-<boot8>.json (one per boot), weston-<boot8>.log. weston.ini: pixman, idle-time=0, desktop-shell panel/clock.
- Shim protocol test (no root, fake fds): OPEN_SEAT -> SEAT_OPENED("seat0")+ENABLE_SEAT, PING->PONG,
  OPEN_DEVICE card0 -> DEVICE_OPENED id + 1 fd (SCM_RIGHTS), unknown path -> ERROR(EPERM), CLOSE_DEVICE/CLOSE_SEAT acks.

## D-2 r1 result + format probe (boot 684daae1)
- r1: weston aborted at start: drm-formats.c:131 assertion (duplicate format) right after "Using Pixman renderer";
  no commit was made, DRM state stayed native, 0 new kernel faults. Restore helper bug (fb_ids in drmabi) fixed; runs
  are now tagged (Y700_RUN_TAG, default r1; r1 record kept as desktop-run-684daae1-r1.json).
- format-probe: all 20 planes carry duplicate formats in BOTH the GETPLANE list and IN_FORMATS (1 modifier):
  planes 95/128/132/136: AB24 x3, NV12 x3, P210 x2; 140/144/148...: AB24 x3. No writeback format blobs.
  => weston 14 cannot start on this vendor driver without patching (WESTON_DISABLE_GBM_MODIFIERS would not help).
- Plan A: wlroots compositor (wlr_drm_format_set_add merges duplicates): labwc (34 pkgs) or sway (11 pkgs), same shim.

## D-2 r2 (labwc 0.9.3 / wlroots 0.19.2), boot 684daae1
- labwc ran the full 120 s: pixman renderer, drm dumb allocator, DSI-1 modeset 1904x3040@120 OK (wlroots also tried
  Virtual-1/2 and failed their tests harmlessly), software cursor, demo clients started. Restore TEST_ONLY+commit OK,
  DRM state back to native, 0 kernel faults.
- Inputs denied: the shim made nodes in /run/y700-seat but /run is nodev -> EACCES. Fixed: nodes in /dev/input
  (root 0600, only if missing, removed afterwards). Also "Unable to clone DRM fd" (drm-lease read-only reopen) is harmless.

## D-2 r3 (labwc, input nodes in /dev/input), boot 684daae1
- card0 + event0/1/2 opened; libinput added NVT touch (pen ignored: "missing tablet capabilities: resolution");
  user: touch works, but only a black corner of the desktop was visible. Restore OK, DRM state native, 0 faults.
- Cause (inferred): wlroots drives one primary plane; the owner's second plane (128, zpos 1, colour bars right half)
  stayed active and overlapped. Fix for r4: single_plane() before start = native FB on plane 95 full width, plane 128
  off (D-1 left_full), plus a DRM state snapshot 10 s into the run (drm-state-during-<boot>-<tag>.txt).

## D-3a underrun comparison (boot 684daae1) [API + 물리 확인]
- 10 s each, native colour-bar FB: A two planes 0/0 underruns, B single plane 8/8, C two planes restored 0/0.
- User photo in B: only the top ~5% of the bars drawn, rest white (the native pattern is full-height bars + checker).
  r4 labwc looked the same (dark top band = labwc's black, rest white). => 1904x3040@120 (915 MHz pixel clock, dual
  DSI, 2 LM + 2 DSC, DT timing@0 is the only mode) needs TWO SSPPs (planes 95+128 halves). Single-plane compositors
  (weston/labwc/sway) cannot drive this panel directly.

## Presenter design (two-plane relay)
- labwc on a HEADLESS output (WLR_BACKENDS=headless,libinput, pixman) -> no DRM for the compositor at all; touch still
  via libinput through the seat shim (input devices only).
- y700-presenter (root for the borrowed DRM fd; Wayland client for labwc):
  1. zwlr_output_manager_v1: set HEADLESS-1 custom mode 1904x3040 (@60 Hz target).
  2. two dumb FBs (reviewed CREATE_DUMB/ADDFB2 path, drmkms.dumb_fb) on the borrowed owner file.
  3. loop: zwlr_screencopy_manager_v1.capture_output -> wl_shm buffer (memfd, XRGB8888 = DRM XR24) ->
     copy_with_damage (paces on real damage) -> ready -> memoryview copy into the back FB (0.9 ms/23 MB measured) ->
     atomic commit planes 95 (left half) + 128 (right half) of that FB, PAGE_FLIP_EVENT -> wait flip -> swap.
  4. exit: native layout restored (same restore as desktop-run), FBs removed, owner untouched.
- Validated now (no DRM, user only): labwc headless offers ext_image_copy_capture v1, zwlr_screencopy v3,
  zwlr_output_manager v4, wl_shm v2, wl_seat v9; default HEADLESS-1 1280x720. Pure-Python wire client wl.py works.
- Cost: CPU copy ~1 ms/frame + labwc pixman render; +1-2 frames latency. Later: GPU/dmabuf path with turnip.

## Presenter step 1: protocol side implemented + host test (no DRM) [로컬 분석/host 테스트]
- wl.py (pure-Python Wayland wire client) + wlcapture.py (zwlr_output_manager_v1 custom mode/scale, zwlr_screencopy v3
  into a memfd wl_shm buffer). test_capture.py: labwc headless -> HEADLESS-1 1280x720 -> set 1904x3040@60 "succeeded";
  capture 1904x3040 stride 7616 XRGB8888 (=DRM XR24); damage-paced 309 frames / 5 s = 61.8 fps; preview PNG shows
  weston-flower + simple-shm on black -> content correct. Windows tiny at scale 1 -> use output scale 2.
- presenter-run.py (r5): shim with card0 denied (inputs only) + labwc headless,libinput + HEADLESS-1 1904x3040@60 scale 2
  + two dumb FBs on the borrowed owner file, row copy (shm stride 7616 -> pitch 7680), blocking atomic commit of
  planes 95/128 halves per frame; exit restores FB 335 halves, RMFB + DESTROY_DUMB of our FBs. Not run yet.

## r5: FIRST WORKING DESKTOP (boot 684daae1, 2026-09-19) [물리 동작 확인]
- presenter-run.py 120 2: labwc headless+libinput via the shim (inputs only), HEADLESS-1 1904x3040@60 scale 2,
  two-plane presenter: 225 frames (damage-paced; idle damage = terminal cursor blink), copy 2.58 ms/frame,
  UNDERRUNS 0/0, restore to native OK, FB cleanup OK, 0 kernel faults.
- User photos: whole screen = labwc background, weston-terminal with the siwal@y700-linux prompt at scale 2,
  weston-simple-touch window with red touch traces drawn by finger -> display + touch confirmed.

## r6: FEX x86-64 GUI apps on the real desktop (boot 684daae1) [물리 동작 확인]
- Host test first (headless labwc, no DRM): x86 xterm (Xwayland), x86 zenity (GTK3/Wayland), x86 glxgears (x86 Mesa
  26.1.6 llvmpipe GL 4.6 under FEX, ~2150 FPS small window) all rendered; capture PNG confirmed.
- presenter-run.py r6 with Y700_STARTUP=x86-demo.sh: 7120 frames / 120 s = 59.3 fps (= headless 60 Hz), copy 1.27 ms,
  UNDERRUNS 0/0, restore OK, 0 faults. User photo: glxgears + x86 xterm (x86_64, Ubuntu 24.04.4); user closed the
  x86 zenity dialog by touch. glxgears ~1960 FPS under the presenter. Battery 40.5 C after the run.
