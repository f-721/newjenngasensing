"""Game score calculation shared by the turn and dashboard APIs."""

SCORE_MODES = {"highest_diff", "lowest_diff", "random_diff"}


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


def turn_scoring_targets(control_mode, rotation_status, attack_success, current_turn):
    if control_mode == "attack_challenge":
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
