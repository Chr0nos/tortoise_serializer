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
