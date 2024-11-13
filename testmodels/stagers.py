from src.django_stagers import Stager, SuperStager
from testmodels.models import Bar, Foo


class FooStager(SuperStager):
    foo = Stager(Foo.objects.all())
    bar = Stager(Bar.objects.all())


def test_foo_stager():
    fs = FooStager()

    foo = Foo()
    bar = Bar()

    # fs.create_mtm(foo, bar, field="normal_bars")

    # Desired usage
    # Notes: Create a "MTMStager" class (standalone) which subclasses "Stager"
    # and focuses on creating through-table instances. Additional functionality
    # should include the `add()` function, which will stage an MTM link between
    # `foo` and `bar`, for instance.
    fs.foo.normal_bars.add(foo, bar)
