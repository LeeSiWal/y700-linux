#!/usr/bin/env python3
"""Create a one-shot stage bundle (pstore | display | gpu) for the CURRENT boot from the reviewed module files of boot
b4e16b26. Reads live identity (boot_id, root, slot, all live module build-ids). No device writes except the bundle dir.
Usage: python3 -B make-boot-bundle.py <stage> [--out DIR]   (default DIR: ~/y700-agent/boot-<stage>-<hash16>)"""
from pathlib import Path
import hashlib, json, os, re, shutil, struct, sys

HERE = Path(__file__).resolve().parent
AGENT = Path('/home/siwal/y700-agent')
MODS = json.loads((Path('/home/siwal/y700-design/modules-current-boot.json')).read_text())
# Reviewed after b4e16b26 (touch bring-up, 3a1d6dac): vendor_dlkm copy from the Mac, 110 imports resolved, CRC 90/111 cross-checked
EXTRA = {'spi-msm-geni.ko': {'path': '/home/siwal/y700-design/touch-20260919/spi-msm-geni.ko',
                             'sha256': '3906bf08d166083580b2dad71e34deaa788dc5add8e137d8e46f7755127c8a02'}}
# Wi-Fi closure reviewed on 3a1d6dac (vendor/system_dlkm copies from the Mac): all imports resolved, CRC 1575/2671 cross-checked, 0 mismatch
for _l in Path('/home/siwal/y700-design/wifi-20260919/modules.sha256').read_text().split('\n'):
    if _l.strip():
        _h, _f = _l.split(); EXTRA[_f] = {'path': '/home/siwal/y700-design/wifi-20260919/' + _f, 'sha256': _h}
# Bluetooth closure reviewed on 3a1d6dac (vendor/system_dlkm copies from the Mac): all imports resolved, CRC 589 cross-checked, 0 mismatch
for _l in Path('/home/siwal/y700-design/bt-20260919/modules.sha256').read_text().split('\n'):
    if _l.strip():
        _h, _f = _l.split(); EXTRA[_f] = {'path': '/home/siwal/y700-design/bt-20260919/' + _f, 'sha256': _h}
# Audio/ADSP closure reviewed on 3a1d6dac (vendor_dlkm copies from the Mac): 37 modules, all imports resolved, CRC cross-checked, 0 mismatch
for _l in Path('/home/siwal/y700-design/audio-20260919/modules.sha256').read_text().split('\n'):
    if _l.strip():
        _h, _f = _l.split(); EXTRA[_f] = {'path': '/home/siwal/y700-design/audio-20260919/' + _f, 'sha256': _h}
# Sensors/FastRPC (3a1d6dac): frpc-adsprpc from vendor_dlkm, imports resolved, CRC 173 cross-checked, 0 mismatch
for _l in Path('/home/siwal/y700-design/sensors-20260919/modules.sha256').read_text().split('\n'):
    if _l.strip():
        _h, _f = _l.split(); EXTRA[_f] = {'path': '/home/siwal/y700-design/sensors-20260919/' + _f, 'sha256': _h}
# PMIC ADC (3a1d6dac): vendor_dlkm copies from the Mac, check-imports: 56 imports resolved, CRC 48 cross-checked, 0 mismatch
for _l in Path('/home/siwal/y700-design/adc-20260919/modules.sha256').read_text().split('\n'):
    if _l.strip():
        _h, _f = _l.split(); EXTRA[_f] = {'path': '/home/siwal/y700-design/adc-20260919/' + _f, 'sha256': _h}
