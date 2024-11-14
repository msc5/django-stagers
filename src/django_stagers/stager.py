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
    depends_on: dict[str, set[str]]

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
        self.depends_on = defaultdict(set)
        self.reset_seen()

    def reset_seen(self):
        self.seen = set()

    def _check_dependencies(self, instance: Model):
        key = str(getattr(instance, self.key, ""))
        for field in instance._meta.get_fields():
            if isinstance(field, models.ForeignKey):
                self.depends_on[key].add(getattr(instance, field.name).pk)

    def create(self, qs_or_instance: QuerySet[M] | M) -> None:
        if isinstance(qs_or_instance, QuerySet):
            for instance in qs_or_instance:
                self.create(instance)
        else:
            assert isinstance(instance := qs_or_instance, self.model)
            key = str(getattr(instance, self.key, ""))

            # Use most recent `qs_or_instance` associated with `key`,
            # potentially overwriting previous version that existed from a
            # different `create()` call.
            self.to_create[key] = instance
            self.existing[key] = instance
            self.seen.add(key)
            self._check_dependencies(instance)

    def update(self, qs_or_instance: QuerySet[M] | M, field: str, value: Any) -> None:
        if isinstance(qs_or_instance, QuerySet):
            for instance in qs_or_instance:
                self.update(instance, field, value)
        else:
            assert isinstance(instance := qs_or_instance, self.model)
            key = str(getattr(instance, self.key, ""))

            if key in self.to_delete:
                raise Exception(f"The model model with key {key} is already staged for deletion.")

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
                self.create(instance)

            self.seen.add(key)
            self._check_dependencies(instance)

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

    def get(self, key: str):
        return self.existing.get(key)

    def has(self, instance: M):
        key = getattr(instance, self.key)
        return key in self.existing

    @property
    def unseen_instances(self) -> list[M]:
        return [
            model for key, model in self.existing.items()
            if key not in self.seen
        ]


class MTMStager(Stager):
    model_stagers: list[Stager]

    def __init__(self, model_stagers: list[Stager] | None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if model_stagers:
            self.model_stagers = model_stagers

    def _get_through_field(self, model: Model):
        model_class = model._meta.model

        for field in self.model._meta.get_fields():
            rel = getattr(field, "remote_field", None)
            if rel and rel.model == model_class:
                return field

        raise Exception(f"No MTM accessor found on model {model}")

    def _get_model_stager(self, model: Model):
        model_class = model._meta.model
        model_stager = None

        for stager in self.model_stagers:
            if stager.model == model._meta.model:
                model_stager = stager

        if not model_stager:
            raise Exception(f"No stager found for model {model_class}")

        return model_stager

    def _add(self, from_instance: Model, to_instance: Model):
        through = self.model()

        for instance in [from_instance, to_instance]:
            model_stager = self._get_model_stager(instance)
            if not model_stager.has(instance):
                model_stager.create(instance)

            through_field = self._get_through_field(instance)
            setattr(through, through_field.name, instance)

        self.create(through)

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
                self._add(from_instance, to_qs_or_instance)


class SuperStager:
    stagers: dict[str, Stager[Model]] 
    stagers_mtm: dict[str, MTMStager]

    def __init__(self, *args, **kwargs):
        self.stagers = {}
        self.stagers_mtm ={}

        # Collect all `Stager` instances defined on subclass and cache them by
        # model name
        for key in dir(self):
            if isinstance(stager := getattr(self, key), Stager):
                self.stagers[stager.model.__name__] = stager

        # Collect all ManyToManyField through-tables and create `MTMStager`
        # instances for through-table models
        for stager_key, stager in self.stagers.items():
            for field in self._get_mtm_fields(stager.model):
                self._create_stager_mtm(field)

    def _create_stager_mtm(self, field: models.ManyToManyField):
        assert (through := field.remote_field.through)

        # Instantiate MTMStager to stage and create ManyToManyField
        # through-table model instances
        stager_mtm = MTMStager(
            queryset=through.objects.all(),
            model_stagers=[
                self.stagers[field.model.__name__],
                self.stagers[field.remote_field.model.__name__],
            ]
        )

        # Cache `MTMStager` instance by through-table model name
        self.stagers_mtm[through._meta.model.__name__] = stager_mtm

        # Assign attributes to `Stager` instances named by the ManyToManyField
        # accessor name, e.g., `foo.special_bars`
        for stager in self.stagers.values():
            if field.model == stager.model:
                setattr(stager, field.name, stager_mtm)
            elif field.remote_field.model == stager.model:
                setattr(stager, field.remote_field.name, stager_mtm)

    def _get_mtm_fields(self, model: type[Model]):
        mtm_fields = []

        for field in model._meta.get_fields():
            if isinstance(field, models.ManyToManyField):
                through = field.remote_field.through
                if through:
                    mtm_fields.append(field)

        return mtm_fields

    def commit(self) -> None:
        from rich import print
        breakpoint()

        for stager in self.stagers.values():
            print(stager.model, stager.to_create)
            for model in stager.to_create.values():
                print(model.__dict__)
            stager.commit()

        for stager in self.stagers_mtm.values():
            print(stager.model, stager.to_create)
            for model in stager.to_create.values():
                print(model.__dict__)
            stager.commit()
