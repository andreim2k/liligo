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
import struct
import subprocess
import sys
import threading
import time
from typing import Optional

from bleak import BleakClient, BleakScanner, BleakError
from char_convert import convert_to_ascii, firmware_filter

import objc
from Foundation import NSObject, NSRunLoop, NSDate, NSTimer, NSUserDefaults
from AppKit import (
    NSApplication,
    NSStatusBar,
    NSMenu,
    NSMenuItem,
    NSVariableStatusItemLength,
    NSImage,
    NSOnState,
    NSOffState,
)
from PyObjCTools import AppHelper
from pynput import keyboard

# BLE UUIDs (must match firmware)
SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
CHAR_TEXT_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
CHAR_STATUS_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26aa"
CHAR_CONFIG_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26ab"
CHAR_TOTAL_UUID  = "beb5483e-36e1-4688-b7f5-ea07361b26ac"

DEVICE_NAME = "KeyBridge"

PROFILE_SLOW = 0
PROFILE_NORMAL = 1
PROFILE_FAST = 2
PROFILE_TURBO = 3
PROFILE_ULTRA = 4
PROFILE_LABELS = {PROFILE_SLOW: "Slow", PROFILE_NORMAL: "Normal", PROFILE_FAST: "Fast", PROFILE_TURBO: "Turbo", PROFILE_ULTRA: "Ultra"}
PROFILE_DEFAULTS_KEY = "profile"


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
        result = subprocess.run(["pbpaste"], capture_output=True, text=False, timeout=2)
        return result.stdout.decode("utf-8", errors="replace")
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

        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{safe_message}" with title "{safe_title}" subtitle "{safe_subtitle}"',
            ],
            capture_output=True,
            timeout=2,
        )
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
        self.cmd_pressed = False

        # Load persisted profile selection (default Normal); the firmware itself
        # persists the same value in NVS so the two should stay in sync.
        defaults = NSUserDefaults.standardUserDefaults()
        stored = defaults.objectForKey_(PROFILE_DEFAULTS_KEY)
        self.current_profile = (
            int(stored) if stored is not None else PROFILE_NORMAL
        )
        if self.current_profile not in PROFILE_LABELS:
            self.current_profile = PROFILE_NORMAL
        self.profile_menu_items = {}

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
        button.setAction_(objc.selector(self.statusItemClicked_, signature=b"v@:@"))

        # Create menu (shown on right-click or after single-click timeout)
        self.menu = NSMenu.alloc().init()

        send_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Send Clipboard", objc.selector(self.sendClipboard_, signature=b"v@:@"), ""
        )
        send_item.setTarget_(self)
        self.menu.addItem_(send_item)

        self.menu.addItem_(NSMenuItem.separatorItem())

        # Speed profile submenu
        profile_root = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Speed Profile", None, ""
        )
        profile_submenu = NSMenu.alloc().init()
        for pid, label in (
            (PROFILE_SLOW, "Slow (~125 chars/sec, VM-safe)"),
            (PROFILE_NORMAL, "Normal (~250 chars/sec)"),
            (PROFILE_FAST, "Fast (~500 chars/sec)"),
            (PROFILE_TURBO, "Turbo (~1000 chars/sec, simple editors only)"),
            (PROFILE_ULTRA, "Ultra (uncapped, TinyUSB-paced, edit.exe only)"),
        ):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                label,
                objc.selector(self.selectProfile_, signature=b"v@:@"),
                "",
            )
            item.setTarget_(self)
            item.setTag_(pid)
            item.setState_(NSOnState if pid == self.current_profile else NSOffState)
            profile_submenu.addItem_(item)
            self.profile_menu_items[pid] = item
        profile_root.setSubmenu_(profile_submenu)
        self.menu.addItem_(profile_root)

        self.menu.addItem_(NSMenuItem.separatorItem())

        about_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "About KeyBridge", objc.selector(self.showAbout_, signature=b"v@:@"), ""
        )
        about_item.setTarget_(self)
        self.menu.addItem_(about_item)

        self.menu.addItem_(NSMenuItem.separatorItem())

        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit", objc.selector(self.quitApp_, signature=b"v@:@"), "q"
        )
        quit_item.setTarget_(self)
        self.menu.addItem_(quit_item)

        # Start BLE thread
        self._start_ble_thread()

        # Setup global hotkey
        self._setup_listener()

    def _setup_listener(self):
        """Setup pynput keyboard listener for global hotkey (Ctrl+Cmd+V)."""
        print("[HOTKEY] Setting up Ctrl+Cmd+V listener...")

        def on_press(key):
            try:
                # Handle Ctrl key
                if (
                    key == keyboard.Key.ctrl
                    or key == keyboard.Key.ctrl_l
                    or key == keyboard.Key.ctrl_r
                ):
                    self.ctrl_pressed = True
                # Handle Cmd key
                elif (
                    key == keyboard.Key.cmd
                    or key == keyboard.Key.cmd_l
                    or key == keyboard.Key.cmd_r
                ):
                    self.cmd_pressed = True
                # Handle V key (as character or Key enum)
                elif hasattr(key, "char") and key.char == "v":
                    if self.ctrl_pressed and self.cmd_pressed:
                        print("[HOTKEY] Ctrl+Cmd+V triggered!")
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            objc.selector(self.sendClipboard_, signature=b"v@:@"),
                            None,
                            False,
                        )
                elif key == keyboard.Key.v:
                    if self.ctrl_pressed and self.cmd_pressed:
                        print("[HOTKEY] Ctrl+Cmd+V triggered!")
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            objc.selector(self.sendClipboard_, signature=b"v@:@"),
                            None,
                            False,
                        )
            except Exception as e:
                pass

        def on_release(key):
            try:
                if (
                    key == keyboard.Key.ctrl
                    or key == keyboard.Key.ctrl_l
                    or key == keyboard.Key.ctrl_r
                ):
                    self.ctrl_pressed = False
                elif (
                    key == keyboard.Key.cmd
                    or key == keyboard.Key.cmd_l
                    or key == keyboard.Key.cmd_r
                ):
                    self.cmd_pressed = False
            except AttributeError:
                pass

        try:
            self.listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self.listener.start()
            print("[HOTKEY] Listener active: Press Ctrl+Cmd+V")
        except Exception as e:
            error_msg = str(e)
            print(f"[HOTKEY] Failed to start listener: {error_msg}")

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
                objc.selector(self.showMenuAfterDelay_, signature=b"v@:@"),
                None,
                False,
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
            "⌨️ Hotkey: Ctrl+Cmd+V\n\n"
            "🔐 Required Permission:\n"
            "System Settings → Privacy & Security → Input Monitoring\n"
            "  ✓ Add KeyBridge to the list\n\n"
            "If the hotkey doesn't work, grant this permission and restart the app."
        )
        alert.addButtonWithTitle_("OK")
        alert.runModal()

    def selectProfile_(self, sender):
        """Profile submenu click handler. Persists locally and pushes to dongle."""
        new_profile = int(sender.tag())
        if new_profile not in PROFILE_LABELS:
            return
        self.current_profile = new_profile

        defaults = NSUserDefaults.standardUserDefaults()
        defaults.setInteger_forKey_(new_profile, PROFILE_DEFAULTS_KEY)

        for pid, item in self.profile_menu_items.items():
            item.setState_(NSOnState if pid == new_profile else NSOffState)

        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self._push_profile_to_dongle(new_profile), self.loop
            )

    async def _push_profile_to_dongle(self, profile_id: int):
        """Briefly connect to the dongle just to set the speed profile.
        The firmware persists it in NVS so this only needs to run once per
        change — subsequent pastes will already see the new profile."""
        client = None
        try:
            device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=5.0)
            if device is None:
                send_notification("KeyBridge", "Profile", "Dongle not found")
                return
            client = BleakClient(device)
            await asyncio.wait_for(client.connect(), timeout=10.0)
            await client.write_gatt_char(
                CHAR_CONFIG_UUID, bytes([profile_id, 0x01]), response=True
            )
            send_notification(
                "KeyBridge", "Profile", f"Set to {PROFILE_LABELS[profile_id]}"
            )
        except Exception as e:
            send_notification("KeyBridge", "Profile error", str(e)[:50])
        finally:
            if client and client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass

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

        # Safety net: auto-reset sending flag after 2 minutes
        def _safety_reset():
            if self.sending:
                print("[SAFETY] Resetting stuck sending flag after timeout")
                self._reset_ui()

        self._send_timer = threading.Timer(120, _safety_reset)
        self._send_timer.daemon = True
        self._send_timer.start()

        # Run async in BLE thread
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self._send_clipboard_flow(clipboard), self.loop
            )

    async def _send_clipboard_flow(self, text):
        """Connect → send chunks with proven flow control → wait for completion → disconnect."""
        client = None
        try:
            device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)
            if device is None:
                await asyncio.sleep(2)
                device = await BleakScanner.find_device_by_name(
                    DEVICE_NAME, timeout=10.0
                )
            if device is None:
                send_notification(
                    "KeyBridge", "Not Found", f"Cannot find '{DEVICE_NAME}'"
                )
                return

            self._set_title("⌨️🔗")
            client = BleakClient(device)
            await asyncio.wait_for(client.connect(), timeout=10.0)

            mtu = client.mtu_size
            chunk_size = mtu - 3

            self._set_title("⌨️📤")
            text = convert_to_ascii(text)
            encoded = text.encode("utf-8")

            # Tell firmware the exact filtered char count so the display
            # can show an accurate countdown.
            total_chars = len(firmware_filter(encoded))
            try:
                await asyncio.wait_for(
                    client.write_gatt_char(
                        CHAR_TOTAL_UUID, struct.pack("<I", total_chars), response=True
                    ),
                    timeout=5.0,
                )
            except Exception as e:
                print(f"[TOTAL] Failed to write total chars: {e}")

            await self._send_chunks(client, encoded, chunk_size)

            self._set_title("⌨️⏳")
            await self._wait_for_completion(client)
            send_notification("KeyBridge", "Sent", f"{len(text)} chars")

        except Exception as e:
            send_notification("KeyBridge", "Error", str(e)[:50])

        finally:
            if client and client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            self._reset_ui()

    async def _send_chunks(self, client, encoded: bytes, chunk_size: int):
        """Send encoded bytes in chunks with STATUS-based flow control.
        Uses notifications + per-chunk reads (belt and suspenders)."""
        FIRMWARE_BUFFER = 65535
        SEND_THRESHOLD = 4096  # pause if fewer than 4 KB free

        buffer_event = asyncio.Event()
        buffer_event.set()
        current_free = [FIRMWARE_BUFFER]

        try:
            raw = await asyncio.wait_for(
                client.read_gatt_char(CHAR_STATUS_UUID), timeout=3.0
            )
            val = int.from_bytes(raw, "little")
            if val > 0:
                current_free[0] = val
        except Exception:
            pass

        def status_callback(sender, data):
            try:
                if data and len(data) >= 4:
                    current_free[0] = int.from_bytes(data[:4], "little")
            except Exception:
                pass
            buffer_event.set()

        await client.start_notify(CHAR_STATUS_UUID, status_callback)
        try:
            for i in range(0, len(encoded), chunk_size):
                chunk = encoded[i : i + chunk_size]

                wait_start = time.monotonic()
                while current_free[0] < SEND_THRESHOLD:
                    buffer_event.clear()
                    try:
                        await asyncio.wait_for(buffer_event.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        try:
                            raw = await asyncio.wait_for(
                                client.read_gatt_char(CHAR_STATUS_UUID), timeout=2.0
                            )
                            current_free[0] = int.from_bytes(raw, "little")
                        except Exception:
                            pass
                        if time.monotonic() - wait_start > 30:
                            print("[FLOW] Buffer wait timed out, forcing through")
                            break

                await asyncio.wait_for(
                    client.write_gatt_char(CHAR_TEXT_UUID, chunk, response=True),
                    timeout=10.0,
                )

                # Per-chunk read: belt-and-suspenders flow control
                try:
                    raw = await asyncio.wait_for(
                        client.read_gatt_char(CHAR_STATUS_UUID), timeout=2.0
                    )
                    current_free[0] = int.from_bytes(raw, "little")
                except Exception:
                    current_free[0] = max(0, current_free[0] - len(chunk))
        finally:
            try:
                await client.stop_notify(CHAR_STATUS_UUID)
            except Exception:
                pass

    async def _wait_for_completion(self, client):
        """Poll STATUS until firmware queue is fully drained (typing complete)."""
        FIRMWARE_BUFFER_FULL = 65535
        COMPLETION_TIMEOUT = 300  # 5 minutes max for very large pastes
        POLL_INTERVAL = 1.0

        start = time.monotonic()
        last_free = 0
        stall_count = 0

        while True:
            elapsed = time.monotonic() - start
            if elapsed >= COMPLETION_TIMEOUT:
                print(f"[COMPLETE] Timed out after {elapsed:.1f}s")
                return

            try:
                raw = await asyncio.wait_for(
                    client.read_gatt_char(CHAR_STATUS_UUID), timeout=3.0
                )
                current_free = int.from_bytes(raw, "little")
            except Exception:
                stall_count += 1
                if stall_count >= 10:
                    return
                await asyncio.sleep(POLL_INTERVAL)
                continue

            stall_count = 0

            if current_free >= FIRMWARE_BUFFER_FULL - 1:
                print(f"[COMPLETE] Queue drained after {elapsed:.1f}s")
                return

            if current_free == last_free:
                stall_count += 1
                if stall_count >= 30:
                    print(f"[COMPLETE] Buffer stalled at {current_free} free")
                    return
            else:
                stall_count = 0

            last_free = current_free
            await asyncio.sleep(POLL_INTERVAL)

    def _set_title(self, title):
        """Set status item title from any thread."""
        self.status_item.performSelectorOnMainThread_withObject_waitUntilDone_(
            objc.selector(self.status_item.setTitle_, signature=b"v@:@"), title, False
        )

    def _reset_ui(self):
        """Reset UI to idle state."""
        self._set_title("⌨️")
        self.sending = False
        if hasattr(self, "_send_timer") and self._send_timer:
            self._send_timer.cancel()
            self._send_timer = None

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
