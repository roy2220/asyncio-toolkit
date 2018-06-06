import asyncio
import functools
import inspect
import itertools
import typing

from .typing import Coroutine


_T = typing.TypeVar("_T")


async def delay_cancellation(coro_or_future: typing.Awaitable[_T], *
                             , loop: typing.Optional[asyncio.AbstractEventLoop]=None) -> _T:
    if loop is None:
        loop = asyncio.get_event_loop()

    future = asyncio.ensure_future(coro_or_future, loop=loop)
    was_cancelled = False

    while True:
        try:
            result = await shield(future, loop=loop)
        except asyncio.CancelledError:
            if future.cancelled():
                raise

            was_cancelled = True
        else:
            break

    if was_cancelled:
        asyncio.Task.current_task(loop=loop).cancel()

    return result


async def atomize_cancellation(coro: Coroutine[_T], *
                               , loop: typing.Optional[asyncio.AbstractEventLoop]=None) -> _T:
    if loop is None:
        loop = asyncio.get_event_loop()

    task = loop.create_task(coro)
    was_cancelled = False

    while True:
        try:
            result = await shield(task, loop=loop)
        except asyncio.CancelledError:
            if task.cancelled():
                raise

            if inspect.getcoroutinestate(coro) == inspect.CORO_CREATED:
                task.cancel()
                raise

            was_cancelled = True
        else:
            break

    if was_cancelled:
        asyncio.Task.current_task(loop=loop).cancel()

    return result


def make_done_future(loop: typing.Optional[asyncio.AbstractEventLoop]=None) -> "asyncio.Future":
    if loop is None:
        loop = asyncio.get_event_loop()

    done_future = _DONE_FUTURES.get(loop, None)

    if done_future is None:
        done_future = loop.create_future()
        done_future.set_result(None)
        _DONE_FUTURES[loop] = done_future

    return done_future


def shield(coro_or_future: typing.Awaitable[_T], *
           , loop: typing.Optional[asyncio.AbstractEventLoop]=None) -> "asyncio.Future[_T]":
    if loop is None:
        loop = asyncio.get_event_loop()

    future = asyncio.ensure_future(coro_or_future, loop=loop)

    if future.done():
        return future

    def callback(future: "asyncio.Future[_T]") -> None:
        if waiter.done():
            if not future.cancelled():
                future.exception()

            return

        if future.cancelled():
            waiter.cancel()
        else:
            exception = future.exception()

            if exception is None:
                waiter.set_result(future.result())
            else:
                waiter.set_exception(exception)

    future.add_immediate_done_callback(callback)  # type: ignore
    waiter: asyncio.Future[_T] = loop.create_future()
    return waiter


def wait_for1(coro_or_future: typing.Awaitable[_T], timeout: float, *
              , loop: typing.Optional[asyncio.AbstractEventLoop]=None) -> "asyncio.Future[_T]":
    if loop is None:
        loop = asyncio.get_event_loop()

    future = asyncio.ensure_future(coro_or_future, loop=loop)

    if future.done():
        return future

    if timeout <= 0.0:
        future.cancel()
        raise asyncio.TimeoutError()

    def callback1(future: "asyncio.Future[_T]") -> None:
        if waiter.done():
            if not future.cancelled():
                future.exception()

            return

        if future.cancelled():
            waiter.cancel()
        else:
            exception = future.exception()

            if exception is None:
                waiter.set_result(future.result())
            else:
                waiter.set_exception(exception)

    future.add_immediate_done_callback(callback1)  # type: ignore
    waiter: asyncio.Future[_T] = loop.create_future()

    def callback2(_) -> None:
        if not future.done():
            future.remove_immediate_done_callback(callback1)  # type: ignore
            future.cancel()

        timer_handle.cancel()

    waiter.add_immediate_done_callback(callback2)  # type: ignore

    def callback3() -> None:
        if waiter.done():
            return

        waiter.set_exception(asyncio.TimeoutError())

    timer_handle = loop.call_later(timeout, callback3)
    return waiter


