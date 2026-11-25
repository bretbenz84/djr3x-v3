"""The front depth board's serial replies are read whole or not at all (owner, November 24).

The firmware's own reply reader (tof_matrix_uart.h) is fed byte streams: a clean 8x8 frame, noise
first (a floating line), a reply cut short, a failure reply, a reply for another command, and
an impossible length. Also the request bytes it sends. No serial port, no robot."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''#include <cstdio>
#include <cstdint>
#include <iostream>
#include <string>
#include "tof_matrix_uart.h"

// stdin: "req CMD ARG..." prints the request bytes; "feed CMD CAP BYTE..." prints each
// result word as it happens, then "end answered=N stored=L first=X last=Y".
int main() {
  std::string op;
  while (std::cin >> op) {
    if (op == "req") {
      unsigned cmd, n; std::cin >> cmd >> n;
      uint8_t args[8], out[16];
      for (unsigned i = 0; i < n; i++) { unsigned v; std::cin >> v; args[i] = (uint8_t)v; }
      const int len = mx_uart_request(out, (uint8_t)cmd, args, (uint8_t)n);
      for (int i = 0; i < len; i++) std::printf("%u ", out[i]);
      std::printf("\n");
      continue;
    }
    unsigned cmd, cap, n; std::cin >> cmd >> cap >> n;
    uint8_t buf[200] = {0};
    MxUartReply r; r.start((uint8_t)cmd, buf, (uint16_t)cap);
    static const char* WORDS[] = {"more", "ok", "failed", "bad"};
    for (unsigned i = 0; i < n; i++) {
      unsigned v; std::cin >> v;
      const MxUartResult res = r.feed((uint8_t)v);
      if (res != MX_UART_MORE) std::printf("%s ", WORDS[res]);
    }
    const unsigned len = r.length();
    std::printf("end answered=%d stored=%u first=%u last=%u\n", r.answered ? 1 : 0, len,
                len ? buf[0] : 0, len ? buf[len - 1] : 0);
  }
}
'''


def frame(payload, status=0x53, cmd=2):
    return [status, cmd, len(payload) & 0xFF, len(payload) >> 8, *payload]


class MatrixUartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        source = Path(cls.tmp.name)/'uart.cpp'
        cls.binary = Path(cls.tmp.name)/'uart'
        source.write_text(HARNESS)
        subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT/'djr3x_motion'),
                        str(source), '-o', str(cls.binary)], check=True)

    def feed(self, data, cmd=2, cap=128):
        text = f'feed {cmd} {cap} {len(data)} ' + ' '.join(map(str, data)) + '\n'
        out = subprocess.run([str(self.binary)], input=text, text=True, capture_output=True, check=True)
        return out.stdout.split()

    GRID = [i % 251 for i in range(128)]   # 64 little-endian millimetre values

    def test_a_whole_frame_is_stored(self):
        words = self.feed(frame(self.GRID))
        self.assertEqual(words, ['ok', 'end', 'answered=1', 'stored=128', 'first=0', f'last={127 % 251}'])

    def test_noise_before_the_reply_is_skipped(self):
        words = self.feed([0, 255, 17, 200, 85, *frame(self.GRID)])   # a floating line, a stray 0x55
        self.assertEqual(words[0], 'ok')
        self.assertIn('stored=128', words)

    def test_a_reply_cut_short_is_not_a_frame(self):
        words = self.feed(frame(self.GRID)[:-10])
        self.assertNotIn('ok', words)
        self.assertIn('answered=1', words)        # still proof a board is on the line

    def test_failure_status_and_wrong_command_are_not_frames(self):
        self.assertEqual(self.feed(frame([7], status=0x63))[0], 'failed')
        self.assertEqual(self.feed(frame(self.GRID, cmd=1))[0], 'bad')

    def test_an_impossible_length_is_dropped(self):
        self.assertEqual(self.feed([0x53, 2, 0xE9, 0x03, 1, 2, 3])[0], 'bad')   # 1001 bytes

    def test_a_short_power_on_frame_is_read_but_shorter(self):
        # A board that reset answers in its power-on mode (4x4): whole, but not 128 bytes,
        # which the driver treats as a failure and sets the board up again.
        words = self.feed(frame(list(range(32))))
        self.assertEqual(words[:4], ['ok', 'end', 'answered=1', 'stored=32'])

    def test_a_longer_reply_than_room_is_drained(self):
        words = self.feed(frame(list(range(40)), cmd=1), cmd=1, cap=8)
        self.assertEqual(words[:4], ['ok', 'end', 'answered=1', 'stored=8'])

    def test_silence_is_no_answer(self):
        self.assertEqual(self.feed([]), ['end', 'answered=0', 'stored=0', 'first=0', 'last=0'])

    def test_requests_match_the_vendor_packets(self):
        out = subprocess.run([str(self.binary)], input='req 2 0\nreq 1 4 0 0 0 8\n', text=True,
                             capture_output=True, check=True).stdout.splitlines()
        self.assertEqual(out[0].split(), ['85', '0', '1', '2'])
        self.assertEqual(out[1].split(), ['85', '0', '5', '1', '0', '0', '0', '8'])


if __name__ == '__main__':
    unittest.main()
