"""Bounded resolution of incomplete Stage-2 episodes.

This is deliberately a post-Stage-2 state machine. It consumes persisted
explain/walk/revise records, checkpoints every paid action, and never turns a
descriptor-only locator into evidence.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import json
import os

from village_drift.stage1 import run as R
from village_drift.stage2 import run as S
from village_drift.stage2 import persistence as persistence_module
from village_drift.stage2.persistence import atomic_write_json, output_lock


RESOLVER_SCHEMA_VERSION = 1
FALLBACK_MODEL = os.environ.get(
    "STAGE2_FALLBACK_MODEL", "claude-opus-4-8")
MAX_EXPANSIONS = 2
MAX_FALLBACKS = 1
MAX_EVIDENCE_REPAIRS = 1


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _hash(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def resolution_episode_id(window_id, index, activity):
    material = f"{window_id}\0{index}\0{activity}".encode()
    return "episode-" + hashlib.sha256(material).hexdigest()[:16]


def _resolver_contract(window, record, runs):
    agent = window["agent"]
    seeds = S._seed_dates(window)
    hi = ((window.get("window") or window).get("forward_to") or seeds[-1])
    # Lookback counts active descriptor rows, not calendar days. Fingerprint
    # every available earlier row so a sparse history cannot extend beyond a
    # guessed calendar-density horizon without invalidating the cache.
    agent_days = sorted(
        day for owner, day in runs if owner == agent and day <= hi)
    lo = agent_days[0] if agent_days else seeds[0]
    descriptors = [
        {"day": day, "threads": entry.get("threads") or [],
         "source": entry.get("source")}
        for (owner, day), entry in sorted(runs.items())
        if owner == agent and lo <= day <= hi
    ]
    evidence = []
    for day in S.window_days(
            agent,
            descriptors[0]["day"] if descriptors else lo, hi):
        signatures = {
            store: S._artifact_signature(store, agent, day)
            for store in ("blockrec", "raw", "digest")
        }
        if any(signatures.values()):
            evidence.append({"day": day, **signatures})
    source_hashes = {}
    for source in (__file__, S.__file__, persistence_module.__file__):
        with open(source, "rb") as fh:
            source_hashes[os.path.basename(source)] = hashlib.sha256(
                fh.read()).hexdigest()
    material = {
        "resolver_schema_version": RESOLVER_SCHEMA_VERSION,
        "stage2_input_fingerprint": record.get("input_fingerprint"),
        "primary_model": R.MODELS["judge"],
        "fallback_model": FALLBACK_MODEL,
        "limits": {
            "max_expansions": MAX_EXPANSIONS,
            "max_fallbacks": MAX_FALLBACKS,
            "max_evidence_repairs": MAX_EVIDENCE_REPAIRS,
            "max_payload_tokens": S.MAX_PAYLOAD_TOKENS,
            "revision_payload_chars": S.REVISION_PAYLOAD_CHARS,
            "walk_lookback": S.WALK_LOOKBACK,
        },
        "prompts": {
            name: hashlib.sha256(S.prompt(name).encode()).hexdigest()
            for name in ("stage2", "walk", "stage2_revision")
        },
        "source_hashes": source_hashes,
        "base_record": record.get("resolver_base"),
        "window_id": window.get("window_id"),
        "descriptors": descriptors,
        "evidence": evidence,
    }
    return _hash(material), {
        "resolver_schema_version": RESOLVER_SCHEMA_VERSION,
        "primary_model": R.MODELS["judge"],
        "fallback_model": FALLBACK_MODEL,
        "descriptor_days": len(descriptors),
        "evidence_days": len(evidence),
        "max_expansions": MAX_EXPANSIONS,
    }


def _checkpoint(callback, record):
    if callback is not None:
        callback(record)


def _normalise_interrupted(record):
    changed = False
    traces = [record.get("window_resolution_trace") or []]
    traces.extend(
        episode.get("resolution_trace") or []
        for episode in record.get("episodes") or []
        if isinstance(episode, dict))
    for candidate in record.get("candidate_resolutions") or []:
        traces.append(candidate.get("resolution_trace") or [])
    for trace in traces:
        for event in trace:
            if event.get("status") == "started":
                event["status"] = "interrupted"
                event["finished_at"] = _now()
                event["outcome"] = "response_unknown_attempt_consumed"
                changed = True
    return changed


def _begin(record, trace, checkpoint, **fields):
    event = {
        "attempt": len(trace) + 1,
        "status": "started",
        "started_at": _now(),
        **fields,
    }
    trace.append(event)
    _checkpoint(checkpoint, record)
    return event


def _finish(record, event, checkpoint, result=None, error=None):
    event["status"] = "completed" if error is None else "failed"
    event["finished_at"] = _now()
    if result is not None:
        usage = result.get("usage")
        event.update({
            "stop_reason": result.get("stop_reason"),
            "usage": usage,
            "cost_usd": R.call_cost(usage, event.get("model")),
            "refused": bool(result.get("refused")),
            "response_complete": result.get("response_complete"),
            "validation_errors": result.get("schema_errors") or [],
            "outcome": _result_outcome(result),
        })
        for key in ("error", "draft", "blockers", "provenance"):
            if result.get(key) is not None:
                event[key] = copy.deepcopy(result[key])
        if result.get("raw") is not None:
            event["raw"] = str(result["raw"])[:400]
    if error is not None:
        event["error"] = R._redact(
            f"{type(error).__name__}: {error}",
            os.environ.get("ANTHROPIC_API_KEY"),
            os.environ.get("OPENAI_API_KEY"))
        event["outcome"] = "exception"


def _pending(state, key):
    pending = state.get(key)
    if not isinstance(pending, dict):
        return None
    result = pending.get("result")
    return result if isinstance(result, dict) else None


def _store_pending(record, state, key, result, checkpoint, **metadata):
    state[key] = {"stored_at": _now(), "result": copy.deepcopy(result),
                  **metadata}
    _checkpoint(checkpoint, record)


def _result_outcome(result):
    if result.get("error"):
        return "invalid_response"
    if result.get("skipped"):
        return "skipped"
    if result.get("refused"):
        return "refusal"
    if result.get("response_complete") is False:
        return "incomplete_response"
    if result.get("raw") is not None:
        return "unparseable_response"
    if result.get("schema_errors"):
        return "schema_invalid"
    return "usable"


def _usable(result):
    return bool(
        not result.get("skipped")
        and not result.get("refused")
        and result.get("response_complete", S.response_completed(
            result.get("stop_reason")))
        and result.get("raw") is None
        and not result.get("schema_errors")
        and isinstance((result.get("verdict") or {}).get("episodes"), list)
        and (result.get("verdict") or {}).get("examined") is True)


def _window_needs_fallback(record):
    validation = record.get("response_validation") or {}
    error = str(record.get("error") or "").lower()
    return bool(
        record.get("stop_reason") == "refusal"
        or record.get("explain_raw") is not None
        or "refus" in error
        or "unparseable" in error
        or "model response did not complete" in error
        or validation.get("complete") is False)


def _apply_explain(record, result, model):
    verdict = result.get("verdict") or {}
    S.preserve_complete_episodes(
        {"episodes": record.get("episodes") or []}, verdict)
    record.update({
        "error": None,
        "examined": verdict.get("examined"),
        "examined_note": verdict.get("examined_note"),
        "episodes": verdict.get("episodes") or [],
        "remaining_history_request": S._history_request(verdict),
        "provenance": result.get("provenance"),
        "payload_chars": result.get("payload_chars"),
        "stop_reason": result.get("stop_reason"),
        "explain_salvaged": result.get("salvaged"),
        "explain_raw": result.get("raw"),
        "response_validation": {
            "complete": (not result.get("schema_errors")
                         and result.get("response_complete")),
            "errors": result.get("schema_errors") or [],
            "model_turn_complete": result.get("response_complete"),
        },
    })


def _fallback_window(window, record, stub, checkpoint):
    meta = record.setdefault("window_resolution", {})
    trace = record.setdefault("window_resolution_trace", [])
    pending = _pending(meta, "pending_result")
    if pending is not None:
        if _usable(pending):
            _apply_explain(record, pending, FALLBACK_MODEL)
            meta.update({"state": "recovered", "stop_reason": None})
        else:
            meta.update({"state": "human_review_required",
                         "stop_reason": _result_outcome(pending)})
        meta.pop("pending_result", None)
        _checkpoint(checkpoint, record)
        return
    if meta.get("fallback_attempts", 0) >= MAX_FALLBACKS:
        meta.update({"state": "human_review_required",
                     "stop_reason": "fallback_exhausted"})
        return
    meta["fallback_attempts"] = meta.get("fallback_attempts", 0) + 1
    try:
        payload, provenance = S.build_payload(window)
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        meta.update({"state": "human_review_required",
                     "stop_reason": "evidence_unavailable",
                     "error": R._redact(
                         f"{type(exc).__name__}: {exc}",
                         os.environ.get("ANTHROPIC_API_KEY"),
                         os.environ.get("OPENAI_API_KEY"))})
        _checkpoint(checkpoint, record)
        return
    event = _begin(
        record, trace, checkpoint, action="fallback_explain",
        model=FALLBACK_MODEL, packet_fingerprint=_hash(payload),
        evidence_range=provenance.get("window_requested"),
        detail_days=provenance.get("window_rendered") or [])
    try:
        result = S.explain(window, stub=stub, model=FALLBACK_MODEL,
                           refusal_retries=0)
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        _finish(record, event, checkpoint, error=exc)
        meta.update({"state": "human_review_required",
                     "stop_reason": "fallback_exception"})
        _checkpoint(checkpoint, record)
        return
    _finish(record, event, checkpoint, result=result)
    record.setdefault("calls", []).append({
        "stage": "resolver_fallback_explain", "model": FALLBACK_MODEL,
        "usage": result.get("usage")})
    _store_pending(record, meta, "pending_result", result, checkpoint)
    if not _usable(result):
        meta.update({"state": "human_review_required",
                     "stop_reason": _result_outcome(result)})
    else:
        _apply_explain(record, result, FALLBACK_MODEL)
        meta.update({"state": "recovered", "stop_reason": None})
    meta.pop("pending_result", None)
    _checkpoint(checkpoint, record)


def _episode_state(record, episode, index):
    state = episode.setdefault("resolution", {})
    state.setdefault("resolution_episode_id", resolution_episode_id(
        record["window_id"], index, episode.get("activity")))
    state.setdefault("activity_anchor", episode.get("activity"))
    state.setdefault("expansion_attempts", 0)
    state.setdefault("fallback_attempts", 0)
    state.setdefault("evidence_repairs", 0)
    state.setdefault("state", "pending")
    episode.setdefault("resolution_trace", [])
    return state


def _previous_descriptor_day(agent, day, runs):
    days = sorted(d for owner, d in runs if owner == agent and d < day)
    return days[-1] if days else None


def _valid_day(value):
    day = str(value or "")[:10]
    try:
        S._date(day)
    except ValueError:
        return None
    return day


def _nearest_evidence_days(window, target):
    """Nearest locally available detailed day on each side of target."""
    target = _valid_day(target)
    if target is None:
        return []
    agent = window["agent"]
    win = window.get("window") or window
    seeds = S._seed_dates(window)
    lo = win.get("back_to") or seeds[0]
    hi = win.get("forward_to") or seeds[-1]
    available = []
    for day in S.window_days(agent, lo, hi):
        if any(S._artifact_signature(store, agent, day)
               for store in ("blockrec", "raw", "digest")):
            available.append(day)
    before = [day for day in available if day < target]
    after = [day for day in available if day > target]
    return ([before[-1]] if before else []) + ([after[0]] if after else [])


def _episode_focus(window, episode, missing):
    """Select evidence days for a field-local repair, not a new boundary."""
    missing = set(missing)
    if missing == {"evidence"}:
        days = []
        for item in episode.get("evidence") or []:
            if isinstance(item, dict):
                day = _valid_day(item.get("day"))
                if day and day not in days:
                    days.append(day)
        return days, []

    if "correction" in missing:
        win = window.get("window") or window
        target = (_valid_day(episode.get("corrected_at"))
                  or _valid_day(win.get("forward_to")))
    else:
        target = _valid_day(episode.get("onset"))
    if target is None:
        return [], []
    optional = []
    for day in [S._shift(target, -1), S._shift(target, 1),
                *_nearest_evidence_days(window, target)]:
        if day != target and day not in optional:
            optional.append(day)
    return [target], optional


def _focused_initial(record, episode, history_request=None):
    clean = copy.deepcopy(episode) if episode else None
    if clean is not None:
        clean.pop("resolution", None)
        clean.pop("resolution_trace", None)
        clean.pop("completeness", None)
    return {
        "examined": True,
        "examined_note": record.get("examined_note") or "Resolver target.",
        "history_request": history_request,
        "episodes": [clean] if episode else [],
    }


def _walk_packet(window, request, runs, lookback):
    agent = window["agent"]
    index, days, _ = S.descriptor_index(
        agent, request["anchor_day"], lookback=lookback, runs=runs)
    material = {
        "anchor_day": request["anchor_day"],
        "anchor_activity": request["activity"],
        "daily_threads": index,
    }
    return _hash(material), days


def _call_walk(window, record, state, trace, request, runs, stub,
               checkpoint, lookback):
    packet_fingerprint, days = _walk_packet(
        window, request, runs, lookback)
    if not days or request["anchor_day"] not in days:
        state.update({"state": "human_review_required",
                      "stop_reason": "evidence_unavailable"})
        return {"error": "descriptor evidence unavailable"}

    def invoke(model, action):
        event = _begin(
            record, trace, checkpoint, action=action, model=model,
            packet_fingerprint=packet_fingerprint,
            evidence_range=[days[0], days[-1]], detail_days=[],
            cursor=days[0])
        try:
            result = S.walk(
                window, stub=stub, runs=runs, request=request,
                lookback=lookback, model=model, lock_activity=True,
                refusal_retries=0)
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            _finish(record, event, checkpoint, error=exc)
            result = {"error": str(exc), "exception": True}
            _store_pending(record, state, "pending_walk", result,
                           checkpoint, model=model, action=action)
            return result
        result.setdefault("response_complete", S.response_completed(
            result.get("stop_reason")))
        result.setdefault("refused", result.get("stop_reason") == "refusal")
        if result.get("usage"):
            record.setdefault("calls", []).append({
                "stage": action, "model": model, "usage": result["usage"]})
        _finish(record, event, checkpoint, result=result)
        _store_pending(record, state, "pending_walk", result, checkpoint,
                       model=model, action=action)
        return result

    result = _pending(state, "pending_walk")
    pending_model = (state.get("pending_walk") or {}).get("model")
    if result is None:
        result = invoke(R.MODELS["judge"], "resolver_walk")
    if (result.get("error")
            and pending_model != FALLBACK_MODEL
            and state.get("fallback_attempts", 0) < MAX_FALLBACKS):
        state["fallback_attempts"] += 1
        _checkpoint(checkpoint, record)
        result = invoke(FALLBACK_MODEL, "resolver_fallback_walk")
    return result


def _revision_packet(window, initial, walked, runs, activity,
                     required_focus_days=None, optional_focus_days=None):
    payload, provenance = S.build_revision_payload(
        window, initial, walked, runs,
        required_focus_days=required_focus_days,
        optional_focus_days=optional_focus_days)
    system = S.revision_system(activity)
    return payload, provenance, _hash({"system": system, "payload": payload})


def _call_revision(window, record, state, trace, initial, walked, runs,
                   stub, checkpoint, validate_boundary, action,
                   required_focus_days=None, optional_focus_days=None):
    payload, provenance, packet_fingerprint = _revision_packet(
        window, initial, walked, runs, state["activity_anchor"],
        required_focus_days, optional_focus_days)
    blockers = S.revision_blockers(provenance)
    if blockers:
        state.update({
            "state": "human_review_required",
            "stop_reason": ("hard_truncated" if blockers.get("hard_truncated")
                            else "evidence_unavailable"),
            "blockers": blockers,
        })
        return {"skipped": True, "blockers": blockers,
                "provenance": provenance}

    def invoke(model, stage):
        event = _begin(
            record, trace, checkpoint, action=stage, model=model,
            packet_fingerprint=packet_fingerprint,
            evidence_range=provenance.get("spine_range"),
            detail_days=provenance.get("detail_days") or [],
            cursor=provenance.get("spine_range", [None])[0])
        try:
            result = S.revise(
                window, initial, walked, stub=stub, runs=runs, model=model,
                locked_activity=state["activity_anchor"],
                refusal_retries=0,
                validate_boundary=validate_boundary,
                required_focus_days=required_focus_days,
                optional_focus_days=optional_focus_days)
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            _finish(record, event, checkpoint, error=exc)
            result = {"error": str(exc), "exception": True,
                      "provenance": provenance}
            _store_pending(record, state, "pending_revision", result,
                           checkpoint, model=model, action=stage)
            return result
        if result.get("usage"):
            record.setdefault("calls", []).append({
                "stage": stage, "model": model, "usage": result["usage"]})
        _finish(record, event, checkpoint, result=result)
        _store_pending(record, state, "pending_revision", result,
                       checkpoint, model=model, action=stage)
        return result

    result = _pending(state, "pending_revision")
    pending_model = (state.get("pending_revision") or {}).get("model")
    if result is None:
        result = invoke(R.MODELS["judge"], action)
    if (not _usable(result)
            and pending_model != FALLBACK_MODEL
            and state.get("fallback_attempts", 0) < MAX_FALLBACKS):
        state["fallback_attempts"] += 1
        _checkpoint(checkpoint, record)
        result = invoke(FALLBACK_MODEL, f"fallback_{action}")
    return result


def _merge_target(record, index, episode, state, trace, result):
    verdict = result.get("verdict") or {}
    returned = verdict.get("episodes") or []
    history_request = S._history_request(verdict)
    anchor = state["activity_anchor"]
    matching = [item for item in returned
                if isinstance(item, dict) and item.get("activity") == anchor]
    unrelated = [item for item in returned if item not in matching]
    if unrelated:
        record.setdefault("resolver_unrelated_candidates", []).extend(
            copy.deepcopy(unrelated))
    if len(returned) != len(matching) or len(matching) > 1:
        state.update({"state": "human_review_required",
                      "stop_reason": "episode_identity_mismatch"})
        return False
    if history_request is None:
        state.pop("history_request", None)
    else:
        state["history_request"] = copy.deepcopy(history_request)
    if not matching:
        if history_request is not None:
            state.update({"state": "pending", "stop_reason": None,
                          "history_request": history_request})
            return False
        record.setdefault("resolution_rejections", []).append({
            "resolution_episode_id": state["resolution_episode_id"],
            "activity": anchor,
            "reason": verdict.get("examined_note"),
            "resolution_trace": copy.deepcopy(trace),
        })
        del record["episodes"][index]
        return "rejected"
    replacement = matching[0]
    if history_request is not None:
        missing = replacement.get("missing_evidence_for")
        if not isinstance(missing, list):
            missing = []
            replacement["missing_evidence_for"] = missing
        if "history_request" not in missing:
            missing.append("history_request")
    replacement["resolution"] = state
    replacement["resolution_trace"] = trace
    missing = S.episode_missing_fields(replacement)
    replacement["missing_evidence_for"] = missing
    replacement["completeness"] = {
        "complete": not missing, "missing_evidence_for": list(missing)}
    record["episodes"][index] = replacement
    if not missing and history_request is None:
        state.update({"state": "resolved", "stop_reason": None})
        return True
    state["state"] = "pending"
    state["stop_reason"] = None
    return False


def _resolve_episode(window, record, index, runs, stub, checkpoint):
    episode = record["episodes"][index]
    missing = S.episode_missing_fields(episode)
    if not missing:
        if episode.get("resolution"):
            episode["resolution"].update(
                {"state": "resolved", "stop_reason": None})
        return
    state = _episode_state(record, episode, index)
    trace = episode["resolution_trace"]
    if state.get("state") in {"resolved", "human_review_required"}:
        return
    evidence_only = set(missing) == {"evidence"}
    continuing = bool(_pending(state, "pending_walk")
                      or _pending(state, "pending_revision"))
    if evidence_only and not continuing:
        if state["evidence_repairs"] >= MAX_EVIDENCE_REPAIRS:
            state.update({"state": "human_review_required",
                          "stop_reason": "evidence_repair_exhausted"})
            _checkpoint(checkpoint, record)
            return
        state["evidence_repairs"] += 1
        action = "resolver_evidence_repair"
    elif not continuing:
        if state["expansion_attempts"] >= MAX_EXPANSIONS:
            state.update({"state": "human_review_required",
                          "stop_reason": "expansion_exhausted"})
            _checkpoint(checkpoint, record)
            return
        state["expansion_attempts"] += 1
        action = "resolver_revision"
    else:
        action = ("resolver_evidence_repair" if evidence_only
                  else "resolver_revision")
    if not continuing:
        _checkpoint(checkpoint, record)

    activity = state["activity_anchor"]
    start = str(episode.get("activity_start") or "")[:10]
    followup = state.get("history_request")
    validate_boundary = ("activity_start" in missing
                         or isinstance(followup, dict))
    if validate_boundary:
        anchor_day = ((followup or {}).get("anchor_day")
                      if isinstance(followup, dict) else None)
        if not anchor_day:
            anchor_day = start if (window["agent"], start) in runs else None
        if anchor_day is None:
            anchor_day = S.anchor_day_for(window, runs)
        if anchor_day is None:
            state.update({"state": "human_review_required",
                          "stop_reason": "evidence_unavailable"})
            _checkpoint(checkpoint, record)
            return
        request = (copy.deepcopy(followup) if isinstance(followup, dict)
                   else {"activity": activity, "anchor_day": anchor_day,
                         "reason": "resolve unsupported activity start"})
        request["activity"] = activity
        request["anchor_day"] = anchor_day
        lookback = S.WALK_LOOKBACK * (state["expansion_attempts"] + 1)
        walked = _call_walk(
            window, record, state, trace, request, runs, stub, checkpoint,
            lookback)
        if walked.get("error") or not walked.get("activity_start_candidate"):
            if state.get("fallback_attempts", 0) >= MAX_FALLBACKS:
                state.update({"state": "human_review_required",
                              "stop_reason": "walk_failed"})
            _checkpoint(checkpoint, record)
            return
    else:
        if not start:
            state.update({"state": "human_review_required",
                          "stop_reason": "boundary_day_unavailable"})
            _checkpoint(checkpoint, record)
            return
        walked = {
            "activity_start_candidate": start,
            "last_nonmatching_day": _previous_descriptor_day(
                window["agent"], start, runs),
            "activity_start_note": episode.get("activity_start_note"),
            "anchor": activity,
            "requested_activity": activity,
            "truncated": False,
        }

    required_focus, optional_focus = None, None
    if not validate_boundary:
        required_focus, optional_focus = _episode_focus(
            window, episode, missing)
        if not required_focus:
            state.update({"state": "human_review_required",
                          "stop_reason": "evidence_unavailable"})
            _checkpoint(checkpoint, record)
            return
    initial = _focused_initial(record, episode, history_request=followup)
    result = _call_revision(
        window, record, state, trace, initial, walked, runs, stub,
        checkpoint, validate_boundary, action,
        required_focus, optional_focus)
    if not _usable(result):
        if state.get("state") != "human_review_required":
            state.update({"state": "human_review_required",
                          "stop_reason": _result_outcome(result)})
        _checkpoint(checkpoint, record)
        return
    _merge_target(record, index, episode, state, trace, result)
    state.pop("pending_walk", None)
    state.pop("pending_revision", None)
    _checkpoint(checkpoint, record)


def _resolve_history_request(window, record, runs, stub, checkpoint):
    request = record.get("remaining_history_request")
    if not isinstance(request, dict):
        return
    candidates = record.setdefault("candidate_resolutions", [])
    if candidates:
        candidate = candidates[0]
    else:
        candidate = {
            "candidate_id": "candidate-" + _hash(request)[:16],
            "activity": request.get("activity"),
            "history_request": copy.deepcopy(request),
            "resolution": {
                "activity_anchor": request.get("activity"),
                "expansion_attempts": 0, "fallback_attempts": 0,
                "evidence_repairs": 0, "state": "pending"},
            "resolution_trace": [],
        }
        candidates.append(candidate)
    state = candidate["resolution"]
    trace = candidate["resolution_trace"]
    if state["state"] in {"resolved", "human_review_required", "rejected"}:
        return
    continuing = bool(_pending(state, "pending_walk")
                      or _pending(state, "pending_revision"))
    if not continuing and state["expansion_attempts"] >= MAX_EXPANSIONS:
        state.update({"state": "human_review_required",
                      "stop_reason": "expansion_exhausted"})
        _checkpoint(checkpoint, record)
        return
    if not continuing:
        state["expansion_attempts"] += 1
        _checkpoint(checkpoint, record)
    lookback = S.WALK_LOOKBACK * (state["expansion_attempts"] + 1)
    walked = _call_walk(
        window, record, state, trace, request, runs, stub, checkpoint,
        lookback)
    if walked.get("error") or not walked.get("activity_start_candidate"):
        state.update({"state": "human_review_required",
                      "stop_reason": "walk_failed"})
        _checkpoint(checkpoint, record)
        return
    initial = _focused_initial(record, None, history_request=request)
    result = _call_revision(
        window, record, state, trace, initial, walked, runs, stub,
        checkpoint, True, "resolver_candidate_revision")
    if not _usable(result):
        if state.get("state") != "human_review_required":
            state.update({"state": "human_review_required",
                          "stop_reason": _result_outcome(result)})
        _checkpoint(checkpoint, record)
        return
    returned = (result.get("verdict") or {}).get("episodes") or []
    next_request = S._history_request(result.get("verdict") or {})
    matching = [ep for ep in returned
                if isinstance(ep, dict)
                and ep.get("activity") == request.get("activity")]
    if len(returned) != len(matching) or len(matching) > 1:
        state.update({"state": "human_review_required",
                      "stop_reason": "episode_identity_mismatch"})
    elif not matching and next_request is not None:
        state.update({"state": "pending", "stop_reason": None})
        record["remaining_history_request"] = next_request
    elif not matching:
        state.update({"state": "rejected", "stop_reason": None})
        record["remaining_history_request"] = None
    else:
        episode = matching[0]
        if next_request is not None:
            missing = episode.get("missing_evidence_for")
            if not isinstance(missing, list):
                missing = []
                episode["missing_evidence_for"] = missing
            if "history_request" not in missing:
                missing.append("history_request")
        episode["resolution"] = state
        episode["resolution_trace"] = trace
        record.setdefault("episodes", []).append(episode)
        missing = S.episode_missing_fields(episode)
        state.update({"state": "resolved" if not missing else "pending",
                      "stop_reason": None})
        # Once the candidate is concrete, episode-local resolution owns any
        # remaining boundary work. Leaving the same history request active
        # would append the episode a second time on the next round.
        record["remaining_history_request"] = None
        candidates.remove(candidate)
    state.pop("pending_walk", None)
    state.pop("pending_revision", None)
    _checkpoint(checkpoint, record)


def recompute_record(record):
    complete = 0
    incomplete = []
    for index, episode in enumerate(record.get("episodes") or []):
        if not isinstance(episode, dict):
            missing = ["episode"]
        else:
            missing = S.episode_missing_fields(episode)
            episode["missing_evidence_for"] = missing
            episode["completeness"] = {
                "complete": not missing,
                "missing_evidence_for": list(missing),
            }
        if missing:
            incomplete.append({"episode_index": index,
                               "missing_evidence_for": missing})
        else:
            complete += 1
    pending_candidates = [
        item for item in record.get("candidate_resolutions") or []
        if (item.get("resolution") or {}).get("state") not in {
            "resolved", "rejected"}
    ]
    record["episode_incompleteness"] = incomplete
    record["n_drift_episodes"] = len(record.get("episodes") or [])
    record["n_complete_episodes"] = complete
    record["n_incomplete_episodes"] = len(incomplete)
    unresolved = bool(
        incomplete or record.get("remaining_history_request")
        or pending_candidates
        or (record.get("window_resolution") or {}).get("state")
        == "human_review_required")
    if record.get("examined") is not True:
        record["status"] = "incomplete"
    elif unresolved:
        record["status"] = "partial" if complete else "incomplete"
    else:
        record["status"] = "final"
    reasons = []
    missing_kinds = sorted({
        field for item in incomplete
        for field in item["missing_evidence_for"]
    })
    if missing_kinds:
        reasons.append("unresolved episode fields: " + ", ".join(
            missing_kinds))
    if record.get("remaining_history_request"):
        reasons.append("history request remains unresolved")
    if pending_candidates:
        reasons.append("one or more resolver candidates require human review")
    if ((record.get("window_resolution") or {}).get("state")
            == "human_review_required"):
        reasons.append("whole-window fallback did not produce a usable result")
    record["missing_evidence_for"] = reasons
    episode_pending = any(
        (ep.get("resolution") or {}).get("state") == "pending"
        for ep in record.get("episodes") or [] if isinstance(ep, dict))
    candidate_pending = any(
        (item.get("resolution") or {}).get("state") == "pending"
        for item in record.get("candidate_resolutions") or []
        if isinstance(item, dict))
    record["resolver_complete"] = not (episode_pending or candidate_pending)
    return record


def resolve_record(window, record, runs, stub=False, checkpoint=None):
    current_input, _ = S.input_fingerprint(window, runs)
    if (record.get("schema_version") != S.OUTPUT_SCHEMA_VERSION
            or record.get("input_fingerprint") != current_input):
        raise ValueError(
            "Stage-2 record does not match the current schema and inputs; "
            "rerun Stage 2 before resolving it")
    record.setdefault("resolver_base", {
        "window_id": record.get("window_id"),
        "examined": record.get("examined"),
        "status": record.get("status"),
        "episode_anchors": [
            {"index": index, "activity": episode.get("activity"),
             "missing_evidence_for": S.episode_missing_fields(episode)}
            for index, episode in enumerate(record.get("episodes") or [])
            if isinstance(episode, dict)
        ],
        "history_request": record.get("remaining_history_request"),
    })
    fingerprint, summary = _resolver_contract(window, record, runs)
    previous = record.get("resolver_fingerprint")
    if previous and previous != fingerprint:
        raise ValueError(
            "resolver inputs changed; rerun Stage 2 before resolving this "
            "record again")
    record["resolver_fingerprint"] = fingerprint
    record["resolver_fingerprint_summary"] = summary
    if _normalise_interrupted(record):
        _checkpoint(checkpoint, record)

    if (record.get("examined") is not True
            and not _window_needs_fallback(record)):
        record.setdefault("window_resolution", {}).update({
            "state": "human_review_required",
            "stop_reason": "non_model_window_failure",
        })
        recompute_record(record)
        _checkpoint(checkpoint, record)
        return record
    if _window_needs_fallback(record):
        _fallback_window(window, record, stub, checkpoint)
        if _window_needs_fallback(record):
            recompute_record(record)
            _checkpoint(checkpoint, record)
            return record

    # Assign immutable IDs before a rejected earlier episode can shift list
    # positions. Complete episodes remain untouched and gain no resolver data.
    for index, episode in enumerate(record.get("episodes") or []):
        if isinstance(episode, dict) and S.episode_missing_fields(episode):
            _episode_state(record, episode, index)

    def resolve_episodes():
        index = 0
        while index < len(record.get("episodes") or []):
            while index < len(record["episodes"]):
                before_len = len(record["episodes"])
                episode = record["episodes"][index]
                before_state = copy.deepcopy(episode.get("resolution") or {})
                _resolve_episode(
                    window, record, index, runs, stub, checkpoint)
                if len(record["episodes"]) < before_len:
                    break
                state = record["episodes"][index].get("resolution") or {}
                if state.get("state") != "pending" or state == before_state:
                    index += 1
                    break
            else:
                break

    resolve_episodes()
    while record.get("remaining_history_request"):
        candidates = record.get("candidate_resolutions") or []
        before_state = copy.deepcopy(
            (candidates[0].get("resolution") if candidates else {}) or {})
        _resolve_history_request(window, record, runs, stub, checkpoint)
        candidates = record.get("candidate_resolutions") or []
        state = (candidates[0].get("resolution") if candidates else {}) or {}
        if state.get("state") != "pending" or state == before_state:
            break
    # A history request can become a concrete incomplete episode. Give its
    # remaining approved expansion to the episode state without duplicating it.
    resolve_episodes()
    recompute_record(record)
    _checkpoint(checkpoint, record)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", help="window_id from windows.jsonl")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--descriptor-tags", required=True,
        help="comma-separated exact Stage-1 tags used for descriptors")
    args = parser.parse_args()
    if not args.window and not args.all:
        raise SystemExit("pass --window <id> or --all")
    tags = tuple(tag.strip() for tag in args.descriptor_tags.split(",")
                 if tag.strip())
    if not tags:
        raise SystemExit("--descriptor-tags requires at least one exact tag")
    runs = S._run_index(tags)
    windows, source = S.load_windows()
    if args.window:
        windows = [w for w in windows if w["window_id"] == args.window]
        if not windows:
            raise SystemExit(f"no window {args.window!r} in {source}")
    if args.limit is not None:
        windows = windows[:args.limit]

    resolved = 0
    for position, window in enumerate(windows, 1):
        path = os.path.join(S.OUT, f"{window['window_id']}.json")
        if not os.path.exists(path):
            print(f"  [{position}/{len(windows)}] {window['window_id']}  "
                  "missing Stage-2 record")
            continue
        with output_lock(path):
            try:
                with open(path) as fh:
                    record = json.load(fh)
            except Exception as exc:
                raise SystemExit(f"cannot read {path}: {exc}") from exc
            resolve_record(
                window, record, runs, stub=False,
                checkpoint=lambda value, p=path: atomic_write_json(p, value))
        resolved += 1
        print(f"  [{position}/{len(windows)}] {window['window_id']}  "
              f"status={record.get('status')} "
              f"complete={record.get('n_complete_episodes', 0)} "
              f"incomplete={record.get('n_incomplete_episodes', 0)}")
    print(f"  resolved {resolved} record(s) -> {S.OUT}")


if __name__ == "__main__":
    main()
