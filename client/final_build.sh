#!/bin/bash

# Final build script with icon fix

echo "🔨 Building KeyBridge macOS App with working rumps..."

# Kill any existing processes
pkill -f KeyBridge 2>/dev/null || true

# Rebuild the working app
python3 working_setup.py py2app

# Launch the app
if [ -d "dist/KeyBridge.app" ]; then
    echo "✅ Build successful!"
    echo "📱 App bundle created: dist/KeyBridge.app"
    echo "🚀 Launching KeyBridge..."
    
    # Check if icon exists
    if [ -f "dist/KeyBridge.app/Contents/Resources/KeyBridge.icns" ]; then
        echo "✅ Icon included in app bundle"
    else
        echo "⚠️  Icon missing from app bundle"
    fi
    
    # Launch the app
    open dist/KeyBridge.app
    
    echo ""
    echo "📋 Usage instructions:"
    echo "   1. Copy text (Cmd+C)"
    echo "   2. Click ⌨️ in menu bar" 
    echo "   3. Click 'Send Clipboard'"
    echo "   4. Watch for connection status:"
    echo "      ⌨️ = Idle"
    echo "      ⌨️⏳ = Connecting/Sending"
    echo "      ⌨️🔗 = Connected"
    echo "      ⌨️📤 = Sending data"
else
    echo "❌ Build failed!"
    exit 1
fi