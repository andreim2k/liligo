/**
 * LilyGo T-Dongle-S3 KeyBridge + Mouse Mover
 *
 * Dual-mode firmware:
 * - Default: Auto mouse mover (keeps computer active)
 * - BLE connected: Keyboard bridge (receives text/keystrokes via BLE)
 *
 * Features LCD display and APA102 LED for status feedback.
 */

#include <Arduino.h>
#include <APA102.h>
#include <LovyanGFX.hpp>
#include <USB.h>
#include <USBHIDKeyboard.h>
#include <USBHIDMouse.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// Display setup using LovyanGFX for T-Dongle-S3
class LGFX : public lgfx::LGFX_Device {
    lgfx::Panel_ST7735S _panel_instance;
    lgfx::Bus_SPI _bus_instance;
    lgfx::Light_PWM _light_instance;

public:
    LGFX(void) {
        {
            auto cfg = _bus_instance.config();
            cfg.spi_mode = 0;
            cfg.freq_write = 27000000;
            cfg.freq_read = 16000000;
            cfg.pin_sclk = DISPLAY_SCLK;
            cfg.pin_mosi = DISPLAY_MOSI;
            cfg.pin_miso = DISPLAY_MISO;
            cfg.pin_dc = DISPLAY_DC;
            cfg.spi_host = SPI3_HOST;
            cfg.spi_3wire = true;
            cfg.use_lock = false;
            cfg.dma_channel = SPI_DMA_CH_AUTO;
            _bus_instance.config(cfg);
            _panel_instance.setBus(&_bus_instance);
        }
        {
            auto cfg = _panel_instance.config();
            cfg.pin_cs = DISPLAY_CS;
            cfg.pin_rst = DISPLAY_RST;
            cfg.pin_busy = DISPLAY_BUSY;
            cfg.panel_width = 80;
            cfg.panel_height = 160;
            cfg.offset_rotation = 1;
            cfg.readable = true;
            cfg.invert = true;
            cfg.rgb_order = false;
            cfg.dlen_16bit = false;
            cfg.bus_shared = true;
            cfg.offset_x = 26;
            cfg.offset_y = 1;
            cfg.dummy_read_pixel = 8;
            cfg.dummy_read_bits = 1;
            cfg.memory_width = 132;
            cfg.memory_height = 160;
            _panel_instance.config(cfg);
        }
        {
            auto cfg = _light_instance.config();
            cfg.pin_bl = DISPLAY_LEDA;
            cfg.invert = true;
            cfg.freq = 12000;
            cfg.pwm_channel = 7;
            _light_instance.config(cfg);
            _panel_instance.setLight(&_light_instance);
        }
        setPanel(&_panel_instance);
    }
};

static LGFX lcd;

// APA102 LED
APA102<LED_DI_PIN, LED_CI_PIN> ledStrip;
rgb_color ledColors[1];

// USB HID Keyboard and Mouse
USBHIDKeyboard Keyboard;
USBHIDMouse Mouse;

// Operating modes
enum OperatingMode { MODE_MOUSE_MOVER, MODE_KEYBOARD_BRIDGE };
OperatingMode currentMode = MODE_MOUSE_MOVER;
OperatingMode lastDisplayMode = MODE_MOUSE_MOVER;  // Track which screen is showing
bool needsDisplayRefresh = true;
bool needsModeSwitch = false;  // Force full screen clear on mode switch

// Mouse mover timing variables
unsigned long lastMoveTime = 0;
unsigned long nextMoveDelay = 0;
unsigned long moveCount = 0;
unsigned long startTime = 0;
unsigned long pausedTimeRemaining = 0;  // Store remaining time when BLE connects

// BLE UUIDs
#define SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define CHAR_TEXT_UUID      "beb5483e-36e1-4688-b7f5-ea07361b26a8"
#define CHAR_HID_UUID       "beb5483e-36e1-4688-b7f5-ea07361b26a9"

