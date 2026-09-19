# Wi-Fi 읽기 전용 조사 (2026-09-19, boot 3a1d6dac)

## 하드웨어 (DT)
- `soc/qcom,cnss-peach@b0000000`: compatible `qcom,cnss-peach`, `supported-ids` 0x110e, `qcom,wlan-rc-num` 0(PCIe RC0)
  - GPIO: `wlan-en-gpio`, `qcom,bt-en-gpio`, sw-ctrl, host/dev SOL. `cnss_wlan_region` 32MiB reserved(d2000000)
  - 레일(pmu_vreg_map): AON/RFA/WLMX/BTCX/BTCMX 0.9 = S2J1, WLCX 0.9 = S1J1, RFA 1.9 = S8F0, RFA 1.3 / PCIe 0.9/1.2 = S7F0, + vrm-wcal(PBS)
- `soc/pcie@1c00000`: compatible `qcom,pci-msm`(pci-domain 0). cmdline에 이미 `pci-msm-drv.pcie_sm_regs=…`가 있다
- 관련 노드: `soc/qcom,qrtr-mhi-cnss`, `soc/qcom,cnss-direct-link`, `soc:wcn786x`(PMU/BT 쪽 추정), `adsp_mhi_region`

## 현재 상태
- `b0000000.qcom,cnss-peach`, `1c00000.pcie`: **드라이버 없음**(waiting_for_supplier 0, 공급자 전부 bind)
- 커널: cfg80211/mac80211/rfkill 미적재, MHI bus 없음, cnss2/wlan 없음, qrtr는 live, `/sys/bus/pci`는 있다
- 레일은 전부 disabled(users 0). 예외: S7F(`pmh0110_f_s7`)는 UFS가 사용 중(users 1, enabled)

## sync_state 위험 평가 (sysfs consumer 링크)
| 레귤레이터 | cnss 외 미bound consumer | cnss bind 시 sync_state |
|---|---|---|
| s7f(UFS 사용 중), s8f, l2g, l3g, s1j, s2j | regulator-ocp-notifer, wcn786x | 실행 안 됨 |
| l3k | regulator-ocp-notifer | 실행 안 됨 |
| vrm-wcal | 없음 | **실행됨**(이미 disabled, users 0 → 영향 없을 것으로 추정) |
- ICC 16c0000 / 31100000 / interconnect@1: pcie, uart, vidc 등 다른 미bound consumer가 남는다(적재 직전에 재확인)

## firmware / 설정 위치 (vendor 이미지, debugfs 읽기 전용)
- `/etc/wifi/peach_v2/WCNSS_qcom_cfg.ini`(3390B). `/firmware/wlan/qca_cld/peach_v2/`에는 **symlink**만 있다
  - `WCNSS_qcom_cfg.ini` → `/vendor/etc/wifi/peach_v2/…`, `wlan_mac.bin` → `/mnt/vendor/persist/peach_v2/wlan_mac.bin`
- **WLAN 본체 firmware(amss/m3/board data/regdb)는 vendor 이미지에 없다** → `modem_a`(sde6, 350MiB, 8:70, NON-HLOS)에 있을 가능성이 크다(미확인)
- MAC: `persist`(sda3, 32MiB, 8:3). 블록 node는 `/dev`에 없다(tmpfs) → 읽으려면 sysfs 번호로 mknod가 필요하다
- `/lib/firmware/regulatory.db`(+p7s) 있음

## 필요한 것 (예상, 확인 필요)
1. 모듈(Mac vendor_dlkm/system_dlkm): `pci-msm-drv`, MHI core, `qrtr-mhi`, `cnss2`, `cnss_prealloc`, `cnss_utils`, `cnss_nl`, WLAN host driver(qca_cld3 peach), `cfg80211`(+rfkill 등 GKI)
2. firmware: modem_a에서 peach용 파일 추출 + `WCNSS_qcom_cfg.ini`
3. userspace: `iw`, `wpa_supplicant`(Ubuntu 패키지 설치는 시스템 변경이다. 별도 승인 필요)

