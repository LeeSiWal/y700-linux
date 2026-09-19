# Audio 조사 (2026-09-19, 기기 세션, boot 3a1d6dac) — 로컬 분석, 기기 변경 없음

## 하드웨어 (DT, sysfs)
- 스피커: **Awinic aw882xx smart PA 2개**(I2C 4-0034, 4-0037, driver 없음). I2S/TDM으로 LPASS에 연결(가설: 어떤 MI2S/TDM 포트인지는 machine DT에서 확인 필요)
- 코덱: WCD939x(okay; I2C 3-000e `qcom,wcd939x-i2c`, SoundWire rx/tx slave) — USB-C 아날로그 헤드셋과 마이크. `fsa4480`(USB-C 오디오 스위치)는 bound
- WCD9378, WSA884x, wsa2-macro: **disabled**(이 보드에서 안 씀). LPASS 코덱 macro: rx/tx/va/wsa, SoundWire master 4개, BT SWR
- 사운드 카드: `soc/spf_core_platform/sound` = `qcom,canoe-asoc-snd`(vendor machine driver)
- 그 밖에: aw22xxx LED(5-006a), awinic haptic L/R(6-005a/b) — audio와 별개

## DSP
- `remoteproc1` = `3000000.remoteproc-adsp`(qcom_q6v5_pas bound), **offline**, firmware `adsp.mdt` + `adsp_dtb.mdt`
- modem_a `/image`: `adsp.mdt`+b00..b54, `adsp_dtb.mdt`+b00..b02, PD mapper JSON `adspr.jsn`/`adspua.jsn`/`adsps.jsn`(+cdsp)
- glink-edge 아래 `qcom,gpr` → `q6prm`(audio_prm), `spf_core`, `audio-pkt`: vendor AudioReach(SPF) 스택
- live: qcom_q6v5_pas, qcom_sysmon, pdr_interface, qcom_glink(_smem), qrtr(vendor), qmi_helpers. GKI config: `QCOM_APR` 없음(upstream q6apm 경로 없음)

## 소리를 내는 데 필요한 층 (가설, 위에서 아래 순서)
1. **ADSP 부팅**: /lib/firmware에 adsp*.mdt/bNN 설치 → remoteproc start. 오디오 PD(`msm/adsp/audio_pd`)는 **PD mapper**(servreg locator, QRTR QMI)가 있어야 올라온다
   - Android는 사용자 공간 `pd-mapper`가 `*.jsn`을 읽어 응답한다 → Linux에는 없다(Python AF_QIPCRTR로 구현할 수 있다)
2. **vendor 오디오 커널 모듈**: gpr, spf_core, audio_pkt, audio_prm, q6_notifier, adsp_loader, pinctrl_lpi, lpass_cdc(+macros), swr_ctrl, wcd939x, aw882xx, machine_dlkm, …(Mac vendor_dlkm에서 확인 필요)
3. **그래프 설정**: vendor ASoC 카드는 PCM만 노출한다. 실제 DSP 그래프(SPF 모듈 연결, calibration)는 사용자 공간 AGM/PAL + ACDB(`/vendor/etc/acdbdata`)가 audio_pkt로 보낸다
   → Linux에서는 **AudioReach 사용자 공간**(Qualcomm 공개 graphservices/AGM, 가설: canoe 지원 여부 미확인)을 쓰거나, GPR 명령으로 최소 재생 그래프를 직접 구성해야 한다 → **가장 큰 작업**
4. aw882xx: firmware/파라미터(`aw882xx_acf.bin` 등, vendor 파티션)
- 대안: USB 오디오(`SND_USB_AUDIO=y`)는 바로 동작하지만, 지금 USB-C 포트는 Ethernet 어댑터가 쓰고 있다

## 권장 단계
- A. 파일 확보(Mac): 오디오 vendor 모듈, vendor 파티션의 acdb/aw882xx firmware/mixer 설정 → 로컬 분석
- B. ADSP 부팅 + PD mapper(Python) → remoteproc running, audio PD up(API 확인)
- C. 오디오 모듈 적재 → ASoC 카드 등록(/proc/asound)
- D. 재생 그래프(가장 큰 불확실성) → 스피커 소리(물리 확인)

## 추가 확인 (2026-09-19)
- 사운드 카드 model: **`canoe-qrd-snd-card`** → vendor 설정 `mixer_paths_canoe_qrd.xml`, `resourcemanager_canoe_qrd.xml`, ACDB `acdbdata/canoe_qrd/QRD_acdb_cal.acdb`(+ .qwsp)가 맞을 것이다(가설: Lenovo가 따로 쓰는 파일이 있을 수 있다)
  - DT: mi2s-audio-intf=1, tdm=0, wsa-max-devs=0, codecs `msm-stub-codec.1|lpass-cdc|wcd939x_codec|swr-haptics|sdca-simple-amp.01`, USB-C 헤드셋(mbhc usbc)
