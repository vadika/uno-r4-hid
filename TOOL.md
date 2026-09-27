# Tool: UNO R4 HID controller

Send physical USB keyboard and relative mouse input to the computer connected to
the Arduino UNO R4 WiFi's onboard USB-C port. Control the board over Wi-Fi using
`hidctl` or its Python API. USB serial also supports control and initial setup.
The board requires no wiring modifications.

This tool generates input only. It cannot read the screen, identify focused
windows, return pointer coordinates, or confirm that an application completed an
action. Observe the target separately. `OK` acknowledges the device command;
it does not prove the intended UI result. Use input only on the intended target
and within the user's requested task.

## Connection and preflight

Install the launcher using [README.md](README.md#install-the-cli-in-localbin).
The installed `~/.local/bin/hidctl` works from any directory and loads
the project's Nix environment and token automatically:

```bash
hidctl --host 192.0.2.10 status
```

No separate `nix develop` or directory change is needed for CLI commands. If
`hidctl` is not on PATH, invoke `~/.local/bin/hidctl` explicitly. The launcher
requires Nix and uses the source in your clone; keep that directory in place.
Only the Python example and firmware build below require that working directory.

| Setting | Value |
| --- | --- |
| Wi-Fi address | Use the IP printed by `configure` or serial `status`; `192.0.2.10` below is a placeholder |
| TCP port | `4242` |
| Authentication | Project's `.arduino/wifi-token`, loaded automatically regardless of working directory |
| Native USB serial | Discover under `/dev/serial/by-id/`; examples use `/dev/ttyACM0` |
| Native USB identity | `2341:006d` (serial number is board-specific) |
| Keyboard interface | Interface 2: class `03`, boot subclass `01`, protocol `01`; endpoint `0x83` |
| Mouse interface | Interface 3: class `03`, boot subclass `01`, protocol `02`; endpoint `0x84` |
| Programming bridge identity | `2341:1002` (serial number is board-specific) |

Keep token and Wi-Fi credential file contents out of logs and prompts. Specify
`--token-file /absolute/path` before the subcommand if using a different token.

Expected format:

```text
OK STATUS configured=1 wifi=connected ip=192.0.2.10 held=0 usb=1
```

Require `usb=1` before injecting input. `held=1` means at least one key or button
is held. Confirm the board's USB cable goes to the intended target; network
status cannot identify that computer. Use one controller at a time: held state
is shared between serial and network control, and only one TCP client is served.
If the IP changes, obtain it using `status` with `--device` and the serial path
above. Serial access requires the board's USB to be connected to this machine.

## Commands

Invocation: `hidctl --host 192.0.2.10 COMMAND`.
Replace `--host ADDRESS` with `--device SERIAL_PATH` to use USB serial.
Use `hidctl --help` or `hidctl --host ADDRESS COMMAND --help` for argument help.

| CLI command | Python method | Behavior |
| --- | --- | --- |
| `ping` | `ping()` | Returns `OK PONG`; also keeps held input alive. |
| `status` | `status()` | Reports configuration, Wi-Fi, IP, held input, USB; also a keepalive. |
| `key ENTER` | `key("ENTER")` | Press and release one key. |
| `combo CTRL SHIFT ESC` | `combo("CTRL", "SHIFT", "ESC")` | Press in order, release in reverse; preserve previously held keys. |
| `text 'Hello world!'` | `text("Hello world!")` | Type US-layout ASCII; API/CLI split long text and translate newlines to Enter. |
| `hid 0x04` | `hid(0x04)` | Tap a raw keyboard usage (here the physical A key). |
| `move 100 -30` | `move(100, -30)` | Relative movement: positive X right, positive Y down. |
| `scroll -3` | `scroll(-3)` | Vertical wheel delta; target settings determine scrolling direction. |
| `click left` | `click("LEFT")` | Press and release a mouse button. |
| `session` with `DOWN CTRL` / `UP CTRL` | `down("CTRL")` / `up("CTRL")` | Hold/release a key on the same open connection. |
| `session` with `DOWN_MOUSE LEFT` / `UP_MOUSE LEFT` | `down_mouse("LEFT")` / `up_mouse("LEFT")` | Hold/release a mouse button on the same open connection. |
| `release-all` | `release_all()` | Clear every held key and mouse button. |

Accepted key names: `A`–`Z`, `0`–`9`, `F1`–`F12`, `ENTER`, `ESC`, `TAB`,
`BACKSPACE`, `SPACE`, `DELETE`, `INSERT`, `HOME`, `END`, `PAGEUP`, `PAGEDOWN`,
`UP`, `DOWN`, `LEFT`, `RIGHT`, `CAPSLOCK`, `PRINTSCREEN`, `SCROLLLOCK`, `PAUSE`,
`CTRL`, `SHIFT`, `ALT`, `GUI`, `RCTRL`, `RSHIFT`, `RALT`, `RGUI`.
`GUI` is the Windows/Command/Super modifier. CLI and API normalize key names.
Mouse buttons are `left`, `right`, `middle` (uppercase in the raw protocol).

Limits: six ordinary keys plus modifiers held simultaneously; movement and wheel
arguments are integers from `-32767` through `32767`; raw keyboard usages are
`0x04`–`0x73` or `0xe0`–`0xe7`. Raw `HID` taps only. Mouse movement is relative,
not pixel positioning. `TEXT` requires a US target layout and no held keyboard
keys; Unicode is unsupported. `key A` selects a physical key, not letter case.

The USB host selects boot or report protocol independently for each HID interface.
Keyboard reports are always eight bytes without a report ID. Mouse reports have
three bytes in boot mode (buttons, X, Y), or four in report mode (plus wheel).
Wheel commands have no scrolling effect in boot mode. CDC serial remains on
interfaces 0 and 1. These HID class/protocol fields match Ghaf's default rules;
actual passthrough and BIOS operation still require validation on the target.

## Multi-step actions and held input

Each ordinary CLI invocation opens and closes a connection. Closing releases
held input, so standalone CLI `down` and `down-mouse` are rejected. Use a Python
context manager or `session` to keep a connection open across related commands.
The API is preferable when delays or observations are needed between actions.

```bash
cd /path/to/uno-r4-hid
nix develop --command python3 - <<'PY'
from pathlib import Path
from hidctl import HidController

token = Path('.arduino/wifi-token').read_text().strip()
with HidController(host='192.0.2.10', token=token) as hid:
    print(hid.status())
    try:
        hid.down('SHIFT')
        hid.key('RIGHT')
        hid.up('SHIFT')
    finally:
        hid.release_all()
PY
```

This example selects text to the right in a focused text editor; it sends real
input. Adapt it to the authorized task. For serial use `HidController(SERIAL_PATH)`.

For `session`, provide uppercase raw protocol lines on standard input, one
command per line, and read the reply before continuing. EOF closes the connection.
Raw commands use the names in the table, with underscores such as `RELEASE_ALL`
and `DOWN_MOUSE`. The Python `command("...")` method also accepts raw commands.

Held input automatically releases after five seconds without a valid command.
For an intentional longer hold, call `ping()` at intervals shorter than five
seconds on the same connection. Invalid commands and partial lines do not renew
the timeout. Disconnects also trigger release. TCP connections expire after
30 seconds without a valid command. Prefer explicit release and context cleanup.

## Replies and errors

Success replies start with `OK`. CLI errors exit nonzero; protocol failures raise
`HidError` in Python, while transport errors may raise `OSError`/serial exceptions.
Examples: `ERR BAD_KEY`, `ERR BAD_ARGUMENT`, `ERR BAD_TEXT`, `ERR TOO_MANY_KEYS`,
`ERR KEYS_HELD`, `ERR KEY_HELD`, `ERR BUTTON_HELD`, `ERR USB_NOT_READY`,
`ERR AUTH_REQUIRED`, `ERR SERIAL_ONLY`, `ERR BAD_LINE`.

On a lost reply or timeout, an input action may already have occurred. Do not
blindly retry typing, clicks, or shortcuts. Close the connection, reconnect,
release input if needed, and inspect the target before continuing. A successful
`release-all` clears tracked state; if USB is unavailable, delivery of the release
report waits for USB readiness. Use the supplied client for orderly TCP closure.

Custom transport clients must send `AUTH <token>` first over TCP and await
`OK AUTH`. Commands are newline-terminated, at most 255 bytes excluding the
newline, with no embedded NUL or newline; raw `TEXT` accepts at most 240 payload
bytes. Send one command and consume its complete reply before sending another.
Prefer the existing client, which handles authentication and text splitting.

## Serial setup and maintenance

Use setup only when configuration is needed; it changes persistent credentials
and generates a new control token. Interactive setup prompts for SSID/password:

```bash
hidctl --device /dev/ttyACM0 configure
```

For unattended setup, add `--credentials /path/to/private.json` after `configure`.
The JSON must contain `ssid` and `password`; no secrets need to appear in command
arguments. SSIDs are 1–32 UTF-8 bytes without comma/NUL/CR/LF. Passwords are
8–63 printable ASCII characters without commas. Configuration survives reboots.
Use the IP printed by setup. The installed CLI saves the new token to the
project's `.arduino/wifi-token`, or the path selected with `--token-file`.
Credential file paths are resolved relative to your current working directory.
If saving fails, the candidate remains beside the token file with a `.pending`
suffix for recovery; do not assume the board kept its old token if the SAVE
reply was lost.

Serial-only maintenance commands: `reboot`, `bootloader`, `reset-config`.
`reset-config` erases stored Wi-Fi/token configuration. These are not required
for normal input control. LED matrix states: `S` setup, `W` connecting, `R` ready,
`A` authenticated client, `E` error.

For firmware work, run from the project directory:

```bash
cd /path/to/uno-r4-hid
nix develop --command make build
```

This patches pinned Arduino core 1.6.0 for USB backpressure, two HID instances,
firmware-owned descriptors, and protocol request validation before compilation.
