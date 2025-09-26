from structlog import get_logger
from tortoise import Model, fields
from tortoise.fields.relational import ForeignKeyFieldInstance

from .exceptions import TortoiseSerializerException
from .serializers import ModelSerializer
from .types import MODEL, ContextType, Type

logger = get_logger()


class BackwardFKBulkCreateMixin:
    """
    Mixin for ModelSerializer to efficiently create backward foreign key
    related objects using bulk_create. This is intended to be used with
    serializers that need to create multiple related objects pointing
    back to a parent instance.

    Note:
        Instances created with bulk_create will NOT have their database-
        generated fields (such as primary key `id`) populated after
        creation. Tortoise ORM's bulk_create does not refresh these
        fields from the database. If you need access to these fields,
        you must query the database again for the created objects,
        since their primary keys are not available after bulk_create.

    See also:
        https://github.com/tortoise/tortoise-orm/issues/1992
    """

    async def _create_backward_fks(
        self,
        serializer_model_class: Type[Model],
        instance: MODEL,
        backward_fks: dict[str, list[ModelSerializer]],
        _context: ContextType | None,
        _exclude: set[str],
    ) -> None:
        """
        Creates the backward ForeignKeys for a given instance of
        self.get_model_class using bulk_create for efficiency.

        Only fields not in _exclude are processed.
        """
        logger.debug(
            "Creating backward foreign keys",
            backward_fks=backward_fks,
            exclude=_exclude,
        )
        for field_name, serializers in backward_fks.items():
            if field_name in _exclude:
                continue
            field: fields.ReverseRelation = (
                serializer_model_class._meta.fields_map[field_name]
            )
            backward_key = field.relation_field
            instances: list[MODEL] = []
            for serializer in serializers:
                model_class = serializer.get_model_class()
                kwargs = serializer.model_dump(exclude=_exclude)
                kwargs[backward_key] = instance.id
                # Note that this object won't be saved to the database yet
                obj = self._build_tortoise_instance(
                    model_class, _context, **kwargs
                )
                instances.append(obj)

            field = getattr(instance, field_name)
            field.related_objects = instances
            field._fetched = True
            logger.debug(
                "Populated related objects",
                field_name=field_name,
                instances=instances,
            )
            await model_class.bulk_create(instances)

    def _build_tortoise_instance(
        self,
        model_class: Type[Model],
        _context: ContextType | None,
        **kwargs,
    ) -> MODEL:
        """
        Build and return an instance of the given model_class using the
        provided kwargs. Returned instances are not saved to the database.
        """
        self._raise_for_nested_foreign_keys(model_class, **kwargs)
        return model_class(**kwargs)

    def _raise_for_nested_foreign_keys(
        self, model_class: Type[Model], **kwargs
    ) -> None:
        """
        Raise an exception if any kwargs correspond to nested foreign keys.

        Creating a foreign key requires the related instance's `id`, which is
        not available when using bulk_create. Until this limitation is fixed
        in tortoise-orm, this mixin cannot handle nested serializers with
        foreign keys.
        """
        for field_name in kwargs.keys():
            field = model_class._meta.fields_map[field_name]
            if isinstance(field, ForeignKeyFieldInstance):
                raise TortoiseSerializerException(
                    "BackwardFKBulkCreateMixin does not support nested foreign keys"
                )
