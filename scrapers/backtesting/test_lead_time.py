"""Test runner for lead_time_analysis — Phase 13 Part 2."""

import os
import sys
from pathlib import Path

# Allow running from the scrapers root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from backtesting.lead_time_analysis import run_lead_time_analysis

if __name__ == "__main__":
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db   = os.environ.get("POSTGRES_DB",   "bizhealth")
    user = os.environ.get("POSTGRES_USER",  "admin")
    pwd  = os.environ["POSTGRES_PASSWORD"]
    engine = create_engine(f"postgresql://{user}:{pwd}@{host}:{port}/{db}", pool_pre_ping=True)
    with Session(engine) as session:
        run_lead_time_analysis(session)
