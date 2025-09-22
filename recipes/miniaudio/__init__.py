from pythonforandroid.recipe import PyProjectRecipe

class MiniaudioRecipe(PyProjectRecipe):
    version = "v1.61"
    url = "https://github.com/irmen/pyminiaudio/archive/refs/tags/{version}.tar.gz"
    site_packages_name = "miniaudio"
    patches = ["setup.py.patch", "build_ffi_module.py.patch"]
    depends = ["python3", "setuptools", "cffi"]
    hostpython_prerequisites = ["setuptools>=42", "cffi>=1.12.0"]

recipe = MiniaudioRecipe()
