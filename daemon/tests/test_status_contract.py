from forcefocus.session.core import CoreMixin


def test_status_contract_has_stable_timer_fields():
    response = CoreMixin._normalize_status({"total_duration_seconds": 61})

    assert response["total_duration_seconds"] == 61
    assert response["duration_minutes"] == 2
    assert response["remaining_seconds"] == 0
    assert response["pomo_phase_remaining"] == 0
    assert response["pomo_phase_total"] == 0


def test_status_contract_clamps_invalid_timer_values():
    response = CoreMixin._normalize_status(
        {
            "total_duration_seconds": -10,
            "remaining_seconds": -1,
            "pomo_phase_remaining": -2,
            "pomo_phase_total": -3,
        }
    )

    assert response["total_duration_seconds"] == 0
    assert response["duration_minutes"] == 0
    assert response["remaining_seconds"] == 0
    assert response["pomo_phase_remaining"] == 0
    assert response["pomo_phase_total"] == 0