ORDER = {
    'pstore': ['qcom_dynamic_ramoops.ko'],
    'display': ['msm_hfi_core.ko', 'nvmem_qfprom.ko', 'qti-fixed-regulator.ko', 'nvt_36xxx.ko', 'ipclite.ko', 'msm_hw_fence.ko',
                'synx-driver.ko', 'qcom_va_minidump.ko', 'sync_fence.ko', 'msm_ext_display.ko', 'gh_irq_lend.ko', 'smcinvoke_dlkm.ko',
                'hdcp_qseecom_dlkm.ko', 'drm_display_helper.ko', 'msm_drm.ko'],
    'owner': [],
    'keeper': [],
    'ownerfinish': [],
    'touch': ['spi-msm-geni.ko'],
    'wifi-a': ['rfkill.ko', 'cfg80211.ko', 'pcie-pdc.ko', 'pci-msm-drv.ko', 'mhi.ko', 'qrtr-mhi.ko', 'cnss_prealloc.ko', 'cnss_plat_ipc_qmi_svc.ko', 'wlan_firmware_service.ko', 'smem-mailbox.ko', 'cnss_utils.ko', 'cnss2.ko', 'cnss_nl.ko'],
    'bt-a': ['msm_geni_serial.ko', 'btpower.ko'],
    'bt-probe': [],
    'bt-b': ['pwrseq-core.ko', 'bluetooth.ko', 'btbcm.ko', 'btqca.ko', 'hci_uart.ko'],
    'btkeeper': [],
    'blescan': [],
    'qrtr-smd': ['qrtr-smd.ko'],
    'adsp': [],
    'audio-c2': ['swr_dlkm.ko', 'swr_ctrl_dlkm.ko', 'wcd_core_dlkm.ko', 'wcd9xxx_dlkm.ko', 'lpass_cdc_dlkm.ko', 'lpass_cdc_rx_macro_dlkm.ko',
                 'lpass_cdc_tx_macro_dlkm.ko', 'lpass_cdc_va_macro_dlkm.ko', 'mbhc_dlkm.ko', 'sdca_registers_dlkm.ko', 'wcd9378_dlkm.ko',
                 'wcd939x_slave_dlkm.ko', 'wcd939x_dlkm.ko', 'wsa883x_dlkm.ko', 'wsa884x_dlkm.ko', 'stub_dlkm.ko', 'aw882xx_dlkm.ko', 'machine_dlkm.ko'],
    'audio-c3': ['hdmi_dlkm.ko'],
    'audio-d1': [],
    'audio-d2': [],
    'audio-d3': [],
    'audio-d4': [],
    'audio-d5': [],
    'frpc': ['frpc-adsprpc.ko'],
    'sensors': [],
    'susp-freezer': [],
    'adc': ['qcom-vadc-common.ko', 'qcom-spmi-adc5-gen3.ko'],
    'susp-devices': [],
    # canoe-asoc-snd with qcom,wcn-bt=1 builds BTFM proxy DAI links on the btfmcodec_dev component, which btfmcodec registers only
    # when bt_fm_swr (SoundWire slave on lpass_bt_swr) registers its hardware endpoint. bt_fm_swr uses btpower only for
    # btpower_get_chipset_version (read-only).
    'audio-c4': ['btfmcodec.ko', 'lpass_bt_swr_dlkm.ko', 'bt_fm_swr.ko'],
    # next boot: C-2 + C-3 + C-4 of 3a1d6dac in one stage; hdmi and BT-audio codecs load before machine so the card probes once
    'audio-c': ['swr_dlkm.ko', 'swr_ctrl_dlkm.ko', 'wcd_core_dlkm.ko', 'wcd9xxx_dlkm.ko', 'lpass_cdc_dlkm.ko', 'lpass_cdc_rx_macro_dlkm.ko',
                'lpass_cdc_tx_macro_dlkm.ko', 'lpass_cdc_va_macro_dlkm.ko', 'mbhc_dlkm.ko', 'sdca_registers_dlkm.ko', 'wcd9378_dlkm.ko',
                'wcd939x_slave_dlkm.ko', 'wcd939x_dlkm.ko', 'wsa883x_dlkm.ko', 'wsa884x_dlkm.ko', 'stub_dlkm.ko', 'aw882xx_dlkm.ko',
                'hdmi_dlkm.ko', 'btfmcodec.ko', 'lpass_bt_swr_dlkm.ko', 'bt_fm_swr.ko', 'machine_dlkm.ko'],
    'audio-c1': ['q6_pdr_dlkm.ko', 'q6_notifier_dlkm.ko', 'snd_event_dlkm.ko', 'gpr_dlkm.ko', 'spf_core_dlkm.ko', 'audpkt_ion_dlkm.ko',
                 'audio_pkt_dlkm.ko', 'audio_prm_dlkm.ko', 'pinctrl_lpi_dlkm.ko', 'q6_dlkm.ko'],
    'wifi-b': ['rmnet_mem.ko', 'gsim.ko', 'usb_f_gsi.ko', 'ipam.ko', 'qca_cld3_peach_v2.ko'],
    'gpu': ['coresight.ko', 'msm_sysstats.ko', 'msm_performance.ko', 'governor_msm_adreno_tz.ko', 'governor_gpubw_mon.ko',
            'governor_msm_adreno_ro.ko', 'msm_kgsl.ko']}
