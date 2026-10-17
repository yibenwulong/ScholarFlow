"""Cache identity shared by batch reading and its outer workflow jobs."""
READER_VERSION = 'reader-v4'


def reader_inputs(inputs: dict) -> dict:
    return dict(inputs, reader_version=READER_VERSION)
