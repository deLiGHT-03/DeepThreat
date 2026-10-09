"""Win and loss outcome flips caused by an individual starter's points."""


def _win_outcome(score: float, opponent_score: float) -> float:
    if score > opponent_score:
        return 1.0
    if score == opponent_score:
        return 0.5
    return 0.0


def count_player_impact_flips(player_points: float | None,
                              team_scores: list[tuple[object, float]]) -> tuple[float, int]:
    """Count signed outcome swings for a player's points above slot average.

    Positive values represent wins gained by above-average production and losses
    avoided if that production is removed. Negative values represent the reverse
    for below-average production. Every team is compared with every other team.
    """
    if player_points is None or len(team_scores) < 2:
        return 0.0, 0

    flips = 0.0
    direction = 1.0 if player_points > 0 else -1.0
    comparisons = 0
    for team_index, (_, team_score) in enumerate(team_scores):
        for opponent_index, (_, opponent_score) in enumerate(team_scores):
            if team_index == opponent_index:
                continue
            baseline = _win_outcome(team_score, opponent_score)
            with_player = _win_outcome(team_score + player_points, opponent_score)
            without_player = _win_outcome(team_score - player_points, opponent_score)
            if direction > 0:
                flips += max(0.0, with_player - baseline)
                flips += max(0.0, baseline - without_player)
            else:
                flips -= max(0.0, baseline - with_player)
                flips -= max(0.0, without_player - baseline)
            comparisons += 1
    return flips, comparisons


def player_impact_percentage(flips: float, comparisons: int) -> float | None:
    if comparisons <= 0:
        return None
    return flips / comparisons * 100
