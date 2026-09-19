# Sensors 조사 (2026-09-19, boot 3a1d6dac, ADSP running)

## 참고 소스 (ref/)
- libssc(Codeberg DylanVanAssche/libssc, GPLv3): data/*.proto, src/*.c/h — SUID sensor 0xABAB…/0xABAB…, msg 512/768(SUID), 1/128(attributes), 513/514/10(enable/disable), 1025/769(report)
- libqmi `data/qmi-service-ssc.json`: service 400, Control 0x0020(TLV 0x01 data u16-len, 0x10 report type), 응답 0x02/0x10 client id/0x11, indication 0x0021/0x0022(0x01 client id, 0x02 data)

## 도구 (nextboot-impl)
- `pb.py`(protobuf 최소 인코더/디코더), `sscclient.py`(QRTR QMI SSC 클라이언트), `sensors-discover.py`(읽기 전용: SUID 조회 + 속성)

## 결과 (API 확인, 읽기 전용, 사용자 권한)
- SSC 서비스: QRTR node 5 port 20. Control 요청은 모두 result 0(client id 부여)
- SUID 응답(msg 768)은 **약 1초보다 늦게** 온다(6초 대기 시 도착). 내용:
  - `suid` → SUID 1개(자기 자신), `accel` → **0개**, `registry` → **0개**(1차 19종 조회 모두 0)
- 해석(가설): SSC 프레임워크는 떴지만 **센서 드라이버와 registry가 초기화되지 않았다**. Android에서는 센서 PD가 FastRPC로 HLOS 파일을 읽는다
  (`/vendor/etc/sensors/sns_reg_config`: input json.lst → output `/mnt/vendor/persist/sensors/registry/registry`, 설정 json들).
  이 파일 제공은 `adsprpcd sensorspd`(FastRPC listener, apps_std 파일 연산)가 한다 → Ubuntu에는 없다
- GKI config: `QCOM_FASTRPC` 없음(upstream fastrpc 드라이버 없음) → vendor `frpc-adsprpc.ko`(Mac vendor_dlkm에 있음)와 vendor uapi가 필요하다

## vendor 센서 설정 (vendor-probe.img, vendor/)
- `sns_reg_config`, `hals.conf`(sensors.qsh.so, dynamic), `json.lst`(kaanapali_* 보드 json + sns_* 알고리즘 json + qsh_camera …)
- 센서 후보 json: lsm6dsv / icm536xx(가속·자이로), mmc56x3x / qmc630x(지자기), tmd3765 / stk3acx(조도·근접), bu52053nvx(홀), sx932x / aw963xx(SAR), lps22df(기압), sht4x(온습도)
- 실제 보드 registry는 persist(sda3) `sensors/registry/`에 있을 것이다(미확인)

## 앞으로의 경로 (선택)
- A. FastRPC(vendor adsprpc) + Python listener(apps_std 파일 서버: vendor json + persist registry) + 센서 PD attach → SSC에 센서가 나타나는지 확인. 작업량 큼(hexagonrpcd 참고, 공개 소스)
- B. 센서는 보류하고 전원/충전 단계로 이동

## A 경로 조사 (2026-09-19)
- 참고: `ref/hexagonrpc/`(github linux-msm/hexagonrpc, GPL-3+: hexagonrpcd = FastRPC reverse tunnel, adsp_listener next2, apps_std 파일 서버, INIT_ATTACH_SNS)
  `ref/dsp-kernel/`(CodeLinaro vendor dsp-kernel `dsp-kernel.lnx.3.4.r2-rel`: dsp/fastrpc.c + fastrpc_rpmsg.c, compat "qcom,fastrpc", uapi는 **upstream과 같은 ioctl**:
  INVOKE _IOWR('R',3), INIT_ATTACH _IO('R',4), INIT_CREATE ('R',5), MMAP ('R',6), INIT_ATTACH_SNS _IO('R',8) …)
- 이 기기 DT: `remoteproc-adsp/glink-edge/qcom,fastrpc`(label **lpass**, channel fastrpcglink-apps-dsp, compute-cb@1..6, vmids 0x16/0x25) → 새 vendor fastrpc 형식.
  rpmsg 장치 `…fastrpcglink-apps-dsp` 존재(ADSP 부팅 후), 드라이버 없음
- 필요: Mac vendor_dlkm `frpc-adsprpc.ko`(compat 확인 필요), persist(sda3) 읽기 전용 복사(센서 registry)
- 설계 초안: frpc 적재 → /dev/fastrpc-* 노드 → Python hexagonrpcd 이식(INIT_ATTACH_SNS, adsp_listener next2 루프, apps_std: fopen/fread/fclose/fseek/stat/opendir…)
  → 제공 트리: vendor `/etc/sensors/config/*.json`, `sns_reg_config`, persist `sensors/registry/*` → SSC SUID 재조회

## 확인 (2026-09-19)
- `frpc-adsprpc.ko` cab94de1…: vendor vermagic, depends qcom-scm, mem_buf_dev, pdr_interface, qcom_glink(모두 live), alias `qcom,fastrpc`, misc 장치 `fastrpc-%s`, closure OK(CRC 173/0 불일치)
- persist.img(33554432 B, ext4): `/sensors/registry/registry/` **336개 파일**(이 보드용으로 생성된 registry), `sensors_list.txt`:
  sensor_temperature, gyro, accel, persist_stationary_detect, tilt, persist_motion_detect, pedometer, step_detect, gravity, sig_motion, device_orient,
  gyro_cal, game_rv, hall, sar, mag, mag_cal, geomag_rv, rotv, proximity, ambient_light, wake_gesture
  - 하드웨어 후보(registry): icm536xx(가속/자이로), bu52053nvx(홀), aw963xx(SAR), mmc56x3x/qmc630x(지자기), tmd3765/stk3acx(조도/근접), lps22df(기압) 등
  - dynamic lib: `sns_tppe.so`(dsp 파티션일 것, 지금은 제공하지 않음)
- vendor `/etc/sensors` 전체 추출(config json 97개, sns_reg_config)
## Python hexagonrpcd 이식 (nextboot-impl)
- `fastrpc.py`: INVOKE 마샬링(prim_in/out, seq 버퍼, sc), remotectl open/close
- `hexrpcd.py`: INIT_ATTACH_SNS → adsp_default_listener register → adsp_listener init2/next2 루프. 로컬 인터페이스 remotectl/apps_std/apps_mem
  - apps_std: fopen_with_env(**쓰기 거부**), fread, fclose, fflush, fseek, opendir/readdir/closedir, stat. apps_mem: ADD_PAGES mmap만
  - VFS(`rootmap.json`): /mnt/vendor/persist/sensors, /persist/sensors → persist tree, /vendor/etc/sensors, /system/vendor/etc/sensors → vendor, /sys/devices/soc0 → 실제 sysfs
- host 테스트 6개(프레이밍 왕복, VFS 경로 탈출 차단, read/EOF, 쓰기 거부, stat 크기, readdir EOF, remotectl)
## S-1 frpc (boot-frpc-8fafca99…, PREFLIGHT PASS): frpc-adsprpc 적재 → fastrpc rpmsg bind, /sys/class/misc/fastrpc-*, compute-cb bind 확인(30초). 장치는 열지 않는다

## S-1 결과 (boot-frpc-8fafca99…): **STAGE_FRPC_LOADED**
- `fastrpcglink-apps-dsp` → qcom,fastrpc, domain **lpass2000**(sysfs /sys/kernel/fastrpc/lpass2000), misc **fastrpc-lpass2000 10:113**, **fastrpc-adsp-secure 10:112**
- compute-cb@1..6 → qcom,fastrpc-cb(iommu group 22–27). PDR: **msm/adsp/audio_pd, sensor_pd, ois_pd "is up"**(pdmapper 경유). fault 0건
- 모듈 문자열 "untrusted app trying to attach to privileged DSP PD", DT에 qcom,fastrpc-gids 없음 → sensors PD attach는 **secure 노드**를 쓴다(Android adsprpcd sensorspd도 secure 노드)
## S-2 (boot-sensors-9a582621…, PREFLIGHT PASS)
- `run-sensors.py`: /dev/fastrpc-adsp-secure mknod → hexrpcd(INIT_ATTACH_SNS, 읽기 전용 VFS, 트리 441파일 digest a4fceb7b… 고정) → LISTENER_READY 20초 안 → 60초 요청 관찰 → SSC 재조회(11종, 6초 대기 + 속성) → registry-sns

## S-2 결과 (boot-sensors-9a582621…): **FastRPC 역방향 통신 성공**, 센서는 0개
- OPEN secure → **ATTACHED sensorspd** → DEFAULT_LISTENER_REGISTERED → LISTENER_READY. PD 요청 12개:
  remotectl open(apps_std)×2, fopen_with_env(ADSP_LIBRARY_PATH, /vendor/etc/sensors/sns_reg_config) ×2, oemconfig.so(없음 → ENOENT), stat sns_reg_config(271), stat/fopen config/json.lst(2373)
  → 그 뒤 요청이 멈췄다. SSC 11종 모두 0개. hexrpcd PID 25261은 next2에서 대기 중(살아 있음). fault 0건
- 원인 가설: **fread 응답 버퍼 길이** — hexagonrpcd는 DSP가 요청한 capacity 크기의 버퍼를 그대로 돌려준다(유효 길이는 prim_out). 이 이식본은 읽은 바이트만 돌려줬다(fread는 로그도 없었다)
- 수정: fread 응답을 capacity까지 0으로 채운다. 모든 호출(fread/fclose/fseek)에 시간과 함께 로그. INIT_ATTACH_SNS는 최대 10번 재시도
- S-2b(`boot-sensors-9eaf2e86…`): registry의 이전 hexrpcd(25261)에 SIGTERM → 종료 확인 → 새 hexrpcd attach → 60초 관찰 → SSC 재조회

## S-2b 결과 (boot-sensors-9eaf2e86…): **fread 수정 효과 확인**, 센서는 0개
- 이전 hexrpcd 25261 SIGTERM → 새 hexrpcd 재attach 성공(ADSP 영향 없음, fault 0건)
- 0.23초 동안: sns_reg_config 읽기 → **registry/DIR를 쓰기로 열기(거부)** → json.lst → config json 약 90개(fread 149번) → 요청 멈춤
  → PD가 json을 파싱해 **registry를 다시 쓰려는** 흐름이다. 쓰기가 막혀서 멈춘 것으로 본다
- 수정: apps_std 쓰기 계열(Qualcomm quic/fastrpc `idl/apps_std.idl`, skel 번호): fwrite 5, ftell 8, flen 10, fileExists 22, fsync 23, fremove 24, mkdir 29, ftrunc 32, frename 33
  - 쓰기는 `rw`로 표시된 prefix에서만 허용한다. run-sensors가 **묶음 안에 persist sensors 트리 사본(persist-rw/)을 만들어** 그곳만 쓰게 한다(기기 persist 파티션과 설계 사본은 쓰지 않는다)
  - host 테스트 8개(쓰기 영역 제한, 읽기 전용 영역 쓰기/삭제 거부, rename, fileExists)
- S-2c(`boot-sensors-7bc737b3…`): hexrpcd 25526 교체 → 쓰기 가능한 사본으로 재attach → 60초 → SSC 재조회

## S-2c 결과 (boot-sensors-7bc737b3…): 재attach 뒤 **요청 0개**, PD 상태 변화 없음, 센서 0개
- 해석: 센서 PD의 registry 초기화는 PD 시작 시 한 번뿐이다. 앞선 실행(S-2b)에서 쓰기 거부 뒤 멈춘 상태로 남았다
## S-2d (boot-sensors-fb91dfef…, PREFLIGHT PASS)
- hexrpcd 25833 교체 → **sensor_pd만 재시작**: upstream `pdr_restart_pd()`와 같은 QMI `SERVREG_RESTART_PD_REQ`(0x24, TLV 0x01 "msm/adsp/sensor_pd")를
  servreg notifier(service 0x42 inst 74, node 5 port 2)로 보낸다 → dmesg에서 sensor_pd down/up 확인(40초) → 쓰기 가능한 사본으로 hexrpcd attach → 60초 → SSC 재조회
- 위험: PD 재시작은 처음이다(ADSP 전체 재시작은 아니다). audio_pd, root PD는 그대로 두는 요청이다

## S-2d 결과 (boot-sensors-fb91dfef…): STOP — **RESTART_PD 거부**(QMI result 1, error 69), 그 밖의 변화 없음
- hexrpcd 25833은 종료된 상태이고, 새 hexrpcd는 시작하지 않았다(센서 PD에 listener 없음). ADSP running, sensor_pd는 up 그대로, fault 0건
- 결론: 이번 부팅의 sensor_pd는 쓰기가 거부된 registry 초기화에서 멈춰 있고, 사용자 공간에서 PD만 재시작할 수 없다(ADSP 전체 재시작은 오디오까지 흔든다 → 하지 않는다)
- **다음 부팅에서 처음 attach부터 쓰기 가능한 registry 사본으로** 다시 시도한다: runbook에 `frpc` → `sensors` 추가(check-runbook OK, 227개)
  (다음 부팅에는 previous가 없으므로 PD 재시작 요청은 들어가지 않는다)

## boot 8e18ad1f: writable copy from the first attach -> still 0 sensors; root cause found [로컬 분석]
- boot-sensors-5a517d22: 300+ requests, registry pass ran with RW copy, but 161 reverse calls had inbufs > 256 B
  (fwrite of registry json fragments, 258..1267 B) -> hexrpcd answered AEE_EUNSUPPORTED ("STOP: large inbufs") ->
  the PD stopped after 1.244 s (last request: frename of a ccd_* file). Upstream hexagonrpcd has the same gap.
- Fix: hexrpcd.get_rest(): adsp_listener_get_in_bufs2 = listener method 5 (in ctx, in offset, rout seq bufs,
  rout bufsLenReq; quic/fastrpc src/adsp_listener_stub.c _stub_method_4, sc 1 in / 2 out), offset = 256. 2 tests.
- builder: restart_pd dropped (refused on 3a1d6dac). New bundle boot-sensors-04315bf7 replaces hexrpcd 7796.
- boot-sensors-04315bf7 (fixed hexrpcd, listener replaced): only 4 setup requests, no registry retry -> the PD had
  finished its (failed) pass and does not re-ask this boot. The fixed hexrpcd must serve the FIRST attach -> next boot.
