"""agent/gateway.py — YOUR control plane. CONTRACTS.md section 4, exactly.

READ agent/README.md FIRST — it maps all five files in this directory to what
each is scored on. This file is the one CONTRACTS.md calls "the trusted
envelope's untrusted half": every single MCP / A2A / DISCOVER command your
agent's model wants to make passes through `Gateway.decide` before it is
allowed to happen.

WHY THERE IS NO `execute()` METHOD ON `GatewayContext` (read this before you
go looking for one — there isn't one, and that is not an oversight)
----------------------------------------------------------------------------
CONTRACTS.md section 4's trusted envelope, reproduced here because it is the
one diagram worth memorising:

    [ trusted ]   loop emits a raw action line
         v
    [ trusted ]   INTERCEPT + CANONICALISE -> Command        (kit/loop/agent.py)
         v
    [ UNTRUSTED ] Gateway.decide(cmd) -> Decision             <- THIS FILE
         v
    [ trusted ]   ENFORCE: honour the Decision, meter it,
                  apply the active mutation, execute the
                  ToolCall or refuse it                       (the arena)
         v
    [ trusted ]   RECORD the authoritative L1 event, then
                  RENDER the Observation                      (the arena)
         v
    [ trusted ]   the model sees the Observation

`decide()` returns a *decision*, never a *result*. You cannot reach a tool
server, a file, a socket, or a clock from in here — there is nothing to
call. Two things follow from that, and both matter more than they look:

  1. YOUR TRACE CANNOT BE FORGED. Every `command` / `decision` / `enforced`
     / `tool_call` / `tool_result` L1 event (CONTRACTS.md 5.2) is written by
     the arena, from what the arena itself actually did — never from
     anything you claimed happened. A student gateway that wanted to lie
     about having blocked an attack ("I totally denied that, trust me")
     simply has no channel to lie through: the only thing you ever hand
     back is this one small `Decision` value, and the arena is the one that
     turns it into history.
  2. NOBODY CAN ACCUSE YOU OF A CALL YOU DID NOT AUTHORISE, either. Because
     `decide()` is the ONLY door a command can walk through on its way to
     actually running, a prosecutor's `enforcement_failure` claim against
     you has exactly one thing to point at: the `Decision` you returned for
     that specific `cmd_id`. There is no ambiguity about "maybe the loop
     called the tool directly" — CONTRACTS.md 4.2 removed that path on
     purpose, and kit/loop/agent.py's own module docstring names the same
     invariant from the other side (the loop never imports this module,
     never sees a `Decision`, never executes anything itself).

The cost of that guarantee is that this file is PURE: synchronous, no I/O,
no threads, no `sleep`, 250 ms wall-clock deadline (RULES.md section 3).
Raising anything, returning something that is not a valid `Decision`, or
missing the deadline is treated by the arena as a DENIED command PLUS a 2
credit penalty PLUS an `integrity` event that hands the prosecutor a free
`enforcement_failure` — CONTRACTS.md 4.1's charging table, reproduced in
agent/README.md's own table. Getting this file to just plainly return valid
`Decision` values, every time, is worth more than getting it clever.

THE IMPLEMENTED GATE
----------------------------------------------------------------------------
The four jobs below validate routing, leases and write preconditions,
authenticate act/scope and A2A delegations, and pace the whole-duel budget.
Valid reads continue; invalid requests are denied before they cost credits.
ReplicaChoice is a heuristic, not freshness proof. Resolved choices may be
pinned through note_replica; a conflicting positional anchor is refused
rather than silently mapped to a different frame.

ONE THING WORTH INTERNALISING BEFORE YOU WRITE YOUR FIRST REAL CHECK:
`verdict="deny"` costs the CALLER (your own team) **zero credits** —
CONTRACTS.md 4.1's charging table has exactly one $0 row, and it is this
one. Refusing to make a call you cannot justify is FREE. That makes
abstention a real strategy, not a luxury you can't afford: a `deny` you can
defend beats a `forward` you can't, every time a prosecutor is watching.

Stdlib only. No network, no randomness, no wall-clock reads, no sleeping —
none of that would even survive the kernel sandbox (CONTRACTS.md 12), but
the point is this file has no reason to want any of it in the first place.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from typing import Any, Mapping, Protocol, runtime_checkable

# kit.mcp.types is a collaborator's file (workspace hard rule 2: import it,
# degrade gracefully). It is present as of this writing and is core, stable
# infrastructure (CONTRACTS.md 3.1) — but this module must still not fail to
# IMPORT if a concurrent edit ever breaks it transiently. When it is
# unavailable, `Decision.call` type-checking is skipped (not enforced), and
# `Gateway.decide` falls back to a minimal local dict-shaped stand-in so the
# rest of this file — everything that does not need a *real* ToolCall — still
# runs.
try:
    from kit.mcp.types import ToolCall
    _TOOLCALL_AVAILABLE = True
except ImportError:  # pragma: no cover - collaborator file
    ToolCall = Any  # type: ignore[assignment, misc]
    _TOOLCALL_AVAILABLE = False

# kit.loop.agent is also a collaborator's file, used only by this module's
# own __main__ demo (to build real Commands the same way the arena's trusted
# canonicaliser would) — never by decide() itself, which never touches the
# loop. Degraded the same way.
try:
    from kit.loop.agent import canonicalise_action as _canonicalise_action
except ImportError:  # pragma: no cover - collaborator file
    _canonicalise_action = None

from agent.telemetry import RecordingGatewayContext, Telemetry
from agent.strategy import BudgetPacer, cheap_mask, is_catalog_trap, pick_replica, successor_of
from kit.mcp.specs import A2A_PEERS, TOOL_SPECS, cost
from kit.mcp.a2a import AgentCard, DelegationToken, admit_skill, verify_card, verify_delegation
from kit.world.anchor import Anchor, AnchorSyntaxError

__all__ = [
    "COMMAND_KINDS",
    "DECISION_VERDICTS",
    "Command",
    "Decision",
    "GatewayContext",
    "Gateway",
]

# CONTRACTS.md 4.1: `Command.kind` — "mcp" | "a2a" | "discover". An "answer"
# action is NEVER a Command (kit/loop/agent.py's own module docstring: "an
# answer is not a tool call routed to a server, so it never becomes a
# Command at all") — it is handled entirely by the loop/arena and never
# reaches `Gateway.decide`.
COMMAND_KINDS: frozenset[str] = frozenset({"mcp", "a2a", "discover"})

# CONTRACTS.md 4.1: `Decision.verdict` — the closed three-member set.
DECISION_VERDICTS: frozenset[str] = frozenset({"forward", "deny", "rewrite"})


@dataclass(frozen=True, slots=True)
class Command:
    """CONTRACTS.md 4.1, field for field — "canonicalised by the arena
    BEFORE the student sees it". You never build one of these from your own
    agent's raw text; the arena's canonicaliser (kit/loop/agent.py's
    `canonicalise_action`, run inside the trusted envelope) already did that
    work and minted `cmd_id` by the time `decide()` sees it. The
    `from_action_dict` classmethod below exists only so this file's own demo
    (and your local tests, if you write any) can build a realistic `Command`
    without duplicating the arena's canonicalisation logic."""

    cmd_id: str
    kind: str  # "mcp" | "a2a" | "discover" — see COMMAND_KINDS
    raw: str
    server: str
    tool: str
    args: dict
    fields: tuple[str, ...]
    headers: dict
    lease_id: str | None
    call_index: int

    def __post_init__(self) -> None:
        if not isinstance(self.cmd_id, str) or not self.cmd_id:
            raise ValueError(f"Command.cmd_id must be a non-empty str, got {self.cmd_id!r}")
        if self.kind not in COMMAND_KINDS:
            raise ValueError(f"Command.kind must be one of {sorted(COMMAND_KINDS)}, got {self.kind!r}")
        if not isinstance(self.server, str) or not self.server:
            raise ValueError(f"Command.server must be a non-empty str, got {self.server!r}")
        if not isinstance(self.tool, str) or not self.tool:
            raise ValueError(f"Command.tool must be a non-empty str, got {self.tool!r}")
        if not isinstance(self.args, dict):
            raise ValueError(f"Command.args must be a dict, got {type(self.args).__name__}")
        if not isinstance(self.headers, dict):
            raise ValueError(f"Command.headers must be a dict, got {type(self.headers).__name__}")
        if (
            not isinstance(self.call_index, int)
            or isinstance(self.call_index, bool)
            or self.call_index < 0
        ):
            raise ValueError(f"Command.call_index must be a non-negative int, got {self.call_index!r}")

    @classmethod
    def from_action_dict(cls, action: Mapping[str, Any], *, cmd_id: str) -> "Command":
        """Build a `Command` from the dict shape `kit.loop.agent.canonicalise_action`
        returns (`kind, raw, server, tool, args, fields, headers, lease_id,
        call_index` — everything except the arena-minted `cmd_id`, supplied
        here as a keyword). Raises `ValueError` if `action["kind"] ==
        "answer"` — an answer is never a Command (see the module docstring).
        This is a convenience for tests/demos, not something the real arena
        calls: the trusted envelope mints `cmd_id` itself and constructs the
        real `Command` on its own side of the boundary."""
        kind = action.get("kind")
        if kind == "answer":
            raise ValueError(
                "an 'answer' action never becomes a Command (kit/loop/agent.py: "
                "\"an answer is not a tool call routed to a server\") — do not "
                "route it through Gateway.decide at all"
            )
        return cls(
            cmd_id=cmd_id,
            kind=kind,
            raw=action["raw"],
            server=action["server"],
            tool=action["tool"],
            args=dict(action.get("args", {})),
            fields=tuple(action.get("fields", ())),
            headers=dict(action.get("headers", {})),
            lease_id=action.get("lease_id"),
            call_index=action.get("call_index", 0),
        )

    def to_dict(self) -> dict:
        return {
            "cmd_id": self.cmd_id,
            "kind": self.kind,
            "raw": self.raw,
            "server": self.server,
            "tool": self.tool,
            "args": dict(self.args),
            "fields": list(self.fields),
            "headers": dict(self.headers),
            "lease_id": self.lease_id,
            "call_index": self.call_index,
        }


