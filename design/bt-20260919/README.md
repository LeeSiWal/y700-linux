# Bluetooth 조사 (2026-09-19, 기기 세션, boot 3a1d6dac) — 로컬 분석, 기기 변경 없음

## 하드웨어 (DT, sysfs 읽기)
- 전원 노드 `soc:wcn786x`(compatible `qcom,wcn786x`, Android `btpower` 드라이버): bt-reset-gpio, sw-ctrl-gpio, fmd-clk-gpio,
  wl-reset-gpio, 공급 전원 10개(aon/dig/rfa0p75/1p25/1p8/wlan-aon/vdd12-io/vdd18-aon/ant-ldo), qmp, pdc 테이블
  - 공급자는 **모두 bound**(rpmh 레귤레이터 7개, clk-rpmh, sdam, spmi gpio, pinctrl). driver는 없다
- HCI UART: alias `hsuart0` = `1994000.qcom,qup_uart`(`qcom,msm-geni-serial-hs`, QUP3). 공급자 모두 bound, driver 없음
  - 내장 `qcom_geni_serial`은 이 compatible을 지원하지 않는다 → vendor `msm_geni_serial.ko`가 필요하다(`/dev/ttyHS0`)
- BT 오디오(LPASS BT SWR, `btfmswr_slave`)는 audio 단계에서 다룬다
- cnss 로그의 `Disabling BT_EN`: Wi-Fi 쪽이 probe할 때 BT_EN을 끈다. BT 전원은 btpower가 따로 켠다

## 커널(GKI 6.12 config)
- `BT=m`, `BT_HCIUART=m`, `BT_HCIUART_QCA=y`, `H4=y`, `SERDEV=y`, `BT_QCA=m`, `BT_BCM=m`, `UHID/HID=y`, `ECDH=y`
- `OF_OVERLAY` 없음, `POWER_SEQUENCING_QCOM_WCN` 없음 → DT에 serdev BT 자식을 추가할 수 없다
- **문제**: hci_uart QCA 프로토콜은 serdev가 아니면(ldisc/btattach) soc_type을 `QCA_ROME`로 가정한다 → WCN786x(`hmt*` TLV)와 맞지 않는다(가설, 소스 기준)
- 선택지
  - (A, 권장) Android와 같은 방식: btpower로 전원 ON → **사용자 공간 Python이 `/dev/ttyHS0`로 rampatch/NVM TLV를 다운로드**(HCI vendor EDL 명령) → ldisc `N_HCI` + `HCIUARTSETPROTO H4`로 hci0 등록 → BlueZ
  - (B) hci_uart QCA(ROME 경로)에 firmware 이름을 맞춰 시도한다. soc별 차이가 커서 비권장
- BlueZ 사용자 공간(bluetoothd/bluetoothctl/btattach)은 **설치되어 있지 않다** → 나중에 apt 설치(별도 승인)

## 필요한 파일
- 모듈(Mac): vendor `btpower.ko`, `msm_geni_serial.ko` / GKI system_dlkm `bluetooth.ko`, `hci_uart.ko`, `btqca.ko`, `btbcm.ko`
- firmware: `bluetooth_a` 파티션(sde7, 8:71, 8 MiB, ro 0). 읽기 전용 복사는 기존 `wifi-20260919/copy-partition.py`가 지원한다
- BD 주소: persist 또는 NVM 기본값(미확인)

## bluetooth_a (사용자 sudo 복사, 2026-09-19): 8388608 B, sha a618bdbd…, **FAT12**
- `fat12.py`(읽기 전용, fat16.py 재사용)로 `/image` 11개를 `firmware/`에 추출했다(SHA256SUMS)
- 버전 `BTFW.BRAHMA.2.0.1-00238-PATCHZ-1_DEF` → 칩 BT 코어 이름은 **Brahma 2.0**(`brh*`), upstream linux-firmware/hci_qca(6.12)에는 없다 → 방식 A 확정
  - `brhbtfw20.tlv`(293904, TLV type 1 rampatch), `brhbtnv20.bin`(14049, TLV type 2 NVM), `brhperifw20.tlv`(26000, type 1 peripheral fw),
    `brhperinv20.bin/.b43/.b45`, `brhbcscal20.bin`, `.mbn` 2개(ELF, 서명본)
