from .books import books_bp
from .users import users_bp
from .categories import categories_bp
from .library import library_bp
from .toc import toc_bp


def register_blueprints(app):
    app.register_blueprint(books_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(categories_bp)
    app.register_blueprint(library_bp)
    app.register_blueprint(toc_bp)
