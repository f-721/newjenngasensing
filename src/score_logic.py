"""Game score calculation shared by the turn and dashboard APIs."""

SCORE_MODES = {"highest_diff", "lowest_diff", "random_diff"}
ATTACK_SCORING_MODES = {"success", "impact", "ranking", "mvp"}


def empty_score_entry():
    return {
        "survival_score": 0,
        "interference_score": 0,
        "ranking_bonus": 0,
        "total_score": 0,
    }


def normalize_series_scores(data, watch_ids):
    scores = data if isinstance(data, dict) else {}
    normalized = {}
    for watch_id in sorted({watch_id for watch_id in watch_ids if isinstance(watch_id, str) and watch_id}):
        entry = scores.get(watch_id, {})
        entry = entry if isinstance(entry, dict) else {}
        normalized[watch_id] = {}
        for field in empty_score_entry():
            try:
                normalized[watch_id][field] = int(entry.get(field, 0))
            except (TypeError, ValueError):
                normalized[watch_id][field] = 0
        normalized[watch_id]["total_score"] = (
            normalized[watch_id]["survival_score"]
            + normalized[watch_id]["interference_score"]
            + normalized[watch_id]["ranking_bonus"]
        )
    return normalized


def successful_attack_events(attack_events, watch_ids):
    watches = set(watch_ids)
    return [
        event for event in attack_events if isinstance(event, dict)
        and event.get("attacker") in watches
    ]


