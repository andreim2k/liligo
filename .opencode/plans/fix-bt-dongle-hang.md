# Fix: Bluetooth Dongle Hangs After 3-4 Paste Operations

## Problem
After 3-4 pasting operations over Bluetooth, the dongle BT gets stuck. Progress stays at 100% green, client goes into waiting state and then disconnects.

## Root Causes Identified

### 1. Optimistic Buffer Deduction Drift (CRITICAL)
**File**: `client/menubar_app.py:455`
The client deducts from `current_free` BEFORE the write succeeds. After multiple paste sessions, the client's estimate diverges from the firmware's actual buffer state. The client can think the buffer is full (stuck at 0) when the firmware's queue is actually empty.

### 2. Infinite Wait Loop
**File**: `client/menubar_app.py:437-452`
When `current_free[0] < SEND_THRESHOLD`, the client enters a `while` loop that can run indefinitely. If notifications stop arriving and the fallback read fails, the client is stuck in 5-second timeout cycles.

### 3. Premature Client Disconnect
**File**: `client/menubar_app.py:468-476`
After the last chunk, the client immediately disconnects. But the firmware is still typing at 200 chars/sec. For a 10K char paste, that's 50 seconds of typing after disconnect. If the user triggers another paste quickly, the firmware may be in a corrupted state.

### 4. Stale Notification Callbacks
**File**: `client/menubar_app.py:463-466`
If `stop_notify` fails (connection lost), the callback remains registered in Bleak. On the next connection, duplicate callbacks can fire, corrupting `current_free[0]`.

### 5. Stale `oldDeviceConnected` in Firmware
**File**: `firmware/src/main.cpp:357-393`
If the client reconnects before the soft reset completes, `oldDeviceConnected` may still be `true`, preventing proper reconnection detection.

## Implementation Steps

### Step 1: Fix Client - `_send_clipboard_flow()` method

**File**: `client/menubar_app.py`
**Lines to replace**: 382-476 (entire `_send_clipboard_flow` method)

Replace the entire method with this new implementation:

```python
    async def _send_clipboard_flow(self, text):
        """Complete flow: connect → send → wait for completion → disconnect."""
        client = None
        try:
            # Find device (with retry in case dongle is mid-restart)
            device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)
            if device is None:
                await asyncio.sleep(2)
                device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)
            if device is None:
                send_notification("KeyBridge", "Not Found", f"Cannot find '{DEVICE_NAME}'")
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
            encoded = text.encode('utf-8')

            # Flow control: track firmware buffer free space via notifications
            FIRMWARE_BUFFER = 65535  # Exact usable buffer (64KB - 1 circular sentinel)
            SEND_THRESHOLD = 4096   # Pause sending if fewer than 4KB free
            TOTAL_SEND_TIMEOUT = 30  # Max seconds to wait for buffer space

            buffer_event = asyncio.Event()
            buffer_event.set()
            current_free = [FIRMWARE_BUFFER]

            # Read actual buffer status from firmware (set on connect)
            try:
                raw = await asyncio.wait_for(
                    client.read_gatt_char(CHAR_STATUS_UUID), timeout=3.0)
                val = int.from_bytes(raw, 'little')
                if val > 0:
                    current_free[0] = val
            except Exception:
                pass  # Fall back to FIRMWARE_BUFFER default

            def status_callback(sender, data):
                current_free[0] = int.from_bytes(data, 'little')
                buffer_event.set()

            await client.start_notify(CHAR_STATUS_UUID, status_callback)
            try:
                for i in range(0, len(encoded), chunk_size):
                    chunk = encoded[i:i + chunk_size]

                    # Wait if not enough free space (with total timeout to prevent infinite hangs)
                    wait_start = time.monotonic()
                    while current_free[0] < SEND_THRESHOLD:
                        elapsed = time.monotonic() - wait_start
                        if elapsed >= TOTAL_SEND_TIMEOUT:
                            print(f"[FLOW] Buffer wait timed out after {elapsed:.1f}s, forcing through")
                            break

                        remaining = TOTAL_SEND_TIMEOUT - elapsed
                        buffer_event.clear()
                        try:
                            await asyncio.wait_for(
                                buffer_event.wait(),
                                timeout=min(2.0, remaining)
                            )
                        except asyncio.TimeoutError:
                            pass  # Re-read below

                        # Always re-read actual buffer status from firmware (no optimistic deduction)
                        try:
                            raw = await asyncio.wait_for(
                                client.read_gatt_char(CHAR_STATUS_UUID), timeout=2.0)
                            current_free[0] = int.from_bytes(raw, 'little')
                        except Exception:
                            pass  # Keep current estimate, retry next iteration

                    # Send with timeout — prevents infinite hang if firmware stops responding
                    await asyncio.wait_for(
                        client.write_gatt_char(CHAR_TEXT_UUID, chunk, response=True),
                        timeout=10.0
                    )

                    # Re-read actual buffer status after write (firmware may have consumed chars)
                    try:
                        raw = await asyncio.wait_for(
                            client.read_gatt_char(CHAR_STATUS_UUID), timeout=2.0)
                        current_free[0] = int.from_bytes(raw, 'little')
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
```

