from tortoise.transactions import in_transaction

from tests.models import Book, BookShelf, Person
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


async def test_model_creation_without_relation():
    class ShelfSerializer(ModelSerializer):
        name: str

        class Meta:
            model = BookShelf

    class BookSerializer(ModelSerializer):
        title: str
        shelf: ShelfSerializer | None = None

        class Meta:
            model = Book

    serializer = BookSerializer(title="test")
    book = await serializer.create_tortoise_instance()
    assert book.id
    assert not book.shelf


async def test_model_m2m_creation():
    class BookSerializer(ModelSerializer):
        title: str
        price: float | None = None
        page_count: int | None = None

        class Meta:
            model = Book

    class PersonSerializer(ModelSerializer):
        name: str
        borrows: list[BookSerializer]

        class Meta:
            model = Person

    serializer = PersonSerializer(
        name="Louise",
        borrows=[
            BookSerializer(title="Harry Potter", price=30, page_count=300),
            BookSerializer(
                title="To Kill a Mockingbird", price=20, page_count=288
            ),
            BookSerializer(title="1984", price=14, page_count=328),
        ],
    )
    async with in_transaction():
        person = await serializer.create_tortoise_instance()

    assert await person.borrows.all().count() == 3


async def test_model_backward_fk_creation():
    class BookSerializer(ModelSerializer):
        title: str
        price: float | None = None
        page_count: int | None = None

        class Meta:
            model = Book

    class ShelfSerializer(ModelSerializer):
        name: str
        books: list[BookSerializer]

        class Meta:
            model = BookShelf

    serializer = ShelfSerializer(
        name="fantastic",
        books=[
            BookSerializer(title="Harry Potter", price=30, page_count=300),
            BookSerializer(title="LOTR", price=40, pages_count=200),
        ],
    )
    shelf = await serializer.create_tortoise_instance()
    assert await shelf.books.all().count() == 2
    assert await Book.filter(title="LOTR", shelf__name="fantastic").exists()
    assert Book.filter(title="LOTR", shelf__name="fantastic").exists()
