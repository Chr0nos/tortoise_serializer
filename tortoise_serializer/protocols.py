from typing import (
    Any,
    Awaitable,
    Callable,
    Generator,
    Protocol,
    Self,
    Sequence,
    Type,
    runtime_checkable,
)

from pydantic.main import IncEx
from tortoise import Model
from tortoise.queryset import QuerySet

from .types import MODEL, ContextType


@runtime_checkable
class SerializerProtocol(Protocol):
    @classmethod
    async def from_tortoise_orm(
        cls,
        instance: Model,
        computed_fields: dict[str, Callable[[Model, Any], Awaitable[Any]]]
        | None = None,
        context: dict[str, Any] | ContextType | None = None,
    ) -> Self: ...

    @classmethod
    async def from_tortoise_instances(
        cls, instances: Sequence[Model], **kwargs
    ) -> list[Self]: ...

    @classmethod
    async def from_queryset(
        cls, queryset: QuerySet, *args, **kwargs
    ) -> list[Self]: ...

    def partial_update_tortoise_instance(
        self, model: Model, **kwargs
    ) -> bool: ...

    async def create_tortoise_instance(
        self,
        model: Type[MODEL],
        _exclude: IncEx | None = None,
        **kwargs,
    ) -> MODEL: ...

    @classmethod
    def get_prefetch_fields_generator(
        cls, prefix: str = ""
    ) -> Generator[str, None, None]: ...

    @classmethod
    def get_prefetch_fields(cls, prefix: str = "") -> list[str]: ...

    def has_been_set(self, field_name: str) -> bool: ...
