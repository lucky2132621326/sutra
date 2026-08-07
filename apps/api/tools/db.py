"""SQLAlchemy engine/session against data/campus.db, seeded by scripts/seed.py."""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "campus.db"
engine = create_engine(f"sqlite:///{DB_PATH}", future=True)
Session = sessionmaker(bind=engine, future=True)