- 모듈 해시(Mac): btpower 2ffc5a60…, msm_geni_serial 5160585c…(vendor_boot와 같음), bluetooth 0f644582…, hci_uart 409c70d1…, btqca ef557865…, btbcm 080c85f7…

## 모듈 확인 (기기 복사, 2026-09-19)
- 6개 해시 = Mac 출력. GKI 4개는 vermagic이 커널과 정확히 같다. btpower/msm_geni_serial은 `…-maybe-dirty-4k`(vendor, modversions → 기존 vendor 모듈과 같은 처리)
- 의존성: bluetooth←rfkill(live), btqca/btbcm←bluetooth, **hci_uart←bluetooth,btbcm,btqca,pwrseq-core**(`POWER_SEQUENCING=m`, 없음 → Mac에서 추가 필요),
  btpower←cnss_utils,qcom_aoss,pinctrl-msm,rfkill(모두 live), msm_geni_serial←qcom_ipc_logging(live)
- msm_geni_serial: compatible `qcom,msm-geni-serial-hs`만 있다(→ hsuart0 하나, `ttyHS`). 디버그 UART a9c000은 `qcom,geni-debug-uart` → 내장 qcom_geni_serial이 이미 bound이므로 겹치지 않는다. console=ttynull
- btpower(CLO `bt-kernel/pwr/btpower.c`, BT+UWB 다중 기술 SoC 상태기계): chrdev + `bt_class`, ioctl 상수(코드에서 mov 즉시값):
  `BT_CMD_PWR_CTRL 0xbfad`, `UWB_CMD_PWR_CTRL 0xbfe1`, `BT_CMD_ACCESS_CTRL 0xbfe4`, `UWB_CMD_ACCESS_CTRL 0xbfe5` (그 밖의 명령은 비교 연산이라 목록 미확정)
  - `PEACH_SOC_VERSION_2_0`, BT_EN/bt-reset/wl-reset GPIO, sw_ctrl, AOP/PDC 재설정, grant(`BT_HAS_GRANT`/`BT_WAITING_FOR_GRANT`) 문자열
  - "Peri"(주변 CPU, UWB와 공유) 크래시 사유에 `Peri TLV/NVM download failed` → **BT 켜기에 `brhperifw20.tlv`/`brhperinv20.*` 다운로드가 필요할 수 있다**(가설)
- 방식 A 순서(초안): msm_geni_serial + btpower 적재 → /dev node(mknod, sysfs dev) → ACCESS_CTRL → PWR_CTRL(on) →
  ttyHS0 115200에서 HCI 버전 확인(EDL 0xFC00) → rampatch/NVM(+peri) TLV 다운로드 → baud 변경 → HCI reset → N_HCI ldisc + H4 → hci0

## bt-a 단계 설계 (2026-09-19, 3a1d6dac)
- `pwrseq-core.ko` b84ef2c5…(GKI vermagic). `check-imports.py`: 7개 모듈 모두 미해결 심볼 0, CRC 589 대조 불일치 0 → `modules.sha256`
- `make-boot-bundle.py bt-a`: msm_geni_serial → btpower. 칩 전원 ON, ttyHS open, ioctl은 **하지 않는다**
- sync_state 분석:
  - wcn786x의 rpmh 레귤레이터 7개는 `soc:regulator-ocp-notifer`(unbound)가 남아 있어 **sync하지 않는다**(preflight에서 정확히 확인)
  - **`soc:interconnect@0`(qcom,canoe-clk_virt, QUP core 경로)는 BT UART가 마지막 미bound consumer다 → bind하면 sync_state가 실행된다**.
    다른 consumer 7개(touch SPI 1a80000, I2C 5개, 디버그 UART a9c000)는 모두 bound이고 각자 투표한다. Android에서도 부팅 뒤 같은 상태다. consumer 집합이 정확히 같을 때만 허용한다
