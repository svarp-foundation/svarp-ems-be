import asyncio
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.core.config import settings
from app.core.database import engine, Base, SessionLocal
from app.api.v1.router import api_router
from app.services.background_tasks import background_monitor_loop, MONITOR_STATS

# Import all models so they are registered with Base.metadata
import app.models  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Create all tables on startup
    Base.metadata.create_all(bind=engine)

    # 2. Start background monitor task (auto-submits expired attempts, enforces timeouts)
    monitor_task = asyncio.create_task(background_monitor_loop(interval_seconds=15))

    yield

    # 3. Clean cancellation on shutdown
    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:[0-9]+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Enhanced Health Check
@app.get("/health", tags=["health"])
def health_check():
    db_status = "healthy"
    db_latency_ms = None
    start = time.perf_counter()
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        db_latency_ms = round((time.perf_counter() - start) * 1000, 2)
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"

    is_overall_healthy = db_status == "healthy" and MONITOR_STATS.get("is_running", False)

    return {
        "status": "healthy" if is_overall_healthy else "degraded",
        "service": settings.PROJECT_NAME,
        "database": {
            "status": db_status,
            "latency_ms": db_latency_ms,
        },
        "background_monitor": MONITOR_STATS,
    }


# API routes
app.include_router(api_router, prefix=settings.API_V1_STR)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5008)
