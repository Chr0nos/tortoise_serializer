# Tortoise Serializer
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/b801e7563fb34f76a27aeae4d38f2853)](https://app.codacy.com/gh/Chr0nos/tortoise_serializer/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)
[![Codacy Badge](https://app.codacy.com/project/badge/Coverage/b801e7563fb34f76a27aeae4d38f2853)](https://app.codacy.com/gh/Chr0nos/tortoise_serializer/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_coverage)

## Motivation
This project was created to address some of the limitations of `pydantic_model_creator`, including:
- The ability to use a `context` in serialization at the field level.
- Access to the actual Tortoise `Model` instance during serialization.
- Improved readability.
- Support for adding extra logic to specific serializers.
- The ability to document fields in a way that is visible in Swagger.

## Useful readings
- https://docs.pydantic.dev/latest/
- https://tortoise.github.io/


## Installation
```shell
pip install tortoise-serializer
```

## Core concept
A `Serializer` does not need to know which model it will serialize. For example:
```python
from tortoise_serializer import Serializer


class ItemByNameSerializer(Serializer):
    id: int
    name: str


products = await ItemByNameSerializer.from_queryset(Product.all())
users = await ItemByNameSerializer.from_queryset(User.all())
```
This is entirely valid.

`Serializers` are `pydantic.BaseModel` objects, which means you can directly return them from FastAPI endpoints or use any functionality provided by BaseModel.


## Usage
### Reading
```python
from tortoise_serializer import Serializer
from tortoise import Model, fields
from pydantic import Field
from fastapi.routing import APIRouter


class MyUser(Model):
    id = fields.IntegerField(primary_key=True)
    name = fields.CharField(max_length=100, unique=True)


class MyUserSerializer(Serializer):
    id: int
    name: str = Field(max_length=100, description="User unique name")


router = APIRouter(prefix="/users")


@router.get("")
async def get_users() -> list[MyUserSerializer]:
    return await MyUserSerializer.from_queryset(MyUser.all(), context={"user": ...})
```

(Note: You can specify a `context` to pass additional information to serializers, but it is not mandatory.)

### Writing
```python
from fastapi import Body
from pydantic import Field


class MyUserCreationSerializer(Serializer):
    name: str = Field(max_length=200)


@router.post("")
async def create_user(user_serializer: MyUserCreationSerializer = Body(...)) -> MyUserSerializer:
    user = await user_serializer.create_tortoise_instance(MyUser)
    # Here you can also pass a `context=` to this function.
    return await MyUserSerializer.from_tortoise_orm(user)
```

> Note: It is currently not possible to handle ForeignKeys directly using the base `Serializer`. You need to manage such logic in your views, or use `ModelSerializer` instead.

### Partial updates
Use `partial_update_tortoise_instance` to apply only the fields that were explicitly set in the serializer (useful for `PATCH` endpoints). It returns `True` if any field was changed, `False` otherwise:

```python
from pydantic import Field
from tortoise_serializer import Serializer


class BookUpdateSerializer(Serializer):
    title: str | None = None
    price: float | None = None


@router.patch("/{book_id}")
async def update_book(book_id: int, update: BookUpdateSerializer) -> BookSerializer:
    book = await Book.get_or_none(id=book_id)
    if not book:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such book")
    changed = update.partial_update_tortoise_instance(book)
    if changed:
        await book.save()
    return await BookSerializer.from_tortoise_orm(book)
```

Use `has_been_set` to check whether a specific field was included in the payload, even when its value is `None` or an empty string:

```python
serializer = BookUpdateSerializer(title=None)
serializer.has_been_set("title")   # True  — field was explicitly sent
serializer.has_been_set("price")   # False — field was omitted entirely
```

### Context
The context passed to serializers is immutable (stored as a `frozendict`). It is forwarded automatically to nested serializers and all resolvers.


### Resolvers
Sometimes, you need to compute values or restrict access to sensitive data. This can be achieved with resolvers and context.

#### Method-based resolvers
Define a classmethod named `resolve_<field_name>`. It can be sync or async:

```python
from tortoise_serializer import ContextType, Serializer
from tortoise import Model, fields


class BookModel(Model):
    id = fields.IntField(primary_key=True)
    title = fields.CharField(db_index=True)
    shelf = fields.ForeignKeyField(
        "models.BookShelf",
        on_delete=fields.SET_NULL,
        null=True,
        related_name="books",
    )


class BookSerializer(Serializer):
    id: int
    title: str
    path: str
    answer_to_the_question: int

    @classmethod
    async def resolve_path(cls, instance: BookModel, context: ContextType) -> str:
        if not instance.shelf:
            return instance.title
        await instance.fetch_related("shelf")
        return f'{instance.shelf.name}/{instance.title}'

    @classmethod
    def resolve_answer_to_the_question(cls, instance: BookModel, context: ContextType) -> int:
        return 42


serializer = await BookSerializer.from_tortoise_orm(my_book)
assert serializer.path == "main/Serializers 101"
assert serializer.answer_to_the_question == 42
```

All async resolvers are executed concurrently via `asyncio.TaskGroup`. Sync resolvers run sequentially.

#### Decorator-based resolvers
Use the `@resolver` decorator to bind a method to a field when the method name doesn't follow the `resolve_<field>` convention. The method must still be a `@classmethod`:

```python
from tortoise_serializer import resolver, Serializer, ContextType


class UserSerializer(Serializer):
    full_name: str

    @resolver("full_name")
    @classmethod
    def compute_full_name(cls, instance, context: ContextType) -> str:
        return f"{instance.first_name} {instance.last_name}"
```

Async resolvers work the same way:

```python
class BookSerializer(Serializer):
    id: int
    title: str
    shelf_name: str | None = None

    @resolver("shelf_name")
    @classmethod
    async def _fetch_shelf_name(cls, instance: Book, context: ContextType) -> str | None:
        if not instance.shelf_id:
            return None
        await instance.fetch_related("shelf")
        return instance.shelf.name
```

`@resolver` and `@require_condition_or_unset` can be combined. Put `@resolver` outermost, then `@require_condition_or_unset`, then `@classmethod`:

```python
from tortoise_serializer import resolver, require_condition_or_unset, Serializer, ContextType


def is_admin(instance, context: ContextType) -> bool:
    return context.get("user_role") == "admin"


class BookSerializer(Serializer):
    id: int
    title: str
    # capitalised and stripped title
    display_title: str
    # only visible to admins; silently omitted otherwise
    internal_margin: float | None = None

    @resolver("display_title")
    @classmethod
    def _clean_title(cls, instance: Book, context: ContextType) -> str:
        return instance.title.title().strip()

    @resolver("internal_margin")
    @require_condition_or_unset(is_admin)
    @classmethod
    async def _margin(cls, instance: Book, context: ContextType) -> float:
        return instance.cost * 1.3
```

When `is_admin` returns `False`, `internal_margin` is silently omitted. Use `response_model_exclude_unset=True` in FastAPI endpoints to keep the JSON clean.

#### Conditional resolvers
Use `require_condition_or_unset` to conditionally expose a field. When the condition returns `False`, the field is omitted (set to `Unset`) instead of raising a validation error:

```python
from tortoise_serializer import ContextType, Serializer, require_condition_or_unset
from tortoise import Model, fields


class UserModel(Model):
    id = fields.IntegerField(primary_key=True)
    address = fields.CharField(max_length=1000)


def is_self(instance: UserModel, context: ContextType) -> bool:
    current_user = context.get("user")
    if not current_user:
        return False
    return current_user.id == instance.id


class UserSerializer(Serializer):
    id: int
    # Default is set to None, but the field will be omitted when the condition is False.
    address: str | None = None

    @classmethod
    @require_condition_or_unset(is_self)
    async def resolve_address(cls, instance: UserModel, context: ContextType) -> str:
        return instance.address


@app.get("/users", response_model_exclude_unset=True)
async def list_users(user: UserModel = Depends(...)) -> list[UserSerializer]:
    return await UserSerializer.from_queryset(UserModel.all(), context={"user": user})
```

This ensures that the `address` field is not exposed to unauthorized users.

The condition checker can itself be async when used with an async resolver.

### Context propagation into nested serializers
The context is forwarded automatically to all nested serializers and their resolvers. This makes it easy to pass request-scoped data (current user, locale, permissions) down the entire serialization tree without manually threading it:

```python
from tortoise_serializer import Serializer, ContextType


class BookSerializer(Serializer):
    id: int
    title: str
    already_borrowed: bool

    @classmethod
    async def resolve_already_borrowed(
        cls, instance: Book, context: ContextType
    ) -> bool:
        person = context.get("current_person")
        if not person:
            return False
        return await person.borrows.filter(id=instance.id).exists()


class PersonSerializer(Serializer):
    id: int
    name: str
    borrows: list[BookSerializer]  # context is forwarded here automatically


serializer = await PersonSerializer.from_tortoise_orm(
    person, context={"current_person": person}
)
# every BookSerializer in .borrows receives the same context
```


## Relations
### ForeignKeys & OneToOne
To serialize relations, declare a field in the serializer as another serializer:

```python
from tortoise import Model, fields
from tortoise_serializer import Serializer


class BookShelf(Model):
    id = fields.IntField(primary_key=True)
    name = fields.CharField(unique=True)


class Book(Model):
    id = fields.IntField(primary_key=True)
    title = fields.CharField(db_index=True)
    shelf = fields.ForeignKeyField(
        "models.BookShelf",
        on_delete=fields.SET_NULL,
        null=True,
        related_name="books",
    )


class BookSerializer(Serializer):
    id: int
    title: str


class ShelfSerializer(Serializer):
    id: int
    name: str
    books: list[BookSerializer] = []


# Prefetching related fields is optional but improves performance.
serializers = await ShelfSerializer.from_queryset(
    BookShelf.all().prefetch_related("books").order_by("name")
)
```

For a forward ForeignKey relationship (book → shelf):

```python
class ShelfSerializer(Serializer):
    id: int
    name: str


class BookSerializer(Serializer):
    id: int
    title: str
    shelf: ShelfSerializer | None
```

Reverse relations are typed as `list[NestedSerializer]`.

**Limitation:** A field cannot mix two different serializer types:
```python
# This is NOT supported:
class MyWrongSerializer(Serializer):
    my_field: SerializerA | SerializerB
```

But `None` is allowed:
```python
class MySerializer(Serializer):
    some_relation: SerializerA | None = None
```

### Many2Many
Declare the M2M field as `list[NestedSerializer]` — the same as a reverse FK:

```python
from tortoise import Model, fields
from tortoise_serializer import Serializer


class Book(Model):
    id = fields.IntField(primary_key=True)
    title = fields.CharField(max_length=200)


class Person(Model):
    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=200)
    borrows = fields.ManyToManyField("models.Book", related_name="borrowers")


class BookSerializer(Serializer):
    id: int
    title: str


class PersonSerializer(Serializer):
    id: int
    name: str
    borrows: list[BookSerializer]


alice = await Person.create(name="Alice")
await alice.borrows.add(*await Book.filter(title__in=["LOTR", "Dune"]))

serializer = await PersonSerializer.from_tortoise_orm(alice)
# serializer.borrows → [BookSerializer(id=..., title="LOTR"), BookSerializer(id=..., title="Dune")]
```

Two patterns are available for more complex cases:

- Use an intermediate model with two ForeignKeys (for extra fields on the join).
- Use a `resolve_<field>` method to apply custom filtering or ordering.

### Computed fields
Fields are resolved in the following priority order:

1. Resolvers (computed fields)
2. ForeignKeys
3. Model fields

This means a resolver can shadow or replace a model field of the same name.

### Prefetching related fields
Use `get_prefetch_fields()` to generate the list of relations to pass to `prefetch_related`:

```python
queryset = Book.all().prefetch_related(*BookSerializer.get_prefetch_fields())
serializers = await BookSerializer.from_queryset(queryset)
```


## Model Serializers
`ModelSerializer` extends `Serializer` with the ability to create model instances and their nested relations in a single call. It is generic over the Tortoise model it targets.

```python
class MySerializer(ModelSerializer[MyModel]):
    ...
```

It supports creating:
- [x] Foreign keys
- [x] Backward foreign keys
- [x] Many2Many relations
- [x] One-to-one relationships

### Basic Usage
```python
from tortoise import Model, fields
from tortoise.fields.relational import BackwardFKRelation
from tortoise_serializer import ModelSerializer


class BookShelf(Model):
    id = fields.IntField(primary_key=True)
    name = fields.CharField(unique=True, max_length=200)
    books: BackwardFKRelation["Book"]


class Book(Model):
    id = fields.IntField(primary_key=True)
    title = fields.CharField(db_index=True, max_length=200)
    shelf = fields.ForeignKeyField(
        "models.BookShelf",
        on_delete=fields.SET_NULL,
        null=True,
        related_name="books",
    )


class ShelfCreationSerializer(ModelSerializer[BookShelf]):
    name: str


class BookCreationSerializer(ModelSerializer[Book]):
    title: str
    shelf: ShelfCreationSerializer


serializer = BookCreationSerializer(title="Some Title", shelf={"name": "where examples lie"})
book = await serializer.create_tortoise_instance()

assert await Book.filter(title="Some Title", shelf__name="where examples lie").exists()
```

> It is strongly recommended to call `create_tortoise_instance` inside a `transaction` context to ensure atomicity.

### FastAPI
Since Serializers inherit from `pydantic.BaseModel`, they work with FastAPI out of the box.

```python
from fastapi import status, Body, HTTPException
from fastapi.routing import APIRouter
from pydantic import Field
from tortoise import Model, fields
from tortoise.transaction import in_transaction
from tortoise_serializer import ModelSerializer


class Author(Model):
    id = fields.IntegerField(primary_key=True)
    name = fields.CharField(max_length=200, unique=True)


class Book(Model):
    id = fields.IntegerField(primary_key=True)
    title = fields.CharField(max_length=200)
    pages_count = fields.IntegerField()
    author = fields.ForeignKeyField("models.Author", related_name="books")


class AuthorCreationSerializer(ModelSerializer[Author]):
    name: str


class BookCreationSerializer(ModelSerializer[Book]):
    title: str = Field(max_length=200)
    author: AuthorCreationSerializer

    async def _get_or_create_author(self) -> Author:
        author = await Author.filter(name=self.author.name).get_or_none()
        if not author:
            author = await self.author.create_tortoise_instance()
        return author

    async def create_tortoise_instance(self, *args, **kwargs) -> Book:
        kwargs["author"] = await self._get_or_create_author()
        return await super().create_tortoise_instance(*args, **kwargs)


class AuthorSerializer(ModelSerializer[Author]):
    id: int
    name: str


class BookSerializer(ModelSerializer[Book]):
    id: int
    title: str
    author: AuthorSerializer


router = APIRouter(prefix="/books")


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_book(serializer: BookCreationSerializer = Body(...)) -> BookSerializer:
    async with in_transaction():
        book = await serializer.create_tortoise_instance()
    return await BookSerializer.from_tortoise_orm(book)


@router.get("")
async def list_books() -> list[BookSerializer]:
    return await BookSerializer.from_queryset(Book.all(), prefetch=True)


@router.get("/{book_id}")
async def get_book(book_id: int) -> BookSerializer:
    book = await (
        Book.filter(id=book_id)
        .prefetch_related(*BookSerializer.get_prefetch_fields())
        .get_or_none()
    )
    if not book:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such book")
    return await BookSerializer.from_tortoise_orm(book)


@router.delete("/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_book(book_id: int) -> None:
    await Book.filter(id=book_id).delete()


@router.patch("/{book_id}")
async def update_book(book_id: int, update: BookCreationSerializer) -> BookSerializer:
    book = await Book.get_or_none(id=book_id)
    if not book:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such book")
    book.author = await update._get_or_create_author()
    update.partial_update_tortoise_instance(book)
    await book.save()
    return await BookSerializer.from_tortoise_orm(book)
```

### Optimizing Database Queries

#### Prefetching
`ModelSerializer.from_queryset` accepts a `prefetch=True` parameter to automatically prefetch all relations declared in the serializer:

```python
books = await BookSerializer.from_queryset(Book.all(), prefetch=True)
```

This is equivalent to manually calling `.prefetch_related(*BookSerializer.get_prefetch_fields())`.

#### Field selection
Starting from `tortoise-orm` 0.25.0, you can limit fetched columns to only those needed by the serializer using `select_only=True`:

```python
books = await BookSerializer.from_queryset(Book.all(), select_only=True)
```

Or manually via `get_only_fetch_fields()`:

```python
books = await BookSerializer.from_queryset(
    Book.all().only(*BookSerializer.get_only_fetch_fields())
)
```

> `prefetch=True` and `select_only=True` are mutually exclusive.

#### Single instance helpers

`ModelSerializer` provides two convenience methods for fetching a single instance:

```python
# Raises DoesNotExist if not found
book = await BookSerializer.from_single_queryset(Book.filter(id=book_id).get())

# Returns None if not found
book = await BookSerializer.from_single_queryset_or_none(Book.filter(id=book_id).get_or_none())
```

Both automatically prefetch related fields by default (`prefetch=True`).


## Mixins

### BackwardFKBulkCreateMixin
When creating a parent record with many backward FK children, the default implementation creates them one by one (required by Tortoise ORM to obtain generated PKs). If you don't need the child PKs after creation, `BackwardFKBulkCreateMixin` uses `bulk_create` for better performance:

```python
from tortoise_serializer import ModelSerializer
from tortoise_serializer.mixins import BackwardFKBulkCreateMixin


class ShelfCreationSerializer(BackwardFKBulkCreateMixin, ModelSerializer[BookShelf]):
    name: str
    books: list[BookCreationSerializer] = []
```

> **Warning:** Instances created via `bulk_create` will not have their database-generated fields (e.g. `id`) populated after creation. Re-query the database if you need them.
