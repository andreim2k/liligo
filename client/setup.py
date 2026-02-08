from setuptools import setup

# Only external packages (built-ins and frameworks are handled via 'includes')
PACKAGES = [
    'bleak',
]

# Core dependencies
INSTALL_REQUIRES = [
    'bleak>=0.20.0',
    'pyobjc-core>=9.0',
    'pyobjc-framework-Cocoa>=9.0',
    'pyobjc-framework-Quartz>=9.0',
]

APP = ['menubar_app.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': False,
    'plist': {
        'LSUIElement': True,  # Hide from Dock, show only in menu bar
        'CFBundleName': 'KeyBridge',
        'CFBundleDisplayName': 'KeyBridge',
        'CFBundleIdentifier': 'com.liligo.keybridge',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleExecutable': 'KeyBridge',
        'CFBundlePackageType': 'APPL',
        'CFBundleSignature': '????',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.15',
        'NSRequiresAquaSystemAppearance': False,
        'NSAccessibilityUsageDescription': 'KeyBridge needs accessibility access to enable the Fn+Cmd+V global hotkey for sending clipboard content.',
        'NSInputMonitoringUsageDescription': 'KeyBridge needs input monitoring access to detect the Fn+Cmd+V keyboard shortcut.',
        'NSAppTransportSecurity': {
            'NSAllowsArbitraryLoads': True
        }
    },
    'iconfile': 'KeyBridge.icns',
    'includes': PACKAGES,
    'excludes': ['tkinter', 'test', 'unittest'],
    'site_packages': True,
    'strip': False,
    'optimize': 0,
}

setup(
    name='KeyBridge',
    version='1.0.0',
    description='LilyGo KeyBridge - Menu Bar BLE Keyboard Bridge',
    author='LilyGo',
    author_email='support@liligo.com',
    url='https://liligo.com',
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
