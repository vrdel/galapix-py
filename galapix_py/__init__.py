__all__ = ["GalapixApp"]


def __getattr__(name: str):
    if name == "GalapixApp":
        from .app import GalapixApp

        return GalapixApp
    raise AttributeError(name)
