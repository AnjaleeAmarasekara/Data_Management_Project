import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://coldsense:coldsense@localhost:5432/coldsense_dq")


def connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)


