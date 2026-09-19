# 유한 두-plane flip 시험 초안 (2026-09-19, 미빌드·미실행)

상태: **로컬 초안.** 컴파일, host 테스트, 기기 실행을 하지 않았다. 기기에는 컴파일러가 없으므로
Mac의 NDK clang(`aarch64-linux-android35-clang -static -Os -Wall -Wextra -Werror`)으로 빌드한다.
빌드한 뒤 `y700-agent.py`의 manifest, health_guard, borrow, supervisor 체계에 넣어 one-shot 묶음으로 만든다.

## 범위
- 보존 대상: 기존 worker 11개, FB78, FB335. 새 DRM open과 기존 FD close를 하지 않는다.
- 새 버퍼: cached dumb 1904×3040 A/B 두 개를 쓴다. 두 버퍼 모두 commit 전에 채우고 CPU 전체 readback으로 확인한다. 그 뒤에는 **다시 쓰지 않는다.**
  따라서 scanout 중인 버퍼를 CPU가 덮어쓰는 경우는 구조적으로 없다.
- 각 단계: plane95와 plane128의 **FB_ID만** 한 atomic에서 함께 바꾼다. geometry는 native와 같다.
  blocking commit에 PAGE_FLIP_EVENT를 붙이고, FLIP_COMPLETE를 1초 안에 1개 읽어 user_data와 crtc205를 확인한다.
- 횟수: A/B를 500ms 간격으로 8회 바꾼 뒤 FB335로 돌아간다. 마지막 화면은 사용자가 이미 확인한 상태와 같다.
- TEST_ONLY는 A, B, FB335 세 전이를 모두 사전에 검사한다. 이 검사는 GO gate 앞에 둔다.
- 오류, 타임아웃, 신호가 생기면 hold한다. restore, close, retry는 하지 않는다.

## 이벤트 소비 가정 (검토 필요)
pidfd_getfd로 빌린 FD는 같은 struct drm_file을 공유한다. 이벤트도 이 file의 큐로 들어간다.
기존 worker들은 hold()의 sleep 루프에 있고 read()를 부르지 않는다. 이 가정은 소스로 확인했다
(native/small kms-held.c). 다른 retained binary(caps 등)의 소스는 Mac에서 다시 확인해야 한다.

## 판정 구분
- API 성공: 9회 FLIP_DONE, 매 단계 property readback 일치, 최종 FB335.
- 물리 확인: 사용자가 A/B 교대(막대 방향 반전, 중앙 검은 사각형)와 최종 native 화면을 육안으로 확인.
- 이 시험은 120fps 렌더링이나 장시간 안정성의 증거가 아니다. 속도 시험(연속 flip, NONBLOCK)은 별도 단계로 한다.

## supervisor에 추가할 검사 (Mac 작업)
- 이전 시험과 같은 static/health_guard/state 비교를 한다. expected state는 FB335 기준으로 새로 생성한다.
- 매 FLIP_DONE마다 underrun counter(encoder status)가 증가하지 않았는지 확인한다.
- 전체 제한 시간은 약 30초로 두고, 넘으면 STOP하며 자원을 보존한다.

---
## 2026-09-19 진행 (기기 세션, 로컬 분석/host 테스트만)

### 확인한 근거 (로컬 분석)
- retained worker 11개의 실행 binary SHA256과 `.c` SHA256이 각 묶음 manifest와 일치한다.
  소스상 read 계열 호출은 stdin gate의 `fgets` 한 곳뿐이다. caps.c에는 그 호출도 없다.
  hold()는 `for(;;)sleep(1)`이므로 **FLIP_COMPLETE 이벤트는 flip-held만 소비한다.**
- native 묶음의 snapshot을 보면 DRM state 텍스트가 commit 직후와 30초 후에 동일하다. before-worker와 before-commit의 state도 expected-initial과 같다.
  → `expected-state.txt`(= native after-30s state, sha256 424d0c52…)를 시작, TEST_ONLY 후, 종료 상태 **모두**의 정확 비교 기준으로 쓴다.
- underrun counter 형식: `intf:N vsync: X underrun: Y mode: video`. 기준값은 intf1=13, intf2=13.

### 파일
| 파일 | 상태 |
|---|---|
| flip-held.c | 초안, **미빌드** |
| fliplog.py | worker 출력 엄격 파서. 순서, fb, user_data, 조기 HELD, 오류 줄을 STOP한다 |
| run-flip.py | supervisor. native run-screen.py 구조에 retained worker 11개 identity 검사, flip마다 underrun 검사, 이탈 시 SIGINT(worker가 hold)를 추가했다. 최종 state 정확 비교 |
| make-manifest.py | native manifest(hash 고정)에서 검토 필드를 복사하고 flip_review를 추가한다. 빌드된 static aarch64 ELF가 없으면 거부 |
| test_flip.py | host 파서 테스트 12개 통과 (기기 CPU에서 순수 Python, 기기 파일/DRM 접근 없음) |
| health_guard.py, borrow.py, runtime-readers.py, y700lib.py | native 묶음 파일을 수정 없이 복사 |

vblank seq/ts는 기록만 하고 강제하지 않는다. vendor 카운터 동작이 검증되지 않았기 때문이다. 잘못 STOP하면 A/B 화면이 남는 쪽이 더 나쁘다.

### 남은 순서 (각 단계는 사람 또는 Mac이 필요)
1. **Mac**: 기기에서 `flip-held.c`, `kms_lifecycle.h`를 가져와 빌드한다.
   `aarch64-linux-android35-clang -static -Os -Wall -Wextra -Werror -o flip-held flip-held.c` 후 이 폴더로 복사한다.
   경고가 나오면 고친 뒤 다시 검토한다.
2. **기기(sudo 불필요)**: `python3 make-manifest.py` → 폴더를 `~/y700-agent/screen-flip-<manifest앞16자>/`로 복사한다. 그다음 그 폴더에서 `python3 run-flip.py --check`.
3. **사용자(sudo, 육안)**: `sudo python3 run-flip.py --show`. 약 5초간 A(막대 정방향)와 B(막대 반전 + 중앙 검은 사각형)가 교대하고, 이어 native 화면으로 돌아오는지 본다.
   30초 관찰 후 결과를 알려 주면 별도 `*-visual.json`에 기록한다.

## 결과 (2026-09-19 00:27 KST, 같은 boot)
- 묶음: `~/y700-agent/screen-flip-64f7797359c13f1a` (worker PID7383/start610451, 새 FB341/342는 계속 보존)
- API 성공: FLIP_DONE 9/9. 매 단계 readback이 일치했고, 복귀 시점과 +30초의 DRM state가 native 기준과 정확히 같았다.
- underrun: 13→13 유지. 새 커널 fault 0건. D 상태는 검토된 3개만 있다.
- 물리 동작 확인: 사용자가 A/B 교대와 native 복귀를 육안으로 확인했다 → `flip-visual.json`.
- 관찰: 이벤트 seq=1, ts=0이 고정이다. vendor 경로가 vblank seq/ts를 채우지 않으므로 pacing에 쓸 수 없다.
  ioctl에서 event까지 약 8.2ms로, 120Hz 1프레임에 해당한다(blocking).
