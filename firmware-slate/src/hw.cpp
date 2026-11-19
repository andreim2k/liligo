#include "app.h"
#include <Wire.h>
#include <TouchDrvGT911.hpp>
#include <SensorPCF8563.hpp>
#include <sys/time.h>
#include <time.h>

static TouchDrvGT911 touch;
static SensorPCF8563 rtc;
static bool touchOk = false, rtcOk = false;
static std::atomic<bool> homePending{false};

void hwBegin() {
    setenv("TZ", "EET-2EEST,M3.5.0/3,M10.5.0/4", 1);
    tzset();
    // Keep the shared SPI bus quiet: LoRa and SD chip-selects high.
    pinMode(PIN_LORA_CS, OUTPUT);
    digitalWrite(PIN_LORA_CS, HIGH);
    pinMode(PIN_SD_CS, OUTPUT);
    digitalWrite(PIN_SD_CS, HIGH);
    pinMode(PIN_BOOT_BTN, INPUT_PULLUP);
    pinMode(PIN_USER_BTN, INPUT_PULLUP);

    Wire.begin(PIN_SDA, PIN_SCL);
    for (uint8_t a = 1; a < 127; a++) {
        Wire.beginTransmission(a);
        if (Wire.endTransmission() == 0) LOG("i2c device 0x%02x\n", a);
    }

    touch.setPins(PIN_TOUCH_RST, PIN_TOUCH_INT);
    touchOk = touch.begin(Wire, GT911_SLAVE_ADDRESS_L, PIN_SDA, PIN_SCL);
    if (touchOk) {
        touch.setHomeButtonCallback([](void *) { homePending = true; }, nullptr);
        touch.setInterruptMode(LOW_LEVEL_QUERY);
        int16_t rx = 0, ry = 0;
        touch.getResolution(&rx, &ry);
        LOG("touch ok, resolution %dx%d\n", rx, ry);
    } else {
        LOG("touch NOT found\n");
    }

    rtcOk = rtc.begin(Wire, PCF8563_SLAVE_ADDRESS, PIN_SDA, PIN_SCL);
    if (rtcOk) {
        RTC_DateTime d = rtc.getDateTime();
        LOG("rtc %04u-%02u-%02u %02u:%02u:%02u UTC\n", d.year, d.month, d.day, d.hour, d.minute, d.second);
        if (d.year >= 2024 && d.year < 2100) {
            struct tm t = {};
            t.tm_year = d.year - 1900; t.tm_mon = d.month - 1; t.tm_mday = d.day;
            t.tm_hour = d.hour; t.tm_min = d.minute; t.tm_sec = d.second;
            setenv("TZ", "UTC0", 1); tzset();
            time_t epoch = mktime(&t);
            setenv("TZ", "EET-2EEST,M3.5.0/3,M10.5.0/4", 1); tzset();
            struct timeval tv = {epoch, 0};
            settimeofday(&tv, nullptr);
        }
        if (!rtcValid()) {
            // No time source without WiFi: start from the firmware build time (local).
            RTC_DateTime b(__DATE__, __TIME__);
            struct tm t = {};
            t.tm_year = b.year - 1900; t.tm_mon = b.month - 1; t.tm_mday = b.day;
            t.tm_hour = b.hour; t.tm_min = b.minute; t.tm_sec = b.second; t.tm_isdst = -1;
            struct timeval tv = {mktime(&t), 0};
            settimeofday(&tv, nullptr);
            rtcWriteFromSystem();
        }
    } else {
        LOG("rtc NOT found\n");
    }
}

bool rtcValid() { return time(nullptr) > 1700000000; }

void rtcWriteFromSystem() {
    if (!rtcOk) return;
    time_t now = time(nullptr);
    struct tm u;
    gmtime_r(&now, &u);
    rtc.setDateTime(u.tm_year + 1900, u.tm_mon + 1, u.tm_mday, u.tm_hour, u.tm_min, u.tm_sec);
}

bool touchRead(int16_t &x, int16_t &y) {
    if (!touchOk || !touch.isPressed()) return false;
    int16_t tx, ty;
    if (!touch.getPoint(&tx, &ty, 1)) return false;
    // The panel's touch frame is portrait (540 x 960); the UI is landscape.
    x = ty;
    y = SCREEN_H - 1 - tx;
    if (settings.flipped) { x = SCREEN_W - 1 - x; y = SCREEN_H - 1 - y; }
    LOG("touch raw %d,%d -> %d,%d\n", tx, ty, x, y);
    return x >= 0 && x < SCREEN_W && y >= 0 && y < SCREEN_H;
}

bool homeKeyPressed() { return homePending.exchange(false); }
