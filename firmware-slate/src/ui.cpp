#include "app.h"
#include <FastEPD.h>
#include <time.h>
#include <sys/time.h>
extern "C" {
#include <epdiy.h>
#include <epd_highlevel.h>
#include <epd_internals.h>
}
#include "fonts/Roboto_Black_10.h"
#include "fonts/Roboto_Black_16.h"
#include "fonts/Roboto_Black_20.h"
#include "fonts/Roboto_Black_24.h"
#include "fonts/Roboto_Black_40.h"

// Frames are drawn offscreen with FastEPD (virtual panel, 16 greys) in the dark
// design and inverted for the light theme while copying to epdiy, which drives
// the panel with its real ED047TC1 waveforms (clean greys, no residue).
static FASTEPD epd;
static EpdiyHighlevelState hl;
static const uint8_t BG = 0, FG = 15;           // FastEPD 4-bit: 0 black .. 15 white
// Pure black and white only: greys do not hold on this panel with fast updates.
static const uint8_t G_BUTTON = FG;             // -> black buttons in the light theme
static const uint8_t G_CARD = BG;               // -> white cards in the light theme

enum Page : uint8_t { P_HOME, P_SNIPPETS, P_SPEED, P_MOVER, P_PATTERNS, P_INTERVAL, P_REFRESH, P_SETTINGS };
static Page page = P_MOVER;
static Page returnPage = P_MOVER, refreshReturn = P_MOVER;  // where Back returns to
static uint32_t rebootArmedUntil = 0;  // like the dongle: mouse mover until something is sent
static std::atomic<bool> redraw{true};
static std::atomic<int> cleanReq{0};  // 1 = light clean, 2 = cascade
static uint32_t lastTouchMs = 0, holdSince = 0, lastFullClean = 0;
static bool lastDark = false, lastFlip = false;
static int shownView = -1;
static void resetDirty();  // page on screen, or 100 for the send-progress view

void uiRequestRedraw() { redraw = true; }
void uiRequestClean(bool cascade) { cleanReq = cascade ? 2 : 1; }

// ---------------- drawing primitives ----------------
static const uint8_t BAYER[4][4] = {{0, 8, 2, 10}, {12, 4, 14, 6}, {3, 11, 1, 9}, {15, 7, 13, 5}};

static bool inRound(int px, int py, int x, int y, int w, int h, int r) {
    if (px < x || py < y || px >= x + w || py >= y + h) return false;
    int cx = px < x + r ? x + r : (px >= x + w - r ? x + w - r - 1 : px);
    int cy = py < y + r ? y + r : (py >= y + h - r ? y + h - r - 1 : py);
    int dx = px - cx, dy = py - cy;
    return dx * dx + dy * dy <= r * r;
}

// Uniform tone: 0 = background, low = sparse 25% grid, >= 8 = 50% checker.
static inline uint8_t tone(int px, int py, int level) {
    if (level <= 0) return BG;
    if (level >= 8) return ((px ^ py) & 1) ? FG : BG;
    return ((px & 1) == 0 && (py & 1) == 0) ? FG : BG;
}
static void ditherRound(int x, int y, int w, int h, int r, int level) {
    for (int py = y; py < y + h; py++)
        for (int px = x; px < x + w; px++)
            if (inRound(px, py, x, y, w, h, r)) epd.drawPixelFast(px, py, tone(px, py, level));
}

// Cards are plain on this panel: dithered gradients look noisy on e-ink.
static void gradientRound(int, int, int, int, int, int, int) {}

static void border(int x, int y, int w, int h, int r, int t, uint8_t c) {
    for (int i = 0; i < t; i++) epd.drawRoundRect(x + i, y + i, w - 2 * i, h - 2 * i, r - i, c);
}

static void dashedBorder(int x, int y, int w, int h, int r, uint8_t c) {
    border(x, y, w, h, r, 2, c);
    for (int px = x + r; px < x + w - r; px += 14) {
        epd.fillRect(px + 8, y, 6, 2, BG);
        epd.fillRect(px + 8, y + h - 2, 6, 2, BG);
    }
    for (int py = y + r; py < y + h - r; py += 14) {
        epd.fillRect(x, py + 8, 2, 6, BG);
        epd.fillRect(x + w - 2, py + 8, 2, 6, BG);
    }
}

static void thickLine(int x0, int y0, int x1, int y1, uint8_t c, int t = 3) {
    for (int dx = -t / 2; dx <= t / 2; dx++)
        for (int dy = -t / 2; dy <= t / 2; dy++) epd.drawLine(x0 + dx, y0 + dy, x1 + dx, y1 + dy, c);
}

static void fillTri(int x0, int y0, int x1, int y1, int x2, int y2, uint8_t c) {
    int minY = min(y0, min(y1, y2)), maxY = max(y0, max(y1, y2));
    for (int y = minY; y <= maxY; y++) {
        int xs[3], n = 0;
        int pts[3][2] = {{x0, y0}, {x1, y1}, {x2, y2}};
        for (int i = 0; i < 3; i++) {
            int ax = pts[i][0], ay = pts[i][1], bx = pts[(i + 1) % 3][0], by = pts[(i + 1) % 3][1];
            if ((y >= ay && y < by) || (y >= by && y < ay)) xs[n++] = ax + (y - ay) * (bx - ax) / (by - ay);
        }
        if (n >= 2) epd.drawLine(min(xs[0], xs[1]), y, max(xs[0], xs[1]), y, c);
    }
}

// ---------------- text ----------------
// Exact metrics of the bundled Roboto Black sizes (ascent/descent in px).
enum Font : int8_t { F_XS = 0, F_S, F_M, F_L, F_XL };
static const uint8_t ASC[] = {15, 23, 30, 36, 60};
static const uint8_t DESC[] = {5, 8, 10, 11, 18};
enum Align : uint8_t { AL_L, AL_C, AL_R };

static void font(Font f) {
    switch (f) {
    case F_XS: epd.setFont(Roboto_Black_10); break;
    case F_S: epd.setFont(Roboto_Black_16); break;
    case F_M: epd.setFont(Roboto_Black_20); break;
    case F_L: epd.setFont(Roboto_Black_24); break;
    default: epd.setFont(Roboto_Black_40); break;
    }
}
static int textW(const char *s) {
    BB_RECT r;
    epd.getStringBox(s, &r);
    return r.w;
}

#ifdef SLATE_DEBUG
static int fitShrinks = 0, fitCuts = 0;
#endif

// Draws s inside the box: largest font from `pref` down to `minF` that fits
// both width and height; if even `minF` is too wide, cuts with "...".
static void tbox(const char *s, int x, int y, int w, int h, Align al, Font pref, Font minF, uint8_t c) {
    Font f = pref;
    for (;; f = (Font)(f - 1)) {
        font(f);
        if ((textW(s) <= w && ASC[f] + DESC[f] <= h) || f <= minF) break;
    }
#ifdef SLATE_DEBUG
    if (f != pref) { fitShrinks++; LOG("fit: shrink '%s' to font %d (box %dx%d)\n", s, f, w, h); }
#endif
    char buf[96];
    strlcpy(buf, s, sizeof buf);
    if (textW(buf) > w) {
#ifdef SLATE_DEBUG
        fitCuts++; LOG("fit: CUT '%s' (box w %d)\n", s, w);
#endif
        size_t n = strlen(buf);
        while (n > 0) {
            buf[--n] = 0;
            char tmp[100];
            snprintf(tmp, sizeof tmp, "%s...", buf);
            if (textW(tmp) <= w) { strlcpy(buf, tmp, sizeof buf); break; }
        }
    }
    int tw = textW(buf);
    int tx = al == AL_L ? x : al == AL_C ? x + (w - tw) / 2 : x + w - tw;
    int base = y + (h - (ASC[f] + DESC[f])) / 2 + ASC[f];
    epd.setTextColor(c, BBEP_TRANSPARENT);
    epd.setCursor(tx, base);
    epd.print(buf);
}

// Two-line variant for card subtitles: breaks at the last space that fits.
static void tbox2(const char *s, int x, int y, int w, Font f, uint8_t c) {
    font(f);
    if (textW(s) <= w) { tbox(s, x, y, w, ASC[f] + DESC[f], AL_L, f, f, c); return; }
    char a[96];
    strlcpy(a, s, sizeof a);
    for (int i = strlen(a) - 1; i > 0; i--) {
        if (a[i] != ' ') continue;
        a[i] = 0;
        if (textW(a) <= w) {
            int lh = ASC[f] + DESC[f];
            tbox(a, x, y, w, lh, AL_L, f, f, c);
            tbox(s + i + 1, x, y + lh + 2, w, lh, AL_L, f, f, c);
            return;
        }
    }
    tbox(s, x, y, w, ASC[f] + DESC[f], AL_L, f, f, c);
}