- vendor-probe.img(ext4, 기기에 있음): `/firmware/aw882xx_acf.bin`, `/etc/audio/sku_canoe/*`, `/etc/acdbdata/*`, `usecaseKvManager.xml`, `card-defs.xml`, `backend_conf.xml`
- AudioReach 사용자 공간(libagm, libar-gsl, libar-acdb, libar-pal …)은 vendor에 **Android(bionic) 바이너리**로만 있다 → Ubuntu에서는 그대로 쓸 수 없다
- Mac vendor_dlkm 오디오 모듈(사용자 find): gpr, spf_core, audio_prm, audio_pkt, adsp_loader, q6, q6_notifier, q6_pdr, snd_event, pinctrl_lpi, swr, swr_ctrl, swr_dmic, swr_haptics,
  lpass_cdc(+rx/tx/va/wsa/wsa2), lpass_bt_swr, bt_fm_swr, wcd_core, wcd9xxx, mbhc, wcd939x(+slave), wcd938x/9378(이 보드 disabled), aw882xx, machine, fsa4480(live), adsp_sleepmon

## 모듈 확인 (기기 복사 30개, 2026-09-19)
- 모두 vendor vermagic(`…-maybe-dirty-4k`, modversions). live 의존성: btpower, fsa4480(×2), hqsysfs, pdr_interface, pinctrl-msm, qcom_ipc_logging, qcom_q6v5_pas,
  qti-regmap-debugfs, qti_battery_charger, rproc_qcom_common, smem, socinfo
- **없는 의존성**: `audpkt_ion_dlkm`(audio_pkt), `wcd9378_dlkm`·`wsa883x_dlkm`·`wsa884x_dlkm`(machine_dlkm의 심볼 의존; 이 보드는 disabled), `btfmcodec`(bt_fm_swr: BT SCO 오디오, 나중)
## ADSP firmware (modem_a에서 읽기 전용 추출)
- `firmware/`: adsp.mdt+b00..b54, adsp_dtb.mdt+b00..b02, adspr/adspua/adsps.jsn (62개, SHA256SUMS)
- PD mapper JSON: domain `msm/adsp/{root_pd, audio_pd, sensor_pd}`, qmi_instance_id 74, 서비스 tms/servreg(+ audio_pd는 avs/audio)
  → **sensor_pd도 ADSP에 있다: sensors 단계도 ADSP 부팅과 PD mapper가 필요하다**

## 추가 모듈 확인 (35개)
- audpkt_ion(071a75a7…), wcd9378(3631ecfc…), wsa883x(2fd7a6cd…), wsa884x(84c1dfed…), btfmcodec(fca3495f…) — vendor vermagic
- `check-imports.py` 35개: 미해결 2개만 남음 — wcd9378의 `sdca_devices_debugfs_dentry_create/remove` → **`sdca_registers_dlkm.ko` 필요**. CRC 대조 불일치 0
- 적재 순서(위상 정렬, 초안): q6_pdr → q6_notifier → snd_event → gpr → spf_core → adsp_loader → adsp_sleepmon → audpkt_ion → audio_pkt → audio_prm → aw882xx → btfmcodec → swr → bt_fm_swr
  → swr_ctrl → wcd_core → lpass_bt_swr → lpass_cdc(+rx/tx/va/wsa2/wsa) → mbhc → sdca_registers → wcd9xxx → wcd9378 → wcd939x_slave → wcd939x → wsa883x → wsa884x → machine → pinctrl_lpi → q6 → swr_dmic → swr_haptics
- 주의(가설): `adsp_loader`가 probe할 때 ADSP 부팅을 트리거할 수 있다 → B단계에서 문자열/DT(`qcom,adsp-state` 등)를 먼저 분석한다

## sdca_registers(fa8226f5…) 추가 → **36개 closure OK**(미해결 0, CRC 불일치 0), `modules.sha256`
## B단계 사전 분석
- adsp_loader: DT 노드에 `qcom,rproc-handle`만 있고 **`qcom,adsp-state`가 없다**. 문자열: `failed to read adsp state`, sysfs `boot_adsp/boot`(adsp_boot_store), `ssr`
  → Android init이 `/sys/kernel/boot_adsp/boot`에 1을 쓸 때만 부팅한다(가설, 소스 형태와 일치). 모듈 적재만으로 ADSP가 켜질 가능성은 낮다
- **QRTR 전송 계층 없음**: rpmsg 드라이버는 pmic_glink_rpmsg/glink_ssr/sysmon/chrdev만 있다. soccp의 `IPCRTR` 채널은 driver 없음, `qrtr-smd` 미적재
  → ADSP의 QMI(servreg locator/notifier, PD 상태)를 쓰려면 **`qrtr-smd.ko`가 필요하다**(주의: 적재하면 soccp IPCRTR도 bind된다 → 영향 검토)
- PD mapper: 사용자 공간 Python(AF_QIPCRTR=42, python 지원)으로 servreg locator(service 0x40, ver 1) 서버를 구현한다 — jsn 3개 사용

