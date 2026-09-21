"""Quant Workbench: run, inspect, debug and extend a portfolio of quantitative projects.

The package is organised as a hexagonal architecture (see ``docs/adr/0001``):

``domain``          pure business vocabulary: entities, value objects, events, ports.
``application``     use cases orchestrating the ports; never touches I/O directly.
``infrastructure``  adapters that implement the ports (subprocess, SQLite, git, libcst...).
``ui`` / ``cli``    delivery mechanisms (PySide6 desktop app, Typer command line).
``bootstrap``       the composition root: the only place that wires adapters to use cases.
"""

__version__ = "0.1.0"
