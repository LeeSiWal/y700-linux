# 다음 부팅용 구현 (2026-09-19, 기기 세션, 로컬 작업 — 기기 변경 없음)

현재 부팅(b4e16b26)에서는 **실행하지 않았다.** 이미 적재·보유 상태라 모든 `--check`가 설계대로 거부한다. host 테스트 **41개 통과**.

## 구현 5개
| # | 파일 | 역할 | 검증 |
|---|---|---|---|
| 1 | `bootguard.py`, `extract-templates.py` → `review-templates.json` | PID와 타임스탬프 대신 **템플릿**으로 검토한다: SPMI 경고 블록(58줄, 레지스터 줄만 형식 검사, ≤ 5초, ≤ 3개), hung-task 보고(ADCI/HFI/fence: 결합된 PID + 전체 tail + 모듈 build-id 실물 대조), 패널 메시지 2개(msm_drm insmod 스레드 한정) | `test_bootguard` 20개: **두 부팅(b4e16b26, 36694bab)의 실제 dmesg가 오탐 0**. 부정 13개 모두 거부 |
| 2 | `bootmon.py`, `load-stage.py`, `make-boot-bundle.py` | pstore / display / gpu 단계를 새 boot_id로 manifest화하고 insmod한다. 대기 작업은 stack 템플릿으로 **tick마다 결합**한다(HFI/fence는 적재 후 130초 안에 결합되어야 한다). gpu 단계는 gcc/ICC 미bound consumer가 남아 있는지 확인한다 | `test_loadstage` 5개(시뮬레이션 preflight, 변조, 선행 조건) |
| 3 | `display-owner.py`, `drmkms.py`, `ownerlog.py`, `run-owner.py` | card0를 **직접 여는 유일한 DRM master**. splash 토폴로지를 확인한다(connector sysfs 69, CRTC 205, primary 두 개 95/128, 20 planes, timing 정확). commit A = screen-small과 같은 modeset, commit B = native two-plane. 각각 TEST_ONLY → 게이트 → readback. 영구 보유하며 **registry** 작성 | `test_owner` 9개. ioctl 번호 8개 = 커널 값. **실기 읽기 검증 통과**(`validate-drmkms/`, 아래) |
| 4 | `gpu-keeper.py`, `keeperlog.py`, `run-keeper.py`, `registry.py` | sysfs **동적 major**로 `/dev/kgsl-3d0`를 만든다 → 첫 open → DEVICE_INFO chip **0x44050a31** → fd 영구 보유 → GPU registry. owner 화면 state, client, underrun을 매 tick 확인 | `test_keeper` 4개 |
| 5 | `run-vk.py`, `make-vk-bundle.py`, `vkanim.py`, `animlog.py`, `borrow_owner.py`, `drmabi.fb_ids` | compute / anim120 / anim600을 registry 기준으로 만든다: owner fd borrow, native FB 기준, 기존 FB 목록(GETRESOURCES)과 겹치지 않는 새 FB, `start=` 줄 동적 탐지, hold된 이전 test worker 자동 포함 | `test_vk` 3개(이번 부팅 실제 state 텍스트 2종) |

## 다음 부팅 실행 순서 (각각 `--check` 후 사용자 sudo 1회)
```
cd ~/y700-design/nextboot-impl
python3 -B make-boot-bundle.py pstore   → cd <출력 폴더> → python3 -B load-stage.py --check → sudo python3 -B load-stage.py --apply
python3 -B make-boot-bundle.py display  → (같은 방식)                    # 약 5분(HFI/fence 결합 + 130초 관찰)
python3 -B make-boot-bundle.py owner    → python3 -B run-owner.py --check → sudo … --apply   # 화면: 중앙 작은 패턴 → 전체 컬러바(육안 확인)
echo schedutil | sudo tee /sys/devices/system/cpu/cpufreq/policy0/scaling_governor /sys/devices/system/cpu/cpufreq/policy6/scaling_governor
python3 -B make-boot-bundle.py gpu      → load-stage.py
python3 -B make-boot-bundle.py keeper   → run-keeper.py
python3 -B make-boot-bundle.py touch    → load-stage.py   # 전제: /lib/firmware/novatek_ts_{fw,mp}.bin(3a1d6dac에서 설치됨, 영구) + keeper registry
sudo python3 -B touch-events.py 40      # /dev/input/event* node를 sysfs로 mknod 후 캡처(읽기 전용, grab 안 함). 결과 json은 이 폴더에 생긴다
python3 -B make-vk-bundle.py compute    → run-vk.py
python3 -B make-vk-bundle.py anim120    → run-vk.py   # 육안 확인 후 anim-visual.json 기록 → anim600 가능
```

