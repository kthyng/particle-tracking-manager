# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.

import os
import pathlib
import sys

# The version info for the project you're documenting, acts as replacement for
# |version| and |release|, also used in various other places throughout the
# built documents.
# see https://pypi.org/project/setuptools-scm/ for details
from importlib.metadata import version as imversion


print("python exec:", sys.executable)
print("sys.path:", sys.path)
root = pathlib.Path(__file__).parent.parent.absolute()
os.environ["PYTHONPATH"] = str(root)
sys.path.insert(0, str(root))

import particle_tracking_manager  # isort:skip

# -- Project information -----------------------------------------------------

project = "particle-tracking-manager"
copyright = "2023-, axiom-data-science"
author = "axiom-data-science"

release = imversion("particle_tracking_manager")
# for example take major/minor
version = ".".join(release.split(".")[:2])

# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
    # "nbsphinx",
    "recommonmark",
    "sphinx.ext.mathjax",
    "sphinx.ext.autosummary",
    "sphinx.ext.extlinks",
    "sphinx.ext.intersphinx",
    "numpydoc",
    # "nbsphinx",
    "IPython.sphinxext.ipython_directive",
    "IPython.sphinxext.ipython_console_highlighting",
    "sphinxcontrib.srclinks",
    "myst_nb",
]

# for compiling notebooks with mystnb
# https://docs.readthedocs.io/en/stable/guides/jupyter.html#using-notebooks-in-other-formats
nb_custom_formats = {
    ".md": ["jupytext.reads", {"fmt": "mystnb"}],
}

# https://myst-nb.readthedocs.io/en/v0.9.0/use/execute.html
# jupyter_execute_notebooks = "off"
nb_execution_mode = "auto"

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = ["_build", "**.ipynb_checkpoints", "Thumbs.db", ".DS_Store"]


# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = "furo"
html_title = "particle-tracking-manager documentation"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]

# https://myst-nb.readthedocs.io/en/v0.13.0/use/execute.html#execution-timeout
# had this message:
# WARNING: 'execution_timeout' is deprecated for 'nb_execution_timeout' [mystnb.config]
# WARNING: 'execution_allow_errors' is deprecated for 'nb_execution_allow_errors' [mystnb.config]
nb_execution_timeout = 180  # seconds.


# -- nbsphinx specific options ----------------------------------------------
# this allows notebooks to be run even if they produce errors.
nbsphinx_allow_errors = True


# copied from cf-xarray
# autosummary_generate = True

autodoc_typehints = "none"
autodoc_default_options = {
    "members": True,
    "inherited-members": True,
    "show-inheritance": True,
    "undoc-members": True,
    "private-members": True,
}
napoleon_use_param = True
napoleon_use_rtype = True
