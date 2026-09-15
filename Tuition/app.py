"""Run the Tuition Management System.

    python app.py

Then open http://127.0.0.1:5058 in your browser.
Your data is stored outside this folder (see tuition/__init__.py).
"""
from tuition import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5058, use_reloader=False)
 