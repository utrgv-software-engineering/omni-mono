# Pinned QuickJS-NG source

Unmodified native sources from quickjs-ng 0.17.0.1 (MIT), including its upstream
QuickJS-NG 0.17.0 engine. The Python wrapper and engine licenses are included.
PyPI source archive SHA256:
`a1f7352b7e508346c8254f5ae790799898ebd1b2df2538bf9b7070a757faf903`.
Source: https://pypi.org/project/quickjs-ng/0.17.0.1/ .

Used only to build the Intel macOS binary distributed inside our platform wheel;
other supported platforms use the pinned upstream wheel. Set OMNI_BUILD_QUICKJS=1
in build/test CI to exercise the same extension on Linux. No std/os bindings are
compiled or exposed. Update engine, wrapper, pin, hash and platform tests together.

The PyPI sdist omits four engine headers. They are copied unmodified from
https://github.com/quickjs-ng/quickjs/tree/v0.17.0 and tracked alongside the engine:

- quickjs-c-atomics.h: 29883c742a5c512278fce54d19b1b9af949006e1d7dfc9ffa2e0946b184d8c5c
- builtin-array-fromasync.h: f41eb2eb028832cff9a5f8d8d2cb438c27771a9d7d85c30dfd16aa78f34fe6de
- builtin-iterator-zip-keyed.h: 24446c6157a043cfeb0b0255c7892f5146809bd3098c70028bfa1ee5311b91f4
- builtin-iterator-zip.h: fdaef760ecce82c1f8b62dca8d0d424ae05ef88a0037e32221858ab7ac8994c9

The engine LICENSE is copied from the same tag. The wrapper and engine license
files are included in binary wheels as well as the source distribution.