@dataclass(frozen=True, slots=True)
class Decision:
    """CONTRACTS.md 4.1, field for field.

    Validated strictly (`__post_init__`) because a *structurally* invalid
    `Decision` is charged exactly like a raised exception — CONTRACTS.md
    4.1's charging table: "malformed Decision (schema-invalid) -> 2 cr
    penalty, command denied." Failing loudly HERE, in your own process
    during development, is strictly better than discovering it live in a
    duel as an unexplained penalty.

    `verdict == "deny"` requires a non-empty `reason` (CONTRACTS.md 4.1:
    "required when verdict == 'deny'; shown in the combat log") and
    forbids `call` — a real denial has nothing left to carry out.
    `verdict` in `("forward", "rewrite")` requires `call` to be set — the
    arena executes exactly that `ToolCall`, nothing else, per the trusted
    envelope's whole point (see the module docstring)."""

    verdict: str  # "forward" | "deny" | "rewrite" — see DECISION_VERDICTS
    reason: str | None = None
    call: "ToolCall | None" = None
    quarantine: bool = False
    note: str | None = None

    def __post_init__(self) -> None:
        if self.verdict not in DECISION_VERDICTS:
            raise ValueError(
                f"Decision.verdict must be one of {sorted(DECISION_VERDICTS)}, got {self.verdict!r}"
            )
        if self.verdict == "deny":
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ValueError("Decision.verdict=='deny' requires a non-empty 'reason'")
            if self.call is not None:
                raise ValueError("Decision.verdict=='deny' must not carry a 'call' — there is nothing to run")
        else:  # forward | rewrite
            if self.call is None:
                raise ValueError(f"Decision.verdict=={self.verdict!r} requires 'call' to be set")
            if _TOOLCALL_AVAILABLE and not isinstance(self.call, ToolCall):
                raise ValueError(
                    f"Decision.call must be a kit.mcp.types.ToolCall instance, got {type(self.call).__name__}"
                )
        if not isinstance(self.quarantine, bool):
            raise ValueError(f"Decision.quarantine must be a bool, got {self.quarantine!r}")
        if self.note is not None and not isinstance(self.note, str):
            raise ValueError(f"Decision.note must be a str or None, got {self.note!r}")

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "call": self.call.to_dict() if self.call is not None and hasattr(self.call, "to_dict") else self.call,
            "quarantine": self.quarantine,
            "note": self.note,
        }


