# touch 읽기 전용 확인 (2026-09-19, boot 3a1d6dac)

## 결과: touch input 장치가 없다
- `/proc/bus/input/devices`에는 `gpio-keys`(input0)만 있다. `/dev/input`에 event node가 없다(by-path만 있음)
- `nvt_36xxx`는 적재되어 있다: `nvt_driver_init` start/finished, "SPI driver NVT-ts has no spi_device_id for novatek,NVT-ts-spi"(경고일 뿐, OF match로 bind 가능)
- **SPI 장치가 하나도 없다**(`/sys/bus/spi/devices` 비어 있음) → touch 노드 `soc/qcom,qupv3_4_geni_se@1ac0000/spi@1a80000/novatek@0`
  (compatible `novatek,NVT-ts-spi`, status ok)가 SPI device로 생성되지 않았다
- 상위 상태
  - QUP wrapper `1ac0000.qcom,qupv3_4_geni_se` → driver `geni_se_qup`(bind)
  - **SPI 컨트롤러 `1a80000.spi`(compatible `qcom,spi-geni`) → driver 없음**, waiting_for_supplier 0
  - 공급자 bind 확인: gcc, GPI DMA(`gpi_dma`), pinctrl(`canoe-pinctrl`), qnoc 1600000/16e0000/31100000, interconnect@0/@1, mdss_mdp(`msm_drm`)
  - 적재된 geni 계열: `i2c_msm_geni`, `msm_gpi`뿐이다. **SPI geni 드라이버 모듈이 없다(원인)**
- `1a80000.spi`는 GPU 분석 때 gcc/ICC의 미bound consumer 목록에 있었다. 적재하면 그 목록에서 빠진다
  → gcc에는 uart/pcie/vidc/cvp/cam-cpas가, ICC에는 uart/vidc/cvp/cam-cpas/cnss/qpace가 남는다. 전역 sync_state는 일어나지 않을 것으로 예상한다(적재 직전 재확인 필요)

## touch 드라이버 요구 사항 (nvt_36xxx.ko 문자열)
- depends: `panel_event_notifier`(적재됨)
- firmware: **`novatek_ts_fw.bin`**, `novatek_ts_mp.bin`(host download 방식 TDDI → probe 또는 panel 이벤트 시 IC에 내려받을 가능성이 크다)
- vendor 이미지 `/firmware`에서 추출(debugfs 읽기 전용):
  - novatek_ts_fw.bin 253952B `a374ce0856f9e61bd62de955e93d2585963a0636ff7390de2248386e896c114f`
  - novatek_ts_mp.bin 253952B `1b83ad26961e9e445b7f7bba172c0211928d64f82eea698e1685e8273b09ec30`

## 위험(가설)
- NT36523은 **TDDI**(display와 touch가 같은 IC)다 → touch firmware 다운로드나 IC reset이 **화면에 영향**을 줄 수 있다. DT에 `novatek,reset-gpio`가 있다
- SPI 컨트롤러 첫 probe(GPI DMA, QUP 4)는 처음 실행하는 경로다

## 결과 (boot 3a1d6dac, 2026-09-19)
- 적재 단계 `boot-touch-923cfc5625ebe7d7`: STAGE_TOUCH_LOADED
  - `spi-msm-geni` → `spi19.0` → NVT-ts. 칩 인식(Cascade), **firmware 다운로드 128.7ms 성공**(fw_ver 0x10, PID 0x609F, x_num 30, y_num 48)
  - input1 `NVTCapacitiveTouchScreen`(event1 13:65), input2 `NVTCapacitivePen`(event2 13:66)
  - 매 tick owner native state와 underrun 0/0 불변(TDDI인데도 화면 영향 없음). fault 0건
  - 참고: `spi_geni … Err getting HW version`(fault 패턴 아님)
- `/dev`는 tmpfs(devtmpfs 아님) → udev가 by-path 링크만 만든다. event node는 sysfs dev로 직접 mknod한다
- **이벤트 캡처(`touch-events.json`, 사용자 터치, 물리 동작 확인)**:
  - 범위: X 0–19040, Y 0–30400(= 1904×3040의 **10배**), slot 0–9(최대 10점), pressure 0–1
  - 순서대로 누른 점: 왼위 (1310,1420) / 오위 (18020,1620) / 오아래 (18050,29420) / 왼아래 (1290,29350) / 가운데 (11230,14700) / 두 손가락 (12090,7600)+(6360,7450)
  - → 사용자가 잡은 방향에서 touch X는 왼→오, Y는 위→아래(패널 세로 원본 방향). 동시 2점 확인
- 도구 버그 1회: 이름 패턴이 pen까지 맞아 시작 전에 STOP(변경 없음) → 정확한 이름으로 수정

## 화면↔touch 매핑 확정 (사용자 확인, 2026-09-19)
- 같은 방향으로 잡은 상태에서: 컬러바 **흰색(fb x=0)이 왼쪽**, **체커(fb y > 3/4)가 아래쪽**
- touch도 X=0이 왼쪽, Y=0이 위쪽 → **변환 없음(identity)**: `fb_x = touch_x / 10`, `fb_y = touch_y / 10`(범위 19040×30400 ↔ 1904×3040)
- 정정: `vkanim120-8b87…/visual-note.json`의 "framebuffer x=0이 사용자 오른쪽" 해석은 **그때 잡은 방향 기준**이었다. 화면/touch 좌우 반전의 근거가 아니다
