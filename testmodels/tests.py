import uuid

from django.utils import timezone
import pytest
from faker import Faker

from src import django_stagers as stagers
from testmodels.models import Bar, Foo

Faker.seed(str(uuid.uuid4()))
fake = Faker()

def create_foo():
    return Foo(
        name=fake.name(),
        value=fake.text(),
        datetime_action=timezone.now()
    )

def create_bar(foo: Foo):
    return Bar(
        foo=foo,
        name=fake.name(),
        value=fake.text(),
        datetime_action=timezone.now()
    )

@pytest.fixture
def foo():
    return create_foo()


@pytest.fixture
def bar(foo: Foo):
    return create_bar(foo)


@pytest.fixture
def foo_stager():
    return stagers.Stager[Foo](Foo.objects.all())

@pytest.fixture
def foo_bar_stager():

    class FooBarStager(stagers.SuperStager):
        foo = stagers.Stager(Foo.objects.all())
        bar = stagers.Stager(Bar.objects.all())

    return FooBarStager()


@pytest.fixture
def bar_stager():
    return stagers.Stager[Bar](Bar.objects.all())


@pytest.fixture
def populated_foo_stager(foo_stager: stagers.Stager[Foo]):
    for _ in range(10):
        foo_stager.create(create_foo())
    foo_stager.commit()
    return foo_stager


@pytest.mark.django_db
def test_foo_create(foo_stager: stagers.Stager[Foo], foo: Foo):
    foo_stager.create(foo)
    foo_stager.commit()

    new_foo = Foo.objects.get(id=foo.id)
    assert new_foo.name == foo.name
    assert new_foo.value == foo.value


@pytest.mark.django_db
def test_foo_unseen(populated_foo_stager: stagers.Stager[Foo], foo: Foo):
    populated_foo_stager.create(foo)
    assert str(foo.id) in populated_foo_stager.seen
    assert len(populated_foo_stager.unseen_instances) == 10

    populated_foo_stager.commit()
    assert len(populated_foo_stager.existing) == 11


@pytest.mark.django_db
def test_foo_bar_multi_creation(foo_bar_stager, bar: Bar):
    foo_bar_stager.bar.create(bar)
    foo_bar_stager.foo.create(bar.foo)
    foo_bar_stager.commit()

    assert Foo.objects.get(id=bar.foo.id)
    assert Bar.objects.get(id=bar.id)


@pytest.mark.django_db
def test_foo_bar_mtm_implicit_creation(foo_bar_stager, bar: Bar):
    foo_bar_stager.foo.normal_bars.add(bar.foo, bar)
    foo_bar_stager.commit()

    new_foo = Foo.objects.get(id=bar.foo.id)
    new_bar = Bar.objects.get(id=bar.id)
    assert new_foo.normal_bars.contains(new_bar)


@pytest.mark.django_db
def test_foo_bar_multi_and_mtm_creation(foo_bar_stager, bar: Bar):
    foo_bar_stager.bar.create(bar)
    foo_bar_stager.foo.create(bar.foo)
    foo_bar_stager.foo.normal_bars.add(bar.foo, bar)
    foo_bar_stager.commit()

    new_foo = Foo.objects.get(id=bar.foo.id)
    new_bar = Bar.objects.get(id=bar.id)
    assert new_foo.normal_bars.contains(new_bar)
    assert new_bar.foo.id == new_foo.id


@pytest.mark.django_db
def test_deduping_mtm_creation(foo_bar_stager, bar: Bar):
    foo_bar_stager.bar.create(bar)
    foo_bar_stager.foo.create(bar.foo)
    foo_bar_stager.foo.normal_bars.add(bar.foo, bar)
    foo_bar_stager.bar.normal_foos.add(bar.foo, bar)
    foo_bar_stager.commit()

    new_foo = Foo.objects.get(id=bar.foo.id)
    new_bar = Bar.objects.get(id=bar.id)
    assert new_foo.normal_bars.contains(new_bar)
    assert new_bar.foo.id == new_foo.id


@pytest.mark.django_db
def test_foo_bar_many_creation(foo_bar_stager):

    n = 100

    for _ in range(n):
        foo = create_foo()
        bar = create_bar(foo)
        foo_bar_stager.bar.create(bar)
        foo_bar_stager.foo.create(bar.foo)
        foo_bar_stager.foo.normal_bars.add(bar.foo, bar)

    foo_bar_stager.commit()

    assert Foo.objects.count() == n
    assert Bar.objects.count() == n
