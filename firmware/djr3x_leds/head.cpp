/*
 * head.cpp — DJ-R3X head LEDs (eyes and mouth) on the combined LED board.
 * Ported from V2 arduino/head_nano/head_nano.ino (SHA-256 72a946e4...).
 *
 * Port note: the show() throttles, repeated-command tolerance and watchdogs below were
 * written for the Nano, where each show() blocked interrupts and lost serial bytes and
 * RAM was nearly full. Neither holds on the ESP32; the logic is kept unchanged so the
 * lights look the same, and comments about dropped bytes or DRAM describe the Nano.
 *
 * Hardware
 * --------
 *   82 WS2812B NeoPixels on HEAD_DATA_PIN (FastLED)
 *   Pixels 0–1   : left and right eyes
 *   Pixels 2–81  : mouth (80-pixel trapezoid PCB)
 *
 * Mouth layout — 10 rows × 8 cols, serpentine wiring
 * ---------------------------------------------------
 *   Even rows (0,2,4,6,8) wire left→right.
 *   Odd  rows (1,3,5,7,9) wire right→left.
 *   Physical center of the array is at grid position (row=4.5, col=3.5):
 *   the "center row pair" is rows 4 and 5.
 *
 * Speaking animation — equalizer bars
 * ------------------------------------
 *   Eight vertical bars (one per column) open outward from the center row
 *   pair like a VU meter. Bar height tracks SPEAK_LEVEL with fast-attack /
 *   slow-decay ballistics; each column wobbles on its own phase so the bars
 *   dance independently, with a center-weighted envelope so the mouth reads
 *   as a mouth (tall in the middle, tapered at the corners). A falling
 *   "peak dot" hangs above each bar. Peak brightness is capped at
 *   SPEAK_MAX_BRIGHT (45 %) and only the center row pair keeps a dim floor —
 *   quiet passages close the mouth to a thin lit line instead of lighting
 *   the whole panel.
 *
 *   Since 0.9.0 (owner, November 24: "sometimes it's got this cool pattern and
 *   other times it's not as cool"): the bars are measured against how loud the
 *   voice has lately been, not against the top of SPEAK_LEVEL's range, so a
 *   quiet line fills the mouth like a loud one; each sound opens the middle
 *   bars first and rolls out to the corners; and a bar is brightest at the
 *   middle of the mouth, dimmer towards its tip. See "Speaking animation" below.
 *
 * Commands — routed here by djr3x_leds.ino, bare or with the H: prefix
 * ---------------------------------------------------------------------
 *   SPEAK:{emotion}       Start speaking animation. Also sets the mouth's
 *                         emotion colour used by the idle glow afterwards.
 *                         emotion = neutral | happy | excited | sad | angry | curious |
 *                                   surprised | proud | skeptical | sympathetic
 *   PRIDE:{0|1}          Rainbow mouth overlay; refresh ON within 10 seconds.
 *   SPEAK_LEVEL:{0-255}   Update audio intensity — drives bar height + motion.
 *                         Send as often as needed; non-blocking.
 *   SPEAK_STOP            Mouth returns to the dim idle glow (current emotion
 *                         colour); eyes unchanged; blinking suspended until next
 *                         EYE: or ACTIVE command.
 *   IDLE                  Mouth idle glow; eyes breathe slowly at last EYE: colour;
 *                         blink system activates (or stays active) immediately.
 *   ACTIVE                Mouth idle glow; preserve the current eye colour and
 *                         resume blinking. Falls back to bright white only
 *                         if no eye colour has been set yet.
 *   EYE:{r},{g},{b}       Set both eyes to RGB colour; blinking resumes. If the
 *                         board was in OFF mode (e.g. fresh reboot mid-session,
 *                         re-lit by the host's eye keep-alive heartbeat), this
 *                         also re-enters ACTIVE so the mouth glow resumes.
 *   STARTUP[:{ms}]        The mouth starts mostly dark and sparkles on in the ordinary
 *                         amber from its centre outwards, slowly, until READY (see
 *                         "Mouth startup"). With {ms}, the time the host expects to
 *                         need; bare, it times itself. The eyes go dark (the visor is
 *                         shut over them); an EYE: meanwhile only sets their colour.
 *   STARTUP_SOUND:{wait},{grow},{end}
 *                         The startup sound has just begun. Milliseconds from now: the mouth
 *                         stays dark until {wait}, twinkles a light or two until {grow}, then
 *                         sparkles on from the centre, reaching as far as it goes without
 *                         READY at {end}.
 *   STARTUP_LEVEL:{0-255} How loud the startup sound is now: more twinkles and brighter
 *                         sparkles when it is louder.
 *   READY                 The host is ready: the sparkle runs out to the edges and the
 *                         whole mouth goes solid as the chest lands on its ordinary panel;
 *                         the eyes blink on as the visor lifts.
 *   WINK[:{L|R}]          One eye goes dark for WINK_MS and comes back as it was; the
 *                         other stays lit. Right eye unless L is given. Ignored while
 *                         the eyes are dark.
 *   OFF                   All 82 pixels off immediately; blinking suspended.
 *   CHARGE:{0-100}        Eyes off; the mouth is a charge meter while Rex is off
 *                         and attached to the charger: rows lit from the bottom
 *                         up to the charge, red at the bottom to blue at the
 *                         top, with a pulse of light climbing it (see "Charge
 *                         display").
 *
 * Mouth idle glow
 * ---------------
 *   Whenever Rex is awake and not speaking (ACTIVE / IDLE / after SPEAK_STOP),
 *   the middle of the mouth glows dimly in the current emotion colour and the
 *   glow falls away to nothing at the edges, whose lights stay dark (owner,
 *   November 24: "the edges are dark and it is brighter towards the middle,
 *   keeping the whole not-talking animation dark"). It breathes slowly, each
 *   breath spreading from the middle outwards: the middle between about 5 and
 *   7.5 %, the last lit lights around 1 % (owner, November 21: a slow effect
 *   when not speaking; the quick animation is for speech only). Speech takes
 *   the mouth over, the glow fading out under the first bars, and the last
 *   bars close back into the glow. OFF / SLEEP / FADEOFF keep their existing
 *   dark / red-breathing behaviour. The glow writes ONLY mouth pixels — eyes
 *   stay exclusively owned by setEyes()/tickIdle()/tickBlink().
 *
 *   When the glow starts from a dark mouth (boot / OFF / waking from SLEEP),
 *   it ramps in from 0 over GLOW_RAMP_IN_MS (4 s) — program launch breathes
 *   the mouth in rather than snapping it on. A SPEAK: cancels the ramp. The
 *   shutdown mirror is FADEOFF, which fades the frozen frame (glowing mouth
 *   included) to black over HEAD_FADEOFF_MS (4 s).
 */

#include <FastLED.h>
#include <math.h>
#include "pins.h"
#include "head.h"

