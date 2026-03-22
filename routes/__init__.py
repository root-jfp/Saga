from .books import books_bp
from .users import users_bp

def register_blueprints(app):
    app.register_blueprint(books_bp)
    app.register_blueprint(users_bp)
