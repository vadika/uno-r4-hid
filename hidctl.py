"""Serial/TCP client for the UNO HID injector."""

import argparse
import getpass
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import time

import serial


class HidError(RuntimeError):
    pass


class HidController:
    def __init__(self, device=None, *, host=None, token=None):
        if bool(device) == bool(host):
            raise ValueError("Specify exactly one serial device or network host")
        self.socket = None
        self.is_serial = bool(device)
        if device:
            self.stream = serial.Serial(device, 115200, timeout=10, write_timeout=3,
                                        exclusive=True)
            self.stream.reset_input_buffer()
        else:
            if not token:
                raise ValueError("Network control requires a token")
            self.socket = socket.create_connection((host, 4242), timeout=10)
            self.stream = self.socket.makefile("rwb", buffering=0)
            try:
                self.command("AUTH " + token)
            except Exception:
                self.stream.close()
                self.socket.close()
                raise

    def command(self, command):
        payload = command.encode("utf-8")
        if any(c in payload for c in (0, 10, 13)) or len(payload) > 255:
            raise ValueError("Command must be one line of at most 255 bytes")
        if self.socket:
            self.socket.sendall(payload + b"\n")
        else:
            self.stream.write(payload + b"\n")
        reply = self.stream.readline(1024)
        if not reply.endswith(b"\n"):
            raise HidError("Incomplete reply or connection timeout")
        reply = reply.decode().strip()
        if reply != "OK" and not reply.startswith("OK "):
            raise HidError(reply)
        return reply

    def ping(self): return self.command("PING")
    def status(self): return self.command("STATUS")
    def key(self, key): return self.command("KEY " + key.upper())
    def down(self, key): return self.command("DOWN " + key.upper())
    def up(self, key): return self.command("UP " + key.upper())
    def combo(self, *keys): return self.command("COMBO " + " ".join(k.upper() for k in keys))
    def hid(self, usage): return self.command(f"HID {int(usage):#x}")
    def move(self, dx, dy): return self.command(f"MOVE {int(dx)} {int(dy)}")
    def scroll(self, amount): return self.command(f"SCROLL {int(amount)}")
    def click(self, button="LEFT"): return self.command("CLICK " + button.upper())
    def down_mouse(self, button): return self.command("DOWN_MOUSE " + button.upper())
    def up_mouse(self, button): return self.command("UP_MOUSE " + button.upper())
    def release_all(self): return self.command("RELEASE_ALL")

    def text(self, text):
        if any(c not in "\t\r\n" and not 32 <= ord(c) <= 126 for c in text):
            raise ValueError("TEXT supports US-layout ASCII only")
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for index, line in enumerate(lines):
            if index:
                self.key("ENTER")
            for start in range(0, len(line), 240):
                self.command("TEXT " + line[start:start + 240])
        return "OK"

    def configure(self, ssid, password, token):
        if not self.is_serial:
            raise ValueError("Configuration requires USB serial")
        if not 1 <= len(ssid.encode()) <= 32 or any(c in ssid for c in ",\0\r\n"):
            raise ValueError("SSID must be 1–32 bytes, without comma, NUL, CR or LF")
        if not 8 <= len(password) <= 63 or any(not 32 <= ord(c) <= 126 or c == ',' for c in password):
            raise ValueError("Password must be 8–63 printable ASCII characters without commas")
        if len(token) != 32 or any(c not in "0123456789abcdefABCDEF" for c in token):
            raise ValueError("Token must contain 32 hexadecimal characters")
        self.command("CONFIG SSID " + ssid)
        self.command("CONFIG PASSWORD " + password)
        self.command("CONFIG TOKEN " + token)
        return self.command("CONFIG SAVE")

    def close(self):
        try:
            if self.socket:
                # The single-client bridge must finish closing before reconnect.
                self.socket.shutdown(socket.SHUT_WR)
                self.stream.read(1)
        except OSError:
            pass
        finally:
            self.stream.close()
            if self.socket:
                self.socket.close()

    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def write_token(path, token):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--device", help="USB serial device (prefer /dev/serial/by-id/…)")
    target.add_argument("--host", help="Wi-Fi IP address; TCP port is 4242")
    parser.add_argument("--token-file", type=Path, default=Path(".arduino/wifi-token"))
    sub = parser.add_subparsers(dest="action", required=True)
    for command in ("ping", "status", "release-all", "reboot", "bootloader", "reset-config"):
        sub.add_parser(command)
    sub.add_parser("session", help="Read protocol commands from stdin on one connection")
    for command in ("key", "down", "up"):
        sub.add_parser(command).add_argument("key")
    sub.add_parser("combo").add_argument("keys", nargs="+")
    sub.add_parser("text").add_argument("text")
    sub.add_parser("hid").add_argument("usage", type=lambda s: int(s, 0))
    move = sub.add_parser("move")
    move.add_argument("dx", type=int)
    move.add_argument("dy", type=int)
    sub.add_parser("scroll").add_argument("amount", type=int)
    for command in ("click", "down-mouse", "up-mouse"):
        sub.add_parser(command).add_argument("button", choices=["left", "right", "middle"])
    setup = sub.add_parser("configure")
    setup.add_argument("--ssid")
    setup.add_argument("--credentials", type=Path, help='JSON file with "ssid" and "password"')
    args = parser.parse_args()
    if args.action in ("configure", "reset-config", "reboot", "bootloader") and not args.device:
        parser.error("This command requires --device")
    if args.action in ("down", "down-mouse"):
        parser.error("Use session or the Python API to hold input across commands on one connection")
    try:
        token = args.token_file.read_text().strip() if args.host else None
        with HidController(args.device, host=args.host, token=token) as hid:
            if args.action == "configure":
                if args.credentials:
                    credentials = json.loads(args.credentials.read_text())
                    ssid, password = credentials["ssid"], credentials["password"]
                else:
                    ssid = args.ssid or input("Wi-Fi SSID: ")
                    password = getpass.getpass("Wi-Fi password: ")
                token = secrets.token_hex(16)
                # Preserve the active token until SAVE succeeds; retain the candidate
                # separately if the device commits but its reply is lost.
                candidate = args.token_file.with_name(args.token_file.name + ".pending")
                write_token(candidate, token)
                print(hid.configure(ssid, password, token))
                os.replace(candidate, args.token_file)
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    status = hid.status()
                    if "wifi=connected" in status and "ip=0.0.0.0" not in status:
                        print(status)
                        return
                    time.sleep(1)
                raise HidError("Configuration saved, but Wi-Fi did not connect; check STATUS")
            elif args.action in ("reboot", "bootloader", "reset-config"):
                print(hid.command("CONFIG RESET" if args.action == "reset-config" else args.action.upper()))
            elif args.action == "session":
                for line in sys.stdin:
                    print(hid.command(line.rstrip("\r\n")), flush=True)
            else:
                values = {
                    "key": [getattr(args, "key", None)], "down": [getattr(args, "key", None)],
                    "up": [getattr(args, "key", None)], "combo": getattr(args, "keys", []),
                    "text": [getattr(args, "text", None)], "hid": [getattr(args, "usage", None)],
                    "move": [getattr(args, "dx", None), getattr(args, "dy", None)],
                    "scroll": [getattr(args, "amount", None)],
                    "click": [getattr(args, "button", None)],
                    "down-mouse": [getattr(args, "button", None)],
                    "up-mouse": [getattr(args, "button", None)],
                }.get(args.action, [])
                print(getattr(hid, args.action.replace("-", "_"))(*values))
    except (OSError, ValueError, KeyError, HidError) as exc:
        parser.exit(1, f"hidctl: {exc}\n")


if __name__ == "__main__":
    main()
