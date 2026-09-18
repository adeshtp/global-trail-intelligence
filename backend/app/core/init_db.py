from sqlmodel import SQLModel

from app.core.database import engine
from app.models.trail import Trail


def init_db():
    SQLModel.metadata.create_all(engine)