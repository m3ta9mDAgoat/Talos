import asyncio
from app.db.init_db import init_db
from app.services.jobs import worker_loop

if __name__ == "__main__":
    init_db()
    asyncio.run(worker_loop())
