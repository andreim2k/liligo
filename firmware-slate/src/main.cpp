/*
 * KeyBridge Slate: LilyGo T5-4.7 S3 E-Paper Pro
 * USB keyboard/mouse bridge for a corporate laptop, fed by the Mac over WiFi.
 */
#include "app.h"
#include <esp_system.h>

RTC_NOINIT_ATTR static char crumbs[256];
RTC_NOINIT_ATTR static uint32_t crumbMagic;
char prevCrumbs[256];
int prevResetReason = 0;

void crumb(const char *what) {
    size_t n = strnlen(crumbs, sizeof crumbs - 1);
    snprintf(crumbs + n, sizeof crumbs - n, "%s>", what);
}

#ifdef SLATE_DEBUG
static void debugTask(void *) {
    for (;;) {
        LOG("prev reset=%d crumbs=%s | now=%s\n", prevResetReason, prevCrumbs, crumbs);
        vTaskDelay(pdMS_TO_TICKS(1500));
    }
}
#endif

void setup() {
    prevResetReason = esp_reset_reason();
    if (crumbMagic == 0x51A7E) strlcpy(prevCrumbs, crumbs, sizeof prevCrumbs);
    crumbMagic = 0x51A7E;
    crumbs[0] = 0;
    hidBegin();      // USB first so the laptop sees the keyboard quickly
    crumb("hid");
    storeBegin();
    statsRollDay();
    crumb("store");
    hwBegin();
    crumb("hw");
#ifdef SLATE_DEBUG
    xTaskCreatePinnedToCore(debugTask, "dbg", 3072, nullptr, 1, nullptr, 0);
#endif
    uiBegin();
    crumb("ui");
    linkBegin();
    crumb("link");
}

void loop() {
    vTaskDelay(pdMS_TO_TICKS(1000));
}
