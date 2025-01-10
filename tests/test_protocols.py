from tortoise_serializer import ModelSerializer, Serializer
from tortoise_serializer.protocols import SerializerProtocol


async def test_serializer_follow_protocol():
    assert isinstance(Serializer(), SerializerProtocol)


async def test_model_serializer_follow_protocol():
    serializer = ModelSerializer()
    assert isinstance(serializer, SerializerProtocol)
