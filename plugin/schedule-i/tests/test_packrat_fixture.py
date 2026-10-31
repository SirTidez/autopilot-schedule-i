"""Exercise acceptance assertions with a fake UI; never infer success from sent keys alone."""

import unittest
from types import SimpleNamespace

from autopilot.schedule_i_acceptance import PackRatToggleHarness


class FakeUi:
    def __init__(self):
        self.opened = False
        self.unknown = False
        self.presses = []
        self.link = SimpleNamespace(hello={"capabilities": ["ui"]})

    def request(self, op, args):
        node = {"id": "runtime:1:9", "name": "PackRatPanel", "active": self.opened, "ancestor_group_alpha": 1}
        if "objects" in args:
            return {"elements": [] if self.unknown else [node], "unknown_ids": [node["id"]] if self.unknown else [],
                    "exact_tracked_query": True}
        return {"elements": [node] if self.opened else []}

    def press(self, key):
        self.presses.append(key)


class PackRatFixtureTests(unittest.TestCase):
    def test_goal_requires_observed_open_and_exact_tracked_close(self):
        game = FakeUi()
        harness = PackRatToggleHarness("PackRatPanel", wait_for_cancel=False)
        harness.setup(game)
        before = harness.observe(game)
        open_option = next(o for o in harness.options(before) if o.name == "open_packrat")
        gen = open_option.run(game, before)
        next(gen)
        self.assertEqual(game.presses, ["B"])
        self.assertFalse(harness.observe(game)["open_observed"], "A sent key cannot prove the panel opened")
        game.opened = True
        opened = harness.observe(game)
        self.assertTrue(opened["open_observed"])
        close_option = next(o for o in harness.options(opened) if o.name == "close_packrat")
        close_gen = close_option.run(game, opened)
        next(close_gen)
        self.assertFalse(harness.observe(game)["close_observed"])
        game.opened = False
        closed = harness.observe(game)
        self.assertEqual(harness.status(closed).state, "done")
        gen.close()
        close_gen.close()

    def test_evicted_or_unknown_panel_identity_cannot_prove_close(self):
        game = FakeUi()
        harness = PackRatToggleHarness("PackRatPanel")
        harness.setup(game)
        harness.open_sent = game.opened = True
        harness.observe(game)
        harness.close_sent = game.unknown = True
        with self.assertRaisesRegex(RuntimeError, "identity"):
            harness.observe(game)
        self.assertFalse(harness.close_observed)

    def test_fixture_rejects_an_initially_open_panel(self):
        game = FakeUi()
        game.opened = True
        with self.assertRaisesRegex(RuntimeError, "closed"):
            PackRatToggleHarness("PackRatPanel").setup(game)


if __name__ == "__main__":
    unittest.main()
