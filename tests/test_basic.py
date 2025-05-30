import asyncio
from typing import Any

import pytest

from tests.models import Book, BookShelf, Person
from tortoise_serializer import (
    ContextType,
    Serializer,
    require_condition_or_unset,
    resolver,
)


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


async def test_foreignkey_none():
    class ShelfSerializer(Serializer):
        name: str

    class BookSerializer(Serializer):
        title: str
        shelf: ShelfSerializer | None

    book = await Book.create(title="test title", shelf=None)
    serializer = await BookSerializer.from_tortoise_orm(book)
    assert serializer.shelf is None


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


async def test_many_to_many_serializers():
    books = [
        await Book.create(title="LOTR"),
        await Book.create(title="Harry Potter"),
        await Book.create(title="The Great Gatsby"),
    ]
    alice = await Person.create(name="Alice")

    await alice.borrows.add(*books)

    class BookSerializer(Serializer):
        id: int
        title: str

    class PersonSerializer(Serializer):
        id: int
        name: str
        borrows: list[BookSerializer]

    assert await alice.borrows.all().count() == 3
    serializer = await PersonSerializer.from_tortoise_orm(alice)
    assert serializer.id == alice.id
    assert serializer.name == alice.name
    assert len(serializer.borrows) == len(books)


async def test_resolver_simple():
    class BookSerializer(Serializer):
        title: str
        price_per_page: float | None

        @classmethod
        def resolve_price_per_page(
            cls, instance: Book, context: ContextType
        ) -> float | None:
            try:
                return instance.price / instance.page_count
            except (ZeroDivisionError, TypeError, ValueError):
                return None

    book = await Book.create(title="Test", price=10, page_count=200)
    serializer = await BookSerializer.from_tortoise_orm(book)
    assert serializer.price_per_page == 0.05


@pytest.mark.parametrize("value", [None, "", "some valid title"])
async def test_has_been_set(value: Any):
    class BookSerializer(Serializer):
        title: str | None = None

    serializer = BookSerializer(title=value)
    assert serializer.has_been_set("title") is True


async def test_has_not_been_set():
    class BookSerializer(Serializer):
        title: str | None = None

    serializer = BookSerializer()
    assert serializer.has_been_set("title") is False


async def test_foreignkey_auto_fetch():
    class ShelfSerializer(Serializer):
        name: str

    class BookSerializer(Serializer):
        title: str
        shelf: ShelfSerializer

    await Book.create(
        title="LOTR", shelf=await BookShelf.create(name="Testing")
    )

    book = await Book.get(title="LOTR")
    serializer = await BookSerializer.from_tortoise_orm(book)
    assert serializer.shelf.name == "Testing"


async def test_resolving_priority():
    class BookSerializer(Serializer):
        price: float

        @classmethod
        def resolve_price(cls, instance: Book, context: ContextType):
            return instance.price * 2

        @classmethod
        def resolve_shelf(cls, instance: Book, context: ContextType):
            return None

    book = await Book.create(
        title="Foo",
        price=10,
        shelf=await BookShelf.create(name="Something"),
    )
    serializer = await BookSerializer.from_tortoise_orm(book)
    assert serializer.price == 20


async def test_nested_context_preservation():
    class BookSerializer(Serializer):
        title: str
        has_been_borrowed: bool

        @classmethod
        async def resolve_has_been_borrowed(
            cls, instance: Book, context: ContextType
        ):
            return (
                await context["person"]
                .borrows.filter(title=instance.title)
                .exists()
            )

    class PersonSerializer(Serializer):
        id: int
        name: str
        borrows: list[BookSerializer]

    person = await Person.create(name="Xena")
    book = await Book.create(title="Dogts are better than cats")
    await person.borrows.add(book)

    serializer = await PersonSerializer.from_tortoise_orm(
        person, context={"person": person}
    )
    assert serializer.borrows[0].has_been_borrowed is True


async def test_prefech_fields_simple():
    class BookSerializer(Serializer):
        title: str

    class ShelfSerializer(Serializer):
        id: int
        name: str
        books: list[BookSerializer]

    assert ShelfSerializer.get_prefetch_fields() == ["books"]


async def test_prefetch_nested():
    class SerializerA(Serializer):
        id: int

    class SerializerB(Serializer):
        id: int
        a: SerializerA | None

    class SerializerC(Serializer):
        id: int
        b: SerializerB

    assert SerializerC._is_nested_serializer("b")
    assert SerializerB._is_nested_serializer("a")
    assert SerializerC.get_prefetch_fields() == ["b", "b__a"]


async def test_resolver_decorator():
    async def check_margin_permission(
        instance: Book, context: ContextType
    ) -> bool:
        return False

    class BookSerializer(Serializer):
        title: str
        discount: float
        margin: float | None = None

        @resolver("discount")
        def _discount(cls, instance: Book, context: ContextType) -> float:
            return 0.15

        @resolver("title")
        @require_condition_or_unset(lambda instance, context: True)
        def _clean_title(cls, instance: Book, context: ContextType) -> str:
            return instance.title.title().strip()

        @resolver("margin")
        @require_condition_or_unset(check_margin_permission)
        async def _margin(cls, instance: Book, context: ContextType) -> float:
            return 0.15

    book = await Book.create(
        title="testing with title", price=10, page_count=200
    )
    serializer = await BookSerializer.from_tortoise_orm(book)
    assert serializer.discount == 0.15
    assert serializer.title == "Testing With Title"
    assert serializer.margin is None
