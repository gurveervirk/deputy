from deproc.core.config import Config
from deproc.core.context import Context
from deproc.plugins.python import PythonPlugin

Config.plugin_registry.register(PythonPlugin)

def create_context(base_path: str = ".") -> Context:
    return Context(base_path=base_path)