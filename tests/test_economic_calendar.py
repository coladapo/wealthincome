"""Calendar fallback dates — the June-11 phantom-CPI-block regression."""

from core.economic_calendar import _FALLBACK_CPI, _FALLBACK_NFP


# Verified against the BLS 2026 schedule on 2026-06-11.
BLS_2026_CPI = [
    "2026-05-12", "2026-06-10", "2026-07-14", "2026-08-12",
    "2026-09-11", "2026-10-14", "2026-11-10", "2026-12-10",
]


def test_cpi_fallback_matches_verified_bls_schedule():
    assert _FALLBACK_CPI == BLS_2026_CPI


def test_fallback_dates_are_iso_and_sorted():
    for lst in (_FALLBACK_CPI, _FALLBACK_NFP):
        assert lst == sorted(lst)
        for d in lst:
            assert len(d) == 10 and d[4] == "-" and d[7] == "-"


def test_nfp_dates_are_plausible_first_friday_window():
    # NFP lands on the first Friday (or Thursday before a holiday) — day must be 1-7.
    for d in _FALLBACK_NFP:
        assert 1 <= int(d[8:10]) <= 7
