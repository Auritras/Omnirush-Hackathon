"""ProofFix package metadata and public localhost entry points."""

__version__ = "0.1.0"

from .cli import main
from .server import ServerConfig, create_server

__all__ = ["__version__", "ServerConfig", "create_server", "main"]
