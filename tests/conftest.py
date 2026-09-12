"""Shared loader for the extensionless arch-update-popup script."""
import importlib.util
import os

APP_PATH = os.path.realpath(
    os.path.join(os.path.dirname(__file__), "..", "arch-update-popup")
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
    return mod
