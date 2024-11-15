from collections import defaultdict
import logging
from typing import Any, Callable, Generic, Hashable, TypeVar

from django.db import models
from django.db import transaction
from django.db.models import Model, QuerySet

from .fields import _get_mtm_fields, _get_mtm_unique_key


M = TypeVar('M', bound=Model)


class Stager(Generic[M]):
    model: type[M]
    model_name: str

    existing: dict[Hashable, M]
    existing_related: dict[Hashable, dict[Hashable, QuerySet]]

    seen: set[Hashable]
    key: str | Callable[[M], Hashable]

    to_create: dict[Hashable, M]
    to_update: dict[Hashable, M]
    to_delete: set[Hashable]

    to_update_fields: set[Hashable]

    add: "Callable | None"
    super_stager: "SuperStager | None"
    depends_on: dict[Hashable, set[Hashable]]

    def __init__(
        self,
        queryset: QuerySet[M] | Callable[[], QuerySet[M]],
        key: str | Callable[[M], Hashable] = 'pk',
        load_related: list[str] = []
    ) -> None:

        if isinstance(queryset, QuerySet):
            self.queryset = queryset
        else:
            self.queryset = queryset()

        assert self.queryset.model
        self.model = self.queryset.model
        self.model_name = str(self.model.__name__)
        self.key = key
        self.load_related = load_related
        self.depends_on = defaultdict(set)

        self.existing = {self.get_key(m): m for m in self.queryset}
        self.existing_related = defaultdict(dict)
        for key in self.load_related:
            for existing_key, existing_model in self.existing.items():
                self.existing_related[key][existing_key] = getattr(existing_model, key).all()

        self.reset()

    def reset(self, exclude: set[Hashable] = set()):
        self.to_create = {
            key: val
            for key, val in getattr(self, "to_create", {}).items()
            if key in exclude
        }

        self.to_update = {}
        self.to_update_fields = set()
        self.to_delete = set()
        self.reset_seen()

    def reset_seen(self):
        self.seen = set()

    def _track_dependencies(self, instance: M):
        key = self.get_key(instance)
        for field in instance._meta.get_fields():
            if isinstance(field, models.ForeignKey):

                remote_instance = getattr(instance, field.name)
                if self.super_stager:
                    self.depends_on[key].add(self.super_stager.get_key_for_instance(remote_instance))
                else:
                    self.depends_on[key].add(str(remote_instance.pk))

    def log(self, message: str):
        logging.debug(f"[ ({self.model_name}) Stager ] {message}")

    def create(self, qs_or_instance: QuerySet[M] | M) -> None:
        if isinstance(qs_or_instance, QuerySet):
            for instance in qs_or_instance:
                self.create(instance)
        else:
            assert isinstance(instance := qs_or_instance, self.model)
            key = self.get_key(instance)

            if key in self.to_delete:
                raise Exception((f"The {self.model_name} instance with key {key} "
                                 f" is already staged for deletion."))
            if key in self.existing:
                raise Exception((f"The {self.model_name} instance with key {key} "
                                 f"already exists in the database."))

            # If a new model with the same `key` in `self.to_create` is staged
            # for creation, it will replace the existing instance.

            self.to_create[key] = instance
            self.seen.add(key)
            self._track_dependencies(instance)

    def update(self, qs_or_instance: QuerySet[M] | M, field: str, value: Any) -> None:
        if isinstance(qs_or_instance, QuerySet):
            for instance in qs_or_instance:
                self.update(instance, field, value)
        else:
            assert isinstance(instance := qs_or_instance, self.model)
            key = self.get_key(instance)

            if key in self.to_delete:
                raise Exception((f"The {self.model_name} instance with key {key} "
                                 f" is already staged for deletion."))

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
            self._track_dependencies(instance)

    def delete(self, qs_or_instance: QuerySet[M] | M) -> None:
        if isinstance(qs_or_instance, QuerySet):
            for instance in qs_or_instance:
                self.delete(instance)
        else:
            assert isinstance(instance := qs_or_instance, self.model)
            key = self.get_key(instance)

            if key in self.to_create:
                raise Exception((f"The {self.model_name} instance with key {key} "
                                 f"is already staged for creation."))
            if key in self.to_update:
                raise Exception((f"The {self.model_name} instance with key {key} "
                                 f"is already staged for update."))

            self.to_delete.add(self.get_key(instance))

    def commit(self, exclude: set[Hashable] = set()) -> tuple[set[Hashable], set[Hashable], set[Hashable]]:

        # Skip any instances that are listed in `exclude` - They may be
        # depending on other models through ForeignKey relationships.
        to_create = set(self.to_create) - exclude

        with transaction.atomic():
            self.log(f"Depends on {len(self.depends_on)} model instances.")

            if to_create:
                self.model.objects.bulk_create([self.to_create[key] for key in to_create])
                self.log(f'Created {len(self.to_create)} {self.model_name} instances.')

            if self.to_update:
                to_update_fields = list([str(f) for f in self.to_update_fields])
                self.model.objects.bulk_update(list(self.to_update.values()), fields=to_update_fields)
                self.log(f'Updated {len(self.to_update)} {self.model_name} instances.')
                self.log(f'Updated Fields: {self.to_update_fields}')

            if self.to_delete:
                self.model.objects.filter(id__in=self.to_delete).delete()
                self.log(f'Deleted {len(self.to_delete)} {self.model_name} instances.')

            created = set(to_create)
            updated = set(self.to_update)
            deleted = self.to_delete

            for key in created:
                self.existing[key] = self.to_create[key]

            # Exclude instances that were skipped
            self.reset(exclude=exclude)

        return created, updated, deleted

    def get(self, key: str):
        return self.existing.get(key)

    def get_key(self, instance: M) -> Hashable:
        if isinstance(self.key, str):
            return str(getattr(instance, self.key))
        else:
            return self.key(instance)

    @property
    def unseen_instances(self) -> list[M]:
        return [
            model for key, model in self.existing.items()
            if key not in self.seen
        ]


