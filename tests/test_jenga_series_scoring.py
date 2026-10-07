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


def test_success_mode_awards_one_point_per_success_in_a_set():
    scores, points, mvp = calculate_set_score({}, WATCHES, "watch2", EVENTS, "success")

    assert scores["watch1"]["total_score"] == 3
    assert scores["watch3"]["total_score"] == 2
    assert scores["watch2"]["total_score"] == 0
    assert points["watch1"]["interference"] == 2
    assert scores["watch1"]["interference_score"] == 2
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


def test_state_ranking_equal_times_share_rank():
    events = [
        {"attacker": "watch1", "quota_keep_ms": 5000, "quota_error_total": 6, "quota_sample_count": 2},
        {"attacker": "watch2", "quota_keep_ms": 5000, "quota_error_total": 2, "quota_sample_count": 2},
    ]

    ranking = calculate_state_ranking(events, WATCHES)

    assert {entry["watch_id"] for entry in ranking if entry["rank"] == 1} == {"watch1", "watch2"}


def test_final_ranking_uses_total_score_before_interference_results():
    scores = {
        "watch1": {"survival_score": 2, "interference_score": 0, "ranking_bonus": 0},
        "watch2": {"survival_score": 1, "interference_score": 1, "ranking_bonus": 0},
        "watch3": {"survival_score": 0, "interference_score": 1, "ranking_bonus": 0},
    }

    ranking = calculate_final_ranking(scores, WATCHES)

    assert [entry["watch_id"] for entry in ranking] == ["watch1", "watch2", "watch3"]


def test_final_ranking_equal_totals_share_rank_despite_different_score_components():
    scores = {
        "watch1": {"survival_score": 2},
        "watch2": {"interference_score": 2},
        "watch3": {"ranking_bonus": 1},
    }
    ranking = calculate_final_ranking(scores, list(reversed(WATCHES)))
    assert [(entry["watch_id"], entry["rank"]) for entry in ranking] == [
        ("watch1", 1), ("watch2", 1), ("watch3", 3)
    ]


def test_final_ranking_all_zero_scores_share_first_place():
    assert [entry["rank"] for entry in calculate_final_ranking({}, WATCHES)] == [1, 1, 1]


def test_final_ranking_ties_below_first_place():
    scores = {"watch1": {"survival_score": 2}, "watch2": {"survival_score": 1},
              "watch3": {"interference_score": 1}}
    assert [entry["rank"] for entry in calculate_final_ranking(scores, WATCHES)] == [1, 2, 2]


def test_success_mode_accumulates_three_successes_and_later_sets():
    events = [{"attacker": "watch1", "turn": turn} for turn in [2, 5, 8]]
    scores, points, _ = calculate_set_score({}, WATCHES, "watch1", events, "success")
    assert scores["watch1"]["interference_score"] == 3
    assert scores["watch1"]["total_score"] == 3
    assert points["watch1"]["interference"] == 3
    scores, points, _ = calculate_set_score(scores, WATCHES, "watch2", events[:2], "success")
    assert scores["watch1"]["interference_score"] == 5
    assert scores["watch1"]["total_score"] == 6
    assert points["watch1"]["interference"] == 2


def test_success_mode_ignores_nonparticipants_and_awards_zero_without_success():
    scores, points, _ = calculate_set_score({}, WATCHES, "watch2", [{"attacker": "watch99"}], "success")
    assert all(score["interference_score"] == 0 for score in scores.values())
    assert all(point["interference"] == 0 for point in points.values())


def test_mvp_mode_keeps_one_bonus_point_per_set():
    scores, points, mvp = calculate_set_score({}, WATCHES, "watch2", EVENTS, "mvp")
    assert mvp == "watch1"
    assert scores["watch1"]["interference_score"] == 1
    assert points["watch1"]["interference"] == 1


def test_state_bonus_is_awarded_per_set_and_ties_share_first_place():
    first = [{"attacker": "watch1", "quota_keep_ms": 9000}]
    scores, _ = apply_state_bonus({}, first, WATCHES)
    second = [{"attacker": "watch1", "quota_keep_ms": 1000},
              {"attacker": "watch2", "quota_keep_ms": 1000}]
    scores, ranking = apply_state_bonus(scores, second, WATCHES)
    assert scores["watch1"]["ranking_bonus"] == 2
    assert scores["watch2"]["ranking_bonus"] == 1
    assert {item["watch_id"] for item in ranking if item["rank"] == 1} == {"watch1", "watch2"}
