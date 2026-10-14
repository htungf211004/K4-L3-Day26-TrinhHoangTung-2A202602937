"""Decision-gate regressions; stdlib only and no changes to protected files.

Run: .venv/bin/python -m unittest qa.test_gateway -v
"""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import json
import unittest
from unittest.mock import patch

from agent.gateway import Command, Gateway
from agent.telemetry import RecordingGatewayContext
from kit.mcp.a2a import issue_card, mint_delegation
from kit.mcp.hardmode import HardMode
from kit.mcp.servers import handle
from kit.mcp.specs import cost
from kit.world.fixture import build_fixture_world
from kit.world.loader import World


ACT = "learner:sv-0417"
SUB = "agent:vlearn-tutor"
FRAME = "Frame:3f2a9c11/w/041"
LEARNER = "Learner:sv-0417"


def context(**changes):
    values = dict(act=ACT, sub=SUB, scopes=frozenset({"wiki.read"}),
                  credits=100, round=1, call_index=0)
    values.update(changes)
    return RecordingGatewayContext(**values)


def command(server="slides", tool="query", **changes):
    values = dict(cmd_id="cmd:0000", kind="mcp", raw="test", server=server,
                  tool=tool, args={"q": "mcp"}, fields=(), headers={},
                  lease_id=None, call_index=0)
    values.update(changes)
    return Command(**values)