namespace head {

// ---------------------------------------------------------------------------
// Layout constants
// ---------------------------------------------------------------------------

#define NUM_EYES    2
#define NUM_MOUTH   80
#define NUM_LEDS    (NUM_EYES + NUM_MOUTH)   // 82 total; eyes first, mouth second
#define MOUTH_START NUM_EYES                 // mouth pixels begin at index 2
#define MOUTH_COLS  8
#define MOUTH_ROWS  10
#define MOUTH_HALF  5                        // half-rows per side of the center pair

// mouthIdx — LED index for a (row, col) grid position, accounting for the
// serpentine wiring: even rows run L→R, odd rows run R→L.
static inline uint8_t mouthIdx(uint8_t row, uint8_t col) {
    uint8_t c = (row & 1) ? (uint8_t)(MOUTH_COLS - 1 - col) : col;
    return MOUTH_START + row * MOUTH_COLS + c;
}

// ---------------------------------------------------------------------------
// Emotion colour table
// ---------------------------------------------------------------------------

struct EmotionColor { uint8_t r, g, b; };

#define EMO_NEUTRAL  0
#define EMO_HAPPY    1
#define EMO_EXCITED  2
#define EMO_SAD      3
#define EMO_ANGRY    4
#define EMO_CURIOUS  5
#define EMO_SURPRISED   6
#define EMO_PROUD       7
#define EMO_SKEPTICAL   8
#define EMO_SYMPATHETIC 9
#define EMO_COUNT    10

// IMPORTANT — mouth colour encoding (leds[2..81]):
// The eye pixels (leds[0-1]) are RGB-ordered LEDs; the mouth PCB uses
// GRB-ordered WS2812B.  Both share one data line so FastLED uses a single
// colour order (RGB, matching the eyes).  For mouth pixels this means the
// first wire byte is interpreted by the GRB strip as GREEN, not RED.
// All mouth colours must therefore have R↔G swapped relative to the
// intended physical colour:
//   physical (R, G, B) on GRB mouth strip → store as EmotionColor { G, R, B }
const EmotionColor EMOTION_COLORS[EMO_COUNT] PROGMEM = {
    { 140, 255,   0 },   // neutral  — warm amber   (physical R=255 G=140 B=0 → swap → 140,255,0)
    { 200,   0, 255 },   // happy    — cyan blue    (physical R=0   G=200 B=255 → swap → 200,0,255)
    { 200, 255,   0 },   // excited  — yellow       (physical R=255 G=200 B=0 → swap → 200,255,0)
    {   0,  40, 200 },   // sad      — blue-purple  (physical R=40  G=0   B=200 → swap → 0,40,200)
    {   0, 255,   0 },   // angry    — red          (physical R=255 G=0   B=0 → swap → 0,255,0)
    {   0, 180, 255 },   // curious  — purple       (physical R=180 G=0   B=255 → swap → 0,180,255)
    // Owner, November 15: four more feelings, matching the chest's.
    { 230, 200, 255 },   // surprised   — icy white      (physical R=200 G=230 B=255)
    { 170, 255,  40 },   // proud       — rich gold      (physical R=255 G=170 B=40)
    { 220, 170,   0 },   // skeptical   — sour yellow-green (physical R=170 G=220 B=0)
    {  60, 255, 110 },   // sympathetic — rose           (physical R=255 G=60  B=110)
};

// ---------------------------------------------------------------------------
// LED array
// ---------------------------------------------------------------------------

CRGB leds[NUM_LEDS];

// This strip has its own controller and master brightness. The chest shares the
// FastLED singleton, so the library's board-wide show, brightness and clear
// would act on both strips; nothing in this file may call them.
static CLEDController *strip = nullptr;
static uint8_t masterBrightness = 255;
// Wink (owner, November 21, 2026): one eye dark for a short time, on the host's word. The
// eye's own colour stays in leds[], whatever sets it meanwhile (a blink, the idle breathing,
// an eye refresh); the winking eye is blanked only on its way to the strip.
#define WINK_MS 450UL
static bool     winking     = false;
static uint8_t  winkEye     = 1;       // 0 left, 1 right
static uint32_t winkUntilMs = 0;
static inline void show() {
    if (!winking) { strip->showLeds(masterBrightness); return; }
    const CRGB kept = leds[winkEye];
    leds[winkEye] = CRGB::Black;
    strip->showLeds(masterBrightness);
    leds[winkEye] = kept;
}
static inline void clearAll() { fill_solid(leds, NUM_LEDS, CRGB::Black); }

// ---------------------------------------------------------------------------
// Animation state
// ---------------------------------------------------------------------------

enum AnimMode : uint8_t {
    ANIM_OFF,
    ANIM_SPEAK,
    ANIM_IDLE,
    ANIM_ACTIVE,
    ANIM_SLEEP,
    ANIM_CHARGE,
    ANIM_STARTUP,
};

AnimMode animMode = ANIM_OFF;

// Mouth colour — set by SPEAK:{emotion}; used by BOTH the speaking wave and the
// idle glow, so the mouth settles back to the same emotion colour it spoke in.
// Default matches EMOTION_COLORS[EMO_NEUTRAL] (wire-order, R↔G pre-swapped for
// the GRB mouth strip — see the EMOTION_COLORS note).
EmotionColor mouthColor  = { 140, 255, 0 };   // neutral amber (wire order)
bool prideMouth = false;
uint32_t prideRefreshMs = 0;
uint8_t swirlHue[NUM_MOUTH]; // spatial angle + radius, computed once at boot
#define PRIDE_TIMEOUT_MS 10000UL
uint8_t      speakLevel  = 0;                 // 0–255 audio intensity

// Charge display (owner, November 15): a meter like the chest's. Each row's colour
// belongs to its place, red at the bottom to blue at the top, and the rows light from
// the bottom up to the charge, so the level is read by where the colours stop. A soft
// band of light climbs from the bottom row to the top every CHARGE_CYCLE_MS, then
// rests; above the charge it lights the empty rows faintly in their own colours. On
// entering the mode the rows come on from the bottom over CHARGE_INTRO_MS.
//
// Which end of the grid is the top is taken from the dashboard's 3D model of the head
// (row 0 widest and highest); it has not been checked against the PCB. If the red
// shows at the top, set this to 0.
#define MOUTH_ROW0_AT_TOP 1
#define CHARGE_FRAME_MS   40UL     // 25 frames a second while the band moves
#define CHARGE_INTRO_MS   1200UL
#define CHARGE_CYCLE_MS   3000UL   // one climb and its rest
#define CHARGE_TRAVEL_MS  2000UL   // the climb
#define CHARGE_BAND       1.8f     // half-width of the band, in rows
#define CHARGE_BASE       26       // a lit row's steady brightness: 10 %, the idle glow's level
#define CHARGE_PEAK       100      // a lit row under the band: 39 %, below the speaking bars
#define CHARGE_EMPTY_PEAK 30       // an empty row under the band: 12 %
// Physical colours from the bottom row up.
const uint8_t CHARGE_RGB[MOUTH_ROWS][3] = {
    { 255,   0,   0 },   // red
    { 255,  48,   0 },   // red/orange
    { 255,  96,   0 },   // orange
    { 255, 160,   0 },   // amber
    { 255, 220,   0 },   // yellow
    { 140, 255,   0 },   // yellow/green
    {   0, 255,   0 },   // green
    {   0, 255, 130 },   // green/blue
    {   0, 140, 255 },   // light blue
    {   0,   0, 255 },   // blue
};
uint8_t      chargeRows    = 0;               // rows lit, 0-10, from the last CHARGE:{soc}
uint32_t     chargeStartMs = 0;               // when the mode was entered
uint32_t     chargeFrameMs = 0;               // last frame considered
uint8_t      chargeShown[MOUTH_ROWS];         // each row's brightness as last drawn, bottom first

// Speaking equalizer state — one bar per mouth column (see tickSpeak).
float        colHeight[MOUTH_COLS];           // smoothed bar height, 0–MOUTH_HALF half-rows
float        colPeak[MOUTH_COLS];             // falling peak-dot height per column
float        colPhase[MOUTH_COLS];            // per-column wobble phase (seeded in setup)
// How loud the voice is against how loud it has lately been, and that a little while ago for
// the bars further from the middle (see "Speaking animation").
#define SPEAK_LAG_FRAMES 3                    // each column out from the middle follows this many frames later
#define SPEAK_HISTORY    (3 * SPEAK_LAG_FRAMES + 1)
#define SPEAK_REF_REST   0.40f                // what counts as loud before any voice has been heard
float        speakRef    = SPEAK_REF_REST;    // what counts as loud now, 0..1 of SPEAK_LEVEL's range
float        speakDrive  = 0.0f;              // the voice against it, smoothed, 0..1
float        driveHistory[SPEAK_HISTORY];     // speakDrive at each of the last frames
uint8_t      driveHead   = 0;                 // where the newest is
float        linePhase   = 0.0f;              // the closed mouth's slow shimmer
// The resting glow fades out under the first bars of a line instead of vanishing.
#define SPEAK_OPEN_MS 200UL
uint32_t     speakOpenMs = 0;                 // millis() when the line began over the glow; 0: nothing to fade
uint8_t      speakUnder[NUM_MOUTH];           // the glow as it was then
// Each light's brightness as the glow or the speaking bars last drew it, by row and column.
uint8_t      mouthShown[NUM_MOUTH];

// Mouth idle glow (ACTIVE / IDLE / post-speech), owner, November 24: dark at the edges, brighter
// towards the middle, the whole of it kept dim. Each light has a fixed share of the middle's
// brightness (glowShape): a bell, most at the centre and falling away quickly (GLOW_WIDTH), with
// nothing at all on the outermost lights or where it would be too faint to hold its colour. The
// glow is about as wide as it is tall on the panel, whose lights are about as far apart across
// the middle rows as the rows are (GLOW_ROWS). The middle breathes between GLOW_LOW and all of
// GLOW_PEAK, one breath about every 6.3 s (GLOW_RATE), each spreading from the centre outwards
// (GLOW_SPREAD), so the pulse of November 21 is kept under the new shape. It was a ripple over
// the whole mouth, every light between 3 and 8 %. This must sit well below the speaking bars.
// Frames are at most GLOW_FRAME_MS apart and are shown only when a light's brightness byte has
// changed.
#define GLOW_PEAK   0.085f                    // the very centre at the top of a breath (the four middle lights reach 7.5 %)
#define GLOW_LOW    0.60f                     // ... and at the bottom, as a share of that
#define GLOW_RATE   1.0f                      // rad/s
#define GLOW_SPREAD 2.4f                      // radians of lag from the centre to where the glow ends
#define GLOW_ROWS   3.6f                      // the glow ends this many rows above and below the middle, and at the outermost columns
#define GLOW_WIDTH  0.58f                     // how far out, as a share of that, the glow has fallen to about a third
#define GLOW_FAINT  2                         // a lit light never shows less: under it amber loses its colour
#define GLOW_FRAME_MS 40UL
float        glowPhase     = 0.0f;            // 0.0 – TWO_PI
uint16_t     glowLastSum   = 0xFFFF;          // sentinel: force first frame
uint32_t     glowFrameMs   = 0;
uint8_t      mouthDistance[NUM_MOUTH];        // each light's distance from the mouth's centre, 0..255
uint8_t      glowShape[NUM_MOUTH];            // each light's share of the middle's brightness, 0..255
uint8_t      glowFar[NUM_MOUTH];              // how far out the light is in the glow, 0 the middle to 255 its end

// Glow ramp-in: when the glow starts from a dark mouth (boot / OFF, or waking
// from SLEEP), brightness ramps 0 → full glow over GLOW_RAMP_IN_MS so program
// launch breathes the mouth in instead of snapping it on.  glowRampStartMs is
// the ramp's millis() origin; 0 = no ramp active.  Speaking cancels the ramp
// (the mouth is fully lit by the wave, so the post-speech glow is full level).
// The shutdown mirror is FADEOFF, which fades the frozen frame — glowing
// mouth included — to black over HEAD_FADEOFF_MS.
#define GLOW_RAMP_IN_MS 4000
uint32_t     glowRampStartMs = 0;

// Glow settle: the glow eases in from what the mouth was showing instead of jumping to it:
// from startup's solid mouth over GLOW_SETTLE_MS, from the last bars of a line over SPEAK_CLOSE_MS.
#define GLOW_SETTLE_MS 1500
#define SPEAK_CLOSE_MS 350
uint32_t     glowSettleStartMs = 0;           // 0: not settling
uint16_t     glowSettleMs      = GLOW_SETTLE_MS;
uint8_t      glowSettleFrom[NUM_MOUTH];       // each light's brightness when the settle began

// Mouth startup (owner request, November 21, 2026: "the mouth started out flickering the
// normal yellow light until it were fully illuminated", lined up with the real startup; and
// after seeing it once: it "gets full bright too quickly. It needs to start mostly off and
// sparkle on from the center outwards. It should go solid brightness during the same moment
// the chest LED animation finishes on the normal pattern").
//
// After seeing that in a real start (owner, the same day): "the mouth starts lighting up right
// away. It should start 2 second after the sound clip starts. It also needs to be much less of
// the grid lit up at first, almost like 1 or 2 LEDs twinkling on here and there for a few
// seconds, then the current animation. The startup sound rises until about 3 seconds in then
// starts dying around 4 seconds a little then it starts going back up around 6.5 seconds. The
// mouth LEDs should match that."
//
// So the mouth follows the startup sound. The host says when the sound begins and gives three
// moments in it (STARTUP_SOUND), and how loud it is as it plays (STARTUP_LEVEL):
//   - until the first moment the mouth is dark;
//   - then a light or two twinkle on and off, here and there over the whole mouth, each for
//     about a third of a second: more of them when the sound is louder, almost none in a lull;
//   - from the second moment the mouth is lit from its centre outwards. A front starts at the
//     centre and moves out, slowly at first, reaching STARTUP_REACH at the third moment (the end
//     of the sound's build). A light the front has passed holds, dimly; one it has just reached
//     still shimmers; the lights just ahead of it sparkle, brighter when the sound is louder;
//     everywhere else the twinkles go on. The front stops there without READY: the edges wait.
// Told nothing about the sound (the LED menu's own STARTUP, or a host that never says), the
// mouth assumes it began STARTUP_SOUND_AFTER_MS after STARTUP, with the usual three moments.
//
#define STARTUP_FRAME_MS    40UL
#define STARTUP_FLICKER_MS  55UL     // a sparkle lasts about this long
#define STARTUP_DIM         18       // a light that holds, at the start: 7 %
#define STARTUP_HIGH        34       // ... by the planned end: 13 %
#define STARTUP_GLINT       96       // a sparkle's brightest
#define STARTUP_FULL        64       // solid: a quarter of full brightness on all 80 lights
#define STARTUP_REACH       150      // how far out (of 255) the front gets without READY
#define STARTUP_BAND        60       // the lights this far ahead of the front sparkle
#define STARTUP_FRESH       26       // a light the front passed this recently still shimmers
#define STARTUP_SWEEP_MS    650UL    // READY to the front reaching the corners
#define STARTUP_TWINKLE_MS  360UL    // how long one twinkle lasts, fading in and out
#define STARTUP_TWINKLE_LOW 60       // sound level under which nothing twinkles
#define STARTUP_TWINKLE_TOP 160      // ... and the level at which the most do (about two and a half at once)
#define STARTUP_LEVEL_DEFAULT 130    // told nothing about the sound: a light or two
#define STARTUP_SOUND_AFTER_MS 1600UL   // the sound is heard about this long after STARTUP (run 0fc38daa: 1.62 s)
#define STARTUP_SOUND_PATIENCE_MS 4000UL   // a host that has not said by then is not going to
#define STARTUP_WAIT_MS     2000UL   // the usual three moments, from the sound's start
#define STARTUP_GROW_MS     6500UL
#define STARTUP_SOLID_MS    600UL    // solid this long before the glow eases down
// The eyes blink on as the visor lifts (owner, November 21: "turn on only when he's ready and the
// visor comes on and make them blink on"): dark for EYES_WAKE_MS after READY, two short flashes,
// each brighter, then on. The visor takes about 0.9 s to go up.
#define EYES_WAKE_MS        250UL
#define EYES_OPEN_MS        650UL    // from READY to the eyes being on for good
uint32_t     startupAt       = 0;
uint32_t     startupSpanMs   = LEDS_STARTUP_DEFAULT_MS;
bool         startupHosted   = false;
bool         startupReady    = false;
uint32_t     startupReadyMs  = 0;
uint8_t      startupReadyReach = 0;           // where the front was when READY came
uint8_t      startupReadyLevel = 0;           // ... and how bright the held lights were
uint32_t     startupFrameMs  = 0;
uint16_t     startupSeed     = 0;             // so no two starts sparkle alike
bool         startupSoundKnown = false;       // the sound's start and its three moments are set
uint32_t     startupSoundAt  = 0;             // millis() when the startup sound began
uint32_t     startupWaitMs   = STARTUP_WAIT_MS;   // from the sound's start: dark until here,
uint32_t     startupGrowMs   = STARTUP_GROW_MS;   // twinkling until here,
uint32_t     startupEndMs    = 0;                 // the front fully out (short of READY) here
uint8_t      startupLevel    = STARTUP_LEVEL_DEFAULT;   // how loud the sound is now, 0..255
bool         startupEyesOpen = true;          // false from STARTUP until the eyes have blinked on
CRGB         startupEyeColor = CRGB(255, 200, 0);   // what they open with: the host's EYE, else warm gold

inline void forceGlowRefresh() { glowLastSum = 0xFFFF; }

// The glow starts from these brightnesses and eases to its own over the time given.
static void settleGlowFrom(const uint8_t *from, uint8_t level, uint16_t ms) {
    if (from) memcpy(glowSettleFrom, from, NUM_MOUTH);
    else      memset(glowSettleFrom, level, NUM_MOUTH);
    glowSettleMs      = ms;
    glowSettleStartMs = millis() | 1;         // |1: never the 0 sentinel
    glowRampStartMs   = 0;
    forceGlowRefresh();
}

// Every bar shut and nothing remembered of the last sound, so the next line opens from the closed line.
static void closeBars() {
    for (uint8_t c = 0; c < MOUTH_COLS; c++) colHeight[c] = colPeak[c] = 0.0f;
    for (uint8_t i = 0; i < SPEAK_HISTORY; i++) driveHistory[i] = 0.0f;
    speakDrive = 0.0f;
}

// A line has ended: the bars close into the resting glow over SPEAK_CLOSE_MS.
static void endSpeech() {
    settleGlowFrom(mouthShown, 0, SPEAK_CLOSE_MS);
    closeBars();
}

inline void startGlowRamp()   { glowRampStartMs = millis() | 1;  glowSettleStartMs = 0; }  // |1: never the 0 sentinel
inline void cancelGlowRamp()  { glowRampStartMs = 0;  glowSettleStartMs = 0; }

// Mouth watchdog. The mouth animation (ANIM_SPEAK) is free-running and is only
// stopped by a host command (SPEAK_STOP/ACTIVE/IDLE/OFF). Those bytes can be
// DROPPED on the lossy serial link — a running mouth calls show() every frame,
// which disables interrupts and can lose inbound UART bytes, and the eye keep-
// alive heartbeat contends for the link at stop time. If every stop byte is lost
// the mouth would pulse forever. So: if we are in ANIM_SPEAK but no SPEAK or
// SPEAK_LEVEL has arrived for SPEAK_TIMEOUT_MS, self-extinguish the mouth. The
// host stops sending SPEAK_LEVEL the instant playback ends, so this auto-clears
// the mouth even if the SPEAK_STOP is never received. SPEAK_TIMEOUT_MS must exceed
// the longest real gap between SPEAK_LEVEL writes (host ~30 Hz, delta-throttled);
// 1500 ms is safe — validate on real TTS audio before lowering it.
#define SPEAK_TIMEOUT_MS 1500
uint32_t     lastSpeakActivityMs = 0;         // millis() of last SPEAK/SPEAK_LEVEL

// FADEOFF: smooth shutdown fade — freeze the current frame (eyes) and ramp master
// brightness to 0 over HEAD_FADEOFF_MS, then go dark. A lifelike "powering down".
// headFadeLastBright throttles the fade's show() calls to actual brightness-byte
// changes (255 steps over 4 s ≈ one change per 15.7 ms ≈ 64 fps — a steady,
// regular cadence). With dithering disabled, frames between byte changes are
// bit-identical, so pushing them would only hammer the WS2812B bus and drop
// inbound serial bytes (show() disables interrupts) for zero visual gain.
#define HEAD_FADEOFF_MS 4000
bool         headFading         = false;
uint8_t      headFadeLastBright = 255;
uint32_t     headFadeStartMs     = 0;
uint8_t      headFadeStartBright = 255;

// Idle breathing state
float        idlePhase   = 0.0f;              // 0.0 – TWO_PI

// Eye brightness scale for idle breathing (0.0–1.0).
// tickIdle() updates this continuously.  Non-idle modes reset it to 1.0 via
// setEyes() so full colour is restored.  tickBlink() reads this to restore
// the correct mid-breath level after a blink, and saves it at blink-start.
float        eyeBrightness      = 1.0f;
float        blinkSavedBrightness = 1.0f;     // captured at blink-start

uint32_t     lastMs      = 0;

// ---------------------------------------------------------------------------
// Eye blink state machine
// ---------------------------------------------------------------------------
//
// Rex blinks at random human-like intervals (2–8 s) with a 100–400 ms closed
// duration.  10 % of blinks are double-blinks: eyes reopen briefly (200–400 ms)
// then close again for a second blink before returning to normal.
//
// Three non-blocking states driven by millis():
//
//   BLINK_OPEN        — eyes showing eyeColor; waiting for next blink interval
//   BLINK_CLOSED      — eyes dark for blinkDuration ms
//   BLINK_DOUBLE_WAIT — eyes restored; short pause before the second blink
//
// eyeColor tracks the *intended* eye colour so a blink always restores to the
// current colour even if an EYE: command arrives mid-blink.
//
// eyesActive = false suspends all blinking.  Set false by OFF and SPEAK_STOP;
// set true by EYE: (non-black) and ACTIVE.  The eyes remain physically visible
// in leds[] when blinking is suspended — only new blink triggers are blocked.
//
// Set BLINK_ENABLED = false to freeze eyes open for debugging.

bool BLINK_ENABLED = true;

enum BlinkState : uint8_t {
    BLINK_OPEN,
    BLINK_CLOSED,
    BLINK_DOUBLE_WAIT,
};

CRGB       eyeColor      = CRGB::Black;  // intended colour; restored after blink
bool       eyesActive    = false;        // false → blink triggers suspended
BlinkState blinkState    = BLINK_OPEN;
bool       isSecondBlink = false;        // true during the 2nd leg of a double-blink
uint32_t   blinkTimer    = 0;           // millis() at start of current blink state
uint32_t   blinkInterval = 4000;        // ms to wait before next blink (overwritten in setup)
uint32_t   blinkDuration = 0;           // ms eyes stay closed / paused between double blinks

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

inline uint8_t clampByte(int v) {
    if (v < 0)   return 0;
    if (v > 255) return 255;
    return (uint8_t)v;
}

// setEyes — store the intended colour and write to the LED buffer.
//
// If a blink is in progress (BLINK_CLOSED), only eyeColor is updated; leds[]
// stays dark so the blink isn't interrupted.  tickBlink() restores eyeColor to
// leds[] when the blink ends.
//
// Transitioning from inactive (eyesActive was false) to active resets the blink
// timer so Rex doesn't blink the instant his eyes come on.
//
// NOTE: does NOT call show() — the caller is responsible.
void setEyes(uint8_t r, uint8_t g, uint8_t b) {
    bool wasActive = eyesActive;

    eyeColor      = CRGB(r, g, b);
    eyeBrightness = 1.0f;   // non-idle modes always use full brightness
    eyesActive    = ((r | g | b) != 0);

    // Update leds[] only when eyes are not mid-blink.
    if (blinkState != BLINK_CLOSED) {
        leds[0] = eyeColor;
        leds[1] = eyeColor;
    }
    // When eyes become active for the first time (or return from an off state),
    // start a fresh blink cycle — avoids an immediate blink right after enable.
    if (eyesActive && !wasActive) {
        blinkTimer    = millis();
        blinkInterval = 2000UL + (uint32_t)random(6001);
        blinkState    = BLINK_OPEN;
        isSecondBlink = false;
    }
}

inline void mouthOff() {
    for (uint8_t i = MOUTH_START; i < NUM_LEDS; i++) leds[i] = CRGB::Black;
}

// Return wire-order mouth colour; eyes use RGB, this PCB uses GRB.
CRGB mouthPixel(uint8_t row, uint8_t col, uint8_t brightness, uint32_t now) {
    CRGB color;
    if (prideMouth) {
        uint8_t hue = swirlHue[row * MOUTH_COLS + col] + (uint8_t)(now / 24UL);
        CRGB rgb = CHSV(hue, 255, 255);
        color = CRGB(rgb.g, rgb.r, rgb.b);
    } else {
        color = CRGB(mouthColor.r, mouthColor.g, mouthColor.b);
    }
    return CRGB(scale8(color.r, brightness), scale8(color.g, brightness),
                scale8(color.b, brightness));
}

static uint8_t parseEmotion(const char *s) {
    if (strcmp(s, "happy")   == 0) return EMO_HAPPY;
    if (strcmp(s, "excited") == 0) return EMO_EXCITED;
    if (strcmp(s, "sad")     == 0) return EMO_SAD;
    if (strcmp(s, "angry")   == 0) return EMO_ANGRY;
    if (strcmp(s, "curious") == 0) return EMO_CURIOUS;
    if (strcmp(s, "surprised")   == 0) return EMO_SURPRISED;
    if (strcmp(s, "proud")       == 0) return EMO_PROUD;
    if (strcmp(s, "skeptical")   == 0) return EMO_SKEPTICAL;
    if (strcmp(s, "sympathetic") == 0) return EMO_SYMPATHETIC;
    return EMO_NEUTRAL;
}

// ---------------------------------------------------------------------------
// Eye blink tick — call every loop()
// ---------------------------------------------------------------------------
//
// Only calls show() when the blink state changes (i.e. rarely), so it
// does not interfere with tickSpeak()'s continuous animation rate.  tickSpeak()
// only writes mouth pixels (index 2+) and never touches leds[0]/leds[1], so
// both functions share leds[] safely without coordination.

void tickBlink() {
    if (!BLINK_ENABLED || !eyesActive) return;

    uint32_t now = millis();

    switch (blinkState) {

        case BLINK_OPEN:
            // Wait for the interval then snap eyes off to start the blink.
            // Capture the current breathing brightness so we restore to the
            // same mid-breath level rather than jumping to full eyeColor.
            if (now - blinkTimer >= blinkInterval) {
                blinkSavedBrightness = eyeBrightness;
                leds[0]       = CRGB::Black;
                leds[1]       = CRGB::Black;
                show();
                blinkTimer    = now;
                blinkDuration = 100UL + (uint32_t)random(301);   // 100–400 ms closed
                blinkState    = BLINK_CLOSED;
            }
            break;

        case BLINK_CLOSED:
            // Hold closed, then restore eye colour at the saved breathing level.
            if (now - blinkTimer >= blinkDuration) {
                uint8_t sc = (uint8_t)(blinkSavedBrightness * 255.0f);
                leds[0]    = CRGB(scale8(eyeColor.r, sc),
                                  scale8(eyeColor.g, sc),
                                  scale8(eyeColor.b, sc));
                leds[1]    = leds[0];
                show();
                blinkTimer = now;

                if (!isSecondBlink && (random(10) == 0)) {
                    // 10 % chance: double-blink — brief open pause, then blink again.
                    blinkDuration = 200UL + (uint32_t)random(201);  // 200–400 ms pause
                    blinkState    = BLINK_DOUBLE_WAIT;
                } else {
                    // Normal recovery: reset for next independent blink.
                    isSecondBlink = false;
                    blinkInterval = 2000UL + (uint32_t)random(6001);  // 2–8 s
                    blinkState    = BLINK_OPEN;
                }
            }
            break;

        case BLINK_DOUBLE_WAIT:
            // Eyes are open; wait the inter-blink pause, then close again.
            // Re-capture brightness for the second blink.
            if (now - blinkTimer >= blinkDuration) {
                blinkSavedBrightness = eyeBrightness;
                leds[0]       = CRGB::Black;
                leds[1]       = CRGB::Black;
                show();
                isSecondBlink = true;                              // prevent triple-blink
                blinkTimer    = now;
                blinkDuration = 100UL + (uint32_t)random(301);   // 100–400 ms closed
                blinkState    = BLINK_CLOSED;
            }
            break;
    }
}

// ---------------------------------------------------------------------------
// Command dispatch
// ---------------------------------------------------------------------------

void command(const char *cmd) {
    if (strcmp(cmd, "PRIDE:1") == 0 || strcmp(cmd, "PRIDE:0") == 0) {
        bool enabled = cmd[6] == '1';
        if (prideMouth != enabled) forceGlowRefresh();
        prideMouth = enabled;
        prideRefreshMs = millis();
        return; // colour overlay only: never wakes or starts the mouth
    }


    // SPEAK_LEVEL:{0-255}  — check before SPEAK: to avoid prefix collision
    if (strncmp(cmd, "SPEAK_LEVEL:", 12) == 0) {
        speakLevel = clampByte(atoi(cmd + 12));
        lastSpeakActivityMs = millis();   // feed the mouth watchdog
        return;
    }

    // SPEAK_STOP — mouth returns to the dim idle glow at the current emotion
    // colour; eyes unchanged in leds[] but blink suspended until the next EYE:
    // or ACTIVE re-enables it.
    //
    // Intentionally idempotent: re-entering ANIM_ACTIVE + forcing a glow frame
    // is harmless if already there.  The Pi may send this command multiple
    // times as a reliability measure; the forced refresh guarantees the mouth
    // leaves the bright speak frame for the glow within one loop pass.
    if (strcmp(cmd, "SPEAK_STOP") == 0) {
        if (animMode == ANIM_STARTUP) return;   // nothing was speaking: the flicker runs on
        if (animMode == ANIM_SPEAK) endSpeech();   // the bars close into the glow
        animMode   = ANIM_ACTIVE;
        eyesActive = false;
        blinkState = BLINK_OPEN;   // reset so next activation starts cleanly
        forceGlowRefresh();        // the glow takes the mouth on the next pass
        return;
    }

    // SPEAK:{emotion}
    if (strncmp(cmd, "SPEAK:", 6) == 0) {
        if (animMode == ANIM_STARTUP && !startupEyesOpen) {
            // A line cuts startup short: the eyes come on with it, in the colour last asked for.
            startupEyesOpen = true;
            setEyes(startupEyeColor.r, startupEyeColor.g, startupEyeColor.b);
        }
        uint8_t emo = parseEmotion(cmd + 6);
        EmotionColor ec;
        memcpy_P(&ec, &EMOTION_COLORS[emo], sizeof(EmotionColor));
        mouthColor = ec;
        if (animMode != ANIM_SPEAK) {
            // A new line. Over the resting glow, that fades out under the first bars; over
            // anything else (startup's sparkle, a dark mouth) the bars take the mouth at once.
            const bool resting = animMode == ANIM_ACTIVE || animMode == ANIM_IDLE;
            if (resting) memcpy(speakUnder, mouthShown, NUM_MOUTH);
            else         memset(mouthShown, 0, NUM_MOUTH);
            speakOpenMs = resting ? (millis() | 1) : 0;
            closeBars();
        }
        animMode   = ANIM_SPEAK;
        // Start every utterance from silence. speakLevel persists across a stop,
        // and the host's SPEAK_LEVEL:0 is the one unflushed byte in speak_stop()
        // (SPEAK_STOP itself is sent three times BECAUSE this link drops bytes
        // during show()). If that zero is lost, the mouth reopened at the PREVIOUS
        // line's amplitude the instant SPEAK: arrived — animating at full tilt
        // before a single sample had played. The bars decay from here as usual.
        speakLevel = 0;
        cancelGlowRamp();       // speech lights the mouth fully; glow resumes at full level
        lastSpeakActivityMs = millis();   // reset watchdog at utterance start
        // Eyes not touched — blink continues at current eyeColor/eyesActive state.
        return;
    }

    // IDLE — mouth idle glow; eyes breathe slowly; blink system active.
    if (strcmp(cmd, "IDLE") == 0) {
        if (animMode == ANIM_STARTUP) return;   // startup ends in the awake glow by itself
        if (animMode == ANIM_OFF || animMode == ANIM_SLEEP) {
            startGlowRamp();    // mouth was dark — breathe the glow in over 4 s
        }
        animMode      = ANIM_IDLE;
        idlePhase     = 0.0f;
        eyeBrightness = 1.0f;   // tickIdle will update from here on first tick
        forceGlowRefresh();     // repaint the mouth at glow level immediately
        // Activate blink system if it was suspended (e.g. after SPEAK_STOP).
        // Only start if eyeColor is non-black — no point blinking dark eyes.
        if (!eyesActive && (eyeColor.r | eyeColor.g | eyeColor.b)) {
            eyesActive    = true;
            blinkTimer    = millis();
            blinkInterval = 2000UL + (uint32_t)random(6001);
            blinkState    = BLINK_OPEN;
            isSecondBlink = false;
        }
        return;
    }

    // ACTIVE — mouth idle glow; preserve current eye colour; blink resumes.
    if (strcmp(cmd, "ACTIVE") == 0) {
        if (animMode == ANIM_STARTUP && !startupEyesOpen) return;   // dark behind the visor until READY
        if (animMode != ANIM_STARTUP) {         // during startup the mouth is the sparkle's; eyes as usual
            if (animMode == ANIM_OFF || animMode == ANIM_SLEEP) {
                startGlowRamp();    // mouth was dark — breathe the glow in over 4 s
            }
            animMode = ANIM_ACTIVE;
            forceGlowRefresh();
        }
        if (eyeColor.r || eyeColor.g || eyeColor.b) {
            setEyes(eyeColor.r, eyeColor.g, eyeColor.b);
        } else {
            setEyes(255, 255, 255);
        }
        show();
        return;
    }

    // EYE:{r},{g},{b} — set eye colour; blink resumes.  If the board is in OFF
    // mode, an EYE means "awake again" (the host's eye keep-alive heartbeat
    // re-lighting us, e.g. after a firmware reboot mid-session) — re-enter
    // ACTIVE so the mouth idle glow resumes too.  Never interrupts SPEAK /
    // IDLE / SLEEP.
    if (strncmp(cmd, "EYE:", 4) == 0) {
        if (headFading) return;   // don't re-light the eyes mid shutdown-fade
        int r, g, b;
        if (sscanf(cmd + 4, "%d,%d,%d", &r, &g, &b) == 3) {
            if (animMode == ANIM_STARTUP && !startupEyesOpen) {
                // The visor is shut over them: stay dark, and open in this colour at READY.
                if (r | g | b) startupEyeColor = CRGB(clampByte(r), clampByte(g), clampByte(b));
                return;
            }
            if (animMode == ANIM_OFF && (r | g | b)) {
                animMode = ANIM_ACTIVE;
                startGlowRamp();   // dark → awake: breathe the glow in over 4 s
                forceGlowRefresh();
            }
            setEyes(clampByte(r), clampByte(g), clampByte(b));
            show();
        }
        return;
    }

    // STARTUP / STARTUP:{ms} — the mouth flickers on in the ordinary amber (see "Mouth
    // startup"). A shutdown fade still running is ended first. Eyes are not touched.
    if (strcmp(cmd, "STARTUP") == 0 || strncmp(cmd, "STARTUP:", 8) == 0) {
        const long given = cmd[7] == ':' ? atol(cmd + 8) : 0;
        if (headFading) {
            headFading = false;
            masterBrightness = headFadeStartBright;
        }
        EmotionColor ec;
        memcpy_P(&ec, &EMOTION_COLORS[EMO_NEUTRAL], sizeof(EmotionColor));
        mouthColor     = ec;
        startupHosted  = given > 0;
        startupSpanMs  = startupHosted ? (uint32_t)constrain(given, LEDS_STARTUP_MIN_MS, LEDS_STARTUP_MAX_MS)
                                       : LEDS_STARTUP_DEFAULT_MS;
        startupAt      = millis();
        startupFrameMs = startupAt - STARTUP_FRAME_MS;   // first frame on the next tick
        startupReady   = false;
        startupReadyReach = startupReadyLevel = 0;
        startupSeed    = random16();
        // A host that gave a time will say when the sound begins; the menu's own start assumes it.
        startupSoundKnown = !startupHosted;
        startupSoundAt    = startupAt + STARTUP_SOUND_AFTER_MS;
        startupWaitMs     = STARTUP_WAIT_MS;
        startupGrowMs     = STARTUP_GROW_MS;
        startupEndMs      = startupSpanMs > STARTUP_SOUND_AFTER_MS + STARTUP_GROW_MS + 1000
                          ? startupSpanMs - STARTUP_SOUND_AFTER_MS : STARTUP_GROW_MS + 1000;
        startupLevel      = STARTUP_LEVEL_DEFAULT;
        // The eyes are behind the shut visor: dark until READY, then they blink on in the colour
        // they had, or the one the host sends meanwhile, or warm gold.
        if (eyeColor.r | eyeColor.g | eyeColor.b) startupEyeColor = eyeColor;
        else startupEyeColor = CRGB(255, 200, 0);
        startupEyesOpen = false;
        eyeColor       = CRGB::Black;
        eyesActive     = false;
        blinkState     = BLINK_OPEN;
        isSecondBlink  = false;
        leds[0] = leds[1] = CRGB::Black;
        cancelGlowRamp();
        animMode       = ANIM_STARTUP;
        return;
    }

    // STARTUP_SOUND:{wait},{grow},{end} — the startup sound has just begun (see "Mouth startup").
    if (strncmp(cmd, "STARTUP_SOUND:", 14) == 0) {
        long wait = 0, grow = 0, end = 0;
        if (animMode == ANIM_STARTUP && !startupReady &&
                sscanf(cmd + 14, "%ld,%ld,%ld", &wait, &grow, &end) == 3 && wait >= 0 && grow >= wait && end > grow) {
            startupSoundKnown = true;
            startupSoundAt    = millis();
            startupWaitMs     = (uint32_t)wait;
            startupGrowMs     = (uint32_t)grow;
            startupEndMs      = (uint32_t)end;
        }
        return;
    }

    // STARTUP_LEVEL:{0-255} — how loud the startup sound is now.
    if (strncmp(cmd, "STARTUP_LEVEL:", 14) == 0) {
        startupLevel = clampByte(atoi(cmd + 14));
        return;
    }

    // READY — the host is ready: every mouth light comes on (the finale).
    if (strcmp(cmd, "READY") == 0) {
        if (animMode == ANIM_STARTUP && !startupReady) {
            startupReady   = true;
            startupReadyMs = millis();
        }
        return;
    }

    // WINK / WINK:L / WINK:R — one eye dark for WINK_MS. Nothing to wink with while the eyes
    // are dark (off, asleep, charging, fading, or still behind the visor at startup).
    if (strcmp(cmd, "WINK") == 0 || strcmp(cmd, "WINK:L") == 0 || strcmp(cmd, "WINK:R") == 0) {
        if (headFading || (eyeColor.r | eyeColor.g | eyeColor.b) == 0) return;
        winkEye     = cmd[4] == ':' && cmd[5] == 'L' ? 0 : 1;
        winking     = true;
        winkUntilMs = millis() + WINK_MS;
        show();
        return;
    }

    // FADEOFF — smooth shutdown fade of the current frame (eyes) to black, then
    // dark. Idempotent: a repeat during an in-progress fade is ignored. Freezes
    // the eyes as-is and stops the mouth; the actual ramp runs in loop().
    if (strcmp(cmd, "FADEOFF") == 0) {
        if (!headFading) {
            // Re-assert dither-off at fade start: nothing in this sketch
            // re-enables dithering today, but the dim fade tail is exactly
            // where stray dithering shows as flicker, so guarantee it here
            // rather than trusting setup() alone.
            strip->setDither(0);
            headFading          = true;
            headFadeStartMs     = millis();
            headFadeStartBright = masterBrightness;
            headFadeLastBright  = headFadeStartBright;  // frame already shows at this level
            animMode            = ANIM_OFF;   // stop mouth; leave eyes lit to fade
        }
        return;
    }

    // OFF — all pixels off; blink suspended until EYE: or ACTIVE.
    if (strcmp(cmd, "OFF") == 0) {
        animMode      = ANIM_OFF;
        eyeColor      = CRGB::Black;
        eyesActive    = false;
        blinkState    = BLINK_OPEN;
        isSecondBlink = false;
        clearAll();
        show();
        return;
    }

    // SLEEP — mouth pulses red (breathing); eyes off; blink suspended.
    if (strcmp(cmd, "SLEEP") == 0) {
        animMode      = ANIM_SLEEP;
        eyeColor      = CRGB::Black;
        eyesActive    = false;
        blinkState    = BLINK_OPEN;
        isSecondBlink = false;
        leds[0]       = CRGB::Black;
        leds[1]       = CRGB::Black;
        mouthOff();
        show();
        return;
    }

    // CHARGE:{soc} — off-state charge meter. The battery companion sends this
    // only while the program is down and the charger is attached. A level sent
    // while the meter is already showing only moves the fill line: the rows do
    // not go dark and come on again.
    if (strncmp(cmd, "CHARGE:", 7) == 0) {
        uint8_t soc = clampByte(atoi(cmd + 7));
        if (soc > 100) soc = 100;
        chargeRows = (uint8_t)((soc + 9) / 10);   // ceiling: any charge lights the bottom row
        if (animMode != ANIM_CHARGE) {
            animMode      = ANIM_CHARGE;
            eyeColor      = CRGB::Black;
            eyesActive    = false;
            blinkState    = BLINK_OPEN;
            isSecondBlink = false;
            leds[0]       = CRGB::Black;
            leds[1]       = CRGB::Black;
            mouthOff();
            show();
            chargeStartMs = millis();
            chargeFrameMs = chargeStartMs - CHARGE_FRAME_MS;
            memset(chargeShown, 0, sizeof(chargeShown));     // every row was just blanked
        }
        return;
    }

    // Unknown — ignore silently
}

// ---------------------------------------------------------------------------
// Speaking animation — audio-reactive equalizer bars
// ---------------------------------------------------------------------------
//
// Eight vertical bars — one per mouth column — open outward from the center
// row pair (rows 4/5), VU-meter style.
//
// Owner, November 24: "sometimes it's got this cool pattern and other times it's not as
// cool". Measured on 151 of his cached lines: a bar's height followed SPEAK_LEVEL against the
// top of its range (255), and his voice sits far below that (half of the voiced moments under
// 48, a line's loudest moment anywhere from 91 to 230). With 26 of them replayed through that
// code the mouth was shut or nearly so in half the frames while he spoke, and a bar reached
// three of its five half-rows in one frame in eight: the tall dancing bars showed only in the
// loudest lines. Since 0.9.0, per frame:
//
//   1. The voice is measured against itself: speakRef follows how loud it has lately been
//      (up quickly, back down over seconds, never under SPEAK_REF_MIN, so room hiss is not
//      blown up into speech), and the level is taken as a share of that, with quiet sounds
//      lifted (SPEAK_CURVE). Under SPEAK_GATE is silence.
//   2. It rolls outward: the two middle columns follow the voice now, each column further
//      out follows it SPEAK_LAG_FRAMES frames later, so every sound opens the middle of the
//      mouth and runs out to the corners.
//   3. Each column wobbles: a per-column sine (distinct rate + phase per column, faster
//      when loud) so the bars dance independently instead of pumping in lockstep.
//   4. Is shaped: a fixed center-weighted envelope keeps the middle columns
//      tallest and the corners tapered, so the lit region reads as a mouth.
//   5. Is smoothed: fast attack / slow decay ballistics — bars snap up on a
//      syllable and settle down through the gaps, closing to a thin center
//      line during pauses.
//   6. Trails a peak dot: a marker rides the bar's recent maximum and
//      falls slowly, adding motion above the bars.
//
// A bar is brightest at the middle of the mouth and dimmer towards its tip (SPEAK_TIP), and
// brighter when the voice is louder; the peak dot above it is brighter than the tip under it.
// Brightness is capped at SPEAK_MAX_BRIGHT (45 %). Only the center row pair keeps a dim
// floor, brightest in the middle and fainter towards the corners, with a slow shimmer along
// it, so quiet passages close the mouth to a thin lit line instead of lighting the whole
// panel.  The bar tip is anti-aliased (fractional height renders as a dimmed pixel) to keep
// motion smooth on a 10-row grid.
//
// NOTE: only writes to mouth pixels (index MOUTH_START and above).
// Eye pixels leds[0] and leds[1] are left alone so tickBlink() owns them.

#define SPEAK_MAX_BRIGHT 0.45f   // brightness of a fully lit bar pixel
#define SPEAK_TIP        0.30f   // ... of the outermost half-row, as a share of that
#define SPEAK_QUIET      0.55f   // a bar's brightness for the faintest sound, as a share of a loud one's
#define SPEAK_FLOOR      0.10f   // dim floor on the center row pair, at its middle
#define SPEAK_FLOOR_END  0.045f  // ... and at its two ends
#define SPEAK_ATTACK     20.0f   // bar rise responsiveness (per second)
#define SPEAK_DECAY       9.0f   // bar fall responsiveness (per second)
#define SPEAK_PEAK_FALL   3.5f   // peak-dot fall speed (half-rows per second)
#define SPEAK_GATE       0.05f   // SPEAK_LEVEL under 13: silence
#define SPEAK_REF_MIN    0.30f   // never count anything under SPEAK_LEVEL 77 as loud
#define SPEAK_REF_RISE   10.0f   // how quickly "loud" follows a louder voice (per second)
#define SPEAK_REF_FALL   4.0f    // ... and how many seconds it takes to come back down
#define SPEAK_HEADROOM   1.00f   // this share of "loud" fills the mouth
#define SPEAK_CURVE      0.75f   // under 1: quiet sounds are lifted

// Center-weighted column envelope: middle columns reach full height, corner
// columns top out lower — the lit shape tapers like a mouth, not a box.
const float COL_ENV[MOUTH_COLS] = {
    0.55f, 0.75f, 0.92f, 1.0f, 1.0f, 0.92f, 0.75f, 0.55f
};

// Frame cap. Unthrottled, tickSpeak rendered + show()ed on EVERY loop pass
// (~400 fps): back-to-back frames leave only FastLED's minimum reset gap,
// which crowds the WS2812 latch window — pixels that miss the latch
// reinterpret the next frame's leading bits and flash wrong hues. It also
// kept interrupts disabled for ~2.5 ms per show, >50 % of wall time, eating
// the host's SPEAK_LEVEL bytes. 50 fps is far beyond smooth for this motion,
// leaves ≥17 ms of latch headroom per frame, and frees the UART. Bar
// ballistics use the real elapsed time between frames, so motion speed is
// independent of the render cadence.
#define SPEAK_FRAME_MS 20
uint32_t lastSpeakFrameMs = 0;

void tickSpeak() {
    // Frame-rate cap: skip the update + render + show until the next slot.
    uint32_t now = millis();
    uint32_t elapsedMs = now - lastSpeakFrameMs;
    if (elapsedMs < SPEAK_FRAME_MS) return;
    lastSpeakFrameMs = now;

    float dt = elapsedMs * 0.001f;
    if (dt > 0.1f) dt = 0.1f;   // clamp: ignore stalls (e.g. first frame after idle)

    float level = speakLevel * (1.0f / 255.0f);

    // --- The voice against how loud it has lately been -----------------------
    if (level > speakRef) {
        float k = SPEAK_REF_RISE * dt;
        speakRef += (level - speakRef) * (k > 1.0f ? 1.0f : k);
    } else {
        speakRef -= (speakRef - SPEAK_REF_MIN) * dt / SPEAK_REF_FALL;
    }
    float share = (level - SPEAK_GATE) / (speakRef * SPEAK_HEADROOM - SPEAK_GATE);
    if (share < 0.0f) share = 0.0f;
    if (share > 1.0f) share = 1.0f;
    const float want = share > 0.0f ? powf(share, SPEAK_CURVE) : 0.0f;
    // The host sends a new level about twenty times a second: smoothed, so the roll outward is not steps.
    float follow = (want > speakDrive ? 30.0f : 18.0f) * dt;
    if (follow > 1.0f) follow = 1.0f;
    speakDrive += (want - speakDrive) * follow;
    driveHead = (uint8_t)((driveHead + 1) % SPEAK_HISTORY);
    driveHistory[driveHead] = speakDrive;

    linePhase += 4.0f * dt;
    if (linePhase >= TWO_PI) linePhase -= TWO_PI;

    // The resting glow, fading out under the first bars of a line.
    float under = 0.0f;
    if (speakOpenMs) {
        const uint32_t open = now - speakOpenMs;
        if (open >= SPEAK_OPEN_MS) speakOpenMs = 0;
        else under = 1.0f - (float)open / (float)SPEAK_OPEN_MS;
    }

    // --- Update per-column bar heights + peak dots, and render ---------------
    for (uint8_t c = 0; c < MOUTH_COLS; c++) {
        // The voice as this column hears it: the middle pair now, each column out a little later.
        const uint8_t out = c < MOUTH_COLS / 2 ? (uint8_t)(MOUTH_COLS / 2 - 1 - c) : (uint8_t)(c - MOUTH_COLS / 2);
        const float heard = driveHistory[(driveHead + SPEAK_HISTORY - out * SPEAK_LAG_FRAMES) % SPEAK_HISTORY];

        // Per-column wobble: distinct rate per column, faster when loud.
        colPhase[c] += (2.6f + 0.6f * c) * (0.7f + 1.3f * heard) * dt;
        if (colPhase[c] >= TWO_PI) colPhase[c] -= TWO_PI;
        float wobble = 0.62f + 0.38f * sinf(colPhase[c]);

        // Target height in half-rows (0 – MOUTH_HALF from the center pair).
        float target = heard * wobble * COL_ENV[c] * 5.6f;
        if (target > (float)MOUTH_HALF) target = (float)MOUTH_HALF;

        // Fast attack, slow decay.
        float rate = (target > colHeight[c]) ? SPEAK_ATTACK : SPEAK_DECAY;
        float k = rate * dt;
        if (k > 1.0f) k = 1.0f;
        colHeight[c] += (target - colHeight[c]) * k;

        // Peak dot rides the maximum, then falls slowly.
        if (colHeight[c] > colPeak[c]) {
            colPeak[c] = colHeight[c];
        } else {
            colPeak[c] -= SPEAK_PEAK_FALL * dt;
            if (colPeak[c] < 0.0f) colPeak[c] = 0.0f;
        }

        uint8_t peakRow = (uint8_t)colPeak[c];
        if (peakRow > MOUTH_HALF - 1) peakRow = MOUTH_HALF - 1;
        bool showPeak = (colPeak[c] > colHeight[c] + 0.6f);

        // The closed line: brightest in the middle, fainter at the ends, shimmering outwards.
        const float floorHere = (SPEAK_FLOOR_END + (SPEAK_FLOOR - SPEAK_FLOOR_END) * (COL_ENV[c] - COL_ENV[0]) / (1.0f - COL_ENV[0]))
                              * (0.85f + 0.15f * sinf(linePhase - 0.9f * out));
        const float loud = SPEAK_MAX_BRIGHT * (SPEAK_QUIET + (1.0f - SPEAK_QUIET) * heard);

        for (uint8_t d = 0; d < MOUTH_HALF; d++) {
            // Bar fill at this half-row: >=1 full, 0–1 anti-aliased tip, <=0 off.
            float fill = colHeight[c] - (float)d;
            if (fill > 1.0f) fill = 1.0f;
            if (fill < 0.0f) fill = 0.0f;

            // Brightest at the middle of the mouth, dimmer towards the tip.
            float bright = fill * loud * (1.0f - (1.0f - SPEAK_TIP) * (float)d / (float)(MOUTH_HALF - 1));
            if (d == 0 && bright < floorHere) bright = floorHere;
            if (showPeak && d == peakRow && bright < SPEAK_MAX_BRIGHT * 0.7f) {
                bright = SPEAK_MAX_BRIGHT * 0.7f;
            }

            uint8_t sc = (uint8_t)(bright * 255.0f);
            for (uint8_t side = 0; side < 2; side++) {
                const uint8_t row = side ? (uint8_t)(5 + d) : (uint8_t)(4 - d);
                const uint8_t at  = row * MOUTH_COLS + c;
                uint8_t shown = sc;
                if (under > 0.0f) {
                    const uint8_t glow = (uint8_t)(speakUnder[at] * under);
                    if (glow > shown) shown = glow;
                }
                mouthShown[at] = shown;
                leds[mouthIdx(row, c)] = mouthPixel(row, c, shown, now);
            }
        }
    }
    show();
}

// ---------------------------------------------------------------------------
// Mouth idle glow — dim emotional pulse while awake and not speaking
// ---------------------------------------------------------------------------
//
// The middle of the mouth glows dimly in mouthColor and the glow falls away to dark edges
// (glowShape); it breathes slowly, each breath spreading from the centre outwards (see
// GLOW_PEAK, GLOW_LOW, GLOW_RATE and GLOW_SPREAD), so the mouth shows a soft version of the
// current emotion colour whenever Rex is on but quiet.  Runs in ANIM_ACTIVE and ANIM_IDLE.
//
// Writes ONLY mouth pixels (MOUTH_START …) and returns true when the frame
// changed — the caller decides when to show(), so IDLE mode can fold
// the glow and the eye breathing into a single show per frame.  At most one
// frame every GLOW_FRAME_MS; forceGlowRefresh() makes the next tick
// repaint unconditionally (used on mode entry so a stale speak frame or
// mouthOff() never lingers).
//
// NOTE: eye pixels leds[0]/leds[1] are never written here — eyes stay
// exclusively owned by setEyes()/tickIdle()/tickBlink().

bool tickMouthGlow(float dt) {
    glowPhase += GLOW_RATE * dt;
    if (glowPhase >= TWO_PI) glowPhase -= TWO_PI;

    const uint32_t now = millis();
    const bool forced = glowLastSum == 0xFFFF;
    if (!forced && now - glowFrameMs < GLOW_FRAME_MS) return false;
    glowFrameMs = now;

    // Ramp-in from dark (program launch / wake): scale the glow 0 → 1 over
    // GLOW_RAMP_IN_MS, then drop the ramp state once complete.
    float gain = 1.0f;
    if (glowRampStartMs) {
        uint32_t elapsed = now - glowRampStartMs;
        if (elapsed >= GLOW_RAMP_IN_MS) {
            glowRampStartMs = 0;
        } else {
            gain = (float)elapsed / (float)GLOW_RAMP_IN_MS;
        }
    }
    // Settle from startup's solid mouth or a line's last bars: ease to the glow, never jump.
    bool  settling = false;
    float settled  = 1.0f;
    if (glowSettleStartMs) {
        uint32_t elapsed = now - glowSettleStartMs;
        if (elapsed >= glowSettleMs) {
            glowSettleStartMs = 0;
        } else {
            settling = true;
            settled  = (float)elapsed / (float)glowSettleMs;
        }
    }

    uint8_t  scale[NUM_MOUTH];
    uint16_t sum = 0;
    for (uint8_t i = 0; i < NUM_MOUTH; i++) {
        float brightness = 0.0f;
        if (glowShape[i]) {
            // The further from the centre, the later the breath arrives and the fainter the light.
            const float breath = 0.5f + 0.5f * sinf(glowPhase - GLOW_SPREAD * glowFar[i] * (1.0f / 255.0f));
            brightness = GLOW_PEAK * glowShape[i] * (1.0f / 255.0f) * (GLOW_LOW + (1.0f - GLOW_LOW) * breath) * gain;
        }
        if (settling) {
            const float from = glowSettleFrom[i] * (1.0f / 255.0f);
            brightness = from + (brightness - from) * settled;
        }
        scale[i] = (uint8_t)(brightness * 255.0f + 0.5f);
        // The faintest lit lights hold steady at the bottom of a breath instead of going out.
        if (glowShape[i] && scale[i] < GLOW_FAINT && gain >= 1.0f && !settling) scale[i] = GLOW_FAINT;
        sum += (uint16_t)scale[i] * (uint16_t)(i + 1);
    }
    if (sum == 0xFFFF) sum = 0xFFFE;
    if (!prideMouth && !forced && sum == glowLastSum) return false;   // the rainbow's colours always move
    glowLastSum = sum;
    memcpy(mouthShown, scale, NUM_MOUTH);

    for (uint8_t row = 0; row < MOUTH_ROWS; row++) {
        for (uint8_t col = 0; col < MOUTH_COLS; col++) {
            leds[mouthIdx(row, col)] = mouthPixel(row, col, scale[row * MOUTH_COLS + col], now);
        }
    }
    return true;
}

// ---------------------------------------------------------------------------
// Idle animation — slow eye breathing
// ---------------------------------------------------------------------------
//
// Eyes pulse gently between 30 % and 100 % of eyeColor using a sine wave
// (period ≈ 7.8 s at 0.8 rad/s).  eyeBrightness is updated every tick so
// tickBlink() can save the mid-breath level at blink-start and restore to
// it exactly after the blink ends, avoiding a jarring brightness jump.
//
// leds[] is NOT written during BLINK_CLOSED — tickBlink() owns the eye
// pixels while the eyes are dark, and will restore them with the saved level.
//
// IMPORTANT — mouth pixels (indices 2-81 / MOUTH_START … NUM_LEDS-1):
//   tickIdle() intentionally writes ONLY leds[0] and leds[1] (the two eyes).
//   Mouth pixels are owned by tickMouthGlow() while awake; any edit that
//   writes a mouth pixel inside tickIdle() is a bug.
//
// Returns true when the eye frame changed — the caller decides when to
// show() (folded with the mouth glow into one show per frame), and
// the byte-change throttle (~45 fps at this breathing rate) keeps show()
// calls — which disable interrupts and can drop inbound serial bytes — off
// the loop()'s hot path.

bool tickIdle(float dt) {
    idlePhase += 0.8f * dt;
    if (idlePhase >= TWO_PI) idlePhase -= TWO_PI;

    // Brightness: 0.30 at trough → 1.00 at peak
    eyeBrightness = 0.30f + 0.35f * (1.0f + sinf(idlePhase));

    // Let tickBlink() own leds[] while eyes are closed.
    if (blinkState == BLINK_CLOSED) return false;

    // Only push a new frame when the scaled brightness byte actually changed.
    static uint8_t lastSc = 255;
    uint8_t sc = (uint8_t)(eyeBrightness * 255.0f);
    if (sc == lastSc) return false;
    lastSc = sc;

    // Only eye pixels — mouth pixels are never written here.
    leds[0] = CRGB(scale8(eyeColor.r, sc),
                   scale8(eyeColor.g, sc),
                   scale8(eyeColor.b, sc));
    leds[1] = leds[0];
    // leds[2] … leds[NUM_LEDS-1] (mouth) are intentionally NOT modified.
    return true;
}

// ---------------------------------------------------------------------------
// Sleep animation — slow red mouth breathing
// ---------------------------------------------------------------------------
//
// All 80 mouth pixels ramp linearly from 0 to 30 % brightness over 4 s then
// back to 0 over the next 4 s (triangle wave, 8 s period).  Peak R value is
// 76 out of 255 (≈ 30 %).  Mouth pixels are GRB-ordered; R↔G is swapped in
// all mouth writes so the physical display is red (see EMOTION_COLORS note).
//
// show() is only called when the red byte changes (≈ every 52 ms at
// this ramp rate) — avoids hammering the WS2812B bus hundreds of times per
// second, which causes visible colour glitches.
//
// Eyes are NOT touched — they are off in SLEEP state.

void tickSleep() {
    uint32_t now   = millis();
    // Triangle wave: phase 0→0.5 ramps up, 0.5→1.0 ramps down.
    float    phase = (float)(now % 8000UL) / 8000.0f;      // 0.0 – 1.0
    float    tri   = (phase < 0.5f) ? (phase * 2.0f)
                                    : (2.0f - phase * 2.0f); // 0.0 – 1.0
    // Cap at 30 % brightness (76 / 255 ≈ 29.8 %)
    uint8_t  r     = (uint8_t)(tri * 76.0f);

    // Only push a new frame when the value has actually changed.
    // This throttles show() to ~19 calls/s and eliminates bus hammering.
    static uint8_t lastR = 255;   // sentinel: non-zero so first call always fires
    if (r == lastR) return;
    lastR = r;

    for (uint8_t i = MOUTH_START; i < NUM_LEDS; i++) {
        leds[i] = CRGB(0, r, 0);   // GRB mouth via RGB FastLED: swap R↔G → physical red
    }
    show();
}

// Charge display (see the block above chargeRows). One frame every
// CHARGE_FRAME_MS, pushed only when a row's brightness changed, so nothing is
// sent while the pulse rests.
void tickCharge() {
    const uint32_t now = millis();
    if ((uint32_t)(now - chargeFrameMs) < CHARGE_FRAME_MS) return;
    chargeFrameMs = now;

    const uint32_t elapsed = now - chargeStartMs;
    uint8_t lit   = chargeRows;     // rows holding their steady light
    float   pulse = -100.0f;        // centre of the climbing band, in rows from the bottom; far off at rest
    if (elapsed < CHARGE_INTRO_MS) {
        lit = (uint8_t)((uint32_t)chargeRows * elapsed / CHARGE_INTRO_MS);
    } else {
        const uint32_t t = (elapsed - CHARGE_INTRO_MS) % CHARGE_CYCLE_MS;
        if (t < CHARGE_TRAVEL_MS)
            pulse = -CHARGE_BAND + (MOUTH_ROWS - 1 + 2.0f * CHARGE_BAND) * (float)t / (float)CHARGE_TRAVEL_MS;
    }

    bool changed = false;
    for (uint8_t level = 0; level < MOUTH_ROWS; level++) {
        float w = 1.0f - fabsf((float)level - pulse) / CHARGE_BAND;
        if (w < 0.0f) w = 0.0f;
        w = w * w * (3.0f - 2.0f * w);                    // soft edges
        uint8_t bright = 0;
        if (level < lit)              bright = (uint8_t)(CHARGE_BASE + w * (CHARGE_PEAK - CHARGE_BASE));
        else if (level >= chargeRows) bright = (uint8_t)(w * CHARGE_EMPTY_PEAK);
        if (bright == chargeShown[level]) continue;
        chargeShown[level] = bright;
        changed = true;
        // Physical RGB -> stored R/G swapped for the GRB mouth strip.
        const CRGB colour(scale8(CHARGE_RGB[level][1], bright), scale8(CHARGE_RGB[level][0], bright),
                          scale8(CHARGE_RGB[level][2], bright));
        const uint8_t row = MOUTH_ROW0_AT_TOP ? (uint8_t)(MOUTH_ROWS - 1 - level) : level;
        for (uint8_t col = 0; col < MOUTH_COLS; col++) leds[mouthIdx(row, col)] = colour;
    }
    if (changed) show();
}

// ---------------------------------------------------------------------------
// Mouth startup — sparkling on from the centre outwards (see "Mouth startup" above)
// ---------------------------------------------------------------------------

// How far out the front is, and how bright a held light, at this much (0..255) of the growth.
static inline uint8_t startupReachAt(uint16_t progress) {
    const float part = progress / 255.0f;
    return (uint8_t)(STARTUP_REACH * part * part);          // slow at first
}
static inline uint8_t startupLevelAt(uint16_t progress) {
    return STARTUP_DIM + (uint8_t)((uint16_t)progress * (STARTUP_HIGH - STARTUP_DIM) / 255);
}

void tickStartup() {
    const uint32_t now = millis();
    const uint32_t planEnd = startupAt + startupSpanMs;
    // A host that was to say when the sound begins and has not: assume the usual.
    if (!startupSoundKnown && now - startupAt >= STARTUP_SOUND_PATIENCE_MS) startupSoundKnown = true;
    // Where the sound is: before the wait the mouth is dark, then twinkles, then grows.
    const int32_t  heard    = startupSoundKnown ? (int32_t)(now - startupSoundAt) : -1;
    const bool     awake    = heard >= (int32_t)startupWaitMs;
    const bool     growing  = heard >= (int32_t)startupGrowMs;
    const uint32_t growSpan = startupEndMs > startupGrowMs ? startupEndMs - startupGrowMs : 1;
    const uint16_t progress = !growing ? 0
                            : (uint32_t)(heard - startupGrowMs) >= growSpan ? 255
                            : (uint16_t)((uint32_t)(heard - startupGrowMs) * 255UL / growSpan);
    // READY's stand-in: the planned end when it times itself, a grace later when the host
    // was to say it.
    if (!startupReady && (int32_t)(now - (planEnd + (startupHosted ? LEDS_READY_GRACE_MS : 0))) >= 0) {
        startupReady   = true;
        startupReadyMs = now;
    }
    if (startupReady && startupReadyReach == 0 && startupReadyLevel == 0) {
        startupReadyReach = startupReachAt(progress) | 1;   // |1: never the "not yet noted" zero
        startupReadyLevel = startupLevelAt(progress);
    }
    const uint32_t since = startupReady ? now - startupReadyMs : 0;
    if (startupReady && since >= LEDS_FINALE_MS + STARTUP_SOLID_MS) {
        if (!startupEyesOpen) {
            startupEyesOpen = true;
            setEyes(startupEyeColor.r, startupEyeColor.g, startupEyeColor.b);
        }
        animMode          = ANIM_ACTIVE;     // the glow takes over and eases down from solid
        settleGlowFrom(nullptr, STARTUP_FULL, GLOW_SETTLE_MS);
        return;
    }
    if (now - startupFrameMs < STARTUP_FRAME_MS) return;
    startupFrameMs = now;

    // Where the front is and how bright a held light is.
    uint16_t reach;
    uint8_t  level;
    const bool solid = startupReady && since >= LEDS_FINALE_MS;
    if (!startupReady) {
        reach = startupReachAt(progress);
        level = startupLevelAt(progress);
    } else {
        // Out to the corners and a band beyond in STARTUP_SWEEP_MS; brighter all the way to solid.
        const uint16_t all = 255 + STARTUP_BAND;
        reach = since >= STARTUP_SWEEP_MS ? all
              : (uint16_t)(startupReadyReach + (uint32_t)(all - startupReadyReach) * since / STARTUP_SWEEP_MS);
        level = since >= LEDS_FINALE_MS ? STARTUP_FULL
              : (uint8_t)(startupReadyLevel + (uint32_t)(STARTUP_FULL - startupReadyLevel) * since / LEDS_FINALE_MS);
    }
    const uint32_t slot = now / STARTUP_FLICKER_MS;

    if (!startupEyesOpen) {
        // Dark behind the visor; after READY two flashes, the second brighter, then on for good,
        // when the ordinary blinking takes them over.
        if (startupReady && since >= EYES_OPEN_MS) {
            startupEyesOpen = true;
            setEyes(startupEyeColor.r, startupEyeColor.g, startupEyeColor.b);
        } else {
            const uint32_t t = startupReady && since >= EYES_WAKE_MS ? since - EYES_WAKE_MS : 0xFFFF;
            const uint8_t flash = t < 70 ? 110 : t < 180 ? 0 : t < 270 ? 180 : 0;
            CRGB eye = startupEyeColor;
            eye.nscale8(flash);
            leds[0] = leds[1] = eye;
        }
    }

    // Twinkles: how many are due at once follows the sound's loudness (none under the low level,
    // about two and a half at its top), each fading in and out over its own stretch of time.
    const uint8_t loud = startupLevel <= STARTUP_TWINKLE_LOW ? 0
                       : startupLevel >= STARTUP_TWINKLE_TOP ? 255
                       : (uint8_t)((uint16_t)(startupLevel - STARTUP_TWINKLE_LOW) * 255 / (STARTUP_TWINKLE_TOP - STARTUP_TWINKLE_LOW));
    const uint8_t twinkleChance = scale8(8, loud);          // of 256, for each light in each stretch
    const uint8_t glintTop = (uint8_t)(STARTUP_DIM + scale8(STARTUP_GLINT - STARTUP_DIM, 128 + startupLevel / 2));

    for (uint8_t row = 0; row < MOUTH_ROWS; row++) {
        for (uint8_t col = 0; col < MOUTH_COLS; col++) {
            const uint8_t i = row * MOUTH_COLS + col;
            uint8_t brightness = 0;
            if (solid) {
                brightness = STARTUP_FULL;
            } else if (awake || startupReady) {
                // A twinkle, wherever the light is: its own stretch of time, up and down again.
                const uint32_t mine    = now + (uint32_t)ledsFlick8(i, 77u + startupSeed) * 11u;
                const uint32_t stretch = mine / STARTUP_TWINKLE_MS;
                uint8_t twinkle = 0;
                if (ledsFlick8(stretch, 900u + i + startupSeed) < twinkleChance) {
                    const uint32_t into = mine % STARTUP_TWINKLE_MS;
                    const uint32_t half = STARTUP_TWINKLE_MS / 2;
                    const uint32_t rise = into < half ? into : STARTUP_TWINKLE_MS - into;       // 0..half
                    twinkle = (uint8_t)(6 + (uint32_t)(glintTop - 6) * rise / half);
                }
                brightness = twinkle;
                if (growing || startupReady) {
                    // Each light sits a little nearer or further than its place, so the front is ragged.
                    const int16_t distance = (int16_t)mouthDistance[i] + (int16_t)(ledsFlick8(i, startupSeed) % 25) - 12;
                    const int16_t behind = (int16_t)reach - distance;          // how long ago the front passed
                    const uint8_t coin  = ledsFlick8(slot, 300u + i + startupSeed);
                    const uint8_t glint = ledsFlick8(slot, 600u + i);
                    if (behind >= STARTUP_FRESH) {
                        brightness = level;                                    // it holds
                    } else if (behind >= 0) {
                        // Just caught: mostly on, shimmering between its level and a glint.
                        brightness = coin < 215 ? (uint8_t)(level + scale8(glint, glintTop > level ? glintTop - level : 0) / 2) : 0;
                    } else if (behind > -STARTUP_BAND && reach > 0) {
                        // Ahead of the front: sparkles, more often the nearer it is.
                        const uint8_t chance = (uint8_t)(18 + (uint16_t)(STARTUP_BAND + behind) * 92 / STARTUP_BAND);
                        if (coin < chance) brightness = (uint8_t)(STARTUP_DIM + scale8(glint, glintTop - STARTUP_DIM));
                    }
                }
            }
            leds[mouthIdx(row, col)] = mouthPixel(row, col, brightness, now);
        }
    }
    show();
}

// ---------------------------------------------------------------------------
// Main animation tick — call every loop()
// ---------------------------------------------------------------------------

void tickAnimation() {
    if (animMode == ANIM_OFF) return;

    uint32_t now     = millis();
    float    dt      = (now - lastMs) * 0.001f;   // seconds since last tick
    lastMs           = now;

    if (dt > 0.1f) dt = 0.1f;   // clamp: ignore stalls > 100 ms (e.g. first tick)

    if (animMode == ANIM_SPEAK) {
        // Mouth watchdog backstop: if no SPEAK/SPEAK_LEVEL has arrived recently the
        // host has stopped speaking and the stop command was dropped — settle the
        // mouth into the idle glow ourselves. Only touches the mouth + animMode;
        // eyes/blink are left alone (the heartbeat keeps them lit), so this is a
        // clean stop.
        if ((uint32_t)(now - lastSpeakActivityMs) > SPEAK_TIMEOUT_MS) {
            endSpeech();
            animMode = ANIM_ACTIVE;
            return;   // glow takes over on the next tick
        }
        tickSpeak();
        return;
    }
    if (animMode == ANIM_SLEEP) { tickSleep(); return; }
    if (animMode == ANIM_CHARGE) { tickCharge(); return; }
    if (animMode == ANIM_STARTUP) { tickStartup(); return; }

    // ANIM_ACTIVE / ANIM_IDLE — mouth idle glow, plus eye breathing in IDLE.
    // Both ticks only mark the buffer; a single show() pushes the combined frame.
    bool changed = tickMouthGlow(dt);
    if (animMode == ANIM_IDLE) changed = tickIdle(dt) || changed;
    if (changed) show();
}

// ---------------------------------------------------------------------------
// setup / loop
// ---------------------------------------------------------------------------

void begin() {
    for (uint8_t row = 0; row < MOUTH_ROWS; row++) {
        for (uint8_t col = 0; col < MOUTH_COLS; col++) {
            float x = col - 3.5f, y = row - 4.5f;
            float angle = atan2f(y, x) + PI;
            swirlHue[row * MOUTH_COLS + col] = (uint8_t)(
                (uint16_t)(angle * (255.0f / TWO_PI) + sqrtf(x*x + y*y) * 18.0f) & 255);
        }
    }
    // Distance of each mouth light from the centre, the corners furthest (255): the startup
    // sparkle spreads along it. The mouth is taller than wide, so each axis is measured in
    // halves of its own span and the spread reaches all four edges together.
    for (uint8_t row = 0; row < MOUTH_ROWS; row++) {
        for (uint8_t col = 0; col < MOUTH_COLS; col++) {
            const float dx = (col - 3.5f) / 3.5f, dy = (row - 4.5f) / 4.5f;
            mouthDistance[row * MOUTH_COLS + col] = (uint8_t)(255.0f * sqrtf((dx * dx + dy * dy) * 0.5f));
        }
    }
    // The resting glow's shape: a bell, all of the middle's brightness at the centre and falling
    // away quickly, with nothing where it ends: the outermost lights stay dark, and so does any
    // light the bell leaves too faint to hold its colour.
    for (uint8_t row = 0; row < MOUTH_ROWS; row++) {
        for (uint8_t col = 0; col < MOUTH_COLS; col++) {
            const uint8_t i = row * MOUTH_COLS + col;
            const float dx = (col - 3.5f) / 3.5f, dy = (row - 4.5f) / GLOW_ROWS;
            const float out = sqrtf(dx * dx + dy * dy);
            const float share = out >= 1.0f ? 0.0f : expf(-(out * out) / (GLOW_WIDTH * GLOW_WIDTH));
            // A light too faint to show GLOW_FAINT at the top of a breath is left dark.
            glowShape[i] = share * GLOW_PEAK * 255.0f >= GLOW_FAINT ? (uint8_t)(255.0f * share) : 0;
            glowFar[i]   = out >= 1.0f ? 255 : (uint8_t)(255.0f * out);
        }
    }
    strip = &FastLED.addLeds<WS2812B, HEAD_DATA_PIN, RGB>(leds, NUM_LEDS);
    masterBrightness = 255;
    // Temporal dithering OFF. FastLED's binary dithering simulates extra
    // brightness depth by alternating pixel values between frames, which only
    // looks smooth with a very fast, steady show() cadence. This sketch
    // deliberately throttles show() to value changes (glow ~8 fps, idle
    // breathing ~45 fps, fade ~64 fps), so at low brightness the dither
    // alternation is visible as flicker — worst in the dim tail of FADEOFF.
    // With dithering off, dim levels quantize to steady values instead.
    strip->setDither(0);

    // WS2812B pixels can latch random data on power-on before the first show().
    // A brief delay lets the supply voltage stabilise so the reset pulse is
    // clean, then we explicitly zero every pixel — including pixel 2 (MOUTH_START)
    // which is the first mouth pixel and the most likely to stay lit from glitch.
    delay(50);
    clearAll();   // fill leds[] with CRGB::Black
    show();    // push zeros to every pixel on the strip
    // Belt-and-suspenders: zero the buffer a second time and show again.
    // The first show() resets any latched state; the second guarantees all
    // 82 pixels — especially the mouth — start in a known-off state.
    clearAll();
    show();

    lastMs        = millis();

    // Stagger the equalizer columns' wobble phases so the bars never start
    // (or drift) in lockstep. Heights/peaks start closed.
    for (uint8_t c = 0; c < MOUTH_COLS; c++) {
        colPhase[c]  = c * 0.9f;
        colHeight[c] = 0.0f;
        colPeak[c]   = 0.0f;
    }
    closeBars();
    memset(mouthShown, 0, NUM_MOUTH);
    memset(speakUnder, 0, NUM_MOUTH);
    memset(glowSettleFrom, 0, NUM_MOUTH);

    // Initialise blink state machine — first blink fires somewhere in 2–8 s.
    blinkTimer    = millis();
    blinkInterval = 2000UL + (uint32_t)random(6001);
    blinkState    = BLINK_OPEN;
    isSecondBlink = false;
}

void tick() {
    if (winking && (int32_t)(millis() - winkUntilMs) >= 0) {
        winking = false;       // the eye comes back as it now is
        show();
    }
    if (headFading) {
        // Shutdown fade: freeze the current frame and ramp master brightness to 0
        // over HEAD_FADEOFF_MS, then go dark. Skip the normal ticks so nothing
        // redraws (or blinks) over the fading eyes.
        //
        // Non-blocking millis()-based step: nothing in this branch (or in the
        // serial reader above it) blocks, so the refresh cadence stays steady.
        // show() is pushed only when the computed brightness BYTE changes —
        // the linear ramp makes those changes land every ~15.7 ms (≈64 fps),
        // a tight, regular cadence. Re-showing bit-identical frames between
        // steps (the old behaviour: show() every loop pass, ~400 fps) added
        // nothing visually with dithering off and cost serial bytes, since
        // every show() disables interrupts for ~2.5 ms.
        uint32_t elapsed = millis() - headFadeStartMs;
        if (elapsed >= HEAD_FADEOFF_MS) {
            headFading = false;
            eyeColor   = CRGB::Black;
            eyesActive = false;
            animMode   = ANIM_OFF;
            masterBrightness = headFadeStartBright;  // restore for next use
            clearAll();
            show();
        } else {
            uint8_t b = (uint8_t)(
                (uint32_t)headFadeStartBright * (HEAD_FADEOFF_MS - elapsed) / HEAD_FADEOFF_MS);
            if (b != headFadeLastBright) {
                headFadeLastBright = b;
                masterBrightness = b;
                show();   // re-show the frozen leds[] at the new level
            }
        }
        return;
    }

    if (prideMouth && (uint32_t)(millis() - prideRefreshMs) > PRIDE_TIMEOUT_MS) {
        prideMouth = false;
        forceGlowRefresh();
    }
    tickAnimation();   // mouth animation (speak wave, idle)
    tickBlink();       // eye blink state machine (all modes)
}

}  // namespace head
