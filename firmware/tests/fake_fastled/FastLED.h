// Stand-in for FastLED and the Arduino core, for running firmware/djr3x_leds/chest.cpp and
// head.cpp on the Mac (firmware/tests/test_chest_transitions.py, test_head_charge.py). It
// has what they use and no more. Fades, blends and the pattern timers follow FastLED's arithmetic; hue-to-colour
// and the sine are approximations, so rainbow colours here are not the strip's.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

typedef uint8_t byte;
typedef uint8_t fract8;
using std::max;
using std::min;
#define TWO_PI 6.283185307179586f
#define PI 3.141592653589793f
#define PROGMEM
#define memcpy_P memcpy
#define constrain(x, lo, hi) ((x) < (lo) ? (lo) : ((x) > (hi) ? (hi) : (x)))

extern uint32_t fake_ms;     // the test's clock
extern uint32_t fake_seed;   // state of its random numbers
inline uint32_t millis() { return fake_ms; }
inline void delay(uint32_t ms) { fake_ms += ms; }
inline uint32_t fake_rand() {
    fake_seed ^= fake_seed << 13;  fake_seed ^= fake_seed >> 17;  fake_seed ^= fake_seed << 5;
    return fake_seed;
}
inline uint8_t  random8()               { return (uint8_t)(fake_rand() >> 8); }
inline uint8_t  random8(uint8_t limit)  { return limit ? (uint8_t)((fake_rand() >> 8) % limit) : 0; }
inline uint16_t random16()              { return (uint16_t)(fake_rand() >> 8); }
inline uint16_t random16(uint16_t limit){ return limit ? (uint16_t)((fake_rand() >> 8) % limit) : 0; }
inline long     random(long lo, long hi){ return hi > lo ? lo + (long)((fake_rand() >> 8) % (uint32_t)(hi - lo)) : lo; }
inline long     random(long limit)      { return random(0, limit); }

inline uint8_t scale8(uint8_t value, uint8_t scale) { return (uint8_t)(((uint16_t)value * (1 + (uint16_t)scale)) >> 8); }
inline uint8_t qadd8(uint8_t a, uint8_t b) { const unsigned sum = a + b; return sum > 255 ? 255 : (uint8_t)sum; }
inline uint8_t sin8(uint8_t theta) { return (uint8_t)lroundf(127.5f + 127.5f * sinf(theta * TWO_PI / 256.0f)); }
inline uint8_t ease8InOutQuad(uint8_t i) {
    uint8_t j = i;
    if (j & 0x80) j = 255 - j;
    uint8_t out = (uint8_t)(scale8(j, j) << 1);
    if (i & 0x80) out = 255 - out;
    return out;
}

struct CHSV {
    uint8_t h, s, v;
    CHSV(uint8_t hue, uint8_t sat, uint8_t val) : h(hue), s(sat), v(val) {}
};

struct CRGB {
    uint8_t r = 0, g = 0, b = 0;
    enum HTMLColorCode : uint32_t { Black = 0x000000, White = 0xFFFFFF };
    CRGB() = default;
    CRGB(uint8_t red, uint8_t green, uint8_t blue) : r(red), g(green), b(blue) {}
    CRGB(uint32_t code) : r((code >> 16) & 255), g((code >> 8) & 255), b(code & 255) {}
    CRGB(HTMLColorCode code) : CRGB((uint32_t)code) {}
    CRGB(const CHSV &c) {
        const float sector = c.h * 6.0f / 256.0f, f = sector - floorf(sector);
        const float v = c.v, p = v * (1 - c.s / 255.0f), q = v * (1 - c.s / 255.0f * f), t = v * (1 - c.s / 255.0f * (1 - f));
        const float table[6][3] = {{v, t, p}, {q, v, p}, {p, v, t}, {p, q, v}, {t, p, v}, {v, p, q}};
        const float *rgb = table[(int)sector % 6];
        r = (uint8_t)rgb[0];  g = (uint8_t)rgb[1];  b = (uint8_t)rgb[2];
    }
    CRGB &operator+=(const CRGB &o) { r = qadd8(r, o.r);  g = qadd8(g, o.g);  b = qadd8(b, o.b);  return *this; }
    CRGB &operator|=(const CRGB &o) { r = max(r, o.r);  g = max(g, o.g);  b = max(b, o.b);  return *this; }
    CRGB &nscale8(uint8_t scale) { r = scale8(r, scale);  g = scale8(g, scale);  b = scale8(b, scale);  return *this; }
    CRGB &fadeToBlackBy(uint8_t amount) { return nscale8(255 - amount); }
    void maximizeBrightness(uint8_t limit = 255) {
        const uint8_t top = max(r, max(g, b));
        if (top == 0) return;
        const uint16_t factor = ((uint16_t)limit * 256) / top;
        r = (uint8_t)((r * factor) / 256);  g = (uint8_t)((g * factor) / 256);  b = (uint8_t)((b * factor) / 256);
    }
};

