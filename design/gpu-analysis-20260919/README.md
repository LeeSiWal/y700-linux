# GPU(KGSL) 로컬 분석 (2026-09-19, 기기 세션, 읽기 전용)

범위: device tree, sysfs device link, 설치된 파일, Ubuntu Mesa 패키지(설치하지 않고 scratchpad에 풀어서 분석), 웹 자료.
**기기 변경, 모듈 적재, debugfs/레지스터 접근은 하지 않았다.**

## 1. 하드웨어 식별 (DT 읽기)
- `soc/qcom,kgsl-3d0@3d00000`: compatible `qcom,adreno-gpu-gen8-2-1 | qcom,kgsl-3d0`, `qcom,gpu-model = Adreno840v2`, `qcom,chipid = 0x44050a01`, status ok
  - clocks: gcc(`gcc_gpu_memnoc_gfx`), gpucc(`gpu_cc_ahb`, `apb_pclk`). interconnect `gpu_icc_path`. nvmem `gpu_debug_bus_bin`, `speed_bin`(qfprom). ubwc-mode 6
  - `zap-shader` → memory-region `gpu_microcode_region@9959a000` (8KiB, no-map)
  - `qcom,gpu-pwrlevel-bins`: pwrlevels-0..3 (speed_bin fuse로 선택)
- `soc/qcom,gmu@3d37000`: `qcom,gen8-gmu`. power-domain cx/gmu_cx는 gpucc, gx는 `3d68024` gx_clkctl-canoe. iommus kgsl-smmu. `qcom,qmp` 사용
- `soc/qcom,kgsl-iommu@3da0000`: `qcom,kgsl-smmu-v2`, 자식 gfx3d_user/secure/lpac. **display SMMU와 분리된** kgsl-smmu(arm-smmu, 이미 bound)

## 2. 공급자 상태 (sysfs, 현재 boot)
- kgsl-3d0, gmu, kgsl-iommu: **driver 없음**, `waiting_for_supplier=0`, 모든 supplier link `available`
- 공급자 driver 모두 bound: gcc, gpucc, gx_clkctl, qfprom(nvmem_qfprom 적재 덕분), qnoc 두 개, aoss_qmp, arm-smmu
- 따라서 **남은 조건은 모듈(msm_kgsl + 의존성)과 firmware뿐**이다. 공급자 측 선행 조건은 이미 충족되어 있다.

## 3. 가장 큰 위험: sync_state 연쇄 (가설, 적재 전 반드시 확인)
- `state_synced=0`: gcc(100000), gpucc(3d90000), qnoc 31100000, interconnect@1, 1600000, 1780000. dispcc(9ba2000)는 이미 1이다.
- 이전 boot 인벤토리 기준으로 각 공급자를 기다리게 하는 consumer:
  - gpucc ← kgsl-3d0, kgsl-iommu, gmu뿐 → KGSL 3개가 bind되면 **gpucc sync_state가 실행될 가능성이 높다.** 영향은 GPU clock 범위로 추정한다.
  - **gcc ← kgsl-3d0, mdss_mdp(현재 bound), dp_display(현재 bound)** → kgsl-3d0 bind가 마지막 조건이면
    **GCC 전체 sync_state가 실행된다.** 그러면 부트로더가 켜 둔, consumer vote 없는 GCC clock이 꺼질 수 있다.
    USB Ethernet, SD, display 경로에서 vote 없이 동작하는 clock이 있으면 SSH나 화면이 끊길 위험이 있다.
  - qnoc 31100000, interconnect@1 ← kgsl, mdss, **cnss(미bound)** → cnss가 남아 있으므로 ICC sync_state는 KGSL만으로는 실행되지 않을 것으로 예상한다.
- 인벤토리는 이전 boot의 11.48s 시점 기록이다. 현재 dmesg에서는 해당 줄이 이미 밀려났다.
  → 적재 전에 **현재 boot의 gcc pending consumer 목록을 다시 확정해야 한다**(sysfs `consumer:` 링크와 각 consumer의 driver).
  vote 없이 켜져 있는 GCC clock의 영향 범위는 **개별 검토한 clock 파일만** 읽어서 판단한다. clk_summary 전체 덤프는 금지다.

### 3.1 현재 boot 확정 (sysfs consumer 링크, 2026-09-19 00:4x)
| 공급자 | consumer 수 | 미bound consumer | KGSL bind 후 sync_state |
|---|---|---|---|
| gcc 100000 | 36 | qup_uart, spi, pcie, vidc, cvp, **kgsl-3d0, gmu**, cam-cpas | **실행 안 됨**(다른 6개가 남음) |
| gpucc 3d90000 | 6 | **kgsl-3d0, gmu, kgsl-iommu**뿐 | **실행될 가능성이 높다** |
| qnoc 31100000 | 22 | uart, spi, vidc, cvp(sso), kgsl, cam-cpas, cnss, qpace | 실행 안 됨 |
| interconnect@1 | 40 | spss, uart, spi, pcie, qcedev, vidc, cvp, kgsl, cam-cpas, cnss, ddr-cdev, qpace | 실행 안 됨 |

