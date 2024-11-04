from collections import defaultdict
import logging
from typing import Any, Generic, TypeVar

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

        # Determine type of provided type argument `M`
        logging.info(f'Committing staged {model_name} instances.')

        with transaction.atomic():

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