// BLE state
BLEServer* pServer = nullptr;
BLECharacteristic* pTextCharacteristic = nullptr;
BLECharacteristic* pHidCharacteristic = nullptr;
bool deviceConnected = false;
bool oldDeviceConnected = false;

// Stats
uint32_t keyCount = 0;
String lastText = "";

// Text queue for async processing (prevents BLE callback blocking)
// Use circular buffer instead of String to avoid heap fragmentation
const size_t MAX_QUEUE_SIZE = 4096;  // 4KB buffer
char textQueueBuffer[MAX_QUEUE_SIZE];
size_t queueStart = 0;
size_t queueEnd = 0;
unsigned long lastCharTime = 0;
const unsigned long CHAR_INTERVAL = 2;  // 2ms between chars for reliable typing

// HID modifier bits
#define MOD_LCTRL   0x01
#define MOD_LSHIFT  0x02
#define MOD_LALT    0x04
#define MOD_LGUI    0x08
#define MOD_RCTRL   0x10
#define MOD_RSHIFT  0x20
#define MOD_RALT    0x40
#define MOD_RGUI    0x80

// Colors
#define COLOR_BG      0x0841    // Dark background
#define COLOR_PANEL   0x2124    // Panel background
#define COLOR_ACCENT  0x05FF    // Cyan
#define COLOR_SUCCESS 0x07E0    // Green
#define COLOR_WARNING 0xFD20    // Orange
#define COLOR_DANGER  0xF800    // Red
#define COLOR_TEXT    0xFFFF    // White
#define COLOR_DIM     0x8410    // Gray
#define COLOR_KEY     0xFFE0    // Yellow

// Set LED color
void setLed(uint8_t r, uint8_t g, uint8_t b) {
    ledColors[0] = rgb_color{r, g, b};
    ledStrip.write(ledColors, 1);
}

// Forward declarations
unsigned long getElapsedTime(unsigned long start, unsigned long current);
unsigned long getRandomDelay();
String formatTime(unsigned long seconds);
void drawMouseMoverHeader();
void drawUptimePanel(unsigned long seconds);
void drawCountdownPanel(unsigned long timeLeft, unsigned long totalTime);
void drawStatsPanel();
void drawProgressBar(int x, int y, int width, int height, float percentage, uint16_t color);
void drawRoundRect(int x, int y, int width, int height, int radius, uint16_t color);
void updateMouseMoverDisplay();
void updateKeyBridgeDisplay();

// Update KeyBridge display with status
void showKeyBridgeStatus(const char* status, uint16_t statusColor) {
    lcd.fillScreen(COLOR_BG);

    // Header
    lcd.setTextColor(COLOR_ACCENT);
    lcd.setTextSize(1);
    lcd.setCursor(5, 5);
    lcd.println("KeyBridge");

    // Divider
    lcd.drawFastHLine(0, 18, lcd.width(), COLOR_ACCENT);

    // Status
    lcd.setTextColor(statusColor);
    lcd.setCursor(5, 25);
    lcd.println(status);

    // Show queue size while typing, otherwise show key count
    size_t queue_size = (queueEnd >= queueStart) ? (queueEnd - queueStart) : (MAX_QUEUE_SIZE - queueStart + queueEnd);
    lcd.setTextColor(COLOR_TEXT);
    lcd.setCursor(5, 45);
    if (queue_size > 0) {
        lcd.printf("Q:%d K:%d", queue_size, keyCount);
    } else {
        lcd.printf("Keys: %d", keyCount);
    }
}

// Update key count display (no blinking)
void updateKeyCount() {
    lcd.setTextColor(COLOR_TEXT, COLOR_BG);
    lcd.setCursor(5, 45);

    // Show queue status while typing
    size_t queue_size = (queueEnd >= queueStart) ? (queueEnd - queueStart) : (MAX_QUEUE_SIZE - queueStart + queueEnd);
    if (queue_size > 0) {
        lcd.printf("Queue: %d  ", queue_size);
    } else {
        lcd.printf("Keys: %d  ", keyCount);
    }
}

