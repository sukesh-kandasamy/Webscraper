"""The worker's copy of app/agent/llm_errors.py, loaded by path (the worker's environment has none of the
app's packages, so it must not import the app package)."""
import importlib.util as _u
from pathlib import Path as _P

_spec = _u.spec_from_file_location("llm_errors_shared", _P(__file__).resolve().parent.parent / "app" / "agent" / "llm_errors.py")
_mod = _u.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})
