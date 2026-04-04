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
import unicodedata
from typing import Optional

from bleak import BleakClient, BleakScanner, BleakError


# Unicode to ASCII character mappings
def _build_unicode_map():
    m = {}
    # Box-drawing: full range U+2500–U+257F
    # Horizontal lines -> '-'
    for cp in [
        0x2500,
        0x2504,
        0x2508,
        0x254C,
        0x2550,  # light/dashed/double horizontal
        0x2501,
        0x2505,
        0x2509,
        0x254D,
    ]:  # heavy/dashed horizontal
        m[chr(cp)] = "-"
    # Vertical lines -> '|'
    for cp in [
        0x2502,
        0x2506,
        0x250A,
        0x254E,
        0x2551,  # light/dashed/double vertical
        0x2503,
        0x2507,
        0x250B,
        0x254F,
    ]:  # heavy/dashed vertical
        m[chr(cp)] = "|"
    # All remaining box-drawing (corners, junctions, diagonals) -> '+'
    for cp in range(0x2500, 0x2580):
        if chr(cp) not in m:
            m[chr(cp)] = "+"
    # Block elements U+2580–U+259F -> '#'
    for cp in range(0x2580, 0x25A0):
        m[chr(cp)] = "#"
    return m


_UNICODE_MAP = _build_unicode_map()
_UNICODE_MAP.update(
    {
        # Smart quotes
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u00ab": '"',
        "\u00bb": '"',
        # Dashes and hyphens
        "\u2014": "-",
        "\u2013": "-",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2015": "-",
        "\ufe58": "-",
        "\ufe63": "-",
        "\uff0d": "-",
        # Spaces (non-breaking, em-space, en-space, thin, etc.)
        "\u00a0": " ",
        "\u2002": " ",
        "\u2003": " ",
        "\u2004": " ",
        "\u2005": " ",
        "\u2006": " ",
        "\u2007": " ",
        "\u2008": " ",
        "\u2009": " ",
        "\u200a": " ",
        "\u202f": " ",
        "\u205f": " ",
        # Zero-width characters (drop them)
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\ufeff": "",
        "\u200e": "",
        "\u200f": "",
        # Common symbols
        "\u2026": "...",
        "\u2022": "*",
        "\u2023": "*",
        "\u25cf": "*",
        "\u25cb": "o",
        "\u25a0": "#",
        "\u25a1": "[]",
        "\u25aa": "*",
        "\u25ab": "*",
        "\u00b0": "o",
        "\u00a7": "SS",
        "\u00b6": "P",
        "\u00b1": "+/-",
        "\u00d7": "x",
        "\u00f7": "/",
        "\u2264": "<=",
        "\u2265": ">=",
        "\u2260": "!=",
        "\u2248": "~=",
        "\u221e": "inf",
        "\u221a": "sqrt",
        # Arrows
        "\u2192": "->",
        "\u2190": "<-",
        "\u2191": "^",
        "\u2193": "v",
        "\u21d2": "=>",
        "\u21d0": "<=",
        "\u21d4": "<=>",
        "\u2794": "->",
        "\u279c": "->",
        "\u27a1": "->",
        # Legal/trademark
        "\u2122": "(TM)",
        "\u00a9": "(C)",
        "\u00ae": "(R)",
        # Currency
        "\u20ac": "EUR",
        "\u00a3": "GBP",
        "\u00a5": "YEN",
        "\u00a2": "c",
        "\u20b9": "INR",
        "\u20bd": "RUB",
        "\u20a9": "KRW",
        # Fractions
        "\u00bc": "1/4",
        "\u00bd": "1/2",
        "\u00be": "3/4",
        "\u2153": "1/3",
        "\u2154": "2/3",
        # Superscripts / subscripts
        "\u00b2": "2",
        "\u00b3": "3",
        "\u00b9": "1",
        "\u2070": "0",
        "\u2074": "4",
        "\u2075": "5",
        "\u2076": "6",
        "\u2077": "7",
        "\u2078": "8",
        "\u2079": "9",
        # Misc punctuation and symbols
        "\u2018": "'",
        "\u2019": "'",
        "\u00bf": "?",
        "\u00a1": "!",
        "\u2116": "No.",
        "\u2030": "0/00",
        "\u2031": "0/000",
        "\u00b7": ".",
        "\u2027": ".",
        "\u30fb": ".",
        # Checkmarks, crosses, media symbols
        "\u2713": "[v]",
        "\u2714": "[v]",
        "\u2715": "[x]",
        "\u2716": "[x]",
        "\u2717": "[x]",
        "\u2718": "[x]",
        "\u23fa": "(o)",
        "\u23f8": "||",
        "\u23f9": "[]",
        "\u23ef": "|>",
        "\u25b6": ">",
        "\u25c0": "<",
        "\u23f5": ">",
        "\u23f4": "<",
        "\u2b50": "*",
        "\u2605": "*",
        "\u2606": "*",
        "\u2764": "<3",
        "\u2665": "<3",
        # Musical notes
        "\u266a": "#",
        "\u266b": "##",
        "\u266c": "##",
        "\u266d": "b",
        "\u266e": "h",
        "\u266f": "#",
        # Accented vowels (common ones not handled by unicodedata NFKD)
        "\u00e6": "ae",
        "\u00c6": "AE",
        "\u0153": "oe",
        "\u0152": "OE",
        "\u00f8": "o",
        "\u00d8": "O",
        "\u00df": "ss",
        "\u0131": "i",
        "\u0110": "D",
        "\u0111": "d",
        "\u0141": "L",
        "\u0142": "l",
        "\u017d": "Z",
        "\u017e": "z",
    }
)


