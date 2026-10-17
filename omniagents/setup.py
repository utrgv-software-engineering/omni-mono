"""Supply the missing Intel macOS engine wheel without user-side compilation."""

import os
import platform
from setuptools import Extension, setup

native = (
    platform.system() == "Darwin" and platform.machine() == "x86_64"
) or os.environ.get("OMNI_BUILD_QUICKJS") == "1"
root = "vendor/quickjs-ng/"
extensions = []
if native:
    extensions = [
        Extension(
            "omniagents._quickjs",
            sources=[
                root + name
                for name in [
                    "module.c",
                    "upstream-quickjs/dtoa.c",
                    "upstream-quickjs/libregexp.c",
                    "upstream-quickjs/libunicode.c",
                    "upstream-quickjs/quickjs.c",
                ]
            ],
            define_macros=[("Py_LIMITED_API", "0x030A0000")],
            py_limited_api=True,
        )
    ]
setup(
    ext_modules=extensions,
    options={"bdist_wheel": {"py_limited_api": "cp310"}} if native else {},
)
