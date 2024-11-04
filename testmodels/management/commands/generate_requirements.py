import os
from django.core.management.base import BaseCommand


class Command(BaseCommand):

    def handle(self, *args, **options):
        os.system('python -m piptools compile requirements.in')
        os.system('python -m piptools compile requirements-dev.in')
