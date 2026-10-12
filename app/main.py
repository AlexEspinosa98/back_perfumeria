from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import accounting, admin, auth, me, public, reviews

app = FastAPI(title="Perfumería API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(public.router)
app.include_router(auth.router)
app.include_router(me.router)
app.include_router(reviews.public_router)
app.include_router(reviews.me_router)
app.include_router(reviews.admin_router)
app.include_router(admin.router)
app.include_router(accounting.router)
