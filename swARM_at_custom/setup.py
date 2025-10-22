from setuptools import setup, find_packages

setup(
    name="swARM_at",              
    version="0.0.5",              
    packages=find_packages(exclude=("test",)),
    python_requires=">=3.8",
    install_requires=[
        "pyserial>=3.0"
    ],
    description="Fork editada de swARM_at (RAK3172/RAK4270).",
)