// Width of the chord of the dial's inner circle at a given distance from centre.
static int chordW(int dy) {
    const int r = 114 - 8;  // keep 8 px clear of the wedges
    dy = abs(dy);
    return dy >= r ? 0 : 2 * (int)sqrtf((float)(r * r - dy * dy));
}
// Text line centred in the dial, sized to the chord at its farthest edge.
static void dialLine(const char *s, int top, int h, Font pref, Font minF, uint8_t c) {
    const int cy = 270;
    int far = max(abs(top - cy), abs(top + h - cy));
    int w = chordW(far);
    tbox(s, 218 - w / 2, top, w, h, AL_C, pref, minF, c);
}

static void fmtCount(uint32_t n, char *out, size_t sz) {
    if (n >= 100000) snprintf(out, sz, "%luk", (unsigned long)(n / 1000));
    else if (n >= 10000) snprintf(out, sz, "%.1fk", n / 1000.0f);
    else if (n >= 1000) snprintf(out, sz, "%lu,%03lu", (unsigned long)(n / 1000), (unsigned long)(n % 1000));
    else snprintf(out, sz, "%lu", (unsigned long)n);
}
// Compact durations that fit a stat card at 20 pt (<= 131 px): 59s, 59m, 5h12m, 23h, 2d 23h.
static void fmtDur(uint32_t s, char *out, size_t sz) {
    unsigned long d = s / 86400, h = s / 3600, m = s / 60 % 60;
    if (d >= 10) snprintf(out, sz, "%lud", d);
    else if (d) snprintf(out, sz, "%lud %luh", d, h % 24);
    else if (h >= 10) snprintf(out, sz, "%luh", h);
    else if (h) snprintf(out, sz, "%luh%02lum", h, m);
    else if (s >= 60) snprintf(out, sz, "%lum", s / 60);
    else snprintf(out, sz, "%lus", (unsigned long)s);
}
static void fmtClock(char *out, size_t sz) {
    if (!rtcValid()) { strlcpy(out, "--:--", sz); return; }
    time_t now = time(nullptr);
    struct tm t;
    localtime_r(&now, &t);
    strftime(out, sz, "%H:%M", &t);
}

// ---------------- icons (28 px box) ----------------
enum Icon : uint8_t { I_PAUSE, I_PLAY, I_STOP, I_SNIP, I_BOLT, I_FLIP, I_MOUSE, I_THEME, I_SCREEN, I_WINDOW, I_PATTERN, I_TIMER, I_CHECK, I_WIFI, I_CLEAN, I_SETTINGS };
static void icon(Icon i, int x, int y, uint8_t c) {
    switch (i) {
    case I_PAUSE: epd.fillRect(x + 7, y + 4, 5, 20, c); epd.fillRect(x + 16, y + 4, 5, 20, c); break;
    case I_PLAY: fillTri(x + 7, y + 3, x + 7, y + 25, x + 24, y + 14, c); break;
    case I_STOP: epd.fillRect(x + 5, y + 5, 18, 18, c); break;
    case I_SNIP:
        thickLine(x + 7, y + 3, x + 21, y + 3, c); thickLine(x + 7, y + 3, x + 7, y + 25, c);
        thickLine(x + 21, y + 3, x + 21, y + 25, c); thickLine(x + 7, y + 25, x + 14, y + 19, c);
        thickLine(x + 21, y + 25, x + 14, y + 19, c); break;
    case I_BOLT: fillTri(x + 16, y + 2, x + 6, y + 16, x + 15, y + 16, c); fillTri(x + 13, y + 12, x + 22, y + 12, x + 12, y + 26, c); break;
    case I_FLIP:
        for (int t = 0; t < 3; t++) epd.drawCircle(x + 14, y + 14, 10 - t, c);
        epd.fillRect(x + 2, y + 12, 24, 4, BG == c ? FG : BG);
        fillTri(x + 21, y + 8, x + 27, y + 8, x + 24, y + 14, c); fillTri(x + 1, y + 20, x + 7, y + 20, x + 4, y + 14, c); break;
    case I_MOUSE: fillTri(x + 6, y + 3, x + 6, y + 24, x + 22, y + 17, c); break;
    case I_THEME:
        for (int t = 0; t < 3; t++) epd.drawCircle(x + 14, y + 14, 11 - t, c);
        for (int dy = -9; dy <= 9; dy++) { int w = (int)sqrtf(81 - dy * dy); epd.drawLine(x + 14, y + 14 + dy, x + 14 + w, y + 14 + dy, c); }
        break;
    case I_SCREEN: border(x + 2, y + 4, 24, 16, 3, 3, c); epd.fillRect(x + 12, y + 20, 4, 4, c); epd.fillRect(x + 8, y + 23, 12, 3, c); break;
    case I_WINDOW: border(x + 2, y + 4, 24, 20, 3, 3, c); epd.fillRect(x + 2, y + 9, 24, 3, c); break;
    case I_PATTERN: for (int k = 0; k < 24; k += 2) epd.fillRect(x + 2 + k, y + 14 + (int)(7 * sinf(k * 0.5f)), 3, 3, c); break;
    case I_TIMER: for (int t = 0; t < 3; t++) epd.drawCircle(x + 14, y + 15, 10 - t, c); thickLine(x + 14, y + 15, x + 14, y + 9, c); thickLine(x + 14, y + 15, x + 18, y + 18, c); break;
    case I_CHECK: thickLine(x + 4, y + 15, x + 11, y + 22, c, 4); thickLine(x + 11, y + 22, x + 25, y + 6, c, 4); break;
    case I_WIFI:
        for (int t = 0; t < 3; t++) { epd.drawCircle(x + 14, y + 24, 20 - t, c); epd.drawCircle(x + 14, y + 24, 13 - t, c); }
        epd.fillRect(x - 8, y + 24, 44, 22, BG == c ? FG : BG); epd.fillCircle(x + 14, y + 22, 3, c); break;
    case I_CLEAN: for (int k = 0; k < 4; k++) epd.fillRect(x + 3, y + 4 + k * 6, 22, 3, c); break;
    case I_SETTINGS:
        for (int k = 0; k < 3; k++) { int yy = y + 4 + k * 10; epd.fillRect(x + 2, yy + 2, 24, 2, c); epd.fillRect(x + 5 + k * 6, yy, 6, 7, c); }
        break;
    }
}

// ---------------- widgets ----------------
struct Hit { int16_t x, y, w, h; uint8_t id; };
static Hit hits[24];
static int nHits = 0;
static void addHit(int x, int y, int w, int h, uint8_t id) { if (nHits < 24) hits[nHits++] = {(int16_t)x, (int16_t)y, (int16_t)w, (int16_t)h, id}; }

enum Action : uint8_t {
    A_NONE, A_HOLD, A_RESUME, A_STOP, A_SNIPPETS, A_SPEED, A_FLIP, A_MOVER, A_THEME, A_CAP_SCREEN, A_CAP_WINDOW,
    A_BACK, A_CLOCK, A_KEYBRIDGE, A_MOVER_TOGGLE, A_PATTERNS, A_INTERVAL, A_START_NOW, A_CLEAN_NOW, A_SETTINGS, A_REFRESH_PAGE, A_REBOOT,
    A_SNIP0 = 40, A_SPEED0 = 50, A_PAT0 = 60, A_INT0 = 70, A_REFR0 = 80
};
enum BtnStyle : uint8_t { B_NORMAL, B_SELECTED, B_DIM };

// Button 67 px tall: icon 28 px, title box 31 px (16 pt), subtitle box 20 px (10 pt).
static void button(int x, int y, int w, int h, Icon ic, const char *title, const char *sub, BtnStyle st, uint8_t id, bool mirror = false) {
    uint8_t ink;
    if (st == B_DIM) { border(x, y, w, h, 22, 2, FG); ink = FG; }
    else if (st == B_SELECTED) { epd.fillRoundRect(x, y, w, h, 22, FG); border(x + 3, y + 3, w - 6, h - 6, 19, 4, BG); ink = BG; }
    else { epd.fillRoundRect(x, y, w, h, 22, G_BUTTON); ink = BG; }
    const int pad = 14, iconW = 28, gap = 10, tw = w - pad - iconW - gap - 10;
    int ix = mirror ? x + w - pad - iconW : x + pad;
    icon(ic, ix, y + (h - iconW) / 2, ink);
    int tx = mirror ? x + 10 : x + pad + iconW + gap;
    Align al = mirror ? AL_R : AL_L;
    int ty = y + (h - 31 - 2 - 20) / 2;
    tbox(title, tx, ty, tw, 31, al, F_S, F_XS, ink);
    tbox(sub, tx, ty + 33, tw, 20, al, F_XS, F_XS, ink);
    if (id) addHit(x, y, w, h, id);
}


