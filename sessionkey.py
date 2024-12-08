import asyncio, asyncpg, os, dotenv
import logging
logging.basicConfig(level=logging.DEBUG)
dotenv.load_dotenv()
async def run():
    logging.debug("Starting database connection...")
    pool = await asyncpg.create_pool(
        database="lfm",
        host="localhost",
        password=os.getenv("PGP"),
        port="5432"
    )
    logging.debug("Connected")
    async with pool.acquire() as conn:
        await conn.execute("CREATE TABLE SESSIONS IF NOT EXISTS (USERNAME VARCHAR(15), KEY TEXT)")
    
    await pool.close()

asyncio.run(run())