→ 3절의 GCC 전체 sync_state 우려는 **현재 boot에서는 해당되지 않는다.** 남는 것은 gpucc sync_state다.
gpucc는 kgsl-smmu(arm-smmu, bound)의 공급자이기도 하다.
kgsl-smmu가 자체 vote 없이 boot 상태의 gpucc clock에 기대고 있다면 GPU SMMU에 영향이 갈 수 있다(가설, GPU 영역 한정).
적재 묶음에 gpucc `state_synced` 0→1 전이 관찰과 전후 SMMU fault 감시를 넣는다.
다만 consumer의 bind 상태는 나중에 다른 모듈(Wi-Fi/cnss, camera 등)을 적재하면 바뀐다. **KGSL 적재 직전에 다시 확인한다.**

## 4. Userspace 경로
- 현재 `msm_drm.ko`(적용 SHA256 8b6dc17b…)에는 adreno/gem_submit/submitqueue 문자열이 **없다** → vendor SDE display 전용이다.
  **renderD128은 GPU 렌더링에 쓸 수 없다**(upstream drm/msm GPU가 없음).
- Ubuntu `mesa-vulkan-drivers 26.0.8-1ubuntu0.3`(미설치, 풀어서 분석한 결과):
  - `libvulkan_freedreno.so`에 `/dev/kgsl-3d0` 문자열이 없다 → **KGSL backend 미포함으로 추정**(msm, virtio만)
  - chip id 표에 0x44050000(FD830)과 0xffff44050a31은 있지만 **0x44050a01(Adreno 840v2)는 없다**(바이트 스캔 기반 추정)
  - 결론: Ubuntu 기본 Mesa로는 이 GPU를 쓸 수 없다.
- 현실적인 후보: **KGSL + turnip(Vulkan), KGSL backend로 직접 빌드**. GL은 그 위에 Zink로 올린다.
  - A840 turnip 지원은 upstream이 아니라 whitebelyash의 gen8 스택(mesa-unified turnip/gen8)에 있다.
    Android(KGSL)용 빌드가 배포되고 있으며 "안정성 미보장"이다.
  - Linux glibc에서 `-Dfreedreno-kmds=kgsl`로 빌드해야 한다. 이 조합의 동작 사례는 **확인하지 못했다.**
- 대안: vendor 독점 Adreno userspace(bionic)를 libhybris로 쓰는 방법. 무겁고 유지보수가 어렵다. 보류.
- upstream drm/msm GPU를 모듈로 추가하는 방법은 vendor msm_drm과 충돌하므로 비현실적이다. 제외.

## 5. Firmware (미확정 → Mac 작업)
- 기기 `/lib/firmware`에는 regulatory.db뿐이다. Android vendor 파티션은 mount되어 있지 않다(mount하지 않음).
- 필요한 것(KGSL gen8 일반 구성 기준, **파일명은 msm_kgsl.ko에서 확정해야 함**): SQE(CP microcode), GMU, zap shader(.mdt/.bNN, TZ PAS로 적재).
  AQE 등 추가 microcode가 필요할 수 있다.
- zap은 qcom_scm/mdt_loader 경로를 쓴다. 두 모듈 모두 이미 적재되어 있다.
- firmware 요청 경로(`firmware_class.path`)는 일반 사용자로 읽을 수 없다(root 필요).

### 5.1 msm_kgsl.ko 문자열 (Mac strings 출력, 2026-09-19)
- 이 모듈은 `qcom,adreno-gpu-gen8-2-1` compatible과 `adreno_gpu_core_gen8_2_1` core를 포함한다. `qcom,gen8-gmu` driver도 있다.
- gen8_2 계열 firmware 이름 후보: **`gen80200_sqe.fw`, `gen80200_gmu.bin`, `gen80200_aqe.fw`, `gen80200_zap.mbn`**
  (zap은 분할 .mdt가 아니라 단일 .mbn이다)
- 문자열만으로는 gen8_2_1 core가 가리키는 이름을 **확정할 수 없다.**
  `gpucore.py`(stdlib ELF relocation 분석, 적재 없음)로 `adreno_gpu_core_gen8_2_1`의 relocation을 확인해 확정한다.
- 관련 오류 문자열: "zap-shader node not found", "Couldn't parse the mem-region from the zap-shader node",
  "GMU FW version 0x%x error (expected 0x%x)" → **GMU firmware 버전 검사가 있다.** vendor 이미지에 들어 있는 짝 그대로 써야 한다.

