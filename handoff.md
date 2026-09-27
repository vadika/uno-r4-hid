
## Project handoff — UNO R4 WiFi dual-USB HID injector

The current implementation uses the intact board: onboard USB-C provides RA4M1
keyboard/mouse HID and native USB serial configuration; onboard Wi-Fi provides
remote control. The dual-USB wiring below is an optional alternative design.
The current serial command path uses `SerialUSB`, not the ESP32 UART bridge.
USB VID:PID stays `2341:006d`. Keyboard and mouse use separate boot-capable HID
interfaces (`03/01/01` and `03/01/02`) with host-selected boot/report protocols;
CDC serial remains available. See `TOOL.md` for the current interface details.

### Objective

Build a two-USB test automation device around an **Arduino UNO R4 WiFi**, with **no external USB-UART adapter**.

```text
Automation host                         Device under test
      │                                      │
      │ USB                                  │ USB
      ▼                                      ▼
┌──────────────┐                     ┌───────────────┐
│ USB CDC/ACM  │                     │ HID keyboard  │
│ control      │                     │ + mouse       │
└──────┬───────┘                     └──────▲────────┘
       │                                    │
       ▼                                    │
┌────────────────────────────────────────────────────┐
│                  UNO R4 WiFi                       │
│                                                    │
│  ESP32-S3 ───────── UART ─────────── RA4M1        │
│  USB device                         USB HID        │
│                                                    │
└────────────────────────────────────────────────────┘
```

The UNO already contains essentially the USB-to-serial hardware we need. Arduino's standard ESP32-S3 firmware implements a USB CDC bridge to the RA4M1; on the R4 WiFi, the Arduino `Serial` interface corresponds to this ESP32-S3 ↔ RA4M1 path. ([Arduino Documentation][1])

---

### Hardware architecture

The standard UNO R4 WiFi has one USB-C connector whose D+/D− lines are switched by **U2 and U6** between:

```text
ESP_P / ESP_N     → ESP32-S3
RA4_P / RA4_N     → RA4M1
```

Arduino provides solder jumper **SJ1 / RA4M1 USB**. Closing SJ1 permanently selects the RA4M1 for the onboard USB-C connector. ([Arduino Documentation][2])

Therefore:

```text
Existing UNO USB-C
        │
        │ SJ1 closed
        ▼
      RA4M1
        │
        └──── USB HID → DUT
```

Add a second USB connector:

```text
New CONTROL USB-C
       │
       ├── D+ ───────── ESP_P
       ├── D- ───────── ESP_N
       └── GND ──────── GND

                       ESP32-S3
```

The schematic confirms:

```text
ESP_N = ESP32-S3 GPIO19 / USB D-
ESP_P = ESP32-S3 GPIO20 / USB D+
```



---

## Required modification

### 1. Close SJ1

Bridge the pads marked:

```text
RA4M1 USB
```

or `SJ1` in the schematic.

After this:

```text
onboard USB-C → RA4M1
```

regardless of software USB-switch state. Arduino explicitly documents SJ1 for permanently bypassing the ESP32-S3 USB path. ([Arduino Documentation][1])

This USB-C becomes:

```text
DUT USB
```

---

### 2. Add CONTROL USB-C

Connect a USB-C receptacle approximately as follows:

```text
CONTROL USB-C

A6/B6 D+ ───────────── ESP_P
A7/B7 D- ───────────── ESP_N

A1/B1/A12/B12 ──────── GND

CC1 ── 5.1 kΩ ── GND
CC2 ── 5.1 kΩ ── GND
```

Preferably add USB ESD protection similar to the original UNO design.

The original UNO USB-C uses 5.1 kΩ CC resistors and ESD protection, so its USB input circuitry provides a useful reference design.

### Prototype connection points

Do **not** solder directly onto ESP32 module pins if avoidable.

Use the ESP-side pads/traces around:

```text
U2 → ESP_P
U6 → ESP_N
```

as accessible connection points.

---

# Power

For the simplest prototype:

```text
DUT USB
   │
   └── powers UNO R4 WiFi

CONTROL USB
   │
   ├── D+
   ├── D-
   └── GND
```

Do not directly connect CONTROL USB VBUS to the UNO 5 V rail initially. That avoids connecting the 5 V supplies of two USB hosts together.

This means:

> **The DUT must be powered for the controller USB interface to operate.**

That's acceptable for v0.1.

For a later test-stand version, add proper power ORing/power muxing so either side can power the controller without backfeeding.

---

# Internal communication

No additional UART wiring is necessary.

The board already connects the ESP32-S3 and RA4M1 through its internal serial interface and level translator.

Arduino's ESP bridge firmware describes the relevant path as:

