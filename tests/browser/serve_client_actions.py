"""Isolated browser fixture. Google and email are simulated; no production data."""
from serve_google_login import app
from werkzeug.security import generate_password_hash
app.config.update(STAFF_USER='fixture-admin',STAFF_PASSWORD_HASH=generate_password_hash('fixture-password'),GOOGLE_LOGIN_REDIRECT_URI='http://127.0.0.1:5003/cliente/google/retorno')
if __name__=='__main__':app.run(host='127.0.0.1',port=5003)
