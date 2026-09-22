# Third-party components and references

The controller, local bridge and utilities in this repository were written for this project and are covered by the repository's [MIT license](LICENSE). Third-party products and dependencies retain their own licenses.

## Game and runtime

You need your own installed copy of **Brotato** to run the agent. This repository does not include the commercial game's executable, PCK, extracted scripts, textures, audio or other game assets. The bridge calls APIs and extends a movement script supplied by the user's game installation. Godot and Brotato's bundled ModLoader are runtime dependencies, not vendored source or binaries in this repository.

**TypeSafe/Jev** is an external service used through its [HTTP API](https://docs.typesafe.ai/api). No TypeSafe SDK is bundled; the controller uses Python's standard library. API access requires a user-supplied credential.

## Installed dependencies and tools

| Component | Use in this project | Upstream license or notice |
| --- | --- | --- |
| Python | Interpreter and standard library | [Python license](https://docs.python.org/3/license.html) |
| Pillow | Optional `video` extra for drawing timelapse overlays | [MIT-CMU license](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| FFmpeg | Separately installed command-line encoder invoked by the renderer | [FFmpeg licensing](https://ffmpeg.org/legal.html); the applicable license depends on the build and enabled components |
| setuptools | Python package build backend | [MIT license](https://github.com/pypa/setuptools/blob/main/LICENSE) |
| actions/checkout | CI source checkout | [MIT license](https://github.com/actions/checkout/blob/11d5960a326750d5838078e36cf38b85af677262/LICENSE) |
| actions/setup-python | CI Python setup | [MIT license](https://github.com/actions/setup-python/blob/a26af69be951a213d495a4c3e4e4022e16d87065/LICENSE) |

These components are installed separately; their implementations are not included in this source repository or the generated mod ZIP. The renderer also uses available system fonts or Pillow's default font; no font files are bundled.

## Mod research

[Research notes](docs/research.md) link existing Brotato integrations inspected while choosing an approach, including Neuro SDK Brotato Integration, BrotatoAI, BOTato, Full Auto Bot and Brotato Exporter. They are research references, not dependencies or bundled mods.

The bridge is an original implementation. No upstream mod implementation is copied into it, including the GPL-licensed Full Auto Bot project. The upstream repositories remain the source for their own code, credits and license terms.
