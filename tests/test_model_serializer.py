from tortoise.transactions import in_transaction

from tests.models import Book, BookShelf, Location, Person
from tortoise_serializer import ModelSerializer


async def test_model_creation():
    class ShelfSerializer(ModelSerializer[BookShelf]):
        name: str

    class BookSerializer(ModelSerializer[Book]):
        title: str
        shelf: ShelfSerializer

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
    class ShelfSerializer(ModelSerializer[BookShelf]):
        name: str

    class BookSerializer(ModelSerializer[Book]):
        title: str
        shelf: ShelfSerializer | None = None

    serializer = BookSerializer(title="test")
    book = await serializer.create_tortoise_instance()
    assert book.id
    assert not book.shelf


async def test_model_m2m_creation():
    class BookSerializer(ModelSerializer[Book]):
        title: str
        price: float | None = None
        page_count: int | None = None

    class PersonSerializer(ModelSerializer[Person]):
        name: str
        borrows: list[BookSerializer]

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
    class BookSerializer(ModelSerializer[Book]):
        title: str
        price: float | None = None
        page_count: int | None = None

    class ShelfSerializer(ModelSerializer[BookShelf]):
        name: str
        books: list[BookSerializer]

    serializer = ShelfSerializer(
        name="fantastic",
        books=[
            BookSerializer(title="Harry Potter", price=30, page_count=300),
            BookSerializer(title="LOTR", price=40, pages_count=200),
        ],
    )
    async with in_transaction():
        shelf = await serializer.create_tortoise_instance()
    assert await shelf.books.all().count() == 2
    assert await Book.filter(title="LOTR", shelf__name="fantastic").exists()
    assert Book.filter(title="LOTR", shelf__name="fantastic").exists()


async def test_get_model_fields():
    class ShelfSerializer(ModelSerializer[BookShelf]):
        id: int
        name: str

    class BookSerializer(ModelSerializer[Book]):
        id: int
        title: str
        price: float | None = None
        page_count: int | None = None
        shelf: ShelfSerializer
        ignore_me: str

    assert BookSerializer.get_model_fields() == {
        "id",
        "title",
        "price",
        "page_count",
        "shelf",
        "shelf__id",
        "shelf__name",
    }


async def test_one_to_one():
    person = await Person.create(
        name="John",
        location=await Location.create(name="Somewhere"),
    )

    class LocationSerializer(ModelSerializer[Location]):
        id: int
        name: str

    class PersonSerializer(ModelSerializer[Person]):
        id: int
        name: str
        location: LocationSerializer

    serializer = await PersonSerializer.from_tortoise_orm(person)

    assert serializer.id == person.id
    assert serializer.name == person.name
    assert serializer.location.id == person.location.id
    assert serializer.location.name == person.location.name


def test_nested_serializer_get_prefetch_fields():
    class LocationSerializer(ModelSerializer[Location]):
        id: int

    class BookSerializer(ModelSerializer[Book]):
        id: int
        title: str

    class PersonSerializer(ModelSerializer[Person]):
        id: int
        name: str
        location: LocationSerializer | None
        books: list[BookSerializer]

    # books does not exists in the `Person` model so it should not be there
    assert "books" not in PersonSerializer.get_prefetch_fields()
    assert "location" in PersonSerializer.get_prefetch_fields()
