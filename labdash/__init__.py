"""labdash — monitoring and infrastructure operations for research projects.

Standard library only. Two surfaces over one set of verbs:
  * a localhost dashboard  (python -m labdash serve)
  * a command line         (python -m labdash.cli ...)

The CLI exists so an agent drives the same well-defined operations you do,
rather than improvising shell commands against a cluster.
"""
__version__ = "2.0.0"
from .server import serve, load_config, DEFAULT_CONFIG  # noqa: F401
from . import ops, adapters  # noqa: F401
