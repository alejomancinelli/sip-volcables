"""Tests del filtro de presencia: confirma por cantidad de frames y libera por tiempo."""

import pytest

from system.inference.presence import PresenceFilter


def _filter(*, hits: int = 3, window: int = 4, release_s: float = 2.0) -> PresenceFilter:
    return PresenceFilter(confirm_hits=hits, confirm_window=window, release_s=release_s)


def _feed(presence: PresenceFilter, seen: str, *, start_s: float = 0.0,
          step_s: float = 0.1) -> list[bool]:
    """Un frame por caracter —`x` con detección, `.` sin—, cada `step_s` segundos."""
    return [presence.update(start_s + i * step_s, mark == "x") for i, mark in enumerate(seen)]


class TestConfirm:
    def test_a_single_frame_does_not_confirm(self):
        assert not any(_feed(_filter(), "x..."))

    def test_enough_hits_in_the_window_confirm(self):
        assert _feed(_filter(), "xxx")[-1]

    def test_the_hits_do_not_need_to_be_consecutive(self):
        """Algo real que el modelo pierde un frame de cada tres se confirma igual."""
        assert _feed(_filter(), "xx.x")[-1]

    def test_hits_spread_wider_than_the_window_do_not_confirm(self):
        assert not any(_feed(_filter(), "x..x..x..x"))

    def test_one_hit_out_of_one_confirms_at_once(self):
        assert _feed(_filter(hits=1, window=1), "x")[-1]


class TestRelease:
    def test_it_holds_while_less_than_the_release_time_has_passed(self):
        presence = _filter(release_s=2.0)
        _feed(presence, "xxx")
        assert presence.update(0.2 + 1.9, False)

    def test_it_releases_after_the_release_time_without_hits(self):
        presence = _filter(release_s=2.0)
        _feed(presence, "xxx")
        assert not presence.update(0.2 + 2.0, False)

    def test_a_hit_while_present_restarts_the_release(self):
        presence = _filter(release_s=2.0)
        _feed(presence, "xxx")
        presence.update(1.5, True)
        assert presence.update(3.0, False)

    def test_after_a_release_it_confirms_again_from_scratch(self):
        presence = _filter(release_s=1.0)
        _feed(presence, "xxx")
        presence.update(5.0, False)
        assert not presence.update(5.1, True)

    def test_with_zero_release_a_frame_with_a_hit_keeps_it(self):
        presence = _filter(hits=1, window=1, release_s=0.0)
        presence.update(0.0, True)
        assert presence.update(0.1, True)
        assert not presence.update(0.2, False)


class TestSettings:
    @pytest.mark.parametrize("hits, window", [(0, 3), (4, 3)])
    def test_an_impossible_confirmation_stops_the_build(self, hits, window):
        with pytest.raises(ValueError):
            _filter(hits=hits, window=window)

    def test_a_negative_release_stops_the_build(self):
        with pytest.raises(ValueError):
            _filter(release_s=-1.0)

    def test_it_starts_absent(self):
        assert not _filter().is_present