@runtime_checkable
class GatewayContext(Protocol):
    """CONTRACTS.md 4.2 — "read-only, arena-provided". Note what this is
    NOT: unlike `Command`/`Decision` above, CONTRACTS.md writes this as a
    plain `class`, not a `@dataclass(frozen=True, ...)`. That is a real
    signal, not an inconsistency — a `GatewayContext` is a LIVE VIEW the
    arena owns and updates as the duel actually progresses (credits get
    spent, rounds advance, leases mint and expire), not a frozen snapshot
    you receive once. This file therefore models it as a structural
    `Protocol`: the arena hands your `Gateway.__init__` some object shaped
    like this (see `agent/telemetry.py`'s `RecordingGatewayContext` for a
    concrete example your own tests/demos can build), and you read
    `self.ctx.<field>` fresh every time `decide()` runs — NEVER cache a
    context field into a local variable across calls; if you need memory
    that persists across the duel (a cache, a budget tracker, drift notes),
    keep it on YOUR OWN `Gateway` instance attributes instead (CONTRACTS.md
    4.3: "Gateway is instantiated once per duel... persists across the 10
    rounds").

    There is deliberately no `execute` method here — see the module
    docstring's "WHY THERE IS NO execute() METHOD" section. `emit` is the
    ONE thing you can do that has any side effect at all, and it writes to
    `own_telemetry` only: your own side, never scored, never shown to the
    opponent (`agent/telemetry.py` wraps it)."""

    act: str  # "learner:sv-0417" — WHOM YOU SERVE. Authority derives from this.
    sub: str  # "agent:vlearn-tutor" — what you are. NOT authority.
    scopes: frozenset[str]  # {"wiki.read"}; writes need "wiki.write:progress" etc.
    credits: int  # remaining this duel
    round: int
    call_index: int
    leases: tuple[str, ...]  # live lease ids, arena-tracked
    history: tuple[Mapping[str, Any], ...]  # YOUR OWN prior (Command, Decision, outcome) triples this duel

    def emit(self, name: str, **payload: Any) -> None: ...


