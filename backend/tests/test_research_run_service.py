from app.models import ResearchRunRecord
from app.schemas import RetrievalRequest
from app.services.answer_generation import GeneratedAnswer
from app.services.research_run import ResearchRunService

from .test_answer_context import make_response
from .test_answer_generation import SequenceAnswerGenerator


class FakePersistenceSession:
    def __init__(self, *, fail_commit: bool = False) -> None:
        self.added: list[ResearchRunRecord] = []
        self.committed = False
        self.rolled_back = False
        self._fail_commit = fail_commit

    def add(self, record) -> None:
        self.added.append(record)

    def commit(self) -> None:
        if self._fail_commit:
            raise RuntimeError("commit failed")
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


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


def make_passing_generator() -> SequenceAnswerGenerator:
    return SequenceAnswerGenerator(
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


def test_research_run_service_persists_run_record() -> None:
    session = FakePersistenceSession()
    service = ResearchRunService(
        session,
        retriever=FakeRunRetriever(),
        answer_generator=make_passing_generator(),
    )

    run = service.run(RetrievalRequest(ticker="AAPL", question="What was revenue?"))

    assert session.committed
    assert len(session.added) == 1
    record = session.added[0]
    assert record.run_id == run.run_id
    assert record.ticker == "AAPL"
    assert record.status == "completed"
    assert record.validation_status == "passed"
    assert record.payload["answer"] == run.answer
    assert record.payload["contract_version"] == "research_run.v1"


def test_research_run_service_survives_persistence_failure() -> None:
    session = FakePersistenceSession(fail_commit=True)
    service = ResearchRunService(
        session,
        retriever=FakeRunRetriever(),
        answer_generator=make_passing_generator(),
    )

    run = service.run(RetrievalRequest(ticker="AAPL", question="What was revenue?"))

    assert run.status == "completed"
    assert session.rolled_back


def test_research_run_service_get_run_round_trips_payload() -> None:
    session = FakePersistenceSession()
    service = ResearchRunService(
        session,
        retriever=FakeRunRetriever(),
        answer_generator=make_passing_generator(),
    )
    run = service.run(RetrievalRequest(ticker="AAPL", question="What was revenue?"))

    class FakeQuerySession(FakePersistenceSession):
        def __init__(self, record) -> None:
            super().__init__()
            self._record = record

        def scalar(self, statement):
            return self._record

    reader = ResearchRunService(
        FakeQuerySession(session.added[0]),
        retriever=FakeRunRetriever(),
        answer_generator=make_passing_generator(),
    )
    loaded = reader.get_run(run.run_id)

    assert loaded is not None
    assert loaded.run_id == run.run_id
    assert loaded.answer == run.answer
    assert loaded.validation_status == "passed"
