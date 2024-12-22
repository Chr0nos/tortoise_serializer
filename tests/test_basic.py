import asyncio

from tests.models import Book, BookShelf
from tortoise_serializer import Serializer


async def test_book_serialization():
    class BookSerializer(Serializer):
        id: int
        title: str

    book = await Book.create(title="test title")
    serializer = await BookSerializer.from_tortoise_orm(book)
    assert book.title == serializer.title
    assert book.id == serializer.id


async def test_foreignkey_serialization():
    class ShelfSerializer(Serializer):
        id: int
        name: str

    class BookSerializer(Serializer):
        id: int
        title: str
        shelf: ShelfSerializer

    shelf = await BookShelf.create(name="test shelf")
    book = await Book.create(title="test title", shelf=shelf)
    serializer = await BookSerializer.from_tortoise_orm(book)
    assert book.shelf.id == serializer.shelf.id
    assert book.shelf.name == serializer.shelf.name


async def test_backward_fk_serialization():
    class BookSerializer(Serializer):
        id: int
        title: str

    class ShelfSerializer(Serializer):
        id: int
        name: str
        books: list[BookSerializer]

    shelf = await BookShelf.create(name="test shelf")
    books = await asyncio.gather(
        Book.create(title="A", shelf=shelf),
        Book.create(title="B", shelf=shelf),
        Book.create(title="C", shelf=shelf),
    )
    serializer = await ShelfSerializer.from_tortoise_orm(shelf)
    assert len(serializer.books) == len(books)
    for book_index, book in enumerate(books):
        assert serializer.books[book_index].id == book.id
        assert serializer.books[book_index].title == book.title


async def test_multiples_instances_serialization():
    class BookSerializer(Serializer):
        title: str

    books = [await Book.create(title="A"), await Book.create(title="B")]
    serializers = await BookSerializer.from_tortoise_instances(books)
    assert isinstance(serializers, list)
    assert len(serializers) == len(books)
    for book_index, serializer in enumerate(serializers):
        assert serializer.title == books[book_index].title


async def test_instance_update():
    class UpdateSerializer(Serializer):
        title: str

    shelf = await BookShelf.create(name="Testing")
    book = await Book.create(title="test title", shelf=shelf)
    serializer = UpdateSerializer(title="New title")
    assert serializer.partial_update_tortoise_instance(book)
    assert book.title == "New title"
    assert book.shelf_id == shelf.id


async def test_instance_creation():
    class BookCreationSerializer(Serializer):
        title: str
        shelf_id: int

    shelf = await BookShelf.create(name="Testing")
    lotr_serializer = BookCreationSerializer(title="LOTR", shelf_id=shelf.id)
    book = await lotr_serializer.create_tortoise_instance(Book)
    assert book.id
    assert book.title == "LOTR"
    assert book.shelf_id == shelf.id


async def test_from_queryset():
    class BookSerializer(Serializer):
        title: str

    await Book.bulk_create(
        [
            Book(title="LOTR"),
            Book(title="Harry Potter"),
            Book(title="The Great Gatsby"),
        ]
    )
    serializers = await BookSerializer.from_queryset(Book.all())
    assert isinstance(serializers, list)
    assert len(serializers) == 3
    for index, title in enumerate(
        ("LOTR", "Harry Potter", "The Great Gatsby")
    ):
        assert serializers[index].title == title
