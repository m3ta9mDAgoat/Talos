from app.core.enums import ResearchStage
from app.services.lifecycle import can_transition


def test_cannot_jump_to_live():
    assert not can_transition(ResearchStage.DRAFT, ResearchStage.LIVE)


def test_approved_can_go_live():
    assert can_transition(ResearchStage.APPROVED, ResearchStage.LIVE)