enum PillState : uint8_t { PS_IDLE, PS_ACTIVE, PS_OFF };
static int pill(int right, const char *label, PillState s) {
    font(F_XS);
    int w = 14 + 10 + 8 + textW(label) + 14, h = 36, x = right - w, y = 27;
    uint8_t ink = FG;
    if (s == PS_ACTIVE) { epd.fillRoundRect(x, y, w, h, 18, FG); ink = BG; epd.fillRect(x + 14, y + 13, 10, 10, BG); }
    else if (s == PS_OFF) { dashedBorder(x, y, w, h, 18, FG); epd.drawRect(x + 14, y + 13, 10, 10, FG); }
    else { border(x, y, w, h, 18, 2, FG); epd.fillRect(x + 14, y + 13, 10, 10, FG); }
    tbox(label, x + 32, y, w - 46, h, AL_L, F_XS, F_XS, ink);
    return x - 10;
}

static PillState hidPill() {
    if (!usbMounted.load() || usbSuspended.load()) return PS_OFF;
    return phase.load() == Phase::Typing ? PS_ACTIVE : PS_IDLE;
}
static PillState macPill() { return macOnline() ? PS_ACTIVE : PS_OFF; }  // filled while the Mac app is connected
static PillState moverPill() {
    switch (moverState()) {
    case MoverState::Active: return PS_ACTIVE;
    case MoverState::Armed: return PS_IDLE;
    default: return PS_OFF;
    }
}

// Pills right-aligned to `right`; the clock sits left of them. Returns the clock's left edge.
static int topBar(int left, int right, bool clockAtLeft) {
    int r = right;
    r = pill(r, "Mover", moverPill());
    r = pill(r, "Mac", macPill());
    r = pill(r, "KeyBridge", hidPill());
    char clk[8];
    fmtClock(clk, sizeof clk);
    const int cw = 110;
    int cx = clockAtLeft ? left : r - 6 - cw;
    tbox(clk, cx, 22, cw, 46, clockAtLeft ? AL_L : AL_R, F_M, F_S, FG);
    return cx;
}

// Stat card 159 x 104: label box 20 px, value box 48 px.
static void stat(int x, int y, int w, int h, const char *label, const char *value) {
    epd.fillRoundRect(x, y, w, h, 22, G_CARD);
    border(x, y, w, h, 22, 2, FG);
    tbox(label, x + 14, y + 14, w - 28, 20, AL_L, F_XS, F_XS, FG);
    tbox(value, x + 14, y + 42, w - 28, 48, AL_L, F_M, F_XS, FG);
}

static void dial(int lit, int offLevel, int altLevel = -1) {
    const int cx = 218, cy = 270, rO = 146, rI = 114;
    for (int py = cy - rO; py <= cy + rO; py++)
        for (int px = cx - rO; px <= cx + rO; px++) {
            int dx = px - cx, dy = py - cy, d2 = dx * dx + dy * dy;
            if (d2 > rO * rO || d2 < rI * rI) continue;
            float a = atan2f((float)dx, (float)-dy) * 57.29578f;
            if (a < 0) a += 360;
            int wedge = (int)(a / 11.25f);
            float within = a - wedge * 11.25f;
            uint8_t c;
            if (within > 9.5f) c = BG;
            else if (wedge < lit) c = FG;
            else {
                // inactive wedge: 2 px outline
                float d = sqrtf((float)d2), arc = d * 0.0174533f;
                bool edge = d - rI < 2.0f || rO - d < 2.0f || within * arc < 2.0f || (9.5f - within) * arc < 2.0f;
                c = edge ? FG : BG;
            }
            epd.drawPixelFast(px, py, c);
        }
}

// Dial centre stacks. Inner circle radius 114 (106 usable), centre y 270.
static void dialStack3(const char *top, const char *mid, const char *bot, uint8_t c) {
    // 20 + 4 + 78 + 4 + 20 = 126 px, centred on 270
    dialLine(top, 207, 20, F_XS, F_XS, c);
    dialLine(mid, 231, 78, F_XL, F_L, c);
    dialLine(bot, 313, 20, F_XS, F_XS, c);
}
static void dialStackIcon(Icon ic, const char *title, const char *sub, const char *sub2, uint8_t c) {
    // icon 28 + 6 + title 47 + 4 + 20 (+ 2 + 20)
    int h = 28 + 6 + 47 + 4 + 20 + (sub2 ? 22 : 0), top = 270 - h / 2;
    icon(ic, 204, top, c);
    dialLine(title, top + 34, 47, F_L, F_M, c);
    dialLine(sub, top + 85, 20, F_XS, F_XS, c);
    if (sub2) dialLine(sub2, top + 107, 20, F_XS, F_XS, c);
}

// USB plug, charging bolt, battery with 4 segments and percentage, right aligned.
static void drawPower(int right, int cy) {
    if (power.battery) {
        char t[8];
        snprintf(t, sizeof t, "%d%%", power.pct);
        tbox(t, right - 64, cy - 10, 64, 20, AL_R, F_XS, F_XS, FG);   // room for "100%" without a cut
        int bx = right - 64 - 6 - 40, by = cy - 9;
        border(bx, by, 36, 18, 4, 2, FG);
        epd.fillRect(bx + 36, by + 5, 4, 8, FG);
        int segs = (power.pct + 24) / 25;                      // 1..4 (0 only when empty)
        if (power.pct < 4) segs = 0;
        for (int i = 0; i < segs; i++) epd.fillRect(bx + 4 + i * 8, by + 4, 7, 10, FG);
        right = bx - 6;
    }
    if (power.chg == 1 || power.chg == 2) {                    // charging: bolt
        int x = right - 12, y = cy - 9;
        fillTri(x + 8, y, x, y + 10, x + 6, y + 10, FG);
        fillTri(x + 4, y + 8, x + 11, y + 8, x + 2, y + 18, FG);
        right = x - 6;
    }
    if (power.vbus && usbMounted.load() && !usbSuspended.load()) {  // USB data enumerated: two arrows
        int x = right - 20, y = cy - 8;
        epd.fillRect(x, y + 2, 16, 2, FG);                     // shaft to the right
        fillTri(x + 16, y + 3, x + 11, y - 1, x + 11, y + 7, FG);
        epd.fillRect(x + 4, y + 11, 16, 2, FG);                // shaft to the left
        fillTri(x + 4, y + 12, x + 9, y + 8, x + 9, y + 16, FG);
    } else if (power.vbus) {                                   // charger only, no data: plug
        int x = right - 16, y = cy - 9;
        epd.fillRect(x + 3, y, 3, 5, FG);
        epd.fillRect(x + 10, y, 3, 5, FG);
        epd.fillRoundRect(x + 1, y + 5, 14, 8, 2, FG);
        epd.fillRect(x + 6, y + 13, 4, 5, FG);
    }
}

static void leftCard(bool dashed, const char *footer) {
    if (dashed) dashedBorder(22, 22, 392, 496, 26, FG); else border(22, 22, 392, 496, 26, 2, FG);
    tbox("KeyBridge Slate", 46, 32, 220, 31, AL_L, F_S, F_XS, FG);
    drawPower(390, 47);
    tbox(footer, 42, 470, 352, 22, AL_C, F_XS, F_XS, FG);
    tbox("© 2026 TechWare", 42, 494, 352, 20, AL_C, F_XS, F_XS, FG);
}

static void pillButton(int x, int y, int w, const char *label, Icon ic, bool right, uint8_t id) {
    const int h = 60;
    epd.fillRoundRect(x, y, w, h, 30, FG);
    if (ic == I_PLAY) {  // back arrow
        thickLine(x + 24, y + 30, x + 46, y + 30, BG, 4);
        thickLine(x + 24, y + 30, x + 34, y + 20, BG, 4);
        thickLine(x + 24, y + 30, x + 34, y + 40, BG, 4);
    } else icon(ic, x + 20, y + 16, BG);
    tbox(label, x + 58, y, w - 58 - 22, h, AL_L, F_S, F_XS, BG);
    addHit(x, y, w, h, id);
    (void)right;
}

// Header for sub-pages: title, subtitle, clock and pills on one 46 px row.
static void pageHeader(const char *title, const char *sub) {
    int clockLeft = topBar(0, 938, false);
    font(F_L);
    int tw = min(textW(title), 260);
    tbox(title, 22, 22, tw, 47, AL_L, F_L, F_M, FG);
    int sx = 22 + tw + 16;
    tbox(sub, sx, 36, clockLeft - 14 - sx, 20, AL_L, F_XS, F_XS, FG);
}
static void pageFooterNote(const char *note) {
    // between Back (ends at 192) and the right edge / Clean now
    tbox(note, 210, 476, 728, 20, AL_R, F_XS, F_XS, FG);
}

// ---------------- pages ----------------
static const char *SPEED_NAMES[] = {"Slow", "Normal", "Fast"};
static const char *PAT_NAMES[] = {"1 px L/R", "Circle", "Square", "Figure 8", "Zigzag", "Random", "Scroll", "Tap"};
// Indexed by the stored interval value (see app.h); shown in this order:
static const uint8_t INT_ORDER[] = {INT_RANDOM, INT_10S, INT_30S, INT_60S};
static const char *intName(uint8_t v) { return v == INT_10S ? "10 s" : v == INT_30S ? "30 s" : v == INT_60S ? "60 s" : "Random"; }
static const char *intSub(uint8_t v) { return v == INT_RANDOM ? "1-30 s, default" : "Fixed"; }
static const char *REFR_NAMES[] = {"Invisible", "Night only", "Off", "5 min"};
static const char *REFR_SUBS[] = {"", "Deep clean at 03:00 only", "By hand only, may ghost", "Full clean every 5 min if needed, and at 03:00"};
static const uint8_t REFR_ORDER[] = {REFRESH_5MIN, REFRESH_NIGHTLY, REFRESH_OFF};

