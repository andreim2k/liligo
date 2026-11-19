#!/bin/zsh
# Flash KeyBridge Slate without touching the buttons: a 1200-baud touch on the
# firmware's debug port reboots it into download mode (dev builds only).
set -e
cd "$(dirname "$0")"
ENV=${1:-slate-dev}
python3 -m platformio run -e "$ENV"
APP=$(ls /dev/cu.usbmodem* 2>/dev/null | grep -v usbmodem1101 | head -1 || true)
if [ -n "$APP" ]; then
  python3 -c "import serial,time; s=serial.Serial('$APP',1200); time.sleep(0.3); s.close()" || true
fi
for i in {1..20}; do
  system_profiler SPUSBHostDataType 2>/dev/null | grep -q "JTAG" && break
  sleep 1
done
PORT=$(ls /dev/cu.usbmodem* | head -1)
python3 -m platformio run -e "$ENV" -t upload --upload-port "$PORT"
esptool --port "$PORT" --before no-reset --after watchdog-reset chip-id >/dev/null 2>&1 || true
echo "flashed $ENV"
