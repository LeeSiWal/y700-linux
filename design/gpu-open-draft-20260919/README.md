# KGSL 2단계: 첫 open 시험 설계 (2026-09-19, 기기 세션)

묶음: `~/y700-agent/gpu-open-c1810de27aec2d85` (manifest SHA256 c1810de27aec2d85c24267a232b98755a6d3b7ed0676d975e843564ba1a247fd)
상태: host 테스트 8개 통과, `--check` 통과. **`--apply`는 아직 실행하지 않았다.** 빌드 불필요(worker가 Python이다).

## 동작
1. 기준 관찰 15초를 한다. 1단계와 같은 매 tick 검사다: 167개 모듈 identity, 검토된 D wait, 커널 fault, GPU 오류 줄, 화면(FB335, worker 13개, 소유자, underrun 13/13), USB Ethernet.
2. `mknod /dev/kgsl-3d0 c 495:0`, 0600 root로 만든다. sysfs dev가 495:0이 아니거나 node가 이미 있으면 STOP한다.
3. worker `kgsl-open.py`(root, 별도 session)를 띄운다 → READY 후 2초 조용한 상태를 확인하고 GO를 보낸다.
4. worker가 `/dev/kgsl-3d0`를 한 번 O_RDWR로 연다. **이 open에서 첫 GPU boot가 일어난다**: GMU fw, SQE/AQE, zap(TZ PAS).
   - `IOCTL_KGSL_DEVICE_GETPROPERTY`(0xC0180902)로 `KGSL_PROP_DEVICE_INFO`만 읽는다. chip_id가 0x44050a01이 아니면 STOP한다.
   - 메모리 할당, context, 명령 제출 ioctl은 없다. **fd는 어떤 결과에서도 닫지 않는다**(worker는 SIGINT/TERM/HUP 무시).
5. GO 후 30초 안에 DEVINFO가 나와야 한다. HELD 이후 60초를 관찰하고 결과를 저장한다.

## 판정
- API 성공: open 성공(소요 ms 기록), DEVINFO chip_id 일치, 60초간 모든 검사 통과, GPU 쪽 오류 줄 0건, 화면/SSH 유지
- 이것은 **GPU boot 경로(GMU/zap/SQE)가 오류 없이 통과했다는 근거**다. 명령 실행이나 렌더링의 증거는 아니다.

## 위험 (가설)
1. GMU boot 실패나 zap PAS 거부 → KGSL 오류 로그 → STOP. worker는 보존되고, 모듈 unload와 재시도는 없다.
2. TZ/SCM 호출 hang → worker D 상태 10초 → STOP. 커널이 전체적으로 멈추면 사람이 재부팅해야 한다.
3. GMU/hwsched가 synx/hw_fence 경로를 초기화한다 → 이전 synx SSR 이력과 관련될 수 있다. msm_hw_fence 검토된 wait의 stack이 바뀌면 STOP한다.
4. open 직후 GPU가 idle slumber/IFPC로 들어가는 것은 정상 동작으로 예상한다. 전원 전이 중 오류 로그가 나오면 STOP한다.
5. 정상인데도 오류 regex에 걸리는 KGSL 메시지가 있을 수 있다(보수적 STOP). 그 경우 로그를 검토한다.

## 결과 (2026-09-19 01:1x KST, 같은 boot)
- 묶음 `gpu-open-c1810de27aec2d85`: node를 만들고, 첫 open(77.4ms)과 DEVICE_INFO가 성공했다.
  supervisor는 **chip_id 기대값 오류**(DT 0x44050a01 ≠ 실제 0x44050a31, Adreno 840)로 STOP했다 → `stage2-review.json`
- 사후 점검
  - 비root(+44s): fault 0건, GPU 오류 줄 0건
  - root `postcheck.py`(+130s): DRM state == FB335 기준, client는 kms-held 2604 하나, underrun 13/13, retained 13개 OK, kgsl worker PID13398이 `/dev/kgsl-3d0` fd 보유
- 순간적인 D: jbd2/mmcblk1p3, systemd-journal, flush-179:0이 1회 관찰됐다.
  이후 12초간 8회 표본에서는 0건이었다. IO pressure avg10/60/300 ≈ 3.3/3.3/3.2%로 5분 평균과 같다 → SD writeback 순간 대기로 판단한다(가설). 계속 감시한다.
- API 성공: GPU 첫 boot 경로. 미검증: 명령 제출, 렌더링, 장시간, 재부팅 재현
