from fastapi import APIRouter

from app.api.routes import companies, health, jobs

api_router = APIRouter()
api_router.include_router(companies.router)
api_router.include_router(health.router)
api_router.include_router(jobs.router)
