import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from score_logic import apply_state_bonus, calculate_final_ranking, calculate_set_score, calculate_state_ranking, determine_impact_winner, determine_interference_mvp


WATCHES = ["watch1", "watch2", "watch3"]
EVENTS = [
    {"attacker": "watch1", "impact": 2, "timestamp": 20},
    {"attacker": "watch1", "impact": 1, "timestamp": 30},
    {"attacker": "watch3", "impact": 4, "timestamp": 10},
]


def test_success_mode_caps_interference_points_at_one_per_set():
    scores, points, mvp = calculate_set_score({}, WATCHES, "watch2", EVENTS, "success")

    assert scores["watch1"]["total_score"] == 2
    assert scores["watch3"]["total_score"] == 2
    assert scores["watch2"]["total_score"] == 0
    assert points["watch1"]["interference"] == 1
    assert mvp is None


def test_impact_mode_awards_only_one_ranking_point_using_all_three_metrics():
    events = [
        {"attacker": "watch1", "impact": 8, "achievement_time_ms": 5000, "timestamp": 10},
        {"attacker": "watch1", "impact": 2, "achievement_time_ms": 3000, "timestamp": 20},
        {"attacker": "watch3", "impact": 20, "achievement_time_ms": 1000, "timestamp": 30},
    ]

    scores, points, _ = calculate_set_score({}, WATCHES, "watch2", events, "impact")

    assert determine_impact_winner(events, WATCHES) == "watch1"
    assert scores["watch1"]["ranking_bonus"] == 1
    assert scores["watch1"]["interference_score"] == 0
    assert scores["watch3"]["interference_score"] == 0
    assert points["watch1"]["ranking"] == 1


def test_mvp_prefers_success_count_then_impact_then_earliest_time():
    assert determine_interference_mvp(EVENTS, WATCHES) == "watch1"


def test_state_bonus_is_applied_only_at_finalization():
    events = [
        {"attacker": "watch1", "quota_keep_ms": 12000, "quota_error_total": 4, "quota_sample_count": 2},
        {"attacker": "watch3", "quota_keep_ms": 8000, "quota_error_total": 1, "quota_sample_count": 1},
    ]
    scores, ranking = apply_state_bonus({}, events, WATCHES)

    assert ranking[0]["watch_id"] == "watch1"
    assert scores["watch1"]["ranking_bonus"] == 1
    assert scores["watch3"]["ranking_bonus"] == 0


def test_state_ranking_uses_average_error_to_break_equal_keep_time():
    events = [
        {"attacker": "watch1", "quota_keep_ms": 5000, "quota_error_total": 6, "quota_sample_count": 2},
        {"attacker": "watch2", "quota_keep_ms": 5000, "quota_error_total": 2, "quota_sample_count": 2},
    ]

    ranking = calculate_state_ranking(events, WATCHES)

    assert ranking[0]["watch_id"] == "watch2"


def test_final_ranking_uses_total_score_before_interference_results():
    scores = {
        "watch1": {"survival_score": 2, "interference_score": 0, "ranking_bonus": 0},
        "watch2": {"survival_score": 1, "interference_score": 1, "ranking_bonus": 0},
        "watch3": {"survival_score": 0, "interference_score": 1, "ranking_bonus": 0},
    }

    ranking = calculate_final_ranking(scores, WATCHES)

    assert [entry["watch_id"] for entry in ranking] == ["watch1", "watch2", "watch3"]
