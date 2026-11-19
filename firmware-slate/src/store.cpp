#include "app.h"
#include <Preferences.h>
#include <esp_random.h>
#include <time.h>

Settings settings;
Stats stats;
SnippetMeta snippets[SNIPPET_COUNT];

static Preferences prefs;
static SemaphoreHandle_t prefsLock;

struct Lock {
    Lock() { xSemaphoreTake(prefsLock, portMAX_DELAY); }
    ~Lock() { xSemaphoreGive(prefsLock); }
};

void storeBegin() {
    prefsLock = xSemaphoreCreateMutex();
    Lock l;
    prefs.begin("slate", false);
    settings.speed = prefs.getUChar("speed", SPEED_NORMAL);
    settings.hold = prefs.getBool("hold", false);
    settings.dark = prefs.getBool("dark", false);
    settings.flipped = prefs.getBool("flip", false);
    settings.moverOn = prefs.getBool("mover", true);
    settings.pattern = prefs.getUChar("pat", PAT_ONEPX) % PAT_COUNT;
    settings.interval = prefs.getUChar("int", INT_RANDOM) % INT_COUNT;
    settings.refresh = prefs.getUChar("refr2", REFRESH_5MIN) % 4;
    prefs.getBytes("stats", &stats, sizeof(stats));
    for (int i = 0; i < SNIPPET_COUNT; i++) {
        char k[4];
        snprintf(k, sizeof k, "n%d", i);
        String n = prefs.getString(k, "");
        strlcpy(snippets[i].name, n.c_str(), sizeof snippets[i].name);
        snprintf(k, sizeof k, "s%d", i);
        snippets[i].len = prefs.isKey(k) ? prefs.getString(k, "").length() : 0;
    }
}

void storeSaveSettings() {
    Lock l;
    prefs.putUChar("speed", settings.speed);
    prefs.putBool("hold", settings.hold);
    prefs.putBool("dark", settings.dark);
    prefs.putBool("flip", settings.flipped);
    prefs.putBool("mover", settings.moverOn);
    prefs.putUChar("pat", settings.pattern);
    prefs.putUChar("int", settings.interval);
    prefs.putUChar("refr2", settings.refresh);
}

void storeSaveStats() {
    Lock l;
    prefs.putBytes("stats", &stats, sizeof(stats));
}

void statsRollDay() {
    time_t now = time(nullptr);
    if (now < 1700000000) return;  // clock not set yet
    struct tm t;
    localtime_r(&now, &t);
    uint32_t day = (t.tm_year + 1900) * 10000 + (t.tm_mon + 1) * 100 + t.tm_mday;
    if (day != stats.day) {
        stats = Stats();
        stats.day = day;
        storeSaveStats();
    }
}

bool storeSetSnippet(int i, const char *name, const char *text) {
    if (i < 0 || i >= SNIPPET_COUNT) return false;
    size_t len = strlen(text);
    if (len > 3800) return false;  // NVS string limit is ~4000 bytes
    Lock l;
    char k[4];
    snprintf(k, sizeof k, "n%d", i);
    prefs.putString(k, name);
    snprintf(k, sizeof k, "s%d", i);
    if (len) prefs.putString(k, text); else prefs.remove(k);
    strlcpy(snippets[i].name, name, sizeof snippets[i].name);
    snippets[i].len = len;
    return true;
}

String storeGetSnippet(int i) {
    if (i < 0 || i >= SNIPPET_COUNT) return String();
    Lock l;
    char k[4];
    snprintf(k, sizeof k, "s%d", i);
    return prefs.getString(k, "");
}

