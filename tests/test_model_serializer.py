from tortoise.transactions import in_transaction

from tests.models import Book, BookShelf
from tortoise_serializer import ModelSerializer


async def test_model_creation():
    class ShelfSerializer(ModelSerializer):
        name: str

        class Meta:
            model = BookShelf

    class BookSerializer(ModelSerializer):
        title: str
        shelf: ShelfSerializer

        class Meta:
            model = Book

    serializer = BookSerializer(
        title="LOTR",
        shelf=ShelfSerializer(name="fantastic"),
    )
    # always use a transaction to avoid weird states in db in case of failures.
    async with in_transaction():
        await serializer.create_tortoise_instance()

    assert serializer.title == "LOTR"
    assert serializer.shelf.name == "fantastic"

    assert BookShelf.filter(name="fantastic").exists()
    assert BookShelf.filter(name="LOTR").exists()
    assert await Book.filter(title="LOTR", shelf__name="fantastic").exists()
