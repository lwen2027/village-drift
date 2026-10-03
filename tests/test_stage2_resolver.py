"""Bounded resolver state, safety, and persistence tests."""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from village_drift.stage1 import run as R  # noqa: E402
from village_drift.stage2 import persistence as P  # noqa: E402
from village_drift.stage2 import resolve as Z  # noqa: E402
from village_drift.stage2 import run as S  # noqa: E402


def _episode(**updates):
    episode = {
        "activity": "locked activity",
        "activity_start": "2026-01-02",
        "activity_predates_window": False,
        "activity_start_supported": True,
        "activity_start_note": "The boundary is visible.",
        "onset": "2026-01-03",
        "onset_supported": True,
        "onset_note": "The transition is visible.",
        "missing_evidence_for": [],
        "mechanism_shape": "activity_changed",
        "mechanism": "The activity displaced the assigned target.",
        "available_levers": ["Return to the assigned target."],
        "corrected_within_evidence": False,
        "corrected_at": None,
        "corrected_note": "No correction appears in the evidence.",
        "evidence": [{"day": "2026-01-03", "quote": "visible quote"}],
        "evidence_validation": {"complete": True},
        "dissent": "The activity may have served the goal indirectly.",
        "verdict_confidence": 0.8,
        "confidence": 0.7,
    }
    episode.update(updates)
    return episode


def _record(episode=None):
    episodes = [] if episode is None else [episode]
    return {
        "window_id": "w", "agent": "Agent", "input_fingerprint": "base",
        "schema_version": S.OUTPUT_SCHEMA_VERSION,
        "examined": True, "examined_note": "examined", "error": None,
        "response_validation": {"complete": True}, "calls": [],
        "episodes": episodes, "remaining_history_request": None,
    }


def _window():
    return {
        "window_id": "w", "agent": "Agent",
        "seed_days": [{"day": "2026-01-03", "stage1_verdict": True,
                       "confidence": 0.8, "route": "positive"}],
        "back_to": "2026-01-01", "forward_to": "2026-01-04",
    }


def _usable(episode=None):
    return {
        "verdict": {"examined": True, "examined_note": "resolved",
                    "history_request": None,
                    "episodes": [] if episode is None else [episode]},
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "response_complete": True, "stop_reason": None, "refused": False,
        "raw": None, "schema_errors": [], "skipped": False,
        "provenance": {}, "payload_chars": 10,
    }


def test_stage2_call_enforces_250k_limit_before_api(monkeypatch):
    called = []
    monkeypatch.setattr(R, "call", lambda *args, **kwargs: called.append(args))
    oversized = "x" * int(S.MAX_PAYLOAD_TOKENS * S.CHARS_PER_TOKEN_BUDGET + 10)
    try:
        S.model_call("claude-opus-5-5", "system", oversized)
    except ValueError as exc:
        assert "hard limit" in str(exc)
    else:
        raise AssertionError("oversized Stage-2 calls must fail closed")
    assert called == []


def test_one_fallback_means_one_provider_request(monkeypatch):
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return {"stop_reason": "refusal", "content": [],
                "usage": {"input_tokens": 1, "output_tokens": 1}}

    monkeypatch.setattr(R, "_post", post)
    monkeypatch.setattr(R, "MAX_INPUT_TOKENS", 1_000_000)
    old = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "test"
    try:
        _, usage = R.call("claude-opus-4-8", "s", "u",
                          refusal_retries=0)
    finally:
        if old is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = old
    assert len(calls) == 1
    assert usage["stop_reason"] == "refusal"


def test_window_refusal_uses_one_opus_48_fallback(monkeypatch):
    record = _record()
    record.update({"error": "judge refused", "examined": None,
                   "response_validation": {"complete": False}})
    seen = []
    monkeypatch.setattr(S, "build_payload", lambda window: (
        "packet", {"window_requested": ["2026-01-01", "2026-01-04"],
                   "window_rendered": ["2026-01-03"]}))

    def explain(window, stub, model, refusal_retries):
        seen.append((model, refusal_retries))
        result = _usable(_episode())
        result["provenance"] = {}
        result["salvaged"] = False
        return result

    monkeypatch.setattr(S, "explain", explain)
    checkpoints = []
    Z._fallback_window(_window(), record, False,
                       lambda value: checkpoints.append(json.dumps(value)))
    assert seen == [("claude-opus-4-8", 0)]
    assert record["window_resolution"]["state"] == "recovered"
    assert record["window_resolution_trace"][0]["status"] == "completed"
    assert len(checkpoints) >= 2