## qrtr-smd (b7031fbf…, vendor_boot에도 같은 파일 → Android는 1단계에서 적재; Bootstrap 2.1 목록에는 없음)
- depends qrtr,qcom_glink(live), alias rpmsg:IPCRTR, closure OK → modules.sha256 37개
- `nextboot-impl/qrtrns.py`: QRTR name service 읽기 전용 조회(NEW_LOOKUP). 현재: node1 svc 1070, node7(WLAN/MHI) svc 69(WLFW)·58
- 단계 `qrtr-smd`(boot-qrtr-smd-4d76a651…, PREFLIGHT PASS): 적재 → SoCCP IPCRTR bind(qcom_smd_qrtr) → 30초 관찰 → 서비스 목록, SoCCP 상태, 배터리 기록
  - 위험 검토: SoCCP 서비스가 name service에 나타나면 대기 중인 커널 클라이언트(pdr_interface ← qti_pmic_glink 등)가 연결을 시도한다. locator(PD mapper)가 없으면 조회는 대기만 한다(가설) → 충전/배터리 상태를 기록한다

## B-1 결과 (boot-qrtr-smd-4d76a651…): **STAGE_QRTR_SMD_LOADED**
- SoCCP IPCRTR → qcom_smd_qrtr bind. 새 QRTR 서비스는 **node 24(SoCCP)의 svc 15 inst 141** 하나. SoCCP attached 유지, 배터리 100% Full 36.5°C, fault 0건
## B-2 설계 (boot-adsp-4a515df8…, PREFLIGHT PASS)
- `pdmapper.py`: servreg locator(0x40 v1, instance 0과 1) 상주 프로세스. GET_DOMAIN_LIST(0x21)에만 응답한다(jsn 4개: root/audio/sensor/ois_pd, inst 74). host 테스트 3개
- `run-adsp.py`: firmware 58개를 /lib/firmware에 설치(없는 파일만, 같은 해시만 허용) → pdmapper 시작, name service에서 확인 → registry-pdm
  → remoteproc1 recovery=disabled(현재도 disabled) → `start` → running 30초 안 → 90초 관찰(ADSP QRTR 서비스, 새 rpmsg 채널, locator 조회 기록, 배터리)
- firmware 검증: adsp.mdt 55 phdr 중 적재 대상 51개가 모두 있다. b00(hash), b54는 추가 파일. dtb는 1/3

## B-2 결과 (boot-adsp-4a515df8…): **ADSP_RUNNING_OBSERVED**
- firmware 58개 설치(/lib/firmware, 0644). pdmapper PID 상주(registry-pdm-3a1d6dac.json), locator 0x40 inst 0/1 공개
- `start` → **running 543 ms**("Booting fw image adsp.mdt", "remote processor … is now up"). 90초 관찰 동안 running 유지, fault 0건, 배터리 100% 37.7°C, 화면/keeper/USB/Wi-Fi 정상
- ADSP(QRTR node 5)가 locator에 5번 조회: `tms/servreg`(→ root/audio/sensor/ois_pd 4개), `tms/pddump_disabled`, `tms/pdr_enabled`(→ 없음)
- node 5 서비스: 15(inst 32/33/34/36), **66 = servreg notifier inst 74**, 43 v2 inst 20, 51(inst 5/9/12), 769, **400(가설: SEE sensors client)**, 5017 v10
- 새 glink 채널: IPCRTR(qrtr_smd bind), **adsp_apps/adsp_apps2(gpr 채널, driver 없음)**, fastrpcglink-apps-dsp, sleepmonglink-apps-adsp, bt_cp_ctrl, glink_ssr, mhi_sat, pcie_drv, rpmsg_ctrl
- 다음: C단계(오디오 모듈 적재 → gpr가 adsp_apps에 bind → audio PD 상태 → ASoC 카드). sensors도 이 ADSP 위에서 진행할 수 있다

## C단계 설계 분석 (2026-09-19)
- 활성 DT 노드 ↔ 모듈 문자열 대응(`compatible\0` 검색):
  - 있음: gpr/spf_core/audio_prm/audio-pkt(glink-edge 자식), spf-core-platform, lpass-cdc(+clk-rsc-mngr, rx/tx/va macro), swr-mstr(swr_ctrl), wcd939x-codec/slave, lpi-pinctrl, msm-cdc-pinctrl(wcd_core), audio-ref-clk(wcd9xxx), msm-audio-ion(audpkt_ion), canoe-asoc-snd(machine), ext-disp-audio-codec-rx(machine)
  - **없음**: `qcom,msm-stub-codec`(사운드 카드 codec 목록 1번 — 없으면 카드가 등록되지 않는다), `qcom,wcd939x-i2c`(I2C 3-000e, wcd939x 제어 경로)
  - 무관: simple-sdca-amp(wsa-macro disabled), coresight 계열, cdsprm, usb-audio-qmi
- 카드 codec phandle 4개: stub-codec, lpass-cdc, wcd939x-codec, **wsa2 master 아래 swr_haptics**(부모 wsa2-macro가 disabled) → 사운드 노드의 `swr-haptics-unsupported`로 건너뛴다(가설, machine 문자열에 있음)
- sync_state: rpmh 레귤레이터(b1b/l15b/l1g)는 regulator-ocp-notifer가 남아 있어 **sync하지 않는다**. LPASS NoC 7400000/7420000은 **이미 synced**. apps-smmu/pinctrl/soc는 다른 미bound consumer가 많다
- 제외 예정: bt_fm_swr/btfmcodec/lpass_bt_swr(BT 오디오: btpower를 쓰므로 BT keeper와 충돌 위험), swr_haptics(대상 노드 disabled), swr_dmic(DMIC 노드 disabled), adsp_sleepmon, adsp_loader(ADSP는 이미 running)
- **중요(가설)**: vendor AudioReach에서는 ASoC 카드가 BE(backend) DAI와 mixer만 노출하고, 재생 데이터는 사용자 공간 AGM/GSL이 audio_pkt + ION 공유 메모리로 보낸다
  → C단계 목표는 "카드 등록 + BE 확인"이고, 소리는 D단계(GPR 그래프 클라이언트)가 필요하다

