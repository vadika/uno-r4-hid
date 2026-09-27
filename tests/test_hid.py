import argparse
from contextlib import ExitStack
from pathlib import Path
import socket
import sys
import time

import evdev
from evdev import ecodes as E
from serial import SerialTimeoutException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hidctl import HidController, HidError


def expect_error(hid, command, expected):
    try:
        hid.command(command)
    except HidError as error:
        assert str(error) == expected, str(error)
    else:
        raise AssertionError('Invalid command was accepted')


def serial_stall(control, events, active, port):
    control.down('CTRL')
    events()
    try:
        control.stream.write(b'INVALID\n' * 1000)
    except SerialTimeoutException:
        pass  # Backpressure is expected while deliberately not reading replies.
    time.sleep(6)
    events()
    held_after_stall = active()
    control.stream.reset_output_buffer()
    control.stream.timeout = .1
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        control.stream.read(4096)
    control.stream.timeout = 10
    control.release_all()
    assert not held_after_stall, 'A serial reader that stopped draining replies blocked release'
    control.close()
    time.sleep(.5)
    with HidController(port) as reopened:
        assert reopened.ping() == 'OK PONG'
    print('PASS: stalled serial reader watchdog and clean serial reconnect', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', required=True)
    parser.add_argument('--usb-path', required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--token-file', type=Path, default=Path('.arduino/wifi-token'))
    parser.add_argument('--serial-stall-only', action='store_true')
    args = parser.parse_args()
    tty = Path(args.port).resolve().name
    usb = next(p for p in (Path('/sys/class/tty') / tty / 'device').resolve().parents
               if (p / 'idVendor').exists())
    assert usb.name == args.usb_path
    assert (usb / 'idVendor').read_text().strip() == '2341'
    assert (usb / 'idProduct').read_text().strip() == '006d'
    assert (usb / 'serial').read_text().strip() == args.serial

    with ExitStack() as stack:
        devices = []
        for node in sorted(Path('/sys/class/input').glob('event*')):
            if usb not in (node / 'device').resolve().parents:
                continue
            device = evdev.InputDevice('/dev/input/' + node.name)
            stack.callback(device.close)
            device.grab()
            stack.callback(device.ungrab)
            devices.append(device)
        assert any(E.KEY_A in d.capabilities().get(E.EV_KEY, []) for d in devices)
        assert any(E.BTN_LEFT in d.capabilities().get(E.EV_KEY, []) for d in devices)
        print('Exclusively grabbed:', ', '.join(d.path for d in devices), flush=True)

        def events():
            time.sleep(.04)
            result = []
            for d in devices:
                try:
                    result.extend((e.type, e.code, e.value) for e in d.read()
                                  if e.type in (E.EV_KEY, E.EV_REL)
                                  and not (e.type == E.EV_KEY and e.value == 2)
                                  and not (e.type == E.EV_REL and e.code == E.REL_WHEEL_HI_RES))
                except BlockingIOError:
                    pass
            return result

        def active():
            return set(k for d in devices for k in d.active_keys())

        control = stack.enter_context(HidController(args.port))
        def cleanup():
            try:
                if control.stream.is_open:
                    control.release_all()
            except (HidError, OSError, SerialTimeoutException) as error:
                print(f'Cleanup failed: {error}', file=sys.stderr)
        stack.callback(cleanup)
        assert control.ping() == 'OK PONG'
        if args.serial_stall_only:
            serial_stall(control, events, active, args.port)
            return
        status = control.status()
        assert 'configured=1' in status and 'wifi=connected' in status, status
        address = dict(x.split('=', 1) for x in status.split()[2:])['ip']
        token = args.token_file.read_text().strip()
        hid = stack.enter_context(HidController(host=address, token=token))
        hid.release_all()
        events()
        control.key('A')
        assert events() == [(E.EV_KEY, E.KEY_A, 1), (E.EV_KEY, E.KEY_A, 0)]
        for name in ['ENTER','ESC','TAB','BACKSPACE','DELETE','INSERT','UP','DOWN',
                     'LEFT','RIGHT','HOME','END','PAGEUP','PAGEDOWN',
                     *[f'F{i}' for i in range(1,13)], *'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
                     'CTRL','SHIFT','ALT','GUI','RCTRL','RSHIFT','RALT','RGUI']:
            hid.key(name)
            pressed = [e for e in events() if e[0] == E.EV_KEY]
            assert len(pressed) == 2 and pressed[0][2] == 1 and pressed[1][2] == 0, (name, pressed)
            assert pressed[0][1] == pressed[1][1], (name, pressed)
        print('PASS: serial + Wi-Fi keys, navigation, F1–F12 and modifiers', flush=True)

        hid.down('CTRL')
        events()
        hid.combo('CTRL', 'SHIFT', 'ESC')
        assert events() == [(E.EV_KEY,E.KEY_LEFTSHIFT,1), (E.EV_KEY,E.KEY_ESC,1),
                            (E.EV_KEY,E.KEY_ESC,0), (E.EV_KEY,E.KEY_LEFTSHIFT,0)]
        assert active() == {E.KEY_LEFTCTRL}
        expect_error(hid, 'TEXT hello', 'ERR KEYS_HELD')
        expect_error(hid, 'COMBO CTRL BADKEY', 'ERR BAD_KEY')
        assert not events() and active() == {E.KEY_LEFTCTRL}
        hid.up('CTRL')
        events()
        hid.hid(0x04)
        assert events() == [(E.EV_KEY,E.KEY_A,1), (E.EV_KEY,E.KEY_A,0)]
        hid.text('Aa! 09\t\n')
        typed = events()
        assert sum(e == (E.EV_KEY,E.KEY_A,1) for e in typed) == 2
        assert (E.EV_KEY,E.KEY_LEFTSHIFT,1) in typed
        assert (E.EV_KEY,E.KEY_TAB,1) in typed and (E.EV_KEY,E.KEY_ENTER,1) in typed
        assert not active()
        expect_error(hid, 'TEXT é', 'ERR BAD_TEXT')
        assert not events()
        for key in 'ABCDEF': hid.down(key)
        events()
        expect_error(hid, 'DOWN G', 'ERR TOO_MANY_KEYS')
        expect_error(hid, 'KEY A', 'ERR KEY_HELD')
        assert not events() and len(active()) == 6
        hid.release_all()
        events()
        print('PASS: held modifiers, combo order, raw usages, ASCII text, rollover and validation', flush=True)

        hid.move(500,-400)
        motion = events()
        assert sum(v for t,c,v in motion if t==E.EV_REL and c==E.REL_X) == 500
        assert sum(v for t,c,v in motion if t==E.EV_REL and c==E.REL_Y) == -400
        hid.scroll(-300)
        assert sum(v for t,c,v in events() if t==E.EV_REL and c==E.REL_WHEEL) == -300
        for name, code in [('LEFT',E.BTN_LEFT),('RIGHT',E.BTN_RIGHT),('MIDDLE',E.BTN_MIDDLE)]:
            hid.click(name)
            assert events() == [(E.EV_KEY,code,1),(E.EV_KEY,code,0)]
            hid.down_mouse(name)
            events()
            assert code in active()
            hid.up_mouse(name)
            events()
        hid.down('CTRL')
        hid.down_mouse('LEFT')
        events()
        hid.release_all()
        events()
        assert not active()
        for command in ['MOVE 1', 'MOVE 999999999999999999999 0', 'MOVE 1x 2',
                        'SCROLL -32768', 'CLICK UNKNOWN', 'KEY', 'COMBO A A']:
            expect_error(hid, command, 'ERR BAD_ARGUMENT')
        assert not events()
        for raw in [b'X'*300+b'\n', b'KEY A\0ignored\n']:
            hid.socket.sendall(raw)
            assert hid.stream.readline().strip() == b'ERR BAD_LINE'
            assert hid.ping() == 'OK PONG'
        assert not events()
        print('PASS: split movement/scroll, all buttons, RELEASE_ALL and invalid arguments', flush=True)

        expect_error(hid, 'CONFIG RESET', 'ERR SERIAL_ONLY')
        expect_error(hid, 'REBOOT', 'ERR SERIAL_ONLY')
        hid.down('CTRL')
        hid.down_mouse('LEFT')
        events()
        start = time.monotonic()
        while time.monotonic() - start < 6:
            expect_error(hid, 'NOT_A_COMMAND', 'ERR UNKNOWN_COMMAND')
            time.sleep(.15)
        events()
        assert not active(), 'Invalid traffic prevented watchdog release'
        hid.down('SHIFT')
        events()
        for _ in range(3):
            time.sleep(2)
            hid.ping()
        assert active() == {E.KEY_LEFTSHIFT}, 'Valid keepalive did not preserve held key'
        hid.release_all()
        events()
        print('PASS: five-second watchdog, invalid traffic and valid keepalive', flush=True)

        hid.down('ALT')
        events()
        hid.socket.sendall(b'KEY ')
        time.sleep(5.5)
        events()
        assert not active()
        hid.socket.sendall(b'\n')
        assert hid.stream.readline().strip() == b'ERR BAD_ARGUMENT'
        hid.down('CTRL')
        hid.down_mouse('LEFT')
        events()
        hid.close()
        deadline = time.monotonic() + 2
        while active() and time.monotonic() < deadline:
            time.sleep(.05)
        events()
        assert not active(), 'Disconnect did not release input'
        with socket.create_connection((address,4242),timeout=5) as s:
            with s.makefile('rwb',buffering=0) as stream:
                s.sendall(b'KEY A\n')
                assert stream.readline().strip() == b'ERR AUTH_REQUIRED'
                s.shutdown(socket.SHUT_WR)
                assert stream.read(1) == b''
        assert not events()
        print('PASS: partial-line watchdog, disconnect release and authentication reset', flush=True)
        serial_stall(control, events, active, args.port)


if __name__ == '__main__':
    main()
