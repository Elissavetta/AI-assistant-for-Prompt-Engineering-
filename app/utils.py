import asyncio


async def run_in_thread(func, *args):
    return await asyncio.to_thread(func, *args)
