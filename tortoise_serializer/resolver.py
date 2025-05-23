from typing import Any, Awaitable, Callable


def resolver(field_name: str):
    """Decorator to mark a method as a resolver for one field.
    The decorated method MUST be defined within a Serializer class.

    Args:
        field_name: The name of the field this method resolves.

    Example:
    ```python
        @resolver("full_name")
        def resolve_full_name(cls, instance: Model, context: Any) -> str:
            return f"{instance.first_name} {instance.last_name}"
    ```
    """

    def decorator(
        func: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        if not hasattr(func, "_resolver_fields"):
            func._resolver_fields = []

        func._resolver_fields.append(field_name)

        # Apply classmethod decorator effect
        func = classmethod(func)
        return func

    return decorator
