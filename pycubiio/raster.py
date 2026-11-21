from .models import ModelsManager


class CubiioRaster:
    """Counterpart of brother_ql's BrotherQLRaster: holds the model and the last job."""

    def __init__(self, model="cubiio"):
        self.model_obj = ModelsManager().get_model_by_identifier(model)
        self.model = self.model_obj.identifier
        self.job = None
