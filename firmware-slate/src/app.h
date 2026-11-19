#pragma once
#include <Arduino.h>
#include <atomic>

// Board pins (LilyGo T5-4.7 S3 E-Paper Pro, H752)
#define PIN_SDA        39
#define PIN_SCL        40
#define PIN_TOUCH_INT  3
#define PIN_TOUCH_RST  9
#define PIN_LORA_CS    46
#define PIN_SD_CS      12
#define PIN_BOOT_BTN   0
#define PIN_USER_BTN   48

#define SCREEN_W 960
#define SCREEN_H 540

#ifdef SLATE_DEBUG
#include <USBCDC.h>
extern USBCDC DebugSerial;
#define LOG(...) DebugSerial.printf(__VA_ARGS__)
#else
#define LOG(...)
#endif

// ---------- persisted settings ----------
enum : uint8_t { SPEED_SLOW = 0, SPEED_NORMAL = 1, SPEED_FAST = 2 };
enum : uint8_t { REFRESH_INVISIBLE = 0, REFRESH_NIGHTLY = 1, REFRESH_OFF = 2, REFRESH_5MIN = 3 };
enum : uint8_t { PAT_ONEPX = 0, PAT_CIRCLE, PAT_SQUARE, PAT_FIG8, PAT_ZIGZAG, PAT_RANDOM, PAT_SCROLL, PAT_TAP, PAT_COUNT };
// Stored values kept stable: 2 = Random (default) and 3 = 30 s, as before.
enum : uint8_t { INT_10S = 0, INT_60S = 1, INT_RANDOM = 2, INT_30S = 3, INT_COUNT = 4 };

struct Settings {
    uint8_t speed = SPEED_NORMAL;
    bool hold = false;
    bool dark = false;          // false = light theme (black on white)
    bool flipped = false;
    bool moverOn = true;
    uint8_t pattern = PAT_ONEPX;
    uint8_t interval = INT_RANDOM;
    uint8_t refresh = REFRESH_5MIN;
};
extern Settings settings;

struct Stats {
    uint32_t day = 0;           // yyyymmdd of the counters below
    uint32_t typedToday = 0;
    uint32_t sendsToday = 0;
    uint32_t refusedToday = 0;
    uint32_t movesToday = 0;
    uint32_t moveDistPx = 0;
};
extern Stats stats;

#define SNIPPET_COUNT 8
struct SnippetMeta { char name[32]; uint32_t len; };
extern SnippetMeta snippets[SNIPPET_COUNT];

void storeBegin();
void storeSaveSettings();
void storeSaveStats();
void storeLoadSnippetMeta();
bool storeSetSnippet(int i, const char *name, const char *text);
String storeGetSnippet(int i);
void statsRollDay();

// ---------- HID engine (hid.cpp) ----------
enum class Phase : uint8_t { Idle, Typing, Sent };
extern std::atomic<Phase> phase;
extern std::atomic<uint32_t> pasteTotal, pasteTyped;
extern std::atomic<uint32_t> sentChars, sentMs, sentAt;
extern std::atomic<bool> usbMounted, usbSuspended;
extern std::atomic<uint32_t> nextMoveAt, lastMoveAt, lastActivityAt, moverArmedUntil;
extern uint32_t bootMs;

void hidBegin();
size_t hidEnqueue(const char *data, size_t len, bool startSession, uint32_t total);
size_t hidFree();
size_t hidQueued();
void hidStop();
void hidCapture(bool window);
void hidRawKey(uint8_t mods, uint8_t code);
bool hidTypeSnippet(int i);
void moverResetSchedule();
void moverStartNow();
uint32_t moverIntervalMs();
enum class MoverState : uint8_t { Active, Armed, Paused };
MoverState moverState();

// ---------- Bluetooth link to the Mac (link.cpp) ----------
void linkBegin();
bool macOnline();   // Mac app currently connected

// ---------- crash breadcrumbs (survive a reboot) ----------
void crumb(const char *what);
extern char prevCrumbs[256];
extern int prevResetReason;

// ---------- hardware (hw.cpp) ----------
void hwBegin();
bool touchRead(int16_t &x, int16_t &y);   // screen coordinates
bool homeKeyPressed();                     // edge-triggered
bool rtcValid();
void rtcWriteFromSystem();

// ---------- UI (ui.cpp) ----------
void uiBegin();
void uiRequestRedraw();
void uiRequestClean(bool cascade);
