from abc import ABC, abstractmethod


class ApplicationAdapter(ABC):

    def __init__(self, dsm_api):
        self.dsm = dsm_api  # injected — adapter never imports DSM internals

    @abstractmethod
    def prepare(self, data):
        """Chunk input (self.chunk) and allocate via self.dsm. Returns object_ids."""
        ...

    @abstractmethod
    def execute(self, dsm_objects):
        """Process via self.dsm.read/write only. Returns processed object_ids."""
        ...

    @abstractmethod
    def collect_result(self, dsm_objects):
        """Read results, self.aggregate(), return final output."""
        ...

    # Optional hooks — safe no-op defaults, override if the adapter needs them
    def chunk(self, data):
        return [data]

    def aggregate(self, partial_results):
        return partial_results

    def estimate_memory(self, data):
        return None