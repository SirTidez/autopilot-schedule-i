"""Reviewed local acceptance harness for an operator-identified Pack Rat panel in a disposable save.

Uses the real upstream Harness/Option/Step loop and typed UI observations. It never grants items,
loads/saves a world, clicks arbitrary widgets or contacts a provider. Fixture preparation is separate.
"""

import time

from .kit.harness import Harness, Option, Status, Step


class PackRatToggleHarness(Harness):
    goal = "Observe Pack Rat opening and closing, then wait for an external cancellation test"
    tempo, tick_hz, stall_seconds = 0, 8, 20

    def __init__(self, panel_name, *, toggle_key="B", wait_for_cancel=True):
        if not isinstance(panel_name, str) or not 1 <= len(panel_name) <= 128:
            raise ValueError("Supply the exact panel name observed in the current game")
        if toggle_key not in ("B",):
            raise ValueError("This reviewed fixture only permits Pack Rat's verified default B toggle")
        self.panel_name, self.toggle_key = panel_name, toggle_key
        self.wait_for_cancel = wait_for_cancel
        self.open_sent = self.close_sent = self.open_observed = self.close_observed = False
        self.target_ids, self.last = [], {}
        self.started = None
        self.plan = [Step("open_packrat", "Observe the identified Pack Rat panel open", lambda o: o["open_observed"],
                          ["open_packrat", "wait_animation"]),
                     Step("close_packrat", "Observe the same tracked panel close", lambda o: o["close_observed"],
                          ["close_packrat", "wait_animation"])]
        if wait_for_cancel:
            self.plan.append(Step("cancel_owned_input", "Operator cancels a held harmless modifier input",
                                  lambda _: False, ["hold_for_cancel", "wait_animation"]))

    @staticmethod
    def shown(element):
        return (element.get("active") is True and not element.get("alpha_chain_truncated", False)
                and element.get("ancestor_group_alpha", 1) > 0.01)

    def read_panel(self, game):
        if self.target_ids:
            result = game.request("ui", {"objects": list(self.target_ids)})
            if (result.get("exact_tracked_query") is not True or result.get("unknown_ids")
                    or len(result.get("elements", [])) != len(self.target_ids)):
                raise RuntimeError("Panel identity is unavailable; cannot infer a successful close")
            elements = result["elements"]
            return any(self.shown(e) for e in elements), elements
        result = game.request("ui", {"name": self.panel_name, "limit": 256})
        elements = [e for e in result.get("elements", []) if e.get("name") == self.panel_name]
        shown = [e for e in elements if self.shown(e)]
        if shown:
            self.target_ids = [e["id"] for e in shown]
        return bool(shown), elements

    def setup(self, game):
        if "ui" not in (game.link.hello or {}).get("capabilities", []):
            raise RuntimeError("This bridge does not advertise typed UI observations")
        opened, _ = self.read_panel(game)
        if opened:
            raise RuntimeError("Fixture must start with the operator-identified Pack Rat panel closed")
        self.started = time.monotonic()

    def observe(self, game):
        opened, elements = self.read_panel(game)
        if self.open_sent and opened:
            self.open_observed = True
        if self.open_observed and self.close_sent and not opened:
            self.close_observed = True
        self.last = {"panel_name": self.panel_name, "panel_open": opened,
                     "open_observed": self.open_observed, "close_observed": self.close_observed,
                     "tracked_panel_ids": list(self.target_ids), "ui_evidence": elements,
                     "waiting_for_cancel": self.close_observed and self.wait_for_cancel}
        return dict(self.last)

    def status(self, obs):
        if obs["close_observed"] and not self.wait_for_cancel:
            return Status("done", "Observed the same panel open and close", dict(obs))
        if self.started is not None and time.monotonic() - self.started > 18 and not obs["close_observed"]:
            return Status("failed", "Pack Rat open/close was not observed within the fixture window", dict(obs))
        return Status("running", "Waiting for observed UI changes or deliberate cancellation", dict(obs))

    def options(self, obs):
        def toggle(want_open):
            def execute(game, _):
                if want_open:
                    self.open_sent = True
                else:
                    self.close_sent = True
                game.press(self.toggle_key)
                end = time.monotonic() + 3
                while time.monotonic() < end:
                    yield
                    if self.last.get("panel_open") is want_open:
                        return True
                return False
            return execute
        def wait(game, _):
            yield
            yield
        def hold(game, _):
            # A modifier alone is deliberately harmless: no movement, interaction, save or OS chord.
            game.hold("LeftShift")
            try:
                while True:
                    yield
            finally:
                game.release("LeftShift")
        choices = [Option("wait_animation", "Wait for the next observed UI state", wait, max_seconds=1)]
        if not obs["open_observed"]:
            choices.append(Option("open_packrat", "Press the configured B key and observe Pack Rat opening",
                                  toggle(True), effect=lambda before, after: after["panel_open"], max_seconds=4))
        elif not obs["close_observed"]:
            choices.append(Option("close_packrat", "Press B and observe the same tracked panel closing",
                                  toggle(False), effect=lambda before, after: not after["panel_open"], max_seconds=4))
        elif self.wait_for_cancel:
            choices.append(Option("hold_for_cancel", "Hold only LeftShift until the operator cancels",
                                  hold, max_seconds=15))
        return choices

    @staticmethod
    def choose(state, criteria):
        situation = state["situation"]
        preferred = ("open_packrat" if not situation["open_observed"] else
                     "close_packrat" if not situation["close_observed"] else "hold_for_cancel")
        return preferred if preferred in criteria else "wait_animation"
