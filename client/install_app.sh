#!/bin/bash

# KeyBridge Install Script
# Installs the built app to the user's Applications folder

set -e

APP_BUNDLE="dist/KeyBridge.app"
INSTALL_PATH="$HOME/Applications/KeyBridge.app"

echo "📱 Installing KeyBridge to $HOME/Applications folder..."

# Check if app exists
if [ ! -d "$APP_BUNDLE" ]; then
    echo "❌ App bundle not found at $APP_BUNDLE"
    echo "🔨 Please run ./build_app.sh first to build the app"
    exit 1
fi

# Create Applications directory if it doesn't exist
mkdir -p "$HOME/Applications"

# Check if already installed and ask to replace
if [ -d "$INSTALL_PATH" ]; then
    echo "⚠️  KeyBridge is already installed at $INSTALL_PATH"
    read -p "🔄 Replace existing installation? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "❌ Installation cancelled"
        exit 1
    fi
    echo "🗑️  Removing existing installation..."
    rm -rf "$INSTALL_PATH"
fi

# Copy app to Applications
echo "📋 Copying app to $HOME/Applications..."
cp -R "$APP_BUNDLE" "$INSTALL_PATH"

echo "✅ KeyBridge successfully installed to $HOME/Applications/"
echo ""
echo "🚀 To launch KeyBridge:"
echo "   open $HOME/Applications/KeyBridge.app"
echo ""
echo "⌨️  The app will appear in your menu bar (⌨️)"
echo "   • Double-click to send clipboard content"
echo "   • Single-click for menu"
echo "   • Use Fn+Cmd+V hotkey to send clipboard"