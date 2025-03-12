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


## Installation
```shell
pip add tortoise-serializer
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

> Note: It is currently not possible to handle ForeignKeys directly using serializers. You need to manage such logic in your views.


### Context
The context in serializers is immutable.


### Resolvers
Sometimes, you need to compute values or restrict access to sensitive data. This can be achieved with `resolvers` and `context`. Here's an example:

```python
from tortoise_serializer import ContextType, Serializer, require_permission_or_unset
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
    # Default is set to None, but the field will be omitted.
    address: str | None = None

    @classmethod
    @require_permission_or_unset(is_self)
    async def resolve_address(cls, instance: UserModel, context: ContextType) -> str:
        return instance.address


@app.get("/users", response_model_exclude_unset=True)
async def list_users(user: UserModel = Depends(...)) -> list[UserSerializer]:
    return await UserSerializer.from_queryset(UserModel.all(), context={"user": user})
```

This ensures that the `address` field is not exposed to unauthorized users.

Async resolvers are called concurrently during serializer instantiation.

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
serializer = ShelfSerializer.from_queryset(
    BookShelf.all().prefetch_related("books").order_by("name")
)
```

For a normal ForeignKey relationship:

```python
class ShelfSerializer(Serializer):
    id: int
    name: str


class BookSerializer(Serializer):
    id: int
    title: str
    shelf: ShelfSerializer | None
```


Reverse relations are `list[Serializer]`

Limitations:
Limitations: You cannot declare a field like this:
```python
class SerializerA(Serializer):
    ...


class SerializerB(Serializer):
    ...


class MyWrongSerializer(Serializer):
    my_field = SerializerA | SerializerB
```

but you can still use `None` like:
```python
class MySerializer(Serializer):
    some_relation: SerializerA | None = None
```

### Many2Many
There are two ways to handle Many-to-Many relationships:

- Use an intermediate Serializer with two ForeignKeys.
- Use a resolver in the serializer.

### Computed fields
Serialization involves resolving fields in the following order:

- Resolvers (computed fields)
- ForeignKeys
- Model fields
This order allows hiding fields based on the request.

Example of a computed field:
```python
from pydantic import Field
from tortoise_serializer import Serializer, ContextType
from tortoise.queryset import QuerySet


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
    path: str
    # This description will appear in Swagger's schema.
    answer_to_the_question: int = Field(description="The answer to the big question of life")

    @classmethod
    async def resolve_path(cls, instance: Book, context: ContextType) -> str:
        if not instance.shelf:
            return instance.title
        if isinstance(instance.shelf, QuerySet):
            await instance.fetch_related("shelf")
        return f'{instance.shelf.name}/{instance.title}'

    @classmethod
    def resolve_answer_to_the_question(cls, instance: Book, context: ContextType) -> int:
        return 42

main_shelf = await Shelf.create(title="main")
my_book = await Book.create(title="Serializers 101", shelf=main_shelf)
serializer = await BookSerializer.from_tortoise_orm(my_book)

assert serializer.path == "main/Serializers 101"
assert serializer.answer_to_the_question == 42

```

All async resolvers will be resolved in concurency in a `asyncio.gather`, non-async ones will be resolved one after the other

## Model Serializers
Sometime it may be usefull or necessary to be able to create a row and it's related foreignkeys at once in one endpoint, to achieve that the `ModelSerializer` class exists:
```python
from tortoise import Model, fields
from tortoise_serializer import ModelSerializer


class Book(Model):
    id = fields.IntField(primary_key=True)
    title = fields.CharField(db_index=True, max_length=200)
    shelf = fields.ForeignKeyField(
        "models.BookShelf",
        on_delete=fields.SET_NULL,
        null=True,
        related_name="books",
    )

class BookShelf(Model):
    id = fields.IntField(primary_key=True)
    name = fields.CharField(unique=True, max_length=200)
    books: BackwardFKRelation[Book]


class ShelfCreationSerializer(ModelSerializer[BookShelf]):
    name: str


class BookCreationSerializer(ModelSerializer[Book]):
    title: str
    # here ofc it's a bit weird to create the shelves with the books but
    # it's only for the example
    shelf: ShelfCreationSerializer


serializer = BookCreationSerializer(title="Some Title", shelv={"name": "where examples lie"})
example = await serializer.create_tortoise_instance()

# example will be an instance of `Book` here with it's related `shelf` realtion

assert await Book.filter(name="Some Title", shelv__name="where examples lie").exists()
```

Models serializer can manage:
- [x] Foreign keys
- [x] Backward foreign key
- [x] Many2Many relations
- [x] One to one relationship