## stub_dlkm(99600b32…) 추가 → 38개 closure OK. `qcom,wcd939x-i2c` 드라이버는 Mac 어디에도 없다 → 이 보드는 USB-C 전환에 fsa4480을 쓴다(사운드 노드에 wcd939x-i2c-handle 없음) → 제외
## C-1 (boot-audio-c1-b85fe138…, PREFLIGHT PASS)
- q6_pdr → q6_notifier → snd_event → gpr → spf_core → audpkt_ion → audio_pkt → audio_prm → pinctrl_lpi → q6. 60초 관찰
- 매 tick 확인: ADSP running, pdmapper, BT keeper, 화면, GPU keeper, USB. 통과 조건: adsp_apps가 gpr에 bind

## C-1 결과 (boot-audio-c1-b85fe138…): **STAGE_AUDIO_C1_LOADED**
- adsp_apps → `qcom,gpr` bind, "gpr-lite probe success", GPR 장치 spf_core:2:3, audio-pkt:2:17, q6prm:2:7
- PDR 알림: `audio_notifier_service_cb: service PDR_ADSP, opcode 0x1` → **"gpr_adsp_up: Q6 is Up"**
- spf_core: `__spf_core_is_apm_ready` → **"apm is up"**, 자식 장치 생성(lpass-cdc, lpi_pinctrl, cdc pinctrl, sound, audio-ion …). msm-audio-ion(+cma) bind(iommu group 21)
- audio_prm: "prm probe success, Sleep API supported :1". fault 0건
- lpi_pinctrl은 공급자 `vote_lpass_audio_hw/core_hw`(audio-ref-clk, wcd9xxx_dlkm)를 기다린다 → C-2에서 풀린다
## C-2 (boot-audio-c2-08b5bbee…, PREFLIGHT PASS)
- aw882xx_acf.bin(vendor /firmware, d3133216…)을 /lib/firmware에 설치 → swr, swr_ctrl, wcd_core, wcd9xxx, lpass_cdc(+rx/tx/va), mbhc, sdca_registers, wcd9378, wcd939x_slave, wcd939x, wsa883x, wsa884x, stub, aw882xx, machine
- 60초 관찰: /proc/asound/cards·pcm, 장치 bind 목록, amp I2C bind, log. mixer 쓰기와 PCM open은 하지 않는다. 오디오 사용자 공간 데몬(pipewire/pulse)과 alsa-utils는 없다

## C-2 결과 (boot-audio-c2-08b5bbee…): STAGE_AUDIO_C2_LOADED, **카드 미등록(no soundcards)**
- 적재 18개 + aw882xx_acf.bin 설치. bind: lpi_pinctrl, audio-ref-clk 13개, lpass-cdc(+clk-rsc-mngr), rx/tx/va macro(tx는 va 등록을 한 번 기다린 뒤 성공),
  swr-mgr rx/va, **wcd939x_codec(WCD9395 v2.0, rx/tx slave devnum 1)**, stub-codec, cdc pinctrl, **aw882xx ×2(chip 2308, aif-0/1, Speaker_Playback_0/1)**
- 경고성 로그(FAULT 패턴 밖): swrm "version … not match with HW 0x2020000", "link status not disconnected", "SWR CMD error, fifo … flushing", "SWR bus clsh detected" → 기록만 한다
- **`soc:spf_core_platform:sound`(canoe-asoc-snd)가 bind되지 않음**, 로그 없음 → probe deferral로 추정
  - 후보: DT `qcom,ext-disp-audio-rx=1`인데 `msm-ext-disp-audio-codec-rx` 장치에 드라이버가 없다(보통 audio-kernel `hdmi_dlkm.ko`)
  - 확인: `/sys/kernel/debug/devices_deferred`(root 읽기)
- devices_deferred(사용자 sudo 읽기): `soc:spf_core_platform:sound`(사유 문자열 없음), `display_gpio_regulator@2`(pmd802x pinctrl 대기, 기존)
- hdmi_dlkm(5f7cfe6b…, depends msm_ext_display live, closure OK) → 39개
## C-3 (boot-audio-c3-0de65aa4…, PREFLIGHT PASS): hdmi_dlkm 1개 → ext-disp audio codec bind → 카드 deferred probe 재시도 → 60초 관찰

## C-3 결과 (boot-audio-c3-0de65aa4…): STAGE_AUDIO_C3_LOADED, ext-disp audio codec bind, **카드는 여전히 미등록**
- machine 문자열/DT: `qcom,wcn-bt=1`, `qcom,wcn-bt-ext=1` → BTFM_PROXY-RX/TX-0/1 DAI link의 codec이 `btfmcodec_dev`. machine `softdep=pre: btfmcodec`
- btfmcodec은 hardware endpoint가 등록될 때만 codec을 등록한다(`btfmcodec_register_hw_ep` ← bt_fm_swr). bt_fm_swr는 btpower에서 `btpower_get_chipset_version`만 쓴다(읽기 전용)
## C-4 (boot-audio-c4-b493279f…, PREFLIGHT PASS): btfmcodec → lpass_bt_swr → bt_fm_swr, 60초 관찰(카드, BT keeper, hci0)