// Four rows of buttons fill the space under the stats: (518 - 208 - 3 * 14) / 4 = 67.
static const int COLX0 = 432, BW = 246, BH = 67, COLX1 = 432 + 246 + 14;
static int rowY(int i) { return 208 + i * (BH + 14); }
static const int SW = (506 - 28) / 3;

static void drawHome() {
    char a[24], b[24], c[24], l1[48], l2[48];
    bool hold = settings.hold;
    Phase ph = phase.load();
    uint32_t now = millis();

    if (hold) {
        leftCard(true, "Sends from the Mac are refused");
        dial(0, 8, 2);
        fmtDur(holdSince ? (now - holdSince) / 1000 : 0, a, sizeof a);
        snprintf(l1, sizeof l1, "For %s", a);
        dialStackIcon(I_PAUSE, "On hold", l1, nullptr, FG);
    } else if (ph == Phase::Typing) {
        uint32_t tot = max<uint32_t>(1, pasteTotal.load()), done = min(pasteTyped.load(), tot);
        int pct = done * 100 / tot;
        leftCard(false, "Tap Stop to cancel");
        dial(pct * 32 / 100, 2);
        snprintf(l1, sizeof l1, "%d%%", pct);
        fmtCount(done, a, sizeof a); fmtCount(tot, b, sizeof b);
        snprintf(l2, sizeof l2, "%s / %s", a, b);
        dialStack3("Typing...", l1, l2, FG);
    } else if (ph == Phase::Sent) {
        leftCard(false, "Back to the mover in a moment");
        dial(32, 2);
        epd.fillCircle(218, 270, 112, FG);
        fmtCount(sentChars.load(), a, sizeof a); snprintf(l1, sizeof l1, "%s chars", a);
        fmtDur(sentMs.load() / 1000, b, sizeof b); snprintf(l2, sizeof l2, "in %s", b);
        dialStackIcon(I_CHECK, "Sent", l1, l2, BG);
    } else {
        leftCard(false, "Send from the Mac to type");
        dial(32, 2);
        dialStackIcon(I_CHECK, "Ready", SPEED_NAMES[settings.speed], "speed", FG);
    }

    topBar(COLX0, 938, true);
    if (hold) {
        fmtCount(stats.refusedToday, a, sizeof a);
        fmtDur(holdSince ? (now - holdSince) / 1000 : 0, b, sizeof b);
        fmtCount(stats.typedToday, c, sizeof c);
        stat(COLX0, 86, SW, 104, "Refused", a); stat(COLX0 + SW + 14, 86, SW, 104, "Held for", b); stat(COLX0 + 2 * (SW + 14), 86, SW, 104, "Typed today", c);
    } else {
        fmtCount(stats.typedToday, a, sizeof a);
        fmtCount(stats.sendsToday, b, sizeof b);
        fmtDur((now - bootMs) / 1000, c, sizeof c);
        stat(COLX0, 86, SW, 104, "Typed today", a); stat(COLX0 + SW + 14, 86, SW, 104, "Sends", b); stat(COLX0 + 2 * (SW + 14), 86, SW, 104, "Uptime", c);
    }

    // KeyBridge is driven from the Mac (speed, snippets, hold live there): the board keeps only
    // what is local to it.
    bool typing = ph == Phase::Typing;
    BtnStyle norm = typing ? B_DIM : B_NORMAL;
    const int FULL = COLX1 + BW - COLX0;
    const char *ms = typing ? "Waits for typing" : moverState() == MoverState::Paused ? "Paused" : moverState() == MoverState::Armed ? "Resumes shortly" : PAT_NAMES[settings.pattern];
    int r = 0;
    if (typing) button(COLX0, rowY(r++), FULL, BH, I_STOP, "Stop", "Cancel this send", B_SELECTED, A_STOP);
    button(COLX0, rowY(r++), FULL, BH, I_MOUSE, "Mover", ms, norm, typing ? 0 : A_MOVER);
    if (hold && !typing) button(COLX0, rowY(r++), FULL, BH, I_PLAY, "Resume", "Accept sends again", B_SELECTED, A_RESUME);
    button(COLX0, rowY(r), BW, BH, I_FLIP, "Flip", "Rotate 180", norm, typing ? 0 : A_FLIP);
    button(COLX1, rowY(r++), BW, BH, I_THEME, "Theme", "Flip colors", norm, typing ? 0 : A_THEME);
    button(COLX0, rowY(r), FULL, BH, I_SETTINGS, "Settings", "Refresh, restart", norm, typing ? 0 : A_SETTINGS);
}

static void drawMover() {
    char a[24], b[24], c[24], l1[48], l2[48];
    MoverState ms = moverState();
    uint32_t now = millis();
    if (ms == MoverState::Paused) {
        leftCard(true, "Screen may lock while paused");
        dial(0, 8, 2);
        dialStackIcon(I_PAUSE, "Paused", "Tap Resume", nullptr, FG);
    } else if (ms == MoverState::Armed) {
        leftCard(false, "Takes over when typing stops");
        uint32_t until = moverArmedUntil.load();
        uint32_t left = until > now ? (until - now + 999) / 1000 : 0;
        dial(9, 2);
        snprintf(l1, sizeof l1, "%lus", (unsigned long)left);
        dialStack3("Armed, starts in", l1, "after typing", FG);
    } else {
        leftCard(false, "");
        int32_t left = (int32_t)(nextMoveAt.load() - now);
        if (left < 0) left = 0;
        uint32_t span = max<uint32_t>(1000, nextMoveAt.load() - lastMoveAt.load());
        dial(min<int>(32, left * 32 / span + 1), 2);
        snprintf(l1, sizeof l1, "%lus", (unsigned long)((left + 999) / 1000));
        fmtCount(stats.movesToday, a, sizeof a);
        snprintf(l2, sizeof l2, "Moves: %s", a);
        dialStack3("Next move in", l1, l2, FG);
    }
    topBar(COLX0, 938, true);
    fmtCount(stats.movesToday, a, sizeof a);
    float m = stats.moveDistPx * 0.000264f;
    if (m < 100) snprintf(b, sizeof b, "%.1f m", m);
    else if (m < 1000) snprintf(b, sizeof b, "%.0f m", m);
    else if (m < 10000) snprintf(b, sizeof b, "%.1f km", m / 1000);
    else snprintf(b, sizeof b, "%.0f km", m / 1000);
    fmtDur((now - bootMs) / 1000, c, sizeof c);
    stat(COLX0, 86, SW, 104, "Moves today", a); stat(COLX0 + SW + 14, 86, SW, 104, "Distance", b); stat(COLX0 + 2 * (SW + 14), 86, SW, 104, "Awake", c);
    button(COLX0, rowY(0), COLX1 + BW - COLX0, BH, I_SNIP, "KeyBridge", settings.hold ? "On hold" : "Types what the Mac sends", B_NORMAL, A_KEYBRIDGE);
    if (ms == MoverState::Paused) button(COLX0, rowY(1), BW, BH, I_PLAY, "Resume", "Keep awake again", B_SELECTED, A_MOVER_TOGGLE);
    else if (ms == MoverState::Armed) button(COLX0, rowY(1), BW, BH, I_PLAY, "Start now", "Skip the wait", B_NORMAL, A_START_NOW);
    else button(COLX0, rowY(1), BW, BH, I_PAUSE, "Pause", "Stop moving", B_NORMAL, A_MOVER_TOGGLE);
    button(COLX1, rowY(1), BW, BH, I_PATTERN, "Pattern", PAT_NAMES[settings.pattern], B_NORMAL, A_PATTERNS);
    snprintf(l1, sizeof l1, "%s%s", intName(settings.interval), settings.interval == INT_RANDOM ? " 1-30 s" : "");
    button(COLX0, rowY(2), BW, BH, I_TIMER, "Interval", l1, B_NORMAL, A_INTERVAL);
    button(COLX1, rowY(2), BW, BH, I_FLIP, "Flip", "Rotate 180", B_NORMAL, A_FLIP);
    button(COLX0, rowY(3), BW, BH, I_THEME, "Theme", "Flip colors", B_NORMAL, A_THEME);
    button(COLX1, rowY(3), BW, BH, I_SETTINGS, "Settings", "Refresh, restart", B_NORMAL, A_SETTINGS);
}

