from setuptools import setup, find_packages

setup(
    name="magistrate",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "click==8.1.7",
        "textual==0.65.2",
        "websockets==12.0",
    ],
    entry_points={
        "console_scripts": [
            "omni=cli.main:cli",
        ],
    },
)
