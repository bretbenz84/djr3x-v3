/*
 * chest.cpp — DJ-R3X chest LED panels on the combined LED board.
 * Ported from V2 arduino/chest_nano/chest_nano.ino (SHA-256 1619abfe...).
 *
 * Port note: the show() throttles, repeated-command tolerance and watchdogs below were
 * written for the Nano, where each show() blocked interrupts and lost serial bytes and
 * RAM was nearly full. Neither holds on the ESP32; the logic is kept unchanged so the
 * lights look the same, and comments about dropped bytes or DRAM describe the Nano.
 *
 * 98 WS2811-type pixels on CHEST_DATA_PIN. Commands are routed here by
 * djr3x_leds.ino, bare or with the C: prefix; the list is above command().
 *
 * Transitions (owner request, November 14, 2026; not in V2): a change of state is
 * blended from the frame that was showing instead of snapping, the charge meter is
 * burned up by a spark that then starts the startup sparkle, and the panel comes on
 * piece by piece under the last startup sparks. See "Transitions" below.
 *
 * Startup paced to Rex's own start (owner request, November 21, 2026): the host says how
 * long it expects to take and when it is ready; under the sparks the three bars rise and
 * struggle to stay on, and lock full at READY. See "Startup rise" below.
 */

#include <FastLED.h>
#include <math.h>
#include "pins.h"
#include "chest.h"

