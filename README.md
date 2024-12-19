# Tortoise Serializer
## Installation
```shell
pip add tortoise-serializer
```

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