- 첫 묶음 `boot-bt-a-72bc…`: preflight STOP(consumer 이름 형식 버그) → 수정. 실행할 묶음은 `boot-bt-a-b50aac0f…`(PREFLIGHT PASS)
- ModemManager가 동작 중이다 → post에서 ttyHS를 연 프로세스가 없는지 확인한다

## bt-a 결과 (boot-bt-a-b50aac0f…, 3a1d6dac): **STAGE_BT_A_LOADED**
- `1994000.qcom,qup_uart` → msm_geni_serial, **ttyHS0 492:0**(GSI 아님, FIFO 64). `soc:wcn786x` → bt_power, chrdev **btpower 491:0**(class bt-dev)
- btpower probe: multi_tech_soc=1, 레귤레이터 9개 정보만 읽었다(켜지 않음), PDC init table 설정, bt_gpio_resetb 없음(bt-reset-gpio 사용)
  - probe가 PMIC SDAM FMD nvmem 셀 3개(fmd_set, fmd_chg_pon, fmd_cnt2_stop)를 설정했다(Android와 같은 동작, 기록해 둠)
- clk_virt `state_synced` 0→1(예상대로). 화면, keeper, USB는 매 tick 정상. fault 0건. ttyHS를 연 프로세스 없음. 배터리 100% 37.1°C

## btpower ioctl 해독 (bt_ioctl, 자체 미니 디스어셈블러)
- `cmd - 0xbfac` 범위 0..0x3a 점프 테이블: 0xbfad PWR_CTRL(arg ≤ 2, 이름 표 "Power OFF/ON/Retention"), 0xbfae CHIPSET_VERS(set), 0xbfaf GET_CHIPSET_ID,
  0xbfb0 CHECK_SW_CTRL, 0xbfb1 GETVAL_POWER_SRCS, 0xbfb2 FMD, 0xbfc0 IPA_TCS, **0xbfc1/0xbfc2 KERNEL_PANIC(절대 사용 금지)**, 0xbfe1 UWB PWR,
  0xbfe2/0xbfe3 REGISTRY(BT/UWB), 0xbfe4/0xbfe5 ACCESS(grant), 0xbfe6 UWB SSR state
- bt-probe(`btprobe.py`): POWER_ON → ttyHS0 115200 CRTSCTS에서 EDL 버전 요청(01 00 FC 01 19), 응답이 없으면 HCI reset → POWER_OFF. firmware 다운로드는 없다

## bt-probe 결과 (boot-bt-probe-70037e22…, 3a1d6dac): **API 성공 — 칩 응답**
- POWER_ON 102 ms(ret 1): regulator vote on → BT_EN low→high, wl-reset 1, sw_ctrl 1, bt-reset-gpio 834 = 1. 상태 "BT powered ON"
- EDL 버전 요청 응답(Command Complete): `04 0e 12 01 00fc 00 19 0c | 21000000 586d 0002 00022140`
  → **product_id 0x21, patch(ROM) 0x6d58, rom_ver 0x0200, soc_id 0x40210200** (Peach 2.0, `brh*20` 파일과 일치)
- POWER_OFF ret 0, 상태 "ALL Client OFF". Wi-Fi(wlp1s0) up 유지, 연결 끊김 없음. 화면, keeper, USB 정상, fault 0건
- firmware 헤더: `brhbtfw20.tlv` type 1, product 0x21, rom 0x200, patch 0x8d38, **download_mode 3(세그먼트별 VSE/CC 이벤트 없음)**. `brhperifw20.tlv`도 같은 product/rom/patch. `brhbtnv20.bin` type 2(NVM, 14045 B)