## C-4 결과 (boot-audio-c4-b493279f…): **CARD_REGISTERED** — `card0 [canoeqrdsndcard]: canoe-qrd-snd-card`
- lpass_bt_swr → bt_swr_mstr(swr-mgr) → bt_fm_swr → btfmcodec_dev 등록 → deferred probe → canoe-asoc-snd bind
- PCM 41개(BE DAI): **MI2S-LPAIF-RX-PRIMARY = pcmC0D14p(multicodec → aw882xx ×2, 스피커)**, CODEC_DMA-LPAIF_RXTX-RX-0..5(WCD939x 헤드셋), VA-TX 0..2(마이크),
  DISPLAY_PORT-RX-0/1, BTFM_PROXY RX/TX 0/1, USB_AUDIO RX/TX, PCM_RT_PROXY, MI2S stub 여러 개
- fault 0건, BT keeper/hci0 유지, ADSP running, 화면/USB 정상
- 참고: AudioReach 구조라 이 PCM들은 BE이다. 재생 데이터는 AGM/GSL이 GPR(audio_pkt `/dev/aud_pasthru_adsp` 계열) + ION 공유 메모리로 DSP 그래프에 넣는다 → D단계

## D단계 로컬 조사 (2026-09-19)
### 참고 소스 (`ref/`, 공개 소스 다운로드)
- upstream Linux v6.12: `audioreach.h/.c`, `q6apm.c/.h`, `q6apm-dai.c`, `q6prm.c`, `apr.h`, `snd_ar_tokens.h`
- vendor audio-kernel-ar(GitHub CodeLinaro mirror, `la/audio-kernel-handset.lnx.11.0.r10-rel`): `ipc/audio-pkt.c`, `ipc/gpr-lite.c/.h`, `dsp/msm_audio_ion.c/.h`, `dsp/spf-core.c`, uapi `msm_audio.h`
### 사용자 공간 → DSP 경로(소스 확인)
- `/dev/aud_pasthru_adsp`(chrdev **488:0**, class aud_pasthru_adsp): write(GPR 패킷 전체, 헤더 포함) → gpr_send_pkt(dst_domain은 드라이버가 ADSP로 설정). read()로 응답을 받는다
  - 응답 라우팅: gpr-lite는 `dst_port`로 서비스를 찾는다. **등록되지 않은 포트는 audio-pkt(passthrough, reg 0x17)로 간다** → 사용자 공간은 임의의 src_port(예: GSL 방식 0x2001)를 쓸 수 있다
  - `APM_CMD_SHARED_MEM_MAP_REGIONS`: property_flag에 PHYS 비트가 없으면 `shm_addr_lsw` 자리에 **dma-buf fd**를 넣는다 → 드라이버가 msm_audio_ion에 등록된 IOVA로 바꾼다
- `/dev/msm_audio_ion`: `IOCTL_MAP_PHYS_ADDR = _IOW('a', 97, int)`(dma-buf fd → audio context bank에 attach, IOVA 기록), `IOCTL_UNMAP_PHYS_ADDR`(98). HYP_ASSIGN(109/110)은 필요할 때만
- dma-buf 할당: `/sys/class/dma_heap`: `system`, `qcom,system`, … → `/dev/dma_heap/system`(mknod) + DMA_HEAP_IOCTL_ALLOC
### 스피커 경로 설정 (vendor QRD 설정; Lenovo 전용 파일은 발견하지 못함)
- resourcemanager: PAL_DEVICE_OUT_SPEAKER → BE **MI2S-LPAIF-RX-PRIMARY**, 2ch, 48 kHz, bit_width 32(지원 형식 S24_LE), speaker_protection_enabled 1
- usecaseKvManager: DEVICERX key 0xA2000000 = SPEAKER 0xA2000001. mixer_paths "speaker"는 비어 있다(aw882xx는 DAPM/자체 제어)
- ACDB(`QRD_acdb_cal.acdb`, 'ACDB' 헤더, 청크 구조): PARAM_ID_I2S_INTF_CFG(0x08001019) 4곳, HW_EP_MF_CFG 19곳, CODEC_DMA_INTF_CFG 16곳 → module IID(0x469e, 0x4b16 …)와 묶여 있다. 완전한 파싱은 AudioReach graphservices(공개)의 ACDB 파서가 필요하다
- aw882xx DT: rx topo 0x1000ff01, port 0x1006(Awinic DSP 알고리즘) — 기본 출력에는 필요 없다고 본다(가설)
### 기기에 빌드 도구 없음(gcc/make/cmake 없음, libasound/tinyalsa 없음) → Python(ctypes/ioctl)로 구현한다
### 계획
- **(권장) A: Python 최소 그래프**(upstream audioreach.c의 패킷 형식을 옮긴다)
  - D-1: `APM_CMD_GET_SPF_STATE`(spf_core가 쓰는 명령, 응답 0x02001007)를 audio_pkt로 보내고 읽는다 → 사용자 공간 GPR 왕복 확인(부작용 없음)
  - D-2: dma-heap 할당 → msm_audio_ion MAP → APM shared-mem map → unmap(그래프 없음)
  - D-3: BE PCM `pcmC0D14p`를 ALSA ioctl(ctypes)로 open/hw_params/prepare(무음) → MI2S 클럭(PRM)과 aw882xx 시작 로그 확인 → close
  - D-4: GRAPH_OPEN(WR_SHARED_MEM_EP → PCM_CNV → I2S_SINK(LPAIF primary)) → PREPARE → START → 낮은 음량 사인파 → 사용자 청취(물리 확인)
