#!/usr/bin/env python3
"""
LilyGo T-Dongle-S3 Bluetooth Keyboard Bridge Client

Connects to the T-Dongle-S3 over BLE and sends keystrokes to be typed
on the target computer via USB HID.
"""

import argparse
import asyncio
import sys
import signal
import subprocess
import os
from typing import Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice


def get_clipboard():
    """Get clipboard content on macOS, preserving all formatting."""
    try:
        # Use text=False to get raw bytes, then decode to preserve all chars
        result = subprocess.run(['pbpaste'], capture_output=True, text=False, timeout=2)
        # Decode as UTF-8, preserving newlines and special chars
        return result.stdout.decode('utf-8', errors='replace')
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(f"Warning: Failed to get clipboard: {e}")
        return None


def is_binary_file(file_path: str, sample_size: int = 8192) -> bool:
    """Check if a file is binary by looking for null bytes."""
    try:
        with open(file_path, 'rb') as f:
            chunk = f.read(sample_size)
            if b'\x00' in chunk:
                return True
            # Check for high ratio of non-text bytes
            text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)))
            non_text = sum(1 for b in chunk if b not in text_chars)
            return non_text / len(chunk) > 0.30 if chunk else False
    except (OSError, IOError) as e:
        print(f"Warning: Could not check if file is binary: {e}")
        return True


def read_text_file(file_path: str) -> Optional[str]:
    """Read a text file and return its contents."""
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}")
        return None

    if not os.path.isfile(file_path):
        print(f"Error: Not a file: {file_path}")
        return None

    if is_binary_file(file_path):
        print(f"Error: File appears to be binary: {file_path}")
        return None

    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except (OSError, IOError, UnicodeDecodeError) as e:
        print(f"Error reading file: {e}")
        return None

# BLE UUIDs (must match firmware)
SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
CHAR_TEXT_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
CHAR_HID_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a9"

DEVICE_NAME = "KeyBridge"

# HID Modifier bits
MOD_LCTRL = 0x01
MOD_LSHIFT = 0x02
MOD_LALT = 0x04
MOD_LGUI = 0x08
MOD_RCTRL = 0x10
MOD_RSHIFT = 0x20
MOD_RALT = 0x40
MOD_RGUI = 0x80

# Mac virtual key to HID keycode mapping
# Based on pynput key codes and USB HID Usage Tables
MAC_KEY_TO_HID = {
    # Letters
    'a': 0x04, 'b': 0x05, 'c': 0x06, 'd': 0x07, 'e': 0x08, 'f': 0x09,
    'g': 0x0A, 'h': 0x0B, 'i': 0x0C, 'j': 0x0D, 'k': 0x0E, 'l': 0x0F,
    'm': 0x10, 'n': 0x11, 'o': 0x12, 'p': 0x13, 'q': 0x14, 'r': 0x15,
    's': 0x16, 't': 0x17, 'u': 0x18, 'v': 0x19, 'w': 0x1A, 'x': 0x1B,
    'y': 0x1C, 'z': 0x1D,
    # Numbers
    '1': 0x1E, '2': 0x1F, '3': 0x20, '4': 0x21, '5': 0x22,
    '6': 0x23, '7': 0x24, '8': 0x25, '9': 0x26, '0': 0x27,
    # Special keys
    'Return': 0x28, 'Escape': 0x29, 'BackSpace': 0x2A, 'Tab': 0x2B,
    'space': 0x2C, ' ': 0x2C,  # Both name and char for space
    'enter': 0x28, 'backspace': 0x2A, 'tab': 0x2B,  # Lowercase variants
    '-': 0x2D, '=': 0x2E, '[': 0x2F, ']': 0x30,
    '\\': 0x31, ';': 0x33, "'": 0x34, '`': 0x35, ',': 0x36,
    '.': 0x37, '/': 0x38,
    # Function keys
    'F1': 0x3A, 'F2': 0x3B, 'F3': 0x3C, 'F4': 0x3D, 'F5': 0x3E,
    'F6': 0x3F, 'F7': 0x40, 'F8': 0x41, 'F9': 0x42, 'F10': 0x43,
    'F11': 0x44, 'F12': 0x45,
    # Navigation
    'Insert': 0x49, 'Home': 0x4A, 'Page_Up': 0x4B, 'Delete': 0x4C,
    'End': 0x4D, 'Page_Down': 0x4E, 'Right': 0x4F, 'Left': 0x50,
    'Down': 0x51, 'Up': 0x52,
}

