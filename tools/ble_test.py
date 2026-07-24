#!/usr/bin/env python3
"""
OpenWaifu BLE CLI client.

Scans for a BLE peripheral advertising the local name "OpenWaifu", connects to
it, locates the Tuya common GATT service (0x1910) and its Write characteristic
(0x2B11), then reads lines from the terminal and sends each line as UTF-8 to
the device.  The firmware prints the payload to the serial debug log and shows
it on the LVGL display.

Tested on macOS with CoreBluetooth.  Requires Python 3.9+ and the ``bleak``
package::

    pip install bleak

Run::

    python3 tools/ble_client.py

Type a message and press Enter to send.  Type ``:quit`` (or press Ctrl-C) to
disconnect and exit.
"""

from __future__ import annotations

import asyncio
import sys

from bleak import BleakClient, BleakScanner

DEVICE_NAME = "OpenWaifu"
SERVICE_UUID = "0000fd50-0000-1000-0880-00805f9b34fb"
WRITE_CHAR_UUID = "00000001-0000-1001-8001-00805f9b07d0"
MAX_PAYLOAD = 240


async def find_device(name: str, timeout: float = 10.0):
    print(f'[*] Scanning for BLE device "{name}"...')
    device = await BleakScanner.find_device_by_name(name, timeout=timeout)
    if device is None:
        print(f'[!] Device "{name}" not found within {timeout:.0f}s.')
        return None
    rssi = device.details.get("rssi") if isinstance(device.details, dict) else None
    rssi_str = f"  RSSI={rssi}" if rssi is not None else ""
    print(f"[+] Found {device.name} [{device.address}]{rssi_str}")
    return device


def pick_write_char(client: BleakClient):
    target = WRITE_CHAR_UUID.lower()
    for service in client.services:
        for char in service.characteristics:
            if char.uuid and char.uuid.lower() == target:
                return char
    return None


async def run() -> int:
    device = await find_device(DEVICE_NAME)
    if device is None:
        return 1

    async with BleakClient(device, timeout=15.0) as client:
        print(f"[+] Connected, MTU={client.mtu_size}")
        char = pick_write_char(client)
        if char is None:
            print(f"[!] Could not locate a writable characteristic under service {SERVICE_UUID}.")
            return 2

        print(f"[+] Write characteristic: {char.uuid}")
        print(f"[*] Type a message and press Enter to send (max {MAX_PAYLOAD} UTF-8 bytes).")
        print("[*] Type :quit or press Ctrl-C to disconnect.\n")

        loop = asyncio.get_running_loop()
        while True:
            try:
                line = await loop.run_in_executor(None, input, "msg> ")
            except (EOFError, KeyboardInterrupt):
                print("\n[*] Exiting.")
                break

            text = line.strip()
            if text.lower() in (":quit", ":q", "exit"):
                break
            if not text:
                continue

            payload = text.encode("utf-8")
            if len(payload) > MAX_PAYLOAD:
                print(f"[!] Payload too large ({len(payload)} > {MAX_PAYLOAD} bytes), truncated.")
                payload = payload[:MAX_PAYLOAD]

            response = "write-without-response" in char.properties
            await client.write_gatt_char(char, payload, response=response)
            print(f"[+] Sent {len(payload)} bytes: {payload!r}")

    print("[*] Disconnected.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run()))
    except KeyboardInterrupt:
        sys.exit(130)
