from collections import defaultdict
import logging
from typing import Any, Callable, Generic, TypeVar

from django.db import models
from django.db.models import Model, QuerySet
from django.db import transaction


M = TypeVar('M', bound=Model)


class Stager(Generic[M]):
    model: type[M]

    existing: dict[str, M]
    seen: set[str]
    key: str

    existing_related: dict[str, dict[str, QuerySet]]

    to_create: dict[str, M]
    to_update: dict[str, M]
    to_delete: set[str]

    to_update_fields: set[str]

    add: "Callable | None"

    def __init__(
        self,
        queryset: QuerySet[M],
        key: str = 'pk',
        load_related: list[str] = []
    ) -> None:

        self.queryset = queryset
        assert self.queryset.model
        self.model = self.queryset.model
        self.key = key
        self.load_related = load_related

        self.existing = {getattr(m, self.key): m for m in self.queryset}
        self.existing_related = defaultdict(dict)
        for key in self.load_related:
            for existing_key, existing_model in self.existing.items():
                self.existing_related[key][existing_key] = getattr(existing_model, key).all()

        self.reset()

    def reset(self):
        self.to_create = {}
        self.to_update = {}
        self.to_delete = set()
        self.to_update_fields = set()
        self.reset_seen()

    def reset_seen(self):
        self.seen = set()

    def create(self, qs_or_instance: QuerySet[M] | M) -> None:
        if isinstance(qs_or_instance, QuerySet):
            for instance in qs_or_instance:
                self.create(instance)
        else:
            key = str(getattr(qs_or_instance, self.key, ""))

            # Use most recent `qs_or_instance` associated with `key`,
            # potentially overwriting previous version that existed from a
            # different `create()` call.
            self.to_create[key] = qs_or_instance
            self.existing[key] = qs_or_instance

            self.seen.add(key)

    def update(self, qs_or_instance: QuerySet[M] | M, field: str, value: Any) -> None:
        if isinstance(qs_or_instance, QuerySet):
            for instance in qs_or_instance:
                self.update(instance, field, value)
        else:
            key = str(getattr(qs_or_instance, self.key, ""))

            if key in self.to_delete:
                raise Exception(f"The model model with key {key} is already staged for deletion.")

            self.seen.add(key)

            # If the model is already staged to be created or updated, we don't
            # need to also stage it for update, since the value will change
            # when the model is created or updated in `commit()`.
            if tracked_instance := self.to_create.get(key):
                if getattr(tracked_instance, field) != value:
                    setattr(tracked_instance, field, value)

            elif tracked_instance := self.to_update.get(key):
                if getattr(tracked_instance, field) != value:
                    setattr(tracked_instance, field, value)
                    self.to_update_fields.add(field)

            elif tracked_instance := self.existing.get(key):
                if getattr(tracked_instance, field) != value:
                    setattr(tracked_instance, field, value)
                    self.to_update_fields.add(field)
                    self.to_update[key] = tracked_instance

            # If the `qs_or_instance` is not found in any of `to_create`,
            # `to_update`, or `to_delete`, then stage the instance for
            # creation.
            else:
                self.create(qs_or_instance)

    def delete(self, qs_or_instance: QuerySet[M] | M) -> None:
        if isinstance(qs_or_instance, QuerySet):
            for instance in qs_or_instance:
                self.delete(instance)
        else:
            self.to_delete.add(str(qs_or_instance.pk))

    def commit(self) -> None:
        model_name = str(self.model.__name__)

        with transaction.atomic():

            if any([self.to_create, self.to_delete, self.to_update]):
                logging.info(f'Committing staged {model_name} instances.')

            if self.to_create:
                self.model.objects.bulk_create(list(self.to_create.values()))
                logging.info(f'Created {len(self.to_create):8,} {model_name} instances.')

            if self.to_update:
                self.model.objects.bulk_update(list(self.to_update.values()), fields=list(self.to_update_fields))
                logging.info(f'Updated {len(self.to_update):8,} {model_name} instances.')
                logging.info(f'Updated Fields: {self.to_update_fields}')

            if self.to_delete:
                self.model.objects.filter(id__in=self.to_delete).delete()
                logging.info(f'Deleted {len(self.to_delete):8,} {model_name} instances.')

            self.reset()

    @property
    def unseen_instances(self) -> list[M]:
        return [
            model for key, model in self.existing.items()
            if key not in self.seen
        ]


class MTMStager(Stager):
    from_stager: Stager
    to_stager: Stager

    def _get_through_field(self, model: Model):
        model_class = model._meta.model
        for field in self.model._meta.get_fields():
            rel = getattr(field, "remote_field", None)
            if rel and rel.model == model_class:
                return field
        raise Exception(f"No MTM accessor found on model {model}")

    def add(self, from_qs_or_instance: QuerySet | Model, to_qs_or_instance: QuerySet | Model): 
        if isinstance(from_qs_or_instance, QuerySet):
            for from_instance in from_qs_or_instance:
                self.add(from_instance, to_qs_or_instance)

        else:
            from_instance = from_qs_or_instance

            if isinstance(to_qs_or_instance, QuerySet):
                for to_instance in to_qs_or_instance:
                    self.add(from_instance, to_instance)

            else:
                to_instance = to_qs_or_instance

                through = self.model()
                setattr(through, self._get_through_field(from_instance).name, from_instance)
                setattr(through, self._get_through_field(to_instance).name, to_instance)
                self.create(through)

                # At some point, if we're creating mtm relations between models
                # we need to make sure they are staged for their individual
                # stagers as well


class SuperStager:
    stagers: dict[str, Stager[Model]] 
    stagers_mtm: dict[str, MTMStager]
    stagers_mtm_by_model: dict[str, dict[str, MTMStager]]

    def __init__(self, *args, **kwargs):
        self.stagers = {}
        self.stagers_mtm ={}
        self.stagers_mtm_by_model = defaultdict(dict)
        self.stagers_by_relation = []

        # Collect all Stagers defined on subclass
        for key in dir(self):
            if isinstance(stager := getattr(self, key), Stager):
                self.stagers[key] = stager

        for stager_key, stager in self.stagers.items():

            # Collect all ManyToMany through-tables
            model = stager.model
            for field in model._meta.get_fields():
                
                if isinstance(field, models.ManyToManyField):
                    through = field.remote_field.through
                    if through:
                        stager_mtm = MTMStager(queryset=through.objects.all())

                        through_model_name = through._meta.model.__name__

                        from_model_name = model.__name__
                        from_field_name = field.name

                        to_field_name = field.remote_field.name
                        to_model_name = field.remote_field.model.__name__

                        self.stagers_mtm[through_model_name] = stager_mtm
                        self.stagers_mtm_by_model[from_model_name][from_field_name] = stager_mtm
                        self.stagers_mtm_by_model[to_model_name][to_field_name] = stager_mtm

                        self.stagers_by_relation.append((stager_mtm, from_model_name))
                        self.stagers_by_relation.append((stager_mtm, to_model_name))


        for stager in self.stagers.values():
            for accessor, stager_mtm in self.stagers_mtm_by_model[stager.model.__name__].items():
                setattr(stager, accessor, stager_mtm)

        # for stager_mtm in self.stagers_mtm.values():
        #     setattr(stager_mtm, "from_stager", stager)

    def commit(self) -> None:

        for stager in self.stagers.values():
            stager.commit()

        for stager in self.stagers_mtm.values():
            stager.commit()