class Gateway:
    """One decision gate for routing, protocol, act/scope authority and budget.

    Only arena-provided context/history and verified protocol data are trusted.
    Result hooks do bookkeeping in memory; they never execute a tool.
    """

    def __init__(self, ctx: GatewayContext) -> None:
        self.ctx = ctx
        self._telemetry = Telemetry(ctx)
        self._pacer = BudgetPacer(starting_pool=max(0, ctx.credits))
        self._round = 0
        self._round_spent = 0
        self._round_allowance = 0
        self._denied_cmd_ids: set[str] = set()
        self._etags: dict[str, tuple[str, int, int]] = {}
        self._leases: dict[str, tuple[int, int]] = {}
        self._cards: dict[str, AgentCard] = {}
        self._replicas: dict[str, str] = {}
        self._replica_etags: dict[tuple, dict[str, str]] = {}
        self._drifting: set[str] = set()
        self._write_keys: set[str] = set()
        self._write_intents: dict[tuple, str] = {}
        self._rate_calls: dict[tuple[str, str], list[int]] = {}
        self._tokens: set[str] = set()
        self._history_seen: set[tuple] = set()
        self._history_count = 0
        self._command_rounds: dict[tuple, int] = {}
        self._failed_reads: set[str] = set()

    @staticmethod
    def _mapping(value: Any) -> dict:
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "to_dict"):
            return value.to_dict()
        return {}

    @staticmethod
    def _learner(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        kind, sep, ident = value.partition(":")
        if sep:
            return "learner:" + ident if kind.lower() == "learner" and ident else None
        return "learner:" + value if value.startswith("sv-") else None

    @staticmethod
    def _headers(headers: dict) -> dict:
        out = {}
        for name, value in headers.items():
            if not isinstance(name, str):
                raise ValueError("header names must be strings")
            key = name.lower()
            if key in out and out[key] != value:
                raise ValueError("conflicting duplicate header")
            out[key] = value
        return out

    @staticmethod
    def _write_intent(call: Any) -> tuple:
        args = call.args
        # Ignore transport headers and irrelevant extra arguments. A new key
        # does not turn a second write to the same record into a new effect.
        return (call.server, call.tool, args.get("anchor"),
                Gateway._learner(args.get("learner") or args.get("learner_id")),
                args.get("concept"))

    @staticmethod
    def _read_key(call: Any) -> str:
        # A fresh authentication token must not make an unchanged failed
        # query look like a new read. Protocol objects are not JSON scalars.
        args = {k: v for k, v in call.args.items() if k not in ("delegation", "agent_card")}
        headers = {k: v for k, v in call.headers.items() if k not in ("delegation", "traceparent")}
        return json.dumps([call.server, call.tool, args, call.fields,
                           headers, call.lease_id], sort_keys=True)

    def note_card(self, server: str, card: Any) -> bool:
        """Accept a signed card from a registry result; never trust verified=True."""
        try:
            parsed = card if isinstance(card, AgentCard) else AgentCard.from_dict(card)
        except (ValueError, TypeError, KeyError):
            self._cards.pop(server, None)
            return False
        check = verify_card(parsed)
        if not check.admitted or check.peer != server:
            self._cards.pop(server, None)
            return False
        self._cards[server] = parsed
        return True

    def note_replica(self, path_id: str, replica: str, *, drifting: bool = False) -> None:
        """Hook for the loop's resolved replica policy, not a request argument.

        Provenance says which replica was read, not which is fresher. Only a
        separately resolved choice may pin a path here.
        """
        if replica not in ("w", "c"):
            raise ValueError("replica must be w or c")
        self._replicas[path_id] = replica
        if drifting:
            self._drifting.add(path_id)

    def note_result(self, call: Any, result: Any, *, round_no: int | None = None) -> None:
        """Consume an authoritative outcome, including failures and partials."""
        call = ToolCall.from_dict(call) if isinstance(call, Mapping) else call
        result = self._mapping(result)
        rnd = self.ctx.round if round_no is None else round_no
        key = (call.server, call.tool)
        spec = TOOL_SPECS.get(key)
        if spec and spec.is_write:
            status = "committed" if result.get("ok") else (result.get("error") or {}).get("code", "unknown")
            self._write_intents[self._write_intent(call)] = status
            self._etags.pop(call.args.get("anchor"), None)
        if not result.get("ok"):
            if not (spec and spec.is_write) and rnd == self.ctx.round:
                self._failed_reads.add(self._read_key(call))
            return
        lease = result.get("lease_id")
        if key in (("slides", "query"), ("slides", "search")) and isinstance(lease, str):
            self._leases[lease] = (rnd, call.call_index)
        if key == ("registry", "provenance"):
            rows = result.get("rows") or []
            etag = result.get("etag") or (rows[0].get("etag") if rows else None)
            anchor = call.args.get("anchor")
            if isinstance(anchor, str) and isinstance(etag, str) and etag:
                self._etags[anchor] = (etag, rnd, call.call_index)
                parsed = Anchor.parse(anchor)
                if parsed.rev in ("w", "c"):
                    identity = (parsed.ns, parsed.slug, parsed.idx, parsed.span)
                    versions = self._replica_etags.setdefault(identity, {})
                    versions[parsed.rev] = etag
                    if len(versions) == 2 and versions["w"] != versions["c"]:
                        if parsed.slug not in self._drifting:
                            self._replicas.pop(parsed.slug, None)
                        self._drifting.add(parsed.slug)
        if key == ("registry", "get_card"):
            for row in result.get("rows") or []:
                card = row.get("card", row)
                if isinstance(card, Mapping) and "signature" in card:
                    self.note_card(str(card.get("name", "")), card)

    def _observe_history(self) -> None:
        history = self.ctx.history
        if len(history) < self._history_count:
            self._history_count = 0
        for entry in history[self._history_count:]:
            if not isinstance(entry, Mapping):
                continue
            command = self._mapping(entry.get("command"))
            outcome = self._mapping(entry.get("outcome", entry.get("result")))
            if not command or not outcome:
                continue
            decision = self._mapping(entry.get("decision"))
            if decision.get("verdict") == "deny":
                continue
            issued = (command.get("cmd_id"), command.get("call_index"))
            if "round" not in entry and issued not in self._command_rounds:
                continue  # Without a known round this cannot be fresh provenance.
            rnd = entry.get("round", self._command_rounds.get(issued, self.ctx.round))
            stamp = (rnd, *issued)
            if stamp in self._history_seen:
                continue
            call = self._mapping(decision.get("call")) or command
            self.note_result(call, outcome.get("result", outcome), round_no=rnd)
            self._history_seen.add(stamp)
        self._history_count = len(history)

    def decide(self, cmd: Command) -> Decision:
        """Synchronous, no I/O. Invalid requests fail closed as valid denials."""
        try:
            self._telemetry.decision_seen(cmd)
            return self._decide(cmd)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            # Malformed JSON/header/mask/protocol data must not crash the gate.
            return self.deny(cmd, "malformed command or protocol metadata")

    def _decide(self, cmd: Command) -> Decision:
        if self.ctx.round != self._round:
            self._round = self.ctx.round
            self._round_spent = 0
            self._failed_reads.clear()
            remaining = max(1, 11 - self._round)
            self._round_allowance = min(12, max(0, self.ctx.credits) // remaining)
        self._observe_history()
        self._command_rounds[(cmd.cmd_id, cmd.call_index)] = self._round
        headers = self._headers(cmd.headers)
        args = dict(cmd.args)
        fields = tuple(cmd.fields)
        if any(not isinstance(f, str) for f in fields):
            return self.deny(cmd, "invalid field mask")
        fields = tuple(sorted({f.lower() for f in fields}))
        if cmd.lease_id is not None and not isinstance(cmd.lease_id, str):
            return self.deny(cmd, "invalid lease id")

        # JOB 1 - ROUTE. The request body cannot choose a replica.
        if any(k in args for k in ("route", "_route", "replica")) or "x-mcp-body-route" in headers:
            return self.deny(cmd, "replica routing belongs in the header, not the body")
        successor = successor_of(cmd.server, cmd.tool)
        server, tool = successor or (cmd.server, cmd.tool)
        spec = TOOL_SPECS.get((server, tool))
        if spec is None:
            return self.deny(cmd, "unknown or unpriced tool")
        if (server in A2A_PEERS) != (cmd.kind == "a2a"):
            return self.deny(cmd, "command kind does not match the server protocol")
        if cmd.kind == "discover" and (server, tool) != ("registry", "list_servers"):
            return self.deny(cmd, "DISCOVER must use registry.list_servers")
        if successor and "snippet" in fields:
            fields = tuple(sorted({"body" if f == "snippet" else f for f in fields}))
        if fields != ("*",) and not set(fields) <= set(spec.all_fields):
            return self.deny(cmd, "field mask contains fields the tool does not expose")
        if headers.get("x-server-fingerprint") == "unvouched" or headers.get("x-card-signature") == "invalid":
            return self.deny(cmd, "server or card identity has failed verification")
        if server == "slides":
            anchor = Anchor.parse(args["anchor"]) if "anchor" in args else None
            if anchor and args.get("path_id") not in (None, anchor.slug):
                return self.deny(cmd, "path_id disagrees with the actual anchor")
            path = anchor.slug if anchor else args.get("path_id")
            choice = pick_replica(path_id=path, known_drifting=path in self._drifting)
            replica = self._replicas.get(path) or (anchor.rev if anchor else None) or choice.replica
            if tool == "get_frame" and path in self._drifting and path not in self._replicas:
                return self.deny(cmd, "replicas disagree; resolve freshness before reading this frame")
            if anchor and anchor.rev and anchor.rev != replica:
                return self.deny(cmd, "anchor and pinned replica disagree; resolve a new anchor first")
            # Frame positions differ across replicas: never rewrite /w/041 to /c/041.
            headers["mcp-replica"] = replica

        routed = replace(cmd, server=server, tool=tool, args=args, fields=fields, headers=headers)

        # JOB 2 - ADMIT. Known protocol failures are refused before charging.
        if spec.needs_lease:
            if not cmd.lease_id or cmd.lease_id not in self.ctx.leases:
                return self.deny(cmd, "get_frame requires a live arena-issued lease")
            minted = self._leases.get(cmd.lease_id)
            if minted and (minted[0] != self._round or not 1 <= cmd.call_index - minted[1] <= 3):
                return self.deny(cmd, "lease is outside its three-call window")
        if spec.rate_limit:
            calls, window = spec.rate_limit
            recent = [r for r in self._rate_calls.get((server, tool), []) if r >= self._round - window + 1]
            if len(recent) >= calls:
                return self.deny(cmd, "tool rate window exhausted")

        # JOB 3 - AUTHORIZE. sub identifies the agent; act owns the target.
        scope = "wiki.write:" + server if spec.is_write else "wiki.read"
        if scope not in self.ctx.scopes:
            return self.deny(cmd, "required scope was not granted: " + scope)
        act = self._learner(self.ctx.act)
        for key in ("learner", "learner_id", "act"):
            if key in args and (not act or self._learner(args[key]) != act):
                return self.deny(cmd, "target learner differs from authenticated act")
        for key in ("anchor", "target", "subject"):
            target = args.get(key)
            if isinstance(target, str) and target.split(":", 1)[0].lower() == "learner":
                if not act or self._learner(target) != act:
                    return self.deny(cmd, "private learner target differs from authenticated act")
        if server in ("progress", "roster") and not (args.get("learner") or args.get("learner_id")):
            return self.deny(cmd, "learner target is required")
        token_id = None
        if server in A2A_PEERS:
            aud = headers.get("aud")
            if aud not in (server, "a2a:" + server, "mcp:" + server):
                return self.deny(cmd, "delegation audience does not match the called server")
            card = args.get("agent_card", self._cards.get(server))
            if card is None:
                return self.deny(cmd, "peer has no verified registry card")
            admitted = admit_skill(card, tool)
            if not admitted.admitted or admitted.peer != server:
                return self.deny(cmd, "peer card signature or declared skill is invalid")
            token = args.get("delegation", headers.get("delegation"))
            if isinstance(token, str):
                if len(token) > 16384:
                    return self.deny(cmd, "delegation metadata is too large")
                token = json.loads(token)
            if not isinstance(token, (DelegationToken, Mapping)):
                return self.deny(cmd, "signed delegation token is required")
            token = token if isinstance(token, DelegationToken) else DelegationToken.from_dict(token)
            checked = verify_delegation(token, aud="a2a:" + server,
                                        call_index=cmd.call_index, expected_act=self.ctx.act,
                                        seen_token_ids=self._tokens)
            if not checked.admitted or token.minted_at_call_index > cmd.call_index or token.sub != self.ctx.sub:
                return self.deny(cmd, "delegation signature, identity, act, audience or lifetime is invalid")
            token_id = token.token_id
            # ToolCall is serialized into the arena trace; protocol dataclasses
            # must leave the gate as JSON data rather than opaque Python objects.
            if "delegation" in args:
                args["delegation"] = token.to_dict()
            else:
                headers["delegation"] = json.dumps(token.to_dict(), sort_keys=True)
            if isinstance(args.get("agent_card"), AgentCard):
                args["agent_card"] = args["agent_card"].to_dict()
        if spec.is_write:
            anchor = args.get("anchor")
            provenance = self._etags.get(anchor)
            if not provenance or provenance[1] != self._round or not 1 <= cmd.call_index - provenance[2] <= 3:
                return self.deny(cmd, "write requires a fresh provenance read for this anchor")
            if headers.get("if-match") != provenance[0]:
                return self.deny(cmd, "If-Match differs from the freshly observed etag")
            idem = headers.get("idempotency-key")
            if not isinstance(idem, str) or not idem or idem in self._write_keys:
                return self.deny(cmd, "write requires an unused idempotency key")
            status = self._write_intents.get(self._write_intent(routed))
            if status not in (None, "conflict", "precondition_missing"):
                return self.deny(cmd, "write effect already committed or outcome is uncertain")

        # JOB 4 - BUDGET. Meter masks and row bounds against the whole duel.
        if fields == ("*",) or is_catalog_trap(server, tool, fields):
            narrow = {("registry", "list_servers"): ("name",),
                      ("glossary", "list_terms"): ("term",)}.get((server, tool), spec.default_fields)
            fields = cheap_mask(server, tool, narrow)
        available = min(max(0, self.ctx.credits), max(0, self._pacer.credits_left),
                        max(0, self._round_allowance - self._round_spent))
        rows = 1
        if spec.row_weight:
            requested = args.get("limit", 1)
            if isinstance(requested, bool):
                return self.deny(cmd, "invalid query row limit")
            rows = max(1, min(6, int(requested)))
            args["limit"] = rows
        estimated = cost(server, tool, fields, n_rows=rows)
        if estimated > available:
            effective = fields or spec.default_fields
            narrow = tuple(f for f in effective if f in spec.default_fields and spec.field_weight[f] == 0)
            if narrow:
                fields = cheap_mask(server, tool, narrow)
            if spec.row_weight:
                rows = max(1, min(rows, available - cost(server, tool, fields, n_rows=0)))
                args["limit"] = rows
            estimated = cost(server, tool, fields, n_rows=rows)
        if estimated > available:
            return self.deny(cmd, "call exceeds available credits or this round's allowance")
        routed = replace(routed, args=args, fields=fields)
        if not spec.is_write and self._read_key(routed) in self._failed_reads:
            return self.deny(cmd, "unchanged retry after a failed read; revise the request")
        call = self._to_tool_call(routed)
        changed = call.to_dict() != self._to_tool_call(cmd).to_dict()
        decision = Decision(verdict="rewrite" if changed else "forward", call=call,
                            note="fields or row limit changed; cite only the returned data" if fields != cmd.fields or args != cmd.args else None)
        self._pacer.record_spend(self._round, estimated)
        self._round_spent += estimated
        if spec.rate_limit:
            self._rate_calls.setdefault((server, tool), []).append(self._round)
        if token_id:
            self._tokens.add(token_id)
        if spec.is_write:
            self._write_keys.add(headers["idempotency-key"])
            self._write_intents[self._write_intent(routed)] = "pending"
            self._etags.pop(args.get("anchor"), None)
        self._telemetry.decision_made(cmd, decision)
        return decision

    def deny(self, cmd: Command, reason: str) -> Decision:
        self._denied_cmd_ids.add(cmd.cmd_id)
        decision = Decision(verdict="deny", reason=reason)
        self._telemetry.decision_made(cmd, decision)
        return decision

    def _to_tool_call(self, cmd: Command) -> "ToolCall":
        return ToolCall(server=cmd.server, tool=cmd.tool, args=dict(cmd.args),
                        fields=cmd.fields, headers=dict(cmd.headers),
                        lease_id=cmd.lease_id, call_index=cmd.call_index)


if __name__ == "__main__":
    ctx = RecordingGatewayContext(act="learner:sv-0417", sub="agent:demo",
                                  scopes=frozenset({"wiki.read"}), credits=100,
                                  round=1, call_index=0)
    gateway = Gateway(ctx)
    for index, (server, tool, args, fields) in enumerate((
        ("slides", "query", {"q": "mcp"}, ()),
        ("slides", "get_frame", {"anchor": "Frame:3f2a9c11/w/041"}, ()),
        ("registry", "list_servers", {}, ("*",)),
    )):
        cmd = Command(f"cmd:{index:04d}", "mcp", "demo", server, tool,
                      args, fields, {}, None, index)
        decision = gateway.decide(cmd)
        print(f"{server}.{tool}: {decision.verdict} {decision.reason or decision.note or ''}")
