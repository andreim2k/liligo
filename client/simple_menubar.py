#!/usr/bin/env python3
"""
KeyBridge Menu Bar App - Version with proper icon support
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
        # Initialize with emoji first, we'll update icon later
        super(KeyBridgeApp, self).__init__("⌨️", "KeyBridge")
        
        self.sending = False
        self.loop = None
        self.ble_thread = None
        
        # Try to set custom icon after initialization
        self._set_custom_icon()
        
        # Start BLE thread
        self._start_ble_thread()
        
    def _set_custom_icon(self):
        """Set custom icon if available."""
        try:
            # Try multiple paths for the icon
            possible_paths = [
                "KeyBridge.icns",  # Current directory
                os.path.join(os.path.dirname(__file__), "KeyBridge.icns"),  # Bundle Resources
                "/Applications/KeyBridge.app/Contents/Resources/KeyBridge.icns",  # Installed app
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    print(f"✅ Found icon at: {path}")
                    self.icon = path
                    return
                    
            print("⚠️  Custom icon not found, using emoji")
            
        except Exception as e:
            print(f"❌ Error setting icon: {e}")
        
    @rumps.clicked("Send Clipboard")
    def send_clipboard(self, _):
        self._send_clipboard()
        
    @rumps.clicked("Test Icon")
    def test_icon(self, _):
        rumps.notification("Icon Test", "Status", f"Current icon: {getattr(self, 'icon', 'emoji')}")
        
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
            rumps.notification("KeyBridge", "Empty", "Clipboard is empty or inaccessible")
            return
            
        self.sending = True
        self.title = "⌨️⏳"
        
        # Run async in BLE thread
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self._send_clipboard_flow(clipboard),
                self.loop
            )
            
    def _get_clipboard(self):
        """Get clipboard content with robust error handling."""
        try:
            # Try multiple clipboard access methods
            methods = [
                # Method 1: Standard pbpaste
                ['pbpaste'],
                # Method 2: With explicit encoding
                ['pbpaste', '-Prefer', 'ascii'],
            ]
            
            for i, cmd in enumerate(methods):
                try:
                    print(f"Trying clipboard method {i+1}: {' '.join(cmd)}")
                    result = subprocess.run(cmd, capture_output=True, text=False, timeout=3)
                    
                    if result.returncode == 0 and result.stdout:
                        content = result.stdout.decode('utf-8', errors='replace')
                        if content.strip():
                            print(f"✅ Clipboard content: '{content[:50]}...' (len: {len(content)})")
                            return content
                        else:
                            print(f"⚠️  Empty clipboard")
                            
                except subprocess.TimeoutExpired:
                    print(f"⏱️  Timeout for method {i+1}")
                    continue
                except subprocess.CalledProcessError as e:
                    print(f"❌ Process error {i+1}: {e}")
                    continue
                except Exception as e:
                    print(f"❌ Unexpected error {i+1}: {e}")
                    continue
            
            print("❌ All clipboard methods failed")
            return None
            
        except Exception as e:
            print(f"🚨 Critical clipboard error: {e}")
            return None
            
    async def _send_clipboard_flow(self, text):
        """Complete flow: connect → send → disconnect."""
        client = None
        try:
            # Find device
            device = await BleakScanner.find_device_by_name("KeyBridge", timeout=10.0)
            if device is None:
                rumps.notification("KeyBridge", "Not Found", "Cannot find 'KeyBridge' device")
                self._reset_ui()
                return
                
            # Connect
            self.title = "⌨️🔗"
            client = BleakClient(device)
            await client.connect()
            
            # Send text
            self.title = "⌨️📤"
            mtu = client.mtu_size
            chunk_size = min(mtu - 3, 500)
            encoded = text.encode('utf-8')
            
            for i in range(0, len(encoded), chunk_size):
                chunk = encoded[i:i + chunk_size]
                await client.write_gatt_char("beb5483e-36e1-4688-b7f5-ea07361b26a8", chunk, response=True)
                
                # Add delay for large pastes
                if len(encoded) > 500:
                    await asyncio.sleep(0.002)
                    
            rumps.notification("KeyBridge", "Success", f"Sent {len(text)} characters")
            
        except Exception as e:
            rumps.notification("KeyBridge", "Error", str(e)[:50])
            print(f"Error: {e}")
            
        finally:
            if client and client.is_connected:
                await client.disconnect()
            self._reset_ui()
            
    def _reset_ui(self):
        """Reset UI to idle state."""
        self.title = "⌨️"
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
        print("rumps not available, cannot create menu bar app")
        print("Install rumps with: pip install rumps")
        return
        
    print("Starting KeyBridge with rumps...")
    print("Icon debug:", os.listdir('.') if os.path.exists('.') else 'No current dir')
    app = KeyBridgeApp()
    app.run()


if __name__ == "__main__":
    main()