// Picker card: title 47 px box, subtitle up to two 20 px lines, optional bar meter.
static void pickerCard(int x, int y, int w, int h, const char *title, const char *sub, bool sel, int bars, uint8_t id) {
    if (sel) { epd.fillRoundRect(x, y, w, h, 24, FG); border(x + 3, y + 3, w - 6, h - 6, 21, 4, BG); }
    else { epd.fillRoundRect(x, y, w, h, 24, G_CARD); border(x, y, w, h, 24, 2, FG); }
    uint8_t ink = sel ? BG : FG;
    const int pad = 22;
    tbox(title, x + pad, y + 20, w - 2 * pad, 47, AL_L, w > 250 ? F_L : F_M, F_S, ink);
    if (sel) icon(I_CHECK, x + w - pad - 28, y + h - pad - 28 - (bars >= 0 ? 90 : 0), ink);
    tbox2(sub, x + pad, y + 76, w - 2 * pad, F_XS, ink);
    if (bars >= 0) {
        int slot = (w - 2 * pad) / 16;
        for (int k = 0; k < 16; k++) {
            int bh2 = 10 + 4 * k, bx = x + pad + k * slot;
            if (k < bars) epd.fillRect(bx, y + h - 24 - bh2, slot - 3, bh2, ink);
            else epd.drawRect(bx, y + h - 24 - bh2, slot - 3, bh2, ink);
        }
    }
    addHit(x, y, w, h, id);
}

static const int TILE_W = (916 - 3 * 14) / 4, TILE_H = (352 - 14) / 2;

static void drawSnippets() {
    pageHeader("Snippets", "Tap one to type it");
    char nb[8], sub[32];
    for (int i = 0; i < SNIPPET_COUNT; i++) {
        int x = 22 + (i % 4) * (TILE_W + 14), y = 84 + (i / 4) * (TILE_H + 14);
        border(x, y, TILE_W, TILE_H, 22, 2, FG);
        snprintf(nb, sizeof nb, "%02d", i + 1);
        tbox(nb, x + 18, y + 14, 90, 47, AL_L, F_L, F_L, FG);
        tbox(snippets[i].len ? snippets[i].name : "Empty", x + 18, y + TILE_H - 66, TILE_W - 36, 31, AL_L, F_S, F_XS, FG);
        if (snippets[i].len) snprintf(sub, sizeof sub, "%lu characters", (unsigned long)snippets[i].len); else strlcpy(sub, "Set it from the Mac", sizeof sub);
        tbox(sub, x + 18, y + TILE_H - 32, TILE_W - 36, 20, AL_L, F_XS, F_XS, FG);
        if (snippets[i].len) addHit(x, y, TILE_W, TILE_H, A_SNIP0 + i);
    }
    pillButton(22, 456, 170, "Back", I_PLAY, false, A_BACK);
}

static void drawSpeed() {
    pageHeader("Speed", "How fast keys are typed");
    int w = (916 - 2 * 16) / 3;
    const char *subs[] = {"Remote desktops, laggy apps", "Works almost everywhere", "Local apps only, may drop keys"};
    const int bars[] = {5, 10, 16};
    for (int i = 0; i < 3; i++) pickerCard(22 + i * (w + 16), 84, w, 352, SPEED_NAMES[i], subs[i], settings.speed == i, bars[i], A_SPEED0 + i);
    pillButton(22, 456, 170, "Back", I_PLAY, false, A_BACK);
    pageFooterNote("Saved on the device, applies to the next send");
}

static void drawPatterns() {
    pageHeader("Pattern", "How the cursor moves");
    for (int i = 0; i < PAT_COUNT; i++) {
        int x = 22 + (i % 4) * (TILE_W + 14), y = 84 + (i / 4) * (TILE_H + 14);
        bool sel = settings.pattern == i;
        if (sel) { epd.fillRoundRect(x, y, TILE_W, TILE_H, 22, FG); border(x + 3, y + 3, TILE_W - 6, TILE_H - 6, 19, 4, BG); }
        else border(x, y, TILE_W, TILE_H, 22, 2, FG);
        uint8_t ink = sel ? BG : FG;
        icon(I_PATTERN, x + 18, y + 22, ink);
        if (sel) icon(I_CHECK, x + TILE_W - 46, y + 18, ink);
        tbox(PAT_NAMES[i], x + 18, y + TILE_H - 50, TILE_W - 36, 31, AL_L, F_S, F_XS, ink);
        addHit(x, y, TILE_W, TILE_H, A_PAT0 + i);
    }
    pillButton(22, 456, 170, "Back", I_PLAY, false, A_BACK);
    pageFooterNote("1 px L/R is invisible on screen");
}

static void drawInterval() {
    pageHeader("Interval", "Time between moves");
    int w = (916 - 3 * 14) / 4;
    const int bars[] = {10, 4, 10, 16};
    for (int k = 0; k < INT_COUNT; k++) {
        uint8_t v = INT_ORDER[k];
        pickerCard(22 + k * (w + 14), 84, w, 352, intName(v), intSub(v), settings.interval == v, bars[k], A_INT0 + v);
    }
    pillButton(22, 456, 170, "Back", I_PLAY, false, A_BACK);
    pageFooterNote("Keep it under your lock timeout");
}

static void drawRefresh() {
    pageHeader("Refresh", "Keeps the e-ink free of ghosting");
    int w = (916 - 2 * 16) / 3;
    for (int k = 0; k < 3; k++) {
        uint8_t v = REFR_ORDER[k];
        pickerCard(22 + k * (w + 16), 84, w, 352, REFR_NAMES[v], REFR_SUBS[v], settings.refresh == v, -1, A_REFR0 + v);
    }
    pillButton(22, 456, 170, "Back", I_PLAY, false, A_BACK);
    pillButton(938 - 230, 456, 230, "Clean now", I_CLEAN, true, A_CLEAN_NOW);
    tbox("Clean now: deep clean with one flash", 210, 476, 938 - 230 - 18 - 210, 20, AL_C, F_XS, F_XS, FG);
}

static void drawSettings() {
    pageHeader("Settings", "Refresh and restart");
    bool armed = millis() < rebootArmedUntil;
    button(22, 110, 448, 120, I_CLEAN, "Refresh", "How often the screen is cleaned", B_NORMAL, A_REFRESH_PAGE);
    if (armed) button(22, 250, 448, 120, I_FLIP, "Tap again to restart", "Restarts the board now", B_SELECTED, A_REBOOT);
    else button(22, 250, 448, 120, I_FLIP, "Reboot", "Tap twice to restart the board", B_NORMAL, A_REBOOT);
    pillButton(22, 456, 170, "Back", I_PLAY, false, A_BACK);
}

// No USB data link (unplugged, charger only, or the laptop is asleep): quiet standby screen.
// VBUS from the charger chip is what notices an unplugged cable (no USB event fires for that).
std::atomic<uint8_t> standbyLight{0};
bool uiStandby();
static bool standbyNow() { return !power.vbus || !usbMounted.load() || usbSuspended.load(); }

bool uiStandby() { return standbyNow(); }

static void standbyStat(int cx, const char *label, const char *value) {
    tbox(label, cx - 110, 368, 220, 20, AL_C, F_XS, F_XS, FG);
    tbox(value, cx - 110, 392, 220, 34, AL_C, F_M, F_XS, FG);
}

// Big seven-segment digit: segments a..g as bits 0..6. c < 0 draws a dash (time not set).
static void segDigit(int x, int y, int c) {
    static const uint8_t SEG[10] = {0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F};
    const int W = 130, H = 210, T = 24, ym = y + (H - T) / 2;
    uint8_t m = c < 0 ? 0x40 : SEG[c];
    if (m & 0x01) epd.fillRoundRect(x + 3, y, W - 6, T, T / 2, FG);                    // a
    if (m & 0x02) epd.fillRoundRect(x + W - T, y + 3, T, ym - y + T - 6, T / 2, FG);   // b
    if (m & 0x04) epd.fillRoundRect(x + W - T, ym + 3, T, y + H - ym - 6, T / 2, FG);  // c
    if (m & 0x08) epd.fillRoundRect(x + 3, y + H - T, W - 6, T, T / 2, FG);            // d
    if (m & 0x10) epd.fillRoundRect(x, ym + 3, T, y + H - ym - 6, T / 2, FG);          // e
    if (m & 0x20) epd.fillRoundRect(x, y + 3, T, ym - y + T - 6, T / 2, FG);           // f
    if (m & 0x40) epd.fillRoundRect(x + 3, ym, W - 6, T, T / 2, FG);                   // g
}