## 남은 위험과 한계 (가설 포함)
- **commit B는 새 조합이다**: 최종 속성 값은 b4e16b26에서 검증된 값과 같지만, 여러 commit이 아니라 한 번에 적용한다. TEST_ONLY와 육안 확인으로 막는다.
- Python owner가 modeset을 하는 것은 처음이다. ioctl 구조는 C와 같지만 실기 검증은 `validate-drmkms`(읽기 전용)까지만 한다.
- fence listener hung-task tail은 **파생 템플릿**이다(실제 보고를 본 적이 없다: hung_task_warnings 예산 10개가 ADCI/HFI에 소진). 불일치하면 STOP한다 → 그때 검토한다.
- SPMI 템플릿은 레지스터/pstate/sp 줄의 **값**을 비교하지 않는다(형식만 본다). 나머지 줄(모듈 목록, 호출 stack 전체)은 정확히 비교한다.
- hung-task 경고 예산(기본 10)이 부팅마다 ADCI에 쓰인다. 예산 안에서 HFI 보고가 나오면 허용하고, 억제된 뒤에는 보고가 없다.
- probe/render 전용 묶음은 일반화하지 않았다(compute와 anim이 그 경로를 포함한다).
- 종료 과정(마지막 DRM/KGSL close)은 여전히 미검증이다.

## 실기 읽기 검증 결과 (validate-drmkms, b4e16b26, 2026-09-19)
- `drmkms` 읽기 함수가 실제 커널에서 동작했다: driver `msm_drm`, dumb cap 1, connector(sysfs) 69 → CRTC 205 ACTIVE 1,
  plane 20개, **95/128 모두 type 1(primary)**, possible_crtcs 0xff, 다른 plane 바인딩 없음
- 현재 mode blob 340 = "1904x3040x120vid", timing이 `REVIEWED_TIMING`과 일치(`hskew 0, vscan 0, type 8`)
- borrow한 복제 fd만 닫았다. 화면 변화 없음
- 아직 실기에서 쓰지 않은 owner 호출: SET_MASTER, SET_CLIENT_CAP, CREATEPROPBLOB, ALLOW_MODESET commit(모두 새 부팅에서 처음 실행)

## 새 부팅 3a1d6dac 실행 기록
- pstore: STAGE_PSTORE_LOADED(ADCI 결합, ramoops bind)
- display: STAGE_DISPLAY_LOADED. HFI(1076)와 fence(1183) 결합, ADCI hung ×2, **HFI hung ×1을 템플릿으로 허용**, 패널 2줄은 insmod T1360
- owner 1차(`boot-owner-1b5f…`): 기준 관찰 중 STOP(패널 허용 줄이 다음 supervisor로 전달되지 않음). 변경 없음 → `reviewed_lines`를 manifest로 전달하도록 수정
- owner 2차(`boot-owner-a559…`): **owner commit A/B 모두 반환, OWNER_READY**(fb native 335, small 336, blob 337, PID1895 hold).
  supervisor는 registry 단계에서 STOP(underrun을 가진 encoder status가 6개 → 선택 오류) → GETCONNECTOR로 DSI encoder를 고르도록 수정.
  `ownerfinish` 단계로 registry를 작성한다
- ownerfinish: registry 작성(encoder 68, **underrun 0/0**, native FB335). 사용자 확인 "컬러바는 그대로야" → `owner-visual.json`
- governor schedutil(사용자 sudo)
- gpu: STAGE_GPU_LOADED(7개, kgsl/gmu/iommu bind, gpucc sync_state만 실행, 495:0). 화면 state 불변
- keeper: GPU_KEEPER_READY(첫 open 80.4ms, chip 0x44050a31, PID2583)
- vk compute: VK_COMPUTE_DONE(fence 5.17ms, 256/256 + 256/256, destroy 완료)
- vk anim120: VK_ANIM_DONE(**58.55fps**, p50 render 2.59 / copy 8.67 / flip 5.39ms, max 49.75ms 1회). native로 정확히 복귀
  사용자 확인 "애니매이션도 잘나왔어" → `anim-visual.json`