REQUIRES = {'pstore': [], 'display': ['qcom_dynamic_ramoops'], 'owner': ['msm_drm'], 'keeper': ['msm_kgsl', 'msm_drm'], 'ownerfinish': ['msm_drm'], 'touch': ['msm_drm', 'nvt_36xxx', 'panel_event_notifier', 'qcom_ipc_logging', 'msm_gpi', 'msm_kgsl'],
            'wifi-a': ['msm_drm', 'msm_kgsl', 'qrtr', 'qmi_helpers', 'smem', 'qcom_ramdump', 'dwc3_msm'],
            'wifi-b': ['rfkill', 'cfg80211', 'pcie_pdc', 'pci_msm_drv', 'mhi', 'qrtr_mhi', 'cnss_prealloc', 'cnss_plat_ipc_qmi_svc', 'wlan_firmware_service', 'smem_mailbox', 'cnss_utils', 'cnss2', 'cnss_nl'],
            'bt-b': ['msm_drm', 'msm_kgsl', 'rfkill', 'msm_geni_serial', 'btpower'],
            'audio-c2': ['msm_drm', 'msm_kgsl', 'gpr_dlkm', 'spf_core_dlkm', 'audio_prm_dlkm', 'pinctrl_lpi_dlkm', 'audpkt_ion_dlkm', 'q6_notifier_dlkm',
                         'snd_event_dlkm', 'fsa4480_i2c', 'fsa4480_sub_i2c', 'hqsysfs', 'qti_regmap_debugfs', 'socinfo'],
            'audio-c4': ['msm_drm', 'msm_kgsl', 'btpower', 'swr_dlkm', 'swr_ctrl_dlkm', 'spf_core_dlkm', 'wcd_core_dlkm', 'snd_event_dlkm', 'hdmi_dlkm', 'machine_dlkm'],
            'sensors': ['msm_drm', 'msm_kgsl', 'frpc_adsprpc', 'qrtr_smd', 'qcom_q6v5_pas'],
            'susp-freezer': ['msm_drm', 'msm_kgsl', 'hci_uart', 'qca_cld3_peach_v2', 'qcom_q6v5_pas'],
            'adc': ['msm_drm', 'msm_kgsl', 'qcom_ipc_logging', 'qti_battery_charger'],
            'susp-devices': ['msm_drm', 'msm_kgsl', 'hci_uart', 'qca_cld3_peach_v2', 'qcom_q6v5_pas'],
            'frpc': ['msm_drm', 'msm_kgsl', 'qcom_scm', 'mem_buf_dev', 'pdr_interface', 'qcom_glink', 'qcom_q6v5_pas'],
            'audio-d5': ['msm_drm', 'msm_kgsl', 'machine_dlkm', 'aw882xx_dlkm', 'audio_pkt_dlkm', 'audpkt_ion_dlkm', 'gpr_dlkm', 'spf_core_dlkm'],
            'audio-d4': ['msm_drm', 'msm_kgsl', 'machine_dlkm', 'aw882xx_dlkm', 'audio_pkt_dlkm', 'audpkt_ion_dlkm', 'gpr_dlkm', 'spf_core_dlkm'],
            'audio-d3': ['msm_drm', 'msm_kgsl', 'machine_dlkm', 'aw882xx_dlkm', 'audio_prm_dlkm', 'lpass_cdc_dlkm'],
            'audio-d2': ['msm_drm', 'msm_kgsl', 'audio_pkt_dlkm', 'gpr_dlkm', 'spf_core_dlkm', 'audpkt_ion_dlkm', 'machine_dlkm'],
            'audio-d1': ['msm_drm', 'msm_kgsl', 'audio_pkt_dlkm', 'gpr_dlkm', 'spf_core_dlkm', 'machine_dlkm'],
            'audio-c3': ['msm_drm', 'msm_kgsl', 'msm_ext_display', 'machine_dlkm', 'wcd939x_dlkm', 'aw882xx_dlkm', 'stub_dlkm', 'lpass_cdc_dlkm'],
            'audio-c': ['msm_drm', 'msm_kgsl', 'gpr_dlkm', 'spf_core_dlkm', 'audio_prm_dlkm', 'pinctrl_lpi_dlkm', 'audpkt_ion_dlkm', 'q6_notifier_dlkm',
                        'snd_event_dlkm', 'fsa4480_i2c', 'fsa4480_sub_i2c', 'hqsysfs', 'qti_regmap_debugfs', 'socinfo', 'btpower', 'msm_ext_display'],
            'audio-c1': ['msm_drm', 'msm_kgsl', 'qrtr_smd', 'qcom_q6v5_pas', 'pdr_interface', 'rproc_qcom_common', 'qcom_ipc_logging', 'qcom_scm'],
            'adsp': ['msm_drm', 'msm_kgsl', 'qrtr', 'qrtr_smd', 'qcom_q6v5_pas', 'qcom_sysmon', 'pdr_interface', 'qcom_glink_smem'],
            'qrtr-smd': ['msm_drm', 'msm_kgsl', 'qrtr', 'qcom_glink', 'qcom_glink_smem', 'qti_pmic_glink', 'pdr_interface'],
            'blescan': ['msm_drm', 'msm_kgsl', 'bluetooth', 'hci_uart', 'btpower', 'msm_geni_serial'],
            'btkeeper': ['msm_drm', 'msm_kgsl', 'msm_geni_serial', 'btpower', 'bluetooth', 'hci_uart', 'btqca', 'btbcm', 'pwrseq_core'],
            'bt-probe': ['msm_drm', 'msm_kgsl', 'cnss2', 'msm_geni_serial', 'btpower'],
            'bt-a': ['msm_drm', 'msm_kgsl', 'cnss2', 'cnss_utils', 'qcom_aoss', 'pinctrl_msm', 'rfkill', 'qcom_ipc_logging', 'msm_gpi'],
            'gpu': ['qcom_dynamic_ramoops'] + [f[:-3].replace('-', '_') for f in ORDER['display']]}