class MTMStager(Stager):
    model_stagers: list[Stager]

    def __init__(self, model_stagers: list[Stager] | None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs, key=_get_mtm_unique_key)
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

            key = model_stager.get_key(instance)
            if key not in model_stager.existing:
                model_stager.create(instance)

            through_field = self._get_through_field(instance)
            setattr(through, through_field.name, instance)

        key = self.get_key(through)
        if not key in self.existing:
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
                to_instance = to_qs_or_instance
                self._add(from_instance, to_instance)


class SuperStager:
    stagers: dict[str, Stager[Model]]
    stagers_mtm: dict[str, MTMStager]

    def __init__(self, *args, **kwargs):
        self.stagers = {}
        self.stagers_mtm = {}

        # Collect all `Stager` instances defined on subclass and cache them by
        # model name
        for key in dir(self):
            if isinstance(stager := getattr(self, key), Stager):
                self.stagers[stager.model.__name__] = stager
                stager.super_stager = self

        # Collect all ManyToManyField through-tables and create `MTMStager`
        # instances for through-table models
        for stager in self.stagers.values():
            for field in _get_mtm_fields(stager.model):
                self._create_stager_mtm(field)

    def _get_stager_for_model(self, model: Model):
        return self.stagers[model._meta.model.__name__]

    def get_key_for_instance(self, model: Model):
        stager = self._get_stager_for_model(model)
        return stager.get_key(model)

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
        stager_mtm.super_stager = self

        # Cache `MTMStager` instance by through-table model name
        self.stagers_mtm[through._meta.model.__name__] = stager_mtm

        # Assign attributes to `Stager` instances named by the ManyToManyField
        # accessor name, e.g., `foo.special_bars`
        for stager in self.stagers.values():
            if field.model == stager.model:
                setattr(stager, field.name, stager_mtm)
            elif field.remote_field.model == stager.model:
                setattr(stager, field.remote_field.name, stager_mtm)

    def _update_stager_dependencies(self, stager: Stager, all_existing: set[str]):

        defunct_dependees = set()
        for dependee, dependencies in stager.depends_on.items():
            stager.depends_on[dependee] = {dep for dep in dependencies if dep not in all_existing}
            if not stager.depends_on[dependee]:
                defunct_dependees.add(dependee)

        stager.depends_on = {
            dependee: dependencies
            for dependee, dependencies in stager.depends_on.items()
            if not dependee in defunct_dependees
        }

    def log(self, message: str):
        logging.debug(f"[ {self.__class__.__name__} ] {message}")

    def commit(self) -> tuple[set[Hashable], set[Hashable], set[Hashable]]:

        all_created = set()
        all_updated = set()
        all_deleted = set()

        all_to_create = set()
        all_existing = set()
        all_dependees = defaultdict(set)

        for stager in self.stagers.values():
            all_to_create |= set(stager.to_create)
            all_existing |= set(stager.get_key(m) for m in stager.existing.values())

            for depender, dependees in stager.depends_on.items():
                for dependee in dependees:
                    all_dependees[dependee].add(depender)

        while all_to_create:
            self.log(f"Creating {len(all_to_create)} model instances")

            for stager in self.stagers.values():

                # TODO: Need to support the case where update() changes an
                # existing instance to a new dependency which may not yet exist

                stager.depends_on = {
                    depender: dependees - all_existing
                    for depender, dependees in stager.depends_on.items()
                    if (depender not in all_existing
                        and not dependees.issubset(all_existing))
                }

                # Create models that don't have dependencies
                created, updated, deleted = stager.commit(exclude=set(stager.depends_on))

                # Remove newly created items from `all_to_create`
                all_to_create -= created
                all_existing |= created

                all_created |= created
                all_updated |= updated
                all_deleted |= deleted

        for stager in self.stagers_mtm.values():
            stager.commit()

        return all_created, all_updated, all_deleted