- **결론: 재부팅 후 절차 전체를 재현했다**(장치 fault 0건). 수정 3건: reviewed_lines 전달, GETCONNECTOR로 encoder 선택, 환경 의존 테스트

## touch 단계 (3a1d6dac에서 추가·검증)
- `make-boot-bundle.py touch`: `spi-msm-geni.ko` 1개(REVIEWED EXTRA 소스, sha 3906bf08…). 전제: msm_drm, nvt_36xxx, panel_event_notifier, qcom_ipc_logging, msm_gpi, msm_kgsl.
  firmware 2개 해시, SPI 장치/컨트롤러 미bind, gcc/ICC에 SPI 외 미bound consumer 존재를 확인한다. **매 tick owner native state, underrun, keeper**를 확인한다(`bootmon.extra`)
- 결과(3a1d6dac): spi19.0 → NVT-ts, touch fw 다운로드 128.7ms, input1 touch / input2 pen, 화면 불변
- 매핑: touch = framebuffer ×10, 방향 동일(`~/y700-design/touch-20260919/README.md`)
- `touch-events.py`는 실행할 때마다 새 폴더에 복사해서 쓴다(결과 json을 덮어쓰지 않음)

## 다음 부팅 전체 순서 (2026-09-19 2차 갱신: 센서, 전원, 절전 포함)
정적 검증: `python3 -B check-runbook.py` → 3a1d6dac 부팅 직후 모듈 144개에서 시작, 단계별 requires/`depends` 순서/파일 해시 확인
→ **RUNBOOK OK, 최종 227개**. host 테스트 **91개** 통과.

각 단계: `python3 -B make-boot-bundle.py <stage>` → 출력 폴더에서 `python3 -B <runner> --check` → `sudo python3 -B <runner> --apply`(사용자 sudo 1회)
모든 묶음은 USB 랜(enx<MAC>) 연결을 매 tick 확인한다 → **랜은 21번(S-2b) 직전까지 뽑지 않는다.**

| # | stage | runner | 내용 / 확인 |
|---|---|---|---|
| 1 | pstore | load-stage.py | ramoops |
| 2 | display | load-stage.py | 모듈 15개, 약 5분 |
| 3 | owner | run-owner.py | 작은 패턴 → native 컬러바(육안). encoder 선택 수정본은 새 부팅에서 처음(실패하면 ownerfinish) |
| – | (governor) | `echo schedutil \| sudo tee …policy0/…policy6/scaling_governor` | |
| – | (전원, 선택) | `sudo python3 -B chgctl.py` (별도 터미널, Ctrl-C로 종료 시 1 복원) | 충전기 연결 시 80% 이상이면 충전 정지(배터리 휴지, USB 전원 동작). `chgwatch.py`는 읽기 전용 기록 |
| 4 | gpu | load-stage.py | KGSL 7개 |
| 5 | keeper | run-keeper.py | GPU fd 보유 |
| 6 | touch | load-stage.py | NVT-ts |
| 6a | adc | load-stage.py | PMIC ADC(qcom-vadc-common, qcom-spmi-adc5-gen3) → NTC 온도 영역 15개, 충전 드라이버가 USB 커넥터 온도를 읽기 시작(3a1d6dac: 35~36 °C). 충전 전에 올린다 |
| 7 | wifi-a | load-stage.py | PCIe/MHI/cnss2 |
| 8 | wifi-b | load-stage.py | fs_ready=1 → 캘리브레이션 → cld3 → **wlp1s0** |
| – | (Wi-Fi 접속) | `sudo nmcli con up y700-wifi-test` | 비밀번호 저장됨, never-default |
| 9 | bt-a | load-stage.py | msm_geni_serial + btpower |
| 10 | bt-b | load-stage.py | bluetooth/hci_uart |
| 11 | btkeeper | run-btkeeper.py | UART clock vote, rampatch+NVM, H4 → **hci0** |
| 12 | qrtr-smd | load-stage.py | IPCRTR |
| 13 | adsp | run-adsp.py | pdmapper + ADSP start |
| 14 | audio-c1 | load-stage.py | GPR/SPF/PRM |
| 15 | audio-c | load-stage.py | **c2+c3+c4 통합(22개)** → **card0 필수** |
| 16 | audio-d5 | load-stage.py | 스피커 확인(L/R 삐 + WAV, 청취) |
| 17 | frpc | load-stage.py | FastRPC (fastrpc-adsp-secure, fastrpc-lpass2000) |
| 18 | sensors | run-sensors.py | **이번 부팅의 첫 attach에서** 쓰기 가능한 persist 사본으로 hexrpcd → SSC 탐색. (3a1d6dac에서는 첫 attach 때 쓰기가 거부돼 PD 초기화가 멈췄고 RESTART_PD는 거부됨) |
| 19 | susp-freezer | run-suspend.py | S-1: pm_test=freezer (3a1d6dac 성공) |
| 20 | susp-devices | run-suspend-dev.py | S-2a: pm_test=devices, 랜 연결 상태. 3a1d6dac: 디스플레이/터치/Wi-Fi 절전·복귀 후 **xhci -22로 중단**, DRM 플래그 3줄 차이 → STOP → 사용자 확인 후 `rebase-display.py`(sudo)로 재기준 기록. 새 부팅에서는 synx 줄 타임스탬프가 달라 **rebase-display.py의 SYNX/S2A 경로를 그 부팅 결과로 새로 검토**해야 한다 |
| 21 | susp-devices | run-suspend-dev.py | S-2b: **마지막 단계**. SSH를 Wi-Fi(.78)로 옮기고 랜을 뽑은 뒤 묶음 생성(빌더가 랜 부재를 보고 S-2b 형식으로 만든다). 다음 방해 요소는 BT UART vote(예상) |
- 선택: blescan(BLE 스캔)

