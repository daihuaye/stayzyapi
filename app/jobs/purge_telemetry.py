"""Run daily: python -m app.jobs.purge_telemetry"""
import asyncio
from app.db import SessionFactory
from app.telemetry import purge
async def main():
    async with SessionFactory() as db:
        await purge(db)
if __name__ == "__main__": asyncio.run(main())