def test_episode_identity_lock_quarantines_unrelated_result():
    original = _episode(onset_supported=False,
                        missing_evidence_for=["onset"])
    record = _record(original)
    state = Z._episode_state(record, original, 0)
    trace = original["resolution_trace"]
    result = _usable(_episode(activity="different activity"))
    assert Z._merge_target(record, 0, original, state, trace, result) is False
    assert record["episodes"][0]["activity"] == "locked activity"
    assert state["stop_reason"] == "episode_identity_mismatch"
    assert record["resolver_unrelated_candidates"][0]["activity"] == (
        "different activity")


def test_rejected_candidate_is_valid_final_negative():
    original = _episode(onset_supported=False,
                        missing_evidence_for=["onset"])
    record = _record(original)
    state = Z._episode_state(record, original, 0)
    trace = original["resolution_trace"]
    assert Z._merge_target(record, 0, original, state, trace,
                           _usable()) == "rejected"
    Z.recompute_record(record)
    assert record["episodes"] == []
    assert record["status"] == "final"
    assert record["resolution_rejections"][0]["activity"] == (
        "locked activity")


def test_empty_revision_with_history_request_remains_unresolved():
    original = _episode(onset_supported=False,
                        missing_evidence_for=["onset"])
    record = _record(original)
    state = Z._episode_state(record, original, 0)
    trace = original["resolution_trace"]
    result = _usable()
    result["verdict"]["history_request"] = {
        "activity": "locked activity", "anchor_day": "2026-01-03",
        "reason": "the transition is still outside detailed evidence"}
    assert Z._merge_target(record, 0, original, state, trace, result) is False
    assert len(record["episodes"]) == 1
    assert state["state"] == "pending"
    assert record.get("resolution_rejections") is None


def test_two_expansion_limit_is_persisted(monkeypatch):
    original = _episode(activity_start_supported=False,
                        activity_predates_window=True,
                        missing_evidence_for=["activity_start"])
    record = _record(original)
    runs = {("Agent", "2026-01-03"): {
        "threads": ["locked activity"], "source": "B-full"}}
    monkeypatch.setattr(Z, "_call_walk", lambda *args, **kwargs: {
        "activity_start_candidate": "2026-01-02",
        "last_nonmatching_day": "2026-01-01",
        "requested_activity": "locked activity", "truncated": True})
    calls = []

    def revise(*args, **kwargs):
        calls.append(1)
        return _usable(_episode(
            activity_start_supported=False, activity_predates_window=True,
            missing_evidence_for=["activity_start"]))

    monkeypatch.setattr(Z, "_call_revision", revise)
    for _ in range(3):
        Z._resolve_episode(_window(), record, 0, runs, False, None)
    state = record["episodes"][0]["resolution"]
    assert len(calls) == 2
    assert state["expansion_attempts"] == 2
    assert state["state"] == "human_review_required"
    assert state["stop_reason"] == "expansion_exhausted"


def test_missing_descriptors_stops_before_model_call(monkeypatch):
    record = _record()
    state = {"fallback_attempts": 0}
    called = []
    monkeypatch.setattr(S, "walk", lambda *args, **kwargs: called.append(1))
    result = Z._call_walk(
        _window(), record, state, [],
        {"activity": "locked activity", "anchor_day": "2026-01-03"},
        {}, False, None, 360)
    assert result["error"]
    assert called == []
    assert state["stop_reason"] == "evidence_unavailable"


def test_focused_hard_truncation_stops_before_model_call(monkeypatch):
    record = _record()
    state = {"activity_anchor": "locked activity", "fallback_attempts": 0}
    monkeypatch.setattr(Z, "_revision_packet", lambda *args: (
        "packet", {"hard_truncated": True, "spine_range": ["a", "b"]},
        "fingerprint"))
    called = []
    monkeypatch.setattr(S, "revise", lambda *args, **kwargs: called.append(1))
    result = Z._call_revision(
        _window(), record, state, [],
        {"episodes": []}, {"activity_start_candidate": "2026-01-02"},
        {}, False, None, True, "resolver_revision")
    assert result["skipped"] is True
    assert called == []
    assert state["stop_reason"] == "hard_truncated"