BIND_AFTER = {'pstore': {}, 'display': {'hfi': 'msm_hfi_core', 'fence': 'msm_hw_fence'}, 'owner': {}, 'keeper': {}, 'ownerfinish': {}, 'touch': {}, 'wifi-a': {}, 'wifi-b': {}, 'bt-a': {}, 'bt-probe': {}, 'bt-b': {}, 'btkeeper': {}, 'blescan': {}, 'qrtr-smd': {}, 'adsp': {}, 'audio-c1': {}, 'audio-c': {}, 'audio-c2': {}, 'audio-c3': {}, 'audio-c4': {}, 'audio-d1': {}, 'audio-d2': {}, 'audio-d3': {}, 'audio-d4': {}, 'audio-d5': {}, 'frpc': {}, 'sensors': {}, 'susp-freezer': {}, 'susp-devices': {}, 'adc': {}, 'gpu': {}}
BIND_NOW = {'pstore': [], 'display': [], 'owner': ['hfi', 'fence'], 'keeper': ['hfi', 'fence'], 'ownerfinish': ['hfi', 'fence'], 'touch': ['hfi', 'fence'], 'wifi-a': ['hfi', 'fence'], 'wifi-b': ['hfi', 'fence'], 'bt-a': ['hfi', 'fence'], 'bt-probe': ['hfi', 'fence'], 'bt-b': ['hfi', 'fence'], 'btkeeper': ['hfi', 'fence'], 'blescan': ['hfi', 'fence'], 'qrtr-smd': ['hfi', 'fence'], 'adsp': ['hfi', 'fence'], 'audio-c1': ['hfi', 'fence'], 'audio-c': ['hfi', 'fence'], 'audio-c2': ['hfi', 'fence'], 'audio-c3': ['hfi', 'fence'], 'audio-c4': ['hfi', 'fence'], 'audio-d1': ['hfi', 'fence'], 'audio-d2': ['hfi', 'fence'], 'audio-d3': ['hfi', 'fence'], 'audio-d4': ['hfi', 'fence'], 'audio-d5': ['hfi', 'fence'], 'frpc': ['hfi', 'fence'], 'sensors': ['hfi', 'fence'], 'susp-freezer': ['hfi', 'fence'], 'susp-devices': ['hfi', 'fence'], 'adc': ['hfi', 'fence'], 'gpu': ['hfi', 'fence']}
FIRMWARE = {'gen80200_sqe.fw': 'e2d4e74282f50e40788b83b9ba4a5f5894c7975ed356c03a0161372d78f12932',
            'gen80200_aqe.fw': 'ed7916ca84e663d63fa94b0d40cc8c222e362e6cff860c539a8cca30d3721b68',
            'gen80200_gmu.bin': '06f4484fa06f91638aa4ae2412051bcddffdad69a7e2f83849c72412e6daa44d',
            'gen80200_zap.mbn': '25e836c603d52107a2cbb2f1a7eb46997837e94c2fb70bb5cc44b5e700d8936a'}
COMMON = ['bootmon.py', 'bootguard.py', 'runtime-readers.py', 'review-templates.json']
CODE_BY_STAGE = {'pstore': ['load-stage.py'], 'display': ['load-stage.py'], 'gpu': ['load-stage.py'],
                 'owner': ['run-owner.py', 'display-owner.py', 'drmkms.py', 'drmabi.py', 'ownerlog.py', 'borrow_owner.py', 'registry.py'],
                 'keeper': ['run-keeper.py', 'gpu-keeper.py', 'kgslabi.py', 'keeperlog.py', 'registry.py'],
                 'touch': ['load-stage.py', 'registry.py'],
                 'wifi-a': ['load-stage.py', 'registry.py'],
                 'wifi-b': ['load-stage.py', 'registry.py'],
                 'bt-a': ['load-stage.py', 'registry.py'],
                 'bt-probe': ['load-stage.py', 'registry.py', 'btprobe.py'],
                 'bt-b': ['load-stage.py', 'registry.py'],
                 'blescan': ['load-stage.py', 'registry.py', 'blescan.py'],
                 'qrtr-smd': ['load-stage.py', 'registry.py', 'qrtrns.py'],
                 'audio-c1': ['load-stage.py', 'registry.py', 'qrtrns.py'],
                 'audio-c2': ['load-stage.py', 'registry.py', 'qrtrns.py'],
                 'audio-c': ['load-stage.py', 'registry.py'],
                 'audio-c3': ['load-stage.py', 'registry.py'],
                 'audio-c4': ['load-stage.py', 'registry.py'],
                 'audio-d1': ['load-stage.py', 'registry.py', 'gprclient.py', 'audiod1.py'],
                 'audio-d3': ['load-stage.py', 'registry.py', 'alsapcm.py', 'audiod3.py'],
                 'frpc': ['load-stage.py', 'registry.py'],
                 'sensors': ['run-sensors.py', 'hexrpcd.py', 'fastrpc.py', 'sensorsfs.py', 'qrtrns.py', 'pb.py', 'sscclient.py', 'registry.py'],
                 'audio-d5': ['load-stage.py', 'registry.py', 'alsapcm.py', 'gprclient.py', 'audioshm.py', 'argraph.py', 'audiod4.py', 'wavtool.py', 'audiod5.py'],
                 'audio-d4': ['load-stage.py', 'registry.py', 'alsapcm.py', 'gprclient.py', 'audioshm.py', 'argraph.py', 'audiod4.py'],
                 'audio-d2': ['load-stage.py', 'registry.py', 'gprclient.py', 'audioshm.py', 'audiod2.py'],
                 'susp-freezer': ['run-suspend.py', 'registry.py'],
                 'adc': ['load-stage.py', 'registry.py'],
                 'susp-devices': ['run-suspend-dev.py', 'registry.py'],
                 'adsp': ['run-adsp.py', 'pdmapper.py', 'qrtrns.py', 'registry.py'],
                 'btkeeper': ['run-btkeeper.py', 'bt-keeper.py', 'btprobe.py', 'registry.py'],
                 'ownerfinish': ['finish-owner.py', 'ownerlog.py', 'registry.py', 'borrow_owner.py', 'drmkms.py', 'drmabi.py']}

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok, msg):
    if not ok: sys.exit('STOP: ' + msg)
def rd(p): return Path(p).read_text()
def file_buildid(p):
    d = Path(p).read_bytes(); shoff = struct.unpack_from('<Q', d, 40)[0]; shes, shn, shstr = struct.unpack_from('<HHH', d, 58)
    sh = [struct.unpack_from('<IIQQQQIIQQ', d, shoff + i * shes) for i in range(shn)]
    names = d[sh[shstr][4]:sh[shstr][4] + sh[shstr][5]]
    for s in sh:
        if names[s[0]:names.index(b'\0', s[0])] == b'.note.gnu.build-id': return d[s[4]:s[4] + s[5]].hex()[-40:]
    need(False, 'no build-id: ' + str(p))

