from fastapi import APIRouter

from app.api.routes import companies, filings, health, jobs, research

api_router = APIRouter()
api_router.include_router(companies.router)
api_router.include_router(filings.router)
api_router.include_router(health.router)
api_router.include_router(jobs.router)
api_router.include_router(research.router)
