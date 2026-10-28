import os

from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://coldsense:coldsense@localhost:5432/coldsense_dq"
)


def get_engine():
    return create_engine(DATABASE_URL)