args = sys.argv[1:]
need(args and args[0] in ORDER and (len(args) == 1 or (len(args) == 3 and args[1] == '--out')), 'usage: make-boot-bundle.py pstore|display|owner|ownerfinish|gpu|keeper|touch|wifi-a|wifi-b|bt-a|bt-probe|bt-b|btkeeper|blescan|qrtr-smd|adsp|audio-c1|audio-c|audio-c2|audio-c3|audio-c4|audio-d1|audio-d2|audio-d3|audio-d4|audio-d5|frpc|sensors|susp-freezer|susp-devices|adc [--out DIR]')
stage = args[0]
CODE = CODE_BY_STAGE[stage] + COMMON
tpl = json.loads(rd(HERE/'review-templates.json'))
live = sorted(x.split()[0] for x in rd('/proc/modules').splitlines())
root = [x.split() for x in rd('/proc/self/mountinfo').splitlines() if x.split()[4] == '/']
need(len(root) == 1, 'ambiguous root')
slot = re.findall(r'^androidboot\.slot_suffix\s*=\s*"([^"]+)"\s*$', rd('/proc/bootconfig'), re.M)
need(slot == ['_a'], 'slot A required')
m = {'stage': stage, 'boot_id': rd('/proc/sys/kernel/random/boot_id').strip(), 'kernel': os.uname().release, 'root': root[0][2],
     'slot': '_a', 'module_names': live,
     'module_notes': {n: (Path('/sys/module')/n/'notes/.note.gnu.build-id').read_bytes().hex()[-40:] for n in live},
     'load_order': ORDER[stage], 'requires_live': REQUIRES[stage], 'bind_after': BIND_AFTER[stage], 'bind_now': BIND_NOW[stage],
     'templates_sha256': sha(HERE/'review-templates.json'), 'ramoops_required': stage != 'pstore'}
need(m['root'] == rd('/sys/class/block/mmcblk1p3/dev').strip(), 'root is not mmcblk1p3')

def _mgmt_net():
    for n in ('enx<MAC>', 'wlan0'):
        try:
            if (Path('/sys/class/net')/n/'carrier').read_text().strip() == '1': return n
        except OSError: pass
    return None                                        # no management link at all (boot with a pad instead of LAN): check skipped
MGMT_NET = _mgmt_net()                             # defined before the first stage block that uses it (wifi-a/b)
if stage == 'keeper':
    m['gpu'] = {'firmware': FIRMWARE}
if stage in ('wifi-a', 'wifi-b'):
    fw = {}
    for _l in Path('/home/siwal/y700-design/wifi-20260919/firmware/SHA256SUMS').read_text().split('\n'):
        if _l.strip(): _h, _f = _l.split(); fw[_f.replace('firmware/', '')] = _h
    m['wifi'] = {'firmware': fw, 'net': MGMT_NET, 'pcie': '1c00000.pcie', 'cnss': 'b0000000.qcom,cnss-peach',
                 'wcal': '18900000.rsc:drv@2:rpmh-regulator-vrm-wcal',
                 # 16c0000 = qcom,canoe-pcie_anoc: PCIe-only NoC; its sync_state after the pcie+cnss bind is expected and allowed only
                 # while its consumer set is exactly {pcie_qtb (bound TBU), pcie, cnss} (checked by load-stage)
                 'pcie_only_suppliers': {'16c0000.interconnect': ['16cd000.pcie_qtb', '1c00000.pcie', 'b0000000.qcom,cnss-peach']},
                 'guard_suppliers': ['31100000.interconnect', 'soc:interconnect@1'] +
                     ['18900000.rsc:drv@2:rpmh-regulator-' + r for r in ('s7f-e0', 's8f-e0', 'l2g-e0', 'l3g-e0', 's1j-e1', 's2j-e1', 'l3k-e1')]}
    if stage == 'wifi-b':
        # cold-boot calibration runs once per boot after fs_ready=1; if this boot already finished it, do not trigger it again
        import subprocess as _sp
        _cal = [l for l in _sp.run(['dmesg'], capture_output=True, text=True, check=True).stdout.splitlines() if 'cnss: Calibration took' in l]
        m['wifi']['calibration_done'] = _cal[-1] if _cal else None
# Management link watched by the adc/wifi/bt/qrtr-smd/adsp/audio stages: the USB Ethernet adapter when it has carrier, else Wi-Fi
# (2026-09-20: the side USB-C port is used by a gamepad; one USB controller serves both ports, so LAN and pad exclude each other)
BTFW = Path('/home/siwal/y700-design/bt-20260919/firmware')
AUDFW = Path('/home/siwal/y700-design/audio-20260919/firmware')
if stage == 'adsp':
    _sums = dict(reversed(l.split()) for l in (AUDFW/'SHA256SUMS').read_text().splitlines() if l.strip())
    m['adsp'] = {'net': MGMT_NET, 'rproc': 'remoteproc1', 'rproc_name': '3000000.remoteproc-adsp',
                 'firmware': {f: h for f, h in sorted(_sums.items()) if not f.endswith('.jsn')},
                 # all four PD JSON files from modem_a (Android pd-mapper reads them all): root, audio, sensor, ois
                 'jsn': ['adspr.jsn', 'adspua.jsn', 'adsps.jsn', 'adspuo.jsn'], 'jsn_sha256': {f: _sums[f] for f in ('adspr.jsn', 'adspua.jsn', 'adsps.jsn', 'adspuo.jsn')}}
    # adsp.mdt + 53 segment files (all 51 loadable segments of the 55 program headers present, checked 3a1d6dac) + adsp_dtb.mdt/b00-b02
    need(len(m['adsp']['firmware']) == 58, 'expected 58 ADSP firmware files, got %d' % len(m['adsp']['firmware']))