static void drawStandby() {
    dashedBorder(22, 22, 916, 496, 26, FG);
    tbox("KeyBridge Slate", 46, 32, 220, 31, AL_L, F_S, F_XS, FG);
    drawPower(914, 47);
    border(405, 30, 150, 34, 17, 2, FG);                       // status chip
    tbox("STANDBY", 415, 36, 130, 22, AL_C, F_XS, F_XS, FG);

    // hero clock: HH:MM in 210 px segments, centred (4 x 130 + 18 + 18 + 70 = 626 wide)
    int h = -1, m = -1;
    char date[40] = "Time not set";
    if (rtcValid()) {
        time_t now = time(nullptr);
        struct tm t;
        localtime_r(&now, &t);
        h = t.tm_hour; m = t.tm_min;
        strftime(date, sizeof date, "%A, %d %B %Y", &t);
    }
    const int x0 = 167, y0 = 84;
    segDigit(x0, y0, h < 0 ? -1 : h / 10);
    segDigit(x0 + 148, y0, h < 0 ? -1 : h % 10);
    epd.fillRoundRect(x0 + 278 + 23, y0 + 62, 24, 24, 6, FG);  // colon
    epd.fillRoundRect(x0 + 278 + 23, y0 + 124, 24, 24, 6, FG);
    segDigit(x0 + 348, y0, m < 0 ? -1 : m / 10);
    segDigit(x0 + 348 + 148, y0, m < 0 ? -1 : m % 10);
    tbox(date, 180, 304, 600, 40, AL_C, F_L, F_M, FG);

    epd.fillRect(120, 356, 720, 2, FG);                        // divider

    const char *usb = power.vbus ? "Power only" : "Not connected";
    const char *bat = power.chg == 1 || power.chg == 2 ? "Charging" : (power.chg == 3 ? "Full" : power.vbus ? "Not charging" : "On battery");
    char pct[12];
    snprintf(pct, sizeof pct, "%d%%", power.pct);
    standbyStat(240, "USB", usb);
    standbyStat(480, "BATTERY", power.battery ? pct : "--");
    standbyStat(720, "STATUS", bat);
    epd.fillRect(360, 372, 2, 54, FG);                         // column rules
    epd.fillRect(600, 372, 2, 54, FG);

    tbox(usbMounted.load() && usbSuspended.load() ? "Computer asleep. Mover paused" : "Mover paused until USB connects",
         180, 450, 600, 24, AL_C, F_XS, F_XS, FG);
    tbox("© 2026 TechWare", 42, 494, 876, 20, AL_C, F_XS, F_XS, FG);
}

static void render() {
    crumb("r");
    nHits = 0;
    epd.fillScreen(BG);
    // A send always takes over the screen with its progress, then hands back.
    Phase ph = phase.load();
    if (ph == Phase::Typing || ph == Phase::Sent) { drawHome(); return; }
    if (standbyNow()) { drawStandby(); return; }
    switch (page) {
    case P_SNIPPETS: drawSnippets(); break;
    case P_SPEED: drawSpeed(); break;
    case P_MOVER: drawMover(); break;
    case P_PATTERNS: drawPatterns(); break;
    case P_INTERVAL: drawInterval(); break;
    case P_REFRESH: drawRefresh(); break;
    case P_SETTINGS: drawSettings(); break;
    default: drawHome(); break;
    }
}

// ---------------- output to the panel (epdiy) ----------------
// What the S3 driver really does (verified in its source and on the board):
//  - every drive phase is one LCD frame of ~28 ms; the waveform's per-phase times
//    are ignored, so epdiy's built-in DU is only 5 frames = ~140 ms: too short
//    to take pixels all the way to black or white (grey haze, ghosts, fading)
//  - epd_push_pixels() ignores its area: it always drives the whole screen
// So: own drive table "DUX" for this driver. Every changed pixel is driven for
// 12 frames each way (~360 ms) straight to full black or full white; unchanged
// pixels are not touched, so nothing flashes and nothing accumulates.
// Calibrated on this panel (calibration screen, 2026-11-19): black is fully
// saturated after 6 frames, white needs 10. Both get 12: full black and white with
// margin, and the same push in both directions, so no charge builds up in the
// pixels (an unbalanced push is what slowly brings shadows back).
static const int DUX_BLACK = 12, DUX_WHITE = 12, DUX_FRAMES = 12;
static uint8_t duxLut[DUX_FRAMES][16][4];                   // [frame][to][from / 4], 2 bits each
static int duxTimes[DUX_FRAMES];
static EpdWaveformPhases duxPhases;
static const EpdWaveformPhases *duxRanges[1] = {&duxPhases};
static EpdWaveformMode duxMode = {1, 1, duxRanges};         // type 1 = MODE_DU

// Calibration table in the MODE_GL16 slot: grey level selects the drive length.
//  level 0: black, 40 frames (reference)   levels 1..7: black, CAL_B frames
//  levels 8..14: white (from black), CAL_W frames   level 15: untouched
static const int CAL_FRAMES = 40;
static const uint8_t CAL_B[7] = {6, 10, 14, 18, 22, 26, 30};
static const uint8_t CAL_W[7] = {6, 10, 14, 18, 22, 26, 30};
static uint8_t calLut[CAL_FRAMES][16][4];
static int calTimes[CAL_FRAMES];
static EpdWaveformPhases calPhases;
static const EpdWaveformPhases *calRanges[1] = {&calPhases};
static EpdWaveformMode calMode = {5, 1, calRanges};
static const EpdWaveformMode *duxModes[2] = {&duxMode, &calMode};
static EpdWaveform duxWaveform = {2, 1, duxModes, nullptr};

static void buildDuxWaveform() {
    // 1 = push black, 2 = push white; a pixel is driven only when it must change
    for (int p = 0; p < DUX_FRAMES; p++) {
        duxTimes[p] = 0;
        for (int to = 0; to < 16; to++)
            for (int from = 0; from < 16; from++) {
                bool toBlack = to < 8, fromBlack = from < 8;
                uint8_t v = 0;
                if (toBlack && !fromBlack) v = 1;                        // white -> black: 12 frames
                else if (!toBlack && fromBlack && p < DUX_WHITE) v = 2;  // black -> white: 12 frames
                duxLut[p][to][from >> 2] |= v << (6 - 2 * (from & 3));
            }
    }
    for (int p = 0; p < CAL_FRAMES; p++) {
        calTimes[p] = 0;
        for (int to = 0; to < 16; to++)
            for (int from = 0; from < 16; from++) {
                bool fromWhite = from >= 8;
                uint8_t v = 0;
                if (to == 0 && fromWhite) v = 1;
                else if (to >= 1 && to <= 7 && fromWhite && p < CAL_B[to - 1]) v = 1;
                else if (to >= 8 && to <= 14 && !fromWhite && p < CAL_W[to - 8]) v = 2;
                calLut[p][to][from >> 2] |= v << (6 - 2 * (from & 3));
            }
    }
    calPhases.phases = CAL_FRAMES;
    calPhases.luts = &calLut[0][0][0];
    calPhases.phase_times = calTimes;
    duxPhases.phases = DUX_FRAMES;
    duxPhases.luts = &duxLut[0][0][0];
    duxPhases.phase_times = duxTimes;
    duxWaveform.temp_intervals = epdiy_ED047TC1.temp_intervals;
}

static int smallUpdates = 0;
static void resetDirty() { smallUpdates = 0; }
static int panelTemp() { return 25; }  // the v7 board has no sensor; the table has one range

// Copies the offscreen frame into epdiy's buffer: nibble order, theme and flip.
// Returns how many pixels differ from what was copied last time.
// bb (optional): {minX, minY, maxX, maxY} of the changed pixels.
static int copyFrame(bool invertAll = false, uint8_t *dst = nullptr, int *bb = nullptr) {
    const uint8_t *src = epd.currentBuffer();
    if (!dst) dst = epd_hl_get_framebuffer(&hl);
    const int pitch = SCREEN_W / 2;
    const bool inv = !settings.dark ^ invertAll, flip = settings.flipped;
    int changed = 0, minX = SCREEN_W, minY = SCREEN_H, maxX = -1, maxY = -1;
    for (int y = 0; y < SCREEN_H; y++) {
        const uint8_t *srow = src + (flip ? SCREEN_H - 1 - y : y) * pitch;
        uint8_t *drow = dst + y * pitch;
        for (int i = 0; i < pitch; i++) {
            uint8_t b;
            if (flip) b = srow[pitch - 1 - i];            // reversed pixel order keeps nibble order
            else { b = srow[i]; b = (b << 4) | (b >> 4); } // FastEPD: even x high nibble; epdiy: low
            if (inv) b ^= 0xFF;                            // 15 - v for each pixel
            uint8_t d = b ^ drow[i];
            if (d) {
                changed += ((d & 0x0F) != 0) + ((d & 0xF0) != 0);
                int x0 = (d & 0x0F) ? 2 * i : 2 * i + 1, x1 = (d & 0xF0) ? 2 * i + 1 : 2 * i;
                if (x0 < minX) minX = x0;
                if (x1 > maxX) maxX = x1;
                if (y < minY) minY = y;
                if (y > maxY) maxY = y;
            }
            drow[i] = b;
        }
    }
    if (bb) { bb[0] = minX; bb[1] = minY; bb[2] = maxX; bb[3] = maxY; }
    return changed;
}

