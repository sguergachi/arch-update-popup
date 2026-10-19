"""Shared loader for the extensionless upkeep script."""
import importlib.util
import os
import sys

import pytest

APP_PATH = os.path.realpath(
    os.path.join(os.path.dirname(__file__), "..", "upkeep")
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def load_app(name="arch_update_app"):
    spec = importlib.util.spec_from_loader(
        name,
        loader=None,
        origin=APP_PATH,
    )
    mod = importlib.util.module_from_spec(spec)
    with open(APP_PATH, "rb") as f:
        src = f.read()
    code = compile(src, APP_PATH, "exec")
    exec(code, mod.__dict__)
    # Registered so fixtures can reach the loaded module.
    sys.modules.setdefault(name, mod)
    return mod


# Every worker thread below shells out to pacman/paru or hits the network.
# Left alone, each window fixture forks real pacman several times: minutes
# of runtime and machine-dependent results. Only the *thread bodies* are
# neutered — every pure helper stays real for the logic tests, and a test
# that needs real work patches the function it calls.
def _no_updates(self):
    self.no_updates.emit()


def _no_obsolete(self):
    self.done.emit([])


def _no_library(self):
    self.done.emit([])


def _no_scan(self):
    self.done.emit([])


def _no_install(self):
    self.done.emit(False, "")


def _quiet(self):
    return None


_EMPTY_THREAD_RUNS = {
    "UpdateChecker": _no_updates,
    "ObsoleteChecker": _no_obsolete,
    "LibraryLoader": _no_library,
    "InfoFetcher": _quiet,
    "SystemScanner": _no_scan,
    "UpdateRunner": _no_install,
    "ObsoleteRemover": _no_install,
}


@pytest.fixture(autouse=True)
def hermetic_workers(monkeypatch):
    """Keep the suite off the real system: no pacman forks, no network."""
    for mod in list(sys.modules.values()):
        if not hasattr(mod, "UpdateWindow"):
            continue
        for cls_name, stub in _EMPTY_THREAD_RUNS.items():
            cls = getattr(mod, cls_name, None)
            if cls is not None:
                monkeypatch.setattr(cls, "run", stub, raising=False)
    yield


@pytest.fixture(autouse=True)
def hermetic_state_files(tmp_path_factory, monkeypatch):
    """Point the on-disk state at a temp dir, never the developer's own.

    The window saves its geometry on close and the info cache is written
    by the fetcher; without this a test run would rewrite real files.
    """
    root = tmp_path_factory.mktemp("state")
    for mod in list(sys.modules.values()):
        if not hasattr(mod, "UpdateWindow"):
            continue
        monkeypatch.setattr(
            mod, "_cache_file",
            lambda r=root: str(r / "info.json"), raising=False)
        monkeypatch.setattr(
            mod, "_settings_file",
            lambda r=root: str(r / "settings.json"), raising=False)
    yield
