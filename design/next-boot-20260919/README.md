# 다음 부팅 재현 절차 (초안, 2026-09-19 기준 boot b4e16b26)

상태: **문서 설계만 했다. 재부팅, 서비스 등록, 영구 설정은 하지 않았다.**
근거는 모두 현재 부팅의 묶음(`~/y700-agent/*`)과 결과 파일이다. 새 부팅에서 **처음 해 보는 조합**은 "미검증"으로 표시했다.

## 1. 부팅 경로 (확인됨)
- 현재 slot A의 init_boot는 **Bootstrap 2.1**이다: stage1 107개 → stage2 37개 모듈 → SD Ubuntu(`mmcblk1p3`, 179:3) systemd
  (`/y700-stage2-loaded.txt`, `/y700-systemd-booted.txt`: 이번 부팅 22:40:42 도달)
- USB Ethernet(r8152, `enx<MAC>`, <LAN-IP>)과 SSH가 자동으로 올라온다 → **재부팅에 flash나 fastboot가 필요 없다**
- 재부팅은 사람이 직접 한다. 자동 재부팅은 하지 않는다.
  - 종료 과정에서 마지막 DRM close와 KGSL close가 일어난다. 이 경로(synx SSR 이력)는 미검증이다.
  - 종료가 멈추면 전원 버튼 강제 재부팅이 필요할 수 있다.

## 2. 재부팅 후 사라지는 것 / 남는 것
| 사라짐(매 부팅 재구성) | 남음(파일) |
|---|---|
| display 16개 + GPU 7개 모듈 | `/lib/firmware/gen80200_{sqe,aqe}.fw, _gmu.bin, _zap.mbn`(root 설치, 해시 기록) |
| `/dev/dri/card0`, `/dev/kgsl-3d0`(`/dev`는 tmpfs, 수동 node) | turnip `~/y700-gpu/turnip-kgsl-26.2.3/`(SHA256SUMS) |
| DRM owner, retained worker 21개 전부, 화면 상태(mode blob, FB, plane 배치) | 모든 묶음, 증거, 설계(`~/y700-agent`, `~/y700-design`) |
| cpufreq governor(부팅 cmdline `performance`로 돌아감) | pstore 백엔드 모듈 파일(묶음 안) |
| 검토된 D wait의 PID/starttime(부팅마다 바뀜) | |

## 3. 단계별 절차
각 단계는 **one-shot 묶음 + supervisor**(기존 방식)로 하고, 단계마다 사용자 sudo 1회로 진행한다.
공통 STOP 조건: boot/slot/root/모듈 identity 변화, 검토되지 않은 커널 fault, 새 D 상태 10초 이상, 배터리 < 20% 또는 ≥ 45°C, USB link 변화.

### 단계 0. 재부팅 전 (현재 부팅)
- 할 일 없음. 증거는 모두 파일로 남아 있다. 필요하면 pstore와 ramoops 설정만 확인한다.
- 권장: 충전기를 분리한다(충전 중 배터리 44°C대 관찰). 사람이 재부팅한다.

### 단계 1. 기준 수집 (읽기 전용, sudo)
- boot_id, 커널, slot `_a`, root 179:3, 모듈 목록과 build-id(stage1+2 = 144개 예상)
- D 상태 task와 전체 stack: ADCI(`adci_thread`)는 알려진 idle wait다. 부팅마다 PID/starttime을 새로 검토해 고정한다.
- dmesg fault: 알려진 SPMI 경고 3개 head와 35 frame, ADCI hung-task 보고 → 이번 부팅의 정확한 텍스트로 검토 목록을 다시 만든다.
- 근거: `reboot-baseline-*`, `runtime-baseline-*`

### 단계 2. pstore 백엔드
- `qcom_dynamic_ramoops.ko`(f8a65dc9…)를 적재하고 `ramoops` bind를 확인한다. pstore 내용은 지우지 않는다.
- 근거: `recover-pstore-b3951187671b3bd4`(이번 부팅)

### 단계 3. display 모듈 15개 (순서 고정)
`msm_hfi_core`(**수정본** 2d515fca…) → `nvmem_qfprom` → `qti-fixed-regulator` → `nvt_36xxx` → `ipclite` → `msm_hw_fence`
→ `synx-driver` → `qcom_va_minidump` → `sync_fence` → `msm_ext_display` → `gh_irq_lend` → `smcinvoke_dlkm`
→ `hdcp_qseecom_dlkm` → `drm_display_helper` → `msm_drm`(**수정본** 8b6dc17b…, build-id 936e3c7b…)
- 파일, 해시, 실제 build-id 일치 확인: `~/y700-design/modules-current-boot.json`(23개 모두 live == file)
- **알려진 문제(이번 부팅에서 실제 STOP 원인)**: `hfi_core_dbg_client_listener`가 idle D wait 상태라 약 120초 뒤 **hung-task INFO**를 출력한다.
  이번에는 `display-recovered-*`가 이 INFO에서 멈췄고 `...-resume`으로 나머지 3개를 올렸다.
  → **다음 묶음에는 HFI와 fence listener의 hung-task 보고를 ADCI처럼 "정확한 frame 목록 일치" 템플릿으로 허용하는 규칙이 필요하다**(구현 완료: `nextboot-impl/bootguard.py`).
- 기존 `failed to parse vregs/power config` 메시지 2개는 msm_drm insmod PID의 정확한 텍스트일 때만 허용한다(기존 규칙).