namespace chest {

void RandomEyes();
void RandomBlocks2();
void rainbow();
void rainbowWithGlitter();
void addGlitter(fract8 chanceOfGlitter);
void addGlitter4(fract8 chanceOfGlitter);
void confetti();
void ConfettiRedWhite();
void bpm();
void juggle();
void AllRed();
void AllGreen();
void ShortCircuit();
void LEDsOff();
void sleepBreath();
void angryFlash();
void complimentFlash();
void excitedPulse();
void sadSigh();
void happyBounce();
void curiousScan();
void surprisedBurst();
void proudRise();
void skepticalStare();
void sympathyBeat();
void chargeGauge();
void startupShow();
void nextPattern();
void LEDBlockOn(uint8_t LEDStart, CRGB Color, int Brightness);


// How many NeoPixels are attached
#define NUM_LEDS 98
#define BRIGHTNESS 90

// Setup the LED Matrix
#define LED_TYPE    WS2811
#define COLOR_ORDER GRB
#define FRAMES_PER_SECOND  30

#define ARRAY_SIZE(A) (sizeof(A) / sizeof((A)[0]))

#define DECAYTIME 80

// define the 4 LED block starting LED numbers
#define PanelAStart 0
#define PanelA1 PanelAStart + 12
#define PanelA2 PanelA1 + 12
#define PanelA3 PanelA2 + 4

#define PanelBStart PanelAStart + 32
#define PanelB1 PanelBStart + 8
#define PanelB2 PanelB1 + 4
#define PanelB3 PanelB2 + 4

#define PanelCStart PanelBStart + 32
#define PanelC1 PanelCStart + 20
#define PanelC2 PanelC1 + 4
#define PanelC3 PanelC2 + 4

uint16_t IntervalTime[NUM_LEDS];
unsigned long LEDMillis[NUM_LEDS];
bool LEDOn[NUM_LEDS];
const uint8_t StartLEDNum[9] = {PanelA1, PanelA2, PanelA3, PanelB1, PanelB2, PanelB3, PanelC1, PanelC2, PanelC3};
uint8_t Bar1Length = 4;
uint8_t Bar2Length = 4;
uint8_t Bar3Length = 4;

CRGB DJLEDs[NUM_LEDS];              // the running pattern draws here
static CRGB shown[NUM_LEDS];        // what the strip is showing, master brightness applied
static CRGB fadeFrom[NUM_LEDS];     // the frame showing when the current blend began
static CRGB sparks[NUM_LEDS];       // startup sparks, added on top of the pattern

// This strip has its own controller and master brightness. The head shares the
// FastLED singleton, so the library's board-wide show, brightness and clear
// would act on both strips; nothing in this file may call them.
static CLEDController *strip = nullptr;
static uint8_t masterBrightness = BRIGHTNESS;
static void compose();
static inline void show()     { compose(); strip->showLeds(255); }
static inline void clearAll() { fill_solid(DJLEDs, NUM_LEDS, CRGB::Black); }
#define BLOCKBRIGHTNESS 225

uint8_t LEDBrightness[NUM_LEDS] = { BRIGHTNESS,BRIGHTNESS };
uint8_t LEDMinBrightness[NUM_LEDS] = { BRIGHTNESS,BRIGHTNESS };

// Command loop processing times
unsigned long previousMillis = 0;
unsigned long interval = 5;

unsigned long LEDUpdateMillis = 0;
unsigned long LEDUpdateInterval = 20;

unsigned long FadeMillis = 0;
unsigned long FadeInterval = 0;

uint16_t DecayTime = DECAYTIME;

#define cRED 0xFF0000
#define cBLUE 0x0000FF
#define cWHITE 0xFFFFFF
#define cGOLD 0xFFDD88

#define cRED2 0x500000
#define cBLUE2 0x000055
#define cWHITE2 0x608888

const CRGB SmallLEDColors[9] = { cRED2, cRED2, cWHITE2, cWHITE2, cBLUE2, cBLUE2, cRED2, cWHITE2, cWHITE2 };
const CRGB BlockLEDColors[4] = { cRED, cWHITE, cGOLD, cBLUE };

// ---------------------------------------------------------------------------
// Chest mode
// ---------------------------------------------------------------------------
enum ChestMode : uint8_t {
    CM_STARTUP,       // host-requested: startup sparks, the panel comes on under the last of them, then IDLE
    CM_IDLE,          // default: RandomBlocks2 at normal brightness
    CM_ACTIVE,        // Rex awake: RandomBlocks2 brighter
    CM_SPEAK_NEUTRAL, // speaking neutral: RandomBlocks2
    CM_SPEAK_EXCITED, // speaking excited: racing gold chases + rapid colour pops
    CM_SPEAK_SAD,     // speaking sad: slow blue sighs (draining bars, breathing blocks)
    CM_SPEAK_ANGRY,   // speaking angry: red alert (scanner bars + alarm blocks + slams)
    CM_SPEAK_HAPPY,   // speaking happy: bouncing gold heads + cheery pops + confetti
    CM_SPEAK_CURIOUS,     // curious: violet heads looking the bars over, one block lit in turn
    CM_SPEAK_SURPRISED,   // surprised: a burst from the middle of every bar, then a flicker that settles
    CM_SPEAK_PROUD,       // proud: bars fill to the top in gold and stay full, blocks steady
    CM_SPEAK_SKEPTICAL,   // skeptical: the panel stops; flat dim lines, one raised eyebrow
    CM_SPEAK_SYMPATHETIC, // sympathetic: a soft rose heartbeat
    CM_COMPLIMENT,    // reacting to a compliment: blue glow + gold/white sparkles (self-ends ~2.5s)
    CM_FADEOFF,       // shutdown: blend the frame that was showing to black, then OFF
    CM_SLEEP,         // sleep: very dim slow red breath
    CM_CHARGE,        // Rex off: contiguous 24-LED SOC meter; upward pulse while charging
    CM_OFF,           // all off
    CM_MANUAL,        // NEXT command: cycle gPatterns[] manually
};
ChestMode chestMode = CM_OFF;
bool pridePanels = false;
uint32_t prideRefreshMs = 0;
#define PRIDE_TIMEOUT_MS 10000UL

bool prideVisible() {
    return pridePanels && (chestMode == CM_IDLE || chestMode == CM_ACTIVE ||
        (chestMode >= CM_SPEAK_NEUTRAL && chestMode <= CM_SPEAK_HAPPY));
}

void prideRainbow() {
    bool speaking = chestMode >= CM_SPEAK_NEUTRAL && chestMode <= CM_SPEAK_HAPPY;
    uint32_t now = millis();
    uint8_t phase = now / (speaking ? 12UL : 32UL);
    uint8_t bright = speaking ? 225 : 100 + scale8(sin8(now / 24UL), 55);
    for (uint8_t i = 0; i < NUM_LEDS; i++) {
        DJLEDs[i] = CHSV(phase + (uint16_t)i * 256 / NUM_LEDS, 255, bright);
    }
}
uint32_t complimentStartMs = 0;   // millis() when CM_COMPLIMENT began (self-timeout)
uint8_t chargeSoc = 0;             // 0..100, supplied by off-state battery monitor
bool chargeConnected = false;
uint32_t chargeIntroStartMs = 0;   // millis() when CM_CHARGE was entered (intro sweep)
#define CHARGE_INTRO_MS 2000

// FADEOFF: blend the frame that was showing to black in a straight line over
// CHEST_FADEOFF_MS, then go fully OFF — a lifelike "powering down" fade.
#define CHEST_FADEOFF_MS 4000

// FADEOFF:{soc}:{0|1} (owner, November 15): Rex's shutdown hands over to the charge
// meter. The panel fades the same way, but under it the meter sweeps up during the last
// CHARGE_INTRO_MS of the fade, so the meter stands where the panel went dark. The level
// is Rex's last battery reading; the off-state battery monitor's own CHARGE confirms it
// a few seconds later. Unconfirmed after CHARGE_UNCONFIRMED_MS (no monitor has the
// port), the meter fades out: a level nobody updates must not stay up for hours.
#define CHARGE_UNCONFIRMED_MS 60000UL
static bool     chargeUnconfirmed = false;
static uint32_t chargeLinkedAt    = 0;

// ---------------------------------------------------------------------------
// Transitions
// ---------------------------------------------------------------------------
//
// Blend: every change of state copies the frame on the strip (fadeFrom) and mixes
// from it into the new pattern over a set time, eased at both ends. FADEOFF is the
// same blend into black, in a straight line. OFF alone is instant.
//
// Panel look: IDLE, ACTIVE and SPEAK:neutral all run RandomBlocks2, so moving among
// them changes brightness only. Arriving from any other pattern wipes the old
// picture from the pattern buffer (the blend still shows it leaving) and starts the
// panel at its ordinary density, so no stale colour is left behind.
//
// Waking from the charge meter: the meter brightens, then a white spark climbs it
// from the bottom, each pixel it passes left to die away; when it reaches the fill
// line the startup sparks begin.
//
// Startup into the panel: during the last BLOCKS_LEAD_MS before the planned end the
// nine blocks come on one at a time in a random order, each catching with a short
// stutter. The sparks sit on their own layer so the panel can run beneath. The three
// bars are the startup rise's (below) until it locks, then the panel's.
// IDLE, ACTIVE, SPEAK:neutral and SPEAK_STOP arriving during startup do not cut it
// short; startup ends in whichever of them came last.
#define FADE_STARTUP_MS   300   // into startup: old brightness to the startup level
#define FADE_QUICK_MS     200   // into a speaking pattern or the sparkle: on with the voice
#define FADE_SETTLE_MS    600   // back to the panel, and brightness changes within it
#define FADE_SLOW_MS     1000   // into sleep
#define FADE_CHARGE_MS    400   // into the charge meter
#define FADE_MANUAL_MS    300   // NEXT
#define FADE_PRIDE_MS     600   // rainbow on or off

static uint32_t fadeStartMs = 0;
static uint16_t fadeMs      = 0;       // 0: nothing is blending
static bool     fadeLinear  = false;

static void beginFade(uint16_t ms, bool linear = false) {
    memcpy(fadeFrom, shown, sizeof(shown));
    fadeStartMs = millis();
    fadeMs      = ms;
    fadeLinear  = linear;
}

static inline bool panelMode(ChestMode m) {
    return m == CM_IDLE || m == CM_ACTIVE || m == CM_SPEAK_NEUTRAL;
}
// Modes in which the two chest lights are RandomEyes' steady gold flicker.
static inline bool awakeMode(ChestMode m) {
    return m == CM_STARTUP || m == CM_COMPLIMENT || m == CM_IDLE || m == CM_ACTIVE ||
        (m >= CM_SPEAK_NEUTRAL && m <= CM_SPEAK_SYMPATHETIC);
}

// The rainbow was drawn on the last pattern step. A change of state that has
// already blended and wiped for it sets this itself, so tick() does not blend again.
static bool prideShown = false;
static uint8_t chargeFilled();

// Waking from the charge meter.
#define IGNITE_HOLD_MS 300             // the meter brightens before the spark sets off
#define IGNITE_STEP_MS 38              // per meter pixel: a full meter burns in about 0.9 s
static bool     igniting      = false;
static uint8_t  igniteFilled  = 0;
static uint32_t igniteStartMs = 0;

// Startup sparks and the blocks coming on beneath them.
#define BOOT_ELEMENTS    12            // bars A, B, C (the startup rise's) then the nine blocks
#define BOOT_BLOCKS       9
#define BOOT_STAGGER_MS  190           // one block starts about this often
#define BOOT_HURRY_MS     60           // ... when READY has already come
#define BOOT_JITTER_MS   90
#define BOOT_CATCH_MS    330           // length of one block's stutter
#define SPARK_TAIL_MS    600           // the last sparks finish dying after startup ends
static bool      sparksLive  = false;
static uint32_t  sparksEndMs = 0;
static bool      booting     = false;  // blocks are still catching: compose() gates them
static bool      bootBegun   = false;  // the panel is running under the sparks
static uint32_t  bootStartMs = 0;
static uint16_t  bootSpanMs  = 0;
static uint16_t  bootOnlineAt[BOOT_ELEMENTS];   // ms after bootStartMs
static bool      bootOnline[BOOT_ELEMENTS];
static ChestMode startupLandMode       = CM_IDLE;
static uint8_t   startupLandBrightness = BRIGHTNESS;

// Startup rise (owner request, November 21, 2026: "the 3 columns of 8 LEDs would slowly
// rise and struggle to stay on", lined up with the real startup time).
//
// STARTUP:<ms> carries the time the host expects to need before it is ready, and READY
// says it is. The sparks thin out over that time. Under them each bar lights from the
// bottom, at its own pace, and struggles: its top light never holds steady and sometimes
// goes out, the next one up tries to catch, the bar browns out for a moment, loses a
// light or two and climbs back. No bar holds its eighth light before READY. READY fills
// all three at once, bright, and over FINALE_MS they ease to steady; then the ordinary
// panel takes the bars over with a blend.
//
// Bare STARTUP (the LED menu) times itself: STARTUP_DEFAULT_MS, then it locks. With a
// host time and no READY the bars keep struggling at the top READY_GRACE_MS longer and
// then lock anyway, so a host that died cannot leave the chest in startup for good.
#define STARTUP_DEFAULT_MS LEDS_STARTUP_DEFAULT_MS   // shared with the mouth: pins.h
#define STARTUP_MIN_MS     LEDS_STARTUP_MIN_MS
#define STARTUP_MAX_MS     LEDS_STARTUP_MAX_MS
#define READY_GRACE_MS     LEDS_READY_GRACE_MS
#define FINALE_MS          LEDS_FINALE_MS
#define BLOCKS_LEAD_MS      3000UL     // the blocks begin this long before the planned end
#define SPARK_WAIT_MS        380UL     // a sparse spark this often while waiting past the planned end
#define SPARK_SUM           3160UL     // 0 + 1 + ... + 79: the 80 bursts' gaps add up to the span
static uint32_t startupAt     = 0;     // millis() when STARTUP arrived
static uint32_t startupSpanMs = STARTUP_DEFAULT_MS;
static bool     startupHosted = false; // the host gave a time and will say READY
static bool     riseBegun     = false; // false while a spark still burns the charge meter
static uint32_t riseStartMs   = 0;
static bool     readySaid     = false; // READY, or its stand-in when it never comes
static uint32_t readyMs       = 0;
static uint8_t  riseLevel[3];          // lights on from the bottom of each bar, 0..8
static uint32_t riseMoveAt[3];         // when the bar may next gain a light
static uint32_t riseSlipAt[3];         // when it next loses ground
static uint32_t riseDipAt[3];          // when it next browns out
static uint32_t riseDipUntil[3];
// Bar A red, bar B blue, bar C white: the panel's own three colours, brighter than the
// ordinary panel's bars so the rise reads under the sparks.
static const CRGB RISE_BODY[3] = { CRGB(0x80, 0x00, 0x00), CRGB(0x00, 0x14, 0x90), CRGB(0x70, 0x98, 0x98) };
static const CRGB RISE_TIP[3]  = { CRGB(0xFF, 0x38, 0x10), CRGB(0x40, 0x70, 0xFF), CRGB(0xFF, 0xFF, 0xFF) };
static const uint8_t RISE_PACE[3] = { 10, 8, 9 };   // A reaches its seventh light first, B last

static void endStartupLayers() {
    igniting = booting = bootBegun = sparksLive = riseBegun = readySaid = false;
    fill_solid(sparks, NUM_LEDS, CRGB::Black);
}

static inline uint8_t flick8(uint32_t a, uint32_t b) { return ledsFlick8(a, b); }

// Which bar or block a pixel belongs to: 0-2 bars A-C, 3-11 the blocks, 255 none.
static uint8_t elementOf(uint8_t pixel) {
    if (pixel >= 96) return 255;
    if ((pixel & 31) < 8) return pixel >> 5;
    for (uint8_t k = 0; k < 9; k++)
        if (pixel >= StartLEDNum[k] && pixel < StartLEDNum[k] + 4) return 3 + k;
    return 255;
}

// How much of an element shows while the panel is coming on: dark until its
// moment, then a tube catching (on, out, half, out, on, dip) and steady.
static uint8_t bootGate(uint8_t element, uint32_t now) {
    const uint32_t since = now - bootStartMs;
    if (since < bootOnlineAt[element]) return 0;
    const uint32_t dt = since - bootOnlineAt[element];
    if (dt <  50) return 255;
    if (dt < 120) return 0;
    if (dt < 170) return 110;
    if (dt < 230) return 0;
    if (dt < 300) return 255;
    if (dt < BOOT_CATCH_MS) return 60;
    return 255;
}

// Build the frame for the strip: pattern, gated while the panel comes on, plus the
// spark layer, at master brightness, mixed with the outgoing frame while blending.
static void compose() {
    const uint32_t now = millis();
    uint8_t mix = 255;                 // share of the new picture
    if (fadeMs) {
        const uint32_t elapsed = now - fadeStartMs;
        if (elapsed >= fadeMs) {
            fadeMs = 0;
        } else {
            mix = (uint8_t)(elapsed * 255UL / fadeMs);
            if (!fadeLinear) mix = ease8InOutQuad(mix);
        }
    }
    for (uint8_t i = 0; i < NUM_LEDS; i++) {
        CRGB c = DJLEDs[i];
        if (booting) {
            const uint8_t element = elementOf(i);
            if (element >= 3 && element != 255) c.nscale8(bootGate(element, now));   // bars are the rise's
        }
        if (sparksLive) c += sparks[i];
        c.nscale8(masterBrightness);
        shown[i] = (mix == 255) ? c : blend(fadeFrom[i], c, mix);
    }
}

// Start the ordinary panel at once at its usual density: bars A and C redraw on the
// next step, about half of bar B and of the blocks are lit, the rest come on soon.
static void panelSeed() {
    const uint32_t now = millis();
    IntervalTime[PanelAStart] = 0;  LEDMillis[PanelAStart] = now - 1;
    IntervalTime[PanelCStart] = 0;  LEDMillis[PanelCStart] = now - 1;
    for (uint8_t i = 0; i < 8; i++) {
        const uint8_t pos = PanelBStart + i;
        LEDMillis[pos] = now;
        if (random8() < 120) {
            DJLEDs[pos] = SmallLEDColors[random(0, 9)];
            LEDOn[pos] = 1;  IntervalTime[pos] = random(500, 2500);
        } else {
            LEDOn[pos] = 0;  IntervalTime[pos] = random(0, 1500);
        }
    }
    for (uint8_t k = 0; k < 9; k++) {
        const uint8_t pos = StartLEDNum[k];
        LEDMillis[pos] = now;
        if (random8() < 128) {
            LEDBlockOn(pos, BlockLEDColors[random(0, 4)], BLOCKBRIGHTNESS);
            LEDOn[pos] = 1;  IntervalTime[pos] = random(200, 1500);
        } else {
            LEDOn[pos] = 0;  IntervalTime[pos] = random(0, 1000);
        }
    }
}

// Wipe the outgoing pattern's picture for the mode about to run. The two chest
// lights keep their flicker when both modes are awake; otherwise they start gold
// for an awake mode (so they fade in with the blend) and dark for any other.
static void freshCanvas(ChestMode mode, bool keepLights) {
    fill_solid(DJLEDs, 96, CRGB::Black);
    if (!keepLights) DJLEDs[96] = DJLEDs[97] = awakeMode(mode) ? CRGB(cGOLD) : CRGB(CRGB::Black);
    if (panelMode(mode)) panelSeed();
}

// When the pattern now running was entered: the patterns with an opening (the surprise's burst,
// pride's rise, the raised eyebrow) time themselves from it.
static uint32_t modeEnteredMs = 0;

// Change state with a blend. Panel modes arriving during startup wait for it to end.
static void enter(ChestMode mode, uint8_t brightness, uint16_t ms) {
    if (chestMode == CM_STARTUP && panelMode(mode)) {
        startupLandMode = mode;
        startupLandBrightness = brightness;
        return;
    }
    const bool samePattern = !prideShown &&
        (mode == chestMode || (panelMode(mode) && panelMode(chestMode)));
    if (samePattern && brightness == masterBrightness) { chestMode = mode; return; }
    beginFade(ms);
    if (!samePattern) {
        endStartupLayers();
        freshCanvas(mode, awakeMode(chestMode) && awakeMode(mode) && !prideShown);
        modeEnteredMs = millis();
    }
    masterBrightness = brightness;
    chestMode = mode;
    prideShown = prideVisible();
}

// setup() function -- runs once at startup --------------------------------

void begin() {
	strip = &FastLED.addLeds<LED_TYPE, CHEST_DATA_PIN, COLOR_ORDER>(shown, NUM_LEDS)
		.setCorrection(TypicalLEDStrip);
	// The board-wide show turned dithering off at this frame rate; showLeds() does not.
	strip->setDither(0);
	masterBrightness = BRIGHTNESS;
	clearAll();
	show();

	// Seed the Array
	for (byte x = 0; x < NUM_LEDS; x++) {
		IntervalTime[x] = random16(3000);
		LEDMillis[x] = millis();
		LEDOn[x] = 0;
	}

	// Stay dark until the host explicitly sends STARTUP. This keeps the panels
	// off after a serial disconnect/reset during program shutdown.
	DecayTime = 80;
	FadeInterval = 0;
	FadeMillis = millis();
	chestMode = CM_OFF;
}

// List of patterns to cycle through.  Each is defined as a separate function below.
typedef void (*SimplePatternList[])();
SimplePatternList gPatterns = {
  LEDsOff,	// 0
  RandomBlocks2,
  AllRed,	// 2
  AllGreen,	//3
  ShortCircuit,	// 4
  ConfettiRedWhite, // 10
  rainbow,
  rainbowWithGlitter,
  confetti,
  juggle,
  bpm
};

uint8_t gCurrentPatternNumber = 1; // Index number of which pattern is current



uint8_t gHue = 0; // rotating "base color" used by many of the patterns
uint8_t gSat = 0; // saturation value
bool updown = 0;

// ---------------------------------------------------------------------------
// Mode dispatcher — called every loop tick
// ---------------------------------------------------------------------------
//
// CM_STARTUP: runs startupShow(), which ends in the panel mode last asked for.
// CM_MANUAL:  uses gPatterns[gCurrentPatternNumber]() so NEXT still works.
// All other modes call a specific pattern function, with masterBrightness
// set at command time (in command()).

void runCurrentMode() {
	if (prideVisible()) { prideRainbow(); return; }
	switch (chestMode) {
		case CM_STARTUP:
			startupShow();
			break;

		case CM_IDLE:
		case CM_SPEAK_NEUTRAL:
			RandomBlocks2();
			break;

		case CM_ACTIVE:
			RandomBlocks2();
			break;

		case CM_SPEAK_EXCITED:
			excitedPulse();
			break;

		case CM_SPEAK_SAD:
			sadSigh();
			break;

		case CM_SPEAK_ANGRY:
			angryFlash();
			break;

		case CM_SPEAK_HAPPY:
			happyBounce();
			break;

		case CM_SPEAK_CURIOUS:
			curiousScan();
			break;

		case CM_SPEAK_SURPRISED:
			surprisedBurst();
			break;

		case CM_SPEAK_PROUD:
			proudRise();
			break;

		case CM_SPEAK_SKEPTICAL:
			skepticalStare();
			break;

		case CM_SPEAK_SYMPATHETIC:
			sympathyBeat();
			break;

		case CM_COMPLIMENT:
			complimentFlash();
			// Self-terminate so a missed follow-up command can't leave the panel
			// stuck flashing; hand back to the awake/active pattern.
			if (millis() - complimentStartMs > 2500UL) enter(CM_ACTIVE, 200, FADE_SETTLE_MS);
			break;

		case CM_FADEOFF:
			// Nothing is drawn: the blend takes the frame that was showing to black.
			if (millis() - fadeStartMs >= CHEST_FADEOFF_MS) chestMode = CM_OFF;
			break;

		case CM_SLEEP:
			sleepBreath();
			break;

		case CM_CHARGE:
			if (chargeUnconfirmed && millis() - chargeLinkedAt > CHEST_FADEOFF_MS + CHARGE_UNCONFIRMED_MS) {
				chargeUnconfirmed = false;
				enter(CM_OFF, BRIGHTNESS, FADE_SLOW_MS);
				break;
			}
			chargeGauge();
			break;

		case CM_OFF:
			LEDsOff();
			break;

		case CM_MANUAL:
			gPatterns[gCurrentPatternNumber]();
			break;
	}
}

// loop() function -- runs repeatedly as long as board is on ---------------

void tick() {
	if (pridePanels && millis() - prideRefreshMs > PRIDE_TIMEOUT_MS) pridePanels = false;

	if (millis() - previousMillis > interval) {
		previousMillis = millis();
		// The rainbow coming or going is blended like any other change; when it
		// goes, its picture is wiped so the pattern beneath starts clean.
		if (prideVisible() != prideShown) {
			beginFade(FADE_PRIDE_MS);
			if (prideShown) freshCanvas(chestMode, false);
			prideShown = !prideShown;
		}
		// Call the current mode function, updating the 'leds' array
		runCurrentMode();
		// RandomEyes only in active modes — skip during sleep/off and while fading
		// (the fade ends in black, so nothing should draw under it). November 15: and
		// under the charge meter, which clears the two lights every step, so each time
		// RandomEyes turned one on it showed for a single frame: a gold blip every few seconds.
		if (!prideVisible() && chestMode != CM_SLEEP && chestMode != CM_OFF && chestMode != CM_FADEOFF &&
		    chestMode != CM_CHARGE) RandomEyes();
		// The last startup sparks finish dying away over the panel.
		if (sparksLive && chestMode != CM_STARTUP) {
			fadeToBlackBy(sparks, NUM_LEDS, 10);
			if (millis() - sparksEndMs > SPARK_TAIL_MS) sparksLive = false;
		}
		if (booting && millis() - bootStartMs > bootSpanMs) booting = false;
	}

	if (millis() - LEDUpdateMillis > LEDUpdateInterval) {

		LEDUpdateMillis = millis();
		show();
	}


	// do some periodic updates
	EVERY_N_MILLISECONDS(20) {
		gHue++;  // slowly cycle the "base color" through the rainbow
		if (updown) {
			gSat++;
			if (gSat == 255) updown = 0;
		}
		else {
			gSat--;
			if (gSat == 0) updown = 1;
		}
	}
	// EVERY_N_SECONDS( 5 ) { nextPattern(); } // change patterns periodically

}

void nextPattern()
{
	// add one to the current pattern number, and wrap around at the end
	gCurrentPatternNumber = (gCurrentPatternNumber + 1) % ARRAY_SIZE(gPatterns);
	// skip 0
	if (gCurrentPatternNumber == 0) gCurrentPatternNumber = 1;
}

// ---------------------------------------------------------------------------
// Serial command handler
// ---------------------------------------------------------------------------
//
// Commands (newline-terminated, 115200 baud):
//   STARTUP          — startup sparks (set off by a spark up the charge meter when it
//                      was showing) with the three bars rising and struggling under them,
//                      the blocks coming on under the last sparks; times itself
//   STARTUP:{ms}     — the same, paced to the time the host expects to need; ends at READY
//   READY            — the host is ready: the bars lock full, then the ordinary panel
//   IDLE             — RandomBlocks2 at normal brightness (default)
//   ACTIVE           — RandomBlocks2 at higher brightness
//   SPEAK:{emotion}  — emotion-specific pattern:
//                        neutral  → RandomBlocks2
//                        excited  → racing gold chases + rapid colour pops, full brightness
//                        sad      → slow blue sighs, dim
//                        angry    → red alert (scanner bars, alarm blocks, slams)
//                        happy    → bouncing gold heads + cheery pops + confetti
//                        curious  → violet heads looking the bars over, one block lit in turn
//                        surprised → a burst from the middle of every bar, then a flicker that settles
//                        proud    → bars fill to the top in gold and stay full, blocks steady
//                        skeptical → the panel stops: flat dim lines, one raised eyebrow
//                        sympathetic → a soft rose heartbeat
//   SPEAK_STOP       — return to IDLE (end of speech)
//   SLEEP            — very dim slow red breathing pulse
//   CHARGE:{soc}:{0|1} — contiguous 24-LED SOC meter; final field says charger attached
//   OFF              — all LEDs off at once, the one change that is not blended
//   NEXT             — cycle to next pattern in gPatterns[]
//   COMPLIMENT       — blue glow with gold and white sparkles for 2.5 s, then ACTIVE
//   FADEOFF          — fade to black over four seconds, then off
//   FADEOFF:{soc}:{0|1} — the same fade, ending in the charge meter, which sweeps up
//                      under its last two seconds; fades out if no CHARGE confirms it
//   PRIDE:{0|1}      — rainbow over the awake patterns; renew ON within 10 seconds

void command(const char *cmd) {
    // Colour overlay only; never changes sleep/off/charge/fade state.
    if (strcmp(cmd, "PRIDE:1") == 0 || strcmp(cmd, "PRIDE:0") == 0) {
        pridePanels = cmd[6] == '1';
        prideRefreshMs = millis();
        return;
    }

	if (strcmp(cmd, "STARTUP") == 0 || strncmp(cmd, "STARTUP:", 8) == 0) {
		const uint32_t now = millis();
		const long given = cmd[7] == ':' ? atol(cmd + 8) : 0;
		// What the meter was showing is the spark's fuel; none from any other state,
		// where the old picture simply dies away under the first sparks.
		const uint8_t fuel = (chestMode == CM_CHARGE) ? chargeFilled() : 0;
		const bool lightsLit = awakeMode(chestMode) && !prideShown;
		beginFade(FADE_STARTUP_MS);
		endStartupLayers();
		if (!lightsLit) DJLEDs[96] = DJLEDs[97] = cGOLD;
		if (fuel) {
			igniting = true;
			igniteFilled = fuel;
			igniteStartMs = now + IGNITE_HOLD_MS;
		}
		sparksLive = true;
		startupHosted = given > 0;
		startupSpanMs = startupHosted ? (uint32_t)constrain(given, STARTUP_MIN_MS, STARTUP_MAX_MS) : STARTUP_DEFAULT_MS;
		startupAt = now;
		DecayTime = DECAYTIME;
		FadeInterval = 0;
		FadeMillis = now;
		gCurrentPatternNumber = 1;
		masterBrightness = BRIGHTNESS;
		startupLandMode = CM_IDLE;
		startupLandBrightness = BRIGHTNESS;
		chestMode = CM_STARTUP;
		prideShown = false;   // any rainbow dies away under the sparks

	} else if (strcmp(cmd, "READY") == 0) {
		if (chestMode == CM_STARTUP && !readySaid) { readySaid = true;  readyMs = millis(); }

	} else if (strcmp(cmd, "IDLE") == 0 || strcmp(cmd, "SPEAK_STOP") == 0) {
		enter(CM_IDLE, BRIGHTNESS, FADE_SETTLE_MS);

	} else if (strcmp(cmd, "ACTIVE") == 0) {
		enter(CM_ACTIVE, 200, FADE_SETTLE_MS);

	} else if (strncmp(cmd, "SPEAK:", 6) == 0) {
		const char *emotion = cmd + 6;
		if (strcmp(emotion, "excited") == 0) {
			enter(CM_SPEAK_EXCITED, 255, FADE_QUICK_MS);
		} else if (strcmp(emotion, "sad") == 0) {
			enter(CM_SPEAK_SAD, 55, FADE_SETTLE_MS);
		} else if (strcmp(emotion, "angry") == 0) {
			enter(CM_SPEAK_ANGRY, 255, FADE_QUICK_MS);
		} else if (strcmp(emotion, "happy") == 0) {
			// match ACTIVE's energy — happy shouldn't be dimmer than idle chat
			enter(CM_SPEAK_HAPPY, 200, FADE_QUICK_MS);
		} else if (strcmp(emotion, "curious") == 0) {
			enter(CM_SPEAK_CURIOUS, 120, FADE_QUICK_MS);
		} else if (strcmp(emotion, "surprised") == 0) {
			enter(CM_SPEAK_SURPRISED, 255, 80);   // a jolt: nearly at once
		} else if (strcmp(emotion, "proud") == 0) {
			enter(CM_SPEAK_PROUD, 200, FADE_QUICK_MS);
		} else if (strcmp(emotion, "skeptical") == 0) {
			enter(CM_SPEAK_SKEPTICAL, 60, FADE_SETTLE_MS);
		} else if (strcmp(emotion, "sympathetic") == 0) {
			enter(CM_SPEAK_SYMPATHETIC, 110, FADE_SETTLE_MS);
		} else {
			// neutral or unknown emotion
			enter(CM_SPEAK_NEUTRAL, BRIGHTNESS, FADE_SETTLE_MS);
		}

	} else if (strcmp(cmd, "SLEEP") == 0) {
		enter(CM_SLEEP, BRIGHTNESS, FADE_SLOW_MS);

	} else if (strncmp(cmd, "CHARGE:", 7) == 0) {
		int soc = 0;
		int connected = 0;
		if (sscanf(cmd + 7, "%d:%d", &soc, &connected) == 2) {
			chargeSoc = (uint8_t)constrain(soc, 0, 100);
			chargeConnected = connected != 0;
			chargeUnconfirmed = false;
			// Intro sweep only when ENTERING charge mode (handoff from main.py,
			// or first paint after a reboot) — SOC updates while already in
			// CM_CHARGE must not replay it.
			if (chestMode != CM_CHARGE) chargeIntroStartMs = millis();
			enter(CM_CHARGE, 55, FADE_CHARGE_MS);  // visible while off, never room-filling
		}

	} else if (strcmp(cmd, "OFF") == 0) {
		fadeMs = 0;
		endStartupLayers();
		masterBrightness = BRIGHTNESS;
		chestMode = CM_OFF;
		prideShown = false;
		clearAll();
		show();

	} else if (strcmp(cmd, "NEXT") == 0) {
		beginFade(FADE_MANUAL_MS);
		endStartupLayers();
		freshCanvas(CM_MANUAL, false);
		nextPattern();
		chestMode = CM_MANUAL;
		prideShown = false;

	} else if (strcmp(cmd, "COMPLIMENT") == 0) {
		complimentStartMs = millis();
		enter(CM_COMPLIMENT, 200, FADE_QUICK_MS);

	} else if (strncmp(cmd, "FADEOFF", 7) == 0 && (cmd[7] == '\0' || cmd[7] == ':')) {
		// Smoothly fade the frame that is showing to black (shutdown). Idempotent: a
		// repeat during an in-progress fade is ignored so the fade isn't restarted.
		// With a battery level the fade ends in the charge meter (see CHARGE_UNCONFIRMED_MS).
		int soc = 0;
		int connected = 0;
		const bool meter = cmd[7] == ':' && sscanf(cmd + 8, "%d:%d", &soc, &connected) == 2;
		const bool fading = chestMode == CM_FADEOFF ||
			(chestMode == CM_CHARGE && chargeUnconfirmed && millis() - chargeLinkedAt < CHEST_FADEOFF_MS);
		if (!fading) {
			beginFade(CHEST_FADEOFF_MS, true);
			endStartupLayers();
			clearAll();
			prideShown = false;
			chestMode = CM_FADEOFF;
			if (meter) {
				chargeSoc = (uint8_t)constrain(soc, 0, 100);
				chargeConnected = connected != 0;
				chargeLinkedAt = millis();
				chargeIntroStartMs = chargeLinkedAt + CHEST_FADEOFF_MS - CHARGE_INTRO_MS;
				chargeUnconfirmed = true;
				masterBrightness = 55;   // the meter's brightness; the outgoing frame keeps its own
				chestMode = CM_CHARGE;
			}
		}
	}
	// Unknown commands are silently ignored.
}

// Physical pixel for a logical gauge level, where level 0 is the BOTTOM of the
// column and level 7 the top. Each 8-pixel panel is wired TOP-DOWN — pixel 0 is the
// topmost LED — so filling from index 0 lit the gauge from the top and emptied
// downward, backwards for a battery (owner 2026-07-24: "the LEDs are inverted. The
// top are showing solid while the bottom are off"). Flip GAUGE_BOTTOM_UP to 0 if the
// panels are ever rewired the other way.
#define GAUGE_BOTTOM_UP 1
static inline uint8_t gaugePixel(uint8_t colStart, uint8_t level) {
	return colStart + (GAUGE_BOTTOM_UP ? (uint8_t)(7 - level) : level);
}

// Anchor colour ladder for the charge meter, red (bottom of the range) to blue
// (top): red -> red/orange -> orange -> yellow -> yellow/green -> green ->
// green/blue -> blue (owner spec 2026-07-24, colour thresholds kept for the
// 24-LED meter 2026-08-01). Each meter pixel's colour is a property of its
// POSITION, not of the current charge — the charge only decides how many are
// lit, so the meter always reads the same way and the level is judged by where
// it stops.
// A switch, not a CRGB table: this sketch sits at 89% DRAM and an array would
// spend DRAM, while the switch costs flash only.
static CRGB gaugeAnchorColor(uint8_t anchor) {
	switch (anchor) {
		case 0:  return CRGB(130,   0,   0);   // red
		case 1:  return CRGB(130,  22,   0);   // red/orange
		case 2:  return CRGB(130,  45,   0);   // orange
		case 3:  return CRGB(120, 100,   0);   // yellow
		case 4:  return CRGB( 60, 110,  15);   // yellow/green
		case 5:  return CRGB(  0, 120,  30);   // green
		case 6:  return CRGB(  0,  82,  90);   // green/blue
		default: return CRGB(  0,  45, 150);   // blue
	}
}

// Static colour of contiguous meter level 0..23: the 8 anchors stretched across
// 24 steps by blending adjacent anchors. Panel A (levels 0-7) lands on the
// red/orange end, panel B (8-15) the yellow/green middle, panel C (16-23) the
// green/blue end. Computed on the fly — no CRGB[24] table (DRAM is at 89%).
static CRGB gauge24Color(uint8_t level) {
	const uint16_t pos = (uint16_t)level * 7u * 256u / 23u; // 8.8 fixed point, 0..7.0
	const uint8_t  i   = (uint8_t)(pos >> 8);
	const uint8_t  f   = (uint8_t)(pos & 0xFF);
	if (i >= 7) return gaugeAnchorColor(7);
	return blend(gaugeAnchorColor(i), gaugeAnchorColor(i + 1), f);
}

// Meter level 0..23 -> physical pixel. The three first 8-pixel bars form ONE
// contiguous 24-LED meter split into thirds: panel A holds levels 0-7, B 8-15,
// C 16-23, each filling from the BOTTOM of its bar.
static inline uint8_t gauge24Pixel(uint8_t level) {
	const uint8_t columns[3] = { PanelAStart, PanelBStart, PanelCStart };
	return gaugePixel(columns[level >> 3], level & 7);
}

// Off-state charge display: one contiguous 24-LED meter across panels A/B/C
// (owner spec 2026-08-01; previously three parallel 8-LED gauges). Full pack =
// all 24 lit; as charge depletes, pixels go dark one by one from the blue end
// down, each pixel keeping its own static colour. The topmost lit pixel blinks
// until it goes off. While the charger is attached, a bright energy packet also
// climbs from the fill boundary to the top of the whole meter.
// Lit meter pixels, 0..24. Each pixel owns one twenty-fourth of the range, so
// pixel k lights the moment the charge passes (k-1)*100/24 %. That is a CEILING,
// not a round-to-nearest (rounding made the bar lag a whole LED behind the charge
// for most of the range back when this was the 8-level gauge). Ceiling of 0 is 0,
// so an empty pack is dark and any non-zero charge keeps at least the bottom red
// pixel lit.
static uint8_t chargeFilled() {
	uint8_t filled = (uint8_t)(((uint16_t)chargeSoc * 24 + 99) / 100);
	return filled > 24 ? 24 : filled;
}

void chargeGauge() {
	const uint8_t filled = chargeFilled();

	// Intro sweep on entering CM_CHARGE: instead of snapping the whole meter on
	// at once, reveal the pixels in order over CHARGE_INTRO_MS until the fill
	// line is reached. Blink and charge packet stay suppressed until it lands.
	// The sweep may be due to start later (FADEOFF:{soc}:{0|1}): the meter is dark until then.
	const int32_t elapsed = (int32_t)(millis() - chargeIntroStartMs);
	if (elapsed < CHARGE_INTRO_MS) {
		const uint8_t reveal = elapsed <= 0 ? 0 : (uint8_t)(((uint32_t)filled * (uint32_t)elapsed) / CHARGE_INTRO_MS);
		clearAll();
		for (uint8_t level = 0; level < reveal; level++)
			DJLEDs[gauge24Pixel(level)] = gauge24Color(level);
		return;
	}

	clearAll();
	for (uint8_t level = 0; level < filled; level++)
		DJLEDs[gauge24Pixel(level)] = gauge24Color(level);

	// The last lit pixel blinks — it is the one currently draining toward its
	// threshold. ~1 Hz square wave; the off phase leaves it dark.
	if (filled > 0 && ((millis() / 500UL) & 1))
		DJLEDs[gauge24Pixel(filled - 1)] = CRGB::Black;

	if (!chargeConnected) return;
	if (filled < 24) {
		const uint8_t travel = 24 - filled;
		const uint8_t phase = (millis() / 170UL) % (travel + 2); // two-beat gap
		if (phase < travel)   // climbs UPWARD from the fill line, crossing panels
			DJLEDs[gauge24Pixel(filled + phase)] = CRGB(80, 190, 255);
	}
	// At full there is nowhere left to climb; the blinking top blue pixel
	// already marks the state.
}

// Turns on block of 4 LEDs based on start number
void LEDBlockOn(uint8_t LEDStart, CRGB Color, int Brightness)
{
	byte i;
	for (i = 0; i < 4; i++) {
		DJLEDs[LEDStart + i] = Color;
	}
}

// Random Eyes
// Simple mostly solid eyes with a bit of flicker to them.
void RandomEyes()
{
  byte i;
  byte y;
  int pos;

  for (pos = 96; pos < NUM_LEDS; pos++) {
    if (!LEDOn[pos]) {	// Fade LEDs up or down
        DJLEDs[pos].maximizeBrightness(LEDBrightness[pos]);
        if (LEDBrightness[pos] < BRIGHTNESS) LEDBrightness[pos]++;
//      DJLEDs[pos].fadeToBlackBy(8);
    }
    else {
        DJLEDs[pos].maximizeBrightness(LEDBrightness[pos]);
        if (LEDBrightness[pos] > LEDMinBrightness[pos]) LEDBrightness[pos]--;
    }
    if (millis() - LEDMillis[pos] > IntervalTime[pos]) {
      if (!LEDOn[pos]) { // LED Off - turn in on
          DJLEDs[pos] = cGOLD;
        IntervalTime[pos] = random(200, 1600);
        LEDMillis[pos] = millis();
        LEDOn[pos] = 1;
        LEDMinBrightness[pos] = random(BRIGHTNESS * 0.2, BRIGHTNESS);
      }
      else {	// Turn the LED off
        IntervalTime[pos] = random(200, 2000);
        LEDMillis[pos] = millis();
        LEDOn[pos] = 0;
      }
    }
  }
}
// Random Blocks
// 8 LED bars are bars and random individual
// 4 LED groups are random together as groups of 4.
void RandomBlocks2()
{
	byte i;
	byte y;
	int pos;

	// Fade all LEDs
//	fadeToBlackBy(DJLEDs, NUM_LEDS, 10);

	// Turn On LEDs
	// Random single bars
	//int pos = random16(8);

	// Bar 1

	// Blink end LED;
	pos = PanelAStart + Bar1Length;
	DJLEDs[pos].fadeToBlackBy(8);
	if (millis() - LEDMillis[pos] > IntervalTime[pos]) {
		if (!LEDOn[pos]) { // LED Off - turn in on


			IntervalTime[pos] = random(200, 500);
			LEDMillis[pos] = millis();
			LEDOn[pos] = 1;
		}
		else {	// Turn the LED off
			DJLEDs[pos] = SmallLEDColors[random(0, 9)];
			IntervalTime[pos] = random(200, 500);
			LEDMillis[pos] = millis();
			LEDOn[pos] = 0;
		}
	}

	// Change the Length
	pos = PanelAStart;
	if (millis() - LEDMillis[pos] > IntervalTime[pos]) {
			IntervalTime[pos] = random(1000, 3500);
			LEDMillis[pos] = millis();
			Bar1Length = random(1, 7);
			//Serial.print("bar1length: ");
			//Serial.println(Bar1Length);
			for (i = 0; i < 8; i++) {
				pos = PanelAStart + i;
				if (i < Bar1Length)	DJLEDs[pos] = cRED2;
				else DJLEDs[pos] = 0;
			}
	}


	// Bar 2
	for (i = 0; i < 8; i++) {
		//for (y = 1; y < 3; y++) {
		y = 1;
			pos = PanelBStart + i;// + y * 20;

			if (!LEDOn[pos]) {	// LED off, fade it
				DJLEDs[pos].fadeToBlackBy(8);
			}
			else {
				//	DJLEDs[pos].fadeLightBy(-5);
			}
			if (millis() - LEDMillis[pos] > IntervalTime[pos]) {
				if (!LEDOn[pos]) { // LED Off - turn in on
					DJLEDs[pos] = SmallLEDColors[random(0, 9)];// CHSV(random(100, 200), random(0, 200), 255); //CHSV(gHue + random8(64), 200, 0);
					IntervalTime[pos] = random(500, 2500);
					LEDMillis[pos] = millis();
					LEDOn[pos] = 1;
				}
				else {	// Turn the LED off
					IntervalTime[pos] = random(500, 3000);
					LEDMillis[pos] = millis();
					LEDOn[pos] = 0;
				}
			}
		//}

	}

	// Bar 3

// Blink end LED;
	pos = PanelCStart + Bar3Length;
	DJLEDs[pos].fadeToBlackBy(8);
	if (millis() - LEDMillis[pos] > IntervalTime[pos]) {
		if (!LEDOn[pos]) { // LED Off - turn in on


			IntervalTime[pos] = random(200, 500);
			LEDMillis[pos] = millis();
			LEDOn[pos] = 1;
		}
		else {	// Turn the LED off
			DJLEDs[pos] = SmallLEDColors[random(0, 9)];
			IntervalTime[pos] = random(200, 500);
			LEDMillis[pos] = millis();
			LEDOn[pos] = 0;
		}
	}

	// Change the Length
	pos = PanelCStart;
	if (millis() - LEDMillis[pos] > IntervalTime[pos]) {
		IntervalTime[pos] = random(1000, 3500);
		LEDMillis[pos] = millis();
		Bar3Length = random(1, 7);
		for (i = 0; i < 8; i++) {
			pos = PanelCStart + i;
			if (i < Bar3Length)	DJLEDs[pos] = cWHITE2;
			else DJLEDs[pos] = 0;
		}
	}



	 //Large Block LEDs
	for (i = 0; i < 9; i++) {

			pos = StartLEDNum[i];

			if (!LEDOn[pos]) {	// LED off, fade it
				for (byte x = 0; x < 4; x++) {
					DJLEDs[pos + x].fadeToBlackBy(8);
				}
			}
			else {
				//	DJLEDs[pos].fadeLightBy(-5);
			}
			if (millis() - LEDMillis[pos] > IntervalTime[pos]) {
				if (!LEDOn[pos]) { // LED Off - turn in on
					byte c = random(0, 4);
					//Serial.println(c);
					LEDBlockOn(pos, BlockLEDColors[c], BLOCKBRIGHTNESS);//CHSV(random(100, 200), random(0, 200), 255), 50);
					IntervalTime[pos] = random(200, 1500);
					LEDMillis[pos] = millis();
					LEDOn[pos] = 1;
				}
				else {	// Turn the LED off
					IntervalTime[pos] = random(200, 2000);
					LEDMillis[pos] = millis();
					LEDOn[pos] = 0;
				}
			}


	}

}


void rainbow()
{
	// FastLED's built-in rainbow generator
	fill_rainbow(DJLEDs, NUM_LEDS, gHue, 7);

}

void rainbowWithGlitter()
{
	// built-in FastLED rainbow, plus some random sparkly glitter
	rainbow();
	addGlitter(80);
}

void addGlitter(fract8 chanceOfGlitter)
{
	if (random8() < chanceOfGlitter) {
		DJLEDs[random16(NUM_LEDS)] += CRGB::White;

	}
}

static void glitter4(CRGB *layer, fract8 chanceOfGlitter)
{
	for (byte i = 0; i < 4; i++) {
		if (random8() < chanceOfGlitter) {
			layer[random16(NUM_LEDS)] += CRGB::White;

		}
	}
}

void addGlitter4(fract8 chanceOfGlitter)
{
	glitter4(DJLEDs, chanceOfGlitter);
}

void confetti()
{
	// random colored speckles that blink in and fade smoothly
	fadeToBlackBy(DJLEDs, NUM_LEDS, 10);

	int pos = random16(NUM_LEDS);
	DJLEDs[pos] += CHSV(gHue + random8(64), 200, 255);

}
void ConfettiRedWhite()
{
	// random colored speckles that blink in and fade smoothly
	fadeToBlackBy(DJLEDs, NUM_LEDS, 10);

	int pos = random16(NUM_LEDS);
	DJLEDs[pos] += CHSV(0, gSat, 192);

}


void bpm()
{
	// colored stripes pulsing at a defined Beats-Per-Minute (BPM)
	uint8_t BeatsPerMinute = 62;
	CRGBPalette16 palette = PartyColors_p;
	uint8_t beat = beatsin8(BeatsPerMinute, 64, 255);
	for (int i = 0; i < NUM_LEDS; i++) { //9948
		DJLEDs[i] = ColorFromPalette(palette, gHue + (i * 2), beat - gHue + (i * 10));

	}
}

void juggle() {
	// eight colored dots, weaving in and out of sync with each other
	fadeToBlackBy(DJLEDs, NUM_LEDS, 20);

	byte dothue = 0;
	for (int i = 0; i < 8; i++) {
		DJLEDs[beatsin16(i + 7, 0, NUM_LEDS - 1)] |= CHSV(dothue, 200, 255);

		dothue += 32;
	}
}
void AllRed() {
	fill_solid(DJLEDs, NUM_LEDS, CRGB(255, 0, 0));

}

void AllGreen() {
	fill_solid(DJLEDs, NUM_LEDS, CRGB(0, 255, 0));

}


void ShortCircuit() {
	if (millis() - FadeMillis > FadeInterval) {
		addGlitter4(150);
		DecayTime--;
		FadeInterval += 4;
		FadeMillis = millis();
	}

	if (DecayTime == 0) {
		DecayTime = DECAYTIME;
		gCurrentPatternNumber = 0;
		FadeInterval = 0;
	}
	fadeToBlackBy(DJLEDs, NUM_LEDS, 10);

}

// One bar or block joins the running panel, lit, so its stutter has something to show.
static void bootElementOn(uint8_t element, uint32_t now) {
	if (element == 0 || element == 2) {          // bars A and C redraw on this step
		const uint8_t pos = element ? PanelCStart : PanelAStart;
		IntervalTime[pos] = 0;
		LEDMillis[pos] = now - 1;
	} else if (element == 1) {                   // bar B: every pixel lit
		for (uint8_t i = 0; i < 8; i++) {
			const uint8_t pos = PanelBStart + i;
			DJLEDs[pos] = SmallLEDColors[random(0, 9)];
			LEDOn[pos] = 1;  LEDMillis[pos] = now;  IntervalTime[pos] = random(600, 2500);
		}
	} else {
		const uint8_t pos = StartLEDNum[element - 3];
		LEDBlockOn(pos, BlockLEDColors[random(0, 4)], BLOCKBRIGHTNESS);
		LEDOn[pos] = 1;  LEDMillis[pos] = now;  IntervalTime[pos] = random(700, 1500);
	}
}

// The bars of the startup rise: one step of their climbing, slipping and flicker.
static void riseStep(uint32_t now) {
	const uint32_t planEnd = startupAt + startupSpanMs;
	const uint32_t span = (int32_t)(planEnd - riseStartMs) > 1000 ? planEnd - riseStartMs : 1000;
	const uint32_t elapsed = now - riseStartMs;
	const uint16_t progress = elapsed >= span ? 255 : (uint16_t)(elapsed * 255UL / span);
	const uint32_t sinceReady = readySaid ? now - readyMs : 0;
	const uint32_t slot = now / 45;              // flicker changes about 22 times a second
	for (uint8_t b = 0; b < 3; b++) {
		const uint16_t planned = (progress * RISE_PACE[b]) >> 8;
		const uint8_t target = readySaid ? 8 : (planned > 7 ? 7 : (uint8_t)planned);
		if (riseLevel[b] < target && (int32_t)(now - riseMoveAt[b]) >= 0) {
			riseLevel[b]++;
			// The lock is quickest, catching up after a slip quick, the first climb slow.
			riseMoveAt[b] = now + (readySaid ? 45 : riseLevel[b] + 1 < target ? 90 + random8(90) : 180 + random8(200));
		}
		if (!readySaid) {
			if (riseLevel[b] >= 2 && (int32_t)(now - riseSlipAt[b]) >= 0) {
				uint8_t lost = 1 + (random8() < 100) + (riseLevel[b] >= 5 && random8() < 50);
				if (lost > riseLevel[b] - 1) lost = riseLevel[b] - 1;
				riseLevel[b] -= lost;
				riseSlipAt[b]   = now + 1100 + random16(1900);
				riseMoveAt[b]   = now + 220 + random16(380);
				riseDipUntil[b] = now + 70 + random8(110);
			}
			if ((int32_t)(now - riseDipAt[b]) >= 0) {
				riseDipUntil[b] = now + 50 + random8(90);
				riseDipAt[b]    = now + 900 + random16(2600);
			}
		}
		const uint8_t column = b == 0 ? PanelAStart : b == 1 ? PanelBStart : PanelCStart;
		const bool dipped = !readySaid && (int32_t)(riseDipUntil[b] - now) > 0;
		for (uint8_t i = 0; i < 8; i++) {
			CRGB c = CRGB::Black;
			if (i < riseLevel[b]) {
				if (readySaid) {
					// Locked: every light as bright as a tip, easing to the bar's own steady colour.
					const uint8_t eased = sinceReady >= FINALE_MS ? 255 : (uint8_t)(sinceReady * 255UL / FINALE_MS);
					c = blend(RISE_TIP[b], RISE_BODY[b], ease8InOutQuad(eased));
				} else if (i + 1 == riseLevel[b]) {
					c = RISE_TIP[b];             // the top light: never steady, now and then out
					c.nscale8(110 + scale8(flick8(slot, b * 16 + i), 145));
					if (flick8(slot, 200 + b) < 40) c = CRGB::Black;
				} else {
					c = RISE_BODY[b];
					if (flick8(slot, b * 16 + i) < 14) c.nscale8(90);   // a light lower down stutters
				}
				if (dipped) c.nscale8(flick8(now / 25, 100 + b) < 128 ? 30 : 0);   // browned out
			} else if (!readySaid && i == riseLevel[b] && flick8(slot, 60 + b) < 36) {
				c = RISE_BODY[b];                // the next light up tries to catch
				c.nscale8(120);
			}
			DJLEDs[gaugePixel(column, i)] = c;
		}
	}
}

// Startup (see "Transitions" and "Startup rise"): a spark up the charge meter when it was
// showing, ShortCircuit's 80 thinning bursts on the spark layer over the planned time, the
// bars rising under them, and the blocks coming on piece by piece near the planned end.
void startupShow() {
	const uint32_t now = millis();

	if (igniting) {
		fadeToBlackBy(DJLEDs, NUM_LEDS, 10);     // the spark's tail, and anything else showing
		const int32_t since = (int32_t)(now - igniteStartMs);
		uint8_t head = 0;
		if (since >= 0) head = (uint8_t)min((int32_t)igniteFilled, since / IGNITE_STEP_MS);
		for (uint8_t level = head; level < igniteFilled; level++)
			DJLEDs[gauge24Pixel(level)] = gauge24Color(level);
		if (since >= 0 && head < igniteFilled) DJLEDs[gauge24Pixel(head)] = CRGB::White;
		if (head < igniteFilled) return;
		igniting = false;                        // the meter is gone: the sparks start now
		FadeMillis = now;
	}
	if (!riseBegun) {                            // the bars start from nothing, each in its own time
		riseBegun = true;
		riseStartMs = now;
		for (uint8_t b = 0; b < 3; b++) {
			riseLevel[b]    = 0;
			riseMoveAt[b]   = now + 150 + random16(500);
			riseSlipAt[b]   = now + 1400 + random16(1500);
			riseDipAt[b]    = now + 600 + random16(1800);
			riseDipUntil[b] = now;
		}
	}

	const uint32_t planEnd = startupAt + startupSpanMs;
	// READY's stand-in: the planned end when it times itself, a grace later when the host
	// was to say it.
	if (!readySaid && (int32_t)(now - (planEnd + (startupHosted ? READY_GRACE_MS : 0))) >= 0) {
		readySaid = true;
		readyMs = now;
	}

	if (now - FadeMillis > FadeInterval) {
		if (DecayTime > 0) {                     // the 80 bursts, their gaps growing to fill the span
			glitter4(sparks, 150);
			DecayTime--;
			const uint32_t span = (int32_t)(planEnd - riseStartMs) > 0 ? planEnd - riseStartMs : 0;
			FadeInterval = (uint32_t)(DECAYTIME - DecayTime) * span / SPARK_SUM;
		} else {                                 // past the plan and still waiting: a sparse few
			if (!readySaid) glitter4(sparks, 70);
			FadeInterval = SPARK_WAIT_MS;
		}
		FadeMillis = now;
	}
	fadeToBlackBy(sparks, NUM_LEDS, 10);

	if (!bootBegun) {
		fadeToBlackBy(DJLEDs, NUM_LEDS, 10);     // whatever was showing dies away
		if (readySaid || (int32_t)(now - (planEnd - BLOCKS_LEAD_MS)) >= 0) {
			// The panel starts running, every block dark until its moment.
			const uint16_t stagger = readySaid ? BOOT_HURRY_MS : BOOT_STAGGER_MS;
			fill_solid(DJLEDs, 96, CRGB::Black);
			uint8_t order[BOOT_BLOCKS];
			for (uint8_t i = 0; i < BOOT_BLOCKS; i++) order[i] = 3 + i;
			for (uint8_t i = BOOT_BLOCKS - 1; i > 0; i--) {
				const uint8_t j = random8(i + 1);
				const uint8_t swap = order[i];  order[i] = order[j];  order[j] = swap;
			}
			for (uint8_t slot = 0; slot < BOOT_BLOCKS; slot++) {
				bootOnlineAt[order[slot]] = slot * stagger + random8(readySaid ? 30 : BOOT_JITTER_MS);
				bootOnline[order[slot]] = false;
			}
			for (uint8_t e = 0; e < 3; e++) { bootOnlineAt[e] = 0;  bootOnline[e] = true; }
			bootSpanMs = BOOT_BLOCKS * stagger + BOOT_JITTER_MS + BOOT_CATCH_MS;
			bootStartMs = now;
			booting = bootBegun = true;
		}
	}
	if (bootBegun) {
		for (uint8_t e = 3; e < BOOT_ELEMENTS; e++) {
			if (!bootOnline[e] && now - bootStartMs >= bootOnlineAt[e]) {
				bootOnline[e] = true;
				bootElementOn(e, now);
			}
		}
		RandomBlocks2();
	}
	riseStep(now);                               // the bars are the rise's until it locks

	if (readySaid && now - readyMs >= FINALE_MS) {
		// Locked and steady: the ordinary panel takes the bars over, blended from the full
		// ones. Any block still to come on keeps its moment, and the last sparks die away
		// on their layer.
		beginFade(FADE_SETTLE_MS);
		for (uint8_t e = 0; e < 3; e++) bootElementOn(e, now);
		RandomBlocks2();
		DecayTime = DECAYTIME;
		FadeInterval = 0;
		gCurrentPatternNumber = 1;
		sparksEndMs = now;
		riseBegun = readySaid = bootBegun = false;
		masterBrightness = startupLandBrightness;
		chestMode = startupLandMode;
	}
}

void LEDsOff() {
	// Turn LEDs off and keep them off until another mode command arrives.
	clearAll();
}

// ---------------------------------------------------------------------------
// New animation functions
// ---------------------------------------------------------------------------

// sleepBreath — all 98 pixels pulse dim red with an 8-second sine-wave period.
// RandomEyes is suppressed while in CM_SLEEP so the last two pixels also breathe.
void sleepBreath() {
    uint32_t now    = millis();
    float    phase  = (float)(now % 8000UL) / 8000.0f;   // 0.0 → 1.0
    float    bright = 0.5f * (1.0f - cosf(TWO_PI * phase)); // sine 0→1→0
    uint8_t  b      = (uint8_t)(bright * 50.0f);           // 0 – 50 (very dim)
    fill_solid(DJLEDs, NUM_LEDS, CRGB(b, 0, 0));
}

// angryFlash — red alert. Instead of strobing the whole panel on/off, this
// keeps the control-panel character of RandomBlocks2 but furious:
//
//   * Bars (3 × 8 LEDs)  — a hot red-orange scanner head sweeps each bar
//     back and forth (different speed/phase per bar), leaving a decaying
//     trail via the per-frame fade.
//   * Blocks (9 × 4 LEDs) — asynchronous alarm strobes: short red bursts
//     with random gaps, some burning orange-hot, like warning lights all
//     tripping at once.
//   * Slam — every 1.2–2.6 s the whole panel flashes full red once and
//     decays in ~200 ms: the old strobe's punch, kept as an accent.
//   * A dim red simmer floor keeps the panel from ever going black.
//
// Eyes (pixels 96–97) stay owned by RandomEyes, as in every awake mode.
void angryFlash() {
    static uint32_t lastRunMs   = 0;
    static uint32_t nextSlamMs  = 0;
    static bool     blockHot[9] = { false };

    uint32_t now = millis();

    // Mode (re)entry: a gap since the last frame means we just switched in.
    // Kick the shared per-pixel timers to short angry intervals — otherwise
    // stale RandomBlocks2 intervals (up to 3 s) leave the blocks frozen —
    // and schedule the first slam almost immediately for instant drama.
    if (now - lastRunMs > 250UL) {
        for (byte i = 0; i < 9; i++) {
            uint8_t pos = StartLEDNum[i];
            LEDMillis[pos]    = now;
            IntervalTime[pos] = random(40, 300);
            LEDOn[pos]        = 0;
        }
        nextSlamMs = now + 150;
    }
    lastRunMs = now;

    // Decay everything from the previous frame (trails, strobes, slam tail).
    // Eyes excluded — RandomEyes owns pixels 96+.
    fadeToBlackBy(DJLEDs, 96, 20);

    // Bars: one hot scanner head per bar, sweeping at slightly different
    // rates so the three bars never sync up.
    const uint8_t barStarts[3] = { PanelAStart, PanelBStart, PanelCStart };
    for (byte b = 0; b < 3; b++) {
        uint8_t head = beatsin8(88 + b * 9, 0, 7, 0, b * 85);
        DJLEDs[barStarts[b] + head] = CRGB(255, 40, 0);
    }

    // Blocks: independent alarm strobes. Bursts rewrite their colour every
    // frame (so the fade can't dim them); gaps let the fade swallow them.
    for (byte i = 0; i < 9; i++) {
        uint8_t pos = StartLEDNum[i];
        if (now - LEDMillis[pos] > IntervalTime[pos]) {
            LEDMillis[pos] = now;
            if (!LEDOn[pos]) {
                LEDOn[pos]        = 1;
                IntervalTime[pos] = random(80, 260);   // short furious burst
                blockHot[i]       = (random(0, 4) == 0);  // 1 in 4 burns orange
            } else {
                LEDOn[pos]        = 0;
                IntervalTime[pos] = random(120, 500);  // dark gap
            }
        }
        if (LEDOn[pos]) {
            LEDBlockOn(pos, blockHot[i] ? CRGB(255, 90, 0) : CRGB(255, 0, 0),
                       BLOCKBRIGHTNESS);
        }
    }

    // Slam: one full-panel red flash, then let the fade eat it.
    if (now >= nextSlamMs) {
        fill_solid(DJLEDs, 96, CRGB(255, 0, 0));
        nextSlamMs = now + random(1200, 2600);
    }

    // Simmer floor: the panel never goes fully dark while he's angry.
    for (byte i = 0; i < 96; i++) DJLEDs[i] |= CRGB(18, 0, 0);
}

// complimentFlash — an "aw shucks" shimmer (triggered when Rex is complimented):
// a soft blue glow washes the panel while gold and white sparkles shower across
// it for the ~2.5 s the mode runs (self-ended by runCurrentMode).
//
// PSU note (learned the hard way): white lights all 3 channels at once, so the
// old FULL-PANEL white flash browned out the WS2811s and rendered as RED. This
// effect keeps white to a couple of sparkle pixels at a time; the sustained
// wash is single-channel blue. If sparkles ever look red/orange, lower
// SPARKLE_CHANCE to thin them out rather than brightening anything.
#define SPARKLE_CHANCE 40   // per attempt, 2 attempts/frame ≈ 60 sparkles/s
void complimentFlash() {
    fadeToBlackBy(DJLEDs, 96, 30);   // sparkles glint out in ~75 ms

    // Soft blue wash, always present under the sparkles.
    for (byte i = 0; i < 96; i++) DJLEDs[i] |= CRGB(0, 0, 45);

    // Gold / white sparkle shower.
    for (byte s = 0; s < 2; s++) {
        if (random8() < SPARKLE_CHANCE) {
            DJLEDs[random8(96)] = (random8() < 100) ? CRGB(255, 255, 255)
                                                    : CRGB(255, 200, 80);   // gold
        }
    }
}

// excitedPulse — the idle panel on caffeine. Same control-panel vocabulary as
// RandomBlocks2 (that's what makes it read "Rex", not a generic light show)
// but pumping ~4x faster: a gold head races up each of the three bars and
// wraps around (each bar at its own tempo), the nine blocks pop in rapid
// red-heavy bursts with gold/white/blue accents, and white glitter rides the
// whole thing. Distinct from angry: racing and celebratory, no slams, no
// menace. Runs at full master brightness (set in handleCommand).
void excitedPulse() {
    static uint32_t lastRunMs   = 0;
    static uint8_t  blockCol[9] = { 0 };
    uint32_t now = millis();

    // Mode (re)entry: kick the shared per-pixel timers to excited tempo so
    // stale intervals from the previous mode can't stall the blocks.
    if (now - lastRunMs > 250UL) {
        for (byte i = 0; i < 9; i++) {
            uint8_t pos = StartLEDNum[i];
            LEDMillis[pos]    = now;
            IntervalTime[pos] = random(30, 200);
            LEDOn[pos]        = 0;
        }
    }
    lastRunMs = now;

    fadeToBlackBy(DJLEDs, 96, 25);   // snappy trails

    // Bars: a gold head races up each bar and wraps, each at its own tempo.
    const uint8_t barStarts[3] = { PanelAStart, PanelBStart, PanelCStart };
    for (byte b = 0; b < 3; b++) {
        uint8_t head = scale8(beat8(140 + b * 30), 7);
        DJLEDs[barStarts[b] + head] = cGOLD;
    }

    // Blocks: rapid pops — half red, half gold/white/blue accents.
    for (byte i = 0; i < 9; i++) {
        uint8_t pos = StartLEDNum[i];
        if (now - LEDMillis[pos] > IntervalTime[pos]) {
            LEDMillis[pos] = now;
            if (!LEDOn[pos]) {
                LEDOn[pos]        = 1;
                IntervalTime[pos] = random(60, 220);
                blockCol[i]       = (random8() < 128) ? 0 : random(1, 4);
            } else {
                LEDOn[pos]        = 0;
                IntervalTime[pos] = random(60, 320);
            }
        }
        if (LEDOn[pos]) LEDBlockOn(pos, BlockLEDColors[blockCol[i]], BLOCKBRIGHTNESS);
    }

    addGlitter(60);   // white sparks riding the energy
}

// sadSigh — slow blue melancholy. Everything breathes on long cycles: each
// bar's level slowly swells and slumps like a sigh (12–20 s per cycle, the
// three bars drifting out of phase), and each block breathes dim blue on its
// own slow period. Stateless (pure beatsin8), so no entry kick is needed.
// Runs dim (master brightness 55, set in handleCommand).
void sadSigh() {
    fadeToBlackBy(DJLEDs, 96, 5);   // long, mournful trails as the bars slump

    // Bars: sighing levels, out of phase with each other.
    const uint8_t barStarts[3] = { PanelAStart, PanelBStart, PanelCStart };
    for (byte b = 0; b < 3; b++) {
        uint8_t len = beatsin8(3 + b, 0, 8, 0, b * 70);   // 3–5 bpm sighs
        for (byte i = 0; i < len; i++) DJLEDs[barStarts[b] + i] = CRGB(0, 0, 200);
    }

    // Blocks: each breathes dim blue on its own slow period and phase.
    for (byte i = 0; i < 9; i++) {
        uint8_t lvl = beatsin8(2 + (i & 3), 15, 160, 0, i * 28);
        LEDBlockOn(StartLEDNum[i], CRGB(0, 0, lvl), BLOCKBRIGHTNESS);
    }
}

// happyBounce — cheerful and playful, halfway between idle's calm and
// excited's frenzy: a gold head BOUNCES end-to-end on each bar (bounce, not
// wrap — that's the visual signature separating happy from excited's racing),
// the blocks pop at a relaxed pace in gold/white/blue (no red — red reads
// angry/excited), and rainbow confetti drifts over the top as a nod to the
// old happy pattern.
void happyBounce() {
    static uint32_t lastRunMs   = 0;
    static uint8_t  blockCol[9] = { 1 };
    uint32_t now = millis();

    // Mode (re)entry: kick the shared per-pixel timers (see excitedPulse).
    if (now - lastRunMs > 250UL) {
        for (byte i = 0; i < 9; i++) {
            uint8_t pos = StartLEDNum[i];
            LEDMillis[pos]    = now;
            IntervalTime[pos] = random(100, 500);
            LEDOn[pos]        = 0;
        }
    }
    lastRunMs = now;

    fadeToBlackBy(DJLEDs, 96, 12);   // soft playful trails

    // Bars: gold heads bounce end-to-end, each at its own happy tempo.
    const uint8_t barStarts[3] = { PanelAStart, PanelBStart, PanelCStart };
    for (byte b = 0; b < 3; b++) {
        uint8_t head = beatsin8(45 + b * 8, 0, 7, 0, b * 85);
        DJLEDs[barStarts[b] + head] = cGOLD;
    }

    // Blocks: relaxed cheerful pops in white/gold/blue.
    for (byte i = 0; i < 9; i++) {
        uint8_t pos = StartLEDNum[i];
        if (now - LEDMillis[pos] > IntervalTime[pos]) {
            LEDMillis[pos] = now;
            if (!LEDOn[pos]) {
                LEDOn[pos]        = 1;
                IntervalTime[pos] = random(150, 500);
                blockCol[i]       = random(1, 4);   // cWHITE / cGOLD / cBLUE
            } else {
                LEDOn[pos]        = 0;
                IntervalTime[pos] = random(150, 700);
            }
        }
        if (LEDOn[pos]) LEDBlockOn(pos, BlockLEDColors[blockCol[i]], BLOCKBRIGHTNESS);
    }

    // Rainbow confetti drifting over the top.
    if (random8() < 90) DJLEDs[random8(96)] += CHSV(gHue + random8(64), 200, 255);
}

// ---------------------------------------------------------------------------
// Feelings added November 15 (owner: emotional reactions on the chest). Each keeps the
// panel's own parts, three bars and nine blocks, so it still reads as Rex's chest; what
// differs is how they move. Bars are wired top down: pixel 0 is the top of a bar.
// ---------------------------------------------------------------------------

static const uint8_t BAR_STARTS[3] = { PanelAStart, PanelBStart, PanelCStart };

// curiousScan — looking something over. A violet head drifts up and down each bar,
// lingering at the ends, the three out of step like eyes moving over a thing, while one
// block at a time lights in turn round the nine, as if checking each. A dim violet floor.
void curiousScan() {
    const uint32_t t = millis() - modeEnteredMs;
    fadeToBlackBy(DJLEDs, 96, 14);
    for (byte i = 0; i < 96; i++) DJLEDs[i] |= CRGB(10, 0, 22);
    for (byte b = 0; b < 3; b++) {
        const uint8_t head = beatsin8(22 + b * 5, 0, 7, 0, b * 85);
        DJLEDs[BAR_STARTS[b] + head] = CRGB(190, 60, 255);
    }
    LEDBlockOn(StartLEDNum[(t / 260UL) % 9], CRGB(150, 0, 255), BLOCKBRIGHTNESS);
}

// surprisedBurst — a jolt. In the first fifth of a second every bar fills from its middle
// outward and every block comes on at once, blue with white tips. Then the blocks flicker
// fast and unevenly while the full bars tremble, and over about two and a half seconds the
// flicker slows to wide awake: bars full, blocks mostly on with a blink now and then.
// The whole panel is never white (see the PSU note at complimentFlash): the body of the
// burst is blue, and white is kept to the bar tips and a block or two.
#define SURPRISE_BURST_MS  180UL
#define SURPRISE_HOLD_MS   350UL
#define SURPRISE_SETTLE_MS 2500UL
void surprisedBurst() {
    static bool     blockWhite[9] = { false };
    const uint32_t now = millis();
    const uint32_t t   = now - modeEnteredMs;

    fadeToBlackBy(DJLEDs, 96, 40);
    for (byte i = 0; i < 96; i++) DJLEDs[i] |= CRGB(0, 8, 26);

    // Bars: out from the middle pair, the outermost lit pair white.
    const uint8_t reach = t >= SURPRISE_BURST_MS ? 4 : (uint8_t)(1 + t * 3 / SURPRISE_BURST_MS);
    for (byte b = 0; b < 3; b++) {
        for (byte d = 0; d < reach; d++) {
            CRGB colour = (d == reach - 1) ? CRGB(200, 230, 255) : CRGB(30, 140, 255);
            if (t > SURPRISE_HOLD_MS) colour.nscale8(190 + random8(66));   // the tremble
            DJLEDs[BAR_STARTS[b] + 3 - d] = colour;
            DJLEDs[BAR_STARTS[b] + 4 + d] = colour;
        }
    }

    // Blocks: all on through the hold, then a flicker whose lit spells lengthen as he settles.
    const uint32_t calm = (t < SURPRISE_SETTLE_MS ? t : SURPRISE_SETTLE_MS) * 255UL / SURPRISE_SETTLE_MS;   // 0 jolted .. 255 settled
    for (byte i = 0; i < 9; i++) {
        const uint8_t pos = StartLEDNum[i];
        if (t <= SURPRISE_HOLD_MS) {
            LEDOn[pos] = 1;
            LEDMillis[pos] = now;
            IntervalTime[pos] = random(40, 140);
            blockWhite[i] = (i == 4);                      // the middle block alone is white
        } else if (now - LEDMillis[pos] > IntervalTime[pos]) {
            LEDMillis[pos] = now;
            LEDOn[pos] = !LEDOn[pos];
            if (LEDOn[pos]) {
                IntervalTime[pos] = random(60, 160) + (uint16_t)(calm * random(300, 900) / 255);
                blockWhite[i] = random8() < 40;
            } else {
                IntervalTime[pos] = random(30, 110);
            }
        }
        if (LEDOn[pos]) LEDBlockOn(pos, blockWhite[i] ? CRGB(190, 225, 255) : CRGB(0, 130, 255), BLOCKBRIGHTNESS);
    }
}

// proudRise — standing tall. The three bars fill from the bottom to the top in gold, the
// middle one leading, each with a pale crest, and stay full; the blocks come on one after
// another and stay on. After that nothing blinks: a sheen climbs the full bars every couple
// of seconds and the blocks breathe very slightly. Fullness and stillness set it apart from
// excited's racing and happy's bouncing.
#define PROUD_FILL_MS 600UL
void proudRise() {
    const uint32_t t = millis() - modeEnteredMs;
    fill_solid(DJLEDs, 96, CRGB::Black);

    const uint8_t sheen = (uint8_t)((t / 90UL) % 27);          // a bright step climbing, then a rest
    for (byte b = 0; b < 3; b++) {
        const uint32_t since = t > (b == 1 ? 0UL : 120UL) ? t - (b == 1 ? 0UL : 120UL) : 0;
        const uint8_t filled = since >= PROUD_FILL_MS ? 8 : (uint8_t)(since * 8 / PROUD_FILL_MS);
        for (byte level = 0; level < filled; level++) {
            CRGB colour = (level == filled - 1) ? CRGB(255, 230, 170) : CRGB(255, 165, 25);
            if (filled == 8 && level == sheen) colour = CRGB(255, 240, 200);
            DJLEDs[BAR_STARTS[b] + 7 - level] = colour;
        }
    }
    for (byte i = 0; i < 9; i++) {
        if (t < 300UL + i * 110UL) continue;
        CRGB colour = (i & 1) ? CRGB(255, 150, 0) : CRGB(cGOLD);
        colour.nscale8(beatsin8(12, 205, 255, 0, i * 28));
        LEDBlockOn(StartLEDNum[i], colour, BLOCKBRIGHTNESS);
    }
}

// skepticalStare — unimpressed. The busy panel stops. Each bar holds one flat two-pixel
// line at its middle, a dim sour yellow-green, unmoving, and seven of the nine blocks are
// dark. Every few seconds the last bar's line creeps up two pixels, stays, and comes slowly
// back: one raised eyebrow. Runs dim.
#define SKEPTIC_CYCLE_MS 3600UL
void skepticalStare() {
    const uint32_t t = (millis() - modeEnteredMs) % SKEPTIC_CYCLE_MS;
    fill_solid(DJLEDs, 96, CRGB::Black);

    uint8_t raised = 0;                                        // pixels the eyebrow is up
    if (t >= 2000UL && t < 2400UL)      raised = (uint8_t)((t - 2000UL) * 3 / 400UL);        // up
    else if (t >= 2400UL && t < 3100UL) raised = 2;                                           // held
    else if (t >= 3100UL)               raised = (uint8_t)(2 - (t - 3100UL) * 3 / 500UL);    // back
    if (raised > 2) raised = 2;
    for (byte b = 0; b < 3; b++) {
        const uint8_t up = (b == 2) ? raised : 0;
        DJLEDs[BAR_STARTS[b] + 3 - up] = CRGB(170, 220, 0);
        DJLEDs[BAR_STARTS[b] + 4 - up] = CRGB(170, 220, 0);
    }
    LEDBlockOn(StartLEDNum[1], CRGB(110, 140, 0), BLOCKBRIGHTNESS);
    LEDBlockOn(StartLEDNum[7], CRGB(110, 140, 0), BLOCKBRIGHTNESS);
}

// sympathyBeat — warmth. The bars and blocks glow soft rose and swell in a slow heartbeat,
// two beats and a rest about fifty times a minute, the middle panel a moment ahead of the
// outer two, over a faint rose floor.
#define HEART_CYCLE_MS 1200UL
static uint8_t heartLevel(uint32_t t) {
    // One beat: up in 90 ms, down over 250 ms. The second, at 300 ms, is smaller.
    auto beat = [](int32_t x) -> float {
        if (x < 0 || x >= 340) return 0.0f;
        return x < 90 ? x / 90.0f : 1.0f - (x - 90) / 250.0f;
    };
    const int32_t x = (int32_t)(t % HEART_CYCLE_MS);
    float level = max(beat(x), 0.7f * beat(x - 300));
    return (uint8_t)(46 + level * 209.0f);                     // never below 18 %
}
void sympathyBeat() {
    const uint32_t t = millis() - modeEnteredMs;
    fill_solid(DJLEDs, 96, CRGB(20, 2, 6));
    for (byte panel = 0; panel < 3; panel++) {
        // 70 ms behind the middle panel; HEART_CYCLE_MS keeps the subtraction from going under zero.
        const uint8_t level = heartLevel(panel == 1 ? t + HEART_CYCLE_MS : t + HEART_CYCLE_MS - 70UL);
        CRGB colour(255, 40, 90);
        colour.nscale8(level);
        for (byte i = 0; i < 8; i++) DJLEDs[BAR_STARTS[panel] + i] = colour;
        for (byte k = 0; k < 3; k++) LEDBlockOn(StartLEDNum[panel * 3 + k], colour, BLOCKBRIGHTNESS);
    }
}

}  // namespace chest
