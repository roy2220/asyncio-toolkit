import asyncio
import functools
import inspect
import itertools
import sys
import typing

from .typing import Coroutine


_PYVER = sys.hexversion >> 16

_T = typing.TypeVar("_T")


class Future(asyncio.Future, typing.Generic[_T]):
    _shieldee: typing.Optional["asyncio.Future[_T]"] = None

    def __init__(self, *, loop=None) -> None:
        super().__init__(loop=loop)
        self._immediate_done_callbacks: typing.List[typing.Callable[["asyncio.Future[_T]"]
                                                                    , None]] = []

    def add_immediate_done_callback(self, immediate_done_callback: typing.Callable[\
        ["asyncio.Future[_T]"], None]) -> None:
        self._immediate_done_callbacks.append(immediate_done_callback)

    def remove_immediate_done_callback(self, immediate_done_callback: typing.Callable\
        [["asyncio.Future[_T]"], None]) -> None:
        self._immediate_done_callbacks.remove(immediate_done_callback)

    if _PYVER >= 0x306:
        def _schedule_callbacks(self):
            if len(self._immediate_done_callbacks) >= 1:
                for immediate_done_callback in self._immediate_done_callbacks:
                    immediate_done_callback(self)

                self._immediate_done_callbacks.clear()

            super()._schedule_callbacks()
    else:
        assert False


async def delay_cancellation(coro_or_future: typing.Awaitable[_T], *
                             , loop: typing.Optional[asyncio.AbstractEventLoop]=None) -> _T:
    if loop is None:
        loop = asyncio.get_event_loop()

    future = asyncio.ensure_future(coro_or_future, loop=loop)
    was_cancelled = False

    while True:
        try:
            result = await shield(future)
        except asyncio.CancelledError:
            if future.cancelled():
                raise

            was_cancelled = True
        else:
            break

    if was_cancelled:
        raise asyncio.CancelledError()

    return result


async def atomize_cancellation(coro: Coroutine[_T], *
                               , loop: typing.Optional[asyncio.AbstractEventLoop]=None) -> _T:
    if loop is None:
        loop = asyncio.get_event_loop()

    task = loop.create_task(coro)
    was_cancelled = False

    while True:
        try:
            result = await shield(task)
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
        raise asyncio.CancelledError()

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

    if isinstance(coro_or_future, Future) and coro_or_future._shieldee is not None:
        future = coro_or_future._shieldee
    else:
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

    if isinstance(future, Future):
        future.add_immediate_done_callback(callback)
    else:
        future.add_done_callback(callback)

    waiter: Future[_T] = Future(loop=loop)
    waiter._shieldee = future
    return waiter


def wait_for(coro_or_future: typing.Awaitable[_T], timeout: float, *
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

    if isinstance(future, Future):
        future.add_immediate_done_callback(callback1)
    else:
        future.add_done_callback(callback1)

    def callback2(_) -> None:
        if not future.done():
            if isinstance(future, Future):
                future.remove_immediate_done_callback(callback1)
            else:
                future.remove_done_callback(callback1)

            future.cancel()

        timer_handle.cancel()

    waiter: Future[_T] = Future(loop=loop)
    waiter.add_immediate_done_callback(callback2)

    def callback3() -> None:
        if waiter.done():
            return

        waiter.set_exception(asyncio.TimeoutError())

    timer_handle = loop.call_later(timeout, callback3)
    return waiter


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

    waiter: Future

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
            if isinstance(future, Future):
                future.add_immediate_done_callback(callback1)
            else:
                future.add_done_callback(callback1)

        def callback2(_) -> None:
            for future in futures:
                if not future.done():
                    if isinstance(future, Future):
                        future.remove_immediate_done_callback(callback1)
                    else:
                        future.remove_done_callback(callback1)

                    future.cancel()

        waiter = Future(loop=loop)
        waiter.add_immediate_done_callback(callback2)
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
            if isinstance(future, Future):
                future.add_immediate_done_callback(callback1)
            else:
                future.add_done_callback(callback1)

        def callback2(_) -> None:
            for future in futures:
                if not future.done():
                    if isinstance(future, Future):
                        future.remove_immediate_done_callback(callback1)
                    else:
                        future.remove_done_callback(callback1)

                future.cancel()

            timer_handle.cancel()

        waiter = Future(loop=loop)
        waiter.add_immediate_done_callback(callback2)

        def callback3() -> None:
            if waiter.done():
                return

            waiter.set_exception(asyncio.TimeoutError)

        timer_handle = loop.call_later(timeout, callback3)

    return waiter


_DONE_FUTURES: typing.Dict[asyncio.AbstractEventLoop, asyncio.Future] = {}
