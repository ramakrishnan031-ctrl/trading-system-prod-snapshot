"""_family_of (GUI grouping key) — retired the blind _long/_short suffix parse in favour
of the STRUCTURED direction (StrategyConfig.direction, surfaced as basic.direction).

Behaviour-neutral for the current naming; robust when a name doesn't encode direction.
"""
from backend.services.strategy_tower import _family_of


def test_behaviour_neutral_for_current_naming():
    # identical to the old blind-suffix result for every correctly-named strategy
    assert _family_of("gap_fade_long", "LONG") == "gap_fade"
    assert _family_of("gap_fade_short", "SHORT") == "gap_fade"
    assert _family_of("vwap_bounce_long", "LONG") == "vwap_bounce"
    assert _family_of("positional_momentum_long", "LONG") == "positional_momentum"
    # a name without a direction suffix is unchanged (matches old behaviour too)
    assert _family_of("positional_sector_rotation", "LONG") == "positional_sector_rotation"


def test_strip_is_direction_driven_not_a_blind_suffix_parse():
    # the token stripped is the one matching the STRUCTURED direction, not any _long/_short
    # a SHORT strategy whose name ends _short strips _short
    assert _family_of("range_breakout_short", "SHORT") == "range_breakout"
    # direction LONG never strips a _short token (would be a mislabel; grouping stays safe)
    assert _family_of("weird_short", "LONG") == "weird_short"


def test_unknown_direction_groups_under_full_name():
    # no direction -> no blind parse -> the strategy groups under itself (safe)
    assert _family_of("gap_fade_long", None) == "gap_fade_long"
    assert _family_of("gap_fade_long", "") == "gap_fade_long"
