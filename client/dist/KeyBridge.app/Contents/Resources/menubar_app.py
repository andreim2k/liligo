#!/usr/bin/env python3
"""
LilyGo KeyBridge Menu Bar App

A macOS menu bar application for sending clipboard content to the KeyBridge dongle.
Double-click on icon: connect → send clipboard → disconnect
Single-click: show menu
"""

import asyncio
import subprocess
import threading
import time
from typing import Optional

from bleak import BleakClient, BleakScanner

import objc
from Foundation import NSObject, NSRunLoop, NSDate
from AppKit import (
    NSApplication,
    NSStatusBar,
    NSMenu,
    NSMenuItem,
    NSVariableStatusItemLength,
    NSImage,
    NSEvent,
    NSKeyDownMask,
    NSCommandKeyMask,
    NSFunctionKeyMask,
)
from PyObjCTools import AppHelper
from Quartz import CGEventGetIntegerValueField, kCGKeyboardEventKeycode

# BLE UUIDs (must match firmware)
SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
CHAR_TEXT_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"

DEVICE_NAME = "KeyBridge"


def get_clipboard():
    """Get clipboard content on macOS."""
    try:
        result = subprocess.run(['pbpaste'], capture_output=True, text=False, timeout=2)
        return result.stdout.decode('utf-8', errors='replace')
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def send_notification(title, subtitle, message):
    """Send macOS notification."""
    try:
        subprocess.run([
            'osascript', '-e',
            f'display notification "{message}" with title "{title}" subtitle "{subtitle}"'
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
        self.event_monitor = None

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

        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit", objc.selector(self.quitApp_, signature=b'v@:@'), "q"
        )
        quit_item.setTarget_(self)
        self.menu.addItem_(quit_item)

        # Start BLE thread
        self._start_ble_thread()

        # Setup global hotkey (Fn+Cmd+V)
        self._setup_global_hotkey()

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
            from Foundation import NSTimer
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

    def sendClipboard_(self, sender):
        """Send clipboard content."""
        if self.sending:
            return

        clipboard = get_clipboard()
        if not clipboard:
            send_notification("KeyBridge", "Empty", "Clipboard is empty")
            return

        self.sending = True
        self._set_title("⌨️⏳")  # Use thread-safe method

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
            chunk_size = min(mtu - 3, 500)

            # Send text
            self._set_title("⌨️📤")
            encoded = text.encode('utf-8')
            for i in range(0, len(encoded), chunk_size):
                chunk = encoded[i:i + chunk_size]
                await client.write_gatt_char(CHAR_TEXT_UUID, chunk, response=True)

                # Add delay between chunks for large pastes to prevent buffer overflow
                if len(encoded) > 1000:
                    await asyncio.sleep(0.005)  # 5ms delay for large pastes
                elif len(encoded) > 500:
                    await asyncio.sleep(0.002)  # 2ms delay for medium pastes

            send_notification("KeyBridge", "Sent", f"{len(text)} chars")

        except Exception as e:
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

    def _setup_global_hotkey(self):
        """Setup global hotkey Fn+Cmd+V to trigger send."""
        def handle_event(event):
            # Check for Cmd+Fn+V using character interpretation (works on all layouts)
            flags = event.modifierFlags()
            chars = event.characters()

            # Check for Cmd+Fn modifiers and V character
            is_cmd = (flags & NSCommandKeyMask) != 0
            is_fn = (flags & NSFunctionKeyMask) != 0

            if is_cmd and is_fn and chars and chars.lower() == 'v':
                self.sendClipboard_(None)
                return None  # Consume the event

            return event

        # Add global event monitor and store reference to prevent garbage collection
        self.event_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSKeyDownMask,
            handle_event
        )

    def quitApp_(self, sender):
        """Quit the application."""
        # Stop event monitor
        if self.event_monitor:
            NSEvent.removeMonitor_(self.event_monitor)
            self.event_monitor = None

        # Cancel any pending timer
        if self.click_timer:
            self.click_timer.invalidate()
            self.click_timer = None

        # Stop asyncio loop
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)

        NSApplication.sharedApplication().terminate_(self)


def main():
    app = NSApplication.sharedApplication()

    # Make it a menu bar only app (no dock icon)
    app.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

    delegate = KeyBridgeDelegate.alloc().init()
    app.setDelegate_(delegate)

    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
