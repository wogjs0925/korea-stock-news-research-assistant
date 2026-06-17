from .errors import router as errors_router
from . import news as news_module
news = news_module

__all__ = ["errors_router", "news"]
