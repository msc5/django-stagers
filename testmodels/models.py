import uuid

from django.db import models
from django.utils import timezone


class DataModel(models.Model):

    class Meta:
        abstract = True

    id = models.UUIDField(default=uuid.uuid4, unique=True, primary_key=True)
    name = models.CharField(max_length=128)
    value = models.CharField(max_length=256)


class Foo(DataModel):
    normal_bars = models.ManyToManyField("Bar", related_name="normal_foos")
    special_bars = models.ManyToManyField("Bar", related_name="special_foos", through="SpecialFooBar")

    datetime_action = models.DateTimeField()
    datetime_created = models.DateTimeField(default=timezone.now)


class Bar(DataModel):
    foo = models.ForeignKey(Foo, on_delete=models.PROTECT, related_name="bars")

    datetime_action = models.DateTimeField()
    datetime_created = models.DateTimeField(default=timezone.now)


class SpecialFooBar(models.Model):
    foo = models.ForeignKey("Foo", related_name="special_foo_bars", on_delete=models.PROTECT)
    bar = models.ForeignKey("Bar", related_name="special_foo_bars", on_delete=models.PROTECT)
