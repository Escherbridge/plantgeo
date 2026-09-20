# Vegetation direct writer

This package owns the independently scheduled vegetation/NDVI daily producer. `source.py` admits
the upstream day, `rows.py` conforms its cells, `adapter.py` binds publication, and `forward.py`
owns the bounded turn, parser, and `WRITER_CONTRACT`. `products.py` contains the one-stream policy
constants, `support.py` contains shared turn mechanics, and `__main__.py` is the supported module
entrypoint.

The package follows the source-direct daily lifecycle even though the UI may group the layer with
other vegetation or climate displays. Ordinary boundary failures should use the shared pipeline
operational error; preserve a specialized exception only for tested control flow such as an
unsettled source day.