### 단계 4. DRM node
- `/dev/dri`를 만들고 `mknod /dev/dri/card0 c 226 0`(0600 root)를 만든다. sysfs `card0/dev` 값을 먼저 확인한다(renderD128은 필요 없다)
- 근거: `screen-small-*/run-screen.py`

### 단계 5. 화면: DRM owner + native two-plane
- 검증된 순서(이번 부팅):
  1. **modeset** — splash 상태 확인(connector→CRTC, ACTIVE=1, plane FB 0) → 같은 timing(1904×3040@120, clock 915552, htotal 2244, vtotal 3400)으로 **새 mode blob**
     → ALLOW_MODESET atomic 1회(plane95, 952×1520 중앙 FB)
  2. … 여러 실험 … → **native**: 1904×3040 cached dumb(pitch 7680 = 1920px stride) → plane95 SRC(0,0,952,3040), plane128 SRC(952,0,952,3040), 1:1
- **다음 부팅 계획(미검증 조합)**:
  - 기존 C 바이너리는 객체 ID(connector 69, CRTC 205, planes 95/128, MODE_ID 340, FB78)를 **하드코딩**했다 → 그대로 재사용할 수 없다.
  - **새 `display-owner`(Python, drmabi 재사용)**: card0를 직접 열어 DRM master가 되고 **끝까지 보유**한다.
    ID는 이름과 속성으로 찾고, 이번 부팅 값과 같은지 비교해 기록한다(다르면 STOP 후 검토).
    - commit A: modeset(screen-small과 같은 형태). commit B: native two-plane. 각각 TEST_ONLY 후 사람이 확인한다.
    - 이후 모든 화면 작업은 `pidfd_getfd`로 owner의 fd를 빌린다. **worker 누적을 없앤다**(owner 1개 + 필요 시 test worker).
  - 금지: 명시적 SCANOUT|WC dumb 경로(이전 hang), clock/ICC/CESTA 쓰기, `bus-control` 문자열 debugfs 읽기, DRM fd close

### 단계 6. CPU governor
- `schedutil`로 설정한다(policy0/6). 이번 부팅에서 CPU 83→51°C, 커널 로그 0건, 비영구
- 근거: `vkprobe2-draft` README

### 단계 7. GPU(KGSL)
- 7개 모듈을 순서대로: `coresight` → `msm_sysstats` → `msm_performance` → `governor_msm_adreno_tz` → `governor_gpubw_mon` → `governor_msm_adreno_ro` → `msm_kgsl`
  - 적재 전 확인: gcc/ICC에 다른 미bound consumer가 남아 있는지(gpucc만 sync_state 예상), `/lib/firmware` 4개 해시
- node: sysfs `/sys/class/kgsl/kgsl-3d0/dev`의 **동적 major**(이번 부팅 495:0)를 읽어 `mknod /dev/kgsl-3d0`(0600 root)를 만든다
- **`gpu-keeper`**: 첫 open(GMU boot, zap/TZ, 이번 77ms) 후 fd를 끝까지 보유한다 → 이후 앱의 open/close는 non-last다(이번 부팅에서 검증된 조건)
- DEVICE_INFO chip_id는 **0x44050a31**이어야 한다(DT의 0x44050a01이 아니다)
- 근거: `gpu-load-4bebd1cff55e9960`, `gpu-open-c1810de27aec2d85`(+postcheck)

### 단계 8. Vulkan 스모크(순서대로, 각각 one-shot)
- vkprobe(26.2.3, debug_utils) → compute(3i+7 검증 + destroy) → render(GPU 프레임 → 새 dumb → two-plane) → anim 120
- 이번 부팅 값: fence 5.22ms / 6.24ms, anim 58.79fps(p50 16.66ms)
- 근거: `vkprobe2623-*`, `vkcompute-*`, `vkrender-a9156afd*`, `vkanim120-19cb62f5*`

## 4. 부팅마다 다시 확인할 값
- boot_id, 모든 PID/starttime, 검토된 D wait(ADCI, HFI listener, fence listener의 PID와 stack)
- DRM 객체 ID(connector, CRTC, plane, mode blob, FB), `/dev` 수동 node의 major:minor(kgsl는 동적), 기대 DRM state 텍스트(부팅마다 새로 만든다)
- dmesg 검토 목록(이번 부팅의 정확한 텍스트는 다음 부팅에 쓸 수 없다. 템플릿으로 만들어야 한다)

## 5. 구현 상태 (2026-09-19 완료, 로컬)
- 1–5 모두 `~/y700-design/nextboot-impl/`에 구현했다. host 테스트 41개 통과. 실행 순서와 한계는 그 README에 있다.
- 남은 실기 검증: `nextboot-impl/validate-drmkms/validate.py`(현재 부팅, 읽기 전용, sudo)

## 6. 미해결 / 주의
- 종료 시 DRM, KGSL, synx 정리 경로 미검증(재부팅 자체의 위험)
- 충전 중 배터리 온도가 높다(98–100%에서 약 2.5–2.9A, 44°C대). 원인 미조사
- Wi-Fi, BT, audio, sensors, suspend, touch 이벤트는 미착수. 화면 방향: 사용자가 잡은 방향에서 framebuffer x=0이 오른쪽(`vkanim120-8b87…/visual-note.json`)
- init_boot 후보(`8cbffbc2…`, Stage2 이후 Android fallback 차단)는 여전히 hardware_boot_tested=false이고 flash하지 않았다. **이 절차에는 필요 없다.**
