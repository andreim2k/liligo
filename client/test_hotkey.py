#!/usr/bin/env python3
"""Minimal test: does CGEventTap detect ANY key press?"""
import sys
import time
from Quartz import (
    CGEventTapCreate, CGEventGetIntegerValueField, CGEventGetFlags,
    CGEventMaskBit, CGEventTapEnable,
    CFMachPortCreateRunLoopSource, CFRunLoopGetCurrent, CFRunLoopAddSource,
    CFRunLoopRunInMode,
    kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionListenOnly,
    kCGEventKeyDown, kCGKeyboardEventKeycode, kCFRunLoopCommonModes,
    kCGEventFlagMaskCommand, kCGEventFlagMaskControl, kCGEventFlagMaskShift,
)

print("Testing CGEventTap... Press any key. Ctrl+C to quit.")
print("Looking for Ctrl+Shift+V (keycode 9)")

def callback(proxy, event_type, event, refcon):
    if event_type != kCGEventKeyDown:
        return event
    keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
    flags = CGEventGetFlags(event)
    ctrl = (flags & kCGEventFlagMaskControl) != 0
    shift = (flags & kCGEventFlagMaskShift) != 0
    cmd = (flags & kCGEventFlagMaskCommand) != 0
    mods = []
    if ctrl: mods.append("Ctrl")
    if shift: mods.append("Shift")
    if cmd: mods.append("Cmd")
    mod_str = "+".join(mods) if mods else "none"
    print(f"  KEY: keycode={keycode} modifiers={mod_str}")
    if keycode == 9 and ctrl and shift:
        print("  >>> CTRL+SHIFT+V DETECTED! <<<")
    return event

tap = CGEventTapCreate(
    kCGSessionEventTap,
    kCGHeadInsertEventTap,
    kCGEventTapOptionListenOnly,  # Listen only, don't need to consume
    CGEventMaskBit(kCGEventKeyDown),
    callback,
    None
)

if not tap:
    print("FAILED to create event tap!")
    print("Check: System Settings -> Privacy & Security -> Input Monitoring")
    print("Make sure Terminal (or your terminal app) has permission.")
    sys.exit(1)

print("Event tap created OK!")
source = CFMachPortCreateRunLoopSource(None, tap, 0)
CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
CGEventTapEnable(tap, True)
print("Listening... press keys now:\n")

try:
    while True:
        CFRunLoopRunInMode(kCFRunLoopCommonModes, 1.0, False)
except KeyboardInterrupt:
    print("\nDone.")