## 6. 다음 단계 (순서대로, 각각 별도 단계)
1. **Mac(로컬)**: `msm_kgsl.ko`의 depends, softdep, firmware 이름, CRC를 분석하고 GPU closure를 다시 계산한다(이전 후보 58개 재검증).
   Android vendor 이미지에서 해당 firmware 파일과 hash를 추출한다.
2. **기기(읽기 전용, sudo 불필요)**: 현재 boot의 gcc/gpucc consumer 목록과 각 driver를 확정한다(sysfs link만 사용).
3. **기기(읽기 전용, sudo)**: vote 없이 켜진 GCC clock의 영향 범위를 개별 파일 읽기로 검토한다. clk_summary는 쓰지 않는다.
4. 검토 결과에 따라 KGSL 적재 one-shot 묶음을 설계한다. 성공 조건에 화면(FB335)과 USB Ethernet이 유지되는 것을 포함한다.
   sync_state 실행 여부 관찰, firmware 요청 로그, 실패 시 unload 금지를 넣는다.
5. 그 뒤에 turnip(KGSL backend, gen8 스택) 빌드를 검토한다(Mac 또는 컨테이너). 최소 vkinfo → 오프스크린 렌더 → 화면 표시 순서로 진행한다.

## 7. msm_kgsl.ko 분석 결과 (기기, 2026-09-19 00:5x, 로컬 분석)
- 파일 SHA256 `12cb8a0d983357cb080748e66efc536671898af60228d76654cdd407f8ee9ae3` (Mac vendor_dlkm_extracted에서 복사)
- **gen8_2_1 core firmware 확정**(`gpucore.py` relocation): `gen80200_sqe.fw`, `gen80200_aqe.fw`, `gen80200_gmu.bin`, `gen80200_zap.mbn`
  - gpudev = `adreno_gen8_hwsched_gpudev` → **GMU hardware scheduling 경로**(synx/HFI 연동)
- vermagic `6.12.30-android16-5-maybe-dirty-4k … modversions`: 적용된 msm_drm.ko와 같다.
  CRC가 있으므로 커널은 버전 부분을 비교하지 않는다.
- depends 17개 중 14개는 적재되어 있다. **빠진 것: `coresight`, `msm_performance`, `msm_sysstats`**
- softdep pre: arm_smmu, nvmem_qfprom, socinfo(적재됨). **`governor_msm_adreno_tz`, `governor_gpubw_mon`, `governor_msm_adreno_ro`(없음)**
- 미정의 심볼 543개 중 현재 export로 해결되지 않는 것은 **6개뿐**이며, 모두 빠진 모듈 몫이다:
  coresight_get_platform_data/register/unregister, msm_perf_events_update, sysstats_(un)register_kgsl_stats_cb
- CRC 대조는 아직이다. 일반 사용자에게는 kallsyms `__crc_*` 값이 0으로 보인다.
  sudo로 kallsyms를 읽거나 Mac GKI symvers와 대조해야 한다.

### 추가 모듈의 위험 메모 (가설)
- coresight core: framework 등록만 한다. 다른 coresight 모듈이 없으면 DT 장치를 probe하지 않을 것으로 예상한다.
- msm_performance: CPU/perf 이벤트 notifier를 등록할 가능성이 있다. 소스 확인이 필요하다.
- governor_msm_adreno_tz: **TZ(SCM) 호출로 DCVS를 수행한다.** ADCI/secure call baseline 문제가 있으므로 가장 조심해야 한다.
- 6개 모듈 각각의 재귀 depends는 파일이 필요하다(Mac).

## 8. 추가 모듈 6개 분석 (2026-09-19, 로컬 분석)
| 모듈 | SHA256 앞 16자 | depends | 결과 |
|---|---|---|---|
| coresight | 0c46d0a4fe3b0332 | 없음 | OK |
| msm_sysstats | 70c14e9a14335cb9 | qcom_dma_heaps(적재됨) | OK |
| msm_performance | 8ba598bb1da788db | qcom-pmu-lib, sched-walt(적재됨) | OK |
| governor_msm_adreno_tz | 74c333aaf377d94b | qcom-scm(적재됨) | OK |
| governor_gpubw_mon | 170ae71a1589f071 | 없음 | OK |
| governor_msm_adreno_ro | 337ab1ae63e5a285 | 없음 | OK |
- **GPU closure = 7개**(위 6개 + msm_kgsl). 나머지 의존성은 모두 이미 적재되어 있다. 7개 모두 vermagic이 일치하고 CRC가 있다.
  남은 미정의 심볼 0개(현재 export와 이 7개 집합으로 모두 해결된다).
