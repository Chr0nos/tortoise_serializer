from typing import Any, Type, TypeVar

from frozendict import frozendict
from tortoise import Model

MODEL = TypeVar("MODEL", bound=Model)
T = TypeVar("T")
ContextType = frozendict[str, Any]


class Unset:
    """
    Describe an unset field. This field will be omitted from the Pydantic model validation when
    instantiating the model.

    They are intented to be used in resolvers for `Serializer` to not set anything
    and be able to use `exclude_unset=True`
    """


UnsetType = Type[Unset]