if stage == 'sensors':
    sys.path.insert(0, str(HERE)); import sensorsfs
    _rm = '/home/siwal/y700-design/sensors-20260919/rootmap.json'
    m['sensors'] = {'net': 'enx<MAC>', 'tree': list(sensorsfs.tree_digest(_rm)), 'rootmap_sha256': sha(Path(_rm))}
    _regs = sorted(AGENT.glob('registry-sns-%s*.json' % m['boot_id'][:8]), key=lambda x: x.stat().st_mtime)
    if _regs:                                              # replace the newest running hexrpcd of this boot
        _r = json.loads(rd(_regs[-1])); m['sensors']['previous'] = {'registry': _regs[-1].name, 'registry_sha256': sha(_regs[-1]), 'hexrpcd': _r['hexrpcd']}
        # no restart_pd: SERVREG_RESTART_PD_REQ was refused (err 69) on 3a1d6dac; replacing the listener alone made the PD retry
        # its registry pass there (run 2: 300 requests)
if stage == 'susp-freezer':
    m['suspend'] = {'net': 'enx<MAC>', 'pm_test': 'freezer', 'state': 'freeze',
                    'stats_before': {f.name: rd(f).strip() for f in sorted(Path('/sys/power/suspend_stats').iterdir())}}
if stage == 'susp-devices':
    _b = [l for l in Path('/sys/class/net').iterdir() if l.name == 'enx<MAC>']
    if _b:   # S-2a (LAN plugged, session over LAN)
        m['suspend'] = {'net': 'enx<MAC>', 'pm_test': 'devices', 'state': 'freeze', 'previous': 'boot-susp-freezer-dc50b8a1f0994e8d',
                        'previous_expect': {'status': 'FREEZER_TEST_DONE'}}
    else:    # S-2b: LAN unplugged so xhci's root hub can suspend; session over Wi-Fi; after the reviewed S-2a STOP + rebase
        need((AGENT/('registry-rebase-%s-s2a.json' % m['boot_id'][:8])).exists(), 'S-2a rebase record missing')
        m['suspend'] = {'net': 'wlp1s0', 'must_be_absent': ['enx<MAC>'], 'pm_test': 'devices', 'state': 'freeze',
                        'previous': 'boot-susp-devices-b163f341c094b6d3', 'previous_expect': {'error': 'native DRM state not back within 10 s'}}
    m['suspend'].update({'stats_before': {f.name: rd(f).strip() for f in sorted(Path('/sys/power/suspend_stats').iterdir())}})
if stage == 'adc':
    # DT: 15 thermal zones use vadc@9000 (phandle 0x719); none has a critical trip (xo-therm: passive 78/80 C + hot 90 C)
    m['adc'] = {'net': MGMT_NET, 'vadc': 'c426000.spmi:pmk8850@0:vadc@9000',
                'zones': ['ap-therm', 'batt-pack-therm', 'batt2-pack-therm', 'fast-chg-therm', 'fcam-ntc', 'flash-led-ntc', 'lcm-thermal',
                          'quiet-therm', 'rear-cam-ntc', 'top-chg-therm', 'ufs-therm', 'usb1-conn-therm', 'usb2-conn-therm', 'wlan-therm', 'xo-therm'],
                'temp_range_mC': [0, 70000], 'charger_msg': 'Failed to get usb1-conn-therm'}
if stage == 'frpc':
    m['frpc'] = {'net': 'enx<MAC>', 'rproc': 'remoteproc1', 'rpmsg': '3000000.remoteproc-adsp:glink-edge.fastrpcglink-apps-dsp.-1.-1'}
if stage == 'audio-d5':
    m['audio'] = {'net': 'enx<MAC>', 'rproc': 'remoteproc1', 'wav': {'melody.wav': sha(Path('/home/siwal/y700-design/audio-20260919/melody.wav'))}}
    need(m['audio']['wav']['melody.wav'] == 'fb1979a37ff35a5a139ce90f6a9056810c8413674f7589638295e0fe7c608e4b', 'melody.wav changed')
if stage == 'audio-d4':
    m['audio'] = {'net': 'enx<MAC>', 'rproc': 'remoteproc1'}
if stage == 'audio-d3':
    m['audio'] = {'net': 'enx<MAC>', 'rproc': 'remoteproc1'}
if stage == 'audio-d2':
    m['audio'] = {'net': 'enx<MAC>', 'rproc': 'remoteproc1'}
if stage == 'audio-d1':
    m['audio'] = {'net': 'enx<MAC>', 'rproc': 'remoteproc1'}
if stage == 'audio-c4':
    m['audio'] = {'net': 'enx<MAC>', 'rproc': 'remoteproc1', 'amps': ['4-0034', '4-0037'], 'sound': 'soc:spf_core_platform:sound',
                  'btswr': 'soc:spf_core_platform:lpass_bt_swr@6CA0000', 'extdisp_codec': 'soc:qcom,msm-ext-disp:qcom,msm-ext-disp-audio-codec-rx'}
