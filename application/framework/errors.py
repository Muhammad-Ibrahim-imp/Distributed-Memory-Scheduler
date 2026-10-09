class AdapterError(Exception):
    """Root of adapter-side errors. Adapters are not part of the DSM core, so these
    deliberately do NOT inherit DSMError; DSM failures still propagate as DSMError."""


class AdapterConfigError(AdapterError):
    pass


class InvalidInputError(AdapterError):
    pass


class ChunkingError(AdapterError):
    pass


class ReconstructionError(AdapterError):
    pass