## 모듈 분석 (Mac에서 복사, 2026-09-19)
- 15개(pci-msm-drv, pcie-pdc, pci_phy_access, mhi, qrtr-mhi, cnss2, cnss_prealloc, cnss_utils, cnss_nl, cnss_plat_ipc_qmi_svc,
  wlan_firmware_service, qca_cld3_peach_v2, cfg80211, rfkill, mac80211): 모두 CRC가 있다. rfkill vermagic은 GKI 문자열이다(modversions라 허용)
- 예상 순서: rfkill → cfg80211 → pcie-pdc → pci-msm-drv → mhi → qrtr-mhi → cnss_prealloc → cnss_plat_ipc_qmi_svc → wlan_firmware_service
  → smem-mailbox → cnss_utils → cnss2 → cnss_nl → (IPA 사슬) → qca_cld3_peach_v2
- 추가 의존성: smem-mailbox(smem만, OK), **ipam → gsim, rmnet_mem, usb_f_gsi**(softdep: subsys-pil-tz, qcom-arm-smmu-mod)
- **DT에 IPA/GSI 노드가 없다**(compatible 검색 0건) → ipam/gsim은 probe할 장치가 없으므로 심볼만 제공할 것으로 추정한다(init 코드 확인 필요)
- 불필요 추정: mac80211(libarc4), pci_phy_access(eom_driver). cld3는 fullmac이다

## firmware (modem_a, 읽기 전용 복사 → FAT16 리더)
- modem_a.img 367001600B sha256 1a84bcd6…1e96(FAT16, 4 KiB 섹터). `/image/peach/` 10개 → `firmware/peach/`(SHA256SUMS)
  - amss20.bin, phy_ucode(20).elf, aux_ucode(20).elf, bdwlan.elf, regdb.bin, Data20.msc, qdss_trace_config_v1/v2.cfg
  - cnss2가 찾는 경로 `peach/%s`와 일치한다(v2 칩은 *20 변형)
- `/image`에 `ipa_fws.mdt/.b00–b04`, adsp.* 등도 있다(audio 단계에서 쓸 수 있다)
- 미확보: `wlan_mac.bin`(persist), `wifi_module_param.ini`(선택으로 추정)

## 적재 시험 설계 (2026-09-19)
- IPA 사슬: gsim/rmnet_mem/usb_f_gsi(dwc3-msm만 의존). 전체 closure 미해결 0. CRC 1575/2671 대조, 불일치 0
  - ipam/gsim init: platform driver 등록 + debugfs만. DT 노드가 없어 probe 없음(추정). usb_f_gsi: usb_function_register + chrdev/debugfs(gadget 구성 전에는 USB 무영향으로 추정)
- `WCNSS_qcom_cfg.ini` 추출(1b83aace…). IPA 설정 없음. `gEnableLpassSupport=1` 주의
- 단계(`make-boot-bundle.py`):
  - **wifi-a**: rfkill → cfg80211 → pcie-pdc → pci-msm-drv → mhi → qrtr-mhi → cnss_prealloc → cnss_plat_ipc_qmi_svc → wlan_firmware_service → smem-mailbox → cnss_utils → cnss2 → cnss_nl, 90초 관찰
  - **wifi-b**: rmnet_mem → gsim → usb_f_gsi → ipam → qca_cld3_peach_v2, 60초 관찰, `wlan0` 필요
  - 공통 매 tick: owner native state, underrun, keeper, **USB Ethernet carrier/operstate**, 템플릿 guard
  - 사전검사: /lib/firmware 11개 해시, ICC 3개와 레귤레이터 7개에 cnss/pcie 외 미bound consumer가 남아 있는지, vrm-wcal idle/disabled

## 결과: wifi-a (boot-wifi-a-a342…, 3a1d6dac)
- 1차 묶음은 preflight STOP: 16c0000 = `qcom,canoe-pcie_anoc`(PCIe 전용 NoC, consumer는 pcie_qtb(bound)/pcie/cnss뿐) → 이 구성일 때만 허용하도록 수정
- **STAGE_WIFI_A_LOADED**: 13개 적재, `1c00000.pcie` → pci-msm, `cnss-peach` → cnss2. **PCIe RC0 PHY ready, link initialized**,
  PCI `0000:00:00.0`(RC)과 `0000:01:00.0`(WLAN), MHI `mhi_110e_00.01.00`, cnss direct-link probe. fault 0건, 화면/USB/keeper 불변
