from functools import wraps
from typing import Callable, Sequence

from structlog import get_logger
from tortoise.queryset import QuerySet

from tortoise_serializer.serializers import Serializer
from tortoise_serializer.types import MODEL, T

logger = get_logger()


def _should_fetch_field(instance: MODEL, field_name: str) -> bool:
    field = getattr(instance, field_name)
    if isinstance(field, QuerySet):
        return True
    if getattr(field, "_fetched", None) is True:
        return False
    return True


def ensure_fetched_fields(
    field_names: Sequence[str],
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Ensure the given fields are fetched for the given instance.

    This decorator checks if the specified related fields on a Tortoise ORM
    instance have been fetched. If not, it fetches them using
    `fetch_related()` before executing the decorated function.

    Args:
        field_names: A sequence of field names to ensure are fetched.

    Returns:
        A decorator function that wraps resolver methods.

    Example:
    ```python
        @resolver("children")
        @ensure_fetched_fields(["children"])
        async def resolve_children(self, instance: Node, context: ContextType):
            return await NodeSerializer.from_tortoise_instances(
                instance.children, context=context
            )
    ```
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(
            cls: Serializer, instance: MODEL, *args, **kwargs
        ) -> T:
            fields_to_fetch = [
                field_name
                for field_name in field_names
                if _should_fetch_field(instance, field_name)
            ]
            if fields_to_fetch:
                logger.debug(
                    "Fetching related fields",
                    serializer_class=cls,
                    instance=instance,
                    fields=fields_to_fetch,
                )
                await instance.fetch_related(*fields_to_fetch)
            return await func(cls, instance, *args, **kwargs)

        return wrapper

    return decorator
