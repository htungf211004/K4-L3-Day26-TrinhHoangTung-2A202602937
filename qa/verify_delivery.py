"""Smoke-test imports from the sealed ZIP, then inspect the two-file delivery."""
import importlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TEAM = "TrinhHoangTung-2A202602937"
BUNDLE = ROOT / "submissions" / f"{TEAM}.bundle"
DELIVERY = ROOT / f"K4-DAY26-{TEAM}"
sys.path[:0] = [str(BUNDLE), str(ROOT)]

student = importlib.import_module("agent")
for module in ("agent", "agent.gateway", "agent.guardrails", "agent.strategy", "agent.telemetry", "eval", "eval.prosecute"):
    loaded = importlib.import_module(module)
    assert str(BUNDLE) in loaded.__file__, (module, loaded.__file__)

ctx = student.RecordingGatewayContext("learner:sv-0417", "agent:vlearn-tutor", frozenset({"wiki.read"}), 100, 1, 0)
cmd = student.Command("cmd:delivery", "discover", "DISCOVER registry.list_servers fields=name", "registry", "list_servers", {}, ("name",), {}, None, 0)
decision = student.Gateway(ctx).decide(cmd)
assert decision.verdict == "forward"
json.dumps(decision.to_dict())
anchor = "Frame:d8f95a7b/w/045"
guarded = student.prepare_answer({"text": "MCP uses narrow queries.", "cited_anchors": [anchor]}, {anchor: {"body": "MCP uses narrow queries."}})
assert not guarded.abstained
assert importlib.import_module("eval.prosecute").prosecute([], {}, {}) == {"v": 1, "claims": []}
with zipfile.ZipFile(BUNDLE) as z:
    assert z.read("agent/prompt.md")
    assert json.loads(z.read("deck/deck.json"))["team"] == TEAM
    assert len(json.loads(z.read("deck/lineup.json"))["order"]) == 10
assert {p.name for p in DELIVERY.iterdir()} == {"TEAMMATES.md", f"{TEAM}.bundle"}
assert (DELIVERY / f"{TEAM}.bundle").read_bytes() == BUNDLE.read_bytes()
team_info = (DELIVERY / "TEAMMATES.md").read_text(encoding="utf-8")
assert "Trịnh Hoàng Tùng" in team_info and "2A202602937" in team_info and TEAM in team_info
print("SEALED ZIP IMPORTS + GATEWAY/GUARDRAIL/PROSECUTOR SMOKE: PASS")
print("DELIVERY: PASS (exactly bundle + TEAMMATES.md; bundle bytes identical)")
