class TortoiseSerializerException(Exception): ...


class TortoiseSerializerClassMethodException(TortoiseSerializerException):
    def __init__(self, faulty_class, field_name: str):
        self._field_name = field_name
        self._faulty_class = faulty_class

    def __str__(self) -> str:
        return (
            "Bad configuration for TortoiseSerializer for class "
            f"{self._faulty_class.__name__}.{self._field_name}"
            "Reason: You have to declare that resolver as a @classmethod"
        )