inline void fill_solid(CRGB *leds, int count, const CRGB &colour) { for (int i = 0; i < count; i++) leds[i] = colour; }
inline void fill_rainbow(CRGB *leds, int count, uint8_t hue, uint8_t step) {
    for (int i = 0; i < count; i++) leds[i] = CHSV((uint8_t)(hue + i * step), 240, 255);
}
inline void fadeToBlackBy(CRGB *leds, int count, uint8_t amount) { for (int i = 0; i < count; i++) leds[i].fadeToBlackBy(amount); }
inline uint8_t blend8(uint8_t a, uint8_t b, uint8_t amountOfB) {
    return (uint8_t)(((uint16_t)a * (255 - amountOfB) + (uint16_t)b * amountOfB + 127) / 255);
}
inline CRGB blend(const CRGB &a, const CRGB &b, fract8 amountOfB) {
    return CRGB(blend8(a.r, b.r, amountOfB), blend8(a.g, b.g, amountOfB), blend8(a.b, b.b, amountOfB));
}

inline uint16_t beat16(uint16_t bpm, uint32_t timebase = 0) {
    return (uint16_t)((((uint64_t)(millis() - timebase)) * ((uint32_t)bpm << 8) * 280) >> 16);
}
inline uint8_t beat8(uint16_t bpm, uint32_t timebase = 0) { return beat16(bpm, timebase) >> 8; }
inline uint8_t beatsin8(uint16_t bpm, uint8_t low = 0, uint8_t high = 255, uint32_t timebase = 0, uint8_t phase = 0) {
    return low + scale8(sin8(beat8(bpm, timebase) + phase), high - low);
}
inline uint16_t beatsin16(uint16_t bpm, uint16_t low = 0, uint16_t high = 65535) {
    const float wave = 0.5f + 0.5f * sinf(beat16(bpm) * TWO_PI / 65536.0f);
    return (uint16_t)(low + wave * (high - low));
}

struct TProgmemPalette16 {};
static const TProgmemPalette16 PartyColors_p;
struct CRGBPalette16 { CRGBPalette16(const TProgmemPalette16 &) {} };
inline CRGB ColorFromPalette(const CRGBPalette16 &, uint8_t index, uint8_t brightness) { return CHSV(index, 255, brightness); }

#define EVERY_N_MILLISECONDS(N) \
    static uint32_t every_n_last = 0; \
    if (millis() - every_n_last >= (N) && ((every_n_last = millis()), true))

struct WS2811 {};
struct WS2812B {};
enum EOrder { RGB, GRB };
enum LEDColorCorrection { TypicalLEDStrip };
void fake_show(const CRGB *leds, int count, uint8_t brightness);   // the test records the frame
struct CLEDController {
    CRGB *leds = nullptr;
    int count = 0;
    CLEDController &setCorrection(LEDColorCorrection) { return *this; }
    void setDither(uint8_t) {}
    void showLeds(uint8_t brightness) { fake_show(leds, count, brightness); }
};
struct FakeFastLED {
    CLEDController controller;
    template <typename CHIP, int PIN, EOrder ORDER> CLEDController &addLeds(CRGB *leds, int count) {
        controller.leds = leds;
        controller.count = count;
        return controller;
    }
};
extern FakeFastLED FastLED;
