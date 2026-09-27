"""Apply USB fixes and descriptor overrides to the pinned Arduino core."""
import os
from pathlib import Path

core = Path(os.environ['ARDUINO_DIRECTORIES_DATA']) / 'packages/arduino/hardware/renesas_uno/1.6.0'
driver = core / 'cores/arduino/tinyusb/rusb2/dcd_rusb2.c'
original = 'cfg |= (RUSB2_PIPECFG_TYPE_BULK | RUSB2_PIPECFG_SHTNAK_Msk | RUSB2_PIPECFG_DBLB_Msk);'
replacement = '''// A second queued bank makes wait_pipe_fifo_empty block the USB ISR
    // when the host stops reading. Single buffering preserves backpressure.
    cfg |= (RUSB2_PIPECFG_TYPE_BULK | RUSB2_PIPECFG_SHTNAK_Msk);'''

def patch(path, original, replacement):
    source = path.read_text()
    if replacement not in source:
        if source.count(original) != 1:
            raise SystemExit(f'Unexpected source in {path}; refusing to patch')
        path.write_text(source.replace(original, replacement))


patch(driver, original, replacement)
patch(core / 'variants/UNOWIFIR4/tusb_config.h',
      '#define CFG_TUD_HID              1', '#define CFG_TUD_HID              2')
usb = core / 'cores/arduino/usb/USB.cpp'
for signature in ('uint8_t const * tud_hid_descriptor_report_cb(uint8_t instance)',
                  'const uint8_t *tud_descriptor_configuration_cb(uint8_t index)'):
    patch(usb, '\n' + signature + ' {', '\n__attribute__((weak)) ' + signature + ' {')
patch(core / 'cores/arduino/tinyusb/class/hid/hid_device.c',
      'case HID_REQ_CONTROL_SET_PROTOCOL:\n        if',
      'case HID_REQ_CONTROL_SET_PROTOCOL:\n'
      '        TU_VERIFY(request->wValue <= HID_PROTOCOL_REPORT && request->wLength == 0);\n'
      '        if')
