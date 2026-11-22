from clinical_triage.demo.emergency import evaluate_stroke_red_flag
from clinical_triage.domain.disposition import Disposition


def test_reused_emergency_rule_stops_ordinary_scheduling():
    result = evaluate_stroke_red_flag(right_arm_weakness="yes", speech_abnormality="yes")
    assert result.disposition is Disposition.EMERGENCY
    assert result.trigger_rule_ids == ("focal_neurologic_deficit",)
    assert result.recommended_scheduling_window is None


def test_reused_incomplete_screen_fails_to_human_review():
    result = evaluate_stroke_red_flag(right_arm_weakness="yes", speech_abnormality="unclear")
    assert result.disposition is Disposition.HUMAN_REVIEW
    assert result.requires_human_review is True
