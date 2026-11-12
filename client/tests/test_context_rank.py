import math

from skriv_client.context.rank import MAX_TERMS, Candidate, Origin, rank
from skriv_client.context.types import TermKind

ID = TermKind.IDENTIFIER


def test_score_follows_the_selected_recipe():
    # user prompt one group back, assistant replies now and two groups back
    ranking = rank(
        [
            Candidate("fooBar", ID, Origin.ASSISTANT, age=0),
            Candidate("fooBar", ID, Origin.ASSISTANT, age=2),
            Candidate("fooBar", ID, Origin.USER, age=1),
        ]
    )
    best = 1.1 * 0.9  # user, age 1
    support = 1 + 0.9**2 + 0.9  # distinct (origin, age) pairs, decayed
    expected = best * (1 + 0.15 * math.log1p(support - 1)) + 0.12 * (2 - 1)
    (trace,) = ranking.trace
    assert math.isclose(trace.score, expected, abs_tol=1e-4)
    assert ranking.terms[0].weight == round(1 - math.exp(-1.5 * trace.score), 4)


def test_stale_session_terms_fade_below_fresh_repository_terms():
    ranking = rank(
        [
            Candidate("staleTerm", ID, Origin.ASSISTANT, age=7),  # 0.85 * 0.9**7 = 0.41
            Candidate("repoTerm", ID, Origin.REPO_RECENT),  # 0.45
        ]
    )
    assert [t.canonical for t in ranking.terms] == ["repoTerm", "staleTerm"]


def test_near_duplicate_spellings_compete_and_the_best_stays():
    ranking = rank(
        [
            Candidate("parseConfig", ID, Origin.REPO),
            Candidate("parse_config", ID, Origin.ASSISTANT),
            Candidate("parse-config", ID, Origin.REPO_RECENT),
        ]
    )
    assert [t.canonical for t in ranking.terms] == ["parse_config"]
    assert ranking.trace[0].competitors == ("parse-config", "parseConfig")


def test_list_is_capped_at_thirty_by_default():
    many = [Candidate(f"term{chr(97 + i % 26)}{i}x", ID, Origin.ASSISTANT) for i in range(50)]
    assert len(rank(many).terms) == MAX_TERMS == 30
    assert len(rank(many, max_terms=5).terms) == 5
