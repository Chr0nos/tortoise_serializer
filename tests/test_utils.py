from tests.models import Node
from tortoise_serializer import (
    ContextType,
    Serializer,
    ensure_fetched_fields,
    resolver,
)


async def test_ensure_fetched_fields():
    class NodeSerializer(Serializer):
        id: int
        name: str
        parent_id: int | None
        children: list["NodeSerializer"]

        @resolver("children")
        @ensure_fetched_fields(["children"])
        async def resolve_children(self, instance: Node, context: ContextType):
            return await NodeSerializer.from_tortoise_instances(
                instance.children,
                context=context,
            )

    node = await Node.create(name="root")
    await Node.create(name="child", parent=node)
    serializer = await NodeSerializer.from_tortoise_orm(node)
    assert serializer.children is not None
    assert len(serializer.children) == 1
    assert serializer.children[0].name == "child"
