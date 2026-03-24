#!/bin/bash

# KeyBridge macOS App Build Script
# Builds the menubar app into a distributable macOS app bundle

set -e

echo "🔨 Building KeyBridge macOS App..."

# Clean previous builds
echo "🧹 Cleaning previous builds..."
rm -rf build/ dist/

# Icon already exists (KeyBridge.icns)
echo "🎨 Using existing app icon..."

# Build the app
echo "📦 Building app bundle..."
pyinstaller KeyBridge.spec --clean

# Check if build succeeded
if [ -d "dist/KeyBridge.app" ]; then
    echo "✅ Build successful!"
    echo "📁 App bundle created: dist/KeyBridge.app"
    echo ""
    echo "🚀 To run the app:"
    echo "   open dist/KeyBridge.app"
    echo ""
    echo "📋 To install to Applications:"
    echo "   cp -R dist/KeyBridge.app /Applications/"
    echo ""
    echo "🔍 App info:"
    ls -la dist/KeyBridge.app
    echo ""
    echo "📦 Bundle size:"
    du -sh dist/KeyBridge.app
else
    echo "❌ Build failed!"
    exit 1
fi