## bt-b / bt-keeper 설계 (2026-09-19)
- `bt-b`(boot-bt-b-c956771d…, PREFLIGHT PASS): pwrseq-core → bluetooth → btbcm → btqca → hci_uart. HCI 장치는 만들지 않는다. post: `/sys/class/bluetooth` 비어 있음, `n_hci 15` ldisc 등록
- `bt-keeper.py` + `run-btkeeper.py`(stage `btkeeper`, bt-b 뒤에 만든다: 모듈 목록 identity):
  POWER_ON → EDL 버전(0x21/0x0200/0x40210200 일치 필수) → PATCH_CONFIG(0x28, 기록만) → rampatch TLV(243 B 세그먼트, mode 3: 이벤트 없음) →
  NVM(**H4용 수정 2바이트**: tag17 data[0] IBS bit7 해제(이미 0), data[1] baud 0x11(3.2M)→0x00(115200); tag27 deep sleep bit0 해제) →
  HCI_Reset/Read_Local_Version/Read_BD_ADDR 확인 → TIOCSETD N_HCI + HCIUARTSETPROTO H4 → hci0. 실패하면 tty close + POWER_OFF 후 hold
  - fd는 끝까지 보유한다. supervisor는 attach 뒤 30초 관찰하고 `registry-bt-<boot>.json`을 쓴다
- host 테스트 53개 통과(test_bt 4개 추가)
- bt-b 결과: **STAGE_BT_B_LOADED**. Bluetooth core 2.22, HCI UART 2.3(H4/LL/Broadcom/QCA), `n_hci 15`, `/sys/class/bluetooth` 비어 있음, fault 0건
- btkeeper 묶음: `boot-btkeeper-d74ae51a…` PREFLIGHT PASS

## btkeeper 1차 (boot-btkeeper-d74ae51a…): STOP RESET_FAILED — 전원 OFF 확인, fault 0건
- POWER_ON 105 ms, 버전 일치, PATCH_CONFIG CC status 0(**명령 지원됨**), rampatch 293904 B/1210 세그먼트 27.3 s 동안 이벤트 없음(mode 3대로)
- **NVM을 이벤트를 기다리지 않고 연속 전송한 것이 오류였다**: 세그먼트마다 CC(`04 0e 05 01 00fc <status> 1e`)가 왔고, status는 첫 번째만 00, 나머지는 0x10/0x01
  → btqca도 NVM은 dnld_type NONE(세그먼트마다 응답을 기다림)으로 보낸다
- 그 뒤 HCI_Reset에 CC 대신 251 B vendor 이벤트(`04 ff fb 01 09 …`, 메모리 덤프 형태)가 왔다 → 컨트롤러 이상으로 판단 → POWER_OFF ret 0
- keeper PID 14170은 전원 OFF 상태로 /dev/btpower를 계속 잡고 있다(종료하지 않음). Wi-Fi up 유지
- 수정(`boot-btkeeper-e66fd45f…`): NVM 세그먼트마다 CC status 0 확인, rampatch 중 이벤트가 오면 STOP, rampatch 뒤 버전 재확인.
  이전 keeper(14170, log sha 고정)만 btpower 보유자로 허용한다

## btkeeper 2차 (boot-btkeeper-e66fd45f…): STOP NVM_FAILED — 전원 OFF 확인, fault 0건
- rampatch 뒤 컨트롤러가 **마지막 세그먼트에 CC 1개(`040e050100fc001e`)를 보낸다**(btqca 주석과 같다: 마지막 패킷에만 응답). 이 응답을 VERSION 응답으로 잘못 읽었다
  → 이후 응답이 하나씩 밀렸다. read_event는 한 번에 두 패킷이 오면 하나를 버렸다 → 58번째 NVM 세그먼트에서 시간 초과
- 수정(3차): 지속 수신 버퍼(패킷을 버리거나 합치지 않는다), rampatch 뒤 ack를 명시적으로 기다림, 모든 EDL 응답은 요청 타입 바이트(0x19/0x28/0x1e)까지 확인,
  대기 중인 패킷이 남아 있으면 다음 명령을 보내지 않는다. NVM 마지막 세그먼트는 10초 대기. pty 재생 테스트 2개 추가(host 55개 통과)
- keeper 14170, 14400은 전원 OFF 상태로 유지(허용된 보유자 목록)

