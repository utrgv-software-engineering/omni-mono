"""Package marker so importlib.resources can locate worker.mjs.

The worker itself is a Node script; this module has no Python runtime
contents. Kept as a package (not a namespace dir) so the .mjs files ship
inside the wheel via setuptools.package-data.
"""
