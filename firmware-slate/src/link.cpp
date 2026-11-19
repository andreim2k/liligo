// Bluetooth link to the Mac app: same service, UUIDs and protocol as the
// KeyBridge dongle, so the existing menubar app works unchanged.
#include "app.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLE2902.h>
#include <ArduinoJson.h>

#define SERVICE_UUID      "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define CHAR_TEXT_UUID    "beb5483e-36e1-4688-b7f5-ea07361b26a8"
#define CHAR_HID_UUID     "beb5483e-36e1-4688-b7f5-ea07361b26a9"
#define CHAR_STATUS_UUID  "beb5483e-36e1-4688-b7f5-ea07361b26aa"
#define CHAR_CONFIG_UUID  "beb5483e-36e1-4688-b7f5-ea07361b26ab"
#define CHAR_TOTAL_UUID   "beb5483e-36e1-4688-b7f5-ea07361b26ac"
#define CHAR_SNIPPET_UUID "beb5483e-36e1-4688-b7f5-ea07361b26ad"

std::atomic<bool> macConnected{false};
bool macOnline() { return macConnected.load(); }

static BLECharacteristic *statusChar = nullptr;
static std::atomic<bool> nextIsSessionStart{true};
static std::atomic<uint32_t> nextTotal{0};
static String snippetBuf;

class ServerCb : public BLEServerCallbacks {
    void onConnect(BLEServer *) override {
        macConnected = true;
        nextIsSessionStart = true;
        uiRequestRedraw();
    }
    void onDisconnect(BLEServer *) override {
        macConnected = false;
        uiRequestRedraw();
        BLEDevice::startAdvertising();
    }
};

class TextCb : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *c) override {
        std::string v = c->getValue();
        if (v.empty()) return;
        if (settings.hold) { stats.refusedToday++; uiRequestRedraw(); return; }
        bool start = nextIsSessionStart.exchange(false);
        hidEnqueue(v.data(), v.size(), start, start ? nextTotal.load() : 0);
    }
};

class HidCb : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *c) override {
        std::string v = c->getValue();
        if (v.size() >= 2) hidRawKey((uint8_t)v[0], (uint8_t)v[1]);
    }
};

class ConfigCb : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *c) override {
        std::string v = c->getValue();
        if (v.empty()) return;
        if (v.size() == 5 && (uint8_t)v[0] == 0xFE) {        // time sync: 0xFE + epoch (u32 LE), sent by the Mac on connect
            const uint8_t *q = (const uint8_t *)v.data() + 1;
            uint32_t epoch = q[0] | (q[1] << 8) | (q[2] << 16) | ((uint32_t)q[3] << 24);
            if (epoch > 1700000000UL) {
                struct timeval tv = {(time_t)epoch, 0};
                settimeofday(&tv, nullptr);
                rtcWriteFromSystem();
                uiRequestRedraw();
            }
            return;
        }
        uint8_t p = (uint8_t)v[0];
        settings.speed = p <= SPEED_FAST ? p : SPEED_FAST;
        storeSaveSettings();
        uiRequestRedraw();
    }
};

class TotalCb : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *c) override {
        std::string v = c->getValue();
        if (v.size() < 4) return;
        const uint8_t *p = (const uint8_t *)v.data();
        nextTotal = (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
        nextIsSessionStart = true;
    }
};

// Snippets: 'B' begins, 'D'+bytes appends, 'C' commits a JSON array of {name,text}.
class SnippetCb : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *c) override {
        std::string v = c->getValue();
        if (v.empty()) return;
        if (v[0] == 'B') snippetBuf = "";
        else if (v[0] == 'D') snippetBuf.concat(v.data() + 1, v.size() - 1);
        else if (v[0] == 'C') {
            JsonDocument d;
            if (!deserializeJson(d, snippetBuf) && d.is<JsonArray>()) {
                int i = 0;
                for (JsonObject o : d.as<JsonArray>()) {
                    if (i >= SNIPPET_COUNT) break;
                    storeSetSnippet(i++, o["name"] | "", o["text"] | "");
                }
                for (; i < SNIPPET_COUNT; i++) storeSetSnippet(i, "", "");
                uiRequestRedraw();
            }
            snippetBuf = "";
        } else if (v[0] == 'T' && v.size() >= 2) {
            if (!settings.hold) hidTypeSnippet(v[1] - '0');
        }
    }
};

static void linkTask(void *) {
    uint32_t lastFree = 0, lastNotify = 0;
    for (;;) {
        uint32_t f = hidFree();
        if (macConnected && statusChar && millis() - lastNotify > 25 &&
            (f > lastFree ? f - lastFree : lastFree - f) >= 32) {
            statusChar->setValue((uint8_t *)&f, 4);
            statusChar->notify();
            lastFree = f;
            lastNotify = millis();
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

void linkBegin() {
    BLEDevice::init("KeyBridge");
    BLEDevice::setMTU(517);
    BLEServer *server = BLEDevice::createServer();
    server->setCallbacks(new ServerCb());
    BLEService *svc = server->createService(BLEUUID(SERVICE_UUID), 30);
    auto mk = [&](const char *uuid, uint32_t props, BLECharacteristicCallbacks *cb) {
        BLECharacteristic *ch = svc->createCharacteristic(uuid, props);
        if (cb) ch->setCallbacks(cb);
        return ch;
    };
    const uint32_t W = BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR;
    mk(CHAR_TEXT_UUID, W, new TextCb());
    mk(CHAR_HID_UUID, W, new HidCb());
    statusChar = mk(CHAR_STATUS_UUID, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY, nullptr);
    statusChar->addDescriptor(new BLE2902());
    uint32_t f = hidFree();
    statusChar->setValue((uint8_t *)&f, 4);
    mk(CHAR_CONFIG_UUID, W, new ConfigCb());
    mk(CHAR_TOTAL_UUID, W, new TotalCb());
    mk(CHAR_SNIPPET_UUID, W, new SnippetCb());
    svc->start();
    BLEAdvertising *adv = BLEDevice::getAdvertising();
    adv->addServiceUUID(SERVICE_UUID);
    adv->setScanResponse(true);
    BLEDevice::startAdvertising();
    xTaskCreatePinnedToCore(linkTask, "link", 4096, nullptr, 2, nullptr, 0);
}
