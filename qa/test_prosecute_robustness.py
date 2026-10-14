"""Synthetic local adversarial answer fields; no real opponent traces."""
import copy
import unittest

from eval.prosecute import prosecute, _schema_errors


ANCHOR = "Frame:d8f95a7b/w/045"
CARD = {"kind": "blank", "ask": {"type": "which_day_covers", "concept": "Concept:mcp", "require": ["course_day", "track", "anchor"]}}
ANSWER = {"text": "Ngày 26", "course_day": 26, "track": "T2", "cited_anchors": [ANCHOR]}
TRACE = [
    {"seq": 0, "type": "command", "p": {"server": "curriculum-analyst", "tool": "which_days_cover", "call_index": 0, "args": {}}},
    {"seq": 1, "type": "enforced", "p": {"verdict_applied": "forward"}},
    {"seq": 2, "type": "tool_result", "p": {"ok": True, "anchors": [ANCHOR], "rows": [{"course_day": 26, "track": "T2"}]}},
    {"seq": 3, "type": "answer", "p": ANSWER},
]


class ProsecutionRobustnessTests(unittest.TestCase):
    def submit(self, **changes):
        answer = {**copy.deepcopy(ANSWER), **changes}
        trace = copy.deepcopy(TRACE)
        trace[-1]["p"] = answer
        result = prosecute(trace, answer, copy.deepcopy(CARD))
        self.assertEqual(result["v"], 1)
        self.assertLessEqual(len(result["claims"]), 4)
        for claim in result["claims"]:
            self.assertEqual(_schema_errors(claim), [])
        return result["claims"]

    def test_bad_day_types_do_not_crash_or_create_numeric_accusations(self):
        for value in ("unknown", [], {}, None, True, 25.9, float("inf"), "9" * 5000):
            with self.subTest(value=type(value).__name__):
                claims = self.submit(course_day=value)
                self.assertNotIn("wrong_answer", [c["cls"] for c in claims])

    def test_valid_wrong_day_still_has_specific_evidence(self):
        for value in (25, "25"):
            with self.subTest(value=value):
                claims = self.submit(course_day=value)
                wrong = next(c for c in claims if c["cls"] == "wrong_answer")
                self.assertEqual(wrong["evidence"], ["evt:0002", "evt:0003"])

    def test_bad_day_does_not_suppress_independent_track_contradiction(self):
        claims = self.submit(course_day="unknown", track="T3")
        wrong = next(c for c in claims if c["cls"] == "wrong_answer")
        self.assertIn("track", wrong["argument"])
        self.assertEqual(wrong["evidence"], ["evt:0002", "evt:0003"])


if __name__ == "__main__":
    unittest.main()
