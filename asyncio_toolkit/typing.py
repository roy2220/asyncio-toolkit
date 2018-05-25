import typing


_T = typing.TypeVar("_T")


BytesLike = typing.Union[bytes, bytearray]
Coroutine = typing.Coroutine[typing.Any, typing.Any, _T]