def convert_to_ascii(text: str) -> str:
    """Convert all non-ASCII characters to ASCII equivalents."""
    result = []
    for c in text:
        if ord(c) <= 127:
            result.append(c)
        elif c in _UNICODE_MAP:
            result.append(_UNICODE_MAP[c])
        else:
            # Fallback: use Unicode decomposition to strip accents (e.g. e with accent -> e)
            nfkd = unicodedata.normalize("NFKD", c)
            ascii_chars = "".join(ch for ch in nfkd if ord(ch) <= 127)
            if ascii_chars:
                result.append(ascii_chars)
            else:
                # Last resort: replace with ? so nothing is silently lost
                result.append("?")
    return "".join(result)


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
        """Complete flow: connect → send → wait for completion → disconnect."""
        client = None
        try:
            # Find device (with retry in case dongle is mid-restart)
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

            # Connect with a fresh client each session (no stale state)
            self._set_title("⌨️🔗")
            client = BleakClient(device)
            await client.connect()

            # Calculate chunk size
            mtu = client.mtu_size
            chunk_size = mtu - 3

            # Send text
            self._set_title("⌨️📤")
            text = convert_to_ascii(text)
            encoded = text.encode("utf-8")

            # Flow control: track firmware buffer free space via notifications
            FIRMWARE_BUFFER = 65535  # Exact usable buffer (64KB - 1 circular sentinel)
            SEND_THRESHOLD = 4096  # Pause sending if fewer than 4KB free
            TOTAL_SEND_TIMEOUT = 30  # Max seconds to wait for buffer space

            buffer_event = asyncio.Event()
            buffer_event.set()
            current_free = [FIRMWARE_BUFFER]

            # Read actual buffer status from firmware (set on connect)
            try:
                raw = await asyncio.wait_for(
                    client.read_gatt_char(CHAR_STATUS_UUID), timeout=3.0
                )
                val = int.from_bytes(raw, "little")
                if val > 0:
                    current_free[0] = val
            except Exception:
                pass  # Fall back to FIRMWARE_BUFFER default

            def status_callback(sender, data):
                current_free[0] = int.from_bytes(data, "little")
                buffer_event.set()

            await client.start_notify(CHAR_STATUS_UUID, status_callback)
            try:
                for i in range(0, len(encoded), chunk_size):
                    chunk = encoded[i : i + chunk_size]

                    # Wait if not enough free space (with total timeout to prevent infinite hangs)
                    wait_start = time.monotonic()
                    while current_free[0] < SEND_THRESHOLD:
                        elapsed = time.monotonic() - wait_start
                        if elapsed >= TOTAL_SEND_TIMEOUT:
                            print(
                                f"[FLOW] Buffer wait timed out after {elapsed:.1f}s, forcing through"
                            )
                            break

                        remaining = TOTAL_SEND_TIMEOUT - elapsed
                        buffer_event.clear()
                        try:
                            await asyncio.wait_for(
                                buffer_event.wait(), timeout=min(2.0, remaining)
                            )
                        except asyncio.TimeoutError:
                            pass  # Re-read below

                        # Always re-read actual buffer status from firmware (no optimistic deduction)
                        try:
                            raw = await asyncio.wait_for(
                                client.read_gatt_char(CHAR_STATUS_UUID), timeout=2.0
                            )
                            current_free[0] = int.from_bytes(raw, "little")
                        except Exception:
                            pass  # Keep current estimate, retry next iteration

                    # Send with timeout — prevents infinite hang if firmware stops responding
                    await asyncio.wait_for(
                        client.write_gatt_char(CHAR_TEXT_UUID, chunk, response=True),
                        timeout=10.0,
                    )

                    # Re-read actual buffer status after write (firmware may have consumed chars)
                    try:
                        raw = await asyncio.wait_for(
                            client.read_gatt_char(CHAR_STATUS_UUID), timeout=2.0
                        )
                        current_free[0] = int.from_bytes(raw, "little")
                    except Exception:
                        pass  # Will be refreshed on next iteration's wait loop
            finally:
                try:
                    await client.stop_notify(CHAR_STATUS_UUID)
                except Exception:
                    # Force disconnect if stop_notify fails (prevents stale callbacks)
                    if client.is_connected:
                        await client.disconnect()
                    raise

            # Wait for firmware to finish typing (queue fully drained)
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

    async def _wait_for_completion(self, client):
        """Wait until firmware reports buffer is fully free (typing complete)."""
        FIRMWARE_BUFFER_FULL = 65535
        COMPLETION_TIMEOUT = 180  # 3 minutes max for very large pastes
        POLL_INTERVAL = 1.0

        start = time.monotonic()
        last_free = 0
        stall_count = 0

        while True:
            elapsed = time.monotonic() - start
            if elapsed >= COMPLETION_TIMEOUT:
                print(
                    f"[COMPLETE] Timed out after {elapsed:.1f}s — firmware may still be typing"
                )
                return

            try:
                raw = await asyncio.wait_for(
                    client.read_gatt_char(CHAR_STATUS_UUID), timeout=3.0
                )
                current_free = int.from_bytes(raw, "little")
            except Exception:
                stall_count += 1
                if stall_count >= 10:
                    print(
                        f"[COMPLETE] Stalled reading status for {stall_count} attempts"
                    )
                    return
                await asyncio.sleep(POLL_INTERVAL)
                continue

            stall_count = 0  # Reset on successful read

            if current_free >= FIRMWARE_BUFFER_FULL - 1:
                print(f"[COMPLETE] Firmware queue drained after {elapsed:.1f}s")
                return

            # Detect stall (buffer not freeing for 30+ seconds)
            if current_free == last_free:
                stall_count += 1
                if stall_count >= 30:
                    print(f"[COMPLETE] Buffer stalled at {current_free} free for 30s")
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
