from core.logging import setup_logger
from core.db import Database

logger, run_id = setup_logger("smoke_test")

logger.info(f"Starting smoke test run_id={run_id}")

db = Database()
db.execute(
    """
    INSERT INTO run (script, run_id, status)
    VALUES (%s, %s, %s)
    """,
    ("smoke_test", run_id, "started")
)

logger.info("DB insert OK")
print("OK")