## btkeeper 3차 (boot-btkeeper-3d166387…): STOP NO_VERSION — 전원 OFF 확인, fault 0건
- 전원 ON 순서는 앞선 3번과 같았다(platform regulator vote on, BT_EN/reset high). 하지만 첫 EDL 버전 요청에 2초 동안 응답이 없었다
- 원인 미확정(가설): 직전 실행(NVM 전체 전송 후 OFF) 뒤 BT 코어 준비가 늦다 / CTS가 낮아 요청이 전송되지 않았다
- 4차(`boot-btkeeper-a8aff2c8…`): open 직후와 매 시도마다 modem line(CTS/RTS)과 송수신 대기 바이트를 기록한다. 버전 요청을 최대 6번(약 15초) 보낸다(요청이 아직 대기 중이면 다시 보내지 않는다)
- 보유자 목록: 14170, 14400, 14635(모두 전원 OFF)

## btkeeper 4차 (boot-btkeeper-a8aff2c8…): STOP RAMPATCH_EVENTS — 전원 OFF 확인, fault 0건
- CTS/RTS high, outq 0. 버전은 첫 시도에서 응답(3차의 무응답은 일시적이었다)
- PATCH_CONFIG 응답이 2초 안에 오지 않았고 rampatch 전송 중에 도착했다(`040e050100fc0028`) → **컨트롤러 응답이 2초를 넘을 때가 있다**(3차 무응답도 같은 원인으로 추정)
- 5차: 모든 명령 응답을 6초까지 기다리고 지연 시간(ms)을 기록한다. PATCH_CONFIG가 실패하면 rampatch 전에 멈춘다. 보유자 목록 14170/14400/14635/14838

## btkeeper 5차 (boot-btkeeper-6564ab80…): STOP PATCH_CONFIG_FAILED — 전원 OFF 확인, fault 0건
- 버전은 즉시 응답. 1초 대기(중복 응답 확인) 뒤 보낸 PATCH_CONFIG에는 6초 동안 응답이 없었다
- 정리: 응답이 즉시 온 경우(probe, 1·2차)는 명령 사이에 쉬는 시간이 없었다. 늦거나 없었던 경우(3·4·5차)는 앞에 약 1초의 idle이 있었다
  → **가설: ROM 상태의 컨트롤러는 한 번 통신한 뒤 idle이 이어지면 잠들고, 이후 들어오는 바이트로 늦게 깬다**(4차에서는 rampatch 스트림 중에 응답이 왔다)
- 6차: 명령 사이 idle을 0.2초 이하로 유지한다(중복 확인 0.05초, NVM 뒤 0.2초). 보유자 목록 5개(모두 전원 OFF)

## btkeeper 6차 (boot-btkeeper-6afead76…): STOP NO_RAMPATCH_ACK — 전원 OFF 확인, fault 0건
- 명령을 쉬지 않고 연달아 보내자 PATCH_CONFIG가 **3.8 ms**에 응답했다. rampatch 전송 뒤 마지막 ack는 5초 안에 오지 않았다(1·2차에서는 다음 명령을 보낸 뒤에야 도착했다)
- **근본 원인(msm_geni_serial 분석)**: 사용자 공간 clock vote가 없으면 HS UART가 idle일 때 runtime suspend된다. 그 뒤 들어오는 RX는 wakeup byte(0xfd)로 시작하지 않으면 버린다
  ("dropping Rx data as wakeup byte not found"). 다음 TX가 포트를 깨우면 늦게 도착한다. 컨트롤러가 잠드는 것이 아니었다. Android BT HAL은 이 vote를 잡고 있다
- ioctl(msm_geni_serial_ioctl 해독): 0x54ed vote_clock_on, 0x54ee vote_clock_off, 0x54ef active 확인, 0x54ec TIOCFAULT(사용 안 함)
- 7차: tty open 직후 vote_clock_on. 실패 경로에서는 vote_off 후 close한다. attach 뒤에는 vote를 계속 유지한다. 보유자 6개(모두 전원 OFF)

