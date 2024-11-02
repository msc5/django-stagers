<p align="center">
  <img width="200px" src="https://github.com/user-attachments/assets/6bc2f8f8-05b8-4463-9a24-2e352f5f65cb" />
</p>

# Django Stagers
This package provides a `Stager` class to stage objects for bulk creation, update, or deletion at a later time.

When syncing data from an external API or data source, this package helps by handling which objects in the database are new, existing, or need updating. That way, you can focus on the sync logic without tracking model instances or their unique identifiers.

# Installation

Install using `pip`:
```bash
pip install django-stagers
```

Import the `Stager` class:
```python
from django_stagers import Stager
```

# Usage
Instantiate a `Stager` class (optionally with a type for better type-hinting) and then call the `create()` function to add models. When any of the three staging methods `create()`, `delete()` or `update()` are called, though, the corresponding action is not actually taken in the database until the user calls `commit()`, at which point all of the staged actions are run in bulk fashion.

```python
from django_stagers import Stager

foo_stager = Stager[Foo](queryset=Foo.objects.all(), key="id")

foo_stager.create(Foo(attr="abc"))
foo_stager.create(Foo(attr="def"))
...

foo_stager.commit()
```

The output of the operation will look something like this:
```
Sat Nov 2 2024 2:36:37 PM [INFO    ]: Committing staged Foo instances.
Sat Nov 2 2024 2:36:37 PM [INFO    ]: Created     37 Foo instances.
```

## Existing
Another key feature of stagers is that they keep track of existing model instances in the database using the `existing` attribute. This way, it is easy to check whether a new object is already tracked in the django database or has already been staged previously. 

```python

for serialized_foo in serialized_foos:
  if existing_foo := foo_stager.existing.get(serialized_foo['key']):
    foo_stager.update(serialized_foo['key'], 'attr', serialized_foo['attr'])
  else:
    foo_stager.create(Foo(**serialized_foo))
...

foo_stager.commit()
```