class GatewayTests(unittest.TestCase):
    def assertDenied(self, decision, reason=None):
        self.assertEqual(decision.verdict, "deny")
        self.assertIsNone(decision.call)
        self.assertTrue(decision.reason)
        if reason:
            self.assertIn(reason, decision.reason)

    def write_setup(self):
        ctx = context(scopes=frozenset({"wiki.read", "wiki.write:progress"}))
        gate = Gateway(ctx)
        provenance = command("registry", "provenance", args={"anchor": LEARNER})
        gate.note_result(provenance, {"ok": True, "etag": "etag:one", "rows": []})
        write = command("progress", "record_mastery", cmd_id="cmd:write", call_index=1,
                        args={"anchor": LEARNER, "learner": ACT, "concept": "Concept:mcp"},
                        headers={"If-Match": "etag:one", "Idempotency-Key": "write-1"})
        return ctx, gate, write

    def delegation_setup(self, server="curriculum-analyst", tool="which_days_cover"):
        ctx = context()
        gate = Gateway(ctx)
        self.assertTrue(gate.note_card(server, issue_card(server, "local:peer", "1", (tool,))))
        token = mint_delegation(ACT, "a2a:" + server, ttl=3, sub=SUB, call_index=0)
        cmd = command(server, tool, kind="a2a", args={"concept": "Concept:mcp", "delegation": token},
                      headers={"aud": server})
        return ctx, gate, cmd

    def test_clean_read_is_allowed_and_original_command_is_untouched(self):
        ctx = context()
        cmd = command(headers={"Mcp-Replica": "c"}, args={"q": "mcp"})
        original = cmd.to_dict()
        decision = Gateway(ctx).decide(cmd)
        self.assertEqual(decision.verdict, "rewrite")
        self.assertEqual(decision.call.headers["mcp-replica"], "w")
        self.assertEqual(decision.call.args["limit"], 1)
        self.assertEqual(cmd.to_dict(), original)

    def test_unmodified_valid_command_returns_forward(self):
        cmd = command("glossary", "define", args={"term": "lease"}, fields=("definition",))
        decision = Gateway(context()).decide(cmd)
        self.assertEqual(decision.verdict, "forward")

    def test_catalog_traps_are_narrowed_at_normal_budget(self):
        for server, tool, field in (("registry", "list_servers", "name"),
                                    ("glossary", "list_terms", "term")):
            for mask in ((), ("*",)):
                with self.subTest(tool=tool, mask=mask):
                    decision = Gateway(context()).decide(command(server, tool, args={}, fields=mask))
                    self.assertEqual(decision.verdict, "rewrite")
                    self.assertEqual(decision.call.fields, (field,))
                    self.assertEqual(cost(server, tool, decision.call.fields), 2)

    def test_deprecated_search_rewrites_tool_and_snippet_mask(self):
        decision = Gateway(context()).decide(command(tool="search", fields=("snippet", "title")))
        self.assertEqual(decision.verdict, "rewrite")
        self.assertEqual(decision.call.tool, "query")
        self.assertEqual(decision.call.fields, ("body", "title"))

    def test_body_routes_and_conflicting_case_variants_are_denied(self):
        cases = [command(args={"q": "mcp", key: "c"}) for key in ("route", "_route", "replica")]
        cases.append(command(headers={"Mcp-Replica": "w", "mcp-replica": "c"}))
        for cmd in cases:
            with self.subTest(cmd=cmd):
                self.assertDenied(Gateway(context()).decide(cmd))

    def test_pinned_replica_never_changes_a_positional_anchor(self):
        gate = Gateway(context(leases=("live",)))
        gate.note_replica("3f2a9c11", "c", drifting=True)
        self.assertDenied(gate.decide(command(tool="get_frame", args={"anchor": FRAME}, lease_id="live")), "anchor")
        query = gate.decide(command(args={"q": "mcp", "path_id": "3f2a9c11"}))
        self.assertEqual(query.call.headers["mcp-replica"], "c")

    def test_explicit_canonical_anchor_keeps_its_replica(self):
        cmd = command(tool="get_frame", args={"anchor": FRAME.replace("/w/", "/c/")}, lease_id="live")
        self.assertEqual(Gateway(context(leases=("live",))).decide(cmd).call.headers["mcp-replica"], "c")

    def test_spoofed_path_id_cannot_bypass_the_replica_pin(self):
        gate = Gateway(context(leases=("live",)))
        gate.note_replica("3f2a9c11", "c")
        cmd = command(tool="get_frame", args={"anchor": FRAME, "path_id": "other-path"}, lease_id="live")
        self.assertDenied(gate.decide(cmd), "path_id")

    def test_observed_replica_conflict_requires_resolution_before_frame_read(self):
        gate = Gateway(context(leases=("live",)))
        for rev in ("w", "c"):
            gate.note_result(command("registry", "provenance", args={"anchor": FRAME.replace("/w/", "/" + rev + "/")}),
                             {"ok": True, "etag": "etag:" + rev})
        read = command(tool="get_frame", args={"anchor": FRAME}, lease_id="live")
        self.assertDenied(gate.decide(read), "resolve freshness")
        gate.note_replica("3f2a9c11", "w", drifting=True)
        self.assertNotEqual(gate.decide(read).verdict, "deny")

    def test_different_frame_indices_do_not_falsely_mark_a_path_as_drifting(self):
        gate = Gateway(context(leases=("live",)))
        for anchor, etag in ((FRAME, "one"), (FRAME.replace("/w/041", "/c/042"), "two")):
            gate.note_result(command("registry", "provenance", args={"anchor": anchor}), {"ok": True, "etag": etag})
        self.assertNotEqual(gate.decide(command(tool="get_frame", args={"anchor": FRAME}, lease_id="live")).verdict, "deny")

    def test_missing_or_forged_lease_is_denied_without_spending(self):
        for lease in (None, "forged"):
            gate = Gateway(context(leases=("live",)))
            self.assertDenied(gate.decide(command(tool="get_frame", args={"anchor": FRAME}, lease_id=lease)), "lease")
            self.assertEqual(gate._pacer.credits_spent, 0)

    def test_lease_window_boundaries_and_round_changes(self):
        for age, allowed in ((0, False), (1, True), (3, True), (4, False)):
            ctx = context(leases=("live",))
            gate = Gateway(ctx)
            gate.note_result(command(), {"ok": True, "lease_id": "live"})
            result = gate.decide(command(tool="get_frame", args={"anchor": FRAME}, lease_id="live", call_index=age))
            with self.subTest(age=age):
                self.assertEqual(result.verdict != "deny", allowed)
        ctx.round = 2
        self.assertDenied(gate.decide(command(tool="get_frame", args={"anchor": FRAME}, lease_id="live", call_index=1)))

    def test_privileged_sub_does_not_authorize_a_cross_learner_write(self):
        ctx, gate, write = self.write_setup()
        self.assertDenied(gate.decide(replace(write, args={**write.args, "learner": "learner:sv-0392"})), "learner")

    def test_private_learner_anchor_cannot_bypass_target_check(self):
        ctx, gate, write = self.write_setup()
        self.assertDenied(gate.decide(replace(write, args={**write.args, "anchor": "Learner:sv-0392"})), "learner")
        self.assertDenied(gate.decide(command("registry", "provenance", args={"anchor": "Learner:sv-0392"})), "learner")

    def test_learner_anchor_and_act_forms_identify_the_same_owner(self):
        ctx, gate, write = self.write_setup()
        self.assertNotEqual(gate.decide(replace(write, args={**write.args, "learner": LEARNER})).verdict, "deny")

    def test_read_and_write_scopes_are_enforced(self):
        self.assertDenied(Gateway(context(scopes=frozenset())).decide(command()), "scope")
        ctx, gate, write = self.write_setup()
        ctx.scopes = frozenset({"wiki.read", "wiki.write:content"})
        self.assertDenied(gate.decide(write), "scope")

    def test_write_requires_observed_etag_and_an_idempotency_key(self):
        ctx, gate, write = self.write_setup()
        cases = [replace(write, headers={}), replace(write, headers={"if-match": "etag:one"}),
                 replace(write, headers={"if-match": "forged", "idempotency-key": "new"})]
        for cmd in cases:
            with self.subTest(headers=cmd.headers):
                self.assertDenied(gate.decide(cmd))
        self.assertDenied(Gateway(ctx).decide(write), "provenance")

    def test_successful_write_cannot_be_replayed_with_a_new_key(self):
        ctx, gate, write = self.write_setup()
        first = gate.decide(write)
        self.assertNotEqual(first.verdict, "deny")
        gate.note_result(first.call, {"ok": True})
        gate.note_result(command("registry", "provenance", args={"anchor": LEARNER}, call_index=2),
                         {"ok": True, "etag": "etag:one"})
        repeat = replace(write, call_index=3, headers={"if-match": "etag:one", "idempotency-key": "new-key"})
        self.assertDenied(gate.decide(repeat), "already committed")

    def test_conflict_requires_reread_and_new_key_but_ambiguous_write_is_not_retried(self):
        for failure, allowed in (("conflict", True), ("unavailable", False)):
            ctx, gate, write = self.write_setup()
            first = gate.decide(write)
            gate.note_result(first.call, {"ok": False, "error": {"code": failure}})
            self.assertDenied(gate.decide(replace(write, call_index=2)), "provenance")
            gate.note_result(command("registry", "provenance", args={"anchor": LEARNER}, call_index=2),
                             {"ok": True, "etag": "etag:two"})
            retry = replace(write, call_index=3, headers={"if-match": "etag:two", "idempotency-key": "key-2"})
            with self.subTest(failure=failure):
                self.assertEqual(gate.decide(retry).verdict != "deny", allowed)

    def test_stale_provenance_is_not_a_write_precondition(self):
        ctx, gate, write = self.write_setup()
        self.assertDenied(gate.decide(replace(write, call_index=4)), "fresh")
        ctx.round = 2
        self.assertDenied(gate.decide(write), "fresh")

    def test_context_history_delivers_provenance_without_an_external_call(self):
        ctx, gate, write = self.write_setup()
        gate = Gateway(ctx)
        ctx.history = ({"round": 1, "command": command("registry", "provenance", args={"anchor": LEARNER}),
                        "outcome": {"ok": True, "etag": "etag:one"}},)
        self.assertNotEqual(gate.decide(write).verdict, "deny")
        # Re-reading old history must not resurrect the consumed precondition.
        self.assertDenied(gate.decide(replace(write, call_index=2)), "provenance")

    def test_reused_command_ids_do_not_refresh_prior_round_history(self):
        ctx, gate, write = self.write_setup()
        provenance = command("registry", "provenance", args={"anchor": LEARNER})
        gate.decide(provenance)
        ctx.history = ({"command": provenance, "outcome": {"ok": True, "etag": "etag:one"}},)
        ctx.round = 2
        gate.decide(command())  # Reuses cmd:0000, call_index=0 in the next round.
        self.assertDenied(gate.decide(write), "fresh")

    def test_unsigned_verified_flag_does_not_admit_a_peer(self):
        gate = Gateway(context())
        self.assertFalse(gate.note_card("curriculum-analyst", {"verified": True, "skills": ["which_days_cover"]}))

    def test_legitimate_signed_a2a_call_is_allowed(self):
        ctx, gate, cmd = self.delegation_setup()
        decision = gate.decide(cmd)
        self.assertNotEqual(decision.verdict, "deny")
        json.dumps(decision.to_dict())  # The authoritative trace can serialize it.

    def test_delegation_replay_forgery_wrong_act_and_wrong_audience_are_denied(self):
        for failure in ("replay", "signature", "act", "aud", "future", "expired"):
            ctx, gate, cmd = self.delegation_setup()
            token = cmd.args["delegation"]
            if failure == "replay":
                self.assertNotEqual(gate.decide(cmd).verdict, "deny")
            elif failure == "signature":
                token = replace(token, signature="0" * 64)
            elif failure == "act":
                token = mint_delegation("learner:sv-0392", "a2a:curriculum-analyst", 3, sub=SUB)
            elif failure == "aud":
                token = mint_delegation(ACT, "a2a:roster", 3, sub=SUB)
            elif failure == "future":
                token = mint_delegation(ACT, "a2a:curriculum-analyst", 3, sub=SUB, call_index=1)
            else:
                cmd = replace(cmd, call_index=4)
            with self.subTest(failure=failure):
                self.assertDenied(gate.decide(replace(cmd, args={**cmd.args, "delegation": token})))

    def test_forged_card_and_undeclared_skill_are_denied(self):
        for skills in (("other_skill",), ("which_days_cover",)):
            ctx, gate, cmd = self.delegation_setup()
            card = issue_card("curriculum-analyst", "local:peer", "1", skills)
            if skills == ("which_days_cover",):
                card = replace(card, signature="0" * 64)
            self.assertDenied(gate.decide(replace(cmd, args={**cmd.args, "agent_card": card})))

    def test_rate_window_is_per_tool_and_expires_after_three_rounds(self):
        ctx, gate, cmd = self.delegation_setup("citation-checker", "verify_source")
        for rnd in (1, 2, 3, 4):
            ctx.round = rnd
            token = mint_delegation(ACT, "a2a:citation-checker", 3, sub=SUB, nonce=rnd)
            decision = gate.decide(replace(cmd, args={"delegation": token}))
            self.assertEqual(decision.verdict != "deny", rnd != 3)

    def test_low_budget_narrows_a_mask_and_zero_budget_denies(self):
        ctx = context(credits=2, round=10, leases=("live",))
        cmd = command(tool="get_frame", args={"anchor": FRAME}, lease_id="live", fields=("*",))
        decision = Gateway(ctx).decide(cmd)
        self.assertEqual(decision.verdict, "rewrite")
        self.assertEqual(decision.call.fields, ("title",))
        self.assertDenied(Gateway(context(credits=0)).decide(command()), "credits")

    def test_whole_duel_budget_does_not_reset_when_context_credits_are_stale(self):
        ctx = context()
        gate = Gateway(ctx)
        total = 0
        for rnd in range(1, 11):
            ctx.round = rnd
            for index in range(6):
                result = gate.decide(command("glossary", "define", args={"term": "mcp"}, fields=("definition",),
                                             call_index=index, cmd_id=f"cmd:{rnd}:{index}"))
                if result.call:
                    total += cost(result.call.server, result.call.tool, result.call.fields)
        self.assertEqual(total, 60)
        # More traffic in late rounds still cannot exceed the original pool.
        for rnd in range(1, 11):
            ctx.round = rnd
            for index in range(20):
                gate.decide(command("glossary", "define", args={"term": "mcp"}, fields=("definition",),
                                    call_index=index, cmd_id=f"more:{rnd}:{index}"))
        self.assertLessEqual(gate._pacer.credits_spent, 100)

    def test_query_limits_cannot_spend_unbounded_row_cost(self):
        decision = Gateway(context()).decide(command(fields=("body", "title"), args={"q": "mcp", "limit": 10000}))
        self.assertLessEqual(decision.call.args["limit"], 6)
        self.assertLessEqual(cost("slides", "query", decision.call.fields, decision.call.args["limit"]), 10)

    def test_opaque_read_failure_blocks_an_unchanged_retry(self):
        gate = Gateway(context())
        cmd = command()
        first = gate.decide(cmd)
        gate.note_result(first.call, {"ok": False, "error": {"code": "unavailable"}})
        self.assertDenied(gate.decide(replace(cmd, call_index=1)), "unchanged retry")
        revised = replace(cmd, args={"q": "delegation"}, call_index=2)
        self.assertNotEqual(gate.decide(revised).verdict, "deny")

    def test_malformed_inputs_return_valid_denials(self):
        cases = [command(fields=(1,)), command(fields=("made_up",)), command(headers={1: "bad"}),
                 command(lease_id=3), command(args={"q": "mcp", "limit": True}),
                 command(server="evil"), command(kind="a2a"),
                 command(tool="get_frame", args={"anchor": "not-an-anchor"})]
        for cmd in cases:
            with self.subTest(cmd=cmd):
                self.assertDenied(Gateway(context()).decide(cmd))

    def test_decide_needs_no_file_io_or_sleep_and_meets_deadline(self):
        timings = []
        with patch("builtins.open", side_effect=AssertionError("I/O inside decide")), \
             patch("pathlib.Path.open", side_effect=AssertionError("I/O inside decide")), \
             patch("time.sleep", side_effect=AssertionError("sleep inside decide")):
            for _ in range(100):
                gate = Gateway(context())
                start = perf_counter()
                decision = gate.decide(command())
                timings.append(perf_counter() - start)
                self.assertNotEqual(decision.verdict, "deny")
        self.assertLess(max(timings), 0.25)

    def test_real_server_query_to_leased_frame_through_the_gateway(self):
        with TemporaryDirectory() as tmp:
            build_fixture_world(Path(tmp), include_truth=False)
            world = World.load(tmp)
            ctx = context()
            gate = Gateway(ctx)
            query = gate.decide(command(args={"q": "mcp", "ns": "Frame"}))
            result = handle(world, query.call)
            self.assertTrue(result["ok"])
            self.assertTrue(result["anchors"])
            ctx.credits -= result["cost"]
            ctx.leases = (result["lease_id"],)
            gate.note_result(query.call, result)
            read = gate.decide(command(tool="get_frame", call_index=1,
                                      args={"anchor": result["anchors"][0]}, lease_id=result["lease_id"]))
            self.assertNotEqual(read.verdict, "deny")
            frame = handle(world, read.call)
            self.assertTrue(frame["ok"])
            self.assertIn("body", frame["rows"][0])

    def test_real_hardmode_provenance_write_and_duplicate_prevention(self):
        with TemporaryDirectory() as tmp:
            build_fixture_world(Path(tmp), include_truth=False)
            world = World.load(tmp)
            ctx = context(scopes=frozenset({"wiki.read", "wiki.write:progress"}))
            gate = Gateway(ctx)
            hardmode = HardMode(world=world, opaque_enabled=False, degraded_replica_enabled=False)
            hardmode.reset("gateway-write", world_id=world.manifest["world_id"])
            provenance = gate.decide(command("registry", "provenance", args={"anchor": LEARNER}))
            result = handle(world, provenance.call, hardmode=hardmode)
            self.assertTrue(result["ok"])
            gate.note_result(provenance.call, result)
            ctx.credits -= result["cost"]
            cmd = command("progress", "record_mastery", cmd_id="cmd:write", call_index=1,
                          args={"anchor": LEARNER, "learner": LEARNER, "concept": "Concept:field-mask"},
                          headers={"if-match": result["etag"], "idempotency-key": "real-write-1"})
            write = gate.decide(cmd)
            self.assertNotEqual(write.verdict, "deny")
            receipt = handle(world, write.call, hardmode=hardmode)
            self.assertTrue(receipt["ok"])
            self.assertTrue(receipt["rows"][0]["receipt_id"])
            gate.note_result(write.call, receipt)
            self.assertDenied(gate.decide(replace(cmd, cmd_id="cmd:retry", call_index=2)))


if __name__ == "__main__":
    unittest.main()
