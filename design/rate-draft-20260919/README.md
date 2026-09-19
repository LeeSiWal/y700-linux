# 연속 flip 속도 시험 설계 (2026-09-19, 기기 세션)

상태: 로컬 분석과 host 테스트(14개 통과)까지. `rate-held.c`는 **미빌드**, 기기 실행 없음.

## 설계
- **새 할당과 픽셀 쓰기가 없다.** flip 시험이 만들고 CPU readback과 사용자 확인을 거친 FB341(A)/FB342(B)를 재사용한다.
  worker와 supervisor는 모두 같은 drm_file(client 2604)을 공유하므로 FB id가 유효하다. 시작할 때 GETFB2로 크기, pitch, format, modifier를 재확인한다.
- 단계: TEST_ONLY(A, B, FB335) → GO gate → blocking atomic commit을 **쉬지 않고** steps회 수행한다(A/B 교대, plane95+128 FB_ID만 변경).
  매번 FLIP_COMPLETE를 200ms 안에 소비한다. 마지막 1회는 FB335로 복귀한다.
- 측정: CLOCK_MONOTONIC. 루프 안에서는 출력하지 않는다. 끝난 뒤 요약과 단계별 완료 시각을 출력한다.
  간격이 12.5ms를 넘으면 slow(놓친 프레임)로 센다. vendor event의 seq/ts는 쓰지 않는다(flip 시험에서 1/0 고정 확인).
- 판정: API 성공 = 전 단계 완료 + FB335 복귀 + 최종/30초 state 정확 일치 + underrun 13→13.
  fps와 slow는 **측정값**이며 합격/불합격 조건이 아니다.
- 안전장치: supervisor가 0.25초마다 underrun과 task/dmesg를 확인한다. underrun이 늘거나 deadline(steps×20ms+10s)을 넘기면
  SIGINT를 보내고, worker는 다음 commit 전에 멈춰 hold한다. 이때 A/B 중 하나가 화면에 남을 수 있다.
- 단계적 진행: `make-manifest.py --steps 120`(약 1초)을 먼저 한다.
  `--steps 1200`(약 10초)은 120 결과가 완료 상태이고 `rate-visual.json`이 있으며 같은 binary일 때만 만들어진다.
- retained 목록에 flip worker PID7383/start610451을 추가한다(1200은 120 worker도 추가).

## 주의
A/B는 막대 순서가 반대인 화면이라 60Hz 전체 화면 깜박임이 생긴다. 광과민성이 있으면 화면을 직접 응시하지 말 것.
관찰 포인트는 찢어짐, 회색 영역, 좌우 반쪽 어긋남, 끝난 뒤 native 복귀다.

## 한계
- blocking commit이라 측정값은 CPU 렌더링 없는 표시 경로 상한이다. 게임/GUI 성능의 증거가 아니다.
- supervisor의 0.25초 점검(dmesg, task scan)이 같은 CPU에서 돌아 timing에 약간 영향을 줄 수 있다.

## 결과: 120회 (2026-09-19 00:35 KST)
- `screen-rate120-101c51d321778fce`, worker PID7798/start653971 보존
- API 성공이다. 복귀 후와 +30초의 state가 기준과 정확히 일치했고, underrun은 13→13(샘플 109개)이다. 새 fault 0건, D 상태는 검토된 3개만 있다.
- 측정: 119.98 fps, 평균 8.334ms, 범위 8.170–8.443ms, 12.5ms 초과 0건
- 물리 확인: 사용자가 깜박임과 native 복귀를 확인했다 → `rate-visual.json`
- 1200회 묶음 생성: `screen-rate1200-b452a79e83e608a1` (같은 binary, retained 13개)

## 결과: 1200회 (2026-09-19 00:38 KST)
- `screen-rate1200-b452a79e83e608a1`, worker PID8168/start670904 보존
- API 성공이다. 복귀 후와 +30초의 state가 정확히 일치했고, underrun은 13→13(샘플 139개)이다. 새 fault 0건, D 상태는 검토된 3개만 있다.
- 측정: 119.99 fps / 9.99초. p50 8.334ms, p99 8.440ms, 최대 10.890ms(1회), 12.5ms 초과 0건
- vsync IRQ가 253에서 1458로 늘었다(+1205, commit 1201회와 대응).
- 물리 확인: 사용자가 10초 깜박임과 native 복귀를 확인했다 → `rate-visual.json`
