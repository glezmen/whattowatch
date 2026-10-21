from whattowatch.preferences.vocabulary import all_labels, by_key, labels_for_title

CATEGORIES = {"tempo", "hangulat", "humor", "tortenet", "karakter", "stilus", "tema"}


def test_labels_well_formed() -> None:
    labels = all_labels()
    assert len(labels) >= 40
    keys = [label.full_key for label in labels]
    assert len(keys) == len(set(keys))
    assert {label.category for label in labels} == CATEGORIES
    for label in labels:
        assert len(label.keywords_hu) >= 3, label.full_key
        assert all(k == k.lower() and k.strip() == k for k in label.keywords_hu)
        assert by_key(label.full_key) is label


def test_predictable_stem_does_not_cover_unpredictable() -> None:
    for stem in by_key("tortenet/kiszamithato").keywords_hu:
        assert stem not in "kiszámíthatatlan"
    assert "kiszámíthatatlan" in by_key("tortenet/csavaros").keywords_hu


def test_labels_for_title_hungarian_genres() -> None:
    keys = labels_for_title(["Thriller", "bűnügyi"])
    assert {"hangulat/sotet", "tema/bunugy", "hangulat/feszult"} <= keys
    assert "humor/vicces" not in keys
    assert "humor/fekete" in labels_for_title(["Vígjáték"])
    assert "stilus/dokumentarista" in labels_for_title(["Dokumentumfilm"])
    assert "tema/tudomany" in labels_for_title(["Sci-fi"])  # TMDB casing varies
    assert "tema/tudomany" in labels_for_title(["Sci-Fi & Fantasy"])


def test_labels_for_title_keywords_and_empty() -> None:
    assert labels_for_title([]) == frozenset()
    assert "tortenet/csavaros" in labels_for_title([], ["Plot Twist"])
