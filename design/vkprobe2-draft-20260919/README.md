# Vulkan probe 2 (turnip 26.2.3) (2026-09-19, 기기 세션)

묶음: `~/y700-agent/vkprobe2623-b4a1c71899a258aa` (manifest SHA256 b4a1c71899a258aa24074c5efe627256bd34929baef39450005031a30b61b999)
상태: host 테스트 11개 통과, `--check` 통과(retained 16 = DRM 13 + kgsl 13398 + 이전 probe 14432). **`--apply` 미실행.**

## 이전 probe와 다른 점
- ICD: `~/y700-gpu/turnip-kgsl-26.2.3/`(libvulkan_freedreno.so 91cdcfad…, ICD api 1.4.354, drirc 2개)
  - 빌드: Mac Colima y700-build, ubuntu:26.04 arm64, `--wrap-mode=nodownload`. 부산물 없음. NEEDED가 26.0.8과 같다.
- `VK_EXT_debug_utils`를 켜고 messenger를 만든다(instance pNext + vkCreateDebugUtilsMessengerEXT).
  → release 빌드의 vk_errorf 메시지가 `VKMSG sev=… type=…` 줄로 worker 로그에 남는다. severity error(0x1000)가 하나라도 있으면 성공으로 보지 않는다.
- 나머지 동작, 감시, 판정은 1차 probe와 같다(no VkDevice/submit, instance 유지, hold).

## 결과 (2026-09-19 01:5x KST, 같은 boot) — API 성공
- 순서대로 성공: ICD 로드 → 협상 v5 → instance → messenger → enumerate("Found compatible device '/dev/kgsl-3d0'") → count 1
- properties: deviceName **"Adreno (TM) 840"**, vendorID 0x5143, deviceID 0x44050a31, apiVersion 1.4.354, driverVersion 109060099, deviceType 1(integrated)
- VKMSG 0건(error 없음), 새 커널 로그 0건, fault 0건, D 상태는 검토된 3개만 있다. underrun 13/13(전·후·60초), final check(firmware/ICD/node) OK
- worker PID15040이 instance를 유지하며 hold 중이다
- 미검증: VkDevice 생성, 메모리/커맨드 제출, 렌더링, 화면 출력 경로, close 경로(정상 destroy)

## 관찰: 발열
- 종료 시 배터리 44.5°C(감시 기준 < 45.0°C)였고, 이후 44.7°C다. CPU 존 최대 83°C, gpuss 약 54°C다. CPU는 100% idle이고 충전은 ~8.3V × 2.47A다.
- 원인(확인): cmdline `cpufreq.default_governor=performance`. Android init이 바꿔 주던 governor를 Ubuntu에서는 아무도 바꾸지 않는다
  → policy0 3.63GHz, policy6 4.61GHz에 고정. 사용 가능 governor: walt conservative powersave performance schedutil

## governor 변경 (2026-09-19 01:5x, 사용자 sudo, 런타임·비영구)
- policy0/6: performance → **schedutil**. 90초 관찰(비root):
  - policy0 384MHz, policy6 4608→768MHz(20초 안에 안정)
  - CPU 존 cpu-1-0-1 83.3→51.2°C, cpu-0-0-1 62.8→49.6°C, gpuss-0 53.6→49.8°C
  - 배터리 44.5–44.7°C로 **변화 없음** → 충전 발열이 주 원인으로 추정(98%에서 약 20W 입력)
  - 새 dmesg 0건, 새 D 0건, retained 17개 프로세스 전부 생존, USB carrier 1
- 재부팅하면 performance로 돌아간다(cmdline). 영구화는 부팅 서비스 설계 때 다룬다.