// HID keycode to ASCII conversion for common keys
char hidToAscii(uint8_t keycode, bool shift) {
    // Letters a-z (keycodes 0x04-0x1D)
    if (keycode >= 0x04 && keycode <= 0x1D) {
        char c = 'a' + (keycode - 0x04);
        return shift ? (c - 32) : c;  // Uppercase if shift
    }
    // Numbers 1-9 (keycodes 0x1E-0x26)
    if (keycode >= 0x1E && keycode <= 0x26) {
        if (shift) {
            const char shifted[] = "!@#$%^&*(";
            return shifted[keycode - 0x1E];
        }
        return '1' + (keycode - 0x1E);
    }
    // 0 (keycode 0x27)
    if (keycode == 0x27) return shift ? ')' : '0';

    // Special characters
    switch (keycode) {
        case 0x28: return '\n';  // Enter
        case 0x2A: return '\b';  // Backspace
        case 0x2B: return '\t';  // Tab
        case 0x2C: return ' ';   // Space
        case 0x2D: return shift ? '_' : '-';
        case 0x2E: return shift ? '+' : '=';
        case 0x2F: return shift ? '{' : '[';
        case 0x30: return shift ? '}' : ']';
        case 0x31: return shift ? '|' : '\\';
        case 0x33: return shift ? ':' : ';';
        case 0x34: return shift ? '"' : '\'';
        case 0x35: return shift ? '~' : '`';
        case 0x36: return shift ? '<' : ',';
        case 0x37: return shift ? '>' : '.';
        case 0x38: return shift ? '?' : '/';
        default: return 0;
    }
}

// Map HID keycode to Arduino key constant for special keys
uint8_t hidToArduinoKey(uint8_t keycode) {
    switch (keycode) {
        // Arrow keys
        case 0x4F: return KEY_RIGHT_ARROW;
        case 0x50: return KEY_LEFT_ARROW;
        case 0x51: return KEY_DOWN_ARROW;
        case 0x52: return KEY_UP_ARROW;
        // Navigation
        case 0x49: return KEY_INSERT;
        case 0x4A: return KEY_HOME;
        case 0x4B: return KEY_PAGE_UP;
        case 0x4C: return KEY_DELETE;
        case 0x4D: return KEY_END;
        case 0x4E: return KEY_PAGE_DOWN;
        // Function keys
        case 0x3A: return KEY_F1;
        case 0x3B: return KEY_F2;
        case 0x3C: return KEY_F3;
        case 0x3D: return KEY_F4;
        case 0x3E: return KEY_F5;
        case 0x3F: return KEY_F6;
        case 0x40: return KEY_F7;
        case 0x41: return KEY_F8;
        case 0x42: return KEY_F9;
        case 0x43: return KEY_F10;
        case 0x44: return KEY_F11;
        case 0x45: return KEY_F12;
        // Special
        case 0x29: return KEY_ESC;
        case 0x39: return KEY_CAPS_LOCK;
        default: return 0;
    }
}

// Send raw HID key event
void sendHidKey(uint8_t modifiers, uint8_t keycode) {
    bool shift = (modifiers & (MOD_LSHIFT | MOD_RSHIFT)) != 0;
    bool ctrl = (modifiers & (MOD_LCTRL | MOD_RCTRL)) != 0;
    bool alt = (modifiers & (MOD_LALT | MOD_RALT)) != 0;
    bool gui = (modifiers & (MOD_LGUI | MOD_RGUI)) != 0;

    // Press modifiers
    if (ctrl) Keyboard.press(KEY_LEFT_CTRL);
    if (alt) Keyboard.press(KEY_LEFT_ALT);
    if (gui) Keyboard.press(KEY_LEFT_GUI);
    if (shift) Keyboard.press(KEY_LEFT_SHIFT);

    // Try special key first
    uint8_t specialKey = hidToArduinoKey(keycode);
    if (specialKey) {
        Keyboard.press(specialKey);
    } else {
        // Convert HID keycode to ASCII and send
        char ascii = hidToAscii(keycode, false);
        if (ascii) {
            Keyboard.press(ascii);
        }
    }

    Keyboard.releaseAll();
    keyCount++;
}

