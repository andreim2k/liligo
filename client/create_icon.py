#!/usr/bin/env python3
"""
Generate a simple app icon for KeyBridge using PIL.
Creates a basic keyboard-style icon with multiple sizes.
"""

import os
import sys
import subprocess
import shutil

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Installing PIL/Pillow...")
    subprocess.run([sys.executable, "-m", "pip", "install", "Pillow"])
    from PIL import Image, ImageDraw, ImageFont

def create_icon():
    """Create app icon at different sizes."""
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    
    for size in sizes:
        # Create image with transparent background
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Draw a simple keyboard icon
        margin = size // 8
        keyboard_width = size - 2 * margin
        keyboard_height = keyboard_width // 2
        
        # Keyboard background
        keyboard_x = margin
        keyboard_y = (size - keyboard_height) // 2
        draw.rounded_rectangle(
            [keyboard_x, keyboard_y, keyboard_x + keyboard_width, keyboard_y + keyboard_height],
            radius=size // 16,
            fill=(60, 60, 60, 255),
            outline=(40, 40, 40, 255),
            width=max(1, size // 64)
        )
        
        # Draw keys
        key_rows = 3
        key_cols = 10
        key_margin = keyboard_width // 40
        key_spacing_x = (keyboard_width - 2 * key_margin) // key_cols
        key_spacing_y = (keyboard_height - 2 * key_margin) // key_rows
        key_size = min(key_spacing_x, key_spacing_y) - key_margin
        
        for row in range(key_rows):
            for col in range(key_cols):
                key_x = keyboard_x + key_margin + col * key_spacing_x
                key_y = keyboard_y + key_margin + row * key_spacing_y
                
                draw.rounded_rectangle(
                    [key_x, key_y, key_x + key_size, key_y + key_size],
                    radius=size // 64,
                    fill=(200, 200, 200, 255),
                    outline=(150, 150, 150, 255),
                    width=1
                )
        
        # Add Bluetooth symbol
        bt_size = size // 4
        bt_x = size - bt_size - margin
        bt_y = margin
        
        # Simple Bluetooth symbol approximation
        draw.polygon([
            (bt_x + bt_size//2, bt_y),
            (bt_x + bt_size, bt_y + bt_size//2),
            (bt_x + bt_size//2, bt_y + bt_size),
            (bt_x + bt_size//2, bt_y + bt_size//3),
            (bt_x + bt_size//3, bt_y + bt_size//2),
            (bt_x + bt_size//2, bt_y + 2*bt_size//3),
        ], fill=(0, 122, 255, 255))
        
        # Save the icon
        filename = f"icon_{size}x{size}.png"
        img.save(filename, 'PNG')
        print(f"Created {filename}")
    
    # Create iconset
    print("\nCreating iconset...")

    # Copy files to iconset with proper names
    shutil.copy("icon_16x16.png", "KeyBridge.iconset/icon_16x16.png")
    shutil.copy("icon_32x32.png", "KeyBridge.iconset/icon_16x16@2x.png")
    shutil.copy("icon_32x32.png", "KeyBridge.iconset/icon_32x32.png") 
    shutil.copy("icon_64x64.png", "KeyBridge.iconset/icon_32x32@2x.png")
    shutil.copy("icon_128x128.png", "KeyBridge.iconset/icon_128x128.png")
    shutil.copy("icon_256x256.png", "KeyBridge.iconset/icon_128x128@2x.png")
    shutil.copy("icon_256x256.png", "KeyBridge.iconset/icon_256x256.png")
    shutil.copy("icon_512x512.png", "KeyBridge.iconset/icon_256x256@2x.png")
    shutil.copy("icon_512x512.png", "KeyBridge.iconset/icon_512x512.png")
    shutil.copy("icon_1024x1024.png", "KeyBridge.iconset/icon_512x512@2x.png")
    
    # Create icns file
    os.system("iconutil -c icns KeyBridge.iconset")
    print("Created KeyBridge.icns")
    
    # Clean up PNG files
    for size in sizes:
        os.remove(f"icon_{size}x{size}.png")

if __name__ == "__main__":
    create_icon()