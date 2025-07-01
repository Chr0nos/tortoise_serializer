import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from enum import Enum
from functools import lru_cache, wraps
from inspect import iscoroutinefunction
from typing import (
    Any,
    Generator,
    Generic,
    Self,
    Sequence,
    Type,
    get_args,
    override,
)

from frozendict import frozendict
from pydantic import BaseModel, ValidationError
from pydantic.main import IncEx
from structlog import get_logger
from tortoise import Model, fields
from tortoise.fields.relational import (
    BackwardFKRelation,
    ForeignKeyFieldInstance,
    ManyToManyFieldInstance,
    ManyToManyRelation,
    _NoneAwaitable,
)
from tortoise.queryset import QuerySet
from typing_extensions import deprecated

from tortoise_serializer.exceptions import (
    TortoiseSerializerClassMethodException,
    TortoiseSerializerException,
)
from tortoise_serializer.types import MODEL, ContextType, T, Unset, UnsetType

logger = get_logger()
log_level = logging.INFO
logging.getLogger(__name__).setLevel(log_level)


@deprecated("use require_condition_or_unset instead")
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


def require_condition_or_unset(
    condition_checker: Callable[[MODEL, ContextType], bool],
) -> Callable[[Callable[..., T]], Callable[..., T | UnsetType]]:
    """Ensure the condition is met for the decorated resolver.
    If the condition is False then this will return UnsetType instead of
    calling the decorated resolver.

    This is a generic version that can be used for any condition, not just permissions.

    :example:
    ```python
    def is_visible(instance: Model, context: ContextType) -> bool:
        return instance.is_public or context.get("user") == instance.owner

    @require_condition_or_unset(is_visible)
    def resolve_content(cls, instance: Post, context) -> str:
        return instance.content

    def is_valid_time(instance: Model, context: ContextType) -> bool:
        return datetime.now() >= instance.publish_time

    @require_condition_or_unset(is_valid_time)
    def resolve_premium_content(cls, instance: Article, context) -> str:
        return instance.premium_content
    ```
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T | UnsetType]:
        @wraps(func)
        def wrapper(
            cls, instance: MODEL, context: ContextType
        ) -> T | UnsetType:
            if not condition_checker(instance, context):
                return Unset
            return func(cls, instance, context)

        @wraps(func)
        async def a_wrapper(
            cls, instance: MODEL, context: ContextType
        ) -> T | UnsetType:
            condition_result = condition_checker(instance, context)
            # the async wrapper support async condition checkers
            if inspect.iscoroutine(condition_result):
                condition_result = await condition_result
            if not condition_result:
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
        by_alias: bool | None = None,
        by_name: bool | None = None,
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
                instance, frozen_context, computed_fields, by_alias, by_name
            ),
            cls._resolve_computed_fields(
                instance, frozen_context, computed_fields
            ),
        )

        fields_values = models_fields | fk_fields | computed_fields_values
        cls._remove_unsets(fields_values)
        try:
            return cls.model_validate(
                fields_values, by_alias=by_alias, by_name=by_name
            )
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
                by_alias=by_alias,
                by_name=by_name,
            )
            raise

    @classmethod
    async def from_tortoise_instances(
        cls,
        instances: Sequence[Model],
        **kwargs,
    ) -> list[Self]:
        """Return a list of Self (Serializer) for the given sequence of
        tortoise instances

        Args:
            instances: Sequence of model instances to serialize
            **kwargs: Other arguments to pass to `from_tortoise_orm`
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
        by_alias: bool | None = None,
        by_name: bool | None = None,
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
                value = await serializer.from_tortoise_instances(
                    relational_instance.related_objects,
                    computed_fields=computed_fields.get(field_name, None),
                    context=context,
                    by_alias=by_alias,
                    by_name=by_name,
                )

            # validating the nested relationship with a from_tortoise_orm call
            # to the nested serializer
            else:
                value = await serializers[0].from_tortoise_orm(
                    relational_instance,
                    context=context,
                    computed_fields=computed_fields.get(field_name, None),
                    by_alias=by_alias,
                    by_name=by_name,
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

        Args:
            field_name: The name of the field to check for nested serializers

        Returns:
            A list of nested Serializer classes found in the field's type hints.
            Returns an empty list if no nested serializers are found or if the field
            doesn't exist.
        """
        if (
            not hasattr(cls, "model_fields")
            or field_name not in cls.model_fields
        ):
            return []

        field_annotation = cls.model_fields[field_name].annotation
        if not field_annotation:
            return []

        # Handle generic types (like list[Serializer])
        type_args = get_args(field_annotation)
        if type_args:
            return [
                arg
                for arg in type_args
                if isinstance(arg, type) and issubclass(arg, Serializer)
            ]

        # Handle direct Serializer type
        if isinstance(field_annotation, type) and issubclass(
            field_annotation, Serializer
        ):
            return [field_annotation]

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
        """Collect all resolvers defined in the class, both method-based and decorator-based."""
        fields = {}

        # Collect method-based resolvers (starting with resolve_)
        for method in dir(cls):
            if method.startswith("resolve_") and callable(
                getattr(cls, method)
            ):
                fields[method.removeprefix("resolve_")] = getattr(cls, method)

        # Collect decorator-based resolvers
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name)
            if callable(attr) and hasattr(attr, "_resolver_fields"):
                for field_name in attr._resolver_fields:
                    fields[field_name] = attr

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
        *,
        _exclude: IncEx | None = None,
        _context: ContextType | None = None,
        **kwargs,
    ) -> MODEL:
        model_data = self.model_dump(exclude=_exclude)
        return await model.create(**(model_data | kwargs))

    def has_been_set(self, field_name: str) -> bool:
        """Return True if `field_name` has been set, otherwise False"""
        return field_name in self.model_fields_set

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

            # check if the serializer need to be filterd out
            if not cls._filter_nested_serializer(
                field_name, field_serializers
            ):
                continue

            # Field is a nested serializer
            yield prefix + field_name

            # Recursively get prefetch fields from nested serializers
            for nested_serializer in field_serializers:
                yield from nested_serializer.get_prefetch_fields(
                    prefix + field_name
                )

    @classmethod
    def _filter_nested_serializer(
        cls, field_name: str, serializers: Sequence["Serializer"]
    ) -> bool:
        """Override to filter out serializers from the prefetch fields"""
        return True

    @classmethod
    def get_prefetch_fields(cls, prefix: str = "") -> list[str]:
        """
        Generate prefetch fields for all nested serializers.
        The concept is to pass the output of that function to
        `Model.fetch_related()` or `QuerySet[Model].prefech_related()`
        """
        return list(cls.get_prefetch_fields_generator(prefix))


class ModelSerializer(Serializer, Generic[MODEL]):
    @classmethod
    @lru_cache()
    def get_model_class(cls) -> Type[MODEL]:
        """
        Retrieve the model class associated with the current ModelSerializer
        subclass.

        This method iterates through the class hierarchy to find the first
        class that inherits from tortoise.models.models.BaseModel and has a
        "__pydantic_generic_metadata__" attribute.
        It then extracts the model class from the "args" of the
        "__pydantic_generic_metadata__" attribute.

        If no such class is found, a TortoiseSerializerException is raised.

        Returns:
            Type[MODEL]: The model class associated with the current
                         ModelSerializer subclass.
        """
        for parent_class in cls.__mro__:
            if issubclass(parent_class, BaseModel) and hasattr(
                parent_class, "__pydantic_generic_metadata__"
            ):
                parent_meta = parent_class.__pydantic_generic_metadata__
                origin = parent_meta.get("origin", None)
                if origin:
                    args = parent_meta.get("args", None)
                    return args[0]

        raise TortoiseSerializerException(
            f"Bad configuration for ModelSerializer {cls}"
        )

    @override
    async def create_tortoise_instance(
        self, *, _exclude=None, _context: ContextType | None = None, **kwargs
    ) -> MODEL:
        """Creates the tortoise instance of this serializer and it's nested relations.
        it's highly recommended to use this inside a a `transaction` context

        `_context` will be passed to any nested ModelSerializer as it is.
        """
        creation_kwargs = {}
        exclude = set()
        many_to_manys: dict[str, list[Model]] = {}
        backward_fks: dict[str, list[ModelSerializer]] = {}
        model_class = self.get_model_class()

        # as tempting as it might be, don't try to put that into a concurent
        # task like asyncio.gather: here we are probably in a transaction
        # context and tortoise will complain if we have 2 concurent operations
        for field_name, serializers in self._get_nested_serializers().items():
            serialized_value = getattr(self, field_name)

            # allow nones to be passed if the model allow them
            if serialized_value is None:
                continue

            serializer_class = serializers[0]
            if not issubclass(serializer_class, ModelSerializer):
                raise TortoiseSerializerException(
                    f"Bad configuration for field {field_name}:"
                    " this must inherit from ModelSerializer"
                )
            relation = model_class._meta.fields_map[field_name]
            if isinstance(relation, ManyToManyFieldInstance):
                for serializer in [
                    serializer_class.model_validate(item)
                    for item in serialized_value
                ]:
                    instance = await serializer.create_tortoise_instance(
                        **kwargs.get(field_name, {}),
                        _context=_context,
                    )
                    many_to_manys[field_name] = many_to_manys.get(
                        field_name, []
                    ) + [instance]
                    exclude.add(field_name)

            # backward foreign keys
            elif isinstance(relation, BackwardFKRelation):
                for serializer in [
                    serializer_class.model_validate(item)
                    for item in serialized_value
                ]:
                    backward_fks[field_name] = backward_fks.get(
                        field_name, []
                    ) + [serializer]
                exclude.add(field_name)

            elif isinstance(relation, ForeignKeyFieldInstance):
                serializer = serializer_class.model_validate(serialized_value)
                relation_instance = await serializer.create_tortoise_instance(
                    **kwargs.get(field_name, {}),
                    _context=_context,
                )

                # assign both `field_name_id` and `field_name` to have them
                # in the instance available (for external use) and avoid to
                # have to re-fetch them
                creation_kwargs[field_name + "_id"] = relation_instance.id
                creation_kwargs[field_name] = relation_instance
                exclude.add(field_name)

        merged_kwargs = creation_kwargs | kwargs
        if _exclude:
            exclude = exclude | set(_exclude)
        instance = await super().create_tortoise_instance(
            model_class,
            _exclude=exclude,
            _context=_context,
            **merged_kwargs,
        )
        for field_name, instances in many_to_manys.items():
            await getattr(instance, field_name).add(*instances)

        await self._create_backward_fks(
            model_class, instance, backward_fks, _context, _exclude or set()
        )
        return instance

    async def _create_backward_fks(
        self,
        serializer_model_class: Type[Model],
        instance: MODEL,
        backward_fks: dict[str, list[Self]],
        _context: ContextType | None,
        _exclude: set[str],
    ) -> None:
        """Creates the backward ForeignKeys for a given instance of self.get_model_class"""

        for field_name, serializers in backward_fks.items():
            if field_name in _exclude:
                continue
            field: fields.ReverseRelation = (
                serializer_model_class._meta.fields_map[field_name]
            )
            backward_key = field.relation_field
            for serializer in serializers:
                await serializer.create_tortoise_instance(
                    _context=_context,
                    **{backward_key: instance.id},
                )

    @classmethod
    @lru_cache()
    def get_model_fields(
        cls, prefix: str | None = None, max_depth: int = 3
    ) -> set[str]:
        """Return the set of fields that are common to the model and this serializer,
        including nested serializer fields up to the specified max_depth.

        Args:
            prefix (str | None): A string prefix to prepend to nested fields.
            max_depth (int): Maximum depth for nested field exploration.

        Returns:
            Set[str]: A set of field names including nested fields, with prefixes applied.
        """
        model_fields: set[str] = set(cls.get_model_class()._meta.fields)
        serializer_fields: set[str] = set(cls.model_fields.keys())
        common_fields = model_fields.intersection(serializer_fields)

        # Prepare prefix if not provided
        prefix = prefix or ""

        if max_depth > 0:
            for field_name in common_fields.copy():
                # Get nested serializers for this field
                serializers = cls._get_nested_serializers_for_field(field_name)
                if not serializers:
                    continue

                serializer_class = serializers[0]
                if not issubclass(serializer_class, ModelSerializer):
                    raise TortoiseSerializerException(
                        f"Bad configuration for field {field_name}:"
                        f" this must inherit from ModelSerializer ({serializer_class})"
                    )

                # Recursive call to get nested fields
                nested_fields = serializer_class.get_model_fields(
                    prefix=f"{prefix}{field_name}__",
                    max_depth=max_depth - 1,
                )
                # Merge nested fields into the common fields
                common_fields.update(nested_fields)

        # Add prefix to all fields
        return {f"{prefix}{field}" for field in common_fields}

    @classmethod
    def _filter_nested_serializer(
        cls, field_name: str, serializers: Sequence["Serializer"]
    ) -> bool:
        # on ModelSerialzer we can check if the nested serializer exists
        # in the model so we avoid to return wrong fields in the prefetch
        # requests
        return field_name in cls.get_model_fields()

    @classmethod
    def get_only_fetch_fields(cls, path: str | None = None) -> list[str]:
        """
        Get the list of fields that should be fetched from the database.

        This method recursively traverses the serializer's fields and nested
        serializers to build a list of database fields that need to be fetched.
        It handles both direct model fields and nested relationships.

        Args:
            path (str | None): Optional path prefix for nested fields. Used
                internally for recursion.

        Returns:
            list[str]: List of field paths that should be fetched from the
                database.

        Raises:
            TortoiseSerializerException: If a nested serializer is not properly
                configured to inherit from ModelSerializer.
        """
        fields = []
        model = cls.get_model_class()
        for field_name in cls.model_fields.keys():
            # Skip computed fields that don't exist in the model
            if field_name not in model._meta.fields_map.keys():
                continue

            if cls._is_nested_serializer(field_name):
                args = get_args(cls.__annotations__[field_name])
                serializers = list(
                    [
                        arg
                        for arg in args
                        if (
                            isinstance(arg, type)
                            and issubclass(arg, ModelSerializer)
                        )
                    ]
                )
                serializer = serializers[0]
                nested_fields = serializer.get_only_fetch_fields(
                    path=f"{path or ''}{field_name}__"
                )
                fields.extend(nested_fields)
            else:
                fields.append(f"{path or ''}{field_name}")

        return fields

    @classmethod
    async def from_queryset(
        cls,
        queryset: QuerySet,
        *args,
        prefetch: bool = False,
        select_only: bool = False,
        **kwargs,
    ) -> list[Self]:
        """
        Return a list of Self (ModelSerializer) from the given queryset.
        All instances are fetched in concurrency using asyncio.

        Parameters:
        - `queryset`: The QuerySet instance to serialize from
        - `prefetch`: If True, prefetch the related fields
        - `select_only`: If True, only fetch the fields that are needed to serialize the model
                         Note that only the fields defined in the serializer
                         and its nested serializers are considered, be careful
                         with the resolvers needs
        any *args, *kwargs will be passed to `Serializer.from_queryset` method."""
        assert not (
            prefetch and select_only
        ), "prefetch and select_only cannot be true at the same time"
        if prefetch:
            queryset = queryset.prefetch_related(*cls.get_prefetch_fields())
        elif select_only:
            queryset = queryset.only(*cls.get_only_fetch_fields())

        return await super().from_queryset(queryset, *args, **kwargs)
