from distutils.core import setup, Extension
import re
import typing


def get_version() -> str:
    with open("asyncio_toolkit/__init__.py", "r") as f:
        matches = re.search(r"__version__\s*=\s*\"(\d+\.\d+\.\d+)\"", f.read())
        assert matches is not None
        return matches[1]


def parse_requirements() -> typing.Dict[str, typing.Any]:
    install_requires = []
    dependency_links = []

    with open("requirements.txt", "r") as f:
        for line in f.readlines():
            line = line.strip()

            if line == "" or line.startswith("#"):
                continue

            if line.startswith("git+"):
                matches = re.match(r".*/([^/]+).git@v(\d+\.\d+\.\d+)$", line)
                assert matches is not None
                install_require = "{}=={}".format(matches[1], matches[2])
                install_requires.append(install_require)
                dependency_link = "{}#egg={}-{}".format(matches[0], matches[1], matches[2])
                dependency_links.append(dependency_link)
            else:
                install_require = line
                install_requires.append(install_require)

    return {
        "install_requires": install_requires,
        "dependency_links": dependency_links,
    }


setup(
    name="asyncio-toolkit",
    version=get_version(),
    description="AsyncIO Toolkit",
    packages=["asyncio_toolkit"],
    ext_modules=[Extension("asyncio_toolkit._asyncio", ["modules/_asynciomodule.c"])],
    python_requires=">=3.6.0",
    **parse_requirements(),
)
