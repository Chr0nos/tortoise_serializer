# Tortoise Serializer
## Installation
```shell
pip add tortoise-serializer
```

## Core concept
A `Serializer` does not know what model it's about to serialize, so if you want to say something like:
```python
from tortoise_serializer import Serializer


class ItemByNameSerializer(Serializer):
    id: int
    name: str


products = await ItemByNameSerializer.from_queryset(Product.all())
users = await ItemByNameSerializer.from_queryset(User.all())
```
that would be totaly fine.

Serialiers are `pydantic.BaseModel` objects, meaning that you can directly return them from `FastAPI` or use anything from a BaseModel


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

(note that you "can" specify `context` to pass information to serializers but you don't have to)


### Writing
```python
from fastapi import Body



class MyUserCreationSerializer(Serializer):
    name: str


@router.post("")
async def create_user(user_serializer: MyUserCreationSerializer = Body(...)) -> MyUserSerializer:
    user = await user_serializer.create_tortoise_instance()
    # here you can also pass `context=` to that function
    return await MyUserSerializer.from_tortoise_orm(user)
```

### Context
The context in serializers is immutable

### Resolvers
Sometime we need to output a computed value or not expose senssitive data to anyone, to achieve that we have 2 concepts: `resolvers` and `context`
here's an example of a resolver:
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
    # we have to put a default to None here but the field will be ommited
    address: str | None = None

    @classmethod
    # you could also use `tortoise_serializer.Unset` (return Type[Unset])
    # wich will lead the serializer to ommit the field from the UserSerializer
    # BaseModel.__init__
    @require_permission_or_unset(is_self)
    async def resolve_address(cls, instance: UserModel, context: ContextType) -> str:
        return instance.address



@app.get("/users", response_model_exclude_unset=True)
async def list_users(user: UserModel = Depends(...)) -> list[UserSerializer]:
    return await UserSerializer.from_queryset(UserModel.all(), context={"user": user})
```

this way you won't expose the field `address` at all to other users.


Async resolvers are all called in concurency at the Serializer instancation time
