# 첫 compute 제출 시험 설계 (turnip 26.2.3 / KGSL) (2026-09-19, 기기 세션)

묶음: `~/y700-agent/vkcompute-edd7e0ad2ba64b4f` (manifest SHA256 edd7e0ad2ba64b4f34fdadf0dec77e2d36768e9457cd1402616fe5da37a9f269)
상태: host 테스트 10개 통과, `--check` 통과(retained 17). **`--apply` 미실행.** 배터리 41.9°C, 방전 중(충전기 분리).

## shader
- `fill.spvasm` → `spirv-as`, `spirv-val --target-env vulkan1.0` VALID(Ubuntu spirv-tools .deb를 scratchpad에 풀어서만 사용, 설치 없음)
- 동작: local_size 64, `d[gl_GlobalInvocationID.x] = 3*i + 7`. `fill.spv` 584B, SHA256 8f04a4d3…6bb7b

## worker(vkcompute.py) 단계
- Gate 1 이후(A단계): instance(+debug_utils messenger) → Adreno 840 확인 → compute queue family → VkDevice(queue 1개, 기능 확장 없음)
  → 2048B storage buffer, HOST_VISIBLE|HOST_COHERENT 메모리, 전체를 0xDEADBEEF로 채움 → shader module, set layout, pipeline layout, compute pipeline
  → descriptor pool/set → command pool/buffer: bind, dispatch(4,1,1), barrier(compute write → host read) → fence → **vkQueueSubmit 1회** → vkWaitForFences 5초
  → CPU 검증: 0..255 == 3i+7, 256..511 == sentinel
- Gate 2 이후(B단계, 검증 후 30초 관찰 뒤 자동 해제): 모든 객체 destroy → vkDeviceWaitIdle → vkDestroyDevice → messenger → **vkDestroyInstance**
  (이 프로세스의 kgsl fd close. PID13398이 fd를 보유하므로 last-close는 아니다) → hold
- 오류(VkResult ≠ 0, fence timeout, 검증 실패, VKMSG error)는 즉시 hold한다. 재시도와 부분 정리는 없다.

## supervisor(run-compute.py)
- 매 tick: 모듈 167 identity, 검토된 D wait, 새 D 10초 제한, 커널 fault, GPU 측 오류 줄, FB335 DRM state, DRM client, underrun 13/13, USB Ethernet, retained 17 identity
- 시작/종료: firmware 4, ICD 4, `/dev/kgsl-3d0` 확인
- 시간 제한: A단계 90초, B단계를 포함해 150초. 이후 60초를 관찰한다.

## 판정
- API/기능 성공: fence signaled, 검증 256/256 + 256/256, VKMSG error 0건, 새 커널 fault 0건, 화면/네트워크/retained 유지, destroy 완료 후 60초 이상 없음
- 이것이 **GPU가 실제로 명령을 실행하고 메모리에 쓴 첫 증거**다(CPU readback). 렌더링, 화면 출력, 성능, 장시간 안정성은 별도다.

## 위험(가설)
1. 첫 GPU 명령 실행이다. GPU fault/hang → KGSL fault recovery(snapshot/reset, GMU 경로) 로그 → STOP. 복구 동작 자체가 처음 실행되는 코드다.
2. 첫 drawctxt(submitqueue) 생성과 hwsched/HW fence 경로 사용 → synx/hw_fence 쪽 검토된 wait stack이 바뀌면 STOP한다.
3. vkDestroyInstance의 정상 close 경로에서 kgsl context destroy가 처음 실행된다.
4. 방전 중이다(용량 20% 미만이면 STOP). 온도 45°C 이상이면 STOP한다.

## 결과 (2026-09-19 02:1x KST, 같은 boot) — 기능 성공
- queue family 0 flags 0xF(graphics|compute|transfer|sparse), 메모리 타입 0 flags 0x7(device-local|host-visible|host-coherent), 할당 2048B
- **vkQueueSubmit → fence 5.22ms에 signal. CPU 검증 256/256 기록(3i+7), 256/256 sentinel 유지** → GPU가 명령을 실행해 메모리에 썼다.
- B단계: 모든 객체 destroy, vkDestroyDevice, vkDestroyInstance 완료(정상 경로의 kgsl context destroy + non-last fd close)
- VKMSG error 0건, 새 커널 로그 0건, fault 0건, D 상태는 검토된 3개만 있다. underrun 13/13(전/계산 후/destroy 후/60초), DRM state 매 tick 일치
- firmware/ICD/node 불변. worker PID15845 hold(fd는 닫힘). 배터리 98%, 41.7°C, 방전 중
- 미검증: 그래픽 렌더링, 화면으로 결과 표시(KHR_display 없음 → DRM 쪽 경로 필요), 부하/장시간, 재부팅 재현