def test_history_candidate_promotes_once_without_duplicate_request(
        monkeypatch):
    request = {"activity": "locked activity", "anchor_day": "2026-01-03",
               "reason": "earlier history could reverse the verdict"}
    record = _record()
    record["remaining_history_request"] = request
    monkeypatch.setattr(Z, "_call_walk", lambda *args, **kwargs: {
        "activity_start_candidate": "2026-01-02",
        "last_nonmatching_day": "2026-01-01",
        "requested_activity": "locked activity"})
    incomplete = _episode(onset_supported=False,
                          missing_evidence_for=["onset"])
    monkeypatch.setattr(Z, "_call_revision", lambda *args, **kwargs: (
        _usable(incomplete)))
    Z._resolve_history_request(
        _window(), record, {("Agent", "2026-01-03"): {
            "threads": ["locked activity"], "source": "B-full"}},
        False, None)
    assert len(record["episodes"]) == 1
    assert record["remaining_history_request"] is None
    assert record["candidate_resolutions"] == []


def test_history_candidate_repeated_request_is_not_a_rejection(monkeypatch):
    request = {"activity": "locked activity", "anchor_day": "2026-01-03",
               "reason": "earlier evidence could reverse the verdict"}
    record = _record()
    record["remaining_history_request"] = request
    monkeypatch.setattr(Z, "_call_walk", lambda *args, **kwargs: {
        "activity_start_candidate": "2026-01-02",
        "last_nonmatching_day": "2026-01-01",
        "requested_activity": "locked activity"})
    result = _usable()
    result["verdict"]["history_request"] = request
    monkeypatch.setattr(Z, "_call_revision", lambda *args, **kwargs: result)
    Z._resolve_history_request(
        _window(), record, {("Agent", "2026-01-03"): {
            "threads": ["locked activity"], "source": "B-full"}},
        False, None)
    assert record["episodes"] == []
    assert record["remaining_history_request"] == request
    assert record["candidate_resolutions"][0]["resolution"]["state"] == (
        "pending")


def test_recompute_is_episode_local():
    complete = _episode(activity="complete")
    incomplete = _episode(activity="open", onset_supported=False,
                          missing_evidence_for=["onset"])
    record = _record()
    record["episodes"] = [complete, incomplete]
    Z.recompute_record(record)
    assert record["status"] == "partial"
    assert record["n_complete_episodes"] == 1
    assert record["n_incomplete_episodes"] == 1
    assert complete["completeness"]["complete"] is True


def test_complete_episode_never_gains_resolution_trace(monkeypatch):
    episode = _episode()
    record = _record(episode)
    monkeypatch.setattr(Z, "_resolver_contract", lambda *args: (
        "fingerprint", {"resolver_schema_version": 1}))
    monkeypatch.setattr(S, "input_fingerprint", lambda *args: ("base", {}))
    Z.resolve_record(_window(), record, {}, checkpoint=None)
    assert "resolution_trace" not in episode
    assert "resolution" not in episode
    assert record["status"] == "final"


def test_resume_rejects_changed_resolver_inputs(monkeypatch):
    record = _record(_episode())
    record["resolver_fingerprint"] = "old"
    monkeypatch.setattr(Z, "_resolver_contract", lambda *args: (
        "new", {"resolver_schema_version": 1}))
    monkeypatch.setattr(S, "input_fingerprint", lambda *args: ("base", {}))
    try:
        Z.resolve_record(_window(), record, {})
    except ValueError as exc:
        assert "inputs changed" in str(exc)
    else:
        raise AssertionError("resolver resume must fail closed on mismatch")


def test_interrupted_attempt_is_consumed():
    episode = _episode(onset_supported=False,
                       missing_evidence_for=["onset"])
    episode["resolution_trace"] = [{"status": "started", "attempt": 1}]
    record = _record(episode)
    assert Z._normalise_interrupted(record) is True
    event = episode["resolution_trace"][0]
    assert event["status"] == "interrupted"
    assert event["outcome"] == "response_unknown_attempt_consumed"


