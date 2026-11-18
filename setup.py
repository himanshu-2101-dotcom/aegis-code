from setuptools import setup, find_packages

setup(
    name="aegis-code",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "aegis=aegis_code.cli:app",
        ],
    },
)
