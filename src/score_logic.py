"""Game score calculation shared by the turn and dashboard APIs."""

SCORE_MODES = {"highest_diff", "lowest_diff", "random_diff"}
ATTACK_SCORING_MODES = {"success", "impact", "ranking", "mvp"}


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

    impacts = {}
    for watch_id in successful_watch_ids:
        try:
            impacts[watch_id] = max(0.0, float(attack_success[watch_id].get("impact", 0) or 0))
        except (TypeError, ValueError):
            impacts[watch_id] = 0.0
    awards = {watch_id: {"points": 0, "reasons": []} for watch_id in successful_watch_ids}

    def add_points(watch_ids, points, reason):
        for watch_id in watch_ids:
            awards[watch_id]["points"] += points
            awards[watch_id]["reasons"].append(reason)

    if scoring_mode == "success":
        add_points(successful_watch_ids, 1, "妨害チャレンジ成功")
    elif scoring_mode == "impact":
        add_points(successful_watch_ids, 2, "妨害チャレンジ成功")
        best_impact = max(impacts.values())
        add_points(
            [watch_id for watch_id in successful_watch_ids if impacts[watch_id] == best_impact],
            3,
            "このターンの最大影響度ボーナス",
        )
    elif scoring_mode == "ranking":
        tier_points = (5, 3, 1)
        impact_tiers = sorted(set(impacts.values()), reverse=True)
        for points, impact in zip(tier_points, impact_tiers):
            add_points(
                [watch_id for watch_id in successful_watch_ids if impacts[watch_id] == impact],
                points,
                f"影響度順位ボーナス {points}点",
            )
    else:
        best_impact = max(impacts.values())
        add_points(
            [watch_id for watch_id in successful_watch_ids if impacts[watch_id] == best_impact],
            5,
            "このターンのMVP",
        )

    return {watch_id: award for watch_id, award in awards.items() if award["points"]}


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
