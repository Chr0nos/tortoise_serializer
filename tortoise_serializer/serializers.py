import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from enum import Enum
from functools import lru_cache, wraps
from inspect import iscoroutinefunction
from typing import Any, Generator, Self, Sequence, Type, TypeVar, get_args

from frozendict import frozendict
from pydantic import BaseModel, ValidationError
from pydantic.main import IncEx
from structlog import get_logger
from tortoise import Model, fields
from tortoise.fields.relational import ManyToManyRelation, _NoneAwaitable
from tortoise.queryset import QuerySet

from .exceptions import TortoiseSerializerClassMethodException

MODEL = TypeVar("MODEL", bound=Model)
T = TypeVar("T")
ContextType = frozendict[str, Any]

logger = get_logger()
log_level = logging.INFO
logging.getLogger(__name__).setLevel(log_level)


class Unset:
    """
    Describe an unset field. This field will be omitted from the Pydantic model validation when
    instantiating the model.

    They are intented to be used in resolvers for `Serializer` to not set anything
    and be able to use `exclude_unset=True`
    """


UnsetType = Type[Unset]


def require_permission_or_unset(
    permission_checker: Callable[[MODEL, ContextType], bool],
):
    """Ensure the context contains the required permissions for the decorated resolver
    if the permission is False then this will return UnsetType instead of
    calling the decorated resolver

    :example:
    ```python
    def is_owner(instance: Model, context: ContextType) -> bool:
        return instance.created_by == context.get("user", None)

    @require_permission_or_unset(is_owner)
    def resolve_secret_value(cls, instance: User, context) -> str:
        return "It's secret!"
    ```
    """

    def decorator(func: Callable[..., T]):
        @wraps(func)
        def wrapper(
            cls, instance: MODEL, context: ContextType
        ) -> T | UnsetType:
            if not permission_checker(instance, context):
                return Unset
            return func(cls, instance, context)

        @wraps(func)
        async def a_wrapper(
            cls, instance: MODEL, context: ContextType
        ) -> T | UnsetType:
            if not permission_checker(instance, context):
                return Unset
            return await func(cls, instance, context)

        return wrapper if not iscoroutinefunction(func) else a_wrapper

    return decorator