// BLE Server Callbacks
class ServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) override {
        deviceConnected = true;

        // Store remaining countdown time before switching modes
        unsigned long elapsed = getElapsedTime(lastMoveTime, millis());
        if (elapsed < nextMoveDelay) {
            pausedTimeRemaining = nextMoveDelay - elapsed;
        } else {
            pausedTimeRemaining = 0;
        }

        // Switch to keyboard bridge mode
        currentMode = MODE_KEYBOARD_BRIDGE;
        needsDisplayRefresh = true;

        setLed(0, 50, 0);  // Green
        Serial.println("BLE connected - switching to KeyBridge mode");
    }

    void onDisconnect(BLEServer* pServer) override {
        deviceConnected = false;

        // Switch back to mouse mover mode
        currentMode = MODE_MOUSE_MOVER;
        needsDisplayRefresh = true;

        // Resume countdown from where it paused
        lastMoveTime = millis();
        nextMoveDelay = pausedTimeRemaining > 0 ? pausedTimeRemaining : getRandomDelay();

        setLed(0, 50, 0);  // Green (mouse mover ready)
        Serial.println("BLE disconnected - switching to Mouse Mover mode");
    }
};

// Text characteristic callbacks
class TextCharCallbacks : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic* pCharacteristic) override {
        std::string value = pCharacteristic->getValue();
        if (value.length() == 0) return;

        // Build clean buffer, filtering out invalid UTF-8 and control chars
        String buffer = "";
        for (size_t i = 0; i < value.length(); i++) {
            uint8_t c = (uint8_t)value[i];

            // Skip UTF-8 multi-byte sequences (firmware doesn't support non-ASCII)
            if (c >= 0x80) {
                if ((c & 0xE0) == 0xC0) i += 1;
                else if ((c & 0xF0) == 0xE0) i += 2;
                else if ((c & 0xF8) == 0xF0) i += 3;
                continue;
            }

            // Skip carriage return
            if (c == '\r') continue;

            // Add valid ASCII characters
            if (c == '\n' || c == '\t' || (c >= 0x20 && c <= 0x7E)) {
                buffer += (char)c;
            }
        }

        if (buffer.length() == 0) return;

        // Queue text for async processing in main loop
        // This prevents BLE callback from blocking during long typing sessions
        lastText = buffer;

        // Add chars to circular buffer queue
        size_t chars_added = 0;
        for (size_t i = 0; i < buffer.length(); i++) {
            size_t next = (queueEnd + 1) % MAX_QUEUE_SIZE;
            if (next != queueStart) {  // Don't overwrite unprocessed chars
                textQueueBuffer[queueEnd] = buffer[i];
                queueEnd = next;
                chars_added++;
            } else {
                break;  // Buffer full
            }
        }

        // Update display
        lcd.setTextColor(COLOR_KEY, COLOR_BG);
        lcd.setCursor(5, 60);
        String display = buffer.substring(0, 12);
        lcd.print(display);
        lcd.print("          ");  // Clear rest of line

        size_t queue_size = (queueEnd >= queueStart) ? (queueEnd - queueStart) : (MAX_QUEUE_SIZE - queueStart + queueEnd);
        Serial.printf("Text queued: %d chars (queue: %d/%d)\n", chars_added, queue_size, MAX_QUEUE_SIZE);
    }
};

// HID characteristic callbacks
class HidCharCallbacks : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic* pCharacteristic) override {
        std::string value = pCharacteristic->getValue();
        if (value.length() < 2) {
            Serial.printf("Error: HID write too short (%d bytes)\n", value.length());
            return;
        }
        uint8_t modifiers = (uint8_t)value[0];
        uint8_t keycode = (uint8_t)value[1];
        sendHidKey(modifiers, keycode);
    }
};

// ============================================================================
// Mouse Mover Functions
// ============================================================================

