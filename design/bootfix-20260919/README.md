# Early-boot dbus failure (boot 8e18ad1f) [로컬 분석/읽기 전용]

## Timeline (journal, short-precise)
- 53.65 systemd-remount-fs fails: "mount: /: can't find UUID=440d7f6f-..." (no /dev/disk/by-uuid yet; / already rw from
  Bootstrap -> harmless).
- 53.669 tmpfiles-setup-dev(-early) finished; 53.683 starting systemd-udevd; 54.105 udev-trigger (coldplug queued);
  54.111 udevd started; 54.1276 sysinit.target; 54.131 dbus.socket listening.
- ~54.13-54.2 dbus-daemon x5: "fatal error setting up standard fds: Failed to open /dev/null: Permission denied" ->
  start-limit-hit -> NetworkManager dependency failed. polkitd x5 "Lost the name org.freedesktop.PolicyKit1" (no bus).
- 54.386 ctime of /dev/null, zero, urandom, kmsg (udev coldplug processing of the mem class touched the nodes).
- /dev = tmpfs made by Bootstrap init (nodes born 1970-01-02 19:35:24, before RTC), not devtmpfs.

## What is ruled out
- SELinux: LSM present but no policy loaded (/sys/fs/selinux/class empty, enforce 0). BPF LSM not available.
- dbus.service has no sandboxing (no PrivateDevices/DevicePolicy); udev rules never set a mode for null/zero/urandom
  (so 54.386 is a timestamp touch, not necessarily a mode change). Exact reason for EACCES in the 54.13-54.39 window:
  NOT determined (original mode of the Bootstrap-created node is unknown).
- 3a1d6dac: dbus started fine -> timing dependent.

## Candidate mitigations (persistent config on the SD root -> needs approval)
1. /etc/systemd/system/dbus.service.d/10-y700-udev-settle.conf:
     [Service]
     ExecStartPre=+/usr/bin/udevadm settle --timeout=15
   dbus then starts only after the coldplug queue (incl. mem devices) is processed (~0.3 s later than now).
2. Bootstrap init: mknod the standard nodes with explicit 0666 (needs init_boot rebuild + flash; heavier).
3. Also possible: a .link file pinning the STA name (MAC <MAC> -> wlan0) to stop the wlp1s0 race.

## Approved (user: "1번 드롭인 승인하고 진행해 줘")
- File prepared: bootfix-20260919/dbus.service.d/10-y700-udev-settle.conf  (ExecStartPre=-+/usr/bin/udevadm settle --timeout=15)
  "+" runs as root (dbus runs as messagebus), "-" ignores a settle timeout so dbus still starts.
- Installed by the user with one sudo command; takes effect on the next boot (no daemon-reload/restart of the running dbus).
- Installed 2026-09-19 on 8e18ad1f: /etc/systemd/system/dbus.service.d/10-y700-udev-settle.conf sha256 ff3bb518…a001a0 (matches).
  systemd-analyze verify dbus.service: no dbus errors (only pre-existing escape warnings of the y700-*-diag units).
  Not reloaded (dbus running); first real test = next boot: expect dbus/NetworkManager active without manual start.

## Wi-Fi name pinning (approved: "(나) Wi-Fi 이름 고정 승인하고 진행해 줘")
- udev log 8e18ad1f 17:32:59: "wlan0: Failed to rename ... to 'wlp1s0': File exists" (wifi-aware0 took wlp1s0 first).
- STA MAC is not stable across boots ("WLAN MAC address is not set ... using default"), so matching is by
  OriginalName + Path (ID_PATH platform-1c00000.pcie-pci-0000:01:00.0), not by MAC.
- /etc/systemd/network/10-y700-wlan-keep.link: no Name/NamePolicy -> kernel names kept; effective when the
  interfaces are created (next wifi-b). NM profile y700-wifi has no interface-name; y700-wifi-test* stay bound to wlp1s0.
- Installed 2026-09-19 18:22 on 8e18ad1f: /etc/systemd/network/10-y700-wlan-keep.link sha256 0e233185… (identical to source).
  Current interfaces unchanged (wlan0 up, wlp1s0 = wifi-aware); first real test = next boot's wifi-b.

## Next boot 2208551e (18:24)
- dbus, NetworkManager, polkit all active without manual start; only systemd-remount-fs failed (known, harmless).
  (one boot; the race was intermittent, so keep checking on later boots.)
- LAN now gets .81/.82 from NM DHCP (was .77 via networkd); SSH works.
- No dwc3 class/hub dumps this boot; dmesg review: only the adci hung report (bound at runtime).