def event_impact(event):
    try:
        return max(0.0, float(event.get("impact", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def determine_interference_mvp(attack_events, watch_ids):
    """Choose one MVP by successes, then best threshold exceedance, then earliest success."""
    events = successful_attack_events(attack_events, watch_ids)
    if not events:
        return None

    by_attacker = {watch_id: [] for watch_id in watch_ids}
    for event in events:
        by_attacker[event["attacker"]].append(event)

    def mvp_key(item):
        watch_id, successes = item
        best_impact = max(event_impact(event) for event in successes)
        earliest = min(event.get("timestamp", float("inf")) for event in successes)
        return (-len(successes), -best_impact, earliest, watch_id)

    return min(
        ((watch_id, successes) for watch_id, successes in by_attacker.items() if successes),
        key=mvp_key,
    )[0]


def calculate_interference_ranking(attack_events, watch_ids):
    """Rank players across all sets by successes, impact, then first success time."""
    events = successful_attack_events(attack_events, watch_ids)
    by_attacker = {watch_id: [] for watch_id in watch_ids}
    for event in events:
        by_attacker[event["attacker"]].append(event)

    def ranking_key(watch_id):
        successes = by_attacker[watch_id]
        total_impact = sum(event_impact(event) for event in successes)
        earliest = min((event.get("timestamp", float("inf")) for event in successes), default=float("inf"))
        return (-len(successes), -total_impact, earliest, watch_id)

    ordered = sorted(watch_ids, key=ranking_key)
    return [
        {
            "watch_id": watch_id,
            "success_count": len(by_attacker[watch_id]),
            "total_impact": sum(event_impact(event) for event in by_attacker[watch_id]),
            "rank": index + 1,
        }
        for index, watch_id in enumerate(ordered)
    ]


def calculate_final_ranking(scores, watch_ids):
    normalized = normalize_series_scores(scores, watch_ids)
    ordered = sorted(
        normalized,
        key=lambda watch_id: (
            -normalized[watch_id]["total_score"],
            -normalized[watch_id]["survival_score"],
            -normalized[watch_id]["interference_score"],
            watch_id,
        ),
    )
    return [
        {
            "watch_id": watch_id,
            "rank": index + 1,
            "total_score": normalized[watch_id]["total_score"],
        }
        for index, watch_id in enumerate(ordered)
    ]


def calculate_set_score(scores, watch_ids, collapsed_player, attack_events, scoring_mode):
    """Apply one completed set: survivors +1 and at most one interference point per player."""
    updated = normalize_series_scores(scores, watch_ids)
    set_points = {watch_id: {"survival": 0, "interference": 0} for watch_id in updated}
    for watch_id in updated:
        if watch_id != collapsed_player:
            updated[watch_id]["survival_score"] += 1
            set_points[watch_id]["survival"] = 1

    events = successful_attack_events(attack_events, updated)
    successful_watch_ids = sorted({event["attacker"] for event in events})
    mvp = None
    if scoring_mode == "success":
        winners = successful_watch_ids
    elif scoring_mode == "impact":
        highest_impact = max((event_impact(event) for event in events), default=None)
        winners = sorted({event["attacker"] for event in events if event_impact(event) == highest_impact})
    elif scoring_mode == "mvp":
        mvp = determine_interference_mvp(events, updated)
        winners = [mvp] if mvp else []
    else:
        winners = []

    for watch_id in winners:
        updated[watch_id]["interference_score"] += 1
        set_points[watch_id]["interference"] = 1
    for entry in updated.values():
        entry["total_score"] = entry["survival_score"] + entry["interference_score"] + entry["ranking_bonus"]
    return updated, set_points, mvp


def apply_ranking_bonus(scores, attack_events, watch_ids):
    """Apply final +2/+1 bonuses for ranking mode exactly once at game completion."""
    updated = normalize_series_scores(scores, watch_ids)
    ranking = calculate_interference_ranking(attack_events, list(updated))
    for result in ranking:
        bonus = 2 if result["rank"] == 1 else 1 if result["rank"] == 2 else 0
        updated[result["watch_id"]]["ranking_bonus"] += bonus
    for entry in updated.values():
        entry["total_score"] = entry["survival_score"] + entry["interference_score"] + entry["ranking_bonus"]
    return updated, ranking


def normalize_scores(data):
    if not isinstance(data, dict):
        return {}
    scores = {}
    for watch_id, value in data.items():
        if not isinstance(watch_id, str):
            continue
        try:
            scores[watch_id] = int(value)
        except (TypeError, ValueError):
            scores[watch_id] = 0
    return scores


def scoring_targets(rotation_status, current_turn=None):
    """Return the watch used by the ending turn's table motor."""
    if not isinstance(rotation_status, dict):
        return set()
    if current_turn:
        info = rotation_status.get(current_turn)
        if not isinstance(info, dict):
            return set()
        target_watch = info.get("target_watch")
        if info.get("mode") in SCORE_MODES and isinstance(target_watch, str) and target_watch:
            return {target_watch}
        return set()
    return {
        info.get("target_watch")
        for info in rotation_status.values()
        if isinstance(info, dict)
        and info.get("mode") in SCORE_MODES
        and isinstance(info.get("target_watch"), str)
        and info.get("target_watch")
    }


def challenge_successful_watch_ids(attack_success, current_turn):
    """Return watches with a recorded challenge success for the ending turn."""
    if not isinstance(attack_success, dict) or not current_turn:
        return set()
    return {
        watch_id
        for watch_id, result in attack_success.items()
        if isinstance(watch_id, str)
        and isinstance(result, dict)
        and result.get("turn") == current_turn
    }


def attack_challenge_score_awards(attack_success, current_turn, scoring_mode):
    """Return per-watch points and reasons for one completed challenge turn."""
    if scoring_mode not in ATTACK_SCORING_MODES:
        scoring_mode = "success"

    successful_watch_ids = sorted(challenge_successful_watch_ids(attack_success, current_turn))
    if not successful_watch_ids:
        return {}

    def score_fields(entry):
        if not isinstance(entry, dict):
            return (0, 0, float("inf"))
        try:
            success_count = int(entry.get("success_count", entry.get("count", 1) or 1))
        except (TypeError, ValueError):
            success_count = 1
        try:
            threshold_duration_ms = float(entry.get("threshold_duration_ms", entry.get("duration_ms", 0) or 0))
        except (TypeError, ValueError):
            threshold_duration_ms = 0.0
        try:
            success_time_ms = float(entry.get("success_time", entry.get("timestamp", float("inf")) or float("inf")))
        except (TypeError, ValueError):
            success_time_ms = float("inf")
        return (success_count, threshold_duration_ms, success_time_ms)

    awards = {watch_id: {"points": 0, "reasons": []} for watch_id in successful_watch_ids}

    def add_points(watch_ids, points, reason):
        for watch_id in watch_ids:
            awards[watch_id]["points"] += points
            awards[watch_id]["reasons"].append(reason)

    if scoring_mode == "success":
        add_points(successful_watch_ids, 1, "妨害チャレンジ成功")
    elif scoring_mode == "impact":
        winners = []
        best_key = None
        for watch_id in successful_watch_ids:
            key = score_fields(attack_success.get(watch_id))
            if best_key is None or key[0] > best_key[0] or (key[0] == best_key[0] and key[1] > best_key[1]) or (key[0] == best_key[0] and key[1] == best_key[1] and key[2] < best_key[2]):
                best_key = key
                winners = [watch_id]
            elif key[0] == best_key[0] and key[1] == best_key[1] and key[2] == best_key[2]:
                winners.append(watch_id)

        for watch_id in successful_watch_ids:
            add_points([watch_id], 1, "妨害チャレンジ成功")
        if winners:
            add_points(winners, 1, "影響度: 成功数・ノルマ維持時間・早さで判定")
    elif scoring_mode == "ranking":
        ordered = sorted(
            successful_watch_ids,
            key=lambda watch_id: (
                score_fields(attack_success.get(watch_id))[0],
                score_fields(attack_success.get(watch_id))[1],
                -score_fields(attack_success.get(watch_id))[2],
            ),
            reverse=True,
        )
        tier_points = {0: 5, 1: 3, 2: 1}
        for index, watch_id in enumerate(ordered[:3]):
            if index in tier_points:
                add_points([watch_id], tier_points[index], f"影響度順位ボーナス {tier_points[index]}点")
    else:
        sample = {}
        for watch_id in successful_watch_ids:
            sample[watch_id] = score_fields(attack_success.get(watch_id))
        best = max(sample.values(), key=lambda item: (item[0], item[1], -item[2]))
        mvp_winners = [watch_id for watch_id, value in sample.items() if value == best]
        add_points(mvp_winners, 5, "このターンのMVP")

    return {watch_id: award for watch_id, award in awards.items() if award["points"]}


def turn_scoring_targets(control_mode, rotation_status, attack_success, current_turn):
    if control_mode in {"attack_challenge", "attack_challenge_wait"}:
        return challenge_successful_watch_ids(attack_success, current_turn)
    if control_mode in SCORE_MODES:
        return scoring_targets(rotation_status, current_turn)
    return set()


def apply_points(scores, watch_ids, points):
    updated = normalize_scores(scores)
    for watch_id in set(watch_ids):
        if isinstance(watch_id, str) and watch_id:
            updated[watch_id] = updated.get(watch_id, 0) + points
    return updated
