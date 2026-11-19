#include "app.h"
#include <USB.h>
#include <USBHIDKeyboard.h>
#include <USBHIDMouse.h>
#include <esp_heap_caps.h>
#include "tusb.h"

USBHIDKeyboard Keyboard;
USBHIDMouse Mouse;
#ifdef SLATE_DEBUG
USBCDC DebugSerial;
#endif

std::atomic<Phase> phase{Phase::Idle};
std::atomic<uint32_t> pasteTotal{0}, pasteTyped{0};
std::atomic<uint32_t> sentChars{0}, sentMs{0}, sentAt{0};
std::atomic<bool> usbMounted{false}, usbSuspended{false};
std::atomic<uint32_t> nextMoveAt{0}, lastMoveAt{0}, lastActivityAt{0}, moverArmedUntil{0};
uint32_t bootMs = 0;

static const size_t QSIZE = 256 * 1024;
static char *q = nullptr;
static std::atomic<size_t> qHead{0}, qTail{0};   // tail = write, head = read
static std::atomic<bool> stopReq{false};
static SemaphoreHandle_t enqLock;
static uint32_t sessionStart = 0;

static const uint32_t ARM_DELAY_MS = 5000;
static const uint32_t SENT_SHOW_MS = 5000;

size_t hidQueued() {
    size_t h = qHead.load(), t = qTail.load();
    return (t >= h) ? t - h : QSIZE - h + t;
}
size_t hidFree() { return QSIZE - 1 - hidQueued(); }

size_t hidEnqueue(const char *data, size_t len, bool startSession, uint32_t total) {
    xSemaphoreTake(enqLock, portMAX_DELAY);
    size_t t = qTail.load(), added = 0, room = hidFree();
    for (size_t i = 0; i < len && added < room; i++) {
        uint8_t c = (uint8_t)data[i];
        if (c == '\r') continue;
        if (c >= 0x80) continue;  // client converts Unicode to ASCII first
        if (c != '\n' && c != '\t' && (c < 0x20 || c > 0x7E)) continue;
        q[t] = (char)c;
        t = (t + 1) % QSIZE;
        added++;
    }
    if (startSession) {
        pasteTyped = 0;
        pasteTotal = total ? total : (uint32_t)added;
        sessionStart = millis();
        stats.sendsToday++;
    } else if (!total) {
        pasteTotal += added;
    }
    qTail.store(t);
    if (added) phase = Phase::Typing;
    xSemaphoreGive(enqLock);
    uiRequestRedraw();
    return added;
}

static std::atomic<uint32_t> abortUntil{0};
bool hidAborting() { return abortUntil.load() && (int32_t)(abortUntil.load() - millis()) > 0; }
void hidClearAbort() { abortUntil = 0; }

void hidStop() {
    abortUntil = millis() + 1200;   // the Mac is told to abort; leftovers are dropped meanwhile
    stopReq = true;
}

static uint32_t typeFails = 0, typeWaits = 0, typeWaitMs = 0;

// The USB HID layer silently drops a report when the previous one has not completed
// ("not ready"), and the e-paper drive threads (highest priority, both cores) can stall the
// USB task for hundreds of ms. So never send before the endpoint is ready.
static bool hidWait(uint32_t timeoutMs) {
    if (tud_hid_n_ready(0)) return true;
    uint32_t t0 = millis();
    typeWaits++;
    while (!tud_hid_n_ready(0)) {
        if (millis() - t0 > timeoutMs) { typeWaitMs += millis() - t0; return false; }
        delay(1);
    }
    typeWaitMs += millis() - t0;
    return true;
}

static void typeChar(char c) {
    uint8_t hold = 2, gap = 1, interval = 4;
    switch (settings.speed) {
    case SPEED_SLOW: interval = 8; hold = 5; gap = 3; break;
    case SPEED_FAST: interval = 2; hold = 2; gap = 0; break;
    default: break;
    }
    if (!hidWait(500)) typeFails++;
    Keyboard.press(c);
    if (hold) delay(hold);
    if (!hidWait(500)) typeFails++;
    Keyboard.releaseAll();
    if (gap) delay(gap);
    if (interval > hold + gap) delay(interval - hold - gap);
}