/**
 * Safely calculates elapsed time handling millis() overflow
 */
unsigned long getElapsedTime(unsigned long start, unsigned long current) {
    if (current >= start) {
        return current - start;
    } else {
        // Handle wrap-around: millis() overflowed
        return (ULONG_MAX - start) + current + 1;
    }
}

/**
 * Get random delay between 7-60 seconds
 */
unsigned long getRandomDelay() {
    unsigned long delayMs = random(7000, 60001);
    Serial.printf("Next mouse move in %lu ms (%lu seconds)\n", delayMs, delayMs / 1000);
    return delayMs;
}

/**
 * Format seconds into HH:MM:SS string
 */
String formatTime(unsigned long seconds) {
    unsigned long hours = seconds / 3600;
    unsigned long minutes = (seconds % 3600) / 60;
    unsigned long secs = seconds % 60;

    char buffer[12];
    sprintf(buffer, "%02lu:%02lu:%02lu", hours, minutes, secs);
    return String(buffer);
}

/**
 * Move mouse 1 pixel right then left
 */
void moveMouse() {
    if (currentMode != MODE_MOUSE_MOVER) return;

    Serial.printf("Moving mouse (count: %lu)\n", moveCount + 1);

    // Flash LED purple when moving
    setLed(128, 0, 255);

    // Move mouse 1 pixel right, then back left
    Mouse.move(1, 0);
    delay(20);
    Mouse.move(-1, 0);
    delay(20);

    // Return LED to green
    setLed(0, 50, 0);

    moveCount++;
}

/**
 * Draw mouse mover header with status indicator
 */
void drawMouseMoverHeader() {
    int dispWidth = lcd.width();

    // Gradient header background
    for (int y = 0; y < 16; y++) {
        uint16_t color = lcd.color565(0, 40 + y * 2, 60 + y * 3);
        lcd.drawFastHLine(0, y, dispWidth, color);
    }

    // Header border
    lcd.drawFastHLine(0, 15, dispWidth, COLOR_ACCENT);

    // Title with play icon
    lcd.setTextSize(1);
    lcd.setTextColor(COLOR_TEXT);
    lcd.setCursor(4, 4);
    lcd.print((char)0x10);  // Triangle icon
    lcd.print(" AUTO MOUSE MOVER");

    // Status indicator (top right)
    int statusX = dispWidth - 10;
    lcd.fillCircle(statusX, 8, 3, COLOR_SUCCESS);
    lcd.drawCircle(statusX, 8, 4, COLOR_TEXT);
}

/**
 * Draw uptime panel showing running time
 */
void drawUptimePanel(unsigned long seconds) {
    int panelY = 18;
    int panelH = 16;
    int dispWidth = lcd.width();

    // Clear panel area
    lcd.fillRect(2, panelY, dispWidth - 4, panelH, COLOR_PANEL);

    // Panel border
    drawRoundRect(2, panelY, dispWidth - 4, panelH, 2, COLOR_ACCENT);

    // Clock icon and label
    lcd.setTextSize(1);
    lcd.setTextColor(COLOR_ACCENT);
    lcd.setCursor(5, panelY + 4);
    lcd.print((char)0x0F);  // Clock symbol

    lcd.setTextColor(COLOR_DIM);
    lcd.setCursor(15, panelY + 4);
    lcd.print("UP:");

    // Time value
    lcd.setTextColor(COLOR_TEXT);
    lcd.setCursor(35, panelY + 4);
    lcd.print(formatTime(seconds));
}

// Animation variables for mouse mover display
static uint8_t pulsePhase = 0;
static bool justMoved = false;
static unsigned long moveAnimationStart = 0;

/**
 * Draw countdown panel with progress bar
 */