if stage == 'audio-c3':
    m['audio'] = {'net': 'enx<MAC>', 'rproc': 'remoteproc1', 'amps': ['4-0034', '4-0037'], 'lpi': 'soc:spf_core_platform:lpi_pinctrl@07760000',
                  'extdisp_codec': 'soc:qcom,msm-ext-disp:qcom,msm-ext-disp-audio-codec-rx', 'sound': 'soc:spf_core_platform:sound'}
if stage in ('audio-c2', 'audio-c'):
    # aw882xx_acf.bin from vendor-probe.img /firmware (debugfs dump, read-only)
    m['audio'] = {'net': MGMT_NET, 'rproc': 'remoteproc1',
                  'firmware_install': {'aw882xx_acf.bin': sha(Path('/home/siwal/y700-design/audio-20260919/aw882xx_acf.bin'))},
                  'amps': ['4-0034', '4-0037'], 'lpi': 'soc:spf_core_platform:lpi_pinctrl@07760000'}
    need(m['audio']['firmware_install']['aw882xx_acf.bin'] == 'd3133216d789643acd0be4ec7873e792d8f5f1b0ec162256927835db68f9f7f3', 'aw882xx_acf.bin changed')
if stage == 'audio-c1':
    m['audio'] = {'net': MGMT_NET, 'rproc': 'remoteproc1', 'gpr_rpmsg': '3000000.remoteproc-adsp:glink-edge.adsp_apps.-1.-1'}
if stage == 'qrtr-smd':
    m['qrtr'] = {'net': MGMT_NET, 'soccp_rpmsg': 'a3380000.remoteproc-soccp:glink-edge.IPCRTR.-1.-1', 'soccp_rproc': 'remoteproc0'}
if stage == 'blescan':
    m['bt'] = {'net': 'enx<MAC>', 'hci': 'hci0', 'secs': 10}
if stage == 'bt-b':
    m['bt'] = {'net': MGMT_NET, 'uart': '1994000.qcom,qup_uart', 'power': 'soc:wcn786x'}
if stage == 'btkeeper':
    _sums = dict(reversed(l.split()) for l in (BTFW/'SHA256SUMS').read_text().splitlines() if l.strip())
    m['bt'] = {'net': MGMT_NET, 'uart': '1994000.qcom,qup_uart', 'power': 'soc:wcn786x', 'wlan': 'wlp1s0',
               'firmware': {f: _sums[f] for f in ('brhbtfw20.tlv', 'brhbtnv20.bin')},
               # values read by bt-probe (70037e22) on this chip
               'expect': {'product_id': 0x21, 'rom_ver': 0x0200, 'soc_id': 0x40210200}, 'retained_keepers': []}
    # a failed keeper of this boot powered the core off and holds /dev/btpower forever (never killed); it is the only allowed holder
    for _d in sorted(AGENT.glob('boot-btkeeper-*')):
        _m = json.loads(rd(_d/'manifest.json'))
        if _m['boot_id'] != m['boot_id'] or not (_d/'attempt.json').exists(): continue
        _r = json.loads(rd(_d/'result.json')); _log = rd(_d/'keeper.log').splitlines()
        need('bt-keeper stopped' in _r.get('error', '') and 'POWER_OFF ret=0' in _log and _log[-1].startswith('HELD pid=%d ' % _r['keeper']['pid']),
             'previous btkeeper %s did not stop with a confirmed power off' % _d.name)
        m['bt']['retained_keepers'].append({'bundle': _d.name, **_r['keeper'], 'keeper_log_sha256': sha(_d/'keeper.log')})
if stage == 'bt-probe':
    m['bt'] = {'net': 'enx<MAC>', 'uart': '1994000.qcom,qup_uart', 'power': 'soc:wcn786x'}
if stage == 'bt-a':
    m['bt'] = {'uart': '1994000.qcom,qup_uart', 'power': 'soc:wcn786x', 'net': MGMT_NET,
               # clk_virt (QUP core paths): the BT UART is its LAST unbound consumer -> its sync_state runs on this bind (the normal
               # Android end state). Allowed only while every other consumer is exactly this reviewed set and all of them are bound.
               'clk_virt': 'soc:interconnect@0',
               'clk_virt_others': ['1a80000.spi', '890000.i2c', 'a8c000.i2c', 'a90000.i2c', 'a94000.i2c', 'a98000.i2c', 'a9c000.qcom,qup_uart'],
               # rpmh regulators feeding wcn786x stay pending through regulator-ocp-notifer (unbound): no regulator sync_state
               'regulators_pending_via': 'soc:regulator-ocp-notifer',
               'regulators': ['18900000.rsc:drv@2:rpmh-regulator-' + r for r in ('l2g-e0', 'l3g-e0', 'l6k-e1', 's1j-e1', 's2j-e1', 's7f-e0', 's8f-e0')]}
if stage == 'touch':
    m['touch'] = {'firmware': {'novatek_ts_fw.bin': 'a374ce0856f9e61bd62de955e93d2585963a0636ff7390de2248386e896c114f',
                               'novatek_ts_mp.bin': '1b83ad26961e9e445b7f7bba172c0211928d64f82eea698e1685e8273b09ec30'},
                  'spi_controller': '1a80000.spi', 'must_keep_pending': ['100000.clock-controller', '31100000.interconnect', 'soc:interconnect@1']}
