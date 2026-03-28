#!/usr/bin/env python3
"""
LilyGo KeyBridge Menu Bar App

A macOS menu bar application for sending clipboard content to the KeyBridge dongle.
Double-click on icon: connect → send clipboard → disconnect
Single-click: show menu
"""

import asyncio
import atexit
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

from bleak import BleakClient, BleakScanner, BleakError

# Box-drawing characters to ASCII
_BOX_CHARS = {
    '┌': '+', '┐': '+', '└': '+', '┘': '+',
    '├': '+', '┤': '+', '┬': '+', '┴': '+', '┼': '+',
    '─': '-', '│': '|',
    '╔': '+', '╗': '+', '╚': '+', '╝': '+',
    '╠': '+', '╣': '+', '╦': '+', '╩': '+', '╬': '+',
    '═': '=', '║': '|',
}


def convert_to_ascii(text: str) -> str:
    """Convert all non-ASCII characters to ASCII equivalents."""
    result = []
    for c in text:
        if ord(c) <= 127:
            # Already ASCII
            result.append(c)
        elif c in _BOX_CHARS:
            # Box-drawing character
            result.append(_BOX_CHARS[c])
        else:
            # Remove non-ASCII characters not in mapping
            pass  # Skip the character
    return ''.join(result)


import objc
from Foundation import NSObject, NSRunLoop, NSDate, NSTimer
from AppKit import (
    NSApplication,
    NSStatusBar,
    NSMenu,
    NSMenuItem,
    NSVariableStatusItemLength,
    NSImage,
)
from PyObjCTools import AppHelper
from pynput import keyboard

# BLE UUIDs (must match firmware)
SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
CHAR_TEXT_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
CHAR_STATUS_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26aa"

DEVICE_NAME = "KeyBridge"

# Global delegate reference
_delegate = None


def signal_handler(signum, frame):
    """Handle signals and cleanup before exit."""
    print(f"[SIGNAL] Received signal {signum}, cleaning up...")
    if _delegate and _delegate.listener:
        _delegate.listener.stop()
    sys.exit(0)


def get_clipboard():
    """Get clipboard content on macOS."""
    try:
        result = subprocess.run(['pbpaste'], capture_output=True, text=False, timeout=2)
        return result.stdout.decode('utf-8', errors='replace')
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def escape_applescript(s):
    """Escape special characters for AppleScript strings."""
    # Escape backslash first, then double quotes
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    return s


def send_notification(title, subtitle, message):
    """Send macOS notification."""
    try:
        # Escape all string interpolations to prevent AppleScript injection
        safe_title = escape_applescript(title)
        safe_subtitle = escape_applescript(subtitle)
        safe_message = escape_applescript(message)

        subprocess.run([
            'osascript', '-e',
            f'display notification "{safe_message}" with title "{safe_title}" subtitle "{safe_subtitle}"'
        ], capture_output=True, timeout=2)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass  # Silent fail - not critical