static void updateArea(EpdRect area) {
    uint32_t t0 = millis();
    enum EpdDrawError err = epd_hl_update_area(&hl, MODE_DU, panelTemp(), area);
    if (err != EPD_DRAW_SUCCESS) LOG("update error %d\n", (int)err);
    LOG("update %ld ms\n", (long)(millis() - t0));
}

// Every tap, switch and tick: no flash, only the changed pixels are driven, 12
// frames each way. No extra one-sided pushes: they leave charge in the pixels and
// the old image slowly creeps back. Traces are removed by the stripe clean
// ("Clean now" and the 5-minute option).
static void showFrame(bool) {
    if (!copyFrame()) return;
    epd_poweron();
    updateArea(epd_full_screen());
    epd_poweroff();
    smallUpdates++;
}

// Recovery / startup: the vendor's full clean (black x10, white x10, 3 cycles),
// then everything redrawn from a clean white panel.
static void deepClean() {
    lastFullClean = millis();
    epd_poweron();
    epd_clear();
    epd_poweroff();
    memset(hl.front_fb, 0xFF, SCREEN_W / 2 * SCREEN_H);
    memset(hl.back_fb, 0xFF, SCREEN_W / 2 * SCREEN_H);
    copyFrame();
    epd_poweron();
    updateArea(epd_full_screen());
    epd_poweroff();
    smallUpdates = 0;
    LOG("deep clean %lu ms\n", (unsigned long)(millis() - lastFullClean));
    lastFullClean = millis();
}

// Shutter (the "diagonal cascade" mockup): 120 px tiles sweep from the top-left
// corner. Each tile goes solid black, then solid white, then back to the screen,
// so every pixel passes through both pure colours: a real clean, tile by tile.
static void shutterClean() {
    lastFullClean = millis();
    const int T = 120, pitch = SCREEN_W / 2;
    const int cols = (SCREEN_W + T - 1) / T, rows = (SCREEN_H + T - 1) / T, diags = cols + rows - 1;
    uint8_t *front = epd_hl_get_framebuffer(&hl);
    copyFrame();                                     // front = what should be shown
    uint8_t *target = (uint8_t *)heap_caps_malloc(pitch * SCREEN_H, MALLOC_CAP_SPIRAM);
    if (!target) { deepClean(); return; }
    memcpy(target, front, pitch * SCREEN_H);
    auto setDiag = [&](int d, int what) {            // 0 black, 1 white, 2 screen
        for (int r = 0; r < rows; r++) {
            int c = d - r;
            if (c < 0 || c >= cols) continue;
            int bx = c * T / 2, bw = (min(SCREEN_W, (c + 1) * T) - c * T) / 2;
            for (int y = r * T; y < min(SCREEN_H, (r + 1) * T); y++) {
                uint8_t *row = front + y * pitch + bx;
                if (what == 2) memcpy(row, target + y * pitch + bx, bw);
                else memset(row, what == 0 ? 0x00 : 0xFF, bw);
            }
        }
    };
    // The stripe: each box turns black, then white, then back to the screen as the
    // stripe moves on. Each phase is two steps after the previous one, so whenever
    // a box is driven, the boxes beside it (diagonal +-1) are idle: no opposite push
    // next to its edges, so no box borders are left behind.
    const int PHASES = 3, GAP = 2;
    epd_poweron();
    for (int step = 0; step < diags + (PHASES - 1) * GAP; step++) {
        for (int ph = 0; ph < PHASES; ph++) {
            int d = step - ph * GAP;
            // the panel's own top-left is your bottom-right when the screen is flipped
            int what = (ph == PHASES - 1) ? 2 : (ph % 2);   // 0 black, 1 white, 2 screen
            if (d >= 0 && d < diags) setDiag(settings.flipped ? diags - 1 - d : d, what);
        }
        updateArea(epd_full_screen());
    }
    epd_poweroff();
    free(target);
    smallUpdates = 0;
    LOG("shutter %lu ms\n", (unsigned long)(millis() - lastFullClean));
    lastFullClean = millis();
}

// Calibration screen (debug command 'Z'): one photo shows how many frames this
// panel needs to reach pure black and pure white.
static uint32_t calibUntil = 0;
static void runCalibration() {
    uint8_t *front = epd_hl_get_framebuffer(&hl);
    const size_t n = SCREEN_W / 2 * SCREEN_H;
    epd_poweron();
    epd_clear();
    memset(front, 0xFF, n);
    memset(hl.back_fb, 0xFF, n);
    auto box = [&](int x, int y, int w, int h, uint8_t level) {
        EpdRect r = {x, y, w, h};
        epd_fill_rect(r, (uint8_t)(level << 4), front);
    };
    // row 1: reference black (left), then black driven 6..30 frames
    box(30, 70, 120, 150, 0);
    for (int i = 0; i < 7; i++) box(200 + i * 105, 70, 95, 150, 1 + i);
    // row 2: squares made black first...
    for (int i = 0; i < 7; i++) box(200 + i * 105, 310, 95, 150, 0);
    epd_hl_update_screen(&hl, MODE_GL16, 25);
    // ...then whitened for 6..30 frames
    for (int i = 0; i < 7; i++) box(200 + i * 105, 310, 95, 150, 8 + i);
    epd_hl_update_screen(&hl, MODE_GL16, 25);
    epd_poweroff();
    calibUntil = millis() + 180000;
    LOG("calibration shown\n");
}

#ifdef SLATE_DEBUG
// Debug: stream the frame exactly as sent to the panel (epdiy 4-bit) to the Mac.
static void dumpFrame() {
    const uint8_t *fb = epd_hl_get_framebuffer(&hl);
    DebugSerial.write((const uint8_t *)"FRAME960x540\n", 13);
    const size_t n = SCREEN_W / 2 * SCREEN_H;
    for (size_t off = 0; off < n; off += 1024) DebugSerial.write(fb + off, min((size_t)1024, n - off));
    DebugSerial.flush();
}
#endif

// ---------------- input ----------------
static void go(Page p) { page = p; }

static void act(uint8_t id) {
    switch (id) {
    case A_HOLD: settings.hold = true; holdSince = millis(); storeSaveSettings(); break;
    case A_RESUME: settings.hold = false; holdSince = 0; storeSaveSettings(); break;
    case A_STOP: hidStop(); break;
    case A_SNIPPETS: go(P_SNIPPETS); break;
    case A_SPEED: go(P_SPEED); break;
    case A_FLIP: settings.flipped = !settings.flipped; storeSaveSettings(); break;
    case A_MOVER: go(P_MOVER); break;
    case A_THEME: settings.dark = !settings.dark; storeSaveSettings(); break;
    case A_CAP_SCREEN: hidCapture(false); break;
    case A_CAP_WINDOW: hidCapture(true); break;
    case A_BACK: go(page == P_REFRESH ? refreshReturn : page == P_SETTINGS ? returnPage : (page == P_PATTERNS || page == P_INTERVAL) ? P_MOVER : P_HOME); break;
    case A_KEYBRIDGE: go(P_HOME); break;
    case A_MOVER_TOGGLE: settings.moverOn = !settings.moverOn; storeSaveSettings(); moverResetSchedule(); break;
    case A_PATTERNS: go(P_PATTERNS); break;
    case A_INTERVAL: go(P_INTERVAL); break;
    case A_START_NOW: moverStartNow(); break;
    // the touch chip can re-report the same tap after the 6 s clean: one clean per tap
    case A_CLEAN_NOW: if (millis() - lastFullClean > 3000) cleanReq = 2; break;
    case A_SETTINGS: returnPage = page; go(P_SETTINGS); break;
    case A_REFRESH_PAGE: refreshReturn = page; go(P_REFRESH); break;
    case A_REBOOT:
        if (millis() < rebootArmedUntil) { storeSaveStats(); LOG("reboot\n"); delay(200); ESP.restart(); }
        rebootArmedUntil = millis() + 4000;
        break;
    default:
        if (id >= A_SNIP0 && id < A_SNIP0 + SNIPPET_COUNT) { hidTypeSnippet(id - A_SNIP0); go(P_MOVER); }
        else if (id >= A_SPEED0 && id < A_SPEED0 + 3) { settings.speed = id - A_SPEED0; storeSaveSettings(); go(P_HOME); }
        else if (id >= A_PAT0 && id < A_PAT0 + PAT_COUNT) { settings.pattern = id - A_PAT0; storeSaveSettings(); go(P_MOVER); }
        else if (id >= A_INT0 && id < A_INT0 + INT_COUNT) { settings.interval = id - A_INT0; storeSaveSettings(); moverResetSchedule(); go(P_MOVER); }
        else if (id >= A_REFR0 && id < A_REFR0 + 4) { settings.refresh = id - A_REFR0; storeSaveSettings(); }
        break;
    }
    redraw = true;
}

static std::atomic<bool> keyBootHit{false};
static void IRAM_ATTR keyBootIsr() { static uint32_t t; uint32_t n = millis(); if (n - t > 90) { t = n; keyBootHit = true; } }
void uiAttachKeys() { attachInterrupt(digitalPinToInterrupt(PIN_BOOT_BTN), keyBootIsr, FALLING); }