## btkeeper 7차 (boot-btkeeper-e7a9f6fe…): 다운로드 성공, 그 뒤 debug ACL에서 STOP — 전원 OFF 확인, fault 0건
- vote_clock_on ret 0 → **전송 문제 해결**: PATCH_CONFIG 3.7 ms, RAMPATCH_ACK 수신, **패치 뒤 버전 patch 0x6d58→0x8d38(파일과 일치)**, NVM 58/58 세그먼트 모두 status 0(25–532 ms)
- NVM 뒤 컨트롤러가 ACL 패킷 `02 dc2e 2400 …`(handle 0x2EDC = hci_qca QCA_DEBUG_HANDLE, SoC 로그)을 보냈다 → 파서가 이벤트만 허용해서 멈췄다
- 8차: 0x2EDC ACL은 소비하고 개수만 기록한다(다른 ACL은 오류). NVM 뒤에 btqca와 같이 `QCA_DISABLE_LOGGING`(0xfc17, 14 00)을 보낸다. 테스트 58개 통과. 보유자 7개(모두 전원 OFF)

## btkeeper 8차 (boot-btkeeper-48feb820…): **BT_KEEPER_READY hci0**
- 전체 순서 성공: POWER_ON → vote_clock_on → 버전(ROM 0x6d58) → PATCH_CONFIG 3.7 ms → rampatch 27.3 s + ack → 버전(**patch 0x8d38**) → NVM 58/58 ack →
  DISABLE_LOGGING(0xfc17) 3.4 ms(그 전에 debug ACL 2개, 64 B) → **HCI_Reset OK 7.4 ms** → Read_Local_Version: HCI/LMP 14(**Bluetooth 5.4**), manufacturer 29(Qualcomm), subver 0x8d38 →
  BD_ADDR **<MAC>**(NVM 기본값, 기기 고유 주소가 아닐 수 있음: Android는 persist에서 읽어 설정한다) → N_HCI + H4 → **hci0**
- 30초 관찰: hci0 등록, rfkill2(hci0) unblocked, rfkill1 `bt_power`(btpower 등록) soft=1. keeper PID 15835가 ttyHS0/btpower를 보유. 커널 fault 0건, hci 오류 로그 없음
- hci0는 DOWN(`HCI_AUTO_OFF`: 사용자 공간 관리자가 없으면 초기화 뒤 자동으로 내려간다) → power on/스캔에는 BlueZ(apt, 별도 승인) 또는 root HCI 도구가 필요하다
- registry `registry-bt-3a1d6dac.json`. 실패 keeper 7개는 전원 OFF 상태로 유지(재부팅하면 정리)
- **다음 부팅 절차**: bt-a → bt-b → btkeeper(8차 코드). bt-probe는 필요 없다

## blescan 단계 설계 (boot-blescan-e0b78a66…, PREFLIGHT PASS)
- `blescan.py`: HCIDEVUP(hci0) → raw HCI socket, 10초 LE active scan(legacy 0x200B/0x200C, 거부되면 extended 0x2041/0x2042) → scan off → HCIDEVDOWN
- 연결, 페어링, 광고, 주소 변경은 하지 않는다. 결과(주소, RSSI, 이름, company id)는 묶음의 result.json에만 남긴다
- 매 tick 확인: 화면, GPU keeper, **BT keeper(15835)**, USB, Wi-Fi 상태

## blescan 결과 (boot-blescan-64a1acc3…, 3a1d6dac): **STAGE_BLESCAN_LOADED — 무선 수신 확인**
- 1차(e0b78a66): HCIDEVUP은 성공했다(226 ms). HCI_FILTER setsockopt가 EINVAL(14 B 전달, 6.12는 sizeof(hci_ufilter)=16 필요) → 스캔 없이 hci0 down
- 2차: HCIDEVUP 227 ms(flags UP|RUNNING) → legacy LE scan 파라미터 거부(0x0C Command Disallowed: 커널이 확장 스캔을 쓰는 컨트롤러로 초기화) → **extended scan 성공**
  10초 동안 광고 보고 1047개, 기기 28개(이름 있는 기기 2개), RSSI -39…-101 dBm, 대부분 Apple(company 76)과 Microsoft(6). scan off 0, hci0 down 복귀
- 커널 fault 0건. 화면, GPU keeper, BT keeper, USB, Wi-Fi는 매 tick 정상 → **API 성공 + 물리 동작(무선 수신) 확인**