class KeyBridgeDelegate(NSObject):
    def init(self):
        self = objc.super(KeyBridgeDelegate, self).init()
        if self is None:
            return None

        self.sending = False
        self.loop = None
        self.ble_thread = None
        self.status_item = None
        self.last_click_time = 0
        self.double_click_threshold = 0.4
        self.click_timer = None
        self.listener = None
        self.ctrl_pressed = False
        self.shift_pressed = False

        return self

    def applicationDidFinishLaunching_(self, notification):
        # Create status bar item
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )

        # Set title
        self.status_item.setTitle_("⌨️")

        # Enable button behavior for click detection
        button = self.status_item.button()
        button.setTarget_(self)
        button.setAction_(objc.selector(self.statusItemClicked_, signature=b'v@:@'))

        # Create menu (shown on right-click or after single-click timeout)
        self.menu = NSMenu.alloc().init()

        send_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Send Clipboard", objc.selector(self.sendClipboard_, signature=b'v@:@'), ""
        )
        send_item.setTarget_(self)
        self.menu.addItem_(send_item)

        self.menu.addItem_(NSMenuItem.separatorItem())

        about_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "About KeyBridge", objc.selector(self.showAbout_, signature=b'v@:@'), ""
        )
        about_item.setTarget_(self)
        self.menu.addItem_(about_item)

        self.menu.addItem_(NSMenuItem.separatorItem())

        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit", objc.selector(self.quitApp_, signature=b'v@:@'), "q"
        )
        quit_item.setTarget_(self)
        self.menu.addItem_(quit_item)

        # Start BLE thread
        self._start_ble_thread()

        # Setup global hotkey
        self._setup_listener()

        # Notify that app started
        send_notification("KeyBridge", "Ready", "Press Ctrl+Shift+V to send clipboard")

    def _setup_listener(self):
        """Setup pynput keyboard listener for global hotkey (Ctrl+Shift+V)."""
        print("[HOTKEY] Setting up Ctrl+Shift+V listener...")

        def on_press(key):
            try:
                # Handle Ctrl key
                if key == keyboard.Key.ctrl or key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                    self.ctrl_pressed = True
                # Handle Shift key
                elif key == keyboard.Key.shift or key == keyboard.Key.shift_l or key == keyboard.Key.shift_r:
                    self.shift_pressed = True
                # Handle V key (as character or Key enum)
                elif hasattr(key, 'char') and key.char == 'v':
                    if self.ctrl_pressed and self.shift_pressed:
                        print("[HOTKEY] Ctrl+Shift+V triggered!")
                        send_notification("KeyBridge", "Hotkey Triggered", "Ctrl+Shift+V detected!")
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            objc.selector(self.sendClipboard_, signature=b'v@:@'),
                            None,
                            False
                        )
                elif key == keyboard.Key.v:
                    if self.ctrl_pressed and self.shift_pressed:
                        print("[HOTKEY] Ctrl+Shift+V triggered!")
                        send_notification("KeyBridge", "Hotkey Triggered", "Ctrl+Shift+V detected!")
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            objc.selector(self.sendClipboard_, signature=b'v@:@'),
                            None,
                            False
                        )
            except Exception as e:
                pass

        def on_release(key):
            try:
                if key == keyboard.Key.ctrl:
                    self.ctrl_pressed = False
                elif key == keyboard.Key.shift:
                    self.shift_pressed = False
            except AttributeError:
                pass

        try:
            self.listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self.listener.start()
            print("[HOTKEY] Listener active: Press Ctrl+Shift+V")
            send_notification("KeyBridge", "Hotkey Ready", "Ctrl+Shift+V listener is active")
        except Exception as e:
            error_msg = str(e)
            print(f"[HOTKEY] Failed to start listener: {error_msg}")
            send_notification("KeyBridge", "Listener Error", f"Hotkey failed: {error_msg[:40]}")

    def statusItemClicked_(self, sender):
        """Handle click on status bar icon."""
        current_time = time.time()
        time_diff = current_time - self.last_click_time

        if time_diff < self.double_click_threshold:
            # Double click detected
            self.last_click_time = 0
            if self.click_timer:
                self.click_timer.invalidate()
                self.click_timer = None
            self.sendClipboard_(sender)
        else:
            # First click - wait to see if it's a double click
            self.last_click_time = current_time

            # Cancel any existing timer
            if self.click_timer:
                self.click_timer.invalidate()

            # Set timer to show menu after threshold
            self.click_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                self.double_click_threshold,
                self,
                objc.selector(self.showMenuAfterDelay_, signature=b'v@:@'),
                None,
                False
            )

    def showMenuAfterDelay_(self, timer):
        """Show menu after single-click delay."""
        self.click_timer = None
        self.last_click_time = 0
        self.status_item.popUpStatusItemMenu_(self.menu)

    def showAbout_(self, sender):
        """Show About dialog with hotkey info and permission requirements."""
        from AppKit import NSAlert

        alert = NSAlert.alloc().init()
        alert.setMessageText_("KeyBridge - Bluetooth Keyboard Bridge")
        alert.setInformativeText_(
            "📱 Send clipboard text via Bluetooth to KeyBridge dongle\n\n"
            "⌨️ Hotkey: Ctrl+Shift+V\n\n"
            "🔐 Required Permissions:\n"
            "• System Settings → Privacy & Security → Input Monitoring\n"
            "  ✓ Add KeyBridge to the list\n\n"
            "• System Settings → Privacy & Security → Accessibility\n"
            "  ✓ Enable KeyBridge\n\n"
            "If the hotkey doesn't work, grant these permissions and restart the app."
        )
        alert.addButtonWithTitle_("OK")
        alert.runModal()

    def sendClipboard_(self, sender):
        """Send clipboard content to BLE device."""
        if self.sending:
            return

        clipboard = get_clipboard()
        if not clipboard:
            send_notification("KeyBridge", "Empty", "Clipboard is empty")
            return

        self.sending = True
        self._set_title("⌨️⏳")

        # Run async in BLE thread
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self._send_clipboard_flow(clipboard),
                self.loop
            )

    async def _send_clipboard_flow(self, text):
        """Complete flow: connect → send → disconnect."""
        client = None
        try:
            # Find device
            device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)
            if device is None:
                send_notification("KeyBridge", "Not Found", f"Cannot find '{DEVICE_NAME}'")
                self._reset_ui()
                return

            # Connect
            self._set_title("⌨️🔗")
            client = BleakClient(device)
            await client.connect()

            # Calculate chunk size
            mtu = client.mtu_size
            chunk_size = mtu - 3

            # Send text
            self._set_title("⌨️📤")
            text = convert_to_ascii(text)
            encoded = text.encode('utf-8')

            if len(encoded) > 60000:  # Flow control for large texts
                buffer_free = asyncio.Event()
                free_space = [0]

                def status_callback(sender, data):
                    free_space[0] = int.from_bytes(data, 'little')
                    buffer_free.set()

                try:
                    await client.start_notify(CHAR_STATUS_UUID, status_callback)

                    for i in range(0, len(encoded), chunk_size):
                        chunk = encoded[i:i + chunk_size]

                        # Check current buffer free space
                        try:
                            raw = await asyncio.wait_for(
                                client.read_gatt_char(CHAR_STATUS_UUID),
                                timeout=5.0
                            )
                            current_free = int.from_bytes(raw, 'little')
                        except Exception as e:
                            print(f"Warning: Could not read buffer status: {e}")
                            current_free = 0

                        # Wait if buffer getting full
                        while current_free < 4096:
                            buffer_free.clear()
                            try:
                                await asyncio.wait_for(buffer_free.wait(), timeout=30.0)
                            except asyncio.TimeoutError:
                                break
                            current_free = free_space[0]

                        await client.write_gatt_char(CHAR_TEXT_UUID, chunk, response=True)
                finally:
                    await client.stop_notify(CHAR_STATUS_UUID)
            else:
                # Small text: send directly (fast path)
                for i in range(0, len(encoded), chunk_size):
                    chunk = encoded[i:i + chunk_size]
                    await client.write_gatt_char(CHAR_TEXT_UUID, chunk, response=True)

            send_notification("KeyBridge", "Sent", f"{len(text)} chars")

        except (BleakError, OSError, asyncio.TimeoutError) as e:
            send_notification("KeyBridge", "Error", str(e)[:50])

        finally:
            if client and client.is_connected:
                await client.disconnect()
            self._reset_ui()

    def _set_title(self, title):
        """Set status item title from any thread."""
        self.status_item.performSelectorOnMainThread_withObject_waitUntilDone_(
            objc.selector(self.status_item.setTitle_, signature=b'v@:@'),
            title,
            False
        )

    def _reset_ui(self):
        """Reset UI to idle state."""
        self._set_title("⌨️")
        self.sending = False

    def _start_ble_thread(self):
        """Start the asyncio event loop in a background thread."""
        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()

        self.ble_thread = threading.Thread(target=run_loop, daemon=True)
        self.ble_thread.start()

    def quitApp_(self, sender):
        """Quit the application."""
        # Stop listener
        if self.listener:
            self.listener.stop()

        # Cancel any pending timer
        if self.click_timer:
            self.click_timer.invalidate()
            self.click_timer = None

        # Stop asyncio loop
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)

        NSApplication.sharedApplication().terminate_(self)


def main():
    global _delegate

    # Register signal handlers for cleanup
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    app = NSApplication.sharedApplication()

    # Make it a menu bar only app (no dock icon)
    app.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

    _delegate = KeyBridgeDelegate.alloc().init()
    app.setDelegate_(_delegate)

    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
