# turnip(KGSL) 빌드 설계 (2026-09-19, 기기 세션, 로컬 분석)

## 확인한 사실
- 소스: upstream `mesa-26.0.8.tar.xz`(archive.mesa3d.org). SHA256 `caf1c006…ad78bc`가 **Ubuntu resolute-updates `mesa_26.0.8.orig.tar.xz`와 같다**(Sources.xz 대조).
- A840: `src/freedreno/common/freedreno_devices.py` 1523행 `GPUId(chip_id=0xffff44050A31, name="Adreno (TM) 840")`(A8XX, 6 CCU, 3 slice)
- KGSL backend: `meson -Dfreedreno-kmds=kgsl` → `tu_knl_kgsl.cc`(`-DTU_HAS_KGSL`). 소스에 Android 전용 분기가 없다.
  - 장치: `/dev/kgsl-3d0` O_RDWR. 앱마다 open/close한다(보존 worker PID13398이 fd를 하나 쥐고 있으므로 KGSL last-close는 일어나지 않는다).
  - `/dev/dma_heap/system` 또는 `/dev/ion`은 **선택 사항**이다. 없으면 `VK_KHR_external_memory_fd`만 빠진다(경고 로그). 기기에는 heap `system`(249:1)이 있지만 node는 없다.
  - **`VK_KHR_display` 미지원**("I can't KHR_display") → 화면 출력은 별도 경로가 필요하다(dma-buf를 msm_drm에 import하거나 CPU 복사).
  - 사용 ioctl: GETPROPERTY, DRAWCTXT_CREATE/DESTROY, GPUMEM_ALLOC_ID/FREE_ID/GET_INFO/BIND_RANGES, GPUOBJ_ALLOC/FREE/IMPORT/INFO,
    GPU_COMMAND, GPU_AUX_COMMAND, TIMESTAMP_EVENT, DEVICE_WAITTIMESTAMP_CTXTID, PERFCOUNTER_READ
- 헤드리스 빌드(`-Dplatforms=`)의 런타임 의존성(z, zstd, expat, stdc++, gcc_s, libc/libm)은 기기에 모두 있다. **Vulkan loader(libvulkan1)는 없다.**
- 빌드 도구: meson ≥ 1.4, ninja, glslangValidator(turnip 내부 compute shader), python3-mako/packaging, bison/flex, zlib/zstd/expat dev

## 빌드 위치: Mac Colima arm64 컨테이너(권장)
- 기기에 패키지를 설치하지 않는다. `ubuntu:26.04` arm64 이미지는 glibc 2.43으로 기기와 같다.
- 기존 profile `y700-build`를 쓴다. `y700-hfi-build` 컨테이너와 기본 Docker context는 건드리지 않는다. 일회성 `--rm` 컨테이너로 돌린다.
- 스크립트 `build-turnip-kgsl.sh`가 하는 일:
  - arm64와 Ubuntu 26.04를 확인하고 tarball hash를 대조한다. 결과 폴더가 이미 있으면 STOP한다.
  - meson 옵션: `-Dplatforms= -Dvulkan-drivers=freedreno -Dfreedreno-kmds=kgsl`, GL/EGL/GBM/LLVM 전부 끔. prefix는 `/home/siwal/y700-gpu/turnip-kgsl-26.0.8`
  - 결과 검사: `/dev/kgsl-3d0` 문자열과 "Adreno (TM) 840"이 들어 있는지, NEEDED 목록과 모든 파일 hash를 `build-info.txt`에 기록한다.
  - 산출물은 `out/turnip-kgsl-26.0.8.tar.gz`와 `.sha256`이다.

## 기기 쪽 첫 시험(다음 설계, 미작성)
- 결과물을 `~/y700-gpu/turnip-kgsl-26.0.8/`에 풀기만 한다. 시스템 설치는 없다.
- loader 없이 Python ctypes로 ICD를 직접 dlopen하는 probe를 쓴다:
  `vk_icdNegotiateLoaderICDInterfaceVersion` → `vk_icdGetInstanceProcAddr` → vkCreateInstance → vkEnumeratePhysicalDevices →
  vkGetPhysicalDeviceProperties. 이 단계는 GETPROPERTY 계열만 쓸 것으로 예상한다(확인 필요). 메모리 할당이나 제출은 없다.
- 그 다음 단계(별도): vkCreateDevice(context/메모리 할당) → 작은 compute dispatch → 결과 readback
- 권한: `/dev/kgsl-3d0`가 0600 root이므로 첫 시험은 sudo로 실행한다. 권한 정책은 나중에 정한다.

## 빌드 결과 (2026-09-19 01:29 KST, Mac Colima y700-build, ubuntu:26.04 arm64, 로컬 분석)
- 산출물 `turnip-kgsl-26.0.8.tar.gz` sha256 OK. gcc 15.2.0, meson 1.10.1, glslang 16.2.0
- `libvulkan_freedreno.so` SHA256 `bc22e36b3840f15bd6cf9b74507b9b8694d4229f00b19081b052e06b5276f4a6`
  - `/dev/kgsl-3d0`, "Adreno (TM) 840", `/dev/dma_heap/system`이 들어 있다. NEEDED: z, zstd, expat, stdc++, m, gcc_s, c, ld → 기기 ldd에서 모두 해결된다.
  - ICD json: api_version 1.4.335, library_path는 `/home/siwal/y700-gpu/turnip-kgsl-26.0.8/lib/libvulkan_freedreno.so`
- 부산물: libxml2/libarchive(헤더, bsdtar, xmllint)가 meson subproject fallback으로 빌드·설치됐다(wrap 다운로드).
  turnip이 링크하지 않으므로(NEEDED에 없고 심볼 참조도 없음) **배치에서 제외했다.**
  다음 빌드에서는 `--wrap-mode=nodefaultfallback` 등으로 막을지 검토한다.
- 배치: `~/y700-gpu/turnip-kgsl-26.0.8/`에 lib .so, ICD json, drirc 3개만 두었다(파일 복사만 했고 시스템 설치는 없다). `SHA256SUMS` 기록

## 26.2.3로 교체 (2026-09-19)
- 이유: 26.0.8 KGSL backend에 UBWC 5/6 처리가 없다 → A840 열거 실패(vkprobe 결과).
- `mesa-26.2.3.tar.xz` SHA256 `1628058a…ea034a81f`
  - **gpgv Good signature**: Eric Engestrom `57551DE15B968F6341C248F68D8E31AFC32428A6`(keys.openpgp.org에서 받음)
  - UBWC_5_0/6_0 case와 A840 GPUId(0xffff44050A31)가 있다. 빌드 옵션은 같다(meson ≥ 1.4).
  - main에만 있고 26.2.3에는 없는 수정: `c96b2e4c` load 실패 시 fd 이중 close, `35f59101` UBWC 실패 누수 → 실패 경로에만 영향이 있다. 패치하지 않는다.
- 스크립트 `build-turnip-kgsl-26.2.3.sh`: 26.0.8판과 차이는 버전/해시/prefix, `libarchive-dev libxml2-dev` 추가, `--wrap-mode=nodownload`(빌드 중 subproject 다운로드 금지)