class Serializer(BaseModel):
    """
    Serializer of tortoise orm models

    Resolvers:
    they are function can be async or not, with the name starting by resolve_*
    if a field is in the serializer and not in the `instance` then the serializer
    will look for a resolver before complaining

    resolvers overrides `computed_fields` with same names since they are technically
    computed fields

    priority order:
    computed_fields > foreign keys > model_fields
    """

    @classmethod
    async def from_tortoise_orm(
        cls,
        instance: Model,
        computed_fields: dict[str, Callable[[Model, Any], Awaitable[Any]]]
        | None = None,
        context: dict[str, Any] | ContextType | None = None,
    ) -> Self:
        if computed_fields is None:
            computed_fields = {}
        computed_fields |= cls._collect_resolvers()

        # using a frozendict to allow caching when context is involved
        # also prevent missuses of the context: it must be considered as
        # read only
        frozen_context = frozendict(context or {})

        # fetch related fields before calling concurent resolvers
        # so all of them are guaranteed to have the model populated properly
        await cls._fetch_related_fields(instance)

        (
            models_fields,
            fk_fields,
            computed_fields_values,
        ) = await asyncio.gather(
            cls._resolve_model_fields(instance),
            cls._resolve_foreignkeys(
                instance, frozen_context, computed_fields
            ),
            cls._resolve_computed_fields(
                instance, frozen_context, computed_fields
            ),
        )

        fields_values = models_fields | fk_fields | computed_fields_values
        cls._remove_unsets(fields_values)
        try:
            return cls.model_validate(fields_values)
        except ValidationError:
            logger.error(
                "Failed to validate with model",
                model=cls.__name__,
                data=fields_values,
                instance=instance,
                context=frozen_context,
                models_fields=models_fields,
                fk_fields=fk_fields,
                computed_fields_values=computed_fields_values,
                computed_fields=computed_fields,
            )
            raise

    @classmethod
    async def from_tortoise_instances(
        cls, instances: Sequence[Model], **kwargs
    ) -> list[Self]:
        """Return a list of Self (Serializer) for the given sequence of
        tortoise instances
        """
        return await asyncio.gather(
            *[
                cls.from_tortoise_orm(instance, **kwargs)
                for instance in instances
            ]
        )

    @classmethod
    async def _fetch_related_fields(cls, instance: Model) -> None:
        fetch_related_fields = cls._get_non_fetched_related_field_names(
            instance
        )
        if not fetch_related_fields:
            return

        logger.debug(
            "Fetching related fields, consider using prefetch_related",
            serializer=cls,
            instance=instance,
            fields=fetch_related_fields,
        )

        # Fetch all the related fields
        await instance.fetch_related(*fetch_related_fields)

    @staticmethod
    def _remove_unsets(data: dict[str, Any]) -> None:
        """Remove any Unset items from the given dictionary"""
        fields_to_remove = [
            field_name
            for field_name, field_value in data.items()
            if field_value is Unset
        ]
        for field in fields_to_remove:
            data.pop(field, None)

    @classmethod
    async def _resolve_model_fields(cls, instance: Model) -> dict[str, Any]:
        data = {}
        for field_name in cls.model_fields.keys():
            if hasattr(instance, field_name):
                field_value = getattr(instance, field_name)

                # ignore this is a job for _resolve_foreignkeys
                if isinstance(field_value, Model):
                    continue
                # ignore, this is a job for _resolve_computed_fields
                if hasattr(cls, f"resolve_{field_name}"):
                    continue

                # unpack enum values
                if isinstance(field_value, Enum):
                    field_value = field_value.value

                data[field_name] = field_value
        return data

    @classmethod
    def _get_non_fetched_related_field_names(
        cls, instance: Model
    ) -> list[str]:
        """Returns the list of all fields that need to be fetched
        to represent the current `cls` instance
        note this won't fetch nested serialziers field names
        """
        fetch_related_fields = []
        for field_name in cls.model_fields:
            # if a resolver already exists we use it instead of trying to
            # resolve it as a foreign key
            if hasattr(cls, f"resolve_{field_name}"):
                continue

            relational_instance = getattr(instance, field_name, None)

            # if the instance has been already fetched we don't add the field
            # to the list
            if isinstance(relational_instance, Model):
                continue

            # if the item is None we output the value as None to see if the
            # serializer can allow it
            if relational_instance is None:
                continue
            elif isinstance(relational_instance, _NoneAwaitable):
                continue
            elif isinstance(relational_instance, ManyToManyRelation):
                if not relational_instance._fetched:
                    fetch_related_fields.append(field_name)
            elif isinstance(relational_instance, fields.ReverseRelation):
                if not relational_instance._fetched:
                    fetch_related_fields.append(field_name)
            else:
                if isinstance(relational_instance, QuerySet):
                    fetch_related_fields.append(field_name)
        return fetch_related_fields

    @classmethod
    async def _resolve_foreignkeys(
        cls,
        instance: Model,
        context: ContextType,
        computed_fields: dict[str, Callable[[Model, Any], Awaitable[Any]]],
    ) -> dict[str, Any]:
        data = {}
        for field_name, serializers in cls._get_nested_serializers().items():
            # resolvers have higher priority
            if hasattr(cls, f"resolve_{field_name}"):
                continue

            # for now: we only support one nested serializer
            if not len(serializers) == 1:
                raise ValueError(
                    "Cannot use more than one serialzier for each nested relation"
                )
            (serializer,) = serializers

            relational_instance = getattr(instance, field_name, None)

            # if the item is None we output the value as None to see if the
            # serializer can allow it
            if relational_instance is None or isinstance(
                relational_instance, _NoneAwaitable
            ):
                value = None
            # handling many to many relationships
            elif isinstance(relational_instance, ManyToManyRelation):
                value = await serializer.from_tortoise_instances(
                    relational_instance.related_objects, context=context
                )

            # handle reverse relations
            elif isinstance(relational_instance, fields.ReverseRelation):
                tasks = [
                    serializer.from_tortoise_orm(
                        instance,
                        context=context,
                        computed_fields=computed_fields.get(field_name, None),
                    )
                    for instance in relational_instance.related_objects
                ]
                value = await asyncio.gather(*tasks)

            # validating the nested relationship with a from_tortoise_orm call
            # to the nested serializer
            else:
                value = await serializers[0].from_tortoise_orm(
                    relational_instance,
                    context=context,
                    computed_fields=computed_fields.get(field_name, None),
                )
            data[field_name] = value
        return data

    @classmethod
    async def _resolve_computed_fields(
        cls,
        instance: Model,
        context: ContextType,
        computed_fields: dict[str, Callable[[Model, Any], Awaitable[Any]]]
        | None = None,
    ) -> dict[str, Any]:
        """Resolve all values for computed fields
        note that async function will be called in an asyncio.TaskGroup
        """
        if not computed_fields:
            return {}
        data = {}
        async with asyncio.TaskGroup() as tg:
            for field_name, field_resolver in computed_fields.items():
                if not inspect.ismethod(field_resolver):
                    raise TortoiseSerializerClassMethodException(
                        cls, field_name
                    )

                # ignore any nested serializers, it will be a job for the
                # foreign key resolver
                if isinstance(
                    field_resolver, dict
                ) and cls._is_nested_serializer(field_name):
                    continue

                # add tasks to the taskgroup
                elif iscoroutinefunction(field_resolver):
                    data[field_name] = tg.create_task(
                        field_resolver(instance, context)
                    )

                # get the values output values of sync resolvers
                elif callable(field_resolver):
                    data[field_name] = field_resolver(instance, context)

                # copy raw values
                else:
                    data[field_name] = field_resolver

        # we unpack the Task results for finished tasks
        for field_name, field_value in data.items():
            if isinstance(field_value, asyncio.Task):
                data[field_name] = field_value.result()

        return data

    @classmethod
    def _is_nested_serializer(cls, field_name: str) -> bool:
        """
        Check if the given field name corresponds to a nested serializer.
        """
        # Ensure the field exists in the annotations
        if field_name not in cls.__annotations__:
            return False

        # Get the type annotation for the field
        field_type = cls.__annotations__[field_name]

        # Check if the field type corresponds to a nested serializer
        args = get_args(field_type)
        if args:
            return any(
                isinstance(arg, type) and issubclass(arg, Serializer)
                for arg in args
            )
        return isinstance(field_type, type) and issubclass(
            field_type, Serializer
        )

    @classmethod
    def _get_nested_serializers_for_field(
        cls, field_name: str
    ) -> list["Serializer"]:
        """
        Get a list of nested serializers for the given field, if any.
        """
        try:
            field_annotation = cls.model_fields[field_name].annotation
            args = get_args(field_annotation)

            # Return all nested serializers from the field's type hints
            return (
                [
                    arg
                    for arg in args
                    if isinstance(arg, type) and issubclass(arg, Serializer)
                ]
                if args
                else (
                    [field_annotation]
                    if isinstance(field_annotation, type)
                    and issubclass(field_annotation, Serializer)
                    else []
                )
            )
        except (KeyError, TypeError):
            return []

    @classmethod
    @lru_cache()
    def _get_nested_serializers(cls) -> dict[str, list["Serializer"]]:
        serializers = {}
        for field_name in cls.model_fields.keys():
            field_serializers = cls._get_nested_serializers_for_field(
                field_name
            )
            if field_serializers:
                serializers[field_name] = field_serializers
            elif cls._is_nested_serializer(field_name):
                serializers[field_name] = [
                    cls.model_fields[field_name].annotation
                ]
        return serializers

    @classmethod
    async def from_queryset(
        cls, queryset: QuerySet, *args, **kwargs
    ) -> list[Self]:
        """
        Return a list of Self (Serializer) from the given queryset
        all instances are fetched in concurency using asyncio

        Parameters:
        - `queryset`: The QuerySet instance to serialize from
        any *args, *kwargs will be passed to `from_tortoise_orm` method.
        """

        tasks = [
            cls.from_tortoise_orm(instance, *args, **kwargs)
            async for instance in queryset
        ]
        return await asyncio.gather(*tasks)

    @classmethod
    def _collect_resolvers(
        cls,
    ) -> dict[str, Callable[[Model, Any], Awaitable[Any]]]:
        fields = {}
        for method in dir(cls):
            if method.startswith("resolve_") and callable(
                getattr(cls, method)
            ):
                fields[method.removeprefix("resolve_")] = getattr(cls, method)
        return fields

    def partial_update_tortoise_instance(self, model: Model, **kwargs) -> bool:
        """Update instance of `model` with the current serializer instance fields
        return `True` if the instance had been changed, `False` otherwise
        """
        updater = self.model_dump(exclude_unset=True, **kwargs)
        if not updater:
            logger.debug(
                "No fields to update", model=model, fields_to_update=updater
            )
            return False
        values_changed: bool = False
        for field, value in updater.items():
            if hasattr(model, field):
                if getattr(model, field) == value:
                    logger.debug(
                        "Value remains the same", model=model, field_name=field
                    )
                else:
                    setattr(model, field, value)
                    logger.debug(
                        "Updated Field", model=model, field_name=field
                    )
                    values_changed = True
        return values_changed

    async def create_tortoise_instance(
        self,
        model: Type[MODEL],
        _exclude: IncEx | None = None,
        **kwargs,
    ) -> MODEL:
        model_data = self.model_dump(exclude=_exclude)
        return await model.create(**(model_data | kwargs))

    def has_been_set(self, field_name: str) -> bool:
        """Return True if `field_name` has been set, otherwise False"""
        data = self.model_dump(include={field_name}, exclude_unset=True)
        return field_name in data

    @classmethod
    def get_prefetch_fields_generator(
        cls, prefix: str = ""
    ) -> Generator[str, None, None]:
        """
        Generate prefetch fields for all nested serializers.
        """
        if prefix:
            prefix = prefix + "__"

        for field_name in cls.model_fields.keys():
            field_serializers = cls._get_nested_serializers_for_field(
                field_name
            )

            # If no nested serializers are found, skip this field
            if not field_serializers:
                continue

            # Field is a nested serializer
            yield prefix + field_name

            # Recursively get prefetch fields from nested serializers
            for nested_serializer in field_serializers:
                yield from nested_serializer.get_prefetch_fields(
                    prefix + field_name
                )

    @classmethod
    def get_prefetch_fields(cls, prefix: str = "") -> list[str]:
        """
        Generate prefetch fields for all nested serializers.
        The concept is to pass the output of that function to
        `Model.fetch_related()` or `QuerySet[Model].prefech_related()`
        """
        return list(cls.get_prefetch_fields_generator(prefix))
