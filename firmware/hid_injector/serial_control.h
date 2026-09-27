#pragma once

// The core runs TinyUSB from its USB ISR. Guard whole API calls, including
// endpoint claims and FIFO updates; the driver's IRQ toggle is not nestable.
class UsbGuard {
  uint32_t saved = __get_PRIMASK();
public:
  UsbGuard() { __disable_irq(); }
  ~UsbGuard() { __set_PRIMASK(saved); }
};

// One bounded reply at a time: apply backpressure without blocking safety checks.
class SerialControl : public Stream {
  uint8_t reply[256];
  size_t used = 0;
public:
  int available() override { UsbGuard guard; return SerialUSB.available(); }
  int read() override { UsbGuard guard; return SerialUSB.read(); }
  int peek() override { UsbGuard guard; return SerialUSB.peek(); }
  void flush() override {
    UsbGuard guard;
    if (!tud_cdc_connected()) return;
    size_t length = min(used, size_t(tud_cdc_write_available()));
    size_t sent = length ? tud_cdc_write(reply, length) : 0;
    used -= sent;
    memmove(reply, reply + sent, used);
    tud_cdc_write_flush();
  }
  bool pendingReply() const { return used != 0; }
  void discardInput() {
    UsbGuard guard;
    used = 0;
    tud_cdc_read_flush();
    tud_cdc_write_clear();
  }
  size_t write(uint8_t c) override { return write(&c, 1); }
  size_t write(const uint8_t *data, size_t length) override {
    length = min(length, sizeof(reply) - used);
    memcpy(reply + used, data, length);
    used += length;
    return length;
  }
};