# Shifted characters to their base key
SHIFT_CHARS = {
    '!': '1', '@': '2', '#': '3', '$': '4', '%': '5',
    '^': '6', '&': '7', '*': '8', '(': '9', ')': '0',
    '_': '-', '+': '=', '{': '[', '}': ']', '|': '\\',
    ':': ';', '"': "'", '~': '`', '<': ',', '>': '.', '?': '/',
    'A': 'a', 'B': 'b', 'C': 'c', 'D': 'd', 'E': 'e', 'F': 'f',
    'G': 'g', 'H': 'h', 'I': 'i', 'J': 'j', 'K': 'k', 'L': 'l',
    'M': 'm', 'N': 'n', 'O': 'o', 'P': 'p', 'Q': 'q', 'R': 'r',
    'S': 's', 'T': 't', 'U': 'u', 'V': 'v', 'W': 'w', 'X': 'x',
    'Y': 'y', 'Z': 'z',
}


class KeyBridgeClient:
    """BLE client for the KeyBridge dongle."""

    def __init__(self):
        self.client: Optional[BleakClient] = None
        self.device: Optional[BLEDevice] = None
        self.running = False
        self.key_queue: asyncio.Queue = asyncio.Queue()
        self.text_queue: asyncio.Queue = asyncio.Queue()  # For clipboard paste
        self.chunk_size = 18  # Default, will be updated after MTU negotiation

    async def scan_and_connect(self, timeout: float = 10.0) -> bool:
        """Scan for the KeyBridge device and connect."""
        print(f"Scanning for '{DEVICE_NAME}'...")

        device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=timeout)

        if device is None:
            print(f"Could not find device '{DEVICE_NAME}'")
            return False

        print(f"Found device: {device.name} ({device.address})")
        self.device = device

        self.client = BleakClient(device, disconnected_callback=self._on_disconnect)

        try:
            await self.client.connect()
            # Get the negotiated MTU (bleak auto-negotiates max MTU)
            mtu = self.client.mtu_size
            self.chunk_size = min(mtu - 3, 500)  # MTU - 3 for ATT header, max 500
            print(f"Connected to {device.name} (MTU: {mtu}, chunk: {self.chunk_size})")
            return True
        except Exception as e:
            print(f"Failed to connect: {e}")
            return False

    def _on_disconnect(self, client: BleakClient):
        """Handle disconnection."""
        print("\nDisconnected from device")
        self.running = False

    async def send_text(self, text: str, slow_mode: bool = False):
        """Send text to be typed on target."""
        if not self.client or not self.client.is_connected:
            print("Not connected")
            return

        # Check for non-ASCII characters
        non_ascii = [(i, c, ord(c)) for i, c in enumerate(text) if ord(c) > 127]
        if non_ascii:
            print(f"Warning: {len(non_ascii)} non-ASCII characters found (will be skipped):")
            for pos, char, code in non_ascii[:10]:
                print(f"  Position {pos}: '{char}' (U+{code:04X})")
            if len(non_ascii) > 10:
                print(f"  ... and {len(non_ascii) - 10} more")

        encoded = text.encode('utf-8')

        if slow_mode:
            # Send one character at a time (for debugging)
            print(f"SLOW MODE: Sending {len(encoded)} bytes one at a time...")
            for i, byte in enumerate(encoded):
                c = chr(byte) if 32 <= byte < 127 else f'[{byte:02x}]'
                print(f"  [{i}] Sending byte {byte:02x} ({c})")
                await self.client.write_gatt_char(CHAR_TEXT_UUID, bytes([byte]), response=True)
                await asyncio.sleep(0.05)  # 50ms between chars for visibility
        else:
            # Send in chunks with acknowledgment for reliability
            total_chunks = (len(encoded) + self.chunk_size - 1) // self.chunk_size
            print(f"Sending {len(encoded)} bytes in {total_chunks} chunks (chunk_size={self.chunk_size})...")
            for i in range(0, len(encoded), self.chunk_size):
                chunk = encoded[i:i + self.chunk_size]
                await self.client.write_gatt_char(CHAR_TEXT_UUID, chunk, response=True)

                # Add delay between chunks for large pastes to prevent buffer overflow
                # Device processes at ~1KB/s, so small delays help
                if len(encoded) > 1000:
                    await asyncio.sleep(0.005)  # 5ms delay for large pastes
                elif len(encoded) > 500:
                    await asyncio.sleep(0.002)  # 2ms delay for medium pastes

        print(f"Sent {len(text)} characters ({len(encoded)} bytes)")

    async def send_hid_key(self, modifiers: int, keycode: int):
        """Send a raw HID key event."""
        if not self.client or not self.client.is_connected:
            return

        data = bytes([modifiers, keycode])
        await self.client.write_gatt_char(CHAR_HID_UUID, data)

    async def capture_mode(self):
        """
        Capture keyboard input and forward to the dongle.
        Cmd+V pastes clipboard content as text.
        Other Cmd shortcuts are mapped to Ctrl.
        """
        try:
            from pynput import keyboard
        except ImportError:
            print("Error: pynput is required for capture mode")
            print("Install with: pip install pynput")
            return

        self.running = True
        print("\nCapture mode active. Press Ctrl+C to exit.")
        print("All keystrokes forwarded. Cmd+V pastes clipboard.")
        print("-" * 50)

        # Track modifier state
        modifiers = 0

        def on_press(key):
            nonlocal modifiers

            try:
                # Handle modifier keys
                if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                    modifiers |= MOD_LCTRL
                    return
                elif key == keyboard.Key.shift_l or key == keyboard.Key.shift:
                    modifiers |= MOD_LSHIFT
                    return
                elif key == keyboard.Key.shift_r:
                    modifiers |= MOD_RSHIFT
                    return
                elif key == keyboard.Key.alt_l or key == keyboard.Key.alt:
                    modifiers |= MOD_LALT
                    return
                elif key == keyboard.Key.alt_r:
                    modifiers |= MOD_RALT
                    return
                elif key == keyboard.Key.cmd_l or key == keyboard.Key.cmd:
                    # Track Cmd for paste detection
                    modifiers |= MOD_LGUI
                    return
                elif key == keyboard.Key.cmd_r:
                    modifiers |= MOD_RGUI
                    return

                # Get the keycode
                keycode = 0
                char = None
                current_mods = modifiers
                cmd_pressed = (modifiers & (MOD_LGUI | MOD_RGUI)) != 0

                # Check for Cmd+V (paste) - send clipboard as text
                if cmd_pressed and hasattr(key, 'char') and key.char == 'v':
                    clipboard = get_clipboard()
                    if clipboard:
                        self.text_queue.put_nowait(clipboard)
                    return

                # Check for Cmd+C (copy) - just ignore, let Mac handle it
                if cmd_pressed and hasattr(key, 'char') and key.char == 'c':
                    return

                # Map Cmd to Ctrl for other shortcuts
                if cmd_pressed:
                    current_mods = (current_mods & ~(MOD_LGUI | MOD_RGUI)) | MOD_LCTRL

                # Handle special keys explicitly
                if key == keyboard.Key.space:
                    keycode = 0x2C
                elif key == keyboard.Key.enter:
                    keycode = 0x28
                elif key == keyboard.Key.backspace:
                    keycode = 0x2A
                elif key == keyboard.Key.tab:
                    keycode = 0x2B
                elif key == keyboard.Key.esc:
                    keycode = 0x29
                elif key == keyboard.Key.delete:
                    keycode = 0x4C
                elif key == keyboard.Key.up:
                    keycode = 0x52
                elif key == keyboard.Key.down:
                    keycode = 0x51
                elif key == keyboard.Key.left:
                    keycode = 0x50
                elif key == keyboard.Key.right:
                    keycode = 0x4F
                elif key == keyboard.Key.home:
                    keycode = 0x4A
                elif key == keyboard.Key.end:
                    keycode = 0x4D
                elif key == keyboard.Key.page_up:
                    keycode = 0x4B
                elif key == keyboard.Key.page_down:
                    keycode = 0x4E
                elif hasattr(key, 'char') and key.char:
                    char = key.char
                elif hasattr(key, 'name'):
                    char = key.name

                # If we got a keycode from special key handling, queue it
                if keycode:
                    self.key_queue.put_nowait((current_mods, keycode))
                    return

                if char:
                    # Preserve existing modifiers and add shift if needed
                    current_mods = modifiers
                    if char in SHIFT_CHARS:
                        base_char = SHIFT_CHARS[char]
                        current_mods |= MOD_LSHIFT  # Add shift, preserving other mods
                        char = base_char

                    # Look up HID keycode
                    char_lower = char.lower() if len(char) == 1 else char
                    keycode = MAC_KEY_TO_HID.get(char_lower, 0)

                    if keycode:
                        # Queue the key event with preserved modifiers
                        self.key_queue.put_nowait((current_mods, keycode))

            except Exception as e:
                print(f"Key error: {e}")

        def on_release(key):
            nonlocal modifiers

            # Clear modifier flags
            if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                modifiers &= ~MOD_LCTRL
            elif key == keyboard.Key.shift_l or key == keyboard.Key.shift:
                modifiers &= ~MOD_LSHIFT
            elif key == keyboard.Key.shift_r:
                modifiers &= ~MOD_RSHIFT
            elif key == keyboard.Key.alt_l or key == keyboard.Key.alt:
                modifiers &= ~MOD_LALT
            elif key == keyboard.Key.alt_r:
                modifiers &= ~MOD_RALT
            elif key == keyboard.Key.cmd_l or key == keyboard.Key.cmd:
                modifiers &= ~MOD_LGUI
            elif key == keyboard.Key.cmd_r:
                modifiers &= ~MOD_RGUI

        # Start keyboard listener
        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()

        try:
            while self.running and self.client and self.client.is_connected:
                # Check for clipboard paste text first
                try:
                    text = self.text_queue.get_nowait()
                    await self.send_text(text)
                    continue
                except asyncio.QueueEmpty:
                    pass

                # Check for key events
                try:
                    mods, keycode = await asyncio.wait_for(
                        self.key_queue.get(),
                        timeout=0.05
                    )
                    await self.send_hid_key(mods, keycode)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
        finally:
            listener.stop()
            print("\nCapture mode ended")

    async def send_key_combo(self, *keys, delay: float = 0.1):
        """Send a key combination (e.g., Ctrl+S)."""
        modifiers = 0
        keycode = 0

        for key in keys:
            if key == 'ctrl':
                modifiers |= MOD_LCTRL
            elif key == 'shift':
                modifiers |= MOD_LSHIFT
            elif key == 'alt':
                modifiers |= MOD_LALT
            elif key == 'gui' or key == 'win' or key == 'super':
                modifiers |= MOD_LGUI
            else:
                # It's a keycode or character
                if isinstance(key, int):
                    keycode = key
                elif key in MAC_KEY_TO_HID:
                    keycode = MAC_KEY_TO_HID[key]
                elif key.lower() in MAC_KEY_TO_HID:
                    keycode = MAC_KEY_TO_HID[key.lower()]

        await self.send_hid_key(modifiers, keycode)
        await asyncio.sleep(delay)

    async def send_keys(self, text: str, delay: float = 0.05):
        """Send text character by character via HID (slower but more reliable for commands)."""
        for char in text:
            modifiers = 0
            if char in SHIFT_CHARS:
                modifiers = MOD_LSHIFT
                char = SHIFT_CHARS[char]

            keycode = MAC_KEY_TO_HID.get(char.lower(), 0)
            if keycode:
                await self.send_hid_key(modifiers, keycode)
                await asyncio.sleep(delay)
            elif char == '\n':
                await self.send_hid_key(0, 0x28)  # Enter
                await asyncio.sleep(delay)

    async def save_file_windows(self, content: str, filename: str):
        """
        Save text file on Windows using PowerShell with proper escaping.
        """
        import shlex

        print("Opening PowerShell on Windows...")

        # Sanitize filename - only allow alphanumerics, dash, underscore, dot
        safe_filename = "".join(c for c in filename if c.isalnum() or c in '.-_')
        if not safe_filename:
            safe_filename = "untitled.txt"

        # Win+R to open Run dialog
        await self.send_key_combo('gui', 'r')
        await asyncio.sleep(0.5)

        # Type powershell and press Enter
        await self.send_keys("powershell")
        await self.send_key_combo(0x28)  # Enter
        await asyncio.sleep(1.0)  # Wait for PowerShell to open

        # Use -EncodedCommand with Base64 to avoid escaping issues
        import base64
        ps_cmd = f"$content = @'\n{content}\n'@; Set-Content -Path \"$HOME\\Documents\\{safe_filename}\" -Value $content -Encoding UTF8"

        encoded_cmd = base64.b64encode(ps_cmd.encode('utf-16-le')).decode('ascii')

        print(f"Saving {safe_filename} ({len(content)} chars)...")
        cmd = f"powershell -EncodedCommand {encoded_cmd}"
        await self.send_keys(cmd)
        await self.send_key_combo(0x28)  # Enter
        await asyncio.sleep(0.5)

        print(f"Saved to Documents\\{safe_filename}")

    async def save_binary_windows(self, data: bytes, filename: str):
        """
        Save binary file on Windows using PowerShell with proper encoding.
        """
        import base64

        # Sanitize filename - only allow alphanumerics, dash, underscore, dot
        safe_filename = "".join(c for c in filename if c.isalnum() or c in '.-_')
        if not safe_filename:
            safe_filename = "file.bin"

        b64_data = base64.b64encode(data).decode('ascii')
        print(f"Saving {safe_filename} ({len(data)} bytes as {len(b64_data)} base64 chars)...")

        print("Opening PowerShell on Windows...")

        # Win+R to open Run dialog
        await self.send_key_combo('gui', 'r')
        await asyncio.sleep(0.5)

        # Type powershell and press Enter
        await self.send_keys("powershell")
        await self.send_key_combo(0x28)  # Enter
        await asyncio.sleep(1.0)  # Wait for PowerShell to open

        # Build PowerShell command using -EncodedCommand with base64
        ps_cmd = f"[System.IO.File]::WriteAllBytes([System.IO.Path]::Combine($HOME, 'Documents', '{safe_filename}'), [System.Convert]::FromBase64String('{b64_data}'))"
        encoded_cmd = base64.b64encode(ps_cmd.encode('utf-16-le')).decode('ascii')

        cmd = f"powershell -EncodedCommand {encoded_cmd}"
        await self.send_keys(cmd)
        await self.send_key_combo(0x28)  # Enter
        await asyncio.sleep(0.5)

        print(f"Saved to Documents\\{safe_filename}")

    async def save_file_linux(self, content: str, filename: str):
        """
        Save text file on Linux using cat heredoc with safe delimiter.
        """
        import time

        # Sanitize filename - only allow safe characters
        safe_filename = "".join(c for c in filename if c.isalnum() or c in '.-_/')
        if not safe_filename or safe_filename.startswith('/'):
            safe_filename = "untitled.txt"

        print(f"Saving {safe_filename} on Linux...")

        # Use unique delimiter to avoid conflicts with content
        delimiter = f"EOF_{int(time.time() * 1000)}"

        # Use cat with heredoc (single quotes prevent expansion)
        await self.send_keys(f"cat > {safe_filename} << '{delimiter}'")
        await self.send_key_combo(0x28)  # Enter
        await asyncio.sleep(0.1)

        # Type the content
        await self.send_text(content)

        # End heredoc with unique delimiter
        await self.send_key_combo(0x28)  # Enter
        await self.send_keys(delimiter)
        await self.send_key_combo(0x28)  # Enter
        print(f"Saved to {safe_filename}")

    async def save_binary_linux(self, data: bytes, filename: str):
        """
        Save binary file on Linux using base64 with safe filename handling.
        """
        import base64
        import time

        # Sanitize filename - only allow safe characters
        safe_filename = "".join(c for c in filename if c.isalnum() or c in '.-_/')
        if not safe_filename or safe_filename.startswith('/'):
            safe_filename = "file.bin"

        b64_data = base64.b64encode(data).decode('ascii')

        print(f"Saving {safe_filename} on Linux ({len(data)} bytes as {len(b64_data)} base64 chars)...")

        # Use unique delimiter and safe filename
        delimiter = f"EOF_{int(time.time() * 1000)}"

        # Use cat with heredoc for base64 data (safer than echo)
        await self.send_keys(f"cat << '{delimiter}' | base64 -d > {safe_filename}")
        await self.send_key_combo(0x28)  # Enter
        await asyncio.sleep(0.1)

        # Send base64 data
        await self.send_text(b64_data)

        # End heredoc
        await self.send_key_combo(0x28)  # Enter
        await self.send_keys(delimiter)
        await self.send_key_combo(0x28)  # Enter
        await asyncio.sleep(0.3)
        print(f"Saved to {safe_filename}")

    async def disconnect(self):
        """Disconnect from the device."""
        if self.client and self.client.is_connected:
            await self.client.disconnect()


