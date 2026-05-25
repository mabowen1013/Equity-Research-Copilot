from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, text

from app.db.base import Base
from app.models.vector_type import Vector


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "chunk_id",
            "provider",
            "model",
            "dimensions",
            "embedding_input_version",
            name="uq_chunk_embeddings_current_version",
        ),
    )

    id = Column(Integer, primary_key=True)
    chunk_id = Column(ForeignKey("document_chunks.id"), nullable=False, index=True)
    company_id = Column(ForeignKey("companies.id"), nullable=False, index=True)
    filing_id = Column(ForeignKey("filings.id"), nullable=False, index=True)
    provider = Column(String(64), nullable=False, index=True)
    model = Column(String(128), nullable=False, index=True)
    dimensions = Column(Integer, nullable=False)
    embedding_input_version = Column(String(32), nullable=False, index=True)
    content_sha256 = Column(String(64), nullable=False)
    embedding = Column(Vector(1536), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
