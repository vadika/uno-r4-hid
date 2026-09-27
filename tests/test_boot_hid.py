"""Exercise boot/report protocols on the physical board, with HID drivers detached."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import sys
import time

import usb.core
import usb.util

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hidctl import HidController


def descriptor_bits(descriptor):
    size = count = 0
    bits = {0x80: 0, 0x90: 0}
    offset = 0
    while offset < len(descriptor):
        prefix = descriptor[offset]
        assert prefix != 0xfe, 'Unexpected long HID item'
        length = (0, 1, 2, 4)[prefix & 3]
        value = int.from_bytes(descriptor[offset+1:offset+1+length], 'little')
        tag = prefix & 0xfc
        assert tag != 0x84, 'Boot interfaces must not use report IDs'
        if tag == 0x74: size = value
        elif tag == 0x94: count = value
        elif tag in bits: bits[tag] += size * count
        offset += 1 + length
    return bits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--usb-path', required=True)
    parser.add_argument('--serial', required=True)
    args = parser.parse_args()
    tty = Path(args.port).resolve().name
    node = next(p for p in (Path('/sys/class/tty') / tty / 'device').resolve().parents
                if (p / 'idVendor').exists())
    assert node.name == args.usb_path
    assert (node / 'serial').read_text().strip() == args.serial
    device = usb.core.find(idVendor=0x2341, idProduct=0x006d,
                          bus=int((node / 'busnum').read_text()),
                          address=int((node / 'devnum').read_text()))
    assert device is not None and device.serial_number == args.serial
    configuration = device.get_active_configuration()
    interfaces = [i for i in configuration if i.bInterfaceClass == 3]
    assert [(i.bInterfaceNumber, i.bInterfaceSubClass, i.bInterfaceProtocol)
            for i in interfaces] == [(2, 1, 1), (3, 1, 2)]
    assert configuration.bNumInterfaces == 4
    assert configuration[(0, 0)].bInterfaceClass == 2
    assert configuration[(1, 0)].bInterfaceClass == 10
    endpoints = {i.bInterfaceNumber: next(iter(i)).bEndpointAddress for i in interfaces}
    assert len(set(endpoints.values())) == 2

    def get(request, value, interface, size):
        return bytes(device.ctrl_transfer(0xa1, request, value, interface, size, timeout=1000))

    def set_(request, value, interface, data=b''):
        device.ctrl_transfer(0x21, request, value, interface, data, timeout=1000)

    def read(interface, timeout=1000):
        return bytes(device.read(endpoints[interface], 8, timeout=timeout))

    def drain(interface):
        for _ in range(16):
            try: read(interface, 20)
            except usb.core.USBTimeoutError: return
        raise AssertionError('Endpoint did not become idle')

    def expect(interface, expected):
        actual = read(interface)
        assert actual == expected, (interface, actual.hex(), expected.hex())

    with ExitStack() as stack:
        stack.callback(usb.util.dispose_resources, device)
        for interface in (2, 3):
            if device.is_kernel_driver_active(interface):
                device.detach_kernel_driver(interface)
                stack.callback(device.attach_kernel_driver, interface)
            usb.util.claim_interface(device, interface)
            stack.callback(usb.util.release_interface, device, interface)
        control = stack.enter_context(HidController(args.port))

        def restore():
            for interface in (2, 3):
                set_(0x0a, 0, interface)
                drain(interface)
            control.release_all()
            for interface in (2, 3):
                drain(interface)
                set_(0x0b, 1, interface)
                drain(interface)
            set_(0x09, 0x0200, 2, b'\0')
        stack.callback(restore)

        for interface, input_bits, output_bits in ((2, 64, 8), (3, 32, 0)):
            descriptor = bytes(device.ctrl_transfer(0x81, 6, 0x2200, interface, 512))
            assert descriptor_bits(descriptor) == {0x80: input_bits, 0x90: output_bits}
            assert get(0x03, 0, interface, 1) == b'\1'
            try: set_(0x0b, 2, interface)
            except usb.core.USBError as error: assert error.errno == 32, error
            else: raise AssertionError('Invalid protocol was accepted')
            assert get(0x03, 0, interface, 1) == b'\1'
            set_(0x0a, 0, interface)
            drain(interface)
        print('PASS: 2341:006d, separate 03/01/01 and 03/01/02 interfaces, CDC and report descriptors', flush=True)

        for protocol in (0, 1, 0, 1):
            for interface in (2, 3):
                set_(0x0b, protocol, interface)
                assert get(0x03, 0, interface, 1) == bytes([protocol])
                drain(interface)
            control.down('CTRL')
            expect(2, b'\x01\0\0\0\0\0\0\0')
            control.down('A')
            keyboard = b'\x01\0\x04\0\0\0\0\0'
            expect(2, keyboard)
            assert get(0x01, 0x0100, 2, 8) == keyboard
            control.up('A')
            expect(2, b'\x01\0\0\0\0\0\0\0')
            control.up('CTRL')
            expect(2, bytes(8))
            control.down_mouse('LEFT')
            expect(3, b'\1\0\0' + (b'\0' if protocol else b''))
            control.move(17, -9)
            expect(3, b'\1\x11\xf7' + (b'\0' if protocol else b''))
            control.scroll(-2)
            expect(3, b'\1\0\0' + (b'\xfe' if protocol else b''))
            assert get(0x01, 0x0100, 3, 8) == b'\1\0\0' + (b'\0' if protocol else b'')
            control.up_mouse('LEFT')
            expect(3, bytes(4 if protocol else 3))
            for leds in (0, 2, 0x1f, 0):
                set_(0x09, 0x0200, 2, bytes([leds]))
                assert get(0x01, 0x0200, 2, 1) == bytes([leds])
        print('PASS: protocol switching, exact 8-byte keyboard and 3/4-byte mouse packets, LED reports', flush=True)

        # Switching one interface must not change the other or lose held state.
        control.down('SHIFT')
        expect(2, b'\2' + bytes(7))
        set_(0x0b, 0, 2)
        expect(2, b'\2' + bytes(7))
        assert get(0x03, 0, 3, 1) == b'\1'
        control.up('SHIFT')
        expect(2, bytes(8))
        control.move(10, -5)
        expect(3, b'\0\x0a\xfb\0')
        for interface in (2, 3):
            set_(0x0a, 5 << 8, interface)  # 20 ms idle period
            assert get(0x02, 0, interface, 1) == b'\5'
            reports = []
            for _ in range(4):
                started = time.monotonic()
                packet = read(interface)
                reports.append(time.monotonic() - started)
                assert packet == bytes(8 if interface == 2 else 4)
            assert all(.008 < elapsed < .08 for elapsed in reports[1:]), reports
            set_(0x0a, 0, interface)
            assert get(0x02, 0, interface, 1) == b'\0'
            drain(interface)
            try: read(interface, 100)
            except usb.core.USBTimeoutError: pass
            else: raise AssertionError('SET_IDLE 0 did not suppress unchanged reports')
        print('PASS: independent protocol state, held keys across switching, idle repeats and suppression', flush=True)


if __name__ == '__main__':
    main()
