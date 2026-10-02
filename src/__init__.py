"""WATERS sentiment mining pipeline.

The environment is loaded here, at package import, because the submodules bind
their configuration to module-level constants (CONTACT, BATCH, MIN_CELL,
DB_PATH, ...) at import time. Loading .env any later — for example inside
cli.main(), after `from . import collectors` has already run — leaves those
constants holding their defaults while os.environ holds the configured values,
so settings appear to be applied but silently are not.

load_dotenv does not override variables already present in the environment, so
real env vars and test fixtures still win over the file.
"""
from dotenv import load_dotenv

load_dotenv()