void hidCapture(bool window) {
    Keyboard.press(window ? KEY_LEFT_ALT : KEY_LEFT_GUI);
    Keyboard.pressRaw(0x46);  // HID usage: Print Screen
    delay(30);
    Keyboard.releaseAll();
    lastActivityAt = millis();
    moverArmedUntil = millis() + ARM_DELAY_MS;
}

void hidRawKey(uint8_t mods, uint8_t code) {
    if (mods & 0x11) Keyboard.press(KEY_LEFT_CTRL);
    if (mods & 0x22) Keyboard.press(KEY_LEFT_SHIFT);
    if (mods & 0x44) Keyboard.press(KEY_LEFT_ALT);
    if (mods & 0x88) Keyboard.press(KEY_LEFT_GUI);
    if (code) Keyboard.pressRaw(code);
    delay(20);
    Keyboard.releaseAll();
    lastActivityAt = millis();
}

bool hidTypeSnippet(int i) {
    String s = storeGetSnippet(i);
    if (!s.length()) return false;
    hidEnqueue(s.c_str(), s.length(), true, 0);
    return true;
}

// ---------- mouse mover ----------
uint32_t moverIntervalMs() {
    switch (settings.interval) {
    case INT_10S: return 10000;
    case INT_30S: return 30000;
    case INT_60S: return 60000;
    default: return random(1000, 30001);
    }
}

void moverResetSchedule() { nextMoveAt = millis() + moverIntervalMs(); }

void moverStartNow() {
    moverArmedUntil = 0;
    nextMoveAt = millis() + 300;
    uiRequestRedraw();
}

MoverState moverState() {
    if (!settings.moverOn) return MoverState::Paused;
    if (phase.load() == Phase::Typing || millis() < moverArmedUntil.load()) return MoverState::Armed;
    return MoverState::Active;
}

struct Step { int8_t dx, dy, wheel; };
static const Step S_ONEPX[] = {{1, 0, 0}, {-1, 0, 0}};
static const Step S_CIRCLE[] = {{3, 1, 0}, {1, 3, 0}, {-1, 3, 0}, {-3, 1, 0}, {-3, -1, 0}, {-1, -3, 0}, {1, -3, 0}, {3, -1, 0}};
static const Step S_SQUARE[] = {{4, 0, 0}, {0, 4, 0}, {-4, 0, 0}, {0, -4, 0}};
static const Step S_FIG8[] = {{3, 1, 0}, {1, 3, 0}, {-1, 3, 0}, {-3, 1, 0}, {-3, -1, 0}, {-1, -3, 0}, {1, -3, 0}, {3, -1, 0},
                              {-3, 1, 0}, {-1, 3, 0}, {1, 3, 0}, {3, 1, 0}, {3, -1, 0}, {1, -3, 0}, {-1, -3, 0}, {-3, -1, 0}};
static const Step S_ZIGZAG[] = {{2, 2, 0}, {2, -2, 0}, {-2, 2, 0}, {-2, -2, 0}};
static const Step S_SCROLL[] = {{0, 0, 1}, {0, 0, -1}};

static uint32_t runSteps(const Step *s, size_t n) {
    uint32_t dist = 0;
    for (size_t i = 0; i < n; i++) {
        Mouse.move(s[i].dx, s[i].dy, s[i].wheel);
        dist += abs(s[i].dx) + abs(s[i].dy);
        delay(15);
    }
    return dist;
}

static void moverMove() {
    uint32_t dist = 0;
    switch (settings.pattern) {
    case PAT_CIRCLE: dist = runSteps(S_CIRCLE, 8); break;
    case PAT_SQUARE: dist = runSteps(S_SQUARE, 4); break;
    case PAT_FIG8: dist = runSteps(S_FIG8, 16); break;
    case PAT_ZIGZAG: dist = runSteps(S_ZIGZAG, 4); break;
    case PAT_SCROLL: runSteps(S_SCROLL, 2); break;
    case PAT_RANDOM: {
        // Visible wander: out 20..60 px in a random direction over 6 steps, then back.
        float ang = random(0, 628) / 100.0f, len = random(20, 61) / 6.0f;
        int8_t dx = (int8_t)lroundf(cosf(ang) * len), dy = (int8_t)lroundf(sinf(ang) * len);
        if (!dx && !dy) dx = 8;
        for (int i = 0; i < 6; i++) { Mouse.move(dx, dy, 0); delay(40); }
        delay(250);                                          // linger so it can be seen
        for (int i = 0; i < 6; i++) { Mouse.move(-dx, -dy, 0); delay(40); }
        dist = (uint32_t)(abs(dx) + abs(dy)) * 12;
        break;
    }
    case PAT_TAP:
        Keyboard.pressRaw(0x6A);  // F15: no default action on Windows
        delay(20);
        Keyboard.releaseAll();
        break;
    default: dist = runSteps(S_ONEPX, 2); break;
    }
    LOG("mover: pattern %d dist %u\n", (int)settings.pattern, (unsigned)dist);
    stats.movesToday++;
    stats.moveDistPx += dist;
    lastMoveAt = millis();
}

