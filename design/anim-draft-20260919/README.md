# 유한 GPU 애니메이션 설계 (2026-09-19, 기기 세션)

묶음: `~/y700-agent/vkanim120-ef670d8c85c0838a` (manifest SHA256 ef670d8c85c0838a6bdce9d4aba5a133f471df17cfa97aee81e4b4ca4ed210bc)
상태: host 테스트 10개 통과, `--check` 통과(retained 20 = 이전 19 + FB343 worker 16522). **`--apply` 미실행.**
600프레임판은 `make-manifest.py 600`으로 만든다. 120프레임 결과와 사용자 확인이 있은 뒤에만 만든다.

## 프레임(anim.spv, spirv-val OK, 1692B, ccea8b51…)
- push constant `frame`: 흰 원 고리(반지름 300–320)의 중심이 x = 400 + 12·frame mod 1104로 **좌→우 이동**한다.
  초록 그라데이션은 y + 8·frame으로 **위로 스크롤**한다. 빨강 그라데이션과 파랑 64px 체커는 고정이다. padding 열(1904–1919)은 0이다.
- 깜박임 교대가 아니라 연속 움직임이다(120프레임 동안 고리가 약 1440px 이동한다 → 1104px 주기로 한 번 되돌아간다).

## 동작
- Gate 1: 이전과 같은 Vulkan 경로 + push constant range, **새 dumb A/B**(1904×3040, pitch 7680) + ADDFB2/GETFB2, TEST_ONLY(A, B, FB343)
- Gate 2(보내기 **전에** supervisor를 anim 모드로 전환): k = 1..N
  1. push constant = k, dispatch 120×190, fence(1초)
  2. CPU가 해당 프레임 공식으로 약 1.2k 픽셀과 padding을 검사한다(프레임마다 다른 위치)
  3. **뒤쪽 버퍼**(k 홀수 → B, 짝수 → A. 화면에 없는 쪽)에 memmove한다. 첫/마지막 프레임은 23MB 전체를 byte 비교한다
  4. blocking atomic FB_ID(95+128) + PAGE_FLIP_EVENT → 이벤트 1개를 소비한다(user_data = k, crtc 205, 1초 제한)
- 끝: FB343으로 복귀(flip event), readback → Vulkan destroy → hold(A/B/FB 보존)
- 프레임별 render/copy/flip 시간과 fps를 기록한다(렌더링 + CPU 복사 경로의 실측이다. 60/120fps 목표는 아니다)

## supervisor 검사
- 시작과 끝(복귀 후): DRM state == 검토된 FB343 텍스트와 **정확히 일치**
- 애니메이션 중: 두 FB343 plane이 {343, A, B} 중 **같은** fb에 있어야 한다. 해당 start 줄만 달라도 된다(나머지 줄, 다른 plane, allocated by = python3 모두 정확히 비교)
- underrun 13/13, client 2604, retained 20, USB, 커널/GPU 로그, D 상태. 제한 시간: ENTER 후 N×0.2 + 10초

## 시각 확인
- 고리가 왼쪽에서 오른쪽으로 부드럽게 움직이고(중간에 한 번 왼쪽으로 되돌아간다), 초록 성분이 위로 흐르는지
- 좌우 반쪽 경계 어긋남, 찢어짐, 멈춤, 회색 영역이 없는지 → 끝에 정지 프레임(FB343)으로 돌아오는지

## 결과
- `vkanim120-ef670d8c…`: manifest 구조 버그(anim_review 위치)로 preflight에서 멈췄다. 기기 변경 없음 → 생성기와 supervisor에 키 검사를 추가했다
- `vkanim120-8b87d4cb…`: frame 1 commit 후 STOP. `drmabi.atomic`이 user_data를 0으로 보냈다(사용자: "오른쪽에 작은 원"). worker PID16945가 FB345를 hold → 수정 + FB345 시작 허용
- **`vkanim120-19cb62f5…`: 성공**(사용자: "애니메이션이 잘 된것 같아", `anim-visual.json`)
  - 120/120 프레임, **58.79fps**(2.041초). p50 render 2.59ms / copy+검사 8.77ms / flip 5.32ms / 합 16.66ms(= 120Hz 2 vblank)
  - 최대 41.47ms 1회(copy 33ms 스파이크, 원인 미상)
  - FB343으로 복귀, DRM state 정확히 일치. underrun 13/13(전/후/+30초), 새 커널 로그 0건, D 상태는 검토된 3개만. worker PID17089 hold
- 관찰: 프레임 시간이 vblank 2개에 맞춰 양자화된다(blocking flip). 60fps 상한은 CPU 복사(~9ms) + blocking flip 구조 때문이다
  → 120fps에는 비동기 flip 또는 zero-copy가 필요하다(가설)
