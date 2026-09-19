# GPU 렌더링 결과를 화면에 표시 — 설계 (2026-09-19, 기기 세션)

묶음: `~/y700-agent/vkrender-65f15832a3ed7457` (manifest SHA256 65f15832a3ed7457c7a83f65ddf874c8ff2dad1b37d7f68b21feeefd9122331b)
상태: host 테스트 12개 통과, `--check` 통과(retained 18). **`--apply` 미실행.** C 빌드 불필요(DRM도 Python ioctl).

## 경로 선택
- turnip(KGSL)은 `VK_KHR_display`를 지원하지 않는다. dma-buf 공유에는 dma_heap node와 msm_drm PRIME이 필요하고, cached dumb에 GPU가 직접 쓰면 캐시 hazard 검토가 필요하다.
  → 1차는 **GPU compute로 프레임 생성 → CPU 복사 → 새 cached dumb buffer → 검증된 two-plane FB_ID 전환**으로 한다.
  graphics pipeline과 zero-copy는 이후 단계로 미룬다.
- DRM은 PID2604의 파일을 borrow한다(`borrow.py`, 이전과 같다). 새 DRM open과 close는 없다. ioctl 구조와 번호는 kms-held.c와 같은 호출을 Python struct로 재현했다(크기와 번호는 테스트로 고정).

## 프레임(render.spv, spirv-val OK, 1320B, 09ebc74a…)
- 1904×3040 XRGB8888, pitch 7680. local 16×16, dispatch 119×190(정확히 나누어떨어진다)
- r = x·255/1903(좌→우 빨강), g = y·255/3039(위→아래 초록), b = 64px 체커(64/255), 중앙(952,1520) 반지름 600–620 **흰 원 고리**
- 검증: CPU 참조 공식(pattern.py)과 약 7만 픽셀(97픽셀 간격 + 네 변 전부 + 고리 4점)을 비교하고, sentinel(0xEF) 잔존 0을 확인한다.
  dumb에 복사한 뒤 23,347,200바이트 전체 byte 비교를 한다.

## 단계
- Gate 1: turnip compute(제출 1회, fence 5초) → 검증 → CREATE_DUMB(1904×3040×32, pitch/size 고정 확인) → mmap → memmove → byte 비교
  → Vulkan 전부 destroy(검증된 close 경로) → ADDFB2 XR24/linear + GETFB2 확인 → TEST_ONLY(planes 95+128 FB_ID만)
- Gate 2: **blocking atomic commit 1회**(geometry 불변) → property readback(두 plane이 새 fb, 1:1 반쪽) → hold
- 이후 화면은 GPU 프레임을 유지한다. FB335와 기존 worker는 그대로 보존한다. 복귀는 별도 단계로 한다.

## supervisor 검사
- 이전과 같음 + **DRM state 모드**: commit 전에는 FB335 텍스트와 정확히 같아야 한다.
  commit 이후에는 FB335 두 plane의 `fb=`와 해당 `start=` 줄만 새 값이 허용되고, 나머지 줄은 전부 정확히 같아야 한다(다른 plane 변화는 STOP).
- underrun 13/13, DRM client는 2604 하나, retained 18, USB Ethernet, 커널/GPU 로그, D 상태

## 시각 확인 포인트(사용자)
- 왼쪽 위는 어두운 파랑, 오른쪽으로 갈수록 빨강, 아래로 갈수록 초록이 강해진다(오른쪽 아래는 노랑 계열). 전체에 64px 파랑 체커가 있다.
  중앙에 흰 원 고리(지름 약 1220px)가 있다. 좌우 반쪽 경계(x=952)에 어긋남이 없어야 한다.

## 1차 실행 결과와 수정 (vkrender-65f15832a3ed7457)
- GPU 렌더링은 성공했다: 23,152,640B 버퍼, 제출 1회, fence 6.22ms, 샘플 69,565개 불일치 0, sentinel 0. 새 커널 로그 0건, fault 0건
- **STOP(설계 오류)**: dumb pitch 7680B = **1920px** row stride(1904×4 = 7616이 아니다). 크기 확인에서 멈췄다 → 복사, ADDFB, commit 없음. 화면 불변
  - worker PID16404: Vulkan 객체와 추가하지 않은 dumb buffer 1개를 보유한 채 hold
- 수정: shader index `y*1920+x`, x ≥ 1904 padding은 0으로 기록, dispatch 120×190, 버퍼 23,347,200B(= dumb size).
  CPU 검증에 padding 열 확인을 추가했다. render.spv b76f0e5f…(spirv-val OK)
- 새 묶음: `vkrender-a9156afd13ae757f`(manifest a9156afd…ab370), host 12 OK, `--check` OK(retained 19)

## 2차 실행 결과 (vkrender-a9156afd13ae757f) — 물리 동작 확인
- worker 전 단계 성공: fence 6.24ms, 검증 통과, dumb 복사 byte 동일, **FB343** 등록, TEST_ONLY, blocking commit 반환, readback(95/128 → FB343, geometry 불변)
- **사용자 확인: "잘나오고 있어"** → `render-visual.json`
- supervisor는 race로 STOP했다: COMMIT_ENTER 줄을 파싱한 뒤에야 state 모드를 바꿔서, 그 사이 tick이 commit된 state를 exact 모드로 검사했다.
  장치 fault가 아니다(새 커널 로그 0건, D 상태는 검토된 3개만, worker PID16522 hold).
  → 초안 수정: gate 2 GO를 보내기 **전에** transition 모드로 바꾼다(다음 묶음부터 적용)
- 30초 관찰을 대신해 root `postcheck.py`(읽기 전용, 30초 10회 표본)를 두었다
- root postcheck(30초 10회): underrun 13/13, client 2604, retained 19 OK, carrier 1. `moved_to` False
  → `diffstate.py`로 확인: 차이는 fb=343, **`allocated by = python3`**(FB 생성 프로세스 이름), obj start뿐이다. 나머지는 모두 동일
  → matcher가 creator 줄을 모르던 버그였다. 초안에서 해당 줄만 `python3`으로 정확히 허용하도록 수정했고, 실제 post-commit 텍스트로 테스트했다(13개 OK)
  → `state-review.json`: 화면 state는 의도대로 검증됐다(FB343, planes 95+128, 1:1, mode 340)