// ---------- USB events ----------
static void usbEvent(void *, esp_event_base_t base, int32_t id, void *) {
    if (base != ARDUINO_USB_EVENTS) return;
    switch (id) {
    case ARDUINO_USB_STARTED_EVENT: usbMounted = true; usbSuspended = false; break;
    case ARDUINO_USB_STOPPED_EVENT: usbMounted = false; break;
    case ARDUINO_USB_SUSPEND_EVENT: usbSuspended = true; break;
    case ARDUINO_USB_RESUME_EVENT: usbSuspended = false; break;
    }
    uiRequestRedraw();
}

// ---------- typing task ----------
static void typingTask(void *) {
    uint32_t lastStatsSave = 0;
    for (;;) {
        uint32_t now = millis();
        if (stopReq.load()) {
            xSemaphoreTake(enqLock, portMAX_DELAY);
            qHead.store(qTail.load());
            xSemaphoreGive(enqLock);
            Keyboard.releaseAll();
            stopReq = false;
            phase = Phase::Idle;
            pasteTotal = 0;
            pasteTyped = 0;
            moverArmedUntil = now + ARM_DELAY_MS;
            uiRequestRedraw();
        }
        size_t h = qHead.load();
        if (h != qTail.load()) {
            char c = q[h];
            typeChar(c);
            qHead.store((h + 1) % QSIZE);
            pasteTyped++;
            stats.typedToday++;
            lastActivityAt = millis();
            if (qHead.load() == qTail.load()) {
                sentChars = pasteTyped.load();
                sentMs = millis() - sessionStart;
                sentAt = millis();
                LOG("send done: %u chars in %u ms, usb-not-ready waits %u (%u ms), gave up %u\n", (unsigned)sentChars.load(), (unsigned)sentMs.load(), (unsigned)typeWaits, (unsigned)typeWaitMs, (unsigned)typeFails);
                typeFails = typeWaits = typeWaitMs = 0;
                phase = Phase::Sent;
                moverArmedUntil = millis() + ARM_DELAY_MS;
                storeSaveStats();
                uiRequestRedraw();
            }
            continue;
        }
        if (phase.load() == Phase::Sent && now - sentAt.load() > SENT_SHOW_MS) {
            phase = Phase::Idle;
            uiRequestRedraw();
        }
        if (usbMounted.load() && !usbSuspended.load() && moverState() == MoverState::Active &&
            (int32_t)(now - nextMoveAt.load()) >= 0) {
            moverMove();
            moverResetSchedule();
        }
        if (now - lastStatsSave > 300000) {
            lastStatsSave = now;
            statsRollDay();
            storeSaveStats();
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

void hidBegin() {
    q = (char *)heap_caps_malloc(QSIZE, MALLOC_CAP_SPIRAM);
    enqLock = xSemaphoreCreateMutex();
    // Identity must be set at runtime: the esp32s3 variant overrides the build flags.
    USB.VID(0x046D);
    USB.PID(0xC31C);
    USB.manufacturerName("Logitech");
    USB.productName("USB Keyboard");
    USB.onEvent(usbEvent);
    Keyboard.begin();
    Mouse.begin();
#ifdef SLATE_DEBUG
    DebugSerial.setTxTimeoutMs(2000);  // block instead of dropping bytes during frame dumps
    DebugSerial.begin();
#endif
    USB.begin();
    bootMs = millis();
    moverResetSchedule();
    xTaskCreatePinnedToCore(typingTask, "typing", 6144, nullptr, 5, nullptr, 1);
}
