def apply() -> None:
    import asyncio

    from . import _asyncio

    asyncio.Future = asyncio.futures.Future = _asyncio.Future
    asyncio.Task = asyncio.tasks.Task = _asyncio.Task
