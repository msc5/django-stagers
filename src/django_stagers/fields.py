from django.db import models


def _get_mtm_fields(model: type[models.Model]):
    mtm_fields = []

    for field in model._meta.get_fields():
        if isinstance(field, models.ManyToManyField):
            through = field.remote_field.through
            if through:
                mtm_fields.append(field)

    return mtm_fields


def _get_mtm_interior_accessors(mtm_model: type[models.Model]):
    accessors = []

    for field in mtm_model._meta.get_fields():
        if isinstance(field, models.ForeignKey):
            accessors.append(field.name)

    # At some point this function needs to be updated to add more flexibility,
    # i.e., in case a ManyToManyField through-table has extra ForeignKey
    # fields. (Is that even possible?)
    assert len(accessors) == 2

    return accessors


def _get_mtm_unique_key(mtm_model: type[models.Model]):
    accessors = _get_mtm_interior_accessors(mtm_model)
    return tuple(str(getattr(mtm_model, accessor).pk) for accessor in accessors)
