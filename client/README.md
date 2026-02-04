# KeyBridge macOS Menu Bar App

A macOS menu bar application for sending clipboard content to the LilyGo KeyBridge dongle via Bluetooth Low Energy.

## Features

- **Menu Bar Integration**: Runs as a menu bar app (no dock icon)
- **Double-Click to Send**: Double-click the menu bar icon to send clipboard content
- **Global Hotkey**: Use Fn+Cmd+V to send clipboard from anywhere
- **Right-Click Menu**: Access additional options via right-click
- **Automatic Discovery**: Automatically finds and connects to KeyBridge dongle
- **Visual Feedback**: Status indicators show connection and sending progress
- **Large Paste Support**: Handles large text with chunked transmission

## Quick Start

### 1. Build the App

```bash
# Clone and navigate to the project
cd /path/to/liligo/client

# Build the macOS app
./build_app.sh
```

### 2. Install the App

```bash
# Install to Applications folder
./install_app.sh
```

### 3. Launch the App

```bash
# Launch the installed app
open /Applications/KeyBridge.app
```

Or find it in your Applications folder and double-click.

## Usage

### Menu Bar Icon
- **Icon**: ⌨️ (keyboard emoji)
- **Status**:
  - ⌨️ = Idle
  - ⌨️⏳ = Connecting/Sending
  - ⌨️🔗 = Connected
  - ⌨️📤 = Sending data

### Interactions
- **Double-click**: Send clipboard content to KeyBridge
- **Single-click**: Show menu (after brief delay)
- **Right-click**: Show menu immediately
- **Fn+Cmd+V**: Global hotkey to send clipboard

### Menu Options
- **Send Clipboard**: Manually send clipboard content
- **Quit**: Exit the application

## Technical Details

### Bluetooth Connection
- **Device Name**: KeyBridge
- **Service UUID**: `4fafc201-1fb5-459e-8fcc-c5c9c331914b`
- **Characteristic UUID**: `beb5483e-36e1-4688-b7f5-ea07361b26a8`

### App Structure
```
KeyBridge.app/
├── Contents/
│   ├── Info.plist          # App metadata and configuration
│   ├── MacOS/
│   │   └── KeyBridge       # Main executable
│   └── Resources/
│       └── KeyBridge.icns   # App icon
```

## Development

### Dependencies
- Python 3.8+
- bleak (BLE library)
- pyobjc (macOS app frameworks)
- py2app (app bundling)

### Build Requirements
```bash
# Install dependencies
pip3 install bleak pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-Quartz py2app Pillow

# Build the app
python3 simple_setup.py py2app
```

### File Structure
- `menubar_app.py` - Main menu bar application
- `hid_bridge.py` - Command-line BLE client with additional features
- `simple_setup.py` - py2app configuration
- `build_app.sh` - Build script
- `install_app.sh` - Installation script
- `create_icon.py` - Icon generation script

## Troubleshooting

### Common Issues

**App doesn't appear in menu bar**
- Check that the app is running in Activity Monitor
- Ensure LSUIElement is set to true in Info.plist
- Try restarting the app

**Cannot find KeyBridge device**
- Ensure the KeyBridge dongle is powered on and in range
- Check Bluetooth is enabled on your Mac
- Try refreshing by quitting and reopening the app

**Clipboard content not sending**
- Check that clipboard has content (use pbpaste command to verify)
- Ensure KeyBridge is connected (status should show 🔗)
- Try sending smaller text first to test connection

### Logs and Debugging

To see detailed logs, run the app from terminal:
```bash
./dist/KeyBridge.app/Contents/MacOS/KeyBridge
```

## License

This project is part of the LilyGo KeyBridge ecosystem.

## Support

For issues and support, please refer to the LilyGo documentation.