static void pollInput() {
    static bool down = false;
    int16_t x, y;
    bool pressed = touchRead(x, y);
    if (pressed && !down && millis() - lastTouchMs > 250) {
        lastTouchMs = millis();
        int hit = -1;
        for (int i = nHits - 1; i >= 0; i--) {
            const Hit &h = hits[i];
            if (x >= h.x && x < h.x + h.w && y >= h.y && y < h.y + h.h) { hit = h.id; break; }
        }
        LOG("tap %d,%d -> action %d (page %d)\n", x, y, hit, (int)page);
        if (hit >= 0) act(hit);
    }
    down = pressed;
    if (homeKeyPressed()) {  // round key: next front-light level
        if (standbyNow()) standbyLight = (standbyLight + 1) % 4;   // standby: temporary, never saved
        else {
            settings.light = (settings.light + 1) % 4;
            storeSaveSettings();
        }
        lightApply();
        lastTouchMs = millis();
    }

    // BOOT key flips the screen: latched by interrupt, so a quick click is never missed
    // while the panel is busy.
    // BOOT key: a short click flips the screen, holding it ~1 s runs the curtain.
    // The press is latched by interrupt, so a quick click is never missed while the panel is busy.
    static uint32_t bootDownAt = 0;
    static bool bootFired = false;
    bool bootLow = digitalRead(PIN_BOOT_BTN) == LOW;
    if (keyBootHit.exchange(false) && !bootDownAt) {
        if (bootLow) { bootDownAt = millis(); bootFired = false; }
        else act(A_FLIP);                          // the click was over before we looked
    }
    if (bootDownAt) {
        if (bootLow) {
            if (!bootFired && millis() - bootDownAt >= 1000) {
                bootFired = true;
                if (millis() - lastFullClean > 3000) cleanReq = 2;
            }
        } else {
            if (!bootFired) act(A_FLIP);
            bootDownAt = 0;
        }
    }
}

// ---------------- task ----------------
static void uiTask(void *) {
    // Offscreen canvas: FastEPD virtual panel, 16 greys, always upright.
    epd.initPanel(BB_PANEL_VIRTUAL);
    epd.setPanelSize(SCREEN_W, SCREEN_H);
    epd.setMode(BB_MODE_4BPP);
    // Panel: epdiy with LilyGo's board definition and the ED047TC1 waveforms.
    epd_init(&epd_board_v7, &ED047TC1, EPD_LUT_64K);
    epd_set_vcom(1560);
    buildDuxWaveform();
    hl = epd_hl_init(&duxWaveform);
    epd_set_rotation(EPD_ROT_LANDSCAPE);
    lastDark = settings.dark;
    lastFlip = settings.flipped;
    uiAttachKeys();
    crumb("r0");
    render();
    crumb("f0");
    deepClean();
    shownView = page;
    crumb("F0");
    uint32_t lastPeriodic = millis(), lastNightly = 0, lastFive = millis();
    int lastMinute = -1;
    for (;;) {
        if (!calibUntil) pollInput();
        uint32_t now = millis();
        if (settings.hold && !holdSince) holdSince = now;
        if (!settings.hold) holdSince = 0;
        if (page != P_MOVER && !settings.hold && now - lastTouchMs > 90000) { page = P_MOVER; redraw = true; }
        static uint32_t lastPower = 0;
        static bool wasStandby = true;                         // light is off in standby, back to the saved level after
        if (standbyNow() != wasStandby) {
            wasStandby = standbyNow();
            standbyLight = 0;
            lightApply();
        }
        if (now - lastPower > 2000 || !lastPower) {          // every 2 s, in this task (shares the I2C bus)
            lastPower = now;
            if (powerUpdate()) redraw = true;
        linkEnable(power.vbus && usbMounted.load());
            LOG("power: batt=%d %d%% %dmV vbus=%d chg=%d\n", power.battery, power.pct, power.mv, power.vbus, power.chg);
        }
        static Phase lastPhase = Phase::Idle;
        Phase ph = phase.load();
        if (ph != lastPhase) {
            if (lastPhase == Phase::Sent && ph == Phase::Idle && !settings.hold) page = P_MOVER;
            lastPhase = ph;
            redraw = true;
        }

#ifdef SLATE_DEBUG
        // Screen audit commands (debug builds only; nothing is typed on the laptop):
        // D dump frame, P<n> page, V0/V1/V2 view none/typing/sent, H hold, K theme, F flip, M mover
        static int pending = 0;
        while (DebugSerial.available()) {
            int c = DebugSerial.read();
            lastTouchMs = millis();  // counts as interaction: no auto-return mid-audit
            if (pending == 'P') { page = (Page)(c - '0'); redraw = true; pending = 0; continue; }
            if (pending == 'V') {
                if (c == '1') { pasteTotal = 10240; pasteTyped = 6656; phase = Phase::Typing; }
                else if (c == '2') { sentChars = 10240; sentMs = 72000; sentAt = millis(); phase = Phase::Sent; }
                else phase = Phase::Idle;
                redraw = true; pending = 0; continue;
            }
            switch (c) {
            case 'D': dumpFrame(); break;
            case 'T': {  // T<unix seconds>\n : set the clock (UTC) and the RTC chip
                char buf[16]; int n = 0; uint32_t t0 = millis();
                while (n < 15 && millis() - t0 < 500) {
                    int ch = DebugSerial.read();
                    if (ch < 0) { vTaskDelay(1); continue; }
                    if (ch == '\n') break;
                    buf[n++] = (char)ch;
                }
                buf[n] = 0;
                long epoch = atol(buf);
                if (epoch > 1700000000L) {
                    struct timeval tv = {epoch, 0};
                    settimeofday(&tv, nullptr);
                    rtcWriteFromSystem();
                    LOG("clock set to %ld\n", epoch);
                    redraw = true;
                }
                break;
            }
            case 'E': {  // E: enqueue "This is short text" like a Bluetooth send (no Bluetooth involved)
                static const char t[] = "This is short text";
                hidEnqueue(t, sizeof t - 1, true, 0);
                break;
            }
            case 'C': cleanReq = 2; break;
            case 'X': cleanReq = 3; break;
            case 'Z': cleanReq = 9; break;
            case 'P': case 'V': pending = c; break;
            case 'H': settings.hold = !settings.hold; redraw = true; break;
            case 'K': settings.dark = !settings.dark; redraw = true; break;
            case 'F': settings.flipped = !settings.flipped; redraw = true; break;
            case 'M': settings.moverOn = !settings.moverOn; redraw = true; break;
            }
        }
#endif
        // periodic content: typing progress, mover countdown, clock
        uint32_t period = 0;
        if (phase.load() == Phase::Typing) period = 1000;
        else if (page == P_MOVER) period = 1000;
        else if (phase.load() == Phase::Sent) period = 2000;
        if (period && now - lastPeriodic >= period) { lastPeriodic = now; redraw = true; }
        time_t t = time(nullptr);
        struct tm lt;
        localtime_r(&t, &lt);
        if (lt.tm_min != lastMinute) { lastMinute = lt.tm_min; redraw = true; statsRollDay(); }

        // nightly / laptop-asleep cascade clean
        bool idle = phase.load() != Phase::Typing;
        bool nightly = rtcValid() && lt.tm_hour == 3 && lt.tm_min == 0 && now - lastNightly > 3600000UL;
        if (idle && (settings.refresh == REFRESH_5MIN || settings.refresh == REFRESH_NIGHTLY) && nightly) { lastNightly = now; cleanReq = 3; }
        if (idle && settings.refresh == REFRESH_5MIN && now - lastFive >= 300000UL) { lastFive = now; if (smallUpdates) cleanReq = 2; }

        if (calibUntil) {  // hold the calibration screen until a tap or 3 minutes
            int16_t tx, ty;
            if (touchRead(tx, ty) || (int32_t)(millis() - calibUntil) > 0) { calibUntil = 0; render(); deepClean(); }
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        int cr = cleanReq.exchange(0);
        if (cr == 9) { runCalibration(); continue; }
        if (cr == 3) { render(); deepClean(); continue; }
        if (cr) { render(); shutterClean(); lastTouchMs = millis(); continue; }

        if (redraw.exchange(false)) {
            bool full = false;
            Phase vp = phase.load();
            int view = vp == Phase::Typing ? 100 : vp == Phase::Sent ? 101 : standbyNow() ? 102 : (int)page;
            if (view != shownView) {
                shownView = view;
                full = true;
            }
            if (settings.flipped != lastFlip) { lastFlip = settings.flipped; full = true; }
            if (settings.dark != lastDark) { lastDark = settings.dark; full = true; }
            render();
            showFrame(full);
        }
        vTaskDelay(pdMS_TO_TICKS(30));
    }
}

void uiBegin() {
    xTaskCreatePinnedToCore(uiTask, "ui", 8192, nullptr, 1, nullptr, 1);
}