- B: AudioReach 공개 사용자 공간(graphservices+ACDB, AGM, tinyalsa)을 Mac에서 aarch64로 빌드 → ACDB 그래프(스피커 보호 등) 그대로 사용. 품질은 좋지만 작업량이 크다

## D-1 (boot-audio-d1-34c0bfff…, PREFLIGHT PASS)
- `gprclient.py`: GPR 헤더 24 B(header 워드 = 0 | 6<<4 | size<<8), dst ADSP(2)/src APPS(3), src_port 0x2001(미등록 → audio-pkt), dst_port APM(1)
- `audiod1.py`: /dev/aud_pasthru_adsp(488:0, mknod 0600) → APM_CMD_GET_SPF_STATE(0x01001021, token 0x5A5A0001) → 응답 0x02001007 확인. open/release는 큐만 비운다(소스 확인)

## D-1 결과 (boot-audio-d1-34c0bfff…): **STAGE_AUDIO_D1_LOADED** — 사용자 공간 GPR 왕복 성공
- 요청 `6018000002030000012000000100000001005a5a21100001` → 응답 opcode **0x02001007**, status **1**(SPF ready). fault 0건
## D-2 (boot-audio-d2-…): 64 KiB dma-buf(system heap) → mmap 패턴 → ION MAP → APM SHARED_MEM_MAP(pool 3, flag 0, lsw=fd) → handle → UNMAP → ION UNMAP

## D-2 결과 (boot-audio-d2-919d43e0…): **STAGE_AUDIO_D2_LOADED**
- dma-buf 64 KiB alloc(fd 5) → mmap 패턴 OK → ION MAP ret 0 → APM MAP 응답 **0x02001001, handle 0xb0977228** → UNMAP 응답 basic(0x0100100D, **status 0**)
- ION UNMAP은 EINVAL("msm_audio_ion_free: dma_buf invalid"). 그 직전 로그 "spf_core_apm_close_all: wait event unblocked":
  **audio-pkt close가 APM_CMD_CLOSE_ALL과 ION 정리를 실행한 것으로 보인다**(가설) → ION 항목이 이미 지워져 있었다
  → 재생 중에는 audio-pkt fd를 계속 열어 두어야 한다. 해제 순서는 ION UNMAP → audio-pkt close로 한다
## D-3 (boot-audio-d3-219ad00c…): pcmC0D14p(MI2S-LPAIF-RX-PRIMARY) open → HW_REFINE/HW_PARAMS(S24_LE, 2ch, 48k, 240×4) → PREPARE → 3초 → DROP → HW_FREE → close. 샘플을 쓰지 않는다
- `alsapcm.py`: snd_pcm_hw_params 608 B(6.12 uapi), HW_PARAMS 0xc2604111
- D-3 1차(219ad00c): open OK(PCM 프로토콜 2.0.18, 116:16), 고정 조건(S24_LE/2ch/48k/240×4) HW_REFINE → **EINVAL**, 커널 로그 없음, prepare 전에 닫음
- D-3 2차(e3cd0336…): 제약 없는 HW_REFINE으로 허용 범위를 먼저 기록 → 형식(S24→S32→S16)/2ch/48k만 고정하고 period/buffer는 커널이 고른다

## D-3 2차 결과 (boot-audio-d3-e3cd0336…): **STAGE_AUDIO_D3_LOADED**
- 허용 범위(refine any): ch 1–384, rate 8k–96k, S16/S24/S32, period_size 3–4096, periods 2–128, **period_bytes 4096–8192**, buffer_bytes 4096–131072
  → 1차 EINVAL 원인: 240프레임×8B = 1920B < period_bytes 최소 4096
- HW_PARAMS: S24_LE/2ch/48k, period 512×32(4096 B) → PREPARE → DROP → HW_FREE → close 모두 성공. fault 0건
- aw882xx: startup → hw_params(48k, 24bit) → mute 0 → start_pa → **"pll&clk check fail", "mode1/mode2 check iis failed", "start failed, cnt:0..2"** → 레지스터 덤프
  → 예상대로 **I2S BCLK/WS가 없다**: MI2S 인터페이스는 DSP 그래프의 I2S sink 모듈이 START해야 구동된다(BE open만으로는 클럭이 없다). PRM 클럭 로그도 없었다
- D-4 설계 요점: BE PCM open/prepare(앰프 시작 재시도 창) + **GPR 그래프(WR_SHARED_MEM_EP → PCM_CNV → I2S_SINK LPAIF RX PRIMARY) START**를 같은 시간대에 수행, 이후 공유 메모리로 사인파

