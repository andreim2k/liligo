from setuptools import setup

APP = ['fixed_menubar.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': False,
    'plist': {
        'LSUIElement': True,  # Hide from Dock, show only in menu bar
        'CFBundleName': 'KeyBridge',
        'CFBundleDisplayName': 'KeyBridge',
        'CFBundleIdentifier': 'com.liligo.keybridge',
        'CFBundleVersion': '2.1.0',
        'CFBundleShortVersionString': '2.1.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.15',
    },
    'includes': [
        'bleak', 'asyncio', 'threading', 'time', 'typing', 'subprocess',
        'rumps', 'objc', 'Foundation', 'AppKit', 'PyObjCTools'
    ],
    'excludes': ['tkinter', 'test', 'unittest'],
    'site_packages': True,
    'strip': False,
    'optimize': 0,
    'iconfile': 'KeyBridge.icns',
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)