# 발열 관리 (2026-09-20, boot 684daae1)

## 조사 (로컬 분석 / API 확인)
- CPU thermal zone(cpu-1-*, cpu-0-*)에는 passive trip(105/112/114°C)과 step_wise 정책이 있지만 **cooling device가 하나도 bind되어 있지 않다**
  (cooling_device0/1 = cpufreq-cpu0/cpu6, kgsl, battery, backlight, ufs는 존재). Android에서는 vendor thermal-engine이 이 일을 한다
  → Linux에서는 125°C 'hot' trip 전까지 아무것도 클럭을 낮추지 않는다
- 프라임 코어(policy6, cpu 6-7)는 4.6 GHz에서 **2초 만에 66 → 100°C**(Steam 다운로드 ~92 Mbps, Steam 클라이언트 FEX CPU 45–70%)
- Steam을 효율 코어(0-5)에 묶으면 프라임은 60°C가 되지만 효율 코어가 3.6 GHz로 올라 배터리 전류가 늘었다 → 기각
- **policy6 상한 3.4 GHz(수동, 사용자 승인)**: 같은 부하에서 프라임 최고 68–78°C(이전 86–102°C), 다운로드 속도 변화 없음

## y700-thermald (`thermald.py`, `y700-thermald.service`)
- 1초마다 도메인별(프라임 CPU, 효율 CPU, GPU) 최고 온도 → 목표 초과 시 상한 한 단계 하강(8°C 이상 초과 시 두 단계, 105°C 이상은 바로 floor),
  목표보다 5°C 낮은 상태가 3초 이어지면 한 단계 상승. 배터리 온도가 한도를 넘으면 모든 ceiling을 낮춘다
- 쓰는 값: `cpufreq/policy{0,6}/scaling_max_freq`, `kgsl-3d0/max_clock_mhz`만. 종료(SIGTERM) 시 원래 최대값으로 복원
- 프로필(`thermal.json`, 설정 앱 "Y700 디스플레이 → 발열 관리"에서 선택, 1초 안에 반영):

| 프로필 | 프라임 CPU | 효율 CPU | GPU | 목표 | 배터리 한도 |
|---|---|---|---|---|---|
| quiet 저발열 | 2.67 GHz | 2.50 GHz | 726 MHz | 75°C | 40°C |
| balanced 균형(기본) | 3.40 GHz | 3.63 GHz | 1050 MHz | 85°C | 44°C |
| performance 성능 | 4.61 GHz | 3.63 GHz | 1200 MHz | 95°C(GPU 92) | 46°C |

- 상태: `/run/y700-thermal/status.json`(설정 앱이 5초마다 표시), 로그 `~/y700-agent/thermald-<boot8>.log`(상한 변경 때만)
- `thermald.py --test`(host 테스트: 단계 제어, 긴급, ceiling, 배터리, 설정 파일), `--dry-run`(읽기 + 판단만, 쓰기 없음)
- 끄기: `~/y700-agent/thermald.disable` 생성 또는 `systemctl disable --now y700-thermald`