### Step 2: Add New Method - `_wait_for_completion()`

**File**: `client/menubar_app.py`
**Insert after**: Line 476 (after `_send_clipboard_flow` method, before `_set_title`)

Add this new method:

```python
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
                print(f"[COMPLETE] Timed out after {elapsed:.1f}s — firmware may still be typing")
                return

            try:
                raw = await asyncio.wait_for(
                    client.read_gatt_char(CHAR_STATUS_UUID), timeout=3.0)
                current_free = int.from_bytes(raw, 'little')
            except Exception:
                stall_count += 1
                if stall_count >= 10:
                    print(f"[COMPLETE] Stalled reading status for {stall_count} attempts")
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
```

### Step 3: Fix Firmware - `onConnect()` Reset State

**File**: `firmware/src/main.cpp`
**Location**: `ServerCallbacks::onConnect()` method, lines 357-393

Add these two lines after line 361 (`pendingRestart = false;`):

```cpp
        oldDeviceConnected = false;  // Prevent stale transition detection
        peakQueueSize.store(0, std::memory_order_relaxed);  // Reset peak for new session
```

The updated `onConnect()` method should look like:

```cpp
    void onConnect(BLEServer *pServer) override
    {
        deviceConnected = true;
        reconnectAttempts = 0;  // Reset backoff on successful connection
        pendingRestart = false;  // Clear restart flag in case connection arrives before restart
        oldDeviceConnected = false;  // Prevent stale transition detection
        peakQueueSize.store(0, std::memory_order_relaxed);  // Reset peak for new session

        // Store remaining countdown time before switching modes
        unsigned long elapsed = getElapsedTime(lastMoveTime, millis());
        if (elapsed < nextMoveDelay)
        {
            pausedTimeRemaining = nextMoveDelay - elapsed;
        }
        else
        {
            pausedTimeRemaining = 0;
        }

        // Switch to keyboard bridge mode
        currentMode = MODE_KEYBOARD_BRIDGE;
        needsDisplayRefresh = true;

        setLed(0, 50, 0); // Green
        Serial.println("BLE connected - switching to KeyBridge mode");

        // Reset flow control state for new connection
        lastReportedFree = 0;

        // Set initial buffer status so client can read it immediately
        if (pStatusCharacteristic)
        {
            size_t qs = queueStart.load(std::memory_order_acquire);
            size_t qe = queueEnd.load(std::memory_order_acquire);
            size_t sz = (qe >= qs) ? (qe - qs) : (MAX_QUEUE_SIZE - qs + qe);
            uint32_t freeBytes = (uint32_t)(MAX_QUEUE_SIZE - 1 - sz);
            pStatusCharacteristic->setValue((uint8_t *)&freeBytes, 4);
        }
    }
```

## Summary of Changes

| File | Change | Lines |
|------|--------|-------|
| `client/menubar_app.py` | Rewrite `_send_clipboard_flow()` | 382-476 |
| `client/menubar_app.py` | Add `_wait_for_completion()` method | After 476 |
| `firmware/src/main.cpp` | Add `oldDeviceConnected = false;` in `onConnect()` | After 361 |
| `firmware/src/main.cpp` | Add `peakQueueSize.store(0, ...)` in `onConnect()` | After 361 |

## Key Improvements

1. **No optimistic deduction** - Client always reads actual buffer status from firmware
2. **30-second timeout** on buffer wait loop prevents infinite hangs
3. **Completion wait** - Client stays connected until firmware finishes typing
4. **Stall detection** - Both wait loops detect and recover from stalls
5. **Force disconnect** on `stop_notify` failure prevents stale callbacks
6. **Clean state on connect** - Firmware resets `oldDeviceConnected` and `peakQueueSize` immediately

## Expected Behavior After Fixes

- **Any number of paste operations**: Each uses fresh buffer state, no cumulative drift
- **Flow control**: Client always reads actual buffer status, never guesses
- **Completion**: Client waits for firmware to finish typing before disconnecting
- **No more hangs**: After any number of paste operations, the dongle remains responsive
