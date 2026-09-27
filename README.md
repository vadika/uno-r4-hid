# UNO R4 WiFi HID controller

Use an unmodified Arduino UNO R4 WiFi as a USB keyboard and mouse controlled over
Wi-Fi. The onboard USB-C connector supplies power and connects to the computer
under test; the stock ESP32 bridge firmware supplies Wi-Fi. USB serial provides
initial configuration and an alternative control channel.

```text
Automation host ── Wi-Fi / TCP 4242 ── UNO R4 WiFi ── USB-C ── Target computer
```

- Keyboard taps, held keys, shortcuts, ASCII text, and raw keyboard usages.
- Relative mouse movement, three buttons, and vertical scrolling.
- Separate boot keyboard and mouse interfaces, with host-selected protocols.
- Token authentication, persistent Wi-Fi configuration, and LED matrix status.
- Release on disconnect and after five seconds without a valid command.
- CLI and Python API; see [TOOL.md](TOOL.md) for the complete agent-facing reference.

The native USB identity remains `2341:006d`. Keyboard interface 2 is `03/01/01`
(class/subclass/protocol); mouse interface 3 is `03/01/02`. Interfaces 0 and 1
provide CDC serial. Keyboard reports are eight bytes; mouse reports are three
bytes in boot mode and four in report mode, which adds the wheel.

## Build and upload

The supplied Nix development environment targets x86-64 Linux and includes
Arduino CLI, Python, and the hardware-test dependencies. Nix must support flakes.

```bash
git clone https://github.com/vadika/uno-r4-hid.git
cd uno-r4-hid
nix develop
make setup
make build
```

`make setup` installs Arduino Renesas core 1.6.0 and Keyboard/Mouse libraries into
the ignored `.arduino/` directory. `make build` applies the required local core
patches before compiling: USB backpressure handling, two HID instances,
descriptor overrides, and protocol request validation. Use this build command
instead of compiling against an unpatched core.

Connect the board to the development machine. Find its port with
`arduino-cli board list` or `ls /dev/serial/by-id/`. In the development shell:

```bash
# Replace this example with the board's current programming port.
arduino-cli upload --fqbn arduino:renesas_uno:unor4wifi \
  --port /dev/ttyACM0 --input-dir build/hid_injector
```

The programming bridge enumerates as `2341:1002`; the running sketch enumerates
as `2341:006d`, with a different serial-device name. For subsequent uploads,
request the bootloader through the running sketch:

```bash
./hidctl --device /dev/ttyACM0 bootloader
```

Wait for the programming bridge to appear, rediscover its port, and upload.
If the sketch cannot accept commands, double-tap the board's reset button to
enter the bootloader. Keep the stock ESP32 bridge firmware; no soldering or
replacement ESP32 firmware is required.

## Configure and use

After uploading, select the native serial port. Still in the development shell:

```bash
./hidctl --device /dev/ttyACM0 configure
```

This prompts for SSID and password, saves configuration on the board, creates a
control token in `.arduino/wifi-token`, and prints the DHCP address. Use that
address below; `192.0.2.10` is only a documentation placeholder.

```bash
./hidctl --host 192.0.2.10 status
./hidctl --host 192.0.2.10 key ENTER
./hidctl --host 192.0.2.10 text 'Hello world!'
./hidctl --host 192.0.2.10 move 100 -30
./hidctl --host 192.0.2.10 release-all
```

Input goes to whichever computer is connected to the board's USB cable. The
tool cannot observe the screen or confirm application results. Text assumes a
US keyboard layout. Use `session` or a Python context manager when holding keys
across commands; individual CLI invocations close their connection and release
input. See [TOOL.md](TOOL.md) for examples, limits, and error recovery.

Control uses a plaintext TCP connection with a bearer token; use a trusted LAN.
Keep credentials and tokens private. They and generated build artifacts are
excluded from Git by `.gitignore`.

## Install the CLI in `~/.local/bin`

Run this Bash snippet from the clone. It creates a launcher that finds the
project's environment and token regardless of your working directory:

```bash
repo_dir="$PWD"
mkdir -p "$HOME/.local/bin"
printf '#!/usr/bin/env bash\nexec nix develop %q --command python3 %q --token-file %q "$@"\n' \
  "$repo_dir" "$repo_dir/hidctl.py" "$repo_dir/.arduino/wifi-token" \
  > "$HOME/.local/bin/hidctl"
chmod +x "$HOME/.local/bin/hidctl"
```

Keep the clone in place, and include `~/.local/bin` in your PATH. Then:

```bash
hidctl --host 192.0.2.10 status
```

## Hardware tests

The tests require the board connected locally, configured Wi-Fi, its token file,
and permission to access USB/input devices. Identify the native serial port,
USB topology path, and serial number in sysfs before running them. Example
arguments below must be replaced with the actual device identifiers.

```bash
# Run inside nix develop. sudo must be able to execute this environment's Python.
sudo "$(command -v python3)" tests/test_boot_hid.py \
  --port /dev/ttyACM0 --usb-path 1-2 --serial BOARD_SERIAL
sudo "$(command -v python3)" tests/test_hid.py \
  --port /dev/ttyACM0 --usb-path 1-2 --serial BOARD_SERIAL
```

The boot test temporarily detaches the board's HID drivers and checks descriptors,
packet formats, protocol switching, idle behavior, and keyboard LED reports.
The command test exclusively grabs the board's input devices so generated input
does not reach the desktop, and tests serial/Wi-Fi commands and release behavior.

Both suites passed on the development UNO R4 WiFi. Actual Ghaf guest passthrough
and BIOS operation remain target-specific validation tasks. The initial
[handoff.md](handoff.md) also records an optional dual-USB hardware proposal;
that wiring is not needed by this implementation.
