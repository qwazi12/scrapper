from backend.app.studio import countdown_topics as ct


def test_topic_is_worded_for_the_size_picked():
    assert ct.sized("Top 10 Scary Movies That Ruin Sleep", 15) == "Top 15 Scary Movies That Ruin Sleep"
    assert ct.sized("The Top 10 Netflix Shows", 5) == "The Top 5 Netflix Shows"
    assert ct.sized("Best Horror of 2026", 10) == "Top 10 Best Horror of 2026"


def test_best_size_is_never_one_the_titles_cannot_fill():
    assert ct._best("top15", [5, 10]) == "top10"          # "Top 15 Best Movies of All Time" with 10 titles
    assert ct._best("top15", [5, 10, 15]) == "top15"
    assert ct._best("nonsense", [5]) == "top5"
