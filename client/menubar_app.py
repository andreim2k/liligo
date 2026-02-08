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

from bleak import BleakClient, BleakScanner

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
from Quartz import (
    CGEventGetIntegerValueField,
    kCGKeyboardEventKeycode,
    CGEventTapCreate,
    CGEventGetFlags,
    kCGSessionEventTap,
    kCGHeadInsertEventTap,
    kCGEventTapOptionDefault,
    CGEventMaskBit,
    kCGEventKeyDown,
    CGEventTapEnable,
    CFMachPortCreateRunLoopSource,
    CFRunLoopGetCurrent,
    CFRunLoopAddSource,
    CFRunLoopRemoveSource,
    kCFRunLoopCommonModes,
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskSecondaryFn,
)

# BLE UUIDs (must match firmware)
SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
CHAR_TEXT_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"

DEVICE_NAME = "KeyBridge"

# Global delegate reference for signal handlers
_delegate = None


def _create_event_tap_callback(delegate):
    """Create a C-level event tap callback that properly captures the delegate."""
    def callback(proxy, event_type, event, refcon):
        try:
            # Only process keydown events
            if event_type != kCGEventKeyDown:
                return event

            # Get key code (9 = V key on macOS)
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)

            # Get modifier flags
            flags = CGEventGetFlags(event)

            # Check for Fn+Cmd+V
            has_fn = (flags & kCGEventFlagMaskSecondaryFn) != 0
            has_cmd = (flags & kCGEventFlagMaskCommand) != 0

            if keycode == 9 and has_fn and has_cmd:
                print("[HOTKEY] Fn+Cmd+V triggered!")
                # Schedule on main thread
                delegate.performSelectorOnMainThread_withObject_waitUntilDone_(
                    objc.selector(delegate.sendClipboard_, signature=b'v@:@'),
                    None,
                    False
                )
                # Consume event (don't pass through)
                return None

            # Pass through all other events
            return event

        except Exception as e:
            print(f"[HOTKEY] Error in callback: {e}")
            return event

    return callback


def cleanup_event_tap():
    """Safely cleanup event tap if delegate exists."""
    global _delegate
    if _delegate:
        try:
            if _delegate.event_tap:
                CGEventTapEnable(_delegate.event_tap, False)
                print("[CLEANUP] Event tap disabled")
            if _delegate.run_loop_source:
                CFRunLoopRemoveSource(
                    CFRunLoopGetCurrent(),
                    _delegate.run_loop_source,
                    kCFRunLoopCommonModes
                )
                print("[CLEANUP] Run loop source removed")
        except Exception as e:
            print(f"[CLEANUP] Error: {e}")


def signal_handler(signum, frame):
    """Handle signals and cleanup before exit."""
    print(f"[SIGNAL] Received signal {signum}, cleaning up...")
    cleanup_event_tap()
    sys.exit(0)


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
        self.event_tap = None
        self.run_loop_source = None
        self.event_tap_callback = None

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

        # Setup global hotkey
        self._setup_event_tap()

    def _setup_event_tap(self):
        """Setup CGEventTap for global hotkey (Fn+Cmd+V)."""
        print("[HOTKEY] Setting up Fn+Cmd+V event tap...")

        # Create the event tap
        event_mask = CGEventMaskBit(kCGEventKeyDown)

        # Create callback with proper closure over delegate
        # Store it to prevent garbage collection
        self.event_tap_callback = _create_event_tap_callback(self)

        self.event_tap = CGEventTapCreate(
            kCGSessionEventTap,           # Session level
            kCGHeadInsertEventTap,        # Insert at head
            kCGEventTapOptionDefault,     # Default options
            event_mask,                   # Key down events only
            self.event_tap_callback,      # Callback function
            None                          # User data
        )

        if not self.event_tap:
            print("[HOTKEY] Failed to create event tap - check permissions")
            return

        # Create run loop source and add to current run loop
        self.run_loop_source = CFMachPortCreateRunLoopSource(None, self.event_tap, 0)

        CFRunLoopAddSource(
            CFRunLoopGetCurrent(),
            self.run_loop_source,
            kCFRunLoopCommonModes
        )

        # Enable the event tap
        CGEventTapEnable(self.event_tap, True)

        print("[HOTKEY] Event tap active: Press Fn+Cmd+V")

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

    def quitApp_(self, sender):
        """Quit the application."""
        # Stop event tap
        if self.event_tap:
            CGEventTapEnable(self.event_tap, False)
            if self.run_loop_source:
                CFRunLoopRemoveSource(
                    CFRunLoopGetCurrent(),
                    self.run_loop_source,
                    kCFRunLoopCommonModes
                )

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

    # Register cleanup to run on any exit
    atexit.register(cleanup_event_tap)

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