## D-4 설계 (boot-audio-d4-59e6be67…, PREFLIGHT PASS) — 첫 소리
- 추가 참고: AudioReach graphservices `spf/api`(apm_memmap_api.h: **IS_OFFSET_MODE = 0x4**, hw_intf_cmn_api.h: LPAIF=0, i2s_api.h: PORT in 2/out 1, SD0=1, WS internal=1),
  `gsl/src/gsl_shmem_mgr.c`(GSL은 offset 모드로 map하고 데이터 명령에 오프셋 주소를 쓴다, src_port 0x2002)
- `argraph.py`(upstream audioreach.c 형식 이식, host 테스트 3개): SG 0x7001[WR_SHARED_MEM_EP 0x7101 → PCM_CNV 0x7102] + SG 0x7002[I2S_SINK 0x7103],
  컨테이너 PP/STREAM, EP/GLOBAL_DEV, stack 8192, ADSP. I2S: LPAIF, PRIMARY, SD0, WS internal, 48k/16bit/2ch. 변환기 출력 deinterleaved unpacked
- `audiod4.py`: BE open+HW_PARAMS(S16_LE) → dma-buf/ION/MAP(offset) → GRAPH_OPEN → SET_CFG → PREPARE → START → BE PREPARE → 440 Hz −30 dBFS 3초(8 KiB 버퍼, 동시 4개) → STOP/CLOSE/UNMAP/ION/close/BE 정리
- 가설/위험: SD 라인(SD0)과 WS 소스가 틀리면 소리가 없거나 앰프 PLL이 여전히 실패한다. 스피커 보호/Awinic DSP 모듈은 없다(낮은 음량으로 제한)

## D-4 1차 (boot-audio-d4-59e6be67…): GRAPH_OPEN in-band → **basic status 1(AR_EFAILED)**, 정리 정상(UNMAP 0, ION unmap OK, BE close), 소리 없음
- ACDB 비교(로컬): 이 ACDB의 컨테이너는 **속성 7개**(type {version 1, **GC 0x0B001001 / SC 0x0B001000**}, pos, stack, domain, heap 0x08001174, parent 0x080010CB=0xFFFFFFFF, peer heap 0x0800124D)
  → upstream v6.12의 legacy capability id(PP=1, EP=3)와 4개 속성 대신 이 형식을 쓴다. ID 범위도 ACDB와 맞춘다(SG 0xB00070xx, 컨테이너 0xE00070xx, 모듈 0x4F0x)
  - 서브그래프는 ACDB에 추가 속성(0x08001374, 0x08001531, VSID)이 있지만 필수인지 모른다 → 3개 유지
- 2차(`boot-audio-d4-1d514401…`): GRAPH_OPEN/SET_CFG를 **out-of-band**(공유 메모리 오프셋 0, 8 KiB 명령 영역)로 보낸다 → 실패하면 SPF가 파라미터별 error_code를 적어 준다 → 기록
  - dma-buf CPU 쓰기/읽기마다 `DMA_BUF_IOCTL_SYNC`(0x40086200)로 캐시를 정리한다. 오디오 슬롯은 8 KiB부터 7개
- `boot-audio-d4-90e02403…`은 캐시 동기화 전 묶음이라 사용하지 않는다(SUPERSEDED)

## D-4 2차 (boot-audio-d4-1d514401…): **DSP 경로 전부 성공, 소리는 안 들림**
- MAP → GRAPH_OPEN(OOB) 0 → SET_CFG(OOB) 0 → PREPARE 0 → START 0 → 버퍼 71개 3.04초에 소비(실시간) → STOP/CLOSE/UNMAP 0 → 정리 OK
  → **컨테이너 type을 GC 형식(ACDB)으로 바꾸자 GRAPH_OPEN이 통과했다**. SPF가 I2S EP를 실시간으로 돌린다
- aw882xx: **`mode1_pll_check: done`**(D-3와 달리 MI2S 클럭 존재) → 그러나 `sysst_check fail reg_val=0x0011`(PLLS|CLKS, 스위칭 SWS/부스트 BSTS 없음) → start_pa 실패 반복
  - acf: C-4 카드 등록 때 `aw882xx_acf.bin`(project A1901, awinic 1.0.0.0) 로드, profile "Music" 적용 확인
  - 원인 가설: 앰프 start 재시도 창(약 1초) 동안 입력이 무음이었다(BE prepare가 첫 샘플보다 약 2초 앞섰다) / 슬롯 폭(16bit=32fs) 불일치 / SD 라인
- 3차(`boot-audio-d4-36cf7746…`): 사인파 5초, 첫 버퍼 2개가 끝난 뒤(약 85 ms) BE PREPARE → 앰프가 신호가 있는 상태에서 시작한다
- 3차(36cf7746): 신호가 흐르는 중에 BE prepare를 했는데도 **SYSST 0x0011 그대로**, start_pa 실패 → 무음 가설 기각. DSP 쪽 118 버퍼/4.99초 정상
- 4차(`boot-audio-d4-6a1d59ef…`): **32비트 슬롯(64fs)**: BE S32_LE, PCM_CNV 출력과 I2S EP 32bit(q31), 공유 메모리 입력은 16bit 유지
- 4차(6a1d59ef): **32비트 슬롯에서 두 앰프 모두 `aw882xx_start_pa: start success`**(처음). DSP 정상. 사용자: −30 dBFS 소리는 불확실
  → 16비트(32fs)가 앰프 시작 실패 원인이었다(acf 프로필이 64fs 기준으로 추정)
