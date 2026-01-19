from setuptools import setup

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
    },
    'packages': ['rumps', 'bleak', 'asyncio'],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
