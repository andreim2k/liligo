#!/usr/bin/env python3
"""
KeyBridge Menu Bar App - HIGHLY VISIBLE VERSION
"""

import asyncio
import subprocess
import threading
import time
import sys
import os
from typing import Optional

from bleak import BleakClient, BleakScanner

try:
    import rumps
    RUMPS_AVAILABLE = True
    print("Using rumps framework")
except ImportError:
    RUMPS_AVAILABLE = False
    print("rumps not available, using basic menu bar")


class KeyBridgeApp(rumps.App):
    def __init__(self):
        # Use HIGHLY VISIBLE text instead of icon
        super(KeyBridgeApp, self).__init__("🔑KB", "KeyBridge")
        self.sending = False
        self.loop = None
        self.ble_thread = None
        
        # Start BLE thread
        self._start_ble_thread()
        
    @rumps.clicked("Send Clipboard")
    def send_clipboard(self, _):
        self._send_clipboard()
        
    @rumps.clicked("Test Visibility")
    def test_visibility(self, _):
        rumps.notification("KeyBridge", "Found You!", "If you see this, the app is working!")
        # Make text even more visible
        self.title = "🔑🔑KEYBRIDGE🔑🔑"
        
    @rumps.clicked("Show Status")
    def show_status(self, _):
        rumps.notification("KeyBridge Status", "Active", f"App is running! Current display: {self.title}")
        
    @rumps.clicked("Quit")
    def quit_app(self, _):
        rumps.quit_application()
        
    def _send_clipboard(self):
        """Send clipboard content."""
        if self.sending:
            rumps.notification("KeyBridge", "Busy", "Already sending...")
            return
            
        clipboard = self._get_clipboard()
        if not clipboard:
            rumps.notification("KeyBridge", "Empty", "Clipboard is empty")
            return
            
        self.sending = True
        self.title = "🔑KB⏳"
        
        # Run async in BLE thread
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self._send_clipboard_flow(clipboard),
                self.loop
            )
            
    def _get_clipboard(self):
        """Get clipboard content."""
        try:
            result = subprocess.run(['pbpaste'], capture_output=True, text=False, timeout=3)
            if result.returncode == 0 and result.stdout:
                content = result.stdout.decode('utf-8', errors='replace')
                if content.strip():
                    print(f"✅ Clipboard: '{content[:50]}...' (len: {len(content)})")
                    return content
                else:
                    print("⚠️  Empty clipboard")
            return None
        except Exception as e:
            print(f"🚨 Clipboard error: {e}")
            return None
            
    async def _send_clipboard_flow(self, text):
        """Complete flow: connect → send → disconnect."""
        client = None
        try:
            device = await BleakScanner.find_device_by_name("KeyBridge", timeout=10.0)
            if device is None:
                rumps.notification("KeyBridge", "Not Found", "Cannot find KeyBridge device")
                self._reset_ui()
                return
                
            # Connect
            self.title = "🔑KB🔗"
            client = BleakClient(device)
            await client.connect()
            
            # Send text
            self.title = "🔑KB📤"
            mtu = client.mtu_size
            chunk_size = min(mtu - 3, 500)
            encoded = text.encode('utf-8')
            
            for i in range(0, len(encoded), chunk_size):
                chunk = encoded[i:i + chunk_size]
                await client.write_gatt_char("beb5483e-36e1-4688-b7f5-ea07361b26a8", chunk, response=True)
                
                if len(encoded) > 500:
                    await asyncio.sleep(0.002)
                    
            rumps.notification("KeyBridge", "Success!", f"Sent {len(text)} characters")
            
        except Exception as e:
            rumps.notification("KeyBridge", "Error", str(e)[:50])
            print(f"Error: {e}")
            
        finally:
            if client and client.is_connected:
                await client.disconnect()
            self._reset_ui()
            
    def _reset_ui(self):
        """Reset UI to idle state."""
        self.title = "🔑KB"
        self.sending = False
            
    def _start_ble_thread(self):
        """Start asyncio event loop in background thread."""
        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()
            
        self.ble_thread = threading.Thread(target=run_loop, daemon=True)
        self.ble_thread.start()
            
    def quit(self):
        """Quit application."""
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
        super().quit()


def main():
    if not RUMPS_AVAILABLE:
        print("rumps not available")
        return
        
    print("Starting HIGHLY VISIBLE KeyBridge...")
    app = KeyBridgeApp()
    app.run()


if __name__ == "__main__":
    main()