# KGSL 1단계 적재 시험 설계 (2026-09-19, 기기 세션)

묶음: `~/y700-agent/gpu-load-fd48db47d4fd099c` (manifest SHA256 fd48db47d4fd099c807e6088139b06b5b16e111af2cb77e249709d20a5f24abf)
상태: host 테스트 8개 통과, `--check` 통과. **`--apply`는 아직 실행하지 않았다.**

## 범위 (1단계)
- 모듈 7개를 순서대로 insmod: coresight → msm_sysstats → msm_performance → governor_msm_adreno_tz → governor_gpubw_mon → governor_msm_adreno_ro → msm_kgsl
- `firmware_class.path`를 묶음의 `firmware/`로 **임시** 설정한다. 원래 값('')이 아니면 시작 전에 STOP한다. 끝나면 성공이든 실패든 ''로 복원하고 readback한다.
- msm_kgsl을 넣은 뒤 60초간 probe/bind를 관찰한다. **`/dev/kgsl-3d0`는 열지 않는다**(존재 여부만 stat).
  명령 제출, clock/ICC/KMS 쓰기는 없다. **어떤 결과가 나와도 unload하지 않는다.**

## 매 tick 검사 (0.2~0.5초)
- 기존 health_guard: boot/slot/root, 160개 모듈 build-id, 새로 적재된 모듈의 build-id, 예상 밖 모듈, ramoops, 배터리, 검토된 D wait 3개,
  새 D 상태 10초 제한, 커널 fault regex(new_kernel_faults + 검토된 28개 기록만 허용)
- 화면: retained worker 13개 identity, DRM state == FB335 기준 텍스트, DRM client == kms-held 2604, underrun 13/13
- 네트워크: enx<MAC> carrier=1, operstate=up
- GPU 측 오류: 새 dmesg 줄 중 kgsl/adreno/gmu/gen8/zap/coresight/devfreq/governor를 포함하고 fail/error/unable/fault/timeout/denied/invalid/not found에 해당하는 줄이 있으면 STOP한다
- 상태 전이 기록: kgsl/gmu/iommu driver bind, gpucc `state_synced`, `/dev/kgsl-3d0` 존재, devfreq 목록

## 판정
- 성공(API/적재): 7개 모듈 live, kgsl-3d0/gmu/kgsl-iommu bind, `/dev/kgsl-3d0` 생성, 60초간 모든 검사 통과,
  화면 FB335와 SSH 유지, 새 fault 0건
- 이것은 **GPU 동작 증거가 아니다.** GMU boot, zap(TZ), SQE 적재는 첫 open 시점일 가능성이 높다(추정).
  이 부분은 2단계(open-only 시험)에서 별도로 확인한다.

## 알려진 위험 (가설)
1. KGSL이 probe 단계에서 GMU boot나 zap PAS 호출을 할 가능성을 배제하지 못했다. 소스 미확인.
   → firmware 경로를 미리 설정했다. TZ 거부나 GMU 오류는 STOP 대상이다.
2. `governor_msm_adreno_tz`가 devfreq 시작 시 SCM 호출을 할 수 있다. ADCI/secure call baseline 문제와 겹칠 가능성이 있다.
3. gpucc sync_state가 실행될 것으로 예상한다. 영향은 GPU 영역으로 추정한다. gcc와 ICC는 다른 미bound consumer가 남아 있어 실행되지 않을 것으로 예상한다.
4. 커널 hang(bus-control 사고 유형)은 감시로 막을 수 없다. SSH가 멈추면 사람이 재부팅해야 한다. pstore 보존 신뢰성은 미검증이다.
5. CRC 261개가 미대조 상태다 → 불일치하면 커널이 init 전에 insmod를 거부한다(안전하게 실패).
6. 새로 생기는 kgsl kthread가 D 상태로 idle하면 10초 규칙으로 STOP한다. 모듈은 유지되고, 이후 stack을 검토한다.

## 결과: 1단계 (2026-09-19 01:0x KST, 같은 boot)
- 묶음 `gpu-load-4bebd1cff55e9960`. 첫 묶음 `gpu-load-fd48db47d4fd099c`는 firmware_class.path를 root로 읽는 단계에서 EPERM이 나 attempt 전에 멈췄다(`stop-note.json`).
- firmware 4개를 `/lib/firmware`에 설치했다(사용자 sudo install). 시험 전후 hash가 동일했다.
- **기기 적재 성공**: 7개 모듈 live. kgsl-3d0 → `kgsl-3d`, gmu → `adreno-gen8-gmu`, kgsl-iommu → `kgsl-iommu` bind(component bind 로그 확인).
  devfreq `3d00000.qcom,kgsl-3d0` 등록, iommu group 15–18
- **gpucc sync_state 실행됨**(state_synced 0→1). 그 후 60초 동안 DRM state(FB335), 13개 worker, underrun 13/13, USB Ethernet이 모두 유지됐다.
- probe 단계의 firmware 요청은 0건이다 → GMU/SQE/zap 적재는 **첫 open 시점**이라는 추정과 맞는다.
- 새 커널 fault 0건, GPU 쪽 오류 줄 0건, D 상태는 검토된 3개만 있다. 배터리 100%, 43.5°C
- `/dev/kgsl-3d0`는 없다: `/dev`가 devtmpfs가 아닌 **tmpfs**(수동 node)라서다. sysfs `kgsl-3d0` dev = **495:0** (/proc/devices `495 kgsl`)
- 미검증: GPU 부팅(GMU, zap/TZ, SQE), 명령 실행, 렌더링, 장시간 안정성, 재부팅 후 재현
