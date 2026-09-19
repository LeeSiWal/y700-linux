# Power / charging (boot 3a1d6dac, 2026-09-19)

## Findings (read-only)
- battery: Si-anode pack (model SI_ANODE_2195MAH_..._2S...), charge_full 8.79/9.0 Ah, cycle 4, voltage_max 4.53 V.
- Firmware (pmic-glink battmgr) changes chg_fv by itself: 4160 (held, health "Over voltage", battery discharging while
  plugged) -> 4560 -> 4530. At 100 % with fv 4560 it charged at ~3.5 A (usb 2.475 A) up to 4.506 V.
- charge_control_start/end_threshold = 70/80 exist but are NOT enforced on Ubuntu (Android's Lenovo service is absent).
- usb1-conn-therm missing (no IIO ADC provider) -> "Failed to get usb1-conn-therm" every 5 s; no connector thermal guard.
- load ~3.3 = 3 D-state kthreads (adci_thread, hfi_core_dbg_client_listener, msm_hw_fence_soccp_listener), not CPU.
- Backlight (aw99706a, panel0-backlight 0..4095), battery only, 30 s avg: 1024 -> 3.09 W, 4095 -> 5.97 W.

## Tools (nextboot-impl/)
- chgwatch.py: read-only logger (jsonl, 10 s). Running this boot -> chgwatch-<boot8>.jsonl.
- chgctl.py: foreground charge limiter (sudo, until Ctrl-C/reboot). Only write: battery/charging_enabled 0|1.
  Hysteresis start/stop from the sysfs thresholds; safety latch at 45.0 C (release 40.0 C) or V > voltage_max+50 mV;
  gives up after 3 firmware overrides; restores the start value on exit. test_chgctl.py (4 tests).

## Plan
- P-1 (done): read-only analysis + logger.
- P-2: first real run of chgctl with the charger plugged in (capacity > 80 -> writes 0). Observe what charging_enabled=0
  means (battery idle on USB power vs. battery discharging) and whether the firmware reverts it. Ctrl-C restores 1.
- P-3: if P-2 is clean, use chgctl whenever charging on Ubuntu. Boot-time automation needs separate approval (persistent service).
- Later: PMIC ADC / usb1-conn-therm (SPMI, separate reviewed stage).

## P-2 result (boot 3a1d6dac, t=1789801939..2149) [API 성공]
- No charger: writes to charging_enabled are ignored (stays 1). With a charger: 0 takes effect ~1 s later (async readback).
- charging_enabled=0 with charger: status "Not charging", battery current 0 (V steady 4.414-4.416 V), USB supplies the
  system (0.25-0.48 A at ~5 V). i.e. battery idle / pass-through. Firmware did not revert it over ~3 min.
- chgctl v1 bugs: judged the immediate (stale) readback -> false "overridden_by_firmware"; exit restore compared the stale
  readback -> left charging_enabled=0. Fixed (v2): write only while usb online, poll readback up to 5 s, exit always
  writes 1 and confirms. 6 tests pass.

## chgctl v2 run (t=1789802276..2324) [API 성공]
- Started with en=0 (left by v1), usb online: held 0 for ~50 s ("Not charging", I=0, V 4.417 V steady).
- Ctrl-C: wrote 1, readback confirmed 1 -> charging resumed ~3.4 A at 97 % (fv 4560). User then also wrote 1 by hand.
- Not yet verified: 70 % re-enable path (needs hours), whether en=0 survives unplug/replug.
