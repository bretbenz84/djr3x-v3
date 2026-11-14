/*
 * djr3x_leds.ino — DJ-R3X combined LED board (one ESP32, head and chest strips).
 *
 * Serial: 115200 baud, ASCII, one command per line. Both strips stay dark after
 * boot until a command arrives.
 *
 *   HELLO        reply "hello djr3x_leds <version> head=82@<pin> chest=98@<pin>"
 *   H:<command>  head only   (commands listed in head.cpp)
 *   C:<command>  chest only  (commands listed above command() in chest.cpp)
 *   <command>    both strips; each ignores what it does not know
 *
 * The same reply, starting "ready", is printed once when the board has booted.
 * Nothing else is ever sent. A line longer than the buffer is dropped whole.
 */

#include <FastLED.h>
#include <esp_random.h>
#include "pins.h"
#include "head.h"
#include "chest.h"

static char    line[64];
static uint8_t lineLength = 0;
static bool    lineTooLong = false;

static void identify(const char *word) {
    Serial.printf("%s djr3x_leds %s head=82@%d chest=98@%d\n",
                  word, LEDS_VERSION, HEAD_DATA_PIN, CHEST_DATA_PIN);
}

static void dispatch(const char *cmd) {
    if (strcmp(cmd, "HELLO") == 0) { identify("hello"); return; }
    if (cmd[0] == 'H' && cmd[1] == ':') { head::command(cmd + 2); return; }
    if (cmd[0] == 'C' && cmd[1] == ':') { chest::command(cmd + 2); return; }
    head::command(cmd);
    chest::command(cmd);
}

static void readSerial() {
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r') {
            if (lineLength > 0 && !lineTooLong) {
                line[lineLength] = '\0';
                dispatch(line);
            }
            lineLength = 0;
            lineTooLong = false;
        } else if (lineLength < sizeof(line) - 1) {
            line[lineLength++] = c;
        } else {
            lineTooLong = true;
        }
    }
}

void setup() {
    Serial.begin(LEDS_BAUD);
    randomSeed(esp_random());              // blink and pattern timing differ each boot
    random16_add_entropy((uint16_t)esp_random());
    head::begin();
    chest::begin();
    identify("ready");
}

void loop() {
    readSerial();
    head::tick();
    chest::tick();
    delay(1);   // both strips keep time in milliseconds; let the idle task run
}