```text
ESP32 Serial1
    ↓
GPIO 20/21
    ↓
RA4M1 P109/P110
    ↓
RA4 Arduino Serial
```

The standard firmware exposes USB CDC on the ESP32 side and forwards it to that RA4 serial interface. ([GitHub][3])

Thus:

```text
Linux /dev/ttyACM0
        │
        │ USB CDC
        ▼
     ESP32-S3
        │
        │ onboard UART
        ▼
      RA4M1
        │
        │ Serial
        ▼
 command parser
```

This is an important distinction from `Serial1`:

```cpp
Serial
```

is the interface to use for controller commands on the UNO R4 WiFi.

`Serial1` is the UART exposed on Arduino pins D0/D1 and is not required for this design. The R4 WiFi's normal serial console is routed through SCI9 and the ESP32-S3 bridge. ([GitHub][4])

---

# RA4M1 firmware

High-level structure:

```cpp
#include <Keyboard.h>
#include <Mouse.h>

void setup()
{
    Serial.begin(115200);

    Keyboard.begin();
    Mouse.begin();

    releaseAll();
}

void loop()
{
    if (Serial.available()) {
        handleCommand();
    }

    checkHidWatchdog();
}
```

Do not use:

```cpp
Serial1
```

for the control path.

---

# Control protocol

Keep v0.1 text-based.

```text
PING

KEY ENTER
KEY ESC
KEY F1
KEY F12

DOWN CTRL
UP CTRL

COMBO CTRL C
COMBO CTRL ALT T

TEXT hello world

MOVE 20 -10

CLICK LEFT
CLICK RIGHT
CLICK MIDDLE

DOWN_MOUSE LEFT
UP_MOUSE LEFT

SCROLL -1

RELEASE_ALL
```

Responses:

```text
OK
OK PONG

ERR BAD_KEY
ERR BAD_ARGUMENT
ERR UNKNOWN_COMMAND
```

Serial settings:

```text
115200
8N1
```

---

# Keyboard implementation

Support symbolic HID keys:

```text
ENTER
ESC
TAB
BACKSPACE
DELETE
INSERT

UP
DOWN
LEFT
RIGHT

HOME
END
PAGEUP
PAGEDOWN

F1 ... F12

CTRL
SHIFT
ALT
GUI
```

And normal keys:

```text
A ... Z
0 ... 9
```

Commands have different semantics:

```text
KEY A
```

means press + release.

```text
DOWN CTRL
```

means hold.

```text
UP CTRL
```

means release.

```text
COMBO CTRL SHIFT ESC
```

should:

1. press CTRL
2. press SHIFT
3. press ESC
4. release ESC
5. release SHIFT
6. release CTRL

---

# Direct HID support

Eventually add a command for explicit USB HID usage codes:

```text
HID 0x04
```

This matters because:

```text
TEXT foo
```

depends on keyboard layout.

For example, Finnish, German and US keyboards do not map every character to the same physical keys.

Physical HID usages allow deterministic UI testing.

---

# Mouse

v0.1 uses relative movement only.

```text
MOVE dx dy
```

Example:

```text
MOVE 100 -25
```

Firmware should divide large movements into multiple reports if necessary.

Also implement:

```text
CLICK LEFT
CLICK RIGHT
CLICK MIDDLE

SCROLL -3
```

Absolute pointer coordinates are outside v0.1 scope.

---

# Safety

This is mandatory for test automation.

Track held state:

```text
held keyboard keys
held mouse buttons
last command timestamp
```

Implement:

```text
RELEASE_ALL
```

which releases every key and mouse button.

Also implement a watchdog:

```text
if anything is held
AND
no valid command for 5 seconds

→ RELEASE_ALL
```

Otherwise:

```text
DOWN CTRL
```

followed by the test process crashing can leave the DUT with Ctrl permanently held.

---

# Host utility

Implement:

```text
hidctl
```

Examples:

```bash
nix develop
make setup build
./hidctl --device /dev/ttyACM0 configure

./hidctl --host 192.0.2.10 ping
./hidctl --host 192.0.2.10 key ENTER
./hidctl --host 192.0.2.10 key F2

./hidctl --host 192.0.2.10 combo CTRL ALT T

./hidctl --host 192.0.2.10 text "hello world"

./hidctl --host 192.0.2.10 move 100 -30
./hidctl --host 192.0.2.10 click left

./hidctl --host 192.0.2.10 release-all
```

Use the detected serial port and the IP printed by `configure`; `192.0.2.10` is a placeholder. Configuration is
stored on the board; the generated control token is saved in `.arduino/wifi-token`.
Use `session` or the Python context manager for held inputs: closing a connection
releases its keys/buttons. `TEXT` assumes a US keyboard layout.

