"""API v1 router — aggregates all endpoint routers."""

from fastapi import APIRouter
from app.api.v1 import auth, streams, questions, exams, attempts, results, audit

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(streams.router, prefix="/streams", tags=["streams"])
api_router.include_router(questions.router, prefix="/questions", tags=["questions"])
api_router.include_router(exams.router, prefix="/exams", tags=["exams"])
api_router.include_router(attempts.router, prefix="/attempts", tags=["attempts"])
api_router.include_router(results.router, prefix="/results", tags=["results"])
api_router.include_router(audit.router, prefix="/audit", tags=["audit"])
