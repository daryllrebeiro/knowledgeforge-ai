from evaluation.chunking_profiles import PROFILES


def test_profiles_are_valid_and_distinct() -> None:
    assert len(PROFILES) >= 2
    assert all(profile.chunk_size > profile.overlap > 0 for profile in PROFILES)
    assert len({profile.name for profile in PROFILES}) == len(PROFILES)
