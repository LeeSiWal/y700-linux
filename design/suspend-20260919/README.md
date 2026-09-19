# Suspend investigation (boot 3a1d6dac, read-only) [로컬 분석/읽기 전용]

## Kernel / policy
- /sys/power/state: freeze mem disk; mem_sleep: s2idle [deep]; pm_test available (none/core/processors/platform/devices/freezer)
- suspend_stats: success=0 fail=0 (never attempted this boot). sync_on_suspend=1, pm_async=1.
- logind: IdleAction=ignore (no auto suspend), HandlePowerKey=poweroff, HandleLidSwitch=suspend.
- NOTE: reading /sys/power/wakeup_count BLOCKS while a wakeup source is active (our own cat hung; killed it).

## Active wakeup sources (would abort any suspend, even pm_test=freezer)
- a600000.ssusb   active 13.4 h  -> USB host mode with the USB LAN adapter (1-1 "USB 10/100 LAN", enx<MAC> = SSH path .77)
- 1994000.qcom,qup_uart active 5.7 h -> BT UART, held by bt keeper 15835 (GENI clock vote 0x54ed)
- charge_wakelock active while the charger is connected
- user wake_lock: none

## Wake-up capable inputs
- gpio-keys (input0) = KEY_VOLUMEUP only, wakeup enabled.
- Power key: pmk8350-pon pwrkey driver NOT loaded -> the power button does nothing in Linux now.
  (If it is ever loaded: logind HandlePowerKey=poweroff would power the device off.)
- rtc0 rtc-pm8xxx (pmk8850) wakeup enabled -> RTC wakealarm usable for timed self-resume.
- Wi-Fi .78 exists as a second SSH path (metric 2000).

