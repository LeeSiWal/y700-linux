# 첫 Vulkan probe(turnip/KGSL, loader 없음) 설계 (2026-09-19, 기기 세션)

묶음: `~/y700-agent/vkprobe-0496c254a7b64f64` (manifest SHA256 0496c254a7b64f64fada99bece16645fde2a9c9657aa84fea752ee15c0250724)
상태: host 테스트 9개 통과, `--check` 통과. **`--apply`는 아직 실행하지 않았다.**

## worker(vkprobe.py, root, ctypes)
1. `libvulkan_freedreno.so`(bc22e36b…)를 dlopen한다 → `vk_icdNegotiateLoaderICDInterfaceVersion`(5부터 협상)
2. `vk_icdGetInstanceProcAddr`로 vkCreateInstance(API 1.3, layer/extension 없음)를 부른다
3. vkEnumeratePhysicalDevices(개수가 정확히 1이어야 한다) → vkGetPhysicalDeviceProperties
4. `PROPERTIES`: vendorID 0x5143, deviceName "Adreno (TM) 840"이 아니면 STOP한다
5. **vkDestroyInstance를 부르지 않는다** → 새 kgsl fd를 유지하고 hold한다(close 경로는 별도 시험)
- env: TU_DEBUG=startup, MESA_SHADER_CACHE_DISABLE=true, HOME/XDG_CACHE_HOME=묶음 폴더(시스템 쓰기 없음)

## 소스로 확인한 GPU 쪽 동작(turnip 26.0.8 `tu_knl_kgsl_load`)
- `/dev/kgsl-3d0`를 새로 O_RDWR로 연다. `/dev/dma_heap/system`과 `/dev/ion`은 없으므로 경고만 남긴다(external_memory_fd 없음).
- GETPROPERTY: DEVICE_INFO, UCHE_GMEM_VADDR, HIGHEST_BANK_BIT, UBWC_MODE(알 수 없는 값이면 turnip이 init을 실패한다), UCHE_TRAP_BASE, IS_RAYTRACING_ENABLED, GPU_VA64_SIZE
- **메모리 probe**: GPUMEM_ALLOC_ID 4K → FREE_ID를 2회(USE_CPU_MAP, IOCOHERENT|WRITEBACK), GPUOBJ_ALLOC VBO 8K + ALLOC_ID 4K + BIND_RANGES → FREE
- context 생성, 명령 제출, VkDevice는 없다. KGSL enumerate가 성공하면 DRM 열거로 넘어가지 않는다(vk_instance.c). DRM client 목록은 매 tick 확인한다.

## supervisor(run-vkprobe.py)
- 이전 단계와 같은 매 tick 검사: 167개 모듈, 검토된 D wait, 커널 fault, GPU 오류 줄, FB335 DRM state, DRM client, underrun 13/13, USB Ethernet
- retained 14개(DRM 13 + kgsl worker 13398)
- 시작/종료 시: firmware 4개, ICD 파일 3개, `/dev/kgsl-3d0`(495:0, 0600 root) 확인
- 드라이버 로그는 MESA/TU info·warning만 허용한다. error는 STOP이다.
- GO 후 30초 안에 HELD, 이후 60초 관찰

## 위험(가설)
- 처음으로 **다른 프로세스가 두 번째 kgsl fd를 열고 GPU 메모리를 할당·해제**한다. SMMU mapping이 생기고 사라지는 첫 사례다 → SMMU fault/커널 경고는 STOP 대상이다.
- turnip이 이 KGSL 버전의 UBWC_MODE 값을 모르면 init이 실패한다(VK_ERROR_INITIALIZATION_FAILED, 안전하게 실패).

## 결과 (2026-09-19 01:4x, vkprobe-0496c254a7b64f64)
- ICD 로드, 협상(v5), vkCreateInstance는 성공했다. **vkEnumeratePhysicalDevices가 -3(VK_ERROR_INITIALIZATION_FAILED)을 반환했다.**
  release 빌드라 vk_errorf 메시지는 stderr로 나오지 않는다(debug-utils messenger 전용).
- 원인(소스 대조, 높은 확신이지만 값을 직접 읽지는 않았다): 26.0.8 `tu_knl_kgsl_load`는 UBWC 1–4만 처리한다. DT `qcom,ubwc-mode = 6`.
  upstream `75fad9e2 tu/kgsl: Add UBWC_5 and UBWC_6 support`(2026-04-02)가 있다.
  msm_kgsl.ko property 표에는 turnip이 쓰는 prop(0x13/0x17/0x1b/0x2c/0x2d/0x2f)이 모두 있다.
- 부수 관찰: 실패 경로에서 turnip이 kgsl fd를 닫았다(26.0.8 이중 close 버그 포함). **두 번째 프로세스의 non-last kgsl close가 처음 실행됐고**,
  새 커널 로그 0건, fault 0건, 화면/네트워크/retained 전부 유지됐다. worker PID14432는 hold 상태다.
