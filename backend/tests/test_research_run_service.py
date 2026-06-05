from app.schemas import RetrievalRequest
from app.services.answer_generation import GeneratedAnswer
from app.services.research_run import ResearchRunService

from .test_answer_context import make_response
from .test_answer_generation import SequenceAnswerGenerator


class FakeRunRetriever:
    def __init__(self):
        self.call_count = 0

    def retrieve(self, request):
        self.call_count += 1
        return make_response()


def test_research_run_service_returns_auditable_completed_run() -> None:
    retriever = FakeRunRetriever()
    generator = SequenceAnswerGenerator(
        [
            GeneratedAnswer(
                answer=(
                    "Revenue was supported by selected evidence. "
                    "[span:101:primary_financial_statement_chunks:0:80]"
                ),
                cited_evidence_ids=["span:101:primary_financial_statement_chunks:0:80"],
            )
        ]
    )
    service = ResearchRunService(
        None,
        retriever=retriever,
        answer_generator=generator,
    )

    run = service.run(RetrievalRequest(ticker="AAPL", question="What was revenue?"))

    assert run.contract_version == "research_run.v1"
    assert run.status == "completed"
    assert run.validation_status == "passed"
    assert run.run_id.startswith("run_")
    assert run.steps
    assert run.evidence
    assert retriever.call_count == 1


def test_research_run_service_preserves_insufficient_evidence_status() -> None:
    class EmptyRetriever:
        def retrieve(self, request):
            response = make_response()
            response.final_evidence_pack = response.final_evidence_pack.model_copy(
                update={
                    "metric_observations": [],
                    "metric_comparisons": [],
                    "primary_financial_statement_chunks": [],
                    "primary_financial_statement_spans": [],
                }
            )
            response.retrieved_facts = []
            return response

    service = ResearchRunService(
        None,
        retriever=EmptyRetriever(),
        answer_generator=SequenceAnswerGenerator([]),
    )

    run = service.run(RetrievalRequest(ticker="AAPL", question="Unsupported?"))

    assert run.status == "insufficient_evidence"
    assert run.validation_status == "insufficient_evidence"
    assert run.limitations
    assert run.diagnostics.source_coverage_summary
