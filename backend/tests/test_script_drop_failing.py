"""Owner (2026-10-19): failing sentences are left out instead of stopping the breakdown."""
from backend.app.studio import research_gate as rg


def _s(n, bad=()):
    return [{"text": f"Sentence {i}.", "check": "unchecked" if i in bad else "ok"} for i in range(1, n + 1)]


def test_failing_sentences_are_dropped_and_listed():
    keep, dropped, issues = rg.drop_failing(_s(10, bad={3}), ["Sentence 3: Unsupported by the storyboard",
                                                               "Sentence 7: Robert Pattinson role/title context conflicts"])
    assert [s["text"] for s in keep] == [f"Sentence {i}." for i in (1, 2, 4, 5, 6, 8, 9, 10)]
    assert [d["reason"] for d in dropped] == ["Unsupported by the storyboard", "Robert Pattinson role/title context conflicts"]
    assert issues == [] and not rg.script_issues({"sentences": keep, "check_issues": issues}, {})


def test_global_problems_still_block():
    keep, dropped, issues = rg.drop_failing(_s(5), ["Web finding R1 evidence expired or no longer matches"])
    assert dropped == [] and issues == ["Web finding R1 evidence expired or no longer matches"]


def test_too_many_failures_still_stop_for_review():
    keep, dropped, issues = rg.drop_failing(_s(10, bad={1, 2, 3, 4, 5}), [])
    assert len(keep) == 10 and dropped == [] and "too many to drop" in issues[-1]