### 재부팅 후에도 남는 것 (설치 완료)
- /lib/firmware: gen80200_* 4개(GPU), novatek_ts_{fw,mp}.bin(터치), peach/ 10개 + wlan/qca_cld/…/WCNSS_qcom_cfg.ini(Wi-Fi), adsp*.mdt/bNN 58개(ADSP), aw882xx_acf.bin(앰프)
- BT firmware(brhbtfw20.tlv, brhbtnv20.bin)는 btkeeper 묶음 안에 들어 있다(/lib/firmware에 설치하지 않음)
- NetworkManager 연결 `y700-wifi-test`(autoconnect no, 비밀번호 저장됨). 같은 이름의 중복 프로필이 3개 있다(정리는 별도 승인)
- sensors-20260919/persist 사본(쓰기 가능 overlay의 원본), power-20260919/ 기록, suspend-20260919/ 기록
### 재부팅하면 사라지는 것
- 모든 모듈, /dev 수동 노드, owner/gpu/bt keeper, pdmapper, hexrpcd, ADSP 실행 상태, governor, 밝기(1536), charging_enabled 값,
  chgwatch 기록 프로세스, 이번 부팅의 실패 BT keeper 7개(전원 OFF 상태), registry-rebase-3a1d6dac-s2a(부팅 id가 달라 적용되지 않음)
### 새 부팅에서 처음 확인되는 것 (가설/위험)
- owner의 GETCONNECTOR encoder 선택(3a1d6dac에서는 ownerfinish로 보완)
- audio-c 통합 순서(이번에는 4단계로 나눠 올렸다)
- sensors: 첫 attach부터 쓰기 가능 사본을 주면 레지스트리 초기화가 끝나 SSC가 센서를 보고하는지
- 로그 버퍼 순환: bootguard의 잘린 머리 규칙(승인됨)이 필요하다
### 전원/절전에서 알게 된 것
- 우분투에서는 70/80% 제한이 적용되지 않는다(안드로이드 서비스 부재). charging_enabled=0은 충전기가 있을 때만 약 1초 뒤 적용, 배터리 전류 0
- usb1-conn-therm: adc 단계(6a) 뒤에는 충전 드라이버가 USB_TEMP를 읽는다(그 전에는 커넥터 과열 보호 없음). PMIC temp-alarm 9개는 아직 미적재(critical trip 검토 필요). 전원 버튼(pon pwrkey) 드라이버 미적재. logind HandlePowerKey=poweroff
- 밝기 1024 ≈ 3.1 W, 4095 ≈ 6.0 W(배터리 전원, 유휴)
- wakeup_count를 읽으면 활성 wakeup 소스가 있는 동안 멈춘다(읽지 말 것)

