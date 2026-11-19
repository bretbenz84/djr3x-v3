"""Rex's Star Tours lines (owner, November 19): code rests each after it is heard, across restarts, and
offers only a rested one in the reply and beat context. Temporary databases and a fake clock: this proves
the rationing, not whether Claude picks the right moments or how the line sounds."""
from r3x import signature_lines
from r3x.memory import Memory
from tests.test_memory_core import MemoryFixture


class SignatureLineTests(MemoryFixture):
    def test_a_heard_line_rests_across_restarts_and_comes_back(self):
        self.state.wall_clock = lambda: 1000.
        self.begin('The dishwasher flooded the kitchen.')
        self.assertEqual(self.state.context()['signature_lines_ready'], ['bad_feeling', 'meant_to'])
        with self.store.transaction() as conn:
            self.assertEqual(signature_lines.heard(conn, "Oh no. I have a very bad feeling about this!", 1000.),
                             ('bad_feeling',))
            self.assertEqual(signature_lines.heard(conn, 'A flooded kitchen. Brutal.', 1000.), ())
        self.begin('It is everywhere.')
        self.assertEqual(self.state.context()['signature_lines_ready'], ['meant_to'])
        self.store.close()
        self.store = Memory(self.path); self.store.start()   # A restart keeps the rest.
        self.assertEqual(signature_lines.ready(self.store, 1000. + signature_lines.REST_SECONDS - 1), ('meant_to',))
        self.assertEqual(signature_lines.ready(self.store, 1000. + signature_lines.REST_SECONDS),
                         ('bad_feeling', 'meant_to'))

    def test_sentry_mode_offers_none(self):
        self.begin('Watch the house.')
        self.state.sentry.phase = 'armed'
        self.assertNotIn('signature_lines_ready', self.state.context())
