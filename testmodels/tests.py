import uuid

from django.utils import timezone
import pytest

from src.django_stagers.stagers import Stager
from testmodels.models import Bar, Foo


@pytest.fixture
def foo_stager():
    return Stager[Foo](Foo.objects.all(), key="id")

def create_foo():
    return Foo(
        name=str(uuid.uuid4()),
        value=str(uuid.uuid4()),
        datetime_action=timezone.now()
    )


@pytest.fixture
def foo():
    return create_foo()


@pytest.fixture
def bar_stager():
    return Stager[Bar](Bar.objects.all(), key="id")


@pytest.fixture
def populated_foo_stager(foo_stager: Stager[Foo]):
    for _ in range(10):
        foo_stager.create(create_foo())
    foo_stager.commit()
    return foo_stager


@pytest.mark.django_db
def test_foo_create(foo_stager: Stager[Foo], foo: Foo):
    foo_stager.create(foo)
    foo_stager.commit()

    new_foo = Foo.objects.get(id=foo.id)
    assert new_foo.name == foo.name
    assert new_foo.value == foo.value


@pytest.mark.django_db
def test_foo_unseen(populated_foo_stager: Stager[Foo], foo: Foo):
    populated_foo_stager.create(foo)

    assert str(foo.id) in populated_foo_stager.seen
    assert len(populated_foo_stager.unseen_instances) == 10
    assert len(populated_foo_stager.existing) == 11