void drawCountdownPanel(unsigned long timeLeft, unsigned long totalTime) {
    int panelY = 36;
    int panelH = 30;
    int dispWidth = lcd.width();

    // Clear panel area
    lcd.fillRect(2, panelY, dispWidth - 4, panelH, COLOR_PANEL);

    // Determine color based on time left
    uint16_t accentColor = COLOR_SUCCESS;
    if (timeLeft < 5)
        accentColor = COLOR_DANGER;
    else if (timeLeft < 15)
        accentColor = COLOR_WARNING;

    // Panel border with pulsing effect when near zero
    if (timeLeft < 5 && (pulsePhase % 64) < 32) {
        drawRoundRect(2, panelY, dispWidth - 4, panelH, 3, accentColor);
        drawRoundRect(3, panelY + 1, dispWidth - 6, panelH - 2, 3, accentColor);
    } else {
        drawRoundRect(2, panelY, dispWidth - 4, panelH, 3, accentColor);
    }

    // Calculate total width for centering
    char timeStr[12];
    sprintf(timeStr, "%lu", timeLeft);

    int labelWidth = 8 * 6;  // "NEXT IN:"
    int spaceWidth = 4;
    int numberWidth = strlen(timeStr) * 12;
    int suffixWidth = 6;
    int totalWidth = labelWidth + spaceWidth + numberWidth + suffixWidth;

    int startX = (dispWidth - totalWidth) / 2;
    int textY = panelY + 10;

    // Draw "NEXT IN:" label
    lcd.setTextSize(1);
    lcd.setTextColor(COLOR_DIM);
    lcd.setCursor(startX, textY);
    lcd.print("NEXT IN:");

    // Draw countdown number (larger)
    lcd.setTextSize(2);
    lcd.setTextColor(accentColor);
    lcd.setCursor(startX + labelWidth + spaceWidth, textY - 2);
    lcd.print(timeLeft);

    // Draw "s" suffix
    lcd.setTextSize(1);
    lcd.setTextColor(COLOR_DIM);
    lcd.setCursor(startX + labelWidth + spaceWidth + numberWidth, textY);
    lcd.print("s");

    // Progress bar at bottom of panel
    float percentage = 1.0 - ((float)timeLeft / (float)totalTime);
    if (percentage < 0) percentage = 0;
    if (percentage > 1) percentage = 1;

    drawProgressBar(6, panelY + panelH - 6, dispWidth - 12, 3, percentage, accentColor);
}

/**
 * Draw stats panel showing total move count
 */
void drawStatsPanel() {
    int panelY = 68;
    int panelH = 11;
    int dispWidth = lcd.width();

    // Clear panel area
    lcd.fillRect(2, panelY, dispWidth - 4, panelH, COLOR_PANEL);

    // Panel border
    lcd.drawRect(2, panelY, dispWidth - 4, panelH, COLOR_ACCENT);

    // Checkmark icon
    lcd.setTextSize(1);
    lcd.setTextColor(COLOR_SUCCESS);
    lcd.setCursor(5, panelY + 2);
    lcd.print((char)0xFB);  // Check mark

    // Label
    lcd.setTextColor(COLOR_DIM);
    lcd.setCursor(15, panelY + 2);
    lcd.print("TOTAL:");

    // Count value
    lcd.setTextColor(COLOR_TEXT);
    lcd.setCursor(55, panelY + 2);
    lcd.print(moveCount);

    // Animation flash on new move
    if (justMoved) {
        lcd.drawRect(1, panelY - 1, dispWidth - 2, panelH + 2, COLOR_SUCCESS);
    }
}

/**
 * Draw a progress bar
 */
void drawProgressBar(int x, int y, int width, int height, float percentage, uint16_t color) {
    // Background
    lcd.fillRect(x, y, width, height, COLOR_BG);
    lcd.drawRect(x, y, width, height, COLOR_DIM);

    // Filled portion
    int fillWidth = (int)((width - 2) * percentage);
    if (fillWidth > 0) {
        lcd.fillRect(x + 1, y + 1, fillWidth, height - 2, color);
    }
}

/**
 * Draw a rounded rectangle border
 */
