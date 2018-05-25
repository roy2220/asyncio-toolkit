import asyncio
import logging
import math
import random
import time
import typing


_T = typing.TypeVar("_T")


class DelayPool(typing.Generic[_T]):
    _used_item_count = 0

    def __init__(self, items: typing.Iterable[_T], item_reuse_factor: float
                 , total_max_delay_duration: float, loop: asyncio.AbstractEventLoop
                 , logger: logging.Logger) -> None:
        self._items = list(set(items))
        assert len(self._items) >= 1
        self._number_of_items: int
        self._max_delay_duration: float
        self.reset(item_reuse_factor, total_max_delay_duration)
        self._loop = loop
        self._logger = logger

    def reset(self, item_reuse_factor: typing.Optional[float]
              , total_max_delay_duration: typing.Optional[float]) -> None:
        assert item_reuse_factor is None or item_reuse_factor > 0, repr(item_reuse_factor)
        assert total_max_delay_duration is None or total_max_delay_duration > 0\
               , repr(total_max_delay_duration)

        if len(self._items) >= 1:
            last_item_index = self._used_item_count % len(self._items) - 1

            if last_item_index < 0:
                random.shuffle(self._items)
            else:
                last_item = self._items.pop(last_item_index)
                random.shuffle(self._items)
                self._items.append(last_item)

        self._used_item_count = 0

        if item_reuse_factor is None:
            if total_max_delay_duration is not None:
                self._max_delay_duration = total_max_delay_duration / self._number_of_items
        else:
            if total_max_delay_duration is None:
                total_max_delay_duration = self._number_of_items * self._max_delay_duration

            self._number_of_items = math.ceil(item_reuse_factor * len(self._items))
            self._max_delay_duration = total_max_delay_duration / self._number_of_items

    async def allocate_item(self) -> typing.Optional[_T]:
        if self._used_item_count == self._number_of_items:
            return None

        now = time.monotonic()

        if self._used_item_count == 0:
            self._next_item_allocable_time = now
            delay_duration = 0.0
        else:
            delay_duration = self._next_item_allocable_time - now

        item = self._items[self._used_item_count % len(self._items)]
        self._used_item_count += 1
        self._next_item_allocable_time += self._max_delay_duration
        self._logger.info("delay pool item allocation: delay_pool_item={!r} delay_duration={!r}"
                          .format(item, delay_duration))

        if delay_duration >= 0.001:
            await asyncio.sleep(delay_duration, loop=self._loop)

        return item

    def when_next_item_allocable(self) -> float:
        return self._next_item_allocable_time