async def main():
    parser = argparse.ArgumentParser(
        description="KeyBridge - Bluetooth keyboard bridge client\n\n"
                    "Default (no args): Connect and enter capture mode",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--capture", "-c",
        action="store_true",
        help="Capture mode: forward all keystrokes to target (default if no other mode specified)"
    )
    parser.add_argument(
        "--text", "-t",
        type=str,
        help="Text mode: send text string and exit (use '-' for stdin)"
    )
    parser.add_argument(
        "--file", "-f",
        type=str,
        help="File mode: send contents of a text file and exit"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="BLE scan timeout in seconds (default: 10)"
    )
    parser.add_argument(
        "--slow", "-s",
        action="store_true",
        help="Slow mode: send one character at a time with debug output"
    )
    parser.add_argument(
        "--saveon",
        type=str,
        choices=['windows', 'linux', 'win', 'lin'],
        help="Save file on target system (Windows or Linux)"
    )
    parser.add_argument(
        "--name", "-n",
        type=str,
        help="Custom filename to save as on target"
    )

    args = parser.parse_args()

    # Default to capture mode if no text/file specified (or explicit -c)
    capture_mode = args.capture or (not args.text and not args.file)

    client = KeyBridgeClient()

    # Handle Ctrl+C
    def signal_handler(sig, frame):
        print("\nExiting...")
        client.running = False

    signal.signal(signal.SIGINT, signal_handler)

    try:
        # Connect to device
        if not await client.scan_and_connect(timeout=args.timeout):
            sys.exit(1)

        if args.file:
            # File mode
            filename = args.name if args.name else os.path.basename(args.file)

            if args.saveon:
                # Save file on target system
                target = args.saveon.lower()
                binary = is_binary_file(args.file)

                if binary:
                    with open(args.file, 'rb') as f:
                        data = f.read()
                    if target in ('windows', 'win'):
                        await client.save_binary_windows(data, filename)
                    elif target in ('linux', 'lin'):
                        await client.save_binary_linux(data, filename)
                else:
                    text = read_text_file(args.file)
                    if text is None:
                        sys.exit(1)
                    if target in ('windows', 'win'):
                        await client.save_file_windows(text, filename)
                    elif target in ('linux', 'lin'):
                        await client.save_file_linux(text, filename)
            else:
                # Just type the content (text only)
                text = read_text_file(args.file)
                if text is None:
                    sys.exit(1)
                print(f"Sending file: {args.file} ({len(text)} chars)")
                await client.send_text(text, slow_mode=args.slow)

        elif args.text:
            # Text mode
            if args.text == "-":
                text = sys.stdin.read()
            else:
                text = args.text

            if args.saveon:
                # Save text on target system
                target = args.saveon.lower()
                filename = args.name if args.name else "untitled.txt"
                if target in ('windows', 'win'):
                    await client.save_file_windows(text, filename)
                elif target in ('linux', 'lin'):
                    await client.save_file_linux(text, filename)
            else:
                await client.send_text(text, slow_mode=args.slow)

        elif capture_mode:
            # Capture mode (default)
            await client.capture_mode()

    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
