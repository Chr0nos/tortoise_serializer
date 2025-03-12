from tortoise import Model, fields
from tortoise.fields.relational import BackwardFKRelation


class Book(Model):
    id = fields.IntField(primary_key=True)
    title = fields.CharField(db_index=True, max_length=200)
    shelf = fields.ForeignKeyField(
        "models.BookShelf",
        on_delete=fields.SET_NULL,
        null=True,
        related_name="books",
    )
    page_count = fields.IntField(null=True, default=None)
    price = fields.FloatField(null=True, default=None)


class BookShelf(Model):
    id = fields.IntField(primary_key=True)
    name = fields.CharField(unique=True, max_length=200)
    books: BackwardFKRelation[Book]


class Location(Model):
    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=200)


class Person(Model):
    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=200)
    borrows = fields.ManyToManyField(
        "models.Book",
        through="borrow",
        related_name="borrowers",
    )
    location = fields.OneToOneField("models.Location", null=True, default=None)