async def wait_for2(coro: Coroutine[_T], timeout: float, *
                    , loop: typing.Optional[asyncio.AbstractEventLoop]=None) -> _T:
    if loop is None:
        loop = asyncio.get_event_loop()

    try:
        value = coro.send(None)
    except StopIteration as exception:
        return exception.value

    if value is None:
        return await wait_for1(coro, timeout, loop=loop)

    assert asyncio.isfuture(value), repr(value)
    future: asyncio.Future = value

    def callback1(future: "asyncio.Future") -> None:
        if waiter.done():
            return

        waiter.set_result(None)

    future.add_immediate_done_callback(callback1)  # type: ignore
    waiter: asyncio.Future[None] = loop.create_future()

    def callback2(_) -> None:
        if not future.done():
            future.remove_immediate_done_callback(callback1)  # type: ignore
            future.cancel()

        timer_handle.cancel()

    waiter.add_immediate_done_callback(callback2)  # type: ignore

    def callback3() -> None:
        if waiter.done():
            return

        waiter.set_exception(asyncio.TimeoutError())

    deadline = loop.time() + timeout
    timer_handle = loop.call_at(deadline, callback3)

    try:
        await waiter
    except Exception:
        loop.call_soon(loop.create_task(coro).cancel)
        raise

    return await wait_for1(coro, deadline - loop.time(), loop=loop)


def wait_for_any(coros_and_futures: typing.Iterable[typing.Awaitable]
                 , timeout: typing.Optional[float]=None, *
                 , loop: typing.Optional[asyncio.AbstractEventLoop]=None) -> "asyncio.Future":
    if loop is None:
        loop = asyncio.get_event_loop()

    futures = set(asyncio.ensure_future(coro_or_future, loop=loop)
                  for coro_or_future in coros_and_futures)

    for future in futures:
        if future.done():
            for future2 in futures:
                future2.cancel()

            return future

    waiter: asyncio.Future

    if timeout is None:
        def callback1(future: "asyncio.Future") -> None:
            if waiter.done():
                if not future.cancelled():
                    future.exception()

                return

            if future.cancelled():
                waiter.cancel()
            else:
                exception = future.exception()

                if exception is None:
                    waiter.set_result(future.result())
                else:
                    waiter.set_exception(exception)

        for future in futures:
            future.add_immediate_done_callback(callback1)  # type: ignore

        waiter = loop.create_future()

        def callback2(_) -> None:
            for future in futures:
                if not future.done():
                    future.remove_immediate_done_callback(callback1)  # type: ignore
                    future.cancel()

        waiter.add_immediate_done_callback(callback2)  # type: ignore
    else:
        if timeout <= 0.0:
            for future in futures:
                future.cancel()

            raise asyncio.TimeoutError()

        def callback1(future: "asyncio.Future") -> None:
            if waiter.done():
                if not future.cancelled():
                    future.exception()

                return

            if future.cancelled():
                waiter.cancel()
            else:
                exception = future.exception()

                if exception is None:
                    waiter.set_result(future.result())
                else:
                    waiter.set_exception(exception)

        for future in futures:
            future.add_immediate_done_callback(callback1)  # type: ignore

        waiter = loop.create_future()

        def callback2(_) -> None:
            for future in futures:
                if not future.done():
                    future.remove_immediate_done_callback(callback1)  # type: ignore
                    future.cancel()

            timer_handle.cancel()

        waiter.add_immediate_done_callback(callback2)  # type: ignore

        def callback3() -> None:
            if waiter.done():
                return

            waiter.set_exception(asyncio.TimeoutError)

        timer_handle = loop.call_later(timeout, callback3)

    return waiter


_DONE_FUTURES: typing.Dict[asyncio.AbstractEventLoop, asyncio.Future] = {}