Python API:

```python
from hidctl import HidController

with HidController("/dev/ttyACM0") as hid:
    hid.combo("CTRL", "ALT", "T")
    hid.text("uname -a")
    hid.key("ENTER")
```

---

# ESP32 firmware

**Do not replace the ESP32 firmware for v0.1.**

Arduino's stock UNO R4 WiFi bridge firmware already provides:

```text
USB CDC
   ↕
ESP32-S3
   ↕ UART
RA4M1 Serial
```

and its implementation is open source. ([GitHub][5])

Reprogramming the ESP32-S3 would overwrite its standard connectivity/bridge firmware and is unnecessary unless the stock bridge proves unsuitable. Arduino explicitly warns that directly programming the ESP32 replaces this firmware. ([Arduino Documentation][1])

---

# Important programming issue

Once SJ1 permanently routes the original USB connector to the RA4M1, the normal UNO upload path changes.

Therefore **do not solder SJ1 first**.

Recommended development sequence:

```text
1. Receive UNO R4 WiFi.

2. Develop basic firmware normally.

3. Verify:
       Serial input
       Keyboard HID
       Mouse HID

4. Add second CONTROL USB connector to ESP_P/ESP_N.

5. Test that the second connector enumerates
   as ESP32 USB CDC.

6. Verify:
       CONTROL USB
          ↓
       ESP32 bridge
          ↓
       RA4 Serial

7. Only then bridge SJ1.

8. Connect onboard USB-C to DUT.

9. Verify simultaneous:
       CONTROL USB → Serial
       DUT USB     → HID
```

This order minimizes the chance of making firmware loading unnecessarily difficult during bring-up.

---

# First hardware acceptance test

Before implementing the full parser:

### Test A

Host:

```bash
echo TEST > /dev/ttyACM0
```

RA4 sketch:

```cpp
if (Serial.available()) {
    Keyboard.write('A');
}
```

Expected result:

```text
Host sends serial byte
        ↓
ESP32-S3 receives USB CDC
        ↓
RA4 receives Serial
        ↓
RA4 generates USB HID
        ↓
DUT receives "A"
```

Once this works, the architecture is proven.

---

# Target device

Final topology:

```text
         CONTROL USB-C                     DUT USB-C
              │                                │
              ▼                                ▼
      ┌─────────────────────────────────────────────┐
      │              UNO R4 WiFi                    │
      │                                             │
      │ ESP32-S3                 RA4M1             │
      │ USB CDC ─── UART ────── Serial            │
      │                           │                 │
      │                           ├── Keyboard HID  │
      │                           └── Mouse HID     │
      │                                             │
      └─────────────────────────────────────────────┘
```

### External components

For v0.1:

* Arduino UNO R4 WiFi
* second USB-C receptacle/breakout
* 2 × 5.1 kΩ CC resistors
* D+/D−/GND wiring
* preferably USB ESD protection
* USB cables

**No CH340, CP2102, FT232 or other USB-UART adapter.**

### First engineering milestone

Do not start with the complete HID command implementation.

The first milestone is only:

```text
second USB-C
    ↓
ESP32-S3 USB CDC
    ↓
onboard bridge
    ↓
RA4M1 Serial
    ↓
one HID key
    ↓
DUT
```

If that chain works, the remainder is straightforward firmware rather than hardware risk.

[1]: https://docs.arduino.cc/tutorials/uno-r4-wifi/cheat-sheet/?queryID=04184345578a106363f564b0c0b029cc&utm_source=chatgpt.com "Arduino UNO R4 WiFi User Manual | Arduino Documentation"
[2]: https://docs.arduino.cc/resources/datasheets/ABX00087-datasheet.pdf?utm_source=chatgpt.com "localhost:8123/ABX00087/1kfa5-datasheet.html"
[3]: https://github.com/arduino/uno-r4-wifi-usb-bridge/blob/main/UNOR4USBBridge/UNOR4USBBridge.ino?utm_source=chatgpt.com "uno-r4-wifi-usb-bridge/UNOR4USBBridge/UNOR4USBBridge.ino at main · arduino/uno-r4-wifi-usb-bridge · GitHub"
[4]: https://github.com/zephyrproject-rtos/zephyr/blob/main/boards/arduino/uno_r4/doc/index.rst?utm_source=chatgpt.com "zephyr/boards/arduino/uno_r4/doc/index.rst at main · zephyrproject-rtos/zephyr · GitHub"
[5]: https://github.com/arduino/uno-r4-wifi-usb-bridge?utm_source=chatgpt.com "GitHub - arduino/uno-r4-wifi-usb-bridge · GitHub"