## Boot automation design (proposal, NOT installed)
- nextboot-impl/bringup.py: runs pstore, display, owner, governor, gpu, keeper, touch, adc, wifi-a, wifi-b, nmcli up
  y700-wifi. Per stage: skip if a bundle of THIS boot already succeeded; STOP if one was attempted without success
  (never retried); otherwise build as siwal (runuser) -> --check as siwal -> --apply as root; success = reviewed status.
  Log ~/y700-agent/bringup-<boot8>.log/.json, per-bundle bringup-apply.log. Kill switch ~/y700-agent/bringup.disable.
  Dry run on 2208551e: skipped pstore..keeper (done), reused fresh touch bundle, would build adc/wifi-a/wifi-b.
- y700-bringup.service: Type=exec + RemainAfterExit (does not delay multi-user.target; held processes stay in the
  unit cgroup), RefuseManualStop=yes (stop would kill owner/keeper), ConditionPathExists=!bringup.disable.
- Scope 1 = display..Wi-Fi only (what the desktop/FEX path needs); BT/ADSP/audio/sensors stay manual for now.
- Validation plan: (1) this boot: sudo python3 -B bringup.py --upto wifi-b --wifi from the current state (touch..Wi-Fi);
  (2) approval -> install + enable the unit (user sudo); (3) reboot -> fully automatic up to Wi-Fi.

## Validation on 2208551e (2026-09-19 18:59-19:08) [API 성공]
- sudo bringup.py --upto wifi-b --wifi: skipped pstore..keeper (done by hand), APPLY touch/adc/wifi-a/wifi-b -> all OK,
  nmcli up y700-wifi rc=0. 8.5 min. Wi-Fi .84.
- .link check: wlan0, wifi-aware0, p2p0 all ID_NET_LINK_FILE=10-y700-wlan-keep.link, no renames, no "File exists".
- dbus drop-in: dbus/NM/polkit active at boot without manual start.
- 2026-09-19 19:10: user installed + enabled y700-bringup.service (approved; "enabled", not started this boot).
  daemon-reload also loaded the dbus drop-in. First automatic run = next boot.

## First fully automatic boot: 684daae1 (2026-09-19) [장기/재부팅 검증]
- y700-bringup.service started 19:12:08 (boot +1 min) -> pstore, display (4m44s), owner (colour bars seen by the user,
  19:18:34), governor, gpu, keeper, touch, adc, wifi-a, wifi-b, nmcli up y700-wifi -> BRINGUP done 19:30:16 (18 min).
  No human input. dbus/NM active again (2nd boot in a row). Wi-Fi names kept (wlan0 .86, wifi-aware0, p2p0).

## Reboot verification 2026-09-21 (boot 85424e1c)
First boot that actually exercised the installed units (`--upto audio-c --wifi`). It found five bugs; all are fixed
here, and `verify-boot.py` checks a boot in one go (units, stages, /dev nodes, network, hci0, sound card, speaker
fifo, presenter, compositor, input guard).

1. `make-boot-bundle.py` used `MGMT_NET` before defining it, so every Wi-Fi stage died while building its bundle.
2. **`KillMode=process` on y700-bringup.service.** A failed stage made systemd clean the unit cgroup, which killed the
   display owner and the GPU keeper - the two processes that must live for the whole boot - so the desktop could not
   start and every later stage refused with "registered process gone/changed". Verified afterwards: wifi-b failed and
   the desktop kept running.
3. `desktop-service.py` waits for the `touch` stage as well when the touch proxy is on. It used to wait only for
   owner+gpu and started 2.5 min before the touchscreen existed ("touch identity changed").
4. `touchproxy.py` resolves the touchscreen by name and creates `/dev/input/eventN` from sysfs: `/dev` is a plain
   tmpfs, so on a fresh boot there is no input node at all, and the event numbers move between boots.
5. `load-stage.py` tolerates a management-link flap for `MGMT_FLAP_S` (30 s). wifi-b loads the USB gateway modules
   (usb_f_gsi, gsim, rmnet_mem, ipam); the USB LAN adapter re-enumerates and its carrier drops for a moment, which the
   guard read as losing the management path and aborted the stage.

`bringup.py` also gained `--from <stage>`: continue at a stage after a human dealt with an earlier one by hand (a
failed bundle is never retried automatically). Nothing is rewritten - the earlier stage keeps its failed record and
the run logs that the operator vouched for what came before.

Stage timings of that boot (the target of the pending speed work): display 263 s, owner 75, gpu 124, keeper 82,
touch 81, adc 90, wifi-a 198, wifi-b 133, bt-a 200, bt-b 72, btkeeper 85, qrtr-smd 51, adsp 114, audio-c1 143,
audio-c 223. Roughly 855 s of that is fixed observation windows in `load-stage.py` (15 s baseline per stage, display
130 s, six stages at 60 s, ...), not module loading.