void drawRoundRect(int x, int y, int width, int height, int radius, uint16_t color) {
    lcd.drawRect(x + radius, y, width - 2 * radius, height, color);
    lcd.drawRect(x, y + radius, width, height - 2 * radius, color);

    // Corner pixels
    lcd.drawPixel(x + radius, y, color);
    lcd.drawPixel(x + width - radius - 1, y, color);
    lcd.drawPixel(x + radius, y + height - 1, color);
    lcd.drawPixel(x + width - radius - 1, y + height - 1, color);
}

/**
 * Update mouse mover display
 */
void updateMouseMoverDisplay() {
    static unsigned long lastUptimeSeconds = 0;
    static unsigned long lastTimeUntilMove = 9999;
    static unsigned long lastMoveCount = 0;
    static bool needsFullRedraw = true;

    unsigned long currentTime = millis();

    // Use safe elapsed time calculation
    unsigned long uptimeSeconds = getElapsedTime(startTime, currentTime) / 1000;
    unsigned long timeUntilMove = 0;

    unsigned long elapsedSinceLastMove = getElapsedTime(lastMoveTime, currentTime);
    if (elapsedSinceLastMove < nextMoveDelay) {
        timeUntilMove = (nextMoveDelay - elapsedSinceLastMove) / 1000;
    }

    // Full redraw on first run or after mode switch
    if (needsFullRedraw || needsDisplayRefresh) {
        lcd.fillScreen(COLOR_BG);
        drawMouseMoverHeader();
        needsFullRedraw = false;
        needsDisplayRefresh = false;
        lastUptimeSeconds = uptimeSeconds;
        lastTimeUntilMove = timeUntilMove;
        lastMoveCount = moveCount;
    }

    // Update uptime panel
    if (uptimeSeconds != lastUptimeSeconds) {
        drawUptimePanel(uptimeSeconds);
        lastUptimeSeconds = uptimeSeconds;
    }

    // Update countdown panel
    if (timeUntilMove != lastTimeUntilMove) {
        drawCountdownPanel(timeUntilMove, nextMoveDelay / 1000);
        lastTimeUntilMove = timeUntilMove;
    }

    // Update stats panel
    if (moveCount != lastMoveCount) {
        drawStatsPanel();
        lastMoveCount = moveCount;
    }
}

/**
 * Update KeyBridge display
 */
void updateKeyBridgeDisplay() {
    static bool needsFullRedraw = true;
    static uint32_t lastKeyCount = 0;

    if (needsFullRedraw || needsDisplayRefresh) {
        showKeyBridgeStatus("Connected", COLOR_SUCCESS);
        needsFullRedraw = false;
        needsDisplayRefresh = false;
        lastKeyCount = keyCount;
    }

    // Update key count if changed
    if (keyCount != lastKeyCount) {
        updateKeyCount();
        lastKeyCount = keyCount;
    }
}

/**
 * Main display update dispatcher
 */
void updateDisplay() {
    // If we're typing (queue has chars), ALWAYS show keyboard screen
    bool isTyping = (queueStart != queueEnd);
    OperatingMode displayMode = (isTyping || currentMode == MODE_KEYBOARD_BRIDGE) ? MODE_KEYBOARD_BRIDGE : MODE_MOUSE_MOVER;

    // Detect mode switch and force full screen clear
    if (displayMode != lastDisplayMode) {
        lcd.fillScreen(COLOR_BG);  // Complete screen clear
        needsModeSwitch = true;
        lastDisplayMode = displayMode;
        needsDisplayRefresh = true;
    }

    if (displayMode == MODE_KEYBOARD_BRIDGE) {
        // Show keyboard screen while typing or connected
        updateKeyBridgeDisplay();
    } else {
        // Show mouse mover screen when idle and disconnected
        updateMouseMoverDisplay();
    }
}

// ============================================================================
// Setup and Loop
// ============================================================================

