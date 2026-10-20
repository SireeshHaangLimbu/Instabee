from evals.faithfulness import extract_numbers, unsupported_numbers


def test_extract_numbers_handles_separators_and_percent():
    assert extract_numbers("8.5% of 248,906 people; 64 086 more; 1 447 122 parcels") == [8.5, 248906, 64086, 1447122]


def test_supported_numbers_pass_including_rounding():
    tools_out = ["pct_covered: 14.3", "population: 248906", "new_population: 64086"]
    answer = "Coverage is 14.3% (about 14%) of 248,906 people, adding 64,086 (roughly 64,000)."
    assert unsupported_numbers(answer, tools_out) == []


def test_invented_numbers_are_flagged():
    assert unsupported_numbers("Coverage is 37.2% across 900 sites.", ["pct_covered: 14.3"]) == [37.2, 900]


def test_fractions_match_percentages():
    assert unsupported_numbers("assuming 80% utilisation", ["- utilisation: 0.8"]) == []


def test_numbers_from_the_given_context_are_supported():
    context = "OSM holds only part of Instabee's network (377 lockers)"
    assert unsupported_numbers("only 377 lockers are mapped", [], context=context) == []
    assert unsupported_numbers("if the share were 26%", [], context=context) == [26]


def test_small_counts_years_and_question_numbers_are_ignored():
    answer = "Top 3 sites for 2026, using a 500 m radius."
    assert unsupported_numbers(answer, [], question="next 3 lockers at 500 m") == []