## Risks
- Display (msm_drm owner holds the CRTC), KGSL keeper, BT UART, cnss2/Wi-Fi WoW: suspend/resume paths never exercised.
- A resume failure = black screen/no network -> only recovery is a forced reboot (loses this boot's keepers).

## Staged plan (each step needs approval; user present)
- S-1 pm_test=freezer (s2idle): freeze/thaw tasks only. Prereqs: SSH over Wi-Fi, USB LAN unplugged, charger unplugged,
  BT UART vote released (bt keeper change -> separate decision) or accept the abort as the first observation.
- S-2 pm_test=devices: all device suspend/resume callbacks, no real sleep (~5 s). Display blanks + restores.
- S-3 real s2idle with rtc wakealarm +30 s; then S-4 deep.

## S-1 result: boot-susp-freezer-dc50b8a1f0994e8d [API 성공]
- state_write 5.23 s, no error. Freezing user space + freezable tasks each 0.001 s; 5 s pm_test wait; thaw; "PM: suspend exit".
- suspend_stats success 0 -> 1. pm_test restored to none. 30 s after: display/owner state, keepers, pdmapper, USB LAN, wlp1s0 up,
  hci0 all unchanged; no reviewed-fault lines. Active wakeup sources (qup_uart, ssusb) did NOT abort (no wakeup_count written).
- Post-thaw: "Resume cause unknown", SD host mmc1 (bh201, ROOT FS) re-runs its tuning load (normal runtime-PM path, also seen before).
- Note for S-2: device suspend will include mmc1 = the SD root filesystem host.

## S-2 preparation (read-only) [로컬 분석/읽기 전용]
- PM callbacks present (kallsyms): msm_geni_serial_sys_suspend/resume, dwc3_msm_pm_suspend/resume, msm_drm (sde/bl/dp pm),
  adreno_pm_suspend/resume, msm_pcie_pm_suspend/resume + cld3 cfg80211 suspend_wlan, sdhci_msm_pm_ops, nvt_ts_pm_suspend/resume,
  spi_geni_suspend/resume. hci0 is an N_HCI ldisc (qca_pm_ops of the serdev driver do not apply). btpower: none.
- Blockers (runtime active + held): BT UART 1994000 usage=1 (bt keeper vote) ; USB host with LAN 1-1 control=on (never LPM).
  Expected (unverified, no source): msm_geni_serial_sys_suspend and dwc3_msm_pm_suspend return -EBUSY -> dpm_suspend aborts,
  already-suspended devices are resumed.
- dpm order = reverse of device_add: module-created devices (wlan PCI 0000:01:00.0 + MHI, phy0, usb 1-1 LAN, spi touch,
  sound card, rpmsg/fastrpc, hci0) suspend BEFORE DT platform devices such as the UART / dwc3 / mdss / sdhci.
  => an aborting S-2 still exercises Wi-Fi/PCIe, USB-LAN, touch, audio, BT resume paths; display/mmc1 likely not reached.
- cfg80211 suspend with no WoWLAN config leaves all interfaces (Wi-Fi disconnects; y700-wifi-test is autoconnect=no).
- SD root: mmc1 = 8804000.sdhci (bh201 bridge), removable (cd-gpios), card SD "EFAQK"; system suspend powers the card
  off and re-inits it on resume (CID check). Host is runtime-suspended normally.
- Display: resume restores the saved atomic state; underrun counters may change at re-enable.

## S-2 design proposal
- S-2a (blockers kept): pm_test=devices, state=freeze. Record which device aborted (dmesg "failed to suspend"), what
  resumed. Checks relaxed: Wi-Fi disconnect and USB LAN flap recorded (not STOP); display state must return to the native
  state within 10 s; underrun change recorded + user visual check.
- S-2b (later, separate decision): blockers removed (BT vote release, LAN unplugged) so display + mmc1 are exercised.

## S-2a result: boot-susp-devices-b163f341c094b6d3 -> STOP "native DRM state not back within 10 s"
- Two suspend entries in one write: 1st aborted right after freeze ("early wake event"; Wi-Fi deauth reason 3),
  2nd ran device suspend: wlan WoW entry/exit OK, NVT touch suspend/resume (+fw reload OK), DISPLAY OFF/ON
  (backlight 0 / bias 0 -> mode set -> bias 1 / backlight 1536; user saw a blink), then xhci-hcd.3.auto
  platform_pm_suspend returned -22 -> abort + resume. write returned EINVAL after 1.23 s. stats fail=2, last xhci -22.
- Display went down BEFORE xhci (my prediction "display not reached" was wrong).
- New fault-pattern line: "synx: warn: synx_recover: 144: Subsystem restart for core_id: 1216" (display power-off path).
- After: owner 1895 still sole master, underrun 0/0, all keepers + pdmapper alive, LAN up, wlp1s0 reconnected by NM, hci0 present.
- DRM state diff vs native (drmdiff.py, saved drm-state-after-s2a.txt): ONLY crtc flags of the last commit
  mode_changed/active_changed/connectors_changed 0 -> 1 (resume re-commit). fb/planes/mode identical.
- Consequence: every later bundle's strict checks would STOP (state text != native; synx line in dmesg) until reviewed.

## Reviewed exceptions (user approved 2026-09-19: "화면 정상이야, 두 예외 승인하고 진행해 줘")
- registry.py: flag_only_diff() + rebased_states(); display_ok accepts native OR a recorded rebase state whose diff vs
  native is exactly mode_changed/active_changed/connectors_changed 0->1 (re-verified on every check, sha-pinned).
- bootguard.Review.exact_ok + bootmon (manifest 'reviewed_extra_lines') + reviewed_lines.collect_extra(): exact dmesg
  lines (with timestamp) from rebase records; same text with a new timestamp is still a fault.
- rebase-display.py (sudo, one-shot, 'x' files): writes registry-rebase-3a1d6dac-s2a.json + display-rebase-3a1d6dac-s2a.txt.
- tests: test_rebase.py (7) ; full suite 91 OK ; RUNBOOK OK.

## S-2b not run (2026-09-19)
- LAN adapter could not be unplugged for the session (user: "랜을 뽑은 상태에서 못하니까"); builder saw enx<MAC>
  and produced an S-2a-type manifest (boot-susp-devices-53e1a579d5906f82). Removed before any attempt (no attempt.json).
- S-2b needs a session without the USB LAN (Wi-Fi only). Deferred.