### 재부팅 직전 기록 (boot 3a1d6dac, uptime 50624 s)
- 모듈 229개(adc 포함), 배터리 100% Full, charging_enabled=1, 34.3 °C, 충전기 연결, USB 랜 연결(SSH .77)
- 새 부팅 첫 작업(읽기 전용): boot_id/slot/root/모듈 144개 확인, D 상태 wait 재검토, dmesg fault 검토,
  pstore에 3a1d6dac 종료 기록이 남았는지 확인(sudo) → 그다음 1번 pstore부터 이 표 순서대로. 묶음은 모두 새 부팅에서 다시 만든다.

### boot 8e18ad1f (재부팅 후, 2026-09-19 17:05)
- 기준: slot _a, root 179:3, 모듈 144, D 대기 adci(119), SPMI 경고 3개(기존 템플릿).
- 새 부팅 패턴: dwc3_msm probe 지연(-517) 재시도 14회마다 "sysfs: cannot create duplicate filename '/class/hub'" + Call trace
  (13.01~13.03 s, T78, 정규화 시 1종 30줄). 사용자 승인("dwc3 템플릿 승인하고 진행해 줘")으로 review-templates.json에
  dwc3_hub 템플릿 추가(≤20개, ≤20 s, 단일 스레드, 정확 일치, dwc3_msm build-id). test_dwc3hub.py 5개.
  (test_bootguard/test_loadstage 일부는 이전 부팅의 적재 모듈을 전제로 해서 새 부팅 직후에는 실행 불가: 환경 의존)
- bt-a 1차 묶음(24a11fec)이 기준 관찰 중 STOP: 링 버퍼가 SPMI 블록 1·2를 밀어내는 순간 3번째 블록이 템플릿 순서와 어긋남(오탐, attempt 없음, 적재 없음 → 삭제).
  사용자 승인("잘린 머리 규칙 확장 승인하고 진행해 줘")으로 bootguard 확장: SPMI 생존 블록을 템플릿 끝에 맞춰 비교 +
  rotated_template_head(버퍼 index 0의 단일 스레드 조각이 SPMI/dwc3 템플릿 블록의 정확한 접미부일 때만, 시간·build-id 조건 유지, audit 기록).
  test_dwc3hub.py 12개(실제 3a1d6dac SPMI 줄 사용).

### boot 8e18ad1f 전체 재현 결과 (2026-09-19) [장기/재부팅 검증]
- 1 pstore → 2 display → 3 owner(encoder 수정본 1회 성공, enc 68) → governor → 4 gpu → 5 keeper → 6 touch → 6a adc →
  7 wifi-a → 8 wifi-b(STA 이름 wlan0) → 9 bt-a(1차 묶음은 링버퍼 오탐 STOP, 미적재·삭제 후 규칙 확장) → 10 bt-b →
  11 btkeeper(1회 성공, hci0) → 12 qrtr-smd → 13 adsp(펌웨어 same) → 14 audio-c1 → 15 audio-c 통합(card0, 1회 성공) →
  16 audio-d5(청취 확인) → 17 frpc → 18 sensors ❌(원인: >256 B 역호출 입력 미처리 → get_in_bufs2 구현, 다음 부팅 검증) →
  19 S-1 freezer ✅(5.38 s). 20 S-2a는 결과가 알려져 있어 생략(rebase-display.py 일반화 필요), 21 S-2b 보류.
- 부팅 문제: dbus-daemon이 부팅 초기에 /dev/null을 못 열어 5회 실패 → NetworkManager 미기동(수동 start로 복구).
  polkit, systemd-remount-fs도 failed. Bootstrap의 /dev 준비 순서 문제로 추정(간헐적, 3a1d6dac에서는 정상).
- 새 부팅 템플릿: dwc3_hub(승인), SPMI/dwc3 잘린 머리 확장(승인).
