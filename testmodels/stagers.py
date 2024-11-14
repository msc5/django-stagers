from django.utils import timezone
from src.django_stagers import Stager, SuperStager
from testmodels.models import Bar, Foo


class FooStager(SuperStager):
    foo = Stager(Foo.objects.all())
    bar = Stager(Bar.objects.all())


def test_foo_stager():
    fs = FooStager()

    foo = Foo(
        name="foo_1",
        value="foo_1",
        datetime_action=timezone.now()
    )
    bar = Bar(
        foo=foo,
        name="bar_1",
        value="bar_1",
        datetime_action=timezone.now()
    )
    fs.foo.normal_bars.add(foo, bar)

    fs.commit()