- 5차(`boot-audio-d4-2cad5b66…`): 구간 A = SD0/440 Hz, 구간 B = SD1/880 Hz, 각각 −18 dBFS 4초, 3초 간격. 각 구간은 BE/그래프를 새로 열고 닫는다. 앰프 start 로그를 요약 출력
- 5차(2cad5b66): 두 구간 모두 DSP 명령 0, 94 버퍼/3.99초, **두 구간 모두 앰프 4개 start success**. 사용자: **"삐 소리가 한번 들렸어"** → **물리 동작 확인(첫 소리)**. 어느 구간(SD0/440 Hz 또는 SD1/880 Hz)인지 확인 중
- 6차(9cffcffd): A = SD0 긴 삐 1번, B = SD1 짧은 삐 3번(660 Hz, −18 dBFS). 두 구간 모두 앰프 start success. 사용자: **"B만 들렸어"**
  → **스피커 데이터 라인 = SD1**(`argraph.SPEAKER_SD`, 기본값으로 고정). **물리 동작 확인: Y700 스피커 재생**
## 확정된 스피커 재생 조건 (3a1d6dac)
- BE `pcmC0D14p`(MI2S-LPAIF-RX-PRIMARY) S32_LE/2ch/48k open+hw_params → 그래프 [WR_SHARED_MEM_EP → PCM_CNV(→32bit, deinterleaved unpacked)] + [I2S_SINK LPAIF/PRIMARY/**SD1**/WS internal, 32bit]
  (컨테이너 GC type, 7 props) → OPEN/SET_CFG(OOB)/PREPARE/START → 데이터가 흐른 뒤 BE PREPARE(앰프 start) → 재생 → STOP/CLOSE/UNMAP → BE 정리
- 남은 것: 좌우 채널 확인, 음량/보호(Awinic 알고리즘·스피커 보호 없음 → 낮은 음량 유지), WAV 재생 도구, 다음 부팅 절차 통합

## D-5 (boot-audio-d5-86d0c2a2…, PREFLIGHT PASS): 좌우 채널 + WAV
- `wavtool.py`: PCM WAV 8/16/24/32bit, mono/stereo, 임의 샘플레이트 → s16 stereo 48 kHz(선형 리샘플), 전체 피크를 −12 dBFS 이하로 제한. host 테스트 6개
- `audiod5.py`: L = 왼쪽 채널만 긴 삐 1번, R = 오른쪽 채널만 짧은 삐 3번(660 Hz −18 dBFS), W = melody.wav(도레미 음계 상행/하행 4초, fb1979a3…). 각 클립마다 open/close, 간격 6초
- audiod4.run은 이제 임의 PCM 클립(`pcm=`)을 받는다. 기본 SD 라인 = SD1
- D-5 결과: **STAGE_AUDIO_D5_LOADED**. L 83/3.49 s, R 71/2.99 s, W 94/3.99 s 버퍼, 앰프 start success 6번, fault 0건. 사용자: **"다 잘 들렸어"** → 왼쪽/오른쪽 채널 모두 출력, **WAV 재생 동작(물리 확인)**
  - 채널 ↔ 물리적 좌/우 스피커 대응은 사용자가 따로 확인하지 않았다(미확정)

## 게임 소리 (2026-09-20, boot 684daae1) — 물리 동작 확인: Dead Cells 소리 + Kishi 패드
- 경로: 게임(x86, FEX) → x86 libpulse → session `pipewire-pulse` → sink "Y700 Speakers"(`desktop-service-20260919/audio/y700-speaker.conf`,
  별도 `pipewire -c` 클라이언트의 pipe-tunnel sink) → `/run/y700-desktop/y700-speaker.fifo`(s16le/2ch/48k) → `nextboot-impl/speakerd.py`(root)
  → D-5와 같은 그래프(SD1, 32bit 슬롯) → 스피커
- speakerd: 데이터가 오면 그래프를 연다(8–9 ms), 2초 무데이터/30초 무음이면 닫는다. 버퍼 4 KiB × 4(≈85 ms), FIFO 백로그 > 32 KiB면 오래된 쪽을 버린다.
  고정 감쇠 0.5(−6 dB; Awinic 보호 알고리즘이 없다). FIFO는 siwal 소유 FIFO인지 확인하고 O_NOFOLLOW로 연다. `--test` host 테스트
- WirePlumber: `audio/50-y700-no-alsa.conf`(~/.config/wireplumber/wireplumber.conf.d/) — canoe 카드의 BE PCM을 열지 않는다
- 결과: 248초 재생 11646 버퍼, DSP 오류 0, short read 6. host 테스트: x86 libpulse(FEX) 2초 톤 → FIFO에 2.01초, 피크 그대로
- 부팅 자동화(재부팅 검증 전): `y700-bringup.service` `--upto audio-c --wifi`, `y700-speakerd.service`(오디오 단계를 스스로 기다린다)