- 칩 firmware 다운로드는 아직 없다: cnss2가 CBC 때문에 대기한다(`fs_ready` 필요, 문자열 근거)
- wifi-b: fs_ready=1 → 캘리브레이션 결과(180초 제한)를 확인한 뒤 모듈 5개를 올린다

## wifi-b 1차 (boot-wifi-b-74cda8c6…, 3a1d6dac): STOP, 모듈 적재 0개
- `fs_ready=1`을 쓴 뒤, 로그 버퍼가 순환하면서 **이미 검토한 ADCI hung 보고의 머리 줄이 잘렸다** → 머리 없는 `Call trace:`가 오탐 STOP을 일으켰다. 새 fault는 0건이다
- STOP 이후 캘리브레이션이 스스로 끝났다: PCIe 링크 up → QMI WLFW connected → **`cnss: Calibration took 8364 ms`** → 칩 전원 정상 해제
- 수정(사용자 승인 "bootguard 잘린 머리 규칙 승인"): `Review.rotated_head` + 테스트 7개(host 49개 통과). 캘리브레이션을 이미 마쳤으면 wifi-b는 fs_ready를 다시 쓰지 않는다
- `boot-wifi-b-c1f4…`은 skip 수정 전에 만든 묶음이다 → 쓰지 않는다(SUPERSEDED 표시). 실행할 묶음은 `boot-wifi-b-3b20f792…`

## wifi-b 2차 (boot-wifi-b-3b20f792…, 3a1d6dac): Wi-Fi 동작 — 출력 끝의 STOP은 이름 검사 때문
- 캘리브레이션을 다시 하지 않고 모듈 5개를 적재했다 → QMI WLFW 재연결(mission) → cld3 HDD 초기화, `phy0`
- netdev: **`wlp1s0`**(udev가 wlan0 이름을 바꿈, MAC <MAC>), `p2p0`, `wifi-aware0`. MHI 채널 ADSP_0-3, IPCR, LOOPBACK
- supervisor는 `wlan0` 이름만 검사해서 마지막에 STOP했다 → 이제 0000:01:00.0에 붙은 wireless netdev를 검사한다
- **NetworkManager와 wpa_supplicant가 이미 동작 중**이다(Ubuntu 기본). wlp1s0은 자동 관리되고 UP/disconnected, 저장된 연결은 없다
- 스캔: 2.4 GHz와 5 GHz(ch 44, 161) AP가 보인다(signal 100까지). **API 성공**
- 커널 fault 0건, D wait 3개 그대로. ADCI 잘린 머리는 규칙으로 허용했다(audit)
- 반복 잡음: `cnss: Fail to send genl ... QDSS`, `nl_srv_bcast ... cld80211 app id 27/28` → Android cnss-daemon/cld80211 수신자가 없어서다. 기능 문제는 아니지만 로그 순환을 빠르게 한다

## 접속 시험 (3a1d6dac, 사용자 nmcli, 연결 이름 y700-wifi-test, never-default, autoconnect no)
- <SSID>(공유기 LAN MAC = 2.4G BSSID 54:07:7d:21:2d:35로 식별) **5 GHz ch44, BSSID …:37, 1170 Mbit/s, signal 80, RSSI -43**
- Auth/Assoc 성공, DHCP <LAN-IP>/24. 기본 경로는 USB에 유지(wlp1s0 subnet route metric 2000)
- `ping -I wlp1s0 <LAN-IP>`: 5/5, 평균 14.0 ms. 인터넷 ping은 wlp1s0에 기본 경로가 없어서 예상대로 실패
- 커널 fault 0건, D wait 3개 그대로, 배터리 100% 37.5°C → **물리 동작 확인(무선 연결)**
