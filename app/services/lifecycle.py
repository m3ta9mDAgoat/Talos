from app.core.enums import ResearchStage

_ALLOWED = {
    ResearchStage.DRAFT: {ResearchStage.BACKTESTED},
    ResearchStage.BACKTESTED: {ResearchStage.VALIDATED, ResearchStage.DRAFT},
    ResearchStage.VALIDATED: {ResearchStage.PAPER, ResearchStage.BACKTESTED},
    ResearchStage.PAPER: {ResearchStage.APPROVED, ResearchStage.VALIDATED},
    ResearchStage.APPROVED: {ResearchStage.LIVE, ResearchStage.PAPER},
    ResearchStage.LIVE: {ResearchStage.APPROVED},
}


def can_transition(current: ResearchStage, target: ResearchStage) -> bool:
    return target in _ALLOWED[current]
