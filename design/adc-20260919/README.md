# PMIC ADC / connector temperature (boot 3a1d6dac) — design [로컬 분석/읽기 전용]

## Why
- qti_battery_charger (Lenovo) polls every 5 s: "lenovo_chg_monitor_work: Failed to get usb1-conn-therm" -> it looks up the
  THERMAL ZONE usb1-conn-therm, which only exists once the ADC driver registers its thermal sensors. No connector
  over-temperature handling while charging at ~3.5 A.

## Topology (DT, read-only)
- Provider: /soc/arbiter@c400000/spmi@c426000/pmk8850@0/vadc@9000, compatible "qcom,spmi-adc5-gen4", 38 channel nodes,
  interrupts adc-sdam0/adc-sdam1, pinctrl "default". Device c426000.spmi:pmk8850@0:vadc@9000 exists, no driver,
  waiting_for_supplier=0. IIO core is built in; /sys/bus/iio/devices empty; no adc5 symbols in the kernel -> module needed.
- usb1-conn-therm = channel 0x2747 = node pmih010x_usb_therm (qcom,adc-tm-type set). usb2-conn-therm = 0x148 (pmh0101).
- 15 DT thermal zones use this ADC (phandle 0x719): ap-therm (passive 48..90 C, no maps), batt-pack, batt2-pack, fast-chg,
  fcam-ntc, flash-led-ntc, lcm-thermal, quiet-therm, rear-cam-ntc, top-chg, ufs, usb1-conn, usb2-conn, wlan (passive 125 C,
  no maps), xo-therm (passive 78/80 C -> display_cdev1..3 + therm_cdev0/1; "shutdown-trip" 90 C type HOT = notify only).
  No CRITICAL trip on any ADC zone.
- Consumers waiting on the ADC (devlinks): 9 PMIC temp-alarm devices (qcom,spmi-temp-alarm, no driver either).
  They are NOT part of this stage (they carry critical trips; separate review later).
- sys_temp_support (lenovo,sys-temp) lists the same NTC names (listener, not needed for the charger lookup).

## Risks
- All ADC traffic goes over SPMI (pmic-arb); bootguard keeps its reviewed SPMI templates; any new SPMI error = STOP.
- ADC5 gen4 is SDAM-based and shared with firmware (battery manager on SoCCP/ADSP); normal on Android.
- A wrong reading could push xo-therm over 78 C -> backlight/charger mitigation (recoverable; no shutdown path).
- New thermal zones change nothing else: step_wise, existing 58 zones untouched.

## Stage "adc" (to build after the modules are copied)
1. Load the ADC module(s) only (driver for qcom,spmi-adc5-gen4 + its dependencies). No temp-alarm.
2. Observe 60 s: vadc bound, iio:device present, the 15 zones registered with plausible temperatures (0..70 C),
   "Failed to get usb1-conn-therm" stops appearing, no unreviewed faults, display/keepers/net unchanged.
3. Read-only report of all 15 zone temperatures + charger reaction (battery/usb attributes).

## Human step: find the modules on the Mac
  find /Users/siwal/Desktop/y700/work \( -iname '*adc*' -o -iname '*vadc*' -o -iname '*temp*alarm*' \) -name '*.ko' -exec shasum -a 256 {} \;
  grep -h -E 'adc5-gen4|adc5_gen4|spmi-adc5' /Users/siwal/Desktop/y700/work/vendor_dlkm_extracted/modules/modules.alias /Users/siwal/Desktop/y700/work/vendor_dlkm_extracted/modules/modules.dep 2>/dev/null

## Modules (copied 2026-09-19) [로컬 분석/host 테스트]
- qcom-vadc-common.ko 513d050b…, qcom-spmi-adc5-gen3.ko 376923c9… (alias covers qcom,spmi-adc5-gen4; depends qcom-vadc-common,
  qcom_ipc_logging). bt-20260919/check-imports.py: unresolved 0, CRC 48 checked, 0 mismatch. modules.sha256 written.
- Bundle boot-adc-042f95e08404956c: --check PASS (load order vadc-common -> adc5-gen3).

## Result boot-adc-042f95e08404956c: STAGE_ADC_LOADED [API 성공]
- driver qcom-spmi-adc5-gen3 bound to vadc@9000, iio:device0; all 15 zones registered, 33.1..38.8 C (step_wise).
- Charger: "Failed to get usb1-conn-therm" stopped (275 total, last at 50400 s, before the load's +15 s);
  lenovo_chg_monitor_work now logs USB_TEMP = [351,360] (usb1/usb2 connector, 0.1 C), BATT_TEMP 343, PD_PPS.
- No unreviewed kernel faults; display/keepers/LAN unchanged. Added to the next-boot runbook as stage 6a (RUNBOOK OK).
- Not done: PMIC temp-alarm (9 devices, qcom-spmi-temp-alarm.ko, critical trips) -> separate review.

## PMIC temp-alarm review (read-only) [로컬 분석/읽기 전용]
- DT: 12 qcom,spmi-temp-alarm nodes; 9 have Linux platform devices (pmh0101, pmh0104, pmh0110 d/f/g/i, pmih010x, pmih010x-lite,
  pmr735d). pm8010 m/n and pmd802x nodes have no platform device (their PMIC children are not populated) -> not probed.
- 6 of the 9 read die temperature through the ADC ("thermal" io-channel: pmh0101, pmh0110 d/f/g/i, pmih010x, pmih010x-lite);
  pmh0104 / pmr735d have no ADC channel -> stage-based estimate only.
- Thermal zones: trips 95 C passive / 115 C hot / 145 C CRITICAL (pmih010x_lite: 125/135/145). Critical -> kernel orderly
  poweroff above 145 C die temperature. Cooling maps (passive 95 C): pmh0110_d -> d_hvx (NSP), _f -> f_gpu, _g/_i -> CPU.
- Driver probe (upstream behaviour of qcom-spmi-temp-alarm): reads the PMIC status and WRITES the alarm configuration
  (over-temperature threshold set chosen from the critical trip, alarm enable). This is a PMIC register write over SPMI,
  the same configuration Android applies on every boot. PMIC hardware stage-2/3 shutdown exists with or without the driver.
- Benefit: software view + CPU/GPU/NSP mitigation at 95 C PMIC die temperature; not required for the charger (done by adc).
- Risk: PMIC register writes (not reversible by unloading the module; persists until reboot), SPMI traffic, a critical
  trip that powers the device off if a reading is wrong (orderly poweroff = losing this boot).
- Proposed stage "tempalarm" (needs explicit approval for the PMIC writes): copy qcom-spmi-temp-alarm.ko (add131a7…),
  import/CRC check, load after adc, observe 60 s: 9 devices bound, 9 *_tz zones registered with 10..90 C, no alarm IRQ,
  no unreviewed fault; record all readings. Abort criteria before load: any die reading from the ADC >= 90 C.
