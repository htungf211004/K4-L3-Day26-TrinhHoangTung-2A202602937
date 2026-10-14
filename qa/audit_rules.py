"""Contract audit of owned entry points using public local tests/fixtures only."""
import copy
import functools
import importlib
import inspect
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.gateway import Gateway, Decision
from qa import test_gateway, test_guardrails
from qa.qa_final import audit_imports, audit_protected, audit_bundle

prosecutor = importlib.import_module("eval.prosecute")
fixtures = prosecutor.load_fixtures()
phase = None
events = []
latency = {"decide": [], "prosecute": []}


class EntryPointIO(BaseException):
    pass


def audit_event(event, args):
    if phase and (event == "open" or event in {"os.listdir", "os.scandir", "os.system", "os.fork", "subprocess.Popen"}
                  or event.startswith(("socket.", "os.exec", "os.spawn"))):
        events.append({"entry": phase, "event": event})
        raise EntryPointIO(f"{phase} requested {event}")


sys.addaudithook(audit_event)


def checked(name, fn):
    assert not inspect.iscoroutinefunction(fn)
    @functools.wraps(fn)
    def invoke(*args, **kwargs):
        global phase
        assert phase is None
        phase = name
        start = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - start
            phase = None
            latency[name].append(elapsed)
        assert elapsed < (0.25 if name == "decide" else 5), (name, elapsed)
        if name == "decide":
            assert isinstance(result, Decision)
            json.dumps(result.to_dict())
        else:
            assert isinstance(result, dict) and result.get("v") == 1
            claims = result["claims"]
            assert isinstance(claims, list) and len(claims) <= 4
            families = [prosecutor.family_of(c["cls"]) for c in claims]
            assert len(families) == len(set(families))
            for claim in claims:
                assert not prosecutor._schema_errors(claim), claim
            json.dumps(result)
        return result
    return invoke


original_decide = Gateway.decide
Gateway.decide = checked("decide", original_decide)
suite = unittest.TestSuite([
    unittest.defaultTestLoader.loadTestsFromModule(test_gateway),
    unittest.defaultTestLoader.loadTestsFromModule(test_guardrails),
])
tests = unittest.TextTestRunner(verbosity=0).run(suite)
Gateway.decide = original_decide
assert tests.wasSuccessful()
checked_prosecute = checked("prosecute", prosecutor.prosecute)
report = prosecutor.score_prosecutor(checked_prosecute, fixtures)
assert report["n_errors"] == report["n_timeouts"] == report["rejected"] == 0
assert not events

# Robustness check: an opponent answer can be JSON without having a numeric day.
# This is synthetic local input, never a real opponent trace.
fx = next(f for f in fixtures if f["card"].get("ask", {}).get("type") == "which_day_covers"
          and "wrong_answer" in f.get("label", {}).get("present_classes", {}))
changed = copy.deepcopy(fx["answer"])
changed["course_day"] = "unknown"
robustness = None
try:
    prosecutor.prosecute(copy.deepcopy(fx["trace"]), changed, copy.deepcopy(fx["card"]))
except Exception as exc:
    robustness = {"synthetic_base_fixture": fx["fixture_id"], "changed_field": "answer.course_day='unknown'",
                  "exception": type(exc).__name__, "detail": str(exc)}

audit_imports()
audit_protected()
audit_bundle()
summary = {
    "date": "2026-10-14", "local_regression_tests": tests.testsRun,
    "runtime_samples": {k: {"calls": len(v), "max_ms": max(v) * 1000} for k, v in latency.items()},
    "runtime_io_events": events,
    "prosecution_public_fixtures": {k: v for k, v in report.items() if k not in {"per_class", "errors", "slow"}},
    "prosecutor_robustness_finding": robustness,
}
(ROOT / "qa/RULES_AUDIT.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(summary, indent=2, ensure_ascii=False))