if stage == 'gpu':
    m['gpu'] = {'firmware': FIRMWARE,
                'expected_before': {'kgsl': None, 'gmu': None, 'iommu': None, 'gpucc_state_synced': '0'},
                'expected_after': {'kgsl': 'kgsl-3d', 'gmu': 'adreno-gen8-gmu', 'iommu': 'kgsl-iommu', 'gpucc_state_synced': '1'},
                'must_keep_pending': ['100000.clock-controller', '31100000.interconnect', 'soc:interconnect@1']}
files, load_notes = {}, {}
SRC = {}
for f in ORDER[stage]:
    if f in EXTRA:
        src = Path(EXTRA[f]['path']); need(sha(src) == EXTRA[f]['sha256'], 'reviewed module source changed: ' + f)
        files[f] = EXTRA[f]['sha256']; load_notes[f[:-3].replace('-', '_')] = file_buildid(src); SRC[f] = src; continue
    rec = MODS[f]; src = AGENT/rec['bundle']/f
    need(sha(src) == rec['sha256'] and file_buildid(src) == rec['file_build_id'] == rec['live_build_id'], 'reviewed module source changed: ' + f)
    files[f] = rec['sha256']; load_notes[f[:-3].replace('-', '_')] = rec['file_build_id']; SRC[f] = src
for f in CODE: files[f] = sha(HERE/f)
if stage == 'btkeeper': files.update(m['bt']['firmware'])
if stage == 'adsp': files.update(m['adsp']['firmware']); files.update(m['adsp']['jsn_sha256'])
if stage in ('audio-c2', 'audio-c'): files.update(m['audio']['firmware_install'])
if stage == 'audio-d5': files.update(m['audio']['wav'])
if stage == 'sensors': files['rootmap.json'] = m['sensors']['rootmap_sha256']
m['files'] = files; m['load_notes'] = load_notes
import reviewed_lines
m['reviewed_extra_lines'] = reviewed_lines.collect_extra(AGENT, m['boot_id'])
m['reviewed_lines'] = reviewed_lines.collect(AGENT, m['boot_id']) if stage in ('owner', 'gpu', 'keeper', 'ownerfinish', 'touch', 'wifi-a', 'wifi-b', 'bt-a', 'bt-probe', 'bt-b', 'btkeeper', 'blescan', 'qrtr-smd', 'adsp', 'audio-c1', 'audio-c', 'audio-c2', 'audio-c3', 'audio-c4', 'audio-d1', 'audio-d2', 'audio-d3', 'audio-d4', 'audio-d5', 'frpc', 'sensors', 'susp-freezer', 'susp-devices', 'adc') else []
if stage in ('owner', 'gpu', 'keeper', 'ownerfinish', 'touch', 'wifi-a', 'wifi-b', 'bt-a', 'bt-probe', 'bt-b', 'btkeeper', 'blescan', 'qrtr-smd', 'adsp', 'audio-c1', 'audio-c', 'audio-c2', 'audio-c3', 'audio-c4', 'audio-d1', 'audio-d2', 'audio-d3', 'audio-d4', 'audio-d5', 'frpc', 'sensors', 'susp-freezer', 'susp-devices', 'adc'): need(len(m['reviewed_lines']) == 2, 'expected the two panel lines reviewed by this boot\'s display stage')
if stage == 'ownerfinish':
    obs = [d for d in AGENT.glob('boot-owner-*') if json.loads(rd(d/'manifest.json'))['boot_id'] == m['boot_id'] and (d/'attempt.json').exists()]
    need(len(obs) == 1, 'expected exactly one attempted owner bundle for this boot')
    r = json.loads(rd(obs[0]/'result.json'))
    need('expected exactly one encoder status' in r.get('error', ''), 'owner bundle did not stop at the encoder-selection step')
    m['owner_bundle'] = obs[0].name
    m['owner_files'] = {f: sha(obs[0]/f) for f in ('owner.log', 'owner.json', 'result.json', 'display-owner.py')}
text = json.dumps(m, indent=2).encode()
out = Path(args[2]) if len(args) == 3 else AGENT/('boot-%s-%s' % (stage, hashlib.sha256(text).hexdigest()[:16]))
need(not out.exists(), 'bundle exists: ' + str(out))
out.mkdir(parents=True)
for f in ORDER[stage]: shutil.copy2(SRC[f], out/f)
for f in CODE: shutil.copy2(HERE/f, out/f)
if stage == 'sensors':
    shutil.copy2('/home/siwal/y700-design/sensors-20260919/rootmap.json', out/'rootmap.json')
if stage == 'audio-d5':
    shutil.copy2('/home/siwal/y700-design/audio-20260919/melody.wav', out/'melody.wav')
if stage in ('audio-c2', 'audio-c'):
    shutil.copy2('/home/siwal/y700-design/audio-20260919/aw882xx_acf.bin', out/'aw882xx_acf.bin')
if stage == 'adsp':
    for f, h in list(m['adsp']['firmware'].items()) + list(m['adsp']['jsn_sha256'].items()):
        need(sha(AUDFW/f) == h, 'ADSP firmware changed: ' + f); shutil.copy2(AUDFW/f, out/f)
if stage == 'btkeeper':
    for f, h in m['bt']['firmware'].items():
        need(sha(BTFW/f) == h, 'BT firmware changed: ' + f); shutil.copy2(BTFW/f, out/f)
(out/'manifest.json').write_bytes(text)
for p in out.iterdir(): p.chmod(0o644)
print(out, sha(out/'manifest.json'))