def test_atomic_checkpoint_preserves_old_file_on_write_failure(
        tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    path.write_text('{"old": true}')

    def fail(*args, **kwargs):
        raise RuntimeError("serialization failed")

    monkeypatch.setattr(P.json, "dump", fail)
    try:
        P.atomic_write_json(str(path), {"new": True})
    except RuntimeError:
        pass
    else:
        raise AssertionError("the injected write failure must escape")
    assert json.loads(path.read_text()) == {"old": True}
    assert not list(tmp_path.glob("*.tmp.*"))


def test_locked_walk_sends_target_identity_and_real_descriptor(monkeypatch):
    captured = []
    runs = {("Agent", "2026-01-03"): {
        "threads": ["locked project details", "unrelated work"],
        "source": "B-full"}}
    monkeypatch.setattr(S, "descriptor_index", lambda *args, **kwargs: (
        "2026-01-03: locked project details | unrelated work",
        ["2026-01-03"], False))

    def model_call(model, system, user, *args, **kwargs):
        captured.append(user)
        return (json.dumps({
            "activity_start_candidate": "2026-01-03",
            "why": "boundary", "last_nonmatching_day": None}),
                {"stop_reason": "end_turn"})

    monkeypatch.setattr(S, "model_call", model_call)
    result = S.walk(
        _window(), runs=runs,
        request={"activity": "locked activity", "anchor_day": "2026-01-03"},
        lock_activity=True)
    assert result["activity_start_candidate"] == "2026-01-03"
    assert "TARGET ACTIVITY (identity lock): locked activity" in captured[0]
    assert "ANCHOR ACTIVITY: locked project details" in captured[0]


def test_evidence_repair_focuses_only_cited_days():
    episode = _episode(
        evidence=[{"day": "2026-01-03", "quote": "bad"}],
        evidence_validation={"complete": False},
        missing_evidence_for=["evidence"])
    required, optional = Z._episode_focus(_window(), episode, ["evidence"])
    assert required == ["2026-01-03"]
    assert optional == []


def test_focused_revision_packet_does_not_require_seed_or_predecessor(
        monkeypatch):
    coverage = {
        "spine_days_total": 4, "spine_days_present": 4,
        "spine_days_missing": 0, "spine_missing_days": [],
        "spine_sources": {"block_and_descriptor": 4}}
    monkeypatch.setattr(S, "build_spine", lambda *args: (
        "compact spine", [], coverage))
    monkeypatch.setattr(S, "day_evidence", lambda agent, day: (
        f"evidence for {day}", "full"))
    _, provenance = S.build_revision_payload(
        _window(), {"episodes": [_episode(onset_supported=False)]},
        {"activity_start_candidate": "2026-01-02",
         "last_nonmatching_day": "2026-01-01"}, {},
        required_focus_days=["2026-01-01"],
        optional_focus_days=["2026-01-02"])
    assert provenance["required_detail_days"] == ["2026-01-01"]
    assert provenance["detail_days"] == ["2026-01-01", "2026-01-02"]


def test_completed_revision_response_is_reused_after_reload(monkeypatch):
    record = {"calls": []}
    state = {"activity_anchor": "locked activity", "fallback_attempts": 0}
    record["state"] = state
    calls = []
    monkeypatch.setattr(Z, "_revision_packet", lambda *args: (
        "packet", {"spine_range": ["2026-01-01", "2026-01-03"],
                   "detail_days": ["2026-01-03"]}, "fingerprint"))
    monkeypatch.setattr(S, "revise", lambda *args, **kwargs: (
        calls.append(1) or _usable(_episode())))
    snapshots = []
    checkpoint = lambda value: snapshots.append(json.dumps(value))
    args = (_window(), record, state, [], {"episodes": []},
            {"activity_start_candidate": "2026-01-02"}, {}, False,
            checkpoint, False, "resolver_revision")
    first = Z._call_revision(*args)
    assert Z._usable(first)
    restored = json.loads(snapshots[-1])
    second = Z._call_revision(
        _window(), restored, restored["state"], [], {"episodes": []},
        {"activity_start_candidate": "2026-01-02"}, {}, False, None,
        False, "resolver_revision")
    assert Z._usable(second)
    assert len(calls) == 1


def test_episode_followup_request_drives_next_walk(monkeypatch):
    original = _episode(
        missing_evidence_for=["history_request"])
    record = _record(original)
    state = Z._episode_state(record, original, 0)
    state["history_request"] = {
        "activity": "locked activity", "anchor_day": "2025-12-31",
        "reason": "inspect earlier history"}
    requests = []
    monkeypatch.setattr(Z, "_call_walk", lambda *args, **kwargs: (
        requests.append(args[4]) or {
            "activity_start_candidate": "2026-01-02",
            "last_nonmatching_day": "2026-01-01"}))
    monkeypatch.setattr(Z, "_call_revision", lambda *args, **kwargs: (
        _usable(_episode())))
    Z._resolve_episode(
        _window(), record, 0,
        {("Agent", "2025-12-31"): {
            "threads": ["locked project"], "source": "B-full"}},
        False, None)
    assert requests[0]["anchor_day"] == "2025-12-31"
