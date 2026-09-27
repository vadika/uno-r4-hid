#include "boot_hid.h"
#include <FspTimer.h>
#include <tusb.h>
#include "serial_control.h"

namespace {
const uint8_t keyboardDescriptor[] = { TUD_HID_REPORT_DESC_KEYBOARD() };
// Three buttons, X/Y and wheel. No report IDs on either interface.
const uint8_t mouseDescriptor[] = {
  0x05,0x01, 0x09,0x02, 0xa1,0x01, 0x09,0x01, 0xa1,0x00,
  0x05,0x09, 0x19,0x01, 0x29,0x03, 0x15,0x00, 0x25,0x01,
  0x95,0x03, 0x75,0x01, 0x81,0x02,
  0x95,0x01, 0x75,0x05, 0x81,0x01,
  0x05,0x01, 0x09,0x30, 0x09,0x31, 0x09,0x38,
  0x15,0x81, 0x25,0x7f, 0x75,0x08, 0x95,0x03, 0x81,0x06,
  0xc0,0xc0
};
const uint8_t configuration[] = {
  TUD_CONFIG_DESCRIPTOR(1, 4, 0, TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN + 2*TUD_HID_DESC_LEN, 0, 500),
  TUD_CDC_DESCRIPTOR(0, 4, 0x81, 8, 0x02, 0x82, 64),
  TUD_HID_DESCRIPTOR(2, 0, HID_ITF_PROTOCOL_KEYBOARD, sizeof(keyboardDescriptor), 0x83, 8, 1),
  TUD_HID_DESCRIPTOR(3, 0, HID_ITF_PROTOCOL_MOUSE, sizeof(mouseDescriptor), 0x84, 8, 1)
};
struct State {
  uint8_t report[8]{};
  uint8_t idle = 0;
  unsigned long lastSent = 0;
  bool protocolChanged = false;
};
State states[2];
uint8_t keyboardLeds = 0;
FspTimer idleTimer;

uint8_t reportSize(uint8_t instance) {
  return instance == KEYBOARD_INSTANCE ? 8 :
    tud_hid_n_get_protocol(instance) == HID_PROTOCOL_BOOT ? 3 : 4;
}

bool submit(uint8_t instance, const void *data) {
  if (!tud_hid_n_ready(instance) || !tud_hid_n_report(instance, 0, data, reportSize(instance)))
    return false;
  states[instance].lastSent = millis();
  states[instance].protocolChanged = false;
  return true;
}

void idleTick(timer_callback_args_t *) {
  // Wi-Fi calls can block longer than a HID idle period. Service USB independently.
  UsbGuard guard;
  if (!tud_mounted()) return;
  for (uint8_t instance = 0; instance < 2; ++instance) {
    auto &state = states[instance];
    if (state.protocolChanged || (state.idle && millis()-state.lastSent >= state.idle*4u))
      submit(instance, state.report);
  }
}
}

bool beginBootHid() {
  uint8_t type;
  int8_t channel = FspTimer::get_available_timer(type);
  return channel >= 0 &&
    idleTimer.begin(TIMER_MODE_PERIODIC, type, channel, 1000.0f, 50.0f, idleTick) &&
    idleTimer.setup_overflow_irq() && idleTimer.open() && idleTimer.start();
}

bool sendBootHid(uint8_t instance, const void *data) {
  unsigned long start = millis();
  while (tud_mounted() && !tud_hid_n_ready(instance) && millis()-start < 50) delay(1);
  UsbGuard guard;
  if (!tud_mounted() || !submit(instance, data)) return false;
  auto &state = states[instance];
  if (instance == KEYBOARD_INSTANCE) memcpy(state.report, data, 8);
  else {
    // Relative motion must never be replayed by GET_REPORT or idle retransmission.
    memset(state.report, 0, sizeof(state.report));
    state.report[0] = *static_cast<const uint8_t *>(data) & 7;
  }
  return true;
}

extern "C" const uint8_t *tud_descriptor_configuration_cb(uint8_t index) {
  return index == 0 ? configuration : nullptr;
}

extern "C" const uint8_t *tud_hid_descriptor_report_cb(uint8_t instance) {
  return instance == KEYBOARD_INSTANCE ? keyboardDescriptor :
    instance == MOUSE_INSTANCE ? mouseDescriptor : nullptr;
}

extern "C" uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t id,
    hid_report_type_t type, uint8_t *buffer, uint16_t requested) {
  if (instance >= 2 || id) return 0;
  if (type == HID_REPORT_TYPE_INPUT) {
    uint16_t size = min(requested, uint16_t(reportSize(instance)));
    memcpy(buffer, states[instance].report, size);
    return size;
  }
  if (instance == KEYBOARD_INSTANCE && type == HID_REPORT_TYPE_OUTPUT && requested) {
    buffer[0] = keyboardLeds;
    return 1;
  }
  return 0;
}

extern "C" void tud_hid_set_report_cb(uint8_t instance, uint8_t id,
    hid_report_type_t type, const uint8_t *buffer, uint16_t size) {
  if (instance == KEYBOARD_INSTANCE && !id && type == HID_REPORT_TYPE_OUTPUT && size == 1)
    keyboardLeds = buffer[0] & 0x1f;
}

extern "C" void tud_hid_set_protocol_cb(uint8_t instance, uint8_t) {
  if (instance < 2) states[instance].protocolChanged = true;
}

extern "C" bool tud_hid_set_idle_cb(uint8_t instance, uint8_t rate) {
  if (instance >= 2) return false;
  states[instance].idle = rate;
  return true;
}

extern "C" void tud_mount_cb() {
  for (auto &state : states) state = State{};
  keyboardLeds = 0;
}