void setup() {
    Serial.begin(115200);
    Serial.println("KeyBridge + Mouse Mover starting...");

    // Initialize random seed
    randomSeed(analogRead(0));

    // Initialize LED - blue during startup
    setLed(0, 0, 50);

    // Initialize display
    lcd.init();
    lcd.setBrightness(255);
    lcd.fillScreen(COLOR_BG);

    // Splash screen
    lcd.setTextColor(COLOR_ACCENT);
    lcd.setTextSize(1);
    lcd.setCursor(5, 25);
    lcd.println("KeyBridge");
    lcd.setCursor(5, 35);
    lcd.println("+ Mouse Mover");
    lcd.setTextColor(COLOR_DIM);
    lcd.setCursor(5, 55);
    lcd.println("Starting...");

    // Initialize USB HID (Keyboard + Mouse)
    Keyboard.begin();
    Mouse.begin();
    USB.begin();
    delay(2000);  // Give USB time to enumerate

    // Initialize BLE with large MTU for fast transfers
    BLEDevice::init("KeyBridge");
    BLEDevice::setMTU(517);  // Max BLE 5.0 MTU
    pServer = BLEDevice::createServer();
    pServer->setCallbacks(new ServerCallbacks());

    BLEService* pService = pServer->createService(SERVICE_UUID);

    pTextCharacteristic = pService->createCharacteristic(
        CHAR_TEXT_UUID,
        BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
    );
    pTextCharacteristic->setCallbacks(new TextCharCallbacks());

    pHidCharacteristic = pService->createCharacteristic(
        CHAR_HID_UUID,
        BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
    );
    pHidCharacteristic->setCallbacks(new HidCharCallbacks());

    pService->start();

    BLEAdvertising* pAdvertising = BLEDevice::getAdvertising();
    pAdvertising->addServiceUUID(SERVICE_UUID);
    pAdvertising->setScanResponse(true);
    pAdvertising->setMinPreferred(0x06);
    pAdvertising->setMinPreferred(0x12);
    BLEDevice::startAdvertising();

    // Initialize mouse mover timing
    startTime = millis();
    lastMoveTime = millis();
    nextMoveDelay = getRandomDelay();

    // Start in mouse mover mode with green LED
    setLed(0, 50, 0);
    needsDisplayRefresh = true;

    Serial.println("Ready! Default mode: Mouse Mover");
    Serial.println("Connect via BLE to switch to KeyBridge mode");
}

void loop() {
    unsigned long currentTime = millis();

    // Handle BLE reconnection
    if (!deviceConnected && oldDeviceConnected) {
        delay(500);
        BLEDevice::startAdvertising();
        oldDeviceConnected = deviceConnected;
    }

    if (deviceConnected && !oldDeviceConnected) {
        oldDeviceConnected = deviceConnected;
    }

    // Process queued text asynchronously (character by character with delays)
    // This keeps BLE responsive while typing
    if (queueStart != queueEnd && getElapsedTime(lastCharTime, currentTime) >= CHAR_INTERVAL) {
        char c = textQueueBuffer[queueStart];
        queueStart = (queueStart + 1) % MAX_QUEUE_SIZE;

        Keyboard.press(c);
        Keyboard.releaseAll();
        keyCount++;
        lastCharTime = currentTime;

        updateKeyCount();
    }

    // Mouse mover logic (only in mouse mode and no text queued)
    if (currentMode == MODE_MOUSE_MOVER && queueStart == queueEnd) {
        if (getElapsedTime(lastMoveTime, currentTime) >= nextMoveDelay) {
            moveMouse();
            lastMoveTime = currentTime;
            nextMoveDelay = getRandomDelay();
            justMoved = true;
            moveAnimationStart = currentTime;
        }

        // Reset move animation after 500ms
        if (justMoved && getElapsedTime(moveAnimationStart, currentTime) > 500) {
            justMoved = false;
        }
    }

    // Update display every 50ms for smooth animations
    static unsigned long lastDisplayUpdate = 0;
    if (getElapsedTime(lastDisplayUpdate, currentTime) >= 50) {
        updateDisplay();
        lastDisplayUpdate = currentTime;
        pulsePhase = (pulsePhase + 1) % 255;
    }

    delay(1);  // Reduced from 10ms to allow faster character processing
}