- 예상 적재 순서: coresight → msm_sysstats → msm_performance → governor_msm_adreno_tz → governor_gpubw_mon → governor_msm_adreno_ro → msm_kgsl
- **CRC 대조(부분)**: 기준은 현재 적재되어 있고 build-id가 live와 일치하는 .ko 67개의 `__versions`이다(알려진 정상 CRC 1577개).
  7개 모듈의 import 811개 중 **550개를 대조했고 불일치 0개**다. **나머지 261개는 미검증**이며, Mac의 GKI symvers(work/gki-13938768)와 대조해야 한다.
  kallsyms `__crc_*`는 값이 아니라 주소라서 sudo로도 이 방법은 쓸 수 없다. 해당 스크립트는 폐기했다.

## 9. firmware 부재
- Mac `/Users/siwal/Desktop/y700` 아래에 `gen80200_*` 파일이 **하나도 없다**(사용자 find 결과, 2026-09-19).
- 출처 후보: Lenovo 공식 ROM의 vendor 이미지(super 안의 vendor_a). 보통 `/vendor/firmware/gen80200_*` 위치다(확인 필요).
  기기 UFS의 super를 읽는 방법은 raw block **읽기**라서 규칙상 쓰기 금지와는 다르다. 그래도 공식 ROM 패키지에서 추출하는 쪽을 우선한다.

## 10. firmware 추출 (2026-09-19 01:0x, 기기, 로컬 분석)
- 원본: Mac `work/vendor-probe.img`(ext4, volume "vendor", UUID ade5fb82-…, 1,540,546,560바이트 = `original/super_7.img`와 같은 크기)
  - 기기 사본 SHA256 `e385e71d6e8d152765980990ade4eaf66c467e1533eccf0891ba3390954672e2`
  - super_7.img와의 hash 대조 결과는 **아직 받지 못했다**(출처 확정 대기).
- `debugfs`로 읽기 전용 추출(mount/설치 없음). 이미지 `/firmware` 안의 gen8 계열 파일은 80000/80200/80900 세트다.
  | 파일 | 크기 | SHA256 |
  |---|---|---|
  | gen80200_sqe.fw | 119156 | e2d4e74282f50e40788b83b9ba4a5f5894c7975ed356c03a0161372d78f12932 |
  | gen80200_aqe.fw | 25880 | ed7916ca84e663d63fa94b0d40cc8c222e362e6cff860c539a8cca30d3721b68 |
  | gen80200_gmu.bin | 112030 | 06f4484fa06f91638aa4ae2412051bcddffdad69a7e2f83849c72412e6daa44d |
  | gen80200_zap.mbn | 12088 | 25e836c603d52107a2cbb2f1a7eb46997837e94c2fb70bb5cc44b5e700d8936a |
- zap.mbn은 32-bit ELF(e_machine 164, phnum 3)이며 서명된 Qualcomm 이미지 형식이다. TZ PAS 검증은 적재 시점에만 알 수 있다.
- cmdline/bootconfig에 `firmware_class.path` 설정이 없다 → 기본 검색 경로(/lib/firmware 등)를 쓴다.
- 적재 시 배치 방안(설계 대상):
  (a) `/sys/module/firmware_class/parameters/path`를 적재 묶음 폴더로 **런타임 설정**한다. 비영구이고 범위가 좁다. 기존 값은 root로 읽어 기록한다.
  (b) `/lib/firmware`에 복사한다. 영구적인 시스템 변경이다.
  → (a)를 우선한다. 단, 다른 드라이버가 같은 시기에 firmware를 요청하면 경로가 바뀐 영향을 받는다.

## 11. 남은 한계
- CRC: 811개 중 550개만 대조했다(불일치 0). 나머지는 커널이 적재 시 검사한다. 불일치면 init 이전에 적재가 거부된다.
- userspace: Ubuntu Mesa로는 불가하다. KGSL + turnip(gen8 외부 브랜치) 빌드는 별도 과제다.

## 12. 정정 (2026-09-19 01:1x)
- 실제 chip id는 **0x44050a31**(KGSL DEVICE_INFO)이다. DT `qcom,chipid`는 0x44050a01이다.
- 4절의 "Ubuntu Mesa chip 표에 없음"은 **틀렸다**(0x44050a01로 검색한 결과였다). Ubuntu Mesa 26.0.8 turnip에는 `0xffff44050a31`과 "Adreno (TM) 840"이 있다.
  upstream drm/msm a8xx catalog에도 `ADRENO_CHIP_IDS(0x44050a31)`로 들어 있다.
- KGSL backend가 빠져 있다는 판단은 유지한다(`/dev/kgsl-3d0` 문자열 없음).
  → 다음 경로: **upstream Mesa 26.0.x turnip을 `-Dfreedreno-kmds=kgsl`로 빌드**한다. 외부 gen8 브랜치는 필요 없을 수 있다(검증 필요).
